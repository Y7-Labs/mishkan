"""Explicit database initialization, inspection, backup, and migration."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import Integer, String, Text, create_engine, inspect
from sqlalchemy.engine import Engine
from sqlalchemy.engine.reflection import Inspector

from mishkan.domain.errors import ErrorCode, MishkanError
from mishkan.persistence.runtime import DatabaseRuntime, DatabaseTarget

I02_BASELINE = "i02_baseline"

_I02_COLUMNS = {
    "runs": {
        "id",
        "resume_key",
        "repository_id",
        "repository_revision",
        "discovery_fingerprint",
        "objective",
        "outcome_id",
        "status",
        "created_at",
    },
    "plans": {"id", "run_id", "fingerprint", "payload", "accepted_at"},
    "tasks": {"id", "run_id", "task_key", "position", "status", "contract"},
    "accepted_results": {"id", "run_id", "task_key", "payload", "accepted_at"},
    "task_acceptances": {
        "id",
        "run_id",
        "task_key",
        "result_id",
        "review_payload",
        "accepted_at",
    },
    "event_outbox": {
        "id",
        "aggregate_id",
        "event_type",
        "payload",
        "occurred_at",
        "published_at",
    },
}

_I02_NULLABLE = {
    "event_outbox": {"published_at"},
}

_I02_INTEGER_COLUMNS = {("tasks", "position")}

_I02_TEXT_COLUMNS = {
    ("runs", "objective"),
    ("plans", "payload"),
    ("tasks", "contract"),
    ("accepted_results", "payload"),
    ("task_acceptances", "review_payload"),
    ("event_outbox", "payload"),
}

_I02_STRING_LENGTHS = {
    "runs": {
        "id": 36,
        "resume_key": 64,
        "repository_id": 64,
        "repository_revision": 128,
        "discovery_fingerprint": 64,
        "outcome_id": 160,
        "status": 32,
        "created_at": 40,
    },
    "plans": {"id": 36, "run_id": 36, "fingerprint": 64, "accepted_at": 40},
    "tasks": {"id": 36, "run_id": 36, "task_key": 64, "status": 32},
    "accepted_results": {"id": 36, "run_id": 36, "task_key": 64, "accepted_at": 40},
    "task_acceptances": {
        "id": 36,
        "run_id": 36,
        "task_key": 64,
        "result_id": 36,
        "accepted_at": 40,
    },
    "event_outbox": {
        "id": 36,
        "aggregate_id": 36,
        "event_type": 120,
        "occurred_at": 40,
        "published_at": 40,
    },
}

_I02_PRIMARY_KEYS = {table: ("id",) for table in _I02_COLUMNS}

_I02_UNIQUES = {
    "runs": {("resume_key",)},
    "plans": {("run_id",)},
    "tasks": {("run_id", "task_key")},
    "accepted_results": {("run_id", "task_key")},
    "task_acceptances": {("run_id", "task_key")},
    "event_outbox": set(),
}

_I02_FOREIGN_KEYS = {
    "runs": set(),
    "plans": {("run_id", "runs", "id")},
    "tasks": {("run_id", "runs", "id")},
    "accepted_results": {("run_id", "runs", "id")},
    "task_acceptances": {
        ("run_id", "runs", "id"),
        ("result_id", "accepted_results", "id"),
    },
    "event_outbox": set(),
}


class DatabaseState(StrEnum):
    EMPTY = "empty"
    LEGACY_I02 = "legacy_i02"
    OUTDATED = "outdated"
    CURRENT = "current"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class DatabaseStatus:
    state: DatabaseState
    current_revision: str | None
    head_revision: str
    backup_path: Path | None = None


class SchemaManager:
    def __init__(self, database: DatabaseTarget) -> None:
        if isinstance(database, DatabaseRuntime):
            self.database_path: Path | None = None
            self._engine: Engine | None = database.engine
            self._display_name = database.display_name
        else:
            self.database_path = database.resolve()
            self._engine = None
            self._display_name = str(self.database_path)

    def status(self) -> DatabaseStatus:
        head = self._head()
        if self.database_path is not None and (
            not self.database_path.exists() or self.database_path.stat().st_size == 0
        ):
            return DatabaseStatus(DatabaseState.EMPTY, None, head)
        engine, owned = self._open_engine()
        try:
            inspector = inspect(engine)
            tables = set(inspector.get_table_names())
            if not tables:
                return DatabaseStatus(DatabaseState.EMPTY, None, head)
            if "alembic_version" not in tables:
                state = DatabaseState.UNKNOWN
                if engine.dialect.name == "sqlite" and self._is_i02(inspector, tables):
                    state = DatabaseState.LEGACY_I02
                return DatabaseStatus(state, None, head)
            with engine.connect() as connection:
                current = MigrationContext.configure(connection).get_current_revision()
            if current == head:
                return DatabaseStatus(DatabaseState.CURRENT, current, head)
            known = {revision.revision for revision in self._scripts().walk_revisions()}
            state = DatabaseState.OUTDATED if current in known else DatabaseState.UNKNOWN
            return DatabaseStatus(state, current, head)
        finally:
            if owned:
                engine.dispose()

    def initialize(self) -> DatabaseStatus:
        observed = self.status()
        if observed.state is not DatabaseState.EMPTY:
            raise MishkanError(
                ErrorCode.VERSION,
                "database initialization requires an empty destination",
                details={"state": observed.state.value, "database": self._display_name},
            )
        if self.database_path is not None:
            self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._upgrade_to_head()
        return self.status()

    def initialize_if_empty(self) -> DatabaseStatus:
        observed = self.status()
        return self.initialize() if observed.state is DatabaseState.EMPTY else observed

    def upgrade(self) -> DatabaseStatus:
        observed = self.status()
        if observed.state is DatabaseState.CURRENT:
            return observed
        if observed.state is DatabaseState.EMPTY:
            return self.initialize()
        if observed.state is DatabaseState.UNKNOWN:
            raise MishkanError(
                ErrorCode.VERSION,
                "database schema is not a recognized MISHKAN revision",
                details={"database": self._display_name, "automatic_migration": False},
            )
        backup = self._backup() if self.database_path is not None else None
        if observed.state is DatabaseState.LEGACY_I02:
            self._stamp(I02_BASELINE)
        self._upgrade_to_head()
        current = self.status()
        return DatabaseStatus(
            current.state,
            current.current_revision,
            current.head_revision,
            backup_path=backup,
        )

    def require_current(self) -> None:
        observed = self.status()
        if observed.state is not DatabaseState.CURRENT:
            raise MishkanError(
                ErrorCode.VERSION,
                "database schema requires an explicit operator action",
                details={
                    "state": observed.state.value,
                    "current_revision": observed.current_revision,
                    "required_revision": observed.head_revision,
                    "automatic_migration": False,
                },
            )

    def _config(self) -> Config:
        config = Config()
        config.set_main_option("script_location", str(Path(__file__).parent / "migrations"))
        if self._engine is not None:
            config.set_main_option(
                "sqlalchemy.url",
                self._engine.url.render_as_string(hide_password=False),
            )
        else:
            assert self.database_path is not None
            config.set_main_option("sqlalchemy.url", f"sqlite:///{self.database_path}")
        return config

    def _scripts(self) -> ScriptDirectory:
        return ScriptDirectory.from_config(self._config())

    def _head(self) -> str:
        head = self._scripts().get_current_head()
        if head is None:
            raise RuntimeError("MISHKAN migration history has no head revision")
        return head

    def _upgrade_to_head(self) -> None:
        self._run_alembic(command.upgrade, "head")

    def _stamp(self, revision: str) -> None:
        self._run_alembic(command.stamp, revision)

    def _run_alembic(self, operation: Callable[[Config, str], None], revision: str) -> None:
        engine, owned = self._open_engine()
        try:
            with engine.begin() as connection:
                config = self._config()
                config.attributes["connection"] = connection
                operation(config, revision)
        finally:
            if owned:
                engine.dispose()

    def _backup(self) -> Path:
        assert self.database_path is not None
        directory = self.database_path.parent / "backups"
        directory.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
        destination = directory / f"{self.database_path.stem}-{timestamp}.db"
        with sqlite3.connect(self.database_path) as source, sqlite3.connect(destination) as target:
            source.backup(target)
        return destination

    def _open_engine(self) -> tuple[Engine, bool]:
        if self._engine is not None:
            return self._engine, False
        assert self.database_path is not None
        return create_engine(f"sqlite:///{self.database_path}"), True

    @staticmethod
    def _is_i02(inspector: Inspector, tables: set[str]) -> bool:
        if tables != set(_I02_COLUMNS):
            return False
        for table, expected_names in _I02_COLUMNS.items():
            columns = {column["name"]: column for column in inspector.get_columns(table)}
            if set(columns) != expected_names:
                return False
            for name, column in columns.items():
                expected_nullable = name in _I02_NULLABLE.get(table, set())
                if bool(column["nullable"]) != expected_nullable:
                    return False
                observed_type = column["type"]
                if (table, name) in _I02_INTEGER_COLUMNS:
                    if not isinstance(observed_type, Integer):
                        return False
                elif (table, name) in _I02_TEXT_COLUMNS:
                    if not isinstance(observed_type, Text):
                        return False
                elif (
                    not isinstance(observed_type, String)
                    or observed_type.length != _I02_STRING_LENGTHS[table][name]
                ):
                    return False
                if column.get("default") is not None:
                    return False
            primary_key = tuple(inspector.get_pk_constraint(table).get("constrained_columns") or ())
            if primary_key != _I02_PRIMARY_KEYS[table]:
                return False
            uniques = {
                tuple(item.get("column_names") or ())
                for item in inspector.get_unique_constraints(table)
            }
            if uniques != _I02_UNIQUES[table]:
                return False
            foreign_keys = {
                (
                    next(iter(item.get("constrained_columns") or ())),
                    str(item.get("referred_table")),
                    next(iter(item.get("referred_columns") or ())),
                )
                for item in inspector.get_foreign_keys(table)
                if len(tuple(item.get("constrained_columns") or ())) == 1
                and len(tuple(item.get("referred_columns") or ())) == 1
            }
            if foreign_keys != _I02_FOREIGN_KEYS[table]:
                return False
            if inspector.get_indexes(table) or inspector.get_check_constraints(table):
                return False
        return True
