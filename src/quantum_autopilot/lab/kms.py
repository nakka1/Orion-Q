"""Local ETSI GS QKD 014 subset with real TLS, certificate identity and test keys.

This is a loopback interoperability laboratory, not a production KMS. Keys are
emulated BB84 output. The legacy shared pool remains a unit-test helper; the
default demonstration uses independent durable KMEs. Evidence omits key bytes.
"""
from __future__ import annotations

import base64
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import ipaddress
import json
from pathlib import Path
import secrets
import ssl
import tempfile
import threading
import time
from urllib.parse import unquote, urlsplit
from uuid import UUID, uuid4

from cryptography import x509
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from .qkd import run_bb84
from quantum_autopilot.security.pki import CertificatePolicy, CertificateRejected, enforce_tls13


IDENTITY_PREFIX = "urn:orion:lab:"


def _strict_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON field")
        result[key] = value
    return result


def _reject_constant(_value):
    raise ValueError("JSON constants must be finite")


class KeyDeliveryError(Exception):
    def __init__(self, status, message):
        self.status, self.message = status, message
        super().__init__(message)


class DemoKeyPool:
    """Atomic paired ownership; consumption is separate at each endpoint."""

    def __init__(self, material: bytes, *, experiment_id, model_id):
        if not isinstance(material, bytes) or len(material) % 32:
            raise ValueError("demonstration pool accepts complete 256-bit keys")
        if not experiment_id or not model_id:
            raise ValueError("experiment and model provenance required")
        self.experiment_id, self.model_id = experiment_id, model_id
        self._lock = threading.Lock()
        self._records = {str(uuid4()): {"key": material[i:i + 32], "alice": False, "bob": False}
                         for i in range(0, len(material), 32)}
        self.capacity = len(self._records)
        self.master_consumed = self.slave_consumed = 0

    def status(self):
        with self._lock:
            available = sum(not record["alice"] for record in self._records.values())
        return {"source_KME_ID": "kme-alice", "target_KME_ID": "kme-bob",
                "master_SAE_ID": "sae-alice", "slave_SAE_ID": "sae-bob", "key_size": 256,
                "stored_key_count": available, "max_key_count": self.capacity,
                "max_key_per_request": 64, "max_key_size": 256, "min_key_size": 256,
                "max_SAE_ID_count": 0}

    @staticmethod
    def _container(pairs):
        return {"keys": [{"key_ID": key_id, "key": base64.b64encode(record["key"]).decode("ascii")}
                         for key_id, record in pairs]}

    def enc(self, request):
        if not isinstance(request, dict) or set(request) - {"number", "size"}:
            raise KeyDeliveryError(400, "unsupported key request fields")
        number, size = request.get("number", 1), request.get("size", 256)
        if isinstance(number, bool) or not isinstance(number, int) or not 1 <= number <= 64:
            raise KeyDeliveryError(400, "number requires an integer from 1 to 64")
        if isinstance(size, bool) or not isinstance(size, int) or size != 256:
            raise KeyDeliveryError(400, "this laboratory supports size 256 only")
        with self._lock:
            ready = [(key_id, record) for key_id, record in self._records.items() if not record["alice"]]
            if len(ready) < number:
                raise KeyDeliveryError(503, "insufficient key buffer")
            selected = ready[:number]
            for _, record in selected:
                record["alice"] = True
            self.master_consumed += number
            return self._container(selected)

    def dec(self, request):
        if not isinstance(request, dict) or set(request) != {"key_IDs"}:
            raise KeyDeliveryError(400, "key_IDs request required")
        rows = request["key_IDs"]
        if not isinstance(rows, list) or not 1 <= len(rows) <= 64:
            raise KeyDeliveryError(400, "key_IDs requires one to 64 objects")
        ids = []
        for row in rows:
            if not isinstance(row, dict) or set(row) != {"key_ID"} or not isinstance(row["key_ID"], str):
                raise KeyDeliveryError(400, "invalid key_ID object")
            try:
                UUID(row["key_ID"])
            except ValueError as exc:
                raise KeyDeliveryError(400, "invalid UUID key_ID") from exc
            ids.append(row["key_ID"])
        if len(set(ids)) != len(ids):
            raise KeyDeliveryError(400, "duplicate key_ID")
        with self._lock:
            # Validate the whole request before any key is consumed.
            if any(key_id not in self._records or not self._records[key_id]["alice"] or
                   self._records[key_id]["bob"] for key_id in ids):
                raise KeyDeliveryError(400, "one or more keys specified are not found on KME")
            selected = [(key_id, self._records[key_id]) for key_id in ids]
            for _, record in selected:
                record["bob"] = True
            self.slave_consumed += len(selected)
            container = self._container(selected)
            # Both local copies have been consumed; remove test bytes from the pool.
            for key_id, _ in selected:
                del self._records[key_id]
            return container


def _certificate_identity(der):
    certificate = x509.load_der_x509_certificate(der)
    try:
        identities = certificate.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
        values = identities.get_values_for_type(x509.UniformResourceIdentifier)
    except x509.ExtensionNotFound:
        values = []
    matching = [value[len(IDENTITY_PREFIX):] for value in values if value.startswith(IDENTITY_PREFIX)]
    if len(matching) != 1:
        raise KeyDeliveryError(401, "certificate must identify exactly one laboratory participant")
    return matching[0]


def _authorize_certificate(pki, der, service, *, expected_identity=None, expected_kind=None, usage="client"):
    """Refresh revocation/authorization even on an already established TLS socket."""
    policy = pki.get("_policy")
    if policy is None:
        raise KeyDeliveryError(401, "provisioned certificate policy required")
    try:
        return policy.authorize(der, service, expected_identity=expected_identity,
                                expected_kind=expected_kind, usage=usage)["identity"]
    except CertificateRejected:
        raise KeyDeliveryError(401, "certificate revoked or service authorization rejected") from None


def create_test_pki(directory):
    """Ephemeral CA and qualified EKUs; the files are deleted after the run."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc)
    ca_key = ec.generate_private_key(ec.SECP256R1())
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "ORION-Q ephemeral laboratory CA")])
    ca = (x509.CertificateBuilder().subject_name(ca_name).issuer_name(ca_name).public_key(ca_key.public_key())
          .serial_number(x509.random_serial_number()).not_valid_before(now - timedelta(minutes=5))
          .not_valid_after(now + timedelta(days=1)).add_extension(x509.BasicConstraints(ca=True, path_length=0), True)
          .add_extension(x509.KeyUsage(digital_signature=True, content_commitment=False, key_encipherment=False,
                                       data_encipherment=False, key_agreement=False, key_cert_sign=True,
                                       crl_sign=True, encipher_only=None, decipher_only=None), True)
          .add_extension(x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key()), False)
          .sign(ca_key, hashes.SHA256()))
    ca_path = directory / "ca.pem"
    ca_path.write_bytes(ca.public_bytes(serialization.Encoding.PEM))
    policy = CertificatePolicy(directory / "certificate-policy.sqlite", secrets.token_bytes(32), profile="emulation")
    paths = {"ca": ca_path, "_policy": policy, "_profile": "emulation"}
    for identity in ("kme-alice", "kme-bob", "sae-alice", "sae-bob", "sae-expired"):
        key = ec.generate_private_key(ec.SECP256R1())
        server = identity.startswith("kme-")
        application_server = identity == "sae-bob"
        expired = identity.endswith("expired")
        names = [x509.UniformResourceIdentifier(IDENTITY_PREFIX + identity)]
        if server or application_server:
            names.append(x509.DNSName("localhost"))
            names.append(x509.IPAddress(ipaddress.ip_address("127.0.0.1")))
        certificate = (x509.CertificateBuilder()
                       .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, identity)]))
                       .issuer_name(ca_name).public_key(key.public_key()).serial_number(x509.random_serial_number())
                       .not_valid_before(now - timedelta(days=2) if expired else now - timedelta(minutes=5))
                       .not_valid_after(now - timedelta(days=1) if expired else now + timedelta(hours=4))
                       .add_extension(x509.BasicConstraints(ca=False, path_length=None), True)
                       .add_extension(x509.KeyUsage(digital_signature=True, content_commitment=False,
                                                    key_encipherment=False, data_encipherment=False,
                                                    key_agreement=False, key_cert_sign=False, crl_sign=False,
                                                    encipher_only=None, decipher_only=None), True)
                       .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), False)
                       .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), False)
                       .add_extension(x509.SubjectAlternativeName(names), False)
                       .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH,
                                                             ExtendedKeyUsageOID.CLIENT_AUTH] if server or application_server else
                                                            [ExtendedKeyUsageOID.CLIENT_AUTH]), False)
                       .sign(ca_key, hashes.SHA256()))
        cert_path, key_path = directory / f"{identity}.pem", directory / f"{identity}.key"
        cert_path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
        key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                              serialization.NoEncryption()))
        paths[identity] = (cert_path, key_path)
        policy.register(certificate, kind="kme" if server else "sae",
                        services=["key-delivery", "kme-peer"] if server else ["key-delivery", "application-transfer"])
    return paths


def _server_context(pki, identity):
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    enforce_tls13(context)
    context.load_cert_chain(*(str(path) for path in pki[identity]))
    context.load_verify_locations(cafile=str(pki["ca"]))
    context.verify_mode = ssl.CERT_REQUIRED
    return context


def client_context(pki, identity="sae-alice", *, ca_path=None):
    context = ssl.create_default_context(cafile=str(ca_path or pki["ca"]))
    enforce_tls13(context)
    if identity is not None:
        context.load_cert_chain(*(str(path) for path in pki[identity]))
    context._orion_pki = pki
    return context


@contextmanager
def serve_kme(pool, pki, identity):
    if identity not in ("kme-alice", "kme-bob"):
        raise ValueError("unknown KME identity")
    if pki.get("_profile") != "emulation":
        raise ValueError("test key pool cannot serve operational material")

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *_args):
            # Deliberately omit payloads, key IDs, certificates and HTTP body logs.
            pass

        def _respond(self, status, data):
            encoded = json.dumps(data, allow_nan=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=UTF-8")
            self.send_header("Content-Length", str(len(encoded)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(encoded)
            self.close_connection = True

        def _process(self):
            try:
                caller = _authorize_certificate(pki, self.connection.getpeercert(binary_form=True), "key-delivery")
                parts = urlsplit(self.path)
                route = parts.path.strip("/").split("/")
                if len(route) != 5 or route[:3] != ["api", "v1", "keys"] or parts.query:
                    raise KeyDeliveryError(404, "unknown key-delivery route")
                peer, operation = unquote(route[3]), route[4]
                expected_caller, expected_peer = (("sae-alice", "sae-bob") if identity == "kme-alice"
                                                  else ("sae-bob", "sae-alice"))
                if caller != expected_caller or peer != expected_peer:
                    raise KeyDeliveryError(401, "SAE identity or peer association rejected")
                if self.command == "GET" and operation == "status" and identity == "kme-alice":
                    result = pool.status()
                elif self.command == "POST" and operation in ("enc_keys", "dec_keys"):
                    if self.headers.get_content_type() != "application/json":
                        raise KeyDeliveryError(400, "application/json required")
                    try:
                        length = int(self.headers.get("Content-Length", "0"))
                        if not 1 <= length <= 65536:
                            raise ValueError()
                        request = json.loads(self.rfile.read(length).decode("utf-8"),
                                             object_pairs_hook=_strict_object, parse_constant=_reject_constant)
                    except (ValueError, UnicodeDecodeError) as exc:
                        raise KeyDeliveryError(400, "invalid JSON request") from exc
                    if operation == "enc_keys" and identity == "kme-alice":
                        result = pool.enc(request)
                    elif operation == "dec_keys" and identity == "kme-bob":
                        result = pool.dec(request)
                    else:
                        raise KeyDeliveryError(401, "operation not authorized for this KME")
                else:
                    raise KeyDeliveryError(405, "method or operation not supported by laboratory subset")
                self._respond(200, result)
            except KeyDeliveryError as exc:
                self._respond(exc.status, {"message": exc.message})

        do_GET = _process
        do_POST = _process

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    server.socket = _server_context(pki, identity).wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    thread.start()
    try:
        yield server.server_address[1]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def request_kme(port, context, expected_kme, method, peer, operation, body=None, *, delivery_token=None, timeout=15):
    """Verify CA, hostname and KME certificate identity before any HTTP request."""
    # Match the server's IPv4 bind; Windows localhost can try ::1 first and
    # add a failed connection timeout to every otherwise valid delivery.
    connection = http.client.HTTPSConnection("127.0.0.1", port, context=context, timeout=timeout)
    started = time.perf_counter()
    try:
        connection.connect()
        _authorize_certificate(context._orion_pki if hasattr(context, "_orion_pki") else {},
                               connection.sock.getpeercert(binary_form=True), "key-delivery",
                               expected_identity=expected_kme, expected_kind="kme", usage="server")
        tls = connection.sock.version()
        payload = json.dumps(body, allow_nan=False).encode("utf-8") if body is not None else None
        headers = {"Content-Type": "application/json"} if payload is not None else {}
        if delivery_token is not None:
            headers["X-Orion-Delivery-Token"] = delivery_token
        connection.request(method, f"/api/v1/keys/{peer}/{operation}", body=payload, headers=headers)
        response = connection.getresponse()
        return {"status": response.status, "body": json.loads(response.read()),
                "latency_ms": (time.perf_counter() - started) * 1000, "tls_version": tls}
    finally:
        connection.close()


def run_shared_pool_demo(output_directory):
    """Legacy shared-state diagnostic; not evidence of independent endpoints."""
    output_directory = Path(output_directory)
    output_directory.mkdir(parents=True, exist_ok=True)
    output_path = output_directory / "qkd_report.json"
    session = run_bb84()
    if len(session.delivery_material) < 10 * 32:
        raise RuntimeError("identified BB84 model did not produce sufficient demonstration material")
    pool = DemoKeyPool(session.delivery_material[:10 * 32], experiment_id=session.summary["experiment_id"],
                       model_id=session.summary["model_id"])
    checks, latencies = {}, []
    with tempfile.TemporaryDirectory(prefix="qkd-pki-", dir=output_directory) as temporary:
        pki = create_test_pki(temporary)
        ca, cb = client_context(pki), client_context(pki, "sae-bob")
        with serve_kme(pool, pki, "kme-alice") as port_a, serve_kme(pool, pki, "kme-bob") as port_b:
            status = request_kme(port_a, ca, "kme-alice", "GET", "sae-bob", "status")
            checks["status_schema_and_initial_buffer"] = status["status"] == 200 and status["body"]["stored_key_count"] == 10
            enc = request_kme(port_a, ca, "kme-alice", "POST", "sae-bob", "enc_keys", {"number": 1, "size": 256})
            entry = enc["body"]["keys"][0]
            identifiers = {"key_IDs": [{"key_ID": entry["key_ID"]}]}
            dec = request_kme(port_b, cb, "kme-bob", "POST", "sae-alice", "dec_keys", identifiers)
            key_a = base64.b64decode(entry["key"], validate=True)
            key_b = base64.b64decode(dec["body"]["keys"][0]["key"], validate=True)
            checks["matching_key_material_and_id"] = key_a == key_b and entry["key_ID"] == dec["body"]["keys"][0]["key_ID"]
            nonce, payload, aad = secrets.token_bytes(12), b"ORION-Q emulated laboratory payload", b"orion-q-lab-v1"
            ciphertext = AESGCM(key_a).encrypt(nonce, payload, aad)
            checks["aes_gcm_roundtrip"] = AESGCM(key_b).decrypt(nonce, ciphertext, aad) == payload
            try:
                AESGCM(key_b).decrypt(nonce, ciphertext[:-1] + bytes([ciphertext[-1] ^ 1]), aad)
                checks["aes_gcm_tamper_rejected"] = False
            except InvalidTag:
                checks["aes_gcm_tamper_rejected"] = True
            repeat = request_kme(port_b, cb, "kme-bob", "POST", "sae-alice", "dec_keys", identifiers)
            checks["consumed_id_rejected"] = repeat["status"] == 400
            wrong = request_kme(port_a, cb, "kme-alice", "POST", "sae-bob", "enc_keys", {})
            checks["valid_certificate_wrong_sae_rejected"] = wrong["status"] == 401
            try:
                request_kme(port_a, ca, "kme-bob", "GET", "sae-bob", "status")
                checks["wrong_kme_identity_rejected"] = False
            except KeyDeliveryError:
                checks["wrong_kme_identity_rejected"] = True
            for label, identity in (("expired_certificate", "sae-expired"), ("missing_certificate", None)):
                try:
                    request_kme(port_a, client_context(pki, identity), "kme-alice", "GET", "sae-bob", "status")
                    checks[label + "_rejected"] = False
                except (ssl.SSLError, OSError, http.client.HTTPException):
                    checks[label + "_rejected"] = True
            untrusted = create_test_pki(Path(temporary) / "untrusted-ca")
            for label, context in (
                    ("untrusted_client_certificate", client_context({**untrusted, "ca": pki["ca"]})),
                    ("untrusted_server_certificate", client_context(pki, ca_path=untrusted["ca"]))):
                try:
                    request_kme(port_a, context, "kme-alice", "GET", "sae-bob", "status")
                    checks[label + "_rejected"] = False
                except (ssl.SSLError, OSError, http.client.HTTPException):
                    checks[label + "_rejected"] = True
            def consume(_index):
                return request_kme(port_a, client_context(pki), "kme-alice", "POST", "sae-bob", "enc_keys", {})
            with ThreadPoolExecutor(max_workers=6) as workers:
                concurrent = list(workers.map(consume, range(12)))
            successes = [result for result in concurrent if result["status"] == 200]
            ids = [result["body"]["keys"][0]["key_ID"] for result in successes]
            checks["atomic_concurrency_no_duplicate_assignment"] = len(ids) == len(set(ids)) == 9
            checks["buffer_exhaustion"] = sum(result["status"] == 503 for result in concurrent) == 3
            end = request_kme(port_a, ca, "kme-alice", "GET", "sae-bob", "status")
            checks["available_buffer_zero"] = end["body"]["stored_key_count"] == 0
            latencies = [enc["latency_ms"], dec["latency_ms"]] + [result["latency_ms"] for result in successes]
            tls = enc["tls_version"]
        # Recreate the transport service with the same process-owned pool.
        with serve_kme(pool, pki, "kme-alice") as restarted:
            after = request_kme(restarted, ca, "kme-alice", "POST", "sae-bob", "enc_keys", {})
            checks["transport_restart_does_not_reissue_consumed_keys"] = after["status"] == 503
        empty = DemoKeyPool(b"", experiment_id=session.summary["experiment_id"], model_id=session.summary["model_id"])
        with serve_kme(empty, pki, "kme-alice") as cold:
            after = request_kme(cold, ca, "kme-alice", "POST", "sae-bob", "enc_keys", {})
            checks["cold_restart_starts_empty_fail_closed"] = after["status"] == 503
    report = {
        "schema": "orion.lab.qkd-delivery-run.v1", "run_at_utc": datetime.now(timezone.utc).isoformat(),
        "mode": "emulation", "validation_levels": ["identified_physical_model", "protocol", "https_mtls_interface"],
        "physical_acquisition": False, "qkd_session": session.summary, "checks": checks,
        "all_passed": all(checks.values()), "key_source": {"experiment_id": pool.experiment_id,
        "model_id": pool.model_id, "origin": "emulated BB84 reconciliation and Toeplitz output", "certified": False},
        "delivery": {"standard": "ETSI GS QKD 014 V1.1.1 subset", "transport": "localhost HTTPS with mutual certificates",
                     "tls_version": tls, "kmes": 2, "consumer_applications": 2, "key_size_bits": 256,
                     "initial_keys": 10, "master_consumed_keys": pool.master_consumed,
                     "slave_consumed_keys": pool.slave_consumed, "buffer_remaining_keys": pool.status()["stored_key_count"],
                     "latency_ms_min": min(latencies), "latency_ms_max": max(latencies),
                     "latency_ms_mean": sum(latencies) / len(latencies), "successful_deliveries_timed": len(latencies),
                     "initial_valid_application_requests": 2, "initial_valid_application_successes": 2,
                     "initial_valid_application_success_ratio": 1.0,
                     "concurrent_requests": 12, "concurrent_successes": len(successes),
                     "aes_gcm_payload_bytes": len(payload)},
        "limits": ["no physical QKD link or appliance validation", "public deterministic model seed makes material insecure",
                   "laboratory subset does not implement multicast or vendor extensions",
                   "memory-only pool: full process restart discards pending keys and requires a fresh session",
                   "no production persistent key store, PKI provisioning or authentication proof",
                   "no automatic retry after a delivery response is lost; outcome must be reconciled"],
        "references": ["https://www.etsi.org/deliver/etsi_gs/QKD/001_099/014/01.01.01_60/gs_qkd014v010101p.pdf",
                       "https://cryptography.io/en/latest/hazmat/primitives/aead/#cryptography.hazmat.primitives.ciphers.aead.AESGCM"],
    }
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    return report


def run_qkd_demo(output_directory, *, config=None, security=None, principal=None):
    """Independent KME/SAE processes with durable fail-closed key ownership."""
    from .independent_kme import run_independent_qkd_demo
    return run_independent_qkd_demo(output_directory, config=config, security=security, principal=principal)
