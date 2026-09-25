from pathlib import Path

import yaml

from mishkan.config.loader import ConfigLoader
from mishkan.config.migration import migrate_to_latest
from mishkan.config.presets import preset_text


def _write_previous_schema(path: Path, version: str) -> None:
    document = yaml.safe_load(preset_text("local"))
    document["schema_version"] = version
    document.pop("knowledge")
    document.pop("engineering_profile")
    document.pop("skills")
    for field in ("web", "browser", "mcp"):
        if version == "1.3":
            break
        document.pop(field)
    if version == "1.1":
        for field in ("daemon", "persistence", "artifacts", "sessions"):
            document.pop(field)
    path.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")


def test_explicit_config_migration_adds_public_integration_surfaces(tmp_path: Path) -> None:
    source = tmp_path / "config.yaml"
    _write_previous_schema(source, "1.2")

    migrate_to_latest(source)
    config = ConfigLoader().load([source]).value

    assert config.schema_version == "1.6"
    assert config.web is not None
    assert config.browser is not None
    assert config.mcp is not None
    assert config.web.sources["searxng-local"].role.value == "broker"
    assert set(config.mcp.connections) == {"graphify-local"}
    assert config.mcp.task_poll_min_seconds == 0.1
    assert config.mcp.task_poll_max_seconds == 5.0
    assert config.skills is not None
    assert config.skills.sources[0].default_activation.value == "candidate"
    assert config.engineering_profile is not None
    assert config.knowledge is not None


def test_latest_migration_can_cross_both_explicit_previous_versions(tmp_path: Path) -> None:
    source = tmp_path / "config.yaml"
    _write_previous_schema(source, "1.1")

    migrate_to_latest(source)
    config = ConfigLoader().load([source]).value

    assert config.schema_version == "1.6"
    assert config.daemon is not None
    assert config.web is not None
    assert config.skills is not None
    assert config.knowledge is not None


def test_latest_migration_adds_skills_to_schema_1_3(tmp_path: Path) -> None:
    source = tmp_path / "config.yaml"
    _write_previous_schema(source, "1.3")

    migrate_to_latest(source)
    config = ConfigLoader().load([source]).value

    assert config.schema_version == "1.6"
    assert config.web is not None
    assert config.skills is not None
    assert config.knowledge is not None


def test_latest_migration_adds_engineering_profile_to_schema_1_4(tmp_path: Path) -> None:
    source = tmp_path / "config.yaml"
    _write_previous_schema(source, "1.4")

    migrate_to_latest(source)
    config = ConfigLoader().load([source]).value

    assert config.schema_version == "1.6"
    assert config.skills is not None
    assert config.engineering_profile == "package://mishkan.resources.environment/default.yaml"
    assert config.knowledge is not None


def test_latest_migration_adds_knowledge_to_schema_1_5(tmp_path: Path) -> None:
    source = tmp_path / "config.yaml"
    _write_previous_schema(source, "1.5")

    migrate_to_latest(source)
    config = ConfigLoader().load([source]).value

    assert config.schema_version == "1.6"
    assert config.knowledge is not None
    assert config.knowledge.selection_order["literal"] == ("literal-native",)
