"""Transactional SQLite authority for attributed knowledge state."""

from __future__ import annotations

import json
from pathlib import Path
from uuid import UUID

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from mishkan.artifacts import ArtifactLifecycle
from mishkan.domain.errors import ErrorCode, MishkanError
from mishkan.domain.identity import new_id
from mishkan.domain.time import utc_now
from mishkan.knowledge.models import (
    KnowledgeBundle,
    KnowledgeCorpus,
    KnowledgeOperation,
    KnowledgeOperationState,
    KnowledgePromotion,
    KnowledgePromotionDisposition,
    KnowledgeQuery,
    KnowledgeQueryRecord,
    KnowledgeQueryState,
    KnowledgeSourceAttempt,
)
from mishkan.persistence.migration import SchemaManager
from mishkan.persistence.sqlite import (
    ArtifactRow,
    KnowledgeCorpusRow,
    KnowledgeOperationRow,
    KnowledgePromotionRow,
    KnowledgeQueryRow,
    KnowledgeSourceAttemptRow,
    OutboxRow,
    create_local_engine,
)

_OPERATION_TRANSITIONS: dict[KnowledgeOperationState, frozenset[KnowledgeOperationState]] = {
    KnowledgeOperationState.QUEUED: frozenset(
        {
            KnowledgeOperationState.RUNNING,
            KnowledgeOperationState.CANCELLED,
            KnowledgeOperationState.FAILED,
        }
    ),
    KnowledgeOperationState.RUNNING: frozenset(
        {
            KnowledgeOperationState.SUCCEEDED,
            KnowledgeOperationState.FAILED,
            KnowledgeOperationState.CANCEL_REQUESTED,
            KnowledgeOperationState.UNCERTAIN,
        }
    ),
    KnowledgeOperationState.CANCEL_REQUESTED: frozenset(
        {
            KnowledgeOperationState.CANCELLED,
            KnowledgeOperationState.SUCCEEDED,
            KnowledgeOperationState.FAILED,
            KnowledgeOperationState.UNCERTAIN,
        }
    ),
    KnowledgeOperationState.UNCERTAIN: frozenset(
        {
            KnowledgeOperationState.SUCCEEDED,
            KnowledgeOperationState.FAILED,
            KnowledgeOperationState.CANCELLED,
        }
    ),
    KnowledgeOperationState.SUCCEEDED: frozenset(),
    KnowledgeOperationState.FAILED: frozenset(),
    KnowledgeOperationState.CANCELLED: frozenset(),
}


class SQLiteKnowledgeRepository:
    def __init__(self, database_path: Path, *, busy_timeout_ms: int = 5_000) -> None:
        SchemaManager(database_path).require_current()
        self._engine = create_local_engine(database_path, busy_timeout_ms=busy_timeout_ms)

    def start_query(self, query: KnowledgeQuery) -> KnowledgeQueryRecord:
        record = KnowledgeQueryRecord(query=query, state=KnowledgeQueryState.RUNNING, revision=1)
        payload = self._json(record)
        with Session(self._engine) as session, session.begin():
            existing = session.get(KnowledgeQueryRow, str(query.query_id))
            if existing is not None:
                durable = KnowledgeQueryRecord.model_validate_json(existing.payload)
                if durable.query.fingerprint != query.fingerprint:
                    raise MishkanError(
                        ErrorCode.DUPLICATE_RESULT,
                        "knowledge query identity already contains different content",
                    )
                return durable
            session.add(
                KnowledgeQueryRow(
                    id=str(query.query_id),
                    knowledge_class=query.knowledge_class.value,
                    project_id=query.scope.project_id,
                    repository_id=query.scope.repository_id,
                    state=record.state.value,
                    required=query.required,
                    revision=record.revision,
                    bundle_artifact_id=None,
                    payload=payload,
                    created_at=record.started_at.isoformat(),
                    completed_at=None,
                )
            )
            self._event(
                session,
                aggregate_id=str(query.query_id),
                event_type="knowledge.query_started",
                payload={
                    "query_id": str(query.query_id),
                    "knowledge_class": query.knowledge_class.value,
                    "project_id": query.scope.project_id,
                    "required": query.required,
                },
            )
        return record

    def record_attempt(self, attempt: KnowledgeSourceAttempt) -> KnowledgeSourceAttempt:
        payload = self._json(attempt)
        with Session(self._engine) as session, session.begin():
            if session.get(KnowledgeQueryRow, str(attempt.query_id)) is None:
                raise MishkanError(ErrorCode.CONTEXT, "knowledge attempt references no query")
            existing = session.get(KnowledgeSourceAttemptRow, str(attempt.attempt_id))
            if existing is not None:
                if existing.payload != payload:
                    raise MishkanError(
                        ErrorCode.DUPLICATE_RESULT,
                        "knowledge attempt identity already contains different content",
                    )
                return attempt
            session.add(
                KnowledgeSourceAttemptRow(
                    id=str(attempt.attempt_id),
                    query_id=str(attempt.query_id),
                    source_id=attempt.source_id,
                    state=attempt.state.value,
                    payload=payload,
                    recorded_at=attempt.recorded_at.isoformat(),
                )
            )
        return attempt

    def complete_query(self, bundle: KnowledgeBundle) -> KnowledgeQueryRecord:
        artifact_id = self._artifact_id(bundle.bundle_reference)
        state = KnowledgeQueryState.DEGRADED if bundle.degraded else KnowledgeQueryState.COMPLETED
        with Session(self._engine) as session, session.begin():
            row = self._require_query_row(session, bundle.query.query_id)
            durable = KnowledgeQueryRecord.model_validate_json(row.payload)
            if durable.state is not KnowledgeQueryState.RUNNING:
                if durable.bundle_reference == bundle.bundle_reference:
                    return durable
                raise MishkanError(
                    ErrorCode.REVISION_MISMATCH,
                    "knowledge query is already terminal",
                )
            artifact = session.get(ArtifactRow, artifact_id)
            if artifact is None or artifact.lifecycle != ArtifactLifecycle.AVAILABLE.value:
                raise MishkanError(ErrorCode.ARTIFACT, "knowledge bundle artifact is unavailable")
            completed = KnowledgeQueryRecord(
                query=bundle.query,
                state=state,
                revision=durable.revision + 1,
                bundle_reference=bundle.bundle_reference,
                limitation_codes=bundle.limitations,
                started_at=durable.started_at,
                completed_at=utc_now(),
            )
            row.state = completed.state.value
            row.revision = completed.revision
            row.bundle_artifact_id = artifact_id
            row.payload = self._json(completed)
            assert completed.completed_at is not None
            row.completed_at = completed.completed_at.isoformat()
            self._event(
                session,
                aggregate_id=str(bundle.query.query_id),
                event_type=(
                    "knowledge.query_degraded" if bundle.degraded else "knowledge.query_completed"
                ),
                payload={
                    "query_id": str(bundle.query.query_id),
                    "bundle_reference": bundle.bundle_reference,
                    "item_count": len(bundle.items),
                    "unavailable_sources": list(bundle.unavailable_sources),
                    "limitations": list(bundle.limitations),
                },
            )
            return completed

    def fail_query(
        self,
        query_id: UUID,
        *,
        state: KnowledgeQueryState,
        limitation_codes: tuple[str, ...],
    ) -> KnowledgeQueryRecord:
        if state not in {KnowledgeQueryState.FAILED, KnowledgeQueryState.CANCELLED}:
            raise MishkanError(ErrorCode.OUTPUT_CONTRACT, "invalid failed query terminal state")
        with Session(self._engine) as session, session.begin():
            row = self._require_query_row(session, query_id)
            durable = KnowledgeQueryRecord.model_validate_json(row.payload)
            if durable.state is not KnowledgeQueryState.RUNNING:
                return durable
            terminal = durable.model_copy(
                update={
                    "state": state,
                    "revision": durable.revision + 1,
                    "limitation_codes": limitation_codes,
                    "completed_at": utc_now(),
                }
            )
            row.state = terminal.state.value
            row.revision = terminal.revision
            row.payload = self._json(terminal)
            assert terminal.completed_at is not None
            row.completed_at = terminal.completed_at.isoformat()
            self._event(
                session,
                aggregate_id=str(query_id),
                event_type=f"knowledge.query_{state.value}",
                payload={"query_id": str(query_id), "limitations": list(limitation_codes)},
            )
            return terminal

    def query(self, query_id: UUID) -> KnowledgeQueryRecord:
        with Session(self._engine) as session:
            return KnowledgeQueryRecord.model_validate_json(
                self._require_query_row(session, query_id).payload
            )

    def attempts(self, query_id: UUID) -> tuple[KnowledgeSourceAttempt, ...]:
        with Session(self._engine) as session:
            rows = session.scalars(
                select(KnowledgeSourceAttemptRow)
                .where(KnowledgeSourceAttemptRow.query_id == str(query_id))
                .order_by(KnowledgeSourceAttemptRow.recorded_at, KnowledgeSourceAttemptRow.id)
            ).all()
            return tuple(KnowledgeSourceAttempt.model_validate_json(row.payload) for row in rows)

    def list_queries(
        self, *, project_id: str | None = None, offset: int = 0, limit: int = 100
    ) -> tuple[KnowledgeQueryRecord, ...]:
        self._query_bounds(offset, limit)
        with Session(self._engine) as session:
            statement = select(KnowledgeQueryRow)
            if project_id is not None:
                statement = statement.where(KnowledgeQueryRow.project_id == project_id)
            rows = session.scalars(
                statement.order_by(KnowledgeQueryRow.created_at, KnowledgeQueryRow.id)
                .offset(offset)
                .limit(limit)
            ).all()
            return tuple(KnowledgeQueryRecord.model_validate_json(row.payload) for row in rows)

    def create_operation(self, operation: KnowledgeOperation) -> KnowledgeOperation:
        if operation.state is not KnowledgeOperationState.QUEUED or operation.revision != 0:
            raise MishkanError(
                ErrorCode.OUTPUT_CONTRACT,
                "new knowledge operation must begin queued at revision zero",
            )
        durable = operation.model_copy(update={"revision": 1})
        with Session(self._engine) as session, session.begin():
            existing = session.get(KnowledgeOperationRow, str(operation.operation_id))
            if existing is not None:
                current = KnowledgeOperation.model_validate_json(existing.payload)
                if current.request_fingerprint != operation.request_fingerprint:
                    raise MishkanError(
                        ErrorCode.DUPLICATE_RESULT,
                        "knowledge operation identity already contains different content",
                    )
                return current
            if (
                durable.corpus_id is not None
                and session.get(KnowledgeCorpusRow, str(durable.corpus_id)) is None
            ):
                raise MishkanError(ErrorCode.CONTEXT, "knowledge operation references no corpus")
            session.add(self._operation_row(durable))
            self._event(
                session,
                aggregate_id=str(durable.operation_id),
                event_type="knowledge.operation_queued",
                payload={
                    "operation_id": str(durable.operation_id),
                    "kind": durable.kind.value,
                    "project_id": durable.project_id,
                    "source_id": durable.source_id,
                },
            )
        return durable

    def transition_operation(
        self,
        operation_id: UUID,
        *,
        expected_revision: int,
        state: KnowledgeOperationState,
        provider_operation_id: str | None = None,
        result_references: tuple[str, ...] = (),
        limitation: str | None = None,
    ) -> KnowledgeOperation:
        with Session(self._engine) as session, session.begin():
            row = session.get(KnowledgeOperationRow, str(operation_id))
            if row is None:
                raise MishkanError(ErrorCode.CONTEXT, "knowledge operation does not exist")
            current = KnowledgeOperation.model_validate_json(row.payload)
            if current.revision != expected_revision:
                raise MishkanError(
                    ErrorCode.REVISION_MISMATCH,
                    "knowledge operation revision differs",
                    details={"expected": expected_revision, "current": current.revision},
                )
            if state not in _OPERATION_TRANSITIONS[current.state]:
                raise MishkanError(
                    ErrorCode.REVISION_MISMATCH,
                    "knowledge operation transition is not permitted",
                    details={"from": current.state.value, "to": state.value},
                )
            for reference in result_references:
                artifact = session.get(ArtifactRow, self._artifact_id(reference))
                if artifact is None or artifact.lifecycle != ArtifactLifecycle.AVAILABLE.value:
                    raise MishkanError(
                        ErrorCode.ARTIFACT,
                        "knowledge operation result artifact is unavailable",
                    )
            updated = current.model_copy(
                update={
                    "state": state,
                    "provider_operation_id": provider_operation_id,
                    "result_references": result_references,
                    "limitation": limitation,
                    "revision": current.revision + 1,
                    "updated_at": utc_now(),
                }
            )
            row.state = updated.state.value
            row.revision = updated.revision
            row.payload = self._json(updated)
            row.updated_at = updated.updated_at.isoformat()
            self._event(
                session,
                aggregate_id=str(operation_id),
                event_type=f"knowledge.operation_{state.value}",
                payload={
                    "operation_id": str(operation_id),
                    "state": state.value,
                    "limitation": limitation,
                },
            )
            return updated

    def operation(self, operation_id: UUID) -> KnowledgeOperation:
        with Session(self._engine) as session:
            row = session.get(KnowledgeOperationRow, str(operation_id))
            if row is None:
                raise MishkanError(ErrorCode.CONTEXT, "knowledge operation does not exist")
            return KnowledgeOperation.model_validate_json(row.payload)

    def list_operations(
        self, *, project_id: str | None = None, offset: int = 0, limit: int = 100
    ) -> tuple[KnowledgeOperation, ...]:
        self._query_bounds(offset, limit)
        with Session(self._engine) as session:
            statement = select(KnowledgeOperationRow)
            if project_id is not None:
                statement = statement.where(KnowledgeOperationRow.project_id == project_id)
            rows = session.scalars(
                statement.order_by(KnowledgeOperationRow.updated_at, KnowledgeOperationRow.id)
                .offset(offset)
                .limit(limit)
            ).all()
            return tuple(KnowledgeOperation.model_validate_json(row.payload) for row in rows)

    def put_corpus(self, corpus: KnowledgeCorpus, *, expected_revision: int) -> KnowledgeCorpus:
        if corpus.revision != expected_revision:
            raise MishkanError(
                ErrorCode.REVISION_MISMATCH,
                "knowledge corpus candidate does not carry the expected revision",
            )
        with Session(self._engine) as session, session.begin():
            row = session.scalar(
                select(KnowledgeCorpusRow).where(
                    KnowledgeCorpusRow.project_id == corpus.project_id,
                    KnowledgeCorpusRow.source_id == corpus.source_id,
                )
            )
            current = row.revision if row is not None else 0
            if current != expected_revision:
                raise MishkanError(
                    ErrorCode.REVISION_MISMATCH,
                    "knowledge corpus compare-and-swap revision differs",
                    details={"expected": expected_revision, "current": current},
                )
            if corpus.snapshot_reference is not None:
                artifact = session.get(ArtifactRow, self._artifact_id(corpus.snapshot_reference))
                if artifact is None or artifact.lifecycle != ArtifactLifecycle.AVAILABLE.value:
                    raise MishkanError(
                        ErrorCode.ARTIFACT,
                        "knowledge corpus snapshot is unavailable",
                    )
            durable = corpus.model_copy(update={"revision": current + 1, "updated_at": utc_now()})
            if row is None:
                row = KnowledgeCorpusRow(
                    id=str(durable.corpus_id),
                    project_id=durable.project_id,
                    source_id=durable.source_id,
                    knowledge_class=durable.knowledge_class.value,
                    external_identity=durable.external_identity,
                    indexed_revision=durable.indexed_revision,
                    snapshot_artifact_id=(
                        self._artifact_id(durable.snapshot_reference)
                        if durable.snapshot_reference is not None
                        else None
                    ),
                    state=durable.state.value,
                    revision=durable.revision,
                    payload=self._json(durable),
                    updated_at=durable.updated_at.isoformat(),
                )
                session.add(row)
            else:
                if row.id != str(corpus.corpus_id):
                    raise MishkanError(
                        ErrorCode.DUPLICATE_RESULT,
                        "knowledge corpus source already has a different identity",
                    )
                row.indexed_revision = durable.indexed_revision
                row.snapshot_artifact_id = (
                    self._artifact_id(durable.snapshot_reference)
                    if durable.snapshot_reference is not None
                    else None
                )
                row.state = durable.state.value
                row.revision = durable.revision
                row.payload = self._json(durable)
                row.updated_at = durable.updated_at.isoformat()
            self._event(
                session,
                aggregate_id=str(durable.corpus_id),
                event_type="knowledge.corpus_updated",
                payload={
                    "corpus_id": str(durable.corpus_id),
                    "project_id": durable.project_id,
                    "source_id": durable.source_id,
                    "state": durable.state.value,
                    "revision": durable.revision,
                },
            )
            return durable

    def corpus(self, corpus_id: UUID) -> KnowledgeCorpus:
        with Session(self._engine) as session:
            row = session.get(KnowledgeCorpusRow, str(corpus_id))
            if row is None:
                raise MishkanError(ErrorCode.CONTEXT, "knowledge corpus does not exist")
            return KnowledgeCorpus.model_validate_json(row.payload)

    def list_corpora(
        self, *, project_id: str | None = None, offset: int = 0, limit: int = 100
    ) -> tuple[KnowledgeCorpus, ...]:
        self._query_bounds(offset, limit)
        with Session(self._engine) as session:
            statement = select(KnowledgeCorpusRow)
            if project_id is not None:
                statement = statement.where(KnowledgeCorpusRow.project_id == project_id)
            rows = session.scalars(
                statement.order_by(KnowledgeCorpusRow.project_id, KnowledgeCorpusRow.source_id)
                .offset(offset)
                .limit(limit)
            ).all()
            return tuple(KnowledgeCorpus.model_validate_json(row.payload) for row in rows)

    def propose_promotion(self, proposal: KnowledgePromotion) -> KnowledgePromotion:
        if (
            proposal.disposition is not KnowledgePromotionDisposition.PROPOSED
            or proposal.revision != 0
        ):
            raise MishkanError(
                ErrorCode.OUTPUT_CONTRACT,
                "new knowledge promotion must begin proposed at revision zero",
            )
        durable = proposal.model_copy(update={"revision": 1})
        with Session(self._engine) as session, session.begin():
            existing = session.get(KnowledgePromotionRow, str(proposal.promotion_id))
            if existing is not None:
                current = KnowledgePromotion.model_validate_json(existing.payload)
                if self._json(current) != self._json(durable):
                    raise MishkanError(
                        ErrorCode.DUPLICATE_RESULT,
                        "knowledge promotion identity already contains different content",
                    )
                return current
            session.add(
                KnowledgePromotionRow(
                    id=str(durable.promotion_id),
                    item_id=str(durable.item_id),
                    source_project_id=durable.source_project_id,
                    target_scope=durable.target_scope,
                    disposition=durable.disposition.value,
                    revision=durable.revision,
                    payload=self._json(durable),
                    created_at=durable.created_at.isoformat(),
                    decided_at=None,
                )
            )
            self._event(
                session,
                aggregate_id=str(durable.promotion_id),
                event_type="knowledge.promotion_proposed",
                payload={
                    "promotion_id": str(durable.promotion_id),
                    "source_project_id": durable.source_project_id,
                    "target_scope": durable.target_scope,
                },
            )
        return durable

    def decide_promotion(
        self,
        promotion_id: UUID,
        *,
        expected_revision: int,
        disposition: KnowledgePromotionDisposition,
        decided_by: str,
        policy_fingerprint: str,
    ) -> KnowledgePromotion:
        if disposition is KnowledgePromotionDisposition.PROPOSED:
            raise MishkanError(ErrorCode.OUTPUT_CONTRACT, "promotion decision cannot be proposed")
        with Session(self._engine) as session, session.begin():
            row = session.get(KnowledgePromotionRow, str(promotion_id))
            if row is None:
                raise MishkanError(ErrorCode.CONTEXT, "knowledge promotion does not exist")
            current = KnowledgePromotion.model_validate_json(row.payload)
            if current.revision != expected_revision:
                raise MishkanError(
                    ErrorCode.REVISION_MISMATCH,
                    "knowledge promotion revision differs",
                    details={"expected": expected_revision, "current": current.revision},
                )
            if current.disposition is not KnowledgePromotionDisposition.PROPOSED:
                raise MishkanError(ErrorCode.REVISION_MISMATCH, "knowledge promotion is settled")
            decided = current.model_copy(
                update={
                    "disposition": disposition,
                    "decided_by": decided_by,
                    "policy_fingerprint": policy_fingerprint,
                    "revision": current.revision + 1,
                    "decided_at": utc_now(),
                }
            )
            row.disposition = decided.disposition.value
            row.revision = decided.revision
            row.payload = self._json(decided)
            assert decided.decided_at is not None
            row.decided_at = decided.decided_at.isoformat()
            self._event(
                session,
                aggregate_id=str(promotion_id),
                event_type=f"knowledge.promotion_{disposition.value}",
                payload={
                    "promotion_id": str(promotion_id),
                    "disposition": disposition.value,
                    "decided_by": decided_by,
                },
                security_relevant=True,
            )
            return decided

    def promotion(self, promotion_id: UUID) -> KnowledgePromotion:
        with Session(self._engine) as session:
            row = session.get(KnowledgePromotionRow, str(promotion_id))
            if row is None:
                raise MishkanError(ErrorCode.CONTEXT, "knowledge promotion does not exist")
            return KnowledgePromotion.model_validate_json(row.payload)

    def list_promotions(
        self, *, project_id: str | None = None, offset: int = 0, limit: int = 100
    ) -> tuple[KnowledgePromotion, ...]:
        self._query_bounds(offset, limit)
        with Session(self._engine) as session:
            statement = select(KnowledgePromotionRow)
            if project_id is not None:
                statement = statement.where(KnowledgePromotionRow.source_project_id == project_id)
            rows = session.scalars(
                statement.order_by(KnowledgePromotionRow.created_at, KnowledgePromotionRow.id)
                .offset(offset)
                .limit(limit)
            ).all()
            return tuple(KnowledgePromotion.model_validate_json(row.payload) for row in rows)

    @staticmethod
    def _require_query_row(session: Session, query_id: UUID) -> KnowledgeQueryRow:
        row = session.get(KnowledgeQueryRow, str(query_id))
        if row is None:
            raise MishkanError(ErrorCode.CONTEXT, "knowledge query does not exist")
        return row

    @staticmethod
    def _operation_row(operation: KnowledgeOperation) -> KnowledgeOperationRow:
        return KnowledgeOperationRow(
            id=str(operation.operation_id),
            kind=operation.kind.value,
            project_id=operation.project_id,
            source_id=operation.source_id,
            corpus_id=str(operation.corpus_id) if operation.corpus_id is not None else None,
            state=operation.state.value,
            request_fingerprint=operation.request_fingerprint,
            revision=operation.revision,
            payload=SQLiteKnowledgeRepository._json(operation),
            created_at=operation.created_at.isoformat(),
            updated_at=operation.updated_at.isoformat(),
        )

    @staticmethod
    def _artifact_id(reference: str) -> str:
        if not reference.startswith("artifact:"):
            raise MishkanError(ErrorCode.ARTIFACT, "invalid artifact reference")
        try:
            return str(UUID(reference.removeprefix("artifact:")))
        except ValueError as exc:
            raise MishkanError(ErrorCode.ARTIFACT, "invalid artifact reference") from exc

    @staticmethod
    def _query_bounds(offset: int, limit: int) -> None:
        if offset < 0 or limit < 1 or limit > 1_000:
            raise MishkanError(ErrorCode.OUTPUT_CONTRACT, "knowledge query bound is invalid")

    @staticmethod
    def _json(record: BaseModel) -> str:
        return json.dumps(record.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))

    @staticmethod
    def _event(
        session: Session,
        *,
        aggregate_id: str,
        event_type: str,
        payload: dict[str, object],
        security_relevant: bool = False,
    ) -> None:
        session.add(
            OutboxRow(
                id=str(new_id()),
                schema_version="1.0",
                aggregate_id=aggregate_id,
                entity_type="knowledge",
                run_id=None,
                task_id=None,
                identity_id=None,
                team_id=None,
                security_relevant=security_relevant,
                event_type=event_type,
                source="mishkan.knowledge",
                payload=json.dumps(payload, sort_keys=True, separators=(",", ":")),
                occurred_at=utc_now().isoformat(),
                command_id=None,
                correlation_id=None,
                causation_id=None,
                sensitivity="internal",
                published_at=None,
            )
        )
