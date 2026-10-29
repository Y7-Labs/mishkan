from datetime import datetime, timedelta
from pathlib import Path
from typing import Literal

import pytest
from pydantic import ValidationError

from mishkan.domain.errors import ErrorCode, MishkanError
from mishkan.domain.identity import new_id
from mishkan.domain.time import utc_now
from mishkan.events import EventEnvelope
from mishkan.missions import (
    AdaptationDisposition,
    AdaptationKind,
    CollaborationEdge,
    CollaborationHistoryCitation,
    CollaborationHistorySelector,
    CollaborationInteraction,
    CollaborationResponsibility,
    CrewAdaptationDecision,
    CrewAssignmentKind,
    CrewSelectionEvidence,
    EvidenceComparability,
    ExecutiveConfirmation,
    MissionBrief,
    MissionBriefStatus,
    MissionCollaborationPlan,
    MissionCrewMember,
    MissionCrewRevision,
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
    TeamEffectivenessValidator,
    TeamHealthEvaluation,
    TeamHealthStatus,
    TeamSignal,
    TeamSignalDetector,
)
from mishkan.organization import load_canonical_organization
from mishkan.persistence import SchemaManager, SQLiteApplicationRepository


def _confirmation(identity_id: Literal["PM", "CTO"]) -> ExecutiveConfirmation:
    return ExecutiveConfirmation(
        identity_id=identity_id,
        disposition="confirmed",
        rationale=f"{identity_id} confirms the mission composition",
        evidence_references=(f"evidence:{identity_id.lower()}",),
        coverage=(
            ("product", "developer-experience", "composition")
            if identity_id == "PM"
            else ("technical", "platform", "security", "quality", "operability")
        ),
    )


def _selection(identity_id: str) -> CrewSelectionEvidence:
    return CrewSelectionEvidence(
        project_references=("project:api",),
        competence_references=(f"profile:{identity_id}:competence",),
        availability_references=(f"profile:{identity_id}:availability",),
        conflict_assessment="No responsibility or independence conflict",
        risk_coverage=("delivery correctness",),
        independence_references=(f"profile:{identity_id}:independence",),
    )


def _mission_context(
    tmp_path: Path,
) -> tuple[
    Path,
    SQLiteMissionRepository,
    MissionRecord,
    MissionBrief,
    MissionCrewRevision,
]:
    database = tmp_path / "mishkan.db"
    SchemaManager(database).initialize()
    missions = SQLiteMissionRepository(database)
    organization = load_canonical_organization()
    missions.record_organization(organization)
    mission = missions.create_mission(
        MissionRecord(
            origin=MissionOrigin(
                kind=MissionOriginKind.CEO,
                actor_id="ceo:y4nn777",
                objective="Improve account recovery without weakening security",
            ),
            organization_id=organization.organization_id,
            organization_version=organization.organization_version,
        )
    )
    brief = MissionBrief(
        mission_id=mission.mission_id,
        version=1,
        organization_id=mission.organization_id,
        organization_version=mission.organization_version,
        status=MissionBriefStatus.CONFIRMED,
        objective=mission.origin.objective,
        problem="Users cannot recover an account after losing a credential",
        desired_outcome="A verified recovery path with explicit residual risks",
        scope=("repository:api",),
        exclusions=("production deployment",),
        acceptance_criteria=("independent recovery evaluation passes",),
        constraints=("preserve existing accounts",),
        risks=("account takeover",),
        authority_scope=("repository:api",),
        proposed_crew=(
            "Backend_Service_Engineer",
            "Product_Functional_Evaluator",
            "Technical_Change_Reporter",
        ),
        evidence_requirements=("test report",),
        escalation_conditions=("unaccepted security tradeoff",),
        environment_intent=MissionEnvironmentIntent(
            known_locations=("repository:api",),
            target_platforms=("linux",),
            target_architectures=("x86_64",),
            required_evidence=("environment readiness",),
            environment_dependent=True,
        ),
        pm_confirmation=_confirmation("PM"),
        cto_confirmation=_confirmation("CTO"),
    )
    missions.record_brief(brief, expected_revision=mission.revision)
    assert brief.pm_confirmation is not None
    assert brief.cto_confirmation is not None
    crew = MissionCrewRevision(
        mission_id=mission.mission_id,
        version=1,
        organization_id=mission.organization_id,
        organization_version=mission.organization_version,
        brief_version=brief.version,
        mission_lead_id="Backend_Service_Engineer",
        members=(
            MissionCrewMember(
                identity_id="Backend_Service_Engineer",
                assignment_kind=CrewAssignmentKind.PRODUCTION,
                responsibility="Produce the accepted backend change",
                selection_evidence=_selection("Backend_Service_Engineer"),
            ),
            MissionCrewMember(
                identity_id="Product_Functional_Evaluator",
                assignment_kind=CrewAssignmentKind.EVALUATION,
                responsibility="Evaluate behavior independently",
                selection_evidence=_selection("Product_Functional_Evaluator"),
            ),
            MissionCrewMember(
                identity_id="Technical_Change_Reporter",
                assignment_kind=CrewAssignmentKind.REPORTING,
                responsibility="Report outcomes and limitations",
                selection_evidence=_selection("Technical_Change_Reporter"),
            ),
        ),
        pm_composition_confirmation_id=brief.pm_confirmation.confirmation_id,
        cto_coverage_confirmation_id=brief.cto_confirmation.confirmation_id,
        revision_reason="Smallest contextual crew with independent assurance",
    )
    current = missions.mission(str(mission.mission_id))
    missions.record_crew(crew, expected_revision=current.revision)
    return database, missions, mission, brief, crew


def _plan(
    mission: MissionRecord,
    brief: MissionBrief,
    crew: MissionCrewRevision,
    *,
    shape: ProductionShape = ProductionShape.SINGLE_OWNER,
) -> MissionCollaborationPlan:
    responsibilities = tuple(
        CollaborationResponsibility(
            identity_id=member.identity_id,
            responsibility=member.responsibility,
            decision_rights=(f"assignment:{member.assignment_kind.value}",),
        )
        for member in crew.members
    )
    interactions = (
        CollaborationEdge(
            source_identity="Backend_Service_Engineer",
            target_identity="Product_Functional_Evaluator",
            interaction=CollaborationInteraction.HANDOFF,
            purpose="Submit production evidence for independent evaluation",
            artifact_contracts=("artifact:implementation-result",),
        ),
        CollaborationEdge(
            source_identity="Product_Functional_Evaluator",
            target_identity="Technical_Change_Reporter",
            interaction=CollaborationInteraction.HANDOFF,
            purpose="Submit accepted evaluation evidence for reporting",
            artifact_contracts=("artifact:evaluation-result",),
        ),
    )
    return MissionCollaborationPlan(
        mission_id=mission.mission_id,
        version=1,
        brief_version=brief.version,
        crew_version=crew.version,
        production_shape=shape,
        production_owner_ids=("Backend_Service_Engineer",),
        collaboration_reasons=(
            ("Independent evaluation and reporting authorities are required",)
            if shape is not ProductionShape.SINGLE_OWNER
            else ()
        ),
        proposal_author_identity="PM",
        crewai_coordination_reference="coordination:crewai:fixture",
        alternatives=("single owner with independent downstream assurance",),
        shared_outcome=brief.desired_outcome,
        user_impact="Users regain access without an unreviewed security regression",
        responsibilities=responsibilities,
        interactions=interactions,
        context_references=("context-pack:mission:1",),
        artifact_references=("artifact:brief",),
        resource_budgets=(MissionResourceLimit(name="model_tokens", value=20_000, unit="token"),),
        communication_budget=MissionResourceLimit(
            name="coordination_messages", value=20, unit="message"
        ),
        escalation_conditions=("material scope or authority must change",),
        permitted_adaptations=(AdaptationKind.REPAIR_CONTEXT,),
        stop_conditions=("accepted outcome is impossible under current authority",),
        pm_confirmation_id=crew.pm_composition_confirmation_id,
        cto_confirmation_id=crew.cto_coverage_confirmation_id,
    )


def _evaluation(plan: MissionCollaborationPlan, *, cursor: int = 10) -> TeamHealthEvaluation:
    now = utc_now()
    assessments = tuple(
        TeamDimensionAssessment(
            dimension=dimension,
            status=TeamHealthStatus.HEALTHY,
            observations=(f"Observed evidence for {dimension.value}",),
            evidence_references=(f"event:{dimension.value}",),
        )
        for dimension in TeamDimension
    )
    return TeamHealthEvaluation(
        mission_id=plan.mission_id,
        collaboration_plan_version=plan.version,
        interval_started_at=now - timedelta(minutes=5),
        interval_ended_at=now,
        evidence_cursor=cursor,
        assessments=assessments,
        strengths=("Clear ownership",),
        risks=("No material risk observed",),
        limitations=("Fixture interval is intentionally short",),
        confidence_basis="All seven dimensions cite immutable evidence",
        status=TeamHealthStatus.HEALTHY,
        evaluator_identity="Product_Functional_Evaluator",
        evidence_references=("artifact:health-evidence",),
    )


def test_smallest_shape_keeps_one_production_owner_with_independent_assurance(
    tmp_path: Path,
) -> None:
    _, _, mission, brief, crew = _mission_context(tmp_path)
    plan = _plan(mission, brief, crew)

    TeamEffectivenessValidator.collaboration_plan(plan, brief=brief, crew=crew)

    assert plan.production_shape is ProductionShape.SINGLE_OWNER
    assert plan.production_owner_ids == ("Backend_Service_Engineer",)
    assert {item.identity_id for item in plan.responsibilities} == {
        "Backend_Service_Engineer",
        "Product_Functional_Evaluator",
        "Technical_Change_Reporter",
    }


def test_multi_agent_production_requires_a_reason_and_interaction_graph(tmp_path: Path) -> None:
    _, _, mission, brief, crew = _mission_context(tmp_path)
    payload = _plan(mission, brief, crew).model_dump(mode="python")
    payload.update(
        production_shape=ProductionShape.MULTI_PRODUCER,
        production_owner_ids=("Backend_Service_Engineer", "Product_Functional_Evaluator"),
        collaboration_reasons=(),
        interactions=(),
    )

    with pytest.raises(ValidationError, match="collaboration rationale"):
        MissionCollaborationPlan.model_validate(payload)


def test_health_is_multidimensional_independent_and_never_a_composite_score(
    tmp_path: Path,
) -> None:
    _, _, mission, brief, crew = _mission_context(tmp_path)
    plan = _plan(mission, brief, crew)
    evaluation = _evaluation(plan)

    TeamEffectivenessValidator.health_evaluation(evaluation, plan=plan)

    assert {item.dimension for item in evaluation.assessments} == set(TeamDimension)
    assert "score" not in evaluation.model_dump(mode="json")
    with pytest.raises(MishkanError) as conflict:
        TeamEffectivenessValidator.health_evaluation(
            evaluation.model_copy(update={"evaluator_identity": "Backend_Service_Engineer"}),
            plan=plan,
        )
    assert conflict.value.envelope.code is ErrorCode.ROLE_CONFLICT


def test_adaptation_boundary_rejects_silent_authority_expansion(tmp_path: Path) -> None:
    _, _, mission, brief, crew = _mission_context(tmp_path)
    plan = _plan(mission, brief, crew)
    bounded = CrewAdaptationDecision(
        mission_id=mission.mission_id,
        detected_condition="The evaluator lacks the latest accepted context",
        considered_alternatives=("Pause evaluation", "Repair the Context Pack"),
        expected_benefit="Restore common ground without changing authority",
        adaptation_kinds=(AdaptationKind.REPAIR_CONTEXT,),
        affected_task_ids=("evaluate-recovery",),
        authority_basis=("collaboration-plan:1",),
        disposition=AdaptationDisposition.BOUNDED,
        before_crew_version=1,
        after_crew_version=1,
        before_plan_version=1,
        after_plan_version=1,
        evidence_references=("signal:context-drift",),
        decided_by="PM",
    )

    TeamEffectivenessValidator.adaptation(bounded, plan=plan)

    with pytest.raises(ValidationError, match="material adaptation requires executive approval"):
        CrewAdaptationDecision.model_validate(
            {
                **bounded.model_dump(mode="python"),
                "decision_id": new_id(),
                "adaptation_kinds": (AdaptationKind.EXPAND_AUTHORITY,),
            }
        )


def test_retrospective_keeps_improvements_as_non_promoted_proposals(tmp_path: Path) -> None:
    _, _, mission, brief, crew = _mission_context(tmp_path)
    plan = _plan(mission, brief, crew)
    retrospective = MissionRetrospective(
        mission_id=mission.mission_id,
        collaboration_plan_version=plan.version,
        trigger="explicit_request",
        expected_outcomes=(brief.desired_outcome,),
        observed_outcomes=("Recovery implementation reached independent evaluation",),
        successful_patterns=("Explicit handoff contract",),
        failed_patterns=("Initial context omitted one risk",),
        contradictions=("Provider evidence differed from repository evidence",),
        rework=("Rebuilt the Context Pack once",),
        coordination_costs=("Two clarification messages",),
        proposals=(
            RetrospectiveProposal(
                destination="skill",
                summary="Propose a recovery threat-model checklist",
                evidence_references=("artifact:retrospective-evidence",),
            ),
        ),
        non_promoted_findings=("No individual competence change was authorized",),
        evidence_references=("artifact:retrospective-evidence",),
        authored_by="Technical_Change_Reporter",
    )

    assert retrospective.proposals[0].lifecycle_state == "proposed"
    assert retrospective.non_promoted_findings


def test_history_is_contextual_and_never_an_opaque_ranking(tmp_path: Path) -> None:
    _, _, mission, _, _ = _mission_context(tmp_path)
    now = utc_now()
    direct = CollaborationHistoryCitation(
        project_id="project:api",
        mission_id=mission.mission_id,
        task_ids=("implement-recovery",),
        evaluation_ids=(new_id(),),
        comparability=EvidenceComparability.DIRECT,
        rationale="The mission has the same product and repository boundary",
        evaluated_at=now - timedelta(days=2),
    )
    partial = direct.model_copy(
        update={
            "mission_id": new_id(),
            "comparability": EvidenceComparability.PARTIAL,
            "rationale": "Only the technical boundary is comparable",
        }
    )
    stale = direct.model_copy(
        update={
            "mission_id": new_id(),
            "evaluated_at": now - timedelta(days=120),
            "rationale": "The evidence predates the current architecture",
        }
    )

    selection = CollaborationHistorySelector.select(
        (direct, partial, stale), freshness_days=30, now=now
    )

    assert selection.preferred == (direct,)
    assert selection.tie_breakers == (partial,)
    assert selection.ignored == (stale,)
    assert any("current mission fit" in item for item in selection.rationale)
    assert "score" not in selection.model_dump(mode="json")


def _assignment(
    plan: MissionCollaborationPlan,
    task_id: str,
    *,
    owner: str = "Backend_Service_Engineer",
    expected_result: str = "A verified recovery implementation",
    crew_version: int | None = None,
) -> MissionTaskAssignment:
    return MissionTaskAssignment(
        mission_id=plan.mission_id,
        crew_version=crew_version or plan.crew_version,
        task_id=task_id,
        accountable_owner=owner,
        assignment_kind=CrewAssignmentKind.PRODUCTION,
        expected_result=expected_result,
        completion_criteria=("independent test passes",),
        authority_scope=("repository:api",),
        exact_tools=("file.patch",),
        path_scopes=("repository:api",),
        limits=(MissionResourceLimit(name="wall_time", value=1800, unit="seconds"),),
        required_evidence=("test report",),
    )


def test_deterministic_signals_detect_duplicate_context_drift_and_broken_handoff(
    tmp_path: Path,
) -> None:
    _, _, mission, brief, crew = _mission_context(tmp_path)
    plan = _plan(mission, brief, crew)
    first = _assignment(plan, "implement-recovery")
    duplicate = _assignment(plan, "implement-recovery-copy")
    stale = _assignment(
        plan,
        "update-client",
        expected_result="A compatible client update",
        crew_version=plan.crew_version + 1,
    )

    signals = TeamSignalDetector.detect(
        plan,
        (first, duplicate, stale),
        (),
        completed_task_ids=frozenset({first.task_id}),
        workload_concentration_threshold=0.8,
        observed_at=utc_now(),
    )

    by_kind = {item.kind: item for item in signals}
    assert {"context_drift", "duplicate_work", "missing_handoff", "workload_concentration"} <= (
        set(by_kind)
    )
    assert set(by_kind["duplicate_work"].task_ids) == {
        "implement-recovery",
        "implement-recovery-copy",
    }
    assert by_kind["missing_handoff"].evidence_references

    recorded_handoff = MissionHandoff(
        mission_id=mission.mission_id,
        collaboration_plan_version=plan.version,
        from_identity="Backend_Service_Engineer",
        to_identity="Product_Functional_Evaluator",
        task_id=first.task_id,
        result_references=("result:implement-recovery",),
        artifact_references=("artifact:implementation",),
        decisions=(),
        changed_assumptions=(),
        uncertainty=("Benchmark pending",),
        failures=(),
        unresolved_questions=(),
        required_next_action="Perform independent evaluation",
        acceptance_conditions=("Recovery tests pass",),
    )
    repaired = TeamSignalDetector.detect(
        plan,
        (first,),
        (recorded_handoff,),
        completed_task_ids=frozenset({first.task_id}),
        observed_at=utc_now(),
    )
    assert all(item.kind != "missing_handoff" for item in repaired)


def test_event_signals_derive_flow_rejection_and_rework_without_model_inference(
    tmp_path: Path,
) -> None:
    _, _, mission, brief, crew = _mission_context(tmp_path)
    plan = _plan(mission, brief, crew)
    started = utc_now() - timedelta(minutes=10)

    def event(
        cursor: int,
        event_type: str,
        occurred_at: datetime,
        payload: dict[str, object],
    ) -> EventEnvelope:
        return EventEnvelope(
            event_id=new_id(),
            cursor=cursor,
            event_type=event_type,
            source="test",
            entity_type="run",
            entity_id="run-1",
            run_id="run-1",
            occurred_at=occurred_at,
            sensitivity="internal",
            payload=payload,
        )

    events = (
        event(1, "run.queued", started, {}),
        event(2, "run.running", started + timedelta(minutes=3), {}),
        event(
            3,
            "task.claimed",
            started + timedelta(minutes=4),
            {"task_id": "task-1", "attempt": 2},
        ),
        event(4, "task.review_rejected", started + timedelta(minutes=5), {"task_id": "task-1"}),
        event(5, "task.review_rejected", started + timedelta(minutes=6), {"task_id": "task-1"}),
    )
    signals = TeamSignalDetector.detect_events(
        plan,
        events,
        {"task-1": "implement-recovery"},
        signal_thresholds={
            "blocked_time_seconds": 60,
            "queue_time_seconds": 60,
            "repeated_rejections": 2,
            "duplicate_work": 1,
            "missing_handoffs": 1,
            "workload_share": 0.75,
        },
    )

    by_kind = {item.kind: item for item in signals}
    assert set(by_kind) == {"queue_time", "rejection", "rework"}
    assert by_kind["queue_time"].value == 180
    assert by_kind["rejection"].task_ids == ("implement-recovery",)
    assert all(
        reference.startswith("event:") for reference in by_kind["rework"].evidence_references
    )


def test_repository_persists_immutable_lineage_signals_and_handoffs(tmp_path: Path) -> None:
    database, missions, mission, brief, crew = _mission_context(tmp_path)
    repository = SQLiteTeamEffectivenessRepository(database)
    plan = _plan(mission, brief, crew)
    evaluation = _evaluation(plan)

    assert repository.record_collaboration_plan(plan) == plan
    assert repository.record_collaboration_plan(plan) == plan
    assert repository.record_health(evaluation) == evaluation
    assignment = _assignment(plan, "implement-recovery")
    missions.record_assignment(assignment)
    adaptation = CrewAdaptationDecision(
        mission_id=mission.mission_id,
        detected_condition="The evaluator lacks one accepted context reference",
        considered_alternatives=("Pause evaluation", "Repair the Context Pack"),
        expected_benefit="Restore common ground without changing authority",
        adaptation_kinds=(AdaptationKind.REPAIR_CONTEXT,),
        affected_task_ids=(assignment.task_id,),
        affected_assignments=(assignment.assignment_id,),
        authority_basis=("collaboration-plan:1",),
        disposition=AdaptationDisposition.BOUNDED,
        before_crew_version=1,
        after_crew_version=1,
        before_plan_version=1,
        after_plan_version=1,
        evidence_references=("signal:context-drift",),
        decided_by="PM",
    )
    assert repository.record_adaptation(adaptation) == adaptation
    signal = TeamSignal(
        mission_id=mission.mission_id,
        collaboration_plan_version=plan.version,
        kind="context_drift",
        task_ids=("implement-recovery",),
        value=1,
        unit="occurrence",
        evidence_references=("event:context-drift",),
    )
    handoff = MissionHandoff(
        mission_id=mission.mission_id,
        collaboration_plan_version=plan.version,
        from_identity="Backend_Service_Engineer",
        to_identity="Product_Functional_Evaluator",
        task_id="implement-recovery",
        result_references=("result:implement-recovery",),
        artifact_references=("artifact:implementation-result",),
        decisions=("Preserve existing token semantics",),
        changed_assumptions=("Recovery token storage already exists",),
        uncertainty=("Provider latency is not benchmarked",),
        failures=(),
        unresolved_questions=("Does mobile require a separate flow?",),
        required_next_action="Evaluate recovery behavior and security boundaries",
        acceptance_conditions=("Independent recovery tests pass",),
    )
    assert repository.record_signal(signal) == signal
    assert repository.record_handoff(handoff) == handoff

    assert repository.collaboration_plan(str(mission.mission_id)) == plan
    assert repository.health(str(mission.mission_id)) == (evaluation,)
    assert repository.signals(str(mission.mission_id)) == (signal,)
    assert repository.handoffs(str(mission.mission_id)) == (handoff,)
    assert repository.adaptations(str(mission.mission_id)) == (adaptation,)
    projection = repository.snapshot()
    assert projection["attention_required"] == 0
    event_types = [
        event.event_type
        for event in SQLiteApplicationRepository(database)
        .events(entity_id=str(mission.mission_id))
        .events
    ]
    assert "mission.collaboration_plan_recorded" in event_types
    assert "mission.team_health_evaluated" in event_types
    assert "mission.team_signal_recorded" in event_types
    assert "mission.handoff_recorded" in event_types


def test_health_cursor_and_record_identity_are_monotone(tmp_path: Path) -> None:
    database, _, mission, brief, crew = _mission_context(tmp_path)
    repository = SQLiteTeamEffectivenessRepository(database)
    plan = _plan(mission, brief, crew)
    repository.record_collaboration_plan(plan)
    evaluation = _evaluation(plan, cursor=10)
    repository.record_health(evaluation)

    with pytest.raises(MishkanError) as stale:
        repository.record_health(
            _evaluation(plan, cursor=9).model_copy(update={"evaluation_id": new_id()})
        )
    assert stale.value.envelope.code is ErrorCode.REVISION_MISMATCH

    changed = plan.model_copy(update={"shared_outcome": "Different immutable content"})
    with pytest.raises(MishkanError) as duplicate:
        repository.record_collaboration_plan(changed)
    assert duplicate.value.envelope.code is ErrorCode.DUPLICATE_RESULT
