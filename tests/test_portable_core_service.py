"""Independent service identity, encrypted state and real TLS, with Windows disabled."""
from contextlib import contextmanager
from http.client import HTTPSConnection
import json
import os
from pathlib import Path
import ssl
from threading import Thread

import pytest

from quantum_autopilot.security import AuthenticationError, SecurityRuntime
from quantum_autopilot.security.http_boundary import HTTPPolicy
from quantum_autopilot.security.pki import OperationalPKI
from quantum_autopilot.security.secret_providers import AESGCMSecretProvider
from quantum_autopilot.security.service_identity import ServiceIdentityBinding, ServiceIdentityProvision
from quantum_autopilot.service.client import CoreClient, CoreRequestError
from quantum_autopilot.service.config import CoreConfig, load_config
from quantum_autopilot.service.runtime import build_runtime, create_core_server
from quantum_autopilot.service.tls import leaf_descriptor


ROOT = Path(__file__).resolve().parents[1]


class FixtureKeySource:
    """Explicit in-memory test secret service; no production default can select it."""
    def __init__(self):
        self.key = os.urandom(32)

    def read_key(self, key_id, version):
        assert (key_id, version) == ("fixture", 1)
        return self.key


def settings(tmp_path):
    return {"schema": "orion.core-service.v1", "repository_root": str(ROOT),
            "state_root": str(tmp_path / "state"),
            "identity": {"subject": "orion-core", "credential_file": str(tmp_path / "service.secret")},
            "provisioning_identity": {"subject": "orion-admin", "credential_file": str(tmp_path / "admin.secret")},
            "vault": {"key_id": "master-v1", "key_file": str(tmp_path / "master.secret")},
            "http": {"host": "127.0.0.1", "port": 0, "allowed_hosts": ["localhost", "127.0.0.1"],
                     "allowed_origins": []},
            "tls": {"root": str(tmp_path / "certificates"), "server_leaf": str(tmp_path / "server-leaf.json")}}


@pytest.fixture
def provisioned(tmp_path, monkeypatch):
    import quantum_autopilot.security.runtime as security_runtime
    import quantum_autopilot.security.vault as vault
    def forbidden(*_args, **_kwargs):
        raise AssertionError("core attempted to use interactive Windows identity/DPAPI")
    monkeypatch.setattr(security_runtime, "operating_system_identity", forbidden)
    monkeypatch.setattr(vault, "_dpapi", forbidden)
    config = CoreConfig.parse(settings(tmp_path))
    key_source = FixtureKeySource()
    provider = AESGCMSecretProvider(key_source, active_key_id="fixture", active_version=1)
    admin_secret, service_secret = os.urandom(48), os.urandom(48)
    runtime = SecurityRuntime(config.security_root, profile="operational", identity_profile="service",
        secret_provider=provider, bootstrap=True,
        service_bootstrap=ServiceIdentityProvision("orion-admin", ("administrator",), ("*",), admin_secret))
    administrator = runtime.authenticate_service("orion-admin", admin_secret)
    with runtime.identity_scope(administrator):
        runtime.grant_service_identity("orion-core", ("operator",), ("*",), service_secret)
        runtime.grant_identity("audit-client", ("auditor",), ("*",), source="certificate")
        pki = OperationalPKI(config.tls["root"], runtime)
        server_leaf = pki.provision("orion-core", kind="service", services=["dashboard"], server=True,
                                    server_names=["orion-core.example.test"])
        client_leaf = pki.provision("audit-client", kind="service", services=["dashboard"])
        operator_leaf = pki.provision("orion-core", kind="service", services=["dashboard"])
        client_context, operator_context = pki.client_context(client_leaf), pki.client_context(operator_leaf)
    Path(config.tls["server_leaf"]).write_text(json.dumps(leaf_descriptor(server_leaf)), encoding="utf-8")
    service = build_runtime(config, secret_provider=provider,
                           identity_binding=ServiceIdentityBinding("orion-core", lambda: service_secret))
    return config, service, runtime, administrator, pki, client_leaf, client_context, operator_context


@contextmanager
def serving(config, runtime):
    server = create_core_server(config, security=runtime)
    thread = Thread(target=server.serve_forever, kwargs={"poll_interval": .02}, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)
        assert not thread.is_alive()


def test_headless_core_tls_does_not_open_issuing_authority_private_key(provisioned, monkeypatch):
    config, runtime, *_ = provisioned
    original = Path.read_bytes
    def read(path):
        if path.name == "ca.encrypted.key":
            raise AssertionError("running core must not read the CA private key")
        return original(path)
    monkeypatch.setattr(Path, "read_bytes", read)
    with serving(config, runtime) as server:
        assert server.service.security is runtime
        assert runtime.snapshot()["identity_profile"] == "service"


def test_remote_routing_pipeline_uses_authenticated_backend(provisioned):
    config, runtime, _, _, _, _, audit_context, operator_context = provisioned
    with serving(config, runtime) as server:
        url = f"https://localhost:{server.server_port}"
        reader, operator = CoreClient(url, audit_context), CoreClient(url, operator_context)
        assert reader.health()["physical_read_only"] is True
        before = reader.network_state()
        assert before["source"]["kind"] == "none"
        with pytest.raises(CoreRequestError) as denied:
            reader.network_step()
        assert denied.value.status == 403
        assert reader.network_state()["tick"] == before["tick"]
        result = operator.network_command("configure_network_input", {"kind": "simulated", "seed": 17, "scenario": "quiet"})
        assert result["source"]["kind"] == "simulated"
        after = operator.network_step()
        assert after["tick"] == before["tick"] + 1
        assert after["source"]["kind"] == "simulated"
        assert reader.network_state() == after
        assert any(link["qber"] is not None for link in after["links"])
        assert runtime.audit.verify()["valid"]


def test_remote_console_reads_own_certificate_identity_and_does_not_step(provisioned):
    config, runtime, _, _, _, _, audit_context, operator_context = provisioned
    with serving(config, runtime) as server:
        url = f"https://localhost:{server.server_port}"
        reader, operator = CoreClient(url, audit_context), CoreClient(url, operator_context)
        frame = reader.console_snapshot()
        assert frame["schema"] == "orion.console.remote.v1"
        assert frame["security"]["subject"] == "audit-client"
        assert frame["security"]["source"] == "certificate"
        assert frame["live"]["topology_version"] == frame["topology"]["topology"]["topology_version"]
        tick = frame["live"]["tick"]
        assert reader.console_snapshot(include_static=False)["live"]["tick"] == tick
        assert "summary" not in reader.console_snapshot(include_static=False)
        with pytest.raises(CoreRequestError) as denied:
            reader.console_command("run_scenario", {"scenario_id": "anonymous-escalation"})
        assert denied.value.status == 403
        assert reader.network_state()["tick"] == tick
        with pytest.raises(CoreRequestError):
            operator.console_command("_release_controller_io_resources", {})
        artifact = operator.console_evidence("network")
        assert artifact["physical_action_performed"] is False
        import hashlib
        raw = json.dumps(artifact["evidence"], sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
        assert artifact["record_sha256"] == hashlib.sha256(raw).hexdigest()


def test_decorative_node_fields_cannot_authorize_global_configuration(provisioned):
    config, runtime, admin_runtime, administrator, pki, _, _, operator_context = provisioned
    scope = "workspace:" + config.state_root.as_posix()
    with admin_runtime.identity_scope(administrator):
        admin_runtime.grant_identity("node-operator", ["operator"], [scope + "/nodes/alice"], source="certificate")
        leaf = pki.provision("node-operator", kind="service", services=["dashboard"])
        context = pki.client_context(leaf)
    with serving(config, runtime) as server:
        url = f"https://localhost:{server.server_port}"
        client, operator = CoreClient(url, context), CoreClient(url, operator_context)
        before = operator.network_state()
        for verb, payload in [
                ("configure_network_input", {"kind": "simulated", "node_id": "alice"}),
                ("configure_technology", {"session_id": "invalid", "enabled": False, "node_id": "alice"}),
                ("configure_technology_source", {"kind": "none", "node_id": "alice"}),
                ("set_ml_control", {"enabled": False, "node_id": "alice"})]:
            with pytest.raises(CoreRequestError) as denied:
                client.network_command(verb, payload)
            assert denied.value.status == 403, "global operations must be denied before parameter processing"
        after = operator.network_state()
        assert after["tick"] == before["tick"] and after["source"] == before["source"]
        assert after["publication_revision"] == before["publication_revision"]


def test_remote_export_cannot_overwrite_server_files(provisioned, tmp_path):
    config, runtime, *_rest, operator_context = provisioned
    marker = tmp_path / "must-not-be-overwritten.json"
    marker.write_text("marker-before-request", encoding="utf-8")
    with serving(config, runtime) as server:
        operator = CoreClient(f"https://localhost:{server.server_port}", operator_context)
        with pytest.raises(CoreRequestError) as rejected:
            operator.network_command("export_technology_events", {"path": str(marker)})
        assert rejected.value.status == 422
        assert marker.read_text(encoding="utf-8") == "marker-before-request"
        assert not marker.with_suffix(".satellite-acquisitions.jsonl").exists()
        with pytest.raises(ValueError):
            server.service.network_command("export_technology_events", {"path": str(marker)})


def test_remote_inventory_has_durable_confirmation_and_unknown_readback(provisioned, monkeypatch):
    config, runtime, *_rest, operator_context = provisioned
    with serving(config, runtime) as server:
        operator = CoreClient(f"https://localhost:{server.server_port}", operator_context)
        topology = operator.console_snapshot()["topology"]["topology"]
        request = {"topology_id": topology["topology_id"],
                   "expected_version": topology["topology_version"], "node_id": topology["nodes"][0]["node_id"],
                   "isolated": True}
        result = operator.console_command("set_node_isolation", request)
        command_id = result["command"]["record"]["command_id"]
        assert result["command"]["record"]["events"][-1]["state"] == "CONFIRMED"
        assert any(row["record"]["command_id"] == command_id for row in operator.console_snapshot()["commands"])
        # A failed readback is explicit UNKNOWN; the client must not retry it.
        request = {**request, "isolated": False,
                   "expected_version": result["topology"]["topology_version"]}
        method = server.service.set_node_isolation
        monkeypatch.setattr(server.service, "set_node_isolation", lambda payload: dict(method(payload), status="unconfirmed"))
        with pytest.raises(CoreRequestError) as uncertain:
            operator.console_command("set_node_isolation", request)
        assert uncertain.value.status == 500 and uncertain.value.uncertain
        rows = operator.console_snapshot()["commands"]
        failed = next(row for row in rows if row["record"]["request"] == request)
        assert failed["record"]["events"][-1]["state"] == "UNKNOWN"


def test_remote_certificate_revocation_applies_to_existing_connection(provisioned):
    config, runtime, admin_runtime, administrator, pki, leaf, context, unaffected = provisioned
    with serving(config, runtime) as server:
        server.RequestHandlerClass.protocol_version = "HTTP/1.1"
        connection = HTTPSConnection("localhost", server.server_port, context=context, timeout=10)
        try:
            connection.request("GET", "/api/v1/network/state")
            response = connection.getresponse()
            assert response.status == 200
            response.read()
            first_socket = connection.sock
            with admin_runtime.identity_scope(administrator):
                pki.revoke(leaf["serial"])
            connection.request("GET", "/api/v1/network/state")
            assert connection.sock is first_socket
            response = connection.getresponse()
            assert response.status == 401
            response.read()
            assert CoreClient(f"https://localhost:{server.server_port}", unaffected).health()["status"] == "ok"
        finally:
            connection.close()


def test_remote_anonymous_tls_and_invented_headers_do_not_grant_authority(provisioned):
    config, runtime, _, _, pki, _, context, _ = provisioned
    anonymous = ssl.create_default_context(cafile=str(pki.ca_path))
    anonymous.minimum_version = anonymous.maximum_version = ssl.TLSVersion.TLSv1_3
    with serving(config, runtime) as server:
        with pytest.raises(CoreRequestError):
            CoreClient(f"https://localhost:{server.server_port}", anonymous).health()
        connection = HTTPSConnection("localhost", server.server_port, context=context, timeout=10)
        try:
            connection.request("POST", "/api/v1/network/step", "{}",
                               {"Content-Type": "application/json", "X-User": "orion-admin", "X-Role": "administrator"})
            response = connection.getresponse()
            assert response.status == 403
            response.read()
        finally:
            connection.close()


def test_core_identity_is_not_an_os_fallback_and_worker_config_drops_sessions(provisioned):
    _, runtime, *_ = provisioned
    with runtime.identity_scope(None), pytest.raises(AuthenticationError):
        runtime.authorize("network.read", "workspace:test")
    with pytest.raises(AuthenticationError):
        runtime.local_identity()
    # Arbitrary provider credentials cannot be exported through spawn configuration.
    with pytest.raises(ValueError, match="reconstructed"):
        runtime.worker_configuration()


@pytest.mark.parametrize("change", [
    {"password": "not-a-config-secret"},
    {"identity": {"subject": "orion-core", "credential_file": "relative.secret"}},
    {"http": {"host": "0.0.0.0", "port": 8765, "allowed_hosts": ["localhost"]}},
    {"http": {"host": "127.0.0.1", "port": True, "allowed_hosts": ["127.0.0.1"]}},
    {"vault": {"key_id": "x", "key_file": "relative"}},
    {"schema": "unknown"},
    {"oidc": "invalid"},
    {"oidc": {"issuer": "https://issuer.test", "audience": "orion", "jwks": {"keys": []},
              "clock_skew_seconds": True}},
    {"oidc": {"issuer": "https://issuer.test", "audience": "orion", "jwks": {"keys": [{"d": "secret"}]}}},
    {"provisioning_identity": False},
    {"provisioning_identity": {}},
    {"clock": {"enabled": 1, "period_s": 1, "stop_timeout_s": 10}},
    {"clock": {"enabled": True, "period_s": True, "stop_timeout_s": 10}},
    {"clock": {"enabled": True, "period_s": float("nan"), "stop_timeout_s": 10}},
    {"clock": {"enabled": True, "period_s": 0, "stop_timeout_s": 10}},
    {"clock": {"enabled": True, "period_s": 1, "stop_timeout_s": 61}},
    {"clock": {"enabled": True}},
    {"identity": {"subject": "os:fake", "credential_file": str(ROOT / "fake.secret")}},
    {"identity": {"subject": "s" * 513, "credential_file": str(ROOT / "fake.secret")}},
    {"identity": {"subject": "s" * 129, "credential_file": str(ROOT / "fake.secret")}},
    {"identity": {"subject": "invalid subject", "credential_file": str(ROOT / "fake.secret")}},
    {"state_root": str(ROOT / "src" / "..")},
])
def test_invalid_deployment_config_is_rejected_before_state_creation(tmp_path, change):
    value = settings(tmp_path)
    value.update(change)
    with pytest.raises(ValueError):
        CoreConfig.parse(value)
    assert not (tmp_path / "state").exists()


def test_identical_admin_and_service_subjects_are_rejected_before_bootstrap(tmp_path):
    value = settings(tmp_path)
    value["provisioning_identity"]["subject"] = value["identity"]["subject"]
    with pytest.raises(ValueError):
        CoreConfig.parse(value)
    assert not (tmp_path / "state").exists()


def test_config_retains_secret_reference_for_nofollow_reader(tmp_path):
    value = settings(tmp_path)
    configured = tmp_path / "private" / ".." / "credential.secret"
    value["identity"]["credential_file"] = str(configured)
    assert CoreConfig.parse(value).identity.credential_file == configured


def test_config_rejects_duplicate_fields(tmp_path):
    path = tmp_path / "config.json"
    path.write_text('{"schema":"x","schema":"y"}')
    with pytest.raises(ValueError):
        load_config(path)


@pytest.mark.parametrize("url", ["http://localhost:8765", "https://user:password@localhost", "https://localhost/?token=x"])
def test_client_cannot_disable_verified_tls_or_smuggle_credentials(url):
    context = ssl.create_default_context()
    context.minimum_version = context.maximum_version = ssl.TLSVersion.TLSv1_3
    with pytest.raises(ValueError):
        CoreClient(url, context)


@pytest.mark.parametrize("settings", [{"max_concurrency": 1.5}, {"max_concurrency": True},
                                    {"requests_per_minute": 10.5}, {"requests_per_minute": True},
                                    {"body_timeout_seconds": True}, {"operation_timeout_seconds": float("nan")},
                                    {"operation_timeout_seconds": float("inf")}])
def test_fractional_quotas_cannot_disable_bounded_admission(settings):
    with pytest.raises(ValueError):
        HTTPPolicy(**settings)


def test_service_shutdown_closes_both_satellite_receivers_and_journal(monkeypatch):
    import quantum_autopilot.service.cli as cli
    calls = []
    class Engine:
        def close_satellite_receivers(self):
            calls.append("receivers")
    class Service:
        _network_engine = Engine()
        def _release_controller_io_resources(self):
            calls.append("journal")
    class Server:
        service = Service()
        def serve_forever(self, **_kwargs):
            calls.append("serve")
        def server_close(self):
            calls.append("socket")
    monkeypatch.setattr(cli.signal, "signal", lambda *_args: None)
    cli._serve(Server())
    assert calls == ["serve", "socket", "receivers", "journal"]


def test_service_shutdown_does_not_close_resources_before_processing_stops(monkeypatch):
    import quantum_autopilot.service.cli as cli
    calls = []
    class Clock:
        def start(self):
            calls.append("clock-start")
        def stop(self, **_kwargs):
            calls.append("clock-stop")
            raise TimeoutError("processing still owns storage")
    class Service:
        _network_engine = None
        def _release_controller_io_resources(self):
            calls.append("journal-closed")
    class Server:
        service = Service()
        def serve_forever(self, **_kwargs):
            calls.append("serve")
        def server_close(self):
            calls.append("socket-closed")
    monkeypatch.setattr(cli.signal, "signal", lambda *_args: None)
    with pytest.raises(TimeoutError):
        cli._serve(Server(), clock=Clock())
    assert calls == ["clock-start", "serve", "clock-stop"]


def test_headless_clock_is_visible_and_recalibration_takes_exclusive_handoff(provisioned):
    from quantum_autopilot.service.clock import HeadlessClock
    import time
    config, runtime, *_rest, operator_context = provisioned
    with serving(config, runtime) as server:
        client = CoreClient(f"https://localhost:{server.server_port}", operator_context)
        topology = client.console_snapshot()["topology"]["topology"]
        clock = HeadlessClock(server.service, period_s=.1)
        server.service._headless_clock = clock
        clock.start()
        try:
            deadline = time.monotonic() + 15
            while clock.status()["processed_steps"] < 1 and time.monotonic() < deadline:
                time.sleep(.01)
            assert clock.status()["processed_steps"] >= 1
            assert client.health()["processing_clock"]["active"]
            result = client.console_command("recalibrate_node", {
                "topology_id": topology["topology_id"], "expected_version": topology["topology_version"],
                "node_id": topology["nodes"][0]["node_id"]})
            assert result["command"]["record"]["events"][-1]["state"] == "CONFIRMED"
            assert result["calibration"]["status"] == "complete"
            assert clock.status()["hold_count"] == 1 and clock.status()["hold_depth"] == 0
            del client  # no connected presentation client drives the next tick
            before = clock.status()["processed_steps"]
            deadline = time.monotonic() + 15
            while clock.status()["processed_steps"] <= before and time.monotonic() < deadline:
                time.sleep(.01)
            assert clock.status()["processed_steps"] > before
            assert runtime.audit.verify()["valid"]
        finally:
            clock.stop()


def test_default_https_port_is_sent_in_explicit_host_header(monkeypatch):
    import quantum_autopilot.service.client as client_module
    calls = []
    class Response:
        status = 200
        def read(self, _limit):
            return b'{"status":"ok"}'
    class Connection:
        def __init__(self, *args, **kwargs):
            pass
        def request(self, method, path, body=None, headers=None):
            calls.append(headers)
        def getresponse(self):
            return Response()
        def close(self):
            pass
    monkeypatch.setattr(client_module.http.client, "HTTPSConnection", Connection)
    context = ssl.create_default_context()
    context.minimum_version = context.maximum_version = ssl.TLSVersion.TLSv1_3
    assert CoreClient("https://example.test", context).health()["status"] == "ok"
    assert calls[0]["Host"] == "example.test:443"


@pytest.mark.skipif(os.name != "posix", reason="actual private secret mounts require a POSIX host")
def test_linux_cli_provision_restart_and_check_with_no_windows_identity(tmp_path, capsys):
    from quantum_autopilot.service.cli import main
    value = settings(tmp_path)
    for name in ("master", "admin", "service"):
        secret = tmp_path / (name + ".secret")
        secret.write_bytes(os.urandom(32))
        secret.chmod(0o600)
    config = tmp_path / "config.json"
    config.write_text(json.dumps(value))
    args = ["--config", str(config)]
    assert main([*args, "check"]) == 2  # No implicit policy/CA creation in serve/check.
    assert main([*args, "provision", "--server-names", "orion-core"]) == 0
    assert main([*args, "check"]) == 0
    assert main([*args, "provision"]) == 2
    assert main([*args, "status"]) == 0
    out = capsys.readouterr()
    assert '"identity_profile": "service"' in out.out
    assert '"source": "os"' not in out.out
