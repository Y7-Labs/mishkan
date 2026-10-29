from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from mishkan.config.loader import ConfigLoader
from mishkan.config.models import MishkanConfig
from mishkan.config.presets import preset_text
from mishkan.crewai.team_effectiveness import (
    CrewAITeamHealthEvaluator,
    TeamHealthAssessmentOutput,
)
from mishkan.domain.errors import ErrorCode, MishkanError
from mishkan.domain.identity import new_id
from mishkan.domain.time import utc_now
from mishkan.missions import (
    AdaptationKind,
    CollaborationEdge,
    CollaborationInteraction,
    CollaborationResponsibility,
    MissionCollaborationPlan,
    MissionResourceLimit,
    ProductionShape,
    TeamDimension,
    TeamDimensionAssessment,
    TeamHealthStatus,
    TeamSignal,
)


def _config(tmp_path: Path) -> MishkanConfig:
    source = tmp_path / "config.yaml"
    source.write_text(preset_text("local"), encoding="utf-8")
    return ConfigLoader().load([source]).value


def _plan() -> MissionCollaborationPlan:
    mission_id = new_id()
    return MissionCollaborationPlan(
        mission_id=mission_id,
        version=1,
        brief_version=1,
        crew_version=1,
        production_shape=ProductionShape.SINGLE_OWNER,
        production_owner_ids=("Backend_Service_Engineer",),
        proposal_author_identity="PM",
        crewai_coordination_reference="coordination:crewai:health-fixture",
        alternatives=("single owner with downstream independent assurance",),
        shared_outcome="A verified account recovery implementation",
        user_impact="Users recover accounts without an unreviewed security regression",
        responsibilities=(
            CollaborationResponsibility(
                identity_id="Backend_Service_Engineer",
                responsibility="Produce the accepted implementation",
                decision_rights=("assignment:production",),
            ),
            CollaborationResponsibility(
                identity_id="Product_Functional_Evaluator",
                responsibility="Evaluate user behavior independently",
                decision_rights=("assignment:evaluation",),
            ),
            CollaborationResponsibility(
                identity_id="Technical_Change_Reporter",
                responsibility="Report accepted evidence and limitations",
                decision_rights=("assignment:reporting",),
            ),
        ),
        interactions=(
            CollaborationEdge(
                source_identity="Backend_Service_Engineer",
                target_identity="Product_Functional_Evaluator",
                interaction=CollaborationInteraction.HANDOFF,
                purpose="Submit implementation evidence for independent evaluation",
            ),
        ),
        context_references=("context-pack:mission:1",),
        resource_budgets=(MissionResourceLimit(name="model_tokens", value=10_000, unit="token"),),
        communication_budget=MissionResourceLimit(
            name="coordination_messages", value=20, unit="message"
        ),
        escalation_conditions=("material scope or authority changes",),
        permitted_adaptations=(AdaptationKind.REPAIR_CONTEXT,),
        stop_conditions=("accepted outcome is impossible under current authority",),
        pm_confirmation_id=new_id(),
        cto_confirmation_id=new_id(),
    )


def _output() -> TeamHealthAssessmentOutput:
    return TeamHealthAssessmentOutput(
        assessments=tuple(
            TeamDimensionAssessment(
                dimension=dimension,
                status=(
                    TeamHealthStatus.ATTENTION
                    if dimension is TeamDimension.EPISTEMIC_SAFETY
                    else TeamHealthStatus.HEALTHY
                ),
                observations=(
                    "A contradictory security finding remains visible"
                    if dimension is TeamDimension.EPISTEMIC_SAFETY
                    else f"Evidence supports {dimension.value}",
                ),
                evidence_references=("artifact:team-evidence",),
            )
            for dimension in TeamDimension
        ),
        strengths=("Ownership is explicit",),
        risks=("A contradictory security finding remains unresolved",),
        limitations=("Only one milestone interval was evaluated",),
        confidence_basis="Every assessment cites the bounded evidence pack",
        status=TeamHealthStatus.ATTENTION,
        evidence_references=("artifact:team-evidence",),
    )


def test_crewai_output_compiles_without_hiding_dissent_or_creating_a_score() -> None:
    plan = _plan()
    now = utc_now()
    evaluation = CrewAITeamHealthEvaluator.compile(
        plan,
        _output(),
        evaluator_identity="Product_Functional_Evaluator",
        interval_started_at=now - timedelta(minutes=10),
        interval_ended_at=now,
        evidence_cursor=42,
        allowed_evidence_references=("artifact:team-evidence",),
        model_route="planning",
    )

    assert evaluation.status is TeamHealthStatus.ATTENTION
    assert "contradictory" in evaluation.risks[0]
    assert "score" not in evaluation.model_dump(mode="json")
    assert "sha256:" in evaluation.confidence_basis


def test_crewai_evaluation_rejects_invented_evidence() -> None:
    plan = _plan()
    now = utc_now()
    with pytest.raises(MishkanError) as error:
        CrewAITeamHealthEvaluator.compile(
            plan,
            _output().model_copy(update={"evidence_references": ("invented:evidence",)}),
            evaluator_identity="Product_Functional_Evaluator",
            interval_started_at=now - timedelta(minutes=10),
            interval_ended_at=now,
            evidence_cursor=42,
            allowed_evidence_references=("artifact:team-evidence",),
            model_route="planning",
        )
    assert error.value.envelope.code is ErrorCode.PLAN


def test_model_failure_yields_uncertain_instead_of_fabricated_health(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = CrewAITeamHealthEvaluator(_config(tmp_path))
    plan = _plan()
    now = utc_now()
    monkeypatch.setattr(
        runner._models,
        "candidates_for",
        lambda _route_name: (SimpleNamespace(),),
    )

    def fail(*_args: object, **_kwargs: object) -> None:
        raise TimeoutError("fixture evaluator timeout")

    monkeypatch.setattr(runner, "_crew", fail)
    evaluation = runner.evaluate(
        plan,
        (
            TeamSignal(
                mission_id=plan.mission_id,
                collaboration_plan_version=plan.version,
                kind="context_drift",
                task_ids=("implement-recovery",),
                value=1,
                unit="occurrence",
                evidence_references=("artifact:team-evidence",),
            ),
        ),
        evaluator_identity="Product_Functional_Evaluator",
        interval_started_at=now - timedelta(minutes=10),
        interval_ended_at=now,
        evidence_cursor=42,
        evidence_references=("artifact:team-evidence",),
    )

    assert evaluation.status is TeamHealthStatus.UNCERTAIN
    assert {item.status for item in evaluation.assessments} == {TeamHealthStatus.UNCERTAIN}
    assert "no healthy or unhealthy conclusion was fabricated" in evaluation.limitations[0]
