"""The shared network model of the Topology screen, and the direct-fibre matrix.

`build_net_model(snapshot, …)` turns one snapshot into the `NetModel` that the
GEO map, the matrix, the logical graph and the unplaced-node queue all draw:
the same nodes, the same fibres, the same selection. Nothing here computes
physics or decides a state — every value is the snapshot's; a value the source
did not produce is "—".
"""
from __future__ import annotations

import math
from collections import OrderedDict, defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
from functools import cached_property

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QPainter, QPen
from PySide6.QtWidgets import QAbstractItemView, QHeaderView, QStyledItemDelegate, QTableView, QWidget

from ..contract import LINK_STATUS_TONE
from ..processing_labels import link_processing_labels
from ..theme import C, family, label_font, mix, mono, qc, text as text_font, text_tone
from .figures import link_key, link_style, marker_kind, paint_marker
from .prims import vbox

FIRST_COLUMN_W = 118
STATUS_SHORT = {"up": "UP", "degraded": "DEGRADED", "down": "DOWN", "disabled": "DISABLED", "isolated": "ISOLATED",
                "calibrating": "CALIBRATING", "compromised": "COMPROMISED", "no_input": "NO INPUT"}
#: Tone of a drawing style, for texts that name a fibre.
STYLE_TONE = {"calm": "ink", "warn": "amber", "bad": "red", "off": "dim", "cal": "violet", "compromised": "red",
              "pair": "violet", "space": "violet", "space_config": "dim"}


def valid_coordinates(latitude, longitude) -> bool:
    return (isinstance(latitude, (int, float)) and isinstance(longitude, (int, float))
            and math.isfinite(latitude) and math.isfinite(longitude)
            and -90 <= latitude <= 90 and -180 <= longitude <= 180)


def format_km(value) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "—"
    return f"{number:.1f} km" if math.isfinite(number) else "—"


def format_lat(value) -> str:
    return "—" if value is None else f"{abs(value):.4f}° {'S' if value < 0 else 'N'}"


def format_lon(value) -> str:
    return "—" if value is None else f"{abs(value):.4f}° {'W' if value < 0 else 'E'}"


# -------------------------------------------------------------------- model
@dataclass(frozen=True)
class NetNode:
    id: str
    role: str
    kind: str
    health: str
    status: str
    isolated: bool
    located: bool
    latitude: float | None
    longitude: float | None
    place: str | None
    x: float
    y: float
    network_role: str = ''
    physical_type: str = ''
    display_id: str = ''
    name: str = ''
    category: str = ''

    @property
    def map_label(self):
        return self.display_id or self.id

    @property
    def labelled(self):
        return self.network_role in ('endpoint','core')


@dataclass(frozen=True)
class NetLink:
    key: str                    # selection identity (`link_key`)
    id: str                     # source-local id, may be ""
    a: str
    b: str
    style: str                  # figures.LINK_STYLES
    status: str                 # engine status or ""
    tone: str
    fidelity: str
    qber: str
    skr: str
    distance_km: float | None
    configured_active: bool | None
    label: str
    kind: str = "fibre"
    fidelity_value: float | None = None
    processing_label: str = "UNREPORTED"
    data_source: str = "unreported"
    processing_detail: str = "No per-link processing record received."
    processing_physics: str = "not reported by the backend"

    @property
    def state_text(self) -> str:
        if self.configured_active is False and self.status != "isolated":
            return "DISABLED"
        if self.status:
            return STATUS_SHORT.get(self.status, self.status.upper())
        return {"bad": "FAULT", "warn": "DEGRADED", "pair": "PAIR PATH"}.get(self.style, "ENABLED")


@dataclass(frozen=True)
class NetModel:
    nodes: tuple = ()
    links: tuple = ()
    selection: tuple = (None, "")           # (kind, id)
    layer: str = "physical"
    route: tuple = ()                        # node ids of the selected demand's route
    badges: tuple = ()                       # ((node id, "END" | "BSM"), …)
    demand_id: str = ""
    has_live: bool = False
    satellites: dict = None
    network_time_s: float = 0.
    communicators: dict = None

    @cached_property
    def _node_index(self):
        result = {}
        for node in self.nodes:
            result.setdefault(node.id, node)
        return result

    @cached_property
    def _link_index(self):
        result = {}
        for link in self.links:
            result.setdefault(link.key, link)
            if link.id:
                result.setdefault(link.id, link)
        return result

    @cached_property
    def _adjacency(self):
        result = defaultdict(list)
        for link in self.links:
            if link.style != "pair":
                result[link.a].append(link)
                if link.b != link.a:
                    result[link.b].append(link)
        return {key: tuple(entries) for key, entries in result.items()}

    def node(self, node_id: str):
        return self._node_index.get(node_id)

    def link(self, key: str):
        return self._link_index.get(key)

    @cached_property
    def located(self) -> tuple:
        return tuple(node for node in self.nodes if node.located)

    @cached_property
    def unlocated(self) -> tuple:
        return tuple(node for node in self.nodes if not node.located)

    def links_of(self, node_id: str) -> tuple:
        return self._adjacency.get(node_id, ())

    @property
    def badge_map(self) -> dict:
        return dict(self.badges)

    def selected(self, kind: str, ident: str) -> bool:
        return self.selection[0] == kind and self.selection[1] == ident


def _fidelity_value(text: str):
    try:
        number = float(text)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and 0 <= number <= 1 else None


def build_net_model(snap, selection=(None, ""), layer: str = "physical", demand_id: str = "") -> NetModel:
    """One model for every view, from the snapshot alone."""

    if snap is None:
        return NetModel(selection=tuple(selection), layer=layer)
    topology, live = snap.topology, snap.live
    ids = {node.id for node in topology.nodes}
    nodes = tuple(NetNode(
        id=node.id, role=node.role, kind=node.kind, health=node.health, status=node.status or "",
        isolated=bool(node.isolated) or node.status == "isolated",
        located=bool(node.located) and valid_coordinates(node.latitude, node.longitude),
        latitude=node.latitude, longitude=node.longitude, place=node.place,
        x=node.x if math.isfinite(node.x) else 0.5, y=node.y if math.isfinite(node.y) else 0.5,
        network_role=node.network_role or ('core' if node.kind=='core' else node.role.split(' · ')[0].lower()),
        physical_type=node.physical_type,display_id=node.display_id,name=node.name,category=node.category)
        for node in topology.nodes)
    distinguish = any(link.tone == "green" for link in topology.links)
    live_links = {link.id: link for link in live.links} if live is not None else {}
    satellite_ids = {n.id for n in nodes if n.physical_type == 'satellite'}
    emitting = {item['link_id'] for state in (live.satellites or {}).values()
                for item in state.get('transmitting_links', ())} if live is not None else set()
    links = []
    for link in topology.links:
        if link.a not in ids or link.b not in ids:
            continue
        engine = live_links.get(link.id)
        processing = link_processing_labels(engine, getattr(live, "technologies", None))
        links.append(NetLink(
            key=link_key(link), id=link.id, a=link.a, b=link.b,
            style=('space' if link.id in emitting else 'space_config') if satellite_ids.intersection((link.a,link.b))
                  else link_style(link, tones_distinguish=distinguish), status=link.status or "", tone=link.tone,
            fidelity=link.fidelity or "—", qber=link.qber or "—", skr=link.skr or "—",
            distance_km=(engine.distance_km if engine is not None and satellite_ids.intersection((link.a,link.b))
                         else link.distance_km), configured_active=link.configured_active, label=link.label,
            kind='satellite_isl' if satellite_ids.issuperset((link.a,link.b))
                 else 'satellite' if satellite_ids.intersection((link.a,link.b)) else link.kind,
            fidelity_value=engine.fidelity if engine is not None else _fidelity_value(link.fidelity),
            processing_label=processing.badge, data_source=processing.data_source,
            processing_detail=processing.detail, processing_physics=processing.physics))
    route, badges, shown = (), (), ""
    demand = live.demand(demand_id) if live is not None and demand_id else None
    if layer == "entanglement" and demand is not None:
        shown = demand.id
        route = tuple(name for name in demand.route if name in ids)
        pair = demand.pair if isinstance(demand.pair, Mapping) else {}
        swaps = pair.get("swaps_at") if list(pair.get("route") or ()) == list(demand.route) else None
        if not isinstance(swaps, (list, tuple)):
            swaps = route[1:-1]          # a path joins its hops by a swap at every intermediate node
        marks = {name: "BSM" for name in swaps if name in ids}
        marks.update({name: "END" for name in (demand.a, demand.b) if name in ids})
        badges = tuple(marks.items())
    return NetModel(nodes=nodes, links=tuple(links), selection=tuple(selection), layer=layer, route=route,
                    badges=badges, demand_id=shown, has_live=live is not None,
                    satellites=live.satellites if live else {}, network_time_s=live.network_time_s if live else 0.,
                    communicators=getattr(live, 'communicators', {}) if live else {})


# ------------------------------------------------------------------- matrix
@dataclass(frozen=True)
class MatrixCell:
    text: str = "·"
    detail: str = ""
    tone: str = "dim"
    link: str = ""               # link key when the cell is ONE direct fibre
    style: str = ""
    tooltip: str = ""


def _fidelity_tone(number) -> str:
    return "green" if number >= .93 else "amber" if number >= .9 else "red"


def _matrix_cell(a, b, entries=()):
    """One direct-fibre cell, shared by exports and the virtual table."""
    if a.id == b.id:
        return MatrixCell("—", tooltip=a.id)
    if len(entries) > 1:
        return MatrixCell("MULTI", f"{len(entries)} fibres", "muted",
                          tooltip="Parallel fibres: " + ", ".join(link.key for link in entries))
    if not entries:
        return MatrixCell("·", tooltip=f"No direct fibre between {a.id} and {b.id}")
    link = entries[0]
    number = link.fidelity_value
    state = link.state_text
    if link.configured_active is False and link.status != "isolated":
        value, tone_name = "OFF", "dim"
    elif link.status:
        tone_name = LINK_STATUS_TONE.get(link.status, "muted")
        value = f"{number:.3f}" if number is not None and link.status in ("up", "degraded", "down") \
            else {"disabled": "OFF", "isolated": "ISO", "calibrating": "CAL", "compromised": "ALARM",
                  "no_input": "—", "down": "×"}.get(link.status, "—")
    elif link.style == "bad":
        value, tone_name = "×", "red"
    elif number is not None:
        value, tone_name = f"{number:.3f}", _fidelity_tone(number)
    else:
        value, tone_name = "—", "muted"
    detail = " · ".join(part for part in (format_km(link.distance_km) if link.distance_km is not None
                                          else "", state.lower()) if part)
    return MatrixCell(value, detail, tone_name, link.key, link.style,
                      f"{link.key} · {a.id} ↔ {b.id} · {state} · F {link.fidelity} · "
                      f"{format_km(link.distance_km)}\n{link.processing_detail}")


def _matrix_pairs(model):
    pairs = defaultdict(list)
    for link in model.links:
        if link.style != "pair":
            pairs[frozenset((link.a, link.b))].append(link)
    return pairs


def matrix_grid(model: NetModel) -> list:
    """Explicit export of all cells; the live table evaluates visible cells only."""
    pairs = _matrix_pairs(model)
    return [[_matrix_cell(a, b, pairs.get(frozenset((a.id, b.id)), ()))
             for b in model.nodes] for a in model.nodes]


def matrix_cells(nodes, links):
    """Text rows of the matrix: `[(node id, colour), (value, colour), …]` per node.

    Direct fibres only; "·" = no direct fibre, "—" = the node itself, MULTI = parallel
    fibres, OFF = disabled in the configuration, × = fault.
    """

    distinguish = any(getattr(link, "tone", "") == "green" for link in links)
    model = NetModel(
        nodes=tuple(nodes),
        links=tuple(NetLink(
            key=link_key(link), id=link.id, a=link.a, b=link.b,
            style=link_style(link, tones_distinguish=distinguish), status=getattr(link, "status", "") or "",
            tone=link.tone, fidelity=link.fidelity, qber=link.qber, skr=link.skr, distance_km=link.distance_km,
            configured_active=link.configured_active, label=link.label, kind=link.kind,
            fidelity_value=_fidelity_value(link.fidelity) if link.provenance in ("measured", "simulated") else None)
            for link in links))
    rows = []
    for node, cells in zip(model.nodes, matrix_grid(model)):
        rows.append([(node.id, C.ink)] + [(cell.text, text_tone(cell.tone)) for cell in cells])
    return rows


_SHORT_STATE = {"degraded": "degr.", "compromised": "alarm", "calibrating": "cal.", "disabled": "off",
                "isolated": "isol.", "no input": "no in.", "no_input": "no in.", "enabled": "on",
                "pair path": "pair"}


def short_detail(detail: str) -> str:
    """The cell's second line with its state word abbreviated, for a column too narrow for the
    full word (the tooltip and the inspector keep the full state)."""

    head, separator, state = detail.rpartition(" · ")
    return f"{head}{separator}{_SHORT_STATE.get(state, state)}"


class MatrixModel(QAbstractTableModel):
    """Column 0 is the node; columns 1… are the same nodes."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.net = NetModel()
        self._pairs = {}
        self._cells = OrderedDict()
        self._presented_ids = ()

    @property
    def grid(self) -> list:
        """Compatibility export; ordinary painting never materializes N² cells."""
        return matrix_grid(self.net)

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.net.nodes)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.net.nodes) + 1

    def set_net(self, net: NetModel, *, presentation=True) -> None:
        ids = tuple(node.id for node in net.nodes)
        if not presentation:
            self.net, self._pairs = net, _matrix_pairs(net)
            self._cells.clear()
            return
        same_shape = ids == self._presented_ids
        if same_shape:
            self.net, self._pairs = net, _matrix_pairs(net)
            self._cells.clear()
            if net.nodes:
                self.dataChanged.emit(self.index(0, 0), self.index(len(net.nodes) - 1, len(net.nodes)))
                self.headerDataChanged.emit(Qt.Orientation.Horizontal, 0, len(net.nodes))
            return
        self.beginResetModel()
        self.net, self._pairs = net, _matrix_pairs(net)
        self._cells.clear()
        self.endResetModel()
        self._presented_ids = ids

    def cell(self, index) -> MatrixCell | None:
        if (not index.isValid() or not 0 <= index.row() < len(self.net.nodes)
                or not 1 <= index.column() <= len(self.net.nodes)):
            return None
        key = (index.row(), index.column()-1)
        cell = self._cells.get(key)
        if cell is None:
            a, b = self.net.nodes[key[0]], self.net.nodes[key[1]]
            cell = _matrix_cell(a, b, self._pairs.get(frozenset((a.id, b.id)), ()))
            if len(self._cells) >= 2048:
                self._cells.popitem(last=False)
            self._cells[key] = cell
        else:
            self._cells.move_to_end(key)
        return cell

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or index.row() >= len(self.net.nodes):
            return None
        node = self.net.nodes[index.row()]
        if index.column() == 0:
            if role == Qt.ItemDataRole.DisplayRole:
                return node.id
            if role == Qt.ItemDataRole.ToolTipRole:
                return f"{node.id} · {node.role}" + ("" if node.located else " · no location (awaiting location)")
            return None
        cell = self.cell(index)
        if cell is None:
            return None
        if role == Qt.ItemDataRole.DisplayRole:
            return cell.text
        if role == Qt.ItemDataRole.ToolTipRole:
            return cell.tooltip
        if role == Qt.ItemDataRole.ForegroundRole:
            return qc(text_tone(cell.tone))
        return None

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if orientation != Qt.Orientation.Horizontal:
            return None
        if section == 0:
            return "NODE" if role == Qt.ItemDataRole.DisplayRole else None
        if section - 1 >= len(self.net.nodes):
            return None
        node = self.net.nodes[section - 1]
        if role == Qt.ItemDataRole.DisplayRole:
            return node.id if node.located else f"{node.id} ◌"
        if role == Qt.ItemDataRole.ToolTipRole:
            return node.id + ("" if node.located else " · no location (awaiting location)")
        return None


class _MatrixDelegate(QStyledItemDelegate):
    """Cell = status rule, live fidelity, distance · status; the selected fibre / node is outlined."""

    def paint(self, painter: QPainter, option, index) -> None:
        model: MatrixModel = index.model()
        net = model.net
        rect = QRectF(option.rect)
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        node = net.nodes[index.row()]
        if index.column() == 0:
            selected = net.selected("node", node.id)
            if selected:
                painter.fillRect(rect, qc(mix(C.sel)))
            painter.setOpacity(0.4 if node.isolated else 1.0)
            centre = rect.left() + 16
            paint_marker(painter, QPointF(centre, rect.center().y()), marker_kind(node), node.health,
                         calibrating=node.status == "calibrating")
            painter.setOpacity(1.0)
            painter.setFont(mono(12))
            painter.setPen(qc(C.ink))
            label = QRectF(rect.left() + 32, rect.top(), rect.width() - 36, rect.height())
            if not node.located:
                painter.drawText(label.adjusted(0, 6, 0, -rect.height() / 2 + 2),
                                 Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                                 painter.fontMetrics().elidedText(node.id, Qt.TextElideMode.ElideRight,
                                                                  int(label.width())))
                painter.setFont(mono(11))
                painter.setPen(qc(C.ink3))
                painter.drawText(label.adjusted(0, rect.height() / 2 - 2, 0, -5),
                                 Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, "no location")
            else:
                painter.drawText(label, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                                 painter.fontMetrics().elidedText(node.id, Qt.TextElideMode.ElideRight,
                                                                  int(label.width())))
        else:
            cell = model.grid[index.row()][index.column() - 1]
            other = net.nodes[index.column() - 1]
            selected = bool(cell.link) and net.selected("link", cell.link)
            in_cross = net.selection[0] == "node" and net.selection[1] in (node.id, other.id)
            if selected:
                painter.fillRect(rect, qc(mix(C.sel)))
            elif in_cross and cell.link:
                painter.fillRect(rect, qc(mix(C.hover)))
            if cell.link:
                painter.fillRect(QRectF(rect.left() + 1, rect.top() + 6, 3, rect.height() - 12),
                                 qc(text_tone(cell.tone)))
                painter.setFont(mono(12.5))
                painter.setPen(qc(text_tone(cell.tone) if cell.tone in ("dim", "muted", "red", "violet") else C.ink))
                painter.drawText(QRectF(rect.left() + 10, rect.top() + 4, rect.width() - 12, rect.height() / 2),
                                 Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, cell.text)
                painter.setFont(text_font(11))
                painter.setPen(qc(text_tone(cell.tone)))
                detail = cell.detail
                if painter.fontMetrics().horizontalAdvance(detail) > rect.width() - 12:
                    detail = short_detail(detail)        # "14.3 km · degraded" -> "14.3 km · degr."
                detail = painter.fontMetrics().elidedText(detail, Qt.TextElideMode.ElideRight,
                                                          int(rect.width() - 12))
                painter.drawText(QRectF(rect.left() + 10, rect.center().y() - 1, rect.width() - 12,
                                        rect.height() / 2 - 3),
                                 Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, detail)
            else:
                painter.setFont(mono(12.5))
                painter.setPen(qc(C.ink4 if cell.text in ("·", "—") else text_tone(cell.tone)))
                painter.drawText(rect.adjusted(10, 0, 0, 0),
                                 Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, cell.text)
            if selected:
                painter.setPen(QPen(qc(C.acc), 2))
                painter.drawRect(rect.adjusted(1, 1, -1, -1))
        painter.setPen(QPen(qc(C.soft), 1))
        painter.drawLine(rect.bottomLeft(), rect.bottomRight())
        painter.restore()


class MatrixView(QWidget):
    """The same nodes as rows and columns; a cell is the direct fibre between the pair."""

    node_selected = Signal(str)
    link_selected = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        lay = vbox(self, (0, 0, 0, 0), 0)
        self.table = QTableView()
        self.records = MatrixModel(self.table)
        self.table.setModel(self.records)
        self.table.setItemDelegate(_MatrixDelegate(self.table))
        header = self.table.horizontalHeader()
        header.setFont(label_font(11))
        header.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        header.setHighlightSections(False)
        header.setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
        header.setSectionsClickable(True)
        header.sectionClicked.connect(self._header_clicked)
        self.table.verticalHeader().hide()
        self.table.verticalHeader().setDefaultSectionSize(40)
        self.table.setShowGrid(False)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.table.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.table.setMouseTracking(True)
        self.table.setCursor(Qt.CursorShape.PointingHandCursor)
        self.table.setStyleSheet(f"""
            QTableView {{ background:transparent; border:0; }}
            QHeaderView::section {{ background:{C.bg}; color:{C.ink3}; border:0;
                border-bottom:1px solid {C.line}; padding:6px 8px;
                font-family:'{family("mono")}'; font-size:11px; }}
            QHeaderView::section:hover {{ color:{C.ink}; }}
        """)
        self.table.clicked.connect(self._cell_clicked)
        self.table.activated.connect(self._cell_clicked)       # Enter on the current cell
        lay.addWidget(self.table, 1)

    # the same model as the other views
    def set_model(self, net: NetModel) -> None:
        self.records.set_net(net)
        self._fit_columns()
        self.table.viewport().update()

    def set_model_state(self, net: NetModel) -> None:
        """Keep hidden inspection data fresh; defer Qt signals/layout to activation."""
        self.records.set_net(net, presentation=False)

    def _fit_columns(self) -> None:
        """Share the width between the node columns; scroll sideways below 84 px per column."""

        count = len(self.records.net.nodes)
        if not count:
            return
        width = max(84, min(140, (self.table.viewport().width() - FIRST_COLUMN_W - 2) // count))
        for column in range(count + 1):
            wanted = FIRST_COLUMN_W if column == 0 else width
            if self.table.columnWidth(column) != wanted:
                self.table.setColumnWidth(column, wanted)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._fit_columns()

    def set_snapshot(self, snap) -> None:
        """Older entry point: a model without selection."""

        self.set_model(build_net_model(snap))

    @property
    def net(self) -> NetModel:
        return self.records.net

    def node_ids(self) -> list:
        return [node.id for node in self.records.net.nodes]

    def link_keys(self) -> set:
        ids = {node.id for node in self.records.net.nodes}
        return {entries[0].key for pair, entries in self.records._pairs.items()
                if len(pair) == 2 and pair <= ids and len(entries) == 1}

    def cell_for(self, a: str, b: str) -> MatrixCell | None:
        ids = self.node_ids()
        if a not in ids or b not in ids:
            return None
        return self.records.cell(self.records.index(ids.index(a), ids.index(b) + 1))

    def index_for(self, a: str, b: str):
        ids = self.node_ids()
        return self.records.index(ids.index(a), ids.index(b) + 1)

    def _cell_clicked(self, index) -> None:
        if index.column() == 0:
            self.node_selected.emit(self.records.net.nodes[index.row()].id)
            return
        cell = self.records.cell(index)
        if cell is not None and cell.link:
            self.link_selected.emit(cell.link)

    def _header_clicked(self, section: int) -> None:
        if 1 <= section <= len(self.records.net.nodes):
            self.node_selected.emit(self.records.net.nodes[section - 1].id)


def __getattr__(name: str):
    # `GeoView` moved to `geo_map.GeoMap`; the old name stays importable.
    if name == "GeoView":
        from .geo_map import GeoMap
        return GeoMap
    raise AttributeError(name)


__all__ = ["MatrixCell", "MatrixModel", "MatrixView", "NetLink", "NetModel", "NetNode", "STATUS_SHORT",
           "STYLE_TONE", "build_net_model", "format_km", "format_lat", "format_lon", "matrix_cells",
           "matrix_grid", "valid_coordinates"]
