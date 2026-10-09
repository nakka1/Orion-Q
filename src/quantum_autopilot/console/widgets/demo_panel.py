"""Live demonstration panel: the backend's models acting on the network, as they act.

Shown wherever `Snapshot.live` exists (Edge control embeds it; it is a plain
widget and can be embedded elsewhere). It has four parts, all read from the
snapshot the backend delivered:

* model status — `live.models`: role, model id, state (active / fallback /
  unavailable), in-contract flag, inference and fault counts, last latency and
  the backend's reason;
* decision feed — `live.decisions`, newest first, filtered by kind. A row is a
  decision the backend took and journalled; the panel adds none and reorders
  nothing beyond showing the newest first;
* demands and routes — `live.demands`;
* input events — only when the source declares `inject_input_event` (the
  demonstration): a control that asks the backend to disturb the SIMULATED INPUT
  of one fibre, and the input timeline from `live.events`.

The inject control changes the input and nothing else. What happens next —
detection, key discard, rerouting, admission — is the ordinary backend pipeline
and appears here only when the backend reports it.
"""
from __future__ import annotations

import json
from collections import Counter

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QComboBox, QDoubleSpinBox, QHeaderView, QSpinBox, QWidget

from ..contract import DECISION_KINDS, DEMAND_STATUS_TONE, MODEL_STATE_TONE, SEVERITY_TONE, LiveView, Snapshot
from ..theme import C, mono, tone
from .cards import (
    DASH, FlowLayout, KVList, LogList, ToggleChip, clock_of, fmt_fidelity, fmt_flag, fmt_number, fmt_percent, grid_row, head_row,
    mono_header, note, set_chip, set_text, status_chip,
)
from .prims import Btn, Card, L, Label, hbox, vbox
from .records import RecordTable

MODEL_HEADERS = ["ROLE", "MODEL", "STATE", "IN CONTRACT", "INFERENCES", "FAULTS", "LATENCY", "REASON"]
DECISION_HEADERS = ["TIME", "KIND", "CONTROLLER", "MODEL", "SUBJECT", "SUMMARY", "OUTCOME", "FLAGS"]
KIND_TONE = {"reroute": "green", "no_route": "red", "calibration": "violet", "detector": "amber"}
KIND_CHIP = {"reroute": "ok", "no_route": "bad", "calibration": "sat", "detector": "warn"}
INPUT_TIMELINE_ROWS = 14
INJECT_NOTE = ("Changes the SIMULATED INPUT of one fibre only (raw observables of the demonstration source). "
               "Detection, key discard, rerouting and admission that follow are the real backend pipeline "
               "reacting to that input; they appear in the feed when the backend reports them.")


def decision_flags(decision) -> str:
    flags = []
    if not decision.in_contract:
        flags.append("OUT OF CONTRACT")
    if decision.fallback_used:
        flags.append("FALLBACK")
    return " · ".join(flags)


def model_row_values(model) -> tuple[str, ...]:
    """The text of one model-status row, straight from `LiveModel`."""

    return (model.role, model.id, model.state, fmt_flag(model.in_contract), str(model.inferences),
            str(model.faults), fmt_number(model.last_latency_ms, ".2f", "ms"), model.reason or DASH)


def input_timeline(live: LiveView) -> list:
    """Input-side events of the backend, newest first."""

    return [item for item in reversed(live.events)
            if item.source == "input_source" or item.kind == "input_event_injected"]


class LiveDemoPanel(QWidget):
    """Model status, decision feed, demands and (demonstration only) input events."""

    command = Signal(str, str)      # verb, JSON target
    select = Signal(str, str)       # "node" | "link", identifier
    notify = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        root = vbox(self, (0, 0, 0, 0), 16)
        self._live: LiveView | None = None
        self._kind: str | None = None
        self._decisions: list = []
        self._selected_decision = ""
        self._links: list[str] = []

        # ---------------------------------------------------------- models
        models = Card()
        self.models_title = Label("ML models · live status", wrap=True)
        self.source_chip = status_chip("", "violet")
        self.tick = L("", mono(12), C.ink3)
        models.add(head_row(self.models_title, self.tick, self.source_chip))
        self.models_table = RecordTable(MODEL_HEADERS)
        self.models_table.setAccessibleName("Model status reported by the backend")
        header = self.models_table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        for column, width in enumerate((116, 270, 120, 120, 110, 80, 100)):
            self.models_table.setColumnWidth(column, width)
        header.setSectionResizeMode(7, QHeaderView.ResizeMode.Stretch)
        self.models_table.clicked.connect(self._model_clicked)
        mono_header(self.models_table)
        self.models_table.setTextElideMode(Qt.TextElideMode.ElideRight)
        models.add(self.models_table)
        self.models_note = note("", 12.5)
        models.add(self.models_note)

        demands = Card()
        self.demands_title = Label("Demands and routes", wrap=True)
        demands.add(self.demands_title)
        self.effects = KVList(size=12.5)
        demands.add(self.effects)
        self.demands = LogList(empty="No demand is defined in the backend state.", time_width=34)
        demands.add(self.demands)
        demands.lay.addStretch(1)
        root.addWidget(models)

        # ------------------------------------------------------------ feed
        feed = Card()
        self.feed_title = Label("Decision feed · newest first", wrap=True)
        self.feed_count = Label("")
        feed.add(head_row(self.feed_title, self.feed_count))
        self.filter_host = QWidget()
        self.filter_lay = FlowLayout(self.filter_host, gap=8)
        self.filters: dict[str, ToggleChip] = {}
        for key in ("all", *DECISION_KINDS):
            chip = ToggleChip(key, KIND_CHIP.get(key, "ink"), key == "all")
            chip.clicked.connect(lambda checked=False, name=key: self.set_kind(None if name == "all" else name))
            self.filter_lay.addWidget(chip)
            self.filters[key] = chip
        feed.add(self.filter_host)
        self.decisions_table = RecordTable(DECISION_HEADERS, identity_columns=())
        self.decisions_table.setAccessibleName("Decisions taken by the backend, newest first")
        header = self.decisions_table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        for column, width in enumerate((92, 116, 196, 108, 96, 0, 150, 252)):
            if width:
                self.decisions_table.setColumnWidth(column, width)
        header.setSectionResizeMode(5, QHeaderView.ResizeMode.Stretch)
        self.decisions_table.setFixedHeight(310)
        self.decisions_table.clicked.connect(self._decision_clicked)
        mono_header(self.decisions_table)
        self.decisions_table.setTextElideMode(Qt.TextElideMode.ElideRight)
        feed.add(self.decisions_table)
        self.decision_detail = note("Select a decision to read its full record and select its subject.", 12.5)
        feed.add(self.decision_detail)
        root.addWidget(feed)

        # ----------------------------------------------------------- input
        inputs = Card()
        self.input_title = Label("Input events · simulated input only", wrap=True)
        inputs.add(self.input_title)
        self.inject_box = QWidget()
        il = vbox(self.inject_box, (0, 0, 0, 0), 8)
        il.addWidget(Label("Event"))
        self.kind_combo = QComboBox()
        self.kind_combo.setFont(mono(12))
        self.kind_combo.setAccessibleName("Input event kind")
        il.addWidget(self.kind_combo)
        il.addWidget(Label("Fibre"))
        self.link_combo = QComboBox()
        self.link_combo.setFont(mono(12))
        self.link_combo.setAccessibleName("Fibre whose input is disturbed")
        il.addWidget(self.link_combo)
        numbers = QWidget()
        nl = hbox(numbers, (0, 0, 0, 0), 8)
        strength_box = QWidget()
        sl = vbox(strength_box, (0, 0, 0, 0), 5)
        sl.addWidget(Label("Strength"))
        self.strength = QDoubleSpinBox()
        self.strength.setRange(0.05, 1.0)
        self.strength.setSingleStep(0.05)
        self.strength.setDecimals(2)
        self.strength.setValue(0.5)
        self.strength.setFont(mono(12))
        self.strength.setAccessibleName("Event strength, 0 to 1")
        sl.addWidget(self.strength)
        duration_box = QWidget()
        dl = vbox(duration_box, (0, 0, 0, 0), 5)
        dl.addWidget(Label("Duration · s"))
        self.duration = QSpinBox()
        self.duration.setRange(1, 3600)
        self.duration.setValue(60)
        self.duration.setFont(mono(12))
        self.duration.setAccessibleName("Event duration in network seconds")
        dl.addWidget(self.duration)
        nl.addWidget(strength_box, 1)
        nl.addWidget(duration_box, 1)
        il.addWidget(numbers)
        self.inject_button = Btn("Inject input event", variant="accent")
        self.inject_button.setProperty("operation", "inject_input_event")
        self.inject_button.clicked.connect(self._inject)
        il.addWidget(self.inject_button)
        inputs.add(self.inject_box)
        self.inject_note = note(INJECT_NOTE, 12.5)
        inputs.add(self.inject_note)
        inputs.lay.addStretch(1)
        timeline = Card()
        timeline.add(Label("Input timeline · reported by the backend"))
        self.timeline = LogList(empty="No input event reported by the backend.")
        timeline.add(self.timeline)
        timeline.lay.addStretch(1)
        root.addWidget(grid_row([demands, inputs, timeline]))
        self._inject_allowed: bool | None = None

    # -------------------------------------------------------------- filters
    def kind(self) -> str | None:
        return self._kind

    def set_kind(self, kind: str | None) -> None:
        self._kind = kind if kind in DECISION_KINDS else None
        for name, chip in self.filters.items():
            chip.set_on(name == (self._kind or "all"))
        if self._live is not None:
            self._render_decisions(self._live)

    # ----------------------------------------------------------------- data
    def set_snapshot(self, snap: Snapshot) -> None:
        live = snap.live
        if live is None:
            return
        self._live = live
        set_text(self.tick, f"tick {live.tick} · network time {live.network_time_s:.0f} s · {clock_of(live.at)} UTC")
        set_chip(self.source_chip, f"{live.source_kind} input".upper(),
                 "violet" if live.source_kind == "simulated" else "green" if live.source_kind == "measured" else "dim")
        self._render_models(live)
        self._render_demands(live)
        self._render_decisions(live)
        self._render_inputs(snap)

    def _render_models(self, live: LiveView) -> None:
        rows = []
        for model in live.models:
            values = model_row_values(model)
            state = tone(MODEL_STATE_TONE.get(model.state, "ink"))
            contract = C.ink3 if model.in_contract is None else C.green if model.in_contract else C.amber
            rows.append([(values[0], C.ink), (values[1], C.ink), (values[2], state), (values[3], contract),
                         (values[4], C.ink), (values[5], C.red if model.faults else C.ink3), (values[6], C.ink),
                         (values[7], C.ink2)])
        self.models_table.set_rows(rows)
        self.models_table.setFixedHeight(40 + 34 * max(1, len(rows)))
        counts = Counter(model.state for model in live.models)
        summary = " · ".join(f"{counts[state]} {state}" for state in ("active", "fallback", "unavailable", "disabled")
                             if counts[state]) or "no model reported"
        set_text(self.models_title, f"ML models · live status · {summary}")
        set_text(self.models_note,
                 "State and reason are the backend's. A model that is unavailable or disabled does not control the network: the reason names "
                 "the reference controller acting in its place. Select a row to read its full reason."
                 if live.models else "The backend reports no model for this source.")

    def _model_clicked(self, index) -> None:
        if self._live is not None and 0 <= index.row() < len(self._live.models):
            model = self._live.models[index.row()]
            digest = f" Archive SHA-256 {model.archive_sha256[:16]}." if model.archive_sha256 else ""
            set_text(self.models_note, f"{model.id} ({model.role}) · {model.state} · {model.reason or DASH}.{digest}")

    def _render_demands(self, live: LiveView) -> None:
        rows = []
        for demand in live.demands:
            flags = [] if demand.in_contract else ["out of contract"]
            if demand.fallback_used:
                flags.append("fallback")
            body = (f"{demand.a} ↔ {demand.b} · {demand.status} · "
                    f"{' → '.join(demand.route) if demand.route else 'no route'}")
            if demand.fidelity is not None:
                body += f" · F {fmt_fidelity(demand.fidelity)}"
            if demand.success_rate is not None:
                body += f" · success {fmt_percent(demand.success_rate, 0)}"
            if demand.policy_id:
                body += f" · {demand.policy_id}"
            if flags:
                body += f" · {', '.join(flags)}"
            if demand.reason:
                body += f" · {demand.reason}"
            rows.append((demand.id, body, DEMAND_STATUS_TONE.get(demand.status, "ink")))
        self.demands.set_rows(rows)
        fibres = Counter(link.status for link in live.links)
        nodes = Counter(node.status for node in live.nodes)
        self.effects.set_rows([
            ("Fibres", " · ".join(f"{count} {status}" for status, count in fibres.items()) or DASH,
             "ink" if set(fibres) <= {"up"} else "amber"),
            ("Nodes", " · ".join(f"{count} {status}" for status, count in nodes.items()) or DASH,
             "ink" if set(nodes) <= {"up"} else "amber"),
        ])
        counts = Counter(demand.status for demand in live.demands)
        summary = " · ".join(f"{count} {status}" for status, count in counts.items())
        set_text(self.demands_title, "Demands and routes" + (f" · {summary}" if summary else ""))

    def _render_decisions(self, live: LiveView) -> None:
        counts = Counter(decision.kind for decision in live.decisions)
        set_text(self.filters["all"], f"all {len(live.decisions)}")
        for kind in DECISION_KINDS:
            set_text(self.filters[kind], f"{kind} {counts[kind]}")
        self._decisions = [decision for decision in reversed(live.decisions)
                           if self._kind is None or decision.kind == self._kind]
        rows = []
        for decision in self._decisions:
            flags = decision_flags(decision)
            rows.append([
                (clock_of(decision.at), C.ink3), (decision.kind, tone(KIND_TONE.get(decision.kind, "ink"))),
                (decision.controller_id, C.ink), (decision.model_state, tone(MODEL_STATE_TONE.get(decision.model_state, "ink"))),
                (decision.subject, C.acc), (decision.summary, C.ink2), (decision.outcome, C.ink),
                (flags, C.red if not decision.in_contract else C.amber)])
        self.decisions_table.set_rows(rows)
        self.decisions_table.setFixedHeight(310 if live.decisions else 84)
        shown = len(self._decisions)
        set_text(self.feed_count, f"{shown} of {len(live.decisions)} in window" if self._kind
                 else f"{shown} in window")
        selected = next((item for item in self._decisions if item.id == self._selected_decision), None)
        if selected is None and self._selected_decision:
            self._selected_decision = ""
            set_text(self.decision_detail, "Select a decision to read its full record and select its subject.")

    def decision_ids(self) -> list[str]:
        """Ids of the feed rows, in the order shown."""

        return [decision.id for decision in self._decisions]

    def model_rows(self) -> list[tuple[str, ...]]:
        records = self.models_table.records
        return [tuple(cell[0] for cell in row) for row in records.rows]

    def subject_kind(self, subject: str) -> str | None:
        live = self._live
        if live is None:
            return None
        if live.link(subject) is not None:
            return "link"
        if live.node(subject) is not None:
            return "node"
        if live.demand(subject) is not None:
            return "demand"
        return None

    def _decision_clicked(self, index) -> None:
        if 0 <= index.row() < len(self._decisions):
            self.select_decision(self._decisions[index.row()].id)

    def select_decision(self, decision_id: str) -> None:
        decision = next((item for item in self._decisions if item.id == decision_id), None)
        if decision is None:
            return
        self._selected_decision = decision.id
        flags = decision_flags(decision)
        inputs = f" · inputs {decision.inputs_sha256[:16]}" if decision.inputs_sha256 else ""
        set_text(self.decision_detail,
                 f"{clock_of(decision.at)} UTC · tick {decision.tick} · {decision.kind} · {decision.subject} · "
                 f"{decision.controller_id} (model {decision.model_state}) · {decision.summary} · outcome "
                 f"{decision.outcome}" + (f" · {flags}" if flags else "")
                 + f" · record {decision.record_sha256[:16]}{inputs}")
        kind = self.subject_kind(decision.subject)
        if kind in ("node", "link"):
            self.select.emit(kind, decision.subject)
            self.notify.emit(f"Selected {kind} {decision.subject}")
        elif kind == "demand":
            demand = self._live.demand(decision.subject)
            self.notify.emit(f"Demand {demand.id}: {' → '.join(demand.route) if demand.route else 'no route'}")

    # ---------------------------------------------------------------- input
    def _render_inputs(self, snap: Snapshot) -> None:
        live = snap.live
        allowed = snap.capabilities.allows("inject_input_event")
        if allowed != self._inject_allowed:
            self._inject_allowed = allowed
            self.inject_box.setVisible(allowed)
            if allowed:
                from ..live_commands import input_event_kinds
                kinds = list(input_event_kinds())
                if [self.kind_combo.itemText(i) for i in range(self.kind_combo.count())] != kinds:
                    self.kind_combo.clear()
                    self.kind_combo.addItems(kinds)
            set_text(self.input_title, "Input events · simulated input only" if allowed else "Input events")
            set_text(self.inject_note, INJECT_NOTE if allowed else
                     "Input events can only be injected in the Demonstration source, whose input is simulated. "
                     f"This source: {live.source_label or live.source_kind}.")
        links = [link.id for link in live.links]
        if links != self._links:
            current = self.link_combo.currentText()
            self._links = links
            self.link_combo.clear()
            self.link_combo.addItems(links)
            if current in links:
                self.link_combo.setCurrentIndex(links.index(current))
        self.timeline.set_rows([
            (clock_of(item.at), f"{item.kind} · {item.subject} · {item.message}", SEVERITY_TONE.get(item.severity, "ink"))
            for item in input_timeline(live)[:INPUT_TIMELINE_ROWS]])

    def inject_payload(self) -> dict:
        """The request the control would send, from its current fields."""

        return {"kind": self.kind_combo.currentText(), "link_id": self.link_combo.currentText(),
                "strength": round(float(self.strength.value()), 2), "duration_s": float(self.duration.value())}

    def set_injection(self, kind: str, link_id: str, strength: float | None = None,
                      duration_s: float | None = None) -> None:
        for combo, value in ((self.kind_combo, kind), (self.link_combo, link_id)):
            index = combo.findText(value)
            if index < 0:
                raise ValueError(f"not offered by the backend: {value}")
            combo.setCurrentIndex(index)
        if strength is not None:
            self.strength.setValue(strength)
        if duration_s is not None:
            self.duration.setValue(int(duration_s))

    def _inject(self) -> None:
        if not self._inject_allowed:
            return
        payload = self.inject_payload()
        if not payload["kind"] or not payload["link_id"]:
            self.notify.emit("Choose an event and a fibre first")
            return
        self.command.emit("inject_input_event", json.dumps(payload))

    # ----------------------------------------------------------- view state
    def save_state(self) -> dict:
        return {"kind": self._kind, "event": self.kind_combo.currentText(), "link": self.link_combo.currentText(),
                "strength": float(self.strength.value()), "duration": int(self.duration.value())}

    def restore_state(self, state: dict) -> None:
        self.set_kind(state.get("kind"))
        for combo, value in ((self.kind_combo, state.get("event")), (self.link_combo, state.get("link"))):
            index = combo.findText(value) if value else -1
            if index >= 0:
                combo.setCurrentIndex(index)
        self.strength.setValue(state.get("strength", self.strength.value()))
        self.duration.setValue(state.get("duration", self.duration.value()))


__all__ = ["DECISION_HEADERS", "INJECT_NOTE", "LiveDemoPanel", "MODEL_HEADERS", "decision_flags", "input_timeline",
           "model_row_values"]
