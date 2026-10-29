from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from pydantic import ValidationError

from mishkan.application import ApplicationCommand
from mishkan.config.loader import ConfigLoader
from mishkan.config.models import MishkanConfig, ProjectConfig
from mishkan.config.presets import preset_text
from mishkan.daemon import DaemonBootstrap, create_app
from mishkan.daemon.auth import TokenFile
from mishkan.domain.errors import ErrorCode, MishkanError
from mishkan.domain.time import utc_now
from mishkan.missions import SQLiteMissionRepository
from mishkan.organization import (
    LearningScopeLevel,
    ProfessionalEvidenceAttribution,
    ProfessionalEvidenceKind,
    ProfessionalEvidenceOutcome,
    ProfessionalEvidenceRecord,
    ProfessionalLearningScope,
    ProfessionalPromotionDisposition,
    ProfessionalPromotionRequest,
    load_canonical_organization,
)
from mishkan.organization import evolution_repository as evolution_repository_module
from mishkan.organization.evolution_repository import SQLiteProfessionalEvolutionRepository
from mishkan.persistence.migration import SchemaManager


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _repositories(
    tmp_path: Path,
) -> SQLiteProfessionalEvolutionRepository:
    database = tmp_path / "mishkan.db"
    SchemaManager(database).initialize()
    SQLiteMissionRepository(database).record_organization(load_canonical_organization())
    return SQLiteProfessionalEvolutionRepository(database)


def _config(tmp_path: Path) -> MishkanConfig:
    source = tmp_path / "config.yaml"
    source.write_text(preset_text("local"), encoding="utf-8")
    loaded = ConfigLoader().load([source]).value
    return loaded.model_copy(update={"project": ProjectConfig(workspace=tmp_path)})


def _scope(level: LearningScopeLevel, scope_id: str) -> ProfessionalLearningScope:
    return ProfessionalLearningScope(level=level, scope_id=scope_id)


def _evidence(
    *,
    outcome: ProfessionalEvidenceOutcome,
    evaluator: str = "Software_Technical_Evaluator",
    critical: bool = True,
) -> ProfessionalEvidenceRecord:
    observed = utc_now()
    return ProfessionalEvidenceRecord(
        identity_id="Backend_Service_Engineer",
        kind=ProfessionalEvidenceKind.TOOL_MASTERY,
        subject="postgresql.schema-review",
        scope=_scope(LearningScopeLevel.MISSION, "mission:recovery"),
        outcome=outcome,
        critical=critical,
        source_references=("artifact:execution-result",),
        evaluation_references=("evaluation:independent-review",),
        evaluator_identity=evaluator,
        recorded_by="mishkand",
        observed_at=observed,
        fresh_until=observed + timedelta(days=30),
        rationale="Independent evidence from an attributable mission result",
    )


def test_critical_mastery_cannot_be_self_certified() -> None:
    with pytest.raises(ValidationError, match="self-certify"):
        _evidence(
            outcome=ProfessionalEvidenceOutcome.DEMONSTRATED,
            evaluator="Backend_Service_Engineer",
        )


def test_promotion_preserves_support_failure_and_contradiction_evidence(
    tmp_path: Path,
) -> None:
    repository = _repositories(tmp_path)
    demonstrated = repository.record_evidence(
        _evidence(outcome=ProfessionalEvidenceOutcome.DEMONSTRATED)
    )
    failed = repository.record_evidence(_evidence(outcome=ProfessionalEvidenceOutcome.FAILED))
    contradicted = repository.record_evidence(
        _evidence(outcome=ProfessionalEvidenceOutcome.CONTRADICTED)
    )
    request = ProfessionalPromotionRequest(
        identity_id=demonstrated.identity_id,
        kind=demonstrated.kind,
        subject=demonstrated.subject,
        source_scope=demonstrated.scope,
        target_scope=_scope(LearningScopeLevel.PROJECT, "project:api"),
        supporting_evidence_ids=(demonstrated.evidence_id,),
        requested_by="PM",
        rationale="Promote only the independently demonstrated project scope",
    )

    decision = repository.decide_promotion(
        request,
        disposition=ProfessionalPromotionDisposition.ACCEPTED,
        decided_by="CTO",
        policy_fingerprint="a" * 64,
        reason="Fresh independent evidence supports this bounded promotion",
    )
    state = repository.competence_state(
        demonstrated.identity_id,
        kind=demonstrated.kind,
        subject=demonstrated.subject,
    )

    assert decision.supporting_evidence_ids == (demonstrated.evidence_id,)
    assert decision.failure_evidence_ids == (failed.evidence_id,)
    assert decision.contradictory_evidence_ids == (contradicted.evidence_id,)
    assert state.effective_scope == request.target_scope
    assert state.failure_evidence_ids == (failed.evidence_id,)
    assert state.contradictory_evidence_ids == (contradicted.evidence_id,)
    assert repository.evidence(demonstrated.identity_id) == (
        demonstrated,
        failed,
        contradicted,
    )
    assert repository.promotion_history(demonstrated.identity_id) == (decision,)
    assert repository.evidence(
        demonstrated.identity_id,
        kind=demonstrated.kind,
        subject=demonstrated.subject,
        offset=1,
        limit=1,
    ) == (failed,)


def test_stale_or_non_demonstrated_evidence_cannot_authorize_promotion(
    tmp_path: Path,
) -> None:
    repository = _repositories(tmp_path)
    partial = repository.record_evidence(
        _evidence(outcome=ProfessionalEvidenceOutcome.PARTIAL, critical=False)
    )
    request = ProfessionalPromotionRequest(
        identity_id=partial.identity_id,
        kind=partial.kind,
        subject=partial.subject,
        source_scope=partial.scope,
        target_scope=_scope(LearningScopeLevel.PROJECT, "project:api"),
        supporting_evidence_ids=(partial.evidence_id,),
        requested_by="PM",
        rationale="A partial result must not silently become mastery",
    )

    with pytest.raises(MishkanError) as error:
        repository.decide_promotion(
            request,
            disposition=ProfessionalPromotionDisposition.ACCEPTED,
            decided_by="CTO",
            policy_fingerprint="b" * 64,
            reason="This attempted promotion lacks demonstrated evidence",
        )
    assert error.value.envelope.code is ErrorCode.AUTHORIZATION_MISSING


def test_aggregate_team_evidence_cannot_promote_individual_competence(
    tmp_path: Path,
) -> None:
    repository = _repositories(tmp_path)
    team_evidence = repository.record_evidence(
        _evidence(outcome=ProfessionalEvidenceOutcome.DEMONSTRATED).model_copy(
            update={
                "attribution": ProfessionalEvidenceAttribution.TEAM_AGGREGATE,
                "source_references": ("team-health:evaluation-1",),
                "evaluation_references": ("team-health:evaluation-1",),
                "rationale": "The mission crew performed well as a whole",
            }
        )
    )
    request = ProfessionalPromotionRequest(
        identity_id=team_evidence.identity_id,
        kind=team_evidence.kind,
        subject=team_evidence.subject,
        source_scope=team_evidence.scope,
        target_scope=_scope(LearningScopeLevel.PROJECT, "project:api"),
        supporting_evidence_ids=(team_evidence.evidence_id,),
        requested_by="PM",
        rationale="Test that team health cannot silently become individual mastery",
    )

    with pytest.raises(MishkanError) as error:
        repository.decide_promotion(
            request,
            disposition=ProfessionalPromotionDisposition.ACCEPTED,
            decided_by="CTO",
            policy_fingerprint="f" * 64,
            reason="Aggregate team evidence is not individual competence evidence",
        )

    assert error.value.envelope.code is ErrorCode.AUTHORIZATION_MISSING
    assert repository.evidence(team_evidence.identity_id) == (team_evidence,)
    state = repository.competence_state(
        team_evidence.identity_id,
        kind=team_evidence.kind,
        subject=team_evidence.subject,
    )
    assert state.effective_scope is None
    assert state.fresh_supporting_evidence_ids == ()


def test_competence_scope_expires_without_erasing_promotion_history(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = _repositories(tmp_path)
    observed_at = datetime(2026, 1, 1, tzinfo=UTC)
    evidence = repository.record_evidence(
        _evidence(outcome=ProfessionalEvidenceOutcome.DEMONSTRATED).model_copy(
            update={
                "observed_at": observed_at,
                "fresh_until": observed_at + timedelta(days=1),
            }
        )
    )
    request = ProfessionalPromotionRequest(
        identity_id=evidence.identity_id,
        kind=evidence.kind,
        subject=evidence.subject,
        source_scope=evidence.scope,
        target_scope=_scope(LearningScopeLevel.PROJECT, "project:api"),
        supporting_evidence_ids=(evidence.evidence_id,),
        requested_by="PM",
        rationale="Promote only while attributable support remains fresh",
    )
    monkeypatch.setattr(
        evolution_repository_module,
        "utc_now",
        lambda: observed_at + timedelta(hours=1),
    )
    decision = repository.decide_promotion(
        request,
        disposition=ProfessionalPromotionDisposition.ACCEPTED,
        decided_by="CTO",
        policy_fingerprint="c" * 64,
        reason="The demonstrated evidence is fresh at decision time",
    )
    fresh = repository.competence_state(
        evidence.identity_id,
        kind=evidence.kind,
        subject=evidence.subject,
    )
    monkeypatch.setattr(
        evolution_repository_module,
        "utc_now",
        lambda: observed_at + timedelta(days=2),
    )
    stale = repository.competence_state(
        evidence.identity_id,
        kind=evidence.kind,
        subject=evidence.subject,
    )

    assert fresh.effective_scope == request.target_scope
    assert fresh.fresh_supporting_evidence_ids == (evidence.evidence_id,)
    assert stale.effective_scope is None
    assert stale.fresh_supporting_evidence_ids == ()
    assert stale.stale_supporting_evidence_ids == (evidence.evidence_id,)
    assert stale.latest_decision_id == decision.decision_id
    assert stale.supporting_evidence_ids == (evidence.evidence_id,)
    assert repository.promotion_history(evidence.identity_id) == (decision,)


def test_rejected_broader_promotion_does_not_revoke_prior_accepted_scope(
    tmp_path: Path,
) -> None:
    repository = _repositories(tmp_path)
    evidence = repository.record_evidence(
        _evidence(outcome=ProfessionalEvidenceOutcome.DEMONSTRATED)
    )
    accepted_request = ProfessionalPromotionRequest(
        identity_id=evidence.identity_id,
        kind=evidence.kind,
        subject=evidence.subject,
        source_scope=evidence.scope,
        target_scope=_scope(LearningScopeLevel.PROJECT, "project:api"),
        supporting_evidence_ids=(evidence.evidence_id,),
        requested_by="PM",
        rationale="Accept the demonstrated project scope",
    )
    accepted = repository.decide_promotion(
        accepted_request,
        disposition=ProfessionalPromotionDisposition.ACCEPTED,
        decided_by="CTO",
        policy_fingerprint="d" * 64,
        reason="The project scope is independently supported",
    )
    rejected_request = ProfessionalPromotionRequest(
        identity_id=evidence.identity_id,
        kind=evidence.kind,
        subject=evidence.subject,
        source_scope=evidence.scope,
        target_scope=_scope(LearningScopeLevel.ORGANIZATION, "organization:mishkan"),
        supporting_evidence_ids=(evidence.evidence_id,),
        requested_by="PM",
        rationale="Ask whether one project result supports organization-wide scope",
    )
    rejected = repository.decide_promotion(
        rejected_request,
        disposition=ProfessionalPromotionDisposition.REJECTED,
        decided_by="CTO",
        policy_fingerprint="e" * 64,
        reason="One project does not prove organization-wide mastery",
    )

    state = repository.competence_state(
        evidence.identity_id,
        kind=evidence.kind,
        subject=evidence.subject,
    )

    assert state.effective_scope == accepted.request.target_scope
    assert state.revision == rejected.revision
    assert state.latest_decision_id == rejected.decision_id
    assert repository.promotion_history(evidence.identity_id) == (accepted, rejected)


def test_promotion_scope_must_expand_in_explicit_order() -> None:
    with pytest.raises(ValidationError, match="broader"):
        ProfessionalPromotionRequest(
            identity_id="Backend_Service_Engineer",
            kind=ProfessionalEvidenceKind.DEMONSTRATED_COMPETENCE,
            subject="api.design",
            source_scope=_scope(LearningScopeLevel.PROJECT, "project:api"),
            target_scope=_scope(LearningScopeLevel.MISSION, "mission:one"),
            supporting_evidence_ids=(uuid4(),),
            requested_by="PM",
            rationale="Invalid narrowing disguised as promotion",
        )


@pytest.mark.anyio
async def test_professional_evolution_uses_governed_daemon_commands(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    paths = DaemonBootstrap().setup(config)
    token = TokenFile(paths.token_file).read()
    evidence = _evidence(outcome=ProfessionalEvidenceOutcome.DEMONSTRATED).model_copy(
        update={"recorded_by": token.principal_id}
    )
    request = ProfessionalPromotionRequest(
        identity_id=evidence.identity_id,
        kind=evidence.kind,
        subject=evidence.subject,
        source_scope=evidence.scope,
        target_scope=_scope(LearningScopeLevel.PROJECT, "project:api"),
        supporting_evidence_ids=(evidence.evidence_id,),
        requested_by=token.principal_id,
        rationale="Promote the demonstrated competence only to this project",
    )
    headers = {"Authorization": f"Bearer {token.token}"}
    transport = httpx.ASGITransport(app=create_app(config))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        forged_evidence = evidence.model_copy(
            update={"evidence_id": uuid4(), "recorded_by": "another-identity"}
        )
        forged_record = await client.post(
            "/v1/commands",
            headers=headers,
            json=ApplicationCommand(
                command_type="organization.evidence.record",
                actor_id=token.principal_id,
                target_type="professional_evidence",
                target_id=str(forged_evidence.evidence_id),
                payload={"evidence": forged_evidence.model_dump(mode="json")},
            ).model_dump(mode="json"),
        )
        recorded = await client.post(
            "/v1/commands",
            headers=headers,
            json=ApplicationCommand(
                command_type="organization.evidence.record",
                actor_id=token.principal_id,
                target_type="professional_evidence",
                target_id=str(evidence.evidence_id),
                payload={"evidence": evidence.model_dump(mode="json")},
            ).model_dump(mode="json"),
        )
        decided = await client.post(
            "/v1/commands",
            headers=headers,
            json=ApplicationCommand(
                command_type="organization.promotion.decide",
                actor_id=token.principal_id,
                target_type="professional_promotion_request",
                target_id=str(request.request_id),
                payload={
                    "request": request.model_dump(mode="json"),
                    "disposition": "accepted",
                    "reason": "Fresh independent evidence supports the bounded promotion",
                },
            ).model_dump(mode="json"),
        )
        state = await client.get(
            f"/v1/organization/profiles/{evidence.identity_id}/competence",
            headers=headers,
            params={"kind": evidence.kind.value, "subject": evidence.subject},
        )
        evidence_history = await client.get(
            f"/v1/organization/profiles/{evidence.identity_id}/evidence",
            headers=headers,
            params={"kind": evidence.kind.value, "subject": evidence.subject},
        )
        promotion_history = await client.get(
            f"/v1/organization/profiles/{evidence.identity_id}/promotions",
            headers=headers,
            params={"kind": evidence.kind.value, "subject": evidence.subject},
        )

    assert forged_record.status_code == 403, forged_record.text
    assert recorded.status_code == 200, recorded.text
    assert decided.status_code == 200, decided.text
    assert state.status_code == 200, state.text
    assert evidence_history.status_code == 200, evidence_history.text
    assert promotion_history.status_code == 200, promotion_history.text
    assert state.json()["effective_scope"] == {
        "level": "project",
        "scope_id": "project:api",
    }
    assert state.json()["supporting_evidence_ids"] == [str(evidence.evidence_id)]
    assert evidence_history.json() == [evidence.model_dump(mode="json")]
    assert promotion_history.json() == [decided.json()["payload"]]
