"""Transient UI notice (2.6 s), bottom-centre, above the status bar."""
from __future__ import annotations

from PySide6.QtCore import QEvent, Qt, QTimer
from PySide6.QtWidgets import QLabel, QWidget

from ..theme import C, TOAST_MS, text


class Toast(QLabel):
    """`window.toast(text)` shows one of these. It never records anything."""

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setFont(text(13))
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setStyleSheet(f"background:{C.ink};color:{C.bg};padding:10px 16px;")
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAccessibleName("Notice")
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.hide)
        self.hide()
        parent.installEventFilter(self)

    def show_message(self, value: str, duration_ms: int = TOAST_MS) -> None:
        self.setText(value)
        self._place()
        self.show()
        self.raise_()
        self._timer.start(duration_ms)

    def _place(self) -> None:
        parent = self.parentWidget()
        self.setMaximumWidth(max(240, parent.width() - 64))
        self.adjustSize()
        self.move((parent.width() - self.width()) // 2, parent.height() - 52 - self.height())

    def eventFilter(self, watched, event):
        if watched is self.parentWidget() and event.type() == QEvent.Type.Resize and self.isVisible():
            self._place()
        return False


__all__ = ["Toast"]
