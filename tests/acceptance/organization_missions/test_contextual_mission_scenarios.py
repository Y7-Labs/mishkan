from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

import pytest

from mishkan.config.loader import ConfigLoader
from mishkan.config.presets import preset_text
from mishkan.crewai.mission_environment import (
    CrewAIMissionEnvironmentPlanningRunner,
    MissionEnvironmentDecisionOutput,
    MissionEnvironmentPlanningOutput,
)
from mishkan.crewai.mission_governance import (
    CrewAICollaborationProposal,
    CrewAIMissionGovernanceRunner,
    CTOMissionReview,
    MissionGovernanceResult,
    PMMissionProposal,
)
from mishkan.environment import EnvironmentOutcome
from mishkan.missions import (
    CollaborationEdge,
    CollaborationInteraction,
    CrewAssignmentKind,
    CrewSelectionEvidence,
    MissionCrewMember,
    MissionEnvironmentAlternative,
    MissionEnvironmentContextRequest,
    MissionEnvironmentIntent,
    MissionEnvironmentPlanningRequest,
    MissionOrigin,
    MissionOriginKind,
    MissionRecord,
    MissionResourceLimit,
    MissionTaskAssignment,
    MissionTemplateLoader,
    MissionTemplateReference,
    MissionTemplateService,
    ProductionShape,
    SQLiteMissionRepository,
)
from mishkan.organization import load_canonical_organization
from mishkan.persistence.migration import SchemaManager


@dataclass(frozen=True)
class _Scenario:
    scenario_id: str
    signal: str
    template_id: str
    origin: MissionOriginKind
    objective: str
    problem: str
    outcome: str
    scope: tuple[str, ...]
    producer: str
    evaluator: str
    reporter: str
    tools: tuple[str, ...]
    evidence: tuple[str, ...]
    environment_dependent: bool = True


_SCENARIOS = (
    _Scenario(
        "greenfield",
        "greenfield",
        "greenfield",
        MissionOriginKind.CEO,
        "Create a private-by-default collaboration service",
        "No product or repository exists yet",
        "A validated service baseline in a prospective workspace",
        ("workspace:collaboration-service",),
        "System_Architect",
        "Software_Technical_Evaluator",
        "Technical_Change_Reporter",
        ("file.read", "search.files", "core.shell.run"),
        ("prospective workspace discovery", "architecture evaluation"),
    ),
    _Scenario(
        "existing",
        "existing-repository",
        "existing-system-change",
        MissionOriginKind.PROJECT_EVIDENCE,
        "Correct duplicate invoice creation in the API",
        "Observed retries can create the same invoice twice",
        "Idempotent invoice creation with regression evidence",
        ("repository:billing-api",),
        "Backend_Service_Engineer",
        "Product_Functional_Evaluator",
        "Product_Delivery_Reporter",
        ("file.read", "search.text", "core.process.exec"),
        ("production reproduction", "idempotency contract tests"),
    ),
    _Scenario(
        "multi-repository",
        "multi-repository",
        "multi-repository-change",
        MissionOriginKind.DEPENDENCY,
        "Evolve the public identity contract across API and web repositories",
        "Two repositories implement incompatible identity payloads",
        "One versioned contract adopted independently by both repositories",
        ("repository:identity-api", "repository:identity-web"),
        "Integration_Architect",
        "Software_Technical_Evaluator",
        "Technical_Change_Reporter",
        ("file.read", "search.symbol", "core.process.exec"),
        ("API contract diff", "consumer compatibility results"),
    ),
    _Scenario(
        "product",
        "product",
        "product-delivery",
        MissionOriginKind.PM,
        "Reduce onboarding abandonment with resumable setup",
        "Users lose progress when onboarding is interrupted",
        "Accessible resumable onboarding validated with users",
        ("repository:web-client",),
        "Web_Application_Engineer",
        "Product_Functional_Evaluator",
        "Product_Delivery_Reporter",
        ("file.read", "browser.observe", "browser.act"),
        ("user journey baseline", "accessibility and functional evaluation"),
    ),
    _Scenario(
        "research",
        "research",
        "research",
        MissionOriginKind.ORGANIZATIONAL_PROPOSAL,
        "Determine whether passkeys fit the account-recovery constraints",
        "The organization lacks attributable decision evidence",
        "A decision-ready comparison with explicit uncertainty",
        ("decision:account-recovery-authentication",),
        "Research_Investigator",
        "Research_Evaluator",
        "Research_Reporter",
        ("web.search", "web.fetch", "web.extract"),
        ("authoritative source set", "independent attribution review"),
        False,
    ),
    _Scenario(
        "incident",
        "incident",
        "incident-response",
        MissionOriginKind.INCIDENT,
        "Restore delayed event delivery without losing evidence",
        "The event backlog is increasing and delivery latency is unsafe",
        "Service recovery with a preserved incident timeline",
        ("service:event-delivery",),
        "Reliability_Engineer",
        "Platform_Release_Evaluator",
        "Incident_Operations_Reporter",
        ("core.shell.run", "search.history", "file.stat"),
        ("timestamped service observations", "independent recovery probe"),
    ),
    _Scenario(
        "modernization",
        "modernization",
        "system-modernization",
        MissionOriginKind.MAINTENANCE,
        "Replace a legacy queue client while preserving delivery semantics",
        "The unsupported queue client blocks security updates",
        "A reversible migration with compatibility proof",
        ("repository:event-worker",),
        "Software_Architect",
        "Performance_Resilience_Evaluator",
        "Technical_Change_Reporter",
        ("file.read", "search.structural", "core.process.exec"),
        ("current semantics baseline", "migration and rollback evaluation"),
    ),
    _Scenario(
        "platform",
        "platform",
        "platform-capability",
        MissionOriginKind.CTO,
        "Provide reproducible ARM64 CI execution for service teams",
        "Current CI environments do not cover the release architecture",
        "A measured and governed ARM64 execution capability",
        ("platform:ci", "architecture:arm64"),
        "Platform_Engineer",
        "Platform_Release_Evaluator",
        "Technical_Change_Reporter",
        ("core.process.exec", "web.fetch", "file.stat"),
        ("target compatibility matrix", "independent release evaluation"),
    ),
    _Scenario(
        "operations",
        "operations",
        "operational-change",
        MissionOriginKind.MAINTENANCE,
        "Rotate the search cluster with bounded service disruption",
        "Cluster nodes require a controlled rolling replacement",
        "A settled rotation with continuity and rollback evidence",
        ("service:search-cluster",),
        "Delivery_Engineer",
        "Platform_Release_Evaluator",
        "Incident_Operations_Reporter",
        ("core.shell.run", "file.stat", "web.request"),
        ("pre-change health snapshot", "settlement and rollback evidence"),
    ),
)


def _selection(identity_id: str, scenario: _Scenario) -> CrewSelectionEvidence:
    return CrewSelectionEvidence(
        project_references=tuple(f"scope:{item}" for item in scenario.scope),
        competence_references=(f"profile:{identity_id}:{scenario.scenario_id}",),
        availability_references=(f"availability:{identity_id}:fixture",),
        conflict_assessment="No production, evaluation, reporting, or ownership conflict",
        risk_coverage=(f"risk:{scenario.scenario_id}",),
        independence_references=(f"independence:{identity_id}",),
    )


def _member(
    identity_id: str,
    kind: CrewAssignmentKind,
    scenario: _Scenario,
) -> MissionCrewMember:
    return MissionCrewMember(
        identity_id=identity_id,
        assignment_kind=kind,
        responsibility=f"Own {kind.value} for {scenario.scenario_id}",
        selection_evidence=_selection(identity_id, scenario),
    )


def _governance(
    runner: CrewAIMissionGovernanceRunner,
    scenario: _Scenario,
    template_reference: MissionTemplateReference,
) -> tuple[MissionRecord, MissionGovernanceResult]:
    organization = load_canonical_organization()
    mission = MissionRecord(
        origin=MissionOrigin(
            schema_version="1.1",
            kind=scenario.origin,
            actor_id=scenario.origin.value,
            objective=scenario.objective,
            source_references=(f"fixture:{scenario.scenario_id}:origin",),
            template_id=scenario.template_id,
            template_reference=template_reference,
        ),
        organization_id=organization.organization_id,
        organization_version=organization.organization_version,
    )
    collaboration = CrewAICollaborationProposal(
        production_shape=ProductionShape.SINGLE_OWNER,
        production_owner_ids=(scenario.producer,),
        alternatives=("single owner with independent downstream assurance",),
        interactions=(
            CollaborationEdge(
                source_identity=scenario.producer,
                target_identity=scenario.evaluator,
                interaction=CollaborationInteraction.HANDOFF,
                purpose=f"Submit {scenario.scenario_id} evidence for independent evaluation",
            ),
            CollaborationEdge(
                source_identity=scenario.evaluator,
                target_identity=scenario.reporter,
                interaction=CollaborationInteraction.HANDOFF,
                purpose=f"Submit accepted {scenario.scenario_id} evidence for reporting",
            ),
        ),
    )
    pm = PMMissionProposal(
        problem=scenario.problem,
        desired_outcome=scenario.outcome,
        scope=scenario.scope,
        exclusions=("production deployment",),
        acceptance_criteria=(f"{scenario.scenario_id} outcome is independently accepted",),
        constraints=(f"constraint:{scenario.scenario_id}",),
        risks=(f"risk:{scenario.scenario_id}",),
        authority_scope=scenario.scope,
        proposed_identity_ids=(scenario.producer, scenario.evaluator, scenario.reporter),
        evidence_requirements=scenario.evidence,
        escalation_conditions=(f"unresolved {scenario.scenario_id} authority boundary",),
        environment_intent=MissionEnvironmentIntent(
            known_locations=scenario.scope,
            target_platforms=(("linux",) if scenario.environment_dependent else ()),
            target_architectures=(("x86_64",) if scenario.environment_dependent else ()),
            required_evidence=(
                (f"environment:{scenario.scenario_id}:verification",)
                if scenario.environment_dependent
                else ()
            ),
            environment_dependent=scenario.environment_dependent,
        ),
        rationale=f"Product rationale for {scenario.scenario_id}",
        evidence_references=(f"fixture:{scenario.scenario_id}:product",),
        collaboration=collaboration,
    )
    cto = CTOMissionReview(
        disposition="confirmed",
        rationale=f"Technical coverage confirmed for {scenario.scenario_id}",
        evidence_references=(f"fixture:{scenario.scenario_id}:technical",),
        coverage=("technical", "platform", "security", "quality", "operability"),
        mission_lead_id=scenario.producer,
        approved_members=(
            _member(scenario.producer, CrewAssignmentKind.PRODUCTION, scenario),
            _member(scenario.evaluator, CrewAssignmentKind.EVALUATION, scenario),
            _member(scenario.reporter, CrewAssignmentKind.REPORTING, scenario),
        ),
        unresolved_findings=(),
        approved_collaboration=collaboration,
    )
    selection_references = tuple(
        reference
        for member in cto.approved_members
        for reference in (
            *member.selection_evidence.project_references,
            *member.selection_evidence.competence_references,
            *member.selection_evidence.availability_references,
            *member.selection_evidence.independence_references,
        )
    )
    return mission, runner.compile(
        mission,
        pm,
        cto,
        evidence_references=(
            f"fixture:{scenario.scenario_id}:product",
            f"fixture:{scenario.scenario_id}:technical",
            *selection_references,
        ),
    )


def _assignments(scenario: _Scenario, mission: MissionRecord) -> tuple[MissionTaskAssignment, ...]:
    task_prefix = scenario.scenario_id
    context_ids = scenario.scope if scenario.environment_dependent else ()
    return (
        MissionTaskAssignment(
            mission_id=mission.mission_id,
            crew_version=1,
            task_id=f"{task_prefix}-produce",
            accountable_owner=scenario.producer,
            assignment_kind=CrewAssignmentKind.PRODUCTION,
            expected_result=scenario.outcome,
            completion_criteria=("production result has attributable evidence",),
            environment_context_ids=context_ids,
            authority_scope=scenario.scope,
            exact_tools=scenario.tools,
            path_scopes=scenario.scope,
            limits=(MissionResourceLimit(name="wall_time", value=1_800, unit="seconds"),),
            required_evidence=scenario.evidence,
            requires_independent_evaluation=True,
        ),
        MissionTaskAssignment(
            mission_id=mission.mission_id,
            crew_version=1,
            task_id=f"{task_prefix}-evaluate",
            accountable_owner=scenario.evaluator,
            assignment_kind=CrewAssignmentKind.EVALUATION,
            expected_result=f"Independent evaluation of {scenario.outcome}",
            completion_criteria=("evaluation verdict is explicit",),
            dependencies=(f"{task_prefix}-produce",),
            authority_scope=scenario.scope,
            exact_tools=("file.read",),
            path_scopes=scenario.scope,
            limits=(MissionResourceLimit(name="wall_time", value=900, unit="seconds"),),
            required_evidence=(f"evaluation:{scenario.scenario_id}",),
        ),
        MissionTaskAssignment(
            mission_id=mission.mission_id,
            crew_version=1,
            task_id=f"{task_prefix}-report",
            accountable_owner=scenario.reporter,
            assignment_kind=CrewAssignmentKind.REPORTING,
            expected_result=f"Attributed report for {scenario.outcome}",
            completion_criteria=("report cites accepted evidence and residual risk",),
            dependencies=(f"{task_prefix}-evaluate",),
            authority_scope=scenario.scope,
            exact_tools=("file.read",),
            path_scopes=scenario.scope,
            limits=(MissionResourceLimit(name="wall_time", value=600, unit="seconds"),),
            required_evidence=(f"report:{scenario.scenario_id}",),
        ),
    )


def _environment_plan_signature(
    scenario_id: str,
    mission: MissionRecord,
    governance: MissionGovernanceResult,
) -> tuple[tuple[str, str, tuple[str, ...], tuple[str, ...]], ...]:
    brief = governance.brief
    crew = governance.crew
    assert crew is not None
    data = {
        "greenfield": (
            (
                "workspace:collaboration-service",
                "generate",
                ("devcontainer",),
                ("devcontainer-cli",),
                ("devcontainer.lifecycle",),
            ),
        ),
        "existing": (
            (
                "repository:billing-api",
                "reuse_existing",
                ("devcontainer",),
                ("devcontainer-cli",),
                ("devcontainer.lifecycle",),
            ),
        ),
        "multi-repository": (
            (
                "repository:identity-api",
                "reuse_existing",
                ("compose",),
                ("docker",),
                ("compose.lifecycle",),
            ),
            ("repository:identity-web", "host_native", (), ("node",), ("language.run",)),
        ),
        "platform": (
            (
                "platform:ci-arm64",
                "generate",
                ("containerfile",),
                ("podman",),
                ("oci.build",),
            ),
        ),
    }[scenario_id]
    contexts = []
    decisions = []
    for index, (context_id, outcome, formats, engines, semantics) in enumerate(data):
        selected_id = f"selected-{index}"
        evidence = f"fixture:{scenario_id}:environment:{index}"
        alternatives = (
            MissionEnvironmentAlternative(
                alternative_id=selected_id,
                requested_outcome=EnvironmentOutcome(outcome),
                required_semantics=semantics,
                allowed_descriptor_formats=formats,
                required_engine_ids=engines,
                eligible_engine_ids=engines,
                evidence_references=(evidence,),
            ),
            MissionEnvironmentAlternative(
                alternative_id=f"unresolved-{index}",
                requested_outcome=EnvironmentOutcome.UNRESOLVED,
                evidence_references=(evidence,),
            ),
        )
        contexts.append(
            MissionEnvironmentContextRequest(
                context_id=context_id,
                observation_id=uuid4(),
                observation_revision=1,
                observation_fingerprint=f"{index + 1:064x}",
                target_platform="linux",
                target_architecture="arm64" if scenario_id == "platform" else "x86_64",
                execution_location=f"fixture:{scenario_id}:{index}",
                affected_task_ids=(f"{scenario_id}-produce",),
                alternatives=alternatives,
                evidence_references=(evidence,),
            )
        )
        decisions.append(
            MissionEnvironmentDecisionOutput(
                context_id=context_id,
                selected_alternative_id=selected_id,
                requested_outcome=outcome,
                rationale=f"Contextual {scenario_id} environment decision {index}",
                constraints=(),
                declared_effects=(f"environment.{outcome}",),
                verification_checks=(f"verify:{scenario_id}:{index}",),
                cleanup_criteria=(f"cleanup:{scenario_id}:{index}",),
                alternatives_considered=(selected_id, f"unresolved-{index}"),
                unknowns=(),
                evidence_references=(evidence,),
            )
        )
    request = MissionEnvironmentPlanningRequest(
        mission_id=mission.mission_id,
        mission_revision=mission.revision,
        brief_version=brief.version,
        crew_version=crew.version,
        planning_task_id=f"{scenario_id}-produce",
        owner_identity=next(
            item.identity_id
            for item in crew.members
            if item.assignment_kind is CrewAssignmentKind.PRODUCTION
        ),
        contexts=tuple(contexts),
        evidence_references=(f"fixture:{scenario_id}:brief",),
    )
    plan = CrewAIMissionEnvironmentPlanningRunner.compile(
        request,
        MissionEnvironmentPlanningOutput(decisions=tuple(decisions)),
        plan_version=1,
        model_route="fixture-planning",
    )
    assert plan.lineage.runtime == "crewai-1.x"
    return tuple(
        (
            item.context_id,
            item.requested_outcome.value,
            item.allowed_descriptor_formats,
            item.required_engine_ids,
        )
        for item in plan.decisions
    )


@pytest.mark.acceptance
def test_contextual_missions_do_not_collapse_to_one_static_workflow(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(preset_text("local"), encoding="utf-8")
    runner = CrewAIMissionGovernanceRunner(ConfigLoader().load([config_path]).value)
    database = tmp_path / "mishkan.db"
    SchemaManager(database).initialize()
    repository = SQLiteMissionRepository(database)
    repository.record_organization(load_canonical_organization())
    templates = MissionTemplateService(
        MissionTemplateLoader().load(
            ("package://mishkan.resources.organization/mission-templates.yaml",), tmp_path
        )
    )

    briefs = set()
    crews = set()
    tools = set()
    evidence = set()
    environment_plans = {}
    for scenario in _SCENARIOS:
        assert [
            item.template_id
            for item in templates.applicable((scenario.signal,), organization_version="1")
        ] == [scenario.template_id]
        mission, governance = _governance(
            runner,
            scenario,
            templates.reference(scenario.template_id),
        )
        stored = repository.create_mission(mission)
        repository.record_brief(governance.brief, expected_revision=stored.revision)
        after_brief = repository.mission(str(mission.mission_id))
        assert governance.crew is not None
        repository.record_crew(governance.crew, expected_revision=after_brief.revision)
        assignments = _assignments(scenario, mission)
        for assignment in assignments:
            repository.record_assignment(assignment)

        briefs.add(
            (
                governance.brief.problem,
                governance.brief.desired_outcome,
                governance.brief.scope,
            )
        )
        crews.add(tuple(item.identity_id for item in governance.crew.members))
        tools.add(assignments[0].exact_tools)
        evidence.add(assignments[0].required_evidence)
        if scenario.scenario_id in {"greenfield", "existing", "multi-repository", "platform"}:
            environment_plans[scenario.scenario_id] = _environment_plan_signature(
                scenario.scenario_id,
                repository.mission(str(mission.mission_id)),
                governance,
            )

    assert len(briefs) == len(_SCENARIOS)
    assert len(crews) == len(_SCENARIOS)
    assert len(tools) == len(_SCENARIOS)
    assert len(evidence) == len(_SCENARIOS)
    assert len({signature for signature in environment_plans.values()}) == 4
    assert len(environment_plans["multi-repository"]) == 2
    assert templates.applicable(("novel-free-form-mission",), organization_version="1") == ()
