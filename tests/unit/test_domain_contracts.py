import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from mishkan.domain.errors import ErrorCode, MishkanError
from mishkan.domain.export import SCHEMAS, export_schemas
from mishkan.domain.identity import DomainRecord, new_id
from mishkan.domain.schema import SchemaRegistry
from mishkan.domain.time import render_timestamp


def test_generated_identifiers_are_unique() -> None:
    assert len({new_id() for _ in range(1_000)}) == 1_000


def test_domain_record_requires_an_unambiguous_timestamp() -> None:
    with pytest.raises(ValueError, match="timezone offset"):
        DomainRecord(created_at=datetime(2026, 8, 23, 12, 0))


def test_timestamp_rendering_names_the_applied_timezone() -> None:
    rendered = render_timestamp(datetime(2026, 8, 23, 12, 0, tzinfo=UTC), "Africa/Ouagadougou")
    assert rendered == "2026-08-23T12:00:00+00:00 [Africa/Ouagadougou]"


def test_unknown_timezone_is_rejected() -> None:
    with pytest.raises(ValueError, match="unknown IANA timezone"):
        render_timestamp(datetime.now(UTC), "Mars/Olympus_Mons")


def test_schema_registry_refuses_automatic_migration() -> None:
    with pytest.raises(MishkanError) as caught:
        SchemaRegistry.require_supported("mishkan.config", "2.0")
    assert caught.value.envelope.code is ErrorCode.VERSION
    assert caught.value.envelope.details["automatic_migration"] is False


def test_error_catalogue_matches_the_srs_namespace() -> None:
    assert {code.value for code in ErrorCode} == {
        "ERR-CFG-001",
        "ERR-PRJ-001",
        "ERR-PLN-001",
        "ERR-PLN-002",
        "ERR-DEC-001",
        "ERR-DEC-002",
        "ERR-POL-001",
        "ERR-POL-002",
        "ERR-ROL-001",
        "ERR-OUT-001",
        "ERR-REV-001",
        "ERR-RUN-001",
        "ERR-RUN-002",
        "ERR-DEP-001",
        "ERR-DEP-002",
        "ERR-SEC-001",
        "ERR-SKL-001",
        "ERR-SKL-002",
        "ERR-SKL-003",
        "ERR-TOL-001",
        "ERR-TOL-002",
        "ERR-TOL-003",
        "ERR-TOL-004",
        "ERR-TOL-005",
        "ERR-CTX-001",
        "ERR-MSN-001",
        "ERR-FIL-001",
        "ERR-EDT-001",
        "ERR-EXE-001",
        "ERR-WEB-001",
        "ERR-BRW-001",
        "ERR-ART-001",
        "ERR-MCP-001",
        "ERR-ENG-001",
        "ERR-SCH-001",
        "ERR-WRK-001",
        "ERR-VER-001",
    }


def test_public_contract_catalogue_exports_deterministically(tmp_path: Path) -> None:
    expected = {
        "application-command-v1.schema.json",
        "artifact-collection-v1.schema.json",
        "artifact-hold-v1.schema.json",
        "artifact-gc-plan-v1.schema.json",
        "artifact-manifest-v1.schema.json",
        "artifact-pin-v1.schema.json",
        "artifact-reconciliation-plan-v1.schema.json",
        "artifact-upload-session-v1.schema.json",
        "artifact-working-reference-v1.schema.json",
        "browser-action-request-v1.schema.json",
        "browser-action-result-v1.schema.json",
        "browser-diagnostic-request-v1.schema.json",
        "browser-diagnostic-result-v1.schema.json",
        "browser-observation-request-v1.schema.json",
        "browser-observation-v1.schema.json",
        "browser-session-request-v1.schema.json",
        "browser-session-v1.schema.json",
        "candidate-assessment-v1.schema.json",
        "candidate-constraints-v1.schema.json",
        "change-set-result-v1.schema.json",
        "change-set-v1.schema.json",
        "command-result-v1.schema.json",
        "community-candidate-catalogue-v1.schema.json",
        "community-candidate-v1.schema.json",
        "config-v1.schema.json",
        "confirmed-engineer-fact-v1.schema.json",
        "context-pack-manifest-v1.schema.json",
        "context-pack-materialization-v1.schema.json",
        "contextual-recommendation-request-v1.schema.json",
        "contextual-recommendation-v1.schema.json",
        "crewai-cto-mission-review-v1.schema.json",
        "crewai-mission-governance-result-v1.schema.json",
        "crewai-mission-governance-disagreement-v1.schema.json",
        "crewai-mission-governance-request-v1.schema.json",
        "crewai-pm-mission-proposal-v1.schema.json",
        "crewai-mission-environment-output-v1.schema.json",
        "conversation-channel-v1.schema.json",
        "conversation-message-v1.schema.json",
        "decision-explanation-preference-v1.schema.json",
        "domain-record-v1.schema.json",
        "error-envelope-v1.schema.json",
        "engineering-command-candidate-v1.schema.json",
        "engineering-command-plan-v1.schema.json",
        "engineering-command-request-v1.schema.json",
        "engineer-profile-v1.schema.json",
        "environment-attempt-v1.schema.json",
        "environment-binding-request-v1.schema.json",
        "environment-binding-v1.schema.json",
        "environment-descriptor-change-plan-v1.schema.json",
        "environment-descriptor-change-request-v1.schema.json",
        "environment-descriptor-validation-result-v1.schema.json",
        "environment-descriptor-set-v1.schema.json",
        "environment-invalidation-v1.schema.json",
        "environment-observation-request-v1.schema.json",
        "environment-observation-v1.schema.json",
        "environment-operation-plan-v1.schema.json",
        "environment-operation-request-v1.schema.json",
        "environment-verification-v1.schema.json",
        "environment-verification-request-v1.schema.json",
        "event-envelope-v1.schema.json",
        "event-hold-v1.schema.json",
        "event-page-v1.schema.json",
        "event-retention-plan-v1.schema.json",
        "event-retention-policy-v1.schema.json",
        "execution-request-v1.schema.json",
        "execution-result-v1.schema.json",
        "execution-cursor-read-v1.schema.json",
        "execution-session-v1.schema.json",
        "git-effect-request-v1.schema.json",
        "git-effect-result-v1.schema.json",
        "knowledge-bundle-v1.schema.json",
        "knowledge-corpus-v1.schema.json",
        "knowledge-item-v1.schema.json",
        "knowledge-memory-proposal-v1.schema.json",
        "knowledge-operation-v1.schema.json",
        "knowledge-promotion-v1.schema.json",
        "knowledge-query-record-v1.schema.json",
        "knowledge-query-v1.schema.json",
        "knowledge-scope-v1.schema.json",
        "knowledge-source-attempt-v1.schema.json",
        "mcp-call-request-v1.schema.json",
        "mcp-call-result-v1.schema.json",
        "mcp-connection-v1.schema.json",
        "mcp-discovery-v1.schema.json",
        "mcp-primitive-v1.schema.json",
        "mcp-progress-v1.schema.json",
        "mission-brief-v1.schema.json",
        "mission-completion-readiness-v1.schema.json",
        "mission-decision-v1.schema.json",
        "mission-escalation-v1.schema.json",
        "mission-intervention-v1.schema.json",
        "mission-crew-revision-v1.schema.json",
        "mission-environment-intent-v1.schema.json",
        "mission-environment-plan-v1.schema.json",
        "mission-environment-plan-acceptance-v1.schema.json",
        "mission-environment-planning-request-v1.schema.json",
        "mission-environment-readiness-v1.schema.json",
        "mission-executive-confirmation-v1.schema.json",
        "mission-origin-v1.schema.json",
        "mission-record-v1.schema.json",
        "mission-resource-limit-v1.schema.json",
        "mission-run-binding-v1.schema.json",
        "mission-run-report-v1.schema.json",
        "mission-run-report-task-v1.schema.json",
        "mission-task-assignment-v1.schema.json",
        "mission-task-acceptance-status-v1.schema.json",
        "mission-task-claim-request-v1.schema.json",
        "mission-task-claim-v1.schema.json",
        "mission-task-eligibility-v1.schema.json",
        "mission-template-catalogue-v1.schema.json",
        "mission-template-definition-v1.schema.json",
        "mission-template-reference-v1.schema.json",
        "mission-transition-v1.schema.json",
        "notification-config-v1.schema.json",
        "notification-page-v1.schema.json",
        "notification-record-v1.schema.json",
        "organization-roster-v1.schema.json",
        "plan-accepted-v1.schema.json",
        "plan-candidate-v1.schema.json",
        "plan-execution-context-v1.schema.json",
        "plan-organization-binding-v1.schema.json",
        "planning-result-v1.schema.json",
        "professional-competence-state-v1.schema.json",
        "professional-evidence-record-v1.schema.json",
        "professional-promotion-decision-v1.schema.json",
        "professional-promotion-request-v1.schema.json",
        "project-discovery-v1.schema.json",
        "prospective-run-request-v1.schema.json",
        "prospective-workspace-binding-v1.schema.json",
        "recommendation-criterion-v1.schema.json",
        "repository-binding-v1.schema.json",
        "repository-establishment-request-v1.schema.json",
        "repository-establishment-v1.schema.json",
        "langsmith-feedback-import-request-v1.schema.json",
        "run-initialization-request-v1.schema.json",
        "skill-bundle-definition-v1.schema.json",
        "skill-bundle-resolution-v1.schema.json",
        "skill-curation-proposal-v1.schema.json",
        "skill-inspection-profile-v1.schema.json",
        "skill-inspection-result-v1.schema.json",
        "skill-invocation-evidence-v1.schema.json",
        "skill-invocation-request-v1.schema.json",
        "skill-learning-record-v1.schema.json",
        "skill-learning-request-v1.schema.json",
        "skill-learning-review-v1.schema.json",
        "skill-learning-source-v1.schema.json",
        "skill-lifecycle-decision-v1.schema.json",
        "skill-load-evidence-v1.schema.json",
        "skill-metadata-v1.schema.json",
        "skill-package-draft-v1.schema.json",
        "skill-selection-v1.schema.json",
        "skill-source-v1.schema.json",
        "skill-usage-record-v1.schema.json",
        "skill-usage-summary-v1.schema.json",
        "skill-update-evidence-v1.schema.json",
        "skill-update-report-v1.schema.json",
        "skill-version-record-v1.schema.json",
        "web-citation-evidence-v1.schema.json",
        "web-crawl-request-v1.schema.json",
        "web-crawl-result-v1.schema.json",
        "web-extraction-request-v1.schema.json",
        "web-extraction-result-v1.schema.json",
        "web-fetch-request-v1.schema.json",
        "web-fetch-result-v1.schema.json",
        "web-http-request-v1.schema.json",
        "web-http-result-v1.schema.json",
        "web-map-request-v1.schema.json",
        "web-map-result-v1.schema.json",
        "web-search-request-v1.schema.json",
        "web-search-response-v1.schema.json",
        "snapshot-envelope-v1.schema.json",
        "task-review-rejection-v1.schema.json",
        "telemetry-export-evidence-v1.schema.json",
        "telemetry-evaluation-candidate-v1.schema.json",
        "telemetry-evaluation-import-result-v1.schema.json",
        "telemetry-record-v1.schema.json",
        "telemetry-status-v1.schema.json",
        "technical-pack-catalogue-v1.schema.json",
    }
    assert set(SCHEMAS) == expected

    first = export_schemas(tmp_path)
    initial_content = {path.name: path.read_bytes() for path in first}
    second = export_schemas(tmp_path)

    assert {path.name for path in first} == expected
    assert {path.name: path.read_bytes() for path in second} == initial_content


def test_schema_export_prunes_only_previously_generated_stale_contracts(tmp_path: Path) -> None:
    stale = tmp_path / "stale-v1.schema.json"
    custom = tmp_path / "custom-v1.schema.json"
    stale.write_text("{}", encoding="utf-8")
    custom.write_text("{}", encoding="utf-8")
    (tmp_path / ".mishkan-schema-export.json").write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "files": ["stale-v1.schema.json"],
            }
        ),
        encoding="utf-8",
    )

    export_schemas(tmp_path)

    assert not stale.exists()
    assert custom.is_file()
