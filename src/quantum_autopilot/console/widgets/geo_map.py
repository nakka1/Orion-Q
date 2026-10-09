"""GEO view: the network on offline national and Salvador vector maps.

One scale factor maps projected metres to pixels on both axes (`geodata.ViewTransform`).
The local projection is approximate over continental extents; backend link lengths
remain WGS84 geodesics. Campus focus recentres the projection for local inspection.
The scale bar is read from the display transform. Node positions are the operator-declared
coordinates of the snapshot; a node without coordinates is not drawn here (it is
in the queue below the map). Connections with an unlocated endpoint remain in
the node details and logical views, without a geographic line.

The base map (water, land, municipality outline, roads, labels, scale bar, north
mark, attribution) is static for a given size, view and theme: it is rendered once
into a pixmap and reused by the 1 Hz refresh. Wheel zooms about the cursor, drag
pans, "Reset view" returns to the default view (the placed nodes with a margin, or
the municipality when nothing is placed).
"""
from __future__ import annotations

from dataclasses import replace

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QFontMetricsF, QPainter, QPainterPath, QPen, QPixmap, QTransform
from PySide6.QtWidgets import QComboBox, QToolTip, QWidget

from ..geodata import BBox, BaseMap, LocalProjection, ViewTransform, load_brazil, load_salvador, load_world, load_campus
from ..theme import C, button_font, current_theme, label_font, mix, mono, qc, text as text_font
from .figures import (
    marker_hit_rect, marker_kind, node_rect, paint_link, paint_node, paint_route, place_node_labels, segment_distance,
)
from .network_views import NetModel, build_net_model, format_km, format_lat, format_lon
from .prims import Clickable, Frame, L, Label, clear_layout, hbox, vbox
from .figures import paint_marker
from .signal_geography import map_cross_rect, paint_signal_cross, signal_map_ocean

ROAD_WIDTH = {"motorway": 3.0, "trunk": 2.4, "primary": 1.5, "secondary": 1.0}
MIN_M_PER_PX, MAX_M_PER_PX = 1.5, 100000.0
DEFAULT_MARGIN_PX = 44.0
ALERT_ROW_H = 44
ALERT_ROW_COMPACT_H = 24
MAX_ALERT_ROWS = 3
INFO_BASE_H = 52                     # title + placed line + padding of the info box
PLACE_RANK2_PX_PER_M = 0.05          # neighbourhood labels of rank 2 appear from 20 m/px inwards


class MapButton(Clickable):
    """Small view control on the map (zoom, reset). View state only: no backend operation."""

    def __init__(self, value: str, parent=None, *, width: int | None = None) -> None:
        super().__init__(value, parent)
        self.setFont(button_font(11))
        self.setFixedHeight(26)
        if width:
            self.setFixedWidth(width)
        self.setStyleSheet(
            f"QPushButton{{color:{C.ink};border:1px solid {C.line};background:{C.bg};padding:0 8px;}}"
            f"QPushButton:hover{{background:{mix(C.hover, C.bg)};border-color:{C.acc};}}")


class _AlertRow(Clickable):
    """A demand the backend could not route. The first one shows the backend's reason under its
    headline; further ones are one line (reason in the tooltip and in Pairs) so that the box does
    not grow over the nodes of the map."""

    def __init__(self, title: str, reason: str, parent=None, *, compact: bool = False) -> None:
        super().__init__("", parent)
        self.setStyleSheet(f"QPushButton{{border:0;border-top:1px solid {C.soft};background:transparent;"
                           f"text-align:left;}}QPushButton:hover{{background:{C.hover};}}")
        lay = vbox(self, (0, 5, 0, 3), 1)
        head = L(title, mono(11), C.red_t)
        head.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        lay.addWidget(head)
        if not compact:
            body = L(reason, text_font(12), C.ink2, elide=True)
            body.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
            lay.addWidget(body)
        self.setToolTip(f"{title}\n{reason}")
        self.setFixedHeight(ALERT_ROW_COMPACT_H if compact else ALERT_ROW_H)


class MapRenderer(QWidget):
    node_selected = Signal(str)
    link_selected = Signal(str)
    location_picked = Signal(float, float)       # latitude, longitude
    pick_cancelled = Signal()
    alert_clicked = Signal(str)                  # demand id
    view_changed = Signal()
    zoom_requested = Signal(float, QPointF)

    def __init__(self, parent=None, basemap: BaseMap | None = None, *, chrome=None) -> None:
        super().__init__(parent)
        self._chrome = chrome
        self.signal_mode = False
        self.basemap = basemap or load_salvador()
        self._automatic_basemap = basemap is None
        self.projection = LocalProjection.for_bbox(self.basemap.bbox)
        self.net = NetModel()
        self.available = False
        self._view: tuple | None = None          # (lon, lat, px_per_m) set by the operator; None = default
        self._anchors: dict = {}                 # link key -> x where a queued node's fibre leaves the map
        self._node_anchors: dict = {}            # queued node id -> x of its chip, in map pixels
        self._base: QPixmap | None = None
        self._base_key = None
        # Rasterize complex national outlines cheaply while sizes are changing.
        # One final antialiased render follows the last resize; node geometry,
        # labels, hit tests and backend updates keep using the current transform.
        self._resize_settle = QTimer(self)
        self._resize_settle.setSingleShot(True)
        self._resize_settle.setInterval(100)
        self._resize_settle.timeout.connect(self._finish_resize)
        self._sides: dict = {}
        self._sides_key = None
        self._paths = None
        self._hover: tuple = (None, "")
        self._drag = None
        self._pick = ""
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.ArrowCursor)
        self.setMinimumHeight(200)
        self.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        self.setAccessibleName("Geographic topology with network nodes at their declared reference coordinates")

        self._alerts: tuple = ()
        if chrome is None:
            # overlays: info / alerts (top left), view controls (top right), pick hint (top centre)
            self.info = Frame(bg=C.bg, border=C.line, parent=self)
            box = vbox(self.info, (12, 8, 12, 8), 2)
            self.title = L("Salvador · BA · operator-declared coordinates", label_font(11), C.ink, elide=True)
            box.addWidget(self.title)
            self.placed = L("", text_font(12), C.ink2, elide=True)
            box.addWidget(self.placed)
            self.alert_host = QWidget()
            self.alert_lay = vbox(self.alert_host, (0, 4, 0, 0), 0)
            box.addWidget(self.alert_host)
            self.alert_host.hide()
            self._alerts: tuple = ()
            self.info.move(12, 12)

            self.controls = QWidget(self)
            row = hbox(self.controls, (0, 0, 0, 0), 4)
            self.focus_node = QComboBox()
            self.focus_node.setFixedWidth(152)
            self.focus_node.setAccessibleName("Focus geographic node")
            self.focus_node.setToolTip("Fit the whole network or zoom to a campus. Use this to select nearby nodes such as USP and IME-USP.")
            self.focus_node.addItem("All nodes", "")
            self.focus_node.currentIndexChanged.connect(self._focus_node)
            row.addWidget(self.focus_node)
            self.zoom_in = MapButton("+", width=28)
            self.zoom_out = MapButton("−", width=28)
            self.reset = MapButton("Reset view")
            self.zoom_in.setAccessibleName("Zoom in")
            self.zoom_out.setAccessibleName("Zoom out")
            self.zoom_in.clicked.connect(lambda: self.zoom_by(1.5))
            self.zoom_out.clicked.connect(lambda: self.zoom_by(1 / 1.5))
            self.reset.clicked.connect(self.reset_view)
            for button in (self.zoom_in, self.zoom_out, self.reset):
                row.addWidget(button)

            self.pick_hint = Frame(bg=C.bg, border=C.acc, parent=self)
            hint = hbox(self.pick_hint, (12, 6, 12, 6), 10)
            self.pick_label = L("", text_font(12.5), C.ink)
            hint.addWidget(self.pick_label)
            self.pick_cancel = MapButton("Cancel")
            self.pick_cancel.clicked.connect(self.cancel_pick)
            hint.addWidget(self.pick_cancel)
            self.pick_hint.hide()
        else:
            self.info = chrome.info
            self.title = chrome.title
            self.placed = chrome.placed
            self.alert_host = chrome.alert_host
            self.alert_lay = chrome.alert_lay
            self.controls = chrome.controls
            self.focus_node = chrome.focus_node
            self.zoom_in = chrome.zoom_in
            self.zoom_out = chrome.zoom_out
            self.reset = chrome.reset
            self.pick_hint = chrome.pick_hint
            self.pick_label = chrome.pick_label
            self.pick_cancel = chrome.pick_cancel

    # ------------------------------------------------------------------ data
    def set_signal_mode(self, enabled: bool) -> None:
        """Change scenery only; declared positions and controls are unchanged."""
        enabled = bool(enabled)
        if self.signal_mode == enabled:
            return
        self.signal_mode = enabled
        self._base = self._base_key = None
        self._surface = self._surface_key = None
        self.update()

    def set_model(self, net: NetModel) -> None:
        located = tuple((node.id, node.longitude, node.latitude) for node in net.located if node.physical_type!='satellite')
        previous = tuple((node.id, node.longitude, node.latitude) for node in self.net.located if node.physical_type!='satellite')
        identities=tuple(node.id for node in net.located)
        previous_identities=tuple(node.id for node in self.net.located)
        self.net = net
        self.available = bool(located)
        if located != previous:
            self._base = None                       # labels avoid the nodes; default view follows them
            focused = net.node(self.focus_node.currentData())
            if self._automatic_basemap and (focused is None or focused.physical_type == 'satellite' or not focused.located):
                self._set_basemap(self._overview())
            selected = self.focus_node.currentData()
            if identities != previous_identities and self._chrome is None:
                self.focus_node.blockSignals(True)
                self.focus_node.clear(); self.focus_node.addItem("All nodes", "")
                for node in net.located:
                    self.focus_node.addItem(node.map_label, node.id)
                self.focus_node.setCurrentIndex(max(0, self.focus_node.findData(selected)))
                self.focus_node.blockSignals(False)
        count = len(net.nodes)
        orbital=sum(node.physical_type=='satellite' for node in net.nodes)
        waiting = count - orbital - len(located)
        text = f"{len(located)} instalações localizadas · {orbital} orbitais" + (f" · {waiting} aguardando localização" if waiting else "")
        if not count:
            text = "No nodes in this snapshot"
        if self.placed.text() != text:
            self.placed.setText(text)
            self._place_overlays()
        self.update()

    def _set_basemap(self, basemap):
        if self.basemap is not basemap:
            self.basemap = basemap
            self.projection = LocalProjection.for_bbox(basemap.bbox)
            self._paths = self._base = self._sides_key = None
            self.title.setText(basemap.meta.get('title') or
                               ("Mundo · referências e conexões propostas" if basemap is load_world() else
                                "Brasil · posições de referência dos campi" if basemap is load_brazil() else
                                "Salvador · BA · operator-declared coordinates"))
            self.setAccessibleName("Offline geographic network map; declared campus references")

    def _focus_node(self, index):
        ident = self.focus_node.itemData(index)
        node = self.net.node(ident)
        if node is None:
            self.reset_view()
            return
        if node.physical_type=='satellite':
            self.node_selected.emit(ident);return
        if self._automatic_basemap:
            self._set_basemap(load_campus(ident) or self._overview())
        self.projection = LocalProjection(node.longitude, node.latitude)
        self._paths = self._base = self._sides_key = None
        # 1.5 m/pixel separates nearby campus nodes without moving geographic positions.
        self.set_view_state((node.longitude, node.latitude, 1.0 / MIN_M_PER_PX))
        self.node_selected.emit(ident)

    def shutdown(self):
        self._resize_settle.stop()
        self._resize_settle.timeout.disconnect(self._finish_resize)
        self._base = self._base_key = self._paths = None
        self._surface = self._surface_key = None
        self._sides = {}; self._sides_key = None
        self._drag = None
        self.net = NetModel()

    def _overview(self):
        if any(not load_brazil().bbox.contains(n.longitude,n.latitude) for n in self.net.located):
            return load_world()
        if any(not load_salvador().bbox.contains(n.longitude,n.latitude) for n in self.net.located):
            return load_brazil()
        return load_salvador()

    def set_topology(self, nodes, links) -> None:
        """Older entry point: contract nodes and links without selection."""

        from types import SimpleNamespace
        self.set_model(build_net_model(SimpleNamespace(
            topology=SimpleNamespace(nodes=list(nodes), links=list(links)), live=None)))

    def set_queue_anchors(self, links: dict, nodes: dict | None = None) -> None:
        """Where the fibres of queued nodes leave the map: `{link key: x}` (and `{node id: x}`), map pixels."""

        links = {key: float(value) for key, value in links.items()}
        nodes = {key: float(value) for key, value in (nodes or {}).items()}
        if links != self._anchors or nodes != self._node_anchors:
            self._anchors, self._node_anchors = links, nodes
            self.update()

    def set_alerts(self, alerts) -> None:
        """`[(demand id, headline, backend reason), …]` — demands the backend could not route."""

        alerts = tuple(tuple(item) for item in alerts)
        if alerts == self._alerts:
            return
        self._alerts = alerts
        clear_layout(self.alert_lay)
        height = 4
        for index, (demand_id, headline, reason) in enumerate(alerts[:MAX_ALERT_ROWS]):
            row = _AlertRow(headline, reason, compact=index > 0)
            row.clicked.connect(lambda checked=False, d=demand_id: self.alert_clicked.emit(d))
            self.alert_lay.addWidget(row)
            row.show()
            height += ALERT_ROW_COMPACT_H if index > 0 else ALERT_ROW_H
        if len(alerts) > MAX_ALERT_ROWS:
            more = L(f"+ {len(alerts) - MAX_ALERT_ROWS} more in Pairs", mono(11), C.red_t)
            more.setFixedHeight(20)
            self.alert_lay.addWidget(more)
            more.show()
            height += 20
        self.alert_host.setFixedHeight(height if alerts else 0)
        self.alert_host.setVisible(bool(alerts))
        self._info_height = 0 if not alerts else height
        self._base = None                        # the labels keep clear of the box
        self._place_overlays()

    @property
    def alerts(self) -> tuple:
        return self._alerts

    # ------------------------------------------------------------------ view
    def default_transform(self) -> ViewTransform:
        """Placed nodes with a margin; the municipality view box when nothing is placed."""

        width, height = max(120.0, float(self.width())), max(120.0, float(self.height()))
        nodes = tuple(node for node in self.net.located if node.physical_type!='satellite')
        if len(nodes) >= 2:
            points = [self.projection.to_xy(node.longitude, node.latitude) for node in nodes]
            xs, ys = [p[0] for p in points], [p[1] for p in points]
            pad_x = max(0.14 * (max(xs) - min(xs)), 1500.0)
            pad_y = max(0.14 * (max(ys) - min(ys)), 1500.0)
            west, south = self.projection.to_lonlat(min(xs) - pad_x, min(ys) - pad_y)
            east, north = self.projection.to_lonlat(max(xs) + pad_x, max(ys) + pad_y)
            box = BBox(west, south, east, north)
            margin = min(DEFAULT_MARGIN_PX, width / 4, height / 4)
        elif len(nodes) == 1:
            x, y = self.projection.to_xy(nodes[0].longitude, nodes[0].latitude)
            west, south = self.projection.to_lonlat(x - 6000.0, y - 6000.0)
            east, north = self.projection.to_lonlat(x + 6000.0, y + 6000.0)
            box, margin = BBox(west, south, east, north), 0.0
        else:
            box, margin = self.basemap.bbox, min(10.0, width / 4, height / 4)
        return self.projection.fit(box, width, height, margin)

    def transform(self) -> ViewTransform:
        if self._view is None:
            return self.default_transform()
        lon, lat, px_per_m = self._view
        width, height = max(1.0, float(self.width())), max(1.0, float(self.height()))
        x, y = self.projection.to_xy(lon, lat)
        return ViewTransform(self.projection, px_per_m, width / 2 - x * px_per_m, height / 2 + y * px_per_m,
                             width, height)

    @property
    def is_default_view(self) -> bool:
        return self._view is None

    @property
    def metres_per_px(self) -> float:
        return self.transform().metres_per_px

    def scale_bar(self) -> tuple:
        """`(length_km, length_px)` of the scale bar drawn on the map."""

        return self.transform().scale_bar(120.0)

    def view_state(self):
        return None if self._view is None else list(self._view)

    def set_view_state(self, state) -> None:
        if state is None:
            self._view = None
        else:
            lon, lat, px_per_m = (float(value) for value in state)
            self._view = (lon, lat, self._clamp_scale(px_per_m))
        self._base = None
        self.update()
        self.view_changed.emit()

    def reset_view(self) -> None:
        if self._automatic_basemap:
            self._set_basemap(self._overview())
        self.projection = LocalProjection.for_bbox(self.basemap.bbox)
        self._paths = self._base = self._sides_key = None
        self.focus_node.blockSignals(True); self.focus_node.setCurrentIndex(0); self.focus_node.blockSignals(False)
        self.set_view_state(None)

    @staticmethod
    def _clamp_scale(px_per_m: float) -> float:
        return max(1.0 / MAX_M_PER_PX, min(1.0 / MIN_M_PER_PX, px_per_m))

    def zoom_by(self, factor: float, anchor: QPointF | None = None) -> None:
        """Zoom about `anchor` (widget pixels; the centre when omitted). Scale stays uniform."""
        current = self.transform()
        anchor = anchor if anchor is not None else QPointF(self.width() / 2, self.height() / 2)
        px_per_m = self._clamp_scale(current.px_per_m * factor)
        lon, lat = current.to_lonlat(anchor.x(), anchor.y())       # stays under the anchor
        x, y = self.projection.to_xy(lon, lat)
        centre_x = x - (anchor.x() - self.width() / 2) / px_per_m
        centre_y = y + (anchor.y() - self.height() / 2) / px_per_m
        centre = self.projection.to_lonlat(centre_x, centre_y)
        self.set_view_state((centre[0], centre[1], px_per_m))

    def pan_by(self, dx: float, dy: float) -> None:
        current = self.transform()
        lon, lat = current.to_lonlat(self.width() / 2 - dx, self.height() / 2 - dy)
        self.set_view_state((lon, lat, current.px_per_m))

    # --------------------------------------------------------------- geometry
    def label_sides(self, transform: ViewTransform | None = None) -> dict:
        """`{node id: side}` — where each node's label is drawn so that close nodes stay readable."""

        transform = transform or self.transform()
        badges = self.net.badge_map
        key = (self.width(), self.height(), round(transform.px_per_m, 9), round(transform.origin_x_px, 2),
               round(transform.origin_y_px, 2),
               tuple((node.id,node.map_label,node.labelled,marker_kind(node),node.longitude,node.latitude,badges.get(node.id,'')) for node in self.net.located if node.physical_type!='satellite'))
        if self._sides_key != key:
            items = [(node.id, QPointF(*transform.to_px(node.longitude, node.latitude)), replace(node,id=node.map_label),
                      badges.get(node.id, "")) for node in self.net.located if node.labelled]
            self._sides = place_node_labels(items, QRectF(2, 2, self.width() - 4, self.height() - 4))
            self._sides_key = key
        return self._sides

    def node_position(self, node_id: str, transform: ViewTransform | None = None) -> QPointF | None:
        node = self.net.node(node_id)
        if node is None or not node.located:
            return None
        x, y = (transform or self.transform()).to_px(node.longitude, node.latitude)
        return QPointF(x, y)

    def _position(self, node) -> QPointF:
        """Pixel position of a located node (older name)."""

        x, y = self.transform().to_px(node.longitude, node.latitude)
        return QPointF(x, y)

    def placed_ids(self) -> list:
        return [node.id for node in self.net.located]

    def segments(self, transform: ViewTransform | None = None) -> list:
        """`[(link, a, b, to_queue)]`: every fibre drawn on the map, in pixels.

        A fibre between two located nodes is a straight line between their coordinates.
        Both endpoints must have declared coordinates. A queue position is not
        a geographic location, even if callers provide a historical anchor.
        """

        transform = transform or self.transform()
        positions = {node.id: QPointF(*transform.to_px(node.longitude, node.latitude)) for node in self.net.located}
        out = []
        for link in self.net.links:
            if link.style == "pair":
                continue
            a, b = positions.get(link.a), positions.get(link.b)
            if a is not None and b is not None:
                out.append((link, a, b, ""))
        return out

    def route_segments(self, transform=None):
        """Contiguous located parts of a route; never bridge an unlocated node."""
        transform = transform or self.transform()
        located = {node.id: node for node in self.net.located}
        segments, points = [], []
        for name in self.net.route:
            node = located.get(name)
            if node is None:
                if len(points) > 1:
                    segments.append(points)
                points = []
            else:
                points.append(QPointF(*transform.to_px(node.longitude, node.latitude)))
        if len(points) > 1:
            segments.append(points)
        return segments

    def drawn_link_keys(self) -> set:
        return {link.key for link, _a, _b, _queue in self.segments()}

    def hit(self, point: QPointF) -> tuple:
        """Nodes first, then the nearest line; no invisible midpoint targets."""

        transform = self.transform()
        focused = self.focus_node.currentData()
        positions = [(node, QPointF(*transform.to_px(node.longitude, node.latitude))) for node in self.net.located]
        # A wide institution label must not intercept another node's physical
        # marker. Resolve the nearest actual marker first; only genuinely
        # coincident centres use focus/backend inventory order as a tie-break.
        markers = [((centre.x()-point.x())**2+(centre.y()-point.y())**2,
                    node.id != focused, index, node.id)
                   for index, (node, centre) in enumerate(positions) if marker_hit_rect(centre, node).contains(point)]
        if markers:
            return 'node', min(markers)[3]
        badges = self.net.badge_map
        sides = self.label_sides(transform)
        for node, centre in sorted(positions, key=lambda item: (item[0].id == focused, item[0].labelled), reverse=True):
            if not node.labelled: continue
            rect = node_rect(centre, replace(node, id=node.map_label), badge=badges.get(node.id, ''),
                             side=sides.get(node.id, 'right'))
            if rect.contains(point):
                return "node", node.id
        segments = self.segments(transform)
        best = None
        for link, a, b, _queue in segments:
            distance = segment_distance(point, a, b)
            if distance <= 5.0 and (best is None or distance < best[0]):
                best = (distance, link.key)
        return ("link", best[1]) if best else (None, "")

    # ------------------------------------------------------------- pick mode
    def start_pick(self, label: str) -> None:
        """The next click on the map reports its latitude / longitude instead of selecting."""

        self._pick = label or "node"
        self.pick_label.setText(f"Click the map to set the position of {self._pick}")
        self.pick_hint.show()
        self.pick_hint.raise_()
        self.setCursor(Qt.CursorShape.CrossCursor)
        self._place_overlays()

    def cancel_pick(self) -> None:
        if self._pick:
            self._end_pick()
            self.pick_cancelled.emit()

    def _end_pick(self) -> None:
        self._pick = ""
        self.pick_hint.hide()
        self.setCursor(Qt.CursorShape.ArrowCursor)

    @property
    def picking(self) -> bool:
        return bool(self._pick)

    # ---------------------------------------------------------------- events
    def resizeEvent(self, event) -> None:
        self._base = None
        if self.isVisible():
            self._resize_settle.start()
        self._place_overlays()
        super().resizeEvent(event)

    def _finish_resize(self) -> None:
        self._base = None
        self.update()

    def hideEvent(self, event) -> None:
        self._resize_settle.stop()
        super().hideEvent(event)

    def _place_overlays(self) -> None:
        if self._chrome is not None:
            self._chrome._place_overlays()
            return
        self.controls.adjustSize()
        controls_w = self.controls.sizeHint().width()
        stacked = self.width() < controls_w + 280
        available = self.width()-24 if stacked else self.width()-controls_w-36
        requested = max(220, *(label.fontMetrics().horizontalAdvance(label.text())+32 for label in (self.title, self.placed)))
        self.info.setFixedWidth(max(40, min(400, requested, available)))
        self.info.setFixedHeight(INFO_BASE_H + getattr(self, "_info_height", 0))
        self.info.move(12, 48 if stacked else 12)
        self.controls.adjustSize()
        self.controls.move(max(0, self.width() - self.controls.sizeHint().width() - 12), 12)
        self.pick_hint.adjustSize()
        self.pick_hint.move(max(12, (self.width() - self.pick_hint.sizeHint().width()) // 2),
                            max(12, self.height() - self.pick_hint.sizeHint().height() - 46))

    def wheelEvent(self, event) -> None:
        delta = event.angleDelta().y()
        if delta:
            factor = 1.2 ** (delta / 120.0)
            if self._chrome is not None:
                self.zoom_requested.emit(factor, event.position())
            else:
                self.zoom_by(factor, event.position())
        event.accept()

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Escape and self._pick:
            self.cancel_pick()
            event.accept()
            return
        super().keyPressEvent(event)

    def mousePressEvent(self, event) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            return super().mousePressEvent(event)
        point = event.position()
        if self._pick:
            lon, lat = self.transform().to_lonlat(point.x(), point.y())
            self._end_pick()
            self.location_picked.emit(lat, lon)
            return
        kind, ident = self.hit(point)
        if kind == "node":
            self.node_selected.emit(ident)
        elif kind == "link":
            self.link_selected.emit(ident)
        else:
            self._drag = (point, self.transform())
            self.setCursor(Qt.CursorShape.ClosedHandCursor)

    def mouseMoveEvent(self, event) -> None:
        point = event.position()
        if self._drag is not None:
            start, origin = self._drag
            lon, lat = origin.to_lonlat(self.width() / 2 - (point.x() - start.x()),
                                        self.height() / 2 - (point.y() - start.y()))
            self._view = (lon, lat, origin.px_per_m)
            self._base = None
            self.update()
            return
        if self._pick:
            lon, lat = self.transform().to_lonlat(point.x(), point.y())
            QToolTip.showText(event.globalPosition().toPoint(), f"{format_lat(lat)} · {format_lon(lon)}", self)
            return
        hover = self.hit(point)
        if hover != self._hover:
            self._hover = hover
            self.setCursor(Qt.CursorShape.PointingHandCursor if hover[0] else Qt.CursorShape.ArrowCursor)
            self.setToolTip(self._tooltip(hover))
            self.update()
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if self._drag is not None:
            self._drag = None
            self.setCursor(Qt.CursorShape.ArrowCursor)
            self.view_changed.emit()
        super().mouseReleaseEvent(event)

    def leaveEvent(self, event) -> None:
        if self._hover != (None, ""):
            self._hover = (None, "")
            self.update()
        super().leaveEvent(event)

    def _tooltip(self, hover) -> str:
        kind, ident = hover
        if kind == "node":
            node = self.net.node(ident)
            if node is not None:
                return f"{node.map_label} · {node.name or node.place or 'no place label'}\n{node.category}\n{node.network_role} · {node.physical_type}\n{format_lat(node.latitude)}, {format_lon(node.longitude)}"
        if kind == "link":
            link = self.net.link(ident)
            if link is not None:
                return f"{link.key} · {format_km(link.distance_km)} · {link.state_text.lower()}"
        return ""

    # ----------------------------------------------------------------- paint
    def _map_paths(self):
        """Base-map geometry in projected metres (x east, y north), built once."""

        if self._paths is None:
            to_xy = self.projection.to_xy

            def polygon_path(polygons):
                path = QPainterPath()
                path.setFillRule(Qt.FillRule.OddEvenFill)
                for polygon in polygons:
                    for ring in polygon.rings:
                        x, y = to_xy(*ring[0])
                        path.moveTo(x, y)
                        for lon, lat in ring[1:]:
                            x, y = to_xy(lon, lat)
                            path.lineTo(x, y)
                        path.closeSubpath()
                return path

            roads = {name: QPainterPath() for name in ROAD_WIDTH}
            for road in self.basemap.roads:
                path = roads.setdefault(road.road_class, QPainterPath())
                for line in road.lines:
                    x, y = to_xy(*line[0])
                    path.moveTo(x, y)
                    for lon, lat in line[1:]:
                        x, y = to_xy(lon, lat)
                        path.lineTo(x, y)
            self._paths = (polygon_path(self.basemap.land), polygon_path(self.basemap.municipality), roads)
        return self._paths

    def _base_layer(self, transform: ViewTransform) -> QPixmap:
        from ..display import raster_scale_for
        ratio = raster_scale_for(self)
        antialiased = not self._resize_settle.isActive()
        key = (self.width(), self.height(), ratio, round(transform.px_per_m, 9), round(transform.origin_x_px, 2),
               round(transform.origin_y_px, 2), current_theme(), self.signal_mode,
               tuple((node.id, node.longitude, node.latitude,node.labelled,node.map_label) for node in self.net.located if node.physical_type!='satellite'),
               tuple(self.net.badges), antialiased)
        if self._base is not None and self._base_key == key:
            return self._base
        pixmap = QPixmap(max(1, round(self.width() * ratio)), max(1, round(self.height() * ratio)))
        pixmap.setDevicePixelRatio(ratio)
        pixmap.fill(qc(signal_map_ocean() if self.signal_mode else C.water))
        p = QPainter(pixmap)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, antialiased)
        land, municipality, roads = self._map_paths()
        metres = QTransform(transform.px_per_m, 0, 0, -transform.px_per_m, transform.origin_x_px,
                            transform.origin_y_px)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(qc(mix(C.land, C.water, 0.5)))          # neighbouring land: between water and land
        p.drawPath(metres.map(land))
        outline = QPen(qc(C.line), 1.2)
        p.setPen(outline)
        p.setBrush(qc(C.land))
        p.drawPath(metres.map(municipality))                # the municipality of Salvador
        p.setBrush(Qt.BrushStyle.NoBrush)
        for name in ("secondary", "primary", "trunk", "motorway"):
            if name == "secondary" and transform.px_per_m < 0.012:
                continue
            pen = QPen(qc(C.road), ROAD_WIDTH.get(name, 1.0))
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            p.setPen(pen)
            p.drawPath(metres.map(roads[name]))
        if self.signal_mode:
            paint_signal_cross(p, map_cross_rect(self.width(), self.height()))
        self._paint_labels(p, transform)
        self._paint_furniture(p, transform)
        p.end()
        self._base, self._base_key = pixmap, key
        return pixmap

    def _reserved(self, transform: ViewTransform) -> list:
        badges = self.net.badge_map
        rects = [QRectF(self.info.geometry()).adjusted(-12, -12, 12, 12),
                 QRectF(self.controls.geometry()).adjusted(-4, -4, 4, 4),
                 QRectF(0, self.height() - 50, 170, 50),
                 QRectF(self.width() - 60, 46, 60, 60)]
        sides = self.label_sides(transform)
        for node in self.net.located:
            centre = QPointF(*transform.to_px(node.longitude, node.latitude))
            rect=node_rect(centre,replace(node,id=node.map_label),badge=badges.get(node.id,''),side=sides.get(node.id,'right')) if node.labelled else QRectF(centre.x()-8,centre.y()-8,16,16)
            rects.append(rect.adjusted(-4,-3,4,3))
        return rects

    def _paint_labels(self, p: QPainter, transform: ViewTransform) -> None:
        taken = self._reserved(transform)
        bounds = QRectF(4, 4, self.width() - 8, self.height() - 26)

        def place(text: str, x: float, y: float, font, colour, pad: float = 5.0) -> bool:
            metrics = QFontMetricsF(font)
            width = metrics.horizontalAdvance(text)
            if font.capitalization():
                width = metrics.horizontalAdvance(text.upper())
            rect = QRectF(x - width / 2, y - metrics.height() / 2, width, metrics.height())
            padded = rect.adjusted(-pad, -2, pad, 2)
            if not bounds.contains(rect) or any(padded.intersects(other) for other in taken):
                return False
            taken.append(padded)
            p.setFont(font)
            p.setPen(qc(colour))
            p.drawText(rect.adjusted(-2, 0, 2, 0), Qt.AlignmentFlag.AlignCenter, text)
            return True

        for item in self.basemap.water_labels:                 # `.lbl t-mute`
            x, y = transform.to_px(item.lon, item.lat)
            if not place(item.name, x, y, label_font(11), C.ink3):
                # the anchor is outside this view: keep the name on the water it names, if visible
                x = min(max(x, 120.0), self.width() - 120.0)
                y = min(max(y, 30.0), self.height() - 50.0)
                lon, lat = transform.to_lonlat(x, y)
                if not self.basemap.contains_land(lon, lat):
                    place(item.name, x, y, label_font(11), C.ink3)
        order = {"municipality": 0, "airport": 1, "landmark": 2, "island": 3, "neighbourhood": 4}
        places = sorted(self.basemap.places, key=lambda item: (item.rank, order.get(item.kind, 9), item.name))
        for item in places:
            if item.rank >= 2 and transform.px_per_m < PLACE_RANK2_PX_PER_M:
                continue
            x, y = transform.to_px(item.lon, item.lat)
            if item.kind == "municipality":
                place(item.name, x, y, label_font(11), C.ink3)
            else:
                place(item.name, x, y, mono(11), C.ink3 if item.rank == 1 else C.ink4)
        seen = set()
        for road in self.basemap.roads:                         # the main roads, by their ref or popular name
            if road.road_class not in ("motorway", "trunk"):
                continue
            name = road.ref or road.alias.replace("Avenida", "Av.") or ""
            if not name or name in seen or road.drawn_km < 3.0:
                continue
            line = max(road.lines, key=len)
            for fraction in (0.5, 0.3, 0.7):
                lon, lat = line[min(len(line) - 1, int(len(line) * fraction))]
                x, y = transform.to_px(lon, lat)
                if place(name, x, y - 9, mono(11), C.ink4, pad=3.0):
                    seen.add(name)
                    break

    def _paint_furniture(self, p: QPainter, transform: ViewTransform) -> None:
        # scale bar: bracket + length, bottom left (prototype)
        length_km, length_px = transform.scale_bar(120.0)
        text = f"{length_km:g} km" if length_km >= 1 else f"{length_km * 1000:g} m"
        metrics = QFontMetricsF(mono(11))
        box = QRectF(12, self.height() - 12 - 34, max(length_px, metrics.horizontalAdvance(text)) + 20, 34)
        p.fillRect(box, qc(C.bg))
        p.setPen(QPen(qc(C.soft), 1))
        p.drawRect(box.adjusted(0.5, 0.5, -0.5, -0.5))
        pen = QPen(qc(C.ink), 2)
        pen.setCapStyle(Qt.PenCapStyle.FlatCap)
        pen.setJoinStyle(Qt.PenJoinStyle.MiterJoin)
        p.setPen(pen)
        left, top = box.left() + 10, box.top() + 7
        p.drawPolyline([QPointF(left, top), QPointF(left, top + 6), QPointF(left + length_px, top + 6),
                        QPointF(left + length_px, top)])
        p.setFont(mono(11))
        p.setPen(qc(C.ink))
        p.drawText(QRectF(left, top + 9, box.width() - 14, 15),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, text)
        self._scale_rect = QRectF(left, top, length_px, 6)
        # north mark, top right under the view controls
        cx, cy = self.width() - 26.0, 66.0
        p.setPen(QPen(qc(C.ink2), 1.4))
        p.drawLine(QPointF(cx, cy + 12), QPointF(cx, cy - 8))
        p.drawPolyline([QPointF(cx - 4, cy - 3), QPointF(cx, cy - 9), QPointF(cx + 4, cy - 3)])
        p.setFont(label_font(11))
        p.drawText(QRectF(cx - 12, cy + 12, 24, 16), Qt.AlignmentFlag.AlignCenter, "N")
        # attribution (mandatory wherever the map is drawn), bottom right
        font = text_font(8.5)
        font.setPixelSize(9)
        metrics = QFontMetricsF(font)
        available = self.width() - box.right() - 30
        note = metrics.elidedText(self.basemap.attribution, Qt.TextElideMode.ElideRight, max(60.0, available))
        width = metrics.horizontalAdvance(note) + 12
        rect = QRectF(self.width() - width - 8, self.height() - 8 - 18, width, 18)
        ground = qc(C.bg)
        ground.setAlphaF(0.78)
        p.fillRect(rect, ground)
        p.setFont(font)
        p.setPen(qc(C.ink3))
        p.drawText(rect, Qt.AlignmentFlag.AlignCenter, note)
        self.attribution_text = note

    def paintEvent(self, _event) -> None:
        from ..display import raster_scale_for
        transform = self.transform()
        base = self._base_layer(transform)
        # Cache this widget's own artwork, never its child controls or a window.
        # Wave animations elsewhere in the graphics proxy must not rerun every
        # map label, marker and link. Any snapshot/view/hover/density change draws
        # a fresh surface; hit testing always reads the current model.
        # Only fields actually drawn belong to this raster. The model contains
        # large satellite science records, communicator contracts and a live
        # clock; their updates remain available to hit tests/details but must
        # not repaint an unchanged terrestrial map or compare nested records.
        key = (tuple(self.net.located),
               tuple((link.key, link.id, link.a, link.b, link.style) for link in self.net.links),
               self.net.selection, self.net.layer, self.net.route, self.net.badges,
               self._hover, base.cacheKey())
        if key != getattr(self, '_surface_key', None):
            scale = raster_scale_for(self)
            surface = QPixmap(max(1, round(self.width() * scale)), max(1, round(self.height() * scale)))
            surface.setDevicePixelRatio(scale)
            surface.fill(Qt.GlobalColor.transparent)
            self._paint_surface(surface, transform, base)
            self._surface_key, self._surface = key, surface
        p = QPainter(self)
        p.drawPixmap(0, 0, self._surface)
        p.end()

    def _paint_surface(self, surface, transform, base) -> None:
        p = QPainter(surface)
        p.drawPixmap(0, 0, base)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        net = self.net
        kind, ident = net.selection
        entanglement = net.layer == "entanglement"
        segments = self.segments(transform)
        p.setOpacity(0.35 if entanglement else 1.0)             # `physOpacity`
        for link, a, b, _queue in segments:
            if any(self.net.node(x).physical_type=='satellite' for x in (link.a,link.b)):
                p.setPen(QPen(qc(C.acc),.8));p.drawLine(a,b)
            else:paint_link(p, a, b, link.style, selected=kind == "link" and ident in (link.key, link.id))
        p.setOpacity(1.0)
        if entanglement and net.route:
            for points in self.route_segments(transform):
                paint_route(p, points)
        badges = net.badge_map
        sides = self.label_sides(transform)
        for node in sorted(net.located,key=lambda n:n.labelled):
            centre = QPointF(*transform.to_px(node.longitude, node.latitude))
            if node.labelled:
                paint_node(p, centre, replace(node,id=node.map_label), selected=kind == "node" and ident == node.id,
                           badge=badges.get(node.id, ""), side=sides.get(node.id, "right"))
            else:
                p.save()
                if node.isolated:p.setOpacity(.4)
                paint_marker(p,centre,marker_kind(node),node.health,selected=kind=='node' and ident==node.id,
                             calibrating=node.status=='calibrating')
                p.restore()
        p.setPen(QPen(qc(C.soft), 1))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRect(QRectF(0.5, 0.5, self.width() - 1, self.height() - 1))
        if not net.located and net.nodes:
            font = text_font(12.5)
            text = "No node has a declared location yet — set one from the queue below."
            width = QFontMetricsF(font).horizontalAdvance(text) + 28
            box = QRectF((self.width() - width) / 2, self.height() - 98, width, 30)
            p.fillRect(box, qc(C.bg))                 # its own ground: place names must not run through it
            p.setPen(QPen(qc(C.line), 1))
            p.drawRect(box.adjusted(0.5, 0.5, -0.5, -0.5))
            p.setFont(font)
            p.setPen(qc(C.ink))
            p.drawText(box, Qt.AlignmentFlag.AlignCenter, text)
        p.end()


from .geo_view import GeoMap  # compatibility facade; only the active renderer exists

__all__ = ["GeoMap", "MapRenderer", "MapButton", "MAX_M_PER_PX", "MIN_M_PER_PX"]
