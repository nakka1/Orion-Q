"""Cold acquisition cannot consume the bounded startup interaction window."""
import os
import time
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QObject, QRect, Qt, Signal
from PySide6.QtWidgets import QApplication
from quantum_autopilot.console.startup_validation import StartupMonitor


class Provider(QObject):
    snapshot_changed=Signal(object)
    error=Signal(str)
    busy=False


class Window:
    """No paint tree: these tests exercise only monitor acquisition deadlines."""
    def __init__(self, app):
        self.canvas=QObject()
        self.provider=Provider()
        self.current_screen='boot'
        self.screens={}
        self.app=app
    def geometry(self): return QRect(0,0,1200,800)
    frameGeometry=geometry
    def windowFlags(self): return Qt.WindowType.FramelessWindowHint
    def isMaximized(self): return False
    def isFullScreen(self): return False
    def screen(self): return self.app.primaryScreen()


def dispose(app, window, monitor):
    monitor.heartbeat.stop(); monitor.driver.stop(); monitor.deadline.stop()
    window.provider.snapshot_changed.disconnect(monitor._snapshot)
    window.provider.error.disconnect(monitor.errors.append)
    window.canvas.removeEventFilter(monitor)
    monitor.setParent(None)
    monitor.deleteLater(); window.canvas.deleteLater(); window.provider.deleteLater()
    QCoreApplication.sendPostedEvents(None,QEvent.Type.DeferredDelete)
    app.processEvents()


@pytest.mark.parametrize('loading_ms',[5500,10500,29000])
def test_full_interaction_window_keeps_cold_load_heartbeat_samples(tmp_path, loading_ms):
    app=QApplication.instance() or QApplication([])
    window=Window(app)
    monitor=StartupMonitor(tmp_path/'report.json',duration_ms=16000)
    monitor.attach(app,window)
    try:
        assert 29000 < monitor.deadline.remainingTime() <= 30000
        monitor.began=time.monotonic()-loading_ms/1000
        monitor.gaps=[20.0,150.0]
        previous=monitor.previous=time.monotonic()-.1
        window.provider.snapshot_changed.emit(None)
        assert 15000 < monitor.deadline.remainingTime() <= 16000
        assert monitor.gaps==[20.0,150.0] and monitor.previous==previous
        assert monitor.first_snapshot_s>=loading_ms/1000 and monitor.snapshots==1
        window.provider.snapshot_changed.emit(None)
        assert monitor.snapshots==2 and not monitor.errors
    finally:
        dispose(app,window,monitor)


def test_late_acquisition_does_not_restart_the_deadline_or_approve_startup(tmp_path):
    app=QApplication.instance() or QApplication([])
    window=Window(app)
    monitor=StartupMonitor(tmp_path/'report.json')
    monitor.attach(app,window)
    try:
        monitor.began=time.monotonic()-31
        window.provider.snapshot_changed.emit(None)
        assert monitor.errors and monitor.deadline.remainingTime()>29000
        assert not monitor.passed
    finally:
        dispose(app,window,monitor)
