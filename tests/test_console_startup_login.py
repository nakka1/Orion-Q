"""The diagnostic waits for ordinary login/source entry without granting access.

Small Qt signal/control doubles exercise monitor sequencing only. Native
authentication itself is covered by the security and full login-flow suites.
"""
from __future__ import annotations

import os
import time
from types import SimpleNamespace

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import pytest

pytest.importorskip('PySide6.QtWidgets')
from PySide6.QtCore import QCoreApplication, QEvent, QObject, QRect, Qt, Signal  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from quantum_autopilot.console.startup_validation import StartupMonitor  # noqa: E402


class _Control:
    def __init__(self, action=lambda: None, *, visible=True, enabled=True):
        self.action, self.visible, self.enabled = action, visible, enabled
        self.clicks = 0

    def isVisible(self):
        return self.visible

    def isEnabled(self):
        return self.enabled

    def click(self):
        self.clicks += 1
        if self.enabled:
            self.action()


class _LoginEvents(QObject):
    succeeded = Signal(object)
    failed = Signal(str)
    active = False


class _Provider(QObject):
    snapshot_changed = Signal(object)
    error = Signal(str)
    busy = False


class _Window:
    """Control surface events, without a security runtime or provider dispatch."""
    def __init__(self, app):
        self.app = app
        self.canvas = QObject()
        self.provider = _Provider(self.canvas)
        self._login_controller = _LoginEvents(self.canvas)
        self._login_verified = False
        self.current_screen = 'boot'
        self.screens = {}
        self.splash = SimpleNamespace(loading=SimpleNamespace(state='loading'))
        self.boot = SimpleNamespace(
            source_rows={'display-fixture': _Control()},
            os_session_button=_Control(self._request_native),
            open_button=_Control(self._open_fixture, enabled=False),
        )

    def _request_native(self):
        self._login_controller.active = True
        self.current_screen = 'splash'

    def _open_fixture(self):
        self.current_screen = 'topology'

    def active_source_key(self):
        return 'display-fixture'

    def geometry(self):
        return QRect(0, 0, 1200, 800)

    frameGeometry = geometry

    def windowFlags(self):
        return Qt.WindowType.FramelessWindowHint

    def isMaximized(self):
        return False

    def isFullScreen(self):
        return False

    def screen(self):
        return self.app.primaryScreen()


@pytest.fixture(scope='module')
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def diagnostic(app, tmp_path):
    window = _Window(app)
    monitor = StartupMonitor(tmp_path / 'startup.json')
    monitor.attach(app, window)
    monitor.driver.stop()
    monitor._snapshot(None)
    calls = []
    monitor.steps = iter([('open-workspace', monitor._open_workspace),
                          ('page-fixture', lambda: calls.append('navigation'))])
    yield monitor, window, calls
    monitor._stop_observing_login()
    monitor.heartbeat.stop()
    monitor.driver.stop()
    monitor.deadline.stop()
    window.canvas.removeEventFilter(monitor)
    window.provider.snapshot_changed.disconnect(monitor._snapshot)
    window.provider.error.disconnect(monitor.errors.append)
    monitor.setParent(None)
    monitor.deleteLater()
    window.canvas.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    app.processEvents()


def test_pending_native_login_submits_once_and_keeps_navigation_pending(diagnostic):
    monitor, window, calls = diagnostic
    for _ in range(4):
        monitor._step()
    assert window.boot.os_session_button.clicks == 1
    assert window.boot.open_button.clicks == 0
    assert window.boot.source_rows['display-fixture'].clicks == 1
    assert monitor.cases == [] and not calls and not monitor.steps_complete
    assert monitor._pending_step[0] == 'open-workspace'
    assert window._login_controller.active
    assert not window._login_verified  # The monitor never sets authority flags.


def test_controller_success_still_waits_for_topology_commit(diagnostic):
    monitor, window, calls = diagnostic
    monitor._step()
    window._login_controller.active = False
    window._login_controller.succeeded.emit(object())
    monitor._step()
    assert monitor.cases == [] and not calls
    assert window.current_screen == 'splash'
    window.current_screen = 'topology'
    monitor._step()
    assert monitor.cases == [{'case': 'open-workspace', 'passed': True}]
    assert monitor._login_observed is None and not calls
    monitor._step()
    monitor._step()
    assert calls == ['navigation'] and monitor.steps_complete
    assert not window._login_verified


def test_topology_without_controller_confirmation_cannot_pass(diagnostic):
    monitor, window, calls = diagnostic
    monitor._step()
    window._login_controller.active = False
    window.current_screen = 'topology'
    monitor._step()
    assert monitor.cases == [] and not calls and not monitor.steps_complete


def test_login_denial_stops_later_steps_and_does_not_copy_backend_text(diagnostic):
    monitor, window, calls = diagnostic
    monitor._step()
    window._login_controller.active = False
    window.current_screen = 'boot'
    window._login_controller.failed.emit('Sensitive text that must not enter this report')
    for _ in range(3):
        monitor._step()
    assert len(monitor.cases) == 1 and not monitor.cases[0]['passed']
    assert 'confirmation was denied' in monitor.cases[0]['detail']
    assert 'Sensitive' not in monitor.cases[0]['detail']
    assert monitor._entry_failed and not monitor.steps_complete
    assert not monitor.driver.isActive() and not calls
    assert monitor._login_observed is None and not window._login_verified


def test_source_error_after_login_stops_navigation(diagnostic):
    monitor, window, calls = diagnostic
    monitor._step()
    window._login_controller.active = False
    window._login_controller.succeeded.emit(object())
    window.splash.loading.state = 'error'
    monitor._step()
    assert monitor._entry_failed and not calls
    assert monitor.cases[0]['passed'] is False
    assert 'selected source did not become ready' in monitor.cases[0]['detail']


@pytest.mark.parametrize('missing_button', (False, True))
def test_developer_fixture_uses_existing_enabled_workspace_button(diagnostic, missing_button):
    monitor, window, calls = diagnostic
    if missing_button:
        del window.boot.os_session_button
    else:
        window.boot.os_session_button.visible = False
    window.boot.open_button.enabled = True
    monitor._step()
    monitor._step()
    assert window.boot.open_button.clicks == 1 and not window._login_verified
    assert monitor.cases[0] == {'case': 'open-workspace', 'passed': True}
    assert calls == ['navigation']
    assert monitor._login_observed is None


@pytest.mark.parametrize('native_visible', (False, True))
def test_disabled_entry_never_falls_through_to_fixture_or_grants_access(diagnostic, native_visible):
    monitor, window, calls = diagnostic
    window.boot.os_session_button.visible = native_visible
    window.boot.os_session_button.enabled = False
    monitor._step()
    monitor._step()
    assert monitor._entry_failed and not calls
    assert monitor.cases[0]['passed'] is False
    assert not window._login_verified and window.current_screen == 'boot'
    assert window.boot.open_button.clicks == window.boot.os_session_button.clicks == 0


def test_provider_busy_keeps_the_current_entry_step_pending(diagnostic):
    monitor, window, calls = diagnostic
    monitor._step()
    window._login_controller.active = False
    window._login_controller.succeeded.emit(object())
    window.current_screen = 'topology'
    window.provider.busy = True
    monitor._step()
    assert monitor._pending_step[0] == 'open-workspace' and not monitor.cases and not calls
    window.provider.busy = False
    monitor._step()
    assert monitor.cases == [{'case': 'open-workspace', 'passed': True}]


@pytest.mark.parametrize('health', ('unavailable', 'starting'))
def test_preparation_placeholder_does_not_start_interaction_budget(diagnostic, health):
    monitor, window, calls = diagnostic
    monitor.first_snapshot_s = None
    monitor.deadline.start(monitor.cold_start_limit_ms)
    monitor.began = time.monotonic() - 5
    placeholder = SimpleNamespace(capabilities=SimpleNamespace(health=health))
    monitor._snapshot(placeholder)
    monitor._step()
    assert monitor.first_snapshot_s is None
    assert 29000 < monitor.deadline.remainingTime() <= 30000
    assert monitor.cases == [] and not calls and window.boot.os_session_button.clicks == 0
    monitor._snapshot(SimpleNamespace(capabilities=SimpleNamespace(health='ready')))
    assert monitor.first_snapshot_s >= 5
    assert 15000 < monitor.deadline.remainingTime() <= 16000
