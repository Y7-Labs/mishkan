"""Transactional SQLite authority for MCP connections and external calls."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from typing import ClassVar
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from mishkan.domain.errors import ErrorCode, MishkanError
from mishkan.domain.time import utc_now
from mishkan.mcp.models import (
    McpCallRequest,
    McpCallResult,
    McpCallState,
    McpConnectionRecord,
    McpDiscoverySnapshot,
    McpEffectDisposition,
    McpPrimitiveDescriptor,
    McpPrimitiveKind,
    McpProgressEvent,
)
from mishkan.persistence.migration import SchemaManager
from mishkan.persistence.runtime import DatabaseTarget
from mishkan.persistence.sqlite import (
    McpCallRow,
    McpConnectionRow,
    McpPrimitiveRow,
    McpProgressRow,
    create_local_engine,
)


@dataclass(frozen=True, slots=True)
class McpCallReservation:
    request: McpCallRequest
    created: bool
    existing_result: McpCallResult | None = None


@dataclass(frozen=True, slots=True)
class McpRemoteTaskBinding:
    request: McpCallRequest
    remote_task_id: str


class McpRepository:
    _CALL_TRANSITIONS: ClassVar[dict[McpCallState, frozenset[McpCallState]]] = {
        McpCallState.RESERVED: frozenset(
            {
                McpCallState.DISPATCHING,
                McpCallState.CANCEL_REQUESTED,
                McpCallState.CANCELLED,
                McpCallState.FAILED,
            }
        ),
        McpCallState.DISPATCHING: frozenset(
            {
                McpCallState.RUNNING,
                McpCallState.CANCEL_REQUESTED,
                McpCallState.COMPLETED,
                McpCallState.FAILED,
                McpCallState.LOST,
                McpCallState.UNCERTAIN,
            }
        ),
        McpCallState.RUNNING: frozenset(
            {
                McpCallState.CANCEL_REQUESTED,
                McpCallState.COMPLETED,
                McpCallState.FAILED,
                McpCallState.LOST,
                McpCallState.UNCERTAIN,
            }
        ),
        McpCallState.CANCEL_REQUESTED: frozenset(
            {
                McpCallState.CANCELLED,
                McpCallState.COMPLETED,
                McpCallState.FAILED,
                McpCallState.LOST,
                McpCallState.UNCERTAIN,
            }
        ),
    }
    _FINAL_CALL_STATES: ClassVar[frozenset[McpCallState]] = frozenset(
        {
            McpCallState.COMPLETED,
            McpCallState.FAILED,
            McpCallState.CANCELLED,
            McpCallState.LOST,
            McpCallState.UNCERTAIN,
        }
    )

    def __init__(self, database: DatabaseTarget, *, busy_timeout_ms: int = 5_000) -> None:
        SchemaManager(database).require_current()
        self._engine = create_local_engine(database, busy_timeout_ms=busy_timeout_ms)

    def create_connection(self, record: McpConnectionRecord) -> McpConnectionRecord:
        with Session(self._engine) as session, session.begin():
            existing = session.scalar(
                select(McpConnectionRow).where(
                    McpConnectionRow.connection_id == record.connection_id
                )
            )
            if existing is not None:
                raise MishkanError(ErrorCode.MCP, "MCP connection identity already exists")
            session.add(self._connection_row(record))
        return record

    def get_connection(self, connection_id: str) -> McpConnectionRecord:
        with Session(self._engine) as session:
            row = session.scalar(
                select(McpConnectionRow).where(McpConnectionRow.connection_id == connection_id)
            )
            if row is None:
                raise MishkanError(ErrorCode.MCP, "MCP connection does not exist")
            return self._connection(row)

    def find_connection(self, connection_id: str) -> McpConnectionRecord | None:
        with Session(self._engine) as session:
            row = session.scalar(
                select(McpConnectionRow).where(McpConnectionRow.connection_id == connection_id)
            )
            return self._connection(row) if row is not None else None

    def list_connections(
        self,
        *,
        offset: int = 0,
        limit: int = 100,
    ) -> tuple[McpConnectionRecord, ...]:
        self._query_bound(offset, limit)
        with Session(self._engine) as session:
            rows = session.scalars(
                select(McpConnectionRow)
                .order_by(McpConnectionRow.connection_id)
                .offset(offset)
                .limit(limit)
            ).all()
        return tuple(self._connection(row) for row in rows)

    def update_connection(
        self,
        record: McpConnectionRecord,
        *,
        expected_revision: int,
    ) -> McpConnectionRecord:
        with Session(self._engine) as session, session.begin():
            row = session.scalar(
                select(McpConnectionRow).where(
                    McpConnectionRow.connection_id == record.connection_id
                )
            )
            if row is None or row.revision != expected_revision:
                raise MishkanError(ErrorCode.REVISION_MISMATCH, "MCP connection revision changed")
            if record.revision != expected_revision + 1:
                raise MishkanError(ErrorCode.REVISION_MISMATCH, "MCP revision must advance by one")
            row.state = record.state.value
            row.revision = record.revision
            row.schema_fingerprint = record.schema_fingerprint
            row.payload = record.model_dump_json()
            row.updated_at = record.updated_at.isoformat()
        return record

    def replace_discovery(
        self,
        snapshot: McpDiscoverySnapshot,
        *,
        expected_connection_revision: int,
        expected_schema_fingerprint: str | None,
    ) -> McpDiscoverySnapshot:
        with Session(self._engine) as session, session.begin():
            connection = session.scalar(
                select(McpConnectionRow).where(
                    McpConnectionRow.connection_id == snapshot.connection_id
                )
            )
            if connection is None or connection.revision != expected_connection_revision:
                raise MishkanError(ErrorCode.REVISION_MISMATCH, "MCP discovery revision changed")
            if (
                expected_schema_fingerprint is not None
                and snapshot.schema_fingerprint != expected_schema_fingerprint
            ):
                raise MishkanError(
                    ErrorCode.TOOL_DRIFT,
                    "MCP discovery differs from the bound schema fingerprint",
                    details={"connection_id": snapshot.connection_id},
                )
            current = self._connection(connection)
            if snapshot.protocol_version not in current.configured_protocol_versions:
                raise MishkanError(
                    ErrorCode.MCP,
                    "MCP discovery used an unconfigured protocol version",
                )
            session.execute(
                delete(McpPrimitiveRow).where(
                    McpPrimitiveRow.connection_id == snapshot.connection_id
                )
            )
            session.add_all(
                McpPrimitiveRow(
                    connection_id=item.connection_id,
                    kind=item.kind.value,
                    name=item.name,
                    schema_hash=item.schema_hash,
                    payload=item.model_dump_json(),
                    discovered_at=item.discovered_at.isoformat(),
                )
                for item in snapshot.primitives
            )
            now = utc_now()
            updated = current.model_copy(
                update={
                    "negotiated_protocol_version": snapshot.protocol_version,
                    "revision": expected_connection_revision + 1,
                    "schema_fingerprint": snapshot.schema_fingerprint,
                    "task_tool_calls_supported": snapshot.task_tool_calls_supported,
                    "task_cancellation_supported": snapshot.task_cancellation_supported,
                    "updated_at": now,
                }
            )
            connection.state = updated.state.value
            connection.revision = updated.revision
            connection.schema_fingerprint = updated.schema_fingerprint
            connection.payload = updated.model_dump_json()
            connection.updated_at = now.isoformat()
        return snapshot

    def list_primitives(self, connection_id: str) -> tuple[McpPrimitiveDescriptor, ...]:
        with Session(self._engine) as session:
            rows = session.scalars(
                select(McpPrimitiveRow)
                .where(McpPrimitiveRow.connection_id == connection_id)
                .order_by(McpPrimitiveRow.kind, McpPrimitiveRow.name)
            ).all()
        return tuple(McpPrimitiveDescriptor.model_validate_json(row.payload) for row in rows)

    def require_primitive(
        self,
        connection_id: str,
        name: str,
        schema_hash: str,
        *,
        kind: McpPrimitiveKind | None = None,
    ) -> McpPrimitiveDescriptor:
        with Session(self._engine) as session:
            query = select(McpPrimitiveRow).where(
                McpPrimitiveRow.connection_id == connection_id,
                McpPrimitiveRow.name == name,
                McpPrimitiveRow.schema_hash == schema_hash,
            )
            if kind is not None:
                query = query.where(McpPrimitiveRow.kind == kind.value)
            row = session.scalar(query)
            if row is None:
                raise MishkanError(ErrorCode.TOOL_DRIFT, "bound MCP primitive is unavailable")
            return McpPrimitiveDescriptor.model_validate_json(row.payload)

    def reserve_call(self, request: McpCallRequest) -> McpCallReservation:
        fingerprint = self._request_fingerprint(request)
        now = utc_now().isoformat()
        with Session(self._engine) as session, session.begin():
            row = session.scalar(
                select(McpCallRow).where(McpCallRow.idempotency_key == str(request.idempotency_key))
            )
            if row is not None:
                if row.request_fingerprint != fingerprint:
                    raise MishkanError(
                        ErrorCode.DUPLICATE_RESULT,
                        "MCP idempotency key has different request content",
                    )
                existing = (
                    McpCallResult.model_validate_json(row.result_payload)
                    if row.result_payload is not None
                    else None
                )
                return McpCallReservation(request, False, existing)
            primitive = session.scalar(
                select(McpPrimitiveRow).where(
                    McpPrimitiveRow.connection_id == request.connection_id,
                    McpPrimitiveRow.name == request.primitive_name,
                    McpPrimitiveRow.schema_hash == request.expected_schema_hash,
                )
            )
            if primitive is None:
                raise MishkanError(
                    ErrorCode.TOOL_DRIFT,
                    "bound MCP primitive is unavailable",
                )
            session.add(
                McpCallRow(
                    id=str(request.id),
                    idempotency_key=str(request.idempotency_key),
                    request_fingerprint=fingerprint,
                    connection_id=request.connection_id,
                    primitive_name=request.primitive_name,
                    state=McpCallState.RESERVED.value,
                    request_payload=request.model_dump_json(),
                    result_payload=None,
                    remote_task_id=None,
                    created_at=now,
                    updated_at=now,
                )
            )
        return McpCallReservation(request, True)

    def set_call_state(self, request_id: UUID, state: McpCallState) -> None:
        with Session(self._engine) as session, session.begin():
            row = session.get(McpCallRow, str(request_id))
            if row is None or row.result_payload is not None:
                raise MishkanError(ErrorCode.MCP, "MCP call cannot change state")
            current = McpCallState(row.state)
            if state not in self._CALL_TRANSITIONS.get(current, frozenset()):
                raise MishkanError(
                    ErrorCode.REVISION_MISMATCH,
                    "MCP call transition is not permitted",
                    details={"current": current.value, "requested": state.value},
                )
            row.state = state.value
            row.updated_at = utc_now().isoformat()

    def attach_remote_task(self, request_id: UUID, remote_task_id: str) -> None:
        if not remote_task_id:
            raise MishkanError(ErrorCode.MCP, "remote MCP task identity is empty")
        with Session(self._engine) as session, session.begin():
            row = session.get(McpCallRow, str(request_id))
            if row is None or row.result_payload is not None:
                raise MishkanError(ErrorCode.MCP, "MCP call cannot bind a remote task")
            if McpCallState(row.state) not in {
                McpCallState.DISPATCHING,
                McpCallState.RUNNING,
                McpCallState.CANCEL_REQUESTED,
            }:
                raise MishkanError(ErrorCode.REVISION_MISMATCH, "MCP call is not dispatching")
            if row.remote_task_id is not None and row.remote_task_id != remote_task_id:
                raise MishkanError(ErrorCode.DUPLICATE_RESULT, "MCP remote task identity changed")
            row.remote_task_id = remote_task_id
            row.updated_at = utc_now().isoformat()

    def get_remote_task(self, request_id: UUID) -> McpRemoteTaskBinding:
        with Session(self._engine) as session:
            row = session.get(McpCallRow, str(request_id))
            if row is None or row.result_payload is not None or row.remote_task_id is None:
                raise MishkanError(ErrorCode.MCP, "recoverable MCP remote task does not exist")
            return McpRemoteTaskBinding(
                McpCallRequest.model_validate_json(row.request_payload),
                row.remote_task_id,
            )

    def call_connection_id(self, request_id: UUID) -> str:
        with Session(self._engine) as session:
            row = session.get(McpCallRow, str(request_id))
            if row is None:
                raise MishkanError(ErrorCode.MCP, "MCP call does not exist")
            return row.connection_id

    def complete_call(self, result: McpCallResult) -> McpCallResult:
        with Session(self._engine) as session, session.begin():
            row = session.get(McpCallRow, str(result.request_id))
            if row is None:
                raise MishkanError(ErrorCode.MCP, "MCP call reservation is missing")
            if row.result_payload is not None:
                existing = McpCallResult.model_validate_json(row.result_payload)
                if existing != result:
                    raise MishkanError(ErrorCode.DUPLICATE_RESULT, "MCP completion differs")
                return existing
            if (
                row.connection_id != result.connection_id
                or row.primitive_name != result.primitive_name
            ):
                raise MishkanError(ErrorCode.MCP, "MCP result identity differs from request")
            if row.remote_task_id is not None and result.remote_task_id != row.remote_task_id:
                raise MishkanError(ErrorCode.MCP, "MCP result remote task identity differs")
            current = McpCallState(row.state)
            if result.state not in self._FINAL_CALL_STATES:
                raise MishkanError(ErrorCode.MCP, "MCP completion state is not final")
            if result.state not in self._CALL_TRANSITIONS.get(current, frozenset()):
                raise MishkanError(
                    ErrorCode.REVISION_MISMATCH,
                    "MCP completion transition is not permitted",
                )
            row.state = result.state.value
            row.result_payload = result.model_dump_json()
            row.remote_task_id = result.remote_task_id
            row.updated_at = result.completed_at.isoformat()
        return result

    def append_progress(
        self,
        progress: McpProgressEvent,
        *,
        max_events: int,
    ) -> McpProgressEvent:
        if max_events < 1:
            raise MishkanError(ErrorCode.OUTPUT_CONTRACT, "MCP progress bound is invalid")
        with Session(self._engine) as session, session.begin():
            call = session.get(McpCallRow, str(progress.request_id))
            if call is None:
                raise MishkanError(ErrorCode.MCP, "MCP progress call does not exist")
            if (
                call.result_payload is not None
                or McpCallState(call.state) in self._FINAL_CALL_STATES
            ):
                raise MishkanError(ErrorCode.MCP, "completed MCP call rejects progress")
            latest = session.scalar(
                select(func.max(McpProgressRow.cursor)).where(
                    McpProgressRow.request_id == str(progress.request_id)
                )
            )
            expected = 0 if latest is None else latest + 1
            if progress.cursor != expected:
                raise MishkanError(ErrorCode.REVISION_MISMATCH, "MCP progress cursor is not next")
            if expected >= max_events:
                raise MishkanError(
                    ErrorCode.MCP,
                    "MCP progress exceeds its configured per-call bound",
                    details={"event_limit": max_events},
                )
            session.add(
                McpProgressRow(
                    request_id=str(progress.request_id),
                    cursor=progress.cursor,
                    payload=progress.model_dump_json(),
                    created_at=progress.created_at.isoformat(),
                )
            )
        return progress

    def progress_after(
        self,
        request_id: UUID,
        cursor: int,
        *,
        limit: int,
    ) -> tuple[McpProgressEvent, ...]:
        if cursor < 0 or limit < 1:
            raise MishkanError(ErrorCode.OUTPUT_CONTRACT, "MCP progress cursor is invalid")
        with Session(self._engine) as session:
            rows = session.scalars(
                select(McpProgressRow)
                .where(
                    McpProgressRow.request_id == str(request_id),
                    McpProgressRow.cursor >= cursor,
                )
                .order_by(McpProgressRow.cursor)
                .limit(limit)
            ).all()
        return tuple(McpProgressEvent.model_validate_json(row.payload) for row in rows)

    def prune_progress(self, *, before: datetime, batch_size: int) -> int:
        if before.tzinfo is None or before.utcoffset() is None or batch_size < 1:
            raise MishkanError(ErrorCode.OUTPUT_CONTRACT, "MCP progress retention bound is invalid")
        with Session(self._engine) as session, session.begin():
            identities = tuple(
                session.execute(
                    select(McpProgressRow.request_id, McpProgressRow.cursor)
                    .where(McpProgressRow.created_at < before.isoformat())
                    .order_by(McpProgressRow.created_at)
                    .limit(batch_size)
                ).all()
            )
            for request_id, cursor in identities:
                session.execute(
                    delete(McpProgressRow).where(
                        McpProgressRow.request_id == request_id,
                        McpProgressRow.cursor == cursor,
                    )
                )
        return len(identities)

    def list_calls(
        self,
        *,
        offset: int = 0,
        limit: int = 100,
    ) -> tuple[dict[str, object], ...]:
        self._query_bound(offset, limit)
        with Session(self._engine) as session:
            rows = session.scalars(
                select(McpCallRow)
                .order_by(McpCallRow.created_at, McpCallRow.id)
                .offset(offset)
                .limit(limit)
            ).all()
        return tuple(
            {
                "request": McpCallRequest.model_validate_json(row.request_payload).model_dump(
                    mode="json"
                ),
                "state": row.state,
                "result": (
                    McpCallResult.model_validate_json(row.result_payload).model_dump(mode="json")
                    if row.result_payload is not None
                    else None
                ),
                "remote_task_id": row.remote_task_id,
                "created_at": row.created_at,
                "updated_at": row.updated_at,
            }
            for row in rows
        )

    def reconcile_incomplete(self) -> tuple[McpCallResult, ...]:
        with Session(self._engine) as session:
            rows = session.scalars(
                select(McpCallRow).where(
                    McpCallRow.state.in_(
                        (
                            McpCallState.DISPATCHING.value,
                            McpCallState.RUNNING.value,
                            McpCallState.CANCEL_REQUESTED.value,
                        )
                    ),
                    McpCallRow.result_payload.is_(None),
                    McpCallRow.remote_task_id.is_(None),
                )
            ).all()
        reconciled: list[McpCallResult] = []
        for row in rows:
            try:
                request = McpCallRequest.model_validate_json(row.request_payload)
            except ValidationError as exc:
                raise MishkanError(ErrorCode.MCP, "MCP call request journal is corrupt") from exc
            uncertain = request.effect_disposition in {
                McpEffectDisposition.NON_IDEMPOTENT,
                McpEffectDisposition.UNKNOWN,
            }
            result = McpCallResult(
                request_id=request.id,
                connection_id=request.connection_id,
                primitive_name=request.primitive_name,
                state=McpCallState.UNCERTAIN if uncertain else McpCallState.LOST,
                schema_hash=request.expected_schema_hash,
                error_code=ErrorCode.MCP,
                reason=(
                    "daemon restarted after an indeterminate external MCP effect"
                    if uncertain
                    else "daemon restarted before an idempotent MCP result was accepted"
                ),
            )
            reconciled.append(self.complete_call(result))
        return tuple(reconciled)

    @staticmethod
    def _query_bound(offset: int, limit: int) -> None:
        if offset < 0 or limit < 1 or limit > 1_000:
            raise MishkanError(ErrorCode.OUTPUT_CONTRACT, "MCP query bound is invalid")

    @staticmethod
    def _request_fingerprint(request: McpCallRequest) -> str:
        payload = json.dumps(request.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode()).hexdigest()

    @staticmethod
    def _connection_row(record: McpConnectionRecord) -> McpConnectionRow:
        return McpConnectionRow(
            id=str(record.id),
            connection_id=record.connection_id,
            direction=record.direction.value,
            transport=record.transport.value,
            state=record.state.value,
            revision=record.revision,
            schema_fingerprint=record.schema_fingerprint,
            payload=record.model_dump_json(),
            created_at=record.created_at.isoformat(),
            updated_at=record.updated_at.isoformat(),
        )

    @staticmethod
    def _connection(row: McpConnectionRow) -> McpConnectionRecord:
        try:
            payload = json.loads(row.payload)
            if not isinstance(payload, dict):
                raise ValueError("MCP connection payload is not an object")
            payload.setdefault(
                "server_identity",
                f"legacy:{row.connection_id}:unrecorded",
            )
            payload.setdefault("policy_fingerprint", "legacy:unrecorded")
            return McpConnectionRecord.model_validate(payload)
        except (ValidationError, ValueError, json.JSONDecodeError) as exc:
            raise MishkanError(ErrorCode.MCP, "MCP connection record is corrupt") from exc
