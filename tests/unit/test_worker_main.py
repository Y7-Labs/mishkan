from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

import mishkan.workers.main as worker_main
from mishkan.domain.errors import ErrorCode, MishkanError


def _profile_document() -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "model_route": "simple",
        "capabilities": [],
        "idle_poll_seconds": 0.01,
    }


def test_worker_profile_loader_accepts_strict_yaml_and_rejects_invalid_input(
    tmp_path: Path,
) -> None:
    valid = tmp_path / "worker.yaml"
    invalid = tmp_path / "invalid.yaml"
    valid.write_text(yaml.safe_dump(_profile_document()), encoding="utf-8")
    invalid.write_text("model_route: [not, a, route]\n", encoding="utf-8")

    assert worker_main._profile(valid).model_route == "simple"
    with pytest.raises(MishkanError, match="worker runtime profile is invalid"):
        worker_main._profile(invalid)
    with pytest.raises(MishkanError, match="worker runtime profile is invalid"):
        worker_main._profile(tmp_path / "absent.yaml")


def test_worker_enrollment_requires_an_explicit_environment_secret(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.delenv("MISHKAN_WORKER_ENROLLMENT_TOKEN", raising=False)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "mishkan-worker",
            "enroll",
            "--state-dir",
            str(tmp_path),
            "--daemon-url",
            "https://127.0.0.1:8888",
            "--display-name",
            "gate-worker",
            "--server-ca",
            str(tmp_path / "ca.pem"),
        ],
    )

    with pytest.raises(SystemExit, match="2"):
        worker_main.main()

    envelope = json.loads(capsys.readouterr().err)
    assert envelope["code"] == ErrorCode.CONFIGURATION.value
    assert "token" not in envelope.get("details", {})


def test_worker_enrollment_and_rotation_emit_only_public_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    calls: list[tuple[str, dict[str, object]]] = []

    class Identity:
        def model_dump_json(self, *, indent: int) -> str:
            assert indent == 2
            return '{"worker_id":"public-worker"}'

    class Store:
        def __init__(self, root: Path) -> None:
            assert root == tmp_path

        def initialize(self, **kwargs: object) -> Identity:
            calls.append(("initialize", kwargs))
            return Identity()

        def rotate(self, **kwargs: object) -> Identity:
            calls.append(("rotate", kwargs))
            return Identity()

    monkeypatch.setattr(worker_main, "WorkerCredentialStore", Store)
    monkeypatch.setenv("WORKER_GATE_TOKEN", "private-enrollment-token")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "mishkan-worker",
            "enroll",
            "--state-dir",
            str(tmp_path),
            "--daemon-url",
            "https://127.0.0.1:8888",
            "--display-name",
            "gate-worker",
            "--server-ca",
            str(tmp_path / "ca.pem"),
            "--token-env",
            "WORKER_GATE_TOKEN",
        ],
    )
    worker_main.main()
    assert "private-enrollment-token" not in capsys.readouterr().out

    monkeypatch.setattr(
        sys,
        "argv",
        ["mishkan-worker", "rotate", "--state-dir", str(tmp_path), "--timeout", "9"],
    )
    worker_main.main()
    assert json.loads(capsys.readouterr().out) == {"worker_id": "public-worker"}
    assert calls[0][0] == "initialize"
    assert calls[1] == ("rotate", {"timeout_seconds": 9.0})


def test_worker_once_and_run_use_the_same_process_boundary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    class Client:
        def __enter__(self) -> Client:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

    once_process = SimpleNamespace(run_once=lambda: True)
    monkeypatch.setattr(worker_main, "_process", lambda _arguments: (once_process, Client(), 0.01))
    common = [
        "--state-dir",
        str(tmp_path),
        "--profile",
        str(tmp_path / "profile.yaml"),
        "--config",
        str(tmp_path / "config.yaml"),
    ]
    monkeypatch.setattr(sys, "argv", ["mishkan-worker", "once", *common])
    worker_main.main()
    assert json.loads(capsys.readouterr().out) == {"executed": True}

    attempts = iter(
        (
            MishkanError(ErrorCode.WORKER, "transient worker failure", retryable=True),
            KeyboardInterrupt(),
        )
    )

    def run_once() -> bool:
        outcome = next(attempts)
        raise outcome

    monkeypatch.setattr(
        worker_main,
        "_process",
        lambda _arguments: (SimpleNamespace(run_once=run_once), Client(), 0.01),
    )
    sleeps: list[float] = []
    monkeypatch.setattr(worker_main.time, "sleep", sleeps.append)
    monkeypatch.setattr(sys, "argv", ["mishkan-worker", "run", *common])
    worker_main.main()

    error = json.loads(capsys.readouterr().err)
    assert error["code"] == ErrorCode.WORKER.value
    assert sleeps == [0.01]
