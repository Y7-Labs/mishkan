"""Server-derived authorization scopes for authoritative application commands.

Command semantics live here as public, versioned contracts. Operational grants do
not: they remain in the configured public policy documents.
"""

from __future__ import annotations

import base64
import math
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from types import MappingProxyType
from typing import Protocol
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import ValidationError

from mishkan.application.contracts import (
    ApplicationCommand,
    ProspectiveRunRequest,
    RepositoryEstablishmentRequest,
    RunInitializationRequest,
)
from mishkan.config.models import (
    CredentialReference,
    McpConfig,
    McpConnectionConfig,
    McpTransport,
    MishkanConfig,
)
from mishkan.context import ContextualRecommendationRequest
from mishkan.conversations import (
    ConversationChannel,
    ConversationMessage,
    MissionDecision,
    MissionEscalation,
    MissionIntervention,
)
from mishkan.crewai.mission_governance import MissionGovernanceRequest
from mishkan.domain.errors import ErrorCode, MishkanError
from mishkan.domain.time import utc_now
from mishkan.edits import ChangeSet
from mishkan.edits.git import GitEffectMode, GitEffectRequest
from mishkan.environment import (
    EngineeringCommandRequest,
    EnvironmentBindingRequest,
    EnvironmentDescriptorChangeRequest,
    EnvironmentDescriptorSet,
    EnvironmentInvalidation,
    EnvironmentObservationRequest,
    EnvironmentOperationPlan,
    EnvironmentOperationRequest,
    EnvironmentVerificationRequest,
)
from mishkan.execution import ExecutionRequest, ExecutionSession
from mishkan.knowledge import (
    KnowledgeClass,
    KnowledgeIngestRequest,
    KnowledgeMemoryCaptureRequest,
    KnowledgeOperation,
    KnowledgeOperationReconcileRequest,
    KnowledgePromotion,
    KnowledgePromotionDecision,
    KnowledgeQuery,
    KnowledgeRefreshRequest,
)
from mishkan.missions import (
    MissionBrief,
    MissionCrewRevision,
    MissionRecord,
    MissionRunBinding,
    MissionRunReport,
    MissionTaskAssignment,
    MissionTaskClaimRequest,
    MissionTransition,
)
from mishkan.missions.environment import (
    MissionEnvironmentPlan,
    MissionEnvironmentPlanningRequest,
)
from mishkan.organization import (
    ProfessionalEvidenceRecord,
    ProfessionalPromotionDisposition,
    ProfessionalPromotionRequest,
)
from mishkan.policy import (
    AuthorizationDecision,
    AuthorizationRequest,
    Decision,
    EffectivePolicy,
    PolicyAuthority,
    PolicyLoader,
    ResourceRequest,
)
from mishkan.skills.models import (
    SkillInvocationRequest,
    SkillLearningRequest,
    SkillLifecycleDecision,
    SkillUsageRecord,
    SkillVersionRecord,
)
from mishkan.telemetry.models import LangSmithFeedbackImportRequest
from mishkan.tools.models import RegistryEntryKind, RegistryLifecycleAction, RegistryMutation


class ChangeSetLookup(Protocol):
    def definition(self, change_set_id: UUID) -> ChangeSet: ...


class SessionLookup(Protocol):
    def status(self, session_id: UUID) -> ExecutionSession: ...


class McpCallLookup(Protocol):
    def call_connection_id(self, request_id: UUID) -> str: ...


class KnowledgeOperationLookup(Protocol):
    def operation(self, operation_id: UUID) -> KnowledgeOperation: ...


@dataclass(frozen=True, slots=True)
class CommandSemantics:
    capability: str
    effect_class: str
    effects: tuple[str, ...]
    network: bool = False


# This registry describes immutable command meaning. It is deliberately not an
# operational allow-list; PolicyAuthority remains closed-world and decides grants.
COMMAND_SEMANTICS = MappingProxyType(
    {
        "system.checkpoint": CommandSemantics(
            "application.system.checkpoint", "control", ("state.checkpoint",)
        ),
        "run.initialize": CommandSemantics(
            "application.run.initialize", "coordination", ("run.initialize",), True
        ),
        "run.prospective.create": CommandSemantics(
            "application.run.context", "coordination", ("run.prospective.create",)
        ),
        "run.repository.establish": CommandSemantics(
            "application.run.context", "coordination", ("run.repository.establish",)
        ),
        "run.cancel": CommandSemantics("application.run.control", "coordination", ("run.cancel",)),
        "run.recover": CommandSemantics(
            "application.run.control", "coordination", ("run.recover",)
        ),
        "artifact.upload.open": CommandSemantics(
            "application.artifact.upload", "artifact", ("artifact.stage",)
        ),
        "artifact.upload.chunk": CommandSemantics(
            "application.artifact.upload", "artifact", ("artifact.append",)
        ),
        "artifact.upload.commit": CommandSemantics(
            "application.artifact.upload", "artifact", ("artifact.publish",)
        ),
        "artifact.upload.abort": CommandSemantics(
            "application.artifact.upload", "artifact", ("artifact.abort",)
        ),
        "artifact.reference.update": CommandSemantics(
            "application.artifact.reference", "artifact", ("artifact.reference.update",)
        ),
        "artifact.collection.create": CommandSemantics(
            "application.artifact.collection", "artifact", ("artifact.collection.create",)
        ),
        "artifact.hold.set": CommandSemantics(
            "application.artifact.retention", "artifact", ("artifact.hold.set",)
        ),
        "artifact.hold.release": CommandSemantics(
            "application.artifact.retention", "artifact", ("artifact.hold.release",)
        ),
        "artifact.pin.set": CommandSemantics(
            "application.artifact.retention", "artifact", ("artifact.pin.set",)
        ),
        "artifact.pin.release": CommandSemantics(
            "application.artifact.retention", "artifact", ("artifact.pin.release",)
        ),
        "artifact.gc.plan": CommandSemantics(
            "application.artifact.gc", "artifact", ("artifact.gc.plan",)
        ),
        "artifact.gc.apply": CommandSemantics(
            "application.artifact.gc", "artifact", ("artifact.gc.apply",)
        ),
        "artifact.reconcile.plan": CommandSemantics(
            "application.artifact.reconcile", "artifact", ("artifact.reconcile.plan",)
        ),
        "artifact.reconcile.apply": CommandSemantics(
            "application.artifact.reconcile", "artifact", ("artifact.reconcile.apply",)
        ),
        "event.hold.create": CommandSemantics(
            "application.event.hold", "control", ("event.hold.create",)
        ),
        "event.hold.release": CommandSemantics(
            "application.event.hold", "control", ("event.hold.release",)
        ),
        "event.retention.plan": CommandSemantics(
            "application.event.retention", "control", ("event.retention.plan",)
        ),
        "event.retention.apply": CommandSemantics(
            "application.event.retention", "control", ("event.retention.apply",)
        ),
        "change.plan": CommandSemantics("application.change.plan", "filesystem", ("change.plan",)),
        "change.apply": CommandSemantics(
            "application.change.apply", "filesystem", ("change.apply",)
        ),
        "change.reconcile": CommandSemantics(
            "application.change.reconcile", "filesystem", ("change.reconcile",)
        ),
        **{
            f"git.{mode.value}": CommandSemantics(
                f"git.{mode.value}",
                mode.effect_class,
                (f"git.{mode.value}",),
                mode.value in {"push", "force_with_lease", "force_push"},
            )
            for mode in GitEffectMode
        },
        "session.start": CommandSemantics(
            "application.session.start", "process", ("process.start",)
        ),
        "session.write": CommandSemantics("application.session.io", "process", ("process.input",)),
        "session.resize": CommandSemantics(
            "application.session.io", "process", ("process.resize",)
        ),
        "session.signal": CommandSemantics(
            "application.session.control", "process", ("process.signal",)
        ),
        "session.cancel": CommandSemantics(
            "application.session.control", "process", ("process.cancel",)
        ),
        "session.settle": CommandSemantics(
            "application.session.settle", "process", ("process.settle",)
        ),
        "mcp.connection.connect": CommandSemantics(
            "application.mcp.connect", "external", ("mcp.connect",), True
        ),
        "mcp.call.cancel": CommandSemantics(
            "application.mcp.control", "external", ("mcp.call.cancel",), True
        ),
        "mcp.call.reconcile": CommandSemantics(
            "application.mcp.control", "external", ("mcp.call.reconcile",), True
        ),
        "knowledge.query": CommandSemantics(
            "application.knowledge.query", "read", ("knowledge.query",)
        ),
        "knowledge.ingest": CommandSemantics(
            "application.knowledge.mutate", "external", ("knowledge.ingest",), True
        ),
        "knowledge.refresh": CommandSemantics(
            "application.knowledge.mutate", "external", ("knowledge.refresh",), True
        ),
        "knowledge.operation.cancel": CommandSemantics(
            "application.knowledge.mutate", "external", ("knowledge.operation.cancel",), True
        ),
        "knowledge.operation.reconcile": CommandSemantics(
            "application.knowledge.mutate", "external", ("knowledge.operation.reconcile",), True
        ),
        "knowledge.memory.capture": CommandSemantics(
            "application.knowledge.mutate", "external", ("knowledge.memory.capture",), True
        ),
        "knowledge.promotion.propose": CommandSemantics(
            "application.knowledge.promotion", "control", ("knowledge.promotion.propose",)
        ),
        "knowledge.promotion.decide": CommandSemantics(
            "application.knowledge.promotion", "control", ("knowledge.promotion.decide",)
        ),
        "skill.version.register": CommandSemantics(
            "application.skill.lifecycle", "skill_lifecycle", ("skill.version.register",)
        ),
        "skill.version.decide": CommandSemantics(
            "application.skill.lifecycle", "skill_lifecycle", ("skill.version.decide",)
        ),
        "skill.version.archive": CommandSemantics(
            "application.skill.lifecycle", "skill_lifecycle", ("skill.version.archive",)
        ),
        "skill.version.delete": CommandSemantics(
            "application.skill.lifecycle", "skill_lifecycle", ("skill.delete",)
        ),
        "skill.version.restore": CommandSemantics(
            "application.skill.lifecycle", "skill_lifecycle", ("skill.restore",)
        ),
        "skill.version.reset": CommandSemantics(
            "application.skill.lifecycle", "skill_lifecycle", ("skill.reset",)
        ),
        "skill.version.pin": CommandSemantics(
            "application.skill.lifecycle", "skill_lifecycle", ("skill.version.pin",)
        ),
        "skill.version.unpin": CommandSemantics(
            "application.skill.lifecycle", "skill_lifecycle", ("skill.version.unpin",)
        ),
        "skill.usage.record": CommandSemantics(
            "application.skill.usage", "control", ("skill.usage.record",)
        ),
        "skill.invoke": CommandSemantics("application.skill.invoke", "control", ("skill.invoke",)),
        "skill.learn": CommandSemantics(
            "application.skill.learn", "skill_lifecycle", ("skill.proposal.create",)
        ),
        "environment.observe": CommandSemantics(
            "application.environment.observe", "read", ("environment.observe",)
        ),
        "environment.resolve": CommandSemantics(
            "application.environment.resolve", "control", ("environment.resolve",)
        ),
        "environment.descriptor.validate": CommandSemantics(
            "application.environment.descriptor", "read", ("environment.descriptor.validate",)
        ),
        "environment.descriptor.change.plan": CommandSemantics(
            "application.environment.descriptor", "control", ("environment.descriptor.change.plan",)
        ),
        "environment.operation.plan": CommandSemantics(
            "application.environment.operation", "control", ("environment.operation.plan",)
        ),
        "environment.command.plan": CommandSemantics(
            "application.environment.command", "control", ("environment.command.plan",)
        ),
        "environment.attempt.settle": CommandSemantics(
            "application.environment.attempt", "control", ("environment.attempt.settle",)
        ),
        "environment.verification.record": CommandSemantics(
            "application.environment.verification",
            "control",
            ("environment.verification.record",),
        ),
        "environment.binding.invalidate": CommandSemantics(
            "application.environment.invalidate", "control", ("environment.binding.invalidate",)
        ),
        "telemetry.evaluation.import": CommandSemantics(
            "application.telemetry.evaluation",
            "artifact",
            ("telemetry.evaluation.import",),
        ),
        "context.recommend": CommandSemantics(
            "application.context.recommend", "read", ("context.recommend",)
        ),
        "mission.create": CommandSemantics(
            "application.mission.lifecycle", "coordination", ("mission.create",)
        ),
        "mission.brief.record": CommandSemantics(
            "application.mission.brief", "coordination", ("mission.brief.record",)
        ),
        "mission.crew.record": CommandSemantics(
            "application.mission.crew", "coordination", ("mission.crew.record",)
        ),
        "conversation.create": CommandSemantics(
            "application.conversation.lifecycle", "coordination", ("conversation.create",)
        ),
        "conversation.message.post": CommandSemantics(
            "application.conversation.message", "coordination", ("conversation.message.post",)
        ),
        "mission.decision.record": CommandSemantics(
            "application.mission.decision", "coordination", ("mission.decision.record",)
        ),
        "mission.escalation.open": CommandSemantics(
            "application.mission.escalation", "coordination", ("mission.escalation.open",)
        ),
        "mission.intervention.apply": CommandSemantics(
            "application.mission.intervention", "coordination", ("mission.intervention.apply",)
        ),
        "mission.assignment.record": CommandSemantics(
            "application.mission.assignment", "coordination", ("mission.assignment.record",)
        ),
        "mission.run-binding.record": CommandSemantics(
            "application.mission.run-binding",
            "coordination",
            ("mission.run-binding.record",),
        ),
        "mission.run-report.record": CommandSemantics(
            "application.mission.reporting",
            "coordination",
            ("mission.run-report.record",),
        ),
        "mission.task.claim": CommandSemantics(
            "application.mission.task", "coordination", ("mission.task.claim",)
        ),
        "mission.transition": CommandSemantics(
            "application.mission.lifecycle", "coordination", ("mission.transition",)
        ),
        "mission.governance.propose": CommandSemantics(
            "application.mission.governance", "coordination", ("mission.governance.propose",)
        ),
        "mission.environment.propose": CommandSemantics(
            "application.mission.environment",
            "coordination",
            ("mission.environment.propose",),
        ),
        "mission.environment.accept": CommandSemantics(
            "application.mission.environment",
            "coordination",
            ("mission.environment.accept",),
        ),
        "mission.environment.resolve": CommandSemantics(
            "application.mission.environment",
            "coordination",
            ("mission.environment.resolve",),
        ),
        "organization.evidence.record": CommandSemantics(
            "application.organization.evolution",
            "coordination",
            ("organization.evidence.record",),
        ),
        "organization.promotion.decide": CommandSemantics(
            "application.organization.evolution",
            "coordination",
            ("organization.promotion.decide",),
        ),
        **{
            f"registry.entry.{action.value}": CommandSemantics(
                "application.registry.lifecycle",
                "registry_lifecycle",
                (f"registry.{action.value}",),
            )
            for action in RegistryLifecycleAction
        },
    }
)

_COMMAND_TARGETS = MappingProxyType(
    {
        "system.checkpoint": ("system", "optional"),
        "run.initialize": ("run", "absent"),
        "run.prospective.create": ("run", "absent"),
        "run.repository.establish": ("run", "required"),
        "run.cancel": ("run", "required"),
        "run.recover": ("run", "required"),
        "artifact.upload.open": ("artifact_service", "absent"),
        "artifact.upload.chunk": ("artifact_upload", "uuid"),
        "artifact.upload.commit": ("artifact_upload", "uuid"),
        "artifact.upload.abort": ("artifact_upload", "uuid"),
        "artifact.reference.update": ("artifact_reference", "absent"),
        "artifact.collection.create": ("artifact_service", "absent"),
        "artifact.hold.set": ("artifact", "uuid"),
        "artifact.hold.release": ("artifact", "uuid"),
        "artifact.pin.set": ("artifact", "uuid"),
        "artifact.pin.release": ("artifact", "uuid"),
        "artifact.gc.plan": ("artifact_service", "absent"),
        "artifact.gc.apply": ("artifact_gc_plan", "uuid"),
        "artifact.reconcile.plan": ("artifact_service", "absent"),
        "artifact.reconcile.apply": ("artifact_reconciliation_plan", "uuid"),
        "event.hold.create": ("event_store", "absent"),
        "event.hold.release": ("event_hold", "uuid"),
        "event.retention.plan": ("event_store", "absent"),
        "event.retention.apply": ("event_retention_plan", "uuid"),
        "change.plan": ("change_set", "uuid"),
        "change.apply": ("change_set", "uuid"),
        "change.reconcile": ("change_set", "uuid"),
        **{f"git.{mode.value}": ("git_repository", "required") for mode in GitEffectMode},
        "session.start": ("session_service", "absent"),
        "session.write": ("session", "uuid"),
        "session.resize": ("session", "uuid"),
        "session.signal": ("session", "uuid"),
        "session.cancel": ("session", "uuid"),
        "session.settle": ("session", "uuid"),
        "mcp.connection.connect": ("mcp_connection", "required"),
        "mcp.call.cancel": ("mcp_call", "uuid"),
        "mcp.call.reconcile": ("mcp_call", "uuid"),
        "knowledge.query": ("knowledge_query", "uuid"),
        "knowledge.ingest": ("knowledge_operation", "uuid"),
        "knowledge.refresh": ("knowledge_operation", "uuid"),
        "knowledge.operation.cancel": ("knowledge_operation", "uuid"),
        "knowledge.operation.reconcile": ("knowledge_operation", "uuid"),
        "knowledge.memory.capture": ("knowledge_operation", "uuid"),
        "knowledge.promotion.propose": ("knowledge_promotion", "uuid"),
        "knowledge.promotion.decide": ("knowledge_promotion", "uuid"),
        "skill.version.register": ("skill_version", "uuid"),
        "skill.version.decide": ("skill_version", "uuid"),
        "skill.version.archive": ("skill_version", "uuid"),
        "skill.version.delete": ("skill_version", "uuid"),
        "skill.version.restore": ("skill_version", "uuid"),
        "skill.version.reset": ("skill_version", "uuid"),
        "skill.version.pin": ("skill_version", "uuid"),
        "skill.version.unpin": ("skill_version", "uuid"),
        "skill.usage.record": ("skill_usage", "uuid"),
        "skill.invoke": ("task", "required"),
        "skill.learn": ("skill_learning", "uuid"),
        "environment.observe": ("environment_observation", "uuid"),
        "environment.resolve": ("environment_binding_request", "uuid"),
        "environment.descriptor.validate": ("environment_descriptor_set", "uuid"),
        "environment.descriptor.change.plan": ("environment_descriptor_change", "uuid"),
        "environment.operation.plan": ("environment_operation", "uuid"),
        "environment.command.plan": ("engineering_command", "uuid"),
        "environment.attempt.settle": ("environment_operation", "uuid"),
        "environment.verification.record": ("environment_verification", "uuid"),
        "environment.binding.invalidate": ("environment_binding", "uuid"),
        "telemetry.evaluation.import": ("telemetry_evaluation", "uuid"),
        "context.recommend": ("context_recommendation", "uuid"),
        "mission.create": ("mission", "uuid"),
        "mission.brief.record": ("mission", "uuid"),
        "mission.crew.record": ("mission", "uuid"),
        "conversation.create": ("conversation", "uuid"),
        "conversation.message.post": ("conversation_message", "uuid"),
        "mission.decision.record": ("mission_decision", "uuid"),
        "mission.escalation.open": ("mission_escalation", "uuid"),
        "mission.intervention.apply": ("mission", "uuid"),
        "mission.assignment.record": ("mission_assignment", "uuid"),
        "mission.run-binding.record": ("mission_run_binding", "uuid"),
        "mission.run-report.record": ("mission_run_report", "uuid"),
        "mission.task.claim": ("mission_task", "required"),
        "mission.transition": ("mission", "uuid"),
        "mission.governance.propose": ("mission_governance_request", "uuid"),
        "mission.environment.propose": ("mission_environment_planning_request", "uuid"),
        "mission.environment.accept": ("mission", "uuid"),
        "mission.environment.resolve": ("mission_environment_plan", "uuid"),
        "organization.evidence.record": ("professional_evidence", "uuid"),
        "organization.promotion.decide": ("professional_promotion_request", "uuid"),
        **{
            f"registry.entry.{action.value}": ("registry_entry", "required")
            for action in RegistryLifecycleAction
        },
    }
)

_COMMAND_PAYLOAD_FIELDS = MappingProxyType(
    {
        "system.checkpoint": (frozenset(), frozenset({"checkpoint", "index"})),
        "run.initialize": (frozenset({"objective"}), frozenset({"schema_version"})),
        "run.prospective.create": (
            frozenset({"workspace_id", "objective", "outcome_id"}),
            frozenset({"schema_version"}),
        ),
        "run.repository.establish": (
            frozenset(
                {
                    "prospective_workspace_id",
                    "discovery_revision",
                    "evidence_references",
                }
            ),
            frozenset({"schema_version"}),
        ),
        "run.cancel": (frozenset(), frozenset()),
        "run.recover": (frozenset(), frozenset()),
        "artifact.upload.open": (
            frozenset({"expected_size", "expected_digest", "media_type", "provenance"}),
            frozenset({"sensitivity", "retention"}),
        ),
        "artifact.upload.chunk": (
            frozenset({"offset", "content_base64"}),
            frozenset(),
        ),
        "artifact.upload.commit": (frozenset(), frozenset()),
        "artifact.upload.abort": (frozenset(), frozenset()),
        "artifact.reference.update": (
            frozenset({"scope", "name", "artifact_reference", "expected_reference_revision"}),
            frozenset(),
        ),
        "artifact.collection.create": (frozenset({"entries"}), frozenset()),
        "artifact.hold.set": (frozenset({"reason"}), frozenset()),
        "artifact.hold.release": (frozenset(), frozenset()),
        "artifact.pin.set": (frozenset(), frozenset()),
        "artifact.pin.release": (frozenset(), frozenset()),
        "artifact.gc.plan": (frozenset({"watermark"}), frozenset()),
        "artifact.gc.apply": (frozenset(), frozenset()),
        "artifact.reconcile.plan": (frozenset(), frozenset()),
        "artifact.reconcile.apply": (frozenset(), frozenset()),
        "event.hold.create": (
            frozenset({"scope", "reason"}),
            frozenset({"scope_id"}),
        ),
        "event.hold.release": (frozenset(), frozenset()),
        "event.retention.plan": (frozenset(), frozenset()),
        "event.retention.apply": (frozenset(), frozenset()),
        "change.plan": (frozenset({"change_set"}), frozenset()),
        "change.apply": (frozenset(), frozenset()),
        "change.reconcile": (frozenset(), frozenset()),
        **{f"git.{mode.value}": (frozenset({"request"}), frozenset()) for mode in GitEffectMode},
        "session.start": (frozenset({"request"}), frozenset()),
        "session.write": (
            frozenset({"content_base64", "declared_effects", "network_destinations"}),
            frozenset(),
        ),
        "session.resize": (frozenset({"rows", "columns"}), frozenset()),
        "session.signal": (frozenset({"signal"}), frozenset()),
        "session.cancel": (frozenset(), frozenset()),
        "session.settle": (frozenset(), frozenset()),
        "mcp.connection.connect": (frozenset(), frozenset()),
        "mcp.call.cancel": (frozenset(), frozenset()),
        "mcp.call.reconcile": (frozenset(), frozenset()),
        "knowledge.query": (frozenset({"query"}), frozenset()),
        "knowledge.ingest": (frozenset({"request"}), frozenset()),
        "knowledge.refresh": (frozenset({"request"}), frozenset()),
        "knowledge.operation.cancel": (frozenset(), frozenset()),
        "knowledge.operation.reconcile": (frozenset({"request"}), frozenset()),
        "knowledge.memory.capture": (frozenset({"request"}), frozenset()),
        "knowledge.promotion.propose": (frozenset({"proposal"}), frozenset()),
        "knowledge.promotion.decide": (frozenset({"decision"}), frozenset()),
        "skill.version.register": (frozenset({"record"}), frozenset()),
        "skill.version.decide": (frozenset({"decision"}), frozenset()),
        "skill.version.archive": (
            frozenset({"decision", "expected_revision"}),
            frozenset(),
        ),
        "skill.version.delete": (
            frozenset({"decision", "expected_revision"}),
            frozenset(),
        ),
        "skill.version.restore": (
            frozenset({"decision", "expected_revision"}),
            frozenset(),
        ),
        "skill.version.reset": (
            frozenset({"decision", "expected_revision"}),
            frozenset(),
        ),
        "skill.version.pin": (frozenset({"expected_revision"}), frozenset()),
        "skill.version.unpin": (frozenset({"expected_revision"}), frozenset()),
        "skill.usage.record": (frozenset({"record"}), frozenset()),
        "skill.invoke": (frozenset({"request"}), frozenset()),
        "skill.learn": (frozenset({"request"}), frozenset()),
        "environment.observe": (frozenset({"request"}), frozenset()),
        "environment.resolve": (frozenset({"request"}), frozenset()),
        "environment.descriptor.validate": (frozenset({"descriptor_set"}), frozenset()),
        "environment.descriptor.change.plan": (frozenset({"request"}), frozenset()),
        "environment.operation.plan": (frozenset({"request"}), frozenset()),
        "environment.command.plan": (frozenset({"request"}), frozenset()),
        "environment.attempt.settle": (
            frozenset({"operation_plan", "session_id"}),
            frozenset(),
        ),
        "environment.verification.record": (frozenset({"request"}), frozenset()),
        "environment.binding.invalidate": (frozenset({"invalidation"}), frozenset()),
        "telemetry.evaluation.import": (frozenset({"request"}), frozenset()),
        "context.recommend": (frozenset({"request"}), frozenset()),
        "mission.create": (frozenset({"record"}), frozenset()),
        "mission.brief.record": (frozenset({"brief"}), frozenset()),
        "mission.crew.record": (frozenset({"crew"}), frozenset()),
        "conversation.create": (frozenset({"channel"}), frozenset()),
        "conversation.message.post": (frozenset({"message"}), frozenset()),
        "mission.decision.record": (frozenset({"decision"}), frozenset()),
        "mission.escalation.open": (frozenset({"escalation"}), frozenset()),
        "mission.intervention.apply": (frozenset({"intervention"}), frozenset()),
        "mission.assignment.record": (frozenset({"assignment"}), frozenset()),
        "mission.run-binding.record": (frozenset({"binding"}), frozenset()),
        "mission.run-report.record": (frozenset({"report"}), frozenset()),
        "mission.task.claim": (frozenset({"request"}), frozenset()),
        "mission.transition": (frozenset({"transition"}), frozenset()),
        "mission.governance.propose": (frozenset({"request"}), frozenset()),
        "mission.environment.propose": (frozenset({"request"}), frozenset()),
        "mission.environment.accept": (frozenset({"plan"}), frozenset()),
        "mission.environment.resolve": (frozenset({"context_id"}), frozenset()),
        "organization.evidence.record": (frozenset({"evidence"}), frozenset()),
        "organization.promotion.decide": (
            frozenset({"request", "disposition", "reason"}),
            frozenset(),
        ),
        "registry.entry.add": (frozenset({"entry_kind", "definition"}), frozenset()),
        "registry.entry.enable": (frozenset({"entry_kind"}), frozenset()),
        "registry.entry.disable": (frozenset({"entry_kind"}), frozenset()),
        "registry.entry.update": (frozenset({"entry_kind", "definition"}), frozenset()),
        "registry.entry.remove": (frozenset({"entry_kind"}), frozenset()),
        "registry.entry.set_precedence": (
            frozenset({"entry_kind", "precedence"}),
            frozenset(),
        ),
    }
)


@dataclass(frozen=True, slots=True)
class AuthorizedApplicationCommand:
    command: ApplicationCommand
    request: AuthorizationRequest
    decision: AuthorizationDecision
    prospective_run_request: ProspectiveRunRequest | None = None
    repository_establishment_request: RepositoryEstablishmentRequest | None = None
    session_request: ExecutionRequest | None = None
    git_request: GitEffectRequest | None = None
    registry_mutation: RegistryMutation | None = None
    skill_version: SkillVersionRecord | None = None
    skill_decision: SkillLifecycleDecision | None = None
    skill_usage: SkillUsageRecord | None = None
    skill_invocation: SkillInvocationRequest | None = None
    skill_learning: SkillLearningRequest | None = None
    environment_observation: EnvironmentObservationRequest | None = None
    environment_binding: EnvironmentBindingRequest | None = None
    environment_descriptor_set: EnvironmentDescriptorSet | None = None
    environment_descriptor_change: EnvironmentDescriptorChangeRequest | None = None
    environment_operation: EnvironmentOperationRequest | None = None
    engineering_command: EngineeringCommandRequest | None = None
    environment_operation_plan: EnvironmentOperationPlan | None = None
    environment_verification: EnvironmentVerificationRequest | None = None
    environment_invalidation: EnvironmentInvalidation | None = None
    telemetry_evaluation: LangSmithFeedbackImportRequest | None = None
    context_recommendation: ContextualRecommendationRequest | None = None
    mission_record: MissionRecord | None = None
    mission_brief: MissionBrief | None = None
    mission_crew: MissionCrewRevision | None = None
    conversation_channel: ConversationChannel | None = None
    conversation_message: ConversationMessage | None = None
    mission_decision: MissionDecision | None = None
    mission_escalation: MissionEscalation | None = None
    mission_intervention: MissionIntervention | None = None
    mission_assignment: MissionTaskAssignment | None = None
    mission_run_binding: MissionRunBinding | None = None
    mission_run_report: MissionRunReport | None = None
    mission_task_claim: MissionTaskClaimRequest | None = None
    mission_transition: MissionTransition | None = None
    mission_governance_request: MissionGovernanceRequest | None = None
    mission_environment_planning_request: MissionEnvironmentPlanningRequest | None = None
    mission_environment_plan: MissionEnvironmentPlan | None = None
    professional_evidence: ProfessionalEvidenceRecord | None = None
    professional_promotion_request: ProfessionalPromotionRequest | None = None
    professional_promotion_disposition: ProfessionalPromotionDisposition | None = None
    knowledge_query: KnowledgeQuery | None = None
    knowledge_ingest: KnowledgeIngestRequest | None = None
    knowledge_refresh: KnowledgeRefreshRequest | None = None
    knowledge_reconcile: KnowledgeOperationReconcileRequest | None = None
    knowledge_memory_capture: KnowledgeMemoryCaptureRequest | None = None
    knowledge_promotion: KnowledgePromotion | None = None
    knowledge_promotion_decision: KnowledgePromotionDecision | None = None


class ApplicationCommandAuthority:
    """Validate command meaning and evaluate its server-derived exact scope."""

    def __init__(
        self,
        config: MishkanConfig,
        workspace: Path,
        changes: ChangeSetLookup,
        sessions: SessionLookup,
        mcp_calls: McpCallLookup | None = None,
        knowledge_operations: KnowledgeOperationLookup | None = None,
    ) -> None:
        self._config = config
        self._workspace = workspace.resolve(strict=True)
        self._changes = changes
        self._sessions = sessions
        self._mcp_calls = mcp_calls
        self._knowledge_operations = knowledge_operations
        self._policy: EffectivePolicy = PolicyLoader().load(config.policy_sources, self._workspace)

    @property
    def policy(self) -> EffectivePolicy:
        return self._policy

    def authorize(self, command: ApplicationCommand) -> AuthorizedApplicationCommand:
        # Deep-normalize the mutable payload before deriving authority and dispatching.
        normalized = ApplicationCommand.model_validate_json(command.model_dump_json())
        semantics = COMMAND_SEMANTICS.get(normalized.command_type)
        if semantics is None:
            raise MishkanError(
                ErrorCode.OUTPUT_CONTRACT,
                "application command type has no public authorization contract",
                details={"command_type": normalized.command_type},
            )
        self._validate_envelope(normalized)

        paths: tuple[str, ...] = ()
        executables: tuple[str, ...] = ()
        arguments: tuple[str, ...] = (normalized.command_type,)
        environments: tuple[str, ...] = ()
        credentials: tuple[str, ...] = ()
        external_resources: tuple[str, ...] = ()
        network_destinations: tuple[str, ...] = ()
        remotes: tuple[str, ...] = ()
        branches: tuple[str, ...] = ()
        uses_network = semantics.network
        effects = semantics.effects
        timeout = 120
        session_request: ExecutionRequest | None = None
        git_request: GitEffectRequest | None = None
        registry_mutation: RegistryMutation | None = None
        skill_version: SkillVersionRecord | None = None
        skill_decision: SkillLifecycleDecision | None = None
        skill_usage: SkillUsageRecord | None = None
        skill_invocation: SkillInvocationRequest | None = None
        skill_learning: SkillLearningRequest | None = None
        environment_observation: EnvironmentObservationRequest | None = None
        environment_binding: EnvironmentBindingRequest | None = None
        environment_descriptor_set: EnvironmentDescriptorSet | None = None
        environment_descriptor_change: EnvironmentDescriptorChangeRequest | None = None
        environment_operation: EnvironmentOperationRequest | None = None
        engineering_command: EngineeringCommandRequest | None = None
        environment_operation_plan: EnvironmentOperationPlan | None = None
        environment_verification: EnvironmentVerificationRequest | None = None
        environment_invalidation: EnvironmentInvalidation | None = None
        telemetry_evaluation: LangSmithFeedbackImportRequest | None = None
        context_recommendation: ContextualRecommendationRequest | None = None
        mission_record: MissionRecord | None = None
        mission_brief: MissionBrief | None = None
        mission_crew: MissionCrewRevision | None = None
        conversation_channel: ConversationChannel | None = None
        conversation_message: ConversationMessage | None = None
        mission_decision: MissionDecision | None = None
        mission_escalation: MissionEscalation | None = None
        mission_intervention: MissionIntervention | None = None
        mission_assignment: MissionTaskAssignment | None = None
        mission_run_binding: MissionRunBinding | None = None
        mission_run_report: MissionRunReport | None = None
        mission_task_claim: MissionTaskClaimRequest | None = None
        mission_transition: MissionTransition | None = None
        mission_governance_request: MissionGovernanceRequest | None = None
        mission_environment_planning_request: MissionEnvironmentPlanningRequest | None = None
        mission_environment_plan: MissionEnvironmentPlan | None = None
        professional_evidence: ProfessionalEvidenceRecord | None = None
        professional_promotion_request: ProfessionalPromotionRequest | None = None
        professional_promotion_disposition: ProfessionalPromotionDisposition | None = None
        prospective_run_request: ProspectiveRunRequest | None = None
        repository_establishment_request: RepositoryEstablishmentRequest | None = None
        knowledge_query: KnowledgeQuery | None = None
        knowledge_ingest: KnowledgeIngestRequest | None = None
        knowledge_refresh: KnowledgeRefreshRequest | None = None
        knowledge_reconcile: KnowledgeOperationReconcileRequest | None = None
        knowledge_memory_capture: KnowledgeMemoryCaptureRequest | None = None
        knowledge_promotion: KnowledgePromotion | None = None
        knowledge_promotion_decision: KnowledgePromotionDecision | None = None

        try:
            if normalized.command_type == "run.initialize":
                RunInitializationRequest.model_validate(normalized.payload)
                timeout = self._config.crewai.model_timeout_seconds
            elif normalized.command_type == "run.prospective.create":
                prospective_run_request = ProspectiveRunRequest.model_validate(normalized.payload)
                paths = (str(self._workspace),)
                external_resources = (
                    f"prospective-workspace:{prospective_run_request.workspace_id}",
                )
            elif normalized.command_type == "run.repository.establish":
                repository_establishment_request = RepositoryEstablishmentRequest.model_validate(
                    normalized.payload
                )
                paths = (str(self._workspace),)
                external_resources = (
                    f"run:{normalized.target_id}",
                    "repository:configured-workspace",
                    f"prospective-workspace:{repository_establishment_request.prospective_workspace_id}",
                    *(
                        f"evidence:{reference}"
                        for reference in repository_establishment_request.evidence_references
                    ),
                )
            elif normalized.command_type == "change.plan":
                change_set = ChangeSet.model_validate(normalized.payload["change_set"])
                if normalized.target_id != str(change_set.id):
                    raise MishkanError(
                        ErrorCode.OUTPUT_CONTRACT,
                        "change plan target differs from its immutable change-set identity",
                    )
                paths, effects = self._change_scope(change_set, semantics.effects)
            elif normalized.command_type in {"change.apply", "change.reconcile"}:
                change_set = self._changes.definition(self._target_uuid(normalized))
                paths, effects = self._change_scope(change_set, semantics.effects)
            elif normalized.command_type.startswith("git."):
                git_request = GitEffectRequest.model_validate(normalized.payload["request"])
                if normalized.command_type != f"git.{git_request.mode.value}":
                    raise MishkanError(
                        ErrorCode.OUTPUT_CONTRACT,
                        "Git command type differs from its typed effect request",
                    )
                if git_request.workspace.resolve(strict=True) != self._workspace:
                    raise MishkanError(
                        ErrorCode.AUTHORITY_NOT_GRANTED,
                        "Git effect must target the daemon's exact configured repository",
                    )
                if normalized.target_id != str(self._workspace):
                    raise MishkanError(
                        ErrorCode.OUTPUT_CONTRACT,
                        "Git command target differs from the configured repository identity",
                    )
                if (
                    git_request.credential_reference is not None
                    and git_request.credential_reference not in self._config.credential_bindings
                ):
                    raise MishkanError(
                        ErrorCode.AUTHORIZATION_MISSING,
                        "Git credential reference is not configured",
                    )
                paths = git_request.paths
                remotes = (git_request.remote,) if git_request.remote else ()
                branches = (git_request.branch,) if git_request.branch else ()
                credentials = (
                    (git_request.credential_reference,)
                    if git_request.credential_reference is not None
                    else ()
                )
                timeout = git_request.timeout_seconds
            elif normalized.command_type == "session.start":
                session_request = ExecutionRequest.model_validate(normalized.payload["request"])
                if session_request.owner != normalized.actor_id:
                    raise MishkanError(
                        ErrorCode.AUTHORITY_NOT_GRANTED,
                        "session owner must match the authenticated command actor",
                    )
                paths = tuple(dict.fromkeys((session_request.cwd, *session_request.declared_paths)))
                assert session_request.executable is not None
                executables = (session_request.executable,)
                arguments = session_request.args
                environments = tuple(sorted(session_request.environment))
                credentials = tuple(
                    sorted(
                        {
                            *(
                                item.locator
                                for item in session_request.credential_environment.values()
                                if isinstance(item, CredentialReference)
                            ),
                            *(item.locator for item in session_request.credential_references),
                        }
                    )
                )
                effects = tuple(sorted({*effects, *session_request.declared_effects}))
                network_destinations = tuple(
                    self._network_destination(value)
                    for value in session_request.network_destinations
                )
                uses_network = bool(network_destinations)
                assert session_request.deadline is not None
                timeout = self._remaining_seconds(session_request.deadline)
            elif normalized.command_type.startswith("session."):
                record = self._sessions.status(self._target_uuid(normalized))
                if record.owner != normalized.actor_id:
                    raise MishkanError(
                        ErrorCode.AUTHORITY_NOT_GRANTED,
                        "session command actor does not own the target session",
                    )
                external_resources = (f"session:{record.session_id}",)
                if normalized.command_type == "session.write":
                    encoded = normalized.payload["content_base64"]
                    if not isinstance(encoded, str):
                        raise ValueError("session input must be base64 text")
                    content = base64.b64decode(encoded, validate=True)
                    sessions = self._config.sessions
                    assert sessions is not None
                    limit = sessions.profiles[record.profile].max_input_bytes
                    if len(content) > limit:
                        raise ValueError("session input exceeds its configured bound")
                    arguments = (content.decode("utf-8", errors="surrogateescape"),)
                    declared = self._string_tuple(normalized.payload["declared_effects"])
                    destinations = self._string_tuple(normalized.payload["network_destinations"])
                    effects = tuple(sorted({*effects, *declared}))
                    network_destinations = tuple(
                        self._network_destination(value) for value in destinations
                    )
                    uses_network = bool(network_destinations)
            elif normalized.command_type == "mcp.connection.connect":
                connection = self._mcp_connection(normalized)
                credentials = tuple(sorted(item.locator for item in connection.credential_refs))
                timeout = math.ceil(connection.connect_timeout_seconds)
                external_resources = (f"mcp-connection:{normalized.target_id}",)
                if connection.transport is McpTransport.STDIO:
                    assert connection.command is not None
                    executables = (connection.command,)
                    arguments = connection.arguments
                    environments = tuple(sorted(connection.inherit_environment))
                else:
                    assert connection.endpoint is not None
                    network_destinations = (self._network_destination(str(connection.endpoint)),)
            elif normalized.command_type.startswith("mcp.call."):
                request_id = self._target_uuid(normalized)
                if self._mcp_calls is None:
                    raise MishkanError(ErrorCode.MCP, "MCP mediation is not configured")
                connection_id = self._mcp_calls.call_connection_id(request_id)
                connection = self._configured_mcp_connection(connection_id)
                credentials = tuple(sorted(item.locator for item in connection.credential_refs))
                timeout = math.ceil(connection.call_timeout_seconds)
                external_resources = (
                    f"mcp-call:{request_id}",
                    f"mcp-connection:{connection_id}",
                )
                if connection.transport is McpTransport.STDIO:
                    assert connection.command is not None
                    executables = (connection.command,)
                else:
                    assert connection.endpoint is not None
                    network_destinations = (self._network_destination(str(connection.endpoint)),)
            elif normalized.command_type == "knowledge.query":
                knowledge_query = KnowledgeQuery.model_validate(normalized.payload["query"])
                if normalized.target_id != str(knowledge_query.query_id):
                    raise ValueError("knowledge query target differs from its identity")
                knowledge = self._config.knowledge
                if knowledge is None:
                    raise MishkanError(
                        ErrorCode.REQUIRED_DEPENDENCY,
                        "knowledge capability is not configured",
                    )
                configured = list(
                    knowledge.selection_order.get(knowledge_query.knowledge_class, ())
                )
                source_ids = (
                    list(knowledge_query.preferred_sources)
                    if knowledge_query.preferred_sources
                    else [item for item in configured if knowledge.sources[item].enabled]
                )
                if (
                    knowledge.literal_fallback
                    and knowledge_query.knowledge_class
                    in {KnowledgeClass.SEMANTIC, KnowledgeClass.STRUCTURAL}
                    and knowledge_query.scope.repository_id is not None
                ):
                    source_ids.extend(
                        item
                        for item in knowledge.selection_order.get(KnowledgeClass.LITERAL, ())
                        if item not in source_ids and knowledge.sources[item].enabled
                    )
                selected_sources = []
                for source_id in source_ids:
                    source = knowledge.sources.get(source_id)
                    if source is None or not source.enabled:
                        raise ValueError("knowledge query references an unavailable source")
                    compatible = source.knowledge_class is knowledge_query.knowledge_class
                    literal_fallback = (
                        source.knowledge_class is KnowledgeClass.LITERAL
                        and knowledge.literal_fallback
                        and knowledge_query.knowledge_class
                        in {KnowledgeClass.SEMANTIC, KnowledgeClass.STRUCTURAL}
                        and knowledge_query.scope.repository_id is not None
                    )
                    if not compatible and not literal_fallback:
                        raise ValueError("knowledge query references an incompatible source")
                    selected_sources.append((source_id, source))
                external_resources = tuple(
                    dict.fromkeys(
                        (
                            f"knowledge-project:{knowledge_query.scope.project_id}",
                            f"knowledge-class:{knowledge_query.knowledge_class.value}",
                            *(f"knowledge-source:{item}" for item, _ in selected_sources),
                        )
                    )
                )
                source_scopes = tuple(
                    self._knowledge_source_scope(source_id) for source_id, _ in selected_sources
                )
                credentials = tuple(
                    sorted({item for scoped, _, _ in source_scopes for item in scoped})
                )
                network_destinations = tuple(
                    sorted({item for _, scoped, _ in source_scopes for item in scoped})
                )
                uses_network = bool(network_destinations) or any(
                    source.mcp_connection is not None for _, source in selected_sources
                )
                if knowledge_query.scope.repository_id is not None:
                    paths = (str(self._workspace),)
                    external_resources = (
                        *external_resources,
                        f"repository:{knowledge_query.scope.repository_id}",
                        f"repository-revision:{knowledge_query.scope.repository_revision}",
                    )
                timeout = math.ceil(knowledge_query.deadline_seconds)
            elif normalized.command_type in {"knowledge.ingest", "knowledge.refresh"}:
                knowledge_request = (
                    KnowledgeIngestRequest.model_validate(normalized.payload["request"])
                    if normalized.command_type == "knowledge.ingest"
                    else KnowledgeRefreshRequest.model_validate(normalized.payload["request"])
                )
                if normalized.target_id != str(knowledge_request.operation_id):
                    raise ValueError("knowledge operation target differs from its identity")
                if knowledge_request.requested_by != normalized.actor_id:
                    raise MishkanError(
                        ErrorCode.AUTHORITY_NOT_GRANTED,
                        "knowledge operation requester must match the authenticated actor",
                    )
                if isinstance(knowledge_request, KnowledgeIngestRequest):
                    knowledge_ingest = knowledge_request
                    external_resources = (
                        f"knowledge-project:{knowledge_request.project_id}",
                        f"knowledge-source:{knowledge_request.source_id}",
                        f"knowledge-corpus:{knowledge_request.corpus_id}",
                        f"artifact:{knowledge_request.content_reference}",
                    )
                else:
                    knowledge_refresh = knowledge_request
                    external_resources = (
                        f"knowledge-project:{knowledge_request.project_id}",
                        f"knowledge-source:{knowledge_request.source_id}",
                        f"knowledge-corpus:{knowledge_request.corpus_id}",
                    )
                (
                    credentials,
                    network_destinations,
                    timeout,
                ) = self._knowledge_source_scope(knowledge_request.source_id)
            elif normalized.command_type == "knowledge.memory.capture":
                knowledge_memory_capture = KnowledgeMemoryCaptureRequest.model_validate(
                    normalized.payload["request"]
                )
                if normalized.target_id != str(knowledge_memory_capture.operation_id):
                    raise ValueError("knowledge memory target differs from its identity")
                if knowledge_memory_capture.requested_by != normalized.actor_id:
                    raise MishkanError(
                        ErrorCode.AUTHORITY_NOT_GRANTED,
                        "knowledge memory requester must match the authenticated actor",
                    )
                proposal = knowledge_memory_capture.proposal
                external_resources = (
                    f"knowledge-project:{proposal.project_id}",
                    f"knowledge-source:{knowledge_memory_capture.source_id}",
                    f"result:{proposal.accepted_result_id}",
                    *(f"artifact:{item}" for item in proposal.evidence_references),
                )
                (
                    credentials,
                    network_destinations,
                    timeout,
                ) = self._knowledge_source_scope(knowledge_memory_capture.source_id)
            elif normalized.command_type == "knowledge.operation.reconcile":
                knowledge_reconcile = KnowledgeOperationReconcileRequest.model_validate(
                    normalized.payload["request"]
                )
                if normalized.target_id != str(knowledge_reconcile.operation_id):
                    raise ValueError("knowledge reconcile target differs from its identity")
                if knowledge_reconcile.requested_by != normalized.actor_id:
                    raise MishkanError(
                        ErrorCode.AUTHORITY_NOT_GRANTED,
                        "knowledge reconcile requester must match the authenticated actor",
                    )
                credentials, network_destinations, timeout, operation = (
                    self._knowledge_operation_scope(knowledge_reconcile.operation_id)
                )
                external_resources = (
                    f"knowledge-operation:{normalized.target_id}",
                    f"knowledge-project:{operation.project_id}",
                    f"knowledge-source:{operation.source_id}",
                )
            elif normalized.command_type == "knowledge.operation.cancel":
                operation_id = self._target_uuid(normalized)
                credentials, network_destinations, timeout, operation = (
                    self._knowledge_operation_scope(operation_id)
                )
                external_resources = (
                    f"knowledge-operation:{normalized.target_id}",
                    f"knowledge-project:{operation.project_id}",
                    f"knowledge-source:{operation.source_id}",
                )
            elif normalized.command_type == "knowledge.promotion.propose":
                knowledge_promotion = KnowledgePromotion.model_validate(
                    normalized.payload["proposal"]
                )
                if normalized.target_id != str(knowledge_promotion.promotion_id):
                    raise ValueError("knowledge promotion target differs from its identity")
                if knowledge_promotion.proposed_by != normalized.actor_id:
                    raise MishkanError(
                        ErrorCode.AUTHORITY_NOT_GRANTED,
                        "knowledge promotion proposer must match the authenticated actor",
                    )
                external_resources = (
                    f"knowledge-project:{knowledge_promotion.source_project_id}",
                    f"knowledge-item:{knowledge_promotion.item_id}",
                    f"knowledge-target-scope:{knowledge_promotion.target_scope}",
                    *(f"artifact:{item}" for item in knowledge_promotion.evidence_references),
                )
            elif normalized.command_type == "knowledge.promotion.decide":
                knowledge_promotion_decision = KnowledgePromotionDecision.model_validate(
                    normalized.payload["decision"]
                )
                if normalized.target_id != str(knowledge_promotion_decision.promotion_id):
                    raise ValueError("knowledge promotion decision target differs")
                if knowledge_promotion_decision.decided_by != normalized.actor_id:
                    raise MishkanError(
                        ErrorCode.AUTHORITY_NOT_GRANTED,
                        "knowledge promotion decider must match the authenticated actor",
                    )
                external_resources = (f"knowledge-promotion:{normalized.target_id}",)
                effects = tuple(
                    sorted(
                        {
                            *effects,
                            f"knowledge.promotion.{knowledge_promotion_decision.disposition.value}",
                        }
                    )
                )
            elif normalized.command_type.startswith("registry.entry."):
                if normalized.target_id is None:
                    raise ValueError("registry lifecycle target identity is required")
                kind_value, separator, entry_identity = normalized.target_id.partition(":")
                if not separator or not entry_identity:
                    raise ValueError("registry target must be '<kind>:<identity>'")
                action = RegistryLifecycleAction(
                    normalized.command_type.removeprefix("registry.entry.")
                )
                entry_kind = RegistryEntryKind(str(normalized.payload["entry_kind"]))
                if kind_value != entry_kind.value:
                    raise ValueError("registry target kind differs from its typed payload")
                registry_mutation = RegistryMutation(
                    entry_kind=entry_kind,
                    identity=entry_identity,
                    action=action,
                    definition=normalized.payload.get("definition"),
                    precedence=normalized.payload.get("precedence"),
                )
                external_resources = (
                    f"registry:{registry_mutation.entry_kind.value}:{registry_mutation.identity}",
                )
            elif normalized.command_type == "skill.version.register":
                skill_version = SkillVersionRecord.model_validate(normalized.payload["record"])
                if normalized.target_id != str(skill_version.id):
                    raise ValueError("skill target differs from its immutable version identity")
                external_resources = (
                    f"skill:{skill_version.skill_name}@{skill_version.skill_version}",
                    f"artifact-collection:{skill_version.package_collection_id}",
                )
                effects = tuple(sorted({*effects, f"skill.{skill_version.mutation_action.value}"}))
            elif normalized.command_type in {
                "skill.version.decide",
                "skill.version.archive",
                "skill.version.delete",
                "skill.version.restore",
                "skill.version.reset",
            }:
                skill_decision = SkillLifecycleDecision.model_validate(
                    normalized.payload["decision"]
                )
                if normalized.target_id != str(skill_decision.version_id):
                    raise ValueError("skill decision targets another immutable version")
                effects = tuple(
                    sorted(
                        {
                            *effects,
                            f"skill.disposition.{skill_decision.disposition.value}",
                            *(
                                ("skill.quarantine.override",)
                                if skill_decision.quarantine_override
                                else ()
                            ),
                        }
                    )
                )
                external_resources = (f"skill-version:{skill_decision.version_id}",)
            elif normalized.command_type in {"skill.version.pin", "skill.version.unpin"}:
                external_resources = (f"skill-version:{self._target_uuid(normalized)}",)
            elif normalized.command_type == "skill.usage.record":
                skill_usage = SkillUsageRecord.model_validate(normalized.payload["record"])
                if normalized.target_id != str(skill_usage.id):
                    raise ValueError("skill usage target differs from its evidence identity")
                external_resources = (
                    f"skill:{skill_usage.requested_skill}",
                    f"task:{skill_usage.task_id}",
                )
            elif normalized.command_type == "skill.invoke":
                skill_invocation = SkillInvocationRequest.model_validate(
                    normalized.payload["request"]
                )
                if normalized.target_id != skill_invocation.context.task_id:
                    raise ValueError("skill invocation target differs from its task context")
                if skill_invocation.context.consuming_identity != normalized.actor_id:
                    raise MishkanError(
                        ErrorCode.AUTHORITY_NOT_GRANTED,
                        "skill invocation identity must match the authenticated command actor",
                    )
                requested = (
                    f"skill:{skill_invocation.requested_name}"
                    if skill_invocation.requested_name is not None
                    else (
                        f"skill-bundle:{skill_invocation.bundle_id}"
                        if skill_invocation.bundle_id is not None
                        else "skill-selection:automatic"
                    )
                )
                external_resources = (requested, f"task:{skill_invocation.context.task_id}")
            elif normalized.command_type == "skill.learn":
                skill_learning = SkillLearningRequest.model_validate(normalized.payload["request"])
                if normalized.target_id != str(skill_learning.request_id):
                    raise ValueError("skill learning target differs from its request identity")
                if skill_learning.consuming_identity != normalized.actor_id:
                    raise MishkanError(
                        ErrorCode.AUTHORITY_NOT_GRANTED,
                        "skill learning identity must match the authenticated command actor",
                    )
                resources = [
                    f"skill-learning:{skill_learning.request_id}",
                    f"task:{skill_learning.task_id}",
                ]
                for learning_source in skill_learning.sources:
                    resources.append(
                        f"learning-source:{learning_source.kind.value}:{learning_source.locator}"
                    )
                external_resources = tuple(resources)
            elif normalized.command_type == "environment.observe":
                environment_observation = EnvironmentObservationRequest.model_validate(
                    normalized.payload["request"]
                )
                if normalized.target_id != str(environment_observation.observation_id):
                    raise ValueError("environment observation target differs from its request")
                if environment_observation.actor_identity != normalized.actor_id:
                    raise MishkanError(
                        ErrorCode.AUTHORITY_NOT_GRANTED,
                        "environment observation identity must match the authenticated actor",
                    )
                paths = (".",)
                external_resources = (
                    f"environment-context:{environment_observation.context_id}",
                    f"execution-location:{environment_observation.execution_location}",
                )
            elif normalized.command_type == "environment.resolve":
                environment_binding = EnvironmentBindingRequest.model_validate(
                    normalized.payload["request"]
                )
                if normalized.target_id != str(environment_binding.request_id):
                    raise ValueError("environment binding target differs from its request")
                if environment_binding.owner_identity != normalized.actor_id:
                    raise MishkanError(
                        ErrorCode.AUTHORITY_NOT_GRANTED,
                        "environment binding owner must match the authenticated actor",
                    )
                external_resources = tuple(
                    dict.fromkeys(
                        (
                            f"environment-context:{environment_binding.context_id}",
                            f"environment-observation:{environment_binding.observation_id}",
                            *(
                                f"engine:{engine_id}"
                                for engine_id in environment_binding.authorized_engine_ids
                            ),
                            *(
                                f"descriptor-format:{format_name}"
                                for format_name in environment_binding.allowed_descriptor_formats
                            ),
                        )
                    )
                )
            elif normalized.command_type == "environment.descriptor.validate":
                environment_descriptor_set = EnvironmentDescriptorSet.model_validate(
                    normalized.payload["descriptor_set"]
                )
                if normalized.target_id != str(environment_descriptor_set.descriptor_set_id):
                    raise ValueError("environment descriptor target differs from its identity")
                paths = tuple(member.logical_path for member in environment_descriptor_set.members)
                external_resources = (
                    f"environment-binding:{environment_descriptor_set.binding_id}",
                    *(member.artifact_reference for member in environment_descriptor_set.members),
                )
            elif normalized.command_type == "environment.descriptor.change.plan":
                environment_descriptor_change = EnvironmentDescriptorChangeRequest.model_validate(
                    normalized.payload["request"]
                )
                if normalized.target_id != str(environment_descriptor_change.request_id):
                    raise ValueError("descriptor change target differs from its request")
                if environment_descriptor_change.owner_identity != normalized.actor_id:
                    raise MishkanError(
                        ErrorCode.AUTHORITY_NOT_GRANTED,
                        "descriptor change owner must match the authenticated actor",
                    )
                external_resources = (
                    f"environment-descriptor-set:{environment_descriptor_change.descriptor_set_id}",
                )
            elif normalized.command_type == "environment.operation.plan":
                environment_operation = EnvironmentOperationRequest.model_validate(
                    normalized.payload["request"]
                )
                if normalized.target_id != str(environment_operation.operation_id):
                    raise ValueError("environment operation target differs from its identity")
                if environment_operation.owner_identity != normalized.actor_id:
                    raise MishkanError(
                        ErrorCode.AUTHORITY_NOT_GRANTED,
                        "environment operation owner must match the authenticated actor",
                    )
                paths = (
                    (environment_operation.descriptor_path,)
                    if environment_operation.descriptor_path is not None
                    else ()
                )
                network_destinations = environment_operation.network_destinations
                environments = tuple(
                    sorted(
                        f"{name}={value}"
                        for name, value in environment_operation.environment.items()
                    )
                )
                credentials = tuple(
                    dict.fromkeys(
                        (
                            *(item.locator for item in environment_operation.credential_references),
                            *(
                                item.locator
                                for item in environment_operation.credential_environment.values()
                            ),
                        )
                    )
                )
                external_resources = (
                    f"environment-binding:{environment_operation.binding_id}",
                    f"environment-adapter:{environment_operation.adapter_id}",
                    f"environment-operation:{environment_operation.operation.value}",
                )
            elif normalized.command_type == "environment.command.plan":
                engineering_command = EngineeringCommandRequest.model_validate(
                    normalized.payload["request"]
                )
                if normalized.target_id != str(engineering_command.request_id):
                    raise ValueError("engineering command target differs from its request")
                if engineering_command.owner_identity != normalized.actor_id:
                    raise MishkanError(
                        ErrorCode.AUTHORITY_NOT_GRANTED,
                        "engineering command owner must match the authenticated actor",
                    )
                external_resources = (
                    f"environment-observation:{engineering_command.observation_id}",
                    f"engineering-pack:{engineering_command.pack_id}",
                    f"engineering-action:{engineering_command.action}",
                )
            elif normalized.command_type == "environment.attempt.settle":
                environment_operation_plan = EnvironmentOperationPlan.model_validate(
                    normalized.payload["operation_plan"]
                )
                session_id = UUID(str(normalized.payload["session_id"]))
                if normalized.target_id != str(environment_operation_plan.request.operation_id):
                    raise ValueError("environment attempt target differs from its operation")
                if session_id != environment_operation_plan.execution.execution_id:
                    raise ValueError("environment attempt session differs from its operation")
                if environment_operation_plan.request.owner_identity != normalized.actor_id:
                    raise MishkanError(
                        ErrorCode.AUTHORITY_NOT_GRANTED,
                        "environment attempt owner must match the authenticated actor",
                    )
                external_resources = (
                    f"environment-binding:{environment_operation_plan.request.binding_id}",
                    f"session:{session_id}",
                )
            elif normalized.command_type == "environment.verification.record":
                environment_verification = EnvironmentVerificationRequest.model_validate(
                    normalized.payload["request"]
                )
                if normalized.target_id != str(environment_verification.verification_id):
                    raise ValueError("environment verification target differs from its request")
                if environment_verification.owner_identity != normalized.actor_id:
                    raise MishkanError(
                        ErrorCode.AUTHORITY_NOT_GRANTED,
                        "environment verification owner must match the authenticated actor",
                    )
                external_resources = (
                    f"environment-binding:{environment_verification.binding_id}",
                    *(
                        f"environment-attempt:{attempt_id}"
                        for values in environment_verification.check_attempt_ids.values()
                        for attempt_id in values
                    ),
                )
            elif normalized.command_type == "environment.binding.invalidate":
                environment_invalidation = EnvironmentInvalidation.model_validate(
                    normalized.payload["invalidation"]
                )
                if normalized.target_id != str(environment_invalidation.binding_id):
                    raise ValueError("environment invalidation target differs from its binding")
                if environment_invalidation.owner_identity != normalized.actor_id:
                    raise MishkanError(
                        ErrorCode.AUTHORITY_NOT_GRANTED,
                        "environment invalidation owner must match the authenticated actor",
                    )
                external_resources = (
                    f"environment-binding:{environment_invalidation.binding_id}",
                    *(f"task:{task_id}" for task_id in environment_invalidation.affected_task_ids),
                )
            elif normalized.command_type == "telemetry.evaluation.import":
                telemetry_evaluation = LangSmithFeedbackImportRequest.model_validate(
                    normalized.payload["request"]
                )
                if normalized.target_id != str(telemetry_evaluation.import_id):
                    raise ValueError("telemetry evaluation target differs from its import request")
                if telemetry_evaluation.owner_identity != normalized.actor_id:
                    raise MishkanError(
                        ErrorCode.AUTHORITY_NOT_GRANTED,
                        "telemetry evaluation owner must match the authenticated actor",
                    )
                external_resources = (
                    f"langsmith-project:{telemetry_evaluation.project_name}",
                    f"langsmith-run:{telemetry_evaluation.traced_run_id}",
                    f"langsmith-feedback:{telemetry_evaluation.external_feedback_id}",
                )
            elif normalized.command_type == "context.recommend":
                context_recommendation = ContextualRecommendationRequest.model_validate(
                    normalized.payload["request"]
                )
                if normalized.target_id != str(context_recommendation.request_id):
                    raise ValueError("context recommendation target differs from its request")
                if context_recommendation.owner_identity != normalized.actor_id:
                    raise MishkanError(
                        ErrorCode.AUTHORITY_NOT_GRANTED,
                        "context recommendation owner must match the authenticated actor",
                    )
                external_resources = (
                    f"context:{context_recommendation.context_id}",
                    *(f"candidate:{item}" for item in context_recommendation.candidate_ids),
                    *(f"evidence:{item}" for item in context_recommendation.project_evidence),
                )
            elif normalized.command_type == "mission.create":
                mission_record = MissionRecord.model_validate(normalized.payload["record"])
                if normalized.target_id != str(mission_record.mission_id):
                    raise ValueError("mission target differs from its immutable identity")
                if normalized.expected_revision not in {None, 0} or mission_record.revision != 0:
                    raise ValueError("new mission must begin at revision zero")
                external_resources = (
                    f"organization:{mission_record.organization_id}"
                    f"@{mission_record.organization_version}",
                    f"mission-origin:{mission_record.origin.origin_id}",
                )
            elif normalized.command_type == "mission.brief.record":
                mission_brief = MissionBrief.model_validate(normalized.payload["brief"])
                if normalized.target_id != str(mission_brief.mission_id):
                    raise ValueError("Mission Brief target differs from its mission identity")
                if normalized.expected_revision is None:
                    raise ValueError("Mission Brief command requires expected mission revision")
                external_resources = (
                    f"mission:{mission_brief.mission_id}",
                    f"organization:{mission_brief.organization_id}"
                    f"@{mission_brief.organization_version}",
                )
            elif normalized.command_type == "mission.crew.record":
                mission_crew = MissionCrewRevision.model_validate(normalized.payload["crew"])
                if normalized.target_id != str(mission_crew.mission_id):
                    raise ValueError("Mission Crew target differs from its mission identity")
                if normalized.expected_revision is None:
                    raise ValueError("Mission Crew command requires expected mission revision")
                external_resources = (
                    f"mission:{mission_crew.mission_id}",
                    f"mission-brief:{mission_crew.mission_id}:{mission_crew.brief_version}",
                    *(f"identity:{member.identity_id}" for member in mission_crew.members),
                )
            elif normalized.command_type == "conversation.create":
                conversation_channel = ConversationChannel.model_validate(
                    normalized.payload["channel"]
                )
                if normalized.target_id != str(conversation_channel.conversation_id):
                    raise ValueError("conversation target differs from its immutable identity")
                if normalized.expected_revision not in {None, 0}:
                    raise ValueError("new conversation must begin at revision zero")
                external_resources = tuple(
                    dict.fromkeys(
                        (
                            f"conversation-class:{conversation_channel.channel_class.value}",
                            *(f"identity:{item}" for item in conversation_channel.participants),
                            *(
                                (f"mission:{conversation_channel.mission_id}",)
                                if conversation_channel.mission_id is not None
                                else ()
                            ),
                            *(
                                (f"branch:{conversation_channel.branch_id}",)
                                if conversation_channel.branch_id is not None
                                else ()
                            ),
                        )
                    )
                )
            elif normalized.command_type == "conversation.message.post":
                conversation_message = ConversationMessage.model_validate(
                    normalized.payload["message"]
                )
                if normalized.target_id != str(conversation_message.message_id):
                    raise ValueError("message target differs from its immutable identity")
                external_resources = (
                    f"conversation:{conversation_message.conversation_id}",
                    f"identity:{conversation_message.author_identity}",
                    f"message-purpose:{conversation_message.purpose.value}",
                    *(
                        (f"message:{conversation_message.reply_to_message_id}",)
                        if conversation_message.reply_to_message_id is not None
                        else ()
                    ),
                )
            elif normalized.command_type == "mission.decision.record":
                mission_decision = MissionDecision.model_validate(normalized.payload["decision"])
                if normalized.target_id != str(mission_decision.decision_id):
                    raise ValueError("decision target differs from its immutable identity")
                if mission_decision.actor_id != normalized.actor_id:
                    raise MishkanError(
                        ErrorCode.AUTHORITY_NOT_GRANTED,
                        "decision actor must match the authenticated command actor",
                    )
                external_resources = (
                    f"mission:{mission_decision.mission_id}",
                    f"conversation:{mission_decision.conversation_id}",
                    f"authority:{mission_decision.authority_reference}",
                    *(f"evidence:{item}" for item in mission_decision.evidence_references),
                    *(
                        (f"decision:{mission_decision.supersedes_decision_id}",)
                        if mission_decision.supersedes_decision_id is not None
                        else ()
                    ),
                )
                if mission_decision.decision_status is not None:
                    effects = tuple(
                        sorted(
                            {
                                *effects,
                                f"mission.decision.{mission_decision.decision_status.value}",
                                *(
                                    ("mission.decision.authority-change",)
                                    if mission_decision.changes_durable_authority
                                    else ()
                                ),
                            }
                        )
                    )
            elif normalized.command_type == "mission.escalation.open":
                mission_escalation = MissionEscalation.model_validate(
                    normalized.payload["escalation"]
                )
                if normalized.target_id != str(mission_escalation.escalation_id):
                    raise ValueError("escalation target differs from its immutable identity")
                external_resources = (
                    f"mission:{mission_escalation.mission_id}",
                    f"conversation:{mission_escalation.conversation_id}",
                    *(f"evidence:{item}" for item in mission_escalation.evidence_references),
                )
            elif normalized.command_type == "mission.intervention.apply":
                mission_intervention = MissionIntervention.model_validate(
                    normalized.payload["intervention"]
                )
                if normalized.target_id != str(mission_intervention.mission_id):
                    raise ValueError("intervention target differs from its mission identity")
                if normalized.expected_revision is None:
                    raise ValueError("mission intervention requires expected mission revision")
                external_resources = tuple(
                    dict.fromkeys(
                        (
                            f"mission:{mission_intervention.mission_id}",
                            f"conversation:{mission_intervention.conversation_id}",
                            f"authority:{mission_intervention.authority_reference}",
                            (
                                f"{mission_intervention.target_kind.value}:"
                                f"{mission_intervention.target_id}"
                            ),
                            *(
                                f"evidence:{item}"
                                for item in mission_intervention.evidence_references
                            ),
                            *(
                                (f"escalation:{mission_intervention.escalation_id}",)
                                if mission_intervention.escalation_id is not None
                                else ()
                            ),
                        )
                    )
                )
                effects = tuple(
                    sorted(
                        {
                            *effects,
                            f"mission.intervention.{mission_intervention.kind.value}",
                        }
                    )
                )
            elif normalized.command_type == "mission.assignment.record":
                mission_assignment = MissionTaskAssignment.model_validate(
                    normalized.payload["assignment"]
                )
                if normalized.target_id != str(mission_assignment.assignment_id):
                    raise ValueError("assignment target differs from its immutable identity")
                if (
                    mission_assignment.change is not None
                    and mission_assignment.change.requested_by_identity != normalized.actor_id
                ):
                    raise MishkanError(
                        ErrorCode.AUTHORITY_NOT_GRANTED,
                        "assignment change requester must match the authenticated command actor",
                    )
                external_resources = (
                    f"mission:{mission_assignment.mission_id}",
                    f"task:{mission_assignment.task_id}",
                    f"identity:{mission_assignment.accountable_owner}",
                    *(f"identity:{item}" for item in mission_assignment.contributors),
                    *(f"tool:{item}" for item in mission_assignment.exact_tools),
                    *(f"path:{item}" for item in mission_assignment.path_scopes),
                    *(
                        (
                            f"assignment:{mission_assignment.change.prior_assignment_id}",
                            f"authority:{mission_assignment.change.authority_reference}",
                            *(
                                f"evidence:{item}"
                                for item in mission_assignment.change.evidence_references
                            ),
                        )
                        if mission_assignment.change is not None
                        else ()
                    ),
                )
                effects = tuple(
                    sorted(
                        {
                            *effects,
                            (
                                "mission.assignment.initial"
                                if mission_assignment.change is None
                                else "mission.assignment."
                                f"{mission_assignment.change.change_kind.value}"
                            ),
                        }
                    )
                )
            elif normalized.command_type == "mission.run-binding.record":
                mission_run_binding = MissionRunBinding.model_validate(
                    normalized.payload["binding"]
                )
                if normalized.target_id != str(mission_run_binding.binding_id):
                    raise ValueError("mission run binding target differs from its identity")
                if mission_run_binding.recorded_by != normalized.actor_id:
                    raise MishkanError(
                        ErrorCode.AUTHORITY_NOT_GRANTED,
                        "mission run binding recorder must match the authenticated actor",
                    )
                external_resources = tuple(
                    dict.fromkeys(
                        (
                            f"mission:{mission_run_binding.mission_id}",
                            f"run:{mission_run_binding.run_id}",
                            f"task:{mission_run_binding.mission_task_id}",
                            f"task:{mission_run_binding.execution_task_id}",
                            f"mission-run-binding:{mission_run_binding.binding_key}",
                            f"identity:{mission_run_binding.recorded_by}",
                            *(
                                f"mission-run-binding:{dependency}"
                                for dependency in mission_run_binding.depends_on_binding_keys
                            ),
                            *(f"evidence:{item}" for item in mission_run_binding.result_references),
                            *(
                                f"acceptance:{item}"
                                for item in mission_run_binding.acceptance_references
                            ),
                        )
                    )
                )
                effects = tuple(
                    sorted(
                        {
                            *effects,
                            f"mission.run-binding.{mission_run_binding.acceptance.value}",
                        }
                    )
                )
            elif normalized.command_type == "mission.run-report.record":
                mission_run_report = MissionRunReport.model_validate(normalized.payload["report"])
                if normalized.target_id != str(mission_run_report.report_id):
                    raise ValueError("mission run report target differs from its identity")
                external_resources = tuple(
                    dict.fromkeys(
                        (
                            f"mission:{mission_run_report.mission_id}",
                            f"run:{mission_run_report.run_id}",
                            f"identity:{mission_run_report.reporter_identity}",
                            *(
                                f"task:{item.mission_task_id}"
                                for item in mission_run_report.task_results
                            ),
                            *(
                                f"task:{item.execution_task_id}"
                                for item in mission_run_report.task_results
                            ),
                            *(
                                f"evidence:{reference}"
                                for item in mission_run_report.task_results
                                for reference in item.evidence_references
                            ),
                            *(
                                f"result:{reference}"
                                for item in mission_run_report.task_results
                                for reference in item.result_references
                            ),
                            *(
                                f"acceptance:{reference}"
                                for item in mission_run_report.task_results
                                for reference in item.acceptance_references
                            ),
                        )
                    )
                )
            elif normalized.command_type == "mission.task.claim":
                mission_task_claim = MissionTaskClaimRequest.model_validate(
                    normalized.payload["request"]
                )
                expected_target = f"{mission_task_claim.mission_id}:{mission_task_claim.task_id}"
                if normalized.target_id != expected_target:
                    raise ValueError("mission task claim target differs from its request")
                external_resources = (
                    f"mission:{mission_task_claim.mission_id}",
                    f"task:{mission_task_claim.task_id}",
                )
            elif normalized.command_type == "mission.transition":
                mission_transition = MissionTransition.model_validate(
                    normalized.payload["transition"]
                )
                if normalized.target_id != str(mission_transition.mission_id):
                    raise ValueError("transition target differs from its mission identity")
                if normalized.expected_revision is None:
                    raise ValueError("mission transition requires expected mission revision")
                external_resources = (
                    f"mission:{mission_transition.mission_id}",
                    *(f"evidence:{item}" for item in mission_transition.evidence_references),
                    *(
                        (f"decision:{mission_transition.decision_id}",)
                        if mission_transition.decision_id is not None
                        else ()
                    ),
                )
                effects = tuple(
                    sorted({*effects, f"mission.transition.{mission_transition.to_state.value}"})
                )
            elif normalized.command_type == "mission.governance.propose":
                mission_governance_request = MissionGovernanceRequest.model_validate(
                    normalized.payload["request"]
                )
                if normalized.target_id != str(mission_governance_request.request_id):
                    raise ValueError("governance request target differs from its identity")
                external_resources = (
                    f"mission:{mission_governance_request.mission_id}",
                    *(f"evidence:{item.reference}" for item in mission_governance_request.evidence),
                )
            elif normalized.command_type == "mission.environment.propose":
                mission_environment_planning_request = (
                    MissionEnvironmentPlanningRequest.model_validate(normalized.payload["request"])
                )
                if normalized.target_id != str(mission_environment_planning_request.request_id):
                    raise ValueError("environment planning target differs from its request")
                external_resources = tuple(
                    dict.fromkeys(
                        (
                            f"mission:{mission_environment_planning_request.mission_id}",
                            f"identity:{mission_environment_planning_request.owner_identity}",
                            f"task:{mission_environment_planning_request.planning_task_id}",
                            *(
                                f"environment-context:{context.context_id}"
                                for context in mission_environment_planning_request.contexts
                            ),
                            *(
                                f"environment-observation:{context.observation_id}"
                                for context in mission_environment_planning_request.contexts
                            ),
                        )
                    )
                )
                timeout = self._config.crewai.model_timeout_seconds
            elif normalized.command_type == "mission.environment.accept":
                mission_environment_plan = MissionEnvironmentPlan.model_validate(
                    normalized.payload["plan"]
                )
                if normalized.target_id != str(mission_environment_plan.mission_id):
                    raise ValueError("environment plan target differs from its mission identity")
                if normalized.expected_revision is None:
                    raise ValueError("environment plan acceptance requires mission revision")
                external_resources = tuple(
                    dict.fromkeys(
                        (
                            f"mission:{mission_environment_plan.mission_id}",
                            f"identity:{mission_environment_plan.owner_identity}",
                            *(
                                f"environment-context:{context.context_id}"
                                for context in mission_environment_plan.contexts
                            ),
                            *(
                                f"engine:{engine_id}"
                                for decision in mission_environment_plan.decisions
                                for engine_id in decision.eligible_engine_ids
                            ),
                            *(
                                f"decision:{decision.consequential_decision_id}"
                                for decision in mission_environment_plan.decisions
                                if decision.consequential_decision_id is not None
                            ),
                        )
                    )
                )
            elif normalized.command_type == "mission.environment.resolve":
                if normalized.target_id is None:
                    raise ValueError("environment plan resolution requires a plan identity")
                context_id = str(normalized.payload["context_id"])
                if not context_id or len(context_id) > 256:
                    raise ValueError("environment plan context identity is invalid")
                external_resources = (
                    f"mission-environment-plan:{normalized.target_id}",
                    f"environment-context:{context_id}",
                )
            elif normalized.command_type == "organization.evidence.record":
                professional_evidence = ProfessionalEvidenceRecord.model_validate(
                    normalized.payload["evidence"]
                )
                if normalized.target_id != str(professional_evidence.evidence_id):
                    raise ValueError("professional evidence target differs from its identity")
                if professional_evidence.recorded_by != normalized.actor_id:
                    raise MishkanError(
                        ErrorCode.AUTHORITY_NOT_GRANTED,
                        "professional evidence recorder must match the authenticated actor",
                    )
                external_resources = (
                    f"identity:{professional_evidence.identity_id}",
                    f"identity:{professional_evidence.evaluator_identity}",
                    f"professional-subject:{professional_evidence.subject}",
                    *(
                        f"evidence:{reference}"
                        for reference in professional_evidence.source_references
                    ),
                    *(
                        f"evaluation:{reference}"
                        for reference in professional_evidence.evaluation_references
                    ),
                )
                effects = tuple(
                    sorted(
                        {
                            *effects,
                            f"organization.evidence.{professional_evidence.outcome.value}",
                        }
                    )
                )
            elif normalized.command_type == "organization.promotion.decide":
                professional_promotion_request = ProfessionalPromotionRequest.model_validate(
                    normalized.payload["request"]
                )
                professional_promotion_disposition = ProfessionalPromotionDisposition(
                    normalized.payload["disposition"]
                )
                if normalized.target_id != str(professional_promotion_request.request_id):
                    raise ValueError("professional promotion target differs from its request")
                if professional_promotion_request.requested_by != normalized.actor_id:
                    raise MishkanError(
                        ErrorCode.AUTHORITY_NOT_GRANTED,
                        "professional promotion requester must match the authenticated actor",
                    )
                external_resources = (
                    f"identity:{professional_promotion_request.identity_id}",
                    f"professional-subject:{professional_promotion_request.subject}",
                    *(
                        f"professional-evidence:{evidence_id}"
                        for evidence_id in professional_promotion_request.supporting_evidence_ids
                    ),
                )
                effects = tuple(
                    sorted(
                        {
                            *effects,
                            f"organization.promotion.{professional_promotion_disposition.value}",
                        }
                    )
                )
            elif normalized.target_id is not None:
                external_resources = (f"{normalized.target_type}:{normalized.target_id}",)
        except (KeyError, TypeError, ValueError, ValidationError) as exc:
            raise MishkanError(
                ErrorCode.OUTPUT_CONTRACT,
                "application command payload does not match its authorization contract",
                details={"command_type": normalized.command_type},
            ) from exc

        request = AuthorizationRequest(
            plan_fingerprint=normalized.fingerprint,
            identity=normalized.actor_id,
            objective_class="application-command",
            repository=str(self._workspace),
            outcome=normalized.command_type,
            role="application-client",
            capability=semantics.capability,
            effect_class=semantics.effect_class,
            effects=effects,
            paths=paths,
            executables=executables,
            arguments=arguments,
            network_destinations=network_destinations,
            remotes=remotes,
            branches=branches,
            environments=environments,
            credentials=credentials,
            external_resources=external_resources,
            resources=ResourceRequest(
                timeout_seconds=max(1, min(timeout, 86_400)),
                network=uses_network,
            ),
        )
        decision = PolicyAuthority().evaluate(request, self._policy)
        return AuthorizedApplicationCommand(
            normalized,
            request,
            decision,
            session_request=session_request,
            git_request=git_request,
            registry_mutation=registry_mutation,
            skill_version=skill_version,
            skill_decision=skill_decision,
            skill_usage=skill_usage,
            skill_invocation=skill_invocation,
            skill_learning=skill_learning,
            environment_observation=environment_observation,
            environment_binding=environment_binding,
            environment_descriptor_set=environment_descriptor_set,
            environment_descriptor_change=environment_descriptor_change,
            environment_operation=environment_operation,
            engineering_command=engineering_command,
            environment_operation_plan=environment_operation_plan,
            environment_verification=environment_verification,
            environment_invalidation=environment_invalidation,
            telemetry_evaluation=telemetry_evaluation,
            context_recommendation=context_recommendation,
            mission_record=mission_record,
            mission_brief=mission_brief,
            mission_crew=mission_crew,
            conversation_channel=conversation_channel,
            conversation_message=conversation_message,
            mission_decision=mission_decision,
            mission_escalation=mission_escalation,
            mission_intervention=mission_intervention,
            mission_assignment=mission_assignment,
            mission_run_binding=mission_run_binding,
            mission_run_report=mission_run_report,
            mission_task_claim=mission_task_claim,
            mission_transition=mission_transition,
            mission_governance_request=mission_governance_request,
            mission_environment_planning_request=mission_environment_planning_request,
            mission_environment_plan=mission_environment_plan,
            professional_evidence=professional_evidence,
            professional_promotion_request=professional_promotion_request,
            professional_promotion_disposition=professional_promotion_disposition,
            prospective_run_request=prospective_run_request,
            repository_establishment_request=repository_establishment_request,
            knowledge_query=knowledge_query,
            knowledge_ingest=knowledge_ingest,
            knowledge_refresh=knowledge_refresh,
            knowledge_reconcile=knowledge_reconcile,
            knowledge_memory_capture=knowledge_memory_capture,
            knowledge_promotion=knowledge_promotion,
            knowledge_promotion_decision=knowledge_promotion_decision,
        )

    @staticmethod
    def is_allowed(authorized: AuthorizedApplicationCommand) -> bool:
        return authorized.decision.decision is Decision.ALLOW

    @staticmethod
    def _target_uuid(command: ApplicationCommand) -> UUID:
        if command.target_id is None:
            raise MishkanError(ErrorCode.OUTPUT_CONTRACT, "command requires a target identity")
        return UUID(command.target_id)

    @staticmethod
    def _validate_envelope(command: ApplicationCommand) -> None:
        target_type, identity_mode = _COMMAND_TARGETS[command.command_type]
        if command.target_type != target_type:
            raise MishkanError(
                ErrorCode.OUTPUT_CONTRACT,
                "application command target type differs from its public contract",
                details={"command_type": command.command_type, "target_type": target_type},
            )
        if identity_mode == "absent" and command.target_id is not None:
            raise MishkanError(ErrorCode.OUTPUT_CONTRACT, "command target identity must be absent")
        if identity_mode in {"required", "uuid"} and command.target_id is None:
            raise MishkanError(ErrorCode.OUTPUT_CONTRACT, "command target identity is required")
        if identity_mode == "uuid" and command.target_id is not None:
            try:
                UUID(command.target_id)
            except ValueError as exc:
                raise MishkanError(
                    ErrorCode.OUTPUT_CONTRACT, "command target identity must be a UUID"
                ) from exc
        required, optional = _COMMAND_PAYLOAD_FIELDS[command.command_type]
        received = frozenset(command.payload)
        if not required.issubset(received) or not received.issubset(required | optional):
            raise MishkanError(
                ErrorCode.OUTPUT_CONTRACT,
                "application command payload fields differ from its public contract",
                details={
                    "command_type": command.command_type,
                    "required_fields": sorted(required),
                    "optional_fields": sorted(optional),
                    "received_fields": sorted(received),
                },
            )

    @staticmethod
    def _change_scope(
        change_set: ChangeSet, base_effects: tuple[str, ...]
    ) -> tuple[tuple[str, ...], tuple[str, ...]]:
        paths = {
            value
            for operation in change_set.operations
            for value in (operation.path, operation.destination)
            if value is not None
        }
        return tuple(sorted(paths)), tuple(sorted({*base_effects, *change_set.declared_effects}))

    @staticmethod
    def _remaining_seconds(deadline: datetime) -> int:
        return max(1, math.ceil((deadline - utc_now()).total_seconds()))

    @staticmethod
    def _string_tuple(value: object) -> tuple[str, ...]:
        if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
            raise ValueError("application command value must be a list of non-empty strings")
        if len(value) != len(set(value)):
            raise ValueError("application command string values must be unique")
        return tuple(value)

    def _knowledge_source_scope(
        self, source_id: str
    ) -> tuple[tuple[str, ...], tuple[str, ...], int]:
        knowledge = self._config.knowledge
        if knowledge is None:
            raise MishkanError(
                ErrorCode.REQUIRED_DEPENDENCY,
                "knowledge capability is not configured",
            )
        source = knowledge.sources.get(source_id)
        if source is None or not source.enabled:
            raise MishkanError(ErrorCode.TOOL_UNAVAILABLE, "knowledge source is unavailable")
        credentials = {reference.locator for reference in source.credential_refs}
        destinations: set[str] = set()
        if source.endpoint is not None:
            destinations.add(self._network_destination(str(source.endpoint)))
        if source.mcp_connection is not None:
            connection = self._configured_mcp_connection(source.mcp_connection)
            credentials.update(reference.locator for reference in connection.credential_refs)
            if connection.endpoint is not None:
                destinations.add(self._network_destination(str(connection.endpoint)))
        return (
            tuple(sorted(credentials)),
            tuple(sorted(destinations)),
            math.ceil(source.operation_timeout_seconds),
        )

    def _knowledge_operation_scope(
        self, operation_id: UUID
    ) -> tuple[tuple[str, ...], tuple[str, ...], int, KnowledgeOperation]:
        if self._knowledge_operations is None:
            raise MishkanError(
                ErrorCode.REQUIRED_DEPENDENCY,
                "knowledge operation authority is unavailable",
            )
        operation = self._knowledge_operations.operation(operation_id)
        credentials, destinations, timeout = self._knowledge_source_scope(operation.source_id)
        return credentials, destinations, timeout, operation

    def _mcp_connection(self, command: ApplicationCommand) -> McpConnectionConfig:
        if command.payload or command.target_id is None:
            raise MishkanError(
                ErrorCode.OUTPUT_CONTRACT,
                "MCP connection command accepts only a configured target identity",
            )
        mcp: McpConfig | None = self._config.mcp
        if mcp is None:
            raise MishkanError(ErrorCode.MCP, "MCP mediation is not configured")
        connection = mcp.connections.get(command.target_id)
        if connection is None or not connection.enabled:
            raise MishkanError(ErrorCode.MCP, "MCP connection is not enabled")
        return connection

    def _configured_mcp_connection(self, connection_id: str) -> McpConnectionConfig:
        mcp = self._config.mcp
        if mcp is None:
            raise MishkanError(ErrorCode.MCP, "MCP mediation is not configured")
        connection = mcp.connections.get(connection_id)
        if connection is None or not connection.enabled:
            raise MishkanError(ErrorCode.MCP, "MCP connection is not enabled")
        return connection

    @staticmethod
    def _network_destination(endpoint: str) -> str:
        parsed = urlsplit(endpoint)
        if not parsed.scheme or parsed.hostname is None:
            raise ValueError("network destination must be an absolute URL")
        port = parsed.port
        if port is None:
            if parsed.scheme == "https":
                port = 443
            elif parsed.scheme == "http":
                port = 80
            else:
                raise ValueError("non-HTTP network destination requires an explicit port")
        return f"{parsed.scheme}://{parsed.hostname}:{port}"
