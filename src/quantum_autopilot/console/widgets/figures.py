"""Painted figures: the network drawing primitives, the logical graph and the chart types.

The drawing primitives (`link_style`, `link_pen`, `paint_link`, `paint_marker`,
`paint_node`) share role-specific symbols and fibre paths. The GEO map,
the logical graph, the unplaced-node queue and the node window all draw with
them, so a state looks the same wherever it appears.
"""
from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFontMetricsF, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QWidget

from ..theme import C, mono, qc, tone

VIEW_W, VIEW_H = 740.0, 560.0

# ------------------------------------------------------------- primitives
#: Engine link status -> drawing style.
STATUS_STYLE = {"up": "calm", "degraded": "warn", "down": "bad", "disabled": "off", "isolated": "off",
                "calibrating": "cal", "compromised": "compromised", "no_input": "calm"}
#: style -> (colour token, width, dash pattern in pen-width units or None). Prototype paths:
#: calm 1.6 · warn 2.4 accent · bad 2 red "6 6" · iso 1.6 ink4 "2 5"; the others extend them.
LINK_STYLES = {
    "calm": ("calm", 1.6, None),
    "warn": ("acc", 2.4, None),
    "bad": ("red", 2.0, (6.0, 6.0)),
    "off": ("ink4", 1.6, (2.0, 5.0)),
    "cal": ("violet", 1.6, (5.0, 4.0)),
    "compromised": ("red", 2.6, (9.0, 3.0, 2.0, 3.0)),
    "pair": ("violet", 1.2, (2.0, 5.0)),
    "space": ("violet", 1.0, None),
    "space_config": ("ink4", .7, (1.0, 4.0)),
    "orbit": ("violet", 1.1, (5.0, 5.0)),
}
HEALTH_FILL = {"up": "green", "degraded": "acc", "down": "red", "unknown": "ink4"}
MID_SIZE = 12.0


def link_key(link) -> str:
    """Identity of a link for selection: its id, or its endpoints when the source has none."""

    return getattr(link, "id", "") or f"{link.a}↔{link.b}"


def link_style(link, *, tones_distinguish: bool = True) -> str:
    """Drawing style of a contract `Link` (or anything with the same fields)."""

    status = getattr(link, "status", "") or ""
    if getattr(link, "configured_active", None) is False:
        # Disabled in the persisted configuration. The engine's status follows at its next
        # tick; until then the fibre is already out of service and is drawn so.
        return "off"
    if status:
        return STATUS_STYLE.get(status, "calm")
    kind = getattr(link, "kind", "fibre")
    if kind == "satellite":
        return "pair"
    name = getattr(link, "tone", "ink")
    if kind == "fault" or name == "red":
        return "bad"
    return "warn" if name == "amber" and tones_distinguish else "calm"


def link_pen(style: str, *, selected: bool = False) -> QPen:
    name, width, dash = LINK_STYLES.get(style, LINK_STYLES["calm"])
    pen = QPen(qc(getattr(C, name)), width+1 if selected else width)
    pen.setCapStyle(Qt.PenCapStyle.FlatCap)
    if dash:
        pen.setDashPattern([value / pen.widthF() for value in dash])
    return pen


def paint_link(p: QPainter, a: QPointF, b: QPointF, style: str, selected: bool = False) -> None:
    p.setPen(link_pen(style))
    p.drawLine(a, b)
    if selected:                      # `paths.sel`: ink, 4 px, 55 % opacity
        colour = qc(C.ink)
        colour.setAlphaF(0.55)
        p.setPen(QPen(colour, 4.0))
        p.drawLine(a, b)


def paint_route(p: QPainter, points) -> None:
    """The end-to-end pair route: violet, 5 px, round caps, "2 7" dashes."""

    if len(points) < 2:
        return
    pen = QPen(qc(C.violet), 5.0)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setDashPattern([2.0 / 5.0, 7.0 / 5.0])
    p.setPen(pen)
    for a, b in zip(points, points[1:]):
        p.drawLine(a, b)


def mid_rect(a: QPointF, b: QPointF) -> QRectF:
    cx, cy = (a.x() + b.x()) / 2.0, (a.y() + b.y()) / 2.0
    return QRectF(cx - MID_SIZE / 2, cy - MID_SIZE / 2, MID_SIZE, MID_SIZE)


def paint_mid(p: QPainter, rect: QRectF, selected: bool = False, hover: bool = False) -> None:
    """Legacy entry point; fibres no longer have a midpoint decoration."""


def marker_kind(node) -> str:
    """Use the published physical type and network role, never an ID or name."""
    kind = getattr(node, 'kind', 'node')
    physical = getattr(node, 'physical_type', '')
    if physical == 'satellite' or kind == 'satellite':
        return 'satellite'
    role = getattr(node, 'network_role', '') or ('core' if kind=='core' else getattr(node, 'role', '').split(' · ')[0])
    role = role.strip().lower()
    if role in ('endpoint', 'core', 'repeater'):
        return role
    if physical == 'quantum_repeater':
        return 'repeater'
    if role in ('measurement', 'ground_station', 'service', 'intermediate'):
        return 'node'
    return kind if kind in ('core', 'repeater') else 'node'


def marker_side(kind: str) -> float:
    return {'endpoint': 24., 'core': 20., 'repeater': 7., 'satellite': 22.}.get(kind, 14.)


def marker_hit_rect(centre: QPointF, node) -> QRectF:
    """A compact symbol retains a usable click target independent of zoom."""
    side = max(24., marker_side(marker_kind(node)) + 8.)
    return QRectF(centre.x()-side/2, centre.y()-side/2, side, side)


def paint_marker(p: QPainter, centre: QPointF, kind: str = "node", health: str = "up", *,
                 selected: bool = False, calibrating: bool = False) -> QRectF:
    """Endpoint port, node hub, discreet repeater and physical satellite glyphs."""

    side = marker_side(kind)
    rect = QRectF(centre.x() - side / 2, centre.y() - side / 2, side, side)
    p.save(); p.setRenderHint(QPainter.RenderHint.Antialiasing)
    colour = qc(getattr(C, HEALTH_FILL.get(health, 'ink4')))
    p.setBrush(qc(C.bg))
    if kind == 'satellite':
        p.setPen(QPen(qc(C.acc if health!='down' else C.red),1.5))
        p.setBrush(qc(C.bg));p.drawRect(rect.adjusted(3,3,-3,-3))
        p.drawLine(centre-QPointF(11,0),centre+QPointF(11,0))
        p.drawLine(centre-QPointF(9,4),centre-QPointF(9,-4));p.drawLine(centre+QPointF(9,4),centre+QPointF(9,-4))
    elif kind == 'endpoint':
        p.setPen(QPen(colour, 2.0));p.drawEllipse(rect)
        ring = QColor(colour);ring.setAlphaF(.5)
        p.setPen(QPen(ring, .8));p.drawEllipse(rect.adjusted(3,3,-3,-3))
        p.setPen(QPen(qc(C.ink), 1.6))
        # Opposite arrows identify arrival and departure at the network port.
        for direction, offset in ((1,-2.5),(-1,2.5)):
            a=centre+QPointF(-direction*5.5,offset);b=centre+QPointF(direction*5.5,offset)
            p.drawLine(a,b);p.drawLine(b,b+QPointF(-direction*2.5,-2.5));p.drawLine(b,b+QPointF(-direction*2.5,2.5))
    elif kind == 'repeater':
        if health == 'up':colour=qc(C.ink2)
        p.setPen(QPen(colour,1.2))
        path=QPainterPath();path.moveTo(centre+QPointF(0,-3.5))
        for offset in (QPointF(3.5,0),QPointF(0,3.5),QPointF(-3.5,0)):path.lineTo(centre+offset)
        path.closeSubpath();p.drawPath(path)
        p.setPen(Qt.PenStyle.NoPen);p.setBrush(colour);p.drawEllipse(centre,.8,.8)
    else:
        path=QPainterPath()
        for index in range(6):
            angle=-math.pi/2+index*math.pi/3
            point=centre+QPointF(math.cos(angle)*side/2,math.sin(angle)*side/2)
            if index:path.lineTo(point)
            else:path.moveTo(point)
        path.closeSubpath();p.setPen(QPen(colour,1.7 if kind=='core' else 1.4));p.drawPath(path)
        if kind=='core':
            p.setPen(QPen(colour,1.2))
            for angle in (-math.pi/2,math.pi/6,5*math.pi/6):
                port=centre+QPointF(math.cos(angle)*5,math.sin(angle)*5);p.drawLine(centre,port)
                p.setBrush(colour);p.drawEllipse(port,1.4,1.4)
        p.setPen(Qt.PenStyle.NoPen);p.setBrush(colour);p.drawEllipse(centre,1.8,1.8)
    if calibrating:
        pen = QPen(qc(C.violet), 1.5)
        pen.setDashPattern([2.0, 2.0])
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(rect.adjusted(-3.5, -3.5, 3.5, 3.5))
    if selected:                      # outline 2 px accent, offset 5 px
        p.setPen(QPen(qc(C.acc), 2))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(rect.adjusted(-5, -5, 5, 5))
    p.restore()
    return rect


def node_label_font():
    return mono(12)


LABEL_SIDES = ("right", "left", "below", "above")


def _node_boxes(centre: QPointF, node, badge: str, max_label: float | None, side: str) -> tuple:
    """`(marker hit box, label box, badge box | None, shown text)` of a node for a label side.

    "right" is the prototype's `.gnode` (marker, 7 px gap, label, optional badge). The other
    sides exist for the map, where two nodes can be closer than a label is wide: the same
    boxes are placed left of, below or above the marker.
    """

    marker = marker_side(marker_kind(node))
    font = node_label_font()
    metrics = QFontMetricsF(font)
    available = metrics.horizontalAdvance(node.id) + 6
    if max_label is not None:
        available = max(12.0, min(available, max_label))
    text = metrics.elidedText(node.id, Qt.TextElideMode.ElideRight, max(8.0, available - 6 + 0.5))
    label_w = metrics.horizontalAdvance(text) + 6
    badge_w = QFontMetricsF(mono(10)).horizontalAdvance(badge) + 12 if badge else 0.0
    half = marker / 2
    square = marker_hit_rect(centre, node)
    if square.height()<26:square.adjust(0,-1,0,1)
    if side == "left":
        label = QRectF(centre.x() - half - 7 - label_w, centre.y() - 9, label_w, 18)
        chip = QRectF(label.left() - 5 - badge_w, centre.y() - 8, badge_w, 16) if badge else None
    elif side in ("below", "above"):
        total = label_w + (5 + badge_w if badge else 0.0)
        top = centre.y() + half + 5 if side == "below" else centre.y() - half - 5 - 18
        label = QRectF(centre.x() - total / 2, top, label_w, 18)
        chip = QRectF(label.right() + 5, top + 1, badge_w, 16) if badge else None
    else:
        label = QRectF(centre.x() + half + 7, centre.y() - 9, label_w, 18)
        chip = QRectF(label.right() + 5, centre.y() - 8, badge_w, 16) if badge else None
    return square, label, chip, text


def node_rect(centre: QPointF, node, *, badge: str = "", max_label: float | None = None,
              side: str = "right") -> QRectF:
    """Hit and layout rectangle of a `.gnode`: marker, label and optional badge."""

    square, label, chip, _text = _node_boxes(centre, node, badge, max_label, side)
    rect = square.united(label.adjusted(0, -4, 4, 4))
    return rect.united(chip.adjusted(0, -5, 4, 5)) if chip is not None else rect


def paint_node(p: QPainter, centre: QPointF, node, *, selected: bool = False, badge: str = "",
               max_label: float | None = None, side: str = "right") -> QRectF:
    """Draw one node as the prototype's `.gnode`; returns its hit rectangle."""

    kind = marker_kind(node)
    status = getattr(node, "status", "") or ""
    isolated = bool(getattr(node, "isolated", False)) or status == "isolated"
    _square, label, chip, text = _node_boxes(centre, node, badge, max_label, side)
    rect = node_rect(centre, node, badge=badge, max_label=max_label, side=side)
    p.save()
    if isolated:
        p.setOpacity(p.opacity() * 0.4)
    paint_marker(p, centre, kind, getattr(node, "health", "up"), selected=selected,
                 calibrating=status == "calibrating")
    ground = qc(C.bg)
    ground.setAlphaF(0.86)
    p.fillRect(label, ground)                       # `.nl`: page ground behind the id
    p.setFont(node_label_font())
    p.setPen(qc(C.ink))
    p.drawText(label, Qt.AlignmentFlag.AlignCenter, text)
    p.restore()
    if chip is not None:                             # `.chip t-sat c-sat`, 10 px
        p.fillRect(chip, qc(C.bg))
        p.setPen(QPen(qc(C.violet), 1))
        p.drawRect(chip.adjusted(0.5, 0.5, -0.5, -0.5))
        p.setFont(mono(10))
        p.setPen(qc(C.violet_t))
        p.drawText(chip, Qt.AlignmentFlag.AlignCenter, badge)
    return rect


def place_node_labels(items, bounds: QRectF | None = None) -> dict:
    """Choose a label side per node so that labels do not cover other nodes or labels.

    `items` is `[(node id, centre, node, badge)]` in drawing order. Greedy and deterministic:
    every marker is reserved first; then each node takes the first side of `LABEL_SIDES`
    whose label is inside `bounds` and free. A node with no free side keeps "right".
    Presentation only — it moves text, never a node.
    """

    items = list(items)
    markers = {ident: _node_boxes(centre, node, badge, None, "right")[0].adjusted(2, 4, -2, -4)
               for ident, centre, node, badge in items}
    taken: list = []
    sides: dict = {}
    for ident, centre, node, badge in items:
        chosen = None
        for side in LABEL_SIDES:
            _square, label, chip, _text = _node_boxes(centre, node, badge, None, side)
            box = label.united(chip) if chip is not None else label
            if bounds is not None and not bounds.contains(box):
                continue
            if any(box.intersects(other) for name, other in markers.items() if name != ident):
                continue
            if any(box.intersects(other) for other in taken):
                continue
            chosen = (side, box)
            break
        if chosen is None:
            _square, label, chip, _text = _node_boxes(centre, node, badge, None, "right")
            chosen = ("right", label.united(chip) if chip is not None else label)
        sides[ident] = chosen[0]
        taken.append(chosen[1].adjusted(-2, -1, 2, 1))
    return sides


def segment_distance(point: QPointF, a: QPointF, b: QPointF) -> float:
    """Distance in pixels from `point` to the segment a-b."""

    dx, dy = b.x() - a.x(), b.y() - a.y()
    length = dx * dx + dy * dy
    if length <= 1e-9:
        return math.hypot(point.x() - a.x(), point.y() - a.y())
    t = max(0.0, min(1.0, ((point.x() - a.x()) * dx + (point.y() - a.y()) * dy) / length))
    return math.hypot(point.x() - (a.x() + t * dx), point.y() - (a.y() + t * dy))


class TopologyGraph(QWidget):
    """Logical graph drawn solely from the active provider snapshot (uses logical x/y)."""

    node_selected = Signal(str)
    link_selected = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMouseTracking(True)
        self.nodes, self.links = [], []
        self.all_links: list = []
        self.selection: tuple = (None, "")
        self.route: tuple = ()
        self.badges: dict = {}
        self.layer = "physical"
        self._styles: dict = {}

    def set_topology(self, nodes, links, *, presentation=True) -> None:
        nodes, links = list(nodes), list(links)
        self.nodes, self.links, self.all_links = [], [], []
        if presentation:
            self.update()
        ids = [node.id for node in nodes]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate topology node IDs")
        if any(not math.isfinite(v) or not 0 <= v <= 1 for node in nodes for v in (node.x, node.y)):
            raise ValueError("node coordinates must be finite normalized values")
        known_ids = set(ids)
        if any(link.a not in known_ids or link.b not in known_ids for link in links):
            raise ValueError("link refers to an unknown node")
        self.nodes = nodes
        self.all_links = links
        # `links` keeps its old meaning: the fibres enabled in the configuration.
        self.links = [link for link in links if getattr(link, "configured_active", None) is not False]
        distinguish = any(getattr(link, "tone", "") == "green" for link in links)
        self._styles = {link_key(link): getattr(link, "style", None) or link_style(link, tones_distinguish=distinguish)
                        for link in links}
        if presentation:
            columns = math.ceil(math.sqrt(len(nodes))) if nodes else 1
            self.setMinimumWidth(190 + columns * 60 if len(nodes) > 8 else 0)
            self.setMinimumHeight(130 + math.ceil(len(nodes) / columns) * 70 if len(nodes) > 8 else 0)

    def set_overlay(self, selection=(None, ""), route=(), badges=None, layer: str = "physical", *, presentation=True) -> None:
        """Selection, the selected demand's route and the END/BSM badges (view state)."""

        state = (tuple(selection), tuple(route), dict(badges or {}), layer)
        if state != (self.selection, self.route, self.badges, self.layer):
            self.selection, self.route, self.badges, self.layer = state
            if presentation:
                self.update()

    @property
    def _boxes(self) -> list:
        """`[(hit rectangle, node id)]` for the current nodes and size (no paint needed)."""

        return [(self._node_rect(node), node.id) for node in self.nodes]

    def _position(self, node):
        return QPointF(95 + node.x * max(1, self.width() - 190),
                       65 + node.y * max(1, self.height() - 130))

    def _max_label(self):
        if len(self.nodes) > 8:
            columns = math.ceil(math.sqrt(len(self.nodes)))
            return max(12.0, (self.width() - 190) / columns - 12 - 34)
        return 150.0

    def _node_rect(self, node):
        if marker_kind(node)=='repeater' and self.selection!=('node',node.id) and not self.badges.get(node.id):
            return marker_hit_rect(self._position(node),node)
        return node_rect(self._position(node), node, badge=self.badges.get(node.id, ""),
                         max_label=self._max_label(),side='below' if marker_kind(node)=='repeater' else 'right')

    def _link_at(self, point):
        positions = {n.id: self._position(n) for n in self.nodes}
        best = None
        for link in self.all_links:
            if getattr(link, "kind", "") == "satellite":
                continue
            a, b = positions[link.a], positions[link.b]
            distance = segment_distance(point, a, b)
            if distance <= 5 and (best is None or distance < best[0]):
                best = (distance, link_key(link))
        return best[1] if best else None

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        positions = {n.id: self._position(n) for n in self.nodes}
        entanglement = self.layer == "entanglement"
        kind, ident = self.selection
        p.setOpacity(0.35 if entanglement else 1.0)
        for link in self.all_links:
            style = self._styles.get(link_key(link), "calm")
            if style == "pair" and not entanglement:
                continue
            paint_link(p, positions[link.a], positions[link.b], style,
                       selected=kind == "link" and ident == link_key(link))
        p.setOpacity(1.0)
        if entanglement:
            paint_route(p, [positions[name] for name in self.route if name in positions])
        limit = self._max_label()
        for node in sorted(self.nodes,key=lambda n:(self.selection==('node',n.id),marker_kind(n) in ('endpoint','core'))):
            selected=kind=='node' and ident==node.id
            if marker_kind(node)=='repeater' and not selected and not self.badges.get(node.id):
                p.save()
                if getattr(node,'isolated',False):p.setOpacity(.4)
                paint_marker(p,positions[node.id],'repeater',node.health,calibrating=getattr(node,'status','')=='calibrating')
                p.restore()
            else:
                paint_node(p, positions[node.id], node, selected=selected,
                           badge=self.badges.get(node.id, ""), max_label=limit,
                           side='below' if marker_kind(node)=='repeater' else 'right')
        if not self.nodes:
            p.setFont(mono(11))
            p.setPen(qc(C.muted))
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "TOPOLOGY UNAVAILABLE")
        p.end()

    def mousePressEvent(self, event) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            return super().mousePressEvent(event)
        # Input can arrive after a new snapshot, before its paint event.
        # Hit testing uses the current graph rather than a stale paint cache.
        for node in self.nodes:
            if self._node_rect(node).contains(event.position()):
                self.node_selected.emit(node.id)
                return
        link = self._link_at(event.position())
        if link is not None:
            self.link_selected.emit(link)
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        node = next((node for node in self.nodes if self._node_rect(node).contains(event.position())), None)
        if node is not None:
            self.setToolTip(f"{node.id} · {node.role} · {node.health}")
        else:
            link = self._link_at(event.position())
            self.setToolTip(link or "Logical topology · click a node or a fibre to inspect")
        super().mouseMoveEvent(event)


class Sparkline(QWidget):
    """A small trend line over values the source delivered; gaps (None) break the line."""

    def __init__(self, parent=None, *, tone_name: str = "amber") -> None:
        super().__init__(parent)
        self.values: tuple = ()
        self.tone_name = tone_name
        self.setFixedSize(150, 40)

    def set_values(self, values) -> None:
        values = tuple(values)
        if values != self.values:
            self.values = values
            self.update()

    def paintEvent(self, _event) -> None:
        present = [value for value in self.values if value is not None]
        if len(present) < 2:
            return
        low, high = min(present), max(present)
        span = max(high - low, 1e-9)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(QPen(qc(tone(self.tone_name)), 2))
        w, h = self.width(), self.height()
        step = w / max(1, len(self.values) - 1)
        path, pen_down = QPainterPath(), False
        for i, value in enumerate(self.values):
            if value is None:
                pen_down = False
                continue
            point = QPointF(i * step, 3 + (h - 6) * (1 - (value - low) / span))
            path.lineTo(point) if pen_down else path.moveTo(point)
            pen_down = True
        p.drawPath(path)
        p.end()


class LineChart(QWidget):
    """Fidelity per link: four gridlines, a dashed threshold, N polylines."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.series: list[tuple[list[float], str, float, bool]] = []
        self.y_min, self.y_max = 0.86, 1.0
        self.threshold: float | None = 0.900
        self.x_labels: list[str] = []

    def set_chart(self, series, y_min: float, y_max: float,
                  threshold: float | None, x_labels: list[str]) -> None:
        self.series = series
        self.y_min, self.y_max = y_min, max(y_max, y_min + 1e-6)
        self.threshold = threshold
        self.x_labels = x_labels
        self.update()

    def _y(self, value: float, plot_h: float) -> float:
        span = self.y_max - self.y_min
        return plot_h - (value - self.y_min) / span * plot_h

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        plot_h = h - 22

        p.setPen(QPen(qc(C.line2), 1))
        for i in range(1, 5):
            y = plot_h * i / 5
            p.drawLine(0, int(y), w, int(y))

        if self.threshold is not None and self.y_min < self.threshold < self.y_max:
            pen = QPen(qc(C.red), 1)
            pen.setDashPattern([8, 6])
            p.setOpacity(0.65)
            p.setPen(pen)
            y = int(self._y(self.threshold, plot_h))
            p.drawLine(0, y, w, y)
            p.setOpacity(1.0)

        for points, colour, width, dashed in self.series:
            if len(points) < 2:
                continue
            pen = QPen(qc(colour), width)
            if dashed:
                pen.setDashPattern([5, 4])
            p.setPen(pen)
            path = QPainterPath()
            step = w / (len(points) - 1)
            for i, value in enumerate(points):
                point = QPointF(i * step, self._y(value, plot_h))
                path.moveTo(point) if i == 0 else path.lineTo(point)
            p.drawPath(path)

        if self.x_labels:
            p.setFont(mono(9))
            p.setPen(qc(C.dim))
            step = w / max(1, len(self.x_labels) - 1)
            for i, label in enumerate(self.x_labels):
                x = i * step
                rect = QRectF(x - 30, plot_h + 4, 60, 16)
                align = Qt.AlignmentFlag.AlignCenter
                if i == 0:
                    rect = QRectF(0, plot_h + 4, 60, 16)
                    align = Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
                elif i == len(self.x_labels) - 1:
                    rect = QRectF(w - 60, plot_h + 4, 60, 16)
                    align = Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
                p.drawText(rect, align, label)
        p.end()


class BarChart(QWidget):
    """Swapping throughput: fixed-width bars, tone per bar."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.bars: list[tuple[float, str]] = []

    def set_bars(self, bars: list[tuple[float, str]]) -> None:
        self.bars = bars
        self.update()

    def paintEvent(self, _event) -> None:
        if not self.bars:
            return
        p = QPainter(self)
        w, h = self.width(), self.height()
        span = w / len(self.bars)
        bar_w = span * 0.70
        for i, (value, name) in enumerate(self.bars):
            height = max(2.0, min(1.0, value) * h)
            colour = qc(tone(name))
            colour.setAlphaF(0.88)
            p.fillRect(
                QRectF(i * span, h - height, bar_w, height), colour
            )
        p.end()


class Quorum(QWidget):
    """The three control-plane quorum columns on the boot screen."""

    def __init__(self, values: list[tuple[str, float, str]], parent=None) -> None:
        super().__init__(parent)
        self.values = values
        self.setFixedHeight(94)

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        w, h = self.width(), self.height()
        span = (w - 24) / max(1, len(self.values))
        for i, (_label, fraction, name) in enumerate(self.values):
            x = i * (span + 12)
            p.setPen(QPen(qc(C.line), 1))
            p.drawRect(QRectF(x, 0, span, h))
            fill = (h - 4) * max(0.0, min(1.0, fraction))
            p.fillRect(QRectF(x + 2, h - 2 - fill, span - 4, fill), qc(tone(name)))
        p.end()


__all__ = [
    "BarChart", "HEALTH_FILL", "LINK_STYLES", "LineChart", "MID_SIZE", "Quorum", "STATUS_STYLE", "Sparkline",
    "TopologyGraph", "link_key", "link_pen", "link_style", "marker_kind", "marker_side", "marker_hit_rect", "mid_rect", "node_rect", "paint_link",
    "paint_marker", "paint_mid", "paint_node", "paint_route", "place_node_labels", "segment_distance",
]
