"""Inbound harness facade over the same daemon command and query authority."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, Protocol, TypeVar

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from mishkan.application import ApplicationCommand, CommandResult
from mishkan.config.models import SUPPORTED_MCP_FACADE_OPERATIONS, KnowledgeConfig, McpConfig
from mishkan.context import ContextualRecommendationService
from mishkan.conversations import SQLiteConversationRepository
from mishkan.domain.errors import ErrorCode, MishkanError
from mishkan.events import EventPage
from mishkan.knowledge import KnowledgeQuery
from mishkan.knowledge.repository import SQLiteKnowledgeRepository
from mishkan.missions import (
    MissionEnvironmentReadinessService,
    MissionTaskClaimService,
    MissionTemplateService,
    SQLiteMissionRepository,
)
from mishkan.missions.inspection import MissionInspectionService
from mishkan.notifications import NotificationDelivery, NotificationService, NotificationSeverity
from mishkan.organization import (
    OrganizationRosterDefinition,
    ProfessionalEvidenceKind,
)
from mishkan.organization.evolution_repository import SQLiteProfessionalEvolutionRepository
from mishkan.persistence import SQLiteApplicationRepository

CommandExecutor = Callable[[ApplicationCommand, str], Awaitable[CommandResult]]
DependencyT = TypeVar("DependencyT")


class McpFacadePort(Protocol):
    operations: tuple[str, ...]
    resources: tuple[str, ...]

    async def invoke(
        self,
        operation: str,
        arguments: dict[str, Any],
        *,
        principal_id: str,
    ) -> dict[str, Any]: ...

    async def read_resource(self, uri: str, *, principal_id: str) -> dict[str, Any]: ...


class FacadeModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class EventQuery(FacadeModel):
    after: int = Field(default=0, ge=0)
    limit: int = Field(default=100, ge=1, le=1_000)
    event_types: tuple[str, ...] = ()
    entity_type: str | None = None
    entity_id: str | None = None


class RunQuery(FacadeModel):
    run_id: str = Field(min_length=1)


class LimitQuery(FacadeModel):
    limit: int = Field(default=100, ge=1, le=1_000)


class ProfessionalCompetenceQuery(FacadeModel):
    identity_id: str = Field(min_length=1, max_length=128)
    kind: ProfessionalEvidenceKind
    subject: str = Field(min_length=1, max_length=512)


class ProfessionalHistoryQuery(FacadeModel):
    identity_id: str = Field(min_length=1, max_length=128)
    kind: ProfessionalEvidenceKind | None = None
    subject: str | None = Field(default=None, min_length=1, max_length=512)
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=100, ge=1, le=1_000)


class MissionQuery(FacadeModel):
    mission_id: str = Field(min_length=1, max_length=128)
    limit: int = Field(default=100, ge=1, le=1_000)


class OrganizationBranchQuery(FacadeModel):
    branch_id: str = Field(pattern=r"^[a-z][a-z0-9-]{1,63}$")
    limit: int = Field(default=100, ge=1, le=1_000)


class MissionTemplateQuery(FacadeModel):
    signals: tuple[str, ...] = ()
    organization_version: str = Field(default="1", min_length=1, max_length=64)


class ConversationListQuery(FacadeModel):
    mission_id: str | None = Field(default=None, min_length=1, max_length=128)
    limit: int = Field(default=100, ge=1, le=1_000)


class ConversationQuery(FacadeModel):
    conversation_id: str = Field(min_length=1, max_length=128)
    limit: int = Field(default=100, ge=1, le=1_000)


class NotificationQuery(FacadeModel):
    after: int = Field(default=0, ge=0)
    limit: int = Field(default=100, ge=1, le=1_000)
    severities: tuple[NotificationSeverity, ...] = ()
    deliveries: tuple[NotificationDelivery, ...] = ()


class KnowledgeListQuery(FacadeModel):
    project_id: str | None = Field(default=None, min_length=1, max_length=256)
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=100, ge=1, le=1_000)


class McpFacadeRouter:
    """Expose only allowlisted operations that have an executable daemon handler."""

    _QUERY_OPERATIONS = SUPPORTED_MCP_FACADE_OPERATIONS - {"command.submit"}

    def __init__(
        self,
        config: McpConfig,
        repository: SQLiteApplicationRepository,
        command_executor: CommandExecutor,
        *,
        schema_revision: str,
        event_page_limit: int,
        organization: OrganizationRosterDefinition | None = None,
        missions: SQLiteMissionRepository | None = None,
        conversations: SQLiteConversationRepository | None = None,
        professional_evolution: SQLiteProfessionalEvolutionRepository | None = None,
        mission_templates: MissionTemplateService | None = None,
        advisory: ContextualRecommendationService | None = None,
        readiness: MissionEnvironmentReadinessService | None = None,
        mission_task_claims: MissionTaskClaimService | None = None,
        mission_inspections: MissionInspectionService | None = None,
        notifications: NotificationService | None = None,
        knowledge_config: KnowledgeConfig | None = None,
        knowledge: SQLiteKnowledgeRepository | None = None,
    ) -> None:
        profile = config.exposure_profiles[config.facade.exposure_profile]
        self.operations = profile.operations
        self.resources = profile.resources
        self._repository = repository
        self._execute = command_executor
        self._schema_revision = schema_revision
        self._event_page_limit = event_page_limit
        self._organization = organization
        self._missions = missions
        self._conversations = conversations
        self._professional_evolution = professional_evolution
        self._mission_templates = mission_templates
        self._advisory = advisory
        self._readiness = readiness
        self._mission_task_claims = mission_task_claims
        self._mission_inspections = mission_inspections
        self._notifications = notifications
        self._knowledge_config = knowledge_config
        self._knowledge = knowledge

    async def invoke(
        self,
        operation: str,
        arguments: dict[str, Any],
        *,
        principal_id: str,
    ) -> dict[str, Any]:
        if operation not in self.operations:
            raise MishkanError(
                ErrorCode.AUTHORITY_NOT_GRANTED,
                "harness operation is outside the active MCP exposure profile",
            )
        if operation == "system.health":
            self._require_empty(arguments)
            return {"status": "ready", "schema": self._schema_revision}
        if operation == "system.snapshot":
            self._require_empty(arguments)
            return self._repository.snapshot(limit=self._event_page_limit).model_dump(mode="json")
        if operation == "events.list":
            query = self._validate(EventQuery, arguments)
            return self._events(query).model_dump(mode="json")
        if operation == "run.get":
            query = self._validate(RunQuery, arguments)
            runs = self._repository.runs(offset=0, limit=1_000)
            found = next((item for item in runs if item.get("id") == query.run_id), None)
            if found is None:
                raise MishkanError(ErrorCode.MISSION, "requested run does not exist")
            return dict(found)
        if operation == "organization.get":
            self._require_empty(arguments)
            organization = self._require_dependency(self._organization, "organization")
            return organization.model_dump(mode="json")
        if operation == "organization.inspect":
            query = self._validate(LimitQuery, arguments)
            inspections = self._require_dependency(
                self._mission_inspections,
                "mission inspections",
            )
            return inspections.organization(limit=query.limit)
        if operation == "organization.branch.inspect":
            query = self._validate(OrganizationBranchQuery, arguments)
            inspections = self._require_dependency(
                self._mission_inspections,
                "mission inspections",
            )
            return inspections.branch(query.branch_id, limit=query.limit)
        if operation == "organization.competence.get":
            query = self._validate(ProfessionalCompetenceQuery, arguments)
            evolution = self._require_dependency(
                self._professional_evolution, "professional evolution"
            )
            return evolution.competence_state(
                query.identity_id,
                kind=query.kind,
                subject=query.subject,
            ).model_dump(mode="json")
        if operation == "organization.evidence.list":
            query = self._validate(ProfessionalHistoryQuery, arguments)
            evolution = self._require_dependency(
                self._professional_evolution, "professional evolution"
            )
            return {
                "evidence": [
                    item.model_dump(mode="json")
                    for item in evolution.evidence(
                        query.identity_id,
                        kind=query.kind,
                        subject=query.subject,
                        offset=query.offset,
                        limit=query.limit,
                    )
                ]
            }
        if operation == "organization.promotions.list":
            query = self._validate(ProfessionalHistoryQuery, arguments)
            evolution = self._require_dependency(
                self._professional_evolution, "professional evolution"
            )
            return {
                "promotions": [
                    item.model_dump(mode="json")
                    for item in evolution.promotion_history(
                        query.identity_id,
                        kind=query.kind,
                        subject=query.subject,
                        offset=query.offset,
                        limit=query.limit,
                    )
                ]
            }
        if operation == "mission.list":
            query = self._validate(LimitQuery, arguments)
            missions = self._require_dependency(self._missions, "missions")
            return {
                "missions": [
                    item.model_dump(mode="json")
                    for item in missions.list_missions(limit=query.limit)
                ]
            }
        if operation == "mission.get":
            query = self._validate(MissionQuery, arguments)
            missions = self._require_dependency(self._missions, "missions")
            return missions.mission(query.mission_id).model_dump(mode="json")
        if operation == "mission.inspect":
            query = self._validate(MissionQuery, arguments)
            inspections = self._require_dependency(
                self._mission_inspections,
                "mission inspections",
            )
            return inspections.mission(query.mission_id, limit=query.limit)
        if operation == "mission.run-reports.list":
            query = self._validate(MissionQuery, arguments)
            missions = self._require_dependency(self._missions, "missions")
            return {
                "reports": [
                    item.model_dump(mode="json")
                    for item in missions.run_reports(query.mission_id, limit=query.limit)
                ]
            }
        if operation == "mission.templates.list":
            query = self._validate(MissionTemplateQuery, arguments)
            templates = self._require_dependency(self._mission_templates, "mission templates")
            selected = (
                templates.catalogue.templates
                if not query.signals
                else templates.applicable(
                    query.signals,
                    organization_version=query.organization_version,
                )
            )
            return {"templates": [item.model_dump(mode="json") for item in selected]}
        if operation == "conversation.list":
            query = self._validate(ConversationListQuery, arguments)
            conversations = self._require_dependency(self._conversations, "conversations")
            return {
                "conversations": [
                    item.model_dump(mode="json")
                    for item in conversations.channels(
                        mission_id=query.mission_id,
                        limit=query.limit,
                    )
                ]
            }
        if operation == "conversation.get":
            query = self._validate(ConversationQuery, arguments)
            conversations = self._require_dependency(self._conversations, "conversations")
            return {
                "conversation": conversations.channel(query.conversation_id).model_dump(
                    mode="json"
                ),
                "messages": [
                    item.model_dump(mode="json")
                    for item in conversations.messages(
                        query.conversation_id,
                        limit=query.limit,
                    )
                ],
            }
        if operation == "advisory.candidates.list":
            self._require_empty(arguments)
            advisory = self._require_dependency(self._advisory, "advisory")
            candidates = advisory.candidates()
            return {
                "candidates": [item.model_dump(mode="json") for item in candidates],
                "count": len(candidates),
                "activation_authorized": False,
            }
        if operation == "notification.list":
            query = self._validate(NotificationQuery, arguments)
            notifications = self._require_dependency(self._notifications, "notifications")
            events = self._repository.events(
                after_cursor=query.after,
                limit=query.limit,
            )
            return notifications.project(
                events,
                severities=frozenset(query.severities),
                deliveries=frozenset(query.deliveries),
            ).model_dump(mode="json")
        if operation == "knowledge.sources.list":
            self._require_empty(arguments)
            knowledge_config = self._require_dependency(self._knowledge_config, "knowledge")
            selection = {
                source_id: index
                for source_ids in knowledge_config.selection_order.values()
                for index, source_id in enumerate(source_ids)
            }
            return {
                "sources": [
                    {
                        "source_id": source_id,
                        "knowledge_class": source.knowledge_class.value,
                        "adapter": source.adapter,
                        "enabled": source.enabled,
                        "cost_class": source.cost_class.value,
                        "selection_index": selection.get(source_id, -1),
                        "health": "configured_unobserved",
                    }
                    for source_id, source in sorted(knowledge_config.sources.items())
                ]
            }
        if operation == "knowledge.operations.list":
            query = self._validate(KnowledgeListQuery, arguments)
            knowledge_repository = self._require_dependency(self._knowledge, "knowledge")
            return {
                "operations": [
                    item.model_dump(mode="json")
                    for item in knowledge_repository.list_operations(
                        project_id=query.project_id,
                        offset=query.offset,
                        limit=query.limit,
                    )
                ]
            }
        if operation == "knowledge.query":
            query = self._validate(KnowledgeQuery, arguments)
            command = ApplicationCommand(
                command_type="knowledge.query",
                actor_id=principal_id,
                target_type="knowledge_query",
                target_id=str(query.query_id),
                payload={"query": query.model_dump(mode="json")},
            )
            return (await self._execute(command, principal_id)).model_dump(mode="json")
        command = self._validate(ApplicationCommand, arguments)
        if command.actor_id != principal_id:
            raise MishkanError(
                ErrorCode.AUTHORITY_NOT_GRANTED,
                "harness command actor differs from its authenticated principal",
            )
        return (await self._execute(command, principal_id)).model_dump(mode="json")

    async def read_resource(self, uri: str, *, principal_id: str) -> dict[str, Any]:
        if uri not in self.resources:
            raise MishkanError(ErrorCode.AUTHORITY_NOT_GRANTED, "MCP resource is not exposed")
        if uri == "mishkan://snapshot":
            return self._repository.snapshot(limit=self._event_page_limit).model_dump(mode="json")
        if uri == "mishkan://runs":
            return {"runs": list(self._repository.runs(offset=0, limit=self._event_page_limit))}
        if uri == "mishkan://events":
            return self._events(EventQuery(limit=self._event_page_limit)).model_dump(mode="json")
        operation_by_uri = {
            "mishkan://organization": "organization.get",
            "mishkan://missions": "mission.list",
            "mishkan://conversations": "conversation.list",
            "mishkan://advisory/candidates": "advisory.candidates.list",
            "mishkan://notifications": "notification.list",
            "mishkan://knowledge/sources": "knowledge.sources.list",
            "mishkan://knowledge/operations": "knowledge.operations.list",
        }
        operation = operation_by_uri[uri]
        arguments = (
            {"limit": self._event_page_limit}
            if operation
            in {
                "mission.list",
                "conversation.list",
                "notification.list",
                "knowledge.operations.list",
            }
            else {}
        )
        return await self.invoke(operation, arguments, principal_id=principal_id)

    def _events(self, query: EventQuery) -> EventPage:
        return self._repository.events(
            after_cursor=query.after,
            limit=query.limit,
            event_types=query.event_types,
            entity_type=query.entity_type,
            entity_id=query.entity_id,
        )

    @staticmethod
    def _require_empty(arguments: dict[str, Any]) -> None:
        if arguments:
            raise MishkanError(ErrorCode.OUTPUT_CONTRACT, "operation accepts no arguments")

    @staticmethod
    def _require_dependency(value: DependencyT | None, name: str) -> DependencyT:
        if value is None:
            raise MishkanError(
                ErrorCode.REQUIRED_DEPENDENCY,
                f"MCP facade {name} query is not configured",
            )
        return value

    @staticmethod
    def _validate(model: type[BaseModel], arguments: dict[str, Any]) -> Any:
        try:
            return model.model_validate(arguments)
        except ValidationError as exc:
            raise MishkanError(
                ErrorCode.OUTPUT_CONTRACT,
                "harness operation arguments are invalid",
            ) from exc
