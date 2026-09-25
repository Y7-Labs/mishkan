from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from pydantic import ValidationError

from mishkan.artifacts import ArtifactProvenance
from mishkan.artifacts.service import DurableArtifactService
from mishkan.config.loader import ConfigLoader
from mishkan.config.presets import PRESET_NAMES, preset_text
from mishkan.domain.errors import ErrorCode, MishkanError
from mishkan.knowledge import (
    KnowledgeAttemptState,
    KnowledgeBundle,
    KnowledgeClass,
    KnowledgeCorpus,
    KnowledgeItem,
    KnowledgeOperation,
    KnowledgeOperationKind,
    KnowledgeOperationState,
    KnowledgePromotion,
    KnowledgePromotionDisposition,
    KnowledgeQuery,
    KnowledgeQueryState,
    KnowledgeScope,
    KnowledgeSourceAttempt,
    KnowledgeStaleness,
)
from mishkan.knowledge.repository import SQLiteKnowledgeRepository
from mishkan.persistence import SchemaManager


def _scope(*, project: str = "project-1") -> KnowledgeScope:
    return KnowledgeScope(
        project_id=project,
        context_revision="context-1",
        repository_id="repository-1",
        repository_revision="revision-1",
        run_id="run-1",
        task_id="task-1",
        agent_identity="Backend_Engineer",
    )


def _artifact_service(tmp_path: Path) -> DurableArtifactService:
    database = tmp_path / "mishkan.db"
    SchemaManager(database).initialize()
    return DurableArtifactService(
        database,
        tmp_path / "artifacts",
        max_artifact_bytes=1_048_576,
        max_chunk_bytes=65_536,
    )


def _artifact(service: DurableArtifactService, content: bytes) -> tuple[str, str]:
    manifest = service.put_bytes(
        content,
        media_type="application/json",
        provenance=ArtifactProvenance(
            producer_identity="mishkand",
            run_id="run-1",
            task_attempt_id="attempt-1",
            call_id="knowledge-1",
            capability="knowledge.query",
            channel="knowledge.evidence",
        ),
        complete=True,
    )
    return manifest.reference, manifest.digest


def test_every_packaged_preset_declares_explicit_knowledge_selection(tmp_path: Path) -> None:
    for name in PRESET_NAMES:
        source = tmp_path / f"{name}.yaml"
        source.write_text(preset_text(name), encoding="utf-8")
        config = ConfigLoader().load([source]).value
        assert config.schema_version == "1.6"
        assert config.knowledge is not None
        assert config.knowledge.selection_order[KnowledgeClass.LITERAL] == ("literal-native",)


def test_repository_knowledge_requires_exact_revision_scope() -> None:
    with pytest.raises(ValidationError, match="repository revision"):
        KnowledgeQuery(
            knowledge_class=KnowledgeClass.STRUCTURAL,
            question="Which modules depend on the daemon?",
            scope=KnowledgeScope(project_id="project-1", context_revision="context-1"),
        )


def test_bundle_cannot_cross_project_or_hide_degradation() -> None:
    query = KnowledgeQuery(
        knowledge_class=KnowledgeClass.LITERAL,
        question="Find the daemon entrypoint",
        scope=_scope(),
    )
    reference = "artifact:11111111-1111-4111-8111-111111111111"
    digest = f"sha256:{'a' * 64}"
    item = KnowledgeItem(
        knowledge_class=KnowledgeClass.LITERAL,
        source_id="literal-native",
        scope=_scope(project="other-project"),
        content_reference=reference,
        content_digest=digest,
        media_type="text/plain",
        source_locator="src/mishkan/daemon/main.py",
        source_revision="revision-1",
        staleness=KnowledgeStaleness.CURRENT,
        ranking_basis="exact path match",
        rank=1,
    )
    with pytest.raises(ValidationError, match="cross project"):
        KnowledgeBundle(
            query=query,
            items=(item,),
            attempts=(),
            bundle_reference=reference,
            bundle_digest=digest,
        )
    with pytest.raises(ValidationError, match="degraded knowledge"):
        KnowledgeBundle(
            query=query,
            items=(),
            attempts=(),
            degraded=True,
            bundle_reference=reference,
            bundle_digest=digest,
        )


def test_query_repository_commits_only_available_attributed_bundle(tmp_path: Path) -> None:
    artifacts = _artifact_service(tmp_path)
    repository = SQLiteKnowledgeRepository(tmp_path / "mishkan.db")
    query = KnowledgeQuery(
        knowledge_class=KnowledgeClass.LITERAL,
        question="Where is the durable daemon state?",
        scope=_scope(),
    )
    started = repository.start_query(query)
    assert repository.start_query(query) == started

    item_reference, item_digest = _artifact(artifacts, b"src/mishkan/persistence/sqlite.py")
    attempt = repository.record_attempt(
        KnowledgeSourceAttempt(
            query_id=query.query_id,
            source_id="literal-native",
            state=KnowledgeAttemptState.SUCCEEDED,
            result_count=1,
            latency_ms=2.5,
            provider_schema="mishkan-literal-1",
        )
    )
    bundle_reference, bundle_digest = _artifact(artifacts, b'{"items": 1}')
    bundle = KnowledgeBundle(
        query=query,
        items=(
            KnowledgeItem(
                knowledge_class=KnowledgeClass.LITERAL,
                source_id="literal-native",
                scope=query.scope,
                content_reference=item_reference,
                content_digest=item_digest,
                media_type="text/plain",
                source_locator="src/mishkan/persistence/sqlite.py",
                source_revision="revision-1",
                staleness=KnowledgeStaleness.CURRENT,
                ranking_basis="exact content match",
                rank=1,
            ),
        ),
        attempts=(attempt,),
        bundle_reference=bundle_reference,
        bundle_digest=bundle_digest,
    )

    completed = repository.complete_query(bundle)

    assert completed.state is KnowledgeQueryState.COMPLETED
    assert completed.bundle_reference == bundle_reference
    assert repository.attempts(query.query_id) == (attempt,)


def test_operations_are_idempotent_and_uncertain_effects_are_explicit(tmp_path: Path) -> None:
    artifacts = _artifact_service(tmp_path)
    repository = SQLiteKnowledgeRepository(tmp_path / "mishkan.db")
    request_reference, _ = _artifact(artifacts, b'{"operation":"ingest"}')
    operation = KnowledgeOperation(
        kind=KnowledgeOperationKind.INGEST,
        project_id="project-1",
        source_id="mem0-local",
        request_fingerprint=f"sha256:{hashlib.sha256(b'ingest').hexdigest()}",
        request_reference=request_reference,
    )

    queued = repository.create_operation(operation)
    assert repository.create_operation(operation) == queued
    running = repository.transition_operation(
        operation.operation_id,
        expected_revision=queued.revision,
        state=KnowledgeOperationState.RUNNING,
        provider_operation_id="provider-operation-1",
    )
    uncertain = repository.transition_operation(
        operation.operation_id,
        expected_revision=running.revision,
        state=KnowledgeOperationState.UNCERTAIN,
        provider_operation_id="provider-operation-1",
        limitation="provider write may have succeeded before connection loss",
    )

    assert uncertain.state is KnowledgeOperationState.UNCERTAIN
    with pytest.raises(MishkanError) as stale:
        repository.transition_operation(
            operation.operation_id,
            expected_revision=running.revision,
            state=KnowledgeOperationState.FAILED,
        )
    assert stale.value.envelope.code is ErrorCode.REVISION_MISMATCH


def test_corpus_is_project_isolated_and_updated_by_compare_and_swap(tmp_path: Path) -> None:
    artifacts = _artifact_service(tmp_path)
    repository = SQLiteKnowledgeRepository(tmp_path / "mishkan.db")
    snapshot, _ = _artifact(artifacts, b"graph snapshot")
    corpus = KnowledgeCorpus(
        project_id="project-1",
        source_id="graphify-local",
        knowledge_class=KnowledgeClass.STRUCTURAL,
        external_identity="project-1-graph",
        authorized_repositories=("repository-1",),
        indexed_revision="revision-1",
        snapshot_reference=snapshot,
        state="ready",
    )

    durable = repository.put_corpus(corpus, expected_revision=0)

    assert durable.revision == 1
    assert repository.list_corpora(project_id="project-1") == (durable,)
    assert repository.list_corpora(project_id="other-project") == ()
    with pytest.raises(MishkanError) as stale:
        repository.put_corpus(corpus, expected_revision=0)
    assert stale.value.envelope.code is ErrorCode.REVISION_MISMATCH


def test_promotion_decision_is_separate_and_attributable(tmp_path: Path) -> None:
    artifacts = _artifact_service(tmp_path)
    repository = SQLiteKnowledgeRepository(tmp_path / "mishkan.db")
    evidence, _ = _artifact(artifacts, b"promotion evidence")
    proposal = KnowledgePromotion(
        item_id="11111111-1111-4111-8111-111111111111",
        source_project_id="project-1",
        target_scope="organization",
        rationale="Reusable incident recovery evidence",
        evidence_references=(evidence,),
        proposed_by="Knowledge_Curator",
    )
    proposed = repository.propose_promotion(proposal)
    decided = repository.decide_promotion(
        proposal.promotion_id,
        expected_revision=proposed.revision,
        disposition=KnowledgePromotionDisposition.APPROVED,
        decided_by="CTO",
        policy_fingerprint=f"sha256:{'b' * 64}",
    )

    assert decided.disposition is KnowledgePromotionDisposition.APPROVED
    assert decided.decided_by == "CTO"
    assert decided.decided_at is not None
