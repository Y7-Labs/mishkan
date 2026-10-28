"""Separate governed adapters for explicit Git stateful effects."""

from __future__ import annotations

import json
import subprocess
import tempfile
from enum import StrEnum
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from pydantic import BaseModel, ConfigDict, Field, model_validator

from mishkan.artifacts import ArtifactProvenance
from mishkan.artifacts.service import DurableArtifactService
from mishkan.domain.errors import ErrorCode, MishkanError
from mishkan.policy import ApprovalEvidence, AuthorizationRequest, Decision, PolicyAuthority
from mishkan.policy.models import EffectivePolicy, ResourceRequest
from mishkan.tools.execution import EffectSettlement


class GitEffectMode(StrEnum):
    STAGE = "stage"
    COMMIT = "commit"
    PUSH = "push"
    FORCE_WITH_LEASE = "force_with_lease"
    FORCE_PUSH = "force_push"

    @property
    def effect_class(self) -> str:
        return (
            "repository_remote_write"
            if self in {self.PUSH, self.FORCE_WITH_LEASE, self.FORCE_PUSH}
            else "repository_write"
        )


class GitEffectRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    mode: GitEffectMode
    workspace: Path
    paths: tuple[str, ...] = ()
    remote: str | None = None
    branch: str | None = None
    author_name: str | None = None
    author_email: str | None = None
    message: str | None = None
    expected_remote_oid: str | None = None
    expected_head: str | None = None
    expected_remote_url: str | None = None
    credential_reference: str | None = None
    require_clean_worktree: bool = False
    timeout_seconds: int = Field(default=120, ge=1, le=3600)

    @model_validator(mode="after")
    def mode_fields_are_exact(self) -> GitEffectRequest:
        if self.mode is GitEffectMode.STAGE and not self.paths:
            raise ValueError("Git stage requires explicit paths")
        if self.mode is GitEffectMode.COMMIT:
            if not all((self.author_name, self.author_email, self.message)):
                raise ValueError("Git commit requires explicit author and message")
            if not self.paths:
                raise ValueError("Git commit requires the exact staged paths")
        if self.mode in {
            GitEffectMode.PUSH,
            GitEffectMode.FORCE_WITH_LEASE,
            GitEffectMode.FORCE_PUSH,
        } and not all((self.remote, self.branch)):
            raise ValueError("Git push effect requires explicit remote and branch")
        if (
            self.mode
            in {
                GitEffectMode.PUSH,
                GitEffectMode.FORCE_WITH_LEASE,
                GitEffectMode.FORCE_PUSH,
            }
            and not self.expected_remote_url
        ):
            raise ValueError("Git push effect requires the expected remote URL")
        if self.mode is GitEffectMode.FORCE_WITH_LEASE and not self.expected_remote_oid:
            raise ValueError("force-with-lease requires the expected remote object id")
        if self.expected_remote_url is not None:
            parsed = urlsplit(self.expected_remote_url)
            if parsed.username is not None or parsed.password is not None:
                raise ValueError("Git remote URL must not embed credentials")
        remote_modes = {
            GitEffectMode.PUSH,
            GitEffectMode.FORCE_WITH_LEASE,
            GitEffectMode.FORCE_PUSH,
        }
        if self.credential_reference is not None and self.mode not in remote_modes:
            raise ValueError("Git credentials are accepted only by remote effects")
        return self


class GitEffectResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    mode: GitEffectMode
    returncode: int
    settlement: EffectSettlement
    before_revision: str | None
    after_revision: str | None
    repository_root: str
    git_directory: str
    current_branch: str | None
    remote_url: str | None
    remote_revision_before: str | None
    remote_revision_after: str | None
    target_revision: str | None
    validation: tuple[str, ...]
    changed_paths: tuple[str, ...]
    diff_reference: str
    stdout: str
    stderr: str


class GovernedGitService:
    def __init__(self, artifacts: DurableArtifactService) -> None:
        self._artifacts = artifacts

    def execute(
        self,
        request: GitEffectRequest,
        *,
        authorization: AuthorizationRequest,
        policy: EffectivePolicy,
        approval: ApprovalEvidence | None = None,
        credential_value: str | None = None,
    ) -> GitEffectResult:
        expected_effect = f"git.{request.mode.value}"
        if authorization.effects != (expected_effect,):
            raise MishkanError(
                ErrorCode.AUTHORITY_NOT_GRANTED,
                "Git effect differs from the exact policy request",
            )
        expected_credentials = (
            (request.credential_reference,) if request.credential_reference is not None else ()
        )
        if authorization.credentials != expected_credentials:
            raise MishkanError(
                ErrorCode.AUTHORITY_NOT_GRANTED,
                "Git credential reference differs from the exact policy request",
            )
        if (credential_value is None) != (request.credential_reference is None):
            raise MishkanError(
                ErrorCode.AUTHORIZATION_MISSING,
                "Git credential material does not match the authorized reference",
            )
        decision = PolicyAuthority().evaluate(authorization, policy, approval)
        if decision.decision is not Decision.ALLOW:
            raise MishkanError(ErrorCode.AUTHORITY_NOT_GRANTED, "Git effect is not authorized")
        workspace = request.workspace.resolve(strict=True)
        repository_root = self._required_value(
            self._run(
                workspace,
                ["rev-parse", "--show-toplevel"],
                timeout=request.timeout_seconds,
            ),
            "Git repository root could not be observed",
        )
        if Path(repository_root).resolve() != workspace:
            raise MishkanError(
                ErrorCode.AUTHORITY_NOT_GRANTED,
                "Git effect workspace is not the exact repository root",
            )
        git_directory = self._required_value(
            self._run(
                workspace,
                ["rev-parse", "--absolute-git-dir"],
                timeout=request.timeout_seconds,
            ),
            "Git directory identity could not be observed",
        )
        branch_result = self._run(
            workspace,
            ["branch", "--show-current"],
            timeout=request.timeout_seconds,
        )
        current_branch = branch_result.stdout.strip() or None
        before = self._run(
            workspace,
            ["rev-parse", "HEAD"],
            timeout=request.timeout_seconds,
        )
        before_revision = before.stdout.strip() if before.returncode == 0 else None
        if request.expected_head is not None and request.expected_head != before_revision:
            raise MishkanError(ErrorCode.REVISION_MISMATCH, "Git HEAD differs from the exact base")
        if request.branch is not None and current_branch != request.branch:
            raise MishkanError(
                ErrorCode.REVISION_MISMATCH,
                "Git branch differs from the authorized branch",
            )
        before_status = self._run(
            workspace,
            ["status", "--porcelain=v1"],
            timeout=request.timeout_seconds,
        ).stdout
        if request.require_clean_worktree and before_status:
            raise MishkanError(
                ErrorCode.REVISION_MISMATCH,
                "Git validation requires a clean worktree before this effect",
            )
        if request.mode is GitEffectMode.COMMIT:
            staged_paths = tuple(
                sorted(
                    item
                    for item in self._run(
                        workspace,
                        ["diff", "--cached", "--name-only"],
                        timeout=request.timeout_seconds,
                    ).stdout.splitlines()
                    if item
                )
            )
            if staged_paths != tuple(sorted(request.paths)):
                raise MishkanError(
                    ErrorCode.REVISION_MISMATCH,
                    "Git staged paths differ from the exact commit target",
                    details={"expected": sorted(request.paths), "observed": list(staged_paths)},
                )
        remote_url: str | None = None
        remote_before: str | None = None
        if request.remote is not None:
            remote_url = self._required_value(
                self._run(
                    workspace,
                    ["remote", "get-url", request.remote],
                    timeout=request.timeout_seconds,
                ),
                "Git remote identity could not be observed",
            )
            if remote_url != request.expected_remote_url:
                raise MishkanError(
                    ErrorCode.REVISION_MISMATCH,
                    "Git remote URL differs from the authorized remote",
                )
            assert request.branch is not None
            assert request.expected_remote_url is not None
            remote_before = self._remote_oid(
                workspace,
                request.expected_remote_url,
                request.branch,
                timeout=request.timeout_seconds,
                credential_value=credential_value,
            )
        argv = self._argv(request, remote_target=request.expected_remote_url)
        completed = (
            self._run_remote(
                workspace,
                argv,
                timeout=request.timeout_seconds,
                credential_value=credential_value,
            )
            if request.remote is not None
            else self._run(workspace, argv, timeout=request.timeout_seconds)
        )
        after = self._run(
            workspace,
            ["rev-parse", "HEAD"],
            timeout=request.timeout_seconds,
        )
        after_revision = after.stdout.strip() if after.returncode == 0 else None
        remote_after = (
            self._remote_oid(
                workspace,
                request.expected_remote_url or "",
                request.branch or "",
                timeout=request.timeout_seconds,
                credential_value=credential_value,
            )
            if request.remote is not None
            else None
        )
        target_revision = after_revision
        diff_arguments, names_arguments = self._evidence_arguments(
            request, before_revision, after_revision, remote_before
        )
        diff = self._run(workspace, diff_arguments, timeout=request.timeout_seconds)
        names = self._run(workspace, names_arguments, timeout=request.timeout_seconds)
        changed_paths = tuple(sorted(item for item in names.stdout.splitlines() if item))
        manifest = self._artifacts.put_bytes(
            diff.stdout.encode(),
            media_type="text/x-diff",
            provenance=ArtifactProvenance(
                producer_identity=authorization.identity,
                run_id=authorization.plan_fingerprint,
                task_attempt_id="git-effect",
                call_id=authorization.fingerprint,
                capability=f"git.{request.mode.value}",
                channel="diff",
            ),
            complete=True,
            retention="git-effect",
        )
        remote = request.mode in {
            GitEffectMode.PUSH,
            GitEffectMode.FORCE_WITH_LEASE,
            GitEffectMode.FORCE_PUSH,
        }
        if remote:
            if target_revision is not None and remote_after == target_revision:
                settlement = EffectSettlement.COMPLETED
            elif remote_after == remote_before:
                settlement = EffectSettlement.ABSENT
            else:
                settlement = EffectSettlement.UNCERTAIN
        else:
            settlement = (
                EffectSettlement.COMPLETED if completed.returncode == 0 else EffectSettlement.ABSENT
            )
        validation = (
            "repository-root-verified",
            "git-directory-verified",
            "branch-verified" if request.branch else "branch-observed",
            "remote-verified" if request.remote else "remote-not-applicable",
            "target-verified" if settlement is EffectSettlement.COMPLETED else "target-unsettled",
        )
        return GitEffectResult(
            mode=request.mode,
            returncode=completed.returncode,
            settlement=settlement,
            before_revision=before_revision,
            after_revision=after_revision,
            repository_root=repository_root,
            git_directory=git_directory,
            current_branch=current_branch,
            remote_url=self._redact_url(remote_url),
            remote_revision_before=remote_before,
            remote_revision_after=remote_after,
            target_revision=target_revision,
            validation=validation,
            changed_paths=changed_paths,
            diff_reference=manifest.reference,
            stdout=completed.stdout,
            stderr=completed.stderr,
        )

    @classmethod
    def _remote_oid(
        cls,
        workspace: Path,
        remote: str,
        branch: str,
        *,
        timeout: int,
        credential_value: str | None,
    ) -> str | None:
        result = cls._run_remote(
            workspace,
            ["ls-remote", "--heads", remote, f"refs/heads/{branch}"],
            timeout=timeout,
            credential_value=credential_value,
        )
        if result.returncode != 0 or not result.stdout.strip():
            return None
        return result.stdout.split()[0]

    @staticmethod
    def _required_value(result: subprocess.CompletedProcess[str], message: str) -> str:
        value = result.stdout.strip()
        if result.returncode != 0 or not value:
            raise MishkanError(ErrorCode.EDIT, message)
        return value

    @staticmethod
    def _redact_url(value: str | None) -> str | None:
        if value is None:
            return None
        parsed = urlsplit(value)
        if parsed.username is None and parsed.password is None:
            return value
        host = parsed.hostname or ""
        if parsed.port is not None:
            host = f"{host}:{parsed.port}"
        return urlunsplit((parsed.scheme, host, parsed.path, parsed.query, parsed.fragment))

    @staticmethod
    def _evidence_arguments(
        request: GitEffectRequest,
        before: str | None,
        after: str | None,
        remote_before: str | None,
    ) -> tuple[list[str], list[str]]:
        if request.mode is GitEffectMode.STAGE:
            return ["diff", "--cached", "--binary"], ["diff", "--cached", "--name-only"]
        base = before if request.mode is GitEffectMode.COMMIT else remote_before
        if base is not None and after is not None:
            return (
                ["diff", "--binary", base, after],
                ["diff", "--name-only", base, after],
            )
        if after is not None:
            return (
                ["show", "--format=", "--binary", after],
                ["show", "--format=", "--name-only", after],
            )
        return ["diff", "--binary"], ["diff", "--name-only"]

    @staticmethod
    def authorization_request(
        request: GitEffectRequest,
        *,
        plan_fingerprint: str,
        identity: str,
        repository: str,
        role: str,
    ) -> AuthorizationRequest:
        return AuthorizationRequest(
            plan_fingerprint=plan_fingerprint,
            identity=identity,
            objective_class="git-effect",
            repository=repository,
            outcome="git-effect",
            role=role,
            capability=f"git.{request.mode.value}",
            effect_class=request.mode.effect_class,
            effects=(f"git.{request.mode.value}",),
            paths=request.paths,
            remotes=(request.remote,) if request.remote else (),
            branches=(request.branch,) if request.branch else (),
            credentials=(request.credential_reference,) if request.credential_reference else (),
            resources=ResourceRequest(
                timeout_seconds=request.timeout_seconds,
                network=request.mode.effect_class == "repository_remote_write",
            ),
        )

    @staticmethod
    def _argv(
        request: GitEffectRequest,
        *,
        remote_target: str | None = None,
    ) -> list[str]:
        if request.mode is GitEffectMode.STAGE:
            return ["add", "--", *request.paths]
        if request.mode is GitEffectMode.COMMIT:
            assert request.author_name and request.author_email and request.message
            return [
                "-c",
                f"user.name={request.author_name}",
                "-c",
                f"user.email={request.author_email}",
                "commit",
                "-m",
                request.message,
            ]
        assert request.remote and request.branch
        if remote_target is None:
            raise MishkanError(
                ErrorCode.AUTHORIZATION_MISSING,
                "Git remote effect has no immutable authorized destination",
            )
        if request.mode is GitEffectMode.PUSH:
            return ["push", remote_target, request.branch]
        if request.mode is GitEffectMode.FORCE_WITH_LEASE:
            assert request.expected_remote_oid
            return [
                "push",
                f"--force-with-lease={request.branch}:{request.expected_remote_oid}",
                remote_target,
                request.branch,
            ]
        return ["push", "--force", remote_target, request.branch]

    @classmethod
    def _run_remote(
        cls,
        workspace: Path,
        arguments: list[str],
        *,
        timeout: int,
        credential_value: str | None,
    ) -> subprocess.CompletedProcess[str]:
        if credential_value is None:
            return cls._run(workspace, arguments, timeout=timeout)
        try:
            credential = json.loads(credential_value)
        except json.JSONDecodeError as exc:
            raise MishkanError(
                ErrorCode.OUTPUT_CONTRACT,
                "Git credential must be a JSON username/password object",
            ) from exc
        if not isinstance(credential, dict) or set(credential) != {"username", "password"}:
            raise MishkanError(
                ErrorCode.OUTPUT_CONTRACT,
                "Git credential must contain only username and password",
            )
        username = credential["username"]
        password = credential["password"]
        if (
            not isinstance(username, str)
            or not username
            or not isinstance(password, str)
            or not password
        ):
            raise MishkanError(ErrorCode.OUTPUT_CONTRACT, "Git credential values must be text")
        with tempfile.TemporaryDirectory(prefix="mishkan-git-askpass-") as directory:
            askpass = Path(directory) / "askpass.sh"
            askpass.write_text(
                '#!/bin/sh\ncase "$1" in *Username*) printf \'%s\' "$MISHKAN_GIT_USERNAME" ;; '
                "*) printf '%s' \"$MISHKAN_GIT_PASSWORD\" ;; esac\n",
                encoding="utf-8",
            )
            askpass.chmod(0o700)
            return cls._run(
                workspace,
                arguments,
                timeout=timeout,
                environment={
                    "GIT_ASKPASS": str(askpass),
                    "GIT_TERMINAL_PROMPT": "0",
                    "MISHKAN_GIT_USERNAME": username,
                    "MISHKAN_GIT_PASSWORD": password,
                },
            )

    @staticmethod
    def _run(
        workspace: Path,
        arguments: list[str],
        *,
        timeout: int,
        environment: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        process_environment = {"PATH": "/usr/local/bin:/usr/bin:/bin", "LC_ALL": "C.UTF-8"}
        process_environment.update(environment or {})
        return subprocess.run(
            ["git", *arguments],
            cwd=workspace,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
            env=process_environment,
        )
