from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

import pytest
from crewai import BaseLLM
from pydantic import Field

from mishkan.config.models import MishkanConfig
from mishkan.crewai.mission_governance import (
    CrewAICollaborationProposal,
    CrewAIMissionGovernanceRunner,
    CTOApprovedAssignment,
    CTOMissionReview,
    MissionGovernanceEvidence,
    MissionGovernanceResult,
    PMMissionProposal,
)
from mishkan.crewai.team_effectiveness import CrewAITeamHealthEvaluator
from mishkan.domain.identity import new_id
from mishkan.domain.time import utc_now
from mishkan.missions import (
    AdaptationDisposition,
    AdaptationKind,
    CollaborationEdge,
    CollaborationExecutionKind,
    CollaborationInteraction,
    CollaborationResponsibility,
    CrewAdaptationDecision,
    CrewAssignmentKind,
    MissionCollaborationExecution,
    MissionCollaborationPlan,
    MissionEnvironmentIntent,
    MissionHandoff,
    MissionOrigin,
    MissionOriginKind,
    MissionRecord,
    MissionResourceLimit,
    MissionRetrospective,
    MissionTaskAssignment,
    ProductionShape,
    RetrospectiveProposal,
    SQLiteMissionRepository,
    SQLiteTeamEffectivenessRepository,
    TeamDimension,
    TeamDimensionAssessment,
    TeamHealthEvaluation,
    TeamHealthStatus,
    TeamSignal,
)
from mishkan.organization import load_canonical_organization
from mishkan.persistence.migration import SchemaManager

OLLAMA_ENDPOINT = "http://127.0.0.1:11434"
OLLAMA_MODEL = "mishkan-qwen2.5-coder-1.5b-16k:latest"


class _DeterministicCrewAILLM(BaseLLM):
    """Deterministic response source that still runs through CrewAI Agent/Task/Crew."""

    responses: dict[str, str]
    calls: list[str] = Field(default_factory=list)

    def call(
        self,
        messages: object,
        tools: object = None,
        callbacks: object = None,
        available_functions: object = None,
        from_task: object = None,
        from_agent: object = None,
        response_model: object = None,
    ) -> str:
        del tools, callbacks, available_functions, from_task, response_model
        role = getattr(from_agent, "role", None)
        if role not in self.responses:
            serialized = json.dumps(messages, default=str)
            role = "CTO" if "Independently confirm or reject" in serialized else "PM"
        self.calls.append(str(role))
        return self.responses[str(role)]


class _DeterministicModelRouter:
    def __init__(self, llm: BaseLLM) -> None:
        self._llm = llm

    def candidates_for(
        self,
        route_name: str,
        *,
        timeout_seconds: int | None = None,
    ) -> Iterator[BaseLLM]:
        del route_name, timeout_seconds
        yield self._llm


def _require_ollama(*models: str) -> None:
    try:
        with urlopen(f"{OLLAMA_ENDPOINT}/api/tags", timeout=2) as response:
            payload = json.load(response)
    except (OSError, URLError) as exc:
        pytest.skip(f"local Ollama is unavailable: {type(exc).__name__}")
    available = {item["name"] for item in payload.get("models", [])}
    missing = sorted(set(models or (OLLAMA_MODEL,)) - available)
    if missing:
        pytest.skip(f"local Ollama models are not installed: {missing}")


def _config(workspace: Path, *, model: str = OLLAMA_MODEL) -> MishkanConfig:
    return MishkanConfig.model_validate(
        {
            "schema_version": "1.1",
            "mode": "local",
            "timezone": "UTC",
            "project": {"workspace": str(workspace)},
            "providers": {
                "ollama-local": {
                    "kind": "ollama",
                    "endpoint": OLLAMA_ENDPOINT,
                }
            },
            "model_routes": {
                "planning": {"candidates": [{"provider": "ollama-local", "model": model}]},
                "execution": {"candidates": [{"provider": "ollama-local", "model": model}]},
            },
            "policy_sources": ["package://mishkan.resources.policies/local-control-plane.yaml"],
            "tool_sources": ["package://mishkan.resources.tools/core-catalog.yaml"],
            "inspection_profile": "package://mishkan.resources.inspection/default-security.yaml",
            "isolation_profiles": ["package://mishkan.resources.isolation/local-no-network.yaml"],
            "crewai": {
                "tracing": False,
                "telemetry": False,
                "temperature": 0,
                "model_timeout_seconds": 180,
                "model_transport_retries": 0,
                "model_max_output_tokens": 2048,
                "max_agent_iterations": 2,
                "task_execution_retries": 0,
                "plan_validation_retries": 0,
                "review_retries": 0,
                "structured_output_retries": 2,
            },
        }
    )


def _plan() -> MissionCollaborationPlan:
    return MissionCollaborationPlan(
        mission_id=new_id(),
        version=1,
        brief_version=1,
        crew_version=1,
        production_shape=ProductionShape.SINGLE_OWNER,
        production_owner_ids=("Backend_Service_Engineer",),
        proposal_author_identity="PM",
        crewai_coordination_reference="coordination:crewai:live-acceptance",
        alternatives=("one production owner with independent assurance",),
        shared_outcome="A verified account recovery implementation",
        user_impact="Users recover accounts without an unreviewed security regression",
        responsibilities=(
            CollaborationResponsibility(
                identity_id="Backend_Service_Engineer",
                responsibility="Produce the recovery implementation",
                decision_rights=("assignment:production",),
            ),
            CollaborationResponsibility(
                identity_id="Product_Functional_Evaluator",
                responsibility="Evaluate outcomes independently",
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
                artifact_contracts=("artifact:implementation",),
            ),
        ),
        context_references=("context-pack:mission:live",),
        artifact_references=("artifact:mission-brief",),
        resource_budgets=(MissionResourceLimit(name="model_tokens", value=8_000, unit="token"),),
        communication_budget=MissionResourceLimit(
            name="coordination_messages", value=12, unit="message"
        ),
        escalation_conditions=("material scope or authority changes",),
        permitted_adaptations=(AdaptationKind.REPAIR_CONTEXT,),
        stop_conditions=("accepted outcome is impossible under current authority",),
        pm_confirmation_id=new_id(),
        cto_confirmation_id=new_id(),
    )


def _governance_evidence() -> tuple[MissionGovernanceEvidence, ...]:
    identities = (
        "Backend_Service_Engineer",
        "Product_Functional_Evaluator",
        "Technical_Change_Reporter",
    )
    evidence = [
        MissionGovernanceEvidence(
            reference="evidence:product-analysis",
            summary=(
                "Account recovery needs one backend production owner, independent functional "
                "evaluation, and an attributable technical report."
            ),
            evidence_kind="product",
        ),
        MissionGovernanceEvidence(
            reference="evidence:technical-review",
            summary=(
                "The technical review covers platform, security, quality, operability, and the "
                "independence boundary between production, evaluation, and reporting."
            ),
            evidence_kind="technical",
        ),
    ]
    for identity in identities:
        evidence.extend(
            (
                MissionGovernanceEvidence(
                    reference=f"scope:repository:api:{identity}",
                    summary=f"{identity} is authorized for the account-recovery API mission.",
                    evidence_kind="project",
                    subject_identity_id=identity,
                ),
                MissionGovernanceEvidence(
                    reference=f"profile:{identity}:competence",
                    summary=f"Current accepted competence evidence for {identity}.",
                    evidence_kind="competence",
                    subject_identity_id=identity,
                ),
                MissionGovernanceEvidence(
                    reference=f"profile:{identity}:availability",
                    summary=f"{identity} is available for this bounded mission.",
                    evidence_kind="availability",
                    subject_identity_id=identity,
                ),
                MissionGovernanceEvidence(
                    reference=f"profile:{identity}:independence",
                    summary=f"Conflict and independence evidence for {identity}.",
                    evidence_kind="independence",
                    subject_identity_id=identity,
                ),
            )
        )
    return tuple(evidence)


def _mission() -> MissionRecord:
    organization = load_canonical_organization()
    return MissionRecord(
        origin=MissionOrigin(
            kind=MissionOriginKind.CEO,
            actor_id="ceo:y4nn777",
            objective=("Design a verified account-recovery API without weakening account security"),
            source_references=("evidence:product-analysis", "evidence:technical-review"),
        ),
        organization_id=organization.organization_id,
        organization_version=organization.organization_version,
    )


def _governance_responses() -> dict[str, str]:
    collaboration = CrewAICollaborationProposal(
        production_shape=ProductionShape.SINGLE_OWNER,
        production_owner_ids=("Backend_Service_Engineer",),
        alternatives=("single owner with independent evaluation and reporting",),
        interactions=(
            CollaborationEdge(
                source_identity="Backend_Service_Engineer",
                target_identity="Product_Functional_Evaluator",
                interaction=CollaborationInteraction.HANDOFF,
                purpose="Submit implementation evidence for independent evaluation",
            ),
            CollaborationEdge(
                source_identity="Product_Functional_Evaluator",
                target_identity="Technical_Change_Reporter",
                interaction=CollaborationInteraction.HANDOFF,
                purpose="Submit accepted evaluation evidence for reporting",
            ),
        ),
    )
    pm = PMMissionProposal(
        problem="Users need account recovery without weakening account security",
        desired_outcome="A verified recovery API with explicit residual risk",
        scope=("repository:api",),
        exclusions=("production deployment",),
        acceptance_criteria=("independent recovery tests pass",),
        constraints=("preserve existing account semantics",),
        risks=("account takeover",),
        authority_scope=("repository:api",),
        proposed_identity_ids=(
            "Backend_Service_Engineer",
            "Product_Functional_Evaluator",
            "Technical_Change_Reporter",
        ),
        evidence_requirements=("test report", "security evidence"),
        escalation_conditions=("material security tradeoff",),
        environment_intent=MissionEnvironmentIntent(
            required_evidence=("environment readiness",),
            environment_dependent=True,
        ),
        rationale="One producer plus independent evaluation and reporting is sufficient",
        evidence_references=("evidence:product-analysis",),
        collaboration=collaboration,
    )
    assignments = tuple(
        CTOApprovedAssignment(
            identity_id=identity_id,
            assignment_kind=kind,
            responsibility=responsibility,
            conflict_assessment="Production, evaluation, and reporting remain separated",
            risk_coverage=("account recovery correctness", "independent assurance"),
        )
        for identity_id, kind, responsibility in (
            (
                "Backend_Service_Engineer",
                CrewAssignmentKind.PRODUCTION,
                "Implement the verified account recovery API",
            ),
            (
                "Product_Functional_Evaluator",
                CrewAssignmentKind.EVALUATION,
                "Evaluate recovery behavior independently",
            ),
            (
                "Technical_Change_Reporter",
                CrewAssignmentKind.REPORTING,
                "Report accepted evidence and residual risk",
            ),
        )
    )
    cto = CTOMissionReview(
        disposition="confirmed",
        rationale="The proposal preserves technical and assurance boundaries",
        evidence_references=("evidence:technical-review",),
        coverage=("technical", "platform", "security", "quality", "operability"),
        mission_lead_id="Backend_Service_Engineer",
        approved_assignments=assignments,
        unresolved_findings=(),
        approved_collaboration=collaboration,
    )
    return {"PM": pm.model_dump_json(), "CTO": cto.model_dump_json()}


def _persist_complete_team_lifecycle(
    result: MissionGovernanceResult,
    database: Path,
) -> tuple[MissionCollaborationExecution, ...]:
    assert result.crew is not None
    SchemaManager(database).initialize()
    missions = SQLiteMissionRepository(database)
    missions.record_organization(load_canonical_organization())
    durable_mission = missions.create_mission(result.mission)
    missions.record_brief(result.brief, expected_revision=durable_mission.revision)
    missions.record_crew(
        result.crew,
        expected_revision=missions.mission(str(result.mission.mission_id)).revision,
    )
    plan = result.collaboration_plan(
        version=1,
        context_references=("context-pack:mission:governance-acceptance",),
        artifact_references=("artifact:mission-brief",),
        resource_budgets=(
            MissionResourceLimit(name="model_tokens", value=8_000, unit="token"),
        ),
        communication_budget=MissionResourceLimit(
            name="coordination_messages", value=12, unit="message"
        ),
        permitted_adaptations=(AdaptationKind.REPAIR_CONTEXT,),
        stop_conditions=("accepted outcome is impossible under current authority",),
        user_impact="Users recover access without an unreviewed security regression",
    )
    teams = SQLiteTeamEffectivenessRepository(database)
    teams.record_collaboration_plan(plan)
    assignment = missions.record_assignment(
        MissionTaskAssignment(
            mission_id=result.mission.mission_id,
            crew_version=result.crew.version,
            task_id="implement-recovery",
            accountable_owner="Backend_Service_Engineer",
            assignment_kind=CrewAssignmentKind.PRODUCTION,
            expected_result="A verified recovery implementation",
            completion_criteria=("independent recovery tests pass",),
            authority_scope=("repository:api",),
            exact_tools=("file.read", "file.patch", "process.run"),
            path_scopes=("repository:api",),
            limits=(MissionResourceLimit(name="wall_time", value=1_800, unit="seconds"),),
            required_evidence=("test report", "change set"),
            requires_independent_evaluation=True,
        )
    )
    now = utc_now()
    health = TeamHealthEvaluation(
        mission_id=result.mission.mission_id,
        collaboration_plan_version=plan.version,
        interval_started_at=now - timedelta(minutes=5),
        interval_ended_at=now,
        evidence_cursor=1,
        assessments=tuple(
            TeamDimensionAssessment(
                dimension=dimension,
                status=TeamHealthStatus.HEALTHY,
                observations=(f"Accepted evidence covers {dimension.value}",),
                evidence_references=("artifact:health-evidence",),
            )
            for dimension in TeamDimension
        ),
        strengths=("Clear ownership and independent assurance",),
        risks=(),
        limitations=("Bounded acceptance fixture",),
        confidence_basis="Every dimension cites immutable accepted evidence",
        status=TeamHealthStatus.HEALTHY,
        evaluator_identity="Product_Functional_Evaluator",
        evidence_references=("artifact:health-evidence",),
    )
    teams.record_health(health)
    handoff = MissionHandoff(
        mission_id=result.mission.mission_id,
        collaboration_plan_version=plan.version,
        from_identity="Backend_Service_Engineer",
        to_identity="Product_Functional_Evaluator",
        task_id=assignment.task_id,
        result_references=("result:implement-recovery",),
        artifact_references=("artifact:implementation-result",),
        decisions=("Preserve existing account semantics",),
        changed_assumptions=(),
        uncertainty=("Provider latency remains to be benchmarked",),
        failures=(),
        unresolved_questions=(),
        required_next_action="Evaluate behavior and security boundaries",
        acceptance_conditions=("Independent recovery tests pass",),
    )
    teams.record_handoff(handoff)
    adaptation = CrewAdaptationDecision(
        mission_id=result.mission.mission_id,
        detected_condition="The evaluator needs one repaired context reference",
        considered_alternatives=("Pause evaluation", "Repair the Context Pack"),
        expected_benefit="Restore common ground without expanding authority",
        adaptation_kinds=(AdaptationKind.REPAIR_CONTEXT,),
        affected_task_ids=(assignment.task_id,),
        affected_assignments=(assignment.assignment_id,),
        authority_basis=(f"collaboration-plan:{plan.version}",),
        disposition=AdaptationDisposition.BOUNDED,
        before_crew_version=result.crew.version,
        after_crew_version=result.crew.version,
        before_plan_version=plan.version,
        after_plan_version=plan.version,
        evidence_references=("artifact:health-evidence",),
        decided_by="PM",
    )
    teams.record_adaptation(adaptation)
    retrospective = MissionRetrospective(
        mission_id=result.mission.mission_id,
        collaboration_plan_version=plan.version,
        trigger="explicit_request",
        expected_outcomes=(result.brief.desired_outcome,),
        observed_outcomes=("CrewAI composition and governed collaboration were attributable",),
        successful_patterns=("Smallest sufficient production shape with independent assurance",),
        failed_patterns=(),
        contradictions=(),
        rework=(),
        coordination_costs=("One PM proposal and one CTO review",),
        proposals=(
            RetrospectiveProposal(
                destination="mission_template",
                summary="Review this collaboration pattern before any promotion",
                evidence_references=("artifact:retrospective-evidence",),
            ),
        ),
        non_promoted_findings=("No retrospective proposal was activated automatically",),
        evidence_references=("artifact:retrospective-evidence",),
        authored_by="Technical_Change_Reporter",
    )
    teams.record_retrospective(retrospective)
    for kind, participants, evidence in (
        (
            CollaborationExecutionKind.HANDOFF,
            ("Backend_Service_Engineer", "Product_Functional_Evaluator"),
            f"handoff:{handoff.handoff_id}",
        ),
        (
            CollaborationExecutionKind.EVALUATE,
            ("Product_Functional_Evaluator", "Technical_Change_Reporter"),
            f"evaluation:{health.evaluation_id}",
        ),
        (
            CollaborationExecutionKind.ADAPT,
            ("Backend_Service_Engineer", "Product_Functional_Evaluator"),
            f"adaptation:{adaptation.decision_id}",
        ),
    ):
        teams.record_interaction(
            MissionCollaborationExecution(
                mission_id=result.mission.mission_id,
                collaboration_plan_version=plan.version,
                kind=kind,
                participant_ids=participants,
                task_ids=(assignment.task_id,),
                causal_reference=f"command:{new_id()}",
                evidence_references=(evidence,),
            )
        )
    return teams.interactions(str(result.mission.mission_id))


@pytest.mark.acceptance
@pytest.mark.ollama
def test_real_crewai_interprets_attributed_team_evidence_without_a_score(
    tmp_path: Path,
) -> None:
    _require_ollama(OLLAMA_MODEL)
    plan = _plan()
    evidence_reference = "artifact:live-team-evidence"
    signal = TeamSignal(
        mission_id=plan.mission_id,
        collaboration_plan_version=plan.version,
        kind="context_drift",
        task_ids=("implement-recovery",),
        value=1,
        unit="occurrence",
        evidence_references=(evidence_reference,),
    )
    now = utc_now()

    evaluator = CrewAITeamHealthEvaluator(_config(tmp_path))
    evaluations = tuple(
        evaluator.evaluate(
            plan,
            (signal,),
            evaluator_identity="Product_Functional_Evaluator",
            interval_started_at=now - timedelta(minutes=5),
            interval_ended_at=now,
            evidence_cursor=index,
            evidence_references=(evidence_reference,),
        )
        for index in (1, 2)
    )

    for evaluation in evaluations:
        assert evaluation.status is not TeamHealthStatus.UNCERTAIN
        assert {item.dimension for item in evaluation.assessments} == set(TeamDimension)
        assert evaluation.evidence_references == (evidence_reference,)
        assert evidence_reference in {
            reference
            for assessment in evaluation.assessments
            for reference in assessment.evidence_references
        }
        assert "score" not in evaluation.model_dump(mode="json")
        assert "CrewAI route" in evaluation.confidence_basis
    assert evaluations[0].status == evaluations[1].status


@pytest.mark.acceptance
def test_real_crewai_pm_and_cto_compose_an_attributed_mission_crew(
    tmp_path: Path,
) -> None:
    llm = _DeterministicCrewAILLM(
        model="deterministic-governance-fixture",
        provider="test",
        responses=_governance_responses(),
    )
    result = CrewAIMissionGovernanceRunner(
        _config(tmp_path),
        model_router=_DeterministicModelRouter(llm),
    ).propose(_mission(), _governance_evidence())

    assert result.disposition == "agreed"
    assert result.crew is not None
    assert result.collaboration is not None
    assert result.lineage.runtime == "crewai-1.x"
    assert result.brief.pm_confirmation is not None
    assert result.brief.cto_confirmation is not None
    assert set(result.collaboration.production_owner_ids).issubset(
        {member.identity_id for member in result.crew.members}
    )
    assert result.collaboration.alternatives
    assert llm.calls == ["PM", "CTO"]
    interactions = _persist_complete_team_lifecycle(result, tmp_path / "mishkan.db")
    assert {item.kind for item in interactions} == {
        CollaborationExecutionKind.HANDOFF,
        CollaborationExecutionKind.EVALUATE,
        CollaborationExecutionKind.ADAPT,
    }
