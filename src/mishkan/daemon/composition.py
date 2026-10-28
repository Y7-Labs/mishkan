"""Resolve public storage configuration into daemon-owned runtime adapters."""

from __future__ import annotations

from mishkan.artifacts.bodies import (
    ArtifactBodyStore,
    FilesystemArtifactBodyStore,
    S3ArtifactBodyStore,
)
from mishkan.config.models import (
    ArtifactConfig,
    CredentialReference,
    MishkanConfig,
    PersistenceConfig,
    PostgreSQLPersistenceConfig,
    S3ArtifactConfig,
)
from mishkan.crewai.credentials import CredentialPoolResolver
from mishkan.daemon.bootstrap import DaemonPaths
from mishkan.domain.errors import ErrorCode, MishkanError
from mishkan.persistence.runtime import DEFAULT_BUSY_TIMEOUT_MS, DatabaseRuntime


def build_database_runtime(
    config: MishkanConfig,
    paths: DaemonPaths,
    credentials: CredentialPoolResolver,
) -> DatabaseRuntime:
    persistence = config.persistence
    if isinstance(persistence, PersistenceConfig):
        if paths.database is None:
            raise MishkanError(ErrorCode.CONFIGURATION, "SQLite database path is unavailable")
        return DatabaseRuntime.sqlite(
            paths.database,
            busy_timeout_ms=persistence.busy_timeout_ms,
        )
    if isinstance(persistence, PostgreSQLPersistenceConfig):
        dsn = _resolve_required(credentials, persistence.dsn_ref)
        return DatabaseRuntime.postgresql(
            dsn,
            pool_size=persistence.pool_size,
            max_overflow=persistence.max_overflow,
            connect_timeout_seconds=persistence.connect_timeout_seconds,
            statement_timeout_ms=persistence.statement_timeout_ms,
        )
    raise MishkanError(ErrorCode.CONFIGURATION, "metadata persistence is not configured")


def build_artifact_body_store(
    config: MishkanConfig,
    paths: DaemonPaths,
    credentials: CredentialPoolResolver,
) -> ArtifactBodyStore:
    artifacts = config.artifacts
    if isinstance(artifacts, ArtifactConfig):
        return FilesystemArtifactBodyStore(paths.artifacts / "blobs")
    if isinstance(artifacts, S3ArtifactConfig):
        access_key = _resolve_optional(credentials, artifacts.access_key_ref)
        secret_key = _resolve_optional(credentials, artifacts.secret_key_ref)
        session_token = _resolve_optional(credentials, artifacts.session_token_ref)
        verify: bool | str = artifacts.tls_verify
        if artifacts.ca_bundle is not None:
            verify = str((paths.workspace / artifacts.ca_bundle).resolve())
        return S3ArtifactBodyStore.from_boto3(
            bucket=artifacts.bucket,
            prefix=artifacts.prefix,
            endpoint_url=str(artifacts.endpoint) if artifacts.endpoint is not None else None,
            region_name=artifacts.region,
            access_key_id=access_key,
            secret_access_key=secret_key,
            session_token=session_token,
            verify=verify,
            addressing_style=artifacts.addressing_style,
            multipart_threshold_bytes=artifacts.multipart_threshold_bytes,
            multipart_chunk_bytes=artifacts.multipart_chunk_bytes,
        )
    raise MishkanError(ErrorCode.CONFIGURATION, "artifact body persistence is not configured")


def repository_busy_timeout_ms(config: MishkanConfig) -> int:
    persistence = config.persistence
    return (
        persistence.busy_timeout_ms
        if isinstance(persistence, PersistenceConfig)
        else DEFAULT_BUSY_TIMEOUT_MS
    )


def _resolve_required(
    resolver: CredentialPoolResolver,
    reference: CredentialReference,
) -> str:
    value = resolver.resolve((reference,))[0]
    if value is None:
        raise MishkanError(ErrorCode.AUTHORIZATION_MISSING, "required credential is unavailable")
    return value


def _resolve_optional(
    resolver: CredentialPoolResolver,
    reference: CredentialReference | None,
) -> str | None:
    if reference is None:
        return None
    return _resolve_required(resolver, reference)
