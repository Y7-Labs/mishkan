"""Daemon-local wake-up, durable dispatch, retry, and restart reconciliation."""

from __future__ import annotations

import asyncio
import math
from collections.abc import Awaitable, Callable, Coroutine
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

from apscheduler.schedulers.asyncio import AsyncIOScheduler  # type: ignore[import-untyped]
from apscheduler.triggers.interval import IntervalTrigger  # type: ignore[import-untyped]

from mishkan.application import ApplicationCommand, CommandResult, CommandStatus
from mishkan.config.models import SchedulingConfig
from mishkan.domain.errors import ErrorCode
from mishkan.domain.time import utc_now
from mishkan.scheduling.models import (
    OccurrenceSource,
    OccurrenceState,
    OverlapMode,
    ScheduleFireRequest,
    ScheduleOccurrence,
    ScheduleRecord,
)
from mishkan.scheduling.repository import SQLiteScheduleRepository

CommandExecutor = Callable[[ApplicationCommand, str], Awaitable[CommandResult]]


class ScheduleDriver:
    """Use APScheduler only as a wake-up clock over SQLite-authoritative state."""

    def __init__(
        self,
        config: SchedulingConfig,
        repository: SQLiteScheduleRepository,
        execute_command: CommandExecutor,
    ) -> None:
        self._config = config
        self._repository = repository
        self._execute = execute_command
        self._scheduler = AsyncIOScheduler(timezone=UTC)
        self._tick_lock = asyncio.Lock()
        self._dispatch_tasks: set[asyncio.Task[None]] = set()

    def start(self) -> None:
        self._scheduler.add_job(
            self.tick,
            IntervalTrigger(seconds=self._config.poll_seconds, timezone=UTC),
            id="mishkan-schedule-wakeup",
            replace_existing=True,
            coalesce=True,
            max_instances=1,
            misfire_grace_time=max(1, math.ceil(self._config.misfire_grace_seconds)),
        )
        self._scheduler.start()
        self._spawn(self.reconcile())

    async def stop(self) -> None:
        if self._scheduler.running:
            self._scheduler.shutdown(wait=False)
        tasks = tuple(self._dispatch_tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    def enqueue(self, occurrence_id: UUID) -> None:
        self._spawn(self.dispatch(occurrence_id))

    async def tick(self, now: datetime | None = None) -> None:
        if self._tick_lock.locked():
            return
        async with self._tick_lock:
            observed = now or utc_now()
            due = await asyncio.to_thread(
                self._repository.due_schedules,
                observed,
                limit=self._config.max_catch_up_occurrences,
            )
            for schedule in due:
                await self._trigger(schedule, observed)
            retries = await asyncio.to_thread(
                self._repository.due_retries,
                observed,
                limit=self._config.max_catch_up_occurrences,
            )
            for occurrence in retries:
                self._spawn(self.dispatch(occurrence.occurrence_id))

    async def reconcile(self) -> None:
        interrupted = await asyncio.to_thread(
            self._repository.interrupted_dispatches,
            limit=self._config.max_catch_up_occurrences,
        )
        for occurrence in interrupted:
            await self._reconcile_occurrence(occurrence)
        pending = await asyncio.to_thread(
            self._repository.pending_occurrences,
            limit=self._config.max_catch_up_occurrences,
        )
        for occurrence in pending:
            self.enqueue(occurrence.occurrence_id)
        await self.tick()

    async def dispatch(self, occurrence_id: UUID) -> None:
        occurrence = await asyncio.to_thread(self._repository.occurrence, occurrence_id)
        if occurrence.state not in {OccurrenceState.PENDING, OccurrenceState.RETRY_WAITING}:
            return
        schedule = await asyncio.to_thread(self._repository.schedule, occurrence.schedule_id)
        if schedule.overlap_mode is OverlapMode.BLOCK and await asyncio.to_thread(
            self._repository.has_active_overlap,
            schedule,
            excluding=occurrence_id,
        ):
            await asyncio.to_thread(
                self._repository.settle_occurrence,
                occurrence_id,
                state=OccurrenceState.OVERLAP_BLOCKED,
                error_code=ErrorCode.SCHEDULE,
                error_message="another run for the same schedule target remains active",
            )
            return

        run_command_id = uuid5(
            NAMESPACE_URL,
            f"mishkan:schedule-dispatch:{occurrence_id}:{occurrence.attempt_count + 1}",
        )
        claimed = await asyncio.to_thread(
            self._repository.claim_dispatch,
            occurrence_id,
            run_command_id=run_command_id,
        )
        command = self._run_command(schedule, claimed)
        try:
            result = await self._execute(command, schedule.created_by)
        except asyncio.CancelledError:
            raise
        except Exception:
            await self._reconcile_claim(claimed)
            return
        await self._settle_result(claimed, result)

    async def _trigger(self, schedule: ScheduleRecord, observed: datetime) -> None:
        assert schedule.next_fire_at is not None
        command_id = uuid5(
            NAMESPACE_URL,
            f"mishkan:schedule-trigger:{schedule.schedule_id}:{schedule.next_fire_at.isoformat()}",
        )
        command = ApplicationCommand(
            command_id=command_id,
            command_type="schedule.trigger",
            actor_id=schedule.created_by,
            target_type="schedule",
            target_id=str(schedule.schedule_id),
            expected_revision=schedule.revision,
            payload={
                "request": ScheduleFireRequest(
                    source=OccurrenceSource.INTERNAL,
                    observed_at=observed,
                ).model_dump(mode="json")
            },
        )
        result = await self._execute(command, schedule.created_by)
        if result.status is not CommandStatus.ACCEPTED:
            return
        raw = result.payload.get("occurrence")
        if not isinstance(raw, dict):
            return
        occurrence = ScheduleOccurrence.model_validate(raw)
        if occurrence.state is OccurrenceState.PENDING:
            self.enqueue(occurrence.occurrence_id)

    async def _reconcile_occurrence(self, occurrence: ScheduleOccurrence) -> None:
        if occurrence.run_command_id is None:
            await asyncio.to_thread(
                self._repository.settle_occurrence,
                occurrence.occurrence_id,
                state=OccurrenceState.UNCERTAIN,
                error_code=ErrorCode.RUN_INTERRUPTED,
                error_message="dispatching occurrence has no durable run command identity",
            )
            return
        await self._reconcile_claim(occurrence)

    async def _reconcile_claim(self, occurrence: ScheduleOccurrence) -> None:
        assert occurrence.run_command_id is not None
        result = await asyncio.to_thread(
            self._repository.application_result,
            occurrence.run_command_id,
        )
        if result is None:
            await asyncio.to_thread(
                self._repository.settle_occurrence,
                occurrence.occurrence_id,
                state=OccurrenceState.UNCERTAIN,
                error_code=ErrorCode.RUN_INTERRUPTED,
                error_message="run command outcome is absent after interrupted dispatch",
            )
            return
        await self._settle_result(occurrence, result)

    async def _settle_result(
        self,
        occurrence: ScheduleOccurrence,
        result: CommandResult,
    ) -> None:
        if result.status is CommandStatus.ACCEPTED:
            run_id = result.payload.get("run_id")
            if not isinstance(run_id, str) or not run_id:
                await asyncio.to_thread(
                    self._repository.settle_occurrence,
                    occurrence.occurrence_id,
                    state=OccurrenceState.UNCERTAIN,
                    error_code=ErrorCode.OUTPUT_CONTRACT,
                    error_message="accepted scheduled run omitted its durable run identity",
                )
                return
            await asyncio.to_thread(
                self._repository.settle_occurrence,
                occurrence.occurrence_id,
                state=OccurrenceState.ACCEPTED,
                run_id=run_id,
            )
            return
        error = result.error
        if (
            error is not None
            and error.retryable
            and occurrence.attempt_count < self._config.max_attempts
        ):
            delay = min(
                self._config.retry_initial_seconds
                * (self._config.retry_multiplier ** max(0, occurrence.attempt_count - 1)),
                self._config.retry_max_seconds,
            )
            await asyncio.to_thread(
                self._repository.settle_occurrence,
                occurrence.occurrence_id,
                state=OccurrenceState.RETRY_WAITING,
                next_attempt_at=utc_now() + timedelta(seconds=delay),
                error_code=error.code,
                error_message=error.message,
            )
            return
        state = (
            OccurrenceState.UNCERTAIN
            if error is not None and error.code is ErrorCode.RUN_INTERRUPTED
            else OccurrenceState.FAILED
        )
        await asyncio.to_thread(
            self._repository.settle_occurrence,
            occurrence.occurrence_id,
            state=state,
            error_code=(error.code if error is not None else ErrorCode.SCHEDULE),
            error_message=(error.message if error is not None else "scheduled run was refused"),
        )

    @staticmethod
    def _run_command(
        schedule: ScheduleRecord,
        occurrence: ScheduleOccurrence,
    ) -> ApplicationCommand:
        assert occurrence.run_command_id is not None
        mission = schedule.mission
        if mission.workspace_id is None:
            command_type = "run.initialize"
            payload: dict[str, object] = {
                "schema_version": "1.0",
                "objective": mission.rendered_objective,
            }
        else:
            assert mission.outcome_id is not None
            command_type = "run.prospective.create"
            payload = {
                "schema_version": "1.0",
                "workspace_id": mission.workspace_id,
                "objective": mission.rendered_objective,
                "outcome_id": mission.outcome_id,
            }
        return ApplicationCommand(
            command_id=occurrence.run_command_id,
            command_type=command_type,
            actor_id=schedule.created_by,
            target_type="run",
            payload=payload,
        )

    def _spawn(self, awaitable: Coroutine[Any, Any, None]) -> None:
        task: asyncio.Task[None] = asyncio.create_task(awaitable)
        self._dispatch_tasks.add(task)

        def settled(completed: asyncio.Task[None]) -> None:
            self._dispatch_tasks.discard(completed)
            if not completed.cancelled():
                completed.exception()

        task.add_done_callback(settled)
