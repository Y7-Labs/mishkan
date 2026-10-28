"""Transactional SQLite repository for environment evidence and decisions."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Protocol, TypeVar

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from mishkan.artifacts import ArtifactLifecycle, ArtifactManifest
from mishkan.domain.errors import ErrorCode, MishkanError
from mishkan.domain.identity import new_id
from mishkan.domain.time import utc_now
from mishkan.environment.models import (
    EnvironmentAttempt,
    EnvironmentBinding,
    EnvironmentBindingState,
    EnvironmentDescriptorSet,
    EnvironmentInvalidation,
    EnvironmentObservation,
    EnvironmentVerification,
)
from mishkan.persistence.migration import SchemaManager
from mishkan.persistence.runtime import DatabaseTarget
from mishkan.persistence.sqlite import (
    EnvironmentAttemptRow,
    EnvironmentBindingRow,
    EnvironmentDescriptorSetRow,
    EnvironmentInvalidationRow,
    EnvironmentObservationRow,
    EnvironmentVerificationRow,
    OutboxRow,
    create_local_engine,
)

RecordT = TypeVar("RecordT", bound=BaseModel)


class ArtifactLookup(Protocol):
    def manifest(self, reference: str) -> ArtifactManifest: ...

    def read_bytes(self, reference: str) -> bytes: ...


class SQLiteEnvironmentRepository:
    def __init__(
        self,
        database_path: DatabaseTarget,
        *,
        artifacts: ArtifactLookup,
        busy_timeout_ms: int = 5_000,
    ) -> None:
        SchemaManager(database_path).require_current()
        self._engine = create_local_engine(database_path, busy_timeout_ms=busy_timeout_ms)
        self._artifacts = artifacts

    def record_observation(self, record: EnvironmentObservation) -> EnvironmentObservation:
        payload = self._json(record)
        with Session(self._engine) as session, session.begin():
            existing = session.get(EnvironmentObservationRow, str(record.observation_id))
            if existing is not None:
                return self._idempotent(existing.payload, payload, record)
            session.add(
                EnvironmentObservationRow(
                    id=str(record.observation_id),
                    context_id=record.context_id,
                    revision=record.revision,
                    fingerprint=record.fingerprint,
                    payload=payload,
                    observed_at=record.observed_at.isoformat(),
                )
            )
            self._event(
                session,
                aggregate_id=str(record.observation_id),
                context_id=record.context_id,
                event_type="environment.observed",
                payload={
                    "observation_id": str(record.observation_id),
                    "revision": record.revision,
                    "fingerprint": record.fingerprint,
                },
            )
        return record

    def observation(self, observation_id: str) -> EnvironmentObservation:
        with Session(self._engine) as session:
            row = session.get(EnvironmentObservationRow, observation_id)
            if row is None:
                raise MishkanError(ErrorCode.ENGINEERING, "environment observation does not exist")
            return EnvironmentObservation.model_validate_json(row.payload)

    def artifact_manifest(self, reference: str) -> ArtifactManifest:
        """Resolve immutable descriptor content through the configured artifact authority."""
        return self._artifacts.manifest(reference)

    def record_binding(self, record: EnvironmentBinding) -> EnvironmentBinding:
        payload = self._json(record)
        with Session(self._engine) as session, session.begin():
            observation = session.get(
                EnvironmentObservationRow,
                str(record.request.observation_id),
            )
            if observation is None:
                raise MishkanError(ErrorCode.ENGINEERING, "binding observation does not exist")
            if (
                observation.revision != record.request.observation_revision
                or observation.fingerprint != record.request.observation_fingerprint
            ):
                raise MishkanError(
                    ErrorCode.REVISION_MISMATCH,
                    "environment observation changed before binding persistence",
                )
            same_request = session.scalar(
                select(EnvironmentBindingRow).where(
                    EnvironmentBindingRow.request_id == str(record.request.request_id)
                )
            )
            if same_request is not None:
                existing_record = EnvironmentBinding.model_validate_json(same_request.payload)
                if existing_record.request != record.request:
                    raise MishkanError(
                        ErrorCode.DUPLICATE_RESULT,
                        "environment binding request identity contains different content",
                    )
                return existing_record
            existing = session.get(EnvironmentBindingRow, str(record.binding_id))
            if existing is not None:
                return self._idempotent(existing.payload, payload, record)
            session.add(
                EnvironmentBindingRow(
                    id=str(record.binding_id),
                    request_id=str(record.request.request_id),
                    observation_id=str(record.request.observation_id),
                    context_id=record.request.context_id,
                    state=record.state.value,
                    revision=record.revision,
                    payload=payload,
                    resolved_at=record.resolved_at.isoformat(),
                )
            )
            self._event(
                session,
                aggregate_id=str(record.binding_id),
                context_id=record.request.context_id,
                event_type=f"environment.binding_{record.state.value}",
                payload={
                    "binding_id": str(record.binding_id),
                    "request_id": str(record.request.request_id),
                    "state": record.state.value,
                    "policy_fingerprint": record.request.policy_fingerprint,
                },
            )
        return record

    def binding(self, binding_id: str) -> EnvironmentBinding:
        with Session(self._engine) as session:
            row = session.get(EnvironmentBindingRow, binding_id)
            if row is None:
                raise MishkanError(ErrorCode.ENGINEERING, "environment binding does not exist")
            binding = EnvironmentBinding.model_validate_json(row.payload)
            invalidation = session.scalar(
                select(EnvironmentInvalidationRow).where(
                    EnvironmentInvalidationRow.binding_id == binding_id
                )
            )
            if invalidation is None:
                return binding
            record = EnvironmentInvalidation.model_validate_json(invalidation.payload)
            return binding.model_copy(
                update={
                    "revision": binding.revision + 1,
                    "state": EnvironmentBindingState.STALE,
                    "selected_engine_ids": (),
                    "selected_adapter_ids": (),
                    "selected_descriptors": (),
                    "missing_conditions": (f"invalidation:{record.cause.value}",),
                    "reason": record.reason,
                }
            )

    def binding_for_request(self, request_id: str) -> EnvironmentBinding | None:
        with Session(self._engine) as session:
            row = session.scalar(
                select(EnvironmentBindingRow).where(EnvironmentBindingRow.request_id == request_id)
            )
            if row is None:
                return None
        return self.binding(row.id)

    def verifications_for_binding(
        self,
        binding_id: str,
        *,
        limit: int = 1_000,
    ) -> tuple[EnvironmentVerification, ...]:
        with Session(self._engine) as session:
            rows = session.scalars(
                select(EnvironmentVerificationRow)
                .where(EnvironmentVerificationRow.binding_id == binding_id)
                .order_by(EnvironmentVerificationRow.recorded_at)
                .limit(limit)
            )
            return tuple(EnvironmentVerification.model_validate_json(row.payload) for row in rows)

    def invalidate(self, record: EnvironmentInvalidation) -> EnvironmentInvalidation:
        payload = self._json(record)
        with Session(self._engine) as session, session.begin():
            row = session.get(EnvironmentBindingRow, str(record.binding_id))
            if row is None:
                raise MishkanError(ErrorCode.ENGINEERING, "environment binding does not exist")
            binding = EnvironmentBinding.model_validate_json(row.payload)
            if binding.revision != record.expected_binding_revision:
                raise MishkanError(
                    ErrorCode.REVISION_MISMATCH,
                    "environment binding revision changed before invalidation",
                )
            if binding.request.owner_identity != record.owner_identity:
                raise MishkanError(
                    ErrorCode.AUTHORITY_NOT_GRANTED,
                    "environment invalidation owner differs from the binding owner",
                )
            if set(record.affected_task_ids) != set(binding.request.affected_task_ids):
                raise MishkanError(
                    ErrorCode.OUTPUT_CONTRACT,
                    "environment invalidation must identify exactly the dependent tasks",
                )
            observation = session.get(
                EnvironmentObservationRow,
                str(binding.request.observation_id),
            )
            if observation is None:
                raise MishkanError(ErrorCode.ENGINEERING, "binding observation does not exist")
            if (
                record.cause.value == "context"
                and record.observed_context_fingerprint == observation.fingerprint
            ):
                raise MishkanError(
                    ErrorCode.OUTPUT_CONTRACT,
                    "context invalidation requires evidence of a changed context",
                )
            existing = session.scalar(
                select(EnvironmentInvalidationRow).where(
                    EnvironmentInvalidationRow.binding_id == str(record.binding_id)
                )
            )
            if existing is not None:
                return self._idempotent(existing.payload, payload, record)
            session.add(
                EnvironmentInvalidationRow(
                    invalidation_id=str(record.invalidation_id),
                    binding_id=str(record.binding_id),
                    payload=payload,
                    recorded_at=record.invalidated_at.isoformat(),
                )
            )
            self._event(
                session,
                aggregate_id=str(record.binding_id),
                context_id=binding.request.context_id,
                event_type="environment.binding_invalidated",
                payload={
                    "binding_id": str(record.binding_id),
                    "invalidation_id": str(record.invalidation_id),
                    "cause": record.cause.value,
                    "affected_task_ids": list(record.affected_task_ids),
                    "policy_fingerprint": record.policy_fingerprint,
                },
            )
        return record

    def record_descriptor_set(self, record: EnvironmentDescriptorSet) -> EnvironmentDescriptorSet:
        for member in record.members:
            self._require_available_artifact(member.artifact_reference)
        return self._record_child(
            record,
            row_type=EnvironmentDescriptorSetRow,
            identity_field="descriptor_set_id",
            identity=str(record.descriptor_set_id),
            binding_id=str(record.binding_id),
            event_type="environment.descriptor_set_recorded",
        )

    def record_attempt(self, record: EnvironmentAttempt) -> EnvironmentAttempt:
        for reference in record.artifact_references:
            self._require_available_artifact(reference)
        return self._record_child(
            record,
            row_type=EnvironmentAttemptRow,
            identity_field="attempt_id",
            identity=str(record.attempt_id),
            binding_id=str(record.binding_id),
            event_type=f"environment.attempt_{record.settlement.value}",
        )

    def record_verification(self, record: EnvironmentVerification) -> EnvironmentVerification:
        for reference in record.artifact_references:
            self._require_available_artifact(reference)
        return self._record_child(
            record,
            row_type=EnvironmentVerificationRow,
            identity_field="verification_id",
            identity=str(record.verification_id),
            binding_id=str(record.binding_id),
            event_type=f"environment.verification_{record.settlement.value}",
        )

    def descriptor_set(self, descriptor_set_id: str) -> EnvironmentDescriptorSet:
        return self._child(
            EnvironmentDescriptorSetRow,
            descriptor_set_id,
            EnvironmentDescriptorSet,
        )

    def attempt(self, attempt_id: str) -> EnvironmentAttempt:
        return self._child(EnvironmentAttemptRow, attempt_id, EnvironmentAttempt)

    def verification(self, verification_id: str) -> EnvironmentVerification:
        return self._child(
            EnvironmentVerificationRow,
            verification_id,
            EnvironmentVerification,
        )

    def _require_available_artifact(self, reference: str) -> ArtifactManifest:
        manifest = self._artifacts.manifest(reference)
        if manifest.lifecycle is not ArtifactLifecycle.AVAILABLE:
            raise MishkanError(
                ErrorCode.ARTIFACT,
                "environment evidence requires an available immutable artifact",
                details={"reference": reference, "lifecycle": manifest.lifecycle.value},
            )
        content = self._artifacts.read_bytes(reference)
        digest = f"sha256:{hashlib.sha256(content).hexdigest()}"
        if len(content) != manifest.size_bytes or digest != manifest.digest:
            raise MishkanError(
                ErrorCode.ARTIFACT,
                "environment evidence artifact failed size or digest verification",
                details={"reference": reference},
            )
        return manifest

    def _child(
        self,
        row_type: type[Any],
        identity: str,
        model: type[RecordT],
    ) -> RecordT:
        with Session(self._engine) as session:
            row = session.get(row_type, identity)
            if row is None:
                raise MishkanError(ErrorCode.ENGINEERING, "environment evidence does not exist")
            return model.model_validate_json(row.payload)

    def _record_child(
        self,
        record: RecordT,
        *,
        row_type: type[Any],
        identity_field: str,
        identity: str,
        binding_id: str,
        event_type: str,
    ) -> RecordT:
        payload = self._json(record)
        with Session(self._engine) as session, session.begin():
            binding_row = session.get(EnvironmentBindingRow, binding_id)
            if binding_row is None:
                raise MishkanError(ErrorCode.ENGINEERING, "child evidence binding does not exist")
            if EnvironmentBindingState(binding_row.state) is not EnvironmentBindingState.COMPATIBLE:
                raise MishkanError(
                    ErrorCode.ENGINEERING,
                    "environment evidence requires a compatible binding",
                )
            invalidation = session.scalar(
                select(EnvironmentInvalidationRow).where(
                    EnvironmentInvalidationRow.binding_id == binding_id
                )
            )
            if invalidation is not None:
                raise MishkanError(
                    ErrorCode.ENGINEERING,
                    "environment evidence cannot be recorded after binding invalidation",
                )
            existing = session.get(row_type, identity)
            if existing is not None:
                return self._idempotent(existing.payload, payload, record)
            session.add(
                row_type(
                    **{
                        identity_field: identity,
                        "binding_id": binding_id,
                        "payload": payload,
                        "recorded_at": utc_now().isoformat(),
                    }
                )
            )
            self._event(
                session,
                aggregate_id=identity,
                context_id=binding_id,
                event_type=event_type,
                payload={"record_id": identity, "binding_id": binding_id},
            )
        return record

    @staticmethod
    def _idempotent(existing: str, requested: str, record: RecordT) -> RecordT:
        if existing != requested:
            raise MishkanError(
                ErrorCode.DUPLICATE_RESULT,
                "environment evidence identity already contains different content",
            )
        return record

    @staticmethod
    def _json(record: BaseModel) -> str:
        return json.dumps(record.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))

    @staticmethod
    def _event(
        session: Session,
        *,
        aggregate_id: str,
        context_id: str,
        event_type: str,
        payload: dict[str, object],
    ) -> None:
        session.add(
            OutboxRow(
                id=str(new_id()),
                schema_version="1.0",
                aggregate_id=aggregate_id,
                entity_type="environment",
                run_id=None,
                task_id=None,
                identity_id=None,
                team_id="Engineering",
                security_relevant=False,
                event_type=event_type,
                source="mishkan.environment",
                payload=json.dumps(payload, sort_keys=True, separators=(",", ":")),
                occurred_at=utc_now().isoformat(),
                command_id=None,
                correlation_id=None,
                causation_id=None,
                sensitivity="internal",
                published_at=None,
            )
        )
