"""Strict public records for authenticated workers and bounded remote execution."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from pathlib import PurePosixPath
from typing import Any, Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator

from mishkan.domain.identity import DomainRecord
from mishkan.domain.time import require_aware
from mishkan.policy.models import canonical_fingerprint, security_identifier


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


class WorkerAvailability(StrEnum):
    ONLINE = "online"
    STALE = "stale"
    REVOKED = "revoked"


class RemoteEnvelopeState(StrEnum):
    LEASED = "leased"
    EXECUTING = "executing"
    VALIDATING = "validating"
    COMPLETED = "completed"
    EXPIRED = "expired"
    RECONCILIATION_REQUIRED = "reconciliation_required"
    CANCELLED = "cancelled"


class WorkerLeaseState(StrEnum):
    ACTIVE = "active"
    COMPLETED = "completed"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


class WorkerCompletionState(StrEnum):
    DELIVERED = "delivered"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    DUPLICATE = "duplicate"
    STALE = "stale"


class RemotePlacementState(StrEnum):
    QUEUED = "queued"
    LEASED = "leased"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class EffectReportState(StrEnum):
    NOT_APPLICABLE = "not_applicable"
    PROVEN = "proven"
    UNCERTAIN = "uncertain"


class WorkerValueModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class CapabilityObservation(WorkerValueModel):
    """One worker-observed capability; observation never grants authority."""

    capability_id: str = Field(pattern=r"^[a-z][a-z0-9_.-]{2,127}$")
    version: str = Field(min_length=1, max_length=128)
    fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    attributes: dict[str, str] = Field(default_factory=dict, max_length=64)

    @field_validator("capability_id", "version")
    @classmethod
    def identifiers_are_stable(cls, value: str) -> str:
        return security_identifier(value)


class CapabilityRequirement(WorkerValueModel):
    """Contextual requirement carried by one immutable task envelope."""

    capability_id: str = Field(pattern=r"^[a-z][a-z0-9_.-]{2,127}$")
    acceptable_versions: tuple[str, ...] = Field(default=(), max_length=64)
    required_fingerprint: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")

    @field_validator("capability_id")
    @classmethod
    def identifier_is_stable(cls, value: str) -> str:
        return security_identifier(value)

    @field_validator("acceptable_versions")
    @classmethod
    def versions_are_unique(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) != len(set(value)):
            raise ValueError("acceptable capability versions must be unique")
        return value

    def satisfied_by(self, observations: tuple[CapabilityObservation, ...]) -> bool:
        for observation in observations:
            if observation.capability_id != self.capability_id:
                continue
            if self.acceptable_versions and observation.version not in self.acceptable_versions:
                continue
            if (
                self.required_fingerprint is not None
                and observation.fingerprint != self.required_fingerprint
            ):
                continue
            return True
        return False


class WorkerResourceSnapshot(WorkerValueModel):
    platform: str = Field(min_length=1, max_length=64)
    architecture: str = Field(min_length=1, max_length=64)
    logical_cpu_count: int = Field(ge=1, le=65_536)
    available_memory_bytes: int = Field(ge=1)
    max_concurrency: int = Field(ge=1, le=10_000)
    active_tasks: int = Field(ge=0, le=10_000)

    @model_validator(mode="after")
    def active_tasks_fit_capacity(self) -> Self:
        if self.active_tasks > self.max_concurrency:
            raise ValueError("worker active tasks exceed advertised concurrency")
        return self


class WorkerHeartbeat(DomainRecord):
    worker_id: UUID
    sequence: int = Field(ge=1)
    capabilities: tuple[CapabilityObservation, ...]
    resources: WorkerResourceSnapshot
    active_lease_ids: tuple[UUID, ...] = ()
    observed_at: datetime

    @field_validator("observed_at")
    @classmethod
    def observed_at_is_aware(cls, value: datetime) -> datetime:
        return require_aware(value)

    @model_validator(mode="after")
    def advertisement_is_unambiguous(self) -> Self:
        capability_ids = [item.capability_id for item in self.capabilities]
        if len(capability_ids) != len(set(capability_ids)):
            raise ValueError("worker capabilities must have unique identities")
        if len(self.active_lease_ids) != len(set(self.active_lease_ids)):
            raise ValueError("active worker leases must be unique")
        return self

    @property
    def fingerprint(self) -> str:
        return canonical_fingerprint(self.model_dump(mode="json", exclude={"id", "created_at"}))


class WorkerHeartbeatRecord(DomainRecord):
    worker_id: UUID
    sequence: int = Field(ge=1)
    advertisement_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    capabilities: tuple[CapabilityObservation, ...]
    resources: WorkerResourceSnapshot
    active_lease_ids: tuple[UUID, ...] = ()
    observed_at: datetime
    expires_at: datetime
    availability: WorkerAvailability

    @field_validator("observed_at", "expires_at")
    @classmethod
    def heartbeat_timestamps_are_aware(cls, value: datetime) -> datetime:
        return require_aware(value)


class TaskPathScope(WorkerValueModel):
    root_id: str = Field(min_length=1, max_length=128)
    relative_path: str = Field(min_length=1, max_length=4_096)
    permissions: tuple[Literal["read", "write", "execute"], ...] = Field(min_length=1)

    @field_validator("relative_path")
    @classmethod
    def path_is_relative_and_bounded(cls, value: str) -> str:
        path = PurePosixPath(value)
        if path.is_absolute() or ".." in path.parts or "." in path.parts:
            raise ValueError("task path scope must be normalized and relative")
        return value

    @field_validator("permissions")
    @classmethod
    def permissions_are_unique(
        cls,
        value: tuple[Literal["read", "write", "execute"], ...],
    ) -> tuple[Literal["read", "write", "execute"], ...]:
        if len(value) != len(set(value)):
            raise ValueError("task path permissions must be unique")
        return value


class TaskResourceLimits(WorkerValueModel):
    timeout_seconds: int = Field(ge=1, le=604_800)
    memory_bytes: int | None = Field(default=None, ge=1)
    cpu_count: int | None = Field(default=None, ge=1, le=65_536)
    max_result_bytes: int = Field(ge=1, le=1_073_741_824)


class TaskInputReference(WorkerValueModel):
    name: str = Field(pattern=r"^[a-z][a-z0-9_.-]{1,127}$")
    artifact_id: UUID
    digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")


class ImmutableTaskEnvelope(DomainRecord):
    """Coordinator-issued, worker-bound assignment that cannot be amended in place."""

    schema_version: Literal["1.0"] = "1.0"
    run_id: UUID
    task_row_id: UUID
    task_id: str = Field(pattern=r"^[a-z][a-z0-9-]{1,63}$")
    task_attempt: int = Field(ge=1)
    worker_id: UUID
    execution_context_revision: str = Field(min_length=7, max_length=128)
    repository_id: str | None = Field(default=None, min_length=12, max_length=256)
    repository_revision: str | None = Field(default=None, min_length=7, max_length=128)
    plan_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    task_contract: dict[str, Any]
    role_definition_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    inputs: tuple[TaskInputReference, ...] = ()
    policy_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    path_scopes: tuple[TaskPathScope, ...] = Field(min_length=1)
    capability_requirements: tuple[CapabilityRequirement, ...]
    resources: TaskResourceLimits
    deadline: datetime
    stateful_effects: bool = False

    @field_validator("deadline")
    @classmethod
    def deadline_is_aware(cls, value: datetime) -> datetime:
        return require_aware(value)

    @model_validator(mode="after")
    def identities_and_context_are_complete(self) -> Self:
        if (self.repository_id is None) != (self.repository_revision is None):
            raise ValueError("repository identity and revision must be supplied together")
        requirement_ids = [item.capability_id for item in self.capability_requirements]
        if len(requirement_ids) != len(set(requirement_ids)):
            raise ValueError("task capability requirements must have unique identities")
        input_names = [item.name for item in self.inputs]
        if len(input_names) != len(set(input_names)):
            raise ValueError("task input names must be unique")
        return self

    @property
    def fingerprint(self) -> str:
        return canonical_fingerprint(self.model_dump(mode="json", exclude={"id", "created_at"}))


class RemotePlacementRequest(DomainRecord):
    """Governed daemon request to make one accepted task remotely claimable."""

    run_id: UUID
    task_id: str = Field(pattern=r"^[a-z][a-z0-9-]{1,63}$")
    preferred_worker_id: UUID | None = None
    role_definition_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    inputs: tuple[TaskInputReference, ...] = ()
    policy_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    path_scopes: tuple[TaskPathScope, ...] = Field(min_length=1)
    capability_requirements: tuple[CapabilityRequirement, ...]
    resources: TaskResourceLimits
    deadline: datetime
    stateful_effects: bool = False

    @field_validator("deadline")
    @classmethod
    def placement_deadline_is_aware(cls, value: datetime) -> datetime:
        return require_aware(value)

    @model_validator(mode="after")
    def placement_values_are_unique(self) -> Self:
        capability_ids = [item.capability_id for item in self.capability_requirements]
        if len(capability_ids) != len(set(capability_ids)):
            raise ValueError("placement capability requirements must have unique identities")
        input_names = [item.name for item in self.inputs]
        if len(input_names) != len(set(input_names)):
            raise ValueError("placement input names must be unique")
        return self

    @property
    def fingerprint(self) -> str:
        return canonical_fingerprint(self.model_dump(mode="json", exclude={"id", "created_at"}))


class RemotePlacement(DomainRecord):
    run_id: UUID
    task_row_id: UUID
    task_id: str
    state: RemotePlacementState
    request_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    selected_worker_id: UUID | None = None
    envelope_id: UUID | None = None
    revision: int = Field(ge=1)
    updated_at: datetime

    @field_validator("updated_at")
    @classmethod
    def placement_updated_at_is_aware(cls, value: datetime) -> datetime:
        return require_aware(value)


class WorkerClaimRequest(DomainRecord):
    """Idempotent pull claim; the worker retains the matching lease secret."""

    lease_token_digest: str = Field(pattern=r"^[0-9a-f]{64}$")


class WorkerLease(DomainRecord):
    envelope_id: UUID
    worker_id: UUID
    state: WorkerLeaseState
    revision: int = Field(ge=1)
    issued_at: datetime
    expires_at: datetime
    last_renewed_at: datetime
    completed_at: datetime | None = None

    @field_validator("issued_at", "expires_at", "last_renewed_at", "completed_at")
    @classmethod
    def lease_timestamps_are_aware(cls, value: datetime | None) -> datetime | None:
        return require_aware(value) if value is not None else None


class WorkerTaskClaim(DomainRecord):
    claim_id: UUID
    envelope: ImmutableTaskEnvelope
    lease: WorkerLease


class WorkerLeaseGrant(DomainRecord):
    envelope: ImmutableTaskEnvelope
    lease: WorkerLease
    lease_token: SecretStr = Field(min_length=43, max_length=256)


class WorkerCompletionSubmission(DomainRecord):
    envelope_id: UUID
    lease_id: UUID
    execution_context_revision: str = Field(min_length=7, max_length=128)
    repository_revision: str | None = Field(default=None, min_length=7, max_length=128)
    result: dict[str, Any]
    artifact_ids: tuple[UUID, ...] = ()
    unified_patch_artifact_id: UUID | None = None
    effect_evidence_artifact_ids: tuple[UUID, ...] = ()
    effect_report: EffectReportState

    @model_validator(mode="after")
    def artifact_references_are_unique(self) -> Self:
        references = (*self.artifact_ids, *self.effect_evidence_artifact_ids)
        if len(references) != len(set(references)):
            raise ValueError("completion artifact references must be unique")
        return self

    @property
    def fingerprint(self) -> str:
        return canonical_fingerprint(self.model_dump(mode="json", exclude={"id", "created_at"}))


class WorkerCompletion(DomainRecord):
    envelope_id: UUID
    lease_id: UUID
    worker_id: UUID
    fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    state: WorkerCompletionState
    result: dict[str, Any]
    artifact_ids: tuple[UUID, ...] = ()
    unified_patch_artifact_id: UUID | None = None
    effect_evidence_artifact_ids: tuple[UUID, ...] = ()
    effect_report: EffectReportState
    received_at: datetime
    accepted_at: datetime | None = None

    @field_validator("received_at", "accepted_at")
    @classmethod
    def completion_timestamps_are_aware(cls, value: datetime | None) -> datetime | None:
        return require_aware(value) if value is not None else None


class EnrollmentCredential(DomainRecord):
    """One-time credential. SecretStr prevents accidental repr or JSON disclosure."""

    token: SecretStr = Field(min_length=43, max_length=256)
    expires_at: datetime

    @field_validator("expires_at")
    @classmethod
    def expiry_is_aware(cls, value: datetime) -> datetime:
        return require_aware(value)


class WorkerEnrollmentRequest(DomainRecord):
    worker_id: UUID
    display_name: str = Field(min_length=1, max_length=160)
    token: SecretStr = Field(min_length=43, max_length=256)
    certificate_signing_request_pem: str = Field(min_length=1, max_length=1_048_576)


class WorkerRotationRequest(DomainRecord):
    worker_id: UUID
    current_certificate_serial: str = Field(min_length=1, max_length=128)
    certificate_signing_request_pem: str = Field(min_length=1, max_length=1_048_576)


class WorkerCertificateRotationRequest(DomainRecord):
    """Public rotation payload; worker identity is derived only from the mTLS peer."""

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
