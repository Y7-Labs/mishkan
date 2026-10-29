from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from mishkan.artifacts.service import DurableArtifactService
from mishkan.domain.errors import ErrorCode, MishkanError
from mishkan.persistence import (
    DatabaseRuntime,
    DatabaseState,
    LocalRunRepository,
    SchemaManager,
    SQLiteApplicationRepository,
)
from mishkan.persistence.sqlite import RunRow, create_local_engine
from mishkan.scheduling import SQLiteScheduleRepository


def test_sqlite_runtime_owns_one_wal_engine_shared_by_compatibility_factory(
    tmp_path: Path,
) -> None:
    database = tmp_path / "mishkan.db"
    runtime = DatabaseRuntime.sqlite(database, busy_timeout_ms=1_234)
    SchemaManager(runtime).initialize()

    assert create_local_engine(runtime) is runtime.engine
    assert runtime.backend == "sqlite"
    assert runtime.dialect == "sqlite"
    assert SchemaManager(runtime).status().state is DatabaseState.CURRENT
    runtime.healthcheck()

    with Session(runtime.engine) as session:
        assert session.query(RunRow).count() == 0

    runtime.dispose()


def test_postgresql_runtime_keeps_credentials_out_of_its_display_name() -> None:
    runtime = DatabaseRuntime.postgresql(
        "postgresql+psycopg://worker:secret@localhost:5432/mishkan",
        pool_size=2,
        max_overflow=1,
        connect_timeout_seconds=3,
        statement_timeout_ms=5_000,
    )

    assert runtime.backend == "postgresql"
    assert runtime.dialect == "postgresql"
    assert runtime.display_name == "postgresql"
    assert "secret" not in runtime.display_name
    runtime.dispose()


def test_repositories_share_the_injected_runtime_engine(tmp_path: Path) -> None:
    database = tmp_path / "mishkan.db"
    runtime = DatabaseRuntime.sqlite(database)
    SchemaManager(runtime).initialize()

    application = SQLiteApplicationRepository(runtime)
    runs = LocalRunRepository(runtime)
    schedules = SQLiteScheduleRepository(runtime)
    artifacts = DurableArtifactService(
        runtime,
        tmp_path / "artifacts",
        max_artifact_bytes=1_024,
        max_chunk_bytes=128,
    )

    assert application._engine is runtime.engine
    assert runs._engine is runtime.engine
    assert schedules._engine is runtime.engine
    assert artifacts._engine is runtime.engine
    runtime.dispose()


@pytest.mark.parametrize(
    ("url", "pool_size", "max_overflow", "connect_timeout", "statement_timeout"),
    [
        ("sqlite:///tmp.db", 2, 1, 3, 5_000),
        ("postgresql://localhost/mishkan", 0, 1, 3, 5_000),
        ("postgresql://localhost/mishkan", 2, -1, 3, 5_000),
        ("postgresql://localhost/mishkan", 2, 1, 0, 5_000),
        ("postgresql://localhost/mishkan", 2, 1, 3, 0),
    ],
)
def test_postgresql_runtime_rejects_invalid_public_bounds(
    url: str,
    pool_size: int,
    max_overflow: int,
    connect_timeout: int,
    statement_timeout: int,
) -> None:
    with pytest.raises(MishkanError) as caught:
        DatabaseRuntime.postgresql(
            url,
            pool_size=pool_size,
            max_overflow=max_overflow,
            connect_timeout_seconds=connect_timeout,
            statement_timeout_ms=statement_timeout,
        )

    assert caught.value.envelope.code is ErrorCode.CONFIGURATION
