"""Worker enrollment and bounded execution application services."""

from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta
from uuid import UUID

from pydantic import SecretStr
from sqlalchemy.orm import Session

from mishkan.domain.identity import new_id
from mishkan.domain.time import utc_now
from mishkan.planning.models import InitializationResult, ReviewDecision
from mishkan.workers.coordination import WorkerCoordinationRepository, lease_token_digest
from mishkan.workers.models import (
    CapabilityRequirement,
    EffectReportState,
    EnrollmentCredential,
    ImmutableTaskEnvelope,
    RemotePlacement,
    RemotePlacementRequest,
    TaskResourceLimits,
    WorkerArtifactUploadRequest,
    WorkerCertificateBundle,
    WorkerClaimRequest,
    WorkerCompletion,
    WorkerCompletionSubmission,
    WorkerEnrollmentRequest,
    WorkerHeartbeat,
    WorkerHeartbeatRecord,
    WorkerIdentity,
    WorkerLease,
    WorkerLeaseGrant,
    WorkerRotationRequest,
    WorkerStatus,
    WorkerTaskClaim,
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
        request_fingerprint = hashlib.sha256(
            request.certificate_signing_request_pem.encode()
        ).hexdigest()
        certificate = self._certificate_authority.sign_worker_csr(
            request.worker_id,
            request.certificate_signing_request_pem,
            lifetime_seconds=self._certificate_ttl_seconds,
        )
        persisted = self._repository.rotate(
            rotation_id=request.id,
            worker_id=request.worker_id,
            current_serial=request.current_certificate_serial,
            request_fingerprint=request_fingerprint,
            certificate=certificate,
        )
        return self._bundle(
            request.worker_id,
            persisted,
            bundle_id=request.id,
            created_at=request.created_at,
        )

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

    def authenticate_rotation_peer(
        self,
        certificate_pem: str,
        rotation_id: UUID,
    ) -> tuple[WorkerIdentity, str]:
        worker_id, serial, fingerprint = self._certificate_authority.inspect_worker_certificate(
            certificate_pem
        )
        return (
            self._repository.authenticate_rotation(
                worker_id,
                serial,
                fingerprint,
                rotation_id,
            ),
            serial,
        )

    def _bundle(
        self,
        worker_id: UUID,
        certificate: SignedWorkerCertificate,
        *,
        bundle_id: UUID | None = None,
        created_at: datetime | None = None,
    ) -> WorkerCertificateBundle:
        bundle = WorkerCertificateBundle(
            worker_id=worker_id,
            certificate_pem=certificate.certificate_pem,
            certificate_authority_pem=certificate.certificate_authority_pem,
            certificate_serial=certificate.serial,
            fingerprint_sha256=certificate.fingerprint_sha256,
            not_before=certificate.not_before,
            not_after=certificate.not_after,
        )
        if bundle_id is not None and created_at is not None:
            return bundle.model_copy(update={"id": bundle_id, "created_at": created_at})
        return bundle

    @staticmethod
    def _digest(secret: str) -> str:
        return hashlib.sha256(secret.encode()).hexdigest()


class WorkerCoordinationService:
    """Generate ephemeral lease credentials around the transactional authority."""

    def __init__(self, repository: WorkerCoordinationRepository) -> None:
        self._repository = repository

    def heartbeat(self, heartbeat: WorkerHeartbeat) -> WorkerHeartbeatRecord:
        return self._repository.record_heartbeat(heartbeat)

    def compatible_workers(
        self,
        requirements: tuple[CapabilityRequirement, ...],
        resources: TaskResourceLimits,
    ) -> tuple[WorkerHeartbeatRecord, ...]:
        return self._repository.compatible_workers(requirements, resources)

    def status(self, worker_id: UUID) -> WorkerStatus:
        return self._repository.worker_status(worker_id)

    def statuses(self, *, offset: int = 0, limit: int = 100) -> tuple[WorkerStatus, ...]:
        return self._repository.worker_statuses(offset=offset, limit=limit)

    def queue(self, request: RemotePlacementRequest) -> RemotePlacement:
        return self._repository.queue_placement(request)

    def queue_in_session(
        self,
        session: Session,
        request: RemotePlacementRequest,
    ) -> RemotePlacement:
        return self._repository.queue_placement_in_session(session, request)

    def claim(self, worker_id: UUID, request: WorkerClaimRequest) -> WorkerTaskClaim | None:
        return self._repository.claim_next(worker_id, request)

    def issue(self, envelope: ImmutableTaskEnvelope) -> WorkerLeaseGrant:
        secret = secrets.token_urlsafe(32)
        lease = self._repository.issue_lease(
            envelope,
            lease_id=new_id(),
            token_digest=lease_token_digest(secret),
        )
        return WorkerLeaseGrant(
            envelope=envelope,
            lease=lease,
            lease_token=SecretStr(secret),
        )

    def begin(self, worker_id: UUID, lease_id: UUID, lease_token: SecretStr) -> WorkerLease:
        return self._repository.begin_execution(
            worker_id,
            lease_id,
            token_digest=lease_token_digest(lease_token.get_secret_value()),
        )

    def renew(
        self,
        worker_id: UUID,
        lease_id: UUID,
        lease_token: SecretStr,
        *,
        expected_revision: int,
    ) -> WorkerLease:
        return self._repository.renew_lease(
            worker_id,
            lease_id,
            token_digest=lease_token_digest(lease_token.get_secret_value()),
            expected_revision=expected_revision,
        )

    def complete(
        self,
        worker_id: UUID,
        submission: WorkerCompletionSubmission,
        lease_token: SecretStr,
    ) -> WorkerCompletion:
        return self._repository.deliver_completion(
            worker_id,
            submission,
            token_digest=lease_token_digest(lease_token.get_secret_value()),
        )

    def accept_completion(
        self,
        completion_id: UUID,
        result: InitializationResult,
        review: ReviewDecision,
        *,
        session: Session | None = None,
    ) -> WorkerCompletion:
        return self._repository.accept_completion(
            completion_id,
            result,
            review,
            session=session,
        )

    def reconcile(self, envelope_id: UUID, *, effect_report: EffectReportState) -> None:
        self._repository.reconcile_expired_envelope(
            envelope_id,
            effect_report=effect_report,
        )

    def expire(self) -> tuple[UUID, ...]:
        return self._repository.expire_leases()

    def authorize_input_artifact(self, worker_id: UUID, artifact_id: UUID) -> None:
        self._repository.authorize_input_artifact(worker_id, artifact_id)

    def register_artifact_upload(
        self,
        worker_id: UUID,
        lease_id: UUID,
        upload_id: UUID,
        request: WorkerArtifactUploadRequest,
        lease_token: SecretStr,
    ) -> None:
        self._repository.register_artifact_upload(
            worker_id,
            lease_id,
            upload_id,
            request,
            token_digest=lease_token_digest(lease_token.get_secret_value()),
        )

    def authorize_artifact_upload(
        self,
        worker_id: UUID,
        upload_id: UUID,
        lease_token: SecretStr,
    ) -> UUID:
        return self._repository.authorize_artifact_upload(
            worker_id,
            upload_id,
            token_digest=lease_token_digest(lease_token.get_secret_value()),
        )

    def complete_artifact_upload(
        self,
        worker_id: UUID,
        upload_id: UUID,
        artifact_id: UUID,
        lease_token: SecretStr,
    ) -> None:
        self._repository.complete_artifact_upload(
            worker_id,
            upload_id,
            artifact_id,
            token_digest=lease_token_digest(lease_token.get_secret_value()),
        )
