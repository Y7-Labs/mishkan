from pathlib import Path
from types import SimpleNamespace
from typing import Literal
from uuid import uuid4

import pytest

from mishkan.config.loader import ConfigLoader
from mishkan.config.models import MishkanConfig
from mishkan.config.presets import preset_text
from mishkan.conversations import EscalationOption
from mishkan.crewai.mission_governance import (
    CrewAICollaborationProposal,
    CrewAIMissionGovernanceRunner,
    CTOMissionReview,
    MissionGovernanceEvidence,
    MissionGovernanceRequest,
    PMMissionProposal,
)
from mishkan.domain.errors import ErrorCode, MishkanError
from mishkan.missions import (
    AdaptationKind,
    CollaborationEdge,
    CollaborationInteraction,
    CrewAssignmentKind,
    CrewSelectionEvidence,
    MissionCrewMember,
    MissionEnvironmentIntent,
    MissionOrigin,
    MissionOriginKind,
    MissionRecord,
    MissionResourceLimit,
    MissionTemplateLoader,
    MissionTemplateService,
    ProductionShape,
)
from mishkan.organization import load_canonical_organization


def _config(tmp_path: Path) -> MishkanConfig:
    source = tmp_path / "config.yaml"
    source.write_text(preset_text("local"), encoding="utf-8")
    return ConfigLoader().load([source]).value


def _mission() -> MissionRecord:
    organization = load_canonical_organization()
    return MissionRecord(
        origin=MissionOrigin(
            kind=MissionOriginKind.CEO,
            actor_id="CEO",
            objective="Implement durable account recovery",
        ),
        organization_id=organization.organization_id,
        organization_version=organization.organization_version,
    )


def _pm() -> PMMissionProposal:
    return PMMissionProposal(
        problem="Users cannot recover access after losing a credential",
        desired_outcome="A verified recovery flow with explicit residual risk",
        scope=("repository:api",),
        exclusions=("production deployment",),
        acceptance_criteria=("independent recovery test passes",),
        constraints=("preserve existing accounts",),
        risks=("account takeover",),
        authority_scope=("repository:api",),
        proposed_identity_ids=(
            "Backend_Service_Engineer",
            "Product_Functional_Evaluator",
            "Technical_Change_Reporter",
        ),
        evidence_requirements=("test report", "security review"),
        escalation_conditions=("material security tradeoff",),
        environment_intent=MissionEnvironmentIntent(
            required_evidence=("environment readiness",), environment_dependent=True
        ),
        rationale="The proposal covers the product outcome and independent acceptance",
        evidence_references=("evidence:product-analysis",),
        collaboration=CrewAICollaborationProposal(
            production_shape=ProductionShape.SINGLE_OWNER,
            production_owner_ids=("Backend_Service_Engineer",),
            alternatives=("single owner with downstream independent assurance",),
            interactions=(
                CollaborationEdge(
                    source_identity="Backend_Service_Engineer",
                    target_identity="Product_Functional_Evaluator",
                    interaction=CollaborationInteraction.HANDOFF,
                    purpose="Submit production evidence for independent evaluation",
                ),
                CollaborationEdge(
                    source_identity="Product_Functional_Evaluator",
                    target_identity="Technical_Change_Reporter",
                    interaction=CollaborationInteraction.HANDOFF,
                    purpose="Submit accepted evaluation evidence for reporting",
                ),
            ),
        ),
    )


def _member(identity_id: str, kind: CrewAssignmentKind) -> MissionCrewMember:
    return MissionCrewMember(
        identity_id=identity_id,
        assignment_kind=kind,
        responsibility=f"Perform attributable {kind.value} work",
        selection_evidence=CrewSelectionEvidence(
            project_references=("repository:api",),
            competence_references=(f"profile:{identity_id}:competence",),
            availability_references=(f"profile:{identity_id}:availability",),
            conflict_assessment="No production, evaluation, or reporting conflict",
            risk_coverage=("account recovery",),
            independence_references=(f"profile:{identity_id}:independence",),
        ),
    )


def _cto(
    disposition: Literal["confirmed", "rejected"] = "confirmed",
) -> CTOMissionReview:
    rejected = disposition == "rejected"
    return CTOMissionReview(
        disposition=disposition,
        rationale="Technical, security, quality, and reporting coverage is explicit",
        evidence_references=("evidence:technical-review",),
        coverage=("technical", "platform", "security", "quality", "operability"),
        mission_lead_id="Backend_Service_Engineer",
        approved_members=(
            _member("Backend_Service_Engineer", CrewAssignmentKind.PRODUCTION),
            _member("Product_Functional_Evaluator", CrewAssignmentKind.EVALUATION),
            _member("Technical_Change_Reporter", CrewAssignmentKind.REPORTING),
        ),
        unresolved_findings=() if not rejected else ("risk unresolved",),
        disputed_scope=() if not rejected else ("task:security-design",),
        alternatives=(
            ()
            if not rejected
            else (
                EscalationOption(
                    option_id="pm-proposal",
                    description="Proceed with the PM recovery design",
                    consequences=("delivery continues",),
                    risks=("security coverage remains disputed",),
                ),
                EscalationOption(
                    option_id="cto-remediation",
                    description="Add security remediation before implementation",
                    consequences=("delivery is delayed",),
                    risks=("product milestone may move",),
                ),
            )
        ),
        pm_recommended_option_id="pm-proposal" if rejected else None,
        cto_recommended_option_id="cto-remediation" if rejected else None,
        independent_work_continuing=("task:documentation",) if rejected else (),
        approved_collaboration=None if rejected else _pm().collaboration,
    )


def _governance_evidence_references() -> tuple[str, ...]:
    return (
        "evidence:product-analysis",
        "evidence:technical-review",
        *tuple(
            reference
            for member in _cto().approved_members
            for reference in (
                *member.selection_evidence.project_references,
                *member.selection_evidence.competence_references,
                *member.selection_evidence.availability_references,
                *member.selection_evidence.independence_references,
            )
        ),
    )


def test_pm_cto_outputs_compile_to_confirmed_brief_and_contextual_crew(tmp_path: Path) -> None:
    runner = CrewAIMissionGovernanceRunner(_config(tmp_path))
    result = runner.compile(
        _mission(),
        _pm(),
        _cto(),
        evidence_references=_governance_evidence_references(),
    )

    assert result.brief.pm_confirmation is not None
    assert result.brief.cto_confirmation is not None
    assert result.lineage.runtime == "crewai-1.x"
    assert result.lineage.pm_model_route == runner._config.crewai.mission_pm_model_route
    assert result.lineage.cto_model_route == runner._config.crewai.mission_cto_model_route
    assert result.crew.mission_lead_id == "Backend_Service_Engineer"
    assert {item.identity_id for item in result.crew.members} == set(result.brief.proposed_crew)
    assert result.collaboration == _pm().collaboration
    assert any(
        ref.startswith("crewai-output:") for ref in result.brief.pm_confirmation.evidence_references
    )
    plan = result.collaboration_plan(
        version=1,
        context_references=("context-pack:mission:1",),
        artifact_references=("artifact:brief",),
        resource_budgets=(MissionResourceLimit(name="model_tokens", value=20_000, unit="token"),),
        communication_budget=MissionResourceLimit(
            name="coordination_messages", value=20, unit="message"
        ),
        permitted_adaptations=(AdaptationKind.REPAIR_CONTEXT,),
        stop_conditions=("material authority change is required",),
        user_impact="Users recover access without unreviewed security risk",
    )
    assert plan.crewai_coordination_reference == str(result.lineage.coordination_id)
    assert plan.production_shape is ProductionShape.SINGLE_OWNER


def test_proposal_runs_bounded_pm_then_cto_crewai_work(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = CrewAIMissionGovernanceRunner(_config(tmp_path))
    calls: list[tuple[str, str, type[object]]] = []
    monkeypatch.setattr(
        runner._models,
        "candidates_for",
        lambda route_name: (SimpleNamespace(route_name=route_name),),
    )

    def crew_result(  # type: ignore[no-untyped-def]
        role, llm, _description, _expected_output, output_model
    ):
        calls.append((role.name, llm.route_name, output_model))
        return SimpleNamespace(pydantic=_pm() if role.name == "PM" else _cto(), raw="")

    monkeypatch.setattr(runner, "_crew", crew_result)

    result = runner.propose(
        _mission(),
        tuple(
            MissionGovernanceEvidence(
                reference=reference,
                summary=f"Attributable mission evidence for {reference}",
            )
            for reference in _governance_evidence_references()
        ),
    )

    assert result.disposition == "agreed"
    assert [identity for identity, _route, _model in calls] == ["PM", "CTO"]
    assert calls[0][1] == runner._config.crewai.mission_pm_model_route
    assert calls[1][1] == runner._config.crewai.mission_cto_model_route
    assert calls[0][2] is PMMissionProposal
    assert calls[1][2] is CTOMissionReview


def test_selected_optional_template_guidance_reaches_both_crewai_tasks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    templates = MissionTemplateService(
        MissionTemplateLoader().load(
            ("package://mishkan.resources.organization/mission-templates.yaml",), tmp_path
        )
    )
    reference = templates.reference("greenfield")
    organization = load_canonical_organization()
    mission = MissionRecord(
        origin=MissionOrigin(
            schema_version="1.1",
            kind=MissionOriginKind.CEO,
            actor_id="CEO",
            objective="Create a service in a prospective workspace",
            template_id=reference.template_id,
            template_reference=reference,
        ),
        organization_id=organization.organization_id,
        organization_version=organization.organization_version,
    )
    runner = CrewAIMissionGovernanceRunner(_config(tmp_path), mission_templates=templates)
    prompts: list[str] = []
    monkeypatch.setattr(
        runner._models,
        "candidates_for",
        lambda route_name: (SimpleNamespace(route_name=route_name),),
    )

    def crew_result(  # type: ignore[no-untyped-def]
        role, _llm, description, _expected_output, _output_model
    ):
        prompts.append(description)
        return SimpleNamespace(pydantic=_pm() if role.name == "PM" else _cto(), raw="")

    monkeypatch.setattr(runner, "_crew", crew_result)

    runner.propose(
        mission,
        tuple(
            MissionGovernanceEvidence(reference=item, summary=f"Evidence for {item}")
            for item in _governance_evidence_references()
        ),
    )

    assert len(prompts) == 2
    assert all('"template_id": "greenfield"' in prompt for prompt in prompts)
    assert all("Clarify the intended users" in prompt for prompt in prompts)
    assert all("fixed_crew" not in prompt and '"tasks"' not in prompt for prompt in prompts)


def test_cto_rejection_compiles_to_actionable_disagreement_without_a_crew(
    tmp_path: Path,
) -> None:
    runner = CrewAIMissionGovernanceRunner(_config(tmp_path))

    result = runner.compile(
        _mission(),
        _pm(),
        _cto("rejected"),
        evidence_references=_governance_evidence_references(),
    )

    assert result.disposition == "disagreement"
    assert result.brief.status.value == "rejected"
    assert result.crew is None
    assert result.disagreement is not None
    assert result.disagreement.blocked_scope == ("task:security-design",)
    assert result.disagreement.independent_work_continuing == ("task:documentation",)
    assert {item.identity_id for item in result.disagreement.recommendations} == {"PM", "CTO"}
    escalation = result.escalation(uuid4())
    assert escalation.blocked_scope == ("task:security-design",)
    assert escalation.independent_work_continuing == ("task:documentation",)
    assert escalation.raised_by == "PM+CTO"
    assert any(item.startswith("crewai-output:") for item in escalation.evidence_references)


def test_governance_disagreement_cannot_claim_blocked_work_as_independent(
    tmp_path: Path,
) -> None:
    runner = CrewAIMissionGovernanceRunner(_config(tmp_path))
    contradictory = _cto("rejected").model_copy(
        update={"independent_work_continuing": ("task:security-design",)}
    )

    with pytest.raises(ValueError, match="both block and continue"):
        runner.compile(
            _mission(),
            _pm(),
            contradictory,
            evidence_references=_governance_evidence_references(),
        )


def test_cto_cannot_silently_replace_the_pm_confirmed_composition(tmp_path: Path) -> None:
    runner = CrewAIMissionGovernanceRunner(_config(tmp_path))
    changed = _cto().model_copy(
        update={
            "approved_members": (
                _member("Backend_Service_Engineer", CrewAssignmentKind.PRODUCTION),
                _member("Software_Technical_Evaluator", CrewAssignmentKind.EVALUATION),
            )
        }
    )

    with pytest.raises(MishkanError) as error:
        runner.compile(
            _mission(),
            _pm(),
            changed,
            evidence_references=_governance_evidence_references(),
        )
    assert error.value.envelope.code is ErrorCode.MISSION


def test_governance_rejects_evidence_references_absent_from_the_input(tmp_path: Path) -> None:
    runner = CrewAIMissionGovernanceRunner(_config(tmp_path))

    with pytest.raises(MishkanError) as error:
        runner.compile(
            _mission(),
            _pm(),
            _cto(),
            evidence_references=tuple(
                reference
                for reference in _governance_evidence_references()
                if reference != "evidence:technical-review"
            ),
        )

    assert error.value.envelope.code is ErrorCode.PLAN
    assert error.value.envelope.details == {"references": ["evidence:technical-review"]}


def test_governance_rejects_unattributed_crew_selection_evidence(tmp_path: Path) -> None:
    runner = CrewAIMissionGovernanceRunner(_config(tmp_path))
    cto = _cto()
    first = cto.approved_members[0]
    invented = first.model_copy(
        update={
            "selection_evidence": first.selection_evidence.model_copy(
                update={"availability_references": ("availability:invented",)}
            )
        }
    )
    changed = cto.model_copy(update={"approved_members": (invented, *cto.approved_members[1:])})

    with pytest.raises(MishkanError) as error:
        runner.compile(
            _mission(),
            _pm(),
            changed,
            evidence_references=_governance_evidence_references(),
        )

    assert error.value.envelope.code is ErrorCode.PLAN
    assert error.value.envelope.details == {"references": ["availability:invented"]}


def test_confirmed_cto_review_requires_complete_i06_coverage() -> None:
    with pytest.raises(ValueError, match="technical, platform, security"):
        CTOMissionReview.model_validate(
            _cto().model_dump() | {"coverage": ("technical", "quality")}
        )


def test_governance_request_rejects_duplicate_evidence_references() -> None:
    evidence = MissionGovernanceEvidence(
        reference="artifact:discovery",
        summary="Repository discovery",
    )

    with pytest.raises(ValueError, match="evidence references must be unique"):
        MissionGovernanceRequest(
            mission_id=_mission().mission_id,
            mission_revision=1,
            evidence=(evidence, evidence),
        )
