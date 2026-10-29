"""Normative durable run/task lifecycle values and evidence envelopes."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from mishkan.domain.time import require_aware
from mishkan.planning.models import InitializationResult, ReviewDecision


class RunState(StrEnum):
    PLANNING = "planning"
    AWAITING_APPROVAL = "awaiting_approval"
    QUEUED = "queued"
    RUNNING = "running"
    BLOCKED = "blocked"
    CANCELLING = "cancelling"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class TaskState(StrEnum):
    PENDING = "pending"
    ELIGIBLE = "eligible"
    LEASED = "leased"
    EXECUTING = "executing"
    VALIDATING = "validating"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    FAILED = "failed"
    CANCELLED = "cancelled"


class TaskReviewRejection(BaseModel):
    """Durable evidence for one independently rejected task-result proposal."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["1.0"] = "1.0"
    run_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    task_attempt: int = Field(ge=1)
    review_sequence: int = Field(ge=1)
    result: InitializationResult
    review: ReviewDecision
    recorded_at: datetime

    @field_validator("review")
    @classmethod
    def review_is_a_rejection(cls, value: ReviewDecision) -> ReviewDecision:
        if value.verdict != "rejected":
            raise ValueError("task review rejection requires a rejected review")
        return value

    @field_validator("recorded_at")
    @classmethod
    def recorded_at_is_aware(cls, value: datetime) -> datetime:
        return require_aware(value)

    @model_validator(mode="after")
    def evidence_identifies_one_task(self) -> TaskReviewRejection:
        if self.result.task_id != self.task_id or self.review.task_id != self.task_id:
            raise ValueError("task review rejection evidence identifies different tasks")
        return self
