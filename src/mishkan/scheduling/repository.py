"""Transactional SQLite authority for schedules and trigger occurrences."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from mishkan.application import CommandResult
from mishkan.domain.errors import ErrorCode, MishkanError
from mishkan.domain.identity import new_id
from mishkan.domain.time import require_aware, utc_now
from mishkan.persistence.migration import SchemaManager
from mishkan.persistence.sqlite import (
    CommandRow,
    OutboxRow,
    RunRow,
    ScheduleOccurrenceRow,
    ScheduleRow,
    create_local_engine,
)
from mishkan.runtime import RunState
from mishkan.scheduling.models import (
    CronTriggerSpec,
    OccurrenceSource,
    OccurrenceState,
    ScheduleCreateRequest,
    ScheduleOccurrence,
    ScheduleRecord,
    ScheduleState,
)

_TERMINAL_RUN_STATES = {
    RunState.COMPLETED.value,
    RunState.FAILED.value,
    RunState.CANCELLED.value,
}


class SQLiteScheduleRepository:
    def __init__(self, database_path: Path, *, busy_timeout_ms: int = 5_000) -> None:
        SchemaManager(database_path).require_current()
        self._engine = create_local_engine(database_path, busy_timeout_ms=busy_timeout_ms)

    def create(
        self,
        request: ScheduleCreateRequest,
        *,
        actor_id: str,
        policy_fingerprint: str,
        next_fire_at: datetime,
    ) -> ScheduleRecord:
        fire_at = require_aware(next_fire_at)
        now = utc_now()
        record = ScheduleRecord(
            schedule_id=request.schedule_id,
            mission=request.mission,
            trigger=request.trigger,
            state=ScheduleState.ACTIVE,
            overlap_key=request.mission.target_key,
            overlap_mode=request.overlap_mode,
            created_by=actor_id,
            preauthorization_policy_fingerprint=policy_fingerprint,
            next_fire_at=fire_at,
            revision=1,
            created_at=now,
            updated_at=now,
        )
        with Session(self._engine) as session, session.begin():
            durable = self.create_in_session(session, record)
            self._event(
                session,
                aggregate_id=str(durable.schedule_id),
                event_type="schedule.created",
                payload=self._schedule_projection(durable),
            )
        return durable

    def create_in_session(self, session: Session, record: ScheduleRecord) -> ScheduleRecord:
        existing = session.get(ScheduleRow, str(record.schedule_id))
        if existing is not None:
            durable = self._schedule(existing)
            if durable != record:
                raise MishkanError(
                    ErrorCode.DUPLICATE_RESULT,
                    "schedule identity already contains different content",
                )
            return durable
        session.add(self._schedule_row(record))
        session.flush()
        return record

    def schedule(self, schedule_id: UUID) -> ScheduleRecord:
        with Session(self._engine) as session:
            return self._schedule(self._require_schedule(session, schedule_id))

    def list_schedules(
        self,
        *,
        state: ScheduleState | None = None,
        offset: int = 0,
        limit: int = 100,
    ) -> tuple[ScheduleRecord, ...]:
        if offset < 0 or limit < 1 or limit > 10_000:
            raise MishkanError(ErrorCode.SCHEDULE, "schedule query bounds are invalid")
        statement = select(ScheduleRow).order_by(ScheduleRow.created_at, ScheduleRow.id)
        if state is not None:
            statement = statement.where(ScheduleRow.state == state.value)
        with Session(self._engine) as session:
            rows = session.scalars(statement.offset(offset).limit(limit)).all()
            return tuple(self._schedule(row) for row in rows)

    def due_schedules(self, now: datetime, *, limit: int) -> tuple[ScheduleRecord, ...]:
        instant = require_aware(now).isoformat()
        with Session(self._engine) as session:
            rows = session.scalars(
                select(ScheduleRow)
                .where(
                    ScheduleRow.state == ScheduleState.ACTIVE.value,
                    ScheduleRow.next_fire_at.is_not(None),
                    ScheduleRow.next_fire_at <= instant,
                )
                .order_by(ScheduleRow.next_fire_at, ScheduleRow.id)
                .limit(limit)
            ).all()
            return tuple(self._schedule(row) for row in rows)

    def update_schedule(
        self,
        schedule_id: UUID,
        *,
        expected_revision: int,
        state: ScheduleState,
        next_fire_at: datetime | None,
        event_type: str,
        last_evaluated_at: datetime | None = None,
    ) -> ScheduleRecord:
        with Session(self._engine) as session, session.begin():
            updated = self.update_schedule_in_session(
                session,
                schedule_id,
                expected_revision=expected_revision,
                state=state,
                next_fire_at=next_fire_at,
                last_evaluated_at=last_evaluated_at,
            )
            self._event(
                session,
                aggregate_id=str(schedule_id),
                event_type=event_type,
                payload=self._schedule_projection(updated),
            )
            return updated

    def update_schedule_in_session(
        self,
        session: Session,
        schedule_id: UUID,
        *,
        expected_revision: int,
        state: ScheduleState,
        next_fire_at: datetime | None,
        last_evaluated_at: datetime | None = None,
    ) -> ScheduleRecord:
        row = self._require_schedule(session, schedule_id)
        current = self._schedule(row)
        if current.revision != expected_revision:
            raise MishkanError(
                ErrorCode.REVISION_MISMATCH,
                "schedule revision changed",
                details={"expected": expected_revision, "current": current.revision},
            )
        updated = current.model_copy(
            update={
                "state": state,
                "next_fire_at": (require_aware(next_fire_at) if next_fire_at is not None else None),
                "last_evaluated_at": (
                    require_aware(last_evaluated_at)
                    if last_evaluated_at is not None
                    else current.last_evaluated_at
                ),
                "revision": current.revision + 1,
                "updated_at": utc_now(),
            }
        )
        self._write_schedule(row, updated)
        session.flush()
        return updated

    def create_occurrence(
        self,
        schedule: ScheduleRecord,
        *,
        occurrence_key: str,
        source: OccurrenceSource,
        scheduled_for: datetime,
    ) -> ScheduleOccurrence:
        instant = require_aware(scheduled_for)
        now = utc_now()
        occurrence = ScheduleOccurrence(
            occurrence_key=occurrence_key,
            schedule_id=schedule.schedule_id,
            schedule_revision=schedule.revision,
            source=source,
            state=OccurrenceState.PENDING,
            scheduled_for=instant,
            created_at=now,
            updated_at=now,
        )
        with Session(self._engine) as session, session.begin():
            durable = self.create_occurrence_in_session(session, occurrence)
            self._event(
                session,
                aggregate_id=str(schedule.schedule_id),
                event_type="schedule.occurrence_created",
                payload=self._occurrence_projection(durable),
            )
        return durable

    def create_occurrence_in_session(
        self,
        session: Session,
        occurrence: ScheduleOccurrence,
    ) -> ScheduleOccurrence:
        self._require_schedule(session, occurrence.schedule_id)
        existing = session.scalar(
            select(ScheduleOccurrenceRow).where(
                ScheduleOccurrenceRow.schedule_id == str(occurrence.schedule_id),
                ScheduleOccurrenceRow.occurrence_key == occurrence.occurrence_key,
            )
        )
        if existing is not None:
            durable = self._occurrence(existing)
            if (
                durable.scheduled_for != occurrence.scheduled_for
                or durable.schedule_id != occurrence.schedule_id
            ):
                raise MishkanError(
                    ErrorCode.DUPLICATE_RESULT,
                    "schedule occurrence key already contains different content",
                )
            return durable
        session.add(self._occurrence_row(occurrence))
        session.flush()
        return occurrence

    def schedule_in_session(self, session: Session, schedule_id: UUID) -> ScheduleRecord:
        return self._schedule(self._require_schedule(session, schedule_id))

    def occurrence(self, occurrence_id: UUID) -> ScheduleOccurrence:
        with Session(self._engine) as session:
            return self._occurrence(self._require_occurrence(session, occurrence_id))

    def application_result(self, command_id: UUID) -> CommandResult | None:
        with Session(self._engine) as session:
            row = session.get(CommandRow, str(command_id))
            return (
                CommandResult.model_validate_json(row.result_payload) if row is not None else None
            )

    def occurrences(
        self,
        *,
        schedule_id: UUID | None = None,
        offset: int = 0,
        limit: int = 100,
    ) -> tuple[ScheduleOccurrence, ...]:
        if offset < 0 or limit < 1 or limit > 10_000:
            raise MishkanError(ErrorCode.SCHEDULE, "schedule history bounds are invalid")
        statement = select(ScheduleOccurrenceRow).order_by(
            ScheduleOccurrenceRow.scheduled_for.desc(),
            ScheduleOccurrenceRow.id,
        )
        if schedule_id is not None:
            statement = statement.where(ScheduleOccurrenceRow.schedule_id == str(schedule_id))
        with Session(self._engine) as session:
            rows = session.scalars(statement.offset(offset).limit(limit)).all()
            return tuple(self._occurrence(row) for row in rows)

    def due_retries(self, now: datetime, *, limit: int) -> tuple[ScheduleOccurrence, ...]:
        instant = require_aware(now).isoformat()
        with Session(self._engine) as session:
            rows = session.scalars(
                select(ScheduleOccurrenceRow)
                .where(
                    ScheduleOccurrenceRow.state == OccurrenceState.RETRY_WAITING.value,
                    ScheduleOccurrenceRow.next_attempt_at.is_not(None),
                    ScheduleOccurrenceRow.next_attempt_at <= instant,
                )
                .order_by(ScheduleOccurrenceRow.next_attempt_at, ScheduleOccurrenceRow.id)
                .limit(limit)
            ).all()
            return tuple(self._occurrence(row) for row in rows)

    def interrupted_dispatches(self, *, limit: int) -> tuple[ScheduleOccurrence, ...]:
        with Session(self._engine) as session:
            rows = session.scalars(
                select(ScheduleOccurrenceRow)
                .where(ScheduleOccurrenceRow.state == OccurrenceState.DISPATCHING.value)
                .order_by(ScheduleOccurrenceRow.updated_at, ScheduleOccurrenceRow.id)
                .limit(limit)
            ).all()
            return tuple(self._occurrence(row) for row in rows)

    def pending_occurrences(self, *, limit: int) -> tuple[ScheduleOccurrence, ...]:
        with Session(self._engine) as session:
            rows = session.scalars(
                select(ScheduleOccurrenceRow)
                .where(ScheduleOccurrenceRow.state == OccurrenceState.PENDING.value)
                .order_by(ScheduleOccurrenceRow.created_at, ScheduleOccurrenceRow.id)
                .limit(limit)
            ).all()
            return tuple(self._occurrence(row) for row in rows)

    def claim_dispatch(
        self,
        occurrence_id: UUID,
        *,
        run_command_id: UUID,
    ) -> ScheduleOccurrence:
        with Session(self._engine) as session, session.begin():
            row = self._require_occurrence(session, occurrence_id)
            current = self._occurrence(row)
            if current.state is OccurrenceState.DISPATCHING:
                if current.run_command_id != run_command_id:
                    raise MishkanError(
                        ErrorCode.REVISION_MISMATCH,
                        "schedule occurrence is already dispatching another command",
                    )
                return current
            if current.state not in {OccurrenceState.PENDING, OccurrenceState.RETRY_WAITING}:
                raise MishkanError(
                    ErrorCode.REVISION_MISMATCH,
                    "schedule occurrence is not dispatchable",
                    details={"state": current.state.value},
                )
            updated = current.model_copy(
                update={
                    "state": OccurrenceState.DISPATCHING,
                    "attempt_count": current.attempt_count + 1,
                    "run_command_id": run_command_id,
                    "next_attempt_at": None,
                    "error_code": None,
                    "error_message": None,
                    "updated_at": utc_now(),
                }
            )
            self._write_occurrence(row, updated)
            self._event(
                session,
                aggregate_id=str(updated.schedule_id),
                event_type="schedule.occurrence_dispatching",
                payload=self._occurrence_projection(updated),
            )
            return updated

    def settle_occurrence(
        self,
        occurrence_id: UUID,
        *,
        state: OccurrenceState,
        run_id: str | None = None,
        next_attempt_at: datetime | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> ScheduleOccurrence:
        if state not in {
            OccurrenceState.ACCEPTED,
            OccurrenceState.RETRY_WAITING,
            OccurrenceState.MISFIRED,
            OccurrenceState.COALESCED,
            OccurrenceState.OVERLAP_BLOCKED,
            OccurrenceState.FAILED,
            OccurrenceState.CANCELLED,
            OccurrenceState.UNCERTAIN,
        }:
            raise MishkanError(ErrorCode.SCHEDULE, "invalid occurrence settlement state")
        with Session(self._engine) as session, session.begin():
            row = self._require_occurrence(session, occurrence_id)
            current = self._occurrence(row)
            if current.state in {
                OccurrenceState.ACCEPTED,
                OccurrenceState.MISFIRED,
                OccurrenceState.COALESCED,
                OccurrenceState.OVERLAP_BLOCKED,
                OccurrenceState.FAILED,
                OccurrenceState.CANCELLED,
                OccurrenceState.UNCERTAIN,
            }:
                if current.state is state and current.run_id == run_id:
                    return current
                raise MishkanError(
                    ErrorCode.REVISION_MISMATCH,
                    "schedule occurrence is already terminal",
                )
            retry_at = require_aware(next_attempt_at) if next_attempt_at is not None else None
            updated = current.model_copy(
                update={
                    "state": state,
                    "run_id": run_id,
                    "next_attempt_at": retry_at,
                    "error_code": error_code,
                    "error_message": error_message,
                    "updated_at": utc_now(),
                }
            )
            self._write_occurrence(row, updated)
            self._event(
                session,
                aggregate_id=str(updated.schedule_id),
                event_type=f"schedule.occurrence_{state.value}",
                payload=self._occurrence_projection(updated),
            )
            return updated

    def has_active_overlap(self, schedule: ScheduleRecord, *, excluding: UUID) -> bool:
        with Session(self._engine) as session:
            dispatching = session.scalar(
                select(ScheduleOccurrenceRow.id)
                .join(ScheduleRow, ScheduleRow.id == ScheduleOccurrenceRow.schedule_id)
                .where(
                    ScheduleRow.overlap_key == schedule.overlap_key,
                    ScheduleOccurrenceRow.id != str(excluding),
                    ScheduleOccurrenceRow.state == OccurrenceState.DISPATCHING.value,
                )
                .limit(1)
            )
            if dispatching is not None:
                return True
            active = session.scalar(
                select(ScheduleOccurrenceRow.id)
                .join(ScheduleRow, ScheduleRow.id == ScheduleOccurrenceRow.schedule_id)
                .join(RunRow, RunRow.id == ScheduleOccurrenceRow.run_id)
                .where(
                    ScheduleRow.overlap_key == schedule.overlap_key,
                    ScheduleOccurrenceRow.id != str(excluding),
                    ScheduleOccurrenceRow.state == OccurrenceState.ACCEPTED.value,
                    RunRow.status.not_in(_TERMINAL_RUN_STATES),
                )
                .limit(1)
            )
            return active is not None

    @staticmethod
    def occurrence_key(schedule_id: UUID, scheduled_for: datetime) -> str:
        instant = require_aware(scheduled_for).isoformat()
        return f"scheduled:{schedule_id}:{instant}"

    @staticmethod
    def _schedule(row: ScheduleRow) -> ScheduleRecord:
        return ScheduleRecord.model_validate_json(row.payload)

    @staticmethod
    def _occurrence(row: ScheduleOccurrenceRow) -> ScheduleOccurrence:
        return ScheduleOccurrence.model_validate_json(row.payload)

    @staticmethod
    def _require_schedule(session: Session, schedule_id: UUID) -> ScheduleRow:
        row = session.get(ScheduleRow, str(schedule_id))
        if row is None:
            raise MishkanError(ErrorCode.SCHEDULE, "schedule does not exist")
        return row

    @staticmethod
    def _require_occurrence(session: Session, occurrence_id: UUID) -> ScheduleOccurrenceRow:
        row = session.get(ScheduleOccurrenceRow, str(occurrence_id))
        if row is None:
            raise MishkanError(ErrorCode.SCHEDULE, "schedule occurrence does not exist")
        return row

    @staticmethod
    def _schedule_row(record: ScheduleRecord) -> ScheduleRow:
        timezone = record.trigger.timezone if isinstance(record.trigger, CronTriggerSpec) else None
        return ScheduleRow(
            id=str(record.schedule_id),
            project_id=record.mission.project_id,
            state=record.state.value,
            trigger_kind=record.trigger.kind.value,
            timezone=timezone,
            overlap_key=record.overlap_key,
            overlap_mode=record.overlap_mode.value,
            next_fire_at=(record.next_fire_at.isoformat() if record.next_fire_at else None),
            last_evaluated_at=(
                record.last_evaluated_at.isoformat() if record.last_evaluated_at else None
            ),
            revision=record.revision,
            created_by=record.created_by,
            policy_fingerprint=record.preauthorization_policy_fingerprint,
            payload=record.model_dump_json(),
            created_at=record.created_at.isoformat(),
            updated_at=record.updated_at.isoformat(),
        )

    @staticmethod
    def _write_schedule(row: ScheduleRow, record: ScheduleRecord) -> None:
        row.state = record.state.value
        row.next_fire_at = record.next_fire_at.isoformat() if record.next_fire_at else None
        row.last_evaluated_at = (
            record.last_evaluated_at.isoformat() if record.last_evaluated_at else None
        )
        row.revision = record.revision
        row.payload = record.model_dump_json()
        row.updated_at = record.updated_at.isoformat()

    @staticmethod
    def _occurrence_row(record: ScheduleOccurrence) -> ScheduleOccurrenceRow:
        return ScheduleOccurrenceRow(
            id=str(record.occurrence_id),
            occurrence_key=record.occurrence_key,
            schedule_id=str(record.schedule_id),
            schedule_revision=record.schedule_revision,
            source=record.source.value,
            state=record.state.value,
            scheduled_for=record.scheduled_for.isoformat(),
            attempt_count=record.attempt_count,
            run_command_id=(str(record.run_command_id) if record.run_command_id else None),
            run_id=record.run_id,
            next_attempt_at=(
                record.next_attempt_at.isoformat() if record.next_attempt_at else None
            ),
            error_code=record.error_code,
            payload=record.model_dump_json(),
            created_at=record.created_at.isoformat(),
            updated_at=record.updated_at.isoformat(),
        )

    @staticmethod
    def _write_occurrence(row: ScheduleOccurrenceRow, record: ScheduleOccurrence) -> None:
        row.state = record.state.value
        row.attempt_count = record.attempt_count
        row.run_command_id = str(record.run_command_id) if record.run_command_id else None
        row.run_id = record.run_id
        row.next_attempt_at = record.next_attempt_at.isoformat() if record.next_attempt_at else None
        row.error_code = record.error_code
        row.payload = record.model_dump_json()
        row.updated_at = record.updated_at.isoformat()

    @staticmethod
    def _schedule_projection(record: ScheduleRecord) -> dict[str, object]:
        return {
            "schedule_id": str(record.schedule_id),
            "project_id": record.mission.project_id,
            "state": record.state.value,
            "trigger_kind": record.trigger.kind.value,
            "overlap_key": record.overlap_key,
            "overlap_mode": record.overlap_mode.value,
            "next_fire_at": record.next_fire_at.isoformat() if record.next_fire_at else None,
            "revision": record.revision,
        }

    @staticmethod
    def _occurrence_projection(record: ScheduleOccurrence) -> dict[str, object]:
        return {
            "occurrence_id": str(record.occurrence_id),
            "occurrence_key": record.occurrence_key,
            "schedule_id": str(record.schedule_id),
            "state": record.state.value,
            "source": record.source.value,
            "scheduled_for": record.scheduled_for.isoformat(),
            "attempt_count": record.attempt_count,
            "run_command_id": str(record.run_command_id) if record.run_command_id else None,
            "run_id": record.run_id,
            "error_code": record.error_code,
        }

    @staticmethod
    def _event(
        session: Session,
        *,
        aggregate_id: str,
        event_type: str,
        payload: dict[str, object],
    ) -> None:
        now = utc_now()
        session.add(
            OutboxRow(
                id=str(new_id()),
                schema_version="1.0",
                aggregate_id=aggregate_id,
                entity_type="schedule",
                run_id=None,
                task_id=None,
                identity_id=None,
                team_id=None,
                security_relevant=False,
                event_type=event_type,
                source="mishkand.scheduling",
                payload=json.dumps(payload, sort_keys=True, separators=(",", ":")),
                occurred_at=now.isoformat(),
                command_id=None,
                correlation_id=None,
                causation_id=None,
                sensitivity="internal",
                published_at=None,
            )
        )
