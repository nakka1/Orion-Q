"""Audit · "Access & audit".

Layout of the approved prototype: a row of cards, the record table with filter
segments, and a side column (verification, sessions, export, evidence lanes).

Data: everything is `snap.ledger`. The backend has no hash chain, no roles and
no multi-user sessions, so the prototype's role cards and active-session list are
not reproduced:

* the top cards are the source's evidence lanes (`ledger.roles` as delivered);
* "chain verification" is the artefact-registry validation the ledger reports;
* "active sessions" is the journal overview (`ledger.sessions`) with the entry
  point of the command history / reconciliation window;
* the filters are the record kinds that exist: the ledger categories, the engine
  decisions and events (matched by their own hashes and ids in `snap.live`) and
  the tracked console commands (`ledger.commands`).
"""
from __future__ import annotations

from PySide6.QtCore import QEvent, QTimer
from PySide6.QtWidgets import QGridLayout, QWidget

from ..contract import Snapshot
from ..theme import C, mono, tone
from ..widgets import Btn, Card, Chip, L, Label, Seg, Track, hbox
from ..widgets.cards import KVList, Page, clock_of, grid_row, mono_header, note, set_text, stack_column
from ..widgets.prims import clear_layout
from ..widgets.records import RecordTable
from ..widgets.evidence_export import choose_evidence_export
from .base import Screen

FILTERS = [("ALL", None), ("CALIBRATION", "calibration"), ("KEY", "key"), ("EXPORT", "export")]
LIVE_FILTERS = ("DECISIONS", "EVENTS")
COMMAND_FILTER = "COMMANDS"
COMMAND_TONE = {"CONFIRMED": "green", "UNKNOWN": "red", "REJECTED": "red"}
UNSIGNED = "Unsigned: this local session has no signing key or hardware token."


class AuditScreen(Screen):
    key = "audit"
    title = "ACCESS & AUDIT"
    heading = "Access & audit"
    status = ""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.add_header()
        self._entries: list = []
        self._commands: tuple = ()
        self._decision_hashes: set[str] = set()
        self._event_ids: set[str] = set()
        self._filter = "ALL"
        self._lanes: list | None = None
        from ..widgets.command_history import CommandHistory
        self.command_history = CommandHistory(self)

        self.page = Page()
        self.root.addWidget(self.page, 1)

        # --------------------------------------------------- evidence lanes
        self.page.add(Label("Evidence lanes"))
        self.role_host = QWidget()
        self.role_lay = QGridLayout(self.role_host)
        self.role_lay.setContentsMargins(0, 0, 0, 0)
        self.role_lay.setSpacing(16)
        self.page.add(self.role_host)

        # ---------------------------------------------------------- ledger
        ledger = Card()
        head = QWidget()
        hl = hbox(head, (0, 0, 0, 0), 16)
        hl.addWidget(Label("Local audit records"), 1)
        self.filters: dict[str, Seg] = {}
        for label in (*(name for name, _category in FILTERS), *LIVE_FILTERS, COMMAND_FILTER):
            item = Seg(label)
            item.clicked.connect(lambda checked=False, name=label: self.set_filter(name))
            hl.addWidget(item)
            self.filters[label] = item
        ledger.add(head)
        self.entry_scroll = RecordTable(["TIME", "ACTOR", "ACTION", "ENTRY HASH"], identity_columns=(3,))
        self.entry_scroll.setAccessibleName("Local audit records")
        self.entry_scroll.setFixedHeight(430)
        header = self.entry_scroll.horizontalHeader()
        header.setSectionResizeMode(header.ResizeMode.Interactive)
        self.entry_scroll.setColumnWidth(0, 96)
        self.entry_scroll.setColumnWidth(1, 190)
        self.entry_scroll.setColumnWidth(3, 190)
        header.setSectionResizeMode(2, header.ResizeMode.Stretch)
        mono_header(self.entry_scroll)
        ledger.add(self.entry_scroll)
        self.entry_empty = note("No entries of this kind in the current window.", 12.5, C.ink3)
        ledger.add(self.entry_empty)
        self.entry_count = note("", 12, C.ink3)
        ledger.add(self.entry_count)
        ledger.lay.addStretch(1)

        chain = Card()
        chain.add(Label("Artefact registry · validation"))
        self.chain_label = L("", mono(18), C.ink)
        chain.add(self.chain_label)
        self.chain_bar = Track(0.0, "violet")
        chain.add(self.chain_bar)
        self.chain_note = note("")
        chain.add(self.chain_note)
        chain.add(note(UNSIGNED, 12.5, C.ink3))

        sessions = Card()
        sessions.add(Label("Journal overview"))
        self.sessions = KVList(size=12.5)
        sessions.add(self.sessions)
        self.commands_button = Btn("Command center", variant="accent")
        self.commands_button.setProperty("operation", "read_console_commands")
        self.commands_button.setEnabled(False)
        self.commands_button.clicked.connect(self.command_history.show)
        sessions.lay.addWidget(self.commands_button)

        export = Card()
        export.add(Label("Export"))
        self.export = KVList(size=12.5)
        export.add(self.export)
        buttons = QWidget()
        bl = hbox(buttons, (0, 0, 0, 0), 8)
        self.export_button = Btn("Export JSON", variant="accent")
        self.export_button.setProperty("operation", "export_record")
        self.export_button.clicked.connect(lambda: choose_evidence_export(self, 'campaign'))
        self.refresh_button = Btn("Refresh", variant="default")
        self.refresh_button.setProperty("operation", "reload")
        self.refresh_button.clicked.connect(lambda: self.command.emit("reload", ""))
        bl.addWidget(self.export_button)
        bl.addWidget(self.refresh_button)
        bl.addStretch(1)
        export.add(buttons)

        self.page.add(grid_row([(ledger, 2), stack_column([chain, sessions, export])]))
        self.set_filter("ALL")
        self._entry_fit = QTimer(self)
        self._entry_fit.setSingleShot(True)
        self._entry_fit.timeout.connect(self._fit_record_table)
        self.page.viewport().installEventFilter(self)

    def eventFilter(self, watched, event):
        if watched is self.page.viewport() and event.type() == QEvent.Type.Resize:
            self._entry_fit.start(0)
        return super().eventFilter(watched, event)

    def _fit_record_table(self):
        height = min(430, max(64, self.page.viewport().height() - 32))
        if self.entry_scroll.height() != height:
            self.entry_scroll.setFixedHeight(height)

    # ---------------------------------------------------------- view state
    def save_state(self) -> dict:
        state = super().save_state()
        state["filter"] = self._filter
        return state

    def restore_state(self, state: dict) -> None:
        super().restore_state(state)
        if state.get("filter") in self.filters:
            self.set_filter(state["filter"])

    # ------------------------------------------------------------ builders
    def _set_roles(self, rows, lane_chip: str) -> None:
        rows = list(rows)
        key = (rows, lane_chip)
        if key == self._lanes:
            return
        self._lanes = key
        clear_layout(self.role_lay)
        columns = 3 if len(rows) == 3 else 4
        for column in range(4):
            self.role_lay.setColumnStretch(column, 1 if column < columns else 0)
        if not rows:
            self.role_lay.addWidget(note("No evidence lane declared by this source.", 12.5, C.ink3), 0, 0, 1, columns)
            return
        for index, (name, count, body, active) in enumerate(rows):
            card = Card(tone_name="warn" if active else None, gap=6)
            head = QWidget()
            hl = hbox(head, (0, 0, 0, 0), 8)
            title = Label(name, wrap=True)
            hl.addWidget(title, 1)
            hl.addWidget(L(count, mono(13), C.ink))
            card.add(head)
            card.add(note(body))
            if active:
                chip = Chip(lane_chip, "warn")
                card.lay.addWidget(chip)
            card.lay.addStretch(1)
            card.setMinimumWidth(0)
            self.role_lay.addWidget(card, index // columns, index % columns)

    def _rows(self, name: str) -> list:
        if name == COMMAND_FILTER:
            return [[(clock_of(item.requested_at), C.ink3), ("console", C.ink),
                     (f"{item.verb} · {item.state}", tone(COMMAND_TONE.get(item.state, "amber"))),
                     (item.command_id, C.violet)] for item in self._commands]
        wanted = dict(FILTERS).get(name)
        if name == "DECISIONS":
            entries = [entry for entry in self._entries if entry.digest in self._decision_hashes]
        elif name == "EVENTS":
            entries = [entry for entry in self._entries if entry.digest in self._event_ids]
        else:
            entries = [entry for entry in self._entries if wanted is None or entry.category == wanted]
        return [[(entry.at, C.ink3), (entry.actor, C.ink), (entry.action, tone(entry.tone)), (entry.digest, C.violet)]
                for entry in entries]

    def _set_entries(self) -> None:
        rows = self._rows(self._filter)
        self.entry_scroll.set_rows(rows)
        self.entry_empty.setVisible(not rows)
        total = len(self._commands) if self._filter == COMMAND_FILTER else len(self._entries)
        set_text(self.entry_count, f"{len(rows)} of {total} records in the current window · each record carries "
                 "its own hash")

    # -------------------------------------------------------------- state
    def set_filter(self, name: str) -> None:
        self._filter = name
        for label, item in self.filters.items():
            item.set_active(label == name)
        self._set_entries()

    def refresh(self, snap: Snapshot) -> None:
        ledger = snap.ledger
        readable = snap.capabilities.allows("read_console_commands")
        if readable:
            self.command_history.set_records(ledger.commands, ledger.commands_note)
        else:
            self.command_history.set_records((), "Command history unavailable for this source.")
        self._commands = tuple(ledger.commands) if readable else ()
        live = snap.live
        self._decision_hashes = {item.record_sha256[:16] for item in live.decisions} if live is not None else set()
        self._event_ids = {item.id for item in live.events} if live is not None else set()
        for name in LIVE_FILTERS:
            self.filters[name].setVisible(live is not None)
        self.filters[COMMAND_FILTER].setVisible(readable)
        if self.filters[self._filter].isHidden() and self._filter != "ALL":
            self._filter = "ALL"
            for label, item in self.filters.items():
                item.set_active(label == "ALL")
        self._set_roles([(r.name, r.count, r.body, r.active) for r in ledger.roles],
                        "Control-plane ingress" if snap.input_kind in ("service", "demo") else "Active")
        self.sessions.set_rows([(v.key, v.value, v.tone) for v in ledger.sessions])
        self._entries = list(ledger.entries)
        self._set_entries()
        set_text(self.chain_label, ledger.chain_verified)
        self.chain_bar.set_fraction(ledger.chain_fraction)
        set_text(self.chain_note, ledger.chain_note)
        self.export.set_rows([(v.key, v.value, v.tone) for v in ledger.export]
                             + [("Signature", "unsigned · local session", "amber")])
        self.bar.set_status(ledger.status, C.violet)


__all__ = ["AuditScreen", "COMMAND_FILTER", "FILTERS", "LIVE_FILTERS"]
