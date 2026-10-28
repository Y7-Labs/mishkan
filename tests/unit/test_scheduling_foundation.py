from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import ValidationError

from mishkan.domain.errors import ErrorCode, MishkanError
from mishkan.persistence import SchemaManager
from mishkan.scheduling import (
    CronTriggerSpec,
    DateTriggerSpec,
    OccurrenceSource,
    OccurrenceState,
    ScheduleCreateRequest,
    ScheduleMissionRequest,
    ScheduleState,
    SQLiteScheduleRepository,
)


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
