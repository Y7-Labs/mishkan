from __future__ import annotations

from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from sqlalchemy.orm import Session

from mishkan.artifacts import ArtifactProvenance
from mishkan.artifacts.service import DurableArtifactService
from mishkan.config.loader import ConfigLoader
from mishkan.config.models import CredentialReference
from mishkan.config.presets import preset_text
from mishkan.domain.errors import ErrorCode, MishkanError
from mishkan.knowledge import (
    KnowledgeClass,
    KnowledgeCorpus,
    KnowledgeIngestRequest,
    KnowledgeMemoryCaptureRequest,
    KnowledgeMemoryProposal,
    KnowledgeOperation,
    KnowledgeOperationReconcileRequest,
    KnowledgeOperationState,
    KnowledgePromotion,
    KnowledgePromotionDecision,
    KnowledgePromotionDisposition,
    KnowledgeRefreshRequest,
)
from mishkan.knowledge.adapters import ProviderMutationResult
from mishkan.knowledge.inspection import (
    EvidenceInspectionProfileLoader,
    KnowledgeEvidenceInspector,
)
from mishkan.knowledge.operations import (
    GraphRefreshBuild,
    KnowledgeMutationService,
    ProviderReconciliation,
    ProviderSettlement,
    SQLiteAcceptedResultVerifier,
)
from mishkan.knowledge.repository import SQLiteKnowledgeRepository
from mishkan.persistence import SchemaManager
from mishkan.persistence.sqlite import AcceptanceRow, ResultRow, RunRow, create_local_engine


class AllowPolicy:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str]] = []

    def authorize_operation(
        self,
        *,
        kind: Any,
        project_id: str,
        source_id: str,
        actor_identity: str,
    ) -> str:
        self.calls.append((kind.value, project_id, actor_identity))
        return "a" * 64


class StaticCredentials:
    def resolve(self, references: tuple[CredentialReference, ...]) -> tuple[str | None, ...]:
        return ("provider-secret",) if references else ()


class AcceptedResults:
    def __init__(self, *, accepted: bool = True) -> None:
        self.accepted = accepted

    def require_accepted(self, *, result_id: str, run_id: str, task_id: str) -> None:
        del result_id, run_id, task_id
        if not self.accepted:
            raise MishkanError(ErrorCode.AUTHORITY_NOT_GRANTED, "result is not accepted")


class SemanticAdapter:
    def __init__(self, *, lose_cognify_response: bool = False) -> None:
        self.add_calls = 0
        self.cognify_calls = 0
        self.lose_cognify_response = lose_cognify_response
        self.settlement = ProviderReconciliation(ProviderSettlement.UNKNOWN)

    def add(self, content: str, **_: Any) -> ProviderMutationResult:
        self.add_calls += 1
        return ProviderMutationResult("add-1", (), {"content": content, "state": "added"})

    def cognify(self, **_: Any) -> ProviderMutationResult:
        self.cognify_calls += 1
        if self.lose_cognify_response:
            raise TimeoutError("cognify response lost")
        return ProviderMutationResult("cognify-1", (), {"state": "cognified"})

    def reconcile(self, *_: Any, **__: Any) -> ProviderReconciliation:
        return self.settlement

    def cancel(self, *_: Any, **__: Any) -> ProviderReconciliation:
        return ProviderReconciliation(ProviderSettlement.CANCELLED)


class MemoryAdapter:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.capture_calls = 0
        self.settlement = ProviderReconciliation(ProviderSettlement.UNKNOWN)

    def capture(self, memory: str, **_: Any) -> ProviderMutationResult:
        self.capture_calls += 1
        if self.fail:
            raise TimeoutError("provider response lost")
        return ProviderMutationResult("memory-1", ("record-1",), {"memory": memory})

    def reconcile(self, *_: Any, **__: Any) -> ProviderReconciliation:
        return self.settlement

    def cancel(self, *_: Any, **__: Any) -> ProviderReconciliation:
        return ProviderReconciliation(ProviderSettlement.CANCELLED)


class GraphRefresh:
    def __init__(self, *, lose_publish_response: bool = False) -> None:
        self.published: list[str] = []
        self.lose_publish_response = lose_publish_response
        self.settlement = ProviderReconciliation(ProviderSettlement.UNKNOWN)

    def build(self, request: KnowledgeRefreshRequest, corpus: KnowledgeCorpus) -> GraphRefreshBuild:
        del corpus
        return GraphRefreshBuild(
            graph=b"graph-v2",
            graph_diff=b'{"changed":1}',
            indexed_revision=request.repository_revision or "unknown",
            provider_operation_id="graphify-update-1",
        )

    def publish(
        self,
        request: KnowledgeRefreshRequest,
        corpus: KnowledgeCorpus,
        graph_reference: str,
    ) -> None:
        del request, corpus
        self.published.append(graph_reference)
        if self.lose_publish_response:
            raise TimeoutError("publish response lost")

    def reconcile(
        self, operation: KnowledgeOperation, corpus: KnowledgeCorpus
    ) -> ProviderReconciliation:
        del operation, corpus
        return self.settlement

    def cancel(
        self, operation: KnowledgeOperation, corpus: KnowledgeCorpus
    ) -> ProviderReconciliation:
        del operation, corpus
        return ProviderReconciliation(ProviderSettlement.CANCELLED)


def _runtime(
    tmp_path: Path,
    adapters: dict[str, object],
    *,
    accepted: bool = True,
    graph: GraphRefresh | None = None,
) -> tuple[KnowledgeMutationService, SQLiteKnowledgeRepository, DurableArtifactService, Any]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    source = tmp_path / "local.yaml"
    source.write_text(preset_text("local"), encoding="utf-8")
    config = ConfigLoader().load([source]).value
    database = tmp_path / "mishkan.db"
    SchemaManager(database).initialize()
    artifacts = DurableArtifactService(
        database,
        tmp_path / "artifacts",
        max_artifact_bytes=4_194_304,
        max_chunk_bytes=65_536,
    )
    repository = SQLiteKnowledgeRepository(database)
    profile = EvidenceInspectionProfileLoader().load(config.knowledge.inspection_profile, tmp_path)
    service = KnowledgeMutationService(
        config.knowledge,
        repository,
        artifacts,
        adapters=adapters,
        network_profiles=config.web.network_profiles,
        inspector=KnowledgeEvidenceInspector(profile),
        policy=AllowPolicy(),
        accepted_results=AcceptedResults(accepted=accepted),
        graph_refresh=graph,
        credential_resolver=StaticCredentials(),
    )
    return service, repository, artifacts, config


def _artifact(artifacts: DurableArtifactService, content: bytes) -> str:
    return artifacts.put_bytes(
        content,
        media_type="text/plain; charset=utf-8",
        provenance=ArtifactProvenance(
            producer_identity="test",
            run_id="run-1",
            task_attempt_id="task-1",
            call_id="call-1",
            capability="test.fixture",
            channel="test.fixture",
        ),
        complete=True,
    ).reference


def _corpus(
    repository: SQLiteKnowledgeRepository,
    *,
    source_id: str,
    knowledge_class: KnowledgeClass,
    repositories: tuple[str, ...] = (),
) -> KnowledgeCorpus:
    return repository.put_corpus(
        KnowledgeCorpus(
            project_id="project-1",
            source_id=source_id,
            knowledge_class=knowledge_class,
            external_identity=f"project-1-{knowledge_class.value}",
            authorized_repositories=repositories,
        ),
        expected_revision=0,
    )


def test_semantic_ingest_is_explicit_idempotent_and_updates_corpus(tmp_path: Path) -> None:
    adapter = SemanticAdapter()
    service, repository, artifacts, _ = _runtime(tmp_path, {"cognee.oss": adapter})
    corpus = _corpus(
        repository,
        source_id="cognee-local",
        knowledge_class=KnowledgeClass.SEMANTIC,
    )
    content = _artifact(artifacts, b"accepted architecture evidence")
    request = KnowledgeIngestRequest(
        project_id="project-1",
        source_id="cognee-local",
        corpus_id=corpus.corpus_id,
        content_reference=content,
        repository_id="repository-1",
        repository_revision="revision-1",
        requested_by="Knowledge_Curator",
    )

    completed = service.ingest(request)
    replayed = service.ingest(request)

    assert completed.state is KnowledgeOperationState.SUCCEEDED
    assert replayed == completed
    assert adapter.add_calls == adapter.cognify_calls == 1
    current = repository.corpus(corpus.corpus_id)
    assert current.indexed_revision == "revision-1"
    assert current.snapshot_reference == content


def test_lost_memory_response_is_uncertain_and_never_blindly_replayed(tmp_path: Path) -> None:
    adapter = MemoryAdapter(fail=True)
    service, _, artifacts, _ = _runtime(tmp_path, {"mem0.oss": adapter})
    evidence = _artifact(artifacts, b"accepted result evidence")
    request = KnowledgeMemoryCaptureRequest(
        source_id="mem0-local",
        requested_by="Backend_Engineer",
        proposal=KnowledgeMemoryProposal(
            project_id="project-1",
            agent_identity="Backend_Engineer",
            run_id="run-1",
            task_id="task-1",
            accepted_result_id="11111111-1111-4111-8111-111111111111",
            memory="The daemon requires explicit schema upgrades.",
            evidence_references=(evidence,),
        ),
    )

    uncertain = service.capture_memory(request)
    replayed = service.capture_memory(request)

    assert uncertain.state is KnowledgeOperationState.UNCERTAIN
    assert replayed == uncertain
    assert adapter.capture_calls == 1
    adapter.settlement = ProviderReconciliation(
        ProviderSettlement.SUCCEEDED,
        provider_operation_id="memory-1",
        response={"id": "record-1", "reconciled": True},
    )
    settled = service.reconcile(
        KnowledgeOperationReconcileRequest(
            operation_id=request.operation_id,
            expected_revision=uncertain.revision,
            requested_by="CTO",
        )
    )
    assert settled.state is KnowledgeOperationState.SUCCEEDED
    assert adapter.capture_calls == 1


def test_reconciled_ingest_finalizes_corpus_from_immutable_request(tmp_path: Path) -> None:
    adapter = SemanticAdapter(lose_cognify_response=True)
    service, repository, artifacts, _ = _runtime(tmp_path, {"cognee.oss": adapter})
    corpus = _corpus(
        repository,
        source_id="cognee-local",
        knowledge_class=KnowledgeClass.SEMANTIC,
    )
    content = _artifact(artifacts, b"durable semantic evidence")
    request = KnowledgeIngestRequest(
        project_id="project-1",
        source_id="cognee-local",
        corpus_id=corpus.corpus_id,
        content_reference=content,
        repository_id="repository-1",
        repository_revision="revision-7",
        requested_by="Knowledge_Curator",
    )

    uncertain = service.ingest(request)
    assert uncertain.state is KnowledgeOperationState.UNCERTAIN
    assert repository.corpus(corpus.corpus_id).state.value == "indexing"
    assert artifacts.read_bytes(uncertain.request_reference)

    adapter.settlement = ProviderReconciliation(
        ProviderSettlement.SUCCEEDED,
        provider_operation_id="cognify-1",
        response={"reconciled": True},
    )
    settled = service.reconcile(
        KnowledgeOperationReconcileRequest(
            operation_id=request.operation_id,
            expected_revision=uncertain.revision,
            requested_by="CTO",
        )
    )

    assert settled.state is KnowledgeOperationState.SUCCEEDED
    current = repository.corpus(corpus.corpus_id)
    assert current.state.value == "ready"
    assert current.indexed_revision == "revision-7"
    assert current.snapshot_reference == content


def test_rejected_result_and_sensitive_memory_never_reach_mem0(tmp_path: Path) -> None:
    adapter = MemoryAdapter()
    service, _, artifacts, _ = _runtime(
        tmp_path,
        {"mem0.oss": adapter},
        accepted=False,
    )
    evidence = _artifact(artifacts, b"evidence")
    request = KnowledgeMemoryCaptureRequest(
        source_id="mem0-local",
        requested_by="Backend_Engineer",
        proposal=KnowledgeMemoryProposal(
            project_id="project-1",
            agent_identity="Backend_Engineer",
            run_id="run-1",
            task_id="task-1",
            accepted_result_id="rejected-result",
            memory="Rejected material",
            evidence_references=(evidence,),
        ),
    )
    with pytest.raises(MishkanError) as rejected:
        service.capture_memory(request)
    assert rejected.value.envelope.code is ErrorCode.AUTHORITY_NOT_GRANTED
    assert adapter.capture_calls == 0

    allowed_service, _, sensitive_artifacts, _ = _runtime(
        tmp_path / "sensitive",
        {"mem0.oss": adapter},
    )
    sensitive_evidence = _artifact(sensitive_artifacts, b"accepted evidence")
    sensitive = request.model_copy(
        update={
            "proposal": request.proposal.model_copy(
                update={
                    "accepted_result_id": "accepted-result",
                    "memory": "Contact private.person@example.com for access.",
                    "evidence_references": (sensitive_evidence,),
                }
            )
        }
    )
    with pytest.raises(MishkanError) as blocked:
        allowed_service.capture_memory(sensitive)
    assert blocked.value.envelope.code is ErrorCode.SECRET_CONTENT
    assert adapter.capture_calls == 0


def test_graph_refresh_publishes_artifact_reference_then_current_corpus(tmp_path: Path) -> None:
    graph = GraphRefresh()
    service, repository, artifacts, _ = _runtime(
        tmp_path,
        {"graphify.mcp": object()},
        graph=graph,
    )
    corpus = _corpus(
        repository,
        source_id="graphify-local",
        knowledge_class=KnowledgeClass.STRUCTURAL,
        repositories=("repository-1",),
    )
    request = KnowledgeRefreshRequest(
        project_id="project-1",
        source_id="graphify-local",
        corpus_id=corpus.corpus_id,
        repository_id="repository-1",
        repository_revision="revision-2",
        requested_by="Knowledge_Curator",
    )

    completed = service.refresh(request)

    assert completed.state is KnowledgeOperationState.SUCCEEDED
    assert len(completed.result_references) == 2
    assert graph.published == [completed.result_references[0]]
    assert artifacts.read_bytes(completed.result_references[0]) == b"graph-v2"
    current = repository.corpus(corpus.corpus_id)
    assert current.indexed_revision == "revision-2"
    assert current.snapshot_reference == completed.result_references[0]


def test_graph_publish_uncertainty_preserves_evidence_and_reconciles(tmp_path: Path) -> None:
    graph = GraphRefresh(lose_publish_response=True)
    service, repository, artifacts, _ = _runtime(
        tmp_path,
        {"graphify.mcp": object()},
        graph=graph,
    )
    corpus = _corpus(
        repository,
        source_id="graphify-local",
        knowledge_class=KnowledgeClass.STRUCTURAL,
        repositories=("repository-1",),
    )
    request = KnowledgeRefreshRequest(
        project_id="project-1",
        source_id="graphify-local",
        corpus_id=corpus.corpus_id,
        repository_id="repository-1",
        repository_revision="revision-3",
        requested_by="Knowledge_Curator",
    )

    uncertain = service.refresh(request)
    assert uncertain.state is KnowledgeOperationState.UNCERTAIN
    assert len(uncertain.result_references) == 2
    assert artifacts.read_bytes(uncertain.result_references[0]) == b"graph-v2"

    graph.settlement = ProviderReconciliation(
        ProviderSettlement.SUCCEEDED,
        provider_operation_id="graphify-update-1",
        response={"published": True},
    )
    settled = service.reconcile(
        KnowledgeOperationReconcileRequest(
            operation_id=request.operation_id,
            expected_revision=uncertain.revision,
            requested_by="CTO",
        )
    )
    assert settled.state is KnowledgeOperationState.SUCCEEDED
    current = repository.corpus(corpus.corpus_id)
    assert current.state.value == "ready"
    assert current.indexed_revision == "revision-3"


def test_promotion_requires_independent_configured_authority_and_can_be_revoked(
    tmp_path: Path,
) -> None:
    service, _, artifacts, _ = _runtime(tmp_path, {"mem0.oss": MemoryAdapter()})
    evidence = _artifact(artifacts, b"promotion evidence")
    proposal = service.propose_promotion(
        KnowledgePromotion(
            item_id=UUID("11111111-1111-4111-8111-111111111111"),
            source_project_id="project-1",
            target_scope="organization",
            rationale="Validated reusable migration guidance",
            evidence_references=(evidence,),
            proposed_by="Knowledge_Curator",
        )
    )
    with pytest.raises(MishkanError) as unauthorized:
        service.decide_promotion(
            KnowledgePromotionDecision(
                promotion_id=proposal.promotion_id,
                expected_revision=proposal.revision,
                disposition=KnowledgePromotionDisposition.APPROVED,
                decided_by="Knowledge_Curator",
                policy_fingerprint=f"sha256:{'a' * 64}",
            )
        )
    assert unauthorized.value.envelope.code is ErrorCode.AUTHORITY_NOT_GRANTED

    approved = service.decide_promotion(
        KnowledgePromotionDecision(
            promotion_id=proposal.promotion_id,
            expected_revision=proposal.revision,
            disposition=KnowledgePromotionDisposition.APPROVED,
            decided_by="CTO",
            policy_fingerprint=f"sha256:{'b' * 64}",
        )
    )
    revoked = service.decide_promotion(
        KnowledgePromotionDecision(
            promotion_id=proposal.promotion_id,
            expected_revision=approved.revision,
            disposition=KnowledgePromotionDisposition.REVOKED,
            decided_by="CEO",
            policy_fingerprint=f"sha256:{'c' * 64}",
        )
    )
    assert revoked.disposition is KnowledgePromotionDisposition.REVOKED


def test_sqlite_accepted_result_verifier_requires_exact_acceptance_scope(tmp_path: Path) -> None:
    database = tmp_path / "mishkan.db"
    SchemaManager(database).initialize()
    engine = create_local_engine(database)
    result_id = "11111111-1111-4111-8111-111111111111"
    with Session(engine) as session, session.begin():
        session.add(
            RunRow(
                id="22222222-2222-4222-8222-222222222222",
                resume_key="resume-key",
                context_kind="project",
                context_id="project-1",
                context_revision="context-1",
                repository_id="repository-1",
                repository_revision="revision-1",
                discovery_fingerprint="a" * 64,
                objective="Test accepted memory",
                outcome_id="test.memory",
                status="completed",
                revision=1,
                cancellation_requested=False,
                created_at="2026-09-25T00:00:00+00:00",
                updated_at="2026-09-25T00:00:00+00:00",
            )
        )
        session.flush()
        session.add(
            ResultRow(
                id=result_id,
                run_id="22222222-2222-4222-8222-222222222222",
                task_key="task-1",
                payload='{"accepted":true}',
                accepted_at="2026-09-25T00:00:00+00:00",
            )
        )
        session.flush()
        session.add(
            AcceptanceRow(
                id="33333333-3333-4333-8333-333333333333",
                run_id="22222222-2222-4222-8222-222222222222",
                task_key="task-1",
                result_id=result_id,
                review_payload='{"accepted":true}',
                accepted_at="2026-09-25T00:00:00+00:00",
            )
        )
    verifier = SQLiteAcceptedResultVerifier(database)
    verifier.require_accepted(
        result_id=result_id,
        run_id="22222222-2222-4222-8222-222222222222",
        task_id="task-1",
    )
    with pytest.raises(MishkanError) as wrong_task:
        verifier.require_accepted(
            result_id=result_id,
            run_id="22222222-2222-4222-8222-222222222222",
            task_id="task-2",
        )
    assert wrong_task.value.envelope.code is ErrorCode.AUTHORITY_NOT_GRANTED
