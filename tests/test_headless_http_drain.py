"""Real HTTP lifecycle and authorization after a bounded clock handoff.

Transport tests use a deliberately inert backend. Authorization tests use the
real protected service boundary, encrypted vault and identity policy; the
recording engine performs no scientific calculation or physical operation.
"""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from http.client import HTTPConnection
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import os
from pathlib import Path
import ssl
from threading import Event, Thread
import time
from types import SimpleNamespace

import pytest

from quantum_autopilot.qkd.dashboard_api import AssuranceDashboardService
from quantum_autopilot.security import AuthenticationError, SecurityRuntime
from quantum_autopilot.security.http_boundary import BoundedHTTPServer, HTTPPolicy
from quantum_autopilot.security.secret_providers import AESGCMSecretProvider
from quantum_autopilot.security.service_boundary import admit_service_call, request_deadline
from quantum_autopilot.security.service_identity import ServiceIdentityBinding, ServiceIdentityProvision
from quantum_autopilot.service import cli
from quantum_autopilot.service.clock import HeadlessClock


ROOT = Path(__file__).resolve().parents[1]


def wait_for(predicate, *, timeout_s=5):
    deadline = time.monotonic() + timeout_s
    while not predicate() and time.monotonic() < deadline:
        time.sleep(.005)
    assert predicate(), "bounded lifecycle condition was not reached"


def request(server):
    connection = HTTPConnection("127.0.0.1", server.server_port, timeout=5)
    try:
        connection.request("GET", "/")
        response = connection.getresponse()
        return response.status, response.read()
    finally:
        connection.close()


def transport_fixture():
    entered, release = Event(), Event()
    calls = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            entered.set()
            assert release.wait(5), "test did not release the admitted request"
            self.send_response(200)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, *_args):
            pass

    class Engine:
        def close_satellite_receivers(self):
            assert release.is_set(), "receiver closed while request still owns it"
            calls.append("receivers")

    class Service:
        _network_engine = Engine()

        def _release_controller_io_resources(self):
            assert release.is_set(), "journal closed while request still owns it"
            calls.append("journal")

    server = BoundedHTTPServer(("127.0.0.1", 0), Handler, policy=HTTPPolicy())
    # These are the headless composition settings, with explicit bounded drain.
    server.daemon_threads, server.block_on_close = False, False
    server.service = Service()
    return server, entered, release, calls


@contextmanager
def serving(server):
    thread = Thread(target=server.serve_forever, kwargs={"poll_interval": .01})
    thread.start()
    try:
        yield
    finally:
        server.shutdown()
        server.drain_requests(timeout_s=2)
        server.server_close()
        thread.join(2)
        assert not thread.is_alive()


def test_cli_shutdown_drains_real_http_before_closing_backend(monkeypatch):
    server, entered, release, calls = transport_fixture()
    drain_started = Event()
    original_drain = server.drain_requests

    def drain(**kwargs):
        drain_started.set()
        assert not calls
        return original_drain(**kwargs)

    server.drain_requests = drain
    monkeypatch.setattr(cli.signal, "signal", lambda *_args: None)

    def finish():
        assert entered.wait(2)
        server.shutdown()
        assert drain_started.wait(2)
        assert not calls
        release.set()

    with ThreadPoolExecutor(max_workers=2) as workers:
        response, coordinator = workers.submit(request, server), workers.submit(finish)
        try:
            cli._serve(server, shutdown_timeout_s=2)
        finally:
            release.set()
            original_drain(timeout_s=2)
            server.server_close()
        assert response.result(2)[0] == 200
        coordinator.result(2)
    assert calls == ["receivers", "journal"]
    assert server._active_requests == 0


def test_cli_http_drain_timeout_preserves_resources_in_use(monkeypatch):
    server, entered, release, calls = transport_fixture()
    monkeypatch.setattr(cli.signal, "signal", lambda *_args: None)

    def finish():
        assert entered.wait(2)
        server.shutdown()

    with ThreadPoolExecutor(max_workers=2) as workers:
        response, coordinator = workers.submit(request, server), workers.submit(finish)
        try:
            with pytest.raises(TimeoutError, match="HTTP processing still owns"):
                cli._serve(server, shutdown_timeout_s=.03)
            assert not calls
            assert server._active_requests == 1
            assert server.socket.fileno() != -1
        finally:
            release.set()
            server.drain_requests(timeout_s=2)
            server.server_close()
        assert response.result(2)[0] == 200
        coordinator.result(2)
    assert not calls


def test_draining_rejects_new_http_and_preserves_admitted_request():
    server, entered, release, _calls = transport_fixture()
    with serving(server), ThreadPoolExecutor(max_workers=1) as workers:
        response = workers.submit(request, server)
        try:
            assert entered.wait(2)
            server.begin_draining()
            assert request(server)[0] == 503
            assert server._active_requests == 1
        finally:
            release.set()
        assert response.result(2)[0] == 200
        server.drain_requests(timeout_s=2)
        assert server._active_requests == 0


def test_request_is_counted_before_handler_thread_creation(monkeypatch):
    server, _entered, release, _calls = transport_fixture()
    release.set()
    dispatch_entered, allow_thread = Event(), Event()
    original = ThreadingHTTPServer.process_request

    def delayed_dispatch(instance, channel, address):
        dispatch_entered.set()
        assert allow_thread.wait(5)
        return original(instance, channel, address)

    monkeypatch.setattr(ThreadingHTTPServer, "process_request", delayed_dispatch)
    with serving(server), ThreadPoolExecutor(max_workers=1) as workers:
        response = workers.submit(request, server)
        try:
            assert dispatch_entered.wait(2)
            assert server._active_requests == 1
            with pytest.raises(TimeoutError, match="HTTP processing still owns"):
                server.drain_requests(timeout_s=.02)
        finally:
            allow_thread.set()
        assert response.result(2)[0] == 200
        server.drain_requests(timeout_s=2)
        assert server._active_requests == 0


def test_failed_tls_handshake_releases_admitted_request():
    server, entered, release, _calls = transport_fixture()
    release.set()
    server.ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    with serving(server):
        # Plain HTTP is deliberately invalid at this TLS-only transport.
        with pytest.raises((OSError, ConnectionError)):
            request(server)
        wait_for(lambda: server._active_requests == 0)
        assert not entered.is_set()
        server.drain_requests(timeout_s=.1)


@pytest.mark.parametrize("timeout", [False, 0, float("nan"), float("inf"), 61])
def test_invalid_drain_timeout_does_not_start_shutdown(timeout):
    server, _entered, release, _calls = transport_fixture()
    release.set()
    try:
        with pytest.raises(ValueError):
            server.drain_requests(timeout_s=timeout)
        assert not server._draining
    finally:
        server.server_close()


class FixtureKeys:
    def __init__(self):
        self.value = os.urandom(32)

    def read_key(self, key_id, version):
        assert (key_id, version) == ("handoff-fixture", 1)
        return self.value


@pytest.fixture
def handoff_service(tmp_path, monkeypatch):
    import quantum_autopilot.security.runtime as runtime_module
    import quantum_autopilot.security.vault as vault_module

    def forbidden(*_args, **_kwargs):
        raise AssertionError("headless handoff used Windows session or DPAPI")

    monkeypatch.setattr(runtime_module, "operating_system_identity", forbidden)
    monkeypatch.setattr(vault_module, "_dpapi", forbidden)
    provider = AESGCMSecretProvider(FixtureKeys(), active_key_id="handoff-fixture", active_version=1)
    admin_secret, service_secret = os.urandom(48), os.urandom(48)
    administrator = SecurityRuntime(tmp_path / "security", profile="operational", identity_profile="service",
        secret_provider=provider, bootstrap=True,
        service_bootstrap=ServiceIdentityProvision("handoff-admin", ("administrator",), ("*",), admin_secret))
    admin = administrator.authenticate_service("handoff-admin", admin_secret)
    with administrator.identity_scope(admin):
        administrator.grant_service_identity("handoff-core", ("operator",), ("*",), service_secret)
        administrator.grant_identity("calibration-client", ("operator",), ("*",), source="certificate")
    runtime = SecurityRuntime(tmp_path / "security", profile="operational", identity_profile="service",
        bootstrap=False, secret_provider=provider,
        service_identity=ServiceIdentityBinding("handoff-core", lambda: service_secret))
    service = AssuranceDashboardService(ROOT, control_journal_path=tmp_path / "workspace.sqlite", security=runtime)
    actor = runtime.principal("calibration-client")
    entered, release = Event(), Event()
    engine = SimpleNamespace(calibration_calls=0)

    def run_calibration(*_args, **_kwargs):
        engine.calibration_calls += 1
        return {"topology": {}, "calibration": {}}

    engine.run_calibration = run_calibration
    service._network_engine = engine
    ticks = []

    def step(*, metadata_only=False):
        admit_service_call(service, "network_step")
        entered.set()
        assert release.wait(5)
        ticks.append(None)
        return {"tick": len(ticks), "network_time_s": len(ticks),
                "publication_revision": len(ticks), "source": {"kind": "none"}}

    service.network_step = step
    yield service, runtime, administrator, admin, actor, entered, release, engine
    release.set()
    service._release_controller_io_resources()


@pytest.mark.parametrize("cause", ["revocation", "deadline"])
def test_calibration_revalidates_after_waiting_for_clock(handoff_service, cause):
    service, runtime, administrator, admin, actor, entered, release, engine = handoff_service
    clock = HeadlessClock(service, period_s=.01)
    service._headless_clock = clock
    clock.start()
    operation_deadlines = []

    def procedure():
        with runtime.identity_scope(actor):
            deadline = time.monotonic() + 2 if cause == "deadline" else None
            operation_deadlines.append(deadline)
            token = request_deadline.set(deadline)
            try:
                return service.recalibrate_node({"node_id": "alice"})
            finally:
                request_deadline.reset(token)

    with ThreadPoolExecutor(max_workers=1) as workers:
        try:
            assert entered.wait(2)
            future = workers.submit(procedure)
            wait_for(lambda: clock.status()["hold_depth"] == 1)
            if cause == "revocation":
                with administrator.identity_scope(admin):
                    administrator.revoke_identity(actor.subject)
                expected = AuthenticationError
            else:
                wait_for(lambda: time.monotonic() >= operation_deadlines[0])
                expected = TimeoutError
            release.set()
            with pytest.raises(expected):
                future.result(2)
            assert engine.calibration_calls == 0
            assert runtime.audit.verify()["valid"]
        finally:
            release.set()
            clock.stop(timeout_s=2)
