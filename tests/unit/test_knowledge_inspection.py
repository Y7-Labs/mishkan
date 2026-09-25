from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from mishkan.domain.errors import ErrorCode, MishkanError
from mishkan.knowledge.inspection import (
    EvidenceInspectionAction,
    EvidenceInspectionProfile,
    EvidenceInspectionProfileLoader,
    EvidenceInspectionRule,
    KnowledgeEvidenceInspector,
)


def _profile(*rules: EvidenceInspectionRule) -> EvidenceInspectionProfile:
    return EvidenceInspectionProfile(
        profile_id="test-inspection",
        revision="1",
        adoption_authority="test",
        rules=rules,
    )


def test_inspection_redacts_marks_binary_and_assembled_secret_content() -> None:
    inspector = KnowledgeEvidenceInspector(
        _profile(
            EvidenceInspectionRule(
                rule_id="email",
                category="pii",
                pattern=r"person@example\.com",
                action=EvidenceInspectionAction.REDACT,
            ),
            EvidenceInspectionRule(
                rule_id="instruction",
                category="instruction_like",
                pattern=r"ignore previous",
                action=EvidenceInspectionAction.MARK,
            ),
        )
    )
    result = inspector.inspect(
        b"person@example.com says ignore previous guidance",
        media_type="text/plain",
    )
    assert result.content == b"[REDACTED] says ignore previous guidance"
    assert result.redacted is True
    assert result.findings == ("pii:email", "instruction_like:instruction")
    binary = inspector.inspect(b"person@example.com", media_type="application/octet-stream")
    assert binary.content == b"person@example.com"
    assert binary.findings == ()

    assembled = b"provider-" + b"secret"
    with pytest.raises(MishkanError) as secret:
        inspector.inspect(
            assembled,
            media_type="application/json",
            resolved_secrets=("provider-secret",),
        )
    assert secret.value.envelope.code is ErrorCode.SECRET_CONTENT


def test_inspection_profile_rejects_duplicate_rules_and_invalid_regex() -> None:
    rule = EvidenceInspectionRule(
        rule_id="duplicate",
        category="test",
        pattern="value",
        action=EvidenceInspectionAction.MARK,
    )
    with pytest.raises(ValidationError, match="unique"):
        _profile(rule, rule)
    invalid = EvidenceInspectionRule(
        rule_id="invalid-regex",
        category="test",
        pattern="[",
        action=EvidenceInspectionAction.MARK,
    )
    with pytest.raises(MishkanError) as regex:
        KnowledgeEvidenceInspector(_profile(invalid))
    assert regex.value.envelope.code is ErrorCode.CONFIGURATION


@pytest.mark.parametrize(
    ("name", "content"),
    (
        ("malformed.yaml", b"rules: ["),
        ("not-mapping.yaml", b"- list"),
        ("invalid.yaml", b"schema_version: '1.0'\nprofile_id: incomplete"),
    ),
)
def test_inspection_loader_rejects_malformed_documents(
    tmp_path: Path,
    name: str,
    content: bytes,
) -> None:
    source = tmp_path / name
    source.write_bytes(content)
    with pytest.raises(MishkanError) as rejected:
        EvidenceInspectionProfileLoader().load(f"project:{name}", tmp_path)
    assert rejected.value.envelope.code is ErrorCode.CONFIGURATION


def test_inspection_loader_rejects_missing_and_incomplete_package_sources(
    tmp_path: Path,
) -> None:
    loader = EvidenceInspectionProfileLoader()
    with pytest.raises(MishkanError) as missing:
        loader.load("project:missing.yaml", tmp_path)
    assert missing.value.envelope.code is ErrorCode.CONFIGURATION
    with pytest.raises(MishkanError) as package:
        loader.load("package://mishkan.resources.knowledge", tmp_path)
    assert package.value.envelope.code is ErrorCode.CONFIGURATION
