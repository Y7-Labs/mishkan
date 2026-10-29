"""CrewAI execution of one coordinator-issued immutable remote task."""

from __future__ import annotations

import base64
import json
from collections.abc import Mapping
from typing import cast

from crewai import Agent, Crew, Process, Task
from crewai.crews.crew_output import CrewOutput
from pydantic import BaseModel, ConfigDict, Field

from mishkan.config.models import MishkanConfig
from mishkan.crewai.routing import CrewAIModelRouter
from mishkan.domain.errors import ErrorCode, MishkanError
from mishkan.planning.models import PlanTask
from mishkan.workers.models import ImmutableTaskEnvelope, WorkerExecutionResult


class _CrewAIWorkerOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    result: dict[str, object] = Field(min_length=1)


class CrewAIWorkerTaskExecutor:
    """Execute accepted task content without acquiring planning or acceptance authority."""

    def __init__(
        self,
        config: MishkanConfig,
        *,
        model_route: str,
        max_input_bytes: int,
    ) -> None:
        if max_input_bytes < 1:
            raise MishkanError(ErrorCode.CONFIGURATION, "worker input bound is invalid")
        self._config = config
        self._model_route = model_route
        self._max_input_bytes = max_input_bytes
        self._models = CrewAIModelRouter(config)

    def execute(
        self,
        envelope: ImmutableTaskEnvelope,
        inputs: Mapping[str, bytes],
    ) -> WorkerExecutionResult:
        contract = PlanTask.model_validate(envelope.task_contract)
        expected_names = {item.name for item in envelope.inputs}
        if set(inputs) != expected_names:
            raise MishkanError(
                ErrorCode.OUTPUT_CONTRACT,
                "materialized worker inputs differ from the immutable envelope",
            )
        encoded: list[dict[str, str]] = []
        total = 0
        for reference in envelope.inputs:
            content = inputs[reference.name]
            total += len(content)
            if total > self._max_input_bytes:
                raise MishkanError(ErrorCode.ARTIFACT, "worker inputs exceed configured bounds")
            encoded.append(
                {
                    "name": reference.name,
                    "artifact_id": str(reference.artifact_id),
                    "digest": reference.digest,
                    "content_base64": base64.b64encode(content).decode(),
                }
            )
        description = f"""
Execute exactly one already accepted MISHKAN task from its immutable envelope.

Task contract: {json.dumps(contract.model_dump(mode="json"), sort_keys=True)}
Task attempt: {envelope.task_attempt}
Execution-context revision: {envelope.execution_context_revision}
Repository revision: {envelope.repository_revision}
Role-definition fingerprint: {envelope.role_definition_fingerprint}
Policy fingerprint: {envelope.policy_fingerprint}
Immutable input evidence: {json.dumps(encoded, sort_keys=True)}

Return only a non-empty result object. Do not plan other work, delegate, authorize an effect,
claim acceptance, or invent evidence. The coordinator validates this result and decides acceptance.
No tools are exposed by this execution boundary.
""".strip()
        failures: list[str] = []
        for llm in self._models.candidates_for(self._model_route):
            try:
                agent = Agent(
                    role=contract.assigned_role,
                    goal=(
                        "Produce the exact bounded result requested by the accepted task contract."
                    ),
                    backstory=(
                        "You are a leased MISHKAN worker agent. The coordinator owns planning, "
                        "policy, effects, validation, and acceptance."
                    ),
                    llm=llm,
                    tools=[],
                    allow_delegation=False,
                    allow_code_execution=False,
                    max_iter=self._config.crewai.max_agent_iterations,
                    max_retry_limit=0,
                    verbose=False,
                )
                task = Task(
                    description=description,
                    expected_output="One non-empty JSON result object grounded only in the inputs.",
                    agent=agent,
                    tools=[],
                    output_pydantic=_CrewAIWorkerOutput,
                )
                output = cast(
                    CrewOutput,
                    Crew(
                        agents=[agent],
                        tasks=[task],
                        process=Process.sequential,
                        tracing=self._config.crewai.tracing,
                        verbose=False,
                    ).kickoff(),
                )
                parsed = (
                    output.pydantic
                    if isinstance(output.pydantic, _CrewAIWorkerOutput)
                    else _CrewAIWorkerOutput.model_validate_json(output.raw)
                )
                result = dict(parsed.result)
                serialized = json.dumps(result, sort_keys=True, separators=(",", ":")).encode()
                if len(serialized) > envelope.resources.max_result_bytes:
                    raise MishkanError(
                        ErrorCode.OUTPUT_CONTRACT,
                        "CrewAI worker result exceeds the immutable envelope bound",
                    )
                return WorkerExecutionResult(result=result)
            except Exception as exc:
                failures.append(type(exc).__name__)
        raise MishkanError(
            ErrorCode.REQUIRED_DEPENDENCY,
            "all configured CrewAI worker model candidates failed",
            details={"route": self._model_route, "failure_types": failures},
            retryable=True,
        )
