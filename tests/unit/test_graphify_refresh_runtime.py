from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from mishkan.artifacts import ArtifactProvenance
from mishkan.artifacts.service import DurableArtifactService
from mishkan.config.loader import ConfigLoader
from mishkan.config.models import ProjectConfig
from mishkan.config.presets import preset_text
from mishkan.domain.errors import ErrorCode, MishkanError
from mishkan.execution import SessionSupervisor
from mishkan.knowledge import (
    KnowledgeClass,
    KnowledgeCorpus,
    KnowledgeOperation,
    KnowledgeOperationKind,
    KnowledgeOperationState,
    KnowledgeRefreshRequest,
)
from mishkan.knowledge.adapters import ProviderSettlement
from mishkan.knowledge.runtime import GraphifyCliRefreshPort
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
