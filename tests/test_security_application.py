"""Attack-oriented checks at the common native/service/HTTP boundary."""
from contextlib import contextmanager
from http.client import HTTPConnection, HTTPSConnection
import json
from pathlib import Path
import socket
import ssl
import time
from threading import Thread

import pytest

from quantum_autopilot.qkd.dashboard_api import AssuranceDashboardService, create_server
from quantum_autopilot.security import AuthenticationError, AuthorizationError, SecurityRuntime
from quantum_autopilot.security.http_boundary import HTTPPolicy
from quantum_autopilot.security.service_boundary import service_resource


ROOT = Path(__file__).resolve().parents[1]


@contextmanager
def serving(tmp_path, **kwargs):
    server = create_server(ROOT, port=0, control_journal_path=tmp_path / "workspace.sqlite", **kwargs)
    thread = Thread(target=server.serve_forever, kwargs={"poll_interval": .02}, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)


def request(server, method="GET", path="/api/v1/health", *, token=None, headers=None, body=None):
    connection = HTTPConnection("127.0.0.1", server.server_port, timeout=15)
    options = dict(headers or {})
    if token is not None:
        options["Authorization"] = "Bearer " + token
    if isinstance(body, dict):
        body = json.dumps(body)
        options.setdefault("Content-Type", "application/json")
    try:
        connection.request(method, path, body, options)
        response = connection.getresponse()
        return response.status, json.loads(response.read())
    finally:
        connection.close()


def consumer(runtime, *, roles=("auditor",), scopes=("*",), subject="test-consumer"):
    runtime.grant_identity(subject, roles, scopes)
    return runtime.issue_service_session(subject)


def test_loopback_is_not_identity_and_source_selection_cannot_authenticate(tmp_path):
    with serving(tmp_path) as server:
        assert request(server)[0] == 401
        assert request(server, token="invented-token-" * 3)[0] == 401
        assert request(server, headers={"X-Role": "administrator", "X-User": "owner"})[0] == 401
        token = server.service.security.issue_local_session()
        assert request(server, token=token)[0] == 200
        server.service.security.revoke_session(token)
        assert request(server, token=token)[0] == 401


def test_anonymous_direct_service_is_denied(tmp_path):
    service = AssuranceDashboardService(ROOT, control_journal_path=tmp_path / "workspace.sqlite")
    with service.security.identity_scope(None), pytest.raises(AuthenticationError):
        service.health()


def test_completed_mutation_authenticates_receipt_digest_without_serializing_result(tmp_path):
    from quantum_autopilot.security.service_boundary import secure_service
    runtime = SecurityRuntime(tmp_path / "security")

    @secure_service
    class Service:
        def __init__(self):
            self.security, self.root = runtime, tmp_path

        def configure(self, request):
            return {"receipt_sha256": "a" * 64, "secret_canary": "NEVER-LOG-THIS-RESULT"}

    Service().configure({"mode": "simulated"})
    rows = [json.loads(line) for line in runtime.audit.path.read_text().splitlines()]
    completed = next(row for row in rows if row.get("result") == "completed")
    assert completed["details"]["artifact_sha256"] == "a" * 64
    assert "NEVER-LOG-THIS-RESULT" not in runtime.audit.path.read_text()
    assert runtime.audit.verify()["valid"]


def test_local_administration_writes_only_nonsecret_report(tmp_path):
    from quantum_autopilot.security.cli import main
    report = tmp_path / "report.json"
    assert main(["--root", str(tmp_path / "security"), "--report", str(report), "status"]) == 0
    value = json.loads(report.read_text())
    assert value["source"] == "os"
    assert "private_key" not in value and "token" not in value


def test_roles_and_resource_scopes_apply_before_mutation(tmp_path):
    with serving(tmp_path) as server:
        runtime = server.service.security
        token = consumer(runtime)
        assert request(server, token=token)[0] == 200
        assert request(server, "POST", "/api/v1/laboratory/configuration", token=token, body={})[0] == 403
        assert not server.service.laboratory.database.exists()
        other = consumer(runtime, scopes=("workspace:another/*",), subject="other-resource")
        assert request(server, token=other)[0] == 403
        principal = runtime.session(token)
        with runtime.identity_scope(principal), pytest.raises(AuthorizationError):
            server.service.network_command("define_pair", {})
        runtime.revoke_identity(principal.subject)
        assert request(server, token=token)[0] == 401


@pytest.mark.parametrize("headers,status", [
    ({"Host": "attacker.example:8765"}, 400),
    ({"Origin": "https://attacker.example"}, 403),
    ({"Origin": "null"}, 403),
    ({"Cookie": "session=ambient"}, 401),
    ({"Transfer-Encoding": "chunked"}, 400),
])
def test_host_origin_cookie_and_smuggling_boundaries(tmp_path, headers, status):
    with serving(tmp_path) as server:
        assert request(server, token=server.service.security.issue_local_session(), headers=headers)[0] == status


@pytest.mark.parametrize("body", ['{"experiment":"qkd","experiment":"photon"}',
                                 '{"experiment":NaN}', '[]', '{broken', '{"n":' + '[' * 30 + '0' + ']' * 30 + '}'])
def test_invalid_json_is_quarantined_without_payload(tmp_path, body):
    with serving(tmp_path) as server:
        token = server.service.security.issue_local_session()
        status, response = request(server, "POST", "/api/v1/laboratory/runs", token=token,
                                   body=body, headers={"Content-Type": "application/json"})
        assert status == 400 and response == {"error": "invalid_json"}
        records = (server.service.security.root / "quarantine" / "rejected.jsonl").read_text()
        assert body not in records and token not in records
        assert not server.service.laboratory.database.exists()


def test_content_type_and_identity_quota(tmp_path):
    with serving(tmp_path, http_policy=HTTPPolicy(requests_per_minute=3)) as server:
        token = server.service.security.issue_local_session()
        assert request(server, "POST", "/api/v1/laboratory/runs", token=token,
                       body='{"experiment":"photon"}', headers={"Content-Type": "text/plain"})[0] == 415
        assert request(server, token=token)[0] == 200
        assert request(server, token=token)[0] == 200
        assert request(server, token=token)[0] == 429


def test_administrator_can_manage_grants_but_auditor_cannot(tmp_path):
    with serving(tmp_path) as server:
        runtime = server.service.security
        reader = consumer(runtime)
        grant = {"subject": "new-auditor", "roles": ["auditor"], "scopes": ["*"], "source": "service"}
        assert request(server, "POST", "/api/v1/security/identities", token=reader, body=grant)[0] == 403
        owner = runtime.issue_local_session()
        assert request(server, "POST", "/api/v1/security/identities", token=owner, body=grant)[0] == 200
        assert request(server, "POST", "/api/v1/security/revocations", token=owner,
                       body={"subject": "new-auditor"})[0] == 200
        with pytest.raises(AuthenticationError):
            runtime.issue_service_session("new-auditor")


def test_secret_canaries_are_absent_from_audit_and_http_errors(tmp_path):
    with serving(tmp_path) as server:
        token = server.service.security.issue_local_session()
        canary = "PRIVATE_KEY_CANARY_43da9c31"
        status, response = request(server, "POST", "/api/v1/laboratory/configuration", token=token,
                                   body={"secret": canary})
        assert status == 422 and canary not in json.dumps(response)
        audit = server.service.security.audit.path.read_text()
        assert canary not in audit and token not in audit
        assert server.service.security.audit.verify()["valid"]


def test_laboratory_shares_authenticated_runtime_and_readonly_inspection(tmp_path):
    service = AssuranceDashboardService(ROOT, control_journal_path=tmp_path / "workspace.sqlite")
    assert service.laboratory.security is service.security
    service.security.grant_identity("reader", ["auditor"], ["*"])
    with service.security.identity_scope(service.security.principal("reader")):
        assert service.laboratory_state()["physical_action_performed"] is False
        with pytest.raises(AuthorizationError):
            service.run_lab_experiment({"experiment": "photon"})


def test_tls13_client_binding_and_revocation_are_checked_on_each_request(tmp_path):
    from quantum_autopilot.security.pki import OperationalPKI
    runtime = SecurityRuntime(tmp_path / "security", profile="operational")
    runtime.grant_identity("dashboard-server", ["operator"], ["*"], source="certificate")
    runtime.grant_identity("audit-client", ["auditor"], ["*"], source="certificate")
    pki = OperationalPKI(tmp_path / "certificates", runtime)
    server_leaf = pki.provision("dashboard-server", kind="service", services=["dashboard"], server=True)
    client_leaf = pki.provision("audit-client", kind="service", services=["dashboard"])
    with serving(tmp_path, security=runtime, ssl_context=pki.server_context(server_leaf),
                 peer_authorizer=pki.peer_authorizer("dashboard", kind="service")) as server:
        connection = HTTPSConnection("localhost", server.server_port, context=pki.client_context(client_leaf), timeout=15)
        server.RequestHandlerClass.protocol_version = "HTTP/1.1"
        try:
            connection.request("GET", "/api/v1/health")
            response = connection.getresponse()
            assert response.status == 200
            response.read()
            connected_socket = connection.sock
            pki.revoke(client_leaf["serial"])
            connection.request("GET", "/api/v1/health")
            assert connection.sock is connected_socket
            response = connection.getresponse()
            assert response.status == 401
            response.read()
        finally:
            connection.close()


def test_public_methods_are_covered_and_unknown_actions_fail_closed(tmp_path):
    from quantum_autopilot.security.service_boundary import secure_service
    class Extended(AssuranceDashboardService):
        def unexpected_future_mutation(self):
            return "must not execute"
    secure_service(Extended)
    service = Extended(ROOT, control_journal_path=tmp_path / "workspace.sqlite")
    service.security.grant_identity("researcher", ["researcher"], [service_resource(service) + "*"])
    with service.security.identity_scope(service.security.principal("researcher")), pytest.raises(AuthorizationError):
        service.unexpected_future_mutation()


def test_partial_body_times_out_without_starting_experiment(tmp_path):
    with serving(tmp_path, http_policy=HTTPPolicy(body_timeout_seconds=.15)) as server:
        token = server.service.security.issue_local_session()
        with socket.create_connection(("127.0.0.1", server.server_port), timeout=5) as connection:
            text = (f"POST /api/v1/laboratory/runs HTTP/1.0\r\nHost: 127.0.0.1:{server.server_port}\r\n"
                    f"Authorization: Bearer {token}\r\nContent-Type: application/json\r\nContent-Length: 100\r\n\r\n{{")
            connection.sendall(text.encode())
            assert b"408" in connection.recv(4096)
        assert not server.service.laboratory.database.exists()


def test_invalid_administration_types_are_rejected_not_executed(tmp_path):
    with serving(tmp_path) as server:
        token = server.service.security.issue_local_session()
        for value in (None, [None], [{"role": "administrator"}], "administrator"):
            payload = {"subject": "invalid-user", "roles": value, "scopes": ["*"], "source": "service"}
            assert request(server, "POST", "/api/v1/security/identities", token=token, body=payload)[0] == 422
        with pytest.raises(AuthenticationError):
            server.service.security.principal("invalid-user")


def test_service_session_issuance_is_explicit_scoped_and_returns_only_vault_reference(tmp_path):
    with serving(tmp_path) as server:
        runtime = server.service.security
        owner = runtime.issue_local_session()
        auditor = consumer(runtime)
        body = {"subject": "test-consumer", "vault_reference": "api-session:ogs-reader"}
        assert request(server, "POST", "/api/v1/security/sessions", token=auditor, body=body)[0] == 403
        status, response = request(server, "POST", "/api/v1/security/sessions", token=owner, body=body)
        assert status == 200 and response == {**body, "created": True}
        issued = runtime.vault.get_bytes(body["vault_reference"]).decode()
        assert issued not in json.dumps(response)
        assert runtime.session(issued).subject == "test-consumer"
        body["vault_reference"] = "identity-policy-v1"
        assert request(server, "POST", "/api/v1/security/sessions", token=owner, body=body)[0] == 422


def test_expired_operation_deadline_reports_receipt_inspection(tmp_path):
    with serving(tmp_path, http_policy=HTTPPolicy(operation_timeout_seconds=.1)) as server:
        token = server.service.security.issue_local_session()
        def slow_completed_read():
            time.sleep(.2)
            return {"completed": True}
        server.service.health = slow_completed_read
        status, response = request(server, token=token)
        assert status == 504 and response["outcome"] == "inspect_durable_receipts"


def test_stalled_tls_handshake_does_not_block_other_authenticated_clients(tmp_path):
    from quantum_autopilot.security.pki import OperationalPKI
    runtime = SecurityRuntime(tmp_path / "security", profile="operational")
    runtime.grant_identity("server", ["operator"], ["*"], source="certificate")
    runtime.grant_identity("reader", ["auditor"], ["*"], source="certificate")
    pki = OperationalPKI(tmp_path / "certificates", runtime)
    server_leaf = pki.provision("server", kind="service", services=["dashboard"], server=True)
    reader = pki.provision("reader", kind="service", services=["dashboard"])
    with serving(tmp_path, security=runtime, ssl_context=pki.server_context(server_leaf),
                 peer_authorizer=pki.peer_authorizer("dashboard"),
                 http_policy=HTTPPolicy(body_timeout_seconds=2, max_concurrency=2)) as server:
        client_tls = pki.client_context(reader)
        with socket.create_connection(("127.0.0.1", server.server_port)) as stalled:
            connection = HTTPSConnection("127.0.0.1", server.server_port, context=client_tls, timeout=5)
            try:
                connection.connect()
                # The second handshake completes while the first socket is
                # still awaiting its ClientHello, independent of audit I/O time.
                stalled.settimeout(.02)
                with pytest.raises(socket.timeout):
                    stalled.recv(1)
                connection.request("GET", "/api/v1/security")
                response = connection.getresponse()
                assert response.status == 200
                response.read()
            finally:
                connection.close()
            stalled.shutdown(socket.SHUT_RDWR)
