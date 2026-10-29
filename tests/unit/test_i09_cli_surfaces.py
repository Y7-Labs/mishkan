from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

import pytest
from typer.testing import CliRunner

import mishkan.cli.app as cli
from mishkan.planning.models import InitializationResult, ReviewDecision


@dataclass(frozen=True)
class _Payload:
    kind: str

    def model_dump(self, *, mode: str) -> dict[str, str]:
        assert mode == "json"
        return {"kind": self.kind}


class _Client:
    workers: _Client

    def __init__(self) -> None:
        self.workers = self

    def __enter__(self) -> _Client:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def statuses(self, *, offset: int, limit: int) -> tuple[_Payload, ...]:
        assert (offset, limit) == (2, 3)
        return (_Payload("worker-status"),)

    def status(self, worker_id: UUID) -> _Payload:
        assert str(worker_id) == "00000000-0000-4000-8000-000000000001"
        return _Payload("worker-detail")

    def accept_completion(
        self,
        completion_id: UUID,
        result: InitializationResult,
        review: ReviewDecision,
    ) -> _Payload:
        assert str(completion_id) == "00000000-0000-4000-8000-000000000002"
        assert result.task_id == review.task_id == "remote-task"
        return _Payload("accepted-completion")


def _contracts(tmp_path: Path) -> tuple[Path, Path]:
    result = InitializationResult(
        repository_revision="a" * 40,
        task_id="remote-task",
        summary="Validated remote result.",
        cited_paths=("README.md",),
        findings=("The result is grounded in the cited path.",),
    )
    review = ReviewDecision(
        task_id="remote-task",
        verdict="accepted",
        summary="Independent review accepted the result.",
        checked_citations=("README.md",),
    )
    result_path = tmp_path / "result.json"
    review_path = tmp_path / "review.json"
    result_path.write_text(result.model_dump_json(), encoding="utf-8")
    review_path.write_text(review.model_dump_json(), encoding="utf-8")
    return result_path, review_path


def test_worker_cli_exposes_status_and_governed_acceptance(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cli, "_daemon_client", lambda _ctx: _Client())
    runner = CliRunner()

    listed = runner.invoke(
        cli.app,
        ["--json", "worker", "list", "--offset", "2", "--limit", "3"],
    )
    shown = runner.invoke(
        cli.app,
        ["--json", "worker", "show", "00000000-0000-4000-8000-000000000001"],
    )
    result_path, review_path = _contracts(tmp_path)
    accepted = runner.invoke(
        cli.app,
        [
            "--json",
            "worker",
            "accept",
            "00000000-0000-4000-8000-000000000002",
            "--result",
            str(result_path),
            "--review",
            str(review_path),
        ],
    )

    assert listed.exit_code == shown.exit_code == accepted.exit_code == 0
    assert json.loads(listed.stdout) == [{"kind": "worker-status"}]
    assert json.loads(shown.stdout) == {"kind": "worker-detail"}
    assert json.loads(accepted.stdout) == {"kind": "accepted-completion"}
