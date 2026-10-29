"""Independent CrewAI milestone evaluation over deterministic team evidence."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Protocol, TypeVar, cast

from crewai import LLM, Agent, Crew, Process, Task
from crewai.crews.crew_output import CrewOutput
from pydantic import BaseModel, ConfigDict, Field

from mishkan.config.models import MishkanConfig
from mishkan.crewai.routing import CrewAIModelRouter
from mishkan.domain.errors import ErrorCode, MishkanError
from mishkan.domain.time import require_aware
from mishkan.missions import (
    MissionCollaborationPlan,
    TeamDimension,
    TeamDimensionAssessment,
    TeamEffectivenessValidator,
    TeamHealthEvaluation,
    TeamHealthStatus,
    TeamSignal,
)
from mishkan.organization import OrganizationRosterDefinition, load_canonical_organization
from mishkan.organization.models import RoleDefinition

OutputT = TypeVar("OutputT", bound=BaseModel)


class TeamHealthAssessmentOutput(BaseModel):
    """Model interpretation only; MISHKAN supplies lineage and validates authority."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    assessments: tuple[TeamDimensionAssessment, ...] = Field(min_length=7, max_length=7)
    strengths: tuple[str, ...]
    risks: tuple[str, ...]
    limitations: tuple[str, ...] = Field(min_length=1)
    confidence_basis: str = Field(min_length=3, max_length=4_096)
    status: TeamHealthStatus
    evidence_references: tuple[str, ...] = Field(min_length=1)


class TeamHealthEvaluator(Protocol):
    def evaluate(
        self,
        plan: MissionCollaborationPlan,
        signals: tuple[TeamSignal, ...],
        *,
        evaluator_identity: str,
        interval_started_at: datetime,
        interval_ended_at: datetime,
        evidence_cursor: int,
        evidence_references: tuple[str, ...],
    ) -> TeamHealthEvaluation: ...


class CrewAITeamHealthEvaluator:
    """Run one bounded independent evaluation outside task-completion transactions."""

    def __init__(
        self,
        config: MishkanConfig,
        organization: OrganizationRosterDefinition | None = None,
    ) -> None:
        self._config = config
        self._models = CrewAIModelRouter(config)
        self._organization = organization or load_canonical_organization()

    def evaluate(
        self,
        plan: MissionCollaborationPlan,
        signals: tuple[TeamSignal, ...],
        *,
        evaluator_identity: str,
        interval_started_at: datetime,
        interval_ended_at: datetime,
        evidence_cursor: int,
        evidence_references: tuple[str, ...],
    ) -> TeamHealthEvaluation:
        interval_started_at = require_aware(interval_started_at)
        interval_ended_at = require_aware(interval_ended_at)
        if not evidence_references:
            raise ValueError("team-health evaluation requires attributable evidence")
        role = self._role(evaluator_identity)
        failures: list[str] = []
        route_name = self._config.crewai.mission_cto_model_route
        for llm in self._models.candidates_for(route_name):
            prompt = self._prompt(plan, signals, evidence_references)
            for _attempt in range(self._config.crewai.structured_output_retries + 1):
                try:
                    output = self._crew(role, llm, prompt)
                    interpreted = (
                        output.pydantic
                        if isinstance(output.pydantic, TeamHealthAssessmentOutput)
                        else TeamHealthAssessmentOutput.model_validate_json(output.raw)
                    )
                    return self.compile(
                        plan,
                        interpreted,
                        evaluator_identity=evaluator_identity,
                        interval_started_at=interval_started_at,
                        interval_ended_at=interval_ended_at,
                        evidence_cursor=evidence_cursor,
                        allowed_evidence_references=evidence_references,
                        model_route=route_name,
                    )
                except Exception as exc:
                    failures.append(type(exc).__name__)
                    prompt = self._prompt(plan, signals, evidence_references) + (
                        "\nReturn exactly one complete value matching the JSON Schema."
                    )
        return self.uncertain(
            plan,
            evaluator_identity=evaluator_identity,
            interval_started_at=interval_started_at,
            interval_ended_at=interval_ended_at,
            evidence_cursor=evidence_cursor,
            evidence_references=evidence_references,
            failure_types=tuple(failures),
        )

    @staticmethod
    def compile(
        plan: MissionCollaborationPlan,
        output: TeamHealthAssessmentOutput,
        *,
        evaluator_identity: str,
        interval_started_at: datetime,
        interval_ended_at: datetime,
        evidence_cursor: int,
        allowed_evidence_references: tuple[str, ...],
        model_route: str,
    ) -> TeamHealthEvaluation:
        unknown = set(output.evidence_references) - set(allowed_evidence_references)
        if unknown:
            raise MishkanError(
                ErrorCode.PLAN,
                "CrewAI team-health evaluation invented an evidence reference",
                details={"references": sorted(unknown)},
            )
        fingerprint = hashlib.sha256(
            json.dumps(output.model_dump(mode="json"), sort_keys=True).encode()
        ).hexdigest()
        evaluation = TeamHealthEvaluation(
            mission_id=plan.mission_id,
            collaboration_plan_version=plan.version,
            interval_started_at=interval_started_at,
            interval_ended_at=interval_ended_at,
            evidence_cursor=evidence_cursor,
            assessments=output.assessments,
            strengths=output.strengths,
            risks=output.risks,
            limitations=output.limitations,
            confidence_basis=(
                f"{output.confidence_basis}; CrewAI route {model_route}; "
                f"output sha256:{fingerprint}"
            ),
            status=output.status,
            evaluator_identity=evaluator_identity,
            evidence_references=output.evidence_references,
        )
        TeamEffectivenessValidator.health_evaluation(evaluation, plan=plan)
        return evaluation

    @staticmethod
    def uncertain(
        plan: MissionCollaborationPlan,
        *,
        evaluator_identity: str,
        interval_started_at: datetime,
        interval_ended_at: datetime,
        evidence_cursor: int,
        evidence_references: tuple[str, ...],
        failure_types: tuple[str, ...],
    ) -> TeamHealthEvaluation:
        limitation = (
            "Independent evaluator failed; no healthy or unhealthy conclusion was fabricated"
        )
        if failure_types:
            limitation += f" ({', '.join(dict.fromkeys(failure_types))})"
        evaluation = TeamHealthEvaluation(
            mission_id=plan.mission_id,
            collaboration_plan_version=plan.version,
            interval_started_at=interval_started_at,
            interval_ended_at=interval_ended_at,
            evidence_cursor=evidence_cursor,
            assessments=tuple(
                TeamDimensionAssessment(
                    dimension=dimension,
                    status=TeamHealthStatus.UNCERTAIN,
                    observations=("No independent interpretation is available",),
                    evidence_references=evidence_references,
                )
                for dimension in TeamDimension
            ),
            strengths=(),
            risks=("Team health remains unknown until independent evaluation succeeds",),
            limitations=(limitation,),
            confidence_basis="No model-backed conclusion is available",
            status=TeamHealthStatus.UNCERTAIN,
            evaluator_identity=evaluator_identity,
            evidence_references=evidence_references,
        )
        TeamEffectivenessValidator.health_evaluation(evaluation, plan=plan)
        return evaluation

    def _role(self, identity_id: str) -> RoleDefinition:
        identity = next(
            (item for item in self._organization.identities if item.identity_id == identity_id),
            None,
        )
        if identity is None:
            raise MishkanError(
                ErrorCode.ROLE_CONFLICT,
                "team-health evaluator is not a canonical professional identity",
                details={"identity_id": identity_id},
            )
        return RoleDefinition(
            name=identity.identity_id,
            goal=identity.responsibility,
            backstory=(
                f"Persistent {identity.identity_id} professional identity in organization "
                f"{self._organization.organization_id}@"
                f"{self._organization.organization_version}."
            ),
            model_route=self._config.crewai.mission_cto_model_route,
        )

    def _crew(self, role: RoleDefinition, llm: LLM, prompt: str) -> CrewOutput:
        agent = Agent(
            role=role.name,
            goal=role.goal,
            backstory=role.backstory,
            llm=llm,
            allow_delegation=False,
            verbose=False,
            max_iter=self._config.crewai.max_agent_iterations,
        )
        task = Task(
            description=prompt,
            expected_output=(
                "One seven-dimension team-health evaluation grounded only in supplied evidence."
            ),
            agent=agent,
            output_pydantic=TeamHealthAssessmentOutput,
        )
        return cast(
            CrewOutput,
            Crew(
                agents=[agent],
                tasks=[task],
                process=Process.sequential,
                verbose=False,
                tracing=self._config.crewai.tracing,
            ).kickoff(),
        )

    @staticmethod
    def _prompt(
        plan: MissionCollaborationPlan,
        signals: tuple[TeamSignal, ...],
        evidence_references: tuple[str, ...],
    ) -> str:
        payload = {
            "collaboration_plan": plan.model_dump(mode="json"),
            "deterministic_signals": [item.model_dump(mode="json") for item in signals],
            "allowed_evidence_references": list(evidence_references),
        }
        return (
            "Independently interpret this bounded mission evidence across exactly seven "
            "dimensions. Preserve dissent, uncertainty, negative evidence, and conflicting "
            "observations. Do not emit a composite score, competence judgment, or promotion. "
            "Use only allowed evidence references.\n" + json.dumps(payload, sort_keys=True)
        )
