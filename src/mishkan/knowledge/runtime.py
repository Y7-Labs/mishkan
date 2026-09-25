"""Daemon-owned runtime bindings for governed knowledge providers.

This module contains transport bindings only.  It does not make source-selection
or authorization decisions: those remain in :mod:`mishkan.knowledge.service`
and the application command authority.
"""

from __future__ import annotations

import re
import subprocess
from collections.abc import Mapping
from datetime import timedelta
from pathlib import Path, PurePosixPath
from typing import Any

from mishkan.config.models import KnowledgeConfig, KnowledgeSourceConfig
from mishkan.crewai.credentials import CredentialPoolResolver
from mishkan.domain.errors import ErrorCode, MishkanError
from mishkan.domain.time import utc_now
from mishkan.knowledge.adapters import ProviderKnowledgeResult, RawKnowledgeRecord
from mishkan.knowledge.models import KnowledgeClass, KnowledgeOperationKind, KnowledgeQuery
from mishkan.mcp import (
    McpCallRequest,
    McpCallState,
    McpEffectDisposition,
    McpPrimitiveKind,
    McpRepository,
    McpServiceRunner,
)
from mishkan.repository import RepositoryInspector


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
