from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from crewai import Crew
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from mishkan.application import ApplicationCommand, CommandStatus
from mishkan.config.loader import ConfigLoader
from mishkan.config.models import MishkanConfig, ProjectConfig
from mishkan.config.presets import preset_text
from mishkan.crewai.flow import CrewAIInitializationFlow
from mishkan.daemon import DaemonBootstrap, create_app
from mishkan.daemon.auth import TokenFile
from mishkan.persistence import SQLiteApplicationRepository


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _config(tmp_path: Path) -> MishkanConfig:
    source = tmp_path / "config.yaml"
    source.write_text(preset_text("local"), encoding="utf-8")
    loaded = ConfigLoader().load([source]).value
    return loaded.model_copy(update={"project": ProjectConfig(workspace=tmp_path)})


def _repository(root: Path) -> None:
    (root / "README.md").write_text("# Harness governed repository\n", encoding="utf-8")
    for arguments in (
        ("init", "-b", "main"),
        ("config", "user.name", "Fixture"),
        ("config", "user.email", "fixture@example.invalid"),
        ("add", "README.md"),
        ("commit", "-m", "fixture"),
    ):
        subprocess.run(["git", *arguments], cwd=root, check=True, capture_output=True)


@pytest.mark.acceptance
@pytest.mark.anyio
async def test_official_sdk_uses_authenticated_stateless_daemon_facade(tmp_path: Path) -> None:
    config = _config(tmp_path)
    paths = DaemonBootstrap().setup(config)
    token = TokenFile(paths.token_file).read()
    app = create_app(config)
    transport = httpx.ASGITransport(app=app)
    headers = {"Authorization": f"Bearer {token.token}"}
    authority = f"http://{config.daemon.host}:{config.daemon.port}"
    command = ApplicationCommand(
        command_type="system.checkpoint",
        actor_id=token.principal_id,
        target_type="system",
        target_id="local-instance",
        expected_revision=0,
        payload={"checkpoint": "mcp-http-facade"},
    )

    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=transport,
            base_url=authority,
            headers=headers,
        ) as client,
    ):
        async with (
            streamable_http_client(
                f"{authority}/mcp/",
                http_client=client,
                terminate_on_close=False,
            ) as (read_stream, write_stream, _session_id),
            ClientSession(read_stream, write_stream) as session,
        ):
            initialized = await session.initialize()
            tools = await session.list_tools()
            resources = await session.list_resources()
            health = await session.call_tool("system.health", {})
            result = await session.call_tool(
                "command.submit",
                command.model_dump(mode="json"),
            )
            snapshot = await session.read_resource("mishkan://snapshot")

        replayed = await client.post(
            "/v1/commands",
            json=command.model_dump(mode="json"),
        )

    assert initialized.serverInfo.name == "mishkan"
    assert [item.name for item in tools.tools] == [
        "system.health",
        "system.snapshot",
        "events.list",
        "run.get",
        "organization.get",
        "organization.inspect",
        "organization.branch.inspect",
        "organization.competence.get",
        "organization.evidence.list",
        "organization.promotions.list",
        "mission.list",
        "mission.get",
        "mission.inspect",
        "mission.run-reports.list",
        "mission.templates.list",
        "conversation.list",
        "conversation.get",
        "advisory.candidates.list",
        "notification.list",
        "command.submit",
    ]
    assert {str(item.uri) for item in resources.resources} == {
        "mishkan://snapshot",
        "mishkan://runs",
        "mishkan://events",
        "mishkan://organization",
        "mishkan://missions",
        "mishkan://conversations",
        "mishkan://advisory/candidates",
        "mishkan://notifications",
    }
    assert health.isError is False
    assert health.structuredContent == {
        "status": "ready",
        "schema": "knowledge_foundation_v1",
    }
    assert result.isError is False
    assert result.structuredContent is not None
    assert result.structuredContent["status"] == "accepted"
    assert snapshot.contents[0].mimeType == "application/json"
    assert replayed.status_code == 200
    assert replayed.json() == result.structuredContent


@pytest.mark.acceptance
@pytest.mark.anyio
async def test_harness_objective_is_accepted_before_crewai_and_replay_is_idempotent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _repository(tmp_path)
    config = _config(tmp_path)
    paths = DaemonBootstrap().setup(config)
    token = TokenFile(paths.token_file).read()
    command = ApplicationCommand(
        command_type="run.initialize",
        actor_id=token.principal_id,
        target_type="run",
        payload={
            "schema_version": "1.0",
            "objective": "Inspect this repository through governed harness evidence",
        },
    )
    ledger = SQLiteApplicationRepository(paths.database)
    crew_calls: list[str] = []

    def kickoff(crew: Crew, *_args: Any, **_kwargs: Any) -> SimpleNamespace:
        receipt = ledger.replay(command)
        assert receipt is not None
        assert receipt.status is CommandStatus.ACCEPTED
        assert isinstance(receipt.payload.get("run_id"), str)
        task = crew.tasks[0]
        output_model = task.output_pydantic
        output_fields = set(output_model.model_fields)
        if output_fields == {"tasks"}:
            crew_calls.append("PlanProposal")
            value = {
                "tasks": [
                    {
                        "task_id": "inspect-harness-readme",
                        "title": "Inspect the harness repository overview",
                        "purpose": "Ground the harness request in governed repository evidence.",
                        "assigned_role": "Repository_Investigator",
                        "tool_calls": [
                            {
                                "call_id": "read-harness-readme",
                                "tool_id": "repository.read_file",
                                "arguments": {"path": "README.md"},
                            }
                        ],
                        "evidence_paths": ["README.md"],
                        "depends_on": [],
                    }
                ]
            }
        elif output_fields == {"summary", "cited_paths", "findings"}:
            crew_calls.append("InitializationSynthesis")
            value = {
                "summary": "The governed harness repository overview was inspected.",
                "cited_paths": ["README.md"],
                "findings": ["The README identifies the harness governed repository."],
            }
        else:
            assert output_fields == {"verdict", "summary", "issues"}
            crew_calls.append("IndependentReview")
            value = {
                "verdict": "accepted",
                "summary": "Independent evidence review accepted the harness result.",
                "issues": [],
            }
        return SimpleNamespace(pydantic=None, raw=json.dumps(value))

    monkeypatch.setattr(Crew, "kickoff", kickoff)
    monkeypatch.setattr(
        CrewAIInitializationFlow,
        "kickoff",
        lambda flow: flow.execute_plan(flow.establish_plan()),
    )
    app = create_app(config)
    transport = httpx.ASGITransport(app=app)
    authority = f"http://{config.daemon.host}:{config.daemon.port}"
    headers = {"Authorization": f"Bearer {token.token}"}

    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=transport,
            base_url=authority,
            headers=headers,
        ) as client,
        streamable_http_client(
            f"{authority}/mcp/",
            http_client=client,
            terminate_on_close=False,
        ) as (read_stream, write_stream, _session_id),
        ClientSession(read_stream, write_stream) as session,
    ):
        await session.initialize()
        result = await session.call_tool(
            "command.submit",
            command.model_dump(mode="json"),
        )
        replay = await session.call_tool(
            "command.submit",
            command.model_dump(mode="json"),
        )
        assert result.structuredContent is not None
        run = await session.call_tool(
            "run.get",
            {"run_id": result.structuredContent["payload"]["run_id"]},
        )

    assert result.isError is False
    assert replay.structuredContent == result.structuredContent
    assert crew_calls == ["PlanProposal", "InitializationSynthesis", "IndependentReview"]
    assert run.structuredContent is not None
    assert run.structuredContent["status"] == "completed"
    events = ledger.events(after_cursor=0, limit=100)
    event_types = [event.event_type for event in events.events]
    assert event_types.index("run.request_accepted") < event_types.index("plan.accepted")


@pytest.mark.acceptance
@pytest.mark.anyio
async def test_mcp_mount_rejects_missing_daemon_bearer(tmp_path: Path) -> None:
    config = _config(tmp_path)
    DaemonBootstrap().setup(config)
    app = create_app(config)
    transport = httpx.ASGITransport(app=app)
    authority = f"http://{config.daemon.host}:{config.daemon.port}"

    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=transport, base_url=authority) as client,
    ):
        response = await client.post(
            "/mcp/",
            headers={"Accept": "application/json, text/event-stream"},
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-11-25",
                    "capabilities": {},
                    "clientInfo": {"name": "unauthenticated", "version": "1"},
                },
            },
        )

    assert response.status_code == 403
    assert response.json()["code"] == "ERR-POL-001"
