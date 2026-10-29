"""Immutable artifact-body backends for local and distributed deployments."""

from __future__ import annotations

import hashlib
import os
import stat
from collections.abc import Iterator, Mapping
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import BinaryIO, Protocol, cast

from botocore.exceptions import BotoCoreError, ClientError  # type: ignore[import-untyped]

from mishkan.domain.errors import ErrorCode, MishkanError
from mishkan.domain.identity import new_id


@dataclass(frozen=True, slots=True)
class BodyObservation:
    """Observed immutable content facts, computed from the stored bytes."""

    digest: str
    size_bytes: int


class ArtifactBodyStore(Protocol):
    """Storage contract used by the authoritative artifact manifest service."""

    def publish(
        self,
        source: Path,
        storage_ref: str,
        *,
        expected_digest: str,
        expected_size: int,
    ) -> None: ...

    def observe(self, storage_ref: str) -> BodyObservation | None: ...

    def iter_bytes(self, storage_ref: str, *, chunk_size: int) -> Iterator[bytes]: ...

    def delete(self, storage_ref: str) -> None: ...

    def list_refs(self) -> Iterator[str]: ...

    def local_path(self, storage_ref: str) -> Path | None: ...


class _StreamingBody(Protocol):
    def read(self, amount: int = -1) -> bytes: ...

    def close(self) -> None: ...


class _S3Client(Protocol):
    def put_object(self, **kwargs: object) -> Mapping[str, object]: ...

    def create_multipart_upload(self, **kwargs: object) -> Mapping[str, object]: ...

    def upload_part(self, **kwargs: object) -> Mapping[str, object]: ...

    def complete_multipart_upload(self, **kwargs: object) -> Mapping[str, object]: ...

    def abort_multipart_upload(self, **kwargs: object) -> Mapping[str, object]: ...

    def get_object(self, **kwargs: object) -> Mapping[str, object]: ...

    def delete_object(self, **kwargs: object) -> Mapping[str, object]: ...

    def list_objects_v2(self, **kwargs: object) -> Mapping[str, object]: ...


class FilesystemArtifactBodyStore:
    """POSIX no-follow content-addressed body store."""

    def __init__(self, root: Path) -> None:
        self._root = root.resolve()
        self._root.mkdir(parents=True, exist_ok=True)

    def publish(
        self,
        source: Path,
        storage_ref: str,
        *,
        expected_digest: str,
        expected_size: int,
    ) -> None:
        destination = self._path(storage_ref)
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.parent / f".{destination.name}.{new_id()}.tmp"
        source_descriptor = _open_verified(source, expected_digest, expected_size)
        try:
            target_descriptor = os.open(
                temporary,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o600,
            )
            try:
                while chunk := os.read(source_descriptor, 1024 * 1024):
                    view = memoryview(chunk)
                    while view:
                        written = os.write(target_descriptor, view)
                        if written < 1:
                            raise OSError("short artifact CAS write")
                        view = view[written:]
                os.fsync(target_descriptor)
            finally:
                os.close(target_descriptor)
            _require_expected(
                _observe_path(temporary),
                expected_digest,
                expected_size,
            )
            try:
                os.link(temporary, destination, follow_symlinks=False)
            except FileExistsError:
                existing = self.observe(storage_ref)
                if existing is None:
                    raise MishkanError(
                        ErrorCode.ARTIFACT,
                        "artifact CAS destination disappeared during publication",
                    ) from None
                _require_expected(existing, expected_digest, expected_size, collision=True)
            directory = os.open(destination.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            os.close(source_descriptor)
            temporary.unlink(missing_ok=True)

    def observe(self, storage_ref: str) -> BodyObservation | None:
        try:
            return _observe_path(self._path(storage_ref))
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise MishkanError(ErrorCode.ARTIFACT, "artifact body cannot be inspected") from exc

    def iter_bytes(self, storage_ref: str, *, chunk_size: int) -> Iterator[bytes]:
        path = self._path(storage_ref)
        try:
            descriptor = _open_regular(path)
        except FileNotFoundError as exc:
            raise MishkanError(ErrorCode.ARTIFACT, "artifact body is missing") from exc
        try:
            while chunk := os.read(descriptor, chunk_size):
                yield chunk
        finally:
            os.close(descriptor)

    def delete(self, storage_ref: str) -> None:
        self._path(storage_ref).unlink(missing_ok=True)

    def list_refs(self) -> Iterator[str]:
        for body in sorted(self._root.glob("sha256/[0-9a-f][0-9a-f]/*")):
            if body.is_symlink() or not body.is_file():
                continue
            yield body.relative_to(self._root).as_posix()

    def local_path(self, storage_ref: str) -> Path | None:
        return self._path(storage_ref)

    def _path(self, storage_ref: str) -> Path:
        _validate_storage_ref(storage_ref)
        path = (self._root / storage_ref).resolve()
        if not path.is_relative_to(self._root):
            raise MishkanError(ErrorCode.ARTIFACT, "artifact storage path escaped its root")
        return path


class S3ArtifactBodyStore:
    """S3-compatible immutable store with conditional single and multipart publication."""

    def __init__(
        self,
        client: _S3Client,
        *,
        bucket: str,
        prefix: str = "",
        multipart_threshold_bytes: int = 8 * 1024 * 1024,
        multipart_chunk_bytes: int = 8 * 1024 * 1024,
    ) -> None:
        if not bucket:
            raise ValueError("S3 artifact bucket cannot be empty")
        if multipart_threshold_bytes < 1:
            raise ValueError("S3 multipart threshold must be positive")
        if multipart_chunk_bytes < 5 * 1024 * 1024:
            raise ValueError("S3 multipart chunks must satisfy the five MiB S3 minimum")
        self._client = client
        self._bucket = bucket
        self._prefix = prefix.strip("/")
        self._multipart_threshold = multipart_threshold_bytes
        self._multipart_chunk = multipart_chunk_bytes

    @classmethod
    def from_boto3(
        cls,
        *,
        bucket: str,
        prefix: str = "",
        endpoint_url: str | None = None,
        region_name: str | None = None,
        access_key_id: str | None = None,
        secret_access_key: str | None = None,
        session_token: str | None = None,
        verify: bool | str = True,
        addressing_style: str = "path",
        multipart_threshold_bytes: int = 8 * 1024 * 1024,
        multipart_chunk_bytes: int = 8 * 1024 * 1024,
    ) -> S3ArtifactBodyStore:
        import boto3  # type: ignore[import-untyped]
        from botocore.config import Config  # type: ignore[import-untyped]

        client = boto3.client(
            "s3",
            endpoint_url=endpoint_url,
            region_name=region_name,
            aws_access_key_id=access_key_id,
            aws_secret_access_key=secret_access_key,
            aws_session_token=session_token,
            verify=verify,
            config=Config(s3={"addressing_style": addressing_style}),
        )
        return cls(
            cast(_S3Client, client),
            bucket=bucket,
            prefix=prefix,
            multipart_threshold_bytes=multipart_threshold_bytes,
            multipart_chunk_bytes=multipart_chunk_bytes,
        )

    def publish(
        self,
        source: Path,
        storage_ref: str,
        *,
        expected_digest: str,
        expected_size: int,
    ) -> None:
        _validate_storage_ref(storage_ref)
        descriptor = _open_verified(source, expected_digest, expected_size)
        with os.fdopen(descriptor, "rb") as stream:
            try:
                if expected_size >= self._multipart_threshold:
                    self._publish_multipart(stream, storage_ref, expected_digest, expected_size)
                else:
                    self._client.put_object(
                        Bucket=self._bucket,
                        Key=self._key(storage_ref),
                        Body=stream,
                        ContentLength=expected_size,
                        Metadata=_metadata(expected_digest, expected_size),
                        IfNoneMatch="*",
                    )
            except ClientError as exc:
                if _is_conditional_conflict(exc):
                    existing = self.observe(storage_ref)
                    if existing is not None:
                        _require_expected(existing, expected_digest, expected_size, collision=True)
                        return
                raise _s3_error("artifact S3 publication failed", exc) from exc
            except BotoCoreError as exc:
                raise MishkanError(
                    ErrorCode.ARTIFACT,
                    "artifact S3 publication failed",
                    details={"provider_code": type(exc).__name__},
                ) from exc
        stored = self.observe(storage_ref)
        if stored is None:
            raise MishkanError(ErrorCode.ARTIFACT, "published S3 artifact body is missing")
        try:
            _require_expected(stored, expected_digest, expected_size)
        except MishkanError:
            self.delete(storage_ref)
            raise

    def _publish_multipart(
        self,
        source: BinaryIO,
        storage_ref: str,
        expected_digest: str,
        expected_size: int,
    ) -> None:
        key = self._key(storage_ref)
        created = self._client.create_multipart_upload(
            Bucket=self._bucket,
            Key=key,
            Metadata=_metadata(expected_digest, expected_size),
        )
        upload_id = created.get("UploadId")
        if not isinstance(upload_id, str) or not upload_id:
            raise MishkanError(ErrorCode.ARTIFACT, "S3 multipart upload returned no identity")
        parts: list[dict[str, object]] = []
        try:
            part_number = 1
            while chunk := source.read(self._multipart_chunk):
                result = self._client.upload_part(
                    Bucket=self._bucket,
                    Key=key,
                    UploadId=upload_id,
                    PartNumber=part_number,
                    Body=chunk,
                    ContentLength=len(chunk),
                )
                etag = result.get("ETag")
                if not isinstance(etag, str) or not etag:
                    raise MishkanError(ErrorCode.ARTIFACT, "S3 multipart part returned no ETag")
                parts.append({"ETag": etag, "PartNumber": part_number})
                part_number += 1
            self._client.complete_multipart_upload(
                Bucket=self._bucket,
                Key=key,
                UploadId=upload_id,
                MultipartUpload={"Parts": parts},
                MpuObjectSize=expected_size,
                IfNoneMatch="*",
            )
        except Exception:
            with suppress(Exception):
                self._client.abort_multipart_upload(
                    Bucket=self._bucket,
                    Key=key,
                    UploadId=upload_id,
                )
            raise

    def observe(self, storage_ref: str) -> BodyObservation | None:
        _validate_storage_ref(storage_ref)
        try:
            response = self._client.get_object(Bucket=self._bucket, Key=self._key(storage_ref))
        except ClientError as exc:
            if _is_missing(exc):
                return None
            raise _s3_error("artifact S3 body cannot be inspected", exc) from exc
        except BotoCoreError as exc:
            raise MishkanError(
                ErrorCode.ARTIFACT,
                "artifact S3 body cannot be inspected",
                details={"provider_code": type(exc).__name__},
            ) from exc
        body = cast(_StreamingBody, response.get("Body"))
        if body is None:
            raise MishkanError(ErrorCode.ARTIFACT, "artifact S3 response contained no body")
        digest = hashlib.sha256()
        size = 0
        try:
            while chunk := body.read(1024 * 1024):
                digest.update(chunk)
                size += len(chunk)
        finally:
            body.close()
        return BodyObservation(digest=f"sha256:{digest.hexdigest()}", size_bytes=size)

    def iter_bytes(self, storage_ref: str, *, chunk_size: int) -> Iterator[bytes]:
        _validate_storage_ref(storage_ref)
        try:
            response = self._client.get_object(Bucket=self._bucket, Key=self._key(storage_ref))
        except ClientError as exc:
            if _is_missing(exc):
                raise MishkanError(ErrorCode.ARTIFACT, "artifact body is missing") from exc
            raise _s3_error("artifact S3 body cannot be read", exc) from exc
        except BotoCoreError as exc:
            raise MishkanError(
                ErrorCode.ARTIFACT,
                "artifact S3 body cannot be read",
                details={"provider_code": type(exc).__name__},
            ) from exc
        body = cast(_StreamingBody, response.get("Body"))
        if body is None:
            raise MishkanError(ErrorCode.ARTIFACT, "artifact S3 response contained no body")
        try:
            while chunk := body.read(chunk_size):
                yield chunk
        finally:
            body.close()

    def delete(self, storage_ref: str) -> None:
        _validate_storage_ref(storage_ref)
        try:
            self._client.delete_object(Bucket=self._bucket, Key=self._key(storage_ref))
        except ClientError as exc:
            raise _s3_error("artifact S3 body deletion failed", exc) from exc
        except BotoCoreError as exc:
            raise MishkanError(
                ErrorCode.ARTIFACT,
                "artifact S3 body deletion failed",
                details={"provider_code": type(exc).__name__},
            ) from exc

    def list_refs(self) -> Iterator[str]:
        prefix = f"{self._prefix}/sha256/" if self._prefix else "sha256/"
        continuation: str | None = None
        while True:
            arguments: dict[str, object] = {"Bucket": self._bucket, "Prefix": prefix}
            if continuation is not None:
                arguments["ContinuationToken"] = continuation
            try:
                page = self._client.list_objects_v2(**arguments)
            except ClientError as exc:
                raise _s3_error("artifact S3 listing failed", exc) from exc
            except BotoCoreError as exc:
                raise MishkanError(
                    ErrorCode.ARTIFACT,
                    "artifact S3 listing failed",
                    details={"provider_code": type(exc).__name__},
                ) from exc
            contents = page.get("Contents", [])
            if isinstance(contents, list):
                for entry in contents:
                    if not isinstance(entry, Mapping):
                        continue
                    key = entry.get("Key")
                    if isinstance(key, str):
                        storage_ref = self._storage_ref(key)
                        if storage_ref is not None:
                            yield storage_ref
            if not page.get("IsTruncated"):
                return
            token = page.get("NextContinuationToken")
            if not isinstance(token, str) or not token:
                raise MishkanError(
                    ErrorCode.ARTIFACT, "truncated S3 listing returned no continuation token"
                )
            continuation = token

    def local_path(self, storage_ref: str) -> Path | None:
        _validate_storage_ref(storage_ref)
        return None

    def _key(self, storage_ref: str) -> str:
        return f"{self._prefix}/{storage_ref}" if self._prefix else storage_ref

    def _storage_ref(self, key: str) -> str | None:
        if self._prefix:
            prefix = f"{self._prefix}/"
            if not key.startswith(prefix):
                return None
            key = key.removeprefix(prefix)
        try:
            _validate_storage_ref(key)
        except MishkanError:
            return None
        return key


def _validate_storage_ref(storage_ref: str) -> None:
    path = PurePosixPath(storage_ref)
    if (
        path.is_absolute()
        or len(path.parts) != 3
        or path.parts[0] != "sha256"
        or len(path.parts[1]) != 2
        or len(path.parts[2]) != 62
        or any(character not in "0123456789abcdef" for character in "".join(path.parts[1:]))
    ):
        raise MishkanError(ErrorCode.ARTIFACT, "artifact storage reference is invalid")


def _open_regular(path: Path) -> int:
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    observed = os.fstat(descriptor)
    if not stat.S_ISREG(observed.st_mode):
        os.close(descriptor)
        raise MishkanError(ErrorCode.ARTIFACT, "artifact body is not a regular file")
    return descriptor


def _observe_path(path: Path) -> BodyObservation:
    descriptor = _open_regular(path)
    digest = hashlib.sha256()
    size = 0
    try:
        while chunk := os.read(descriptor, 1024 * 1024):
            digest.update(chunk)
            size += len(chunk)
    finally:
        os.close(descriptor)
    return BodyObservation(digest=f"sha256:{digest.hexdigest()}", size_bytes=size)


def _open_verified(path: Path, expected_digest: str, expected_size: int) -> int:
    descriptor = _open_regular(path)
    digest = hashlib.sha256()
    size = 0
    try:
        while chunk := os.read(descriptor, 1024 * 1024):
            digest.update(chunk)
            size += len(chunk)
        observed = BodyObservation(digest=f"sha256:{digest.hexdigest()}", size_bytes=size)
        _require_expected(observed, expected_digest, expected_size)
        os.lseek(descriptor, 0, os.SEEK_SET)
        return descriptor
    except Exception:
        os.close(descriptor)
        raise


def _require_expected(
    observed: BodyObservation,
    expected_digest: str,
    expected_size: int,
    *,
    collision: bool = False,
) -> None:
    if observed.digest == expected_digest and observed.size_bytes == expected_size:
        return
    raise MishkanError(
        ErrorCode.ARTIFACT,
        "artifact CAS collision was detected"
        if collision
        else "artifact upload failed size or digest verification",
        details={
            "expected_digest": expected_digest,
            "observed_digest": observed.digest,
            "expected_size": expected_size,
            "observed_size": observed.size_bytes,
        },
    )


def _metadata(digest: str, size: int) -> dict[str, str]:
    return {"mishkan-sha256": digest.removeprefix("sha256:"), "mishkan-size": str(size)}


def _is_conditional_conflict(exc: ClientError) -> bool:
    code = str(exc.response.get("Error", {}).get("Code", ""))
    status = exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
    return code in {"ConditionalRequestConflict", "PreconditionFailed"} or status in {409, 412}


def _is_missing(exc: ClientError) -> bool:
    code = str(exc.response.get("Error", {}).get("Code", ""))
    status = exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
    return code in {"NoSuchKey", "NotFound", "404"} or status == 404


def _s3_error(message: str, exc: ClientError) -> MishkanError:
    code = str(exc.response.get("Error", {}).get("Code", "unknown"))
    return MishkanError(ErrorCode.ARTIFACT, message, details={"provider_code": code})
