from __future__ import annotations

import hashlib
import json
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from pydantic import SecretStr

from mishkan.domain.errors import MishkanError
from mishkan.domain.time import utc_now
from mishkan.workers import (
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
