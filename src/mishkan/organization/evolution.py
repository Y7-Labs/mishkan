"""Evidence-based professional evolution contracts."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from mishkan.domain.identity import new_id
from mishkan.domain.time import require_aware, utc_now


class ProfessionalEvolutionModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ProfessionalEvidenceKind(StrEnum):
    PROJECT_KNOWLEDGE = "project_knowledge"
    TOOL_MASTERY = "tool_mastery"
    SKILL_ASSOCIATION = "skill_association"
    DEMONSTRATED_COMPETENCE = "demonstrated_competence"


class ProfessionalEvidenceOutcome(StrEnum):
    DEMONSTRATED = "demonstrated"
    PARTIAL = "partial"
    FAILED = "failed"
    CONTRADICTED = "contradicted"


class ProfessionalEvidenceAttribution(StrEnum):
    INDIVIDUAL_RESULT = "individual_result"
    TEAM_AGGREGATE = "team_aggregate"


class LearningScopeLevel(StrEnum):
    MISSION = "mission"
    PROJECT = "project"
    AGENT = "agent"
    BRANCH = "branch"
    ORGANIZATION = "organization"


_SCOPE_RANK = {
    LearningScopeLevel.MISSION: 0,
    LearningScopeLevel.PROJECT: 1,
    LearningScopeLevel.AGENT: 2,
    LearningScopeLevel.BRANCH: 3,
    LearningScopeLevel.ORGANIZATION: 4,
}


class ProfessionalLearningScope(ProfessionalEvolutionModel):
    level: LearningScopeLevel
    scope_id: str = Field(min_length=1, max_length=256)

    def is_broader_than(self, other: ProfessionalLearningScope) -> bool:
        return _SCOPE_RANK[self.level] > _SCOPE_RANK[other.level]


class ProfessionalEvidenceRecord(ProfessionalEvolutionModel):
    schema_version: Literal["1.0"] = "1.0"
    evidence_id: UUID = Field(default_factory=new_id)
    identity_id: str = Field(min_length=2, max_length=128)
    kind: ProfessionalEvidenceKind
    subject: str = Field(min_length=1, max_length=512)
    scope: ProfessionalLearningScope
    outcome: ProfessionalEvidenceOutcome
    attribution: ProfessionalEvidenceAttribution = ProfessionalEvidenceAttribution.INDIVIDUAL_RESULT
    critical: bool
    source_references: tuple[str, ...] = Field(min_length=1)
    evaluation_references: tuple[str, ...] = Field(min_length=1)
    evaluator_identity: str = Field(min_length=2, max_length=128)
    recorded_by: str = Field(min_length=1, max_length=256)
    observed_at: datetime = Field(default_factory=utc_now)
    fresh_until: datetime
    rationale: str = Field(min_length=3, max_length=8_192)

    @field_validator("observed_at", "fresh_until")
    @classmethod
    def timestamps_are_aware(cls, value: datetime) -> datetime:
        return require_aware(value)

    @model_validator(mode="after")
    def evidence_is_attributable_and_fresh(self) -> ProfessionalEvidenceRecord:
        if self.fresh_until <= self.observed_at:
            raise ValueError("professional evidence freshness must end after observation")
        if (
            self.critical
            and self.outcome is ProfessionalEvidenceOutcome.DEMONSTRATED
            and self.evaluator_identity == self.identity_id
        ):
            raise ValueError("an identity cannot self-certify critical mastery")
        return self


class ProfessionalPromotionRequest(ProfessionalEvolutionModel):
    schema_version: Literal["1.0"] = "1.0"
    request_id: UUID = Field(default_factory=new_id)
    identity_id: str = Field(min_length=2, max_length=128)
    kind: ProfessionalEvidenceKind
    subject: str = Field(min_length=1, max_length=512)
    source_scope: ProfessionalLearningScope
    target_scope: ProfessionalLearningScope
    supporting_evidence_ids: tuple[UUID, ...] = Field(min_length=1)
    requested_by: str = Field(min_length=1, max_length=256)
    rationale: str = Field(min_length=3, max_length=8_192)
    requested_at: datetime = Field(default_factory=utc_now)

    @field_validator("requested_at")
    @classmethod
    def requested_at_is_aware(cls, value: datetime) -> datetime:
        return require_aware(value)

    @model_validator(mode="after")
    def promotion_is_broader_and_unambiguous(self) -> ProfessionalPromotionRequest:
        if not self.target_scope.is_broader_than(self.source_scope):
            raise ValueError("professional promotion target must be broader than its source")
        if len(self.supporting_evidence_ids) != len(set(self.supporting_evidence_ids)):
            raise ValueError("promotion evidence identities must be unique")
        return self


class ProfessionalPromotionDisposition(StrEnum):
    ACCEPTED = "accepted"
    REJECTED = "rejected"


class ProfessionalPromotionDecision(ProfessionalEvolutionModel):
    schema_version: Literal["1.0"] = "1.0"
    decision_id: UUID = Field(default_factory=new_id)
    request: ProfessionalPromotionRequest
    disposition: ProfessionalPromotionDisposition
    revision: int = Field(ge=1)
    previous_decision_id: UUID | None = None
    supporting_evidence_ids: tuple[UUID, ...] = Field(min_length=1)
    contradictory_evidence_ids: tuple[UUID, ...]
    failure_evidence_ids: tuple[UUID, ...]
    decided_by: str = Field(min_length=1, max_length=256)
    policy_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    reason: str = Field(min_length=3, max_length=8_192)
    decided_at: datetime = Field(default_factory=utc_now)

    @field_validator("decided_at")
    @classmethod
    def decided_at_is_aware(cls, value: datetime) -> datetime:
        return require_aware(value)


class ProfessionalCompetenceState(ProfessionalEvolutionModel):
    schema_version: Literal["1.1"] = "1.1"
    identity_id: str
    kind: ProfessionalEvidenceKind
    subject: str
    effective_scope: ProfessionalLearningScope | None
    revision: int = Field(ge=0)
    latest_decision_id: UUID | None
    freshness_evaluated_at: datetime
    supporting_evidence_ids: tuple[UUID, ...]
    fresh_supporting_evidence_ids: tuple[UUID, ...]
    stale_supporting_evidence_ids: tuple[UUID, ...]
    contradictory_evidence_ids: tuple[UUID, ...]
    failure_evidence_ids: tuple[UUID, ...]

    @field_validator("freshness_evaluated_at")
    @classmethod
    def evaluation_time_is_aware(cls, value: datetime) -> datetime:
        return require_aware(value)
