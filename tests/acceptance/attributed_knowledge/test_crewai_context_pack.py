from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from crewai import Agent, Crew, Process, Task
from pydantic import BaseModel, ConfigDict

from mishkan.artifacts import ArtifactProvenance
from mishkan.artifacts.service import DurableArtifactService
from mishkan.config.loader import ConfigLoader
from mishkan.config.presets import preset_text
from mishkan.context import ContextPackEntry, ContextPackManifest, ContextPackMaterializer
from mishkan.knowledge.adapters import (
    ProviderKnowledgeResult,
    RawKnowledgeRecord,
)
from mishkan.knowledge.inspection import (
    EvidenceInspectionProfileLoader,
    KnowledgeEvidenceInspector,
)
from mishkan.knowledge.models import KnowledgeClass, KnowledgeQuery, KnowledgeScope
from mishkan.knowledge.repository import SQLiteKnowledgeRepository
from mishkan.knowledge.service import KnowledgeService
from mishkan.persistence import SchemaManager


class _StaticAdapter:
    adapter_id = "native.literal"

    def query(self, *_args: Any, **_kwargs: Any) -> ProviderKnowledgeResult:
        return ProviderKnowledgeResult(
            (
                RawKnowledgeRecord(
                    external_record_id="README.md",
                    content=b"SQLite/WAL is authoritative for local MISHKAN metadata.",
                    media_type="text/plain; charset=utf-8",
                    source_locator="git:README.md",
                    source_revision="revision-1",
                    ranking_basis="exact tracked blob",
                    score=1.0,
                ),
            ),
            "literal-git-1",
        )


class _AllowPolicy:
    def authorize_query(self, *_args: Any, **_kwargs: Any) -> None:
        return None


class _NoCredentials:
    def resolve(self, _references: Any) -> tuple[()]:
        return ()


class _Synthesis(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary: str


def _entry(manifest: Any, *, path: str, order: int) -> ContextPackEntry:
    return ContextPackEntry(
        logical_path=path,
        layer="task",
        order=order,
        artifact_reference=manifest.reference,
        digest=manifest.digest,
        size_bytes=manifest.size_bytes,
        media_type=manifest.declared_media_type,
        sensitivity=manifest.sensitivity,
    )


def test_real_crewai_task_receives_attributed_knowledge_through_context_pack(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    config_path = tmp_path / "local.yaml"
    config_path.write_text(preset_text("local"), encoding="utf-8")
    config = ConfigLoader().load([config_path]).value
    assert config.knowledge is not None
    assert config.web is not None
    database = tmp_path / "mishkan.db"
    SchemaManager(database).initialize()
    artifacts = DurableArtifactService(
        database,
        tmp_path / "artifacts",
        max_artifact_bytes=4_194_304,
        max_chunk_bytes=65_536,
    )
    inspector = KnowledgeEvidenceInspector(
        EvidenceInspectionProfileLoader().load(
            config.knowledge.inspection_profile,
            tmp_path,
        )
    )
    service = KnowledgeService(
        config.knowledge,
        SQLiteKnowledgeRepository(database),
        artifacts,
        adapters={"native.literal": _StaticAdapter()},
        network_profiles=config.web.network_profiles,
        inspector=inspector,
        policy_gate=_AllowPolicy(),
        credential_resolver=_NoCredentials(),
    )
    query = KnowledgeQuery(
        knowledge_class=KnowledgeClass.LITERAL,
        question="Where is authoritative local metadata stored?",
        scope=KnowledgeScope(
            project_id="project-1",
            context_revision="context-1",
            repository_id="repository-1",
            repository_revision="revision-1",
            run_id="run-1",
            task_id="task-1",
            agent_identity="Backend_Engineer",
        ),
    )
    bundle = service.query(query)
    provenance = ArtifactProvenance(
        producer_identity="MISHKAN",
        run_id="run-1",
        task_attempt_id="task-1-attempt-1",
        call_id="context-pack",
        capability="context.materialize",
        channel="context.contract",
    )
    output = artifacts.put_bytes(
        b'{"type":"object","required":["summary"]}',
        media_type="application/json",
        provenance=provenance,
        complete=True,
    )
    verification = artifacts.put_bytes(
        b"Require a cited attributed knowledge artifact.",
        media_type="text/plain",
        provenance=provenance,
        complete=True,
    )
    knowledge_entries = service.context_entries(bundle, start_order=0)
    manifest = ContextPackManifest(
        organization_revision="mishkan-initial@1.0.0",
        agent_identity="Backend_Engineer",
        run_id="run-1",
        task_id="task-1",
        mission_id="mission-1",
        mission_brief_revision="brief:1",
        plan_revision="plan:1",
        output_contract_reference=output.reference,
        output_contract_digest=output.digest,
        verification_contract_reference=verification.reference,
        verification_contract_digest=verification.digest,
        max_entries=4,
        max_entry_bytes=1_000_000,
        max_total_bytes=2_000_000,
        entries=(
            *knowledge_entries,
            _entry(output, path="task/output.schema.json", order=2),
            _entry(verification, path="task/verification.txt", order=3),
        ),
    )
    materializer = ContextPackMaterializer(artifacts, root=tmp_path / "context-packs")
    materialization = materializer.materialize(manifest, Path("mission-1/task-1"))
    projection = materializer.model_projection(manifest, max_bytes=2_000_000)
    assert materializer.verify(manifest, materialization, Path("mission-1/task-1"))

    def kickoff(crew: Crew, *_args: Any, **_kwargs: Any) -> SimpleNamespace:
        description = crew.tasks[0].description
        assert bundle.bundle_reference in description
        assert bundle.bundle_digest in description
        assert "untrusted_evidence" in description
        assert "SQLite/WAL is authoritative" in description
        return SimpleNamespace(
            pydantic=None,
            raw=json.dumps({"summary": "SQLite/WAL is authoritative locally."}),
        )

    monkeypatch.setattr(Crew, "kickoff", kickoff)
    agent = Agent(
        role="Backend_Engineer",
        goal="Synthesize accepted evidence",
        backstory="Persistent MISHKAN professional identity.",
        llm="ollama/qwen2.5:1.5b",
        tools=[],
        allow_delegation=False,
        allow_code_execution=False,
        max_iter=1,
        max_retry_limit=0,
        verbose=False,
    )
    task = Task(
        description=("Synthesize only the bounded attributed Context Pack below.\n" + projection),
        expected_output="One evidence-grounded summary.",
        agent=agent,
        tools=[],
        output_pydantic=_Synthesis,
    )
    result = Crew(
        agents=[agent],
        tasks=[task],
        process=Process.sequential,
        tracing=False,
        verbose=False,
    ).kickoff()

    raw = getattr(result, "raw", None)
    assert isinstance(raw, str)
    assert _Synthesis.model_validate_json(raw).summary.startswith("SQLite/WAL")
