"""Transactional authority for worker enrollment and certificate state."""

from __future__ import annotations

import json
from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from mishkan.domain.errors import ErrorCode, MishkanError
from mishkan.domain.identity import new_id
from mishkan.domain.time import require_aware, utc_now
from mishkan.persistence.migration import SchemaManager
from mishkan.persistence.runtime import DatabaseTarget
from mishkan.persistence.sqlite import (
    OutboxRow,
    WorkerCertificateRow,
    WorkerEnrollmentTokenRow,
    WorkerIdentityRow,
    create_local_engine,
)
from mishkan.workers.models import CertificateState, EnrollmentState, WorkerIdentity, WorkerState
from mishkan.workers.pki import SignedWorkerCertificate


class WorkerIdentityRepository:
    def __init__(self, database: DatabaseTarget, *, busy_timeout_ms: int = 5_000) -> None:
        SchemaManager(database).require_current()
        self._engine = create_local_engine(database, busy_timeout_ms=busy_timeout_ms)

    def create_enrollment_token(
        self,
        *,
        token_id: UUID,
        token_digest: str,
        actor_id: str,
        expires_at: datetime,
    ) -> None:
        now = utc_now()
        expiry = require_aware(expires_at)
        if expiry <= now:
            raise MishkanError(ErrorCode.WORKER, "worker enrollment token expiry is invalid")
        try:
            with Session(self._engine) as session, session.begin():
                session.add(
                    WorkerEnrollmentTokenRow(
                        id=str(token_id),
                        token_digest=token_digest,
                        state=EnrollmentState.ACTIVE.value,
                        created_by=actor_id,
                        worker_id=None,
                        expires_at=expiry.isoformat(),
                        created_at=now.isoformat(),
                        consumed_at=None,
                        revoked_at=None,
                    )
                )
                self._event(
                    session,
                    aggregate_id=str(token_id),
                    entity_type="worker_enrollment",
                    event_type="worker.enrollment_token_created",
                    payload={"token_id": str(token_id), "expires_at": expiry.isoformat()},
                )
        except IntegrityError as exc:
            raise MishkanError(ErrorCode.WORKER, "worker enrollment token already exists") from exc

    def revoke_enrollment_token(self, token_id: UUID) -> None:
        with Session(self._engine) as session, session.begin():
            row = session.get(WorkerEnrollmentTokenRow, str(token_id))
            if row is None:
                raise MishkanError(ErrorCode.WORKER, "worker enrollment token does not exist")
            if row.state == EnrollmentState.CONSUMED.value:
                raise MishkanError(ErrorCode.WORKER, "consumed enrollment token cannot be revoked")
            if row.state == EnrollmentState.REVOKED.value:
                return
            now = utc_now()
            row.state = EnrollmentState.REVOKED.value
            row.revoked_at = now.isoformat()
            self._event(
                session,
                aggregate_id=row.id,
                entity_type="worker_enrollment",
                event_type="worker.enrollment_token_revoked",
                payload={"token_id": row.id},
            )

    def enroll(
        self,
        *,
        token_digest: str,
        worker_id: UUID,
        display_name: str,
        certificate: SignedWorkerCertificate,
    ) -> WorkerIdentity:
        now = utc_now()
        with Session(self._engine) as session:
            with session.begin():
                token = session.scalar(
                    select(WorkerEnrollmentTokenRow)
                    .where(WorkerEnrollmentTokenRow.token_digest == token_digest)
                    .with_for_update()
                )
                if token is None:
                    raise MishkanError(ErrorCode.WORKER, "worker enrollment token is invalid")
                if token.state != EnrollmentState.ACTIVE.value:
                    raise MishkanError(
                        ErrorCode.WORKER,
                        "worker enrollment token is not active",
                        details={"state": token.state},
                    )
                if datetime.fromisoformat(token.expires_at) <= now:
                    token.state = EnrollmentState.EXPIRED.value
                    session.flush()
                    expired = True
                else:
                    expired = False
                if expired:
                    # Commit the terminal token state before returning the refusal.
                    continue_after_commit = True
                else:
                    continue_after_commit = False
                    if session.get(WorkerIdentityRow, str(worker_id)) is not None:
                        raise MishkanError(ErrorCode.WORKER, "worker identity already exists")
                    worker = WorkerIdentityRow(
                        id=str(worker_id),
                        display_name=display_name,
                        state=WorkerState.ACTIVE.value,
                        current_certificate_serial=certificate.serial,
                        revision=1,
                        created_at=now.isoformat(),
                        updated_at=now.isoformat(),
                        revoked_at=None,
                    )
                    session.add(worker)
                    # No ORM relationship is declared between authority rows; flush the
                    # identity before its certificate and consumed-token foreign keys.
                    session.flush()
                    session.add(self._certificate_row(worker_id, certificate))
                    token.state = EnrollmentState.CONSUMED.value
                    token.worker_id = str(worker_id)
                    token.consumed_at = now.isoformat()
                    self._event(
                        session,
                        aggregate_id=str(worker_id),
                        entity_type="worker",
                        event_type="worker.enrolled",
                        payload={
                            "worker_id": str(worker_id),
                            "certificate_serial": certificate.serial,
                        },
                    )
            if continue_after_commit:
                raise MishkanError(ErrorCode.WORKER, "worker enrollment token has expired")
        return self.identity(worker_id)

    def rotate(
        self,
        *,
        worker_id: UUID,
        current_serial: str,
        certificate: SignedWorkerCertificate,
    ) -> WorkerIdentity:
        now = utc_now()
        with Session(self._engine) as session, session.begin():
            worker = session.scalar(
                select(WorkerIdentityRow)
                .where(WorkerIdentityRow.id == str(worker_id))
                .with_for_update()
            )
            if worker is None or worker.state != WorkerState.ACTIVE.value:
                raise MishkanError(ErrorCode.WORKER, "worker identity is not active")
            if worker.current_certificate_serial != current_serial:
                raise MishkanError(ErrorCode.WORKER, "worker certificate is no longer current")
            previous = session.get(WorkerCertificateRow, current_serial)
            if previous is None or previous.state != CertificateState.CURRENT.value:
                raise MishkanError(ErrorCode.WORKER, "worker certificate state is inconsistent")
            previous.state = CertificateState.SUPERSEDED.value
            previous.superseded_at = now.isoformat()
            session.add(self._certificate_row(worker_id, certificate))
            worker.current_certificate_serial = certificate.serial
            worker.revision += 1
            worker.updated_at = now.isoformat()
            self._event(
                session,
                aggregate_id=str(worker_id),
                entity_type="worker",
                event_type="worker.certificate_rotated",
                payload={
                    "worker_id": str(worker_id),
                    "previous_serial": current_serial,
                    "certificate_serial": certificate.serial,
                },
            )
        return self.identity(worker_id)

    def revoke_worker(self, worker_id: UUID, *, expected_revision: int) -> WorkerIdentity:
        now = utc_now()
        with Session(self._engine) as session, session.begin():
            worker = session.scalar(
                select(WorkerIdentityRow)
                .where(WorkerIdentityRow.id == str(worker_id))
                .with_for_update()
            )
            if worker is None:
                raise MishkanError(ErrorCode.WORKER, "worker identity does not exist")
            if worker.revision != expected_revision:
                raise MishkanError(
                    ErrorCode.REVISION_MISMATCH,
                    "worker identity revision changed",
                    details={"expected": expected_revision, "current": worker.revision},
                )
            if worker.state == WorkerState.REVOKED.value:
                return self._identity(worker)
            worker.state = WorkerState.REVOKED.value
            worker.revision += 1
            worker.updated_at = now.isoformat()
            worker.revoked_at = now.isoformat()
            certificates = session.scalars(
                select(WorkerCertificateRow).where(
                    WorkerCertificateRow.worker_id == str(worker_id),
                    WorkerCertificateRow.state != CertificateState.REVOKED.value,
                )
            ).all()
            for certificate in certificates:
                certificate.state = CertificateState.REVOKED.value
                certificate.revoked_at = now.isoformat()
            self._event(
                session,
                aggregate_id=str(worker_id),
                entity_type="worker",
                event_type="worker.revoked",
                payload={"worker_id": str(worker_id)},
            )
        return self.identity(worker_id)

    def authenticate(self, worker_id: UUID, serial: str, fingerprint: str) -> WorkerIdentity:
        now = utc_now()
        with Session(self._engine) as session:
            worker = session.get(WorkerIdentityRow, str(worker_id))
            certificate = session.get(WorkerCertificateRow, serial)
            if (
                worker is None
                or worker.state != WorkerState.ACTIVE.value
                or worker.current_certificate_serial != serial
                or certificate is None
                or certificate.worker_id != str(worker_id)
                or certificate.state != CertificateState.CURRENT.value
                or certificate.fingerprint != fingerprint
                or datetime.fromisoformat(certificate.not_before) > now
                or datetime.fromisoformat(certificate.not_after) <= now
            ):
                raise MishkanError(ErrorCode.WORKER, "worker certificate is not active")
            return self._identity(worker)

    def identity(self, worker_id: UUID) -> WorkerIdentity:
        with Session(self._engine) as session:
            row = session.get(WorkerIdentityRow, str(worker_id))
            if row is None:
                raise MishkanError(ErrorCode.WORKER, "worker identity does not exist")
            return self._identity(row)

    @staticmethod
    def _certificate_row(
        worker_id: UUID,
        certificate: SignedWorkerCertificate,
    ) -> WorkerCertificateRow:
        return WorkerCertificateRow(
            serial=certificate.serial,
            worker_id=str(worker_id),
            state=CertificateState.CURRENT.value,
            fingerprint=certificate.fingerprint_sha256,
            not_before=certificate.not_before.isoformat(),
            not_after=certificate.not_after.isoformat(),
            issued_at=utc_now().isoformat(),
            superseded_at=None,
            revoked_at=None,
        )

    @staticmethod
    def _identity(row: WorkerIdentityRow) -> WorkerIdentity:
        return WorkerIdentity(
            id=UUID(row.id),
            display_name=row.display_name,
            state=WorkerState(row.state),
            current_certificate_serial=row.current_certificate_serial,
            revision=row.revision,
            created_at=datetime.fromisoformat(row.created_at),
            updated_at=datetime.fromisoformat(row.updated_at),
            revoked_at=(datetime.fromisoformat(row.revoked_at) if row.revoked_at else None),
        )

    @staticmethod
    def _event(
        session: Session,
        *,
        aggregate_id: str,
        entity_type: str,
        event_type: str,
        payload: dict[str, str],
    ) -> None:
        session.add(
            OutboxRow(
                id=str(new_id()),
                schema_version="1.0",
                aggregate_id=aggregate_id,
                entity_type=entity_type,
                run_id=None,
                task_id=None,
                identity_id=aggregate_id if entity_type == "worker" else None,
                team_id=None,
                security_relevant=True,
                event_type=event_type,
                source="mishkan.worker_identity",
                payload=json.dumps(payload, sort_keys=True, separators=(",", ":")),
                occurred_at=utc_now().isoformat(),
                command_id=None,
                correlation_id=None,
                causation_id=None,
                sensitivity="internal",
                published_at=None,
            )
        )
