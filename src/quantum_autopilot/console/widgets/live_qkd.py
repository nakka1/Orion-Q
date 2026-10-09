"""Live QKD panel: the decoy-state BB84 model of the backend engine, fibre by fibre.

Shown wherever `Snapshot.live` exists. Every figure is a field of `LiveLink` /
`LiveQkd` / `LiveDetector` for the last network second, or a point of the
backend's 1 Hz `LiveLink.history`. The panel computes nothing: it formats the
numbers and plots the history point for point. The accumulated key pool grows
because the backend adds each period's key to it, not because the panel counts.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QGridLayout, QWidget

from ..contract import LINK_STATUS_TONE, MODEL_STATE_TONE, LiveLink, LiveView
from ..theme import C, mono, text as text_font, text_tone, tone
from .cards import (
    DASH, KVList, LinkPicker, clock_of, fmt_bits, fmt_fraction, fmt_number, fmt_percent, fmt_qber, fmt_rate,
    grid_row, head_row, mono_header, note, set_chip, set_text, status_chip,
)
from .charts import ChartSeries, SeriesChart, axis_labels
from .prims import Card, L, Label, hbox, scoped_style, set_color, vbox
from .records import RecordTable

TABLE_HEADERS = ["FIBRE", "STATUS", "QBER", "SIFTED", "KEY RATE", "KEY POOL", "DISCARDED", "LAST ACTION",
                 "DETECTOR", "ALARM"]


def controller_of(link: LiveLink, role: str):
    return next((item for item in link.controllers if item.role == role), None)


def controller_text(link: LiveLink, role: str) -> str:
    controller = controller_of(link, role)
    if controller is None:
        return DASH
    return controller.controller_id + (f" · model {controller.model_state}" if controller.model_state else "")


def detector_text(link: LiveLink) -> str:
    detector = link.detector
    if detector.label is not None:
        return detector.label
    return detector.state or DASH


def alarm_text(link: LiveLink) -> str:
    alarm = link.detector.alarm
    return DASH if alarm is None else "ALARM" if alarm else "none"


def qkd_values(link: LiveLink) -> list[tuple[str, str, str]]:
    """(key, value, tone) rows of one fibre's QKD session, straight from the snapshot."""

    qkd, detector = link.qkd, link.detector
    keyrate = controller_of(link, "keyrate")
    state_tone = MODEL_STATE_TONE.get(keyrate.model_state or "", "ink") if keyrate is not None else "ink"
    return [
        ("Protocol", qkd.protocol, "ink"),
        ("Signal intensity μ", fmt_number(qkd.signal_intensity, ".4f"), "ink"),
        ("Decoy intensity ν", fmt_number(qkd.decoy_intensity, ".4f"), "ink"),
        ("Clock rate", fmt_rate(qkd.clock_rate_hz, "Hz"), "ink"),
        ("Signal gain", fmt_number(qkd.signal_gain, ".6f"), "ink"),
        ("Decoy gain", fmt_number(qkd.decoy_gain, ".6f"), "ink"),
        ("QBER", fmt_qber(link.qber), "ink"),
        ("Sifted rate", fmt_rate(link.sifted_rate_hz, "Hz"), "ink"),
        ("Secret key rate", fmt_rate(link.skr_bps), "violet"),
        ("Key pool", fmt_bits(qkd.key_pool_bits), "violet"),
        ("Discarded on alarms", fmt_bits(qkd.discarded_bits), "red" if qkd.discarded_bits else "ink"),
        ("Last key-rate action", qkd.last_action or DASH, "ink"),
        ("Key-rate controller", controller_text(link, "keyrate"), state_tone),
        ("Detector", controller_text(link, "detector"), "ink"),
        ("Detector label", detector.label or DASH, "ink"),
        ("Attack probability", fmt_percent(detector.attack_probability), "ink"),
        ("Attack alarm", alarm_text(link), "red" if detector.alarm else "ink"),
        ("Detector state", detector.state or DASH, "ink"),
    ]


class Stage(QWidget):
    """One cell of the four-stage pipeline: index · name, 28 px value, note."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        scoped_style(self, f"border-left:1px solid {C.soft};")
        lay = vbox(self, (16, 12, 16, 12), 4)
        self.name = L("", mono(12), C.ink3, elide=True)
        lay.addWidget(self.name)
        reading = QWidget()
        rl = hbox(reading, (0, 0, 0, 0), 6)
        self.value = L(DASH, text_font(28), C.ink)
        self.value.setMinimumWidth(0)
        self.unit = L("", mono(13), C.ink3)
        rl.addWidget(self.value)
        rl.addWidget(self.unit, 0, Qt.AlignmentFlag.AlignBottom)
        rl.addStretch(1)
        lay.addWidget(reading)
        self.body = note("", 12.5)
        lay.addWidget(self.body)
        lay.addStretch(1)

    def set(self, name: str, value: str, unit: str, body: str, tone_name: str = "ink",
            numeric: bool = True) -> None:
        set_text(self.name, name)
        font = text_font(28) if numeric else mono(13)
        if self.value.font() != font:
            self.value.setFont(font)
        self.value.setWordWrap(not numeric)
        set_text(self.value, value)
        self.value.setToolTip(value)
        set_color(self.value, text_tone(tone_name))
        self.unit.setVisible(bool(unit))
        set_text(self.unit, unit)
        set_text(self.body, body)


class Pipeline(QWidget):
    """`grid-template-columns: repeat(4, 1fr)` row of stages."""

    def __init__(self, parent=None, count: int = 4) -> None:
        super().__init__(parent)
        self.grid = QGridLayout(self)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setSpacing(0)
        self.stages: list[Stage] = []
        self.set_count(count)

    def set_count(self, count: int) -> None:
        if count == len(self.stages):
            return
        while self.grid.count():
            widget = self.grid.takeAt(0).widget()
            if widget is not None:
                widget.hide()
                widget.setParent(None)
                widget.deleteLater()
        self.stages = [Stage() for _ in range(count)]
        for column, stage in enumerate(self.stages):
            self.grid.addWidget(stage, 0, column)
            self.grid.setColumnStretch(column, 1)


def split_unit(text: str) -> tuple[str, str]:
    value, _, unit = text.partition(" ")
    return value, unit


class LiveQkdPanel(QWidget):
    """The QKD model in action for one fibre, with the table of all fibres."""

    link_picked = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        root = vbox(self, (0, 0, 0, 0), 16)
        self._link = ""
        self._live: LiveView | None = None

        top = Card()
        self.title = Label("Live QKD · decoy-state BB84 · model in action")
        self.source_chip = status_chip("", "violet")
        self.tick = L("", mono(12), C.ink3)
        top.add(head_row(self.title, self.tick, self.source_chip))
        row = QWidget()
        rl = hbox(row, (0, 0, 0, 0), 14)
        rl.addWidget(Label("Fibre"))
        self.picker = LinkPicker()
        self.picker.picked.connect(self._pick)
        rl.addWidget(self.picker, 1)
        self.status_chip = status_chip("", "dim")
        rl.addWidget(self.status_chip)
        top.add(row)
        self.pipeline = Pipeline()
        top.add(self.pipeline)
        self.source_note = note("", 12.5)
        top.add(self.source_note)
        root.addWidget(top)

        chart_card = Card()
        self.chart_title = Label("Secret key rate and QBER", wrap=True)
        chart_card.add(self.chart_title)
        chart_card.add(L("■ secret key rate", mono(11), tone("violet")))
        self.skr_chart = SeriesChart(height=120, grow=True)
        chart_card.add(self.skr_chart, 1)
        chart_card.add(L("■ QBER", mono(11), tone("amber")))
        self.qber_chart = SeriesChart(height=140, grow=True)
        chart_card.add(self.qber_chart, 1)
        self.chart_note = note("", 12, C.ink3)
        chart_card.add(self.chart_note)

        session = Card()
        self.session_title = Label("Session", wrap=True)
        session.add(self.session_title)
        self.session = KVList(size=12.5)
        session.add(self.session)
        session.lay.addStretch(1)
        root.addWidget(grid_row([(chart_card, 2), session]))

        table_card = Card()
        table_card.add(Label("All fibres · last network second"))
        self.table = RecordTable(TABLE_HEADERS)
        self.table.setAccessibleName("Live QKD state of every fibre")
        self.table.clicked.connect(self._row_clicked)
        mono_header(self.table)
        table_card.add(self.table)
        root.addWidget(table_card)

    # ------------------------------------------------------------ selection
    def current(self) -> str:
        return self._link

    def set_current(self, link_id: str, *, presentation: bool = True) -> None:
        if link_id and link_id != self._link:
            self._link = link_id
            if presentation and self._live is not None:
                self.set_live(self._live)

    def _pick(self, link_id: str) -> None:
        self.set_current(link_id)
        self.link_picked.emit(link_id)

    def _row_clicked(self, index) -> None:
        if self._live is not None and 0 <= index.row() < len(self._live.links):
            self._pick(self._live.links[index.row()].id)

    def link(self) -> LiveLink | None:
        return self._live.link(self._link) if self._live is not None else None

    # ----------------------------------------------------------------- data
    def set_live(self, live: LiveView) -> None:
        self._live = live
        if live.link(self._link) is None:
            self._link = live.links[0].id if live.links else ""
        link = live.link(self._link)
        self.picker.set_ids([item.id for item in live.links], self._link)
        set_text(self.tick, f"tick {live.tick} · network time {live.network_time_s:.0f} s · {clock_of(live.at)} UTC")
        set_chip(self.source_chip, f"{live.source_kind} input".upper(),
                 "violet" if live.source_kind == "simulated" else "green" if live.source_kind == "measured" else "dim")
        set_text(self.source_note, " — ".join(part for part in (live.source_label, live.source_note) if part))
        rows = []
        for item in live.links:
            status_colour = tone(LINK_STATUS_TONE.get(item.status, "dim"))
            rows.append([
                (item.id, C.ink), (item.status, status_colour), (fmt_qber(item.qber), C.ink),
                (fmt_rate(item.sifted_rate_hz, "Hz"), C.ink), (fmt_rate(item.skr_bps), C.violet),
                (fmt_bits(item.qkd.key_pool_bits), C.violet),
                (fmt_bits(item.qkd.discarded_bits), C.red if item.qkd.discarded_bits else C.ink3),
                (item.qkd.last_action or DASH, C.ink), (detector_text(item), C.ink2),
                (alarm_text(item), C.red if item.detector.alarm else C.ink3)])
        self.table.set_rows(rows)
        self.table.setFixedHeight(min(330, 40 + 34 * max(1, len(rows))))
        if link is None:
            set_chip(self.status_chip, "NO FIBRE", "dim")
            for stage in self.pipeline.stages:
                stage.set("", DASH, "", "")
            self.session.set_rows([])
            self.skr_chart.set_series([], empty_text="No fibre in the backend state")
            self.qber_chart.set_series([], empty_text="No fibre in the backend state")
            return
        set_chip(self.status_chip, link.status.upper(), LINK_STATUS_TONE.get(link.status, "dim"))
        qkd = link.qkd
        clock, clock_unit = split_unit(fmt_rate(qkd.clock_rate_hz, "Hz"))
        sifted, sifted_unit = split_unit(fmt_rate(link.sifted_rate_hz, "Hz"))
        rate, rate_unit = split_unit(fmt_rate(link.skr_bps))
        stages = self.pipeline.stages
        stages[0].set("01 · PULSE CLOCK", clock, clock_unit,
                      f"signal μ {fmt_number(qkd.signal_intensity, '.4f')} · decoy ν "
                      f"{fmt_number(qkd.decoy_intensity, '.4f')}")
        stages[1].set("02 · SIFTED", sifted, sifted_unit,
                      f"signal gain {fmt_number(qkd.signal_gain, '.6f')} · decoy gain "
                      f"{fmt_number(qkd.decoy_gain, '.6f')}")
        stages[2].set("03 · QBER", DASH if link.qber is None else f"{link.qber * 100:.2f}",
                      "" if link.qber is None else "%", f"detector: {detector_text(link)} · alarm {alarm_text(link)}",
                      "red" if link.detector.alarm else "ink")
        stages[3].set("04 · SECRET KEY", rate, rate_unit,
                      f"{controller_text(link, 'keyrate')} · last action {qkd.last_action or DASH} · pool "
                      f"{fmt_bits(qkd.key_pool_bits)}", "violet")
        set_text(self.session_title, f"Session · {link.id} · {link.a} ↔ {link.b}")
        self.session.set_rows(qkd_values(link))
        history = link.history
        labels = axis_labels(history.t_s[0], history.t_s[-1], 5, lambda value: f"t {value:.0f} s") \
            if history.t_s else []
        self.skr_chart.set_series([ChartSeries(f"{link.id} key rate", history.t_s, history.skr_bps, "violet", 2.0)],
                                  y_format=fmt_rate, empty_text="No key-rate history for this fibre yet")
        self.qber_chart.set_series([ChartSeries(f"{link.id} QBER", history.t_s, history.qber, "amber", 2.0)],
                                   y_format=lambda value: f"{value * 100:.2f} %", x_labels=labels,
                                   empty_text="No QBER history for this fibre yet")
        set_text(self.chart_title, f"Secret key rate and QBER · {link.id} · backend history")
        set_text(self.chart_note,
                 f"{len(history.t_s)} points, one per network second, as kept by the backend (last 300 s). "
                 f"Stability {fmt_fraction(link.stability)}.")

    def values(self) -> dict[str, str]:
        """The session card as shown: {row key: text}."""

        return self.session.values()


__all__ = ["LiveQkdPanel", "Pipeline", "Stage", "TABLE_HEADERS", "alarm_text", "controller_text", "detector_text",
           "qkd_values"]
