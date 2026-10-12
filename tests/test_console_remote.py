"""Remote Qt composition: real TLS backend plus explicit scheduling fixtures.

The controlled-client fixtures below exercise lifecycle/backpressure only; they
are labelled test records and are not satellite/hardware qualification.
"""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import ssl
from threading import Event
import time

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import QCoreApplication, QEvent, QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from quantum_autopilot.console.app import build_parser, build_provider  # noqa: E402
from quantum_autopilot.console.remote_provider import RemoteProvider, remote_snapshot  # noqa: E402
from quantum_autopilot.console.remote_security import RemoteConsoleWindow, core_tls_context  # noqa: E402
from quantum_autopilot.service.client import CoreClient, CoreRequestError  # noqa: E402
from tests.test_console_service_provider import FakeService  # noqa: E402
from tests.test_portable_core_service import provisioned, serving  # noqa: E402,F401


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def deliver(app, predicate, timeout=15):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.processEvents()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        if predicate():
            return
        time.sleep(.005)
    raise AssertionError("bounded remote operation did not complete")


def fixture_frame(*, roles=("operator",), subject="fixture-certificate-peer"):
    service = FakeService()
    return {"schema": "orion.console.remote.v1", "health": service.health(),
            "topology": service.control_plane_topology(), "model": service.wdm_model_status(),
            "events": service.control_plane_events()["events"], "summary": service.summary(),
            "evidence_lanes": service.evidence_lanes(), "live": None,
            "security": {"subject": subject, "roles": list(roles), "scopes": ["*"],
                         "source": "certificate", "fixture": True},
            "replay": {"available": False, "reason": "identified scheduler fixture"}}


class ControlledClient:
    """Deterministic fixture transport, never a selectable production client."""
    def __init__(self, frame=None):
        self.frame = frame or fixture_frame()
        self.reads = []
        self.commands = []
        self.entered, self.release = Event(), Event()
        self.block = False
        self.command_error = None

    def console_snapshot(self, *, include_static=True):
        self.reads.append(include_static)
        self.entered.set()
        if self.block:
            assert self.release.wait(10), "fixture wait expired"
        result = deepcopy(self.frame)
        if not include_static:
            for key in ("model", "summary", "evidence_lanes"):
                result.pop(key)
        return result

    def network_command(self, verb, payload):
        self.commands.append((verb, payload))
        if self.command_error:
            raise self.command_error
        return {"physical_action_performed": False, "status": "fixture-only"}


def test_real_tls_remote_snapshot_selection_and_backend_mutation(app, provisioned, tmp_path, monkeypatch):
    config, runtime, *_rest, operator_context = provisioned
    import quantum_autopilot.console.app as console_app
    def forbidden(*_args, **_kwargs):
        raise AssertionError("remote desktop attempted to construct a local core")
    monkeypatch.setattr(console_app, "console_security", forbidden)
    monkeypatch.setattr(console_app, "build_service", forbidden)
    monkeypatch.setattr(console_app, "build_demo_service", forbidden)
    with serving(config, runtime) as server:
        client = CoreClient(f"https://localhost:{server.server_port}", operator_context)
        provider = RemoteProvider(client, endpoint=f"https://localhost:{server.server_port}",
                                  poll_ms=60000, export_directory=tmp_path / "exports")
        errors = []
        provider.error.connect(errors.append)
        provider.start()
        try:
            deliver(app, lambda: provider.ready or bool(errors))
            assert not errors
            snapshot = provider.snapshot()
            assert snapshot.security["subject"] == "orion-core"
            assert snapshot.security["source"] in {"service", "certificate"}
            assert snapshot.live is not None and snapshot.live.source_kind == "none"
            assert provider.security is None and not hasattr(provider, "service")
            before = client.network_state()["tick"]
            provider.select_node(snapshot.topology.nodes[0].id)
            assert provider.snapshot().node.id == snapshot.topology.nodes[0].id
            assert provider.dispatch("configure_network_input", json.dumps({"kind": "simulated", "seed": 17}))
            deliver(app, lambda: not provider.busy and provider.last_network_result is not None)
            assert not errors
            assert client.network_state()["source"]["kind"] == "simulated"
            # Polling presents only; the independently authorized backend steps.
            assert client.network_state()["tick"] == before
            published = client.network_step()
            provider.request_refresh()
            deliver(app, lambda: provider.snapshot().live.tick == published["tick"])
            assert provider.snapshot().node.id == snapshot.topology.nodes[0].id
            assert any(link.qber is not None for link in provider.snapshot().live.links)
            assert not (tmp_path / "client-journal.sqlite").exists()
        finally:
            provider.stop()


def test_remote_window_login_navigation_theme_and_export_use_core_only(app, provisioned, tmp_path, monkeypatch):
    config, runtime, _, _, _, _, _, operator_context = provisioned
    import quantum_autopilot.console.login_session as login_session
    import quantum_autopilot.console.service_provider as service_provider
    def forbidden(*_args, **_kwargs):
        raise AssertionError("remote window attempted Windows login/local service")
    monkeypatch.setattr(login_session, "NativeLoginController", forbidden)
    monkeypatch.setattr(service_provider.ServiceProvider, "__init__", forbidden)
    ui_root = tmp_path / "ui-preferences"
    with serving(config, runtime) as server:
        endpoint = f"https://localhost:{server.server_port}"
        provider = RemoteProvider(CoreClient(endpoint, operator_context), endpoint=endpoint,
                                  poll_ms=60000, export_directory=tmp_path / "exports")
        window = RemoteConsoleWindow(provider, state_root=ui_root)
        window.resize(1280, 900)
        window.show()
        window.provider.start()
        try:
            deliver(app, lambda: provider.ready)
            assert window._security is None and window._login_controller is None
            assert window.boot.operator_id.text() == "orion-core"
            assert window.boot.operator_id.isReadOnly() and not window.boot.password.isEnabled()
            assert not window.boot.os_session_button.isVisible()
            assert tuple(window.boot.source_rows) == ("service",)
            assert window.boot.open_button.isEnabled()
            assert window.open_workspace()
            assert window._current == "topology"
            selected = provider.snapshot().topology.nodes[0].id
            window.select_node(selected)
            assert provider.snapshot().node.id == selected
            window.show_screen("node")
            node_screen = window.screens["node"]
            original_version = provider.snapshot().topology.version
            node_screen.isolate.click()
            deliver(app, lambda: provider.snapshot().topology.version == original_version + 1)
            assert next(node for node in provider.snapshot().topology.nodes if node.id == selected).isolated
            assert provider.snapshot().ledger.commands[0].state == "CONFIRMED"
            node_screen.isolate.click()
            deliver(app, lambda: provider.snapshot().topology.version == original_version + 2)
            assert not next(node for node in provider.snapshot().topology.nodes if node.id == selected).isolated
            topology = window.screens["topology"]
            window.show_screen("topology")
            runner = topology.path_runner
            runner.begin()
            raw_nodes = provider._raw["topology"]["topology"]["nodes"]
            endpoints = [node["node_id"] for node in raw_nodes if node["role"] == "endpoint"]
            repeater = next(node["node_id"] for node in raw_nodes if node["role"] == "repeater")
            for key, identifier in (("source", endpoints[0]), ("repeater", repeater), ("destination", endpoints[1])):
                runner.fields[key].setCurrentText(identifier)
            runner.target.setText("0.8")
            run_id = runner.run_id
            runner.submit()
            deliver(app, lambda: provider.snapshot().workspace_path is not None
                    and provider.snapshot().workspace_path.run_id == run_id)
            assert runner.pending is None and "CONFIRMED" in runner.note.text()
            assert provider.last_network_result["record"]["physical_action_performed"] is False
            runner.close()
            for screen in window.screens:
                window.show_screen(screen)
                app.processEvents()
            previous_theme = window.canvas.property("orionSignalBrand")
            window.set_theme("light")
            assert window.boot.operator_id.text() == "orion-core"
            assert window._security is None
            assert window.canvas.property("orionSignalBrand") == previous_theme
            assert not window.apply_source("demo")
            assert window.provider.active is provider
            exports = []
            provider.export_completed.connect(exports.append)
            destination = tmp_path / "chosen-remote-evidence.json"
            assert window.provider.dispatch("export_record", json.dumps({"scope": "network", "destination": str(destination)}))
            deliver(app, lambda: bool(exports) and not provider.busy)
            assert Path(exports[0]) == destination
            exported = json.loads(Path(exports[0]).read_text(encoding="utf-8"))
            assert exported["evidence"]["security"]["subject"] == "orion-core"
            assert exported["physical_action_performed"] is False
            assert not ui_root.exists(), "remote viewing must not create a local service workspace"
        finally:
            window.close()
            app.processEvents()


def test_readonly_peer_and_live_revocation_disable_remote_controls(app, provisioned):
    config, runtime, admin_runtime, administrator, pki, leaf, audit_context, _ = provisioned
    with serving(config, runtime) as server:
        endpoint = f"https://localhost:{server.server_port}"
        provider = RemoteProvider(CoreClient(endpoint, audit_context), endpoint=endpoint, poll_ms=60000)
        errors = []
        provider.error.connect(errors.append)
        provider.start()
        try:
            deliver(app, lambda: provider.ready)
            assert provider.capabilities.mode == "read_only"
            assert not provider.capabilities.allows("configure_network_input")
            with pytest.raises(ValueError):
                provider.dispatch("configure_network_input", '{"kind":"simulated"}')
            before = server.service.network_engine.live_state().tick
            with admin_runtime.identity_scope(administrator):
                pki.revoke(leaf["serial"])
            provider.request_refresh()
            deliver(app, lambda: bool(errors))
            assert not provider.ready and provider.capabilities.mode == "unavailable"
            assert provider.snapshot().presentation_stats["last_snapshot_only"]
            assert server.service.network_engine.live_state().tick == before
        finally:
            provider.stop()


def test_anonymous_tls_failure_leaves_remote_login_unavailable(app, provisioned, tmp_path):
    config, runtime, _, _, pki, *_ = provisioned
    context = ssl.create_default_context(cafile=str(pki.ca_path))
    context.minimum_version = context.maximum_version = ssl.TLSVersion.TLSv1_3
    with serving(config, runtime) as server:
        endpoint = f"https://localhost:{server.server_port}"
        provider = RemoteProvider(CoreClient(endpoint, context), endpoint=endpoint, poll_ms=60000)
        window = RemoteConsoleWindow(provider, state_root=tmp_path / "absent-ui-state")
        errors = []
        provider.error.connect(errors.append)
        window.provider.start()
        try:
            deliver(app, lambda: bool(errors))
            assert not provider.ready and provider.capabilities.operations == ()
            assert not window.boot.open_button.isEnabled()
            assert not window.open_workspace()
            assert window._security is None and window._login_controller is None
            assert not (tmp_path / "absent-ui-state").exists()
        finally:
            window.close()
            app.processEvents()


def test_global_geo_remote_satellites_and_mode_changes_preserve_backend_state(app, provisioned, tmp_path):
    from quantum_autopilot.network_engine.institutional import ensure_institutional_workspace
    config, runtime, _, _, _, _, _, operator_context = provisioned
    with serving(config, runtime) as server:
        # Backend fixture setup installs the existing complete demo topology.
        # The remote GUI subsequently receives only normal authenticated API records.
        ensure_institutional_workspace(server.service.topology_store)
        endpoint = f"https://localhost:{server.server_port}"
        class CountingClient(CoreClient):
            reads = 0
            def console_snapshot(self, *, include_static=True):
                self.reads += 1
                return super().console_snapshot(include_static=include_static)
        client = CountingClient(endpoint, operator_context)
        client.network_command("configure_network_input", {"kind": "simulated", "seed": 17, "scenario": "quiet"})
        published = client.network_step()
        provider = RemoteProvider(client, endpoint=endpoint, poll_ms=60000)
        window = RemoteConsoleWindow(provider, state_root=tmp_path / "no-local-engine")
        window.resize(1280, 900)
        window.show()
        window.provider.start()
        try:
            deliver(app, lambda: provider.ready, timeout=30)
            assert window.open_workspace()
            window.show_screen("topology")
            geo = window.screens["topology"].geo
            assert provider.snapshot().live.tick == published["tick"]
            satellites = {node.id for node in geo.net.nodes if node.physical_type == "satellite"}
            assert len(satellites) >= 2
            assert satellites == set(geo.net.satellites)
            selected = sorted(satellites)[0]
            window.select_node(selected)
            reads = client.reads
            topology_id, version = provider.snapshot().topology.id, provider.snapshot().topology.version
            for mode in ("globe", "map", "globe", "map", "globe", "map"):
                geo.set_mode(mode)
                deliver(app, lambda: geo.mode == mode and geo.renderer is not None
                        and geo.resource_stats()["renderer_instances"] == 1)
                assert geo.resource_stats()["active_renderers"] == 1
                assert set(geo.net.satellites) == satellites
                assert provider.snapshot().node.id == selected
                if mode == "globe":
                    assert {identifier for identifier, _ in geo.globe._states()} == satellites
                    assert all(points for _, points in geo.globe._tracks)
            assert client.reads == reads, "switching a renderer must not request another backend frame"
            assert (provider.snapshot().topology.id, provider.snapshot().topology.version) == (topology_id, version)
            assert client.network_state()["tick"] == published["tick"]
            assert not (tmp_path / "no-local-engine").exists()
        finally:
            window.close()
            app.processEvents()


def test_mutual_tls_context_rejects_encrypted_key_without_prompt_and_accepts_protected_reader(provisioned):
    _, _, admin_runtime, administrator, _, leaf, *_ = provisioned
    parameters = {"ca_file": leaf["ca"], "certificate_file": leaf["certificate"], "key_file": leaf["private_key"]}
    started = time.monotonic()
    with pytest.raises(ssl.SSLError):
        core_tls_context(**parameters)
    assert time.monotonic() - started < 3
    with admin_runtime.identity_scope(administrator):
        secret = admin_runtime.vault.get_bytes(leaf["password_ref"])
    context = core_tls_context(**parameters, key_password_reader=lambda: secret)
    assert context.minimum_version == context.maximum_version == ssl.TLSVersion.TLSv1_3
    assert context.verify_mode == ssl.CERT_REQUIRED and context.check_hostname
    def unavailable_reader():
        raise RuntimeError("canary-client-password")
    with pytest.raises(ValueError) as failure:
        core_tls_context(**parameters, key_password_reader=unavailable_reader)
    assert "canary-client-password" not in str(failure.value)


def test_remote_factory_and_executable_forwarding_do_not_resolve_local_core(app, tmp_path, monkeypatch):
    import quantum_autopilot.console.app as console_app
    import quantum_autopilot.console.remote_security as remote_security
    import quantum_autopilot.desktop as desktop
    def forbidden(*_args, **_kwargs):
        raise AssertionError("remote launch resolved local identity/state")
    context = ssl.create_default_context()
    context.minimum_version = context.maximum_version = ssl.TLSVersion.TLSv1_3
    monkeypatch.setattr(remote_security, "core_tls_context", lambda **_: context)
    monkeypatch.setattr(console_app, "console_security", forbidden)
    monkeypatch.setattr(desktop, "resolve_native_paths", forbidden)
    args = build_parser().parse_args(["--provider", "remote", "--core-url", "https://localhost:8765",
                                     "--core-ca", str(tmp_path / "ca.pem"), "--core-cert", str(tmp_path / "peer.pem"),
                                     "--core-key", str(tmp_path / "peer.key")])
    provider = build_provider(args)
    assert isinstance(provider, RemoteProvider) and provider.security is None
    assert not hasattr(args, "_security") and not (tmp_path / "journal.sqlite").exists()
    received = []
    monkeypatch.setattr(console_app, "run", lambda arguments, **kwargs: received.append((arguments, kwargs)) or 0)
    executable_args = desktop.build_parser().parse_args(["--core-url", args.core_url,
        "--core-ca", str(args.core_ca), "--core-cert", str(args.core_cert), "--core-key", str(args.core_key)])
    assert desktop._run_native(executable_args) == 0
    forwarded, _ = received[0]
    assert forwarded.provider == "remote" and forwarded.core_url == args.core_url
    assert forwarded.control_journal is None and forwarded.wdm_model_bundle is None


def test_slow_remote_poll_is_bounded_and_does_not_block_qt(app):
    client = ControlledClient()
    client.block = True
    provider = RemoteProvider(client, endpoint="https://fixture.example.test", poll_ms=250)
    heartbeats = []
    timer = QTimer()
    timer.setInterval(10)
    timer.timeout.connect(lambda: heartbeats.append(time.monotonic()))
    timer.start()
    provider.start()
    try:
        deliver(app, lambda: client.entered.is_set())
        for _ in range(10):
            assert not provider.request_refresh()
        deliver(app, lambda: len(heartbeats) >= 10)
        assert len(client.reads) == 1 and not provider.ready
        client.release.set()
        deliver(app, lambda: provider.ready)
        provider.select_node("repeater-a")
        provider.request_refresh()
        deliver(app, lambda: provider._poll_future is None)
        assert client.reads[:2] == [True, False]
        assert provider.snapshot().node.id == "repeater-a"
        assert provider.snapshot().keys.status != "GRID UNAVAILABLE"
    finally:
        client.release.set()
        provider.stop()
        timer.stop()


def test_uncertain_mutation_is_never_automatically_retried_or_logged(app):
    client = ControlledClient()
    provider = RemoteProvider(client, endpoint="https://fixture.example.test", poll_ms=60000)
    errors = []
    provider.error.connect(errors.append)
    provider.start()
    try:
        deliver(app, lambda: provider.ready)
        client.command_error = CoreRequestError(None, uncertain=True)
        assert provider.dispatch("set_ml_control", '{"enabled":false}')
        deliver(app, lambda: bool(errors))
        assert len(client.commands) == 1
        assert not provider.ready and provider.snapshot().presentation_stats["mutation_outcome_uncertain"]
        assert "receipts" in errors[-1]
        provider.request_refresh()
        deliver(app, lambda: provider.ready)
        assert len(client.commands) == 1, "reconnect must read; it must not retry a mutation"
        assert provider.snapshot().presentation_stats["mutation_outcome_uncertain"]
        assert provider.snapshot().presentation_stats["last_remote_command"]["client_correlation_id"]
        assert provider.dispatch("read_console_commands")
        deliver(app, lambda: provider._poll_future is None)
        assert len(client.commands) == 1
        client.command_error = RuntimeError("secret-canary-must-not-reach-Qt")
        provider.dispatch("set_ml_control", '{"enabled":false}')
        deliver(app, lambda: len(errors) == 2)
        assert "secret-canary" not in " ".join(errors)
    finally:
        provider.stop()


def test_stopped_provider_does_not_admit_late_poll_or_accumulate_workers(app):
    client = ControlledClient()
    client.block = True
    provider = RemoteProvider(client, endpoint="https://fixture.example.test", poll_ms=250)
    delivered = []
    provider.snapshot_changed.connect(delivered.append)
    provider.start()
    deliver(app, lambda: client.entered.is_set())
    future = provider._poll_future
    provider.stop()
    client.release.set()
    deliver(app, future.done)
    assert not delivered and not provider.ready
    client.block = False
    provider.start()
    deliver(app, lambda: provider.ready)
    provider.stop()
    assert len(delivered) == 1


@pytest.mark.parametrize("change", [
    {"health": {"physical_read_only": False, "automation_allowed": False}},
    {"health": {"physical_read_only": True, "automation_allowed": True}},
    {"security": {"subject": "os:forged", "roles": ["administrator"], "scopes": ["*"], "source": "os"}},
    {"schema": "unknown"},
])
def test_invalid_or_physical_remote_contract_is_rejected(change):
    frame = fixture_frame()
    frame.update(change)
    with pytest.raises(ValueError):
        remote_snapshot(frame, endpoint="https://fixture.example.test")


def test_workspace_path_envelope_is_integrity_checked_and_mapped_without_execution():
    request = {"run_id": "fixture-path", "expected_version": 6, "protocol": "werner_single_swap_v1"}
    record = {"request": request, "source_topology": {},
              "source_topology_sha256": hashlib.sha256(b"{}").hexdigest(),
              "result": {"route": ["alice", "repeater-a", "bob"], "conditional_fidelity": .86,
                         "target_fidelity": .8, "conditional_target_met": True},
              "physical_action_performed": False, "evidence_kind": "simulated"}
    canonical = json.dumps(record, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    envelope = {"record": record, "record_sha256": hashlib.sha256(canonical).hexdigest()}
    frame = dict(fixture_frame(), workspace_path=envelope)
    snapshot = remote_snapshot(frame, endpoint="https://fixture.example.test")
    assert snapshot.workspace_path.run_id == "fixture-path"
    assert snapshot.workspace_path.route == ("alice", "repeater-a", "bob")
    assert snapshot.workspace_path.fidelity == .86
    frame["workspace_path"]["record"]["result"]["conditional_fidelity"] = .99
    with pytest.raises(ValueError):
        remote_snapshot(frame, endpoint="https://fixture.example.test")
