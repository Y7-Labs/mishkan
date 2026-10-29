"""Versioned high-performance Mission Crew contracts and deterministic gates."""

from __future__ import annotations

from datetime import datetime, timedelta
from enum import StrEnum
from typing import Literal
from uuid import NAMESPACE_URL, UUID, uuid5

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from mishkan.domain.errors import ErrorCode, MishkanError
from mishkan.domain.identity import new_id
from mishkan.domain.time import require_aware, utc_now
from mishkan.events import EventEnvelope
from mishkan.missions.assignment_graph import MissionAssignmentGraphValidator
from mishkan.missions.models import (
    CrewAssignmentKind,
    MissionBrief,
    MissionCrewRevision,
    MissionResourceLimit,
    MissionTaskAssignment,
)


class TeamModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ProductionShape(StrEnum):
    SINGLE_OWNER = "single_owner"
    CONSULTATIVE = "consultative"
    MULTI_PRODUCER = "multi_producer"


class CollaborationInteraction(StrEnum):
    DELEGATE = "delegate"
    COLLABORATE = "collaborate"
    CONSULT = "consult"
    HANDOFF = "handoff"
    CHALLENGE = "challenge"
    EVALUATE = "evaluate"


class EvidenceComparability(StrEnum):
    DIRECT = "direct"
    PARTIAL = "partial"
    STALE = "stale"
    CONFLICTING = "conflicting"
    MATERIALLY_DIFFERENT = "materially_different"


class TeamHealthStatus(StrEnum):
    HEALTHY = "healthy"
    ATTENTION = "attention"
    BLOCKED = "blocked"
    UNCERTAIN = "uncertain"


class TeamDimension(StrEnum):
    OUTCOME = "outcome"
    STABILITY = "stability"
    FLOW = "flow"
    COORDINATION = "coordination"
    EPISTEMIC_SAFETY = "epistemic_safety"
    LEARNING = "learning"
    RESOURCE_EFFICIENCY = "resource_efficiency"


class AdaptationDisposition(StrEnum):
    BOUNDED = "bounded"
    APPROVAL_REQUIRED = "approval_required"


class AdaptationKind(StrEnum):
    REPAIR_CONTEXT = "repair_context"
    REQUEST_CONSULTATION = "request_consultation"
    REQUEST_CHALLENGE = "request_challenge"
    SPLIT_TASK = "split_task"
    COMBINE_TASKS = "combine_tasks"
    REDUCE_PARALLELISM = "reduce_parallelism"
    REASSIGN_WITHIN_CREW = "reassign_within_crew"
    PAUSE_DEPENDENT_WORK = "pause_dependent_work"
    ADD_CREW_MEMBER = "add_crew_member"
    CHANGE_INDEPENDENCE = "change_independence"
    CHANGE_MATERIAL_SCOPE = "change_material_scope"
    EXPAND_AUTHORITY = "expand_authority"
    ACCEPT_CONSEQUENTIAL_RISK = "accept_consequential_risk"


BOUNDED_ADAPTATIONS = frozenset(
    {
        AdaptationKind.REPAIR_CONTEXT,
        AdaptationKind.REQUEST_CONSULTATION,
        AdaptationKind.REQUEST_CHALLENGE,
        AdaptationKind.SPLIT_TASK,
        AdaptationKind.COMBINE_TASKS,
        AdaptationKind.REDUCE_PARALLELISM,
        AdaptationKind.REASSIGN_WITHIN_CREW,
        AdaptationKind.PAUSE_DEPENDENT_WORK,
    }
)


class CollaborationResponsibility(TeamModel):
    identity_id: str = Field(min_length=2, max_length=128)
    responsibility: str = Field(min_length=3, max_length=4_096)
    decision_rights: tuple[str, ...] = Field(min_length=1)


class CollaborationEdge(TeamModel):
    source_identity: str = Field(min_length=2, max_length=128)
    target_identity: str = Field(min_length=2, max_length=128)
    interaction: CollaborationInteraction
    purpose: str = Field(min_length=3, max_length=4_096)
    artifact_contracts: tuple[str, ...] = ()

    @model_validator(mode="after")
    def identities_are_distinct(self) -> CollaborationEdge:
        if self.source_identity == self.target_identity:
            raise ValueError("collaboration edge identities must be distinct")
        return self


class CollaborationHistoryCitation(TeamModel):
    project_id: str = Field(min_length=1, max_length=256)
    mission_id: UUID
    task_ids: tuple[str, ...] = Field(min_length=1)
    evaluation_ids: tuple[UUID, ...] = Field(min_length=1)
    comparability: EvidenceComparability
    rationale: str = Field(min_length=3, max_length=4_096)
    evaluated_at: datetime

    @field_validator("evaluated_at")
    @classmethod
    def evaluated_at_is_aware(cls, value: datetime) -> datetime:
        return require_aware(value)


class CollaborationHistorySelection(TeamModel):
    preferred: tuple[CollaborationHistoryCitation, ...]
    tie_breakers: tuple[CollaborationHistoryCitation, ...]
    ignored: tuple[CollaborationHistoryCitation, ...]
    rationale: tuple[str, ...] = Field(min_length=1)


class CollaborationHistorySelector:
    """Classify attributable history without creating an opaque ranking score."""

    @staticmethod
    def select(
        citations: tuple[CollaborationHistoryCitation, ...],
        *,
        freshness_days: int,
        now: datetime | None = None,
    ) -> CollaborationHistorySelection:
        if freshness_days < 1:
            raise ValueError("collaboration history freshness must be positive")
        observed_at = require_aware(now or utc_now())
        cutoff = observed_at - timedelta(days=freshness_days)
        preferred: list[CollaborationHistoryCitation] = []
        tie_breakers: list[CollaborationHistoryCitation] = []
        ignored: list[CollaborationHistoryCitation] = []
        for citation in citations:
            if citation.evaluated_at < cutoff or citation.comparability in {
                EvidenceComparability.STALE,
                EvidenceComparability.CONFLICTING,
                EvidenceComparability.MATERIALLY_DIFFERENT,
            }:
                ignored.append(citation)
            elif citation.comparability is EvidenceComparability.DIRECT:
                preferred.append(citation)
            else:
                tie_breakers.append(citation)
        return CollaborationHistorySelection(
            preferred=tuple(preferred),
            tie_breakers=tuple(tie_breakers),
            ignored=tuple(ignored),
            rationale=(
                "direct recent evidence may be preferred",
                "partial evidence is tie-breaker context only",
                "stale, conflicting, or materially different evidence is ignored",
                "current mission fit and required authority remain decisive",
            ),
        )


class MissionCollaborationPlan(TeamModel):
    schema_version: Literal["1.0"] = "1.0"
    plan_id: UUID = Field(default_factory=new_id)
    mission_id: UUID
    version: int = Field(ge=1)
    brief_version: int = Field(ge=1)
    crew_version: int = Field(ge=1)
    production_shape: ProductionShape
    production_owner_ids: tuple[str, ...] = Field(min_length=1)
    collaboration_reasons: tuple[str, ...] = ()
    proposal_author_identity: str = Field(min_length=2, max_length=128)
    crewai_coordination_reference: str = Field(min_length=1, max_length=1_024)
    alternatives: tuple[str, ...] = Field(min_length=1)
    shared_outcome: str = Field(min_length=3, max_length=8_192)
    user_impact: str = Field(min_length=3, max_length=8_192)
    responsibilities: tuple[CollaborationResponsibility, ...] = Field(min_length=1)
    interactions: tuple[CollaborationEdge, ...] = ()
    context_references: tuple[str, ...] = Field(min_length=1)
    artifact_references: tuple[str, ...] = ()
    resource_budgets: tuple[MissionResourceLimit, ...] = Field(min_length=1)
    communication_budget: MissionResourceLimit
    escalation_conditions: tuple[str, ...] = Field(min_length=1)
    permitted_adaptations: tuple[AdaptationKind, ...] = ()
    stop_conditions: tuple[str, ...] = Field(min_length=1)
    history_citations: tuple[CollaborationHistoryCitation, ...] = ()
    pm_confirmation_id: UUID
    cto_confirmation_id: UUID
    created_at: datetime = Field(default_factory=utc_now)

    @field_validator("created_at")
    @classmethod
    def timestamp_is_aware(cls, value: datetime) -> datetime:
        return require_aware(value)

    @model_validator(mode="after")
    def shape_and_edges_are_coherent(self) -> MissionCollaborationPlan:
        owners = self.production_owner_ids
        if len(owners) != len(set(owners)):
            raise ValueError("production owners must be unique")
        if (
            self.production_shape
            in {
                ProductionShape.SINGLE_OWNER,
                ProductionShape.CONSULTATIVE,
            }
            and len(owners) != 1
        ):
            raise ValueError("single-owner and consultative shapes require one production owner")
        if self.production_shape is ProductionShape.MULTI_PRODUCER and len(owners) < 2:
            raise ValueError("multi-producer shape requires at least two production owners")
        if self.production_shape is not ProductionShape.SINGLE_OWNER and not (
            self.collaboration_reasons
        ):
            raise ValueError("multi-agent production requires a collaboration rationale")
        if self.production_shape is ProductionShape.CONSULTATIVE and not any(
            edge.interaction
            in {CollaborationInteraction.CONSULT, CollaborationInteraction.CHALLENGE}
            for edge in self.interactions
        ):
            raise ValueError("consultative shape requires a consult or challenge edge")
        if self.production_shape is ProductionShape.MULTI_PRODUCER and not self.interactions:
            raise ValueError("multi-producer shape requires an explicit interaction graph")
        identities = {item.identity_id for item in self.responsibilities}
        if len(identities) != len(self.responsibilities):
            raise ValueError("collaboration responsibilities must be unique by identity")
        if not set(owners).issubset(identities):
            raise ValueError("every production owner requires an explicit responsibility")
        for edge in self.interactions:
            if edge.source_identity not in identities or edge.target_identity not in identities:
                raise ValueError("collaboration edge references an undeclared responsibility")
        if any(item not in BOUNDED_ADAPTATIONS for item in self.permitted_adaptations):
            raise ValueError("collaboration plan cannot pre-authorize a material adaptation")
        return self


class TeamDimensionAssessment(TeamModel):
    dimension: TeamDimension
    status: TeamHealthStatus
    observations: tuple[str, ...] = Field(min_length=1)
    evidence_references: tuple[str, ...] = Field(min_length=1)


class TeamHealthEvaluation(TeamModel):
    schema_version: Literal["1.0"] = "1.0"
    evaluation_id: UUID = Field(default_factory=new_id)
    mission_id: UUID
    collaboration_plan_version: int = Field(ge=1)
    interval_started_at: datetime
    interval_ended_at: datetime
    evidence_cursor: int = Field(ge=0)
    assessments: tuple[TeamDimensionAssessment, ...] = Field(min_length=7, max_length=7)
    strengths: tuple[str, ...]
    risks: tuple[str, ...]
    limitations: tuple[str, ...] = Field(min_length=1)
    confidence_basis: str = Field(min_length=3, max_length=4_096)
    status: TeamHealthStatus
    evaluator_identity: str = Field(min_length=2, max_length=128)
    evidence_references: tuple[str, ...] = Field(min_length=1)
    created_at: datetime = Field(default_factory=utc_now)

    @field_validator("interval_started_at", "interval_ended_at", "created_at")
    @classmethod
    def timestamps_are_aware(cls, value: datetime) -> datetime:
        return require_aware(value)

    @model_validator(mode="after")
    def interval_and_dimensions_are_complete(self) -> TeamHealthEvaluation:
        if self.interval_ended_at < self.interval_started_at:
            raise ValueError("team-health interval cannot end before it starts")
        dimensions = tuple(item.dimension for item in self.assessments)
        if len(set(dimensions)) != len(TeamDimension) or set(dimensions) != set(TeamDimension):
            raise ValueError("team-health evaluation requires every dimension exactly once")
        return self


class TeamHealthEvaluationRequest(TeamModel):
    schema_version: Literal["1.0"] = "1.0"
    request_id: UUID = Field(default_factory=new_id)
    mission_id: UUID
    collaboration_plan_version: int = Field(ge=1)
    interval_started_at: datetime
    interval_ended_at: datetime
    evidence_cursor: int = Field(ge=0)
    evaluator_identity: str = Field(min_length=2, max_length=128)
    evidence_references: tuple[str, ...] = Field(min_length=1)

    @field_validator("interval_started_at", "interval_ended_at")
    @classmethod
    def timestamps_are_aware(cls, value: datetime) -> datetime:
        return require_aware(value)

    @model_validator(mode="after")
    def interval_is_ordered(self) -> TeamHealthEvaluationRequest:
        if self.interval_ended_at < self.interval_started_at:
            raise ValueError("team-health request interval cannot end before it starts")
        return self


class CrewAdaptationDecision(TeamModel):
    schema_version: Literal["1.0"] = "1.0"
    decision_id: UUID = Field(default_factory=new_id)
    mission_id: UUID
    detected_condition: str = Field(min_length=3, max_length=8_192)
    considered_alternatives: tuple[str, ...] = Field(min_length=1)
    expected_benefit: str = Field(min_length=3, max_length=4_096)
    adaptation_kinds: tuple[AdaptationKind, ...] = Field(min_length=1)
    affected_task_ids: tuple[str, ...] = Field(min_length=1)
    affected_assignments: tuple[UUID, ...] = ()
    authority_basis: tuple[str, ...] = Field(min_length=1)
    disposition: AdaptationDisposition
    before_crew_version: int = Field(ge=1)
    after_crew_version: int = Field(ge=1)
    before_plan_version: int = Field(ge=1)
    after_plan_version: int = Field(ge=1)
    approval_references: tuple[str, ...] = ()
    evidence_references: tuple[str, ...] = Field(min_length=1)
    decided_by: str = Field(min_length=2, max_length=128)
    created_at: datetime = Field(default_factory=utc_now)

    @field_validator("created_at")
    @classmethod
    def timestamp_is_aware(cls, value: datetime) -> datetime:
        return require_aware(value)

    @model_validator(mode="after")
    def disposition_matches_authority_boundary(self) -> CrewAdaptationDecision:
        kinds = set(self.adaptation_kinds)
        material = bool(kinds - BOUNDED_ADAPTATIONS)
        if material and self.disposition is not AdaptationDisposition.APPROVAL_REQUIRED:
            raise ValueError("material adaptation requires executive approval")
        if self.disposition is AdaptationDisposition.APPROVAL_REQUIRED and not (
            self.approval_references
        ):
            raise ValueError("approval-required adaptation needs decision references")
        if self.disposition is AdaptationDisposition.BOUNDED:
            if self.approval_references:
                raise ValueError("bounded adaptation cannot claim executive approval")
            if self.after_crew_version != self.before_crew_version:
                raise ValueError("bounded adaptation cannot change crew membership revision")
        if self.after_plan_version < self.before_plan_version:
            raise ValueError("adaptation cannot move to an older collaboration plan")
        return self


class RetrospectiveProposal(TeamModel):
    proposal_id: UUID = Field(default_factory=new_id)
    destination: Literal["knowledge", "skill", "mission_template", "professional_competence"]
    summary: str = Field(min_length=3, max_length=8_192)
    evidence_references: tuple[str, ...] = Field(min_length=1)
    lifecycle_state: Literal["proposed"] = "proposed"


class MissionRetrospective(TeamModel):
    schema_version: Literal["1.0"] = "1.0"
    retrospective_id: UUID = Field(default_factory=new_id)
    mission_id: UUID
    collaboration_plan_version: int = Field(ge=1)
    trigger: Literal["mission_closure", "major_failure", "explicit_request"]
    expected_outcomes: tuple[str, ...] = Field(min_length=1)
    observed_outcomes: tuple[str, ...] = Field(min_length=1)
    successful_patterns: tuple[str, ...]
    failed_patterns: tuple[str, ...]
    contradictions: tuple[str, ...]
    rework: tuple[str, ...]
    coordination_costs: tuple[str, ...]
    proposals: tuple[RetrospectiveProposal, ...]
    non_promoted_findings: tuple[str, ...] = Field(min_length=1)
    evidence_references: tuple[str, ...] = Field(min_length=1)
    authored_by: str = Field(min_length=2, max_length=128)
    created_at: datetime = Field(default_factory=utc_now)

    @field_validator("created_at")
    @classmethod
    def timestamp_is_aware(cls, value: datetime) -> datetime:
        return require_aware(value)


TeamSignalKind = Literal[
    "blocked_time",
    "queue_time",
    "rejection",
    "rework",
    "duplicate_work",
    "overlapping_work",
    "missing_handoff",
    "repaired_handoff",
    "dependency_drift",
    "context_drift",
    "workload_concentration",
    "unacknowledged_risk",
    "coordination_cost",
    "model_cost",
    "tool_cost",
]


class TeamSignal(TeamModel):
    schema_version: Literal["1.0"] = "1.0"
    signal_id: UUID = Field(default_factory=new_id)
    mission_id: UUID
    collaboration_plan_version: int = Field(ge=1)
    kind: TeamSignalKind
    task_ids: tuple[str, ...]
    value: float = Field(ge=0)
    unit: str = Field(min_length=1, max_length=64)
    evidence_references: tuple[str, ...] = Field(min_length=1)
    observed_at: datetime = Field(default_factory=utc_now)

    @field_validator("observed_at")
    @classmethod
    def timestamp_is_aware(cls, value: datetime) -> datetime:
        return require_aware(value)


class MissionHandoff(TeamModel):
    schema_version: Literal["1.0"] = "1.0"
    handoff_id: UUID = Field(default_factory=new_id)
    mission_id: UUID
    collaboration_plan_version: int = Field(ge=1)
    from_identity: str = Field(min_length=2, max_length=128)
    to_identity: str = Field(min_length=2, max_length=128)
    task_id: str = Field(min_length=1, max_length=256)
    result_references: tuple[str, ...] = Field(min_length=1)
    artifact_references: tuple[str, ...] = Field(min_length=1)
    decisions: tuple[str, ...]
    changed_assumptions: tuple[str, ...]
    uncertainty: tuple[str, ...]
    failures: tuple[str, ...]
    unresolved_questions: tuple[str, ...]
    required_next_action: str = Field(min_length=3, max_length=8_192)
    acceptance_conditions: tuple[str, ...] = Field(min_length=1)
    created_at: datetime = Field(default_factory=utc_now)

    @field_validator("created_at")
    @classmethod
    def timestamp_is_aware(cls, value: datetime) -> datetime:
        return require_aware(value)

    @model_validator(mode="after")
    def identities_are_distinct(self) -> MissionHandoff:
        if self.from_identity == self.to_identity:
            raise ValueError("handoff producer and recipient must be distinct")
        return self


class TeamEffectivenessValidator:
    """Validate cross-contract invariants without performing model work."""

    @staticmethod
    def collaboration_plan(
        plan: MissionCollaborationPlan,
        *,
        brief: MissionBrief,
        crew: MissionCrewRevision,
    ) -> None:
        if plan.mission_id != brief.mission_id or plan.mission_id != crew.mission_id:
            raise MishkanError(ErrorCode.MISSION, "collaboration plan mission lineage differs")
        if plan.brief_version != brief.version or plan.crew_version != crew.version:
            raise MishkanError(
                ErrorCode.REVISION_MISMATCH,
                "collaboration plan does not reference current Brief and crew revisions",
            )
        member_ids = {item.identity_id for item in crew.members}
        referenced = {
            *(item.identity_id for item in plan.responsibilities),
            *(edge.source_identity for edge in plan.interactions),
            *(edge.target_identity for edge in plan.interactions),
        }
        if unknown := referenced - member_ids:
            raise MishkanError(
                ErrorCode.ROLE_CONFLICT,
                "collaboration plan references identities outside the Mission Crew",
                details={"unknown": sorted(unknown)},
            )
        if (
            plan.pm_confirmation_id != crew.pm_composition_confirmation_id
            or plan.cto_confirmation_id != crew.cto_coverage_confirmation_id
        ):
            raise MishkanError(
                ErrorCode.AUTHORITY_NOT_GRANTED,
                "collaboration plan lacks the current PM/CTO crew confirmation",
            )

    @staticmethod
    def health_evaluation(
        evaluation: TeamHealthEvaluation,
        *,
        plan: MissionCollaborationPlan,
    ) -> None:
        if evaluation.mission_id != plan.mission_id or (
            evaluation.collaboration_plan_version != plan.version
        ):
            raise MishkanError(ErrorCode.REVISION_MISMATCH, "health evaluation lineage is stale")
        if evaluation.evaluator_identity in plan.production_owner_ids:
            raise MishkanError(
                ErrorCode.ROLE_CONFLICT,
                "a production owner cannot independently evaluate team health",
            )
        responsibilities = {
            item.identity_id: item.decision_rights for item in plan.responsibilities
        }
        evaluator_rights = responsibilities.get(evaluation.evaluator_identity, ())
        if not {"assignment:evaluation", "assignment:audit"}.intersection(evaluator_rights):
            raise MishkanError(
                ErrorCode.ROLE_CONFLICT,
                "team-health evaluator lacks an independent evaluation responsibility",
            )

    @staticmethod
    def adaptation(
        decision: CrewAdaptationDecision,
        *,
        plan: MissionCollaborationPlan,
    ) -> None:
        if decision.mission_id != plan.mission_id or decision.before_plan_version != plan.version:
            raise MishkanError(ErrorCode.REVISION_MISMATCH, "adaptation starts from stale plan")
        if decision.disposition is AdaptationDisposition.BOUNDED and not set(
            decision.adaptation_kinds
        ).issubset(set(plan.permitted_adaptations)):
            raise MishkanError(
                ErrorCode.AUTHORITY_NOT_GRANTED,
                "bounded adaptation is not pre-authorized by the collaboration plan",
            )

    @staticmethod
    def retrospective(
        record: MissionRetrospective,
        *,
        plan: MissionCollaborationPlan,
    ) -> None:
        if record.mission_id != plan.mission_id or (
            record.collaboration_plan_version != plan.version
        ):
            raise MishkanError(ErrorCode.REVISION_MISMATCH, "retrospective lineage is stale")
        responsibilities = {
            item.identity_id: item.decision_rights for item in plan.responsibilities
        }
        author_rights = responsibilities.get(record.authored_by, ())
        if not {"assignment:reporting", "assignment:audit"}.intersection(author_rights):
            raise MishkanError(
                ErrorCode.ROLE_CONFLICT,
                "mission retrospective requires an independent reporting or audit identity",
            )


class TeamSignalDetector:
    """Derive cheap, attributable coordination signals without model inference."""

    @classmethod
    def detect(
        cls,
        plan: MissionCollaborationPlan,
        assignments: tuple[MissionTaskAssignment, ...],
        handoffs: tuple[MissionHandoff, ...],
        *,
        completed_task_ids: frozenset[str] = frozenset(),
        workload_concentration_threshold: float = 0.8,
        duplicate_work_threshold: float = 1,
        missing_handoff_threshold: float = 1,
        observed_at: datetime | None = None,
    ) -> tuple[TeamSignal, ...]:
        if not 0 <= workload_concentration_threshold <= 1:
            raise ValueError("workload concentration threshold must be between zero and one")
        if duplicate_work_threshold < 0 or missing_handoff_threshold < 0:
            raise ValueError("team-signal occurrence thresholds must be non-negative")
        latest = MissionAssignmentGraphValidator.latest(assignments)
        timestamp = require_aware(observed_at or utc_now())
        signals: list[TeamSignal] = []

        stale = tuple(item for item in latest.values() if item.crew_version != plan.crew_version)
        if stale:
            signals.append(
                cls._signal(
                    plan,
                    "context_drift",
                    tuple(item.task_id for item in stale),
                    float(len(stale)),
                    "assignment",
                    tuple(f"assignment:{item.assignment_id}" for item in stale),
                    timestamp,
                )
            )

        production = tuple(
            item
            for item in latest.values()
            if item.assignment_kind
            in {CrewAssignmentKind.PRODUCTION, CrewAssignmentKind.CONTRIBUTOR}
        )
        if len(production) > 1:
            owner_counts: dict[str, int] = {}
            for item in production:
                owner_counts[item.accountable_owner] = (
                    owner_counts.get(item.accountable_owner, 0) + 1
                )
            owner, count = max(owner_counts.items(), key=lambda item: (item[1], item[0]))
            ratio = count / len(production)
            if ratio >= workload_concentration_threshold:
                concentrated = tuple(item for item in production if item.accountable_owner == owner)
                signals.append(
                    cls._signal(
                        plan,
                        "workload_concentration",
                        tuple(item.task_id for item in concentrated),
                        ratio,
                        "ratio",
                        tuple(f"assignment:{item.assignment_id}" for item in concentrated),
                        timestamp,
                    )
                )

        duplicate_tasks: set[str] = set()
        duplicate_evidence: set[str] = set()
        duplicate_pairs = 0
        for index, left in enumerate(production):
            for right in production[index + 1 :]:
                if (
                    left.expected_result.casefold().strip()
                    == right.expected_result.casefold().strip()
                    and set(left.path_scopes).intersection(right.path_scopes)
                ):
                    duplicate_pairs += 1
                    duplicate_tasks.update((left.task_id, right.task_id))
                    duplicate_evidence.update(
                        (f"assignment:{left.assignment_id}", f"assignment:{right.assignment_id}")
                    )
        if duplicate_tasks and duplicate_pairs >= duplicate_work_threshold:
            signals.append(
                cls._signal(
                    plan,
                    "duplicate_work",
                    tuple(sorted(duplicate_tasks)),
                    float(duplicate_pairs),
                    "pair",
                    tuple(sorted(duplicate_evidence)),
                    timestamp,
                )
            )

        recorded = {(item.task_id, item.from_identity, item.to_identity) for item in handoffs}
        missing: list[tuple[str, CollaborationEdge, MissionTaskAssignment]] = []
        for task_id in sorted(completed_task_ids):
            assignment = latest.get(task_id)
            if assignment is None:
                continue
            for edge in plan.interactions:
                if (
                    edge.interaction is CollaborationInteraction.HANDOFF
                    and edge.source_identity == assignment.accountable_owner
                    and (task_id, edge.source_identity, edge.target_identity) not in recorded
                ):
                    missing.append((task_id, edge, assignment))
        if missing and len(missing) >= missing_handoff_threshold:
            signals.append(
                cls._signal(
                    plan,
                    "missing_handoff",
                    tuple(item[0] for item in missing),
                    float(len(missing)),
                    "handoff",
                    tuple(
                        f"assignment:{item[2].assignment_id}:handoff:{item[1].target_identity}"
                        for item in missing
                    ),
                    timestamp,
                )
            )
        return tuple(signals)

    @classmethod
    def detect_events(
        cls,
        plan: MissionCollaborationPlan,
        events: tuple[EventEnvelope, ...],
        task_mapping: dict[str, str],
        *,
        signal_thresholds: dict[str, float],
    ) -> tuple[TeamSignal, ...]:
        """Derive flow/rework signals from immutable run events at milestone time."""
        if not events:
            return ()
        ordered = tuple(sorted(events, key=lambda item: item.cursor))
        observed_at = max(item.occurred_at for item in ordered)
        signals: list[TeamSignal] = []

        rejected = tuple(
            item for item in ordered if item.event_type in {"task.review_rejected", "task.rejected"}
        )
        if len(rejected) >= signal_thresholds["repeated_rejections"]:
            signals.append(
                cls._event_signal(
                    plan,
                    "rejection",
                    rejected,
                    task_mapping,
                    float(len(rejected)),
                    "occurrence",
                    observed_at,
                )
            )

        reworked = tuple(
            item
            for item in ordered
            if item.event_type == "task.claimed"
            and isinstance(item.payload.get("attempt"), int)
            and int(item.payload["attempt"]) > 1
        )
        if reworked:
            signals.append(
                cls._event_signal(
                    plan,
                    "rework",
                    reworked,
                    task_mapping,
                    float(len(reworked)),
                    "attempt",
                    observed_at,
                )
            )

        queue_seconds, queue_evidence = cls._elapsed(
            ordered,
            start_type="run.queued",
            end_types={"run.running", "run.failed", "run.cancelled"},
        )
        if queue_seconds >= signal_thresholds["queue_time_seconds"] and queue_evidence:
            signals.append(
                cls._event_signal(
                    plan,
                    "queue_time",
                    queue_evidence,
                    task_mapping,
                    queue_seconds,
                    "seconds",
                    observed_at,
                )
            )

        blocked_seconds, blocked_evidence = cls._elapsed(
            ordered,
            start_type="run.blocked",
            end_types={"run.running", "run.failed", "run.cancelled"},
        )
        if blocked_seconds >= signal_thresholds["blocked_time_seconds"] and blocked_evidence:
            signals.append(
                cls._event_signal(
                    plan,
                    "blocked_time",
                    blocked_evidence,
                    task_mapping,
                    blocked_seconds,
                    "seconds",
                    observed_at,
                )
            )
        return tuple(signals)

    @classmethod
    def _event_signal(
        cls,
        plan: MissionCollaborationPlan,
        kind: TeamSignalKind,
        events: tuple[EventEnvelope, ...],
        task_mapping: dict[str, str],
        value: float,
        unit: str,
        observed_at: datetime,
    ) -> TeamSignal:
        task_ids = tuple(
            dict.fromkeys(
                task_mapping[task_id]
                for event in events
                if (
                    task_id := event.task_id
                    or (
                        str(event.payload["task_id"])
                        if event.payload.get("task_id") is not None
                        else None
                    )
                )
                and task_id in task_mapping
            )
        )
        return cls._signal(
            plan,
            kind,
            task_ids,
            value,
            unit,
            tuple(f"event:{item.event_id}" for item in events),
            observed_at,
        )

    @staticmethod
    def _elapsed(
        events: tuple[EventEnvelope, ...],
        *,
        start_type: str,
        end_types: set[str],
    ) -> tuple[float, tuple[EventEnvelope, ...]]:
        pending: dict[str, EventEnvelope] = {}
        elapsed = 0.0
        evidence: list[EventEnvelope] = []
        for event in events:
            run_id = event.run_id or event.entity_id
            if event.event_type == start_type:
                pending[run_id] = event
            elif event.event_type in end_types and run_id in pending:
                started = pending.pop(run_id)
                elapsed += max(0.0, (event.occurred_at - started.occurred_at).total_seconds())
                evidence.extend((started, event))
        return elapsed, tuple(evidence)

    @staticmethod
    def _signal(
        plan: MissionCollaborationPlan,
        kind: TeamSignalKind,
        task_ids: tuple[str, ...],
        value: float,
        unit: str,
        evidence_references: tuple[str, ...],
        observed_at: datetime,
    ) -> TeamSignal:
        identity = "|".join(
            (
                str(plan.mission_id),
                str(plan.version),
                kind,
                *task_ids,
                *evidence_references,
                observed_at.isoformat(),
            )
        )
        return TeamSignal(
            signal_id=uuid5(NAMESPACE_URL, f"mishkan:team-signal:{identity}"),
            mission_id=plan.mission_id,
            collaboration_plan_version=plan.version,
            kind=kind,
            task_ids=task_ids,
            value=value,
            unit=unit,
            evidence_references=evidence_references,
            observed_at=observed_at,
        )
