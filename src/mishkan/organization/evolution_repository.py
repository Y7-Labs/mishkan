"""Durable professional evidence and scoped promotion authority."""

from __future__ import annotations

import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from mishkan.domain.errors import ErrorCode, MishkanError
from mishkan.domain.identity import new_id
from mishkan.domain.time import utc_now
from mishkan.organization.evolution import (
    ProfessionalCompetenceState,
    ProfessionalEvidenceAttribution,
    ProfessionalEvidenceKind,
    ProfessionalEvidenceOutcome,
    ProfessionalEvidenceRecord,
    ProfessionalPromotionDecision,
    ProfessionalPromotionDisposition,
    ProfessionalPromotionRequest,
)
from mishkan.organization.models import OrganizationRosterDefinition
from mishkan.persistence.migration import SchemaManager
from mishkan.persistence.runtime import DatabaseTarget
from mishkan.persistence.sqlite import (
    OrganizationRosterRow,
    OutboxRow,
    ProfessionalEvidenceRow,
    ProfessionalPromotionRow,
    create_local_engine,
)


class SQLiteProfessionalEvolutionRepository:
    def __init__(self, database_path: DatabaseTarget, *, busy_timeout_ms: int = 5_000) -> None:
        SchemaManager(database_path).require_current()
        self._engine = create_local_engine(database_path, busy_timeout_ms=busy_timeout_ms)

    def record_evidence(self, record: ProfessionalEvidenceRecord) -> ProfessionalEvidenceRecord:
        payload = record.model_dump_json()
        with Session(self._engine) as session, session.begin():
            self._require_identity(session, record.identity_id)
            self._require_identity(session, record.evaluator_identity)
            existing = session.get(ProfessionalEvidenceRow, str(record.evidence_id))
            if existing is not None:
                if existing.payload != payload:
                    raise MishkanError(
                        ErrorCode.DUPLICATE_RESULT,
                        "professional evidence identity contains different content",
                    )
                return record
            session.add(
                ProfessionalEvidenceRow(
                    evidence_id=str(record.evidence_id),
                    identity_id=record.identity_id,
                    kind=record.kind.value,
                    subject=record.subject,
                    scope_level=record.scope.level.value,
                    outcome=record.outcome.value,
                    fresh_until=record.fresh_until.isoformat(),
                    payload=payload,
                    observed_at=record.observed_at.isoformat(),
                )
            )
            self._event(
                session,
                aggregate_id=record.identity_id,
                event_type="organization.professional_evidence_recorded",
                payload={
                    "evidence_id": str(record.evidence_id),
                    "identity_id": record.identity_id,
                    "kind": record.kind.value,
                    "subject": record.subject,
                    "scope": record.scope.model_dump(mode="json"),
                    "outcome": record.outcome.value,
                    "critical": record.critical,
                },
            )
        return record

    def decide_promotion(
        self,
        request: ProfessionalPromotionRequest,
        *,
        disposition: ProfessionalPromotionDisposition,
        decided_by: str,
        policy_fingerprint: str,
        reason: str,
    ) -> ProfessionalPromotionDecision:
        with Session(self._engine) as session, session.begin():
            self._require_identity(session, request.identity_id)
            existing = session.scalar(
                select(ProfessionalPromotionRow).where(
                    ProfessionalPromotionRow.request_id == str(request.request_id)
                )
            )
            if existing is not None:
                decision = ProfessionalPromotionDecision.model_validate_json(existing.payload)
                if (
                    decision.request != request
                    or decision.disposition is not disposition
                    or decision.decided_by != decided_by
                    or decision.policy_fingerprint != policy_fingerprint
                    or decision.reason != reason
                ):
                    raise MishkanError(
                        ErrorCode.DUPLICATE_RESULT,
                        "professional promotion request contains different decision content",
                    )
                return decision
            records = self._evidence_for_subject(
                session,
                identity_id=request.identity_id,
                kind=request.kind.value,
                subject=request.subject,
            )
            by_id = {record.evidence_id: record for record in records}
            missing = set(request.supporting_evidence_ids) - set(by_id)
            if missing:
                raise MishkanError(
                    ErrorCode.OUTPUT_CONTRACT,
                    "professional promotion references unavailable evidence",
                    details={"missing_evidence_ids": sorted(str(item) for item in missing)},
                )
            support = tuple(by_id[item] for item in request.supporting_evidence_ids)
            if any(item.scope != request.source_scope for item in support):
                raise MishkanError(
                    ErrorCode.OUTPUT_CONTRACT,
                    "professional promotion evidence does not match its declared source scope",
                )
            now = utc_now()
            demonstrable = tuple(
                item
                for item in support
                if item.outcome is ProfessionalEvidenceOutcome.DEMONSTRATED
                and item.fresh_until > now
                and item.attribution is ProfessionalEvidenceAttribution.INDIVIDUAL_RESULT
            )
            if disposition is ProfessionalPromotionDisposition.ACCEPTED and not demonstrable:
                raise MishkanError(
                    ErrorCode.AUTHORIZATION_MISSING,
                    "accepted professional promotion requires fresh individually attributed "
                    "demonstrated evidence",
                )
            contradictory = tuple(
                item.evidence_id
                for item in records
                if item.outcome is ProfessionalEvidenceOutcome.CONTRADICTED
            )
            failures = tuple(
                item.evidence_id
                for item in records
                if item.outcome is ProfessionalEvidenceOutcome.FAILED
            )
            prior = session.scalar(
                select(ProfessionalPromotionRow)
                .where(
                    ProfessionalPromotionRow.identity_id == request.identity_id,
                    ProfessionalPromotionRow.kind == request.kind.value,
                    ProfessionalPromotionRow.subject == request.subject,
                )
                .order_by(ProfessionalPromotionRow.revision.desc())
                .limit(1)
            )
            decision = ProfessionalPromotionDecision(
                request=request,
                disposition=disposition,
                revision=(prior.revision if prior is not None else 0) + 1,
                previous_decision_id=(
                    ProfessionalPromotionDecision.model_validate_json(prior.payload).decision_id
                    if prior is not None
                    else None
                ),
                supporting_evidence_ids=request.supporting_evidence_ids,
                contradictory_evidence_ids=contradictory,
                failure_evidence_ids=failures,
                decided_by=decided_by,
                policy_fingerprint=policy_fingerprint,
                reason=reason,
            )
            session.add(
                ProfessionalPromotionRow(
                    decision_id=str(decision.decision_id),
                    request_id=str(request.request_id),
                    identity_id=request.identity_id,
                    kind=request.kind.value,
                    subject=request.subject,
                    disposition=disposition.value,
                    revision=decision.revision,
                    payload=decision.model_dump_json(),
                    decided_at=decision.decided_at.isoformat(),
                )
            )
            self._event(
                session,
                aggregate_id=request.identity_id,
                event_type=f"organization.professional_promotion_{disposition.value}",
                payload={
                    "decision_id": str(decision.decision_id),
                    "request_id": str(request.request_id),
                    "identity_id": request.identity_id,
                    "kind": request.kind.value,
                    "subject": request.subject,
                    "source_scope": request.source_scope.model_dump(mode="json"),
                    "target_scope": request.target_scope.model_dump(mode="json"),
                    "contradictory_evidence_ids": [str(item) for item in contradictory],
                    "failure_evidence_ids": [str(item) for item in failures],
                    "revision": decision.revision,
                },
            )
        return decision

    def competence_state(
        self,
        identity_id: str,
        *,
        kind: ProfessionalEvidenceKind,
        subject: str,
    ) -> ProfessionalCompetenceState:
        with Session(self._engine) as session:
            self._require_identity(session, identity_id)
            records = self._evidence_for_subject(
                session,
                identity_id=identity_id,
                kind=kind.value,
                subject=subject,
            )
            latest = session.scalar(
                select(ProfessionalPromotionRow)
                .where(
                    ProfessionalPromotionRow.identity_id == identity_id,
                    ProfessionalPromotionRow.kind == kind.value,
                    ProfessionalPromotionRow.subject == subject,
                )
                .order_by(ProfessionalPromotionRow.revision.desc())
                .limit(1)
            )
            latest_decision = (
                ProfessionalPromotionDecision.model_validate_json(latest.payload)
                if latest is not None
                else None
            )
            latest_accepted = session.scalar(
                select(ProfessionalPromotionRow)
                .where(
                    ProfessionalPromotionRow.identity_id == identity_id,
                    ProfessionalPromotionRow.kind == kind.value,
                    ProfessionalPromotionRow.subject == subject,
                    ProfessionalPromotionRow.disposition
                    == ProfessionalPromotionDisposition.ACCEPTED.value,
                )
                .order_by(ProfessionalPromotionRow.revision.desc())
                .limit(1)
            )
            effective_decision = (
                ProfessionalPromotionDecision.model_validate_json(latest_accepted.payload)
                if latest_accepted is not None
                else None
            )
            freshness_evaluated_at = utc_now()
            demonstrated = tuple(
                item for item in records if item.outcome is ProfessionalEvidenceOutcome.DEMONSTRATED
            )
            fresh_support = tuple(
                item.evidence_id
                for item in demonstrated
                if item.fresh_until > freshness_evaluated_at
                and item.attribution is ProfessionalEvidenceAttribution.INDIVIDUAL_RESULT
            )
            stale_support = tuple(
                item.evidence_id
                for item in demonstrated
                if item.fresh_until <= freshness_evaluated_at
            )
            decision_has_fresh_support = effective_decision is not None and bool(
                set(effective_decision.supporting_evidence_ids).intersection(fresh_support)
            )
            return ProfessionalCompetenceState(
                identity_id=identity_id,
                kind=kind,
                subject=subject,
                effective_scope=(
                    effective_decision.request.target_scope
                    if effective_decision is not None and decision_has_fresh_support
                    else None
                ),
                revision=latest_decision.revision if latest_decision is not None else 0,
                latest_decision_id=(
                    latest_decision.decision_id if latest_decision is not None else None
                ),
                freshness_evaluated_at=freshness_evaluated_at,
                supporting_evidence_ids=tuple(item.evidence_id for item in demonstrated),
                fresh_supporting_evidence_ids=fresh_support,
                stale_supporting_evidence_ids=stale_support,
                contradictory_evidence_ids=tuple(
                    item.evidence_id
                    for item in records
                    if item.outcome is ProfessionalEvidenceOutcome.CONTRADICTED
                ),
                failure_evidence_ids=tuple(
                    item.evidence_id
                    for item in records
                    if item.outcome is ProfessionalEvidenceOutcome.FAILED
                ),
            )

    def evidence(
        self,
        identity_id: str,
        *,
        kind: ProfessionalEvidenceKind | None = None,
        subject: str | None = None,
        offset: int = 0,
        limit: int = 100,
    ) -> tuple[ProfessionalEvidenceRecord, ...]:
        self._query_bound(offset, limit)
        with Session(self._engine) as session:
            self._require_identity(session, identity_id)
            query = select(ProfessionalEvidenceRow).where(
                ProfessionalEvidenceRow.identity_id == identity_id
            )
            if kind is not None:
                query = query.where(ProfessionalEvidenceRow.kind == kind.value)
            if subject is not None:
                query = query.where(ProfessionalEvidenceRow.subject == subject)
            rows = session.scalars(
                query.order_by(
                    ProfessionalEvidenceRow.observed_at,
                    ProfessionalEvidenceRow.evidence_id,
                )
                .offset(offset)
                .limit(limit)
            ).all()
            return tuple(
                ProfessionalEvidenceRecord.model_validate_json(row.payload) for row in rows
            )

    def promotion_history(
        self,
        identity_id: str,
        *,
        kind: ProfessionalEvidenceKind | None = None,
        subject: str | None = None,
        offset: int = 0,
        limit: int = 100,
    ) -> tuple[ProfessionalPromotionDecision, ...]:
        self._query_bound(offset, limit)
        with Session(self._engine) as session:
            self._require_identity(session, identity_id)
            query = select(ProfessionalPromotionRow).where(
                ProfessionalPromotionRow.identity_id == identity_id
            )
            if kind is not None:
                query = query.where(ProfessionalPromotionRow.kind == kind.value)
            if subject is not None:
                query = query.where(ProfessionalPromotionRow.subject == subject)
            rows = session.scalars(
                query.order_by(
                    ProfessionalPromotionRow.decided_at,
                    ProfessionalPromotionRow.decision_id,
                )
                .offset(offset)
                .limit(limit)
            ).all()
            return tuple(
                ProfessionalPromotionDecision.model_validate_json(row.payload) for row in rows
            )

    @staticmethod
    def _query_bound(offset: int, limit: int) -> None:
        if offset < 0 or limit < 1 or limit > 1_000:
            raise MishkanError(ErrorCode.OUTPUT_CONTRACT, "query bound is invalid")

    @staticmethod
    def _evidence_for_subject(
        session: Session,
        *,
        identity_id: str,
        kind: str,
        subject: str,
    ) -> tuple[ProfessionalEvidenceRecord, ...]:
        rows = session.scalars(
            select(ProfessionalEvidenceRow)
            .where(
                ProfessionalEvidenceRow.identity_id == identity_id,
                ProfessionalEvidenceRow.kind == kind,
                ProfessionalEvidenceRow.subject == subject,
            )
            .order_by(ProfessionalEvidenceRow.observed_at)
        ).all()
        return tuple(ProfessionalEvidenceRecord.model_validate_json(row.payload) for row in rows)

    @staticmethod
    def _require_identity(session: Session, identity_id: str) -> None:
        rows = session.scalars(select(OrganizationRosterRow)).all()
        known = {
            identity.identity_id
            for row in rows
            for identity in OrganizationRosterDefinition.model_validate_json(row.payload).identities
        }
        if identity_id not in known:
            raise MishkanError(
                ErrorCode.ROLE_CONFLICT,
                "professional evidence references an unknown organization identity",
                details={"identity_id": identity_id},
            )

    @staticmethod
    def _event(
        session: Session,
        *,
        aggregate_id: str,
        event_type: str,
        payload: dict[str, object],
    ) -> None:
        session.add(
            OutboxRow(
                id=str(new_id()),
                schema_version="1.0",
                aggregate_id=aggregate_id,
                entity_type="professional_profile",
                run_id=None,
                task_id=None,
                identity_id=aggregate_id,
                team_id=None,
                security_relevant=False,
                event_type=event_type,
                source="mishkan.organization.evolution",
                payload=json.dumps(payload, sort_keys=True, separators=(",", ":")),
                occurred_at=utc_now().isoformat(),
                command_id=None,
                correlation_id=None,
                causation_id=None,
                sensitivity="internal",
                published_at=None,
            )
        )
