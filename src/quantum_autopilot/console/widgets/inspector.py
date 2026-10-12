"""The Topology inspector: the 330 px aside with Inspector · Events · Links · Pairs.

Every text here is read from the snapshot. The panels name operations (through
`Inspector.action`) but decide nothing: whether a fibre is disabled, a node is
isolated or a demand is rerouted is the backend's answer, shown when the next
snapshot carries it.
"""
from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QGridLayout, QHeaderView, QSizePolicy, QStackedWidget, QWidget

from ..contract import (
    DEMAND_STATUS_TONE, HEALTH_TONE, LINK_STATUS_TONE, NODE_STATUS_TONE, SEVERITY_TONE,
)
from ..geodata import geodesic_km
from ..processing_labels import link_processing_labels
from ..theme import C, button_font, faded, family, mono, text_tone, tone
from .network_views import NetModel, STYLE_TONE, format_km, format_lat, format_lon
from .prims import (
    Btn, Chip, Clickable, Frame, KVRow, L, Label, Prose, RowBtn, Seg, Track, clear_layout, hbox,
    scoped_style, scroll_panel, set_color, vbox,
)
from .records import RecordTable

ASIDE_W = 330
TABS = (("insp", "Inspector"), ("events", "Events"), ("links", "Links"), ("pairs", "Pairs"))
MAX_EVENT_CARDS = 40
UNROUTED = ("no_route", "blocked")
PAIRS_TEXT = (
    "End-to-end pairs are logical. The backend router plans each demand with episodes of the author's "
    "repeater-network environment on the effective graph; the route is the path of the delivered pair.")
PAIRS_NOTE = (
    "An episode heralds elementary pairs on every hop (real fibre lengths), swaps at the intermediate nodes "
    "and purifies when the threshold needs it. The routing plant uses one raw fidelity (0.90) for every heralded pair; the per-fibre estimate "
    "(1 − 3·QBER/2 of the QKD session) is shown beside the plan and does not enter it. A fibre leaves the "
    "graph only when it is disabled, an end is isolated or calibrating, or an attack alarm is held. "
    "Purify runs a second episode and applies the plant's DEJMPS closed form; a failed round loses both copies.")


# ----------------------------------------------------------------- helpers
def dash(value) -> str:
    return "—" if value in (None, "") else str(value)


def number(value, spec: str = ".4f", unit: str = "") -> str:
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value):
        return "—"
    return format(value, spec) + (f" {unit}" if unit else "")


def percent(value, digits: int = 2) -> str:
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value):
        return "—"
    return f"{value * 100:.{digits}f} %"


def rate(value) -> str:
    """Bit rate with an SI prefix (display formatting of the backend's number)."""

    if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value):
        return "—"
    for limit, suffix in ((1e9, "Gbit/s"), (1e6, "Mbit/s"), (1e3, "kbit/s")):
        if abs(value) >= limit:
            return f"{value / limit:.3f} {suffix}"
    return f"{value:.0f} bit/s"


def compact_rate(value) -> str:
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value):
        return "—"
    for limit, suffix in ((1e9, "G"), (1e6, "M"), (1e3, "k")):
        if abs(value) >= limit:
            return f"{value / limit:.2f}{suffix}"
    return f"{value:.0f}"


def clock(iso: str) -> str:
    """HH:MM:SS of an ISO-8601 timestamp (as the journal shows it)."""

    return iso[11:19] if isinstance(iso, str) and len(iso) >= 19 else dash(iso)


def node_coordinates(node) -> str:
    if node is None or not getattr(node, "located", False):
        return "—"
    return f"{format_lat(node.latitude)} · {format_lon(node.longitude)}"


def table_header_style() -> str:
    """`.tbl th`: mono 11 px label on a line rule (the application style gives headers the text font)."""

    return (f"QHeaderView::section{{background:{C.raised};color:{C.ink3};border:0;"
            f"border-bottom:1px solid {C.line};padding:6px 4px;font-family:'{family('mono')}';font-size:11px;}}")


class PlainBtn(Clickable):
    """`.btn` look for view-only actions (navigation, opening a window).

    Not a `Btn`: it names no backend operation, so capability gating never touches it.
    """

    def __init__(self, value: str, parent=None, *, height: int = 34, accent: bool = False) -> None:
        super().__init__(value, parent)
        self.setFont(button_font(11.5))
        self.setFixedHeight(height)
        fg, bd = (C.acc_t, C.acc) if accent else (C.ink, C.line)
        self.setStyleSheet(
            f"QPushButton{{color:{fg};border:1px solid {bd};background:transparent;padding:0 12px;}}"
            f"QPushButton:hover:enabled{{background:{C.hover};}}"
            f"QPushButton:pressed:enabled{{background:{C.sel};}}"
            f"QPushButton:disabled{{color:{faded(fg)};border-color:{faded(bd)};}}")


def op_button(value: str, operation: str, *, accent: bool = False, height: int = 34) -> Btn:
    """A backend operation button: gated by the source's declared capabilities."""

    button = Btn(value, variant="accent" if accent else "default", height=height)
    button.setProperty("operation", operation)
    from .button_help import button_description
    button.setToolTip(button_description(value, operation))
    return button


class KVList(QWidget):
    """`.kv` rows addressed by key; rows are reused while the keys stay the same."""

    def __init__(self, parent=None, *, size: float = 13, wrap: bool = False) -> None:
        super().__init__(parent)
        self.lay = vbox(self, (0, 0, 0, 0), 0)
        self.size = size
        self.wrap = wrap
        self.rows: dict[str, KVRow] = {}
        self._values: dict[str, tuple] = {}

    def set_rows(self, rows) -> None:
        rows = [(str(key), dash(value), tone_name) for key, value, tone_name in
                ((*row, "ink")[:3] for row in rows)]
        keys = [key for key, _value, _tone in rows]
        if keys != list(self.rows):
            previous = self.rows
            while self.lay.count():
                self.lay.takeAt(0)
            self.rows = {}
            for key, value, tone_name in rows:
                row = previous.pop(key, None)
                if row is None:
                    # Build in the final owner: late parenting re-polishes the
                    # wrapped labels and repeats their layout on every new row.
                    row = KVRow(key, value, tone_name=tone_name, size=self.size,
                                wrap=self.wrap, parent=self)
                elif self._values.get(key) != (value, str(text_tone(tone_name))):
                    row.set_value(value, tone_name)
                if row.value_label.toolTip() != value:
                    row.value_label.setToolTip(value)
                self.rows[key] = row
                self.lay.addWidget(row)
            for row in previous.values():
                row.hide()
                row.deleteLater()
            self._values = {key: (value, str(text_tone(tone_name))) for key, value, tone_name in rows}
            return
        for key, value, tone_name in rows:
            signature = (value, str(text_tone(tone_name)))
            if self._values.get(key) != signature:
                self.rows[key].set_value(value, tone_name)
                self.rows[key].value_label.setToolTip(value)
                self._values[key] = signature

    def value(self, key: str) -> str:
        return self.rows[key].value_label.full_text

    def texts(self) -> dict:
        return {key: row.value_label.full_text for key, row in self.rows.items()}


class ChipRow(QWidget):
    """A row of `.chip` tags, rebuilt only when the tags change."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.lay = hbox(self, (0, 0, 0, 0), 8)
        self._chips: tuple = ()
        self._widgets: list[Chip] = []

    def set_chips(self, chips) -> None:
        chips = tuple(chips)
        if chips == self._chips:
            return
        self._chips = chips
        if len(chips) == len(self._widgets):
            for chip, (value, tone_name, outline) in zip(self._widgets, chips):
                if chip.text() != value:
                    chip.setText(value)
                if (chip._tone, chip._outline) != (tone_name, outline):
                    chip.set_tone(tone_name, outline)
            return
        clear_layout(self.lay)
        self._widgets = []
        for value, tone_name, outline in chips:
            chip = Chip(value, tone_name, outline=outline)
            self._widgets.append(chip)
            self.lay.addWidget(chip)
        self.lay.addStretch(1)

    def texts(self) -> list:
        return [value for value, _tone, _outline in self._chips]


class StatusLine(QWidget):
    """Lifecycle of the last operation on the shown subject (PENDING, CONFIRMED, UNKNOWN + message)."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        lay = vbox(self, (0, 0, 0, 0), 0)
        self.label = Prose("", 12, C.ink3)
        lay.addWidget(self.label)
        self.hide()

    def set_status(self, value: str, tone_name: str = "dim") -> None:
        if self.label.text() != value:
            self.label.setText(value)
        set_color(self.label, text_tone(tone_name))
        self.setVisible(bool(value))

    def text(self) -> str:
        return self.label.text()


@dataclass(frozen=True)
class CommandStatus:
    """Lifecycle of the last operation this screen dispatched (read back from the backend)."""

    subject: tuple = (None, "")          # (kind, id)
    text: str = ""
    tone: str = "dim"
    pending: bool = False

    def for_subject(self, kind: str, ident: str) -> tuple:
        if self.text and self.subject == (kind, ident):
            return self.text, self.tone
        return "", "dim"


def link_status_chips(link, live_link) -> list:
    chips = []
    if live_link is not None:
        name = LINK_STATUS_TONE.get(live_link.status, "muted")
        chips.append((live_link.status.replace("_", " "), {"green": "ok", "amber": "warn", "red": "bad",
                                                             "violet": "sat"}.get(name, "mute"), name in
                      ("green", "amber", "red", "violet")))
        if live_link.configured_active is False and live_link.status != "disabled":
            chips.append(("disabled", "mute", False))
        if not live_link.effective:
            chips.append(("out of routing graph", "warn", False))
    elif link is not None:
        name = STYLE_TONE.get(link.style, "ink")
        chips.append((link.state_text, {"amber": "warn", "red": "bad", "violet": "sat", "dim": "mute"}.get(name, "ok"),
                      name in ("amber", "red", "violet")))
    return chips


def node_status_chips(node, live_node) -> list:
    chips = []
    if live_node is not None:
        name = NODE_STATUS_TONE.get(live_node.status, "dim")
        chips.append((live_node.status, {"green": "ok", "amber": "warn", "red": "bad", "violet": "sat"}.get(
            name, "mute"), name != "dim"))
    elif node is not None:
        name = HEALTH_TONE.get(node.health, "dim")
        chips.append((node.health, {"green": "ok", "amber": "warn", "red": "bad"}.get(name, "mute"), name != "dim"))
    if node is not None:
        chips.append((node.kind, "mute", False))
        if node.isolated and (live_node is None or live_node.status != "isolated"):
            chips.append(("isolated", "warn", False))
    return chips


def demand_reason(demand) -> str:
    """The backend's reason for a demand it could not route (never a UI guess)."""

    if demand.status not in UNROUTED:
        return ""
    return demand.reason or str((demand.detail or {}).get("reason") or "") or "the backend gave no reason"


# ----------------------------------------------------------------- panels
class _NodePanel(QWidget):
    def __init__(self, owner: "Inspector") -> None:
        super().__init__()
        self.owner = owner
        self.node_id = ""
        lay = vbox(self, (0, 0, 0, 0), 12)
        self.title = L("", mono(30), C.ink, elide=True)
        lay.addWidget(self.title)
        self.role = Prose("", 13.5, C.ink2)
        lay.addWidget(self.role)
        self.chips = ChipRow()
        lay.addWidget(self.chips)
        self.kv = KVList()
        lay.addWidget(self.kv)
        self.calibration = QWidget()
        cal = vbox(self.calibration, (0, 0, 0, 0), 4)
        self.calibration_label = Label("", C.violet_t)
        cal.addWidget(self.calibration_label)
        self.calibration_track = Track(0.0, "violet")
        cal.addWidget(self.calibration_track)
        lay.addWidget(self.calibration)
        self.links_label = Label("Links")
        lay.addWidget(self.links_label)
        self.links_host = QWidget()
        self.links_lay = vbox(self.links_host, (0, 0, 0, 0), 0)
        lay.addWidget(self.links_host)
        self._links: tuple = ()
        self._link_rows: dict = {}
        actions = QWidget()
        row = hbox(actions, (0, 6, 0, 0), 8)
        self.recalibrate = op_button("Recalibrate", "recalibrate_node", accent=True)
        self.isolate = op_button("Isolate", "set_node_isolation")
        row.addWidget(self.recalibrate)
        row.addWidget(self.isolate)
        row.addStretch(1)
        lay.addWidget(actions)
        more = QWidget()
        row2 = hbox(more, (0, 0, 0, 0), 8)
        self.locate = op_button("Set location…", "set_node_location")
        self.detail = PlainBtn("Node detail →")
        row2.addWidget(self.locate)
        row2.addWidget(self.detail)
        row2.addStretch(1)
        lay.addWidget(more)
        self.status = StatusLine()
        lay.addWidget(self.status)
        lay.addStretch(1)
        self.recalibrate.clicked.connect(lambda: owner.action.emit("recalibrate_node", "node", self.node_id, {}))
        self.isolate.clicked.connect(self._toggle_isolation)
        self.locate.clicked.connect(lambda: owner.locate.emit(self.node_id))
        self.detail.clicked.connect(lambda: owner.open_node.emit(self.node_id))
        self._isolated = False

    def _toggle_isolation(self) -> None:
        self.owner.action.emit("set_node_isolation", "node", self.node_id, {"isolated": not self._isolated})

    def show_node(self, snap, net: NetModel, node, status: CommandStatus) -> None:
        live = snap.live
        live_node = live.node(node.id) if live is not None else None
        self.node_id = node.id
        self._isolated = node.isolated
        self.title.setText(node.id)
        satellite=node.physical_type=='satellite'
        self.role.setText('Satélite · função lógica: '+node.network_role if satellite else node.role)
        self.recalibrate.setVisible(not satellite);self.locate.setVisible(not satellite)
        self.chips.set_chips(node_status_chips(node, live_node))
        source = next((item for item in snap.topology.nodes if item.id == node.id), None)
        address = getattr(source, "connection_address", None)
        port = getattr(source, "connection_port", None)
        rows = [("Location", node.place or "no place label" if node.located else "no location · in queue",
                 "ink" if node.located else "amber"),
                ("Coordinates", node_coordinates(node)),
                ("Address", f"{address}:{port}" if address and port else address or "not supplied",
                 "ink" if address else "dim")]
        if live_node is not None:
            rows += [("Routes through", ", ".join(live_node.routes_through) or "none"),
                     ("Calibrations", str(live_node.calibration_count))]
        else:
            rows.append(("Calibrations", str(getattr(source, "calibration_count", 0))))
        operations=(live.technologies or {}).get('global_operations',{}) if live is not None else {}
        component=next((item for item in operations.get('components',()) if item.get('component')==node.id),{})
        if component:
            rows += [('Operação',str(component.get('operation_state','not_configured')).replace('_',' ')),
                     ('Capacidades',', '.join(name for name,enabled in component.get('capabilities',{}).items() if enabled) or 'não configuradas'),
                     ('Demandas ativas',str(component.get('active_demands',0))),
                     ('Operações recebidas',str(component.get('successful_local_or_classical_operations',0))),
                     ('Impedimentos','; '.join(component.get('impediments',())) or 'nenhum registrado')]
        self.kv.set_rows(rows)
        calibration = live_node.calibration if live_node is not None else None
        running = calibration is not None and calibration.status == "running"
        self.calibration.setVisible(running)
        if running:
            self.calibration_label.setText(f"Calibrating · {calibration.phase}"
                                           + ("" if calibration.progress is None
                                              else f" · {calibration.progress * 100:.0f} %"))
            self.calibration_track.set_fraction(calibration.progress or 0.0)
        links = net.links_of(node.id)
        keys = tuple(link.key for link in links)
        if keys != self._links:
            self._links = keys
            clear_layout(self.links_lay)
            self._link_rows = {}
            for link in links:
                row = RowBtn()
                name = row.add(L(link.key, mono(12.5), C.ink, elide=True), 1)
                state = row.add(L("", mono(11), C.ink3))
                value = row.add(L("", mono(12.5), C.ink3))
                row.clicked.connect(lambda checked=False, key=link.key: self.owner.select.emit("link", key))
                self._link_rows[link.key] = (row, name, state, value)
                self.links_lay.addWidget(row)
            if not links:
                self.links_lay.addWidget(Prose("No link is configured for this node.", 12.5, C.ink3))
        for link in links:
            row, name, state, value = self._link_rows[link.key]
            other = link.b if link.a == node.id else link.a
            set_color(name, text_tone(STYLE_TONE.get(link.style, "ink")))
            state.setText(link.state_text.lower())
            value.setText(f"F {link.fidelity}")
            row.setToolTip(f"{link.key} · {node.id} ↔ {other} · {link.state_text} · {format_km(link.distance_km)}\n{link.processing_detail}")
        self.isolate.setText("Restore" if node.isolated else "Isolate")
        self.status.set_status(*status.for_subject("node", node.id))


class _LinkPanel(QWidget):
    def __init__(self, owner: "Inspector") -> None:
        super().__init__()
        self.owner = owner
        self.link_key = ""
        self._active: bool | None = None
        self._ends: tuple = ()
        lay = vbox(self, (0, 0, 0, 0), 12)
        self.title = L("", mono(26), C.ink, elide=True)
        lay.addWidget(self.title)
        self.role = Prose("", 13.5, C.ink2)
        lay.addWidget(self.role)
        self.chips = ChipRow()
        lay.addWidget(self.chips)
        self.kv = KVList()
        lay.addWidget(self.kv)
        self.reasons = Prose("", 12.5, C.ink2)
        lay.addWidget(self.reasons)
        self.ends_host = QWidget()
        self.ends_lay = hbox(self.ends_host, (0, 0, 0, 0), 8)
        lay.addWidget(self.ends_host)
        actions = QWidget()
        row = hbox(actions, (0, 0, 0, 0), 8)
        self.toggle = op_button("Disable fibre", "set_link_active", accent=True)
        self.telemetry = PlainBtn("Telemetry →")
        row.addWidget(self.toggle)
        row.addWidget(self.telemetry)
        row.addStretch(1)
        lay.addWidget(actions)
        self.status = StatusLine()
        lay.addWidget(self.status)
        lay.addStretch(1)
        self.toggle.clicked.connect(self._toggle)
        self.telemetry.clicked.connect(lambda: owner.navigate.emit("telemetry"))

    def _toggle(self) -> None:
        if self._active is not None:
            self.owner.action.emit("set_link_active", "link", self.link_key, {"active": not self._active})

    def show_link(self, snap, net: NetModel, link, status: CommandStatus) -> None:
        live = snap.live
        live_link = live.link(link.id) if live is not None and link.id else None
        self.link_key = link.key
        self._active = link.configured_active
        self.title.setText(link.key)
        isl=link.kind=='satellite_isl'
        satellite=link.kind in ('satellite','satellite_isl')
        self.role.setText(("Enlace óptico satélite–satélite" if isl else "Enlace satélite / OGS" if satellite else "Pair path" if link.style == "pair" else "Fibre") + f" · {link.a} ↔ {link.b}")
        self.chips.set_chips(link_status_chips(link, live_link))
        a, b = net.node(link.a), net.node(link.b)
        both = a is not None and b is not None and a.located and b.located
        straight = f"{geodesic_km(a.latitude, a.longitude, b.latitude, b.longitude):.3f} km" if both \
            else "— (an end has no location)"
        rows = [("Fidelity", link.fidelity), ("QBER", link.qber), ("Secret key rate", link.skr),
                ("Fibre length", number(link.distance_km, ".3f", "km")),
                ("Straight line on map", straight, "ink" if both else "dim"),
                ("Length source", "geodesic between the node locations" if both else "operator-configured",
                 "dim")]
        if satellite:
            metrics=live_link.provenance.get('protocol_metrics',{}) if live_link is not None else {}
            channels=live_link.provenance.get('channels',{}).get('configuration',{}) if live_link is not None else {}
            rows=[('Distância oblíqua',number(link.distance_km,'.3f','km')),
                  ('Origem da distância','Geometria orbital recebida' if metrics.get('housekeeping') or metrics.get('satellite_housekeeping') else 'Referência cadastrada; sem observação'),
                  ('Canal clássico',channels.get('classical',{}).get('channel_id','—')),
                  ('Payload quântico',channels.get('quantum',{}).get('channel_id','—')),
                  ('Associação',channels.get('association_id','—')),
                  ('Coincidências conjuntas',str(metrics.get('coincidences','—'))),
                  ('Fidelidade conjunta',number(metrics.get('fidelity'),'.4f')),
                  ('QBER conjunto',percent(metrics.get('qber'))),
                  ('Chaves certificadas',str(metrics.get('certified_secret_bits',0))),
                  ('Origem',metrics.get('observation_origin','sem observação'))]
            if isl:
                geometry=metrics.get('isl_geometry',{})
                rows += [('Visibilidade mútua',str(geometry.get('visible','sem observação'))),
                         ('Obstrução terrestre',str(geometry.get('earth_obstructed','sem observação'))),
                         ('Orçamento óptico',number(geometry.get('link_budget_db'),'.2f','dB')),
                         ('Tempo de voo',number(geometry.get('flight_time_s'),'.5f','s')),
                         ('Recursos quânticos',metrics.get('resource_scope','Detecções consumidas; sem repetição quântica a bordo'))]
        elif live_link is not None:
            detector = live_link.detector
            alarm = "ALARM" if detector.alarm else "no alarm" if detector.alarm is False else "—"
            # Before its first call the detector reports what it is doing (e.g. "session baseline
            # (34 of 60 periods)"): that is shown instead of two dashes.
            state = getattr(detector, "state", None)
            verdict = f"{detector.label} · {alarm}" if detector.label is not None else (state or "—")
            rows += [("Stability", number(live_link.stability, ".3f")),
                     ("Sync residual", number(live_link.sync_residual_ps, ".2f", "ps")),
                     ("Polarisation residual", number(live_link.polarisation_residual_mrad, ".1f", "mrad")),
                     ("Detector", verdict, "red" if detector.alarm else "ink" if detector.label is not None
                      else "dim"),
                     ("Input", live_link.input, "dim"),
                     ("Provenance", live_link.display_provenance, "dim")]
        else:
            source = next((item for item in snap.topology.links if item.id == link.id and item.a == link.a), None)
            rows.append(("Provenance", getattr(source, "provenance", "—"), "dim"))
        processing = link_processing_labels(live_link, getattr(live, "technologies", None))
        rows += [("Data origin", processing.data_source, "dim"),
                 ("Processing", processing.badge, "amber" if processing.fallback else "violet" if "MODEL" in processing.badge else "dim"),
                 ("Physics / protocol", processing.physics, "dim"),
                 ("Acting controllers", processing.controls, "dim")]
        self.kv.set_rows(rows)
        self.kv.rows["Processing"].setToolTip(processing.detail)
        reasons = "; ".join(live_link.status_reasons) if live_link is not None else ""
        self.reasons.setText(reasons)
        self.reasons.setVisible(bool(reasons))
        ends = (link.a, link.b)
        if ends != self._ends:
            self._ends = ends
            clear_layout(self.ends_lay)
            for name in ends:
                button = PlainBtn(name)
                button.clicked.connect(lambda checked=False, n=name: self.owner.select.emit("node", n))
                self.ends_lay.addWidget(button)
            self.ends_lay.addStretch(1)
        subject='enlace' if satellite else 'fibre'
        self.toggle.setText(('Enable ' if link.configured_active is False else 'Disable ')+subject)
        self.toggle.setProperty("ready", link.configured_active is not None and bool(link.id))
        self.status.set_status(*status.for_subject("link", link.key))


class _NetworkPanel(QWidget):
    """Nothing selected: what the source says about the topology as a whole."""

    def __init__(self) -> None:
        super().__init__()
        lay = vbox(self, (0, 0, 0, 0), 12)
        lay.addWidget(L("Network", mono(26), C.ink))
        self.hint = Prose("Select a node or a fibre on the map, in the matrix or in the Links tab.", 13.5, C.ink2)
        lay.addWidget(self.hint)
        self.kv = KVList()
        lay.addWidget(self.kv)
        self.caveat = Prose("No snapshot loaded.", 12.5, C.ink3)
        lay.addWidget(self.caveat)
        lay.addStretch(1)

    def show_network(self, snap) -> None:
        topology = snap.topology
        rows = [(metric.label.title(), f"{metric.value}{(' ' + metric.unit) if metric.unit else ''}",
                 metric.tone) for metric in topology.metrics]
        rows += [(item.key.title(), item.value, item.tone) for item in topology.footer]
        seen, unique = set(), []
        for row in rows:
            if row[0] not in seen:
                seen.add(row[0])
                unique.append(row)
        self.kv.set_rows(unique)
        self.caveat.setText(topology.caveat)


class _EventCard(Frame):
    def __init__(self, kind: str, subject: str, at: str, body: str, tone_name: str, on_subject=None) -> None:
        QWidget.__init__(self)
        scoped_style(self, f"background:{C.bg};border:1px solid {C.soft};border-left:3px solid {tone(tone_name)};")
        lay = vbox(self, (12, 10, 12, 10), 6)
        head = QWidget()
        row = hbox(head, (0, 0, 0, 0), 8)
        row.addWidget(L(kind, mono(12), text_tone(tone_name)))
        if on_subject is not None:
            link = Clickable(subject)
            link.setFont(mono(12))
            link.setStyleSheet(f"QPushButton{{color:{C.ink};border:0;background:transparent;"
                               f"text-decoration:underline;text-align:left;padding:0;}}"
                               f"QPushButton:hover{{color:{C.acc_t};}}")
            link.clicked.connect(on_subject)
            row.addWidget(link)
            self.subject_button = link
        else:
            row.addWidget(L(subject, mono(12), C.ink2, elide=True), 1)
            self.subject_button = None
        row.addStretch(1)
        row.addWidget(L(at, mono(12), C.ink3))
        lay.addWidget(head)
        self.body = Prose(body, 13, C.ink2)
        lay.addWidget(self.body)
        self.kind, self.subject = kind, subject


class _EventsPanel(QWidget):
    def __init__(self, owner: "Inspector") -> None:
        super().__init__()
        self.owner = owner
        self.lay = vbox(self, (0, 0, 0, 0), 10)
        self._key = None
        self._by_id: dict = {}
        self.cards: list = []
        self.attention = 0

    @staticmethod
    def snapshot_rows(snap) -> list:
        """Event metadata is independent of the visible Qt cards."""
        live = snap.live
        rows = []
        if live is not None:
            for event in live.events:
                rows.append((event.at, event.id, event.kind.upper().replace("_", " "), event.subject,
                             clock(event.at), event.message, SEVERITY_TONE.get(event.severity, "ink"),
                             event.severity))
            known = {(event.subject, event.message) for event in live.events}
        else:
            known = set()
        for index, event in enumerate(snap.topology.events):
            if (event.subject, event.body) in known:
                continue
            rows.append((f"~{event.at}", f"topology-{index}-{event.at}-{event.kind}",
                         event.kind.upper().replace("_", " "), event.subject, event.at, event.body, event.tone,
                         "warning" if event.tone == "red" else "info"))
        # newest first: engine events by timestamp, topology-store events by their clock
        rows.sort(key=lambda row: (clock(row[0]) if not row[0].startswith("~") else row[0][1:], row[0]),
                  reverse=True)
        return rows

    def show_events(self, snap, net: NetModel, *, rows=None) -> None:
        live = snap.live
        rows = self.snapshot_rows(snap) if rows is None else rows
        self.attention = sum(1 for row in rows if row[7] in ("warning", "critical"))
        rows = rows[:MAX_EVENT_CARDS]
        key = tuple(row[1] for row in rows)
        if key == self._key:
            return
        self._key = key
        # An event is immutable: its card is built once and kept while the event stays in the
        # window; a new event adds one card instead of rebuilding forty of them.
        while self.lay.count():
            item = self.lay.takeAt(0)
            widget = item.widget()
            if widget is not None and widget not in self._by_id.values():
                widget.hide()
                widget.deleteLater()
        for ident in set(self._by_id) - set(key):
            card = self._by_id.pop(ident)
            card.hide()
            card.deleteLater()
        self.cards = []
        node_ids = {node.id for node in net.nodes}
        demand_ids = {demand.id for demand in live.demands} if live is not None else set()
        for _at, ident, kind, subject, at, body, tone_name, _severity in rows:
            card = self._by_id.get(ident)
            if card is None:
                target = None
                if subject in node_ids:
                    target = lambda checked=False, s=subject: self.owner.select.emit("node", s)  # noqa: E731
                elif net.link(subject) is not None:
                    target = lambda checked=False, s=subject: self.owner.select.emit("link", s)  # noqa: E731
                elif subject in demand_ids:
                    target = lambda checked=False, s=subject: self.owner.show_demand(s)  # noqa: E731
                card = _EventCard(kind, subject, at, body, tone_name if tone_name != "ink" else "mute", target)
                self._by_id[ident] = card
            self.cards.append(card)
            self.lay.addWidget(card)
            card.show()
        if not rows:
            self.lay.addWidget(Prose("The source delivered no events.", 13, C.ink3))
        self.lay.addStretch(1)


class _PairRow(RowBtn):
    def __init__(self, demand_id: str) -> None:
        super().__init__(padding=(10, 8, 10, 8), gap=0)
        self.demand_id = demand_id
        column = QWidget()
        column.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        lay = vbox(column, (0, 0, 0, 0), 2)
        head = QWidget()
        row = hbox(head, (0, 0, 0, 0), 8)
        self.name = L("", mono(13), C.ink)
        self.state = L("", mono(11), C.ink3, Qt.AlignmentFlag.AlignRight)
        row.addWidget(self.name, 1)
        row.addWidget(self.state)
        lay.addWidget(head)
        self.route = L("", mono(11.5), C.ink3, elide=True)
        self.route.setMinimumWidth(0)
        self.route.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        lay.addWidget(self.route)
        self.reason = Prose("", 12, C.red_t)
        self.reason.hide()
        lay.addWidget(self.reason)
        self.lay.addWidget(column, 1)

    def show_demand(self, demand, selected: bool) -> None:
        self.name.setText(f"{demand.id} · {demand.a} ↔ {demand.b}")
        self.state.setText(demand.status.upper().replace("_", " "))
        set_color(self.state, text_tone(DEMAND_STATUS_TONE.get(demand.status, "dim")))
        route = " → ".join(demand.route) if demand.route else "no route"
        estimate = f"F {demand.fidelity:.3f}" if demand.fidelity is not None else "F —"
        self.route.setText(f"{route} · {estimate}")
        reason = demand_reason(demand)
        if self.reason.text() != reason:
            self.reason.setText(reason)
        self.reason.setVisible(bool(reason))
        self.set_on(selected)
        self.updateGeometry()


class _PairsPanel(QWidget):
    def __init__(self, owner: "Inspector") -> None:
        super().__init__()
        self.owner = owner
        self.demand_id = ""
        lay = vbox(self, (0, 0, 0, 0), 12)
        self.intro = Prose(PAIRS_TEXT, 12.5, C.ink2)
        lay.addWidget(self.intro)
        self.rows_host = QWidget()
        self.rows_lay = vbox(self.rows_host, (0, 0, 0, 0), 0)
        lay.addWidget(self.rows_host)
        self.rows: dict[str, _PairRow] = {}
        self.name = Label("", C.ink)
        lay.addWidget(self.name)
        self.blocked = Prose("", 12.5, C.red_t)
        lay.addWidget(self.blocked)
        self.kv = KVList(size=12.5)
        lay.addWidget(self.kv)
        self.pair_label = Label("Stored pair")
        lay.addWidget(self.pair_label)
        self.pair_kv = KVList(size=12.5)
        lay.addWidget(self.pair_kv)
        actions = QWidget()
        row = hbox(actions, (0, 0, 0, 0), 8)
        self.request = op_button("Request pair", "request_pair", accent=True)
        self.purify = op_button("Purify", "purify_pair")
        self.consume = op_button("Consume", "consume_pair")
        for button in (self.request, self.purify, self.consume):
            row.addWidget(button)
        row.addStretch(1)
        lay.addWidget(actions)
        self.status = StatusLine()
        lay.addWidget(self.status)
        self.decisions_label = Label("Routing decisions")
        lay.addWidget(self.decisions_label)
        self.decisions = Prose("", 12, C.ink2)
        lay.addWidget(self.decisions)
        self.note = Prose(PAIRS_NOTE, 11.5, C.ink3)
        lay.addWidget(self.note)
        lay.addStretch(1)
        for button, verb in ((self.request, "request_pair"), (self.purify, "purify_pair"),
                             (self.consume, "consume_pair")):
            button.clicked.connect(lambda checked=False, v=verb: owner.action.emit(v, "demand", self.demand_id, {}))

    def show_pairs(self, snap, demand_id: str, status: CommandStatus) -> None:
        live = snap.live
        demands = live.demands if live is not None else ()
        ids = [demand.id for demand in demands]
        if ids != list(self.rows):
            clear_layout(self.rows_lay)
            self.rows = {}
            for demand in demands:
                row = _PairRow(demand.id)
                row.clicked.connect(lambda checked=False, d=demand.id: self.owner.show_demand(d))
                self.rows[demand.id] = row
                self.rows_lay.addWidget(row)
            if not demands:
                self.rows_lay.addWidget(Prose(
                    "This source has no network engine: no end-to-end demand is planned." if live is None
                    else "No demand is defined. Define one from the terminal: define_pair <id> <a> <b>.",
                    12.5, C.ink3))
        for demand in demands:
            self.rows[demand.id].show_demand(demand, demand.id == demand_id)
        demand = live.demand(demand_id) if live is not None and demand_id else None
        self.demand_id = demand.id if demand is not None else ""
        for widget in (self.name, self.kv, self.pair_label, self.pair_kv, self.decisions_label, self.decisions,
                       self.request, self.purify, self.consume):
            widget.setVisible(demand is not None)
        self.note.setVisible(live is not None)
        if demand is None:
            self.blocked.hide()
            self.status.set_status("")
            for button in (self.request, self.purify, self.consume):
                button.setProperty("ready", False)
            return
        detail = demand.detail if isinstance(demand.detail, Mapping) else {}
        self.name.setText(f"{demand.id} · {demand.a} ↔ {demand.b}")
        reason = demand_reason(demand)
        self.blocked.setText(f"{demand.status.upper().replace('_', ' ')} — {reason}" if reason else "")
        self.blocked.setVisible(bool(reason))
        estimate = detail.get("link_estimate_fidelity") if isinstance(detail.get("link_estimate_fidelity"), Mapping) \
            else {}
        hops = estimate.get("links") if isinstance(estimate.get("links"), (list, tuple)) else ()
        rows = [("Status", demand.status.replace("_", " "), DEMAND_STATUS_TONE.get(demand.status, "dim")),
                ("Route", " → ".join(demand.route) or "none", "ink" if demand.route else "red")]
        for index, link_id in enumerate(demand.route_links):
            value = hops[index] if index < len(hops) else None
            rows.append((link_id, f"F {number(value, '.4f')} (fibre estimate)"))
        rows += [("Chain estimate (fibres)", number(estimate.get("fidelity"), ".4f")),
                 ("Plan · delivered fidelity", number(demand.fidelity, ".4f")),
                 ("Plan · success rate", percent(demand.success_rate, 0)
                  + (f" of {detail.get('episodes')} episodes" if detail.get("episodes") is not None else "")),
                 ("Fidelity threshold", number(demand.min_fidelity, ".2f")),
                 ("Deadline", number(detail.get("deadline_s", None) and detail["deadline_s"] * 1e3, ".0f", "ms")),
                 ("Policy", dash(demand.policy_id)),
                 ("Model state", dash(detail.get("model_state")),
                  {"active": "green", "fallback": "amber"}.get(detail.get("model_state"), "dim")),
                 ("In contract", "yes" if demand.in_contract else "no — outside the reviewed plant",
                  "ink" if demand.in_contract else "amber"),
                 ("Fallback used", "yes" if demand.fallback_used else "no"),
                 ("Trigger", dash(detail.get("trigger"))),
                 ("Changed at tick", dash(demand.changed_at_tick))]
        previous = detail.get("previous_route")
        if previous:
            rows.append(("Previous route", " → ".join(str(item) for item in previous)))
        self.kv.set_rows(rows)
        if not demand.in_contract and detail.get("contract_reasons"):
            self.kv.rows["In contract"].setToolTip("; ".join(str(item) for item in detail["contract_reasons"]))
        pair = demand.pair if isinstance(demand.pair, Mapping) else None
        if pair is None:
            self.pair_kv.set_rows([("Pair", "not requested", "dim")])
        else:
            purification = pair.get("last_purification") if isinstance(pair.get("last_purification"), Mapping) else {}
            self.pair_kv.set_rows([
                ("Pair", str(pair.get("status", "—")).replace("_", " "),
                 {"delivered": "green", "failed": "red", "purification_failed": "red",
                  "invalidated": "amber"}.get(pair.get("status"), "dim")),
                ("Historical F" if pair.get("status") == "invalidated" else "Current F",
                 number(pair.get("fidelity"), ".4f")),
                ("Swaps (BSM) at", ", ".join(str(item) for item in pair.get("swaps_at") or ()) or "none"),
                ("Hops", dash(pair.get("hops"))),
                ("Memory wait", number(pair.get("memory_wait_ms"), ".2f", "ms")),
                ("Latency", number(pair.get("latency_ms"), ".2f", "ms")),
                ("Purification rounds", dash(pair.get("rounds"))),
                ("Elementary copies used", dash(pair.get("copies_consumed"))),
                ("Last round success p", number(purification.get("success_probability"), ".2f")),
                ("Requested at tick", dash(pair.get("requested_at_tick"))),
            ])
            self.pair_kv.rows["Pair"].setToolTip(str(pair.get("invalidation_reason") or ""))
        delivered = pair is not None and pair.get("status") == "delivered"
        self.request.setProperty("ready", True)
        self.purify.setProperty("ready", delivered)
        self.consume.setProperty("ready", delivered)
        decisions = [item for item in live.decisions if item.subject == demand.id
                     and item.kind in ("reroute", "no_route")][-4:]
        self.decisions.setText("\n\n".join(
            f"tick {item.tick} · {item.kind.replace('_', ' ')} · {item.controller_id} ({item.model_state})"
            f"{'' if item.in_contract else ' · out of contract'}\n{item.summary} → {item.outcome}"
            for item in reversed(decisions)) or "No routing decision recorded for this demand yet.")
        self.status.set_status(*status.for_subject("demand", demand.id))


# -------------------------------------------------------------- the aside
class Inspector(Frame):
    select = Signal(str, str)                # kind, id
    open_node = Signal(str)
    locate = Signal(str)                     # node id: open the set-location dialog
    navigate = Signal(str)
    action = Signal(str, str, str, object)   # verb, subject kind, subject id, payload fields
    demand_selected = Signal(str)
    tab_changed = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(bg=C.raised, border=C.soft, sides="l", parent=parent)
        self.setFixedWidth(ASIDE_W)
        lay = vbox(self, (0, 0, 0, 0), 0)
        tabs = Frame(border=C.soft, sides="b")
        row = hbox(tabs, (14, 10, 10, 0), 10)        # four tabs with a count must fit 330 px
        self.tabs: dict[str, Seg] = {}
        for key, title in TABS:
            button = Seg(title)
            button.clicked.connect(lambda checked=False, k=key: self.set_tab(k))
            row.addWidget(button)
            self.tabs[key] = button
        row.addStretch(1)
        lay.addWidget(tabs)

        self.stack = QStackedWidget()
        self._node_panel = _NodePanel(self)
        self._link_panel = _LinkPanel(self)
        self.network_panel = _NetworkPanel()
        self.insp_stack = QStackedWidget()
        for panel in (self.network_panel, self._node_panel, self._link_panel):
            self.insp_stack.addWidget(panel)
        self._events_panel = _EventsPanel(self)
        self.links_panel = QWidget()
        links = vbox(self.links_panel, (0, 0, 0, 0), 8)
        self.links_actions = QWidget()
        actions = QGridLayout(self.links_actions)       # three buttons do not fit one 298 px row
        actions.setContentsMargins(0, 0, 0, 0)
        actions.setSpacing(6)
        self.edit_links = op_button("Edit links", "configure_simulated_link", height=30)
        self.run_path = op_button("Run path", "run_workspace_path", height=30)
        self.run_campaign = op_button("Temporal campaigns", "read_temporal_campaign_history", height=30)
        for button, (row, column, span) in ((self.edit_links, (0, 0, 1)), (self.run_path, (0, 1, 1)),
                                            (self.run_campaign, (1, 0, 2))):
            button.setFixedHeight(30)
            actions.addWidget(button, row, column, 1, span)
        links.addWidget(self.links_actions)
        self.links_up = Label("—")
        links.addWidget(self.links_up)
        self.link_table = RecordTable(["LINK", "F", "QBER", "SKR", "STATE", "PROCESSING / DATA"])
        self.link_table.setFont(mono(12))
        header = self.link_table.horizontalHeader()
        header.setSectionResizeMode(5, QHeaderView.ResizeMode.Fixed)
        self.link_table.setColumnWidth(5, 175)
        header.setStyleSheet(table_header_style())
        header.setMinimumSectionSize(40)
        for column in (1, 2, 3, 4):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.Fixed)
            self.link_table.setColumnWidth(column, 56)
        links.addWidget(self.link_table, 1)
        self._pairs_panel = _PairsPanel(self)
        self._pages = {}
        for key, widget, scrolls in (("insp", self.insp_stack, True), ("events", self._events_panel, True),
                                     ("links", self.links_panel, False), ("pairs", self._pairs_panel, True)):
            host = QWidget()
            inner = vbox(host, (16, 16, 16, 16), 0)
            inner.addWidget(widget)
            page = scroll_panel(host, 80) if scrolls else host
            self._pages[key] = page
            self.stack.addWidget(page)
        lay.addWidget(self.stack, 1)

        footer = Frame(border=C.soft, sides="t")
        foot = vbox(footer, (16, 10, 16, 10), 0)
        self.footer = KVList(size=12)
        foot.addWidget(self.footer)
        lay.addWidget(footer)
        self.tab = "insp"
        self._demand = ""
        self._latest = None
        self._revision = 0
        self._presented = {}
        self._event_rows = []
        self.set_tab("insp")

    @property
    def node_panel(self):
        self._present_page("insp")
        return self._node_panel

    @property
    def link_panel(self):
        self._present_page("insp")
        return self._link_panel

    @property
    def events_panel(self):
        self._present_page("events")
        return self._events_panel

    @property
    def pairs_panel(self):
        self._present_page("pairs")
        return self._pairs_panel

    def _present_page(self, key: str) -> None:
        """Present a page once per publication, on activation or explicit readback."""
        revision = (self._revision, self._demand if key == "pairs" else "")
        if self._latest is None or self._presented.get(key) == revision:
            return
        snap, net, status = self._latest
        if key == "insp":
            kind, ident = net.selection
            node = net.node(ident) if kind == "node" else None
            link = net.link(ident) if kind == "link" else None
            if node is not None:
                self._node_panel.show_node(snap, net, node, status)
                self.insp_stack.setCurrentWidget(self._node_panel)
            elif link is not None:
                self._link_panel.show_link(snap, net, link, status)
                self.insp_stack.setCurrentWidget(self._link_panel)
            else:
                self.network_panel.show_network(snap)
                self.insp_stack.setCurrentWidget(self.network_panel)
        elif key == "events":
            self._events_panel.show_events(snap, net, rows=self._event_rows)
        elif key == "pairs":
            self._pairs_panel.show_pairs(snap, self._demand, status)
        self._presented[key] = revision

    # ------------------------------------------------------------------ tabs
    def set_tab(self, key: str) -> None:
        if key not in self.tabs:
            return
        self.tab = key
        for name, button in self.tabs.items():
            button.set_active(name == key)
        self.stack.setCurrentWidget(self._pages[key])
        self._present_page(key)
        self._paint_alert()
        self.tab_changed.emit(key)

    def show_demand(self, demand_id: str) -> None:
        self._demand = demand_id
        self.set_tab("pairs")
        self.demand_selected.emit(demand_id)

    def _paint_alert(self) -> None:
        """The Pairs tab turns red while the backend reports an unrouted demand."""

        button = self.tabs["pairs"]
        alert = bool(getattr(self, "_alert", False))
        signature = (alert, button.active, str(C.red_t), str(C.acc), str(C.ink3))
        if signature == getattr(self, "_alert_style_key", None):
            return
        self._alert_style_key = signature
        if alert:
            active = button.active
            button.setStyleSheet(
                f"QPushButton{{color:{C.red_t};background:transparent;border:0;"
                f"border-bottom:2px solid {C.acc if active else 'transparent'};padding:6px 2px;}}")
        else:
            active = button.active
            button._active = None
            button.set_active(active)

    # --------------------------------------------------------------- refresh
    def refresh(self, snap, net: NetModel, demand_id: str, status: CommandStatus) -> None:
        self._latest = (snap, net, status)
        self._revision += 1
        self._demand = demand_id
        self._event_rows = self._events_panel.snapshot_rows(snap)
        self._events_panel.attention = sum(1 for row in self._event_rows if row[7] in ("warning", "critical"))
        self._present_page(self.tab)
        title = f"Events {self._events_panel.attention}" if self._events_panel.attention else "Events"
        if self.tabs["events"].text() != title:
            self.tabs["events"].setText(title)
            self.tabs["events"].setToolTip("Events of severity warning or critical among those delivered")
        live = snap.live
        unrouted = [demand for demand in live.demands if demand.status in UNROUTED] if live is not None else []
        alert = bool(unrouted)
        title = "Pairs ●" if alert else "Pairs"
        if self.tabs["pairs"].text() != title or alert != getattr(self, "_alert", None):
            self._alert = alert
            self.tabs["pairs"].setText(title)
            self.tabs["pairs"].setToolTip(
                "; ".join(f"{demand.id} {demand.status.replace('_', ' ')}" for demand in unrouted))
            self._paint_alert()
        topology = snap.topology
        version = "—" if topology.version is None else f"v{topology.version}"
        if live is not None:
            source = f"{snap.input_kind} · {live.source_kind} input"
            tick = f"{live.tick} · {live.network_time_s:.0f} s network time"
        else:
            source, tick = snap.input_kind, "— (no network engine)"
        self.footer.set_rows([("Data source", source), ("Topology", f"{dash(topology.id)} · {version}"),
                              ("Live tick", tick, "violet" if live is not None else "dim")])
        if live is not None:
            self.footer.rows["Data source"].setToolTip(live.source_label)


__all__ = ["ASIDE_W", "ChipRow", "CommandStatus", "Inspector", "KVList", "PAIRS_NOTE", "PAIRS_TEXT", "PlainBtn",
           "StatusLine", "UNROUTED", "clock", "compact_rate", "dash", "demand_reason", "link_status_chips",
           "node_coordinates", "node_status_chips", "number", "op_button", "percent", "rate"]
