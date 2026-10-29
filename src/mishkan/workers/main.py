"""Command-line entry point for enrollment and stateless leased execution."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from uuid import UUID, uuid4

import yaml
from pydantic import SecretStr, ValidationError

from mishkan.config import ConfigLoader
from mishkan.crewai.environment import configure_crewai_environment
from mishkan.domain.errors import ErrorCode, MishkanError
from mishkan.workers.client import WorkerHttpClient
from mishkan.workers.models import WorkerRuntimeProfile
from mishkan.workers.process import WorkerProcess, WorkerRuntimeJournal
from mishkan.workers.state import WorkerCredentialStore


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="mishkan-worker")
    subcommands = parser.add_subparsers(dest="command", required=True)
    enroll = subcommands.add_parser("enroll", help="Create a private mTLS worker identity.")
    enroll.add_argument("--state-dir", type=Path, required=True)
    enroll.add_argument("--daemon-url", required=True)
    enroll.add_argument("--display-name", required=True)
    enroll.add_argument("--server-ca", type=Path, required=True)
    enroll.add_argument("--worker-id", type=UUID, default=None)
    enroll.add_argument("--token-env", default="MISHKAN_WORKER_ENROLLMENT_TOKEN")
    enroll.add_argument("--timeout", type=float, default=120)

    rotate = subcommands.add_parser("rotate", help="Rotate and recover the local mTLS identity.")
    rotate.add_argument("--state-dir", type=Path, required=True)
    rotate.add_argument("--timeout", type=float, default=120)

    for name in ("once", "run"):
        command = subcommands.add_parser(name)
        command.add_argument("--state-dir", type=Path, required=True)
        command.add_argument("--profile", type=Path, required=True)
        command.add_argument("--config", type=Path, action="append", required=True)
    return parser


def _profile(path: Path) -> WorkerRuntimeProfile:
    try:
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
        return WorkerRuntimeProfile.model_validate(document)
    except (OSError, ValueError, ValidationError, yaml.YAMLError) as exc:
        raise MishkanError(ErrorCode.CONFIGURATION, "worker runtime profile is invalid") from exc


def _process(arguments: argparse.Namespace) -> tuple[WorkerProcess, WorkerHttpClient, float]:
    store = WorkerCredentialStore(arguments.state_dir)
    identity = store.load()
    profile = _profile(arguments.profile)
    effective = ConfigLoader().load(arguments.config)
    configure_crewai_environment(
        effective.value.crewai,
        arguments.state_dir / "crewai",
    )
    from mishkan.workers.executor import CrewAIWorkerTaskExecutor

    client = WorkerHttpClient(
        identity.daemon_url,
        ca_certificate=store.paths.server_ca,
        client_certificate=store.paths.certificate,
        client_private_key=store.paths.private_key,
        timeout_seconds=profile.request_timeout_seconds,
    )
    executor = CrewAIWorkerTaskExecutor(
        effective.value,
        model_route=profile.model_route,
        max_input_bytes=profile.max_input_bytes,
    )
    return (
        WorkerProcess(
            identity,
            profile,
            client,
            executor,
            WorkerRuntimeJournal(arguments.state_dir),
        ),
        client,
        profile.idle_poll_seconds,
    )


def main() -> None:
    arguments = _parser().parse_args()
    try:
        if arguments.command == "enroll":
            raw_token = os.environ.get(arguments.token_env)
            if raw_token is None:
                raise MishkanError(
                    ErrorCode.CONFIGURATION,
                    "worker enrollment token environment variable is absent",
                    details={"environment": arguments.token_env},
                )
            identity = WorkerCredentialStore(arguments.state_dir).initialize(
                daemon_url=arguments.daemon_url,
                display_name=arguments.display_name,
                worker_id=arguments.worker_id or uuid4(),
                enrollment_token=SecretStr(raw_token),
                server_ca=arguments.server_ca,
                timeout_seconds=arguments.timeout,
            )
            print(identity.model_dump_json(indent=2))
            return
        if arguments.command == "rotate":
            identity = WorkerCredentialStore(arguments.state_dir).rotate(
                timeout_seconds=arguments.timeout
            )
            print(identity.model_dump_json(indent=2))
            return
        process, client, idle_seconds = _process(arguments)
        with client:
            if arguments.command == "once":
                print(json.dumps({"executed": process.run_once()}))
                return
            while True:
                try:
                    executed = process.run_once()
                except MishkanError as error:
                    print(error.envelope.model_dump_json(), file=sys.stderr)
                    executed = False
                if not executed:
                    time.sleep(idle_seconds)
    except KeyboardInterrupt:
        return
    except MishkanError as error:
        print(error.envelope.model_dump_json(), file=sys.stderr)
        raise SystemExit(2) from error


if __name__ == "__main__":
    main()
