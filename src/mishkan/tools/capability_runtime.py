"""Truthful assembly of configured capability adapters for the production Gateway."""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

from mishkan.artifacts.service import DurableArtifactService
from mishkan.browser import (
    BrowserSupervisor,
    LazyPlaywrightChromiumDriver,
    build_browser_tool_adapters,
    playwright_chromium_ready,
)
from mishkan.config.models import MishkanConfig
from mishkan.daemon.composition import repository_busy_timeout_ms
from mishkan.domain.errors import ErrorCode, MishkanError
from mishkan.edits.git import GovernedGitService
from mishkan.persistence.runtime import DatabaseTarget
from mishkan.policy.models import EffectivePolicy
from mishkan.tools.adapters import CapabilityAdapter
from mishkan.tools.git import build_git_adapters
from mishkan.tools.inspection import ContentInspector
from mishkan.web.adapters import (
    BraveSearchAdapter,
    ExtractionAdapter,
    SearchAdapter,
    SearxngSearchAdapter,
    TrafilaturaExtractionAdapter,
)
from mishkan.web.cache import SQLiteWebCache
from mishkan.web.network import HttpxWebTransport
from mishkan.web.service import WebService
from mishkan.web.tools import build_web_tool_adapters


@dataclass(slots=True)
class CapabilityRuntime:
    adapters: dict[str, CapabilityAdapter]
    dependencies: frozenset[str]
    _browser: LazyPlaywrightChromiumDriver | None = None
    _browser_supervisor: BrowserSupervisor | None = None

    @property
    def adapter_ids(self) -> frozenset[str]:
        return frozenset(self.adapters)

    @property
    def browser_started(self) -> bool:
        return self._browser.started if self._browser is not None else False

    def close(self) -> None:
        if self._browser is not None:
            self._browser.shutdown()
        if self._browser_supervisor is not None:
            # Any session not explicitly closed before adapter shutdown has lost
            # its live handle and must never remain advertised as active.
            self._browser_supervisor.reconcile_all()


def build_capability_runtime(
    config: MishkanConfig,
    database: DatabaseTarget,
    workspace: Path,
    artifacts: DurableArtifactService,
    inspector: ContentInspector,
    policy: EffectivePolicy,
) -> CapabilityRuntime:
    """Build configured adapters without performing network or browser effects."""
    web_config = config.web
    browser_config = config.browser
    persistence = config.persistence
    if web_config is None or browser_config is None or persistence is None:
        raise MishkanError(ErrorCode.CONFIGURATION, "capability configuration is incomplete")
    busy_timeout_ms = repository_busy_timeout_ms(config)

    transport = HttpxWebTransport()
    search_adapters: dict[str, SearchAdapter] = {}
    for source in web_config.sources.values():
        if source.adapter == BraveSearchAdapter.adapter_id:
            search_adapters.setdefault(source.adapter, BraveSearchAdapter(transport))
        elif source.adapter == SearxngSearchAdapter.adapter_id:
            search_adapters.setdefault(source.adapter, SearxngSearchAdapter(transport))

    extraction_adapters: dict[str, ExtractionAdapter] = {}
    for extractor in web_config.extractors.values():
        if extractor.adapter == TrafilaturaExtractionAdapter.adapter_id:
            extraction_adapters.setdefault(
                extractor.adapter,
                TrafilaturaExtractionAdapter(),
            )

    web_service = WebService(
        web_config,
        artifacts,
        search_adapters=search_adapters,
        extraction_adapters=extraction_adapters,
        transport=transport,
        cache=SQLiteWebCache(database, busy_timeout_ms=busy_timeout_ms),
        content_inspector=inspector,
    )
    adapters = build_web_tool_adapters(web_config, web_service)
    git_available = shutil.which("git") is not None
    if git_available:
        adapters.update(build_git_adapters(workspace, GovernedGitService(artifacts), policy))

    configured_drivers = {profile.adapter for profile in browser_config.profiles.values()}
    browser: LazyPlaywrightChromiumDriver | None = None
    supervisor: BrowserSupervisor | None = None
    if (
        LazyPlaywrightChromiumDriver.adapter_id in configured_drivers
        and playwright_chromium_ready()
    ):
        browser = LazyPlaywrightChromiumDriver(
            web_config.network_profiles,
            max_diagnostic_entries=browser_config.max_diagnostic_entries,
            max_pending_downloads=browser_config.max_pending_downloads,
        )
        supervisor = BrowserSupervisor(
            database,
            workspace,
            browser_config,
            artifacts,
            {browser.adapter_id: browser},
            inspector,
            busy_timeout_ms=busy_timeout_ms,
        )
        # Reconcile handles lost by a prior daemon/runtime interruption before
        # exposing any Browser adapter to a new CrewAI run.
        supervisor.reconcile_all()
        adapters.update(build_browser_tool_adapters(supervisor))
    dependencies = {"trafilatura"} if extraction_adapters else set()
    if git_available:
        dependencies.add("git")
    if browser is not None:
        dependencies.add("playwright")
    return CapabilityRuntime(adapters, frozenset(dependencies), browser, supervisor)
