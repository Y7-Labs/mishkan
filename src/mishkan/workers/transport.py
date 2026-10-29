"""Pinned HTTPS transport boundary exposing verified client certificates to ASGI."""

from __future__ import annotations

import asyncio
import os
import ssl
import tempfile
from contextlib import AbstractContextManager
from pathlib import Path
from types import TracebackType
from typing import Any

import uvicorn
from starlette.types import ASGIApp, Receive, Scope, Send
from uvicorn.config import Config
from uvicorn.protocols.http.h11_impl import H11Protocol
from uvicorn.server import ServerState

from mishkan.domain.errors import ErrorCode, MishkanError

PINNED_UVICORN_VERSION = "0.52.4"
PEER_CERTIFICATE_EXTENSION = "mishkan.peer_certificate_der"


class PeerCertificateH11Protocol(H11Protocol):
    """Fail-closed Uvicorn adapter pinned to the reviewed H11 protocol contract."""

    def __init__(
        self,
        config: Config,
        server_state: ServerState,
        app_state: dict[str, Any],
        _loop: asyncio.AbstractEventLoop | None = None,
    ) -> None:
        if uvicorn.__version__ != PINNED_UVICORN_VERSION:
            raise RuntimeError(
                "distributed TLS adapter requires reviewed Uvicorn "
                f"{PINNED_UVICORN_VERSION}; observed {uvicorn.__version__}"
            )
        super().__init__(config, server_state, app_state, _loop)
        original = self.app
        self._peer_certificate_der: bytes | None = None

        async def expose_peer_certificate(
            scope: Scope,
            receive: Receive,
            send: Send,
        ) -> None:
            if scope["type"] == "http":
                if scope.get("scheme") != "https":
                    await self._reject_plain_http(send)
                    return
                scoped = dict(scope)
                extensions = dict(scope.get("extensions", {}))
                extensions[PEER_CERTIFICATE_EXTENSION] = self._peer_certificate_der
                scoped["extensions"] = extensions
                await original(scoped, receive, send)
                return
            await original(scope, receive, send)

        self.app: ASGIApp = expose_peer_certificate

    def connection_made(  # type: ignore[override]
        self,
        transport: asyncio.Transport,
    ) -> None:
        super().connection_made(transport)
        ssl_object = transport.get_extra_info("ssl_object")
        if ssl_object is None:
            transport.close()
            return
        peer = ssl_object.getpeercert(binary_form=True)
        self._peer_certificate_der = bytes(peer) if peer is not None else None

    @staticmethod
    async def _reject_plain_http(send: Send) -> None:
        body = b'{"detail":"distributed worker listener requires TLS"}'
        await send(
            {
                "type": "http.response.start",
                "status": 503,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode()),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})


class DistributedTlsFiles(AbstractContextManager["DistributedTlsFiles"]):
    """Materialize resolved PEM values in a private, process-lifetime directory."""

    def __init__(self, server_certificate: str, server_key: str, worker_ca: str) -> None:
        self._values = {
            "server-certificate.pem": server_certificate,
            "server-private-key.pem": server_key,
            "worker-ca.pem": worker_ca,
        }
        self._temporary: tempfile.TemporaryDirectory[str] | None = None
        self.server_certificate: Path | None = None
        self.server_private_key: Path | None = None
        self.worker_ca: Path | None = None

    def __enter__(self) -> DistributedTlsFiles:
        self._temporary = tempfile.TemporaryDirectory(prefix="mishkan-mtls-")
        root = Path(self._temporary.name)
        os.chmod(root, 0o700)
        paths: dict[str, Path] = {}
        for name, value in self._values.items():
            path = root / name
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(value.encode())
                stream.flush()
                os.fsync(stream.fileno())
            paths[name] = path
        self.server_certificate = paths["server-certificate.pem"]
        self.server_private_key = paths["server-private-key.pem"]
        self.worker_ca = paths["worker-ca.pem"]
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if self._temporary is not None:
            self._temporary.cleanup()
        self._temporary = None

    def require_paths(self) -> tuple[Path, Path, Path]:
        if any(
            item is None
            for item in (self.server_certificate, self.server_private_key, self.worker_ca)
        ):
            raise MishkanError(ErrorCode.CONFIGURATION, "distributed TLS files are not active")
        assert self.server_certificate is not None
        assert self.server_private_key is not None
        assert self.worker_ca is not None
        return self.server_certificate, self.server_private_key, self.worker_ca


def distributed_ssl_context(
    config: Config,
    default_factory: Any,
) -> ssl.SSLContext:
    """Retain Uvicorn loading while fixing protocol and verification bounds."""

    context: ssl.SSLContext = default_factory()
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.verify_mode = ssl.CERT_OPTIONAL
    context.check_hostname = False
    return context


def peer_certificate_der(scope: Scope) -> bytes:
    extensions = scope.get("extensions", {})
    raw = extensions.get(PEER_CERTIFICATE_EXTENSION)
    if not isinstance(raw, bytes) or not raw:
        raise MishkanError(
            ErrorCode.WORKER,
            "a verified worker client certificate is required",
        )
    return raw
