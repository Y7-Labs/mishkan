"""Worker enrollment application service."""

from __future__ import annotations

import hashlib
import secrets
from datetime import timedelta
from uuid import UUID

from pydantic import SecretStr

from mishkan.domain.time import utc_now
from mishkan.workers.models import (
    EnrollmentCredential,
    WorkerCertificateBundle,
    WorkerEnrollmentRequest,
    WorkerIdentity,
    WorkerRotationRequest,
)
from mishkan.workers.pki import SignedWorkerCertificate, WorkerCertificateAuthority
from mishkan.workers.repository import WorkerIdentityRepository


class WorkerEnrollmentService:
    def __init__(
        self,
        repository: WorkerIdentityRepository,
        certificate_authority: WorkerCertificateAuthority,
        *,
        enrollment_ttl_seconds: int,
        certificate_ttl_seconds: int,
    ) -> None:
        self._repository = repository
        self._certificate_authority = certificate_authority
        self._enrollment_ttl_seconds = enrollment_ttl_seconds
        self._certificate_ttl_seconds = certificate_ttl_seconds

    def create_enrollment(self, *, actor_id: str) -> EnrollmentCredential:
        secret = secrets.token_urlsafe(48)
        expires_at = utc_now() + timedelta(seconds=self._enrollment_ttl_seconds)
        credential = EnrollmentCredential(token=SecretStr(secret), expires_at=expires_at)
        self._repository.create_enrollment_token(
            token_id=credential.id,
            token_digest=self._digest(secret),
            actor_id=actor_id,
            expires_at=expires_at,
        )
        return credential

    def revoke_enrollment(self, token_id: UUID) -> None:
        self._repository.revoke_enrollment_token(token_id)

    def enroll(self, request: WorkerEnrollmentRequest) -> WorkerCertificateBundle:
        certificate = self._certificate_authority.sign_worker_csr(
            request.worker_id,
            request.certificate_signing_request_pem,
            lifetime_seconds=self._certificate_ttl_seconds,
        )
        self._repository.enroll(
            token_digest=self._digest(request.token.get_secret_value()),
            worker_id=request.worker_id,
            display_name=request.display_name,
            certificate=certificate,
        )
        return self._bundle(request.worker_id, certificate)

    def rotate(self, request: WorkerRotationRequest) -> WorkerCertificateBundle:
        certificate = self._certificate_authority.sign_worker_csr(
            request.worker_id,
            request.certificate_signing_request_pem,
            lifetime_seconds=self._certificate_ttl_seconds,
        )
        self._repository.rotate(
            worker_id=request.worker_id,
            current_serial=request.current_certificate_serial,
            certificate=certificate,
        )
        return self._bundle(request.worker_id, certificate)

    def revoke_worker(self, worker_id: UUID, *, expected_revision: int) -> WorkerIdentity:
        return self._repository.revoke_worker(worker_id, expected_revision=expected_revision)

    def authenticate_certificate(self, certificate_pem: str) -> WorkerIdentity:
        identity, _serial = self.authenticate_peer(certificate_pem)
        return identity

    def authenticate_peer(self, certificate_pem: str) -> tuple[WorkerIdentity, str]:
        worker_id, serial, fingerprint = self._certificate_authority.inspect_worker_certificate(
            certificate_pem
        )
        return self._repository.authenticate(worker_id, serial, fingerprint), serial

    def _bundle(
        self,
        worker_id: UUID,
        certificate: SignedWorkerCertificate,
    ) -> WorkerCertificateBundle:
        return WorkerCertificateBundle(
            worker_id=worker_id,
            certificate_pem=certificate.certificate_pem,
            certificate_authority_pem=certificate.certificate_authority_pem,
            certificate_serial=certificate.serial,
            fingerprint_sha256=certificate.fingerprint_sha256,
            not_before=certificate.not_before,
            not_after=certificate.not_after,
        )

    @staticmethod
    def _digest(secret: str) -> str:
        return hashlib.sha256(secret.encode()).hexdigest()
