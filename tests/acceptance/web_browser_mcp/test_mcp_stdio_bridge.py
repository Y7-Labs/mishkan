from __future__ import annotations

import sys
from pathlib import Path

import anyio
import pytest
import yaml
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client
from support.integrations import loopback_listener, running_daemon

from mishkan.application import ApplicationCommand
from mishkan.config.loader import ConfigLoader
from mishkan.config.models import MishkanConfig
from mishkan.config.presets import preset_text
from mishkan.daemon import DaemonBootstrap
from mishkan.daemon.auth import TokenFile


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _config(tmp_path: Path, port: int) -> tuple[MishkanConfig, Path]:
    document = yaml.safe_load(preset_text("local"))
    document["project"]["workspace"] = str(tmp_path)
    document["daemon"]["port"] = port
    source = tmp_path / "config.yaml"
    source.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")
    return ConfigLoader().load([source]).value, source


@pytest.mark.acceptance
@pytest.mark.anyio
async def test_stdio_bridge_is_a_stateless_client_of_mishkand(tmp_path: Path) -> None:
    listener = loopback_listener()
    port = int(listener.getsockname()[1])
    config, source = _config(tmp_path, port)
    paths = DaemonBootstrap().setup(config)
    token = TokenFile(paths.token_file).read()
    parameters = StdioServerParameters(
        command=sys.executable,
        args=["-m", "mishkan.mcp.stdio_bridge", "--config", str(source)],
        cwd=tmp_path,
    )
    command = ApplicationCommand(
        command_type="system.checkpoint",
        actor_id=token.principal_id,
        target_type="system",
        target_id="local-instance",
        expected_revision=0,
        payload={"checkpoint": "mcp-stdio-bridge"},
    )

    with running_daemon(config, listener), anyio.fail_after(30):
        async with (
            stdio_client(parameters) as (read_stream, write_stream),
            ClientSession(read_stream, write_stream) as session,
        ):
            initialized = await session.initialize()
            tools = await session.list_tools()
            health = await session.call_tool("system.health", {})
            result = await session.call_tool(
                "command.submit",
                command.model_dump(mode="json"),
            )

    assert initialized.serverInfo.name == "mishkan"
    assert "command.submit" in {item.name for item in tools.tools}
    assert health.structuredContent == {
        "status": "ready",
        "schema": "knowledge_foundation_v1",
    }
    assert result.isError is False
    assert result.structuredContent is not None
    assert result.structuredContent["status"] == "accepted"
