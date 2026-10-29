"""Durable Browser session supervisor with observation-bound interactions."""

from __future__ import annotations

import io
import json
import os
import stat
import tarfile
import threading
from collections.abc import Callable, Mapping
from contextlib import suppress
from datetime import timedelta
from fnmatch import fnmatchcase
from pathlib import Path
from uuid import UUID

import httpx
from pydantic import AnyHttpUrl, ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from mishkan.artifacts import ArtifactProvenance
from mishkan.artifacts.service import DurableArtifactService
from mishkan.browser.driver import (
    BrowserDriver,
    BrowserOperationCancelled,
    BrowserUncertainEffect,
    DriverArtifact,
)
from mishkan.browser.models import (
    BrowserActionRequest,
    BrowserActionResult,
    BrowserActionState,
    BrowserDiagnosticRequest,
    BrowserDiagnosticResult,
    BrowserObservation,
    BrowserObservationRequest,
    BrowserSession,
    BrowserSessionRequest,
    BrowserSessionState,
    BrowserTarget,
)
from mishkan.config.models import BrowserConfig, BrowserProfileConfig, BrowserProfileKind
from mishkan.domain.errors import ErrorCode, MishkanError
from mishkan.domain.time import utc_now
from mishkan.persistence.migration import SchemaManager
from mishkan.persistence.runtime import DatabaseTarget
from mishkan.persistence.sqlite import (
    BrowserActionRow,
    BrowserObservationRow,
    BrowserSessionRow,
    create_local_engine,
)
from mishkan.tools.inspection import ContentInspector


class BrowserSupervisor:
    def __init__(
        self,
        database: DatabaseTarget,
        workspace: Path,
        config: BrowserConfig,
        artifacts: DurableArtifactService,
        drivers: dict[str, BrowserDriver],
        inspector: ContentInspector,
        *,
        busy_timeout_ms: int = 5_000,
    ) -> None:
        SchemaManager(database).require_current()
        self._workspace = workspace.resolve()
        self._config = config
        self._artifacts = artifacts
        self._drivers = dict(drivers)
        self._inspector = inspector
        self._handles: dict[UUID, tuple[BrowserDriver, str]] = {}
        self._session_secrets: dict[UUID, set[str]] = {}
        self._session_locks: dict[UUID, threading.RLock] = {}
        self._session_locks_guard = threading.Lock()
        self._engine = create_local_engine(database, busy_timeout_ms=busy_timeout_ms)

    def profile(self, profile_id: str) -> BrowserProfileConfig:
        try:
            return self._config.profiles[profile_id]
        except KeyError as exc:
            raise MishkanError(ErrorCode.BROWSER, "browser profile is not configured") from exc

    def open(self, request: BrowserSessionRequest) -> BrowserSession:
        try:
            profile = self._config.profiles[request.profile_id]
        except KeyError as exc:
            raise MishkanError(ErrorCode.BROWSER, "browser profile is not configured") from exc
        driver = self._drivers.get(profile.adapter)
        if driver is None:
            raise MishkanError(
                ErrorCode.REQUIRED_DEPENDENCY,
                "configured browser adapter is unavailable",
                details={"adapter": profile.adapter},
            )
        if (
            profile.kind is BrowserProfileKind.ATTACHED_EXISTING
            and not request.attached_profile_selected
        ):
            raise MishkanError(
                ErrorCode.AUTHORIZATION_MISSING,
                "attached browser profile requires explicit selection",
            )
        workspace = self._resolve_workspace(request.workspace)
        if profile.kind is BrowserProfileKind.PROJECT_PERSISTENT:
            if workspace != self._workspace:
                raise MishkanError(
                    ErrorCode.AUTHORITY_NOT_GRANTED,
                    "persistent browser profiles require the project workspace root",
                )
            self._restore_profile_state(request.profile_id, profile, workspace)
        initial_url = str(request.initial_url) if request.initial_url else None
        if initial_url is not None:
            self._require_origin(profile.allowed_origins, initial_url)
        now = utc_now()
        opening = BrowserSession(
            profile_id=request.profile_id,
            profile_kind=profile.kind,
            owner_identity=request.owner_identity,
            run_id=request.run_id,
            task_attempt_id=request.task_attempt_id,
            workspace=workspace.relative_to(self._workspace).as_posix() or ".",
            adapter=profile.adapter,
            engine=profile.engine,
            engine_version="unavailable",
            state=BrowserSessionState.OPENING,
            revision=0,
            sensitivity=profile.sensitivity,
            retention=profile.retention,
            created_at=now,
            updated_at=now,
        )
        self._insert_session(opening)
        try:
            observed = driver.open(
                profile,
                workspace=str(workspace),
                initial_url=initial_url,
            )
        except BrowserUncertainEffect as exc:
            uncertain = opening.model_copy(
                update={
                    "state": BrowserSessionState.UNCERTAIN,
                    "revision": 1,
                    "uncertain_effect": "browser.session.open",
                    "last_error": str(exc),
                    "updated_at": utc_now(),
                }
            )
            self._replace_session(uncertain, expected_revision=0)
            return uncertain
        except Exception as exc:
            state = (
                BrowserSessionState.UNCERTAIN
                if profile.kind is BrowserProfileKind.PROJECT_PERSISTENT
                else BrowserSessionState.FAILED
            )
            failed = opening.model_copy(
                update={
                    "state": state,
                    "revision": 1,
                    "last_error": type(exc).__name__,
                    "uncertain_effect": (
                        "browser.profile.write" if state is BrowserSessionState.UNCERTAIN else None
                    ),
                    "updated_at": utc_now(),
                }
            )
            self._replace_session(failed, expected_revision=0)
            return failed
        active = opening.model_copy(
            update={
                "state": BrowserSessionState.ACTIVE,
                "revision": 1,
                "page_ids": observed.page_ids,
                "engine_version": observed.engine_version,
                "updated_at": utc_now(),
            }
        )
        self._replace_session(active, expected_revision=0)
        self._handles[active.id] = (driver, observed.handle)
        return active

    def get(self, session_id: UUID, *, owner_identity: str) -> BrowserSession:
        value = self._load_session(session_id)
        self._require_owner(value, owner_identity)
        return value

    def list(
        self,
        *,
        owner_identity: str,
        offset: int = 0,
        limit: int = 100,
    ) -> tuple[BrowserSession, ...]:
        with Session(self._engine) as session:
            rows = session.scalars(
                select(BrowserSessionRow)
                .where(BrowserSessionRow.owner_identity == owner_identity)
                .order_by(BrowserSessionRow.created_at.desc())
                .offset(offset)
                .limit(limit)
            ).all()
        return tuple(BrowserSession.model_validate_json(row.payload) for row in rows)

    def observe(
        self,
        request: BrowserObservationRequest,
        *,
        owner_identity: str,
    ) -> BrowserObservation:
        with self._session_lock(request.session_id):
            return self._observe(request, owner_identity=owner_identity)

    def _observe(
        self,
        request: BrowserObservationRequest,
        *,
        owner_identity: str,
    ) -> BrowserObservation:
        browser = self._active(request.session_id, owner_identity)
        self._require_revision(browser, request.expected_session_revision)
        if request.page_id not in browser.page_ids:
            raise MishkanError(ErrorCode.BROWSER, "browser page is not owned by the session")
        driver, handle = self._handle(browser)
        observed = driver.observe(handle, request.page_id, screenshot=request.include_screenshot)
        if len(observed.tree) > self._config.max_observation_bytes:
            raise MishkanError(
                ErrorCode.BROWSER,
                "browser observation exceeds its configured bound",
            )
        self._require_origin(
            self._config.profiles[browser.profile_id].allowed_origins,
            observed.url,
        )
        secrets = tuple(self._session_secrets.get(browser.id, ()))
        clean_tree = self._inspector.inspect(
            observed.tree.decode(errors="replace"),
            secrets,
        ).encode()
        clean_targets = tuple(
            target.model_copy(update={"name": self._inspector.inspect(target.name, secrets)})
            for target in observed.targets
        )
        clean_title = self._inspector.inspect(observed.title, secrets)
        tree = self._artifact(browser, "browser.tree", "text/yaml", clean_tree)
        screenshot = (
            self._artifact(browser, "browser.screenshot", "image/png", observed.screenshot)
            if observed.screenshot is not None
            else None
        )
        now = utc_now()
        observation = BrowserObservation(
            session_id=browser.id,
            page_id=request.page_id,
            session_revision=browser.revision,
            url=AnyHttpUrl(observed.url),
            title=clean_title,
            targets=clean_targets,
            tree_artifact_reference=tree,
            screenshot_artifact_reference=screenshot,
            engine=browser.engine,
            engine_version=browser.engine_version,
            created_at=now,
            expires_at=now + timedelta(seconds=self._config.observation_ttl_seconds),
        )
        with Session(self._engine) as session, session.begin():
            session.add(
                BrowserObservationRow(
                    id=str(observation.id),
                    session_id=str(browser.id),
                    page_id=observation.page_id,
                    session_revision=observation.session_revision,
                    payload=observation.model_dump_json(),
                    created_at=observation.created_at.isoformat(),
                    expires_at=observation.expires_at.isoformat(),
                )
            )
        return observation

    def act(
        self,
        request: BrowserActionRequest,
        *,
        owner_identity: str,
        credential_values: Mapping[str, str] | None = None,
        cancellation_requested: Callable[[], bool] | None = None,
    ) -> BrowserActionResult:
        with self._session_lock(request.session_id):
            return self._act(
                request,
                owner_identity=owner_identity,
                credential_values=credential_values,
                cancellation_requested=cancellation_requested,
            )

    def _act(
        self,
        request: BrowserActionRequest,
        *,
        owner_identity: str,
        credential_values: Mapping[str, str] | None = None,
        cancellation_requested: Callable[[], bool] | None = None,
    ) -> BrowserActionResult:
        replayed = self._action_by_key(request)
        if replayed is not None:
            return replayed
        browser = self._active(request.session_id, owner_identity)
        self._require_revision(browser, request.expected_session_revision)
        observation = self._load_observation(request.observation_id)
        if (
            observation.session_id != browser.id
            or observation.page_id != request.page_id
            or observation.session_revision != browser.revision
            or observation.expires_at <= utc_now()
        ):
            raise MishkanError(ErrorCode.BROWSER, "browser action references a stale observation")
        target = self._target(observation.targets, request.target_reference)
        if request.kind.value == "coordinate_click":
            if (
                observation.screenshot_artifact_reference is None
                or request.visual_evidence_artifact_reference
                != observation.screenshot_artifact_reference
            ):
                raise MishkanError(
                    ErrorCode.BROWSER,
                    "browser coordinate action requires its source observation screenshot",
                )
            assert request.visual_evidence_artifact_reference is not None
            self._artifacts.manifest(request.visual_evidence_artifact_reference)
        if (
            target is not None
            and request.kind.value != "javascript"
            and target.candidate_effects
            and request.resolved_effect not in target.candidate_effects
        ):
            raise MishkanError(
                ErrorCode.AUTHORITY_NOT_GRANTED,
                "resolved browser effect differs from observed target evidence",
            )
        profile = self._config.profiles[browser.profile_id]
        for origin in request.authorized_origins:
            if self._origin(origin) != origin:
                raise MishkanError(
                    ErrorCode.AUTHORITY_NOT_GRANTED,
                    "browser action authority must name exact normalized origins",
                )
            self._require_origin(profile.allowed_origins, origin)
        if request.kind.value == "navigate" and isinstance(request.value, str):
            destination = self._origin(request.value)
            if request.authorized_origins != (destination,):
                raise MishkanError(
                    ErrorCode.AUTHORITY_NOT_GRANTED,
                    "browser navigation destination was not authorized exactly",
                )
        if (
            target is not None
            and target.destination_origin is not None
            and target.destination_origin not in request.authorized_origins
        ):
            raise MishkanError(
                ErrorCode.AUTHORITY_NOT_GRANTED,
                "observed browser target destination was not authorized exactly",
            )
        self._require_safe_literal(request.value)
        dispatched = request
        if request.credential_reference is not None:
            assert request.credential_origin is not None
            if self._origin(str(observation.url)) != self._origin(str(request.credential_origin)):
                raise MishkanError(
                    ErrorCode.AUTHORITY_NOT_GRANTED,
                    "browser credential origin differs from the observed page origin",
                )
            self._require_origin(
                self._config.profiles[browser.profile_id].allowed_origins,
                str(request.credential_origin),
            )
            resolved = dict(credential_values or {})
            if set(resolved) != {request.credential_reference}:
                raise MishkanError(
                    ErrorCode.AUTHORIZATION_MISSING,
                    "browser credential value was not resolved by the authorized gateway",
                )
            dispatched = request.model_copy(
                update={"value": resolved[request.credential_reference]}
            )
            self._session_secrets.setdefault(browser.id, set()).add(
                resolved[request.credential_reference]
            )
        elif credential_values:
            raise MishkanError(
                ErrorCode.TOOL_SCHEMA,
                "browser action received undeclared credential values",
            )
        try:
            self._insert_action(request, BrowserActionState.FAILED)
        except IntegrityError:
            replayed = self._action_by_key(request)
            if replayed is not None:
                return replayed
            raise
        driver, handle = self._handle(browser)
        cancellation = cancellation_requested or (lambda: False)
        try:
            outcome = driver.act(
                handle,
                dispatched,
                target,
                cancellation_requested=cancellation,
            )
        except BrowserOperationCancelled:
            result = self._action_result(
                request,
                browser,
                BrowserActionState.CANCELLED,
                "browser action cancellation was observed before dispatch",
                observation_invalidated=False,
            )
            self._complete_action(request, result)
            return result
        except BrowserUncertainEffect as exc:
            uncertain = browser.model_copy(
                update={
                    "state": BrowserSessionState.UNCERTAIN,
                    "revision": browser.revision + 1,
                    "uncertain_effect": request.resolved_effect,
                    "last_error": str(exc),
                    "updated_at": utc_now(),
                }
            )
            self._replace_session(uncertain, expected_revision=browser.revision)
            result = self._action_result(
                request,
                uncertain,
                BrowserActionState.UNCERTAIN,
                "browser adapter lost certainty after dispatch",
            )
            self._complete_action(request, result)
            return result
        except Exception as exc:
            result = self._action_result(
                request,
                browser,
                BrowserActionState.FAILED,
                f"browser action failed before a completed effect: {type(exc).__name__}",
            )
            self._complete_action(request, result)
            return result
        references = tuple(self._driver_artifact(browser, item) for item in outcome.artifacts)
        settled = browser.model_copy(
            update={
                "revision": browser.revision + 1,
                "page_ids": outcome.page_ids,
                "updated_at": utc_now(),
            }
        )
        self._replace_session(settled, expected_revision=browser.revision)
        result = self._action_result(
            request,
            settled,
            BrowserActionState.COMPLETED,
            "browser action completed and invalidated its source observation",
            references,
        )
        self._complete_action(request, result)
        return result

    def diagnostics(
        self,
        request: BrowserDiagnosticRequest,
        *,
        owner_identity: str,
    ) -> BrowserDiagnosticResult:
        browser = self._active(request.session_id, owner_identity)
        if request.page_id not in browser.page_ids:
            raise MishkanError(ErrorCode.BROWSER, "browser page is not owned by the session")
        limit = min(request.limit, self._config.max_diagnostic_entries)
        driver, handle = self._handle(browser)
        observed = driver.diagnostics(
            handle,
            request.page_id,
            tuple(channel.value for channel in request.channels),
            request.cursor,
            limit,
        )
        raw_payload = json.dumps(observed.entries, sort_keys=True)
        clean_payload = self._inspector.inspect(
            raw_payload,
            tuple(self._session_secrets.get(browser.id, ())),
        )
        loaded = json.loads(clean_payload)
        if not isinstance(loaded, list) or not all(isinstance(item, dict) for item in loaded):
            raise MishkanError(ErrorCode.BROWSER, "browser diagnostics failed inspection")
        entries = tuple(loaded)
        payload = clean_payload.encode()
        reference = self._artifact(
            browser,
            "browser.diagnostics",
            "application/json",
            payload,
        )
        return BrowserDiagnosticResult(
            session_id=browser.id,
            page_id=request.page_id,
            cursor=request.cursor,
            next_cursor=observed.next_cursor,
            entries=entries,
            truncated=observed.truncated or request.limit > limit,
            artifact_reference=reference,
            engine=browser.engine,
            engine_version=browser.engine_version,
        )

    def close(self, session_id: UUID, *, owner_identity: str) -> BrowserSession:
        with self._session_lock(session_id):
            return self._close(session_id, owner_identity=owner_identity)

    def _close(self, session_id: UUID, *, owner_identity: str) -> BrowserSession:
        browser = self._load_session(session_id)
        self._require_owner(browser, owner_identity)
        if browser.state is BrowserSessionState.CLOSED:
            return browser
        if browser.state is not BrowserSessionState.ACTIVE:
            raise MishkanError(
                ErrorCode.BROWSER,
                "browser session cannot be closed without a proven live handle",
                details={"state": browser.state.value},
            )
        pair = self._handles.get(browser.id)
        if pair is None:
            raise MishkanError(ErrorCode.BROWSER, "live browser adapter handle is unavailable")
        closing = browser.model_copy(
            update={
                "state": BrowserSessionState.CLOSING,
                "revision": browser.revision + 1,
                "updated_at": utc_now(),
            }
        )
        self._replace_session(closing, expected_revision=browser.revision)
        try:
            pair[0].close(pair[1])
            profile_reference = (
                self._snapshot_profile_state(browser)
                if browser.profile_kind is BrowserProfileKind.PROJECT_PERSISTENT
                else None
            )
        except Exception as exc:
            self._handles.pop(browser.id, None)
            self._session_secrets.pop(browser.id, None)
            uncertain = closing.model_copy(
                update={
                    "state": BrowserSessionState.UNCERTAIN,
                    "revision": closing.revision + 1,
                    "uncertain_effect": (
                        "browser.profile.snapshot"
                        if browser.profile_kind is BrowserProfileKind.PROJECT_PERSISTENT
                        else "browser.session.close"
                    ),
                    "last_error": type(exc).__name__,
                    "updated_at": utc_now(),
                }
            )
            self._replace_session(uncertain, expected_revision=closing.revision)
            return uncertain
        self._handles.pop(browser.id, None)
        self._session_secrets.pop(browser.id, None)
        closed = closing.model_copy(
            update={
                "state": BrowserSessionState.CLOSED,
                "revision": closing.revision + 1,
                "profile_state_artifact_reference": profile_reference,
                "updated_at": utc_now(),
            }
        )
        self._replace_session(closed, expected_revision=closing.revision)
        return closed

    def reconcile_all(self) -> int:
        with Session(self._engine) as session:
            rows = session.scalars(
                select(BrowserSessionRow).where(
                    BrowserSessionRow.state.in_(
                        [
                            BrowserSessionState.OPENING.value,
                            BrowserSessionState.ACTIVE.value,
                            BrowserSessionState.CLOSING.value,
                        ]
                    )
                )
            ).all()
            incomplete = {
                row.session_id: BrowserActionRequest.model_validate_json(row.payload)
                for row in session.scalars(
                    select(BrowserActionRow).where(BrowserActionRow.completed_at.is_(None))
                ).all()
            }
        count = 0
        for row in rows:
            browser = BrowserSession.model_validate_json(row.payload)
            request = incomplete.get(str(browser.id))
            lifecycle_effect = {
                BrowserSessionState.OPENING: "browser.session.open",
                BrowserSessionState.CLOSING: "browser.session.close",
            }.get(browser.state)
            state = (
                BrowserSessionState.UNCERTAIN
                if request is not None or lifecycle_effect is not None
                else BrowserSessionState.LOST
            )
            reconciled = browser.model_copy(
                update={
                    "state": state,
                    "revision": browser.revision + 1,
                    "uncertain_effect": (
                        request.resolved_effect if request is not None else lifecycle_effect
                    ),
                    "last_error": (
                        "daemon restarted with an incomplete browser action"
                        if request is not None
                        else (
                            "daemon restarted during a Browser lifecycle effect"
                            if lifecycle_effect is not None
                            else "daemon restarted without a live browser handle"
                        )
                    ),
                    "updated_at": utc_now(),
                }
            )
            self._replace_session(reconciled, expected_revision=browser.revision)
            count += 1
        return count

    def _resolve_workspace(self, logical: str) -> Path:
        candidate = (self._workspace / logical).resolve()
        if not candidate.is_relative_to(self._workspace):
            raise MishkanError(ErrorCode.AUTHORITY_NOT_GRANTED, "browser workspace escapes scope")
        return candidate

    def _require_safe_literal(self, value: object) -> None:
        if value is None:
            return
        try:
            serialized = json.dumps(value, sort_keys=True)
        except TypeError as exc:
            raise MishkanError(
                ErrorCode.BROWSER, "browser action value is not serializable"
            ) from exc
        if self._inspector.inspect(serialized) != serialized:
            raise MishkanError(
                ErrorCode.SECRET_CONTENT,
                "browser action literal requires redaction and cannot be executed faithfully",
            )

    @staticmethod
    def _require_origin(allowed: tuple[str, ...], raw_url: str) -> None:
        origin = BrowserSupervisor._origin(raw_url)
        if not any(fnmatchcase(origin, pattern) for pattern in allowed):
            raise MishkanError(
                ErrorCode.AUTHORITY_NOT_GRANTED,
                "browser origin is outside its configured profile",
                details={"origin": origin},
            )

    @staticmethod
    def _origin(raw_url: str) -> str:
        url = httpx.URL(raw_url)
        if url.scheme not in {"http", "https"} or url.host is None or url.userinfo:
            raise MishkanError(
                ErrorCode.AUTHORITY_NOT_GRANTED,
                "browser URL is not an allowed HTTP origin",
            )
        port = url.port or (443 if url.scheme == "https" else 80)
        default = (url.scheme == "https" and port == 443) or (url.scheme == "http" and port == 80)
        origin = f"{url.scheme}://{url.host}" if default else f"{url.scheme}://{url.host}:{port}"
        return origin

    @staticmethod
    def _require_owner(browser: BrowserSession, owner: str) -> None:
        if browser.owner_identity != owner:
            raise MishkanError(ErrorCode.AUTHORITY_NOT_GRANTED, "browser session owner differs")

    @staticmethod
    def _require_revision(browser: BrowserSession, expected: int) -> None:
        if browser.revision != expected:
            raise MishkanError(ErrorCode.REVISION_MISMATCH, "browser session revision changed")

    def _active(self, session_id: UUID, owner: str) -> BrowserSession:
        browser = self._load_session(session_id)
        self._require_owner(browser, owner)
        if browser.state is not BrowserSessionState.ACTIVE:
            raise MishkanError(
                ErrorCode.BROWSER,
                "browser session is not active",
                details={"state": browser.state.value},
            )
        return browser

    def _handle(self, browser: BrowserSession) -> tuple[BrowserDriver, str]:
        pair = self._handles.get(browser.id)
        if pair is None:
            raise MishkanError(ErrorCode.BROWSER, "live browser adapter handle is unavailable")
        return pair

    def _session_lock(self, session_id: UUID) -> threading.RLock:
        with self._session_locks_guard:
            return self._session_locks.setdefault(session_id, threading.RLock())

    def _insert_session(self, browser: BrowserSession) -> None:
        with Session(self._engine) as session, session.begin():
            session.add(
                BrowserSessionRow(
                    id=str(browser.id),
                    owner_identity=browser.owner_identity,
                    run_id=browser.run_id,
                    task_attempt_id=browser.task_attempt_id,
                    state=browser.state.value,
                    revision=browser.revision,
                    payload=browser.model_dump_json(),
                    created_at=browser.created_at.isoformat(),
                    updated_at=browser.updated_at.isoformat(),
                )
            )

    def _replace_session(self, browser: BrowserSession, *, expected_revision: int) -> None:
        with Session(self._engine) as session, session.begin():
            row = session.get(BrowserSessionRow, str(browser.id))
            if row is None or row.revision != expected_revision:
                raise MishkanError(ErrorCode.REVISION_MISMATCH, "browser session update conflicted")
            row.state = browser.state.value
            row.revision = browser.revision
            row.payload = browser.model_dump_json()
            row.updated_at = browser.updated_at.isoformat()

    def _load_session(self, session_id: UUID) -> BrowserSession:
        with Session(self._engine) as session:
            row = session.get(BrowserSessionRow, str(session_id))
            if row is None:
                raise MishkanError(ErrorCode.BROWSER, "browser session does not exist")
            return BrowserSession.model_validate_json(row.payload)

    def _load_observation(self, observation_id: UUID) -> BrowserObservation:
        with Session(self._engine) as session:
            row = session.get(BrowserObservationRow, str(observation_id))
            if row is None:
                raise MishkanError(ErrorCode.BROWSER, "browser observation does not exist")
            return BrowserObservation.model_validate_json(row.payload)

    @staticmethod
    def _target(
        targets: tuple[BrowserTarget, ...],
        reference: str | None,
    ) -> BrowserTarget | None:
        if reference is None:
            return None
        matches = [target for target in targets if target.reference == reference]
        if len(matches) != 1:
            raise MishkanError(ErrorCode.BROWSER, "browser target is stale or unknown")
        return matches[0]

    def _artifact(
        self,
        browser: BrowserSession,
        channel: str,
        media_type: str,
        content: bytes,
    ) -> str:
        return self._artifacts.put_bytes(
            content,
            media_type=media_type,
            provenance=ArtifactProvenance(
                producer_identity=browser.owner_identity,
                run_id=browser.run_id,
                task_attempt_id=browser.task_attempt_id,
                call_id=str(browser.id),
                capability="browser.session",
                channel=channel,
            ),
            complete=True,
            sensitivity=browser.sensitivity,
            retention=browser.retention,
            resolved_secrets=tuple(self._session_secrets.get(browser.id, ())),
        ).reference

    def _driver_artifact(self, browser: BrowserSession, artifact: DriverArtifact) -> str:
        return self._artifact(browser, artifact.channel, artifact.media_type, artifact.content)

    def _snapshot_profile_state(self, browser: BrowserSession) -> str:
        profile = self.profile(browser.profile_id)
        assert profile.user_data_dir is not None
        content = self._archive_directory(
            self._workspace,
            profile.user_data_dir,
            self._config.max_profile_state_bytes,
        )
        reference = self._artifact(
            browser,
            "browser.profile_state",
            "application/x-tar",
            content,
        )
        current = self._artifacts.reference("browser-profile", browser.profile_id)
        self._artifacts.update_reference(
            "browser-profile",
            browser.profile_id,
            reference,
            expected_revision=current.revision if current is not None else 0,
        )
        self._remove_tree(self._workspace, profile.user_data_dir)
        return reference

    def _restore_profile_state(
        self,
        profile_id: str,
        profile: BrowserProfileConfig,
        workspace: Path,
    ) -> None:
        assert profile.user_data_dir is not None
        try:
            observed = self._open_directory_beneath(workspace, profile.user_data_dir)
        except FileNotFoundError:
            pass
        except OSError as exc:
            raise MishkanError(
                ErrorCode.AUTHORITY_NOT_GRANTED,
                "persistent browser profile path cannot be resolved without links",
            ) from exc
        else:
            os.close(observed)
            raise MishkanError(
                ErrorCode.RUN_INTERRUPTED,
                "persistent browser working state exists without a proven settlement",
                details={"reconciliation_required": True},
            )
        current = self._artifacts.reference("browser-profile", profile_id)
        if current is None:
            return
        content = self._artifacts.read_bytes(current.artifact_reference)
        if len(content) > self._config.max_profile_state_bytes:
            raise MishkanError(ErrorCode.BROWSER, "browser profile artifact exceeds its bound")
        root = self._create_directory_beneath(workspace, profile.user_data_dir)
        try:
            extracted_bytes = 0
            with tarfile.open(fileobj=io.BytesIO(content), mode="r:") as archive:
                for member in archive.getmembers():
                    relative = Path(member.name)
                    if (
                        relative.is_absolute()
                        or not relative.parts
                        or ".." in relative.parts
                        or member.issym()
                        or member.islnk()
                    ):
                        raise MishkanError(ErrorCode.BROWSER, "browser profile artifact is unsafe")
                    if member.isdir():
                        directory = self._open_directory_at(
                            root,
                            relative.parts,
                            create=True,
                        )
                        os.close(directory)
                        continue
                    if not member.isfile() or member.size > self._config.max_profile_state_bytes:
                        raise MishkanError(ErrorCode.BROWSER, "browser profile artifact is unsafe")
                    source = archive.extractfile(member)
                    if source is None:
                        raise MishkanError(ErrorCode.BROWSER, "browser profile artifact is corrupt")
                    data = source.read(self._config.max_profile_state_bytes + 1)
                    extracted_bytes += len(data)
                    if (
                        len(data) != member.size
                        or extracted_bytes > self._config.max_profile_state_bytes
                    ):
                        raise MishkanError(ErrorCode.BROWSER, "browser profile artifact is corrupt")
                    parent = self._open_directory_at(root, relative.parts[:-1], create=True)
                    try:
                        descriptor = os.open(
                            relative.name,
                            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                            member.mode & 0o700 or 0o600,
                            dir_fd=parent,
                        )
                        try:
                            view = memoryview(data)
                            while view:
                                written = os.write(descriptor, view)
                                if written < 1:
                                    raise OSError("short browser profile restore write")
                                view = view[written:]
                            os.fsync(descriptor)
                        finally:
                            os.close(descriptor)
                    finally:
                        os.close(parent)
            os.fsync(root)
        except Exception:
            os.close(root)
            self._remove_tree(workspace, profile.user_data_dir)
            raise
        os.close(root)

    @staticmethod
    def _archive_directory(base: Path, relative: Path, max_bytes: int) -> bytes:
        buffer = io.BytesIO()
        total = 0
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW

        def visit(archive: tarfile.TarFile, directory: int, prefix: Path) -> None:
            nonlocal total
            for name in sorted(os.listdir(directory)):
                observed = os.stat(name, dir_fd=directory, follow_symlinks=False)
                relative = prefix / name
                if stat.S_ISDIR(observed.st_mode):
                    info = tarfile.TarInfo(relative.as_posix() + "/")
                    info.type = tarfile.DIRTYPE
                    info.mode = stat.S_IMODE(observed.st_mode) & 0o700
                    info.mtime = 0
                    archive.addfile(info)
                    child = os.open(name, flags, dir_fd=directory)
                    try:
                        visit(archive, child, relative)
                    finally:
                        os.close(child)
                    continue
                if not stat.S_ISREG(observed.st_mode):
                    raise MishkanError(
                        ErrorCode.BROWSER,
                        "persistent browser profile contains an unsupported object",
                    )
                descriptor = os.open(
                    name,
                    os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW,
                    dir_fd=directory,
                )
                try:
                    chunks: list[bytes] = []
                    received = 0
                    while True:
                        chunk = os.read(
                            descriptor,
                            min(65_536, max_bytes - total - received + 1),
                        )
                        if not chunk:
                            break
                        chunks.append(chunk)
                        received += len(chunk)
                        if total + received > max_bytes:
                            raise MishkanError(
                                ErrorCode.BROWSER,
                                "persistent browser profile exceeds its configured bound",
                            )
                    data = b"".join(chunks)
                    if len(data) != observed.st_size:
                        raise MishkanError(
                            ErrorCode.BROWSER,
                            "persistent browser profile changed during snapshot",
                        )
                finally:
                    os.close(descriptor)
                total += len(data)
                info = tarfile.TarInfo(relative.as_posix())
                info.size = len(data)
                info.mode = stat.S_IMODE(observed.st_mode) & 0o700
                info.mtime = 0
                archive.addfile(info, io.BytesIO(data))

        root_descriptor = BrowserSupervisor._open_directory_beneath(base, relative)
        try:
            with tarfile.open(fileobj=buffer, mode="w") as archive:
                visit(archive, root_descriptor, Path())
        finally:
            os.close(root_descriptor)
        content = buffer.getvalue()
        if len(content) > max_bytes:
            raise MishkanError(
                ErrorCode.BROWSER,
                "persistent browser profile archive exceeds its configured bound",
            )
        return content

    @staticmethod
    def _remove_tree(base: Path, relative: Path) -> None:
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW

        def remove_at(parent: int, name: str) -> None:
            child = os.open(name, flags, dir_fd=parent)
            try:
                for entry in os.listdir(child):
                    observed = os.stat(entry, dir_fd=child, follow_symlinks=False)
                    if stat.S_ISDIR(observed.st_mode):
                        remove_at(child, entry)
                    else:
                        os.unlink(entry, dir_fd=child)
            finally:
                os.close(child)
            os.rmdir(name, dir_fd=parent)

        parts = BrowserSupervisor._safe_relative_parts(relative)
        parent = (
            os.open(base, flags)
            if len(parts) == 1
            else BrowserSupervisor._open_directory_beneath(base, Path(*parts[:-1]))
        )
        try:
            remove_at(parent, parts[-1])
        finally:
            os.close(parent)

    @staticmethod
    def _safe_relative_parts(relative: Path) -> tuple[str, ...]:
        if relative.is_absolute() or not relative.parts or ".." in relative.parts:
            raise MishkanError(
                ErrorCode.AUTHORITY_NOT_GRANTED,
                "browser profile path is outside its managed root",
            )
        return relative.parts

    @staticmethod
    def _open_directory_at(
        root: int,
        parts: tuple[str, ...],
        *,
        create: bool,
    ) -> int:
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW
        current = os.dup(root)
        try:
            for part in parts:
                if part in {"", ".", ".."}:
                    raise MishkanError(
                        ErrorCode.AUTHORITY_NOT_GRANTED,
                        "browser profile member path is unsafe",
                    )
                if create:
                    with suppress(FileExistsError):
                        os.mkdir(part, mode=0o700, dir_fd=current)
                following = os.open(part, flags, dir_fd=current)
                os.close(current)
                current = following
            return current
        except Exception:
            os.close(current)
            raise

    @staticmethod
    def _open_directory_beneath(base: Path, relative: Path) -> int:
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW
        root = os.open(base, flags)
        try:
            return BrowserSupervisor._open_directory_at(
                root,
                BrowserSupervisor._safe_relative_parts(relative),
                create=False,
            )
        finally:
            os.close(root)

    @staticmethod
    def _create_directory_beneath(base: Path, relative: Path) -> int:
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW
        parts = BrowserSupervisor._safe_relative_parts(relative)
        root = os.open(base, flags)
        try:
            parent = BrowserSupervisor._open_directory_at(root, parts[:-1], create=True)
        finally:
            os.close(root)
        try:
            os.mkdir(parts[-1], mode=0o700, dir_fd=parent)
            return os.open(parts[-1], flags, dir_fd=parent)
        finally:
            os.close(parent)

    def _insert_action(self, request: BrowserActionRequest, state: BrowserActionState) -> None:
        now = utc_now()
        with Session(self._engine) as session, session.begin():
            session.add(
                BrowserActionRow(
                    id=str(request.idempotency_key),
                    idempotency_key=str(request.idempotency_key),
                    session_id=str(request.session_id),
                    state=state.value,
                    payload=request.model_dump_json(),
                    created_at=now.isoformat(),
                    completed_at=None,
                )
            )

    def _action_by_key(self, request: BrowserActionRequest) -> BrowserActionResult | None:
        with Session(self._engine) as session:
            row = session.scalar(
                select(BrowserActionRow).where(
                    BrowserActionRow.idempotency_key == str(request.idempotency_key)
                )
            )
            if row is None:
                return None
            try:
                stored = json.loads(row.payload)
                stored_request = BrowserActionRequest.model_validate(
                    stored["request"] if row.completed_at is not None else stored
                )
                if stored_request != request:
                    raise MishkanError(
                        ErrorCode.DUPLICATE_RESULT,
                        "browser action idempotency key has different content",
                    )
                if row.completed_at is None:
                    raise MishkanError(
                        ErrorCode.BROWSER,
                        "browser action is incomplete and requires reconciliation",
                        details={"reconciliation_required": True},
                    )
                return BrowserActionResult.model_validate(stored["result"])
            except MishkanError:
                raise
            except (KeyError, TypeError, ValidationError, json.JSONDecodeError) as exc:
                raise MishkanError(ErrorCode.BROWSER, "browser action result is corrupt") from exc

    def _complete_action(
        self,
        request: BrowserActionRequest,
        result: BrowserActionResult,
    ) -> None:
        with Session(self._engine) as session, session.begin():
            row = session.scalar(
                select(BrowserActionRow).where(
                    BrowserActionRow.idempotency_key == str(request.idempotency_key)
                )
            )
            if row is None:
                raise MishkanError(ErrorCode.BROWSER, "browser action journal is missing")
            row.state = result.state.value
            row.payload = json.dumps(
                {
                    "request": request.model_dump(mode="json"),
                    "result": result.model_dump(mode="json"),
                },
                sort_keys=True,
            )
            row.completed_at = result.completed_at.isoformat()

    @staticmethod
    def _action_result(
        request: BrowserActionRequest,
        browser: BrowserSession,
        state: BrowserActionState,
        reason: str,
        references: tuple[str, ...] = (),
        *,
        observation_invalidated: bool = True,
    ) -> BrowserActionResult:
        return BrowserActionResult(
            request_id=request.idempotency_key,
            session_id=browser.id,
            page_id=request.page_id,
            state=state,
            resolved_effect=request.resolved_effect,
            session_revision=browser.revision,
            observation_invalidated=observation_invalidated,
            artifact_references=references,
            error_code=(ErrorCode.BROWSER if state is not BrowserActionState.COMPLETED else None),
            reason=reason,
        )
