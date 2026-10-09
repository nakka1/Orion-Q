"""GEO controller: shared observations/cameras, one disposable renderer.

The controller never polls a provider. Topology feeds the same NetModel used by
the inspector, matrix and graph. Renderer retirement finishes before activation;
only a short-lived raster is retained for the visual crossfade.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import math
import time

from PySide6.QtCore import QPoint, QPointF, Qt, QTimer, QVariantAnimation, Signal
from PySide6.QtGui import QPainter, QPixmap
from PySide6.QtWidgets import QComboBox, QWidget

from ..geodata import LocalProjection, load_salvador
from ..theme import C, label_font, qc, text as text_font
from .network_views import NetModel, build_net_model
from .prims import Frame, L, Label, hbox, vbox

# Width in projected metres, independent of monitor size. The return span is
# well inside the map threshold; tiny inverse scrolls cannot flip modes again.
MAP_TO_GLOBE_SPAN_M = 60_000_000.
GLOBE_TO_MAP_SPAN_M = 20_000_000.
# Keep close inspection in the globe; the renderer supports zoom up to 6.
GLOBE_TO_MAP_ZOOM = 5.8


@dataclass
class GeoSceneState:
    net: NetModel = field(default_factory=NetModel)
    mode: str = 'map'
    map_view: tuple | None = None
    map_basemap: object = None  # immutable geographic data, never a graphics cache
    projection_anchor: tuple = (-38.5, -12.95)
    globe_camera: tuple | None = None
    globe_entry_center: tuple | None = None
    focus_id: str = ''
    satellite_id: str = ''
    alerts: tuple = ()
    pick_id: str = ''
    link_anchors: dict = field(default_factory=dict)
    node_anchors: dict = field(default_factory=dict)
    last_selection: tuple = (None, '')
    orbit_clocks: dict = field(default_factory=dict)
    signal_mode: bool = False


class _TransitionRaster(QWidget):
    def __init__(self, parent):
        super().__init__(parent)
        self.image = None
        self.opacity = 0.
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.hide()

    def paintEvent(self, event):
        if self.image is not None:
            painter = QPainter(self)
            painter.setOpacity(self.opacity)
            painter.drawPixmap(self.rect(), self.image)
            painter.end()


class GeoMap(QWidget):
    """Public GEO facade retained for existing screens and geographic callers."""
    node_selected = Signal(str)
    link_selected = Signal(str)
    location_picked = Signal(float, float)
    pick_cancelled = Signal()
    alert_clicked = Signal(str)
    view_changed = Signal()
    mode_changed = Signal(str)

    def __init__(self, parent=None, basemap=None):
        super().__init__(parent)
        from .geo_map import MapButton
        self.scene = GeoSceneState(map_basemap=basemap or load_salvador())
        self._initial_basemap = basemap
        self._renderer = None
        self._retiring = False
        self._closed = False
        self._created = self._destroyed = 0
        self._focus_signature = None
        self._info_signature = None
        self._pending_focus = ''
        self._presentation_pending = False
        self._presentation_selection = (None, '')
        self._alerts = ()  # Actual rows presented, independent of shared metadata.
        self._sides = {}; self._sides_key = None
        self._activate_timer = QTimer(self)
        self._activate_timer.setSingleShot(True)
        self._activate_timer.timeout.connect(self._activate_renderer)
        self._fade = _TransitionRaster(self)
        self._fade_animation = QVariantAnimation(self)
        self._fade_animation.setDuration(350)
        self._fade_animation.setStartValue(1.)
        self._fade_animation.setEndValue(0.)
        self._fade_animation.valueChanged.connect(self._fade_step)
        self._fade_animation.finished.connect(self._clear_fade)
        self._fade_guard=QTimer(self);self._fade_guard.setSingleShot(True)
        self._fade_guard.setTimerType(Qt.TimerType.PreciseTimer)
        self._fade_guard.timeout.connect(self._clear_fade)
        self.setMinimumHeight(200)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        self.setAccessibleName('GEO: alternância Mapa e Globo; dados compartilhados do backend')

        self.info = Frame(bg=C.bg, border=C.line, parent=self)
        box = vbox(self.info, (12, 8, 12, 8), 2)
        self.title = L('Mapa · coordenadas cadastradas', label_font(11), C.ink, elide=True)
        self.placed = L('', text_font(12), C.ink2, elide=True)
        box.addWidget(self.title); box.addWidget(self.placed)
        self.alert_host = QWidget()
        self.alert_lay = vbox(self.alert_host, (0, 4, 0, 0), 0)
        box.addWidget(self.alert_host); self.alert_host.hide()
        self._info_height = 0

        self.controls = QWidget(self)
        row = hbox(self.controls, (0, 0, 0, 0), 4)
        self.mode_button = MapButton('Mapa → Globo', width=132)
        self.mode_button.setAccessibleName('Visualização atual: Mapa. Alternar para Globo')
        self.mode_button.setToolTip('Alternar Mapa/Globo preservando dados, seleção e detalhes. O scroll também alterna a visualização.')
        self.mode_button.clicked.connect(self.toggle_mode)
        row.addWidget(self.mode_button)
        self.focus_node = QComboBox()
        self.focus_node.setFixedWidth(152)
        self.focus_node.setAccessibleName('Selecionar node ou satélite do cenário')
        self.focus_node.setToolTip('Todos os componentes continuam acessíveis, inclusive fora do enquadramento. Selecione um satélite para seus detalhes ou um campus para focar o mapa.')
        self.focus_node.addItem('All nodes', '')
        self.focus_node.currentIndexChanged.connect(self._focus_node)
        row.addWidget(self.focus_node)
        self.zoom_in = MapButton('+', width=28)
        self.zoom_out = MapButton('−', width=28)
        self.reset = MapButton('Reset view')
        self.zoom_in.setAccessibleName('Zoom in'); self.zoom_out.setAccessibleName('Zoom out')
        self.zoom_in.setToolTip('Aproximar a visualização ativa. O scroll também permite passar do globo ao mapa.')
        self.zoom_out.setToolTip('Afastar a visualização ativa. O scroll do mapa-múndi permite passar ao globo.')
        self.reset.setToolTip('Retornar ao mapa com todas as instalações; preservar dados e seleção.')
        self.zoom_in.clicked.connect(lambda: self.zoom_by(1.5, automatic=False))
        self.zoom_out.clicked.connect(lambda: self.zoom_by(1/1.5, automatic=False))
        self.reset.clicked.connect(self.reset_view)
        for control in (self.zoom_in, self.zoom_out, self.reset): row.addWidget(control)

        self.pick_hint = Frame(bg=C.bg, border=C.acc, parent=self)
        hint = hbox(self.pick_hint, (12, 6, 12, 6), 10)
        self.pick_label = L('', text_font(12.5), C.ink)
        hint.addWidget(self.pick_label)
        self.pick_cancel = MapButton('Cancel')
        self.pick_cancel.clicked.connect(self.cancel_pick)
        hint.addWidget(self.pick_cancel); self.pick_hint.hide()
        self._activate_renderer()

    @property
    def net(self): return self.scene.net

    @property
    def signal_mode(self): return self.scene.signal_mode

    def set_signal_mode(self, enabled: bool) -> None:
        """Keep EVA scenery across exclusive renderer swaps and reconstruction."""
        enabled = bool(enabled)
        if self.scene.signal_mode == enabled:
            return
        self.scene.signal_mode = enabled
        self._clear_fade()
        if self._renderer is not None:
            self._renderer.set_signal_mode(enabled)

    @property
    def mode(self): return self.scene.mode

    @property
    def renderer(self): return self._renderer

    @property
    def globe(self):
        return self._renderer if self.mode == 'globe' else None

    @property
    def available(self): return any(n.located and n.physical_type != 'satellite' for n in self.net.nodes)

    @property
    def basemap(self):
        return self._renderer.basemap if self._renderer is not None and self.mode == 'map' else self.scene.map_basemap

    @property
    def projection(self):
        return self._renderer.projection if self._renderer is not None and self.mode == 'map' else LocalProjection(*self.scene.projection_anchor)

    @property
    def _view(self):
        return self._renderer._view if self._renderer is not None and self.mode == 'map' else self.scene.map_view

    def __getattr__(self, name):
        # Read-only compatibility for map diagnostics (scale rectangle, raster
        # cache etc.). Never creates an inactive renderer to satisfy a query.
        renderer = self.__dict__.get('_renderer')
        if renderer is not None and self.__dict__.get('scene') is not None and self.scene.mode == 'map':
            return getattr(renderer, name)
        raise AttributeError(name)

    def _sync_camera(self):
        if self._renderer is None: return
        if self.mode == 'map':
            self.scene.map_view = self._renderer._view
            self.scene.map_basemap = self._renderer.basemap
            p = self._renderer.projection
            self.scene.projection_anchor = (p.lon0, p.lat0)
        else:
            self.scene.globe_camera = self._renderer.camera_state()

    def _camera_changed(self):
        self._sync_camera(); self.view_changed.emit()

    def _apply_model(self):
        if self._renderer is None: return
        # A hidden QWidget postpones resizeEvent until show. Geometry queries
        # and campus hit tests must still use the controller's current size.
        self._renderer.setGeometry(self.rect())
        if self.mode == 'map':
            self._renderer.set_model(self.net)
            self._sync_camera()
        else:
            self._renderer.set_model(self.net, self.scene.satellite_id, clocks=self.scene.orbit_clocks)
            self.title.setText('Globo · todos os satélites do cenário')
        self._update_info()

    def set_model(self, net):
        self.set_model_state(net)
        self._present_model()

    def set_model_state(self, net):
        """Publish shared metadata while another topology view is active.

        No widget layout, raster/path construction, renderer activation or
        paint scheduling occurs here. Even hidden publications advance the
        orbital clock, so reopening does not invent an accelerated clock step.
        """
        self.scene.net = net
        wall = time.monotonic(); clocks = {}
        for node in net.nodes:
            if node.physical_type != 'satellite': continue
            state = (net.satellites or {}).get(node.id)
            if state is None: continue
            clock = state.get('at_network_s', net.network_time_s)
            if not isinstance(clock, (int, float)) or not math.isfinite(clock): clock = net.network_time_s
            old = self.scene.orbit_clocks.get(node.id)
            clocks[node.id] = old if old and old[0] == clock else (clock, max(0., clock-old[0]) if old else 0., wall)
        self.scene.orbit_clocks = clocks
        self.scene.last_selection = tuple(net.selection)
        selected = net.node(net.selection[1]) if net.selection[0] == 'node' else None
        if selected and selected.physical_type == 'satellite': self.scene.satellite_id = selected.id
        self._presentation_pending = True

    def _present_model(self):
        net = self.scene.net
        previous = self._presentation_selection
        self._presentation_selection = tuple(net.selection)
        self._presentation_pending = False
        signature = tuple((n.id, n.map_label, n.located, n.physical_type) for n in net.nodes)
        if signature != self._focus_signature:
            self._focus_signature = signature
            self.focus_node.blockSignals(True)
            self.focus_node.clear(); self.focus_node.addItem('All nodes', '')
            for node in net.nodes:
                suffix = ' · sem posição' if not node.located and node.physical_type != 'satellite' else ''
                self.focus_node.addItem(node.map_label + suffix, node.id)
            self.focus_node.setCurrentIndex(max(0, self.focus_node.findData(self.scene.focus_id)))
            self.focus_node.blockSignals(False)
        selected = net.node(net.selection[1]) if net.selection[0] == 'node' else None
        if selected and selected.physical_type == 'satellite':
            self.scene.satellite_id = selected.id
            if tuple(net.selection) != previous and self.mode == 'map': self.set_mode('globe', focus_satellite=selected.id)
        self._apply_model()

    def _update_info(self):
        orbital = sum(n.physical_type == 'satellite' for n in self.net.nodes)
        located = sum(n.located and n.physical_type != 'satellite' for n in self.net.nodes)
        waiting = len(self.net.nodes)-orbital-located
        value = f'{located} instalações localizadas · {orbital} orbitais' + (f' · {waiting} aguardando localização' if waiting else '')
        value = value if self.net.nodes else 'No nodes in this snapshot'
        if self.placed.text() != value: self.placed.setText(value)
        # The text is constant across most telemetry publications. Layout
        # still follows mode/title, size, controls and alert-height changes.
        hint = self.controls.sizeHint()
        signature = (value, self.title.text(), self.mode_button.text(), self.width(),
                     self.height(), self._info_height, hint.width(), hint.height())
        if signature != self._info_signature:
            self._info_signature = signature
            self._place_overlays()

    def toggle_mode(self):
        self.set_mode('globe' if self.mode == 'map' else 'map')

    def set_mode(self, mode, *, focus_satellite='', camera=None):
        if self._closed or mode not in ('map', 'globe'): return
        if mode == self.mode:
            if camera is not None and mode == 'globe':
                self.scene.globe_camera = camera
                if self.globe is not None: self.globe.set_camera(*camera)
            return
        self._sync_camera()
        if mode == 'globe':
            self._pending_focus = ''
            if camera is None:
                transform = self.transform()
                lon, lat = transform.to_lonlat(self.width()/2, self.height()/2)
                if focus_satellite:
                    sat = (self.net.satellites or {}).get(focus_satellite, {})
                    lon = sat.get('sat_longitude', lon); lat = sat.get('sat_latitude', lat)
                old = self.scene.globe_camera
                zoom = old[2] if old and self.scene.globe_entry_center == ((lon+180.) % 360.-180., max(-85., min(85., lat))) and not focus_satellite else 1.
                camera = (lon, lat, zoom)
            camera = ((camera[0]+180.) % 360.-180., max(-85., min(85., camera[1])), camera[2])
            self.scene.globe_camera = camera
            self.scene.globe_entry_center = camera[:2]
        else:
            if camera is None and self.scene.globe_camera and self.scene.globe_camera[:2] != self.scene.globe_entry_center:
                camera = self.scene.globe_camera
            if camera is not None:
                lon, lat = (camera[0]+180.) % 360.-180., max(-85., min(85., camera[1]))
                self.scene.projection_anchor = (lon, lat)
                self.scene.map_view = (lon, lat, max(1e-5, self.width()/GLOBE_TO_MAP_SPAN_M))
        self._retire_renderer(capture=True)
        self.scene.mode = mode
        label = 'Mapa → Globo' if mode == 'map' else 'Globo → Mapa'
        self.mode_button.setText(label)
        self.mode_button.setAccessibleName('Visualização atual: ' + ('Mapa. Alternar para Globo' if mode == 'map' else 'Globo. Alternar para Mapa'))
        self.mode_button.setToolTip('Alternar Mapa/Globo preservando observações, seleção e detalhes. O scroll usa limites distintos para evitar alternâncias repetidas.')
        self._update_info()
        if not self._retiring: self._activate_timer.start(0)
        self.mode_changed.emit(mode)

    def _retire_renderer(self, *, capture=False):
        old = self._renderer
        if old is None: return
        if capture and self.isVisible():
            self._fade_guard.stop()
            self._fade_animation.stop()
            # Bound the capture target before rendering. grab().scaled() first
            # allocates a full monitor-sized screenshot, which is unnecessary
            # for this short transition on ultrawide/high-DPI displays.
            self._fade.image = self._transition_image(old)
            self._fade.opacity = 1.; self._fade.setGeometry(self.rect()); self._fade.show(); self._fade.raise_()
        self._renderer = None
        self._retiring = True
        old.shutdown()
        old.hide()
        old.setParent(None)
        old.destroyed.connect(self._renderer_destroyed)
        old.deleteLater()

    @staticmethod
    def _transition_image(renderer):
        factor = min(1., 1200/max(1, renderer.width()), 800/max(1, renderer.height()))
        image = QPixmap(max(1, round(renderer.width()*factor)), max(1, round(renderer.height()*factor)))
        image.fill(Qt.GlobalColor.transparent)
        painter = QPainter(image)
        try:
            painter.scale(factor, factor)
            renderer.render(painter, QPoint())
        finally:
            painter.end()
        return image

    def _renderer_destroyed(self, *args):
        self._destroyed += 1
        self._retiring = False
        if not self._closed: self._activate_timer.start(0)

    def _activate_renderer(self):
        if self._closed or self._retiring or self._renderer is not None: return
        if self.mode == 'map':
            from .geo_map import MapRenderer
            renderer = MapRenderer(self, self._initial_basemap, chrome=self)
            self._renderer = renderer
            renderer.setGeometry(self.rect())
            renderer.set_model(self.net)
            if self.scene.map_view is not None:
                renderer._set_basemap(self.scene.map_basemap)
                renderer.projection = LocalProjection(*self.scene.projection_anchor)
                renderer._view = self.scene.map_view
            renderer._pick = self.scene.pick_id
            renderer.set_queue_anchors(self.scene.link_anchors, self.scene.node_anchors)
            renderer.link_selected.connect(self.link_selected.emit)
        else:
            from .orbit_globe import OrbitGlobe
            renderer = OrbitGlobe(self)
            self._renderer = renderer
            renderer.setGeometry(self.rect())
            renderer.set_model(self.net, self.scene.satellite_id, clocks=self.scene.orbit_clocks)
            renderer.set_camera(*(self.scene.globe_camera or (0., 0., 1.)))
            renderer.set_pick(self.scene.pick_id)
            renderer.link_selected.connect(self.link_selected.emit)
            self.title.setText('Globo · todos os satélites do cenário')
        renderer.set_signal_mode(self.scene.signal_mode)
        self._created += 1
        renderer.node_selected.connect(self._node_selected)
        renderer.location_picked.connect(self._location_picked)
        renderer.pick_cancelled.connect(self.cancel_pick)
        renderer.view_changed.connect(self._camera_changed)
        renderer.zoom_requested.connect(self.zoom_by)
        renderer.setVisible(self.isVisible())
        if self._pending_focus and self.mode == 'map':
            ident = self._pending_focus; self._pending_focus = ''
            index = self.focus_node.findData(ident)
            if index >= 0: renderer._focus_node(index)
        self._sync_camera(); self._place_overlays()
        for control in (self.info, self.controls, self.pick_hint): control.raise_()
        if self._fade.image is not None:
            self._fade.show(); self._fade.raise_()
            for control in (self.info, self.controls, self.pick_hint): control.raise_()
            self._fade_animation.start()
            self._fade_guard.start(self._fade_animation.duration())
        self.view_changed.emit()

    def _fade_step(self, value):
        self._fade.opacity = float(value); self._fade.update()

    def _clear_fade(self):
        self._fade_guard.stop();self._fade_animation.stop()
        self._fade.hide(); self._fade.image = None

    def _focus_node(self, index):
        ident = self.focus_node.itemData(index) or ''
        self.scene.focus_id = ident
        node = self.net.node(ident)
        if not ident: self.reset_view(); return
        if node is None: return
        if node.physical_type == 'satellite':
            self.scene.satellite_id = ident
            if self.mode == 'map': self.set_mode('globe', focus_satellite=ident)
            elif self.globe is not None: self.globe.set_model(self.net, ident, clocks=self.scene.orbit_clocks)
            self.node_selected.emit(ident)
        elif node.located:
            if self.mode == 'globe':
                self._pending_focus = ident
                self.set_mode('map', camera=(node.longitude, node.latitude))
            elif self._renderer is not None:
                self._renderer._focus_node(index); self._sync_camera()
            else:
                self._pending_focus = ident
        else:
            self.node_selected.emit(ident)

    def _node_selected(self, ident):
        self.scene.focus_id = ident
        node = self.net.node(ident)
        if node and node.physical_type == 'satellite': self.scene.satellite_id = ident
        self.focus_node.blockSignals(True)
        self.focus_node.setCurrentIndex(max(0, self.focus_node.findData(ident)))
        self.focus_node.blockSignals(False)
        self.node_selected.emit(ident)

    def zoom_by(self, factor, anchor=None, *, automatic=True):
        if self._renderer is None or not math.isfinite(factor) or factor <= 0: return
        anchor = anchor or QPointF(self.width()/2, self.height()/2)
        if self.mode == 'globe':
            focus = self.globe.lonlat_at(anchor) or (self.globe.longitude, self.globe.latitude)
            self.globe.zoom_by(factor, anchor)
            self._sync_camera()
            if automatic and factor > 1 and self.globe._zoom >= GLOBE_TO_MAP_ZOOM:
                self.set_mode('map', camera=focus)
        else:
            self._renderer.zoom_by(factor, anchor)
            self._sync_camera()
            if automatic and factor < 1 and self.transform().metres_per_px*self.width() >= MAP_TO_GLOBE_SPAN_M and not self.picking:
                lon, lat = self.transform().to_lonlat(anchor.x(), anchor.y())
                self.set_mode('globe', camera=(lon, max(-85., min(85., lat)), 1.))

    def reset_view(self):
        self.scene.map_view = None
        self.scene.focus_id = ''
        self._pending_focus = ''
        self.focus_node.blockSignals(True); self.focus_node.setCurrentIndex(0); self.focus_node.blockSignals(False)
        if self.mode == 'globe':
            self.scene.globe_entry_center = self.scene.globe_camera[:2] if self.scene.globe_camera else None
            self.set_mode('map')
            self.scene.map_view = None
        elif self._renderer is not None:
            self._renderer.reset_view(); self._sync_camera()

    def view_state(self):
        self._sync_camera()
        return None if self.scene.map_view is None else list(self.scene.map_view)

    def set_view_state(self, state):
        self.scene.map_view = None if state is None else tuple(map(float, state))
        if self.mode == 'map' and self._renderer is not None:
            self._renderer.set_view_state(state); self._sync_camera()

    @property
    def is_default_view(self): return self._view is None

    def transform(self):
        from .geo_map import MapRenderer
        return MapRenderer.transform(self)

    def default_transform(self):
        from .geo_map import MapRenderer
        return MapRenderer.default_transform(self)

    @property
    def metres_per_px(self): return self.transform().metres_per_px

    def scale_bar(self): return self.transform().scale_bar(120.)

    def placed_ids(self): return [n.id for n in self.net.located]

    def node_position(self, ident, transform=None):
        node = self.net.node(ident)
        if node is None: return None
        if self.globe is not None and transform is None:
            if node.physical_type == 'satellite':
                from .orbit_globe import _valid_vector
                vector = (self.net.satellites or {}).get(ident, {}).get('ecef_km')
                return self.globe.project(vector)[0] if _valid_vector(vector) else None
            if not node.located: return None
            return self.globe.project(self.globe._vector(node))[0]
        if not node.located: return None
        return QPointF(*(transform or self.transform()).to_px(node.longitude, node.latitude))

    def label_sides(self, transform=None):
        from .geo_map import MapRenderer
        return MapRenderer.label_sides(self, transform)

    def segments(self, transform=None):
        from .geo_map import MapRenderer
        return MapRenderer.segments(self, transform)

    def route_segments(self, transform=None):
        from .geo_map import MapRenderer
        return MapRenderer.route_segments(self, transform)

    def drawn_link_keys(self):
        if self.globe is not None:
            if not self._presentation_pending: return {link.key for link, *_ in self.globe.connection_geometry()}
            from .orbit_globe import _valid_vector
            states = self.net.satellites or {}
            satellite_ids = {node.id for node in self.net.nodes if node.physical_type == 'satellite'}
            def positioned(ident):
                node = self.net.node(ident)
                if node is None: return False
                if node.physical_type == 'satellite': return _valid_vector(states.get(ident, {}).get('ecef_km'))
                return node.located
            # Read persisted associations without rebuilding a hidden renderer.
            # This is availability for drawing, never evidence of photon traffic.
            return {link.key for link in self.net.links if link.style != 'pair'
                    and satellite_ids.intersection((link.a, link.b)) and positioned(link.a) and positioned(link.b)}
        return {link.key for link, *_ in self.segments()}

    def hit(self, point):
        if self._renderer is None: return None, ''
        if self._presentation_pending and self.mode == 'map':
            from .geo_map import MapRenderer
            return MapRenderer.hit(self, point)
        self._renderer.setGeometry(self.rect())
        if self.mode == 'map': return self._renderer.hit(point)
        return self.globe.hit(point)

    def pan_by(self, dx, dy):
        if self._renderer is None: return
        if self.globe is not None:
            self.globe.set_camera(self.globe.longitude-dx*.3, self.globe.latitude+dy*.3, self.globe._zoom)
        else: self._renderer.pan_by(dx, dy)
        self._camera_changed()

    def set_queue_anchors(self, links, nodes=None):
        self.scene.link_anchors = dict(links); self.scene.node_anchors = dict(nodes or {})
        if self.mode == 'map' and self._renderer is not None: self._renderer.set_queue_anchors(links, nodes)

    @property
    def alerts(self): return self.scene.alerts

    def set_alerts(self, alerts, *, presentation=True):
        self.scene.alerts = tuple(tuple(item) for item in alerts)
        if presentation: self._present_alerts()

    def _present_alerts(self):
        if self.scene.alerts == self._alerts: return
        from .geo_map import MapRenderer
        # Alert rows belong to the controller chrome, not a graphics renderer.
        # Compare against rows actually displayed, not the most recently
        # received metadata; otherwise reopening can leave old rows frozen.
        MapRenderer.set_alerts(self, self.scene.alerts)
        if self._renderer is not None and self.mode == 'map':
            self._renderer._base_key = None; self._renderer.update()

    @property
    def picking(self): return bool(self.scene.pick_id)

    def start_pick(self, label):
        self.scene.pick_id = label or 'node'
        self.pick_label.setText(f'Clique no mapa ou na Terra para localizar {self.scene.pick_id}')
        self.pick_hint.show(); self.pick_hint.raise_()
        if self.globe is not None: self.globe.set_pick(self.scene.pick_id)
        elif self._renderer is not None: self._renderer._pick = self.scene.pick_id; self._renderer.setCursor(Qt.CursorShape.CrossCursor)
        self._place_overlays()

    def _location_picked(self, lat, lon):
        self.scene.pick_id = ''; self.pick_hint.hide()
        if self.mode == 'map' and self._renderer is not None: self._renderer._end_pick()
        self.location_picked.emit(lat, lon)

    def cancel_pick(self):
        if not self.scene.pick_id: return
        self.scene.pick_id = ''; self.pick_hint.hide()
        if self.globe is not None: self.globe.set_pick('')
        elif self._renderer is not None: self._renderer._end_pick()
        self.pick_cancelled.emit()

    def set_topology(self, nodes, links):
        from types import SimpleNamespace
        self.set_model(build_net_model(SimpleNamespace(topology=SimpleNamespace(nodes=list(nodes), links=list(links)), live=None)))

    def save_scene_state(self):
        self._sync_camera()
        return {'mode': self.mode, 'map_view': self.view_state(), 'projection': list(self.scene.projection_anchor),
                'signal_mode': self.scene.signal_mode,
                'globe_camera': self.scene.globe_camera, 'focus_id': self.scene.focus_id,
                'globe_entry_center': self.scene.globe_entry_center,
                'satellite_id': self.scene.satellite_id, 'last_selection': list(self.scene.last_selection)}

    def restore_scene_state(self, state):
        self.set_signal_mode(state.get('signal_mode', False))
        self.scene.last_selection = tuple(state.get('last_selection', (None, '')))
        self.scene.focus_id = state.get('focus_id', '')
        self.scene.satellite_id = state.get('satellite_id', '')
        self.scene.projection_anchor = tuple(state.get('projection', self.scene.projection_anchor))
        if self._renderer is not None and self.mode == 'map':
            self._renderer.projection = LocalProjection(*self.scene.projection_anchor)
        self.set_view_state(state.get('map_view'))
        if state.get('mode') == 'globe': self.set_mode('globe', camera=state.get('globe_camera'))
        entry = state.get('globe_entry_center')
        if entry is not None: self.scene.globe_entry_center = tuple(entry)

    def resource_stats(self):
        return {'mode': self.mode, 'created': self._created, 'destroyed': self._destroyed,
                'renderer_instances': self._created-self._destroyed,
                'active_renderers': int(self._renderer is not None), 'retiring': self._retiring,
                'orbit_timer_active': bool(self.globe is not None and self.globe._timer.isActive()),
                'transition_raster': self._fade.image is not None}

    def _place_overlays(self):
        if not hasattr(self, 'controls'): return
        from .geo_map import INFO_BASE_H
        self.controls.adjustSize()
        width = self.controls.sizeHint().width()
        stacked = self.width() < width+280
        # A stacked header has the full row available. Reserve the controls'
        # width only while both overlays actually occupy the same row.
        available = self.width()-24 if stacked else self.width()-width-36
        requested = max(220, *(label.fontMetrics().horizontalAdvance(label.text())+32 for label in (self.title, self.placed)))
        self.info.setFixedWidth(max(40, min(400, requested, available)))
        self.info.setFixedHeight(INFO_BASE_H+self._info_height)
        self.info.move(12, 48 if stacked else 12)
        self.controls.move(max(0, self.width()-width-12), 12)
        self.pick_hint.adjustSize()
        self.pick_hint.move(max(12, (self.width()-self.pick_hint.sizeHint().width())//2), max(12, self.height()-self.pick_hint.sizeHint().height()-46))
        for control in (self.info, self.controls, self.pick_hint): control.raise_()

    def resizeEvent(self, event):
        if self._renderer is not None: self._renderer.setGeometry(self.rect())
        self._fade.setGeometry(self.rect()); self._place_overlays()
        super().resizeEvent(event)

    def wheelEvent(self, event):
        delta = event.angleDelta().y() or event.pixelDelta().y()*3
        if delta: self.zoom_by(1.2**(max(-600, min(600, delta))/120.), event.position())
        event.accept()

    def mousePressEvent(self, event):
        if self._renderer is not None: self._renderer.mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._renderer is not None: self._renderer.mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self._renderer is not None: self._renderer.mouseReleaseEvent(event)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape and self.picking:
            self.cancel_pick(); event.accept(); return
        super().keyPressEvent(event)

    def hideEvent(self, event):
        self._fade_animation.stop(); self._clear_fade()
        if self._renderer is not None: self._renderer.hide()
        super().hideEvent(event)

    def showEvent(self, event):
        if self._presentation_pending: self._present_model()
        self._present_alerts()
        if self._renderer is not None: self._renderer.show()
        self._place_overlays(); super().showEvent(event)

    def shutdown(self):
        if self._closed: return
        self._closed = True
        self._activate_timer.stop(); self._fade_animation.stop(); self._clear_fade()
        self._retire_renderer()

    def closeEvent(self, event):
        self.shutdown(); super().closeEvent(event)
