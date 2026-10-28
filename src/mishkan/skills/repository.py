"""Durable skill-use evidence and bounded miss aggregation."""

from __future__ import annotations

import json
from datetime import datetime

from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from mishkan.domain.errors import ErrorCode, MishkanError
from mishkan.domain.identity import new_id
from mishkan.domain.time import utc_now
from mishkan.persistence.migration import SchemaManager
from mishkan.persistence.runtime import DatabaseTarget
from mishkan.persistence.sqlite import (
    OutboxRow,
    SkillActiveVersionRow,
    SkillLifecycleDecisionRow,
    SkillUsageRow,
    SkillVersionRow,
    create_local_engine,
)
from mishkan.skills.models import (
    SkillInspectionResult,
    SkillLifecycleDecision,
    SkillMutationDisposition,
    SkillUsageRecord,
    SkillUsageSummary,
    SkillUseOutcome,
    SkillVersionRecord,
    SkillVersionState,
)


class SQLiteSkillUsageRepository:
    """Persist usage facts without turning aggregate thresholds into activation."""

    def __init__(self, database_path: DatabaseTarget, *, busy_timeout_ms: int = 5_000) -> None:
        SchemaManager(database_path).require_current()
        self._engine = create_local_engine(database_path, busy_timeout_ms=busy_timeout_ms)

    def record(self, record: SkillUsageRecord) -> SkillUsageRecord:
        payload = self._payload(record)
        with Session(self._engine) as session, session.begin():
            existing = session.get(SkillUsageRow, str(record.id))
            if existing is not None:
                if existing.evidence_payload == payload:
                    return record
                raise MishkanError(
                    ErrorCode.DUPLICATE_RESULT,
                    "skill usage identity already contains different evidence",
                    details={"record_id": str(record.id)},
                )
            session.add(
                SkillUsageRow(
                    id=str(record.id),
                    task_id=record.task_id,
                    task_class=record.task_class,
                    consuming_identity=record.consuming_identity,
                    requested_skill=record.requested_skill,
                    skill_version=record.skill_version,
                    package_fingerprint=record.package_fingerprint,
                    outcome=record.outcome.value,
                    reason=record.reason,
                    policy_fingerprint=record.policy_fingerprint,
                    evidence_payload=payload,
                    recorded_at=record.recorded_at.isoformat(),
                )
            )
            session.add(
                OutboxRow(
                    id=str(new_id()),
                    schema_version="1.0",
                    aggregate_id=record.task_id,
                    entity_type="skill_usage",
                    run_id=None,
                    task_id=record.task_id,
                    identity_id=record.consuming_identity,
                    team_id=None,
                    security_relevant=False,
                    event_type=f"skill.usage_{record.outcome.value}",
                    source="mishkan.skills",
                    payload=json.dumps(
                        {
                            "record_id": str(record.id),
                            "requested_skill": record.requested_skill,
                            "task_class": record.task_class,
                            "outcome": record.outcome.value,
                            "policy_fingerprint": record.policy_fingerprint,
                        },
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                    occurred_at=record.recorded_at.isoformat(),
                    command_id=None,
                    correlation_id=None,
                    causation_id=None,
                    sensitivity="internal",
                    published_at=None,
                )
            )
        return record

    def summary(
        self,
        task_class: str,
        *,
        requested_skill: str | None = None,
    ) -> SkillUsageSummary:
        conditions = [SkillUsageRow.task_class == task_class]
        if requested_skill is not None:
            conditions.append(SkillUsageRow.requested_skill == requested_skill)
        with Session(self._engine) as session:
            rows = session.execute(
                select(
                    SkillUsageRow.outcome,
                    func.count(SkillUsageRow.id),
                    func.max(SkillUsageRow.recorded_at),
                )
                .where(*conditions)
                .group_by(SkillUsageRow.outcome)
            ).all()
        counts = {outcome: count for outcome, count, _ in rows}
        timestamps = [datetime.fromisoformat(timestamp) for _, _, timestamp in rows if timestamp]
        return SkillUsageSummary(
            task_class=task_class,
            requested_skill=requested_skill,
            hits=counts.get(SkillUseOutcome.HIT.value, 0),
            partials=counts.get(SkillUseOutcome.PARTIAL.value, 0),
            misses=counts.get(SkillUseOutcome.MISS.value, 0),
            last_recorded_at=max(timestamps) if timestamps else None,
        )

    def last_used(self, requested_skill: str) -> datetime | None:
        with Session(self._engine) as session:
            value = session.scalar(
                select(func.max(SkillUsageRow.recorded_at)).where(
                    SkillUsageRow.requested_skill == requested_skill,
                    SkillUsageRow.outcome.in_(
                        (SkillUseOutcome.HIT.value, SkillUseOutcome.PARTIAL.value)
                    ),
                )
            )
        return None if value is None else datetime.fromisoformat(value)

    @staticmethod
    def _payload(record: SkillUsageRecord) -> str:
        return json.dumps(record.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))


class SQLiteSkillLifecycleRepository:
    """Own immutable packages and mutable lifecycle pointers in short transactions."""

    def __init__(self, database_path: DatabaseTarget, *, busy_timeout_ms: int = 5_000) -> None:
        SchemaManager(database_path).require_current()
        self._engine = create_local_engine(database_path, busy_timeout_ms=busy_timeout_ms)

    def register_candidate(self, record: SkillVersionRecord) -> SkillVersionRecord:
        if record.state is not SkillVersionState.CANDIDATE or record.inspection is not None:
            raise MishkanError(
                ErrorCode.SKILL_CONTRACT,
                "new skill version must enter as an uninspected candidate",
            )
        try:
            with Session(self._engine) as session, session.begin():
                existing = session.scalar(
                    select(SkillVersionRow).where(
                        SkillVersionRow.skill_name == record.skill_name,
                        SkillVersionRow.skill_version == record.skill_version,
                    )
                )
                if existing is not None:
                    observed = self._record(existing)
                    if observed == record:
                        return observed
                    raise MishkanError(
                        ErrorCode.SKILL_TRUST,
                        "skill semantic version already identifies different content",
                        details={"skill": record.skill_name, "version": record.skill_version},
                    )
                session.add(self._row(record))
                self._event(session, record, "skill.version_candidate")
        except IntegrityError as exc:
            raise MishkanError(
                ErrorCode.SKILL_TRUST,
                "skill candidate conflicts with an existing immutable version",
            ) from exc
        return record

    def record_inspection(
        self,
        version_id: str,
        inspection: SkillInspectionResult,
        *,
        expected_revision: int,
    ) -> SkillVersionRecord:
        with Session(self._engine) as session, session.begin():
            row = self._require_row(session, version_id)
            current = self._record(row)
            if current.revision != expected_revision:
                raise self._revision_error(current)
            if inspection.package_fingerprint != current.provenance.package_fingerprint:
                raise MishkanError(
                    ErrorCode.SKILL_TRUST,
                    "skill inspection differs from the provenance lock",
                )
            if current.state not in {
                SkillVersionState.CANDIDATE,
                SkillVersionState.VALIDATING,
                SkillVersionState.QUARANTINED,
            }:
                raise MishkanError(
                    ErrorCode.SKILL_TRUST,
                    "skill version state does not permit inspection",
                    details={"state": current.state.value},
                )
            state = (
                SkillVersionState.QUARANTINED
                if inspection.quarantined
                else SkillVersionState.ELIGIBLE
            )
            updated = current.model_copy(
                update={
                    "inspection": inspection,
                    "state": state,
                    "revision": current.revision + 1,
                    "updated_at": utc_now(),
                }
            )
            self._update_row(row, updated)
            self._event(session, updated, f"skill.inspection_{state.value}")
            return updated

    def decide(self, decision: SkillLifecycleDecision) -> SkillVersionRecord:
        with Session(self._engine) as session, session.begin():
            existing_decision = session.get(SkillLifecycleDecisionRow, str(decision.decision_id))
            if existing_decision is not None:
                if existing_decision.payload != self._json(decision):
                    raise MishkanError(
                        ErrorCode.DUPLICATE_RESULT,
                        "skill lifecycle decision identity contains different content",
                    )
                return self._record(self._require_row(session, str(decision.version_id)))
            row = self._require_row(session, str(decision.version_id))
            current = self._record(row)
            if current.inspection is None:
                raise MishkanError(
                    ErrorCode.SKILL_TRUST,
                    "skill version cannot be decided before inspection",
                )
            if decision.disposition is SkillMutationDisposition.DENY:
                target = SkillVersionState.REJECTED
            elif current.inspection.quarantined and not decision.quarantine_override:
                target = SkillVersionState.QUARANTINED
            elif decision.disposition is SkillMutationDisposition.REQUIRE_REVIEW:
                target = SkillVersionState.STAGED
            else:
                self._activate_pointer(session, current, decision)
                target = SkillVersionState.ACTIVE
            updated = current.model_copy(
                update={
                    "state": target,
                    "activation_decision_id": decision.decision_id,
                    "policy_fingerprint": decision.policy_fingerprint,
                    "revision": current.revision + 1,
                    "updated_at": utc_now(),
                }
            )
            self._update_row(row, updated)
            session.add(
                SkillLifecycleDecisionRow(
                    id=str(decision.decision_id),
                    version_id=str(decision.version_id),
                    disposition=decision.disposition.value,
                    policy_fingerprint=decision.policy_fingerprint,
                    payload=self._json(decision),
                    decided_at=decision.decided_at.isoformat(),
                )
            )
            self._event(session, updated, f"skill.version_{target.value}")
            return updated

    def archive(
        self,
        version_id: str,
        decision: SkillLifecycleDecision,
        *,
        expected_revision: int,
    ) -> SkillVersionRecord:
        if str(decision.version_id) != version_id:
            raise MishkanError(ErrorCode.SKILL_CONTRACT, "archive decision targets another version")
        if decision.disposition is not SkillMutationDisposition.ALLOW:
            raise MishkanError(
                ErrorCode.AUTHORITY_NOT_GRANTED, "archive requires an allow decision"
            )
        with Session(self._engine) as session, session.begin():
            row = self._require_row(session, version_id)
            current = self._record(row)
            if current.revision != expected_revision:
                raise self._revision_error(current)
            if current.pinned:
                raise MishkanError(ErrorCode.SKILL_TRUST, "pinned skill version cannot be archived")
            pointer = session.get(SkillActiveVersionRow, current.skill_name)
            if pointer is not None and pointer.version_id == version_id:
                session.execute(
                    delete(SkillActiveVersionRow).where(
                        SkillActiveVersionRow.skill_name == current.skill_name
                    )
                )
            updated = current.model_copy(
                update={
                    "state": SkillVersionState.ARCHIVED,
                    "revision": current.revision + 1,
                    "updated_at": utc_now(),
                }
            )
            self._update_row(row, updated)
            self._record_decision(session, decision)
            self._event(session, updated, "skill.version_archived")
            return updated

    def reactivate(
        self,
        version_id: str,
        decision: SkillLifecycleDecision,
        *,
        expected_revision: int,
        operation: str,
    ) -> SkillVersionRecord:
        if operation not in {"restore", "reset"}:
            raise MishkanError(ErrorCode.SKILL_CONTRACT, "skill reactivation mode is invalid")
        if str(decision.version_id) != version_id:
            raise MishkanError(
                ErrorCode.SKILL_CONTRACT,
                "skill reactivation decision targets another version",
            )
        if decision.disposition is not SkillMutationDisposition.ALLOW:
            raise MishkanError(
                ErrorCode.AUTHORITY_NOT_GRANTED,
                "skill reactivation requires an allow decision",
            )
        with Session(self._engine) as session, session.begin():
            if session.get(SkillLifecycleDecisionRow, str(decision.decision_id)) is not None:
                return self._record(self._require_row(session, version_id))
            row = self._require_row(session, version_id)
            current = self._record(row)
            if current.revision != expected_revision:
                raise self._revision_error(current)
            if current.inspection is None:
                raise MishkanError(
                    ErrorCode.SKILL_TRUST,
                    "skill version cannot be reactivated before inspection",
                )
            if current.inspection.quarantined and not decision.quarantine_override:
                raise MishkanError(
                    ErrorCode.SKILL_TRUST,
                    "quarantined skill reactivation requires explicit override authority",
                )
            self._activate_pointer(session, current, decision)
            updated = current.model_copy(
                update={
                    "state": SkillVersionState.ACTIVE,
                    "activation_decision_id": decision.decision_id,
                    "policy_fingerprint": decision.policy_fingerprint,
                    "revision": current.revision + 1,
                    "updated_at": utc_now(),
                }
            )
            self._update_row(row, updated)
            self._record_decision(session, decision)
            event_type = (
                "skill.version_restored" if operation == "restore" else "skill.version_reset"
            )
            self._event(session, updated, event_type)
            return updated

    def set_pin(
        self,
        version_id: str,
        *,
        pinned: bool,
        expected_revision: int,
    ) -> SkillVersionRecord:
        with Session(self._engine) as session, session.begin():
            row = self._require_row(session, version_id)
            current = self._record(row)
            if current.revision != expected_revision:
                raise self._revision_error(current)
            updated = current.model_copy(
                update={
                    "pinned": pinned,
                    "revision": current.revision + 1,
                    "updated_at": utc_now(),
                }
            )
            self._update_row(row, updated)
            self._event(
                session, updated, "skill.version_pinned" if pinned else "skill.version_unpinned"
            )
            return updated

    def get(self, version_id: str) -> SkillVersionRecord:
        with Session(self._engine) as session:
            return self._record(self._require_row(session, version_id))

    def active(self, skill_name: str) -> SkillVersionRecord | None:
        with Session(self._engine) as session:
            pointer = session.get(SkillActiveVersionRow, skill_name)
            if pointer is None:
                return None
            return self._record(self._require_row(session, pointer.version_id))

    def active_versions(self, *, limit: int) -> tuple[SkillVersionRecord, ...]:
        if limit < 1 or limit > 100_000:
            raise MishkanError(ErrorCode.OUTPUT_CONTRACT, "active skill query bound is invalid")
        with Session(self._engine) as session:
            rows = session.scalars(
                select(SkillVersionRow)
                .join(
                    SkillActiveVersionRow,
                    SkillActiveVersionRow.version_id == SkillVersionRow.id,
                )
                .order_by(SkillVersionRow.skill_name, SkillVersionRow.id)
                .limit(limit)
            ).all()
            return tuple(self._record(row) for row in rows)

    def versions(self, skill_name: str) -> tuple[SkillVersionRecord, ...]:
        with Session(self._engine) as session:
            rows = session.scalars(
                select(SkillVersionRow)
                .where(SkillVersionRow.skill_name == skill_name)
                .order_by(SkillVersionRow.created_at, SkillVersionRow.id)
            ).all()
            return tuple(self._record(row) for row in rows)

    def list_versions(
        self,
        *,
        offset: int = 0,
        limit: int = 100,
        skill_name: str | None = None,
    ) -> tuple[SkillVersionRecord, ...]:
        if offset < 0 or limit < 1 or limit > 1_000:
            raise MishkanError(ErrorCode.OUTPUT_CONTRACT, "skill query bound is invalid")
        query = select(SkillVersionRow)
        if skill_name is not None:
            query = query.where(SkillVersionRow.skill_name == skill_name)
        with Session(self._engine) as session:
            rows = session.scalars(
                query.order_by(SkillVersionRow.updated_at, SkillVersionRow.id)
                .offset(offset)
                .limit(limit)
            ).all()
            return tuple(self._record(row) for row in rows)

    def _activate_pointer(
        self,
        session: Session,
        current: SkillVersionRecord,
        decision: SkillLifecycleDecision,
    ) -> None:
        pointer = session.get(SkillActiveVersionRow, current.skill_name)
        observed_id = None if pointer is None else pointer.version_id
        expected_id = (
            None
            if decision.expected_active_version_id is None
            else str(decision.expected_active_version_id)
        )
        if observed_id != expected_id:
            raise MishkanError(
                ErrorCode.REVISION_MISMATCH,
                "active skill version changed before activation",
                details={"expected": expected_id, "observed": observed_id},
            )
        timestamp = decision.decided_at.isoformat()
        if pointer is None:
            session.add(
                SkillActiveVersionRow(
                    skill_name=current.skill_name,
                    version_id=str(current.id),
                    revision=1,
                    updated_at=timestamp,
                )
            )
            return
        prior = self._require_row(session, pointer.version_id)
        prior_record = self._record(prior)
        if prior_record.id != current.id:
            superseded = prior_record.model_copy(
                update={
                    "state": SkillVersionState.SUPERSEDED,
                    "revision": prior_record.revision + 1,
                    "updated_at": utc_now(),
                }
            )
            self._update_row(prior, superseded)
        pointer.version_id = str(current.id)
        pointer.revision += 1
        pointer.updated_at = timestamp

    def _record_decision(
        self,
        session: Session,
        decision: SkillLifecycleDecision,
    ) -> None:
        if session.get(SkillLifecycleDecisionRow, str(decision.decision_id)) is not None:
            raise MishkanError(
                ErrorCode.DUPLICATE_RESULT, "skill lifecycle decision already exists"
            )
        session.add(
            SkillLifecycleDecisionRow(
                id=str(decision.decision_id),
                version_id=str(decision.version_id),
                disposition=decision.disposition.value,
                policy_fingerprint=decision.policy_fingerprint,
                payload=self._json(decision),
                decided_at=decision.decided_at.isoformat(),
            )
        )

    @staticmethod
    def _require_row(session: Session, version_id: str) -> SkillVersionRow:
        row = session.get(SkillVersionRow, version_id)
        if row is None:
            raise MishkanError(
                ErrorCode.SKILL_SELECTION,
                "skill version does not exist",
                details={"version_id": version_id},
            )
        return row

    @staticmethod
    def _row(record: SkillVersionRecord) -> SkillVersionRow:
        return SkillVersionRow(
            id=str(record.id),
            skill_name=record.skill_name,
            skill_version=record.skill_version,
            package_fingerprint=record.provenance.package_fingerprint,
            state=record.state.value,
            payload=SQLiteSkillLifecycleRepository._json(record),
            revision=record.revision,
            created_at=record.created_at.isoformat(),
            updated_at=record.updated_at.isoformat(),
        )

    @staticmethod
    def _update_row(row: SkillVersionRow, record: SkillVersionRecord) -> None:
        row.state = record.state.value
        row.payload = SQLiteSkillLifecycleRepository._json(record)
        row.revision = record.revision
        row.updated_at = record.updated_at.isoformat()

    @staticmethod
    def _record(row: SkillVersionRow) -> SkillVersionRecord:
        return SkillVersionRecord.model_validate_json(row.payload)

    @staticmethod
    def _json(model: SkillVersionRecord | SkillLifecycleDecision) -> str:
        return json.dumps(model.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))

    @staticmethod
    def _revision_error(current: SkillVersionRecord) -> MishkanError:
        return MishkanError(
            ErrorCode.REVISION_MISMATCH,
            "skill version revision changed",
            details={"observed_revision": current.revision},
        )

    @staticmethod
    def _event(session: Session, record: SkillVersionRecord, event_type: str) -> None:
        session.add(
            OutboxRow(
                id=str(new_id()),
                schema_version="1.0",
                aggregate_id=str(record.id),
                entity_type="skill_version",
                run_id=None,
                task_id=None,
                identity_id=None,
                team_id=None,
                security_relevant=record.state is SkillVersionState.QUARANTINED,
                event_type=event_type,
                source="mishkan.skills",
                payload=json.dumps(
                    {
                        "version_id": str(record.id),
                        "skill_name": record.skill_name,
                        "skill_version": record.skill_version,
                        "state": record.state.value,
                        "revision": record.revision,
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                occurred_at=record.updated_at.isoformat(),
                command_id=None,
                correlation_id=None,
                causation_id=None,
                sensitivity="internal",
                published_at=None,
            )
        )
