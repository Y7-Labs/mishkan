"""Explicit configuration-schema migrations."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any

import yaml

from mishkan.config.loader import ConfigLoader
from mishkan.config.presets import preset_text
from mishkan.domain.errors import ErrorCode, MishkanError


def migrate_to_1_2(source: Path) -> Path:
    """Upgrade one explicit 1.1 YAML source without modifying layered peers."""
    target = source.expanduser().resolve()
    try:
        document: Any = yaml.safe_load(target.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise MishkanError(
            ErrorCode.CONFIGURATION,
            "configuration source cannot be migrated",
            details={"source": str(target)},
        ) from exc
    if not isinstance(document, dict) or document.get("schema_version") != "1.1":
        raise MishkanError(
            ErrorCode.VERSION,
            "configuration migration requires one schema 1.1 source",
            details={"source": str(target), "automatic_migration": False},
        )
    mode = document.get("mode")
    if mode not in {"local", "cloud", "hybrid"}:
        raise MishkanError(
            ErrorCode.CONFIGURATION,
            "configuration mode has no daemon migration preset",
            details={"mode": mode},
        )
    defaults = yaml.safe_load(preset_text(str(mode)))
    document["schema_version"] = "1.2"
    for field in ("daemon", "persistence", "artifacts", "sessions"):
        document.setdefault(field, defaults[field])
    payload = yaml.safe_dump(document, sort_keys=False, allow_unicode=True).encode()
    descriptor, staged_name = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent)
    staged = Path(staged_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        ConfigLoader().load([staged])
        os.replace(staged, target)
        directory = os.open(target.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        staged.unlink(missing_ok=True)
    return target


def migrate_to_latest(source: Path) -> Path:
    """Atomically migrate one explicit 1.1-1.5 source to schema 1.6."""
    target = source.expanduser().resolve()
    try:
        document: Any = yaml.safe_load(target.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise MishkanError(
            ErrorCode.CONFIGURATION,
            "configuration source cannot be migrated",
            details={"source": str(target)},
        ) from exc
    if not isinstance(document, dict) or document.get("schema_version") not in {
        "1.1",
        "1.2",
        "1.3",
        "1.4",
        "1.5",
    }:
        raise MishkanError(
            ErrorCode.VERSION,
            "configuration migration requires one schema 1.1 through 1.5 source",
            details={"source": str(target), "automatic_migration": False},
        )
    mode = document.get("mode")
    if mode not in {"local", "cloud", "hybrid"}:
        raise MishkanError(
            ErrorCode.CONFIGURATION,
            "configuration mode has no capability migration preset",
            details={"mode": mode},
        )
    defaults = yaml.safe_load(preset_text(str(mode)))
    document["schema_version"] = "1.6"
    for field in (
        "daemon",
        "persistence",
        "artifacts",
        "sessions",
        "web",
        "browser",
        "mcp",
        "skills",
        "engineering_profile",
        "knowledge",
    ):
        document.setdefault(field, defaults[field])
    payload = yaml.safe_dump(document, sort_keys=False, allow_unicode=True).encode()
    descriptor, staged_name = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent)
    staged = Path(staged_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        ConfigLoader().load([staged])
        os.replace(staged, target)
        directory = os.open(target.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        staged.unlink(missing_ok=True)
    return target
