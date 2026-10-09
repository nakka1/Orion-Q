"""Keys · "Key management".

Layout of the approved prototype: a four-stage pipeline card, the key-pool cell
grid beside the finite-key card, the session table (selectable row with a detail
box) beside consumers and source provenance.

Data:

* `snap.keys` is the reviewed QKD assurance data of the source (published
  operating grid, campaigns, example assessment, provenance). It is rendered as
  delivered, and each card says so when the source supplies nothing;
* with a network engine (`snap.live`), the first section is the live QKD panel:
  the decoy-state BB84 model of the backend running on each fibre.

The prototype's vault / HSM / policy line and its fixed finite-key numbers have
no backend counterpart and are not shown.
"""
from __future__ import annotations

from PySide6.QtWidgets import QWidget

from ..contract import Snapshot
from ..theme import C, mono, tone
from ..widgets import Btn, Card, Cells, L, Label, hbox, vbox
from ..widgets.cards import (
    NOT_SUPPLIED, KVList, MeterList, Page, grid_row, head_row, mono_header, note, set_text,
)
from ..widgets.live_qkd import Pipeline
from ..widgets.prims import clear_layout, scoped_style
from ..widgets.records import RecordTable
from ..widgets.evidence_export import choose_evidence_export
from .base import Screen

SESSION_HEADERS = ["SESSION", "LINK", "RATE", "QBER", "STATE", "SOURCE HASH"]


def _is_number(value: str) -> bool:
    try:
        float(value)
    except ValueError:
        return False
    return True


class KeysScreen(Screen):
    key = "keys"
    title = "QKD EVIDENCE"
    heading = "Key management"
    status = ""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        header = self.add_header()
        self.reload_button = Btn("Reload source", variant="default")
        self.reload_button.setProperty("operation", "reload")
        self.reload_button.clicked.connect(lambda: self.command.emit("reload", ""))
        self.export_button = Btn("Export evidence", variant="accent")
        self.export_button.setProperty("operation", "export_record")
        self.export_button.clicked.connect(lambda: choose_evidence_export(self, 'qkd'))
        header.actions.addWidget(self.reload_button)
        header.actions.addWidget(self.export_button)

        self._sessions: list = []
        self._session_id = ""
        self._fibre = ""
        self.live_panel = None

        self.page = Page()
        self.root.addWidget(self.page, 1)

        from ..widgets.laboratory import LaboratoryPanel
        self.laboratory_panel = LaboratoryPanel()
        self.laboratory_panel.command.connect(self.command.emit)
        self.page.add(self.laboratory_panel)

        # Live QKD section: built the first time a snapshot carries live state.
        self.live_host = QWidget()
        self.live_lay = vbox(self.live_host, (0, 0, 0, 0), 16)
        self.live_host.setVisible(False)
        self.page.add(self.live_host)

        # ------------------------------------------------------- pipeline
        pipe = Card()
        self.pipeline_title = Label("Assessment pipeline", wrap=True)
        pipe.add(self.pipeline_title)
        self.pipeline = Pipeline(count=0)
        pipe.add(self.pipeline)
        self.pipeline_empty = note(NOT_SUPPLIED, 12.5, C.ink3)
        pipe.add(self.pipeline_empty)
        self.page.add(pipe)

        # ------------------------------------------------- pool · example
        pool = Card()
        self.pool_title = Label("Key pool", wrap=True)
        self.pool_legend_host = QWidget()
        self.pool_legend_lay = hbox(self.pool_legend_host, (0, 0, 0, 0), 14)
        pool.add(head_row(self.pool_title, self.pool_legend_host))
        self.pool = Cells([], columns=32, cell_h=14, gap=3)
        pool.add(self.pool)
        ends = QWidget()
        el = hbox(ends, (0, 0, 0, 0), 0)
        self.pool_start = L("", mono(11), C.ink3)
        self.pool_end = L("", mono(11), C.ink3)
        el.addWidget(self.pool_start)
        el.addStretch(1)
        el.addWidget(self.pool_end)
        pool.add(ends)
        self.pool_empty = note(NOT_SUPPLIED, 12.5, C.ink3)
        pool.add(self.pool_empty)
        pool.lay.addStretch(1)
        self._legend: list | None = None

        finite = Card()
        self.finite_title = Label("Example assessment")
        finite.add(self.finite_title)
        self.finite = KVList()
        finite.add(self.finite)
        self.scope_note = note("")
        finite.add(self.scope_note)
        finite.lay.addStretch(1)
        self.page.add(grid_row([(pool, 2), finite]))

        # --------------------------------------------- sessions · consumers
        sessions = Card()
        self.sessions_title = Label("Key sessions")
        sessions.add(self.sessions_title)
        self.session_scroll = RecordTable(SESSION_HEADERS)
        self.session_scroll.setAccessibleName("QKD sessions and published campaigns")
        self.session_scroll.setFixedHeight(250)
        mono_header(self.session_scroll)
        self.session_scroll.clicked.connect(self._session_clicked)
        sessions.add(self.session_scroll)
        detail = QWidget()
        scoped_style(detail, f"background:{C.bg};border:1px solid {C.soft};")
        dl = vbox(detail, (12, 12, 12, 12), 4)
        self.session_head = Label("")
        dl.addWidget(self.session_head)
        self.session_detail = note("", 13)
        dl.addWidget(self.session_detail)
        self.sessions_note = self.session_detail
        sessions.add(detail)

        side = Card()
        side.add(Label("Consumers"))
        self.consumers = MeterList(mono_label=True)
        side.add(self.consumers)
        self.consumers_empty = note(NOT_SUPPLIED, 12.5, C.ink3)
        side.add(self.consumers_empty)
        side.add(Label("Source provenance"))
        self.provenance = KVList(size=12.5)
        side.add(self.provenance)
        self.provenance_empty = note(NOT_SUPPLIED, 12.5, C.ink3)
        side.add(self.provenance_empty)
        side.lay.addStretch(1)
        self.page.add(grid_row([(sessions, 2), side]))

    # ---------------------------------------------------------- view state
    def save_state(self) -> dict:
        state = super().save_state()
        state.update(session=self._session_id, fibre=self._fibre)
        return state

    def restore_state(self, state: dict) -> None:
        super().restore_state(state)
        self._session_id = state.get("session", "")
        self._fibre = state.get("fibre", "")
        if self._snap is not None:
            self.refresh(self._snap)

    def on_selection(self, selection) -> None:
        if getattr(selection, "kind", None) == "link" and selection.id:
            self._fibre = selection.id
            if self.live_panel is not None:
                self.live_panel.set_current(selection.id)

    def cache_selection(self, selection) -> None:
        if getattr(selection, "kind", None) == "link" and selection.id:
            self._fibre = selection.id

    def _fibre_picked(self, link_id: str) -> None:
        self._fibre = link_id
        self.select.emit("link", link_id)

    def _session_clicked(self, index) -> None:
        if 0 <= index.row() < len(self._sessions):
            self._session_id = self._sessions[index.row()].id
            self._render_session()

    # ------------------------------------------------------------ builders
    def _set_pool_legend(self, rows) -> None:
        rows = list(rows)
        if rows == self._legend:
            return
        self._legend = rows
        clear_layout(self.pool_legend_lay)
        for label, name in rows:
            self.pool_legend_lay.addWidget(Label(f"■ {label}", tone(name)))

    def _render_session(self) -> None:
        selected = next((item for item in self._sessions if item.id == self._session_id), None)
        body = self._snap.keys.sessions_note if self._snap is not None else ""
        if selected is None:
            set_text(self.session_head, "No session selected" if self._sessions else "No sessions")
            set_text(self.session_detail, body or NOT_SUPPLIED)
            return
        set_text(self.session_head, f"{selected.id} · {selected.link}")
        set_text(self.session_detail,
                 f"State {selected.state} · rate {selected.skr} · QBER {selected.qber} · source hash "
                 f"{selected.fingerprint}." + (f" {body}" if body else ""))

    def _ensure_live_panel(self):
        if self.live_panel is None:
            from ..widgets.live_qkd import LiveQkdPanel
            self.live_panel = LiveQkdPanel()
            self.live_panel.link_picked.connect(self._fibre_picked)
            self.live_lay.addWidget(self.live_panel)
            if self._fibre:
                self.live_panel.set_current(self._fibre)
        return self.live_panel

    # --------------------------------------------------------------- state
    def refresh(self, snap: Snapshot) -> None:
        self.laboratory_panel.set_snapshot(snap)
        k = snap.keys
        artificial = snap.campaign.startswith("synthetic-")
        measured = snap.input_kind == "measured"

        if snap.live is not None:
            panel = self._ensure_live_panel()
            # A hidden screen stores selection without rebuilding its table.
            # Apply it with this single latest-state presentation on entry.
            if self._fibre:
                panel.set_current(self._fibre, presentation=False)
            panel.set_live(snap.live)
            self._fibre = panel.current()
        self.live_host.setVisible(snap.live is not None)

        set_text(self.pipeline_title, f"Assessment pipeline · {k.pipeline_link}")
        self.pipeline.set_count(len(k.pipeline))
        for stage, item in zip(self.pipeline.stages, k.pipeline):
            stage.set(f"{item.index} · {item.name}", item.value, item.unit, item.note,
                      "violet" if item.emphasis and item.tone == "ink" else item.tone, _is_number(item.value))
        self.pipeline.setVisible(bool(k.pipeline))
        self.pipeline_empty.setVisible(not k.pipeline)
        set_text(self.pipeline_empty, f"{NOT_SUPPLIED} · {k.scope_note}" if k.scope_note and not k.pipeline
                 else NOT_SUPPLIED)

        set_text(self.pool_title, k.pool_label)
        self.pool.columns = max(32, -(-len(k.pool_blocks) // 8)) if len(k.pool_blocks) > 256 else 32
        self.pool.gap = 1 if self.pool.columns > 100 else 3
        self.pool.set_tones(list(k.pool_blocks))
        self.pool.setVisible(bool(k.pool_blocks))
        self.pool_empty.setVisible(not k.pool_blocks)
        self._set_pool_legend([(f"{v.key}: {v.value}", v.tone) for v in k.pool_legend])
        if measured:
            start, end = "No key material supplied", "Read only"
        elif artificial:
            start, end = "Block 001 · artificial", f"Block {len(k.pool_blocks)}"
        else:
            start, end = "Published cells", "Grid aggregates"
        set_text(self.pool_start, start if k.pool_blocks or measured else "")
        set_text(self.pool_end, end if k.pool_blocks or measured else "")

        self.finite.set_rows([(v.key, v.value, "amber" if v.key == "Attack model" else v.tone) for v in k.finite_key])
        set_text(self.scope_note, k.scope_note)

        set_text(self.sessions_title, "Measured QKD samples" if measured else
                 "Synthetic key sessions" if artificial else "Published campaigns")
        self._sessions = list(k.sessions)
        self.session_scroll.set_rows([
            [(v.id, C.ink), (v.link, tone(v.tone)), (v.skr, C.ink),
             (v.qber, tone(v.tone) if v.tone == "amber" else C.ink), (v.state, tone(v.tone)),
             (v.fingerprint, C.violet if v.fingerprint != "—" else C.ink3)]
            for v in k.sessions])
        self.session_scroll.setFixedHeight(min(250, 44 + 34 * max(1, len(k.sessions))))
        if self._session_id and all(item.id != self._session_id for item in self._sessions):
            self._session_id = ""
        self._render_session()

        self.consumers.set_rows([(v.label, v.value, v.fraction, "violet") for v in k.consumers])
        self.consumers_empty.setVisible(not k.consumers)
        self.provenance.set_rows([(v.key, v.value, v.tone) for v in k.provenance])
        self.provenance_empty.setVisible(not k.provenance)
        self.bar.set_status(k.status, C.violet)


__all__ = ["KeysScreen"]
