"""Persistent Web cache with explicit freshness and no hidden network fallback."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.orm import Session

from mishkan.domain.time import require_aware, utc_now
from mishkan.persistence.migration import SchemaManager
from mishkan.persistence.runtime import DatabaseTarget
from mishkan.persistence.sqlite import WebCacheRow, create_local_engine
from mishkan.web.models import CacheDisposition


@dataclass(frozen=True, slots=True)
class CacheHit:
    payload: str
    disposition: CacheDisposition
    stored_at: datetime
    fresh_until: datetime


class SQLiteWebCache:
    def __init__(self, database: DatabaseTarget, *, busy_timeout_ms: int = 5_000) -> None:
        SchemaManager(database).require_current()
        self._engine = create_local_engine(database, busy_timeout_ms=busy_timeout_ms)

    def get(
        self,
        key: str,
        *,
        kind: str,
        allow_stale_seconds: int,
        now: datetime | None = None,
    ) -> CacheHit | None:
        observed_at = require_aware(now) if now is not None else utc_now()
        with Session(self._engine) as session:
            row = session.get(WebCacheRow, key)
            if row is None or row.kind != kind:
                return None
            stored_at = datetime.fromisoformat(row.stored_at)
            fresh_until = datetime.fromisoformat(row.fresh_until)
            if observed_at <= fresh_until:
                disposition = CacheDisposition.FRESH
            elif observed_at <= fresh_until + timedelta(seconds=allow_stale_seconds):
                disposition = CacheDisposition.STALE
            else:
                return None
            return CacheHit(row.payload, disposition, stored_at, fresh_until)

    def put(
        self,
        key: str,
        *,
        kind: str,
        payload: str,
        ttl_seconds: int,
        now: datetime | None = None,
    ) -> None:
        stored_at = require_aware(now) if now is not None else utc_now()
        fresh_until = stored_at + timedelta(seconds=ttl_seconds)
        with Session(self._engine) as session, session.begin():
            values = {
                "key": key,
                "kind": kind,
                "payload": payload,
                "stored_at": stored_at.isoformat(),
                "fresh_until": fresh_until.isoformat(),
            }
            session.execute(
                insert(WebCacheRow)
                .values(**values)
                .on_conflict_do_update(index_elements=[WebCacheRow.key], set_=values)
            )

    def delete(self, key: str) -> None:
        with Session(self._engine) as session, session.begin():
            session.execute(delete(WebCacheRow).where(WebCacheRow.key == key))

    def prune(self, *, before: datetime) -> int:
        cutoff = require_aware(before).isoformat()
        with Session(self._engine) as session, session.begin():
            keys = tuple(
                session.scalars(
                    select(WebCacheRow.key).where(WebCacheRow.fresh_until < cutoff)
                ).all()
            )
            if keys:
                session.execute(delete(WebCacheRow).where(WebCacheRow.key.in_(keys)))
            return len(keys)
