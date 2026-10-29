from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import httpx
import pytest

from mishkan.client import Mishkan
from mishkan.daemon.auth import TokenFile
from mishkan.events import EventEnvelope, EventPage
from mishkan.planning.models import InitializationResult, ReviewDecision
from mishkan.scheduling import DateTriggerSpec, ScheduleCreateRequest, ScheduleMissionRequest


def _event(cursor: int) -> EventEnvelope:
    return EventEnvelope(
        event_id=uuid4(),
        cursor=cursor,
        event_type="system.checkpoint_recorded",
        source="mishkand",
        entity_type="system",
        entity_id="local-instance",
        occurred_at=datetime.now(UTC),
        sensitivity="internal",
        payload={"cursor": cursor},
    )


def test_event_export_is_paginated_atomic_jsonl(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    token_file = tmp_path / "token.json"
    TokenFile(token_file).create("operator")
    client = Mishkan("http://127.0.0.1:8888", token_file=token_file)

    def events(*, after: int = 0, limit: int | None = None, event_types=()):  # type: ignore[no-untyped-def]
        del limit, event_types
        values = tuple(_event(cursor) for cursor in range(after + 1, min(after + 3, 4)))
        return EventPage(
            after_cursor=after,
            next_cursor=values[-1].cursor if values else after,
            retained_from_cursor=1,
            events=values,
        )

    monkeypatch.setattr(client, "events", events)
    output = tmp_path / "exports" / "events.jsonl"
    count, cursor = client.export_events_jsonl(output, page_size=2)
    client.close()

    documents = [json.loads(line) for line in output.read_text().splitlines()]
    assert count == 3
    assert cursor == 3
    assert [document["cursor"] for document in documents] == [1, 2, 3]
    assert not tuple(output.parent.glob("*.tmp"))


def test_event_export_rejects_unbounded_page_size(tmp_path: Path) -> None:
    token_file = tmp_path / "token.json"
    TokenFile(token_file).create("operator")
    with (
        Mishkan("http://127.0.0.1:8888", token_file=token_file) as client,
        pytest.raises(ValueError),
    ):
        client.export_events_jsonl(tmp_path / "events.jsonl", page_size=1001)


def test_sdk_emits_versioned_prospective_and_repository_establishment_commands(
    tmp_path: Path,
) -> None:
    token_file = tmp_path / "token.json"
    TokenFile(token_file).create("PM")
    commands: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        command = json.loads(request.content)
        commands.append(command)
        payload = (
            {
                "run_id": "run-prospective",
                "execution_context": {"kind": "prospective_workspace"},
            }
            if command["command_type"] == "run.prospective.create"
            else {
                "run_id": "run-prospective",
                "repository_establishment": {"schema_version": "1.0"},
            }
        )
        return httpx.Response(
            200,
            json={
                "schema_version": "1.0",
                "command_id": command["command_id"],
                "status": "accepted",
                "target_type": command["target_type"],
                "target_id": command["target_id"],
                "revision": 1,
                "event_cursor": len(commands),
                "payload": payload,
                "error": None,
                "completed_at": datetime.now(UTC).isoformat(),
            },
        )

    with Mishkan("http://mishkand.test", token_file=token_file) as client:
        client._client.close()
        client._client = httpx.Client(
            base_url="http://mishkand.test",
            transport=httpx.MockTransport(handler),
        )
        created = client.create_prospective_run(
            workspace_id="prospective:sdk-fixture",
            objective="Build the prospective service",
            outcome_id="greenfield",
        )
        established = client.establish_repository(
            "run-prospective",
            prospective_workspace_id="prospective:sdk-fixture",
            discovery_revision="a" * 64,
            evidence_references=("artifact:establishment",),
        )

    assert created["run_id"] == "run-prospective"
    assert established["repository_establishment"] == {"schema_version": "1.0"}
    assert [command["command_type"] for command in commands] == [
        "run.prospective.create",
        "run.repository.establish",
    ]
    assert commands[0]["target_id"] is None
    assert commands[0]["payload"] == {
        "schema_version": "1.0",
        "workspace_id": "prospective:sdk-fixture",
        "objective": "Build the prospective service",
        "outcome_id": "greenfield",
    }
    assert commands[1]["target_id"] == "run-prospective"
    assert commands[1]["payload"] == {
        "schema_version": "1.0",
        "prospective_workspace_id": "prospective:sdk-fixture",
        "discovery_revision": "a" * 64,
        "evidence_references": ["artifact:establishment"],
    }


def test_schedule_sdk_emits_the_governed_versioned_command(tmp_path: Path) -> None:
    token_file = tmp_path / "token.json"
    TokenFile(token_file).create("PM")
    run_at = datetime(2099, 1, 1, tzinfo=UTC)
    request = ScheduleCreateRequest(
        mission=ScheduleMissionRequest(project_id="project-a", objective="Run verified checks"),
        trigger=DateTriggerSpec(run_at=run_at),
    )
    commands: list[dict[str, object]] = []

    def handler(http_request: httpx.Request) -> httpx.Response:
        command = json.loads(http_request.content)
        commands.append(command)
        now = datetime.now(UTC).isoformat()
        schedule = {
            "schema_version": "1.0",
            "schedule_id": str(request.schedule_id),
            "mission": request.mission.model_dump(mode="json"),
            "trigger": request.trigger.model_dump(mode="json"),
            "state": "active",
            "overlap_key": request.mission.target_key,
            "overlap_mode": "block",
            "created_by": "PM",
            "preauthorization_policy_fingerprint": "a" * 64,
            "next_fire_at": run_at.isoformat(),
            "last_evaluated_at": None,
            "revision": 1,
            "created_at": now,
            "updated_at": now,
        }
        return httpx.Response(
            200,
            json={
                "schema_version": "1.0",
                "command_id": command["command_id"],
                "status": "accepted",
                "target_type": "schedule",
                "target_id": str(request.schedule_id),
                "revision": 1,
                "event_cursor": 1,
                "payload": {"schedule": schedule, "occurrence": None, "coalesced": []},
                "error": None,
                "completed_at": now,
            },
        )

    with Mishkan("http://mishkand.test", token_file=token_file) as client:
        client._client.close()
        client._client = httpx.Client(
            base_url="http://mishkand.test",
            transport=httpx.MockTransport(handler),
        )
        created = client.schedules.create(request)

    assert created.schedule_id == request.schedule_id
    assert commands[0]["command_type"] == "schedule.create"
    assert commands[0]["expected_revision"] == 0
    assert commands[0]["payload"] == {"request": request.model_dump(mode="json")}


def test_worker_sdk_keeps_one_time_secret_out_of_the_command(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token_file = tmp_path / "token.json"
    TokenFile(token_file).create("CTO")
    secret = "worker-enrollment-secret-with-sufficient-entropy"
    monkeypatch.setattr("mishkan.client.secrets.token_urlsafe", lambda _size: secret)
    commands: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        command = json.loads(request.content)
        commands.append(command)
        now = datetime.now(UTC)
        return httpx.Response(
            200,
            json={
                "schema_version": "1.0",
                "command_id": command["command_id"],
                "status": "accepted",
                "target_type": "worker_enrollment",
                "target_id": command["target_id"],
                "revision": 1,
                "event_cursor": 1,
                "payload": {
                    "token_id": command["target_id"],
                    "expires_at": (now + timedelta(minutes=10)).isoformat(),
                },
                "error": None,
                "completed_at": now.isoformat(),
            },
        )

    with Mishkan("http://mishkand.test", token_file=token_file) as client:
        client._client.close()
        client._client = httpx.Client(
            base_url="http://mishkand.test",
            transport=httpx.MockTransport(handler),
        )
        credential = client.workers.create_enrollment()

    assert credential.token.get_secret_value() == secret
    assert commands[0]["command_type"] == "worker.enrollment.create"
    serialized = json.dumps(commands[0])
    assert secret not in serialized
    assert commands[0]["payload"] == {"token_digest": hashlib.sha256(secret.encode()).hexdigest()}


def test_worker_sdk_exposes_bounded_availability_status(tmp_path: Path) -> None:
    token_file = tmp_path / "token.json"
    TokenFile(token_file).create("CTO")
    worker_id = uuid4()
    now = datetime.now(UTC).isoformat()
    status = {
        "identity": {
            "id": str(worker_id),
            "created_at": now,
            "display_name": "worker-without-heartbeat",
            "state": "active",
            "current_certificate_serial": None,
            "revision": 1,
            "updated_at": now,
            "revoked_at": None,
        },
        "availability": "never_seen",
        "heartbeat": None,
    }

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"].startswith("Bearer ")
        if request.url.path == "/v1/workers/status":
            assert dict(request.url.params) == {"offset": "2", "limit": "3"}
            return httpx.Response(200, json=[status])
        assert request.url.path == f"/v1/workers/{worker_id}/status"
        return httpx.Response(200, json=status)

    with Mishkan("http://mishkand.test", token_file=token_file) as client:
        client._client.close()
        client._client = httpx.Client(
            base_url="http://mishkand.test",
            transport=httpx.MockTransport(handler),
        )
        listed = client.workers.statuses(offset=2, limit=3)
        observed = client.workers.status(worker_id)

    assert listed == (observed,)
    assert observed.identity.id == worker_id
    assert observed.availability.value == "never_seen"


def test_worker_sdk_submits_governed_completion_acceptance(tmp_path: Path) -> None:
    token_file = tmp_path / "token.json"
    TokenFile(token_file).create("CTO")
    completion_id = uuid4()
    envelope_id = uuid4()
    lease_id = uuid4()
    worker_id = uuid4()
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

    def handler(request: httpx.Request) -> httpx.Response:
        command = json.loads(request.content)
        assert command["command_type"] == "worker.completion.accept"
        assert command["target_id"] == str(completion_id)
        assert command["payload"] == {
            "result": result.model_dump(mode="json"),
            "review": review.model_dump(mode="json"),
        }
        now = datetime.now(UTC).isoformat()
        completion = {
            "id": str(completion_id),
            "created_at": now,
            "envelope_id": str(envelope_id),
            "lease_id": str(lease_id),
            "worker_id": str(worker_id),
            "fingerprint": "f" * 64,
            "state": "accepted",
            "result": result.model_dump(mode="json"),
            "artifact_ids": [],
            "unified_patch_artifact_id": None,
            "effect_evidence_artifact_ids": [],
            "effect_report": "not_applicable",
            "received_at": now,
            "accepted_at": now,
        }
        return httpx.Response(
            200,
            json={
                "schema_version": "1.0",
                "command_id": command["command_id"],
                "status": "accepted",
                "target_type": "worker_completion",
                "target_id": str(completion_id),
                "revision": 1,
                "event_cursor": 1,
                "payload": completion,
                "error": None,
                "completed_at": now,
            },
        )

    with Mishkan("http://mishkand.test", token_file=token_file) as client:
        client._client.close()
        client._client = httpx.Client(
            base_url="http://mishkand.test",
            transport=httpx.MockTransport(handler),
        )
        accepted = client.workers.accept_completion(completion_id, result, review)

    assert accepted.id == completion_id
    assert accepted.state.value == "accepted"


def test_sdk_bounded_query_surfaces_use_authenticated_versioned_routes(tmp_path: Path) -> None:
    token_file = tmp_path / "token.json"
    credential = TokenFile(token_file).create("operator")
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/v1/health":
            return httpx.Response(200, json={"status": "ok"})
        if request.url.path == "/v1/context/community-candidates":
            return httpx.Response(200, json={"candidates": []})
        if request.url.path.endswith("/active"):
            return httpx.Response(200, content=b"null")
        return httpx.Response(200, json=[])

    with Mishkan("http://mishkand.test/", token_file=token_file) as client:
        client._client.close()
        client._client = httpx.Client(
            base_url="http://mishkand.test",
            transport=httpx.MockTransport(handler),
        )
        assert client.principal_id == "operator"
        assert client.health() == {"status": "ok"}
        assert client.community_candidates() == ()
        assert client.artifacts(offset=2, limit=3) == ()
        assert client.artifact_collections(offset=2, limit=3) == ()
        assert client.artifact_references(offset=2, limit=3) == ()
        assert client.artifact_holds(offset=2, limit=3) == ()
        assert client.artifact_pins(offset=2, limit=3) == ()
        assert client.change_sets(offset=2, limit=3) == ()
        assert client.sessions(offset=2, limit=3) == ()
        assert client.runs(offset=2, limit=3) == ()
        assert client.tasks("run/id", offset=2, limit=3) == ()
        assert client.skills(offset=2, limit=3, name="review") == ()
        assert client.active_skill("review/name") is None
        assert client.skill_learning(offset=2, limit=3) == ()
        assert client.skill_curation() == ()
        assert client.mcp_connections(offset=2, limit=3) == ()
        assert client.mcp_primitives("connection/id") == ()
        assert client.mcp_contracts("connection/id") == ()
        assert client.mcp_calls(offset=2, limit=3) == ()
        assert client.mcp_progress("request/id", cursor=4) == ()

    authenticated = [request for request in requests if request.url.path != "/v1/health"]
    assert authenticated
    assert all(
        request.headers["Authorization"] == f"Bearer {credential.token}"
        for request in authenticated
    )
    paths = {request.url.raw_path.decode() for request in requests}
    assert "/v1/skills/review%2Fname/active" in paths
    assert "/v1/runs/run/id/tasks?offset=2&limit=3" in paths
    assert "/v1/mcp/connections/connection%2Fid/primitives" in paths
    assert "/v1/mcp/calls/request%2Fid/progress?cursor=4" in paths
