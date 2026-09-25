"""Daemon-owned runtime bindings for governed knowledge providers.

This module contains transport bindings only.  It does not make source-selection
or authorization decisions: those remain in :mod:`mishkan.knowledge.service`
and the application command authority.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import tarfile
import tempfile
import time
from collections.abc import Mapping
from datetime import timedelta
from pathlib import Path, PurePosixPath
from typing import Any

from mishkan.artifacts.service import DurableArtifactService
from mishkan.config.models import (
    KnowledgeConfig,
    KnowledgeGraphRefreshConfig,
    KnowledgeSourceConfig,
)
from mishkan.crewai.credentials import CredentialPoolResolver
from mishkan.domain.errors import ErrorCode, MishkanError
from mishkan.domain.time import utc_now
from mishkan.execution.sessions import SessionMode, SessionState
from mishkan.execution.supervisor import SessionSupervisor
from mishkan.knowledge.adapters import (
    ProviderKnowledgeResult,
    ProviderReconciliation,
    ProviderSettlement,
    RawKnowledgeRecord,
)
from mishkan.knowledge.models import (
    KnowledgeClass,
    KnowledgeCorpus,
    KnowledgeOperation,
    KnowledgeOperationKind,
    KnowledgeQuery,
    KnowledgeRefreshRequest,
)
from mishkan.knowledge.operations import GraphRefreshBuild
from mishkan.mcp import (
    McpCallRequest,
    McpCallState,
    McpEffectDisposition,
    McpPrimitiveKind,
    McpRepository,
    McpServiceRunner,
)
from mishkan.repository import RepositoryInspector
from mishkan.tools.execution import EffectSettlement, ExecutionRequest, ExecutionStatus


class DaemonKnowledgePolicy:
    """Enforce source compatibility and the daemon's exact repository binding.

    Application policy has already authorized the actor and external resources
    before this boundary is reached.  This second gate prevents an internal
    adapter call from widening the configured source or repository scope.
    """

    def __init__(self, config: KnowledgeConfig, workspace: Path) -> None:
        self._config = config
        self._workspace = workspace

    def authorize_query(
        self,
        query: KnowledgeQuery,
        *,
        source_id: str,
        source: KnowledgeSourceConfig,
    ) -> None:
        configured = self._config.sources.get(source_id)
        if configured != source or not source.enabled:
            raise MishkanError(ErrorCode.AUTHORITY_NOT_GRANTED, "knowledge source is not enabled")
        compatible = source.knowledge_class is query.knowledge_class
        literal_fallback = (
            source.knowledge_class is KnowledgeClass.LITERAL
            and self._config.literal_fallback
            and query.knowledge_class in {KnowledgeClass.SEMANTIC, KnowledgeClass.STRUCTURAL}
            and query.scope.repository_id is not None
        )
        if not compatible and not literal_fallback:
            raise MishkanError(
                ErrorCode.POLICY_CONFLICT,
                "knowledge source is incompatible with the explicit query class",
            )
        if query.scope.repository_id is not None:
            binding = RepositoryInspector().bind(self._workspace)
            if query.scope.repository_id != binding.repository_id:
                raise MishkanError(
                    ErrorCode.CONTEXT,
                    "knowledge query repository differs from the daemon workspace",
                )
            if query.scope.repository_revision != binding.base_revision:
                raise MishkanError(
                    ErrorCode.REVISION_MISMATCH,
                    "knowledge query repository revision differs from the daemon workspace",
                )

    def authorize_operation(
        self,
        *,
        kind: KnowledgeOperationKind,
        project_id: str,
        source_id: str,
        actor_identity: str,
    ) -> str:
        del project_id
        if not actor_identity:
            raise MishkanError(ErrorCode.AUTHORITY_NOT_GRANTED, "knowledge actor is absent")
        if kind is not KnowledgeOperationKind.PROMOTE:
            source = self._config.sources.get(source_id)
            if source is None or not source.enabled:
                raise MishkanError(
                    ErrorCode.AUTHORITY_NOT_GRANTED,
                    "knowledge mutation source is not enabled",
                )
        # The authoritative fingerprint is recorded by ApplicationCommandAuthority.
        # This internal compatibility gate deliberately does not mint another policy.
        return "application-command-authority"


class RepositoryLiteralKnowledgePort:
    """Read immutable tracked blobs from the exact bound Git revision."""

    _TOKEN = re.compile(r"[A-Za-z0-9_.:/-]{2,128}")

    def __init__(self, workspace: Path) -> None:
        self._workspace = workspace

    def query(self, query: KnowledgeQuery, *, source_id: str) -> ProviderKnowledgeResult:
        scope = query.scope
        binding = RepositoryInspector().bind(self._workspace)
        if (
            scope.repository_id != binding.repository_id
            or scope.repository_revision != binding.base_revision
        ):
            raise MishkanError(
                ErrorCode.REVISION_MISMATCH,
                "literal query is not bound to the daemon repository revision",
            )
        tokens = tuple(
            dict.fromkeys(
                match.group(0).casefold() for match in self._TOKEN.finditer(query.question)
            )
        )
        if not tokens:
            raise MishkanError(
                ErrorCode.OUTPUT_CONTRACT, "literal query contains no searchable term"
            )
        records: list[tuple[int, RawKnowledgeRecord]] = []
        remaining = query.max_bytes
        for relative in self._tracked_paths(binding.root, binding.base_revision):
            if len(records) >= query.max_results or remaining <= 0:
                break
            content = self._blob(
                binding.root,
                binding.base_revision,
                relative,
                max_bytes=remaining,
            )
            if content is None:
                continue
            text = content.decode("utf-8", errors="replace")
            folded_path = relative.casefold()
            folded_text = text.casefold()
            score = sum(folded_path.count(token) * 8 + folded_text.count(token) for token in tokens)
            if score == 0:
                continue
            encoded = text.encode()
            remaining -= len(encoded)
            records.append(
                (
                    score,
                    RawKnowledgeRecord(
                        external_record_id=relative,
                        content=encoded,
                        media_type="text/plain; charset=utf-8",
                        source_locator=f"git:{binding.base_revision}:{relative}",
                        source_revision=binding.base_revision,
                        ranking_basis="exact tracked-path and blob token occurrences",
                        score=float(score),
                        confidence="deterministic literal match",
                    ),
                )
            )
        ordered = tuple(
            record
            for _, record in sorted(records, key=lambda item: (-item[0], item[1].source_locator))
        )
        return ProviderKnowledgeResult(ordered, "native-git-literal-1")

    def _tracked_paths(self, root: Path, revision: str) -> tuple[str, ...]:
        output = self._git(
            root,
            "ls-tree",
            "-r",
            "--name-only",
            "-z",
            revision,
        )
        paths: list[str] = []
        for raw in output.split(b"\0"):
            if not raw:
                continue
            value = raw.decode("utf-8", errors="strict")
            logical = PurePosixPath(value)
            if logical.is_absolute() or ".." in logical.parts:
                raise MishkanError(ErrorCode.CONTEXT, "Git tree contains an unsafe logical path")
            paths.append(value)
        return tuple(sorted(paths))

    def _blob(
        self,
        root: Path,
        revision: str,
        relative: str,
        *,
        max_bytes: int,
    ) -> bytes | None:
        try:
            body = self._git(root, "show", f"{revision}:{relative}")
        except MishkanError:
            return None
        if b"\0" in body[:8_192] or len(body) > max_bytes:
            return None
        return body

    def _git(self, root: Path, *arguments: str) -> bytes:
        try:
            completed = subprocess.run(
                ["git", *arguments],
                cwd=root,
                check=True,
                capture_output=True,
                timeout=30,
            )
        except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            raise MishkanError(
                ErrorCode.PROJECT,
                "literal repository evidence could not be read",
                details={"operation": "git " + " ".join(arguments[:2])},
            ) from exc
        return bytes(completed.stdout)


class GraphifyCliRefreshPort:
    """Run ``graphify update`` as a governed job against an exact Git snapshot."""

    def __init__(
        self,
        workspace: Path,
        config: KnowledgeGraphRefreshConfig,
        supervisor: SessionSupervisor,
        artifacts: DurableArtifactService,
        *,
        staging_root: Path,
        max_graph_bytes: int,
        poll_seconds: float,
        operation_timeout_seconds: float,
    ) -> None:
        self._workspace = workspace.resolve(strict=True)
        self._config = config
        self._supervisor = supervisor
        self._artifacts = artifacts
        self._staging_root = staging_root
        self._max_graph_bytes = max_graph_bytes
        self._poll_seconds = poll_seconds
        self._operation_timeout_seconds = operation_timeout_seconds

    def build(
        self,
        request: KnowledgeRefreshRequest,
        corpus: KnowledgeCorpus,
        *,
        policy_fingerprint: str,
    ) -> GraphRefreshBuild:
        if request.repository_id is None or request.repository_revision is None:
            raise MishkanError(
                ErrorCode.OUTPUT_CONTRACT,
                "Graphify refresh requires an exact repository revision",
            )
        binding = RepositoryInspector().bind(self._workspace)
        if (
            request.repository_id != binding.repository_id
            or request.repository_revision != binding.base_revision
            or request.repository_id not in corpus.authorized_repositories
        ):
            raise MishkanError(
                ErrorCode.REVISION_MISMATCH,
                "Graphify refresh repository binding is not current and authorized",
            )
        stage = self._stage(request.operation_id)
        repository = stage / "repository"
        self._materialize_revision(binding.root, binding.base_revision, repository)
        graph = repository / self._config.graph_relative_path
        if corpus.snapshot_reference is not None:
            previous = self._artifacts.read_bytes(corpus.snapshot_reference)
            graph.parent.mkdir(parents=True, exist_ok=True)
            self._write_new_file(graph, previous)
        executable = self._config.executable
        args = ["update", str(repository.relative_to(self._workspace))]
        if self._config.no_cluster:
            args.append("--no-cluster")
        deadline = utc_now() + timedelta(seconds=self._operation_timeout_seconds)
        session = self._supervisor.start(
            ExecutionRequest(
                execution_id=request.operation_id,
                mode=SessionMode.JOB,
                cwd=".",
                executable=str(executable),
                args=tuple(args),
                environment={"PYTHONDONTWRITEBYTECODE": "1"},
                timeout_seconds=max(1, int(self._operation_timeout_seconds)),
                declared_paths=(str(stage.relative_to(self._workspace)),),
                declared_executables=(str(executable),),
                declared_effects=("filesystem.write",),
                owner=request.requested_by,
                run_id=f"knowledge:{request.project_id}",
                task_id=str(request.operation_id),
                session_profile=self._config.session_profile,
                deadline=deadline,
                policy_fingerprint=policy_fingerprint,
            )
        )
        while session.state not in {
            SessionState.SETTLED,
            SessionState.FAILED,
            SessionState.LOST,
            SessionState.UNCERTAIN,
        }:
            time.sleep(self._poll_seconds)
            session = self._supervisor.status(session.execution_id)
        return self._build_from_settled(request, corpus, session)

    def recover(
        self,
        operation: KnowledgeOperation,
        corpus: KnowledgeCorpus,
    ) -> GraphRefreshBuild | None:
        try:
            session = self._supervisor.status(operation.operation_id)
        except MishkanError:
            return None
        if session.state not in {
            SessionState.SETTLED,
            SessionState.FAILED,
            SessionState.LOST,
            SessionState.UNCERTAIN,
        }:
            return None
        request = KnowledgeRefreshRequest.model_validate_json(
            self._artifacts.read_bytes(operation.request_reference)
        )
        return self._build_from_settled(request, corpus, session)

    def publish(
        self,
        request: KnowledgeRefreshRequest,
        corpus: KnowledgeCorpus,
        graph_reference: str,
    ) -> None:
        del request, corpus
        content = self._artifacts.read_bytes(graph_reference)
        target = self._safe_project_path(self._config.publish_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.is_symlink() or any(parent.is_symlink() for parent in target.parents):
            raise MishkanError(ErrorCode.AUTHORITY_NOT_GRANTED, "Graphify publish path is unsafe")
        descriptor, temporary_name = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent)
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, target)
            directory = os.open(target.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            temporary.unlink(missing_ok=True)

    def reconcile(
        self, operation: KnowledgeOperation, corpus: KnowledgeCorpus
    ) -> ProviderReconciliation:
        recovered = self.recover(operation, corpus)
        if recovered is None:
            return ProviderReconciliation(
                ProviderSettlement.UNKNOWN,
                provider_operation_id=f"session:{operation.operation_id}",
                limitation="Graphify job settlement is not yet proven",
            )
        return ProviderReconciliation(
            ProviderSettlement.SUCCEEDED,
            provider_operation_id=recovered.provider_operation_id,
            response={"indexed_revision": recovered.indexed_revision},
        )

    def cancel(
        self, operation: KnowledgeOperation, corpus: KnowledgeCorpus
    ) -> ProviderReconciliation:
        del corpus
        try:
            session = self._supervisor.cancel(
                operation.operation_id, cause="knowledge_operation_cancelled"
            )
        except MishkanError:
            return ProviderReconciliation(
                ProviderSettlement.UNKNOWN,
                provider_operation_id=f"session:{operation.operation_id}",
                limitation="Graphify job identity or cancellation settlement is unavailable",
            )
        settlement = (
            ProviderSettlement.CANCELLED
            if session.result is not None and session.result.status is ExecutionStatus.CANCELLED
            else ProviderSettlement.UNKNOWN
        )
        return ProviderReconciliation(
            settlement,
            provider_operation_id=f"session:{operation.operation_id}",
        )

    def _build_from_settled(
        self,
        request: KnowledgeRefreshRequest,
        corpus: KnowledgeCorpus,
        session: Any,
    ) -> GraphRefreshBuild:
        result = session.result
        if (
            result is None
            or result.status is not ExecutionStatus.COMPLETED
            or result.effect_settlement is not EffectSettlement.COMPLETED
        ):
            raise MishkanError(
                ErrorCode.EXECUTION,
                "Graphify refresh did not produce a proven filesystem effect",
                details={"session_state": session.state.value},
            )
        graph_path = (
            self._stage(request.operation_id) / "repository" / self._config.graph_relative_path
        )
        graph = self._read_regular_file(graph_path, self._max_graph_bytes)
        previous = (
            self._artifacts.read_bytes(corpus.snapshot_reference)
            if corpus.snapshot_reference is not None
            else b"{}"
        )
        return GraphRefreshBuild(
            graph=graph,
            graph_diff=self._graph_diff(previous, graph),
            indexed_revision=request.repository_revision or "",
            provider_operation_id=f"session:{request.operation_id}",
            media_type="application/json",
        )

    def _stage(self, operation_id: Any) -> Path:
        root = self._safe_project_path(self._staging_root)
        root.mkdir(parents=True, exist_ok=True)
        stage = root / str(operation_id)
        if stage.exists():
            if stage.is_symlink() or not stage.is_dir():
                raise MishkanError(ErrorCode.AUTHORITY_NOT_GRANTED, "Graphify stage is unsafe")
            return stage
        stage.mkdir(mode=0o700)
        return stage

    def _materialize_revision(self, root: Path, revision: str, destination: Path) -> None:
        if destination.exists():
            if destination.is_symlink():
                raise MishkanError(ErrorCode.AUTHORITY_NOT_GRANTED, "Graphify checkout is unsafe")
            shutil.rmtree(destination)
        destination.mkdir(parents=True)
        try:
            process = subprocess.Popen(
                ["git", "archive", "--format=tar", revision],
                cwd=root,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
            )
        except OSError as exc:
            raise MishkanError(ErrorCode.PROJECT, "repository revision export failed") from exc
        assert process.stdout is not None
        archive_stream = io.BytesIO()
        deadline = time.monotonic() + self._operation_timeout_seconds
        while True:
            if time.monotonic() >= deadline:
                process.kill()
                process.wait()
                raise MishkanError(ErrorCode.EXECUTION, "repository revision export timed out")
            chunk = process.stdout.read(1_048_576)
            if not chunk:
                break
            if archive_stream.tell() + len(chunk) > self._config.max_repository_bytes:
                process.kill()
                process.wait()
                raise MishkanError(
                    ErrorCode.OUTPUT_CONTRACT,
                    "repository archive exceeds configured bound",
                )
            archive_stream.write(chunk)
        return_code = process.wait()
        if return_code != 0:
            raise MishkanError(ErrorCode.PROJECT, "repository revision export failed")
        archive_stream.seek(0)
        count = 0
        total = 0
        with tarfile.open(fileobj=archive_stream, mode="r:") as stream:
            for member in stream:
                logical = PurePosixPath(member.name)
                if logical.is_absolute() or not logical.parts or ".." in logical.parts:
                    raise MishkanError(ErrorCode.CONTEXT, "repository archive path is unsafe")
                if not (member.isdir() or member.isfile()):
                    raise MishkanError(
                        ErrorCode.AUTHORITY_NOT_GRANTED,
                        "repository archive contains a non-regular entry",
                    )
                count += 1
                total += member.size
                if (
                    count > self._config.max_repository_files
                    or total > self._config.max_repository_bytes
                ):
                    raise MishkanError(
                        ErrorCode.OUTPUT_CONTRACT,
                        "repository snapshot exceeds bounds",
                    )
                target = destination.joinpath(*logical.parts)
                if member.isdir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                source = stream.extractfile(member)
                if source is None:
                    raise MishkanError(ErrorCode.PROJECT, "repository archive entry is unreadable")
                with target.open("xb") as output:
                    shutil.copyfileobj(source, output)
                target.chmod(0o755 if member.mode & 0o111 else 0o644)
        output_root = destination / self._config.graph_relative_path.parts[0]
        if output_root.exists():
            if output_root.is_symlink():
                raise MishkanError(
                    ErrorCode.AUTHORITY_NOT_GRANTED,
                    "Graphify output root is unsafe",
                )
            shutil.rmtree(output_root)

    def _graph_diff(self, previous: bytes, current: bytes) -> bytes:
        before = self._graph_members(previous)
        after = self._graph_members(current)
        payload: dict[str, object] = {
            "schema_version": "1.0",
            "before_digest": f"sha256:{hashlib.sha256(previous).hexdigest()}",
            "after_digest": f"sha256:{hashlib.sha256(current).hexdigest()}",
        }
        for kind in ("nodes", "edges"):
            added = sorted(after[kind] - before[kind])
            removed = sorted(before[kind] - after[kind])
            payload[kind] = {
                "before": len(before[kind]),
                "after": len(after[kind]),
                "added": added[: self._config.max_diff_entries],
                "removed": removed[: self._config.max_diff_entries],
                "truncated": len(added) > self._config.max_diff_entries
                or len(removed) > self._config.max_diff_entries,
            }
        return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()

    @staticmethod
    def _graph_members(content: bytes) -> dict[str, set[str]]:
        try:
            value = json.loads(content)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise MishkanError(
                ErrorCode.OUTPUT_CONTRACT,
                "Graphify graph is not valid JSON",
            ) from exc
        if not isinstance(value, dict):
            raise MishkanError(ErrorCode.OUTPUT_CONTRACT, "Graphify graph root is not an object")
        members: dict[str, set[str]] = {"nodes": set(), "edges": set()}
        for kind in members:
            records = value.get("links", []) if kind == "edges" else value.get(kind, [])
            if not isinstance(records, list):
                raise MishkanError(ErrorCode.OUTPUT_CONTRACT, f"Graphify {kind} is not a list")
            for record in records:
                canonical = json.dumps(record, sort_keys=True, separators=(",", ":"))
                members[kind].add(hashlib.sha256(canonical.encode()).hexdigest())
        return members

    def _safe_project_path(self, relative: Path) -> Path:
        candidate = self._workspace / relative
        resolved_parent = candidate.parent.resolve()
        if not resolved_parent.is_relative_to(self._workspace):
            raise MishkanError(ErrorCode.AUTHORITY_NOT_GRANTED, "Graphify path escapes workspace")
        return candidate

    @staticmethod
    def _read_regular_file(path: Path, maximum: int) -> bytes:
        if path.is_symlink() or not path.is_file():
            raise MishkanError(ErrorCode.OUTPUT_CONTRACT, "Graphify output is unavailable")
        if path.stat().st_size > maximum:
            raise MishkanError(ErrorCode.OUTPUT_CONTRACT, "Graphify output exceeds source bound")
        return path.read_bytes()

    @staticmethod
    def _write_new_file(path: Path, content: bytes) -> None:
        if path.exists() or path.is_symlink():
            raise MishkanError(ErrorCode.REVISION_MISMATCH, "Graphify stage output already exists")
        with path.open("xb") as stream:
            stream.write(content)


class GraphifyMcpKnowledgePort:
    """Invoke only a discovered, schema-bound Graphify MCP tool."""

    def __init__(
        self,
        runner: McpServiceRunner,
        repository: McpRepository,
        connection_credentials: Mapping[str, tuple[Any, ...]],
        *,
        poll_seconds: float,
        credential_resolver: CredentialPoolResolver | None = None,
    ) -> None:
        self._runner = runner
        self._repository = repository
        self._connection_credentials = dict(connection_credentials)
        self._poll_seconds = poll_seconds
        self._credentials = credential_resolver or CredentialPoolResolver()

    def call(
        self,
        connection_id: str,
        *,
        primitive: str,
        arguments: dict[str, Any],
        query: KnowledgeQuery,
    ) -> dict[str, Any]:
        descriptor = next(
            (
                item
                for item in self._repository.list_primitives(connection_id)
                if item.kind is McpPrimitiveKind.TOOL
                and item.name == primitive
                and item.invocation_supported
            ),
            None,
        )
        if descriptor is None:
            raise MishkanError(
                ErrorCode.TOOL_DRIFT,
                "configured Graphify MCP primitive is unavailable",
                details={"connection_id": connection_id, "primitive": primitive},
            )
        references = self._connection_credentials.get(connection_id)
        if references is None:
            raise MishkanError(ErrorCode.MCP, "Graphify MCP connection is not configured")
        credentials = self._credentials.resolve_exact(references)
        result = self._runner.invoke(
            McpCallRequest(
                connection_id=connection_id,
                primitive_name=primitive,
                caller_identity=query.scope.agent_identity or "mishkan-knowledge",
                run_id=query.scope.run_id or str(query.query_id),
                task_attempt_id=query.scope.task_id or str(query.query_id),
                arguments=arguments,
                declared_effects=(),
                effect_disposition=McpEffectDisposition.READ_ONLY,
                expected_schema_hash=descriptor.schema_hash,
                deadline=utc_now() + timedelta(seconds=query.deadline_seconds),
            ),
            credentials=credentials,
            cancellation_requested=lambda: False,
            poll_seconds=self._poll_seconds,
        )
        if result.state is not McpCallState.COMPLETED or result.output is None:
            raise MishkanError(
                ErrorCode.MCP,
                "Graphify MCP query did not complete",
                details={"state": result.state.value, "reason": result.reason},
            )
        return result.output
