"""Daemon-owned Unix PTY and managed-job supervision."""

from __future__ import annotations

import base64
import fcntl
import hashlib
import json
import os
import pty
import signal
import stat
import struct
import subprocess
import sys
import termios
import threading
import time
from collections.abc import Iterator, Mapping
from contextlib import contextmanager, suppress
from datetime import datetime
from pathlib import Path
from typing import Literal, Protocol
from uuid import UUID

import psutil  # type: ignore[import-untyped]
from sqlalchemy import select
from sqlalchemy.orm import Session

from mishkan.artifacts import ArtifactProvenance
from mishkan.artifacts.service import DurableArtifactService
from mishkan.config.models import SessionConfig, SessionProfileConfig
from mishkan.domain.errors import ErrorCode, MishkanError
from mishkan.domain.time import utc_now
from mishkan.execution.sessions import (
    CursorRead,
    ExecutionSession,
    SessionEffectSettlement,
    SessionMode,
    SessionRequest,
    SessionState,
)
from mishkan.persistence.migration import SchemaManager
from mishkan.persistence.runtime import DatabaseTarget
from mishkan.persistence.sqlite import ExecutionSessionRow, create_local_engine
from mishkan.tools.effects import (
    WorkspaceEffectEvidence,
    WorkspaceEffectObserver,
    WorkspaceSnapshot,
)
from mishkan.tools.execution import ExecutionResult, ExecutionStatus


class _SecretFilter:
    def __init__(self, secrets: tuple[str, ...]) -> None:
        self._secrets = tuple(secret.encode() for secret in secrets if secret)
        self._tail = b""

    def feed(self, chunk: bytes, *, final: bool = False) -> bytes:
        combined = self._tail + chunk
        for secret in self._secrets:
            combined = combined.replace(secret, b"[REDACTED]")
        if final:
            emit, self._tail = combined, b""
        else:
            keep = max(
                (
                    length
                    for secret in self._secrets
                    for length in range(1, len(secret))
                    if combined.endswith(secret[:length])
                ),
                default=0,
            )
            emit = combined[:-keep] if keep else combined
            self._tail = combined[-keep:] if keep else b""
        return emit


class SessionContentInspector(Protocol):
    def inspect(self, content: str, resolved_secrets: tuple[str, ...] = ()) -> str: ...


class SessionSupervisor:
    def __init__(
        self,
        database: DatabaseTarget,
        workspace: Path,
        spool_root: Path,
        config: SessionConfig,
        artifacts: DurableArtifactService,
        *,
        busy_timeout_ms: int = 5_000,
        content_inspector: SessionContentInspector | None = None,
    ) -> None:
        SchemaManager(database).require_current()
        self._workspace = workspace.resolve(strict=True)
        self._spool_root = spool_root.resolve()
        if not self._spool_root.is_relative_to(self._workspace):
            raise MishkanError(ErrorCode.AUTHORITY_NOT_GRANTED, "session spool escapes workspace")
        self._spool_root.mkdir(parents=True, exist_ok=True)
        self._config = config
        self._artifacts = artifacts
        self._content_inspector = content_inspector
        self._engine = create_local_engine(database, busy_timeout_ms=busy_timeout_ms)
        self._processes: dict[UUID, subprocess.Popen[bytes]] = {}
        self._pty_masters: dict[UUID, int] = {}
        self._threads: dict[UUID, tuple[threading.Thread, ...]] = {}
        self._monitors: dict[UUID, threading.Thread] = {}
        self._locks: dict[tuple[UUID, str], threading.Lock] = {}
        self._session_locks: dict[UUID, threading.RLock] = {}
        self._workspace_effects = WorkspaceEffectObserver()

    def start(
        self,
        request: SessionRequest,
        *,
        credential_values: Mapping[str, str] | None = None,
    ) -> ExecutionSession:
        if request.mode not in {SessionMode.PTY, SessionMode.JOB}:
            raise MishkanError(ErrorCode.EXECUTION, "session start requires PTY or job mode")
        assert request.session_profile is not None
        assert request.owner is not None
        assert request.run_id is not None
        assert request.task_id is not None
        assert request.deadline is not None
        profile = self._profile(request.session_profile)
        workspace = self._session_workspace(request.cwd)
        assert request.executable is not None
        executable = Path(request.executable)
        if not executable.is_absolute() or not executable.is_file():
            raise MishkanError(
                ErrorCode.EXECUTION, "session executable must be an existing absolute file"
            )
        resolved = dict(credential_values or {})
        environment_references = {
            name: reference
            for name, reference in request.credential_environment.items()
            if not isinstance(reference, str)
        }
        if len(environment_references) != len(request.credential_environment):
            raise MishkanError(ErrorCode.OUTPUT_CONTRACT, "session credential reference is invalid")
        required_locators = {
            *(reference.locator for reference in environment_references.values()),
            *(reference.locator for reference in request.credential_references),
        }
        if not required_locators.issubset(resolved):
            raise MishkanError(
                ErrorCode.AUTHORIZATION_MISSING,
                "session credential references were not resolved after authorization",
            )
        environment = dict(request.environment)
        environment.update(
            {
                name: resolved[reference.locator]
                for name, reference in environment_references.items()
            }
        )
        secret_values = tuple(resolved.values())
        serialized_request = request.model_dump_json()
        if (
            self._content_inspector is not None
            and self._content_inspector.inspect(serialized_request, secret_values)
            != serialized_request
        ):
            raise MishkanError(
                ErrorCode.SECRET_CONTENT,
                "session request requires redaction and cannot be executed faithfully",
            )
        sanitized_request = request.model_copy(
            update={
                "environment": {name: "[PRESENT]" for name in request.environment},
                "credential_environment": request.credential_environment,
            }
        )
        session_id = request.execution_id
        directory = self._spool_root / str(session_id)
        directory.mkdir(mode=0o700)
        stdout_spool = directory / "stdout.spool"
        stderr_spool = directory / "stderr.spool"
        stdout_writer, stdout_identity = self._create_spool(stdout_spool)
        try:
            stderr_writer, stderr_identity = self._create_spool(stderr_spool)
        except Exception:
            os.close(stdout_writer)
            raise
        before_state = self._declared_path_state(request)
        workspace_before = self._workspace_effects.snapshot(
            self._workspace,
            profile.effect_observation.model_dump(mode="python"),
        )
        now = utc_now()
        with Session(self._engine) as session, session.begin():
            session.add(
                ExecutionSessionRow(
                    id=str(session_id),
                    mode=request.mode.value,
                    state=SessionState.STARTING.value,
                    owner=request.owner,
                    run_id=request.run_id,
                    task_id=request.task_id,
                    workspace=request.cwd,
                    profile=request.session_profile,
                    request_payload=sanitized_request.model_dump_json(),
                    pid=None,
                    process_group_id=None,
                    process_create_time=None,
                    stdout_spool=str(stdout_spool.relative_to(self._spool_root)),
                    stderr_spool=str(stderr_spool.relative_to(self._spool_root)),
                    stdout_cursor=0,
                    stderr_cursor=0,
                    exit_code=None,
                    signal=None,
                    stdout_artifact_reference=None,
                    stderr_artifact_reference=None,
                    before_state_payload=json.dumps(
                        {
                            "declared_paths": before_state,
                            "workspace": self._snapshot_payload(workspace_before),
                            "spools": {
                                "stdout": stdout_identity,
                                "stderr": stderr_identity,
                            },
                        },
                        sort_keys=True,
                    ),
                    effect_evidence_payload="{}",
                    observed_effects_payload="[]",
                    produced_artifacts_payload="[]",
                    effect_settlement=None,
                    termination_cause=None,
                    retryable=False,
                    error=None,
                    cancellation_requested=False,
                    deadline=request.deadline.isoformat(),
                    started_at=now.isoformat(),
                    finished_at=None,
                    created_at=now.isoformat(),
                    updated_at=now.isoformat(),
                )
            )
        process: subprocess.Popen[bytes] | None = None
        release_descriptor: int | None = None
        pty_master: int | None = None
        try:
            try:
                if request.mode is SessionMode.PTY:
                    process, threads, release_descriptor, pty_master = self._start_pty(
                        session_id,
                        request,
                        workspace,
                        environment,
                        stdout_writer,
                        profile,
                        secret_values,
                    )
                else:
                    process, threads, release_descriptor = self._start_job(
                        session_id,
                        request,
                        workspace,
                        environment,
                        stdout_writer,
                        stderr_writer,
                        profile,
                        secret_values,
                    )
            finally:
                os.close(stdout_writer)
                os.close(stderr_writer)
            self._processes[session_id] = process
            self._threads[session_id] = threads
            if pty_master is not None:
                self._pty_masters[session_id] = pty_master
            identity = psutil.Process(process.pid)
            with Session(self._engine) as session, session.begin():
                current = session.get(ExecutionSessionRow, str(session_id))
                assert current is not None
                current.pid = process.pid
                current.process_group_id = os.getpgid(process.pid)
                current.process_create_time = identity.create_time()
                current.state = SessionState.RUNNING.value
                current.updated_at = utc_now().isoformat()
        except Exception as exc:
            self._abort_start(session_id, process, release_descriptor)
            raise MishkanError(
                ErrorCode.EXECUTION,
                "session target could not be launched after durable preparation",
            ) from exc
        assert process is not None and release_descriptor is not None
        try:
            os.write(release_descriptor, b"1")
        finally:
            os.close(release_descriptor)
        if request.mode is SessionMode.JOB and request.readiness is not None:
            self._await_readiness(session_id, request, profile)
        record = self.status(session_id)
        if record.state in {SessionState.RUNNING, SessionState.READY}:
            self._ensure_monitor(session_id)
        return record

    def write(
        self,
        session_id: UUID,
        content: bytes,
        *,
        declared_effects: tuple[str, ...] = (),
        network_destinations: tuple[str, ...] = (),
    ) -> int:
        descriptor = self._pty_masters.get(session_id)
        if descriptor is None:
            raise MishkanError(ErrorCode.EXECUTION, "PTY master is unavailable")
        row = self._require_live_identity(session_id)
        profile = self._profile(row.profile)
        if not content or len(content) > profile.max_input_bytes:
            raise MishkanError(
                ErrorCode.OUTPUT_CONTRACT,
                "PTY input is outside the configured bound",
                details={"bytes": len(content), "maximum": profile.max_input_bytes},
            )
        with Session(self._engine) as session, session.begin():
            current = session.get(ExecutionSessionRow, str(session_id))
            assert current is not None
            request = SessionRequest.model_validate_json(current.request_payload)
            current.request_payload = SessionRequest.model_validate(
                {
                    **request.model_dump(mode="python"),
                    "declared_effects": tuple(
                        sorted({*request.declared_effects, *declared_effects})
                    ),
                    "network_destinations": tuple(
                        sorted({*request.network_destinations, *network_destinations})
                    ),
                }
            ).model_dump_json()
            current.updated_at = utc_now().isoformat()
        return os.write(descriptor, content)

    def resize(self, session_id: UUID, *, rows: int, columns: int) -> None:
        descriptor = self._pty_masters.get(session_id)
        if descriptor is None:
            raise MishkanError(ErrorCode.EXECUTION, "PTY master is unavailable")
        row = self._require_live_identity(session_id)
        if row.mode != SessionMode.PTY.value or not (1 <= rows <= 1000 and 1 <= columns <= 4000):
            raise MishkanError(ErrorCode.OUTPUT_CONTRACT, "PTY resize request is invalid")
        fcntl.ioctl(descriptor, termios.TIOCSWINSZ, struct.pack("HHHH", rows, columns, 0, 0))

    def read(
        self,
        session_id: UUID,
        *,
        channel: Literal["stdout", "stderr"],
        offset: int,
        limit: int,
        binary: bool = False,
    ) -> CursorRead:
        if channel not in {"stdout", "stderr"} or offset < 0 or limit < 1 or limit > 16_777_216:
            raise MishkanError(ErrorCode.OUTPUT_CONTRACT, "session cursor request is invalid")
        row = self._row(session_id)
        size = self._spool_size(row, channel)
        if offset > size:
            raise MishkanError(
                ErrorCode.REVISION_MISMATCH,
                "session cursor is beyond the durable spool",
                details={"cursor": offset, "size": size},
            )
        content = self._spool_range(row, channel, offset, limit)
        encoding: Literal["utf-8", "base64"] = "base64" if binary else "utf-8"
        data = base64.b64encode(content).decode() if binary else content.decode(errors="replace")
        return CursorRead(
            execution_id=session_id,
            channel=channel,
            offset=offset,
            next_offset=offset + len(content),
            encoding=encoding,
            data=data,
            eof=offset + len(content) >= size
            and row.state
            in {
                SessionState.SETTLED.value,
                SessionState.FAILED.value,
                SessionState.LOST.value,
                SessionState.UNCERTAIN.value,
            },
        )

    def signal(self, session_id: UUID, signal_name: str) -> ExecutionSession:
        row = self._require_live_identity(session_id)
        profile = self._profile(row.profile)
        allowed = set(profile.cancellation_signals)
        if signal_name not in allowed:
            raise MishkanError(
                ErrorCode.AUTHORITY_NOT_GRANTED, "signal is not allowed by session profile"
            )
        signum = self._signal_number(signal_name)
        assert row.process_group_id is not None
        try:
            os.killpg(row.process_group_id, signum)
        except ProcessLookupError:
            return self.settle(session_id)
        return self._record(self._row(session_id))

    def cancel(self, session_id: UUID, *, cause: str = "cancelled") -> ExecutionSession:
        with self._session_lock(session_id):
            return self._cancel_locked(session_id, cause=cause)

    def _cancel_locked(self, session_id: UUID, *, cause: str) -> ExecutionSession:
        terminal: ExecutionSessionRow | None = None
        with Session(self._engine) as session, session.begin():
            row = session.get(ExecutionSessionRow, str(session_id))
            if row is None:
                raise MishkanError(ErrorCode.EXECUTION, "session does not exist")
            if row.finished_at is not None:
                session.expunge(row)
                terminal = row
                profile_name = row.profile
            else:
                row.cancellation_requested = True
                row.state = SessionState.CANCELLING.value
                row.termination_cause = cause
                row.updated_at = utc_now().isoformat()
                profile_name = row.profile
        if terminal is not None:
            return self._record(terminal)
        profile = self._profile(profile_name)
        for signal_name in profile.cancellation_signals:
            try:
                self.signal(session_id, signal_name)
            except MishkanError as error:
                if error.envelope.code is ErrorCode.RUN_INTERRUPTED:
                    break
                raise
            deadline = time.monotonic() + profile.grace_seconds
            while time.monotonic() < deadline:
                if not self._identity_matches(self._row(session_id)):
                    return self.settle(session_id)
                time.sleep(min(0.05, profile.grace_seconds))
        return self._settle_locked(session_id)

    def status(self, session_id: UUID) -> ExecutionSession:
        row = self._row(session_id)
        process = self._processes.get(session_id)
        if process is not None:
            returncode = process.poll()
            if (
                returncode is None
                and not row.cancellation_requested
                and utc_now() >= datetime.fromisoformat(row.deadline)
            ):
                return self.cancel(session_id, cause="timed_out")
            profile = self._profile(row.profile)
            resource_cause = self._resource_violation(row, profile)
            if returncode is None and resource_cause is not None:
                return self.cancel(session_id, cause=resource_cause)
            if not row.cancellation_requested:
                try:
                    output_limit = self._output_limit_reached(row, profile)
                except MishkanError:
                    return self.cancel(session_id, cause="spool_integrity")
                if output_limit:
                    return self.cancel(session_id, cause="output_limit")
            if returncode is not None and row.state not in {
                SessionState.SETTLED.value,
                SessionState.FAILED.value,
            }:
                return self.settle(session_id)
        elif row.state in {
            SessionState.STARTING.value,
            SessionState.RUNNING.value,
            SessionState.READY.value,
            SessionState.CANCELLING.value,
        }:
            if row.state == SessionState.STARTING.value:
                self._mark_start_interrupted(session_id)
                return self.settle(session_id)
            if row.cancellation_requested:
                return self.cancel(
                    session_id,
                    cause=row.termination_cause or "cancelled",
                )
            if not row.cancellation_requested and utc_now() >= datetime.fromisoformat(row.deadline):
                return self.cancel(session_id, cause="timed_out")
            profile = self._profile(row.profile)
            resource_cause = self._resource_violation(row, profile)
            if resource_cause is not None:
                return self.cancel(session_id, cause=resource_cause)
            try:
                output_limit = self._output_limit_reached(row, profile)
            except MishkanError:
                return self.cancel(session_id, cause="spool_integrity")
            if output_limit:
                return self.cancel(session_id, cause="output_limit")
            if row.mode == SessionMode.PTY.value:
                self._update_state(session_id, SessionState.LOST)
                return self.settle(session_id)
            elif not self._identity_matches(row):
                self._update_state(session_id, SessionState.UNCERTAIN)
                return self.settle(session_id)
        return self._record(self._row(session_id))

    def settle(self, session_id: UUID) -> ExecutionSession:
        with self._session_lock(session_id):
            return self._settle_locked(session_id)

    def _settle_locked(self, session_id: UUID) -> ExecutionSession:
        row = self._row(session_id)
        if row.finished_at is not None:
            return self._record(row)
        process = self._processes.get(session_id)
        if process is not None and process.poll() is None:
            return self._record(row)
        descriptor = self._pty_masters.pop(session_id, None)
        if descriptor is not None:
            os.close(descriptor)
        for thread in self._threads.get(session_id, ()):
            thread.join(timeout=self._profile(row.profile).settle_timeout_seconds)
        exit_code = process.returncode if process is not None else row.exit_code
        spool_error: str | None = None
        try:
            stdout = self._spool_bytes(row, "stdout")
            stderr = self._spool_bytes(row, "stderr")
        except MishkanError:
            stdout = b""
            stderr = b""
            spool_error = "spool_integrity_failed"
        stdout_reference = self._output_artifact(row, "stdout", stdout)
        stderr_reference = self._output_artifact(row, "stderr", stderr)
        request = SessionRequest.model_validate_json(row.request_payload)
        evidence_error: str | None = spool_error
        workspace_evidence: WorkspaceEffectEvidence | None = None
        effect_diff_reference: str | None = None
        try:
            workspace_evidence = self._workspace_effect_evidence(row, request)
            observed_effects = tuple(
                f"filesystem.change:{path}" for path in workspace_evidence.changed_paths
            )
            if workspace_evidence.changed_paths:
                effect_diff_reference = self._effect_diff_artifact(
                    row,
                    workspace_evidence,
                )
        except MishkanError:
            observed_effects = ()
            evidence_error = "effect_observation_failed"
        try:
            produced_artifacts = self._produced_artifacts(row, request)
        except MishkanError:
            produced_artifacts = ()
            evidence_error = evidence_error or "produced_artifact_capture_failed"
        settlement = (
            SessionEffectSettlement.UNCERTAIN
            if evidence_error is not None or workspace_evidence is None
            else self._effect_settlement(
                request,
                workspace_evidence,
                effect_diff_reference,
            )
        )
        effect_uncertain = settlement is SessionEffectSettlement.UNCERTAIN
        termination_cause = row.termination_cause
        if termination_cause is None and exit_code is not None and exit_code < 0:
            termination_cause = "signal_termination"
        failed = exit_code not in request.expected_exit_codes
        terminal_override = (
            SessionState(row.state)
            if row.state in {SessionState.LOST.value, SessionState.UNCERTAIN.value}
            else None
        )
        if terminal_override is SessionState.LOST:
            result_status = ExecutionStatus.LOST
            termination_cause = termination_cause or "pty_handle_lost"
            settlement = (
                SessionEffectSettlement.UNCERTAIN if request.declared_effects else settlement
            )
        elif terminal_override is SessionState.UNCERTAIN:
            result_status = ExecutionStatus.UNCERTAIN
            termination_cause = termination_cause or "process_identity_lost"
            settlement = SessionEffectSettlement.UNCERTAIN
        elif evidence_error is not None or effect_uncertain:
            result_status = ExecutionStatus.UNCERTAIN
        elif row.cancellation_requested:
            result_status = (
                ExecutionStatus.TIMED_OUT
                if termination_cause == "timed_out"
                else ExecutionStatus.CANCELLED
            )
        elif failed:
            result_status = ExecutionStatus.FAILED
        else:
            result_status = ExecutionStatus.COMPLETED
        error = (
            None
            if result_status is ExecutionStatus.COMPLETED
            else evidence_error or termination_cause
        )
        if error is None and failed:
            error = "unexpected_exit_code"
        finished_at = utc_now()
        retryable = (
            result_status is ExecutionStatus.FAILED and settlement is SessionEffectSettlement.ABSENT
        )
        with Session(self._engine) as session, session.begin():
            current = session.get(ExecutionSessionRow, str(session_id))
            assert current is not None
            current.state = (
                terminal_override.value
                if terminal_override is not None
                else SessionState.UNCERTAIN.value
                if evidence_error is not None or effect_uncertain
                else SessionState.SETTLED.value
                if row.cancellation_requested
                else SessionState.FAILED.value
                if failed
                else SessionState.SETTLED.value
            )
            current.exit_code = exit_code
            current.signal = -exit_code if exit_code is not None and exit_code < 0 else None
            current.stdout_cursor = len(stdout)
            current.stderr_cursor = len(stderr)
            current.stdout_artifact_reference = stdout_reference
            current.stderr_artifact_reference = stderr_reference
            current.observed_effects_payload = json.dumps(observed_effects)
            current.effect_evidence_payload = json.dumps(
                self._effect_evidence_payload(workspace_evidence, effect_diff_reference),
                sort_keys=True,
            )
            current.produced_artifacts_payload = json.dumps(produced_artifacts)
            current.effect_settlement = settlement.value
            current.termination_cause = termination_cause
            current.retryable = retryable
            current.error = error
            current.finished_at = finished_at.isoformat()
            current.updated_at = finished_at.isoformat()
        return self._record(self._row(session_id))

    def reconcile_all(self) -> tuple[ExecutionSession, ...]:
        with Session(self._engine) as session:
            identifiers = session.scalars(
                select(ExecutionSessionRow.id).where(
                    ExecutionSessionRow.state.in_(
                        (
                            SessionState.STARTING.value,
                            SessionState.RUNNING.value,
                            SessionState.READY.value,
                            SessionState.CANCELLING.value,
                        )
                    )
                )
            ).all()
        records = tuple(self.status(UUID(identifier)) for identifier in identifiers)
        for record in records:
            if record.state in {SessionState.RUNNING, SessionState.READY}:
                self._ensure_monitor(record.execution_id)
        return records

    def list(self, *, offset: int = 0, limit: int = 100) -> tuple[ExecutionSession, ...]:
        if offset < 0 or limit < 1 or limit > 1_000:
            raise MishkanError(ErrorCode.OUTPUT_CONTRACT, "session query bound is invalid")
        with Session(self._engine) as session:
            rows = session.scalars(
                select(ExecutionSessionRow)
                .order_by(ExecutionSessionRow.created_at, ExecutionSessionRow.id)
                .offset(offset)
                .limit(limit)
            ).all()
            return tuple(self._record(row) for row in rows)

    def _start_pty(
        self,
        session_id: UUID,
        request: SessionRequest,
        workspace: Path,
        environment: dict[str, str],
        spool_descriptor: int,
        profile: SessionProfileConfig,
        secrets: tuple[str, ...],
    ) -> tuple[subprocess.Popen[bytes], tuple[threading.Thread, ...], int, int]:
        assert request.executable is not None
        master, slave = pty.openpty()
        fcntl.ioctl(
            slave, termios.TIOCSWINSZ, struct.pack("HHHH", request.rows, request.columns, 0, 0)
        )
        try:
            process, release_descriptor = self._spawn_paused(
                request,
                workspace,
                environment,
                stdin=slave,
                stdout=slave,
                stderr=slave,
            )
        except Exception:
            os.close(master)
            raise
        finally:
            os.close(slave)
        thread = self._reader_thread(
            session_id,
            "stdout",
            master,
            spool_descriptor,
            secrets,
            profile,
            close_descriptor=False,
        )
        return process, (thread,), release_descriptor, master

    def _start_job(
        self,
        session_id: UUID,
        request: SessionRequest,
        workspace: Path,
        environment: dict[str, str],
        stdout_spool_descriptor: int,
        stderr_spool_descriptor: int,
        profile: SessionProfileConfig,
        secrets: tuple[str, ...],
    ) -> tuple[subprocess.Popen[bytes], tuple[threading.Thread, ...], int]:
        assert request.executable is not None
        process, release_descriptor = self._spawn_paused(
            request,
            workspace,
            environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        assert process.stdout is not None and process.stderr is not None
        stdout_descriptor = os.dup(process.stdout.fileno())
        stderr_descriptor = os.dup(process.stderr.fileno())
        process.stdout.close()
        process.stderr.close()
        stdout = self._reader_thread(
            session_id,
            "stdout",
            stdout_descriptor,
            stdout_spool_descriptor,
            secrets,
            profile,
        )
        stderr = self._reader_thread(
            session_id,
            "stderr",
            stderr_descriptor,
            stderr_spool_descriptor,
            secrets,
            profile,
        )
        return process, (stdout, stderr), release_descriptor

    @staticmethod
    def _spawn_paused(
        request: SessionRequest,
        workspace: Path,
        environment: dict[str, str],
        *,
        stdin: int,
        stdout: int,
        stderr: int,
    ) -> tuple[subprocess.Popen[bytes], int]:
        assert request.executable is not None
        control_read, control_write = os.pipe()
        try:
            process = subprocess.Popen(
                [
                    sys.executable,
                    str(Path(__file__).with_name("_launcher.py")),
                    str(control_read),
                    request.executable,
                    *request.args,
                ],
                cwd=workspace,
                env=environment,
                stdin=stdin,
                stdout=stdout,
                stderr=stderr,
                start_new_session=True,
                close_fds=True,
                pass_fds=(control_read,),
            )
        except Exception:
            os.close(control_write)
            raise
        finally:
            os.close(control_read)
        return process, control_write

    def _abort_start(
        self,
        session_id: UUID,
        process: subprocess.Popen[bytes] | None,
        release_descriptor: int | None,
    ) -> None:
        if release_descriptor is not None:
            with suppress(OSError):
                os.close(release_descriptor)
        if process is not None:
            with suppress(subprocess.TimeoutExpired):
                process.wait(timeout=1)
            if process.poll() is None:
                process.kill()
                process.wait()
        self._mark_start_interrupted(session_id)
        self.settle(session_id)

    def _mark_start_interrupted(self, session_id: UUID) -> None:
        with Session(self._engine) as session, session.begin():
            row = session.get(ExecutionSessionRow, str(session_id))
            if row is None or row.finished_at is not None:
                return
            row.state = SessionState.FAILED.value
            row.termination_cause = "launch_interrupted"
            row.error = "session launch did not cross its durable release gate"
            row.updated_at = utc_now().isoformat()

    def _reader_thread(
        self,
        session_id: UUID,
        channel: str,
        descriptor: int,
        spool_descriptor: int,
        secrets: tuple[str, ...],
        profile: SessionProfileConfig,
        *,
        close_descriptor: bool = True,
    ) -> threading.Thread:
        lock = self._locks.setdefault((session_id, channel), threading.Lock())
        durable_descriptor = os.dup(spool_descriptor)

        def reader() -> None:
            filtered = _SecretFilter(secrets)
            written = 0
            try:
                while written < profile.max_output_bytes:
                    try:
                        chunk = os.read(
                            descriptor,
                            min(profile.read_chunk_bytes, profile.max_output_bytes - written),
                        )
                    except OSError:
                        break
                    if not chunk:
                        break
                    clean = filtered.feed(chunk)
                    remaining = profile.max_output_bytes - written
                    clean = clean[:remaining]
                    with lock:
                        self._write_descriptor(durable_descriptor, clean)
                    written += len(clean)
                tail = filtered.feed(b"", final=True)
                remaining = profile.max_output_bytes - written
                if tail and remaining > 0:
                    with lock:
                        self._write_descriptor(durable_descriptor, tail[:remaining])
            finally:
                with suppress(OSError):
                    os.fsync(durable_descriptor)
                with suppress(OSError):
                    os.close(durable_descriptor)
                if close_descriptor:
                    with suppress(OSError):
                        os.close(descriptor)

        thread = threading.Thread(
            target=reader, name=f"mishkan-{session_id}-{channel}", daemon=True
        )
        thread.start()
        return thread

    def _ensure_monitor(self, session_id: UUID) -> None:
        with self._session_lock(session_id):
            existing = self._monitors.get(session_id)
            if existing is not None and existing.is_alive():
                return

            def monitor() -> None:
                while True:
                    try:
                        record = self.status(session_id)
                    except MishkanError:
                        return
                    if record.state not in {SessionState.RUNNING, SessionState.READY}:
                        return
                    profile = self._profile(record.profile)
                    time.sleep(profile.readiness_poll_seconds)

            thread = threading.Thread(
                target=monitor,
                name=f"mishkan-{session_id}-monitor",
                daemon=True,
            )
            self._monitors[session_id] = thread
            thread.start()

    def _session_lock(self, session_id: UUID) -> threading.RLock:
        return self._session_locks.setdefault(session_id, threading.RLock())

    def _await_readiness(
        self,
        session_id: UUID,
        request: SessionRequest,
        profile: SessionProfileConfig,
    ) -> None:
        assert request.readiness is not None
        deadline = time.monotonic() + request.readiness.timeout_seconds
        while time.monotonic() < deadline:
            process = self._processes[session_id]
            if process.poll() is not None:
                self.settle(session_id)
                return
            ready = request.readiness.kind == "process_running"
            if request.readiness.kind == "output_contains":
                value = (request.readiness.value or "").encode()
                row = self._row(session_id)
                ready = value in self._spool_bytes(row, "stdout")
            if ready:
                self._update_state(session_id, SessionState.READY)
                return
            time.sleep(profile.readiness_poll_seconds)
        self.cancel(session_id)

    def _require_live_identity(self, session_id: UUID) -> ExecutionSessionRow:
        row = self._row(session_id)
        if not self._identity_matches(row):
            raise MishkanError(
                ErrorCode.RUN_INTERRUPTED,
                "process identity cannot be proven; no signal was sent",
            )
        return row

    @staticmethod
    def _identity_matches(row: ExecutionSessionRow) -> bool:
        if row.pid is None or row.process_create_time is None or row.process_group_id is None:
            return False
        try:
            process = psutil.Process(row.pid)
            return (
                process.is_running()
                and process.status() != psutil.STATUS_ZOMBIE
                and abs(process.create_time() - row.process_create_time) < 0.000_001
                and os.getpgid(row.pid) == row.process_group_id
            )
        except (psutil.Error, OSError):
            return False

    @staticmethod
    def _resource_violation(row: ExecutionSessionRow, profile: SessionProfileConfig) -> str | None:
        if row.pid is None:
            return None
        try:
            process = psutil.Process(row.pid)
            members = [process, *process.children(recursive=True)]
            if profile.max_memory_mb is not None:
                resident = sum(member.memory_info().rss for member in members)
                if resident > profile.max_memory_mb * 1024 * 1024:
                    return "memory_limit"
            if profile.max_cpu_seconds is not None:
                cpu = sum(member.cpu_times().user + member.cpu_times().system for member in members)
                if cpu > profile.max_cpu_seconds:
                    return "cpu_limit"
        except psutil.Error:
            return None
        return None

    def _output_limit_reached(
        self,
        row: ExecutionSessionRow,
        profile: SessionProfileConfig,
    ) -> bool:
        return (
            self._spool_size(row, "stdout") >= profile.max_output_bytes
            or self._spool_size(row, "stderr") >= profile.max_output_bytes
        )

    def _row(self, session_id: UUID) -> ExecutionSessionRow:
        with Session(self._engine) as session:
            row = session.get(ExecutionSessionRow, str(session_id))
            if row is None:
                raise MishkanError(ErrorCode.EXECUTION, "session does not exist")
            session.expunge(row)
            return row

    def _record(self, row: ExecutionSessionRow) -> ExecutionSession:
        request = SessionRequest.model_validate_json(row.request_payload)
        profile = self._profile(row.profile)
        stdout_content = self._projection_spool_bytes(row, "stdout")
        stderr_content = self._projection_spool_bytes(row, "stderr")
        stdout_preview = stdout_content[-profile.preview_bytes :].decode(errors="replace")
        stderr_preview = stderr_content[-profile.preview_bytes :].decode(errors="replace")
        terminal = row.state in {
            SessionState.SETTLED.value,
            SessionState.FAILED.value,
            SessionState.LOST.value,
            SessionState.UNCERTAIN.value,
        }
        result: ExecutionResult | None = None
        if terminal:
            settlement = SessionEffectSettlement(
                row.effect_settlement
                or (
                    SessionEffectSettlement.UNCERTAIN.value
                    if request.declared_effects
                    else SessionEffectSettlement.ABSENT.value
                )
            )
            observed_effects = tuple(json.loads(row.observed_effects_payload or "[]"))
            produced_artifacts = tuple(json.loads(row.produced_artifacts_payload or "[]"))
            effect_evidence = json.loads(row.effect_evidence_payload or "{}")
            if not isinstance(effect_evidence, dict):
                raise MishkanError(ErrorCode.EXECUTION, "session effect evidence is invalid")
            status = self._execution_status(row)
            finished_at = datetime.fromisoformat(row.finished_at or row.updated_at)
            result = ExecutionResult(
                execution_id=UUID(row.id),
                mode=SessionMode(row.mode),
                status=status,
                executable=request.executable or "",
                args=request.args,
                cwd=row.workspace,
                exit_code=row.exit_code if row.exit_code is None or row.exit_code >= 0 else None,
                signal=row.signal,
                started_at=datetime.fromisoformat(row.started_at),
                finished_at=finished_at,
                stdout_preview=stdout_preview,
                stderr_preview=stderr_preview,
                stdout_bytes=len(stdout_content),
                stderr_bytes=len(stderr_content),
                stdout_digest=self._content_digest(stdout_content),
                stderr_digest=self._content_digest(stderr_content),
                stdout_artifact_ref=row.stdout_artifact_reference,
                stderr_artifact_ref=row.stderr_artifact_reference,
                produced_artifact_refs=produced_artifacts,
                truncated=row.termination_cause == "output_limit",
                termination_cause=row.termination_cause,
                expected_exit_codes=request.expected_exit_codes,
                environment_names=tuple(sorted(request.environment)),
                credential_environment_names=tuple(sorted(request.credential_environment)),
                declared_effects=request.declared_effects,
                observed_effects=observed_effects,
                effect_settlement=settlement,
                base_snapshot_fingerprint=effect_evidence.get("base_snapshot_fingerprint"),
                after_snapshot_fingerprint=effect_evidence.get("after_snapshot_fingerprint"),
                changed_paths=tuple(effect_evidence.get("changed_paths", ())),
                scope_deviations=tuple(effect_evidence.get("scope_deviations", ())),
                effect_diff_artifact_ref=effect_evidence.get("effect_diff_artifact_ref"),
                effect_observation_complete=bool(
                    effect_evidence.get("effect_observation_complete", False)
                ),
                effect_observation_omissions=tuple(
                    effect_evidence.get("effect_observation_omissions", ())
                ),
                effect_validation=str(effect_evidence.get("effect_validation", "not-observed")),
                retryable=row.retryable,
                execution_location="local",
                error=row.error or self._terminal_error(row),
            )
        return ExecutionSession(
            execution_id=UUID(row.id),
            mode=SessionMode(row.mode),
            state=SessionState(row.state),
            owner=row.owner,
            run_id=row.run_id,
            task_id=row.task_id,
            cwd=row.workspace,
            profile=row.profile,
            pid=row.pid,
            process_group_id=row.process_group_id,
            process_create_time=row.process_create_time,
            stdout_cursor=len(stdout_content),
            stderr_cursor=len(stderr_content),
            result=result,
            cancellation_requested=row.cancellation_requested,
            deadline=datetime.fromisoformat(row.deadline),
            created_at=datetime.fromisoformat(row.created_at),
            updated_at=datetime.fromisoformat(row.updated_at),
        )

    def _declared_path_state(self, request: SessionRequest) -> dict[str, object]:
        return {path: self._path_state(path) for path in request.declared_paths}

    def _workspace_effect_evidence(
        self,
        row: ExecutionSessionRow,
        request: SessionRequest,
    ) -> WorkspaceEffectEvidence:
        before_payload = json.loads(row.before_state_payload or "{}")
        workspace_payload = (
            before_payload.get("workspace") if isinstance(before_payload, dict) else None
        )
        if not isinstance(workspace_payload, dict):
            raise MishkanError(
                ErrorCode.EXECUTION,
                "session predates durable workspace effect evidence",
            )
        try:
            fingerprint = workspace_payload["fingerprint"]
            entries = workspace_payload["entries"]
            complete = workspace_payload["complete"]
            omissions = workspace_payload["omissions"]
            if not isinstance(fingerprint, str) or len(fingerprint) != 64:
                raise TypeError
            if not isinstance(entries, dict) or not all(
                isinstance(path, str) and isinstance(value, dict) for path, value in entries.items()
            ):
                raise TypeError
            if not isinstance(complete, bool) or not isinstance(omissions, list):
                raise TypeError
            if not all(isinstance(item, str) for item in omissions):
                raise TypeError
            before = WorkspaceSnapshot(
                fingerprint=fingerprint,
                entries=entries,
                complete=complete,
                omissions=tuple(omissions),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise MishkanError(
                ErrorCode.EXECUTION,
                "session workspace effect evidence is invalid",
            ) from exc
        profile = self._profile(row.profile)
        after = self._workspace_effects.snapshot(
            self._workspace,
            profile.effect_observation.model_dump(mode="python"),
        )
        return self._workspace_effects.compare(
            before,
            after,
            allowed_scopes=request.declared_paths,
        )

    def _produced_artifacts(
        self, row: ExecutionSessionRow, request: SessionRequest
    ) -> tuple[str, ...]:
        before_payload = json.loads(row.before_state_payload or "{}")
        before = (
            before_payload.get("declared_paths", {})
            if isinstance(before_payload, dict) and "declared_paths" in before_payload
            else before_payload
        )
        if not isinstance(before, dict):
            raise MishkanError(ErrorCode.EXECUTION, "session path evidence is invalid")
        references: list[str] = []
        for relative in request.declared_paths:
            state = self._path_state(relative)
            if state == before.get(relative) or not isinstance(state, dict):
                continue
            if state.get("kind") != "file":
                continue
            target = self._safe_declared_path(relative)
            references.append(
                self._artifacts.put_bytes(
                    self._read_declared_file(target, self._artifacts.max_artifact_bytes),
                    media_type="application/octet-stream",
                    provenance=ArtifactProvenance(
                        producer_identity=row.owner,
                        run_id=row.run_id,
                        task_attempt_id=row.task_id,
                        call_id=row.id,
                        capability=f"session.{row.mode}",
                        channel="produced",
                    ),
                    complete=True,
                    retention="session",
                ).reference
            )
        return tuple(references)

    @staticmethod
    def _effect_settlement(
        request: SessionRequest,
        evidence: WorkspaceEffectEvidence,
        diff_reference: str | None,
    ) -> SessionEffectSettlement:
        if (
            not evidence.complete
            or evidence.scope_deviations
            or (evidence.changed_paths and diff_reference is None)
        ):
            return SessionEffectSettlement.UNCERTAIN
        filesystem_only = bool(request.declared_effects) and all(
            effect.startswith("filesystem.") for effect in request.declared_effects
        )
        if not request.declared_effects:
            return (
                SessionEffectSettlement.UNCERTAIN
                if evidence.changed_paths
                else SessionEffectSettlement.ABSENT
            )
        if filesystem_only and request.declared_paths:
            return (
                SessionEffectSettlement.COMPLETED
                if evidence.changed_paths
                else SessionEffectSettlement.ABSENT
            )
        return SessionEffectSettlement.UNCERTAIN

    @staticmethod
    def _snapshot_payload(snapshot: WorkspaceSnapshot) -> dict[str, object]:
        return {
            "fingerprint": snapshot.fingerprint,
            "entries": snapshot.entries,
            "complete": snapshot.complete,
            "omissions": list(snapshot.omissions),
        }

    @staticmethod
    def _effect_evidence_payload(
        evidence: WorkspaceEffectEvidence | None,
        diff_reference: str | None,
    ) -> dict[str, object]:
        if evidence is None:
            return {}
        return {
            "base_snapshot_fingerprint": evidence.base_fingerprint,
            "after_snapshot_fingerprint": evidence.after_fingerprint,
            "changed_paths": list(evidence.changed_paths),
            "scope_deviations": list(evidence.scope_deviations),
            "effect_diff_artifact_ref": diff_reference,
            "effect_observation_complete": evidence.complete,
            "effect_observation_omissions": list(evidence.omissions),
            "effect_validation": (
                "bounded-before-after-snapshot"
                if evidence.complete and not evidence.scope_deviations
                else "incomplete-or-out-of-scope"
            ),
        }

    def _effect_diff_artifact(
        self,
        row: ExecutionSessionRow,
        evidence: WorkspaceEffectEvidence,
    ) -> str:
        return self._artifacts.put_bytes(
            evidence.diff,
            media_type="application/vnd.mishkan.workspace-diff+json",
            provenance=ArtifactProvenance(
                producer_identity=row.owner,
                run_id=row.run_id,
                task_attempt_id=row.task_id,
                call_id=row.id,
                capability=f"session.{row.mode}",
                channel="effect-diff",
            ),
            complete=True,
            retention="session",
        ).reference

    def _path_state(self, relative: str) -> dict[str, object]:
        target = self._safe_declared_path(relative)
        if not target.exists():
            return {"kind": "absent"}
        try:
            descriptor = os.open(target, os.O_RDONLY | os.O_NOFOLLOW)
        except FileNotFoundError:
            return {"kind": "absent"}
        except OSError as exc:
            raise MishkanError(
                ErrorCode.AUTHORITY_NOT_GRANTED,
                "declared execution path could not be inspected without following links",
            ) from exc
        try:
            metadata = os.fstat(descriptor)
            if stat.S_ISREG(metadata.st_mode):
                digest = hashlib.sha256()
                size = 0
                with os.fdopen(os.dup(descriptor), "rb") as stream:
                    for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                        digest.update(chunk)
                        size += len(chunk)
                return {"kind": "file", "size": size, "digest": digest.hexdigest()}
            if stat.S_ISDIR(metadata.st_mode):
                entries = tuple(sorted(os.listdir(descriptor)))
                return {"kind": "directory", "entries": entries}
            return {"kind": "special"}
        finally:
            os.close(descriptor)

    @staticmethod
    def _read_declared_file(target: Path, limit: int) -> bytes:
        try:
            descriptor = os.open(target, os.O_RDONLY | os.O_NOFOLLOW)
        except OSError as exc:
            raise MishkanError(
                ErrorCode.AUTHORITY_NOT_GRANTED,
                "produced artifact path could not be opened without following links",
            ) from exc
        try:
            if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                raise MishkanError(ErrorCode.ARTIFACT, "produced artifact is not a regular file")
            with os.fdopen(os.dup(descriptor), "rb") as stream:
                content = stream.read(limit + 1)
            if len(content) > limit:
                raise MishkanError(
                    ErrorCode.ARTIFACT,
                    "produced artifact exceeds the configured artifact bound",
                )
            return content
        finally:
            os.close(descriptor)

    def _safe_declared_path(self, relative: str) -> Path:
        candidate = self._workspace / relative
        if not candidate.is_relative_to(self._workspace):
            raise MishkanError(ErrorCode.AUTHORITY_NOT_GRANTED, "execution path escaped workspace")
        parent = candidate.parent.resolve(strict=False)
        if not parent.is_relative_to(self._workspace) or candidate.is_symlink():
            raise MishkanError(
                ErrorCode.AUTHORITY_NOT_GRANTED,
                "execution path escaped workspace through a symbolic link",
            )
        return candidate

    @staticmethod
    def _execution_status(row: ExecutionSessionRow) -> ExecutionStatus:
        if row.state == SessionState.LOST.value:
            return ExecutionStatus.LOST
        if row.state == SessionState.UNCERTAIN.value:
            return ExecutionStatus.UNCERTAIN
        if row.termination_cause == "timed_out":
            return ExecutionStatus.TIMED_OUT
        if row.cancellation_requested:
            return ExecutionStatus.CANCELLED
        return (
            ExecutionStatus.COMPLETED
            if row.state == SessionState.SETTLED.value
            else ExecutionStatus.FAILED
        )

    @staticmethod
    def _terminal_error(row: ExecutionSessionRow) -> str | None:
        if row.state == SessionState.FAILED.value:
            return "execution exited unsuccessfully"
        if row.state == SessionState.LOST.value:
            return "PTY live handle was lost"
        if row.state == SessionState.UNCERTAIN.value:
            return "process identity or external effects could not be reconciled"
        return None

    @staticmethod
    def _create_spool(path: Path) -> tuple[int, dict[str, int]]:
        flags = os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC
        flags |= getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(path, flags, 0o600)
        except OSError as exc:
            raise MishkanError(
                ErrorCode.EXECUTION, "session spool could not be created safely"
            ) from exc
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
            os.close(descriptor)
            raise MishkanError(ErrorCode.EXECUTION, "session spool identity is unsafe")
        return descriptor, {"device": metadata.st_dev, "inode": metadata.st_ino}

    @staticmethod
    def _write_descriptor(descriptor: int, content: bytes) -> None:
        view = memoryview(content)
        while view:
            written = os.write(descriptor, view)
            if written < 1:
                raise OSError("session spool write made no progress")
            view = view[written:]

    def _spool_path(self, row: ExecutionSessionRow, channel: str) -> Path:
        relative = row.stdout_spool if channel == "stdout" else row.stderr_spool
        path = self._spool_root / relative
        parent = path.parent.resolve(strict=True)
        if not parent.is_relative_to(self._spool_root) or path.name not in {
            "stdout.spool",
            "stderr.spool",
        }:
            raise MishkanError(ErrorCode.EXECUTION, "session spool path escaped its root")
        return path

    def _spool_identity(self, row: ExecutionSessionRow, channel: str) -> tuple[int, int]:
        try:
            before = json.loads(row.before_state_payload)
            spools = before["spools"]
            identity = spools[channel]
            device = identity["device"]
            inode = identity["inode"]
            if (
                not isinstance(device, int)
                or isinstance(device, bool)
                or not isinstance(inode, int)
                or isinstance(inode, bool)
            ):
                raise TypeError
            return device, inode
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise MishkanError(
                ErrorCode.EXECUTION,
                "session spool identity is unavailable",
            ) from exc

    @contextmanager
    def _verified_spool(
        self, row: ExecutionSessionRow, channel: str
    ) -> Iterator[tuple[int, os.stat_result]]:
        expected = self._spool_identity(row, channel)
        path = self._spool_path(row, channel)
        try:
            descriptor = os.open(
                path,
                os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0),
            )
        except OSError as exc:
            raise MishkanError(
                ErrorCode.EXECUTION,
                "session spool could not be reopened without following links",
            ) from exc
        try:
            metadata = os.fstat(descriptor)
            if (
                not stat.S_ISREG(metadata.st_mode)
                or metadata.st_nlink != 1
                or (metadata.st_dev, metadata.st_ino) != expected
            ):
                raise MishkanError(
                    ErrorCode.EXECUTION,
                    "session spool identity changed after creation",
                )
            yield descriptor, metadata
        finally:
            os.close(descriptor)

    def _spool_size(self, row: ExecutionSessionRow, channel: str) -> int:
        with self._verified_spool(row, channel) as (_, metadata):
            return metadata.st_size

    def _spool_bytes(self, row: ExecutionSessionRow, channel: str) -> bytes:
        with self._verified_spool(row, channel) as (descriptor, metadata):
            content = os.pread(descriptor, metadata.st_size, 0)
            after = os.fstat(descriptor)
        if len(content) != metadata.st_size:
            raise MishkanError(ErrorCode.EXECUTION, "session spool changed while being read")
        if after.st_size < metadata.st_size:
            raise MishkanError(ErrorCode.EXECUTION, "session spool was truncated while being read")
        return content

    def _spool_range(
        self,
        row: ExecutionSessionRow,
        channel: str,
        offset: int,
        limit: int,
    ) -> bytes:
        with self._verified_spool(row, channel) as (descriptor, _):
            return os.pread(descriptor, limit, offset)

    def _projection_spool_bytes(self, row: ExecutionSessionRow, channel: str) -> bytes:
        try:
            return self._spool_bytes(row, channel)
        except MishkanError:
            reference = (
                row.stdout_artifact_reference
                if channel == "stdout"
                else row.stderr_artifact_reference
            )
            if reference is not None:
                return self._artifacts.read_bytes(reference)
            return b""

    @staticmethod
    def _content_digest(content: bytes) -> str:
        return f"sha256:{hashlib.sha256(content).hexdigest()}"

    def _session_workspace(self, relative: str) -> Path:
        path = (self._workspace / relative).resolve(strict=True)
        if not path.is_relative_to(self._workspace):
            raise MishkanError(ErrorCode.AUTHORITY_NOT_GRANTED, "session workspace escaped project")
        return path

    def _profile(self, name: str) -> SessionProfileConfig:
        profile = self._config.profiles.get(name)
        if profile is None:
            raise MishkanError(ErrorCode.CONFIGURATION, "session profile does not exist")
        return profile

    @staticmethod
    def _signal_number(name: str) -> int:
        try:
            return int(getattr(signal, f"SIG{name}"))
        except (AttributeError, ValueError) as exc:
            raise MishkanError(
                ErrorCode.CONFIGURATION, "session profile signal is unsupported"
            ) from exc

    def _update_state(self, session_id: UUID, state: SessionState) -> None:
        with Session(self._engine) as session, session.begin():
            row = session.get(ExecutionSessionRow, str(session_id))
            assert row is not None
            row.state = state.value
            row.updated_at = utc_now().isoformat()

    def _output_artifact(self, row: ExecutionSessionRow, channel: str, content: bytes) -> str:
        return self._artifacts.put_bytes(
            content,
            media_type="application/octet-stream",
            provenance=ArtifactProvenance(
                producer_identity=row.owner,
                run_id=row.run_id,
                task_attempt_id=row.task_id,
                call_id=row.id,
                capability=f"session.{row.mode}",
                channel=channel,
            ),
            complete=True,
            retention="session",
        ).reference
