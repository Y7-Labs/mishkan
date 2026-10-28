"""Deterministic schedule mutation and trigger calculation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import cast
from uuid import UUID
from zoneinfo import ZoneInfo

from apscheduler.triggers.cron import CronTrigger  # type: ignore[import-untyped]
from sqlalchemy.orm import Session

from mishkan.config.models import SchedulingConfig
from mishkan.domain.errors import ErrorCode, MishkanError
from mishkan.domain.time import require_aware, utc_now
from mishkan.scheduling.models import (
    CronTriggerSpec,
    DateTriggerSpec,
    OccurrenceSource,
    OccurrenceState,
    ScheduleCreateRequest,
    ScheduleFireRequest,
    ScheduleOccurrence,
    ScheduleRecord,
    ScheduleState,
)
from mishkan.scheduling.repository import SQLiteScheduleRepository


@dataclass(frozen=True, slots=True)
class ScheduleMutationResult:
    schedule: ScheduleRecord
    occurrence: ScheduleOccurrence | None = None
    coalesced: tuple[ScheduleOccurrence, ...] = ()

    def payload(self) -> dict[str, object]:
        return {
            "schedule": self.schedule.model_dump(mode="json"),
            "occurrence": (
                self.occurrence.model_dump(mode="json") if self.occurrence is not None else None
            ),
            "coalesced": [item.model_dump(mode="json") for item in self.coalesced],
        }


class ScheduleClock:
    """Pure APScheduler-backed trigger calculations; APS owns no durable state."""

    @staticmethod
    def initial(trigger: DateTriggerSpec | CronTriggerSpec, now: datetime) -> datetime:
        instant = require_aware(now)
        if isinstance(trigger, DateTriggerSpec):
            if trigger.run_at <= instant:
                raise MishkanError(
                    ErrorCode.SCHEDULE,
                    "one-shot schedule must be created for a future instant",
                )
            return trigger.run_at
        next_fire = ScheduleClock._cron(trigger).get_next_fire_time(None, instant)
        if next_fire is None:
            raise MishkanError(ErrorCode.SCHEDULE, "cron schedule has no future occurrence")
        return cast(datetime, next_fire)

    @staticmethod
    def next_after(
        trigger: DateTriggerSpec | CronTriggerSpec,
        previous: datetime,
    ) -> datetime | None:
        if isinstance(trigger, DateTriggerSpec):
            return None
        instant = require_aware(previous)
        return cast(
            datetime | None,
            ScheduleClock._cron(trigger).get_next_fire_time(instant, instant),
        )

    @staticmethod
    def _cron(trigger: CronTriggerSpec) -> CronTrigger:
        try:
            return CronTrigger.from_crontab(
                trigger.expression,
                timezone=ZoneInfo(trigger.timezone),
            )
        except (TypeError, ValueError) as exc:
            raise MishkanError(ErrorCode.SCHEDULE, "cron expression is invalid") from exc


class ScheduleMutationService:
    """Apply schedule commands inside the application command transaction."""

    def __init__(
        self,
        config: SchedulingConfig,
        repository: SQLiteScheduleRepository,
    ) -> None:
        self._config = config
        self._repository = repository

    def create(
        self,
        session: Session,
        request: ScheduleCreateRequest,
        *,
        actor_id: str,
        policy_fingerprint: str,
        revision: int,
        now: datetime | None = None,
    ) -> ScheduleMutationResult:
        observed = require_aware(now or utc_now())
        record = ScheduleRecord(
            schedule_id=request.schedule_id,
            mission=request.mission,
            trigger=request.trigger,
            state=ScheduleState.ACTIVE,
            overlap_key=request.mission.target_key,
            overlap_mode=request.overlap_mode,
            created_by=actor_id,
            preauthorization_policy_fingerprint=policy_fingerprint,
            next_fire_at=ScheduleClock.initial(request.trigger, observed),
            revision=revision,
            created_at=observed,
            updated_at=observed,
        )
        return ScheduleMutationResult(self._repository.create_in_session(session, record))

    def transition(
        self,
        session: Session,
        schedule_id: UUID,
        *,
        state: ScheduleState,
        expected_revision: int,
        now: datetime | None = None,
    ) -> ScheduleMutationResult:
        current = self._repository.schedule_in_session(session, schedule_id)
        if current.state is ScheduleState.REMOVED:
            raise MishkanError(ErrorCode.SCHEDULE, "removed schedule cannot transition")
        if state is ScheduleState.ACTIVE and current.state is ScheduleState.COMPLETED:
            raise MishkanError(ErrorCode.SCHEDULE, "completed one-shot schedule cannot resume")
        next_fire = None if state is ScheduleState.REMOVED else current.next_fire_at
        updated = self._repository.update_schedule_in_session(
            session,
            schedule_id,
            expected_revision=expected_revision,
            state=state,
            next_fire_at=next_fire,
            last_evaluated_at=now,
        )
        return ScheduleMutationResult(updated)

    def run_now(
        self,
        session: Session,
        schedule_id: UUID,
        *,
        command_id: UUID,
        expected_revision: int,
        now: datetime | None = None,
    ) -> ScheduleMutationResult:
        observed = require_aware(now or utc_now())
        current = self._repository.schedule_in_session(session, schedule_id)
        if current.state is ScheduleState.REMOVED:
            raise MishkanError(ErrorCode.SCHEDULE, "removed schedule cannot run")
        occurrence = ScheduleOccurrence(
            occurrence_key=f"manual:{schedule_id}:{command_id}",
            schedule_id=schedule_id,
            schedule_revision=current.revision,
            source=OccurrenceSource.MANUAL,
            state=OccurrenceState.PENDING,
            scheduled_for=observed,
            created_at=observed,
            updated_at=observed,
        )
        durable = self._repository.create_occurrence_in_session(session, occurrence)
        updated = self._repository.update_schedule_in_session(
            session,
            schedule_id,
            expected_revision=expected_revision,
            state=current.state,
            next_fire_at=current.next_fire_at,
            last_evaluated_at=observed,
        )
        return ScheduleMutationResult(updated, durable)

    def fire(
        self,
        session: Session,
        schedule_id: UUID,
        request: ScheduleFireRequest,
        *,
        expected_revision: int,
    ) -> ScheduleMutationResult:
        current = self._repository.schedule_in_session(session, schedule_id)
        if current.state is not ScheduleState.ACTIVE or current.next_fire_at is None:
            raise MishkanError(ErrorCode.SCHEDULE, "schedule is not active")
        observed = request.observed_at
        if current.next_fire_at > observed:
            raise MishkanError(
                ErrorCode.SCHEDULE,
                "schedule trigger arrived before the next due instant",
                details={"next_fire_at": current.next_fire_at.isoformat()},
            )

        due = self._due_slots(current, observed)
        selected = due[-1] if self._config.coalesce else due[0]
        coalesced_slots = due[:-1] if self._config.coalesce else ()
        coalesced = tuple(
            self._occurrence(
                current,
                slot,
                request.source,
                state=OccurrenceState.COALESCED,
                message="coalesced by configured schedule policy",
            )
            for slot in coalesced_slots
        )
        for item in coalesced:
            self._repository.create_occurrence_in_session(session, item)

        lateness = observed - selected
        state = (
            OccurrenceState.MISFIRED
            if lateness > timedelta(seconds=self._config.misfire_grace_seconds)
            else OccurrenceState.PENDING
        )
        occurrence = self._occurrence(
            current,
            selected,
            request.source,
            state=state,
            message=(
                "schedule occurrence exceeded its configured misfire grace"
                if state is OccurrenceState.MISFIRED
                else None
            ),
        )
        durable = self._repository.create_occurrence_in_session(session, occurrence)
        next_fire = ScheduleClock.next_after(current.trigger, selected)
        next_state = ScheduleState.ACTIVE if next_fire is not None else ScheduleState.COMPLETED
        updated = self._repository.update_schedule_in_session(
            session,
            schedule_id,
            expected_revision=expected_revision,
            state=next_state,
            next_fire_at=next_fire,
            last_evaluated_at=observed,
        )
        return ScheduleMutationResult(updated, durable, coalesced)

    def _due_slots(self, schedule: ScheduleRecord, observed: datetime) -> tuple[datetime, ...]:
        assert schedule.next_fire_at is not None
        slots: list[datetime] = []
        candidate: datetime | None = schedule.next_fire_at
        while (
            candidate is not None
            and candidate <= observed
            and len(slots) < self._config.max_catch_up_occurrences
        ):
            slots.append(candidate)
            candidate = ScheduleClock.next_after(schedule.trigger, candidate)
        if not slots:
            raise MishkanError(ErrorCode.SCHEDULE, "schedule has no due occurrence")
        return tuple(slots)

    @staticmethod
    def _occurrence(
        schedule: ScheduleRecord,
        scheduled_for: datetime,
        source: OccurrenceSource,
        *,
        state: OccurrenceState,
        message: str | None,
    ) -> ScheduleOccurrence:
        return ScheduleOccurrence(
            occurrence_key=SQLiteScheduleRepository.occurrence_key(
                schedule.schedule_id,
                scheduled_for,
            ),
            schedule_id=schedule.schedule_id,
            schedule_revision=schedule.revision,
            source=source,
            state=state,
            scheduled_for=scheduled_for,
            error_code=(ErrorCode.SCHEDULE if state is not OccurrenceState.PENDING else None),
            error_message=message,
            created_at=utc_now(),
            updated_at=utc_now(),
        )
