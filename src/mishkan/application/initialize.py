"""Repository initialization application service."""

from collections.abc import Callable, Mapping
from dataclasses import replace
from pathlib import Path

from mishkan.artifacts import ArtifactStore, FilesystemArtifactStore
from mishkan.artifacts.service import DurableArtifactService
from mishkan.config.models import CredentialReference, MishkanConfig
from mishkan.crewai.credentials import CredentialPoolResolver
from mishkan.crewai.environment import configure_crewai_environment
from mishkan.domain.errors import ErrorCode, MishkanError
from mishkan.organization import load_initialization_definitions
from mishkan.persistence import LocalRunRepository, SchemaManager
from mishkan.planning import PlanValidator
from mishkan.planning.models import InitializationReport
from mishkan.policy import PolicyAuthority, PolicyLoader
from mishkan.repository import RepositoryInspector
from mishkan.tools.adapters import CapabilityAdapter, ContainerCommandAdapter
from mishkan.tools.capability_runtime import CapabilityRuntime, build_capability_runtime
from mishkan.tools.catalog import ToolCatalog
from mishkan.tools.gateway import (
    CapabilityGateway,
    GitRepositoryStateObserver,
)
from mishkan.tools.inspection import ContentInspector, InspectionProfileLoader
from mishkan.tools.isolation import IsolationProfileLoader, observe_container_commands
from mishkan.tools.lifecycle import ToolRegistryLifecycle
from mishkan.tools.native import (
    available_contracts,
    build_native_adapters,
    discover_native_environment,
)


class _ConfiguredCredentialResolver:
    """Resolve only configured locators, at the moment the Gateway dispatches."""

    def __init__(self, references: Mapping[str, CredentialReference]) -> None:
        self._references = dict(references)
        self._resolver = CredentialPoolResolver()

    def resolve(self, locators: tuple[str, ...]) -> dict[str, str]:
        try:
            references = tuple(self._references[item] for item in locators)
        except KeyError as exc:
            raise MishkanError(
                ErrorCode.AUTHORIZATION_MISSING,
                "capability credential locator is not configured",
            ) from exc
        return self._resolver.resolve_exact(references)


def _configured_credential_resolver(config: MishkanConfig) -> _ConfiguredCredentialResolver:
    references = dict(config.credential_bindings)
    if config.web is not None:
        for web_source in config.web.sources.values():
            references.update({item.locator: item for item in web_source.credential_refs})
    if config.knowledge is not None:
        for knowledge_source in config.knowledge.sources.values():
            references.update({item.locator: item for item in knowledge_source.credential_refs})
    if config.mcp is not None:
        for connection in config.mcp.connections.values():
            references.update({item.locator: item for item in connection.credential_refs})
    return _ConfiguredCredentialResolver(references)


class MishkanInitializer:
    def run(
        self,
        config: MishkanConfig,
        repository_path: Path,
        objective: str,
        *,
        on_run_started: Callable[[str], None] | None = None,
        capability_adapters: Mapping[str, CapabilityAdapter] | None = None,
    ) -> InitializationReport:
        if config.schema_version not in {"1.1", "1.2", "1.3", "1.4", "1.5", "1.6"}:
            raise MishkanError(
                ErrorCode.VERSION,
                "governed initialization requires configuration schema 1.1 or 1.2",
                details={"received": config.schema_version, "automatic_migration": False},
            )
        discovery = RepositoryInspector().inspect(repository_path)
        configure_crewai_environment(
            config.crewai,
            discovery.binding.root / ".mishkan" / "crewai-runtime",
        )
        from mishkan.crewai.coordinator import CrewAIInitializationCoordinator
        from mishkan.crewai.flow import CrewAIInitializationFlow, InitializationFlowState

        organization, outcome = load_initialization_definitions()
        database = discovery.binding.root / ".mishkan" / "mishkan.db"
        SchemaManager(database).initialize_if_empty()
        persistence_config = config.persistence
        busy_timeout_ms = (
            persistence_config.busy_timeout_ms if persistence_config is not None else 5_000
        )
        native_environment = discover_native_environment()
        policy = PolicyLoader().load(config.policy_sources, discovery.binding.root)
        inspection_source = config.inspection_profile
        if inspection_source is None:
            raise MishkanError(
                ErrorCode.CONFIGURATION,
                "governed initialization requires an inspection profile",
            )
        inspector = ContentInspector(
            InspectionProfileLoader().load(inspection_source, discovery.binding.root)
        )
        state_repository = LocalRunRepository(
            database,
            busy_timeout_ms=busy_timeout_ms,
            content_inspector=inspector,
        )
        runtime: CapabilityRuntime | None = None
        artifact_store: ArtifactStore
        available_environment = native_environment
        isolation_loader = IsolationProfileLoader()
        isolation_profiles = tuple(
            isolation_loader.load(source, discovery.binding.root)
            for source in config.isolation_profiles
        )
        profile_ids = [profile.profile_id for profile in isolation_profiles]
        if len(profile_ids) != len(set(profile_ids)):
            raise MishkanError(
                ErrorCode.CONFIGURATION,
                "configured isolation profile identities must be unique",
            )
        isolated_commands = observe_container_commands(isolation_profiles)
        if isolated_commands:
            available_environment = replace(
                available_environment,
                adapter_ids=(
                    available_environment.adapter_ids
                    | frozenset({ContainerCommandAdapter.adapter_id})
                ),
            )
        if config.schema_version in {"1.3", "1.4", "1.5", "1.6"}:
            artifact_config = config.artifacts
            assert artifact_config is not None
            durable_artifacts = DurableArtifactService(
                database,
                discovery.binding.root / artifact_config.root,
                max_artifact_bytes=artifact_config.max_artifact_bytes,
                max_chunk_bytes=artifact_config.chunk_bytes,
                busy_timeout_ms=busy_timeout_ms,
                staging_ttl_seconds=artifact_config.staging_ttl_seconds,
                content_inspector=inspector,
            )
            runtime = build_capability_runtime(
                config,
                database,
                discovery.binding.root,
                durable_artifacts,
                inspector,
                policy,
            )
            runtime.adapters.update(capability_adapters or {})
            artifact_store = durable_artifacts
            available_environment = replace(
                native_environment,
                adapter_ids=native_environment.adapter_ids | runtime.adapter_ids,
                dependencies=native_environment.dependencies | runtime.dependencies,
            )
        catalog = ToolCatalog(
            config.tool_sources,
            discovery.binding.root,
            available_dependencies=available_environment.dependencies,
            available_adapters=available_environment.adapter_ids,
            lifecycle=ToolRegistryLifecycle(database, busy_timeout_ms=busy_timeout_ms).projection(),
        )
        authority = PolicyAuthority()
        contracts = available_contracts(catalog, outcome.allowed_tools)
        runtime_adapter_ids = runtime.adapter_ids if runtime is not None else frozenset()
        native_tool_ids = tuple(
            contract.tool_id
            for contract in contracts
            if contract.adapter not in runtime_adapter_ids
        )
        adapters = dict(build_native_adapters(catalog, native_tool_ids, native_environment))
        if any(contract.adapter == ContainerCommandAdapter.adapter_id for contract in contracts):
            adapters[ContainerCommandAdapter.adapter_id] = ContainerCommandAdapter(
                isolated_commands
            )
        if runtime is not None:
            adapters.update(runtime.adapters)
        artifact_limit = max(
            (
                value
                for contract in contracts
                if isinstance((value := contract.adapter_config.get("max_output_bytes")), int)
            ),
            default=1,
        )
        if runtime is None:
            artifact_store = FilesystemArtifactStore(
                discovery.binding.root / ".mishkan" / "artifacts",
                max_artifact_bytes=artifact_limit,
            )
        gateway = CapabilityGateway(
            discovery.binding.root,
            authority,
            _configured_credential_resolver(config),
            inspector,
            adapters,
            state_repository,
            cancellation=state_repository,
            artifact_store=artifact_store,
            planned_calls=state_repository,
            repository_observer=GitRepositoryStateObserver(),
        )
        snapshot = state_repository.start_or_resume(discovery, objective, outcome.outcome_id)
        if on_run_started is not None:
            on_run_started(snapshot.run_id)
        state = InitializationFlowState(
            run_id=snapshot.run_id,
            objective=objective,
            discovery=discovery,
            resumed=snapshot.resumed,
            accepted_plan=snapshot.plan,
            accepted_results=list(snapshot.results),
            accepted_reviews=list(snapshot.reviews),
        )
        coordinator = CrewAIInitializationCoordinator(
            config,
            organization,
            outcome,
            gateway,
            policy,
            available_tools=contracts,
            available_executables=native_environment.executables,
        )
        flow = CrewAIInitializationFlow(
            state,
            coordinator,
            state_repository,
            organization,
            outcome,
            PlanValidator(
                catalog,
                policy,
                authority,
                inspector,
                max_agent_iterations=config.crewai.max_agent_iterations,
            ),
            tracing=config.crewai.tracing,
        )
        try:
            output = flow.kickoff()
        finally:
            if runtime is not None:
                runtime.close()
        if not isinstance(output, InitializationReport):
            return InitializationReport.model_validate(output)
        return output
