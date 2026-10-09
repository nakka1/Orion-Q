"""The live Qt loop must settle at fractional ultrawide canvas sizes."""
from __future__ import annotations

import gc
import os
import time

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import pytest
pytest.importorskip('PySide6.QtWidgets')
from PySide6.QtCore import QEvent, QObject, QTimer, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QWidget

from quantum_autopilot.console import app as console_app, theme
from quantum_autopilot.console.stage import ResponsiveStage


@pytest.fixture
def app():
    instance = QApplication.instance() or QApplication([])
    theme.set_theme('dark')
    theme.install(instance)
    return instance


class CanvasResizes(QObject):
    def __init__(self, content):
        super().__init__(content)
        self.count = 0
        content.installEventFilter(self)

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.Resize:
            self.count += 1
        return False


@pytest.mark.parametrize('size', [(3096, 1219), (3088, 1211), (3440, 1358), (3440, 1406),
                                  (2999, 1259), (2191, 1253), (5120, 2137)])
def test_fractional_canvas_keeps_servicing_the_event_loop(app, size):
    from quantum_autopilot.console.screens import SplashScreen
    stage = ResponsiveStage(SplashScreen())
    counter = CanvasResizes(stage.content)
    stage.resize(*size)
    stage.show()
    beats = []
    timer = QTimer(); timer.setInterval(10); timer.timeout.connect(lambda: beats.append(time.monotonic()))
    timer.start()
    try:
        QTest.qWait(180)
        # Windows timer delivery can use a 15.6 ms clock quantum. This is a
        # liveness check; the resize count below detects the actual regression.
        assert len(beats) >= 4
        assert counter.count <= 8, 'The canvas is cycling between scrollbar sizes'
        assert not stage.horizontalScrollBar().isVisible()
        assert not stage.verticalScrollBar().isVisible()
        before = stage.content.size(), stage.transform(), counter.count
        QTest.qWait(120)
        assert (stage.content.size(), stage.transform(), counter.count) == before
    finally:
        timer.stop()
        stage.close()
        stage.deleteLater()


def test_small_window_can_scroll_both_axes_and_then_recover(app):
    stage = ResponsiveStage(QWidget())
    stage.resize(320, 240)
    stage.show()
    try:
        QTest.qWait(60)
        assert stage.horizontalScrollBar().isVisible() and stage.verticalScrollBar().isVisible()
        hbar, vbar = stage.horizontalScrollBar(), stage.verticalScrollBar()
        hbar.setValue(hbar.maximum()); vbar.setValue(vbar.maximum())
        assert hbar.value() > 0 and vbar.value() > 0
        stage.resize(3096, 1219)
        QTest.qWait(100)
        assert not hbar.isVisible() and not vbar.isVisible()
        assert hbar.value() == 0 and vbar.value() == 0
        # A native standalone QWidget can be constrained by WM_GETMINMAXINFO
        # at 200% DPI. Verify coverage of its actual viewport, rather than an
        # assumed 900-pixel canvas on a physically shorter logical monitor.
        mapped = stage.mapToScene(stage.viewport().rect()).boundingRect().toAlignedRect()
        assert stage.content.rect().adjusted(-1, -1, 1, 1).contains(mapped)
    finally:
        stage.close()
        stage.deleteLater()


@pytest.mark.parametrize('failing_phase', [None, 'theme', 'provider', 'window', 'show', 'start', 'exec', 'stop'])
def test_collector_covers_construction_and_is_restored_on_every_exit(app, monkeypatch, failing_phase):
    from quantum_autopilot.console.ui_gc import UiThreadCollector
    from quantum_autopilot import desktop_instance
    from types import SimpleNamespace
    events = []
    was_enabled = gc.isenabled()
    gc.enable()
    def phase(name):
        events.append(name)
        assert not gc.isenabled(), name
        if failing_phase == name:
            raise RuntimeError(name)
    class Provider:
        def start(self): phase('start')
        def stop(self): phase('stop')
    class Window:
        def __init__(self, provider, **kwargs):
            phase('window'); self.provider = provider; self.after_paint = None
        def centralWidget(self):
            return SimpleNamespace(first_frame_painted=SimpleNamespace(
                connect=lambda callback: setattr(self, 'after_paint', callback)))
        def isVisible(self): return True
    class Owner:
        is_primary = True
        closed = False
        def attach_qt_window(self, window): pass
        def close(self): self.closed = True
    owner = Owner()
    monkeypatch.setattr(desktop_instance, 'acquire_desktop_instance', lambda: owner)
    def provider(args):
        phase('provider'); return Provider()
    monkeypatch.setattr(console_app.theme, 'install', lambda _app: phase('theme'))
    monkeypatch.setattr(console_app, 'build_provider', provider)
    monkeypatch.setattr(console_app, 'ConsoleWindow', Window)
    from quantum_autopilot.console import display
    def show(window, **kwargs):
        phase('show')
        window.after_paint()
    monkeypatch.setattr(display, 'show_main_window', show)
    # Model the first painted frame directly so lifecycle failures propagate
    # synchronously here. Real Qt scheduling has separate startup coverage.
    monkeypatch.setattr(console_app.QTimer, 'singleShot', lambda *args: args[-1]())
    monkeypatch.setattr(app, 'exec', lambda: phase('exec') or 0)
    try:
        args = console_app.build_parser().parse_args([])
        if failing_phase:
            with pytest.raises(RuntimeError, match=failing_phase):
                console_app.run(args)
        else:
            assert console_app.run(args) == 0
            assert events == ['theme', 'provider', 'window', 'show', 'start', 'exec', 'stop']
        assert gc.isenabled()
        assert not any(item.active for item in app.findChildren(UiThreadCollector))
        assert owner.closed, 'Ownership must be released after every construction or cleanup failure'
    finally:
        if not was_enabled:
            gc.disable()


@pytest.mark.parametrize('text', ['3440x1440', '320x240', '7680x4320'])
def test_startup_diagnostic_accepts_bounded_client_sizes(text):
    from quantum_autopilot.console.startup_validation import parse_size
    assert parse_size(text) == tuple(int(item) for item in text.split('x'))


@pytest.mark.parametrize('text', ['abc', '3440', '3440x0', '-3440x1440', '99999x1440', '3440x1440x2'])
def test_startup_diagnostic_rejects_invalid_sizes(text):
    from quantum_autopilot.console.startup_validation import parse_size
    with pytest.raises(ValueError):
        parse_size(text)


def test_startup_diagnostic_never_defaults_to_operator_state(tmp_path, monkeypatch):
    from quantum_autopilot import desktop
    from quantum_autopilot.console.startup_validation import run_startup_test
    states = []
    def launch(args, *, startup_monitor):
        states.append(args.state_root)
        assert args.state_root.parent == tmp_path and not any(args.state_root.iterdir())
        startup_monitor.passed = True
        return 0
    monkeypatch.setattr(desktop, '_run_native', launch)
    monkeypatch.setattr(desktop, 'default_state_root', lambda: pytest.fail('Operator state must not be selected'))
    for _ in range(2):
        args = desktop.build_parser().parse_args(['--startup-test', str(tmp_path / 'report.json')])
        assert run_startup_test(args) == 0
    assert states[0] != states[1]


def test_startup_diagnostic_refuses_a_nonempty_state_root(tmp_path, monkeypatch):
    from quantum_autopilot import desktop
    from quantum_autopilot.console.startup_validation import run_startup_test
    state = tmp_path / 'operator'; state.mkdir()
    original = b'Existing journal data'
    (state / 'sandbox_journal.sqlite').write_bytes(original)
    monkeypatch.setattr(desktop, '_run_native', lambda *a, **k: pytest.fail('Must not launch'))
    args = desktop.build_parser().parse_args(['--startup-test', str(tmp_path / 'report.json'),
                                              '--state-root', str(state)])
    with pytest.raises(ValueError, match='empty, isolated'):
        run_startup_test(args)
    assert (state / 'sandbox_journal.sqlite').read_bytes() == original


@pytest.mark.parametrize('options', [['--startup-size', '3440x1440'],
    ['--ui', 'web', '--startup-test', 'report.json'],
    ['--startup-test', 'report.json', '--self-test', 'other.json'],
    ['--startup-test', 'report.json', '--synthetic', 'reference']])
def test_startup_cli_rejects_incompatible_modes(options):
    from quantum_autopilot.desktop import main
    with pytest.raises(SystemExit) as failure:
        main(options)
    assert failure.value.code == 2


def test_portable_package_requires_startup_checks_for_the_same_executable(tmp_path):
    import importlib.util
    from pathlib import Path
    path = Path(__file__).resolve().parents[1] / 'tools' / 'package_console_portable.py'
    spec = importlib.util.spec_from_file_location('startup_package_gate', path)
    packager = importlib.util.module_from_spec(spec); spec.loader.exec_module(packager)
    exe = tmp_path / 'ORION-Q.exe'; exe.write_bytes(b'candidate-executable')
    report = {'schema': 'orion.frozen-startup-check.v1', 'passed': True,
              'executable_sha256': packager.digest(exe),
              'cases': [{'case': key, 'passed': True} for key in
                        ('service-100', 'service-125', 'service-150', 'service-200', 'demo-100', 'demo-150')]}
    packager.validate_startup(report, exe)
    report['cases'][-1]['passed'] = False
    with pytest.raises(ValueError, match='startup'):
        packager.validate_startup(report, exe)
    report['cases'][-1]['passed'] = True
    exe.write_bytes(b'different-executable')
    with pytest.raises(ValueError, match='startup'):
        packager.validate_startup(report, exe)
