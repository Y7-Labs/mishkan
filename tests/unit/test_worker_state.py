from __future__ import annotations

import os
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID
from pydantic import SecretStr

from mishkan.domain.errors import MishkanError
from mishkan.domain.time import utc_now
from mishkan.workers.client import WorkerHttpClient
from mishkan.workers.models import WorkerCertificateBundle
from mishkan.workers.state import WorkerCredentialStore


def _test_authority() -> tuple[ec.EllipticCurvePrivateKey, x509.Certificate, str]:
    key = ec.generate_private_key(ec.SECP256R1())
    now = utc_now()
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "worker test CA")])
    certificate = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .sign(key, hashes.SHA256())
    )
    pem = certificate.public_bytes(serialization.Encoding.PEM).decode()
    return key, certificate, pem


def _signed_bundle(
    worker_id,  # type: ignore[no-untyped-def]
    csr_pem: str,
    authority_key: ec.EllipticCurvePrivateKey,
    authority: x509.Certificate,
    authority_pem: str,
) -> WorkerCertificateBundle:
    csr = x509.load_pem_x509_csr(csr_pem.encode())
    now = utc_now()
    certificate = (
        x509.CertificateBuilder()
        .subject_name(csr.subject)
        .issuer_name(authority.subject)
        .public_key(csr.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(seconds=30))
        .not_valid_after(now + timedelta(hours=1))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .sign(authority_key, hashes.SHA256())
    )
    return WorkerCertificateBundle(
        worker_id=worker_id,
        certificate_pem=certificate.public_bytes(serialization.Encoding.PEM).decode(),
        certificate_authority_pem=authority_pem,
        certificate_serial=str(certificate.serial_number),
        fingerprint_sha256=certificate.fingerprint(hashes.SHA256()).hex(),
        not_before=certificate.not_valid_before_utc,
        not_after=certificate.not_valid_after_utc,
    )


def test_worker_enrollment_store_is_atomic_private_and_contains_no_token(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    worker_id = uuid4()
    now = utc_now()
    bundle = WorkerCertificateBundle(
        worker_id=worker_id,
        certificate_pem="worker certificate",
        certificate_authority_pem="worker ca",
        certificate_serial="123",
        fingerprint_sha256="a" * 64,
        not_before=now,
        not_after=now + timedelta(hours=1),
    )
    secret = "one-time-enrollment-secret-that-must-not-persist"
    observed: list[str] = []

    def enroll(_cls, _url, request, **_kwargs):  # type: ignore[no-untyped-def]
        observed.append(request.token.get_secret_value())
        return bundle

    monkeypatch.setattr(WorkerHttpClient, "enroll", classmethod(enroll))
    server_ca = tmp_path / "source-server-ca.pem"
    server_ca.write_text("server ca", encoding="utf-8")
    store = WorkerCredentialStore(tmp_path / "worker-state")

    identity = store.initialize(
        daemon_url="https://daemon.test/",
        display_name="worker-one",
        worker_id=worker_id,
        enrollment_token=SecretStr(secret),
        server_ca=server_ca,
    )

    assert observed == [secret]
    assert store.load() == identity
    assert identity.daemon_url == "https://daemon.test"
    assert (store.paths.root.stat().st_mode & 0o777) == 0o700
    assert all((path.stat().st_mode & 0o777) == 0o600 for path in store.paths.root.iterdir())
    assert secret.encode() not in b"".join(path.read_bytes() for path in store.paths.root.iterdir())
    assert not tuple(tmp_path.glob(".worker-state.*"))


def test_worker_store_refuses_reinitialization_and_exposed_permissions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    worker_id = uuid4()
    now = utc_now()
    bundle = WorkerCertificateBundle(
        worker_id=worker_id,
        certificate_pem="worker certificate",
        certificate_authority_pem="worker ca",
        certificate_serial="123",
        fingerprint_sha256="a" * 64,
        not_before=now,
        not_after=now + timedelta(hours=1),
    )
    monkeypatch.setattr(
        WorkerHttpClient,
        "enroll",
        classmethod(lambda _cls, _url, _request, **_kwargs: bundle),
    )
    server_ca = tmp_path / "server-ca.pem"
    server_ca.write_text("server ca", encoding="utf-8")
    store = WorkerCredentialStore(tmp_path / "worker-state")
    arguments = {
        "daemon_url": "https://daemon.test",
        "display_name": "worker-one",
        "worker_id": worker_id,
        "enrollment_token": SecretStr("one-time-enrollment-secret-that-is-long-enough"),
        "server_ca": server_ca,
    }
    store.initialize(**arguments)

    with pytest.raises(MishkanError, match="already exists"):
        store.initialize(**arguments)
    os.chmod(store.paths.private_key, 0o644)
    with pytest.raises(MishkanError, match="not private"):
        store.load()


def test_worker_rotation_recovers_a_lost_server_response_without_new_request(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    worker_id = uuid4()
    authority_key, authority, authority_pem = _test_authority()

    def enroll(_cls, _url, request, **_kwargs):  # type: ignore[no-untyped-def]
        return _signed_bundle(
            worker_id,
            request.certificate_signing_request_pem,
            authority_key,
            authority,
            authority_pem,
        )

    monkeypatch.setattr(WorkerHttpClient, "enroll", classmethod(enroll))
    server_ca = tmp_path / "server-ca.pem"
    server_ca.write_text("server ca", encoding="utf-8")
    store = WorkerCredentialStore(tmp_path / "worker-state")
    original = store.initialize(
        daemon_url="https://daemon.test",
        display_name="worker-one",
        worker_id=worker_id,
        enrollment_token=SecretStr("one-time-enrollment-secret-that-is-long-enough"),
        server_ca=server_ca,
    )
    requests = []
    outcomes = {}
    lost = True

    class RotationClient:
        def __init__(self, *_args, **_kwargs) -> None:  # type: ignore[no-untyped-def]
            pass

        def __enter__(self):  # type: ignore[no-untyped-def]
            return self

        def __exit__(self, *_args) -> None:  # type: ignore[no-untyped-def]
            pass

        def rotate(self, request):  # type: ignore[no-untyped-def]
            nonlocal lost
            requests.append(request)
            outcome = outcomes.setdefault(
                request.id,
                _signed_bundle(
                    worker_id,
                    request.certificate_signing_request_pem,
                    authority_key,
                    authority,
                    authority_pem,
                ).model_copy(update={"id": request.id, "created_at": request.created_at}),
            )
            if lost:
                lost = False
                raise RuntimeError("rotation response lost")
            return outcome

    monkeypatch.setattr("mishkan.workers.state.WorkerHttpClient", RotationClient)

    with pytest.raises(RuntimeError, match="response lost"):
        store.rotate()
    assert store.load() == original
    assert store.paths.pending_rotation.is_file()

    rotated = store.rotate()

    assert requests[0] == requests[1]
    assert rotated.certificate_serial != original.certificate_serial
    assert store.load() == rotated
    assert not store.paths.pending_rotation.exists()
    assert all((path.stat().st_mode & 0o777) == 0o600 for path in store.paths.root.iterdir())
