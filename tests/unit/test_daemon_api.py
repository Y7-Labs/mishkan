from __future__ import annotations

import asyncio
import base64
import hashlib
import stat
import subprocess
import time
from datetime import timedelta
from pathlib import Path

import httpx
import pytest
from sqlalchemy import create_engine, text

from mishkan.application import (
    ApplicationCommand,
    ProspectiveRunRequest,
    RepositoryEstablishmentRequest,
)
from mishkan.config.loader import ConfigLoader
from mishkan.config.models import MishkanConfig, ProjectConfig, TelemetryConfig
from mishkan.config.presets import preset_text
from mishkan.daemon import DaemonBootstrap, create_app
from mishkan.daemon.auth import TokenFile
from mishkan.daemon.bootstrap import DaemonPaths
from mishkan.domain.errors import ErrorCode, MishkanError
from mishkan.domain.time import utc_now
from mishkan.persistence import SQLiteApplicationRepository
from mishkan.telemetry.models import (
    LangSmithFeedbackImportRequest,
    TelemetryDisclosure,
    TelemetryEvaluationImportResult,
    TelemetryRecord,
)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _config(tmp_path: Path) -> MishkanConfig:
    source = tmp_path / "config.yaml"
    source.write_text(preset_text("local"), encoding="utf-8")
    loaded = ConfigLoader().load([source]).value
    return loaded.model_copy(update={"project": ProjectConfig(workspace=tmp_path)})


def test_daemon_setup_creates_current_database_and_private_token(tmp_path: Path) -> None:
    config = _config(tmp_path)
    paths = DaemonBootstrap().setup(config, principal_id="operator-1")

    assert paths.database.is_file()
    assert stat.S_IMODE(paths.token_file.stat().st_mode) == 0o600
    assert TokenFile(paths.token_file).read().principal_id == "operator-1"


@pytest.mark.anyio
async def test_daemon_preserves_prospective_lineage_when_repository_is_established(
    tmp_path: Path,
) -> None:
    (tmp_path / "README.md").write_text("# Prospective service\n", encoding="utf-8")
    config = _config(tmp_path)
    paths = DaemonBootstrap().setup(config, principal_id="PM")
    token = TokenFile(paths.token_file).read()
    headers = {"Authorization": f"Bearer {token.token}"}
    request = ProspectiveRunRequest(
        workspace_id="prospective:daemon-fixture",
        objective="Establish the service from the observed workspace",
        outcome_id="greenfield-service",
    )
    create = ApplicationCommand(
        command_type="run.prospective.create",
        actor_id=token.principal_id,
        target_type="run",
        payload=request.model_dump(mode="json"),
    )
    transport = httpx.ASGITransport(app=create_app(config))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        created_response = await client.post(
            "/v1/commands",
            headers=headers,
            json=create.model_dump(mode="json"),
        )
        assert created_response.status_code == 200
        created = created_response.json()["payload"]
        context = created["execution_context"]
        assert context["kind"] == "prospective_workspace"
        assert context["repository_id"] is None
        assert context["repository_revision"] is None

        subprocess.run(["git", "init", "-b", "main"], cwd=tmp_path, check=True, capture_output=True)
        subprocess.run(
            ["git", "config", "user.name", "Fixture"],
            cwd=tmp_path,
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "config", "user.email", "fixture@example.invalid"],
            cwd=tmp_path,
            check=True,
            capture_output=True,
        )
        subprocess.run(["git", "add", "README.md"], cwd=tmp_path, check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "establish repository"],
            cwd=tmp_path,
            check=True,
            capture_output=True,
        )
        establishment_request = RepositoryEstablishmentRequest(
            prospective_workspace_id=request.workspace_id,
            discovery_revision=context["discovery_revision"],
            evidence_references=("artifact:repository-establishment",),
        )
        establish = ApplicationCommand(
            command_type="run.repository.establish",
            actor_id=token.principal_id,
            target_type="run",
            target_id=created["run_id"],
            payload=establishment_request.model_dump(mode="json"),
        )
        established_response = await client.post(
            "/v1/commands",
            headers=headers,
            json=establish.model_dump(mode="json"),
        )
        assert established_response.status_code == 200
        established = established_response.json()["payload"]
        assert established["execution_context"] == context
        prospective = established["repository_establishment"]["prospective_workspace"]
        assert prospective["context_kind"] == "prospective_workspace"
        assert prospective["workspace_id"] == request.workspace_id
        assert prospective["root"] == str(tmp_path.resolve())
        assert prospective["discovery_revision"] == context["discovery_revision"]
        assert len(prospective["workspace_fingerprint"]) == 64
        assert prospective["repository_id"] is None
        assert prospective["base_revision"] is None
        assert established["repository_establishment"]["repository"]["repository_id"]

        replay = await client.post(
            "/v1/commands",
            headers=headers,
            json=establish.model_dump(mode="json"),
        )
        assert replay.status_code == 200
        assert replay.json()["status"] == "accepted"
        assert replay.json()["payload"] == established


@pytest.mark.anyio
async def test_health_is_public_but_queries_require_authentication(tmp_path: Path) -> None:
    config = _config(tmp_path)
    paths = DaemonBootstrap().setup(config)
    transport = httpx.ASGITransport(app=create_app(config))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        assert (await client.get("/v1/health")).status_code == 200
        assert (await client.get("/v1/snapshot")).status_code == 403
        token = TokenFile(paths.token_file).read().token
        response = await client.get("/v1/snapshot", headers={"Authorization": f"Bearer {token}"})
        assert response.status_code == 200
        assert response.json()["cursor"] == 0


@pytest.mark.anyio
async def test_langsmith_feedback_enters_only_as_attributed_candidate_artifact(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    paths = DaemonBootstrap().setup(config)
    token = TokenFile(paths.token_file).read()
    headers = {"Authorization": f"Bearer {token.token}"}
    request = LangSmithFeedbackImportRequest(
        owner_identity=token.principal_id,
        project_name="mishkan-evaluation",
        external_feedback_id="d08f11da-cd89-4f87-b437-f962877de1cf",
        traced_run_id="b439e8d1-a9d0-4c93-9201-e98932683745",
        key="correctness",
        score=1.0,
        comment="The result follows the accepted evidence.",
        feedback_source_type="model",
        source_created_at=utc_now(),
    )
    command = ApplicationCommand(
        command_type="telemetry.evaluation.import",
        actor_id=token.principal_id,
        target_type="telemetry_evaluation",
        target_id=str(request.import_id),
        payload={"request": request.model_dump(mode="json")},
    )
    transport = httpx.ASGITransport(app=create_app(config))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/v1/commands",
            headers=headers,
            json=command.model_dump(mode="json"),
        )
        assert response.status_code == 200
        result = TelemetryEvaluationImportResult.model_validate(response.json()["payload"])
        assert result.candidate.authority == "candidate_only"
        assert result.candidate.accepted is False
        assert result.candidate.request.external_feedback_id == request.external_feedback_id

        artifact_id = result.artifact_reference.removeprefix("artifact:")
        artifact = await client.get(f"/v1/artifacts/{artifact_id}/content", headers=headers)
        assert artifact.status_code == 200
        persisted = artifact.json()
        assert persisted["authority"] == "candidate_only"
        assert persisted["accepted"] is False

        replay = await client.post(
            "/v1/commands",
            headers=headers,
            json=command.model_dump(mode="json"),
        )
        assert replay.json()["payload"] == response.json()["payload"]


@pytest.mark.anyio
async def test_daemon_rejects_request_bodies_above_the_public_configured_bound(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    assert config.daemon is not None
    config = config.model_copy(
        update={"daemon": config.daemon.model_copy(update={"max_request_bytes": 128})}
    )
    paths = DaemonBootstrap().setup(config)
    token = TokenFile(paths.token_file).read().token
    transport = httpx.ASGITransport(app=create_app(config))

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/v1/commands",
            headers={"Authorization": f"Bearer {token}"},
            content=b"x" * 129,
        )

    assert response.status_code == 413
    assert response.json()["code"] == ErrorCode.OUTPUT_CONTRACT


@pytest.mark.anyio
async def test_authenticated_command_and_event_query_share_durable_contract(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    paths = DaemonBootstrap().setup(config)
    token = TokenFile(paths.token_file).read().token
    headers = {"Authorization": f"Bearer {token}"}
    command = ApplicationCommand(
        command_type="system.checkpoint",
        actor_id="local-operator",
        target_type="system",
        target_id="local-instance",
        expected_revision=0,
        payload={"checkpoint": "daemon-api"},
    )

    transport = httpx.ASGITransport(app=create_app(config))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        first = await client.post(
            "/v1/commands", headers=headers, json=command.model_dump(mode="json")
        )
        retried = await client.post(
            "/v1/commands", headers=headers, json=command.model_dump(mode="json")
        )
        events = await client.get("/v1/events", headers=headers)

    assert first.status_code == 200
    assert retried.json() == first.json()
    assert events.status_code == 200
    assert len(events.json()["events"]) == 1
    assert events.json()["events"][0]["command_id"] == str(command.command_id)
    event_payload = events.json()["events"][0]["payload"]
    assert event_payload == {
        "authorization_decision": "allow",
        "authorization_request_fingerprint": event_payload["authorization_request_fingerprint"],
        "command_type": "system.checkpoint",
        "matched_rule_ids": ["local.application-commands"],
        "policy_fingerprint": event_payload["policy_fingerprint"],
        "policy_revisions": ["bundled.local@30"],
        "request_schema_version": "1.0",
        "payload_fields": ["checkpoint"],
        "result_fields": ["recorded"],
    }
    assert len(event_payload["authorization_request_fingerprint"]) == 64
    assert len(event_payload["policy_fingerprint"]) == 64


@pytest.mark.anyio
async def test_command_telemetry_is_derived_after_acceptance_and_queryable(tmp_path: Path) -> None:
    class Exporter:
        def __init__(self) -> None:
            self.records: list[TelemetryRecord] = []

        def export(self, record: TelemetryRecord) -> bool:
            self.records.append(record)
            return True

    exporter = Exporter()
    config = _config(tmp_path).model_copy(
        update={
            "telemetry": TelemetryConfig(
                disclosure=TelemetryDisclosure.METADATA_ONLY,
                exporter={
                    "kind": "otlp_http",
                    "endpoint": "https://telemetry.example/v1/traces",
                    "network_profile": "public-read",
                },
                metadata_attributes=(
                    "mishkan.command.id",
                    "mishkan.command.type",
                    "mishkan.result.status",
                ),
                include_command_payload=True,
            )
        }
    )
    paths = DaemonBootstrap().setup(config)
    token = TokenFile(paths.token_file).read().token
    headers = {"Authorization": f"Bearer {token}"}
    command = ApplicationCommand(
        command_type="system.checkpoint",
        actor_id="local-operator",
        target_type="system",
        target_id="telemetry-checkpoint",
        payload={"checkpoint": "must-not-cross-metadata-only"},
    )
    transport = httpx.ASGITransport(
        app=create_app(config, telemetry_exporter_factory=lambda: exporter)
    )
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/v1/commands", headers=headers, json=command.model_dump(mode="json")
        )
        assert response.json()["status"] == "accepted"
        for _ in range(100):
            status = await client.get("/v1/telemetry/status", headers=headers)
            if status.json()["attempted"] == 1:
                break
            await asyncio.sleep(0.01)

    assert status.json()["exported"] == 1
    assert exporter.records[0].attributes == {
        "mishkan.command.id": str(command.command_id),
        "mishkan.command.type": "system.checkpoint",
        "mishkan.result.status": "accepted",
    }


@pytest.mark.anyio
async def test_slow_command_keeps_health_responsive_and_deduplicates_in_flight(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from mishkan.daemon import api as daemon_api

    config = _config(tmp_path)
    paths = DaemonBootstrap().setup(config)
    token = TokenFile(paths.token_file).read().token
    headers = {"Authorization": f"Bearer {token}"}
    command = ApplicationCommand(
        command_type="system.checkpoint",
        actor_id="local-operator",
        target_type="system",
        target_id="slow-checkpoint",
        payload={"checkpoint": "slow"},
    )
    original = daemon_api._dispatch
    invocations = 0

    def delayed_dispatch(*args: object, **kwargs: object) -> tuple[str, dict[str, object]]:
        nonlocal invocations
        invocations += 1
        time.sleep(0.3)
        return original(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(daemon_api, "_dispatch", delayed_dispatch)
    transport = httpx.ASGITransport(app=create_app(config))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        first = asyncio.create_task(
            client.post("/v1/commands", headers=headers, json=command.model_dump(mode="json"))
        )
        await asyncio.sleep(0.05)
        started = time.perf_counter()
        health = await asyncio.wait_for(client.get("/v1/health"), timeout=0.15)
        health_latency = time.perf_counter() - started
        duplicate = asyncio.create_task(
            client.post("/v1/commands", headers=headers, json=command.model_dump(mode="json"))
        )
        first_response, duplicate_response = await asyncio.gather(first, duplicate)

    assert health.status_code == 200
    assert health_latency < 0.15
    assert first_response.json() == duplicate_response.json()
    assert invocations == 1


@pytest.mark.anyio
async def test_waiting_command_rechecks_revision_before_dispatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from mishkan.daemon import api as daemon_api

    config = _config(tmp_path)
    paths = DaemonBootstrap().setup(config)
    token = TokenFile(paths.token_file).read().token
    headers = {"Authorization": f"Bearer {token}"}
    commands = [
        ApplicationCommand(
            command_type="system.checkpoint",
            actor_id="local-operator",
            target_type="system",
            target_id="shared-checkpoint",
            expected_revision=0,
            payload={"checkpoint": f"sequence-{sequence}"},
        )
        for sequence in (1, 2)
    ]
    original = daemon_api._dispatch
    invocations = 0

    def delayed_dispatch(*args: object, **kwargs: object) -> tuple[str, dict[str, object]]:
        nonlocal invocations
        invocations += 1
        time.sleep(0.2)
        return original(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(daemon_api, "_dispatch", delayed_dispatch)
    transport = httpx.ASGITransport(app=create_app(config))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        first = asyncio.create_task(
            client.post("/v1/commands", headers=headers, json=commands[0].model_dump(mode="json"))
        )
        await asyncio.sleep(0.03)
        second = asyncio.create_task(
            client.post("/v1/commands", headers=headers, json=commands[1].model_dump(mode="json"))
        )
        first_response, second_response = await asyncio.gather(first, second)

    assert first_response.status_code == 200, first_response.text
    assert first_response.json()["status"] == "accepted"
    assert second_response.json()["status"] == "refused"
    assert second_response.json()["error"]["code"] == ErrorCode.REVISION_MISMATCH
    assert second_response.json()["error"]["details"]["effect_dispatched"] is False
    assert invocations == 1


@pytest.mark.anyio
async def test_registry_lifecycle_command_is_atomic_idempotent_and_revisioned(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    paths = DaemonBootstrap().setup(config)
    token_record = TokenFile(paths.token_file).read()
    headers = {"Authorization": f"Bearer {token_record.token}"}
    disabled = ApplicationCommand(
        command_type="registry.entry.disable",
        actor_id=token_record.principal_id,
        target_type="registry_entry",
        target_id="adapter:native.repository.read_file",
        expected_revision=0,
        payload={"entry_kind": "adapter"},
    )

    transport = httpx.ASGITransport(app=create_app(config))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        first = await client.post(
            "/v1/commands", headers=headers, json=disabled.model_dump(mode="json")
        )
        replayed = await client.post(
            "/v1/commands", headers=headers, json=disabled.model_dump(mode="json")
        )
        stale = ApplicationCommand(
            command_type="registry.entry.enable",
            actor_id=token_record.principal_id,
            target_type="registry_entry",
            target_id="adapter:native.repository.read_file",
            expected_revision=0,
            payload={"entry_kind": "adapter"},
        )
        refused = await client.post(
            "/v1/commands", headers=headers, json=stale.model_dump(mode="json")
        )
        registry = await client.get("/v1/tools/registry", headers=headers)
        events = await client.get(
            "/v1/events", headers=headers, params={"entity_type": "registry_entry"}
        )

    assert first.status_code == 200
    assert first.json()["status"] == "accepted"
    assert first.json()["revision"] == 1
    assert replayed.json() == first.json()
    assert refused.status_code == 200
    assert refused.json()["status"] == "refused"
    assert refused.json()["error"]["code"] == ErrorCode.REVISION_MISMATCH
    assert registry.json()["entries"] == [
        {
            "id": registry.json()["entries"][0]["id"],
            "entry_kind": "adapter",
            "identity": "native.repository.read_file",
            "enabled": False,
            "removed": False,
            "precedence": 0,
            "revision": 1,
            "definition": None,
            "definition_fingerprint": None,
            "updated_at": registry.json()["entries"][0]["updated_at"],
        }
    ]
    assert [event["event_type"] for event in events.json()["events"]] == [
        "registry.entry_disable",
        "application.command_refused",
    ]


@pytest.mark.anyio
async def test_event_retention_is_explicit_authorized_and_inspectable(tmp_path: Path) -> None:
    config = _config(tmp_path)
    paths = DaemonBootstrap().setup(config)
    token_record = TokenFile(paths.token_file).read()
    headers = {"Authorization": f"Bearer {token_record.token}"}
    command = ApplicationCommand(
        command_type="event.retention.plan",
        actor_id=token_record.principal_id,
        target_type="event_store",
        payload={},
    )

    transport = httpx.ASGITransport(app=create_app(config))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        planned = await client.post(
            "/v1/commands", headers=headers, json=command.model_dump(mode="json")
        )
        inspected = await client.get("/v1/events/retention-plans", headers=headers)

    assert planned.status_code == 200
    assert planned.json()["payload"]["state"] == "planned"
    assert planned.json()["payload"]["policy"]["max_age_days"] == 30
    assert inspected.status_code == 200
    assert inspected.json() == [planned.json()["payload"]]


@pytest.mark.anyio
async def test_command_cannot_claim_another_actor(tmp_path: Path) -> None:
    config = _config(tmp_path)
    paths = DaemonBootstrap().setup(config)
    token = TokenFile(paths.token_file).read().token
    command = ApplicationCommand(
        command_type="system.checkpoint",
        actor_id="another-actor",
        target_type="system",
        payload={},
    )

    transport = httpx.ASGITransport(app=create_app(config))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/v1/commands",
            headers={"Authorization": f"Bearer {token}"},
            json=command.model_dump(mode="json"),
        )

    assert response.status_code == 403
    assert response.json()["code"] == "ERR-POL-001"


@pytest.mark.anyio
async def test_public_policy_refusal_is_idempotent_audited_and_prevents_effect(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    policy = tmp_path / "operator-policy.yaml"
    policy.write_text(
        """\
schema_version: "1.0"
source_id: test.operator
revision: "1"
adoption_authority: test
priority: 100
rules:
  - rule_id: test.deny-session-start
    priority: 100
    decision: deny
    scope:
      identities: [local-operator]
      objective_classes: [application-command]
      repositories: ["*"]
      outcomes: [session.start]
      roles: [application-client]
      capabilities: [application.session.start]
      effect_classes: [process]
""",
        encoding="utf-8",
    )
    config = config.model_copy(update={"policy_sources": (str(policy),)})
    paths = DaemonBootstrap().setup(config)
    token = TokenFile(paths.token_file).read().token
    headers = {"Authorization": f"Bearer {token}"}
    request = {
        "mode": "job",
        "owner": "local-operator",
        "run_id": "run-denied",
        "task_id": "task-denied",
        "cwd": ".",
        "executable": "/bin/sh",
        "args": ["-c", "touch must-not-exist"],
        "environment": {},
        "credential_environment": {},
        "credential_references": [],
        "session_profile": "standard",
        "deadline": (utc_now() + timedelta(minutes=1)).isoformat(),
        "policy_fingerprint": "0" * 64,
    }
    command = ApplicationCommand(
        command_type="session.start",
        actor_id="local-operator",
        target_type="session_service",
        payload={"request": request},
    )
    transport = httpx.ASGITransport(app=create_app(config))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        first = await client.post(
            "/v1/commands", headers=headers, json=command.model_dump(mode="json")
        )
        replayed = await client.post(
            "/v1/commands", headers=headers, json=command.model_dump(mode="json")
        )
        sessions = await client.get("/v1/sessions", headers=headers)
        events = await client.get("/v1/events", headers=headers)
        security_events = await client.get(
            "/v1/events",
            headers=headers,
            params={"security_relevant": "true", "identity_id": "local-operator"},
        )

    assert first.status_code == 200
    assert first.json()["status"] == "refused"
    assert first.json()["error"]["code"] == "ERR-POL-001"
    assert replayed.json() == first.json()
    assert sessions.json() == []
    assert not (tmp_path / "must-not-exist").exists()
    assert events.json()["events"][0]["event_type"] == "application.command_refused"
    assert events.json()["events"][0]["sensitivity"] == "security"
    assert events.json()["events"][0]["payload"]["authorization_decision"] == "deny"
    assert len(security_events.json()["events"]) == 1
    assert security_events.json()["events"][0]["security_relevant"] is True
    assert security_events.json()["events"][0]["identity_id"] == "local-operator"


@pytest.mark.anyio
async def test_git_stage_is_a_typed_daemon_command_with_durable_evidence(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    (tmp_path / "app.txt").write_text("content")
    config = _config(tmp_path)
    policy = tmp_path / "git-policy.yaml"
    policy.write_text(
        """\
schema_version: "1.0"
source_id: test.git
revision: "1"
adoption_authority: test
rules:
  - rule_id: test.git-stage
    decision: allow
    scope:
      identities: [local-operator]
      objective_classes: [application-command]
      repositories: ["*"]
      outcomes: [git.stage]
      roles: [application-client]
      capabilities: [git.stage]
      effect_classes: [repository_write]
      effects: [git.stage]
      paths: [app.txt]
""",
        encoding="utf-8",
    )
    config = config.model_copy(update={"policy_sources": (str(policy),)})
    paths = DaemonBootstrap().setup(config)
    token = TokenFile(paths.token_file).read().token
    headers = {"Authorization": f"Bearer {token}"}
    command = ApplicationCommand(
        command_type="git.stage",
        actor_id="local-operator",
        target_type="git_repository",
        target_id=str(tmp_path.resolve()),
        payload={
            "request": {
                "mode": "stage",
                "workspace": str(tmp_path),
                "paths": ["app.txt"],
            }
        },
    )

    transport = httpx.ASGITransport(app=create_app(config))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/v1/commands", headers=headers, json=command.model_dump(mode="json")
        )
        events = await client.get("/v1/events", headers=headers)

    assert response.status_code == 200
    assert response.json()["status"] == "accepted"
    assert response.json()["payload"]["mode"] == "stage"
    assert response.json()["payload"]["changed_paths"] == ["app.txt"]
    assert response.json()["payload"]["settlement"] == "completed"
    assert events.json()["events"][-1]["event_type"] == "git.stage_settled"


@pytest.mark.anyio
async def test_invalid_effect_command_is_rejected_before_reservation(tmp_path: Path) -> None:
    config = _config(tmp_path)
    paths = DaemonBootstrap().setup(config)
    token = TokenFile(paths.token_file).read().token
    command = ApplicationCommand(
        command_type="session.start",
        actor_id="local-operator",
        target_type="session",
        target_id="not-a-session",
        payload={"request": {}},
    )
    transport = httpx.ASGITransport(app=create_app(config))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/v1/commands",
            headers={"Authorization": f"Bearer {token}"},
            json=command.model_dump(mode="json"),
        )
        sessions = await client.get("/v1/sessions", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 422
    assert response.json()["code"] == "ERR-OUT-001"
    assert sessions.json() == []
    assert (
        SQLiteApplicationRepository(paths.database).command_result(str(command.command_id)) is None
    )


@pytest.mark.anyio
async def test_authorized_dispatch_failure_is_a_replayable_typed_refusal(tmp_path: Path) -> None:
    config = _config(tmp_path)
    paths = DaemonBootstrap().setup(config)
    token = TokenFile(paths.token_file).read().token
    headers = {"Authorization": f"Bearer {token}"}
    command = ApplicationCommand(
        command_type="artifact.upload.chunk",
        actor_id="local-operator",
        target_type="artifact_upload",
        target_id="00000000-0000-0000-0000-000000000001",
        payload={"offset": 0, "content_base64": "not-base64"},
    )
    transport = httpx.ASGITransport(app=create_app(config))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        first = await client.post(
            "/v1/commands", headers=headers, json=command.model_dump(mode="json")
        )
        replay = await client.post(
            "/v1/commands", headers=headers, json=command.model_dump(mode="json")
        )
        events = await client.get("/v1/events", headers=headers)

    assert first.status_code == 200
    assert replay.json() == first.json()
    assert first.json()["status"] == "refused"
    assert first.json()["error"]["code"] == ErrorCode.OUTPUT_CONTRACT
    assert events.json()["events"][-1]["event_type"] == ("application.command_effect_unaccepted")


@pytest.mark.anyio
async def test_session_declared_effects_are_part_of_authoritative_policy_scope(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    policy = tmp_path / "session-policy.yaml"
    policy.write_text(
        """\
schema_version: "1.0"
source_id: test.session
revision: "1"
adoption_authority: test
priority: 100
rules:
  - rule_id: test.allow-effectless-session
    priority: 100
    decision: allow
    scope:
      identities: [local-operator]
      objective_classes: [application-command]
      repositories: ["*"]
      outcomes: [session.start]
      roles: [application-client]
      capabilities: [application.session.start]
      effect_classes: [process]
      effects: [process.start]
      paths: ["*"]
      executables: ["*"]
      arguments: ["*"]
      environments: ["*"]
      credentials: ["*"]
      max_timeout_seconds: 3600
      allow_network: false
""",
        encoding="utf-8",
    )
    config = config.model_copy(update={"policy_sources": (str(policy),)})
    paths = DaemonBootstrap().setup(config)
    token = TokenFile(paths.token_file).read().token
    request = {
        "mode": "job",
        "owner": "local-operator",
        "run_id": "run-effect",
        "task_id": "task-effect",
        "cwd": ".",
        "executable": "/bin/sh",
        "args": ["-c", "touch must-not-exist"],
        "session_profile": "standard",
        "deadline": (utc_now() + timedelta(minutes=1)).isoformat(),
        "declared_effects": ["filesystem.write"],
        "network_destinations": [],
        "policy_fingerprint": "a" * 64,
    }
    command = ApplicationCommand(
        command_type="session.start",
        actor_id="local-operator",
        target_type="session_service",
        payload={"request": request},
    )

    transport = httpx.ASGITransport(app=create_app(config))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/v1/commands",
            headers={"Authorization": f"Bearer {token}"},
            json=command.model_dump(mode="json"),
        )

    assert response.status_code == 200
    assert response.json()["status"] == "refused"
    assert not (tmp_path / "must-not-exist").exists()


@pytest.mark.anyio
async def test_artifact_upload_commands_publish_only_verified_content(tmp_path: Path) -> None:
    config = _config(tmp_path)
    paths = DaemonBootstrap().setup(config)
    token = TokenFile(paths.token_file).read().token
    headers = {"Authorization": f"Bearer {token}"}
    content = b"daemon artifact"
    provenance = {
        "producer_identity": "engineer",
        "run_id": "run-1",
        "task_attempt_id": "attempt-1",
        "call_id": "call-1",
        "capability": "terminal.process",
        "channel": "stdout",
    }
    transport = httpx.ASGITransport(app=create_app(config))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        opened = await client.post(
            "/v1/commands",
            headers=headers,
            json=ApplicationCommand(
                command_type="artifact.upload.open",
                actor_id="local-operator",
                target_type="artifact_service",
                expected_revision=0,
                payload={
                    "expected_size": len(content),
                    "expected_digest": f"sha256:{hashlib.sha256(content).hexdigest()}",
                    "media_type": "text/plain",
                    "provenance": provenance,
                },
            ).model_dump(mode="json"),
        )
        upload_id = opened.json()["payload"]["upload_id"]
        chunked = await client.post(
            "/v1/commands",
            headers=headers,
            json=ApplicationCommand(
                command_type="artifact.upload.chunk",
                actor_id="local-operator",
                target_type="artifact_upload",
                target_id=upload_id,
                expected_revision=0,
                payload={
                    "offset": 0,
                    "content_base64": base64.b64encode(content).decode(),
                },
            ).model_dump(mode="json"),
        )
        committed = await client.post(
            "/v1/commands",
            headers=headers,
            json=ApplicationCommand(
                command_type="artifact.upload.commit",
                actor_id="local-operator",
                target_type="artifact_upload",
                target_id=upload_id,
                expected_revision=1,
                payload={},
            ).model_dump(mode="json"),
        )
        artifact_id = committed.json()["payload"]["id"]
        body = await client.get(f"/v1/artifacts/{artifact_id}/content", headers=headers)
        held = await client.post(
            "/v1/commands",
            headers=headers,
            json=ApplicationCommand(
                command_type="artifact.hold.set",
                actor_id="local-operator",
                target_type="artifact",
                target_id=artifact_id,
                expected_revision=0,
                payload={"reason": "acceptance evidence"},
            ).model_dump(mode="json"),
        )
        collected = await client.post(
            "/v1/commands",
            headers=headers,
            json=ApplicationCommand(
                command_type="artifact.collection.create",
                actor_id="local-operator",
                target_type="artifact_service",
                payload={"entries": {"evidence/output.txt": f"artifact:{artifact_id}"}},
            ).model_dump(mode="json"),
        )
        holds = await client.get("/v1/artifact-holds", headers=headers)
        collections = await client.get("/v1/artifact-collections", headers=headers)
        released = await client.post(
            "/v1/commands",
            headers=headers,
            json=ApplicationCommand(
                command_type="artifact.hold.release",
                actor_id="local-operator",
                target_type="artifact",
                target_id=artifact_id,
                expected_revision=1,
                payload={},
            ).model_dump(mode="json"),
        )

    assert opened.status_code == 200
    assert chunked.status_code == 200
    assert committed.status_code == 200
    assert committed.json()["payload"]["facts"]["security_scan"] == "passed"
    assert body.content == content
    assert held.status_code == 200
    assert collected.status_code == 200
    assert holds.json()[0]["reason"] == "acceptance evidence"
    assert collections.json()[0]["ordered_paths"] == ["evidence/output.txt"]
    assert released.status_code == 200


@pytest.mark.anyio
@pytest.mark.secrets
async def test_artifact_secret_is_refused_with_non_secret_durable_evidence(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    paths = DaemonBootstrap().setup(config)
    token = TokenFile(paths.token_file).read().token
    headers = {"Authorization": f"Bearer {token}"}
    content = b"api_key=must-never-become-an-artifact"
    provenance = {
        "producer_identity": "engineer",
        "run_id": "run-secret",
        "task_attempt_id": "attempt-secret",
        "call_id": "call-secret",
        "capability": "terminal.process",
        "channel": "stdout",
    }
    transport = httpx.ASGITransport(app=create_app(config))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        opened = await client.post(
            "/v1/commands",
            headers=headers,
            json=ApplicationCommand(
                command_type="artifact.upload.open",
                actor_id="local-operator",
                target_type="artifact_service",
                payload={
                    "expected_size": len(content),
                    "expected_digest": f"sha256:{hashlib.sha256(content).hexdigest()}",
                    "media_type": "text/plain",
                    "provenance": provenance,
                },
            ).model_dump(mode="json"),
        )
        upload_id = opened.json()["payload"]["upload_id"]
        await client.post(
            "/v1/commands",
            headers=headers,
            json=ApplicationCommand(
                command_type="artifact.upload.chunk",
                actor_id="local-operator",
                target_type="artifact_upload",
                target_id=upload_id,
                payload={
                    "offset": 0,
                    "content_base64": base64.b64encode(content).decode(),
                },
            ).model_dump(mode="json"),
        )
        commit_command = ApplicationCommand(
            command_type="artifact.upload.commit",
            actor_id="local-operator",
            target_type="artifact_upload",
            target_id=upload_id,
            payload={},
        )
        refused = await client.post(
            "/v1/commands",
            headers=headers,
            json=commit_command.model_dump(mode="json"),
        )
        replayed = await client.post(
            "/v1/commands",
            headers=headers,
            json=commit_command.model_dump(mode="json"),
        )
        artifacts = await client.get("/v1/artifacts", headers=headers)
        upload = await client.get(f"/v1/artifact-uploads/{upload_id}", headers=headers)
        events = await client.get("/v1/events", headers=headers)

    assert refused.status_code == 200
    assert refused.json()["status"] == "refused"
    assert refused.json()["error"]["code"] == ErrorCode.SECRET_CONTENT
    assert replayed.json() == refused.json()
    assert artifacts.json() == []
    assert upload.json()["lifecycle"] == "aborted"
    assert not (paths.artifacts / "staging" / f"{upload_id}.upload").exists()
    final_event = events.json()["events"][-1]
    assert final_event["event_type"] == "application.command_effect_unaccepted"
    assert final_event["payload"]["error_code"] == ErrorCode.SECRET_CONTENT
    assert content.decode() not in str(final_event)


@pytest.mark.anyio
async def test_artifact_reconciliation_is_a_governed_plan_then_apply_command(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    paths = DaemonBootstrap().setup(config)
    token = TokenFile(paths.token_file).read().token
    headers = {"Authorization": f"Bearer {token}"}
    orphan = paths.artifacts / "blobs" / "sha256" / "aa" / ("c" * 62)
    orphan.parent.mkdir(parents=True, exist_ok=True)
    orphan.write_bytes(b"orphan")
    transport = httpx.ASGITransport(app=create_app(config))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        planned = await client.post(
            "/v1/commands",
            headers=headers,
            json=ApplicationCommand(
                command_type="artifact.reconcile.plan",
                actor_id="local-operator",
                target_type="artifact_service",
                payload={},
            ).model_dump(mode="json"),
        )
        plan_id = planned.json()["payload"]["plan_id"]
        assert orphan.exists()
        applied = await client.post(
            "/v1/commands",
            headers=headers,
            json=ApplicationCommand(
                command_type="artifact.reconcile.apply",
                actor_id="local-operator",
                target_type="artifact_reconciliation_plan",
                target_id=plan_id,
                payload={},
            ).model_dump(mode="json"),
        )

    assert planned.status_code == 200
    assert planned.json()["payload"]["issues"][0]["action"] == "delete_orphan_blob"
    assert applied.status_code == 200
    assert applied.json()["payload"]["applied"] is True
    assert not orphan.exists()


@pytest.mark.anyio
async def test_managed_job_is_started_and_observed_through_commands(tmp_path: Path) -> None:
    config = _config(tmp_path)
    paths = DaemonBootstrap().setup(config)
    token = TokenFile(paths.token_file).read().token
    headers = {"Authorization": f"Bearer {token}"}
    request = {
        "mode": "job",
        "owner": "local-operator",
        "run_id": "run-1",
        "task_id": "task-1",
        "cwd": ".",
        "executable": "/bin/sh",
        "args": ["-c", "printf daemon-job"],
        "environment": {},
        "credential_environment": {},
        "credential_references": [],
        "session_profile": "standard",
        "deadline": (utc_now() + timedelta(minutes=1)).isoformat(),
        "policy_fingerprint": "a" * 64,
    }
    transport = httpx.ASGITransport(app=create_app(config))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        started = await client.post(
            "/v1/commands",
            headers=headers,
            json=ApplicationCommand(
                command_type="session.start",
                actor_id="local-operator",
                target_type="session_service",
                payload={"request": request},
            ).model_dump(mode="json"),
        )
        session_id = started.json()["payload"]["execution_id"]
        for _ in range(50):
            observed = await client.get(f"/v1/sessions/{session_id}", headers=headers)
            if observed.json()["state"] in {"settled", "failed"}:
                break
            await asyncio.sleep(0.01)
        output = await client.get(
            f"/v1/sessions/{session_id}/output",
            headers=headers,
            params={"channel": "stdout", "offset": 0},
        )

    assert started.status_code == 200
    assert observed.json()["state"] == "settled"
    assert output.json()["data"] == "daemon-job"


@pytest.mark.anyio
async def test_session_credentials_are_references_resolved_after_policy_and_never_persisted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path)
    paths = DaemonBootstrap().setup(config)
    token = TokenFile(paths.token_file).read().token
    headers = {"Authorization": f"Bearer {token}"}
    secret = "SESSION-CREDENTIAL-CANARY"
    monkeypatch.setenv("MISHKAN_SESSION_TEST_TOKEN", secret)
    request = {
        "mode": "job",
        "owner": "local-operator",
        "run_id": "run-credential",
        "task_id": "task-credential",
        "cwd": ".",
        "executable": "/bin/sh",
        "args": ["-c", 'printf %s "$TOKEN"'],
        "environment": {},
        "credential_environment": {
            "TOKEN": {"source": "env", "locator": "MISHKAN_SESSION_TEST_TOKEN"}
        },
        "credential_references": [],
        "session_profile": "standard",
        "deadline": (utc_now() + timedelta(minutes=1)).isoformat(),
        "policy_fingerprint": "f" * 64,
    }
    transport = httpx.ASGITransport(app=create_app(config))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        started = await client.post(
            "/v1/commands",
            headers=headers,
            json=ApplicationCommand(
                command_type="session.start",
                actor_id="local-operator",
                target_type="session_service",
                payload={"request": request},
            ).model_dump(mode="json"),
        )
        session_id = started.json()["payload"]["execution_id"]
        for _ in range(50):
            observed = await client.get(f"/v1/sessions/{session_id}", headers=headers)
            if observed.json()["state"] in {"settled", "failed"}:
                break
            await asyncio.sleep(0.01)
        output = await client.get(
            f"/v1/sessions/{session_id}/output",
            headers=headers,
            params={"channel": "stdout", "offset": 0},
        )
        events = await client.get("/v1/events", headers=headers)

    with create_engine(f"sqlite:///{paths.database}").connect() as connection:
        persisted = connection.execute(
            text("SELECT request_payload FROM execution_sessions WHERE id = :id"),
            {"id": session_id},
        ).scalar_one()
    assert started.status_code == 200
    assert observed.json()["state"] == "settled"
    assert secret not in output.json()["data"]
    assert "[REDACTED]" in output.json()["data"]
    assert secret not in str(persisted)
    assert "MISHKAN_SESSION_TEST_TOKEN" in str(persisted)
    assert secret not in str(events.json())


@pytest.mark.anyio
@pytest.mark.secrets
async def test_secret_like_session_request_is_blocked_before_process_and_persistence(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    paths = DaemonBootstrap().setup(config)
    token = TokenFile(paths.token_file).read().token
    headers = {"Authorization": f"Bearer {token}"}
    request = {
        "mode": "job",
        "owner": "local-operator",
        "run_id": "run-secret-request",
        "task_id": "task-secret-request",
        "cwd": ".",
        "executable": "/bin/sh",
        "args": ["-c", "printf api_key=must-not-persist"],
        "environment": {},
        "credential_environment": {},
        "credential_references": [],
        "session_profile": "standard",
        "deadline": (utc_now() + timedelta(minutes=1)).isoformat(),
        "policy_fingerprint": "f" * 64,
    }
    command = ApplicationCommand(
        command_type="session.start",
        actor_id="local-operator",
        target_type="session_service",
        payload={"request": request},
    )
    transport = httpx.ASGITransport(app=create_app(config))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        refused = await client.post(
            "/v1/commands",
            headers=headers,
            json=command.model_dump(mode="json"),
        )
        replayed = await client.post(
            "/v1/commands",
            headers=headers,
            json=command.model_dump(mode="json"),
        )
        sessions = await client.get("/v1/sessions", headers=headers)

    assert refused.status_code == 200
    assert refused.json()["status"] == "refused"
    assert refused.json()["error"]["code"] == ErrorCode.SECRET_CONTENT
    assert replayed.json() == refused.json()
    assert sessions.json() == []
    assert not any(paths.sessions.iterdir())


def test_token_rotation_invalidates_previous_credential(tmp_path: Path) -> None:
    config = _config(tmp_path)
    paths = DaemonBootstrap().setup(config)
    token_file = TokenFile(paths.token_file)
    previous = token_file.read().token
    current = token_file.rotate().token

    assert previous != current
    assert token_file.authenticate(previous) is None
    assert token_file.authenticate(current) is not None


def test_daemon_paths_are_project_scoped(tmp_path: Path) -> None:
    config = _config(tmp_path)
    paths = DaemonPaths.from_config(config)
    assert paths.database.is_relative_to(tmp_path)
    assert paths.token_file.is_relative_to(tmp_path)


def test_application_bootstrap_stays_below_ten_seconds(tmp_path: Path) -> None:
    config = _config(tmp_path)
    DaemonBootstrap().setup(config)

    started = time.perf_counter()
    application = create_app(config)

    assert application.title == "MISHKAN application API"
    assert time.perf_counter() - started < 10


@pytest.mark.anyio
async def test_crash_after_effect_reservation_refuses_implicit_replay(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path)
    paths = DaemonBootstrap().setup(config)
    token = TokenFile(paths.token_file).read().token
    headers = {"Authorization": f"Bearer {token}"}
    request = {
        "mode": "job",
        "owner": "local-operator",
        "run_id": "run-crash",
        "task_id": "task-crash",
        "cwd": ".",
        "executable": "/bin/sh",
        "args": ["-c", "sleep 10"],
        "environment": {},
        "credential_environment": {},
        "credential_references": [],
        "session_profile": "standard",
        "deadline": (utc_now() + timedelta(minutes=1)).isoformat(),
        "policy_fingerprint": "b" * 64,
    }
    command = ApplicationCommand(
        command_type="session.start",
        actor_id="local-operator",
        target_type="session_service",
        payload={"request": request},
    )
    original = SQLiteApplicationRepository.complete_reserved

    def crash(*_args: object, **_kwargs: object):  # type: ignore[no-untyped-def]
        raise MishkanError(ErrorCode.RUN_INTERRUPTED, "injected crash after effect")

    monkeypatch.setattr(SQLiteApplicationRepository, "complete_reserved", crash)
    transport = httpx.ASGITransport(app=create_app(config))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        crashed = await client.post(
            "/v1/commands", headers=headers, json=command.model_dump(mode="json")
        )
        sessions_after_crash = await client.get("/v1/sessions", headers=headers)
        monkeypatch.setattr(SQLiteApplicationRepository, "complete_reserved", original)
        replayed = await client.post(
            "/v1/commands", headers=headers, json=command.model_dump(mode="json")
        )
        sessions_after_replay = await client.get("/v1/sessions", headers=headers)
        session_id = sessions_after_crash.json()[0]["execution_id"]
        cancelled = await client.post(
            "/v1/commands",
            headers=headers,
            json=ApplicationCommand(
                command_type="session.cancel",
                actor_id="local-operator",
                target_type="session",
                target_id=session_id,
                payload={},
            ).model_dump(mode="json"),
        )

    assert crashed.status_code == 400
    assert len(sessions_after_crash.json()) == 1
    assert replayed.status_code == 200
    assert replayed.json()["status"] == "refused"
    assert replayed.json()["error"]["details"]["automatic_retry"] is False
    assert len(sessions_after_replay.json()) == 1
    assert cancelled.status_code == 200


@pytest.mark.anyio
async def test_sse_removed_last_event_id_returns_explicit_snapshot_gap(tmp_path: Path) -> None:
    config = _config(tmp_path)
    paths = DaemonBootstrap().setup(config)
    token = TokenFile(paths.token_file).read().token
    headers = {"Authorization": f"Bearer {token}"}
    transport = httpx.ASGITransport(app=create_app(config))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        for index in range(3):
            response = await client.post(
                "/v1/commands",
                headers=headers,
                json=ApplicationCommand(
                    command_type="system.checkpoint",
                    actor_id="local-operator",
                    target_type="system",
                    target_id=str(index),
                    payload={"index": index},
                ).model_dump(mode="json"),
            )
            assert response.status_code == 200
        with create_engine(f"sqlite:///{paths.database}").begin() as connection:
            connection.execute(text("DELETE FROM event_outbox WHERE cursor <= 2"))
        gap = await client.get(
            "/v1/events/stream",
            headers={**headers, "Last-Event-ID": "1"},
        )
        malformed = await client.get(
            "/v1/events/stream",
            headers={**headers, "Last-Event-ID": "not-a-cursor"},
        )

    assert gap.status_code == 400
    assert gap.json()["details"]["category"] == "cursor_gap"
    assert gap.json()["details"]["snapshot_required"] is True
    assert malformed.status_code == 422
