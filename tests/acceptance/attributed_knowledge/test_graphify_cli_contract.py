from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[3]


def _require_docker() -> str:
    docker = shutil.which("docker")
    if sys.platform != "linux" or docker is None:
        pytest.skip("real Graphify contract gate requires Linux and Docker")
    completed = subprocess.run(
        [docker, "info", "--format", "{{.ServerVersion}}"],
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        pytest.skip("Docker daemon is unavailable")
    return docker


@pytest.mark.acceptance
@pytest.mark.container
def test_pinned_graphify_cli_performs_real_incremental_code_refresh(tmp_path: Path) -> None:
    docker = _require_docker()
    image = subprocess.run(
        [
            docker,
            "build",
            "--quiet",
            "--file",
            str(ROOT / "deploy/knowledge/graphify.Dockerfile"),
            str(ROOT),
        ],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    repository = tmp_path / "repository"
    repository.mkdir()
    (repository / "sample.py").write_text(
        "def hello():\n    return 'world'\n",
        encoding="utf-8",
    )

    version = subprocess.run(
        [
            docker,
            "run",
            "--rm",
            "--entrypoint",
            "python",
            image,
            "-c",
            "import importlib.metadata; print(importlib.metadata.version('graphifyy'))",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    refreshed = subprocess.run(
        [
            docker,
            "run",
            "--rm",
            "--entrypoint",
            "graphify",
            "--volume",
            f"{repository}:/repository",
            image,
            "update",
            "/repository",
            "--no-cluster",
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    graph = json.loads((repository / "graphify-out/graph.json").read_text(encoding="utf-8"))
    assert version.stdout.strip() == "0.9.67"
    assert "Code graph updated" in refreshed.stdout
    assert graph["nodes"]
    assert graph["links"]
