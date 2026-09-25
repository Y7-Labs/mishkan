"""Strict public configuration contracts."""

import ipaddress
from enum import StrEnum
from pathlib import Path
from typing import Self

from pydantic import (
    AnyHttpUrl,
    AnyUrl,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from mishkan.domain.time import validate_timezone
from mishkan.knowledge.models import KnowledgeClass
from mishkan.notifications import NotificationConfig
from mishkan.skills.models import SkillBounds, SkillBundleDefinition, SkillSourceDefinition
from mishkan.telemetry.models import TelemetryDisclosure, TelemetryExporterKind


class StrictConfigModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class OperatingMode(StrEnum):
    LOCAL = "local"
    CLOUD = "cloud"
    HYBRID = "hybrid"
    DISTRIBUTED = "distributed"


class CredentialSource(StrEnum):
    ENV = "env"
    FILE = "file"
    KEYRING = "keyring"
    COMMAND = "command"


class CredentialReference(StrictConfigModel):
    """A late-resolution locator; never a credential value."""

    source: CredentialSource
    locator: str = Field(min_length=1)


class ProviderConfig(StrictConfigModel):
    kind: str = Field(min_length=1)
    endpoint: AnyUrl
    credential_pool: tuple[CredentialReference, ...] = ()
    probe_url: AnyUrl | None = None


class ServiceConfig(StrictConfigModel):
    kind: str = Field(min_length=1)
    endpoint: AnyUrl
    credential_refs: tuple[CredentialReference, ...] = ()
    required: bool = False
    probe_url: AnyUrl | None = None


class ModelCandidate(StrictConfigModel):
    provider: str = Field(min_length=1)
    model: str = Field(min_length=1)


class ModelRoute(StrictConfigModel):
    candidates: tuple[ModelCandidate, ...] = Field(min_length=1)


class ProjectConfig(StrictConfigModel):
    workspace: Path


class CrewAIRuntimeConfig(StrictConfigModel):
    tracing: bool = False
    telemetry: bool = False
    temperature: float = Field(default=0.0, ge=0.0, le=2.0)
    model_timeout_seconds: int = Field(default=120, ge=10, le=3_600)
    model_transport_retries: int = Field(default=0, ge=0, le=10)
    model_max_output_tokens: int = Field(default=2_048, ge=128, le=131_072)
    max_agent_iterations: int = Field(default=4, ge=1, le=100)
    task_execution_retries: int = Field(default=2, ge=0, le=10)
    plan_validation_retries: int = Field(default=2, ge=0, le=10)
    review_retries: int = Field(default=2, ge=0, le=10)
    structured_output_retries: int = Field(default=2, ge=0, le=10)
    mission_pm_model_route: str = Field(default="planning", min_length=1)
    mission_cto_model_route: str = Field(default="planning", min_length=1)
    mission_environment_model_route: str = Field(default="planning", min_length=1)


class DaemonConfig(StrictConfigModel):
    host: str
    port: int = Field(ge=1, le=65_535)
    token_file: Path
    heartbeat_seconds: int = Field(ge=1, le=300)
    event_poll_seconds: float = Field(ge=0.05, le=30)
    event_page_limit: int = Field(ge=1, le=1_000)
    request_timeout_seconds: int = Field(ge=1, le=3_600)
    max_request_bytes: int = Field(default=8_388_608, ge=1, le=1_073_741_824)

    @field_validator("host")
    @classmethod
    def host_is_loopback_for_i03(cls, value: str) -> str:
        try:
            address = ipaddress.ip_address(value)
        except ValueError as exc:
            raise ValueError("daemon host must be a literal loopback address") from exc
        if not address.is_loopback:
            raise ValueError("daemon host must be loopback-only")
        return value

    @field_validator("token_file")
    @classmethod
    def token_file_is_project_relative(cls, value: Path) -> Path:
        if value.is_absolute() or not value.parts:
            raise ValueError("daemon token file must be project-relative")
        return value


class PersistenceConfig(StrictConfigModel):
    database: Path
    busy_timeout_ms: int = Field(ge=1, le=300_000)
    event_retention_days: int = Field(ge=1, le=36_500)

    @field_validator("database")
    @classmethod
    def database_is_project_relative(cls, value: Path) -> Path:
        if value.is_absolute() or not value.parts:
            raise ValueError("metadata database must be project-relative")
        return value


class ArtifactConfig(StrictConfigModel):
    root: Path
    max_artifact_bytes: int = Field(ge=1)
    chunk_bytes: int = Field(ge=1)
    staging_ttl_seconds: int = Field(ge=1)

    @field_validator("root")
    @classmethod
    def root_is_project_relative(cls, value: Path) -> Path:
        if value.is_absolute() or not value.parts:
            raise ValueError("artifact root must be project-relative")
        return value


class SessionEffectObservationConfig(StrictConfigModel):
    max_entries: int = Field(ge=1)
    max_file_bytes: int = Field(ge=1)
    max_total_bytes: int = Field(ge=1)
    exclude: tuple[str, ...] = ()


class SessionProfileConfig(StrictConfigModel):
    cancellation_signals: tuple[str, ...] = Field(min_length=1)
    grace_seconds: float = Field(ge=0.05, le=300)
    settle_timeout_seconds: float = Field(ge=0.1, le=3600)
    max_output_bytes: int = Field(ge=1)
    max_input_bytes: int = Field(default=1_048_576, ge=1)
    preview_bytes: int = Field(default=4_096, ge=1)
    read_chunk_bytes: int = Field(ge=1, le=16_777_216)
    readiness_poll_seconds: float = Field(ge=0.01, le=60)
    max_memory_mb: int | None = Field(default=None, ge=1)
    max_cpu_seconds: float | None = Field(default=None, gt=0, le=604_800)
    effect_observation: SessionEffectObservationConfig


class SessionConfig(StrictConfigModel):
    spool_root: Path
    default_profile: str = Field(min_length=1)
    profiles: dict[str, SessionProfileConfig] = Field(min_length=1)

    @field_validator("spool_root")
    @classmethod
    def spool_root_is_project_relative(cls, value: Path) -> Path:
        if value.is_absolute() or not value.parts:
            raise ValueError("session spool root must be project-relative")
        return value

    @model_validator(mode="after")
    def default_profile_exists(self) -> Self:
        if self.default_profile not in self.profiles:
            raise ValueError("default session profile does not exist")
        return self


class WebComponentRole(StrEnum):
    DIRECT_SOURCE = "direct_source"
    BROKER = "broker"
    COMPOSITE_GATEWAY = "composite_gateway"
    TRANSPORT = "transport"
    EXTRACTOR = "extractor"
    CRAWLER = "crawler"


class SearchStrategy(StrEnum):
    DIRECT = "direct"
    AGGREGATE = "aggregate"
    VERIFICATION = "verification"
    AUTOMATIC = "automatic"


class NetworkProfileConfig(StrictConfigModel):
    allowed_schemes: tuple[str, ...] = Field(min_length=1)
    allowed_ports: tuple[int, ...] = Field(min_length=1)
    allow_public: bool
    allow_private: bool
    allow_loopback: bool
    allow_link_local: bool
    allow_multicast: bool
    max_redirects: int = Field(ge=0, le=100)
    connect_timeout_seconds: float = Field(gt=0, le=3_600)
    read_timeout_seconds: float = Field(gt=0, le=3_600)
    max_response_bytes: int = Field(ge=1)
    max_decompressed_bytes: int = Field(ge=1)
    max_concurrency: int = Field(ge=1, le=10_000)
    credential_header_names: tuple[str, ...] = Field(min_length=1)

    @field_validator("allowed_schemes")
    @classmethod
    def schemes_are_explicit(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(item.casefold() for item in value)
        if len(normalized) != len(set(normalized)) or any(
            not item or not item.isascii() or not item.isalpha() for item in normalized
        ):
            raise ValueError("network schemes must be unique ASCII names")
        return normalized

    @field_validator("allowed_ports")
    @classmethod
    def ports_are_explicit(cls, value: tuple[int, ...]) -> tuple[int, ...]:
        if len(value) != len(set(value)) or any(port < 1 or port > 65_535 for port in value):
            raise ValueError("network ports must be unique values in the TCP port range")
        return value

    @field_validator("credential_header_names")
    @classmethod
    def credential_headers_are_explicit(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(item.casefold() for item in value)
        if len(normalized) != len(set(normalized)) or any(not item for item in normalized):
            raise ValueError("credential header names must be non-empty and unique")
        return normalized

    @model_validator(mode="after")
    def decompression_bound_covers_wire_bound(self) -> Self:
        if self.max_decompressed_bytes < self.max_response_bytes:
            raise ValueError("decompressed response bound cannot be below the wire response bound")
        return self


class WebSourceConfig(StrictConfigModel):
    role: WebComponentRole
    adapter: str = Field(min_length=1)
    endpoint: AnyHttpUrl
    network_profile: str = Field(min_length=1)
    credential_refs: tuple[CredentialReference, ...] = ()
    reported_upstreams: tuple[str, ...] | None = None
    enabled: bool = True
    max_results: int = Field(ge=1, le=1_000)

    @model_validator(mode="after")
    def source_role_is_search_capable(self) -> Self:
        if self.role not in {
            WebComponentRole.DIRECT_SOURCE,
            WebComponentRole.BROKER,
            WebComponentRole.COMPOSITE_GATEWAY,
        }:
            raise ValueError("web search source must declare a search-capable role")
        return self


class WebExtractorConfig(StrictConfigModel):
    role: WebComponentRole
    adapter: str = Field(min_length=1)
    enabled: bool = True
    max_input_bytes: int = Field(ge=1)
    output_format: str = Field(min_length=1)

    @model_validator(mode="after")
    def role_is_extractor(self) -> Self:
        if self.role is not WebComponentRole.EXTRACTOR:
            raise ValueError("web extractor must declare the extractor role")
        return self


class WebCrawlerConfig(StrictConfigModel):
    role: WebComponentRole
    adapter: str = Field(min_length=1)
    network_profile: str = Field(min_length=1)
    enabled: bool = True
    max_depth: int = Field(ge=0, le=100)
    max_pages: int = Field(ge=1, le=1_000_000)
    max_concurrency: int = Field(ge=1, le=10_000)
    delay_seconds: float = Field(ge=0, le=3_600)
    robots_profile: str = Field(min_length=1)
    render_mode: str = Field(min_length=1)

    @model_validator(mode="after")
    def role_is_crawler(self) -> Self:
        if self.role is not WebComponentRole.CRAWLER:
            raise ValueError("web crawler must declare the crawler role")
        return self


class WebConfig(StrictConfigModel):
    network_profiles: dict[str, NetworkProfileConfig] = Field(min_length=1)
    sources: dict[str, WebSourceConfig] = Field(min_length=1)
    extractors: dict[str, WebExtractorConfig] = Field(min_length=1)
    crawlers: dict[str, WebCrawlerConfig] = Field(min_length=1)
    default_network_profile: str = Field(min_length=1)
    default_search_strategy: SearchStrategy
    default_search_sources: tuple[str, ...] = Field(min_length=1)
    default_extractor: str = Field(min_length=1)
    default_crawler: str = Field(min_length=1)
    cache_ttl_seconds: int = Field(ge=0, le=31_536_000)

    @model_validator(mode="after")
    def references_exist(self) -> Self:
        missing_profiles = sorted(
            {
                self.default_network_profile,
                *(source.network_profile for source in self.sources.values()),
                *(crawler.network_profile for crawler in self.crawlers.values()),
            }
            - set(self.network_profiles)
        )
        missing_sources = sorted(set(self.default_search_sources) - set(self.sources))
        if missing_profiles or missing_sources:
            raise ValueError(
                f"web configuration references unknown profiles/sources: "
                f"profiles={missing_profiles}, sources={missing_sources}"
            )
        if self.default_extractor not in self.extractors:
            raise ValueError("default web extractor does not exist")
        if self.default_crawler not in self.crawlers:
            raise ValueError("default web crawler does not exist")
        return self


class BrowserProfileKind(StrEnum):
    ISOLATED = "isolated"
    PROJECT_PERSISTENT = "project_persistent"
    ATTACHED_EXISTING = "attached_existing"


class BrowserProfileConfig(StrictConfigModel):
    kind: BrowserProfileKind
    adapter: str = Field(min_length=1)
    engine: str = Field(min_length=1)
    network_profile: str = Field(min_length=1)
    allowed_origins: tuple[str, ...] = Field(min_length=1)
    sensitivity: str = Field(min_length=1)
    retention: str = Field(min_length=1)
    headless: bool
    max_pages: int = Field(ge=1, le=1_000)
    max_download_bytes: int = Field(ge=1)
    max_upload_bytes: int = Field(default=16_777_216, ge=1)
    action_timeout_seconds: float = Field(gt=0, le=3_600)
    navigation_timeout_seconds: float = Field(gt=0, le=3_600)
    user_data_dir: Path | None = None
    cdp_endpoint: AnyHttpUrl | None = None

    @model_validator(mode="after")
    def profile_inputs_match_kind(self) -> Self:
        if self.kind is BrowserProfileKind.PROJECT_PERSISTENT:
            if (
                self.user_data_dir is None
                or self.user_data_dir.is_absolute()
                or ".." in self.user_data_dir.parts
            ):
                raise ValueError("persistent browser profile requires a project-relative directory")
        elif self.user_data_dir is not None:
            raise ValueError("only persistent browser profiles may declare a user-data directory")
        if self.kind is BrowserProfileKind.ATTACHED_EXISTING:
            if self.cdp_endpoint is None:
                raise ValueError("attached browser profile requires an explicit CDP endpoint")
        elif self.cdp_endpoint is not None:
            raise ValueError("only attached browser profiles may declare a CDP endpoint")
        return self


class BrowserConfig(StrictConfigModel):
    staging_root: Path
    default_profile: str = Field(min_length=1)
    profiles: dict[str, BrowserProfileConfig] = Field(min_length=1)
    observation_ttl_seconds: float = Field(gt=0, le=3_600)
    max_observation_bytes: int = Field(ge=1)
    max_diagnostic_entries: int = Field(ge=1, le=1_000_000)
    max_pending_downloads: int = Field(default=100, ge=1, le=10_000)
    max_profile_state_bytes: int = Field(default=67_108_864, ge=1, le=1_073_741_824)

    @field_validator("staging_root")
    @classmethod
    def staging_root_is_project_relative(cls, value: Path) -> Path:
        if value.is_absolute() or not value.parts or ".." in value.parts:
            raise ValueError("browser staging root must be project-relative")
        return value

    @model_validator(mode="after")
    def default_profile_exists(self) -> Self:
        if self.default_profile not in self.profiles:
            raise ValueError("default browser profile does not exist")
        for profile in self.profiles.values():
            if (
                profile.kind is BrowserProfileKind.PROJECT_PERSISTENT
                and profile.user_data_dir is not None
                and not profile.user_data_dir.is_relative_to(self.staging_root)
            ):
                raise ValueError(
                    "persistent browser state must live below the managed Browser staging root"
                )
        return self


class McpTransport(StrEnum):
    STDIO = "stdio"
    STREAMABLE_HTTP = "streamable_http"


class McpProtocolStrategy(StrEnum):
    PINNED = "pinned"
    COMPATIBLE_SET = "compatible_set"
    ISOLATED_LEGACY = "isolated_legacy"


class McpConnectionConfig(StrictConfigModel):
    transport: McpTransport
    protocol_strategy: McpProtocolStrategy
    protocol_versions: tuple[str, ...] = Field(min_length=1)
    trust: str = Field(min_length=1)
    exposure_profile: str = Field(min_length=1)
    credential_refs: tuple[CredentialReference, ...] = ()
    network_profile: str | None = None
    endpoint: AnyHttpUrl | None = None
    command: str | None = None
    isolation_profile: str | None = None
    arguments: tuple[str, ...] = ()
    inherit_environment: tuple[str, ...] = ()
    environment: dict[str, CredentialReference] = Field(default_factory=dict)
    headers: dict[str, CredentialReference] = Field(default_factory=dict)
    enabled: bool = True
    remote_tasks_enabled: bool = False
    connect_timeout_seconds: float = Field(gt=0, le=3_600)
    call_timeout_seconds: float = Field(gt=0, le=86_400)
    max_result_bytes: int = Field(ge=1)
    discovery_timeout_seconds: float = Field(default=30, gt=0, le=3_600)
    max_discovery_pages: int = Field(default=100, ge=1, le=10_000)
    max_discovered_primitives: int = Field(default=10_000, ge=1, le=100_000)
    max_discovery_bytes: int = Field(default=16_777_216, ge=1, le=268_435_456)

    @model_validator(mode="after")
    def transport_inputs_are_disjoint(self) -> Self:
        if self.transport is McpTransport.STDIO:
            if (
                not self.command
                or not self.isolation_profile
                or self.endpoint is not None
                or self.network_profile is not None
                or self.headers
            ):
                raise ValueError(
                    "STDIO MCP connection requires an explicit command and isolation profile"
                )
        elif (
            self.endpoint is None
            or not self.network_profile
            or self.command is not None
            or self.isolation_profile is not None
            or self.environment
        ):
            raise ValueError("Streamable HTTP MCP connection requires endpoint and network profile")
        if self.transport is McpTransport.STREAMABLE_HTTP and self.inherit_environment:
            raise ValueError("Streamable HTTP MCP connections do not inherit process environment")
        if len(self.protocol_versions) != len(set(self.protocol_versions)):
            raise ValueError("MCP protocol versions must be unique")
        declared = {item.locator for item in self.credential_refs}
        mapped = {item.locator for item in (*self.environment.values(), *self.headers.values())}
        if not mapped.issubset(declared):
            raise ValueError("MCP credential mappings must reference declared credentials")
        return self


SUPPORTED_MCP_FACADE_OPERATIONS = frozenset(
    {
        "system.health",
        "system.snapshot",
        "events.list",
        "run.get",
        "organization.get",
        "organization.inspect",
        "organization.branch.inspect",
        "organization.competence.get",
        "organization.evidence.list",
        "organization.promotions.list",
        "mission.list",
        "mission.get",
        "mission.inspect",
        "mission.run-reports.list",
        "mission.templates.list",
        "conversation.list",
        "conversation.get",
        "advisory.candidates.list",
        "notification.list",
        "knowledge.query",
        "knowledge.sources.list",
        "knowledge.operations.list",
        "command.submit",
    }
)
SUPPORTED_MCP_FACADE_RESOURCES = frozenset(
    {
        "mishkan://snapshot",
        "mishkan://runs",
        "mishkan://events",
        "mishkan://organization",
        "mishkan://missions",
        "mishkan://conversations",
        "mishkan://advisory/candidates",
        "mishkan://notifications",
        "mishkan://knowledge/sources",
        "mishkan://knowledge/operations",
    }
)


class McpExposureProfileConfig(StrictConfigModel):
    operations: tuple[str, ...] = Field(min_length=1)
    resources: tuple[str, ...] = ()
    prompts: tuple[str, ...] = ()


class McpFacadeConfig(StrictConfigModel):
    enabled: bool
    streamable_http_path: str = Field(pattern=r"^/[A-Za-z0-9._~!$&'()*+,;=:@%/-]+$")
    stdio_bridge_enabled: bool
    exposure_profile: str = Field(min_length=1)


class McpConfig(StrictConfigModel):
    connections: dict[str, McpConnectionConfig] = Field(default_factory=dict)
    exposure_profiles: dict[str, McpExposureProfileConfig] = Field(min_length=1)
    facade: McpFacadeConfig
    progress_retention_seconds: int = Field(ge=1, le=31_536_000)
    progress_page_limit: int = Field(default=500, ge=1, le=10_000)
    max_progress_events_per_call: int = Field(default=10_000, ge=1, le=1_000_000)
    max_progress_message_bytes: int = Field(default=16_384, ge=1, le=1_048_576)
    progress_prune_batch_size: int = Field(default=1_000, ge=1, le=100_000)
    cancellation_poll_seconds: float = Field(gt=0, le=60)
    task_poll_min_seconds: float = Field(default=0.1, gt=0, le=60)
    task_poll_max_seconds: float = Field(default=5.0, gt=0, le=3_600)

    @model_validator(mode="after")
    def references_exist(self) -> Self:
        missing_exposures = sorted(
            {
                self.facade.exposure_profile,
                *(connection.exposure_profile for connection in self.connections.values()),
            }
            - set(self.exposure_profiles)
        )
        if missing_exposures:
            raise ValueError(
                f"MCP connections reference unknown exposure profiles: {missing_exposures}"
            )
        facade = self.exposure_profiles[self.facade.exposure_profile]
        if (self.facade.enabled or self.facade.stdio_bridge_enabled) and (
            set(facade.operations) - SUPPORTED_MCP_FACADE_OPERATIONS
            or set(facade.resources) - SUPPORTED_MCP_FACADE_RESOURCES
            or facade.prompts
        ):
            raise ValueError(
                "active MCP facade profile contains a primitive without an executable adapter"
            )
        if self.task_poll_min_seconds > self.task_poll_max_seconds:
            raise ValueError("MCP task polling minimum exceeds its maximum")
        return self


class SkillsConfig(StrictConfigModel):
    managed_root: Path
    sources: tuple[SkillSourceDefinition, ...] = Field(min_length=1)
    bounds: SkillBounds
    bundles: tuple[SkillBundleDefinition, ...] = ()
    inspection_profile: str = Field(min_length=1, max_length=4_096)
    automatic_selection: bool
    max_automatic_skills: int = Field(ge=1, le=1_000)
    miss_proposal_threshold: int = Field(ge=1, le=1_000_000)
    stale_after_days: int = Field(ge=1, le=36_500)
    learning_max_source_bytes: int = Field(ge=1, le=1_073_741_824)

    @field_validator("managed_root")
    @classmethod
    def managed_root_is_project_relative(cls, value: Path) -> Path:
        if value.is_absolute() or not value.parts or ".." in value.parts:
            raise ValueError("managed skill root must be project-relative")
        return value

    @model_validator(mode="after")
    def identities_are_unique(self) -> Self:
        source_ids = [source.source_id for source in self.sources]
        bundle_ids = [bundle.bundle_id for bundle in self.bundles]
        if len(source_ids) != len(set(source_ids)):
            raise ValueError("skill source identities must be unique")
        if len(bundle_ids) != len(set(bundle_ids)):
            raise ValueError("skill bundle identities must be unique")
        return self


class TelemetryExporterConfig(StrictConfigModel):
    kind: TelemetryExporterKind
    endpoint: AnyHttpUrl
    network_profile: str = Field(min_length=1)
    credential_ref: CredentialReference | None = None
    credential_header: str = Field(default="authorization", min_length=1, max_length=128)
    credential_prefix: str = Field(default="Bearer ", max_length=64)
    project: str | None = Field(default=None, min_length=1, max_length=256)
    service_name: str = Field(default="mishkand", min_length=1, max_length=256)
    timeout_seconds: float = Field(default=5.0, gt=0, le=300)

    @field_validator("credential_header")
    @classmethod
    def credential_header_is_safe(cls, value: str) -> str:
        normalized = value.casefold()
        forbidden = {
            "connection",
            "content-length",
            "host",
            "keep-alive",
            "proxy-authorization",
            "te",
            "trailer",
            "transfer-encoding",
            "upgrade",
        }
        if not value.replace("-", "").isalnum() or normalized in forbidden:
            raise ValueError("telemetry credential header is invalid or transport-controlled")
        return value

    @model_validator(mode="after")
    def exporter_contract_matches_kind(self) -> Self:
        if self.kind is TelemetryExporterKind.LANGSMITH_OTLP:
            if self.credential_ref is None or self.project is None:
                raise ValueError("LangSmith OTLP requires a credential reference and project")
            if self.credential_header.casefold() != "x-api-key":
                raise ValueError("LangSmith OTLP credential header must be x-api-key")
            if self.credential_prefix:
                raise ValueError("LangSmith OTLP credential prefix must be empty")
        return self


class TelemetryConfig(StrictConfigModel):
    disclosure: TelemetryDisclosure = TelemetryDisclosure.OFF
    exporter: TelemetryExporterConfig | None = None
    metadata_attributes: tuple[str, ...] = Field(default_factory=tuple, max_length=64)
    include_command_payload: bool = False
    include_result_payload: bool = False
    max_attributes: int = Field(default=64, ge=1, le=1_000)
    max_attribute_bytes: int = Field(default=4_096, ge=1, le=1_048_576)

    @model_validator(mode="after")
    def disclosure_has_exact_exporter(self) -> Self:
        if self.disclosure is TelemetryDisclosure.OFF and self.exporter is not None:
            raise ValueError("off telemetry cannot configure an exporter")
        if self.disclosure is not TelemetryDisclosure.OFF and self.exporter is None:
            raise ValueError("enabled telemetry disclosure requires an exporter")
        if len(self.metadata_attributes) != len(set(self.metadata_attributes)):
            raise ValueError("telemetry metadata attributes must be unique")
        return self


class KnowledgeCostClass(StrEnum):
    LOCAL_FREE = "local_free"
    EXTERNAL_FREE = "external_free"
    METERED = "metered"


class KnowledgeSourceConfig(StrictConfigModel):
    knowledge_class: KnowledgeClass
    adapter: str = Field(pattern=r"^[a-z][a-z0-9_.-]{2,127}$")
    endpoint: AnyHttpUrl | None = None
    mcp_connection: str | None = Field(default=None, min_length=1, max_length=256)
    credential_refs: tuple[CredentialReference, ...] = ()
    credential_header: str | None = Field(default=None, min_length=1, max_length=128)
    credential_prefix: str = Field(default="", max_length=64)
    network_profile: str | None = Field(default=None, min_length=1, max_length=256)
    disclosure_profile: str = Field(min_length=1, max_length=256)
    cost_class: KnowledgeCostClass
    enabled: bool = True
    query_timeout_seconds: float = Field(gt=0, le=3_600)
    operation_timeout_seconds: float = Field(gt=0, le=86_400)
    max_results: int = Field(ge=1, le=10_000)
    max_result_bytes: int = Field(ge=1, le=268_435_456)
    provider_schema: str = Field(min_length=1, max_length=128)

    @field_validator("credential_header")
    @classmethod
    def credential_header_is_transport_safe(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.casefold()
        forbidden = {
            "connection",
            "content-length",
            "host",
            "keep-alive",
            "proxy-authorization",
            "te",
            "trailer",
            "transfer-encoding",
            "upgrade",
        }
        if not value.replace("-", "").isalnum() or normalized in forbidden:
            raise ValueError("knowledge credential header is invalid or transport-controlled")
        return normalized

    @model_validator(mode="after")
    def adapter_has_exact_transport(self) -> Self:
        if self.adapter == "native.literal":
            if self.endpoint is not None or self.mcp_connection is not None:
                raise ValueError("literal knowledge cannot configure an external transport")
            if self.credential_refs or self.credential_header or self.network_profile:
                raise ValueError(
                    "literal knowledge cannot configure external credentials or network"
                )
        elif self.adapter == "graphify.mcp":
            if self.mcp_connection is None or self.endpoint is not None:
                raise ValueError("Graphify knowledge requires exactly one MCP connection")
            if self.knowledge_class is not KnowledgeClass.STRUCTURAL:
                raise ValueError("Graphify adapter is structural knowledge only")
        elif (
            self.endpoint is None or self.mcp_connection is not None or self.network_profile is None
        ):
            raise ValueError("HTTP knowledge adapters require endpoint and network profile")
        if bool(self.credential_refs) != bool(self.credential_header):
            raise ValueError("knowledge credentials require an explicit header mapping")
        return self


class KnowledgeConfig(StrictConfigModel):
    staging_root: Path
    inspection_profile: str = Field(min_length=1, max_length=1_024)
    sources: dict[str, KnowledgeSourceConfig] = Field(min_length=1)
    selection_order: dict[KnowledgeClass, tuple[str, ...]] = Field(min_length=1)
    literal_fallback: bool
    promotion_approver_identities: tuple[str, ...] = Field(min_length=1)
    capture_max_characters: int = Field(ge=1, le=1_048_576)
    operation_poll_seconds: float = Field(gt=0, le=60)
    query_retention_days: int = Field(ge=1, le=36_500)

    @field_validator("staging_root")
    @classmethod
    def staging_root_is_project_relative(cls, value: Path) -> Path:
        if value.is_absolute() or not value.parts or ".." in value.parts:
            raise ValueError("knowledge staging root must be project-relative")
        return value

    @model_validator(mode="after")
    def source_selection_is_complete_and_compatible(self) -> Self:
        if len(self.promotion_approver_identities) != len(set(self.promotion_approver_identities)):
            raise ValueError("knowledge promotion approver identities must be unique")
        selected: set[str] = set()
        for knowledge_class, source_ids in self.selection_order.items():
            if len(source_ids) != len(set(source_ids)):
                raise ValueError("knowledge source selection order must be unique")
            for source_id in source_ids:
                source = self.sources.get(source_id)
                if source is None:
                    raise ValueError(f"knowledge selection references unknown source: {source_id}")
                if source.knowledge_class is not knowledge_class:
                    raise ValueError("knowledge selection source has a different context class")
                selected.add(source_id)
        enabled = {source_id for source_id, source in self.sources.items() if source.enabled}
        if enabled - selected:
            raise ValueError(
                "every enabled knowledge source must have deterministic selection order"
            )
        literal = self.selection_order.get(KnowledgeClass.LITERAL, ())
        if not literal or not any(self.sources[item].enabled for item in literal):
            raise ValueError("knowledge configuration requires an enabled literal source")
        return self


class MishkanConfig(StrictConfigModel):
    """Complete effective configuration required before a run can be accepted."""

    schema_version: str
    mode: OperatingMode
    timezone: str
    project: ProjectConfig
    providers: dict[str, ProviderConfig] = Field(min_length=1)
    model_routes: dict[str, ModelRoute] = Field(min_length=1)
    agent_routes: dict[str, str] = Field(default_factory=dict)
    services: dict[str, ServiceConfig] = Field(default_factory=dict)
    credential_bindings: dict[str, CredentialReference] = Field(default_factory=dict)
    policy_sources: tuple[str, ...] = Field(min_length=1)
    tool_sources: tuple[str, ...] = ()
    inspection_profile: str | None = None
    isolation_profiles: tuple[str, ...] = ()
    crewai: CrewAIRuntimeConfig = Field(default_factory=CrewAIRuntimeConfig)
    daemon: DaemonConfig | None = None
    persistence: PersistenceConfig | None = None
    artifacts: ArtifactConfig | None = None
    sessions: SessionConfig | None = None
    web: WebConfig | None = None
    browser: BrowserConfig | None = None
    mcp: McpConfig | None = None
    skills: SkillsConfig | None = None
    engineer_profile: str | None = None
    community_candidate_sources: tuple[str, ...] = ()
    engineering_profile: str | None = None
    engineering_pack_sources: tuple[str, ...] = Field(
        default=("package://mishkan.resources.environment/technical-packs.yaml",),
        min_length=1,
    )
    mission_template_sources: tuple[str, ...] = (
        "package://mishkan.resources.organization/mission-templates.yaml",
    )
    notifications: NotificationConfig = Field(default_factory=NotificationConfig)
    telemetry: TelemetryConfig = Field(default_factory=TelemetryConfig)
    knowledge: KnowledgeConfig | None = None

    @field_validator("timezone")
    @classmethod
    def timezone_is_iana(cls, value: str) -> str:
        return validate_timezone(value)

    @model_validator(mode="after")
    def references_exist(self) -> Self:
        if self.schema_version in {"1.1", "1.2", "1.3", "1.4", "1.5", "1.6"}:
            missing = [
                field
                for field, value in (
                    ("tool_sources", self.tool_sources),
                    ("inspection_profile", self.inspection_profile),
                    ("isolation_profiles", self.isolation_profiles),
                )
                if not value
            ]
            if missing:
                raise ValueError(
                    f"configuration 1.1 requires governed capability fields: {missing}"
                )
        if self.schema_version in {"1.2", "1.3", "1.4", "1.5", "1.6"}:
            missing_daemon = [
                field
                for field, value in (
                    ("daemon", self.daemon),
                    ("persistence", self.persistence),
                    ("artifacts", self.artifacts),
                    ("sessions", self.sessions),
                )
                if value is None
            ]
            if missing_daemon:
                raise ValueError(f"configuration 1.2 requires daemon fields: {missing_daemon}")
        if self.schema_version in {"1.3", "1.4", "1.5", "1.6"}:
            missing_capabilities = [
                field
                for field, value in (
                    ("web", self.web),
                    ("browser", self.browser),
                    ("mcp", self.mcp),
                )
                if value is None
            ]
            if missing_capabilities:
                raise ValueError(
                    f"configuration 1.3 requires capability fields: {missing_capabilities}"
                )
            assert self.web is not None
            assert self.browser is not None
            assert self.mcp is not None
            referenced_network_profiles = {
                profile.network_profile for profile in self.browser.profiles.values()
            } | {
                connection.network_profile
                for connection in self.mcp.connections.values()
                if connection.network_profile is not None
            }
            missing_network_profiles = sorted(
                referenced_network_profiles - set(self.web.network_profiles)
            )
            if missing_network_profiles:
                raise ValueError(
                    f"browser/MCP configuration references unknown network profiles: "
                    f"{missing_network_profiles}"
                )
            if (
                self.telemetry.exporter is not None
                and self.telemetry.exporter.network_profile not in self.web.network_profiles
            ):
                raise ValueError("telemetry exporter references an unknown network profile")
        if self.schema_version in {"1.4", "1.5", "1.6"} and self.skills is None:
            raise ValueError("configuration 1.4+ requires the Skills capability configuration")
        if self.schema_version in {"1.5", "1.6"} and self.engineering_profile is None:
            raise ValueError("configuration 1.5+ requires an Engineering profile")
        if self.schema_version == "1.6":
            if self.knowledge is None:
                raise ValueError("configuration 1.6 requires Knowledge configuration")
            assert self.web is not None
            assert self.mcp is not None
            referenced_network_profiles = {
                source.network_profile
                for source in self.knowledge.sources.values()
                if source.network_profile is not None
            }
            missing_knowledge_networks = sorted(
                referenced_network_profiles - set(self.web.network_profiles)
            )
            if missing_knowledge_networks:
                raise ValueError(
                    "knowledge configuration references unknown network profiles: "
                    f"{missing_knowledge_networks}"
                )
            referenced_mcp = {
                source.mcp_connection
                for source in self.knowledge.sources.values()
                if source.mcp_connection is not None
            }
            missing_knowledge_mcp = sorted(referenced_mcp - set(self.mcp.connections))
            if missing_knowledge_mcp:
                raise ValueError(
                    "knowledge configuration references unknown MCP connections: "
                    f"{missing_knowledge_mcp}"
                )

        missing_providers = sorted(
            {
                candidate.provider
                for route in self.model_routes.values()
                for candidate in route.candidates
                if candidate.provider not in self.providers
            }
        )
        if missing_providers:
            raise ValueError(f"model routes reference unknown providers: {missing_providers}")

        missing_routes = sorted(
            {
                *self.agent_routes.values(),
                self.crewai.mission_pm_model_route,
                self.crewai.mission_cto_model_route,
                self.crewai.mission_environment_model_route,
            }
            - set(self.model_routes)
        )
        if missing_routes:
            raise ValueError(f"CrewAI routing references unknown model routes: {missing_routes}")
        return self
