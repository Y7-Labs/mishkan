from __future__ import annotations

import hashlib
import io
from pathlib import Path
from typing import cast

import boto3
import pytest
from botocore.exceptions import ClientError, EndpointConnectionError

from mishkan.artifacts.bodies import (
    ArtifactBodyStore,
    FilesystemArtifactBodyStore,
    S3ArtifactBodyStore,
)
from mishkan.artifacts.models import ArtifactProvenance
from mishkan.artifacts.service import DurableArtifactService
from mishkan.domain.errors import ErrorCode, MishkanError
from mishkan.persistence import SchemaManager


class _Body(io.BytesIO):
    pass


class _MemoryS3:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.uploads: dict[str, dict[int, bytes]] = {}
        self.multipart_completed = 0

    def put_object(self, **kwargs: object) -> dict[str, object]:
        key = str(kwargs["Key"])
        if key in self.objects and kwargs.get("IfNoneMatch") == "*":
            raise self._error("PreconditionFailed", 412, "PutObject")
        body = kwargs["Body"]
        self.objects[key] = cast(io.BufferedReader, body).read()
        return {"ETag": "single"}

    def create_multipart_upload(self, **kwargs: object) -> dict[str, object]:
        upload_id = f"upload-{len(self.uploads) + 1}"
        self.uploads[upload_id] = {}
        return {"UploadId": upload_id}

    def upload_part(self, **kwargs: object) -> dict[str, object]:
        upload_id = str(kwargs["UploadId"])
        part = int(cast(int, kwargs["PartNumber"]))
        self.uploads[upload_id][part] = bytes(cast(bytes, kwargs["Body"]))
        return {"ETag": f"part-{part}"}

    def complete_multipart_upload(self, **kwargs: object) -> dict[str, object]:
        key = str(kwargs["Key"])
        if key in self.objects and kwargs.get("IfNoneMatch") == "*":
            raise self._error("PreconditionFailed", 412, "CompleteMultipartUpload")
        upload_id = str(kwargs["UploadId"])
        self.objects[key] = b"".join(
            value for _, value in sorted(self.uploads.pop(upload_id).items())
        )
        self.multipart_completed += 1
        return {"ETag": "multipart"}

    def abort_multipart_upload(self, **kwargs: object) -> dict[str, object]:
        self.uploads.pop(str(kwargs["UploadId"]), None)
        return {}

    def get_object(self, **kwargs: object) -> dict[str, object]:
        key = str(kwargs["Key"])
        if key not in self.objects:
            raise self._error("NoSuchKey", 404, "GetObject")
        return {"Body": _Body(self.objects[key])}

    def delete_object(self, **kwargs: object) -> dict[str, object]:
        self.objects.pop(str(kwargs["Key"]), None)
        return {}

    def list_objects_v2(self, **kwargs: object) -> dict[str, object]:
        prefix = str(kwargs["Prefix"])
        return {
            "Contents": [{"Key": key} for key in sorted(self.objects) if key.startswith(prefix)],
            "IsTruncated": False,
        }

    @staticmethod
    def _error(code: str, status: int, operation: str) -> ClientError:
        return ClientError(
            {
                "Error": {"Code": code, "Message": code},
                "ResponseMetadata": {"HTTPStatusCode": status},
            },
            operation,
        )


class _NoBodyS3(_MemoryS3):
    def get_object(self, **kwargs: object) -> dict[str, object]:
        return {}


class _BrokenS3(_MemoryS3):
    def __init__(self, operation: str) -> None:
        super().__init__()
        self.operation = operation

    def put_object(self, **kwargs: object) -> dict[str, object]:
        if self.operation == "put":
            raise self._error("AccessDenied", 403, "PutObject")
        return super().put_object(**kwargs)

    def get_object(self, **kwargs: object) -> dict[str, object]:
        if self.operation == "get":
            raise EndpointConnectionError(endpoint_url="http://unavailable")
        return super().get_object(**kwargs)

    def delete_object(self, **kwargs: object) -> dict[str, object]:
        if self.operation == "delete":
            raise self._error("AccessDenied", 403, "DeleteObject")
        return super().delete_object(**kwargs)

    def list_objects_v2(self, **kwargs: object) -> dict[str, object]:
        if self.operation == "list":
            raise self._error("AccessDenied", 403, "ListObjectsV2")
        if self.operation == "truncated":
            return {"Contents": [], "IsTruncated": True}
        return super().list_objects_v2(**kwargs)


class _BadMultipartS3(_MemoryS3):
    def __init__(self, failure: str) -> None:
        super().__init__()
        self.failure = failure

    def create_multipart_upload(self, **kwargs: object) -> dict[str, object]:
        if self.failure == "identity":
            return {}
        return super().create_multipart_upload(**kwargs)

    def upload_part(self, **kwargs: object) -> dict[str, object]:
        if self.failure == "etag":
            upload_id = str(kwargs["UploadId"])
            self.uploads[upload_id][int(cast(int, kwargs["PartNumber"]))] = bytes(
                cast(bytes, kwargs["Body"])
            )
            return {}
        return super().upload_part(**kwargs)


def _facts(content: bytes) -> tuple[str, str]:
    digest = hashlib.sha256(content).hexdigest()
    return f"sha256:{digest}", f"sha256/{digest[:2]}/{digest[2:]}"


def _publish(store: ArtifactBodyStore, source: Path, content: bytes) -> str:
    source.write_bytes(content)
    digest, storage_ref = _facts(content)
    store.publish(
        source,
        storage_ref,
        expected_digest=digest,
        expected_size=len(content),
    )
    return storage_ref


def test_filesystem_backend_is_immutable_streamable_and_listable(tmp_path: Path) -> None:
    store = FilesystemArtifactBodyStore(tmp_path / "blobs")
    content = b"bounded evidence"
    storage_ref = _publish(store, tmp_path / "source", content)

    observed = store.observe(storage_ref)
    assert observed is not None and observed.size_bytes == len(content)
    assert b"".join(store.iter_bytes(storage_ref, chunk_size=3)) == content
    assert tuple(store.list_refs()) == (storage_ref,)
    assert store.local_path(storage_ref) is not None

    _publish(store, tmp_path / "same", content)
    store.delete(storage_ref)
    assert store.observe(storage_ref) is None


def test_s3_backend_uses_conditional_publication_and_streamed_observation(tmp_path: Path) -> None:
    client = _MemoryS3()
    store = S3ArtifactBodyStore(
        cast(object, client),
        bucket="evidence",
        prefix="tenant-a",
        multipart_threshold_bytes=1024,
        multipart_chunk_bytes=5 * 1024 * 1024,
    )
    content = b"attributed result"
    storage_ref = _publish(store, tmp_path / "source", content)

    assert store.observe(storage_ref) is not None
    assert b"".join(store.iter_bytes(storage_ref, chunk_size=4)) == content
    assert tuple(store.list_refs()) == (storage_ref,)
    assert store.local_path(storage_ref) is None

    _publish(store, tmp_path / "same", content)
    client.objects[f"tenant-a/{storage_ref}"] = b"collision"
    with pytest.raises(MishkanError) as failure:
        _publish(store, tmp_path / "collision", content)
    assert failure.value.envelope.code is ErrorCode.ARTIFACT


def test_s3_backend_completes_bounded_multipart_upload(tmp_path: Path) -> None:
    client = _MemoryS3()
    chunk_bytes = 5 * 1024 * 1024
    store = S3ArtifactBodyStore(
        cast(object, client),
        bucket="evidence",
        multipart_threshold_bytes=1,
        multipart_chunk_bytes=chunk_bytes,
    )
    content = b"a" * (chunk_bytes + 17)
    storage_ref = _publish(store, tmp_path / "large", content)

    assert client.multipart_completed == 1
    assert store.observe(storage_ref) is not None
    assert b"".join(store.iter_bytes(storage_ref, chunk_size=1024 * 1024)) == content


def test_body_backends_reject_digest_mismatch_and_unsafe_reference(tmp_path: Path) -> None:
    store = FilesystemArtifactBodyStore(tmp_path / "blobs")
    source = tmp_path / "source"
    source.write_bytes(b"actual")
    digest, storage_ref = _facts(b"expected")

    with pytest.raises(MishkanError):
        store.publish(
            source,
            storage_ref,
            expected_digest=digest,
            expected_size=len(b"expected"),
        )
    with pytest.raises(MishkanError):
        store.observe("../escape")


def test_durable_manifest_authority_uses_remote_body_backend(tmp_path: Path) -> None:
    database = tmp_path / "mishkan.db"
    SchemaManager(database).initialize()
    client = _MemoryS3()
    body_store = S3ArtifactBodyStore(
        cast(object, client),
        bucket="evidence",
        prefix="project",
        multipart_threshold_bytes=1024,
        multipart_chunk_bytes=5 * 1024 * 1024,
    )
    service = DurableArtifactService(
        database,
        tmp_path / "staging",
        max_artifact_bytes=1024,
        max_chunk_bytes=4,
        body_store=body_store,
    )

    manifest = service.put_bytes(
        b"remote evidence",
        media_type="text/plain",
        provenance=ArtifactProvenance(
            producer_identity="test-agent",
            run_id="run-1",
            task_attempt_id="attempt-1",
            call_id="call-1",
            capability="artifact.test",
            channel="test.evidence",
        ),
        complete=True,
    )

    assert service.read_bytes(manifest.reference) == b"remote evidence"
    with pytest.raises(MishkanError, match="streamed reads"):
        service.body_path(manifest.reference)
    assert not tuple((tmp_path / "staging" / "staging").glob("*.upload"))


def test_body_backend_validation_and_missing_contracts(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="bucket"):
        S3ArtifactBodyStore(cast(object, _MemoryS3()), bucket="")
    with pytest.raises(ValueError, match="threshold"):
        S3ArtifactBodyStore(
            cast(object, _MemoryS3()), bucket="evidence", multipart_threshold_bytes=0
        )
    with pytest.raises(ValueError, match="five MiB"):
        S3ArtifactBodyStore(
            cast(object, _MemoryS3()), bucket="evidence", multipart_chunk_bytes=1024
        )

    content = b"missing"
    _, storage_ref = _facts(content)
    filesystem = FilesystemArtifactBodyStore(tmp_path / "blobs")
    with pytest.raises(MishkanError, match="missing"):
        tuple(filesystem.iter_bytes(storage_ref, chunk_size=2))
    remote = S3ArtifactBodyStore(cast(object, _MemoryS3()), bucket="evidence")
    assert remote.observe(storage_ref) is None
    with pytest.raises(MishkanError, match="missing"):
        tuple(remote.iter_bytes(storage_ref, chunk_size=2))


def test_s3_provider_failures_are_stable_and_do_not_expose_provider_text(tmp_path: Path) -> None:
    content = b"failure"
    source = tmp_path / "source"
    digest, storage_ref = _facts(content)
    source.write_bytes(content)

    put_store = S3ArtifactBodyStore(cast(object, _BrokenS3("put")), bucket="evidence")
    with pytest.raises(MishkanError) as failure:
        put_store.publish(
            source,
            storage_ref,
            expected_digest=digest,
            expected_size=len(content),
        )
    assert failure.value.envelope.details == {"provider_code": "AccessDenied"}

    get_store = S3ArtifactBodyStore(cast(object, _BrokenS3("get")), bucket="evidence")
    with pytest.raises(MishkanError, match="cannot be inspected"):
        get_store.observe(storage_ref)
    delete_store = S3ArtifactBodyStore(cast(object, _BrokenS3("delete")), bucket="evidence")
    with pytest.raises(MishkanError, match="deletion failed"):
        delete_store.delete(storage_ref)
    list_store = S3ArtifactBodyStore(cast(object, _BrokenS3("list")), bucket="evidence")
    with pytest.raises(MishkanError, match="listing failed"):
        tuple(list_store.list_refs())
    truncated = S3ArtifactBodyStore(cast(object, _BrokenS3("truncated")), bucket="evidence")
    with pytest.raises(MishkanError, match="continuation token"):
        tuple(truncated.list_refs())


def test_s3_rejects_malformed_responses_and_aborts_multipart(tmp_path: Path) -> None:
    content = b"multipart"
    source = tmp_path / "source"
    digest, storage_ref = _facts(content)
    source.write_bytes(content)
    no_body = S3ArtifactBodyStore(cast(object, _NoBodyS3()), bucket="evidence")
    with pytest.raises(MishkanError, match="no body"):
        no_body.observe(storage_ref)
    with pytest.raises(MishkanError, match="no body"):
        tuple(no_body.iter_bytes(storage_ref, chunk_size=2))

    for malformed, message in (("identity", "no identity"), ("etag", "no ETag")):
        client = _BadMultipartS3(malformed)
        store = S3ArtifactBodyStore(
            cast(object, client),
            bucket="evidence",
            multipart_threshold_bytes=1,
            multipart_chunk_bytes=5 * 1024 * 1024,
        )
        with pytest.raises(MishkanError, match=message):
            store.publish(
                source,
                storage_ref,
                expected_digest=digest,
                expected_size=len(content),
            )
        assert client.uploads == {}


def test_boto3_factory_preserves_public_connection_options(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _MemoryS3()
    observed: dict[str, object] = {}

    def build_client(service: str, **kwargs: object) -> _MemoryS3:
        observed.update({"service": service, **kwargs})
        return client

    monkeypatch.setattr(boto3, "client", build_client)
    store = S3ArtifactBodyStore.from_boto3(
        bucket="evidence",
        prefix="project",
        endpoint_url="http://127.0.0.1:9000",
        region_name="us-east-1",
        access_key_id="reference-value",
        secret_access_key="reference-value",
        verify=False,
        addressing_style="path",
    )

    assert store.local_path(_facts(b"content")[1]) is None
    assert observed["service"] == "s3"
    assert observed["endpoint_url"] == "http://127.0.0.1:9000"
