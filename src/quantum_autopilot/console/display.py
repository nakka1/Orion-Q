"""Monitor geometry and native DPI for the console and its desktop dialogs.

Qt uses logical pixels and applies each monitor's DPI. Only the workspace's
presentation zoom is added; Windows scaling must never be applied a second time.
"""
from __future__ import annotations

from weakref import ref

from PySide6.QtCore import QEvent, QObject, QPoint, QTimer, Qt
from PySide6.QtGui import QCursor, QGuiApplication


def configure_high_dpi() -> None:
    """Call before creating QApplication so fractional Windows scales stay exact."""
    if QGuiApplication.instance() is None:
        QGuiApplication.setHighDpiScaleFactorRoundingPolicy(
            Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)


def screen_for(widget=None):
    """Resolve a proxy child through the desktop window that actually displays it."""
    if widget is not None:
        root = widget.window()
        proxy = root.graphicsProxyWidget()
        if proxy is not None and proxy.scene() is not None:
            views = proxy.scene().views()
            if views:
                return views[0].window().screen()
        return root.screen()
    return QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()


def raster_scale_for(widget) -> float:
    """Render cached artwork at the view's real pixel density, including zoom."""
    proxy = widget.window().graphicsProxyWidget()
    if proxy is not None and proxy.scene() is not None:
        views = proxy.scene().views()
        if views:
            view = views[0]
            return max(1.0, view.viewport().devicePixelRatioF() * abs(view.transform().m11()))
    return max(1.0, widget.devicePixelRatioF())


def fit_to_screen(window, screen=None, margin=16) -> None:
    """Keep the complete native frame inside the chosen monitor's work area."""
    screen = screen or screen_for(window)
    if screen is None:
        return
    area = screen.availableGeometry().adjusted(margin, max(40, margin), -margin, -margin)
    frame = window.frameGeometry()
    extra_w = max(0, frame.width() - window.width())
    extra_h = max(0, frame.height() - window.height())
    # Native move/frame notifications are asynchronous. Leave a frame-edge inset
    # so rounding on a fractional-DPI or negative-origin monitor cannot cut a border.
    edge = max(2, extra_w // 2)
    area = area.adjusted(edge, edge, -edge, -edge)
    window.resize(min(window.width(), max(1, area.width() - extra_w)),
                  min(window.height(), max(1, area.height() - extra_h)))
    frame = window.frameGeometry()
    # Include the platform's frame offset and nonzero/negative monitor origins.
    offset = window.pos() - frame.topLeft()
    window.move(QPoint(
        max(area.left(), min(frame.left(), area.right() - frame.width() + 1)),
        max(area.top(), min(frame.top(), area.bottom() - frame.height() + 1))) + offset)


def prepare_main_window(window, screen=None) -> None:
    """Choose the launch monitor and a useful, freely resizable restored size."""
    screen = screen or screen_for()
    if screen is None:
        window.resize(1440, 900)
        return
    window.setScreen(screen)
    area = screen.availableGeometry()
    window.resize(max(window.minimumWidth(), round(area.width() * .9)),
                  max(window.minimumHeight(), round(area.height() * .9)))
    window.move(area.center() - window.rect().center())
    fit_to_screen(window, screen)


def show_main_window(window, *, fullscreen=False) -> None:
    prepare_main_window(window)
    if fullscreen:
        window.showFullScreen()
    else:
        window.show()


class DisplayObserver(QObject):
    """Follow monitor/work-area changes without rebuilding widgets or losing input."""

    def __init__(self, window, *, fit=False) -> None:
        super().__init__(window)
        self._window_ref, self.fit = ref(window), fit
        self._screen = self._handle = None
        self._queued = False
        window.installEventFilter(self)

    @property
    def window(self):
        # Qt can still dispatch teardown events while Python clears a wrapper.
        # A weak reference also avoids keeping a closed window alive in a cycle.
        reference = getattr(self, "_window_ref", None)
        return reference() if reference is not None else None

    def eventFilter(self, watched, event):
        window = self.window
        if window is not None and watched is window and event.type() in (QEvent.Type.Show, QEvent.Type.ScreenChangeInternal):
            handle = window.windowHandle()
            if handle is not None and handle is not self._handle:
                self._handle = handle
                handle.screenChanged.connect(self.bind_screen)
                # Startup geometry is fitted synchronously by the launcher/dialog.
                # A deferred second fit would undo an operator resize just after Show.
                self.bind_screen(handle.screen(), synchronize=False)
        return False

    def bind_screen(self, screen, *, synchronize=True) -> None:
        if screen is not self._screen:
            for old in (self._screen,):
                if old is not None:
                    for name in ("availableGeometryChanged", "logicalDotsPerInchChanged", "geometryChanged"):
                        try:
                            getattr(old, name).disconnect(self.schedule)
                        except (RuntimeError, TypeError):
                            pass  # A removed QScreen may already have been destroyed by Qt.
            self._screen = screen
            if screen is not None:
                for name in ("availableGeometryChanged", "logicalDotsPerInchChanged", "geometryChanged"):
                    getattr(screen, name).connect(self.schedule)
        if synchronize:
            self.schedule()

    def schedule(self, *_args) -> None:
        if self.window is None:
            return
        if not self._queued:
            self._queued = True
            QTimer.singleShot(0, self, self.synchronize)

    def synchronize(self) -> None:
        self._queued = False
        window = self.window
        if window is None or not window.isVisible():
            return
        if self.fit and not (window.isMaximized() or window.isFullScreen()):
            fit_to_screen(window, self._screen)
        stage = getattr(window, "centralWidget", lambda: None)()
        if stage is not None and hasattr(stage, "_arrange"):
            stage._arrange()
            stage.viewport().update()
        window.update()
