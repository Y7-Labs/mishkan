"""Private local identity material for a non-authoritative worker process."""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from uuid import UUID

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID
from pydantic import ConfigDict, SecretStr, field_validator

from mishkan.domain.errors import ErrorCode, MishkanError
from mishkan.domain.identity import DomainRecord
from mishkan.domain.time import require_aware
from mishkan.workers.client import WorkerHttpClient
from mishkan.workers.models import (
    WorkerCertificateBundle,
    WorkerCertificateRotationRequest,
    WorkerEnrollmentRequest,
)


class WorkerLocalIdentity(DomainRecord):
    """Non-authoritative connection identity; private key bytes are never serialized here."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    worker_id: UUID
    display_name: str
    daemon_url: str
    certificate_serial: str
    certificate_fingerprint_sha256: str
    certificate_not_after: datetime

    @field_validator("certificate_not_after")
    @classmethod
    def expiry_is_aware(cls, value: datetime) -> datetime:
        return require_aware(value)


@dataclass(frozen=True, slots=True)
class WorkerCredentialPaths:
    root: Path
    identity: Path
    certificate: Path
    private_key: Path
    server_ca: Path
    worker_ca: Path
    pending_rotation: Path


class WorkerCredentialStore:
    """Atomically store one worker's identity with private filesystem permissions."""

    def __init__(self, root: Path) -> None:
        self.paths = self._paths(root)

    @staticmethod
    def _paths(root: Path) -> WorkerCredentialPaths:
        return WorkerCredentialPaths(
            root=root,
            identity=root / "identity.json",
            certificate=root / "worker-certificate.pem",
            private_key=root / "worker-private-key.pem",
            server_ca=root / "server-ca.pem",
            worker_ca=root / "worker-ca.pem",
            pending_rotation=root / "pending-rotation.json",
        )

    def initialize(
        self,
        *,
        daemon_url: str,
        display_name: str,
        worker_id: UUID,
        enrollment_token: SecretStr,
        server_ca: Path,
        timeout_seconds: float = 120,
    ) -> WorkerLocalIdentity:
        if self.paths.identity.exists() or self.paths.private_key.exists():
            raise MishkanError(ErrorCode.WORKER, "worker credential store already exists")
        if self.paths.root.exists():
            raise MishkanError(ErrorCode.WORKER, "worker credential directory already exists")
        if not server_ca.is_file():
            raise MishkanError(ErrorCode.CONFIGURATION, "worker server CA file does not exist")
        server_ca_content = server_ca.read_bytes()
        key = ec.generate_private_key(ec.SECP256R1())
        csr = (
            x509.CertificateSigningRequestBuilder()
            .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, display_name)]))
            .sign(key, hashes.SHA256())
            .public_bytes(serialization.Encoding.PEM)
            .decode()
        )
        bundle = WorkerHttpClient.enroll(
            daemon_url,
            WorkerEnrollmentRequest(
                worker_id=worker_id,
                display_name=display_name,
                token=enrollment_token,
                certificate_signing_request_pem=csr,
            ),
            ca_certificate=server_ca,
            timeout_seconds=timeout_seconds,
        )
        if bundle.worker_id != worker_id:
            raise MishkanError(ErrorCode.WORKER, "enrollment returned another worker identity")
        identity = WorkerLocalIdentity(
            worker_id=worker_id,
            display_name=display_name,
            daemon_url=daemon_url.rstrip("/"),
            certificate_serial=bundle.certificate_serial,
            certificate_fingerprint_sha256=bundle.fingerprint_sha256,
            certificate_not_after=bundle.not_after,
        )
        private_key = key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
        self.paths.root.parent.mkdir(parents=True, exist_ok=True)
        staging_root = Path(
            tempfile.mkdtemp(
                prefix=f".{self.paths.root.name}.",
                dir=self.paths.root.parent,
            )
        )
        os.chmod(staging_root, 0o700)
        staging = self._paths(staging_root)
        try:
            self._write_private(staging.private_key, private_key)
            self._write_private(staging.certificate, bundle.certificate_pem.encode())
            self._write_private(staging.worker_ca, bundle.certificate_authority_pem.encode())
            self._write_private(staging.server_ca, server_ca_content)
            self._write_private(
                staging.identity,
                identity.model_dump_json(indent=2).encode(),
            )
            os.replace(staging_root, self.paths.root)
            directory_descriptor = os.open(self.paths.root.parent, os.O_RDONLY)
            try:
                os.fsync(directory_descriptor)
            finally:
                os.close(directory_descriptor)
        except Exception:
            for path in staging_root.iterdir():
                path.unlink(missing_ok=True)
            staging_root.rmdir()
            raise
        return identity

    def load(self) -> WorkerLocalIdentity:
        required = (
            self.paths.identity,
            self.paths.certificate,
            self.paths.private_key,
            self.paths.server_ca,
            self.paths.worker_ca,
        )
        if not all(path.is_file() for path in required):
            raise MishkanError(ErrorCode.WORKER, "worker credential store is incomplete")
        if (self.paths.root.stat().st_mode & 0o077) != 0:
            raise MishkanError(ErrorCode.WORKER, "worker credential directory is not private")
        exposed = [str(path) for path in required if (path.stat().st_mode & 0o077) != 0]
        if exposed:
            raise MishkanError(
                ErrorCode.WORKER,
                "worker credential files are not private",
                details={"paths": exposed},
            )
        try:
            return WorkerLocalIdentity.model_validate_json(self.paths.identity.read_text())
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            raise MishkanError(ErrorCode.WORKER, "worker identity state is invalid") from exc

    def rotate(self, *, timeout_seconds: float = 120) -> WorkerLocalIdentity:
        """Rotate atomically enough to recover both lost responses and local interruption."""

        identity = self.load()
        request, private_key, bundle = self._pending_rotation(identity)
        if bundle is None:
            with WorkerHttpClient(
                identity.daemon_url,
                ca_certificate=self.paths.server_ca,
                client_certificate=self.paths.certificate,
                client_private_key=self.paths.private_key,
                timeout_seconds=timeout_seconds,
            ) as client:
                bundle = client.rotate(request)
            self._write_rotation_pending(request, private_key, bundle)
        rotated = self._validate_rotation(identity, private_key, bundle)
        self._write_private(self.paths.private_key, private_key)
        self._write_private(self.paths.certificate, bundle.certificate_pem.encode())
        self._write_private(
            self.paths.worker_ca,
            bundle.certificate_authority_pem.encode(),
        )
        self._write_private(self.paths.identity, rotated.model_dump_json(indent=2).encode())
        self.paths.pending_rotation.unlink(missing_ok=True)
        return rotated

    def _pending_rotation(
        self,
        identity: WorkerLocalIdentity,
    ) -> tuple[WorkerCertificateRotationRequest, bytes, WorkerCertificateBundle | None]:
        if self.paths.pending_rotation.exists():
            try:
                payload = json.loads(self.paths.pending_rotation.read_text(encoding="utf-8"))
                request = WorkerCertificateRotationRequest.model_validate(payload["request"])
                private_key = str(payload["private_key_pem"]).encode()
                bundle = (
                    WorkerCertificateBundle.model_validate(payload["bundle"])
                    if payload.get("bundle") is not None
                    else None
                )
                return request, private_key, bundle
            except (KeyError, OSError, TypeError, ValueError) as exc:
                raise MishkanError(ErrorCode.WORKER, "pending worker rotation is invalid") from exc
        key = ec.generate_private_key(ec.SECP256R1())
        csr = (
            x509.CertificateSigningRequestBuilder()
            .subject_name(
                x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, identity.display_name)])
            )
            .sign(key, hashes.SHA256())
            .public_bytes(serialization.Encoding.PEM)
            .decode()
        )
        request = WorkerCertificateRotationRequest(certificate_signing_request_pem=csr)
        private_key = key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
        self._write_rotation_pending(request, private_key, None)
        return request, private_key, None

    def _write_rotation_pending(
        self,
        request: WorkerCertificateRotationRequest,
        private_key: bytes,
        bundle: WorkerCertificateBundle | None,
    ) -> None:
        self._write_private(
            self.paths.pending_rotation,
            json.dumps(
                {
                    "request": request.model_dump(mode="json"),
                    "private_key_pem": private_key.decode(),
                    "bundle": bundle.model_dump(mode="json") if bundle is not None else None,
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode(),
        )

    def _validate_rotation(
        self,
        identity: WorkerLocalIdentity,
        private_key_pem: bytes,
        bundle: WorkerCertificateBundle,
    ) -> WorkerLocalIdentity:
        if bundle.worker_id != identity.worker_id:
            raise MishkanError(ErrorCode.WORKER, "rotation returned another worker identity")
        if bundle.certificate_authority_pem.encode() != self.paths.worker_ca.read_bytes():
            raise MishkanError(ErrorCode.WORKER, "rotation changed the trusted worker CA")
        try:
            private_key = serialization.load_pem_private_key(private_key_pem, password=None)
            certificate = x509.load_pem_x509_certificate(bundle.certificate_pem.encode())
            authority = x509.load_pem_x509_certificate(bundle.certificate_authority_pem.encode())
            certificate.verify_directly_issued_by(authority)
        except (TypeError, ValueError) as exc:
            raise MishkanError(ErrorCode.WORKER, "rotated worker certificate is invalid") from exc
        if not isinstance(private_key, ec.EllipticCurvePrivateKey):
            raise MishkanError(ErrorCode.WORKER, "rotated worker private key is invalid")
        certificate_public = certificate.public_key().public_bytes(
            serialization.Encoding.DER,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        private_public = private_key.public_key().public_bytes(
            serialization.Encoding.DER,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        if certificate_public != private_public:
            raise MishkanError(
                ErrorCode.WORKER, "rotated certificate does not match its private key"
            )
        if (
            str(certificate.serial_number) != bundle.certificate_serial
            or certificate.fingerprint(hashes.SHA256()).hex() != bundle.fingerprint_sha256
            or certificate.not_valid_before_utc != bundle.not_before
            or certificate.not_valid_after_utc != bundle.not_after
        ):
            raise MishkanError(ErrorCode.WORKER, "rotated certificate metadata is inconsistent")
        return WorkerLocalIdentity(
            id=identity.id,
            created_at=identity.created_at,
            worker_id=identity.worker_id,
            display_name=identity.display_name,
            daemon_url=identity.daemon_url,
            certificate_serial=bundle.certificate_serial,
            certificate_fingerprint_sha256=bundle.fingerprint_sha256,
            certificate_not_after=bundle.not_after,
        )

    @staticmethod
    def _write_private(path: Path, content: bytes) -> None:
        descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
        temporary = Path(temporary_name)
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "wb", closefd=True) as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
            directory_descriptor = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_descriptor)
            finally:
                os.close(directory_descriptor)
        except Exception:
            temporary.unlink(missing_ok=True)
            raise
