"""Asynchronous cadence for independent Mission Crew health evaluation."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from uuid import NAMESPACE_URL, uuid5

from mishkan.application import ApplicationCommand, CommandResult
from mishkan.config.models import TeamEffectivenessConfig
from mishkan.domain.time import utc_now
from mishkan.events import EventEnvelope
from mishkan.missions.models import MissionRunBinding
from mishkan.missions.repository import SQLiteMissionRepository
from mishkan.missions.team_effectiveness import (
    MissionCollaborationPlan,
    TeamHealthEvaluationRequest,
)
from mishkan.missions.team_repository import SQLiteTeamEffectivenessRepository
from mishkan.persistence.application import SQLiteApplicationRepository

CommandExecutor = Callable[[ApplicationCommand, str], Awaitable[CommandResult]]
logger = logging.getLogger(__name__)


class TeamHealthCadenceDriver:
    """Wake independent evaluation outside task completion when public cadence is due."""

    def __init__(
        self,
        config: TeamEffectivenessConfig,
        application: SQLiteApplicationRepository,
        missions: SQLiteMissionRepository,
        teams: SQLiteTeamEffectivenessRepository,
        execute_command: CommandExecutor,
    ) -> None:
        self._config = config
        self._application = application
        self._missions = missions
        self._teams = teams
        self._execute = execute_command
        self._task: asyncio.Task[None] | None = None
        self._lock = asyncio.Lock()
        self._last_failure: dict[str, str] | None = None
        self._last_success_at: datetime | None = None

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._loop(), name="team-health-cadence")

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        await asyncio.gather(self._task, return_exceptions=True)
        self._task = None

    async def tick(self, now: datetime | None = None) -> None:
        if self._lock.locked():
            return
        async with self._lock:
            requests = await asyncio.to_thread(self._due_requests, now or utc_now())
            for request in requests:
                command_id = uuid5(
                    NAMESPACE_URL,
                    "mishkan:team-health-cadence:"
                    f"{request.mission_id}:{request.collaboration_plan_version}:"
                    f"{request.evidence_cursor}",
                )
                command = ApplicationCommand(
                    command_id=command_id,
                    command_type="mission.health.evaluate",
                    actor_id=request.evaluator_identity,
                    target_type="team_health_evaluation",
                    target_id=str(request.request_id),
                    expected_revision=0,
                    payload={"request": request.model_dump(mode="json")},
                )
                result = await self._execute(command, request.evaluator_identity)
                if result.status.value == "refused":
                    code = result.error.code if result.error is not None else "unknown"
                    raise RuntimeError(f"team-health cadence command was durably refused: {code}")
            self._last_success_at = now or utc_now()

    async def _loop(self) -> None:
        poll_seconds = max(1.0, min(30.0, self._config.evaluation_cadence_seconds / 10))
        while True:
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                # A failed independent evaluator must not stop task execution or the daemon.
                self._last_failure = {
                    "error_type": type(exc).__name__,
                    "failed_at": utc_now().isoformat(),
                }
                logger.exception("team-health cadence tick failed")
            await asyncio.sleep(poll_seconds)

    def status(self) -> dict[str, object]:
        return {
            "running": self._task is not None and not self._task.done(),
            "last_success_at": (
                self._last_success_at.isoformat() if self._last_success_at is not None else None
            ),
            "last_failure": dict(self._last_failure) if self._last_failure is not None else None,
        }

    def _due_requests(self, now: datetime) -> tuple[TeamHealthEvaluationRequest, ...]:
        plans = self._active_plans()
        latest_health = {
            plan.mission_id: records[0]
            for plan in plans
            if (records := self._teams.health(str(plan.mission_id), limit=1))
        }
        requests: list[TeamHealthEvaluationRequest] = []
        for plan in plans:
            previous = latest_health.get(plan.mission_id)
            after_cursor = previous.evidence_cursor if previous is not None else 0
            interval_started_at = previous.created_at if previous is not None else plan.created_at
            events = self._mission_events(plan, after_cursor=after_cursor)
            if not events:
                continue
            due_by_events = len(events) >= self._config.evaluation_cadence_events
            due_by_time = now - interval_started_at >= timedelta(
                seconds=self._config.evaluation_cadence_seconds
            )
            if not due_by_events and not due_by_time:
                continue
            evaluator = next(
                (
                    item.identity_id
                    for item in plan.responsibilities
                    if item.identity_id not in plan.production_owner_ids
                    and {"assignment:evaluation", "assignment:audit"}.intersection(
                        item.decision_rights
                    )
                ),
                None,
            )
            if evaluator is None:
                continue
            bounded = events[: self._config.evidence_max_events]
            references = tuple(
                f"event:{item.event_id}" for item in bounded[: self._config.evidence_max_artifacts]
            )
            cursor = max(item.cursor for item in bounded)
            request_id = uuid5(
                NAMESPACE_URL,
                f"mishkan:team-health-request:{plan.mission_id}:{plan.version}:{cursor}",
            )
            requests.append(
                TeamHealthEvaluationRequest(
                    request_id=request_id,
                    mission_id=plan.mission_id,
                    collaboration_plan_version=plan.version,
                    interval_started_at=interval_started_at,
                    interval_ended_at=now,
                    evidence_cursor=cursor,
                    evaluator_identity=evaluator,
                    evidence_references=references,
                )
            )
        return tuple(requests)

    def _active_plans(self) -> tuple[MissionCollaborationPlan, ...]:
        page_size = self._config.max_history_candidates
        offset = 0
        plans: list[MissionCollaborationPlan] = []
        while True:
            page = self._teams.active_collaboration_plans(offset=offset, limit=page_size)
            plans.extend(page)
            if len(page) < page_size:
                return tuple(plans)
            offset += len(page)

    def _mission_events(
        self, plan: MissionCollaborationPlan, *, after_cursor: int
    ) -> tuple[EventEnvelope, ...]:
        events = list(
            self._application.events(
                after_cursor=after_cursor,
                limit=min(1_000, self._config.evidence_max_events),
                entity_id=str(plan.mission_id),
            ).events
        )
        bindings: tuple[MissionRunBinding, ...] = self._missions.run_bindings(str(plan.mission_id))
        remaining = self._config.evidence_max_events - len(events)
        for run_id in dict.fromkeys(item.run_id for item in bindings):
            if remaining <= 0:
                break
            page = self._application.events(
                after_cursor=after_cursor,
                limit=min(1_000, remaining),
                run_id=run_id,
            )
            events.extend(page.events)
            remaining -= len(page.events)
        internal_health_events = {
            "mission.team_health_evaluated",
            "mission.team_signal_recorded",
        }
        unique = {
            item.event_id: item for item in events if item.event_type not in internal_health_events
        }.values()
        return tuple(sorted(unique, key=lambda item: item.cursor))
