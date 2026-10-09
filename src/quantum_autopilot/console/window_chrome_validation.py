"""Exercise window controls in source and frozen self-tests, without desktop input."""
from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtCore import QEvent, QObject, QPoint, QPointF, QRect, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMainWindow

from .window_chrome import FramelessControls, TITLE_HEIGHT


def settle():
    for _ in range(6):
        QApplication.processEvents()
    # Native maximize/fullscreen notifications arrive asynchronously. Allow
    # their normal delivery before observing chrome and grip visibility.
    QTest.qWait(8)


def drag(widget, delta):
    start = widget.mapToGlobal(widget.rect().center())
    motions = [start + QPoint(round(delta.x() * step / 8), round(delta.y() * step / 8))
               for step in range(1, 9)]
    for kind, point, button, buttons in [
        (QEvent.Type.MouseButtonPress, start, Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton),
        *((QEvent.Type.MouseMove, point, Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton) for point in motions),
        (QEvent.Type.MouseButtonRelease, start + delta, Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton),
    ]:
        event = QMouseEvent(kind, QPointF(widget.mapFromGlobal(point)), QPointF(point),
                            button, buttons, Qt.KeyboardModifier.NoModifier)
        QApplication.sendEvent(widget, event)
        settle()
    settle()


class _WidgetGestureIsolation(QObject):
    """Native focus changes are unrelated to a scripted, uncaptured gesture.

    The diagnostic window accepts no desktop input. Qt may nevertheless post a
    deactivation after its artificial mouse press; production correctly cancels
    a real drag on deactivation. Isolate just the scripted gesture from that
    unrelated focus stream. A real captured press is never suppressed.
    """

    active = False

    def eventFilter(self, watched, event):
        if self.active and event.type() == QEvent.Type.WindowDeactivate:
            controls = getattr(watched, 'window_controls', None)
            return controls is not None and not any(grip._press_spontaneous is True for grip in controls.grips)
        return False


def validate_window_chrome(window, destination):
    """Check chrome separately from simulated network input or optional models.

    Pointer events target the Qt widgets, not the operator's desktop. System
    move/resize delegation is covered by source tests; these geometry checks
    force the documented fallback to avoid entering an OS mouse capture loop.
    """
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    controls = window.window_controls
    stage = window.centralWidget()
    geometry, state, zoom = QRect(window.geometry()), window.windowState(), stage.user_zoom
    flags = window.windowFlags()
    show_without_activation = window.testAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
    system_move, system_resize = controls.start_system_move, controls.start_system_resize
    checks = {}
    geometry_observations = []
    isolation = _WidgetGestureIsolation(window)
    window.installEventFilter(isolation)
    def scripted_drag(widget, delta):
        isolation.active = True
        try:
            drag(widget, delta)
        finally:
            isolation.active = False
    try:
        # These gestures use widget events, not the operator's mouse. Prevent a
        # real desktop hover/deactivation from interrupting an artificial drag.
        window.setWindowFlag(Qt.WindowType.WindowTransparentForInput, True)
        window.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        window.showNormal()
        window.resize(960, 640)
        settle()
        bar = controls.bar
        checks['frameless'] = bool(window.windowFlags() & Qt.WindowType.FramelessWindowHint)
        checks['no_native_frame_extent'] = window.frameGeometry() == window.geometry()
        checks['native_title_bar_outside_scaled_canvas'] = (
            window.menuWidget() is bar and bar.graphicsProxyWidget() is None
            and window.centralWidget().geometry().top() >= TITLE_HEIGHT)
        checks['root_window_handle'] = bar.window().windowHandle() is window.windowHandle()
        if QApplication.instance().platformName() == 'windows':
            checks['windows_resize_without_modal_loop'] = not system_resize(Qt.Edge.RightEdge)
            checks['windows_move_without_modal_loop'] = not system_move()
        checks['dpr_applied_once'] = bar.devicePixelRatioF() == window.devicePixelRatioF()
        for name, button in (('minimize', bar.minimize_button), ('maximize', bar.maximize_button),
                             ('close', bar.close_button)):
            checks[name + '_accessible'] = bool(button.accessibleName() and button.toolTip())
            checks[name + '_visible'] = button.isVisible() and bar.rect().contains(button.geometry())
        normal = QRect(window.geometry())
        QTest.mouseClick(bar.maximize_button, Qt.MouseButton.LeftButton)
        settle()
        checks['maximize'] = window.isMaximized() and bar.maximize_button.action == 'restore'
        checks['maximized_work_area'] = window.screen().availableGeometry().adjusted(-2, -2, 2, 2).contains(
            window.frameGeometry())
        checks['maximized_grips_disabled'] = not any(grip.isVisible() for grip in controls.grips)
        QTest.mouseClick(bar.maximize_button, Qt.MouseButton.LeftButton)
        settle()
        checks['restore_geometry'] = not window.isMaximized() and window.geometry() == normal
        QTest.mouseDClick(bar.drag_area, Qt.MouseButton.LeftButton)
        settle()
        checks['title_double_click_maximize'] = window.isMaximized()
        QTest.mouseDClick(bar.drag_area, Qt.MouseButton.LeftButton)
        settle()
        checks['title_double_click_restore'] = not window.isMaximized()
        QTest.mouseClick(bar.minimize_button, Qt.MouseButton.LeftButton)
        settle()
        checks['minimize'] = window.isMinimized()
        window.showNormal()
        settle()
        checks['restore_from_minimize'] = not window.isMinimized() and window.isVisible()
        controls.start_system_move = lambda: False
        controls.start_system_resize = lambda _edges: False
        before = window.pos()
        scripted_drag(bar.drag_area, QPoint(29, 21))
        checks['drag_fallback'] = window.pos() == before + QPoint(29, 21)
        for index, grip in enumerate(controls.grips):
            window.setGeometry(normal)
            settle()
            checks[f'grip_{index}_visible'] = grip.isVisible() and window.rect().contains(grip.geometry())
            scripted_drag(grip, QPoint(31, 27))
            after = window.geometry()
            geometry_observations.append({"grip": index, "requested_start": normal.getRect(),
                                          "observed_after": after.getRect()})
            horizontal = (
                after.left() == normal.left() + 31 and after.right() == normal.right()
                if grip.edges & Qt.Edge.LeftEdge else
                after.left() == normal.left() and after.right() == normal.right() + 31
                if grip.edges & Qt.Edge.RightEdge else after.left() == normal.left() and after.width() == normal.width())
            vertical = (
                after.top() == normal.top() + 27 and after.bottom() == normal.bottom()
                if grip.edges & Qt.Edge.TopEdge else
                after.top() == normal.top() and after.bottom() == normal.bottom() + 27
                if grip.edges & Qt.Edge.BottomEdge else after.top() == normal.top() and after.height() == normal.height())
            checks[f'grip_{index}_fallback'] = horizontal and vertical
            checks[f'grip_{index}_capture_released'] = grip.mouseGrabber() is not grip
        window.resize(320, 240)
        stage.change_zoom(.4)
        settle()
        checks['compact_buttons_visible'] = all(button.isVisible() and bar.rect().contains(button.geometry())
            for button in (bar.minimize_button, bar.maximize_button, bar.close_button))
        checks['zoom_does_not_scale_window_controls'] = bar.height() == TITLE_HEIGHT
        window.showFullScreen()
        settle()
        checks['fullscreen_controls_visible'] = bar.isVisible() and bar.maximize_button.action == 'restore'
        checks['fullscreen_grips_disabled'] = not any(grip.isVisible() for grip in controls.grips)
        QTest.mouseClick(bar.maximize_button, Qt.MouseButton.LeftButton)
        settle()
        checks['fullscreen_restore'] = not window.isFullScreen()
        window.setGeometry(normal)
        stage.user_zoom = zoom
        stage._arrange()
        settle()
        checks['screenshot_saved'] = window.grab().save(str(destination / 'frameless-window.png'))

        class CloseProbe(QMainWindow):
            allow_close = False
            attempted = False

            def closeEvent(self, event):
                self.attempted = True
                event.accept() if self.allow_close else event.ignore()

        probe = CloseProbe()
        probe.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        probe.setWindowFlag(Qt.WindowType.WindowDoesNotAcceptFocus, True)
        probe_controls = FramelessControls(probe)
        probe.resize(640, 480)
        probe.show()
        settle()
        QTest.mouseClick(probe_controls.bar.close_button, Qt.MouseButton.LeftButton)
        settle()
        checks['close_honors_ignored_close_event'] = probe.attempted and probe.isVisible()
        probe.allow_close = True
        QTest.mouseClick(probe_controls.bar.close_button, Qt.MouseButton.LeftButton)
        settle()
        checks['close_button_closes'] = not probe.isVisible()
        probe.deleteLater()
        settle()
    finally:
        window.removeEventFilter(isolation)
        isolation.deleteLater()
        controls.start_system_move, controls.start_system_resize = system_move, system_resize
        window.setWindowFlags(flags)
        window.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, show_without_activation)
        stage.user_zoom = zoom
        stage._arrange()
        window.showNormal()
        window.setGeometry(geometry)
        window.setWindowState(state)
        settle()
    result = {'passed': all(checks.values()), 'check_count': len(checks), 'checks': checks,
              'geometry_observations': geometry_observations,
              'qt_platform': QApplication.instance().platformName(), 'dpr': window.devicePixelRatioF(),
              'scope': 'Qt window states and widget events; forced geometry fallback; native focus isolated during scripted gestures; no OS input injection',
              'physical_action_performed': False}
    (destination / 'report.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    return result
