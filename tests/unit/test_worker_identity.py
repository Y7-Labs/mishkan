from __future__ import annotations

import hashlib
from datetime import timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID
from sqlalchemy import text
from sqlalchemy.orm import Session

from mishkan.application import ApplicationCommand
from mishkan.domain.errors import ErrorCode, MishkanError
from mishkan.domain.time import utc_now
from mishkan.persistence import SchemaManager, SQLiteApplicationRepository
from mishkan.persistence.sqlite import (
    WorkerCertificateRow,
    WorkerEnrollmentTokenRow,
    create_local_engine,
)
from mishkan.workers import (
    EnrollmentCredential,
    EnrollmentState,
    WorkerCertificateAuthority,
    WorkerCertificateBundle,
    WorkerEnrollmentRequest,
    WorkerEnrollmentService,
    WorkerIdentityRepository,
    WorkerState,
)
from mishkan.workers.models import CertificateState, WorkerRotationRequest


def _ca_material() -> tuple[str, str]:
    key = ec.generate_private_key(ec.SECP256R1())
    now = utc_now()
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "MISHKAN worker test CA")])
    certificate = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(days=7))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=True,
                crl_sign=True,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .sign(key, hashes.SHA256())
    )
    return (
        certificate.public_bytes(serialization.Encoding.PEM).decode(),
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ).decode(),
    )


def _csr(*, request_ca: bool = False) -> tuple[object, str]:
    key = ec.generate_private_key(ec.SECP256R1())
    builder = x509.CertificateSigningRequestBuilder().subject_name(
        x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "untrusted-worker-input")])
    )
    if request_ca:
        builder = builder.add_extension(
            x509.BasicConstraints(ca=True, path_length=None),
            critical=True,
        )
    request = builder.sign(key, hashes.SHA256())
    return key, request.public_bytes(serialization.Encoding.PEM).decode()


def _service(tmp_path: Path) -> tuple[WorkerEnrollmentService, WorkerIdentityRepository, Path]:
    database = tmp_path / "mishkan.db"
    SchemaManager(database).initialize()
    repository = WorkerIdentityRepository(database)
    ca_certificate, ca_key = _ca_material()
    service = WorkerEnrollmentService(
        repository,
        WorkerCertificateAuthority(ca_certificate, ca_key),
        enrollment_ttl_seconds=600,
        certificate_ttl_seconds=3_600,
    )
    return service, repository, database


def _enroll(
    service: WorkerEnrollmentService,
    worker_id: UUID | None = None,
) -> tuple[UUID, EnrollmentCredential, WorkerCertificateBundle]:
    worker_id = worker_id or uuid4()
    credential = service.create_enrollment(actor_id="CTO")
    _, csr = _csr()
    bundle = service.enroll(
        WorkerEnrollmentRequest(
            worker_id=worker_id,
            display_name=f"worker-{worker_id}",
            token=credential.token,
            certificate_signing_request_pem=csr,
        )
    )
    return worker_id, credential, bundle


def test_enrollment_persists_only_digest_and_issues_exact_worker_identity(
    tmp_path: Path,
) -> None:
    service, repository, database = _service(tmp_path)
    worker_id, credential, bundle = _enroll(service)
    secret = credential.token.get_secret_value()

    with Session(create_local_engine(database)) as session:
        token = session.get(WorkerEnrollmentTokenRow, str(credential.id))
        assert token is not None
        assert token.token_digest == hashlib.sha256(secret.encode()).hexdigest()
        assert secret not in token.token_digest
        assert token.state == EnrollmentState.CONSUMED.value

    certificate = x509.load_pem_x509_certificate(bundle.certificate_pem.encode())
    assert certificate.extensions.get_extension_for_class(x509.BasicConstraints).value.ca is False
    assert (
        ExtendedKeyUsageOID.CLIENT_AUTH
        in certificate.extensions.get_extension_for_class(x509.ExtendedKeyUsage).value
    )
    assert certificate.extensions.get_extension_for_class(
        x509.SubjectAlternativeName
    ).value.get_values_for_type(x509.UniformResourceIdentifier) == [
        f"spiffe://mishkan/worker/{worker_id}"
    ]
    assert repository.identity(worker_id).state is WorkerState.ACTIVE
    assert service.authenticate_certificate(bundle.certificate_pem).id == worker_id


def test_enrollment_token_is_single_use(tmp_path: Path) -> None:
    service, _, _ = _service(tmp_path)
    worker_id, credential, _ = _enroll(service)
    _, csr = _csr()

    with pytest.raises(MishkanError) as caught:
        service.enroll(
            WorkerEnrollmentRequest(
                worker_id=worker_id,
                display_name="duplicate",
                token=credential.token,
                certificate_signing_request_pem=csr,
            )
        )

    assert caught.value.envelope.code is ErrorCode.WORKER
    assert "not active" in caught.value.envelope.message


def test_expired_and_revoked_tokens_fail_closed(tmp_path: Path) -> None:
    service, _, database = _service(tmp_path)
    expired = service.create_enrollment(actor_id="CTO")
    revoked = service.create_enrollment(actor_id="CTO")
    service.revoke_enrollment(revoked.id)
    with Session(create_local_engine(database)) as session, session.begin():
        row = session.get(WorkerEnrollmentTokenRow, str(expired.id))
        assert row is not None
        row.expires_at = (utc_now() - timedelta(seconds=1)).isoformat()
    _, csr = _csr()

    for credential in (expired, revoked):
        with pytest.raises(MishkanError) as caught:
            service.enroll(
                WorkerEnrollmentRequest(
                    worker_id=uuid4(),
                    display_name="refused",
                    token=credential.token,
                    certificate_signing_request_pem=csr,
                )
            )
        assert caught.value.envelope.code is ErrorCode.WORKER

    with Session(create_local_engine(database)) as session:
        row = session.get(WorkerEnrollmentTokenRow, str(expired.id))
        assert row is not None
        assert row.state == EnrollmentState.EXPIRED.value


def test_rotation_supersedes_old_certificate_and_authenticates_only_current(
    tmp_path: Path,
) -> None:
    service, repository, database = _service(tmp_path)
    worker_id, _, original = _enroll(service)
    _, csr = _csr()
    replacement = service.rotate(
        WorkerRotationRequest(
            worker_id=worker_id,
            current_certificate_serial=original.certificate_serial,
            certificate_signing_request_pem=csr,
        )
    )

    with pytest.raises(MishkanError):
        service.authenticate_certificate(original.certificate_pem)
    assert service.authenticate_certificate(replacement.certificate_pem).id == worker_id
    assert repository.identity(worker_id).revision == 2
    with Session(create_local_engine(database)) as session:
        previous = session.get(WorkerCertificateRow, original.certificate_serial)
        current = session.get(WorkerCertificateRow, replacement.certificate_serial)
        assert previous is not None and previous.state == CertificateState.SUPERSEDED.value
        assert current is not None and current.state == CertificateState.CURRENT.value


def test_rotation_replay_recovers_the_same_bundle_after_response_loss(tmp_path: Path) -> None:
    service, repository, _database = _service(tmp_path)
    worker_id, _, original = _enroll(service)
    _, csr = _csr()
    request = WorkerRotationRequest(
        worker_id=worker_id,
        current_certificate_serial=original.certificate_serial,
        certificate_signing_request_pem=csr,
    )

    replacement = service.rotate(request)

    replay_identity, replay_serial = service.authenticate_rotation_peer(
        original.certificate_pem,
        request.id,
    )
    replay = service.rotate(request)
    assert replay_identity.id == worker_id
    assert replay_serial == original.certificate_serial
    assert replay == replacement
    assert repository.identity(worker_id).revision == 2

    with pytest.raises(MishkanError, match="rotation certificate"):
        service.authenticate_rotation_peer(original.certificate_pem, uuid4())

    _, different_csr = _csr()
    with pytest.raises(MishkanError) as reused:
        service.rotate(
            request.model_copy(update={"certificate_signing_request_pem": different_csr})
        )
    assert reused.value.envelope.code is ErrorCode.DUPLICATE_RESULT


def test_revocation_is_individual_and_revision_guarded(tmp_path: Path) -> None:
    service, _, _ = _service(tmp_path)
    first_id, _, first = _enroll(service)
    second_id, _, second = _enroll(service)

    with pytest.raises(MishkanError) as stale:
        service.revoke_worker(first_id, expected_revision=99)
    assert stale.value.envelope.code is ErrorCode.REVISION_MISMATCH

    revoked = service.revoke_worker(first_id, expected_revision=1)
    assert revoked.state is WorkerState.REVOKED
    with pytest.raises(MishkanError):
        service.authenticate_certificate(first.certificate_pem)
    assert service.authenticate_certificate(second.certificate_pem).id == second_id


def test_requested_ca_privilege_is_not_copied_from_csr(tmp_path: Path) -> None:
    service, _, _ = _service(tmp_path)
    credential = service.create_enrollment(actor_id="CTO")
    _, csr = _csr(request_ca=True)
    bundle = service.enroll(
        WorkerEnrollmentRequest(
            worker_id=uuid4(),
            display_name="no-escalation",
            token=credential.token,
            certificate_signing_request_pem=csr,
        )
    )

    certificate = x509.load_pem_x509_certificate(bundle.certificate_pem.encode())
    assert certificate.extensions.get_extension_for_class(x509.BasicConstraints).value.ca is False


def test_weak_worker_key_and_mismatched_ca_are_rejected(tmp_path: Path) -> None:
    ca_certificate, ca_key = _ca_material()
    other_certificate, _ = _ca_material()
    with pytest.raises(MishkanError):
        WorkerCertificateAuthority(other_certificate, ca_key)

    authority = WorkerCertificateAuthority(ca_certificate, ca_key)
    weak_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    weak_csr = (
        x509.CertificateSigningRequestBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "weak")]))
        .sign(weak_key, hashes.SHA256())
        .public_bytes(serialization.Encoding.PEM)
        .decode()
    )
    with pytest.raises(MishkanError) as caught:
        authority.sign_worker_csr(uuid4(), weak_csr, lifetime_seconds=3_600)
    assert caught.value.envelope.code is ErrorCode.WORKER


def test_enrollment_command_is_atomic_idempotent_and_never_persists_secret(
    tmp_path: Path,
) -> None:
    database = tmp_path / "mishkan.db"
    SchemaManager(database).initialize()
    application = SQLiteApplicationRepository(database)
    workers = WorkerIdentityRepository(database)
    token_id = uuid4()
    secret = "one-time-secret-that-must-never-be-persisted"
    digest = hashlib.sha256(secret.encode()).hexdigest()
    command = ApplicationCommand(
        command_type="worker.enrollment.create",
        actor_id="CTO",
        target_type="worker_enrollment",
        target_id=str(token_id),
        payload={"token_digest": digest},
    )

    def mutate(
        session: Session,
        _revision: int,
    ) -> tuple[dict[str, object], dict[str, object]]:
        expires_at = utc_now() + timedelta(minutes=10)
        workers.create_enrollment_token_in_session(
            session,
            token_id=token_id,
            token_digest=digest,
            actor_id="CTO",
            expires_at=expires_at,
        )
        result: dict[str, object] = {
            "token_id": str(token_id),
            "expires_at": expires_at.isoformat(),
        }
        return result, result

    first = application.accept_with_mutation(
        command,
        target_id=str(token_id),
        event_type="worker.enrollment_token_created",
        mutation=mutate,
    )
    replay = application.accept_with_mutation(
        command,
        target_id=str(token_id),
        event_type="worker.enrollment_token_created",
        mutation=mutate,
    )

    assert replay == first
    with Session(create_local_engine(database)) as session:
        token = session.get(WorkerEnrollmentTokenRow, str(token_id))
        assert token is not None and token.token_digest == digest
        rows = session.execute(
            text(
                "SELECT result_payload FROM application_commands "
                "UNION ALL SELECT payload FROM event_outbox"
            )
        ).scalars()
        persisted = "\n".join(rows)
    assert secret not in persisted
    assert digest not in persisted


def test_worker_revision_remains_aligned_with_application_command_authority(
    tmp_path: Path,
) -> None:
    service, workers, database = _service(tmp_path)
    worker_id, _, _bundle = _enroll(service)
    application = SQLiteApplicationRepository(database)
    command = ApplicationCommand(
        command_type="worker.revoke",
        actor_id="CTO",
        target_type="worker",
        target_id=str(worker_id),
        expected_revision=1,
        payload={},
    )

    def mutate(
        session: Session,
        _revision: int,
    ) -> tuple[dict[str, object], dict[str, object]]:
        identity = workers.revoke_worker_in_session(
            session,
            worker_id,
            expected_revision=1,
        )
        payload = identity.model_dump(mode="json")
        return payload, {"worker_id": str(worker_id), "revision": identity.revision}

    result = application.accept_with_mutation(
        command,
        target_id=str(worker_id),
        event_type="worker.revoked",
        mutation=mutate,
    )

    assert result.revision == 2
    assert result.payload["state"] == WorkerState.REVOKED.value
    assert workers.identity(worker_id).revision == 2
