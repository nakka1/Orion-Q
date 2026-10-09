"""Telemetry · "Fidelity & decoherence".

Layout of the approved prototype: heading row with the real operations, link
selector, three headline cards with provenance chip and expandable note, a
two-column chart card with toggleable series, a card of per-fibre quantities,
and the claim / conditions / limitations row.

Every figure comes from the snapshot:

* with a network engine (`snap.live`): the selected fibre's estimates, its 1 Hz
  history (`LiveLink.history`, plotted point for point) and its controllers;
* always: `snap.telemetry` (model-replay headline, chart, conditions, scope,
  limitations and artefacts), shown as delivered.

The prototype's memory-coherence card (T1 / T2* of one repeater) has no backend
counterpart; its place is taken by the selected fibre's live quantities.
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QLabel, QScrollArea, QWidget

from ..contract import LINK_STATUS_TONE, Snapshot
from ..theme import C, mono
from ..widgets import Btn, Card, Frame, L, Label, Prose, SegBar, hbox, vbox
from ..widgets.cards import (
    DASH, NOT_SUPPLIED, KVList, LinkPicker, MeterList, MetricCard, Page, ToggleChip, clock_of, fmt_fidelity,
    fmt_fraction, fmt_number, fmt_percent, fmt_rate, grid_row, head_row, note, set_chip, set_text,
    shrinkable, status_chip, unit_fraction,
)
from ..widgets.charts import ChartSeries, SeriesChart, axis_labels
from ..widgets.prims import clear_layout
from ..widgets.evidence_export import choose_evidence_export
from .base import Screen

#: Series slots of the chart card, in the prototype's colours: accent, green, red dashed.
SLOTS = (("a", "amber", 2.2, False), ("b", "green", 1.8, False), ("c", "red", 1.8, True))
SLOT_CHIP = {"a": "warn", "b": "ok", "c": "bad"}
METRICS = (("fidelity", "Fidelity"), ("qber", "QBER"), ("skr", "Key rate"))
METRIC_FORMAT = {
    "fidelity": lambda value: f"{value:.4f}",
    "qber": lambda value: f"{value * 100:.2f} %",
    "skr": fmt_rate,
}
TONE_CHIP = {"amber": "warn", "green": "ok", "red": "bad", "violet": "sat", "dim": "mute", "muted": "mute",
             "ink": "ink"}
def backend_link_thresholds(live) -> dict:
    """The "fibre down" threshold the RUNNING engine applies, from its published state.

    `live.policy["qber_down"]` is the QBER at which this engine marks a fibre down and
    `fidelity_at_qber_down` its own Werner form of that number. Nothing is imported from the
    backend modules and nothing is computed here: an engine that does not publish the
    thresholds gets no line on the chart.
    """

    if live is None:
        return {}
    values = {"qber": live.threshold("qber_down"), "fidelity": live.threshold("fidelity_at_qber_down")}
    return {name: value for name, value in values.items() if value is not None}


def history_values(link, metric: str):
    """(xs, ys) of one fibre's backend history for a chart metric."""

    history = link.history
    return history.t_s, {"fidelity": history.fidelity, "qber": history.qber, "skr": history.skr_bps}[metric]


class TelemetryScreen(Screen):
    key = "telemetry"
    title = "TELEMETRY"
    heading = "Fidelity & decoherence"
    status = ""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        header = self.add_header()
        self.replay_button = Btn("Replay model", variant="default")
        self.replay_button.setProperty("operation", "replay_model")
        self.replay_button.clicked.connect(lambda: self.command.emit("replay_model", ""))
        self.export_button = Btn("Export record", variant="accent")
        self.export_button.setProperty("operation", "export_record")
        self.export_button.clicked.connect(lambda: choose_evidence_export(self, 'telemetry'))
        header.actions.addWidget(self.replay_button)
        header.actions.addWidget(self.export_button)

        self._link = ""
        self._metric = "fidelity"
        self._chart_mode = "live"
        self._series_on = {slot: True for slot, *_ in SLOTS}
        self._replay_off: set[str] = set()
        self._slot_links: dict[str, str] = {}
        self._open_notes: dict[str, bool] = {}

        self.page = Page()
        self.root.addWidget(self.page, 1)

        # ---------------------------------------------------- link selector
        self.link_row = QWidget()
        lr = hbox(self.link_row, (0, 0, 0, 0), 14)
        lr.addWidget(Label("Link"))
        self.link_picker = LinkPicker()
        self.link_picker.picked.connect(self._pick_link)
        lr.addWidget(self.link_picker, 1)
        self.link_note = Label("")
        lr.addWidget(self.link_note)
        self.page.add(self.link_row)

        # -------------------------------------------------- headline cards
        self.live_cards = [MetricCard() for _ in range(3)]
        for index, card in enumerate(self.live_cards):
            card.toggle.clicked.connect(lambda checked=False, i=index: self._remember_note(f"live{i}"))
        self.live_card_row = grid_row(self.live_cards)
        self.page.add(self.live_card_row)
        self.card_host = QWidget()
        self.card_layout = vbox(self.card_host, (0, 0, 0, 0), 16)
        self.cards: list[MetricCard] = []
        self.card_hosts: list[tuple] = []
        self.card_titles: list = []

        # ------------------------------------------------------- chart row
        chart_card = Card()
        self.window_note = Label("Fidelity by link", wrap=True)
        self.metric_bar = SegBar(METRICS, "fidelity")
        self.metric_bar.selected.connect(self._set_metric)
        self.mode_bar = SegBar((("live", "Live history"), ("replay", "Model replay")), "live")
        self.mode_bar.selected.connect(self._set_chart_mode)
        self.series_host = QWidget()
        self.series_lay = hbox(self.series_host, (0, 0, 0, 0), 8)
        self.series_chips: dict[str, ToggleChip] = {}
        # The title needs the chart's full width. Sharing its row with the
        # selectors can leave only a few characters per line at desktop sizes.
        chart_card.add(self.window_note)
        chart_card.add(head_row(self.mode_bar, self.metric_bar, self.series_host))
        self.chart = SeriesChart(height=230, grow=True)
        chart_card.add(self.chart, 1)
        self.threshold_note = note("", 12, C.ink3)
        chart_card.add(self.threshold_note)

        state_card = Card()
        self.meter_title = Label("Link state", wrap=True)
        self.state_chip = status_chip("", "dim")
        state_card.add(head_row(self.meter_title, self.state_chip, gap=8))
        self.live_meters = MeterList()
        state_card.add(self.live_meters)
        self.live_values = KVList()
        state_card.add(self.live_values)
        self.replay_title = Label("", wrap=True)
        state_card.add(self.replay_title)
        self.meters = MeterList(mono_label=True)
        state_card.add(self.meters)
        self.coherence_note = note("")
        state_card.add(self.coherence_note)
        state_card.lay.addStretch(1)
        self.chart_row = grid_row([(chart_card, 2), state_card])
        # Without live state the source's own headline leads, as in the prototype; with it,
        # the selected fibre's cards lead and the source headline follows the chart.
        self.page.add(self.card_host)
        self.page.add(self.chart_row)
        self._headline_first = True

        # ------------------------------------------------------ bottom row
        claim = Card(tone_name="warn")
        self.claim_verdict = Label("Evidence scope", C.acc)
        self.claim_at = L("", mono(12), C.ink3)
        claim.add(head_row(self.claim_verdict, self.claim_at))
        self.claim_text = note("", 15, C.ink)
        claim.add(self.claim_text)
        self.claim_reason = note("", 13)
        claim.add(self.claim_reason)
        claim.lay.addStretch(1)

        conditions = Card()
        conditions.add(Label("Conditions"))
        self.condition_host = QWidget()
        self.condition_lay = vbox(self.condition_host, (0, 0, 0, 0), 0)
        holder = QWidget()
        hold = vbox(holder, (0, 0, 8, 0), 0)
        hold.addWidget(self.condition_host)
        hold.addStretch(1)
        self.condition_scroll = QScrollArea()
        self.condition_scroll.setWidgetResizable(True)
        self.condition_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.condition_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.condition_scroll.setWidget(holder)
        self.condition_scroll.setMinimumHeight(236)
        self.condition_scroll.setMaximumHeight(320)
        conditions.add(self.condition_scroll, 1)
        self._conditions: list | None = None
        self._condition_values: list[QLabel] = []

        record = Card()
        record.add(Label("Limitations of record"))
        self.limit_host = QWidget()
        self.limit_lay = vbox(self.limit_host, (0, 0, 0, 0), 8)
        record.add(self.limit_host)
        record.add(Label("Artefacts in view"))
        self.artefacts = KVList(size=12)
        record.add(self.artefacts)
        self.artefact_empty = note(NOT_SUPPLIED, 12.5, C.ink3)
        record.add(self.artefact_empty)
        record.lay.addStretch(1)
        self._limits: list | None = None
        self.page.add(grid_row([claim, conditions, record]))

    # ---------------------------------------------------------- view state
    def breadcrumb(self, snap, selection=None) -> str:
        return "/ telemetry" + (f" / {self._link}" if self._link else "")

    def save_state(self) -> dict:
        state = super().save_state()
        state.update(link=self._link, metric=self._metric, chart_mode=self._chart_mode,
                     series=dict(self._series_on), replay_off=sorted(self._replay_off),
                     notes=dict(self._open_notes))
        return state

    def restore_state(self, state: dict) -> None:
        super().restore_state(state)
        self._link = state.get("link", self._link)
        self._metric = state.get("metric", self._metric)
        self._chart_mode = state.get("chart_mode", self._chart_mode)
        self._series_on.update(state.get("series", {}))
        self._replay_off = set(state.get("replay_off", ()))
        self._open_notes.update(state.get("notes", {}))
        if self._snap is not None:
            self.refresh(self._snap)

    def on_selection(self, selection) -> None:
        if getattr(selection, "kind", None) == "link" and selection.id and selection.id != self._link:
            self._link = selection.id
            if self._snap is not None:
                self.refresh(self._snap)

    def cache_selection(self, selection) -> None:
        if getattr(selection, "kind", None) == "link" and selection.id:
            self._link = selection.id

    def _pick_link(self, link_id: str) -> None:
        self._link = link_id
        if self._snap is not None:
            self.refresh(self._snap)
        self.select.emit("link", link_id)

    def _set_metric(self, metric: str) -> None:
        self._metric = metric
        if self._snap is not None:
            self.refresh(self._snap)

    def _set_chart_mode(self, mode: str) -> None:
        self._chart_mode = mode
        if self._snap is not None:
            self.refresh(self._snap)

    def _toggle_series(self, key: str, on: bool) -> None:
        if key in self._series_on:
            self._series_on[key] = on
        elif on:
            self._replay_off.discard(key)
        else:
            self._replay_off.add(key)
        if self._snap is not None:
            self._draw_chart(self._snap)

    def _remember_note(self, key: str) -> None:
        cards = {f"live{i}": card for i, card in enumerate(self.live_cards)}
        cards.update({f"head{i}": card for i, card in enumerate(self.cards)})
        if key in cards:
            self._open_notes[key] = cards[key].open

    # ------------------------------------------------------------ builders
    def _set_headline(self, metrics) -> None:
        if len(metrics) != len(self.cards):
            clear_layout(self.card_layout)
            self.cards = [MetricCard() for _ in metrics]
            for index, card in enumerate(self.cards):
                card.toggle.clicked.connect(lambda checked=False, i=index: self._remember_note(f"head{i}"))
            for start in range(0, len(self.cards), 3):
                chunk = list(self.cards[start:start + 3])
                while len(chunk) < 3:
                    chunk.append(QWidget())
                self.card_layout.addWidget(grid_row(chunk))
            self.card_titles = [card.title for card in self.cards]
            self.card_hosts = [(card.value, card.unit, card.chip, card.note) for card in self.cards]
        for index, (card, metric) in enumerate(zip(self.cards, metrics)):
            card.set_metric(metric.label, metric.value, metric.unit, metric.uncertainty, metric.provenance,
                            metric.note, metric.tone)
            card.set_open(self._open_notes.get(f"head{index}", False))
        self.card_host.setVisible(bool(metrics))

    def _set_conditions(self, rows) -> None:
        rows = list(rows)
        if rows == self._conditions:
            return
        previous_keys = [key for key, _value in self._conditions or ()]
        if [key for key, _value in rows] == previous_keys:
            self._conditions = rows
            for reading, (_key, value) in zip(self._condition_values, rows):
                set_text(reading, value)
                if reading.toolTip() != value:
                    reading.setToolTip(value)
            return
        self._conditions = rows
        clear_layout(self.condition_lay)
        self._condition_values = []
        for index, (label, value) in enumerate(rows):
            # `.kv` with a value that wraps: key, 12 px gap, value (layout item 2).
            row = Frame(border=C.soft if index < len(rows) - 1 else None, sides="b")
            layout = hbox(row, (0, 6, 0, 6), 0)
            layout.addWidget(L(label, mono(12), C.ink2), 0, Qt.AlignmentFlag.AlignTop)
            layout.addSpacing(12)
            reading = shrinkable(Prose(value, 12, C.ink))
            reading.setFont(mono(12))
            reading.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop)
            reading.setToolTip(value)
            self._condition_values.append(reading)
            layout.addWidget(reading, 1)
            self.condition_lay.addWidget(row)

    def _set_limits(self, rows) -> None:
        rows = list(rows)
        if rows == self._limits:
            return
        self._limits = rows
        clear_layout(self.limit_lay)
        for body in rows:
            self.limit_lay.addWidget(note(f"— {body}", 13))
        if not rows:
            self.limit_lay.addWidget(note(NOT_SUPPLIED, 12.5, C.ink3))

    def _set_series_chips(self, entries) -> None:
        """`entries` are (key, label, chip tone, on)."""

        wanted = [(key, label, tone_name) for key, label, tone_name, _on in entries]
        current = [(key, chip.text()[2:], chip._tone) for key, chip in self.series_chips.items()]
        if wanted != current:
            clear_layout(self.series_lay)
            self.series_chips = {}
            for key, label, tone_name, on in entries:
                chip = ToggleChip(f"■ {label}", tone_name, on)
                chip.toggled_on.connect(lambda value, k=key: self._toggle_series(k, value))
                self.series_lay.addWidget(chip)
                self.series_chips[key] = chip
        for key, _label, _tone, on in entries:
            self.series_chips[key].set_on(on)

    # ---------------------------------------------------------------- live
    def _live_link(self, snap: Snapshot):
        live = snap.live
        if live is None or not live.links:
            return None
        if live.link(self._link) is None:
            self._link = live.links[0].id
        return live.link(self._link)

    def _comparison(self, live, link) -> dict[str, str]:
        ids = [item.id for item in live.links]
        start = ids.index(link.id)
        others = [ids[(start + offset) % len(ids)] for offset in range(1, len(ids))][:2]
        return dict(zip(("a", "b", "c"), [link.id, *others]))

    def _refresh_live(self, snap: Snapshot, link) -> None:
        provenance = link.display_provenance
        reasons = "; ".join(link.status_reasons)
        status_line = f"Fibre status {link.status}" + (f" ({reasons})" if reasons else "") + "."
        rate = fmt_rate(link.skr_bps)
        rate_value, _, rate_unit = rate.partition(" ")
        qber_definition = str(link.provenance.get("qber_definition", "")) if link.provenance else ""
        calibration = link.provenance.get("key_rate_calibration_hz") if link.provenance else None
        readings = (
            (f"Fidelity · {link.id}", fmt_fidelity(link.fidelity), "", "Werner estimate",
             "Werner estimate computed by the backend engine from this fibre's QBER for the last network second "
             "(contract: F = 1 − 3·QBER/2). It is an estimate, not a tomography result; the backend reports no "
             f"uncertainty for it. {status_line}"),
            (f"QBER · {link.id}", DASH if link.qber is None else f"{link.qber * 100:.2f}",
             "" if link.qber is None else "%", "uncertainty not supplied",
             (f"Backend definition: {qber_definition}. " if qber_definition else "")
             + f"Computed by the engine from the {link.input} input of the last network second. {status_line}"),
            (f"Secret key rate · {link.id}", rate_value, rate_unit, "uncertainty not supplied",
             f"Secret key rate of the {link.qkd.protocol} period, computed by the backend key-rate stage."
             + (f" Commissioning calibration rate {fmt_rate(float(calibration))}." if isinstance(calibration, (int, float))
                else "") + f" {status_line}"),
        )
        for index, (card, (label, value, unit, uncertainty, body)) in enumerate(zip(self.live_cards, readings)):
            # Without a value there is nothing whose provenance could be stated: the chip names the input.
            card.set_metric(label, value, unit, uncertainty if value != DASH else "",
                            provenance if value != DASH else f"{link.input} input".replace("none input", "no input"),
                            body, "ink" if value != DASH else "dim")
            card.set_open(self._open_notes.get(f"live{index}", False))

        set_text(self.meter_title, f"Link state · {link.id} · last network second")
        set_chip(self.state_chip, link.status.upper(), LINK_STATUS_TONE.get(link.status, "dim"))
        detector = link.detector
        admission = link.classical_admission
        meters = [
            ("Stability", fmt_fraction(link.stability), unit_fraction(link.stability) if link.stability is not None else None,
             "green"),
            ("Gate efficiency", fmt_fraction(link.gate_efficiency),
             unit_fraction(link.gate_efficiency) if link.gate_efficiency is not None else None, "green"),
            ("Attack probability", fmt_percent(detector.attack_probability),
             unit_fraction(detector.attack_probability) if detector.attack_probability is not None else None,
             "red" if detector.alarm else "violet"),
        ]
        if admission is not None:
            meters.append(("Classical admission", fmt_fraction(admission.level),
                           unit_fraction(admission.level) if admission.level is not None else None, "amber"))
        self.live_meters.set_rows(meters)
        self.live_values.set_rows([
            ("Sync residual", fmt_number(link.sync_residual_ps, ".2f", "ps")),
            ("Polarisation residual", fmt_number(link.polarisation_residual_mrad, ".1f", "mrad")),
            ("Sifted rate", fmt_rate(link.sifted_rate_hz, "Hz")),
            ("Detector state", detector.state or DASH),
            ("Detector label", detector.label or DASH),
            ("Detector alarm", DASH if detector.alarm is None else "ALARM" if detector.alarm else "none",
             "red" if detector.alarm else "ink"),
            ("QBER-threshold alarm", DASH if detector.baseline_alarm is None else
             "ALARM" if detector.baseline_alarm else "none", "red" if detector.baseline_alarm else "ink"),
        ])

    def _live_conditions(self, snap: Snapshot, link) -> list[tuple[str, str]]:
        live = snap.live
        reasons = "; ".join(link.status_reasons)
        rows = [
            ("FIBRE", f"{link.id} · {link.a} ↔ {link.b}"),
            ("DISTANCE", fmt_number(link.distance_km, ".3f", "km")),
            ("STATUS", link.status + (f" · {reasons}" if reasons else "")),
            ("INPUT", f"{link.input} · {live.source_label}" if live.source_label else link.input),
            ("NETWORK TIME", f"{live.network_time_s:.0f} s · tick {live.tick} · {clock_of(live.at)} UTC"),
            ("HISTORY", f"{len(link.history.t_s)} points · 1 Hz"),
        ]
        for controller in link.controllers:
            state = f" · model {controller.model_state}" if controller.model_state else ""
            rows.append((f"{controller.role.upper()} CONTROLLER", f"{controller.controller_id}{state}"))
        for item in link.domain_notes:
            verdict = DASH if item.in_domain is None else "in validated domain" if item.in_domain \
                else "outside validated domain"
            rows.append((f"{item.role.upper()} DOMAIN", f"{verdict} · {item.detail}" if item.detail else verdict))
        return rows

    # --------------------------------------------------------------- chart
    def _draw_chart(self, snap: Snapshot) -> None:
        live, telemetry = snap.live, snap.telemetry
        link = self._live_link(snap)
        replay = telemetry.fidelity
        has_live, has_replay = link is not None, bool(replay.series)
        mode = self._chart_mode if (has_live and has_replay) else "live" if has_live else "replay"
        self.mode_bar.setVisible(has_live and has_replay)
        self.mode_bar.set_current(mode)
        self.metric_bar.setVisible(mode == "live" and has_live)
        self.metric_bar.set_current(self._metric)
        if mode == "live" and has_live:
            self._slot_links = self._comparison(live, link)
            entries, series = [], []
            for slot, tone_name, width, dashed in SLOTS:
                link_id = self._slot_links.get(slot)
                if link_id is None:
                    continue
                entries.append((slot, link_id, SLOT_CHIP[slot], self._series_on[slot]))
                if self._series_on[slot]:
                    xs, ys = history_values(live.link(link_id), self._metric)
                    series.append(ChartSeries(link_id, xs, ys, tone_name, width, dashed))
            self._set_series_chips(entries)
            threshold = backend_link_thresholds(live).get(self._metric)
            fmt = METRIC_FORMAT[self._metric]
            xs_all = [x for item in series for x, _ in item.points]
            labels = axis_labels(min(xs_all), max(xs_all), 5, lambda value: f"t {value:.0f} s") if xs_all else []
            self.chart.set_series(series, threshold=threshold, y_format=fmt, x_labels=labels,
                                  threshold_label="", empty_text="No history for the selected series yet")
            drawn = self.chart.threshold_in_range()
            if threshold is None:
                label = ""
            elif self._metric == "qber":
                label = f"fibre down at QBER {threshold * 100:.1f} %"
            else:
                label = f"Werner estimate at the down threshold {threshold:.4f}"
            self.chart.threshold_label = f"— — {label}" if drawn else ""
            self.chart.update()
            title = {"fidelity": "Fidelity by link · Werner estimate", "qber": "QBER by link",
                     "skr": "Secret key rate by link"}[self._metric]
            set_text(self.window_note, title)
            points = len(link.history.t_s)
            history_s = live.threshold("history_window_s")
            window = "its rolling window" if history_s is None else f"the last {history_s:.0f} s"
            if not xs_all:
                text = (f"No history point for the plotted fibres yet ({'no' if link.input == 'none' else link.input} input). The backend keeps one "
                        f"point per network second for {window}.")
            else:
                text = (f"One point per network second from the backend history (kept for {window}): "
                        f"{points} for {link.id}. The last point is the current value in the cards above.")
                if label:
                    text += (f" Down threshold published by the running engine: {label}"
                             + ("." if drawn else " (outside the plotted range)."))
            set_text(self.threshold_note, text)
            return
        entries, series = [], []
        for index, item in enumerate(replay.series):
            on = item.label not in self._replay_off
            entries.append((item.label, item.label, TONE_CHIP.get(item.tone, "mute"), on))
            if on:
                series.append(ChartSeries(item.label, list(range(len(item.points))), list(item.points), item.tone,
                                          item.width, item.dashed))
        self._set_series_chips(entries)
        self.chart.set_series(
            series, threshold=replay.threshold, y_format=lambda value: f"{value:.3f}",
            x_labels=list(replay.x_labels), y_range=(replay.y_min, replay.y_max) if replay.series else None,
            threshold_label="", empty_text="No fidelity series supplied by this source")
        set_text(self.window_note, snap.telemetry.window_note or "Fidelity series")
        set_text(self.threshold_note, replay.threshold_label)

    # --------------------------------------------------------------- state
    def refresh(self, snap: Snapshot) -> None:
        t = snap.telemetry
        live = snap.live
        link = self._live_link(snap)
        synthetic = snap.campaign.startswith("synthetic-")
        self.replay_button.setText("Advance test frame" if synthetic else "Replay model")

        self.link_row.setVisible(link is not None)
        self.live_card_row.setVisible(link is not None)
        if link is not None:
            self.link_picker.set_ids([item.id for item in live.links], link.id)
            missing = sum(item.fidelity is None for item in live.links)
            set_text(self.link_note, f"Fibres without an estimate: {missing} of {len(live.links)}")
            self._refresh_live(snap, link)
        self._set_headline(list(t.headline))
        if (link is None) != self._headline_first:
            self._headline_first = link is None
            self.page.col.removeWidget(self.card_host)
            index = self.page.col.indexOf(self.chart_row)
            self.page.col.insertWidget(index if self._headline_first else index + 1, self.card_host)
        self._draw_chart(snap)

        for widget in (self.live_meters, self.live_values, self.state_chip):
            widget.setVisible(link is not None)
        replay_title = "Synthetic memory signals" if synthetic else "Effective actions"
        if snap.input_kind == "measured":
            replay_title = "Memory data not supplied"
        if link is None:
            set_text(self.meter_title, replay_title)
            self.replay_title.setVisible(False)
        else:
            set_text(self.replay_title, replay_title + " · model replay")
            self.replay_title.setVisible(bool(t.coherence))
        self.meters.set_rows([(m.label, m.value, m.fraction, m.tone, m.tick) for m in t.coherence])
        self.meters.setVisible(bool(t.coherence))
        set_text(self.coherence_note, t.coherence_note if (t.coherence or link is None) else
                 "Per-fibre estimates of the backend engine for the last network second. The backend supplies "
                 "no memory-coherence (T1 / T2*) measurement.")

        conditions = [(v.key, v.value) for v in t.conditions]
        if link is not None:
            conditions = self._live_conditions(snap, link) + conditions
        self._set_conditions(conditions)
        set_text(self.claim_at, t.claim.at)
        set_text(self.claim_text, t.claim.claim)
        set_text(self.claim_reason, t.claim.reasoning)
        self._set_limits(list(t.limitations))
        self.artefacts.set_rows([(a.name, f"{a.digest} · {a.size}", "violet") for a in t.artefacts])
        self.artefact_empty.setVisible(not t.artefacts)
        if live is not None:
            source = "no input" if live.source_kind == "none" else f"{live.source_kind} input"
            window = f"Live · {source} · network time {live.network_time_s:.0f} s · 1 Hz history, last 300 s"
            self.bar.set_status(f"{window} · {t.window_note}" if t.window_note else window)
        else:
            self.bar.set_status(t.window_note)


__all__ = ["TelemetryScreen", "backend_link_thresholds", "history_values"]
