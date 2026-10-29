from __future__ import annotations

import base64
import json
from datetime import timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
import yaml

import mishkan.workers.executor as executor_module
from mishkan.config.models import MishkanConfig
from mishkan.config.presets import preset_text
from mishkan.domain.errors import MishkanError
from mishkan.domain.time import utc_now
from mishkan.planning.models import InitializationResult, PlanTask
from mishkan.workers import ImmutableTaskEnvelope, TaskPathScope, TaskResourceLimits
from mishkan.workers.executor import CrewAIWorkerTaskExecutor


def _config() -> MishkanConfig:
    return MishkanConfig.model_validate(yaml.safe_load(preset_text("local")))


def _envelope(*, max_result_bytes: int = 4096) -> ImmutableTaskEnvelope:
    contract = PlanTask(
        task_id="remote-task",
        title="Synthesize immutable evidence",
        purpose="Return one bounded structured result.",
        assigned_role="Engineer",
        tools=("file.read",),
        evidence_paths=("README.md",),
    )
    return ImmutableTaskEnvelope(
        run_id=uuid4(),
        task_row_id=uuid4(),
        task_id=contract.task_id,
        task_attempt=1,
        worker_id=uuid4(),
        execution_context_revision="a" * 40,
        repository_id="e" * 64,
        repository_revision="a" * 40,
        plan_fingerprint="b" * 64,
        task_contract=contract.model_dump(mode="json"),
        role_definition_fingerprint="c" * 64,
        policy_fingerprint="d" * 64,
        path_scopes=(
            TaskPathScope(
                root_id="repository",
                relative_path="README.md",
                permissions=("read",),
            ),
        ),
        capability_requirements=(),
        resources=TaskResourceLimits(
            timeout_seconds=60,
            max_result_bytes=max_result_bytes,
        ),
        deadline=utc_now() + timedelta(minutes=5),
    )


def _crew_output() -> str:
    result = InitializationResult(
        repository_revision="a" * 40,
        task_id="remote-task",
        summary="Grounded remote result.",
        cited_paths=("README.md",),
        findings=("The accepted input supports this finding.",),
    )
    return json.dumps({"result": result.model_dump(mode="json")})


def test_remote_executor_uses_one_crewai_crew_without_tools_or_delegation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: dict[str, object] = {}

    class Agent:
        def __init__(self, **kwargs):  # type: ignore[no-untyped-def]
            observed["agent"] = kwargs

    class Task:
        def __init__(self, **kwargs):  # type: ignore[no-untyped-def]
            observed["task"] = kwargs

    class Crew:
        def __init__(self, **kwargs):  # type: ignore[no-untyped-def]
            observed["crew"] = kwargs

        def kickoff(self):  # type: ignore[no-untyped-def]
            return SimpleNamespace(pydantic=None, raw=_crew_output())

    monkeypatch.setattr(executor_module, "Agent", Agent)
    monkeypatch.setattr(executor_module, "Task", Task)
    monkeypatch.setattr(executor_module, "Crew", Crew)
    executor = CrewAIWorkerTaskExecutor(
        _config(),
        model_route="simple",
        max_input_bytes=1024,
    )
    monkeypatch.setattr(executor._models, "candidates_for", lambda _route: iter((object(),)))

    result = executor.execute(_envelope(), {})

    assert result.result["task_id"] == "remote-task"
    assert result.result["summary"] == "Grounded remote result."
    assert len(result.generated_artifacts) == 1
    assert (
        json.loads(base64.b64decode(result.generated_artifacts[0].content_base64)) == result.result
    )
    agent = observed["agent"]
    assert isinstance(agent, dict)
    assert agent["tools"] == []
    assert agent["allow_delegation"] is False
    assert agent["allow_code_execution"] is False
    crew = observed["crew"]
    assert isinstance(crew, dict)
    assert len(crew["agents"]) == 1
    assert len(crew["tasks"]) == 1


def test_remote_executor_enforces_immutable_result_bound(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Crew:
        def __init__(self, **_kwargs):  # type: ignore[no-untyped-def]
            pass

        def kickoff(self):  # type: ignore[no-untyped-def]
            return SimpleNamespace(pydantic=None, raw=_crew_output())

    monkeypatch.setattr(executor_module, "Agent", lambda **_kwargs: object())
    monkeypatch.setattr(executor_module, "Task", lambda **_kwargs: object())
    monkeypatch.setattr(executor_module, "Crew", Crew)
    executor = CrewAIWorkerTaskExecutor(
        _config(),
        model_route="simple",
        max_input_bytes=1024,
    )
    monkeypatch.setattr(executor._models, "candidates_for", lambda _route: iter((object(),)))

    with pytest.raises(MishkanError, match="all configured CrewAI worker model candidates failed"):
        executor.execute(_envelope(max_result_bytes=1), {})
