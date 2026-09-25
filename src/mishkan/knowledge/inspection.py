"""Public, versioned inspection of untrusted knowledge evidence."""

from __future__ import annotations

import re
from enum import StrEnum
from importlib.resources import files
from pathlib import Path
from typing import Any, Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from mishkan.domain.errors import ErrorCode, MishkanError
from mishkan.domain.schema import SchemaRegistry
from mishkan.domain.sources import resolve_source_path


class EvidenceInspectionModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class EvidenceInspectionAction(StrEnum):
    BLOCK = "block"
    REDACT = "redact"
    MARK = "mark"


class EvidenceInspectionRule(EvidenceInspectionModel):
    rule_id: str = Field(pattern=r"^[a-z][a-z0-9_.-]{1,127}$")
    category: str = Field(pattern=r"^[a-z][a-z0-9_.-]{1,63}$")
    pattern: str = Field(min_length=1, max_length=2_048)
    action: EvidenceInspectionAction
    replacement: str = Field(default="[REDACTED]", max_length=256)


class EvidenceInspectionProfile(EvidenceInspectionModel):
    schema_version: str = "1.0"
    profile_id: str = Field(min_length=1, max_length=256)
    revision: str = Field(min_length=1, max_length=128)
    adoption_authority: str = Field(min_length=1, max_length=256)
    rules: tuple[EvidenceInspectionRule, ...]

    @model_validator(mode="after")
    def rule_identities_are_unique(self) -> Self:
        identities = [rule.rule_id for rule in self.rules]
        if len(identities) != len(set(identities)):
            raise ValueError("knowledge inspection rule identities must be unique")
        return self


class EvidenceInspectionResult(EvidenceInspectionModel):
    content: bytes
    findings: tuple[str, ...] = ()
    redacted: bool = False


class KnowledgeEvidenceInspector:
    """Apply the configured evidence profile before artifact publication."""

    def __init__(self, profile: EvidenceInspectionProfile) -> None:
        self.profile = profile
        try:
            self._compiled = tuple((rule, re.compile(rule.pattern)) for rule in profile.rules)
        except re.error as exc:
            raise MishkanError(
                ErrorCode.CONFIGURATION,
                "knowledge inspection profile contains an invalid expression",
                details={"profile_id": profile.profile_id},
            ) from exc

    def inspect(
        self,
        content: bytes,
        *,
        media_type: str,
        resolved_secrets: tuple[str, ...] = (),
    ) -> EvidenceInspectionResult:
        for secret in resolved_secrets:
            if secret and secret.encode("utf-8") in content:
                raise MishkanError(
                    ErrorCode.SECRET_CONTENT,
                    "resolved credential content reached the knowledge boundary",
                    details={"profile_id": self.profile.profile_id},
                )
        if not self._is_textual(media_type):
            return EvidenceInspectionResult(content=content)
        text = content.decode("utf-8", errors="replace")
        findings: list[str] = []
        redacted = False
        for rule, expression in self._compiled:
            if expression.search(text) is None:
                continue
            findings.append(f"{rule.category}:{rule.rule_id}")
            if rule.action is EvidenceInspectionAction.BLOCK:
                raise MishkanError(
                    ErrorCode.SECRET_CONTENT,
                    "configured knowledge inspection rule blocked evidence",
                    details={
                        "profile_id": self.profile.profile_id,
                        "rule_id": rule.rule_id,
                        "category": rule.category,
                    },
                )
            if rule.action is EvidenceInspectionAction.REDACT:
                text = expression.sub(rule.replacement, text)
                redacted = True
        return EvidenceInspectionResult(
            content=text.encode("utf-8"),
            findings=tuple(findings),
            redacted=redacted,
        )

    @staticmethod
    def _is_textual(media_type: str) -> bool:
        normalized = media_type.casefold()
        return normalized.startswith("text/") or "json" in normalized or "xml" in normalized


class EvidenceInspectionProfileLoader:
    def load(self, uri: str, project_root: Path) -> EvidenceInspectionProfile:
        raw = self._read(uri, project_root)
        try:
            document: Any = yaml.safe_load(raw)
        except yaml.YAMLError as exc:
            raise MishkanError(
                ErrorCode.CONFIGURATION,
                "knowledge inspection profile is malformed YAML",
                details={"source": uri},
            ) from exc
        if not isinstance(document, dict):
            raise MishkanError(
                ErrorCode.CONFIGURATION,
                "knowledge inspection profile must contain a mapping",
                details={"source": uri},
            )
        SchemaRegistry.require_supported(
            "mishkan.knowledge-inspection", document.get("schema_version")
        )
        try:
            return EvidenceInspectionProfile.model_validate(document)
        except ValidationError as exc:
            raise MishkanError(
                ErrorCode.CONFIGURATION,
                "knowledge inspection profile is invalid",
                details={"source": uri, "violations": len(exc.errors())},
            ) from exc

    @staticmethod
    def _read(uri: str, project_root: Path) -> bytes:
        if uri.startswith("package://"):
            location = uri.removeprefix("package://")
            module, separator, resource = location.partition("/")
            if not separator:
                raise MishkanError(
                    ErrorCode.CONFIGURATION,
                    "package knowledge-inspection URI must identify a resource",
                )
            return files(module).joinpath(resource).read_bytes()
        path = resolve_source_path(uri, project_root, "knowledge inspection profile")
        try:
            return path.read_bytes()
        except OSError as exc:
            raise MishkanError(
                ErrorCode.CONFIGURATION,
                "knowledge inspection profile cannot be read",
                details={"source": uri, "reason": type(exc).__name__},
            ) from exc
