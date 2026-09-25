"""Versioned contracts for attributed knowledge and durable provider operations."""

from __future__ import annotations

import hashlib
import json
import unicodedata
from datetime import datetime
from enum import StrEnum
from typing import Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from mishkan.domain.identity import new_id
from mishkan.domain.time import require_aware, utc_now

_ARTIFACT_PATTERN = r"^artifact:[0-9a-f-]{36}$"
_DIGEST_PATTERN = r"^sha256:[a-f0-9]{64}$"


class KnowledgeModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class KnowledgeClass(StrEnum):
    LITERAL = "literal"
    EPISODIC = "episodic"
    SEMANTIC = "semantic"
    STRUCTURAL = "structural"


class KnowledgeQueryState(StrEnum):
    RUNNING = "running"
    COMPLETED = "completed"
    DEGRADED = "degraded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class KnowledgeAttemptState(StrEnum):
    SUCCEEDED = "succeeded"
    EMPTY = "empty"
    UNAVAILABLE = "unavailable"
    INCOMPATIBLE = "incompatible"
    FAILED = "failed"
    CANCELLED = "cancelled"


class KnowledgeStaleness(StrEnum):
    CURRENT = "current"
    STALE = "stale"
    NOT_APPLICABLE = "not_applicable"
    UNKNOWN = "unknown"


class KnowledgeOperationKind(StrEnum):
    INGEST = "ingest"
    REFRESH = "refresh"
    CAPTURE = "capture"
    PROMOTE = "promote"


class KnowledgeOperationState(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCEL_REQUESTED = "cancel_requested"
    CANCELLED = "cancelled"
    UNCERTAIN = "uncertain"


class KnowledgeCorpusState(StrEnum):
    EMPTY = "empty"
    INDEXING = "indexing"
    READY = "ready"
    STALE = "stale"
    DEGRADED = "degraded"
    FAILED = "failed"


class KnowledgePromotionDisposition(StrEnum):
    PROPOSED = "proposed"
    APPROVED = "approved"
    REJECTED = "rejected"
    REVOKED = "revoked"


class KnowledgeScope(KnowledgeModel):
    """Exact execution and organizational scope for one knowledge request."""

    project_id: str = Field(min_length=1, max_length=256)
    context_revision: str = Field(min_length=1, max_length=512)
    repository_id: str | None = Field(default=None, min_length=1, max_length=256)
    repository_revision: str | None = Field(default=None, min_length=1, max_length=512)
    mission_id: str | None = Field(default=None, min_length=1, max_length=256)
    run_id: str | None = Field(default=None, min_length=1, max_length=256)
    task_id: str | None = Field(default=None, min_length=1, max_length=256)
    agent_identity: str | None = Field(default=None, min_length=1, max_length=256)

    @model_validator(mode="after")
    def repository_identity_is_complete(self) -> Self:
        if (self.repository_id is None) != (self.repository_revision is None):
            raise ValueError("repository identity and revision must be provided together")
        return self


class KnowledgeQuery(KnowledgeModel):
    schema_version: Literal["1.0"] = "1.0"
    query_id: UUID = Field(default_factory=new_id)
    knowledge_class: KnowledgeClass
    question: str = Field(min_length=1, max_length=16_384)
    scope: KnowledgeScope
    required: bool = False
    preferred_sources: tuple[str, ...] = ()
    max_results: int = Field(default=10, ge=1, le=1_000)
    max_bytes: int = Field(default=262_144, ge=1, le=67_108_864)
    deadline_seconds: float = Field(default=30.0, gt=0, le=3_600)
    created_at: datetime = Field(default_factory=utc_now)

    @field_validator("created_at")
    @classmethod
    def created_at_is_aware(cls, value: datetime) -> datetime:
        return require_aware(value)

    @model_validator(mode="after")
    def repository_questions_are_revision_bound(self) -> Self:
        if self.knowledge_class in {KnowledgeClass.LITERAL, KnowledgeClass.STRUCTURAL} and (
            self.scope.repository_id is None
        ):
            raise ValueError("literal and structural queries require a repository revision")
        if len(self.preferred_sources) != len(set(self.preferred_sources)):
            raise ValueError("preferred knowledge sources must be unique")
        return self

    @property
    def fingerprint(self) -> str:
        payload = self.model_dump(mode="json", exclude={"query_id", "created_at"})
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


class KnowledgeQueryRecord(KnowledgeModel):
    schema_version: Literal["1.0"] = "1.0"
    query: KnowledgeQuery
    state: KnowledgeQueryState
    revision: int = Field(ge=1)
    bundle_reference: str | None = Field(default=None, pattern=_ARTIFACT_PATTERN)
    limitation_codes: tuple[str, ...] = ()
    started_at: datetime = Field(default_factory=utc_now)
    completed_at: datetime | None = None

    @field_validator("started_at", "completed_at")
    @classmethod
    def times_are_aware(cls, value: datetime | None) -> datetime | None:
        return require_aware(value) if value is not None else None

    @model_validator(mode="after")
    def terminal_state_has_completion(self) -> Self:
        terminal = self.state is not KnowledgeQueryState.RUNNING
        if terminal != (self.completed_at is not None):
            raise ValueError("knowledge query completion must match its state")
        if self.state in {KnowledgeQueryState.COMPLETED, KnowledgeQueryState.DEGRADED} and (
            self.bundle_reference is None
        ):
            raise ValueError("completed knowledge query requires an immutable bundle")
        return self


class KnowledgeSourceAttempt(KnowledgeModel):
    schema_version: Literal["1.0"] = "1.0"
    attempt_id: UUID = Field(default_factory=new_id)
    query_id: UUID
    source_id: str = Field(min_length=1, max_length=256)
    state: KnowledgeAttemptState
    result_count: int = Field(default=0, ge=0)
    latency_ms: float = Field(ge=0)
    error_code: str | None = Field(default=None, min_length=1, max_length=64)
    limitation: str | None = Field(default=None, min_length=1, max_length=2_048)
    provider_schema: str | None = Field(default=None, min_length=1, max_length=128)
    recorded_at: datetime = Field(default_factory=utc_now)

    @field_validator("recorded_at")
    @classmethod
    def recorded_at_is_aware(cls, value: datetime) -> datetime:
        return require_aware(value)


class KnowledgeItem(KnowledgeModel):
    schema_version: Literal["1.0"] = "1.0"
    item_id: UUID = Field(default_factory=new_id)
    knowledge_class: KnowledgeClass
    source_id: str = Field(min_length=1, max_length=256)
    external_record_id: str | None = Field(default=None, min_length=1, max_length=1_024)
    scope: KnowledgeScope
    content_reference: str = Field(pattern=_ARTIFACT_PATTERN)
    content_digest: str = Field(pattern=_DIGEST_PATTERN)
    media_type: str = Field(min_length=1, max_length=256)
    source_locator: str = Field(min_length=1, max_length=4_096)
    source_revision: str | None = Field(default=None, min_length=1, max_length=512)
    staleness: KnowledgeStaleness
    staleness_reason: str | None = Field(default=None, min_length=1, max_length=2_048)
    ranking_basis: str = Field(min_length=1, max_length=256)
    rank: int = Field(ge=1)
    score: float | None = None
    confidence: str | None = Field(default=None, min_length=1, max_length=128)
    inspection_findings: tuple[str, ...] = ()
    content_transformed: bool = False
    retrieved_at: datetime = Field(default_factory=utc_now)
    trust: Literal["untrusted_evidence"] = "untrusted_evidence"

    @field_validator("retrieved_at")
    @classmethod
    def retrieved_at_is_aware(cls, value: datetime) -> datetime:
        return require_aware(value)

    @field_validator(
        "external_record_id",
        "source_locator",
        "source_revision",
        "ranking_basis",
        "confidence",
    )
    @classmethod
    def provider_identity_is_safe(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if unicodedata.normalize("NFC", value) != value or any(ord(char) < 32 for char in value):
            raise ValueError("knowledge provider identity contains unsafe characters")
        return value

    @field_validator("inspection_findings")
    @classmethod
    def inspection_findings_are_safe(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if len(values) != len(set(values)) or any(
            not value or len(value) > 256 or any(ord(char) < 32 for char in value)
            for value in values
        ):
            raise ValueError("knowledge inspection findings must be unique safe identifiers")
        return values

    @model_validator(mode="after")
    def staleness_is_explained(self) -> Self:
        if self.staleness in {KnowledgeStaleness.STALE, KnowledgeStaleness.UNKNOWN} and (
            self.staleness_reason is None
        ):
            raise ValueError("stale or unknown knowledge must explain its limitation")
        if (
            self.staleness is KnowledgeStaleness.CURRENT
            and self.scope.repository_id is not None
            and self.source_revision is None
        ):
            raise ValueError("current repository knowledge requires a source revision")
        return self


class KnowledgeBundle(KnowledgeModel):
    schema_version: Literal["1.0"] = "1.0"
    bundle_id: UUID = Field(default_factory=new_id)
    query: KnowledgeQuery
    items: tuple[KnowledgeItem, ...]
    attempts: tuple[KnowledgeSourceAttempt, ...]
    unavailable_sources: tuple[str, ...] = ()
    limitations: tuple[str, ...] = ()
    degraded: bool = False
    bundle_reference: str = Field(pattern=_ARTIFACT_PATTERN)
    bundle_digest: str = Field(pattern=_DIGEST_PATTERN)
    created_at: datetime = Field(default_factory=utc_now)

    @field_validator("created_at")
    @classmethod
    def created_at_is_aware(cls, value: datetime) -> datetime:
        return require_aware(value)

    @model_validator(mode="after")
    def evidence_matches_query(self) -> Self:
        if any(item.knowledge_class is not self.query.knowledge_class for item in self.items):
            raise ValueError("knowledge bundle cannot mix context classes")
        if any(item.scope.project_id != self.query.scope.project_id for item in self.items):
            raise ValueError("knowledge bundle cannot cross project scope")
        if any(attempt.query_id != self.query.query_id for attempt in self.attempts):
            raise ValueError("knowledge attempt belongs to a different query")
        if self.degraded != bool(self.unavailable_sources or self.limitations):
            raise ValueError("degraded knowledge must expose its sources or limitations")
        return self


class KnowledgeCorpus(KnowledgeModel):
    schema_version: Literal["1.0"] = "1.0"
    corpus_id: UUID = Field(default_factory=new_id)
    project_id: str = Field(min_length=1, max_length=256)
    source_id: str = Field(min_length=1, max_length=256)
    knowledge_class: KnowledgeClass
    external_identity: str = Field(min_length=1, max_length=1_024)
    authorized_repositories: tuple[str, ...] = ()
    indexed_revision: str | None = Field(default=None, min_length=1, max_length=512)
    snapshot_reference: str | None = Field(default=None, pattern=_ARTIFACT_PATTERN)
    state: KnowledgeCorpusState = KnowledgeCorpusState.EMPTY
    revision: int = Field(default=0, ge=0)
    updated_at: datetime = Field(default_factory=utc_now)

    @field_validator("updated_at")
    @classmethod
    def updated_at_is_aware(cls, value: datetime) -> datetime:
        return require_aware(value)

    @model_validator(mode="after")
    def ready_corpus_has_snapshot(self) -> Self:
        if self.state is KnowledgeCorpusState.READY and self.snapshot_reference is None:
            raise ValueError("ready knowledge corpus requires immutable snapshot evidence")
        if len(self.authorized_repositories) != len(set(self.authorized_repositories)):
            raise ValueError("authorized repository identities must be unique")
        return self


class KnowledgeOperation(KnowledgeModel):
    schema_version: Literal["1.0"] = "1.0"
    operation_id: UUID = Field(default_factory=new_id)
    kind: KnowledgeOperationKind
    project_id: str = Field(min_length=1, max_length=256)
    source_id: str = Field(min_length=1, max_length=256)
    corpus_id: UUID | None = None
    state: KnowledgeOperationState = KnowledgeOperationState.QUEUED
    request_fingerprint: str = Field(pattern=_DIGEST_PATTERN)
    provider_operation_id: str | None = Field(default=None, min_length=1, max_length=1_024)
    result_references: tuple[str, ...] = ()
    limitation: str | None = Field(default=None, min_length=1, max_length=2_048)
    revision: int = Field(default=0, ge=0)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    @field_validator("created_at", "updated_at")
    @classmethod
    def operation_times_are_aware(cls, value: datetime) -> datetime:
        return require_aware(value)

    @field_validator("result_references")
    @classmethod
    def result_references_are_artifacts(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if any(not value.startswith("artifact:") for value in values):
            raise ValueError("knowledge operation results must be artifact references")
        return values


class KnowledgeMemoryProposal(KnowledgeModel):
    schema_version: Literal["1.0"] = "1.0"
    proposal_id: UUID = Field(default_factory=new_id)
    project_id: str = Field(min_length=1, max_length=256)
    agent_identity: str = Field(min_length=1, max_length=256)
    run_id: str = Field(min_length=1, max_length=256)
    task_id: str = Field(min_length=1, max_length=256)
    accepted_result_id: str = Field(min_length=1, max_length=256)
    memory: str = Field(min_length=1, max_length=16_384)
    evidence_references: tuple[str, ...] = Field(min_length=1)

    @field_validator("evidence_references")
    @classmethod
    def evidence_is_immutable(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if any(not value.startswith("artifact:") for value in values):
            raise ValueError("memory evidence must use immutable artifact references")
        if len(values) != len(set(values)):
            raise ValueError("memory evidence references must be unique")
        return values


class KnowledgePromotion(KnowledgeModel):
    schema_version: Literal["1.0"] = "1.0"
    promotion_id: UUID = Field(default_factory=new_id)
    item_id: UUID
    source_project_id: str = Field(min_length=1, max_length=256)
    target_scope: str = Field(min_length=1, max_length=512)
    rationale: str = Field(min_length=1, max_length=4_096)
    evidence_references: tuple[str, ...] = Field(min_length=1)
    proposed_by: str = Field(min_length=1, max_length=256)
    disposition: KnowledgePromotionDisposition = KnowledgePromotionDisposition.PROPOSED
    decided_by: str | None = Field(default=None, min_length=1, max_length=256)
    policy_fingerprint: str | None = Field(default=None, pattern=_DIGEST_PATTERN)
    revision: int = Field(default=0, ge=0)
    created_at: datetime = Field(default_factory=utc_now)
    decided_at: datetime | None = None

    @field_validator("created_at", "decided_at")
    @classmethod
    def promotion_times_are_aware(cls, value: datetime | None) -> datetime | None:
        return require_aware(value) if value is not None else None

    @field_validator("evidence_references")
    @classmethod
    def promotion_evidence_is_immutable(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if any(not value.startswith("artifact:") for value in values):
            raise ValueError("promotion evidence must use immutable artifact references")
        return values

    @model_validator(mode="after")
    def decision_fields_match_disposition(self) -> Self:
        proposed = self.disposition is KnowledgePromotionDisposition.PROPOSED
        if proposed != (self.decided_at is None):
            raise ValueError("knowledge promotion decision time does not match disposition")
        if proposed != (self.decided_by is None):
            raise ValueError("knowledge promotion decision identity does not match disposition")
        if proposed != (self.policy_fingerprint is None):
            raise ValueError("knowledge promotion policy does not match disposition")
        return self
