from __future__ import annotations

import hashlib
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path
from threading import Barrier
from uuid import UUID, uuid4

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.orm import Session

from mishkan.artifacts import ArtifactProvenance
from mishkan.artifacts.bodies import S3ArtifactBodyStore
from mishkan.artifacts.service import DurableArtifactService
from mishkan.domain.errors import MishkanError
from mishkan.domain.time import utc_now
from mishkan.persistence import DatabaseState, LocalRunRepository, SchemaManager
from mishkan.persistence.runtime import DatabaseRuntime
from mishkan.persistence.sqlite import (
    TaskRow,
    WorkerCompletionRow,
    WorkerTaskAcceptanceRow,
)
from mishkan.planning.models import AcceptedPlan, InitializationResult, PlanTask, ReviewDecision
from mishkan.repository.models import DiscoverySnapshot, RepositoryBinding
from mishkan.workers import (
    CapabilityObservation,
    CapabilityRequirement,
    EffectReportState,
    RemotePlacementRequest,
    TaskPathScope,
    TaskResourceLimits,
    WorkerArtifactUploadRequest,
    WorkerCertificateAuthority,
    WorkerCertificateBundle,
    WorkerClaimRequest,
    WorkerCompletionState,
    WorkerCompletionSubmission,
    WorkerCoordinationRepository,
    WorkerCoordinationService,
    WorkerEnrollmentRequest,
    WorkerEnrollmentService,
    WorkerHeartbeat,
    WorkerIdentityRepository,
    WorkerResourceSnapshot,
)
from mishkan.workers.coordination import lease_token_digest

pytestmark = [pytest.mark.acceptance, pytest.mark.distributed]


def _runtime() -> DatabaseRuntime:
    dsn = os.environ.get("MISHKAN_I09_POSTGRES_DSN")
    if not dsn:
        pytest.skip("set MISHKAN_I09_POSTGRES_DSN for the distributed gate")
    return DatabaseRuntime.postgresql(
        dsn,
        pool_size=8,
        max_overflow=8,
        connect_timeout_seconds=5,
        statement_timeout_ms=90_000,
    )


def _body_store() -> S3ArtifactBodyStore:
    endpoint = os.environ.get("MISHKAN_I09_S3_ENDPOINT")
    if not endpoint:
        pytest.skip("set MISHKAN_I09_S3_ENDPOINT for the distributed gate")
    return S3ArtifactBodyStore.from_boto3(
        bucket="mishkan-evidence",
        prefix=f"gate/{uuid4()}",
        endpoint_url=endpoint,
        region_name="us-east-1",
        access_key_id="test",
        secret_access_key="test",
        verify=False,
        addressing_style="path",
    )


def _plan() -> AcceptedPlan:
    tasks = tuple(
        PlanTask(
            task_id=task_id,
            title=f"Execute {task_id} remotely",
            purpose="Produce one independently validated immutable result.",
            assigned_role="Engineer",
            tools=("file.read",),
            evidence_paths=("README.md",),
            depends_on=depends_on,
        )
        for task_id, depends_on in (
            ("worker-one-task", ()),
            ("worker-two-task", ()),
            ("recovered-task", ("worker-one-task", "worker-two-task")),
            ("revision-mismatch-task", ("recovered-task",)),
        )
    )
    return AcceptedPlan(
        objective="Prove distributed loss recovery",
        outcome_id="distributed-gate",
        repository_revision="b" * 40,
        tasks=tasks,
        fingerprint="d" * 64,
        discovery_fingerprint="c" * 64,
    )


def _run(runtime: DatabaseRuntime, root: Path) -> tuple[UUID, dict[str, UUID]]:
    repository = LocalRunRepository(runtime)
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
        f"Distributed gate {uuid4()}",
        "distributed-gate",
    )
    repository.mark_awaiting_approval(snapshot.run_id)
    repository.accept_plan(snapshot.run_id, _plan())
    repository.start_run(snapshot.run_id)
    with Session(runtime.engine) as session:
        tasks = session.scalars(select(TaskRow).where(TaskRow.run_id == snapshot.run_id)).all()
    return UUID(snapshot.run_id), {row.task_key: UUID(row.id) for row in tasks}


def _enrollment_service(
    runtime: DatabaseRuntime,
) -> tuple[WorkerEnrollmentService, WorkerIdentityRepository]:
    key = ec.generate_private_key(ec.SECP256R1())
    now = utc_now()
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "MISHKAN I09 gate CA")])
    certificate = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=True,
                crl_sign=True,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .sign(key, hashes.SHA256())
    )
    authority = WorkerCertificateAuthority(
        certificate.public_bytes(serialization.Encoding.PEM).decode(),
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ).decode(),
    )
    repository = WorkerIdentityRepository(runtime)
    return (
        WorkerEnrollmentService(
            repository,
            authority,
            enrollment_ttl_seconds=60,
            certificate_ttl_seconds=3600,
        ),
        repository,
    )


def _worker(enrollment: WorkerEnrollmentService) -> tuple[UUID, WorkerCertificateBundle]:
    worker_id = uuid4()
    credential = enrollment.create_enrollment(actor_id="CTO")
    key = ec.generate_private_key(ec.SECP256R1())
    csr = (
        x509.CertificateSigningRequestBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, str(worker_id))]))
        .sign(key, hashes.SHA256())
        .public_bytes(serialization.Encoding.PEM)
        .decode()
    )
    bundle = enrollment.enroll(
        WorkerEnrollmentRequest(
            worker_id=worker_id,
            display_name=f"gate-{worker_id}",
            token=credential.token,
            certificate_signing_request_pem=csr,
        )
    )
    return worker_id, bundle


def _heartbeat(worker_id: UUID) -> WorkerHeartbeat:
    return WorkerHeartbeat(
        worker_id=worker_id,
        sequence=1,
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
            logical_cpu_count=4,
            available_memory_bytes=4 * 1024 * 1024 * 1024,
            max_concurrency=2,
            active_tasks=0,
        ),
        observed_at=utc_now(),
    )


def _placement(run_id: UUID, task_id: str, worker_id: UUID | None) -> RemotePlacementRequest:
    return RemotePlacementRequest(
        run_id=run_id,
        task_id=task_id,
        preferred_worker_id=worker_id,
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
        resources=TaskResourceLimits(timeout_seconds=60, max_result_bytes=4096),
        deadline=utc_now() + timedelta(minutes=5),
    )


def _claim(service: WorkerCoordinationService, worker_id: UUID):  # type: ignore[no-untyped-def]
    token = SecretStr(f"lease-{uuid4()}-{uuid4()}")
    claim = service.claim(
        worker_id,
        WorkerClaimRequest(lease_token_digest=lease_token_digest(token.get_secret_value())),
    )
    assert claim is not None
    service.begin(worker_id, claim.lease.id, token)
    return claim, token


def _result(task_id: str) -> InitializationResult:
    return InitializationResult(
        repository_revision="b" * 40,
        task_id=task_id,
        summary=f"Accepted result for {task_id}.",
        cited_paths=("README.md",),
        findings=(f"Worker produced {task_id} evidence.",),
    )


def _review(task_id: str) -> ReviewDecision:
    return ReviewDecision(
        task_id=task_id,
        verdict="accepted",
        summary=f"Independent validation accepted {task_id}.",
        checked_citations=("README.md",),
    )


def _submission(claim, result, *, completion_id=None, artifacts=()):  # type: ignore[no-untyped-def]
    return WorkerCompletionSubmission(
        id=completion_id or uuid4(),
        envelope_id=claim.envelope.id,
        lease_id=claim.lease.id,
        execution_context_revision=claim.envelope.execution_context_revision,
        repository_revision=claim.envelope.repository_revision,
        result=result.model_dump(mode="json"),
        artifact_ids=artifacts,
        effect_report=EffectReportState.NOT_APPLICABLE,
    )


def test_two_workers_recover_loss_and_accept_exactly_once(tmp_path: Path) -> None:
    runtime = _runtime()
    try:
        schema = SchemaManager(runtime).status()
        if schema.state is DatabaseState.EMPTY:
            SchemaManager(runtime).initialize()
        else:
            SchemaManager(runtime).require_current()
        run_id, _tasks = _run(runtime, tmp_path)
        enrollment, _identities = _enrollment_service(runtime)
        worker_one, worker_one_certificate = _worker(enrollment)
        worker_two, worker_two_certificate = _worker(enrollment)
        repository = WorkerCoordinationRepository(
            runtime,
            heartbeat_timeout_seconds=30,
            lease_seconds=10,
            max_worker_concurrency=2,
            max_completion_bytes=1024 * 1024,
        )
        service = WorkerCoordinationService(repository)
        service.heartbeat(_heartbeat(worker_one))
        service.heartbeat(_heartbeat(worker_two))

        service.queue(_placement(run_id, "worker-one-task", worker_one))
        service.queue(_placement(run_id, "worker-two-task", worker_two))
        first_claim, first_token = _claim(service, worker_one)
        second_claim, second_token = _claim(service, worker_two)

        artifact_content = b"shared S3 evidence from worker one"
        digest = f"sha256:{hashlib.sha256(artifact_content).hexdigest()}"
        artifact_service = DurableArtifactService(
            runtime,
            tmp_path / "artifact-staging",
            max_artifact_bytes=4096,
            max_chunk_bytes=1024,
            body_store=_body_store(),
        )
        provenance = ArtifactProvenance(
            producer_identity=f"worker:{worker_one}",
            run_id=str(run_id),
            task_attempt_id=str(first_claim.envelope.id),
            call_id="distributed-gate-output",
            capability="crewai.task.execute",
            channel="worker-output",
        )
        upload_request = WorkerArtifactUploadRequest(
            expected_size=len(artifact_content),
            expected_digest=digest,
            media_type="text/plain",
            provenance=provenance,
        )
        upload = artifact_service.open_upload(
            expected_size=upload_request.expected_size,
            expected_digest=upload_request.expected_digest,
            media_type=upload_request.media_type,
            provenance=upload_request.provenance,
        )
        service.register_artifact_upload(
            worker_one,
            first_claim.lease.id,
            upload.upload_id,
            upload_request,
            first_token,
        )
        artifact_service.append_chunk(upload.upload_id, offset=0, content=artifact_content)
        manifest = artifact_service.commit_upload(upload.upload_id)
        service.complete_artifact_upload(
            worker_one,
            upload.upload_id,
            manifest.id,
            first_token,
        )
        assert (
            b"".join(artifact_service.iter_bytes(manifest.reference, chunk_size=7))
            == artifact_content
        )

        first_result = _result("worker-one-task")
        first_completion = service.complete(
            worker_one,
            _submission(first_claim, first_result, artifacts=(manifest.id,)),
            first_token,
        )
        repository.accept_completion(
            first_completion.id,
            first_result,
            _review("worker-one-task"),
        )
        second_result = _result("worker-two-task")
        second_completion = service.complete(
            worker_two,
            _submission(second_claim, second_result),
            second_token,
        )
        repository.accept_completion(
            second_completion.id,
            second_result,
            _review("worker-two-task"),
        )

        service.queue(_placement(run_id, "recovered-task", None))
        barrier = Barrier(2)

        def contend(worker_id: UUID):  # type: ignore[no-untyped-def]
            token = SecretStr(f"lease-{uuid4()}-{uuid4()}")
            request = WorkerClaimRequest(
                lease_token_digest=lease_token_digest(token.get_secret_value())
            )
            barrier.wait(timeout=10)
            return worker_id, token, service.claim(worker_id, request)

        with ThreadPoolExecutor(max_workers=2) as pool:
            contenders = tuple(pool.map(contend, (worker_one, worker_two), timeout=30))
        winners = tuple(item for item in contenders if item[2] is not None)
        losers = tuple(item for item in contenders if item[2] is None)
        assert len(winners) == 1
        assert len(losers) == 1
        lost_worker, lost_token, lost_claim = winners[0]
        recovery_worker = losers[0][0]
        assert lost_claim is not None
        service.begin(lost_worker, lost_claim.lease.id, lost_token)
        repository.expire_leases(now=lost_claim.lease.expires_at + timedelta(seconds=1))

        recovered_claim, recovered_token = _claim(service, recovery_worker)
        assert recovered_claim.envelope.task_attempt == lost_claim.envelope.task_attempt + 1
        result = _result("recovered-task")
        stale = service.complete(
            lost_worker,
            _submission(lost_claim, result),
            lost_token,
        )
        assert stale.state is WorkerCompletionState.STALE
        delivered_submission = _submission(recovered_claim, result)
        delivered = service.complete(recovery_worker, delivered_submission, recovered_token)
        accepted = repository.accept_completion(
            delivered.id,
            result,
            _review("recovered-task"),
        )
        assert accepted.state is WorkerCompletionState.ACCEPTED

        replay = service.complete(recovery_worker, delivered_submission, recovered_token)
        duplicate = service.complete(
            recovery_worker,
            _submission(recovered_claim, result),
            recovered_token,
        )
        assert replay.id == accepted.id
        assert duplicate.state is WorkerCompletionState.DUPLICATE
        refused_duplicate = repository.accept_completion(
            duplicate.id,
            result,
            _review("recovered-task"),
        )
        assert refused_duplicate.state is WorkerCompletionState.DUPLICATE

        service.queue(_placement(run_id, "revision-mismatch-task", worker_two))
        revision_claim, revision_token = _claim(service, worker_two)
        mismatched = _submission(revision_claim, _result("revision-mismatch-task")).model_copy(
            update={"execution_context_revision": "f" * 40}
        )
        revision_refusal = service.complete(worker_two, mismatched, revision_token)
        assert revision_refusal.state is WorkerCompletionState.STALE
        with Session(runtime.engine) as session:
            revision_row = session.get(WorkerCompletionRow, str(revision_refusal.id))
            assert revision_row is not None
            assert revision_row.decision_reason is not None
            assert "execution-context revision" in revision_row.decision_reason

        enrollment.revoke_worker(worker_one, expected_revision=1)
        with pytest.raises(MishkanError):
            enrollment.authenticate_certificate(worker_one_certificate.certificate_pem)
        assert (
            enrollment.authenticate_certificate(worker_two_certificate.certificate_pem).id
            == worker_two
        )

        with Session(runtime.engine) as session:
            acceptances = session.scalars(
                select(WorkerTaskAcceptanceRow).where(
                    WorkerTaskAcceptanceRow.task_row_id == str(recovered_claim.envelope.task_row_id)
                )
            ).all()
        assert len(acceptances) == 1
        assert acceptances[0].completion_id == str(delivered.id)
    finally:
        runtime.dispose()
