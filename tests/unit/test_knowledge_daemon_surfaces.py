from __future__ import annotations

import subprocess
from pathlib import Path

import httpx
import pytest

from mishkan.application import ApplicationCommand
from mishkan.config.loader import ConfigLoader
from mishkan.config.models import MishkanConfig, ProjectConfig
from mishkan.config.presets import preset_text
from mishkan.daemon import DaemonBootstrap, create_app
from mishkan.daemon.auth import TokenFile
from mishkan.knowledge import KnowledgeClass, KnowledgeQuery, KnowledgeScope
from mishkan.repository import RepositoryInspector


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _repository(path: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "Fixture"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "fixture@example.invalid"], cwd=path, check=True)
    (path / "README.md").write_text(
        "# Attributed fixture\n\nThe control plane preserves source provenance.\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "add", "README.md"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-qm", "fixture"], cwd=path, check=True)


def _config(path: Path) -> MishkanConfig:
    source = path / "config.yaml"
    source.write_text(preset_text("local"), encoding="utf-8")
    loaded = ConfigLoader().load([source]).value
    return loaded.model_copy(update={"project": ProjectConfig(workspace=path)})


def _query(path: Path, knowledge_class: KnowledgeClass) -> KnowledgeQuery:
    binding = RepositoryInspector().bind(path)
    return KnowledgeQuery(
        knowledge_class=knowledge_class,
        question="control plane provenance",
        scope=KnowledgeScope(
            project_id="fixture-project",
            context_revision="context-1",
            repository_id=binding.repository_id,
            repository_revision=binding.base_revision,
            run_id="run-1",
            task_id="task-1",
            agent_identity="local-operator",
        ),
        max_results=5,
        max_bytes=65_536,
    )


@pytest.mark.anyio
async def test_literal_query_is_idempotent_attributed_and_projected(tmp_path: Path) -> None:
    _repository(tmp_path)
    config = _config(tmp_path)
    paths = DaemonBootstrap().setup(config)
    token = TokenFile(paths.token_file).read()
    headers = {"Authorization": f"Bearer {token.token}"}
    query = _query(tmp_path, KnowledgeClass.LITERAL)
    command = ApplicationCommand(
        command_type="knowledge.query",
        actor_id=token.principal_id,
        target_type="knowledge_query",
        target_id=str(query.query_id),
        payload={"query": query.model_dump(mode="json")},
    )

    transport = httpx.ASGITransport(app=create_app(config))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        first = await client.post(
            "/v1/commands", headers=headers, json=command.model_dump(mode="json")
        )
        replay = await client.post(
            "/v1/commands", headers=headers, json=command.model_dump(mode="json")
        )
        record = await client.get(f"/v1/knowledge/queries/{query.query_id}", headers=headers)
        sources = await client.get("/v1/knowledge/sources", headers=headers)
        snapshot = await client.get("/v1/snapshot", headers=headers)

    assert first.status_code == 200
    assert replay.json() == first.json()
    assert first.json()["status"] == "accepted"
    bundle = first.json()["payload"]
    assert bundle["degraded"] is False
    assert bundle["items"][0]["source_locator"].endswith(":README.md")
    assert bundle["items"][0]["content_reference"].startswith("artifact:")
    assert bundle["bundle_reference"].startswith("artifact:")
    assert record.json()["query"]["state"] == "completed"
    assert record.json()["attempts"][0]["source_id"] == "literal-native"
    assert sources.json()["sources"][0]["health"] == "configured_unobserved"
    assert "knowledge" in snapshot.json()["projections"]


@pytest.mark.anyio
async def test_optional_semantic_failure_degrades_to_literal_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("MISHKAN_COGNEE_BEARER", raising=False)
    _repository(tmp_path)
    config = _config(tmp_path)
    paths = DaemonBootstrap().setup(config)
    token = TokenFile(paths.token_file).read()
    query = _query(tmp_path, KnowledgeClass.SEMANTIC)
    command = ApplicationCommand(
        command_type="knowledge.query",
        actor_id=token.principal_id,
        target_type="knowledge_query",
        target_id=str(query.query_id),
        payload={"query": query.model_dump(mode="json")},
    )
    transport = httpx.ASGITransport(app=create_app(config))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/v1/commands",
            headers={"Authorization": f"Bearer {token.token}"},
            json=command.model_dump(mode="json"),
        )

    assert response.status_code == 200
    assert response.json()["status"] == "accepted"
    bundle = response.json()["payload"]
    assert bundle["degraded"] is True
    assert "cognee-local" in bundle["unavailable_sources"]
    assert any(item["source_id"] == "literal-native" for item in bundle["items"])
