"""Command palette (Ctrl+K): a filtered list generated from real state.

Entries come from `palette_commands(snapshot)`: the six screens, the nodes and
links of the current snapshot, the operations the active source's capabilities
allow, the theme switch and the session entries. Nothing is listed that the
source cannot do. An entry is a terminal verb plus a target, so the palette and
the typed terminal share one dispatch path (`ConsoleWindow.run_verb`).

Other modules extend the list with `register_palette_provider`.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Iterable

from PySide6.QtCore import QEvent, Qt, Signal
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import QFrame, QLineEdit, QScrollArea, QWidget

from ..theme import C, button_font, current_theme, mono, qc, text
from .chrome import ROUTES
from .journal import link_name
from .prims import Clickable, Frame, L, Label, RowBtn, clear_layout, hbox, vbox

GROUPS = ("screen", "command", "select", "view", "session")
MAX_VISIBLE = 80


@dataclass(frozen=True)
class PaletteCommand:
    label: str
    group: str                      # one of GROUPS (others sort after them)
    verb: str                       # terminal verb, run by ConsoleWindow.run_verb
    target: str = ""
    operation: str | None = None    # capability this entry was derived from
    keywords: str = ""


PaletteProvider = Callable[[object], Iterable[PaletteCommand]]
PALETTE_PROVIDERS: list[PaletteProvider] = []


def register_palette_provider(provider: PaletteProvider) -> PaletteProvider:
    """Add a function `snapshot -> iterable of PaletteCommand` to the registry."""

    if provider not in PALETTE_PROVIDERS:
        PALETTE_PROVIDERS.append(provider)
    return provider


def palette_commands(snapshot) -> list[PaletteCommand]:
    """Every entry the registry yields for this snapshot, grouped in GROUPS order."""

    seen: set[tuple[str, str]] = set()
    commands: list[PaletteCommand] = []
    for provider in PALETTE_PROVIDERS:
        for command in provider(snapshot):
            key = (command.label, command.group)
            if key not in seen:
                seen.add(key)
                commands.append(command)
    order = {name: index for index, name in enumerate(GROUPS)}
    return sorted(commands, key=lambda command: order.get(command.group, len(GROUPS)))


def filter_commands(commands: Iterable[PaletteCommand], query: str) -> list[PaletteCommand]:
    """Keep entries whose label, group or keywords contain every typed term."""

    terms = str(query or "").lower().split()
    if not terms:
        return list(commands)
    kept = []
    for command in commands:
        haystack = f"{command.label} {command.group} {command.keywords}".lower()
        if all(term in haystack for term in terms):
            kept.append(command)
    return kept


# ---- built-in providers ---------------------------------------------------
@register_palette_provider
def _screens(_snapshot):
    for key, title, _accessible in ROUTES:
        yield PaletteCommand(f"Go to {title}", "screen", "go", key)


# operation -> (label, terminal verb); only declared operations are listed.
OPERATION_ENTRIES = (
    ("run_scenario", "Run simulated scenario", "run_scenario"),
    ("replay_model", "Replay WDM model (shadow)", "replay_model"),
    ("export_record", "Export record", "export"),
    ("reload", "Reload source", "reload"),
    ("pause", "Pause / resume artificial stream", "pause"),
    ("dropout", "Inject / clear data dropout", "dropout"),
    ("cancel", "Cancel controller campaign", "cancel"),
)


@register_palette_provider
def _operations(snapshot):
    if snapshot is None:
        return
    capabilities = snapshot.capabilities
    for operation, label, verb in OPERATION_ENTRIES:
        if capabilities.allows(operation):
            yield PaletteCommand(label, "command", verb, "", operation)


@register_palette_provider
def _node_actions(snapshot):
    """Recalibrate / isolate entries through the operations this source declares."""

    if snapshot is None:
        return
    capabilities = snapshot.capabilities
    simulated = capabilities.allows("apply_simulated_node_action")
    for node in snapshot.topology.nodes:
        if simulated or capabilities.allows("recalibrate"):
            yield PaletteCommand(f"Recalibrate {node.id}", "command", "recalibrate", node.id,
                                 "apply_simulated_node_action" if simulated else "recalibrate")
        if simulated and not node.isolated:
            yield PaletteCommand(f"Isolate {node.id}", "command", "isolate", node.id,
                                 "apply_simulated_node_action")


@register_palette_provider
def _selection(snapshot):
    if snapshot is None:
        return
    for node in snapshot.topology.nodes:
        yield PaletteCommand(f"Select node {node.id}", "select", "select_node", node.id, keywords=node.role)
    for link in snapshot.topology.links:
        name = link_name(link)
        yield PaletteCommand(f"Select link {name}", "select", "select_link", name,
                             keywords=f"{link.a} {link.b} {link.label}")


@register_palette_provider
def _view_and_session(snapshot):
    other = "light" if current_theme() == "dark" else "dark"
    yield PaletteCommand(f"Switch to {other} theme", "view", "theme", other)
    if snapshot is not None:
        yield PaletteCommand("Show input capabilities / execution scope", "view", "capabilities")
    yield PaletteCommand("Change data source", "session", "sign_out", keywords="login source demonstration")
    yield PaletteCommand("Sign out", "session", "sign_out")


# ------------------------------------------------------------------ widget
class _Search(QLineEdit):
    move = Signal(int)
    run = Signal()
    cancel = Signal()

    def keyPressEvent(self, event) -> None:
        key = event.key()
        if key == Qt.Key.Key_Escape:
            self.cancel.emit()
        elif key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.run.emit()
        elif key == Qt.Key.Key_Down:
            self.move.emit(1)
        elif key == Qt.Key.Key_Up:
            self.move.emit(-1)
        elif key == Qt.Key.Key_PageDown:
            self.move.emit(8)
        elif key == Qt.Key.Key_PageUp:
            self.move.emit(-8)
        else:
            super().keyPressEvent(event)
            return
        event.accept()


class CommandPalette(QWidget):
    """Full-window overlay with the 560 px palette panel, 96 px from the top."""

    run_requested = Signal(object)      # PaletteCommand
    closed = Signal()

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self._commands: list[PaletteCommand] = []
        self._visible: list[PaletteCommand] = []
        self._rows: list[RowBtn] = []
        self._index = 0
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.panel = Frame(C.bg, C.line, parent=self)
        self.panel.setFixedWidth(560)
        pl = vbox(self.panel, (1, 1, 1, 1), 0)

        head = Frame(border=C.soft, sides="b")
        hl = hbox(head, (16, 12, 16, 12), 10)
        hl.addWidget(L("›", mono(15), C.acc_t))
        self.search = _Search()
        self.search.setFont(mono(15))
        self.search.setFrame(False)
        self.search.setPlaceholderText("Type a command or screen…")
        self.search.setAccessibleName("Search commands")
        self.search.setStyleSheet(f"QLineEdit{{background:transparent;border:0;color:{C.ink};padding:0;}}"
                                  f"QLineEdit:focus{{border:0;}}")
        self.search.textChanged.connect(self._refilter)
        self.search.move.connect(self._move)
        self.search.run.connect(self.run_current)
        self.search.cancel.connect(self.close_palette)
        hl.addWidget(self.search, 1)
        self.esc = Clickable("Esc")
        self.esc.setFont(button_font(11))
        self.esc.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.esc.setStyleSheet(f"QPushButton{{color:{C.ink};border:1px solid {C.line};padding:2px 8px;background:transparent;}}"
                               f"QPushButton:hover{{background:{C.hover};}}")
        self.esc.clicked.connect(self.close_palette)
        hl.addWidget(self.esc)
        pl.addWidget(head)

        self.list_host = QWidget()
        self.list_lay = vbox(self.list_host, (0, 0, 0, 0), 0)
        self.list_lay.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setWidget(self.list_host)
        self.scroll.setMaximumHeight(380)
        pl.addWidget(self.scroll)
        self.hide()
        parent.installEventFilter(self)

    # ----------------------------------------------------------------- open
    def open_with(self, commands: list[PaletteCommand]) -> None:
        self._commands = list(commands)
        self.search.blockSignals(True)
        self.search.clear()
        self.search.blockSignals(False)
        self._refilter("")
        self._fit()
        self.show()
        self.raise_()
        self.search.setFocus(Qt.FocusReason.PopupFocusReason)

    def close_palette(self) -> None:
        if self.isVisible():
            self.hide()
            self.closed.emit()

    def is_open(self) -> bool:
        return self.isVisible()

    def items(self) -> list[PaletteCommand]:
        """Entries matching the current query (all of them, not only the rendered ones)."""

        return list(self._visible)

    def current(self) -> PaletteCommand | None:
        return self._visible[self._index] if 0 <= self._index < len(self._visible) else None

    def run_current(self) -> None:
        command = self.current()
        if command is not None:
            self._run(command)

    def _run(self, command: PaletteCommand) -> None:
        self.close_palette()
        self.run_requested.emit(command)

    # ----------------------------------------------------------------- list
    def _refilter(self, query: str) -> None:
        self._visible = filter_commands(self._commands, query)
        self._index = 0
        clear_layout(self.list_lay)
        self._rows = []
        for command in self._visible[:MAX_VISIBLE]:
            row = RowBtn(padding=(16, 10, 16, 10), gap=10)
            row.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            row.add(L(command.label, text(13.5), C.ink), 1)
            row.add(Label(command.group))
            row.clicked.connect(lambda checked=False, item=command: self._run(item))
            self.list_lay.addWidget(row)
            self._rows.append(row)
        if not self._visible:
            empty = L("No matching command.", text(13.5), C.ink3)
            empty.setContentsMargins(16, 16, 16, 16)
            self.list_lay.addWidget(empty)
        elif len(self._visible) > MAX_VISIBLE:
            more = Label(f"{len(self._visible) - MAX_VISIBLE} more · keep typing to narrow the list")
            more.setContentsMargins(16, 10, 16, 10)
            self.list_lay.addWidget(more)
        self._highlight()
        self._fit()

    def _move(self, step: int) -> None:
        if self._rows:
            self._index = max(0, min(len(self._rows) - 1, self._index + step))
            self._highlight()

    def _highlight(self) -> None:
        for index, row in enumerate(self._rows):
            row.set_on(index == self._index)
        if self._rows:
            self.scroll.ensureWidgetVisible(self._rows[self._index], 0, 0)

    # ------------------------------------------------------------- geometry
    def _fit(self) -> None:
        parent = self.parentWidget()
        if parent is None:
            return
        self.setGeometry(parent.rect())
        self.panel.setFixedWidth(min(560, max(300, self.width() - 32)))
        self.list_host.adjustSize()
        overhead = self.panel.layout().minimumSize().height() - self.scroll.minimumHeight()
        room = max(48, self.height() - overhead - 32)
        wanted = min(380, room, max(48, self.list_lay.sizeHint().height()))
        self.scroll.setFixedHeight(wanted)
        self.panel.adjustSize()
        top = min(96, max(16, self.height() - self.panel.height() - 16))
        self.panel.move(max(0, (self.width() - self.panel.width()) // 2), top)

    def eventFilter(self, watched, event):
        if watched is self.parentWidget() and event.type() == QEvent.Type.Resize and self.isVisible():
            self._fit()
        return False

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.fillRect(self.rect(), qc("rgba(20,20,19,.35)"))
        p.end()

    def mousePressEvent(self, event) -> None:
        if not self.panel.geometry().contains(event.position().toPoint()):
            self.close_palette()
        event.accept()


__all__ = ["CommandPalette", "GROUPS", "OPERATION_ENTRIES", "PALETTE_PROVIDERS", "PaletteCommand",
           "filter_commands", "palette_commands", "register_palette_provider"]
