"""Use a provisioned TLS leaf and certificate registry without opening CA keys."""
from __future__ import annotations

from pathlib import Path
import ssl

from cryptography import x509
from cryptography.hazmat.primitives import serialization

from quantum_autopilot.security.http_boundary import strict_object
from quantum_autopilot.security.pki import CertificatePolicy, CertificateRejected, enforce_tls13


def load_leaf(path: Path | str) -> dict:
    path = Path(path)
    if path.stat().st_size > 65536:
        raise ValueError("certificate descriptor too large")
    leaf = strict_object(path.read_bytes())
    expected = {"identity", "serial", "certificate", "private_key", "password_ref", "ca", "kind", "services"}
    if set(leaf) != expected or leaf["kind"] not in {"sae", "kme", "service"}:
        raise ValueError("invalid provisioned certificate descriptor")
    if not isinstance(leaf["services"], list) or not leaf["services"] or any(
            not isinstance(value, str) for value in leaf["services"]):
        raise ValueError("invalid provisioned certificate services")
    for key in ("identity", "serial", "password_ref"):
        if not isinstance(leaf[key], str) or not leaf[key] or len(leaf[key]) > 512:
            raise ValueError("invalid certificate binding")
    for key in ("certificate", "private_key", "ca"):
        if not isinstance(leaf[key], str) or not Path(leaf[key]).is_absolute():
            raise ValueError("certificate paths must be absolute")
    return leaf


def leaf_descriptor(leaf: dict) -> dict:
    return {key: list(value) if key == "services" else str(value)
            for key, value in leaf.items() if key != "policy"}


class ServiceTLS:
    """TLS 1.3 + per-request revocation, shared with the existing PKI policy.

    Only public CA, the service's encrypted leaf and a provisioned policy MAC
    are needed. Issuing-authority private keys are an offline provisioning
    concern and need not be mounted in the service container.
    """

    def __init__(self, root, runtime, leaf: dict, *, identity: str):
        root = Path(root).resolve()
        runtime.authorize("credentials.use", "certificate:" + identity)
        if runtime.profile != "operational" or runtime.identity_profile != "service":
            raise ValueError("headless TLS requires operational service identity")
        if (leaf["identity"] != identity or leaf["kind"] != "service" or "dashboard" not in leaf["services"]
                or leaf["password_ref"] != "pki-leaf-password:" + identity
                or Path(leaf["ca"]).resolve() != root / "ca.pem"):
            raise CertificateRejected("server certificate binding rejected")
        if not (root / "registry.sqlite").is_file() or not (root / "ca.pem").is_file():
            raise CertificateRejected("certificate policy has not been provisioned")
        self.runtime, self.leaf = runtime, leaf
        self.policy = CertificatePolicy(root / "registry.sqlite", runtime.vault.get_bytes("pki-policy-mac-v1"),
                                        vault=runtime.vault,
                                        journal_path=runtime.root / "pki-recovery" / "revocations.jsonl")
        self._server_der = x509.load_pem_x509_certificate(Path(leaf["certificate"]).read_bytes()).public_bytes(
            serialization.Encoding.DER)
        self._validate_server()

    def _validate_server(self):
        self.runtime.principal(self.leaf["identity"])
        certificate = x509.load_der_x509_certificate(self._server_der)
        if str(certificate.serial_number) != self.leaf["serial"]:
            raise CertificateRejected("server certificate serial rejected")
        self.policy.authorize(self._server_der, "dashboard", expected_identity=self.leaf["identity"],
                              expected_kind="service", usage="server")

    def server_context(self) -> ssl.SSLContext:
        self._validate_server()
        context = enforce_tls13(ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER))
        context.load_cert_chain(self.leaf["certificate"], self.leaf["private_key"],
                                password=self.runtime.vault.get_bytes(self.leaf["password_ref"]))
        context.load_verify_locations(cafile=self.leaf["ca"])
        context.verify_mode = ssl.CERT_REQUIRED
        return context

    def authorize_peer(self, der: bytes):
        self._validate_server()
        binding = self.policy.authorize(der, "dashboard", expected_kind="service")
        return self.runtime.principal(binding["identity"])
