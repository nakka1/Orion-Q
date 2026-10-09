"""Floating node window: everything the backend holds about one node, and its wave chart.

A window is a movable, non-modal panel that stays above the map. Several can be
open; each closes with its ✕ or Esc. It shows every field of `contract.LiveNode`
(or what the topology carries, for a source without a network engine), each
incident fibre with its live values, the running / last recalibration, the
demands routed through the node, received protocol observations, and a real-time
wave chart for legacy polarisation connections (`LiveLink.wave`). Actions only name a backend operation; the result
is what the next snapshot shows. Timestamped protocol observables use their own
panel and physical axes, without conversion into an optical wave.
"""
from __future__ import annotations

from collections.abc import Mapping
from collections import deque
from time import perf_counter
from typing import TypedDict

from PySide6.QtCore import QEvent, QObject, QPoint, QPointF, QTimer, Qt, Signal
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import QApplication, QLayout, QSizePolicy, QWidget

from ..contract import DEMAND_STATUS_TONE, LINK_STATUS_TONE
from ..theme import C, mono, text as text_font, text_tone
from .figures import marker_kind, paint_marker
from .inspector import (
    ChipRow, CommandStatus, KVList, StatusLine, UNROUTED, clock, dash, demand_reason, node_status_chips,
    number, op_button, percent, rate,
)
from .network_views import NetModel, format_km, format_lat, format_lon
from .prims import (
    Chip, Clickable, Frame, L, Label, Prose, RowBtn, Track, hbox, scoped_style,
    scroll_panel, set_color, vbox,
)
from .wave_chart import LegendSwatch, WaveChart, WaveSeries
from .metric_table import MetricTable
from .protocol_observations import node_protocol_observations
from .protocol_series import ProtocolSeriesPanel
from .brand_mark import OrionQWordmark
from .loading_state import LoadingStatus

WINDOW_W, WINDOW_H = 548, 610
NO_WAVE_SOURCE = "this source delivers no wave"
WAVE_CAPTION = 'Visibilidade de polarização · enlaces legados · 1 − 2·QBER'

#: Label of every `LiveNode` field as the window shows it (tests read `NodeWindow.field_texts()`).
NODE_FIELDS = ("id", "role", "kind", "status", "located", "latitude", "longitude", "place", "links",
               "memory_state", "pairs", "temperature_c", "calibration", "calibration_count",
               "routes_through", "address", "port")


class NodePresentationStatus(TypedDict):
    ready: bool
    state: str
    presentation_kind: str
    generation: int
    rendered_generation: int | None
    core_tick: int | None
    latest_tick: int | None
    presented_tick: int | None
    source_kind: str
    source_label: str
    network_time_s: float | None
    expected_link_keys: tuple[str, ...]
    completed_link_keys: tuple[str, ...]
    expected_sections: tuple[str, ...]
    completed_sections: tuple[str, ...]
    pending: int
    pending_items: int
    pending_core_rows: int
    step_max_ms: float
    error: str


class _Marker(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.kind, self.health, self.calibrating = "node", "up", False
        self.setFixedSize(36, 36)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

    def set_node(self, kind: str, health: str, calibrating: bool) -> None:
        if (kind, health, calibrating) != (self.kind, self.health, self.calibrating):
            self.kind, self.health, self.calibrating = kind, health, calibrating
            self.update()

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        paint_marker(p, QPointF(self.width() / 2, self.height() / 2), self.kind, self.health,
                     calibrating=self.calibrating)
        p.end()


class _LegendRow(RowBtn):
    """Legend entry of one connection: swatch, peer, fibre, state / reason, numeric stability."""

    hovered = Signal(str)

    def __init__(self, index: int, key: str) -> None:
        super().__init__(padding=(8, 6, 8, 6), gap=10)
        self.key = key
        self.swatch = self.add(LegendSwatch(index))
        self.peer = self.add(L("", mono(12.5), C.ink, elide=True), 1)
        self.link = self.add(L("", mono(12), C.ink3, elide=True), 1)
        for label in (self.peer, self.link):
            label.setMinimumWidth(0)
        self.note = self.add(L("", text_font(12), C.ink3, elide=True), 1)
        self.note.setMinimumWidth(0)
        self.note.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.stability = self.add(L("", mono(12.5), C.ink, Qt.AlignmentFlag.AlignRight))

    def text(self) -> str:
        return " · ".join(part for part in (self.peer.text(), self.link.text(), self.note.full_text,
                                            self.stability.text()) if part)

    def enterEvent(self, event) -> None:
        self.hovered.emit(self.key)
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self.hovered.emit("")
        super().leaveEvent(event)


class _FibreCard(Frame):
    def __init__(self, window: "NodeWindow", key: str) -> None:
        QWidget.__init__(self)
        scoped_style(self, f"background:{C.bg};border:1px solid {C.soft};")
        self.key = key
        self._active: bool | None = None
        lay = vbox(self, (12, 10, 12, 10), 6)
        head = QWidget()
        row = hbox(head, (0, 0, 0, 0), 8)
        self.name = Clickable(key)
        self.name.setMinimumWidth(0)
        self.name.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.name.setToolTip('Inspecionar enlace · '+key)
        self.name.setFont(mono(13))
        self.name.setStyleSheet(f"QPushButton{{color:{C.ink};border:0;background:transparent;text-align:left;"
                                f"padding:0;text-decoration:underline;}}QPushButton:hover{{color:{C.acc_t};}}")
        self.name.clicked.connect(lambda: window.select.emit("link", self.key))
        row.addWidget(self.name, 1)
        self.peer = L("", mono(12.5), C.ink2, elide=True)
        self.peer.setMinimumWidth(0)
        row.addWidget(self.peer, 1)
        self.chip = Chip("", "mute")
        row.addWidget(self.chip)
        row.addStretch(1)
        self.toggle = op_button("Disable", "set_link_active", height=28)
        self.toggle.setFixedHeight(28)
        self.toggle.clicked.connect(self._toggle)
        row.addWidget(self.toggle)
        lay.addWidget(head)
        # Compact navigation headers may elide long IDs; these full fields
        # remain readable in the same scrolling document at every panel width.
        self.identity = KVList(size=12.5, wrap=True)
        lay.addWidget(self.identity)
        self.left, self.right = KVList(size=12.5, wrap=True), KVList(size=12.5, wrap=True)
        columns = QWidget()
        cols = vbox(columns, (0, 0, 0, 0), 0)
        cols.addWidget(self.left);cols.addWidget(self.right)
        lay.addWidget(columns)
        self.controllers = Prose("", 12, C.ink3)
        lay.addWidget(self.controllers)
        self._window = window
        self._satellite_dialog=None;self._satellite_metrics={};self._satellite_session='';self._satellite_clock=None
        self.satellite_details=Clickable('HOUSEKEEPING / SCIENCE · SATÉLITE / OGS')
        self.satellite_details.setToolTip('Inspeciona os dados orbitais recebidos, apontamento, perdas, cliques e QKD derivados pelo backend.')
        self.satellite_details.clicked.connect(self._show_satellite)
        self.satellite_details.hide();lay.addWidget(self.satellite_details)

    def _show_satellite(self):
        if self._satellite_dialog is None:
            from .satellite_telemetry import SatelliteTelemetryDialog
            self._satellite_dialog=SatelliteTelemetryDialog(self._window)
        self._satellite_dialog.set_metrics(self._satellite_metrics,session_id=self._satellite_session,clock_s=self._satellite_clock)
        self._satellite_dialog.show();self._satellite_dialog.raise_()

    def _toggle(self) -> None:
        if self._active is not None:
            self._window.action.emit("set_link_active", "link", self.key, {"active": not self._active})

    def show_link(self, node_id: str, link, live_link, *, clock_s=None) -> None:
        self._satellite_clock=clock_s
        # Wave buffers and rolling link histories change on every publication,
        # but neither is rendered in a card. Skip identical visible fields so
        # a new tick cannot re-polish all existing card layouts unnecessarily.
        if live_link is None:
            signature = (node_id, link, None)
        else:
            provenance = live_link.provenance
            metrics = provenance.get('protocol_metrics', {})
            technology = provenance.get('technology') or {}
            if 'satellite_doppler' in technology.get('technologies', ()):
                # A hidden drill-down must open the newest received science and
                # housekeeping record even when the compact card is unchanged.
                self._satellite_metrics = metrics
                self._satellite_session = technology['session_id']
            signature = (node_id, link, live_link.status, live_link.distance_km,
                         live_link.fidelity, live_link.qber, live_link.skr_bps,
                         live_link.sync_residual_ps, live_link.polarisation_residual_mrad,
                         live_link.stability, live_link.detector, live_link.controllers,
                         live_link.status_reasons,
                         tuple(repr(provenance.get(key)) for key in
                               ('technology', 'source', 'session_status', 'security_status', 'repeater_scope', 'channels')),
                         tuple(metrics.get(key) for key in ('qber', 'conditional_key_estimate_bps', 'certified_secret_bits',
                               'coincidences', 'phase_reference_residual_rad', 'generation_pair_rate_hz',
                               'delivered_pair_rate_hz', 'bsm_success_fraction', 'purification_yield')),
                         clock_s if self._satellite_dialog is not None and self._satellite_dialog.isVisible() else None)
        if signature == getattr(self, '_shown_signature', None):
            return
        self.satellite_details.hide()
        other = link.b if link.a == node_id else link.a
        self.identity.set_rows([('Identificador do enlace', self.key), ('Ponto conectado', other)])
        self.peer.setText(f"↔ {other}")
        self._active = link.configured_active
        self.toggle.setText("Enable" if link.configured_active is False else "Disable")
        prior_ready = self.toggle.property('ready')
        ready = link.configured_active is not None and bool(link.id)
        self.toggle.setProperty("ready", ready)
        if not ready:self.toggle.setEnabled(False)
        elif prior_ready is False:self._window.materialized.emit()
        if live_link is not None:
            name = LINK_STATUS_TONE.get(live_link.status, "muted")
            self.chip.setText(live_link.status.replace("_", " "))
            self.chip.set_tone({"green": "ok", "amber": "warn", "red": "bad", "violet": "sat"}.get(name, "mute"),
                               name in ("green", "amber", "red", "violet"))
            detector = live_link.detector
            alarm = "ALARM" if detector.alarm else "no alarm" if detector.alarm is False else "—"
            technology = live_link.provenance.get('technology')
            if not technology:
                self.left.set_rows([
                    ("Distance", format_km(live_link.distance_km)),
                    ("Fidelity", number(live_link.fidelity, ".4f")),
                    ("QBER", percent(live_link.qber)),
                    ("Secret key rate", rate(live_link.skr_bps)),
                ])
                self.right.set_rows([
                    ("Sync residual", number(live_link.sync_residual_ps, ".2f", "ps")),
                    ("Polarisation residual", number(live_link.polarisation_residual_mrad, ".1f", "mrad")),
                    ("Stability", number(live_link.stability, ".3f")),
                    ("Detector", f"{dash(detector.label)} · {alarm}", "red" if detector.alarm else "ink"),
                ])
            if technology:
                metrics = live_link.provenance.get('protocol_metrics', {})
                satellite='satellite_doppler' in technology['technologies']
                self.satellite_details.setVisible(satellite)
                if satellite:
                    self._satellite_metrics=metrics;self._satellite_session=technology['session_id']
                    if self._satellite_dialog is not None and self._satellite_dialog.isVisible():
                        self._satellite_dialog.set_metrics(metrics,session_id=self._satellite_session,clock_s=clock_s)
                self.left.set_rows([
                    ('Distance', format_km(live_link.distance_km)),
                    ('Fidelity estimate', number(live_link.fidelity, '.4f')),
                    ('Session', technology['session_id']),
                    ('Input origin', live_link.provenance.get('source', 'unavailable')),
                ])
                if any(t in technology['technologies'] for t in ('tf_qkd', 'satellite_doppler')):
                    rows = [('QBER', percent(metrics.get('qber'))),
                            ('Conditional budget', rate(metrics.get('conditional_key_estimate_bps'))),
                            ('Certified key bits', str(metrics.get('certified_secret_bits', 0)))]
                    if 'satellite_doppler' in technology['technologies']:
                        rows.append(('Coincidences', number(metrics.get('coincidences'), '.0f')))
                    else:
                        rows.append(('Phase residual', number(metrics.get('phase_reference_residual_rad'), '.4f', 'rad')))
                else:
                    rows = [('Pair generation', number(metrics.get('generation_pair_rate_hz'), '.3g', 'pair/s')),
                            ('Pair delivery', number(metrics.get('delivered_pair_rate_hz'), '.3g', 'pair/s')),
                            ('BSM success', percent(metrics.get('bsm_success_fraction'))),
                            ('Purification yield', percent(metrics.get('purification_yield')))]
                self.right.set_rows(rows)
            notes = [f"{item.role}: {item.controller_id}" + (f" ({item.model_state})" if item.model_state else "")
                     for item in live_link.controllers]
            if technology:
                notes.append('technology: ' + ' + '.join(technology['technologies']))
                notes.append('session: ' + live_link.provenance.get('session_status', 'configured'))
                notes.append(live_link.provenance.get('security_status', ''))
            channels = live_link.provenance.get("channels", {})
            configuration = channels.get("configuration") or {}
            if configuration:
                classical = configuration["classical"]["channel_id"]
                quantum = configuration["quantum"]["channel_id"]
                notes.append(f"channels: {classical} ↔ {quantum} · {channels.get('status', 'no_input')} "
                             "· software statistical ports; no physical transmission")
            elif channels:
                notes.append("channels: not configured; no communication attested")
            if channels.get("error"):
                notes.append("channel intake refused: " + channels["error"])
            if live_link.provenance.get("repeater_scope"):
                notes.append(live_link.provenance["repeater_scope"])
            if detector.state:
                notes.append(f"detector state: {detector.state}")
            if live_link.status_reasons:
                notes.append("status: " + "; ".join(live_link.status_reasons))
            self.controllers.setText("Controllers · " + " · ".join(notes) if notes else "")
        else:
            self.satellite_details.hide()
            self.chip.setText(link.state_text.lower())
            self.left.set_rows([("Distance", format_km(link.distance_km)), ("Fidelity", link.fidelity)])
            self.right.set_rows([("QBER", link.qber), ("Secret key rate", link.skr)])
            self.controllers.setText("")
        self.controllers.setVisible(bool(self.controllers.text()))
        self._shown_signature = signature

    def texts(self) -> dict:
        return {"link": self.key, "peer": self.peer.text(), "status": self.chip.text(), **self.left.texts(),
                **self.right.texts(), "controllers": self.controllers.text()}


class NodeWindow(Frame):
    select = Signal(str, str)
    command = Signal(str, str)
    action = Signal(str, str, str, object)       # verb, subject kind, subject id, payload fields
    locate = Signal(str)
    closed = Signal(str)
    materialized = Signal()

    def __init__(self, node_id: str, parent=None, *, incremental: bool = False) -> None:
        QWidget.__init__(self, parent)
        scoped_style(self, f"background:{C.raised};border:1px solid {C.line};")
        # The window covers the map with an opaque ground; telling Qt so keeps the map (and every
        # other widget under it) out of the repaints the wave animation causes.
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, True)
        self.node_id = node_id
        self._drag: QPoint | None = None
        self._isolated = False
        self._legend: dict[str, _LegendRow] = {}
        self._cards: dict[str, _FibreCard] = {}
        self._pinned = ""
        self._satellite_dialog = None
        self._satellite_state = {}
        self._satellite_permissions = {}
        self._satellite_mode = None
        self.incremental = incremental
        self._generation = 0
        self._rendered_generation = None
        self._latest_context = None
        self._presentation_snapshot = None
        self._source_context = None
        self._expected_keys = ()
        self._views_configured = False
        self._pending_widgets = deque()
        self._link_generations = {}
        self._link_context = {}
        self._section_rows = {}
        self._presented_rows = {}
        self._previous_tick = None
        self._deferred_refresh = False
        self._expected_sections = ()
        self._section_generations = {}
        self._closed_presentation = False
        self._presentation_state = 'empty'
        self._presentation_error = ''
        self._step_max_ms = 0.
        self._presentation_timer = QTimer(self)
        self._presentation_timer.setSingleShot(True)
        self._presentation_timer.setInterval(20)
        self._presentation_timer.timeout.connect(self._materialize_step)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName(f"Node {node_id}")
        root = vbox(self, (1, 1, 1, 1), 0)

        # ---- title bar (drag handle)
        self.bar = Frame(bg=C.panel, border=C.soft, sides="b")
        self.bar.setCursor(Qt.CursorShape.SizeAllCursor)
        bar = hbox(self.bar, (12, 8, 8, 8), 10)
        self.marker = _Marker()
        bar.addWidget(self.marker)
        self.title = L(node_id, mono(18), C.ink)
        self.title.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        bar.addWidget(self.title)
        self.chips = ChipRow()
        self.chips.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        bar.addWidget(self.chips, 1)
        self.close_button = Clickable("✕")
        self.close_button.setFont(mono(13))
        self.close_button.setFixedSize(30, 30)
        self.close_button.setAccessibleName(f"Close the window of node {node_id}")
        self.close_button.setToolTip("Close (Esc)")
        self.close_button.setStyleSheet(
            f"QPushButton{{color:{C.ink2};border:1px solid {C.line};background:transparent;}}"
            f"QPushButton:hover{{color:{C.ink};background:{C.hover};}}")
        self.close_button.clicked.connect(self.close_window)
        bar.addWidget(self.close_button)
        root.addWidget(self.bar)

        # ---- body
        body = QWidget()
        lay = vbox(body, (14, 12, 14, 12), 10)
        # Rows arrive after the window opens. Update the scroll content's minimum size
        # with them so the first repaint scrolls instead of squeezing rows over the chart.
        lay.setSizeConstraint(QLayout.SizeConstraint.SetMinAndMaxSize)
        from .satellite_telemetry import SatelliteLivePanel
        self.satellite_panel=SatelliteLivePanel();self.satellite_panel.hide()
        self.satellite_panel.details_requested.connect(self._satellite_details)
        lay.addWidget(self.satellite_panel)
        self.protocol_series = ProtocolSeriesPanel()
        lay.addWidget(self.protocol_series)
        self.protocol_label = Label('Observações dos protocolos · recebidas pelo canal clássico', wrap=True)
        self.protocol_label.setToolTip('Dados da sessão publicados pelo backend após aquisição, validação e recebimento clássico. '
                                       'Um bloco recebido sem pares ou cliques úteis não estabelece uma rota quântica.')
        self.protocol_label.hide()
        lay.addWidget(self.protocol_label)
        self.protocols = MetricTable()
        self.protocols.hide()
        lay.addWidget(self.protocols)
        self.wave_label = Label(WAVE_CAPTION, wrap=True)
        lay.addWidget(self.wave_label)
        self.chart = WaveChart(empty_message='Nenhuma amostra de polarização recebida')
        self.chart.setFixedHeight(170)
        lay.addWidget(self.chart)
        self.legend_host = QWidget()
        self.legend_lay = vbox(self.legend_host, (0, 0, 0, 0), 0)
        lay.addWidget(self.legend_host)

        self.identity, self.state = KVList(wrap=True), KVList(wrap=True)
        columns = QWidget()
        cols = vbox(columns, (0, 0, 0, 0), 0)
        cols.addWidget(self.identity);cols.addWidget(self.state)
        self.identity_host = columns
        lay.addWidget(columns)

        self.calibration_label = Label("Calibration")
        lay.addWidget(self.calibration_label)
        self.calibration_progress = QWidget()
        progress = vbox(self.calibration_progress, (0, 0, 0, 0), 4)
        self.calibration_phase = L("", mono(12.5), C.violet_t)
        progress.addWidget(self.calibration_phase)
        self.calibration_track = Track(0.0, "violet")
        progress.addWidget(self.calibration_track)
        lay.addWidget(self.calibration_progress)
        self.calibration_kv = KVList(size=12.5, wrap=True)
        lay.addWidget(self.calibration_kv)
        self.calibration_links = KVList(size=12.5, wrap=True)
        lay.addWidget(self.calibration_links)

        self.routes_label = Label("Demands through this node")
        lay.addWidget(self.routes_label)
        self.routes = KVList(size=12.5, wrap=True)
        lay.addWidget(self.routes)
        self.unrouted = Prose("", 12.5, C.red_t)
        lay.addWidget(self.unrouted)

        self.connections_label = Label("Connections")
        lay.addWidget(self.connections_label)
        self.cards_host = QWidget()
        self.cards_lay = vbox(self.cards_host, (0, 0, 0, 0), 8)
        lay.addWidget(self.cards_host)
        lay.addStretch(1)
        self.scroll = scroll_panel(body, 120)
        self.scroll.setStyleSheet(f"QScrollArea{{background:{C.raised};border:0;}}")
        root.addWidget(self.scroll, 1)

        # ---- actions
        footer = Frame(border=C.soft, sides="t")
        foot = vbox(footer, (14, 10, 14, 10), 6)
        loading_row = QWidget()
        loading_layout = hbox(loading_row, (0, 0, 0, 0), 12)
        self.loading_brand = OrionQWordmark(24)
        loading_layout.addWidget(self.loading_brand, 0, Qt.AlignmentFlag.AlignTop)
        self.loading = LoadingStatus()
        self.loading.start("Aguardando observações do backend.", total=1)
        self._loading_generation = None
        self.presentation_line = self.loading.message
        self.presentation_line.setFont(text_font(11.5))
        self.presentation_line.setMinimumHeight(self.presentation_line.fontMetrics().height()*3)
        loading_layout.addWidget(self.loading, 1)
        foot.addWidget(loading_row)
        buttons = QWidget()
        row = hbox(buttons, (0, 0, 0, 0), 8)
        self.recalibrate = op_button("Recalibrate", "recalibrate_node", accent=True)
        self.isolate = op_button("Isolate", "set_node_isolation")
        self.locate_button = op_button("Set location…", "set_node_location")
        for button in (self.recalibrate, self.isolate, self.locate_button):
            row.addWidget(button)
        row.addStretch(1)
        foot.addWidget(buttons)
        self.status = StatusLine()
        foot.addWidget(self.status)
        root.addWidget(footer)
        self.recalibrate.clicked.connect(lambda: self.action.emit("recalibrate_node", "node", self.node_id, {}))
        self.isolate.clicked.connect(
            lambda: self.action.emit("set_node_isolation", "node", self.node_id, {"isolated": not self._isolated}))
        self.locate_button.clicked.connect(lambda: self.locate.emit(self.node_id))
        self.chart.highlight_changed.connect(self._show_highlight)
        self.resize(WINDOW_W, WINDOW_H)
        self._fields: dict = {}
        self._protocol_link_keys = frozenset()

    # ---------------------------------------------------------------- window
    def close_window(self) -> None:
        self.cancel_presentation()
        if self._satellite_dialog is not None:self._satellite_dialog.hide()
        self.hide()
        self.closed.emit(self.node_id)

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Escape:
            self.close_window()
            event.accept()
            return
        super().keyPressEvent(event)

    def mousePressEvent(self, event) -> None:
        self.raise_()
        if event.button() == Qt.MouseButton.LeftButton and self.bar.geometry().contains(event.position().toPoint()):
            self._drag = event.globalPosition().toPoint() - self.pos()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._drag is not None:
            self.move_within(event.globalPosition().toPoint() - self._drag)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        self._drag = None
        super().mouseReleaseEvent(event)

    def move_within(self, point: QPoint) -> None:
        """Move, keeping the title bar reachable inside the host."""

        host = self.parentWidget()
        if host is None:
            self.move(point)
            return
        x = max(-self.width() + 120, min(point.x(), host.width() - 120))
        y = max(0, min(point.y(), host.height() - 44))
        self.move(x, y)

    def fit_host(self) -> None:
        host = self.parentWidget()
        if host is not None:
            self.resize(min(WINDOW_W, max(360, host.width() - 16)), min(WINDOW_H, max(260, host.height() - 16)))
            self.move_within(self.pos())

    def hideEvent(self, event) -> None:
        self._presentation_timer.stop()
        if self._pending_widgets and not self._closed_presentation and self._presentation_state != 'error':
            self._presentation_state = 'paused'
        super().hideEvent(event)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if self._deferred_refresh and not self._closed_presentation:
            self._deferred_refresh = False
            self.refresh(*self._latest_context)
        if self._pending_widgets and not self._closed_presentation and self._presentation_state != 'error':
            self._presentation_state = 'presenting'
            self._presentation_timer.start()
            self._update_presentation_line()

    def cancel_presentation(self, state='cancelled') -> None:
        self._closed_presentation = True
        self._generation += 1
        self._presentation_timer.stop()
        self._pending_widgets.clear()
        self._presentation_snapshot = None
        self._latest_context = None
        self._link_context.clear()
        self._section_rows.clear()
        self._presented_rows.clear()
        self._presentation_state = state
        self._presentation_error = ''
        self.loading.dispose()

    @staticmethod
    def _origin(snap):
        live = snap.live
        return (getattr(snap, 'input_kind', None), getattr(snap, 'campaign', None),
                getattr(getattr(snap, 'capabilities', None), 'adapter_id', None),
                getattr(snap.topology, 'id', None),
                *((live.source_kind, live.source_label, live.source_seed, live.source_scenario)
                  if live is not None else (None, None, None, None)))

    @staticmethod
    def _retire_views(layout, rows, wanted, *, discard=False):
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget is not None and widget not in rows.values():
                widget.hide(); widget.deleteLater()
        retained = {}
        for key, row in rows.items():
            if discard or key not in wanted:
                row.hide(); row.deleteLater()
            else:
                retained[key] = row
        for key in wanted:
            if key in retained:
                layout.addWidget(retained[key])
        return {key: retained[key] for key in wanted if key in retained}

    def _configure_link_views(self, keys, *, discard=False):
        if self._views_configured and keys == self._expected_keys and not discard:
            return
        self._views_configured = True
        self._presentation_timer.stop()
        self._legend = self._retire_views(self.legend_lay, self._legend, keys, discard=discard)
        self._cards = self._retire_views(self.cards_lay, self._cards, keys, discard=discard)
        self._expected_keys = keys
        self._link_generations = {}
        sections = [task for task in self._pending_widgets if task[0] in ('core', 'satellite')]
        self._pending_widgets = deque(sections + [(kind, key, index) for index, key in enumerate(keys)
            for kind, rows in (('legend', self._legend), ('card', self._cards)) if key not in rows])
        if not keys:
            self.legend_lay.addWidget(Prose("This node has no fibre: there is no connection to trace.", 12.5, C.ink3))
            self.cards_lay.addWidget(Prose("No fibre is configured for this node.", 12.5, C.ink3))
        if self._pinned not in keys:
            self._pinned = ''; self.chart.set_highlight('')

    def _refresh_legend(self, key):
        row = self._legend.get(key)
        if row is None:
            return
        index, node_id, link, item, engine, _clock = self._link_context[key]
        row.swatch.setVisible(key not in self._protocol_link_keys)
        if row.swatch.index != index:
            row.swatch.index = index; row.swatch.update()
        row.peer.setText(link.b if link.a == node_id else link.a)
        row.link.setText(link.key)
        if item.note:
            row.note.setText(item.note)
            set_color(row.note, text_tone('amber' if engine is not None else 'dim'))
        else:
            row.note.setText(engine.status.replace('_', ' '))
            set_color(row.note, text_tone(LINK_STATUS_TONE.get(engine.status, 'muted')))
        if key in self._protocol_link_keys:
            row.stability.setText('sessão ' + str(engine.provenance.get('session_status', 'não publicada'))
                                  if engine is not None else 'sessão não publicada')
        else:
            row.stability.setText('stability ' + number(engine.stability if engine is not None else None, '.3f'))
        row.setToolTip(row.text())

    def _refresh_card(self, key):
        card = self._cards.get(key)
        if card is not None:
            _index, node_id, link, _item, engine, clock_s = self._link_context[key]
            card.show_link(node_id, link, engine, clock_s=clock_s)

    def _core_widget(self, key):
        return getattr(self, 'calibration_kv' if key == 'calibration' else key)

    def _show_core(self, key):
        visible = key not in ('calibration_links', 'protocols') or bool(self._section_rows[key])
        self._core_widget(key).setVisible(visible)
        if key == 'protocols':self.protocol_label.setVisible(visible)

    def _set_core_rows(self, key, rows):
        """Coalesce presentation only; reuse equal rows and yield changed sections."""
        if key == 'protocols' and not rows:
            # An absent section cannot mark the initial construction as already
            # started and move legacy metadata behind its connection widgets.
            if self.protocols.rows:self.protocols.set_rows(())
            self.protocols.hide();self.protocol_label.hide()
            self._presented_rows.pop(key, None)
            self._section_generations.pop(key, None)
            return
        self._section_rows[key] = tuple(rows)
        widget = self._core_widget(key)
        if not self.incremental or not rows:
            widget.set_rows(rows)
            self._presented_rows[key] = tuple(rows)
            self._section_generations[key] = self._generation
            self._show_core(key)
        elif self._presented_rows.get(key) == tuple(rows):
            self._section_generations[key] = self._generation
            self._show_core(key)
        else:
            # Existing observations remain readable while the footer identifies
            # their previous publication. No layout or update runs in this tick.
            if not all(row[0] in widget.rows for row in rows):widget.hide()
            self._section_generations.pop(key, None)

    def _configure_sections(self, kind, keys):
        self._expected_sections = tuple(keys)
        self._section_generations = {key: self._section_generations[key] for key in keys
                                     if key in self._section_generations}
        retained = [task for task in self._pending_widgets if task[0] in ('legend', 'card', 'refresh')
                    or (task[0] == kind and task[1] in keys
                        and self._section_generations.get(task[1]) != self._generation)]
        queued = {task[1] for task in retained if task[0] == kind}
        added = [(kind, key, index) for index, key in enumerate(keys)
                 if key not in queued and self._section_generations.get(key) != self._generation]
        # Metadata leads initial construction. Later updates preserve FIFO order;
        # prepending dirty sections on every fast tick starves existing cards.
        initial = not self._legend and not self._cards and not self._section_generations.keys()-{'calibration_links'}
        self._pending_widgets = deque(added+retained if initial else retained+added)

    def _materialize_one(self, kind, key, index):
        if kind == 'core':
            widget = self._core_widget(key)
            rows = self._section_rows[key]
            missing = [row for row in rows if row[0] not in widget.rows]
            if self.incremental and key != 'protocols' and len(missing) > 1:
                first = missing[0][0]
                widget.set_rows([row for row in rows if row[0] in widget.rows or row[0] == first])
                return False  # Keep this section queued; create one new KVRow per turn.
            widget.set_rows(rows)
            self._presented_rows[key] = tuple(rows)
            self._show_core(key)
            self._section_generations[key] = self._generation
            return
        if kind == 'satellite':
            self.satellite_panel.present_section(key)
            self._section_generations[key] = self._generation
            return
        if key not in self._link_context:
            return
        if kind == 'refresh':
            self._refresh_legend(key)
            self._refresh_card(key)
            self._link_generations[key] = self._generation
            return
        if kind == 'legend':
            row = _LegendRow(index, key)
            row.hovered.connect(self._hover)
            row.clicked.connect(lambda checked=False, ident=key: self._pin(ident))
            self._legend[key] = row
            self._legend = {ident: self._legend[ident] for ident in self._expected_keys if ident in self._legend}
            position = list(self._legend).index(key)
            self.legend_lay.insertWidget(position, row)
            self._refresh_legend(key)
        else:
            card = _FibreCard(self, key)
            self._cards[key] = card
            self._cards = {ident: self._cards[ident] for ident in self._expected_keys if ident in self._cards}
            position = list(self._cards).index(key)
            self.cards_lay.insertWidget(position, card)
            self._refresh_card(key)
        if key in self._cards and key in self._legend:
            self._link_generations[key] = self._generation

    def _materialize_step(self):
        if self._closed_presentation or not self.isVisible() or self._presentation_state == 'error':
            return
        started = perf_counter()
        generation = self._generation
        made = False
        created = False
        refreshed = 0
        # At most one new expensive card; a small legend can share its turn.
        # A Qt operation cannot be preempted by the wall budget; expose its max.
        while self._pending_widgets:
            kind = self._pending_widgets[0][0]
            key = self._pending_widgets[0][1]
            new = (kind == 'card' or kind == 'legend' or
                   (kind == 'core' and any(row[0] not in self._core_widget(key).rows
                                           for row in self._section_rows[key])) or
                   (kind == 'satellite' and (key.removeprefix('station:') not in self.satellite_panel.stations
                    if key.startswith('station:') else key not in (self.satellite_panel._tables or {}))))
            if not self._materialize_pending():
                break
            made = True
            created = created or new
            if kind == 'refresh':refreshed += 1
            # Group small existing updates so one repaint covers several cards.
            # Creation remains one expensive item per turn, with no forced GC.
            if kind in ('card', 'core', 'satellite') or refreshed >= 4 or perf_counter()-started >= .006:
                break
        self._step_max_ms = max(self._step_max_ms, (perf_counter()-started)*1000)
        if made and created:
            self.materialized.emit()  # New controls are capability-gated before the next input turn.
        if self._closed_presentation or generation != self._generation:
            self._step_max_ms = max(self._step_max_ms, (perf_counter()-started)*1000)
            return
        self._update_presentation_line()
        if self._presentation_state == 'error':
            self._step_max_ms = max(self._step_max_ms, (perf_counter()-started)*1000)
            return
        if self._pending_widgets:
            self._presentation_timer.start()
        else:
            self._complete_presentation()
        self._step_max_ms = max(self._step_max_ms, (perf_counter()-started)*1000)

    def _materialize_pending(self):
        """Commit a visual item only after creation and population both succeed."""
        kind, key, index = self._pending_widgets[0]
        try:
            complete = self._materialize_one(kind, key, index)
        except Exception as error:
            if kind in ('legend', 'card'):
                rows = self._legend if kind == 'legend' else self._cards
                row = rows.pop(key, None)
                if row is not None:
                    row.hide(); row.deleteLater()
                self._link_generations.pop(key, None)
            elif kind == 'refresh':
                self._link_generations.pop(key, None)
            else:
                self._section_generations.pop(key, None)
                if kind == 'core':self._core_widget(key).hide()
            self._presentation_timer.stop()
            self._rendered_generation = None
            self._presentation_snapshot = None
            self._presentation_state = 'error'
            # Exception messages may contain addresses/credentials supplied by an
            # adapter. Expose the type and affected view, never its arbitrary text.
            self._presentation_error = f'Falha na visualização de {key} ({type(error).__name__})'
            self._update_presentation_line()
            return False
        if complete is not False:self._pending_widgets.popleft()
        return True

    def _complete_presentation(self):
        self._presentation_timer.stop()
        self._presentation_state = 'ready'
        self._rendered_generation = self._generation
        if self._latest_context is not None:
            self._presentation_snapshot = self._latest_context[:2]
        self._update_presentation_line()

    def _schedule_presentation(self):
        if not self.incremental:
            made = bool(self._pending_widgets)
            while self._pending_widgets:
                if not self._materialize_pending():break
            if made:self.materialized.emit()
            if self._presentation_state != 'error':self._complete_presentation()
        elif self._pending_widgets:
            self._presentation_state = 'presenting' if self.isVisible() else 'paused'
            self._update_presentation_line()
            if self.isVisible() and not self._presentation_timer.isActive():self._presentation_timer.start()
        else:self._complete_presentation()

    def _update_presentation_line(self):
        status = self.presentation_status()
        completed, expected = len(status['completed_link_keys']), len(status['expected_link_keys'])
        sections = f"campos {len(status['completed_sections'])}/{len(status['expected_sections'])}"
        if status['pending_core_rows']:sections += f" · novos campos restantes {status['pending_core_rows']}"
        text = (f"Tick {status['core_tick'] if status['core_tick'] is not None else '—'} · {sections}"
                f" · {completed}/{expected} conexões\n")
        if self._pending_widgets:
            text += ('Atualização pendente' + (f' · anterior tick {self._previous_tick}'
                                             if self._previous_tick is not None else ' · preparando campos'))
        else:text += 'Observações recebidas · ' + status['source_kind']
        if status['error']:
            text += ' · ' + status['error']
        if self.presentation_line.text() != text:
            self.presentation_line.setText(text)
        total_items = len(status['expected_sections']) + expected
        completed_items = len(status['completed_sections']) + completed
        total_items = max(1, total_items)
        if self._loading_generation != status['generation']:
            self._loading_generation = status['generation']
            self.loading.start(text, total=total_items, completed=completed_items)
        if status['error']:
            self.loading.abort(text)
        elif status['ready']:
            self.loading.complete(text)
        elif not self._closed_presentation:
            self.loading.set_progress(completed_items, total_items, text)
        self.presentation_line.setToolTip(status['source_label'])
        # Keep this slot reserved across ticks. Showing/hiding it on each normal
        # publication resized the scroll viewport and every table beneath it.
        self.presentation_line.setVisible(not self._closed_presentation)

    # --------------------------------------------------------------- content
    def _show_highlight(self, key: str) -> None:
        for name, row in self._legend.items():
            row.set_on(bool(key) and name == key)
            row.swatch.set_active(not key or name == key)

    def _hover(self, key: str) -> None:
        if key in self._protocol_link_keys:return
        self.chart.set_highlight(key or self._pinned)

    def _pin(self, key: str) -> None:
        if key in self._protocol_link_keys:
            self.select.emit('link', key)
            return
        self._pinned = "" if self._pinned == key else key
        self.chart.set_highlight(self._pinned)

    def _satellite_details(self,session_id):
        from .satellite_telemetry import SatelliteTelemetryDialog
        if self._satellite_dialog is None:
            self._satellite_dialog=SatelliteTelemetryDialog(self)
            self._satellite_dialog.command.connect(self.command.emit)
        bridge=self._satellite_session(session_id)
        self._satellite_dialog.set_metrics(bridge.get('metrics',{}),session_id=session_id,
            clock_s=self._satellite_state.get('clock_s'), satellite_id=self.node_id,
            mission_ingress=self._satellite_state.get('satellite_mission_ingress'),
            **self._satellite_permissions)
        self._satellite_dialog.show();self._satellite_dialog.raise_()

    def _satellite_session(self,session_id):
        candidates=(*self._satellite_state.get('bridges',[]), *self._satellite_state.get('isl_links',[]))
        return next((item for item in candidates if item.get('session_id')==session_id and item.get('metrics')),
                    next((item for item in candidates if item.get('session_id')==session_id),{}))

    @staticmethod
    def _field_metadata(node, live_node, source, links) -> dict:
        """Format received node fields without building any inspection widgets."""
        # -- every LiveNode field
        role = live_node.role if live_node is not None else node.role
        kind = (live_node.kind if live_node is not None else node.kind) or "—"
        status_text = live_node.status if live_node is not None else f"{node.health} (no engine status)"
        located = live_node.located if live_node is not None else node.located
        latitude = live_node.latitude if live_node is not None else node.latitude
        longitude = live_node.longitude if live_node is not None else node.longitude
        place = (live_node.place if live_node is not None else node.place) or ""
        address = live_node.address if live_node is not None else getattr(source, "connection_address", None)
        port = live_node.port if live_node is not None else getattr(source, "connection_port", None)
        incident = live_node.links if live_node is not None else tuple(link.key for link in links)
        fields = {
            "id": node.id, "role": role, "kind": kind, "status": status_text,
            "located": "located" if located else "no location",
            "latitude": format_lat(latitude) if located else "no location",
            "longitude": format_lon(longitude) if located else "no location",
            "place": place or ("no place label" if located else "no location"),
            "links": ", ".join(incident) or "none",
            "address": dash(address) if address else "not supplied",
            "port": dash(port) if port is not None else "not supplied",
        }
        if live_node is not None:
            pairs = live_node.pairs
            calibration = live_node.calibration
            fields.update({
                "memory_state": dash(live_node.memory_state),
                "pairs": (f"{len(pairs)} stored · F " + ", ".join(f"{value:.3f}" for value in pairs)) if pairs
                else "none stored",
                "temperature_c": number(live_node.temperature_c, ".2f", "°C"),
                "calibration_count": str(live_node.calibration_count),
                "routes_through": ", ".join(live_node.routes_through) or "none",
                "calibration": "none recorded" if calibration is None else
                f"{calibration.calibration_id} · {calibration.status} · {calibration.phase}",
            })
        else:
            missing = "not supplied by this source"
            fields.update({"memory_state": missing, "pairs": missing, "temperature_c": missing,
                           "calibration_count": str(getattr(source, "calibration_count", 0)),
                           "routes_through": missing, "calibration": missing})
        return fields

    def refresh(self, snap, net: NetModel, status: CommandStatus) -> bool:
        """Present a received snapshot; optionally build new link widgets over turns.

        Scientific inputs are unchanged. Visual changes are coalesced and presented
        in small turns; readiness attests the actual complete current publication.
        """

        node = net.node(self.node_id)
        if node is None:
            self.cancel_presentation('removed')
            return False
        if self._closed_presentation:
            return False
        if self.incremental and not self.isVisible():
            # Hidden windows keep one received photograph, without touching
            # labels, charts, tables or layout. showEvent presents it once.
            self._generation += 1
            self._latest_context = (snap, net, status)
            self._deferred_refresh = True
            self._rendered_generation = None
            self._presentation_snapshot = None
            self._presentation_state = 'paused'
            self._presentation_timer.stop()
            return True
        origin = self._origin(snap)
        source_changed = self._source_context is not None and origin != self._source_context
        self._source_context = origin
        previous = self._presentation_snapshot
        if previous is not None and previous[0].live is not None:
            self._previous_tick = previous[0].live.tick
        self._generation += 1
        self._presentation_error = ''
        self._presentation_state = 'presenting'
        self._rendered_generation = None
        self._latest_context = (snap, net, status)
        self._presentation_snapshot = None
        self._section_rows = {}
        live = snap.live
        live_node = live.node(node.id) if live is not None else None
        source = next((item for item in snap.topology.nodes if item.id == node.id), None)
        self._isolated = node.isolated
        satellite=node.physical_type=='satellite'
        type_changed = self._satellite_mode is not None and satellite != self._satellite_mode
        discard = source_changed or type_changed
        if discard:
            self._presentation_timer.stop()
            self._pending_widgets.clear()
            self._section_generations.clear()
            self._presented_rows.clear()
            self._previous_tick = None
            self.satellite_panel.invalidate_presentation()
            self.chart.set_series((), None)
        if satellite != self._satellite_mode:
            self._satellite_mode = satellite
            self.satellite_panel.setVisible(satellite)
            for widget in (self.wave_label,self.chart,self.legend_host,self.protocol_label,self.protocols,self.identity_host,
                           self.calibration_label,self.calibration_kv,self.calibration_links,self.routes_label,self.routes,self.unrouted,
                           self.connections_label,self.cards_host):
                widget.setVisible(not satellite)
            self.recalibrate.setVisible(not satellite);self.locate_button.setVisible(not satellite)
            if satellite:
                self.calibration_progress.hide()
                self.chart.set_series((), live.network_time_s if live is not None else None)
        if discard:
            # Changing mode can re-show terrestrial containers. Their old rows
            # must remain hidden until the new source's sections are presented.
            for key in ('identity', 'state', 'calibration', 'calibration_links', 'routes', 'protocols'):
                self._core_widget(key).hide()
            self.protocol_label.hide()
        links = net.links_of(node.id)
        self.protocol_series.set_state(live.technologies if live is not None else {}, node_id=node.id)
        self._fields = self._field_metadata(node, live_node, source, links)
        if satellite:
            capabilities = getattr(snap, 'capabilities', None)
            self._satellite_permissions = {
                'allowed': capabilities is not None and capabilities.allows('configure_technology_source'),
                'allowed_provision': capabilities is not None and capabilities.allows('provision_satellite_session'),
                'ingress': (getattr(live, 'technologies', {}) or {}).get('ogs_ingress', {}),
            }
            self._satellite_state=(net.satellites or {}).get(node.id,{})
            self.satellite_panel.set_state(self._satellite_state,(net.communicators or {}).get(node.id,{}),
                                           presentation=not self.incremental)
            if self._satellite_dialog is not None and self._satellite_dialog.isVisible():
                bridge=self._satellite_session(self._satellite_dialog._session_id)
                self._satellite_dialog.set_metrics(bridge.get('metrics',{}),session_id=bridge.get('session_id',''),
                    clock_s=self._satellite_state.get('clock_s'), satellite_id=self.node_id,
                    mission_ingress=self._satellite_state.get('satellite_mission_ingress'),
                    **self._satellite_permissions)
        self.marker.set_node(marker_kind(node), node.health, node.status == "calibrating")
        self.chips.set_chips(node_status_chips(node, live_node))
        self.isolate.setText("Restore" if node.isolated else "Isolate")
        if satellite:
            # Its housekeeping/science panel already exposes the registered downlinks,
            # bridges and ISLs. Building invisible fibre cards caused hundreds of extra
            # Qt rows and layouts on every first selection of a satellite.
            self.status.set_status(*status.for_subject("node", node.id))
            if not self.status.text() and status.subject[0] == "link" and any(
                    link.key == status.subject[1] for link in links):
                self.status.set_status(status.text, status.tone)
            self._link_context = {}
            self._configure_link_views((), discard=discard)
            sections = self.satellite_panel.presentation_keys()
            if not self.incremental:
                self._section_generations.update((key, self._generation) for key in sections)
            self._configure_sections('satellite', sections)
            self._schedule_presentation()
            return True

        protocol_rows, managed_links = node_protocol_observations(node.id, live, links)
        self._protocol_link_keys = frozenset(link.key for link in links if link.id in managed_links)
        if self._pinned in self._protocol_link_keys:
            self._pinned = ''; self.chart.set_highlight('')
        self._set_core_rows('protocols', protocol_rows)
        legacy_links = tuple(link for link in links if link.id not in managed_links)
        show_wave = bool(legacy_links) or not links
        self.wave_label.setVisible(show_wave)
        self.chart.setVisible(show_wave)
        self.legend_host.setVisible(show_wave)
        # Sample rate/window apply only to the legacy polarisation monitor.
        wave_hz = live.threshold("wave_rate_hz") if live is not None else None
        wave_s = live.threshold("wave_window_s") if live is not None else None
        caption = WAVE_CAPTION
        if wave_hz is not None and wave_s is not None:
            caption += f" · {wave_hz:g} Hz · last {wave_s:g} s"
            if self.chart.window_s != float(wave_s):
                self.chart.window_s = float(wave_s)
        if self.wave_label.text() != caption:
            self.wave_label.setText(caption)
        live_links = {link.id: link for link in live.links} if live is not None else {}
        # Protocol session metrics are not polarisation waves. Keep the link
        # cards and current states, but never synthesize a 20 Hz trace from them.
        series, keys = [], tuple(link.key for link in links)
        wave_series = []
        for link in links:
            other = link.b if link.a == node.id else link.a
            engine = live_links.get(link.id)
            if link.id in managed_links:
                samples, hz = (), 20.0
                note = 'métricas de protocolo na tabela da sessão'
                if engine is not None:note += ' · ' + engine.status.replace('_', ' ')
            elif engine is None:
                note, samples, hz = NO_WAVE_SOURCE, (), 20.0
            else:
                samples, hz = tuple(engine.wave), engine.wave_rate_hz
                note = ""
                if not samples:
                    note = "no trace · " + engine.status.replace("_", " ")
                    if engine.status_reasons:
                        note += " (" + "; ".join(engine.status_reasons) + ")"
            item = WaveSeries(link.key, f"{other} · {link.key}", samples, hz, note)
            series.append(item)
            if link.id not in managed_links:wave_series.append(item)
        self.chart.set_series(wave_series, live.network_time_s if live is not None else None)
        wave_indices = {item.key: index for index, item in enumerate(wave_series)}
        clock_s = live.technologies.get('clock_s') if live is not None else None
        self._link_context = {link.key: (wave_indices.get(link.key, 0), node.id, link, item, live_links.get(link.id), clock_s)
                              for item, link in zip(series, links)}
        self._configure_link_views(keys, discard=discard)

        fields = self._fields
        located = live_node.located if live_node is not None else node.located
        address = live_node.address if live_node is not None else getattr(source, "connection_address", None)
        port = live_node.port if live_node is not None else getattr(source, "connection_port", None)
        self._set_core_rows('identity', [
            ("Role", fields["role"]), ("Kind", fields["kind"]),
            ("Status", fields["status"]),
            ("Isolation", "isolated — fibres out of the routing graph" if node.isolated else "not isolated",
             "amber" if node.isolated else "ink"),
            ("Place", fields["place"], "ink" if located else "amber"),
            ("Latitude", fields["latitude"], "ink" if located else "dim"),
            ("Longitude", fields["longitude"], "ink" if located else "dim"),
            ("Address", fields["address"], "ink" if address else "dim"),
            ("Port", fields["port"], "ink" if port is not None else "dim"),
        ])
        operations=(live.technologies or {}).get('global_operations',{}) if live is not None else {}
        component=next((item for item in operations.get('components',()) if item.get('component')==node.id),{})
        operational_rows=([
            ('Operação',str(component.get('operation_state','not_configured')).replace('_',' ')),
            ('Demandas ativas',str(component.get('active_demands',0))),
            ('Operações recebidas',str(component.get('successful_local_or_classical_operations',0))),
            ('Impedimentos','; '.join(component.get('impediments',())) or 'nenhum registrado'),
        ] if component else [])
        bank_rows=[]
        occupied=0
        unknown_banks=0
        for ident,session in ((live.technologies or {}).get('sessions',{}).items() if live is not None else ()):
            config=session.get('config',{})
            if node.id not in config.get('path',()) or 'quantum_memory' not in config.get('technologies',()):
                continue
            metrics=session.get('metrics',{})
            occupancy=metrics.get('memory_qubit_occupancy')
            parameters=config.get('parameters',{})
            capacity=parameters.get('device_parameters',{}).get(node.id,{}).get('memory_capacity',parameters.get('memory_capacity'))
            current=occupancy.get(node.id,0) if occupancy is not None else None
            if current is not None:occupied+=current
            else:unknown_banks+=1
            bank_rows.append((ident,('aguardando aquisição' if current is None else f'{current} qubits ocupados')+
                (f' / {capacity} modos' if capacity is not None else '')+f" · {session.get('status','configured')}"))
        memory_rows=([
            ('Memória de protocolos',f'{occupied} qubits ocupados observados · {len(bank_rows)} bancos por sessão'+
                (f' · {unknown_banks} aguardando aquisição' if unknown_banks else '')),
            ('Bancos de memória','; '.join(f'{ident}: {value}' for ident,value in bank_rows)),
        ] if bank_rows else [])
        self._set_core_rows('state', [
            ("Memory state · legacy inventory", fields["memory_state"]),
            ("Stored pairs · legacy inventory", fields["pairs"]),
            ("Temperature", fields["temperature_c"]),
            ("Calibrations", fields["calibration_count"]),
            ("Routes through", fields["routes_through"]),
            ("Fibres", fields["links"]),
            ("Location", fields["located"], "ink" if located else "amber"),
        ]+memory_rows+operational_rows)

        # -- calibration: running progress, then the last report (before / after per fibre)
        calibration = live_node.calibration if live_node is not None else None
        running = calibration is not None and calibration.status == "running"
        self.calibration_progress.setVisible(running and not satellite)
        if running:
            self.calibration_phase.setText(
                f"CALIBRATING · phase {calibration.phase}"
                + ("" if calibration.progress is None else f" · {calibration.progress * 100:.0f} %"))
            self.calibration_track.set_fraction(calibration.progress or 0.0)
        if calibration is None:
            self._set_core_rows('calibration', [("Last calibration", fields["calibration"], "dim")])
            self._set_core_rows('calibration_links', [])
        else:
            self._set_core_rows('calibration', [
                ("Calibration", calibration.calibration_id),
                ("Result", f"{calibration.status} · phase {calibration.phase}",
                 {"complete": "green", "failed": "red", "running": "violet"}.get(calibration.status, "ink")),
                ("Completed", clock(calibration.completed_at) if calibration.completed_at else "—"),
                ("Duration", number(calibration.duration_s, ".0f", "s network time")),
                ("Memory reset", "—" if calibration.memory_reset is None else
                 "yes" if calibration.memory_reset else "no"),
                ("Report", (calibration.report_sha256 or "—")[:16] + ("…" if calibration.report_sha256 else "")),
            ])
            rows = []
            for item in calibration.links:
                rows.append((f"{item.link_id} · QBER", f"{percent(item.qber_before)} → {percent(item.qber_after)}"))
                rows.append((f"{item.link_id} · sync", f"{number(item.sync_residual_ps_before, '.2f')} → "
                                                       f"{number(item.sync_residual_ps_after, '.2f', 'ps')}"))
                rows.append((f"{item.link_id} · after",
                             f"{rate(item.key_rate_hz_after)} · {dash(item.status)}",
                             {"ok": "green", "degraded": "amber"}.get(item.status, "dim")))
            self._set_core_rows('calibration_links', rows)

        # -- demands through / at this node, with the backend's reason when unrouted
        demands = [demand for demand in (live.demands if live is not None else ())
                   if node.id in demand.route or node.id in (demand.a, demand.b)]
        self._set_core_rows('routes', [
            (f"{demand.id} · {demand.a} ↔ {demand.b}",
             f"{demand.status.replace('_', ' ')} · " + (" → ".join(demand.route) if demand.route else "no route"),
             DEMAND_STATUS_TONE.get(demand.status, "dim")) for demand in demands]
            or [("Demands", "none" if live is not None else "not supplied by this source", "dim")])
        blocked = [demand for demand in demands if demand.status in UNROUTED]
        self.unrouted.setText("\n".join(
            f"{demand.id} {demand.status.upper().replace('_', ' ')} — {demand_reason(demand)}" for demand in blocked))
        self.unrouted.setVisible(bool(blocked) and not satellite)
        self._configure_sections('core', self._section_rows)

        # Updating every existing fibre synchronously recreated the layout burst
        # on each tick. One queued item updates its legend and card from the latest
        # received context. Preserve queue order when ticks arrive before completion.
        queued = {task[1] for task in self._pending_widgets if task[0] in ('card', 'refresh')}
        for index, key in enumerate(keys):
            if self.incremental and key in self._legend and key in self._cards:
                if key not in queued:self._pending_widgets.append(('refresh', key, index))
            else:
                self._refresh_legend(key)
                self._refresh_card(key)
                if key in self._legend and key in self._cards:
                    self._link_generations[key] = self._generation
        self.status.set_status(*status.for_subject("node", node.id))
        if not self.status.text() and status.subject[0] == "link" and status.subject[1] in keys:
            self.status.set_status(status.text, status.tone)
        self._schedule_presentation()
        return True

    # ------------------------------------------------------------ inspection
    def field_texts(self) -> dict:
        """Text shown for every `LiveNode` field (keys = `NODE_FIELDS`)."""

        return dict(self._fields)

    def legend_labels(self) -> list:
        return [row.text() for row in self._legend.values()]

    def connection_texts(self) -> dict:
        return {key: card.texts() for key, card in self._cards.items()}

    def presentation_status(self) -> NodePresentationStatus:
        """Read-only progress; completion describes actual materialized widgets."""
        snap = self._latest_context[0] if self._latest_context is not None else None
        live = snap.live if snap is not None else None
        tick = live.tick if live is not None else None
        ready = (not self._closed_presentation and not self._pending_widgets
                 and self._rendered_generation == self._generation
                 and self._presentation_snapshot is not None)
        completed = tuple(key for key in self._expected_keys
                          if key in self._legend and key in self._cards
                          and self._link_generations.get(key) == self._generation)
        sections = tuple(key for key in self._expected_sections
                         if self._section_generations.get(key) == self._generation)
        ready = ready and completed == self._expected_keys and sections == self._expected_sections
        return {'ready': ready, 'state': self._presentation_state,
                'presentation_kind': 'satellite' if self._satellite_mode else 'ground',
                'generation': self._generation, 'rendered_generation': self._rendered_generation,
                'core_tick': tick, 'latest_tick': tick, 'presented_tick': tick if ready else None,
                'source_kind': str(live.source_kind if live is not None else
                                   getattr(snap, 'input_kind', 'unavailable')),
                'source_label': str(live.source_label if live is not None else 'not supplied'),
                'network_time_s': live.network_time_s if live is not None else None,
                'expected_link_keys': self._expected_keys, 'completed_link_keys': completed,
                'expected_sections': self._expected_sections, 'completed_sections': sections,
                'pending': int(self._presentation_timer.isActive()),
                'pending_items': len(self._pending_widgets), 'step_max_ms': self._step_max_ms,
                'pending_core_rows': sum(row[0] not in self._core_widget(key).rows
                                         for key, rows in self._section_rows.items() for row in rows),
                'error': self._presentation_error}

    def presentation_snapshot(self):
        """Exact received snapshot/model displayed when all current rows are ready."""
        return self._presentation_snapshot if self.presentation_status()['ready'] else None

    def content_texts(self) -> dict:
        """Read strings from rendered controls without constructing missing controls."""
        contents = {'id': self.node_id, 'title': self.title.text(),
                    'protocols': self.protocols.texts() if not self._satellite_mode else {},
                    'protocol_series': self.protocol_series.content_texts(),
                    'identity': self.identity.texts(), 'state': self.state.texts(),
                    'calibration': self.calibration_kv.texts(),
                    'calibration_links': self.calibration_links.texts(), 'routes': self.routes.texts(),
                    'legend': self.legend_labels(), 'cards': self.connection_texts(),
                    'status': self.status.text(),
                    'loading': self.presentation_line.text() if self._pending_widgets or self._presentation_error else ''}
        if self._satellite_mode:
            contents['satellite'], contents['stations'] = self.satellite_panel.content_texts()
        return contents


class NodeWindows(QObject):
    """The open node windows of one screen (a registry, not a widget)."""

    select = Signal(str, str)
    command = Signal(str, str)
    action = Signal(str, str, str, object)
    locate = Signal(str)
    changed = Signal()

    def __init__(self, host: QWidget, *, incremental: bool = False) -> None:
        super().__init__(host)
        self.host = host
        self.incremental = incremental
        self.windows: dict[str, NodeWindow] = {}
        self._order: list[str] = []
        self.overlay_active = None           # callables of the host: another Esc-dismissable state (map pick)
        self.overlay_cancel = None
        self._filtering = False

    def _filter(self, wanted: bool) -> None:
        """Watch application clicks only while a window is open (to raise the one clicked)."""

        app = QApplication.instance()
        if app is None or wanted == self._filtering:
            return
        self._filtering = wanted
        app.installEventFilter(self) if wanted else app.removeEventFilter(self)

    # The shell's Esc handling and older callers treat this object as "the node overlay".
    def isVisible(self) -> bool:  # noqa: N802 - the overlay protocol of the shell
        return bool(self.windows) or bool(self.overlay_active and self.overlay_active())

    def isHidden(self) -> bool:  # noqa: N802
        return not self.isVisible()

    def hide(self) -> None:
        """Esc: cancel the host's own overlay state first, else close the topmost window."""

        if self.overlay_active and self.overlay_active():
            self.overlay_cancel()
            return
        if self._order:
            self.close(self._order[-1])

    def eventFilter(self, watched, event) -> bool:
        if event.type() == QEvent.Type.MouseButtonPress and isinstance(watched, QWidget) and self.windows:
            for node_id, window in self.windows.items():
                if watched is window or window.isAncestorOf(watched):
                    self._raise(node_id)
                    break
        return False

    def _raise(self, node_id: str) -> None:
        window = self.windows.get(node_id)
        if window is None:
            return
        window.raise_()
        if node_id in self._order:
            self._order.remove(node_id)
        self._order.append(node_id)

    def open(self, node_id: str, anchor: QPoint | None = None, position: QPoint | None = None,
             *, prepare=None) -> NodeWindow:
        window = self.windows.get(node_id)
        if window is None:
            window = NodeWindow(node_id, self.host, incremental=self.incremental)
            window.select.connect(self.select.emit)
            window.command.connect(self.command.emit)
            window.action.connect(self.action.emit)
            window.locate.connect(self.locate.emit)
            window.closed.connect(self.close)
            window.materialized.connect(self.changed.emit)
            self.windows[node_id] = window
            self._filter(True)
            if prepare is not None:
                prepare(window)
            window.fit_host()
            if position is None:
                offset = 26 * (len(self.windows) - 1)
                centre_w = max(200, self.host.width() - 330)          # the column left of the inspector
                right = QPoint(centre_w - window.width() - 14 - offset, 10 + offset)
                left = QPoint(14 + offset, 10 + offset)
                position = left if anchor is not None and anchor.x() > centre_w / 2 else right
                fit_y = max(0, self.host.height() - window.height() - 6)      # the whole window on screen
                position = QPoint(position.x(), min(position.y(), fit_y))
            window.move_within(position)
            window.show()
        elif prepare is not None:
            prepare(window)
        self._raise(node_id)
        window.setFocus(Qt.FocusReason.OtherFocusReason)
        self.changed.emit()
        return window

    def close(self, node_id: str) -> None:
        window = self.windows.pop(node_id, None)
        if node_id in self._order:
            self._order.remove(node_id)
        if window is not None:
            window.cancel_presentation()
            window.chart.freeze(1.0)
            window.hide()
            # Keep ownership until deferred deletion. Reparenting a large panel to a
            # native top-level window re-polished its whole hidden widget tree.
            window.deleteLater()
            self._filter(bool(self.windows))
            self.changed.emit()

    def close_all(self) -> None:
        for node_id in list(self.windows):
            self.close(node_id)

    def refresh(self, snap, net: NetModel, status: CommandStatus) -> None:
        for node_id, window in list(self.windows.items()):
            if not window.refresh(snap, net, status):
                self.close(node_id)

    def fit(self) -> None:
        for window in self.windows.values():
            window.fit_host()

    def state(self) -> list:
        return [{"id": node_id, "x": self.windows[node_id].x(), "y": self.windows[node_id].y()}
                for node_id in self._order if node_id in self.windows]

    def restore(self, state) -> None:
        for item in state or ():
            if isinstance(item, Mapping) and item.get("id"):
                self.open(str(item["id"]), position=QPoint(int(item.get("x", 14)), int(item.get("y", 10))))

    def shutdown(self) -> None:
        self.close_all()
        self._filter(False)


__all__ = ["NODE_FIELDS", "NO_WAVE_SOURCE", "NodePresentationStatus", "NodeWindow", "NodeWindows", "WINDOW_H", "WINDOW_W"]
