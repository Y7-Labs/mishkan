from __future__ import annotations

import copy
import hashlib
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

import pytest
import yaml
from pydantic import ValidationError

from mishkan.application import ApplicationCommand
from mishkan.application.authorization import ApplicationCommandAuthority
from mishkan.artifacts.bodies import FilesystemArtifactBodyStore, S3ArtifactBodyStore
from mishkan.config.models import (
    MishkanConfig,
    OperatingMode,
    PostgreSQLPersistenceConfig,
    S3ArtifactConfig,
)
from mishkan.config.presets import preset_text
from mishkan.crewai.credentials import CredentialPoolResolver
from mishkan.daemon.bootstrap import DaemonPaths
from mishkan.daemon.composition import build_artifact_body_store, build_database_runtime
from mishkan.domain.errors import ErrorCode, MishkanError
from mishkan.domain.time import utc_now
from mishkan.planning.models import InitializationResult, ReviewDecision
from mishkan.policy import Decision
from mishkan.workers import (
    CapabilityRequirement,
    RemotePlacementRequest,
    TaskPathScope,
    TaskResourceLimits,
)


class _UnusedLookup:
    def definition(self, _identity):  # type: ignore[no-untyped-def]
        raise AssertionError("change lookup should not be used")

    def status(self, _identity):  # type: ignore[no-untyped-def]
        raise AssertionError("session lookup should not be used")


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
        "server_ca_certificate_ref": reference,
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
    assert config.distributed.server_ca_certificate_ref.locator.endswith("placeholder.pem")


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


def test_distributed_daemon_requires_explicit_server_and_worker_ca_material() -> None:
    config = MishkanConfig.model_validate(_distributed_document())

    assert config.distributed is not None
    assert config.distributed.server_certificate_ref.locator.endswith("placeholder.pem")
    assert config.distributed.server_private_key_ref.locator.endswith("placeholder.pem")
    assert config.distributed.worker_ca_certificate_ref.locator.endswith("placeholder.pem")
    assert config.distributed.worker_ca_private_key_ref.locator.endswith("placeholder.pem")


def test_worker_admin_commands_have_public_policy_derived_authority(tmp_path: Path) -> None:
    document = _distributed_document()
    document["project"]["workspace"] = str(tmp_path)
    config = MishkanConfig.model_validate(document)
    authority = ApplicationCommandAuthority(
        config,
        tmp_path,
        _UnusedLookup(),
        _UnusedLookup(),
    )
    token_id = uuid4()
    authorized = authority.authorize(
        ApplicationCommand(
            command_type="worker.enrollment.create",
            actor_id="CTO",
            target_type="worker_enrollment",
            target_id=str(token_id),
            payload={"token_digest": hashlib.sha256(b"secret").hexdigest()},
        )
    )

    assert authorized.decision.decision is Decision.ALLOW
    assert authorized.request.capability == "application.worker.enrollment"
    assert authorized.request.external_resources == (f"worker-enrollment:{token_id}",)

    with pytest.raises(MishkanError) as malformed:
        authority.authorize(
            ApplicationCommand(
                command_type="worker.enrollment.create",
                actor_id="CTO",
                target_type="worker_enrollment",
                target_id=str(uuid4()),
                payload={"token_digest": "not-a-digest"},
            )
        )
    assert malformed.value.envelope.code is ErrorCode.OUTPUT_CONTRACT


def test_worker_placement_command_derives_exact_contextual_scope(tmp_path: Path) -> None:
    document = _distributed_document()
    document["project"]["workspace"] = str(tmp_path)
    config = MishkanConfig.model_validate(document)
    authority = ApplicationCommandAuthority(
        config,
        tmp_path,
        _UnusedLookup(),
        _UnusedLookup(),
    )
    placement = RemotePlacementRequest(
        run_id=uuid4(),
        task_id="remote-task",
        role_definition_fingerprint="a" * 64,
        policy_fingerprint="b" * 64,
        path_scopes=(
            TaskPathScope(
                root_id="repository",
                relative_path="src",
                permissions=("read",),
            ),
        ),
        capability_requirements=(CapabilityRequirement(capability_id="file.read"),),
        resources=TaskResourceLimits(timeout_seconds=90, max_result_bytes=4096),
        deadline=utc_now() + timedelta(minutes=5),
    )

    authorized = authority.authorize(
        ApplicationCommand(
            command_type="worker.task.place",
            actor_id="CTO",
            target_type="worker_task_placement",
            target_id=str(placement.id),
            payload={"request": placement.model_dump(mode="json")},
        )
    )

    assert authorized.decision.decision is Decision.ALLOW
    assert authorized.worker_placement == placement
    assert authorized.request.capability == "application.worker.placement"
    assert authorized.request.paths == ("repository:src",)
    assert authorized.request.external_resources == (
        f"run:{placement.run_id}",
        f"task:{placement.run_id}:remote-task",
    )


def test_worker_completion_acceptance_has_typed_public_authority(tmp_path: Path) -> None:
    document = _distributed_document()
    document["project"]["workspace"] = str(tmp_path)  # type: ignore[index]
    config = MishkanConfig.model_validate(document)
    authority = ApplicationCommandAuthority(
        config,
        tmp_path,
        _UnusedLookup(),
        _UnusedLookup(),
    )
    completion_id = uuid4()
    result = InitializationResult(
        repository_revision="a" * 40,
        task_id="remote-task",
        summary="Validated remote result.",
        cited_paths=("README.md",),
        findings=("The result is grounded in the cited path.",),
    )
    review = ReviewDecision(
        task_id="remote-task",
        verdict="accepted",
        summary="Independent review accepted the result.",
        checked_citations=("README.md",),
    )

    authorized = authority.authorize(
        ApplicationCommand(
            command_type="worker.completion.accept",
            actor_id="CTO",
            target_type="worker_completion",
            target_id=str(completion_id),
            payload={
                "result": result.model_dump(mode="json"),
                "review": review.model_dump(mode="json"),
            },
        )
    )

    assert authorized.decision.decision is Decision.ALLOW
    assert authorized.request.capability == "application.worker.acceptance"
    assert authorized.worker_completion_result == result
    assert authorized.worker_completion_review == review
    assert authorized.request.external_resources == (
        f"worker-completion:{completion_id}",
        "task:remote-task",
    )
