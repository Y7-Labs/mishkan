from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from mishkan.artifacts import ArtifactProvenance
from mishkan.artifacts.service import DurableArtifactService
from mishkan.config.loader import ConfigLoader
from mishkan.config.models import ProjectConfig
from mishkan.config.presets import preset_text
from mishkan.domain.errors import ErrorCode, MishkanError
from mishkan.execution import SessionState, SessionSupervisor
from mishkan.knowledge import (
    KnowledgeClass,
    KnowledgeCorpus,
    KnowledgeOperation,
    KnowledgeOperationKind,
    KnowledgeOperationState,
    KnowledgeQuery,
    KnowledgeRefreshRequest,
    KnowledgeScope,
)
from mishkan.knowledge.adapters import ProviderSettlement
from mishkan.knowledge.runtime import (
    DaemonKnowledgePolicy,
    GraphifyCliRefreshPort,
    GraphifyMcpKnowledgePort,
    RepositoryLiteralKnowledgePort,
)
from mishkan.mcp import (
    McpCallResult,
    McpCallState,
    McpEffectDisposition,
    McpPrimitiveDescriptor,
    McpPrimitiveKind,
)
from mishkan.persistence import SchemaManager
from mishkan.repository import RepositoryInspector


def _git(root: Path, *arguments: str) -> str:
    return subprocess.run(
        ["git", *arguments],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _runtime(
    tmp_path: Path,
) -> tuple[
    GraphifyCliRefreshPort,
    DurableArtifactService,
    KnowledgeCorpus,
    KnowledgeRefreshRequest,
]:
    _git(tmp_path, "init")
    _git(tmp_path, "config", "user.name", "Graph Test")
    _git(tmp_path, "config", "user.email", "graph@example.invalid")
    (tmp_path / "sample.py").write_text("def hello():\n    return 'world'\n", encoding="utf-8")
    _git(tmp_path, "add", "sample.py")
    _git(tmp_path, "commit", "-m", "fixture")
    binding = RepositoryInspector().bind(tmp_path)

    executable = tmp_path / "graphify-fixture"
    executable.write_text(
        "#!/bin/sh\n"
        "set -eu\n"
        'test "$1" = update\n'
        "repo=$2\n"
        'mkdir -p "$repo/graphify-out"\n'
        'printf \'%s\' \'{"directed":true,"nodes":[{"id":"hello"}],'
        '"links":[{"source":"sample.py","target":"hello"}]}\' '
        '> "$repo/graphify-out/graph.json"\n',
        encoding="utf-8",
    )
    executable.chmod(0o755)

    config_path = tmp_path / "config.yaml"
    config_path.write_text(preset_text("local"), encoding="utf-8")
    config = (
        ConfigLoader()
        .load([config_path])
        .value.model_copy(update={"project": ProjectConfig(workspace=tmp_path)})
    )
    assert config.knowledge is not None
    assert config.knowledge.graph_refresh is not None
    assert config.sessions is not None
    refresh_config = config.knowledge.graph_refresh.model_copy(
        update={
            "executable": executable,
            "publish_path": Path(".mishkan/knowledge/published/graph.json"),
            "max_repository_files": 100,
            "max_repository_bytes": 1_000_000,
        }
    )
    database = tmp_path / ".mishkan" / "mishkan.db"
    SchemaManager(database).initialize()
    artifacts = DurableArtifactService(
        database,
        tmp_path / ".mishkan" / "artifacts",
        max_artifact_bytes=2_000_000,
        max_chunk_bytes=65_536,
    )
    supervisor = SessionSupervisor(
        database,
        tmp_path,
        tmp_path / config.sessions.spool_root,
        config.sessions,
        artifacts,
    )
    port = GraphifyCliRefreshPort(
        tmp_path,
        refresh_config,
        supervisor,
        artifacts,
        staging_root=config.knowledge.staging_root,
        max_graph_bytes=1_000_000,
        poll_seconds=0.01,
        operation_timeout_seconds=10,
    )
    corpus = KnowledgeCorpus(
        project_id="project-a",
        source_id="graphify-local",
        knowledge_class=KnowledgeClass.STRUCTURAL,
        external_identity="project-a-graph",
        authorized_repositories=(binding.repository_id,),
    )
    request = KnowledgeRefreshRequest(
        project_id="project-a",
        source_id="graphify-local",
        corpus_id=corpus.corpus_id,
        repository_id=binding.repository_id,
        repository_revision=binding.base_revision,
        requested_by="CTO",
    )
    return port, artifacts, corpus, request


def test_graphify_refresh_runs_as_governed_job_and_publishes_atomically(tmp_path: Path) -> None:
    port, artifacts, corpus, request = _runtime(tmp_path)

    build = port.build(request, corpus, policy_fingerprint="a" * 64)

    assert json.loads(build.graph)["nodes"] == [{"id": "hello"}]
    diff = json.loads(build.graph_diff)
    assert diff["nodes"]["after"] == 1
    assert diff["edges"]["after"] == 1
    manifest = artifacts.put_bytes(
        build.graph,
        media_type=build.media_type,
        provenance=ArtifactProvenance(
            producer_identity="CTO",
            run_id="knowledge:project-a",
            task_attempt_id=str(request.operation_id),
            call_id=str(request.operation_id),
            capability="knowledge.refresh",
            channel="knowledge.graph_snapshot",
        ),
        complete=True,
    )
    port.publish(request, corpus, manifest.reference)
    assert (tmp_path / ".mishkan/knowledge/published/graph.json").read_bytes() == build.graph


def test_graphify_refresh_recovers_a_proven_completed_job_without_replay(tmp_path: Path) -> None:
    port, artifacts, corpus, request = _runtime(tmp_path)
    expected = port.build(request, corpus, policy_fingerprint="b" * 64)
    request_artifact = artifacts.put_bytes(
        request.model_dump_json().encode(),
        media_type="application/json",
        provenance=ArtifactProvenance(
            producer_identity="CTO",
            run_id="knowledge:project-a",
            task_attempt_id=str(request.operation_id),
            call_id=str(request.operation_id),
            capability="knowledge.refresh",
            channel="knowledge.operation_request",
        ),
        complete=True,
    )
    operation = KnowledgeOperation(
        operation_id=request.operation_id,
        kind=KnowledgeOperationKind.REFRESH,
        project_id=request.project_id,
        source_id=request.source_id,
        corpus_id=corpus.corpus_id,
        state=KnowledgeOperationState.UNCERTAIN,
        request_fingerprint=request.fingerprint,
        request_reference=request_artifact.reference,
        revision=3,
    )

    recovered = port.recover(operation, corpus)

    assert recovered is not None
    assert recovered.graph == expected.graph
    assert recovered.provider_operation_id == f"session:{request.operation_id}"


def test_graphify_refresh_failures_remain_bounded_and_explicit(tmp_path: Path) -> None:
    port, artifacts, corpus, request = _runtime(tmp_path)
    without_repository = request.model_copy(
        update={"repository_id": None, "repository_revision": None}
    )
    with pytest.raises(MishkanError) as incomplete:
        port.build(without_repository, corpus, policy_fingerprint="c" * 64)
    assert incomplete.value.envelope.code is ErrorCode.OUTPUT_CONTRACT

    wrong_repository = request.model_copy(update={"repository_id": "other-repository"})
    with pytest.raises(MishkanError) as mismatched:
        port.build(wrong_repository, corpus, policy_fingerprint="c" * 64)
    assert mismatched.value.envelope.code is ErrorCode.REVISION_MISMATCH

    request_artifact = artifacts.put_bytes(
        request.model_dump_json().encode(),
        media_type="application/json",
        provenance=ArtifactProvenance(
            producer_identity="CTO",
            run_id="knowledge:project-a",
            task_attempt_id=str(request.operation_id),
            call_id=str(request.operation_id),
            capability="knowledge.refresh",
            channel="knowledge.operation_request",
        ),
        complete=True,
    )
    operation = KnowledgeOperation(
        operation_id=request.operation_id,
        kind=KnowledgeOperationKind.REFRESH,
        project_id=request.project_id,
        source_id=request.source_id,
        corpus_id=corpus.corpus_id,
        state=KnowledgeOperationState.UNCERTAIN,
        request_fingerprint=request.fingerprint,
        request_reference=request_artifact.reference,
        revision=2,
    )
    assert port.recover(operation, corpus) is None
    assert port.reconcile(operation, corpus).settlement is ProviderSettlement.UNKNOWN
    assert port.cancel(operation, corpus).settlement is ProviderSettlement.UNKNOWN

    with pytest.raises(MishkanError) as invalid_json:
        port._graph_members(b"not-json")
    assert invalid_json.value.envelope.code is ErrorCode.OUTPUT_CONTRACT
    with pytest.raises(MishkanError) as invalid_root:
        port._graph_members(b"[]")
    assert invalid_root.value.envelope.code is ErrorCode.OUTPUT_CONTRACT
    with pytest.raises(MishkanError) as invalid_nodes:
        port._graph_members(b'{"nodes":{},"links":[]}')
    assert invalid_nodes.value.envelope.code is ErrorCode.OUTPUT_CONTRACT


class _McpRepository:
    def __init__(self, primitives: tuple[McpPrimitiveDescriptor, ...]) -> None:
        self.primitives = primitives

    def list_primitives(self, connection_id: str) -> tuple[McpPrimitiveDescriptor, ...]:
        assert connection_id == "graphify-local"
        return self.primitives


class _McpRunner:
    def __init__(self, state: McpCallState) -> None:
        self.state = state
        self.credentials: dict[str, str] | None = None

    def invoke(self, request: object, **kwargs: object) -> McpCallResult:
        from mishkan.mcp import McpCallRequest

        assert isinstance(request, McpCallRequest)
        credentials = kwargs["credentials"]
        assert isinstance(credentials, dict)
        self.credentials = credentials
        return McpCallResult(
            request_id=request.id,
            connection_id=request.connection_id,
            primitive_name=request.primitive_name,
            state=self.state,
            output={"nodes": [{"id": "daemon"}]} if self.state is McpCallState.COMPLETED else None,
            schema_hash=request.expected_schema_hash,
            reason="fixture",
        )


class _CredentialResolver:
    def resolve_exact(self, references: tuple[object, ...]) -> dict[str, str]:
        assert references == ("credential-reference",)
        return {"authorization": "secret"}


def _graphify_descriptor() -> McpPrimitiveDescriptor:
    kind = McpPrimitiveKind.TOOL
    name = "graph_stats"
    input_schema: dict[str, object] = {"type": "object"}
    output_schema: dict[str, object] = {"type": "object"}
    annotations: dict[str, object] = {"readOnlyHint": True}
    return McpPrimitiveDescriptor(
        connection_id="graphify-local",
        protocol_version="2025-11-25",
        kind=kind,
        name=name,
        input_schema=input_schema,
        output_schema=output_schema,
        annotations=annotations,
        effect_disposition=McpEffectDisposition.READ_ONLY,
        invocation_supported=True,
        schema_hash=McpPrimitiveDescriptor.claim_hash(
            kind, name, input_schema, output_schema, annotations
        ),
        provenance="fixture",
    )


def _structural_query() -> KnowledgeQuery:
    return KnowledgeQuery(
        knowledge_class=KnowledgeClass.STRUCTURAL,
        question="Show graph statistics",
        scope=KnowledgeScope(
            project_id="project-a",
            context_revision="context-1",
            repository_id="repository-1",
            repository_revision="revision-1",
            run_id="run-1",
            task_id="task-1",
            agent_identity="CTO",
        ),
    )


def test_graphify_mcp_port_requires_discovered_bound_primitive_and_connection() -> None:
    descriptor = _graphify_descriptor()
    runner = _McpRunner(McpCallState.COMPLETED)
    port = GraphifyMcpKnowledgePort(
        runner,  # type: ignore[arg-type]
        _McpRepository((descriptor,)),  # type: ignore[arg-type]
        {"graphify-local": ("credential-reference",)},
        poll_seconds=0.01,
        credential_resolver=_CredentialResolver(),  # type: ignore[arg-type]
    )

    result = port.call(
        "graphify-local",
        primitive="graph_stats",
        arguments={},
        query=_structural_query(),
    )
    assert result == {"nodes": [{"id": "daemon"}]}
    assert runner.credentials == {"authorization": "secret"}

    unavailable = GraphifyMcpKnowledgePort(
        runner,  # type: ignore[arg-type]
        _McpRepository(()),  # type: ignore[arg-type]
        {"graphify-local": ("credential-reference",)},
        poll_seconds=0.01,
        credential_resolver=_CredentialResolver(),  # type: ignore[arg-type]
    )
    with pytest.raises(MishkanError) as drift:
        unavailable.call(
            "graphify-local", primitive="graph_stats", arguments={}, query=_structural_query()
        )
    assert drift.value.envelope.code is ErrorCode.TOOL_DRIFT

    missing_connection = GraphifyMcpKnowledgePort(
        runner,  # type: ignore[arg-type]
        _McpRepository((descriptor,)),  # type: ignore[arg-type]
        {},
        poll_seconds=0.01,
        credential_resolver=_CredentialResolver(),  # type: ignore[arg-type]
    )
    with pytest.raises(MishkanError) as missing:
        missing_connection.call(
            "graphify-local", primitive="graph_stats", arguments={}, query=_structural_query()
        )
    assert missing.value.envelope.code is ErrorCode.MCP


def test_graphify_mcp_port_preserves_failed_settlement() -> None:
    descriptor = _graphify_descriptor()
    port = GraphifyMcpKnowledgePort(
        _McpRunner(McpCallState.FAILED),  # type: ignore[arg-type]
        _McpRepository((descriptor,)),  # type: ignore[arg-type]
        {"graphify-local": ("credential-reference",)},
        poll_seconds=0.01,
        credential_resolver=_CredentialResolver(),  # type: ignore[arg-type]
    )
    with pytest.raises(MishkanError) as failed:
        port.call(
            "graphify-local", primitive="graph_stats", arguments={}, query=_structural_query()
        )
    assert failed.value.envelope.code is ErrorCode.MCP


def _bound_workspace(tmp_path: Path) -> tuple[object, object]:
    _git(tmp_path, "init")
    _git(tmp_path, "config", "user.name", "Knowledge Test")
    _git(tmp_path, "config", "user.email", "knowledge@example.invalid")
    (tmp_path / "README.md").write_text("durable knowledge evidence\n", encoding="utf-8")
    (tmp_path / "binary.bin").write_bytes(b"\0binary")
    _git(tmp_path, "add", "README.md", "binary.bin")
    _git(tmp_path, "commit", "-m", "fixture")
    config_path = tmp_path / "config.yaml"
    config_path.write_text(preset_text("local"), encoding="utf-8")
    config = ConfigLoader().load([config_path]).value
    return config, RepositoryInspector().bind(tmp_path)


def test_daemon_knowledge_policy_enforces_source_actor_and_repository_binding(
    tmp_path: Path,
) -> None:
    config, binding = _bound_workspace(tmp_path)
    assert config.knowledge is not None
    policy = DaemonKnowledgePolicy(config.knowledge, tmp_path)
    source = config.knowledge.sources["literal-native"]
    query = KnowledgeQuery(
        knowledge_class=KnowledgeClass.LITERAL,
        question="durable knowledge",
        scope=KnowledgeScope(
            project_id="project-a",
            context_revision="context-1",
            repository_id=binding.repository_id,
            repository_revision=binding.base_revision,
        ),
    )
    policy.authorize_query(query, source_id="literal-native", source=source)
    assert (
        policy.authorize_operation(
            kind=KnowledgeOperationKind.INGEST,
            project_id="project-a",
            source_id="literal-native",
            actor_identity="CTO",
        )
        == "application-command-authority"
    )

    with pytest.raises(MishkanError) as disabled:
        policy.authorize_query(
            query,
            source_id="literal-native",
            source=source.model_copy(update={"enabled": False}),
        )
    assert disabled.value.envelope.code is ErrorCode.AUTHORITY_NOT_GRANTED

    incompatible = query.model_copy(update={"knowledge_class": KnowledgeClass.EPISODIC})
    with pytest.raises(MishkanError) as conflict:
        policy.authorize_query(incompatible, source_id="literal-native", source=source)
    assert conflict.value.envelope.code is ErrorCode.POLICY_CONFLICT

    wrong_repository = query.model_copy(
        update={"scope": query.scope.model_copy(update={"repository_id": "other"})}
    )
    with pytest.raises(MishkanError) as repository:
        policy.authorize_query(wrong_repository, source_id="literal-native", source=source)
    assert repository.value.envelope.code is ErrorCode.CONTEXT

    wrong_revision = query.model_copy(
        update={"scope": query.scope.model_copy(update={"repository_revision": "other"})}
    )
    with pytest.raises(MishkanError) as revision:
        policy.authorize_query(wrong_revision, source_id="literal-native", source=source)
    assert revision.value.envelope.code is ErrorCode.REVISION_MISMATCH

    with pytest.raises(MishkanError) as absent_actor:
        policy.authorize_operation(
            kind=KnowledgeOperationKind.INGEST,
            project_id="project-a",
            source_id="literal-native",
            actor_identity="",
        )
    assert absent_actor.value.envelope.code is ErrorCode.AUTHORITY_NOT_GRANTED

    with pytest.raises(MishkanError) as absent_source:
        policy.authorize_operation(
            kind=KnowledgeOperationKind.INGEST,
            project_id="project-a",
            source_id="absent",
            actor_identity="CTO",
        )
    assert absent_source.value.envelope.code is ErrorCode.AUTHORITY_NOT_GRANTED

    assert (
        policy.authorize_operation(
            kind=KnowledgeOperationKind.PROMOTE,
            project_id="project-a",
            source_id="policy-only",
            actor_identity="CEO",
        )
        == "application-command-authority"
    )


def test_literal_repository_port_is_revision_bound_and_skips_binary_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, binding = _bound_workspace(tmp_path)
    port = RepositoryLiteralKnowledgePort(tmp_path)
    query = KnowledgeQuery(
        knowledge_class=KnowledgeClass.LITERAL,
        question="durable knowledge missing-term",
        scope=KnowledgeScope(
            project_id="project-a",
            context_revision="context-1",
            repository_id=binding.repository_id,
            repository_revision=binding.base_revision,
        ),
    )
    result = port.query(query, source_id="literal-native")
    assert [record.external_record_id for record in result.records] == ["README.md"]

    with pytest.raises(MishkanError) as revision:
        port.query(
            query.model_copy(
                update={"scope": query.scope.model_copy(update={"repository_revision": "other"})}
            ),
            source_id="literal-native",
        )
    assert revision.value.envelope.code is ErrorCode.REVISION_MISMATCH

    with pytest.raises(MishkanError) as empty:
        port.query(query.model_copy(update={"question": "!"}), source_id="literal-native")
    assert empty.value.envelope.code is ErrorCode.OUTPUT_CONTRACT

    assert (
        port.query(
            query.model_copy(update={"question": "not-present-anywhere"}), source_id="x"
        ).records
        == ()
    )

    monkeypatch.setattr(port, "_git", lambda *_: b"../escape\0")
    with pytest.raises(MishkanError) as unsafe:
        port._tracked_paths(tmp_path, binding.base_revision)
    assert unsafe.value.envelope.code is ErrorCode.CONTEXT


def test_graphify_runtime_file_guards_are_explicit(tmp_path: Path) -> None:
    port, _, _, request = _runtime(tmp_path)
    outside = Path("../../outside/graph.json")
    with pytest.raises(MishkanError) as escaped:
        port._safe_project_path(outside)
    assert escaped.value.envelope.code is ErrorCode.AUTHORITY_NOT_GRANTED

    missing = tmp_path / "missing.json"
    with pytest.raises(MishkanError) as unavailable:
        port._read_regular_file(missing, 10)
    assert unavailable.value.envelope.code is ErrorCode.OUTPUT_CONTRACT

    oversized = tmp_path / "oversized.json"
    oversized.write_bytes(b"{}")
    with pytest.raises(MishkanError) as bounded:
        port._read_regular_file(oversized, 1)
    assert bounded.value.envelope.code is ErrorCode.OUTPUT_CONTRACT

    created = tmp_path / "new.json"
    port._write_new_file(created, b"{}")
    assert created.read_bytes() == b"{}"
    with pytest.raises(MishkanError) as exists:
        port._write_new_file(created, b"other")
    assert exists.value.envelope.code is ErrorCode.REVISION_MISMATCH

    with pytest.raises(MishkanError) as unsettled:
        port._build_from_settled(
            request,
            KnowledgeCorpus(
                project_id="project-a",
                source_id="graphify-local",
                knowledge_class=KnowledgeClass.STRUCTURAL,
                external_identity="graph",
            ),
            SimpleNamespace(result=None, state=SessionState.FAILED),
        )
    assert unsettled.value.envelope.code is ErrorCode.EXECUTION
