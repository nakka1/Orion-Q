"""Table viewports with their own pixels after native and proxy scrolling."""
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QTableView, QTableWidget

from ..theme import C


class _OpaqueTableViewport:
    def __init__(self, *args, background=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._viewport_background = QColor(str(background or C.bg))
        viewport = self.viewport()
        viewport.setAutoFillBackground(True)
        viewport.setStyleSheet(f"background:{self._viewport_background.name()};")
        viewport.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, True)

    def paintEvent(self, event):  # noqa: N802 - Qt API
        # The global QWidget style is transparent. Supplying the complete
        # background prevents reused scroll pixels from depending on a parent
        # repaint (including when that parent lives in QGraphicsProxyWidget).
        painter = QPainter(self.viewport())
        painter.fillRect(event.rect(), self._viewport_background)
        painter.end()
        super().paintEvent(event)

    def scrollContentsBy(self, dx, dy):  # noqa: N802
        super().scrollContentsBy(dx, dy)
        self.viewport().update()


class ScrollableTableView(_OpaqueTableViewport, QTableView):
    """Keep Qt model/view selection and scrolling; repaint the whole viewport."""


class ScrollableTableWidget(_OpaqueTableViewport, QTableWidget):
    """The item-based counterpart, preserving QTableWidget constructors."""
