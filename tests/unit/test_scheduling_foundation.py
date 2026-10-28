from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy.orm import Session

from mishkan.application import CommandResult, CommandStatus
from mishkan.config.models import SchedulingConfig
from mishkan.domain.errors import ErrorCode, MishkanError
from mishkan.persistence import SchemaManager
from mishkan.persistence.sqlite import create_local_engine
from mishkan.scheduling import (
    CronTriggerSpec,
    DateTriggerSpec,
    OccurrenceSource,
    OccurrenceState,
    ScheduleCreateRequest,
    ScheduleExportTarget,
    ScheduleFireRequest,
    ScheduleMissionRequest,
    ScheduleState,
    SQLiteScheduleRepository,
    render_schedule_export,
)
from mishkan.scheduling.driver import ScheduleDriver
from mishkan.scheduling.service import ScheduleMutationService


def _repository(tmp_path: Path) -> SQLiteScheduleRepository:
    database = tmp_path / "mishkan.db"
    SchemaManager(database).initialize()
    return SQLiteScheduleRepository(database)


def _request(*, run_at: datetime | None = None) -> ScheduleCreateRequest:
    return ScheduleCreateRequest(
        mission=ScheduleMissionRequest(
            project_id="project-a",
            objective="Inspect and improve the repository",
            inputs={"priority": "high"},
        ),
        trigger=DateTriggerSpec(run_at=run_at or datetime(2030, 1, 1, tzinfo=UTC)),
    )


def _scheduling_config(*, coalesce: bool = True, grace: int = 600) -> SchedulingConfig:
    return SchedulingConfig(
        enabled=True,
        poll_seconds=1,
        misfire_grace_seconds=grace,
        coalesce=coalesce,
        max_catch_up_occurrences=100,
        max_attempts=3,
        retry_initial_seconds=1,
        retry_multiplier=2,
        retry_max_seconds=60,
        history_page_limit=100,
    )


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def test_schedule_contract_rejects_ambiguous_time_and_cron() -> None:
    with pytest.raises(ValidationError, match="timezone offset"):
        DateTriggerSpec(run_at=datetime(2030, 1, 1))
    with pytest.raises(ValidationError, match="five fields"):
        CronTriggerSpec(expression="00 12 ** **", timezone="UTC")
    with pytest.raises(ValidationError, match="unknown IANA timezone"):
        CronTriggerSpec(expression="0 12 * * *", timezone="Mars/Olympus")


def test_schedule_target_derives_stable_non_literal_overlap_key() -> None:
    first = ScheduleMissionRequest(project_id="secret-project", objective="Run checks")
    second = ScheduleMissionRequest(project_id="secret-project", objective="Different work")
    other = ScheduleMissionRequest(project_id="other-project", objective="Run checks")
    assert first.target_key == second.target_key
    assert first.target_key != other.target_key
    assert "secret-project" not in first.target_key


def test_schedule_repository_persists_state_and_idempotent_occurrence(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    request = _request()
    created = repository.create(
        request,
        actor_id="operator",
        policy_fingerprint="a" * 64,
        next_fire_at=request.trigger.run_at,
    )
    assert repository.schedule(created.schedule_id) == created
    assert repository.list_schedules() == (created,)
    assert repository.due_schedules(created.next_fire_at, limit=10) == (created,)

    occurrence = repository.create_occurrence(
        created,
        occurrence_key="external:one",
        source=OccurrenceSource.EXTERNAL,
        scheduled_for=created.next_fire_at,
    )
    assert (
        repository.create_occurrence(
            created,
            occurrence_key="external:one",
            source=OccurrenceSource.EXTERNAL,
            scheduled_for=created.next_fire_at,
        )
        == occurrence
    )
    with pytest.raises(MishkanError) as mismatch:
        repository.create_occurrence(
            created,
            occurrence_key="external:one",
            source=OccurrenceSource.EXTERNAL,
            scheduled_for=created.next_fire_at + timedelta(seconds=1),
        )
    assert mismatch.value.envelope.code is ErrorCode.DUPLICATE_RESULT


def test_occurrence_dispatch_and_retry_are_durable(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    request = _request()
    schedule = repository.create(
        request,
        actor_id="operator",
        policy_fingerprint="b" * 64,
        next_fire_at=request.trigger.run_at,
    )
    occurrence = repository.create_occurrence(
        schedule,
        occurrence_key="manual:one",
        source=OccurrenceSource.MANUAL,
        scheduled_for=request.trigger.run_at,
    )
    first_command = uuid4()
    dispatching = repository.claim_dispatch(
        occurrence.occurrence_id,
        run_command_id=first_command,
    )
    assert dispatching.state is OccurrenceState.DISPATCHING
    assert dispatching.attempt_count == 1

    retry_at = datetime.now(UTC) + timedelta(seconds=10)
    retrying = repository.settle_occurrence(
        occurrence.occurrence_id,
        state=OccurrenceState.RETRY_WAITING,
        next_attempt_at=retry_at,
        error_code=ErrorCode.OPTIONAL_DEPENDENCY,
        error_message="temporary dependency failure",
    )
    assert repository.due_retries(retry_at, limit=10) == (retrying,)
    second_command = uuid4()
    retried = repository.claim_dispatch(
        occurrence.occurrence_id,
        run_command_id=second_command,
    )
    assert retried.attempt_count == 2
    assert retried.run_command_id == second_command


def test_schedule_revision_prevents_stale_control(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    request = _request()
    schedule = repository.create(
        request,
        actor_id="operator",
        policy_fingerprint="c" * 64,
        next_fire_at=request.trigger.run_at,
    )
    paused = repository.update_schedule(
        schedule.schedule_id,
        expected_revision=1,
        state=ScheduleState.PAUSED,
        next_fire_at=None,
        event_type="schedule.paused",
    )
    assert paused.revision == 2
    with pytest.raises(MishkanError) as stale:
        repository.update_schedule(
            schedule.schedule_id,
            expected_revision=1,
            state=ScheduleState.REMOVED,
            next_fire_at=None,
            event_type="schedule.removed",
        )
    assert stale.value.envelope.code is ErrorCode.REVISION_MISMATCH


def test_schedule_mutation_atomically_records_due_one_shot(tmp_path: Path) -> None:
    database = tmp_path / "mishkan.db"
    SchemaManager(database).initialize()
    repository = SQLiteScheduleRepository(database)
    service = ScheduleMutationService(_scheduling_config(), repository)
    request = _request(run_at=datetime(2030, 1, 1, tzinfo=UTC))
    engine = create_local_engine(database)
    with Session(engine) as session, session.begin():
        created = service.create(
            session,
            request,
            actor_id="operator",
            policy_fingerprint="d" * 64,
            revision=1,
            now=datetime(2029, 1, 1, tzinfo=UTC),
        ).schedule
    with Session(engine) as session, session.begin():
        result = service.fire(
            session,
            created.schedule_id,
            ScheduleFireRequest(
                source=OccurrenceSource.INTERNAL,
                observed_at=datetime(2030, 1, 1, 0, 1, tzinfo=UTC),
            ),
            expected_revision=1,
        )
    assert result.schedule.state is ScheduleState.COMPLETED
    assert result.schedule.next_fire_at is None
    assert result.occurrence is not None
    assert result.occurrence.state is OccurrenceState.PENDING
    assert repository.occurrence(result.occurrence.occurrence_id) == result.occurrence


def test_cron_coalesces_missed_slots_and_preserves_visible_history(tmp_path: Path) -> None:
    database = tmp_path / "mishkan.db"
    SchemaManager(database).initialize()
    repository = SQLiteScheduleRepository(database)
    service = ScheduleMutationService(_scheduling_config(coalesce=True), repository)
    request = ScheduleCreateRequest(
        mission=ScheduleMissionRequest(project_id="project-a", objective="Run checks"),
        trigger=CronTriggerSpec(expression="* * * * *", timezone="UTC"),
    )
    engine = create_local_engine(database)
    with Session(engine) as session, session.begin():
        created = service.create(
            session,
            request,
            actor_id="operator",
            policy_fingerprint="e" * 64,
            revision=1,
            now=datetime(2029, 12, 31, 23, 59, 30, tzinfo=UTC),
        ).schedule
    with Session(engine) as session, session.begin():
        result = service.fire(
            session,
            created.schedule_id,
            ScheduleFireRequest(
                source=OccurrenceSource.EXTERNAL,
                observed_at=datetime(2030, 1, 1, 0, 3, 10, tzinfo=UTC),
            ),
            expected_revision=1,
        )
    assert len(result.coalesced) == 3
    assert all(item.state is OccurrenceState.COALESCED for item in result.coalesced)
    assert result.occurrence is not None
    assert result.occurrence.scheduled_for == datetime(2030, 1, 1, 0, 3, tzinfo=UTC)
    assert result.schedule.next_fire_at == datetime(2030, 1, 1, 0, 4, tzinfo=UTC)


def test_schedule_misfire_is_terminal_and_never_dispatched(tmp_path: Path) -> None:
    database = tmp_path / "mishkan.db"
    SchemaManager(database).initialize()
    repository = SQLiteScheduleRepository(database)
    service = ScheduleMutationService(_scheduling_config(grace=1), repository)
    request = _request(run_at=datetime(2030, 1, 1, tzinfo=UTC))
    engine = create_local_engine(database)
    with Session(engine) as session, session.begin():
        created = service.create(
            session,
            request,
            actor_id="operator",
            policy_fingerprint="f" * 64,
            revision=1,
            now=datetime(2029, 1, 1, tzinfo=UTC),
        ).schedule
    with Session(engine) as session, session.begin():
        result = service.fire(
            session,
            created.schedule_id,
            ScheduleFireRequest(
                source=OccurrenceSource.INTERNAL,
                observed_at=datetime(2030, 1, 1, 0, 0, 2, tzinfo=UTC),
            ),
            expected_revision=1,
        )
    assert result.occurrence is not None
    assert result.occurrence.state is OccurrenceState.MISFIRED
    assert result.occurrence.error_code == ErrorCode.SCHEDULE


@pytest.mark.anyio
async def test_driver_journals_command_before_retryable_dispatch(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    request = _request()
    schedule = repository.create(
        request,
        actor_id="operator",
        policy_fingerprint="a" * 64,
        next_fire_at=request.trigger.run_at,
    )
    occurrence = repository.create_occurrence(
        schedule,
        occurrence_key="manual:retry",
        source=OccurrenceSource.MANUAL,
        scheduled_for=request.trigger.run_at,
    )
    observed_commands = []

    async def execute(command: object, principal_id: str) -> CommandResult:
        from mishkan.application import ApplicationCommand

        assert isinstance(command, ApplicationCommand)
        durable = repository.occurrence(occurrence.occurrence_id)
        assert durable.state is OccurrenceState.DISPATCHING
        assert durable.run_command_id == command.command_id
        assert principal_id == "operator"
        observed_commands.append(command)
        error = MishkanError(
            ErrorCode.OPTIONAL_DEPENDENCY,
            "temporary model dependency failure",
            retryable=True,
        )
        return CommandResult(
            command_id=command.command_id,
            status=CommandStatus.REFUSED,
            target_type="run",
            target_id="local-instance",
            error=error.envelope,
        )

    driver = ScheduleDriver(_scheduling_config(), repository, execute)
    await driver.dispatch(occurrence.occurrence_id)

    settled = repository.occurrence(occurrence.occurrence_id)
    assert len(observed_commands) == 1
    assert settled.state is OccurrenceState.RETRY_WAITING
    assert settled.attempt_count == 1
    assert settled.next_attempt_at is not None


@pytest.mark.anyio
async def test_driver_marks_unprovable_interrupted_dispatch_uncertain(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    request = _request()
    schedule = repository.create(
        request,
        actor_id="operator",
        policy_fingerprint="b" * 64,
        next_fire_at=request.trigger.run_at,
    )
    occurrence = repository.create_occurrence(
        schedule,
        occurrence_key="manual:interrupted",
        source=OccurrenceSource.MANUAL,
        scheduled_for=request.trigger.run_at,
    )
    repository.claim_dispatch(occurrence.occurrence_id, run_command_id=uuid4())

    async def should_not_execute(_command: object, _principal_id: str) -> CommandResult:
        raise AssertionError("restart reconciliation must not replay an unproven effect")

    driver = ScheduleDriver(_scheduling_config(), repository, should_not_execute)
    await driver.reconcile()

    settled = repository.occurrence(occurrence.occurrence_id)
    assert settled.state is OccurrenceState.UNCERTAIN
    assert settled.error_code == ErrorCode.RUN_INTERRUPTED


@pytest.mark.anyio
async def test_restart_reconciles_committed_pending_occurrence(tmp_path: Path) -> None:
    import asyncio

    repository = _repository(tmp_path)
    request = _request()
    schedule = repository.create(
        request,
        actor_id="operator",
        policy_fingerprint="b" * 64,
        next_fire_at=request.trigger.run_at,
    )
    occurrence = repository.create_occurrence(
        schedule,
        occurrence_key="manual:pending-before-crash",
        source=OccurrenceSource.MANUAL,
        scheduled_for=request.trigger.run_at,
    )
    dispatched = asyncio.Event()

    async def execute(command: object, _principal_id: str) -> CommandResult:
        from mishkan.application import ApplicationCommand

        assert isinstance(command, ApplicationCommand)
        dispatched.set()
        error = MishkanError(
            ErrorCode.OPTIONAL_DEPENDENCY,
            "temporary dependency failure",
            retryable=True,
        )
        return CommandResult(
            command_id=command.command_id,
            status=CommandStatus.REFUSED,
            target_type="run",
            error=error.envelope,
        )

    driver = ScheduleDriver(_scheduling_config(), repository, execute)
    await driver.reconcile()
    await asyncio.wait_for(dispatched.wait(), timeout=1)
    for _ in range(100):
        settled = repository.occurrence(occurrence.occurrence_id)
        if settled.state is OccurrenceState.RETRY_WAITING:
            break
        await asyncio.sleep(0.01)
    assert settled.state is OccurrenceState.RETRY_WAITING
    assert settled.attempt_count == 1


@pytest.mark.anyio
async def test_overlap_blocks_second_dispatch_before_run_effect(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    request = _request()
    schedule = repository.create(
        request,
        actor_id="operator",
        policy_fingerprint="b" * 64,
        next_fire_at=request.trigger.run_at,
    )
    first = repository.create_occurrence(
        schedule,
        occurrence_key="manual:first",
        source=OccurrenceSource.MANUAL,
        scheduled_for=request.trigger.run_at,
    )
    second = repository.create_occurrence(
        schedule,
        occurrence_key="manual:second",
        source=OccurrenceSource.MANUAL,
        scheduled_for=request.trigger.run_at + timedelta(seconds=1),
    )
    repository.claim_dispatch(first.occurrence_id, run_command_id=uuid4())

    async def should_not_execute(_command: object, _principal_id: str) -> CommandResult:
        raise AssertionError("overlap must be blocked before the run command")

    driver = ScheduleDriver(_scheduling_config(), repository, should_not_execute)
    await driver.dispatch(second.occurrence_id)

    settled = repository.occurrence(second.occurrence_id)
    assert settled.state is OccurrenceState.OVERLAP_BLOCKED
    assert settled.attempt_count == 0


def test_os_exports_invoke_the_same_daemon_trigger_surface(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    request = ScheduleCreateRequest(
        mission=ScheduleMissionRequest(project_id="project-a", objective="Run checks"),
        trigger=CronTriggerSpec(expression="5 * * * *", timezone="Africa/Ouagadougou"),
    )
    schedule = repository.create(
        request,
        actor_id="operator",
        policy_fingerprint="c" * 64,
        next_fire_at=datetime(2030, 1, 1, 0, 5, tzinfo=UTC),
    )
    for target in ScheduleExportTarget:
        rendered = render_schedule_export(schedule, target)
        assert f"schedule trigger {schedule.schedule_id}" in rendered or (
            "<string>schedule</string><string>trigger</string>" in rendered
            and f"<string>{schedule.schedule_id}</string>" in rendered
        )
        assert "run.initialize" not in rendered
