import hashlib
from pathlib import Path

import pytest
from pydantic import ValidationError

from mishkan.artifacts.models import ArtifactManifest, ArtifactProvenance
from mishkan.artifacts.store import FilesystemArtifactStore
from mishkan.context import ContextPackEntry, ContextPackManifest, ContextPackMaterializer
from mishkan.domain.errors import ErrorCode, MishkanError
from mishkan.skills.models import SkillLoadEvidence, SkillUseOutcome


def _provenance() -> ArtifactProvenance:
    return ArtifactProvenance(
        producer_identity="context-test",
        run_id="run-1",
        task_attempt_id="task-1-attempt-1",
        call_id="call-1",
        capability="artifact.put",
        channel="context.input",
    )


def _store(tmp_path: Path) -> FilesystemArtifactStore:
    return FilesystemArtifactStore(tmp_path / "artifacts", max_artifact_bytes=100_000)


def _put(store: FilesystemArtifactStore, content: bytes) -> ArtifactManifest:
    return store.put_bytes(
        content,
        media_type="text/markdown",
        provenance=_provenance(),
        complete=True,
    )


def _entry(
    artifact: ArtifactManifest,
    *,
    path: str,
    layer: str,
    order: int,
    required: bool = True,
) -> ContextPackEntry:
    return ContextPackEntry(
        logical_path=path,
        layer=layer,
        order=order,
        artifact_reference=artifact.reference,
        digest=artifact.digest,
        size_bytes=artifact.size_bytes,
        media_type=artifact.declared_media_type,
        sensitivity=artifact.sensitivity,
        required=required,
        source_revision="git:abc123",
    )


def _manifest(store: FilesystemArtifactStore) -> ContextPackManifest:
    identity = _put(store, b"# Agent identity\n")
    task = _put(store, b"# Task contract\n")
    output = _put(store, b'{"type":"object"}\n')
    verification = _put(store, b"# Verification\n- schema\n")
    entries = (
        _entry(identity, path="identity/agent.md", layer="identity", order=0),
        _entry(task, path="task/contract.md", layer="task", order=1),
        _entry(output, path="task/output.schema.json", layer="task", order=2),
        _entry(verification, path="task/verify.md", layer="task", order=3),
    )
    return ContextPackManifest(
        organization_revision="organization:59@1",
        agent_identity="platform-engineer",
        mission_id="mission-1",
        mission_brief_revision="brief:1",
        plan_revision="plan:3",
        run_id="run-1",
        task_id="task-1",
        output_contract_reference=output.reference,
        output_contract_digest=output.digest,
        verification_contract_reference=verification.reference,
        verification_contract_digest=verification.digest,
        max_entries=10,
        max_entry_bytes=10_000,
        max_total_bytes=50_000,
        entries=entries,
    )


def _materializer(store: FilesystemArtifactStore, tmp_path: Path) -> ContextPackMaterializer:
    return ContextPackMaterializer(store, root=tmp_path / "packs")


@pytest.mark.parametrize(
    "logical_path",
    ("../secret", "/absolute", "a/../../secret", "a\\b", ".mishkan/override.json"),
)
def test_context_entry_rejects_unsafe_logical_paths(logical_path: str) -> None:
    with pytest.raises(ValidationError, match="context logical path"):
        ContextPackEntry(
            logical_path=logical_path,
            layer="task",
            order=0,
            artifact_reference="artifact:00000000-0000-0000-0000-000000000000",
            digest=f"sha256:{'0' * 64}",
            size_bytes=1,
            media_type="text/plain",
            sensitivity="internal",
        )


def test_context_manifest_requires_order_unique_paths_bounds_and_contracts(tmp_path: Path) -> None:
    store = _store(tmp_path)
    manifest = _manifest(store)
    duplicated = (*manifest.entries, manifest.entries[-1].model_copy(update={"order": 4}))
    with pytest.raises(ValidationError, match="logical paths must be unique"):
        ContextPackManifest(**{**manifest.model_dump(), "entries": duplicated})

    case_collision = (
        *manifest.entries,
        manifest.entries[-1].model_copy(update={"logical_path": "TASK/VERIFY.md", "order": 4}),
    )
    with pytest.raises(ValidationError, match="portable across case rules"):
        ContextPackManifest(**{**manifest.model_dump(), "entries": case_collision})

    prefix_collision = (
        *manifest.entries,
        manifest.entries[-1].model_copy(update={"logical_path": "task", "order": 4}),
    )
    with pytest.raises(ValidationError, match="must not contain another file path"):
        ContextPackManifest(**{**manifest.model_dump(), "entries": prefix_collision})

    reversed_entries = tuple(reversed(manifest.entries))
    with pytest.raises(ValidationError, match="deterministic order"):
        ContextPackManifest(**{**manifest.model_dump(), "entries": reversed_entries})

    with pytest.raises(ValidationError, match="total byte bound"):
        ContextPackManifest(**{**manifest.model_dump(), "max_total_bytes": 1})

    without_output = tuple(
        entry for entry in manifest.entries if "output.schema" not in entry.logical_path
    )
    with pytest.raises(ValidationError, match="exact output contract"):
        ContextPackManifest(**{**manifest.model_dump(), "entries": without_output})

    optional_output = tuple(
        entry.model_copy(update={"required": False})
        if "output.schema" in entry.logical_path
        else entry
        for entry in manifest.entries
    )
    with pytest.raises(ValidationError, match="exact output contract"):
        ContextPackManifest(**{**manifest.model_dump(), "entries": optional_output})


def test_context_manifest_binds_loaded_skill_evidence_to_exact_content(tmp_path: Path) -> None:
    store = _store(tmp_path)
    manifest = _manifest(store)
    instruction = manifest.entries[1]
    load = SkillLoadEvidence(
        task_id=manifest.task_id,
        task_class="software.review",
        consuming_identity=manifest.agent_identity,
        skill_name="code-review",
        skill_version="1.0.0",
        package_fingerprint=f"sha256:{'f' * 64}",
        outcome=SkillUseOutcome.HIT,
        reason="skill is eligible",
        instruction_fingerprint=instruction.digest,
    )
    bound = ContextPackManifest(**{**manifest.model_dump(), "skill_loads": (load,)})
    assert bound.skill_loads == (load,)

    missing = load.model_copy(update={"instruction_fingerprint": f"sha256:{'0' * 64}"})
    with pytest.raises(ValidationError, match="every loaded skill content item"):
        ContextPackManifest(**{**manifest.model_dump(), "skill_loads": (missing,)})


def test_context_pack_materializes_reproducibly_and_detects_edits(tmp_path: Path) -> None:
    store = _store(tmp_path)
    manifest = _manifest(store)
    materializer = _materializer(store, tmp_path)

    first = materializer.materialize(manifest, Path("first"))
    second = materializer.materialize(manifest, Path("second"))

    assert first == second
    assert first.manifest_fingerprint == manifest.fingerprint
    assert materializer.verify(manifest, first, Path("first"))
    assert materializer.verify(manifest, second, Path("second"))
    assert (tmp_path / "packs/first/.mishkan/context-pack.json").is_file()

    projected = tmp_path / "packs/first/task/contract.md"
    projected.chmod(0o600)
    projected.write_text("changed outside MISHKAN", encoding="utf-8")
    assert not materializer.verify(manifest, first, Path("first"))
    assert manifest.fingerprint == second.manifest_fingerprint


def test_context_pack_renders_a_verified_bounded_model_projection(tmp_path: Path) -> None:
    store = _store(tmp_path)
    manifest = _manifest(store)
    materializer = _materializer(store, tmp_path)

    projection = materializer.model_projection(manifest, max_bytes=50_000)

    assert manifest.fingerprint in projection
    assert "artifact_reference" in projection
    assert "Agent identity" in projection
    with pytest.raises(MishkanError, match="exceeds its configured bound"):
        materializer.model_projection(manifest, max_bytes=10)


def test_context_pack_reports_missing_optional_source(tmp_path: Path) -> None:
    store = _store(tmp_path)
    manifest = _manifest(store)
    missing = ContextPackEntry(
        logical_path="references/optional.md",
        layer="reference",
        order=4,
        artifact_reference="artifact:00000000-0000-0000-0000-000000000000",
        digest=f"sha256:{'0' * 64}",
        size_bytes=1,
        media_type="text/markdown",
        sensitivity="internal",
        required=False,
    )
    manifest = ContextPackManifest(
        **{**manifest.model_dump(), "entries": (*manifest.entries, missing)}
    )

    result = _materializer(store, tmp_path).materialize(manifest, Path("pack"))

    assert result.omitted_optional_paths == ("references/optional.md",)
    assert not (tmp_path / "packs/pack/references/optional.md").exists()


def test_context_pack_refuses_missing_required_source_and_existing_target(tmp_path: Path) -> None:
    store = _store(tmp_path)
    manifest = _manifest(store)
    missing_required = manifest.entries[0].model_copy(
        update={
            "artifact_reference": "artifact:00000000-0000-0000-0000-000000000000",
            "digest": f"sha256:{'0' * 64}",
        }
    )
    manifest = ContextPackManifest(
        **{**manifest.model_dump(), "entries": (missing_required, *manifest.entries[1:])}
    )
    with pytest.raises(MishkanError) as caught:
        _materializer(store, tmp_path).materialize(manifest, Path("missing"))
    assert caught.value.envelope.code is ErrorCode.CONTEXT
    assert not (tmp_path / "packs/missing").exists()

    valid = _manifest(store)
    existing = tmp_path / "packs/existing"
    existing.mkdir()
    with pytest.raises(MishkanError, match="must not already exist"):
        _materializer(store, tmp_path).materialize(valid, Path("existing"))

    with pytest.raises(MishkanError, match="safe relative path"):
        _materializer(store, tmp_path).materialize(valid, Path("../escape"))


def test_context_pack_refuses_manifest_digest_drift(tmp_path: Path) -> None:
    store = _store(tmp_path)
    manifest = _manifest(store)
    drifted = manifest.entries[0].model_copy(
        update={"digest": f"sha256:{hashlib.sha256(b'other').hexdigest()}"}
    )
    manifest = ContextPackManifest(
        **{**manifest.model_dump(), "entries": (drifted, *manifest.entries[1:])}
    )

    with pytest.raises(MishkanError, match="does not match"):
        _materializer(store, tmp_path).materialize(manifest, Path("drift"))
