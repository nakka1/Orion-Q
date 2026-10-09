"""Background reads must keep controls steady and serialize accepted commands."""
import os
import threading
import time

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QEvent, QObject, QPointF, Qt
from PySide6.QtGui import QEnterEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from quantum_autopilot.console import theme
from quantum_autopilot.console.service_provider import ServiceProvider
from quantum_autopilot.console.shell import ConsoleWindow
from tests.test_console_service_provider import FakeService


def settle(app, condition, *, timeout=5.0):
    deadline = time.monotonic() + timeout
    while not condition() and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(.005)
    app.processEvents()
    assert condition()


@pytest.fixture
def ready_window():
    app = QApplication.instance() or QApplication([])
    theme.install(app)
    provider = ServiceProvider(FakeService(), poll_ms=600000)
    window = ConsoleWindow(provider, skip_auth=True)
    window.provider.start()
    settle(app, lambda: window._snap is not None and not window.provider.busy)
    window.show_screen("topology")
    window.resize(1920, 1080)
    window.show()
    app.processEvents()
    yield app, provider, window
    window.close()
    window.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


class EnabledTrace(QObject):
    def __init__(self):
        super().__init__()
        self.changes = []

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.EnabledChange:
            self.changes.append(watched.isEnabled())
        return False


def block_next_read(provider, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    original = provider.build

    def blocked_build():
        entered.set()
        if not release.wait(5):
            raise TimeoutError("test did not release background read")
        return original()

    monkeypatch.setattr(provider, "build", blocked_build)
    provider.request_refresh()
    return entered, release


def test_background_read_keeps_buttons_enabled_and_navigation_available(ready_window, monkeypatch):
    app, provider, window = ready_window
    topology = window.screens["topology"]
    buttons = [topology.recalibrate, window.topbar.palette_button, window.topbar.source_button,
               window.rail.sign_out, *window.rail.buttons.values()]
    trace = EnabledTrace()
    for button in buttons:
        assert button.isEnabled()
        button.installEventFilter(trace)
    received = []
    window.provider.snapshot_changed.connect(received.append)
    entered, release = block_next_read(provider, monkeypatch)
    try:
        settle(app, entered.is_set)
        assert not window.provider.busy
        assert not window._busy
        assert all(button.isEnabled() for button in buttons)
        QTest.mouseClick(window.rail.buttons["telemetry"], Qt.MouseButton.LeftButton)
        assert window._current == "telemetry"
    finally:
        release.set()
    settle(app, lambda: bool(received) and provider._future is None)
    assert not trace.changes
    # Continue live polling: the fix must not simply stop updates.
    provider._timer.setInterval(30)
    settle(app, lambda: len(received) >= 4)
    provider._timer.stop()
    settle(app, lambda: provider._future is None)
    assert not trace.changes


def test_selection_during_background_read_is_authorized_without_becoming_busy(ready_window, monkeypatch):
    """The authenticated selection worker publishes without waiting for the blocked read."""
    app, provider, window = ready_window
    entered, release = block_next_read(provider, monkeypatch)
    busy = []
    window.provider.busy_changed.connect(busy.append)
    monkeypatch.setattr(provider, "select_node", lambda _node: pytest.fail("selection must not run a command"))
    try:
        settle(app, entered.is_set)
        window._inspect("bob")
        assert not window.provider.busy and not window._busy
        assert window.screens[window._current].isEnabled() and window.screens["node"].isEnabled()
        # Selection already uses an authenticated worker. Its queued Qt frame
        # is asynchronous even when every presentation input is cached.
        settle(app, lambda: window._snap.node.id == "bob" and window._current == "node", timeout=5.0)
        assert not window.provider.busy and not window._busy
        assert window._snap.node.id == "bob" and window._current == "node"
        assert window.screens["node"].isEnabled()
        with pytest.raises(ValueError, match="node unavailable"):
            provider.dispatch("select_node", "nobody")
    finally:
        release.set()
    # the read that was running finishes with the operator's selection, not the one it started with
    settle(app, lambda: provider._future is None)
    assert window._snap.node.id == "bob" and busy == []
    assert not window.screens["fpga"].flash.isEnabled()


def test_click_during_background_read_is_accepted_once_and_runs_after_the_read(ready_window, monkeypatch):
    app, provider, window = ready_window
    entered, release = block_next_read(provider, monkeypatch)
    calls = []

    def acknowledge(target):
        assert release.is_set()
        calls.append(target)

    monkeypatch.setattr(provider, "acknowledge", acknowledge)
    try:
        settle(app, entered.is_set)
        window._command("acknowledge", "bob")
        assert window.provider.busy and window._busy
        assert calls == []
        with pytest.raises(RuntimeError, match="current operation"):
            window.provider.dispatch("acknowledge", "alice")
        assert provider.dispatch("acknowledge", "alice") is False
    finally:
        release.set()
    settle(app, lambda: not window.provider.busy and provider._future is None)
    assert calls == ["bob"]
    assert window.screens["node"].isEnabled()
    assert not window.screens["fpga"].flash.isEnabled()


def test_failed_queued_command_releases_busy_and_does_not_confirm(ready_window, monkeypatch):
    app, provider, window = ready_window
    entered, release = block_next_read(provider, monkeypatch)
    errors = []
    provider.error.connect(errors.append)

    def fail(_target):
        raise ValueError("queued command failed")

    monkeypatch.setattr(provider, "acknowledge", fail)
    try:
        settle(app, entered.is_set)
        assert window.provider.dispatch("acknowledge", "bob") is True
        assert window.provider.busy
    finally:
        release.set()
    settle(app, lambda: not window.provider.busy and provider._future is None)
    assert errors == ["queued command failed"]


def test_stop_finishes_an_accepted_command_and_ignores_late_completion(ready_window, monkeypatch):
    app, provider, window = ready_window
    entered, release = block_next_read(provider, monkeypatch)
    calls, snapshots = [], []
    provider.snapshot_changed.connect(snapshots.append)
    monkeypatch.setattr(provider, "acknowledge", lambda value: calls.append(value))
    try:
        settle(app, entered.is_set)
        assert provider.dispatch("acknowledge", "bob") is True
    finally:
        release.set()
    provider.stop()
    app.processEvents()
    assert calls == ["bob"]
    assert snapshots == []
    assert provider._executor is None
    assert provider._future is None


def test_initial_loading_still_refuses_commands_before_a_snapshot(monkeypatch):
    app = QApplication.instance() or QApplication([])
    provider = ServiceProvider(FakeService(), poll_ms=600000)
    entered, release = threading.Event(), threading.Event()
    original = provider.build

    def blocked_build():
        entered.set()
        assert release.wait(5)
        return original()

    monkeypatch.setattr(provider, "build", blocked_build)
    busy = []
    provider.busy_changed.connect(busy.append)
    provider.start()
    try:
        settle(app, entered.is_set)
        assert busy == [True]
        assert provider.dispatch("select_node", "bob") is False
    finally:
        release.set()
    settle(app, lambda: provider._future is None)
    assert busy == [True, False]
    provider.stop()


def test_command_accepted_from_a_background_snapshot_keeps_its_busy_state(ready_window, monkeypatch):
    app, provider, window = ready_window
    entered, release = threading.Event(), threading.Event()
    accepted, calls = [], []

    def blocked(target):
        entered.set()
        assert release.wait(5)
        calls.append(target)

    def on_snapshot(_snapshot):
        if not accepted:
            accepted.append(window.provider.dispatch("acknowledge", "bob"))

    monkeypatch.setattr(provider, "acknowledge", blocked)
    window.provider.snapshot_changed.connect(on_snapshot)
    provider.request_refresh()
    try:
        settle(app, entered.is_set)
        assert accepted == [True]
        assert window.provider.busy and window._busy
        with pytest.raises(RuntimeError, match="current operation"):
            window.provider.dispatch("acknowledge", "alice")
    finally:
        release.set()
    settle(app, lambda: not window.provider.busy and calls == ["bob"])
    assert accepted == [True]


@pytest.mark.parametrize("filled", [False, True])
def test_hover_and_focus_do_not_replace_the_button_stylesheet(filled):
    from quantum_autopilot.console.widgets.prims import Btn

    app = QApplication.instance() or QApplication([])
    button = Btn("STEADY CONTROL", filled=filled)
    button.show()
    app.processEvents()
    stylesheet, size = button.styleSheet(), button.size()
    try:
        for _ in range(4):
            # Repeated enter/leave events previously reinstalled the style.
            button.event(QEnterEvent(QPointF(), QPointF(), QPointF()))
            button.setFocus()
            app.processEvents()
            assert button.styleSheet() == stylesheet
            assert button.size() == size
            button.event(QEvent(QEvent.Type.Leave))
            button.clearFocus()
            app.processEvents()
            assert button.styleSheet() == stylesheet
            assert button.size() == size
    finally:
        button.close()


@pytest.mark.parametrize("control", ["Tab", "Toggle"])
def test_repeated_selection_does_not_repolish_an_unchanged_control(control):
    from quantum_autopilot.console.widgets import prims

    app = QApplication.instance() or QApplication([])
    button = getattr(prims, control)("MODE")
    button.show()
    app.processEvents()

    class StyleTrace(QObject):
        count = 0

        def eventFilter(self, watched, event):
            if event.type() == QEvent.Type.StyleChange:
                self.count += 1
            return False

    trace = StyleTrace()
    button.installEventFilter(trace)
    initial = button.styleSheet()
    try:
        for _ in range(4):
            button.set_active(False)
            app.processEvents()
        assert trace.count == 0
        button.set_active(True)
        app.processEvents()
        assert button.styleSheet() != initial
        active_count = trace.count
        for _ in range(4):
            button.set_active(True)
            app.processEvents()
        assert trace.count == active_count
        button.set_active(False)
        app.processEvents()
        assert button.styleSheet() == initial
        assert trace.count > active_count
    finally:
        button.removeEventFilter(trace)
        button.close()
