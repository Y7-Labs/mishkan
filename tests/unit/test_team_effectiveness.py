from datetime import datetime, timedelta
from pathlib import Path
from typing import Literal

import pytest
from pydantic import ValidationError

from mishkan.conversations import (
    ChannelClass,
    ConversationChannel,
    DecisionAlternative,
    DecisionContext,
    DecisionContextElement,
    DecisionCriterion,
    DecisionCriterionAssessment,
    DecisionEvidenceClaim,
    DecisionEvidenceClass,
    DecisionRecommendation,
    DecisionStatus,
    DecisionValidation,
    DecisionValidationStatus,
    MissionDecision,
    SQLiteConversationRepository,
)
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


def _record_material_approval(
    database: Path,
    mission: MissionRecord,
    *,
    decided_by: str,
) -> MissionDecision:
    conversations = SQLiteConversationRepository(database)
    channel = conversations.create_channel(
        ConversationChannel(
            channel_class=ChannelClass.MISSION,
            title="Mission crew adaptation",
            participants=("CEO", "PM", "CTO", "Backend_Service_Engineer"),
            mission_id=mission.mission_id,
            created_by="PM",
        )
    )
    evidence_reference = "evidence:team-health-evaluation"
    context = DecisionContext(
        question="Should the mission collaboration authority change?",
        objective_reference="objective:account-recovery",
        effective_policy_reference="policy:mission",
        requirements=(
            DecisionContextElement(
                statement="Preserve explicit executive authority",
                provenance_reference="requirement:MSN-022",
            ),
        ),
        repository_evidence=(
            DecisionContextElement(
                statement="Current team evidence is attributable",
                provenance_reference=evidence_reference,
            ),
        ),
        constraints=(
            DecisionContextElement(
                statement="Do not expand authority silently",
                provenance_reference="constraint:governance",
            ),
        ),
        declared_preferences=(
            DecisionContextElement(
                statement="Repair the smallest affected scope",
                provenance_reference="preference:bounded-change",
            ),
        ),
        risks=(
            DecisionContextElement(
                statement="A material change could weaken independence",
                provenance_reference="risk:independence",
            ),
        ),
        material_unknowns=(
            DecisionContextElement(
                statement="The benefit after adaptation remains forecast",
                provenance_reference="unknown:benefit",
            ),
        ),
    )
    criterion = DecisionCriterion(
        criterion_id="authority",
        description="Preserve explicit executive authority",
        provenance_references=("requirement:MSN-022",),
    )

    def alternative(option_id: str, description: str) -> DecisionAlternative:
        return DecisionAlternative(
            option_id=option_id,
            description=description,
            credible=True,
            assessments=(
                DecisionCriterionAssessment(
                    criterion_id=criterion.criterion_id,
                    assessment=f"{description} preserves recorded authority",
                    evidence_references=(evidence_reference,),
                ),
            ),
        )

    staged = MissionDecision(
        schema_version="1.1",
        mission_id=mission.mission_id,
        conversation_id=channel.conversation_id,
        actor_id="PM",
        producer_identity="PM",
        subject="Material crew adaptation",
        disposition=DecisionStatus.STAGED.value,
        decision_status=DecisionStatus.STAGED,
        reason="The change requires an independently validated executive decision",
        scope=(f"mission:{mission.mission_id}",),
        evidence_references=(evidence_reference,),
        authority_reference="authority:mission-executive",
        changes_durable_authority=True,
        context=context,
        evidence=(
            DecisionEvidenceClaim(
                claim="The health evaluation identifies a material collaboration risk",
                classification=DecisionEvidenceClass.VERIFIED,
                source_reference=evidence_reference,
            ),
        ),
        criteria=(criterion,),
        alternatives=(
            alternative("retain-plan", "Retain the accepted collaboration plan"),
            alternative("change-scope", "Authorize the bounded material scope change"),
        ),
        alternatives_search="Compared retaining the plan with the requested scope change",
        recommendation=DecisionRecommendation(
            recommended_option_id="change-scope",
            rationale="The evidence supports a recorded material scope change",
            tradeoffs=("The collaboration plan must be reviewed again",),
            risks=("The expected benefit may not materialize",),
            confidence=0.8,
            confidence_basis="Independent team evidence is available",
            unresolved_questions=("Will the revised scope reduce rework?",),
            expected_consequences=("Only approved work receives new scope",),
            reversal_or_migration=("Record a superseding plan decision",),
        ),
        validation=DecisionValidation(
            validation_type="independent evidence review",
            planned_evidence=("Review team-health and assignment evidence",),
            status=DecisionValidationStatus.PENDING,
            findings=("Independent validation is pending",),
        ),
    )
    conversations.record_decision(staged)
    accepted = staged.model_copy(
        update={
            "decision_id": new_id(),
            "actor_id": decided_by,
            "deciding_identity": decided_by,
            "disposition": DecisionStatus.ACCEPTED.value,
            "decision_status": DecisionStatus.ACCEPTED,
            "supersedes_decision_id": staged.decision_id,
            "reason": "Independent evidence supports the governed material adaptation",
            "validation": staged.validation.model_copy(
                update={
                    "status": DecisionValidationStatus.PASSED,
                    "evaluator_identity": "Product_Functional_Evaluator",
                    "evidence_references": (evidence_reference,),
                    "findings": ("The adaptation remains inside the recorded mission scope",),
                }
            ),
        }
    )
    accepted = MissionDecision.model_validate(accepted.model_dump(mode="json"))
    conversations.record_decision(accepted)
    return accepted


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


def test_composition_fixtures_make_multi_agent_value_and_cost_explicit(tmp_path: Path) -> None:
    _, _, mission, brief, crew = _mission_context(tmp_path)
    single_owner = _plan(mission, brief, crew)
    base = single_owner.model_dump(mode="python")
    specialist = CollaborationResponsibility(
        identity_id="Integration_SDK_Engineer",
        responsibility="Own the client compatibility work",
        decision_rights=("assignment:production",),
    )
    consultative = MissionCollaborationPlan.model_validate(
        {
            **base,
            "plan_id": new_id(),
            "production_shape": ProductionShape.CONSULTATIVE,
            "collaboration_reasons": (
                "Distinct SDK expertise reduces compatibility risk without adding another owner",
            ),
            "responsibilities": (*single_owner.responsibilities, specialist),
            "interactions": (
                *single_owner.interactions,
                CollaborationEdge(
                    source_identity="Backend_Service_Engineer",
                    target_identity="Integration_SDK_Engineer",
                    interaction=CollaborationInteraction.CONSULT,
                    purpose="Challenge the recovery API compatibility assumptions",
                    artifact_contracts=("artifact:api-contract",),
                ),
            ),
            "communication_budget": MissionResourceLimit(
                name="coordination_messages", value=24, unit="message"
            ),
        }
    )
    multi_producer = MissionCollaborationPlan.model_validate(
        {
            **consultative.model_dump(mode="python"),
            "plan_id": new_id(),
            "production_shape": ProductionShape.MULTI_PRODUCER,
            "production_owner_ids": (
                "Backend_Service_Engineer",
                "Integration_SDK_Engineer",
            ),
            "collaboration_reasons": (
                "Independent backend and SDK work is parallelizable",
                "Distinct ownership covers both required product surfaces",
            ),
            "interactions": (
                *consultative.interactions,
                CollaborationEdge(
                    source_identity="Backend_Service_Engineer",
                    target_identity="Integration_SDK_Engineer",
                    interaction=CollaborationInteraction.COLLABORATE,
                    purpose="Coordinate the shared versioned API contract",
                    artifact_contracts=("artifact:api-contract",),
                ),
            ),
            "communication_budget": MissionResourceLimit(
                name="coordination_messages", value=32, unit="message"
            ),
        }
    )

    assert single_owner.production_owner_ids == ("Backend_Service_Engineer",)
    assert consultative.production_owner_ids == single_owner.production_owner_ids
    assert any(
        edge.interaction is CollaborationInteraction.CONSULT for edge in consultative.interactions
    )
    assert len(multi_producer.production_owner_ids) == 2
    assert multi_producer.collaboration_reasons
    assert multi_producer.communication_budget.value > single_owner.communication_budget.value


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


def test_execution_location_does_not_change_authoritative_team_evidence(
    tmp_path: Path,
) -> None:
    _, _, mission, brief, crew = _mission_context(tmp_path)
    plan = _plan(mission, brief, crew)
    observed_at = utc_now()
    event_id = new_id()

    def claimed(execution_location: str) -> EventEnvelope:
        return EventEnvelope(
            event_id=event_id,
            cursor=1,
            event_type="task.claimed",
            source="mishkand",
            entity_type="run",
            entity_id="run-1",
            run_id="run-1",
            task_id="task-1",
            occurred_at=observed_at,
            sensitivity="internal",
            payload={
                "task_id": "task-1",
                "attempt": 2,
                "execution_location": execution_location,
            },
        )

    thresholds = {
        "blocked_time_seconds": 60,
        "queue_time_seconds": 60,
        "repeated_rejections": 2,
        "duplicate_work": 1,
        "missing_handoffs": 1,
        "workload_share": 0.75,
    }
    local = TeamSignalDetector.detect_events(
        plan,
        (claimed("daemon:local"),),
        {"task-1": "implement-recovery"},
        signal_thresholds=thresholds,
    )
    distributed = TeamSignalDetector.detect_events(
        plan,
        (claimed("worker:remote-1"),),
        {"task-1": "implement-recovery"},
        signal_thresholds=thresholds,
    )

    assert local == distributed
    assert local[0].kind == "rework"
    assert local[0].evidence_references == (f"event:{event_id}",)


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
    retrospective = MissionRetrospective(
        mission_id=mission.mission_id,
        collaboration_plan_version=plan.version,
        trigger="explicit_request",
        expected_outcomes=(brief.desired_outcome,),
        observed_outcomes=("The accepted collaboration evidence is preserved",),
        successful_patterns=("Explicit responsibility and handoff contracts",),
        failed_patterns=(),
        contradictions=(),
        rework=(),
        coordination_costs=("One independent milestone evaluation",),
        proposals=(
            RetrospectiveProposal(
                destination="mission_template",
                summary="Propose the verified handoff pattern for later review",
                evidence_references=("artifact:retrospective-evidence",),
            ),
        ),
        non_promoted_findings=("No proposal was activated by this retrospective",),
        evidence_references=("artifact:retrospective-evidence",),
        authored_by="Technical_Change_Reporter",
    )
    assert repository.record_signal(signal) == signal
    assert repository.record_handoff(handoff) == handoff
    assert repository.record_retrospective(retrospective) == retrospective

    assert repository.collaboration_plan(str(mission.mission_id)) == plan
    assert repository.health(str(mission.mission_id)) == (evaluation,)
    assert repository.signals(str(mission.mission_id)) == (signal,)
    assert repository.handoffs(str(mission.mission_id)) == (handoff,)
    assert repository.adaptations(str(mission.mission_id)) == (adaptation,)
    assert repository.retrospectives(str(mission.mission_id)) == (retrospective,)
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
    assert "mission.retrospective_recorded" in event_types


def test_material_adaptation_requires_an_accepted_executive_decision(tmp_path: Path) -> None:
    database, missions, mission, brief, crew = _mission_context(tmp_path)
    repository = SQLiteTeamEffectivenessRepository(database)
    plan = _plan(mission, brief, crew)
    repository.record_collaboration_plan(plan)
    assignment = _assignment(plan, "material-scope-change")
    missions.record_assignment(assignment)

    def adaptation(reference: str) -> CrewAdaptationDecision:
        return CrewAdaptationDecision(
            mission_id=mission.mission_id,
            detected_condition="The accepted task scope cannot cover the verified change",
            considered_alternatives=("Retain scope", "Authorize a material scope change"),
            expected_benefit="Align task authority with the accepted mission outcome",
            adaptation_kinds=(AdaptationKind.CHANGE_MATERIAL_SCOPE,),
            affected_task_ids=(assignment.task_id,),
            affected_assignments=(assignment.assignment_id,),
            authority_basis=("collaboration-plan:1",),
            disposition=AdaptationDisposition.APPROVAL_REQUIRED,
            before_crew_version=1,
            after_crew_version=1,
            before_plan_version=1,
            after_plan_version=1,
            approval_references=(reference,),
            evidence_references=("team-health:evaluation-1",),
            decided_by="PM",
        )

    non_executive = _record_material_approval(
        database,
        mission,
        decided_by="Backend_Service_Engineer",
    )
    with pytest.raises(MishkanError) as unauthorized:
        repository.record_adaptation(adaptation(f"decision:{non_executive.decision_id}"))
    assert unauthorized.value.envelope.code is ErrorCode.AUTHORITY_NOT_GRANTED

    executive = _record_material_approval(database, mission, decided_by="CTO")
    governed = adaptation(f"decision:{executive.decision_id}").model_copy(
        update={"decision_id": new_id()}
    )
    assert repository.record_adaptation(governed) == governed


def test_collaboration_history_rejects_unproven_lineage(tmp_path: Path) -> None:
    database, _, mission, brief, crew = _mission_context(tmp_path)
    repository = SQLiteTeamEffectivenessRepository(database)
    citation = CollaborationHistoryCitation(
        project_id="project:other",
        mission_id=new_id(),
        task_ids=("prior-task",),
        evaluation_ids=(new_id(),),
        comparability=EvidenceComparability.DIRECT,
        rationale="Claimed similar mission without persisted evidence",
        evaluated_at=utc_now(),
    )
    plan = _plan(mission, brief, crew).model_copy(update={"history_citations": (citation,)})

    with pytest.raises(MishkanError) as invalid:
        repository.record_collaboration_plan(plan)
    assert invalid.value.envelope.code is ErrorCode.DECISION_VALIDATION


def test_retrospective_author_must_be_independent_reporting_or_audit(
    tmp_path: Path,
) -> None:
    database, _, mission, brief, crew = _mission_context(tmp_path)
    repository = SQLiteTeamEffectivenessRepository(database)
    plan = _plan(mission, brief, crew)
    repository.record_collaboration_plan(plan)
    record = MissionRetrospective(
        mission_id=mission.mission_id,
        collaboration_plan_version=plan.version,
        trigger="major_failure",
        expected_outcomes=(brief.desired_outcome,),
        observed_outcomes=("The mission encountered a major failure",),
        successful_patterns=(),
        failed_patterns=("The production path failed",),
        contradictions=(),
        rework=("A replacement implementation is required",),
        coordination_costs=("One failed production attempt",),
        proposals=(),
        non_promoted_findings=("Failure remains evidence, not an automatic penalty",),
        evidence_references=("artifact:failure-evidence",),
        authored_by="Backend_Service_Engineer",
    )

    with pytest.raises(MishkanError) as conflict:
        repository.record_retrospective(record)
    assert conflict.value.envelope.code is ErrorCode.ROLE_CONFLICT


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
