from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from mishkan.application import ApplicationCommand, CommandResult, CommandStatus
from mishkan.config.models import MishkanConfig
from mishkan.config.presets import preset_text
from mishkan.domain.errors import ErrorCode, MishkanError
from mishkan.mcp import McpFacadeRouter
from mishkan.persistence import SchemaManager, SQLiteApplicationRepository


def _router(tmp_path: Path) -> tuple[McpFacadeRouter, list[ApplicationCommand]]:
    database = tmp_path / "mishkan.db"
    SchemaManager(database).initialize()
    repository = SQLiteApplicationRepository(database)
    config = MishkanConfig.model_validate(yaml.safe_load(preset_text("local")))
    assert config.mcp is not None
    submitted: list[ApplicationCommand] = []

    async def execute(command: ApplicationCommand, principal: str) -> CommandResult:
        assert command.actor_id == principal
        submitted.append(command)
        return CommandResult(
            command_id=command.command_id,
            status=CommandStatus.ACCEPTED,
            target_type=command.target_type,
            target_id=command.target_id,
            revision=1,
        )

    return (
        McpFacadeRouter(
            config.mcp,
            repository,
            execute,
            schema_revision="capability_contract_v1",
            event_page_limit=100,
        ),
        submitted,
    )


@pytest.mark.anyio
async def test_facade_exposes_only_implemented_allowlisted_operations(tmp_path: Path) -> None:
    router, _submitted = _router(tmp_path)

    assert router.operations == (
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
        "knowledge.query",
        "knowledge.sources.list",
        "knowledge.operations.list",
        "command.submit",
    )
    assert await router.invoke("system.health", {}, principal_id="harness") == {
        "status": "ready",
        "schema": "capability_contract_v1",
    }
    with pytest.raises(MishkanError) as hidden:
        await router.invoke("events.stream", {}, principal_id="harness")
    assert hidden.value.envelope.code is ErrorCode.AUTHORITY_NOT_GRANTED


@pytest.mark.anyio
async def test_facade_command_uses_authenticated_daemon_executor(tmp_path: Path) -> None:
    router, submitted = _router(tmp_path)
    command = ApplicationCommand(
        command_type="system.checkpoint",
        actor_id="harness",
        target_type="system",
        payload={"note": "external review"},
    )

    result = await router.invoke(
        "command.submit",
        command.model_dump(mode="json"),
        principal_id="harness",
    )

    assert result["status"] == "accepted"
    assert submitted == [command]
    with pytest.raises(MishkanError) as impersonation:
        await router.invoke(
            "command.submit",
            command.model_copy(update={"actor_id": "another"}).model_dump(mode="json"),
            principal_id="harness",
        )
    assert impersonation.value.envelope.code is ErrorCode.AUTHORITY_NOT_GRANTED


@pytest.mark.anyio
async def test_facade_resources_are_bounded_daemon_queries(tmp_path: Path) -> None:
    router, _submitted = _router(tmp_path)

    snapshot = await router.read_resource("mishkan://snapshot", principal_id="harness")
    events = await router.read_resource("mishkan://events", principal_id="harness")

    assert snapshot["cursor"] == 0
    assert events["events"] == []
