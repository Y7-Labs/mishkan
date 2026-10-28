"""Durable request-to-candidate lineage for governed skill learning."""

from __future__ import annotations

import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from mishkan.domain.errors import ErrorCode, MishkanError
from mishkan.domain.identity import new_id
from mishkan.persistence.migration import SchemaManager
from mishkan.persistence.runtime import DatabaseTarget
from mishkan.persistence.sqlite import OutboxRow, SkillLearningRow, create_local_engine
from mishkan.skills.models import SkillLearningRecord


class SQLiteSkillLearningRepository:
    def __init__(self, database_path: DatabaseTarget, *, busy_timeout_ms: int = 5_000) -> None:
        SchemaManager(database_path).require_current()
        self._engine = create_local_engine(database_path, busy_timeout_ms=busy_timeout_ms)

    def create(self, record: SkillLearningRecord) -> SkillLearningRecord:
        with Session(self._engine) as session, session.begin():
            existing = session.get(SkillLearningRow, str(record.request.request_id))
            if existing is not None:
                observed = self._record(existing)
                if observed == record:
                    return observed
                raise MishkanError(
                    ErrorCode.DUPLICATE_RESULT,
                    "skill learning request identity already contains different content",
                )
            session.add(self._row(record))
            self._event(session, record)
        return record

    def update(
        self,
        record: SkillLearningRecord,
        *,
        expected_revision: int,
    ) -> SkillLearningRecord:
        with Session(self._engine) as session, session.begin():
            row = self._require_row(session, str(record.request.request_id))
            current = self._record(row)
            if current.revision != expected_revision:
                raise MishkanError(
                    ErrorCode.REVISION_MISMATCH,
                    "skill learning record revision changed",
                    details={"observed_revision": current.revision},
                )
            if record.revision != expected_revision + 1:
                raise MishkanError(
                    ErrorCode.SKILL_CONTRACT,
                    "skill learning update must advance exactly one revision",
                )
            row.state = record.state.value
            row.payload = self._json(record)
            row.revision = record.revision
            row.updated_at = record.updated_at.isoformat()
            self._event(session, record)
        return record

    def get(self, request_id: str) -> SkillLearningRecord:
        with Session(self._engine) as session:
            return self._record(self._require_row(session, request_id))

    def list(
        self,
        *,
        offset: int = 0,
        limit: int = 100,
    ) -> tuple[SkillLearningRecord, ...]:
        if offset < 0 or limit < 1 or limit > 1_000:
            raise MishkanError(ErrorCode.OUTPUT_CONTRACT, "skill learning query bound is invalid")
        with Session(self._engine) as session:
            rows = session.scalars(
                select(SkillLearningRow)
                .order_by(SkillLearningRow.updated_at, SkillLearningRow.request_id)
                .offset(offset)
                .limit(limit)
            ).all()
            return tuple(self._record(row) for row in rows)

    @staticmethod
    def _row(record: SkillLearningRecord) -> SkillLearningRow:
        return SkillLearningRow(
            request_id=str(record.request.request_id),
            task_id=record.request.task_id,
            task_class=record.request.task_class,
            state=record.state.value,
            payload=SQLiteSkillLearningRepository._json(record),
            revision=record.revision,
            created_at=record.created_at.isoformat(),
            updated_at=record.updated_at.isoformat(),
        )

    @staticmethod
    def _record(row: SkillLearningRow) -> SkillLearningRecord:
        return SkillLearningRecord.model_validate_json(row.payload)

    @staticmethod
    def _require_row(session: Session, request_id: str) -> SkillLearningRow:
        row = session.get(SkillLearningRow, request_id)
        if row is None:
            raise MishkanError(
                ErrorCode.SKILL_SELECTION,
                "skill learning request does not exist",
                details={"request_id": request_id},
            )
        return row

    @staticmethod
    def _json(record: SkillLearningRecord) -> str:
        return json.dumps(record.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))

    @staticmethod
    def _event(session: Session, record: SkillLearningRecord) -> None:
        session.add(
            OutboxRow(
                id=str(new_id()),
                schema_version="1.0",
                aggregate_id=str(record.request.request_id),
                entity_type="skill_learning",
                run_id=None,
                task_id=record.request.task_id,
                identity_id=record.request.consuming_identity,
                team_id="Research",
                security_relevant=False,
                event_type=f"skill.learning_{record.state.value}",
                source="mishkan.skills",
                payload=json.dumps(
                    {
                        "request_id": str(record.request.request_id),
                        "task_class": record.request.task_class,
                        "state": record.state.value,
                        "base_version_id": (
                            None if record.base_version_id is None else str(record.base_version_id)
                        ),
                        "candidate_version_id": (
                            None
                            if record.candidate_version_id is None
                            else str(record.candidate_version_id)
                        ),
                        "policy_fingerprint": record.policy_fingerprint,
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                occurred_at=record.updated_at.isoformat(),
                command_id=None,
                correlation_id=None,
                causation_id=None,
                sensitivity="internal",
                published_at=None,
            )
        )
