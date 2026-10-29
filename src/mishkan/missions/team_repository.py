"""Durable authority for collaboration plans and team-effectiveness evidence."""

from __future__ import annotations

import json
from typing import TypeVar
from uuid import UUID

from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from mishkan.domain.errors import ErrorCode, MishkanError
from mishkan.domain.identity import new_id
from mishkan.domain.time import utc_now
from mishkan.missions.models import (
    MissionBrief,
    MissionCrewRevision,
    MissionRecord,
    MissionState,
    MissionTaskAssignment,
)
from mishkan.missions.team_effectiveness import (
    CrewAdaptationDecision,
    MissionCollaborationPlan,
    MissionHandoff,
    MissionRetrospective,
    TeamEffectivenessValidator,
    TeamHealthEvaluation,
    TeamSignal,
)
from mishkan.persistence.migration import SchemaManager
from mishkan.persistence.runtime import DatabaseTarget
from mishkan.persistence.sqlite import (
    CrewAdaptationDecisionRow,
    MissionAssignmentRow,
    MissionBriefRow,
    MissionCollaborationPlanRow,
    MissionCrewRow,
    MissionDecisionRow,
    MissionHandoffRow,
    MissionRetrospectiveRow,
    MissionRow,
    OutboxRow,
    TeamHealthEvaluationRow,
    TeamSignalRow,
    create_local_engine,
)

RecordT = TypeVar("RecordT", bound=BaseModel)


class SQLiteTeamEffectivenessRepository:
    """Persist immutable team records and expose bounded mission projections."""

    def __init__(self, database_path: DatabaseTarget, *, busy_timeout_ms: int = 5_000) -> None:
        SchemaManager(database_path).require_current()
        self._engine = create_local_engine(database_path, busy_timeout_ms=busy_timeout_ms)

    def record_collaboration_plan(self, plan: MissionCollaborationPlan) -> MissionCollaborationPlan:
        payload = self._json(plan)
        with Session(self._engine) as session, session.begin():
            existing = session.get(MissionCollaborationPlanRow, str(plan.plan_id))
            if existing is not None:
                return self._idempotent(existing.payload, payload, plan)
            mission, brief, crew = self._mission_context(session, plan.mission_id)
            TeamEffectivenessValidator.collaboration_plan(plan, brief=brief, crew=crew)
            self._validate_history_citations(session, plan)
            if plan.version != self._next_plan_version(session, str(plan.mission_id)):
                raise MishkanError(
                    ErrorCode.REVISION_MISMATCH,
                    "collaboration plan version is stale or skips a revision",
                )
            if mission.current_brief_version != plan.brief_version or (
                mission.current_crew_version != plan.crew_version
            ):
                raise MishkanError(
                    ErrorCode.REVISION_MISMATCH,
                    "collaboration plan does not use current mission revisions",
                )
            session.add(
                MissionCollaborationPlanRow(
                    plan_id=str(plan.plan_id),
                    mission_id=str(plan.mission_id),
                    version=plan.version,
                    brief_version=plan.brief_version,
                    crew_version=plan.crew_version,
                    production_shape=plan.production_shape.value,
                    payload=payload,
                    created_at=plan.created_at.isoformat(),
                )
            )
            self._event(
                session,
                plan.mission_id,
                "mission.collaboration_plan_recorded",
                {
                    "plan_id": str(plan.plan_id),
                    "version": plan.version,
                    "production_shape": plan.production_shape.value,
                    "crew_version": plan.crew_version,
                },
            )
        return plan

    def record_health(self, evaluation: TeamHealthEvaluation) -> TeamHealthEvaluation:
        payload = self._json(evaluation)
        with Session(self._engine) as session, session.begin():
            existing = session.get(TeamHealthEvaluationRow, str(evaluation.evaluation_id))
            if existing is not None:
                return self._idempotent(existing.payload, payload, evaluation)
            plan = self._plan(
                session,
                str(evaluation.mission_id),
                evaluation.collaboration_plan_version,
            )
            TeamEffectivenessValidator.health_evaluation(evaluation, plan=plan)
            latest_cursor = session.scalar(
                select(TeamHealthEvaluationRow.evidence_cursor)
                .where(TeamHealthEvaluationRow.mission_id == str(evaluation.mission_id))
                .order_by(TeamHealthEvaluationRow.evidence_cursor.desc())
                .limit(1)
            )
            if latest_cursor is not None and evaluation.evidence_cursor <= latest_cursor:
                raise MishkanError(
                    ErrorCode.REVISION_MISMATCH,
                    "team-health evidence cursor must advance monotonically",
                )
            session.add(
                TeamHealthEvaluationRow(
                    evaluation_id=str(evaluation.evaluation_id),
                    mission_id=str(evaluation.mission_id),
                    collaboration_plan_version=evaluation.collaboration_plan_version,
                    evidence_cursor=evaluation.evidence_cursor,
                    status=evaluation.status.value,
                    evaluator_identity=evaluation.evaluator_identity,
                    payload=payload,
                    created_at=evaluation.created_at.isoformat(),
                )
            )
            self._event(
                session,
                evaluation.mission_id,
                "mission.team_health_evaluated",
                {
                    "evaluation_id": str(evaluation.evaluation_id),
                    "status": evaluation.status.value,
                    "evidence_cursor": evaluation.evidence_cursor,
                },
            )
        return evaluation

    def record_adaptation(self, decision: CrewAdaptationDecision) -> CrewAdaptationDecision:
        payload = self._json(decision)
        with Session(self._engine) as session, session.begin():
            existing = session.get(CrewAdaptationDecisionRow, str(decision.decision_id))
            if existing is not None:
                return self._idempotent(existing.payload, payload, decision)
            plan = self._plan(session, str(decision.mission_id), decision.before_plan_version)
            TeamEffectivenessValidator.adaptation(decision, plan=plan)
            self._validate_adaptation_lineage(session, decision, plan)
            session.add(
                CrewAdaptationDecisionRow(
                    decision_id=str(decision.decision_id),
                    mission_id=str(decision.mission_id),
                    before_plan_version=decision.before_plan_version,
                    after_plan_version=decision.after_plan_version,
                    disposition=decision.disposition.value,
                    payload=payload,
                    created_at=decision.created_at.isoformat(),
                )
            )
            self._event(
                session,
                decision.mission_id,
                "mission.crew_adaptation_recorded",
                {
                    "decision_id": str(decision.decision_id),
                    "disposition": decision.disposition.value,
                    "adaptation_kinds": [item.value for item in decision.adaptation_kinds],
                },
            )
        return decision

    def record_retrospective(self, record: MissionRetrospective) -> MissionRetrospective:
        payload = self._json(record)
        with Session(self._engine) as session, session.begin():
            existing = session.get(MissionRetrospectiveRow, str(record.retrospective_id))
            if existing is not None:
                return self._idempotent(existing.payload, payload, record)
            plan = self._plan(session, str(record.mission_id), record.collaboration_plan_version)
            TeamEffectivenessValidator.retrospective(record, plan=plan)
            session.add(
                MissionRetrospectiveRow(
                    retrospective_id=str(record.retrospective_id),
                    mission_id=str(record.mission_id),
                    collaboration_plan_version=record.collaboration_plan_version,
                    trigger=record.trigger,
                    payload=payload,
                    created_at=record.created_at.isoformat(),
                )
            )
            self._event(
                session,
                record.mission_id,
                "mission.retrospective_recorded",
                {
                    "retrospective_id": str(record.retrospective_id),
                    "trigger": record.trigger,
                    "proposal_count": len(record.proposals),
                },
            )
        return record

    def record_signal(self, signal: TeamSignal) -> TeamSignal:
        payload = self._json(signal)
        with Session(self._engine) as session, session.begin():
            existing = session.get(TeamSignalRow, str(signal.signal_id))
            if existing is not None:
                return self._idempotent(existing.payload, payload, signal)
            self._plan(session, str(signal.mission_id), signal.collaboration_plan_version)
            self._require_tasks(session, signal.mission_id, signal.task_ids)
            session.add(
                TeamSignalRow(
                    signal_id=str(signal.signal_id),
                    mission_id=str(signal.mission_id),
                    collaboration_plan_version=signal.collaboration_plan_version,
                    kind=signal.kind,
                    payload=payload,
                    observed_at=signal.observed_at.isoformat(),
                )
            )
            self._event(
                session,
                signal.mission_id,
                "mission.team_signal_recorded",
                {"signal_id": str(signal.signal_id), "kind": signal.kind},
            )
        return signal

    def record_handoff(self, handoff: MissionHandoff) -> MissionHandoff:
        payload = self._json(handoff)
        with Session(self._engine) as session, session.begin():
            existing = session.get(MissionHandoffRow, str(handoff.handoff_id))
            if existing is not None:
                return self._idempotent(existing.payload, payload, handoff)
            plan = self._plan(session, str(handoff.mission_id), handoff.collaboration_plan_version)
            identities = {item.identity_id for item in plan.responsibilities}
            if handoff.from_identity not in identities or handoff.to_identity not in identities:
                raise MishkanError(
                    ErrorCode.ROLE_CONFLICT,
                    "handoff identities are outside the collaboration plan",
                )
            assignments = self._require_tasks(session, handoff.mission_id, (handoff.task_id,))
            if assignments[handoff.task_id].accountable_owner != handoff.from_identity:
                raise MishkanError(
                    ErrorCode.ROLE_CONFLICT,
                    "handoff producer is not the accountable owner of the task",
                )
            session.add(
                MissionHandoffRow(
                    handoff_id=str(handoff.handoff_id),
                    mission_id=str(handoff.mission_id),
                    collaboration_plan_version=handoff.collaboration_plan_version,
                    task_id=handoff.task_id,
                    from_identity=handoff.from_identity,
                    to_identity=handoff.to_identity,
                    payload=payload,
                    created_at=handoff.created_at.isoformat(),
                )
            )
            self._event(
                session,
                handoff.mission_id,
                "mission.handoff_recorded",
                {"handoff_id": str(handoff.handoff_id), "task_id": handoff.task_id},
            )
        return handoff

    def collaboration_plan(
        self, mission_id: str, version: int | None = None
    ) -> MissionCollaborationPlan:
        with Session(self._engine) as session:
            return self._plan(session, mission_id, version)

    def health(self, mission_id: str, *, limit: int = 100) -> tuple[TeamHealthEvaluation, ...]:
        with Session(self._engine) as session:
            rows = session.scalars(
                select(TeamHealthEvaluationRow)
                .where(TeamHealthEvaluationRow.mission_id == mission_id)
                .order_by(TeamHealthEvaluationRow.evidence_cursor.desc())
                .limit(limit)
            )
            return tuple(TeamHealthEvaluation.model_validate_json(row.payload) for row in rows)

    def adaptations(
        self, mission_id: str, *, limit: int = 100
    ) -> tuple[CrewAdaptationDecision, ...]:
        with Session(self._engine) as session:
            rows = session.scalars(
                select(CrewAdaptationDecisionRow)
                .where(CrewAdaptationDecisionRow.mission_id == mission_id)
                .order_by(CrewAdaptationDecisionRow.created_at.desc())
                .limit(limit)
            )
            return tuple(CrewAdaptationDecision.model_validate_json(row.payload) for row in rows)

    def retrospectives(
        self, mission_id: str, *, limit: int = 100
    ) -> tuple[MissionRetrospective, ...]:
        with Session(self._engine) as session:
            rows = session.scalars(
                select(MissionRetrospectiveRow)
                .where(MissionRetrospectiveRow.mission_id == mission_id)
                .order_by(MissionRetrospectiveRow.created_at.desc())
                .limit(limit)
            )
            return tuple(MissionRetrospective.model_validate_json(row.payload) for row in rows)

    def signals(self, mission_id: str, *, limit: int = 1_000) -> tuple[TeamSignal, ...]:
        with Session(self._engine) as session:
            rows = session.scalars(
                select(TeamSignalRow)
                .where(TeamSignalRow.mission_id == mission_id)
                .order_by(TeamSignalRow.observed_at.desc())
                .limit(limit)
            )
            return tuple(TeamSignal.model_validate_json(row.payload) for row in rows)

    def handoffs(self, mission_id: str, *, limit: int = 1_000) -> tuple[MissionHandoff, ...]:
        with Session(self._engine) as session:
            rows = session.scalars(
                select(MissionHandoffRow)
                .where(MissionHandoffRow.mission_id == mission_id)
                .order_by(MissionHandoffRow.created_at.desc())
                .limit(limit)
            )
            return tuple(MissionHandoff.model_validate_json(row.payload) for row in rows)

    def snapshot(self, *, limit: int = 1_000) -> dict[str, object]:
        """Return bounded current projections without creating a second authority."""
        if limit < 1:
            raise MishkanError(ErrorCode.OUTPUT_CONTRACT, "snapshot limit must be positive")
        with Session(self._engine) as session:
            plan_heads = (
                select(
                    MissionCollaborationPlanRow.mission_id.label("mission_id"),
                    func.max(MissionCollaborationPlanRow.version).label("version"),
                )
                .group_by(MissionCollaborationPlanRow.mission_id)
                .order_by(MissionCollaborationPlanRow.mission_id)
                .limit(limit)
                .subquery()
            )
            plans = session.scalars(
                select(MissionCollaborationPlanRow).join(
                    plan_heads,
                    (MissionCollaborationPlanRow.mission_id == plan_heads.c.mission_id)
                    & (MissionCollaborationPlanRow.version == plan_heads.c.version),
                )
            ).all()
            latest_plans = {
                row.mission_id: MissionCollaborationPlan.model_validate_json(row.payload)
                for row in plans
            }

            health_heads = (
                select(
                    TeamHealthEvaluationRow.mission_id.label("mission_id"),
                    func.max(TeamHealthEvaluationRow.evidence_cursor).label("evidence_cursor"),
                )
                .group_by(TeamHealthEvaluationRow.mission_id)
                .order_by(TeamHealthEvaluationRow.mission_id)
                .limit(limit)
                .subquery()
            )
            health_rows = session.scalars(
                select(TeamHealthEvaluationRow).join(
                    health_heads,
                    (TeamHealthEvaluationRow.mission_id == health_heads.c.mission_id)
                    & (TeamHealthEvaluationRow.evidence_cursor == health_heads.c.evidence_cursor),
                )
            ).all()
            latest_health = {
                row.mission_id: TeamHealthEvaluation.model_validate_json(row.payload)
                for row in health_rows
            }

            open_attention = sum(
                evaluation.status.value in {"attention", "blocked", "uncertain"}
                for evaluation in latest_health.values()
            )
            return {
                "collaboration_plans": [
                    item.model_dump(mode="json") for item in latest_plans.values()
                ],
                "latest_health": [item.model_dump(mode="json") for item in latest_health.values()],
                "attention_required": open_attention,
            }

    @staticmethod
    def _next_plan_version(session: Session, mission_id: str) -> int:
        latest = session.scalar(
            select(MissionCollaborationPlanRow.version)
            .where(MissionCollaborationPlanRow.mission_id == mission_id)
            .order_by(MissionCollaborationPlanRow.version.desc())
            .limit(1)
        )
        return (latest or 0) + 1

    @staticmethod
    def _require_tasks(
        session: Session,
        mission_id: UUID,
        task_ids: tuple[str, ...],
    ) -> dict[str, MissionTaskAssignment]:
        if not task_ids:
            return {}
        rows = session.scalars(
            select(MissionAssignmentRow).where(
                MissionAssignmentRow.mission_id == str(mission_id),
                MissionAssignmentRow.task_id.in_(task_ids),
            )
        ).all()
        latest: dict[str, MissionAssignmentRow] = {}
        for row in rows:
            current = latest.get(row.task_id)
            if current is None or row.assignment_revision > current.assignment_revision:
                latest[row.task_id] = row
        missing = set(task_ids) - set(latest)
        if missing:
            raise MishkanError(
                ErrorCode.DECISION_VALIDATION,
                "team evidence references unknown mission tasks",
                details={"missing_task_ids": sorted(missing)},
            )
        return {
            task_id: MissionTaskAssignment.model_validate_json(row.payload)
            for task_id, row in latest.items()
        }

    @staticmethod
    def _validate_history_citations(
        session: Session,
        plan: MissionCollaborationPlan,
    ) -> None:
        for citation in plan.history_citations:
            prior_mission = session.get(MissionRow, str(citation.mission_id))
            if prior_mission is None or prior_mission.state != MissionState.COMPLETED.value:
                raise MishkanError(
                    ErrorCode.DECISION_VALIDATION,
                    "collaboration history must reference a completed mission",
                )
            for evaluation_id in citation.evaluation_ids:
                evaluation = session.get(TeamHealthEvaluationRow, str(evaluation_id))
                if evaluation is None or evaluation.mission_id != str(citation.mission_id):
                    raise MishkanError(
                        ErrorCode.DECISION_VALIDATION,
                        "collaboration history evaluation lineage is invalid",
                    )

    @classmethod
    def _validate_adaptation_lineage(
        cls,
        session: Session,
        decision: CrewAdaptationDecision,
        plan: MissionCollaborationPlan,
    ) -> None:
        assignment_rows = session.scalars(
            select(MissionAssignmentRow).where(
                MissionAssignmentRow.mission_id == str(decision.mission_id),
                MissionAssignmentRow.task_id.in_(decision.affected_task_ids),
            )
        ).all()
        latest_by_task: dict[str, MissionAssignmentRow] = {}
        for row in assignment_rows:
            current = latest_by_task.get(row.task_id)
            if current is None or row.assignment_revision > current.assignment_revision:
                latest_by_task[row.task_id] = row
        missing = set(decision.affected_task_ids) - set(latest_by_task)
        if missing:
            raise MishkanError(
                ErrorCode.DECISION_VALIDATION,
                "crew adaptation references unknown mission tasks",
                details={"missing_task_ids": sorted(missing)},
            )
        known_assignments = {UUID(row.id) for row in latest_by_task.values()}
        if not set(decision.affected_assignments).issubset(known_assignments):
            raise MishkanError(
                ErrorCode.REVISION_MISMATCH,
                "crew adaptation references stale or unrelated assignments",
            )
        if decision.after_plan_version != decision.before_plan_version:
            cls._plan(session, str(decision.mission_id), decision.after_plan_version)
        mission = session.get(MissionRow, str(decision.mission_id))
        assert mission is not None
        if mission.current_crew_version != decision.after_crew_version:
            raise MishkanError(
                ErrorCode.REVISION_MISMATCH,
                "crew adaptation after-version is not the current governed crew",
            )
        if decision.disposition.value != "approval_required":
            return
        # Imported only at the governance boundary to avoid a package cycle:
        # conversation contracts reference mission state models.
        from mishkan.conversations.models import DecisionStatus, MissionDecision

        for reference in decision.approval_references:
            prefix, separator, raw_identity = reference.partition(":")
            if prefix != "decision" or not separator:
                raise MishkanError(
                    ErrorCode.DECISION_VALIDATION,
                    "material adaptation approval must reference a mission decision",
                )
            try:
                decision_id = UUID(raw_identity)
            except ValueError as exc:
                raise MishkanError(
                    ErrorCode.DECISION_VALIDATION,
                    "material adaptation approval identity is invalid",
                ) from exc
            decision_row = session.get(MissionDecisionRow, str(decision_id))
            if decision_row is None or decision_row.mission_id != str(decision.mission_id):
                raise MishkanError(
                    ErrorCode.DECISION_VALIDATION,
                    "material adaptation approval does not belong to the mission",
                )
            approval = MissionDecision.model_validate_json(decision_row.payload)
            if approval.decision_status is not DecisionStatus.ACCEPTED:
                raise MishkanError(
                    ErrorCode.DECISION_VALIDATION,
                    "material adaptation requires an accepted mission decision",
                )
            executive = approval.deciding_identity or approval.actor_id
            if executive not in {"PM", "CTO"} and not executive.casefold().startswith("ceo"):
                raise MishkanError(
                    ErrorCode.AUTHORITY_NOT_GRANTED,
                    "material adaptation requires PM, CTO, or CEO authority",
                )

    @staticmethod
    def _plan(session: Session, mission_id: str, version: int | None) -> MissionCollaborationPlan:
        query = select(MissionCollaborationPlanRow).where(
            MissionCollaborationPlanRow.mission_id == mission_id
        )
        query = (
            query.where(MissionCollaborationPlanRow.version == version)
            if version is not None
            else query.order_by(MissionCollaborationPlanRow.version.desc()).limit(1)
        )
        row = session.scalar(query)
        if row is None:
            raise MishkanError(ErrorCode.MISSION, "mission collaboration plan does not exist")
        return MissionCollaborationPlan.model_validate_json(row.payload)

    @staticmethod
    def _mission_context(
        session: Session, mission_id: UUID
    ) -> tuple[MissionRecord, MissionBrief, MissionCrewRevision]:
        mission_row = session.get(MissionRow, str(mission_id))
        if mission_row is None:
            raise MishkanError(ErrorCode.MISSION, "mission does not exist")
        mission = MissionRecord.model_validate_json(mission_row.payload)
        if mission.current_brief_version is None or mission.current_crew_version is None:
            raise MishkanError(ErrorCode.MISSION, "mission has no current Brief and crew")
        brief_row = session.scalar(
            select(MissionBriefRow).where(
                MissionBriefRow.mission_id == str(mission_id),
                MissionBriefRow.version == mission.current_brief_version,
            )
        )
        crew_row = session.scalar(
            select(MissionCrewRow).where(
                MissionCrewRow.mission_id == str(mission_id),
                MissionCrewRow.version == mission.current_crew_version,
            )
        )
        assert brief_row is not None and crew_row is not None
        return (
            mission,
            MissionBrief.model_validate_json(brief_row.payload),
            MissionCrewRevision.model_validate_json(crew_row.payload),
        )

    @staticmethod
    def _json(record: BaseModel) -> str:
        return json.dumps(record.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))

    @staticmethod
    def _idempotent(existing: str, requested: str, record: RecordT) -> RecordT:
        if existing != requested:
            raise MishkanError(
                ErrorCode.DUPLICATE_RESULT,
                "durable team-evidence identity already contains different content",
            )
        return record

    @staticmethod
    def _event(
        session: Session,
        mission_id: UUID,
        event_type: str,
        payload: dict[str, object],
    ) -> None:
        session.add(
            OutboxRow(
                id=str(new_id()),
                schema_version="1.0",
                aggregate_id=str(mission_id),
                entity_type="mission",
                run_id=None,
                task_id=None,
                identity_id=None,
                team_id=str(mission_id),
                security_relevant=False,
                event_type=event_type,
                source="mishkan.missions.team_effectiveness",
                payload=json.dumps(payload, sort_keys=True, separators=(",", ":")),
                occurred_at=utc_now().isoformat(),
                command_id=None,
                correlation_id=None,
                causation_id=None,
                sensitivity="internal",
                published_at=None,
            )
        )
