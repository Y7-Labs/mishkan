"""Strict public records for individually authenticated workers."""

from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import ConfigDict, Field, SecretStr, field_validator

from mishkan.domain.identity import DomainRecord
from mishkan.domain.time import require_aware


class EnrollmentState(StrEnum):
    ACTIVE = "active"
    CONSUMED = "consumed"
    REVOKED = "revoked"
    EXPIRED = "expired"


class WorkerState(StrEnum):
    ACTIVE = "active"
    REVOKED = "revoked"


class CertificateState(StrEnum):
    CURRENT = "current"
    SUPERSEDED = "superseded"
    REVOKED = "revoked"


class EnrollmentCredential(DomainRecord):
    """One-time credential. SecretStr prevents accidental repr or JSON disclosure."""

    token: SecretStr
    expires_at: datetime

    @field_validator("expires_at")
    @classmethod
    def expiry_is_aware(cls, value: datetime) -> datetime:
        return require_aware(value)


class WorkerEnrollmentRequest(DomainRecord):
    worker_id: UUID
    display_name: str = Field(min_length=1, max_length=160)
    token: SecretStr
    certificate_signing_request_pem: str = Field(min_length=1, max_length=1_048_576)


class WorkerRotationRequest(DomainRecord):
    worker_id: UUID
    current_certificate_serial: str = Field(min_length=1, max_length=128)
    certificate_signing_request_pem: str = Field(min_length=1, max_length=1_048_576)


class WorkerCertificateBundle(DomainRecord):
    worker_id: UUID
    certificate_pem: str
    certificate_authority_pem: str
    certificate_serial: str = Field(min_length=1, max_length=128)
    fingerprint_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    not_before: datetime
    not_after: datetime

    @field_validator("not_before", "not_after")
    @classmethod
    def validity_is_aware(cls, value: datetime) -> datetime:
        return require_aware(value)


class WorkerIdentity(DomainRecord):
    model_config = ConfigDict(extra="forbid", frozen=True)

    display_name: str = Field(min_length=1, max_length=160)
    state: WorkerState
    current_certificate_serial: str | None = Field(default=None, max_length=128)
    revision: int = Field(ge=1)
    updated_at: datetime
    revoked_at: datetime | None = None

    @field_validator("updated_at", "revoked_at")
    @classmethod
    def timestamps_are_aware(cls, value: datetime | None) -> datetime | None:
        return require_aware(value) if value is not None else None


class IssuedWorkerCertificate(DomainRecord):
    worker_id: UUID
    serial: str = Field(min_length=1, max_length=128)
    state: CertificateState
    fingerprint_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    not_before: datetime
    not_after: datetime
    superseded_at: datetime | None = None
    revoked_at: datetime | None = None

    @field_validator("not_before", "not_after", "superseded_at", "revoked_at")
    @classmethod
    def certificate_timestamps_are_aware(cls, value: datetime | None) -> datetime | None:
        return require_aware(value) if value is not None else None
