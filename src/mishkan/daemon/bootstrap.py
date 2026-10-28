"""Explicit daemon bootstrap and path resolution."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from mishkan.config.models import ArtifactConfig, MishkanConfig, PersistenceConfig
from mishkan.crewai.credentials import CredentialPoolResolver
from mishkan.daemon.auth import TokenFile
from mishkan.domain.errors import ErrorCode, MishkanError
from mishkan.persistence import DatabaseState, SchemaManager


@dataclass(frozen=True, slots=True)
class DaemonPaths:
    workspace: Path
    database: Path | None
    token_file: Path
    artifacts: Path
    sessions: Path

    @classmethod
    def from_config(cls, config: MishkanConfig) -> DaemonPaths:
        if config.schema_version not in {
            "1.2",
            "1.3",
            "1.4",
            "1.5",
            "1.6",
            "1.7",
            "1.8",
        } or not all((config.daemon, config.persistence, config.artifacts, config.sessions)):
            raise MishkanError(
                ErrorCode.VERSION,
                "daemon operation requires configuration schema 1.2 or later",
                details={"received": config.schema_version, "automatic_migration": False},
            )
        assert config.daemon is not None
        assert config.persistence is not None
        assert config.artifacts is not None
        assert config.sessions is not None
        workspace = config.project.workspace.expanduser().resolve()
        database = (
            (workspace / config.persistence.database).resolve()
            if isinstance(config.persistence, PersistenceConfig)
            else None
        )
        artifact_root = (
            config.artifacts.root
            if isinstance(config.artifacts, ArtifactConfig)
            else config.artifacts.staging_root
        )
        paths = cls(
            workspace=workspace,
            database=database,
            token_file=(workspace / config.daemon.token_file).resolve(),
            artifacts=(workspace / artifact_root).resolve(),
            sessions=(workspace / config.sessions.spool_root).resolve(),
        )
        for path in (paths.database, paths.token_file, paths.artifacts, paths.sessions):
            if path is None:
                continue
            if not path.is_relative_to(workspace):
                raise MishkanError(
                    ErrorCode.AUTHORITY_NOT_GRANTED,
                    "daemon metadata path escapes the configured workspace",
                    details={"path": str(path)},
                )
        return paths


class DaemonBootstrap:
    def setup(self, config: MishkanConfig, *, principal_id: str = "local-operator") -> DaemonPaths:
        paths = DaemonPaths.from_config(config)
        paths.workspace.mkdir(parents=True, exist_ok=True)
        if paths.database is not None:
            paths.database.parent.mkdir(parents=True, exist_ok=True)
        from mishkan.daemon.composition import build_database_runtime

        runtime = build_database_runtime(config, paths, CredentialPoolResolver())
        try:
            manager = SchemaManager(runtime)
            state = manager.status().state
            if state is DatabaseState.EMPTY:
                manager.initialize()
            elif state is not DatabaseState.CURRENT:
                raise MishkanError(
                    ErrorCode.VERSION,
                    "daemon setup found metadata requiring explicit database upgrade",
                    details={"state": state.value, "automatic_migration": False},
                )
        finally:
            runtime.dispose()
        paths.artifacts.mkdir(parents=True, exist_ok=True)
        paths.sessions.mkdir(parents=True, exist_ok=True)
        if config.skills is not None:
            skill_root = (paths.workspace / config.skills.managed_root).resolve()
            if not skill_root.is_relative_to(paths.workspace):
                raise MishkanError(
                    ErrorCode.AUTHORITY_NOT_GRANTED,
                    "managed skill root escapes the configured workspace",
                )
            skill_root.mkdir(parents=True, exist_ok=True)
        TokenFile(paths.token_file).create(principal_id)
        return paths
