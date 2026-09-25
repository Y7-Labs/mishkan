"""Authenticated HTTP/OpenAPI and resumable SSE facade for mishkand."""

from __future__ import annotations

import asyncio
import base64
import json
from collections.abc import AsyncIterator, Callable, Iterator, Mapping
from contextlib import asynccontextmanager
from datetime import datetime
from functools import partial
from pathlib import Path
from typing import Annotated, Literal, ParamSpec, TypeVar
from uuid import UUID

import anyio
from fastapi import Depends, FastAPI, Header, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from mishkan.application import (
    ApplicationCommand,
    CommandResult,
    RunInitializationRequest,
    SnapshotEnvelope,
)
from mishkan.application.authorization import (
    ApplicationCommandAuthority,
    AuthorizedApplicationCommand,
)
from mishkan.application.initialize import MishkanInitializer
from mishkan.artifacts import (
    ArtifactCollection,
    ArtifactManifest,
    ArtifactPin,
    ArtifactProvenance,
    UploadSession,
    WorkingReference,
)
from mishkan.artifacts import (
    ArtifactHold as ArtifactEvidenceHold,
)
from mishkan.artifacts.service import DurableArtifactService
from mishkan.config.models import CredentialReference, McpConfig, MishkanConfig
from mishkan.context import (
    CommunityCandidateLoader,
    ContextualRecommendation,
    ContextualRecommendationService,
    EngineerProfile,
    EngineerProfileLoader,
)
from mishkan.conversations import (
    ConversationChannel,
    EscalationState,
    SQLiteConversationRepository,
)
from mishkan.crewai.credentials import CredentialPoolResolver
from mishkan.crewai.mission_environment import (
    CrewAIMissionEnvironmentPlanningRunner,
    MissionEnvironmentPlanningRunner,
)
from mishkan.crewai.mission_governance import (
    CrewAIMissionGovernanceRunner,
    MissionGovernanceResult,
    MissionGovernanceRunner,
)
from mishkan.crewai.skill_learning import CrewAISkillLearningRunner
from mishkan.daemon.auth import TokenFile, TokenRecord
from mishkan.daemon.bootstrap import DaemonPaths
from mishkan.domain.errors import ErrorCode, MishkanError
from mishkan.domain.time import utc_now
from mishkan.edits import ChangeSet, ChangeSetResult, ChangeSetService
from mishkan.edits.git import GovernedGitService
from mishkan.environment import (
    DescriptorValidationResult,
    EngineeringCommandCandidate,
    EngineeringCommandPlan,
    EnvironmentAttempt,
    EnvironmentBinding,
    EnvironmentDescriptorChangePlan,
    EnvironmentDescriptorChangePlanner,
    EnvironmentDescriptorSet,
    EnvironmentDescriptorValidator,
    EnvironmentEvidenceService,
    EnvironmentInvalidation,
    EnvironmentObservation,
    EnvironmentObserver,
    EnvironmentOperationPlan,
    EnvironmentOperationPlanner,
    EnvironmentProfile,
    EnvironmentResolver,
    EnvironmentVerification,
    TechnicalPackLoader,
    TechnicalPackService,
    load_environment_profile,
)
from mishkan.environment.repository import SQLiteEnvironmentRepository
from mishkan.events import (
    EventHold as EventEvidenceHold,
)
from mishkan.events import (
    EventHoldScope,
    EventPage,
    EventRetentionPlan,
    EventRetentionPolicy,
)
from mishkan.execution import CursorRead, ExecutionRequest, ExecutionSession, SessionSupervisor
from mishkan.knowledge import (
    KnowledgeOperation,
)
from mishkan.knowledge.adapters import (
    CogneeOssAdapter,
    GraphifyMcpAdapter,
    KnowledgeQueryAdapter,
    LiteralKnowledgeAdapter,
    Mem0OssAdapter,
)
from mishkan.knowledge.inspection import (
    EvidenceInspectionProfileLoader,
    KnowledgeEvidenceInspector,
)
from mishkan.knowledge.operations import KnowledgeMutationService, SQLiteAcceptedResultVerifier
from mishkan.knowledge.repository import SQLiteKnowledgeRepository
from mishkan.knowledge.runtime import (
    DaemonKnowledgePolicy,
    GraphifyMcpKnowledgePort,
    RepositoryLiteralKnowledgePort,
)
from mishkan.knowledge.service import KnowledgeService
from mishkan.knowledge.tools import KnowledgeQueryToolAdapter
from mishkan.mcp import (
    McpContractFactory,
    McpFacadeRouter,
    McpHttpFacade,
    McpPrimitiveKind,
    McpRepository,
    McpSdkClient,
    McpService,
    McpServiceRunner,
)
from mishkan.mcp.sdk import McpStdioCommandBuilder
from mishkan.missions import (
    MissionBrief,
    MissionCompletionReadiness,
    MissionCrewRevision,
    MissionEnvironmentReadiness,
    MissionEnvironmentReadinessService,
    MissionRecord,
    MissionRunBinding,
    MissionState,
    MissionTaskClaimService,
    MissionTaskEligibility,
    MissionTemplateLoader,
    MissionTemplateService,
    SQLiteMissionRepository,
)
from mishkan.missions.environment import (
    MissionEnvironmentPlanAcceptance,
    MissionEnvironmentPlanValidator,
)
from mishkan.missions.inspection import MissionInspectionService
from mishkan.notifications import (
    NotificationDelivery,
    NotificationPage,
    NotificationService,
    NotificationSeverity,
)
from mishkan.organization import (
    ProfessionalCompetenceState,
    ProfessionalEvidenceKind,
    ProfessionalEvidenceRecord,
    ProfessionalPromotionDecision,
    load_canonical_organization,
)
from mishkan.organization.evolution_repository import SQLiteProfessionalEvolutionRepository
from mishkan.persistence import LocalRunRepository, SchemaManager, SQLiteApplicationRepository
from mishkan.policy import Decision
from mishkan.policy.models import EffectivePolicy
from mishkan.repository import (
    ProspectiveWorkspaceBinding,
    ProspectiveWorkspaceInspector,
    RepositoryEstablishment,
    RepositoryInspector,
)
from mishkan.runtime import TaskReviewRejection
from mishkan.skills import SkillInspectionProfileLoader, SkillPackageInspector
from mishkan.skills.catalog import validate_skill_metadata_document
from mishkan.skills.learning import SkillLearningRunner, SkillLearningService
from mishkan.skills.learning_repository import SQLiteSkillLearningRepository
from mishkan.skills.maintenance import SkillMaintenanceService
from mishkan.skills.models import (
    SkillCurationProposal,
    SkillLearningRecord,
    SkillUpdateReport,
    SkillUsageSummary,
    SkillVersionRecord,
)
from mishkan.skills.repository import SQLiteSkillLifecycleRepository, SQLiteSkillUsageRepository
from mishkan.skills.service import SkillInvocationService
from mishkan.telemetry.evidence import TelemetryEvaluationService
from mishkan.telemetry.exporters import OtlpHttpTelemetryExporter, TelemetryExporter
from mishkan.telemetry.models import (
    TelemetryEvaluationImportResult,
    TelemetryExporterKind,
    TelemetryRecord,
    TelemetryRecordStatus,
    TelemetryStatus,
)
from mishkan.telemetry.service import TelemetryService
from mishkan.tools.inspection import ContentInspector, InspectionProfileLoader
from mishkan.tools.isolation import IsolationProfileLoader, observe_container_commands
from mishkan.tools.lifecycle import ToolRegistryLifecycle
from mishkan.web.network import HttpxWebTransport

_P = ParamSpec("_P")
_R = TypeVar("_R")


def _telemetry_record(
    config: MishkanConfig,
    command: ApplicationCommand,
    result: CommandResult,
    *,
    started_at: datetime,
    completed_at: datetime | None = None,
    name: str = "mishkan.command.completed",
) -> TelemetryRecord:
    attributes: dict[str, str | bool | int | float] = {
        "mishkan.command.id": str(command.command_id),
        "mishkan.command.type": command.command_type,
        "mishkan.actor.id": command.actor_id,
        "mishkan.target.type": command.target_type,
        "mishkan.result.status": result.status.value,
    }
    if result.revision is not None:
        attributes["mishkan.result.revision"] = result.revision
    if result.event_cursor is not None:
        attributes["mishkan.event.cursor"] = result.event_cursor
    if result.error is not None:
        attributes["mishkan.error.code"] = result.error.code.value
    content: list[str] = []
    if config.telemetry.include_command_payload:
        attributes["mishkan.command.payload"] = json.dumps(
            command.payload, sort_keys=True, separators=(",", ":")
        )
        content.append("mishkan.command.payload")
    if config.telemetry.include_result_payload:
        attributes["mishkan.result.payload"] = json.dumps(
            result.payload, sort_keys=True, separators=(",", ":")
        )
        content.append("mishkan.result.payload")
    return TelemetryRecord(
        name=name,
        started_at=started_at,
        completed_at=completed_at or result.completed_at,
        status=(TelemetryRecordStatus.OK if result.error is None else TelemetryRecordStatus.ERROR),
        attributes=attributes,
        content_attribute_names=tuple(content),
    )


def _build_telemetry_exporter(
    config: MishkanConfig,
    credential_resolver: CredentialPoolResolver,
) -> TelemetryExporter:
    exporter = config.telemetry.exporter
    if exporter is None or config.web is None:
        raise MishkanError(
            ErrorCode.OPTIONAL_DEPENDENCY,
            "telemetry exporter is disabled",
        )
    headers: dict[str, str] = {}
    credential = exporter.credential_ref
    if credential is not None:
        value = credential_resolver.resolve((credential,))[0]
        if value is None:
            raise MishkanError(
                ErrorCode.AUTHORIZATION_MISSING,
                "telemetry exporter credential is unavailable",
            )
        headers[exporter.credential_header] = f"{exporter.credential_prefix}{value}"
    if exporter.kind is TelemetryExporterKind.LANGSMITH_OTLP:
        assert exporter.project is not None
        headers["Langsmith-Project"] = exporter.project
    return OtlpHttpTelemetryExporter(
        endpoint=str(exporter.endpoint),
        profile=config.web.network_profiles[exporter.network_profile],
        service_name=exporter.service_name,
        headers=headers,
        timeout_seconds=exporter.timeout_seconds,
    )


async def _thread_call(function: Callable[_P, _R], /, *args: _P.args, **kwargs: _P.kwargs) -> _R:
    """Keep sync work off-loop while remaining live if a thread wakeup is coalesced."""
    call = partial(function, *args, **kwargs)
    task = asyncio.create_task(anyio.to_thread.run_sync(call))
    while not task.done():
        await asyncio.sleep(0.01)
    return task.result()


class _RequestBodyLimitMiddleware:
    def __init__(self, app: ASGIApp, *, max_bytes: int) -> None:
        self._app = app
        self._max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return
        headers = {name.lower(): value for name, value in scope.get("headers", ())}
        declared = headers.get(b"content-length")
        if declared is not None:
            try:
                if int(declared) > self._max_bytes:
                    await self._reject(scope, receive, send)
                    return
            except ValueError:
                await self._reject(scope, receive, send)
                return
        body = bytearray()
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            chunk = message.get("body", b"")
            body.extend(chunk)
            if len(body) > self._max_bytes:
                await self._reject(scope, receive, send)
                return
            if not message.get("more_body", False):
                break
        delivered = False
        response_complete = asyncio.Event()

        async def replay() -> Message:
            nonlocal delivered
            if delivered:
                await response_complete.wait()
                return {"type": "http.disconnect"}
            delivered = True
            return {"type": "http.request", "body": bytes(body), "more_body": False}

        async def observe_send(message: Message) -> None:
            await send(message)
            if message["type"] == "http.response.body" and not message.get("more_body", False):
                response_complete.set()

        try:
            await self._app(scope, replay, observe_send)
        finally:
            response_complete.set()

    @staticmethod
    async def _reject(scope: Scope, receive: Receive, send: Send) -> None:
        response = JSONResponse(
            status_code=413,
            content={
                "code": ErrorCode.OUTPUT_CONTRACT,
                "message": "application request body exceeds its configured bound",
            },
        )
        await response(scope, receive, send)


def _http_status(error: MishkanError) -> int:
    code = error.envelope.code
    if code in {ErrorCode.AUTHORITY_NOT_GRANTED, ErrorCode.AUTHORIZATION_MISSING}:
        return 403
    if code in {ErrorCode.REVISION_MISMATCH, ErrorCode.DUPLICATE_RESULT}:
        return 409
    if code in {ErrorCode.VERSION, ErrorCode.REQUIRED_DEPENDENCY}:
        return 503
    if code in {ErrorCode.OUTPUT_CONTRACT, ErrorCode.CONFIGURATION}:
        return 422
    return 400


def _require_knowledge(
    repository: SQLiteKnowledgeRepository | None,
    service: KnowledgeService | None,
    mutations: KnowledgeMutationService | None,
) -> tuple[SQLiteKnowledgeRepository, KnowledgeService, KnowledgeMutationService]:
    if repository is None or service is None or mutations is None:
        raise MishkanError(
            ErrorCode.REQUIRED_DEPENDENCY,
            "knowledge capability is not configured",
        )
    return repository, service, mutations


def _knowledge_source_projection(config: MishkanConfig) -> list[dict[str, object]]:
    knowledge = config.knowledge
    if knowledge is None:
        return []
    selection = {
        source_id: (knowledge_class.value, index)
        for knowledge_class, source_ids in knowledge.selection_order.items()
        for index, source_id in enumerate(source_ids)
    }
    return [
        {
            "source_id": source_id,
            "knowledge_class": source.knowledge_class.value,
            "adapter": source.adapter,
            "enabled": source.enabled,
            "cost_class": source.cost_class.value,
            "disclosure_profile": source.disclosure_profile,
            "selection_index": selection.get(source_id, ("", -1))[1],
            "health": "configured_unobserved",
        }
        for source_id, source in sorted(knowledge.sources.items())
    ]


def create_app(
    config: MishkanConfig,
    *,
    mcp_stdio_commands: Mapping[str, McpStdioCommandBuilder] | None = None,
    skill_learning_runner: SkillLearningRunner | None = None,
    mission_governance_runner: MissionGovernanceRunner | None = None,
    mission_environment_runner: MissionEnvironmentPlanningRunner | None = None,
    telemetry_exporter_factory: Callable[[], TelemetryExporter] | None = None,
) -> FastAPI:
    paths = DaemonPaths.from_config(config)
    SchemaManager(paths.database).require_current()
    token_file = TokenFile(paths.token_file)
    token_file.read()
    persistence = config.persistence
    assert persistence is not None
    repository = SQLiteApplicationRepository(
        paths.database,
        busy_timeout_ms=persistence.busy_timeout_ms,
    )
    registry_lifecycle = ToolRegistryLifecycle(
        paths.database, busy_timeout_ms=persistence.busy_timeout_ms
    )
    security = HTTPBearer(auto_error=False)
    security_dependency = Depends(security)
    daemon = config.daemon
    artifact_config = config.artifacts
    inspection_source = config.inspection_profile
    assert daemon is not None
    assert artifact_config is not None
    if inspection_source is None:
        raise MishkanError(
            ErrorCode.CONFIGURATION,
            "daemon artifact persistence requires an inspection profile",
        )
    content_inspector = ContentInspector(
        InspectionProfileLoader().load(inspection_source, paths.workspace)
    )
    run_repository = LocalRunRepository(
        paths.database,
        busy_timeout_ms=persistence.busy_timeout_ms,
        content_inspector=content_inspector,
    )
    artifacts = DurableArtifactService(
        paths.database,
        paths.artifacts,
        max_artifact_bytes=artifact_config.max_artifact_bytes,
        max_chunk_bytes=artifact_config.chunk_bytes,
        busy_timeout_ms=persistence.busy_timeout_ms,
        staging_ttl_seconds=artifact_config.staging_ttl_seconds,
        content_inspector=content_inspector,
    )
    environment_repository: SQLiteEnvironmentRepository | None = None
    environment_observer: EnvironmentObserver | None = None
    environment_resolver: EnvironmentResolver | None = None
    environment_descriptor_validator: EnvironmentDescriptorValidator | None = None
    environment_descriptor_change_planner: EnvironmentDescriptorChangePlanner | None = None
    environment_operation_planner: EnvironmentOperationPlanner | None = None
    environment_evidence_service: EnvironmentEvidenceService | None = None
    technical_pack_service: TechnicalPackService | None = None
    environment_profile: EnvironmentProfile | None = None
    if config.engineering_profile is not None:
        environment_profile = load_environment_profile(
            config.engineering_profile,
            paths.workspace,
        )
        environment_repository = SQLiteEnvironmentRepository(
            paths.database,
            artifacts=artifacts,
            busy_timeout_ms=persistence.busy_timeout_ms,
        )
        environment_observer = EnvironmentObserver(environment_profile)
        environment_resolver = EnvironmentResolver(
            freshness_seconds=environment_profile.freshness_seconds
        )
        environment_descriptor_validator = EnvironmentDescriptorValidator(
            environment_repository,
            artifacts,
            max_descriptor_bytes=environment_profile.max_descriptor_bytes,
        )
        environment_descriptor_change_planner = EnvironmentDescriptorChangePlanner(
            environment_repository
        )
        environment_operation_planner = EnvironmentOperationPlanner(
            environment_profile,
            environment_repository,
            artifacts,
        )
        environment_evidence_service = EnvironmentEvidenceService(
            environment_profile,
            environment_repository,
        )
        technical_pack_service = TechnicalPackService(
            TechnicalPackLoader().load(config.engineering_pack_sources, paths.workspace)
        )
    skill_lifecycle: SQLiteSkillLifecycleRepository | None = None
    skill_usage: SQLiteSkillUsageRepository | None = None
    skill_inspector: SkillPackageInspector | None = None
    skill_invocation_service: SkillInvocationService | None = None
    skill_learning_repository: SQLiteSkillLearningRepository | None = None
    skill_learning_service: SkillLearningService | None = None
    skill_maintenance: SkillMaintenanceService | None = None
    if config.skills is not None:
        skill_lifecycle = SQLiteSkillLifecycleRepository(
            paths.database, busy_timeout_ms=persistence.busy_timeout_ms
        )
        skill_usage = SQLiteSkillUsageRepository(
            paths.database, busy_timeout_ms=persistence.busy_timeout_ms
        )
        skill_inspector = SkillPackageInspector(
            SkillInspectionProfileLoader().load(
                config.skills.inspection_profile,
                paths.workspace,
            )
        )
        skill_invocation_service = SkillInvocationService(
            skill_lifecycle,
            skill_usage,
            artifacts,
            bundles=config.skills.bundles,
            automatic_selection=config.skills.automatic_selection,
            max_automatic_skills=config.skills.max_automatic_skills,
        )
        skill_learning_repository = SQLiteSkillLearningRepository(
            paths.database, busy_timeout_ms=persistence.busy_timeout_ms
        )
        skill_learning_service = SkillLearningService(
            skill_learning_repository,
            skill_lifecycle,
            artifacts,
            skill_inspector,
            content_inspector,
            skill_learning_runner or CrewAISkillLearningRunner(config),
            max_source_bytes=config.skills.learning_max_source_bytes,
            max_catalog_candidates=config.skills.bounds.max_package_files,
        )
        skill_maintenance = SkillMaintenanceService(
            skill_lifecycle,
            skill_usage,
            sources=config.skills.sources,
            bounds=config.skills.bounds,
            project_root=paths.workspace,
            stale_after_days=config.skills.stale_after_days,
        )
    changes = ChangeSetService(
        paths.database,
        paths.workspace,
        artifacts,
        busy_timeout_ms=persistence.busy_timeout_ms,
        content_inspector=content_inspector,
    )
    git_effects = GovernedGitService(artifacts)
    session_config = config.sessions
    assert session_config is not None
    supervisor = SessionSupervisor(
        paths.database,
        paths.workspace,
        paths.sessions,
        session_config,
        artifacts,
        busy_timeout_ms=persistence.busy_timeout_ms,
        content_inspector=content_inspector,
    )
    supervisor.reconcile_all()
    command_lock = asyncio.Lock()
    run_execution_lock = asyncio.Lock()
    active_run_commands: dict[UUID, tuple[str, asyncio.Task[CommandResult]]] = {}
    active_commands: dict[UUID, tuple[str, asyncio.Task[CommandResult]]] = {}
    target_effect_locks: dict[tuple[str, str], asyncio.Lock] = {}
    mcp_repository: McpRepository | None = None
    mcp_runner: McpServiceRunner | None = None
    mcp_config = config.mcp
    if mcp_config is not None:
        web_config = config.web
        if web_config is None:
            raise MishkanError(
                ErrorCode.CONFIGURATION,
                "daemon MCP mediation requires Web and inspection configuration",
            )
        mcp_repository = McpRepository(
            paths.database,
            busy_timeout_ms=persistence.busy_timeout_ms,
        )
        stdio_commands = dict(mcp_stdio_commands or {})
        if mcp_stdio_commands is None and any(
            connection.transport.value == "stdio" for connection in mcp_config.connections.values()
        ):
            loader = IsolationProfileLoader()
            profiles = tuple(
                loader.load(source, paths.workspace) for source in config.isolation_profiles
            )
            stdio_commands.update(observe_container_commands(profiles))
        mcp_service = McpService(
            paths.workspace,
            mcp_config,
            mcp_repository,
            McpSdkClient(
                web_config.network_profiles,
                stdio_commands=stdio_commands,
            ),
            content_inspector,
        )
        mcp_service.reconcile_after_restart()
        mcp_runner = McpServiceRunner(mcp_service)
    credential_resolver = CredentialPoolResolver()
    knowledge_repository: SQLiteKnowledgeRepository | None = None
    knowledge_service: KnowledgeService | None = None
    knowledge_mutations: KnowledgeMutationService | None = None
    knowledge_tool_adapter: KnowledgeQueryToolAdapter | None = None
    if config.knowledge is not None:
        knowledge_repository = SQLiteKnowledgeRepository(
            paths.database,
            busy_timeout_ms=persistence.busy_timeout_ms,
        )
        knowledge_policy = DaemonKnowledgePolicy(config.knowledge, paths.workspace)
        knowledge_inspector = KnowledgeEvidenceInspector(
            EvidenceInspectionProfileLoader().load(
                config.knowledge.inspection_profile,
                paths.workspace,
            )
        )
        literal_adapter = LiteralKnowledgeAdapter(RepositoryLiteralKnowledgePort(paths.workspace))
        adapters: dict[str, KnowledgeQueryAdapter] = {literal_adapter.adapter_id: literal_adapter}
        if config.web is not None:
            transport = HttpxWebTransport()
            mem0_adapter = Mem0OssAdapter(transport)
            cognee_adapter = CogneeOssAdapter(transport)
            adapters[mem0_adapter.adapter_id] = mem0_adapter
            adapters[cognee_adapter.adapter_id] = cognee_adapter
        if mcp_runner is not None and mcp_repository is not None and mcp_config is not None:
            graphify_adapter = GraphifyMcpAdapter(
                GraphifyMcpKnowledgePort(
                    mcp_runner,
                    mcp_repository,
                    {
                        connection_id: connection.credential_refs
                        for connection_id, connection in mcp_config.connections.items()
                    },
                    poll_seconds=mcp_config.cancellation_poll_seconds,
                    credential_resolver=credential_resolver,
                )
            )
            adapters[graphify_adapter.adapter_id] = graphify_adapter
        network_profiles = config.web.network_profiles if config.web is not None else {}
        knowledge_service = KnowledgeService(
            config.knowledge,
            knowledge_repository,
            artifacts,
            adapters=adapters,
            network_profiles=network_profiles,
            inspector=knowledge_inspector,
            policy_gate=knowledge_policy,
            credential_resolver=credential_resolver,
        )
        knowledge_mutations = KnowledgeMutationService(
            config.knowledge,
            knowledge_repository,
            artifacts,
            adapters=adapters,
            network_profiles=network_profiles,
            inspector=knowledge_inspector,
            policy=knowledge_policy,
            accepted_results=SQLiteAcceptedResultVerifier(
                paths.database,
                busy_timeout_ms=persistence.busy_timeout_ms,
            ),
            credential_resolver=credential_resolver,
        )
        knowledge_tool_adapter = KnowledgeQueryToolAdapter(
            config.knowledge,
            knowledge_service,
            web=config.web,
            mcp=mcp_config,
        )
    telemetry_exporter_config = config.telemetry.exporter

    telemetry_service = TelemetryService(
        config.telemetry,
        content_inspector,
        telemetry_exporter_factory
        or (
            partial(_build_telemetry_exporter, config, credential_resolver)
            if telemetry_exporter_config is not None
            else None
        ),
    )
    telemetry_evaluation_service = TelemetryEvaluationService(artifacts)
    engineer_profile = (
        EngineerProfileLoader().load(config.engineer_profile, paths.workspace)
        if config.engineer_profile is not None
        else None
    )
    community_recommendations = ContextualRecommendationService(
        CommunityCandidateLoader().load(config.community_candidate_sources, paths.workspace)
    )
    mission_repository = SQLiteMissionRepository(
        paths.database,
        busy_timeout_ms=persistence.busy_timeout_ms,
    )
    # Loading the daemon must not advance the public event cursor. The immutable
    # roster is persisted as reference data; explicit roster changes remain events.
    mission_repository.record_organization(load_canonical_organization(), emit_event=False)
    conversation_repository = SQLiteConversationRepository(
        paths.database,
        busy_timeout_ms=persistence.busy_timeout_ms,
    )
    professional_evolution = SQLiteProfessionalEvolutionRepository(
        paths.database,
        busy_timeout_ms=persistence.busy_timeout_ms,
    )
    mission_templates = MissionTemplateService(
        MissionTemplateLoader().load(config.mission_template_sources, paths.workspace)
    )
    mission_governance = mission_governance_runner or CrewAIMissionGovernanceRunner(
        config, mission_templates=mission_templates
    )
    mission_environment_planning = (
        mission_environment_runner or CrewAIMissionEnvironmentPlanningRunner(config)
    )
    mission_readiness = MissionEnvironmentReadinessService(
        mission_repository,
        environment_repository,
    )
    mission_task_claims = MissionTaskClaimService(
        mission_repository,
        conversation_repository,
        mission_readiness,
        run_repository,
    )
    mission_inspections = MissionInspectionService(
        organization=load_canonical_organization(),
        missions=mission_repository,
        conversations=conversation_repository,
        application=repository,
        runs=run_repository,
        artifacts=artifacts,
        readiness=mission_readiness,
        task_claims=mission_task_claims,
    )
    notification_service = NotificationService(config.notifications)
    telemetry_tasks: set[asyncio.Task[object]] = set()

    def project_telemetry(
        command: ApplicationCommand,
        result: CommandResult,
        started_at: datetime,
        resolved_secrets: tuple[str, ...],
    ) -> None:
        record = _telemetry_record(config, command, result, started_at=started_at)
        task = asyncio.create_task(
            _thread_call(
                telemetry_service.observe,
                record,
                resolved_secrets=resolved_secrets,
            ),
            name=f"telemetry:{command.command_id}",
        )
        telemetry_tasks.add(task)
        task.add_done_callback(telemetry_tasks.discard)

    command_authority = ApplicationCommandAuthority(
        config,
        paths.workspace,
        changes,
        supervisor,
        mcp_runner,
        knowledge_repository,
    )

    async def execute_command(command: ApplicationCommand, principal_id: str) -> CommandResult:
        command_started_at = utc_now()
        if command.actor_id != principal_id:
            raise MishkanError(
                ErrorCode.AUTHORITY_NOT_GRANTED,
                "command actor does not match the authenticated client identity",
                details={"actor_id": command.actor_id},
            )
        async with command_lock:
            active = active_run_commands.get(command.command_id) or active_commands.get(
                command.command_id
            )
            if active is not None:
                fingerprint, active_task = active
                if fingerprint != command.fingerprint:
                    raise MishkanError(
                        ErrorCode.DUPLICATE_RESULT,
                        "command identity was already used for different content",
                        details={"command_id": str(command.command_id)},
                    )
            else:
                active_task = None
        if active_task is not None:
            return await asyncio.shield(active_task)
        replayed = repository.replay(command)
        if replayed is not None:
            return replayed
        authorized = command_authority.authorize(command)
        if authorized.decision.decision is not Decision.ALLOW:
            code = (
                ErrorCode.AUTHORIZATION_MISSING
                if authorized.decision.decision is Decision.REQUIRE_APPROVAL
                else ErrorCode.AUTHORITY_NOT_GRANTED
            )
            refusal = MishkanError(
                code,
                "public policy did not authorize the exact application command scope",
                details={
                    "request_fingerprint": authorized.request.fingerprint,
                    "policy_fingerprint": authorized.decision.policy_fingerprint,
                    "policy_revisions": list(authorized.decision.policy_revisions),
                    "matched_rule_ids": list(authorized.decision.matched_rule_ids),
                    "decision": authorized.decision.decision.value,
                },
            )
            result = repository.refuse(
                authorized.command,
                target_id=authorized.command.target_id or "local-instance",
                error=refusal,
                event_payload={
                    "command_type": authorized.command.command_type,
                    **_authorization_projection(authorized),
                    "error_code": refusal.envelope.code,
                },
            )
            project_telemetry(command, result, command_started_at, ())
            return result
        command = authorized.command
        if command.command_type.startswith("registry.entry."):
            mutation = authorized.registry_mutation
            if mutation is None or command.target_id is None:
                raise MishkanError(
                    ErrorCode.OUTPUT_CONTRACT,
                    "authorized registry lifecycle mutation is absent",
                )

            def apply_registry_mutation(
                session: Session, next_revision: int
            ) -> tuple[dict[str, object], dict[str, object]]:
                entry = registry_lifecycle.mutate(session, mutation, revision=next_revision)
                result = entry.model_dump(mode="json")
                evidence = {
                    "entry_kind": entry.entry_kind.value,
                    "identity": entry.identity,
                    "action": mutation.action.value,
                    "enabled": entry.enabled,
                    "removed": entry.removed,
                    "precedence": entry.precedence,
                    "definition_fingerprint": entry.definition_fingerprint,
                    **_authorization_projection(authorized),
                }
                return result, evidence

            async with command_lock:
                try:
                    return repository.accept_with_mutation(
                        command,
                        target_id=command.target_id,
                        event_type=f"registry.entry_{mutation.action.value}",
                        mutation=apply_registry_mutation,
                        source="mishkand.registry",
                    )
                except MishkanError as error:
                    return repository.refuse(
                        command,
                        target_id=command.target_id,
                        error=error,
                        event_payload={
                            "command_type": command.command_type,
                            "entry_kind": mutation.entry_kind.value,
                            "identity": mutation.identity,
                            **_authorization_projection(authorized),
                            "error_code": error.envelope.code,
                        },
                    )
        resolved_credentials = _resolve_command_credentials(
            authorized,
            credential_resolver,
            mcp_runner,
            mcp_config,
            config,
        )
        if command.command_type == "run.initialize":
            if command.target_type != "run" or command.target_id is not None:
                raise MishkanError(
                    ErrorCode.OUTPUT_CONTRACT,
                    "run.initialize targets the daemon's configured repository",
                )
            try:
                request = RunInitializationRequest.model_validate(command.payload)
            except (TypeError, ValueError) as exc:
                raise MishkanError(
                    ErrorCode.OUTPUT_CONTRACT,
                    "run.initialize payload does not match its public contract",
                ) from exc

            async with command_lock:
                active = active_run_commands.get(command.command_id)
                if active is not None:
                    fingerprint, task = active
                    if fingerprint != command.fingerprint:
                        raise MishkanError(
                            ErrorCode.DUPLICATE_RESULT,
                            "command identity was already used for different content",
                            details={"command_id": str(command.command_id)},
                        )
                else:
                    replayed = repository.reserve(command, target_id="local-instance")
                    if replayed is not None:
                        return replayed
                    accepted: list[CommandResult] = []

                    def accept_run(run_id: str) -> None:
                        accepted.append(
                            repository.complete_reserved(
                                command,
                                target_id="local-instance",
                                event_type="run.request_accepted",
                                result_payload={"run_id": run_id},
                                event_payload={
                                    "run_id": run_id,
                                    "request_schema_version": request.schema_version,
                                    **_authorization_projection(authorized),
                                },
                                source="mishkand",
                            )
                        )

                    async def execute_run() -> CommandResult:
                        async with run_execution_lock:
                            await _thread_call(
                                MishkanInitializer().run,
                                config,
                                paths.workspace,
                                request.objective,
                                on_run_started=accept_run,
                                capability_adapters=(
                                    {knowledge_tool_adapter.adapter_id: knowledge_tool_adapter}
                                    if knowledge_tool_adapter is not None
                                    else None
                                ),
                            )
                        if len(accepted) != 1:
                            raise MishkanError(
                                ErrorCode.RUN_INTERRUPTED,
                                "CrewAI run did not establish exactly one durable run identity",
                            )
                        result = accepted[0]
                        record = _telemetry_record(
                            config,
                            command,
                            result,
                            started_at=command_started_at,
                            completed_at=utc_now(),
                            name="mishkan.run.initialized",
                        )
                        task = asyncio.create_task(
                            _thread_call(
                                telemetry_service.observe,
                                record,
                                resolved_secrets=tuple(resolved_credentials.values()),
                            ),
                            name=f"telemetry:{command.command_id}",
                        )
                        telemetry_tasks.add(task)
                        task.add_done_callback(telemetry_tasks.discard)
                        return result

                    task = asyncio.create_task(
                        execute_run(),
                        name=f"run.initialize:{command.command_id}",
                    )
                    active_run_commands[command.command_id] = (command.fingerprint, task)

                    def forget(completed: asyncio.Task[CommandResult]) -> None:
                        current = active_run_commands.get(command.command_id)
                        if current is not None and current[1] is completed:
                            active_run_commands.pop(command.command_id, None)
                        if not completed.cancelled():
                            completed.exception()

                    task.add_done_callback(forget)
            return await asyncio.shield(task)

        target_id = command.target_id or "local-instance"
        async with command_lock:
            active = active_commands.get(command.command_id)
            if active is not None:
                fingerprint, task = active
                if fingerprint != command.fingerprint:
                    raise MishkanError(
                        ErrorCode.DUPLICATE_RESULT,
                        "command identity was already used for different content",
                        details={"command_id": str(command.command_id)},
                    )
            else:
                replayed = repository.reserve(command, target_id=target_id)
                if replayed is not None:
                    return replayed
                target_lock = target_effect_locks.setdefault(
                    (command.target_type, target_id), asyncio.Lock()
                )

                async def execute_effect() -> CommandResult:
                    async with target_lock:
                        try:
                            await _thread_call(
                                repository.verify_reserved_precondition,
                                command,
                                target_id=target_id,
                            )
                            event_type, result_payload = await _thread_call(
                                _dispatch,
                                command,
                                authorized,
                                repository,
                                EventRetentionPolicy(
                                    max_age_days=persistence.event_retention_days,
                                    batch_size=daemon.event_page_limit,
                                ),
                                artifacts,
                                changes,
                                git_effects,
                                command_authority.policy,
                                supervisor,
                                run_repository,
                                paths.workspace,
                                mcp_runner,
                                mcp_config,
                                resolved_credentials,
                                skill_lifecycle,
                                skill_usage,
                                skill_inspector,
                                skill_invocation_service,
                                skill_learning_service,
                                environment_repository,
                                environment_observer,
                                environment_resolver,
                                environment_descriptor_validator,
                                environment_descriptor_change_planner,
                                environment_operation_planner,
                                environment_evidence_service,
                                technical_pack_service,
                                telemetry_evaluation_service,
                                knowledge_service,
                                knowledge_mutations,
                                community_recommendations,
                                mission_repository,
                                conversation_repository,
                                mission_governance,
                                mission_environment_planning,
                                environment_profile,
                                professional_evolution,
                                mission_task_claims,
                            )
                        except MishkanError as error:
                            result = repository.fail_reserved(
                                command,
                                target_id=target_id,
                                error=error,
                                event_payload=_authorization_projection(authorized),
                                sensitivity=(
                                    "security"
                                    if error.envelope.code
                                    in {
                                        ErrorCode.AUTHORITY_NOT_GRANTED,
                                        ErrorCode.AUTHORIZATION_MISSING,
                                        ErrorCode.POLICY_CONFLICT,
                                        ErrorCode.SECRET_CONTENT,
                                    }
                                    else "internal"
                                ),
                            )
                        except (KeyError, TypeError, ValueError):
                            payload_error = MishkanError(
                                ErrorCode.OUTPUT_CONTRACT,
                                "application command payload does not match "
                                "its registered contract",
                                details={"command_type": command.command_type},
                            )
                            result = repository.fail_reserved(
                                command,
                                target_id=target_id,
                                error=payload_error,
                                event_payload=_authorization_projection(authorized),
                            )
                        else:
                            result = repository.complete_reserved(
                                command,
                                target_id=target_id,
                                event_type=event_type,
                                result_payload=result_payload,
                                event_payload=_event_projection(
                                    command, result_payload, authorized
                                ),
                                source="mishkand",
                            )
                    project_telemetry(
                        command,
                        result,
                        command_started_at,
                        tuple(resolved_credentials.values()),
                    )
                    return result

                task = asyncio.create_task(
                    execute_effect(),
                    name=f"application-command:{command.command_id}",
                )
                active_commands[command.command_id] = (command.fingerprint, task)

                def forget_effect(completed: asyncio.Task[CommandResult]) -> None:
                    current = active_commands.get(command.command_id)
                    if current is not None and current[1] is completed:
                        active_commands.pop(command.command_id, None)
                    if not completed.cancelled():
                        completed.exception()

                task.add_done_callback(forget_effect)
        return await asyncio.shield(task)

    mcp_http: McpHttpFacade | None = None
    if mcp_config is not None and mcp_config.facade.enabled:
        schema_revision = SchemaManager(paths.database).status().head_revision
        router = McpFacadeRouter(
            mcp_config,
            repository,
            execute_command,
            schema_revision=schema_revision,
            event_page_limit=daemon.event_page_limit,
            organization=load_canonical_organization(),
            missions=mission_repository,
            conversations=conversation_repository,
            professional_evolution=professional_evolution,
            mission_templates=mission_templates,
            advisory=community_recommendations,
            readiness=mission_readiness,
            mission_task_claims=mission_task_claims,
            mission_inspections=mission_inspections,
            notifications=notification_service,
            knowledge_config=config.knowledge,
            knowledge=knowledge_repository,
        )
        mcp_http = McpHttpFacade(
            router,
            token_file,
            daemon_host=daemon.host,
            daemon_port=daemon.port,
        )

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        try:
            if mcp_http is None:
                yield
                return
            async with mcp_http.lifespan():
                yield
        finally:
            for task in telemetry_tasks:
                task.cancel()
            if mcp_runner is not None:
                mcp_runner.close()

    app = FastAPI(
        title="MISHKAN application API",
        version="1.0",
        docs_url=None,
        redoc_url=None,
        lifespan=lifespan,
    )
    app.add_middleware(_RequestBodyLimitMiddleware, max_bytes=daemon.max_request_bytes)

    @app.exception_handler(MishkanError)
    async def mishkan_error_handler(_request: Request, error: MishkanError) -> JSONResponse:
        return JSONResponse(
            status_code=_http_status(error),
            content=error.envelope.model_dump(mode="json"),
        )

    async def authenticate(
        credentials: HTTPAuthorizationCredentials | None = security_dependency,
    ) -> TokenRecord:
        if credentials is None or credentials.scheme.lower() != "bearer":
            raise MishkanError(
                ErrorCode.AUTHORITY_NOT_GRANTED,
                "authenticated daemon client identity is required",
            )
        record = token_file.authenticate(credentials.credentials)
        if record is None:
            raise MishkanError(
                ErrorCode.AUTHORITY_NOT_GRANTED,
                "daemon bearer credential is invalid",
            )
        return record

    authenticated = Depends(authenticate)

    @app.get("/v1/health")
    async def health() -> dict[str, str]:
        status = SchemaManager(paths.database).status()
        return {"status": "ready", "schema": status.head_revision}

    @app.post("/v1/commands", response_model=CommandResult)
    async def command(
        command: ApplicationCommand,
        principal: TokenRecord = authenticated,
    ) -> CommandResult:
        return await execute_command(command, principal.principal_id)

    @app.get("/v1/snapshot")
    async def snapshot(
        _principal: TokenRecord = authenticated,
    ) -> SnapshotEnvelope:
        current = await _thread_call(repository.snapshot, limit=daemon.event_page_limit)
        if knowledge_repository is None or config.knowledge is None:
            return current
        operations = await _thread_call(
            knowledge_repository.list_operations,
            limit=daemon.event_page_limit,
        )
        corpora = await _thread_call(
            knowledge_repository.list_corpora,
            limit=daemon.event_page_limit,
        )
        projections = dict(current.projections)
        projections["knowledge"] = {
            "sources": _knowledge_source_projection(config),
            "operations": [item.model_dump(mode="json") for item in operations],
            "corpora": [item.model_dump(mode="json") for item in corpora],
            "degraded": any(item.state.value in {"degraded", "failed"} for item in corpora),
        }
        return current.model_copy(update={"projections": projections})

    @app.get("/v1/telemetry/status")
    async def telemetry_status(
        _principal: TokenRecord = authenticated,
    ) -> TelemetryStatus:
        return telemetry_service.status()

    @app.get("/v1/knowledge/sources", response_model=None)
    async def knowledge_sources(
        _principal: TokenRecord = authenticated,
    ) -> dict[str, object]:
        _require_knowledge(knowledge_repository, knowledge_service, knowledge_mutations)
        return {"sources": _knowledge_source_projection(config)}

    @app.get("/v1/knowledge/queries/{query_id}", response_model=None)
    async def knowledge_query_get(
        query_id: UUID,
        _principal: TokenRecord = authenticated,
    ) -> dict[str, object]:
        knowledge = _require_knowledge(
            knowledge_repository, knowledge_service, knowledge_mutations
        )[0]
        record = await _thread_call(knowledge.query, query_id)
        attempts = await _thread_call(knowledge.attempts, query_id)
        return {
            "query": record.model_dump(mode="json"),
            "attempts": [item.model_dump(mode="json") for item in attempts],
        }

    @app.get("/v1/knowledge/corpora", response_model=None)
    async def knowledge_corpora(
        _principal: TokenRecord = authenticated,
        project_id: Annotated[str | None, Query(min_length=1, max_length=256)] = None,
        offset: Annotated[int, Query(ge=0)] = 0,
        limit: Annotated[int, Query(ge=1, le=1_000)] = 100,
    ) -> tuple[dict[str, object], ...]:
        knowledge = _require_knowledge(
            knowledge_repository, knowledge_service, knowledge_mutations
        )[0]
        records = await _thread_call(
            knowledge.list_corpora,
            project_id=project_id,
            offset=offset,
            limit=limit,
        )
        return tuple(item.model_dump(mode="json") for item in records)

    @app.get("/v1/knowledge/operations", response_model=None)
    async def knowledge_operations(
        _principal: TokenRecord = authenticated,
        project_id: Annotated[str | None, Query(min_length=1, max_length=256)] = None,
        offset: Annotated[int, Query(ge=0)] = 0,
        limit: Annotated[int, Query(ge=1, le=1_000)] = 100,
    ) -> tuple[dict[str, object], ...]:
        knowledge = _require_knowledge(
            knowledge_repository, knowledge_service, knowledge_mutations
        )[0]
        records = await _thread_call(
            knowledge.list_operations,
            project_id=project_id,
            offset=offset,
            limit=limit,
        )
        return tuple(item.model_dump(mode="json") for item in records)

    @app.get("/v1/knowledge/operations/{operation_id}", response_model=KnowledgeOperation)
    async def knowledge_operation_get(
        operation_id: UUID,
        _principal: TokenRecord = authenticated,
    ) -> KnowledgeOperation:
        knowledge = _require_knowledge(
            knowledge_repository, knowledge_service, knowledge_mutations
        )[0]
        return await _thread_call(knowledge.operation, operation_id)

    @app.get("/v1/knowledge/promotions", response_model=None)
    async def knowledge_promotions(
        _principal: TokenRecord = authenticated,
        project_id: Annotated[str | None, Query(min_length=1, max_length=256)] = None,
        offset: Annotated[int, Query(ge=0)] = 0,
        limit: Annotated[int, Query(ge=1, le=1_000)] = 100,
    ) -> tuple[dict[str, object], ...]:
        knowledge = _require_knowledge(
            knowledge_repository, knowledge_service, knowledge_mutations
        )[0]
        records = await _thread_call(
            knowledge.list_promotions,
            project_id=project_id,
            offset=offset,
            limit=limit,
        )
        return tuple(item.model_dump(mode="json") for item in records)

    @app.get("/v1/context/engineer-profile")
    async def confirmed_engineer_profile(
        _principal: TokenRecord = authenticated,
    ) -> EngineerProfile:
        if engineer_profile is None:
            raise MishkanError(
                ErrorCode.REQUIRED_DEPENDENCY,
                "A confirmed portable engineer profile is not configured",
            )
        return engineer_profile

    @app.get("/v1/context/community-candidates")
    async def community_candidates(
        _principal: TokenRecord = authenticated,
    ) -> dict[str, object]:
        candidates = community_recommendations.candidates()
        return {
            "candidates": [candidate.model_dump(mode="json") for candidate in candidates],
            "count": len(candidates),
            "activation_authorized": False,
        }

    @app.get("/v1/organization", response_model=None)
    async def organization(
        _principal: TokenRecord = authenticated,
    ) -> dict[str, object]:
        roster = load_canonical_organization()
        return roster.model_dump(mode="json")

    @app.get("/v1/organization/inspection", response_model=None)
    async def organization_inspection(
        _principal: TokenRecord = authenticated,
        limit: Annotated[int, Query(ge=1, le=1_000)] = 100,
    ) -> dict[str, object]:
        return await _thread_call(mission_inspections.organization, limit=limit)

    @app.get("/v1/organization/branches/{branch_id}/inspection", response_model=None)
    async def organization_branch_inspection(
        branch_id: str,
        _principal: TokenRecord = authenticated,
        limit: Annotated[int, Query(ge=1, le=1_000)] = 100,
    ) -> dict[str, object]:
        return await _thread_call(mission_inspections.branch, branch_id, limit=limit)

    @app.get(
        "/v1/organization/profiles/{identity_id}/competence",
        response_model=ProfessionalCompetenceState,
    )
    async def professional_competence_get(
        identity_id: str,
        kind: ProfessionalEvidenceKind,
        subject: Annotated[str, Query(min_length=1, max_length=512)],
        _principal: TokenRecord = authenticated,
    ) -> ProfessionalCompetenceState:
        return await _thread_call(
            professional_evolution.competence_state,
            identity_id,
            kind=kind,
            subject=subject,
        )

    @app.get(
        "/v1/organization/profiles/{identity_id}/evidence",
        response_model=None,
    )
    async def professional_evidence_list(
        identity_id: str,
        _principal: TokenRecord = authenticated,
        kind: ProfessionalEvidenceKind | None = None,
        subject: Annotated[str | None, Query(min_length=1, max_length=512)] = None,
        offset: Annotated[int, Query(ge=0)] = 0,
        limit: Annotated[int, Query(ge=1, le=1_000)] = 100,
    ) -> tuple[dict[str, object], ...]:
        records: tuple[ProfessionalEvidenceRecord, ...] = await _thread_call(
            professional_evolution.evidence,
            identity_id,
            kind=kind,
            subject=subject,
            offset=offset,
            limit=limit,
        )
        return tuple(record.model_dump(mode="json") for record in records)

    @app.get(
        "/v1/organization/profiles/{identity_id}/promotions",
        response_model=None,
    )
    async def professional_promotion_list(
        identity_id: str,
        _principal: TokenRecord = authenticated,
        kind: ProfessionalEvidenceKind | None = None,
        subject: Annotated[str | None, Query(min_length=1, max_length=512)] = None,
        offset: Annotated[int, Query(ge=0)] = 0,
        limit: Annotated[int, Query(ge=1, le=1_000)] = 100,
    ) -> tuple[dict[str, object], ...]:
        records: tuple[ProfessionalPromotionDecision, ...] = await _thread_call(
            professional_evolution.promotion_history,
            identity_id,
            kind=kind,
            subject=subject,
            offset=offset,
            limit=limit,
        )
        return tuple(record.model_dump(mode="json") for record in records)

    @app.get("/v1/missions", response_model=None)
    async def mission_list(
        _principal: TokenRecord = authenticated,
        limit: Annotated[int, Query(ge=1, le=1_000)] = 100,
    ) -> tuple[dict[str, object], ...]:
        records = await _thread_call(mission_repository.list_missions, limit=limit)
        return tuple(record.model_dump(mode="json") for record in records)

    @app.get("/v1/mission-templates", response_model=None)
    async def mission_template_list(
        _principal: TokenRecord = authenticated,
        signal: Annotated[list[str] | None, Query()] = None,
        organization_version: str = "1",
    ) -> tuple[dict[str, object], ...]:
        records = (
            mission_templates.catalogue.templates
            if signal is None
            else mission_templates.applicable(
                tuple(signal), organization_version=organization_version
            )
        )
        return tuple(record.model_dump(mode="json") for record in records)

    @app.get("/v1/missions/{mission_id}", response_model=MissionRecord)
    async def mission_get(
        mission_id: UUID,
        _principal: TokenRecord = authenticated,
    ) -> MissionRecord:
        return await _thread_call(mission_repository.mission, str(mission_id))

    @app.get("/v1/missions/{mission_id}/brief", response_model=MissionBrief)
    async def mission_brief_get(
        mission_id: UUID,
        _principal: TokenRecord = authenticated,
        version: Annotated[int | None, Query(ge=1)] = None,
    ) -> MissionBrief:
        return await _thread_call(mission_repository.brief, str(mission_id), version)

    @app.get("/v1/missions/{mission_id}/crew", response_model=MissionCrewRevision)
    async def mission_crew_get(
        mission_id: UUID,
        _principal: TokenRecord = authenticated,
        version: Annotated[int | None, Query(ge=1)] = None,
    ) -> MissionCrewRevision:
        return await _thread_call(mission_repository.crew, str(mission_id), version)

    @app.get(
        "/v1/missions/{mission_id}/environment-plan",
        response_model=MissionEnvironmentPlanAcceptance,
    )
    async def mission_environment_plan_get(
        mission_id: UUID,
        _principal: TokenRecord = authenticated,
        version: Annotated[int | None, Query(ge=1)] = None,
    ) -> MissionEnvironmentPlanAcceptance:
        return await _thread_call(
            mission_repository.environment_plan,
            str(mission_id),
            version,
        )

    @app.get(
        "/v1/missions/{mission_id}/readiness",
        response_model=MissionEnvironmentReadiness,
    )
    async def mission_environment_readiness(
        mission_id: UUID,
        _principal: TokenRecord = authenticated,
    ) -> MissionEnvironmentReadiness:
        return await _thread_call(mission_readiness.inspect, str(mission_id))

    @app.get(
        "/v1/missions/{mission_id}/tasks/{task_id}/eligibility",
        response_model=MissionTaskEligibility,
    )
    async def mission_task_eligibility(
        mission_id: UUID,
        task_id: str,
        _principal: TokenRecord = authenticated,
    ) -> MissionTaskEligibility:
        return await _thread_call(mission_task_claims.inspect, str(mission_id), task_id)

    @app.get(
        "/v1/missions/{mission_id}/completion-readiness",
        response_model=MissionCompletionReadiness,
    )
    async def mission_completion_readiness(
        mission_id: UUID,
        _principal: TokenRecord = authenticated,
    ) -> MissionCompletionReadiness:
        return await _thread_call(mission_task_claims.inspect_completion, str(mission_id))

    @app.get("/v1/missions/{mission_id}/assignments", response_model=None)
    async def mission_assignments(
        mission_id: UUID,
        _principal: TokenRecord = authenticated,
        limit: Annotated[int, Query(ge=1, le=1_000)] = 1_000,
    ) -> tuple[dict[str, object], ...]:
        records = await _thread_call(mission_repository.assignments, str(mission_id), limit=limit)
        return tuple(record.model_dump(mode="json") for record in records)

    @app.get("/v1/missions/{mission_id}/run-bindings", response_model=None)
    async def mission_run_bindings(
        mission_id: UUID,
        _principal: TokenRecord = authenticated,
        limit: Annotated[int, Query(ge=1, le=1_000)] = 1_000,
    ) -> tuple[dict[str, object], ...]:
        records = await _thread_call(
            mission_repository.run_bindings,
            str(mission_id),
            limit=limit,
        )
        return tuple(record.model_dump(mode="json") for record in records)

    @app.get("/v1/missions/{mission_id}/run-reports", response_model=None)
    async def mission_run_reports(
        mission_id: UUID,
        _principal: TokenRecord = authenticated,
        limit: Annotated[int, Query(ge=1, le=1_000)] = 1_000,
    ) -> tuple[dict[str, object], ...]:
        records = await _thread_call(
            mission_repository.run_reports,
            str(mission_id),
            limit=limit,
        )
        return tuple(record.model_dump(mode="json") for record in records)

    @app.get("/v1/missions/{mission_id}/transitions", response_model=None)
    async def mission_transitions(
        mission_id: UUID,
        _principal: TokenRecord = authenticated,
        limit: Annotated[int, Query(ge=1, le=1_000)] = 1_000,
    ) -> tuple[dict[str, object], ...]:
        records = await _thread_call(mission_repository.transitions, str(mission_id), limit=limit)
        return tuple(record.model_dump(mode="json") for record in records)

    @app.get("/v1/conversations", response_model=None)
    async def conversation_list(
        _principal: TokenRecord = authenticated,
        mission_id: UUID | None = None,
        limit: Annotated[int, Query(ge=1, le=1_000)] = 100,
    ) -> tuple[dict[str, object], ...]:
        records = await _thread_call(
            conversation_repository.channels,
            mission_id=str(mission_id) if mission_id is not None else None,
            limit=limit,
        )
        return tuple(record.model_dump(mode="json") for record in records)

    @app.get("/v1/conversations/{conversation_id}", response_model=ConversationChannel)
    async def conversation_get(
        conversation_id: UUID,
        _principal: TokenRecord = authenticated,
    ) -> ConversationChannel:
        return await _thread_call(conversation_repository.channel, str(conversation_id))

    @app.get("/v1/conversations/{conversation_id}/messages", response_model=None)
    async def conversation_messages(
        conversation_id: UUID,
        _principal: TokenRecord = authenticated,
        limit: Annotated[int, Query(ge=1, le=1_000)] = 100,
    ) -> tuple[dict[str, object], ...]:
        records = await _thread_call(
            conversation_repository.messages, str(conversation_id), limit=limit
        )
        return tuple(record.model_dump(mode="json") for record in records)

    @app.get("/v1/missions/{mission_id}/escalations", response_model=None)
    async def mission_escalations(
        mission_id: UUID,
        _principal: TokenRecord = authenticated,
        state: EscalationState | None = None,
        limit: Annotated[int, Query(ge=1, le=1_000)] = 100,
    ) -> tuple[dict[str, object], ...]:
        records = await _thread_call(
            conversation_repository.escalations,
            str(mission_id),
            state=state,
            limit=limit,
        )
        return tuple(record.model_dump(mode="json") for record in records)

    @app.get("/v1/missions/{mission_id}/decisions", response_model=None)
    async def mission_decisions(
        mission_id: UUID,
        _principal: TokenRecord = authenticated,
        limit: Annotated[int, Query(ge=1, le=1_000)] = 100,
    ) -> tuple[dict[str, object], ...]:
        records = await _thread_call(
            conversation_repository.decisions,
            str(mission_id),
            limit=limit,
        )
        return tuple(record.model_dump(mode="json") for record in records)

    @app.get("/v1/missions/{mission_id}/interventions", response_model=None)
    async def mission_interventions(
        mission_id: UUID,
        _principal: TokenRecord = authenticated,
        limit: Annotated[int, Query(ge=1, le=1_000)] = 100,
    ) -> tuple[dict[str, object], ...]:
        records = await _thread_call(
            conversation_repository.interventions, str(mission_id), limit=limit
        )
        return tuple(record.model_dump(mode="json") for record in records)

    @app.get("/v1/missions/{mission_id}/inspection", response_model=None)
    async def mission_inspection(
        mission_id: UUID,
        _principal: TokenRecord = authenticated,
        limit: Annotated[int, Query(ge=1, le=1_000)] = 100,
    ) -> dict[str, object]:
        return await _thread_call(mission_inspections.mission, str(mission_id), limit=limit)

    @app.get("/v1/tools/registry")
    async def tool_registry(
        _principal: TokenRecord = authenticated,
        limit: Annotated[int, Query(ge=1, le=1_000)] = 1_000,
    ) -> dict[str, object]:
        entries = await _thread_call(registry_lifecycle.entries, limit=limit)
        return {
            "entries": [entry.model_dump(mode="json") for entry in entries],
            "count": len(entries),
        }

    @app.get("/v1/events")
    async def events(
        _principal: TokenRecord = authenticated,
        after: Annotated[int, Query(ge=0)] = 0,
        limit: Annotated[int | None, Query(ge=1, le=1_000)] = None,
        event_type: Annotated[list[str] | None, Query()] = None,
        entity_type: str | None = None,
        entity_id: str | None = None,
        run_id: str | None = None,
        task_id: str | None = None,
        identity_id: str | None = None,
        team_id: str | None = None,
        occurred_after: datetime | None = None,
        occurred_before: datetime | None = None,
        security_relevant: bool | None = None,
    ) -> EventPage:
        return await _thread_call(
            repository.events,
            after_cursor=after,
            limit=limit or daemon.event_page_limit,
            event_types=tuple(event_type or ()),
            entity_type=entity_type,
            entity_id=entity_id,
            run_id=run_id,
            task_id=task_id,
            identity_id=identity_id,
            team_id=team_id,
            occurred_after=occurred_after,
            occurred_before=occurred_before,
            security_relevant=security_relevant,
        )

    @app.get("/v1/notifications", response_model=NotificationPage)
    async def notifications(
        _principal: TokenRecord = authenticated,
        after: Annotated[int, Query(ge=0)] = 0,
        limit: Annotated[int | None, Query(ge=1, le=1_000)] = None,
        severity: Annotated[list[NotificationSeverity] | None, Query()] = None,
        delivery: Annotated[list[NotificationDelivery] | None, Query()] = None,
    ) -> NotificationPage:
        event_page = await _thread_call(
            repository.events,
            after_cursor=after,
            limit=limit or config.notifications.page_limit,
        )
        return notification_service.project(
            event_page,
            severities=frozenset(severity or ()),
            deliveries=frozenset(delivery or ()),
        )

    @app.get("/v1/events/holds")
    async def event_holds(
        _principal: TokenRecord = authenticated,
        active_only: bool = False,
    ) -> tuple[EventEvidenceHold, ...]:
        return await _thread_call(repository.event_holds, active_only=active_only)

    @app.get("/v1/events/retention-policy")
    async def event_retention_policy_query(
        _principal: TokenRecord = authenticated,
    ) -> EventRetentionPolicy:
        return EventRetentionPolicy(
            max_age_days=persistence.event_retention_days,
            batch_size=daemon.event_page_limit,
        )

    @app.get("/v1/events/retention-plans")
    async def event_retention_plans(
        _principal: TokenRecord = authenticated,
    ) -> tuple[EventRetentionPlan, ...]:
        return await _thread_call(repository.event_retention_plans)

    @app.get("/v1/events/stream")
    async def event_stream(
        request: Request,
        _principal: TokenRecord = authenticated,
        after: Annotated[int, Query(ge=0)] = 0,
        last_event_id: Annotated[str | None, Header(alias="Last-Event-ID")] = None,
        event_type: Annotated[list[str] | None, Query()] = None,
        entity_type: str | None = None,
        entity_id: str | None = None,
        run_id: str | None = None,
        task_id: str | None = None,
        identity_id: str | None = None,
        team_id: str | None = None,
        occurred_after: datetime | None = None,
        occurred_before: datetime | None = None,
        security_relevant: bool | None = None,
    ) -> StreamingResponse:
        cursor = after
        if last_event_id is not None:
            try:
                cursor = max(cursor, int(last_event_id))
            except ValueError as exc:
                raise MishkanError(
                    ErrorCode.OUTPUT_CONTRACT,
                    "Last-Event-ID must contain an integer event cursor",
                ) from exc
        initial = await _thread_call(
            repository.events,
            after_cursor=cursor,
            limit=daemon.event_page_limit,
            event_types=tuple(event_type or ()),
            entity_type=entity_type,
            entity_id=entity_id,
            run_id=run_id,
            task_id=task_id,
            identity_id=identity_id,
            team_id=team_id,
            occurred_after=occurred_after,
            occurred_before=occurred_before,
            security_relevant=security_relevant,
        )

        async def stream() -> AsyncIterator[str]:
            current = cursor
            page = initial
            heartbeat_elapsed = 0.0
            while True:
                if await request.is_disconnected():
                    return
                if page.events:
                    for item in page.events:
                        data = json.dumps(
                            item.model_dump(mode="json"),
                            sort_keys=True,
                            separators=(",", ":"),
                        )
                        yield f"id: {item.cursor}\nevent: {item.event_type}\ndata: {data}\n\n"
                    current = page.next_cursor
                    heartbeat_elapsed = 0.0
                await asyncio.sleep(daemon.event_poll_seconds)
                heartbeat_elapsed += daemon.event_poll_seconds
                if heartbeat_elapsed >= daemon.heartbeat_seconds:
                    yield f": heartbeat {current}\n\n"
                    heartbeat_elapsed = 0.0
                page = await _thread_call(
                    repository.events,
                    after_cursor=current,
                    limit=daemon.event_page_limit,
                    event_types=tuple(event_type or ()),
                    entity_type=entity_type,
                    entity_id=entity_id,
                    run_id=run_id,
                    task_id=task_id,
                    identity_id=identity_id,
                    team_id=team_id,
                    occurred_after=occurred_after,
                    occurred_before=occurred_before,
                    security_relevant=security_relevant,
                )

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.get("/v1/artifacts")
    async def artifact_list(
        _principal: TokenRecord = authenticated,
        offset: Annotated[int, Query(ge=0)] = 0,
        limit: Annotated[int, Query(ge=1, le=1_000)] = 100,
    ) -> tuple[ArtifactManifest, ...]:
        return await _thread_call(artifacts.list_manifests, offset=offset, limit=limit)

    @app.get("/v1/artifacts/{artifact_id}")
    async def artifact_manifest(
        artifact_id: str,
        _principal: TokenRecord = authenticated,
    ) -> ArtifactManifest:
        return await _thread_call(artifacts.manifest, f"artifact:{artifact_id}")

    @app.get("/v1/artifacts/{artifact_id}/content")
    async def artifact_content(
        artifact_id: str,
        _principal: TokenRecord = authenticated,
    ) -> StreamingResponse:
        manifest = await _thread_call(artifacts.manifest, f"artifact:{artifact_id}")

        def body() -> Iterator[bytes]:
            yield from artifacts.iter_bytes(
                manifest.reference, chunk_size=artifact_config.chunk_bytes
            )

        return StreamingResponse(
            body(),
            media_type=manifest.detected_media_type or manifest.declared_media_type,
            headers={"Content-Length": str(manifest.size_bytes)},
        )

    @app.get("/v1/artifact-uploads/{upload_id}")
    async def artifact_upload(
        upload_id: UUID,
        _principal: TokenRecord = authenticated,
    ) -> UploadSession:
        return await _thread_call(artifacts.upload, upload_id)

    @app.get("/v1/artifact-collections")
    async def artifact_collection_list(
        _principal: TokenRecord = authenticated,
        offset: Annotated[int, Query(ge=0)] = 0,
        limit: Annotated[int, Query(ge=1, le=1_000)] = 100,
    ) -> tuple[ArtifactCollection, ...]:
        return await _thread_call(artifacts.list_collections, offset=offset, limit=limit)

    @app.get("/v1/artifact-references")
    async def artifact_reference_list(
        _principal: TokenRecord = authenticated,
        offset: Annotated[int, Query(ge=0)] = 0,
        limit: Annotated[int, Query(ge=1, le=1_000)] = 100,
    ) -> tuple[WorkingReference, ...]:
        return await _thread_call(artifacts.list_references, offset=offset, limit=limit)

    @app.get("/v1/artifact-holds")
    async def artifact_hold_list(
        _principal: TokenRecord = authenticated,
        offset: Annotated[int, Query(ge=0)] = 0,
        limit: Annotated[int, Query(ge=1, le=1_000)] = 100,
    ) -> tuple[ArtifactEvidenceHold, ...]:
        return await _thread_call(artifacts.list_holds, offset=offset, limit=limit)

    @app.get("/v1/artifact-pins")
    async def artifact_pin_list(
        _principal: TokenRecord = authenticated,
        offset: Annotated[int, Query(ge=0)] = 0,
        limit: Annotated[int, Query(ge=1, le=1_000)] = 100,
    ) -> tuple[ArtifactPin, ...]:
        return await _thread_call(artifacts.list_pins, offset=offset, limit=limit)

    @app.get("/v1/change-sets")
    async def change_set_list(
        _principal: TokenRecord = authenticated,
        offset: Annotated[int, Query(ge=0)] = 0,
        limit: Annotated[int, Query(ge=1, le=1_000)] = 100,
    ) -> tuple[ChangeSetResult, ...]:
        return await _thread_call(changes.list, offset=offset, limit=limit)

    @app.get("/v1/change-sets/{change_set_id}")
    async def change_set_get(
        change_set_id: UUID,
        _principal: TokenRecord = authenticated,
    ) -> ChangeSetResult:
        return await _thread_call(changes.get, change_set_id)

    @app.get("/v1/sessions")
    async def session_list(
        _principal: TokenRecord = authenticated,
        offset: Annotated[int, Query(ge=0)] = 0,
        limit: Annotated[int, Query(ge=1, le=1_000)] = 100,
    ) -> tuple[ExecutionSession, ...]:
        return await _thread_call(supervisor.list, offset=offset, limit=limit)

    @app.get("/v1/sessions/{session_id}")
    async def session_get(
        session_id: UUID,
        _principal: TokenRecord = authenticated,
    ) -> ExecutionSession:
        return await _thread_call(supervisor.status, session_id)

    @app.get("/v1/sessions/{session_id}/output")
    async def session_output(
        session_id: UUID,
        _principal: TokenRecord = authenticated,
        channel: Annotated[str, Query(pattern=r"^(stdout|stderr)$")] = "stdout",
        offset: Annotated[int, Query(ge=0)] = 0,
        limit: Annotated[int, Query(ge=1, le=16_777_216)] = 65_536,
        binary: bool = False,
    ) -> CursorRead:
        selected: Literal["stdout", "stderr"] = "stdout" if channel == "stdout" else "stderr"
        return await _thread_call(
            supervisor.read,
            session_id,
            channel=selected,
            offset=offset,
            limit=limit,
            binary=binary,
        )

    @app.get("/v1/runs")
    async def run_list(
        _principal: TokenRecord = authenticated,
        offset: Annotated[int, Query(ge=0)] = 0,
        limit: Annotated[int, Query(ge=1, le=1_000)] = 100,
    ) -> tuple[dict[str, object], ...]:
        return await _thread_call(repository.runs, offset=offset, limit=limit)

    @app.get("/v1/runs/{run_id}/tasks")
    async def task_list(
        run_id: str,
        _principal: TokenRecord = authenticated,
        offset: Annotated[int, Query(ge=0)] = 0,
        limit: Annotated[int, Query(ge=1, le=1_000)] = 100,
    ) -> tuple[dict[str, object], ...]:
        return await _thread_call(repository.tasks, run_id, offset=offset, limit=limit)

    @app.get("/v1/runs/{run_id}/review-rejections")
    async def review_rejection_list(
        run_id: str,
        _principal: TokenRecord = authenticated,
    ) -> tuple[TaskReviewRejection, ...]:
        return await _thread_call(run_repository.rejected_reviews, run_id)

    @app.get("/v1/skills")
    async def skill_version_list(
        _principal: TokenRecord = authenticated,
        offset: Annotated[int, Query(ge=0)] = 0,
        limit: Annotated[int, Query(ge=1, le=1_000)] = 100,
        name: str | None = None,
    ) -> tuple[SkillVersionRecord, ...]:
        if skill_lifecycle is None:
            return ()
        return await _thread_call(
            skill_lifecycle.list_versions,
            offset=offset,
            limit=limit,
            skill_name=name,
        )

    @app.get("/v1/skills/{skill_name}/active")
    async def active_skill_version(
        skill_name: str,
        _principal: TokenRecord = authenticated,
    ) -> SkillVersionRecord | None:
        if skill_lifecycle is None:
            return None
        return await _thread_call(skill_lifecycle.active, skill_name)

    @app.get("/v1/skill-usage/summary")
    async def skill_usage_summary(
        task_class: str,
        _principal: TokenRecord = authenticated,
        skill_name: str | None = None,
    ) -> SkillUsageSummary:
        if skill_usage is None:
            raise MishkanError(ErrorCode.REQUIRED_DEPENDENCY, "Skills capability is not configured")
        return await _thread_call(
            skill_usage.summary,
            task_class,
            requested_skill=skill_name,
        )

    @app.get("/v1/skill-learning")
    async def skill_learning_list(
        _principal: TokenRecord = authenticated,
        offset: Annotated[int, Query(ge=0)] = 0,
        limit: Annotated[int, Query(ge=1, le=1_000)] = 100,
    ) -> tuple[SkillLearningRecord, ...]:
        if skill_learning_repository is None:
            return ()
        return await _thread_call(skill_learning_repository.list, offset=offset, limit=limit)

    @app.get("/v1/skill-learning/{request_id}")
    async def skill_learning_get(
        request_id: UUID,
        _principal: TokenRecord = authenticated,
    ) -> SkillLearningRecord:
        if skill_learning_repository is None:
            raise MishkanError(ErrorCode.REQUIRED_DEPENDENCY, "Skill learning is not configured")
        return await _thread_call(skill_learning_repository.get, str(request_id))

    @app.get("/v1/skill-updates")
    async def skill_update_report(
        _principal: TokenRecord = authenticated,
    ) -> SkillUpdateReport:
        if skill_maintenance is None:
            raise MishkanError(ErrorCode.REQUIRED_DEPENDENCY, "Skill maintenance is not configured")
        return await _thread_call(skill_maintenance.updates)

    @app.get("/v1/skill-curation")
    async def skill_curation_proposals(
        _principal: TokenRecord = authenticated,
    ) -> tuple[SkillCurationProposal, ...]:
        if skill_maintenance is None:
            return ()
        return await _thread_call(skill_maintenance.curation)

    @app.get("/v1/environment/observations/{observation_id}")
    async def environment_observation_get(
        observation_id: UUID,
        _principal: TokenRecord = authenticated,
    ) -> EnvironmentObservation:
        if environment_repository is None:
            raise MishkanError(
                ErrorCode.REQUIRED_DEPENDENCY,
                "Environment capability is not configured",
            )
        return await _thread_call(environment_repository.observation, str(observation_id))

    @app.get("/v1/environment/observations/{observation_id}/command-candidates")
    async def environment_command_candidates(
        observation_id: UUID,
        _principal: TokenRecord = authenticated,
    ) -> tuple[EngineeringCommandCandidate, ...]:
        if environment_repository is None or technical_pack_service is None:
            raise MishkanError(
                ErrorCode.REQUIRED_DEPENDENCY,
                "Engineering command packs are not configured",
            )
        observation = await _thread_call(environment_repository.observation, str(observation_id))
        return await _thread_call(technical_pack_service.candidates, observation)

    @app.get("/v1/environment/bindings/{binding_id}")
    async def environment_binding_get(
        binding_id: UUID,
        _principal: TokenRecord = authenticated,
    ) -> EnvironmentBinding:
        if environment_repository is None:
            raise MishkanError(
                ErrorCode.REQUIRED_DEPENDENCY,
                "Environment capability is not configured",
            )
        return await _thread_call(environment_repository.binding, str(binding_id))

    @app.get("/v1/environment/descriptor-sets/{descriptor_set_id}")
    async def environment_descriptor_set_get(
        descriptor_set_id: UUID,
        _principal: TokenRecord = authenticated,
    ) -> EnvironmentDescriptorSet:
        if environment_repository is None:
            raise MishkanError(
                ErrorCode.REQUIRED_DEPENDENCY,
                "Environment capability is not configured",
            )
        return await _thread_call(
            environment_repository.descriptor_set,
            str(descriptor_set_id),
        )

    @app.get("/v1/environment/attempts/{attempt_id}")
    async def environment_attempt_get(
        attempt_id: UUID,
        _principal: TokenRecord = authenticated,
    ) -> EnvironmentAttempt:
        if environment_repository is None:
            raise MishkanError(
                ErrorCode.REQUIRED_DEPENDENCY,
                "Environment capability is not configured",
            )
        return await _thread_call(environment_repository.attempt, str(attempt_id))

    @app.get("/v1/environment/verifications/{verification_id}")
    async def environment_verification_get(
        verification_id: UUID,
        _principal: TokenRecord = authenticated,
    ) -> EnvironmentVerification:
        if environment_repository is None:
            raise MishkanError(
                ErrorCode.REQUIRED_DEPENDENCY,
                "Environment capability is not configured",
            )
        return await _thread_call(
            environment_repository.verification,
            str(verification_id),
        )

    @app.get("/v1/mcp/connections")
    async def mcp_connection_list(
        _principal: TokenRecord = authenticated,
        offset: Annotated[int, Query(ge=0)] = 0,
        limit: Annotated[int, Query(ge=1, le=1_000)] = 100,
    ) -> tuple[dict[str, object], ...]:
        if mcp_repository is None:
            return ()
        connections = await _thread_call(
            mcp_repository.list_connections,
            offset=offset,
            limit=limit,
        )
        return tuple(item.model_dump(mode="json") for item in connections)

    @app.get("/v1/mcp/connections/{connection_id}/primitives")
    async def mcp_primitive_list(
        connection_id: str,
        _principal: TokenRecord = authenticated,
    ) -> tuple[dict[str, object], ...]:
        if mcp_repository is None:
            return ()
        primitives = await _thread_call(mcp_repository.list_primitives, connection_id)
        return tuple(item.model_dump(mode="json") for item in primitives)

    @app.get("/v1/mcp/connections/{connection_id}/contracts")
    async def mcp_contract_list(
        connection_id: str,
        _principal: TokenRecord = authenticated,
    ) -> tuple[dict[str, object], ...]:
        if mcp_repository is None or mcp_config is None:
            return ()
        factory = McpContractFactory(mcp_config)
        primitives = await _thread_call(mcp_repository.list_primitives, connection_id)
        return tuple(
            factory.build(connection_id, item).model_dump(mode="json")
            for item in primitives
            if item.kind is McpPrimitiveKind.TOOL
        )

    @app.get("/v1/mcp/calls")
    async def mcp_call_list(
        _principal: TokenRecord = authenticated,
        offset: Annotated[int, Query(ge=0)] = 0,
        limit: Annotated[int, Query(ge=1, le=1_000)] = 100,
    ) -> tuple[dict[str, object], ...]:
        if mcp_repository is None:
            return ()
        return await _thread_call(mcp_repository.list_calls, offset=offset, limit=limit)

    @app.get("/v1/mcp/calls/{request_id}/progress")
    async def mcp_progress_list(
        request_id: UUID,
        _principal: TokenRecord = authenticated,
        cursor: Annotated[int, Query(ge=0)] = 0,
        limit: Annotated[int, Query(ge=1, le=10_000)] = 100,
    ) -> tuple[dict[str, object], ...]:
        if mcp_repository is None:
            return ()
        assert mcp_config is not None
        progress = await _thread_call(
            mcp_repository.progress_after,
            request_id,
            cursor,
            limit=min(limit, mcp_config.progress_page_limit),
        )
        return tuple(item.model_dump(mode="json") for item in progress)

    if mcp_http is not None:
        assert mcp_config is not None
        app.mount(mcp_config.facade.streamable_http_path, mcp_http.app)

    return app


def _event_projection(
    command: ApplicationCommand,
    result_payload: dict[str, object],
    authorized: AuthorizedApplicationCommand,
) -> dict[str, object]:
    """Describe a command settlement without copying effect inputs or result bodies."""

    return {
        "command_type": command.command_type,
        "request_schema_version": command.schema_version,
        "payload_fields": sorted(command.payload),
        "result_fields": sorted(result_payload),
        **_authorization_projection(authorized),
    }


def _authorization_projection(
    authorized: AuthorizedApplicationCommand,
) -> dict[str, object]:
    return {
        "authorization_request_fingerprint": authorized.request.fingerprint,
        "policy_fingerprint": authorized.decision.policy_fingerprint,
        "policy_revisions": list(authorized.decision.policy_revisions),
        "matched_rule_ids": list(authorized.decision.matched_rule_ids),
        "authorization_decision": authorized.decision.decision.value,
    }


def _dispatch(
    command: ApplicationCommand,
    authorized: AuthorizedApplicationCommand,
    repository: SQLiteApplicationRepository,
    event_retention_policy: EventRetentionPolicy,
    artifacts: DurableArtifactService,
    changes: ChangeSetService,
    git_effects: GovernedGitService,
    effective_policy: EffectivePolicy,
    supervisor: SessionSupervisor,
    runs: LocalRunRepository,
    workspace: Path,
    mcp_runner: McpServiceRunner | None,
    mcp_config: McpConfig | None,
    resolved_credentials: dict[str, str],
    skill_lifecycle: SQLiteSkillLifecycleRepository | None,
    skill_usage: SQLiteSkillUsageRepository | None,
    skill_inspector: SkillPackageInspector | None,
    skill_invocation_service: SkillInvocationService | None,
    skill_learning_service: SkillLearningService | None,
    environment_repository: SQLiteEnvironmentRepository | None,
    environment_observer: EnvironmentObserver | None,
    environment_resolver: EnvironmentResolver | None,
    environment_descriptor_validator: EnvironmentDescriptorValidator | None,
    environment_descriptor_change_planner: EnvironmentDescriptorChangePlanner | None,
    environment_operation_planner: EnvironmentOperationPlanner | None,
    environment_evidence_service: EnvironmentEvidenceService | None,
    technical_pack_service: TechnicalPackService | None,
    telemetry_evaluation_service: TelemetryEvaluationService,
    knowledge_service: KnowledgeService | None,
    knowledge_mutations: KnowledgeMutationService | None,
    community_recommendations: ContextualRecommendationService,
    mission_repository: SQLiteMissionRepository,
    conversation_repository: SQLiteConversationRepository,
    mission_governance: MissionGovernanceRunner,
    mission_environment_planning: MissionEnvironmentPlanningRunner,
    environment_profile: EnvironmentProfile | None,
    professional_evolution: SQLiteProfessionalEvolutionRepository,
    mission_task_claims: MissionTaskClaimService,
) -> tuple[str, dict[str, object]]:
    payload = command.payload
    if command.command_type == "knowledge.query":
        if knowledge_service is None or authorized.knowledge_query is None:
            raise MishkanError(
                ErrorCode.REQUIRED_DEPENDENCY,
                "knowledge query capability is unavailable",
            )
        bundle = knowledge_service.query(authorized.knowledge_query)
        return "knowledge.query_settled", bundle.model_dump(mode="json")
    if command.command_type.startswith("knowledge."):
        if knowledge_mutations is None:
            raise MishkanError(
                ErrorCode.REQUIRED_DEPENDENCY,
                "knowledge mutation capability is unavailable",
            )
        if command.command_type == "knowledge.ingest" and authorized.knowledge_ingest:
            result = knowledge_mutations.ingest(authorized.knowledge_ingest)
            return "knowledge.operation_settled", result.model_dump(mode="json")
        if command.command_type == "knowledge.refresh" and authorized.knowledge_refresh:
            result = knowledge_mutations.refresh(authorized.knowledge_refresh)
            return "knowledge.operation_settled", result.model_dump(mode="json")
        if (
            command.command_type == "knowledge.memory.capture"
            and authorized.knowledge_memory_capture
        ):
            result = knowledge_mutations.capture_memory(authorized.knowledge_memory_capture)
            return "knowledge.operation_settled", result.model_dump(mode="json")
        if command.command_type == "knowledge.operation.cancel" and command.target_id:
            result = knowledge_mutations.cancel(UUID(command.target_id))
            return "knowledge.operation_settled", result.model_dump(mode="json")
        if (
            command.command_type == "knowledge.operation.reconcile"
            and authorized.knowledge_reconcile
        ):
            result = knowledge_mutations.reconcile(authorized.knowledge_reconcile)
            return "knowledge.operation_reconciled", result.model_dump(mode="json")
        if command.command_type == "knowledge.promotion.propose" and authorized.knowledge_promotion:
            promotion = knowledge_mutations.propose_promotion(authorized.knowledge_promotion)
            return "knowledge.promotion_proposed", promotion.model_dump(mode="json")
        if (
            command.command_type == "knowledge.promotion.decide"
            and authorized.knowledge_promotion_decision
        ):
            promotion = knowledge_mutations.decide_promotion(
                authorized.knowledge_promotion_decision,
                policy_fingerprint=f"sha256:{authorized.decision.policy_fingerprint}",
            )
            return "knowledge.promotion_decided", promotion.model_dump(mode="json")
        raise MishkanError(
            ErrorCode.OUTPUT_CONTRACT,
            "knowledge command payload is incomplete",
        )
    if command.command_type == "system.checkpoint" and command.target_type == "system":
        return "system.checkpoint_recorded", {"recorded": True}
    if command.command_type == "run.prospective.create":
        prospective_request = authorized.prospective_run_request
        if prospective_request is None:
            raise MishkanError(
                ErrorCode.OUTPUT_CONTRACT,
                "authorized prospective run request is absent",
            )
        discovery = ProspectiveWorkspaceInspector().inspect(
            workspace,
            workspace_id=prospective_request.workspace_id,
        )
        snapshot = runs.start_or_resume(
            discovery,
            prospective_request.objective,
            prospective_request.outcome_id,
        )
        return "run.prospective_created", {
            "run_id": snapshot.run_id,
            "resumed": snapshot.resumed,
            "execution_context": snapshot.execution_context.model_dump(mode="json"),
        }
    if command.command_type == "run.repository.establish" and command.target_id is not None:
        establishment_request = authorized.repository_establishment_request
        if establishment_request is None:
            raise MishkanError(
                ErrorCode.OUTPUT_CONTRACT,
                "authorized repository establishment request is absent",
            )
        discovery = ProspectiveWorkspaceInspector().inspect(
            workspace,
            workspace_id=establishment_request.prospective_workspace_id,
        )
        if discovery.binding.context_revision != establishment_request.discovery_revision:
            raise MishkanError(
                ErrorCode.REVISION_MISMATCH,
                "prospective workspace discovery revision changed before establishment",
            )
        if not isinstance(discovery.binding, ProspectiveWorkspaceBinding):
            raise MishkanError(ErrorCode.PROJECT, "prospective workspace binding is invalid")
        establishment = RepositoryEstablishment(
            run_id=command.target_id,
            prospective_workspace=discovery.binding,
            repository=RepositoryInspector().bind(workspace),
            evidence_references=establishment_request.evidence_references,
            established_by=command.actor_id,
        )
        snapshot = runs.record_repository_establishment(establishment)
        return "run.repository_established", {
            "run_id": snapshot.run_id,
            "execution_context": snapshot.execution_context.model_dump(mode="json"),
            "repository_establishment": establishment.model_dump(mode="json"),
        }
    if command.command_type == "telemetry.evaluation.import":
        evaluation_request = authorized.telemetry_evaluation
        if evaluation_request is None:
            raise MishkanError(
                ErrorCode.OUTPUT_CONTRACT,
                "authorized telemetry evaluation import is absent",
            )
        evaluation: TelemetryEvaluationImportResult = telemetry_evaluation_service.import_langsmith(
            evaluation_request,
            policy_fingerprint=authorized.decision.policy_fingerprint,
            resolved_secrets=tuple(resolved_credentials.values()),
        )
        return "telemetry.evaluation_imported", evaluation.model_dump(mode="json")
    if command.command_type == "context.recommend":
        recommendation_request = authorized.context_recommendation
        if recommendation_request is None:
            raise MishkanError(
                ErrorCode.OUTPUT_CONTRACT,
                "authorized contextual recommendation request is absent",
            )
        recommendation: ContextualRecommendation = community_recommendations.recommend(
            recommendation_request
        )
        return "context.recommendation_generated", recommendation.model_dump(mode="json")
    if command.command_type == "mission.create":
        record = authorized.mission_record
        if record is None:
            raise MishkanError(ErrorCode.OUTPUT_CONTRACT, "authorized mission record is absent")
        created = mission_repository.create_mission(record)
        return "mission.created", created.model_dump(mode="json")
    if command.command_type == "mission.brief.record":
        brief = authorized.mission_brief
        if brief is None or command.expected_revision is None:
            raise MishkanError(
                ErrorCode.OUTPUT_CONTRACT,
                "Mission Brief command requires a record and expected mission revision",
            )
        recorded_brief = mission_repository.record_brief(
            brief,
            expected_revision=command.expected_revision,
        )
        return "mission.brief_recorded", recorded_brief.model_dump(mode="json")
    if command.command_type == "mission.crew.record":
        crew = authorized.mission_crew
        if crew is None or command.expected_revision is None:
            raise MishkanError(
                ErrorCode.OUTPUT_CONTRACT,
                "Mission Crew command requires a revision and expected mission revision",
            )
        recorded_crew = mission_repository.record_crew(
            crew,
            expected_revision=command.expected_revision,
        )
        return "mission.crew_recorded", recorded_crew.model_dump(mode="json")
    if command.command_type == "conversation.create":
        channel = authorized.conversation_channel
        if channel is None:
            raise MishkanError(
                ErrorCode.OUTPUT_CONTRACT, "authorized conversation channel is absent"
            )
        created_channel = conversation_repository.create_channel(channel)
        return "conversation.created", created_channel.model_dump(mode="json")
    if command.command_type == "conversation.message.post":
        message = authorized.conversation_message
        if message is None:
            raise MishkanError(
                ErrorCode.OUTPUT_CONTRACT, "authorized conversation message is absent"
            )
        posted = conversation_repository.post_message(message)
        return "conversation.message_posted", posted.model_dump(mode="json")
    if command.command_type == "mission.decision.record":
        mission_decision_record = authorized.mission_decision
        if mission_decision_record is None:
            raise MishkanError(ErrorCode.OUTPUT_CONTRACT, "authorized mission decision is absent")
        recorded_decision = conversation_repository.record_decision(mission_decision_record)
        return "mission.decision_recorded", recorded_decision.model_dump(mode="json")
    if command.command_type == "mission.escalation.open":
        escalation = authorized.mission_escalation
        if escalation is None:
            raise MishkanError(ErrorCode.OUTPUT_CONTRACT, "authorized mission escalation is absent")
        opened = conversation_repository.create_escalation(escalation)
        return "mission.escalation_opened", opened.model_dump(mode="json")
    if command.command_type == "mission.intervention.apply":
        intervention = authorized.mission_intervention
        if intervention is None or command.expected_revision is None:
            raise MishkanError(
                ErrorCode.OUTPUT_CONTRACT,
                "mission intervention requires an authorized record and expected revision",
            )
        applied = conversation_repository.apply_intervention(
            intervention, expected_revision=command.expected_revision
        )
        return "mission.intervention_applied", applied.model_dump(mode="json")
    if command.command_type == "mission.assignment.record":
        assignment = authorized.mission_assignment
        if assignment is None:
            raise MishkanError(ErrorCode.OUTPUT_CONTRACT, "authorized mission assignment is absent")
        recorded_assignment = mission_repository.record_assignment(assignment)
        return "mission.task_assigned", recorded_assignment.model_dump(mode="json")
    if command.command_type == "mission.run-binding.record":
        run_binding = authorized.mission_run_binding
        if run_binding is None:
            raise MishkanError(
                ErrorCode.OUTPUT_CONTRACT,
                "authorized mission run binding is absent",
            )
        recorded_run_binding: MissionRunBinding = mission_repository.record_run_binding(run_binding)
        return "mission.run_bound", recorded_run_binding.model_dump(mode="json")
    if command.command_type == "mission.run-report.record":
        run_report = authorized.mission_run_report
        if run_report is None:
            raise MishkanError(
                ErrorCode.OUTPUT_CONTRACT,
                "authorized mission run report is absent",
            )
        recorded_report = mission_repository.record_run_report(run_report)
        return "mission.run_reported", recorded_report.model_dump(mode="json")
    if command.command_type == "mission.task.claim":
        claim_request = authorized.mission_task_claim
        if claim_request is None:
            raise MishkanError(
                ErrorCode.OUTPUT_CONTRACT,
                "authorized mission task claim is absent",
            )
        claim = mission_task_claims.claim(claim_request)
        return "mission.task_claimed", claim.model_dump(mode="json")
    if command.command_type == "mission.transition":
        transition = authorized.mission_transition
        if transition is None or command.expected_revision is None:
            raise MishkanError(
                ErrorCode.OUTPUT_CONTRACT,
                "mission transition requires an authorized record and expected revision",
            )
        if transition.to_state is MissionState.COMPLETED:
            mission_task_claims.require_completion_ready(str(transition.mission_id))
        recorded_transition = mission_repository.transition(
            transition, expected_revision=command.expected_revision
        )
        return "mission.state_transitioned", recorded_transition.model_dump(mode="json")
    if command.command_type == "mission.governance.propose":
        governance_request = authorized.mission_governance_request
        if governance_request is None:
            raise MishkanError(
                ErrorCode.OUTPUT_CONTRACT, "authorized mission governance request is absent"
            )
        mission = mission_repository.mission(str(governance_request.mission_id))
        if mission.revision != governance_request.mission_revision:
            raise MishkanError(
                ErrorCode.REVISION_MISMATCH,
                "mission changed after the governance request was authored",
                details={
                    "expected": mission.revision,
                    "received": governance_request.mission_revision,
                },
            )
        proposal: MissionGovernanceResult = mission_governance.propose(
            mission, governance_request.evidence
        )
        if proposal.mission != mission:
            raise MishkanError(
                ErrorCode.OUTPUT_CONTRACT,
                "CrewAI governance proposal returned a different mission snapshot",
            )
        return "mission.governance_proposed", proposal.model_dump(mode="json")
    if command.command_type == "mission.environment.propose":
        planning_request = authorized.mission_environment_planning_request
        if planning_request is None or environment_repository is None:
            raise MishkanError(
                ErrorCode.REQUIRED_DEPENDENCY,
                "mission environment planning is not configured",
            )
        mission = mission_repository.mission(str(planning_request.mission_id))
        brief = mission_repository.brief(str(mission.mission_id))
        crew = mission_repository.crew(str(mission.mission_id))
        assignments = mission_repository.assignments(str(mission.mission_id))
        observations = tuple(
            environment_repository.observation(str(context.observation_id))
            for context in planning_request.contexts
        )
        MissionEnvironmentPlanValidator.validate_request(
            planning_request,
            mission=mission,
            brief=brief,
            crew=crew,
            assignments=assignments,
            observations=observations,
            profile=environment_profile,
        )
        proposed_environment_plan = mission_environment_planning.propose(
            planning_request,
            mission=mission,
            brief=brief,
            crew=crew,
            observations=observations,
            plan_version=(mission.current_environment_plan_version or 0) + 1,
        )
        if (
            proposed_environment_plan.mission_id != mission.mission_id
            or proposed_environment_plan.source_request_id != planning_request.request_id
            or proposed_environment_plan.owner_identity != planning_request.owner_identity
            or proposed_environment_plan.contexts != planning_request.contexts
        ):
            raise MishkanError(
                ErrorCode.OUTPUT_CONTRACT,
                "CrewAI environment proposal changed its authoritative planning input",
            )
        MissionEnvironmentPlanValidator.validate_plan(
            proposed_environment_plan,
            mission=mission,
            brief=brief,
            crew=crew,
            assignments=assignments,
            observations=observations,
            profile=environment_profile,
        )
        return (
            "mission.environment_plan_proposed",
            proposed_environment_plan.model_dump(mode="json"),
        )
    if command.command_type == "mission.environment.accept":
        accepted_environment_plan = authorized.mission_environment_plan
        if (
            accepted_environment_plan is None
            or command.expected_revision is None
            or environment_repository is None
        ):
            raise MishkanError(
                ErrorCode.REQUIRED_DEPENDENCY,
                "mission environment plan acceptance is not configured",
            )
        mission = mission_repository.mission(str(accepted_environment_plan.mission_id))
        brief = mission_repository.brief(str(accepted_environment_plan.mission_id))
        crew = mission_repository.crew(str(accepted_environment_plan.mission_id))
        assignments = mission_repository.assignments(str(accepted_environment_plan.mission_id))
        observations = tuple(
            environment_repository.observation(str(context.observation_id))
            for context in accepted_environment_plan.contexts
        )
        MissionEnvironmentPlanValidator.validate_plan(
            accepted_environment_plan,
            mission=mission,
            brief=brief,
            crew=crew,
            assignments=assignments,
            observations=observations,
            profile=environment_profile,
        )
        consequential_ids = {
            decision.consequential_decision_id
            for decision in accepted_environment_plan.decisions
            if decision.consequential_decision_id is not None
        }
        MissionEnvironmentPlanValidator.validate_consequential_decisions(
            accepted_environment_plan,
            {
                decision_id: conversation_repository.decision(str(decision_id))
                for decision_id in consequential_ids
            },
        )
        if not repository.has_accepted_result_for_target(
            command_type="mission.environment.propose",
            target_type="mission_environment_planning_request",
            target_id=str(accepted_environment_plan.source_request_id),
            result_payload=accepted_environment_plan.model_dump(mode="json"),
        ):
            raise MishkanError(
                ErrorCode.PLAN,
                "environment plan was not produced by its accepted CrewAI proposal command",
            )
        acceptance = MissionEnvironmentPlanAcceptance(
            plan=accepted_environment_plan,
            accepted_by=command.actor_id,
            policy_fingerprint=authorized.decision.policy_fingerprint,
        )
        recorded_environment_acceptance = mission_repository.accept_environment_plan(
            acceptance,
            expected_revision=command.expected_revision,
        )
        return (
            "mission.environment_plan_accepted",
            recorded_environment_acceptance.model_dump(mode="json"),
        )
    if command.command_type == "mission.environment.resolve":
        if (
            command.target_id is None
            or environment_repository is None
            or environment_resolver is None
        ):
            raise MishkanError(
                ErrorCode.REQUIRED_DEPENDENCY,
                "mission environment resolution is not configured",
            )
        acceptance = mission_repository.environment_plan_by_id(command.target_id)
        mission = mission_repository.mission(str(acceptance.plan.mission_id))
        if mission.current_environment_plan_version != acceptance.plan.version:
            raise MishkanError(
                ErrorCode.REVISION_MISMATCH,
                "only the current accepted mission environment plan can be resolved",
            )
        context_id = str(payload["context_id"])
        mission_environment_binding_request = acceptance.plan.binding_request(
            context_id,
            policy_fingerprint=authorized.decision.policy_fingerprint,
        )
        observation = environment_repository.observation(
            str(mission_environment_binding_request.observation_id)
        )
        binding = environment_resolver.resolve(
            mission_environment_binding_request,
            observation,
        )
        recorded_binding = environment_repository.record_binding(binding)
        return (
            f"mission.environment_binding_{recorded_binding.state.value}",
            recorded_binding.model_dump(mode="json"),
        )
    if command.command_type == "organization.evidence.record":
        professional_evidence = authorized.professional_evidence
        if professional_evidence is None:
            raise MishkanError(
                ErrorCode.OUTPUT_CONTRACT,
                "authorized professional evidence record is absent",
            )
        recorded_professional_evidence = professional_evolution.record_evidence(
            professional_evidence
        )
        return (
            "organization.professional_evidence_recorded",
            recorded_professional_evidence.model_dump(mode="json"),
        )
    if command.command_type == "organization.promotion.decide":
        promotion_request = authorized.professional_promotion_request
        promotion_disposition = authorized.professional_promotion_disposition
        if promotion_request is None or promotion_disposition is None:
            raise MishkanError(
                ErrorCode.OUTPUT_CONTRACT,
                "authorized professional promotion decision is incomplete",
            )
        promotion_decision: ProfessionalPromotionDecision = professional_evolution.decide_promotion(
            promotion_request,
            disposition=promotion_disposition,
            decided_by=command.actor_id,
            policy_fingerprint=authorized.decision.policy_fingerprint,
            reason=str(payload["reason"]),
        )
        return (
            f"organization.professional_promotion_{promotion_decision.disposition.value}",
            promotion_decision.model_dump(mode="json"),
        )
    if command.command_type == "artifact.upload.open":
        upload = artifacts.open_upload(
            expected_size=int(payload["expected_size"]),
            expected_digest=str(payload["expected_digest"]),
            media_type=str(payload["media_type"]),
            provenance=ArtifactProvenance.model_validate(payload["provenance"]),
            sensitivity=str(payload.get("sensitivity", "internal")),
            retention=str(payload.get("retention", "run")),
        )
        return "artifact.upload_opened", upload.model_dump(mode="json")
    if command.command_type == "artifact.upload.chunk" and command.target_id is not None:
        try:
            content = base64.b64decode(str(payload["content_base64"]), validate=True)
        except (ValueError, TypeError) as exc:
            raise MishkanError(
                ErrorCode.OUTPUT_CONTRACT, "artifact chunk is not valid base64"
            ) from exc
        upload = artifacts.append_chunk(
            UUID(command.target_id), offset=int(payload["offset"]), content=content
        )
        return "artifact.chunk_appended", upload.model_dump(mode="json")
    if command.command_type == "artifact.upload.commit" and command.target_id is not None:
        manifest = artifacts.commit_upload(UUID(command.target_id))
        return "artifact.available", manifest.model_dump(mode="json")
    if command.command_type == "artifact.upload.abort" and command.target_id is not None:
        upload = artifacts.abort_upload(UUID(command.target_id))
        return "artifact.upload_aborted", upload.model_dump(mode="json")
    if command.command_type == "artifact.reference.update":
        reference = artifacts.update_reference(
            str(payload["scope"]),
            str(payload["name"]),
            str(payload["artifact_reference"]),
            expected_revision=int(payload["expected_reference_revision"]),
        )
        return "artifact.reference_updated", reference.model_dump(mode="json")
    if command.command_type == "artifact.collection.create":
        entries = payload["entries"]
        if not isinstance(entries, dict) or not all(
            isinstance(key, str) and isinstance(value, str) for key, value in entries.items()
        ):
            raise MishkanError(
                ErrorCode.OUTPUT_CONTRACT,
                "artifact collection entries must map logical paths to artifact references",
            )
        normalized_entries = {str(key): str(value) for key, value in entries.items()}
        collection = artifacts.create_collection(normalized_entries)
        return "artifact.collection_created", collection.model_dump(mode="json")
    if command.command_type == "artifact.hold.set" and command.target_id is not None:
        artifact_hold = artifacts.hold(f"artifact:{command.target_id}", str(payload["reason"]))
        return "artifact.hold_set", artifact_hold.model_dump(mode="json")
    if command.command_type == "artifact.hold.release" and command.target_id is not None:
        released_artifact_hold = artifacts.release_hold(f"artifact:{command.target_id}")
        return "artifact.hold_released", released_artifact_hold.model_dump(mode="json")
    if command.command_type == "artifact.pin.set" and command.target_id is not None:
        pin = artifacts.pin(f"artifact:{command.target_id}")
        return "artifact.pin_set", pin.model_dump(mode="json")
    if command.command_type == "artifact.pin.release" and command.target_id is not None:
        pin = artifacts.release_pin(f"artifact:{command.target_id}")
        return "artifact.pin_released", pin.model_dump(mode="json")
    if command.command_type == "artifact.gc.plan":
        plan = artifacts.plan_gc(watermark=datetime.fromisoformat(str(payload["watermark"])))
        return "artifact.gc_planned", plan.model_dump(mode="json")
    if command.command_type == "artifact.gc.apply" and command.target_id is not None:
        plan = artifacts.apply_gc(UUID(command.target_id))
        return "artifact.gc_applied", plan.model_dump(mode="json")
    if command.command_type == "artifact.reconcile.plan":
        reconciliation = artifacts.plan_reconciliation()
        return "artifact.reconciliation_planned", reconciliation.model_dump(mode="json")
    if command.command_type == "artifact.reconcile.apply" and command.target_id is not None:
        reconciliation = artifacts.apply_reconciliation(UUID(command.target_id))
        return "artifact.reconciliation_applied", reconciliation.model_dump(mode="json")
    if command.command_type == "event.hold.create":
        event_hold = repository.create_event_hold(
            scope=EventHoldScope(str(payload["scope"])),
            scope_id=(str(payload["scope_id"]) if payload.get("scope_id") is not None else None),
            reason=str(payload["reason"]),
            actor_id=command.actor_id,
        )
        return "event.hold_created", event_hold.model_dump(mode="json")
    if command.command_type == "event.hold.release" and command.target_id is not None:
        released_event_hold = repository.release_event_hold(UUID(command.target_id))
        return "event.hold_released", released_event_hold.model_dump(mode="json")
    if command.command_type == "event.retention.plan":
        event_plan = repository.plan_event_retention(event_retention_policy)
        return "event.retention_planned", event_plan.model_dump(mode="json")
    if command.command_type == "event.retention.apply" and command.target_id is not None:
        applied_event_plan = repository.apply_event_retention(UUID(command.target_id))
        return "event.retention_applied", applied_event_plan.model_dump(mode="json")
    if command.command_type == "change.plan":
        change_result = changes.plan(ChangeSet.model_validate(payload["change_set"]))
        return "change_set.planned", change_result.model_dump(mode="json")
    if command.command_type == "change.apply" and command.target_id is not None:
        change_result = changes.apply(UUID(command.target_id))
        return "change_set.settled", change_result.model_dump(mode="json")
    if command.command_type == "change.reconcile" and command.target_id is not None:
        change_result = changes.reconcile(UUID(command.target_id))
        return "change_set.reconciled", change_result.model_dump(mode="json")
    if command.command_type.startswith("git."):
        git_request = authorized.git_request
        if git_request is None:
            raise MishkanError(ErrorCode.OUTPUT_CONTRACT, "authorized Git request is absent")
        git_result = git_effects.execute(
            git_request,
            authorization=authorized.request,
            policy=effective_policy,
            credential_value=(
                resolved_credentials.get(git_request.credential_reference)
                if git_request.credential_reference is not None
                else None
            ),
        )
        return f"git.{git_request.mode.value}_settled", git_result.model_dump(mode="json")
    if command.command_type == "session.start":
        session_request = authorized.session_request
        if session_request is None:
            raise MishkanError(ErrorCode.OUTPUT_CONTRACT, "authorized session request is absent")
        effective_request = session_request.model_copy(
            update={"policy_fingerprint": authorized.decision.policy_fingerprint}
        )
        session_record = supervisor.start(effective_request, credential_values=resolved_credentials)
        return "session.started", session_record.model_dump(mode="json")
    if command.command_type == "session.write" and command.target_id is not None:
        content = base64.b64decode(str(payload["content_base64"]), validate=True)
        written = supervisor.write(
            UUID(command.target_id),
            content,
            declared_effects=tuple(str(value) for value in payload["declared_effects"]),
            network_destinations=tuple(str(value) for value in payload["network_destinations"]),
        )
        return "session.input_written", {"written": written}
    if command.command_type == "session.resize" and command.target_id is not None:
        supervisor.resize(
            UUID(command.target_id), rows=int(payload["rows"]), columns=int(payload["columns"])
        )
        return "session.resized", {"rows": int(payload["rows"]), "columns": int(payload["columns"])}
    if command.command_type == "session.signal" and command.target_id is not None:
        session_record = supervisor.signal(UUID(command.target_id), str(payload["signal"]))
        return "session.signalled", session_record.model_dump(mode="json")
    if command.command_type == "session.cancel" and command.target_id is not None:
        session_record = supervisor.cancel(UUID(command.target_id))
        return "session.cancelled", session_record.model_dump(mode="json")
    if command.command_type == "session.settle" and command.target_id is not None:
        session_record = supervisor.settle(UUID(command.target_id))
        return "session.settled", session_record.model_dump(mode="json")
    if command.command_type == "run.cancel" and command.target_id is not None:
        snapshot = runs.cancel_run(command.target_id)
        return "run.cancellation_requested", {"run_id": snapshot.run_id}
    if command.command_type == "run.recover" and command.target_id is not None:
        released = runs.recover_interrupted(command.target_id)
        return "run.recovered", {"run_id": command.target_id, "released_tasks": released}
    if command.command_type == "mcp.connection.connect" and command.target_id is not None:
        if command.payload:
            raise MishkanError(
                ErrorCode.OUTPUT_CONTRACT,
                "MCP connection command accepts no credential values or payload",
            )
        if mcp_runner is None or mcp_config is None:
            raise MishkanError(ErrorCode.MCP, "MCP mediation is not configured")
        configured = mcp_config.connections.get(command.target_id)
        if configured is None or not configured.enabled:
            raise MishkanError(ErrorCode.MCP, "MCP connection is not enabled")
        mcp_record = mcp_runner.connect(
            command.target_id,
            principal=command.actor_id,
            policy_fingerprint=authorized.decision.policy_fingerprint,
            credentials=resolved_credentials,
        )
        return "mcp.connection_ready", mcp_record.model_dump(mode="json")
    if command.command_type == "mcp.call.cancel" and command.target_id is not None:
        if command.payload:
            raise MishkanError(ErrorCode.OUTPUT_CONTRACT, "MCP cancellation accepts no payload")
        if mcp_runner is None:
            raise MishkanError(ErrorCode.MCP, "MCP mediation is not configured")
        request_id = UUID(command.target_id)
        if mcp_runner.cancel(request_id):
            return "mcp.call_cancellation_requested", {"request_id": command.target_id}
        if mcp_config is None:
            raise MishkanError(ErrorCode.MCP, "MCP mediation is not configured")
        mcp_result = mcp_runner.cancel_remote_task(request_id, credentials=resolved_credentials)
        return "mcp.call_cancelled", mcp_result.model_dump(mode="json")
    if command.command_type == "mcp.call.reconcile" and command.target_id is not None:
        if command.payload:
            raise MishkanError(ErrorCode.OUTPUT_CONTRACT, "MCP reconciliation accepts no payload")
        if mcp_runner is None or mcp_config is None:
            raise MishkanError(ErrorCode.MCP, "MCP mediation is not configured")
        request_id = UUID(command.target_id)
        mcp_result = mcp_runner.resume_remote_task(request_id, credentials=resolved_credentials)
        return "mcp.call_reconciled", mcp_result.model_dump(mode="json")
    if command.command_type == "skill.version.register":
        version = authorized.skill_version
        if skill_lifecycle is None or skill_inspector is None or version is None:
            raise MishkanError(ErrorCode.REQUIRED_DEPENDENCY, "Skills lifecycle is not configured")
        collection = artifacts.collection(version.package_collection_id)
        total = 0
        skill_entries: dict[str, bytes] = {}
        for logical_path, artifact_reference in collection.entries.items():
            manifest = artifacts.manifest(artifact_reference)
            total += manifest.size_bytes
            if (
                manifest.size_bytes > skill_inspector.profile.max_scanned_file_bytes
                or total > skill_inspector.profile.max_total_bytes
            ):
                raise MishkanError(
                    ErrorCode.SKILL_TRUST,
                    "skill artifact collection exceeds configured inspection bounds",
                )
            skill_entries[logical_path] = artifacts.read_bytes(artifact_reference)
        if "SKILL.md" not in skill_entries:
            raise MishkanError(ErrorCode.SKILL_CONTRACT, "skill package has no SKILL.md")
        if version.metadata.activation.value != "candidate":
            raise MishkanError(
                ErrorCode.SKILL_TRUST,
                "new skill metadata must enter as a candidate",
            )
        resources = tuple(sorted(path for path in skill_entries if path != "SKILL.md"))
        if version.metadata.resource_paths != resources:
            raise MishkanError(
                ErrorCode.SKILL_TRUST,
                "skill metadata resource paths differ from its immutable collection",
            )
        validate_skill_metadata_document(version.metadata, skill_entries["SKILL.md"])
        effective_version = version.model_copy(
            update={"policy_fingerprint": authorized.decision.policy_fingerprint}
        )
        registered = skill_lifecycle.register_candidate(effective_version)
        inspection = skill_inspector.inspect_entries(
            skill_entries,
            expected_fingerprint=registered.provenance.package_fingerprint,
        )
        inspected = skill_lifecycle.record_inspection(
            str(registered.id),
            inspection,
            expected_revision=registered.revision,
        )
        return "skill.version_inspected", inspected.model_dump(mode="json")
    if command.command_type == "skill.version.decide":
        decision = authorized.skill_decision
        if skill_lifecycle is None or decision is None:
            raise MishkanError(ErrorCode.REQUIRED_DEPENDENCY, "Skills lifecycle is not configured")
        effective_decision = decision.model_copy(
            update={"policy_fingerprint": authorized.decision.policy_fingerprint}
        )
        decided = skill_lifecycle.decide(effective_decision)
        return f"skill.version_{decided.state.value}", decided.model_dump(mode="json")
    if command.command_type in {"skill.version.archive", "skill.version.delete"}:
        decision = authorized.skill_decision
        if skill_lifecycle is None or decision is None or command.target_id is None:
            raise MishkanError(ErrorCode.REQUIRED_DEPENDENCY, "Skills lifecycle is not configured")
        effective_decision = decision.model_copy(
            update={"policy_fingerprint": authorized.decision.policy_fingerprint}
        )
        archived = skill_lifecycle.archive(
            command.target_id,
            effective_decision,
            expected_revision=int(payload["expected_revision"]),
        )
        return (
            "skill.version_archived"
            if command.command_type.endswith(".archive")
            else "skill.version_deleted",
            archived.model_dump(mode="json"),
        )
    if command.command_type in {"skill.version.restore", "skill.version.reset"}:
        decision = authorized.skill_decision
        if skill_lifecycle is None or decision is None or command.target_id is None:
            raise MishkanError(ErrorCode.REQUIRED_DEPENDENCY, "Skills lifecycle is not configured")
        effective_decision = decision.model_copy(
            update={"policy_fingerprint": authorized.decision.policy_fingerprint}
        )
        operation = command.command_type.rsplit(".", 1)[-1]
        restored = skill_lifecycle.reactivate(
            command.target_id,
            effective_decision,
            expected_revision=int(payload["expected_revision"]),
            operation=operation,
        )
        event_type = "skill.version_restored" if operation == "restore" else "skill.version_reset"
        return event_type, restored.model_dump(mode="json")
    if command.command_type in {"skill.version.pin", "skill.version.unpin"}:
        if skill_lifecycle is None or command.target_id is None:
            raise MishkanError(ErrorCode.REQUIRED_DEPENDENCY, "Skills lifecycle is not configured")
        pinned = command.command_type.endswith(".pin")
        version = skill_lifecycle.set_pin(
            command.target_id,
            pinned=pinned,
            expected_revision=int(payload["expected_revision"]),
        )
        return (
            "skill.version_pinned" if pinned else "skill.version_unpinned",
            version.model_dump(mode="json"),
        )
    if command.command_type == "skill.usage.record":
        usage = authorized.skill_usage
        if skill_usage is None or usage is None:
            raise MishkanError(ErrorCode.REQUIRED_DEPENDENCY, "Skills usage is not configured")
        effective_usage = usage.model_copy(
            update={"policy_fingerprint": authorized.decision.policy_fingerprint}
        )
        recorded = skill_usage.record(effective_usage)
        return "skill.usage_recorded", recorded.model_dump(mode="json")
    if command.command_type == "skill.invoke":
        request = authorized.skill_invocation
        if skill_invocation_service is None or request is None:
            raise MishkanError(ErrorCode.REQUIRED_DEPENDENCY, "Skills invocation is not configured")
        evidence = skill_invocation_service.invoke(
            request,
            policy_fingerprint=authorized.decision.policy_fingerprint,
        )
        return "skill.invocation_resolved", evidence.model_dump(mode="json")
    if command.command_type == "skill.learn":
        learning_request = authorized.skill_learning
        if skill_learning_service is None or learning_request is None:
            raise MishkanError(ErrorCode.REQUIRED_DEPENDENCY, "Skill learning is not configured")
        learning_record = skill_learning_service.learn(
            learning_request,
            policy_fingerprint=authorized.decision.policy_fingerprint,
        )
        return (
            f"skill.learning_{learning_record.state.value}",
            learning_record.model_dump(mode="json"),
        )
    if command.command_type == "environment.observe":
        observation_request = authorized.environment_observation
        if (
            environment_repository is None
            or environment_observer is None
            or observation_request is None
        ):
            raise MishkanError(
                ErrorCode.REQUIRED_DEPENDENCY,
                "Environment observation is not configured",
            )
        observation = environment_observer.observe(
            Path(authorized.request.repository),
            request=observation_request,
        )
        recorded_observation = environment_repository.record_observation(observation)
        return "environment.observed", recorded_observation.model_dump(mode="json")
    if command.command_type == "environment.resolve":
        binding_request = authorized.environment_binding
        if (
            environment_repository is None
            or environment_resolver is None
            or binding_request is None
        ):
            raise MishkanError(
                ErrorCode.REQUIRED_DEPENDENCY,
                "Environment resolution is not configured",
            )
        effective_binding_request = binding_request.model_copy(
            update={"policy_fingerprint": authorized.decision.policy_fingerprint}
        )
        observation = environment_repository.observation(
            str(effective_binding_request.observation_id)
        )
        binding = environment_resolver.resolve(effective_binding_request, observation)
        recorded_binding = environment_repository.record_binding(binding)
        return (
            f"environment.binding_{binding.state.value}",
            recorded_binding.model_dump(mode="json"),
        )
    if command.command_type == "environment.descriptor.validate":
        descriptor_set = authorized.environment_descriptor_set
        if (
            environment_repository is None
            or environment_descriptor_validator is None
            or descriptor_set is None
        ):
            raise MishkanError(
                ErrorCode.REQUIRED_DEPENDENCY,
                "Environment descriptor validation is not configured",
            )
        binding = environment_repository.binding(str(descriptor_set.binding_id))
        if binding.request.owner_identity != command.actor_id:
            raise MishkanError(
                ErrorCode.AUTHORITY_NOT_GRANTED,
                "environment descriptor owner must match the authenticated actor",
            )
        validation: DescriptorValidationResult = environment_descriptor_validator.validate(
            descriptor_set
        )
        if validation.valid:
            environment_repository.record_descriptor_set(descriptor_set)
        return (
            "environment.descriptor_validated"
            if validation.valid
            else "environment.descriptor_rejected",
            validation.model_dump(mode="json"),
        )
    if command.command_type == "environment.descriptor.change.plan":
        change_request = authorized.environment_descriptor_change
        if environment_descriptor_change_planner is None or change_request is None:
            raise MishkanError(
                ErrorCode.REQUIRED_DEPENDENCY,
                "Environment descriptor change planning is not configured",
            )
        descriptor_change: EnvironmentDescriptorChangePlan = (
            environment_descriptor_change_planner.plan(change_request)
        )
        return "environment.descriptor_change_planned", descriptor_change.model_dump(mode="json")
    if command.command_type == "environment.operation.plan":
        operation_request = authorized.environment_operation
        if environment_operation_planner is None or operation_request is None:
            raise MishkanError(
                ErrorCode.REQUIRED_DEPENDENCY,
                "Environment operation planning is not configured",
            )
        operation_plan: EnvironmentOperationPlan = environment_operation_planner.plan(
            operation_request
        )
        return "environment.operation_planned", operation_plan.model_dump(mode="json")
    if command.command_type == "environment.command.plan":
        command_request = authorized.engineering_command
        if (
            environment_repository is None
            or technical_pack_service is None
            or command_request is None
        ):
            raise MishkanError(
                ErrorCode.REQUIRED_DEPENDENCY,
                "Engineering command planning is not configured",
            )
        observation = environment_repository.observation(str(command_request.observation_id))
        command_plan: EngineeringCommandPlan = technical_pack_service.plan(
            observation,
            command_request,
        )
        return "environment.command_planned", command_plan.model_dump(mode="json")
    if command.command_type == "environment.attempt.settle":
        attempt_operation_plan = authorized.environment_operation_plan
        if attempt_operation_plan is None or environment_evidence_service is None:
            raise MishkanError(
                ErrorCode.REQUIRED_DEPENDENCY,
                "Environment attempt settlement is not configured",
            )
        session = supervisor.status(UUID(str(payload["session_id"])))
        attempt = environment_evidence_service.settle_attempt(attempt_operation_plan, session)
        return f"environment.attempt_{attempt.settlement.value}", attempt.model_dump(mode="json")
    if command.command_type == "environment.verification.record":
        verification_request = authorized.environment_verification
        if verification_request is None or environment_evidence_service is None:
            raise MishkanError(
                ErrorCode.REQUIRED_DEPENDENCY,
                "Environment verification is not configured",
            )
        verification = environment_evidence_service.verify(verification_request)
        return (
            f"environment.verification_{verification.settlement.value}",
            verification.model_dump(mode="json"),
        )
    if command.command_type == "environment.binding.invalidate":
        invalidation = authorized.environment_invalidation
        if invalidation is None or environment_repository is None:
            raise MishkanError(
                ErrorCode.REQUIRED_DEPENDENCY,
                "Environment invalidation is not configured",
            )
        effective_invalidation: EnvironmentInvalidation = invalidation.model_copy(
            update={"policy_fingerprint": authorized.decision.policy_fingerprint}
        )
        recorded_invalidation = environment_repository.invalidate(effective_invalidation)
        return "environment.binding_invalidated", recorded_invalidation.model_dump(mode="json")
    raise MishkanError(
        ErrorCode.OUTPUT_CONTRACT,
        "application command type has no registered handler",
        details={"command_type": command.command_type},
    )


def _session_credential_references(request: ExecutionRequest) -> tuple[CredentialReference, ...]:
    references = (
        tuple(
            value
            for value in request.credential_environment.values()
            if isinstance(value, CredentialReference)
        )
        + request.credential_references
    )
    by_locator: dict[str, CredentialReference] = {}
    for reference in references:
        current = by_locator.get(reference.locator)
        if current is not None and current != reference:
            raise MishkanError(
                ErrorCode.CONFIGURATION,
                "session credential locator maps to conflicting credential sources",
            )
        by_locator[reference.locator] = reference
    return tuple(by_locator[key] for key in sorted(by_locator))


def _resolve_command_credentials(
    authorized: AuthorizedApplicationCommand,
    resolver: CredentialPoolResolver,
    mcp_runner: McpServiceRunner | None,
    mcp_config: McpConfig | None,
    config: MishkanConfig,
) -> dict[str, str]:
    command = authorized.command
    references: tuple[CredentialReference, ...] = ()
    if authorized.session_request is not None:
        references = _session_credential_references(authorized.session_request)
    elif authorized.git_request is not None:
        binding_id = authorized.git_request.credential_reference
        if binding_id is None:
            return {}
        reference = config.credential_bindings.get(binding_id)
        if reference is None:
            raise MishkanError(ErrorCode.AUTHORIZATION_MISSING, "Git credential is not configured")
        resolved = resolver.resolve_exact((reference,))
        return {binding_id: resolved[reference.locator]}
    elif command.command_type == "mcp.connection.connect" and command.target_id is not None:
        if mcp_config is None:
            raise MishkanError(ErrorCode.MCP, "MCP mediation is not configured")
        references = mcp_config.connections[command.target_id].credential_refs
    elif command.command_type in {"mcp.call.cancel", "mcp.call.reconcile"}:
        if mcp_config is None or mcp_runner is None or command.target_id is None:
            raise MishkanError(ErrorCode.MCP, "MCP mediation is not configured")
        connection_id = mcp_runner.call_connection_id(UUID(command.target_id))
        references = mcp_config.connections[connection_id].credential_refs
    elif command.command_type.startswith("knowledge."):
        if command.command_type == "knowledge.query":
            # Query sources are optional and individually degraded by KnowledgeService.
            # Resolving all candidate credentials here would turn one absent optional
            # credential into a command-wide failure before compatible fallback.
            return {}
        configured: dict[str, CredentialReference] = {}
        if config.knowledge is not None:
            for source in config.knowledge.sources.values():
                configured.update({item.locator: item for item in source.credential_refs})
        if mcp_config is not None:
            for connection in mcp_config.connections.values():
                configured.update({item.locator: item for item in connection.credential_refs})
        locators = authorized.request.credentials
        try:
            references = tuple(configured[item] for item in locators)
        except KeyError as exc:
            raise MishkanError(
                ErrorCode.AUTHORIZATION_MISSING,
                "knowledge credential scope is not configured",
            ) from exc
    return resolver.resolve_exact(references)
