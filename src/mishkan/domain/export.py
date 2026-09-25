"""Export public contract schemas for non-Python consumers."""

import json
from pathlib import Path

from pydantic import BaseModel

from mishkan.application.contracts import (
    ApplicationCommand,
    CommandResult,
    ProspectiveRunRequest,
    RepositoryEstablishmentRequest,
    RunInitializationRequest,
    SnapshotEnvelope,
)
from mishkan.artifacts.models import (
    ArtifactCollection,
    ArtifactManifest,
    ArtifactPin,
    ArtifactReconciliationPlan,
    GarbageCollectionPlan,
    UploadSession,
    WorkingReference,
)
from mishkan.artifacts.models import (
    ArtifactHold as ArtifactEvidenceHold,
)
from mishkan.browser.models import (
    BrowserActionRequest,
    BrowserActionResult,
    BrowserDiagnosticRequest,
    BrowserDiagnosticResult,
    BrowserObservation,
    BrowserObservationRequest,
    BrowserSession,
    BrowserSessionRequest,
)
from mishkan.config.models import MishkanConfig
from mishkan.context.candidates import (
    CandidateAssessment,
    CandidateConstraints,
    CommunityCandidate,
    CommunityCandidateCatalogue,
    ContextualRecommendation,
    ContextualRecommendationRequest,
    RecommendationCriterion,
)
from mishkan.context.models import ContextPackManifest, ContextPackMaterialization
from mishkan.context.profile import ConfirmedEngineerFact, EngineerProfile
from mishkan.conversations.models import (
    ConversationChannel,
    ConversationMessage,
    DecisionExplanationPreference,
    MissionDecision,
    MissionEscalation,
    MissionIntervention,
)
from mishkan.crewai.mission_environment import MissionEnvironmentPlanningOutput
from mishkan.crewai.mission_governance import (
    CTOMissionReview,
    MissionGovernanceDisagreement,
    MissionGovernanceRequest,
    MissionGovernanceResult,
    PMMissionProposal,
)
from mishkan.domain.errors import ErrorEnvelope
from mishkan.domain.identity import DomainRecord
from mishkan.edits.git import GitEffectRequest, GitEffectResult
from mishkan.edits.models import ChangeSet, ChangeSetResult
from mishkan.environment.models import (
    DescriptorValidationResult,
    EnvironmentAttempt,
    EnvironmentBinding,
    EnvironmentBindingRequest,
    EnvironmentDescriptorChangePlan,
    EnvironmentDescriptorChangeRequest,
    EnvironmentDescriptorSet,
    EnvironmentInvalidation,
    EnvironmentObservation,
    EnvironmentObservationRequest,
    EnvironmentOperationPlan,
    EnvironmentOperationRequest,
    EnvironmentVerification,
    EnvironmentVerificationRequest,
)
from mishkan.environment.packs import (
    EngineeringCommandCandidate,
    EngineeringCommandPlan,
    EngineeringCommandRequest,
    TechnicalPackCatalogue,
)
from mishkan.events.models import (
    EventEnvelope,
    EventPage,
    EventRetentionPlan,
    EventRetentionPolicy,
)
from mishkan.events.models import (
    EventHold as EventEvidenceHold,
)
from mishkan.execution.sessions import CursorRead, ExecutionSession
from mishkan.knowledge.models import (
    KnowledgeBundle,
    KnowledgeCorpus,
    KnowledgeItem,
    KnowledgeMemoryProposal,
    KnowledgeOperation,
    KnowledgePromotion,
    KnowledgeQuery,
    KnowledgeQueryRecord,
    KnowledgeScope,
    KnowledgeSourceAttempt,
)
from mishkan.mcp.models import (
    McpCallRequest,
    McpCallResult,
    McpConnectionRecord,
    McpDiscoverySnapshot,
    McpPrimitiveDescriptor,
    McpProgressEvent,
)
from mishkan.missions.environment import (
    MissionEnvironmentPlan,
    MissionEnvironmentPlanAcceptance,
    MissionEnvironmentPlanningRequest,
)
from mishkan.missions.execution import (
    MissionCompletionReadiness,
    MissionTaskAcceptanceStatus,
    MissionTaskClaim,
    MissionTaskClaimRequest,
    MissionTaskEligibility,
)
from mishkan.missions.models import (
    ExecutiveConfirmation,
    MissionBrief,
    MissionCrewRevision,
    MissionEnvironmentIntent,
    MissionOrigin,
    MissionRecord,
    MissionResourceLimit,
    MissionRunBinding,
    MissionRunReport,
    MissionRunReportTask,
    MissionTaskAssignment,
    MissionTransition,
)
from mishkan.missions.readiness import MissionEnvironmentReadiness
from mishkan.missions.templates import MissionTemplateCatalogue, MissionTemplateDefinition
from mishkan.notifications import NotificationConfig, NotificationPage, NotificationRecord
from mishkan.organization.evolution import (
    ProfessionalCompetenceState,
    ProfessionalEvidenceRecord,
    ProfessionalPromotionDecision,
    ProfessionalPromotionRequest,
)
from mishkan.organization.models import OrganizationRosterDefinition
from mishkan.planning.models import (
    AcceptedPlan,
    InitializationResult,
    MissionTemplateReference,
    PlanCandidate,
    PlanExecutionContext,
    PlanOrganizationBinding,
)
from mishkan.repository.models import (
    DiscoverySnapshot,
    ProspectiveWorkspaceBinding,
    RepositoryBinding,
    RepositoryEstablishment,
)
from mishkan.runtime import TaskReviewRejection
from mishkan.skills.models import (
    SkillBundleDefinition,
    SkillBundleResolution,
    SkillCurationProposal,
    SkillInspectionProfile,
    SkillInspectionResult,
    SkillInvocationEvidence,
    SkillInvocationRequest,
    SkillLearningRecord,
    SkillLearningRequest,
    SkillLearningReview,
    SkillLearningSource,
    SkillLifecycleDecision,
    SkillLoadEvidence,
    SkillMetadata,
    SkillPackageDraft,
    SkillSelection,
    SkillSourceDefinition,
    SkillUpdateEvidence,
    SkillUpdateReport,
    SkillUsageRecord,
    SkillUsageSummary,
    SkillVersionRecord,
)
from mishkan.telemetry.models import (
    LangSmithFeedbackImportRequest,
    TelemetryEvaluationCandidate,
    TelemetryEvaluationImportResult,
    TelemetryExportEvidence,
    TelemetryRecord,
    TelemetryStatus,
)
from mishkan.tools.execution import ExecutionRequest, ExecutionResult
from mishkan.web.models import (
    CitationEvidence,
    CrawlRequest,
    CrawlResult,
    ExtractionRequest,
    ExtractionResult,
    FetchRequest,
    FetchResult,
    HttpRequest,
    HttpResult,
    MapRequest,
    MapResult,
    SearchRequest,
    SearchResponse,
)

SCHEMAS: dict[str, type[BaseModel]] = {
    "application-command-v1.schema.json": ApplicationCommand,
    "artifact-collection-v1.schema.json": ArtifactCollection,
    "artifact-hold-v1.schema.json": ArtifactEvidenceHold,
    "artifact-gc-plan-v1.schema.json": GarbageCollectionPlan,
    "artifact-manifest-v1.schema.json": ArtifactManifest,
    "artifact-pin-v1.schema.json": ArtifactPin,
    "artifact-reconciliation-plan-v1.schema.json": ArtifactReconciliationPlan,
    "artifact-upload-session-v1.schema.json": UploadSession,
    "artifact-working-reference-v1.schema.json": WorkingReference,
    "browser-action-request-v1.schema.json": BrowserActionRequest,
    "browser-action-result-v1.schema.json": BrowserActionResult,
    "browser-diagnostic-request-v1.schema.json": BrowserDiagnosticRequest,
    "browser-diagnostic-result-v1.schema.json": BrowserDiagnosticResult,
    "browser-observation-request-v1.schema.json": BrowserObservationRequest,
    "browser-observation-v1.schema.json": BrowserObservation,
    "browser-session-request-v1.schema.json": BrowserSessionRequest,
    "browser-session-v1.schema.json": BrowserSession,
    "change-set-result-v1.schema.json": ChangeSetResult,
    "change-set-v1.schema.json": ChangeSet,
    "command-result-v1.schema.json": CommandResult,
    "config-v1.schema.json": MishkanConfig,
    "prospective-run-request-v1.schema.json": ProspectiveRunRequest,
    "repository-establishment-request-v1.schema.json": RepositoryEstablishmentRequest,
    "confirmed-engineer-fact-v1.schema.json": ConfirmedEngineerFact,
    "candidate-assessment-v1.schema.json": CandidateAssessment,
    "candidate-constraints-v1.schema.json": CandidateConstraints,
    "community-candidate-v1.schema.json": CommunityCandidate,
    "community-candidate-catalogue-v1.schema.json": CommunityCandidateCatalogue,
    "context-pack-manifest-v1.schema.json": ContextPackManifest,
    "context-pack-materialization-v1.schema.json": ContextPackMaterialization,
    "contextual-recommendation-request-v1.schema.json": ContextualRecommendationRequest,
    "contextual-recommendation-v1.schema.json": ContextualRecommendation,
    "crewai-cto-mission-review-v1.schema.json": CTOMissionReview,
    "crewai-mission-governance-result-v1.schema.json": MissionGovernanceResult,
    "crewai-mission-governance-disagreement-v1.schema.json": MissionGovernanceDisagreement,
    "crewai-mission-governance-request-v1.schema.json": MissionGovernanceRequest,
    "crewai-pm-mission-proposal-v1.schema.json": PMMissionProposal,
    "crewai-mission-environment-output-v1.schema.json": MissionEnvironmentPlanningOutput,
    "conversation-channel-v1.schema.json": ConversationChannel,
    "conversation-message-v1.schema.json": ConversationMessage,
    "decision-explanation-preference-v1.schema.json": DecisionExplanationPreference,
    "domain-record-v1.schema.json": DomainRecord,
    "error-envelope-v1.schema.json": ErrorEnvelope,
    "engineering-command-candidate-v1.schema.json": EngineeringCommandCandidate,
    "engineering-command-plan-v1.schema.json": EngineeringCommandPlan,
    "engineering-command-request-v1.schema.json": EngineeringCommandRequest,
    "engineer-profile-v1.schema.json": EngineerProfile,
    "recommendation-criterion-v1.schema.json": RecommendationCriterion,
    "environment-attempt-v1.schema.json": EnvironmentAttempt,
    "environment-descriptor-validation-result-v1.schema.json": DescriptorValidationResult,
    "environment-binding-request-v1.schema.json": EnvironmentBindingRequest,
    "environment-binding-v1.schema.json": EnvironmentBinding,
    "environment-descriptor-change-plan-v1.schema.json": EnvironmentDescriptorChangePlan,
    "environment-descriptor-change-request-v1.schema.json": EnvironmentDescriptorChangeRequest,
    "environment-descriptor-set-v1.schema.json": EnvironmentDescriptorSet,
    "environment-invalidation-v1.schema.json": EnvironmentInvalidation,
    "environment-observation-request-v1.schema.json": EnvironmentObservationRequest,
    "environment-observation-v1.schema.json": EnvironmentObservation,
    "environment-operation-plan-v1.schema.json": EnvironmentOperationPlan,
    "environment-operation-request-v1.schema.json": EnvironmentOperationRequest,
    "environment-verification-v1.schema.json": EnvironmentVerification,
    "environment-verification-request-v1.schema.json": EnvironmentVerificationRequest,
    "event-envelope-v1.schema.json": EventEnvelope,
    "event-hold-v1.schema.json": EventEvidenceHold,
    "event-page-v1.schema.json": EventPage,
    "event-retention-plan-v1.schema.json": EventRetentionPlan,
    "event-retention-policy-v1.schema.json": EventRetentionPolicy,
    "execution-request-v1.schema.json": ExecutionRequest,
    "execution-result-v1.schema.json": ExecutionResult,
    "git-effect-request-v1.schema.json": GitEffectRequest,
    "git-effect-result-v1.schema.json": GitEffectResult,
    "knowledge-bundle-v1.schema.json": KnowledgeBundle,
    "knowledge-corpus-v1.schema.json": KnowledgeCorpus,
    "knowledge-item-v1.schema.json": KnowledgeItem,
    "knowledge-memory-proposal-v1.schema.json": KnowledgeMemoryProposal,
    "knowledge-operation-v1.schema.json": KnowledgeOperation,
    "knowledge-promotion-v1.schema.json": KnowledgePromotion,
    "knowledge-query-record-v1.schema.json": KnowledgeQueryRecord,
    "knowledge-query-v1.schema.json": KnowledgeQuery,
    "knowledge-scope-v1.schema.json": KnowledgeScope,
    "knowledge-source-attempt-v1.schema.json": KnowledgeSourceAttempt,
    "mcp-call-request-v1.schema.json": McpCallRequest,
    "mcp-call-result-v1.schema.json": McpCallResult,
    "mcp-connection-v1.schema.json": McpConnectionRecord,
    "mcp-discovery-v1.schema.json": McpDiscoverySnapshot,
    "mcp-primitive-v1.schema.json": McpPrimitiveDescriptor,
    "mcp-progress-v1.schema.json": McpProgressEvent,
    "mission-brief-v1.schema.json": MissionBrief,
    "mission-completion-readiness-v1.schema.json": MissionCompletionReadiness,
    "mission-crew-revision-v1.schema.json": MissionCrewRevision,
    "mission-environment-intent-v1.schema.json": MissionEnvironmentIntent,
    "mission-environment-plan-v1.schema.json": MissionEnvironmentPlan,
    "mission-environment-plan-acceptance-v1.schema.json": MissionEnvironmentPlanAcceptance,
    "mission-environment-planning-request-v1.schema.json": MissionEnvironmentPlanningRequest,
    "mission-environment-readiness-v1.schema.json": MissionEnvironmentReadiness,
    "mission-executive-confirmation-v1.schema.json": ExecutiveConfirmation,
    "mission-origin-v1.schema.json": MissionOrigin,
    "mission-decision-v1.schema.json": MissionDecision,
    "mission-escalation-v1.schema.json": MissionEscalation,
    "mission-intervention-v1.schema.json": MissionIntervention,
    "mission-record-v1.schema.json": MissionRecord,
    "mission-resource-limit-v1.schema.json": MissionResourceLimit,
    "mission-run-binding-v1.schema.json": MissionRunBinding,
    "mission-run-report-v1.schema.json": MissionRunReport,
    "mission-run-report-task-v1.schema.json": MissionRunReportTask,
    "mission-task-assignment-v1.schema.json": MissionTaskAssignment,
    "mission-task-acceptance-status-v1.schema.json": MissionTaskAcceptanceStatus,
    "mission-task-claim-request-v1.schema.json": MissionTaskClaimRequest,
    "mission-task-claim-v1.schema.json": MissionTaskClaim,
    "mission-task-eligibility-v1.schema.json": MissionTaskEligibility,
    "mission-template-catalogue-v1.schema.json": MissionTemplateCatalogue,
    "mission-template-definition-v1.schema.json": MissionTemplateDefinition,
    "mission-template-reference-v1.schema.json": MissionTemplateReference,
    "plan-accepted-v1.schema.json": AcceptedPlan,
    "plan-candidate-v1.schema.json": PlanCandidate,
    "plan-execution-context-v1.schema.json": PlanExecutionContext,
    "plan-organization-binding-v1.schema.json": PlanOrganizationBinding,
    "planning-result-v1.schema.json": InitializationResult,
    "project-discovery-v1.schema.json": DiscoverySnapshot,
    "prospective-workspace-binding-v1.schema.json": ProspectiveWorkspaceBinding,
    "repository-binding-v1.schema.json": RepositoryBinding,
    "repository-establishment-v1.schema.json": RepositoryEstablishment,
    "mission-transition-v1.schema.json": MissionTransition,
    "notification-config-v1.schema.json": NotificationConfig,
    "notification-page-v1.schema.json": NotificationPage,
    "notification-record-v1.schema.json": NotificationRecord,
    "organization-roster-v1.schema.json": OrganizationRosterDefinition,
    "professional-competence-state-v1.schema.json": ProfessionalCompetenceState,
    "professional-evidence-record-v1.schema.json": ProfessionalEvidenceRecord,
    "professional-promotion-decision-v1.schema.json": ProfessionalPromotionDecision,
    "professional-promotion-request-v1.schema.json": ProfessionalPromotionRequest,
    "langsmith-feedback-import-request-v1.schema.json": LangSmithFeedbackImportRequest,
    "run-initialization-request-v1.schema.json": RunInitializationRequest,
    "skill-bundle-definition-v1.schema.json": SkillBundleDefinition,
    "skill-bundle-resolution-v1.schema.json": SkillBundleResolution,
    "skill-curation-proposal-v1.schema.json": SkillCurationProposal,
    "skill-inspection-profile-v1.schema.json": SkillInspectionProfile,
    "skill-inspection-result-v1.schema.json": SkillInspectionResult,
    "skill-invocation-evidence-v1.schema.json": SkillInvocationEvidence,
    "skill-invocation-request-v1.schema.json": SkillInvocationRequest,
    "skill-learning-record-v1.schema.json": SkillLearningRecord,
    "skill-learning-request-v1.schema.json": SkillLearningRequest,
    "skill-learning-review-v1.schema.json": SkillLearningReview,
    "skill-learning-source-v1.schema.json": SkillLearningSource,
    "skill-lifecycle-decision-v1.schema.json": SkillLifecycleDecision,
    "skill-load-evidence-v1.schema.json": SkillLoadEvidence,
    "skill-metadata-v1.schema.json": SkillMetadata,
    "skill-package-draft-v1.schema.json": SkillPackageDraft,
    "skill-selection-v1.schema.json": SkillSelection,
    "skill-source-v1.schema.json": SkillSourceDefinition,
    "skill-usage-record-v1.schema.json": SkillUsageRecord,
    "skill-usage-summary-v1.schema.json": SkillUsageSummary,
    "skill-update-evidence-v1.schema.json": SkillUpdateEvidence,
    "skill-update-report-v1.schema.json": SkillUpdateReport,
    "skill-version-record-v1.schema.json": SkillVersionRecord,
    "web-citation-evidence-v1.schema.json": CitationEvidence,
    "web-crawl-request-v1.schema.json": CrawlRequest,
    "web-crawl-result-v1.schema.json": CrawlResult,
    "web-extraction-request-v1.schema.json": ExtractionRequest,
    "web-extraction-result-v1.schema.json": ExtractionResult,
    "web-fetch-request-v1.schema.json": FetchRequest,
    "web-fetch-result-v1.schema.json": FetchResult,
    "web-http-request-v1.schema.json": HttpRequest,
    "web-http-result-v1.schema.json": HttpResult,
    "web-map-request-v1.schema.json": MapRequest,
    "web-map-result-v1.schema.json": MapResult,
    "web-search-request-v1.schema.json": SearchRequest,
    "web-search-response-v1.schema.json": SearchResponse,
    "execution-cursor-read-v1.schema.json": CursorRead,
    "execution-session-v1.schema.json": ExecutionSession,
    "snapshot-envelope-v1.schema.json": SnapshotEnvelope,
    "task-review-rejection-v1.schema.json": TaskReviewRejection,
    "telemetry-export-evidence-v1.schema.json": TelemetryExportEvidence,
    "telemetry-evaluation-candidate-v1.schema.json": TelemetryEvaluationCandidate,
    "telemetry-evaluation-import-result-v1.schema.json": TelemetryEvaluationImportResult,
    "telemetry-record-v1.schema.json": TelemetryRecord,
    "telemetry-status-v1.schema.json": TelemetryStatus,
    "technical-pack-catalogue-v1.schema.json": TechnicalPackCatalogue,
}

_EXPORT_MANIFEST = ".mishkan-schema-export.json"


def export_schemas(output: Path) -> tuple[Path, ...]:
    target = output.expanduser().resolve()
    target.mkdir(parents=True, exist_ok=True)
    manifest_path = target / _EXPORT_MANIFEST
    if manifest_path.is_file():
        try:
            previous = json.loads(manifest_path.read_text(encoding="utf-8"))
            previous_files = previous["files"]
        except (OSError, KeyError, TypeError, json.JSONDecodeError):
            previous_files = []
        if isinstance(previous_files, list):
            for filename in previous_files:
                if (
                    isinstance(filename, str)
                    and Path(filename).name == filename
                    and filename.endswith(".schema.json")
                    and filename not in SCHEMAS
                ):
                    (target / filename).unlink(missing_ok=True)
    written: list[Path] = []
    for filename, model in SCHEMAS.items():
        path = target / filename
        content = json.dumps(model.model_json_schema(), indent=2, sort_keys=True) + "\n"
        path.write_text(content, encoding="utf-8")
        written.append(path)
    manifest_path.write_text(
        json.dumps(
            {"schema_version": "1.0", "files": sorted(SCHEMAS)},
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return tuple(written)
