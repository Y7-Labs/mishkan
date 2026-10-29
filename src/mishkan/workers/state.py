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
from mishkan.workers.models import WorkerEnrollmentRequest


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
