"""Sign out must not wait for workers needing the Qt event loop.

Events force the real executor/Qt dependency; bounded waits keep the pre-fix
regression from hanging pytest. No hardware, database, or scientific data are
substituted by these lifecycle fixtures.
"""
from __future__ import annotations

import os
import threading
import time

import pytest

pytest.importorskip("PySide6.QtWidgets")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from quantum_autopilot.console.service_provider import ServiceProvider  # noqa: E402
from tests.test_console_service_provider import FakeService  # noqa: E402

pytestmark = pytest.mark.unit


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def drain(app, predicate, timeout=10):
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(.005)
    assert predicate()


def test_sign_out_does_not_block_a_worker_waiting_for_qt(app, monkeypatch):
    provider = ServiceProvider(FakeService(), poll_ms=60_000)
    entered, release, completed = threading.Event(), threading.Event(), threading.Event()
    trace = []

    def accepted_command():
        entered.set()
        # Represents a worker needing a queued Qt callback to continue. The
        # timeout is the safety fuse that makes the original deadlock testable.
        assert release.wait(2), "Qt callback could not execute during stop()"
        trace.append("accepted_command_completed")
        completed.set()

    monkeypatch.setattr(provider, "request_refresh", lambda: None)
    provider.start()
    assert provider._schedule(accepted_command)
    assert entered.wait(2)
    QTimer.singleShot(0, release.set)
    started = time.perf_counter()
    try:
        provider.stop()
        elapsed = time.perf_counter() - started
        assert elapsed < .5, "Sign out synchronously waited for its worker"
        assert provider._executor is None
        assert not provider._timer.isActive()
        assert not provider._live_timer.isActive()
        drain(app, completed.is_set)
        drain(app, lambda: not provider.stopping)
        assert trace == ["accepted_command_completed"]
        assert provider._future is None
    finally:
        release.set()
        provider.stop()
        if hasattr(provider, "stopping"):
            drain(app, lambda: not provider.stopping)
        provider.deleteLater()
        app.processEvents()


class CleanupService(FakeService):
    def __init__(self, trace, *, close_failure=False):
        from types import SimpleNamespace
        super().__init__()
        self.trace = trace
        self.close_failure = close_failure
        self.controller_lock = threading.Lock()
        self._network_engine = SimpleNamespace(
            fpga=SimpleNamespace(close=self.close_fpga),
            close_satellite_receivers=lambda: trace.append("receivers_closed"))

    def invalidate_policy_preparation(self):
        with self.controller_lock:
            self.trace.append("policy_invalidated")

    def close_fpga(self):
        if self.close_failure:
            raise ValueError("fixture resource release failure")
        self.trace.append("fpga_closed")

    def _release_controller_io_resources(self):
        self.trace.append("io_released")


def test_stop_drains_accepted_command_before_invalidating_and_releasing_devices(app, monkeypatch):
    trace = []
    service = CleanupService(trace)
    provider = ServiceProvider(service)
    entered, release = threading.Event(), threading.Event()

    def accepted():
        with service.controller_lock:
            entered.set()
            assert release.wait(2)
            trace.append("accepted_command_completed")

    monkeypatch.setattr(provider, "request_refresh", lambda: None)
    provider.start()
    assert provider._schedule(accepted)
    assert entered.wait(2)
    try:
        provider.stop()
        assert provider.stopping
        assert trace == []
        # Even a late UI action cannot reopen acquisition during device drain.
        with pytest.raises(RuntimeError, match="releasing"):
            provider.dispatch("acknowledge", "bob")
        with pytest.raises(RuntimeError, match="previous session"):
            provider.start()
        assert provider._schedule(lambda: trace.append("unaccepted_work")) is False
        provider.stop()  # Idempotent while the accepted operation is pending.
        release.set()
        drain(app, lambda: not provider.stopping)
        assert trace == ["accepted_command_completed", "policy_invalidated",
                         "fpga_closed", "receivers_closed", "io_released"]
        assert provider._shutdown_error is None
    finally:
        release.set()
        provider.stop()
        drain(app, lambda: not provider.stopping)
        provider.deleteLater()
        app.processEvents()


def test_busy_listener_reentrant_stop_cannot_create_two_cleanup_threads(app, monkeypatch):
    trace = []
    provider = ServiceProvider(CleanupService(trace))
    monkeypatch.setattr(provider, "request_refresh", lambda: None)
    provider.start()
    provider._foreground_busy = True
    provider.busy_changed.connect(lambda busy: provider.stop() if not busy else None)
    epoch = provider._publication_epoch
    try:
        provider.stop()
        drain(app, lambda: not provider.stopping)
        assert provider._publication_epoch == epoch + 1
        assert trace.count("policy_invalidated") == 1
        assert trace.count("fpga_closed") == 1
    finally:
        provider.deleteLater()
        app.processEvents()


def test_teardown_failure_blocks_restart_until_resources_are_released(app, monkeypatch):
    trace = []
    service = CleanupService(trace, close_failure=True)
    provider = ServiceProvider(service)
    monkeypatch.setattr(provider, "request_refresh", lambda: None)
    provider.start()
    try:
        provider.stop()
        drain(app, lambda: not provider.stopping)
        assert isinstance(provider._shutdown_error, ValueError)
        with pytest.raises(RuntimeError, match="resource release failed"):
            provider.start()
        with pytest.raises(RuntimeError, match="releasing resources"):
            provider.dispatch("acknowledge", "bob")
        assert "io_released" not in trace
        service.close_failure = False
        provider.stop()
        drain(app, lambda: not provider.stopping)
        assert provider._shutdown_error is None
        provider.start()
        assert provider._executor is not None
    finally:
        service.close_failure = False
        provider.stop()
        drain(app, lambda: not provider.stopping)
        provider.deleteLater()
        app.processEvents()


def paused_source(app, monkeypatch, service=None):
    provider = ServiceProvider(service or FakeService())
    monkeypatch.setattr(provider, "request_refresh", lambda: None)
    entered, release = threading.Event(), threading.Event()
    def read():
        entered.set()
        assert release.wait(3)
    provider.start()
    assert provider._schedule(read, background=True)
    assert entered.wait(2)
    return provider, release


def test_source_switch_starts_only_latest_candidate_after_all_drains(app, monkeypatch):
    from quantum_autopilot.console.source_switch import SwitchableProvider
    from tests.test_prepared_source import FixtureProvider
    old, release = paused_source(app, monkeypatch)
    plug = SwitchableProvider(old)
    plug.running = True
    first, latest = FixtureProvider(object()), FixtureProvider(object())
    try:
        plug.switch(lambda: first)
        assert old.stopping and not plug.running and first.starts == 0
        plug.switch(lambda: latest)
        assert plug.active is latest and latest.starts == first.starts == 0
        release.set()
        drain(app, lambda: not old.stopping and plug.running)
        assert first.starts == 0 and latest.starts == 1
        assert not plug._retiring_sources and plug._pending_start is None
    finally:
        release.set()
        plug.stop()
        drain(app, lambda: not old.stopping)
        plug.deleteLater()
        app.processEvents()


def test_stop_cancels_queued_source_start_without_cancelling_old_accepted_work(app, monkeypatch):
    from quantum_autopilot.console.source_switch import SwitchableProvider
    from tests.test_prepared_source import FixtureProvider
    old, release = paused_source(app, monkeypatch)
    plug = SwitchableProvider(old)
    plug.running = True
    candidate = FixtureProvider(object())
    try:
        plug.switch(lambda: candidate)
        assert plug._pending_start is not None
        plug.stop()
        assert plug._pending_start is None and not plug.running
        release.set()
        drain(app, lambda: not old.stopping)
        app.processEvents()
        assert candidate.starts == 0
    finally:
        release.set()
        plug.stop()
        drain(app, lambda: not old.stopping)
        plug.deleteLater()
        app.processEvents()


def test_source_swap_fails_closed_after_old_resource_release_failure(app, monkeypatch):
    from quantum_autopilot.console.source_switch import SwitchableProvider
    from tests.test_prepared_source import FixtureProvider
    old, release = paused_source(app, monkeypatch, CleanupService([], close_failure=True))
    plug = SwitchableProvider(old)
    plug.running = True
    errors = []
    plug.error.connect(errors.append)
    candidate = FixtureProvider(object())
    try:
        plug.switch(lambda: candidate)
        release.set()
        drain(app, lambda: not old.stopping and bool(errors))
        assert candidate.starts == 0 and not plug.running
        assert old.shutdown_error is not None
        with pytest.raises(RuntimeError, match="could not release"):
            plug.start()
    finally:
        release.set()
        old.service.close_failure = False
        old.stop()
        drain(app, lambda: not old.stopping)
        plug.stop()
        plug.deleteLater()
        app.processEvents()


def test_start_failure_rolls_back_busy_and_the_same_candidate(app):
    from quantum_autopilot.console.source_switch import SwitchableProvider
    from tests.test_prepared_source import FixtureProvider
    class FailingStart(FixtureProvider):
        def start(self):
            self.starts += 1
            self.busy_changed.emit(True)
            raise ValueError("fixture startup failure")
    candidate = FailingStart(object())
    plug = SwitchableProvider(candidate)
    try:
        with pytest.raises(ValueError, match="startup failure"):
            plug.start()
        assert not plug.running and not plug.busy
        assert candidate.starts == candidate.stops == 1
        assert not plug._retiring_sources
    finally:
        plug.stop()
        plug.deleteLater()
        app.processEvents()


def test_deferred_wrapper_preserves_delegate_until_resource_drain(app, monkeypatch):
    import shiboken6
    from quantum_autopilot.console.prepared_source import DeferredProvider
    from quantum_autopilot.console.source_switch import SwitchableProvider
    from tests.test_prepared_source import FixtureProvider
    delegate, release = paused_source(app, monkeypatch)
    wrapper = DeferredProvider(lambda: object(), lambda _: delegate)
    # Attach the already prepared real provider on its owning Qt thread.
    wrapper._delegate = delegate
    wrapper.running = wrapper._ready = True
    delegate.setParent(wrapper)
    plug = SwitchableProvider(wrapper)
    plug.running = True
    candidate = FixtureProvider(object())
    try:
        plug.switch(lambda: candidate)
        assert wrapper.stopping and delegate.stopping and candidate.starts == 0
        app.processEvents()
        assert shiboken6.isValid(delegate), "QObject retired while accepted work still uses it"
        release.set()
        drain(app, lambda: not wrapper.stopping and plug.running)
        assert candidate.starts == 1 and wrapper.delegate is None
        drain(app, lambda: not wrapper._retired_delegates)
    finally:
        release.set()
        plug.stop()
        drain(app, lambda: not wrapper.stopping)
        plug.deleteLater()
        wrapper.deleteLater()
        app.processEvents()


def test_cancelled_deferred_preparation_releases_late_service_without_creating_qt(app):
    from quantum_autopilot.console.prepared_source import DeferredProvider
    from tests.test_prepared_source import FixtureProvider
    entered, release = threading.Event(), threading.Event()
    trace, created = [], []
    service = CleanupService(trace)
    def prepare():
        entered.set()
        assert release.wait(3)
        return service
    wrapper = DeferredProvider(prepare, lambda value: created.append(value) or FixtureProvider(value))
    try:
        wrapper.start()
        assert entered.wait(2)
        wrapper.stop()
        assert wrapper.stopping and not created and trace == []
        with pytest.raises(RuntimeError, match="releasing"):
            wrapper.start()
        release.set()
        drain(app, lambda: not wrapper.stopping)
        assert created == []
        assert trace == ["policy_invalidated", "fpga_closed", "receivers_closed", "io_released"]
        assert wrapper.shutdown_error is None
    finally:
        release.set()
        wrapper.stop()
        drain(app, lambda: not wrapper.stopping)
        wrapper.deleteLater()
        app.processEvents()


def test_reentrant_start_failure_cannot_stop_successor_source(app):
    from quantum_autopilot.console.source_switch import SwitchableProvider
    from tests.test_prepared_source import FixtureProvider
    class ReentrantStart(FixtureProvider):
        def start(self):
            self.starts += 1
            self.snapshot_changed.emit(self._snap)
            raise ValueError("old source failed after publishing")
    old, successor = ReentrantStart(object()), FixtureProvider(object())
    plug = SwitchableProvider(old)
    switched = []
    def choose_successor(_):
        if not switched:
            switched.append(True)
            plug.switch(lambda: successor)
    plug.snapshot_changed.connect(choose_successor)
    try:
        with pytest.raises(ValueError, match="old source failed"):
            plug.start()
        assert plug.active is successor and plug.running
        assert successor.starts == 1 and successor.stops == 0
        assert not plug._pending_start
    finally:
        plug.stop()
        plug.deleteLater()
        app.processEvents()


def test_failed_old_stop_releases_unstarted_candidate_and_prevents_unsafe_retry(app):
    from quantum_autopilot.console.source_switch import SwitchableProvider
    from tests.test_prepared_source import FixtureProvider
    class FailedStop(FixtureProvider):
        def stop(self):
            self.stops += 1
            raise ValueError("old source release failed")
    old, candidate = FailedStop(object()), FixtureProvider(object())
    plug = SwitchableProvider(old)
    try:
        plug.start()
        with pytest.raises(ValueError, match="old source release failed"):
            plug.switch(lambda: candidate)
        assert plug.active is old and not plug.running
        assert candidate.starts == 0 and candidate.stops == 1
        with pytest.raises(RuntimeError, match="could not release"):
            plug.start()
    finally:
        old.stop = lambda: None
        plug.stop()
        plug.deleteLater()
        app.processEvents()
