"""Strict public contracts for schedules and durable trigger occurrences."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from mishkan.domain.identity import new_id
from mishkan.domain.time import require_aware, utc_now, validate_timezone
from mishkan.planning.models import MissionTemplateReference


class SchedulingModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ScheduleState(StrEnum):
    ACTIVE = "active"
    PAUSED = "paused"
    COMPLETED = "completed"
    REMOVED = "removed"


class ScheduleTriggerKind(StrEnum):
    DATE = "date"
    CRON = "cron"


class OverlapMode(StrEnum):
    BLOCK = "block"
    ALLOW = "allow"


class OccurrenceSource(StrEnum):
    INTERNAL = "internal"
    MANUAL = "manual"
    EXTERNAL = "external"


class OccurrenceState(StrEnum):
    PENDING = "pending"
    DISPATCHING = "dispatching"
    RETRY_WAITING = "retry_waiting"
    ACCEPTED = "accepted"
    MISFIRED = "misfired"
    OVERLAP_BLOCKED = "overlap_blocked"
    FAILED = "failed"
    CANCELLED = "cancelled"
    UNCERTAIN = "uncertain"


class DateTriggerSpec(SchedulingModel):
    kind: Literal[ScheduleTriggerKind.DATE] = ScheduleTriggerKind.DATE
    run_at: datetime

    @field_validator("run_at")
    @classmethod
    def run_at_is_aware(cls, value: datetime) -> datetime:
        return require_aware(value)


class CronTriggerSpec(SchedulingModel):
    kind: Literal[ScheduleTriggerKind.CRON] = ScheduleTriggerKind.CRON
    expression: str = Field(min_length=9, max_length=256)
    timezone: str

    @field_validator("expression")
    @classmethod
    def expression_has_five_fields(cls, value: str) -> str:
        normalized = " ".join(value.split())
        if len(normalized.split(" ")) != 5:
            raise ValueError("cron trigger requires exactly five fields")
        return normalized

    @field_validator("timezone")
    @classmethod
    def timezone_is_iana(cls, value: str) -> str:
        return validate_timezone(value)


ScheduleTrigger = Annotated[DateTriggerSpec | CronTriggerSpec, Field(discriminator="kind")]


class ScheduleMissionRequest(SchedulingModel):
    """Free-form mission request; it is not a static workflow or outcome catalogue."""

    schema_version: Literal["1.0"] = "1.0"
    project_id: str = Field(min_length=1, max_length=256)
    workspace_id: str | None = Field(default=None, min_length=1, max_length=256)
    objective: str = Field(min_length=3, max_length=8_192)
    outcome_id: str | None = Field(default=None, min_length=1, max_length=160)
    template_reference: MissionTemplateReference | None = None
    inputs: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def prospective_workspace_has_an_outcome(self) -> Self:
        if self.workspace_id is None and self.outcome_id is not None:
            raise ValueError("repository mission cannot declare a prospective outcome id")
        if self.workspace_id is not None and self.outcome_id is None:
            raise ValueError("prospective workspace mission requires an outcome id")
        if len(self.inputs) > 256:
            raise ValueError("scheduled mission input count exceeds 256")
        if any(
            not key or len(key) > 256 or len(value) > 16_384 for key, value in self.inputs.items()
        ):
            raise ValueError("scheduled mission inputs exceed their public bounds")
        return self

    @property
    def target_key(self) -> str:
        kind = "workspace" if self.workspace_id is not None else "project"
        identity = self.workspace_id or self.project_id
        digest = hashlib.sha256(f"{kind}:{identity}".encode()).hexdigest()
        return f"{kind}:sha256:{digest}"

    @property
    def rendered_objective(self) -> str:
        if self.template_reference is None and not self.inputs:
            return self.objective
        context = {
            "template_reference": (
                self.template_reference.model_dump(mode="json")
                if self.template_reference is not None
                else None
            ),
            "inputs": self.inputs,
        }
        encoded = json.dumps(context, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        return f"{self.objective}\n\nMISHKAN scheduled mission context (data):\n{encoded}"


class ScheduleCreateRequest(SchedulingModel):
    schema_version: Literal["1.0"] = "1.0"
    schedule_id: UUID = Field(default_factory=new_id)
    mission: ScheduleMissionRequest
    trigger: ScheduleTrigger
    overlap_mode: OverlapMode = OverlapMode.BLOCK


class ScheduleRecord(SchedulingModel):
    schema_version: Literal["1.0"] = "1.0"
    schedule_id: UUID
    mission: ScheduleMissionRequest
    trigger: ScheduleTrigger
    state: ScheduleState
    overlap_key: str = Field(min_length=1, max_length=256)
    overlap_mode: OverlapMode
    created_by: str = Field(min_length=1, max_length=256)
    preauthorization_policy_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    next_fire_at: datetime | None = None
    last_evaluated_at: datetime | None = None
    revision: int = Field(ge=1)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    @field_validator("next_fire_at", "last_evaluated_at", "created_at", "updated_at")
    @classmethod
    def timestamps_are_aware(cls, value: datetime | None) -> datetime | None:
        return require_aware(value) if value is not None else None

    @model_validator(mode="after")
    def overlap_key_matches_target(self) -> Self:
        if self.overlap_key != self.mission.target_key:
            raise ValueError("schedule overlap key must derive from the mission target")
        if self.state is ScheduleState.COMPLETED and self.next_fire_at is not None:
            raise ValueError("completed schedule cannot retain a next fire time")
        return self


class ScheduleOccurrence(SchedulingModel):
    schema_version: Literal["1.0"] = "1.0"
    occurrence_id: UUID = Field(default_factory=new_id)
    occurrence_key: str = Field(min_length=1, max_length=256)
    schedule_id: UUID
    schedule_revision: int = Field(ge=1)
    source: OccurrenceSource
    state: OccurrenceState
    scheduled_for: datetime
    attempt_count: int = Field(default=0, ge=0)
    run_command_id: UUID | None = None
    run_id: str | None = Field(default=None, min_length=1, max_length=256)
    next_attempt_at: datetime | None = None
    error_code: str | None = Field(default=None, min_length=1, max_length=64)
    error_message: str | None = Field(default=None, min_length=1, max_length=4_096)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    @field_validator("scheduled_for", "next_attempt_at", "created_at", "updated_at")
    @classmethod
    def times_are_aware(cls, value: datetime | None) -> datetime | None:
        return require_aware(value) if value is not None else None

    @model_validator(mode="after")
    def state_fields_are_coherent(self) -> Self:
        if self.state is OccurrenceState.ACCEPTED and (
            self.run_command_id is None or self.run_id is None
        ):
            raise ValueError("accepted occurrence requires run command and run identity")
        if self.state is OccurrenceState.RETRY_WAITING and self.next_attempt_at is None:
            raise ValueError("retrying occurrence requires the next attempt time")
        if self.state is not OccurrenceState.RETRY_WAITING and self.next_attempt_at is not None:
            raise ValueError("only retrying occurrence may retain a next attempt time")
        return self
