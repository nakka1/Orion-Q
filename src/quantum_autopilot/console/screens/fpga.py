"""Edge control · "Tier 0 · Edge decision". Aggregate only; never per-shot records.

Layout of the approved prototype: three cards — edge device and decision source
(with the entry points of the policy dialogs), action mix and decisions of the
journal window, model card with the optional model catalog and safety events —
followed, when the source carries a network engine, by the live demonstration
panel (`widgets.demo_panel.LiveDemoPanel`): model status, decision feed, demands
and, in the demonstration source only, the input-event control.

Data: the action mix and the decision list are counted from `snap.ledger.entries`
(journal records); the catalog rows are the backend's `model_intake` catalog with
its declared compatibility text; everything in the live panel is `snap.live`.
FPGA inventory and supervised acquisition controls come from the device runtime;
simulated identities and modeled timing are explicitly distinguished from hardware.
"""
from __future__ import annotations

import re
from collections import Counter

from PySide6.QtCore import Qt
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import QGridLayout, QSizePolicy, QWidget

from ..contract import MODEL_STATE_TONE, Snapshot
from ..theme import C, mono, qc, text as text_font, text_tone, tone
from ..widgets import Bar, Btn, Card, L, Label, RowBtn, hbox, vbox
from ..widgets.cards import LogList, Page, grid_row, head_row, note, set_text
from ..widgets.model_catalog import ModelCatalog
from .base import Screen

ACTIONS = ("SWAP", "PURIFY", "WAIT", "HALT")
ACTION_TONE = {"SWAP": "green", "PURIFY": "violet", "WAIT": "amber", "HALT": "red"}
ACTION_WORD = {action: re.compile(rf"\b{action}\b") for action in ACTIONS}
SAFETY_WORDS = ("INTERLOCK", "WATCHDOG", "DECISION SOURCE", "ML ")
DECISION_ROWS = 6


def entry_action(entry) -> str | None:
    """The control action a journal entry records, or None."""

    upper = entry.action.upper()
    return next((action for action in ACTIONS if ACTION_WORD[action].search(upper)), None)


class MixBar(QWidget):
    """The 36 px stacked bar of the action mix."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setFixedHeight(36)
        self.counts: Counter = Counter()

    def set_counts(self, counts: Counter) -> None:
        if counts != self.counts:
            self.counts = Counter(counts)
            self.update()

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        rect = self.rect().adjusted(0, 0, -1, -1)
        total = sum(self.counts[action] for action in ACTIONS)
        p.setFont(mono(11))
        if total:
            x = float(rect.left() + 1)
            width = rect.width() - 1
            for action in ACTIONS:
                share = self.counts[action] / total
                if share <= 0:
                    continue
                span = width * share
                p.fillRect(int(x), rect.top() + 1, max(1, int(round(span))), rect.height() - 1, qc(tone(ACTION_TONE[action])))
                label = f"{action} {share:.0%}"
                if p.fontMetrics().horizontalAdvance(label) + 8 <= span:
                    p.setPen(qc("#141413"))
                    p.drawText(int(x), rect.top(), int(span), rect.height(), Qt.AlignmentFlag.AlignCenter, label)
                x += span
        else:
            p.setPen(qc(C.ink3))
            p.drawText(rect, Qt.AlignmentFlag.AlignCenter, "no control action in the journal window")
        p.setPen(qc(C.soft))
        p.drawRect(rect)
        p.end()


class CatalogRow(QWidget):
    """One optional model: name and status; opens to the catalog's declared fields."""

    def __init__(self, option, parent=None) -> None:
        super().__init__(parent)
        self.option = option
        self.open = False
        lay = vbox(self, (0, 0, 0, 0), 0)
        self.button = RowBtn(padding=(10, 8, 10, 8), gap=8)
        self.name = L(option.model_id, text_font(13), C.ink, elide=True)
        self.name.setMinimumWidth(0)
        self.name.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.sign = L("", mono(11.5), C.ink3)
        self.button.add(self.name, 1)
        self.button.add(self.sign)
        self.button.setAccessibleName(f"Optional model {option.model_id}")
        self.button.clicked.connect(lambda: self.set_open(not self.open))
        lay.addWidget(self.button)
        self.body = note("", 12.5)
        self.body.setTextFormat(Qt.TextFormat.RichText)
        self.body.setContentsMargins(10, 6, 10, 8)
        self.body.setVisible(False)
        lay.addWidget(self.body)
        self._live = None
        self.set_live(None)

    def set_live(self, model) -> None:
        """`model` is the `LiveModel` with this id, or None when the engine does not report it."""

        self._live = model
        if model is not None:
            text, colour = f"LIVE · {model.state.upper()}", text_tone(MODEL_STATE_TONE.get(model.state, "dim"))
        else:
            text = "OFFLINE ONLY" if self.option.status == "offline_only" else "BLOCKED"
            colour = C.ink3
        set_text(self.sign, text)
        style = f"color:{colour};background:transparent;"
        if self.sign.styleSheet() != style:
            self.sign.setStyleSheet(style)
        if self.open:
            self._fill()

    def set_open(self, value: bool) -> None:
        self.open = bool(value)
        self.button.set_on(self.open)
        if self.open:
            self._fill()
        self.body.setVisible(self.open)

    def _fill(self) -> None:
        from html import escape
        option = self.option
        try:
            from ...model_intake.policy_compatibility import compatibility_notes
            gate = compatibility_notes(option.model_id).splitlines()[0]
        except Exception as exc:
            gate = f"compatibility record unavailable ({type(exc).__name__})"
        ok, warn = text_tone("ok"), text_tone("warn")
        lines = [
            f'<span style="color:{ok};font-family:\'{mono(12).family()}\'">PURPOSE</span> {escape(option.purpose)}',
            f'<span style="color:{warn};font-family:\'{mono(12).family()}\'">GATE</span> {escape(gate)} · '
            f'package {escape(option.archive_kind.upper())} (external, not bundled) · default '
            f'{"ON" if option.default_enabled else "OFF"} · runtime '
            f'{"see policy gate" if option.runtime_available else "not integrated"}',
        ]
        if self._live is not None:
            lines.append(f'<span style="color:{ok};font-family:\'{mono(12).family()}\'">LIVE</span> '
                         f'{escape(self._live.state)} · {escape(self._live.reason or "no reason reported")}')
        self.body.setText("<br>".join(lines))


class EdgeControlScreen(Screen):
    key = "fpga"
    title = "TIER 0 · EDGE DECISION"
    heading = "Edge control"
    status = "AGGREGATE ONLY · NO PER-SHOT DISPLAY"

    def __init__(self, parent=None):
        super().__init__(parent)
        self.add_header("Tier 0 · Edge decision")
        self.ml_on = False
        self._catalog_context = None
        self.demo_panel = None
        self._panel_state: dict | None = None
        self.page = Page()
        self.root.addWidget(self.page, 1)

        # ---------------------------------------------------------- device
        device = Card()
        device.add(Label("Tier 0 · edge device"))
        self.device = note("Software control-plane sandbox.", 13.5, C.ink)
        device.add(self.device)
        self.device_note = note("No FPGA hardware identity, firmware or bitstream attestation supplied.", 13)
        device.add(self.device_note)
        device.add(Label("Loop period"))
        self.period = L("—", text_font(30), C.ink3)
        device.add(self.period)
        self.margin = Bar(0, C.dim)
        self.margin.setVisible(False)
        device.add(self.margin)
        self.margin_note = note("Timing figures remain unavailable; no hardware margin is inferred.")
        device.add(self.margin_note)
        device.add(Label("Decision source"))
        toggles = QWidget()
        tl = hbox(toggles, (0, 0, 0, 0), 8)
        self.ml = Btn("ML model", variant="default")
        self.deterministic = Btn("Deterministic", variant="default")
        for button in (self.ml, self.deterministic):
            button.setProperty("operation", "set_decision_source")
        self.ml.clicked.connect(lambda: self._set_source(True))
        self.deterministic.clicked.connect(lambda: self._set_source(False))
        tl.addWidget(self.ml)
        tl.addWidget(self.deterministic)
        tl.addStretch(1)
        device.add(toggles)
        self.decision_note = note("Unavailable until a supporting software input is connected.")
        device.add(self.decision_note)

        from ..widgets.policy_review import PolicyReview
        self.policy_review = PolicyReview(self)
        self.policy_review_button = Btn("Policy review", variant="accent")
        self.policy_review_button.setProperty("operation", "review_policy_preparation")
        self.policy_review_button.setEnabled(False)
        self.policy_review_button.clicked.connect(self.policy_review.show)
        from ..widgets.policy_decisions import PolicyDecisionAudit
        self.policy_decisions = PolicyDecisionAudit(self)
        self.policy_decisions_button = Btn("Decision audit", variant="default")
        self.policy_decisions_button.setProperty("operation", "read_policy_decisions")
        self.policy_decisions_button.setEnabled(False)
        self.policy_decisions_button.clicked.connect(self.policy_decisions.show)
        from ..widgets.policy_context import PolicyContextInspection
        self.policy_context = PolicyContextInspection(self)
        self.policy_context_button = Btn("Context inspection", variant="default")
        self.policy_context_button.setProperty("operation", "observe_policy_context")
        self.policy_context_button.setEnabled(False)
        self.policy_context_button.clicked.connect(self.policy_context.show)
        from ..widgets.polarisation_control import PolarisationControl
        self.polarisation_control = PolarisationControl(self)
        self.polarisation_button = Btn("Polarisation loop", variant="default")
        self.polarisation_button.setToolTip("Simulated supervised loop; a learned compensator runs only after "
                                            "prepare, review, approval and activation.")
        self.polarisation_button.clicked.connect(self._show_polarisation)
        dialogs = QWidget()
        dg = QGridLayout(dialogs)
        dg.setContentsMargins(0, 6, 0, 0)
        dg.setSpacing(8)
        for index, button in enumerate((self.policy_review_button, self.policy_decisions_button,
                                        self.policy_context_button, self.polarisation_button)):
            dg.addWidget(button, index // 2, index % 2)
        device.add(dialogs)
        device.lay.addStretch(1)

        # ------------------------------------------------------------- mix
        mix = Card()
        self.mix_total = Label("0 decisions")
        mix.add(head_row(Label("Action mix · journal window"), self.mix_total))
        self.mix = MixBar()
        mix.add(self.mix)
        counts = QWidget()
        cg = QGridLayout(counts)
        cg.setContentsMargins(0, 0, 0, 0)
        cg.setSpacing(8)
        self.count_labels: dict[str, L] = {}
        for column, action in enumerate(ACTIONS):
            box = QWidget()
            bl = vbox(box, (0, 0, 0, 0), 0)
            bl.addWidget(Label(action, tone(ACTION_TONE[action])))
            value = L("—", text_font(24), C.ink)
            bl.addWidget(value)
            cg.addWidget(box, 0, column)
            cg.setColumnStretch(column, 1)
            self.count_labels[action] = value
        mix.add(counts)
        mix.add(note("No individual photon-arrival decision is displayed. Actions are aggregated from the journal. "
                     "Run a simulated scenario to add decisions."))
        self.scenario_button = Btn("Run simulated scenario", variant="accent")
        self.scenario_button.setProperty("operation", "run_scenario")
        self.scenario_button.clicked.connect(lambda: self.command.emit("run_scenario", ""))
        mix.lay.addWidget(self.scenario_button, 0, Qt.AlignmentFlag.AlignLeft)
        mix.add(Label("Decisions in window"))
        self.decisions = LogList(empty="No decisions in the current window.")
        mix.add(self.decisions)
        mix.add(Label("Loop latency · source telemetry only"))
        self.latency_note = note("Unavailable — no measured timing supplied.", 12.5, C.ink3)
        mix.add(self.latency_note)
        mix.lay.addStretch(1)

        # ----------------------------------------------------------- model
        model = Card()
        model.add(Label("Model card · not a measured claim", C.acc))
        self.model = note("No model metadata supplied by this source.", 13)
        model.add(self.model)
        model.add(Label("Optional model catalog · read-only"))
        self.model_catalog = ModelCatalog(self)
        catalog = QWidget()
        cl = vbox(catalog, (0, 0, 0, 0), 0)
        self.catalog_rows: dict[str, CatalogRow] = {}
        for option in self.model_catalog.options:
            row = CatalogRow(option)
            cl.addWidget(row)
            self.catalog_rows[option.model_id] = row
        model.add(catalog)
        self.catalog_button = Btn("Open model catalog", variant="default")
        self.catalog_button.setToolTip("Read-only inventory; does not load weights or change the runtime policy.")
        self.catalog_button.clicked.connect(self._show_catalog)
        model.lay.addWidget(self.catalog_button, 0, Qt.AlignmentFlag.AlignLeft)
        model.add(Label("Safety events"))
        self.events = note("No safety events in the current window.")
        model.add(self.events)
        buttons = QWidget()
        bl = hbox(buttons, (0, 0, 0, 0), 8)
        self.flash = Btn("Flash unavailable", variant="default")
        self.flash.setEnabled(False)
        self.export = Btn("Export decision log", variant="default")
        self.export.setProperty("operation", "export_record")
        self.export.clicked.connect(lambda: self.command.emit("export", "decision-log"))
        bl.addWidget(self.flash)
        bl.addWidget(self.export)
        bl.addStretch(1)
        model.add(buttons)
        model.lay.addStretch(1)
        self.page.add(grid_row([device, mix, model]))
        from ..widgets.fpga_devices import FpgaDevicesPanel
        self.fpga_devices = FpgaDevicesPanel()
        self.fpga_devices.command.connect(self.command)
        self.page.add(self.fpga_devices)

        # Live demonstration panel: built the first time a snapshot carries live state.
        self.demo_host = QWidget()
        self.demo_lay = vbox(self.demo_host, (0, 0, 0, 0), 16)
        self.demo_title = Label("Live network · models in action")
        self.demo_lay.addWidget(self.demo_title)
        self.demo_host.setVisible(False)
        self.page.add(self.demo_host)

    # ---------------------------------------------------------- view state
    def apply_command_capabilities(self):
        super().apply_command_capabilities()
        # Generic operation permissions cannot grant an unavailable physical
        # adapter a connection or expose simulator-only fault controls.
        self.fpga_devices.set_busy(getattr(self, "commands_busy", False))

    def save_state(self) -> dict:
        state = super().save_state()
        state["catalog_open"] = [key for key, row in self.catalog_rows.items() if row.open]
        if self.demo_panel is not None:
            state["panel"] = self.demo_panel.save_state()
        return state

    def restore_state(self, state: dict) -> None:
        super().restore_state(state)
        for key in state.get("catalog_open", ()):
            if key in self.catalog_rows:
                self.catalog_rows[key].set_open(True)
        self._panel_state = state.get("panel")
        if self.demo_panel is not None and self._panel_state:
            self.demo_panel.restore_state(self._panel_state)
            self._panel_state = None

    def _show_polarisation(self):
        self.polarisation_control.show()
        self.polarisation_control.raise_()
        self.polarisation_control.activateWindow()

    def _show_catalog(self):
        self.model_catalog.show()
        self.model_catalog.raise_()
        self.model_catalog.activateWindow()

    def _draw_mix(self, counts):
        total = sum(counts[action] for action in ACTIONS)
        self.mix.set_counts(Counter({action: counts[action] for action in ACTIONS}))
        for action in ACTIONS:
            set_text(self.count_labels[action], str(counts[action]) if total else "—")
        set_text(self.mix_total, f"{total} decisions")

    def _set_source(self, value):
        if not (self.ml.isEnabled() and self.deterministic.isEnabled()):
            return
        previous = self.ml_on
        self.ml_on = value
        self._mark_source(True)
        self.command.emit("set_decision_source", f"{'ml' if value else 'deterministic'}:{'ml' if previous else 'deterministic'}")

    def _mark_source(self, binding: bool) -> None:
        self.ml.set_variant("accent" if binding and self.ml_on else "default")
        self.deterministic.set_variant("accent" if binding and not self.ml_on else "default")

    def _ensure_demo_panel(self):
        if self.demo_panel is None:
            from ..widgets.demo_panel import LiveDemoPanel
            self.demo_panel = LiveDemoPanel()
            self.demo_panel.command.connect(self.command)
            self.demo_panel.select.connect(self.select)
            self.demo_panel.notify.connect(self.notify)
            self.demo_lay.addWidget(self.demo_panel)
        return self.demo_panel

    def refresh(self, snap: Snapshot):
        self.fpga_devices.set_snapshot(snap)
        context = (snap.input_kind, snap.campaign, snap.capabilities.adapter_id)
        if self._catalog_context is not None and context != self._catalog_context:
            self.model_catalog.checker.clear()
        self._catalog_context = context
        self.model_catalog.accept_shadow_snapshot(snap)
        counts = Counter()
        safety, decisions = [], []
        for entry in snap.ledger.entries:
            action = entry_action(entry)
            if action:
                counts[action] += 1
                if len(decisions) < DECISION_ROWS:
                    decisions.append((entry.at, f"{entry.action} · {entry.actor} · {entry.digest}", ACTION_TONE[action]))
            upper = entry.action.upper()
            if any(word in upper for word in SAFETY_WORDS):
                safety.append(f"{entry.at} · {entry.action}")
        self._draw_mix(counts)
        self.decisions.set_rows(decisions)
        policy_binding = snap.capabilities.allows("set_decision_source")
        tip = snap.capabilities.describe()
        for button in (self.ml, self.deterministic):
            button.setEnabled(policy_binding)
            if button.toolTip() != tip:
                button.setToolTip(tip)
        if not policy_binding:
            self.ml_on = False
        self._mark_source(policy_binding)
        set_text(self.decision_note, "Runtime policy selection unavailable; no active policy is inferred from telemetry."
                 if not policy_binding else "Runtime policy selection supported by this adapter.")
        set_text(self.model, "\n".join(f"{m.label}: {m.value} {m.unit} · {m.provenance.upper()}"
                                       for m in snap.telemetry.headline[:4]) or
                 "No model metadata supplied by this source. The console does not invent an EdgeLSTM version or training count.")
        set_text(self.events, "\n".join(safety[:8]) or "No safety events in the current window.")

        live = snap.live
        reported = {model.id: model for model in live.models} if live is not None else {}
        for key, row in self.catalog_rows.items():
            row.set_live(reported.get(key))
        if live is not None:
            panel = self._ensure_demo_panel()
            panel.set_snapshot(snap)
            if self._panel_state:
                panel.restore_state(self._panel_state)
                self._panel_state = None
            set_text(self.demo_title, "Live network · models in action · "
                     + ("demonstration: simulated input, real pipeline" if snap.input_kind == "demo"
                        else f"{live.source_kind} input"))
        self.demo_host.setVisible(live is not None)


__all__ = ["ACTIONS", "CatalogRow", "EdgeControlScreen", "MixBar", "entry_action"]
