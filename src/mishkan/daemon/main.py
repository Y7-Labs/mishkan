"""mishkand process entry point."""

from __future__ import annotations

import argparse
import os
import ssl
from pathlib import Path

import uvicorn

from mishkan.config.loader import ConfigLoader
from mishkan.config.models import OperatingMode
from mishkan.crewai.credentials import CredentialPoolResolver
from mishkan.daemon.api import create_app
from mishkan.daemon.bootstrap import DaemonPaths
from mishkan.daemon.composition import build_distributed_tls_files
from mishkan.workers.transport import PeerCertificateH11Protocol, distributed_ssl_context


def main() -> None:
    parser = argparse.ArgumentParser(prog="mishkand")
    parser.add_argument("--config", "-c", action="append", type=Path)
    arguments = parser.parse_args()
    encoded = os.environ.get("MISHKAN_CONFIG", "")
    sources = tuple(arguments.config or (Path(item) for item in encoded.split(os.pathsep) if item))
    effective = ConfigLoader().load(sources)
    paths = DaemonPaths.from_config(effective.value)
    del paths
    daemon = effective.value.daemon
    assert daemon is not None
    app = create_app(effective.value)
    if effective.value.mode is not OperatingMode.DISTRIBUTED:
        uvicorn.run(app, host=daemon.host, port=daemon.port)
        return
    with build_distributed_tls_files(
        effective.value,
        CredentialPoolResolver(),
    ) as material:
        server_certificate, server_private_key, worker_ca = material.require_paths()
        uvicorn.run(
            app,
            host=daemon.host,
            port=daemon.port,
            http=PeerCertificateH11Protocol,
            proxy_headers=False,
            ssl_certfile=server_certificate,
            ssl_keyfile=server_private_key,
            ssl_ca_certs=worker_ca,
            ssl_cert_reqs=ssl.CERT_OPTIONAL,
            ssl_context_factory=distributed_ssl_context,
        )


if __name__ == "__main__":
    main()
