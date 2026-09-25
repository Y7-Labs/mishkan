"""CapabilityGateway adapter for attributed Knowledge queries."""

from __future__ import annotations

from pydantic import ValidationError

from mishkan.config.models import CredentialReference, KnowledgeConfig, McpConfig, WebConfig
from mishkan.domain.errors import ErrorCode, MishkanError
from mishkan.knowledge.models import KnowledgeClass, KnowledgeQuery
from mishkan.knowledge.service import KnowledgeService
from mishkan.tools.adapters import AdapterCall
from mishkan.tools.gateway_models import AdapterResult
from mishkan.web.network import NetworkGuard


class KnowledgeQueryToolAdapter:
    adapter_id = "native.knowledge.query"

    def __init__(
        self,
        config: KnowledgeConfig,
        service: KnowledgeService,
        *,
        web: WebConfig | None,
        mcp: McpConfig | None,
    ) -> None:
        self._config = config
        self._service = service
        self._web = web
        self._mcp = mcp

    def invoke(self, call: AdapterCall) -> AdapterResult:
        try:
            query = KnowledgeQuery.model_validate(call.arguments["query"])
        except (KeyError, ValidationError) as exc:
            raise MishkanError(
                ErrorCode.TOOL_SCHEMA,
                "knowledge query tool requires the public KnowledgeQuery schema",
            ) from exc
        if query.scope.agent_identity not in {None, call.acting_identity}:
            raise MishkanError(
                ErrorCode.AUTHORITY_NOT_GRANTED,
                "knowledge query agent scope differs from the acting identity",
            )
        source_ids = self._source_order(query)
        expected_external = (
            f"knowledge-project:{query.scope.project_id}",
            f"knowledge-class:{query.knowledge_class.value}",
            *(f"knowledge-source:{item}" for item in source_ids),
            *(
                (
                    f"repository:{query.scope.repository_id}",
                    f"repository-revision:{query.scope.repository_revision}",
                )
                if query.scope.repository_id is not None
                else ()
            ),
        )
        expected_network = self._network_destinations(source_ids)
        expected_credentials = tuple(
            sorted(
                {
                    reference.locator
                    for source_id in source_ids
                    for reference in self._source_credentials(source_id)
                }
            )
        )
        declared_credentials = call.arguments.get("credential_refs")
        if declared_credentials != list(expected_credentials):
            raise MishkanError(
                ErrorCode.TOOL_SCHEMA,
                "knowledge credential declarations differ from selected sources",
            )
        if call.credentials:
            raise MishkanError(
                ErrorCode.TOOL_SCHEMA,
                "knowledge query credentials must be resolved inside the optional-source boundary",
            )
        if call.targets.external_resources != expected_external:
            raise MishkanError(
                ErrorCode.TOOL_SCHEMA,
                "knowledge external targets differ from deterministic source selection",
            )
        if call.targets.network_destinations != expected_network:
            raise MishkanError(
                ErrorCode.TOOL_SCHEMA,
                "knowledge network targets differ from deterministic source selection",
            )
        bundle = self._service.query(query)
        return AdapterResult(
            output=bundle.model_dump(mode="json"),
            actual_targets=call.targets,
            external_references=(bundle.bundle_reference,),
            evidence={
                "adapter": self.adapter_id,
                "knowledge_class": query.knowledge_class.value,
                "degraded": bundle.degraded,
            },
        )

    def _source_order(self, query: KnowledgeQuery) -> tuple[str, ...]:
        configured = tuple(self._config.selection_order.get(query.knowledge_class, ()))
        selected = (
            query.preferred_sources
            if query.preferred_sources
            else tuple(item for item in configured if self._config.sources[item].enabled)
        )
        if (
            self._config.literal_fallback
            and query.knowledge_class in {KnowledgeClass.SEMANTIC, KnowledgeClass.STRUCTURAL}
            and query.scope.repository_id is not None
        ):
            selected = (
                *selected,
                *(
                    item
                    for item in self._config.selection_order.get(KnowledgeClass.LITERAL, ())
                    if item not in selected and self._config.sources[item].enabled
                ),
            )
        return tuple(selected)

    def _source_credentials(self, source_id: str) -> tuple[CredentialReference, ...]:
        source = self._config.sources[source_id]
        references = source.credential_refs
        if source.mcp_connection is not None:
            if self._mcp is None or source.mcp_connection not in self._mcp.connections:
                raise MishkanError(ErrorCode.MCP, "Graphify MCP connection is unavailable")
            references = (
                *references,
                *self._mcp.connections[source.mcp_connection].credential_refs,
            )
        return references

    def _network_destinations(self, source_ids: tuple[str, ...]) -> tuple[str, ...]:
        origins: list[str] = []
        for source_id in source_ids:
            source = self._config.sources[source_id]
            if source.endpoint is not None:
                if self._web is None or source.network_profile is None:
                    raise MishkanError(ErrorCode.CONFIGURATION, "knowledge Web scope is absent")
                origins.append(
                    NetworkGuard(self._web.network_profiles[source.network_profile])
                    .validate_url(str(source.endpoint))
                    .origin
                )
            if source.mcp_connection is not None:
                if self._mcp is None:
                    raise MishkanError(ErrorCode.MCP, "Graphify MCP scope is absent")
                connection = self._mcp.connections[source.mcp_connection]
                if connection.endpoint is not None:
                    assert self._web is not None and connection.network_profile is not None
                    origins.append(
                        NetworkGuard(self._web.network_profiles[connection.network_profile])
                        .validate_url(str(connection.endpoint))
                        .origin
                    )
        return tuple(dict.fromkeys(origins))
