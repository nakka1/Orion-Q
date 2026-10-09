"""Journal · terminal: backend records and the typed-command grammar.

The list shows records the BACKEND delivered in the snapshot — the command
lifecycle (`snap.ledger.commands`) and the ledger entries (`snap.ledger.entries`).
The UI never writes a row of its own into that list. What the operator types is
answered in a separate, differently styled feedback strip ("INPUT" rows): it is
feedback about the input, not a record.

A typed line is parsed into `verb target` and looked up in `TERMINAL_VERBS`.
Every verb runs through the same window methods as the buttons (`_command`,
`show_screen`, `select_node`, …), after the source's capabilities allow it.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Callable

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QFrame, QLineEdit, QScrollArea, QSizePolicy, QWidget

from ..theme import C, current_theme, mono, text_tone
from .prims import Clickable, Frame, L, Label, hbox, set_color, vbox


# ----------------------------------------------------------------- records
@dataclass(frozen=True)
class JournalRow:
    at: str
    text: str
    status: str
    tone: str = "ink"
    origin: str = "ledger"          # "ledger" | "command" (backend) · "input" (UI feedback)


COMMAND_STATE_TONE = {"CONFIRMED": "green", "REJECTED": "red", "UNKNOWN": "red",
                      "REQUESTED": "amber", "RUNNING": "amber"}
MAX_ROWS = 60


def _clock(value: str) -> str:
    try:
        return datetime.fromisoformat(value).strftime("%H:%M:%S")
    except (TypeError, ValueError):
        return (value or "")[11:19] or (value or "")


def journal_rows(snapshot, limit: int = MAX_ROWS) -> list[JournalRow]:
    """Backend records of a snapshot, oldest first (newest last).

    The two backend lists arrive newest-first. Each keeps its own order; they
    are interleaved by their clock strings. Nothing is synthesised.
    """

    if snapshot is None:
        return []
    ledger = snapshot.ledger
    entries = [JournalRow(e.at, f"{e.actor} · {e.action}", (e.category or "").upper(), e.tone, "ledger")
               for e in reversed(list(ledger.entries))]
    commands = [JournalRow(_clock(c.requested_at), f"{c.verb} · {c.command_id[:8]}", str(c.state).upper(),
                           COMMAND_STATE_TONE.get(str(c.state).upper(), "muted"), "command")
                for c in reversed(list(ledger.commands))]
    rows: list[JournalRow] = []
    i = j = 0
    while i < len(entries) or j < len(commands):
        if j >= len(commands) or (i < len(entries) and entries[i].at <= commands[j].at):
            rows.append(entries[i])
            i += 1
        else:
            rows.append(commands[j])
            j += 1
    return rows[-limit:]


# ----------------------------------------------------------------- grammar
@dataclass(frozen=True)
class Feedback:
    """Answer to a typed line. Never a backend record."""

    text: str
    status: str = "OK"
    tone: str = "muted"

    @property
    def ok(self) -> bool:
        return self.status not in {"ERR", "REFUSED"}


@dataclass(frozen=True)
class TerminalVerb:
    name: str
    usage: str
    run: Callable[[object, str], "Feedback | None"]
    operation: str | Callable[[object], "str | None"] | None = None
    summary: str = ""
    aliases: tuple[str, ...] = ()


TERMINAL_VERBS: dict[str, TerminalVerb] = {}

SCREEN_NAMES = {"topology": "topology", "telemetry": "telemetry", "keys": "keys", "nodes": "node",
                "node": "node", "edge": "fpga", "fpga": "fpga", "audit": "audit"}


def register_verb(verb: TerminalVerb) -> TerminalVerb:
    """Add or replace a terminal verb (and its aliases)."""

    TERMINAL_VERBS[verb.name] = verb
    for alias in verb.aliases:
        TERMINAL_VERBS[alias] = verb
    return verb


def parse_command(line: str) -> tuple[str, str] | None:
    """`/Verb  target words` → ("verb", "target words"); empty input → None."""

    stripped = str(line or "").strip().lstrip("/").strip()
    if not stripped:
        return None
    parts = stripped.split()
    return parts[0].lower(), " ".join(parts[1:])


def normalise(value: str) -> str:
    return "".join(ch for ch in str(value or "").upper() if ch.isalnum())


def find_node(snapshot, target: str):
    wanted = normalise(target)
    if snapshot is None or not wanted:
        return None
    return next((node for node in snapshot.topology.nodes if normalise(node.id) == wanted), None)


def link_name(link) -> str:
    return link.id or f"{link.a}↔{link.b}"


def find_link(snapshot, target: str):
    wanted = normalise(target)
    if snapshot is None or not wanted:
        return None
    for link in snapshot.topology.links:
        if wanted in {normalise(link.id), normalise(link.label), normalise(f"{link.a}{link.b}"),
                      normalise(f"{link.b}{link.a}")} - {""}:
            return link
    return None


def required_operation(verb: TerminalVerb, window) -> str | None:
    operation = verb.operation
    return operation(window) if callable(operation) else operation


def run_text(window, line: str) -> Feedback | None:
    """Parse and dispatch one typed line. Returns the feedback to show."""

    parsed = parse_command(line)
    if parsed is None:
        return None
    name, target = parsed
    verb = TERMINAL_VERBS.get(name)
    if verb is None:
        return Feedback(f"cannot dispatch: {line.strip()} · type help", "ERR", "red")
    snapshot = window.snapshot
    operation = required_operation(verb, window)
    if operation is not None:
        capabilities = snapshot.capabilities if snapshot is not None else None
        if capabilities is None or not capabilities.allows(operation):
            adapter = f"{capabilities.adapter_id} ({capabilities.mode})" if capabilities else "no source"
            return Feedback(f"{verb.name}: not available for {adapter} · requires {operation}", "REFUSED", "red")
        if getattr(window, "_busy", False) and operation != "cancel":
            return Feedback("Wait for the current operation to finish.", "REFUSED", "red")
    try:
        result = verb.run(window, target)
    except ValueError as exc:
        return Feedback(f"{verb.name}: {exc}", "ERR", "red")
    if result is not None:
        return result
    return Feedback(" ".join(part for part in (verb.name, target) if part),
                    "DISPATCHED" if operation else "OK", "muted")


# ---- built-in verbs -------------------------------------------------------
def _help(_window, _target):
    seen, usages = set(), []
    for verb in TERMINAL_VERBS.values():
        if verb.name not in seen:
            seen.add(verb.name)
            usages.append(verb.usage)
    return Feedback("verbs: " + " · ".join(usages), "HELP", "muted")


def _go(window, target):
    key = SCREEN_NAMES.get(target.strip().lower())
    if key is None:
        return Feedback(f"unknown screen: {target or '—'} (topology · telemetry · keys · nodes · edge · audit)", "ERR", "red")
    window.show_screen(key)
    return None


def _theme(window, target):
    wanted = target.strip().lower() or ("light" if current_theme() == "dark" else "dark")
    if wanted not in {"dark", "light"}:
        return Feedback(f"unknown theme: {target} (dark · light)", "ERR", "red")
    if not window.set_theme(wanted):
        return Feedback("theme unchanged · finish the running operation or close open dialogs first", "REFUSED", "red")
    return Feedback(f"theme {wanted}", "OK", "muted")


def _select_node(window, target):
    node = find_node(window.snapshot, target)
    if node is None:
        return Feedback(f"unknown node: {target or '—'}", "ERR", "red")
    window.select_node(node.id)
    return Feedback(f"select_node {node.id}", "OK", "muted")


def _select_link(window, target):
    link = find_link(window.snapshot, target)
    if link is None:
        return Feedback(f"unknown link: {target or '—'}", "ERR", "red")
    window.select_link(link_name(link))
    return Feedback(f"select_link {link_name(link)}", "OK", "muted")


def _dispatch(verb: str):
    def run(window, target):
        window._command(verb, target)
        return None
    return run


def _node_action_operation(action: str):
    def operation(window):
        snapshot = window.snapshot
        if snapshot is not None and snapshot.capabilities.allows("apply_simulated_node_action"):
            return "apply_simulated_node_action"
        return action if action == "recalibrate" else "apply_simulated_node_action"
    return operation


def _node_action(action: str):
    def run(window, target):
        snapshot = window.snapshot
        node = find_node(snapshot, target)
        if node is None:
            return Feedback(f"unknown node: {target or '—'}", "ERR", "red")
        if snapshot.capabilities.allows("apply_simulated_node_action"):
            from dataclasses import asdict
            from quantum_autopilot.control_plane.inventory import SimulatedNodeAction
            request = SimulatedNodeAction(action, node.id, snapshot.topology.version, snapshot.topology.id)
            window._command("apply_simulated_node_action", json.dumps(asdict(request)))
        else:
            window._command(action, node.id)
        return Feedback(f"{action} {node.id}", "DISPATCHED", "muted")
    return run


def _capabilities(window, _target):
    window.show_capabilities()
    return None


def _sign_out(window, _target):
    window.show_screen("boot")
    return None


for _verb in (
    TerminalVerb("help", "help", _help, summary="List the verbs"),
    TerminalVerb("go", "go <screen>", _go, summary="Open a screen"),
    TerminalVerb("theme", "theme [dark|light]", _theme, summary="Switch the theme"),
    TerminalVerb("select_node", "select_node <id>", _select_node, summary="Inspect a node"),
    TerminalVerb("select_link", "select_link <id>", _select_link, summary="Inspect a link"),
    TerminalVerb("run_scenario", "run_scenario", _dispatch("run_scenario"), "run_scenario"),
    TerminalVerb("replay_model", "replay_model", _dispatch("replay_model"), "replay_model"),
    TerminalVerb("export", "export [scope]", _dispatch("export"), "export_record"),
    TerminalVerb("reload", "reload", _dispatch("reload"), "reload"),
    TerminalVerb("recalibrate", "recalibrate <node>", _node_action("recalibrate"), _node_action_operation("recalibrate")),
    TerminalVerb("isolate", "isolate <node>", _node_action("isolate"), _node_action_operation("isolate")),
    TerminalVerb("pause", "pause", _dispatch("pause"), "pause"),
    TerminalVerb("dropout", "dropout", _dispatch("dropout"), "dropout"),
    TerminalVerb("cancel", "cancel", _dispatch("cancel"), "cancel"),
    TerminalVerb("capabilities", "capabilities", _capabilities),
    TerminalVerb("sign_out", "sign_out", _sign_out, aliases=("signout",)),
):
    register_verb(_verb)


# ------------------------------------------------------------------ widget
class PromptEdit(QLineEdit):
    """Terminal prompt: Enter dispatches, ↑/↓ recall, Ctrl+K opens the palette."""

    palette_requested = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.history: list[str] = []
        self._cursor = 0
        self.setFont(mono(13))
        self.setFrame(False)
        self.setAccessibleName("Command")
        self.setStyleSheet(f"QLineEdit{{background:transparent;border:0;color:{C.ink};padding:4px 0;}}"
                           f"QLineEdit:focus{{border:0;}}")

    def remember(self, line: str) -> None:
        if line and (not self.history or self.history[-1] != line):
            self.history.append(line)
            del self.history[:-50]
        self._cursor = len(self.history)

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_K and event.modifiers() == Qt.KeyboardModifier.ControlModifier:
            self.palette_requested.emit()
            event.accept()
            return
        if event.key() in (Qt.Key.Key_Up, Qt.Key.Key_Down) and self.history:
            step = -1 if event.key() == Qt.Key.Key_Up else 1
            self._cursor = max(0, min(len(self.history), self._cursor + step))
            self.setText(self.history[self._cursor] if self._cursor < len(self.history) else "")
            event.accept()
            return
        super().keyPressEvent(event)


class _RowWidget(QWidget):
    """One journal line: time, text (elided, full text in the tooltip), status."""

    def __init__(self, row: JournalRow) -> None:
        super().__init__()
        lay = hbox(self, (0, 0, 0, 0), 16)
        self.at = L("", mono(12.5), C.ink3)
        self.at.setFixedWidth(70)
        lay.addWidget(self.at)
        self.body = L("", mono(12.5), C.ink2, elide=True)
        self.body.setMinimumWidth(0)
        self.body.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        lay.addWidget(self.body, 1)
        self.status = L("", mono(12.5), C.ink)
        lay.addWidget(self.status)
        self._shown: JournalRow | None = None
        self._local: bool | None = None
        self._tone = None
        self.show_row(row)

    def show_row(self, row: JournalRow) -> None:
        if row == self._shown:
            return
        self._shown = row
        local = row.origin == "input"
        at = "INPUT" if local else row.at
        if self.at.text() != at:
            self.at.setText(at)
        body = ("› " if local else "") + row.text
        if self.body.full_text != body:
            self.body.setText(body)
        if self.status.text() != row.status:
            self.status.setText(row.status)
        if local != self._local:
            self._local = local
            set_color(self.at, C.ink4 if local else C.ink3)
            set_color(self.body, C.ink3 if local else C.ink2)
            self.setToolTip("Console input feedback · not a backend record" if local else "")
        if row.tone != self._tone:
            self._tone = row.tone
            set_color(self.status, text_tone(row.tone))


class JournalPanel(Frame):
    """Resizable journal list, input-feedback strip and command prompt."""

    submitted = Signal(str)
    toggled = Signal(bool)
    palette_requested = Signal()
    close_requested = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(C.deep, C.line, sides="t", parent=parent)
        self._rows: list[JournalRow] = []
        self._feedback: list[JournalRow] = []
        self._row_widgets: list[_RowWidget] = []
        self._feedback_widgets: list[_RowWidget] = []
        self._open = True
        root = vbox(self, (0, 0, 0, 0), 0)

        self.head = QWidget()
        self.head.setFixedHeight(34)
        hl = hbox(self.head, (18, 3, 18, 3), 12)
        self.toggle = Clickable("▾ Journal · terminal")
        self.toggle.setFont(Label("").font())
        self.toggle.setStyleSheet(f"QPushButton{{color:{C.ink};border:0;background:transparent;padding:0;text-align:left;}}")
        self.toggle.setAccessibleName("Collapse or expand the journal and terminal")
        self.toggle.setToolTip("Collapse or expand the terminal while keeping its header visible.")
        self.toggle.clicked.connect(lambda: self.set_open(not self._open))
        hl.addWidget(self.toggle)
        hl.addStretch(1)
        hl.addWidget(Label("type help · Enter dispatches"))
        self.close_button = Clickable("×")
        self.close_button.setFixedSize(28, 26)
        self.close_button.setFont(mono(18))
        self.close_button.setStyleSheet(
            f"QPushButton{{color:{C.ink3};border:0;background:transparent;}}"
            f"QPushButton:hover{{color:{C.ink};background:{C.hover};}}")
        self.close_button.setAccessibleName("Close terminal")
        self.close_button.setToolTip("Close the terminal and give its space to the screen. Reopen with Terminal or Ctrl+J; history is kept.")
        self.close_button.clicked.connect(self.close_requested.emit)
        hl.addWidget(self.close_button)
        root.addWidget(self.head)

        self.body = QWidget()
        bl = vbox(self.body, (0, 0, 0, 0), 0)
        self.list_host = QWidget()
        self.list_lay = vbox(self.list_host, (18, 0, 18, 0), 2)
        self.list_lay.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setMinimumHeight(32)
        self.scroll.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.scroll.setWidget(self.list_host)
        self.scroll.setAccessibleName("Journal records from the data source")
        self._stick = True      # follow the newest record until the operator scrolls up
        self.scroll.verticalScrollBar().rangeChanged.connect(self._range_changed)
        self.scroll.verticalScrollBar().valueChanged.connect(self._value_changed)
        bl.addWidget(self.scroll, 1)
        self.empty = L("No journal records from this source yet.", mono(12.5), C.ink3)
        self.list_lay.addWidget(self.empty)

        self.feedback_host = QWidget()
        self.feedback_lay = vbox(self.feedback_host, (18, 4, 18, 0), 2)
        self.feedback_host.setAccessibleName("Console input feedback, not a backend record")
        self.feedback_host.hide()
        bl.addWidget(self.feedback_host)

        self.prompt_row = QWidget()
        self.prompt_row.setFixedHeight(40)
        pl = hbox(self.prompt_row, (18, 6, 18, 8), 10)
        pl.addWidget(L("›", mono(13), C.acc_t))
        self.prompt = PromptEdit()
        self.prompt.setPlaceholderText("run_scenario · select_node <id> · go telemetry · help")
        self.prompt.returnPressed.connect(self._submit)
        self.prompt.palette_requested.connect(self.palette_requested.emit)
        pl.addWidget(self.prompt, 1)
        bl.addWidget(self.prompt_row)
        root.addWidget(self.body, 1)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    # ---------------------------------------------------------------- state
    @property
    def is_open(self) -> bool:
        return self._open

    def set_open(self, value: bool) -> None:
        value = bool(value)
        if value == self._open:
            return
        self._open = value
        self.body.setVisible(value)
        self.setMaximumHeight(16777215 if value else self.head.height())
        self.toggle.setText(("▾" if value else "▸") + " Journal · terminal")
        self.toggled.emit(value)

    def rows(self) -> list[JournalRow]:
        return list(self._rows)

    def feedback(self) -> list[JournalRow]:
        return list(self._feedback)

    def _submit(self) -> None:
        line = self.prompt.text()
        self.prompt.clear()
        if line.strip():
            self.prompt.remember(line.strip())
            self.submitted.emit(line)

    # ----------------------------------------------------------------- rows
    @staticmethod
    def _row(row: JournalRow) -> QWidget:
        return _RowWidget(row)

    @staticmethod
    def _sync_rows(layout, widgets: list, rows: list) -> None:
        """Make `widgets` show `rows`: texts are updated in place and only the difference in
        length is created or deleted (a journal that scrolls by one record per second must not
        rebuild sixty styled rows each time)."""

        while len(widgets) > len(rows):
            widget = widgets.pop()
            layout.removeWidget(widget)
            widget.hide()
            widget.setParent(None)
            widget.deleteLater()
        for index, row in enumerate(rows):
            if index < len(widgets):
                widgets[index].show_row(row)
            else:
                widget = _RowWidget(row)
                widgets.append(widget)
                layout.addWidget(widget)

    def set_rows(self, rows: list[JournalRow]) -> None:
        """Show backend records. A no-op when nothing changed (steady polling)."""

        rows = list(rows)
        if rows == self._rows:
            return
        self._rows = rows
        if self.empty is not None and rows:
            self.list_lay.removeWidget(self.empty)
            self.empty.hide()
            self.empty.setParent(None)
            self.empty.deleteLater()
            self.empty = None
        self._sync_rows(self.list_lay, self._row_widgets, rows)
        if not rows and self.empty is None:
            self.empty = L("No journal records from this source yet.", mono(12.5), C.ink3)
            self.list_lay.addWidget(self.empty)
        self._stick = True

    def set_feedback(self, rows: list[JournalRow]) -> None:
        rows = list(rows)[-3:]
        if rows == self._feedback:
            return
        self._feedback = rows
        self._sync_rows(self.feedback_lay, self._feedback_widgets, rows)
        self.feedback_host.setVisible(bool(rows))

    def _range_changed(self, _minimum: int, maximum: int) -> None:
        if self._stick:
            self.scroll.verticalScrollBar().setValue(maximum)

    def _value_changed(self, value: int) -> None:
        self._stick = value >= self.scroll.verticalScrollBar().maximum()


short_clock = _clock


__all__ = ["COMMAND_STATE_TONE", "Feedback", "JournalPanel", "JournalRow", "PromptEdit", "SCREEN_NAMES",
           "TERMINAL_VERBS", "TerminalVerb", "find_link", "find_node", "journal_rows", "link_name",
           "normalise", "parse_command", "register_verb", "required_operation", "run_text"]
