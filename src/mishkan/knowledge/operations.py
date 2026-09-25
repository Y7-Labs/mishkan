"""Explicit, durable knowledge mutations and reconciliation.

Provider effects happen only after policy, scope, input, and credential validation.
Once an effect may have been dispatched, loss of its settlement is represented as
``uncertain`` and can only be resolved by the reconciliation path below.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol, cast
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from mishkan.artifacts import ArtifactManifest, ArtifactProvenance, WorkingReference
from mishkan.config.models import (
    CredentialReference,
    KnowledgeConfig,
    KnowledgeSourceConfig,
    NetworkProfileConfig,
)
from mishkan.crewai.credentials import CredentialPoolResolver
from mishkan.domain.errors import ErrorCode, MishkanError
from mishkan.knowledge.adapters import (
    ProviderMutationResult,
    ProviderReconciliation,
    ProviderSettlement,
)
from mishkan.knowledge.inspection import KnowledgeEvidenceInspector
from mishkan.knowledge.models import (
    KnowledgeClass,
    KnowledgeCorpus,
    KnowledgeCorpusState,
    KnowledgeIngestRequest,
    KnowledgeMemoryCaptureRequest,
    KnowledgeOperation,
    KnowledgeOperationKind,
    KnowledgeOperationReconcileRequest,
    KnowledgeOperationState,
    KnowledgePromotion,
    KnowledgePromotionDecision,
    KnowledgePromotionDisposition,
    KnowledgeRefreshRequest,
)
from mishkan.persistence.migration import SchemaManager
from mishkan.persistence.sqlite import AcceptanceRow, ResultRow, create_local_engine


@dataclass(frozen=True, slots=True)
class GraphRefreshBuild:
    graph: bytes
    graph_diff: bytes
    indexed_revision: str
    provider_operation_id: str | None
    media_type: str = "application/octet-stream"


class KnowledgeMutationRepository(Protocol):
    def create_operation(self, operation: KnowledgeOperation) -> KnowledgeOperation: ...

    def transition_operation(
        self,
        operation_id: UUID,
        *,
        expected_revision: int,
        state: KnowledgeOperationState,
        provider_operation_id: str | None = None,
        result_references: tuple[str, ...] = (),
        limitation: str | None = None,
    ) -> KnowledgeOperation: ...

    def operation(self, operation_id: UUID) -> KnowledgeOperation: ...

    def corpus(self, corpus_id: UUID) -> KnowledgeCorpus: ...

    def put_corpus(self, corpus: KnowledgeCorpus, *, expected_revision: int) -> KnowledgeCorpus: ...

    def propose_promotion(self, proposal: KnowledgePromotion) -> KnowledgePromotion: ...

    def promotion(self, promotion_id: UUID) -> KnowledgePromotion: ...

    def decide_promotion(
        self,
        promotion_id: UUID,
        *,
        expected_revision: int,
        disposition: KnowledgePromotionDisposition,
        decided_by: str,
        policy_fingerprint: str,
    ) -> KnowledgePromotion: ...


class KnowledgeMutationArtifacts(Protocol):
    def put_bytes(
        self,
        content: bytes,
        *,
        media_type: str,
        provenance: ArtifactProvenance,
        complete: bool,
        sensitivity: str = "internal",
        retention: str = "run",
        resolved_secrets: tuple[str, ...] = (),
    ) -> ArtifactManifest: ...

    def manifest(self, reference: str) -> ArtifactManifest: ...

    def read_bytes(self, reference: str) -> bytes: ...

    def reference(self, scope: str, name: str) -> WorkingReference | None: ...

    def update_reference(
        self,
        scope: str,
        name: str,
        artifact_reference: str,
        *,
        expected_revision: int,
    ) -> WorkingReference: ...


class KnowledgeMutationPolicy(Protocol):
    def authorize_operation(
        self,
        *,
        kind: KnowledgeOperationKind,
        project_id: str,
        source_id: str,
        actor_identity: str,
    ) -> str: ...


class KnowledgeCredentialResolver(Protocol):
    def resolve(self, references: tuple[CredentialReference, ...]) -> tuple[str | None, ...]: ...


class AcceptedResultVerifier(Protocol):
    def require_accepted(
        self,
        *,
        result_id: str,
        run_id: str,
        task_id: str,
    ) -> None: ...


class SemanticMutationAdapter(Protocol):
    def add(
        self,
        content: str,
        *,
        dataset: str,
        operation_id: str,
        source_id: str,
        source: KnowledgeSourceConfig,
        credentials: tuple[str, ...],
        network_profile: NetworkProfileConfig,
    ) -> ProviderMutationResult: ...

    def cognify(
        self,
        *,
        dataset: str,
        operation_id: str,
        source_id: str,
        source: KnowledgeSourceConfig,
        credentials: tuple[str, ...],
        network_profile: NetworkProfileConfig,
    ) -> ProviderMutationResult: ...


class MemoryMutationAdapter(Protocol):
    def capture(
        self,
        memory: str,
        *,
        project_id: str,
        agent_identity: str,
        run_id: str,
        operation_id: str,
        source_id: str,
        source: KnowledgeSourceConfig,
        credentials: tuple[str, ...],
        network_profile: NetworkProfileConfig,
    ) -> ProviderMutationResult: ...


class ReconciliationAdapter(Protocol):
    def reconcile(
        self,
        operation: KnowledgeOperation,
        *,
        source_id: str,
        source: KnowledgeSourceConfig,
        credentials: tuple[str, ...],
        network_profile: NetworkProfileConfig | None,
    ) -> ProviderReconciliation: ...

    def cancel(
        self,
        operation: KnowledgeOperation,
        *,
        source_id: str,
        source: KnowledgeSourceConfig,
        credentials: tuple[str, ...],
        network_profile: NetworkProfileConfig | None,
    ) -> ProviderReconciliation: ...


class GraphRefreshPort(Protocol):
    """Governed Graphify refresh port; implementations must use the Effect Gateway."""

    def build(
        self,
        request: KnowledgeRefreshRequest,
        corpus: KnowledgeCorpus,
        *,
        policy_fingerprint: str,
    ) -> GraphRefreshBuild: ...

    def recover(
        self, operation: KnowledgeOperation, corpus: KnowledgeCorpus
    ) -> GraphRefreshBuild | None: ...

    def publish(
        self,
        request: KnowledgeRefreshRequest,
        corpus: KnowledgeCorpus,
        graph_reference: str,
    ) -> None: ...

    def reconcile(
        self, operation: KnowledgeOperation, corpus: KnowledgeCorpus
    ) -> ProviderReconciliation: ...

    def cancel(
        self, operation: KnowledgeOperation, corpus: KnowledgeCorpus
    ) -> ProviderReconciliation: ...


class SQLiteAcceptedResultVerifier:
    """Prove memory proposals originate from an actually accepted task result."""

    def __init__(self, database_path: Any, *, busy_timeout_ms: int = 5_000) -> None:
        SchemaManager(database_path).require_current()
        self._engine = create_local_engine(database_path, busy_timeout_ms=busy_timeout_ms)

    def require_accepted(self, *, result_id: str, run_id: str, task_id: str) -> None:
        with Session(self._engine) as session:
            result = session.get(ResultRow, result_id)
            if result is None or result.run_id != run_id or result.task_key != task_id:
                raise MishkanError(
                    ErrorCode.AUTHORITY_NOT_GRANTED,
                    "memory proposal does not reference the declared accepted result",
                )
            acceptance = session.scalar(
                select(AcceptanceRow).where(
                    AcceptanceRow.result_id == result_id,
                    AcceptanceRow.run_id == run_id,
                    AcceptanceRow.task_key == task_id,
                )
            )
            if acceptance is None:
                raise MishkanError(
                    ErrorCode.AUTHORITY_NOT_GRANTED,
                    "memory proposal references a result without durable acceptance",
                )


class KnowledgeMutationService:
    """Execute explicit knowledge writes while preserving deterministic authority."""

    def __init__(
        self,
        config: KnowledgeConfig,
        repository: KnowledgeMutationRepository,
        artifacts: KnowledgeMutationArtifacts,
        *,
        adapters: Mapping[str, object],
        network_profiles: Mapping[str, NetworkProfileConfig],
        inspector: KnowledgeEvidenceInspector,
        policy: KnowledgeMutationPolicy,
        accepted_results: AcceptedResultVerifier,
        graph_refresh: GraphRefreshPort | None = None,
        credential_resolver: KnowledgeCredentialResolver | None = None,
    ) -> None:
        self._config = config
        self._repository = repository
        self._artifacts = artifacts
        self._adapters = dict(adapters)
        self._network_profiles = dict(network_profiles)
        self._inspector = inspector
        self._policy = policy
        self._accepted_results = accepted_results
        self._graph_refresh = graph_refresh
        self._credentials = credential_resolver or CredentialPoolResolver()

    def ingest(self, request: KnowledgeIngestRequest) -> KnowledgeOperation:
        source, adapter, credentials, profile = self._prepare(
            kind=KnowledgeOperationKind.INGEST,
            project_id=request.project_id,
            source_id=request.source_id,
            actor=request.requested_by,
        )
        if source.knowledge_class is not KnowledgeClass.SEMANTIC:
            raise MishkanError(ErrorCode.TOOL_SCHEMA, "ingest requires a semantic source")
        corpus = self._require_corpus(request.corpus_id, request.project_id, request.source_id)
        body = self._artifacts.read_bytes(request.content_reference)
        if len(body) > source.max_result_bytes:
            raise MishkanError(
                ErrorCode.OUTPUT_CONTRACT,
                "knowledge ingest body exceeds source bound",
            )
        inspected = self._inspector.inspect(
            body,
            media_type=self._artifacts.manifest(request.content_reference).declared_media_type,
            resolved_secrets=credentials,
        )
        try:
            content = inspected.content.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise MishkanError(
                ErrorCode.OUTPUT_CONTRACT,
                "semantic ingest requires UTF-8 text",
            ) from exc
        operation = self._resume_existing(request.operation_id, request.fingerprint)
        if operation is None:
            request_artifact = self._request_artifact(
                operation_id=request.operation_id,
                kind=KnowledgeOperationKind.INGEST,
                project_id=request.project_id,
                source_id=request.source_id,
                value=request.model_dump(mode="json"),
                source_artifacts=(request.content_reference,),
            )
            operation = self._begin(
                KnowledgeOperation(
                    operation_id=request.operation_id,
                    kind=KnowledgeOperationKind.INGEST,
                    project_id=request.project_id,
                    source_id=request.source_id,
                    corpus_id=request.corpus_id,
                    request_fingerprint=request.fingerprint,
                    request_reference=request_artifact.reference,
                )
            )
        if operation.state is not KnowledgeOperationState.RUNNING:
            return operation
        indexing = self._set_corpus_state(corpus, KnowledgeCorpusState.INDEXING)
        result: ProviderMutationResult | None = None
        try:
            semantic = cast(SemanticMutationAdapter, adapter)
            result = semantic.add(
                content,
                dataset=corpus.external_identity,
                operation_id=str(operation.operation_id),
                source_id=request.source_id,
                source=source,
                credentials=credentials,
                network_profile=self._require_profile(profile),
            )
            cognified = semantic.cognify(
                dataset=corpus.external_identity,
                operation_id=str(operation.operation_id),
                source_id=request.source_id,
                source=source,
                credentials=credentials,
                network_profile=self._require_profile(profile),
            )
            response = self._response_artifact(
                operation,
                {"add": result.response, "cognify": cognified.response},
                source_artifacts=(request.content_reference,),
                resolved_secrets=credentials,
            )
            self._repository.put_corpus(
                indexing.model_copy(
                    update={
                        "state": KnowledgeCorpusState.READY,
                        "snapshot_reference": request.content_reference,
                        "indexed_revision": request.repository_revision,
                    }
                ),
                expected_revision=indexing.revision,
            )
            return self._repository.transition_operation(
                operation.operation_id,
                expected_revision=operation.revision,
                state=KnowledgeOperationState.SUCCEEDED,
                provider_operation_id=(
                    cognified.provider_operation_id or result.provider_operation_id
                ),
                result_references=(request.content_reference, response.reference),
            )
        except Exception as exc:
            return self._mark_uncertain(operation, exc, result)

    def capture_memory(self, request: KnowledgeMemoryCaptureRequest) -> KnowledgeOperation:
        proposal = request.proposal
        source, adapter, credentials, profile = self._prepare(
            kind=KnowledgeOperationKind.CAPTURE,
            project_id=proposal.project_id,
            source_id=request.source_id,
            actor=request.requested_by,
        )
        if source.knowledge_class is not KnowledgeClass.EPISODIC:
            raise MishkanError(ErrorCode.TOOL_SCHEMA, "memory capture requires an episodic source")
        self._accepted_results.require_accepted(
            result_id=proposal.accepted_result_id,
            run_id=proposal.run_id,
            task_id=proposal.task_id,
        )
        if len(proposal.memory) > self._config.capture_max_characters:
            raise MishkanError(
                ErrorCode.OUTPUT_CONTRACT,
                "memory proposal exceeds configured bound",
            )
        for reference in proposal.evidence_references:
            self._artifacts.manifest(reference)
        inspected = self._inspector.inspect(
            proposal.memory.encode(),
            media_type="text/plain; charset=utf-8",
            resolved_secrets=credentials,
        )
        if inspected.findings:
            raise MishkanError(
                ErrorCode.SECRET_CONTENT,
                "memory proposal contains configured sensitive or instruction-like content",
                details={"findings": list(inspected.findings)},
            )
        operation = self._resume_existing(request.operation_id, request.fingerprint)
        if operation is None:
            proposal_artifact = self._request_artifact(
                operation_id=request.operation_id,
                kind=KnowledgeOperationKind.CAPTURE,
                project_id=proposal.project_id,
                source_id=request.source_id,
                value=request.model_dump(mode="json"),
                source_artifacts=proposal.evidence_references,
            )
            operation = self._begin(
                KnowledgeOperation(
                    operation_id=request.operation_id,
                    kind=KnowledgeOperationKind.CAPTURE,
                    project_id=proposal.project_id,
                    source_id=request.source_id,
                    request_fingerprint=request.fingerprint,
                    request_reference=proposal_artifact.reference,
                )
            )
        else:
            proposal_artifact = self._artifacts.manifest(operation.request_reference)
        if operation.state is not KnowledgeOperationState.RUNNING:
            return operation
        result: ProviderMutationResult | None = None
        try:
            memory = cast(MemoryMutationAdapter, adapter)
            result = memory.capture(
                inspected.content.decode("utf-8"),
                project_id=proposal.project_id,
                agent_identity=proposal.agent_identity,
                run_id=proposal.run_id,
                operation_id=str(operation.operation_id),
                source_id=request.source_id,
                source=source,
                credentials=credentials,
                network_profile=self._require_profile(profile),
            )
            provider_artifact = self._response_artifact(
                operation,
                result.response,
                source_artifacts=(proposal_artifact.reference,),
                resolved_secrets=credentials,
            )
            return self._repository.transition_operation(
                operation.operation_id,
                expected_revision=operation.revision,
                state=KnowledgeOperationState.SUCCEEDED,
                provider_operation_id=result.provider_operation_id,
                result_references=(proposal_artifact.reference, provider_artifact.reference),
            )
        except Exception as exc:
            return self._mark_uncertain(
                operation,
                exc,
                result,
                result_references=(proposal_artifact.reference,),
            )

    def refresh(
        self,
        request: KnowledgeRefreshRequest,
        *,
        policy_fingerprint: str | None = None,
    ) -> KnowledgeOperation:
        source, adapter, credentials, profile = self._prepare(
            kind=KnowledgeOperationKind.REFRESH,
            project_id=request.project_id,
            source_id=request.source_id,
            actor=request.requested_by,
        )
        if source.knowledge_class is KnowledgeClass.STRUCTURAL and self._graph_refresh is None:
            raise MishkanError(
                ErrorCode.TOOL_UNAVAILABLE,
                "Graphify refresh port is unavailable",
            )
        if source.knowledge_class is KnowledgeClass.STRUCTURAL and policy_fingerprint is None:
            raise MishkanError(
                ErrorCode.POLICY_CONFLICT,
                "Graphify refresh lacks authoritative policy evidence",
            )
        corpus = self._require_corpus(request.corpus_id, request.project_id, request.source_id)
        if source.knowledge_class is KnowledgeClass.SEMANTIC and corpus.snapshot_reference is None:
            raise MishkanError(
                ErrorCode.REQUIRED_DEPENDENCY,
                "semantic refresh requires a previously ingested immutable snapshot",
            )
        if (
            request.repository_id is not None
            and request.repository_id not in corpus.authorized_repositories
        ):
            raise MishkanError(
                ErrorCode.AUTHORITY_NOT_GRANTED,
                "repository is not authorized for corpus",
            )
        operation = self._resume_existing(request.operation_id, request.fingerprint)
        if operation is None:
            request_artifact = self._request_artifact(
                operation_id=request.operation_id,
                kind=KnowledgeOperationKind.REFRESH,
                project_id=request.project_id,
                source_id=request.source_id,
                value=request.model_dump(mode="json"),
            )
            operation = self._begin(
                KnowledgeOperation(
                    operation_id=request.operation_id,
                    kind=KnowledgeOperationKind.REFRESH,
                    project_id=request.project_id,
                    source_id=request.source_id,
                    corpus_id=request.corpus_id,
                    request_fingerprint=request.fingerprint,
                    request_reference=request_artifact.reference,
                )
            )
        if operation.state is not KnowledgeOperationState.RUNNING:
            return operation
        indexing = self._set_corpus_state(corpus, KnowledgeCorpusState.INDEXING)
        result: ProviderMutationResult | None = None
        try:
            if source.knowledge_class is KnowledgeClass.STRUCTURAL:
                assert policy_fingerprint is not None
                return self._refresh_graph(
                    request,
                    operation,
                    indexing,
                    policy_fingerprint=policy_fingerprint,
                )
            if source.knowledge_class is not KnowledgeClass.SEMANTIC:
                raise MishkanError(
                    ErrorCode.TOOL_SCHEMA,
                    "source does not support explicit refresh",
                )
            semantic = cast(SemanticMutationAdapter, adapter)
            result = semantic.cognify(
                dataset=corpus.external_identity,
                operation_id=str(operation.operation_id),
                source_id=request.source_id,
                source=source,
                credentials=credentials,
                network_profile=self._require_profile(profile),
            )
            response = self._response_artifact(
                operation,
                result.response,
                resolved_secrets=credentials,
            )
            self._repository.put_corpus(
                indexing.model_copy(
                    update={
                        "state": KnowledgeCorpusState.READY,
                        "indexed_revision": request.repository_revision,
                    }
                ),
                expected_revision=indexing.revision,
            )
            return self._repository.transition_operation(
                operation.operation_id,
                expected_revision=operation.revision,
                state=KnowledgeOperationState.SUCCEEDED,
                provider_operation_id=result.provider_operation_id,
                result_references=(response.reference,),
            )
        except Exception as exc:
            return self._mark_uncertain(operation, exc, result)

    def cancel(self, operation_id: UUID) -> KnowledgeOperation:
        operation = self._repository.operation(operation_id)
        if operation.state is KnowledgeOperationState.QUEUED:
            return self._repository.transition_operation(
                operation_id,
                expected_revision=operation.revision,
                state=KnowledgeOperationState.CANCELLED,
            )
        if operation.state is not KnowledgeOperationState.RUNNING:
            return operation
        requested = self._repository.transition_operation(
            operation_id,
            expected_revision=operation.revision,
            state=KnowledgeOperationState.CANCEL_REQUESTED,
        )
        source, adapter, credentials, profile = self._source_runtime(operation.source_id)
        try:
            if (
                source.knowledge_class is KnowledgeClass.STRUCTURAL
                and self._graph_refresh is not None
            ):
                assert operation.corpus_id is not None
                settlement = self._graph_refresh.cancel(
                    requested,
                    self._repository.corpus(operation.corpus_id),
                )
            else:
                settlement = cast(ReconciliationAdapter, adapter).cancel(
                    requested,
                    source_id=operation.source_id,
                    source=source,
                    credentials=credentials,
                    network_profile=profile,
                )
            return self._settle(requested, settlement, resolved_secrets=credentials)
        except Exception as exc:
            return self._repository.transition_operation(
                operation_id,
                expected_revision=requested.revision,
                state=KnowledgeOperationState.UNCERTAIN,
                limitation=f"cancellation settlement unavailable: {type(exc).__name__}",
            )

    def reconcile(self, request: KnowledgeOperationReconcileRequest) -> KnowledgeOperation:
        operation = self._repository.operation(request.operation_id)
        if operation.revision != request.expected_revision:
            raise MishkanError(ErrorCode.REVISION_MISMATCH, "knowledge operation revision differs")
        if operation.state is not KnowledgeOperationState.UNCERTAIN:
            return operation
        source, adapter, credentials, profile = self._source_runtime(operation.source_id)
        if source.knowledge_class is KnowledgeClass.STRUCTURAL and self._graph_refresh is not None:
            assert operation.corpus_id is not None
            corpus = self._repository.corpus(operation.corpus_id)
            recovered = self._graph_refresh.recover(operation, corpus)
            if recovered is not None:
                refresh_request = KnowledgeRefreshRequest.model_validate_json(
                    self._artifacts.read_bytes(operation.request_reference)
                )
                return self._complete_graph_refresh(
                    refresh_request,
                    operation,
                    corpus,
                    recovered,
                )
            settlement = self._graph_refresh.reconcile(operation, corpus)
        else:
            settlement = cast(ReconciliationAdapter, adapter).reconcile(
                operation,
                source_id=operation.source_id,
                source=source,
                credentials=credentials,
                network_profile=profile,
            )
        if settlement.settlement is ProviderSettlement.UNKNOWN:
            return operation
        if settlement.settlement is ProviderSettlement.SUCCEEDED:
            self._finalize_reconciled_corpus(operation, settlement)
        return self._settle(operation, settlement, resolved_secrets=credentials)

    def propose_promotion(self, proposal: KnowledgePromotion) -> KnowledgePromotion:
        self._policy.authorize_operation(
            kind=KnowledgeOperationKind.PROMOTE,
            project_id=proposal.source_project_id,
            source_id="promotion",
            actor_identity=proposal.proposed_by,
        )
        return self._repository.propose_promotion(proposal)

    def decide_promotion(
        self,
        decision: KnowledgePromotionDecision,
        *,
        policy_fingerprint: str | None = None,
    ) -> KnowledgePromotion:
        proposal = self._repository.promotion(decision.promotion_id)
        if decision.decided_by not in self._config.promotion_approver_identities:
            raise MishkanError(ErrorCode.AUTHORITY_NOT_GRANTED, "identity cannot decide promotion")
        if decision.decided_by == proposal.proposed_by:
            raise MishkanError(ErrorCode.ROLE_CONFLICT, "promotion proposer cannot approve itself")
        self._policy.authorize_operation(
            kind=KnowledgeOperationKind.PROMOTE,
            project_id=proposal.source_project_id,
            source_id="promotion",
            actor_identity=decision.decided_by,
        )
        effective_fingerprint = policy_fingerprint or decision.policy_fingerprint
        if effective_fingerprint is None:
            raise MishkanError(
                ErrorCode.POLICY_CONFLICT,
                "knowledge promotion decision lacks authoritative policy evidence",
            )
        return self._repository.decide_promotion(
            decision.promotion_id,
            expected_revision=decision.expected_revision,
            disposition=decision.disposition,
            decided_by=decision.decided_by,
            policy_fingerprint=effective_fingerprint,
        )

    def _refresh_graph(
        self,
        request: KnowledgeRefreshRequest,
        operation: KnowledgeOperation,
        corpus: KnowledgeCorpus,
        *,
        policy_fingerprint: str,
    ) -> KnowledgeOperation:
        if self._graph_refresh is None:
            raise MishkanError(ErrorCode.TOOL_UNAVAILABLE, "Graphify refresh port is unavailable")
        build = self._graph_refresh.build(
            request,
            corpus,
            policy_fingerprint=policy_fingerprint,
        )
        return self._complete_graph_refresh(request, operation, corpus, build)

    def _complete_graph_refresh(
        self,
        request: KnowledgeRefreshRequest,
        operation: KnowledgeOperation,
        corpus: KnowledgeCorpus,
        build: GraphRefreshBuild,
    ) -> KnowledgeOperation:
        if self._graph_refresh is None:
            raise MishkanError(ErrorCode.TOOL_UNAVAILABLE, "Graphify refresh port is unavailable")
        graph = self._put_artifact(
            operation,
            build.graph,
            media_type=build.media_type,
            channel="knowledge.graph_snapshot",
        )
        diff = self._put_artifact(
            operation,
            build.graph_diff,
            media_type="application/json",
            channel="knowledge.graph_diff",
            source_artifacts=(graph.reference,),
        )
        try:
            scope = f"knowledge:{request.project_id}:{request.source_id}"
            current = self._artifacts.reference(scope, "current-graph")
            if current is None or current.artifact_reference != graph.reference:
                self._artifacts.update_reference(
                    scope,
                    "current-graph",
                    graph.reference,
                    expected_revision=current.revision if current is not None else 0,
                )
            self._graph_refresh.publish(request, corpus, graph.reference)
            if not (
                corpus.state is KnowledgeCorpusState.READY
                and corpus.snapshot_reference == graph.reference
                and corpus.indexed_revision == build.indexed_revision
            ):
                self._repository.put_corpus(
                    corpus.model_copy(
                        update={
                            "state": KnowledgeCorpusState.READY,
                            "snapshot_reference": graph.reference,
                            "indexed_revision": build.indexed_revision,
                        }
                    ),
                    expected_revision=corpus.revision,
                )
            return self._repository.transition_operation(
                operation.operation_id,
                expected_revision=operation.revision,
                state=KnowledgeOperationState.SUCCEEDED,
                provider_operation_id=build.provider_operation_id,
                result_references=(graph.reference, diff.reference),
            )
        except Exception as exc:
            return self._repository.transition_operation(
                operation.operation_id,
                expected_revision=operation.revision,
                state=KnowledgeOperationState.UNCERTAIN,
                provider_operation_id=build.provider_operation_id,
                result_references=(graph.reference, diff.reference),
                limitation=f"Graphify publish settlement unavailable: {type(exc).__name__}",
            )

    def _prepare(
        self,
        *,
        kind: KnowledgeOperationKind,
        project_id: str,
        source_id: str,
        actor: str,
    ) -> tuple[KnowledgeSourceConfig, object, tuple[str, ...], NetworkProfileConfig | None]:
        self._policy.authorize_operation(
            kind=kind,
            project_id=project_id,
            source_id=source_id,
            actor_identity=actor,
        )
        return self._source_runtime(source_id)

    def _source_runtime(
        self, source_id: str
    ) -> tuple[KnowledgeSourceConfig, object, tuple[str, ...], NetworkProfileConfig | None]:
        source = self._config.sources.get(source_id)
        if source is None or not source.enabled:
            raise MishkanError(ErrorCode.TOOL_UNAVAILABLE, "knowledge source is unavailable")
        adapter = self._adapters.get(source.adapter)
        if adapter is None:
            raise MishkanError(ErrorCode.TOOL_UNAVAILABLE, "knowledge adapter is unavailable")
        resolved = self._credentials.resolve(source.credential_refs)
        if any(value is None for value in resolved):
            raise MishkanError(
                ErrorCode.AUTHORIZATION_MISSING,
                "knowledge credential is unresolved",
            )
        credentials = tuple(cast(str, value) for value in resolved)
        profile = (
            self._network_profiles.get(source.network_profile)
            if source.network_profile is not None
            else None
        )
        if source.network_profile is not None and profile is None:
            raise MishkanError(ErrorCode.CONFIGURATION, "knowledge network profile is unavailable")
        return source, adapter, credentials, profile

    def _begin(self, candidate: KnowledgeOperation) -> KnowledgeOperation:
        durable = self._repository.create_operation(candidate)
        if durable.state is KnowledgeOperationState.QUEUED:
            return self._repository.transition_operation(
                durable.operation_id,
                expected_revision=durable.revision,
                state=KnowledgeOperationState.RUNNING,
            )
        if durable.state is KnowledgeOperationState.RUNNING:
            return self._repository.transition_operation(
                durable.operation_id,
                expected_revision=durable.revision,
                state=KnowledgeOperationState.UNCERTAIN,
                provider_operation_id=durable.provider_operation_id,
                result_references=durable.result_references,
                limitation="prior provider dispatch has no durable settlement",
            )
        return durable

    def _resume_existing(
        self, operation_id: UUID, request_fingerprint: str
    ) -> KnowledgeOperation | None:
        try:
            operation = self._repository.operation(operation_id)
        except MishkanError as exc:
            if exc.envelope.code is ErrorCode.CONTEXT:
                return None
            raise
        if operation.request_fingerprint != request_fingerprint:
            raise MishkanError(
                ErrorCode.DUPLICATE_RESULT,
                "knowledge operation identity already contains different content",
            )
        if operation.state is KnowledgeOperationState.QUEUED:
            return self._repository.transition_operation(
                operation.operation_id,
                expected_revision=operation.revision,
                state=KnowledgeOperationState.RUNNING,
            )
        if operation.state is KnowledgeOperationState.RUNNING:
            return self._repository.transition_operation(
                operation.operation_id,
                expected_revision=operation.revision,
                state=KnowledgeOperationState.UNCERTAIN,
                provider_operation_id=operation.provider_operation_id,
                result_references=operation.result_references,
                limitation="prior provider dispatch has no durable settlement",
            )
        return operation

    def _mark_uncertain(
        self,
        operation: KnowledgeOperation,
        error: Exception,
        result: ProviderMutationResult | None,
        *,
        result_references: tuple[str, ...] = (),
    ) -> KnowledgeOperation:
        return self._repository.transition_operation(
            operation.operation_id,
            expected_revision=operation.revision,
            state=KnowledgeOperationState.UNCERTAIN,
            provider_operation_id=(result.provider_operation_id if result is not None else None),
            result_references=result_references,
            limitation=f"provider settlement unavailable: {type(error).__name__}",
        )

    def _settle(
        self,
        operation: KnowledgeOperation,
        settlement: ProviderReconciliation,
        *,
        resolved_secrets: tuple[str, ...] = (),
    ) -> KnowledgeOperation:
        states = {
            ProviderSettlement.SUCCEEDED: KnowledgeOperationState.SUCCEEDED,
            ProviderSettlement.FAILED: KnowledgeOperationState.FAILED,
            ProviderSettlement.CANCELLED: KnowledgeOperationState.CANCELLED,
        }
        if settlement.settlement is ProviderSettlement.UNKNOWN:
            return operation
        response_refs = operation.result_references
        if settlement.response is not None:
            response = self._response_artifact(
                operation,
                settlement.response,
                resolved_secrets=resolved_secrets,
            )
            response_refs = (*response_refs, response.reference)
        return self._repository.transition_operation(
            operation.operation_id,
            expected_revision=operation.revision,
            state=states[settlement.settlement],
            provider_operation_id=settlement.provider_operation_id
            or operation.provider_operation_id,
            result_references=response_refs,
            limitation=settlement.limitation,
        )

    def _finalize_reconciled_corpus(
        self,
        operation: KnowledgeOperation,
        settlement: ProviderReconciliation,
    ) -> None:
        if operation.corpus_id is None:
            return
        corpus = self._repository.corpus(operation.corpus_id)
        expected_snapshot: str | None
        expected_revision: str | None
        if operation.kind is KnowledgeOperationKind.INGEST:
            ingest_request = KnowledgeIngestRequest.model_validate_json(
                self._artifacts.read_bytes(operation.request_reference)
            )
            expected_snapshot = ingest_request.content_reference
            expected_revision = ingest_request.repository_revision
        elif operation.kind is KnowledgeOperationKind.REFRESH:
            refresh_request = KnowledgeRefreshRequest.model_validate_json(
                self._artifacts.read_bytes(operation.request_reference)
            )
            expected_snapshot = corpus.snapshot_reference
            expected_revision = refresh_request.repository_revision
            if corpus.knowledge_class is KnowledgeClass.STRUCTURAL:
                response = settlement.response or {}
                graph_reference = response.get("graph_reference") or (
                    operation.result_references[0] if operation.result_references else None
                )
                indexed_revision = response.get("indexed_revision") or (
                    refresh_request.repository_revision
                )
                if not isinstance(graph_reference, str) or not isinstance(indexed_revision, str):
                    raise MishkanError(
                        ErrorCode.REQUIRED_DEPENDENCY,
                        "Graphify reconciliation lacks published graph evidence",
                    )
                self._artifacts.manifest(graph_reference)
                expected_snapshot = graph_reference
                expected_revision = indexed_revision
        else:
            return
        if (
            corpus.state is KnowledgeCorpusState.READY
            and corpus.snapshot_reference == expected_snapshot
            and corpus.indexed_revision == expected_revision
        ):
            return
        if corpus.state is not KnowledgeCorpusState.INDEXING:
            raise MishkanError(
                ErrorCode.REVISION_MISMATCH,
                "knowledge corpus cannot be finalized from its current state",
            )
        self._repository.put_corpus(
            corpus.model_copy(
                update={
                    "state": KnowledgeCorpusState.READY,
                    "snapshot_reference": expected_snapshot,
                    "indexed_revision": expected_revision,
                }
            ),
            expected_revision=corpus.revision,
        )

    def _require_corpus(self, corpus_id: UUID, project_id: str, source_id: str) -> KnowledgeCorpus:
        corpus = self._repository.corpus(corpus_id)
        if corpus.project_id != project_id or corpus.source_id != source_id:
            raise MishkanError(ErrorCode.AUTHORITY_NOT_GRANTED, "knowledge corpus scope differs")
        return corpus

    def _set_corpus_state(
        self, corpus: KnowledgeCorpus, state: KnowledgeCorpusState
    ) -> KnowledgeCorpus:
        return self._repository.put_corpus(
            corpus.model_copy(update={"state": state}),
            expected_revision=corpus.revision,
        )

    @staticmethod
    def _require_profile(profile: NetworkProfileConfig | None) -> NetworkProfileConfig:
        if profile is None:
            raise MishkanError(ErrorCode.CONFIGURATION, "knowledge operation requires network")
        return profile

    def _response_artifact(
        self,
        operation: KnowledgeOperation,
        response: dict[str, Any],
        *,
        source_artifacts: tuple[str, ...] = (),
        resolved_secrets: tuple[str, ...] = (),
    ) -> ArtifactManifest:
        return self._json_artifact(
            operation,
            response,
            channel="knowledge.provider_response",
            source_artifacts=source_artifacts,
            resolved_secrets=resolved_secrets,
        )

    def _request_artifact(
        self,
        *,
        operation_id: UUID,
        kind: KnowledgeOperationKind,
        project_id: str,
        source_id: str,
        value: object,
        source_artifacts: tuple[str, ...] = (),
    ) -> ArtifactManifest:
        try:
            content = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
        except (TypeError, ValueError) as exc:
            raise MishkanError(ErrorCode.OUTPUT_CONTRACT, "operation request is not JSON") from exc
        inspected = self._inspector.inspect(content, media_type="application/json")
        return self._artifacts.put_bytes(
            inspected.content,
            media_type="application/json",
            provenance=ArtifactProvenance(
                producer_identity="mishkand",
                run_id=f"knowledge:{project_id}",
                task_attempt_id=str(operation_id),
                call_id=str(operation_id),
                capability=f"knowledge.{kind.value}",
                channel="knowledge.operation_request",
                source_artifacts=source_artifacts,
                engine=source_id if source_artifacts else None,
            ),
            complete=True,
            retention="project",
        )

    def _json_artifact(
        self,
        operation: KnowledgeOperation,
        value: object,
        *,
        channel: str,
        source_artifacts: tuple[str, ...] = (),
        resolved_secrets: tuple[str, ...] = (),
    ) -> ArtifactManifest:
        try:
            content = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
        except (TypeError, ValueError) as exc:
            raise MishkanError(ErrorCode.OUTPUT_CONTRACT, "provider response is not JSON") from exc
        inspected = self._inspector.inspect(
            content,
            media_type="application/json",
            resolved_secrets=resolved_secrets,
        )
        return self._put_artifact(
            operation,
            inspected.content,
            media_type="application/json",
            channel=channel,
            source_artifacts=source_artifacts,
            resolved_secrets=resolved_secrets,
        )

    def _put_artifact(
        self,
        operation: KnowledgeOperation,
        content: bytes,
        *,
        media_type: str,
        channel: str,
        source_artifacts: tuple[str, ...] = (),
        resolved_secrets: tuple[str, ...] = (),
    ) -> ArtifactManifest:
        return self._artifacts.put_bytes(
            content,
            media_type=media_type,
            provenance=ArtifactProvenance(
                producer_identity="mishkand",
                run_id=f"knowledge:{operation.project_id}",
                task_attempt_id=str(operation.operation_id),
                call_id=str(operation.operation_id),
                capability=f"knowledge.{operation.kind.value}",
                channel=channel,
                source_artifacts=source_artifacts,
                engine=operation.source_id if source_artifacts else None,
            ),
            complete=True,
            retention="project",
            resolved_secrets=resolved_secrets,
        )
