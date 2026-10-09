"""Continuous client-edge sizing of the complete institutional console."""
import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QPoint, QPointF, QRect, Qt, QTimer
from PySide6.QtGui import QMouseEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QWidget

from quantum_autopilot.console import theme
from quantum_autopilot.console.service_provider import ServiceProvider
from quantum_autopilot.console.shell import ConsoleWindow
from quantum_autopilot.qkd.dashboard_api import demo_service


@pytest.fixture(scope="module")
def app():
    instance = QApplication.instance() or QApplication([])
    theme.install(instance)
    return instance


@pytest.fixture(scope="module")
def service(tmp_path_factory):
    backend = demo_service(Path(__file__).resolve().parents[1],
                           tmp_path_factory.mktemp("resize-service"),
                           preset="brazil-institutions-v1", scenario="quiet")
    backend.network_step()
    return backend


def settle(app):
    for _ in range(3):
        app.processEvents()


@pytest.fixture
def window(app, service, tmp_path):
    provider = ServiceProvider(service, state_root=tmp_path, poll_ms=600000)
    provider.refresh()
    win = ConsoleWindow(provider, skip_auth=True, state_root=tmp_path)
    win.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
    win.resize(1280, 820)
    win._on_snapshot(provider.snapshot())
    win.show_screen("topology")
    win.show()
    settle(app)
    yield win
    win.close()
    win.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    settle(app)


def pointer(widget, kind, point, *, released=False):
    button = Qt.MouseButton.NoButton if kind == QEvent.Type.MouseMove else Qt.MouseButton.LeftButton
    buttons = Qt.MouseButton.NoButton if released else Qt.MouseButton.LeftButton
    QApplication.sendEvent(widget, QMouseEvent(kind, QPointF(widget.mapFromGlobal(point)), QPointF(point),
                          button, buttons, Qt.KeyboardModifier.NoModifier))


def direct_edges(window, monkeypatch):
    # Windows exercises the production branch; other QPA plugins exercise its
    # geometry fallback without entering a desktop window-manager mouse loop.
    if QApplication.platformName() != "windows":
        monkeypatch.setattr(window.window_controls, "start_system_resize", lambda _edges: False)


@pytest.mark.parametrize("index", range(8))
def test_continuous_edge_drag_releases_mouse_and_keeps_console_usable(window, app, monkeypatch, index):
    direct_edges(window, monkeypatch)
    grip = window.window_controls.grips[index]
    before = QRect(window.geometry())
    initial_links = window.screens["topology"].geo.drawn_link_keys()
    start = grip.mapToGlobal(grip.rect().center())
    ticks = []
    timer = QTimer(); timer.setInterval(5); timer.timeout.connect(lambda: ticks.append(True)); timer.start()
    try:
        pointer(grip, QEvent.Type.MouseButtonPress, start)
        for delta in (QPoint(8, 5), QPoint(26, 18), QPoint(-21, -13), QPoint(44, 27), QPoint(37, 29)):
            pointer(grip, QEvent.Type.MouseMove, start + delta)
            settle(app)
            QTest.qWait(8)
        pointer(grip, QEvent.Type.MouseButtonRelease, start + QPoint(37, 29), released=True)
        settle(app)
        after = QRect(window.geometry())
        assert after != before
        assert QWidget.mouseGrabber() is not grip
        if grip.edges & Qt.Edge.LeftEdge:
            assert after.left() == before.left() + 37 and after.right() == before.right()
        if grip.edges & Qt.Edge.RightEdge:
            assert after.right() == before.right() + 37 and after.left() == before.left()
        if grip.edges & Qt.Edge.TopEdge:
            assert after.top() == before.top() + 29 and after.bottom() == before.bottom()
        if grip.edges & Qt.Edge.BottomEdge:
            assert after.bottom() == before.bottom() + 29 and after.top() == before.top()
        pointer(grip, QEvent.Type.MouseMove, start + QPoint(80, 60), released=True)
        assert window.geometry() == after
        assert len(ticks) >= 3
        QTest.mouseClick(window.rail.buttons["telemetry"], Qt.MouseButton.LeftButton)
        settle(app)
        assert window.current_screen == "telemetry"
        QTest.mouseClick(window.rail.buttons["topology"], Qt.MouseButton.LeftButton)
        settle(app)
        topology = window.screens["topology"]
        assert window.current_screen == "topology" and topology.ml_control.switch.isEnabled()
        net=topology._net
        expected_ids = {node.id for node in window.snapshot.topology.nodes}
        assert len(expected_ids) == 71  # Full current global preset, including all nine satellites.
        assert {node.id for node in net.nodes} == expected_ids
        # Resizing and navigation must preserve the actual registered scene.
        # Availability/traffic is checked separately by the satellite pipeline
        # tests; a configured association is not evidence of photon transmission.
        assert initial_links and topology.geo.drawn_link_keys()==initial_links
    finally:
        timer.stop()


def test_windows_resize_does_not_dispatch_a_native_modal_operation(window, monkeypatch):
    monkeypatch.setattr(QApplication, "platformName", lambda: "windows")
    handle = window.windowHandle()
    monkeypatch.setattr(handle, "startSystemResize", lambda _edges: pytest.fail("Windows sizing must stay in Qt"))
    assert window.window_controls.start_system_resize(Qt.Edge.BottomEdge | Qt.Edge.RightEdge) is False


def test_hover_from_another_input_stream_does_not_interrupt_an_edge_drag(window, app, monkeypatch):
    direct_edges(window, monkeypatch)
    grip = window.window_controls.grips[3]
    before = QRect(window.geometry())
    start = grip.mapToGlobal(grip.rect().center())
    pointer(grip, QEvent.Type.MouseButtonPress, start)
    pointer(grip, QEvent.Type.MouseMove, start + QPoint(15, 0))

    class NativeHover(QMouseEvent):
        def spontaneous(self):
            return True

    hover = NativeHover(QEvent.Type.MouseMove, QPointF(grip.rect().center()), QPointF(start),
                        Qt.MouseButton.NoButton, Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier)
    grip.mouseMoveEvent(hover)
    pointer(grip, QEvent.Type.MouseMove, start + QPoint(37, 0))
    pointer(grip, QEvent.Type.MouseButtonRelease, start + QPoint(37, 0), released=True)
    assert window.width() == before.width() + 37
    assert QWidget.mouseGrabber() is not grip


@pytest.mark.parametrize("lost", ["deactivate", "capture", "hide", "no_button"])
def test_lost_release_cancels_resize_and_next_drag_still_works(window, app, monkeypatch, lost):
    direct_edges(window, monkeypatch)
    grip = window.window_controls.grips[3]
    start = grip.mapToGlobal(grip.rect().center())
    pointer(grip, QEvent.Type.MouseButtonPress, start)
    pointer(grip, QEvent.Type.MouseMove, start + QPoint(24, 0)); settle(app)
    before = QRect(window.geometry())
    if lost == "deactivate":
        QApplication.sendEvent(window, QEvent(QEvent.Type.WindowDeactivate))
    elif lost == "capture":
        QApplication.sendEvent(grip, QEvent(QEvent.Type.UngrabMouse))
    elif lost == "hide":
        grip.hide()
    else:
        pointer(grip, QEvent.Type.MouseMove, start + QPoint(25, 0), released=True)
    pointer(grip, QEvent.Type.MouseMove, start + QPoint(120, 0))
    assert window.geometry() == before
    assert QWidget.mouseGrabber() is not grip
    window.window_controls.synchronize(); settle(app)
    start = grip.mapToGlobal(grip.rect().center())
    pointer(grip, QEvent.Type.MouseButtonPress, start)
    pointer(grip, QEvent.Type.MouseMove, start + QPoint(31, 0))
    pointer(grip, QEvent.Type.MouseButtonRelease, start + QPoint(31, 0), released=True)
    assert window.width() == before.width() + 31


def test_arbitrary_window_formats_survive_maximize_restore(window, app):
    window.showNormal()
    for size in ((880, 730), (1320, 510), (740, 960), (1280, 820)):
        window.resize(*size); settle(app)
        assert (window.width(), window.height()) == size
        original = QRect(window.geometry())
        window.showMaximized(); settle(app)
        assert not any(grip.isVisible() for grip in window.window_controls.grips)
        window.showNormal(); settle(app)
        assert window.geometry() == original
        assert all(grip.isVisible() for grip in window.window_controls.grips)


def test_map_returns_to_full_quality_and_reuses_cache_after_resize(window, app):
    geo = window.screens["topology"].geo
    QTest.qWait(160); settle(app)
    initial = geo.grab().toImage()
    size = window.size()
    positions = {node.id: geo.node_position(node.id) for node in geo.net.located}
    for width in range(1260, 940, -16):
        window.resize(width, 710); settle(app)
        assert not geo.grab().isNull()
    window.resize(size); QTest.qWait(160); settle(app)
    assert not geo._resize_settle.isActive()
    assert geo.grab().toImage() == initial
    assert {node.id: geo.node_position(node.id) for node in geo.net.located} == positions
    base = geo._base
    for _ in range(4):
        geo.set_model(geo.net); geo.grab()
    assert geo._base is base


def test_queued_resize_work_does_not_touch_a_deleted_screen(app, service, tmp_path, monkeypatch):
    import sys
    from quantum_autopilot.console.screens.topology import TopologyScreen

    errors = []
    monkeypatch.setattr(sys, "excepthook", lambda kind, error, traceback: errors.append(error))
    provider = ServiceProvider(service, state_root=tmp_path, poll_ms=600000)
    provider.refresh()
    screen = TopologyScreen(); screen.resize(1280, 820)
    screen.apply(provider.snapshot()); screen.show(); settle(app)
    screen.resize(1100, 700)
    screen.shutdown(); screen.close(); screen.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    settle(app); QTest.qWait(20)
    provider.stop()
    assert not errors
