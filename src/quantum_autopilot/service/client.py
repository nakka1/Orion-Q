"""Transport client for independent desktop, Linux and equipment services."""
from __future__ import annotations

import http.client
import json
import ssl
import re
from urllib.parse import urlsplit

from quantum_autopilot.security.http_boundary import strict_object


class CoreRequestError(RuntimeError):
    def __init__(self, status: int | None, *, uncertain: bool = False, committed=False,
                 request_id=None, receipt_sha256=None):
        super().__init__("durable result committed; audit pending; retry the same request id" if committed else
                         "core operation outcome uncertain; inspect durable receipts" if uncertain
                         else "core request rejected or unavailable")
        self.status, self.uncertain = status, uncertain
        self.committed, self.request_id, self.receipt_sha256 = committed, request_id, receipt_sha256


class CoreClient:
    """Explicit TLS identity; no OS session, automatic retry or local fallback.

    A caller supplies its own provisioned TLS context. This client never opens
    a journal, executes a protocol locally or substitutes a simulated result.
    """

    def __init__(self, url: str, context: ssl.SSLContext, *, timeout_s=30.0, max_response_bytes=32 * 1024 * 1024):
        endpoint = urlsplit(url)
        if (endpoint.scheme != "https" or not endpoint.hostname or endpoint.username or endpoint.password
                or endpoint.query or endpoint.fragment or endpoint.path not in {"", "/"}):
            raise ValueError("core endpoint must be an explicit HTTPS origin")
        if (not isinstance(context, ssl.SSLContext) or not context.check_hostname
                or context.verify_mode != ssl.CERT_REQUIRED
                or context.minimum_version != ssl.TLSVersion.TLSv1_3
                or context.maximum_version != ssl.TLSVersion.TLSv1_3):
            raise ValueError("core client requires verified TLS 1.3")
        if isinstance(timeout_s, bool) or not isinstance(timeout_s, (int, float)) or not 0 < timeout_s <= 300:
            raise ValueError("invalid core request timeout")
        if type(max_response_bytes) is not int or not 1024 <= max_response_bytes <= 64 * 1024 * 1024:
            raise ValueError("invalid core response limit")
        self.host, self.port, self.context = endpoint.hostname, endpoint.port or 443, context
        self.timeout_s, self.max_response_bytes = timeout_s, max_response_bytes

    def _request(self, method: str, path: str, payload: dict | None = None):
        connection = http.client.HTTPSConnection(self.host, self.port, context=self.context, timeout=self.timeout_s)
        body = None if payload is None else json.dumps(payload, allow_nan=False, separators=(",", ":")).encode()
        host = "[" + self.host + "]" if ":" in self.host else self.host
        headers = {"Accept": "application/json", "Host": f"{host}:{self.port}"}
        if body is not None:
            headers["Content-Type"] = "application/json"
        try:
            connection.request(method, path, body=body, headers=headers)
            response = connection.getresponse()
            encoded = response.read(self.max_response_bytes + 1)
            if len(encoded) > self.max_response_bytes:
                raise CoreRequestError(response.status, uncertain=method != "GET")
            if response.status != 200:
                if response.status == 503 and method == "POST":
                    try:
                        recovery = strict_object(encoded)
                    except ValueError:
                        recovery = {}
                    if (recovery.get("error") == "durable_result_audit_pending" and
                            recovery.get("committed") is True and
                            isinstance(recovery.get("request_id"), str) and
                            re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", recovery["request_id"]) and
                            isinstance(recovery.get("receipt_sha256"), str) and
                            re.fullmatch(r"[a-f0-9]{64}", recovery["receipt_sha256"])):
                        raise CoreRequestError(503, uncertain=True, committed=True,
                                               request_id=recovery["request_id"],
                                               receipt_sha256=recovery["receipt_sha256"])
                # Server body can contain sensitive application data; never log it.
                raise CoreRequestError(response.status, uncertain=response.status >= 500 and method != "GET")
            return strict_object(encoded)
        except CoreRequestError:
            raise
        except (OSError, http.client.HTTPException, ValueError):
            raise CoreRequestError(None, uncertain=method != "GET") from None
        finally:
            connection.close()

    def health(self):
        return self._request("GET", "/api/v1/health")

    def console_snapshot(self, *, include_static=True):
        if type(include_static) is not bool:
            raise ValueError("include_static must be a boolean")
        return self._request("GET", "/api/v1/console/snapshot?include_static=" + str(include_static).lower())

    def console_command(self, verb: str, payload: dict):
        if not isinstance(verb, str) or not isinstance(payload, dict):
            raise ValueError("typed console command required")
        return self._request("POST", "/api/v1/console/commands", {"verb": verb, "payload": payload})

    def console_evidence(self, scope: str):
        return self._request("POST", "/api/v1/console/evidence", {"scope": scope})

    def topology(self):
        return self._request("GET", "/api/v1/control-plane/topology")

    def network_state(self, *, metadata_only=False):
        if type(metadata_only) is not bool:
            raise ValueError("metadata_only must be a boolean")
        return self._request("GET", "/api/v1/network/state?metadata_only=" + str(metadata_only).lower())

    def network_step(self, *, metadata_only=False):
        if type(metadata_only) is not bool:
            raise ValueError("metadata_only must be a boolean")
        return self._request("POST", "/api/v1/network/step", {"metadata_only": metadata_only})

    def network_command(self, verb: str, payload: dict):
        if not isinstance(verb, str) or not isinstance(payload, dict):
            raise ValueError("typed network command required")
        return self._request("POST", "/api/v1/network/commands", {"verb": verb, "payload": payload})

    def control_cycle(self, request: dict):
        return self._request("POST", "/api/v1/control-plane/cycles", request)

    def laboratory(self):
        return self._request("GET", "/api/v1/laboratory")

    def configure_laboratory(self, request: dict):
        return self._request("POST", "/api/v1/laboratory/configuration", request)

    def mixed_status(self):
        return self._request("GET", "/api/v1/mixed-controller/status")

    def mixed_configure(self, request: dict):
        return self._request("POST", "/api/v1/mixed-controller/configure", request)

    def mixed_ingest(self, request: dict):
        return self._request("POST", "/api/v1/mixed-controller/observations", request)

    def mixed_demand(self, request: dict):
        return self._request("POST", "/api/v1/mixed-controller/demands", request)

    def mixed_release(self, request: dict):
        return self._request("POST", "/api/v1/mixed-controller/release", request)
