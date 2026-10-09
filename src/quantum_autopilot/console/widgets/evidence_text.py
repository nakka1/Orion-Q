"""Text viewports that repaint independently of their transparent containers."""
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QPlainTextEdit

from ..theme import C


class ScrollableTextEdit(QPlainTextEdit):
    """Keep Qt's normal editing API and both text/parent constructor overloads."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._viewport_background = QColor(str(C.void))
        # The global theme gives QWidget a transparent background. A text
        # viewport needs its own opaque pixels when Qt scrolls its backing store.
        self.viewport().setAutoFillBackground(True)
        self.viewport().setStyleSheet(f"background:{C.void};")
        self.viewport().setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, True)

    def paintEvent(self, event):  # noqa: N802
        # WA_OpaquePaintEvent skips Qt's automatic background fill. Supply every
        # background pixel ourselves before QPlainTextEdit paints the glyphs;
        # otherwise a native capture can contain a black, unreadable viewport.
        painter = QPainter(self.viewport())
        painter.fillRect(event.rect(), self._viewport_background)
        painter.end()
        super().paintEvent(event)

    def set_evidence(self, text):
        # Passive refreshes must preserve the cursor, selection and scroll bars.
        if text != self.toPlainText():
            self.setPlainText(text)

    def scrollContentsBy(self, dx, dy):  # noqa: N802
        super().scrollContentsBy(dx, dy)
        # Small wheel steps, keys and scrollbar drags all use this hook. Paint
        # the entire viewport, including the pixels reused by Qt's scroll blit.
        self.viewport().update()


class EvidenceText(ScrollableTextEdit):
    """Read-only evidence; unchanged refreshes retain selection and position."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setReadOnly(True)
        self.setToolTip("Scroll to read the captured evidence. Select text to copy it without exporting.")
