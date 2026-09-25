from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from mishkan.application import ApplicationCommand
from mishkan.config.loader import ConfigLoader
from mishkan.config.models import MishkanConfig, ProjectConfig
from mishkan.config.presets import preset_text
from mishkan.daemon import DaemonBootstrap, create_app
from mishkan.daemon.auth import TokenFile
from mishkan.domain.errors import ErrorCode, MishkanError
from mishkan.mcp import DaemonMcpFacade
from mishkan.missions import MissionOrigin, MissionOriginKind, MissionRecord
from mishkan.organization import load_canonical_organization


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _config(tmp_path: Path) -> MishkanConfig:
    source = tmp_path / "config.yaml"
    source.write_text(preset_text("local"), encoding="utf-8")
    loaded = ConfigLoader().load([source]).value
    return loaded.model_copy(update={"project": ProjectConfig(workspace=tmp_path)})


@pytest.mark.anyio
async def test_remote_facade_forwards_queries_commands_and_resources(tmp_path: Path) -> None:
    config = _config(tmp_path)
    paths = DaemonBootstrap().setup(config)
    token_file = TokenFile(paths.token_file)
    token = token_file.read()
    assert config.mcp is not None
    assert config.daemon is not None
    facade = DaemonMcpFacade(
        config.mcp,
        config.daemon,
        token_file,
        transport=httpx.ASGITransport(app=create_app(config)),
    )
    command = ApplicationCommand(
        command_type="system.checkpoint",
        actor_id=token.principal_id,
        target_type="system",
        target_id="local-instance",
        expected_revision=0,
        payload={"checkpoint": "remote-facade"},
    )
    roster = load_canonical_organization()
    mission = MissionRecord(
        origin=MissionOrigin(
            kind=MissionOriginKind.CEO,
            actor_id=token.principal_id,
            objective="Inspect a mission through the governed MCP facade",
        ),
        organization_id=roster.organization_id,
        organization_version=roster.organization_version,
    )
    mission_command = ApplicationCommand(
        command_type="mission.create",
        actor_id=token.principal_id,
        target_type="mission",
        target_id=str(mission.mission_id),
        expected_revision=0,
        payload={"record": mission.model_dump(mode="json")},
    )

    health = await facade.invoke("system.health", {}, principal_id=token.principal_id)
    organization = await facade.invoke("organization.get", {}, principal_id=token.principal_id)
    missions = await facade.read_resource("mishkan://missions", principal_id=token.principal_id)
    conversations = await facade.read_resource(
        "mishkan://conversations", principal_id=token.principal_id
    )
    advisory = await facade.read_resource(
        "mishkan://advisory/candidates", principal_id=token.principal_id
    )
    result = await facade.invoke(
        "command.submit",
        command.model_dump(mode="json"),
        principal_id=token.principal_id,
    )
    mission_result = await facade.invoke(
        "command.submit",
        mission_command.model_dump(mode="json"),
        principal_id=token.principal_id,
    )
    mission_projection = await facade.invoke(
        "mission.get",
        {"mission_id": str(mission.mission_id)},
        principal_id=token.principal_id,
    )
    mission_inspection = await facade.invoke(
        "mission.inspect",
        {"mission_id": str(mission.mission_id), "limit": 10},
        principal_id=token.principal_id,
    )
    organization_inspection = await facade.invoke(
        "organization.inspect",
        {"limit": 10},
        principal_id=token.principal_id,
    )
    branch_inspection = await facade.invoke(
        "organization.branch.inspect",
        {"branch_id": roster.branches[0].branch_id, "limit": 10},
        principal_id=token.principal_id,
    )
    run_reports = await facade.invoke(
        "mission.run-reports.list",
        {"mission_id": str(mission.mission_id), "limit": 10},
        principal_id=token.principal_id,
    )
    notifications = await facade.read_resource(
        "mishkan://notifications", principal_id=token.principal_id
    )
    events = await facade.read_resource("mishkan://events", principal_id=token.principal_id)

    assert health == {"status": "ready", "schema": "knowledge_foundation_v1"}
    assert len(organization["identities"]) == 59
    assert missions == {"missions": []}
    assert conversations == {"conversations": []}
    assert advisory["activation_authorized"] is False
    assert notifications["notifications"]
    assert notifications["notifications"][0]["event_type"] == "system.checkpoint_recorded"
    assert result["status"] == "accepted"
    assert mission_result["status"] == "accepted"
    assert mission_projection["mission_id"] == str(mission.mission_id)
    assert mission_inspection["mission"] == mission_projection
    assert mission_inspection["brief"] is None
    assert organization_inspection["organization"]["organization_id"] == roster.organization_id
    assert branch_inspection["branch"]["branch_id"] == roster.branches[0].branch_id
    assert run_reports == {"reports": []}
    assert len(events["events"]) == 3


@pytest.mark.anyio
async def test_remote_facade_rejects_hidden_operations_and_identity_mismatch(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    paths = DaemonBootstrap().setup(config)
    token_file = TokenFile(paths.token_file)
    token = token_file.read()
    assert config.mcp is not None
    assert config.daemon is not None
    facade = DaemonMcpFacade(
        config.mcp,
        config.daemon,
        token_file,
        transport=httpx.ASGITransport(app=create_app(config)),
    )

    with pytest.raises(MishkanError) as hidden:
        await facade.invoke("events.stream", {}, principal_id=token.principal_id)
    with pytest.raises(MishkanError) as identity:
        await facade.invoke("system.health", {}, principal_id="another-client")

    assert hidden.value.envelope.code is ErrorCode.AUTHORITY_NOT_GRANTED
    assert identity.value.envelope.code is ErrorCode.AUTHORITY_NOT_GRANTED
