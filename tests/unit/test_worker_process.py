from __future__ import annotations

import base64
import os
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

import pytest

from mishkan.domain.time import utc_now
from mishkan.planning.models import PlanTask
from mishkan.workers import (
    CapabilityObservation,
    EffectReportState,
    ImmutableTaskEnvelope,
    TaskPathScope,
    TaskResourceLimits,
    WorkerArtifactKind,
    WorkerClaimRequest,
    WorkerCompletionState,
    WorkerExecutionResult,
    WorkerGeneratedArtifact,
    WorkerHeartbeat,
    WorkerHeartbeatRecord,
    WorkerLease,
    WorkerLeaseState,
    WorkerRuntimeProfile,
    WorkerTaskClaim,
)
from mishkan.workers.models import WorkerAvailability, WorkerCompletion
from mishkan.workers.process import WorkerProcess, WorkerRuntimeJournal
from mishkan.workers.state import WorkerLocalIdentity


def _envelope(worker_id):  # type: ignore[no-untyped-def]
    contract = PlanTask(
        task_id="remote-task",
        title="Synthesize immutable evidence",
        purpose="Return one bounded structured result.",
        assigned_role="Engineer",
        tools=("file.read",),
        evidence_paths=("README.md",),
    )
    return ImmutableTaskEnvelope(
        run_id=uuid4(),
        task_row_id=uuid4(),
        task_id=contract.task_id,
        task_attempt=1,
        worker_id=worker_id,
        execution_context_revision="a" * 40,
        plan_fingerprint="b" * 64,
        task_contract=contract.model_dump(mode="json"),
        role_definition_fingerprint="c" * 64,
        policy_fingerprint="d" * 64,
        path_scopes=(
            TaskPathScope(
                root_id="repository",
                relative_path="README.md",
                permissions=("read",),
            ),
        ),
        capability_requirements=(),
        resources=TaskResourceLimits(timeout_seconds=60, max_result_bytes=4096),
        deadline=utc_now() + timedelta(minutes=5),
    )


def _claim(worker_id):  # type: ignore[no-untyped-def]
    envelope = _envelope(worker_id)
    now = utc_now()
    lease = WorkerLease(
        envelope_id=envelope.id,
        worker_id=worker_id,
        state=WorkerLeaseState.ACTIVE,
        revision=1,
        issued_at=now,
        expires_at=now + timedelta(minutes=1),
        last_renewed_at=now,
    )
    return WorkerTaskClaim(claim_id=uuid4(), envelope=envelope, lease=lease)


def _profile() -> WorkerRuntimeProfile:
    return WorkerRuntimeProfile(
        model_route="worker",
        capabilities=(
            CapabilityObservation(
                capability_id="crewai.task.execute",
                version="1.0.0",
                fingerprint="e" * 64,
            ),
        ),
        max_concurrency=1,
        heartbeat_interval_seconds=300,
        renewal_interval_seconds=300,
        idle_poll_seconds=1,
    )


def _identity(worker_id):  # type: ignore[no-untyped-def]
    return WorkerLocalIdentity(
        worker_id=worker_id,
        display_name="worker-one",
        daemon_url="https://daemon.test",
        certificate_serial="123",
        certificate_fingerprint_sha256="f" * 64,
        certificate_not_after=utc_now() + timedelta(hours=1),
    )


class _Executor:
    def __init__(self) -> None:
        self.calls = 0

    def execute(self, _envelope, _inputs):  # type: ignore[no-untyped-def]
        self.calls += 1
        return WorkerExecutionResult(result={"summary": "remote result"})


class _Client:
    def __init__(self, claim: WorkerTaskClaim) -> None:
        self.claim_template = claim
        self.claim_value = claim
        self.claim_requests: list[WorkerClaimRequest] = []
        self.heartbeats: list[WorkerHeartbeat] = []
        self.submissions = []
        self.uploads = []
        self.fail_uploads = 0
        self.fail_completions = 0

    def heartbeat(self, heartbeat):  # type: ignore[no-untyped-def]
        self.heartbeats.append(heartbeat)
        return WorkerHeartbeatRecord(
            worker_id=heartbeat.worker_id,
            sequence=heartbeat.sequence,
            advertisement_fingerprint=heartbeat.fingerprint,
            capabilities=heartbeat.capabilities,
            resources=heartbeat.resources,
            active_lease_ids=heartbeat.active_lease_ids,
            observed_at=heartbeat.observed_at,
            expires_at=heartbeat.observed_at + timedelta(seconds=30),
            availability=WorkerAvailability.ONLINE,
        )

    def claim(self, request):  # type: ignore[no-untyped-def]
        self.claim_requests.append(request)
        value = self.claim_value
        self.claim_value = None
        return value

    def begin(self, _lease_id, _token):  # type: ignore[no-untyped-def]
        return self.claim_template.lease

    def renew(self, _lease_id, _token, *, expected_revision):  # type: ignore[no-untyped-def]
        return self.claim_template.lease.model_copy(update={"revision": expected_revision + 1})

    def input_artifact(self, _artifact_id, *, max_bytes):  # type: ignore[no-untyped-def]
        del max_bytes
        raise AssertionError("fixture has no inputs")

    def complete(self, submission, _token):  # type: ignore[no-untyped-def]
        self.submissions.append(submission)
        if self.fail_completions:
            self.fail_completions -= 1
            raise RuntimeError("response lost")
        return WorkerCompletion(
            id=submission.id,
            created_at=submission.created_at,
            envelope_id=submission.envelope_id,
            lease_id=submission.lease_id,
            worker_id=self.claim_template.envelope.worker_id,
            fingerprint=submission.fingerprint,
            state=WorkerCompletionState.DELIVERED,
            result=submission.result,
            artifact_ids=submission.artifact_ids,
            unified_patch_artifact_id=submission.unified_patch_artifact_id,
            effect_evidence_artifact_ids=submission.effect_evidence_artifact_ids,
            effect_report=EffectReportState.NOT_APPLICABLE,
            received_at=utc_now(),
        )

    def upload_artifact(  # type: ignore[no-untyped-def]
        self, lease_id, lease_token, content, request, *, chunk_bytes
    ):
        if self.fail_uploads:
            self.fail_uploads -= 1
            raise RuntimeError("artifact response lost")
        artifact_id = uuid4()
        self.uploads.append((lease_id, lease_token, content, request, chunk_bytes, artifact_id))
        return type("Manifest", (), {"id": artifact_id})()


WORKER_ID = uuid4()


def _process(tmp_path: Path, client: _Client, executor: _Executor) -> WorkerProcess:
    root = tmp_path / "state"
    root.mkdir(mode=0o700, exist_ok=True)
    return WorkerProcess(
        _identity(WORKER_ID),
        _profile(),
        client,  # type: ignore[arg-type]
        executor,
        WorkerRuntimeJournal(root),
    )


def test_worker_process_claims_executes_and_delivers_once(tmp_path: Path) -> None:
    client = _Client(_claim(WORKER_ID))
    executor = _Executor()
    process = _process(tmp_path, client, executor)

    assert process.run_once()

    assert executor.calls == 1
    assert len(client.heartbeats) == 1
    assert len(client.claim_requests) == 1
    assert len(client.submissions) == 1
    assert client.submissions[0].result == {"summary": "remote result"}
    assert not (tmp_path / "state" / "pending-delivery.json").exists()


def test_lost_completion_response_replays_same_submission_without_reexecution(
    tmp_path: Path,
) -> None:
    client = _Client(_claim(WORKER_ID))
    client.fail_completions = 1
    executor = _Executor()
    process = _process(tmp_path, client, executor)

    with pytest.raises(RuntimeError, match="response lost"):
        process.run_once()
    pending = tmp_path / "state" / "pending-delivery.json"
    assert pending.exists()
    assert (pending.stat().st_mode & 0o777) == 0o600
    first_submission = client.submissions[0]

    assert process.run_once()
    assert executor.calls == 1
    assert client.submissions[1] == first_submission
    assert not pending.exists()


def test_generated_outputs_are_uploaded_once_and_bound_to_completion(tmp_path: Path) -> None:
    client = _Client(_claim(WORKER_ID))
    executor = _Executor()
    generated = WorkerExecutionResult(
        result={"summary": "remote result"},
        generated_artifacts=(
            WorkerGeneratedArtifact(
                name="report",
                media_type="text/plain",
                content_base64=base64.b64encode(b"durable report").decode("ascii"),
            ),
            WorkerGeneratedArtifact(
                name="patch",
                kind=WorkerArtifactKind.UNIFIED_PATCH,
                media_type="text/x-diff",
                content_base64=base64.b64encode(b"--- a/file\n+++ b/file\n").decode("ascii"),
            ),
        ),
    )
    executor.execute = lambda _envelope, _inputs: generated  # type: ignore[method-assign]
    process = _process(tmp_path, client, executor)

    assert process.run_once()

    assert executor.calls == 0
    assert [upload[2] for upload in client.uploads] == [
        b"durable report",
        b"--- a/file\n+++ b/file\n",
    ]
    submission = client.submissions[0]
    assert submission.artifact_ids == (client.uploads[0][-1],)
    assert submission.unified_patch_artifact_id == client.uploads[1][-1]
    assert all(
        upload[3].provenance.task_attempt_id == str(client.claim_template.envelope.id)
        for upload in client.uploads
    )


def test_generated_execution_survives_artifact_delivery_retry(tmp_path: Path) -> None:
    client = _Client(_claim(WORKER_ID))
    client.fail_uploads = 1

    class GeneratingExecutor(_Executor):
        def execute(self, _envelope, _inputs):  # type: ignore[no-untyped-def]
            self.calls += 1
            return WorkerExecutionResult(
                result={"summary": "stable result"},
                generated_artifacts=(
                    WorkerGeneratedArtifact(
                        name="report",
                        media_type="text/plain",
                        content_base64=base64.b64encode(b"stable bytes").decode("ascii"),
                    ),
                ),
            )

    executor = GeneratingExecutor()
    process = _process(tmp_path, client, executor)

    with pytest.raises(RuntimeError, match="artifact response lost"):
        process.run_once()
    assert executor.calls == 1
    assert '"execution"' in (tmp_path / "state" / "pending-delivery.json").read_text()

    assert process.run_once()
    assert executor.calls == 1
    assert client.submissions[0].artifact_ids == (client.uploads[0][-1],)


def test_claim_request_and_secret_survive_ambiguous_claim_response(tmp_path: Path) -> None:
    client = _Client(_claim(WORKER_ID))
    executor = _Executor()
    process = _process(tmp_path, client, executor)
    original_claim = client.claim
    attempts = 0

    def lose_once(request):  # type: ignore[no-untyped-def]
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            client.claim_requests.append(request)
            raise RuntimeError("claim response lost")
        return original_claim(request)

    client.claim = lose_once  # type: ignore[method-assign]
    with pytest.raises(RuntimeError, match="claim response lost"):
        process.run_once()
    pending = (tmp_path / "state" / "pending-delivery.json").read_text()
    assert "lease_token" in pending

    assert process.run_once()
    assert client.claim_requests[0] == client.claim_requests[1]
    assert executor.calls == 1


def test_runtime_journal_refuses_corrupt_sequence(tmp_path: Path) -> None:
    root = tmp_path / "state"
    root.mkdir(mode=0o700)
    sequence = root / "heartbeat-sequence"
    sequence.write_text("invalid", encoding="utf-8")
    os.chmod(sequence, 0o600)

    with pytest.raises(Exception, match="heartbeat state is invalid"):
        WorkerRuntimeJournal(root).next_sequence()
