from __future__ import annotations

import os
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import SecretStr

from mishkan.domain.errors import MishkanError
from mishkan.domain.time import utc_now
from mishkan.workers.client import WorkerHttpClient
from mishkan.workers.models import WorkerCertificateBundle
from mishkan.workers.state import WorkerCredentialStore


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
