"""Provisioned TLS 1.3 identities with per-request certificate authorization.

Transport validation alone does not authorize a SAE/KME or revoke an already
established TLS session. The registry binds serial + fingerprint + SAN identity
to explicitly allowed services; revocation is rechecked for every operation.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from contextlib import contextmanager
import hmac
import ipaddress
import json
from pathlib import Path
import re
import sqlite3
import ssl

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from .key_lifecycle import ConsumptionJournal, RecoveryIntegrityError, exclusive_file, _atomic_bytes, _canonical


OPERATIONAL_PREFIX = "urn:orion:identity:"
LAB_PREFIX = "urn:orion:lab:"


class CertificateRejected(PermissionError):
    pass


def certificate_identity(certificate, *, prefix=OPERATIONAL_PREFIX):
    try:
        values = certificate.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
        uris = values.get_values_for_type(x509.UniformResourceIdentifier)
    except x509.ExtensionNotFound:
        uris = []
    identities = [value[len(prefix):] for value in uris if value.startswith(prefix)]
    if len(identities) != 1 or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", identities[0]):
        raise CertificateRejected("certificate identity binding rejected")
    return identities[0]


def enforce_tls13(context):
    context.minimum_version = ssl.TLSVersion.TLSv1_3
    context.maximum_version = ssl.TLSVersion.TLSv1_3
    context.options |= ssl.OP_NO_COMPRESSION
    # No early data or reusable server tickets for key-delivery operations.
    if hasattr(context, "num_tickets") and context.protocol == ssl.PROTOCOL_TLS_SERVER:
        context.num_tickets = 0
    return context


class CertificatePolicy:
    def __init__(self, path, secret, *, vault=None, journal_path=None, profile="operational"):
        self.path = Path(path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.secret, self.profile = secret, profile
        self.prefix = LAB_PREFIX if profile == "emulation" else OPERATIONAL_PREFIX
        self.journal = ConsumptionJournal(journal_path or self.path.parent / "revocations.jsonl", secret,
            vault=vault, namespace="pki-revocations", operational=profile == "operational")
        with self._db() as db:
            db.execute("CREATE TABLE IF NOT EXISTS certificates (serial TEXT PRIMARY KEY, binding TEXT NOT NULL, mac TEXT NOT NULL)")

    @contextmanager
    def _db(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.execute("PRAGMA synchronous=FULL")
        db.execute("PRAGMA busy_timeout=15000")
        try:
            with db:
                yield db
        finally:
            db.close()

    def register(self, certificate, *, kind, services):
        if (kind not in {"sae", "kme", "service"} or isinstance(services, str) or not services or
                len(services) > 32 or any(not isinstance(s, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", s) for s in services)):
            raise ValueError("certificate kind and allowed services required")
        identity = certificate_identity(certificate, prefix=self.prefix)
        binding = {"identity": identity, "kind": kind, "services": sorted(set(services)),
                   "fingerprint": certificate.fingerprint(hashes.SHA256()).hex(),
                   "expires": certificate.not_valid_after_utc.timestamp(), "profile": self.profile}
        encoded = _canonical(binding)
        mac = hmac.digest(self.secret, encoded, "sha256").hex()
        with self._db() as db:
            db.execute("INSERT INTO certificates VALUES (?,?,?)", (str(certificate.serial_number), encoded.decode(), mac))
        return binding

    def authorize(self, der, service, *, expected_identity=None, expected_kind=None, usage="client"):
        try:
            cert = x509.load_der_x509_certificate(der)
            identity = certificate_identity(cert, prefix=self.prefix)
            now = datetime.now(timezone.utc)
            if not cert.not_valid_before_utc <= now < cert.not_valid_after_utc:
                raise CertificateRejected("certificate validity rejected")
            eku = cert.extensions.get_extension_for_class(x509.ExtendedKeyUsage).value
            if (ExtendedKeyUsageOID.SERVER_AUTH if usage == "server" else ExtendedKeyUsageOID.CLIENT_AUTH) not in eku:
                raise CertificateRejected("certificate purpose rejected")
            with self._db() as db:
                row = db.execute("SELECT binding,mac FROM certificates WHERE serial=?", (str(cert.serial_number),)).fetchone()
            if row is None or not hmac.compare_digest(hmac.digest(self.secret, row[0].encode(), "sha256").hex(), row[1]):
                raise CertificateRejected("certificate registry binding rejected")
            binding = json.loads(row[0])
            fingerprint = cert.fingerprint(hashes.SHA256()).hex()
            revoked = self.journal.latest()
            if str(cert.serial_number) in revoked:
                raise CertificateRejected("certificate revoked")
            if (binding["fingerprint"] != fingerprint or binding["identity"] != identity or
                    binding["profile"] != self.profile or service not in binding["services"] or
                    (expected_identity is not None and identity != expected_identity) or
                    (expected_kind is not None and binding["kind"] != expected_kind)):
                raise CertificateRejected("certificate service authorization rejected")
            return {**binding, "serial": str(cert.serial_number)}
        except (ValueError, TypeError, KeyError, x509.ExtensionNotFound, RecoveryIntegrityError):
            raise CertificateRejected("certificate policy unavailable or invalid") from None

    def revoke(self, serial, *, reason="operator_revocation"):
        with self._db() as db:
            if not db.execute("SELECT 1 FROM certificates WHERE serial=?", (str(serial),)).fetchone():
                raise ValueError("unknown certificate serial")
        self.journal.append([str(serial)], "revoked", reason)


class OperationalPKI:
    """Local issuing authority; its private keys/passwords remain vault protected.

    Authority operations require SecurityRuntime administrator permission. This
    implements lifecycle mechanics, not external CA/HSM or OCSP qualification.
    """
    def __init__(self, root, runtime, *, revocation_root=None):
        self.root, self.runtime = Path(root).resolve(), runtime
        runtime.authorize("security.manage", "security:pki")
        self.root.mkdir(parents=True, exist_ok=True)
        if runtime.profile != "operational":
            raise ValueError("operational PKI requires the operational secret-vault profile")
        self.password = runtime.vault.get_or_create_bytes("pki-authority-password-v1", 32)
        secret = runtime.vault.get_or_create_bytes("pki-policy-mac-v1", 32)
        recovery_root = Path(revocation_root or runtime.root / "pki-recovery").resolve()
        if recovery_root == self.root or self.root in recovery_root.parents:
            raise ValueError("PKI revocations must be outside the certificate-backup directory")
        self.policy = CertificatePolicy(self.root / "registry.sqlite", secret, vault=runtime.vault,
                                       journal_path=recovery_root / "revocations.jsonl")
        self.ca_path, self.key_path = self.root / "ca.pem", self.root / "ca.encrypted.key"
        with exclusive_file(self.root / ".authority.lock"):
            if self.ca_path.exists() != self.key_path.exists():
                raise RecoveryIntegrityError("issuing authority files incomplete")
            if not self.ca_path.exists():
                runtime.authorize("security.manage", "security:pki")
                key = ec.generate_private_key(ec.SECP256R1())
                name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "ORION-Q provisioned issuing CA")])
                now = datetime.now(timezone.utc)
                ca = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
                    .serial_number(x509.random_serial_number()).not_valid_before(now - timedelta(minutes=1))
                    .not_valid_after(now + timedelta(days=3650))
                    .add_extension(x509.BasicConstraints(ca=True, path_length=0), True)
                    .add_extension(x509.KeyUsage(True, False, False, False, False, True, True, None, None), True)
                    .sign(key, hashes.SHA256()))
                _atomic_bytes(self.key_path, key.private_bytes(serialization.Encoding.PEM,
                    serialization.PrivateFormat.PKCS8, serialization.BestAvailableEncryption(self.password)))
                _atomic_bytes(self.ca_path, ca.public_bytes(serialization.Encoding.PEM))
        self._ca = x509.load_pem_x509_certificate(self.ca_path.read_bytes())
        self._key = serialization.load_pem_private_key(self.key_path.read_bytes(), password=self.password)

    def provision(self, identity, *, kind, services, server=False, days=30, server_names=()):
        principal = self.runtime.authorize("security.manage", "security:pki")
        if (not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", identity) or isinstance(days, bool) or
                not isinstance(days, int) or not 1 <= days <= 90):
            raise ValueError("identity or certificate lifetime invalid")
        if (kind not in {"sae", "kme", "service"} or not isinstance(services, (list, tuple)) or not services or
                len(services) > 32 or any(not isinstance(s, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", s) for s in services)):
            raise ValueError("certificate kind and services required")
        if (not isinstance(server, bool) or not isinstance(server_names, (tuple, list))
                or len(server_names) > 32 or (server_names and not server)):
            raise ValueError("server SANs require a server certificate")
        extra_names = []
        for name in server_names:
            if not isinstance(name, str) or not name or len(name) > 253:
                raise ValueError("invalid server SAN")
            try:
                address = ipaddress.ip_address(name)
            except ValueError:
                if not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?", name) or any(
                        not label or len(label) > 63 or label.startswith("-") or label.endswith("-")
                        for label in name.split(".")):
                    raise ValueError("invalid server SAN")
                extra_names.append(x509.DNSName(name))
            else:
                if address.is_unspecified or address.is_multicast:
                    raise ValueError("invalid server SAN")
                extra_names.append(x509.IPAddress(address))
        # Bind to an administrator-provisioned backend principal as well as SAN.
        self.runtime.principal(identity)
        key, now = ec.generate_private_key(ec.SECP256R1()), datetime.now(timezone.utc)
        names = [x509.UniformResourceIdentifier(OPERATIONAL_PREFIX + identity)]
        if server:
            names.extend([x509.DNSName("localhost"), x509.IPAddress(ipaddress.ip_address("127.0.0.1"))])
            names.extend(name for name in extra_names if name not in names)
        cert = (x509.CertificateBuilder().subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, identity)]))
            .issuer_name(self._ca.subject).public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(minutes=1)).not_valid_after(now + timedelta(days=days))
            .add_extension(x509.BasicConstraints(False, None), True)
            .add_extension(x509.KeyUsage(True, False, False, False, False, False, False, None, None), True)
            .add_extension(x509.SubjectAlternativeName(names), False)
            .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.CLIENT_AUTH, ExtendedKeyUsageOID.SERVER_AUTH]
                                               if server else [ExtendedKeyUsageOID.CLIENT_AUTH]), False)
            .sign(self._key, hashes.SHA256()))
        self.policy.register(cert, kind=kind, services=services)
        password = self.runtime.vault.get_or_create_bytes("pki-leaf-password:" + identity, 32)
        cert_path = self.root / (str(cert.serial_number) + ".pem")
        key_path = self.root / (str(cert.serial_number) + ".encrypted.key")
        _atomic_bytes(cert_path, cert.public_bytes(serialization.Encoding.PEM))
        _atomic_bytes(key_path, key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                                serialization.BestAvailableEncryption(password)))
        self.runtime.audit.record(principal, "pki.provision", "certificate:" + identity, "accepted",
                                  {"kind": kind, "services": list(services)})
        return {"identity": identity, "serial": str(cert.serial_number), "certificate": cert_path,
                "private_key": key_path, "password_ref": "pki-leaf-password:" + identity,
                "ca": self.ca_path, "policy": self.policy, "kind": kind, "services": tuple(services)}

    def renew(self, previous_serial, **configuration):
        self.runtime.authorize("security.manage", "security:pki")
        with self.policy._db() as db:
            row = db.execute("SELECT binding,mac FROM certificates WHERE serial=?", (str(previous_serial),)).fetchone()
        if row is None or not hmac.compare_digest(hmac.digest(self.policy.secret, row[0].encode(), "sha256").hex(), row[1]):
            raise CertificateRejected("renewal predecessor binding rejected")
        binding = json.loads(row[0])
        if configuration.get("identity") != binding["identity"] or configuration.get("kind") != binding["kind"]:
            raise CertificateRejected("renewal cannot change the bound identity or device kind")
        # Replacement is fully persisted first; old serial then becomes unusable.
        replacement = self.provision(**configuration)
        self.revoke(previous_serial, reason="superseded_by_renewal")
        return replacement

    def revoke(self, serial, *, reason="operator_revocation"):
        principal = self.runtime.authorize("security.manage", "security:pki")
        self.policy.revoke(serial, reason=reason)
        self.runtime.audit.record(principal, "pki.revoke", "security:pki", "accepted")

    def server_context(self, leaf):
        self._authorize_leaf(leaf, "server")
        context = enforce_tls13(ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER))
        context.load_cert_chain(str(leaf["certificate"]), str(leaf["private_key"]),
                                password=self.runtime.vault.get_bytes(leaf["password_ref"]))
        context.load_verify_locations(cafile=str(self.ca_path))
        context.verify_mode = ssl.CERT_REQUIRED
        return context

    def client_context(self, leaf):
        self._authorize_leaf(leaf, "client")
        context = enforce_tls13(ssl.create_default_context(cafile=str(self.ca_path)))
        context.load_cert_chain(str(leaf["certificate"]), str(leaf["private_key"]),
                                password=self.runtime.vault.get_bytes(leaf["password_ref"]))
        return context

    def _authorize_leaf(self, leaf, usage):
        self.runtime.authorize("credentials.use", "certificate:" + leaf["identity"])
        if leaf["password_ref"] != "pki-leaf-password:" + leaf["identity"]:
            raise CertificateRejected("private credential reference binding rejected")
        certificate = x509.load_pem_x509_certificate(Path(leaf["certificate"]).read_bytes())
        if str(certificate.serial_number) != leaf["serial"]:
            raise CertificateRejected("private credential serial binding rejected")
        self.policy.authorize(certificate.public_bytes(serialization.Encoding.DER), leaf["services"][0],
            expected_identity=leaf["identity"], expected_kind=leaf["kind"], usage=usage)

    def peer_authorizer(self, service, *, kind=None):
        def authorize(der):
            binding = self.policy.authorize(der, service, expected_kind=kind)
            return self.runtime.principal(binding["identity"])
        return authorize
