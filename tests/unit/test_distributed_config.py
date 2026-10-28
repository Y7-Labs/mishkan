from __future__ import annotations

import copy

import pytest
import yaml
from pydantic import ValidationError

from mishkan.artifacts.bodies import FilesystemArtifactBodyStore, S3ArtifactBodyStore
from mishkan.config.models import (
    MishkanConfig,
    OperatingMode,
    PostgreSQLPersistenceConfig,
    S3ArtifactConfig,
)
from mishkan.config.presets import preset_text
from mishkan.crewai.credentials import CredentialPoolResolver
from mishkan.daemon.api import create_app
from mishkan.daemon.bootstrap import DaemonPaths
from mishkan.daemon.composition import build_artifact_body_store, build_database_runtime
from mishkan.domain.errors import ErrorCode, MishkanError


def _distributed_document() -> dict[str, object]:
    document = copy.deepcopy(yaml.safe_load(preset_text("local")))
    document["mode"] = "distributed"
    daemon = document["daemon"]
    assert isinstance(daemon, dict)
    daemon["host"] = "0.0.0.0"
    document["persistence"] = {
        "kind": "postgresql",
        "dsn_ref": {"source": "env", "locator": "MISHKAN_POSTGRES_DSN"},
        "pool_size": 12,
        "max_overflow": 24,
        "connect_timeout_seconds": 8,
        "statement_timeout_ms": 90_000,
        "event_retention_days": 30,
    }
    document["artifacts"] = {
        "kind": "s3",
        "endpoint": "http://127.0.0.1:9000",
        "bucket": "mishkan-evidence",
        "prefix": "projects/example",
        "region": "us-east-1",
        "addressing_style": "path",
        "tls_verify": False,
        "access_key_ref": {"source": "env", "locator": "MISHKAN_S3_ACCESS_KEY"},
        "secret_key_ref": {"source": "env", "locator": "MISHKAN_S3_SECRET_KEY"},
        "staging_root": ".mishkan/artifact-staging",
        "max_artifact_bytes": 536_870_912,
        "chunk_bytes": 1_048_576,
        "staging_ttl_seconds": 86_400,
        "multipart_threshold_bytes": 8_388_608,
        "multipart_chunk_bytes": 8_388_608,
    }
    reference = {"source": "file", "locator": ".mishkan/pki/placeholder.pem"}
    document["distributed"] = {
        "enrollment_ttl_seconds": 600,
        "certificate_ttl_seconds": 86_400,
        "certificate_rotation_before_seconds": 3_600,
        "heartbeat_interval_seconds": 10,
        "heartbeat_timeout_seconds": 30,
        "lease_seconds": 60,
        "claim_page_limit": 10,
        "max_worker_concurrency": 8,
        "max_envelope_bytes": 8_388_608,
        "max_completion_bytes": 67_108_864,
        "server_certificate_ref": reference,
        "server_private_key_ref": reference,
        "worker_ca_certificate_ref": reference,
        "worker_ca_private_key_ref": reference,
    }
    return document


def test_distributed_mode_requires_explicit_postgresql_s3_and_worker_bounds() -> None:
    config = MishkanConfig.model_validate(_distributed_document())

    assert config.mode is OperatingMode.DISTRIBUTED
    assert isinstance(config.persistence, PostgreSQLPersistenceConfig)
    assert isinstance(config.artifacts, S3ArtifactConfig)
    assert config.distributed is not None


@pytest.mark.parametrize("missing", ["persistence", "artifacts", "distributed"])
def test_distributed_mode_rejects_incompatible_or_missing_storage(missing: str) -> None:
    document = _distributed_document()
    local = yaml.safe_load(preset_text("local"))
    if missing == "distributed":
        document.pop("distributed")
    else:
        document[missing] = local[missing]

    with pytest.raises(ValidationError):
        MishkanConfig.model_validate(document)


def test_non_distributed_mode_cannot_expose_non_loopback_daemon() -> None:
    document = yaml.safe_load(preset_text("local"))
    document["daemon"]["host"] = "0.0.0.0"

    with pytest.raises(ValidationError, match="loopback-only"):
        MishkanConfig.model_validate(document)


def test_s3_credentials_are_references_and_require_a_complete_pair() -> None:
    document = _distributed_document()
    del document["artifacts"]["secret_key_ref"]

    with pytest.raises(ValidationError, match="configured together"):
        MishkanConfig.model_validate(document)


def test_storage_composition_selects_backends_from_public_mode(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    local_document = yaml.safe_load(preset_text("local"))
    local_document["project"]["workspace"] = str(tmp_path / "local")
    local = MishkanConfig.model_validate(local_document)
    local_paths = DaemonPaths.from_config(local)
    assert local_paths.database is not None
    local_paths.database.parent.mkdir(parents=True)
    local_runtime = build_database_runtime(local, local_paths, CredentialPoolResolver())
    try:
        assert local_runtime.backend == "sqlite"
        assert isinstance(
            build_artifact_body_store(local, local_paths, CredentialPoolResolver()),
            FilesystemArtifactBodyStore,
        )
    finally:
        local_runtime.dispose()

    distributed_document = _distributed_document()
    distributed_document["project"]["workspace"] = str(tmp_path / "distributed")
    distributed = MishkanConfig.model_validate(distributed_document)
    monkeypatch.setenv("MISHKAN_POSTGRES_DSN", "postgresql+psycopg://test:test@db/test")
    monkeypatch.setenv("MISHKAN_S3_ACCESS_KEY", "reference-access")
    monkeypatch.setenv("MISHKAN_S3_SECRET_KEY", "reference-secret")
    distributed_paths = DaemonPaths.from_config(distributed)
    runtime = build_database_runtime(distributed, distributed_paths, CredentialPoolResolver())
    try:
        assert runtime.backend == "postgresql"
        assert runtime.display_name == "postgresql"
        assert isinstance(
            build_artifact_body_store(distributed, distributed_paths, CredentialPoolResolver()),
            S3ArtifactBodyStore,
        )
    finally:
        runtime.dispose()


def test_distributed_database_credential_is_late_resolved_and_required(tmp_path) -> None:
    document = _distributed_document()
    document["project"]["workspace"] = str(tmp_path)
    document["persistence"]["dsn_ref"]["locator"] = "MISHKAN_TEST_ABSENT_POSTGRES_DSN"
    config = MishkanConfig.model_validate(document)

    with pytest.raises(MishkanError) as failure:
        build_database_runtime(config, DaemonPaths.from_config(config), CredentialPoolResolver())
    assert failure.value.envelope.code is ErrorCode.CONFIGURATION


def test_distributed_daemon_cannot_fall_back_to_plain_bearer_http() -> None:
    config = MishkanConfig.model_validate(_distributed_document())

    with pytest.raises(MishkanError, match="mTLS listener"):
        create_app(config)
