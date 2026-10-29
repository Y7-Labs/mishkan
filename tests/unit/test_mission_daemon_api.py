from datetime import datetime
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from support.capabilities import resolved_tool_lineage

from mishkan.application import ApplicationCommand
from mishkan.config.loader import ConfigLoader
from mishkan.config.models import MishkanConfig, ProjectConfig
from mishkan.config.presets import preset_text
from mishkan.conversations import (
    ChannelClass,
    ConversationChannel,
    ConversationMessage,
    EscalationOption,
    ExecutiveRecommendation,
    InterventionKind,
    InterventionResultState,
    InterventionTargetKind,
    MissionEscalation,
    MissionIntervention,
)
from mishkan.crewai.mission_environment import (
    CrewAIMissionEnvironmentPlanningRunner,
    MissionEnvironmentDecisionOutput,
    MissionEnvironmentPlanningOutput,
)
from mishkan.crewai.mission_governance import (
    CrewAICollaborationProposal,
    CrewAIGovernanceLineage,
    MissionGovernanceEvidence,
    MissionGovernanceRequest,
    MissionGovernanceResult,
)
from mishkan.daemon import DaemonBootstrap, create_app
from mishkan.daemon.auth import TokenFile
from mishkan.domain.errors import ErrorCode
from mishkan.environment import (
    EnvironmentBindingState,
    EnvironmentObservation,
    EnvironmentObservationRequest,
    EnvironmentOutcome,
)
from mishkan.missions import (
    AdaptationKind,
    CollaborationEdge,
    CollaborationInteraction,
    CollaborationResponsibility,
    CrewAssignmentKind,
    CrewSelectionEvidence,
    ExecutiveConfirmation,
    MissionBrief,
    MissionBriefStatus,
    MissionCollaborationPlan,
    MissionCrewMember,
    MissionCrewRevision,
    MissionEnvironmentAlternative,
    MissionEnvironmentContextRequest,
    MissionEnvironmentIntent,
    MissionEnvironmentPlan,
    MissionEnvironmentPlanAcceptance,
    MissionEnvironmentPlanningRequest,
    MissionOrigin,
    MissionOriginKind,
    MissionRecord,
    MissionResourceLimit,
    MissionRunAcceptance,
    MissionRunBinding,
    MissionRunReport,
    MissionRunReportTask,
    MissionState,
    MissionTaskAssignment,
    MissionTaskClaimRequest,
    MissionTransition,
    ProductionShape,
    SQLiteMissionRepository,
    TeamDimension,
    TeamDimensionAssessment,
    TeamHealthEvaluation,
    TeamHealthEvaluationRequest,
    TeamHealthStatus,
    TeamSignal,
)
from mishkan.organization import load_canonical_organization
from mishkan.persistence import LocalRunRepository
from mishkan.planning import AcceptedPlan, PlanExecutionContext, PlanOrganizationBinding, PlanTask
from mishkan.planning.models import InitializationResult, ReviewDecision
from mishkan.repository.models import DiscoverySnapshot, RepositoryBinding


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _config(tmp_path: Path) -> MishkanConfig:
    source = tmp_path / "config.yaml"
    source.write_text(preset_text("local"), encoding="utf-8")
    loaded = ConfigLoader().load([source]).value
    return loaded.model_copy(update={"project": ProjectConfig(workspace=tmp_path)})


@pytest.mark.anyio
async def test_daemon_exposes_optional_template_guidance_and_allows_no_match(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    paths = DaemonBootstrap().setup(config)
    token = TokenFile(paths.token_file).read()
    headers = {"Authorization": f"Bearer {token.token}"}
    transport = httpx.ASGITransport(app=create_app(config))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        matched = await client.get(
            "/v1/mission-templates", headers=headers, params=[("signal", "incident")]
        )
        unmatched = await client.get(
            "/v1/mission-templates", headers=headers, params=[("signal", "unknown")]
        )

    assert [item["template_id"] for item in matched.json()] == ["incident-response"]
    assert unmatched.json() == []


def _confirmation(identity_id: str) -> ExecutiveConfirmation:
    return ExecutiveConfirmation(
        identity_id=identity_id,
        disposition="confirmed",
        rationale=f"{identity_id} confirms the mission coverage",
        evidence_references=(f"evidence:{identity_id.lower()}",),
        coverage=(
            ("product", "developer-experience", "composition")
            if identity_id == "PM"
            else ("technical", "platform", "security", "quality", "operability")
        ),
    )


def _selection(identity_id: str) -> CrewSelectionEvidence:
    return CrewSelectionEvidence(
        project_references=("repository:api",),
        competence_references=(f"profile:{identity_id}:competence",),
        availability_references=(f"profile:{identity_id}:availability",),
        conflict_assessment="No production, evaluation, reporting, or ownership conflict",
        risk_coverage=("recovery correctness",),
        independence_references=(f"profile:{identity_id}:independence",),
    )


def _brief(mission: MissionRecord) -> MissionBrief:
    return MissionBrief(
        mission_id=mission.mission_id,
        version=1,
        organization_id=mission.organization_id,
        organization_version=mission.organization_version,
        status=MissionBriefStatus.CONFIRMED,
        objective=mission.origin.objective,
        problem="Users cannot recover access after losing a credential",
        desired_outcome="A verified recovery flow with explicit residual risk",
        scope=("repository:api",),
        exclusions=("production deployment",),
        acceptance_criteria=("independent recovery test passes",),
        constraints=("preserve existing accounts",),
        risks=("account takeover",),
        authority_scope=("repository:api",),
        proposed_crew=(
            "Backend_Service_Engineer",
            "Product_Functional_Evaluator",
            "Technical_Change_Reporter",
        ),
        evidence_requirements=("test report", "security review"),
        escalation_conditions=("security tradeoff exceeds accepted risk",),
        environment_intent=MissionEnvironmentIntent(
            required_evidence=("environment readiness",),
            environment_dependent=True,
        ),
        pm_confirmation=_confirmation("PM"),
        cto_confirmation=_confirmation("CTO"),
    )


def _crew(brief: MissionBrief) -> MissionCrewRevision:
    assert brief.pm_confirmation is not None
    assert brief.cto_confirmation is not None
    members = tuple(
        MissionCrewMember(
            identity_id=identity_id,
            assignment_kind=assignment,
            responsibility=responsibility,
            selection_evidence=_selection(identity_id),
        )
        for identity_id, assignment, responsibility in (
            (
                "Backend_Service_Engineer",
                CrewAssignmentKind.PRODUCTION,
                "Implement the accepted recovery scope",
            ),
            (
                "Product_Functional_Evaluator",
                CrewAssignmentKind.EVALUATION,
                "Evaluate recovery behavior independently",
            ),
            (
                "Technical_Change_Reporter",
                CrewAssignmentKind.REPORTING,
                "Report verified outcomes and residual risks",
            ),
        )
    )
    return MissionCrewRevision(
        mission_id=brief.mission_id,
        version=1,
        organization_id=brief.organization_id,
        organization_version=brief.organization_version,
        brief_version=brief.version,
        mission_lead_id="Backend_Service_Engineer",
        members=members,
        pm_composition_confirmation_id=brief.pm_confirmation.confirmation_id,
        cto_coverage_confirmation_id=brief.cto_confirmation.confirmation_id,
        revision_reason="Initial evidence-based composition",
    )


class _MissionGovernanceRunner:
    def propose(
        self, mission: MissionRecord, _evidence: tuple[MissionGovernanceEvidence, ...]
    ) -> MissionGovernanceResult:
        brief = _brief(mission)
        return MissionGovernanceResult(
            mission=mission,
            brief=brief,
            crew=_crew(brief),
            collaboration=CrewAICollaborationProposal(
                production_shape=ProductionShape.SINGLE_OWNER,
                production_owner_ids=("Backend_Service_Engineer",),
                alternatives=("single owner with independent assurance",),
                interactions=(
                    CollaborationEdge(
                        source_identity="Backend_Service_Engineer",
                        target_identity="Product_Functional_Evaluator",
                        interaction=CollaborationInteraction.HANDOFF,
                        purpose="Submit production evidence for evaluation",
                    ),
                ),
            ),
            lineage=CrewAIGovernanceLineage(
                pm_model_route="fixture-pm",
                cto_model_route="fixture-cto",
                pm_output_fingerprint="a" * 64,
                cto_output_fingerprint="b" * 64,
            ),
        )


class _MissionEnvironmentRunner:
    def propose(
        self,
        request: MissionEnvironmentPlanningRequest,
        *,
        mission: MissionRecord,
        brief: MissionBrief,
        crew: MissionCrewRevision,
        observations: tuple[EnvironmentObservation, ...],
        plan_version: int,
    ) -> MissionEnvironmentPlan:
        del mission, brief, crew, observations
        context = request.contexts[0]
        return CrewAIMissionEnvironmentPlanningRunner.compile(
            request,
            MissionEnvironmentPlanningOutput(
                decisions=(
                    MissionEnvironmentDecisionOutput(
                        context_id=context.context_id,
                        selected_alternative_id="unresolved",
                        requested_outcome="unresolved",
                        rationale="Current evidence cannot prove a compatible environment",
                        constraints=("do not fabricate environment readiness",),
                        declared_effects=(),
                        verification_checks=("re-observe compatible engine",),
                        cleanup_criteria=("no resources were created",),
                        alternatives_considered=("unresolved",),
                        unknowns=("compatible engine is not yet evidenced",),
                        evidence_references=("observation:environment",),
                    ),
                )
            ),
            plan_version=plan_version,
            model_route="planning",
        )


class _TeamHealthEvaluator:
    def evaluate(
        self,
        plan: MissionCollaborationPlan,
        _signals: tuple[TeamSignal, ...],
        *,
        evaluator_identity: str,
        interval_started_at: datetime,
        interval_ended_at: datetime,
        evidence_cursor: int,
        evidence_references: tuple[str, ...],
    ) -> TeamHealthEvaluation:
        return TeamHealthEvaluation(
            mission_id=plan.mission_id,
            collaboration_plan_version=plan.version,
            interval_started_at=interval_started_at,
            interval_ended_at=interval_ended_at,
            evidence_cursor=evidence_cursor,
            assessments=tuple(
                TeamDimensionAssessment(
                    dimension=dimension,
                    status=TeamHealthStatus.HEALTHY,
                    observations=(f"Fixture evidence covers {dimension.value}",),
                    evidence_references=evidence_references,
                )
                for dimension in TeamDimension
            ),
            strengths=("Clear outcome and ownership",),
            risks=(),
            limitations=("Deterministic daemon fixture",),
            confidence_basis="Every dimension cites the request evidence",
            status=TeamHealthStatus.HEALTHY,
            evaluator_identity=evaluator_identity,
            evidence_references=evidence_references,
        )


@pytest.mark.anyio
async def test_collaboration_plan_uses_command_query_snapshot_and_event_surfaces(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    paths = DaemonBootstrap().setup(config)
    token = TokenFile(paths.token_file).read()
    headers = {"Authorization": f"Bearer {token.token}"}
    organization = load_canonical_organization()
    mission = MissionRecord(
        origin=MissionOrigin(
            kind=MissionOriginKind.CEO,
            actor_id="ceo:y4nn777",
            objective="Add durable account recovery",
        ),
        organization_id=organization.organization_id,
        organization_version=organization.organization_version,
    )
    brief = _brief(mission)
    crew = _crew(brief)
    plan = MissionCollaborationPlan(
        mission_id=mission.mission_id,
        version=1,
        brief_version=brief.version,
        crew_version=crew.version,
        production_shape=ProductionShape.SINGLE_OWNER,
        production_owner_ids=("Backend_Service_Engineer",),
        proposal_author_identity="PM",
        crewai_coordination_reference="coordination:crewai:daemon-fixture",
        alternatives=("single owner with downstream independent assurance",),
        shared_outcome=brief.desired_outcome,
        user_impact="Users can recover access without an unreviewed security regression",
        responsibilities=tuple(
            CollaborationResponsibility(
                identity_id=member.identity_id,
                responsibility=member.responsibility,
                decision_rights=(f"assignment:{member.assignment_kind.value}",),
            )
            for member in crew.members
        ),
        interactions=(
            CollaborationEdge(
                source_identity="Backend_Service_Engineer",
                target_identity="Product_Functional_Evaluator",
                interaction=CollaborationInteraction.HANDOFF,
                purpose="Provide production evidence for independent evaluation",
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
        pm_confirmation_id=crew.pm_composition_confirmation_id,
        cto_confirmation_id=crew.cto_coverage_confirmation_id,
    )
    health_request = TeamHealthEvaluationRequest(
        mission_id=mission.mission_id,
        collaboration_plan_version=plan.version,
        interval_started_at=mission.created_at,
        interval_ended_at=brief.created_at,
        evidence_cursor=4,
        evaluator_identity="Product_Functional_Evaluator",
        evidence_references=("artifact:team-evidence",),
    )
    commands = (
        ApplicationCommand(
            command_type="mission.create",
            actor_id=token.principal_id,
            target_type="mission",
            target_id=str(mission.mission_id),
            expected_revision=0,
            payload={"record": mission.model_dump(mode="json")},
        ),
        ApplicationCommand(
            command_type="mission.brief.record",
            actor_id=token.principal_id,
            target_type="mission",
            target_id=str(mission.mission_id),
            expected_revision=1,
            payload={"brief": brief.model_dump(mode="json")},
        ),
        ApplicationCommand(
            command_type="mission.crew.record",
            actor_id=token.principal_id,
            target_type="mission",
            target_id=str(mission.mission_id),
            expected_revision=2,
            payload={"crew": crew.model_dump(mode="json")},
        ),
        ApplicationCommand(
            command_type="mission.collaboration.record",
            actor_id=token.principal_id,
            target_type="mission_collaboration",
            target_id=str(plan.plan_id),
            payload={"plan": plan.model_dump(mode="json")},
        ),
        ApplicationCommand(
            command_type="mission.health.evaluate",
            actor_id=token.principal_id,
            target_type="team_health_evaluation",
            target_id=str(health_request.request_id),
            payload={"request": health_request.model_dump(mode="json")},
        ),
    )

    transport = httpx.ASGITransport(
        app=create_app(config, team_health_evaluator=_TeamHealthEvaluator())
    )
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        for command in commands:
            response = await client.post(
                "/v1/commands", headers=headers, json=command.model_dump(mode="json")
            )
            assert response.status_code == 200, response.text
            assert response.json()["status"] == "accepted"
        collaboration = await client.get(
            f"/v1/missions/{mission.mission_id}/collaboration", headers=headers
        )
        snapshot = await client.get("/v1/snapshot", headers=headers)
        health = await client.get(f"/v1/missions/{mission.mission_id}/health", headers=headers)
        events = await client.get(
            "/v1/events",
            headers=headers,
            params={"entity_id": str(mission.mission_id)},
        )

    assert collaboration.status_code == 200
    assert collaboration.json()["plan_id"] == str(plan.plan_id)
    assert snapshot.json()["projections"]["team_effectiveness"]["collaboration_plans"]
    assert health.json()[0]["status"] == "healthy"
    assert "mission.collaboration_plan_recorded" in {
        item["event_type"] for item in events.json()["events"]
    }


@pytest.mark.anyio
async def test_mission_brief_and_contextual_crew_use_the_common_daemon_authority(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    paths = DaemonBootstrap().setup(config)
    token = TokenFile(paths.token_file).read()
    headers = {"Authorization": f"Bearer {token.token}"}
    organization = load_canonical_organization()
    mission = MissionRecord(
        origin=MissionOrigin(
            kind=MissionOriginKind.CEO,
            actor_id="ceo:y4nn777",
            objective="Add durable account recovery",
        ),
        organization_id=organization.organization_id,
        organization_version=organization.organization_version,
    )
    brief = _brief(mission)
    crew = _crew(brief)
    assignment = MissionTaskAssignment(
        mission_id=mission.mission_id,
        crew_version=crew.version,
        task_id="implement-recovery",
        accountable_owner="Backend_Service_Engineer",
        assignment_kind=CrewAssignmentKind.PRODUCTION,
        expected_result="A verified recovery implementation",
        completion_criteria=("independent recovery test passes",),
        authority_scope=("repository:api",),
        exact_tools=("file.read", "file.patch", "process.run"),
        path_scopes=("repository:api",),
        limits=(MissionResourceLimit(name="wall_time", value=1800, unit="seconds"),),
        required_evidence=("test report", "change set"),
    )
    planned = MissionTransition(
        mission_id=mission.mission_id,
        from_state=MissionState.CLARIFYING,
        to_state=MissionState.PLANNED,
        actor_or_cause="PM+CTO",
        reason="Brief, crew, and task assignment are ready",
        affected_scope=("mission:all",),
        evidence_references=(f"brief:{brief.brief_id}", f"crew:{crew.crew_id}"),
    )
    active = MissionTransition(
        mission_id=mission.mission_id,
        from_state=MissionState.PLANNED,
        to_state=MissionState.ACTIVE,
        actor_or_cause="Backend_Service_Engineer",
        reason="The accountable task assignment is eligible",
        affected_scope=("task:implement-recovery",),
        evidence_references=(f"assignment:{assignment.assignment_id}",),
    )

    commands = (
        ApplicationCommand(
            command_type="mission.create",
            actor_id=token.principal_id,
            target_type="mission",
            target_id=str(mission.mission_id),
            expected_revision=0,
            payload={"record": mission.model_dump(mode="json")},
        ),
        ApplicationCommand(
            command_type="mission.brief.record",
            actor_id=token.principal_id,
            target_type="mission",
            target_id=str(mission.mission_id),
            expected_revision=1,
            payload={"brief": brief.model_dump(mode="json")},
        ),
        ApplicationCommand(
            command_type="mission.crew.record",
            actor_id=token.principal_id,
            target_type="mission",
            target_id=str(mission.mission_id),
            expected_revision=2,
            payload={"crew": crew.model_dump(mode="json")},
        ),
    )

    transport = httpx.ASGITransport(app=create_app(config))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        results = []
        for command in commands:
            response = await client.post(
                "/v1/commands", headers=headers, json=command.model_dump(mode="json")
            )
            assert response.status_code == 200
            assert response.json()["status"] == "accepted"
            results.append(response.json())

        for command in (
            ApplicationCommand(
                command_type="mission.assignment.record",
                actor_id=token.principal_id,
                target_type="mission_assignment",
                target_id=str(assignment.assignment_id),
                expected_revision=0,
                payload={"assignment": assignment.model_dump(mode="json")},
            ),
            ApplicationCommand(
                command_type="mission.transition",
                actor_id=token.principal_id,
                target_type="mission",
                target_id=str(mission.mission_id),
                expected_revision=3,
                payload={"transition": planned.model_dump(mode="json")},
            ),
            ApplicationCommand(
                command_type="mission.transition",
                actor_id=token.principal_id,
                target_type="mission",
                target_id=str(mission.mission_id),
                expected_revision=4,
                payload={"transition": active.model_dump(mode="json")},
            ),
        ):
            response = await client.post(
                "/v1/commands", headers=headers, json=command.model_dump(mode="json")
            )
            assert response.status_code == 200
            assert response.json()["status"] == "accepted"

        replay = await client.post(
            "/v1/commands", headers=headers, json=commands[-1].model_dump(mode="json")
        )
        roster_response = await client.get("/v1/organization", headers=headers)
        mission_response = await client.get(f"/v1/missions/{mission.mission_id}", headers=headers)
        brief_response = await client.get(
            f"/v1/missions/{mission.mission_id}/brief", headers=headers
        )
        crew_response = await client.get(f"/v1/missions/{mission.mission_id}/crew", headers=headers)
        assignment_response = await client.get(
            f"/v1/missions/{mission.mission_id}/assignments", headers=headers
        )
        transition_response = await client.get(
            f"/v1/missions/{mission.mission_id}/transitions", headers=headers
        )

    assert replay.json() == results[-1]
    assert len(roster_response.json()["identities"]) == 59
    durable = MissionRecord.model_validate(mission_response.json())
    assert durable.revision == 5
    assert durable.state is MissionState.ACTIVE
    assert MissionBrief.model_validate(brief_response.json()) == brief
    assert MissionCrewRevision.model_validate(crew_response.json()) == crew
    assert MissionTaskAssignment.model_validate(assignment_response.json()[0]) == assignment
    assert [item["to_state"] for item in transition_response.json()] == ["planned", "active"]


@pytest.mark.anyio
async def test_daemon_claims_only_an_explicitly_bound_eligible_mission_task(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    paths = DaemonBootstrap().setup(config)
    token = TokenFile(paths.token_file).read()
    headers = {"Authorization": f"Bearer {token.token}"}
    organization = load_canonical_organization()
    mission_identity = uuid4()
    runs = LocalRunRepository(paths.database)
    discovery = DiscoverySnapshot(
        binding=RepositoryBinding(
            repository_id="a" * 64,
            root=tmp_path,
            base_revision="b" * 40,
            working_tree_dirty=False,
            working_tree_fingerprint="c" * 64,
        ),
        facts=(),
        unknowns=(),
        fingerprint="d" * 64,
    )
    run = runs.start_or_resume(discovery, "Execute the accepted mission task", "mission")
    task_contract = PlanTask(
        task_id="implement-recovery",
        title="Implement recovery",
        purpose="Produce the accepted mission result",
        assigned_role="Backend_Service_Engineer",
        tools=("file.read",),
        evidence_paths=("README.md",),
    )
    registry, tool_bindings = resolved_tool_lineage(tmp_path, (task_contract,))
    accepted_plan = AcceptedPlan(
        objective="Execute the accepted mission task",
        outcome_id="mission",
        repository_revision=discovery.binding.base_revision,
        tasks=(task_contract,),
        fingerprint="e" * 64,
        discovery_fingerprint=discovery.fingerprint,
        registry=registry,
        tool_bindings=tool_bindings,
        organization_binding=PlanOrganizationBinding(
            organization_id=organization.organization_id,
            organization_version=organization.organization_version,
            organization_fingerprint=organization.fingerprint,
            mission_id=mission_identity,
        ),
    )
    runs.accept_plan(
        run.run_id,
        accepted_plan,
    )
    runs.start_run(run.run_id)
    missions = SQLiteMissionRepository(paths.database)
    missions.record_organization(organization, emit_event=False)
    mission = missions.create_mission(
        MissionRecord(
            mission_id=mission_identity,
            origin=MissionOrigin(
                kind=MissionOriginKind.CEO,
                actor_id="CEO",
                objective="Execute a mission task through the complete claim gate",
            ),
            organization_id=organization.organization_id,
            organization_version=organization.organization_version,
        )
    )
    brief = _brief(mission)
    missions.record_brief(brief, expected_revision=mission.revision)
    crew = _crew(brief)
    missions.record_crew(crew, expected_revision=2)
    assignment = MissionTaskAssignment(
        mission_id=mission.mission_id,
        crew_version=crew.version,
        task_id="implement-recovery",
        accountable_owner="Backend_Service_Engineer",
        assignment_kind=CrewAssignmentKind.PRODUCTION,
        expected_result="A verified recovery implementation",
        completion_criteria=("independent recovery test passes",),
        execution_run_id=run.run_id,
        execution_task_id="implement-recovery",
        authority_scope=("repository:api",),
        exact_tools=("file.read",),
        path_scopes=("repository:api",),
        limits=(MissionResourceLimit(name="wall_time", value=1800, unit="seconds"),),
        required_evidence=("test report",),
    )
    missions.record_assignment(assignment)
    missions.record_run_binding(
        MissionRunBinding(
            mission_id=mission.mission_id,
            binding_key="implement-recovery",
            mission_task_id=assignment.task_id,
            assignment_id=assignment.assignment_id,
            assignment_revision=assignment.assignment_revision,
            run_id=run.run_id,
            execution_task_id=assignment.execution_task_id or assignment.task_id,
            plan_fingerprint=accepted_plan.fingerprint,
            execution_context=PlanExecutionContext.from_binding(discovery.binding),
            authority_scope=assignment.authority_scope,
            path_scopes=assignment.path_scopes,
            recorded_by=token.principal_id,
        )
    )
    missions.transition(
        MissionTransition(
            mission_id=mission.mission_id,
            from_state=MissionState.CLARIFYING,
            to_state=MissionState.PLANNED,
            actor_or_cause="PM+CTO",
            reason="The mission has an accepted Brief, crew, and bound task",
            affected_scope=("mission:all",),
            evidence_references=(f"assignment:{assignment.assignment_id}",),
        ),
        expected_revision=3,
    )
    missions.transition(
        MissionTransition(
            mission_id=mission.mission_id,
            from_state=MissionState.PLANNED,
            to_state=MissionState.ACTIVE,
            actor_or_cause="Mission_Lead",
            reason="The bound task is ready for a governed claim",
            affected_scope=("task:implement-recovery",),
            evidence_references=(f"run:{run.run_id}",),
        ),
        expected_revision=4,
    )
    request = MissionTaskClaimRequest(
        mission_id=mission.mission_id,
        mission_revision=5,
        task_id=assignment.task_id,
        assignment_revision=assignment.assignment_revision,
    )
    command = ApplicationCommand(
        command_type="mission.task.claim",
        actor_id=token.principal_id,
        target_type="mission_task",
        target_id=f"{mission.mission_id}:{assignment.task_id}",
        payload={"request": request.model_dump(mode="json")},
    )

    transport = httpx.ASGITransport(app=create_app(config))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        eligible = await client.get(
            f"/v1/missions/{mission.mission_id}/tasks/{assignment.task_id}/eligibility",
            headers=headers,
        )
        claimed = await client.post(
            "/v1/commands",
            headers=headers,
            json=command.model_dump(mode="json"),
        )

    assert eligible.status_code == 200
    assert eligible.json()["eligible"] is True
    assert claimed.status_code == 200
    assert claimed.json()["status"] == "accepted", claimed.json()
    assert claimed.json()["payload"]["attempt"] == 1
    assert runs.task_states(run.run_id)[assignment.task_id] == "executing"


@pytest.mark.anyio
async def test_mission_command_refuses_a_stale_application_revision(tmp_path: Path) -> None:
    config = _config(tmp_path)
    paths = DaemonBootstrap().setup(config)
    token = TokenFile(paths.token_file).read()
    organization = load_canonical_organization()
    mission = MissionRecord(
        origin=MissionOrigin(
            kind=MissionOriginKind.CEO,
            actor_id="ceo:y4nn777",
            objective="Verify stale mission commands",
        ),
        organization_id=organization.organization_id,
        organization_version=organization.organization_version,
    )
    create = ApplicationCommand(
        command_type="mission.create",
        actor_id=token.principal_id,
        target_type="mission",
        target_id=str(mission.mission_id),
        expected_revision=0,
        payload={"record": mission.model_dump(mode="json")},
    )
    stale = ApplicationCommand(
        command_type="mission.brief.record",
        actor_id=token.principal_id,
        target_type="mission",
        target_id=str(mission.mission_id),
        expected_revision=0,
        payload={"brief": _brief(mission).model_dump(mode="json")},
    )
    headers = {"Authorization": f"Bearer {token.token}"}
    transport = httpx.ASGITransport(app=create_app(config))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await client.post("/v1/commands", headers=headers, json=create.model_dump(mode="json"))
        response = await client.post(
            "/v1/commands", headers=headers, json=stale.model_dump(mode="json")
        )

    assert response.status_code == 409
    assert response.json()["code"] == "ERR-REV-001"


@pytest.mark.anyio
async def test_crewai_governance_command_returns_candidate_without_implicit_mutation(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    paths = DaemonBootstrap().setup(config)
    token = TokenFile(paths.token_file).read()
    organization = load_canonical_organization()
    mission = MissionRecord(
        origin=MissionOrigin(
            kind=MissionOriginKind.CEO,
            actor_id="CEO",
            objective="Propose a governed account recovery mission",
        ),
        organization_id=organization.organization_id,
        organization_version=organization.organization_version,
    )
    request = MissionGovernanceRequest(
        mission_id=mission.mission_id,
        mission_revision=1,
        evidence=(
            MissionGovernanceEvidence(
                reference="artifact:discovery",
                summary="Repository evidence for account recovery",
            ),
        ),
    )
    create = ApplicationCommand(
        command_type="mission.create",
        actor_id=token.principal_id,
        target_type="mission",
        target_id=str(mission.mission_id),
        expected_revision=0,
        payload={"record": mission.model_dump(mode="json")},
    )
    propose = ApplicationCommand(
        command_type="mission.governance.propose",
        actor_id=token.principal_id,
        target_type="mission_governance_request",
        target_id=str(request.request_id),
        expected_revision=0,
        payload={"request": request.model_dump(mode="json")},
    )
    headers = {"Authorization": f"Bearer {token.token}"}
    transport = httpx.ASGITransport(
        app=create_app(config, mission_governance_runner=_MissionGovernanceRunner())
    )
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        created = await client.post(
            "/v1/commands", headers=headers, json=create.model_dump(mode="json")
        )
        proposed = await client.post(
            "/v1/commands", headers=headers, json=propose.model_dump(mode="json")
        )
        durable = await client.get(f"/v1/missions/{mission.mission_id}", headers=headers)

    assert created.json()["status"] == "accepted"
    assert proposed.json()["status"] == "accepted"
    result = MissionGovernanceResult.model_validate(proposed.json()["payload"])
    assert result.brief.status is MissionBriefStatus.CONFIRMED
    assert durable.json()["revision"] == 1
    assert durable.json()["current_brief_version"] is None
    assert durable.json()["current_crew_version"] is None


@pytest.mark.anyio
async def test_crewai_environment_plan_requires_explicit_acceptance_and_exact_resolution(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    paths = DaemonBootstrap().setup(config)
    token = TokenFile(paths.token_file).read()
    organization = load_canonical_organization()
    mission = MissionRecord(
        origin=MissionOrigin(
            kind=MissionOriginKind.CEO,
            actor_id="CEO",
            objective="Plan an attributable execution environment",
        ),
        organization_id=organization.organization_id,
        organization_version=organization.organization_version,
    )
    brief = _brief(mission)
    crew = _crew(brief)
    planning_assignment = MissionTaskAssignment(
        mission_id=mission.mission_id,
        crew_version=crew.version,
        task_id="plan-environment",
        accountable_owner="Backend_Service_Engineer",
        assignment_kind=CrewAssignmentKind.PRODUCTION,
        expected_result="An attributable environment plan",
        completion_criteria=("one exposed outcome is requested per context",),
        authority_scope=("repository:api",),
        exact_tools=(),
        path_scopes=("repository:api",),
        limits=(MissionResourceLimit(name="wall_time", value=600, unit="seconds"),),
        required_evidence=("environment observation",),
    )
    dependent_assignment = planning_assignment.model_copy(
        update={
            "assignment_id": uuid4(),
            "task_id": "build-project",
            "expected_result": "A verified project build",
            "environment_context_ids": ("repository:api",),
        }
    )
    observation_request = EnvironmentObservationRequest(
        actor_identity=token.principal_id,
        context_id="repository:api",
        repository_id="api",
        repository_revision="abc123",
        execution_location="local:test",
    )
    headers = {"Authorization": f"Bearer {token.token}"}
    transport = httpx.ASGITransport(
        app=create_app(config, mission_environment_runner=_MissionEnvironmentRunner())
    )
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        setup_commands = (
            ApplicationCommand(
                command_type="mission.create",
                actor_id=token.principal_id,
                target_type="mission",
                target_id=str(mission.mission_id),
                expected_revision=0,
                payload={"record": mission.model_dump(mode="json")},
            ),
            ApplicationCommand(
                command_type="mission.brief.record",
                actor_id=token.principal_id,
                target_type="mission",
                target_id=str(mission.mission_id),
                expected_revision=1,
                payload={"brief": brief.model_dump(mode="json")},
            ),
            ApplicationCommand(
                command_type="mission.crew.record",
                actor_id=token.principal_id,
                target_type="mission",
                target_id=str(mission.mission_id),
                expected_revision=2,
                payload={"crew": crew.model_dump(mode="json")},
            ),
            *(
                ApplicationCommand(
                    command_type="mission.assignment.record",
                    actor_id=token.principal_id,
                    target_type="mission_assignment",
                    target_id=str(assignment.assignment_id),
                    expected_revision=0,
                    payload={"assignment": assignment.model_dump(mode="json")},
                )
                for assignment in (planning_assignment, dependent_assignment)
            ),
        )
        for command in setup_commands:
            response = await client.post(
                "/v1/commands", headers=headers, json=command.model_dump(mode="json")
            )
            assert response.status_code == 200, response.text

        observed_response = await client.post(
            "/v1/commands",
            headers=headers,
            json=ApplicationCommand(
                command_type="environment.observe",
                actor_id=token.principal_id,
                target_type="environment_observation",
                target_id=str(observation_request.observation_id),
                payload={"request": observation_request.model_dump(mode="json")},
            ).model_dump(mode="json"),
        )
        observation = EnvironmentObservation.model_validate(observed_response.json()["payload"])
        planning_request = MissionEnvironmentPlanningRequest(
            mission_id=mission.mission_id,
            mission_revision=3,
            brief_version=1,
            crew_version=1,
            planning_task_id="plan-environment",
            owner_identity="Backend_Service_Engineer",
            contexts=(
                MissionEnvironmentContextRequest(
                    context_id=observation.context_id,
                    observation_id=observation.observation_id,
                    observation_revision=observation.revision,
                    observation_fingerprint=observation.fingerprint,
                    target_platform=observation.platform,
                    target_architecture=observation.architecture,
                    execution_location=observation.execution_location,
                    affected_task_ids=("build-project",),
                    alternatives=(
                        MissionEnvironmentAlternative(
                            alternative_id="unresolved",
                            requested_outcome=EnvironmentOutcome.UNRESOLVED,
                            evidence_references=("observation:environment",),
                        ),
                    ),
                    evidence_references=("observation:environment",),
                ),
            ),
            evidence_references=("brief:environment-intent",),
        )
        proposed_response = await client.post(
            "/v1/commands",
            headers=headers,
            json=ApplicationCommand(
                command_type="mission.environment.propose",
                actor_id=token.principal_id,
                target_type="mission_environment_planning_request",
                target_id=str(planning_request.request_id),
                payload={"request": planning_request.model_dump(mode="json")},
            ).model_dump(mode="json"),
        )
        proposed = MissionEnvironmentPlan.model_validate(proposed_response.json()["payload"])
        before_acceptance = await client.get(f"/v1/missions/{mission.mission_id}", headers=headers)
        resolver_authored = proposed.model_copy(update={"plan_id": uuid4()})
        refused_resolver_authored = await client.post(
            "/v1/commands",
            headers=headers,
            json=ApplicationCommand(
                command_type="mission.environment.accept",
                actor_id=token.principal_id,
                target_type="mission",
                target_id=str(proposed.mission_id),
                expected_revision=3,
                payload={"plan": resolver_authored.model_dump(mode="json")},
            ).model_dump(mode="json"),
        )
        missing_decision_id = uuid4()
        selected = (
            proposed.contexts[0]
            .alternatives[0]
            .model_copy(
                update={
                    "requires_consequential_decision": True,
                    "consequential_decision_id": missing_decision_id,
                    "consequential_option_id": "unresolved",
                }
            )
        )
        consequential_without_decision = MissionEnvironmentPlan.model_validate(
            proposed.model_copy(
                update={
                    "contexts": (
                        proposed.contexts[0].model_copy(update={"alternatives": (selected,)}),
                    ),
                    "decisions": (
                        proposed.decisions[0].model_copy(
                            update={
                                "requires_consequential_decision": True,
                                "consequential_decision_id": missing_decision_id,
                                "consequential_option_id": "unresolved",
                            }
                        ),
                    ),
                }
            ).model_dump(mode="json")
        )
        refused_consequential = await client.post(
            "/v1/commands",
            headers=headers,
            json=ApplicationCommand(
                command_type="mission.environment.accept",
                actor_id=token.principal_id,
                target_type="mission",
                target_id=str(proposed.mission_id),
                expected_revision=3,
                payload={"plan": consequential_without_decision.model_dump(mode="json")},
            ).model_dump(mode="json"),
        )
        accepted_response = await client.post(
            "/v1/commands",
            headers=headers,
            json=ApplicationCommand(
                command_type="mission.environment.accept",
                actor_id=token.principal_id,
                target_type="mission",
                target_id=str(proposed.mission_id),
                expected_revision=3,
                payload={"plan": proposed.model_dump(mode="json")},
            ).model_dump(mode="json"),
        )
        assert accepted_response.status_code == 200, accepted_response.text
        resolved_response = await client.post(
            "/v1/commands",
            headers=headers,
            json=ApplicationCommand(
                command_type="mission.environment.resolve",
                actor_id=token.principal_id,
                target_type="mission_environment_plan",
                target_id=str(proposed.plan_id),
                payload={"context_id": observation.context_id},
            ).model_dump(mode="json"),
        )
        assert resolved_response.status_code == 200, resolved_response.text
        duplicate_resolution = await client.post(
            "/v1/commands",
            headers=headers,
            json=ApplicationCommand(
                command_type="mission.environment.resolve",
                actor_id=token.principal_id,
                target_type="mission_environment_plan",
                target_id=str(proposed.plan_id),
                payload={"context_id": observation.context_id},
            ).model_dump(mode="json"),
        )
        assert duplicate_resolution.status_code == 200, duplicate_resolution.text
        durable_plan_response = await client.get(
            f"/v1/missions/{mission.mission_id}/environment-plan", headers=headers
        )
        readiness_response = await client.get(
            f"/v1/missions/{mission.mission_id}/readiness", headers=headers
        )

    assert proposed_response.json()["status"] == "accepted"
    assert refused_resolver_authored.status_code == 200
    assert refused_resolver_authored.json()["status"] == "refused"
    assert refused_resolver_authored.json()["error"]["code"] == ErrorCode.PLAN.value
    assert refused_consequential.status_code == 200
    assert refused_consequential.json()["status"] == "refused"
    assert refused_consequential.json()["error"]["code"] == ErrorCode.DECISION_VALIDATION.value
    assert before_acceptance.json()["revision"] == 3
    assert before_acceptance.json()["current_environment_plan_version"] is None
    acceptance = MissionEnvironmentPlanAcceptance.model_validate(
        accepted_response.json()["payload"]
    )
    assert acceptance.plan == proposed
    assert durable_plan_response.json() == acceptance.model_dump(mode="json")
    assert resolved_response.json()["payload"]["state"] == EnvironmentBindingState.UNRESOLVED
    assert resolved_response.json()["payload"]["request"]["requested_outcome"] == "unresolved"
    assert duplicate_resolution.json()["payload"] == resolved_response.json()["payload"]
    readiness = readiness_response.json()
    assert readiness["ready_task_ids"] == ["plan-environment"]
    assert readiness["blocked_task_ids"] == ["build-project"]
    assert readiness["tasks"][0]["state"] == "unresolved"


@pytest.mark.anyio
async def test_conversation_escalation_and_intervention_share_daemon_semantics(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    paths = DaemonBootstrap().setup(config)
    token = TokenFile(paths.token_file).read()
    organization = load_canonical_organization()
    mission = MissionRecord(
        origin=MissionOrigin(
            kind=MissionOriginKind.CEO,
            actor_id="CEO",
            objective="Resolve a bounded PM and CTO disagreement",
        ),
        organization_id=organization.organization_id,
        organization_version=organization.organization_version,
    )
    brief = _brief(mission)
    channel = ConversationChannel(
        channel_class=ChannelClass.MISSION,
        title="Bounded disagreement",
        participants=("CEO", "PM", "CTO"),
        mission_id=mission.mission_id,
        created_by="PM",
    )
    message = ConversationMessage(
        conversation_id=channel.conversation_id,
        author_identity="CTO",
        body="This recommendation is evidence, not an implicit command.",
    )
    escalation = MissionEscalation(
        mission_id=mission.mission_id,
        conversation_id=channel.conversation_id,
        raised_by="PM",
        blocked_scope=("task:dependent",),
        decision_required="Choose whether the bounded risk is acceptable",
        reason="PM and CTO recommendations differ",
        options=(
            EscalationOption(
                option_id="accept",
                description="Accept the bounded risk",
                consequences=("dependent work continues",),
                risks=("compatibility remains imperfect",),
            ),
            EscalationOption(
                option_id="reject",
                description="Require remediation first",
                consequences=("dependent work remains paused",),
                risks=("delivery is delayed",),
            ),
        ),
        recommendations=(
            ExecutiveRecommendation(
                identity_id="PM",
                recommended_option_id="accept",
                rationale="User benefit outweighs the bounded risk",
                evidence_references=("evidence:product",),
            ),
            ExecutiveRecommendation(
                identity_id="CTO",
                recommended_option_id="reject",
                rationale="Technical evidence remains incomplete",
                evidence_references=("evidence:technical",),
            ),
        ),
        uncertainty=("legacy compatibility",),
        independent_work_continuing=("task:independent",),
        evidence_references=("evidence:joint-review",),
    )
    intervention = MissionIntervention(
        schema_version="1.1",
        mission_id=mission.mission_id,
        conversation_id=channel.conversation_id,
        actor_id="CEO",
        kind=InterventionKind.ANSWER_ESCALATION,
        target_kind=InterventionTargetKind.ESCALATION,
        target_id=str(escalation.escalation_id),
        reason="The product evidence supports accepting the bounded risk",
        scope=("task:dependent",),
        confirmation="Accept the risk for the dependent task only",
        authority_reference="authority:ceo",
        evidence_references=("evidence:ceo-answer",),
        escalation_id=escalation.escalation_id,
        selected_option_id="accept",
        effect="Answer the escalation without pausing independent work",
        resulting_target_state=InterventionResultState.ANSWERED,
    )
    commands = (
        ApplicationCommand(
            command_type="mission.create",
            actor_id=token.principal_id,
            target_type="mission",
            target_id=str(mission.mission_id),
            expected_revision=0,
            payload={"record": mission.model_dump(mode="json")},
        ),
        ApplicationCommand(
            command_type="mission.brief.record",
            actor_id=token.principal_id,
            target_type="mission",
            target_id=str(mission.mission_id),
            expected_revision=1,
            payload={"brief": brief.model_dump(mode="json")},
        ),
        ApplicationCommand(
            command_type="conversation.create",
            actor_id=token.principal_id,
            target_type="conversation",
            target_id=str(channel.conversation_id),
            expected_revision=0,
            payload={"channel": channel.model_dump(mode="json")},
        ),
        ApplicationCommand(
            command_type="conversation.message.post",
            actor_id=token.principal_id,
            target_type="conversation_message",
            target_id=str(message.message_id),
            expected_revision=0,
            payload={"message": message.model_dump(mode="json")},
        ),
        ApplicationCommand(
            command_type="mission.escalation.open",
            actor_id=token.principal_id,
            target_type="mission_escalation",
            target_id=str(escalation.escalation_id),
            expected_revision=0,
            payload={"escalation": escalation.model_dump(mode="json")},
        ),
        ApplicationCommand(
            command_type="mission.intervention.apply",
            actor_id=token.principal_id,
            target_type="mission",
            target_id=str(mission.mission_id),
            expected_revision=2,
            payload={"intervention": intervention.model_dump(mode="json")},
        ),
    )
    headers = {"Authorization": f"Bearer {token.token}"}
    transport = httpx.ASGITransport(app=create_app(config))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        for command in commands:
            response = await client.post(
                "/v1/commands", headers=headers, json=command.model_dump(mode="json")
            )
            assert response.status_code == 200
            assert response.json()["status"] == "accepted"
        messages = await client.get(
            f"/v1/conversations/{channel.conversation_id}/messages", headers=headers
        )
        escalations = await client.get(
            f"/v1/missions/{mission.mission_id}/escalations", headers=headers
        )
        interventions = await client.get(
            f"/v1/missions/{mission.mission_id}/interventions", headers=headers
        )
        notifications = await client.get(
            "/v1/notifications",
            headers=headers,
            params={"severity": "urgent"},
        )
        durable_mission = await client.get(f"/v1/missions/{mission.mission_id}", headers=headers)

    assert [item["message_id"] for item in messages.json()] == [str(message.message_id)]
    assert escalations.json()[0]["state"] == "answered"
    assert interventions.json()[0]["intervention_id"] == str(intervention.intervention_id)
    escalation_notifications = [
        item
        for item in notifications.json()["notifications"]
        if item["event_type"] == "mission.escalation_opened"
    ]
    assert len(escalation_notifications) == 1
    assert escalation_notifications[0]["severity"] == "urgent"
    assert escalation_notifications[0]["delivery"] == "feed"
    assert escalation_notifications[0]["action_reference"].startswith("event:")
    assert durable_mission.json()["revision"] == 3


@pytest.mark.anyio
async def test_policy_can_distinguish_comment_from_stop_interventions(tmp_path: Path) -> None:
    config = _config(tmp_path)
    policy = tmp_path / "intervention-policy.yaml"
    policy.write_text(
        """\
schema_version: "1.0"
source_id: test.interventions
revision: "1"
adoption_authority: test
priority: 100
rules:
  - rule_id: test.deny-stop
    priority: 200
    decision: deny
    scope:
      identities: [local-operator]
      objective_classes: [application-command]
      repositories: ["*"]
      outcomes: [mission.intervention.apply]
      roles: [application-client]
      capabilities: [application.mission.intervention]
      effect_classes: [coordination]
      effects: [mission.intervention.apply, mission.intervention.stop]
  - rule_id: test.allow-intervention
    priority: 100
    decision: allow
    scope:
      identities: [local-operator]
      objective_classes: [application-command]
      repositories: ["*"]
      outcomes: [mission.intervention.apply]
      roles: [application-client]
      capabilities: [application.mission.intervention]
      effect_classes: [coordination]
      effects: ["*"]
      external_resources: ["*"]
""",
        encoding="utf-8",
    )
    config = config.model_copy(update={"policy_sources": (str(policy),)})
    paths = DaemonBootstrap().setup(config)
    token = TokenFile(paths.token_file).read()
    mission_id = uuid4()
    conversation_id = uuid4()

    def command(kind: InterventionKind) -> ApplicationCommand:
        lifecycle = kind is InterventionKind.STOP
        intervention = MissionIntervention(
            schema_version="1.1",
            mission_id=mission_id,
            conversation_id=conversation_id,
            actor_id="CEO",
            kind=kind,
            target_kind=InterventionTargetKind.MISSION,
            target_id=str(mission_id),
            reason="Exercise the exact public intervention policy effect",
            scope=("mission:all",),
            confirmation="Confirm this exact governed intervention",
            authority_reference="authority:ceo",
            evidence_references=("evidence:policy-test",),
            effect="Record the requested intervention only when policy permits it",
            resulting_target_state=(
                InterventionResultState.CANCELLED
                if lifecycle
                else InterventionResultState.UNCHANGED
            ),
            resulting_mission_state=MissionState.CANCELLED if lifecycle else None,
        )
        return ApplicationCommand(
            command_type="mission.intervention.apply",
            actor_id=token.principal_id,
            target_type="mission",
            target_id=str(mission_id),
            expected_revision=0,
            payload={"intervention": intervention.model_dump(mode="json")},
        )

    transport = httpx.ASGITransport(app=create_app(config))
    headers = {"Authorization": f"Bearer {token.token}"}
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        stopped = await client.post(
            "/v1/commands",
            headers=headers,
            json=command(InterventionKind.STOP).model_dump(mode="json"),
        )
        commented = await client.post(
            "/v1/commands",
            headers=headers,
            json=command(InterventionKind.COMMENT).model_dump(mode="json"),
        )

    assert stopped.json()["status"] == "refused"
    assert stopped.json()["error"]["code"] == ErrorCode.AUTHORITY_NOT_GRANTED
    assert commented.status_code == 200, commented.json()
    assert commented.json()["status"] == "refused"
    assert commented.json()["error"]["code"] == ErrorCode.MISSION


@pytest.mark.anyio
@pytest.mark.parametrize("reporter_acceptance_conflict", [False, True])
async def test_mission_completion_requires_separated_accepted_task_chain(
    tmp_path: Path,
    reporter_acceptance_conflict: bool,
) -> None:
    config = _config(tmp_path)
    paths = DaemonBootstrap().setup(config)
    token = TokenFile(paths.token_file).read()
    headers = {"Authorization": f"Bearer {token.token}"}
    organization = load_canonical_organization()
    mission_identity = uuid4()
    backend_branch = next(
        item.branch_id
        for item in organization.identities
        if item.identity_id == "Backend_Service_Engineer"
    )
    runs = LocalRunRepository(paths.database)
    discovery = DiscoverySnapshot(
        binding=RepositoryBinding(
            repository_id="a" * 64,
            root=tmp_path,
            base_revision="b" * 40,
            working_tree_dirty=False,
            working_tree_fingerprint="c" * 64,
        ),
        facts=(),
        unknowns=(),
        fingerprint="d" * 64,
    )
    run = runs.start_or_resume(discovery, "Deliver one governed mission", "mission-chain")
    task_contracts = (
        PlanTask(
            task_id="produce-change",
            title="Produce the change",
            purpose="Produce the accepted workspace change",
            assigned_role="Backend_Service_Engineer",
            tools=("file.read",),
            evidence_paths=("README.md",),
        ),
        PlanTask(
            task_id="evaluate-change",
            title="Evaluate the change",
            purpose="Evaluate the production result independently",
            assigned_role="Product_Functional_Evaluator",
            tools=("file.read",),
            evidence_paths=("README.md",),
            depends_on=("produce-change",),
        ),
        PlanTask(
            task_id="report-change",
            title="Report the change",
            purpose="Report only independently accepted evidence",
            assigned_role="Technical_Change_Reporter",
            tools=("file.read",),
            evidence_paths=("README.md",),
            depends_on=("evaluate-change",),
        ),
    )
    registry, tool_bindings = resolved_tool_lineage(tmp_path, task_contracts)
    accepted_plan = AcceptedPlan(
        objective="Deliver one governed mission",
        outcome_id="mission-chain",
        repository_revision=discovery.binding.base_revision,
        tasks=task_contracts,
        fingerprint="e" * 64,
        discovery_fingerprint=discovery.fingerprint,
        registry=registry,
        tool_bindings=tool_bindings,
        organization_binding=PlanOrganizationBinding(
            organization_id=organization.organization_id,
            organization_version=organization.organization_version,
            organization_fingerprint=organization.fingerprint,
            mission_id=mission_identity,
        ),
    )
    runs.accept_plan(
        run.run_id,
        accepted_plan,
    )
    runs.start_run(run.run_id)
    mission = MissionRecord(
        mission_id=mission_identity,
        origin=MissionOrigin(
            kind=MissionOriginKind.CEO,
            actor_id="CEO",
            objective="Deliver one change through independent assurance and reporting",
        ),
        organization_id=organization.organization_id,
        organization_version=organization.organization_version,
    )
    brief = _brief(mission)
    crew = _crew(brief)
    assignments = (
        MissionTaskAssignment(
            mission_id=mission.mission_id,
            crew_version=crew.version,
            task_id="produce-change",
            accountable_owner="Backend_Service_Engineer",
            assignment_kind=CrewAssignmentKind.PRODUCTION,
            expected_result="An attributable production change",
            completion_criteria=("production result is reviewable",),
            execution_run_id=run.run_id,
            execution_task_id="produce-change",
            authority_scope=("repository:api",),
            exact_tools=("file.read",),
            path_scopes=("repository:api",),
            limits=(MissionResourceLimit(name="wall_time", value=600, unit="seconds"),),
            required_evidence=("artifact:production",),
            requires_independent_evaluation=True,
        ),
        MissionTaskAssignment(
            mission_id=mission.mission_id,
            crew_version=crew.version,
            task_id="evaluate-change",
            accountable_owner="Product_Functional_Evaluator",
            assignment_kind=CrewAssignmentKind.EVALUATION,
            expected_result="An independent acceptance decision",
            completion_criteria=("evaluation decision is explicit",),
            dependencies=("produce-change",),
            execution_run_id=run.run_id,
            execution_task_id="evaluate-change",
            authority_scope=("repository:api",),
            exact_tools=("file.read",),
            path_scopes=("repository:api",),
            limits=(MissionResourceLimit(name="wall_time", value=600, unit="seconds"),),
            required_evidence=("evaluation:production",),
        ),
        MissionTaskAssignment(
            mission_id=mission.mission_id,
            crew_version=crew.version,
            task_id="report-change",
            accountable_owner="Technical_Change_Reporter",
            assignment_kind=CrewAssignmentKind.REPORTING,
            expected_result="A structured report grounded in accepted evidence",
            completion_criteria=("report identifies evidence and residual risk",),
            dependencies=("evaluate-change",),
            execution_run_id=run.run_id,
            execution_task_id="report-change",
            authority_scope=("repository:api",),
            exact_tools=("file.read",),
            path_scopes=("repository:api",),
            limits=(MissionResourceLimit(name="wall_time", value=600, unit="seconds"),),
            required_evidence=("report:mission",),
        ),
    )
    run_bindings = tuple(
        MissionRunBinding(
            mission_id=mission.mission_id,
            binding_key=assignment.task_id,
            mission_task_id=assignment.task_id,
            assignment_id=assignment.assignment_id,
            assignment_revision=assignment.assignment_revision,
            run_id=run.run_id,
            execution_task_id=assignment.execution_task_id or assignment.task_id,
            plan_fingerprint=accepted_plan.fingerprint,
            execution_context=PlanExecutionContext.from_binding(discovery.binding),
            depends_on_binding_keys=assignment.dependencies,
            authority_scope=assignment.authority_scope,
            path_scopes=assignment.path_scopes,
            recorded_by=token.principal_id,
        )
        for assignment in assignments
    )
    planned = MissionTransition(
        mission_id=mission.mission_id,
        from_state=MissionState.CLARIFYING,
        to_state=MissionState.PLANNED,
        actor_or_cause="PM+CTO",
        reason="Brief, crew, and contextual task plan are confirmed",
        affected_scope=("mission:all",),
        evidence_references=(f"brief:{brief.brief_id}", f"crew:{crew.crew_id}"),
    )
    active = MissionTransition(
        mission_id=mission.mission_id,
        from_state=MissionState.PLANNED,
        to_state=MissionState.ACTIVE,
        actor_or_cause="Mission_Lead",
        reason="The separated task graph is ready",
        affected_scope=("mission:all",),
        evidence_references=tuple(
            f"assignment:{assignment.assignment_id}" for assignment in assignments
        ),
    )

    transport = httpx.ASGITransport(app=create_app(config))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        setup_commands = [
            ApplicationCommand(
                command_type="mission.create",
                actor_id=token.principal_id,
                target_type="mission",
                target_id=str(mission.mission_id),
                expected_revision=0,
                payload={"record": mission.model_dump(mode="json")},
            ),
            ApplicationCommand(
                command_type="mission.brief.record",
                actor_id=token.principal_id,
                target_type="mission",
                target_id=str(mission.mission_id),
                expected_revision=1,
                payload={"brief": brief.model_dump(mode="json")},
            ),
            ApplicationCommand(
                command_type="mission.crew.record",
                actor_id=token.principal_id,
                target_type="mission",
                target_id=str(mission.mission_id),
                expected_revision=2,
                payload={"crew": crew.model_dump(mode="json")},
            ),
            *(
                ApplicationCommand(
                    command_type="mission.assignment.record",
                    actor_id=token.principal_id,
                    target_type="mission_assignment",
                    target_id=str(assignment.assignment_id),
                    expected_revision=0,
                    payload={"assignment": assignment.model_dump(mode="json")},
                )
                for assignment in assignments
            ),
            *(
                ApplicationCommand(
                    command_type="mission.run-binding.record",
                    actor_id=token.principal_id,
                    target_type="mission_run_binding",
                    target_id=str(binding.binding_id),
                    expected_revision=0,
                    payload={"binding": binding.model_dump(mode="json")},
                )
                for binding in run_bindings
            ),
            ApplicationCommand(
                command_type="mission.transition",
                actor_id=token.principal_id,
                target_type="mission",
                target_id=str(mission.mission_id),
                expected_revision=3,
                payload={"transition": planned.model_dump(mode="json")},
            ),
            ApplicationCommand(
                command_type="mission.transition",
                actor_id=token.principal_id,
                target_type="mission",
                target_id=str(mission.mission_id),
                expected_revision=4,
                payload={"transition": active.model_dump(mode="json")},
            ),
        ]
        for command in setup_commands:
            response = await client.post(
                "/v1/commands", headers=headers, json=command.model_dump(mode="json")
            )
            assert response.status_code == 200, response.json()
        before = await client.get(
            f"/v1/missions/{mission.mission_id}/completion-readiness",
            headers=headers,
        )
        assert before.status_code == 200
        assert not before.json()["ready"]
        settled_bindings: list[MissionRunBinding] = []
        for assignment, run_binding in zip(assignments, run_bindings, strict=True):
            claim_request = MissionTaskClaimRequest(
                mission_id=mission.mission_id,
                mission_revision=5,
                task_id=assignment.task_id,
                assignment_revision=assignment.assignment_revision,
            )
            claimed = await client.post(
                "/v1/commands",
                headers=headers,
                json=ApplicationCommand(
                    command_type="mission.task.claim",
                    actor_id=token.principal_id,
                    target_type="mission_task",
                    target_id=f"{mission.mission_id}:{assignment.task_id}",
                    payload={"request": claim_request.model_dump(mode="json")},
                ).model_dump(mode="json"),
            )
            assert claimed.status_code == 200, claimed.json()
            runs.mark_validating(run.run_id, assignment.task_id)
            runs.accept_result(
                run.run_id,
                InitializationResult(
                    repository_revision=discovery.binding.base_revision,
                    task_id=assignment.task_id,
                    summary=f"Accepted result for {assignment.task_id}",
                    cited_paths=("README.md",),
                    findings=(f"{assignment.task_id} contract satisfied",),
                ),
                ReviewDecision(
                    schema_version="1.1",
                    task_id=assignment.task_id,
                    producer_identity=assignment.accountable_owner,
                    evaluator_identity=(
                        "Technical_Change_Reporter"
                        if reporter_acceptance_conflict
                        and assignment.accountable_owner == "Backend_Service_Engineer"
                        else (
                            "Software_Technical_Evaluator"
                            if assignment.accountable_owner == "Product_Functional_Evaluator"
                            else "Product_Functional_Evaluator"
                        )
                    ),
                    verdict="accepted",
                    summary=f"Independent review accepted {assignment.task_id}",
                    checked_citations=("README.md",),
                ),
            )
            settled_binding = run_binding.model_copy(
                update={
                    "binding_id": uuid4(),
                    "binding_revision": 2,
                    "result_references": (f"run-result:{run.run_id}:{assignment.task_id}",),
                    "acceptance_references": (f"run-acceptance:{run.run_id}:{assignment.task_id}",),
                    "acceptance": MissionRunAcceptance.ACCEPTED,
                }
            )
            settled_response = await client.post(
                "/v1/commands",
                headers=headers,
                json=ApplicationCommand(
                    command_type="mission.run-binding.record",
                    actor_id=token.principal_id,
                    target_type="mission_run_binding",
                    target_id=str(settled_binding.binding_id),
                    expected_revision=0,
                    payload={"binding": settled_binding.model_dump(mode="json")},
                ).model_dump(mode="json"),
            )
            assert settled_response.status_code == 200, settled_response.json()
            settled_bindings.append(settled_binding)
        bindings_response = await client.get(
            f"/v1/missions/{mission.mission_id}/run-bindings",
            headers=headers,
        )
        inspection_response = await client.get(
            f"/v1/missions/{mission.mission_id}/inspection",
            headers=headers,
        )
        organization_response = await client.get(
            "/v1/organization/inspection",
            headers=headers,
        )
        branch_response = await client.get(
            f"/v1/organization/branches/{backend_branch}/inspection",
            headers=headers,
        )
        assert bindings_response.status_code == 200
        assert len(bindings_response.json()) == 6
        inspection = inspection_response.json()
        assert len(inspection["run_bindings"]) == 6
        assert {
            "agents",
            "plans",
            "tasks",
            "artifacts",
            "evidence",
            "risks",
            "failures",
            "costs",
            "schedules",
            "events",
        }.issubset(inspection)
        assert inspection["projection"]["authoritative"] is False
        assert organization_response.status_code == 200
        assert branch_response.status_code == 200
        assert str(mission.mission_id) in branch_response.json()["mission_drill_down"]
        evaluating = MissionTransition(
            mission_id=mission.mission_id,
            from_state=MissionState.ACTIVE,
            to_state=MissionState.EVALUATING,
            actor_or_cause="Mission_Lead",
            reason="All planned results are ready for mission acceptance",
            affected_scope=("mission:all",),
            evidence_references=(f"run:{run.run_id}",),
        )
        evaluated = await client.post(
            "/v1/commands",
            headers=headers,
            json=ApplicationCommand(
                command_type="mission.transition",
                actor_id=token.principal_id,
                target_type="mission",
                target_id=str(mission.mission_id),
                expected_revision=5,
                payload={"transition": evaluating.model_dump(mode="json")},
            ).model_dump(mode="json"),
        )
        assert evaluated.status_code == 200, evaluated.json()
        not_reported = await client.get(
            f"/v1/missions/{mission.mission_id}/completion-readiness",
            headers=headers,
        )
        assert not_reported.status_code == 200
        assert not not_reported.json()["ready"]
        assert any(
            "no versioned mission report" in item for item in not_reported.json()["blockers"]
        )
        report = MissionRunReport(
            mission_id=mission.mission_id,
            mission_revision=6,
            run_id=run.run_id,
            execution_context=PlanExecutionContext.from_binding(discovery.binding),
            plan_fingerprint="e" * 64,
            reporter_identity="Technical_Change_Reporter",
            reporting_mission_task_id="report-change",
            reporting_execution_task_id="report-change",
            task_results=tuple(
                MissionRunReportTask(
                    mission_task_id=assignment.task_id,
                    execution_task_id=assignment.execution_task_id or assignment.task_id,
                    accountable_owner=assignment.accountable_owner,
                    assignment_kind=assignment.assignment_kind,
                    result_references=binding.result_references,
                    acceptance_references=binding.acceptance_references,
                    outcome_summary=f"Accepted outcome for {assignment.task_id}",
                    evidence_references=(
                        *binding.result_references,
                        *binding.acceptance_references,
                    ),
                )
                for assignment, binding in zip(assignments, settled_bindings, strict=True)
            ),
            delivered_outcomes=("The governed change and independent evaluation were accepted",),
            validation_summary=("All three accepted task contracts are covered",),
            residual_risks=("Production deployment remains excluded",),
        )
        report_response = await client.post(
            "/v1/commands",
            headers=headers,
            json=ApplicationCommand(
                command_type="mission.run-report.record",
                actor_id=token.principal_id,
                target_type="mission_run_report",
                target_id=str(report.report_id),
                expected_revision=0,
                payload={"report": report.model_dump(mode="json")},
            ).model_dump(mode="json"),
        )
        if reporter_acceptance_conflict:
            assert report_response.status_code == 200
            assert report_response.json()["status"] == "refused"
            assert report_response.json()["error"]["code"] == "ERR-ROL-001"
            conflict_reports = await client.get(
                f"/v1/missions/{mission.mission_id}/run-reports",
                headers=headers,
            )
            assert conflict_reports.status_code == 200
            assert conflict_reports.json() == []
            return
        reports_response = await client.get(
            f"/v1/missions/{mission.mission_id}/run-reports",
            headers=headers,
        )
        assert report_response.status_code == 200, report_response.json()
        assert reports_response.status_code == 200
        assert reports_response.json() == [report.model_dump(mode="json")]
        ready = await client.get(
            f"/v1/missions/{mission.mission_id}/completion-readiness",
            headers=headers,
        )
        completed = await client.post(
            "/v1/commands",
            headers=headers,
            json=ApplicationCommand(
                command_type="mission.transition",
                actor_id=token.principal_id,
                target_type="mission",
                target_id=str(mission.mission_id),
                expected_revision=6,
                payload={
                    "transition": MissionTransition(
                        mission_id=mission.mission_id,
                        from_state=MissionState.EVALUATING,
                        to_state=MissionState.COMPLETED,
                        actor_or_cause="PM+CTO",
                        reason="Production, evaluation, and reporting are durably accepted",
                        affected_scope=("mission:all",),
                        evidence_references=(f"run:{run.run_id}",),
                    ).model_dump(mode="json")
                },
            ).model_dump(mode="json"),
        )

    assert ready.status_code == 200
    assert ready.json()["ready"]
    assert {item["task_id"]: item["assignment_kind"] for item in ready.json()["tasks"]} == {
        "produce-change": "production",
        "evaluate-change": "evaluation",
        "report-change": "reporting",
    }
    assert completed.status_code == 200, completed.json()
    assert completed.json()["payload"]["to_state"] == "completed"
