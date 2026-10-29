"""CrewAI PM/CTO proposal boundary with deterministic MISHKAN compilation."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Literal, Protocol, TypeVar, cast
from uuid import UUID

from crewai import LLM, Agent, Crew, Process, Task
from crewai.crews.crew_output import CrewOutput
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from mishkan.config.models import MishkanConfig
from mishkan.conversations import (
    EscalationOption,
    ExecutiveRecommendation,
    MissionEscalation,
)
from mishkan.crewai.routing import CrewAIModelRouter
from mishkan.domain.errors import ErrorCode, MishkanError
from mishkan.domain.identity import new_id
from mishkan.domain.time import require_aware, utc_now
from mishkan.missions import (
    AdaptationKind,
    CollaborationEdge,
    CollaborationResponsibility,
    CrewAssignmentKind,
    ExecutiveConfirmation,
    MissionBrief,
    MissionBriefStatus,
    MissionCollaborationPlan,
    MissionCrewMember,
    MissionCrewRevision,
    MissionEnvironmentIntent,
    MissionRecord,
    MissionResourceLimit,
    MissionTemplateService,
    ProductionShape,
)
from mishkan.organization import OrganizationRosterDefinition, load_canonical_organization
from mishkan.organization.models import RoleDefinition

OutputT = TypeVar("OutputT", bound=BaseModel)


class GovernanceOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class MissionGovernanceEvidence(GovernanceOutput):
    reference: str = Field(min_length=1, max_length=2_048)
    summary: str = Field(min_length=1, max_length=8_192)


class CrewAICollaborationProposal(GovernanceOutput):
    """Attributable CrewAI proposal; MISHKAN still validates and authorizes it."""

    production_shape: ProductionShape
    production_owner_ids: tuple[str, ...] = Field(min_length=1)
    collaboration_reasons: tuple[str, ...] = ()
    alternatives: tuple[str, ...] = Field(min_length=1)
    interactions: tuple[CollaborationEdge, ...] = ()

    @model_validator(mode="after")
    def production_shape_is_explicit(self) -> CrewAICollaborationProposal:
        owners = self.production_owner_ids
        if len(owners) != len(set(owners)):
            raise ValueError("collaboration proposal owners must be unique")
        if self.production_shape is ProductionShape.MULTI_PRODUCER:
            if len(owners) < 2 or not self.collaboration_reasons or not self.interactions:
                raise ValueError(
                    "multi-producer proposal requires owners, reasons, and interactions"
                )
        elif len(owners) != 1:
            raise ValueError("single-owner and consultative proposals require one owner")
        if self.production_shape is not ProductionShape.SINGLE_OWNER and not (
            self.collaboration_reasons
        ):
            raise ValueError("multi-agent collaboration requires an explicit reason")
        return self


class MissionGovernanceRequest(GovernanceOutput):
    schema_version: Literal["1.0"] = "1.0"
    request_id: UUID = Field(default_factory=new_id)
    mission_id: UUID
    mission_revision: int = Field(ge=1)
    evidence: tuple[MissionGovernanceEvidence, ...] = Field(min_length=1)
    requested_at: datetime = Field(default_factory=utc_now)

    @field_validator("requested_at")
    @classmethod
    def _validate_timestamp(cls, value: datetime) -> datetime:
        return require_aware(value)

    @field_validator("evidence")
    @classmethod
    def _evidence_references_are_unique(
        cls, value: tuple[MissionGovernanceEvidence, ...]
    ) -> tuple[MissionGovernanceEvidence, ...]:
        references = tuple(item.reference for item in value)
        if len(references) != len(set(references)):
            raise ValueError("mission governance evidence references must be unique")
        return value


class PMMissionProposal(GovernanceOutput):
    problem: str = Field(min_length=3, max_length=8_192)
    desired_outcome: str = Field(min_length=3, max_length=8_192)
    scope: tuple[str, ...] = Field(min_length=1)
    exclusions: tuple[str, ...]
    acceptance_criteria: tuple[str, ...] = Field(min_length=1)
    constraints: tuple[str, ...]
    risks: tuple[str, ...]
    authority_scope: tuple[str, ...] = Field(min_length=1)
    proposed_identity_ids: tuple[str, ...] = Field(min_length=2)
    evidence_requirements: tuple[str, ...] = Field(min_length=1)
    escalation_conditions: tuple[str, ...] = Field(min_length=1)
    environment_intent: MissionEnvironmentIntent
    rationale: str = Field(min_length=3, max_length=4_096)
    evidence_references: tuple[str, ...] = Field(min_length=1)
    collaboration: CrewAICollaborationProposal


class CTOMissionReview(GovernanceOutput):
    disposition: Literal["confirmed", "rejected"]
    rationale: str = Field(min_length=3, max_length=4_096)
    evidence_references: tuple[str, ...] = Field(min_length=1)
    coverage: tuple[str, ...] = Field(min_length=1)
    mission_lead_id: str = Field(min_length=2, max_length=128)
    approved_members: tuple[MissionCrewMember, ...] = Field(min_length=2)
    unresolved_findings: tuple[str, ...]
    disputed_scope: tuple[str, ...] = ()
    alternatives: tuple[EscalationOption, ...] = ()
    pm_recommended_option_id: str | None = None
    cto_recommended_option_id: str | None = None
    independent_work_continuing: tuple[str, ...] = ()
    approved_collaboration: CrewAICollaborationProposal | None = None

    @model_validator(mode="after")
    def rejection_is_actionable(self) -> CTOMissionReview:
        if self.disposition == "confirmed":
            if self.approved_collaboration is None:
                raise ValueError("confirmed CTO review requires an approved collaboration proposal")
            normalized = " ".join(self.coverage).lower()
            missing = {
                required
                for required in (
                    "technical",
                    "platform",
                    "security",
                    "quality",
                    "operability",
                )
                if required not in normalized
            }
            if missing:
                raise ValueError(
                    "confirmed CTO review must cover technical, platform, security, quality, "
                    "and operability"
                )
            return self
        if self.approved_collaboration is not None:
            raise ValueError("rejected CTO review cannot approve a collaboration proposal")
        option_ids = {item.option_id for item in self.alternatives}
        if (
            not self.unresolved_findings
            or not self.disputed_scope
            or len(self.alternatives) < 2
            or self.pm_recommended_option_id not in option_ids
            or self.cto_recommended_option_id not in option_ids
        ):
            raise ValueError(
                "rejected CTO review requires disputed scope, two options, findings, and "
                "PM/CTO recommendations"
            )
        return self


class MissionGovernanceDisagreement(GovernanceOutput):
    decision_required: str = Field(min_length=3, max_length=8_192)
    reason: str = Field(min_length=3, max_length=8_192)
    blocked_scope: tuple[str, ...] = Field(min_length=1)
    options: tuple[EscalationOption, ...] = Field(min_length=2)
    recommendations: tuple[ExecutiveRecommendation, ...] = Field(min_length=2, max_length=2)
    uncertainty: tuple[str, ...] = Field(min_length=1)
    independent_work_continuing: tuple[str, ...]
    requires_ceo_escalation: bool = True

    @model_validator(mode="after")
    def independent_work_is_not_blocked(self) -> MissionGovernanceDisagreement:
        if set(self.blocked_scope).intersection(self.independent_work_continuing):
            raise ValueError(
                "governance disagreement cannot both block and continue the same scope"
            )
        return self


class CrewAIGovernanceLineage(GovernanceOutput):
    runtime: Literal["crewai-1.x"] = "crewai-1.x"
    coordination_id: UUID = Field(default_factory=new_id)
    pm_task_id: UUID = Field(default_factory=new_id)
    cto_task_id: UUID = Field(default_factory=new_id)
    pm_model_route: str = Field(min_length=1, max_length=256)
    cto_model_route: str = Field(min_length=1, max_length=256)
    pm_output_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    cto_output_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    produced_at: datetime = Field(default_factory=utc_now)

    @field_validator("produced_at")
    @classmethod
    def produced_at_is_aware(cls, value: datetime) -> datetime:
        return require_aware(value)


class MissionGovernanceResult(GovernanceOutput):
    disposition: Literal["agreed", "disagreement"] = "agreed"
    mission: MissionRecord
    brief: MissionBrief
    crew: MissionCrewRevision | None
    disagreement: MissionGovernanceDisagreement | None = None
    collaboration: CrewAICollaborationProposal | None = None
    lineage: CrewAIGovernanceLineage

    @model_validator(mode="after")
    def disposition_matches_payload(self) -> MissionGovernanceResult:
        if self.disposition == "agreed" and (
            self.crew is None or self.disagreement is not None or self.collaboration is None
        ):
            raise ValueError("agreed governance requires a crew and no disagreement")
        if self.disposition == "disagreement" and (
            self.crew is not None or self.disagreement is None or self.collaboration is not None
        ):
            raise ValueError("governance disagreement requires escalation data and no crew")
        return self

    def collaboration_plan(
        self,
        *,
        version: int,
        context_references: tuple[str, ...],
        artifact_references: tuple[str, ...],
        resource_budgets: tuple[MissionResourceLimit, ...],
        communication_budget: MissionResourceLimit,
        permitted_adaptations: tuple[AdaptationKind, ...],
        stop_conditions: tuple[str, ...],
        user_impact: str,
    ) -> MissionCollaborationPlan:
        if self.crew is None or self.collaboration is None:
            raise ValueError("governance disagreement cannot create a collaboration plan")
        responsibilities = tuple(
            CollaborationResponsibility(
                identity_id=member.identity_id,
                responsibility=member.responsibility,
                decision_rights=(f"assignment:{member.assignment_kind.value}",),
            )
            for member in self.crew.members
        )
        return MissionCollaborationPlan(
            mission_id=self.mission.mission_id,
            version=version,
            brief_version=self.brief.version,
            crew_version=self.crew.version,
            production_shape=self.collaboration.production_shape,
            production_owner_ids=self.collaboration.production_owner_ids,
            collaboration_reasons=self.collaboration.collaboration_reasons,
            proposal_author_identity="PM",
            crewai_coordination_reference=str(self.lineage.coordination_id),
            alternatives=self.collaboration.alternatives,
            shared_outcome=self.brief.desired_outcome,
            user_impact=user_impact,
            responsibilities=responsibilities,
            interactions=self.collaboration.interactions,
            context_references=context_references,
            artifact_references=artifact_references,
            resource_budgets=resource_budgets,
            communication_budget=communication_budget,
            escalation_conditions=self.brief.escalation_conditions,
            permitted_adaptations=permitted_adaptations,
            stop_conditions=stop_conditions,
            pm_confirmation_id=self.crew.pm_composition_confirmation_id,
            cto_confirmation_id=self.crew.cto_coverage_confirmation_id,
        )

    def escalation(self, conversation_id: UUID) -> MissionEscalation:
        if self.disagreement is None:
            raise ValueError("agreed mission governance has no escalation")
        evidence = tuple(
            dict.fromkeys(
                (
                    f"mission-brief:{self.brief.fingerprint}",
                    f"crewai-output:{self.lineage.pm_output_fingerprint}",
                    f"crewai-output:{self.lineage.cto_output_fingerprint}",
                    *(
                        reference
                        for recommendation in self.disagreement.recommendations
                        for reference in recommendation.evidence_references
                    ),
                )
            )
        )
        return MissionEscalation(
            mission_id=self.mission.mission_id,
            conversation_id=conversation_id,
            raised_by="PM+CTO",
            blocked_scope=self.disagreement.blocked_scope,
            decision_required=self.disagreement.decision_required,
            reason=self.disagreement.reason,
            options=self.disagreement.options,
            recommendations=self.disagreement.recommendations,
            uncertainty=self.disagreement.uncertainty,
            independent_work_continuing=self.disagreement.independent_work_continuing,
            evidence_references=evidence,
        )


class MissionGovernanceRunner(Protocol):
    def propose(
        self, mission: MissionRecord, evidence: tuple[MissionGovernanceEvidence, ...]
    ) -> MissionGovernanceResult: ...


class CrewAIMissionGovernanceRunner:
    """Run attributable PM then CTO work through CrewAI's production runtime."""

    def __init__(
        self,
        config: MishkanConfig,
        organization: OrganizationRosterDefinition | None = None,
        mission_templates: MissionTemplateService | None = None,
    ) -> None:
        self._config = config
        self._models = CrewAIModelRouter(config)
        self._organization = organization or load_canonical_organization()
        self._mission_templates = mission_templates

    def propose(
        self, mission: MissionRecord, evidence: tuple[MissionGovernanceEvidence, ...]
    ) -> MissionGovernanceResult:
        pm = self._kickoff_structured(
            identity_id="PM",
            route_name=self._config.crewai.mission_pm_model_route,
            description=self._pm_prompt(mission, evidence),
            expected_output="One complete product Mission Brief proposal grounded in the evidence.",
            output_model=PMMissionProposal,
        )
        cto = self._kickoff_structured(
            identity_id="CTO",
            route_name=self._config.crewai.mission_cto_model_route,
            description=self._cto_prompt(mission, pm, evidence),
            expected_output="One technical, security, and quality coverage decision.",
            output_model=CTOMissionReview,
        )
        return self.compile(
            mission,
            pm,
            cto,
            evidence_references=tuple(item.reference for item in evidence),
        )

    def compile(
        self,
        mission: MissionRecord,
        pm: PMMissionProposal,
        cto: CTOMissionReview,
        *,
        evidence_references: tuple[str, ...],
    ) -> MissionGovernanceResult:
        proposed = tuple(pm.proposed_identity_ids)
        approved = tuple(member.identity_id for member in cto.approved_members)
        if len(proposed) != len(set(proposed)):
            raise MishkanError(ErrorCode.MISSION, "PM proposed duplicate Mission Crew identities")
        if cto.disposition == "confirmed" and set(proposed) != set(approved):
            raise MishkanError(
                ErrorCode.MISSION,
                "PM and CTO did not confirm the same Mission Crew composition",
            )
        if cto.disposition == "confirmed" and cto.approved_collaboration != pm.collaboration:
            raise MishkanError(
                ErrorCode.MISSION,
                "PM and CTO did not confirm the same collaboration proposal",
            )
        collaboration_identities = {
            *pm.collaboration.production_owner_ids,
            *(edge.source_identity for edge in pm.collaboration.interactions),
            *(edge.target_identity for edge in pm.collaboration.interactions),
        }
        if unknown_collaborators := collaboration_identities - set(proposed):
            raise MishkanError(
                ErrorCode.ROLE_CONFLICT,
                "collaboration proposal references identities outside the proposed crew",
                details={"unknown": sorted(unknown_collaborators)},
            )
        approved_producers = {
            member.identity_id
            for member in cto.approved_members
            if member.assignment_kind
            in {CrewAssignmentKind.PRODUCTION, CrewAssignmentKind.CONTRIBUTOR}
        }
        if cto.disposition == "confirmed" and not set(
            pm.collaboration.production_owner_ids
        ).issubset(approved_producers):
            raise MishkanError(
                ErrorCode.ROLE_CONFLICT,
                "collaboration production owners lack a production assignment",
            )
        known = {item.identity_id for item in self._organization.identities}
        if unknown := set(proposed) - known:
            raise MishkanError(
                ErrorCode.MISSION,
                "mission proposal references unknown professional identities",
                details={"unknown": sorted(unknown)},
            )
        allowed_evidence = set(evidence_references)
        crew_selection_references = {
            reference
            for member in cto.approved_members
            for reference in (
                *member.selection_evidence.project_references,
                *member.selection_evidence.competence_references,
                *member.selection_evidence.availability_references,
                *member.selection_evidence.independence_references,
            )
        }
        unattributed = (
            set(pm.evidence_references) | set(cto.evidence_references) | crew_selection_references
        ) - allowed_evidence
        if unattributed:
            raise MishkanError(
                ErrorCode.PLAN,
                "CrewAI mission governance output invented an evidence reference",
                details={"references": sorted(unattributed)},
            )
        pm_fingerprint = self._fingerprint(pm)
        cto_fingerprint = self._fingerprint(cto)
        lineage = CrewAIGovernanceLineage(
            pm_model_route=self._config.crewai.mission_pm_model_route,
            cto_model_route=self._config.crewai.mission_cto_model_route,
            pm_output_fingerprint=pm_fingerprint,
            cto_output_fingerprint=cto_fingerprint,
        )
        pm_confirmation = ExecutiveConfirmation(
            identity_id="PM",
            disposition="confirmed",
            rationale=pm.rationale,
            evidence_references=(*pm.evidence_references, f"crewai-output:{pm_fingerprint}"),
            coverage=("product", "developer-experience", "composition"),
        )
        cto_confirmation = ExecutiveConfirmation(
            identity_id="CTO",
            disposition=cto.disposition,
            rationale=cto.rationale,
            evidence_references=(*cto.evidence_references, f"crewai-output:{cto_fingerprint}"),
            coverage=cto.coverage,
        )
        brief = MissionBrief(
            mission_id=mission.mission_id,
            version=(mission.current_brief_version or 0) + 1,
            organization_id=mission.organization_id,
            organization_version=mission.organization_version,
            status=(
                MissionBriefStatus.CONFIRMED
                if cto.disposition == "confirmed"
                else MissionBriefStatus.REJECTED
            ),
            objective=mission.origin.objective,
            problem=pm.problem,
            desired_outcome=pm.desired_outcome,
            scope=pm.scope,
            exclusions=pm.exclusions,
            acceptance_criteria=pm.acceptance_criteria,
            constraints=pm.constraints,
            risks=pm.risks,
            authority_scope=pm.authority_scope,
            proposed_crew=proposed,
            evidence_requirements=pm.evidence_requirements,
            escalation_conditions=pm.escalation_conditions,
            environment_intent=pm.environment_intent,
            pm_confirmation=pm_confirmation,
            cto_confirmation=cto_confirmation,
        )
        if cto.disposition == "rejected":
            assert cto.pm_recommended_option_id is not None
            assert cto.cto_recommended_option_id is not None
            disagreement = MissionGovernanceDisagreement(
                decision_required="Resolve PM and CTO disagreement before disputed work continues",
                reason=cto.rationale,
                blocked_scope=cto.disputed_scope,
                options=cto.alternatives,
                recommendations=(
                    ExecutiveRecommendation(
                        identity_id="PM",
                        recommended_option_id=cto.pm_recommended_option_id,
                        rationale=pm.rationale,
                        evidence_references=pm.evidence_references,
                    ),
                    ExecutiveRecommendation(
                        identity_id="CTO",
                        recommended_option_id=cto.cto_recommended_option_id,
                        rationale=cto.rationale,
                        evidence_references=cto.evidence_references,
                    ),
                ),
                uncertainty=cto.unresolved_findings,
                independent_work_continuing=cto.independent_work_continuing,
            )
            return MissionGovernanceResult(
                disposition="disagreement",
                mission=mission,
                brief=brief,
                crew=None,
                disagreement=disagreement,
                collaboration=None,
                lineage=lineage,
            )
        crew = MissionCrewRevision(
            mission_id=mission.mission_id,
            version=(mission.current_crew_version or 0) + 1,
            organization_id=mission.organization_id,
            organization_version=mission.organization_version,
            brief_version=brief.version,
            mission_lead_id=cto.mission_lead_id,
            members=cto.approved_members,
            pm_composition_confirmation_id=pm_confirmation.confirmation_id,
            cto_coverage_confirmation_id=cto_confirmation.confirmation_id,
            revision_reason="PM/CTO CrewAI proposal compiled from attributable evidence",
        )
        return MissionGovernanceResult(
            disposition="agreed",
            mission=mission,
            brief=brief,
            crew=crew,
            disagreement=None,
            collaboration=pm.collaboration,
            lineage=lineage,
        )

    def _kickoff_structured(
        self,
        *,
        identity_id: str,
        route_name: str,
        description: str,
        expected_output: str,
        output_model: type[OutputT],
    ) -> OutputT:
        failures: list[str] = []
        role = self._role(identity_id, route_name)
        for llm in self._models.candidates_for(route_name):
            prompt = description
            for _attempt in range(self._config.crewai.structured_output_retries + 1):
                try:
                    output = self._crew(role, llm, prompt, expected_output, output_model)
                    if isinstance(output.pydantic, output_model):
                        return output.pydantic
                    return output_model.model_validate_json(output.raw)
                except Exception as exc:
                    failures.append(type(exc).__name__)
                    prompt = description + "\nReturn one complete value matching the JSON Schema."
        raise MishkanError(
            ErrorCode.REQUIRED_DEPENDENCY,
            "all configured CrewAI mission-governance candidates failed",
            details={"identity_id": identity_id, "failure_types": failures},
            retryable=True,
        )

    def _crew(
        self,
        role: RoleDefinition,
        llm: LLM,
        description: str,
        expected_output: str,
        output_model: type[OutputT],
    ) -> CrewOutput:
        agent = Agent(
            role=role.name,
            goal=role.goal,
            backstory=role.backstory,
            llm=llm,
            tools=[],
            allow_delegation=False,
            allow_code_execution=False,
            max_iter=self._config.crewai.max_agent_iterations,
            max_retry_limit=0,
            verbose=False,
        )
        task = Task(
            description=description,
            expected_output=expected_output,
            agent=agent,
            tools=[],
            output_pydantic=output_model,
        )
        return cast(
            CrewOutput,
            Crew(
                agents=[agent],
                tasks=[task],
                process=Process.sequential,
                tracing=self._config.crewai.tracing,
                verbose=False,
            ).kickoff(),
        )

    def _role(self, identity_id: str, route_name: str) -> RoleDefinition:
        identity = next(
            (item for item in self._organization.identities if item.identity_id == identity_id),
            None,
        )
        if identity is None:
            raise MishkanError(ErrorCode.ROLE_CONFLICT, "required executive identity is absent")
        return RoleDefinition(
            name=identity.identity_id,
            goal=identity.responsibility,
            backstory=(
                f"Persistent {identity.identity_id} professional identity in organization "
                f"{self._organization.organization_id}@{self._organization.organization_version}."
            ),
            model_route=route_name,
        )

    def _pm_prompt(
        self, mission: MissionRecord, evidence: tuple[MissionGovernanceEvidence, ...]
    ) -> str:
        return (
            "Produce the product Mission Brief proposal for this mission. Do not invent evidence, "
            "authority, tools, or a fixed workflow. Select identities only from the supplied "
            "roster.\n"
            f"Mission: {mission.model_dump_json()}\n"
            f"Optional template guidance: {self._template_projection(mission)}\n"
            f"Roster: {self._roster_projection()}\n"
            f"Evidence: {self._evidence_projection(evidence)}"
        )

    def _cto_prompt(
        self,
        mission: MissionRecord,
        pm: PMMissionProposal,
        evidence: tuple[MissionGovernanceEvidence, ...],
    ) -> str:
        return (
            "Independently confirm or reject technical, security, quality, operability, and "
            "independence coverage. Keep exactly the PM-proposed identities when confirming; "
            "otherwise reject with findings, disputed scope, at least two real options with "
            "consequences and risks, both PM and CTO recommendations, and the independent work "
            "that can continue. Do not grant tools or authority.\n"
            f"Mission: {mission.model_dump_json()}\nPM proposal: {pm.model_dump_json()}\n"
            f"Optional template guidance: {self._template_projection(mission)}\n"
            f"Roster: {self._roster_projection()}\n"
            f"Evidence: {self._evidence_projection(evidence)}"
        )

    @staticmethod
    def _evidence_projection(evidence: tuple[MissionGovernanceEvidence, ...]) -> str:
        return json.dumps(
            [item.model_dump(mode="json") for item in evidence],
            sort_keys=True,
        )

    def _roster_projection(self) -> str:
        return json.dumps(
            [
                {
                    "identity_id": item.identity_id,
                    "responsibility": item.responsibility,
                    "independence_class": item.independence_class.value,
                    "mission_lead_eligible": item.mission_lead_eligible,
                }
                for item in self._organization.identities
            ],
            sort_keys=True,
        )

    def _template_projection(self, mission: MissionRecord) -> str:
        reference = mission.origin.template_reference
        if reference is None:
            return json.dumps({"selected": False}, sort_keys=True)
        if self._mission_templates is None:
            raise MishkanError(
                ErrorCode.REQUIRED_DEPENDENCY,
                "selected mission template requires the configured template catalogue",
            )
        template = self._mission_templates.resolve(reference)
        return json.dumps(
            {
                "selected": True,
                "template_id": template.template_id,
                "version": template.version,
                "objective_guidance": template.objective_guidance,
                "constraint_guidance": template.constraint_guidance,
                "risk_guidance": template.risk_guidance,
                "evidence_guidance": template.evidence_guidance,
                "completion_guidance": template.completion_guidance,
                "required_responsibility_classes": template.required_responsibility_classes,
                "declared_configuration_conditions": template.declared_configuration_conditions,
                "declared_skill_conditions": template.declared_skill_conditions,
                "declared_tool_conditions": template.declared_tool_conditions,
                "declared_execution_conditions": template.declared_execution_conditions,
                "validation_expectations": template.validation_expectations,
                "reporting_expectations": template.reporting_expectations,
            },
            sort_keys=True,
        )

    @staticmethod
    def _fingerprint(value: BaseModel) -> str:
        return hashlib.sha256(value.model_dump_json().encode()).hexdigest()
