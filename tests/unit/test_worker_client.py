from __future__ import annotations

import hashlib
import json
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from pydantic import SecretStr

from mishkan.artifacts import (
    ArtifactLifecycle,
    ArtifactManifest,
    ArtifactProvenance,
    ArtifactValidation,
    UploadSession,
)
from mishkan.domain.errors import MishkanError
from mishkan.domain.time import utc_now
from mishkan.workers import (
    WorkerArtifactUploadRequest,
    WorkerCertificateBundle,
    WorkerClaimRequest,
    WorkerEnrollmentRequest,
    WorkerIdentity,
    WorkerLease,
    WorkerLeaseState,
    WorkerState,
)
from mishkan.workers.client import WorkerHttpClient


def _paths(tmp_path: Path) -> tuple[Path, Path, Path]:
    paths = (tmp_path / "ca.pem", tmp_path / "worker.pem", tmp_path / "worker-key.pem")
    for path in paths:
        path.write_text("test material", encoding="utf-8")
    return paths


def test_enrollment_transmits_secret_once_without_bearer(tmp_path: Path) -> None:
    worker_id = uuid4()
    secret = "one-time-enrollment-secret-that-is-long-enough"
    now = utc_now()
    bundle = WorkerCertificateBundle(
        worker_id=worker_id,
        certificate_pem="worker certificate",
        certificate_authority_pem="worker ca",
        certificate_serial="123",
        fingerprint_sha256="a" * 64,
        not_before=now,
        not_after=now + timedelta(hours=1),
    )

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/workers/enroll"
        assert "authorization" not in request.headers
        assert json.loads(request.content)["token"] == secret
        return httpx.Response(200, json=bundle.model_dump(mode="json"))

    ca, _certificate, _key = _paths(tmp_path)
    enrolled = WorkerHttpClient.enroll(
        "https://daemon.test",
        WorkerEnrollmentRequest(
            worker_id=worker_id,
            display_name="worker-one",
            token=SecretStr(secret),
            certificate_signing_request_pem="csr",
        ),
        ca_certificate=ca,
        transport=httpx.MockTransport(handler),
    )

    assert enrolled == bundle


def test_worker_protocol_uses_claim_digest_and_lease_header_without_bearer(
    tmp_path: Path,
) -> None:
    worker_id = uuid4()
    lease_id = uuid4()
    envelope_id = uuid4()
    now = utc_now()
    identity = WorkerIdentity(
        id=worker_id,
        display_name="worker-one",
        state=WorkerState.ACTIVE,
        current_certificate_serial="123",
        revision=1,
        updated_at=now,
    )
    lease = WorkerLease(
        id=lease_id,
        envelope_id=envelope_id,
        worker_id=worker_id,
        state=WorkerLeaseState.ACTIVE,
        revision=1,
        issued_at=now,
        expires_at=now + timedelta(minutes=1),
        last_renewed_at=now,
    )
    secret = "worker-retained-lease-secret-that-is-long-enough"
    claim = WorkerClaimRequest(lease_token_digest="b" * 64)
    observed: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert "authorization" not in request.headers
        observed.append(request.url.path)
        if request.url.path == "/v1/workers/me":
            return httpx.Response(200, json=identity.model_dump(mode="json"))
        if request.url.path == "/v1/workers/tasks/claim":
            assert json.loads(request.content)["lease_token_digest"] == "b" * 64
            assert "x-mishkan-lease-token" not in request.headers
            return httpx.Response(
                200,
                content=b"null",
                headers={"content-type": "application/json"},
            )
        assert request.url.path == f"/v1/workers/leases/{lease_id}/start"
        assert request.headers["x-mishkan-lease-token"] == secret
        return httpx.Response(200, json=lease.model_dump(mode="json"))

    ca, certificate, key = _paths(tmp_path)
    with WorkerHttpClient(
        "https://daemon.test",
        ca_certificate=ca,
        client_certificate=certificate,
        client_private_key=key,
        transport=httpx.MockTransport(handler),
    ) as client:
        assert client.identity() == identity
        assert client.claim(claim) is None
        assert client.begin(lease_id, SecretStr(secret)) == lease

    assert observed == [
        "/v1/workers/me",
        "/v1/workers/tasks/claim",
        f"/v1/workers/leases/{lease_id}/start",
    ]


def test_worker_input_artifact_is_bounded_and_digest_verified(tmp_path: Path) -> None:
    content = b"immutable task input"
    artifact_id = uuid4()

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == f"/v1/workers/artifacts/{artifact_id}/content"
        return httpx.Response(
            200,
            content=content,
            headers={"X-Mishkan-Artifact-Digest": f"sha256:{hashlib.sha256(content).hexdigest()}"},
        )

    ca, certificate, key = _paths(tmp_path)
    with WorkerHttpClient(
        "https://daemon.test",
        ca_certificate=ca,
        client_certificate=certificate,
        client_private_key=key,
        transport=httpx.MockTransport(handler),
    ) as client:
        assert client.input_artifact(artifact_id, max_bytes=len(content)) == content
        with pytest.raises(MishkanError, match="exceeds worker bound"):
            client.input_artifact(artifact_id, max_bytes=len(content) - 1)


def test_worker_artifact_upload_streams_under_lease_token(tmp_path: Path) -> None:
    content = b"streamed-worker-output"
    digest = f"sha256:{hashlib.sha256(content).hexdigest()}"
    lease_id = uuid4()
    upload_id = uuid4()
    worker_id = uuid4()
    token = SecretStr("worker-retained-lease-secret-that-is-long-enough")
    provenance = ArtifactProvenance(
        producer_identity=f"worker:{worker_id}",
        run_id=str(uuid4()),
        task_attempt_id=str(uuid4()),
        call_id="worker-output",
        capability="crewai.task.execute",
        channel="worker-output",
    )
    request_model = WorkerArtifactUploadRequest(
        expected_size=len(content),
        expected_digest=digest,
        media_type="text/plain",
        provenance=provenance,
    )
    manifest = ArtifactManifest(
        digest=digest,
        size_bytes=len(content),
        declared_media_type="text/plain",
        provenance=provenance,
        sensitivity="internal",
        retention="run",
        validation=ArtifactValidation.INTEGRITY_VERIFIED,
        lifecycle=ArtifactLifecycle.AVAILABLE,
        storage_ref=f"sha256/{digest[7:9]}/{digest[9:]}",
    )
    received = bytearray()

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["x-mishkan-lease-token"] == token.get_secret_value()
        if request.url.path == f"/v1/workers/leases/{lease_id}/artifacts":
            return httpx.Response(
                200,
                json=UploadSession(
                    upload_id=upload_id,
                    expected_size=len(content),
                    expected_digest=digest,
                    media_type="text/plain",
                    offset=0,
                    lifecycle="staging",
                ).model_dump(mode="json"),
            )
        if request.url.path == f"/v1/workers/artifact-uploads/{upload_id}":
            assert int(request.url.params["offset"]) == len(received)
            received.extend(request.content)
            return httpx.Response(
                200,
                json=UploadSession(
                    upload_id=upload_id,
                    expected_size=len(content),
                    expected_digest=digest,
                    media_type="text/plain",
                    offset=len(received),
                    lifecycle="staging",
                ).model_dump(mode="json"),
            )
        assert request.url.path == f"/v1/workers/artifact-uploads/{upload_id}/commit"
        return httpx.Response(200, json=manifest.model_dump(mode="json"))

    ca, certificate, key = _paths(tmp_path)
    with WorkerHttpClient(
        "https://daemon.test",
        ca_certificate=ca,
        client_certificate=certificate,
        client_private_key=key,
        transport=httpx.MockTransport(handler),
    ) as client:
        uploaded = client.upload_artifact(
            lease_id,
            token,
            content,
            request_model,
            chunk_bytes=4,
        )

    assert bytes(received) == content
    assert uploaded == manifest
