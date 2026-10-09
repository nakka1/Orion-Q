"""Client-side window controls, outside the zoomed workspace.

Windows dragging and edge resizing use pointer geometry without entering the OS
modal move/resize loop; other platforms use system operations where supported.
All positions and grip sizes are logical pixels; Qt applies the monitor DPI.
"""
from __future__ import annotations

from weakref import ref
from quantum_autopilot.product import PRODUCT_NAME

from PySide6.QtCore import QEvent, QObject, QPoint, QRect, Qt
from PySide6.QtGui import QPainter, QPen
from PySide6.QtWidgets import QApplication, QSizePolicy, QWidget

from .theme import C, mono, qc
from .widgets.prims import Clickable, Frame, L, hbox, scoped_style

TITLE_HEIGHT = 34
GRIP_WIDTH = 6
CORNER_WIDTH = 12


def resized_geometry(rect, edges, delta, minimum, maximum):
    """Resize only the requested edges and keep the opposite edges anchored."""
    result = QRect(rect)
    if edges & (Qt.Edge.LeftEdge | Qt.Edge.RightEdge):
        width = rect.width() + (-delta.x() if edges & Qt.Edge.LeftEdge else delta.x())
        width = max(minimum.width(), min(maximum.width(), width))
        result.setWidth(width)
        if edges & Qt.Edge.LeftEdge:
            result.moveLeft(rect.right() + 1 - width)
    if edges & (Qt.Edge.TopEdge | Qt.Edge.BottomEdge):
        height = rect.height() + (-delta.y() if edges & Qt.Edge.TopEdge else delta.y())
        height = max(minimum.height(), min(maximum.height(), height))
        result.setHeight(height)
        if edges & Qt.Edge.TopEdge:
            result.moveTop(rect.bottom() + 1 - height)
    return result


class WindowButton(Clickable):
    """Vector icons remain sharp at fractional DPI and keyboard focus is visible."""

    def __init__(self, action, parent=None):
        super().__init__("", parent)
        self.action = action
        self.setFixedSize(46, TITLE_HEIGHT)
        self.setStyleSheet("QPushButton{border:0;background:transparent;padding:0;}")
        self.set_action(action)

    def set_action(self, action):
        self.action = action
        names = {"minimize": "Minimize window", "maximize": "Maximize window",
                 "restore": "Restore window", "close": "Close window"}
        self.setAccessibleName(names[action])
        self.setToolTip(names[action] + (" (Alt+F4)" if action == "close" else ""))
        self.update()

    def enterEvent(self, event):
        super().enterEvent(event)
        self.update()

    def leaveEvent(self, event):
        super().leaveEvent(event)
        self.update()

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        hot = self.underMouse() or self.isDown()
        if hot:
            painter.fillRect(self.rect(), qc(C.red if self.action == "close" else C.hover))
        painter.setPen(QPen(qc("#ffffff" if hot and self.action == "close" else C.ink), 1.2))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        x, y = self.width() // 2 - 5, self.height() // 2 - 5
        if self.action == "minimize":
            painter.drawLine(x, y + 6, x + 10, y + 6)
        elif self.action == "maximize":
            painter.drawRect(x, y, 10, 10)
        elif self.action == "restore":
            painter.drawRect(x + 3, y, 7, 7)
            painter.fillRect(x, y + 3, 7, 7, qc(C.hover if hot else C.bg))
            painter.drawRect(x, y + 3, 7, 7)
        else:
            painter.drawLine(x, y, x + 10, y + 10)
            painter.drawLine(x + 10, y, x, y + 10)
        if self._focus_visible and self.hasFocus():
            painter.setPen(QPen(qc(C.acc), 2))
            painter.drawRect(self.rect().adjusted(2, 2, -3, -3))
        painter.end()


class DragArea(QWidget):
    def __init__(self, controls, parent=None):
        super().__init__(parent)
        self.controls = controls
        self._press = self._fallback_offset = None
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.setAccessibleName("Window drag area")
        self.setToolTip("Drag to move the window. Double-click to maximize or restore.")
        layout = hbox(self, (16, 0, 8, 0), 0)
        self.title = L(f"{PRODUCT_NAME} · Quantum Network Console", mono(11), C.ink2, elide=True)
        self.title.setMinimumWidth(0)
        self.title.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.title.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        layout.addWidget(self.title, 1)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._press = event.globalPosition().toPoint()
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if not event.buttons() & Qt.MouseButton.LeftButton:
            self._press = self._fallback_offset = None
            return
        point = event.globalPosition().toPoint()
        window = self.controls.window
        if window is None or window.isFullScreen():
            return
        if self._fallback_offset is not None:
            window.move(point - self._fallback_offset)
        elif self._press is not None and (point - self._press).manhattanLength() >= QApplication.startDragDistance():
            pressed = self._press
            self._press = None
            restored = window.isMaximized()
            if restored:
                # Restore beneath the same point of the title bar, including on
                # monitors whose global origin is negative. No manual DPI factor.
                fraction = max(0.0, min(1.0, (pressed.x() - window.x()) / max(1, window.width())))
                offset_y = max(0, min(TITLE_HEIGHT - 1, pressed.y() - window.y()))
                normal = window.normalGeometry()
                window.showNormal()
                # Some platforms apply showNormal asynchronously. Use the saved
                # restored geometry, rather than the still-maximized client size.
                window.setGeometry(QRect(
                    point - QPoint(round(normal.width() * fraction), offset_y), normal.size()))
            if not self.controls.start_system_move():
                # A restored maximized window is already positioned at this point.
                self._fallback_offset = (point if restored else pressed) - window.pos()
                window.move(point - self._fallback_offset)
        event.accept()

    def mouseReleaseEvent(self, event):
        self._press = self._fallback_offset = None
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._press = self._fallback_offset = None
            self.controls.toggle_maximize()
            event.accept()
        else:
            super().mouseDoubleClickEvent(event)


class ResizeGrip(QWidget):
    def __init__(self, controls, edges, cursor):
        super().__init__(controls.window)
        self.controls, self.edges = controls, edges
        self._origin = self._geometry = None
        self._press_spontaneous = None
        self.setMouseTracking(True)
        self.setCursor(cursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setAccessibleName("Resize window")
        self.setToolTip("Drag this edge or corner to resize the window.")

    def mousePressEvent(self, event):
        window = self.controls.window
        if event.button() != Qt.MouseButton.LeftButton or window is None or window.isMaximized() or window.isFullScreen():
            super().mousePressEvent(event)
            return
        if not self.controls.start_system_resize(self.edges):
            self._origin = event.globalPosition().toPoint()
            self._geometry = QRect(window.geometry())
            self._press_spontaneous = event.spontaneous()
            # Keep delivery on the pressed grip even as that edge moves. The
            # release, loss of capture and deactivation all end this operation.
            # Programmatic widget events have no corresponding OS button state.
            # Capturing the desktop for those events lets an unrelated native
            # hover move (NoButton) cancel the simulated drag mid-way.
            if QApplication.platformName() == "windows" and event.spontaneous():
                self.grabMouse()
        event.accept()

    def mouseMoveEvent(self, event):
        window = self.controls.window
        if self._origin is not None and event.spontaneous() != self._press_spontaneous:
            # A backing-store/scene hover update is a different input stream
            # from the drag that owns this grip. It must not cancel or move it.
            event.ignore()
            return
        if not event.buttons() & Qt.MouseButton.LeftButton:
            self.cancel_resize()
        if self._origin is not None and event.buttons() & Qt.MouseButton.LeftButton and window is not None:
            window.setGeometry(resized_geometry(
                self._geometry, self.edges, event.globalPosition().toPoint() - self._origin,
                window.minimumSize(), window.maximumSize()))
            event.accept()
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self.cancel_resize()
        super().mouseReleaseEvent(event)

    def cancel_resize(self):
        self._origin = self._geometry = None
        self._press_spontaneous = None
        if QWidget.mouseGrabber() is self:
            self.releaseMouse()

    def event(self, event):
        if event.type() in (QEvent.Type.UngrabMouse, QEvent.Type.Hide):
            self.cancel_resize()
        return super().event(event)


class WindowTitleBar(Frame):
    def __init__(self, controls):
        super().__init__(C.bg, C.soft, sides="b", parent=controls.window)
        self.setFixedHeight(TITLE_HEIGHT)
        self.setAccessibleName("Window controls")
        layout = hbox(self, (0, 0, 0, 0), 0)
        self.drag_area = DragArea(controls, self)
        layout.addWidget(self.drag_area, 1)
        self.minimize_button = WindowButton("minimize", self)
        self.maximize_button = WindowButton("maximize", self)
        self.close_button = WindowButton("close", self)
        for button in (self.minimize_button, self.maximize_button, self.close_button):
            layout.addWidget(button)
        self.minimize_button.clicked.connect(controls.window.showMinimized)
        self.maximize_button.clicked.connect(controls.toggle_maximize)
        # Use the shell's closeEvent: cancellation and provider shutdown still apply.
        self.close_button.clicked.connect(controls.window.close)

    def refresh_theme(self):
        scoped_style(self, f"background:{C.bg};border-bottom:1px solid {C.soft};")
        self.drag_area.title.setStyleSheet(f"color:{C.ink2};background:transparent;")
        self.update()
        for button in (self.minimize_button, self.maximize_button, self.close_button):
            button.update()


class FramelessControls(QObject):
    """Keep chrome independent of page rebuilds, presentation zoom and tick paints."""

    def __init__(self, window):
        super().__init__(window)
        self._window_ref = ref(window)
        window.setWindowFlag(Qt.WindowType.FramelessWindowHint, True)
        self.bar = WindowTitleBar(self)
        window.setMenuWidget(self.bar)
        left, right, top, bottom = Qt.Edge.LeftEdge, Qt.Edge.RightEdge, Qt.Edge.TopEdge, Qt.Edge.BottomEdge
        self.grips = [ResizeGrip(self, edges, cursor) for edges, cursor in (
            (top | left, Qt.CursorShape.SizeFDiagCursor),
            (top, Qt.CursorShape.SizeVerCursor),
            (top | right, Qt.CursorShape.SizeBDiagCursor),
            (right, Qt.CursorShape.SizeHorCursor),
            (bottom | right, Qt.CursorShape.SizeFDiagCursor),
            (bottom, Qt.CursorShape.SizeVerCursor),
            (bottom | left, Qt.CursorShape.SizeBDiagCursor),
            (left, Qt.CursorShape.SizeHorCursor),
        )]
        window.installEventFilter(self)
        self.synchronize()

    @property
    def window(self):
        return self._window_ref()

    def start_system_move(self):
        if QApplication.platformName() == "windows":
            return False
        window = self.window
        handle = window.windowHandle() if window is not None else None
        return bool(handle is not None and handle.startSystemMove())

    def start_system_resize(self, edges):
        # QWindow.startSystemResize posts WM_SYSCOMMAND/SC_SIZE on Windows.
        # Its modal message loop bypasses the ordinary pointer delivery used
        # by these client-side grips. Keep sizing in the Qt event loop instead.
        if QApplication.platformName() == "windows":
            return False
        window = self.window
        handle = window.windowHandle() if window is not None else None
        return bool(handle is not None and handle.startSystemResize(edges))

    def toggle_maximize(self):
        window = self.window
        if window is not None:
            if window.isMaximized() or window.isFullScreen():
                window.showNormal()
            else:
                window.showMaximized()

    def eventFilter(self, watched, event):
        if watched is self.window and event.type() == QEvent.Type.WindowDeactivate:
            for grip in self.grips:
                grip.cancel_resize()
        if watched is self.window and event.type() in (
            QEvent.Type.Resize, QEvent.Type.Show, QEvent.Type.WindowStateChange, QEvent.Type.Hide,
        ):
            self.synchronize()
        return False

    def synchronize(self):
        window = self.window
        if window is None:
            return
        expanded = window.isMaximized() or window.isFullScreen()
        action = "restore" if expanded else "maximize"
        if self.bar.maximize_button.action != action:
            self.bar.maximize_button.set_action(action)
        w, h, corner, edge = window.width(), window.height(), CORNER_WIDTH, GRIP_WIDTH
        rectangles = (
            (0, 0, corner, corner), (corner, 0, max(0, w - 2 * corner), edge),
            (w - corner, 0, corner, corner), (w - edge, corner, edge, max(0, h - 2 * corner)),
            (w - corner, h - corner, corner, corner), (corner, h - edge, max(0, w - 2 * corner), edge),
            (0, h - corner, corner, corner), (0, corner, edge, max(0, h - 2 * corner)),
        )
        for grip, rectangle in zip(self.grips, rectangles):
            grip.setGeometry(*rectangle)
            grip.setVisible(window.isVisible() and not expanded)
            if not expanded:
                grip.raise_()
