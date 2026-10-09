"""Window management through real Qt controls and pointer events."""
import os
import json
import subprocess
import sys

import pytest

pytest.importorskip("PySide6.QtWidgets")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QEvent, QPoint, QPointF, QRect, QSize, Qt  # noqa: E402
from PySide6.QtGui import QMouseEvent  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication, QLabel, QMainWindow  # noqa: E402

from quantum_autopilot.console import theme  # noqa: E402
from quantum_autopilot.console.window_chrome import FramelessControls, TITLE_HEIGHT, resized_geometry  # noqa: E402


def settle(app):
    for _ in range(5):
        app.processEvents()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture()
def window(app):
    theme.set_theme("dark")
    theme.install(app)
    win = QMainWindow()
    win.setWindowTitle("ORION-Q · Quantum Network Console")
    win.setMinimumSize(320, 240)
    win.setCentralWidget(QLabel("Workspace"))
    win.controls = FramelessControls(win)
    win.resize(640, 480)
    win.move(70, 65)
    win.show()
    settle(app)
    yield win
    win.close()
    win.deleteLater()
    theme.set_theme("dark")
    theme.install(app)
    settle(app)


def pointer(widget, event_type, global_point, *, button=Qt.MouseButton.NoButton, buttons=Qt.MouseButton.LeftButton):
    local = widget.mapFromGlobal(global_point)
    event = QMouseEvent(event_type, QPointF(local), QPointF(global_point), button, buttons,
                        Qt.KeyboardModifier.NoModifier)
    QApplication.sendEvent(widget, event)


def drag(widget, delta):
    start = widget.mapToGlobal(widget.rect().center())
    pointer(widget, QEvent.Type.MouseButtonPress, start, button=Qt.MouseButton.LeftButton)
    pointer(widget, QEvent.Type.MouseMove, start + delta)
    pointer(widget, QEvent.Type.MouseButtonRelease, start + delta, button=Qt.MouseButton.LeftButton,
            buttons=Qt.MouseButton.NoButton)


def test_native_frame_removed_and_chrome_outside_workspace(window):
    assert window.windowFlags() & Qt.WindowType.FramelessWindowHint
    assert window.menuWidget() is window.controls.bar
    assert window.centralWidget().geometry().top() >= TITLE_HEIGHT
    assert window.controls.bar.graphicsProxyWidget() is None
    assert window.frameGeometry() == window.geometry()
    assert len(window.controls.grips) == 8
    assert all(grip.isVisible() for grip in window.controls.grips)


def test_system_requests_use_the_real_desktop_handle(window, monkeypatch):
    # Exercise the platforms that support Qt's native move/resize loop. Windows
    # deliberately uses the client-side grips and drag area to keep events flowing.
    from quantum_autopilot.console import window_chrome
    monkeypatch.setattr(window_chrome.QApplication, "platformName", lambda: "xcb")
    handle = window.windowHandle()
    calls = []
    monkeypatch.setattr(handle, "startSystemMove", lambda: calls.append("move") or True)
    monkeypatch.setattr(handle, "startSystemResize", lambda edges: calls.append(edges) or True)
    assert window.controls.start_system_move()
    assert window.controls.start_system_resize(Qt.Edge.LeftEdge | Qt.Edge.TopEdge)
    assert calls == ["move", Qt.Edge.LeftEdge | Qt.Edge.TopEdge]


def test_controls_have_accessible_names_tooltips_and_keyboard_activation(window, app):
    bar = window.controls.bar
    for button in (bar.minimize_button, bar.maximize_button, bar.close_button):
        assert button.accessibleName() and button.toolTip()
        assert bar.rect().contains(button.geometry())
    bar.maximize_button.setFocus(Qt.FocusReason.TabFocusReason)
    QTest.keyClick(bar.maximize_button, Qt.Key.Key_Return)
    settle(app)
    assert window.isMaximized() and bar.maximize_button.accessibleName() == "Restore window"
    assert not any(grip.isVisible() for grip in window.controls.grips)
    QTest.keyClick(bar.maximize_button, Qt.Key.Key_Space)
    settle(app)
    assert not window.isMaximized() and bar.maximize_button.toolTip() == "Maximize window"
    assert all(grip.isVisible() for grip in window.controls.grips)


def test_minimize_restore_and_close_from_title_controls(window, app):
    bar = window.controls.bar
    QTest.mouseClick(bar.minimize_button, Qt.MouseButton.LeftButton)
    settle(app)
    assert window.isMinimized()
    window.showNormal()
    settle(app)
    assert window.isVisible() and not window.isMinimized()
    QTest.mouseClick(bar.close_button, Qt.MouseButton.LeftButton)
    settle(app)
    assert not window.isVisible()


def test_double_click_toggles_and_buttons_do_not_start_a_drag(window, app, monkeypatch):
    calls = []
    monkeypatch.setattr(window.controls, "start_system_move", lambda: calls.append(True) or True)
    bar = window.controls.bar
    QTest.mouseDClick(bar.drag_area, Qt.MouseButton.LeftButton)
    settle(app)
    assert window.isMaximized()
    QTest.mouseDClick(bar.drag_area, Qt.MouseButton.LeftButton)
    settle(app)
    assert not window.isMaximized()
    QTest.mouseClick(bar.maximize_button, Qt.MouseButton.LeftButton)
    settle(app)
    assert window.isMaximized() and calls == []


@pytest.mark.parametrize("supported", [False, True])
def test_drag_prefers_system_move_and_fallback_moves_by_pointer_delta(window, monkeypatch, supported):
    calls = []
    monkeypatch.setattr(window.controls, "start_system_move", lambda: calls.append(True) or supported)
    start = window.pos()
    drag(window.controls.bar.drag_area, QPoint(41, -23))
    assert len(calls) == 1
    assert window.pos() == (start if supported else start + QPoint(41, -23))


def test_small_motion_is_a_click_and_released_motion_cannot_move_window(window, monkeypatch):
    calls = []
    monkeypatch.setattr(window.controls, "start_system_move", lambda: calls.append(True) or False)
    area = window.controls.bar.drag_area
    before = window.pos()
    drag(area, QPoint(1, 1))
    assert window.pos() == before and calls == []
    point = area.mapToGlobal(area.rect().center())
    pointer(area, QEvent.Type.MouseMove, point + QPoint(70, 30), buttons=Qt.MouseButton.NoButton)
    assert window.pos() == before


def test_dragging_maximized_window_restores_under_pointer(window, app, monkeypatch):
    monkeypatch.setattr(window.controls, "start_system_move", lambda: False)
    size = window.size()
    window.showMaximized()
    settle(app)
    area = window.controls.bar.drag_area
    pressed = area.mapToGlobal(QPoint(area.width() // 3, TITLE_HEIGHT // 2))
    fraction = (pressed.x() - window.x()) / window.width()
    moved = pressed + QPoint(38, 24)
    pointer(area, QEvent.Type.MouseButtonPress, pressed, button=Qt.MouseButton.LeftButton)
    pointer(area, QEvent.Type.MouseMove, moved)
    settle(app)
    assert not window.isMaximized() and window.size() == size
    assert abs(moved.x() - window.x() - round(size.width() * fraction)) <= 1
    pointer(area, QEvent.Type.MouseButtonRelease, moved, button=Qt.MouseButton.LeftButton,
            buttons=Qt.MouseButton.NoButton)


@pytest.mark.parametrize("index", range(8))
def test_every_edge_and_corner_dispatches_system_resize(window, monkeypatch, index):
    calls = []
    monkeypatch.setattr(window.controls, "start_system_resize", lambda edges: calls.append(edges) or True)
    grip = window.controls.grips[index]
    assert window.rect().contains(grip.geometry())
    assert not grip.geometry().contains(window.rect().center())
    before = window.geometry()
    drag(grip, QPoint(37, 29))
    assert calls == [grip.edges] and window.geometry() == before


@pytest.mark.parametrize("index", range(8))
def test_every_edge_and_corner_fallback_resizes_without_moving_opposite_edge(window, monkeypatch, index):
    monkeypatch.setattr(window.controls, "start_system_resize", lambda _edges: False)
    grip = window.controls.grips[index]
    before = window.geometry()
    drag(grip, QPoint(37, 29))
    after = window.geometry()
    if grip.edges & Qt.Edge.LeftEdge:
        assert after.left() == before.left() + 37 and after.right() == before.right()
    elif grip.edges & Qt.Edge.RightEdge:
        assert after.left() == before.left() and after.right() == before.right() + 37
    else:
        assert after.left() == before.left() and after.width() == before.width()
    if grip.edges & Qt.Edge.TopEdge:
        assert after.top() == before.top() + 29 and after.bottom() == before.bottom()
    elif grip.edges & Qt.Edge.BottomEdge:
        assert after.top() == before.top() and after.bottom() == before.bottom() + 29
    else:
        assert after.top() == before.top() and after.height() == before.height()


@pytest.mark.parametrize("edges", [Qt.Edge.LeftEdge | Qt.Edge.TopEdge, Qt.Edge.RightEdge | Qt.Edge.BottomEdge])
def test_resize_constraints_and_negative_monitor_coordinates(edges):
    original = QRect(-1500, -600, 640, 480)
    direction = 1 if edges & Qt.Edge.LeftEdge else -1
    small = resized_geometry(original, edges, QPoint(2000 * direction, 2000 * direction),
                             QSize(320, 240), QSize(900, 700))
    large = resized_geometry(original, edges, QPoint(-2000 * direction, -2000 * direction),
                             QSize(320, 240), QSize(900, 700))
    assert small.size() == QSize(320, 240) and large.size() == QSize(900, 700)
    if edges & Qt.Edge.LeftEdge:
        assert small.bottomRight() == large.bottomRight() == original.bottomRight()
    else:
        assert small.topLeft() == large.topLeft() == original.topLeft()


def test_fullscreen_controls_restore_and_disable_edge_resize(window, app):
    window.showFullScreen()
    settle(app)
    assert window.controls.bar.isVisible()
    assert window.controls.bar.maximize_button.action == "restore"
    assert not any(grip.isVisible() for grip in window.controls.grips)
    QTest.mouseClick(window.controls.bar.maximize_button, Qt.MouseButton.LeftButton)
    settle(app)
    assert not window.isFullScreen() and all(grip.isVisible() for grip in window.controls.grips)


@pytest.mark.parametrize("mode", ["dark", "light"])
def test_compact_chrome_keeps_buttons_visible_and_rethemes_in_place(window, app, mode):
    controls = window.controls
    window.resize(320, 240)
    theme.set_theme(mode)
    theme.install(app)
    controls.bar.refresh_theme()
    settle(app)
    assert window.controls is controls
    assert controls.bar.height() == TITLE_HEIGHT
    for button in (controls.bar.minimize_button, controls.bar.maximize_button, controls.bar.close_button):
        assert button.isVisible() and controls.bar.rect().contains(button.geometry())
    image = controls.bar.grab().toImage()
    assert image.pixelColor(150, 2).name() == theme.TOKENS[mode]["bg"]


@pytest.mark.parametrize("factor", [1.0, 1.5, 2.0])
def test_window_chrome_uses_logical_pixels_at_fractional_dpi(factor):
    code = '''
import json
from PySide6.QtWidgets import QApplication, QMainWindow, QLabel
from quantum_autopilot.console.display import configure_high_dpi
from quantum_autopilot.console.window_chrome import FramelessControls
configure_high_dpi()
app = QApplication([])
window = QMainWindow()
window.setCentralWidget(QLabel("workspace"))
controls = FramelessControls(window)
window.resize(640, 480)
window.show()
for _ in range(6): app.processEvents()
print(json.dumps({"dpr": window.devicePixelRatioF(), "height": controls.bar.height(),
                  "pixels": controls.bar.grab().width(), "width": controls.bar.width(),
                  "frame_removed": window.frameGeometry() == window.geometry()}))
window.close()
'''
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen", QT_SCALE_FACTOR=str(factor))
    process = subprocess.run([sys.executable, "-B", "-c", code], env=env, capture_output=True,
                             text=True, timeout=30)
    assert process.returncode == 0, process.stderr
    result = json.loads(process.stdout)
    assert result["dpr"] == pytest.approx(factor) and result["frame_removed"]
    assert result["height"] == TITLE_HEIGHT
    assert result["pixels"] == round(result["width"] * factor)


def test_frozen_window_chrome_validator(app, tmp_path):
    from quantum_autopilot.console.providers import MockProvider
    from quantum_autopilot.console.shell import ConsoleWindow
    from quantum_autopilot.console.window_chrome_validation import validate_window_chrome

    provider = MockProvider()
    window = ConsoleWindow(provider, skip_auth=True, state_root=tmp_path)
    window._on_snapshot(provider.snapshot())
    window.show()
    settle(app)
    try:
        result = validate_window_chrome(window, tmp_path / 'window-chrome')
        assert result['passed'], [name for name, passed in result['checks'].items() if not passed]
        assert result['check_count'] >= 40
        assert result['physical_action_performed'] is False
    finally:
        window.close()
        window.deleteLater()
        settle(app)


def test_diagnostic_focus_isolation_never_suppresses_a_captured_drag(app):
    from quantum_autopilot.console.providers import MockProvider
    from quantum_autopilot.console.shell import ConsoleWindow
    from quantum_autopilot.console.window_chrome_validation import _WidgetGestureIsolation
    window = ConsoleWindow(MockProvider())
    isolation = _WidgetGestureIsolation(window)
    event = QEvent(QEvent.Type.WindowDeactivate)
    try:
        assert isolation.eventFilter(window, event) is False
        isolation.active = True
        assert isolation.eventFilter(window, event) is True
        grip = window.window_controls.grips[0]
        grip._press_spontaneous = True
        assert isolation.eventFilter(window, event) is False
        assert isolation.eventFilter(window, QEvent(QEvent.Type.Resize)) is False
    finally:
        window.close(); window.deleteLater()
        settle(app)


def test_console_controls_survive_page_zoom_and_theme_rebuild(app, tmp_path):
    from quantum_autopilot.console.providers import MockProvider
    from quantum_autopilot.console.shell import ConsoleWindow

    theme.set_theme("dark")
    theme.install(app)
    provider = MockProvider()
    win = ConsoleWindow(provider, skip_auth=True, state_root=tmp_path)
    win.resize(1280, 800)
    win.show()
    settle(app)
    controls = win.window_controls
    try:
        assert win.current_screen == "splash" and controls.bar.isVisible()
        win._on_snapshot(provider.snapshot())
        for page in ("boot", *win.screens):
            win.show_screen(page)
            settle(app)
            assert controls.bar.isVisible()
            assert controls.bar.graphicsProxyWidget() is None
        win.centralWidget().change_zoom(.4)
        settle(app)
        assert controls.bar.height() == TITLE_HEIGHT
        win.show_screen("telemetry")
        assert win.set_theme("light")
        settle(app)
        assert win.window_controls is controls and controls.bar.isVisible()
        assert theme.TOKENS["light"]["bg"] in controls.bar.styleSheet()
        assert win.current_screen == "telemetry"
        assert win.centralWidget().user_zoom == pytest.approx(1.4)
        win.activateWindow()
        win.setFocus()
        settle(app)
        QTest.keyClick(win, Qt.Key.Key_F4, Qt.KeyboardModifier.AltModifier)
        settle(app)
        assert not win.isVisible()
    finally:
        win.close()
        win.deleteLater()
        theme.set_theme("dark")
        theme.install(app)
        settle(app)
