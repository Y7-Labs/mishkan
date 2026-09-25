"""Durable artifact sessions with SQLite manifests and filesystem CAS bodies."""

from __future__ import annotations

import hashlib
import json
import os
import stat
import threading
from collections.abc import Iterator
from datetime import datetime, timedelta
from pathlib import Path, PurePosixPath
from typing import Literal, Protocol, cast
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from mishkan.artifacts.models import (
    ArtifactAvailability,
    ArtifactCollection,
    ArtifactFacts,
    ArtifactFactState,
    ArtifactHold,
    ArtifactLifecycle,
    ArtifactManifest,
    ArtifactPin,
    ArtifactProvenance,
    ArtifactReconciliationAction,
    ArtifactReconciliationIssue,
    ArtifactReconciliationPlan,
    ArtifactTrust,
    ArtifactValidation,
    GarbageCollectionPlan,
    UploadSession,
    WorkingReference,
)
from mishkan.domain.errors import ErrorCode, MishkanError
from mishkan.domain.identity import new_id
from mishkan.domain.time import utc_now
from mishkan.persistence.migration import SchemaManager
from mishkan.persistence.sqlite import (
    ArtifactCollectionRow,
    ArtifactGCPlanRow,
    ArtifactHoldRow,
    ArtifactPinRow,
    ArtifactReconciliationPlanRow,
    ArtifactReferenceRow,
    ArtifactRow,
    ArtifactUploadRow,
    BrowserActionRow,
    BrowserObservationRow,
    ChangeOperationRow,
    ChangeSetRow,
    ExecutionSessionRow,
    McpCallRow,
    McpProgressRow,
    ResultRow,
    create_local_engine,
)


class ArtifactContentInspector(Protocol):
    def require_safe_file(
        self,
        path: Path,
        resolved_secrets: tuple[str, ...] = (),
    ) -> None: ...


class DurableArtifactService:
    """Keep authoritative metadata transactional and bodies immutable in local CAS."""

    def __init__(
        self,
        database: Path,
        root: Path,
        *,
        max_artifact_bytes: int,
        max_chunk_bytes: int,
        busy_timeout_ms: int = 5_000,
        staging_ttl_seconds: int | None = None,
        content_inspector: ArtifactContentInspector | None = None,
    ) -> None:
        SchemaManager(database).require_current()
        self._root = root.resolve()
        self._blobs = self._root / "blobs"
        self._staging = self._root / "staging"
        self._legacy_manifests = self._root / "manifests"
        self._max_artifact_bytes = max_artifact_bytes
        self._max_chunk_bytes = max_chunk_bytes
        self._staging_ttl_seconds = staging_ttl_seconds
        self._content_inspector = content_inspector
        self._cas_locks = tuple(threading.RLock() for _ in range(64))
        for directory in (self._blobs, self._staging):
            directory.mkdir(parents=True, exist_ok=True)
        self._engine = create_local_engine(database, busy_timeout_ms=busy_timeout_ms)

    @property
    def max_artifact_bytes(self) -> int:
        return self._max_artifact_bytes

    def open_upload(
        self,
        *,
        expected_size: int,
        expected_digest: str,
        media_type: str,
        provenance: ArtifactProvenance,
        sensitivity: str = "internal",
        retention: str = "run",
    ) -> UploadSession:
        self._validate_digest(expected_digest)
        self._validate_provenance(provenance)
        if expected_size < 0 or expected_size > self._max_artifact_bytes:
            raise MishkanError(
                ErrorCode.ARTIFACT,
                "artifact upload size is outside the configured bound",
                details={"size_bytes": expected_size, "limit": self._max_artifact_bytes},
            )
        upload_id = new_id()
        staged = self._staging / f"{upload_id}.upload"
        descriptor = os.open(staged, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.close(descriptor)
        now = utc_now()
        metadata = {
            "provenance": provenance.model_dump(mode="json"),
            "sensitivity": sensitivity,
            "retention": retention,
        }
        try:
            with Session(self._engine) as session, session.begin():
                session.add(
                    ArtifactUploadRow(
                        id=str(upload_id),
                        expected_digest=expected_digest,
                        expected_size=expected_size,
                        media_type=media_type,
                        offset=0,
                        state="staging",
                        artifact_id=None,
                        staging_path=staged.name,
                        metadata_payload=json.dumps(metadata, sort_keys=True),
                        created_at=now.isoformat(),
                        updated_at=now.isoformat(),
                    )
                )
        except Exception:
            staged.unlink(missing_ok=True)
            raise
        return UploadSession(
            upload_id=upload_id,
            expected_size=expected_size,
            expected_digest=expected_digest,
            media_type=media_type,
            offset=0,
            lifecycle="staging",
            created_at=now,
        )

    def put_bytes(
        self,
        content: bytes,
        *,
        media_type: str,
        provenance: ArtifactProvenance,
        complete: bool,
        sensitivity: str = "internal",
        retention: str = "run",
        resolved_secrets: tuple[str, ...] = (),
    ) -> ArtifactManifest:
        if not complete:
            raise MishkanError(
                ErrorCode.ARTIFACT,
                "durable artifact publication requires complete content",
            )
        upload = self.open_upload(
            expected_size=len(content),
            expected_digest=self._digest(content),
            media_type=media_type,
            provenance=provenance,
            sensitivity=sensitivity,
            retention=retention,
        )
        for offset in range(0, len(content), self._max_chunk_bytes):
            self.append_chunk(
                upload.upload_id,
                offset=offset,
                content=content[offset : offset + self._max_chunk_bytes],
            )
        return self.commit_upload(upload.upload_id, resolved_secrets=resolved_secrets)

    def append_chunk(self, upload_id: UUID, *, offset: int, content: bytes) -> UploadSession:
        if not content or len(content) > self._max_chunk_bytes:
            raise MishkanError(
                ErrorCode.ARTIFACT,
                "artifact chunk is empty or exceeds the configured backpressure bound",
                details={"size_bytes": len(content), "limit": self._max_chunk_bytes},
            )
        with Session(self._engine) as session, session.begin():
            row = self._require_upload(session, upload_id)
            if row.state != "staging" or offset != row.offset:
                raise MishkanError(
                    ErrorCode.REVISION_MISMATCH,
                    "artifact chunk offset does not match the durable upload cursor",
                    details={"expected_offset": row.offset, "received_offset": offset},
                )
            if row.offset + len(content) > row.expected_size:
                raise MishkanError(ErrorCode.ARTIFACT, "artifact chunk exceeds expected size")
            staged = self._staging_path(row)
            try:
                descriptor = os.open(staged, os.O_WRONLY | os.O_APPEND | os.O_NOFOLLOW)
            except OSError as exc:
                row.state = "uncertain"
                raise MishkanError(
                    ErrorCode.ARTIFACT,
                    "artifact staging body could not be opened without following links",
                ) from exc
            try:
                metadata = os.fstat(descriptor)
                if not stat.S_ISREG(metadata.st_mode) or metadata.st_size != row.offset:
                    row.state = "uncertain"
                    raise MishkanError(
                        ErrorCode.ARTIFACT,
                        "artifact staging body differs from its durable cursor",
                        details={
                            "durable_offset": row.offset,
                            "observed_size": metadata.st_size,
                        },
                    )
                written = os.write(descriptor, content)
                if written != len(content):
                    raise OSError("short artifact chunk write")
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            row.offset += len(content)
            row.updated_at = utc_now().isoformat()
            return self._upload_model(row)

    def commit_upload(
        self,
        upload_id: UUID,
        *,
        resolved_secrets: tuple[str, ...] = (),
    ) -> ArtifactManifest:
        with Session(self._engine) as session:
            row = self._require_upload(session, upload_id)
            storage_ref = f"sha256/{row.expected_digest[7:9]}/{row.expected_digest[9:]}"
        with self._cas_lock(storage_ref):
            return self._commit_upload_locked(upload_id, resolved_secrets=resolved_secrets)

    def _commit_upload_locked(
        self,
        upload_id: UUID,
        *,
        resolved_secrets: tuple[str, ...],
    ) -> ArtifactManifest:
        with Session(self._engine) as session:
            row = self._require_upload(session, upload_id)
            if row.state == "committed" and row.artifact_id is not None:
                artifact = session.get(ArtifactRow, row.artifact_id)
                if artifact is not None:
                    return ArtifactManifest.model_validate_json(artifact.manifest_payload)
            if row.state != "staging":
                raise MishkanError(ErrorCode.ARTIFACT, "artifact upload is not committable")
            staged = self._staging_path(row)
            content_digest = row.expected_digest.removeprefix("sha256:")
            size = row.expected_size
            media_type = row.media_type
            metadata_payload = row.metadata_payload
            staging_path = row.staging_path
        expected_digest = f"sha256:{content_digest}"
        storage_ref = f"sha256/{content_digest[:2]}/{content_digest[2:]}"
        destination = self._safe_blob(storage_ref)
        destination.parent.mkdir(parents=True, exist_ok=True)
        try:
            if os.path.lexists(staged):
                self._commit_staged_blob(
                    staged,
                    destination,
                    content_digest,
                    size,
                    self._content_inspector,
                    resolved_secrets,
                )
            else:
                self._require_existing_committed_blob(destination, content_digest, size)
        except MishkanError as exc:
            if exc.envelope.code is ErrorCode.SECRET_CONTENT:
                self._reject_secret_upload(upload_id, staged)
            raise

        with Session(self._engine) as session, session.begin():
            row = self._require_upload(session, upload_id)
            if row.state == "committed" and row.artifact_id is not None:
                artifact = session.get(ArtifactRow, row.artifact_id)
                if artifact is not None:
                    return ArtifactManifest.model_validate_json(artifact.manifest_payload)
            if (
                row.state != "staging"
                or row.expected_digest != expected_digest
                or row.expected_size != size
                or row.media_type != media_type
                or row.metadata_payload != metadata_payload
                or row.staging_path != staging_path
            ):
                raise MishkanError(
                    ErrorCode.REVISION_MISMATCH,
                    "artifact upload changed while content was being verified",
                )
            metadata = json.loads(metadata_payload)
            manifest = ArtifactManifest(
                digest=expected_digest,
                size_bytes=size,
                declared_media_type=media_type,
                detected_media_type=None,
                provenance=ArtifactProvenance.model_validate(metadata["provenance"]),
                sensitivity=str(metadata["sensitivity"]),
                retention=str(metadata["retention"]),
                validation=ArtifactValidation.INTEGRITY_VERIFIED,
                lifecycle=ArtifactLifecycle.AVAILABLE,
                storage_ref=storage_ref,
                facts=ArtifactFacts(
                    integrity=ArtifactFactState.PASSED,
                    security_scan=(
                        ArtifactFactState.PASSED
                        if self._content_inspector is not None
                        else ArtifactFactState.NOT_EVALUATED
                    ),
                    sensitivity=str(metadata["sensitivity"]),
                    availability=ArtifactAvailability.AVAILABLE,
                ),
            )
            session.add(
                ArtifactRow(
                    id=str(manifest.id),
                    digest=manifest.digest,
                    size_bytes=manifest.size_bytes,
                    media_type=manifest.declared_media_type,
                    lifecycle=manifest.lifecycle.value,
                    storage_ref=manifest.storage_ref,
                    manifest_payload=manifest.model_dump_json(),
                    created_at=manifest.created_at.isoformat(),
                    tombstoned_at=None,
                )
            )
            row.state = "committed"
            row.artifact_id = str(manifest.id)
            row.updated_at = utc_now().isoformat()
            return manifest

    def _require_existing_committed_blob(
        self,
        destination: Path,
        digest: str,
        size: int,
    ) -> None:
        try:
            observed_digest, observed_size = self._hash_file(destination)
        except OSError as exc:
            raise MishkanError(
                ErrorCode.ARTIFACT,
                "artifact staging body and committed CAS body are unavailable",
            ) from exc
        if observed_digest != digest or observed_size != size:
            raise MishkanError(
                ErrorCode.ARTIFACT,
                "artifact CAS recovery body failed size or digest verification",
            )
        if self._content_inspector is not None:
            self._content_inspector.require_safe_file(destination)

    def _reject_secret_upload(self, upload_id: UUID, staged: Path) -> None:
        with Session(self._engine) as session, session.begin():
            row = self._require_upload(session, upload_id)
            if row.state == "staging":
                row.state = "aborted"
                row.updated_at = utc_now().isoformat()
        staged.unlink(missing_ok=True)

    def manifest(self, reference: str) -> ArtifactManifest:
        artifact_id = self._reference_id(reference)
        with Session(self._engine) as session:
            row = session.get(ArtifactRow, str(artifact_id))
            if row is None:
                raise MishkanError(ErrorCode.ARTIFACT, "artifact manifest does not exist")
            return ArtifactManifest.model_validate_json(row.manifest_payload).model_copy(
                update={"lifecycle": ArtifactLifecycle(row.lifecycle)}
            )

    def read_manifest(self, reference: str) -> ArtifactManifest:
        """Expose the common immutable artifact-reader contract."""
        return self.manifest(reference)

    def list_manifests(self, *, offset: int = 0, limit: int = 100) -> tuple[ArtifactManifest, ...]:
        if offset < 0 or limit < 1 or limit > 1_000:
            raise MishkanError(ErrorCode.OUTPUT_CONTRACT, "artifact query bound is invalid")
        with Session(self._engine) as session:
            rows = session.scalars(
                select(ArtifactRow)
                .order_by(ArtifactRow.created_at, ArtifactRow.id)
                .offset(offset)
                .limit(limit)
            ).all()
            return tuple(
                ArtifactManifest.model_validate_json(row.manifest_payload).model_copy(
                    update={"lifecycle": ArtifactLifecycle(row.lifecycle)}
                )
                for row in rows
            )

    def read_bytes(self, reference: str) -> bytes:
        return b"".join(self.iter_bytes(reference, chunk_size=self._max_chunk_bytes))

    def iter_bytes(self, reference: str, *, chunk_size: int) -> Iterator[bytes]:
        """Verify and stream one immutable body through the same no-follow descriptor."""
        if chunk_size < 1 or chunk_size > self._max_chunk_bytes:
            raise MishkanError(
                ErrorCode.OUTPUT_CONTRACT,
                "artifact stream chunk exceeds the configured backpressure bound",
                details={"chunk_size": chunk_size, "maximum": self._max_chunk_bytes},
            )
        manifest = self.manifest(reference)
        if manifest.lifecycle is not ArtifactLifecycle.AVAILABLE:
            raise MishkanError(
                ErrorCode.ARTIFACT,
                "artifact body is not available",
                details={"lifecycle": manifest.lifecycle.value},
            )
        blob = self._safe_blob(manifest.storage_ref)
        try:
            descriptor = os.open(blob, os.O_RDONLY | os.O_NOFOLLOW)
        except OSError as exc:
            self._set_lifecycle(manifest.id, ArtifactLifecycle.MISSING)
            raise MishkanError(ErrorCode.ARTIFACT, "artifact body is missing") from exc
        try:
            observed = os.fstat(descriptor)
            if not stat.S_ISREG(observed.st_mode):
                self._set_lifecycle(manifest.id, ArtifactLifecycle.CORRUPT)
                raise MishkanError(ErrorCode.ARTIFACT, "artifact body is not a regular file")
            digest = hashlib.sha256()
            size = 0
            while chunk := os.read(descriptor, self._max_chunk_bytes):
                digest.update(chunk)
                size += len(chunk)
            if size != manifest.size_bytes or f"sha256:{digest.hexdigest()}" != manifest.digest:
                self._set_lifecycle(manifest.id, ArtifactLifecycle.CORRUPT)
                raise MishkanError(
                    ErrorCode.ARTIFACT, "artifact body failed integrity verification"
                )
            os.lseek(descriptor, 0, os.SEEK_SET)
            while chunk := os.read(descriptor, chunk_size):
                yield chunk
        finally:
            os.close(descriptor)

    def upload(self, upload_id: UUID) -> UploadSession:
        with Session(self._engine) as session:
            return self._upload_model(self._require_upload(session, upload_id))

    def abort_upload(self, upload_id: UUID) -> UploadSession:
        with Session(self._engine) as session, session.begin():
            row = self._require_upload(session, upload_id)
            if row.state == "committed":
                raise MishkanError(
                    ErrorCode.ARTIFACT, "committed artifact upload cannot be aborted"
                )
            row.state = "aborted"
            row.updated_at = utc_now().isoformat()
            self._staging_path(row).unlink(missing_ok=True)
            return self._upload_model(row)

    def body_path(self, reference: str) -> Path:
        manifest = self.manifest(reference)
        if manifest.lifecycle is not ArtifactLifecycle.AVAILABLE:
            raise MishkanError(
                ErrorCode.ARTIFACT,
                "artifact body is not available",
                details={"lifecycle": manifest.lifecycle.value},
            )
        blob = self._safe_blob(manifest.storage_ref)
        try:
            digest, size = self._hash_file(blob)
        except OSError as exc:
            self._set_lifecycle(manifest.id, ArtifactLifecycle.MISSING)
            raise MishkanError(ErrorCode.ARTIFACT, "artifact body is missing") from exc
        if size != manifest.size_bytes or f"sha256:{digest}" != manifest.digest:
            self._set_lifecycle(manifest.id, ArtifactLifecycle.CORRUPT)
            raise MishkanError(ErrorCode.ARTIFACT, "artifact body failed integrity verification")
        return blob

    def create_collection(self, entries: dict[str, str]) -> ArtifactCollection:
        normalized: dict[str, str] = {}
        for logical_path, reference in entries.items():
            self._validate_logical_path(logical_path)
            member = self.manifest(reference)
            if member.lifecycle is not ArtifactLifecycle.AVAILABLE:
                raise MishkanError(
                    ErrorCode.ARTIFACT,
                    "artifact collection member is unavailable",
                    details={"artifact": reference, "lifecycle": member.lifecycle.value},
                )
            normalized[logical_path] = reference
        collection = ArtifactCollection(
            collection_id=new_id(),
            entries=normalized,
            ordered_paths=tuple(normalized),
        )
        with Session(self._engine) as session, session.begin():
            session.add(
                ArtifactCollectionRow(
                    id=str(collection.collection_id),
                    entries_payload=json.dumps(normalized, separators=(",", ":")),
                    created_at=collection.created_at.isoformat(),
                )
            )
        return collection

    def collection(self, collection_id: UUID) -> ArtifactCollection:
        with Session(self._engine) as session:
            row = session.get(ArtifactCollectionRow, str(collection_id))
            if row is None:
                raise MishkanError(ErrorCode.ARTIFACT, "artifact collection does not exist")
            entries = cast(dict[str, str], json.loads(row.entries_payload))
            return ArtifactCollection(
                collection_id=UUID(row.id),
                entries=entries,
                ordered_paths=tuple(entries),
                created_at=datetime.fromisoformat(row.created_at),
            )

    def list_collections(
        self, *, offset: int = 0, limit: int = 100
    ) -> tuple[ArtifactCollection, ...]:
        self._query_bound(offset, limit)
        with Session(self._engine) as session:
            rows = session.scalars(
                select(ArtifactCollectionRow)
                .order_by(ArtifactCollectionRow.created_at, ArtifactCollectionRow.id)
                .offset(offset)
                .limit(limit)
            ).all()
            return tuple(self.collection(UUID(row.id)) for row in rows)

    def list_references(self, *, offset: int = 0, limit: int = 100) -> tuple[WorkingReference, ...]:
        self._query_bound(offset, limit)
        with Session(self._engine) as session:
            rows = session.scalars(
                select(ArtifactReferenceRow)
                .order_by(ArtifactReferenceRow.scope, ArtifactReferenceRow.name)
                .offset(offset)
                .limit(limit)
            ).all()
            return tuple(
                WorkingReference(
                    id=UUID(row.record_id),
                    scope=row.scope,
                    name=row.name,
                    artifact_reference=f"artifact:{row.artifact_id}",
                    revision=row.revision,
                    prior_artifact_reference=(
                        f"artifact:{row.prior_artifact_id}" if row.prior_artifact_id else None
                    ),
                    prior_revision=row.prior_revision,
                    updated_at=datetime.fromisoformat(row.updated_at),
                )
                for row in rows
            )

    def reference(self, scope: str, name: str) -> WorkingReference | None:
        with Session(self._engine) as session:
            row = session.get(ArtifactReferenceRow, (scope, name))
            if row is None:
                return None
            return WorkingReference(
                id=UUID(row.record_id),
                scope=row.scope,
                name=row.name,
                artifact_reference=f"artifact:{row.artifact_id}",
                revision=row.revision,
                prior_artifact_reference=(
                    f"artifact:{row.prior_artifact_id}" if row.prior_artifact_id else None
                ),
                prior_revision=row.prior_revision,
                updated_at=datetime.fromisoformat(row.updated_at),
            )

    def update_reference(
        self,
        scope: str,
        name: str,
        artifact_reference: str,
        *,
        expected_revision: int,
    ) -> WorkingReference:
        artifact_id = self._reference_id(artifact_reference)
        WorkingReference(
            scope=scope,
            name=name,
            artifact_reference=artifact_reference,
            revision=max(expected_revision + 1, 1),
        )
        with Session(self._engine) as session, session.begin():
            artifact = session.get(ArtifactRow, str(artifact_id))
            if artifact is None or artifact.lifecycle != ArtifactLifecycle.AVAILABLE.value:
                raise MishkanError(ErrorCode.ARTIFACT, "working reference target is unavailable")
            row = session.get(ArtifactReferenceRow, (scope, name))
            current = row.revision if row is not None else 0
            if current != expected_revision:
                raise MishkanError(
                    ErrorCode.REVISION_MISMATCH,
                    "working reference compare-and-swap revision differs",
                    details={"expected": expected_revision, "current": current},
                )
            now = utc_now()
            if row is None:
                row = ArtifactReferenceRow(
                    scope=scope,
                    name=name,
                    record_id=str(new_id()),
                    artifact_id=str(artifact_id),
                    revision=1,
                    prior_artifact_id=None,
                    prior_revision=None,
                    updated_at=now.isoformat(),
                )
                session.add(row)
            else:
                row.prior_artifact_id = row.artifact_id
                row.prior_revision = row.revision
                row.artifact_id = str(artifact_id)
                row.revision += 1
                row.updated_at = now.isoformat()
            session.flush()
            return WorkingReference(
                id=UUID(row.record_id),
                scope=scope,
                name=name,
                artifact_reference=artifact_reference,
                revision=row.revision,
                prior_artifact_reference=(
                    f"artifact:{row.prior_artifact_id}" if row.prior_artifact_id else None
                ),
                prior_revision=row.prior_revision,
                updated_at=now,
            )

    def hold(self, reference: str, reason: str) -> ArtifactHold:
        artifact_id = self._reference_id(reference)
        with Session(self._engine) as session, session.begin():
            if session.get(ArtifactRow, str(artifact_id)) is None:
                raise MishkanError(ErrorCode.ARTIFACT, "artifact hold target does not exist")
            existing = session.get(ArtifactHoldRow, str(artifact_id))
            if existing is not None:
                if existing.reason != reason:
                    raise MishkanError(
                        ErrorCode.REVISION_MISMATCH,
                        "artifact already has a hold with a different reason",
                    )
                return self._hold_model(existing)
            row = ArtifactHoldRow(
                artifact_id=str(artifact_id),
                record_id=str(new_id()),
                reason=reason,
                created_at=utc_now().isoformat(),
            )
            session.add(row)
            session.flush()
            return self._hold_model(row)

    def release_hold(self, reference: str) -> ArtifactHold:
        artifact_id = self._reference_id(reference)
        with Session(self._engine) as session, session.begin():
            row = session.get(ArtifactHoldRow, str(artifact_id))
            if row is None:
                raise MishkanError(ErrorCode.ARTIFACT, "artifact hold does not exist")
            result = self._hold_model(row)
            session.delete(row)
            return result

    def list_holds(self, *, offset: int = 0, limit: int = 100) -> tuple[ArtifactHold, ...]:
        self._query_bound(offset, limit)
        with Session(self._engine) as session:
            rows = session.scalars(
                select(ArtifactHoldRow)
                .order_by(ArtifactHoldRow.created_at, ArtifactHoldRow.artifact_id)
                .offset(offset)
                .limit(limit)
            ).all()
            return tuple(self._hold_model(row) for row in rows)

    def pin(self, reference: str) -> ArtifactPin:
        artifact_id = self._reference_id(reference)
        with Session(self._engine) as session, session.begin():
            if session.get(ArtifactRow, str(artifact_id)) is None:
                raise MishkanError(ErrorCode.ARTIFACT, "artifact pin target does not exist")
            row = session.get(ArtifactPinRow, str(artifact_id))
            if row is None:
                row = ArtifactPinRow(
                    artifact_id=str(artifact_id),
                    record_id=str(new_id()),
                    created_at=utc_now().isoformat(),
                )
                session.add(row)
                session.flush()
            return self._pin_model(row)

    def release_pin(self, reference: str) -> ArtifactPin:
        artifact_id = self._reference_id(reference)
        with Session(self._engine) as session, session.begin():
            row = session.get(ArtifactPinRow, str(artifact_id))
            if row is None:
                raise MishkanError(ErrorCode.ARTIFACT, "artifact pin does not exist")
            result = self._pin_model(row)
            session.delete(row)
            return result

    def list_pins(self, *, offset: int = 0, limit: int = 100) -> tuple[ArtifactPin, ...]:
        self._query_bound(offset, limit)
        with Session(self._engine) as session:
            rows = session.scalars(
                select(ArtifactPinRow)
                .order_by(ArtifactPinRow.created_at, ArtifactPinRow.artifact_id)
                .offset(offset)
                .limit(limit)
            ).all()
            return tuple(self._pin_model(row) for row in rows)

    def plan_gc(self, *, watermark: datetime) -> GarbageCollectionPlan:
        with Session(self._engine) as session, session.begin():
            rooted = self._rooted_ids(session)
            rows = session.scalars(
                select(ArtifactRow).where(
                    ArtifactRow.lifecycle == ArtifactLifecycle.AVAILABLE.value,
                    ArtifactRow.created_at < watermark.isoformat(),
                )
            ).all()
            candidates = tuple(f"artifact:{row.id}" for row in rows if row.id not in rooted)
            plan = GarbageCollectionPlan(
                plan_id=new_id(), candidates=candidates, watermark=watermark
            )
            session.add(
                ArtifactGCPlanRow(
                    id=str(plan.plan_id),
                    watermark=watermark.isoformat(),
                    candidates_payload=json.dumps(candidates),
                    applied_at=None,
                    created_at=utc_now().isoformat(),
                )
            )
            return plan

    def apply_gc(self, plan_id: UUID) -> GarbageCollectionPlan:
        blobs: set[str] = set()
        with Session(self._engine) as session, session.begin():
            plan_row = session.get(ArtifactGCPlanRow, str(plan_id))
            if plan_row is None:
                raise MishkanError(ErrorCode.ARTIFACT, "garbage collection plan does not exist")
            candidates = tuple(json.loads(plan_row.candidates_payload))
            watermark = datetime.fromisoformat(plan_row.watermark)
            if plan_row.applied_at is not None:
                return GarbageCollectionPlan(
                    plan_id=plan_id,
                    candidates=candidates,
                    watermark=watermark,
                    applied=True,
                )
            rooted = self._rooted_ids(session)
            for reference in candidates:
                artifact_id = str(self._reference_id(reference))
                if artifact_id in rooted:
                    raise MishkanError(
                        ErrorCode.REVISION_MISMATCH,
                        "garbage collection roots changed after planning",
                        details={"artifact": reference},
                    )
                row = session.get(ArtifactRow, artifact_id)
                if row is not None and row.lifecycle == ArtifactLifecycle.AVAILABLE.value:
                    row.tombstoned_at = utc_now().isoformat()
                    self._set_row_lifecycle(row, ArtifactLifecycle.TOMBSTONED)
                    blobs.add(row.storage_ref)
            plan_row.applied_at = utc_now().isoformat()
        for storage_ref in blobs:
            with self._cas_lock(storage_ref):
                with Session(self._engine) as session:
                    live = session.scalar(
                        select(ArtifactRow.id).where(
                            ArtifactRow.storage_ref == storage_ref,
                            ArtifactRow.lifecycle.not_in(
                                (
                                    ArtifactLifecycle.TOMBSTONED.value,
                                    ArtifactLifecycle.DELETED.value,
                                    ArtifactLifecycle.MISSING.value,
                                    ArtifactLifecycle.CORRUPT.value,
                                )
                            ),
                        )
                    )
                if live is None:
                    self._safe_blob(storage_ref).unlink(missing_ok=True)
                    with Session(self._engine) as session, session.begin():
                        rows = session.scalars(
                            select(ArtifactRow).where(
                                ArtifactRow.storage_ref == storage_ref,
                                ArtifactRow.lifecycle == ArtifactLifecycle.TOMBSTONED.value,
                            )
                        ).all()
                        for row in rows:
                            self._set_row_lifecycle(row, ArtifactLifecycle.DELETED)
        return GarbageCollectionPlan(
            plan_id=plan_id,
            candidates=candidates,
            watermark=watermark,
            applied=True,
        )

    def plan_reconciliation(self) -> ArtifactReconciliationPlan:
        issues: list[ArtifactReconciliationIssue] = []
        planned_at = utc_now()
        with Session(self._engine) as session, session.begin():
            if self._staging_ttl_seconds is not None:
                cutoff = planned_at - timedelta(seconds=self._staging_ttl_seconds)
                expired_uploads = session.scalars(
                    select(ArtifactUploadRow).where(
                        ArtifactUploadRow.state.in_(("staging", "aborted")),
                        ArtifactUploadRow.updated_at < cutoff.isoformat(),
                    )
                ).all()
                for upload in expired_uploads:
                    issues.append(
                        ArtifactReconciliationIssue(
                            action=ArtifactReconciliationAction.ABORT_EXPIRED_UPLOAD,
                            upload_id=UUID(upload.id),
                        )
                    )
            all_rows = session.scalars(
                select(ArtifactRow).where(
                    ArtifactRow.lifecycle.not_in(
                        (ArtifactLifecycle.TOMBSTONED.value, ArtifactLifecycle.DELETED.value)
                    )
                )
            ).all()
            live_storage = {row.storage_ref for row in all_rows}
            rows = [
                row
                for row in all_rows
                if row.lifecycle
                in {
                    ArtifactLifecycle.AVAILABLE.value,
                    ArtifactLifecycle.VALIDATING.value,
                    ArtifactLifecycle.QUARANTINED.value,
                }
            ]
            unavailable_ids: set[str] = set()
            for row in rows:
                blob = self._safe_blob(row.storage_ref)
                try:
                    digest, size = self._hash_file(blob)
                except OSError:
                    unavailable_ids.add(row.id)
                    issues.append(
                        ArtifactReconciliationIssue(
                            action=ArtifactReconciliationAction.MARK_MISSING,
                            artifact_reference=f"artifact:{row.id}",
                            storage_ref=row.storage_ref,
                        )
                    )
                    continue
                if size != row.size_bytes or f"sha256:{digest}" != row.digest:
                    unavailable_ids.add(row.id)
                    issues.append(
                        ArtifactReconciliationIssue(
                            action=ArtifactReconciliationAction.MARK_CORRUPT,
                            artifact_reference=f"artifact:{row.id}",
                            storage_ref=row.storage_ref,
                        )
                    )
            for blob in sorted(self._blobs.glob("sha256/[0-9a-f][0-9a-f]/*")):
                if blob.is_symlink() or not blob.is_file():
                    continue
                storage_ref = blob.relative_to(self._blobs).as_posix()
                if storage_ref not in live_storage:
                    issues.append(
                        ArtifactReconciliationIssue(
                            action=ArtifactReconciliationAction.DELETE_ORPHAN_BLOB,
                            storage_ref=storage_ref,
                        )
                    )
            for reference in session.scalars(select(ArtifactReferenceRow)).all():
                target = session.get(ArtifactRow, reference.artifact_id)
                if (
                    target is None
                    or target.id in unavailable_ids
                    or target.lifecycle != ArtifactLifecycle.AVAILABLE.value
                ):
                    issues.append(
                        ArtifactReconciliationIssue(
                            action=ArtifactReconciliationAction.DELETE_INVALID_REFERENCE,
                            scope=reference.scope,
                            name=reference.name,
                        )
                    )
            for collection in session.scalars(select(ArtifactCollectionRow)).all():
                if self._collection_is_incomplete(session, collection, unavailable_ids):
                    issues.append(
                        ArtifactReconciliationIssue(
                            action=ArtifactReconciliationAction.DELETE_INCOMPLETE_COLLECTION,
                            collection_id=UUID(collection.id),
                        )
                    )
            ordered = tuple(sorted(issues, key=lambda item: item.model_dump_json()))
            plan = ArtifactReconciliationPlan(
                plan_id=new_id(),
                issues=ordered,
                created_at=planned_at,
            )
            session.add(
                ArtifactReconciliationPlanRow(
                    id=str(plan.plan_id),
                    payload=plan.model_dump_json(),
                    applied_at=None,
                    created_at=plan.created_at.isoformat(),
                )
            )
            return plan

    def apply_reconciliation(self, plan_id: UUID) -> ArtifactReconciliationPlan:
        with Session(self._engine) as session:
            row = session.get(ArtifactReconciliationPlanRow, str(plan_id))
            if row is None:
                raise MishkanError(ErrorCode.ARTIFACT, "artifact reconciliation plan is absent")
            plan = ArtifactReconciliationPlan.model_validate_json(row.payload)
            if row.applied_at is not None:
                return plan.model_copy(update={"applied": True})
            self._validate_reconciliation_plan(session, plan)

        for issue in plan.issues:
            if (
                issue.action is ArtifactReconciliationAction.DELETE_ORPHAN_BLOB
                and issue.storage_ref is not None
            ):
                with self._cas_lock(issue.storage_ref):
                    with Session(self._engine) as session:
                        live = session.scalar(
                            select(ArtifactRow.id).where(
                                ArtifactRow.storage_ref == issue.storage_ref,
                                ArtifactRow.lifecycle.not_in(
                                    (
                                        ArtifactLifecycle.TOMBSTONED.value,
                                        ArtifactLifecycle.DELETED.value,
                                        ArtifactLifecycle.MISSING.value,
                                        ArtifactLifecycle.CORRUPT.value,
                                    )
                                ),
                            )
                        )
                    if live is None:
                        self._safe_blob(issue.storage_ref).unlink(missing_ok=True)

        expired_staging: list[Path] = []
        with Session(self._engine) as session, session.begin():
            row = session.get(ArtifactReconciliationPlanRow, str(plan_id))
            if row is None:
                raise MishkanError(ErrorCode.ARTIFACT, "artifact reconciliation plan is absent")
            if row.applied_at is not None:
                return plan.model_copy(update={"applied": True})
            for issue in plan.issues:
                if issue.action in {
                    ArtifactReconciliationAction.MARK_MISSING,
                    ArtifactReconciliationAction.MARK_CORRUPT,
                }:
                    assert issue.artifact_reference is not None
                    artifact = session.get(
                        ArtifactRow, str(self._reference_id(issue.artifact_reference))
                    )
                    assert artifact is not None
                    lifecycle = (
                        ArtifactLifecycle.MISSING
                        if issue.action is ArtifactReconciliationAction.MARK_MISSING
                        else ArtifactLifecycle.CORRUPT
                    )
                    self._set_row_lifecycle(artifact, lifecycle)
                elif issue.action is ArtifactReconciliationAction.DELETE_INVALID_REFERENCE:
                    assert issue.scope is not None and issue.name is not None
                    session.execute(
                        delete(ArtifactReferenceRow).where(
                            ArtifactReferenceRow.scope == issue.scope,
                            ArtifactReferenceRow.name == issue.name,
                        )
                    )
                elif issue.action is ArtifactReconciliationAction.DELETE_INCOMPLETE_COLLECTION:
                    assert issue.collection_id is not None
                    session.execute(
                        delete(ArtifactCollectionRow).where(
                            ArtifactCollectionRow.id == str(issue.collection_id)
                        )
                    )
                elif issue.action is ArtifactReconciliationAction.ABORT_EXPIRED_UPLOAD:
                    assert issue.upload_id is not None
                    upload = session.get(ArtifactUploadRow, str(issue.upload_id))
                    assert upload is not None
                    upload.state = "aborted"
                    expired_staging.append(self._staging_path(upload))
        for staging_path in expired_staging:
            staging_path.unlink(missing_ok=True)
        with Session(self._engine) as session, session.begin():
            row = session.get(ArtifactReconciliationPlanRow, str(plan_id))
            if row is None:
                raise MishkanError(ErrorCode.ARTIFACT, "artifact reconciliation plan is absent")
            row.applied_at = utc_now().isoformat()
        return plan.model_copy(update={"applied": True})

    def _validate_reconciliation_plan(
        self, session: Session, plan: ArtifactReconciliationPlan
    ) -> None:
        planned_unavailable = {
            str(self._reference_id(issue.artifact_reference))
            for issue in plan.issues
            if issue.action
            in {
                ArtifactReconciliationAction.MARK_MISSING,
                ArtifactReconciliationAction.MARK_CORRUPT,
            }
            and issue.artifact_reference is not None
        }
        for issue in plan.issues:
            if issue.action in {
                ArtifactReconciliationAction.MARK_MISSING,
                ArtifactReconciliationAction.MARK_CORRUPT,
            }:
                if issue.artifact_reference is None or issue.storage_ref is None:
                    raise MishkanError(ErrorCode.ARTIFACT, "reconciliation issue is incomplete")
                row = session.get(ArtifactRow, str(self._reference_id(issue.artifact_reference)))
                if row is None or row.storage_ref != issue.storage_ref:
                    raise MishkanError(
                        ErrorCode.REVISION_MISMATCH, "artifact changed after planning"
                    )
                blob = self._safe_blob(issue.storage_ref)
                if issue.action is ArtifactReconciliationAction.MARK_MISSING:
                    if blob.exists():
                        raise MishkanError(
                            ErrorCode.REVISION_MISMATCH,
                            "missing artifact reappeared after planning",
                        )
                else:
                    try:
                        digest, size = self._hash_file(blob)
                    except OSError as exc:
                        raise MishkanError(
                            ErrorCode.REVISION_MISMATCH,
                            "corrupt artifact became missing after planning",
                        ) from exc
                    if size == row.size_bytes and f"sha256:{digest}" == row.digest:
                        raise MishkanError(
                            ErrorCode.REVISION_MISMATCH,
                            "corrupt artifact was repaired after planning",
                        )
            elif issue.action is ArtifactReconciliationAction.DELETE_ORPHAN_BLOB:
                if issue.storage_ref is None:
                    raise MishkanError(ErrorCode.ARTIFACT, "orphan issue has no storage ref")
                live = session.scalar(
                    select(ArtifactRow.id).where(
                        ArtifactRow.storage_ref == issue.storage_ref,
                        ArtifactRow.lifecycle.not_in(
                            (ArtifactLifecycle.TOMBSTONED.value, ArtifactLifecycle.DELETED.value)
                        ),
                    )
                )
                if live is not None:
                    raise MishkanError(
                        ErrorCode.REVISION_MISMATCH, "orphan blob became referenced after planning"
                    )
            elif issue.action is ArtifactReconciliationAction.DELETE_INVALID_REFERENCE:
                if issue.scope is None or issue.name is None:
                    raise MishkanError(ErrorCode.ARTIFACT, "reference issue is incomplete")
                reference = session.get(ArtifactReferenceRow, (issue.scope, issue.name))
                if reference is None:
                    continue
                target = session.get(ArtifactRow, reference.artifact_id)
                if (
                    target is not None
                    and target.id not in planned_unavailable
                    and target.lifecycle == ArtifactLifecycle.AVAILABLE.value
                ):
                    raise MishkanError(
                        ErrorCode.REVISION_MISMATCH, "working reference was repaired after planning"
                    )
            elif issue.action is ArtifactReconciliationAction.DELETE_INCOMPLETE_COLLECTION:
                if issue.collection_id is None:
                    raise MishkanError(ErrorCode.ARTIFACT, "collection issue is incomplete")
                collection = session.get(ArtifactCollectionRow, str(issue.collection_id))
                if collection is not None and not self._collection_is_incomplete(
                    session, collection, planned_unavailable
                ):
                    raise MishkanError(
                        ErrorCode.REVISION_MISMATCH, "collection was repaired after planning"
                    )
            elif issue.action is ArtifactReconciliationAction.ABORT_EXPIRED_UPLOAD:
                if issue.upload_id is None or self._staging_ttl_seconds is None:
                    raise MishkanError(ErrorCode.ARTIFACT, "expired upload issue is incomplete")
                upload = session.get(ArtifactUploadRow, str(issue.upload_id))
                if upload is None or upload.state not in {"staging", "aborted"}:
                    raise MishkanError(
                        ErrorCode.REVISION_MISMATCH,
                        "artifact upload changed after expiration planning",
                    )
                cutoff = plan.created_at - timedelta(seconds=self._staging_ttl_seconds)
                if datetime.fromisoformat(upload.updated_at) >= cutoff:
                    raise MishkanError(
                        ErrorCode.REVISION_MISMATCH,
                        "artifact upload was refreshed after expiration planning",
                    )

    def _collection_is_incomplete(
        self,
        session: Session,
        collection: ArtifactCollectionRow,
        unavailable_ids: set[str] | None = None,
    ) -> bool:
        unavailable = unavailable_ids or set()
        try:
            entries = json.loads(collection.entries_payload)
            if not isinstance(entries, dict):
                return True
            for logical_path, reference in entries.items():
                if not isinstance(logical_path, str) or not isinstance(reference, str):
                    return True
                self._validate_logical_path(logical_path)
                target = session.get(ArtifactRow, str(self._reference_id(reference)))
                if (
                    target is None
                    or target.id in unavailable
                    or target.lifecycle != ArtifactLifecycle.AVAILABLE.value
                ):
                    return True
        except (MishkanError, ValueError, TypeError):
            return True
        return False

    def import_legacy_manifests(self) -> int:
        """Import I02 JSON manifests and classify unavailable bodies without hiding them."""
        if not self._legacy_manifests.is_dir():
            return 0
        imported = 0
        for path in sorted(self._legacy_manifests.glob("*.json")):
            try:
                manifest = ArtifactManifest.model_validate_json(path.read_text(encoding="utf-8"))
            except (OSError, ValidationError):
                continue
            blob = self._safe_blob(manifest.storage_ref)
            lifecycle = manifest.lifecycle
            if not blob.is_file():
                lifecycle = ArtifactLifecycle.MISSING
            else:
                digest, size = self._hash_file(blob)
                if size != manifest.size_bytes or f"sha256:{digest}" != manifest.digest:
                    lifecycle = ArtifactLifecycle.CORRUPT
            imported_manifest = manifest.model_copy(update={"lifecycle": lifecycle})
            with Session(self._engine) as session, session.begin():
                if session.get(ArtifactRow, str(manifest.id)) is not None:
                    continue
                session.add(
                    ArtifactRow(
                        id=str(manifest.id),
                        digest=manifest.digest,
                        size_bytes=manifest.size_bytes,
                        media_type=manifest.declared_media_type,
                        lifecycle=lifecycle.value,
                        storage_ref=manifest.storage_ref,
                        manifest_payload=imported_manifest.model_dump_json(),
                        created_at=manifest.created_at.isoformat(),
                        tombstoned_at=None,
                    )
                )
                imported += 1
        return imported

    def _set_lifecycle(self, artifact_id: UUID, lifecycle: ArtifactLifecycle) -> None:
        with Session(self._engine) as session, session.begin():
            row = session.get(ArtifactRow, str(artifact_id))
            if row is not None:
                self._set_row_lifecycle(row, lifecycle)

    @staticmethod
    def _set_row_lifecycle(row: ArtifactRow, lifecycle: ArtifactLifecycle) -> None:
        row.lifecycle = lifecycle.value
        manifest = ArtifactManifest.model_validate_json(row.manifest_payload)
        facts = manifest.facts.model_copy(
            update={
                "availability": (
                    ArtifactAvailability.AVAILABLE
                    if lifecycle
                    in {
                        ArtifactLifecycle.AVAILABLE,
                        ArtifactLifecycle.QUARANTINED,
                        ArtifactLifecycle.REJECTED,
                        ArtifactLifecycle.EXPIRED,
                    }
                    else ArtifactAvailability.UNAVAILABLE
                ),
                "integrity": (
                    ArtifactFactState.FAILED
                    if lifecycle in {ArtifactLifecycle.MISSING, ArtifactLifecycle.CORRUPT}
                    else manifest.facts.integrity
                ),
                "trust": (
                    ArtifactTrust.QUARANTINED
                    if lifecycle is ArtifactLifecycle.QUARANTINED
                    else manifest.facts.trust
                ),
            }
        )
        row.manifest_payload = manifest.model_copy(
            update={"lifecycle": lifecycle, "facts": facts}
        ).model_dump_json()

    def _staging_path(self, row: ArtifactUploadRow) -> Path:
        expected_name = f"{row.id}.upload"
        if (
            row.staging_path != expected_name
            or PurePosixPath(row.staging_path).name != expected_name
        ):
            raise MishkanError(ErrorCode.ARTIFACT, "artifact staging path escaped its root")
        return self._staging / expected_name

    def _safe_blob(self, storage_ref: str) -> Path:
        path = (self._blobs / storage_ref).resolve()
        if not path.is_relative_to(self._blobs):
            raise MishkanError(ErrorCode.ARTIFACT, "artifact storage path escaped its root")
        return path

    @staticmethod
    def _require_upload(session: Session, upload_id: UUID) -> ArtifactUploadRow:
        row = session.get(ArtifactUploadRow, str(upload_id))
        if row is None:
            raise MishkanError(ErrorCode.ARTIFACT, "artifact upload session does not exist")
        return row

    @staticmethod
    def _upload_model(row: ArtifactUploadRow) -> UploadSession:
        observed = row.state if row.state in {"staging", "committed", "aborted"} else "aborted"
        lifecycle = cast(Literal["staging", "committed", "aborted"], observed)
        return UploadSession(
            upload_id=UUID(row.id),
            expected_size=row.expected_size,
            expected_digest=row.expected_digest,
            media_type=row.media_type,
            offset=row.offset,
            lifecycle=lifecycle,
            created_at=datetime.fromisoformat(row.created_at),
        )

    def _cas_lock(self, storage_ref: str) -> threading.RLock:
        digest = hashlib.sha256(storage_ref.encode()).digest()
        return self._cas_locks[int.from_bytes(digest[:2], "big") % len(self._cas_locks)]

    @staticmethod
    def _commit_staged_blob(
        staged: Path,
        destination: Path,
        digest: str,
        size: int,
        inspector: ArtifactContentInspector | None,
        resolved_secrets: tuple[str, ...],
    ) -> None:
        try:
            source_descriptor = os.open(staged, os.O_RDONLY | os.O_NOFOLLOW)
        except OSError as exc:
            raise MishkanError(
                ErrorCode.ARTIFACT,
                "artifact staging body could not be opened without following links",
            ) from exc
        source_stat = os.fstat(source_descriptor)
        if not stat.S_ISREG(source_stat.st_mode):
            os.close(source_descriptor)
            raise MishkanError(ErrorCode.ARTIFACT, "artifact staging body is not a regular file")
        temporary = destination.parent / f".{destination.name}.{new_id()}.tmp"
        try:
            target_descriptor = os.open(
                temporary,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o600,
            )
        except Exception:
            os.close(source_descriptor)
            raise
        observed_digest = hashlib.sha256()
        observed_size = 0
        try:
            while chunk := os.read(source_descriptor, 1024 * 1024):
                observed_size += len(chunk)
                observed_digest.update(chunk)
                view = memoryview(chunk)
                while view:
                    written = os.write(target_descriptor, view)
                    if written < 1:
                        raise OSError("short artifact CAS write")
                    view = view[written:]
            os.fsync(target_descriptor)
        except Exception:
            temporary.unlink(missing_ok=True)
            raise
        finally:
            os.close(target_descriptor)
            os.close(source_descriptor)
        observed_digest_value = observed_digest.hexdigest()
        if observed_digest_value != digest or observed_size != size:
            temporary.unlink(missing_ok=True)
            raise MishkanError(
                ErrorCode.ARTIFACT,
                "artifact upload failed size or digest verification",
                details={
                    "expected_size": size,
                    "observed_size": observed_size,
                    "expected_digest": f"sha256:{digest}",
                    "observed_digest": f"sha256:{observed_digest_value}",
                },
            )
        try:
            if inspector is not None:
                inspector.require_safe_file(temporary, resolved_secrets)
            try:
                os.link(temporary, destination, follow_symlinks=False)
            except FileExistsError:
                try:
                    existing_digest, existing_size = DurableArtifactService._hash_file(destination)
                except OSError as exc:
                    raise MishkanError(
                        ErrorCode.ARTIFACT,
                        "artifact CAS destination is not a readable regular file",
                    ) from exc
                if existing_digest != digest or existing_size != size:
                    raise MishkanError(
                        ErrorCode.ARTIFACT, "artifact CAS collision was detected"
                    ) from None
            descriptor = os.open(destination.parent, os.O_RDONLY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            try:
                current = os.stat(staged, follow_symlinks=False)
            except FileNotFoundError as exc:
                raise MishkanError(
                    ErrorCode.ARTIFACT,
                    "artifact staging body changed during commit",
                ) from exc
            if (
                current.st_dev != source_stat.st_dev
                or current.st_ino != source_stat.st_ino
                or not stat.S_ISREG(current.st_mode)
            ):
                raise MishkanError(
                    ErrorCode.ARTIFACT,
                    "artifact staging body changed during commit",
                )
            staged.unlink()
        finally:
            temporary.unlink(missing_ok=True)

    @staticmethod
    def _hash_file(path: Path) -> tuple[str, int]:
        digest = hashlib.sha256()
        size = 0
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(descriptor, "rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                size += len(chunk)
                digest.update(chunk)
        return digest.hexdigest(), size

    @staticmethod
    def _digest(content: bytes) -> str:
        return f"sha256:{hashlib.sha256(content).hexdigest()}"

    @staticmethod
    def _validate_digest(value: str) -> None:
        if len(value) != 71 or not value.startswith("sha256:"):
            raise MishkanError(ErrorCode.ARTIFACT, "artifact digest is invalid")
        try:
            int(value.removeprefix("sha256:"), 16)
        except ValueError as exc:
            raise MishkanError(ErrorCode.ARTIFACT, "artifact digest is invalid") from exc

    @staticmethod
    def _reference_id(reference: str) -> UUID:
        prefix, separator, value = reference.partition(":")
        if prefix != "artifact" or not separator:
            raise MishkanError(ErrorCode.ARTIFACT, "artifact reference is invalid")
        try:
            return UUID(value)
        except ValueError as exc:
            raise MishkanError(ErrorCode.ARTIFACT, "artifact reference is invalid") from exc

    @staticmethod
    def _validate_logical_path(value: str) -> None:
        path = PurePosixPath(value)
        if not value or path.is_absolute() or ".." in path.parts or "." in path.parts:
            raise MishkanError(ErrorCode.ARTIFACT, "collection logical path is unsafe")

    def _validate_provenance(self, provenance: ArtifactProvenance) -> None:
        for reference in provenance.source_artifacts:
            source = self.manifest(reference)
            if source.lifecycle is not ArtifactLifecycle.AVAILABLE:
                raise MishkanError(
                    ErrorCode.ARTIFACT,
                    "derived artifact source is unavailable",
                    details={"artifact": reference, "lifecycle": source.lifecycle.value},
                )

    @staticmethod
    def _query_bound(offset: int, limit: int) -> None:
        if offset < 0 or limit < 1 or limit > 1_000:
            raise MishkanError(ErrorCode.OUTPUT_CONTRACT, "artifact query bound is invalid")

    @staticmethod
    def _hold_model(row: ArtifactHoldRow) -> ArtifactHold:
        return ArtifactHold(
            id=UUID(row.record_id),
            artifact_reference=f"artifact:{row.artifact_id}",
            reason=row.reason,
            created_at=datetime.fromisoformat(row.created_at),
        )

    @staticmethod
    def _pin_model(row: ArtifactPinRow) -> ArtifactPin:
        return ArtifactPin(
            id=UUID(row.record_id),
            artifact_reference=f"artifact:{row.artifact_id}",
            created_at=datetime.fromisoformat(row.created_at),
        )

    @staticmethod
    def _rooted_ids(session: Session) -> set[str]:
        rooted = set(session.scalars(select(ArtifactReferenceRow.artifact_id)).all())
        rooted.update(session.scalars(select(ArtifactHoldRow.artifact_id)).all())
        rooted.update(session.scalars(select(ArtifactPinRow.artifact_id)).all())
        for payload in session.scalars(select(ArtifactCollectionRow.entries_payload)).all():
            rooted.update(
                str(DurableArtifactService._reference_id(ref))
                for ref in json.loads(payload).values()
            )
        for payload in session.scalars(select(ResultRow.payload)).all():
            rooted.update(DurableArtifactService._artifact_ids_in_payload(payload))
        for payload in session.scalars(select(BrowserObservationRow.payload)).all():
            rooted.update(DurableArtifactService._artifact_ids_in_payload(payload))
        for payload in session.scalars(select(BrowserActionRow.payload)).all():
            rooted.update(DurableArtifactService._artifact_ids_in_payload(payload))
        for row in session.scalars(select(McpCallRow)).all():
            rooted.update(DurableArtifactService._artifact_ids_in_payload(row.request_payload))
            if row.result_payload is not None:
                rooted.update(DurableArtifactService._artifact_ids_in_payload(row.result_payload))
        for payload in session.scalars(select(McpProgressRow.payload)).all():
            rooted.update(DurableArtifactService._artifact_ids_in_payload(payload))
        for execution_row in session.scalars(select(ExecutionSessionRow)).all():
            rooted.update(
                DurableArtifactService._artifact_ids_in_payload(
                    execution_row.effect_evidence_payload
                )
            )
            for reference in (
                execution_row.stdout_artifact_reference,
                execution_row.stderr_artifact_reference,
                *json.loads(execution_row.produced_artifacts_payload or "[]"),
            ):
                if reference:
                    rooted.add(str(DurableArtifactService._reference_id(reference)))
        for reference in session.scalars(select(ChangeSetRow.diff_reference)).all():
            if reference:
                rooted.add(str(DurableArtifactService._reference_id(reference)))
        for reference in session.scalars(select(ChangeOperationRow.preimage_reference)).all():
            if reference:
                rooted.add(str(DurableArtifactService._reference_id(reference)))
        provenance_edges: dict[str, tuple[str, ...]] = {}
        for artifact_row in session.scalars(select(ArtifactRow)).all():
            try:
                manifest = ArtifactManifest.model_validate_json(artifact_row.manifest_payload)
            except ValidationError:
                continue
            provenance_edges[artifact_row.id] = tuple(
                str(DurableArtifactService._reference_id(reference))
                for reference in manifest.provenance.source_artifacts
            )
        pending = list(rooted)
        while pending:
            artifact_id = pending.pop()
            for source_id in provenance_edges.get(artifact_id, ()):
                if source_id not in rooted:
                    rooted.add(source_id)
                    pending.append(source_id)
        return rooted

    @staticmethod
    def _artifact_ids_in_payload(payload: str) -> set[str]:
        try:
            value = json.loads(payload)
        except (TypeError, json.JSONDecodeError):
            return set()
        found: set[str] = set()

        def visit(item: object) -> None:
            if isinstance(item, str) and item.startswith("artifact:"):
                try:
                    found.add(str(DurableArtifactService._reference_id(item)))
                except MishkanError:
                    return
            elif isinstance(item, dict):
                for nested in item.values():
                    visit(nested)
            elif isinstance(item, list):
                for nested in item:
                    visit(nested)

        visit(value)
        return found
