"""Transactional SQLite authority for organizations, missions, Briefs, and crews."""

from __future__ import annotations

import hashlib
import json
from typing import TypeVar

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from mishkan.domain.errors import ErrorCode, MishkanError
from mishkan.domain.identity import new_id
from mishkan.domain.time import utc_now
from mishkan.missions.assignment_graph import MissionAssignmentGraphValidator
from mishkan.missions.environment import (
    MissionEnvironmentPlanAcceptance,
)
from mishkan.missions.models import (
    AssignmentChangeKind,
    CrewAssignmentKind,
    MissionBrief,
    MissionBriefStatus,
    MissionCrewRevision,
    MissionRecord,
    MissionRunAcceptance,
    MissionRunBinding,
    MissionRunReport,
    MissionState,
    MissionTaskAssignment,
    MissionTransition,
)
from mishkan.organization.models import OrganizationRosterDefinition
from mishkan.persistence.migration import SchemaManager
from mishkan.persistence.runtime import DatabaseTarget
from mishkan.persistence.sqlite import (
    AcceptanceRow,
    MissionAssignmentRow,
    MissionBriefRow,
    MissionCrewRow,
    MissionEnvironmentPlanRow,
    MissionRow,
    MissionRunBindingRow,
    MissionRunReportRow,
    MissionTransitionRow,
    OrganizationRosterRow,
    OutboxRow,
    PlanRow,
    ResultRow,
    ReviewRejectionRow,
    RunRow,
    create_local_engine,
)
from mishkan.planning.models import AcceptedPlan, PlanExecutionContext, ReviewDecision
from mishkan.tools.models import EffectClass

RecordT = TypeVar("RecordT", bound=BaseModel)

_MISSION_TRANSITIONS: dict[MissionState, frozenset[MissionState]] = {
    MissionState.PROPOSED: frozenset({MissionState.CLARIFYING, MissionState.CANCELLED}),
    MissionState.CLARIFYING: frozenset(
        {MissionState.PLANNED, MissionState.BLOCKED, MissionState.CANCELLED}
    ),
    MissionState.PLANNED: frozenset(
        {MissionState.ACTIVE, MissionState.PAUSED, MissionState.BLOCKED, MissionState.CANCELLED}
    ),
    MissionState.ACTIVE: frozenset(
        {
            MissionState.PAUSED,
            MissionState.BLOCKED,
            MissionState.EVALUATING,
            MissionState.FAILED,
            MissionState.CANCELLED,
        }
    ),
    MissionState.PAUSED: frozenset(
        {MissionState.ACTIVE, MissionState.BLOCKED, MissionState.CANCELLED}
    ),
    MissionState.BLOCKED: frozenset(
        {MissionState.ACTIVE, MissionState.PAUSED, MissionState.FAILED, MissionState.CANCELLED}
    ),
    MissionState.EVALUATING: frozenset(
        {
            MissionState.REMEDIATING,
            MissionState.COMPLETED,
            MissionState.FAILED,
            MissionState.PAUSED,
        }
    ),
    MissionState.REMEDIATING: frozenset(
        {
            MissionState.ACTIVE,
            MissionState.EVALUATING,
            MissionState.FAILED,
            MissionState.CANCELLED,
        }
    ),
}


class SQLiteMissionRepository:
    """Persist immutable revisions and update only the mission's current projection."""

    def __init__(self, database_path: DatabaseTarget, *, busy_timeout_ms: int = 5_000) -> None:
        SchemaManager(database_path).require_current()
        self._engine = create_local_engine(database_path, busy_timeout_ms=busy_timeout_ms)

    def record_organization(
        self,
        organization: OrganizationRosterDefinition,
        *,
        emit_event: bool = True,
    ) -> OrganizationRosterDefinition:
        payload = self._json(organization)
        fingerprint = hashlib.sha256(payload.encode()).hexdigest()
        key = (organization.organization_id, organization.organization_version)
        with Session(self._engine) as session, session.begin():
            existing = session.get(OrganizationRosterRow, key)
            if existing is not None:
                return self._idempotent(existing.payload, payload, organization)
            session.add(
                OrganizationRosterRow(
                    organization_id=organization.organization_id,
                    organization_version=organization.organization_version,
                    fingerprint=fingerprint,
                    payload=payload,
                    recorded_at=utc_now().isoformat(),
                )
            )
            if emit_event:
                self._event(
                    session,
                    aggregate_id=organization.organization_id,
                    entity_type="organization",
                    event_type="organization.roster_recorded",
                    payload={
                        "organization_id": organization.organization_id,
                        "organization_version": organization.organization_version,
                        "fingerprint": fingerprint,
                        "identity_count": len(organization.identities),
                    },
                )
        return organization

    def create_mission(self, record: MissionRecord) -> MissionRecord:
        if record.revision != 0 or record.state is not MissionState.PROPOSED:
            raise MishkanError(
                ErrorCode.MISSION,
                "new mission must begin as an unpersisted proposed record",
            )
        durable = record.model_copy(update={"revision": 1})
        payload = self._json(durable)
        with Session(self._engine) as session, session.begin():
            self._require_organization(
                session,
                durable.organization_id,
                durable.organization_version,
            )
            existing = session.get(MissionRow, str(durable.mission_id))
            if existing is not None:
                return self._idempotent(existing.payload, payload, durable)
            session.add(
                MissionRow(
                    id=str(durable.mission_id),
                    state=durable.state.value,
                    revision=durable.revision,
                    organization_id=durable.organization_id,
                    organization_version=durable.organization_version,
                    current_brief_version=None,
                    current_crew_version=None,
                    current_environment_plan_version=None,
                    payload=payload,
                    created_at=durable.created_at.isoformat(),
                    updated_at=durable.updated_at.isoformat(),
                )
            )
            self._event(
                session,
                aggregate_id=str(durable.mission_id),
                entity_type="mission",
                event_type="mission.proposed",
                payload={
                    "mission_id": str(durable.mission_id),
                    "origin_kind": durable.origin.kind.value,
                    "actor_id": durable.origin.actor_id,
                    "revision": durable.revision,
                },
            )
        return durable

    def record_brief(self, brief: MissionBrief, *, expected_revision: int) -> MissionBrief:
        payload = self._json(brief)
        with Session(self._engine) as session, session.begin():
            existing = session.get(MissionBriefRow, str(brief.brief_id))
            if existing is not None:
                return self._idempotent(existing.payload, payload, brief)
            mission_row = self._require_mission_row(session, str(brief.mission_id))
            mission = MissionRecord.model_validate_json(mission_row.payload)
            self._require_revision(mission, expected_revision)
            if mission.state in {
                MissionState.COMPLETED,
                MissionState.FAILED,
                MissionState.CANCELLED,
            }:
                raise MishkanError(ErrorCode.MISSION, "terminal mission cannot accept a new Brief")
            if (
                brief.organization_id != mission.organization_id
                or brief.organization_version != mission.organization_version
            ):
                raise MishkanError(ErrorCode.MISSION, "Mission Brief organization does not match")
            expected_version = (mission.current_brief_version or 0) + 1
            if brief.version != expected_version:
                raise MishkanError(
                    ErrorCode.REVISION_MISMATCH,
                    "Mission Brief version is stale or skips a revision",
                    details={"expected": expected_version, "received": brief.version},
                )
            roster = self._require_organization(
                session,
                mission.organization_id,
                mission.organization_version,
            )
            known = {identity.identity_id for identity in roster.identities}
            unknown = set(brief.proposed_crew) - known
            if unknown:
                raise MishkanError(
                    ErrorCode.MISSION,
                    "Mission Brief proposes unknown professional identities",
                    details={"unknown": sorted(unknown)},
                )
            version_row = session.scalar(
                select(MissionBriefRow).where(
                    MissionBriefRow.mission_id == str(brief.mission_id),
                    MissionBriefRow.version == brief.version,
                )
            )
            if version_row is not None:
                raise MishkanError(
                    ErrorCode.DUPLICATE_RESULT,
                    "Mission Brief version already has another immutable identity",
                )
            session.add(
                MissionBriefRow(
                    brief_id=str(brief.brief_id),
                    mission_id=str(brief.mission_id),
                    version=brief.version,
                    status=brief.status.value,
                    fingerprint=brief.fingerprint,
                    payload=payload,
                    created_at=brief.created_at.isoformat(),
                )
            )
            updated = mission.model_copy(
                update={
                    "revision": mission.revision + 1,
                    "state": MissionState.CLARIFYING,
                    "current_brief_version": brief.version,
                    "updated_at": utc_now(),
                }
            )
            self._update_mission_row(mission_row, updated)
            self._event(
                session,
                aggregate_id=str(brief.mission_id),
                entity_type="mission",
                event_type="mission.brief_recorded",
                payload={
                    "mission_id": str(brief.mission_id),
                    "brief_id": str(brief.brief_id),
                    "brief_version": brief.version,
                    "status": brief.status.value,
                    "fingerprint": brief.fingerprint,
                    "revision": updated.revision,
                },
            )
        return brief

    def record_crew(
        self, crew: MissionCrewRevision, *, expected_revision: int
    ) -> MissionCrewRevision:
        payload = self._json(crew)
        with Session(self._engine) as session, session.begin():
            existing = session.get(MissionCrewRow, str(crew.crew_id))
            if existing is not None:
                return self._idempotent(existing.payload, payload, crew)
            mission_row = self._require_mission_row(session, str(crew.mission_id))
            mission = MissionRecord.model_validate_json(mission_row.payload)
            self._require_revision(mission, expected_revision)
            if crew.organization_id != mission.organization_id or (
                crew.organization_version != mission.organization_version
            ):
                raise MishkanError(ErrorCode.MISSION, "Mission Crew organization does not match")
            if crew.brief_version != mission.current_brief_version:
                raise MishkanError(
                    ErrorCode.REVISION_MISMATCH,
                    "Mission Crew does not reference the current Mission Brief",
                )
            brief = self._brief_row(session, str(crew.mission_id), crew.brief_version)
            if brief.status is not MissionBriefStatus.CONFIRMED:
                raise MishkanError(
                    ErrorCode.MISSION,
                    "Mission Crew requires a jointly confirmed Mission Brief",
                )
            if brief.pm_confirmation is None or brief.cto_confirmation is None:
                raise MishkanError(ErrorCode.MISSION, "Mission Brief confirmations are incomplete")
            if crew.pm_composition_confirmation_id != brief.pm_confirmation.confirmation_id or (
                crew.cto_coverage_confirmation_id != brief.cto_confirmation.confirmation_id
            ):
                raise MishkanError(
                    ErrorCode.MISSION,
                    "Mission Crew confirmation lineage does not match its Brief",
                )
            expected_version = (mission.current_crew_version or 0) + 1
            if crew.version != expected_version:
                raise MishkanError(
                    ErrorCode.REVISION_MISMATCH,
                    "Mission Crew version is stale or skips a revision",
                    details={"expected": expected_version, "received": crew.version},
                )
            if {member.identity_id for member in crew.members} != set(brief.proposed_crew):
                raise MishkanError(
                    ErrorCode.MISSION,
                    "Mission Crew must match the current PM/CTO-confirmed composition",
                )
            roster = self._require_organization(
                session,
                mission.organization_id,
                mission.organization_version,
            )
            identities = {identity.identity_id: identity for identity in roster.identities}
            for member in crew.members:
                identity = identities.get(member.identity_id)
                if identity is None:
                    raise MishkanError(ErrorCode.MISSION, "Mission Crew identity is unknown")
                if (
                    member.identity_id == crew.mission_lead_id
                    and not identity.mission_lead_eligible
                ):
                    raise MishkanError(
                        ErrorCode.ROLE_CONFLICT,
                        "selected professional identity cannot hold Mission Lead responsibility",
                    )
                self._require_assignment_authority(
                    member.assignment_kind,
                    identity.authority_limits,
                )
            version_row = session.scalar(
                select(MissionCrewRow).where(
                    MissionCrewRow.mission_id == str(crew.mission_id),
                    MissionCrewRow.version == crew.version,
                )
            )
            if version_row is not None:
                raise MishkanError(
                    ErrorCode.DUPLICATE_RESULT,
                    "Mission Crew version already has another immutable identity",
                )
            session.add(
                MissionCrewRow(
                    crew_id=str(crew.crew_id),
                    mission_id=str(crew.mission_id),
                    version=crew.version,
                    brief_version=crew.brief_version,
                    fingerprint=crew.fingerprint,
                    payload=payload,
                    created_at=crew.created_at.isoformat(),
                )
            )
            updated = mission.model_copy(
                update={
                    "revision": mission.revision + 1,
                    "current_crew_version": crew.version,
                    "updated_at": utc_now(),
                }
            )
            self._update_mission_row(mission_row, updated)
            self._event(
                session,
                aggregate_id=str(crew.mission_id),
                entity_type="mission",
                event_type="mission.crew_recorded",
                payload={
                    "mission_id": str(crew.mission_id),
                    "crew_id": str(crew.crew_id),
                    "crew_version": crew.version,
                    "brief_version": crew.brief_version,
                    "mission_lead_id": crew.mission_lead_id,
                    "fingerprint": crew.fingerprint,
                    "revision": updated.revision,
                },
            )
        return crew

    def record_assignment(self, assignment: MissionTaskAssignment) -> MissionTaskAssignment:
        payload = self._json(assignment)
        with Session(self._engine) as session, session.begin():
            existing = session.get(MissionAssignmentRow, str(assignment.assignment_id))
            if existing is not None:
                return self._idempotent(existing.payload, payload, assignment)
            mission = MissionRecord.model_validate_json(
                self._require_mission_row(session, str(assignment.mission_id)).payload
            )
            if assignment.crew_version != mission.current_crew_version:
                raise MishkanError(
                    ErrorCode.REVISION_MISMATCH,
                    "task assignment does not reference the current Mission Crew",
                )
            crew = self._crew_row(session, str(assignment.mission_id), assignment.crew_version)
            brief = self._brief_row(session, str(assignment.mission_id), crew.brief_version)
            if assignment.environment_context_ids and not (
                brief.environment_intent.environment_dependent
            ):
                raise MishkanError(
                    ErrorCode.PLAN,
                    "task cannot declare environment contexts when the Brief has no "
                    "environment-dependent work",
                )
            members = {member.identity_id: member for member in crew.members}
            assigned = {assignment.accountable_owner, *assignment.contributors}
            unknown = assigned - set(members)
            if unknown:
                raise MishkanError(
                    ErrorCode.MISSION,
                    "task assignment contains identities outside the current Mission Crew",
                    details={"unknown": sorted(unknown)},
                )
            owner = members[assignment.accountable_owner]
            if owner.assignment_kind is not assignment.assignment_kind:
                raise MishkanError(
                    ErrorCode.ROLE_CONFLICT,
                    "task responsibility differs from its accountable crew assignment",
                    details={
                        "task_id": assignment.task_id,
                        "task_kind": assignment.assignment_kind.value,
                        "crew_kind": owner.assignment_kind.value,
                    },
                )
            allowed_contributor_kinds = {
                CrewAssignmentKind.PRODUCTION: {
                    CrewAssignmentKind.PRODUCTION,
                    CrewAssignmentKind.CONTRIBUTOR,
                },
                CrewAssignmentKind.CONTRIBUTOR: {
                    CrewAssignmentKind.PRODUCTION,
                    CrewAssignmentKind.CONTRIBUTOR,
                },
                CrewAssignmentKind.EVALUATION: {
                    CrewAssignmentKind.EVALUATION,
                    CrewAssignmentKind.AUDIT,
                },
                CrewAssignmentKind.REPORTING: {CrewAssignmentKind.REPORTING},
                CrewAssignmentKind.AUDIT: {
                    CrewAssignmentKind.EVALUATION,
                    CrewAssignmentKind.AUDIT,
                },
            }[assignment.assignment_kind]
            conflicting_contributors = sorted(
                identity_id
                for identity_id in assignment.contributors
                if members[identity_id].assignment_kind not in allowed_contributor_kinds
            )
            if conflicting_contributors:
                raise MishkanError(
                    ErrorCode.ROLE_CONFLICT,
                    "task contributors violate responsibility separation",
                    details={
                        "task_id": assignment.task_id,
                        "conflicting_identity_ids": conflicting_contributors,
                    },
                )
            prior_row = session.scalar(
                select(MissionAssignmentRow)
                .where(
                    MissionAssignmentRow.mission_id == str(assignment.mission_id),
                    MissionAssignmentRow.task_id == assignment.task_id,
                )
                .order_by(MissionAssignmentRow.assignment_revision.desc())
                .limit(1)
            )
            expected_revision = 1 + (prior_row.assignment_revision if prior_row is not None else 0)
            if assignment.assignment_revision != expected_revision:
                raise MishkanError(
                    ErrorCode.REVISION_MISMATCH,
                    "task assignment revision is stale or skips a revision",
                    details={
                        "expected": expected_revision,
                        "received": assignment.assignment_revision,
                    },
                )
            if prior_row is not None:
                prior = MissionTaskAssignment.model_validate_json(prior_row.payload)
                self._require_assignment_change_governance(assignment, prior, crew)
            session.add(
                MissionAssignmentRow(
                    id=str(assignment.assignment_id),
                    mission_id=str(assignment.mission_id),
                    task_id=assignment.task_id,
                    assignment_revision=assignment.assignment_revision,
                    accountable_owner=assignment.accountable_owner,
                    payload=payload,
                    created_at=assignment.created_at.isoformat(),
                )
            )
            self._event(
                session,
                aggregate_id=str(assignment.mission_id),
                entity_type="mission",
                event_type="mission.task_assigned",
                payload={
                    "assignment_id": str(assignment.assignment_id),
                    "task_id": assignment.task_id,
                    "assignment_revision": assignment.assignment_revision,
                    "accountable_owner": assignment.accountable_owner,
                    "crew_version": assignment.crew_version,
                    "change_kind": (
                        assignment.change.change_kind.value
                        if assignment.change is not None
                        else None
                    ),
                    "prior_assignment_id": (
                        str(assignment.change.prior_assignment_id)
                        if assignment.change is not None
                        else None
                    ),
                },
            )
        return assignment

    @staticmethod
    def _require_assignment_change_governance(
        assignment: MissionTaskAssignment,
        prior: MissionTaskAssignment,
        crew: MissionCrewRevision,
    ) -> None:
        change = assignment.change
        if change is None:
            raise MishkanError(
                ErrorCode.MISSION,
                "revised assignment is missing its governed change lineage",
            )
        if (
            change.prior_assignment_id != prior.assignment_id
            or change.prior_assignment_revision != prior.assignment_revision
        ):
            raise MishkanError(
                ErrorCode.REVISION_MISMATCH,
                "assignment change does not reference the exact prior assignment",
            )
        if change.requested_by_identity != crew.mission_lead_id:
            raise MishkanError(
                ErrorCode.AUTHORITY_NOT_GRANTED,
                "only the current Mission Lead may request an assignment change",
            )
        contract_fields = (
            "task_id",
            "assignment_kind",
            "expected_result",
            "completion_criteria",
            "dependencies",
            "execution_run_id",
            "execution_task_id",
            "environment_context_ids",
            "authority_scope",
            "exact_tools",
            "path_scopes",
            "limits",
            "required_evidence",
            "requires_independent_evaluation",
        )
        contract_changed = any(
            getattr(prior, field) != getattr(assignment, field) for field in contract_fields
        )
        if contract_changed and change.change_kind is not AssignmentChangeKind.REPLANNED:
            raise MishkanError(
                ErrorCode.PLAN,
                "assignment change outside the accepted task contract requires replanning",
            )
        owner_or_composition_changed = (
            prior.accountable_owner != assignment.accountable_owner
            or prior.crew_version != assignment.crew_version
        )
        if owner_or_composition_changed:
            if change.change_kind is AssignmentChangeKind.IN_PLAN_LOCAL:
                raise MishkanError(
                    ErrorCode.AUTHORITY_NOT_GRANTED,
                    "local Mission Lead authority cannot change formal ownership or composition",
                )
            cto = change.cto_coverage_confirmation
            pm = change.pm_reassignment_confirmation
            if cto is None or pm is None:
                raise MishkanError(
                    ErrorCode.AUTHORITY_NOT_GRANTED,
                    "formal reassignment requires CTO coverage then PM confirmation",
                )
            coverage = {item.lower() for item in cto.coverage}
            if not all(
                any(required in item for item in coverage)
                for required in ("technical", "security", "quality")
            ):
                raise MishkanError(
                    ErrorCode.ROLE_CONFLICT,
                    "CTO reassignment confirmation omits technical, security, or quality coverage",
                )
            if cto.confirmed_at > pm.confirmed_at:
                raise MishkanError(
                    ErrorCode.AUTHORITY_NOT_GRANTED,
                    "PM reassignment confirmation must follow CTO coverage confirmation",
                )
        elif change.change_kind is AssignmentChangeKind.FORMAL_REASSIGNMENT:
            raise MishkanError(
                ErrorCode.MISSION,
                "formal reassignment must change accountable ownership or crew composition",
            )
        if (
            not contract_changed
            and not owner_or_composition_changed
            and (prior.contributors == assignment.contributors)
        ):
            raise MishkanError(ErrorCode.MISSION, "assignment revision does not change anything")

    def transition(
        self,
        transition: MissionTransition,
        *,
        expected_revision: int,
    ) -> MissionTransition:
        payload = self._json(transition)
        with Session(self._engine) as session, session.begin():
            existing = session.get(MissionTransitionRow, str(transition.transition_id))
            if existing is not None:
                return self._idempotent(existing.payload, payload, transition)
            mission_row = self._require_mission_row(session, str(transition.mission_id))
            mission = MissionRecord.model_validate_json(mission_row.payload)
            self._require_revision(mission, expected_revision)
            if transition.from_state is not mission.state:
                raise MishkanError(
                    ErrorCode.REVISION_MISMATCH,
                    "mission transition starts from a stale state",
                    details={
                        "expected": mission.state.value,
                        "received": transition.from_state.value,
                    },
                )
            allowed = _MISSION_TRANSITIONS.get(mission.state, frozenset())
            if transition.to_state not in allowed:
                raise MishkanError(
                    ErrorCode.MISSION,
                    "mission lifecycle transition is not permitted",
                    details={
                        "from": mission.state.value,
                        "to": transition.to_state.value,
                    },
                )
            if transition.to_state not in {
                MissionState.CLARIFYING,
                MissionState.CANCELLED,
            } and (mission.current_brief_version is None or mission.current_crew_version is None):
                raise MishkanError(
                    ErrorCode.MISSION,
                    "mission cannot advance without a current Brief and Mission Crew",
                )
            if transition.to_state is MissionState.ACTIVE:
                assert mission.current_crew_version is not None
                current_crew = self._crew_row(
                    session,
                    str(mission.mission_id),
                    mission.current_crew_version,
                )
                if current_crew.brief_version != mission.current_brief_version:
                    raise MishkanError(
                        ErrorCode.REVISION_MISMATCH,
                        "current Mission Crew does not reference the current Mission Brief",
                    )
                assignment_rows = session.scalars(
                    select(MissionAssignmentRow).where(
                        MissionAssignmentRow.mission_id == str(mission.mission_id)
                    )
                ).all()
                assignments = tuple(
                    MissionTaskAssignment.model_validate_json(row.payload)
                    for row in assignment_rows
                )
                latest = MissionAssignmentGraphValidator.latest(assignments)
                stale = sorted(
                    assignment.task_id
                    for assignment in latest.values()
                    if assignment.crew_version != mission.current_crew_version
                )
                if stale:
                    raise MishkanError(
                        ErrorCode.REVISION_MISMATCH,
                        "mission task assignments do not reference the current Mission Crew",
                        details={"task_ids": stale},
                    )
                MissionAssignmentGraphValidator.validate(assignments)
            updated = mission.model_copy(
                update={
                    "state": transition.to_state,
                    "revision": mission.revision + 1,
                    "updated_at": utc_now(),
                }
            )
            self._update_mission_row(mission_row, updated)
            session.add(
                MissionTransitionRow(
                    id=str(transition.transition_id),
                    mission_id=str(transition.mission_id),
                    from_state=transition.from_state.value,
                    to_state=transition.to_state.value,
                    payload=payload,
                    created_at=transition.created_at.isoformat(),
                )
            )
            self._event(
                session,
                aggregate_id=str(transition.mission_id),
                entity_type="mission",
                event_type="mission.state_transitioned",
                payload={
                    "transition_id": str(transition.transition_id),
                    "from_state": transition.from_state.value,
                    "to_state": transition.to_state.value,
                    "actor_or_cause": transition.actor_or_cause,
                    "reason": transition.reason,
                    "decision_id": (
                        str(transition.decision_id) if transition.decision_id is not None else None
                    ),
                    "affected_scope": list(transition.affected_scope),
                    "evidence_references": list(transition.evidence_references),
                    "revision": updated.revision,
                },
            )
        return transition

    def assignments(
        self, mission_id: str, *, limit: int = 1_000
    ) -> tuple[MissionTaskAssignment, ...]:
        with Session(self._engine) as session:
            self._require_mission_row(session, mission_id)
            rows = session.scalars(
                select(MissionAssignmentRow)
                .where(MissionAssignmentRow.mission_id == mission_id)
                .order_by(
                    MissionAssignmentRow.task_id,
                    MissionAssignmentRow.assignment_revision,
                )
                .limit(limit)
            )
            return tuple(MissionTaskAssignment.model_validate_json(row.payload) for row in rows)

    def record_run_binding(self, binding: MissionRunBinding) -> MissionRunBinding:
        payload = self._json(binding)
        with Session(self._engine) as session, session.begin():
            existing = session.get(MissionRunBindingRow, str(binding.binding_id))
            if existing is not None:
                return self._idempotent(existing.payload, payload, binding)
            mission = MissionRecord.model_validate_json(
                self._require_mission_row(session, str(binding.mission_id)).payload
            )
            run = session.get(RunRow, binding.run_id)
            if run is None:
                raise MishkanError(ErrorCode.MISSION, "mission run binding references no run")
            if run.context_kind == "repository":
                observed_context = PlanExecutionContext(
                    kind="repository",
                    context_id=run.context_id,
                    revision=run.context_revision,
                    repository_id=run.repository_id,
                    repository_revision=run.repository_revision,
                )
            elif run.context_kind == "prospective_workspace":
                observed_context = PlanExecutionContext(
                    kind="prospective_workspace",
                    context_id=run.context_id,
                    revision=run.context_revision,
                    prospective_workspace_id=run.context_id,
                    discovery_revision=run.context_revision,
                )
            else:
                raise MishkanError(
                    ErrorCode.CONTEXT,
                    "mission run binding references an unknown run context kind",
                )
            if binding.execution_context != observed_context:
                raise MishkanError(
                    ErrorCode.REVISION_MISMATCH,
                    "mission run binding context differs from the exact durable run context",
                )
            plan_row = session.scalar(select(PlanRow).where(PlanRow.run_id == binding.run_id))
            if plan_row is None:
                raise MishkanError(ErrorCode.PLAN, "mission run binding requires an accepted plan")
            accepted_plan = AcceptedPlan.model_validate_json(plan_row.payload)
            organization = accepted_plan.organization_binding
            if organization is None or (
                organization.organization_id != mission.organization_id
                or organization.organization_version != mission.organization_version
                or organization.mission_id != binding.mission_id
            ):
                raise MishkanError(
                    ErrorCode.PLAN,
                    "accepted run plan does not carry this mission and organization identity",
                )
            if mission.origin.schema_version == "1.1" and (
                organization.mission_origin_id != mission.origin.origin_id
                or organization.mission_template_reference != mission.origin.template_reference
            ):
                raise MishkanError(
                    ErrorCode.PLAN,
                    "accepted run plan does not preserve the exact mission origin and template",
                )
            plan_task = next(
                (task for task in accepted_plan.tasks if task.task_id == binding.execution_task_id),
                None,
            )
            if plan_task is None:
                raise MishkanError(
                    ErrorCode.PLAN,
                    "mission run binding references no exact accepted plan task",
                )
            assignment_row = session.scalar(
                select(MissionAssignmentRow)
                .where(
                    MissionAssignmentRow.mission_id == str(binding.mission_id),
                    MissionAssignmentRow.task_id == binding.mission_task_id,
                )
                .order_by(MissionAssignmentRow.assignment_revision.desc())
                .limit(1)
            )
            if assignment_row is None:
                raise MishkanError(
                    ErrorCode.MISSION,
                    "mission run binding references no current mission task assignment",
                )
            assignment = MissionTaskAssignment.model_validate_json(assignment_row.payload)
            if (
                binding.schema_version != "1.1"
                or binding.assignment_id != assignment.assignment_id
                or binding.assignment_revision != assignment.assignment_revision
                or assignment.execution_run_id != binding.run_id
                or assignment.execution_task_id != binding.execution_task_id
                or assignment.accountable_owner != plan_task.assigned_role
                or assignment.exact_tools != plan_task.tools
                or binding.plan_fingerprint != accepted_plan.fingerprint
                or assignment.authority_scope != binding.authority_scope
                or assignment.path_scopes != binding.path_scopes
            ):
                raise MishkanError(
                    ErrorCode.AUTHORITY_NOT_GRANTED,
                    "mission run binding exceeds or contradicts its accepted task assignment",
                )
            self._require_exact_tool_resolution(accepted_plan, plan_task.task_id, assignment)
            prior = session.scalar(
                select(MissionRunBindingRow)
                .where(
                    MissionRunBindingRow.mission_id == str(binding.mission_id),
                    MissionRunBindingRow.binding_key == binding.binding_key,
                )
                .order_by(MissionRunBindingRow.binding_revision.desc())
                .limit(1)
            )
            expected_revision = 1 + (prior.binding_revision if prior is not None else 0)
            if binding.binding_revision != expected_revision:
                raise MishkanError(
                    ErrorCode.REVISION_MISMATCH,
                    "mission run binding revision is stale or skips a revision",
                    details={"expected": expected_revision, "received": binding.binding_revision},
                )
            if prior is not None:
                self._require_run_binding_transition(
                    MissionRunBinding.model_validate_json(prior.payload), binding
                )
            self._require_run_binding_dependencies(session, binding)
            self._require_run_binding_settlement(session, binding, assignment)
            same_run_rows = session.scalars(
                select(MissionRunBindingRow).where(
                    MissionRunBindingRow.mission_id == str(binding.mission_id),
                    MissionRunBindingRow.run_id == binding.run_id,
                    MissionRunBindingRow.binding_key != binding.binding_key,
                )
            ).all()
            duplicate_task = next(
                (
                    row
                    for row in same_run_rows
                    if MissionRunBinding.model_validate_json(row.payload).execution_task_id
                    == binding.execution_task_id
                ),
                None,
            )
            if duplicate_task is not None:
                raise MishkanError(
                    ErrorCode.MISSION,
                    "one run task cannot be bound to multiple mission tasks",
                )
            session.add(
                MissionRunBindingRow(
                    id=str(binding.binding_id),
                    mission_id=str(binding.mission_id),
                    binding_key=binding.binding_key,
                    binding_revision=binding.binding_revision,
                    run_id=binding.run_id,
                    acceptance=binding.acceptance.value,
                    payload=payload,
                    created_at=binding.created_at.isoformat(),
                )
            )
            self._event(
                session,
                aggregate_id=str(binding.mission_id),
                entity_type="mission",
                event_type="mission.run_bound",
                payload={
                    "binding_id": str(binding.binding_id),
                    "binding_key": binding.binding_key,
                    "binding_revision": binding.binding_revision,
                    "mission_task_id": binding.mission_task_id,
                    "assignment_id": str(binding.assignment_id),
                    "assignment_revision": binding.assignment_revision,
                    "run_id": binding.run_id,
                    "plan_fingerprint": binding.plan_fingerprint,
                    "execution_context": binding.execution_context.model_dump(mode="json"),
                    "acceptance": binding.acceptance.value,
                },
            )
        return binding

    def run_bindings(self, mission_id: str, *, limit: int = 1_000) -> tuple[MissionRunBinding, ...]:
        with Session(self._engine) as session:
            self._require_mission_row(session, mission_id)
            rows = session.scalars(
                select(MissionRunBindingRow)
                .where(MissionRunBindingRow.mission_id == mission_id)
                .order_by(
                    MissionRunBindingRow.binding_key,
                    MissionRunBindingRow.binding_revision,
                )
                .limit(limit)
            )
            return tuple(MissionRunBinding.model_validate_json(row.payload) for row in rows)

    def record_run_report(self, report: MissionRunReport) -> MissionRunReport:
        payload = self._json(report)
        with Session(self._engine) as session, session.begin():
            existing = session.get(MissionRunReportRow, str(report.report_id))
            if existing is not None:
                return self._idempotent(existing.payload, payload, report)
            mission = MissionRecord.model_validate_json(
                self._require_mission_row(session, str(report.mission_id)).payload
            )
            if report.mission_revision != mission.revision:
                raise MishkanError(
                    ErrorCode.REVISION_MISMATCH,
                    "mission changed before its run report was recorded",
                )
            if mission.state is not MissionState.EVALUATING:
                raise MishkanError(
                    ErrorCode.MISSION,
                    "mission run report requires the evaluating mission state",
                )
            duplicate = session.scalar(
                select(MissionRunReportRow).where(
                    MissionRunReportRow.mission_id == str(report.mission_id),
                    MissionRunReportRow.run_id == report.run_id,
                )
            )
            if duplicate is not None:
                raise MishkanError(
                    ErrorCode.DUPLICATE_RESULT,
                    "mission run already has another immutable report",
                )
            run = session.get(RunRow, report.run_id)
            plan_row = session.scalar(select(PlanRow).where(PlanRow.run_id == report.run_id))
            if run is None or plan_row is None:
                raise MishkanError(ErrorCode.RUN_INTERRUPTED, "reported run or plan does not exist")
            plan = AcceptedPlan.model_validate_json(plan_row.payload)
            if run.status != "completed" or len(plan.tasks) < 2:
                raise MishkanError(
                    ErrorCode.MISSION,
                    "run report requires one completed multi-task run",
                )
            if report.plan_fingerprint != plan.fingerprint:
                raise MishkanError(
                    ErrorCode.REVISION_MISMATCH,
                    "mission run report references another accepted plan",
                )
            observed_context = PlanExecutionContext(
                kind=run.context_kind,  # type: ignore[arg-type]
                context_id=run.context_id,
                revision=run.context_revision,
                repository_id=run.repository_id,
                repository_revision=run.repository_revision,
                prospective_workspace_id=(
                    run.context_id if run.context_kind == "prospective_workspace" else None
                ),
                discovery_revision=(
                    run.context_revision if run.context_kind == "prospective_workspace" else None
                ),
            )
            if report.execution_context != observed_context:
                raise MishkanError(
                    ErrorCode.REVISION_MISMATCH,
                    "mission run report execution context differs from its run",
                )
            assignments = MissionAssignmentGraphValidator.latest(
                tuple(
                    MissionTaskAssignment.model_validate_json(row.payload)
                    for row in session.scalars(
                        select(MissionAssignmentRow).where(
                            MissionAssignmentRow.mission_id == str(report.mission_id)
                        )
                    ).all()
                )
            )
            MissionAssignmentGraphValidator.validate(tuple(assignments.values()))
            latest_bindings: dict[str, MissionRunBinding] = {}
            for row in session.scalars(
                select(MissionRunBindingRow).where(
                    MissionRunBindingRow.mission_id == str(report.mission_id),
                    MissionRunBindingRow.run_id == report.run_id,
                )
            ).all():
                binding = MissionRunBinding.model_validate_json(row.payload)
                current = latest_bindings.get(binding.binding_key)
                if current is None or binding.binding_revision > current.binding_revision:
                    latest_bindings[binding.binding_key] = binding
            by_execution_task = {
                binding.execution_task_id: binding for binding in latest_bindings.values()
            }
            expected_tasks = {task.task_id for task in plan.tasks}
            reported_tasks = {item.execution_task_id for item in report.task_results}
            if reported_tasks != expected_tasks:
                raise MishkanError(
                    ErrorCode.MISSION,
                    "mission run report does not cover the exact accepted plan tasks",
                    details={
                        "missing": sorted(expected_tasks - reported_tasks),
                        "unexpected": sorted(reported_tasks - expected_tasks),
                    },
                )
            accepted_results = {
                row.task_key
                for row in session.scalars(
                    select(ResultRow).where(ResultRow.run_id == report.run_id)
                ).all()
            }
            acceptance_reviews = {
                row.task_key: ReviewDecision.model_validate_json(row.review_payload)
                for row in session.scalars(
                    select(AcceptanceRow).where(AcceptanceRow.run_id == report.run_id)
                ).all()
            }
            if accepted_results != expected_tasks or set(acceptance_reviews) != expected_tasks:
                raise MishkanError(
                    ErrorCode.MISSION,
                    "mission run report requires every task result to be durably accepted",
                )
            for item in report.task_results:
                assignment = assignments.get(item.mission_task_id)
                matched_binding = by_execution_task.get(item.execution_task_id)
                if (
                    assignment is None
                    or matched_binding is None
                    or (
                        assignment.execution_run_id != report.run_id
                        or assignment.execution_task_id != item.execution_task_id
                        or assignment.accountable_owner != item.accountable_owner
                        or assignment.assignment_kind is not item.assignment_kind
                        or matched_binding.mission_task_id != item.mission_task_id
                        or matched_binding.acceptance is not MissionRunAcceptance.ACCEPTED
                        or matched_binding.result_references != item.result_references
                        or matched_binding.acceptance_references != item.acceptance_references
                    )
                ):
                    raise MishkanError(
                        ErrorCode.MISSION,
                        "mission run report contradicts an accepted assignment or result binding",
                        details={"execution_task_id": item.execution_task_id},
                    )
                required_evidence = {
                    *item.result_references,
                    *item.acceptance_references,
                }
                if not required_evidence.issubset(item.evidence_references):
                    raise MishkanError(
                        ErrorCode.MISSION,
                        "mission run report omits accepted result evidence",
                        details={"execution_task_id": item.execution_task_id},
                    )
                review = acceptance_reviews[item.execution_task_id]
                if review.evaluator_identity == report.reporter_identity:
                    raise MishkanError(
                        ErrorCode.ROLE_CONFLICT,
                        "task acceptance evaluator cannot report that same task",
                        details={"execution_task_id": item.execution_task_id},
                    )
            reporting = assignments[report.reporting_mission_task_id]
            if (
                reporting.assignment_kind is not CrewAssignmentKind.REPORTING
                or reporting.accountable_owner != report.reporter_identity
                or reporting.execution_run_id != report.run_id
                or reporting.execution_task_id != report.reporting_execution_task_id
            ):
                raise MishkanError(
                    ErrorCode.ROLE_CONFLICT,
                    "mission run report is not attributable to its independent Reporter task",
                )
            session.add(
                MissionRunReportRow(
                    id=str(report.report_id),
                    mission_id=str(report.mission_id),
                    run_id=report.run_id,
                    reporter_identity=report.reporter_identity,
                    payload=payload,
                    created_at=report.created_at.isoformat(),
                )
            )
            self._event(
                session,
                aggregate_id=str(report.mission_id),
                entity_type="mission",
                event_type="mission.run_reported",
                payload={
                    "report_id": str(report.report_id),
                    "run_id": report.run_id,
                    "reporter_identity": report.reporter_identity,
                    "plan_fingerprint": report.plan_fingerprint,
                    "task_count": len(report.task_results),
                },
            )
        return report

    def run_reports(self, mission_id: str, *, limit: int = 1_000) -> tuple[MissionRunReport, ...]:
        with Session(self._engine) as session:
            self._require_mission_row(session, mission_id)
            rows = session.scalars(
                select(MissionRunReportRow)
                .where(MissionRunReportRow.mission_id == mission_id)
                .order_by(MissionRunReportRow.created_at)
                .limit(limit)
            )
            return tuple(MissionRunReport.model_validate_json(row.payload) for row in rows)

    def transitions(self, mission_id: str, *, limit: int = 1_000) -> tuple[MissionTransition, ...]:
        with Session(self._engine) as session:
            self._require_mission_row(session, mission_id)
            rows = session.scalars(
                select(MissionTransitionRow)
                .where(MissionTransitionRow.mission_id == mission_id)
                .order_by(MissionTransitionRow.created_at)
                .limit(limit)
            )
            return tuple(MissionTransition.model_validate_json(row.payload) for row in rows)

    def accept_environment_plan(
        self,
        acceptance: MissionEnvironmentPlanAcceptance,
        *,
        expected_revision: int,
    ) -> MissionEnvironmentPlanAcceptance:
        plan = acceptance.plan
        payload = self._json(acceptance)
        with Session(self._engine) as session, session.begin():
            existing = session.get(MissionEnvironmentPlanRow, str(plan.plan_id))
            if existing is not None:
                return self._idempotent(existing.payload, payload, acceptance)
            mission_row = self._require_mission_row(session, str(plan.mission_id))
            mission = MissionRecord.model_validate_json(mission_row.payload)
            self._require_revision(mission, expected_revision)
            if plan.mission_revision != mission.revision:
                raise MishkanError(
                    ErrorCode.REVISION_MISMATCH,
                    "environment plan was authored against a stale mission revision",
                )
            if (
                plan.brief_version != mission.current_brief_version
                or plan.crew_version != mission.current_crew_version
            ):
                raise MishkanError(
                    ErrorCode.REVISION_MISMATCH,
                    "environment plan does not reference the current Brief and crew",
                )
            expected_version = (mission.current_environment_plan_version or 0) + 1
            if plan.version != expected_version:
                raise MishkanError(
                    ErrorCode.REVISION_MISMATCH,
                    "environment plan version is stale or skips a revision",
                    details={"expected": expected_version, "received": plan.version},
                )
            crew = self._crew_row(session, str(plan.mission_id), plan.crew_version)
            members = {item.identity_id: item for item in crew.members}
            owner = members.get(plan.owner_identity)
            if owner is None or owner.assignment_kind in {
                CrewAssignmentKind.EVALUATION,
                CrewAssignmentKind.REPORTING,
                CrewAssignmentKind.AUDIT,
            }:
                raise MishkanError(
                    ErrorCode.ROLE_CONFLICT,
                    "environment plan owner is not an eligible current crew member",
                )
            assignment_rows = session.scalars(
                select(MissionAssignmentRow).where(
                    MissionAssignmentRow.mission_id == str(plan.mission_id)
                )
            ).all()
            latest: dict[str, MissionTaskAssignment] = {}
            for row in assignment_rows:
                assignment = MissionTaskAssignment.model_validate_json(row.payload)
                current = latest.get(assignment.task_id)
                if current is None or (
                    assignment.assignment_revision > current.assignment_revision
                ):
                    latest[assignment.task_id] = assignment
            planning = latest.get(plan.planning_task_id)
            if planning is None or planning.accountable_owner != plan.owner_identity:
                raise MishkanError(
                    ErrorCode.ROLE_CONFLICT,
                    "environment planning assignment does not belong to the plan owner",
                )
            affected = {
                task_id for context in plan.contexts for task_id in context.affected_task_ids
            }
            if unknown := affected - set(latest):
                raise MishkanError(
                    ErrorCode.PLAN,
                    "environment plan references unassigned dependent tasks",
                    details={"unknown_task_ids": sorted(unknown)},
                )
            duplicate = session.scalar(
                select(MissionEnvironmentPlanRow).where(
                    MissionEnvironmentPlanRow.mission_id == str(plan.mission_id),
                    MissionEnvironmentPlanRow.version == plan.version,
                )
            )
            if duplicate is not None:
                raise MishkanError(
                    ErrorCode.DUPLICATE_RESULT,
                    "environment plan version already has another immutable identity",
                )
            session.add(
                MissionEnvironmentPlanRow(
                    plan_id=str(plan.plan_id),
                    mission_id=str(plan.mission_id),
                    version=plan.version,
                    fingerprint=plan.fingerprint,
                    owner_identity=plan.owner_identity,
                    payload=payload,
                    accepted_at=acceptance.accepted_at.isoformat(),
                )
            )
            updated = mission.model_copy(
                update={
                    "revision": mission.revision + 1,
                    "current_environment_plan_version": plan.version,
                    "updated_at": utc_now(),
                }
            )
            self._update_mission_row(mission_row, updated)
            self._event(
                session,
                aggregate_id=str(plan.mission_id),
                entity_type="mission",
                event_type="mission.environment_plan_accepted",
                payload={
                    "plan_id": str(plan.plan_id),
                    "plan_version": plan.version,
                    "fingerprint": plan.fingerprint,
                    "owner_identity": plan.owner_identity,
                    "context_ids": [item.context_id for item in plan.contexts],
                    "revision": updated.revision,
                },
            )
        return acceptance

    def environment_plan(
        self,
        mission_id: str,
        version: int | None = None,
    ) -> MissionEnvironmentPlanAcceptance:
        with Session(self._engine) as session:
            mission = MissionRecord.model_validate_json(
                self._require_mission_row(session, mission_id).payload
            )
            selected = version or mission.current_environment_plan_version
            if selected is None:
                raise MishkanError(ErrorCode.MISSION, "mission has no accepted environment plan")
            row = session.scalar(
                select(MissionEnvironmentPlanRow).where(
                    MissionEnvironmentPlanRow.mission_id == mission_id,
                    MissionEnvironmentPlanRow.version == selected,
                )
            )
            if row is None:
                raise MishkanError(ErrorCode.MISSION, "environment plan revision does not exist")
            return MissionEnvironmentPlanAcceptance.model_validate_json(row.payload)

    def environment_plan_by_id(self, plan_id: str) -> MissionEnvironmentPlanAcceptance:
        with Session(self._engine) as session:
            row = session.get(MissionEnvironmentPlanRow, plan_id)
            if row is None:
                raise MishkanError(ErrorCode.MISSION, "accepted environment plan does not exist")
            return MissionEnvironmentPlanAcceptance.model_validate_json(row.payload)

    def mission(self, mission_id: str) -> MissionRecord:
        with Session(self._engine) as session:
            return MissionRecord.model_validate_json(
                self._require_mission_row(session, mission_id).payload
            )

    def brief(self, mission_id: str, version: int | None = None) -> MissionBrief:
        with Session(self._engine) as session:
            mission = MissionRecord.model_validate_json(
                self._require_mission_row(session, mission_id).payload
            )
            selected = version or mission.current_brief_version
            if selected is None:
                raise MishkanError(ErrorCode.MISSION, "mission has no Mission Brief")
            return self._brief_row(session, mission_id, selected)

    def crew(self, mission_id: str, version: int | None = None) -> MissionCrewRevision:
        with Session(self._engine) as session:
            mission = MissionRecord.model_validate_json(
                self._require_mission_row(session, mission_id).payload
            )
            selected = version or mission.current_crew_version
            if selected is None:
                raise MishkanError(ErrorCode.MISSION, "mission has no Mission Crew")
            row = session.scalar(
                select(MissionCrewRow).where(
                    MissionCrewRow.mission_id == mission_id,
                    MissionCrewRow.version == selected,
                )
            )
            if row is None:
                raise MishkanError(ErrorCode.MISSION, "Mission Crew revision does not exist")
            return MissionCrewRevision.model_validate_json(row.payload)

    def list_missions(self, *, limit: int = 100) -> tuple[MissionRecord, ...]:
        if not 1 <= limit <= 1_000:
            raise MishkanError(ErrorCode.OUTPUT_CONTRACT, "mission query limit is out of bounds")
        with Session(self._engine) as session:
            rows = session.scalars(
                select(MissionRow).order_by(MissionRow.updated_at.desc()).limit(limit)
            ).all()
            return tuple(MissionRecord.model_validate_json(row.payload) for row in rows)

    @staticmethod
    def _require_run_binding_transition(
        prior: MissionRunBinding,
        current: MissionRunBinding,
    ) -> None:
        preserved = (
            "schema_version",
            "mission_id",
            "binding_key",
            "mission_task_id",
            "assignment_id",
            "assignment_revision",
            "run_id",
            "execution_task_id",
            "plan_fingerprint",
            "execution_context",
            "depends_on_binding_keys",
            "authority_scope",
            "path_scopes",
        )
        if any(getattr(prior, field) != getattr(current, field) for field in preserved):
            raise MishkanError(
                ErrorCode.REVISION_MISMATCH,
                "mission run settlement cannot rewrite its accepted binding lineage",
            )
        if prior.acceptance is not MissionRunAcceptance.PENDING:
            raise MishkanError(
                ErrorCode.DUPLICATE_RESULT,
                "settled mission run binding is terminal",
            )
        if current.acceptance is MissionRunAcceptance.PENDING:
            raise MishkanError(
                ErrorCode.REVISION_MISMATCH,
                "mission run binding revision must settle its pending relationship",
            )

    @staticmethod
    def _require_exact_tool_resolution(
        plan: AcceptedPlan,
        execution_task_id: str,
        assignment: MissionTaskAssignment,
    ) -> None:
        if plan.registry is None:
            raise MishkanError(
                ErrorCode.PLAN,
                "mission execution requires an immutable tool registry snapshot",
            )
        bindings = tuple(
            item
            for item in plan.tool_bindings
            if item.task_id == execution_task_id and item.role == assignment.accountable_owner
        )
        bound_ids = tuple(item.tool_id for item in bindings)
        if len(bound_ids) != len(set(bound_ids)) or set(bound_ids) != set(assignment.exact_tools):
            raise MishkanError(
                ErrorCode.AUTHORITY_NOT_GRANTED,
                "mission task does not have one exact resolved binding for every assigned tool",
            )
        for item in bindings:
            try:
                contract = plan.registry.require(item.tool_id)
            except ValueError as exc:
                raise MishkanError(
                    ErrorCode.PLAN,
                    "mission tool binding is absent from its immutable registry snapshot",
                ) from exc
            if (
                item.tool_version != contract.version
                or item.contract_fingerprint != contract.provenance_fingerprint
                or item.registry_fingerprint != plan.registry.fingerprint
            ):
                raise MishkanError(
                    ErrorCode.PLAN,
                    "mission tool identity or version differs from its immutable registry snapshot",
                )
        can_change_workspace = any(
            plan.registry.require(tool_id).effect_class is not EffectClass.READ
            for tool_id in assignment.exact_tools
        )
        if (
            assignment.assignment_kind is CrewAssignmentKind.PRODUCTION
            and can_change_workspace
            and not assignment.requires_independent_evaluation
        ):
            raise MishkanError(
                ErrorCode.ROLE_CONFLICT,
                "workspace-changing production requires independent downstream evaluation",
            )

    @staticmethod
    def _require_run_binding_dependencies(
        session: Session,
        binding: MissionRunBinding,
    ) -> None:
        rows = session.scalars(
            select(MissionRunBindingRow)
            .where(MissionRunBindingRow.mission_id == str(binding.mission_id))
            .order_by(MissionRunBindingRow.binding_revision.desc())
        ).all()
        latest: dict[str, MissionRunBinding] = {}
        for row in rows:
            latest.setdefault(row.binding_key, MissionRunBinding.model_validate_json(row.payload))
        missing = set(binding.depends_on_binding_keys) - set(latest)
        if missing:
            raise MishkanError(
                ErrorCode.PLAN,
                "mission run binding references unknown mission-level dependencies",
                details={"missing_binding_keys": sorted(missing)},
            )
        graph = {key: set(item.depends_on_binding_keys) for key, item in latest.items()}
        graph[binding.binding_key] = set(binding.depends_on_binding_keys)
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(key: str) -> None:
            if key in visiting:
                raise MishkanError(ErrorCode.PLAN, "mission run dependencies contain a cycle")
            if key in visited:
                return
            visiting.add(key)
            for dependency in graph.get(key, set()):
                visit(dependency)
            visiting.remove(key)
            visited.add(key)

        for key in graph:
            visit(key)

    @staticmethod
    def _require_run_binding_settlement(
        session: Session,
        binding: MissionRunBinding,
        assignment: MissionTaskAssignment,
    ) -> None:
        if binding.acceptance is MissionRunAcceptance.PENDING:
            return
        result_reference = f"run-result:{binding.run_id}:{binding.execution_task_id}"
        if result_reference not in binding.result_references:
            raise MishkanError(
                ErrorCode.OUTPUT_CONTRACT,
                "mission run settlement omits its exact durable result reference",
            )
        if binding.acceptance is MissionRunAcceptance.ACCEPTED:
            result = session.scalar(
                select(ResultRow).where(
                    ResultRow.run_id == binding.run_id,
                    ResultRow.task_key == binding.execution_task_id,
                )
            )
            acceptance = session.scalar(
                select(AcceptanceRow).where(
                    AcceptanceRow.run_id == binding.run_id,
                    AcceptanceRow.task_key == binding.execution_task_id,
                )
            )
            expected = f"run-acceptance:{binding.run_id}:{binding.execution_task_id}"
            if (
                result is None
                or acceptance is None
                or expected not in binding.acceptance_references
            ):
                raise MishkanError(
                    ErrorCode.DECISION_VALIDATION,
                    "mission run acceptance is not backed by durable task acceptance",
                )
            review = ReviewDecision.model_validate_json(acceptance.review_payload)
            if (
                review.schema_version != "1.1"
                or review.producer_identity != assignment.accountable_owner
            ):
                raise MishkanError(
                    ErrorCode.ROLE_CONFLICT,
                    "mission task acceptance lacks exact separated producer and evaluator lineage",
                    details={"execution_task_id": binding.execution_task_id},
                )
            return
        rejection = session.scalar(
            select(ReviewRejectionRow)
            .where(
                ReviewRejectionRow.run_id == binding.run_id,
                ReviewRejectionRow.task_key == binding.execution_task_id,
            )
            .order_by(
                ReviewRejectionRow.task_attempt.desc(),
                ReviewRejectionRow.review_sequence.desc(),
            )
            .limit(1)
        )
        expected = f"run-rejection:{binding.run_id}:{binding.execution_task_id}"
        if rejection is None or expected not in binding.acceptance_references:
            raise MishkanError(
                ErrorCode.DECISION_VALIDATION,
                "mission run rejection is not backed by durable review evidence",
            )

    @staticmethod
    def _require_assignment_authority(
        assignment: CrewAssignmentKind, authority_limits: tuple[str, ...]
    ) -> None:
        required_prefix = {
            CrewAssignmentKind.EVALUATION: "evaluation.",
            CrewAssignmentKind.REPORTING: "reporting.",
            CrewAssignmentKind.AUDIT: "audit.",
        }.get(assignment)
        if required_prefix is not None and not any(
            authority.startswith(required_prefix) for authority in authority_limits
        ):
            raise MishkanError(
                ErrorCode.ROLE_CONFLICT,
                "professional profile does not cover the assigned independent responsibility",
                details={"assignment_kind": assignment.value},
            )

    @staticmethod
    def _require_revision(mission: MissionRecord, expected_revision: int) -> None:
        if mission.revision != expected_revision:
            raise MishkanError(
                ErrorCode.REVISION_MISMATCH,
                "mission revision changed before mutation",
                details={"expected": expected_revision, "current": mission.revision},
            )

    @staticmethod
    def _require_mission_row(session: Session, mission_id: str) -> MissionRow:
        row = session.get(MissionRow, mission_id)
        if row is None:
            raise MishkanError(ErrorCode.MISSION, "mission does not exist")
        return row

    @staticmethod
    def _require_organization(
        session: Session, organization_id: str, organization_version: str
    ) -> OrganizationRosterDefinition:
        row = session.get(OrganizationRosterRow, (organization_id, organization_version))
        if row is None:
            raise MishkanError(
                ErrorCode.MISSION,
                "mission organization version is not registered",
            )
        return OrganizationRosterDefinition.model_validate_json(row.payload)

    @staticmethod
    def _brief_row(session: Session, mission_id: str, version: int) -> MissionBrief:
        row = session.scalar(
            select(MissionBriefRow).where(
                MissionBriefRow.mission_id == mission_id,
                MissionBriefRow.version == version,
            )
        )
        if row is None:
            raise MishkanError(ErrorCode.MISSION, "Mission Brief revision does not exist")
        return MissionBrief.model_validate_json(row.payload)

    @staticmethod
    def _crew_row(session: Session, mission_id: str, version: int) -> MissionCrewRevision:
        row = session.scalar(
            select(MissionCrewRow).where(
                MissionCrewRow.mission_id == mission_id,
                MissionCrewRow.version == version,
            )
        )
        if row is None:
            raise MishkanError(ErrorCode.MISSION, "Mission Crew revision does not exist")
        return MissionCrewRevision.model_validate_json(row.payload)

    @staticmethod
    def _update_mission_row(row: MissionRow, record: MissionRecord) -> None:
        row.state = record.state.value
        row.revision = record.revision
        row.current_brief_version = record.current_brief_version
        row.current_crew_version = record.current_crew_version
        row.current_environment_plan_version = record.current_environment_plan_version
        row.payload = SQLiteMissionRepository._json(record)
        row.updated_at = record.updated_at.isoformat()

    @staticmethod
    def _idempotent(existing: str, requested: str, record: RecordT) -> RecordT:
        if existing != requested:
            raise MishkanError(
                ErrorCode.DUPLICATE_RESULT,
                "durable mission identity already contains different content",
            )
        return record

    @staticmethod
    def _json(record: BaseModel) -> str:
        return json.dumps(record.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))

    @staticmethod
    def _event(
        session: Session,
        *,
        aggregate_id: str,
        entity_type: str,
        event_type: str,
        payload: dict[str, object],
    ) -> None:
        session.add(
            OutboxRow(
                id=str(new_id()),
                schema_version="1.0",
                aggregate_id=aggregate_id,
                entity_type=entity_type,
                run_id=None,
                task_id=None,
                identity_id=None,
                team_id=None,
                security_relevant=False,
                event_type=event_type,
                source="mishkan.missions",
                payload=json.dumps(payload, sort_keys=True, separators=(",", ":")),
                occurred_at=utc_now().isoformat(),
                command_id=None,
                correlation_id=None,
                causation_id=None,
                sensitivity="internal",
                published_at=None,
            )
        )
