"""Versioned provider adapters behind the governed Knowledge service."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Protocol

from mishkan.config.models import KnowledgeSourceConfig, NetworkProfileConfig
from mishkan.domain.errors import ErrorCode, MishkanError
from mishkan.knowledge.models import KnowledgeQuery
from mishkan.web.adapters import SingleRequestTransport
from mishkan.web.network import HttpExchange


@dataclass(frozen=True, slots=True)
class RawKnowledgeRecord:
    external_record_id: str | None
    content: bytes
    media_type: str
    source_locator: str
    source_revision: str | None
    ranking_basis: str
    score: float | None = None
    confidence: str | None = None


@dataclass(frozen=True, slots=True)
class ProviderKnowledgeResult:
    records: tuple[RawKnowledgeRecord, ...]
    provider_schema: str
    limitation: str | None = None


@dataclass(frozen=True, slots=True)
class ProviderMutationResult:
    provider_operation_id: str | None
    external_record_ids: tuple[str, ...]
    response: dict[str, Any]


class KnowledgeQueryAdapter(Protocol):
    adapter_id: str

    def query(
        self,
        query: KnowledgeQuery,
        *,
        source_id: str,
        source: KnowledgeSourceConfig,
        credentials: tuple[str, ...],
        network_profile: NetworkProfileConfig | None,
    ) -> ProviderKnowledgeResult: ...


class LiteralKnowledgePort(Protocol):
    def query(self, query: KnowledgeQuery, *, source_id: str) -> ProviderKnowledgeResult: ...


class GraphifyMcpPort(Protocol):
    def call(
        self,
        connection_id: str,
        *,
        primitive: str,
        arguments: dict[str, Any],
        query: KnowledgeQuery,
    ) -> dict[str, Any]: ...


class LiteralKnowledgeAdapter:
    adapter_id = "native.literal"

    def __init__(self, port: LiteralKnowledgePort) -> None:
        self._port = port

    def query(
        self,
        query: KnowledgeQuery,
        *,
        source_id: str,
        source: KnowledgeSourceConfig,
        credentials: tuple[str, ...],
        network_profile: NetworkProfileConfig | None,
    ) -> ProviderKnowledgeResult:
        del source, network_profile
        if credentials:
            raise MishkanError(
                ErrorCode.TOOL_SCHEMA,
                "literal knowledge cannot receive external credentials",
            )
        return self._port.query(query, source_id=source_id)


class _HttpKnowledgeAdapter:
    adapter_id: str

    def __init__(self, transport: SingleRequestTransport) -> None:
        self._transport = transport

    def _request_json(
        self,
        method: str,
        url: str,
        *,
        source_id: str,
        source: KnowledgeSourceConfig,
        profile: NetworkProfileConfig | None,
        credentials: tuple[str, ...],
        payload: dict[str, Any],
        timeout_seconds: float | None = None,
    ) -> Any:
        if profile is None:
            raise MishkanError(
                ErrorCode.CONFIGURATION,
                "HTTP knowledge source has no network profile",
                details={"source_id": source_id},
            )
        headers = {"accept": "application/json", "content-type": "application/json"}
        if source.credential_header is not None:
            if len(credentials) != 1:
                raise MishkanError(
                    ErrorCode.AUTHORIZATION_MISSING,
                    "knowledge source requires exactly one resolved credential",
                    details={"source_id": source_id},
                )
            headers[source.credential_header] = source.credential_prefix + credentials[0]
        elif credentials:
            raise MishkanError(
                ErrorCode.TOOL_SCHEMA,
                "knowledge credentials have no configured header",
                details={"source_id": source_id},
            )
        exchange = self._transport.request(
            method,
            url,
            profile=profile,
            headers=headers,
            content=json.dumps(payload, sort_keys=True, separators=(",", ":")).encode(),
            timeout_seconds=timeout_seconds or source.query_timeout_seconds,
        )
        return self._decode(exchange, source_id=source_id, source=source)

    @staticmethod
    def _decode(
        exchange: HttpExchange,
        *,
        source_id: str,
        source: KnowledgeSourceConfig,
    ) -> Any:
        if not 200 <= exchange.status_code < 300:
            raise MishkanError(
                ErrorCode.OPTIONAL_DEPENDENCY,
                "knowledge source returned a non-success status",
                details={"source_id": source_id, "status_code": exchange.status_code},
                retryable=exchange.status_code == 429 or exchange.status_code >= 500,
            )
        if len(exchange.content) > source.max_result_bytes:
            raise MishkanError(
                ErrorCode.OUTPUT_CONTRACT,
                "knowledge source response exceeds its configured bound",
                details={"source_id": source_id, "limit": source.max_result_bytes},
            )
        try:
            return json.loads(exchange.content)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise MishkanError(
                ErrorCode.OUTPUT_CONTRACT,
                "knowledge source returned malformed JSON",
                details={"source_id": source_id},
            ) from exc

    @staticmethod
    def _endpoint(source: KnowledgeSourceConfig, path: str) -> str:
        if source.endpoint is None:
            raise MishkanError(ErrorCode.CONFIGURATION, "knowledge endpoint is missing")
        return f"{str(source.endpoint).rstrip('/')}/{path.lstrip('/')}"


class Mem0OssAdapter(_HttpKnowledgeAdapter):
    """mem0 OSS REST adapter. OSS paths intentionally have no /v1 prefix."""

    adapter_id = "mem0.oss"

    def query(
        self,
        query: KnowledgeQuery,
        *,
        source_id: str,
        source: KnowledgeSourceConfig,
        credentials: tuple[str, ...],
        network_profile: NetworkProfileConfig | None,
    ) -> ProviderKnowledgeResult:
        filters: dict[str, str] = {"user_id": query.scope.project_id}
        if query.scope.agent_identity is not None:
            filters["agent_id"] = query.scope.agent_identity
        if query.scope.run_id is not None:
            filters["run_id"] = query.scope.run_id
        document = self._request_json(
            "POST",
            self._endpoint(source, "search"),
            source_id=source_id,
            source=source,
            profile=network_profile,
            credentials=credentials,
            payload={
                "query": query.question,
                "filters": filters,
                "top_k": min(query.max_results, source.max_results),
            },
        )
        raw = document.get("results") if isinstance(document, dict) else document
        if not isinstance(raw, list):
            raise MishkanError(
                ErrorCode.OUTPUT_CONTRACT,
                "mem0 search response omits the results list",
                details={"source_id": source_id},
            )
        records: list[RawKnowledgeRecord] = []
        for item in raw[: min(query.max_results, source.max_results)]:
            if not isinstance(item, dict) or not isinstance(item.get("memory"), str):
                raise MishkanError(
                    ErrorCode.OUTPUT_CONTRACT,
                    "mem0 result does not match the OSS memory contract",
                    details={"source_id": source_id},
                )
            identifier = str(item["id"]) if item.get("id") is not None else None
            score = item.get("score")
            records.append(
                RawKnowledgeRecord(
                    external_record_id=identifier,
                    content=item["memory"].encode(),
                    media_type="text/plain; charset=utf-8",
                    source_locator=f"mem0:{identifier or 'unidentified'}",
                    source_revision=str(item.get("updated_at") or item.get("created_at") or "")
                    or None,
                    ranking_basis="mem0 semantic similarity",
                    score=float(score) if isinstance(score, (int, float)) else None,
                )
            )
        return ProviderKnowledgeResult(tuple(records), source.provider_schema)

    def capture(
        self,
        memory: str,
        *,
        project_id: str,
        agent_identity: str,
        run_id: str,
        operation_id: str,
        source_id: str,
        source: KnowledgeSourceConfig,
        credentials: tuple[str, ...],
        network_profile: NetworkProfileConfig,
    ) -> ProviderMutationResult:
        document = self._request_json(
            "POST",
            self._endpoint(source, "memories"),
            source_id=source_id,
            source=source,
            profile=network_profile,
            credentials=credentials,
            timeout_seconds=source.operation_timeout_seconds,
            payload={
                "messages": [{"role": "user", "content": memory}],
                "user_id": project_id,
                "agent_id": agent_identity,
                "run_id": run_id,
                "metadata": {
                    "mishkan_operation_id": operation_id,
                    "project_id": project_id,
                    "accepted_result": True,
                },
            },
        )
        if not isinstance(document, dict):
            raise MishkanError(ErrorCode.OUTPUT_CONTRACT, "mem0 capture returned no object")
        raw_results = document.get("results", document.get("memories", []))
        if not isinstance(raw_results, list):
            raw_results = []
        identifiers = tuple(
            str(item["id"]) for item in raw_results if isinstance(item, dict) and item.get("id")
        )
        return ProviderMutationResult(operation_id, identifiers, document)


class CogneeOssAdapter(_HttpKnowledgeAdapter):
    adapter_id = "cognee.oss"

    def query(
        self,
        query: KnowledgeQuery,
        *,
        source_id: str,
        source: KnowledgeSourceConfig,
        credentials: tuple[str, ...],
        network_profile: NetworkProfileConfig | None,
    ) -> ProviderKnowledgeResult:
        document = self._request_json(
            "POST",
            self._endpoint(source, "search"),
            source_id=source_id,
            source=source,
            profile=network_profile,
            credentials=credentials,
            payload={
                "query": query.question,
                "search_type": "CHUNKS",
                "datasets": [query.scope.project_id],
            },
        )
        raw = document.get("results") if isinstance(document, dict) else document
        if not isinstance(raw, list):
            raise MishkanError(
                ErrorCode.OUTPUT_CONTRACT,
                "Cognee raw chunk response omits the results list",
                details={"source_id": source_id},
            )
        records: list[RawKnowledgeRecord] = []
        for rank, item in enumerate(raw[: min(query.max_results, source.max_results)], start=1):
            if isinstance(item, str):
                content = item
                identifier = None
                score: float | None = None
                revision = None
            elif isinstance(item, dict):
                value = item.get("text", item.get("content", item.get("chunk")))
                if not isinstance(value, str):
                    raise MishkanError(
                        ErrorCode.OUTPUT_CONTRACT,
                        "Cognee CHUNKS result contains generated or unknown material",
                        details={"source_id": source_id},
                    )
                content = value
                identifier = str(item["id"]) if item.get("id") is not None else None
                raw_score = item.get("score")
                score = float(raw_score) if isinstance(raw_score, (int, float)) else None
                revision = str(item["updated_at"]) if item.get("updated_at") else None
            else:
                raise MishkanError(
                    ErrorCode.OUTPUT_CONTRACT,
                    "Cognee CHUNKS result has an invalid value",
                    details={"source_id": source_id},
                )
            records.append(
                RawKnowledgeRecord(
                    external_record_id=identifier,
                    content=content.encode(),
                    media_type="text/plain; charset=utf-8",
                    source_locator=f"cognee:{query.scope.project_id}:{identifier or rank}",
                    source_revision=revision,
                    ranking_basis="Cognee CHUNKS order",
                    score=score,
                )
            )
        return ProviderKnowledgeResult(tuple(records), source.provider_schema)

    def add(
        self,
        content: str,
        *,
        dataset: str,
        operation_id: str,
        source_id: str,
        source: KnowledgeSourceConfig,
        credentials: tuple[str, ...],
        network_profile: NetworkProfileConfig,
    ) -> ProviderMutationResult:
        document = self._request_json(
            "POST",
            self._endpoint(source, "add"),
            source_id=source_id,
            source=source,
            profile=network_profile,
            credentials=credentials,
            timeout_seconds=source.operation_timeout_seconds,
            payload={
                "data": content,
                "datasetName": dataset,
                "metadata": {"mishkan_operation_id": operation_id},
            },
        )
        if not isinstance(document, dict):
            raise MishkanError(ErrorCode.OUTPUT_CONTRACT, "Cognee add returned no object")
        provider_id = document.get("operation_id", document.get("task_id"))
        return ProviderMutationResult(
            str(provider_id) if provider_id is not None else operation_id,
            (),
            document,
        )

    def cognify(
        self,
        *,
        dataset: str,
        operation_id: str,
        source_id: str,
        source: KnowledgeSourceConfig,
        credentials: tuple[str, ...],
        network_profile: NetworkProfileConfig,
    ) -> ProviderMutationResult:
        document = self._request_json(
            "POST",
            self._endpoint(source, "cognify"),
            source_id=source_id,
            source=source,
            profile=network_profile,
            credentials=credentials,
            timeout_seconds=source.operation_timeout_seconds,
            payload={
                "datasets": [dataset],
                "metadata": {"mishkan_operation_id": operation_id},
            },
        )
        if not isinstance(document, dict):
            raise MishkanError(ErrorCode.OUTPUT_CONTRACT, "Cognee cognify returned no object")
        provider_id = document.get("operation_id", document.get("task_id"))
        return ProviderMutationResult(
            str(provider_id) if provider_id is not None else operation_id,
            (),
            document,
        )


class GraphifyMcpAdapter:
    adapter_id = "graphify.mcp"
    _ALLOWED_PRIMITIVES = frozenset(
        {
            "query_graph",
            "get_node",
            "get_neighbors",
            "graphify_node",
            "graphify_callers",
            "graphify_callees",
            "graphify_trace",
            "graphify_impact",
            "graphify_rank_files",
            "shortest_path",
            "god_nodes",
            "graph_stats",
        }
    )

    def __init__(self, port: GraphifyMcpPort) -> None:
        self._port = port

    def query(
        self,
        query: KnowledgeQuery,
        *,
        source_id: str,
        source: KnowledgeSourceConfig,
        credentials: tuple[str, ...],
        network_profile: NetworkProfileConfig | None,
    ) -> ProviderKnowledgeResult:
        del credentials, network_profile
        if source.mcp_connection is None:
            raise MishkanError(ErrorCode.CONFIGURATION, "Graphify MCP connection is missing")
        primitive, arguments = self._request(query)
        if primitive not in self._ALLOWED_PRIMITIVES:
            raise MishkanError(ErrorCode.MCP, "Graphify primitive is not compatible")
        document = self._port.call(
            source.mcp_connection,
            primitive=primitive,
            arguments=arguments,
            query=query,
        )
        content = json.dumps(document, sort_keys=True, separators=(",", ":")).encode()
        if len(content) > min(query.max_bytes, source.max_result_bytes):
            raise MishkanError(
                ErrorCode.OUTPUT_CONTRACT,
                "Graphify result exceeds the configured knowledge bound",
            )
        return ProviderKnowledgeResult(
            (
                RawKnowledgeRecord(
                    external_record_id=None,
                    content=content,
                    media_type="application/json",
                    source_locator=f"graphify:{source.mcp_connection}:{primitive}",
                    source_revision=query.scope.repository_revision,
                    ranking_basis=f"Graphify {primitive} traversal",
                    confidence="provider edge confidence retained in content",
                ),
            ),
            source.provider_schema,
        )

    @staticmethod
    def _request(query: KnowledgeQuery) -> tuple[str, dict[str, Any]]:
        return (
            "query_graph",
            {
                "query": query.question,
                "max_results": query.max_results,
                "repository_id": query.scope.repository_id,
                "repository_revision": query.scope.repository_revision,
            },
        )
