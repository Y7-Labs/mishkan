"""Thin CLI over local configuration and daemon application functions."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any, TypeVar

import typer
import yaml
from pydantic import BaseModel

from mishkan.config.editor import set_value
from mishkan.config.loader import ConfigLoader, EffectiveConfig
from mishkan.config.presets import PRESET_NAMES, write_preset
from mishkan.config.probe import probe_connections
from mishkan.domain.errors import MishkanError
from mishkan.domain.export import export_schemas

app = typer.Typer(help="MISHKAN engineering control plane.", no_args_is_help=True)
config_app = typer.Typer(help="Create, inspect, edit, and validate effective configuration.")
schema_app = typer.Typer(help="Export versioned public schemas.")
daemon_app = typer.Typer(help="Bootstrap and administer the local mishkand instance.")
daemon_token_app = typer.Typer(help="Administer the local daemon bearer credential.")
database_app = typer.Typer(help="Inspect and explicitly migrate authoritative metadata.")
events_app = typer.Typer(help="Query and export the durable event stream.")
artifact_app = typer.Typer(help="Inspect and reconcile immutable artifacts.")
change_app = typer.Typer(help="Plan, apply, and inspect recoverable change sets.")
git_app = typer.Typer(help="Execute separately governed Git effects through mishkand.")
terminal_app = typer.Typer(help="Open and control daemon-owned PTY sessions.")
job_app = typer.Typer(help="Start and control daemon-owned managed jobs.")
run_app = typer.Typer(help="Inspect, cancel, and recover durable runs.")
mcp_app = typer.Typer(help="Connect and inspect governed MCP peers through mishkand.")
skill_app = typer.Typer(help="Inspect and govern procedural skill versions through mishkand.")
environment_app = typer.Typer(help="Observe and resolve engineering environments truthfully.")
telemetry_app = typer.Typer(help="Inspect telemetry and import attributed evaluation evidence.")
context_app = typer.Typer(help="Inspect confirmed portable and observed engineering context.")
org_app = typer.Typer(help="Inspect the organization and govern professional evolution.")
mission_app = typer.Typer(help="Create, inspect, plan, and govern contextual missions.")
conversation_app = typer.Typer(help="Use durable Executive, Mission, Branch, and Direct channels.")
intervention_app = typer.Typer(help="Inspect escalations and apply governed mission interventions.")
advisory_app = typer.Typer(help="Inspect evidence-based contextual recommendations.")
knowledge_app = typer.Typer(help="Query and govern attributed knowledge through mishkand.")
memory_app = typer.Typer(help="Recall and capture accepted episodic memory through mishkand.")
code_graph_app = typer.Typer(help="Query and refresh structural repository evidence.")
app.add_typer(config_app, name="config")
app.add_typer(schema_app, name="schema")
app.add_typer(daemon_app, name="daemon")
daemon_app.add_typer(daemon_token_app, name="token")
app.add_typer(database_app, name="db")
app.add_typer(events_app, name="events")
app.add_typer(artifact_app, name="artifact")
app.add_typer(change_app, name="change")
app.add_typer(git_app, name="git")
app.add_typer(terminal_app, name="terminal")
app.add_typer(job_app, name="job")
app.add_typer(run_app, name="run")
app.add_typer(mcp_app, name="mcp")
app.add_typer(skill_app, name="skill")
app.add_typer(environment_app, name="environment")
app.add_typer(telemetry_app, name="telemetry")
app.add_typer(context_app, name="context")
app.add_typer(org_app, name="org")
app.add_typer(mission_app, name="mission")
app.add_typer(conversation_app, name="conversation")
app.add_typer(intervention_app, name="intervention")
app.add_typer(advisory_app, name="advisory")
app.add_typer(knowledge_app, name="knowledge")
app.add_typer(memory_app, name="memory")
app.add_typer(code_graph_app, name="code-graph")

ModelT = TypeVar("ModelT", bound=BaseModel)


def _read_contract(path: Path, model: type[ModelT], option: str) -> ModelT:
    try:
        return model.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise typer.BadParameter(f"{option} must contain a valid {model.__name__}") from exc


def _dump_models(values: tuple[BaseModel, ...]) -> list[dict[str, Any]]:
    return [value.model_dump(mode="json") for value in values]


@knowledge_app.command("query")
def query_knowledge(
    ctx: typer.Context,
    request_file: Annotated[Path, typer.Option("--request")],
) -> None:
    """Submit one explicit-class, bounded attributed knowledge query."""
    from mishkan.knowledge import KnowledgeQuery

    request = _read_contract(request_file, KnowledgeQuery, "--request")
    with _daemon_client(ctx) as client:
        result = client.knowledge.query(request)
    _emit(result.model_dump(mode="json"), as_json=_state(ctx).json_output)


@knowledge_app.command("sources")
def list_knowledge_sources(ctx: typer.Context) -> None:
    """List configured sources without probing or mutating them."""
    with _daemon_client(ctx) as client:
        sources = client.knowledge.sources()
    _emit(list(sources), as_json=_state(ctx).json_output)


@knowledge_app.command("ingest")
def ingest_knowledge(
    ctx: typer.Context,
    request_file: Annotated[Path, typer.Option("--request")],
) -> None:
    """Explicitly ingest an immutable artifact into a semantic corpus."""
    from mishkan.knowledge import KnowledgeIngestRequest

    request = _read_contract(request_file, KnowledgeIngestRequest, "--request")
    with _daemon_client(ctx) as client:
        result = client.knowledge.ingest(request)
    _emit(result.model_dump(mode="json"), as_json=_state(ctx).json_output)


@knowledge_app.command("refresh")
def refresh_knowledge(
    ctx: typer.Context,
    request_file: Annotated[Path, typer.Option("--request")],
) -> None:
    """Explicitly refresh a compatible semantic or structural corpus."""
    from mishkan.knowledge import KnowledgeRefreshRequest

    request = _read_contract(request_file, KnowledgeRefreshRequest, "--request")
    with _daemon_client(ctx) as client:
        result = client.knowledge.refresh(request)
    _emit(result.model_dump(mode="json"), as_json=_state(ctx).json_output)


@knowledge_app.command("operations")
def list_knowledge_operations(
    ctx: typer.Context,
    project_id: Annotated[str | None, typer.Option("--project")] = None,
    offset: Annotated[int, typer.Option(min=0)] = 0,
    limit: Annotated[int, typer.Option(min=1, max=1_000)] = 100,
) -> None:
    """List durable provider operations and visible uncertain settlements."""
    with _daemon_client(ctx) as client:
        records = client.knowledge.operations(project_id=project_id, offset=offset, limit=limit)
    _emit(_dump_models(records), as_json=_state(ctx).json_output)


@knowledge_app.command("promote")
def propose_knowledge_promotion(
    ctx: typer.Context,
    proposal_file: Annotated[Path, typer.Option("--proposal")],
) -> None:
    """Propose a separately governed change in knowledge scope."""
    from mishkan.knowledge import KnowledgePromotion

    proposal = _read_contract(proposal_file, KnowledgePromotion, "--proposal")
    with _daemon_client(ctx) as client:
        result = client.knowledge.propose(proposal)
    _emit(result.model_dump(mode="json"), as_json=_state(ctx).json_output)


@knowledge_app.command("decide")
def decide_knowledge_promotion(
    ctx: typer.Context,
    decision_file: Annotated[Path, typer.Option("--decision")],
) -> None:
    """Approve, reject, or revoke a promotion under the current public policy."""
    from mishkan.knowledge import KnowledgePromotionDecision

    decision = _read_contract(decision_file, KnowledgePromotionDecision, "--decision")
    with _daemon_client(ctx) as client:
        result = client.knowledge.decide(decision)
    _emit(result.model_dump(mode="json"), as_json=_state(ctx).json_output)


@memory_app.command("recall")
def recall_memory(
    ctx: typer.Context,
    request_file: Annotated[Path, typer.Option("--request")],
) -> None:
    """Recall episodic evidence using an explicit episodic KnowledgeQuery."""
    from mishkan.knowledge import KnowledgeClass, KnowledgeQuery

    request = _read_contract(request_file, KnowledgeQuery, "--request")
    if request.knowledge_class is not KnowledgeClass.EPISODIC:
        raise typer.BadParameter("--request must declare knowledge_class=episodic")
    with _daemon_client(ctx) as client:
        result = client.memory.recall(request)
    _emit(result.model_dump(mode="json"), as_json=_state(ctx).json_output)


@memory_app.command("capture")
def capture_memory(
    ctx: typer.Context,
    request_file: Annotated[Path, typer.Option("--request")],
) -> None:
    """Capture a concise proposal only from a durably accepted result."""
    from mishkan.knowledge import KnowledgeMemoryCaptureRequest

    request = _read_contract(request_file, KnowledgeMemoryCaptureRequest, "--request")
    with _daemon_client(ctx) as client:
        result = client.memory.capture(request)
    _emit(result.model_dump(mode="json"), as_json=_state(ctx).json_output)


@memory_app.command("list")
def list_memory_operations(
    ctx: typer.Context,
    project_id: Annotated[str | None, typer.Option("--project")] = None,
    offset: Annotated[int, typer.Option(min=0)] = 0,
    limit: Annotated[int, typer.Option(min=1, max=1_000)] = 100,
) -> None:
    """List durable memory operations; provider storage is never queried directly."""
    with _daemon_client(ctx) as client:
        records = client.memory.list(project_id=project_id, offset=offset, limit=limit)
    _emit(_dump_models(records), as_json=_state(ctx).json_output)


def _code_graph_query(ctx: typer.Context, request_file: Path, operation: str) -> None:
    from mishkan.knowledge import (
        KnowledgeClass,
        KnowledgeQuery,
        KnowledgeStructureOperation,
    )

    request = _read_contract(request_file, KnowledgeQuery, "--request")
    if request.knowledge_class is not KnowledgeClass.STRUCTURAL:
        raise typer.BadParameter("--request must declare knowledge_class=structural")
    request = request.model_copy(
        update={"structure_operation": KnowledgeStructureOperation(operation)}
    )
    with _daemon_client(ctx) as client:
        result = client.structure.query(request)
    _emit(result.model_dump(mode="json"), as_json=_state(ctx).json_output)


def _register_code_graph_command(command_name: str, operation: str) -> None:
    def command(
        ctx: typer.Context,
        request_file: Annotated[Path, typer.Option("--request")],
    ) -> None:
        _code_graph_query(ctx, request_file, operation)

    command.__name__ = f"code_graph_{command_name.replace('-', '_')}"
    command.__doc__ = f"Run the typed Graphify {operation} operation through MISHKAN."
    code_graph_app.command(command_name)(command)


for _command_name, _operation in (
    ("query", "query_graph"),
    ("node", "get_node"),
    ("neighbors", "get_neighbors"),
    ("path", "shortest_path"),
    ("stats", "graph_stats"),
):
    _register_code_graph_command(_command_name, _operation)


@code_graph_app.command("refresh")
def refresh_code_graph(
    ctx: typer.Context,
    request_file: Annotated[Path, typer.Option("--request")],
) -> None:
    """Run an explicit, durable Graphify refresh operation."""
    refresh_knowledge(ctx, request_file)


@code_graph_app.command("status")
def code_graph_status(
    ctx: typer.Context,
    project_id: Annotated[str | None, typer.Option("--project")] = None,
) -> None:
    """Show structural corpus state without probing Graphify implicitly."""
    with _daemon_client(ctx) as client:
        records = client.structure.status(project_id=project_id)
    structural = [
        item.model_dump(mode="json")
        for item in records
        if item.knowledge_class.value == "structural"
    ]
    _emit(structural, as_json=_state(ctx).json_output)


@org_app.command("show")
def show_organization(ctx: typer.Context) -> None:
    """Show the canonical organization and its 59 professional identities."""
    with _daemon_client(ctx) as client:
        roster = client.organization()
    _emit(roster.model_dump(mode="json"), as_json=_state(ctx).json_output)


@org_app.command("inspect")
def inspect_organization(
    ctx: typer.Context,
    limit: Annotated[int, typer.Option(min=1, max=1_000)] = 100,
) -> None:
    """Inspect the bounded non-authoritative organization and branch status."""
    with _daemon_client(ctx) as client:
        projection = client.organization_inspection(limit=limit)
    _emit(projection, as_json=_state(ctx).json_output)


@org_app.command("branch")
def inspect_organization_branch(
    ctx: typer.Context,
    branch_id: str,
    limit: Annotated[int, typer.Option(min=1, max=1_000)] = 100,
) -> None:
    """Drill into one branch, its agents, pools, missions, and conversations."""
    with _daemon_client(ctx) as client:
        projection = client.organization_branch_inspection(branch_id, limit=limit)
    _emit(projection, as_json=_state(ctx).json_output)


@org_app.command("competence")
def show_professional_competence(
    ctx: typer.Context,
    identity_id: str,
    kind: Annotated[str, typer.Option("--kind")],
    subject: Annotated[str, typer.Option("--subject")],
) -> None:
    """Show attributable evidence and accepted promotion for one competence."""
    from mishkan.organization import ProfessionalEvidenceKind

    try:
        evidence_kind = ProfessionalEvidenceKind(kind)
    except ValueError as exc:
        raise typer.BadParameter("--kind must name a ProfessionalEvidenceKind") from exc
    with _daemon_client(ctx) as client:
        state = client.professional_competence(identity_id, kind=evidence_kind, subject=subject)
    _emit(state.model_dump(mode="json"), as_json=_state(ctx).json_output)


@org_app.command("evidence-record")
def record_professional_evidence(
    ctx: typer.Context,
    evidence_file: Annotated[Path, typer.Option("--evidence")],
) -> None:
    """Record immutable professional evidence; it never changes agent authority."""
    from mishkan.organization import ProfessionalEvidenceRecord

    evidence = _read_contract(evidence_file, ProfessionalEvidenceRecord, "--evidence")
    with _daemon_client(ctx) as client:
        result = client.record_professional_evidence(evidence)
    _emit(result.model_dump(mode="json"), as_json=_state(ctx).json_output)


@org_app.command("evidence")
def list_professional_evidence(
    ctx: typer.Context,
    identity_id: str,
    kind: Annotated[str | None, typer.Option("--kind")] = None,
    subject: Annotated[str | None, typer.Option("--subject")] = None,
    offset: Annotated[int, typer.Option(min=0)] = 0,
    limit: Annotated[int, typer.Option(min=1, max=1_000)] = 100,
) -> None:
    """List immutable evidence behind one professional profile."""
    from mishkan.organization import ProfessionalEvidenceKind

    try:
        evidence_kind = ProfessionalEvidenceKind(kind) if kind is not None else None
    except ValueError as exc:
        raise typer.BadParameter("--kind must name a ProfessionalEvidenceKind") from exc
    with _daemon_client(ctx) as client:
        records = client.professional_evidence(
            identity_id,
            kind=evidence_kind,
            subject=subject,
            offset=offset,
            limit=limit,
        )
    _emit(_dump_models(records), as_json=_state(ctx).json_output)


@org_app.command("promotions")
def list_professional_promotions(
    ctx: typer.Context,
    identity_id: str,
    kind: Annotated[str | None, typer.Option("--kind")] = None,
    subject: Annotated[str | None, typer.Option("--subject")] = None,
    offset: Annotated[int, typer.Option(min=0)] = 0,
    limit: Annotated[int, typer.Option(min=1, max=1_000)] = 100,
) -> None:
    """List accepted and rejected professional promotion decisions."""
    from mishkan.organization import ProfessionalEvidenceKind

    try:
        evidence_kind = ProfessionalEvidenceKind(kind) if kind is not None else None
    except ValueError as exc:
        raise typer.BadParameter("--kind must name a ProfessionalEvidenceKind") from exc
    with _daemon_client(ctx) as client:
        records = client.professional_promotions(
            identity_id,
            kind=evidence_kind,
            subject=subject,
            offset=offset,
            limit=limit,
        )
    _emit(_dump_models(records), as_json=_state(ctx).json_output)


@org_app.command("promotion-decide")
def decide_professional_promotion(
    ctx: typer.Context,
    request_file: Annotated[Path, typer.Option("--request")],
    disposition: Annotated[str, typer.Option("--disposition")],
    reason: Annotated[str, typer.Option("--reason")],
) -> None:
    """Accept or reject a broader competence promotion from attributable evidence."""
    from mishkan.organization import (
        ProfessionalPromotionDisposition,
        ProfessionalPromotionRequest,
    )

    request = _read_contract(request_file, ProfessionalPromotionRequest, "--request")
    try:
        selected_disposition = ProfessionalPromotionDisposition(disposition)
    except ValueError as exc:
        raise typer.BadParameter(
            "--disposition must name a ProfessionalPromotionDisposition"
        ) from exc
    with _daemon_client(ctx) as client:
        result = client.decide_professional_promotion(
            request,
            disposition=selected_disposition,
            reason=reason,
        )
    _emit(result.model_dump(mode="json"), as_json=_state(ctx).json_output)


@mission_app.command("list")
def list_missions(
    ctx: typer.Context,
    limit: Annotated[int, typer.Option(min=1, max=1_000)] = 100,
) -> None:
    """List durable missions."""
    with _daemon_client(ctx) as client:
        records = client.missions(limit=limit)
    _emit(_dump_models(records), as_json=_state(ctx).json_output)


@mission_app.command("show")
def show_mission(ctx: typer.Context, mission_id: str) -> None:
    """Show one durable mission."""
    with _daemon_client(ctx) as client:
        record = client.mission(mission_id)
    _emit(record.model_dump(mode="json"), as_json=_state(ctx).json_output)


@mission_app.command("inspect")
def inspect_mission(
    ctx: typer.Context,
    mission_id: str,
    limit: Annotated[int, typer.Option(min=1, max=1_000)] = 100,
) -> None:
    """Inspect the bounded, non-authoritative projection for one mission."""
    with _daemon_client(ctx) as client:
        projection = client.mission_inspection(mission_id, limit=limit)
    _emit(projection, as_json=_state(ctx).json_output)


@mission_app.command("templates")
def list_mission_templates(
    ctx: typer.Context,
    signal: Annotated[list[str] | None, typer.Option("--signal")] = None,
    organization_version: Annotated[str, typer.Option("--organization-version")] = "1",
) -> None:
    """List optional mission guidance matching explicit signals."""
    with _daemon_client(ctx) as client:
        templates = client.mission_templates(
            signals=tuple(signal) if signal else None,
            organization_version=organization_version,
        )
    _emit(_dump_models(templates), as_json=_state(ctx).json_output)


@mission_app.command("create")
def create_mission(
    ctx: typer.Context,
    record_file: Annotated[Path, typer.Option("--record")],
) -> None:
    """Create a mission from its versioned public record."""
    from mishkan.missions import MissionRecord

    record = _read_contract(record_file, MissionRecord, "--record")
    with _daemon_client(ctx) as client:
        result = client.create_mission(record)
    _emit(result.model_dump(mode="json"), as_json=_state(ctx).json_output)


@mission_app.command("governance-propose")
def propose_mission_governance(
    ctx: typer.Context,
    request_file: Annotated[Path, typer.Option("--request")],
) -> None:
    """Run the PM/CTO CrewAI turn and return a proposal without implicit mutation."""
    from mishkan.crewai import MissionGovernanceRequest

    request = _read_contract(request_file, MissionGovernanceRequest, "--request")
    with _daemon_client(ctx) as client:
        result = client.propose_mission_governance(request)
    _emit(result.model_dump(mode="json"), as_json=_state(ctx).json_output)


@mission_app.command("governance-escalate")
def escalate_mission_governance(
    ctx: typer.Context,
    proposal_file: Annotated[Path, typer.Option("--proposal")],
    conversation_id: Annotated[str, typer.Option("--conversation")],
) -> None:
    """Explicitly open the actionable escalation from a PM/CTO disagreement."""
    from mishkan.crewai import MissionGovernanceResult

    proposal = _read_contract(proposal_file, MissionGovernanceResult, "--proposal")
    with _daemon_client(ctx) as client:
        escalation = client.open_mission_governance_escalation(proposal, conversation_id)
    _emit(escalation.model_dump(mode="json"), as_json=_state(ctx).json_output)


@mission_app.command("brief")
def show_mission_brief(
    ctx: typer.Context,
    mission_id: str,
    version: Annotated[int | None, typer.Option(min=1)] = None,
) -> None:
    """Show the current or requested Mission Brief revision."""
    with _daemon_client(ctx) as client:
        brief = client.mission_brief(mission_id, version=version)
    _emit(brief.model_dump(mode="json"), as_json=_state(ctx).json_output)


@mission_app.command("brief-record")
def record_mission_brief(
    ctx: typer.Context,
    brief_file: Annotated[Path, typer.Option("--brief")],
    expected_revision: Annotated[int, typer.Option("--expected-revision", min=0)],
) -> None:
    """Record an explicitly reviewed Mission Brief revision."""
    from mishkan.missions import MissionBrief

    brief = _read_contract(brief_file, MissionBrief, "--brief")
    with _daemon_client(ctx) as client:
        result = client.record_mission_brief(brief, expected_revision=expected_revision)
    _emit(result.model_dump(mode="json"), as_json=_state(ctx).json_output)


@mission_app.command("crew")
def show_mission_crew(
    ctx: typer.Context,
    mission_id: str,
    version: Annotated[int | None, typer.Option(min=1)] = None,
) -> None:
    """Show the current or requested contextual Mission Crew."""
    with _daemon_client(ctx) as client:
        crew = client.mission_crew(mission_id, version=version)
    _emit(crew.model_dump(mode="json"), as_json=_state(ctx).json_output)


@mission_app.command("crew-record")
def record_mission_crew(
    ctx: typer.Context,
    crew_file: Annotated[Path, typer.Option("--crew")],
    expected_revision: Annotated[int, typer.Option("--expected-revision", min=0)],
) -> None:
    """Record an explicitly selected contextual Mission Crew revision."""
    from mishkan.missions import MissionCrewRevision

    crew = _read_contract(crew_file, MissionCrewRevision, "--crew")
    with _daemon_client(ctx) as client:
        result = client.record_mission_crew(crew, expected_revision=expected_revision)
    _emit(result.model_dump(mode="json"), as_json=_state(ctx).json_output)


@mission_app.command("assignments")
def list_mission_assignments(
    ctx: typer.Context,
    mission_id: str,
    limit: Annotated[int, typer.Option(min=1, max=10_000)] = 1_000,
) -> None:
    """List durable task assignments and their independent assurance roles."""
    with _daemon_client(ctx) as client:
        assignments = client.mission_assignments(mission_id, limit=limit)
    _emit(_dump_models(assignments), as_json=_state(ctx).json_output)


@mission_app.command("assignment-record")
def record_mission_assignment(
    ctx: typer.Context,
    assignment_file: Annotated[Path, typer.Option("--assignment")],
) -> None:
    """Record one versioned mission task assignment."""
    from mishkan.missions import MissionTaskAssignment

    assignment = _read_contract(assignment_file, MissionTaskAssignment, "--assignment")
    with _daemon_client(ctx) as client:
        result = client.record_mission_assignment(assignment)
    _emit(result.model_dump(mode="json"), as_json=_state(ctx).json_output)


@mission_app.command("run-bindings")
def list_mission_run_bindings(
    ctx: typer.Context,
    mission_id: str,
    limit: Annotated[int, typer.Option(min=1, max=10_000)] = 1_000,
) -> None:
    """List exact durable mission-to-run bindings and their acceptance state."""
    with _daemon_client(ctx) as client:
        bindings = client.mission_run_bindings(mission_id, limit=limit)
    _emit(_dump_models(bindings), as_json=_state(ctx).json_output)


@mission_app.command("run-binding-record")
def record_mission_run_binding(
    ctx: typer.Context,
    binding_file: Annotated[Path, typer.Option("--binding")],
) -> None:
    """Bind a mission task to one exact run context and durable result state."""
    from mishkan.missions import MissionRunBinding

    binding = _read_contract(binding_file, MissionRunBinding, "--binding")
    with _daemon_client(ctx) as client:
        result = client.record_mission_run_binding(binding)
    _emit(result.model_dump(mode="json"), as_json=_state(ctx).json_output)


@mission_app.command("run-reports")
def list_mission_run_reports(
    ctx: typer.Context,
    mission_id: str,
    limit: Annotated[int, typer.Option(min=1, max=10_000)] = 1_000,
) -> None:
    """List versioned reports for completed multi-task mission runs."""
    with _daemon_client(ctx) as client:
        reports = client.mission_run_reports(mission_id, limit=limit)
    _emit(_dump_models(reports), as_json=_state(ctx).json_output)


@mission_app.command("run-report-record")
def record_mission_run_report(
    ctx: typer.Context,
    report_file: Annotated[Path, typer.Option("--report")],
) -> None:
    """Record one attributable report after all run results are accepted."""
    from mishkan.missions import MissionRunReport

    report = _read_contract(report_file, MissionRunReport, "--report")
    with _daemon_client(ctx) as client:
        result = client.record_mission_run_report(report)
    _emit(result.model_dump(mode="json"), as_json=_state(ctx).json_output)


@mission_app.command("transitions")
def list_mission_transitions(
    ctx: typer.Context,
    mission_id: str,
    limit: Annotated[int, typer.Option(min=1, max=10_000)] = 1_000,
) -> None:
    """List the durable mission lifecycle history."""
    with _daemon_client(ctx) as client:
        transitions = client.mission_transitions(mission_id, limit=limit)
    _emit(_dump_models(transitions), as_json=_state(ctx).json_output)


@mission_app.command("transition")
def transition_mission(
    ctx: typer.Context,
    transition_file: Annotated[Path, typer.Option("--transition")],
    expected_revision: Annotated[int, typer.Option("--expected-revision", min=0)],
) -> None:
    """Apply one governed mission lifecycle transition."""
    from mishkan.missions import MissionTransition

    transition = _read_contract(transition_file, MissionTransition, "--transition")
    with _daemon_client(ctx) as client:
        result = client.transition_mission(transition, expected_revision=expected_revision)
    _emit(result.model_dump(mode="json"), as_json=_state(ctx).json_output)


@mission_app.command("decisions")
def list_mission_decisions(
    ctx: typer.Context,
    mission_id: str,
    limit: Annotated[int, typer.Option(min=1, max=1_000)] = 100,
) -> None:
    """List durable decisions governing one mission."""
    with _daemon_client(ctx) as client:
        decisions = client.mission_decisions(mission_id, limit=limit)
    _emit(_dump_models(decisions), as_json=_state(ctx).json_output)


@mission_app.command("environment-plan")
def show_mission_environment_plan(
    ctx: typer.Context,
    mission_id: str,
    version: Annotated[int | None, typer.Option(min=1)] = None,
) -> None:
    """Show an accepted agent-authored environment plan."""
    with _daemon_client(ctx) as client:
        plan = client.mission_environment_plan(mission_id, version=version)
    _emit(plan.model_dump(mode="json"), as_json=_state(ctx).json_output)


@mission_app.command("readiness")
def show_mission_readiness(ctx: typer.Context, mission_id: str) -> None:
    """Show which task environment dependencies are proved ready or blocked."""
    with _daemon_client(ctx) as client:
        readiness = client.mission_environment_readiness(mission_id)
    _emit(readiness.model_dump(mode="json"), as_json=_state(ctx).json_output)


@mission_app.command("task-eligibility")
def show_mission_task_eligibility(
    ctx: typer.Context,
    mission_id: str,
    task_id: str,
) -> None:
    """Show the exact mission, environment, escalation, and run claim gate."""
    with _daemon_client(ctx) as client:
        eligibility = client.mission_task_eligibility(mission_id, task_id)
    _emit(eligibility.model_dump(mode="json"), as_json=_state(ctx).json_output)


@mission_app.command("completion-readiness")
def show_mission_completion_readiness(ctx: typer.Context, mission_id: str) -> None:
    """Show whether every governed mission result is durably accepted."""
    with _daemon_client(ctx) as client:
        readiness = client.mission_completion_readiness(mission_id)
    _emit(readiness.model_dump(mode="json"), as_json=_state(ctx).json_output)


@mission_app.command("task-claim")
def claim_mission_task(
    ctx: typer.Context,
    request_file: Annotated[Path, typer.Option("--request")],
) -> None:
    """Atomically request execution of a currently eligible mission task."""
    from mishkan.missions import MissionTaskClaimRequest

    request = _read_contract(request_file, MissionTaskClaimRequest, "--request")
    with _daemon_client(ctx) as client:
        claim = client.claim_mission_task(request)
    _emit(claim.model_dump(mode="json"), as_json=_state(ctx).json_output)


@mission_app.command("environment-propose")
def propose_mission_environment(
    ctx: typer.Context,
    request_file: Annotated[Path, typer.Option("--request")],
) -> None:
    """Ask the assigned CrewAI agent for an environment plan without mutating the mission."""
    from mishkan.missions import MissionEnvironmentPlanningRequest

    request = _read_contract(request_file, MissionEnvironmentPlanningRequest, "--request")
    with _daemon_client(ctx) as client:
        plan = client.propose_mission_environment(request)
    _emit(plan.model_dump(mode="json"), as_json=_state(ctx).json_output)


@mission_app.command("environment-accept")
def accept_mission_environment(
    ctx: typer.Context,
    plan_file: Annotated[Path, typer.Option("--plan")],
    expected_revision: Annotated[int, typer.Option("--expected-revision", min=0)],
) -> None:
    """Accept an agent-authored environment plan at an explicit mission revision."""
    from mishkan.missions import MissionEnvironmentPlan

    plan = _read_contract(plan_file, MissionEnvironmentPlan, "--plan")
    with _daemon_client(ctx) as client:
        accepted = client.accept_mission_environment(plan, expected_revision=expected_revision)
    _emit(accepted.model_dump(mode="json"), as_json=_state(ctx).json_output)


@mission_app.command("environment-resolve")
def resolve_mission_environment(
    ctx: typer.Context,
    plan_id: str,
    context_id: str,
) -> None:
    """Bind one exact accepted environment outcome; the resolver cannot choose another."""
    with _daemon_client(ctx) as client:
        binding = client.resolve_mission_environment(plan_id, context_id)
    _emit(binding.model_dump(mode="json"), as_json=_state(ctx).json_output)


@conversation_app.command("list")
def list_conversations(
    ctx: typer.Context,
    mission_id: Annotated[str | None, typer.Option("--mission")] = None,
    limit: Annotated[int, typer.Option(min=1, max=1_000)] = 100,
) -> None:
    """List durable channels, optionally scoped to one mission."""
    with _daemon_client(ctx) as client:
        channels = client.conversations(mission_id=mission_id, limit=limit)
    _emit(_dump_models(channels), as_json=_state(ctx).json_output)


@conversation_app.command("show")
def show_conversation(ctx: typer.Context, conversation_id: str) -> None:
    """Show one durable conversation channel."""
    with _daemon_client(ctx) as client:
        channel = client.conversation(conversation_id)
    _emit(channel.model_dump(mode="json"), as_json=_state(ctx).json_output)


@conversation_app.command("messages")
def list_conversation_messages(
    ctx: typer.Context,
    conversation_id: str,
    limit: Annotated[int, typer.Option(min=1, max=10_000)] = 100,
) -> None:
    """List attributable messages in deterministic channel order."""
    with _daemon_client(ctx) as client:
        messages = client.conversation_messages(conversation_id, limit=limit)
    _emit(_dump_models(messages), as_json=_state(ctx).json_output)


@conversation_app.command("create")
def create_conversation(
    ctx: typer.Context,
    channel_file: Annotated[Path, typer.Option("--channel")],
) -> None:
    """Create a versioned Executive, Mission, Branch, or Direct channel."""
    from mishkan.conversations import ConversationChannel

    channel = _read_contract(channel_file, ConversationChannel, "--channel")
    with _daemon_client(ctx) as client:
        result = client.create_conversation(channel)
    _emit(result.model_dump(mode="json"), as_json=_state(ctx).json_output)


@conversation_app.command("post")
def post_conversation_message(
    ctx: typer.Context,
    message_file: Annotated[Path, typer.Option("--message")],
) -> None:
    """Post an attributable durable message without turning it into a command."""
    from mishkan.conversations import ConversationMessage

    message = _read_contract(message_file, ConversationMessage, "--message")
    with _daemon_client(ctx) as client:
        result = client.post_message(message)
    _emit(result.model_dump(mode="json"), as_json=_state(ctx).json_output)


@app.command("chat")
def chat(
    ctx: typer.Context,
    conversation_id: Annotated[str, typer.Option("--conversation")],
    author: Annotated[str, typer.Option("--author")],
    message: Annotated[str, typer.Option("--message")],
    reply_to: Annotated[str | None, typer.Option("--reply-to")] = None,
    evidence: Annotated[list[str] | None, typer.Option("--evidence")] = None,
) -> None:
    """Post one durable message through the same governed daemon contract."""
    from uuid import UUID

    from mishkan.conversations import ConversationMessage

    try:
        record = ConversationMessage(
            conversation_id=UUID(conversation_id),
            author_identity=author,
            body=message,
            reply_to_message_id=UUID(reply_to) if reply_to is not None else None,
            evidence_references=tuple(evidence or ()),
        )
    except ValueError as exc:
        raise typer.BadParameter("conversation and reply identities must be UUIDs") from exc
    with _daemon_client(ctx) as client:
        result = client.post_message(record)
    _emit(result.model_dump(mode="json"), as_json=_state(ctx).json_output)


@intervention_app.command("escalations")
def list_mission_escalations(
    ctx: typer.Context,
    mission_id: str,
    state: Annotated[str | None, typer.Option("--state")] = None,
    limit: Annotated[int, typer.Option(min=1, max=1_000)] = 100,
) -> None:
    """List explicit mission escalations."""
    from mishkan.conversations import EscalationState

    try:
        selected_state = EscalationState(state) if state is not None else None
    except ValueError as exc:
        raise typer.BadParameter("--state must name an EscalationState") from exc
    with _daemon_client(ctx) as client:
        escalations = client.mission_escalations(
            mission_id,
            state=selected_state,
            limit=limit,
        )
    _emit(_dump_models(escalations), as_json=_state(ctx).json_output)


@intervention_app.command("interventions")
def list_mission_interventions(
    ctx: typer.Context,
    mission_id: str,
    limit: Annotated[int, typer.Option(min=1, max=1_000)] = 100,
) -> None:
    """List governed interventions and their effects."""
    with _daemon_client(ctx) as client:
        interventions = client.mission_interventions(mission_id, limit=limit)
    _emit(_dump_models(interventions), as_json=_state(ctx).json_output)


@intervention_app.command("decision-record")
def record_mission_decision(
    ctx: typer.Context,
    decision_file: Annotated[Path, typer.Option("--decision")],
) -> None:
    """Record a durable decision distinct from messages and commands."""
    from mishkan.conversations import MissionDecision

    decision = _read_contract(decision_file, MissionDecision, "--decision")
    with _daemon_client(ctx) as client:
        result = client.record_mission_decision(decision)
    _emit(result.model_dump(mode="json"), as_json=_state(ctx).json_output)


@intervention_app.command("escalation-open")
def open_mission_escalation(
    ctx: typer.Context,
    escalation_file: Annotated[Path, typer.Option("--escalation")],
) -> None:
    """Open an attributable escalation with an explicit decision owner."""
    from mishkan.conversations import MissionEscalation

    escalation = _read_contract(escalation_file, MissionEscalation, "--escalation")
    with _daemon_client(ctx) as client:
        result = client.open_mission_escalation(escalation)
    _emit(result.model_dump(mode="json"), as_json=_state(ctx).json_output)


@intervention_app.command("apply")
def apply_mission_intervention(
    ctx: typer.Context,
    intervention_file: Annotated[Path, typer.Option("--intervention")],
    expected_revision: Annotated[int, typer.Option("--expected-revision", min=0)],
) -> None:
    """Apply a policy-governed comment, pause, resume, reassign, stop, or risk acceptance."""
    from mishkan.conversations import MissionIntervention

    intervention = _read_contract(intervention_file, MissionIntervention, "--intervention")
    with _daemon_client(ctx) as client:
        result = client.apply_mission_intervention(
            intervention,
            expected_revision=expected_revision,
        )
    _emit(result.model_dump(mode="json"), as_json=_state(ctx).json_output)


@advisory_app.command("candidates")
def show_advisory_candidates(ctx: typer.Context) -> None:
    """Show configured community candidates without activating any of them."""
    with _daemon_client(ctx) as client:
        candidates = client.community_candidates()
    _emit(
        {
            "candidates": _dump_models(candidates),
            "count": len(candidates),
            "activation_authorized": False,
        },
        as_json=_state(ctx).json_output,
    )


@advisory_app.command("recommend")
def recommend_advisory_candidate(
    ctx: typer.Context,
    request_file: Annotated[Path, typer.Option("--request")],
) -> None:
    """Rank configured candidates against project evidence without activating them."""
    from mishkan.context import ContextualRecommendationRequest

    request = _read_contract(request_file, ContextualRecommendationRequest, "--request")
    with _daemon_client(ctx) as client:
        if request.owner_identity != client.principal_id:
            raise typer.BadParameter(
                "request owner_identity must match the authenticated daemon principal"
            )
        result = client.recommend_community_candidate(request)
    _emit(result.model_dump(mode="json"), as_json=_state(ctx).json_output)


@context_app.command("engineer-profile")
def show_engineer_profile(ctx: typer.Context) -> None:
    """Show only explicitly confirmed portable profile facts."""
    with _daemon_client(ctx) as client:
        profile = client.engineer_profile()
    _emit(profile.model_dump(mode="json"), as_json=_state(ctx).json_output)


@context_app.command("community-candidates")
def show_community_candidates(ctx: typer.Context) -> None:
    """Show configured candidates; discovery never grants activation authority."""
    with _daemon_client(ctx) as client:
        candidates = client.community_candidates()
    _emit(
        {
            "candidates": [candidate.model_dump(mode="json") for candidate in candidates],
            "count": len(candidates),
            "activation_authorized": False,
        },
        as_json=_state(ctx).json_output,
    )


@context_app.command("recommend")
def recommend_community_candidate(
    ctx: typer.Context,
    request_file: Annotated[
        Path,
        typer.Option("--request", help="JSON ContextualRecommendationRequest."),
    ],
) -> None:
    """Rank configured candidates against explicit evidence and criteria."""
    from mishkan.context import ContextualRecommendationRequest

    try:
        request = ContextualRecommendationRequest.model_validate_json(
            request_file.read_text(encoding="utf-8")
        )
    except (OSError, ValueError) as exc:
        raise typer.BadParameter(
            "--request must contain a valid ContextualRecommendationRequest"
        ) from exc
    with _daemon_client(ctx) as client:
        if request.owner_identity != client.principal_id:
            raise typer.BadParameter(
                "request owner_identity must match the authenticated daemon principal"
            )
        result = client.recommend_community_candidate(request)
    _emit(result.model_dump(mode="json"), as_json=_state(ctx).json_output)


@telemetry_app.command("status")
def telemetry_status(ctx: typer.Context) -> None:
    """Show the optional exporter state without making telemetry authoritative."""
    with _daemon_client(ctx) as client:
        status = client.telemetry_status()
    _emit(status.model_dump(mode="json"), as_json=_state(ctx).json_output)


@telemetry_app.command("import-langsmith-feedback")
def import_langsmith_feedback(
    ctx: typer.Context,
    request_file: Annotated[
        Path,
        typer.Option("--request", help="JSON LangSmithFeedbackImportRequest."),
    ],
) -> None:
    """Store LangSmith feedback as immutable candidate-only evidence."""
    from mishkan.telemetry import LangSmithFeedbackImportRequest

    try:
        request = LangSmithFeedbackImportRequest.model_validate_json(
            request_file.read_text(encoding="utf-8")
        )
    except (OSError, ValueError) as exc:
        raise typer.BadParameter(
            "--request must contain a valid LangSmithFeedbackImportRequest"
        ) from exc
    with _daemon_client(ctx) as client:
        if request.owner_identity != client.principal_id:
            raise typer.BadParameter(
                "request owner_identity must match the authenticated daemon principal"
            )
        result = client.import_langsmith_feedback(request)
    _emit(result.model_dump(mode="json"), as_json=_state(ctx).json_output)


@dataclass(frozen=True, slots=True)
class CliState:
    sources: tuple[Path, ...]
    json_output: bool


def _environment_sources() -> tuple[Path, ...]:
    encoded = os.environ.get("MISHKAN_CONFIG", "")
    return tuple(Path(item) for item in encoded.split(os.pathsep) if item)


@app.callback()
def main(
    ctx: typer.Context,
    config: Annotated[
        list[Path] | None,
        typer.Option("--config", "-c", help="YAML layer; repeat from low to high precedence."),
    ] = None,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Emit machine-readable JSON."),
    ] = False,
) -> None:
    """Configure shared options for every command."""

    ctx.obj = CliState(sources=tuple(config or _environment_sources()), json_output=json_output)


def _state(ctx: typer.Context) -> CliState:
    state = ctx.find_root().obj
    if not isinstance(state, CliState):
        return CliState(sources=(), json_output=False)
    return state


def _emit(value: Any, *, as_json: bool) -> None:
    if as_json:
        typer.echo(json.dumps(value, indent=2, sort_keys=True, default=str))
    else:
        typer.echo(yaml.safe_dump(value, sort_keys=False, allow_unicode=True).rstrip())


def _emit_error(error: MishkanError, *, as_json: bool) -> None:
    payload = error.envelope.model_dump(mode="json")
    if as_json:
        _emit(payload, as_json=True)
    else:
        typer.echo(f"{payload['code']}: {payload['message']}", err=True)
        if payload["details"]:
            typer.echo(yaml.safe_dump(payload["details"], sort_keys=False).rstrip(), err=True)


def _load_or_exit(ctx: typer.Context) -> EffectiveConfig:
    state = _state(ctx)
    try:
        return ConfigLoader().load(state.sources)
    except MishkanError as error:
        _emit_error(error, as_json=state.json_output)
        raise typer.Exit(code=2) from error


@config_app.command("validate")
def validate_config(ctx: typer.Context) -> None:
    """Validate and fingerprint the explicit effective configuration."""

    effective = _load_or_exit(ctx)
    _emit(
        {
            "valid": True,
            "schema_version": effective.value.schema_version,
            "fingerprint": effective.fingerprint,
            "layers": [layer.as_dict() for layer in effective.layers],
        },
        as_json=_state(ctx).json_output,
    )


@config_app.command("show")
def show_config(ctx: typer.Context) -> None:
    """Show the effective non-secret configuration and field provenance."""

    effective = _load_or_exit(ctx)
    _emit(effective.public_view(), as_json=_state(ctx).json_output)


@config_app.command("setup")
def setup_config(
    ctx: typer.Context,
    preset: Annotated[str, typer.Option(help=f"One of: {', '.join(PRESET_NAMES)}")] = "local",
    output: Annotated[Path, typer.Option(help="Destination YAML file.")] = Path(
        ".mishkan/config.yaml"
    ),
    force: Annotated[bool, typer.Option(help="Replace an existing destination.")] = False,
    test_connections: Annotated[
        bool,
        typer.Option(help="Probe configured endpoints; failures are warnings."),
    ] = False,
) -> None:
    """Write an inspectable local, cloud, or hybrid configuration preset."""

    state = _state(ctx)
    try:
        target = write_preset(preset, output, overwrite=force)
        effective = ConfigLoader().load([target])
    except MishkanError as error:
        _emit_error(error, as_json=state.json_output)
        raise typer.Exit(code=2) from error

    result: dict[str, Any] = {
        "created": str(target),
        "preset": preset,
        "fingerprint": effective.fingerprint,
    }
    if test_connections:
        result["connection_probes"] = [
            probe.model_dump(mode="json") for probe in probe_connections(effective.value)
        ]
    _emit(result, as_json=state.json_output)


@config_app.command("set")
def set_config(
    ctx: typer.Context,
    path: Annotated[str, typer.Argument(help="Dot-separated configuration field.")],
    value: Annotated[str, typer.Argument(help="YAML-encoded value.")],
    source: Annotated[Path, typer.Option("--file", help="Explicit YAML source to edit.")],
) -> None:
    """Atomically update one explicit source after validating the complete result."""

    state = _state(ctx)
    try:
        target = set_value(source, path, value)
        effective = ConfigLoader().load([target])
    except MishkanError as error:
        _emit_error(error, as_json=state.json_output)
        raise typer.Exit(code=2) from error
    _emit(
        {"updated": str(target), "field": path, "fingerprint": effective.fingerprint},
        as_json=state.json_output,
    )


@config_app.command("migrate")
def migrate_config(
    ctx: typer.Context,
    source: Annotated[Path, typer.Option("--file", help="Explicit schema 1.1 or 1.2 YAML source.")],
) -> None:
    """Explicitly migrate one configuration source to the current schema."""
    from mishkan.config.migration import migrate_to_latest

    state = _state(ctx)
    try:
        target = migrate_to_latest(source)
        effective = ConfigLoader().load([target])
    except MishkanError as error:
        _emit_error(error, as_json=state.json_output)
        raise typer.Exit(code=2) from error
    _emit(
        {"migrated": str(target), "schema_version": effective.value.schema_version},
        as_json=state.json_output,
    )


@daemon_app.command("setup")
def setup_daemon(
    ctx: typer.Context,
    principal: Annotated[str, typer.Option(help="Authenticated local operator identity.")] = (
        "local-operator"
    ),
) -> None:
    """Explicitly initialize an empty daemon database and local credential."""
    from mishkan.daemon import DaemonBootstrap
    from mishkan.daemon.auth import TokenFile

    state = _state(ctx)
    effective = _load_or_exit(ctx)
    try:
        paths = DaemonBootstrap().setup(effective.value, principal_id=principal)
        token = TokenFile(paths.token_file).public_status()
    except MishkanError as error:
        _emit_error(error, as_json=state.json_output)
        raise typer.Exit(code=2) from error
    _emit(
        {"database": str(paths.database), "artifacts": str(paths.artifacts), "token": token},
        as_json=state.json_output,
    )


@daemon_token_app.command("rotate")
def rotate_daemon_token(ctx: typer.Context) -> None:
    """Atomically rotate the configured local bearer credential."""
    from mishkan.daemon.auth import TokenFile
    from mishkan.daemon.bootstrap import DaemonPaths

    state = _state(ctx)
    effective = _load_or_exit(ctx)
    try:
        paths = DaemonPaths.from_config(effective.value)
        token_file = TokenFile(paths.token_file)
        record = token_file.rotate()
    except MishkanError as error:
        _emit_error(error, as_json=state.json_output)
        raise typer.Exit(code=2) from error
    _emit(
        {"rotated": True, "path": str(paths.token_file), "principal_id": record.principal_id},
        as_json=state.json_output,
    )


@database_app.command("status")
def database_status(ctx: typer.Context) -> None:
    """Report the observed schema without changing it."""
    from mishkan.daemon.bootstrap import DaemonPaths
    from mishkan.persistence import SchemaManager

    state = _state(ctx)
    effective = _load_or_exit(ctx)
    try:
        paths = DaemonPaths.from_config(effective.value)
        observed = SchemaManager(paths.database).status()
    except MishkanError as error:
        _emit_error(error, as_json=state.json_output)
        raise typer.Exit(code=2) from error
    _emit(
        {
            "database": str(paths.database),
            "state": observed.state.value,
            "current_revision": observed.current_revision,
            "head_revision": observed.head_revision,
        },
        as_json=state.json_output,
    )


@database_app.command("upgrade")
def database_upgrade(ctx: typer.Context) -> None:
    """Back up and explicitly upgrade recognized metadata to the current head."""
    from mishkan.daemon.bootstrap import DaemonPaths
    from mishkan.persistence import SchemaManager

    state = _state(ctx)
    effective = _load_or_exit(ctx)
    try:
        paths = DaemonPaths.from_config(effective.value)
        observed = SchemaManager(paths.database).upgrade()
        artifact_config = effective.value.artifacts
        persistence_config = effective.value.persistence
        assert artifact_config is not None
        assert persistence_config is not None
        from mishkan.artifacts.service import DurableArtifactService

        imported_artifacts = DurableArtifactService(
            paths.database,
            paths.artifacts,
            max_artifact_bytes=artifact_config.max_artifact_bytes,
            max_chunk_bytes=artifact_config.chunk_bytes,
            busy_timeout_ms=persistence_config.busy_timeout_ms,
            staging_ttl_seconds=artifact_config.staging_ttl_seconds,
        ).import_legacy_manifests()
    except MishkanError as error:
        _emit_error(error, as_json=state.json_output)
        raise typer.Exit(code=2) from error
    _emit(
        {
            "database": str(paths.database),
            "state": observed.state.value,
            "revision": observed.current_revision,
            "backup": str(observed.backup_path) if observed.backup_path else None,
            "imported_artifacts": imported_artifacts,
        },
        as_json=state.json_output,
    )


def _daemon_client(ctx: typer.Context):  # type: ignore[no-untyped-def]
    from mishkan.client import Mishkan, daemon_url
    from mishkan.daemon.bootstrap import DaemonPaths

    effective = _load_or_exit(ctx)
    paths = DaemonPaths.from_config(effective.value)
    daemon = effective.value.daemon
    assert daemon is not None
    return Mishkan(
        daemon_url(daemon.host, daemon.port),
        token_file=paths.token_file,
        timeout_seconds=daemon.request_timeout_seconds,
    )


@events_app.command("list")
def list_events(
    ctx: typer.Context,
    after: Annotated[int, typer.Option(min=0)] = 0,
    limit: Annotated[int | None, typer.Option(min=1, max=1_000)] = None,
    event_type: Annotated[list[str] | None, typer.Option("--type")] = None,
    run_id: Annotated[str | None, typer.Option("--run")] = None,
    task_id: Annotated[str | None, typer.Option("--task")] = None,
    identity_id: Annotated[str | None, typer.Option("--identity")] = None,
    team_id: Annotated[str | None, typer.Option("--team")] = None,
    since: Annotated[str | None, typer.Option(help="Inclusive ISO-8601 timestamp.")] = None,
    until: Annotated[str | None, typer.Option(help="Inclusive ISO-8601 timestamp.")] = None,
    security: Annotated[bool, typer.Option("--security", help="Only security events.")] = False,
) -> None:
    """Query a bounded page from the durable daemon stream."""
    state = _state(ctx)
    try:
        with _daemon_client(ctx) as client:
            page = client.events(
                after=after,
                limit=limit,
                event_types=tuple(event_type or ()),
                run_id=run_id,
                task_id=task_id,
                identity_id=identity_id,
                team_id=team_id,
                occurred_after=_event_time(since),
                occurred_before=_event_time(until),
                security_relevant=True if security else None,
            )
    except MishkanError as error:
        _emit_error(error, as_json=state.json_output)
        raise typer.Exit(code=2) from error
    _emit(page.model_dump(mode="json"), as_json=state.json_output)


@events_app.command("notifications")
def list_notifications(
    ctx: typer.Context,
    after: Annotated[int, typer.Option(min=0)] = 0,
    limit: Annotated[int | None, typer.Option(min=1, max=1_000)] = None,
    severity: Annotated[list[str] | None, typer.Option("--severity")] = None,
    delivery: Annotated[list[str] | None, typer.Option("--delivery")] = None,
) -> None:
    """Project configured severity and delivery without hiding source events."""
    from mishkan.notifications import NotificationDelivery, NotificationSeverity

    try:
        severities = tuple(NotificationSeverity(item) for item in (severity or ()))
        deliveries = tuple(NotificationDelivery(item) for item in (delivery or ()))
    except ValueError as exc:
        raise typer.BadParameter("notification severity or delivery is invalid") from exc
    try:
        with _daemon_client(ctx) as client:
            page = client.notifications(
                after=after,
                limit=limit,
                severities=severities,
                deliveries=deliveries,
            )
    except MishkanError as error:
        _emit_error(error, as_json=_state(ctx).json_output)
        raise typer.Exit(code=2) from error
    _emit(page.model_dump(mode="json"), as_json=_state(ctx).json_output)


@events_app.command("tail")
def tail_events(
    ctx: typer.Context,
    after: Annotated[int, typer.Option(min=0)] = 0,
    count: Annotated[
        int,
        typer.Option(min=0, help="Stop after this many events; zero follows continuously."),
    ] = 0,
    event_type: Annotated[list[str] | None, typer.Option("--type")] = None,
    run_id: Annotated[str | None, typer.Option("--run")] = None,
    task_id: Annotated[str | None, typer.Option("--task")] = None,
    identity_id: Annotated[str | None, typer.Option("--identity")] = None,
    team_id: Annotated[str | None, typer.Option("--team")] = None,
    since: Annotated[str | None, typer.Option(help="Inclusive ISO-8601 timestamp.")] = None,
    security: Annotated[bool, typer.Option("--security", help="Only security events.")] = False,
) -> None:
    """Follow the resumable SSE stream from an explicit durable cursor."""
    emitted = 0
    with _daemon_client(ctx) as client:
        for event in client.stream_events(
            after=after,
            event_types=tuple(event_type or ()),
            run_id=run_id,
            task_id=task_id,
            identity_id=identity_id,
            team_id=team_id,
            occurred_after=_event_time(since),
            security_relevant=True if security else None,
        ):
            _emit(event.model_dump(mode="json"), as_json=_state(ctx).json_output)
            emitted += 1
            if count and emitted >= count:
                return


def _event_time(value: str | None) -> datetime | None:
    if value is None:
        return None
    try:
        observed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise typer.BadParameter("event time must be an ISO-8601 timestamp") from exc
    if observed.tzinfo is None or observed.utcoffset() is None:
        raise typer.BadParameter("event time must include a timezone offset")
    return observed


@events_app.command("export")
def export_events(
    ctx: typer.Context,
    output: Annotated[Path, typer.Option(help="Atomic JSONL destination.")],
    after: Annotated[int, typer.Option(min=0)] = 0,
) -> None:
    """Export all currently retained events after a cursor as inspectable JSONL."""
    with _daemon_client(ctx) as client:
        count, cursor = client.export_events_jsonl(output, after=after)
    _emit(
        {"output": str(output), "events": count, "cursor": cursor},
        as_json=_state(ctx).json_output,
    )


@events_app.command("holds")
def list_event_holds(
    ctx: typer.Context,
    active_only: Annotated[bool, typer.Option("--active")] = False,
) -> None:
    """Inspect durable evidence holds."""
    with _daemon_client(ctx) as client:
        holds = client.event_holds(active_only=active_only)
    _emit(
        [hold.model_dump(mode="json") for hold in holds],
        as_json=_state(ctx).json_output,
    )


@events_app.command("hold")
def create_event_hold(
    ctx: typer.Context,
    scope: Annotated[str, typer.Option(help="One of: all, run, event.")],
    reason: Annotated[str, typer.Option(help="Inspectable reason for preserving evidence.")],
    scope_id: Annotated[str | None, typer.Option(help="Run ID or event UUID.")] = None,
) -> None:
    """Create a policy-authorized hold on all, run, or event evidence."""
    from mishkan.application import ApplicationCommand

    payload: dict[str, object] = {"scope": scope, "reason": reason}
    if scope_id is not None:
        payload["scope_id"] = scope_id
    with _daemon_client(ctx) as client:
        result = client.command(
            ApplicationCommand(
                command_type="event.hold.create",
                actor_id=client.principal_id,
                target_type="event_store",
                payload=payload,
            )
        )
    _emit(result.model_dump(mode="json"), as_json=_state(ctx).json_output)


@events_app.command("hold-release")
def release_event_hold(
    ctx: typer.Context,
    hold_id: Annotated[str, typer.Argument()],
    expected_revision: Annotated[int | None, typer.Option(min=0)] = None,
) -> None:
    """Release one hold without erasing its audit record."""
    from mishkan.application import ApplicationCommand

    with _daemon_client(ctx) as client:
        result = client.command(
            ApplicationCommand(
                command_type="event.hold.release",
                actor_id=client.principal_id,
                target_type="event_hold",
                target_id=hold_id,
                expected_revision=expected_revision,
                payload={},
            )
        )
    _emit(result.model_dump(mode="json"), as_json=_state(ctx).json_output)


@events_app.command("retention-plans")
def list_event_retention_plans(ctx: typer.Context) -> None:
    """Inspect immutable retention policy snapshots and their settlements."""
    with _daemon_client(ctx) as client:
        plans = client.event_retention_plans()
    _emit(
        [plan.model_dump(mode="json") for plan in plans],
        as_json=_state(ctx).json_output,
    )


@events_app.command("retention-policy")
def show_event_retention_policy(ctx: typer.Context) -> None:
    """Show the exact versioned policy that a new retention plan will snapshot."""
    with _daemon_client(ctx) as client:
        policy = client.event_retention_policy()
    _emit(
        {**policy.model_dump(mode="json"), "fingerprint": policy.fingerprint},
        as_json=_state(ctx).json_output,
    )


@events_app.command("retention-plan")
def plan_event_retention(ctx: typer.Context) -> None:
    """Persist a bounded retention plan without removing evidence."""
    from mishkan.application import ApplicationCommand

    with _daemon_client(ctx) as client:
        result = client.command(
            ApplicationCommand(
                command_type="event.retention.plan",
                actor_id=client.principal_id,
                target_type="event_store",
                payload={},
            )
        )
    _emit(result.model_dump(mode="json"), as_json=_state(ctx).json_output)


@events_app.command("retention-apply")
def apply_event_retention(
    ctx: typer.Context,
    plan_id: Annotated[str, typer.Argument()],
    expected_revision: Annotated[int | None, typer.Option(min=0)] = None,
) -> None:
    """Apply one plan after rechecking active holds and incomplete runs."""
    from mishkan.application import ApplicationCommand

    with _daemon_client(ctx) as client:
        result = client.command(
            ApplicationCommand(
                command_type="event.retention.apply",
                actor_id=client.principal_id,
                target_type="event_retention_plan",
                target_id=plan_id,
                expected_revision=expected_revision,
                payload={},
            )
        )
    _emit(result.model_dump(mode="json"), as_json=_state(ctx).json_output)


@change_app.command("list")
def list_change_sets(
    ctx: typer.Context,
    offset: Annotated[int, typer.Option(min=0)] = 0,
    limit: Annotated[int, typer.Option(min=1, max=1_000)] = 100,
) -> None:
    """Query bounded change-set state from mishkand."""
    with _daemon_client(ctx) as client:
        values = client.change_sets(offset=offset, limit=limit)
    _emit(
        [value.model_dump(mode="json") for value in values],
        as_json=_state(ctx).json_output,
    )


@artifact_app.command("list")
def list_artifacts(
    ctx: typer.Context,
    offset: Annotated[int, typer.Option(min=0)] = 0,
    limit: Annotated[int, typer.Option(min=1, max=1_000)] = 100,
) -> None:
    """List bounded artifact manifests from mishkand."""

    with _daemon_client(ctx) as client:
        manifests = client.artifacts(offset=offset, limit=limit)
    _emit(
        [manifest.model_dump(mode="json") for manifest in manifests],
        as_json=_state(ctx).json_output,
    )


@artifact_app.command("show")
def show_artifact(ctx: typer.Context, reference: Annotated[str, typer.Argument()]) -> None:
    """Show one immutable artifact manifest."""

    with _daemon_client(ctx) as client:
        manifest = client.artifact(reference)
    _emit(manifest.model_dump(mode="json"), as_json=_state(ctx).json_output)


@artifact_app.command("upload-status")
def show_artifact_upload(ctx: typer.Context, upload_id: str) -> None:
    """Inspect the durable cursor of an interrupted artifact upload."""

    with _daemon_client(ctx) as client:
        upload = client.artifact_upload(upload_id)
    _emit(upload.model_dump(mode="json"), as_json=_state(ctx).json_output)


@artifact_app.command("upload-abort")
def abort_artifact_upload(
    ctx: typer.Context,
    upload_id: str,
    expected_revision: Annotated[int | None, typer.Option(min=0)] = None,
) -> None:
    """Abort one incomplete upload without publishing partial content."""

    from mishkan.application import ApplicationCommand

    with _daemon_client(ctx) as client:
        result = client.command(
            ApplicationCommand(
                command_type="artifact.upload.abort",
                actor_id=client.principal_id,
                target_type="artifact_upload",
                target_id=upload_id,
                expected_revision=expected_revision,
                payload={},
            )
        )
    _emit(result.model_dump(mode="json"), as_json=_state(ctx).json_output)


def _artifact_root_effect(
    ctx: typer.Context,
    artifact_id: str,
    command_type: str,
    payload: dict[str, object],
    expected_revision: int | None,
) -> None:
    from mishkan.application import ApplicationCommand

    with _daemon_client(ctx) as client:
        result = client.command(
            ApplicationCommand(
                command_type=command_type,
                actor_id=client.principal_id,
                target_type="artifact",
                target_id=artifact_id.removeprefix("artifact:"),
                expected_revision=expected_revision,
                payload=payload,
            )
        )
    _emit(result.model_dump(mode="json"), as_json=_state(ctx).json_output)


@artifact_app.command("collection-create")
def create_artifact_collection(
    ctx: typer.Context,
    source: Annotated[Path, typer.Option("--file", help="YAML/JSON logical-path mapping.")],
) -> None:
    """Create an ordered immutable collection from artifact references."""
    from mishkan.application import ApplicationCommand

    entries = yaml.safe_load(source.read_text(encoding="utf-8"))
    if not isinstance(entries, dict):
        raise typer.BadParameter("collection file must contain a logical-path mapping")
    with _daemon_client(ctx) as client:
        result = client.command(
            ApplicationCommand(
                command_type="artifact.collection.create",
                actor_id=client.principal_id,
                target_type="artifact_service",
                payload={"entries": entries},
            )
        )
    _emit(result.model_dump(mode="json"), as_json=_state(ctx).json_output)


@artifact_app.command("collections")
def list_artifact_collections(ctx: typer.Context) -> None:
    """Inspect immutable multi-artifact collections."""
    with _daemon_client(ctx) as client:
        values = client.artifact_collections()
    _emit([value.model_dump(mode="json") for value in values], as_json=_state(ctx).json_output)


@artifact_app.command("references")
def list_artifact_references(ctx: typer.Context) -> None:
    """Inspect current compare-and-swap working references."""
    with _daemon_client(ctx) as client:
        values = client.artifact_references()
    _emit([value.model_dump(mode="json") for value in values], as_json=_state(ctx).json_output)


@artifact_app.command("reference-update")
def update_artifact_reference(
    ctx: typer.Context,
    scope: str,
    name: str,
    artifact: str,
    expected_reference_revision: Annotated[int, typer.Option(min=0)],
) -> None:
    """Compare-and-swap one scoped working reference."""
    from mishkan.application import ApplicationCommand

    with _daemon_client(ctx) as client:
        result = client.command(
            ApplicationCommand(
                command_type="artifact.reference.update",
                actor_id=client.principal_id,
                target_type="artifact_reference",
                payload={
                    "scope": scope,
                    "name": name,
                    "artifact_reference": artifact,
                    "expected_reference_revision": expected_reference_revision,
                },
            )
        )
    _emit(result.model_dump(mode="json"), as_json=_state(ctx).json_output)


@artifact_app.command("hold")
def hold_artifact(
    ctx: typer.Context,
    artifact: str,
    reason: Annotated[str, typer.Option()],
    expected_revision: Annotated[int | None, typer.Option(min=0)] = None,
) -> None:
    """Root an artifact with an inspectable retention reason."""
    _artifact_root_effect(ctx, artifact, "artifact.hold.set", {"reason": reason}, expected_revision)


@artifact_app.command("hold-release")
def release_artifact_hold(
    ctx: typer.Context,
    artifact: str,
    expected_revision: Annotated[int | None, typer.Option(min=0)] = None,
) -> None:
    """Release an artifact hold through the governed command boundary."""
    _artifact_root_effect(ctx, artifact, "artifact.hold.release", {}, expected_revision)


@artifact_app.command("holds")
def list_artifact_holds(ctx: typer.Context) -> None:
    """Inspect current artifact holds."""
    with _daemon_client(ctx) as client:
        values = client.artifact_holds()
    _emit([value.model_dump(mode="json") for value in values], as_json=_state(ctx).json_output)


@artifact_app.command("pin")
def pin_artifact(
    ctx: typer.Context,
    artifact: str,
    expected_revision: Annotated[int | None, typer.Option(min=0)] = None,
) -> None:
    """Pin an artifact as a durable garbage-collection root."""
    _artifact_root_effect(ctx, artifact, "artifact.pin.set", {}, expected_revision)


@artifact_app.command("pin-release")
def release_artifact_pin(
    ctx: typer.Context,
    artifact: str,
    expected_revision: Annotated[int | None, typer.Option(min=0)] = None,
) -> None:
    """Release an artifact pin through the governed command boundary."""
    _artifact_root_effect(ctx, artifact, "artifact.pin.release", {}, expected_revision)


@artifact_app.command("pins")
def list_artifact_pins(ctx: typer.Context) -> None:
    """Inspect current artifact pins."""
    with _daemon_client(ctx) as client:
        values = client.artifact_pins()
    _emit([value.model_dump(mode="json") for value in values], as_json=_state(ctx).json_output)


@artifact_app.command("gc-plan")
def plan_artifact_gc(
    ctx: typer.Context,
    watermark: Annotated[str, typer.Option(help="Aware ISO-8601 retention watermark.")],
) -> None:
    """Persist an inspectable reachability-based garbage-collection plan."""
    from mishkan.application import ApplicationCommand

    parsed = _event_time(watermark)
    assert parsed is not None
    with _daemon_client(ctx) as client:
        result = client.command(
            ApplicationCommand(
                command_type="artifact.gc.plan",
                actor_id=client.principal_id,
                target_type="artifact_service",
                payload={"watermark": parsed.isoformat()},
            )
        )
    _emit(result.model_dump(mode="json"), as_json=_state(ctx).json_output)


@artifact_app.command("gc-apply")
def apply_artifact_gc(
    ctx: typer.Context,
    plan_id: str,
    expected_revision: Annotated[int | None, typer.Option(min=0)] = None,
) -> None:
    """Apply one stored GC plan after rechecking every current root."""
    from mishkan.application import ApplicationCommand

    with _daemon_client(ctx) as client:
        result = client.command(
            ApplicationCommand(
                command_type="artifact.gc.apply",
                actor_id=client.principal_id,
                target_type="artifact_gc_plan",
                target_id=plan_id,
                expected_revision=expected_revision,
                payload={},
            )
        )
    _emit(result.model_dump(mode="json"), as_json=_state(ctx).json_output)


@artifact_app.command("reconcile-plan")
def plan_artifact_reconciliation(ctx: typer.Context) -> None:
    """Observe inconsistencies and persist a non-mutating reconciliation plan."""

    from mishkan.application import ApplicationCommand

    with _daemon_client(ctx) as client:
        result = client.command(
            ApplicationCommand(
                command_type="artifact.reconcile.plan",
                actor_id=client.principal_id,
                target_type="artifact_service",
                payload={},
            )
        )
    _emit(result.model_dump(mode="json"), as_json=_state(ctx).json_output)


@artifact_app.command("reconcile-apply")
def apply_artifact_reconciliation(
    ctx: typer.Context,
    plan_id: Annotated[str, typer.Argument()],
    expected_revision: Annotated[int | None, typer.Option(min=0)] = None,
) -> None:
    """Apply one previously persisted reconciliation plan exactly once."""

    from mishkan.application import ApplicationCommand

    with _daemon_client(ctx) as client:
        result = client.command(
            ApplicationCommand(
                command_type="artifact.reconcile.apply",
                actor_id=client.principal_id,
                target_type="artifact_reconciliation_plan",
                target_id=plan_id,
                expected_revision=expected_revision,
                payload={},
            )
        )
    _emit(result.model_dump(mode="json"), as_json=_state(ctx).json_output)


@change_app.command("plan")
def plan_change_set(
    ctx: typer.Context,
    source: Annotated[Path, typer.Option("--file", help="Versioned change-set YAML or JSON.")],
) -> None:
    """Submit an immutable change-set plan to mishkand."""
    from mishkan.application import ApplicationCommand
    from mishkan.edits import ChangeSet

    change_set = ChangeSet.model_validate(yaml.safe_load(source.read_text(encoding="utf-8")))
    with _daemon_client(ctx) as client:
        command = ApplicationCommand(
            command_type="change.plan",
            actor_id=client.principal_id,
            target_type="change_set",
            target_id=str(change_set.id),
            expected_revision=0,
            payload={"change_set": change_set.model_dump(mode="json")},
        )
        result = client.command(command)
    _emit(result.model_dump(mode="json"), as_json=_state(ctx).json_output)


@change_app.command("apply")
def apply_change_set(
    ctx: typer.Context,
    change_set_id: Annotated[str, typer.Argument()],
    expected_revision: Annotated[int, typer.Option(min=0)] = 1,
) -> None:
    """Apply a previously planned change set through mishkand."""
    from mishkan.application import ApplicationCommand

    with _daemon_client(ctx) as client:
        command = ApplicationCommand(
            command_type="change.apply",
            actor_id=client.principal_id,
            target_type="change_set",
            target_id=change_set_id,
            expected_revision=expected_revision,
            payload={},
        )
        result = client.command(command)
    _emit(result.model_dump(mode="json"), as_json=_state(ctx).json_output)


def _execute_git_effect(ctx: typer.Context, source: Path, expected_mode: str) -> None:
    from mishkan.application import ApplicationCommand
    from mishkan.edits.git import GitEffectMode, GitEffectRequest

    request = GitEffectRequest.model_validate(yaml.safe_load(source.read_text(encoding="utf-8")))
    mode = GitEffectMode(expected_mode)
    if request.mode is not mode:
        raise typer.BadParameter(f"request mode must be {mode.value}")
    repository = str(request.workspace.resolve(strict=True))
    with _daemon_client(ctx) as client:
        result = client.command(
            ApplicationCommand(
                command_type=f"git.{mode.value}",
                actor_id=client.principal_id,
                target_type="git_repository",
                target_id=repository,
                payload={"request": request.model_dump(mode="json")},
            )
        )
    _emit(result.model_dump(mode="json"), as_json=_state(ctx).json_output)


@git_app.command("stage")
def git_stage(
    ctx: typer.Context,
    source: Annotated[Path, typer.Option("--file", help="Typed Git stage request.")],
) -> None:
    """Stage exact paths under public policy."""

    _execute_git_effect(ctx, source, "stage")


@git_app.command("commit")
def git_commit(
    ctx: typer.Context,
    source: Annotated[Path, typer.Option("--file", help="Typed Git commit request.")],
) -> None:
    """Commit with an explicit identity, message, and base."""

    _execute_git_effect(ctx, source, "commit")


@git_app.command("push")
def git_push(
    ctx: typer.Context,
    source: Annotated[Path, typer.Option("--file", help="Typed Git push request.")],
) -> None:
    """Push an exact branch to a verified remote."""

    _execute_git_effect(ctx, source, "push")


@git_app.command("force-with-lease")
def git_force_with_lease(
    ctx: typer.Context,
    source: Annotated[Path, typer.Option("--file", help="Typed force-with-lease request.")],
) -> None:
    """Force with an exact observed remote lease."""

    _execute_git_effect(ctx, source, "force_with_lease")


@git_app.command("force-push")
def git_force_push(
    ctx: typer.Context,
    source: Annotated[Path, typer.Option("--file", help="Typed explicit force-push request.")],
) -> None:
    """Force push only when public policy authorizes that exact effect."""

    _execute_git_effect(ctx, source, "force_push")


def _start_session(ctx: typer.Context, source: Path, mode: str) -> None:
    from mishkan.application import ApplicationCommand
    from mishkan.execution import ExecutionMode, ExecutionRequest

    request = ExecutionRequest.model_validate(yaml.safe_load(source.read_text(encoding="utf-8")))
    expected_mode = ExecutionMode(mode)
    if request.mode is not expected_mode:
        raise typer.BadParameter(f"request mode must be {mode}")
    with _daemon_client(ctx) as client:
        result = client.command(
            ApplicationCommand(
                command_type="session.start",
                actor_id=client.principal_id,
                target_type="session_service",
                payload={"request": request.model_dump(mode="json")},
            )
        )
    _emit(result.model_dump(mode="json"), as_json=_state(ctx).json_output)


def _session_effect(
    ctx: typer.Context,
    session_id: str,
    command_type: str,
    payload: dict[str, object],
    expected_revision: int | None,
) -> None:
    from mishkan.application import ApplicationCommand

    with _daemon_client(ctx) as client:
        result = client.command(
            ApplicationCommand(
                command_type=command_type,
                actor_id=client.principal_id,
                target_type="session",
                target_id=session_id,
                expected_revision=expected_revision,
                payload=payload,
            )
        )
    _emit(result.model_dump(mode="json"), as_json=_state(ctx).json_output)


@terminal_app.command("open")
def open_terminal(
    ctx: typer.Context,
    source: Annotated[Path, typer.Option("--file", help="Versioned PTY request YAML.")],
) -> None:
    """Open a governed PTY session."""
    _start_session(ctx, source, "pty")


@job_app.command("start")
def start_job(
    ctx: typer.Context,
    source: Annotated[Path, typer.Option("--file", help="Versioned job request YAML.")],
) -> None:
    """Start a governed managed job."""
    _start_session(ctx, source, "job")


@terminal_app.command("write")
def write_terminal(
    ctx: typer.Context,
    session_id: str,
    data: str,
    declared_effect: Annotated[list[str] | None, typer.Option("--effect")] = None,
    network_destination: Annotated[list[str] | None, typer.Option("--network-destination")] = None,
    expected_revision: Annotated[int | None, typer.Option(min=0)] = None,
) -> None:
    """Write UTF-8 input to an owned PTY."""
    import base64

    _session_effect(
        ctx,
        session_id,
        "session.write",
        {
            "content_base64": base64.b64encode(data.encode()).decode(),
            "declared_effects": declared_effect or [],
            "network_destinations": network_destination or [],
        },
        expected_revision,
    )


@terminal_app.command("resize")
def resize_terminal(
    ctx: typer.Context,
    session_id: str,
    rows: Annotated[int, typer.Option(min=1, max=1000)],
    columns: Annotated[int, typer.Option(min=1, max=4000)],
    expected_revision: Annotated[int | None, typer.Option(min=0)] = None,
) -> None:
    """Resize an owned PTY."""
    _session_effect(
        ctx,
        session_id,
        "session.resize",
        {"rows": rows, "columns": columns},
        expected_revision,
    )


def _read_session(
    ctx: typer.Context,
    session_id: str,
    channel: str,
    offset: int,
    limit: int,
    binary: bool,
) -> None:
    with _daemon_client(ctx) as client:
        result = client.session_output(
            session_id, channel=channel, offset=offset, limit=limit, binary=binary
        )
    _emit(result.model_dump(mode="json"), as_json=_state(ctx).json_output)


@terminal_app.command("read")
def read_terminal(
    ctx: typer.Context,
    session_id: str,
    offset: Annotated[int, typer.Option(min=0)] = 0,
    limit: Annotated[int, typer.Option(min=1, max=16_777_216)] = 65_536,
    binary: bool = False,
) -> None:
    """Read PTY output from a durable cursor."""
    _read_session(ctx, session_id, "stdout", offset, limit, binary)


@job_app.command("read")
def read_job(
    ctx: typer.Context,
    session_id: str,
    channel: Annotated[str, typer.Option()] = "stdout",
    offset: Annotated[int, typer.Option(min=0)] = 0,
    limit: Annotated[int, typer.Option(min=1, max=16_777_216)] = 65_536,
    binary: bool = False,
) -> None:
    """Read managed-job output from a durable cursor."""
    _read_session(ctx, session_id, channel, offset, limit, binary)


@job_app.command("status")
@terminal_app.command("status")
def session_status(ctx: typer.Context, session_id: str) -> None:
    """Inspect one execution session."""
    with _daemon_client(ctx) as client:
        result = client.session(session_id)
    _emit(result.model_dump(mode="json"), as_json=_state(ctx).json_output)


@job_app.command("signal")
@terminal_app.command("signal")
def signal_session(
    ctx: typer.Context,
    session_id: str,
    signal_name: str,
    expected_revision: Annotated[int | None, typer.Option(min=0)] = None,
) -> None:
    """Send a profile-authorized signal to a proven process identity."""
    _session_effect(
        ctx,
        session_id,
        "session.signal",
        {"signal": signal_name},
        expected_revision,
    )


@job_app.command("stop")
@terminal_app.command("close")
def cancel_session(
    ctx: typer.Context,
    session_id: str,
    expected_revision: Annotated[int | None, typer.Option(min=0)] = None,
) -> None:
    """Request durable cancellation and settle the session."""
    _session_effect(ctx, session_id, "session.cancel", {}, expected_revision)


@job_app.command("settle")
def settle_job(
    ctx: typer.Context,
    session_id: str,
    expected_revision: Annotated[int | None, typer.Option(min=0)] = None,
) -> None:
    """Finalize completed job spools as immutable Artifacts."""
    _session_effect(ctx, session_id, "session.settle", {}, expected_revision)


@run_app.command("list")
def list_runs(
    ctx: typer.Context,
    offset: Annotated[int, typer.Option(min=0)] = 0,
    limit: Annotated[int, typer.Option(min=1, max=1_000)] = 100,
) -> None:
    """List bounded durable run projections."""
    with _daemon_client(ctx) as client:
        values = client.runs(offset=offset, limit=limit)
    _emit(values, as_json=_state(ctx).json_output)


@run_app.command("tasks")
def list_run_tasks(
    ctx: typer.Context,
    run_id: str,
    offset: Annotated[int, typer.Option(min=0)] = 0,
    limit: Annotated[int, typer.Option(min=1, max=1_000)] = 100,
) -> None:
    """List bounded task projections for one run."""
    with _daemon_client(ctx) as client:
        values = client.tasks(run_id, offset=offset, limit=limit)
    _emit(values, as_json=_state(ctx).json_output)


@run_app.command("prospective-create")
def create_prospective_run(
    ctx: typer.Context,
    workspace_id: Annotated[str, typer.Option("--workspace-id")],
    objective: Annotated[str, typer.Option("--objective")],
    outcome_id: Annotated[str, typer.Option("--outcome-id")],
) -> None:
    """Create a run for the configured workspace before a repository exists."""
    with _daemon_client(ctx) as client:
        value = client.create_prospective_run(
            workspace_id=workspace_id,
            objective=objective,
            outcome_id=outcome_id,
        )
    _emit(value, as_json=_state(ctx).json_output)


@run_app.command("repository-establish")
def establish_run_repository(
    ctx: typer.Context,
    run_id: str,
    workspace_id: Annotated[str, typer.Option("--workspace-id")],
    discovery_revision: Annotated[str, typer.Option("--discovery-revision")],
    evidence: Annotated[list[str], typer.Option("--evidence")],
) -> None:
    """Record an explicitly proven repository for a prospective run."""
    if not evidence:
        raise typer.BadParameter("at least one --evidence reference is required")
    with _daemon_client(ctx) as client:
        value = client.establish_repository(
            run_id,
            prospective_workspace_id=workspace_id,
            discovery_revision=discovery_revision,
            evidence_references=tuple(evidence),
        )
    _emit(value, as_json=_state(ctx).json_output)


@run_app.command("cancel")
def cancel_run(
    ctx: typer.Context,
    run_id: str,
    expected_revision: Annotated[int | None, typer.Option(min=0)] = None,
) -> None:
    """Persist monotone cancellation before stopping new eligibility."""
    _run_effect(ctx, run_id, "run.cancel", {}, expected_revision)


@run_app.command("recover")
def recover_run(
    ctx: typer.Context,
    run_id: str,
    expected_revision: Annotated[int | None, typer.Option(min=0)] = None,
) -> None:
    """Ask the daemon to release only tasks whose durable effects are reconciled."""
    _run_effect(
        ctx,
        run_id,
        "run.recover",
        {},
        expected_revision,
    )


def _run_effect(
    ctx: typer.Context,
    run_id: str,
    command_type: str,
    payload: dict[str, object],
    expected_revision: int | None,
) -> None:
    from mishkan.application import ApplicationCommand

    with _daemon_client(ctx) as client:
        result = client.command(
            ApplicationCommand(
                command_type=command_type,
                actor_id=client.principal_id,
                target_type="run",
                target_id=run_id,
                expected_revision=expected_revision,
                payload=payload,
            )
        )
    _emit(result.model_dump(mode="json"), as_json=_state(ctx).json_output)


@skill_app.command("list")
def list_skills(
    ctx: typer.Context,
    name: Annotated[str | None, typer.Option(help="Filter by exact skill identity.")] = None,
    offset: Annotated[int, typer.Option(min=0)] = 0,
    limit: Annotated[int, typer.Option(min=1, max=1_000)] = 100,
) -> None:
    """List bounded immutable skill versions and their lifecycle state."""
    with _daemon_client(ctx) as client:
        values = client.skills(name=name, offset=offset, limit=limit)
    _emit(
        [value.model_dump(mode="json") for value in values],
        as_json=_state(ctx).json_output,
    )


@skill_app.command("active")
def active_skill(
    ctx: typer.Context,
    name: Annotated[str, typer.Argument(help="Exact skill identity.")],
) -> None:
    """Show the active immutable version, if one exists."""
    with _daemon_client(ctx) as client:
        value = client.active_skill(name)
    _emit(
        None if value is None else value.model_dump(mode="json"),
        as_json=_state(ctx).json_output,
    )


@skill_app.command("invoke")
def invoke_skill(
    ctx: typer.Context,
    task_id: Annotated[str, typer.Argument(help="Durable task identity.")],
    task_class: Annotated[str, typer.Argument(help="Exact task class.")],
    selector: Annotated[
        str | None,
        typer.Argument(help="Slash skill selector such as /code-review."),
    ] = None,
    bundle: Annotated[
        str | None,
        typer.Option(help="Configured bundle identity; mutually exclusive with selector."),
    ] = None,
    organization_version: Annotated[
        str,
        typer.Option(help="Organization definition revision used by the task."),
    ] = "*",
    platform: Annotated[
        str | None,
        typer.Option(help="Observed target platform; defaults to the current Python platform."),
    ] = None,
    available_tool: Annotated[
        list[str] | None,
        typer.Option("--available-tool", help="Actually available tool identity; repeatable."),
    ] = None,
) -> None:
    """Resolve explicit `/skill`, bundle, or automatic skills and return exact evidence."""
    import sys

    from mishkan.skills import SkillInvocationRequest, SkillSelectionContext

    requested_name: str | None = None
    if selector is not None:
        if not selector.startswith("/") or len(selector) == 1:
            raise typer.BadParameter("selector must use slash form, for example /code-review")
        requested_name = selector[1:]
    if requested_name is not None and bundle is not None:
        raise typer.BadParameter("selector and --bundle are mutually exclusive")
    with _daemon_client(ctx) as client:
        request = SkillInvocationRequest(
            requested_name=requested_name,
            bundle_id=bundle,
            context=SkillSelectionContext(
                task_id=task_id,
                task_class=task_class,
                consuming_identity=client.principal_id,
                platform=platform or sys.platform,
                organization_version=organization_version,
                available_tools=frozenset(available_tool or ()),
            ),
        )
        evidence = client.invoke_skill(request)
    _emit(evidence.model_dump(mode="json"), as_json=_state(ctx).json_output)


@skill_app.command("learn")
def learn_skill(
    ctx: typer.Context,
    source: Annotated[
        str,
        typer.Argument(help="Text, local file, HTTP(S) URL, or artifact reference."),
    ],
    task_id: Annotated[str, typer.Option(help="Durable task identity.")],
    task_class: Annotated[str, typer.Option(help="Exact task class to improve.")],
    reason: Annotated[str, typer.Option(help="Why this procedural learning is requested.")],
    name: Annotated[
        str | None,
        typer.Option(help="Exact existing or proposed skill name; required when no match exists."),
    ] = None,
    source_kind: Annotated[
        str,
        typer.Option(
            "--kind",
            help="auto, text, file, url, artifact, repository_evidence, or execution_evidence.",
        ),
    ] = "auto",
    organization_version: Annotated[str, typer.Option()] = "*",
    platform: Annotated[str | None, typer.Option()] = None,
    available_tool: Annotated[
        list[str] | None,
        typer.Option("--available-tool", help="Observed available tool; repeatable."),
    ] = None,
) -> None:
    """Run `/learn <source>` as governed Research work and return a staged candidate or refusal."""
    import sys

    from mishkan.artifacts import ArtifactProvenance
    from mishkan.domain.identity import new_id
    from mishkan.skills import (
        SkillLearningRequest,
        SkillLearningSource,
        SkillLearningSourceKind,
    )

    effective = _load_or_exit(ctx).value
    if effective.skills is None or effective.artifacts is None:
        raise typer.BadParameter("skills and artifacts must be configured")
    request_id = new_id()
    inferred = source_kind
    candidate_path = Path(source)
    if inferred == "auto":
        if source.startswith(("https://", "http://")):
            inferred = "url"
        elif source.startswith("artifact:"):
            inferred = "artifact"
        elif candidate_path.is_file():
            inferred = "file"
        else:
            inferred = "text"
    with _daemon_client(ctx) as client:
        if inferred == "file":
            try:
                content = candidate_path.read_bytes()
            except OSError as exc:
                raise typer.BadParameter("learning source file cannot be read") from exc
            if len(content) > effective.skills.learning_max_source_bytes:
                raise typer.BadParameter("learning source file exceeds the configured byte bound")
            manifest = client.put_artifact(
                content,
                media_type="application/octet-stream",
                provenance=ArtifactProvenance(
                    producer_identity=client.principal_id,
                    run_id=f"skill-learning:{request_id}",
                    task_attempt_id=task_id,
                    call_id=f"source:{request_id}",
                    capability="skill.learn.source",
                    channel="learning-source",
                ),
                chunk_bytes=effective.artifacts.chunk_bytes,
                retention="skill-lineage",
            )
            learning_source = SkillLearningSource(
                kind=SkillLearningSourceKind.ARTIFACT,
                locator=manifest.reference,
            )
        else:
            try:
                selected_kind = SkillLearningSourceKind(inferred)
            except ValueError as exc:
                raise typer.BadParameter("unsupported learning source kind") from exc
            learning_source = SkillLearningSource(
                kind=selected_kind,
                locator="inline:cli" if selected_kind is SkillLearningSourceKind.TEXT else source,
                content=source if selected_kind is SkillLearningSourceKind.TEXT else None,
            )
        request = SkillLearningRequest(
            request_id=request_id,
            task_id=task_id,
            task_class=task_class,
            consuming_identity=client.principal_id,
            suggested_name=name,
            sources=(learning_source,),
            platform=platform or sys.platform,
            organization_version=organization_version,
            available_tools=frozenset(available_tool or ()),
            reason=reason,
        )
        record = client.learn_skill(request)
    _emit(record.model_dump(mode="json"), as_json=_state(ctx).json_output)


@skill_app.command("register")
def register_skill(
    ctx: typer.Context,
    record_file: Annotated[
        Path,
        typer.Option("--record", help="JSON SkillVersionRecord referencing an ArtifactCollection."),
    ],
    expected_revision: Annotated[int, typer.Option(min=0)] = 0,
) -> None:
    """Register and inspect an Artifact-first candidate through effective policy."""
    from mishkan.application import ApplicationCommand
    from mishkan.skills import SkillVersionRecord

    try:
        record = SkillVersionRecord.model_validate_json(record_file.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise typer.BadParameter("--record must contain a valid SkillVersionRecord") from exc
    with _daemon_client(ctx) as client:
        result = client.command(
            ApplicationCommand(
                command_type="skill.version.register",
                actor_id=client.principal_id,
                target_type="skill_version",
                target_id=str(record.id),
                expected_revision=expected_revision,
                payload={"record": record.model_dump(mode="json")},
            )
        )
    _emit(result.model_dump(mode="json"), as_json=_state(ctx).json_output)


@skill_app.command("decide")
def decide_skill(
    ctx: typer.Context,
    version_id: Annotated[str, typer.Argument(help="Skill version UUID.")],
    disposition: Annotated[
        str,
        typer.Option(help="One of allow, require_review, or deny."),
    ],
    reason: Annotated[str, typer.Option(help="Non-secret decision reason.")],
    expected_revision: Annotated[int | None, typer.Option(min=0)] = None,
    expected_active: Annotated[
        str | None, typer.Option(help="Current active version UUID.")
    ] = None,
    quarantine_override: Annotated[
        bool,
        typer.Option(help="Request the separately governed quarantine-override effect."),
    ] = False,
) -> None:
    """Stage, deny, or atomically activate an inspected skill version."""
    from uuid import UUID

    from mishkan.application import ApplicationCommand
    from mishkan.skills import SkillLifecycleDecision, SkillMutationDisposition

    try:
        selected = SkillMutationDisposition(disposition)
        identity = UUID(version_id)
        current = None if expected_active is None else UUID(expected_active)
    except ValueError as exc:
        raise typer.BadParameter("invalid disposition or skill version UUID") from exc
    with _daemon_client(ctx) as client:
        decision = SkillLifecycleDecision(
            version_id=identity,
            disposition=selected,
            actor_id=client.principal_id,
            policy_fingerprint="0" * 64,
            expected_active_version_id=current,
            quarantine_override=quarantine_override,
            reason=reason,
        )
        result = client.command(
            ApplicationCommand(
                command_type="skill.version.decide",
                actor_id=client.principal_id,
                target_type="skill_version",
                target_id=version_id,
                expected_revision=expected_revision,
                payload={"decision": decision.model_dump(mode="json")},
            )
        )
    _emit(result.model_dump(mode="json"), as_json=_state(ctx).json_output)


@skill_app.command("archive")
def archive_skill(
    ctx: typer.Context,
    version_id: Annotated[str, typer.Argument(help="Skill version UUID.")],
    version_revision: Annotated[int, typer.Option(min=1, help="Lifecycle CAS revision.")],
    reason: Annotated[str, typer.Option(help="Non-secret archival reason.")],
    expected_revision: Annotated[int | None, typer.Option(min=0)] = None,
) -> None:
    """Archive one unpinned version without erasing its history."""
    from uuid import UUID

    from mishkan.application import ApplicationCommand
    from mishkan.skills import SkillLifecycleDecision, SkillMutationDisposition

    try:
        identity = UUID(version_id)
    except ValueError as exc:
        raise typer.BadParameter("skill version identity must be a UUID") from exc
    with _daemon_client(ctx) as client:
        decision = SkillLifecycleDecision(
            version_id=identity,
            disposition=SkillMutationDisposition.ALLOW,
            actor_id=client.principal_id,
            policy_fingerprint="0" * 64,
            reason=reason,
        )
        result = client.command(
            ApplicationCommand(
                command_type="skill.version.archive",
                actor_id=client.principal_id,
                target_type="skill_version",
                target_id=version_id,
                expected_revision=expected_revision,
                payload={
                    "decision": decision.model_dump(mode="json"),
                    "expected_revision": version_revision,
                },
            )
        )
    _emit(result.model_dump(mode="json"), as_json=_state(ctx).json_output)


@skill_app.command("delete")
def delete_skill(
    ctx: typer.Context,
    version_id: Annotated[str, typer.Argument(help="Skill version UUID.")],
    version_revision: Annotated[int, typer.Option(min=1, help="Lifecycle CAS revision.")],
    reason: Annotated[str, typer.Option(help="Non-secret logical deletion reason.")],
    expected_revision: Annotated[int | None, typer.Option(min=0)] = None,
) -> None:
    """Logically delete one unpinned version while preserving recoverable history."""
    _archive_or_delete_skill(
        ctx,
        version_id,
        version_revision,
        reason,
        expected_revision,
        command_type="skill.version.delete",
    )


def _archive_or_delete_skill(
    ctx: typer.Context,
    version_id: str,
    version_revision: int,
    reason: str,
    expected_revision: int | None,
    *,
    command_type: str,
) -> None:
    from uuid import UUID

    from mishkan.application import ApplicationCommand
    from mishkan.skills import SkillLifecycleDecision, SkillMutationDisposition

    try:
        identity = UUID(version_id)
    except ValueError as exc:
        raise typer.BadParameter("skill version identity must be a UUID") from exc
    with _daemon_client(ctx) as client:
        decision = SkillLifecycleDecision(
            version_id=identity,
            disposition=SkillMutationDisposition.ALLOW,
            actor_id=client.principal_id,
            policy_fingerprint="0" * 64,
            reason=reason,
        )
        result = client.command(
            ApplicationCommand(
                command_type=command_type,
                actor_id=client.principal_id,
                target_type="skill_version",
                target_id=version_id,
                expected_revision=expected_revision,
                payload={
                    "decision": decision.model_dump(mode="json"),
                    "expected_revision": version_revision,
                },
            )
        )
    _emit(result.model_dump(mode="json"), as_json=_state(ctx).json_output)


def _reactivate_skill(
    ctx: typer.Context,
    version_id: str,
    version_revision: int,
    reason: str,
    expected_active: str | None,
    expected_revision: int | None,
    *,
    operation: str,
) -> None:
    from uuid import UUID

    from mishkan.application import ApplicationCommand
    from mishkan.skills import SkillLifecycleDecision, SkillMutationDisposition

    try:
        identity = UUID(version_id)
        active_identity = None if expected_active is None else UUID(expected_active)
    except ValueError as exc:
        raise typer.BadParameter("skill version identities must be UUIDs") from exc
    with _daemon_client(ctx) as client:
        decision = SkillLifecycleDecision(
            version_id=identity,
            disposition=SkillMutationDisposition.ALLOW,
            actor_id=client.principal_id,
            policy_fingerprint="0" * 64,
            expected_active_version_id=active_identity,
            reason=reason,
        )
        result = client.command(
            ApplicationCommand(
                command_type=f"skill.version.{operation}",
                actor_id=client.principal_id,
                target_type="skill_version",
                target_id=version_id,
                expected_revision=expected_revision,
                payload={
                    "decision": decision.model_dump(mode="json"),
                    "expected_revision": version_revision,
                },
            )
        )
    _emit(result.model_dump(mode="json"), as_json=_state(ctx).json_output)


@skill_app.command("restore")
def restore_skill(
    ctx: typer.Context,
    version_id: Annotated[str, typer.Argument(help="Archived skill version UUID.")],
    version_revision: Annotated[int, typer.Option(min=1)],
    reason: Annotated[str, typer.Option(help="Non-secret restoration reason.")],
    expected_active: Annotated[str | None, typer.Option()] = None,
    expected_revision: Annotated[int | None, typer.Option(min=0)] = None,
) -> None:
    """Atomically restore an archived immutable version through policy and CAS."""
    _reactivate_skill(
        ctx,
        version_id,
        version_revision,
        reason,
        expected_active,
        expected_revision,
        operation="restore",
    )


@skill_app.command("reset")
def reset_skill(
    ctx: typer.Context,
    version_id: Annotated[str, typer.Argument(help="Prior inspected version UUID.")],
    version_revision: Annotated[int, typer.Option(min=1)],
    reason: Annotated[str, typer.Option(help="Non-secret reset reason.")],
    expected_active: Annotated[str, typer.Option(help="Current active version UUID.")],
    expected_revision: Annotated[int | None, typer.Option(min=0)] = None,
) -> None:
    """Atomically reset an active pointer to a prior inspected version through CAS."""
    _reactivate_skill(
        ctx,
        version_id,
        version_revision,
        reason,
        expected_active,
        expected_revision,
        operation="reset",
    )


def _pin_skill(
    ctx: typer.Context,
    version_id: str,
    version_revision: int,
    expected_revision: int | None,
    *,
    pinned: bool,
) -> None:
    from mishkan.application import ApplicationCommand

    with _daemon_client(ctx) as client:
        result = client.command(
            ApplicationCommand(
                command_type="skill.version.pin" if pinned else "skill.version.unpin",
                actor_id=client.principal_id,
                target_type="skill_version",
                target_id=version_id,
                expected_revision=expected_revision,
                payload={"expected_revision": version_revision},
            )
        )
    _emit(result.model_dump(mode="json"), as_json=_state(ctx).json_output)


@skill_app.command("pin")
def pin_skill(
    ctx: typer.Context,
    version_id: str,
    version_revision: Annotated[int, typer.Option(min=1)],
    expected_revision: Annotated[int | None, typer.Option(min=0)] = None,
) -> None:
    """Protect a skill version from configured archival curation."""
    _pin_skill(ctx, version_id, version_revision, expected_revision, pinned=True)


@skill_app.command("unpin")
def unpin_skill(
    ctx: typer.Context,
    version_id: str,
    version_revision: Annotated[int, typer.Option(min=1)],
    expected_revision: Annotated[int | None, typer.Option(min=0)] = None,
) -> None:
    """Remove archival protection through the same command authority."""
    _pin_skill(ctx, version_id, version_revision, expected_revision, pinned=False)


@skill_app.command("usage")
def skill_usage(
    ctx: typer.Context,
    task_class: Annotated[str, typer.Argument(help="Exact task class.")],
    name: Annotated[str | None, typer.Option(help="Optional exact skill identity.")] = None,
) -> None:
    """Show durable hit, partial, and miss aggregates without activating anything."""
    with _daemon_client(ctx) as client:
        summary = client.skill_usage_summary(task_class, skill_name=name)
    _emit(summary.model_dump(mode="json"), as_json=_state(ctx).json_output)


@skill_app.command("updates")
def skill_updates(ctx: typer.Context) -> None:
    """Detect configured-source updates and conflicts without activating them."""
    with _daemon_client(ctx) as client:
        report = client.skill_updates()
    _emit(report.model_dump(mode="json"), as_json=_state(ctx).json_output)


@skill_app.command("curation")
def skill_curation(ctx: typer.Context) -> None:
    """Show non-destructive archival proposals under the configured stale-use rule."""
    with _daemon_client(ctx) as client:
        proposals = client.skill_curation()
    _emit(
        [item.model_dump(mode="json") for item in proposals],
        as_json=_state(ctx).json_output,
    )


@environment_app.command("observe")
def observe_environment(
    ctx: typer.Context,
    context_id: Annotated[str, typer.Option(help="Mission-local environment context identity.")],
    execution_location: Annotated[
        str,
        typer.Option(help="Exact machine, worker, or prospective location identity."),
    ],
    repository_id: Annotated[str | None, typer.Option()] = None,
    repository_revision: Annotated[str | None, typer.Option()] = None,
) -> None:
    """Record bounded read-only evidence for the configured project workspace."""
    from mishkan.environment import EnvironmentObservationRequest

    with _daemon_client(ctx) as client:
        observation = client.observe_environment(
            EnvironmentObservationRequest(
                actor_identity=client.principal_id,
                context_id=context_id,
                repository_id=repository_id,
                repository_revision=repository_revision,
                execution_location=execution_location,
            )
        )
    _emit(observation.model_dump(mode="json"), as_json=_state(ctx).json_output)


@environment_app.command("resolve")
def resolve_environment(
    ctx: typer.Context,
    request_file: Annotated[
        Path,
        typer.Option("--request", help="JSON EnvironmentBindingRequest authored by the crew."),
    ],
) -> None:
    """Resolve one exact agent-authored outcome without substituting another outcome."""
    from mishkan.environment import EnvironmentBindingRequest

    try:
        request = EnvironmentBindingRequest.model_validate_json(
            request_file.read_text(encoding="utf-8")
        )
    except (OSError, ValueError) as exc:
        raise typer.BadParameter(
            "--request must contain a valid EnvironmentBindingRequest"
        ) from exc
    with _daemon_client(ctx) as client:
        if request.owner_identity != client.principal_id:
            raise typer.BadParameter(
                "request owner_identity must match the authenticated daemon principal"
            )
        binding = client.resolve_environment(request)
    _emit(binding.model_dump(mode="json"), as_json=_state(ctx).json_output)


@environment_app.command("observation")
def show_environment_observation(
    ctx: typer.Context,
    observation_id: Annotated[str, typer.Argument(help="Environment observation UUID.")],
) -> None:
    """Show one exact durable observation."""
    with _daemon_client(ctx) as client:
        observation = client.environment_observation(observation_id)
    _emit(observation.model_dump(mode="json"), as_json=_state(ctx).json_output)


@environment_app.command("binding")
def show_environment_binding(
    ctx: typer.Context,
    binding_id: Annotated[str, typer.Argument(help="Environment binding UUID.")],
) -> None:
    """Show one exact durable compatibility decision."""
    with _daemon_client(ctx) as client:
        binding = client.environment_binding(binding_id)
    _emit(binding.model_dump(mode="json"), as_json=_state(ctx).json_output)


@environment_app.command("command-candidates")
def show_engineering_command_candidates(
    ctx: typer.Context,
    observation_id: Annotated[str, typer.Argument(help="Environment observation UUID.")],
) -> None:
    """Show exact executable pack commands and explicit unavailable alternatives."""
    with _daemon_client(ctx) as client:
        candidates = client.engineering_command_candidates(observation_id)
    _emit(
        [item.model_dump(mode="json") for item in candidates],
        as_json=_state(ctx).json_output,
    )


def _engineering_command_request(source: Path):  # type: ignore[no-untyped-def]
    from mishkan.environment import EngineeringCommandRequest

    try:
        return EngineeringCommandRequest.model_validate_json(source.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise typer.BadParameter(
            "--request must contain a valid EngineeringCommandRequest"
        ) from exc


@environment_app.command("plan-command")
def plan_engineering_command(
    ctx: typer.Context,
    request_file: Annotated[
        Path,
        typer.Option("--request", help="JSON exact technical-pack command request."),
    ],
) -> None:
    """Resolve an applicable pack action to one observed executable without running it."""
    request = _engineering_command_request(request_file)
    with _daemon_client(ctx) as client:
        if request.owner_identity != client.principal_id:
            raise typer.BadParameter(
                "request owner_identity must match the authenticated daemon principal"
            )
        plan = client.plan_engineering_command(request)
    _emit(plan.model_dump(mode="json"), as_json=_state(ctx).json_output)


@environment_app.command("start-command")
def start_engineering_command(
    ctx: typer.Context,
    request_file: Annotated[
        Path,
        typer.Option("--request", help="JSON exact technical-pack command request."),
    ],
) -> None:
    """Plan and start a pack command through the governed I03 session supervisor."""
    request = _engineering_command_request(request_file)
    with _daemon_client(ctx) as client:
        if request.owner_identity != client.principal_id:
            raise typer.BadParameter(
                "request owner_identity must match the authenticated daemon principal"
            )
        plan, session = client.start_engineering_command(request)
    _emit(
        {
            "plan": plan.model_dump(mode="json"),
            "session": session.model_dump(mode="json"),
        },
        as_json=_state(ctx).json_output,
    )


@environment_app.command("validate-descriptors")
def validate_environment_descriptors(
    ctx: typer.Context,
    descriptor_file: Annotated[
        Path,
        typer.Option("--set", help="JSON EnvironmentDescriptorSet using immutable artifacts."),
    ],
) -> None:
    """Validate and, only when valid, record an exact descriptor set."""
    from mishkan.environment import EnvironmentDescriptorSet

    try:
        descriptor_set = EnvironmentDescriptorSet.model_validate_json(
            descriptor_file.read_text(encoding="utf-8")
        )
    except (OSError, ValueError) as exc:
        raise typer.BadParameter("--set must contain a valid EnvironmentDescriptorSet") from exc
    with _daemon_client(ctx) as client:
        result = client.validate_environment_descriptors(descriptor_set)
    _emit(result.model_dump(mode="json"), as_json=_state(ctx).json_output)


@environment_app.command("plan-descriptor-change")
def plan_environment_descriptor_change(
    ctx: typer.Context,
    request_file: Annotated[
        Path,
        typer.Option("--request", help="JSON EnvironmentDescriptorChangeRequest."),
    ],
) -> None:
    """Compose validated artifacts into an exact-base change set without applying it."""
    from mishkan.environment import EnvironmentDescriptorChangeRequest

    try:
        request = EnvironmentDescriptorChangeRequest.model_validate_json(
            request_file.read_text(encoding="utf-8")
        )
    except (OSError, ValueError) as exc:
        raise typer.BadParameter(
            "--request must contain a valid EnvironmentDescriptorChangeRequest"
        ) from exc
    with _daemon_client(ctx) as client:
        if request.owner_identity != client.principal_id:
            raise typer.BadParameter(
                "request owner_identity must match the authenticated daemon principal"
            )
        plan = client.plan_environment_descriptor_change(request)
    _emit(plan.model_dump(mode="json"), as_json=_state(ctx).json_output)


def _environment_operation_request(source: Path):  # type: ignore[no-untyped-def]
    from mishkan.environment import EnvironmentOperationRequest

    try:
        return EnvironmentOperationRequest.model_validate_json(source.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise typer.BadParameter(
            "--request must contain a valid EnvironmentOperationRequest"
        ) from exc


@environment_app.command("plan-operation")
def plan_environment_operation(
    ctx: typer.Context,
    request_file: Annotated[
        Path,
        typer.Option("--request", help="JSON exact adapter operation request."),
    ],
) -> None:
    """Produce a literal policy-visible execution request without executing it."""
    request = _environment_operation_request(request_file)
    with _daemon_client(ctx) as client:
        if request.owner_identity != client.principal_id:
            raise typer.BadParameter(
                "request owner_identity must match the authenticated daemon principal"
            )
        plan = client.plan_environment_operation(request)
    _emit(plan.model_dump(mode="json"), as_json=_state(ctx).json_output)


@environment_app.command("start-operation")
def start_environment_operation(
    ctx: typer.Context,
    request_file: Annotated[
        Path,
        typer.Option("--request", help="JSON exact managed-job operation request."),
    ],
) -> None:
    """Plan then start a governed environment operation through the I03 job supervisor."""
    request = _environment_operation_request(request_file)
    with _daemon_client(ctx) as client:
        if request.owner_identity != client.principal_id:
            raise typer.BadParameter(
                "request owner_identity must match the authenticated daemon principal"
            )
        plan, session = client.start_environment_operation(request)
    _emit(
        {
            "plan": plan.model_dump(mode="json"),
            "session": session.model_dump(mode="json"),
        },
        as_json=_state(ctx).json_output,
    )


@environment_app.command("descriptor-set")
def show_environment_descriptor_set(
    ctx: typer.Context,
    descriptor_set_id: Annotated[str, typer.Argument(help="Environment descriptor-set UUID.")],
) -> None:
    """Show one validated durable descriptor set."""
    with _daemon_client(ctx) as client:
        descriptor_set = client.environment_descriptor_set(descriptor_set_id)
    _emit(descriptor_set.model_dump(mode="json"), as_json=_state(ctx).json_output)


@environment_app.command("settle-attempt")
def settle_environment_attempt(
    ctx: typer.Context,
    plan_file: Annotated[
        Path,
        typer.Option("--plan", help="JSON EnvironmentOperationPlan returned by mishkand."),
    ],
    session_id: Annotated[str, typer.Option(help="Settled managed-job UUID.")],
) -> None:
    """Derive durable attempt evidence from an exact plan and terminal session."""
    from mishkan.environment import EnvironmentOperationPlan

    try:
        plan = EnvironmentOperationPlan.model_validate_json(plan_file.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise typer.BadParameter("--plan must contain a valid EnvironmentOperationPlan") from exc
    with _daemon_client(ctx) as client:
        attempt = client.settle_environment_attempt(plan, session_id)
    _emit(attempt.model_dump(mode="json"), as_json=_state(ctx).json_output)


@environment_app.command("verify")
def verify_environment(
    ctx: typer.Context,
    request_file: Annotated[
        Path,
        typer.Option("--request", help="JSON EnvironmentVerificationRequest."),
    ],
) -> None:
    """Derive location-bound verification from durable execution attempts."""
    from mishkan.environment import EnvironmentVerificationRequest

    try:
        request = EnvironmentVerificationRequest.model_validate_json(
            request_file.read_text(encoding="utf-8")
        )
    except (OSError, ValueError) as exc:
        raise typer.BadParameter(
            "--request must contain a valid EnvironmentVerificationRequest"
        ) from exc
    with _daemon_client(ctx) as client:
        verification = client.verify_environment(request)
    _emit(verification.model_dump(mode="json"), as_json=_state(ctx).json_output)


@environment_app.command("invalidate")
def invalidate_environment(
    ctx: typer.Context,
    invalidation_file: Annotated[
        Path,
        typer.Option("--request", help="JSON EnvironmentInvalidation."),
    ],
) -> None:
    """Invalidate only one binding and its exact dependent task set."""
    from mishkan.environment import EnvironmentInvalidation

    try:
        invalidation = EnvironmentInvalidation.model_validate_json(
            invalidation_file.read_text(encoding="utf-8")
        )
    except (OSError, ValueError) as exc:
        raise typer.BadParameter("--request must contain a valid EnvironmentInvalidation") from exc
    with _daemon_client(ctx) as client:
        recorded = client.invalidate_environment(invalidation)
    _emit(recorded.model_dump(mode="json"), as_json=_state(ctx).json_output)


@environment_app.command("attempt")
def show_environment_attempt(
    ctx: typer.Context,
    attempt_id: Annotated[str, typer.Argument(help="Environment attempt UUID.")],
) -> None:
    """Show one durable environment attempt."""
    with _daemon_client(ctx) as client:
        attempt = client.environment_attempt(attempt_id)
    _emit(attempt.model_dump(mode="json"), as_json=_state(ctx).json_output)


@environment_app.command("verification")
def show_environment_verification(
    ctx: typer.Context,
    verification_id: Annotated[str, typer.Argument(help="Environment verification UUID.")],
) -> None:
    """Show one location-bound environment verification."""
    with _daemon_client(ctx) as client:
        verification = client.environment_verification(verification_id)
    _emit(verification.model_dump(mode="json"), as_json=_state(ctx).json_output)


@mcp_app.command("connect")
def connect_mcp(
    ctx: typer.Context,
    connection_id: Annotated[str, typer.Argument(help="Configured MCP connection identity.")],
    expected_revision: Annotated[int | None, typer.Option(min=0)] = None,
) -> None:
    """Explicitly connect or reconnect one configured MCP peer and discover its claims."""
    from mishkan.application import ApplicationCommand

    with _daemon_client(ctx) as client:
        result = client.command(
            ApplicationCommand(
                command_type="mcp.connection.connect",
                actor_id=client.principal_id,
                target_type="mcp_connection",
                target_id=connection_id,
                expected_revision=expected_revision,
                payload={},
            )
        )
    _emit(result.model_dump(mode="json"), as_json=_state(ctx).json_output)


@mcp_app.command("connections")
def list_mcp_connections(
    ctx: typer.Context,
    offset: Annotated[int, typer.Option(min=0)] = 0,
    limit: Annotated[int, typer.Option(min=1, max=1_000)] = 100,
) -> None:
    """List bounded durable MCP connection states."""
    with _daemon_client(ctx) as client:
        values = client.mcp_connections(offset=offset, limit=limit)
    _emit(list(values), as_json=_state(ctx).json_output)


@mcp_app.command("primitives")
def list_mcp_primitives(
    ctx: typer.Context,
    connection_id: Annotated[str, typer.Argument(help="Configured MCP connection identity.")],
) -> None:
    """List normalized claims from the accepted discovery snapshot."""
    with _daemon_client(ctx) as client:
        values = client.mcp_primitives(connection_id)
    _emit(list(values), as_json=_state(ctx).json_output)


@mcp_app.command("calls")
def list_mcp_calls(
    ctx: typer.Context,
    offset: Annotated[int, typer.Option(min=0)] = 0,
    limit: Annotated[int, typer.Option(min=1, max=1_000)] = 100,
) -> None:
    """List bounded durable outbound MCP call journals."""
    with _daemon_client(ctx) as client:
        values = client.mcp_calls(offset=offset, limit=limit)
    _emit(list(values), as_json=_state(ctx).json_output)


@mcp_app.command("contracts")
def list_mcp_contracts(
    ctx: typer.Context,
    connection_id: Annotated[str, typer.Argument(help="Configured MCP connection identity.")],
) -> None:
    """List candidate Gateway contracts derived from one accepted discovery snapshot."""
    with _daemon_client(ctx) as client:
        values = client.mcp_contracts(connection_id)
    _emit(list(values), as_json=_state(ctx).json_output)


@mcp_app.command("progress")
def list_mcp_progress(
    ctx: typer.Context,
    request_id: Annotated[str, typer.Argument(help="MCP call request UUID.")],
    cursor: Annotated[int, typer.Option(min=0)] = 0,
) -> None:
    """Read durable progress from an exact monotone cursor."""
    with _daemon_client(ctx) as client:
        values = client.mcp_progress(request_id, cursor=cursor)
    _emit(list(values), as_json=_state(ctx).json_output)


@mcp_app.command("cancel")
def cancel_mcp_call(
    ctx: typer.Context,
    request_id: Annotated[str, typer.Argument(help="MCP call request UUID.")],
    expected_revision: Annotated[int | None, typer.Option(min=0)] = None,
) -> None:
    """Request cancellation without claiming that a remote effect was stopped."""
    from mishkan.application import ApplicationCommand

    with _daemon_client(ctx) as client:
        result = client.command(
            ApplicationCommand(
                command_type="mcp.call.cancel",
                actor_id=client.principal_id,
                target_type="mcp_call",
                target_id=request_id,
                expected_revision=expected_revision,
                payload={},
            )
        )
    _emit(result.model_dump(mode="json"), as_json=_state(ctx).json_output)


@mcp_app.command("reconcile")
def reconcile_mcp_call(
    ctx: typer.Context,
    request_id: Annotated[str, typer.Argument(help="Recoverable MCP call request UUID.")],
    expected_revision: Annotated[int | None, typer.Option(min=0)] = None,
) -> None:
    """Reconnect to a durable remote task and accept only its proven terminal result."""
    from mishkan.application import ApplicationCommand

    with _daemon_client(ctx) as client:
        result = client.command(
            ApplicationCommand(
                command_type="mcp.call.reconcile",
                actor_id=client.principal_id,
                target_type="mcp_call",
                target_id=request_id,
                expected_revision=expected_revision,
                payload={},
            )
        )
    _emit(result.model_dump(mode="json"), as_json=_state(ctx).json_output)


@schema_app.command("export")
def export_contract_schemas(
    ctx: typer.Context,
    output: Annotated[Path, typer.Option(help="Schema output directory.")] = Path(
        "definitions/schemas"
    ),
) -> None:
    """Export deterministic JSON Schemas for public contracts."""

    paths = export_schemas(output)
    _emit({"exported": [str(path) for path in paths]}, as_json=_state(ctx).json_output)


@app.command("init")
def initialize_repository(
    ctx: typer.Context,
    objective: Annotated[
        str,
        typer.Argument(help="Repository-specific initialization objective."),
    ],
    repository: Annotated[
        Path | None,
        typer.Option("--repository", "-r", help="Git repository to initialize."),
    ] = None,
) -> None:
    """Submit repository initialization to the authoritative local daemon."""

    state = _state(ctx)
    effective = _load_or_exit(ctx)
    workspace = effective.value.project.workspace.resolve()
    if repository is not None and repository.resolve() != workspace:
        raise typer.BadParameter(
            "--repository must match the workspace configured for this mishkand instance",
            param_hint="--repository",
        )
    try:
        from mishkan.application import ApplicationCommand, RunInitializationRequest

        with _daemon_client(ctx) as client:
            result = client.command(
                ApplicationCommand(
                    command_type="run.initialize",
                    actor_id=client.principal_id,
                    target_type="run",
                    payload=RunInitializationRequest(objective=objective).model_dump(mode="json"),
                )
            )
    except MishkanError as error:
        _emit_error(error, as_json=state.json_output)
        raise typer.Exit(code=2) from error
    _emit(result.model_dump(mode="json"), as_json=state.json_output)


if __name__ == "__main__":
    app()
