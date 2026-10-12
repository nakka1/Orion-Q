"""Topology — offline geographic network, matrix and functional logical graph.

Three views of ONE model (`widgets.network_views.build_net_model`), built from the
snapshot on every refresh: GEO (default) draws the nodes at their declared
coordinates, to scale; MATRIX shows the same nodes and the direct fibre of each
pair; GRAPH keeps the logical layout. A node without location waits in the queue
under the view with its connections. Selection is shared with the shell, so the
palette, the terminal and the three views always show the same element.

The screen names operations and reads results. A click on Isolate, Recalibrate,
Disable fibre, Set location or Request pair dispatches the backend verb and shows
PENDING; the state on screen changes only when the next snapshot carries the
backend's answer (the command lifecycle in `snap.ledger.commands`, the node and
fibre states, the re-planned demands, the engine's events) or its refusal.
"""
from __future__ import annotations

from ..widgets.ml_control import MLControlPanel
from ..widgets.technology_panel import TechnologyPanel

import json
import statistics
import time
from dataclasses import dataclass, field

from PySide6.QtCore import QEvent, QPoint, QPointF, QRect, QSize, Qt, QTimer
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import QLayout, QStackedWidget, QWidget

from ..contract import Snapshot
from ..theme import C, mono, text as text_font, text_tone
from ..widgets import Btn, Chip, Frame, L, Label, Seg, TopologyGraph, clear_layout, hbox, vbox, vrule
from ..widgets.campaign_runner import CampaignRunner
from ..widgets.figures import Sparkline, link_pen, paint_route, paint_marker
from ..widgets.geo_map import GeoMap
from ..widgets.inspector import (
    CommandStatus, Inspector, UNROUTED, compact_rate, demand_reason, number, percent, rate,
)
from ..widgets.link_editor import LinkEditor
from ..widgets.location_dialog import LocationDialog
from ..widgets.network_views import MatrixView, NetModel, STYLE_TONE, build_net_model
from ..widgets.node_window import NodeWindows
from ..widgets.path_runner import PathRunner
from ..widgets.unplaced_queue import UnplacedQueue
from .base import Screen

VIEWS = (("GEO", "Geo"), ("MATRIX", "Matrix"), ("GRAPH", "Graph"))        # GEO first: it is the main view
LAYERS = (("physical", "Physical"), ("entanglement", "Entanglement"))
#: Verbs tracked in the command journal (REQUESTED → RUNNING → CONFIRMED | UNKNOWN).
TRACKED_VERBS = ("set_node_isolation", "recalibrate_node", "set_link_active", "set_node_location")
PAIR_VERBS = ("request_pair", "purify_pair", "consume_pair")
PENDING_READBACK_TIMEOUT_S = 30.0  # Wall time without command evidence or calibration progress.
#: (short text shown beside the legend, full sentence in its tooltip)
VIEW_NOTES = {
    "GEO": ("Configured connections · reference map",
            "Campus or operator-declared reference positions. Lines show configured associations and received transmission blocks. "
            "Terrestrial reference lengths use WGS84 geodesics; satellite slant ranges use received orbital geometry. "
            "The display is approximate at continental scale. Actual fibre routes have not been surveyed."),
    "MATRIX": ("Direct fibres only",
               "The same nodes and fibres as the map. A cell is the direct fibre between two nodes: live fidelity "
               "estimate, length and status. MULTI = parallel fibres; · = no direct fibre. No swapped or "
               "multi-hop fidelity is inferred."),
    "GRAPH": ("Logical geometry", "Graph geometry is logical, not geographic."),
}
LEGEND = (("calm", "fibre", "ink"), ("warn", "degraded", "amber"), ("bad", "fault", "red"),
          ("compromised", "compromised", "red"), ("cal", "calibrating", "violet"),
          ("off", "isolated · disabled", "dim"), ("route", "pair route", "violet"))
NODE_LEGEND = (("endpoint", "Endpoint", "Entrada e saída da informação; símbolo de maior destaque."),
               ("core", "Core", "Hub central da rede; classificação publicada pelo backend."),
               ("node", "Node", "Instalação ou dispositivo intermediário; inspecione suas capacidades."),
               ("repeater", "Repetidor", "Componente intermediário. Marca discreta, com área de clique ampliada."),
               ("satellite", "Satélite", "Carga útil óptica orbital que atende estações terrestres nas suas janelas operacionais."))
SPACE_LEGEND = (("space_config", "Satélite · cadastrado", "dim"),
                ("space", "Satélite · emissão recebida", "violet"), ("orbit", "Órbita", "violet"))
LEGEND_TIPS = {"route": "End-to-end pair route of the selected demand (entanglement layer)",
               "compromised": "Attack alarm held by the backend detector: the fibre is out of the routing graph",
               "off": "Fibre disabled by the operator, or an end isolated: out of the routing graph",
               "cal": "An end is being recalibrated: the fibre is out of the routing graph meanwhile"}
LEGEND_TIPS.update(space_config="Associação cadastrada, sem emissão recente comprovada. Não indica comunicação ativa.",
                   space="Emissão óptica informada pelo último bloco recebido; origem e idade na inspeção. Não é estado físico instantâneo.",
                   orbit="Trajetória orbital tracejada; não representa um canal de comunicação.")


class _StripLayout(QLayout):
    """The prototype's wrapping header row: groups flow onto a new line when they do not fit.

    The first group of a line sits left; the others are packed against the right edge.
    """

    def __init__(self, parent=None, margins=(18, 10, 18, 10), gap: int = 14, line_gap: int = 8) -> None:
        super().__init__(parent)
        self._items: list = []
        self._gap, self._line_gap = gap, line_gap
        self.setContentsMargins(*margins)

    def addItem(self, item) -> None:  # noqa: N802 - Qt naming
        self._items.append(item)

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, index: int):  # noqa: N802
        return self._items[index] if 0 <= index < len(self._items) else None

    def takeAt(self, index: int):  # noqa: N802
        return self._items.pop(index) if 0 <= index < len(self._items) else None

    def set_gap(self, gap: int) -> None:
        if gap != self._gap:
            self._gap = gap
            self.invalidate()

    def needed_width(self, skip=()) -> int:
        """Width at which the visible groups (except `skip`) share one line."""

        margins = self.contentsMargins()
        items = [item for item in self._visible() if item.widget() not in skip]
        return (sum(item.sizeHint().width() for item in items) + self._gap * max(0, len(items) - 1)
                + margins.left() + margins.right())

    def line_count(self, width: int) -> int:
        return self._lines(max(0, width - self.contentsMargins().left() - self.contentsMargins().right()))

    def hasHeightForWidth(self) -> bool:  # noqa: N802
        return True

    def heightForWidth(self, width: int) -> int:  # noqa: N802
        return self._arrange(QRect(0, 0, width, 0), apply=False)

    def sizeHint(self) -> QSize:  # noqa: N802
        return self.minimumSize()

    def minimumSize(self) -> QSize:  # noqa: N802
        margins = self.contentsMargins()
        widest = max((item.minimumSize().width() for item in self._visible()), default=0)
        tallest = max((item.sizeHint().height() for item in self._visible()), default=0)
        return QSize(widest + margins.left() + margins.right(), tallest + margins.top() + margins.bottom())

    def setGeometry(self, rect: QRect) -> None:  # noqa: N802
        super().setGeometry(rect)
        self._arrange(rect, apply=True)

    def _visible(self) -> list:
        return [item for item in self._items if item.widget() is None or not item.widget().isHidden()]

    def _lines(self, available: int) -> int:
        count, used = 0, 0
        for item in self._visible():
            width = item.sizeHint().width()
            if count == 0 or used + self._gap + width > available:
                count, used = count + 1, width
            else:
                used += self._gap + width
        return count

    def _arrange(self, rect: QRect, apply: bool) -> int:
        margins = self.contentsMargins()
        left, right = rect.left() + margins.left(), rect.right() - margins.right() + 1
        available = max(0, right - left)
        lines, current, used = [], [], 0
        for item in self._visible():
            width = item.sizeHint().width()
            if current and used + self._gap + width > available:
                lines.append(current)
                current, used = [], 0
            used += (self._gap if current else 0) + width
            current.append(item)
        if current:
            lines.append(current)
        y = rect.top() + margins.top()
        for line in lines:
            height = max(item.sizeHint().height() for item in line)
            if apply:
                first = line[0]
                width = min(first.sizeHint().width(), available)
                first.setGeometry(QRect(left, y + (height - first.sizeHint().height()) // 2, width,
                                        first.sizeHint().height()))
                x = right
                for item in reversed(line[1:]):
                    hint = item.sizeHint()
                    x -= hint.width()
                    item.setGeometry(QRect(x, y + (height - hint.height()) // 2, hint.width(), hint.height()))
                    x -= self._gap
            y += height + self._line_gap
        return y - (self._line_gap if lines else 0) + margins.bottom() - rect.top()


class _LegendSample(QWidget):
    def __init__(self, style: str, parent=None) -> None:
        super().__init__(parent)
        self.style = style
        self.setFixedSize(34, 32 if style.startswith('node:') else 12)

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        a, b = QPointF(1, self.height() / 2), QPointF(self.width() - 1, self.height() / 2)
        if self.style.startswith('node:'):
            paint_marker(p, QPointF(self.width()/2,self.height()/2), self.style.split(':',1)[1], 'unknown')
        elif self.style == "route":
            paint_route(p, [a, b])
        else:
            p.setPen(link_pen(self.style))
            p.drawLine(a, b)
        p.end()


class _StripCell(Frame):
    """One cell of the three-cell strip: label · scope, large value, optional unit and trend."""

    def __init__(self, title: str, *, border: bool = True, sparkline: bool = False) -> None:
        super().__init__(border=C.soft if border else None, sides="r")
        self.title = title
        lay = hbox(self, (18, 12, 18, 12), 16)
        column = QWidget()
        col = vbox(column, (0, 0, 0, 0), 0)
        self.label = Label(title)
        col.addWidget(self.label)
        row = QWidget()
        value_row = hbox(row, (0, 0, 0, 0), 6)
        self.value = L("—", text_font(26), C.ink)
        self.unit = L("", mono(13), C.ink3)
        value_row.addWidget(self.value)
        value_row.addWidget(self.unit, 0, Qt.AlignmentFlag.AlignBottom)
        value_row.addStretch(1)
        col.addWidget(row)
        lay.addWidget(column)
        self.spark = Sparkline() if sparkline else None
        if self.spark is not None:
            self.spark.setAccessibleName(f"{title} trend")
            lay.addWidget(self.spark)
        lay.addStretch(1)

    def show_value(self, scope: str, value: str, unit: str = "") -> None:
        label = f"{self.title} · {scope}" if scope else self.title
        if self.label.text() != label:
            self.label.setText(label)
        if self.value.text() != value:
            self.value.setText(value)
        if self.unit.text() != unit:
            self.unit.setText(unit)


@dataclass
class _Pending:
    verb: str
    subject: tuple
    known_commands: frozenset
    known_events: frozenset
    waited: int = 0
    seen: dict = field(default_factory=dict)
    last_progress_at: float = field(default_factory=time.monotonic)


def _median(values):
    values = [value for value in values if value is not None]
    return statistics.median(values) if values else None


class TopologyScreen(Screen):
    key = "topology"
    title = "TOPOLOGY"
    heading = "Topology"

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.view = "GEO"
        self.layer = "physical"
        self._selection: tuple = (None, "")
        self._demand = ""
        self._pending: _Pending | None = None
        self._command_status = CommandStatus()
        self._context = None
        self._net = NetModel()
        self._fitting_strip = False
        self._strip_resize = QTimer(self)
        self._strip_resize.setSingleShot(True)
        self._strip_resize.timeout.connect(self._fit_strip)
        self._link_rows: list = []
        self._pick_node = ""

        self.link_editor = LinkEditor(self)
        self.link_editor.command.connect(self.command.emit)
        self.path_runner = PathRunner(self)
        self.path_runner.command.connect(self.command.emit)
        self.campaign_runner = CampaignRunner(self)
        self.location_dialog = LocationDialog(self)
        self.location_dialog.submitted.connect(self._submit_location)
        self.location_dialog.pick_requested.connect(self._begin_pick)

        middle = QWidget()
        mid = hbox(middle, (0, 0, 0, 0), 0)
        self.root.addWidget(middle, 1)
        centre = QWidget()
        column = vbox(centre, (0, 0, 0, 0), 0)
        mid.addWidget(centre, 1)
        self.centre = centre

        # ---- header strip ----------------------------------------------
        strip = Frame(border=C.soft, sides="b")
        self.strip_layout = _StripLayout(strip)
        left = QWidget()
        ll = hbox(left, (0, 0, 0, 0), 14)
        self.ring_label = Label("Topology · loading", C.ink)
        ll.addWidget(self.ring_label)
        self.health_host = QWidget()
        self.health_lay = hbox(self.health_host, (0, 0, 0, 0), 14)
        ll.addWidget(self.health_host)
        self._health: tuple = ()
        self._health_widgets: list[Chip] = []
        self.links_up = Label("—")
        ll.addWidget(self.links_up)
        self.strip_layout.addWidget(left)
        self._strip_left, self._strip_left_lay = left, ll
        views = QWidget()
        vl = hbox(views, (0, 0, 0, 0), 14)
        self._strip_views_lay = vl
        self.layer_caption = Label("Layer")
        vl.addWidget(self.layer_caption)
        self.layers: dict[str, Seg] = {}
        for key, title in LAYERS:
            button = Seg(title)
            button.clicked.connect(lambda checked=False, k=key: self.set_layer(k))
            vl.addWidget(button)
            self.layers[key] = button
        vl.addWidget(vrule(20))
        self.toggles: dict[str, Seg] = {}
        for key, title in VIEWS:
            button = Seg(title)
            button.setAccessibleName(f"{title} view")
            button.clicked.connect(lambda checked=False, k=key: self.set_view(k))
            vl.addWidget(button)
            self.toggles[key] = button
        self.strip_layout.addWidget(views)
        # When the row is too narrow for the two action buttons they fold behind this one, so the
        # strip stays on ONE line (the buttons then open on a second line on demand).
        self.more = Btn("⋯", variant="default")
        self.more.setFixedWidth(40)
        self.more.setAccessibleName("More actions")
        self.more.clicked.connect(self._toggle_actions)
        self.strip_layout.addWidget(self.more)
        self.more.hide()
        self.more.setEnabled(False)
        self._actions_open = False
        self._actions_allowed = (False, False)
        self._strip_key = None
        self._strip_level = 0
        self._strip_counts: dict = {}
        self._strip_links = ("—", "—", "")
        actions = QWidget()
        al = hbox(actions, (0, 0, 0, 0), 8)
        # Attribute names kept from the first console: `recalibrate` runs the scenario, `isolate` replays the model.
        self.recalibrate = Btn("Run simulated scenario", variant="accent")
        self.isolate = Btn("Replay WDM model", variant="default")
        self.recalibrate.setProperty("operation", "run_scenario")
        self.isolate.setProperty("operation", "replay_model")
        self.recalibrate.clicked.connect(lambda: self.command.emit("run_scenario", ""))
        self.isolate.clicked.connect(lambda: self.command.emit("replay_model", ""))
        al.addWidget(self.recalibrate)
        al.addWidget(self.isolate)
        self.actions = actions
        self.strip_layout.addWidget(actions)
        self.ml_control = MLControlPanel(self)
        self.ml_control.command.connect(self.command.emit)
        self.strip_layout.addWidget(self.ml_control)
        self.technology_panel = TechnologyPanel(self)
        self.technology_panel.command.connect(self.command.emit)
        self.strip_layout.addWidget(self.technology_panel)
        column.addWidget(strip)
        self.strip = strip
        strip.installEventFilter(self)

        # ---- the view ---------------------------------------------------
        view_host = QWidget()
        host = vbox(view_host, (18, 8, 18, 0), 0)
        self.graph_stack = QStackedWidget()
        self.geo = GeoMap()
        self.matrix = MatrixView()
        self.graph = TopologyGraph()
        for widget in (self.geo, self.matrix, self.graph):
            self.graph_stack.addWidget(widget)
        host.addWidget(self.graph_stack, 1)
        self.queue = UnplacedQueue()
        host.addWidget(self.queue)
        column.addWidget(view_host, 1)

        for view in (self.geo, self.matrix, self.graph, self.queue):
            view.node_selected.connect(self._node_clicked)
            view.link_selected.connect(self._link_clicked)
        self.geo.location_picked.connect(self._picked)
        self.geo.pick_cancelled.connect(self._pick_cancelled)
        self.geo.alert_clicked.connect(self._show_demand)
        self.geo.view_changed.connect(self._sync_anchors)
        self.queue.locate.connect(self._locate)
        self.queue.anchors_changed.connect(self._sync_anchors)

        # ---- legend and the three-cell strip ---------------------------
        legend = QWidget()
        lg = _StripLayout(legend, margins=(18, 6, 18, 6), gap=14, line_gap=4)
        legend.setAccessibleName('Legenda: componentes, estados dos enlaces e órbitas')
        for kind, label, tip in NODE_LEGEND:
            item = QWidget(); item.setToolTip(tip)
            il = hbox(item,(0,0,0,0),6)
            il.addWidget(_LegendSample('node:'+kind)); il.addWidget(Label(label))
            lg.addWidget(item)
        for style, label, tone_name in LEGEND + SPACE_LEGEND:
            item = QWidget()
            item.setToolTip(LEGEND_TIPS.get(style, ""))
            il = hbox(item, (0, 0, 0, 0), 6)
            il.addWidget(_LegendSample(style))
            il.addWidget(Label(label, None if tone_name in ("ink", "dim") else
                               {"amber": C.acc_t, "red": C.red_t, "violet": C.violet_t}[tone_name]))
            lg.addWidget(item)
        self.view_note = L("", text_font(12), C.ink3, Qt.AlignmentFlag.AlignRight, elide=True)
        self.view_note.setMinimumWidth(0)
        lg.addWidget(self.view_note)
        column.addWidget(legend)
        self.legend = legend

        cells = Frame(border=C.soft, sides="t")
        cl = hbox(cells, (0, 0, 0, 0), 0)
        self.cell_fidelity = _StripCell("Bell fidelity", sparkline=True)
        self.cell_qber = _StripCell("QBER")
        self.cell_skr = _StripCell("Secret key rate", border=False)
        for cell in (self.cell_fidelity, self.cell_qber, self.cell_skr):
            cl.addWidget(cell, 1)
        column.addWidget(cells)

        # ---- inspector --------------------------------------------------
        self.inspector = Inspector()
        mid.addWidget(self.inspector)
        self.inspector.select.connect(self._inspector_select)
        self.inspector.open_node.connect(self._open_window)
        self.inspector.locate.connect(self._locate)
        self.inspector.navigate.connect(self.navigate.emit)
        self.inspector.action.connect(self._action)
        self.inspector.demand_selected.connect(self._demand_selected)
        self.inspector.tab_changed.connect(self.apply_command_capabilities)
        self.link_table = self.inspector.link_table
        self.link_table.clicked.connect(self._link_selected)
        self.edit_links, self.run_path = self.inspector.edit_links, self.inspector.run_path
        self.run_campaign = self.inspector.run_campaign
        self.edit_links.clicked.connect(self.link_editor.begin)
        self.run_path.clicked.connect(self.path_runner.begin)
        self.run_campaign.clicked.connect(self.campaign_runner.begin)

        # ---- floating node windows -------------------------------------
        self.node_windows = NodeWindows(self, incremental=True)
        self.node_windows.controls_changed.connect(self.apply_command_capabilities)
        self.node_windows.overlay_active = lambda: self.geo.picking
        self.node_windows.overlay_cancel = self.geo.cancel_pick
        self.node_windows.select.connect(self._inspector_select)
        self.node_windows.command.connect(self.command.emit)
        self.node_windows.action.connect(self._action)
        self.node_windows.locate.connect(self._locate)
        self.node_popover = self.node_windows        # the shell's Esc and older callers use this name

        self.set_layer("physical")
        self.set_view("GEO")

    # ------------------------------------------------------------ view state
    def breadcrumb(self, snap, selection=None) -> str:
        selected = (selection.id if selection is not None and selection.id else
                    snap.node.id if snap is not None else "")
        return "/ topology" + (f" / {selected}" if selected else "")

    def save_state(self) -> dict:
        return {**super().save_state(), "view": self.view, "layer": self.layer, "tab": self.inspector.tab,
                "demand": self._demand, "geo": self.geo.view_state(), "geo_scene": self.geo.save_scene_state(),
                "windows": self.node_windows.state()}

    def restore_state(self, state: dict) -> None:
        super().restore_state(state)
        if state.get("layer") in self.layers:
            self.set_layer(state["layer"])
        if state.get("view") in self.toggles:
            self.set_view(state["view"])
        if state.get("demand"):
            self._demand = state["demand"]
        if state.get("geo"):
            self.geo.set_view_state(state["geo"])
        if state.get("geo_scene"):
            self.geo.restore_scene_state(state["geo_scene"])
        if state.get("tab"):
            self.inspector.set_tab(state["tab"])
        self.node_windows.restore(state.get("windows"))      # node windows survive a theme rebuild
        self._render()

    def shutdown(self) -> None:
        self.geo.shutdown()
        self.technology_panel.shutdown()
        self.node_windows.shutdown()
        self.geo.cancel_pick()
        super().shutdown()

    def set_view(self, view: str) -> None:
        if view not in self.toggles:
            return
        self.view = view
        for name, item in self.toggles.items():
            item.set_active(name == view)
        self.graph_stack.setCurrentWidget({"GEO": self.geo, "MATRIX": self.matrix, "GRAPH": self.graph}[view])
        if view != "GEO":
            self.geo.cancel_pick()
        self.view_note.setText(VIEW_NOTES[view][0])
        self.view_note.setToolTip(VIEW_NOTES[view][1])
        self._render()

    def set_layer(self, layer: str) -> None:
        if layer not in self.layers:
            return
        self.layer = layer
        for name, item in self.layers.items():
            item.set_active(name == layer)
        self._render()

    def eventFilter(self, watched, event) -> bool:
        if watched is self.strip and event.type() == QEvent.Type.Resize \
                and event.size().width() != event.oldSize().width():
            # Changing header margins and labels inside Qt's active resize can
            # recursively resize its scroll host. Coalesce at the next turn.
            self._strip_resize.start(0)
        return super().eventFilter(watched, event)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self.node_windows.fit()
        QTimer.singleShot(0, self, self._sync_anchors)

    # ---------------------------------------------------------- header strip
    STRIP_LEVELS = 4          # 0 full · 1 compact · 2 no "Layer" caption · 3 counts only (words in tooltips)

    def _toggle_actions(self) -> None:
        self._actions_open = not self._actions_open
        self._strip_key = None
        self._fit_strip()

    def _health_chips(self, level: int) -> tuple:
        """`(text, tone, tooltip)` of the node-health chips from the node states of the snapshot."""

        counts = self._strip_counts
        chips = []
        for symbol, name, tone_name, always in (("●", "up", "ok", True), ("▲", "degraded", "warn", True),
                                                ("■", "down", "bad", True), ("○", "isolated", "mute", False),
                                                ("○", "calibrating", "sat", False), ("○", "unknown", "mute", False)):
            count = counts.get(name, 0)
            if not count and not always:
                continue
            short = level >= 3 or (level >= 1 and not count)
            chips.append((f"{symbol} {count}" if short else f"{symbol} {count} {name}", tone_name,
                          f"{count} node{'' if count == 1 else 's'} {name}"))
        return tuple(chips)

    def _apply_strip_level(self, level: int) -> None:
        compact = level >= 1
        gap = 8 if level >= 3 else 10 if compact else 14
        self.strip_layout.setContentsMargins(12 if compact else 18, 10, 12 if compact else 18, 10)
        self.strip_layout.set_gap(gap)
        for layout in (self._strip_left_lay, self._strip_views_lay, self.health_lay):
            if layout.spacing() != gap:
                layout.setSpacing(gap)
        chips = self._health_chips(level)
        if chips != self._health:
            self._health = chips
            if len(chips) != len(self._health_widgets):
                clear_layout(self.health_lay)
                self._health_widgets = [Chip() for _ in chips]
                for chip in self._health_widgets:
                    self.health_lay.addWidget(chip)
            for chip, (value, tone_name, tip) in zip(self._health_widgets, chips):
                if chip.text() != value:
                    chip.setText(value)
                if chip._tone != tone_name:
                    chip.set_tone(tone_name)
                if chip.toolTip() != tip:
                    chip.setToolTip(tip)
                chip.ensurePolished()         # its styled size must be known before the row is measured
        text = self._strip_links[1 if compact else 0]
        if self.links_up.text() != text:
            self.links_up.setText(text)
        self.links_up.setToolTip(self._strip_links[2])
        self.layer_caption.setVisible(level < 2)
        any_action = any(self._actions_allowed)
        self.more.setVisible(compact and any_action)
        self.more.setEnabled(any_action)          # a source that declares neither action offers nothing here
        self.more.setText("×" if self._actions_open else "⋯")
        self.actions.setVisible(any_action and (not compact or self._actions_open))

    def _fit_strip(self) -> None:
        if self._fitting_strip:
            self._strip_resize.start(0)
            return
        self._fitting_strip = True
        try:
            self._fit_strip_groups()
        finally:
            self._fitting_strip = False

    def _fit_strip_groups(self) -> None:
        """Choose the least compact header that keeps its groups on one line at this width."""

        width = self.strip.width()
        key = (width, tuple(sorted(self._strip_counts.items())), self._strip_links, self._actions_allowed,
               self._actions_open, self.recalibrate.text(), self.isolate.text(), self.ring_label.text(),
               not self.ml_control.isHidden(), self.ml_control.sizeHint().width())
        if key == self._strip_key:
            return
        self._strip_key = key
        for level in range(self.STRIP_LEVELS):
            self._apply_strip_level(level)
            # folded actions open on a line of their own: they are not part of the one-line budget
            skip = (self.actions,) if level >= 1 else ()
            # Nested and view layouts must settle before the outer group is
            # measured; otherwise it retains the full labels after compaction.
            for layout in (self.health_lay, self._strip_views_lay, self._strip_left_lay):
                layout.invalidate()
                layout.activate()
            if self.strip_layout.needed_width(skip) + 6 <= width:
                break
        self._strip_level = level
        self.strip_layout.invalidate()

    # ------------------------------------------------------------- selection
    def on_selection(self, selection) -> None:
        current = (selection.kind, selection.id) if selection is not None and selection.kind else (None, "")
        if current != self._selection:
            self._selection = current
            self._render()

    def cache_selection(self, selection) -> None:
        self._selection = (selection.kind, selection.id) if selection is not None and selection.kind else (None, "")

    def _select(self, kind: str, ident: str) -> None:
        if (kind, ident) != self._selection:
            self._selection = (kind, ident)
            self._render()
        self.select.emit(kind, ident)                # the shell keeps the selection for every screen

    def _node_clicked(self, node_id: str) -> None:
        """A click on a node selects it and opens its floating window."""

        self._select("node", node_id)
        self.inspector.set_tab("insp")
        self._open_window(node_id)

    def _link_clicked(self, key: str) -> None:
        self._select("link", key)
        self.inspector.set_tab("insp")

    def _inspector_select(self, kind: str, ident: str) -> None:
        self._select(kind, ident)
        if self.inspector.tab != "insp":
            self.inspector.set_tab("insp")

    def _link_selected(self, index) -> None:
        if 0 <= index.row() < len(self._link_rows):
            self._select("link", self._link_rows[index.row()])

    def _demand_selected(self, demand_id: str) -> None:
        self._demand = demand_id
        self._render()

    def _show_demand(self, demand_id: str) -> None:
        self.inspector.show_demand(demand_id)

    def _open_window(self, node_id: str):
        if self._snap is None or self._net.node(node_id) is None:
            return None
        anchor = None
        if self.view == "GEO":
            position = self.geo.node_position(node_id)
            if position is not None:
                anchor = self.geo.mapTo(self, QPoint(round(position.x()), round(position.y())))
        window = self.node_windows.open(
            node_id, anchor,
            prepare=lambda item: item.refresh(self._snap, self._net, self._command_status),
        )
        self.apply_command_capabilities()
        return window

    # -------------------------------------------------------------- location
    def _locate(self, node_id: str) -> None:
        node = self._net.node(node_id)
        if node is not None:
            self.location_dialog.begin(node)
            self.apply_command_capabilities()

    def _begin_pick(self, node_id: str) -> None:
        self._pick_node = node_id
        self.set_view("GEO")
        self.location_dialog.hide()
        self.geo.start_pick(node_id)

    def _picked(self, latitude: float, longitude: float) -> None:
        self.location_dialog.set_picked(latitude, longitude)

    def _pick_cancelled(self) -> None:
        if self._pick_node:
            self.location_dialog.show()

    def _submit_location(self, node_id: str, values: dict) -> None:
        self._action("set_node_location", "node", node_id, values)

    # ---------------------------------------------------------------- commands
    def _action(self, verb: str, kind: str, ident: str, fields: dict) -> None:
        """Dispatch a backend verb for a node, a fibre or a demand and show PENDING."""

        snap = self._snap
        if snap is None or self._pending is not None:
            return
        if not snap.capabilities.allows(verb):
            self._set_status(CommandStatus((kind, ident), f"UNAVAILABLE · {verb} is not declared by this source.",
                                           "amber"))
            return
        payload = dict(fields or {})
        if kind == "node":
            payload["node_id"] = ident
        elif kind == "link":
            link = self._net.link(ident)
            if link is None or not link.id:
                self._set_status(CommandStatus((kind, ident), "UNAVAILABLE · this fibre has no identifier.", "amber"))
                return
            payload["link_id"] = link.id
        else:
            payload["demand_id"] = ident
        if verb in TRACKED_VERBS:
            payload.update(expected_version=snap.topology.version, topology_id=snap.topology.id)
        live = snap.live
        self._pending = _Pending(
            verb, (kind, ident), frozenset(item.command_id for item in snap.ledger.commands),
            frozenset(event.id for event in live.events) if live is not None else frozenset())
        self._set_status(CommandStatus((kind, ident), f"PENDING · {verb} · {ident} · waiting for the backend",
                                       "amber", True))
        self.command.emit(verb, json.dumps(payload, ensure_ascii=False))

    def command_failed(self, message) -> None:
        """The provider reported an error: for a pending verb this is the backend's refusal."""

        self.ml_control.command_failed(message)
        self.technology_panel.command_failed(message)
        pending = self._pending
        if pending is None:
            return
        self._pending = None
        label = "UNKNOWN" if pending.verb in TRACKED_VERBS else "REFUSED"
        self._set_status(CommandStatus(pending.subject, f"{label} · {pending.verb} · {message}", "red"))
        if pending.verb == "set_node_location":
            self.location_dialog.set_note(f"{label} · {message}", "red")

    def _set_status(self, status: CommandStatus) -> None:
        self._command_status = status
        self._render()
        self.apply_command_capabilities()

    def _resolve_pending(self, snap: Snapshot) -> None:
        pending = self._pending
        if pending is None:
            return
        kind, ident = pending.subject
        if pending.verb in TRACKED_VERBS:
            record = next((item for item in snap.ledger.commands if item.verb == pending.verb
                           and item.command_id not in pending.known_commands), None)
            if record is not None:
                state = str(record.state).upper()
                short = record.command_id[:8]
                if state == "CONFIRMED":
                    self._pending = None
                    self._command_status = CommandStatus(
                        pending.subject, f"CONFIRMED · {pending.verb} · {ident} · command {short} · "
                        f"topology v{snap.topology.version}", "green")
                    if pending.verb == "set_node_location":
                        self.location_dialog.set_note("CONFIRMED · the backend persisted the location.", "green")
                        self.location_dialog.close()
                    return
                if state in ("UNKNOWN", "REJECTED"):
                    self._pending = None
                    self._command_status = CommandStatus(
                        pending.subject, f"{state} · {pending.verb} · {ident} · command {short} · the backend did "
                        "not confirm it; the topology is unchanged unless shown otherwise.", "red")
                    return
                self._command_status = CommandStatus(
                    pending.subject, f"{state} · {pending.verb} · {ident} · command {short}", "amber", True)
                return
        elif snap.live is not None:
            event = next((item for item in reversed(snap.live.events) if item.subject == ident
                          and item.id not in pending.known_events and item.source != "input_source"
                          and item.kind.startswith(("pair", "purif"))), None)
            if event is not None:
                self._pending = None
                self._command_status = CommandStatus(
                    pending.subject, f"{event.kind.upper().replace('_', ' ')} · {event.message}",
                    {"info": "green", "warning": "amber", "critical": "red"}.get(event.severity, "ink"))
                return
        now = time.monotonic()
        if pending.verb == "recalibrate_node" and snap.live is not None:
            node = snap.live.node(ident)
            calibration = node.calibration if node is not None else None
            if calibration is not None and calibration.status == "running":
                progress = (calibration.calibration_id, calibration.phase, calibration.progress)
                if pending.seen.get("calibration_progress") != progress:
                    pending.seen["calibration_progress"] = progress
                    pending.last_progress_at = now
                if now - pending.last_progress_at < PENDING_READBACK_TIMEOUT_S:
                    self._command_status = CommandStatus(
                        pending.subject, f"RUNNING · {pending.verb} · {ident} · {calibration.phase} · "
                        f"progress {percent(calibration.progress)} · awaiting command receipt", "amber", True)
                    return
        pending.waited += 1
        if now - pending.last_progress_at >= PENDING_READBACK_TIMEOUT_S:
            self._pending = None
            self._command_status = CommandStatus(
                pending.subject, f"UNRESOLVED · {pending.verb} · {ident} · no readback from the backend in "
                f"{PENDING_READBACK_TIMEOUT_S:g} seconds without progress; inspect the journal.", "red")

    def apply_command_capabilities(self) -> None:
        super().apply_command_capabilities()
        if self._snap is None:
            return
        busy = self._pending is not None
        for button in self.findChildren(Btn):
            if not button.property("operation"):
                continue
            if button.property("ready") is False or (busy and button.property("operation") in
                                                     TRACKED_VERBS + PAIR_VERBS):
                button.setEnabled(False)

    # ----------------------------------------------------------------- refresh
    def refresh(self, snap: Snapshot) -> None:
        self.ml_control.set_snapshot(snap)
        self.technology_panel.set_snapshot(snap)
        context = (snap.input_kind, snap.campaign, snap.capabilities.adapter_id, snap.topology.id)
        if self._context is not None and self._context != context:
            self._pending = None
            self._command_status = CommandStatus()
            self.node_windows.close_all()
            self.location_dialog.hide()
            self.geo.cancel_pick()
            self._demand = ""
        self._context = context
        self._resolve_pending(snap)
        self.link_editor.set_snapshot(snap)
        self.path_runner.set_snapshot(snap)
        capabilities = snap.capabilities
        self.run_path.setVisible(capabilities.allows("run_workspace_path"))
        self.run_campaign.setVisible(capabilities.allows("read_temporal_campaign_history"))
        self.edit_links.setVisible(capabilities.allows("configure_simulated_link"))
        self._actions_allowed = (capabilities.allows("run_scenario"), capabilities.allows("replay_model"))
        self.recalibrate.setVisible(self._actions_allowed[0])
        self.isolate.setVisible(self._actions_allowed[1])
        artificial = snap.campaign.startswith("synthetic-")
        self.recalibrate.setText("Run controller tests" if artificial else "Run simulated scenario")
        self.isolate.setText("Advance test frame" if artificial else "Replay WDM model")
        self.more.setToolTip("More actions: " + " · ".join(
            button.text() for button, allowed in zip((self.recalibrate, self.isolate), self._actions_allowed)
            if allowed))
        self._render()

    def _render(self) -> None:
        """Share fresh state with every view; present only the active view."""

        snap = self._snap
        if snap is None:
            return
        live, topology = snap.live, snap.topology
        if live is not None:
            if self._demand and live.demand(self._demand) is None:
                self._demand = ""
            if not self._demand and live.demands:
                self._demand = next((demand.id for demand in live.demands if demand.status in UNROUTED),
                                    live.demands[0].id)
        else:
            self._demand = ""
        kind, ident = self._selection
        net = build_net_model(snap, self._selection, self.layer, self._demand)
        if (kind == "node" and net.node(ident) is None) or (kind == "link" and net.link(ident) is None):
            net = build_net_model(snap, (None, ""), self.layer, self._demand)
        self._net = net

        # header strip: source, health chips from the real node states, fibres in service
        if snap.input_kind == "measured":
            source = "measured input"
        elif live is not None:
            source = {"simulated": "simulated input", "none": "no input feed"}.get(live.source_kind,
                                                                                   f"{live.source_kind} input")
        else:
            source = "simulated"
        self.ring_label.setText(f"{topology.ring} · {source}".upper())
        self.ring_label.setToolTip(topology.caveat)
        counts: dict[str, int] = {}
        for node in net.nodes:
            name = node.status or node.health
            counts[name] = counts.get(name, 0) + 1
        if live is not None:
            in_service = sum(1 for link in live.links if link.effective)
            links = (f"{in_service}/{len(live.links)} fibres in graph", f"{in_service}/{len(live.links)} fibres",
                     "Fibres in the effective routing graph: enabled, no end isolated or calibrating, no attack "
                     "alarm held")
        elif snap.input_kind == "measured":
            links = (topology.links_up, topology.links_up, "Fibres enabled in the configuration")
        else:
            links = (f"{topology.links_up} enabled", f"{topology.links_up} enabled",
                     "Fibres enabled in the configuration")
        self._strip_counts, self._strip_links = counts, links
        self._fit_strip()

        # Hidden inspections retain current data without layout, painting or
        # Qt table notifications. Activation presents the latest shared state.
        if self.view == "GEO":
            self.geo.set_model(net)
        else:
            self.geo.set_model_state(net)
        if self.view == "MATRIX":
            self.matrix.set_model(net)
        else:
            self.matrix.set_model_state(net)
        self.graph.set_topology(net.nodes, net.links, presentation=self.view == "GRAPH")
        self.graph.set_overlay(net.selection, net.route, net.badge_map, net.layer,
                               presentation=self.view == "GRAPH")
        self.queue.set_model(net, map_visible=self.view == "GEO")
        unrouted = [demand for demand in live.demands if demand.status in UNROUTED] if live is not None else []
        self.geo.set_alerts([(demand.id, f"■ {demand.id} {demand.a} ↔ {demand.b} · "
                              f"{demand.status.upper().replace('_', ' ')}", demand_reason(demand))
                             for demand in unrouted], presentation=self.view == "GEO")
        self._sync_anchors()

        self._render_strip(snap, net)
        self._render_links(snap, net)
        self.inspector.refresh(snap, net, self._demand, self._command_status)
        self.node_windows.refresh(snap, net, self._command_status)
        if self.location_dialog.isVisible() and self._pending is not None \
                and self._pending.verb == "set_node_location":
            self.location_dialog.set_note(self._command_status.text, "amber")

    def _sync_anchors(self) -> None:
        if not self.queue.isVisible() and not self.queue.chips:
            self.geo.set_queue_anchors({}, {})
            return
        links, nodes = self.queue.wires_for_map()
        offset = self.queue.mapTo(self.centre, QPoint(0, 0)).x() - self.geo.mapTo(self.centre, QPoint(0, 0)).x() \
            if self.geo.isVisible() and self.queue.isVisible() else 0
        self.geo.set_queue_anchors({key: x + offset for key, x in links.items()},
                                   {key: x + offset for key, x in nodes.items()})

    def _render_strip(self, snap: Snapshot, net: NetModel) -> None:
        live = snap.live
        kind, ident = net.selection
        link = net.link(ident) if kind == "link" else None
        spark: tuple = ()
        if link is not None:
            engine = live.link(link.id) if live is not None and link.id else None
            scope = skr_scope = link.key
            if engine is not None:
                fidelity, qber = number(engine.fidelity, ".3f"), percent(engine.qber)
                skr = rate(engine.skr_bps)
                spark = engine.history.fidelity
            else:
                fidelity, qber, skr = link.fidelity, link.qber, link.skr
        elif live is not None:
            producing = [item for item in live.links if item.fidelity is not None]
            scope = f"median of {len(producing)} fibres"
            skr_scope = f"sum of {len(producing)} fibres"
            fidelity = number(_median(item.fidelity for item in producing), ".3f")
            qber = percent(_median(item.qber for item in producing))
            rates = [item.skr_bps for item in producing if item.skr_bps is not None]
            skr = rate(sum(rates)) if rates else "—"
            length = max((len(item.history.fidelity) for item in producing), default=0)

            def sample(item, index):          # histories are aligned at their newest value
                position = index - (length - len(item.history.fidelity))
                return item.history.fidelity[position] if position >= 0 else None

            spark = tuple(_median(sample(item, index) for item in producing) for index in range(length))
        else:
            values = [item.fidelity_value for item in net.links if item.fidelity_value is not None
                      and item.style != "off"]
            scope = f"median of {len(values)} links"
            skr_scope = "not aggregated for this source"
            fidelity, qber, skr = number(_median(values), ".3f"), "—", "—"
        value, _, unit = skr.partition(" ")
        self.cell_fidelity.show_value(scope, fidelity)
        self.cell_qber.show_value(scope, qber)
        self.cell_skr.show_value(skr_scope, value, unit)
        self.cell_fidelity.spark.set_values(spark)
        self.cell_fidelity.spark.setVisible(len([item for item in spark if item is not None]) >= 2)

    def _render_links(self, snap: Snapshot, net: NetModel) -> None:
        live = snap.live
        measured = snap.input_kind == "measured"
        rows, keys = [], []
        short = {"up": "UP", "degraded": "DEGR", "down": "DOWN", "disabled": "OFF", "isolated": "ISOL",
                 "calibrating": "CAL", "compromised": "ALARM", "no_input": "NO IN"}
        for link in net.links:
            engine = live.link(link.id) if live is not None and link.id else None
            colour = text_tone(STYLE_TONE.get(link.style, "ink"))
            name = f"{link.label}" if measured else link.key
            if engine is not None:
                state = short.get(engine.status, engine.status.upper())
                rows.append((name, number(engine.fidelity, ".3f"),
                             "—" if engine.qber is None else f"{engine.qber * 100:.2f}%",
                             compact_rate(engine.skr_bps), colour, C.ink, state,
                             f"{link.processing_label} | {link.data_source.upper()}"))
            else:
                state = {"ENABLED": "ON", "DISABLED": "OFF", "DEGRADED": "DEGR", "PAIR PATH": "PAIR"}.get(
                    link.state_text, link.state_text)
                rows.append((name, link.fidelity, "—" if link.qber == "not in this lane" else link.qber,
                             "—" if link.skr == "not in this lane" else link.skr, colour, C.ink, state,
                             f"{link.processing_label} | {link.data_source.upper()}"))
            keys.append(link.key)
        self._link_rows = keys
        self._set_links(rows)
        kind, ident = net.selection
        selected = net.link(ident) if kind == "link" else None
        if selected is not None and selected.key in keys:
            row = keys.index(selected.key)
            if self.link_table.currentIndex().row() != row or not self.link_table.selectionModel().hasSelection():
                self.link_table.selectRow(row)
        elif self.link_table.selectionModel().hasSelection():
            self.link_table.clearSelection()
        if live is not None:
            summary = f"{sum(1 for item in live.links if item.effective)}/{len(live.links)} in the routing graph"
        elif measured:
            summary = snap.topology.links_up
        else:
            summary = f"{snap.topology.links_up} enabled"
        if self.inspector.links_up.text() != summary:
            self.inspector.links_up.setText(summary)
        self.inspector.links_actions.setVisible(any(snap.capabilities.allows(name) for name in (
            "configure_simulated_link", "run_workspace_path", "read_temporal_campaign_history")))

    def _set_links(self, rows) -> None:
        """Rows of the Links tab: `(name, F, QBER, SKR, name colour, value colour[, state])`."""

        records = []
        for row in rows:
            name, fidelity, qber, skr, name_colour, value_colour = row[:6]
            state = row[6] if len(row) > 6 else ""
            record = [(name, name_colour), (fidelity, value_colour), (qber, C.ink), (skr, C.ink),
                      (state, name_colour)]
            if self.link_table.model().columnCount() > 5:
                record.append((row[7] if len(row) > 7 else "UNREPORTED", C.ink3))
            records.append(record)
        self.link_table.set_rows(records)
        metrics = self.link_table.fontMetrics()
        for col in (1, 2, 3, 4):
            texts = {row[col][0] for row in self.link_table.records.rows}
            width = max((metrics.horizontalAdvance(value) + 18 for value in texts),
                        default=40)
            width = min(120, max(56 if col < 4 else 46, width))
            if self.link_table.columnWidth(col) != width:
                self.link_table.setColumnWidth(col, width)


__all__ = ["LAYERS", "PAIR_VERBS", "TRACKED_VERBS", "TopologyScreen", "VIEWS"]
