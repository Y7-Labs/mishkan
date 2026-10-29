"""Transactional worker heartbeat, lease, delivery, and acceptance authority."""

from __future__ import annotations

import hashlib
import hmac
import json
from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from mishkan.domain.errors import ErrorCode, MishkanError
from mishkan.domain.identity import new_id
from mishkan.domain.time import require_aware, utc_now
from mishkan.persistence.migration import SchemaManager
from mishkan.persistence.runtime import DatabaseTarget
from mishkan.persistence.sqlite import (
    AcceptanceRow,
    OutboxRow,
    PlanRow,
    RemoteTaskEnvelopeRow,
    RemoteTaskPlacementRow,
    ResultRow,
    RunRow,
    TaskRow,
    WorkerCompletionRow,
    WorkerHeartbeatRow,
    WorkerIdentityRow,
    WorkerLeaseRow,
    WorkerTaskAcceptanceRow,
    create_local_engine,
)
from mishkan.planning.models import InitializationResult, PlanTask, ReviewDecision
from mishkan.runtime import RunState, TaskState
from mishkan.workers.models import (
    CapabilityRequirement,
    EffectReportState,
    ImmutableTaskEnvelope,
    RemoteEnvelopeState,
    RemotePlacement,
    RemotePlacementRequest,
    RemotePlacementState,
    TaskResourceLimits,
    WorkerAvailability,
    WorkerClaimRequest,
    WorkerCompletion,
    WorkerCompletionState,
    WorkerCompletionSubmission,
    WorkerHeartbeat,
    WorkerHeartbeatRecord,
    WorkerLease,
    WorkerLeaseState,
    WorkerState,
    WorkerTaskClaim,
)


class WorkerCoordinationRepository:
    """Own the distributed delivery state while run acceptance stays daemon-owned."""

    def __init__(
        self,
        database: DatabaseTarget,
        *,
        heartbeat_timeout_seconds: int,
        lease_seconds: int,
        max_worker_concurrency: int,
        max_completion_bytes: int,
        busy_timeout_ms: int = 5_000,
    ) -> None:
        if heartbeat_timeout_seconds < 1 or lease_seconds < 1:
            raise MishkanError(ErrorCode.CONFIGURATION, "worker timing bounds are invalid")
        if max_worker_concurrency < 1 or max_completion_bytes < 1:
            raise MishkanError(ErrorCode.CONFIGURATION, "worker resource bounds are invalid")
        SchemaManager(database).require_current()
        self._engine = create_local_engine(database, busy_timeout_ms=busy_timeout_ms)
        self._heartbeat_timeout_seconds = heartbeat_timeout_seconds
        self._lease_seconds = lease_seconds
        self._max_worker_concurrency = max_worker_concurrency
        self._max_completion_bytes = max_completion_bytes

    def record_heartbeat(self, heartbeat: WorkerHeartbeat) -> WorkerHeartbeatRecord:
        now = utc_now()
        payload = heartbeat.model_dump_json()
        with Session(self._engine) as session, session.begin():
            worker = self._active_worker(session, heartbeat.worker_id, lock=True)
            if heartbeat.resources.max_concurrency > self._max_worker_concurrency:
                raise MishkanError(
                    ErrorCode.WORKER,
                    "worker advertised concurrency exceeds the configured bound",
                )
            active_lease_ids = set(heartbeat.active_lease_ids)
            if active_lease_ids:
                observed = set(
                    session.scalars(
                        select(WorkerLeaseRow.id).where(
                            WorkerLeaseRow.worker_id == str(heartbeat.worker_id),
                            WorkerLeaseRow.state == WorkerLeaseState.ACTIVE.value,
                            WorkerLeaseRow.id.in_(str(item) for item in active_lease_ids),
                        )
                    ).all()
                )
                unknown = sorted(
                    str(item) for item in active_lease_ids if str(item) not in observed
                )
                if unknown:
                    raise MishkanError(
                        ErrorCode.WORKER,
                        "worker heartbeat advertises an unknown active lease",
                        details={"lease_ids": unknown},
                    )
            row = session.scalar(
                select(WorkerHeartbeatRow)
                .where(WorkerHeartbeatRow.worker_id == str(heartbeat.worker_id))
                .with_for_update()
            )
            if row is not None and heartbeat.sequence <= row.sequence:
                if (
                    heartbeat.sequence == row.sequence
                    and heartbeat.fingerprint == row.advertisement_fingerprint
                ):
                    return self._heartbeat_record(row, worker.state, now)
                raise MishkanError(
                    ErrorCode.REVISION_MISMATCH,
                    "worker heartbeat sequence is stale or reused",
                    details={"current": row.sequence, "received": heartbeat.sequence},
                )
            expires_at = now + timedelta(seconds=self._heartbeat_timeout_seconds)
            if row is None:
                row = WorkerHeartbeatRow(
                    worker_id=str(heartbeat.worker_id),
                    sequence=heartbeat.sequence,
                    advertisement_fingerprint=heartbeat.fingerprint,
                    payload=payload,
                    observed_at=now.isoformat(),
                    expires_at=expires_at.isoformat(),
                )
                session.add(row)
            else:
                row.sequence = heartbeat.sequence
                row.advertisement_fingerprint = heartbeat.fingerprint
                row.payload = payload
                row.observed_at = now.isoformat()
                row.expires_at = expires_at.isoformat()
            self._event(
                session,
                aggregate_id=str(heartbeat.worker_id),
                entity_type="worker",
                event_type="worker.heartbeat_recorded",
                payload={
                    "worker_id": str(heartbeat.worker_id),
                    "sequence": heartbeat.sequence,
                    "expires_at": expires_at.isoformat(),
                },
                security_relevant=False,
            )
            session.flush()
            return self._heartbeat_record(row, worker.state, now)

    def heartbeat(self, worker_id: UUID, *, now: datetime | None = None) -> WorkerHeartbeatRecord:
        observed_now = require_aware(now) if now is not None else utc_now()
        with Session(self._engine) as session:
            worker = session.get(WorkerIdentityRow, str(worker_id))
            row = session.get(WorkerHeartbeatRow, str(worker_id))
            if worker is None or row is None:
                raise MishkanError(ErrorCode.WORKER, "worker heartbeat does not exist")
            return self._heartbeat_record(row, worker.state, observed_now)

    def compatible_workers(
        self,
        requirements: tuple[CapabilityRequirement, ...],
        resources: TaskResourceLimits,
        *,
        now: datetime | None = None,
    ) -> tuple[WorkerHeartbeatRecord, ...]:
        observed_now = require_aware(now) if now is not None else utc_now()
        with Session(self._engine) as session:
            rows = session.execute(
                select(WorkerHeartbeatRow, WorkerIdentityRow)
                .join(WorkerIdentityRow, WorkerIdentityRow.id == WorkerHeartbeatRow.worker_id)
                .where(WorkerIdentityRow.state == WorkerState.ACTIVE.value)
                .order_by(WorkerHeartbeatRow.observed_at.desc(), WorkerHeartbeatRow.worker_id)
            ).all()
            compatible: list[WorkerHeartbeatRecord] = []
            for heartbeat_row, worker_row in rows:
                record = self._heartbeat_record(heartbeat_row, worker_row.state, observed_now)
                if record.availability is not WorkerAvailability.ONLINE:
                    continue
                if record.resources.active_tasks >= record.resources.max_concurrency:
                    continue
                if resources.memory_bytes is not None and (
                    record.resources.available_memory_bytes < resources.memory_bytes
                ):
                    continue
                if resources.cpu_count is not None and (
                    record.resources.logical_cpu_count < resources.cpu_count
                ):
                    continue
                if not all(item.satisfied_by(record.capabilities) for item in requirements):
                    continue
                compatible.append(record)
            return tuple(
                sorted(
                    compatible,
                    key=lambda item: (
                        item.resources.active_tasks / item.resources.max_concurrency,
                        str(item.worker_id),
                    ),
                )
            )

    def queue_placement(self, request: RemotePlacementRequest) -> RemotePlacement:
        with Session(self._engine) as session, session.begin():
            return self.queue_placement_in_session(session, request)

    def queue_placement_in_session(
        self,
        session: Session,
        request: RemotePlacementRequest,
    ) -> RemotePlacement:
        """Queue a placement inside the authoritative application-command transaction."""

        now = utc_now()
        if request.deadline <= now:
            raise MishkanError(ErrorCode.WORKER, "remote placement deadline has expired")
        run = session.scalar(
            select(RunRow).where(RunRow.id == str(request.run_id)).with_for_update()
        )
        task = session.scalar(
            select(TaskRow)
            .where(
                TaskRow.run_id == str(request.run_id),
                TaskRow.task_key == request.task_id,
            )
            .with_for_update()
        )
        if run is None or task is None:
            raise MishkanError(ErrorCode.WORKER, "remote placement task does not exist")
        if run.status != RunState.RUNNING.value or run.cancellation_requested:
            raise MishkanError(ErrorCode.RUN_INTERRUPTED, "run does not accept remote work")
        if task.status != TaskState.ELIGIBLE.value:
            raise MishkanError(
                ErrorCode.REVISION_MISMATCH,
                "task is not eligible for remote placement",
                details={"state": task.status},
            )
        if request.preferred_worker_id is not None:
            self._active_worker(session, request.preferred_worker_id, lock=False)
        existing = session.scalar(
            select(RemoteTaskPlacementRow)
            .where(RemoteTaskPlacementRow.task_row_id == task.id)
            .with_for_update()
        )
        if existing is not None:
            if existing.request_fingerprint != request.fingerprint:
                raise MishkanError(
                    ErrorCode.DUPLICATE_RESULT,
                    "remote task already has a different placement request",
                )
            return self._placement(existing)
        row = RemoteTaskPlacementRow(
            id=str(request.id),
            run_id=run.id,
            task_row_id=task.id,
            task_key=task.task_key,
            state=RemotePlacementState.QUEUED.value,
            preferred_worker_id=(
                str(request.preferred_worker_id)
                if request.preferred_worker_id is not None
                else None
            ),
            selected_worker_id=None,
            current_envelope_id=None,
            request_fingerprint=request.fingerprint,
            request_payload=request.model_dump_json(),
            revision=1,
            created_at=request.created_at.isoformat(),
            updated_at=now.isoformat(),
        )
        session.add(row)
        self._event(
            session,
            aggregate_id=run.id,
            entity_type="run",
            event_type="worker.task_placement_queued",
            payload={
                "placement_id": row.id,
                "task_id": task.task_key,
                "preferred_worker_id": row.preferred_worker_id,
            },
            run_id=run.id,
            task_id=task.task_key,
        )
        session.flush()
        return self._placement(row)

    def claim_next(
        self,
        worker_id: UUID,
        request: WorkerClaimRequest,
    ) -> WorkerTaskClaim | None:
        """Atomically bind the first compatible queued placement to one worker claim."""

        now = utc_now()
        self._require_digest(request.lease_token_digest)
        with Session(self._engine) as session, session.begin():
            self._active_worker(session, worker_id, lock=True)
            previous = session.scalar(
                select(WorkerLeaseRow)
                .where(WorkerLeaseRow.claim_id == str(request.id))
                .with_for_update()
            )
            if previous is not None:
                self._require_token(previous, request.lease_token_digest)
                if previous.worker_id != str(worker_id):
                    raise MishkanError(ErrorCode.WORKER, "worker claim belongs to another identity")
                envelope_row = session.get(RemoteTaskEnvelopeRow, previous.envelope_id)
                if envelope_row is None:
                    raise MishkanError(ErrorCode.WORKER, "claimed task envelope is missing")
                return WorkerTaskClaim(
                    id=request.id,
                    created_at=request.created_at,
                    claim_id=request.id,
                    envelope=self._envelope(envelope_row),
                    lease=self._lease(previous),
                )
            heartbeat = session.scalar(
                select(WorkerHeartbeatRow)
                .where(WorkerHeartbeatRow.worker_id == str(worker_id))
                .with_for_update()
            )
            if heartbeat is None or datetime.fromisoformat(heartbeat.expires_at) <= now:
                raise MishkanError(ErrorCode.WORKER, "worker heartbeat is unavailable or stale")
            advertisement = WorkerHeartbeat.model_validate_json(heartbeat.payload)
            active_count = int(
                session.scalar(
                    select(func.count(WorkerLeaseRow.id)).where(
                        WorkerLeaseRow.worker_id == str(worker_id),
                        WorkerLeaseRow.state == WorkerLeaseState.ACTIVE.value,
                    )
                )
                or 0
            )
            capacity = min(
                advertisement.resources.max_concurrency,
                self._max_worker_concurrency,
            )
            if active_count >= capacity:
                return None
            placements = session.scalars(
                select(RemoteTaskPlacementRow)
                .where(
                    RemoteTaskPlacementRow.state == RemotePlacementState.QUEUED.value,
                    (
                        (RemoteTaskPlacementRow.preferred_worker_id.is_(None))
                        | (RemoteTaskPlacementRow.preferred_worker_id == str(worker_id))
                    ),
                )
                .order_by(RemoteTaskPlacementRow.created_at, RemoteTaskPlacementRow.id)
                .with_for_update(skip_locked=True)
            ).all()
            selected: tuple[RemoteTaskPlacementRow, RemotePlacementRequest] | None = None
            for placement in placements:
                placement_request = RemotePlacementRequest.model_validate_json(
                    placement.request_payload
                )
                if placement_request.deadline <= now:
                    placement.state = RemotePlacementState.CANCELLED.value
                    placement.revision += 1
                    placement.updated_at = now.isoformat()
                    continue
                if not all(
                    requirement.satisfied_by(advertisement.capabilities)
                    for requirement in placement_request.capability_requirements
                ):
                    continue
                try:
                    self._require_resource_capacity(advertisement, placement_request.resources)
                except MishkanError:
                    continue
                selected = placement, placement_request
                break
            if selected is None:
                return None
            placement, placement_request = selected
            run = session.scalar(
                select(RunRow).where(RunRow.id == placement.run_id).with_for_update()
            )
            task = session.scalar(
                select(TaskRow).where(TaskRow.id == placement.task_row_id).with_for_update()
            )
            plan = session.scalar(select(PlanRow).where(PlanRow.run_id == placement.run_id))
            if run is None or task is None or plan is None:
                raise MishkanError(ErrorCode.WORKER, "remote placement authority is incomplete")
            envelope = ImmutableTaskEnvelope(
                run_id=UUID(run.id),
                task_row_id=UUID(task.id),
                task_id=task.task_key,
                task_attempt=task.attempt_count + 1,
                worker_id=worker_id,
                execution_context_revision=run.context_revision,
                repository_id=run.repository_id,
                repository_revision=run.repository_revision,
                plan_fingerprint=plan.fingerprint,
                task_contract=PlanTask.model_validate_json(task.contract).model_dump(mode="json"),
                role_definition_fingerprint=(placement_request.role_definition_fingerprint),
                inputs=placement_request.inputs,
                policy_fingerprint=placement_request.policy_fingerprint,
                path_scopes=placement_request.path_scopes,
                capability_requirements=placement_request.capability_requirements,
                resources=placement_request.resources,
                deadline=placement_request.deadline,
                stateful_effects=placement_request.stateful_effects,
            )
            self._validate_envelope_against_authority(envelope, run, task, plan)
            if task.status != TaskState.ELIGIBLE.value:
                raise MishkanError(
                    ErrorCode.REVISION_MISMATCH,
                    "placed task is no longer eligible",
                )
            lease_expiry = min(
                envelope.deadline,
                now + timedelta(seconds=self._lease_seconds),
            )
            lease = WorkerLease(
                id=new_id(),
                created_at=now,
                envelope_id=envelope.id,
                worker_id=worker_id,
                state=WorkerLeaseState.ACTIVE,
                revision=1,
                issued_at=now,
                expires_at=lease_expiry,
                last_renewed_at=now,
            )
            session.add(
                RemoteTaskEnvelopeRow(
                    id=str(envelope.id),
                    run_id=run.id,
                    task_row_id=task.id,
                    task_key=task.task_key,
                    task_attempt=envelope.task_attempt,
                    worker_id=str(worker_id),
                    state=RemoteEnvelopeState.LEASED.value,
                    fingerprint=envelope.fingerprint,
                    stateful_effects=envelope.stateful_effects,
                    payload=envelope.model_dump_json(),
                    deadline=envelope.deadline.isoformat(),
                    created_at=envelope.created_at.isoformat(),
                    updated_at=now.isoformat(),
                )
            )
            session.add(
                WorkerLeaseRow(
                    id=str(lease.id),
                    envelope_id=str(envelope.id),
                    worker_id=str(worker_id),
                    claim_id=str(request.id),
                    token_digest=request.lease_token_digest,
                    state=lease.state.value,
                    revision=lease.revision,
                    issued_at=lease.issued_at.isoformat(),
                    expires_at=lease.expires_at.isoformat(),
                    last_renewed_at=lease.last_renewed_at.isoformat(),
                    completed_at=None,
                )
            )
            task.status = TaskState.LEASED.value
            task.attempt_count = envelope.task_attempt
            task.revision += 1
            task.updated_at = now.isoformat()
            placement.state = RemotePlacementState.LEASED.value
            placement.selected_worker_id = str(worker_id)
            placement.current_envelope_id = str(envelope.id)
            placement.revision += 1
            placement.updated_at = now.isoformat()
            self._event(
                session,
                aggregate_id=run.id,
                entity_type="run",
                event_type="worker.task_claimed",
                payload={
                    "claim_id": str(request.id),
                    "placement_id": placement.id,
                    "worker_id": str(worker_id),
                    "envelope_id": str(envelope.id),
                    "lease_id": str(lease.id),
                    "task_attempt": envelope.task_attempt,
                },
                run_id=run.id,
                task_id=task.task_key,
            )
            session.flush()
            return WorkerTaskClaim(
                id=request.id,
                created_at=request.created_at,
                claim_id=request.id,
                envelope=envelope,
                lease=lease,
            )

    def issue_lease(
        self,
        envelope: ImmutableTaskEnvelope,
        *,
        lease_id: UUID,
        token_digest: str,
    ) -> WorkerLease:
        self._require_digest(token_digest)
        now = utc_now()
        if envelope.deadline <= now:
            raise MishkanError(ErrorCode.WORKER, "remote task deadline has expired")
        try:
            with Session(self._engine) as session, session.begin():
                self._active_worker(session, envelope.worker_id, lock=True)
                heartbeat = session.scalar(
                    select(WorkerHeartbeatRow)
                    .where(WorkerHeartbeatRow.worker_id == str(envelope.worker_id))
                    .with_for_update()
                )
                if heartbeat is None or datetime.fromisoformat(heartbeat.expires_at) <= now:
                    raise MishkanError(ErrorCode.WORKER, "worker heartbeat is unavailable or stale")
                advertisement = WorkerHeartbeat.model_validate_json(heartbeat.payload)
                if advertisement.resources.active_tasks >= advertisement.resources.max_concurrency:
                    raise MishkanError(ErrorCode.WORKER, "worker has no advertised task capacity")
                active_lease_count = session.scalar(
                    select(func.count(WorkerLeaseRow.id)).where(
                        WorkerLeaseRow.worker_id == str(envelope.worker_id),
                        WorkerLeaseRow.state == WorkerLeaseState.ACTIVE.value,
                    )
                )
                authoritative_limit = min(
                    advertisement.resources.max_concurrency,
                    self._max_worker_concurrency,
                )
                if int(active_lease_count or 0) >= authoritative_limit:
                    raise MishkanError(
                        ErrorCode.WORKER,
                        "worker has reached its authoritative lease capacity",
                    )
                if not all(
                    item.satisfied_by(advertisement.capabilities)
                    for item in envelope.capability_requirements
                ):
                    raise MishkanError(
                        ErrorCode.WORKER,
                        "worker does not satisfy the immutable task requirements",
                    )
                self._require_resource_capacity(advertisement, envelope.resources)
                run = session.scalar(
                    select(RunRow).where(RunRow.id == str(envelope.run_id)).with_for_update()
                )
                task = session.scalar(
                    select(TaskRow).where(TaskRow.id == str(envelope.task_row_id)).with_for_update()
                )
                plan = session.scalar(select(PlanRow).where(PlanRow.run_id == str(envelope.run_id)))
                self._validate_envelope_against_authority(envelope, run, task, plan)
                assert task is not None
                if task.status != TaskState.ELIGIBLE.value:
                    raise MishkanError(
                        ErrorCode.REVISION_MISMATCH,
                        "task is not eligible for a remote lease",
                        details={"state": task.status},
                    )
                if envelope.task_attempt != task.attempt_count + 1:
                    raise MishkanError(
                        ErrorCode.REVISION_MISMATCH,
                        "remote task attempt is not the next accepted attempt",
                    )
                lease_expiry = min(
                    envelope.deadline,
                    now + timedelta(seconds=self._lease_seconds),
                )
                session.add(
                    RemoteTaskEnvelopeRow(
                        id=str(envelope.id),
                        run_id=str(envelope.run_id),
                        task_row_id=str(envelope.task_row_id),
                        task_key=envelope.task_id,
                        task_attempt=envelope.task_attempt,
                        worker_id=str(envelope.worker_id),
                        state=RemoteEnvelopeState.LEASED.value,
                        fingerprint=envelope.fingerprint,
                        stateful_effects=envelope.stateful_effects,
                        payload=envelope.model_dump_json(),
                        deadline=envelope.deadline.isoformat(),
                        created_at=envelope.created_at.isoformat(),
                        updated_at=now.isoformat(),
                    )
                )
                lease = WorkerLease(
                    id=lease_id,
                    created_at=now,
                    envelope_id=envelope.id,
                    worker_id=envelope.worker_id,
                    state=WorkerLeaseState.ACTIVE,
                    revision=1,
                    issued_at=now,
                    expires_at=lease_expiry,
                    last_renewed_at=now,
                )
                session.add(
                    WorkerLeaseRow(
                        id=str(lease.id),
                        envelope_id=str(envelope.id),
                        worker_id=str(envelope.worker_id),
                        token_digest=token_digest,
                        state=lease.state.value,
                        revision=lease.revision,
                        issued_at=lease.issued_at.isoformat(),
                        expires_at=lease.expires_at.isoformat(),
                        last_renewed_at=lease.last_renewed_at.isoformat(),
                        completed_at=None,
                    )
                )
                task.status = TaskState.LEASED.value
                task.attempt_count = envelope.task_attempt
                task.revision += 1
                task.updated_at = now.isoformat()
                self._event(
                    session,
                    aggregate_id=str(envelope.run_id),
                    entity_type="run",
                    event_type="worker.task_leased",
                    payload={
                        "worker_id": str(envelope.worker_id),
                        "envelope_id": str(envelope.id),
                        "lease_id": str(lease.id),
                        "task_id": envelope.task_id,
                        "task_attempt": envelope.task_attempt,
                        "expires_at": lease_expiry.isoformat(),
                    },
                    run_id=str(envelope.run_id),
                    task_id=envelope.task_id,
                )
                session.flush()
                return lease
        except IntegrityError as exc:
            raise MishkanError(
                ErrorCode.DUPLICATE_RESULT,
                "remote task envelope, lease, or token identity already exists",
            ) from exc

    def begin_execution(self, worker_id: UUID, lease_id: UUID, *, token_digest: str) -> WorkerLease:
        now = utc_now()
        with Session(self._engine) as session, session.begin():
            lease, envelope = self._active_lease(
                session,
                worker_id,
                lease_id,
                token_digest=token_digest,
                now=now,
            )
            if envelope.state == RemoteEnvelopeState.EXECUTING.value:
                return self._lease(lease)
            if envelope.state != RemoteEnvelopeState.LEASED.value:
                raise MishkanError(ErrorCode.WORKER, "remote task envelope cannot start")
            task = session.get(TaskRow, envelope.task_row_id)
            if task is None or task.status != TaskState.LEASED.value:
                raise MishkanError(ErrorCode.WORKER, "leased task state is inconsistent")
            envelope.state = RemoteEnvelopeState.EXECUTING.value
            envelope.updated_at = now.isoformat()
            task.status = TaskState.EXECUTING.value
            task.revision += 1
            task.updated_at = now.isoformat()
            self._event(
                session,
                aggregate_id=envelope.run_id,
                entity_type="run",
                event_type="worker.task_execution_started",
                payload={"lease_id": str(lease_id), "worker_id": str(worker_id)},
                run_id=envelope.run_id,
                task_id=envelope.task_key,
            )
            return self._lease(lease)

    def renew_lease(
        self,
        worker_id: UUID,
        lease_id: UUID,
        *,
        token_digest: str,
        expected_revision: int,
    ) -> WorkerLease:
        now = utc_now()
        with Session(self._engine) as session, session.begin():
            lease, envelope = self._active_lease(
                session,
                worker_id,
                lease_id,
                token_digest=token_digest,
                now=now,
            )
            if lease.revision != expected_revision:
                raise MishkanError(
                    ErrorCode.REVISION_MISMATCH,
                    "worker lease revision changed",
                    details={"expected": expected_revision, "current": lease.revision},
                )
            deadline = datetime.fromisoformat(envelope.deadline)
            renewed_until = min(deadline, now + timedelta(seconds=self._lease_seconds))
            if renewed_until <= now:
                raise MishkanError(ErrorCode.WORKER, "remote task deadline has expired")
            lease.revision += 1
            lease.expires_at = renewed_until.isoformat()
            lease.last_renewed_at = now.isoformat()
            self._event(
                session,
                aggregate_id=envelope.run_id,
                entity_type="run",
                event_type="worker.lease_renewed",
                payload={
                    "lease_id": str(lease_id),
                    "worker_id": str(worker_id),
                    "revision": lease.revision,
                    "expires_at": renewed_until.isoformat(),
                },
                run_id=envelope.run_id,
                task_id=envelope.task_key,
                security_relevant=False,
            )
            return self._lease(lease)

    def deliver_completion(
        self,
        worker_id: UUID,
        submission: WorkerCompletionSubmission,
        *,
        token_digest: str,
    ) -> WorkerCompletion:
        payload = submission.model_dump_json()
        if len(payload.encode()) > self._max_completion_bytes:
            raise MishkanError(ErrorCode.WORKER, "worker completion exceeds configured bounds")
        now = utc_now()
        with Session(self._engine) as session, session.begin():
            existing = session.get(WorkerCompletionRow, str(submission.id))
            if existing is not None:
                if existing.fingerprint != submission.fingerprint:
                    raise MishkanError(
                        ErrorCode.DUPLICATE_RESULT,
                        "worker completion identity was reused for different content",
                    )
                return self._completion(existing)
            envelope = session.scalar(
                select(RemoteTaskEnvelopeRow)
                .where(RemoteTaskEnvelopeRow.id == str(submission.envelope_id))
                .with_for_update()
            )
            lease = session.scalar(
                select(WorkerLeaseRow)
                .where(WorkerLeaseRow.id == str(submission.lease_id))
                .with_for_update()
            )
            if envelope is None or lease is None:
                raise MishkanError(ErrorCode.WORKER, "completion lease or envelope does not exist")
            if (
                envelope.worker_id != str(worker_id)
                or lease.worker_id != str(worker_id)
                or lease.envelope_id != envelope.id
            ):
                raise MishkanError(ErrorCode.WORKER, "completion authority does not match worker")
            self._require_token(lease, token_digest)
            immutable = self._envelope(envelope)
            stale_reason = self._completion_stale_reason(
                immutable,
                lease,
                submission,
                now=now,
            )
            prior = session.scalar(
                select(WorkerCompletionRow)
                .where(WorkerCompletionRow.envelope_id == envelope.id)
                .order_by(WorkerCompletionRow.received_at, WorkerCompletionRow.id)
            )
            state = WorkerCompletionState.DELIVERED
            if prior is not None:
                state = WorkerCompletionState.DUPLICATE
            elif stale_reason is not None:
                state = WorkerCompletionState.STALE
            row = WorkerCompletionRow(
                id=str(submission.id),
                envelope_id=envelope.id,
                lease_id=lease.id,
                worker_id=str(worker_id),
                fingerprint=submission.fingerprint,
                state=state.value,
                payload=payload,
                received_at=now.isoformat(),
                accepted_at=None,
                decided_at=now.isoformat()
                if state is not WorkerCompletionState.DELIVERED
                else None,
                decision_reason=stale_reason,
            )
            session.add(row)
            if state is WorkerCompletionState.DELIVERED:
                task = session.get(TaskRow, envelope.task_row_id)
                if task is None or task.status not in {
                    TaskState.LEASED.value,
                    TaskState.EXECUTING.value,
                }:
                    raise MishkanError(ErrorCode.WORKER, "remote task state is inconsistent")
                lease.state = WorkerLeaseState.COMPLETED.value
                lease.completed_at = now.isoformat()
                envelope.state = RemoteEnvelopeState.VALIDATING.value
                envelope.updated_at = now.isoformat()
                task.status = TaskState.VALIDATING.value
                task.revision += 1
                task.updated_at = now.isoformat()
            self._event(
                session,
                aggregate_id=envelope.run_id,
                entity_type="run",
                event_type=f"worker.completion_{state.value}",
                payload={
                    "completion_id": str(submission.id),
                    "envelope_id": envelope.id,
                    "lease_id": lease.id,
                    "worker_id": str(worker_id),
                    "reason": stale_reason,
                },
                run_id=envelope.run_id,
                task_id=envelope.task_key,
                security_relevant=state is not WorkerCompletionState.DELIVERED,
            )
            session.flush()
            return self._completion(row)

    def accept_completion(
        self,
        completion_id: UUID,
        result: InitializationResult,
        review: ReviewDecision,
    ) -> WorkerCompletion:
        """Atomically accept one delivered result and release its dependencies."""

        now = utc_now()
        with Session(self._engine) as session, session.begin():
            completion = session.scalar(
                select(WorkerCompletionRow)
                .where(WorkerCompletionRow.id == str(completion_id))
                .with_for_update()
            )
            if completion is None:
                raise MishkanError(ErrorCode.WORKER, "worker completion does not exist")
            envelope = session.scalar(
                select(RemoteTaskEnvelopeRow)
                .where(RemoteTaskEnvelopeRow.id == completion.envelope_id)
                .with_for_update()
            )
            if envelope is None:
                raise MishkanError(ErrorCode.WORKER, "worker completion envelope is missing")
            task = session.scalar(
                select(TaskRow).where(TaskRow.id == envelope.task_row_id).with_for_update()
            )
            run = session.scalar(
                select(RunRow).where(RunRow.id == envelope.run_id).with_for_update()
            )
            if task is None or run is None:
                raise MishkanError(ErrorCode.WORKER, "worker completion task authority is missing")
            existing_acceptance = session.get(WorkerTaskAcceptanceRow, task.id)
            if existing_acceptance is not None:
                if existing_acceptance.completion_id != completion.id:
                    completion.state = WorkerCompletionState.DUPLICATE.value
                    completion.decided_at = now.isoformat()
                    completion.decision_reason = "another valid completion was already accepted"
                return self._completion(completion)
            if completion.state != WorkerCompletionState.DELIVERED.value:
                raise MishkanError(
                    ErrorCode.WORKER, "worker completion is not eligible for acceptance"
                )
            submission = WorkerCompletionSubmission.model_validate_json(completion.payload)
            if submission.result != result.model_dump(mode="json"):
                raise MishkanError(
                    ErrorCode.OUTPUT_CONTRACT,
                    "validated result differs from the delivered worker result",
                )
            if result.task_id != task.task_key or review.task_id != task.task_key:
                raise MishkanError(ErrorCode.OUTPUT_CONTRACT, "completion identifies another task")
            if review.verdict != "accepted":
                raise MishkanError(ErrorCode.OUTPUT_CONTRACT, "rejected review cannot be accepted")
            if run.cancellation_requested:
                raise MishkanError(ErrorCode.RUN_INTERRUPTED, "run cancellation blocks acceptance")
            if result.repository_revision != run.repository_revision:
                raise MishkanError(
                    ErrorCode.REVISION_MISMATCH,
                    "completion repository revision differs from the accepted run",
                )
            contract = PlanTask.model_validate_json(task.contract)
            if (
                review.schema_version == "1.1"
                and review.producer_identity != contract.assigned_role
            ):
                raise MishkanError(
                    ErrorCode.ROLE_CONFLICT,
                    "review producer identity differs from the accepted task owner",
                )
            if not self._dependencies_accepted(session, run.id, contract):
                raise MishkanError(
                    ErrorCode.RUN_INTERRUPTED,
                    "task dependencies are not durably accepted",
                )
            accepted_result = session.scalar(
                select(ResultRow).where(
                    ResultRow.run_id == run.id,
                    ResultRow.task_key == task.task_key,
                )
            )
            if accepted_result is not None:
                raise MishkanError(
                    ErrorCode.DUPLICATE_RESULT,
                    "task already has an accepted result outside this delivery",
                )
            result_id = str(new_id())
            session.add(
                ResultRow(
                    id=result_id,
                    run_id=run.id,
                    task_key=task.task_key,
                    payload=result.model_dump_json(),
                    accepted_at=now.isoformat(),
                )
            )
            session.flush()
            session.add(
                AcceptanceRow(
                    id=str(new_id()),
                    run_id=run.id,
                    task_key=task.task_key,
                    result_id=result_id,
                    review_payload=review.model_dump_json(),
                    accepted_at=now.isoformat(),
                )
            )
            session.add(
                WorkerTaskAcceptanceRow(
                    task_row_id=task.id,
                    completion_id=completion.id,
                    envelope_id=envelope.id,
                    accepted_at=now.isoformat(),
                )
            )
            completion.state = WorkerCompletionState.ACCEPTED.value
            completion.accepted_at = now.isoformat()
            completion.decided_at = now.isoformat()
            envelope.state = RemoteEnvelopeState.COMPLETED.value
            envelope.updated_at = now.isoformat()
            placement = session.scalar(
                select(RemoteTaskPlacementRow)
                .where(RemoteTaskPlacementRow.current_envelope_id == envelope.id)
                .with_for_update()
            )
            if placement is not None:
                placement.state = RemotePlacementState.COMPLETED.value
                placement.revision += 1
                placement.updated_at = now.isoformat()
            task.status = TaskState.ACCEPTED.value
            task.revision += 1
            task.updated_at = now.isoformat()
            self._event(
                session,
                aggregate_id=run.id,
                entity_type="run",
                event_type="worker.completion_accepted",
                payload={
                    "completion_id": completion.id,
                    "envelope_id": envelope.id,
                    "task_id": task.task_key,
                },
                run_id=run.id,
                task_id=task.task_key,
            )
            self._release_dependents(session, run.id)
            pending = session.scalar(
                select(TaskRow.id).where(
                    TaskRow.run_id == run.id,
                    TaskRow.status != TaskState.ACCEPTED.value,
                )
            )
            if pending is None:
                run.status = RunState.COMPLETED.value
                run.revision += 1
                run.updated_at = now.isoformat()
                self._event(
                    session,
                    aggregate_id=run.id,
                    entity_type="run",
                    event_type="run.completed",
                    payload={},
                    run_id=run.id,
                )
            session.flush()
            return self._completion(completion)

    def expire_leases(self, *, now: datetime | None = None) -> tuple[UUID, ...]:
        observed_now = require_aware(now) if now is not None else utc_now()
        expired: list[UUID] = []
        with Session(self._engine) as session, session.begin():
            leases = session.scalars(
                select(WorkerLeaseRow)
                .where(
                    WorkerLeaseRow.state == WorkerLeaseState.ACTIVE.value,
                    WorkerLeaseRow.expires_at <= observed_now.isoformat(),
                )
                .with_for_update()
            ).all()
            for lease in leases:
                envelope = session.get(RemoteTaskEnvelopeRow, lease.envelope_id)
                if envelope is None:
                    continue
                task = session.get(TaskRow, envelope.task_row_id)
                lease.state = WorkerLeaseState.EXPIRED.value
                lease.revision += 1
                if envelope.stateful_effects:
                    envelope.state = RemoteEnvelopeState.RECONCILIATION_REQUIRED.value
                else:
                    envelope.state = RemoteEnvelopeState.EXPIRED.value
                    if task is not None and task.status in {
                        TaskState.LEASED.value,
                        TaskState.EXECUTING.value,
                    }:
                        task.status = TaskState.ELIGIBLE.value
                        task.revision += 1
                        task.updated_at = observed_now.isoformat()
                    placement = session.scalar(
                        select(RemoteTaskPlacementRow)
                        .where(RemoteTaskPlacementRow.current_envelope_id == envelope.id)
                        .with_for_update()
                    )
                    if placement is not None:
                        placement.state = RemotePlacementState.QUEUED.value
                        placement.selected_worker_id = None
                        placement.current_envelope_id = None
                        placement.revision += 1
                        placement.updated_at = observed_now.isoformat()
                envelope.updated_at = observed_now.isoformat()
                self._event(
                    session,
                    aggregate_id=envelope.run_id,
                    entity_type="run",
                    event_type="worker.lease_expired",
                    payload={
                        "lease_id": lease.id,
                        "envelope_id": envelope.id,
                        "reconciliation_required": envelope.stateful_effects,
                    },
                    run_id=envelope.run_id,
                    task_id=envelope.task_key,
                    security_relevant=envelope.stateful_effects,
                )
                expired.append(UUID(lease.id))
        return tuple(expired)

    def reconcile_expired_envelope(
        self,
        envelope_id: UUID,
        *,
        effect_report: EffectReportState,
    ) -> None:
        now = utc_now()
        with Session(self._engine) as session, session.begin():
            envelope = session.scalar(
                select(RemoteTaskEnvelopeRow)
                .where(RemoteTaskEnvelopeRow.id == str(envelope_id))
                .with_for_update()
            )
            if (
                envelope is None
                or envelope.state != RemoteEnvelopeState.RECONCILIATION_REQUIRED.value
            ):
                raise MishkanError(ErrorCode.WORKER, "remote task is not awaiting reconciliation")
            if effect_report is not EffectReportState.PROVEN:
                raise MishkanError(
                    ErrorCode.WORKER,
                    "stateful task remains blocked until effects are proven settled",
                )
            task = session.get(TaskRow, envelope.task_row_id)
            if task is None or task.status not in {
                TaskState.LEASED.value,
                TaskState.EXECUTING.value,
            }:
                raise MishkanError(ErrorCode.WORKER, "remote task state is inconsistent")
            envelope.state = RemoteEnvelopeState.EXPIRED.value
            envelope.updated_at = now.isoformat()
            placement = session.scalar(
                select(RemoteTaskPlacementRow)
                .where(RemoteTaskPlacementRow.current_envelope_id == envelope.id)
                .with_for_update()
            )
            if placement is not None:
                placement.state = RemotePlacementState.QUEUED.value
                placement.selected_worker_id = None
                placement.current_envelope_id = None
                placement.revision += 1
                placement.updated_at = now.isoformat()
            task.status = TaskState.ELIGIBLE.value
            task.revision += 1
            task.updated_at = now.isoformat()
            self._event(
                session,
                aggregate_id=envelope.run_id,
                entity_type="run",
                event_type="worker.effects_reconciled",
                payload={"envelope_id": envelope.id, "effect_report": effect_report.value},
                run_id=envelope.run_id,
                task_id=envelope.task_key,
            )

    def envelope(self, envelope_id: UUID) -> ImmutableTaskEnvelope:
        with Session(self._engine) as session:
            row = session.get(RemoteTaskEnvelopeRow, str(envelope_id))
            if row is None:
                raise MishkanError(ErrorCode.WORKER, "remote task envelope does not exist")
            return self._envelope(row)

    def placement(self, placement_id: UUID) -> RemotePlacement:
        with Session(self._engine) as session:
            row = session.get(RemoteTaskPlacementRow, str(placement_id))
            if row is None:
                raise MishkanError(ErrorCode.WORKER, "remote task placement does not exist")
            return self._placement(row)

    def completion(self, completion_id: UUID) -> WorkerCompletion:
        with Session(self._engine) as session:
            row = session.get(WorkerCompletionRow, str(completion_id))
            if row is None:
                raise MishkanError(ErrorCode.WORKER, "worker completion does not exist")
            return self._completion(row)

    def authorize_input_artifact(self, worker_id: UUID, artifact_id: UUID) -> None:
        """Prove that an active lease explicitly references one immutable input artifact."""

        with Session(self._engine) as session:
            self._active_worker(session, worker_id, lock=False)
            envelope_rows = session.scalars(
                select(RemoteTaskEnvelopeRow)
                .join(WorkerLeaseRow, WorkerLeaseRow.envelope_id == RemoteTaskEnvelopeRow.id)
                .where(
                    WorkerLeaseRow.worker_id == str(worker_id),
                    WorkerLeaseRow.state == WorkerLeaseState.ACTIVE.value,
                    WorkerLeaseRow.expires_at > utc_now().isoformat(),
                )
            ).all()
            for row in envelope_rows:
                envelope = self._envelope(row)
                if artifact_id in {item.artifact_id for item in envelope.inputs}:
                    return
        raise MishkanError(
            ErrorCode.AUTHORITY_NOT_GRANTED,
            "worker has no active lease for the requested input artifact",
        )

    @staticmethod
    def _active_worker(session: Session, worker_id: UUID, *, lock: bool) -> WorkerIdentityRow:
        statement = select(WorkerIdentityRow).where(WorkerIdentityRow.id == str(worker_id))
        if lock:
            statement = statement.with_for_update()
        worker = session.scalar(statement)
        if worker is None or worker.state != WorkerState.ACTIVE.value:
            raise MishkanError(ErrorCode.WORKER, "worker identity is not active")
        return worker

    @staticmethod
    def _require_resource_capacity(
        advertisement: WorkerHeartbeat,
        limits: TaskResourceLimits,
    ) -> None:
        if limits.memory_bytes is not None and (
            advertisement.resources.available_memory_bytes < limits.memory_bytes
        ):
            raise MishkanError(ErrorCode.WORKER, "worker memory does not satisfy task bounds")
        if limits.cpu_count is not None and (
            advertisement.resources.logical_cpu_count < limits.cpu_count
        ):
            raise MishkanError(ErrorCode.WORKER, "worker CPU does not satisfy task bounds")

    @staticmethod
    def _validate_envelope_against_authority(
        envelope: ImmutableTaskEnvelope,
        run: RunRow | None,
        task: TaskRow | None,
        plan: PlanRow | None,
    ) -> None:
        if run is None or task is None or plan is None:
            raise MishkanError(ErrorCode.WORKER, "remote task authority is incomplete")
        if run.status != RunState.RUNNING.value or run.cancellation_requested:
            raise MishkanError(ErrorCode.RUN_INTERRUPTED, "run does not accept remote work")
        if task.run_id != run.id or task.task_key != envelope.task_id:
            raise MishkanError(ErrorCode.WORKER, "remote task identity differs from accepted task")
        if run.context_revision != envelope.execution_context_revision:
            raise MishkanError(
                ErrorCode.REVISION_MISMATCH,
                "execution-context revision differs from the immutable envelope",
            )
        if (
            run.repository_id != envelope.repository_id
            or run.repository_revision != envelope.repository_revision
        ):
            raise MishkanError(
                ErrorCode.REVISION_MISMATCH,
                "repository identity or revision differs from the immutable envelope",
            )
        if plan.fingerprint != envelope.plan_fingerprint:
            raise MishkanError(ErrorCode.REVISION_MISMATCH, "plan fingerprint changed")
        accepted_contract = PlanTask.model_validate_json(task.contract)
        if accepted_contract.model_dump(mode="json") != envelope.task_contract:
            raise MishkanError(ErrorCode.REVISION_MISMATCH, "task contract changed")

    def _active_lease(
        self,
        session: Session,
        worker_id: UUID,
        lease_id: UUID,
        *,
        token_digest: str,
        now: datetime,
    ) -> tuple[WorkerLeaseRow, RemoteTaskEnvelopeRow]:
        self._active_worker(session, worker_id, lock=True)
        lease = session.scalar(
            select(WorkerLeaseRow).where(WorkerLeaseRow.id == str(lease_id)).with_for_update()
        )
        if lease is None or lease.worker_id != str(worker_id):
            raise MishkanError(ErrorCode.WORKER, "worker lease is invalid")
        self._require_token(lease, token_digest)
        if lease.state != WorkerLeaseState.ACTIVE.value:
            raise MishkanError(ErrorCode.WORKER, "worker lease is not active")
        if datetime.fromisoformat(lease.expires_at) <= now:
            raise MishkanError(ErrorCode.WORKER, "worker lease has expired")
        envelope = session.scalar(
            select(RemoteTaskEnvelopeRow)
            .where(RemoteTaskEnvelopeRow.id == lease.envelope_id)
            .with_for_update()
        )
        if envelope is None or envelope.worker_id != str(worker_id):
            raise MishkanError(ErrorCode.WORKER, "worker envelope is invalid")
        return lease, envelope

    @classmethod
    def _require_token(cls, lease: WorkerLeaseRow, token_digest: str) -> None:
        cls._require_digest(token_digest)
        if not hmac.compare_digest(lease.token_digest, token_digest):
            raise MishkanError(ErrorCode.WORKER, "worker lease credential is invalid")

    @staticmethod
    def _require_digest(value: str) -> None:
        if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
            raise MishkanError(ErrorCode.WORKER, "worker lease credential digest is invalid")

    @staticmethod
    def _completion_stale_reason(
        envelope: ImmutableTaskEnvelope,
        lease: WorkerLeaseRow,
        submission: WorkerCompletionSubmission,
        *,
        now: datetime,
    ) -> str | None:
        if lease.state != WorkerLeaseState.ACTIVE.value:
            return "lease is not active"
        if datetime.fromisoformat(lease.expires_at) <= now:
            return "lease expired before completion delivery"
        if submission.execution_context_revision != envelope.execution_context_revision:
            return "execution-context revision differs from the immutable envelope"
        if submission.repository_revision != envelope.repository_revision:
            return "repository revision differs from the immutable envelope"
        if (
            envelope.stateful_effects
            and submission.effect_report is EffectReportState.NOT_APPLICABLE
        ):
            return "stateful task omitted effect-settlement evidence"
        return None

    @staticmethod
    def _heartbeat_record(
        row: WorkerHeartbeatRow,
        worker_state: str,
        now: datetime,
    ) -> WorkerHeartbeatRecord:
        heartbeat = WorkerHeartbeat.model_validate_json(row.payload)
        if worker_state == WorkerState.REVOKED.value:
            availability = WorkerAvailability.REVOKED
        elif datetime.fromisoformat(row.expires_at) <= now:
            availability = WorkerAvailability.STALE
        else:
            availability = WorkerAvailability.ONLINE
        return WorkerHeartbeatRecord(
            id=heartbeat.id,
            created_at=heartbeat.created_at,
            worker_id=heartbeat.worker_id,
            sequence=row.sequence,
            advertisement_fingerprint=row.advertisement_fingerprint,
            capabilities=heartbeat.capabilities,
            resources=heartbeat.resources,
            active_lease_ids=heartbeat.active_lease_ids,
            observed_at=datetime.fromisoformat(row.observed_at),
            expires_at=datetime.fromisoformat(row.expires_at),
            availability=availability,
        )

    @staticmethod
    def _envelope(row: RemoteTaskEnvelopeRow) -> ImmutableTaskEnvelope:
        envelope = ImmutableTaskEnvelope.model_validate_json(row.payload)
        if envelope.fingerprint != row.fingerprint:
            raise MishkanError(ErrorCode.WORKER, "immutable task envelope fingerprint drifted")
        return envelope

    @staticmethod
    def _lease(row: WorkerLeaseRow) -> WorkerLease:
        issued_at = datetime.fromisoformat(row.issued_at)
        return WorkerLease(
            id=UUID(row.id),
            created_at=issued_at,
            envelope_id=UUID(row.envelope_id),
            worker_id=UUID(row.worker_id),
            state=WorkerLeaseState(row.state),
            revision=row.revision,
            issued_at=issued_at,
            expires_at=datetime.fromisoformat(row.expires_at),
            last_renewed_at=datetime.fromisoformat(row.last_renewed_at),
            completed_at=(datetime.fromisoformat(row.completed_at) if row.completed_at else None),
        )

    @staticmethod
    def _placement(row: RemoteTaskPlacementRow) -> RemotePlacement:
        request = RemotePlacementRequest.model_validate_json(row.request_payload)
        if request.fingerprint != row.request_fingerprint:
            raise MishkanError(ErrorCode.WORKER, "remote placement fingerprint drifted")
        return RemotePlacement(
            id=UUID(row.id),
            created_at=datetime.fromisoformat(row.created_at),
            run_id=UUID(row.run_id),
            task_row_id=UUID(row.task_row_id),
            task_id=row.task_key,
            state=RemotePlacementState(row.state),
            request_fingerprint=row.request_fingerprint,
            selected_worker_id=(
                UUID(row.selected_worker_id) if row.selected_worker_id is not None else None
            ),
            envelope_id=(
                UUID(row.current_envelope_id) if row.current_envelope_id is not None else None
            ),
            revision=row.revision,
            updated_at=datetime.fromisoformat(row.updated_at),
        )

    @staticmethod
    def _completion(row: WorkerCompletionRow) -> WorkerCompletion:
        submission = WorkerCompletionSubmission.model_validate_json(row.payload)
        return WorkerCompletion(
            id=UUID(row.id),
            created_at=submission.created_at,
            envelope_id=UUID(row.envelope_id),
            lease_id=UUID(row.lease_id),
            worker_id=UUID(row.worker_id),
            fingerprint=row.fingerprint,
            state=WorkerCompletionState(row.state),
            result=submission.result,
            artifact_ids=submission.artifact_ids,
            unified_patch_artifact_id=submission.unified_patch_artifact_id,
            effect_evidence_artifact_ids=submission.effect_evidence_artifact_ids,
            effect_report=submission.effect_report,
            received_at=datetime.fromisoformat(row.received_at),
            accepted_at=(datetime.fromisoformat(row.accepted_at) if row.accepted_at else None),
        )

    @staticmethod
    def _dependencies_accepted(session: Session, run_id: str, contract: PlanTask) -> bool:
        if not contract.depends_on:
            return True
        accepted = set(
            session.scalars(
                select(TaskRow.task_key).where(
                    TaskRow.run_id == run_id,
                    TaskRow.task_key.in_(contract.depends_on),
                    TaskRow.status == TaskState.ACCEPTED.value,
                )
            ).all()
        )
        return accepted == set(contract.depends_on)

    @staticmethod
    def _release_dependents(session: Session, run_id: str) -> None:
        pending = session.scalars(
            select(TaskRow).where(
                TaskRow.run_id == run_id,
                TaskRow.status == TaskState.PENDING.value,
            )
        ).all()
        for task in pending:
            contract = PlanTask.model_validate_json(task.contract)
            if WorkerCoordinationRepository._dependencies_accepted(session, run_id, contract):
                task.status = TaskState.ELIGIBLE.value
                task.revision += 1
                task.updated_at = utc_now().isoformat()

    @staticmethod
    def _event(
        session: Session,
        *,
        aggregate_id: str,
        entity_type: str,
        event_type: str,
        payload: dict[str, object],
        run_id: str | None = None,
        task_id: str | None = None,
        security_relevant: bool = True,
    ) -> None:
        session.add(
            OutboxRow(
                id=str(new_id()),
                schema_version="1.0",
                aggregate_id=aggregate_id,
                entity_type=entity_type,
                run_id=run_id,
                task_id=task_id,
                identity_id=None,
                team_id=None,
                security_relevant=security_relevant,
                event_type=event_type,
                source="mishkan.worker_coordination",
                payload=json.dumps(payload, sort_keys=True, separators=(",", ":")),
                occurred_at=utc_now().isoformat(),
                command_id=None,
                correlation_id=None,
                causation_id=None,
                sensitivity="security" if security_relevant else "internal",
                published_at=None,
            )
        )


def lease_token_digest(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()
