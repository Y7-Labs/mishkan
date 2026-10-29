"""Transactional command ledger, typed event queries, and bounded snapshots."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from mishkan.application import (
    ApplicationCommand,
    CommandResult,
    CommandStatus,
    SnapshotEnvelope,
)
from mishkan.domain.errors import ErrorCode, MishkanError
from mishkan.domain.identity import new_id
from mishkan.domain.time import utc_now
from mishkan.events import (
    EventEnvelope,
    EventHold,
    EventHoldScope,
    EventPage,
    EventRetentionPlan,
    EventRetentionPlanState,
    EventRetentionPolicy,
)
from mishkan.persistence.migration import SchemaManager
from mishkan.persistence.runtime import DatabaseTarget
from mishkan.persistence.sqlite import (
    AggregateRevisionRow,
    CommandRow,
    EventHoldRow,
    EventRetentionPlanRow,
    OutboxRow,
    RunRow,
    TaskRow,
    create_local_engine,
)
from mishkan.runtime import RunState


class SQLiteApplicationRepository:
    """Own command idempotence, optimistic revisions, and the durable event cursor."""

    def __init__(self, database_path: DatabaseTarget, *, busy_timeout_ms: int = 5_000) -> None:
        SchemaManager(database_path).require_current()
        self._engine = create_local_engine(
            database_path,
            busy_timeout_ms=busy_timeout_ms,
        )

    def refuse(
        self,
        command: ApplicationCommand,
        *,
        target_id: str,
        error: MishkanError,
        event_payload: Mapping[str, Any],
    ) -> CommandResult:
        """Persist one idempotent typed refusal without advancing target revision."""

        with Session(self._engine) as session, session.begin():
            existing = session.get(CommandRow, str(command.command_id))
            if existing is not None:
                return self._existing(existing, command)
            now = utc_now()
            event_row = OutboxRow(
                id=str(new_id()),
                schema_version="1.0",
                aggregate_id=target_id,
                entity_type=command.target_type,
                **self._event_dimensions(command, target_id, "security"),
                event_type="application.command_refused",
                source="mishkand.policy",
                payload=json.dumps(dict(event_payload), sort_keys=True, separators=(",", ":")),
                occurred_at=now.isoformat(),
                command_id=str(command.command_id),
                correlation_id=str(command.command_id),
                causation_id=None,
                sensitivity="security",
                published_at=None,
            )
            session.add(event_row)
            session.flush()
            result = CommandResult(
                command_id=command.command_id,
                status=CommandStatus.REFUSED,
                target_type=command.target_type,
                target_id=target_id,
                event_cursor=event_row.cursor,
                error=error.envelope,
                completed_at=now,
            )
            session.add(self._command_row(command, result))
            return result

    def accept(
        self,
        command: ApplicationCommand,
        *,
        target_id: str,
        event_type: str,
        result_payload: Mapping[str, Any] | None = None,
        event_payload: Mapping[str, Any] | None = None,
        source: str = "mishkan.application",
        sensitivity: str = "internal",
    ) -> CommandResult:
        try:
            with Session(self._engine) as session, session.begin():
                existing = session.get(CommandRow, str(command.command_id))
                if existing is not None:
                    return self._existing(existing, command)
                revision_row = session.get(AggregateRevisionRow, (command.target_type, target_id))
                current_revision = revision_row.revision if revision_row is not None else 0
                if (
                    command.expected_revision is not None
                    and command.expected_revision != current_revision
                ):
                    raise MishkanError(
                        ErrorCode.REVISION_MISMATCH,
                        "application command expected a stale aggregate revision",
                        details={
                            "target_type": command.target_type,
                            "target_id": target_id,
                            "expected": command.expected_revision,
                            "current": current_revision,
                        },
                    )
                next_revision = current_revision + 1
                now = utc_now()
                if revision_row is None:
                    session.add(
                        AggregateRevisionRow(
                            entity_type=command.target_type,
                            entity_id=target_id,
                            revision=next_revision,
                            updated_at=now.isoformat(),
                        )
                    )
                else:
                    revision_row.revision = next_revision
                    revision_row.updated_at = now.isoformat()
                event_row = OutboxRow(
                    id=str(new_id()),
                    schema_version="1.0",
                    aggregate_id=target_id,
                    entity_type=command.target_type,
                    **self._event_dimensions(command, target_id, sensitivity),
                    event_type=event_type,
                    source=source,
                    payload=json.dumps(
                        dict(event_payload or {}), sort_keys=True, separators=(",", ":")
                    ),
                    occurred_at=now.isoformat(),
                    command_id=str(command.command_id),
                    correlation_id=str(command.command_id),
                    causation_id=None,
                    sensitivity=sensitivity,
                    published_at=None,
                )
                session.add(event_row)
                session.flush()
                result = CommandResult(
                    command_id=command.command_id,
                    status=CommandStatus.ACCEPTED,
                    target_type=command.target_type,
                    target_id=target_id,
                    revision=next_revision,
                    event_cursor=event_row.cursor,
                    payload=dict(result_payload or {}),
                    completed_at=now,
                )
                session.add(self._command_row(command, result))
                session.flush()
                return result
        except IntegrityError as exc:
            with Session(self._engine) as session:
                existing = session.get(CommandRow, str(command.command_id))
                if existing is not None:
                    return self._existing(existing, command)
            raise MishkanError(
                ErrorCode.DUPLICATE_RESULT,
                "application command could not be committed uniquely",
            ) from exc

    def accept_with_mutation(
        self,
        command: ApplicationCommand,
        *,
        target_id: str,
        event_type: str,
        mutation: Callable[[Session, int], tuple[Mapping[str, Any], Mapping[str, Any]]],
        source: str = "mishkan.application",
        sensitivity: str = "internal",
    ) -> CommandResult:
        """Commit a state mutation, aggregate revision, event, and receipt together."""
        try:
            with Session(self._engine) as session, session.begin():
                existing = session.get(CommandRow, str(command.command_id))
                if existing is not None:
                    return self._existing(existing, command)
                revision_row = session.get(AggregateRevisionRow, (command.target_type, target_id))
                current_revision = revision_row.revision if revision_row is not None else 0
                if (
                    command.expected_revision is not None
                    and command.expected_revision != current_revision
                ):
                    raise MishkanError(
                        ErrorCode.REVISION_MISMATCH,
                        "application command expected a stale aggregate revision",
                        details={
                            "target_type": command.target_type,
                            "target_id": target_id,
                            "expected": command.expected_revision,
                            "current": current_revision,
                        },
                    )
                next_revision = current_revision + 1
                result_payload, event_payload = mutation(session, next_revision)
                now = utc_now()
                if revision_row is None:
                    session.add(
                        AggregateRevisionRow(
                            entity_type=command.target_type,
                            entity_id=target_id,
                            revision=next_revision,
                            updated_at=now.isoformat(),
                        )
                    )
                else:
                    revision_row.revision = next_revision
                    revision_row.updated_at = now.isoformat()
                event_row = OutboxRow(
                    id=str(new_id()),
                    schema_version="1.0",
                    aggregate_id=target_id,
                    entity_type=command.target_type,
                    **self._event_dimensions(command, target_id, sensitivity),
                    event_type=event_type,
                    source=source,
                    payload=json.dumps(dict(event_payload), sort_keys=True, separators=(",", ":")),
                    occurred_at=now.isoformat(),
                    command_id=str(command.command_id),
                    correlation_id=str(command.command_id),
                    causation_id=None,
                    sensitivity=sensitivity,
                    published_at=None,
                )
                session.add(event_row)
                session.flush()
                result = CommandResult(
                    command_id=command.command_id,
                    status=CommandStatus.ACCEPTED,
                    target_type=command.target_type,
                    target_id=target_id,
                    revision=next_revision,
                    event_cursor=event_row.cursor,
                    payload=dict(result_payload),
                    completed_at=now,
                )
                session.add(self._command_row(command, result))
                return result
        except IntegrityError as exc:
            with Session(self._engine) as session:
                existing = session.get(CommandRow, str(command.command_id))
                if existing is not None:
                    return self._existing(existing, command)
            raise MishkanError(
                ErrorCode.DUPLICATE_RESULT,
                "application command mutation could not be committed uniquely",
            ) from exc

    def reserve(self, command: ApplicationCommand, *, target_id: str) -> CommandResult | None:
        """Durably reserve an effectful command before executing it.

        A daemon crash leaves a stable refused receipt. Replaying that UUID therefore
        fails closed instead of repeating an effect whose outcome has not been
        reconciled.
        """
        with Session(self._engine) as session, session.begin():
            existing = session.get(CommandRow, str(command.command_id))
            if existing is not None:
                return self._existing(existing, command)
            revision_row = session.get(AggregateRevisionRow, (command.target_type, target_id))
            current_revision = revision_row.revision if revision_row is not None else 0
            if (
                command.expected_revision is not None
                and command.expected_revision != current_revision
            ):
                raise MishkanError(
                    ErrorCode.REVISION_MISMATCH,
                    "application command expected a stale aggregate revision",
                    details={
                        "target_type": command.target_type,
                        "target_id": target_id,
                        "expected": command.expected_revision,
                        "current": current_revision,
                    },
                )
            now = utc_now()
            interrupted = MishkanError(
                ErrorCode.RUN_INTERRUPTED,
                "command was interrupted before its effect could be accepted",
                details={
                    "command_id": str(command.command_id),
                    "reconciliation_required": True,
                    "automatic_retry": False,
                },
            )
            result = CommandResult(
                command_id=command.command_id,
                status=CommandStatus.REFUSED,
                target_type=command.target_type,
                target_id=target_id,
                error=interrupted.envelope,
                completed_at=now,
            )
            session.add(self._command_row(command, result))
            return None

    def complete_reserved(
        self,
        command: ApplicationCommand,
        *,
        target_id: str,
        event_type: str,
        result_payload: Mapping[str, Any] | None = None,
        event_payload: Mapping[str, Any] | None = None,
        source: str = "mishkan.application",
        sensitivity: str = "internal",
    ) -> CommandResult:
        """Atomically accept one reserved command, its revision, and its event."""
        with Session(self._engine) as session, session.begin():
            row = session.get(CommandRow, str(command.command_id))
            if row is None:
                raise MishkanError(ErrorCode.RUN_INTERRUPTED, "command was not reserved")
            if row.fingerprint != command.fingerprint:
                raise MishkanError(
                    ErrorCode.DUPLICATE_RESULT,
                    "command identity was already used for different content",
                    details={"command_id": str(command.command_id)},
                )
            current_result = CommandResult.model_validate_json(row.result_payload)
            if current_result.status is CommandStatus.ACCEPTED:
                return current_result
            if (
                current_result.status is not CommandStatus.REFUSED
                or current_result.error is None
                or not current_result.error.details.get("reconciliation_required")
            ):
                return current_result
            revision_row = session.get(AggregateRevisionRow, (command.target_type, target_id))
            current_revision = revision_row.revision if revision_row is not None else 0
            if (
                command.expected_revision is not None
                and command.expected_revision != current_revision
            ):
                raise MishkanError(
                    ErrorCode.REVISION_MISMATCH,
                    "aggregate revision changed while command effect was executing",
                    details={
                        "target_type": command.target_type,
                        "target_id": target_id,
                        "expected": command.expected_revision,
                        "current": current_revision,
                        "effect_reconciliation_required": True,
                    },
                )
            next_revision = current_revision + 1
            now = utc_now()
            if revision_row is None:
                session.add(
                    AggregateRevisionRow(
                        entity_type=command.target_type,
                        entity_id=target_id,
                        revision=next_revision,
                        updated_at=now.isoformat(),
                    )
                )
            else:
                revision_row.revision = next_revision
                revision_row.updated_at = now.isoformat()
            event_row = OutboxRow(
                id=str(new_id()),
                schema_version="1.0",
                aggregate_id=target_id,
                entity_type=command.target_type,
                **self._event_dimensions(command, target_id, sensitivity),
                event_type=event_type,
                source=source,
                payload=json.dumps(
                    dict(event_payload or {}), sort_keys=True, separators=(",", ":")
                ),
                occurred_at=now.isoformat(),
                command_id=str(command.command_id),
                correlation_id=str(command.command_id),
                causation_id=None,
                sensitivity=sensitivity,
                published_at=None,
            )
            session.add(event_row)
            session.flush()
            result = CommandResult(
                command_id=command.command_id,
                status=CommandStatus.ACCEPTED,
                target_type=command.target_type,
                target_id=target_id,
                revision=next_revision,
                event_cursor=event_row.cursor,
                payload=dict(result_payload or {}),
                completed_at=now,
            )
            row.status = result.status.value
            row.result_payload = result.model_dump_json()
            row.event_cursor = result.event_cursor
            row.completed_at = result.completed_at.isoformat()
            return result

    def verify_reserved_precondition(
        self,
        command: ApplicationCommand,
        *,
        target_id: str,
    ) -> None:
        """Recheck a reserved command immediately before its external effect.

        ``reserve`` deliberately happens before a target's in-process effect lock so
        duplicate command identities can be settled quickly.  The aggregate may have
        advanced while the command waited for that lock, so the expected revision is
        authoritative only after this second check.
        """
        with Session(self._engine) as session, session.begin():
            row = session.get(CommandRow, str(command.command_id))
            if row is None:
                raise MishkanError(ErrorCode.RUN_INTERRUPTED, "command was not reserved")
            if row.fingerprint != command.fingerprint:
                raise MishkanError(
                    ErrorCode.DUPLICATE_RESULT,
                    "command identity was already used for different content",
                    details={"command_id": str(command.command_id)},
                )
            current_result = CommandResult.model_validate_json(row.result_payload)
            if (
                current_result.status is not CommandStatus.REFUSED
                or current_result.error is None
                or not current_result.error.details.get("reconciliation_required")
            ):
                raise MishkanError(
                    ErrorCode.DUPLICATE_RESULT,
                    "reserved command is already settled",
                    details={"command_id": str(command.command_id)},
                )
            revision_row = session.get(AggregateRevisionRow, (command.target_type, target_id))
            current_revision = revision_row.revision if revision_row is not None else 0
            if (
                command.expected_revision is not None
                and command.expected_revision != current_revision
            ):
                raise MishkanError(
                    ErrorCode.REVISION_MISMATCH,
                    "aggregate revision changed before command effect dispatch",
                    details={
                        "target_type": command.target_type,
                        "target_id": target_id,
                        "expected": command.expected_revision,
                        "current": current_revision,
                        "effect_dispatched": False,
                    },
                )

    def fail_reserved(
        self,
        command: ApplicationCommand,
        *,
        target_id: str,
        error: MishkanError,
        event_payload: Mapping[str, Any] | None = None,
        sensitivity: str = "internal",
    ) -> CommandResult:
        """Atomically settle an authorized reserved command without accepting its effect."""
        with Session(self._engine) as session, session.begin():
            row = session.get(CommandRow, str(command.command_id))
            if row is None:
                raise MishkanError(ErrorCode.RUN_INTERRUPTED, "command was not reserved")
            if row.fingerprint != command.fingerprint:
                raise MishkanError(
                    ErrorCode.DUPLICATE_RESULT,
                    "command identity was already used for different content",
                )
            current = CommandResult.model_validate_json(row.result_payload)
            if (
                current.status is not CommandStatus.REFUSED
                or current.error is None
                or not current.error.details.get("reconciliation_required")
            ):
                return current
            now = utc_now()
            event_row = OutboxRow(
                id=str(new_id()),
                schema_version="1.0",
                aggregate_id=target_id,
                entity_type=command.target_type,
                **self._event_dimensions(command, target_id, sensitivity),
                event_type="application.command_effect_unaccepted",
                source="mishkand",
                payload=json.dumps(
                    {
                        "command_type": command.command_type,
                        "error_code": error.envelope.code,
                        **dict(event_payload or {}),
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                occurred_at=now.isoformat(),
                command_id=str(command.command_id),
                correlation_id=str(command.command_id),
                causation_id=None,
                sensitivity=sensitivity,
                published_at=None,
            )
            session.add(event_row)
            session.flush()
            result = CommandResult(
                command_id=command.command_id,
                status=CommandStatus.REFUSED,
                target_type=command.target_type,
                target_id=target_id,
                event_cursor=event_row.cursor,
                error=error.envelope,
                completed_at=now,
            )
            row.status = result.status.value
            row.result_payload = result.model_dump_json()
            row.event_cursor = result.event_cursor
            row.completed_at = result.completed_at.isoformat()
            return result

    def command_result(self, command_id: str) -> CommandResult | None:
        with Session(self._engine) as session:
            row = session.get(CommandRow, command_id)
            return (
                CommandResult.model_validate_json(row.result_payload) if row is not None else None
            )

    def has_accepted_result_for_target(
        self,
        *,
        command_type: str,
        target_type: str,
        target_id: str,
        result_payload: Mapping[str, Any],
    ) -> bool:
        """Prove an exact candidate was produced by a prior accepted command."""

        with Session(self._engine) as session:
            rows = session.scalars(
                select(CommandRow).where(
                    CommandRow.command_type == command_type,
                    CommandRow.target_type == target_type,
                    CommandRow.target_id == target_id,
                    CommandRow.status == CommandStatus.ACCEPTED.value,
                )
            ).all()
            return any(
                CommandResult.model_validate_json(row.result_payload).payload == result_payload
                for row in rows
            )

    def replay(self, command: ApplicationCommand) -> CommandResult | None:
        """Return an identical command result or reject reuse of its UUID."""
        with Session(self._engine) as session:
            row = session.get(CommandRow, str(command.command_id))
            return self._existing(row, command) if row is not None else None

    def require_expected_revision(self, command: ApplicationCommand, target_id: str) -> None:
        if command.expected_revision is None:
            return
        with Session(self._engine) as session:
            row = session.get(AggregateRevisionRow, (command.target_type, target_id))
            current = row.revision if row is not None else 0
        if current != command.expected_revision:
            raise MishkanError(
                ErrorCode.REVISION_MISMATCH,
                "application command expected a stale aggregate revision",
                details={"expected": command.expected_revision, "current": current},
            )

    def events(
        self,
        *,
        after_cursor: int = 0,
        limit: int = 100,
        event_types: tuple[str, ...] = (),
        entity_type: str | None = None,
        entity_id: str | None = None,
        run_id: str | None = None,
        task_id: str | None = None,
        identity_id: str | None = None,
        team_id: str | None = None,
        occurred_after: datetime | None = None,
        occurred_before: datetime | None = None,
        security_relevant: bool | None = None,
    ) -> EventPage:
        if limit < 1 or limit > 1_000:
            raise MishkanError(
                ErrorCode.OUTPUT_CONTRACT,
                "event query limit is outside the supported bound",
                details={"limit": limit, "minimum": 1, "maximum": 1000},
            )
        with Session(self._engine) as session:
            retained = session.scalar(select(func.min(OutboxRow.cursor))) or 0
            if retained and after_cursor and after_cursor < retained - 1:
                raise MishkanError(
                    ErrorCode.RUN_INTERRUPTED,
                    "event cursor is older than the retained stream",
                    details={
                        "category": "cursor_gap",
                        "after_cursor": after_cursor,
                        "retained_from_cursor": retained,
                        "snapshot_required": True,
                    },
                )
            statement = select(OutboxRow).where(OutboxRow.cursor > after_cursor)
            if event_types:
                statement = statement.where(OutboxRow.event_type.in_(event_types))
            if entity_type is not None:
                statement = statement.where(OutboxRow.entity_type == entity_type)
            if entity_id is not None:
                statement = statement.where(OutboxRow.aggregate_id == entity_id)
            if run_id is not None:
                statement = statement.where(OutboxRow.run_id == run_id)
            if task_id is not None:
                statement = statement.where(OutboxRow.task_id == task_id)
            if identity_id is not None:
                statement = statement.where(OutboxRow.identity_id == identity_id)
            if team_id is not None:
                statement = statement.where(OutboxRow.team_id == team_id)
            if occurred_after is not None:
                statement = statement.where(
                    OutboxRow.occurred_at >= self._normalized_time(occurred_after)
                )
            if occurred_before is not None:
                statement = statement.where(
                    OutboxRow.occurred_at <= self._normalized_time(occurred_before)
                )
            if security_relevant is not None:
                statement = statement.where(OutboxRow.security_relevant == security_relevant)
            rows = session.scalars(statement.order_by(OutboxRow.cursor).limit(limit)).all()
            events = tuple(self._event(row) for row in rows)
            return EventPage(
                after_cursor=after_cursor,
                next_cursor=events[-1].cursor if events else after_cursor,
                retained_from_cursor=retained,
                events=events,
            )

    def create_event_hold(
        self,
        *,
        scope: EventHoldScope,
        scope_id: str | None,
        reason: str,
        actor_id: str,
    ) -> EventHold:
        """Create an explicit evidence hold after proving a targeted entity exists."""
        candidate = EventHold(
            hold_id=new_id(),
            scope=scope,
            scope_id=scope_id,
            reason=reason,
            actor_id=actor_id,
            created_at=utc_now(),
        )
        with Session(self._engine) as session, session.begin():
            if scope is EventHoldScope.RUN and session.get(RunRow, scope_id) is None:
                raise MishkanError(ErrorCode.OUTPUT_CONTRACT, "event hold run does not exist")
            if scope is EventHoldScope.EVENT:
                observed = session.scalar(select(OutboxRow.id).where(OutboxRow.id == scope_id))
                if observed is None:
                    raise MishkanError(ErrorCode.OUTPUT_CONTRACT, "event hold event does not exist")
            session.add(
                EventHoldRow(
                    id=str(candidate.hold_id),
                    scope=candidate.scope.value,
                    scope_id=candidate.scope_id,
                    reason=candidate.reason,
                    actor_id=candidate.actor_id,
                    created_at=candidate.created_at.isoformat(),
                    released_at=None,
                )
            )
        return candidate

    def release_event_hold(self, hold_id: UUID) -> EventHold:
        """Release a hold idempotently while retaining its audit record."""
        with Session(self._engine) as session, session.begin():
            row = session.get(EventHoldRow, str(hold_id))
            if row is None:
                raise MishkanError(ErrorCode.OUTPUT_CONTRACT, "event hold does not exist")
            if row.released_at is None:
                row.released_at = utc_now().isoformat()
            session.flush()
            return self._hold(row)

    def event_holds(self, *, active_only: bool = False) -> tuple[EventHold, ...]:
        with Session(self._engine) as session:
            statement = select(EventHoldRow)
            if active_only:
                statement = statement.where(EventHoldRow.released_at.is_(None))
            rows = session.scalars(statement.order_by(EventHoldRow.created_at)).all()
            return tuple(self._hold(row) for row in rows)

    def plan_event_retention(self, policy: EventRetentionPolicy) -> EventRetentionPlan:
        """Persist a bounded candidate set without deleting any evidence."""
        now = utc_now()
        cutoff = now - timedelta(days=policy.max_age_days)
        with Session(self._engine) as session, session.begin():
            candidates = self._eligible_retention_rows(session, cutoff, policy.batch_size)
            plan = EventRetentionPlan(
                plan_id=new_id(),
                policy=policy,
                policy_fingerprint=policy.fingerprint,
                cutoff=cutoff,
                candidate_event_ids=tuple(UUID(row.id) for row in candidates),
                candidate_cursors=tuple(row.cursor for row in candidates),
                state=EventRetentionPlanState.PLANNED,
                deleted_count=0,
                created_at=now,
            )
            session.add(
                EventRetentionPlanRow(
                    id=str(plan.plan_id),
                    policy_payload=policy.model_dump_json(),
                    policy_fingerprint=plan.policy_fingerprint,
                    cutoff=cutoff.isoformat(),
                    candidates_payload=json.dumps(
                        [
                            {"event_id": str(event_id), "cursor": cursor}
                            for event_id, cursor in zip(
                                plan.candidate_event_ids,
                                plan.candidate_cursors,
                                strict=True,
                            )
                        ],
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                    state=plan.state.value,
                    deleted_count=0,
                    created_at=now.isoformat(),
                    applied_at=None,
                )
            )
        return plan

    def apply_event_retention(self, plan_id: UUID) -> EventRetentionPlan:
        """Apply a stored plan after rechecking current runs and active holds."""
        with Session(self._engine) as session, session.begin():
            row = session.get(EventRetentionPlanRow, str(plan_id))
            if row is None:
                raise MishkanError(ErrorCode.OUTPUT_CONTRACT, "event retention plan does not exist")
            if row.state == EventRetentionPlanState.APPLIED.value:
                return self._retention_plan(row)
            policy = EventRetentionPolicy.model_validate_json(row.policy_payload)
            if policy.fingerprint != row.policy_fingerprint:
                raise MishkanError(
                    ErrorCode.OUTPUT_CONTRACT,
                    "stored event retention policy fingerprint is invalid",
                )
            candidate_ids = {
                str(candidate["event_id"]) for candidate in json.loads(row.candidates_payload)
            }
            eligible = {
                candidate.id
                for candidate in self._eligible_retention_rows(
                    session,
                    datetime.fromisoformat(row.cutoff),
                    policy.batch_size,
                    candidate_ids=candidate_ids,
                )
            }
            for event_id in eligible:
                event_row = session.scalar(select(OutboxRow).where(OutboxRow.id == event_id))
                if event_row is not None:
                    session.delete(event_row)
            row.state = EventRetentionPlanState.APPLIED.value
            row.deleted_count = len(eligible)
            row.applied_at = utc_now().isoformat()
            session.flush()
            return self._retention_plan(row)

    def event_retention_plans(self) -> tuple[EventRetentionPlan, ...]:
        with Session(self._engine) as session:
            rows = session.scalars(
                select(EventRetentionPlanRow).order_by(EventRetentionPlanRow.created_at)
            ).all()
            return tuple(self._retention_plan(row) for row in rows)

    def snapshot(self, *, limit: int = 1_000) -> SnapshotEnvelope:
        self._query_bound(0, limit)
        with Session(self._engine) as session, session.begin():
            cursor = session.scalar(select(func.max(OutboxRow.cursor))) or 0
            runs = session.execute(
                select(RunRow.id, RunRow.status, RunRow.revision)
                .order_by(RunRow.created_at)
                .limit(limit)
            ).all()
            tasks = session.execute(
                select(TaskRow.id, TaskRow.run_id, TaskRow.status, TaskRow.revision)
                .order_by(TaskRow.run_id, TaskRow.position)
                .limit(limit)
            ).all()
            return SnapshotEnvelope(
                cursor=cursor,
                projections={
                    "runs": [
                        {"id": row.id, "status": row.status, "revision": row.revision}
                        for row in runs
                    ],
                    "tasks": [
                        {
                            "id": row.id,
                            "run_id": row.run_id,
                            "status": row.status,
                            "revision": row.revision,
                        }
                        for row in tasks
                    ],
                },
            )

    def runs(self, *, offset: int = 0, limit: int = 100) -> tuple[dict[str, Any], ...]:
        self._query_bound(offset, limit)
        with Session(self._engine) as session:
            rows = session.scalars(
                select(RunRow).order_by(RunRow.created_at, RunRow.id).offset(offset).limit(limit)
            ).all()
            return tuple(
                {
                    "id": row.id,
                    "status": row.status,
                    "revision": row.revision,
                    "cancellation_requested": row.cancellation_requested,
                    "execution_context": {
                        "kind": row.context_kind,
                        "context_id": row.context_id,
                        "revision": row.context_revision,
                    },
                    "repository_revision": row.repository_revision,
                    "created_at": row.created_at,
                    "updated_at": row.updated_at,
                }
                for row in rows
            )

    def tasks(
        self,
        run_id: str,
        *,
        offset: int = 0,
        limit: int = 100,
    ) -> tuple[dict[str, Any], ...]:
        self._query_bound(offset, limit)
        with Session(self._engine) as session:
            rows = session.scalars(
                select(TaskRow)
                .where(TaskRow.run_id == run_id)
                .order_by(TaskRow.position)
                .offset(offset)
                .limit(limit)
            ).all()
            return tuple(
                {
                    "id": row.id,
                    "task_id": row.task_key,
                    "status": row.status,
                    "revision": row.revision,
                    "attempt_count": row.attempt_count,
                    "updated_at": row.updated_at,
                }
                for row in rows
            )

    @staticmethod
    def _query_bound(offset: int, limit: int) -> None:
        if offset < 0 or limit < 1 or limit > 1_000:
            raise MishkanError(ErrorCode.OUTPUT_CONTRACT, "query bound is invalid")

    @staticmethod
    def _command_row(command: ApplicationCommand, result: CommandResult) -> CommandRow:
        return CommandRow(
            command_id=str(command.command_id),
            schema_version=command.schema_version,
            fingerprint=command.fingerprint,
            command_type=command.command_type,
            actor_id=command.actor_id,
            target_type=command.target_type,
            target_id=result.target_id,
            expected_revision=command.expected_revision,
            issued_at=command.issued_at.isoformat(),
            status=result.status.value,
            result_payload=result.model_dump_json(),
            event_cursor=result.event_cursor,
            completed_at=result.completed_at.isoformat(),
        )

    @staticmethod
    def _existing(row: CommandRow, command: ApplicationCommand) -> CommandResult:
        if row.fingerprint != command.fingerprint:
            raise MishkanError(
                ErrorCode.DUPLICATE_RESULT,
                "command identity was already used for different content",
                details={"command_id": str(command.command_id)},
            )
        return CommandResult.model_validate_json(row.result_payload)

    @staticmethod
    def _event(row: OutboxRow) -> EventEnvelope:
        return EventEnvelope(
            event_id=UUID(row.id),
            cursor=row.cursor,
            schema_version=row.schema_version,
            event_type=row.event_type,
            source=row.source,
            entity_type=row.entity_type,
            entity_id=row.aggregate_id,
            run_id=row.run_id,
            task_id=row.task_id,
            identity_id=row.identity_id,
            team_id=row.team_id,
            security_relevant=row.security_relevant,
            occurred_at=datetime.fromisoformat(row.occurred_at),
            command_id=UUID(row.command_id) if row.command_id is not None else None,
            correlation_id=(UUID(row.correlation_id) if row.correlation_id is not None else None),
            causation_id=UUID(row.causation_id) if row.causation_id is not None else None,
            sensitivity=row.sensitivity,
            payload=json.loads(row.payload),
        )

    @staticmethod
    def _hold(row: EventHoldRow) -> EventHold:
        return EventHold(
            hold_id=UUID(row.id),
            scope=EventHoldScope(row.scope),
            scope_id=row.scope_id,
            reason=row.reason,
            actor_id=row.actor_id,
            created_at=datetime.fromisoformat(row.created_at),
            released_at=(datetime.fromisoformat(row.released_at) if row.released_at else None),
        )

    @staticmethod
    def _retention_plan(row: EventRetentionPlanRow) -> EventRetentionPlan:
        candidates = json.loads(row.candidates_payload)
        return EventRetentionPlan(
            plan_id=UUID(row.id),
            policy=EventRetentionPolicy.model_validate_json(row.policy_payload),
            policy_fingerprint=row.policy_fingerprint,
            cutoff=datetime.fromisoformat(row.cutoff),
            candidate_event_ids=tuple(UUID(value["event_id"]) for value in candidates),
            candidate_cursors=tuple(int(value["cursor"]) for value in candidates),
            state=EventRetentionPlanState(row.state),
            deleted_count=row.deleted_count,
            created_at=datetime.fromisoformat(row.created_at),
            applied_at=(datetime.fromisoformat(row.applied_at) if row.applied_at else None),
        )

    @staticmethod
    def _eligible_retention_rows(
        session: Session,
        cutoff: datetime,
        limit: int,
        *,
        candidate_ids: set[str] | None = None,
    ) -> list[OutboxRow]:
        active_holds = session.scalars(
            select(EventHoldRow).where(EventHoldRow.released_at.is_(None))
        ).all()
        if any(row.scope == EventHoldScope.ALL.value for row in active_holds):
            return []
        held_runs = {row.scope_id for row in active_holds if row.scope == EventHoldScope.RUN.value}
        held_events = {
            row.scope_id for row in active_holds if row.scope == EventHoldScope.EVENT.value
        }
        terminal = {RunState.COMPLETED.value, RunState.FAILED.value, RunState.CANCELLED.value}
        incomplete_runs = set(
            session.scalars(select(RunRow.id).where(RunRow.status.not_in(terminal))).all()
        )
        statement = select(OutboxRow).where(OutboxRow.occurred_at < cutoff.isoformat())
        if candidate_ids is not None:
            if not candidate_ids:
                return []
            statement = statement.where(OutboxRow.id.in_(candidate_ids))
        protected_runs = incomplete_runs | {value for value in held_runs if value is not None}
        if protected_runs:
            statement = statement.where(
                (OutboxRow.run_id.is_(None)) | (OutboxRow.run_id.not_in(protected_runs))
            )
        protected_events = {value for value in held_events if value is not None}
        if protected_events:
            statement = statement.where(OutboxRow.id.not_in(protected_events))
        return list(session.scalars(statement.order_by(OutboxRow.cursor).limit(limit)).all())

    @staticmethod
    def _normalized_time(value: datetime) -> str:
        if value.tzinfo is None or value.utcoffset() is None:
            raise MishkanError(
                ErrorCode.OUTPUT_CONTRACT,
                "event time filters require an explicit timezone offset",
            )
        return value.astimezone(UTC).isoformat()

    @staticmethod
    def _event_dimensions(
        command: ApplicationCommand,
        target_id: str,
        sensitivity: str,
    ) -> dict[str, object]:
        nested = command.payload.get("request")
        request = nested if isinstance(nested, dict) else {}

        def selected(name: str) -> str | None:
            value = command.payload.get(name, request.get(name))
            return str(value) if isinstance(value, str) and value else None

        return {
            "run_id": target_id if command.target_type == "run" else selected("run_id"),
            "task_id": selected("task_id"),
            "identity_id": command.actor_id,
            "team_id": selected("team_id"),
            "security_relevant": sensitivity == "security",
        }
