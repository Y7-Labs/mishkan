"""Synchronous Python SDK for the authoritative mishkand API."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import tempfile
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from urllib.parse import quote
from uuid import UUID

import httpx

from mishkan.application import (
    ApplicationCommand,
    CommandResult,
    ProspectiveRunRequest,
    RepositoryEstablishmentRequest,
    SnapshotEnvelope,
)
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
from mishkan.context import (
    CommunityCandidate,
    ContextualRecommendation,
    ContextualRecommendationRequest,
    EngineerProfile,
)
from mishkan.conversations import (
    ConversationChannel,
    ConversationMessage,
    EscalationState,
    MissionDecision,
    MissionEscalation,
    MissionIntervention,
)
from mishkan.crewai.mission_governance import (
    MissionGovernanceRequest,
    MissionGovernanceResult,
)
from mishkan.daemon.auth import TokenFile
from mishkan.edits import ChangeSetResult
from mishkan.environment import (
    DescriptorValidationResult,
    EngineeringCommandCandidate,
    EngineeringCommandPlan,
    EngineeringCommandRequest,
    EnvironmentAttempt,
    EnvironmentBinding,
    EnvironmentBindingRequest,
    EnvironmentDescriptorChangePlan,
    EnvironmentDescriptorChangeRequest,
    EnvironmentDescriptorSet,
    EnvironmentInvalidation,
    EnvironmentObservation,
    EnvironmentObservationRequest,
    EnvironmentOperationPlan,
    EnvironmentOperationRequest,
    EnvironmentVerification,
    EnvironmentVerificationRequest,
)
from mishkan.events import (
    EventEnvelope,
    EventPage,
    EventRetentionPlan,
    EventRetentionPolicy,
)
from mishkan.events import (
    EventHold as EventEvidenceHold,
)
from mishkan.execution import CursorRead, ExecutionSession
from mishkan.knowledge import (
    KnowledgeBundle,
    KnowledgeCorpus,
    KnowledgeIngestRequest,
    KnowledgeMemoryCaptureRequest,
    KnowledgeOperation,
    KnowledgeOperationReconcileRequest,
    KnowledgePromotion,
    KnowledgePromotionDecision,
    KnowledgeQuery,
    KnowledgeQueryRecord,
    KnowledgeRefreshRequest,
)
from mishkan.missions import (
    MissionBrief,
    MissionCompletionReadiness,
    MissionCrewRevision,
    MissionEnvironmentReadiness,
    MissionRecord,
    MissionRunBinding,
    MissionRunReport,
    MissionTaskAssignment,
    MissionTaskClaim,
    MissionTaskClaimRequest,
    MissionTaskEligibility,
    MissionTemplateDefinition,
    MissionTransition,
)
from mishkan.missions.environment import (
    MissionEnvironmentPlan,
    MissionEnvironmentPlanAcceptance,
    MissionEnvironmentPlanningRequest,
)
from mishkan.notifications import NotificationDelivery, NotificationPage, NotificationSeverity
from mishkan.organization import (
    OrganizationRosterDefinition,
    ProfessionalCompetenceState,
    ProfessionalEvidenceKind,
    ProfessionalEvidenceRecord,
    ProfessionalPromotionDecision,
    ProfessionalPromotionDisposition,
    ProfessionalPromotionRequest,
)
from mishkan.skills.models import (
    SkillCurationProposal,
    SkillInvocationEvidence,
    SkillInvocationRequest,
    SkillLearningRecord,
    SkillLearningRequest,
    SkillUpdateReport,
    SkillUsageSummary,
    SkillVersionRecord,
)
from mishkan.telemetry.models import (
    LangSmithFeedbackImportRequest,
    TelemetryEvaluationImportResult,
    TelemetryStatus,
)


class Mishkan:
    def __init__(
        self,
        base_url: str,
        *,
        token_file: Path,
        timeout_seconds: float = 120,
    ) -> None:
        self._token_file = TokenFile(token_file)
        self._client = httpx.Client(base_url=base_url.rstrip("/"), timeout=timeout_seconds)

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> Mishkan:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def health(self) -> dict[str, str]:
        response = self._client.get("/v1/health")
        response.raise_for_status()
        return dict(response.json())

    @property
    def principal_id(self) -> str:
        return self._token_file.read().principal_id

    @property
    def knowledge(self) -> KnowledgeClient:
        return KnowledgeClient(self)

    @property
    def memory(self) -> MemoryClient:
        return MemoryClient(self)

    @property
    def structure(self) -> StructureClient:
        return StructureClient(self)

    def command(self, command: ApplicationCommand) -> CommandResult:
        response = self._client.post(
            "/v1/commands",
            headers=self._headers(),
            json=command.model_dump(mode="json"),
        )
        response.raise_for_status()
        return CommandResult.model_validate(response.json())

    def put_artifact(
        self,
        content: bytes,
        *,
        media_type: str,
        provenance: ArtifactProvenance,
        chunk_bytes: int,
        sensitivity: str = "internal",
        retention: str = "run",
    ) -> ArtifactManifest:
        """Stream immutable bytes through the same versioned daemon commands."""
        if chunk_bytes < 1:
            raise ValueError("artifact chunk bound must be positive")
        opened = self.command(
            ApplicationCommand(
                command_type="artifact.upload.open",
                actor_id=self.principal_id,
                target_type="artifact_service",
                payload={
                    "expected_size": len(content),
                    "expected_digest": f"sha256:{hashlib.sha256(content).hexdigest()}",
                    "media_type": media_type,
                    "provenance": provenance.model_dump(mode="json"),
                    "sensitivity": sensitivity,
                    "retention": retention,
                },
            )
        )
        upload = UploadSession.model_validate(opened.payload)
        for offset in range(0, len(content), chunk_bytes):
            self.command(
                ApplicationCommand(
                    command_type="artifact.upload.chunk",
                    actor_id=self.principal_id,
                    target_type="artifact_upload",
                    target_id=str(upload.upload_id),
                    payload={
                        "offset": offset,
                        "content_base64": base64.b64encode(
                            content[offset : offset + chunk_bytes]
                        ).decode("ascii"),
                    },
                )
            )
        committed = self.command(
            ApplicationCommand(
                command_type="artifact.upload.commit",
                actor_id=self.principal_id,
                target_type="artifact_upload",
                target_id=str(upload.upload_id),
                payload={},
            )
        )
        return ArtifactManifest.model_validate(committed.payload)

    def snapshot(self) -> SnapshotEnvelope:
        response = self._client.get("/v1/snapshot", headers=self._headers())
        response.raise_for_status()
        return SnapshotEnvelope.model_validate(response.json())

    def organization(self) -> OrganizationRosterDefinition:
        response = self._client.get("/v1/organization", headers=self._headers())
        response.raise_for_status()
        return OrganizationRosterDefinition.model_validate(response.json())

    def organization_inspection(self, *, limit: int = 100) -> dict[str, object]:
        response = self._client.get(
            "/v1/organization/inspection",
            headers=self._headers(),
            params={"limit": limit},
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise TypeError("organization inspection response must be an object")
        return payload

    def organization_branch_inspection(
        self,
        branch_id: str,
        *,
        limit: int = 100,
    ) -> dict[str, object]:
        response = self._client.get(
            f"/v1/organization/branches/{quote(branch_id, safe='')}/inspection",
            headers=self._headers(),
            params={"limit": limit},
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise TypeError("organization branch inspection response must be an object")
        return payload

    def record_professional_evidence(
        self,
        evidence: ProfessionalEvidenceRecord,
    ) -> ProfessionalEvidenceRecord:
        result = self.command(
            ApplicationCommand(
                command_type="organization.evidence.record",
                actor_id=self.principal_id,
                target_type="professional_evidence",
                target_id=str(evidence.evidence_id),
                payload={"evidence": evidence.model_dump(mode="json")},
            )
        )
        return ProfessionalEvidenceRecord.model_validate(result.payload)

    def decide_professional_promotion(
        self,
        request: ProfessionalPromotionRequest,
        *,
        disposition: ProfessionalPromotionDisposition,
        reason: str,
    ) -> ProfessionalPromotionDecision:
        result = self.command(
            ApplicationCommand(
                command_type="organization.promotion.decide",
                actor_id=self.principal_id,
                target_type="professional_promotion_request",
                target_id=str(request.request_id),
                payload={
                    "request": request.model_dump(mode="json"),
                    "disposition": disposition.value,
                    "reason": reason,
                },
            )
        )
        return ProfessionalPromotionDecision.model_validate(result.payload)

    def professional_competence(
        self,
        identity_id: str,
        *,
        kind: ProfessionalEvidenceKind,
        subject: str,
    ) -> ProfessionalCompetenceState:
        identity = quote(identity_id, safe="")
        response = self._client.get(
            f"/v1/organization/profiles/{identity}/competence",
            headers=self._headers(),
            params={"kind": kind.value, "subject": subject},
        )
        response.raise_for_status()
        return ProfessionalCompetenceState.model_validate(response.json())

    def professional_evidence(
        self,
        identity_id: str,
        *,
        kind: ProfessionalEvidenceKind | None = None,
        subject: str | None = None,
        offset: int = 0,
        limit: int = 100,
    ) -> tuple[ProfessionalEvidenceRecord, ...]:
        identity = quote(identity_id, safe="")
        params: dict[str, str | int] = {"offset": offset, "limit": limit}
        if kind is not None:
            params["kind"] = kind.value
        if subject is not None:
            params["subject"] = subject
        response = self._client.get(
            f"/v1/organization/profiles/{identity}/evidence",
            headers=self._headers(),
            params=params,
        )
        response.raise_for_status()
        return tuple(ProfessionalEvidenceRecord.model_validate(item) for item in response.json())

    def professional_promotions(
        self,
        identity_id: str,
        *,
        kind: ProfessionalEvidenceKind | None = None,
        subject: str | None = None,
        offset: int = 0,
        limit: int = 100,
    ) -> tuple[ProfessionalPromotionDecision, ...]:
        identity = quote(identity_id, safe="")
        params: dict[str, str | int] = {"offset": offset, "limit": limit}
        if kind is not None:
            params["kind"] = kind.value
        if subject is not None:
            params["subject"] = subject
        response = self._client.get(
            f"/v1/organization/profiles/{identity}/promotions",
            headers=self._headers(),
            params=params,
        )
        response.raise_for_status()
        return tuple(ProfessionalPromotionDecision.model_validate(item) for item in response.json())

    def create_mission(self, record: MissionRecord) -> MissionRecord:
        result = self.command(
            ApplicationCommand(
                command_type="mission.create",
                actor_id=self.principal_id,
                target_type="mission",
                target_id=str(record.mission_id),
                expected_revision=0,
                payload={"record": record.model_dump(mode="json")},
            )
        )
        return MissionRecord.model_validate(result.payload)

    def propose_mission_governance(
        self, request: MissionGovernanceRequest
    ) -> MissionGovernanceResult:
        result = self.command(
            ApplicationCommand(
                command_type="mission.governance.propose",
                actor_id=self.principal_id,
                target_type="mission_governance_request",
                target_id=str(request.request_id),
                expected_revision=0,
                payload={"request": request.model_dump(mode="json")},
            )
        )
        return MissionGovernanceResult.model_validate(result.payload)

    def open_mission_governance_escalation(
        self,
        proposal: MissionGovernanceResult,
        conversation_id: str,
    ) -> MissionEscalation:
        return self.open_mission_escalation(proposal.escalation(UUID(conversation_id)))

    def propose_mission_environment(
        self,
        request: MissionEnvironmentPlanningRequest,
    ) -> MissionEnvironmentPlan:
        result = self.command(
            ApplicationCommand(
                command_type="mission.environment.propose",
                actor_id=self.principal_id,
                target_type="mission_environment_planning_request",
                target_id=str(request.request_id),
                expected_revision=0,
                payload={"request": request.model_dump(mode="json")},
            )
        )
        return MissionEnvironmentPlan.model_validate(result.payload)

    def accept_mission_environment(
        self,
        plan: MissionEnvironmentPlan,
        *,
        expected_revision: int,
    ) -> MissionEnvironmentPlanAcceptance:
        result = self.command(
            ApplicationCommand(
                command_type="mission.environment.accept",
                actor_id=self.principal_id,
                target_type="mission",
                target_id=str(plan.mission_id),
                expected_revision=expected_revision,
                payload={"plan": plan.model_dump(mode="json")},
            )
        )
        return MissionEnvironmentPlanAcceptance.model_validate(result.payload)

    def resolve_mission_environment(
        self,
        plan_id: str,
        context_id: str,
    ) -> EnvironmentBinding:
        result = self.command(
            ApplicationCommand(
                command_type="mission.environment.resolve",
                actor_id=self.principal_id,
                target_type="mission_environment_plan",
                target_id=plan_id,
                payload={"context_id": context_id},
            )
        )
        return EnvironmentBinding.model_validate(result.payload)

    def mission_environment_plan(
        self,
        mission_id: str,
        *,
        version: int | None = None,
    ) -> MissionEnvironmentPlanAcceptance:
        identity = quote(mission_id, safe="")
        response = self._client.get(
            f"/v1/missions/{identity}/environment-plan",
            headers=self._headers(),
            params={"version": version} if version is not None else None,
        )
        response.raise_for_status()
        return MissionEnvironmentPlanAcceptance.model_validate(response.json())

    def mission_environment_readiness(self, mission_id: str) -> MissionEnvironmentReadiness:
        identity = quote(mission_id, safe="")
        response = self._client.get(
            f"/v1/missions/{identity}/readiness",
            headers=self._headers(),
        )
        response.raise_for_status()
        return MissionEnvironmentReadiness.model_validate(response.json())

    def mission_task_eligibility(self, mission_id: str, task_id: str) -> MissionTaskEligibility:
        mission_identity = quote(mission_id, safe="")
        task_identity = quote(task_id, safe="")
        response = self._client.get(
            f"/v1/missions/{mission_identity}/tasks/{task_identity}/eligibility",
            headers=self._headers(),
        )
        response.raise_for_status()
        return MissionTaskEligibility.model_validate(response.json())

    def mission_completion_readiness(self, mission_id: str) -> MissionCompletionReadiness:
        identity = quote(mission_id, safe="")
        response = self._client.get(
            f"/v1/missions/{identity}/completion-readiness",
            headers=self._headers(),
        )
        response.raise_for_status()
        return MissionCompletionReadiness.model_validate(response.json())

    def claim_mission_task(self, request: MissionTaskClaimRequest) -> MissionTaskClaim:
        result = self.command(
            ApplicationCommand(
                command_type="mission.task.claim",
                actor_id=self.principal_id,
                target_type="mission_task",
                target_id=f"{request.mission_id}:{request.task_id}",
                payload={"request": request.model_dump(mode="json")},
            )
        )
        return MissionTaskClaim.model_validate(result.payload)

    def record_mission_brief(
        self,
        brief: MissionBrief,
        *,
        expected_revision: int,
    ) -> MissionBrief:
        result = self.command(
            ApplicationCommand(
                command_type="mission.brief.record",
                actor_id=self.principal_id,
                target_type="mission",
                target_id=str(brief.mission_id),
                expected_revision=expected_revision,
                payload={"brief": brief.model_dump(mode="json")},
            )
        )
        return MissionBrief.model_validate(result.payload)

    def record_mission_crew(
        self,
        crew: MissionCrewRevision,
        *,
        expected_revision: int,
    ) -> MissionCrewRevision:
        result = self.command(
            ApplicationCommand(
                command_type="mission.crew.record",
                actor_id=self.principal_id,
                target_type="mission",
                target_id=str(crew.mission_id),
                expected_revision=expected_revision,
                payload={"crew": crew.model_dump(mode="json")},
            )
        )
        return MissionCrewRevision.model_validate(result.payload)

    def record_mission_assignment(self, assignment: MissionTaskAssignment) -> MissionTaskAssignment:
        result = self.command(
            ApplicationCommand(
                command_type="mission.assignment.record",
                actor_id=self.principal_id,
                target_type="mission_assignment",
                target_id=str(assignment.assignment_id),
                expected_revision=0,
                payload={"assignment": assignment.model_dump(mode="json")},
            )
        )
        return MissionTaskAssignment.model_validate(result.payload)

    def record_mission_run_binding(self, binding: MissionRunBinding) -> MissionRunBinding:
        result = self.command(
            ApplicationCommand(
                command_type="mission.run-binding.record",
                actor_id=self.principal_id,
                target_type="mission_run_binding",
                target_id=str(binding.binding_id),
                expected_revision=0,
                payload={"binding": binding.model_dump(mode="json")},
            )
        )
        return MissionRunBinding.model_validate(result.payload)

    def record_mission_run_report(self, report: MissionRunReport) -> MissionRunReport:
        result = self.command(
            ApplicationCommand(
                command_type="mission.run-report.record",
                actor_id=self.principal_id,
                target_type="mission_run_report",
                target_id=str(report.report_id),
                expected_revision=0,
                payload={"report": report.model_dump(mode="json")},
            )
        )
        return MissionRunReport.model_validate(result.payload)

    def transition_mission(
        self,
        transition: MissionTransition,
        *,
        expected_revision: int,
    ) -> MissionTransition:
        result = self.command(
            ApplicationCommand(
                command_type="mission.transition",
                actor_id=self.principal_id,
                target_type="mission",
                target_id=str(transition.mission_id),
                expected_revision=expected_revision,
                payload={"transition": transition.model_dump(mode="json")},
            )
        )
        return MissionTransition.model_validate(result.payload)

    def missions(self, *, limit: int = 100) -> tuple[MissionRecord, ...]:
        response = self._client.get(
            "/v1/missions",
            headers=self._headers(),
            params={"limit": limit},
        )
        response.raise_for_status()
        return tuple(MissionRecord.model_validate(item) for item in response.json())

    def mission_templates(
        self,
        *,
        signals: tuple[str, ...] | None = None,
        organization_version: str = "1",
    ) -> tuple[MissionTemplateDefinition, ...]:
        params: dict[str, str | tuple[str, ...]] = {"organization_version": organization_version}
        if signals is not None:
            params["signal"] = signals
        response = self._client.get("/v1/mission-templates", headers=self._headers(), params=params)
        response.raise_for_status()
        return tuple(MissionTemplateDefinition.model_validate(item) for item in response.json())

    def mission(self, mission_id: str) -> MissionRecord:
        response = self._client.get(f"/v1/missions/{mission_id}", headers=self._headers())
        response.raise_for_status()
        return MissionRecord.model_validate(response.json())

    def mission_inspection(self, mission_id: str, *, limit: int = 100) -> dict[str, object]:
        response = self._client.get(
            f"/v1/missions/{quote(mission_id, safe='')}/inspection",
            headers=self._headers(),
            params={"limit": limit},
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise TypeError("mission inspection response must be an object")
        return payload

    def mission_brief(self, mission_id: str, *, version: int | None = None) -> MissionBrief:
        params = {} if version is None else {"version": version}
        response = self._client.get(
            f"/v1/missions/{mission_id}/brief",
            headers=self._headers(),
            params=params,
        )
        response.raise_for_status()
        return MissionBrief.model_validate(response.json())

    def mission_crew(
        self,
        mission_id: str,
        *,
        version: int | None = None,
    ) -> MissionCrewRevision:
        params = {} if version is None else {"version": version}
        response = self._client.get(
            f"/v1/missions/{mission_id}/crew",
            headers=self._headers(),
            params=params,
        )
        response.raise_for_status()
        return MissionCrewRevision.model_validate(response.json())

    def mission_assignments(
        self, mission_id: str, *, limit: int = 1_000
    ) -> tuple[MissionTaskAssignment, ...]:
        response = self._client.get(
            f"/v1/missions/{mission_id}/assignments",
            headers=self._headers(),
            params={"limit": limit},
        )
        response.raise_for_status()
        return tuple(MissionTaskAssignment.model_validate(item) for item in response.json())

    def mission_run_bindings(
        self, mission_id: str, *, limit: int = 1_000
    ) -> tuple[MissionRunBinding, ...]:
        response = self._client.get(
            f"/v1/missions/{mission_id}/run-bindings",
            headers=self._headers(),
            params={"limit": limit},
        )
        response.raise_for_status()
        return tuple(MissionRunBinding.model_validate(item) for item in response.json())

    def mission_run_reports(
        self, mission_id: str, *, limit: int = 1_000
    ) -> tuple[MissionRunReport, ...]:
        response = self._client.get(
            f"/v1/missions/{mission_id}/run-reports",
            headers=self._headers(),
            params={"limit": limit},
        )
        response.raise_for_status()
        return tuple(MissionRunReport.model_validate(item) for item in response.json())

    def mission_transitions(
        self, mission_id: str, *, limit: int = 1_000
    ) -> tuple[MissionTransition, ...]:
        response = self._client.get(
            f"/v1/missions/{mission_id}/transitions",
            headers=self._headers(),
            params={"limit": limit},
        )
        response.raise_for_status()
        return tuple(MissionTransition.model_validate(item) for item in response.json())

    def create_conversation(self, channel: ConversationChannel) -> ConversationChannel:
        result = self.command(
            ApplicationCommand(
                command_type="conversation.create",
                actor_id=self.principal_id,
                target_type="conversation",
                target_id=str(channel.conversation_id),
                expected_revision=0,
                payload={"channel": channel.model_dump(mode="json")},
            )
        )
        return ConversationChannel.model_validate(result.payload)

    def post_message(self, message: ConversationMessage) -> ConversationMessage:
        result = self.command(
            ApplicationCommand(
                command_type="conversation.message.post",
                actor_id=self.principal_id,
                target_type="conversation_message",
                target_id=str(message.message_id),
                expected_revision=0,
                payload={"message": message.model_dump(mode="json")},
            )
        )
        return ConversationMessage.model_validate(result.payload)

    def record_mission_decision(self, decision: MissionDecision) -> MissionDecision:
        result = self.command(
            ApplicationCommand(
                command_type="mission.decision.record",
                actor_id=self.principal_id,
                target_type="mission_decision",
                target_id=str(decision.decision_id),
                expected_revision=0,
                payload={"decision": decision.model_dump(mode="json")},
            )
        )
        return MissionDecision.model_validate(result.payload)

    def open_mission_escalation(self, escalation: MissionEscalation) -> MissionEscalation:
        result = self.command(
            ApplicationCommand(
                command_type="mission.escalation.open",
                actor_id=self.principal_id,
                target_type="mission_escalation",
                target_id=str(escalation.escalation_id),
                expected_revision=0,
                payload={"escalation": escalation.model_dump(mode="json")},
            )
        )
        return MissionEscalation.model_validate(result.payload)

    def apply_mission_intervention(
        self,
        intervention: MissionIntervention,
        *,
        expected_revision: int,
    ) -> MissionIntervention:
        result = self.command(
            ApplicationCommand(
                command_type="mission.intervention.apply",
                actor_id=self.principal_id,
                target_type="mission",
                target_id=str(intervention.mission_id),
                expected_revision=expected_revision,
                payload={"intervention": intervention.model_dump(mode="json")},
            )
        )
        return MissionIntervention.model_validate(result.payload)

    def conversations(
        self,
        *,
        mission_id: str | None = None,
        limit: int = 100,
    ) -> tuple[ConversationChannel, ...]:
        params: dict[str, str | int] = {"limit": limit}
        if mission_id is not None:
            params["mission_id"] = mission_id
        response = self._client.get("/v1/conversations", headers=self._headers(), params=params)
        response.raise_for_status()
        return tuple(ConversationChannel.model_validate(item) for item in response.json())

    def conversation(self, conversation_id: str) -> ConversationChannel:
        response = self._client.get(f"/v1/conversations/{conversation_id}", headers=self._headers())
        response.raise_for_status()
        return ConversationChannel.model_validate(response.json())

    def conversation_messages(
        self, conversation_id: str, *, limit: int = 100
    ) -> tuple[ConversationMessage, ...]:
        response = self._client.get(
            f"/v1/conversations/{conversation_id}/messages",
            headers=self._headers(),
            params={"limit": limit},
        )
        response.raise_for_status()
        return tuple(ConversationMessage.model_validate(item) for item in response.json())

    def mission_escalations(
        self,
        mission_id: str,
        *,
        state: EscalationState | None = None,
        limit: int = 100,
    ) -> tuple[MissionEscalation, ...]:
        params: dict[str, str | int] = {"limit": limit}
        if state is not None:
            params["state"] = state.value
        response = self._client.get(
            f"/v1/missions/{mission_id}/escalations",
            headers=self._headers(),
            params=params,
        )
        response.raise_for_status()
        return tuple(MissionEscalation.model_validate(item) for item in response.json())

    def mission_decisions(
        self, mission_id: str, *, limit: int = 100
    ) -> tuple[MissionDecision, ...]:
        response = self._client.get(
            f"/v1/missions/{mission_id}/decisions",
            headers=self._headers(),
            params={"limit": limit},
        )
        response.raise_for_status()
        return tuple(MissionDecision.model_validate(item) for item in response.json())

    def mission_interventions(
        self, mission_id: str, *, limit: int = 100
    ) -> tuple[MissionIntervention, ...]:
        response = self._client.get(
            f"/v1/missions/{mission_id}/interventions",
            headers=self._headers(),
            params={"limit": limit},
        )
        response.raise_for_status()
        return tuple(MissionIntervention.model_validate(item) for item in response.json())

    def telemetry_status(self) -> TelemetryStatus:
        response = self._client.get("/v1/telemetry/status", headers=self._headers())
        response.raise_for_status()
        return TelemetryStatus.model_validate(response.json())

    def engineer_profile(self) -> EngineerProfile:
        response = self._client.get("/v1/context/engineer-profile", headers=self._headers())
        response.raise_for_status()
        return EngineerProfile.model_validate(response.json())

    def community_candidates(self) -> tuple[CommunityCandidate, ...]:
        response = self._client.get("/v1/context/community-candidates", headers=self._headers())
        response.raise_for_status()
        return tuple(
            CommunityCandidate.model_validate(item) for item in response.json()["candidates"]
        )

    def recommend_community_candidate(
        self, request: ContextualRecommendationRequest
    ) -> ContextualRecommendation:
        result = self.command(
            ApplicationCommand(
                command_type="context.recommend",
                actor_id=self.principal_id,
                target_type="context_recommendation",
                target_id=str(request.request_id),
                payload={"request": request.model_dump(mode="json")},
            )
        )
        return ContextualRecommendation.model_validate(result.payload)

    def import_langsmith_feedback(
        self,
        request: LangSmithFeedbackImportRequest,
    ) -> TelemetryEvaluationImportResult:
        result = self.command(
            ApplicationCommand(
                command_type="telemetry.evaluation.import",
                actor_id=self.principal_id,
                target_type="telemetry_evaluation",
                target_id=str(request.import_id),
                payload={"request": request.model_dump(mode="json")},
            )
        )
        return TelemetryEvaluationImportResult.model_validate(result.payload)

    def events(
        self,
        *,
        after: int = 0,
        limit: int | None = None,
        event_types: tuple[str, ...] = (),
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
        params = self._event_params(
            after=after,
            event_types=event_types,
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
        if limit is not None:
            params = params.set("limit", limit)
        response = self._client.get("/v1/events", headers=self._headers(), params=params)
        response.raise_for_status()
        return EventPage.model_validate(response.json())

    def notifications(
        self,
        *,
        after: int = 0,
        limit: int | None = None,
        severities: tuple[NotificationSeverity, ...] = (),
        deliveries: tuple[NotificationDelivery, ...] = (),
    ) -> NotificationPage:
        params: list[tuple[str, str | int | float | bool | None]] = [("after", after)]
        if limit is not None:
            params.append(("limit", limit))
        params.extend(("severity", item.value) for item in severities)
        params.extend(("delivery", item.value) for item in deliveries)
        response = self._client.get(
            "/v1/notifications",
            headers=self._headers(),
            params=params,
        )
        response.raise_for_status()
        return NotificationPage.model_validate(response.json())

    def stream_events(
        self,
        *,
        after: int = 0,
        event_types: tuple[str, ...] = (),
        entity_type: str | None = None,
        entity_id: str | None = None,
        run_id: str | None = None,
        task_id: str | None = None,
        identity_id: str | None = None,
        team_id: str | None = None,
        occurred_after: datetime | None = None,
        occurred_before: datetime | None = None,
        security_relevant: bool | None = None,
    ) -> Iterator[EventEnvelope]:
        params = self._event_params(
            after=after,
            event_types=event_types,
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
        with self._client.stream(
            "GET",
            "/v1/events/stream",
            headers={**self._headers(), "Last-Event-ID": str(after)},
            params=params,
        ) as response:
            response.raise_for_status()
            data: list[str] = []
            for line in response.iter_lines():
                if not line:
                    if data:
                        yield EventEnvelope.model_validate(json.loads("\n".join(data)))
                        data.clear()
                    continue
                if line.startswith("data: "):
                    data.append(line.removeprefix("data: "))

    @staticmethod
    def _event_params(
        *,
        after: int,
        event_types: tuple[str, ...],
        entity_type: str | None,
        entity_id: str | None,
        run_id: str | None,
        task_id: str | None,
        identity_id: str | None,
        team_id: str | None,
        occurred_after: datetime | None,
        occurred_before: datetime | None,
        security_relevant: bool | None,
    ) -> httpx.QueryParams:
        params = httpx.QueryParams({"after": after})
        optional = {
            "entity_type": entity_type,
            "entity_id": entity_id,
            "run_id": run_id,
            "task_id": task_id,
            "identity_id": identity_id,
            "team_id": team_id,
            "occurred_after": occurred_after.isoformat() if occurred_after else None,
            "occurred_before": occurred_before.isoformat() if occurred_before else None,
            "security_relevant": security_relevant,
        }
        for name, value in optional.items():
            if value is not None:
                params = params.set(name, value)
        for value in event_types:
            params = params.add("event_type", value)
        return params

    def export_events_jsonl(
        self,
        destination: Path,
        *,
        after: int = 0,
        page_size: int = 1_000,
    ) -> tuple[int, int]:
        """Export a coherent event range using an atomic local replacement."""
        if page_size < 1 or page_size > 1_000:
            raise ValueError("page_size must be between 1 and 1000")
        destination.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent
        )
        cursor = after
        count = 0
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                while True:
                    page = self.events(after=cursor, limit=page_size)
                    for event in page.events:
                        stream.write(event.model_dump_json() + "\n")
                        count += 1
                    if not page.events:
                        break
                    cursor = page.next_cursor
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary_name, destination)
            directory = os.open(destination.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        except BaseException:
            Path(temporary_name).unlink(missing_ok=True)
            raise
        return count, cursor

    def event_holds(self, *, active_only: bool = False) -> tuple[EventEvidenceHold, ...]:
        response = self._client.get(
            "/v1/events/holds",
            headers=self._headers(),
            params={"active_only": active_only},
        )
        response.raise_for_status()
        return tuple(EventEvidenceHold.model_validate(item) for item in response.json())

    def event_retention_plans(self) -> tuple[EventRetentionPlan, ...]:
        response = self._client.get(
            "/v1/events/retention-plans",
            headers=self._headers(),
        )
        response.raise_for_status()
        return tuple(EventRetentionPlan.model_validate(item) for item in response.json())

    def event_retention_policy(self) -> EventRetentionPolicy:
        response = self._client.get(
            "/v1/events/retention-policy",
            headers=self._headers(),
        )
        response.raise_for_status()
        return EventRetentionPolicy.model_validate(response.json())

    def artifacts(self, *, offset: int = 0, limit: int = 100) -> tuple[ArtifactManifest, ...]:
        response = self._client.get(
            "/v1/artifacts",
            headers=self._headers(),
            params={"offset": offset, "limit": limit},
        )
        response.raise_for_status()
        return tuple(ArtifactManifest.model_validate(item) for item in response.json())

    def artifact(self, reference: str) -> ArtifactManifest:
        artifact_id = reference.removeprefix("artifact:")
        response = self._client.get(f"/v1/artifacts/{artifact_id}", headers=self._headers())
        response.raise_for_status()
        return ArtifactManifest.model_validate(response.json())

    def artifact_content(self, reference: str) -> Iterator[bytes]:
        artifact_id = reference.removeprefix("artifact:")
        with self._client.stream(
            "GET", f"/v1/artifacts/{artifact_id}/content", headers=self._headers()
        ) as response:
            response.raise_for_status()
            yield from response.iter_bytes()

    def artifact_upload(self, upload_id: str) -> UploadSession:
        response = self._client.get(f"/v1/artifact-uploads/{upload_id}", headers=self._headers())
        response.raise_for_status()
        return UploadSession.model_validate(response.json())

    def artifact_collections(
        self, *, offset: int = 0, limit: int = 100
    ) -> tuple[ArtifactCollection, ...]:
        response = self._client.get(
            "/v1/artifact-collections",
            headers=self._headers(),
            params={"offset": offset, "limit": limit},
        )
        response.raise_for_status()
        return tuple(ArtifactCollection.model_validate(item) for item in response.json())

    def artifact_references(
        self, *, offset: int = 0, limit: int = 100
    ) -> tuple[WorkingReference, ...]:
        response = self._client.get(
            "/v1/artifact-references",
            headers=self._headers(),
            params={"offset": offset, "limit": limit},
        )
        response.raise_for_status()
        return tuple(WorkingReference.model_validate(item) for item in response.json())

    def artifact_holds(
        self, *, offset: int = 0, limit: int = 100
    ) -> tuple[ArtifactEvidenceHold, ...]:
        response = self._client.get(
            "/v1/artifact-holds",
            headers=self._headers(),
            params={"offset": offset, "limit": limit},
        )
        response.raise_for_status()
        return tuple(ArtifactEvidenceHold.model_validate(item) for item in response.json())

    def artifact_pins(self, *, offset: int = 0, limit: int = 100) -> tuple[ArtifactPin, ...]:
        response = self._client.get(
            "/v1/artifact-pins",
            headers=self._headers(),
            params={"offset": offset, "limit": limit},
        )
        response.raise_for_status()
        return tuple(ArtifactPin.model_validate(item) for item in response.json())

    def change_sets(self, *, offset: int = 0, limit: int = 100) -> tuple[ChangeSetResult, ...]:
        response = self._client.get(
            "/v1/change-sets",
            headers=self._headers(),
            params={"offset": offset, "limit": limit},
        )
        response.raise_for_status()
        return tuple(ChangeSetResult.model_validate(item) for item in response.json())

    def change_set(self, change_set_id: str) -> ChangeSetResult:
        response = self._client.get(f"/v1/change-sets/{change_set_id}", headers=self._headers())
        response.raise_for_status()
        return ChangeSetResult.model_validate(response.json())

    def sessions(self, *, offset: int = 0, limit: int = 100) -> tuple[ExecutionSession, ...]:
        response = self._client.get(
            "/v1/sessions",
            headers=self._headers(),
            params={"offset": offset, "limit": limit},
        )
        response.raise_for_status()
        return tuple(ExecutionSession.model_validate(item) for item in response.json())

    def session(self, session_id: str) -> ExecutionSession:
        response = self._client.get(f"/v1/sessions/{session_id}", headers=self._headers())
        response.raise_for_status()
        return ExecutionSession.model_validate(response.json())

    def session_output(
        self,
        session_id: str,
        *,
        channel: str = "stdout",
        offset: int = 0,
        limit: int = 65_536,
        binary: bool = False,
    ) -> CursorRead:
        response = self._client.get(
            f"/v1/sessions/{session_id}/output",
            headers=self._headers(),
            params={
                "channel": channel,
                "offset": offset,
                "limit": limit,
                "binary": binary,
            },
        )
        response.raise_for_status()
        return CursorRead.model_validate(response.json())

    def runs(self, *, offset: int = 0, limit: int = 100) -> tuple[dict[str, object], ...]:
        response = self._client.get(
            "/v1/runs",
            headers=self._headers(),
            params={"offset": offset, "limit": limit},
        )
        response.raise_for_status()
        return tuple(dict(item) for item in response.json())

    def create_prospective_run(
        self,
        *,
        workspace_id: str,
        objective: str,
        outcome_id: str,
    ) -> dict[str, object]:
        """Create a run bound to observed pre-repository workspace identity."""
        request = ProspectiveRunRequest(
            workspace_id=workspace_id,
            objective=objective,
            outcome_id=outcome_id,
        )
        result = self.command(
            ApplicationCommand(
                command_type="run.prospective.create",
                actor_id=self.principal_id,
                target_type="run",
                payload=request.model_dump(mode="json"),
            )
        )
        return dict(result.payload)

    def establish_repository(
        self,
        run_id: str,
        *,
        prospective_workspace_id: str,
        discovery_revision: str,
        evidence_references: tuple[str, ...],
    ) -> dict[str, object]:
        """Record proven repository establishment without rewriting run lineage."""
        request = RepositoryEstablishmentRequest(
            prospective_workspace_id=prospective_workspace_id,
            discovery_revision=discovery_revision,
            evidence_references=evidence_references,
        )
        result = self.command(
            ApplicationCommand(
                command_type="run.repository.establish",
                actor_id=self.principal_id,
                target_type="run",
                target_id=run_id,
                payload=request.model_dump(mode="json"),
            )
        )
        return dict(result.payload)

    def tasks(
        self,
        run_id: str,
        *,
        offset: int = 0,
        limit: int = 100,
    ) -> tuple[dict[str, object], ...]:
        response = self._client.get(
            f"/v1/runs/{run_id}/tasks",
            headers=self._headers(),
            params={"offset": offset, "limit": limit},
        )
        response.raise_for_status()
        return tuple(dict(item) for item in response.json())

    def skills(
        self,
        *,
        offset: int = 0,
        limit: int = 100,
        name: str | None = None,
    ) -> tuple[SkillVersionRecord, ...]:
        params: dict[str, str | int] = {"offset": offset, "limit": limit}
        if name is not None:
            params["name"] = name
        response = self._client.get("/v1/skills", headers=self._headers(), params=params)
        response.raise_for_status()
        return tuple(SkillVersionRecord.model_validate(item) for item in response.json())

    def active_skill(self, name: str) -> SkillVersionRecord | None:
        identity = quote(name, safe="")
        response = self._client.get(
            f"/v1/skills/{identity}/active",
            headers=self._headers(),
        )
        response.raise_for_status()
        payload = response.json()
        return None if payload is None else SkillVersionRecord.model_validate(payload)

    def skill_usage_summary(
        self,
        task_class: str,
        *,
        skill_name: str | None = None,
    ) -> SkillUsageSummary:
        params: dict[str, str] = {"task_class": task_class}
        if skill_name is not None:
            params["skill_name"] = skill_name
        response = self._client.get(
            "/v1/skill-usage/summary",
            headers=self._headers(),
            params=params,
        )
        response.raise_for_status()
        return SkillUsageSummary.model_validate(response.json())

    def invoke_skill(self, request: SkillInvocationRequest) -> SkillInvocationEvidence:
        """Resolve and load active instructions through the governed command path."""
        result = self.command(
            ApplicationCommand(
                command_type="skill.invoke",
                actor_id=self.principal_id,
                target_type="task",
                target_id=request.context.task_id,
                payload={"request": request.model_dump(mode="json")},
            )
        )
        return SkillInvocationEvidence.model_validate(result.payload)

    def learn_skill(self, request: SkillLearningRequest) -> SkillLearningRecord:
        """Execute a governed Research proposal and return its durable lineage."""
        result = self.command(
            ApplicationCommand(
                command_type="skill.learn",
                actor_id=self.principal_id,
                target_type="skill_learning",
                target_id=str(request.request_id),
                payload={"request": request.model_dump(mode="json")},
            )
        )
        return SkillLearningRecord.model_validate(result.payload)

    def skill_learning(
        self,
        *,
        offset: int = 0,
        limit: int = 100,
    ) -> tuple[SkillLearningRecord, ...]:
        response = self._client.get(
            "/v1/skill-learning",
            headers=self._headers(),
            params={"offset": offset, "limit": limit},
        )
        response.raise_for_status()
        return tuple(SkillLearningRecord.model_validate(item) for item in response.json())

    def skill_learning_record(self, request_id: str) -> SkillLearningRecord:
        identity = quote(request_id, safe="")
        response = self._client.get(
            f"/v1/skill-learning/{identity}",
            headers=self._headers(),
        )
        response.raise_for_status()
        return SkillLearningRecord.model_validate(response.json())

    def skill_updates(self) -> SkillUpdateReport:
        response = self._client.get("/v1/skill-updates", headers=self._headers())
        response.raise_for_status()
        return SkillUpdateReport.model_validate(response.json())

    def skill_curation(self) -> tuple[SkillCurationProposal, ...]:
        response = self._client.get("/v1/skill-curation", headers=self._headers())
        response.raise_for_status()
        return tuple(SkillCurationProposal.model_validate(item) for item in response.json())

    def observe_environment(
        self,
        request: EnvironmentObservationRequest,
    ) -> EnvironmentObservation:
        result = self.command(
            ApplicationCommand(
                command_type="environment.observe",
                actor_id=self.principal_id,
                target_type="environment_observation",
                target_id=str(request.observation_id),
                payload={"request": request.model_dump(mode="json")},
            )
        )
        return EnvironmentObservation.model_validate(result.payload)

    def resolve_environment(self, request: EnvironmentBindingRequest) -> EnvironmentBinding:
        result = self.command(
            ApplicationCommand(
                command_type="environment.resolve",
                actor_id=self.principal_id,
                target_type="environment_binding_request",
                target_id=str(request.request_id),
                payload={"request": request.model_dump(mode="json")},
            )
        )
        return EnvironmentBinding.model_validate(result.payload)

    def environment_observation(self, observation_id: str) -> EnvironmentObservation:
        identity = quote(observation_id, safe="")
        response = self._client.get(
            f"/v1/environment/observations/{identity}",
            headers=self._headers(),
        )
        response.raise_for_status()
        return EnvironmentObservation.model_validate(response.json())

    def environment_binding(self, binding_id: str) -> EnvironmentBinding:
        identity = quote(binding_id, safe="")
        response = self._client.get(
            f"/v1/environment/bindings/{identity}",
            headers=self._headers(),
        )
        response.raise_for_status()
        return EnvironmentBinding.model_validate(response.json())

    def engineering_command_candidates(
        self,
        observation_id: str,
    ) -> tuple[EngineeringCommandCandidate, ...]:
        identity = quote(observation_id, safe="")
        response = self._client.get(
            f"/v1/environment/observations/{identity}/command-candidates",
            headers=self._headers(),
        )
        response.raise_for_status()
        return tuple(EngineeringCommandCandidate.model_validate(item) for item in response.json())

    def plan_engineering_command(
        self,
        request: EngineeringCommandRequest,
    ) -> EngineeringCommandPlan:
        result = self.command(
            ApplicationCommand(
                command_type="environment.command.plan",
                actor_id=self.principal_id,
                target_type="engineering_command",
                target_id=str(request.request_id),
                payload={"request": request.model_dump(mode="json")},
            )
        )
        return EngineeringCommandPlan.model_validate(result.payload)

    def start_engineering_command(
        self,
        request: EngineeringCommandRequest,
    ) -> tuple[EngineeringCommandPlan, ExecutionSession]:
        plan = self.plan_engineering_command(request)
        if plan.execution.mode.value not in {"job", "pty"}:
            raise ValueError("daemon engineering command requires a supervised session mode")
        result = self.command(
            ApplicationCommand(
                command_type="session.start",
                actor_id=self.principal_id,
                target_type="session_service",
                payload={"request": plan.execution.model_dump(mode="json")},
            )
        )
        return plan, ExecutionSession.model_validate(result.payload)

    def validate_environment_descriptors(
        self,
        descriptor_set: EnvironmentDescriptorSet,
    ) -> DescriptorValidationResult:
        result = self.command(
            ApplicationCommand(
                command_type="environment.descriptor.validate",
                actor_id=self.principal_id,
                target_type="environment_descriptor_set",
                target_id=str(descriptor_set.descriptor_set_id),
                payload={"descriptor_set": descriptor_set.model_dump(mode="json")},
            )
        )
        return DescriptorValidationResult.model_validate(result.payload)

    def plan_environment_descriptor_change(
        self,
        request: EnvironmentDescriptorChangeRequest,
    ) -> EnvironmentDescriptorChangePlan:
        result = self.command(
            ApplicationCommand(
                command_type="environment.descriptor.change.plan",
                actor_id=self.principal_id,
                target_type="environment_descriptor_change",
                target_id=str(request.request_id),
                payload={"request": request.model_dump(mode="json")},
            )
        )
        return EnvironmentDescriptorChangePlan.model_validate(result.payload)

    def plan_environment_operation(
        self,
        request: EnvironmentOperationRequest,
    ) -> EnvironmentOperationPlan:
        result = self.command(
            ApplicationCommand(
                command_type="environment.operation.plan",
                actor_id=self.principal_id,
                target_type="environment_operation",
                target_id=str(request.operation_id),
                payload={"request": request.model_dump(mode="json")},
            )
        )
        return EnvironmentOperationPlan.model_validate(result.payload)

    def start_environment_operation(
        self,
        request: EnvironmentOperationRequest,
    ) -> tuple[EnvironmentOperationPlan, ExecutionSession]:
        plan = self.plan_environment_operation(request)
        if plan.execution.mode.value != "job":
            raise ValueError("daemon environment execution requires a managed-job adapter")
        result = self.command(
            ApplicationCommand(
                command_type="session.start",
                actor_id=self.principal_id,
                target_type="session_service",
                payload={"request": plan.execution.model_dump(mode="json")},
            )
        )
        return plan, ExecutionSession.model_validate(result.payload)

    def environment_descriptor_set(self, descriptor_set_id: str) -> EnvironmentDescriptorSet:
        identity = quote(descriptor_set_id, safe="")
        response = self._client.get(
            f"/v1/environment/descriptor-sets/{identity}",
            headers=self._headers(),
        )
        response.raise_for_status()
        return EnvironmentDescriptorSet.model_validate(response.json())

    def settle_environment_attempt(
        self,
        plan: EnvironmentOperationPlan,
        session_id: str,
    ) -> EnvironmentAttempt:
        result = self.command(
            ApplicationCommand(
                command_type="environment.attempt.settle",
                actor_id=self.principal_id,
                target_type="environment_operation",
                target_id=str(plan.request.operation_id),
                payload={
                    "operation_plan": plan.model_dump(mode="json"),
                    "session_id": session_id,
                },
            )
        )
        return EnvironmentAttempt.model_validate(result.payload)

    def verify_environment(
        self,
        request: EnvironmentVerificationRequest,
    ) -> EnvironmentVerification:
        result = self.command(
            ApplicationCommand(
                command_type="environment.verification.record",
                actor_id=self.principal_id,
                target_type="environment_verification",
                target_id=str(request.verification_id),
                payload={"request": request.model_dump(mode="json")},
            )
        )
        return EnvironmentVerification.model_validate(result.payload)

    def invalidate_environment(
        self,
        invalidation: EnvironmentInvalidation,
    ) -> EnvironmentInvalidation:
        result = self.command(
            ApplicationCommand(
                command_type="environment.binding.invalidate",
                actor_id=self.principal_id,
                target_type="environment_binding",
                target_id=str(invalidation.binding_id),
                payload={"invalidation": invalidation.model_dump(mode="json")},
            )
        )
        return EnvironmentInvalidation.model_validate(result.payload)

    def environment_attempt(self, attempt_id: str) -> EnvironmentAttempt:
        identity = quote(attempt_id, safe="")
        response = self._client.get(
            f"/v1/environment/attempts/{identity}",
            headers=self._headers(),
        )
        response.raise_for_status()
        return EnvironmentAttempt.model_validate(response.json())

    def environment_verification(self, verification_id: str) -> EnvironmentVerification:
        identity = quote(verification_id, safe="")
        response = self._client.get(
            f"/v1/environment/verifications/{identity}",
            headers=self._headers(),
        )
        response.raise_for_status()
        return EnvironmentVerification.model_validate(response.json())

    def mcp_connections(
        self,
        *,
        offset: int = 0,
        limit: int = 100,
    ) -> tuple[dict[str, object], ...]:
        response = self._client.get(
            "/v1/mcp/connections",
            headers=self._headers(),
            params={"offset": offset, "limit": limit},
        )
        response.raise_for_status()
        return tuple(dict(item) for item in response.json())

    def mcp_primitives(self, connection_id: str) -> tuple[dict[str, object], ...]:
        identity = quote(connection_id, safe="")
        response = self._client.get(
            f"/v1/mcp/connections/{identity}/primitives",
            headers=self._headers(),
        )
        response.raise_for_status()
        return tuple(dict(item) for item in response.json())

    def mcp_contracts(self, connection_id: str) -> tuple[dict[str, object], ...]:
        identity = quote(connection_id, safe="")
        response = self._client.get(
            f"/v1/mcp/connections/{identity}/contracts",
            headers=self._headers(),
        )
        response.raise_for_status()
        return tuple(dict(item) for item in response.json())

    def mcp_calls(
        self,
        *,
        offset: int = 0,
        limit: int = 100,
    ) -> tuple[dict[str, object], ...]:
        response = self._client.get(
            "/v1/mcp/calls",
            headers=self._headers(),
            params={"offset": offset, "limit": limit},
        )
        response.raise_for_status()
        return tuple(dict(item) for item in response.json())

    def mcp_progress(self, request_id: str, *, cursor: int = 0) -> tuple[dict[str, object], ...]:
        identity = quote(request_id, safe="")
        response = self._client.get(
            f"/v1/mcp/calls/{identity}/progress",
            headers=self._headers(),
            params={"cursor": cursor},
        )
        response.raise_for_status()
        return tuple(dict(item) for item in response.json())

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._token_file.read().token}"}


class KnowledgeClient:
    """Thin attributed-knowledge client; all authority remains in mishkand."""

    def __init__(self, client: Mishkan) -> None:
        self._client = client

    def query(self, query: KnowledgeQuery) -> KnowledgeBundle:
        result = self._client.command(
            ApplicationCommand(
                command_type="knowledge.query",
                actor_id=self._client.principal_id,
                target_type="knowledge_query",
                target_id=str(query.query_id),
                payload={"query": query.model_dump(mode="json")},
            )
        )
        return KnowledgeBundle.model_validate(result.payload)

    def sources(self) -> tuple[dict[str, object], ...]:
        response = self._client._client.get(
            "/v1/knowledge/sources", headers=self._client._headers()
        )
        response.raise_for_status()
        payload = response.json()
        return tuple(dict(item) for item in payload["sources"])

    def query_record(
        self, query_id: UUID
    ) -> tuple[KnowledgeQueryRecord, tuple[dict[str, object], ...]]:
        response = self._client._client.get(
            f"/v1/knowledge/queries/{query_id}", headers=self._client._headers()
        )
        response.raise_for_status()
        payload = response.json()
        return (
            KnowledgeQueryRecord.model_validate(payload["query"]),
            tuple(dict(item) for item in payload["attempts"]),
        )

    def ingest(self, request: KnowledgeIngestRequest) -> KnowledgeOperation:
        return self._operation("knowledge.ingest", request.operation_id, request)

    def refresh(self, request: KnowledgeRefreshRequest) -> KnowledgeOperation:
        return self._operation("knowledge.refresh", request.operation_id, request)

    def cancel(self, operation_id: UUID) -> KnowledgeOperation:
        result = self._client.command(
            ApplicationCommand(
                command_type="knowledge.operation.cancel",
                actor_id=self._client.principal_id,
                target_type="knowledge_operation",
                target_id=str(operation_id),
                payload={},
            )
        )
        return KnowledgeOperation.model_validate(result.payload)

    def reconcile(self, request: KnowledgeOperationReconcileRequest) -> KnowledgeOperation:
        return self._operation("knowledge.operation.reconcile", request.operation_id, request)

    def operations(
        self, *, project_id: str | None = None, offset: int = 0, limit: int = 100
    ) -> tuple[KnowledgeOperation, ...]:
        params: dict[str, str | int] = {"offset": offset, "limit": limit}
        if project_id is not None:
            params["project_id"] = project_id
        response = self._client._client.get(
            "/v1/knowledge/operations",
            headers=self._client._headers(),
            params=params,
        )
        response.raise_for_status()
        return tuple(KnowledgeOperation.model_validate(item) for item in response.json())

    def operation(self, operation_id: UUID) -> KnowledgeOperation:
        response = self._client._client.get(
            f"/v1/knowledge/operations/{operation_id}", headers=self._client._headers()
        )
        response.raise_for_status()
        return KnowledgeOperation.model_validate(response.json())

    def corpora(
        self, *, project_id: str | None = None, offset: int = 0, limit: int = 100
    ) -> tuple[KnowledgeCorpus, ...]:
        params: dict[str, str | int] = {"offset": offset, "limit": limit}
        if project_id is not None:
            params["project_id"] = project_id
        response = self._client._client.get(
            "/v1/knowledge/corpora", headers=self._client._headers(), params=params
        )
        response.raise_for_status()
        return tuple(KnowledgeCorpus.model_validate(item) for item in response.json())

    def propose(self, proposal: KnowledgePromotion) -> KnowledgePromotion:
        result = self._client.command(
            ApplicationCommand(
                command_type="knowledge.promotion.propose",
                actor_id=self._client.principal_id,
                target_type="knowledge_promotion",
                target_id=str(proposal.promotion_id),
                payload={"proposal": proposal.model_dump(mode="json")},
            )
        )
        return KnowledgePromotion.model_validate(result.payload)

    def decide(self, decision: KnowledgePromotionDecision) -> KnowledgePromotion:
        result = self._client.command(
            ApplicationCommand(
                command_type="knowledge.promotion.decide",
                actor_id=self._client.principal_id,
                target_type="knowledge_promotion",
                target_id=str(decision.promotion_id),
                payload={"decision": decision.model_dump(mode="json")},
            )
        )
        return KnowledgePromotion.model_validate(result.payload)

    def promotions(
        self, *, project_id: str | None = None, offset: int = 0, limit: int = 100
    ) -> tuple[KnowledgePromotion, ...]:
        params: dict[str, str | int] = {"offset": offset, "limit": limit}
        if project_id is not None:
            params["project_id"] = project_id
        response = self._client._client.get(
            "/v1/knowledge/promotions", headers=self._client._headers(), params=params
        )
        response.raise_for_status()
        return tuple(KnowledgePromotion.model_validate(item) for item in response.json())

    def _operation(
        self,
        command_type: str,
        operation_id: UUID,
        request: KnowledgeIngestRequest
        | KnowledgeRefreshRequest
        | KnowledgeOperationReconcileRequest,
    ) -> KnowledgeOperation:
        result = self._client.command(
            ApplicationCommand(
                command_type=command_type,
                actor_id=self._client.principal_id,
                target_type="knowledge_operation",
                target_id=str(operation_id),
                payload={"request": request.model_dump(mode="json")},
            )
        )
        return KnowledgeOperation.model_validate(result.payload)


class MemoryClient:
    """Episodic-memory view over the same daemon knowledge authority."""

    def __init__(self, client: Mishkan) -> None:
        self._client = client

    def recall(self, query: KnowledgeQuery) -> KnowledgeBundle:
        return self._client.knowledge.query(query)

    def capture(self, request: KnowledgeMemoryCaptureRequest) -> KnowledgeOperation:
        result = self._client.command(
            ApplicationCommand(
                command_type="knowledge.memory.capture",
                actor_id=self._client.principal_id,
                target_type="knowledge_operation",
                target_id=str(request.operation_id),
                payload={"request": request.model_dump(mode="json")},
            )
        )
        return KnowledgeOperation.model_validate(result.payload)

    def list(
        self, *, project_id: str | None = None, offset: int = 0, limit: int = 100
    ) -> tuple[KnowledgeOperation, ...]:
        return self._client.knowledge.operations(project_id=project_id, offset=offset, limit=limit)


class StructureClient:
    """Structural-evidence view over governed knowledge queries and refreshes."""

    def __init__(self, client: Mishkan) -> None:
        self._client = client

    def query(self, query: KnowledgeQuery) -> KnowledgeBundle:
        return self._client.knowledge.query(query)

    def refresh(self, request: KnowledgeRefreshRequest) -> KnowledgeOperation:
        return self._client.knowledge.refresh(request)

    def status(self, *, project_id: str | None = None) -> tuple[KnowledgeCorpus, ...]:
        return self._client.knowledge.corpora(project_id=project_id)


def daemon_url(host: str, port: int) -> str:
    formatted = f"[{host}]" if ":" in host else host
    return f"http://{formatted}:{port}"
