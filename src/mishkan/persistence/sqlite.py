"""SQLAlchemy repository with SQLite/WAL and transactional outbox semantics."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol

from sqlalchemy import (
    Boolean,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    create_engine,
    event,
    select,
)
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, relationship

from mishkan.domain.errors import ErrorCode, MishkanError
from mishkan.domain.identity import new_id
from mishkan.domain.time import utc_now
from mishkan.planning.models import (
    AcceptedPlan,
    InitializationResult,
    PlanExecutionContext,
    PlanTask,
    ReviewDecision,
)
from mishkan.repository.models import (
    DiscoverySnapshot,
    RepositoryEstablishment,
)
from mishkan.runtime import RunState, TaskReviewRejection, TaskState
from mishkan.tools.execution import EffectSettlement
from mishkan.tools.gateway_models import AuditEvent, CallStatus, ToolResultEnvelope

DEFAULT_BUSY_TIMEOUT_MS = 5_000


def create_local_engine(
    database_path: Path,
    *,
    busy_timeout_ms: int = DEFAULT_BUSY_TIMEOUT_MS,
) -> Engine:
    """Create one WAL SQLite engine from the effective public lock-wait setting."""
    if busy_timeout_ms < 1:
        raise MishkanError(ErrorCode.CONFIGURATION, "SQLite busy timeout must be positive")
    engine = create_engine(
        f"sqlite:///{database_path.resolve()}",
        connect_args={"timeout": busy_timeout_ms / 1_000},
    )

    def configure(dbapi_connection: Any, _connection_record: Any) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute(f"PRAGMA busy_timeout={busy_timeout_ms}")
        cursor.close()

    event.listen(engine, "connect", configure)
    return engine


class Base(DeclarativeBase):
    pass


class RunRow(Base):
    __tablename__ = "runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    resume_key: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    context_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    context_id: Mapped[str] = mapped_column(String(256), nullable=False)
    context_revision: Mapped[str] = mapped_column(String(128), nullable=False)
    repository_id: Mapped[str | None] = mapped_column(String(64))
    repository_revision: Mapped[str | None] = mapped_column(String(128))
    discovery_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    objective: Mapped[str] = mapped_column(Text, nullable=False)
    outcome_id: Mapped[str] = mapped_column(String(160), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cancellation_requested: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[str] = mapped_column(String(40), nullable=False)
    updated_at: Mapped[str] = mapped_column(String(40), nullable=False)
    plan: Mapped[PlanRow | None] = relationship(back_populates="run", uselist=False)


class RepositoryEstablishmentRow(Base):
    __tablename__ = "repository_establishments"

    establishment_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"), unique=True, nullable=False)
    prospective_workspace_id: Mapped[str] = mapped_column(String(256), nullable=False)
    discovery_revision: Mapped[str] = mapped_column(String(128), nullable=False)
    repository_id: Mapped[str] = mapped_column(String(64), nullable=False)
    repository_revision: Mapped[str] = mapped_column(String(128), nullable=False)
    payload: Mapped[str] = mapped_column(Text, nullable=False)
    established_at: Mapped[str] = mapped_column(String(40), nullable=False)


class PlanRow(Base):
    __tablename__ = "plans"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"), unique=True, nullable=False)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[str] = mapped_column(Text, nullable=False)
    accepted_at: Mapped[str] = mapped_column(String(40), nullable=False)
    run: Mapped[RunRow] = relationship(back_populates="plan")


class TaskRow(Base):
    __tablename__ = "tasks"
    __table_args__ = (UniqueConstraint("run_id", "task_key"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"), nullable=False)
    task_key: Mapped[str] = mapped_column(String(64), nullable=False)
    position: Mapped[int] = mapped_column(nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    contract: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[str] = mapped_column(String(40), nullable=False)


class PlannedToolCallRow(Base):
    __tablename__ = "planned_tool_calls"
    __table_args__ = (
        UniqueConstraint(
            "run_id",
            "task_attempt_id",
            "planned_call_id",
            name="uq_planned_tool_call_identity",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"), nullable=False, index=True)
    task_attempt_id: Mapped[str] = mapped_column(String(128), nullable=False)
    planned_call_id: Mapped[str] = mapped_column(String(64), nullable=False)
    request_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    tool_id: Mapped[str] = mapped_column(String(128), nullable=False)
    tool_version: Mapped[str] = mapped_column(String(64), nullable=False)
    effect_class: Mapped[str] = mapped_column(String(64), nullable=False)
    declared_effects_payload: Mapped[str] = mapped_column(Text, nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    effect_settlement: Mapped[str | None] = mapped_column(String(32))
    result_payload: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(String(40), nullable=False)
    updated_at: Mapped[str] = mapped_column(String(40), nullable=False)


class ToolRegistryEntryRow(Base):
    __tablename__ = "tool_registry_entries"

    record_id: Mapped[str] = mapped_column(String(36), unique=True, nullable=False)
    entry_kind: Mapped[str] = mapped_column(String(16), primary_key=True)
    identity: Mapped[str] = mapped_column(String(128), primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False)
    removed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    precedence: Mapped[int] = mapped_column(Integer, nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    definition_payload: Mapped[str | None] = mapped_column(Text)
    definition_fingerprint: Mapped[str | None] = mapped_column(String(64))
    updated_at: Mapped[str] = mapped_column(String(40), nullable=False)


class ResultRow(Base):
    __tablename__ = "accepted_results"
    __table_args__ = (UniqueConstraint("run_id", "task_key"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"), nullable=False)
    task_key: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[str] = mapped_column(Text, nullable=False)
    accepted_at: Mapped[str] = mapped_column(String(40), nullable=False)


class AcceptanceRow(Base):
    __tablename__ = "task_acceptances"
    __table_args__ = (UniqueConstraint("run_id", "task_key"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"), nullable=False)
    task_key: Mapped[str] = mapped_column(String(64), nullable=False)
    result_id: Mapped[str] = mapped_column(ForeignKey("accepted_results.id"), nullable=False)
    review_payload: Mapped[str] = mapped_column(Text, nullable=False)
    accepted_at: Mapped[str] = mapped_column(String(40), nullable=False)


class ReviewRejectionRow(Base):
    __tablename__ = "task_review_rejections"
    __table_args__ = (UniqueConstraint("run_id", "task_key", "task_attempt", "review_sequence"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"), nullable=False)
    task_key: Mapped[str] = mapped_column(String(64), nullable=False)
    task_attempt: Mapped[int] = mapped_column(Integer, nullable=False)
    review_sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    result_payload: Mapped[str] = mapped_column(Text, nullable=False)
    review_payload: Mapped[str] = mapped_column(Text, nullable=False)
    recorded_at: Mapped[str] = mapped_column(String(40), nullable=False)


class OutboxRow(Base):
    __tablename__ = "event_outbox"
    __table_args__ = {"sqlite_autoincrement": True}  # noqa: RUF012

    cursor: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    id: Mapped[str] = mapped_column(String(36), unique=True, nullable=False)
    schema_version: Mapped[str] = mapped_column(String(16), nullable=False, default="1.0")
    aggregate_id: Mapped[str] = mapped_column(String(256), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(64), nullable=False)
    run_id: Mapped[str | None] = mapped_column(String(256))
    task_id: Mapped[str | None] = mapped_column(String(256))
    identity_id: Mapped[str | None] = mapped_column(String(256))
    team_id: Mapped[str | None] = mapped_column(String(256))
    security_relevant: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    event_type: Mapped[str] = mapped_column(String(120), nullable=False)
    source: Mapped[str] = mapped_column(String(128), nullable=False)
    payload: Mapped[str] = mapped_column(Text, nullable=False)
    occurred_at: Mapped[str] = mapped_column(String(40), nullable=False)
    command_id: Mapped[str | None] = mapped_column(String(36))
    correlation_id: Mapped[str | None] = mapped_column(String(36))
    causation_id: Mapped[str | None] = mapped_column(String(36))
    sensitivity: Mapped[str] = mapped_column(String(32), nullable=False, default="internal")
    published_at: Mapped[str | None] = mapped_column(String(40))


class CommandRow(Base):
    __tablename__ = "application_commands"

    command_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    schema_version: Mapped[str] = mapped_column(String(16), nullable=False)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    command_type: Mapped[str] = mapped_column(String(128), nullable=False)
    actor_id: Mapped[str] = mapped_column(String(256), nullable=False)
    target_type: Mapped[str] = mapped_column(String(64), nullable=False)
    target_id: Mapped[str | None] = mapped_column(String(256))
    expected_revision: Mapped[int | None] = mapped_column(Integer)
    issued_at: Mapped[str] = mapped_column(String(40), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    result_payload: Mapped[str] = mapped_column(Text, nullable=False)
    event_cursor: Mapped[int | None] = mapped_column(Integer)
    completed_at: Mapped[str] = mapped_column(String(40), nullable=False)


class EventHoldRow(Base):
    __tablename__ = "event_holds"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    scope: Mapped[str] = mapped_column(String(16), nullable=False)
    scope_id: Mapped[str | None] = mapped_column(String(256))
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    actor_id: Mapped[str] = mapped_column(String(256), nullable=False)
    created_at: Mapped[str] = mapped_column(String(40), nullable=False)
    released_at: Mapped[str | None] = mapped_column(String(40))


class EventRetentionPlanRow(Base):
    __tablename__ = "event_retention_plans"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    policy_payload: Mapped[str] = mapped_column(Text, nullable=False)
    policy_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    cutoff: Mapped[str] = mapped_column(String(40), nullable=False)
    candidates_payload: Mapped[str] = mapped_column(Text, nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False)
    deleted_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[str] = mapped_column(String(40), nullable=False)
    applied_at: Mapped[str | None] = mapped_column(String(40))


class AggregateRevisionRow(Base):
    __tablename__ = "aggregate_revisions"

    entity_type: Mapped[str] = mapped_column(String(64), primary_key=True)
    entity_id: Mapped[str] = mapped_column(String(256), primary_key=True)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    updated_at: Mapped[str] = mapped_column(String(40), nullable=False)


class ArtifactRow(Base):
    __tablename__ = "artifacts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    digest: Mapped[str] = mapped_column(String(71), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    media_type: Mapped[str] = mapped_column(String(255), nullable=False)
    lifecycle: Mapped[str] = mapped_column(String(32), nullable=False)
    storage_ref: Mapped[str] = mapped_column(String(128), nullable=False)
    manifest_payload: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[str] = mapped_column(String(40), nullable=False)
    tombstoned_at: Mapped[str | None] = mapped_column(String(40))


class ArtifactUploadRow(Base):
    __tablename__ = "artifact_uploads"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    expected_digest: Mapped[str] = mapped_column(String(71), nullable=False)
    expected_size: Mapped[int] = mapped_column(Integer, nullable=False)
    media_type: Mapped[str] = mapped_column(String(255), nullable=False)
    offset: Mapped[int] = mapped_column(Integer, nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    artifact_id: Mapped[str | None] = mapped_column(ForeignKey("artifacts.id"))
    staging_path: Mapped[str] = mapped_column(Text, nullable=False)
    metadata_payload: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[str] = mapped_column(String(40), nullable=False)
    updated_at: Mapped[str] = mapped_column(String(40), nullable=False)


class ArtifactCollectionRow(Base):
    __tablename__ = "artifact_collections"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    entries_payload: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[str] = mapped_column(String(40), nullable=False)


class ArtifactReferenceRow(Base):
    __tablename__ = "artifact_references"

    scope: Mapped[str] = mapped_column(String(256), primary_key=True)
    name: Mapped[str] = mapped_column(String(256), primary_key=True)
    record_id: Mapped[str] = mapped_column(String(36), nullable=False, unique=True)
    artifact_id: Mapped[str] = mapped_column(ForeignKey("artifacts.id"), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    prior_artifact_id: Mapped[str | None] = mapped_column(String(36))
    prior_revision: Mapped[int | None] = mapped_column(Integer)
    updated_at: Mapped[str] = mapped_column(String(40), nullable=False)


class ArtifactHoldRow(Base):
    __tablename__ = "artifact_holds"

    artifact_id: Mapped[str] = mapped_column(ForeignKey("artifacts.id"), primary_key=True)
    record_id: Mapped[str] = mapped_column(String(36), nullable=False, unique=True)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[str] = mapped_column(String(40), nullable=False)


class ArtifactPinRow(Base):
    __tablename__ = "artifact_pins"

    artifact_id: Mapped[str] = mapped_column(ForeignKey("artifacts.id"), primary_key=True)
    record_id: Mapped[str] = mapped_column(String(36), nullable=False, unique=True)
    created_at: Mapped[str] = mapped_column(String(40), nullable=False)


class ArtifactGCPlanRow(Base):
    __tablename__ = "artifact_gc_plans"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    watermark: Mapped[str] = mapped_column(String(40), nullable=False)
    candidates_payload: Mapped[str] = mapped_column(Text, nullable=False)
    applied_at: Mapped[str | None] = mapped_column(String(40))
    created_at: Mapped[str] = mapped_column(String(40), nullable=False)


class ArtifactReconciliationPlanRow(Base):
    __tablename__ = "artifact_reconciliation_plans"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    payload: Mapped[str] = mapped_column(Text, nullable=False)
    applied_at: Mapped[str | None] = mapped_column(String(40))
    created_at: Mapped[str] = mapped_column(String(40), nullable=False)


class ChangeSetRow(Base):
    __tablename__ = "change_sets"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    workspace: Mapped[str] = mapped_column(Text, nullable=False)
    scope: Mapped[str] = mapped_column(String(256), nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    operation_index: Mapped[int] = mapped_column(Integer, nullable=False)
    payload: Mapped[str] = mapped_column(Text, nullable=False)
    diff_reference: Mapped[str | None] = mapped_column(String(64))
    validation_payload: Mapped[str | None] = mapped_column(Text)
    reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(String(40), nullable=False)
    updated_at: Mapped[str] = mapped_column(String(40), nullable=False)


class WebCacheRow(Base):
    __tablename__ = "web_cache_entries"

    key: Mapped[str] = mapped_column(String(71), primary_key=True)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    payload: Mapped[str] = mapped_column(Text, nullable=False)
    stored_at: Mapped[str] = mapped_column(String(40), nullable=False)
    fresh_until: Mapped[str] = mapped_column(String(40), nullable=False)


class BrowserSessionRow(Base):
    __tablename__ = "browser_sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    owner_identity: Mapped[str] = mapped_column(String(256), nullable=False)
    run_id: Mapped[str] = mapped_column(String(36), nullable=False)
    task_attempt_id: Mapped[str] = mapped_column(String(128), nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    payload: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[str] = mapped_column(String(40), nullable=False)
    updated_at: Mapped[str] = mapped_column(String(40), nullable=False)


class BrowserObservationRow(Base):
    __tablename__ = "browser_observations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("browser_sessions.id"), nullable=False)
    page_id: Mapped[str] = mapped_column(String(128), nullable=False)
    session_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    payload: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[str] = mapped_column(String(40), nullable=False)
    expires_at: Mapped[str] = mapped_column(String(40), nullable=False)


class BrowserActionRow(Base):
    __tablename__ = "browser_actions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    idempotency_key: Mapped[str] = mapped_column(String(36), unique=True, nullable=False)
    session_id: Mapped[str] = mapped_column(ForeignKey("browser_sessions.id"), nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    payload: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[str] = mapped_column(String(40), nullable=False)
    completed_at: Mapped[str | None] = mapped_column(String(40))


class McpConnectionRow(Base):
    __tablename__ = "mcp_connections"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    connection_id: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    direction: Mapped[str] = mapped_column(String(16), nullable=False)
    transport: Mapped[str] = mapped_column(String(32), nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    schema_fingerprint: Mapped[str | None] = mapped_column(String(64))
    payload: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[str] = mapped_column(String(40), nullable=False)
    updated_at: Mapped[str] = mapped_column(String(40), nullable=False)


class McpPrimitiveRow(Base):
    __tablename__ = "mcp_primitives"

    connection_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    kind: Mapped[str] = mapped_column(String(16), primary_key=True)
    name: Mapped[str] = mapped_column(String(256), primary_key=True)
    schema_hash: Mapped[str] = mapped_column(String(71), nullable=False)
    payload: Mapped[str] = mapped_column(Text, nullable=False)
    discovered_at: Mapped[str] = mapped_column(String(40), nullable=False)


class McpCallRow(Base):
    __tablename__ = "mcp_calls"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    idempotency_key: Mapped[str] = mapped_column(String(36), unique=True, nullable=False)
    request_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    connection_id: Mapped[str] = mapped_column(String(128), nullable=False)
    primitive_name: Mapped[str] = mapped_column(String(256), nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    request_payload: Mapped[str] = mapped_column(Text, nullable=False)
    result_payload: Mapped[str | None] = mapped_column(Text)
    remote_task_id: Mapped[str | None] = mapped_column(String(256))
    created_at: Mapped[str] = mapped_column(String(40), nullable=False)
    updated_at: Mapped[str] = mapped_column(String(40), nullable=False)


class McpProgressRow(Base):
    __tablename__ = "mcp_progress"

    request_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    cursor: Mapped[int] = mapped_column(Integer, primary_key=True)
    payload: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[str] = mapped_column(String(40), nullable=False)


class ChangeOperationRow(Base):
    __tablename__ = "change_operations"

    change_set_id: Mapped[str] = mapped_column(ForeignKey("change_sets.id"), primary_key=True)
    position: Mapped[int] = mapped_column(Integer, primary_key=True)
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    before_token: Mapped[str | None] = mapped_column(Text)
    preimage_reference: Mapped[str | None] = mapped_column(String(64))
    expected_after_token: Mapped[str | None] = mapped_column(Text)
    actual_after_token: Mapped[str | None] = mapped_column(Text)
    payload: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[str] = mapped_column(String(40), nullable=False)


class ExecutionSessionRow(Base):
    __tablename__ = "execution_sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    mode: Mapped[str] = mapped_column(String(16), nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    owner: Mapped[str] = mapped_column(String(256), nullable=False)
    run_id: Mapped[str] = mapped_column(String(36), nullable=False)
    task_id: Mapped[str] = mapped_column(String(256), nullable=False)
    workspace: Mapped[str] = mapped_column(Text, nullable=False)
    profile: Mapped[str] = mapped_column(String(128), nullable=False)
    request_payload: Mapped[str] = mapped_column(Text, nullable=False)
    pid: Mapped[int | None] = mapped_column(Integer)
    process_group_id: Mapped[int | None] = mapped_column(Integer)
    process_create_time: Mapped[float | None] = mapped_column(Float)
    stdout_spool: Mapped[str] = mapped_column(Text, nullable=False)
    stderr_spool: Mapped[str] = mapped_column(Text, nullable=False)
    stdout_cursor: Mapped[int] = mapped_column(Integer, nullable=False)
    stderr_cursor: Mapped[int] = mapped_column(Integer, nullable=False)
    exit_code: Mapped[int | None] = mapped_column(Integer)
    signal: Mapped[int | None] = mapped_column(Integer)
    stdout_artifact_reference: Mapped[str | None] = mapped_column(String(64))
    stderr_artifact_reference: Mapped[str | None] = mapped_column(String(64))
    before_state_payload: Mapped[str] = mapped_column(Text, nullable=False)
    effect_evidence_payload: Mapped[str] = mapped_column(Text, nullable=False)
    observed_effects_payload: Mapped[str] = mapped_column(Text, nullable=False)
    produced_artifacts_payload: Mapped[str] = mapped_column(Text, nullable=False)
    effect_settlement: Mapped[str | None] = mapped_column(String(32))
    termination_cause: Mapped[str | None] = mapped_column(String(64))
    retryable: Mapped[bool] = mapped_column(Boolean, nullable=False)
    error: Mapped[str | None] = mapped_column(Text)
    cancellation_requested: Mapped[bool] = mapped_column(Boolean, nullable=False)
    deadline: Mapped[str] = mapped_column(String(40), nullable=False)
    started_at: Mapped[str] = mapped_column(String(40), nullable=False)
    finished_at: Mapped[str | None] = mapped_column(String(40))
    created_at: Mapped[str] = mapped_column(String(40), nullable=False)
    updated_at: Mapped[str] = mapped_column(String(40), nullable=False)


class SkillUsageRow(Base):
    __tablename__ = "skill_usage"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    task_id: Mapped[str] = mapped_column(String(256), nullable=False)
    task_class: Mapped[str] = mapped_column(String(256), nullable=False)
    consuming_identity: Mapped[str] = mapped_column(String(256), nullable=False)
    requested_skill: Mapped[str] = mapped_column(String(64), nullable=False)
    skill_version: Mapped[str | None] = mapped_column(String(128))
    package_fingerprint: Mapped[str | None] = mapped_column(String(71))
    outcome: Mapped[str] = mapped_column(String(16), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    policy_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    evidence_payload: Mapped[str] = mapped_column(Text, nullable=False)
    recorded_at: Mapped[str] = mapped_column(String(40), nullable=False)


class SkillVersionRow(Base):
    __tablename__ = "skill_versions"
    __table_args__ = (UniqueConstraint("skill_name", "skill_version"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    skill_name: Mapped[str] = mapped_column(String(64), nullable=False)
    skill_version: Mapped[str] = mapped_column(String(128), nullable=False)
    package_fingerprint: Mapped[str] = mapped_column(String(71), nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    payload: Mapped[str] = mapped_column(Text, nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[str] = mapped_column(String(40), nullable=False)
    updated_at: Mapped[str] = mapped_column(String(40), nullable=False)


class SkillActiveVersionRow(Base):
    __tablename__ = "skill_active_versions"

    skill_name: Mapped[str] = mapped_column(String(64), primary_key=True)
    version_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("skill_versions.id"), nullable=False
    )
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    updated_at: Mapped[str] = mapped_column(String(40), nullable=False)


class SkillLifecycleDecisionRow(Base):
    __tablename__ = "skill_lifecycle_decisions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    version_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("skill_versions.id"), nullable=False
    )
    disposition: Mapped[str] = mapped_column(String(32), nullable=False)
    policy_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[str] = mapped_column(Text, nullable=False)
    decided_at: Mapped[str] = mapped_column(String(40), nullable=False)


class SkillLearningRow(Base):
    __tablename__ = "skill_learning"

    request_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    task_id: Mapped[str] = mapped_column(String(256), nullable=False)
    task_class: Mapped[str] = mapped_column(String(256), nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    payload: Mapped[str] = mapped_column(Text, nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[str] = mapped_column(String(40), nullable=False)
    updated_at: Mapped[str] = mapped_column(String(40), nullable=False)


class EnvironmentObservationRow(Base):
    __tablename__ = "environment_observations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    context_id: Mapped[str] = mapped_column(String(256), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[str] = mapped_column(Text, nullable=False)
    observed_at: Mapped[str] = mapped_column(String(40), nullable=False)


class EnvironmentBindingRow(Base):
    __tablename__ = "environment_bindings"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    request_id: Mapped[str] = mapped_column(String(36), unique=True, nullable=False)
    observation_id: Mapped[str] = mapped_column(String(36), nullable=False)
    context_id: Mapped[str] = mapped_column(String(256), nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    payload: Mapped[str] = mapped_column(Text, nullable=False)
    resolved_at: Mapped[str] = mapped_column(String(40), nullable=False)


class EnvironmentDescriptorSetRow(Base):
    __tablename__ = "environment_descriptor_sets"

    descriptor_set_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    binding_id: Mapped[str] = mapped_column(String(36), nullable=False)
    payload: Mapped[str] = mapped_column(Text, nullable=False)
    recorded_at: Mapped[str] = mapped_column(String(40), nullable=False)


class EnvironmentAttemptRow(Base):
    __tablename__ = "environment_attempts"

    attempt_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    binding_id: Mapped[str] = mapped_column(String(36), nullable=False)
    payload: Mapped[str] = mapped_column(Text, nullable=False)
    recorded_at: Mapped[str] = mapped_column(String(40), nullable=False)


class EnvironmentVerificationRow(Base):
    __tablename__ = "environment_verifications"

    verification_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    binding_id: Mapped[str] = mapped_column(String(36), nullable=False)
    payload: Mapped[str] = mapped_column(Text, nullable=False)
    recorded_at: Mapped[str] = mapped_column(String(40), nullable=False)


class EnvironmentInvalidationRow(Base):
    __tablename__ = "environment_invalidations"

    invalidation_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    binding_id: Mapped[str] = mapped_column(String(36), unique=True, nullable=False)
    payload: Mapped[str] = mapped_column(Text, nullable=False)
    recorded_at: Mapped[str] = mapped_column(String(40), nullable=False)


class OrganizationRosterRow(Base):
    __tablename__ = "organization_rosters"

    organization_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    organization_version: Mapped[str] = mapped_column(String(64), primary_key=True)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[str] = mapped_column(Text, nullable=False)
    recorded_at: Mapped[str] = mapped_column(String(40), nullable=False)


class MissionRow(Base):
    __tablename__ = "missions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    organization_id: Mapped[str] = mapped_column(String(128), nullable=False)
    organization_version: Mapped[str] = mapped_column(String(64), nullable=False)
    current_brief_version: Mapped[int | None] = mapped_column(Integer)
    current_crew_version: Mapped[int | None] = mapped_column(Integer)
    current_environment_plan_version: Mapped[int | None] = mapped_column(Integer)
    payload: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[str] = mapped_column(String(40), nullable=False)
    updated_at: Mapped[str] = mapped_column(String(40), nullable=False)


class MissionBriefRow(Base):
    __tablename__ = "mission_briefs"
    __table_args__ = (UniqueConstraint("mission_id", "version"),)

    brief_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    mission_id: Mapped[str] = mapped_column(ForeignKey("missions.id"), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[str] = mapped_column(String(40), nullable=False)


class MissionCrewRow(Base):
    __tablename__ = "mission_crews"
    __table_args__ = (UniqueConstraint("mission_id", "version"),)

    crew_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    mission_id: Mapped[str] = mapped_column(ForeignKey("missions.id"), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    brief_version: Mapped[int] = mapped_column(Integer, nullable=False)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[str] = mapped_column(String(40), nullable=False)


class ConversationChannelRow(Base):
    __tablename__ = "conversation_channels"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    channel_class: Mapped[str] = mapped_column(String(32), nullable=False)
    mission_id: Mapped[str | None] = mapped_column(ForeignKey("missions.id"))
    branch_id: Mapped[str | None] = mapped_column(String(128))
    payload: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[str] = mapped_column(String(40), nullable=False)


class ConversationMessageRow(Base):
    __tablename__ = "conversation_messages"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    conversation_id: Mapped[str] = mapped_column(
        ForeignKey("conversation_channels.id"), nullable=False
    )
    author_identity: Mapped[str] = mapped_column(String(256), nullable=False)
    payload: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[str] = mapped_column(String(40), nullable=False)


class MissionDecisionRow(Base):
    __tablename__ = "mission_decisions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    mission_id: Mapped[str] = mapped_column(ForeignKey("missions.id"), nullable=False)
    conversation_id: Mapped[str] = mapped_column(
        ForeignKey("conversation_channels.id"), nullable=False
    )
    supersedes_decision_id: Mapped[str | None] = mapped_column(String(36))
    payload: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[str] = mapped_column(String(40), nullable=False)


class MissionEscalationRow(Base):
    __tablename__ = "mission_escalations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    mission_id: Mapped[str] = mapped_column(ForeignKey("missions.id"), nullable=False)
    conversation_id: Mapped[str] = mapped_column(
        ForeignKey("conversation_channels.id"), nullable=False
    )
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    payload: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[str] = mapped_column(String(40), nullable=False)
    updated_at: Mapped[str] = mapped_column(String(40), nullable=False)


class MissionInterventionRow(Base):
    __tablename__ = "mission_interventions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    mission_id: Mapped[str] = mapped_column(ForeignKey("missions.id"), nullable=False)
    conversation_id: Mapped[str] = mapped_column(
        ForeignKey("conversation_channels.id"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[str] = mapped_column(String(40), nullable=False)


class MissionAssignmentRow(Base):
    __tablename__ = "mission_assignments"
    __table_args__ = (UniqueConstraint("mission_id", "task_id", "assignment_revision"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    mission_id: Mapped[str] = mapped_column(ForeignKey("missions.id"), nullable=False)
    task_id: Mapped[str] = mapped_column(String(256), nullable=False)
    assignment_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    accountable_owner: Mapped[str] = mapped_column(String(128), nullable=False)
    payload: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[str] = mapped_column(String(40), nullable=False)


class MissionRunBindingRow(Base):
    __tablename__ = "mission_run_bindings"
    __table_args__ = (UniqueConstraint("mission_id", "binding_key", "binding_revision"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    mission_id: Mapped[str] = mapped_column(ForeignKey("missions.id"), nullable=False)
    binding_key: Mapped[str] = mapped_column(String(128), nullable=False)
    binding_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"), nullable=False)
    acceptance: Mapped[str] = mapped_column(String(32), nullable=False)
    payload: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[str] = mapped_column(String(40), nullable=False)


class MissionRunReportRow(Base):
    __tablename__ = "mission_run_reports"
    __table_args__ = (UniqueConstraint("mission_id", "run_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    mission_id: Mapped[str] = mapped_column(ForeignKey("missions.id"), nullable=False)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"), nullable=False)
    reporter_identity: Mapped[str] = mapped_column(String(128), nullable=False)
    payload: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[str] = mapped_column(String(40), nullable=False)


class MissionTransitionRow(Base):
    __tablename__ = "mission_transitions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    mission_id: Mapped[str] = mapped_column(ForeignKey("missions.id"), nullable=False)
    from_state: Mapped[str] = mapped_column(String(32), nullable=False)
    to_state: Mapped[str] = mapped_column(String(32), nullable=False)
    payload: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[str] = mapped_column(String(40), nullable=False)


class MissionEnvironmentPlanRow(Base):
    __tablename__ = "mission_environment_plans"
    __table_args__ = (UniqueConstraint("mission_id", "version"),)

    plan_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    mission_id: Mapped[str] = mapped_column(ForeignKey("missions.id"), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    fingerprint: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    owner_identity: Mapped[str] = mapped_column(String(128), nullable=False)
    payload: Mapped[str] = mapped_column(Text, nullable=False)
    accepted_at: Mapped[str] = mapped_column(String(40), nullable=False)


class ProfessionalEvidenceRow(Base):
    __tablename__ = "professional_evidence"

    evidence_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    identity_id: Mapped[str] = mapped_column(String(128), nullable=False)
    kind: Mapped[str] = mapped_column(String(64), nullable=False)
    subject: Mapped[str] = mapped_column(String(512), nullable=False)
    scope_level: Mapped[str] = mapped_column(String(32), nullable=False)
    outcome: Mapped[str] = mapped_column(String(32), nullable=False)
    fresh_until: Mapped[str] = mapped_column(String(40), nullable=False)
    payload: Mapped[str] = mapped_column(Text, nullable=False)
    observed_at: Mapped[str] = mapped_column(String(40), nullable=False)


class ProfessionalPromotionRow(Base):
    __tablename__ = "professional_promotions"
    __table_args__ = (UniqueConstraint("identity_id", "kind", "subject", "revision"),)

    decision_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    request_id: Mapped[str] = mapped_column(String(36), unique=True, nullable=False)
    identity_id: Mapped[str] = mapped_column(String(128), nullable=False)
    kind: Mapped[str] = mapped_column(String(64), nullable=False)
    subject: Mapped[str] = mapped_column(String(512), nullable=False)
    disposition: Mapped[str] = mapped_column(String(32), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    payload: Mapped[str] = mapped_column(Text, nullable=False)
    decided_at: Mapped[str] = mapped_column(String(40), nullable=False)


class KnowledgeQueryRow(Base):
    __tablename__ = "knowledge_queries"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    knowledge_class: Mapped[str] = mapped_column(String(32), nullable=False)
    project_id: Mapped[str] = mapped_column(String(256), nullable=False)
    repository_id: Mapped[str | None] = mapped_column(String(256))
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    required: Mapped[bool] = mapped_column(Boolean, nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    bundle_artifact_id: Mapped[str | None] = mapped_column(ForeignKey("artifacts.id"))
    payload: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[str] = mapped_column(String(40), nullable=False)
    completed_at: Mapped[str | None] = mapped_column(String(40))


class KnowledgeSourceAttemptRow(Base):
    __tablename__ = "knowledge_source_attempts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    query_id: Mapped[str] = mapped_column(ForeignKey("knowledge_queries.id"), nullable=False)
    source_id: Mapped[str] = mapped_column(String(256), nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    payload: Mapped[str] = mapped_column(Text, nullable=False)
    recorded_at: Mapped[str] = mapped_column(String(40), nullable=False)


class KnowledgeCorpusRow(Base):
    __tablename__ = "knowledge_corpora"
    __table_args__ = (UniqueConstraint("project_id", "source_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(256), nullable=False)
    source_id: Mapped[str] = mapped_column(String(256), nullable=False)
    knowledge_class: Mapped[str] = mapped_column(String(32), nullable=False)
    external_identity: Mapped[str] = mapped_column(String(1024), nullable=False)
    indexed_revision: Mapped[str | None] = mapped_column(String(512))
    snapshot_artifact_id: Mapped[str | None] = mapped_column(ForeignKey("artifacts.id"))
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    payload: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[str] = mapped_column(String(40), nullable=False)


class KnowledgeOperationRow(Base):
    __tablename__ = "knowledge_operations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    project_id: Mapped[str] = mapped_column(String(256), nullable=False)
    source_id: Mapped[str] = mapped_column(String(256), nullable=False)
    corpus_id: Mapped[str | None] = mapped_column(ForeignKey("knowledge_corpora.id"))
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    request_fingerprint: Mapped[str] = mapped_column(String(71), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    payload: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[str] = mapped_column(String(40), nullable=False)
    updated_at: Mapped[str] = mapped_column(String(40), nullable=False)


class KnowledgePromotionRow(Base):
    __tablename__ = "knowledge_promotions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    item_id: Mapped[str] = mapped_column(String(36), nullable=False)
    source_project_id: Mapped[str] = mapped_column(String(256), nullable=False)
    target_scope: Mapped[str] = mapped_column(String(512), nullable=False)
    disposition: Mapped[str] = mapped_column(String(32), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    payload: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[str] = mapped_column(String(40), nullable=False)
    decided_at: Mapped[str | None] = mapped_column(String(40))


@dataclass(frozen=True, slots=True)
class RunSnapshot:
    run_id: str
    resumed: bool
    plan: AcceptedPlan | None
    results: tuple[InitializationResult, ...]
    reviews: tuple[ReviewDecision, ...]
    execution_context: PlanExecutionContext
    repository_establishment: RepositoryEstablishment | None

    @property
    def completed_task_ids(self) -> frozenset[str]:
        return frozenset(result.task_id for result in self.results)


class RunContentInspector(Protocol):
    def inspect(self, content: str, resolved_secrets: tuple[str, ...] = ()) -> str: ...


class LocalRunRepository:
    def __init__(
        self,
        database_path: Path,
        *,
        busy_timeout_ms: int = DEFAULT_BUSY_TIMEOUT_MS,
        content_inspector: RunContentInspector | None = None,
    ) -> None:
        from mishkan.persistence.migration import SchemaManager

        SchemaManager(database_path).require_current()
        self._engine = create_local_engine(
            database_path,
            busy_timeout_ms=busy_timeout_ms,
        )
        self._content_inspector = content_inspector

    def start_or_resume(
        self,
        discovery: DiscoverySnapshot,
        objective: str,
        outcome_id: str,
    ) -> RunSnapshot:
        self._require_safe_content(objective)
        resume_key = self._resume_key(discovery, objective, outcome_id)
        with Session(self._engine) as session, session.begin():
            run = session.scalar(select(RunRow).where(RunRow.resume_key == resume_key))
            resumed = run is not None
            if run is None:
                now = utc_now().isoformat()
                context = PlanExecutionContext.from_binding(discovery.binding)
                run = RunRow(
                    id=str(new_id()),
                    resume_key=resume_key,
                    context_kind=context.kind,
                    context_id=context.context_id,
                    context_revision=context.revision,
                    repository_id=context.repository_id,
                    repository_revision=context.repository_revision,
                    discovery_fingerprint=discovery.fingerprint,
                    objective=objective,
                    outcome_id=outcome_id,
                    status=RunState.PLANNING.value,
                    cancellation_requested=False,
                    created_at=now,
                    updated_at=now,
                )
                session.add(run)
                self._add_event(
                    session,
                    run.id,
                    "run.started",
                    {"execution_context": context.model_dump(mode="json")},
                )
            session.flush()
            return self._snapshot(session, run, resumed=resumed)

    def snapshot(self, run_id: str) -> RunSnapshot:
        """Read one exact durable run without changing its lifecycle."""
        with Session(self._engine) as session:
            run = self._require_run(session, run_id)
            return self._snapshot(session, run, resumed=True)

    def task_count(self, run_id: str) -> int:
        """Return the exact task count from the immutable accepted run plan."""
        with Session(self._engine) as session:
            self._require_run(session, run_id)
            plan = session.scalar(select(PlanRow).where(PlanRow.run_id == run_id))
            if plan is None:
                return 0
            return len(AcceptedPlan.model_validate_json(plan.payload).tasks)

    def accept_plan(self, run_id: str, plan: AcceptedPlan) -> RunSnapshot:
        self._require_safe_content(plan.model_dump_json())
        with Session(self._engine) as session, session.begin():
            run = self._require_run(session, run_id)
            expected_context = self._run_execution_context(run)
            if plan.schema_version == "1.2" and plan.execution_context != expected_context:
                raise MishkanError(
                    ErrorCode.REVISION_MISMATCH,
                    "accepted plan execution context differs from its run",
                )
            if plan.schema_version != "1.2" and run.context_kind != "repository":
                raise MishkanError(
                    ErrorCode.PLAN,
                    "prospective workspace run requires an explicit plan 1.2 context",
                )
            existing = session.scalar(select(PlanRow).where(PlanRow.run_id == run_id))
            payload = plan.model_dump_json()
            if existing is not None:
                if existing.payload != payload:
                    raise MishkanError(
                        ErrorCode.DUPLICATE_RESULT,
                        "run already has a different accepted plan",
                        details={"run_id": run_id},
                    )
                return self._snapshot(session, run, resumed=True)

            session.add(
                PlanRow(
                    id=str(new_id()),
                    run_id=run_id,
                    fingerprint=plan.fingerprint,
                    payload=payload,
                    accepted_at=utc_now().isoformat(),
                )
            )
            for position, task in enumerate(plan.tasks):
                task_state = TaskState.ELIGIBLE if not task.depends_on else TaskState.PENDING
                session.add(
                    TaskRow(
                        id=str(new_id()),
                        run_id=run_id,
                        task_key=task.task_id,
                        position=position,
                        status=task_state.value,
                        revision=0,
                        attempt_count=0,
                        contract=task.model_dump_json(),
                        updated_at=utc_now().isoformat(),
                    )
                )
            run.status = RunState.QUEUED.value
            run.revision += 1
            run.updated_at = utc_now().isoformat()
            self._add_event(
                session,
                run_id,
                "plan.accepted",
                {"plan_fingerprint": plan.fingerprint},
            )
            if plan.registry is not None:
                selected: set[tuple[str, str]] = set()
                for binding in plan.tool_bindings:
                    contract = plan.registry.require(binding.tool_id)
                    selection = (contract.tool_id, contract.version)
                    if selection not in selected:
                        self._add_event(
                            session,
                            run_id,
                            "tool.selected",
                            {
                                "tool_id": contract.tool_id,
                                "tool_version": contract.version,
                                "registry_fingerprint": plan.registry.fingerprint,
                                "plan_fingerprint": plan.fingerprint,
                            },
                        )
                        selected.add(selection)
                    self._add_event(
                        session,
                        run_id,
                        "tool.bound",
                        {
                            "task_id": binding.task_id,
                            "role": binding.role,
                            "tool_id": binding.tool_id,
                            "tool_version": binding.tool_version,
                            "contract_fingerprint": binding.contract_fingerprint,
                            "registry_fingerprint": binding.registry_fingerprint,
                        },
                    )
            self._add_event(session, run_id, "run.queued", {})
            session.flush()
            return self._snapshot(session, run, resumed=False)

    def record_repository_establishment(
        self,
        establishment: RepositoryEstablishment,
    ) -> RunSnapshot:
        from mishkan.repository.inspector import RepositoryInspector

        observed = RepositoryInspector().bind(establishment.repository.root)
        if observed != establishment.repository:
            raise MishkanError(
                ErrorCode.REVISION_MISMATCH,
                "repository establishment does not match the currently observed repository",
            )
        if establishment.prospective_workspace.root.resolve() != observed.root.resolve():
            raise MishkanError(
                ErrorCode.PROJECT,
                "repository was not established at the prospective workspace root",
            )
        self._require_safe_content(establishment.model_dump_json())
        with Session(self._engine) as session, session.begin():
            run = self._require_run(session, establishment.run_id)
            if run.context_kind != "prospective_workspace" or (
                run.context_id != establishment.prospective_workspace.workspace_id
                or run.context_revision != establishment.prospective_workspace.discovery_revision
            ):
                raise MishkanError(
                    ErrorCode.REVISION_MISMATCH,
                    "repository establishment does not descend from the run context",
                )
            existing = session.scalar(
                select(RepositoryEstablishmentRow).where(
                    RepositoryEstablishmentRow.run_id == run.id
                )
            )
            payload = establishment.model_dump_json()
            if existing is not None:
                if existing.payload != payload:
                    raise MishkanError(
                        ErrorCode.DUPLICATE_RESULT,
                        "prospective run already established a different repository",
                    )
                return self._snapshot(session, run, resumed=True)
            session.add(
                RepositoryEstablishmentRow(
                    establishment_id=str(establishment.establishment_id),
                    run_id=run.id,
                    prospective_workspace_id=(establishment.prospective_workspace.workspace_id),
                    discovery_revision=(establishment.prospective_workspace.discovery_revision),
                    repository_id=establishment.repository.repository_id,
                    repository_revision=establishment.repository.base_revision,
                    payload=payload,
                    established_at=establishment.established_at.isoformat(),
                )
            )
            self._add_event(
                session,
                run.id,
                "run.repository_established",
                {
                    "prospective_workspace_id": (establishment.prospective_workspace.workspace_id),
                    "discovery_revision": (establishment.prospective_workspace.discovery_revision),
                    "repository_id": establishment.repository.repository_id,
                    "repository_revision": establishment.repository.base_revision,
                    "establishment_id": str(establishment.establishment_id),
                    "evidence_references": list(establishment.evidence_references),
                },
            )
            session.flush()
            return self._snapshot(session, run, resumed=False)

    def mark_awaiting_approval(self, run_id: str) -> None:
        """Expose the approval boundary before deterministic plan acceptance."""
        with Session(self._engine) as session, session.begin():
            run = self._require_run(session, run_id)
            if run.status == RunState.AWAITING_APPROVAL.value:
                return
            if run.status != RunState.PLANNING.value:
                raise MishkanError(ErrorCode.REVISION_MISMATCH, "run is not planning")
            run.status = RunState.AWAITING_APPROVAL.value
            run.revision += 1
            run.updated_at = utc_now().isoformat()
            self._add_event(session, run_id, "run.awaiting_approval", {})

    def start_run(self, run_id: str) -> None:
        """Start one durably queued plan; repeated starts are idempotent."""
        with Session(self._engine) as session, session.begin():
            run = self._require_run(session, run_id)
            if run.status in {RunState.RUNNING.value, RunState.COMPLETED.value}:
                return
            if run.status != RunState.QUEUED.value:
                raise MishkanError(ErrorCode.REVISION_MISMATCH, "run is not queued")
            run.status = RunState.RUNNING.value
            run.revision += 1
            run.updated_at = utc_now().isoformat()
            self._add_event(session, run_id, "run.running", {})

    def claim_task(self, run_id: str, task_id: str) -> int:
        """Move one eligible task to executing and return its monotone attempt number."""
        with Session(self._engine) as session, session.begin():
            run = self._require_run(session, run_id)
            task = self._require_task(session, run_id, task_id)
            if run.cancellation_requested or run.status != RunState.RUNNING.value:
                raise MishkanError(ErrorCode.RUN_INTERRUPTED, "run is not accepting task claims")
            if task.status != TaskState.ELIGIBLE.value:
                raise MishkanError(
                    ErrorCode.REVISION_MISMATCH,
                    "task is not eligible for execution",
                    details={"task_id": task_id, "state": task.status},
                )
            task.status = TaskState.EXECUTING.value
            task.attempt_count += 1
            task.revision += 1
            task.updated_at = utc_now().isoformat()
            self._add_event(
                session,
                run_id,
                "task.claimed",
                {"task_id": task_id, "attempt": task.attempt_count},
            )
            return task.attempt_count

    def mark_validating(self, run_id: str, task_id: str) -> None:
        with Session(self._engine) as session, session.begin():
            task = self._require_task(session, run_id, task_id)
            if task.status != TaskState.EXECUTING.value:
                raise MishkanError(ErrorCode.REVISION_MISMATCH, "task is not executing")
            task.status = TaskState.VALIDATING.value
            task.revision += 1
            task.updated_at = utc_now().isoformat()
            self._add_event(session, run_id, "task.validating", {"task_id": task_id})

    def mark_task_failure(self, run_id: str, task_id: str, *, rejected: bool) -> None:
        with Session(self._engine) as session, session.begin():
            run = self._require_run(session, run_id)
            task = self._require_task(session, run_id, task_id)
            if task.status not in {TaskState.EXECUTING.value, TaskState.VALIDATING.value}:
                raise MishkanError(ErrorCode.REVISION_MISMATCH, "task is not active")
            state = TaskState.REJECTED if rejected else TaskState.FAILED
            task.status = state.value
            task.revision += 1
            task.updated_at = utc_now().isoformat()
            self._add_event(session, run_id, f"task.{state.value}", {"task_id": task_id})
            run.status = RunState.FAILED.value
            run.revision += 1
            run.updated_at = task.updated_at
            self._add_event(
                session,
                run_id,
                "run.failed",
                {"task_id": task_id, "task_state": state.value},
            )

    def record_rejected_review(
        self,
        run_id: str,
        result: InitializationResult,
        review: ReviewDecision,
        *,
        review_sequence: int,
    ) -> TaskReviewRejection:
        """Persist rejection evidence before another synthesis attempt can begin."""
        self._require_safe_content(result.model_dump_json(), review.model_dump_json())
        with Session(self._engine) as session, session.begin():
            run = self._require_run(session, run_id)
            task = self._require_task(session, run_id, result.task_id)
            if task.status != TaskState.VALIDATING.value:
                raise MishkanError(ErrorCode.REVISION_MISMATCH, "task is not validating")
            if review.task_id != result.task_id or review.verdict != "rejected":
                raise MishkanError(
                    ErrorCode.OUTPUT_CONTRACT,
                    "rejection evidence does not match the validating task",
                )
            if not self._result_matches_run_context(result, run):
                raise MishkanError(
                    ErrorCode.REVISION_MISMATCH,
                    "rejected result revision differs from the run base revision",
                )
            existing = session.scalar(
                select(ReviewRejectionRow).where(
                    ReviewRejectionRow.run_id == run_id,
                    ReviewRejectionRow.task_key == result.task_id,
                    ReviewRejectionRow.task_attempt == task.attempt_count,
                    ReviewRejectionRow.review_sequence == review_sequence,
                )
            )
            if existing is not None:
                if (
                    existing.result_payload != result.model_dump_json()
                    or existing.review_payload != review.model_dump_json()
                ):
                    raise MishkanError(
                        ErrorCode.DUPLICATE_RESULT,
                        "review rejection identity was reused for different evidence",
                    )
                return self._review_rejection(existing)
            recorded_at = utc_now().isoformat()
            row = ReviewRejectionRow(
                id=str(new_id()),
                run_id=run_id,
                task_key=result.task_id,
                task_attempt=task.attempt_count,
                review_sequence=review_sequence,
                result_payload=result.model_dump_json(),
                review_payload=review.model_dump_json(),
                recorded_at=recorded_at,
            )
            session.add(row)
            self._add_event(
                session,
                run_id,
                "task.review_rejected",
                {
                    "task_id": result.task_id,
                    "task_attempt": task.attempt_count,
                    "review_sequence": review_sequence,
                    "issue_count": len(review.issues),
                },
            )
            session.flush()
            return self._review_rejection(row)

    def rejected_reviews(self, run_id: str) -> tuple[TaskReviewRejection, ...]:
        with Session(self._engine) as session:
            rows = session.scalars(
                select(ReviewRejectionRow)
                .where(ReviewRejectionRow.run_id == run_id)
                .order_by(
                    ReviewRejectionRow.task_attempt,
                    ReviewRejectionRow.review_sequence,
                    ReviewRejectionRow.recorded_at,
                )
            ).all()
            return tuple(self._review_rejection(row) for row in rows)

    def cancel_run(self, run_id: str) -> RunSnapshot:
        """Persist cancellation before preventing all future eligibility."""
        with Session(self._engine) as session, session.begin():
            run = self._require_run(session, run_id)
            if run.status in {RunState.COMPLETED.value, RunState.CANCELLED.value}:
                return self._snapshot(session, run, resumed=True)
            run.cancellation_requested = True
            run.status = RunState.CANCELLING.value
            run.revision += 1
            now = utc_now().isoformat()
            run.updated_at = now
            tasks = session.scalars(select(TaskRow).where(TaskRow.run_id == run_id)).all()
            for task in tasks:
                if task.status in {TaskState.PENDING.value, TaskState.ELIGIBLE.value}:
                    task.status = TaskState.CANCELLED.value
                    task.revision += 1
                    task.updated_at = now
                elif task.status in {TaskState.EXECUTING.value, TaskState.VALIDATING.value}:
                    self._add_event(
                        session,
                        run_id,
                        "task.cancellation_requested",
                        {"task_id": task.task_key},
                    )
            if not any(
                task.status in {TaskState.EXECUTING.value, TaskState.VALIDATING.value}
                for task in tasks
            ):
                run.status = RunState.CANCELLED.value
            self._add_event(session, run_id, "run.cancellation_requested", {})
            return self._snapshot(session, run, resumed=False)

    def requested(self, run_id: str, task_attempt_id: str) -> bool:
        """Expose the durable run signal through the Gateway cancellation protocol."""
        del task_attempt_id
        with Session(self._engine) as session:
            run = session.get(RunRow, run_id)
            return run is not None and run.cancellation_requested

    def reserve_planned_call(
        self,
        *,
        invocation_id: str,
        run_id: str,
        task_attempt_id: str,
        planned_call_id: str,
        request_fingerprint: str,
        tool_id: str,
        tool_version: str,
        effect_class: str,
        declared_effects: tuple[str, ...],
    ) -> ToolResultEnvelope | None:
        """Reserve one accepted call or replay its exact durable terminal result."""
        with Session(self._engine) as session, session.begin():
            self._require_run(session, run_id)
            row = session.scalar(
                select(PlannedToolCallRow).where(
                    PlannedToolCallRow.run_id == run_id,
                    PlannedToolCallRow.task_attempt_id == task_attempt_id,
                    PlannedToolCallRow.planned_call_id == planned_call_id,
                )
            )
            if row is None:
                now = utc_now().isoformat()
                session.add(
                    PlannedToolCallRow(
                        id=invocation_id,
                        run_id=run_id,
                        task_attempt_id=task_attempt_id,
                        planned_call_id=planned_call_id,
                        request_fingerprint=request_fingerprint,
                        tool_id=tool_id,
                        tool_version=tool_version,
                        effect_class=effect_class,
                        declared_effects_payload=json.dumps(declared_effects),
                        state="reserved",
                        effect_settlement=None,
                        result_payload=None,
                        created_at=now,
                        updated_at=now,
                    )
                )
                return None
            identity = (
                row.id,
                row.request_fingerprint,
                row.tool_id,
                row.tool_version,
                row.effect_class,
            )
            expected = (
                invocation_id,
                request_fingerprint,
                tool_id,
                tool_version,
                effect_class,
            )
            if identity != expected or tuple(json.loads(row.declared_effects_payload)) != (
                declared_effects
            ):
                raise MishkanError(
                    ErrorCode.DUPLICATE_RESULT,
                    "planned call identity was reused for different content",
                    details={
                        "run_id": run_id,
                        "task_attempt_id": task_attempt_id,
                        "planned_call_id": planned_call_id,
                    },
                )
            if row.state == CallStatus.COMPLETED.value and row.result_payload is not None:
                return ToolResultEnvelope.model_validate_json(row.result_payload)
            if row.state in {"dispatching", CallStatus.UNCERTAIN.value} or (
                row.effect_settlement == EffectSettlement.UNCERTAIN.value
            ):
                raise MishkanError(
                    ErrorCode.RUN_INTERRUPTED,
                    "planned call has an unreconciled effect",
                    details={
                        "planned_call_id": planned_call_id,
                        "state": row.state,
                        "effect_settlement": row.effect_settlement,
                        "automatic_retry": False,
                    },
                )
            if row.state in {
                CallStatus.FAILED.value,
                CallStatus.CANCELLED.value,
                CallStatus.REFUSED.value,
            }:
                if row.effect_settlement != EffectSettlement.ABSENT.value:
                    raise MishkanError(
                        ErrorCode.RUN_INTERRUPTED,
                        "planned call cannot be retried without absent-effect proof",
                        details={"planned_call_id": planned_call_id, "automatic_retry": False},
                    )
                row.state = "reserved"
                row.effect_settlement = None
                row.result_payload = None
                row.updated_at = utc_now().isoformat()
                self._add_event(
                    session,
                    run_id,
                    "tool.call_retry_reserved",
                    {
                        "invocation_id": row.id,
                        "task_attempt_id": row.task_attempt_id,
                        "planned_call_id": row.planned_call_id,
                        "tool_id": row.tool_id,
                        "tool_version": row.tool_version,
                        "reason": "prior terminal result proved effect absent",
                    },
                )
            return None

    def mark_planned_call_dispatching(
        self,
        invocation_id: str,
        request_fingerprint: str,
    ) -> None:
        """Persist the conservative crash boundary immediately before adapter dispatch."""
        with Session(self._engine) as session, session.begin():
            row = session.get(PlannedToolCallRow, invocation_id)
            if row is None or row.request_fingerprint != request_fingerprint:
                raise MishkanError(ErrorCode.DUPLICATE_RESULT, "planned call reservation differs")
            if row.state != "reserved":
                raise MishkanError(
                    ErrorCode.REVISION_MISMATCH,
                    "planned call is not reserved for dispatch",
                    details={"state": row.state},
                )
            row.state = "dispatching"
            row.updated_at = utc_now().isoformat()

    def complete_planned_call(
        self,
        invocation_id: str,
        request_fingerprint: str,
        result: ToolResultEnvelope,
        effect_settlement: EffectSettlement,
    ) -> ToolResultEnvelope:
        """Store the inspected terminal envelope before it can be accepted by CrewAI."""
        payload = result.model_dump_json()
        self._require_safe_content(payload)
        with Session(self._engine) as session, session.begin():
            row = session.get(PlannedToolCallRow, invocation_id)
            if row is None or row.request_fingerprint != request_fingerprint:
                raise MishkanError(ErrorCode.DUPLICATE_RESULT, "planned call reservation differs")
            if row.result_payload is not None:
                existing = ToolResultEnvelope.model_validate_json(row.result_payload)
                if existing != result or row.effect_settlement != effect_settlement.value:
                    raise MishkanError(
                        ErrorCode.DUPLICATE_RESULT,
                        "planned call completion differs from its durable result",
                    )
                return existing
            if row.state not in {"reserved", "dispatching"}:
                raise MishkanError(
                    ErrorCode.REVISION_MISMATCH,
                    "planned call cannot accept a completion from its current state",
                    details={"state": row.state},
                )
            row.state = result.status.value
            row.effect_settlement = effect_settlement.value
            row.result_payload = payload
            row.updated_at = utc_now().isoformat()
            return result

    def unresolved_planned_effects(self, run_id: str) -> tuple[str, ...]:
        """Return blockers derived only from the authoritative planned-call journal."""
        with Session(self._engine) as session:
            self._require_run(session, run_id)
            return self._unresolved_planned_effects(session, run_id)

    def settle_cancellation(self, run_id: str) -> RunSnapshot:
        """Settle active tasks only after their local execution boundary has returned."""
        with Session(self._engine) as session, session.begin():
            run = self._require_run(session, run_id)
            if not run.cancellation_requested:
                raise MishkanError(
                    ErrorCode.REVISION_MISMATCH,
                    "run cancellation has not been requested",
                )
            if run.status == RunState.CANCELLED.value:
                return self._snapshot(session, run, resumed=True)
            now = utc_now().isoformat()
            tasks = session.scalars(select(TaskRow).where(TaskRow.run_id == run_id)).all()
            for task in tasks:
                if task.status in {
                    TaskState.PENDING.value,
                    TaskState.ELIGIBLE.value,
                    TaskState.EXECUTING.value,
                    TaskState.VALIDATING.value,
                }:
                    task.status = TaskState.CANCELLED.value
                    task.revision += 1
                    task.updated_at = now
                    self._add_event(
                        session,
                        run_id,
                        "task.cancelled",
                        {"task_id": task.task_key},
                    )
            run.status = RunState.CANCELLED.value
            run.revision += 1
            run.updated_at = now
            self._add_event(session, run_id, "run.cancelled", {})
            return self._snapshot(session, run, resumed=False)

    def recover_interrupted(
        self,
        run_id: str,
    ) -> tuple[str, ...]:
        """Release work only after the daemon proves every planned effect retry-safe."""
        with Session(self._engine) as session, session.begin():
            run = self._require_run(session, run_id)
            retry_safe = session.scalars(
                select(PlannedToolCallRow).where(
                    PlannedToolCallRow.run_id == run_id,
                    PlannedToolCallRow.state == "dispatching",
                    PlannedToolCallRow.declared_effects_payload == "[]",
                )
            ).all()
            for call in retry_safe:
                call.state = CallStatus.FAILED.value
                call.effect_settlement = EffectSettlement.ABSENT.value
                call.updated_at = utc_now().isoformat()
                self._add_event(
                    session,
                    run_id,
                    "tool.call_retry_reconciled",
                    {
                        "invocation_id": call.id,
                        "task_attempt_id": call.task_attempt_id,
                        "planned_call_id": call.planned_call_id,
                        "tool_id": call.tool_id,
                        "tool_version": call.tool_version,
                        "reason": "interrupted call had no declared effects",
                    },
                )
            if retry_safe:
                self._add_event(
                    session,
                    run_id,
                    "run.retry_safe_calls_reconciled",
                    {"call_count": len(retry_safe)},
                )
            unresolved_effects = self._unresolved_durable_effects(session, run_id)
            if unresolved_effects and run.status != RunState.BLOCKED.value:
                run.status = RunState.BLOCKED.value
                run.revision += 1
                run.updated_at = utc_now().isoformat()
                self._add_event(
                    session,
                    run_id,
                    "run.blocked",
                    {"effect_count": len(unresolved_effects)},
                )
        if unresolved_effects:
            raise MishkanError(
                ErrorCode.RUN_INTERRUPTED,
                "run contains unreconciled stateful effects",
                details={"effects": unresolved_effects, "automatic_retry": False},
            )
        with Session(self._engine) as session, session.begin():
            run = self._require_run(session, run_id)
            if run.cancellation_requested:
                return ()
            if run.status in {RunState.BLOCKED.value, RunState.FAILED.value}:
                run.status = RunState.RUNNING.value
                run.revision += 1
                run.updated_at = utc_now().isoformat()
                self._add_event(session, run_id, "run.running", {"resumed": True})
            tasks = session.scalars(
                select(TaskRow).where(
                    TaskRow.run_id == run_id,
                    TaskRow.status.in_(
                        (
                            TaskState.EXECUTING.value,
                            TaskState.VALIDATING.value,
                            TaskState.REJECTED.value,
                            TaskState.FAILED.value,
                        )
                    ),
                )
            ).all()
            released: list[str] = []
            for task in tasks:
                if self._dependencies_accepted(session, run_id, task):
                    task.status = TaskState.ELIGIBLE.value
                    task.revision += 1
                    task.updated_at = utc_now().isoformat()
                    released.append(task.task_key)
            if released:
                self._add_event(
                    session, run_id, "run.interrupted_tasks_released", {"tasks": released}
                )
            return tuple(released)

    @staticmethod
    def _unresolved_planned_effects(session: Session, run_id: str) -> tuple[str, ...]:
        rows = session.scalars(
            select(PlannedToolCallRow)
            .where(
                PlannedToolCallRow.run_id == run_id,
                (
                    PlannedToolCallRow.state.in_(("dispatching", CallStatus.UNCERTAIN.value))
                    | (PlannedToolCallRow.effect_settlement == EffectSettlement.UNCERTAIN.value)
                ),
            )
            .order_by(
                PlannedToolCallRow.task_attempt_id,
                PlannedToolCallRow.planned_call_id,
            )
        ).all()
        return tuple(
            f"planned-call:{row.task_attempt_id}:{row.planned_call_id}:{row.state}" for row in rows
        )

    @classmethod
    def _unresolved_durable_effects(cls, session: Session, run_id: str) -> tuple[str, ...]:
        blockers = list(cls._unresolved_planned_effects(session, run_id))
        execution_rows = session.scalars(
            select(ExecutionSessionRow).where(ExecutionSessionRow.run_id == run_id)
        ).all()
        for execution_row in execution_rows:
            try:
                request = json.loads(execution_row.request_payload)
                declared = request.get("declared_effects", [])
            except (AttributeError, json.JSONDecodeError):
                blockers.append(f"execution:{execution_row.id}:corrupt")
                continue
            stateful_active = execution_row.state in {
                "starting",
                "running",
                "ready",
                "cancelling",
            } and bool(declared)
            if (
                stateful_active
                or execution_row.state in {"uncertain"}
                or execution_row.effect_settlement == EffectSettlement.UNCERTAIN.value
            ):
                blockers.append(f"execution:{execution_row.id}:{execution_row.state}")
        browser_rows = session.scalars(
            select(BrowserSessionRow).where(BrowserSessionRow.run_id == run_id)
        ).all()
        for browser_row in browser_rows:
            if browser_row.state == "uncertain":
                blockers.append(f"browser:{browser_row.id}:{browser_row.state}")
        mcp_rows = session.scalars(select(McpCallRow)).all()
        for mcp_row in mcp_rows:
            try:
                request = json.loads(mcp_row.request_payload)
            except json.JSONDecodeError:
                blockers.append(f"mcp:{mcp_row.id}:corrupt")
                continue
            if not isinstance(request, dict) or request.get("run_id") != run_id:
                continue
            if mcp_row.state in {"dispatching", "running", "cancel_requested", "uncertain"}:
                blockers.append(f"mcp:{mcp_row.id}:{mcp_row.state}")
        return tuple(dict.fromkeys(blockers))

    def task_states(self, run_id: str) -> dict[str, str]:
        with Session(self._engine) as session:
            rows = session.execute(
                select(TaskRow.task_key, TaskRow.status)
                .where(TaskRow.run_id == run_id)
                .order_by(TaskRow.position)
            ).all()
            return {row.task_key: row.status for row in rows}

    def task_contract(self, run_id: str, task_id: str) -> PlanTask:
        with Session(self._engine) as session:
            task = self._require_task(session, run_id, task_id)
            return PlanTask.model_validate_json(task.contract)

    def run_state(self, run_id: str) -> str:
        with Session(self._engine) as session:
            return self._require_run(session, run_id).status

    def accept_result(
        self,
        run_id: str,
        result: InitializationResult,
        review: ReviewDecision,
    ) -> RunSnapshot:
        self._require_safe_content(result.model_dump_json(), review.model_dump_json())
        with Session(self._engine) as session, session.begin():
            run = self._require_run(session, run_id)
            task = session.scalar(
                select(TaskRow).where(
                    TaskRow.run_id == run_id,
                    TaskRow.task_key == result.task_id,
                )
            )
            if task is None:
                raise MishkanError(
                    ErrorCode.OUTPUT_CONTRACT,
                    "result does not identify an accepted task",
                    details={"run_id": run_id, "task_id": result.task_id},
                )
            if not self._result_matches_run_context(result, run):
                raise MishkanError(
                    ErrorCode.REVISION_MISMATCH,
                    "result revision differs from the run base revision",
                    details={"run_id": run_id, "task_id": result.task_id},
                )

            payload = result.model_dump_json()
            existing = session.scalar(
                select(ResultRow).where(
                    ResultRow.run_id == run_id,
                    ResultRow.task_key == result.task_id,
                )
            )
            if existing is not None:
                if existing.payload != payload:
                    raise MishkanError(
                        ErrorCode.DUPLICATE_RESULT,
                        "task already has a different accepted result",
                        details={"run_id": run_id, "task_id": result.task_id},
                    )
                acceptance = session.scalar(
                    select(AcceptanceRow).where(
                        AcceptanceRow.run_id == run_id,
                        AcceptanceRow.task_key == result.task_id,
                    )
                )
                if acceptance is None or acceptance.review_payload != review.model_dump_json():
                    raise MishkanError(
                        ErrorCode.DUPLICATE_RESULT,
                        "task already has different review evidence",
                        details={"run_id": run_id, "task_id": result.task_id},
                    )
                self._add_event(
                    session,
                    run_id,
                    "task.completion_duplicated",
                    {"task_id": result.task_id, "ignored": True},
                )
                return self._snapshot(session, run, resumed=True)

            if run.cancellation_requested:
                raise MishkanError(
                    ErrorCode.RUN_INTERRUPTED,
                    "run cancellation prevents new result acceptance",
                    details={"run_id": run_id},
                )
            if not self._dependencies_accepted(session, run_id, task):
                raise MishkanError(
                    ErrorCode.RUN_INTERRUPTED,
                    "task dependencies are not durably accepted",
                    details={"run_id": run_id, "task_id": result.task_id},
                )
            if task.status != TaskState.VALIDATING.value:
                raise MishkanError(
                    ErrorCode.REVISION_MISMATCH,
                    "task result can be accepted only from validating state",
                    details={"task_id": result.task_id, "state": task.status},
                )
            if review.verdict != "accepted":
                raise MishkanError(
                    ErrorCode.OUTPUT_CONTRACT,
                    "rejected review cannot become a durable task acceptance",
                )
            if review.schema_version == "1.1":
                contract = PlanTask.model_validate_json(task.contract)
                if review.producer_identity != contract.assigned_role:
                    raise MishkanError(
                        ErrorCode.ROLE_CONFLICT,
                        "review producer identity differs from the accepted task owner",
                        details={
                            "task_id": result.task_id,
                            "expected": contract.assigned_role,
                            "received": review.producer_identity,
                        },
                    )

            result_id = str(new_id())
            accepted_at = utc_now().isoformat()
            session.add(
                ResultRow(
                    id=result_id,
                    run_id=run_id,
                    task_key=result.task_id,
                    payload=payload,
                    accepted_at=accepted_at,
                )
            )
            session.flush()
            session.add(
                AcceptanceRow(
                    id=str(new_id()),
                    run_id=run_id,
                    task_key=result.task_id,
                    result_id=result_id,
                    review_payload=review.model_dump_json(),
                    accepted_at=accepted_at,
                )
            )
            task.status = TaskState.ACCEPTED.value
            task.revision += 1
            task.updated_at = accepted_at
            self._add_event(
                session,
                run_id,
                "task.result_accepted",
                {"task_id": result.task_id},
            )
            self._release_dependents(session, run_id)
            pending = session.scalar(
                select(TaskRow.id).where(
                    TaskRow.run_id == run_id,
                    TaskRow.status != TaskState.ACCEPTED.value,
                )
            )
            if pending is None:
                run.status = RunState.COMPLETED.value
                run.revision += 1
                run.updated_at = accepted_at
                self._add_event(session, run_id, "run.completed", {})
            session.flush()
            return self._snapshot(session, run, resumed=False)

    def outbox_events(self) -> tuple[dict[str, Any], ...]:
        with Session(self._engine) as session:
            rows = session.scalars(select(OutboxRow).order_by(OutboxRow.cursor)).all()
            return tuple(
                {
                    "cursor": row.cursor,
                    "id": row.id,
                    "aggregate_id": row.aggregate_id,
                    "entity_type": row.entity_type,
                    "event_type": row.event_type,
                    "source": row.source,
                    "payload": json.loads(row.payload),
                    "occurred_at": row.occurred_at,
                }
                for row in rows
            )

    def record(self, audit: AuditEvent) -> None:
        """Persist already-inspected capability evidence in the authoritative outbox."""
        self._require_safe_content(audit.model_dump_json())
        with Session(self._engine) as session, session.begin():
            self._require_run(session, audit.run_id)
            session.add(
                OutboxRow(
                    id=str(audit.id),
                    schema_version=audit.schema_version,
                    aggregate_id=audit.run_id,
                    entity_type="run",
                    run_id=audit.run_id,
                    task_id=audit.task_attempt_id,
                    identity_id=audit.identity,
                    team_id=None,
                    security_relevant=(
                        audit.decision.casefold() not in {"allow", "allowed", "completed"}
                    ),
                    event_type=audit.event_type,
                    source="mishkan.gateway",
                    payload=audit.model_dump_json(),
                    occurred_at=audit.created_at.isoformat(),
                    command_id=None,
                    correlation_id=None,
                    causation_id=None,
                    sensitivity="internal",
                    published_at=None,
                )
            )

    def _require_safe_content(self, *payloads: str) -> None:
        if self._content_inspector is None:
            return
        for payload in payloads:
            if self._content_inspector.inspect(payload) != payload:
                raise MishkanError(
                    ErrorCode.SECRET_CONTENT,
                    "run evidence requires redaction and cannot be persisted faithfully",
                )

    @staticmethod
    def _resume_key(
        discovery: DiscoverySnapshot,
        objective: str,
        outcome_id: str,
    ) -> str:
        source = "\0".join(
            (
                discovery.binding.context_kind,
                discovery.binding.context_id,
                discovery.binding.context_revision,
                discovery.fingerprint,
                objective,
                outcome_id,
            )
        )
        return hashlib.sha256(source.encode()).hexdigest()

    @staticmethod
    def _require_run(session: Session, run_id: str) -> RunRow:
        run = session.get(RunRow, run_id)
        if run is None:
            raise MishkanError(
                ErrorCode.RUN_INTERRUPTED,
                "run state does not exist",
                details={"run_id": run_id},
            )
        return run

    @staticmethod
    def _result_matches_run_context(result: InitializationResult, run: RunRow) -> bool:
        if result.schema_version == "1.1":
            context = result.execution_context
            return context is not None and (
                context.kind == run.context_kind
                and context.context_id == run.context_id
                and context.revision == run.context_revision
                and context.repository_id == run.repository_id
                and context.repository_revision == run.repository_revision
            )
        return run.context_kind == "repository" and (
            result.repository_revision == run.repository_revision
        )

    @staticmethod
    def _run_execution_context(run: RunRow) -> PlanExecutionContext:
        return PlanExecutionContext.model_validate(
            {
                "kind": run.context_kind,
                "context_id": run.context_id,
                "revision": run.context_revision,
                "repository_id": run.repository_id,
                "repository_revision": run.repository_revision,
                "prospective_workspace_id": (
                    run.context_id if run.context_kind == "prospective_workspace" else None
                ),
                "discovery_revision": (
                    run.context_revision if run.context_kind == "prospective_workspace" else None
                ),
            }
        )

    @staticmethod
    def _require_task(session: Session, run_id: str, task_id: str) -> TaskRow:
        task = session.scalar(
            select(TaskRow).where(TaskRow.run_id == run_id, TaskRow.task_key == task_id)
        )
        if task is None:
            raise MishkanError(
                ErrorCode.RUN_INTERRUPTED,
                "run task does not exist",
                details={"run_id": run_id, "task_id": task_id},
            )
        return task

    @staticmethod
    def _dependencies_accepted(session: Session, run_id: str, task: TaskRow) -> bool:
        contract = PlanTask.model_validate_json(task.contract)
        if not contract.depends_on:
            return True
        accepted = set(
            session.scalars(
                select(TaskRow.task_key).where(
                    TaskRow.run_id == run_id,
                    TaskRow.task_key.in_(contract.depends_on),
                    TaskRow.status == TaskState.ACCEPTED.value,
                )
            ).all()
        )
        return accepted == set(contract.depends_on)

    @classmethod
    def _release_dependents(cls, session: Session, run_id: str) -> None:
        pending = session.scalars(
            select(TaskRow).where(
                TaskRow.run_id == run_id,
                TaskRow.status == TaskState.PENDING.value,
            )
        ).all()
        for task in pending:
            if cls._dependencies_accepted(session, run_id, task):
                task.status = TaskState.ELIGIBLE.value
                task.revision += 1
                task.updated_at = utc_now().isoformat()

    @staticmethod
    def _snapshot(session: Session, run: RunRow, *, resumed: bool) -> RunSnapshot:
        plan_row = session.scalar(select(PlanRow).where(PlanRow.run_id == run.id))
        result_rows = session.scalars(
            select(ResultRow).where(ResultRow.run_id == run.id).order_by(ResultRow.accepted_at)
        ).all()
        acceptance_rows = session.scalars(
            select(AcceptanceRow)
            .where(AcceptanceRow.run_id == run.id)
            .order_by(AcceptanceRow.accepted_at)
        ).all()
        plan = AcceptedPlan.model_validate_json(plan_row.payload) if plan_row is not None else None
        establishment_row = session.scalar(
            select(RepositoryEstablishmentRow).where(RepositoryEstablishmentRow.run_id == run.id)
        )
        results = tuple(
            InitializationResult.model_validate_json(row.payload) for row in result_rows
        )
        reviews = tuple(
            ReviewDecision.model_validate_json(row.review_payload) for row in acceptance_rows
        )
        return RunSnapshot(
            run_id=run.id,
            resumed=resumed,
            plan=plan,
            results=results,
            reviews=reviews,
            execution_context=LocalRunRepository._run_execution_context(run),
            repository_establishment=(
                RepositoryEstablishment.model_validate_json(establishment_row.payload)
                if establishment_row is not None
                else None
            ),
        )

    @staticmethod
    def _review_rejection(row: ReviewRejectionRow) -> TaskReviewRejection:
        return TaskReviewRejection(
            run_id=row.run_id,
            task_id=row.task_key,
            task_attempt=row.task_attempt,
            review_sequence=row.review_sequence,
            result=InitializationResult.model_validate_json(row.result_payload),
            review=ReviewDecision.model_validate_json(row.review_payload),
            recorded_at=datetime.fromisoformat(row.recorded_at),
        )

    @staticmethod
    def _add_event(
        session: Session,
        aggregate_id: str,
        event_type: str,
        payload: dict[str, Any],
    ) -> None:
        session.add(
            OutboxRow(
                id=str(new_id()),
                schema_version="1.0",
                aggregate_id=aggregate_id,
                entity_type="run",
                run_id=aggregate_id,
                task_id=(str(payload["task_id"]) if payload.get("task_id") else None),
                identity_id=None,
                team_id=None,
                security_relevant=event_type.startswith("security."),
                event_type=event_type,
                source="mishkan.runtime",
                payload=json.dumps(payload, sort_keys=True, separators=(",", ":")),
                occurred_at=datetime.isoformat(utc_now()),
                command_id=None,
                correlation_id=None,
                causation_id=None,
                sensitivity="internal",
                published_at=None,
            )
        )
