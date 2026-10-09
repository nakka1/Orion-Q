"""Keep dialog controls reachable in a small or high-DPI work area."""
from PySide6.QtCore import QEvent, Qt
from PySide6.QtWidgets import QDialog, QFrame, QLayout, QScrollArea, QVBoxLayout, QWidget

from ..theme import C
from ..display import DisplayObserver, fit_to_screen, screen_for


class _FocusScrollArea(QScrollArea):
    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.FocusIn and isinstance(watched, QWidget):
            if self.widget().isAncestorOf(watched):
                self.ensureWidgetVisible(watched, 12, 12)
        return super().eventFilter(watched, event)


class AdaptiveDialog(QDialog):
    def __init__(self, parent=None):
        # Screens live in a scaled graphics proxy. Child dialogs must remain
        # desktop windows: auto-embedding corrupts the Windows scene backing store.
        super().__init__(parent, Qt.WindowType.Dialog | Qt.WindowType.BypassGraphicsProxyWidget)
        self.content_scroll = None
        self._display_observer = DisplayObserver(self, fit=True)
        self._first_show = True

    def showEvent(self, event):
        from .button_help import install_control_help
        install_control_help(self)
        if self.content_scroll is None and self.layout() is not None:
            original_size = self.size()
            footer = None
            if getattr(self, "fixed_footer", ()):
                footer = QWidget()
                footer_layout = QVBoxLayout(footer)
                footer_layout.setContentsMargins(18, 8, 18, 16)
                footer_layout.setSpacing(10)
                for widget in self.fixed_footer:
                    self.layout().removeWidget(widget)
                    footer_layout.addWidget(widget)
            body = QWidget()
            body.setMinimumSize(self.minimumSize())
            body.setLayout(self.layout())
            body.layout().setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)
            scroll = _FocusScrollArea(self)
            scroll.setFrameShape(QFrame.Shape.NoFrame)
            scroll.setWidgetResizable(True)
            scroll.setWidget(body)
            for child in body.findChildren(QWidget):
                child.installEventFilter(scroll)
            scroll.setStyleSheet(f"QScrollArea, QScrollArea > QWidget > QWidget {{background:{C.raised};}}")
            self.content_scroll = scroll
            self.setMinimumSize(320, 240)
            layout = QVBoxLayout(self)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.addWidget(scroll, 1)
            if footer is not None:
                layout.addWidget(footer)
            self.resize(original_size)
        if self._first_show:
            screen = screen_for(self.parentWidget())
            if screen is not None:
                self.setScreen(screen)
            self._first_show = False
        fit_to_screen(self)
        super().showEvent(event)
