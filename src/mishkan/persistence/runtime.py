"""Shared SQLAlchemy runtime for local and distributed metadata stores."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine

from mishkan.domain.errors import ErrorCode, MishkanError

DEFAULT_BUSY_TIMEOUT_MS = 5_000


@dataclass(frozen=True, slots=True)
class DatabaseRuntime:
    """Own one SQLAlchemy engine and the dialect facts shared by repositories."""

    engine: Engine
    backend: str
    display_name: str

    @classmethod
    def sqlite(
        cls,
        database_path: Path,
        *,
        busy_timeout_ms: int = DEFAULT_BUSY_TIMEOUT_MS,
    ) -> DatabaseRuntime:
        if busy_timeout_ms < 1:
            raise MishkanError(ErrorCode.CONFIGURATION, "SQLite busy timeout must be positive")
        resolved = database_path.expanduser().resolve()
        engine = create_engine(
            f"sqlite:///{resolved}",
            connect_args={"timeout": busy_timeout_ms / 1_000},
        )

        def configure(dbapi_connection: Any, _connection_record: Any) -> None:
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute(f"PRAGMA busy_timeout={busy_timeout_ms}")
            cursor.close()

        event.listen(engine, "connect", configure)
        return cls(engine=engine, backend="sqlite", display_name=str(resolved))

    @classmethod
    def postgresql(
        cls,
        url: str,
        *,
        pool_size: int,
        max_overflow: int,
        connect_timeout_seconds: int,
        statement_timeout_ms: int,
    ) -> DatabaseRuntime:
        if not url.startswith(("postgresql://", "postgresql+psycopg://")):
            raise MishkanError(
                ErrorCode.CONFIGURATION,
                "distributed metadata requires a PostgreSQL DSN",
            )
        if min(pool_size, connect_timeout_seconds, statement_timeout_ms) < 1 or max_overflow < 0:
            raise MishkanError(
                ErrorCode.CONFIGURATION,
                "PostgreSQL pool and timeout bounds are invalid",
            )
        engine = create_engine(
            url,
            pool_size=pool_size,
            max_overflow=max_overflow,
            pool_pre_ping=True,
            connect_args={
                "connect_timeout": connect_timeout_seconds,
                "options": f"-c statement_timeout={statement_timeout_ms}",
            },
        )
        return cls(engine=engine, backend="postgresql", display_name="postgresql")

    @property
    def dialect(self) -> str:
        return self.engine.dialect.name

    def healthcheck(self) -> None:
        with self.engine.connect() as connection:
            connection.execute(text("SELECT 1"))

    def dispose(self) -> None:
        self.engine.dispose()


DatabaseTarget = Path | DatabaseRuntime


def database_engine(
    target: DatabaseTarget,
    *,
    busy_timeout_ms: int = DEFAULT_BUSY_TIMEOUT_MS,
) -> Engine:
    """Return the shared engine or preserve the legacy path-based local composition."""

    if isinstance(target, DatabaseRuntime):
        return target.engine
    return DatabaseRuntime.sqlite(target, busy_timeout_ms=busy_timeout_ms).engine
