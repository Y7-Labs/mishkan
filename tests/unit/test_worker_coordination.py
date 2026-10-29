from __future__ import annotations

import hashlib
from datetime import timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from pydantic import SecretStr
from sqlalchemy.orm import Session

from mishkan.artifacts import ArtifactProvenance
from mishkan.artifacts.service import DurableArtifactService
from mishkan.domain.errors import ErrorCode, MishkanError
from mishkan.domain.time import utc_now
from mishkan.persistence import LocalRunRepository, SchemaManager
from mishkan.persistence.sqlite import (
    RemoteTaskEnvelopeRow,
    TaskRow,
    WorkerIdentityRow,
    WorkerLeaseRow,
    create_local_engine,
)
from mishkan.planning.models import (
    AcceptedPlan,
    InitializationResult,
    PlanTask,
    ReviewDecision,
)
from mishkan.repository.models import DiscoverySnapshot, RepositoryBinding
from mishkan.runtime import TaskState
from mishkan.workers import (
    CapabilityObservation,
    CapabilityRequirement,
    EffectReportState,
    ImmutableTaskEnvelope,
    RemotePlacementRequest,
    RemotePlacementState,
    TaskInputReference,
    TaskPathScope,
    TaskResourceLimits,
    WorkerArtifactUploadRequest,
    WorkerAvailability,
    WorkerClaimRequest,
    WorkerCompletionState,
    WorkerCompletionSubmission,
    WorkerCoordinationRepository,
    WorkerCoordinationService,
    WorkerHeartbeat,
    WorkerResourceSnapshot,
)
from mishkan.workers.coordination import lease_token_digest


def _plan() -> AcceptedPlan:
    return AcceptedPlan(
        objective="Execute a distributed task",
        outcome_id="mission",
        repository_revision="b" * 40,
        tasks=(
            PlanTask(
                task_id="remote-task",
                title="Produce remote evidence",
                purpose="Produce one structured result on an authorized worker.",
                assigned_role="Engineer",
                tools=("file.read",),
                evidence_paths=("README.md",),
            ),
        ),
        fingerprint="d" * 64,
        discovery_fingerprint="c" * 64,
    )


def _run(database: Path, root: Path) -> tuple[LocalRunRepository, UUID, UUID]:
    repository = LocalRunRepository(database)
    snapshot = repository.start_or_resume(
        DiscoverySnapshot(
            binding=RepositoryBinding(
                repository_id="a" * 64,
                root=root,
                base_revision="b" * 40,
                working_tree_dirty=False,
                working_tree_fingerprint="0" * 64,
            ),
            facts=(),
            unknowns=(),
            fingerprint="c" * 64,
        ),
        "Execute distributed work",
        "mission",
    )
    repository.mark_awaiting_approval(snapshot.run_id)
    repository.accept_plan(snapshot.run_id, _plan())
    repository.start_run(snapshot.run_id)
    with Session(create_local_engine(database)) as session:
        task = session.query(TaskRow).filter_by(run_id=snapshot.run_id).one()
        return repository, UUID(snapshot.run_id), UUID(task.id)


def _worker(database: Path) -> UUID:
    worker_id = uuid4()
    now = utc_now().isoformat()
    with Session(create_local_engine(database)) as session, session.begin():
        session.add(
            WorkerIdentityRow(
                id=str(worker_id),
                display_name="test-worker",
                state="active",
                current_certificate_serial="1",
                revision=1,
                created_at=now,
                updated_at=now,
                revoked_at=None,
            )
        )
    return worker_id


def _heartbeat(worker_id: UUID, *, sequence: int = 1) -> WorkerHeartbeat:
    return WorkerHeartbeat(
        worker_id=worker_id,
        sequence=sequence,
        capabilities=(
            CapabilityObservation(
                capability_id="file.read",
                version="1.0.0",
                fingerprint="e" * 64,
            ),
        ),
        resources=WorkerResourceSnapshot(
            platform="linux",
            architecture="x86_64",
            logical_cpu_count=8,
            available_memory_bytes=8 * 1024 * 1024 * 1024,
            max_concurrency=2,
            active_tasks=0,
        ),
        observed_at=utc_now(),
    )


def _envelope(
    run_id: UUID,
    task_row_id: UUID,
    worker_id: UUID,
    *,
    task_attempt: int = 1,
    stateful: bool = False,
) -> ImmutableTaskEnvelope:
    task = _plan().tasks[0]
    return ImmutableTaskEnvelope(
        run_id=run_id,
        task_row_id=task_row_id,
        task_id=task.task_id,
        task_attempt=task_attempt,
        worker_id=worker_id,
        execution_context_revision="b" * 40,
        repository_id="a" * 64,
        repository_revision="b" * 40,
        plan_fingerprint="d" * 64,
        task_contract=task.model_dump(mode="json"),
        role_definition_fingerprint="f" * 64,
        policy_fingerprint="1" * 64,
        path_scopes=(
            TaskPathScope(
                root_id="repository",
                relative_path="README.md",
                permissions=("read",),
            ),
        ),
        capability_requirements=(
            CapabilityRequirement(
                capability_id="file.read",
                acceptable_versions=("1.0.0",),
                required_fingerprint="e" * 64,
            ),
        ),
        resources=TaskResourceLimits(
            timeout_seconds=300,
            memory_bytes=512 * 1024 * 1024,
            cpu_count=2,
            max_result_bytes=1024 * 1024,
        ),
        deadline=utc_now() + timedelta(minutes=10),
        stateful_effects=stateful,
    )


def _placement_request(
    run_id: UUID,
    *,
    preferred_worker_id: UUID | None = None,
    stateful: bool = False,
) -> RemotePlacementRequest:
    return RemotePlacementRequest(
        run_id=run_id,
        task_id="remote-task",
        preferred_worker_id=preferred_worker_id,
        role_definition_fingerprint="f" * 64,
        policy_fingerprint="1" * 64,
        path_scopes=(
            TaskPathScope(
                root_id="repository",
                relative_path="README.md",
                permissions=("read",),
            ),
        ),
        capability_requirements=(
            CapabilityRequirement(
                capability_id="file.read",
                acceptable_versions=("1.0.0",),
                required_fingerprint="e" * 64,
            ),
        ),
        resources=TaskResourceLimits(
            timeout_seconds=300,
            memory_bytes=512 * 1024 * 1024,
            cpu_count=2,
            max_result_bytes=1024 * 1024,
        ),
        deadline=utc_now() + timedelta(minutes=10),
        stateful_effects=stateful,
    )


def _repository(database: Path) -> WorkerCoordinationRepository:
    return WorkerCoordinationRepository(
        database,
        heartbeat_timeout_seconds=30,
        lease_seconds=60,
        max_worker_concurrency=8,
        max_completion_bytes=1024 * 1024,
    )


def _setup(
    tmp_path: Path,
) -> tuple[
    Path,
    LocalRunRepository,
    WorkerCoordinationRepository,
    WorkerCoordinationService,
    UUID,
    UUID,
    UUID,
]:
    database = tmp_path / "mishkan.db"
    SchemaManager(database).initialize()
    runs, run_id, task_row_id = _run(database, tmp_path)
    worker_id = _worker(database)
    coordination = _repository(database)
    service = WorkerCoordinationService(coordination)
    service.heartbeat(_heartbeat(worker_id))
    return database, runs, coordination, service, run_id, task_row_id, worker_id


def _result() -> InitializationResult:
    return InitializationResult(
        repository_revision="b" * 40,
        task_id="remote-task",
        summary="Accepted remote result.",
        cited_paths=("README.md",),
        findings=("Remote evidence was produced.",),
    )


def _review() -> ReviewDecision:
    return ReviewDecision(
        task_id="remote-task",
        verdict="accepted",
        summary="Independent validation accepted the remote evidence.",
        checked_citations=("README.md",),
    )


def test_heartbeat_is_monotone_and_capability_placement_is_contextual(tmp_path: Path) -> None:
    database = tmp_path / "mishkan.db"
    SchemaManager(database).initialize()
    worker_id = _worker(database)
    repository = _repository(database)
    heartbeat = _heartbeat(worker_id)

    first = repository.record_heartbeat(heartbeat)
    replay = repository.record_heartbeat(heartbeat)

    assert replay == first
    assert first.availability is WorkerAvailability.ONLINE
    compatible = repository.compatible_workers(
        (
            CapabilityRequirement(
                capability_id="file.read",
                acceptable_versions=("1.0.0",),
            ),
        ),
        TaskResourceLimits(
            timeout_seconds=60,
            memory_bytes=1024,
            cpu_count=1,
            max_result_bytes=1024,
        ),
    )
    assert [item.worker_id for item in compatible] == [worker_id]
    assert (
        repository.compatible_workers(
            (CapabilityRequirement(capability_id="browser.observe"),),
            TaskResourceLimits(timeout_seconds=60, max_result_bytes=1024),
        )
        == ()
    )
    with pytest.raises(MishkanError) as stale:
        repository.record_heartbeat(_heartbeat(worker_id, sequence=1))
    assert stale.value.envelope.code is ErrorCode.REVISION_MISMATCH


def test_pull_claim_is_atomic_idempotent_and_keeps_worker_secret_local(
    tmp_path: Path,
) -> None:
    database, runs, coordination, service, run_id, _task_row_id, worker_id = _setup(tmp_path)
    placement = service.queue(_placement_request(run_id))
    secret = "worker-retained-lease-secret"
    request = WorkerClaimRequest(lease_token_digest=lease_token_digest(secret))

    claimed = service.claim(worker_id, request)

    assert claimed is not None
    assert claimed.claim_id == request.id
    assert claimed.envelope.worker_id == worker_id
    assert claimed.envelope.task_attempt == 1
    assert service.claim(worker_id, request) == claimed
    assert runs.task_states(str(run_id)) == {"remote-task": TaskState.LEASED.value}
    projected = coordination.placement(placement.id)
    assert projected.state is RemotePlacementState.LEASED
    assert projected.selected_worker_id == worker_id
    assert projected.envelope_id == claimed.envelope.id
    with Session(create_local_engine(database)) as session:
        row = session.get(WorkerLeaseRow, str(claimed.lease.id))
        assert row is not None
        assert row.token_digest == lease_token_digest(secret)
        assert secret not in row.token_digest

    conflicting = request.model_copy(
        update={"lease_token_digest": lease_token_digest("different-secret")}
    )
    with pytest.raises(MishkanError) as reused:
        service.claim(worker_id, conflicting)
    assert reused.value.envelope.code is ErrorCode.WORKER


def test_expired_pull_claim_is_requeued_for_another_compatible_worker(
    tmp_path: Path,
) -> None:
    _database, _runs, coordination, service, run_id, _task_row_id, worker_id = _setup(tmp_path)
    second_worker = _worker(_database)
    service.heartbeat(_heartbeat(second_worker))
    placement = service.queue(_placement_request(run_id))
    first = service.claim(
        worker_id,
        WorkerClaimRequest(lease_token_digest=lease_token_digest("first-secret")),
    )
    assert first is not None

    coordination.expire_leases(now=first.lease.expires_at + timedelta(seconds=1))
    queued = coordination.placement(placement.id)
    assert queued.state is RemotePlacementState.QUEUED
    assert queued.selected_worker_id is None
    assert queued.envelope_id is None

    second = service.claim(
        second_worker,
        WorkerClaimRequest(lease_token_digest=lease_token_digest("second-secret")),
    )
    assert second is not None
    assert second.envelope.worker_id == second_worker
    assert second.envelope.task_attempt == 2


def test_stateful_pull_claim_requires_reconciliation_before_requeue(
    tmp_path: Path,
) -> None:
    database, _runs, coordination, service, run_id, _task_row_id, worker_id = _setup(tmp_path)
    second_worker = _worker(database)
    service.heartbeat(_heartbeat(second_worker))
    placement = service.queue(_placement_request(run_id, stateful=True))
    first = service.claim(
        worker_id,
        WorkerClaimRequest(lease_token_digest=lease_token_digest("stateful-secret")),
    )
    assert first is not None

    coordination.expire_leases(now=first.lease.expires_at + timedelta(seconds=1))
    assert coordination.placement(placement.id).state is RemotePlacementState.LEASED
    assert (
        service.claim(
            second_worker,
            WorkerClaimRequest(lease_token_digest=lease_token_digest("blocked-secret")),
        )
        is None
    )

    service.reconcile(first.envelope.id, effect_report=EffectReportState.PROVEN)
    claimed = service.claim(
        second_worker,
        WorkerClaimRequest(lease_token_digest=lease_token_digest("reconciled-secret")),
    )
    assert claimed is not None
    assert claimed.envelope.task_attempt == 2


def test_pull_claim_respects_preferred_worker_and_observed_capabilities(
    tmp_path: Path,
) -> None:
    database, _runs, _coordination, service, run_id, _task_row_id, worker_id = _setup(tmp_path)
    preferred = _worker(database)
    service.heartbeat(_heartbeat(preferred))
    service.queue(_placement_request(run_id, preferred_worker_id=preferred))

    assert (
        service.claim(
            worker_id,
            WorkerClaimRequest(lease_token_digest=lease_token_digest("wrong-worker")),
        )
        is None
    )
    claim = service.claim(
        preferred,
        WorkerClaimRequest(lease_token_digest=lease_token_digest("preferred-worker")),
    )
    assert claim is not None
    assert claim.envelope.worker_id == preferred


def test_lease_delivery_and_acceptance_are_atomic_and_at_most_once(tmp_path: Path) -> None:
    _database, runs, coordination, service, run_id, task_row_id, worker_id = _setup(tmp_path)
    grant = service.issue(_envelope(run_id, task_row_id, worker_id))
    service.begin(worker_id, grant.lease.id, grant.lease_token)
    result = _result()
    submission = WorkerCompletionSubmission(
        envelope_id=grant.envelope.id,
        lease_id=grant.lease.id,
        execution_context_revision="b" * 40,
        repository_revision="b" * 40,
        result=result.model_dump(mode="json"),
        effect_report=EffectReportState.NOT_APPLICABLE,
    )

    delivered = service.complete(worker_id, submission, grant.lease_token)
    accepted = coordination.accept_completion(delivered.id, result, _review())

    assert delivered.state is WorkerCompletionState.DELIVERED
    assert accepted.state is WorkerCompletionState.ACCEPTED
    assert runs.task_states(str(run_id)) == {"remote-task": TaskState.ACCEPTED.value}
    assert coordination.accept_completion(delivered.id, result, _review()).id == delivered.id

    duplicate_submission = submission.model_copy(update={"id": uuid4()})
    duplicate = service.complete(worker_id, duplicate_submission, grant.lease_token)
    assert duplicate.state is WorkerCompletionState.DUPLICATE
    assert runs.task_states(str(run_id)) == {"remote-task": TaskState.ACCEPTED.value}


def test_revision_mismatch_completion_is_preserved_but_never_accepted(tmp_path: Path) -> None:
    _database, runs, coordination, service, run_id, task_row_id, worker_id = _setup(tmp_path)
    grant = service.issue(_envelope(run_id, task_row_id, worker_id))
    submission = WorkerCompletionSubmission(
        envelope_id=grant.envelope.id,
        lease_id=grant.lease.id,
        execution_context_revision="different-revision",
        repository_revision="b" * 40,
        result=_result().model_dump(mode="json"),
        effect_report=EffectReportState.NOT_APPLICABLE,
    )

    stale = service.complete(worker_id, submission, grant.lease_token)

    assert stale.state is WorkerCompletionState.STALE
    assert runs.task_states(str(run_id)) == {"remote-task": TaskState.LEASED.value}
    with pytest.raises(MishkanError):
        coordination.accept_completion(stale.id, _result(), _review())


def test_expired_stateful_lease_blocks_retry_until_effect_reconciliation(
    tmp_path: Path,
) -> None:
    _database, runs, coordination, service, run_id, task_row_id, worker_id = _setup(tmp_path)
    grant = service.issue(_envelope(run_id, task_row_id, worker_id, stateful=True))

    expired = coordination.expire_leases(now=grant.lease.expires_at + timedelta(seconds=1))

    assert expired == (grant.lease.id,)
    assert runs.task_states(str(run_id)) == {"remote-task": TaskState.LEASED.value}
    with pytest.raises(MishkanError):
        service.reconcile(grant.envelope.id, effect_report=EffectReportState.UNCERTAIN)
    service.reconcile(grant.envelope.id, effect_report=EffectReportState.PROVEN)
    assert runs.task_states(str(run_id)) == {"remote-task": TaskState.ELIGIBLE.value}


def test_lease_secret_is_not_serialized_by_public_grant(tmp_path: Path) -> None:
    _database, _runs, _coordination, service, run_id, task_row_id, worker_id = _setup(tmp_path)
    grant = service.issue(_envelope(run_id, task_row_id, worker_id))
    secret = grant.lease_token.get_secret_value()

    assert secret not in repr(grant)
    assert secret not in grant.model_dump_json()
    with pytest.raises(MishkanError):
        service.begin(worker_id, grant.lease.id, SecretStr("wrong-secret" * 5))


def test_worker_reads_only_artifacts_referenced_by_an_active_lease(tmp_path: Path) -> None:
    _database, _runs, coordination, service, run_id, task_row_id, worker_id = _setup(tmp_path)
    artifact_id = uuid4()
    envelope = _envelope(run_id, task_row_id, worker_id).model_copy(
        update={
            "inputs": (
                TaskInputReference(
                    name="context",
                    artifact_id=artifact_id,
                    digest=f"sha256:{'a' * 64}",
                ),
            )
        }
    )
    grant = service.issue(envelope)

    service.authorize_input_artifact(worker_id, artifact_id)
    with pytest.raises(MishkanError) as unrelated:
        service.authorize_input_artifact(worker_id, uuid4())
    assert unrelated.value.envelope.code is ErrorCode.AUTHORITY_NOT_GRANTED

    coordination.expire_leases(now=grant.lease.expires_at + timedelta(seconds=1))
    with pytest.raises(MishkanError):
        service.authorize_input_artifact(worker_id, artifact_id)


def test_completion_accepts_only_artifacts_uploaded_under_the_exact_lease(
    tmp_path: Path,
) -> None:
    database, _runs, coordination, service, run_id, task_row_id, worker_id = _setup(tmp_path)
    grant = service.issue(_envelope(run_id, task_row_id, worker_id))
    content = b"worker evidence"
    digest = f"sha256:{hashlib.sha256(content).hexdigest()}"
    provenance = ArtifactProvenance(
        producer_identity=f"worker:{worker_id}",
        run_id=str(run_id),
        task_attempt_id=str(grant.envelope.id),
        call_id="remote-task-output",
        capability="crewai.task.execute",
        channel="worker-output",
    )
    request = WorkerArtifactUploadRequest(
        expected_size=len(content),
        expected_digest=digest,
        media_type="text/plain",
        provenance=provenance,
    )
    artifacts = DurableArtifactService(
        database,
        tmp_path / "artifacts",
        max_artifact_bytes=1024,
        max_chunk_bytes=1024,
    )
    upload = artifacts.open_upload(
        expected_size=request.expected_size,
        expected_digest=request.expected_digest,
        media_type=request.media_type,
        provenance=request.provenance,
    )
    service.register_artifact_upload(
        worker_id,
        grant.lease.id,
        upload.upload_id,
        request,
        grant.lease_token,
    )
    artifacts.append_chunk(upload.upload_id, offset=0, content=content)
    manifest = artifacts.commit_upload(upload.upload_id)
    service.complete_artifact_upload(
        worker_id,
        upload.upload_id,
        manifest.id,
        grant.lease_token,
    )
    result = _result()
    unrelated_content = b"unrelated"
    unrelated_digest = f"sha256:{hashlib.sha256(unrelated_content).hexdigest()}"
    unrelated_upload = artifacts.open_upload(
        expected_size=len(unrelated_content),
        expected_digest=unrelated_digest,
        media_type="text/plain",
        provenance=provenance.model_copy(update={"call_id": "outside-lease-binding"}),
    )
    artifacts.append_chunk(unrelated_upload.upload_id, offset=0, content=unrelated_content)
    unrelated = artifacts.commit_upload(unrelated_upload.upload_id)
    with pytest.raises(MishkanError, match="outside its active lease"):
        service.complete(
            worker_id,
            WorkerCompletionSubmission(
                envelope_id=grant.envelope.id,
                lease_id=grant.lease.id,
                execution_context_revision="b" * 40,
                repository_revision="b" * 40,
                result=result.model_dump(mode="json"),
                artifact_ids=(unrelated.id,),
                effect_report=EffectReportState.NOT_APPLICABLE,
            ),
            grant.lease_token,
        )
    submission = WorkerCompletionSubmission(
        envelope_id=grant.envelope.id,
        lease_id=grant.lease.id,
        execution_context_revision="b" * 40,
        repository_revision="b" * 40,
        result=result.model_dump(mode="json"),
        artifact_ids=(manifest.id,),
        effect_report=EffectReportState.NOT_APPLICABLE,
    )

    delivered = service.complete(worker_id, submission, grant.lease_token)

    assert delivered.artifact_ids == (manifest.id,)
    assert coordination.completion(delivered.id).artifact_ids == (manifest.id,)


def test_artifact_upload_reservations_share_the_envelope_result_bound(tmp_path: Path) -> None:
    database, _runs, _coordination, service, run_id, task_row_id, worker_id = _setup(tmp_path)
    envelope = _envelope(run_id, task_row_id, worker_id).model_copy(
        update={
            "resources": TaskResourceLimits(
                timeout_seconds=300,
                memory_bytes=512 * 1024 * 1024,
                cpu_count=2,
                max_result_bytes=10,
            )
        }
    )
    grant = service.issue(envelope)
    artifacts = DurableArtifactService(
        database,
        tmp_path / "artifacts",
        max_artifact_bytes=1024,
        max_chunk_bytes=1024,
    )
    provenance = ArtifactProvenance(
        producer_identity=f"worker:{worker_id}",
        run_id=str(run_id),
        task_attempt_id=str(grant.envelope.id),
        call_id="bounded-output",
        capability="crewai.task.execute",
        channel="worker-output",
    )
    for index in range(2):
        content = bytes([index]) * 6
        digest = f"sha256:{hashlib.sha256(content).hexdigest()}"
        request = WorkerArtifactUploadRequest(
            expected_size=len(content),
            expected_digest=digest,
            media_type="application/octet-stream",
            provenance=provenance.model_copy(update={"call_id": f"bounded-output-{index}"}),
        )
        upload = artifacts.open_upload(
            expected_size=request.expected_size,
            expected_digest=request.expected_digest,
            media_type=request.media_type,
            provenance=request.provenance,
        )
        if index == 0:
            service.register_artifact_upload(
                worker_id,
                grant.lease.id,
                upload.upload_id,
                request,
                grant.lease_token,
            )
        else:
            with pytest.raises(MishkanError, match="result bound"):
                service.register_artifact_upload(
                    worker_id,
                    grant.lease.id,
                    upload.upload_id,
                    request,
                    grant.lease_token,
                )


def test_heartbeat_refuses_invalid_bounds_unknown_leases_and_stale_workers(
    tmp_path: Path,
) -> None:
    database = tmp_path / "mishkan.db"
    SchemaManager(database).initialize()
    worker_id = _worker(database)
    repository = _repository(database)

    with pytest.raises(MishkanError) as missing:
        repository.heartbeat(uuid4())
    assert missing.value.envelope.code is ErrorCode.WORKER
    with pytest.raises(MishkanError):
        WorkerCoordinationRepository(
            database,
            heartbeat_timeout_seconds=0,
            lease_seconds=60,
            max_worker_concurrency=8,
            max_completion_bytes=1024,
        )
    with pytest.raises(MishkanError):
        WorkerCoordinationRepository(
            database,
            heartbeat_timeout_seconds=30,
            lease_seconds=60,
            max_worker_concurrency=0,
            max_completion_bytes=1024,
        )

    too_large = _heartbeat(worker_id).model_copy(
        update={
            "resources": _heartbeat(worker_id).resources.model_copy(update={"max_concurrency": 9})
        }
    )
    with pytest.raises(MishkanError):
        repository.record_heartbeat(too_large)

    first = repository.record_heartbeat(_heartbeat(worker_id))
    unknown_lease = _heartbeat(worker_id, sequence=2).model_copy(
        update={"active_lease_ids": (uuid4(),)}
    )
    with pytest.raises(MishkanError):
        repository.record_heartbeat(unknown_lease)
    updated = repository.record_heartbeat(_heartbeat(worker_id, sequence=2))
    assert updated.sequence == 2
    stale = repository.heartbeat(worker_id, now=updated.expires_at + timedelta(seconds=1))
    assert stale.availability is WorkerAvailability.STALE
    assert (
        repository.compatible_workers(
            (),
            TaskResourceLimits(timeout_seconds=60, max_result_bytes=1024),
            now=stale.expires_at + timedelta(seconds=1),
        )
        == ()
    )
    assert first.worker_id == worker_id


def test_lease_renewal_is_revision_guarded_and_begin_is_idempotent(tmp_path: Path) -> None:
    _database, _runs, _coordination, service, run_id, task_row_id, worker_id = _setup(tmp_path)
    grant = service.issue(_envelope(run_id, task_row_id, worker_id))

    begun = service.begin(worker_id, grant.lease.id, grant.lease_token)
    replay = service.begin(worker_id, grant.lease.id, grant.lease_token)
    renewed = service.renew(
        worker_id,
        grant.lease.id,
        grant.lease_token,
        expected_revision=begun.revision,
    )

    assert replay == begun
    assert renewed.revision == begun.revision + 1
    with pytest.raises(MishkanError) as stale:
        service.renew(
            worker_id,
            grant.lease.id,
            grant.lease_token,
            expected_revision=begun.revision,
        )
    assert stale.value.envelope.code is ErrorCode.REVISION_MISMATCH


@pytest.mark.parametrize(
    "change",
    [
        "deadline",
        "capability",
        "memory",
        "context",
        "repository",
        "plan",
        "contract",
        "attempt",
    ],
)
def test_lease_issuance_refuses_authority_and_capacity_drift(
    tmp_path: Path,
    change: str,
) -> None:
    _database, _runs, _coordination, service, run_id, task_row_id, worker_id = _setup(tmp_path)
    envelope = _envelope(run_id, task_row_id, worker_id)
    updates: dict[str, object]
    if change == "deadline":
        updates = {"deadline": utc_now() - timedelta(seconds=1)}
    elif change == "capability":
        updates = {
            "capability_requirements": (CapabilityRequirement(capability_id="browser.observe"),)
        }
    elif change == "memory":
        updates = {
            "resources": envelope.resources.model_copy(
                update={"memory_bytes": 16 * 1024 * 1024 * 1024}
            )
        }
    elif change == "context":
        updates = {"execution_context_revision": "c" * 40}
    elif change == "repository":
        updates = {"repository_revision": "c" * 40}
    elif change == "plan":
        updates = {"plan_fingerprint": "c" * 64}
    elif change == "contract":
        updates = {"task_contract": {**envelope.task_contract, "title": "Changed title"}}
    else:
        updates = {"task_attempt": 2}

    with pytest.raises(MishkanError):
        service.issue(envelope.model_copy(update=updates))


def test_completion_delivery_is_idempotent_and_content_conflicts_are_refused(
    tmp_path: Path,
) -> None:
    database, _runs, _coordination, service, run_id, task_row_id, worker_id = _setup(tmp_path)
    grant = service.issue(_envelope(run_id, task_row_id, worker_id))
    result = _result()
    submission = WorkerCompletionSubmission(
        envelope_id=grant.envelope.id,
        lease_id=grant.lease.id,
        execution_context_revision="b" * 40,
        repository_revision="b" * 40,
        result=result.model_dump(mode="json"),
        effect_report=EffectReportState.NOT_APPLICABLE,
    )

    first = service.complete(worker_id, submission, grant.lease_token)
    assert service.complete(worker_id, submission, grant.lease_token) == first
    conflicting = submission.model_copy(update={"result": {"different": True}})
    with pytest.raises(MishkanError) as reused:
        service.complete(worker_id, conflicting, grant.lease_token)
    assert reused.value.envelope.code is ErrorCode.DUPLICATE_RESULT

    bounded = WorkerCoordinationService(
        WorkerCoordinationRepository(
            database,
            heartbeat_timeout_seconds=30,
            lease_seconds=60,
            max_worker_concurrency=8,
            max_completion_bytes=1,
        )
    )
    with pytest.raises(MishkanError):
        bounded.complete(
            worker_id, submission.model_copy(update={"id": uuid4()}), grant.lease_token
        )


def test_stateless_expiry_releases_task_and_public_queries_detect_tampering(
    tmp_path: Path,
) -> None:
    database, runs, coordination, service, run_id, task_row_id, worker_id = _setup(tmp_path)
    grant = service.issue(_envelope(run_id, task_row_id, worker_id))

    assert coordination.envelope(grant.envelope.id) == grant.envelope
    coordination.expire_leases(now=grant.lease.expires_at + timedelta(seconds=1))
    assert runs.task_states(str(run_id)) == {"remote-task": TaskState.ELIGIBLE.value}
    with pytest.raises(MishkanError):
        coordination.envelope(uuid4())
    with pytest.raises(MishkanError):
        coordination.completion(uuid4())

    with Session(create_local_engine(database)) as session, session.begin():
        row = session.get(RemoteTaskEnvelopeRow, str(grant.envelope.id))
        assert row is not None
        row.fingerprint = "0" * 64
    with pytest.raises(MishkanError, match="fingerprint drifted"):
        coordination.envelope(grant.envelope.id)


def test_acceptance_refuses_unvalidated_or_changed_worker_results(tmp_path: Path) -> None:
    _database, _runs, coordination, service, run_id, task_row_id, worker_id = _setup(tmp_path)
    grant = service.issue(_envelope(run_id, task_row_id, worker_id))
    result = _result()
    submission = WorkerCompletionSubmission(
        envelope_id=grant.envelope.id,
        lease_id=grant.lease.id,
        execution_context_revision="b" * 40,
        repository_revision="b" * 40,
        result=result.model_dump(mode="json"),
        effect_report=EffectReportState.NOT_APPLICABLE,
    )
    delivered = service.complete(worker_id, submission, grant.lease_token)

    changed = result.model_copy(update={"summary": "Changed after delivery."})
    with pytest.raises(MishkanError) as mismatch:
        coordination.accept_completion(delivered.id, changed, _review())
    assert mismatch.value.envelope.code is ErrorCode.OUTPUT_CONTRACT
    rejected = _review().model_copy(update={"verdict": "rejected"})
    with pytest.raises(MishkanError):
        coordination.accept_completion(delivered.id, result, rejected)
    assert coordination.completion(delivered.id).state is WorkerCompletionState.DELIVERED
