"""Authenticated HTTP boundary; loopback is a transport choice, never identity.

No cookies or URL credentials are accepted. Bearer tokens and provisioned client
certificates are checked per request. Deadlines bound admission and nested service
calls; an already committed operation is never reported as rolled back on timeout.
"""
from __future__ import annotations

from collections import OrderedDict, deque
from dataclasses import dataclass
import hashlib
from http.server import ThreadingHTTPServer
import io
import json
import math
import re
from pathlib import Path
import ssl
import threading
import time
from urllib.parse import urlsplit

from .identity import AuthenticationError, AuthorizationError
from .audit import AuditIntegrityError
from .vault import VaultError
from .service_boundary import request_deadline


@dataclass(frozen=True)
class HTTPPolicy:
    allowed_hosts: tuple[str, ...] = ("127.0.0.1", "localhost")
    allowed_origins: tuple[str, ...] = tuple(f"http://{h}:{p}" for h in ("localhost", "127.0.0.1")
                                           for p in (3000, 3001, 3002))
    requests_per_minute: int = 120
    max_concurrency: int = 8
    body_timeout_seconds: float = 5
    operation_timeout_seconds: float = 30

    def __post_init__(self):
        if not self.allowed_hosts or not all(isinstance(h, str) and h and "/" not in h for h in self.allowed_hosts):
            raise ValueError("explicit HTTP host allowlist required")
        if not 1 <= self.requests_per_minute <= 10000 or not 1 <= self.max_concurrency <= 64:
            raise ValueError("invalid HTTP quota/concurrency")
        if not 0 < self.body_timeout_seconds <= 30 or not 0 < self.operation_timeout_seconds <= 300:
            raise ValueError("invalid HTTP deadline")


class Quotas:
    def __init__(self, limit):
        self.limit, self.lock, self.rows = limit, threading.Lock(), OrderedDict()

    def admit(self, identity):
        now = time.monotonic()
        with self.lock:
            row = self.rows.setdefault(identity, deque())
            self.rows.move_to_end(identity)
            while row and row[0] <= now - 60:
                row.popleft()
            admitted = len(row) < self.limit
            if admitted:
                row.append(now)
            # Memory is bounded even under unauthenticated address churn.
            while len(self.rows) > 4096:
                self.rows.popitem(last=False)
            return admitted


def strict_object(data):
    def unique(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise ValueError("duplicate JSON field")
            value[key] = item
        return value
    def nonfinite(_):
        raise ValueError("nonfinite JSON number")
    value = json.loads(data.decode("utf-8"), object_pairs_hook=unique, parse_constant=nonfinite)
    if not isinstance(value, dict):
        raise ValueError("JSON object required")
    def depth(item, level=0):
        if level > 24:
            raise ValueError("JSON nesting limit exceeded")
        if isinstance(item, float) and not math.isfinite(item):
            raise ValueError("nonfinite JSON number")
        if isinstance(item, dict):
            for child in item.values():
                depth(child, level + 1)
        elif isinstance(item, list):
            for child in item:
                depth(child, level + 1)
    depth(value)
    return value


class BoundedHTTPServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, handler, *, policy):
        self._slots = threading.BoundedSemaphore(policy.max_concurrency)
        self._transport_policy = policy
        self.ssl_context = None
        super().__init__(address, handler)

    def get_request(self):
        request, address = super().get_request()
        request.settimeout(self._transport_policy.body_timeout_seconds)
        return request, address

    def process_request(self, request, client_address):
        if not self._slots.acquire(blocking=False):
            try:
                if self.ssl_context is None:
                    request.sendall(b"HTTP/1.0 503 Service Unavailable\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
            finally:
                self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self._slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            if self.ssl_context is not None:
                try:
                    request = self.ssl_context.wrap_socket(request, server_side=True, do_handshake_on_connect=False)
                    request.do_handshake()
                except (ssl.SSLError, OSError):
                    self.shutdown_request(request)
                    return
            super().process_request_thread(request, client_address)
        finally:
            self._slots.release()


def secure_handler(base, service, *, policy=None, peer_authorizer=None, max_request_bytes=262144):
    policy = policy or HTTPPolicy()
    quotas = Quotas(policy.requests_per_minute)
    peers = Quotas(policy.requests_per_minute * 2)
    quarantine_lock = threading.Lock()

    class AuthenticatedHandler(base):
        def _send_json(self, status, payload):
            deadline = request_deadline.get()
            if deadline is not None and status < 300 and time.monotonic() >= deadline:
                status, payload = 504, {"error": "operation_deadline_exceeded", "outcome": "inspect_durable_receipts"}
            if status == 422 and not getattr(self, "_quarantined", False):
                self._quarantine("configuration_invalid", getattr(self, "_request_body", b""))
            return super()._send_json(status, payload)

        def setup(self):
            self.request.settimeout(policy.body_timeout_seconds)
            super().setup()

        def _error(self, status, code):
            self.close_connection = True
            self._send_json(status, {"error": code})

        def _quarantine(self, code, data=b""):
            # Deliberately retain no raw body, URL query, headers, token or key.
            row = {"schema": "orion.security.quarantine.v1", "reason": code,
                   "record_sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data), "at": time.time()}
            self._quarantined = True
            folder = service.security.root / "quarantine"
            with quarantine_lock:
                folder.mkdir(exist_ok=True)
                path = folder / "rejected.jsonl"
                # Bounded diagnostic retention; authenticated audit retains the action.
                if path.exists() and path.stat().st_size > 1048576:
                    path.replace(folder / "previous.jsonl")
                with path.open("a", encoding="utf-8") as stream:
                    stream.write(json.dumps(row, sort_keys=True) + "\n")
            service.security.audit.record("anonymous", "ingestion.reject", "api:ingestion", "rejected",
                {"reason": "configuration_invalid", "artifact_sha256": row["record_sha256"]})

        def _origin(self):
            origin = self.headers.get("Origin")
            if origin is None:
                return None
            scheme = "https" if isinstance(self.connection, ssl.SSLSocket) else "http"
            port = self.server.server_address[1]
            same = {f"{scheme}://{host}:{port}" for host in policy.allowed_hosts}
            return origin if origin in set(policy.allowed_origins) | same else False

        def _headers(self):
            result = super()._headers()
            result.pop("Access-Control-Allow-Origin", None)
            origin = self._origin()
            if origin:
                result.update({"Access-Control-Allow-Origin": origin, "Vary": "Origin"})
            result["Referrer-Policy"] = "no-referrer"
            return result

        def _validate_transport(self):
            for name in ("Host", "Origin", "Authorization", "Content-Length", "Content-Type"):
                if len(self.headers.get_all(name, [])) > 1:
                    self._error(400, "duplicate_header")
                    return False
            host = self.headers.get("Host", "")
            try:
                parsed = urlsplit("//" + host)
                valid = (parsed.hostname in policy.allowed_hosts and parsed.port == self.server.server_address[1]
                         and not parsed.username and not parsed.password and not parsed.path and not parsed.query
                         and not parsed.fragment)
            except ValueError:
                valid = False
            if not valid:
                self._error(400, "host_not_allowed")
                return False
            if self._origin() is False:
                self._error(403, "origin_not_allowed")
                return False
            if self.headers.get("Transfer-Encoding") is not None:
                self._error(400, "transfer_encoding_not_supported")
                return False
            if len(self.path) > 2048 or "\x00" in self.path:
                self._error(400, "invalid_path")
                return False
            if not peers.admit(self.client_address[0]):
                self._error(429, "peer_quota_exceeded")
                return False
            return True

        def _principal(self):
            runtime = service.security
            if self.headers.get("Cookie") is not None:
                raise AuthenticationError("cookie authentication is not supported")
            peer = None
            if isinstance(self.connection, ssl.SSLSocket):
                certificate = self.connection.getpeercert(binary_form=True)
                if certificate is not None and peer_authorizer is not None:
                    try:
                        peer = peer_authorizer(certificate)
                    except PermissionError as error:
                        raise AuthenticationError("certificate is not authorized") from error
            authorization = self.headers.get("Authorization", "")
            if authorization:
                if not authorization.startswith("Bearer ") or len(authorization) > 16400:
                    raise AuthenticationError("invalid bearer authentication")
                token = authorization[7:]
                principal = runtime.authenticate_oidc(token) if token.count(".") == 2 else runtime.session(token)
                if peer is not None and principal.subject != peer.subject:
                    raise AuthenticationError("certificate and bearer identities disagree")
                return principal
            if peer is not None:
                return peer
            raise AuthenticationError("authenticated identity required")

        def _dispatch(self, method):
            self._quarantined = False
            self._request_body = b""
            if not self._validate_transport():
                return
            api = urlsplit(self.path).path.startswith("/api/")
            if not api and method == "GET":
                return super().do_GET()  # public application assets, no operational data
            runtime = service.security
            try:
                # Anonymous scope prevents fallback to the server's OS account.
                with runtime.identity_scope(None):
                    principal = self._principal()
                if not quotas.admit(principal.subject):
                    self._error(429, "identity_quota_exceeded")
                    return
                with runtime.identity_scope(principal):
                    deadline = request_deadline.set(time.monotonic() + policy.operation_timeout_seconds)
                    try:
                        payload = None
                        if method == "POST":
                            if self.headers.get_content_type() != "application/json":
                                self._quarantine("content_type_not_supported")
                                self._error(415, "content_type_not_supported")
                                return
                            try:
                                size = int(self.headers.get("Content-Length", "0"))
                            except ValueError:
                                self._error(400, "invalid_content_length")
                                return
                            if not 0 < size <= max_request_bytes:
                                self._error(413, "request_size_out_of_bounds")
                                return
                            data = self.rfile.read(size)
                            self._request_body = data
                            if len(data) != size:
                                self._error(400, "incomplete_body")
                                return
                            try:
                                payload = strict_object(data)
                            except (ValueError, RecursionError, UnicodeDecodeError):
                                self._quarantine("invalid_json", data)
                                self._error(400, "invalid_json")
                                return
                            self.rfile = io.BytesIO(data)
                        if self.path == "/api/v1/security" and method == "GET":
                            self._send_json(200, service.security_status())
                        elif self.path == "/api/v1/security/identities" and method == "POST":
                            if set(payload) != {"subject", "roles", "scopes", "source"}:
                                raise ValueError("identity configuration fields invalid")
                            if (not isinstance(payload["subject"], str) or not isinstance(payload["source"], str)
                                    or not isinstance(payload["roles"], list) or not 1 <= len(payload["roles"]) <= 5
                                    or not isinstance(payload["scopes"], list) or not 1 <= len(payload["scopes"]) <= 64
                                    or not all(isinstance(item, str) for item in payload["roles"] + payload["scopes"])):
                                raise ValueError("identity configuration types invalid")
                            granted = runtime.grant_identity(**payload)
                            self._send_json(200, {"subject": granted.subject, "roles": sorted(granted.roles)})
                        elif self.path == "/api/v1/security/revocations" and method == "POST":
                            if set(payload) != {"subject"}:
                                raise ValueError("identity revocation fields invalid")
                            runtime.revoke_identity(payload["subject"])
                            self._send_json(200, {"revoked": True})
                        elif self.path == "/api/v1/security/sessions" and method == "POST":
                            if set(payload) != {"subject", "vault_reference"}:
                                raise ValueError("session issuance fields invalid")
                            if not all(isinstance(value, str) for value in payload.values()):
                                raise ValueError("session issuance types invalid")
                            subject, reference = payload["subject"], payload["vault_reference"]
                            if not re.fullmatch(r"api-session:[A-Za-z0-9_.:-]{1,128}", reference):
                                raise ValueError("session reference requires the reserved api-session namespace")
                            runtime.authorize("security.manage", "security:sessions")
                            token = runtime.issue_service_session(subject)
                            runtime.vault.put_bytes(reference, token.encode())
                            # API returns a protected reference, never the bearer token.
                            self._send_json(200, {"subject": subject, "vault_reference": reference, "created": True})
                        else:
                            getattr(super(), "do_" + method)()
                    finally:
                        request_deadline.reset(deadline)
            except AuthenticationError:
                runtime.audit.record("anonymous", "api.authenticate", "api:access", "denied")
                self._error(401, "authentication_required")
            except AuthorizationError:
                self._error(403, "permission_denied")
            except (TimeoutError, OSError):
                self._error(408, "request_timeout_inspect_receipts")
            except ValueError:
                self._quarantine("configuration_invalid")
                self._error(422, "configuration_invalid")

        def do_GET(self):
            self._safe_dispatch("GET")

        def do_POST(self):
            self._safe_dispatch("POST")

        def _safe_dispatch(self, method):
            try:
                self._dispatch(method)
            except (AuditIntegrityError, VaultError):
                self._error(503, "security_state_unavailable")
            except OSError:
                self.close_connection = True  # interrupted transport cannot prove receipt
            except Exception:
                self._error(500, "internal_error")

        def do_OPTIONS(self):
            if not self._validate_transport():
                return
            origin = self._origin()
            if not origin:
                self._error(403, "origin_not_allowed")
                return
            self.send_response(204)
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type")
            self.send_header("Access-Control-Max-Age", "600")
            self.send_header("Vary", "Origin")
            self.end_headers()

    return AuthenticatedHandler
