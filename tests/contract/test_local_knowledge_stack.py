from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).parents[2]


def _compose() -> dict[str, Any]:
    loaded = yaml.safe_load((ROOT / "docker-compose.local.yaml").read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


def test_local_knowledge_stack_is_loopback_authenticated_and_pinned() -> None:
    document = _compose()
    services = document["services"]

    assert services["mem0"]["ports"] == ["127.0.0.1:7776:8000"]
    assert services["cognee"]["ports"] == ["127.0.0.1:7777:8000"]
    assert services["graphify"]["ports"] == ["127.0.0.1:7778:7778"]
    assert services["mishkand"]["ports"] == ["127.0.0.1:8888:18888"]

    assert services["mem0"]["environment"]["AUTH_DISABLED"] == "false"
    assert services["cognee"]["environment"]["REQUIRE_AUTHENTICATION"] == "true"
    assert "--api-key" in services["graphify"]["command"]
    assert "./.mishkan/knowledge/published:/graphs" in services["graphify"]["volumes"]
    assert services["mem0"]["environment"]["MEM0_TELEMETRY"] == "false"
    assert (
        services["mem0"]["environment"]["HISTORY_DB_PATH"] == "/opt/mem0/server/history/history.db"
    )
    assert "mem0-history:/opt/mem0/server/history" in services["mem0"]["volumes"]
    assert services["cognee"]["environment"]["TELEMETRY_DISABLED"] == "1"

    for service in services.values():
        image = service.get("image")
        if image is not None:
            assert ":latest" not in image
            assert ":main" not in image
            assert "@sha256:" in image


def test_provider_builds_pin_contracts_and_prove_local_ollama_wiring() -> None:
    mem0 = (ROOT / "deploy/knowledge/mem0.Dockerfile").read_text(encoding="utf-8")
    patch = (ROOT / "deploy/knowledge/mem0-ollama.patch").read_text(encoding="utf-8")
    cognee = (ROOT / "deploy/knowledge/cognee.Dockerfile").read_text(encoding="utf-8")
    graphify = (ROOT / "deploy/knowledge/graphify.Dockerfile").read_text(encoding="utf-8")
    mishkan = (ROOT / "deploy/knowledge/mishkan.Dockerfile").read_text(encoding="utf-8")

    assert "MEM0_COMMIT=47a69e1e72dc562b6fdd49a9ef892229afc7508a" in mem0
    assert "mem0ai==2.2.0" in patch
    assert "ollama==0.6.2" in patch
    assert '"ollama"' in patch
    assert '"cognee[api,ollama]==1.6.1"' in cognee
    assert '"graphifyy[mcp]==0.9.67"' in graphify
    assert '"graphifyy==0.9.67"' in mishkan


def test_cognee_state_is_bound_to_explicit_persistent_roots() -> None:
    service = _compose()["services"]["cognee"]

    assert service["environment"]["SYSTEM_ROOT_DIRECTORY"] == "/var/lib/cognee/system"
    assert service["environment"]["DATA_ROOT_DIRECTORY"] == "/var/lib/cognee/data"
    assert "cognee-system:/var/lib/cognee/system" in service["volumes"]
    assert "cognee-data:/var/lib/cognee/data" in service["volumes"]
