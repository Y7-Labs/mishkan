"""Certificate authority boundary for worker-generated mTLS identities."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, cast
from uuid import UUID

from cryptography import x509
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, ed448, ed25519, rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from mishkan.domain.errors import ErrorCode, MishkanError
from mishkan.domain.time import utc_now

_WORKER_URI_PREFIX = "spiffe://mishkan/worker/"
_SigningPrivateKey = (
    rsa.RSAPrivateKey
    | ec.EllipticCurvePrivateKey
    | ed25519.Ed25519PrivateKey
    | ed448.Ed448PrivateKey
)
_SigningPublicKey = (
    rsa.RSAPublicKey | ec.EllipticCurvePublicKey | ed25519.Ed25519PublicKey | ed448.Ed448PublicKey
)


@dataclass(frozen=True, slots=True)
class SignedWorkerCertificate:
    certificate_pem: str
    certificate_authority_pem: str
    serial: str
    fingerprint_sha256: str
    not_before: datetime
    not_after: datetime


class WorkerCertificateAuthority:
    """Signs worker-owned public keys without ever receiving their private keys."""

    def __init__(self, certificate_pem: str, private_key_pem: str) -> None:
        try:
            certificate = x509.load_pem_x509_certificate(certificate_pem.encode())
            private_key = serialization.load_pem_private_key(
                private_key_pem.encode(),
                password=None,
            )
        except (TypeError, ValueError) as exc:
            raise MishkanError(ErrorCode.WORKER, "worker CA material is invalid") from exc
        if not isinstance(
            private_key,
            (
                rsa.RSAPrivateKey,
                ec.EllipticCurvePrivateKey,
                ed25519.Ed25519PrivateKey,
                ed448.Ed448PrivateKey,
            ),
        ):
            raise MishkanError(ErrorCode.WORKER, "worker CA key algorithm is unsupported")
        self._require_ca(certificate)
        if self._public_bytes(certificate.public_key()) != self._public_bytes(
            private_key.public_key()
        ):
            raise MishkanError(
                ErrorCode.WORKER,
                "worker CA certificate does not match its private key",
            )
        now = utc_now()
        if not (certificate.not_valid_before_utc <= now < certificate.not_valid_after_utc):
            raise MishkanError(ErrorCode.WORKER, "worker CA certificate is not currently valid")
        self._certificate = certificate
        self._private_key: _SigningPrivateKey = private_key
        self._certificate_pem = certificate.public_bytes(serialization.Encoding.PEM).decode()

    @property
    def certificate_pem(self) -> str:
        return self._certificate_pem

    def sign_worker_csr(
        self,
        worker_id: UUID,
        csr_pem: str,
        *,
        lifetime_seconds: int,
    ) -> SignedWorkerCertificate:
        if lifetime_seconds < 300:
            raise MishkanError(ErrorCode.WORKER, "worker certificate lifetime is too short")
        try:
            csr = x509.load_pem_x509_csr(csr_pem.encode())
        except ValueError as exc:
            raise MishkanError(ErrorCode.WORKER, "worker CSR is invalid") from exc
        if not csr.is_signature_valid:
            raise MishkanError(ErrorCode.WORKER, "worker CSR signature is invalid")
        self._require_worker_public_key(csr.public_key())

        now = utc_now()
        not_before = now - timedelta(seconds=30)
        not_after = min(
            now + timedelta(seconds=lifetime_seconds),
            self._certificate.not_valid_after_utc,
        )
        if not_after <= now:
            raise MishkanError(ErrorCode.WORKER, "worker CA expires before the worker certificate")
        uri = x509.UniformResourceIdentifier(f"{_WORKER_URI_PREFIX}{worker_id}")
        builder = (
            x509.CertificateBuilder()
            .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, str(worker_id))]))
            .issuer_name(self._certificate.subject)
            .public_key(csr.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(not_before)
            .not_valid_after(not_after)
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(
                x509.KeyUsage(
                    digital_signature=True,
                    content_commitment=False,
                    key_encipherment=isinstance(csr.public_key(), rsa.RSAPublicKey),
                    data_encipherment=False,
                    key_agreement=False,
                    key_cert_sign=False,
                    crl_sign=False,
                    encipher_only=False,
                    decipher_only=False,
                ),
                critical=True,
            )
            .add_extension(
                x509.ExtendedKeyUsage([ExtendedKeyUsageOID.CLIENT_AUTH]),
                critical=True,
            )
            .add_extension(x509.SubjectAlternativeName([uri]), critical=False)
            .add_extension(
                x509.SubjectKeyIdentifier.from_public_key(csr.public_key()),
                critical=False,
            )
            .add_extension(
                x509.AuthorityKeyIdentifier.from_issuer_public_key(self._issuer_public_key()),
                critical=False,
            )
        )
        certificate = builder.sign(
            private_key=self._private_key,
            algorithm=self._signature_hash(self._private_key),
        )
        return SignedWorkerCertificate(
            certificate_pem=certificate.public_bytes(serialization.Encoding.PEM).decode(),
            certificate_authority_pem=self._certificate_pem,
            serial=str(certificate.serial_number),
            fingerprint_sha256=certificate.fingerprint(hashes.SHA256()).hex(),
            not_before=certificate.not_valid_before_utc,
            not_after=certificate.not_valid_after_utc,
        )

    def inspect_worker_certificate(self, certificate_pem: str) -> tuple[UUID, str, str]:
        try:
            certificate = x509.load_pem_x509_certificate(certificate_pem.encode())
            certificate.verify_directly_issued_by(self._certificate)
            constraints = certificate.extensions.get_extension_for_class(
                x509.BasicConstraints
            ).value
            usage = certificate.extensions.get_extension_for_class(x509.ExtendedKeyUsage).value
            alternatives = certificate.extensions.get_extension_for_class(
                x509.SubjectAlternativeName
            ).value
        except (ValueError, InvalidSignature, x509.ExtensionNotFound) as exc:
            raise MishkanError(ErrorCode.WORKER, "worker certificate is not trusted") from exc
        if constraints.ca or ExtendedKeyUsageOID.CLIENT_AUTH not in usage:
            raise MishkanError(ErrorCode.WORKER, "certificate is not a worker client identity")
        uris = alternatives.get_values_for_type(x509.UniformResourceIdentifier)
        worker_uris = [uri for uri in uris if uri.startswith(_WORKER_URI_PREFIX)]
        if len(worker_uris) != 1:
            raise MishkanError(ErrorCode.WORKER, "worker certificate identity is ambiguous")
        try:
            worker_id = UUID(worker_uris[0][len(_WORKER_URI_PREFIX) :])
        except ValueError as exc:
            raise MishkanError(ErrorCode.WORKER, "worker certificate identity is invalid") from exc
        common_names = certificate.subject.get_attributes_for_oid(NameOID.COMMON_NAME)
        if len(common_names) != 1 or common_names[0].value != str(worker_id):
            raise MishkanError(
                ErrorCode.WORKER,
                "worker certificate subject does not match its URI",
            )
        return (
            worker_id,
            str(certificate.serial_number),
            certificate.fingerprint(hashes.SHA256()).hex(),
        )

    @staticmethod
    def _public_bytes(key: Any) -> bytes:
        return cast(
            bytes,
            key.public_bytes(
                serialization.Encoding.DER,
                serialization.PublicFormat.SubjectPublicKeyInfo,
            ),
        )

    def _issuer_public_key(self) -> _SigningPublicKey:
        key = self._certificate.public_key()
        if not isinstance(
            key,
            (
                rsa.RSAPublicKey,
                ec.EllipticCurvePublicKey,
                ed25519.Ed25519PublicKey,
                ed448.Ed448PublicKey,
            ),
        ):
            raise MishkanError(ErrorCode.WORKER, "worker CA key algorithm is unsupported")
        return key

    @staticmethod
    def _require_ca(certificate: x509.Certificate) -> None:
        try:
            constraints = certificate.extensions.get_extension_for_class(
                x509.BasicConstraints
            ).value
            usage = certificate.extensions.get_extension_for_class(x509.KeyUsage).value
        except x509.ExtensionNotFound as exc:
            raise MishkanError(ErrorCode.WORKER, "worker CA extensions are incomplete") from exc
        if not constraints.ca or not usage.key_cert_sign:
            raise MishkanError(ErrorCode.WORKER, "worker CA certificate cannot sign identities")

    @staticmethod
    def _require_worker_public_key(key: object) -> None:
        if isinstance(key, rsa.RSAPublicKey):
            if key.key_size < 3072:
                raise MishkanError(ErrorCode.WORKER, "worker RSA key must be at least 3072 bits")
            return
        if isinstance(key, ec.EllipticCurvePublicKey):
            if key.curve.key_size < 256:
                raise MishkanError(ErrorCode.WORKER, "worker EC key must be at least 256 bits")
            return
        if isinstance(key, (ed25519.Ed25519PublicKey, ed448.Ed448PublicKey)):
            return
        raise MishkanError(ErrorCode.WORKER, "worker public key algorithm is unsupported")

    @staticmethod
    def _signature_hash(private_key: _SigningPrivateKey) -> hashes.SHA256 | None:
        if isinstance(private_key, (ed25519.Ed25519PrivateKey, ed448.Ed448PrivateKey)):
            return None
        return hashes.SHA256()
