from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

import pytest

from mishkan.config.models import MishkanConfig
from mishkan.crewai.team_effectiveness import CrewAITeamHealthEvaluator
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
    TeamHealthStatus,
    TeamSignal,
)

OLLAMA_ENDPOINT = "http://127.0.0.1:11434"
OLLAMA_MODEL = "mishkan-qwen2.5-coder-1.5b-16k:latest"


def _require_ollama() -> None:
    try:
        with urlopen(f"{OLLAMA_ENDPOINT}/api/tags", timeout=2) as response:
            payload = json.load(response)
    except (OSError, URLError) as exc:
        pytest.skip(f"local Ollama is unavailable: {type(exc).__name__}")
    available = {item["name"] for item in payload.get("models", [])}
    if OLLAMA_MODEL not in available:
        pytest.skip(f"local Ollama model is not installed: {OLLAMA_MODEL}")


def _config(workspace: Path) -> MishkanConfig:
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
                "planning": {"candidates": [{"provider": "ollama-local", "model": OLLAMA_MODEL}]},
                "execution": {"candidates": [{"provider": "ollama-local", "model": OLLAMA_MODEL}]},
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


@pytest.mark.acceptance
@pytest.mark.ollama
def test_real_crewai_interprets_attributed_team_evidence_without_a_score(
    tmp_path: Path,
) -> None:
    _require_ollama()
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

    evaluation = CrewAITeamHealthEvaluator(_config(tmp_path)).evaluate(
        plan,
        (signal,),
        evaluator_identity="Product_Functional_Evaluator",
        interval_started_at=now - timedelta(minutes=5),
        interval_ended_at=now,
        evidence_cursor=1,
        evidence_references=(evidence_reference,),
    )

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
