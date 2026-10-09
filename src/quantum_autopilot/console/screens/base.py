"""Screen scaffolding. A screen renders only its body; the shell owns the chrome.

The shell (`console.shell.ConsoleWindow`) provides the top bar, the icon rail,
the journal/terminal and the status bar once for the whole application, and
hosts each screen in a scroll area below the top bar.

Contract for a screen:

* subclass `Screen`, set `key`, `heading` (and `title` for older callers) and
  list the class in `screens.SCREENS`; the rail order is `widgets.chrome.ROUTES`;
* build the body into `self.root` in `__init__`, reading colours from `theme.C`
  at that moment (the shell rebuilds every screen when the theme changes);
* update live values in `refresh(snap)`; never keep state the snapshot owns;
* emit `command(verb, target)` for backend operations, `navigate(key)` to change
  screen, `inspect_node(id)` to open the node screen, `select(kind, id)` to set
  the shell selection and `notify(text)` for a transient toast;
* override `save_state()` / `restore_state()` for view state that must survive
  a theme rebuild, `breadcrumb()` for the top-bar trail and `on_selection()`
  to follow the shell selection.
"""
from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QDialog, QLineEdit, QWidget

from ..contract import Snapshot
from ..theme import C, DESIGN_H, DESIGN_W, MIN_CONTENT_W, RAIL_W
from ..widgets import scoped_style, vbox
from ..widgets.chrome import ROUTES, ScreenHeader

# (key, title) in rail order — kept for callers that imported the old list.
NAV = [(key, title) for key, title, _accessible in ROUTES]


class Screen(QWidget):
    """Body of one workspace screen, with flexible width and height."""

    navigate = Signal(str)
    inspect_node = Signal(str)
    command = Signal(str, str)      # verb, target
    select = Signal(str, str)       # kind ("node" | "link"), identifier
    notify = Signal(str)            # transient notice shown by the shell toast

    key = "screen"
    title = "SCREEN"
    heading = ""                    # body heading and palette/crumb name
    status = ""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.resize(DESIGN_W, DESIGN_H)
        self.setMinimumWidth(MIN_CONTENT_W - RAIL_W)
        scoped_style(self, f"background:{C.bg};")   # own ground only; children stay transparent
        self.root = vbox(self, (0, 0, 0, 0), 0)
        self._snap: Snapshot | None = None

    # ---------------------------------------------------------------- body
    def add_header(self, heading: str | None = None) -> ScreenHeader:
        """Add the body header: heading, status label and optional widgets."""

        header = ScreenHeader(heading or self.heading or self.title.title())
        header.set_status(self.status)
        self.bar = header
        self.root.addWidget(header)
        return header

    def add_app_bar(self, wordmark_lit: bool = False) -> ScreenHeader:
        """Older name of `add_header`; the application bar now lives in the shell."""

        return self.add_header()

    # --------------------------------------------------------------- hooks
    def breadcrumb(self, snap: Snapshot | None, selection=None) -> str:
        """Trail shown after "orion-q" in the top bar."""

        return "/ " + (self.heading or self.title).lower()

    def on_selection(self, selection) -> None:
        """The shell selection changed (`selection.kind`, `selection.id`)."""

    def cache_selection(self, selection) -> None:
        """Keep a hidden screen's selection without presenting its widgets.

        Screens with selection-dependent rendering override this hook. The
        default keeps compatibility with extensions that implement on_selection.
        The shell applies its latest snapshot before showing a cached screen.
        """

        self.on_selection(selection)

    def save_state(self) -> dict:
        """View state to carry across a rebuild (theme change). JSON-like dict."""

        return {"fields": [field.text() for field in self._form_fields()]}

    def restore_state(self, state: dict) -> None:
        fields = self._form_fields()
        values = state.get("fields", [])
        if len(values) == len(fields):
            for field, value in zip(fields, values):
                if value and field.text() != value:
                    field.setText(value)

    def _form_fields(self) -> list[QLineEdit]:
        def in_dialog(widget):
            parent = widget.parentWidget()
            while parent is not None and parent is not self:
                if isinstance(parent, QDialog):
                    return True
                parent = parent.parentWidget()
            return False
        return [field for field in self.findChildren(QLineEdit)
                if not field.isReadOnly() and not in_dialog(field)
                and field.echoMode() == QLineEdit.EchoMode.Normal]

    def shutdown(self) -> None:
        """Close secondary windows and stop their work before the screen is discarded."""

        for dialog in self.findChildren(QDialog):
            dialog.close()

    # ---------------------------------------------------------------- data
    def apply(self, snap: Snapshot) -> None:
        if self._snap is not None and (
            self._snap.campaign != snap.campaign or self._snap.input_kind != snap.input_kind
            or not snap.topology.nodes
        ):
            overlay = getattr(self, "node_popover", None)
            if overlay is not None:
                overlay.hide()
        self._snap = snap
        self.refresh(snap)
        self.apply_command_capabilities()

    def apply_command_capabilities(self):
        if self._snap is None:
            return
        from ..widgets import Btn
        from ..widgets.button_help import button_description
        capabilities = self._snap.capabilities
        for button in self.findChildren(Btn):
            operation = button.property("operation")
            if operation:
                allowed = capabilities.allows(operation)
                busy = getattr(self, "commands_busy", False)
                button.setEnabled(allowed and not busy)
                description = button_description(button.text(), operation)
                if not allowed:
                    description += f"\nUnavailable: {operation}. {capabilities.note}"
                elif busy:
                    description += "\nWait for the current local operation to finish."
                button.setToolTip(description)

    def refresh(self, snap: Snapshot) -> None:
        """Update live values only."""


__all__ = ["NAV", "Screen", "ScreenHeader"]
