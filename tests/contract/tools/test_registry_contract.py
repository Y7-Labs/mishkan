from __future__ import annotations

from importlib.resources import files
from pathlib import Path

import pytest

from mishkan.domain.errors import ErrorCode, MishkanError
from mishkan.tools.catalog import ToolCatalog
from mishkan.tools.models import AvailabilityState

CATALOG_URI = "package://mishkan.resources.tools/core-catalog.yaml"
MECHANISM_CATALOG_URI = str(
    Path(__file__).parents[2] / "fixtures" / "tools" / "mechanism-catalog.yaml"
)
READ_ADAPTER = "native.repository.read_file"


def test_level_zero_search_does_not_load_full_tool_schema(tmp_path: Path) -> None:
    index = tmp_path / "catalog.yaml"
    index.write_text(
        """schema_version: '1.0'
source_id: project
source_kind: project
revision: '1'
adoption_authority: Engineer
tools:
  - tool_id: project.missing_contract
    version: 1.0.0
    summary: Searchable metadata without eager schema loading.
    effect_class: read
    source_id: project
    source_kind: project
    contract_uri: project:does-not-exist.yaml
""",
        encoding="utf-8",
    )

    catalog = ToolCatalog((str(index),), tmp_path)

    assert catalog.search("searchable")[0].tool_id == "project.missing_contract"
    with pytest.raises(MishkanError) as caught:
        catalog.snapshot(("project.missing_contract",))
    assert caught.value.envelope.code is ErrorCode.TOOL_CONTRACT


def test_nested_toolsets_resolve_to_exact_immutable_snapshot_and_binding(tmp_path: Path) -> None:
    index = tmp_path / "nested.yaml"
    index.write_text(
        """schema_version: '1.0'
source_id: bundled.core
source_kind: native
revision: test-only
adoption_authority: MISHKAN test suite
tools:
  - tool_id: repository.read_file
    version: 1.0.0
    summary: Test nested resolution with one runnable tool.
    effect_class: read
    source_id: bundled.core
    source_kind: native
    contract_uri: package://mishkan.resources.tools/contracts/read-file.yaml
toolsets:
  - toolset_id: test.inner
    version: 1.0.0
    summary: Inner test toolset.
    tools: [repository.read_file]
  - toolset_id: test.outer
    version: 1.0.0
    summary: Outer test toolset.
    toolsets: [test.inner]
""",
        encoding="utf-8",
    )
    catalog = ToolCatalog(
        (str(index),),
        tmp_path,
        available_adapters=frozenset({READ_ADAPTER}),
    )
    snapshot = catalog.snapshot(("test.outer",))
    binding = catalog.bind(
        snapshot,
        task_id="read-task",
        role="Engineer",
        tool_id="repository.read_file",
        allowed_targets=("README.md",),
    )

    assert tuple(tool.tool_id for tool in snapshot.tools) == ("repository.read_file",)
    assert binding.registry_fingerprint == snapshot.fingerprint
    assert (
        binding.contract_fingerprint
        == snapshot.require("repository.read_file").provenance_fingerprint
    )


def test_availability_is_visible_and_not_an_authorization_decision(tmp_path: Path) -> None:
    catalog = ToolCatalog((MECHANISM_CATALOG_URI,), tmp_path, runtime="python")
    command = next(tool for tool in catalog.list_metadata() if tool.tool_id == "command.run")

    availability = catalog.availability(command)

    assert availability.state is AvailabilityState.UNAVAILABLE
    assert availability.missing_conditions == ("runtime:python",)
    with pytest.raises(MishkanError) as caught:
        catalog.snapshot(("command.run",))
    assert caught.value.envelope.code is ErrorCode.TOOL_UNAVAILABLE


def test_bundled_catalogue_lists_metadata_but_binds_only_available_adapters(
    tmp_path: Path,
) -> None:
    adapters = frozenset(
        {
            READ_ADAPTER,
            "native.file.resolve",
            "native.file.stat",
            "native.file.read",
            "native.file.list",
            "native.search.files",
            "ripgrep.search.text",
            "python.ast.search.structure",
            "python.ast.search.symbol",
            "git.search.history",
            "native.process.exec",
            "native.shell.bash",
        }
    )
    catalog = ToolCatalog(
        (CATALOG_URI,),
        tmp_path,
        available_dependencies=frozenset({"rg", "git", "bash"}),
        available_adapters=adapters,
    )

    assert tuple(tool.tool_id for tool in catalog.list_metadata()) == (
        "knowledge.query",
        "file.resolve",
        "file.stat",
        "file.read",
        "file.list",
        "search.files",
        "search.text",
        "search.structural",
        "search.symbol",
        "search.history",
        "repository.read_file",
        "core.process.exec",
        "core.shell.run",
        "command.run",
        "git.stage",
        "git.commit",
        "git.push",
        "git.force_with_lease",
        "git.force_push",
        "web.search",
        "web.fetch",
        "web.request",
        "web.extract",
        "web.map",
        "web.crawl",
        "browser.open",
        "browser.observe",
        "browser.act",
        "browser.diagnostics",
        "browser.close",
    )
    snapshot = catalog.snapshot(("file.readonly",))
    assert tuple(tool.tool_id for tool in snapshot.tools) == (
        "file.resolve",
        "file.stat",
        "file.read",
        "file.list",
    )
    search_snapshot = catalog.snapshot(("search.basic",))
    assert tuple(tool.tool_id for tool in search_snapshot.tools) == (
        "search.files",
        "search.text",
    )
    complete_search = catalog.snapshot(("search.complete",))
    assert tuple(tool.tool_id for tool in complete_search.tools) == (
        "search.files",
        "search.text",
        "search.structural",
        "search.symbol",
        "search.history",
    )
    shell_snapshot = catalog.snapshot(("execution.shell",))
    assert tuple(tool.tool_id for tool in shell_snapshot.tools) == ("core.shell.run",)
    execution_snapshot = catalog.snapshot(("execution.local",))
    assert tuple(tool.tool_id for tool in execution_snapshot.tools) == (
        "core.process.exec",
        "core.shell.run",
    )
    with pytest.raises(MishkanError) as caught:
        catalog.snapshot(("web.complete",))
    assert caught.value.envelope.code is ErrorCode.TOOL_UNAVAILABLE
    with pytest.raises(MishkanError) as browser_caught:
        catalog.snapshot(("browser.complete",))
    assert browser_caught.value.envelope.code is ErrorCode.TOOL_UNAVAILABLE
    with pytest.raises(MishkanError) as git_caught:
        catalog.snapshot(("git.complete",))
    assert git_caught.value.envelope.code is ErrorCode.TOOL_UNAVAILABLE


def test_contract_without_an_installed_adapter_cannot_enter_snapshot(tmp_path: Path) -> None:
    catalog = ToolCatalog((CATALOG_URI,), tmp_path, available_adapters=frozenset())

    with pytest.raises(MishkanError) as caught:
        catalog.snapshot(("repository.read_file",))

    assert caught.value.envelope.code is ErrorCode.TOOL_UNAVAILABLE
    assert caught.value.envelope.details == {
        "tool_id": "repository.read_file",
        "missing_conditions": (f"adapter:{READ_ADAPTER}",),
    }


def test_tool_contract_materializes_only_public_input_defaults(tmp_path: Path) -> None:
    contract = (
        ToolCatalog(
            (CATALOG_URI,),
            tmp_path,
            available_adapters=frozenset({"native.process.exec"}),
        )
        .snapshot(("core.process.exec",))
        .require("core.process.exec")
    )
    supplied = {"executable": "/usr/bin/git", "args": ["show", "HEAD:README.md"]}

    materialized = contract.materialize_input_defaults(supplied)

    assert supplied == {"executable": "/usr/bin/git", "args": ["show", "HEAD:README.md"]}
    assert materialized == {
        "mode": "process",
        "executable": "/usr/bin/git",
        "args": ["show", "HEAD:README.md"],
        "cwd": ".",
        "environment": {},
        "credential_environment": {},
        "stdin": None,
        "timeout_seconds": 30,
        "expected_exit_codes": [0],
        "declared_effects": [],
        "output_policy": {
            "preview_bytes": 8192,
            "preserve_full_output_as_artifact": False,
        },
    }


def test_contract_with_installed_adapter_enters_snapshot(tmp_path: Path) -> None:
    catalog = ToolCatalog(
        (CATALOG_URI,),
        tmp_path,
        available_adapters=frozenset({READ_ADAPTER}),
    )

    snapshot = catalog.snapshot(("repository.read_file",))

    assert snapshot.require("repository.read_file").adapter == READ_ADAPTER


def test_complete_web_toolset_binds_only_when_all_concrete_adapters_exist(
    tmp_path: Path,
) -> None:
    adapters = frozenset(
        {
            "native.web.search",
            "native.web.fetch",
            "native.web.request",
            "native.web.extract",
            "native.web.map",
            "native.web.crawl",
        }
    )
    catalog = ToolCatalog((CATALOG_URI,), tmp_path, available_adapters=adapters)

    snapshot = catalog.snapshot(("web.complete",))

    assert tuple(contract.tool_id for contract in snapshot.tools) == (
        "web.search",
        "web.map",
        "web.fetch",
        "web.request",
        "web.extract",
        "web.crawl",
    )


def test_complete_browser_toolset_requires_playwright_and_all_governed_adapters(
    tmp_path: Path,
) -> None:
    adapters = frozenset(
        {
            "native.browser.open",
            "native.browser.observe",
            "native.browser.act",
            "native.browser.diagnostics",
            "native.browser.close",
        }
    )
    catalog = ToolCatalog(
        (CATALOG_URI,),
        tmp_path,
        available_dependencies=frozenset({"playwright"}),
        available_adapters=adapters,
    )

    snapshot = catalog.snapshot(("browser.complete",))

    assert tuple(contract.tool_id for contract in snapshot.tools) == (
        "browser.open",
        "browser.observe",
        "browser.act",
        "browser.diagnostics",
        "browser.close",
    )


def test_identity_collision_blocks_registry_snapshot(tmp_path: Path) -> None:
    template = """schema_version: '1.0'
source_id: {source}
source_kind: project
revision: '1'
adoption_authority: Engineer
tools:
  - tool_id: project.same
    version: 1.0.0
    summary: A colliding tool identity.
    effect_class: read
    source_id: {source}
    source_kind: project
    contract_uri: project:unused.yaml
"""
    first = tmp_path / "first.yaml"
    second = tmp_path / "second.yaml"
    first.write_text(template.format(source="first"), encoding="utf-8")
    second.write_text(template.format(source="second"), encoding="utf-8")

    with pytest.raises(MishkanError) as caught:
        ToolCatalog((str(first), str(second)), tmp_path)

    assert caught.value.envelope.code is ErrorCode.TOOL_DRIFT
    assert caught.value.envelope.details["collisions"] == {"project.same": ["first", "second"]}


def test_toolset_cycle_is_rejected_with_public_nesting_context(tmp_path: Path) -> None:
    index = tmp_path / "cycle.yaml"
    index.write_text(
        """schema_version: '1.0'
source_id: project
source_kind: project
revision: '1'
adoption_authority: Engineer
toolsets:
  - toolset_id: project.a
    version: 1.0.0
    summary: First cyclic set.
    toolsets: [project.b]
  - toolset_id: project.b
    version: 1.0.0
    summary: Second cyclic set.
    toolsets: [project.a]
""",
        encoding="utf-8",
    )

    with pytest.raises(MishkanError) as caught:
        ToolCatalog((str(index),), tmp_path).snapshot(("project.a",))

    assert caught.value.envelope.code is ErrorCode.TOOL_CONTRACT
    assert "cycle" in caught.value.envelope.message


def test_toolset_availability_and_health_are_public_binding_conditions(
    tmp_path: Path,
) -> None:
    index = tmp_path / "availability.yaml"
    index.write_text(
        """schema_version: '1.0'
source_id: project
source_kind: project
revision: '1'
adoption_authority: Engineer
tools:
  - tool_id: repository.read_file
    version: 1.0.0
    summary: One runnable read tool.
    effect_class: read
    source_id: project
    source_kind: project
    contract_uri: project:contract.yaml
toolsets:
  - toolset_id: project.healthy_reads
    version: 1.0.0
    summary: Reads requiring an observed healthy index.
    tools: [repository.read_file]
    availability:
      services: [project-index]
      health_checks: [project-index-ready]
""",
        encoding="utf-8",
    )
    contract = files("mishkan.resources.tools.contracts").joinpath("read-file.yaml")
    (tmp_path / "contract.yaml").write_text(
        contract.read_text(encoding="utf-8").replace(
            "source_id: bundled.core", "source_id: project"
        ),
        encoding="utf-8",
    )
    unavailable = ToolCatalog(
        (str(index),),
        tmp_path,
        available_services=frozenset({"project-index"}),
        available_adapters=frozenset({READ_ADAPTER}),
    )

    with pytest.raises(MishkanError) as caught:
        unavailable.snapshot(("project.healthy_reads",))

    assert caught.value.envelope.code is ErrorCode.TOOL_UNAVAILABLE
    assert caught.value.envelope.details["missing_conditions"] == ("health:project-index-ready",)
    available = ToolCatalog(
        (str(index),),
        tmp_path,
        available_services=frozenset({"project-index"}),
        healthy_checks=frozenset({"project-index-ready"}),
        available_adapters=frozenset({READ_ADAPTER}),
    )
    assert available.snapshot(("project.healthy_reads",)).tools[0].tool_id == (
        "repository.read_file"
    )


def test_metadata_contract_drift_blocks_binding(tmp_path: Path) -> None:
    index = tmp_path / "catalog.yaml"
    contract = tmp_path / "contract.yaml"
    index.write_text(
        """schema_version: '1.0'
source_id: project
source_kind: project
revision: '1'
adoption_authority: Engineer
tools:
  - tool_id: project.read
    version: 1.0.0
    summary: Metadata version one.
    effect_class: read
    source_id: project
    source_kind: project
    contract_uri: project:contract.yaml
""",
        encoding="utf-8",
    )
    contract.write_text(
        """schema_version: '1.0'
tool_id: project.read
version: 2.0.0
crewai_name: project_read
summary: Contract version two.
effect_class: read
source_id: project
source_kind: project
adapter: project.read
input_schema: {type: object}
result_schema: {type: object}
timeout_behavior: cancel_local
idempotency: idempotent
target_scopes: [path]
target_arguments: {path: [path]}
resources: {timeout_seconds: 30, memory_mb: 64, network: false, concurrency: 1}
""",
        encoding="utf-8",
    )

    with pytest.raises(MishkanError) as caught:
        ToolCatalog((str(index),), tmp_path).snapshot(("project.read",))

    assert caught.value.envelope.code is ErrorCode.TOOL_DRIFT
