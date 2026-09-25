from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from mishkan.config.loader import ConfigLoader
from mishkan.config.models import KnowledgeSourceConfig, NetworkProfileConfig
from mishkan.config.presets import preset_text
from mishkan.knowledge.adapters import CogneeOssAdapter, Mem0OssAdapter
from mishkan.knowledge.models import KnowledgeClass, KnowledgeQuery, KnowledgeScope
from mishkan.web.network import HttpxWebTransport


def _live() -> None:
    if os.environ.get("MISHKAN_RUN_KNOWLEDGE_LIVE") != "1":
        pytest.skip("live attributed-knowledge gate requires explicit opt-in")


def _credential(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        pytest.skip(f"live attributed-knowledge gate requires {name}")
    return value


def _config(tmp_path: Path):  # type: ignore[no-untyped-def]
    source = tmp_path / "local.yaml"
    source.write_text(preset_text("local"), encoding="utf-8")
    return ConfigLoader().load([source]).value


def _source(config, source_id: str) -> tuple[KnowledgeSourceConfig, NetworkProfileConfig]:  # type: ignore[no-untyped-def]
    source = config.knowledge.sources[source_id]
    assert source.network_profile is not None
    return source, config.web.network_profiles[source.network_profile]


def _p95(values: list[float]) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(0.95 * len(ordered)))]


@pytest.mark.acceptance
@pytest.mark.knowledge_live
def test_live_mem0_uses_ollama_and_meets_warm_recall_gate(tmp_path: Path) -> None:
    _live()
    config = _config(tmp_path)
    source, profile = _source(config, "mem0-local")
    credential = _credential("MISHKAN_MEM0_API_KEY")
    adapter = Mem0OssAdapter(HttpxWebTransport())
    identity = f"i07-live-{uuid4()}"
    capture = adapter.capture(
        "MISHKAN accepts knowledge only with attributable immutable evidence.",
        project_id=identity,
        agent_identity="Knowledge_Curator",
        run_id="acceptance-run",
        operation_id=str(uuid4()),
        source_id="mem0-local",
        source=source,
        credentials=(credential,),
        network_profile=profile,
    )
    assert capture.external_record_ids
    query = KnowledgeQuery(
        knowledge_class=KnowledgeClass.EPISODIC,
        question="What must knowledge retain?",
        scope=KnowledgeScope(
            project_id=identity,
            context_revision="acceptance-context",
            run_id="acceptance-run",
            agent_identity="Knowledge_Curator",
        ),
        max_results=5,
    )

    first = adapter.query(
        query,
        source_id="mem0-local",
        source=source,
        credentials=(credential,),
        network_profile=profile,
    )
    assert first.records
    durations: list[float] = []
    for _ in range(20):
        started = time.perf_counter()
        result = adapter.query(
            query,
            source_id="mem0-local",
            source=source,
            credentials=(credential,),
            network_profile=profile,
        )
        durations.append(time.perf_counter() - started)
        assert result.records
    assert _p95(durations) <= 0.3


@pytest.mark.acceptance
@pytest.mark.knowledge_live
def test_live_cognee_returns_raw_chunks_and_meets_warm_retrieval_gate(tmp_path: Path) -> None:
    _live()
    config = _config(tmp_path)
    source, profile = _source(config, "cognee-local")
    credential = _credential("MISHKAN_COGNEE_BEARER")
    adapter = CogneeOssAdapter(HttpxWebTransport())
    dataset = f"i07-live-{uuid4()}"
    operation_id = str(uuid4())
    added = adapter.add(
        "MISHKAN stores authoritative local metadata in SQLite WAL.",
        dataset=dataset,
        operation_id=operation_id,
        source_id="cognee-local",
        source=source,
        credentials=(credential,),
        network_profile=profile,
    )
    assert added.provider_operation_id
    cognified = adapter.cognify(
        dataset=dataset,
        operation_id=operation_id,
        source_id="cognee-local",
        source=source,
        credentials=(credential,),
        network_profile=profile,
    )
    assert cognified.provider_operation_id
    query = KnowledgeQuery(
        knowledge_class=KnowledgeClass.SEMANTIC,
        question="Where is authoritative local metadata stored?",
        scope=KnowledgeScope(project_id=dataset, context_revision="acceptance-context"),
        max_results=5,
    )

    first = adapter.query(
        query,
        source_id="cognee-local",
        source=source,
        credentials=(credential,),
        network_profile=profile,
    )
    assert first.records
    assert b"SQLite WAL" in first.records[0].content
    durations: list[float] = []
    for _ in range(20):
        started = time.perf_counter()
        result = adapter.query(
            query,
            source_id="cognee-local",
            source=source,
            credentials=(credential,),
            network_profile=profile,
        )
        durations.append(time.perf_counter() - started)
        assert result.records
    assert _p95(durations) <= 1.0


@pytest.mark.acceptance
@pytest.mark.knowledge_live
def test_live_graphify_mcp_meets_warm_query_gate() -> None:
    _live()
    credential = _credential("MISHKAN_GRAPHIFY_API_KEY")

    async def measure() -> tuple[str, list[float]]:
        durations: list[float] = []
        async with (
            httpx.AsyncClient(
                headers={"x-api-key": credential},
                timeout=30,
                follow_redirects=False,
            ) as client,
            streamable_http_client(
                "http://127.0.0.1:7778/mcp",
                http_client=client,
                terminate_on_close=True,
            ) as (read_stream, write_stream, _session_id),
            ClientSession(read_stream, write_stream) as session,
        ):
            initialized = await session.initialize()
            tools = {tool.name for tool in (await session.list_tools()).tools}
            assert {
                "query_graph",
                "get_node",
                "get_neighbors",
                "shortest_path",
                "graph_stats",
            }.issubset(tools)
            first = await session.call_tool("graph_stats", {})
            assert first.content
            for _ in range(20):
                started = time.perf_counter()
                result = await session.call_tool("graph_stats", {})
                durations.append(time.perf_counter() - started)
                assert result.content
            return str(initialized.protocolVersion), durations

    protocol, durations = asyncio.run(measure())
    assert protocol == "2025-11-25"
    assert _p95(durations) <= 2.0
