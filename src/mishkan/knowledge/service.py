"""Deterministic attributed retrieval and visible degraded operation."""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Mapping
from typing import Protocol
from uuid import UUID

from pydantic import ValidationError

from mishkan.artifacts import ArtifactManifest, ArtifactProvenance
from mishkan.config.models import (
    CredentialReference,
    KnowledgeConfig,
    KnowledgeSourceConfig,
    NetworkProfileConfig,
)
from mishkan.context.models import ContextPackEntry
from mishkan.crewai.credentials import CredentialPoolResolver
from mishkan.domain.errors import ErrorCode, MishkanError
from mishkan.domain.identity import new_id
from mishkan.domain.time import utc_now
from mishkan.knowledge.adapters import KnowledgeQueryAdapter, RawKnowledgeRecord
from mishkan.knowledge.inspection import KnowledgeEvidenceInspector
from mishkan.knowledge.models import (
    KnowledgeAttemptState,
    KnowledgeBundle,
    KnowledgeClass,
    KnowledgeItem,
    KnowledgeQuery,
    KnowledgeQueryRecord,
    KnowledgeQueryState,
    KnowledgeSourceAttempt,
    KnowledgeStaleness,
)


class KnowledgeRepositoryPort(Protocol):
    def start_query(self, query: KnowledgeQuery) -> KnowledgeQueryRecord: ...

    def record_attempt(self, attempt: KnowledgeSourceAttempt) -> KnowledgeSourceAttempt: ...

    def complete_query(self, bundle: KnowledgeBundle) -> KnowledgeQueryRecord: ...

    def fail_query(
        self,
        query_id: UUID,
        *,
        state: KnowledgeQueryState,
        limitation_codes: tuple[str, ...],
    ) -> KnowledgeQueryRecord: ...


class KnowledgeArtifactPort(Protocol):
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


class KnowledgeCredentialResolver(Protocol):
    def resolve(self, references: tuple[CredentialReference, ...]) -> tuple[str | None, ...]: ...


class KnowledgePolicyGate(Protocol):
    def authorize_query(
        self,
        query: KnowledgeQuery,
        *,
        source_id: str,
        source: KnowledgeSourceConfig,
    ) -> None: ...


class KnowledgeService:
    """Resolve configured sources while MISHKAN owns lineage and acceptance."""

    def __init__(
        self,
        config: KnowledgeConfig,
        repository: KnowledgeRepositoryPort,
        artifacts: KnowledgeArtifactPort,
        *,
        adapters: Mapping[str, KnowledgeQueryAdapter],
        network_profiles: Mapping[str, NetworkProfileConfig],
        inspector: KnowledgeEvidenceInspector,
        policy_gate: KnowledgePolicyGate,
        credential_resolver: KnowledgeCredentialResolver | None = None,
    ) -> None:
        self._config = config
        self._repository = repository
        self._artifacts = artifacts
        self._adapters = dict(adapters)
        self._network_profiles = dict(network_profiles)
        self._inspector = inspector
        self._policy = policy_gate
        self._credentials = credential_resolver or CredentialPoolResolver()

    def query(self, query: KnowledgeQuery) -> KnowledgeBundle:
        source_ids = self._source_order(query)
        started = self._repository.start_query(query)
        if started.state in {KnowledgeQueryState.COMPLETED, KnowledgeQueryState.DEGRADED}:
            return self._load_bundle(started)
        if started.state is not KnowledgeQueryState.RUNNING:
            raise MishkanError(
                ErrorCode.REVISION_MISMATCH,
                "knowledge query is already terminal",
                details={"state": started.state.value},
            )

        deadline = time.monotonic() + query.deadline_seconds
        attempts: list[KnowledgeSourceAttempt] = []
        items: list[KnowledgeItem] = []
        unavailable: list[str] = []
        limitations: list[str] = []
        total_bytes = 0
        literal_fallback_used = False

        for source_id in source_ids:
            if len(items) >= query.max_results or total_bytes >= query.max_bytes:
                break
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                limitations.append("deadline_exhausted")
                break
            source = self._config.sources[source_id]
            adapter = self._adapters.get(source.adapter)
            started_at = time.monotonic()
            if adapter is None:
                attempt = self._attempt(
                    query,
                    source_id,
                    KnowledgeAttemptState.UNAVAILABLE,
                    started_at,
                    error_code=ErrorCode.OPTIONAL_DEPENDENCY.value,
                    limitation=f"adapter unavailable: {source.adapter}",
                )
                attempts.append(self._repository.record_attempt(attempt))
                unavailable.append(source_id)
                continue
            try:
                self._policy.authorize_query(query, source_id=source_id, source=source)
                credentials = tuple(
                    value
                    for value in self._credentials.resolve(source.credential_refs)
                    if value is not None
                )
                effective_source = source.model_copy(
                    update={"query_timeout_seconds": min(source.query_timeout_seconds, remaining)}
                )
                profile = (
                    self._network_profiles.get(source.network_profile)
                    if source.network_profile is not None
                    else None
                )
                result = adapter.query(
                    query,
                    source_id=source_id,
                    source=effective_source,
                    credentials=credentials,
                    network_profile=profile,
                )
            except MishkanError as exc:
                if exc.envelope.code in {
                    ErrorCode.AUTHORITY_NOT_GRANTED,
                    ErrorCode.POLICY_CONFLICT,
                    ErrorCode.CONTEXT,
                    ErrorCode.SECRET_CONTENT,
                }:
                    self._repository.fail_query(
                        query.query_id,
                        state=KnowledgeQueryState.FAILED,
                        limitation_codes=(exc.envelope.code.value,),
                    )
                    raise
                state = (
                    KnowledgeAttemptState.UNAVAILABLE
                    if exc.envelope.code
                    in {
                        ErrorCode.OPTIONAL_DEPENDENCY,
                        ErrorCode.REQUIRED_DEPENDENCY,
                        ErrorCode.TOOL_UNAVAILABLE,
                        ErrorCode.AUTHORIZATION_MISSING,
                        ErrorCode.CONFIGURATION,
                    }
                    else KnowledgeAttemptState.FAILED
                )
                attempt = self._attempt(
                    query,
                    source_id,
                    state,
                    started_at,
                    error_code=exc.envelope.code.value,
                    limitation=exc.envelope.message,
                )
                attempts.append(self._repository.record_attempt(attempt))
                if state is KnowledgeAttemptState.UNAVAILABLE:
                    unavailable.append(source_id)
                else:
                    limitations.append(f"source_failed:{source_id}:{exc.envelope.code.value}")
                continue

            accepted = 0
            record_error: MishkanError | None = None
            for record in result.records:
                if len(items) >= query.max_results:
                    break
                try:
                    item, content_size, finding_limitations = self._publish_item(
                        query,
                        source_id=source_id,
                        source=source,
                        record=record,
                        rank=len(items) + 1,
                        resolved_secrets=credentials,
                        remaining_bytes=query.max_bytes - total_bytes,
                    )
                except MishkanError as exc:
                    if exc.envelope.code is ErrorCode.SECRET_CONTENT:
                        self._repository.fail_query(
                            query.query_id,
                            state=KnowledgeQueryState.FAILED,
                            limitation_codes=(exc.envelope.code.value,),
                        )
                        raise
                    if (
                        exc.envelope.code is ErrorCode.OUTPUT_CONTRACT
                        and exc.envelope.details.get("bound") == "bytes"
                    ):
                        limitations.append("result_bound_reached")
                        break
                    record_error = exc
                    break
                items.append(item)
                total_bytes += content_size
                accepted += 1
                limitations.extend(finding_limitations)
            state = (
                KnowledgeAttemptState.FAILED
                if record_error is not None
                else KnowledgeAttemptState.SUCCEEDED
                if accepted
                else KnowledgeAttemptState.EMPTY
            )
            attempt = self._attempt(
                query,
                source_id,
                state,
                started_at,
                result_count=accepted,
                error_code=(record_error.envelope.code.value if record_error is not None else None),
                provider_schema=result.provider_schema,
                limitation=(
                    record_error.envelope.message if record_error is not None else result.limitation
                ),
            )
            attempts.append(self._repository.record_attempt(attempt))
            if record_error is not None:
                limitations.append(f"source_failed:{source_id}:{record_error.envelope.code.value}")
            if result.limitation:
                limitations.append(f"source:{source_id}:{result.limitation}")

        if not any(self._acceptable(item) for item in items) and self._literal_fallback_allowed(
            query, source_ids
        ):
            fallback_sources = [
                source_id
                for source_id in self._config.selection_order[KnowledgeClass.LITERAL]
                if source_id not in source_ids and self._config.sources[source_id].enabled
            ]
            literal_fallback_used = bool(fallback_sources)
            fallback_query = query.model_copy(update={"preferred_sources": ()})
            source_ids.extend(fallback_sources)
            if fallback_sources:
                try:
                    fallback = self._query_fallback(
                        fallback_query,
                        source_ids=fallback_sources,
                        deadline=deadline,
                        start_rank=len(items) + 1,
                        byte_budget=query.max_bytes - total_bytes,
                    )
                except MishkanError as exc:
                    if exc.envelope.code in {
                        ErrorCode.AUTHORITY_NOT_GRANTED,
                        ErrorCode.POLICY_CONFLICT,
                        ErrorCode.CONTEXT,
                        ErrorCode.SECRET_CONTENT,
                    }:
                        self._repository.fail_query(
                            query.query_id,
                            state=KnowledgeQueryState.FAILED,
                            limitation_codes=(exc.envelope.code.value,),
                        )
                    raise
                attempts.extend(fallback[0])
                items.extend(fallback[1])
                total_bytes += fallback[2]
                unavailable.extend(fallback[3])
                limitations.extend(fallback[4])
        if literal_fallback_used:
            limitations.append("literal_fallback")

        limitations.extend(self._staleness_limitations(items))
        limitations = list(dict.fromkeys(limitations))
        unavailable = list(dict.fromkeys(unavailable))
        acceptable_items = tuple(item for item in items if self._acceptable(item))
        if query.required and not acceptable_items:
            self._repository.fail_query(
                query.query_id,
                state=KnowledgeQueryState.FAILED,
                limitation_codes=(ErrorCode.REQUIRED_DEPENDENCY.value,),
            )
            raise MishkanError(
                ErrorCode.REQUIRED_DEPENDENCY,
                "required knowledge query produced no current acceptable evidence",
                details={"query_id": str(query.query_id), "sources": source_ids},
                retryable=bool(unavailable),
            )
        if unavailable:
            limitations.append(ErrorCode.OPTIONAL_DEPENDENCY.value)

        bundle = self._publish_bundle(
            query,
            items=tuple(items),
            attempts=tuple(attempts),
            unavailable=tuple(unavailable),
            limitations=tuple(dict.fromkeys(limitations)),
        )
        self._repository.complete_query(bundle)
        return bundle

    def context_entry(self, bundle: KnowledgeBundle, *, order: int) -> ContextPackEntry:
        return self.context_entries(bundle, start_order=order)[0]

    def context_entries(
        self,
        bundle: KnowledgeBundle,
        *,
        start_order: int,
    ) -> tuple[ContextPackEntry, ...]:
        """Bind bundle lineage and every bounded evidence body into a Context Pack."""
        manifest = self._artifacts.manifest(bundle.bundle_reference)
        base = f"knowledge/{bundle.query.knowledge_class.value}/{bundle.query.query_id}"
        entries = [
            ContextPackEntry(
                logical_path=f"{base}/bundle.json",
                layer="knowledge",
                order=start_order,
                artifact_reference=bundle.bundle_reference,
                digest=bundle.bundle_digest,
                size_bytes=manifest.size_bytes,
                media_type=manifest.declared_media_type,
                sensitivity=manifest.sensitivity,
                required=bundle.query.required,
                source_revision=bundle.query.scope.repository_revision,
            )
        ]
        for offset, item in enumerate(bundle.items, start=1):
            item_manifest = self._artifacts.manifest(item.content_reference)
            entries.append(
                ContextPackEntry(
                    logical_path=f"{base}/items/{item.rank:04d}-{item.item_id}.evidence",
                    layer="knowledge",
                    order=start_order + offset,
                    artifact_reference=item.content_reference,
                    digest=item.content_digest,
                    size_bytes=item_manifest.size_bytes,
                    media_type=item_manifest.declared_media_type,
                    sensitivity=item_manifest.sensitivity,
                    required=bundle.query.required,
                    source_revision=item.source_revision,
                )
            )
        return tuple(entries)

    def _query_fallback(
        self,
        query: KnowledgeQuery,
        *,
        source_ids: list[str] | tuple[str, ...],
        deadline: float,
        start_rank: int,
        byte_budget: int,
    ) -> tuple[
        list[KnowledgeSourceAttempt],
        list[KnowledgeItem],
        int,
        list[str],
        list[str],
    ]:
        attempts: list[KnowledgeSourceAttempt] = []
        items: list[KnowledgeItem] = []
        unavailable: list[str] = []
        limitations: list[str] = []
        used_bytes = 0
        for source_id in source_ids:
            remaining = deadline - time.monotonic()
            if remaining <= 0 or len(items) >= query.max_results or used_bytes >= byte_budget:
                limitations.append("deadline_or_bound_exhausted")
                break
            source = self._config.sources[source_id]
            adapter = self._adapters.get(source.adapter)
            started_at = time.monotonic()
            if adapter is None:
                attempt = self._attempt(
                    query,
                    source_id,
                    KnowledgeAttemptState.UNAVAILABLE,
                    started_at,
                    error_code=ErrorCode.OPTIONAL_DEPENDENCY.value,
                    limitation=f"adapter unavailable: {source.adapter}",
                )
                attempts.append(self._repository.record_attempt(attempt))
                unavailable.append(source_id)
                continue
            try:
                self._policy.authorize_query(query, source_id=source_id, source=source)
                credentials = tuple(
                    value
                    for value in self._credentials.resolve(source.credential_refs)
                    if value is not None
                )
                effective = source.model_copy(
                    update={"query_timeout_seconds": min(source.query_timeout_seconds, remaining)}
                )
                profile = (
                    self._network_profiles.get(source.network_profile)
                    if source.network_profile is not None
                    else None
                )
                result = adapter.query(
                    query,
                    source_id=source_id,
                    source=effective,
                    credentials=credentials,
                    network_profile=profile,
                )
            except MishkanError as exc:
                if exc.envelope.code in {
                    ErrorCode.AUTHORITY_NOT_GRANTED,
                    ErrorCode.POLICY_CONFLICT,
                    ErrorCode.CONTEXT,
                    ErrorCode.SECRET_CONTENT,
                }:
                    raise
                state = (
                    KnowledgeAttemptState.UNAVAILABLE
                    if exc.envelope.code
                    in {
                        ErrorCode.OPTIONAL_DEPENDENCY,
                        ErrorCode.REQUIRED_DEPENDENCY,
                        ErrorCode.TOOL_UNAVAILABLE,
                    }
                    else KnowledgeAttemptState.FAILED
                )
                attempt = self._attempt(
                    query,
                    source_id,
                    state,
                    started_at,
                    error_code=exc.envelope.code.value,
                    limitation=exc.envelope.message,
                )
                attempts.append(self._repository.record_attempt(attempt))
                if state is KnowledgeAttemptState.UNAVAILABLE:
                    unavailable.append(source_id)
                else:
                    limitations.append(f"source_failed:{source_id}:{exc.envelope.code.value}")
                continue
            accepted = 0
            record_error: MishkanError | None = None
            for record in result.records:
                if len(items) >= query.max_results:
                    break
                try:
                    item, size, item_limitations = self._publish_item(
                        query,
                        source_id=source_id,
                        source=source,
                        record=record,
                        rank=start_rank + len(items),
                        resolved_secrets=credentials,
                        remaining_bytes=byte_budget - used_bytes,
                    )
                except MishkanError as exc:
                    if (
                        exc.envelope.code is ErrorCode.OUTPUT_CONTRACT
                        and exc.envelope.details.get("bound") == "bytes"
                    ):
                        limitations.append("result_bound_reached")
                        break
                    record_error = exc
                    break
                items.append(item)
                used_bytes += size
                accepted += 1
                limitations.extend(item_limitations)
            attempt = self._attempt(
                query,
                source_id,
                KnowledgeAttemptState.FAILED
                if record_error is not None
                else KnowledgeAttemptState.SUCCEEDED
                if accepted
                else KnowledgeAttemptState.EMPTY,
                started_at,
                result_count=accepted,
                error_code=(record_error.envelope.code.value if record_error is not None else None),
                provider_schema=result.provider_schema,
                limitation=(
                    record_error.envelope.message if record_error is not None else result.limitation
                ),
            )
            attempts.append(self._repository.record_attempt(attempt))
            if record_error is not None:
                limitations.append(f"source_failed:{source_id}:{record_error.envelope.code.value}")
        return attempts, items, used_bytes, unavailable, limitations

    def _publish_item(
        self,
        query: KnowledgeQuery,
        *,
        source_id: str,
        source: KnowledgeSourceConfig,
        record: RawKnowledgeRecord,
        rank: int,
        resolved_secrets: tuple[str, ...],
        remaining_bytes: int,
    ) -> tuple[KnowledgeItem, int, list[str]]:
        inspected = self._inspector.inspect(
            record.content,
            media_type=record.media_type,
            resolved_secrets=resolved_secrets,
        )
        if len(inspected.content) > remaining_bytes:
            raise MishkanError(
                ErrorCode.OUTPUT_CONTRACT,
                "knowledge result exceeds the remaining query byte bound",
                details={"bound": "bytes", "remaining": remaining_bytes},
            )
        manifest = self._artifacts.put_bytes(
            inspected.content,
            media_type=record.media_type,
            provenance=self._provenance(query, source_id, source, "knowledge.item"),
            complete=True,
            sensitivity="project-private",
            retention="knowledge-query",
            resolved_secrets=resolved_secrets,
        )
        staleness, reason = self._staleness(query, record.source_revision)
        try:
            item = KnowledgeItem(
                knowledge_class=query.knowledge_class,
                source_id=source_id,
                external_record_id=record.external_record_id,
                scope=query.scope,
                content_reference=manifest.reference,
                content_digest=manifest.digest,
                media_type=record.media_type,
                source_locator=record.source_locator,
                source_revision=record.source_revision,
                staleness=staleness,
                staleness_reason=reason,
                ranking_basis=record.ranking_basis,
                rank=rank,
                score=record.score,
                confidence=record.confidence,
                inspection_findings=inspected.findings,
                content_transformed=inspected.redacted,
            )
        except ValidationError as exc:
            raise MishkanError(
                ErrorCode.OUTPUT_CONTRACT,
                "knowledge provider record violates the normalized evidence contract",
                details={"source_id": source_id, "violations": len(exc.errors())},
            ) from exc
        limitations = [f"content_finding:{finding}" for finding in inspected.findings]
        return item, len(inspected.content), limitations

    def _publish_bundle(
        self,
        query: KnowledgeQuery,
        *,
        items: tuple[KnowledgeItem, ...],
        attempts: tuple[KnowledgeSourceAttempt, ...],
        unavailable: tuple[str, ...],
        limitations: tuple[str, ...],
    ) -> KnowledgeBundle:
        bundle_id = new_id()
        created_at = utc_now()
        payload = {
            "schema_version": "1.0",
            "bundle_id": str(bundle_id),
            "query": query.model_dump(mode="json"),
            "items": [item.model_dump(mode="json") for item in items],
            "attempts": [attempt.model_dump(mode="json") for attempt in attempts],
            "unavailable_sources": list(unavailable),
            "limitations": list(limitations),
            "degraded": bool(unavailable or limitations),
            "created_at": created_at.isoformat(),
        }
        content = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        manifest = self._artifacts.put_bytes(
            content,
            media_type="application/vnd.mishkan.knowledge-bundle+json",
            provenance=ArtifactProvenance(
                producer_identity="mishkand",
                run_id=query.scope.run_id or f"knowledge:{query.query_id}",
                task_attempt_id=query.scope.task_id or f"knowledge:{query.query_id}",
                call_id=str(query.query_id),
                capability="knowledge.query",
                channel="knowledge.bundle",
                source_artifacts=tuple(item.content_reference for item in items),
                engine="mishkan.knowledge.bundle",
                engine_version="1.0",
            ),
            complete=True,
            sensitivity="project-private",
            retention="knowledge-query",
        )
        return KnowledgeBundle(
            bundle_id=bundle_id,
            query=query,
            items=items,
            attempts=attempts,
            unavailable_sources=unavailable,
            limitations=limitations,
            degraded=bool(unavailable or limitations),
            bundle_reference=manifest.reference,
            bundle_digest=manifest.digest,
            created_at=created_at,
        )

    def _load_bundle(self, record: KnowledgeQueryRecord) -> KnowledgeBundle:
        assert record.bundle_reference is not None
        manifest = self._artifacts.manifest(record.bundle_reference)
        try:
            payload = json.loads(self._artifacts.read_bytes(record.bundle_reference))
            return KnowledgeBundle.model_validate(
                {
                    **payload,
                    "bundle_reference": record.bundle_reference,
                    "bundle_digest": manifest.digest,
                }
            )
        except (json.JSONDecodeError, ValidationError, TypeError) as exc:
            raise MishkanError(
                ErrorCode.ARTIFACT,
                "durable knowledge bundle cannot be reconstructed",
                details={"reference": record.bundle_reference},
            ) from exc

    def _source_order(self, query: KnowledgeQuery) -> list[str]:
        configured = list(self._config.selection_order.get(query.knowledge_class, ()))
        if query.preferred_sources:
            invalid = [
                source_id
                for source_id in query.preferred_sources
                if source_id not in configured
                or not self._config.sources[source_id].enabled
                or self._config.sources[source_id].knowledge_class is not query.knowledge_class
            ]
            if invalid:
                raise MishkanError(
                    ErrorCode.OUTPUT_CONTRACT,
                    "preferred knowledge source is disabled or incompatible",
                    details={"sources": invalid},
                )
            return list(query.preferred_sources)
        return [source_id for source_id in configured if self._config.sources[source_id].enabled]

    def _literal_fallback_allowed(self, query: KnowledgeQuery, selected: list[str]) -> bool:
        return (
            self._config.literal_fallback
            and query.knowledge_class in {KnowledgeClass.SEMANTIC, KnowledgeClass.STRUCTURAL}
            and query.scope.repository_id is not None
            and not any(
                self._config.sources[source_id].knowledge_class is KnowledgeClass.LITERAL
                for source_id in selected
            )
        )

    @staticmethod
    def _acceptable(item: KnowledgeItem) -> bool:
        return item.staleness not in {KnowledgeStaleness.STALE, KnowledgeStaleness.UNKNOWN}

    @staticmethod
    def _staleness(
        query: KnowledgeQuery, source_revision: str | None
    ) -> tuple[KnowledgeStaleness, str | None]:
        expected = query.scope.repository_revision
        if expected is None or query.knowledge_class is KnowledgeClass.EPISODIC:
            return KnowledgeStaleness.NOT_APPLICABLE, None
        if source_revision is None:
            return KnowledgeStaleness.UNKNOWN, "source omitted its indexed repository revision"
        if source_revision == expected:
            return KnowledgeStaleness.CURRENT, None
        return (
            KnowledgeStaleness.STALE,
            f"source revision {source_revision} differs from requested revision {expected}",
        )

    @staticmethod
    def _staleness_limitations(items: list[KnowledgeItem]) -> list[str]:
        return list(
            dict.fromkeys(
                f"staleness:{item.source_id}:{item.staleness.value}"
                for item in items
                if item.staleness in {KnowledgeStaleness.STALE, KnowledgeStaleness.UNKNOWN}
            )
        )

    @staticmethod
    def _attempt(
        query: KnowledgeQuery,
        source_id: str,
        state: KnowledgeAttemptState,
        started_at: float,
        *,
        result_count: int = 0,
        error_code: str | None = None,
        limitation: str | None = None,
        provider_schema: str | None = None,
    ) -> KnowledgeSourceAttempt:
        return KnowledgeSourceAttempt(
            query_id=query.query_id,
            source_id=source_id,
            state=state,
            result_count=result_count,
            latency_ms=max(0.0, (time.monotonic() - started_at) * 1_000),
            error_code=error_code,
            limitation=limitation,
            provider_schema=provider_schema,
        )

    @staticmethod
    def _provenance(
        query: KnowledgeQuery,
        source_id: str,
        source: KnowledgeSourceConfig,
        channel: str,
    ) -> ArtifactProvenance:
        encoded = json.dumps(
            source.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
        ).encode()
        return ArtifactProvenance(
            producer_identity=f"knowledge:{source_id}",
            run_id=query.scope.run_id or f"knowledge:{query.query_id}",
            task_attempt_id=query.scope.task_id or f"knowledge:{query.query_id}",
            call_id=str(query.query_id),
            capability="knowledge.query",
            channel=channel,
            engine=source.adapter,
            engine_version=source.provider_schema,
            configuration_fingerprint=hashlib.sha256(encoded).hexdigest(),
        )
