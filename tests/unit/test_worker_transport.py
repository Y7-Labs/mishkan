from __future__ import annotations

import asyncio
import os
import socket
import ssl
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
import uvicorn
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID
from starlette.types import Message, Receive, Scope, Send
from uvicorn.config import Config
from uvicorn.server import ServerState

from mishkan.domain.errors import MishkanError
from mishkan.domain.time import utc_now
from mishkan.workers.pki import WorkerCertificateAuthority
from mishkan.workers.transport import (
    PEER_CERTIFICATE_EXTENSION,
    DistributedTlsFiles,
    PeerCertificateH11Protocol,
    distributed_ssl_context,
    peer_certificate_der,
)


def _authority() -> tuple[object, str, str]:
    key = ec.generate_private_key(ec.SECP256R1())
    now = utc_now()
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "MISHKAN transport CA")])
    certificate = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=True,
                crl_sign=True,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .sign(key, hashes.SHA256())
    )
    return (
        key,
        certificate.public_bytes(serialization.Encoding.PEM).decode(),
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ).decode(),
    )


def _server_certificate(ca_key: object, ca_pem: str) -> tuple[str, str]:
    ca = x509.load_pem_x509_certificate(ca_pem.encode())
    key = ec.generate_private_key(ec.SECP256R1())
    now = utc_now()
    certificate = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "127.0.0.1")]))
        .issuer_name(ca.subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(hours=1))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.SubjectAlternativeName(
                [x509.IPAddress(__import__("ipaddress").ip_address("127.0.0.1"))]
            ),
            critical=False,
        )
        .add_extension(
            x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]),
            critical=True,
        )
        .sign(ca_key, hashes.SHA256())  # type: ignore[arg-type]
    )
    return (
        certificate.public_bytes(serialization.Encoding.PEM).decode(),
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ).decode(),
    )


def _worker_material(ca_pem: str, ca_key_pem: str) -> tuple[str, str]:
    key = ec.generate_private_key(ec.SECP256R1())
    csr = (
        x509.CertificateSigningRequestBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "worker")]))
        .sign(key, hashes.SHA256())
        .public_bytes(serialization.Encoding.PEM)
        .decode()
    )
    issued = WorkerCertificateAuthority(ca_pem, ca_key_pem).sign_worker_csr(
        uuid4(), csr, lifetime_seconds=3_600
    )
    return (
        issued.certificate_pem,
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ).decode(),
    )


def test_tls_files_are_private_and_removed(tmp_path: Path) -> None:
    del tmp_path
    material = DistributedTlsFiles("certificate", "key", "ca")
    with material:
        paths = material.require_paths()
        root = paths[0].parent
        assert all((path.stat().st_mode & 0o777) == 0o600 for path in paths)
        assert (root.stat().st_mode & 0o777) == 0o700
    assert not root.exists()


@pytest.mark.anyio
async def test_protocol_injects_peer_certificate_and_rejects_plain_http() -> None:
    observed: list[Scope] = []

    async def application(scope: Scope, receive: Receive, send: Send) -> None:
        del receive
        observed.append(scope)
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    config = Config(application, lifespan="off")
    protocol = PeerCertificateH11Protocol(config, ServerState(), {})
    protocol._peer_certificate_der = b"verified-der"
    messages: list[Message] = []

    async def receive() -> Message:
        return {"type": "http.disconnect"}

    async def send(message: Message) -> None:
        messages.append(message)

    base: Scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "https",
        "path": "/",
        "raw_path": b"/",
        "query_string": b"",
        "root_path": "",
        "headers": [],
        "client": ("127.0.0.1", 1234),
        "server": ("127.0.0.1", 8888),
    }
    await protocol.app(base, receive, send)
    assert observed[0]["extensions"][PEER_CERTIFICATE_EXTENSION] == b"verified-der"
    assert peer_certificate_der(observed[0]) == b"verified-der"

    plain = dict(base)
    plain["scheme"] = "http"
    await protocol.app(plain, receive, send)
    assert len(observed) == 1
    assert any(item.get("status") == 503 for item in messages)


def test_peer_certificate_is_required_by_worker_routes() -> None:
    with pytest.raises(MishkanError, match="certificate is required"):
        peer_certificate_der({"type": "http", "extensions": {}})


@pytest.mark.anyio
async def test_real_https_listener_accepts_optional_then_verified_worker_certificate(
    tmp_path: Path,
) -> None:
    ca_key, ca_pem, ca_key_pem = _authority()
    server_pem, server_key_pem = _server_certificate(ca_key, ca_pem)
    worker_pem, worker_key_pem = _worker_material(ca_pem, ca_key_pem)
    worker_certificate = tmp_path / "worker.pem"
    worker_key = tmp_path / "worker-key.pem"
    worker_certificate.write_text(worker_pem, encoding="utf-8")
    worker_key.write_text(worker_key_pem, encoding="utf-8")
    os.chmod(worker_certificate, 0o600)
    os.chmod(worker_key, 0o600)

    async def application(scope: Scope, receive: Receive, send: Send) -> None:
        del receive
        peer = scope.get("extensions", {}).get(PEER_CERTIFICATE_EXTENSION)
        body = b"peer" if peer else b"anonymous"
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"content-length", str(len(body)).encode())],
            }
        )
        await send({"type": "http.response.body", "body": body})

    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    port = listener.getsockname()[1]
    with DistributedTlsFiles(server_pem, server_key_pem, ca_pem) as material:
        server_certificate, server_key, worker_ca = material.require_paths()
        config = Config(
            application,
            http=PeerCertificateH11Protocol,
            lifespan="off",
            log_level="warning",
            proxy_headers=False,
            ssl_certfile=server_certificate,
            ssl_keyfile=server_key,
            ssl_ca_certs=worker_ca,
            ssl_cert_reqs=ssl.CERT_OPTIONAL,
            ssl_context_factory=distributed_ssl_context,
        )
        server = uvicorn.Server(config)
        task = asyncio.create_task(server.serve(sockets=[listener]))
        for _ in range(100):
            if server.started:
                break
            await asyncio.sleep(0.01)
        assert server.started
        anonymous_context = ssl.create_default_context(cadata=ca_pem)
        worker_context = ssl.create_default_context(cadata=ca_pem)
        worker_context.load_cert_chain(worker_certificate, worker_key)
        try:
            async with httpx.AsyncClient(verify=anonymous_context) as client:
                response = await client.get(f"https://127.0.0.1:{port}/")
                assert response.text == "anonymous"
            async with httpx.AsyncClient(verify=worker_context) as client:
                response = await client.get(f"https://127.0.0.1:{port}/")
                assert response.text == "peer"
        finally:
            server.should_exit = True
            await task
