"""mTLS-only client for the distributed worker protocol."""

from __future__ import annotations

import hashlib
from pathlib import Path
from uuid import UUID

import httpx
from pydantic import SecretStr

from mishkan.domain.errors import ErrorCode, MishkanError
from mishkan.workers.models import (
    WorkerCertificateBundle,
    WorkerCertificateRotationRequest,
    WorkerClaimRequest,
    WorkerCompletion,
    WorkerCompletionSubmission,
    WorkerEnrollmentRequest,
    WorkerHeartbeat,
    WorkerHeartbeatRecord,
    WorkerIdentity,
    WorkerLease,
    WorkerTaskClaim,
)


class WorkerHttpClient:
    """Thin protocol client; it owns neither placement nor acceptance authority."""

    def __init__(
        self,
        base_url: str,
        *,
        ca_certificate: Path,
        client_certificate: Path,
        client_private_key: Path,
        timeout_seconds: float = 120,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"),
            verify=str(ca_certificate),
            cert=(str(client_certificate), str(client_private_key)),
            timeout=timeout_seconds,
            transport=transport,
        )

    @classmethod
    def enroll(
        cls,
        base_url: str,
        request: WorkerEnrollmentRequest,
        *,
        ca_certificate: Path,
        timeout_seconds: float = 120,
        transport: httpx.BaseTransport | None = None,
    ) -> WorkerCertificateBundle:
        with httpx.Client(
            base_url=base_url.rstrip("/"),
            verify=str(ca_certificate),
            timeout=timeout_seconds,
            transport=transport,
        ) as client:
            payload = request.model_dump(mode="json", exclude={"token"})
            payload["token"] = request.token.get_secret_value()
            response = client.post(
                "/v1/workers/enroll",
                json=payload,
            )
            response.raise_for_status()
            return WorkerCertificateBundle.model_validate(response.json())

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> WorkerHttpClient:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def identity(self) -> WorkerIdentity:
        response = self._client.get("/v1/workers/me")
        response.raise_for_status()
        return WorkerIdentity.model_validate(response.json())

    def rotate(
        self,
        request: WorkerCertificateRotationRequest,
    ) -> WorkerCertificateBundle:
        response = self._client.post(
            "/v1/workers/certificate/rotate",
            json=request.model_dump(mode="json"),
        )
        response.raise_for_status()
        return WorkerCertificateBundle.model_validate(response.json())

    def heartbeat(self, heartbeat: WorkerHeartbeat) -> WorkerHeartbeatRecord:
        response = self._client.post(
            "/v1/workers/heartbeat",
            json=heartbeat.model_dump(mode="json"),
        )
        response.raise_for_status()
        return WorkerHeartbeatRecord.model_validate(response.json())

    def claim(self, request: WorkerClaimRequest) -> WorkerTaskClaim | None:
        response = self._client.post(
            "/v1/workers/tasks/claim",
            json=request.model_dump(mode="json"),
        )
        response.raise_for_status()
        payload = response.json()
        return WorkerTaskClaim.model_validate(payload) if payload is not None else None

    def begin(self, lease_id: UUID, lease_token: SecretStr) -> WorkerLease:
        response = self._client.post(
            f"/v1/workers/leases/{lease_id}/start",
            headers=self._lease_headers(lease_token),
        )
        response.raise_for_status()
        return WorkerLease.model_validate(response.json())

    def renew(
        self,
        lease_id: UUID,
        lease_token: SecretStr,
        *,
        expected_revision: int,
    ) -> WorkerLease:
        response = self._client.post(
            f"/v1/workers/leases/{lease_id}/renew",
            headers=self._lease_headers(lease_token),
            params={"expected_revision": expected_revision},
        )
        response.raise_for_status()
        return WorkerLease.model_validate(response.json())

    def complete(
        self,
        submission: WorkerCompletionSubmission,
        lease_token: SecretStr,
    ) -> WorkerCompletion:
        response = self._client.post(
            "/v1/workers/tasks/complete",
            headers=self._lease_headers(lease_token),
            json=submission.model_dump(mode="json"),
        )
        response.raise_for_status()
        return WorkerCompletion.model_validate(response.json())

    def input_artifact(self, artifact_id: UUID, *, max_bytes: int) -> bytes:
        if max_bytes < 1:
            raise MishkanError(ErrorCode.OUTPUT_CONTRACT, "artifact input bound is invalid")
        with self._client.stream(
            "GET",
            f"/v1/workers/artifacts/{artifact_id}/content",
        ) as response:
            response.raise_for_status()
            expected = response.headers.get("X-Mishkan-Artifact-Digest")
            if expected is None:
                raise MishkanError(ErrorCode.ARTIFACT, "artifact digest header is absent")
            content = bytearray()
            digest = hashlib.sha256()
            for chunk in response.iter_bytes():
                if len(content) + len(chunk) > max_bytes:
                    raise MishkanError(ErrorCode.ARTIFACT, "artifact input exceeds worker bound")
                content.extend(chunk)
                digest.update(chunk)
        if expected != f"sha256:{digest.hexdigest()}":
            raise MishkanError(ErrorCode.ARTIFACT, "artifact input digest does not match")
        return bytes(content)

    @staticmethod
    def _lease_headers(lease_token: SecretStr) -> dict[str, str]:
        return {"X-Mishkan-Lease-Token": lease_token.get_secret_value()}
