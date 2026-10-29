"""Stateless leased worker loop with only transient, recoverable delivery state."""

from __future__ import annotations

import hashlib
import json
import platform
import secrets
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from uuid import UUID

import psutil  # type: ignore[import-untyped]
from pydantic import SecretStr

from mishkan.artifacts import ArtifactProvenance
from mishkan.domain.errors import ErrorCode, MishkanError
from mishkan.domain.time import utc_now
from mishkan.workers.client import WorkerHttpClient
from mishkan.workers.coordination import lease_token_digest
from mishkan.workers.models import (
    ImmutableTaskEnvelope,
    WorkerArtifactKind,
    WorkerArtifactUploadRequest,
    WorkerClaimRequest,
    WorkerCompletionSubmission,
    WorkerExecutionResult,
    WorkerHeartbeat,
    WorkerLease,
    WorkerResourceSnapshot,
    WorkerRuntimeProfile,
    WorkerTaskClaim,
)
from mishkan.workers.state import WorkerCredentialStore, WorkerLocalIdentity


class WorkerTaskExecutor(Protocol):
    def execute(
        self,
        envelope: ImmutableTaskEnvelope,
        inputs: dict[str, bytes],
    ) -> WorkerExecutionResult: ...


@dataclass(frozen=True, slots=True)
class PendingWorkerDelivery:
    request: WorkerClaimRequest
    lease_token: SecretStr
    claim: WorkerTaskClaim | None = None
    execution: WorkerExecutionResult | None = None
    uploaded_artifacts: tuple[tuple[str, UUID], ...] = ()
    submission: WorkerCompletionSubmission | None = None


class WorkerRuntimeJournal:
    """Persist retry identities and secrets, never authoritative task state."""

    def __init__(self, root: Path) -> None:
        self._sequence_path = root / "heartbeat-sequence"
        self._pending_path = root / "pending-delivery.json"
        self._lock = threading.Lock()

    def next_sequence(self) -> int:
        with self._lock:
            current = 0
            if self._sequence_path.exists():
                try:
                    current = int(self._sequence_path.read_text(encoding="utf-8"))
                except (OSError, ValueError) as exc:
                    raise MishkanError(
                        ErrorCode.WORKER,
                        "worker heartbeat state is invalid",
                    ) from exc
            sequence = current + 1
            WorkerCredentialStore._write_private(
                self._sequence_path,
                f"{sequence}\n".encode(),
            )
            return sequence

    def save(self, delivery: PendingWorkerDelivery) -> None:
        payload: dict[str, object] = {
            "request": delivery.request.model_dump(mode="json"),
            "lease_token": delivery.lease_token.get_secret_value(),
            "claim": (
                delivery.claim.model_dump(mode="json") if delivery.claim is not None else None
            ),
            "execution": (
                delivery.execution.model_dump(mode="json")
                if delivery.execution is not None
                else None
            ),
            "uploaded_artifacts": [
                [name, str(artifact_id)] for name, artifact_id in delivery.uploaded_artifacts
            ],
            "submission": (
                delivery.submission.model_dump(mode="json")
                if delivery.submission is not None
                else None
            ),
        }
        WorkerCredentialStore._write_private(
            self._pending_path,
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode(),
        )

    def load(self) -> PendingWorkerDelivery | None:
        if not self._pending_path.exists():
            return None
        try:
            payload = json.loads(self._pending_path.read_text(encoding="utf-8"))
            return PendingWorkerDelivery(
                request=WorkerClaimRequest.model_validate(payload["request"]),
                lease_token=SecretStr(str(payload["lease_token"])),
                claim=(
                    WorkerTaskClaim.model_validate(payload["claim"])
                    if payload.get("claim") is not None
                    else None
                ),
                execution=(
                    WorkerExecutionResult.model_validate(payload["execution"])
                    if payload.get("execution") is not None
                    else None
                ),
                uploaded_artifacts=tuple(
                    (str(item[0]), UUID(str(item[1])))
                    for item in payload.get("uploaded_artifacts", ())
                ),
                submission=(
                    WorkerCompletionSubmission.model_validate(payload["submission"])
                    if payload.get("submission") is not None
                    else None
                ),
            )
        except (KeyError, OSError, TypeError, ValueError) as exc:
            raise MishkanError(ErrorCode.WORKER, "pending worker delivery is invalid") from exc

    def clear(self) -> None:
        self._pending_path.unlink(missing_ok=True)


class WorkerProcess:
    """Claim, execute with CrewAI, and idempotently deliver one task at a time."""

    def __init__(
        self,
        identity: WorkerLocalIdentity,
        profile: WorkerRuntimeProfile,
        client: WorkerHttpClient,
        executor: WorkerTaskExecutor,
        journal: WorkerRuntimeJournal,
    ) -> None:
        self._identity = identity
        self._profile = profile
        self._client = client
        self._executor = executor
        self._journal = journal

    def run_once(self) -> bool:
        pending = self._journal.load()
        if pending is None:
            self._heartbeat(())
            secret = SecretStr(secrets.token_urlsafe(32))
            pending = PendingWorkerDelivery(
                request=WorkerClaimRequest(
                    lease_token_digest=lease_token_digest(secret.get_secret_value())
                ),
                lease_token=secret,
            )
            self._journal.save(pending)
        if pending.claim is None:
            claim = self._client.claim(pending.request)
            if claim is None:
                self._journal.clear()
                return False
            pending = PendingWorkerDelivery(
                request=pending.request,
                lease_token=pending.lease_token,
                claim=claim,
            )
            self._journal.save(pending)
        claim = pending.claim
        assert claim is not None
        if pending.submission is not None:
            self._client.complete(pending.submission, pending.lease_token)
            self._journal.clear()
            return True
        if claim.lease.expires_at <= utc_now():
            self._journal.clear()
            return False
        if claim.envelope.stateful_effects:
            raise MishkanError(
                ErrorCode.AUTHORITY_NOT_GRANTED,
                "this worker executor exposes no stateful capability",
            )
        self._validate_context(claim.envelope)
        active_lease = self._client.begin(claim.lease.id, pending.lease_token)
        inputs = self._materialize_inputs(claim.envelope)
        keeper = _LeaseKeeper(self, claim, active_lease, pending.lease_token)
        with keeper:
            execution = pending.execution
            if execution is None:
                execution = self._executor.execute(claim.envelope, inputs)
                pending = PendingWorkerDelivery(
                    request=pending.request,
                    lease_token=pending.lease_token,
                    claim=claim,
                    execution=execution,
                )
                self._journal.save(pending)
            self._validate_execution_size(claim.envelope, execution)
            pending = self._upload_generated_artifacts(pending, execution)
        if keeper.failure is not None:
            raise MishkanError(
                ErrorCode.WORKER,
                "worker lost its lease while CrewAI was executing",
                details={"failure_type": type(keeper.failure).__name__},
                retryable=True,
            )
        artifact_ids, patch_id, effect_ids = self._artifact_references(pending, execution)
        submission = WorkerCompletionSubmission(
            envelope_id=claim.envelope.id,
            lease_id=claim.lease.id,
            execution_context_revision=claim.envelope.execution_context_revision,
            repository_revision=claim.envelope.repository_revision,
            result=execution.result,
            artifact_ids=artifact_ids,
            unified_patch_artifact_id=patch_id,
            effect_evidence_artifact_ids=effect_ids,
            effect_report=execution.effect_report,
        )
        pending = PendingWorkerDelivery(
            request=pending.request,
            lease_token=pending.lease_token,
            claim=claim,
            execution=execution,
            uploaded_artifacts=pending.uploaded_artifacts,
            submission=submission,
        )
        self._journal.save(pending)
        self._client.complete(submission, pending.lease_token)
        self._journal.clear()
        return True

    def _validate_context(self, envelope: ImmutableTaskEnvelope) -> None:
        if envelope.repository_id is None:
            return
        binding = next(
            (
                item
                for item in self._profile.context_bindings
                if item.repository_id == envelope.repository_id
            ),
            None,
        )
        if (
            binding is None
            or binding.repository_revision != envelope.repository_revision
            or binding.execution_context_revision != envelope.execution_context_revision
        ):
            raise MishkanError(
                ErrorCode.REVISION_MISMATCH,
                "worker context does not match the immutable task envelope",
            )

    def _materialize_inputs(self, envelope: ImmutableTaskEnvelope) -> dict[str, bytes]:
        remaining = self._profile.max_input_bytes
        materialized: dict[str, bytes] = {}
        for reference in envelope.inputs:
            content = self._client.input_artifact(reference.artifact_id, max_bytes=remaining)
            observed = f"sha256:{hashlib.sha256(content).hexdigest()}"
            if observed != reference.digest:
                raise MishkanError(
                    ErrorCode.ARTIFACT,
                    "materialized input differs from the immutable envelope digest",
                )
            materialized[reference.name] = content
            remaining -= len(content)
        return materialized

    def _upload_generated_artifacts(
        self,
        pending: PendingWorkerDelivery,
        execution: WorkerExecutionResult,
    ) -> PendingWorkerDelivery:
        claim = pending.claim
        assert claim is not None
        uploaded = dict(pending.uploaded_artifacts)
        for generated in execution.generated_artifacts:
            if generated.name in uploaded:
                continue
            content = generated.content
            request = WorkerArtifactUploadRequest(
                expected_size=len(content),
                expected_digest=f"sha256:{hashlib.sha256(content).hexdigest()}",
                media_type=generated.media_type,
                provenance=ArtifactProvenance(
                    producer_identity=f"worker:{self._identity.worker_id}",
                    run_id=str(claim.envelope.run_id),
                    task_attempt_id=str(claim.envelope.id),
                    call_id=generated.name,
                    capability="crewai.task.execute",
                    channel=f"worker-{generated.kind.value}",
                ),
                sensitivity=generated.sensitivity,
                retention=generated.retention,
            )
            manifest = self._client.upload_artifact(
                claim.lease.id,
                pending.lease_token,
                content,
                request,
                chunk_bytes=self._profile.artifact_chunk_bytes,
            )
            uploaded[generated.name] = manifest.id
            pending = PendingWorkerDelivery(
                request=pending.request,
                lease_token=pending.lease_token,
                claim=claim,
                execution=execution,
                uploaded_artifacts=tuple(sorted(uploaded.items())),
            )
            self._journal.save(pending)
        return pending

    @staticmethod
    def _validate_execution_size(
        envelope: ImmutableTaskEnvelope,
        execution: WorkerExecutionResult,
    ) -> None:
        result_bytes = len(
            json.dumps(execution.result, sort_keys=True, separators=(",", ":")).encode()
        )
        generated_bytes = sum(len(item.content) for item in execution.generated_artifacts)
        if result_bytes + generated_bytes > envelope.resources.max_result_bytes:
            raise MishkanError(
                ErrorCode.OUTPUT_CONTRACT,
                "worker result and generated artifacts exceed the immutable envelope bound",
            )

    @staticmethod
    def _artifact_references(
        pending: PendingWorkerDelivery,
        execution: WorkerExecutionResult,
    ) -> tuple[tuple[UUID, ...], UUID | None, tuple[UUID, ...]]:
        uploaded = dict(pending.uploaded_artifacts)
        output_ids = list(execution.artifact_ids)
        effect_ids = list(execution.effect_evidence_artifact_ids)
        patch_id = execution.unified_patch_artifact_id
        for generated in execution.generated_artifacts:
            artifact_id = uploaded.get(generated.name)
            if artifact_id is None:
                raise MishkanError(ErrorCode.ARTIFACT, "generated worker artifact is not uploaded")
            if generated.kind is WorkerArtifactKind.OUTPUT:
                output_ids.append(artifact_id)
            elif generated.kind is WorkerArtifactKind.EFFECT_EVIDENCE:
                effect_ids.append(artifact_id)
            elif patch_id is not None:
                raise MishkanError(
                    ErrorCode.OUTPUT_CONTRACT,
                    "worker execution produced more than one unified patch",
                )
            else:
                patch_id = artifact_id
        return tuple(output_ids), patch_id, tuple(effect_ids)

    def _heartbeat(self, active_lease_ids: tuple) -> None:  # type: ignore[type-arg]
        resources = WorkerResourceSnapshot(
            platform=platform.system().lower(),
            architecture=platform.machine().lower(),
            logical_cpu_count=psutil.cpu_count(logical=True) or 1,
            available_memory_bytes=max(1, int(psutil.virtual_memory().available)),
            max_concurrency=self._profile.max_concurrency,
            active_tasks=len(active_lease_ids),
        )
        self._client.heartbeat(
            WorkerHeartbeat(
                worker_id=self._identity.worker_id,
                sequence=self._journal.next_sequence(),
                capabilities=self._profile.capabilities,
                resources=resources,
                active_lease_ids=active_lease_ids,
                observed_at=utc_now(),
            )
        )


class _LeaseKeeper:
    def __init__(
        self,
        process: WorkerProcess,
        claim: WorkerTaskClaim,
        lease: WorkerLease,
        token: SecretStr,
    ) -> None:
        self._process = process
        self._claim = claim
        self._lease = lease
        self._token = token
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.failure: BaseException | None = None

    def __enter__(self) -> _LeaseKeeper:
        self._thread = threading.Thread(target=self._run, name="mishkan-worker-lease", daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *_args: object) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=self._process._profile.heartbeat_interval_seconds + 1)

    def _run(self) -> None:
        heartbeat_interval = self._process._profile.heartbeat_interval_seconds
        renewal_interval = self._process._profile.renewal_interval_seconds
        until_renewal = renewal_interval
        while not self._stop.wait(min(heartbeat_interval, until_renewal)):
            try:
                self._process._heartbeat((self._claim.lease.id,))
                until_renewal -= min(heartbeat_interval, until_renewal)
                if until_renewal <= 0:
                    self._lease = self._process._client.renew(
                        self._lease.id,
                        self._token,
                        expected_revision=self._lease.revision,
                    )
                    until_renewal = renewal_interval
            except BaseException as exc:
                self.failure = exc
                self._stop.set()
                return
