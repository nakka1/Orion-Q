"""Bounded diagnostic over the ordinary app.run startup and its live Qt loop.

It uses a fresh journal, receives the normal provider's snapshots, then operates
navigation and layout controls. It never generates measurements or commands a
node. An external process timeout is required for an unresponsive Qt loop.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from PySide6.QtCore import QEvent, QObject, QTimer, Qt
from PySide6.QtWidgets import QApplication


def parse_size(value):
    """An explicit diagnostic client size, expressed in Qt logical pixels."""
    try:
        width, height = (int(item) for item in value.lower().split('x'))
    except (ValueError, AttributeError):
        raise ValueError('startup size must be WIDTHxHEIGHT') from None
    if not (320 <= width <= 7680 and 240 <= height <= 4320):
        raise ValueError('startup size must be between 320x240 and 7680x4320')
    return width, height


class StartupMonitor(QObject):
    def __init__(self, report_path: Path, *, size=None, duration_ms=16000, cold_start_limit_ms=30000):
        super().__init__()
        self.report_path = Path(report_path)
        self.size = size
        self.duration_ms = duration_ms
        self.cold_start_limit_ms = cold_start_limit_ms
        self.cases, self.errors, self.gaps = [], [], []
        self.canvas_resizes = self.snapshots = 0
        self.first_snapshot_s = None
        self.previous = None
        self.running_steps = False
        self.steps_complete = False
        self.passed = False
        self._pending_step = None
        self._entry_requested = False
        self._entry_failed = False
        self._login_denied = False
        self._login_confirmed = False
        self._login_observed = None

    def attach(self, app, window):
        self.setParent(app)
        self.app, self.window = app, window
        self.began = time.monotonic()
        self.original = window.geometry()
        self.initial_page = window.current_screen
        self.initial_flags = int(window.windowFlags())
        self.initial_expanded = window.isMaximized() or window.isFullScreen()
        self.screen = window.screen()
        self.initial_geometry = window.geometry().getRect()
        self.initial_frame = window.frameGeometry().getRect()
        window.canvas.installEventFilter(self)
        window.provider.snapshot_changed.connect(self._snapshot)
        window.provider.error.connect(self.errors.append)
        self.heartbeat = QTimer(self)
        self.heartbeat.setTimerType(Qt.TimerType.PreciseTimer)
        self.heartbeat.setInterval(20)
        self.heartbeat.timeout.connect(self._beat)
        self.heartbeat.start()
        self.driver = QTimer(self)
        self.driver.setInterval(400)
        self.driver.timeout.connect(self._step)
        self.driver.start()
        self.deadline = QTimer(self)
        self.deadline.setTimerType(Qt.TimerType.PreciseTimer)
        self.deadline.setSingleShot(True)
        self.deadline.timeout.connect(self.finish)
        # Collect responsiveness from show(), including cold loading. Allow a
        # separately bounded acquisition startup, then the complete interaction
        # window; otherwise larger topologies consume the navigation budget.
        self.deadline.start(self.cold_start_limit_ms)
        self.steps = iter([
            ('open-workspace', self._open_workspace),
            *[(f'page-{key}', lambda key=key: self._navigate(key)) for key in window.screens],
            ('compact-layout', lambda: self._resize(320, 240)),
            ('ultrawide-layout', lambda: self._resize(*(self.size or self.original.size().toTuple()))),
            ('maximize', self._maximize), ('restore', self._restore),
            ('terminal-close', lambda: self._terminal(False)),
            ('terminal-open', lambda: self._terminal(True)),
            ('original-layout', self._original_layout),
        ])

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.Resize:
            self.canvas_resizes += 1
        return False

    def _snapshot(self, value):
        self.snapshots += 1
        capabilities = getattr(value, 'capabilities', None)
        if getattr(capabilities, 'health', None) in {'unavailable', 'starting'}:
            return
        if self.first_snapshot_s is None:
            self.first_snapshot_s = time.monotonic() - self.began
            if self.first_snapshot_s * 1000 > self.cold_start_limit_ms:
                self.errors.append('First provider snapshot exceeded the cold-start limit')
            else:
                self.deadline.start(self.duration_ms)

    def _beat(self):
        now = time.monotonic()
        if self.previous is not None:
            self.gaps.append((now - self.previous) * 1000)
        self.previous = now

    def _step(self):
        if self._entry_failed or self.first_snapshot_s is None or self.window.provider.busy:
            return
        self.running_steps = True
        if self._pending_step is None:
            try:
                self._pending_step = next(self.steps)
            except StopIteration:
                self.driver.stop()
                self.steps_complete = True
                return
        name, action = self._pending_step
        try:
            result = action()
        except Exception as exc:
            self.cases.append({'case': name, 'passed': False, 'detail': f'{type(exc).__name__}: {exc}'})
            self._pending_step = None
            if name == 'open-workspace':
                # Navigation cannot be tested after authentication or source
                # entry failed; stopping this driver never changes authority.
                self._entry_failed = True
                self.driver.stop()
        else:
            if result is False:
                return
            self.cases.append({'case': name, 'passed': True})
            self._pending_step = None

    def _native_login_failed(self, _message):
        self._login_denied = True

    def _native_login_succeeded(self, _identity):
        self._login_confirmed = True

    def _observe_login(self, controller):
        self._login_observed = controller
        controller.failed.connect(self._native_login_failed)
        controller.succeeded.connect(self._native_login_succeeded)

    def _stop_observing_login(self):
        controller, self._login_observed = self._login_observed, None
        if controller is not None:
            controller.failed.disconnect(self._native_login_failed)
            controller.succeeded.disconnect(self._native_login_succeeded)

    def _open_workspace(self):
        window = self.window
        if not self._entry_requested:
            if window.current_screen != 'boot':
                raise AssertionError('Ordinary startup did not reach the source-selection screen')
            key = window.active_source_key()
            window.boot.source_rows[key].click()
            session_button = getattr(window.boot, 'os_session_button', None)
            if session_button is not None and session_button.isVisible():
                if not session_button.isEnabled():
                    raise AssertionError('Current Windows session confirmation is unavailable')
                controller = getattr(window, '_login_controller', None)
                if controller is None:
                    raise AssertionError('Native login controller is unavailable')
                self._observe_login(controller)
                self._entry_requested = True
                session_button.click()
            else:
                # Explicit developer UI fixtures retain their ordinary button
                # path. An unavailable/anonymous form cannot be opened here.
                if not window.boot.open_button.isEnabled():
                    raise AssertionError('The workspace entry button is unavailable')
                self._entry_requested = True
                window.boot.open_button.click()
        if self._login_denied:
            self._stop_observing_login()
            raise AssertionError('Native Windows session confirmation was denied')
        controller = self._login_observed
        if controller is not None and (controller.active or not self._login_confirmed):
            return False
        if window.current_screen == 'topology':
            self._stop_observing_login()
            return True
        if window.current_screen == 'splash':
            status = getattr(getattr(getattr(window, 'splash', None), 'loading', None), 'state', None)
            if status == 'error':
                self._stop_observing_login()
                raise AssertionError('The selected source did not become ready')
            return False
        self._stop_observing_login()
        raise AssertionError('The workspace button did not open Topology')

    def _navigate(self, key):
        self.window.rail.buttons[key].click()
        if self.window.current_screen != key:
            raise AssertionError(f'Navigation did not reach {key}')

    def _resize(self, width, height):
        self.window.resize(width, height)
        if self.window.size().toTuple() != (width, height):
            raise AssertionError(f'Requested {width}x{height}, received {self.window.size().toTuple()}')

    def _maximize(self):
        self.window.window_controls.bar.maximize_button.click()
        if not self.window.isMaximized():
            raise AssertionError('Maximize button did not maximize the window')

    def _restore(self):
        self.window.window_controls.bar.maximize_button.click()
        if self.window.isMaximized() or self.window.isFullScreen():
            raise AssertionError('Restore button did not restore the window')

    def _terminal(self, visible):
        self.window.status.terminal_button.click()
        if self.window.terminal_workspace.terminal_visible != visible:
            raise AssertionError('The terminal toggle did not change visibility')

    def _original_layout(self):
        self.window.setGeometry(self.original)

    def finish(self):
        self.heartbeat.stop()
        self.driver.stop()
        self.deadline.stop()
        self._stop_observing_login()
        gaps = sorted(self.gaps)
        self.passed = bool(
            self.first_snapshot_s is not None and self.steps_complete and not self.errors
            and all(item['passed'] for item in self.cases)
            and len(gaps) >= 200 and max(gaps, default=0) < 3000
            and self.canvas_resizes < 200 and not self.initial_expanded
            and self.initial_flags & int(Qt.WindowType.FramelessWindowHint))
        report = {
            'schema': 'orion.console-startup.v1', 'passed': self.passed,
            'scope': 'ordinary app.run, native startup, real provider, isolated local journal; no physical actuation',
            'platform': QApplication.platformName(), 'elapsed_after_show_s': time.monotonic() - self.began,
            'first_snapshot_after_show_s': self.first_snapshot_s, 'snapshot_count': self.snapshots,
            'heartbeat_count': len(gaps), 'heartbeat_max_ms': max(gaps, default=0),
            'heartbeat_p95_ms': gaps[min(len(gaps) - 1, int(len(gaps) * .95))] if gaps else None,
            'canvas_resize_count': self.canvas_resizes, 'steps_complete': self.steps_complete,
            'initial_geometry': self.initial_geometry, 'initial_frame': self.initial_frame,
            'initial_expanded': self.initial_expanded, 'initial_page': self.initial_page,
            'monitor_geometry': self.screen.geometry().getRect(),
            'monitor_available': self.screen.availableGeometry().getRect(),
            'device_pixel_ratio': self.window.devicePixelRatioF(),
            'diagnostic_client_size': self.size, 'state_root': str(self.window.input_state_root),
            'source': self.window.active_source_key(), 'cases': self.cases, 'errors': self.errors,
            'limits': {'min_heartbeats': 200, 'max_gap_ms': 3000, 'max_canvas_resizes': 200,
                       'first_snapshot_max_ms': self.cold_start_limit_ms,
                       'interaction_window_after_first_snapshot_ms': self.duration_ms,
                       'external_timeout_required': True, 'physical_mouse_drag_verified': False},
        }
        self.report_path.parent.mkdir(parents=True, exist_ok=True)
        self.report_path.write_text(json.dumps(report, indent=2), encoding='utf-8')
        self.window.close()
        self.app.quit()


def run_startup_test(args):
    """Use the normal desktop entry, changing only state isolation and monitoring."""
    from tempfile import mkdtemp
    from quantum_autopilot.desktop import _run_native
    report_path = args.startup_test.resolve()
    report_path.parent.mkdir(parents=True, exist_ok=True)
    # An explicit state root is also required to be empty: diagnostics never
    # write to an operator's existing journal or demonstration workspace.
    if args.state_root is None:
        args.state_root = Path(mkdtemp(prefix='startup-state-', dir=report_path.parent))
    elif args.state_root.exists() and any(args.state_root.iterdir()):
        raise ValueError('startup diagnostic requires an empty, isolated --state-root')
    size = parse_size(args.startup_size) if args.startup_size else None
    monitor = StartupMonitor(report_path, size=size)
    code = _run_native(args, startup_monitor=monitor)
    return 0 if code == 0 and monitor.passed else 1
