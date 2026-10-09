"""Qt event-loop tests for worker preparation; no windows, credentials or hardware."""
from __future__ import annotations

from contextvars import ContextVar
from dataclasses import replace
import os
import threading
import time
from types import SimpleNamespace

import pytest

pytest.importorskip("PySide6.QtCore")
pytest.importorskip("PySide6.QtWidgets")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import QThread, QTimer, Signal  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from quantum_autopilot.console.capabilities import SourceCapabilities  # noqa: E402
from quantum_autopilot.console.input_sources import empty_snapshot  # noqa: E402
from quantum_autopilot.console.prepared_source import DeferredProvider  # noqa: E402
from quantum_autopilot.console.providers import DataProvider  # noqa: E402
from quantum_autopilot.console.source_switch import SwitchableProvider  # noqa: E402
from quantum_autopilot.security import SecurityRuntime  # noqa: E402
from quantum_autopilot.security import runtime as runtime_module  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def until(app, predicate, timeout=3):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.processEvents()
        if predicate():
            return
        time.sleep(.001)
    pytest.fail("Expected asynchronous state was not reached")


class FixtureProvider(DataProvider):
    busy_changed = Signal(bool)
    error = Signal(str)
    progress = Signal(str)
    dispatch_validate_backend_permissions = True

    def __init__(self, service, *, publish=True, kind="service"):
        super().__init__()
        self.service = service
        self.state_root = "fixture-workspace"
        self.has_network_engine = True
        self.source_label = "PREPARED " + kind.upper()
        self.is_demo = kind == "demo"
        self._publish = publish
        self.starts = self.stops = 0
        self.calls = []
        self._snap = replace(empty_snapshot(self.source_label, "Fixture snapshot", kind),
                             capabilities=SourceCapabilities(adapter_id="prepared-fixture", mode="simulation",
                                                             health="ready", operations=("reload", "export_record")))

    @property
    def capabilities(self):
        return self._snap.capabilities

    def start(self):
        self.starts += 1
        self.busy_changed.emit(True)
        for completed in range(1, 6):
            self.loading_progress.emit({"completed": completed, "total": 5, "message": "Measured fixture step"})
        if self._publish:
            self.snapshot_changed.emit(self._snap)
        self.busy_changed.emit(False)

    def stop(self):
        self.stops += 1

    def dispatch(self, verb, target=""):
        self.calls.append((verb, target))
        return True

    def policy_context_source(self):
        return self.service

    def policy_decision_store_path(self):
        return "fixture-policy.db"

    def command_reconciliation_store_path(self):
        return "fixture-commands.db"

    def temporal_campaign_store_path(self):
        return "fixture-topology.db"


def test_service_prepares_in_worker_while_qt_heartbeat_continues(app):
    release, entered = threading.Event(), threading.Event()
    thread_ids, beats, providers = [], [], []
    timer = QTimer()
    timer.setInterval(2)
    timer.timeout.connect(lambda: beats.append(True))
    gui_thread = threading.get_ident()

    def prepare():
        thread_ids.append(threading.get_ident())
        entered.set()
        assert release.wait(3)
        return SimpleNamespace(name="prepared")

    def create(service):
        assert threading.get_ident() == gui_thread
        provider = FixtureProvider(service)
        assert provider.thread() == QThread.currentThread()
        providers.append(provider)
        return provider

    deferred = DeferredProvider(prepare, create)
    try:
        timer.start()
        deferred.start()
        until(app, lambda: entered.is_set() and len(beats) >= 5)
        assert deferred.busy and not deferred.ready
        assert deferred.service is None and not providers
        assert deferred.capabilities.health == "unavailable"
        assert deferred.snapshot().input_kind == "service"
        release.set()
        until(app, lambda: deferred.ready)
        assert thread_ids and thread_ids[0] != gui_thread
        assert deferred.delegate is providers[0] and providers[0].starts == 1
        assert not deferred.busy
    finally:
        release.set()
        timer.stop()
        deferred.stop()


def test_loading_counts_actual_preparation_and_snapshot_steps(app):
    deferred = DeferredProvider(lambda: object(), FixtureProvider, kind="demo")
    progress, ready, changed = [], [], []
    deferred.loading_progress.connect(lambda value: progress.append((value, deferred.ready)))
    deferred.source_ready.connect(ready.append)
    deferred.source_changed.connect(changed.append)
    try:
        assert deferred.is_demo and deferred.service is None
        deferred.start()
        until(app, lambda: deferred.ready)
        completed = [value["completed"] for value, _ in progress]
        assert completed == sorted(completed)
        assert set(completed) == set(range(8))
        assert all(value["total"] == 7 for value, _ in progress)
        assert all(is_ready for value, is_ready in progress if value["completed"] == 7)
        assert len(ready) == len(changed) == 1
    finally:
        deferred.stop()


def test_delegate_terminal_progress_and_clearing_frames_cannot_claim_readiness(app):
    deferred = DeferredProvider(lambda: object(), lambda service: FixtureProvider(service, publish=False))
    progress = []
    deferred.loading_progress.connect(progress.append)
    try:
        deferred.start()
        until(app, lambda: deferred.delegate is not None)
        delegate = deferred.delegate
        assert deferred.busy and not deferred.ready
        assert max(value["completed"] for value in progress) == 6
        delegate.snapshot_changed.emit(empty_snapshot("cleared", "No committed data", "service"))
        assert not deferred.ready and deferred.service is None
        with pytest.raises(RuntimeError, match="committed"):
            deferred.dispatch("reload")
        delegate.snapshot_changed.emit(delegate._snap)
        assert deferred.ready and not deferred.busy
        assert progress[-1]["completed"] == 7
    finally:
        deferred.stop()


def test_stop_does_not_wait_for_preparation_and_late_generation_is_ignored(app):
    first_release, entered, first_done = threading.Event(), threading.Event(), threading.Event()
    lock, prepared, created = threading.Lock(), [], []

    def prepare():
        with lock:
            number = len(prepared) + 1
            prepared.append(number)
        if number == 1:
            entered.set()
            assert first_release.wait(3)
            first_done.set()
        return number

    def create(value):
        created.append(value)
        return FixtureProvider(value)

    deferred = DeferredProvider(prepare, create)
    try:
        deferred.start()
        until(app, entered.is_set)
        previous_future = deferred._future
        deferred.stop()
        assert previous_future.running() and not first_release.is_set()
        deferred.start()
        until(app, lambda: deferred.ready)
        assert created == [2] and deferred.service == 2
        first_release.set()
        until(app, lambda: first_done.is_set() and previous_future.done())
        app.processEvents()
        assert created == [2] and deferred.service == 2
    finally:
        first_release.set()
        deferred.stop()


def test_cancellation_at_prepared_milestone_prevents_qt_provider_creation(app):
    created = []
    deferred = DeferredProvider(lambda: object(), lambda service: created.append(service) or FixtureProvider(service))

    def cancel_at_prepared(value):
        if value["completed"] == 1:
            deferred.stop()

    deferred.loading_progress.connect(cancel_at_prepared)
    deferred.start()
    until(app, lambda: not deferred.running)
    assert not created and deferred.delegate is None


def test_failed_prepare_is_sanitized_and_can_restart(app):
    calls, failures = [], []

    def prepare():
        calls.append(True)
        if len(calls) == 1:
            raise ValueError("private fixture payload")
        return object()

    deferred = DeferredProvider(prepare, FixtureProvider)
    deferred.source_failure.connect(failures.append)
    try:
        deferred.start()
        until(app, lambda: bool(failures))
        assert not deferred.running and not deferred.busy
        assert "ValueError" in failures[0] and "private fixture payload" not in failures[0]
        assert deferred.capabilities.health == "unavailable"
        deferred.request_refresh()
        until(app, lambda: deferred.ready)
        assert len(calls) == 2
    finally:
        deferred.stop()


def test_failed_create_and_initial_read_release_candidate(app):
    providers, failures = [], []

    class BrokenProvider(FixtureProvider):
        def start(self):
            self.error.emit("private backend error")

    def create(service):
        provider = BrokenProvider(service)
        providers.append(provider)
        return provider

    deferred = DeferredProvider(lambda: object(), create)
    deferred.source_failure.connect(failures.append)
    deferred.start()
    until(app, lambda: bool(failures))
    assert deferred.delegate is None and not deferred.ready and not deferred.busy
    assert providers[0].stops == 1
    assert "private backend error" not in failures[0]
    deferred.stop()


def test_approved_backend_paths_and_command_signals_forward_after_ready(app):
    deferred = DeferredProvider(lambda: SimpleNamespace(name="backend"), FixtureProvider, state_root="pending-root")
    exports = []
    deferred.export_completed.connect(exports.append)
    try:
        assert deferred.state_root == "pending-root"
        with pytest.raises(RuntimeError):
            deferred.policy_context_source()
        deferred.start()
        until(app, lambda: deferred.ready)
        assert deferred.service is deferred.delegate.service
        assert deferred.state_root == "fixture-workspace" and deferred.has_network_engine
        assert deferred.policy_context_source() is deferred.service
        assert deferred.command_reconciliation_store_path() == "fixture-commands.db"
        assert deferred.temporal_campaign_store_path() == "fixture-topology.db"
        assert deferred.export_record("fixture-scope")
        assert deferred.delegate.calls == [("export_record", "fixture-scope")]
        deferred.delegate.export_completed.emit("fixture-export.json")
        assert exports == ["fixture-export.json"]
    finally:
        deferred.stop()


def test_switchable_plug_relays_loading_and_rejects_switch_while_preparing(app):
    release, entered = threading.Event(), threading.Event()

    def prepare():
        entered.set()
        assert release.wait(3)
        return object()

    deferred = DeferredProvider(prepare, FixtureProvider)
    plug = SwitchableProvider(deferred)
    progress = []
    changes = []
    plug.loading_progress.connect(progress.append)
    plug.source_changed.connect(changes.append)
    try:
        plug.start()
        until(app, entered.is_set)
        assert plug.busy
        with pytest.raises(RuntimeError):
            plug.switch(lambda: FixtureProvider(object()))
        assert plug.active is deferred
        release.set()
        until(app, lambda: deferred.ready)
        assert progress[-1]["completed"] == 7
        assert changes == [deferred.source_label]
        assert plug.snapshot().capabilities.health == "ready"
    finally:
        release.set()
        plug.stop()


@pytest.fixture
def security(tmp_path, monkeypatch):
    monkeypatch.setattr(runtime_module, "operating_system_identity",
                        lambda: ("os:sid:S-1-5-21-111111111-1001", "fixture-owner"))
    return SecurityRuntime(tmp_path / "security")


def test_preparation_and_qt_creation_keep_lower_role_and_captured_context(app, security):
    identity = security.grant_identity("service:fixture-researcher", ["researcher"], ["*"])
    marker = ContextVar("prepared-test-marker", default="missing")
    observed = []

    def observe():
        principal = security.current_identity()
        observed.append((principal.subject, principal.roles, marker.get()))

    def prepare():
        observe()
        return object()

    def create(service):
        observe()
        return FixtureProvider(service)

    with security.identity_scope(identity):
        token = marker.set("captured")
        deferred = DeferredProvider(prepare, create, security=security)
        deferred.start()
        marker.reset(token)
    try:
        assert security.current_identity().roles == frozenset({"administrator"})
        until(app, lambda: deferred.ready)
        assert observed == [(identity.subject, frozenset({"researcher"}), "captured")] * 2
        assert deferred._identity.subject == identity.subject
    finally:
        deferred.stop()


def test_explicit_anonymous_scope_cannot_fall_back_to_process_os_identity(app, security):
    prepared, failures = [], []
    with security.identity_scope(None):
        deferred = DeferredProvider(lambda: prepared.append(True) or object(), FixtureProvider, security=security)
    deferred.source_failure.connect(failures.append)
    deferred.start()
    until(app, lambda: bool(failures))
    assert not prepared and deferred._identity is None
    assert "AuthenticationError" in failures[0]
    deferred.stop()


def test_identity_revoked_during_preparation_cannot_create_delegate(app, security):
    identity = security.grant_identity("service:fixture-researcher", ["researcher"], ["*"])
    release, entered = threading.Event(), threading.Event()
    created, failures = [], []

    def prepare():
        entered.set()
        assert release.wait(3)
        return object()

    deferred = DeferredProvider(prepare, lambda service: created.append(service) or FixtureProvider(service),
                                security=security, principal=identity)
    deferred.source_failure.connect(failures.append)
    try:
        deferred.start()
        until(app, entered.is_set)
        security.revoke_identity(identity.subject)
        release.set()
        until(app, lambda: bool(failures))
        assert not created and not deferred.ready
    finally:
        release.set()
        deferred.stop()
