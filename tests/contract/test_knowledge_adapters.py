from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from mishkan.config.loader import ConfigLoader
from mishkan.config.models import KnowledgeSourceConfig
from mishkan.config.presets import preset_text
from mishkan.domain.errors import ErrorCode, MishkanError
from mishkan.knowledge.adapters import (
    CogneeOssAdapter,
    GraphifyMcpAdapter,
    Mem0OssAdapter,
    ProviderKnowledgeResult,
)
from mishkan.knowledge.models import KnowledgeClass, KnowledgeQuery, KnowledgeScope
from mishkan.web.network import ConnectionEvidence, HttpExchange


class FakeTransport:
    def __init__(self, responses: list[tuple[int, Any]]) -> None:
        self.responses = list(responses)
        self.requests: list[dict[str, Any]] = []

    def request(
        self,
        method: str,
        url: str,
        *,
        profile: Any,
        headers: dict[str, str] | None = None,
        content: bytes | None = None,
        timeout_seconds: float | None = None,
    ) -> HttpExchange:
        del profile
        self.requests.append(
            {
                "method": method,
                "url": url,
                "headers": headers,
                "body": json.loads(content or b"{}"),
                "timeout": timeout_seconds,
            }
        )
        status, payload = self.responses.pop(0)
        encoded = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        return HttpExchange(
            status_code=status,
            headers={"content-type": "application/json"},
            content=encoded,
            wire_bytes=len(encoded),
            decoded_bytes=len(encoded),
            connection=ConnectionEvidence(
                dns_answers=("127.0.0.1",),
                connected_address="127.0.0.1",
            ),
        )


class FakeGraphifyPort:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def call(
        self,
        connection_id: str,
        *,
        primitive: str,
        arguments: dict[str, Any],
        query: KnowledgeQuery,
    ) -> dict[str, Any]:
        self.calls.append(
            {
                "connection_id": connection_id,
                "primitive": primitive,
                "arguments": arguments,
                "query_id": str(query.query_id),
            }
        )
        return {
            "nodes": [{"id": "daemon", "file": "src/mishkan/daemon/app.py", "line": 1}],
            "edges": [{"confidence": "EXTRACTED"}],
        }


def _config(tmp_path: Path) -> Any:
    source = tmp_path / "local.yaml"
    source.write_text(preset_text("local"), encoding="utf-8")
    return ConfigLoader().load([source]).value


def _query(knowledge_class: KnowledgeClass) -> KnowledgeQuery:
    return KnowledgeQuery(
        knowledge_class=knowledge_class,
        question="Where does durable state live?",
        scope=KnowledgeScope(
            project_id="project-1",
            context_revision="context-1",
            repository_id="repository-1",
            repository_revision="revision-1",
            run_id="run-1",
            agent_identity="Backend_Engineer",
        ),
        max_results=3,
    )


def _source(config: Any, source_id: str) -> tuple[KnowledgeSourceConfig, Any]:
    source = config.knowledge.sources[source_id]
    profile = (
        config.web.network_profiles[source.network_profile]
        if source.network_profile is not None
        else None
    )
    return source, profile


def test_mem0_uses_unversioned_oss_paths_and_exact_scope_filters(tmp_path: Path) -> None:
    config = _config(tmp_path)
    source, profile = _source(config, "mem0-local")
    transport = FakeTransport(
        [
            (
                200,
                {
                    "results": [
                        {
                            "id": "memory-1",
                            "memory": "SQLite is authoritative locally.",
                            "score": 0.91,
                            "updated_at": "2026-09-25T00:00:00Z",
                        }
                    ]
                },
            )
        ]
    )

    result = Mem0OssAdapter(transport).query(
        _query(KnowledgeClass.EPISODIC),
        source_id="mem0-local",
        source=source,
        credentials=("mem0-secret",),
        network_profile=profile,
    )

    assert result.records[0].external_record_id == "memory-1"
    assert transport.requests[0]["url"] == "http://127.0.0.1:7776/search"
    assert transport.requests[0]["headers"]["x-api-key"] == "mem0-secret"
    assert transport.requests[0]["body"]["filters"] == {
        "user_id": "project-1",
        "agent_id": "Backend_Engineer",
        "run_id": "run-1",
    }
    assert transport.requests[0]["timeout"] == source.query_timeout_seconds


def test_mem0_capture_keeps_operation_and_project_metadata(tmp_path: Path) -> None:
    config = _config(tmp_path)
    source, profile = _source(config, "mem0-local")
    assert profile is not None
    transport = FakeTransport([(200, {"results": [{"id": "memory-2"}]})])

    result = Mem0OssAdapter(transport).capture(
        "A durably accepted implementation fact.",
        project_id="project-1",
        agent_identity="Backend_Engineer",
        run_id="run-1",
        operation_id="operation-1",
        source_id="mem0-local",
        source=source,
        credentials=("mem0-secret",),
        network_profile=profile,
    )

    assert result.external_record_ids == ("memory-2",)
    assert transport.requests[0]["url"] == "http://127.0.0.1:7776/memories"
    assert transport.requests[0]["body"]["metadata"] == {
        "mishkan_operation_id": "operation-1",
        "project_id": "project-1",
        "accepted_result": True,
    }
    assert transport.requests[0]["timeout"] == source.operation_timeout_seconds


def test_cognee_retrieves_only_raw_chunks_and_never_generated_answers(tmp_path: Path) -> None:
    config = _config(tmp_path)
    source, profile = _source(config, "cognee-local")
    transport = FakeTransport([(200, {"results": [{"id": "chunk-1", "text": "raw"}]})])

    result = CogneeOssAdapter(transport).query(
        _query(KnowledgeClass.SEMANTIC),
        source_id="cognee-local",
        source=source,
        credentials=("cognee-secret",),
        network_profile=profile,
    )

    assert result.records[0].content == b"raw"
    assert transport.requests[0]["url"] == "http://127.0.0.1:7777/api/v1/search"
    assert transport.requests[0]["body"]["search_type"] == "CHUNKS"
    assert transport.requests[0]["body"]["datasets"] == ["project-1"]
    assert transport.requests[0]["headers"]["authorization"] == "Bearer cognee-secret"
    assert transport.requests[0]["timeout"] == source.query_timeout_seconds

    malformed = FakeTransport([(200, {"results": [{"answer": "generated synthesis"}]})])
    with pytest.raises(MishkanError) as caught:
        CogneeOssAdapter(malformed).query(
            _query(KnowledgeClass.SEMANTIC),
            source_id="cognee-local",
            source=source,
            credentials=("cognee-secret",),
            network_profile=profile,
        )
    assert caught.value.envelope.code is ErrorCode.OUTPUT_CONTRACT


def test_graphify_uses_existing_mcp_boundary_and_preserves_edge_confidence(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    source, profile = _source(config, "graphify-local")
    port = FakeGraphifyPort()

    result = GraphifyMcpAdapter(port).query(
        _query(KnowledgeClass.STRUCTURAL),
        source_id="graphify-local",
        source=source,
        credentials=(),
        network_profile=profile,
    )

    assert result.records[0].source_locator == "graphify:graphify-local:query_graph"
    assert json.loads(result.records[0].content)["edges"][0]["confidence"] == "EXTRACTED"
    assert port.calls[0]["connection_id"] == "graphify-local"
    assert port.calls[0]["arguments"]["repository_revision"] == "revision-1"


def test_provider_timeout_and_schema_drift_are_stable_failures(tmp_path: Path) -> None:
    config = _config(tmp_path)
    source, profile = _source(config, "mem0-local")
    unavailable = FakeTransport([(503, {"error": "unavailable"})])
    with pytest.raises(MishkanError) as dependency:
        Mem0OssAdapter(unavailable).query(
            _query(KnowledgeClass.EPISODIC),
            source_id="mem0-local",
            source=source,
            credentials=("secret",),
            network_profile=profile,
        )
    assert dependency.value.envelope.code is ErrorCode.OPTIONAL_DEPENDENCY
    assert dependency.value.envelope.retryable is True

    malformed = FakeTransport([(200, b"not-json")])
    with pytest.raises(MishkanError) as drift:
        Mem0OssAdapter(malformed).query(
            _query(KnowledgeClass.EPISODIC),
            source_id="mem0-local",
            source=source,
            credentials=("secret",),
            network_profile=profile,
        )
    assert drift.value.envelope.code is ErrorCode.OUTPUT_CONTRACT


class FakeLiteralPort:
    def query(self, query: KnowledgeQuery, *, source_id: str) -> ProviderKnowledgeResult:
        del query, source_id
        return ProviderKnowledgeResult((), "literal-test-1")
