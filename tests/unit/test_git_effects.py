from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from mishkan.artifacts.service import DurableArtifactService
from mishkan.edits.git import GitEffectMode, GitEffectRequest, GovernedGitService
from mishkan.persistence import SchemaManager
from mishkan.policy import Decision, EffectivePolicy, PolicyDocument, PolicyRule, PolicyScope
from mishkan.policy.models import ResourceRequest, canonical_fingerprint
from mishkan.tools.adapters import AdapterCall
from mishkan.tools.execution import EffectSettlement
from mishkan.tools.gateway_models import ResolvedPath, ResolvedTargets
from mishkan.tools.git import build_git_adapters


def _policy(effect: str, capability: str) -> EffectivePolicy:
    document = PolicyDocument(
        source_id="test.git",
        revision="1",
        adoption_authority="test",
        rules=(
            PolicyRule(
                rule_id="git.effect",
                decision=Decision.ALLOW,
                scope=PolicyScope(capabilities=(capability,), effects=(effect,)),
            ),
        ),
    )
    return EffectivePolicy(
        documents=(document,),
        source_uris=("test:git",),
        fingerprint=canonical_fingerprint({"document": document.fingerprint}),
    )


def test_stage_and_commit_are_distinct_executable_policy_effects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = tmp_path / "repo"
    repository.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repository, check=True)
    (repository / "app.txt").write_text("content")
    database = tmp_path / "mishkan.db"
    SchemaManager(database).initialize()
    artifacts = DurableArtifactService(
        database,
        tmp_path / "artifacts",
        max_artifact_bytes=1024 * 1024,
        max_chunk_bytes=1024,
    )
    service = GovernedGitService(artifacts)
    observed_timeouts: list[int] = []
    run = GovernedGitService._run

    def observe_timeout(
        workspace: Path,
        arguments: list[str],
        *,
        timeout: int,
        environment: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        observed_timeouts.append(timeout)
        return run(workspace, arguments, timeout=timeout, environment=environment)

    monkeypatch.setattr(GovernedGitService, "_run", staticmethod(observe_timeout))

    stage = GitEffectRequest(
        mode=GitEffectMode.STAGE,
        workspace=repository,
        paths=("app.txt",),
        timeout_seconds=37,
    )
    stage_auth = service.authorization_request(
        stage,
        plan_fingerprint="a" * 64,
        identity="role:Engineer",
        repository="repo",
        role="Engineer",
    )
    staged = service.execute(
        stage,
        authorization=stage_auth,
        policy=_policy("git.stage", "git.stage"),
    )
    assert staged.settlement is EffectSettlement.COMPLETED
    assert observed_timeouts and set(observed_timeouts) == {37}
    observed_timeouts.clear()

    commit = GitEffectRequest(
        mode=GitEffectMode.COMMIT,
        workspace=repository,
        author_name="Y4NN777",
        author_email="axel.studiesmail@gmail.com",
        message="feat: initial",
        paths=("app.txt",),
        timeout_seconds=43,
    )
    commit_auth = service.authorization_request(
        commit,
        plan_fingerprint="b" * 64,
        identity="role:Engineer",
        repository="repo",
        role="Engineer",
    )
    committed = service.execute(
        commit,
        authorization=commit_auth,
        policy=_policy("git.commit", "git.commit"),
    )
    assert committed.settlement is EffectSettlement.COMPLETED
    assert committed.after_revision is not None
    assert committed.repository_root == str(repository)
    assert committed.current_branch in {"main", "master"}
    assert b"app.txt" in artifacts.read_bytes(committed.diff_reference)
    assert observed_timeouts and set(observed_timeouts) == {43}


def test_push_force_with_lease_and_force_push_verify_remote_target(tmp_path: Path) -> None:
    repository = tmp_path / "repo"
    remote = tmp_path / "remote.git"
    repository.mkdir()
    subprocess.run(["git", "init", "-q", "--bare", str(remote)], check=True)
    subprocess.run(["git", "init", "-q", "-b", "develop"], cwd=repository, check=True)
    subprocess.run(["git", "remote", "add", "origin", str(remote)], cwd=repository, check=True)
    (repository / "app.txt").write_text("one")
    subprocess.run(["git", "add", "app.txt"], cwd=repository, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Y4NN777",
            "-c",
            "user.email=axel.studiesmail@gmail.com",
            "commit",
            "-qm",
            "initial",
        ],
        cwd=repository,
        check=True,
    )
    database = tmp_path / "mishkan.db"
    SchemaManager(database).initialize()
    artifacts = DurableArtifactService(
        database,
        tmp_path / "artifacts",
        max_artifact_bytes=1024 * 1024,
        max_chunk_bytes=1024,
    )
    service = GovernedGitService(artifacts)

    def execute(request: GitEffectRequest):
        effect = f"git.{request.mode.value}"
        authorization = service.authorization_request(
            request,
            plan_fingerprint=effect.ljust(64, "a")[:64],
            identity="role:Engineer",
            repository="repo",
            role="Engineer",
        )
        return service.execute(
            request,
            authorization=authorization,
            policy=_policy(effect, effect),
            credential_value=(
                json.dumps({"username": "fixture", "password": "secret-canary"})
                if request.credential_reference is not None
                else None
            ),
        )

    pushed = execute(
        GitEffectRequest(
            mode=GitEffectMode.PUSH,
            workspace=repository,
            remote="origin",
            branch="develop",
            expected_remote_url=str(remote),
            credential_reference="git.remote",
        )
    )
    assert pushed.settlement is EffectSettlement.COMPLETED
    assert pushed.remote_revision_after == pushed.target_revision
    assert "secret-canary" not in pushed.model_dump_json()

    first_remote = pushed.remote_revision_after
    assert first_remote is not None
    (repository / "app.txt").write_text("two")
    subprocess.run(["git", "add", "app.txt"], cwd=repository, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Y4NN777",
            "-c",
            "user.email=axel.studiesmail@gmail.com",
            "commit",
            "-qm",
            "second",
        ],
        cwd=repository,
        check=True,
    )
    leased = execute(
        GitEffectRequest(
            mode=GitEffectMode.FORCE_WITH_LEASE,
            workspace=repository,
            remote="origin",
            branch="develop",
            expected_remote_url=str(remote),
            expected_remote_oid=first_remote,
        )
    )
    assert leased.settlement is EffectSettlement.COMPLETED

    forced = execute(
        GitEffectRequest(
            mode=GitEffectMode.FORCE_PUSH,
            workspace=repository,
            remote="origin",
            branch="develop",
            expected_remote_url=str(remote),
            expected_head=leased.target_revision,
        )
    )
    assert forced.settlement is EffectSettlement.COMPLETED


def test_gateway_git_adapter_is_concrete_and_preserves_authorized_targets(tmp_path: Path) -> None:
    repository = tmp_path / "repo"
    repository.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repository, check=True)
    target = repository / "app.txt"
    target.write_text("content")
    database = tmp_path / "mishkan.db"
    SchemaManager(database).initialize()
    artifacts = DurableArtifactService(
        database,
        tmp_path / "artifacts",
        max_artifact_bytes=1024 * 1024,
        max_chunk_bytes=1024,
    )
    policy = _policy("git.stage", "git.stage")
    adapter = build_git_adapters(repository, GovernedGitService(artifacts), policy)[
        "native.git.stage"
    ]
    targets = ResolvedTargets(
        paths=(
            ResolvedPath(
                requested="app.txt",
                lexical_relative="app.txt",
                relative="app.txt",
                absolute=target,
            ),
        )
    )
    result = adapter.invoke(
        AdapterCall(
            arguments={"paths": ["app.txt"]},
            targets=targets,
            credentials={},
            execution_id="git-stage-1",
            resources=ResourceRequest(timeout_seconds=120),
            isolation_profile=None,
            cancellation_requested=lambda: False,
            run_id="run-1",
            task_attempt_id="task-1",
            acting_identity="role:Engineer",
            capability="git.stage",
            plan_fingerprint="a" * 64,
            repository=str(repository),
            role="Engineer",
        )
    )

    assert result.actual_targets == targets
    assert result.output["settlement"] == "completed"
    assert result.evidence["diff_reference"]
