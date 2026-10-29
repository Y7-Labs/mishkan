"""Durable, policy-mediated lifecycle for registry sources and entries."""

from __future__ import annotations

import hashlib
import json
from typing import Any
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from mishkan.domain.errors import ErrorCode, MishkanError
from mishkan.domain.identity import new_id
from mishkan.domain.time import utc_now
from mishkan.persistence.migration import SchemaManager
from mishkan.persistence.runtime import DatabaseTarget
from mishkan.persistence.sqlite import ToolRegistryEntryRow, create_local_engine
from mishkan.tools.models import (
    AdapterDescriptor,
    RegistryEntry,
    RegistryEntryKind,
    RegistryLifecycleAction,
    RegistryLifecycleProjection,
    RegistryMutation,
    ToolContract,
    ToolsetDefinition,
    ToolSourceIndex,
)


class ToolRegistryLifecycle:
    """Validate and persist lifecycle overrides without mutating old snapshots."""

    def __init__(self, database: DatabaseTarget, *, busy_timeout_ms: int = 5_000) -> None:
        SchemaManager(database).require_current()
        self._engine = create_local_engine(database, busy_timeout_ms=busy_timeout_ms)

    def mutate(
        self,
        session: Session,
        mutation: RegistryMutation,
        *,
        revision: int,
    ) -> RegistryEntry:
        definition, fingerprint = self._validated_definition(mutation)
        key = (mutation.entry_kind.value, mutation.identity)
        row = session.get(ToolRegistryEntryRow, key)
        action = mutation.action
        if action is RegistryLifecycleAction.ADD and row is not None and not row.removed:
            raise MishkanError(
                ErrorCode.REVISION_MISMATCH,
                "registry entry already exists",
                details={"entry_kind": key[0], "identity": key[1]},
            )
        if action is RegistryLifecycleAction.UPDATE and (row is None or row.removed):
            raise MishkanError(
                ErrorCode.REVISION_MISMATCH,
                "registry entry cannot be updated before it is added",
                details={"entry_kind": key[0], "identity": key[1]},
            )

        now = utc_now().isoformat()
        if row is None:
            row = ToolRegistryEntryRow(
                record_id=str(new_id()),
                entry_kind=key[0],
                identity=key[1],
                enabled=True,
                removed=False,
                precedence=0,
                revision=revision,
                definition_payload=None,
                definition_fingerprint=None,
                updated_at=now,
            )
            session.add(row)

        if action in {RegistryLifecycleAction.ADD, RegistryLifecycleAction.UPDATE}:
            assert definition is not None and fingerprint is not None
            row.definition_payload = json.dumps(definition, sort_keys=True, separators=(",", ":"))
            row.definition_fingerprint = fingerprint
            row.enabled = True
            row.removed = False
        elif action is RegistryLifecycleAction.ENABLE:
            row.enabled = True
            row.removed = False
        elif action is RegistryLifecycleAction.DISABLE:
            row.enabled = False
        elif action is RegistryLifecycleAction.REMOVE:
            row.enabled = False
            row.removed = True
        elif action is RegistryLifecycleAction.SET_PRECEDENCE:
            assert mutation.precedence is not None
            row.precedence = mutation.precedence
        row.revision = revision
        row.updated_at = now
        session.flush()
        return self._entry(row)

    def entries(self, *, limit: int = 1_000) -> tuple[RegistryEntry, ...]:
        if limit < 1 or limit > 10_000:
            raise MishkanError(ErrorCode.OUTPUT_CONTRACT, "registry query limit is out of bounds")
        with Session(self._engine) as session:
            rows = session.scalars(
                select(ToolRegistryEntryRow)
                .order_by(ToolRegistryEntryRow.entry_kind, ToolRegistryEntryRow.identity)
                .limit(limit)
            ).all()
        return tuple(self._entry(row) for row in rows)

    def projection(self) -> RegistryLifecycleProjection:
        source_indices: list[ToolSourceIndex] = []
        tool_contracts: list[ToolContract] = []
        toolsets: list[ToolsetDefinition] = []
        disabled: dict[RegistryEntryKind, set[str]] = {kind: set() for kind in RegistryEntryKind}
        source_precedence: dict[str, int] = {}
        for entry in self.entries(limit=10_000):
            if entry.entry_kind is RegistryEntryKind.SOURCE:
                source_precedence[entry.identity] = entry.precedence
            if not entry.enabled or entry.removed:
                disabled[entry.entry_kind].add(entry.identity)
                continue
            if entry.definition is None:
                continue
            model = self._definition_model(entry.entry_kind)
            parsed = model.model_validate(entry.definition)
            if isinstance(parsed, ToolSourceIndex):
                source_indices.append(parsed)
            elif isinstance(parsed, ToolContract):
                tool_contracts.append(parsed)
            elif isinstance(parsed, ToolsetDefinition):
                toolsets.append(parsed)
        return RegistryLifecycleProjection(
            source_indices=tuple(source_indices),
            tool_contracts=tuple(tool_contracts),
            toolsets=tuple(toolsets),
            disabled_sources=frozenset(disabled[RegistryEntryKind.SOURCE]),
            disabled_adapters=frozenset(disabled[RegistryEntryKind.ADAPTER]),
            disabled_tools=frozenset(disabled[RegistryEntryKind.TOOL]),
            disabled_toolsets=frozenset(disabled[RegistryEntryKind.TOOLSET]),
            source_precedence=source_precedence,
        )

    @classmethod
    def _validated_definition(
        cls, mutation: RegistryMutation
    ) -> tuple[dict[str, Any] | None, str | None]:
        if mutation.definition is None:
            return None, None
        model = cls._definition_model(mutation.entry_kind)
        try:
            parsed = model.model_validate(mutation.definition)
        except ValidationError as exc:
            raise MishkanError(
                ErrorCode.TOOL_CONTRACT,
                "registry lifecycle definition is invalid",
                details={
                    "entry_kind": mutation.entry_kind.value,
                    "identity": mutation.identity,
                    "violations": len(exc.errors()),
                },
            ) from exc
        actual_identity = {
            RegistryEntryKind.SOURCE: "source_id",
            RegistryEntryKind.ADAPTER: "adapter_id",
            RegistryEntryKind.TOOL: "tool_id",
            RegistryEntryKind.TOOLSET: "toolset_id",
        }[mutation.entry_kind]
        if getattr(parsed, actual_identity) != mutation.identity:
            raise MishkanError(
                ErrorCode.TOOL_CONTRACT,
                "registry entry identity differs from its typed definition",
                details={"identity": mutation.identity, "identity_field": actual_identity},
            )
        definition = parsed.model_dump(mode="json")
        encoded = json.dumps(definition, sort_keys=True, separators=(",", ":")).encode()
        return definition, hashlib.sha256(encoded).hexdigest()

    @staticmethod
    def _definition_model(
        kind: RegistryEntryKind,
    ) -> (
        type[ToolSourceIndex]
        | type[AdapterDescriptor]
        | type[ToolContract]
        | type[ToolsetDefinition]
    ):
        if kind is RegistryEntryKind.SOURCE:
            return ToolSourceIndex
        if kind is RegistryEntryKind.ADAPTER:
            return AdapterDescriptor
        if kind is RegistryEntryKind.TOOL:
            return ToolContract
        return ToolsetDefinition

    @staticmethod
    def _entry(row: ToolRegistryEntryRow) -> RegistryEntry:
        definition = json.loads(row.definition_payload) if row.definition_payload else None
        return RegistryEntry(
            id=UUID(row.record_id),
            entry_kind=RegistryEntryKind(row.entry_kind),
            identity=row.identity,
            enabled=row.enabled,
            removed=row.removed,
            precedence=row.precedence,
            revision=row.revision,
            definition=definition,
            definition_fingerprint=row.definition_fingerprint,
            updated_at=row.updated_at,
        )
