"""Atomic materialization and verification of context-package projections."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Protocol

from mishkan.artifacts.models import ArtifactLifecycle, ArtifactManifest, ArtifactValidation
from mishkan.context.models import (
    ContextPackEntry,
    ContextPackManifest,
    ContextPackMaterialization,
    MaterializedContextEntry,
)
from mishkan.domain.errors import ErrorCode, MishkanError

_MANIFEST_PATH = Path(".mishkan/context-pack.json")


class ContextArtifactReader(Protocol):
    def read_manifest(self, reference: str) -> ArtifactManifest: ...

    def read_bytes(self, reference: str) -> bytes: ...


class ContextPackMaterializer:
    """Create a disposable context tree without making it authoritative."""

    def __init__(self, artifacts: ContextArtifactReader, *, root: Path) -> None:
        self._artifacts = artifacts
        self._root = root.resolve()
        self._root.mkdir(mode=0o700, parents=True, exist_ok=True)

    def materialize(
        self,
        manifest: ContextPackManifest,
        destination: Path,
    ) -> ContextPackMaterialization:
        target = self._new_target(destination)
        staging = Path(tempfile.mkdtemp(prefix=f".{target.name}-", dir=target.parent))
        materialized: list[MaterializedContextEntry] = []
        omitted: list[str] = []
        try:
            for entry in manifest.entries:
                try:
                    content = self._read_verified(entry)
                except MishkanError:
                    if entry.required:
                        raise
                    omitted.append(entry.logical_path)
                    continue
                path = staging.joinpath(*Path(entry.logical_path).parts)
                path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                self._write_new_file(path, content)
                materialized.append(
                    MaterializedContextEntry(
                        logical_path=entry.logical_path,
                        artifact_reference=entry.artifact_reference,
                        digest=entry.digest,
                        size_bytes=len(content),
                    )
                )

            result = self._result(manifest, materialized, omitted)
            metadata = staging / _MANIFEST_PATH
            metadata.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            self._write_new_file(
                metadata,
                self._metadata_bytes(manifest, result),
            )
            os.replace(staging, target)
            self._fsync_directory(target.parent)
            return result
        except BaseException:
            shutil.rmtree(staging, ignore_errors=True)
            raise

    def verify(
        self,
        manifest: ContextPackManifest,
        materialization: ContextPackMaterialization,
        destination: Path,
    ) -> bool:
        try:
            target = self._target(destination)
        except MishkanError:
            return False
        if not target.is_dir() or materialization.manifest_fingerprint != manifest.fingerprint:
            return False
        observed: list[MaterializedContextEntry] = []
        try:
            for entry in materialization.entries:
                path = target.joinpath(*Path(entry.logical_path).parts)
                if path.is_symlink() or not path.is_file():
                    return False
                content = path.read_bytes()
                digest = f"sha256:{hashlib.sha256(content).hexdigest()}"
                if digest != entry.digest or len(content) != entry.size_bytes:
                    return False
                observed.append(entry)
            metadata = target / _MANIFEST_PATH
            if metadata.is_symlink() or metadata.read_bytes() != self._metadata_bytes(
                manifest, materialization
            ):
                return False
        except OSError:
            return False
        return self._tree_digest(observed) == materialization.tree_digest

    def model_projection(self, manifest: ContextPackManifest, *, max_bytes: int) -> str:
        """Render one verified, bounded projection for an agent-model boundary."""
        if max_bytes < 1:
            raise ValueError("context model projection bound must be positive")
        entries: list[dict[str, object]] = []
        omitted: list[str] = []
        for entry in manifest.entries:
            try:
                content = self._read_verified(entry)
            except MishkanError:
                if entry.required:
                    raise
                omitted.append(entry.logical_path)
                continue
            projected: dict[str, object] = {
                "logical_path": entry.logical_path,
                "layer": entry.layer,
                "artifact_reference": entry.artifact_reference,
                "digest": entry.digest,
                "media_type": entry.media_type,
                "source_revision": entry.source_revision,
            }
            if self._textual(entry.media_type):
                try:
                    projected["content"] = content.decode("utf-8")
                except UnicodeDecodeError as exc:
                    raise MishkanError(
                        ErrorCode.CONTEXT,
                        "textual context artifact is not valid UTF-8",
                        details={"logical_path": entry.logical_path},
                    ) from exc
            else:
                projected["content"] = None
                projected["limitation"] = "binary content omitted from model projection"
            entries.append(projected)
        encoded = json.dumps(
            {
                "schema_version": "1.0",
                "context_pack_id": str(manifest.context_pack_id),
                "manifest_fingerprint": manifest.fingerprint,
                "entries": entries,
                "omitted_optional_paths": omitted,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        if len(encoded) > max_bytes:
            raise MishkanError(
                ErrorCode.CONTEXT,
                "context model projection exceeds its configured bound",
                details={"observed": len(encoded), "limit": max_bytes},
            )
        return encoded.decode("utf-8")

    def _read_verified(self, entry: ContextPackEntry) -> bytes:
        try:
            artifact = self._artifacts.read_manifest(entry.artifact_reference)
            content = self._artifacts.read_bytes(entry.artifact_reference)
        except MishkanError as exc:
            raise MishkanError(
                ErrorCode.CONTEXT,
                "context source artifact is unavailable",
                details={
                    "logical_path": entry.logical_path,
                    "artifact_reference": entry.artifact_reference,
                    "required": entry.required,
                },
            ) from exc
        observed = f"sha256:{hashlib.sha256(content).hexdigest()}"
        if (
            artifact.digest != entry.digest
            or artifact.size_bytes != entry.size_bytes
            or observed != entry.digest
            or len(content) != entry.size_bytes
            or artifact.declared_media_type != entry.media_type
            or artifact.sensitivity != entry.sensitivity
            or artifact.lifecycle is not ArtifactLifecycle.AVAILABLE
            or artifact.validation is not ArtifactValidation.INTEGRITY_VERIFIED
        ):
            raise MishkanError(
                ErrorCode.CONTEXT,
                "context source does not match the accepted manifest",
                details={
                    "logical_path": entry.logical_path,
                    "artifact_reference": entry.artifact_reference,
                },
            )
        return content

    def _new_target(self, destination: Path) -> Path:
        target = self._target(destination)
        parent = target.parent
        parent.mkdir(parents=True, exist_ok=True)
        if target.parent.resolve() != parent or target.exists() or target.is_symlink():
            raise MishkanError(
                ErrorCode.CONTEXT,
                "context materialization destination must not already exist",
                details={"destination": str(target)},
            )
        return target

    def _target(self, destination: Path) -> Path:
        if destination.is_absolute() or any(part in {"", ".", ".."} for part in destination.parts):
            raise MishkanError(
                ErrorCode.CONTEXT,
                "context materialization destination must be a safe relative path",
                details={"destination": str(destination)},
            )
        parent = (self._root / destination.parent).resolve()
        target = parent / destination.name
        if not target.is_relative_to(self._root):
            raise MishkanError(
                ErrorCode.CONTEXT,
                "context materialization destination escapes its configured root",
                details={"destination": str(destination)},
            )
        return target

    @staticmethod
    def _write_new_file(path: Path, content: bytes) -> None:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(path, flags, 0o400)
        try:
            with os.fdopen(descriptor, "wb", closefd=False) as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
        finally:
            os.close(descriptor)

    @classmethod
    def _result(
        cls,
        manifest: ContextPackManifest,
        entries: list[MaterializedContextEntry],
        omitted: list[str],
    ) -> ContextPackMaterialization:
        frozen_entries = tuple(entries)
        return ContextPackMaterialization(
            context_pack_id=manifest.context_pack_id,
            manifest_fingerprint=manifest.fingerprint,
            tree_digest=cls._tree_digest(frozen_entries),
            entries=frozen_entries,
            omitted_optional_paths=tuple(omitted),
            total_bytes=sum(entry.size_bytes for entry in frozen_entries),
        )

    @staticmethod
    def _tree_digest(
        entries: tuple[MaterializedContextEntry, ...] | list[MaterializedContextEntry],
    ) -> str:
        payload = [entry.model_dump(mode="json") for entry in entries]
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return f"sha256:{hashlib.sha256(canonical).hexdigest()}"

    @staticmethod
    def _metadata_bytes(
        manifest: ContextPackManifest,
        result: ContextPackMaterialization,
    ) -> bytes:
        return (
            json.dumps(
                {
                    "manifest": manifest.model_dump(mode="json"),
                    "materialization": result.model_dump(mode="json"),
                },
                indent=2,
                sort_keys=True,
            )
            + "\n"
        ).encode("utf-8")

    @staticmethod
    def _fsync_directory(path: Path) -> None:
        descriptor = os.open(path, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    @staticmethod
    def _textual(media_type: str) -> bool:
        normalized = media_type.casefold()
        return normalized.startswith("text/") or any(
            token in normalized for token in ("json", "yaml", "xml")
        )
