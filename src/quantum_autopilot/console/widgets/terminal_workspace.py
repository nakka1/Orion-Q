"""A docked terminal whose size and visibility give space back to the screen."""
from __future__ import annotations

from PySide6.QtCore import QEvent, Qt, Signal
from PySide6.QtGui import QPainter, QPen
from PySide6.QtWidgets import QSplitter, QSplitterHandle, QWidget

from ..theme import C, qc
from .journal import JournalPanel

DEFAULT_TERMINAL_HEIGHT = 220
MIN_SCREEN_HEIGHT = 160


class _TerminalHandle(QSplitterHandle):
    def __init__(self, orientation, parent) -> None:
        super().__init__(orientation, parent)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.SizeVerCursor)
        self.setAccessibleName("Resize terminal")
        self.setToolTip("Drag up or down to resize the terminal. Arrow keys resize; double-click or Home restores its default height.")

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setPen(QPen(qc(C.acc if self.hasFocus() or self.underMouse() else C.ink4), 1))
        center = self.rect().center()
        painter.drawLine(center.x() - 24, center.y(), center.x() + 24, center.y())

    def keyPressEvent(self, event) -> None:
        if event.key() in (Qt.Key.Key_Up, Qt.Key.Key_Down, Qt.Key.Key_Home):
            workspace = self.splitter()
            if event.key() == Qt.Key.Key_Home:
                workspace.reset_terminal_height()
            else:
                step = 40 if event.modifiers() & Qt.KeyboardModifier.ShiftModifier else 12
                workspace.set_terminal_height(workspace.journal.height() +
                                              (step if event.key() == Qt.Key.Key_Up else -step))
            event.accept()
            return
        super().keyPressEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.splitter().reset_terminal_height()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)


class TerminalWorkspace(QSplitter):
    """Keep the requested terminal height while the screen takes surplus space.

    Hiding only removes the panel from the layout: widgets, prompt draft,
    history and backend records stay alive. Compact windows clamp the visible
    height; expanding the window restores the operator's preferred height.
    """

    visibility_changed = Signal(bool)

    def __init__(self, screen: QWidget, journal: JournalPanel, parent=None) -> None:
        super().__init__(Qt.Orientation.Vertical, parent)
        self.journal = journal
        self.preferred_height = DEFAULT_TERMINAL_HEIGHT
        self._arranging = False
        self.setObjectName("terminalWorkspace")
        self.setChildrenCollapsible(False)
        self.setOpaqueResize(True)
        self.setHandleWidth(9)
        self.setStyleSheet(f"QSplitter#terminalWorkspace::handle{{background:{C.raised};}}")
        screen.setMinimumHeight(MIN_SCREEN_HEIGHT)
        self.addWidget(screen)
        self.addWidget(journal)
        self.setStretchFactor(0, 1)
        self.setStretchFactor(1, 0)
        self.splitterMoved.connect(self._remember_height)
        journal.toggled.connect(self._arrange)
        journal.installEventFilter(self)
        self._arrange()

    def createHandle(self):  # noqa: N802 - Qt virtual
        return _TerminalHandle(self.orientation(), self)

    @property
    def terminal_visible(self) -> bool:
        # isVisible() would also be false on the login page or during a rebuild.
        return not self.journal.isHidden()

    def set_terminal_visible(self, visible: bool, *, focus: bool = True) -> None:
        visible = bool(visible)
        changed = visible != self.terminal_visible
        if visible:
            self.journal.set_open(True)
        self.journal.setVisible(visible)
        self._arrange()
        if changed:
            self.visibility_changed.emit(visible)
        if visible and focus:
            self.journal.prompt.setFocus(Qt.FocusReason.OtherFocusReason)

    def set_terminal_height(self, height: int) -> None:
        self.preferred_height = max(1, int(height))
        self._arrange()

    def reset_terminal_height(self) -> None:
        self.journal.set_open(True)
        self.set_terminal_height(DEFAULT_TERMINAL_HEIGHT)

    def _remember_height(self, _position: int, _index: int) -> None:
        if not self._arranging and self.terminal_visible and self.journal.is_open:
            self.preferred_height = self.journal.height()

    def _arrange(self, *_args) -> None:
        if self._arranging or self.count() != 2:
            return
        self._arranging = True
        try:
            # A retracted panel has only its header; do not overwrite the saved
            # expanded height through an inactive resize control.
            self.handle(1).setEnabled(self.journal.is_open)
            if not self.terminal_visible:
                self.setSizes([self.height(), 0])
                return
            available = max(0, self.height() - self.handleWidth())
            minimum = self.journal.minimumSizeHint().height()
            maximum = max(minimum, available - self.widget(0).minimumHeight())
            wanted = self.preferred_height if self.journal.is_open else self.journal.head.height()
            height = min(maximum, max(minimum, wanted))
            self.setSizes([max(0, available - height), height])
        finally:
            self._arranging = False

    def eventFilter(self, watched, event) -> bool:  # noqa: N802 - Qt virtual
        if watched is self.journal and event.type() == QEvent.Type.LayoutRequest:
            # Input feedback can change the panel's minimum readable height.
            self._arrange()
        return super().eventFilter(watched, event)

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt virtual
        super().resizeEvent(event)
        self._arrange()


__all__ = ["DEFAULT_TERMINAL_HEIGHT", "MIN_SCREEN_HEIGHT", "TerminalWorkspace"]
