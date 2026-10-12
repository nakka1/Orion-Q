"""Disposable globe of all published satellites; animation never predicts state."""
from __future__ import annotations

import math
import time
from functools import lru_cache
from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QPainter, QPainterPath, QPen, QPixmap, QRadialGradient, QFontMetricsF
from PySide6.QtWidgets import QWidget, QToolTip
from ..geodata import load_world
from ..theme import C, qc, mono, text as text_font, current_theme
from .figures import link_pen, marker_kind, marker_side, paint_marker, segment_distance
from .signal_geography import globe_cross_rect, paint_signal_cross, signal_cross_vector, signal_globe_ocean

EARTH_KM = 6371.
SATELLITE_ICON_PX = 32.  # logical screen pixels, independent of zoom
SATELLITE_HIT_PX = 48.
GLOBE_MAX_ZOOM = 6.


@lru_cache(maxsize=1)
def _world_vectors():
    """Immutable geography; no device observations or graphical resources.

    NumPy is already a core dependency. Project the entire catalog in native
    code, then transfer its coordinates once per camera to the existing Qt
    paths. Keeping this array avoids repeating trigonometry and allocations
    for every mouse movement and every renderer incarnation.
    """
    import numpy as np
    rings = [ring for polygon in load_world().land for ring in polygon.rings]
    vectors = []
    offsets = [0]
    for ring in rings:
        for longitude, latitude in ring:
            lon, lat = math.radians(longitude), math.radians(latitude)
            vectors.append((math.cos(lat)*math.cos(lon), math.cos(lat)*math.sin(lon), math.sin(lat)))
        offsets.append(len(vectors))
    matrix = np.asarray(vectors, dtype=np.float64)
    matrix.setflags(write=False)
    return matrix, tuple(offsets)


def _valid_vector(value):
    return isinstance(value, (list, tuple)) and len(value) == 3 and all(
        isinstance(v, (int, float)) and math.isfinite(v) for v in value)


class OrbitGlobe(QWidget):
    node_selected = Signal(str)
    link_selected = Signal(str)
    location_picked = Signal(float, float)
    pick_cancelled = Signal()
    view_changed = Signal()
    zoom_requested = Signal(float, QPointF)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.net = None
        self.signal_mode = False
        self.satellite_id = ''; self.hovered_id = ''; self.hovered_ground_id = ''; self.hovered_link = ''
        self.longitude = self.latitude = 0.
        self._zoom = 1.; self._extent = 1.2
        self._base = self._base_key = None
        self._orbit_key = None; self._orbit_paths = {}; self._icons = ()
        self._paths_key = None; self._tracks = (); self._positions_key = ()
        self._state_items = (); self._state_map = {}; self._nodes = {}; self._space_links = {}
        self._ground_key = None; self._ground_items = []
        self._connection_key = None; self._connections = []
        self._transmission_key = None; self._transmissions = []
        self._overlay = self._overlay_key = None
        self._projection_key = None; self._projection_values = None
        self._labels_key = None; self._labels = []
        self._clocks = {}; self._drag = None; self._pick = ''; self._closed = False
        self._dash_offsets = None
        self.earth_builds = self.orbit_builds = 0
        self.overlay_builds = self.ground_builds = self.connection_builds = 0
        self._timer = QTimer(self); self._timer.setInterval(33)
        self._timer.timeout.connect(self._animation_tick)
        self.setMouseTracking(True); self.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        self.setAccessibleName('Globo: todos os satélites e órbitas publicados pelo backend')
        self.setToolTip('Arraste para girar a Terra. Satélites têm prioridade visual e de seleção; instalações terrestres são referências discretas, acessíveis por clique e pelo seletor. Traços são órbitas; linhas finas são transmissões observadas. Marcadores ocultos pela Terra aparecem atenuados.')

    def set_signal_mode(self, enabled: bool) -> None:
        """Style the Earth without rebuilding orbital observations or picking."""
        enabled = bool(enabled)
        if self.signal_mode == enabled:
            return
        self.signal_mode = enabled
        self._base = self._base_key = None
        self.update()

    def _states(self):
        return self._state_items

    def set_model(self, net, ident='', *, clocks=None):
        self.net = net; self.satellite_id = ident
        states = net.satellites or {}
        self._state_items = tuple((node.id, states[node.id]) for node in net.nodes
                                  if node.physical_type == 'satellite' and node.id in states)
        self._state_map = dict(self._state_items)
        self._nodes = {node.id: node for node in net.nodes}
        self._space_links = {ident: [] for ident, _ in self._state_items}
        for link in net.links:
            if link.style == 'pair': continue
            for endpoint in (link.a, link.b):
                if endpoint in self._space_links: self._space_links[endpoint].append(link)
        # Normalize geometry only on delivery of a model. Orbital telemetry
        # remains the latest published state; animation never predicts it.
        self._tracks = tuple((key, tuple(tuple(vector) for vector in state.get('orbit_ecef_km', ())
                                        if _valid_vector(vector))) for key, state in self._state_items)
        self._positions_key = tuple((key, tuple(state.get('ecef_km')) if _valid_vector(state.get('ecef_km')) else ())
                                    for key, state in self._state_items)
        extents = [math.sqrt(sum(v*v for v in point))/EARTH_KM
                   for _, points in self._tracks for point in points]
        extents.extend(math.sqrt(sum(v*v for v in point))/EARTH_KM for _, point in self._positions_key if point)
        self._extent = max(1.05, max(extents, default=1.2))
        self._ground_key = self._connection_key = self._transmission_key = self._overlay_key = self._labels_key = None
        published = clocks
        wall = time.monotonic(); clocks = {}
        for key, state in self._states():
            clock = state.get('at_network_s', net.network_time_s)
            if not isinstance(clock, (int, float)) or not math.isfinite(clock): clock = net.network_time_s
            old = self._clocks.get(key)
            clocks[key] = old if old and old[0] == clock else (clock, max(0., clock-old[0]) if old else 0., wall)
        self._clocks = dict(published) if published is not None else clocks
        if self.hovered_id and not net.node(self.hovered_id): self.hovered_id = ''; QToolTip.hideText()
        if self.hovered_ground_id and not net.node(self.hovered_ground_id): self.hovered_ground_id = ''; QToolTip.hideText()
        self.update()

    def camera_state(self): return (self.longitude, self.latitude, self._zoom)

    def set_camera(self, longitude, latitude, zoom=1.):
        if not all(math.isfinite(float(v)) for v in (longitude, latitude, zoom)): return
        self.longitude = (float(longitude)+180.) % 360.-180.
        self.latitude = max(-85., min(85., float(latitude)))
        self._zoom = max(.5, min(GLOBE_MAX_ZOOM, float(zoom))); self.update()

    def showEvent(self, event):
        if not self._closed: self._timer.start()
        super().showEvent(event)

    def hideEvent(self, event):
        self._timer.stop(); QToolTip.hideText(); super().hideEvent(event)

    def radius(self):
        return max(.001, min(self.width(), max(1, self.height()-150))*.41/self._extent)*self._zoom

    def project(self, vector, radius=None):
        east_axis, north_axis, depth_axis = self._projection_basis()
        x, y, z = (v/EARTH_KM for v in vector)
        east = east_axis[0]*x+east_axis[1]*y
        north = north_axis[0]*x+north_axis[1]*y+north_axis[2]*z
        depth = depth_axis[0]*x+depth_axis[1]*y+depth_axis[2]*z
        radius = self.radius() if radius is None else radius
        return QPointF(self.width()/2+east*radius, self.height()/2-north*radius), depth

    def _projection_basis(self):
        key = (self.longitude, self.latitude)
        if key != self._projection_key:
            lon, lat = map(math.radians, key)
            sl, cl, sp, cp = math.sin(lon), math.cos(lon), math.sin(lat), math.cos(lat)
            self._projection_values = ((-sl, cl, 0.), (-sp*cl, -sp*sl, cp), (cp*cl, cp*sl, sp))
            self._projection_key = key
        return self._projection_values

    def _camera_key(self):
        return self.camera_state(), self.width(), self.height(), self._extent

    def lonlat_at(self, point):
        east = (point.x()-self.width()/2)/self.radius()
        north = (self.height()/2-point.y())/self.radius()
        square = east*east+north*north
        if square > 1.: return None
        depth = math.sqrt(max(0., 1.-square)); lo, la = map(math.radians, (self.longitude, self.latitude))
        x = depth*math.cos(la)*math.cos(lo)-east*math.sin(lo)-north*math.sin(la)*math.cos(lo)
        y = depth*math.cos(la)*math.sin(lo)+east*math.cos(lo)-north*math.sin(la)*math.sin(lo)
        z = depth*math.sin(la)+north*math.cos(la)
        return math.degrees(math.atan2(y, x)), math.degrees(math.asin(max(-1., min(1., z))))

    def zoom_by(self, factor, anchor=None):
        focus = self.lonlat_at(anchor) if anchor is not None else None
        self._zoom = max(.5, min(GLOBE_MAX_ZOOM, self._zoom*factor))
        if focus:
            # Off-centre projection is nonlinear. Solve the camera correction
            # iteratively so consecutive scrolls retain the same ground point.
            for _ in range(8):
                after=self.lonlat_at(anchor)
                if after is None:break
                dl=(focus[0]-after[0]+180.)%360.-180.; dp=focus[1]-after[1]
                if max(abs(dl),abs(dp))<1e-10:break
                self.longitude=(self.longitude+dl+180.)%360.-180.
                self.latitude=max(-85.,min(85.,self.latitude+dp))
        self.update(); self.view_changed.emit()

    def wheelEvent(self, event):
        delta = event.angleDelta().y() or event.pixelDelta().y()*3
        if delta: self.zoom_requested.emit(1.2**(max(-600, min(600, delta))/120.), event.position())
        event.accept()

    def _vector(self, node):
        lat, lon = map(math.radians, (node.latitude, node.longitude))
        return EARTH_KM*math.cos(lat)*math.cos(lon), EARTH_KM*math.cos(lat)*math.sin(lon), EARTH_KM*math.sin(lat)

    def _earth(self):
        from ..display import raster_scale_for
        ratio = raster_scale_for(self)
        key = (self.width(), self.height(), ratio, round(self.longitude, 3), round(self.latitude, 3),
               current_theme(), round(self.radius(), 2), self.signal_mode)
        if self._base_key == key: return self._base
        image = QPixmap(max(1, round(self.width()*ratio)), max(1, round(self.height()*ratio)))
        image.setDevicePixelRatio(ratio); image.fill(qc(C.bg))
        p = QPainter(image); p.setRenderHint(QPainter.RenderHint.Antialiasing)
        radius = self.radius(); centre = QPointF(self.width()/2, self.height()/2)
        dark = current_theme() == 'dark'
        ocean, limb, land = ('#355971', '#92b3c8', '#78877c') if dark else ('#bfdbe9', '#507c96', '#e7e9dc')
        shadow = '#1d3243' if dark else '#80aabd'
        if self.signal_mode:
            ocean, shadow, limb = signal_globe_ocean()
        shade = QRadialGradient(centre-QPointF(radius*.25, radius*.3), radius*1.5)
        shade.setColorAt(0, qc(ocean)); shade.setColorAt(1, qc(shadow))
        p.setBrush(shade); p.setPen(QPen(qc(limb), 1.2)); p.drawEllipse(centre, radius, radius)
        clip = QPainterPath(); clip.addEllipse(centre, radius, radius); p.setClipPath(clip)
        p.setPen(QPen(qc(limb), .65)); p.setBrush(qc(land))
        import numpy as np
        matrix, offsets = _world_vectors()
        projected = matrix @ np.asarray(self._projection_basis(), dtype=np.float64).T
        projected[:, 0] = self.width()/2+projected[:, 0]*radius
        projected[:, 1] = self.height()/2-projected[:, 1]*radius
        coordinates = projected.tolist()
        for first, last in zip(offsets[:-1], offsets[1:]):
            path = QPainterPath(); active = False
            for x, y, depth in coordinates[first:last]:
                if depth < 0:
                    if active: path.closeSubpath(); active = False
                elif not active: path.moveTo(x, y); active = True
                else: path.lineTo(x, y)
            if active: path.closeSubpath()
            p.drawPath(path)
        p.setBrush(Qt.BrushStyle.NoBrush); p.setPen(QPen(qc('rgba(219,238,249,.30)' if dark else 'rgba(52,86,108,.28)'), .6))
        for latitude in (-60, -30, 0, 30, 60):
            path = QPainterPath(); active = False
            for longitude in range(-180, 181, 4):
                la, lo = map(math.radians, (latitude, longitude))
                point, depth = self.project((EARTH_KM*math.cos(la)*math.cos(lo), EARTH_KM*math.cos(la)*math.sin(lo), EARTH_KM*math.sin(la)), radius)
                if depth >= 0:
                    if active: path.lineTo(point)
                    else: path.moveTo(point); active = True
                else: active = False
            p.drawPath(path)
        for longitude in range(-180, 180, 30):
            path = QPainterPath(); active = False
            for latitude in range(-90, 91, 4):
                la, lo = map(math.radians, (latitude, longitude))
                point, depth = self.project((EARTH_KM*math.cos(la)*math.cos(lo), EARTH_KM*math.cos(la)*math.sin(lo), EARTH_KM*math.sin(la)), radius)
                if depth >= 0:
                    if active: path.lineTo(point)
                    else: path.moveTo(point); active = True
                else: active = False
            p.drawPath(path)
        p.setClipping(False); p.setPen(QPen(qc(limb), 1.2)); p.drawEllipse(centre, radius, radius)
        if self.signal_mode:
            anchor, depth = self.project(signal_cross_vector(), radius)
            if depth >= 0:
                cross = globe_cross_rect(anchor, radius)
                if cross.intersects(QRectF(self.rect())):
                    paint_signal_cross(p, cross)
        p.end(); self._base_key = key; self._base = image; self.earth_builds += 1
        return image

    def _hidden(self, vector):
        point, depth = self.project(vector)
        return depth < 0 and math.hypot(point.x()-self.width()/2, point.y()-self.height()/2) < self.radius()

    def orbital_items(self):
        """Screen-space handles with leaders; physical coordinates stay intact."""
        states = self._states()
        camera = self._camera_key()
        paths_key = (camera, self._tracks)
        if paths_key != self._paths_key:
            paths = {}
            for ident, samples in self._tracks:
                if not samples: continue
                front, back = QPainterPath(), QPainterPath(); last_side = None
                for vector in samples:
                    point, depth = self.project(vector)
                    side = not (depth < 0 and math.hypot(point.x()-self.width()/2,
                                                          point.y()-self.height()/2) < self.radius())
                    path = front if side else back
                    if side == last_side: path.lineTo(point)
                    else: path.moveTo(point)
                    last_side = side
                paths[ident] = (front, back)
            self._paths_key = paths_key; self._orbit_paths = paths; self.orbit_builds += 1
        key = (camera, self._positions_key)
        if key == self._orbit_key:
            for item in self._icons: item['state'] = self._state_map[item['id']]
            return self._icons
        icons = []; taken = []
        bounds = QRectF(24, 150, max(1, self.width()-48), max(1, self.height()-265))
        for ident, state in sorted(states):
            vector = state.get('ecef_km')
            if not _valid_vector(vector): continue
            physical, _ = self.project(vector)
            centre = QPointF(max(bounds.left(), min(bounds.right(), physical.x())), max(bounds.top(), min(bounds.bottom(), physical.y())))
            candidate = centre
            for step in range(80):
                if not any((candidate-other).manhattanLength() < SATELLITE_HIT_PX for other in taken): break
                angle = step*2.399963; radius = 32.*math.sqrt(step+1)
                candidate = QPointF(max(bounds.left(), min(bounds.right(), centre.x()+math.cos(angle)*radius)), max(bounds.top(), min(bounds.bottom(), centre.y()+math.sin(angle)*radius)))
            taken.append(candidate)
            icons.append({'id': ident, 'state': state, 'physical': physical, 'centre': candidate,
                          'hidden': self._hidden(vector), 'size_px': SATELLITE_ICON_PX,
                          'hit': QRectF(candidate.x()-SATELLITE_HIT_PX/2, candidate.y()-SATELLITE_HIT_PX/2, SATELLITE_HIT_PX, SATELLITE_HIT_PX)})
        self._orbit_key = key; self._icons = tuple(icons)
        return self._icons

    def satellite_position(self, ident):
        state = self._state_map.get(ident, {}); vector = state.get('ecef_km')
        return self.project(vector)[0] if _valid_vector(vector) else None

    def hit_satellite(self, point):
        candidates = [item for item in self.orbital_items() if item['hit'].contains(point)]
        return min(candidates, key=lambda item: (item['centre']-point).manhattanLength())['id'] if candidates else ''

    def ground_marker_geometry(self):
        """Globe-only screen sizes. Map symbols and backend roles stay intact."""
        key = (self._camera_key(), self.net.selection if self.net else (), self.hovered_ground_id)
        if key == self._ground_key: return self._ground_items
        detail = max(0., min(1., (self.radius()-80.)/350.))
        sizes = {'endpoint': (9., 5.), 'core': (8., 4.), 'node': (5., 3.), 'repeater': (2.5, 2.)}
        items = []
        for node in self.net.located if self.net else ():
            if node.physical_type == 'satellite': continue
            point, depth = self.project(self._vector(node))
            if depth < 0: continue
            kind = marker_kind(node); low, extra = sizes.get(kind, sizes['node'])
            selected = self.net.selected('node', node.id)
            hover = node.id == self.hovered_ground_id
            side = low+extra*detail+(2. if selected or hover else 0.)
            hit_side = max(14., min(18., side+5.))
            items.append({'node': node, 'kind': kind, 'point': point, 'size_px': side,
                          'selected': selected, 'hover': hover,
                          'hit': QRectF(point.x()-hit_side/2, point.y()-hit_side/2, hit_side, hit_side)})
        self._ground_key = key; self._ground_items = items; self.ground_builds += 1
        return items

    def _endpoint_vector(self, ident):
        node = self._nodes.get(ident)
        if node is None: return None
        if node.physical_type == 'satellite':
            vector = self._state_map.get(ident, {}).get('ecef_km')
            return vector if _valid_vector(vector) else None
        return self._vector(node) if node.located else None

    def _segment_visible(self, a, b):
        """A backend link may be valid but its drawing lies behind the Earth."""
        # This only clips camera occlusion. Physical mutual visibility is
        # determined by the backend and never inferred as photon evidence here.
        return all(not self._hidden(tuple(a[i]+(b[i]-a[i])*step/12 for i in range(3)))
                   for step in range(13))

    def transmission_geometry(self):
        key = self._camera_key()
        if key == self._transmission_key: return self._transmissions
        lines = []
        seen = set()
        for ident, state in self._states():
            vector = state.get('ecef_km')
            if not _valid_vector(vector): continue
            for transmission in state.get('transmitting_links', ()):
                link_id = transmission.get('link_id')
                peer = transmission.get('peer_id') or transmission.get('station_id', '')
                target = self._endpoint_vector(peer)
                if target is not None and link_id not in seen:
                    seen.add(link_id)
                    lines.append((transmission, self.project(vector)[0], self.project(target)[0]))
        self._transmission_key = key; self._transmissions = lines
        return lines

    def overlay_geometry(self):
        state = self._state_map.get(self.satellite_id, {})
        keys = {item.get('link_id') for item in state.get('transmitting_links', ())}
        return state, [item for item in self.transmission_geometry() if item[0].get('link_id') in keys]

    def connection_geometry(self):
        """Persisted associations, including unavailable links; never photon evidence."""
        key = self._camera_key()
        if key == self._connection_key: return self._connections
        lines = []
        seen = set()
        for ident, state in self._states():
            vector = state.get('ecef_km')
            if not _valid_vector(vector): continue
            for link in self._space_links.get(ident, ()):
                target = self._endpoint_vector(link.b if link.a == ident else link.a)
                if target is not None and link.key not in seen:
                    seen.add(link.key)
                    lines.append((link, self.project(vector)[0], self.project(target)[0],
                                  self._segment_visible(vector, target)))
        self._connection_key = key; self._connections = lines; self.connection_builds += 1
        return lines

    def hit_link(self, point):
        candidates = [(segment_distance(point,a,b),link.key) for link,a,b,visible in self.connection_geometry() if visible]
        best = min(candidates, default=(float('inf'),''))
        return best[1] if best[0] <= 5 else ''

    def hit(self,point):
        ident=self.hit_satellite(point)
        if ident:return 'node',ident
        candidates = [item for item in self.ground_marker_geometry() if item['hit'].contains(point)]
        if candidates:
            item = min(candidates, key=lambda entry: (entry['point']-point).manhattanLength())
            return 'node', item['node'].id
        link=self.hit_link(point)
        return ('link',link) if link else (None,'')

    def label_geometry(self):
        ground_items = self.ground_marker_geometry()
        points = [(item['node'], item['point'], item['size_px']) for item in ground_items if item['node'].labelled]
        budget = 0 if self.radius() < 90. else min(12, max(2, int(self.radius()/35.)))
        focus = self.hovered_ground_id or (self.net.selection[1] if self.net and self.net.selection[0] == 'node' else '')
        icons = self.orbital_items()
        key = (self.camera_state(), self.width(), self.height(), focus, budget,
               tuple((n.id, n.map_label, marker_kind(n), round(p.x(), 2), round(p.y(), 2)) for n, p, _ in points),
               tuple((item['id'], round(item['centre'].x(),2), round(item['centre'].y(),2)) for item in icons))
        if self._labels_key == key: return self._labels
        metrics = QFontMetricsF(mono(9)); taken = [item['hit'].adjusted(-4,-4,4,4) for item in icons]; labels = []
        taken.extend(item['hit'] for item in ground_items)
        bounds = QRectF(10, 150, self.width()-20, self.height()-250)
        for node, point, side in sorted(points, key=lambda entry: (entry[0].id != focus, entry[0].network_role != 'core', entry[0].id)):
            if len(labels) >= budget and node.id != focus: continue
            width = metrics.horizontalAdvance(node.map_label)+6; height = metrics.height()+4; chosen = None
            for level in range(8):
                for direction in (1, -1):
                    gap=side/2+4
                    rect = QRectF(point.x()+gap if direction == 1 else point.x()-width-gap, point.y()-height-4+level*(height+2), width, height)
                    if bounds.contains(rect) and not any(rect.adjusted(-3, -2, 3, 2).intersects(r) for r in taken): chosen = rect; break
                if chosen: break
            if chosen: taken.append(chosen); labels.append((node, point, chosen))
        self._labels_key = key; self._labels = labels
        return labels

    def dash_offset(self, ident=None):
        ident = ident or self.satellite_id; state = self._state_map.get(ident, {})
        clock, delta, wall = self._clocks.get(ident, (0., 0., time.monotonic()))
        visual = clock+min(1., time.monotonic()-wall)*delta
        return -visual/max(1., state.get('period_s', 5400.))*5000.

    def _animation_tick(self):
        """Keep the timer, but paint only when a published dash phase moves.

        Camera/model/hover/theme invalidation remains independent of this
        animation callback. The existing one-second interpolation clamp
        never predicts a new satellite position or scientific observation.
        """
        if self._closed: return
        offsets = tuple((ident, self.dash_offset(ident)) for ident in self._orbit_paths)
        if offsets != self._dash_offsets:
            self._dash_offsets = offsets
            self.update()

    def paintEvent(self, event):
        if self._closed: return
        p = QPainter(self); p.setRenderHint(QPainter.RenderHint.Antialiasing); p.drawPixmap(0, 0, self._earth())
        icons = self.orbital_items()
        for ident, (front, back) in self._orbit_paths.items():
            selected = ident == self.satellite_id
            for path, visible in ((back, False), (front, True)):
                pen = QPen(qc(C.acc if selected and visible else C.violet if visible else C.dim), 2. if selected and visible else 1.1 if visible else .75)
                pen.setDashPattern([5., 5.]); pen.setDashOffset(self.dash_offset(ident))
                p.setPen(pen); p.setBrush(Qt.BrushStyle.NoBrush); p.drawPath(path)
        p.drawPixmap(0, 0, self._overlay_layer(icons))
        p.end()

    def _overlay_layer(self, icons):
        """Only dashed orbit paths animate between published observations.

        Connections, markers, labels and metadata keep the latest model. Their
        raster is invalidated on each publication, camera, hover, selection,
        theme or pixel-density change; scientific events are never throttled.
        """
        from ..display import raster_scale_for
        ratio = raster_scale_for(self)
        key = (self._camera_key(), ratio, current_theme(), self.satellite_id,
               self.hovered_id, self.hovered_ground_id, self.hovered_link,
               self.net.selection if self.net else ())
        if self._overlay is not None and self._overlay_key == key: return self._overlay
        image = QPixmap(max(1, round(self.width()*ratio)), max(1, round(self.height()*ratio)))
        image.setDevicePixelRatio(ratio); image.fill(Qt.GlobalColor.transparent)
        painter = QPainter(image); painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        self._paint_overlay(painter, icons)
        painter.end(); self._overlay = image; self._overlay_key = key; self.overlay_builds += 1
        return image

    def _paint_overlay(self, p, icons):
        emitting = {item.get('link_id') for _,state in self._states() for item in state.get('transmitting_links',())}
        connections = self.connection_geometry()
        for link,a,b,visible in connections:
            if visible and link.id not in emitting:
                p.setPen(link_pen('space_config',selected=self.net.selected('link',link.key) or self.hovered_link==link.key)); p.drawLine(a,b)
        visible_links = set(); p.setPen(link_pen('space'))
        for _, state in self._states():
            vector = state.get('ecef_km')
            if not _valid_vector(vector) or self._hidden(vector): continue
            for item in state.get('transmitting_links', ()):
                target = self._endpoint_vector(item.get('peer_id') or item.get('station_id', ''))
                if target is not None and self._segment_visible(vector, target): visible_links.add(item.get('link_id'))
        for transmission, a, b in self.transmission_geometry():
            key=transmission.get('link_id')
            if key in visible_links:
                p.setPen(link_pen('space',selected=self.net.selected('link',key) or self.hovered_link==key));p.drawLine(a, b)
        for item in sorted(self.ground_marker_geometry(),key=lambda entry: entry['node'].labelled):
            node, point, kind = item['node'], item['point'], item['kind']
            p.save()
            p.setOpacity(.4 if node.isolated else 1. if item['selected'] or item['hover'] else .7)
            p.translate(point); p.scale(item['size_px']/marker_side(kind), item['size_px']/marker_side(kind))
            paint_marker(p,QPointF(),kind,node.health,selected=item['selected'] or item['hover'],
                         calibrating=node.status=='calibrating')
            p.restore()
        for node, point, rect in self.label_geometry():
            p.setPen(QPen(qc(C.dim), .65)); p.drawLine(point, rect.center())
            ground = qc(C.bg); ground.setAlphaF(.8); p.fillRect(rect, ground)
            p.setFont(mono(9)); p.setPen(qc(C.ink2)); p.drawText(rect, Qt.AlignmentFlag.AlignCenter, node.map_label)
        for item in icons:
            point = item['centre']; physical = item['physical']; selected = item['id'] == self.satellite_id; hover = item['id'] == self.hovered_id
            colour = qc(C.acc if selected else C.green if hover else C.ink)
            if (point-physical).manhattanLength() > 1:
                p.setPen(QPen(qc(C.dim), .8, Qt.PenStyle.DotLine)); p.drawLine(physical, point); p.drawEllipse(physical, 2., 2.)
            p.setPen(QPen(colour, 2.4 if selected or hover else 1.6)); p.setBrush(qc(C.bg))
            p.drawEllipse(point, 20. if hover or selected else 18., 20. if hover or selected else 18.)
            p.setOpacity(.65 if item['hidden'] else 1.); p.drawRoundedRect(QRectF(point.x()-6, point.y()-9, 12, 18), 2, 2)
            p.setBrush(qc(C.violet)); p.drawRect(QRectF(point.x()-16, point.y()-6, 8, 12)); p.drawRect(QRectF(point.x()+8, point.y()-6, 8, 12))
            p.drawLine(point-QPointF(8, 0), point+QPointF(8, 0)); p.setOpacity(1.)
            if selected or hover:
                p.setFont(mono(11)); metrics = p.fontMetrics()
                label = item['state'].get('device_id', item['id'])+(' · oculto' if item['hidden'] else '')
                rect = QRectF(max(8., min(self.width()-metrics.horizontalAdvance(label)-16, point.x()-metrics.horizontalAdvance(label)/2-5)), point.y()+23, metrics.horizontalAdvance(label)+10, metrics.height()+4)
                p.fillRect(rect, qc(C.bg)); p.setPen(colour); p.drawText(rect, Qt.AlignmentFlag.AlignCenter, label)
        p.setFont(text_font(12)); p.setPen(qc(C.ink2)); state = self._state_map.get(self.satellite_id, {})
        total = sum(n.physical_type == 'satellite' for n in self.net.nodes) if self.net else 0
        if state:
            altitude = state.get('altitude_km'); height = f'{altitude:.1f} km' if isinstance(altitude, (float, int)) else 'altitude indisponível'
            title = f"{state.get('device_id', self.satellite_id)} · {state.get('orbital_class', '')} · {height} · origem: {state.get('origin', 'unavailable')}"
        else: title = 'Selecione um satélite para consultar observações e detalhes'
        p.drawText(QRectF(16, self.height()-82, self.width()-32, 26), Qt.AlignmentFlag.AlignCenter, title)
        missing = total-len(icons)
        p.drawText(QRectF(16, self.height()-54, self.width()-32, 40), Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap,
                   f'{len(icons)}/{total} satélites com posição · {len(self._orbit_paths)} órbitas publicadas · {len(visible_links)} transmissões visíveis'+(f' · {missing} sem efemérides' if missing else '')+'\nSolo discreto: clique ou use o seletor. Órbitas tracejadas; transmissões somente por observações validadas.')

    def set_pick(self, label):
        self._pick = label; self.setCursor(Qt.CursorShape.CrossCursor if label else Qt.CursorShape.ArrowCursor)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.setFocus(); self._drag = (event.position(), self.longitude, self.latitude)

    def mouseMoveEvent(self, event):
        if self._drag:
            start, lon, lat = self._drag
            self.longitude = (lon-(event.position().x()-start.x())*.3+180.) % 360.-180.
            self.latitude = max(-85., min(85., lat+(event.position().y()-start.y())*.3)); self.update(); self.view_changed.emit()
        elif not self._pick:
            kind,target=self.hit(event.position())
            link=target if kind=='link' else ''
            if link!=self.hovered_link:self.hovered_link=link;self.update()
            self.setCursor(Qt.CursorShape.PointingHandCursor if kind else Qt.CursorShape.ArrowCursor)
            ident = target if kind == 'node' and self._nodes[target].physical_type == 'satellite' else ''
            ground = target if kind == 'node' and not ident else ''
            if ground != self.hovered_ground_id:
                self.hovered_ground_id = ground; self.update()
            if ident != self.hovered_id:
                self.hovered_id = ident; self.update()
                if ident:
                    item = next(item for item in self.orbital_items() if item['id'] == ident); node = self.net.node(ident); state = item['state']
                    QToolTip.showText(event.globalPosition().toPoint(), f"{node.map_label} · {node.name or ident}\n{state.get('orbital_class', '')} · origem: {state.get('origin', 'unavailable')}\n"+('Oculto pela Terra; identificação acessível.' if item['hidden'] else 'Posição publicada pelo backend.')+'\nClique para abrir os detalhes.', self)
                else: QToolTip.hideText()
            if link:
                item=self.net.link(link)
                QToolTip.showText(event.globalPosition().toPoint(),f'{link} · {item.a} ↔ {item.b}\n'+
                    ('Emissão no último bloco recebido' if item.style=='space' else 'Associação cadastrada; sem emissão comprovada')+
                    '\nClique para inspecionar protocolo, métricas e os dois canais.',self)
            elif ground:
                node=self.net.node(ground)
                QToolTip.showText(event.globalPosition().toPoint(),f'{node.map_label} · {node.name or node.id}\n'
                    f'{node.network_role} · {node.physical_type or "instalação terrestre"} · {node.status or node.health}\n'
                    'Clique para consultar capacidades, conexões e atividade no backend.',self)

    def mouseReleaseEvent(self, event):
        if self._drag and (event.position()-self._drag[0]).manhattanLength() < 4:
            if self._pick:
                position = self.lonlat_at(event.position())
                if position: self.set_pick(''); self.location_picked.emit(position[1], position[0])
            else:
                kind,ident=self.hit(event.position())
                if kind=='node':
                    if self.net.node(ident).physical_type=='satellite':self.satellite_id=ident
                    self.update();self.node_selected.emit(ident)
                elif kind=='link':self.link_selected.emit(ident)
        self._drag = None

    def leaveEvent(self, event):
        self.hovered_id = ''; self.hovered_ground_id = '';self.hovered_link=''; QToolTip.hideText(); self.update(); super().leaveEvent(event)

    def keyPressEvent(self, event):
        if self._pick and event.key() == Qt.Key.Key_Escape:
            self.set_pick(''); self.pick_cancelled.emit(); event.accept(); return
        super().keyPressEvent(event)

    def shutdown(self):
        if self._closed: return
        self._closed = True; self._timer.stop(); self._timer.timeout.disconnect(self._animation_tick)
        self._base = self._base_key = self._orbit_key = None
        self._overlay = self._overlay_key = None
        self._paths_key = None; self._tracks = (); self._positions_key = ()
        self._state_items = (); self._state_map = {}; self._nodes = {}; self._space_links = {}
        self._ground_key = None; self._ground_items = []
        self._connection_key = None; self._connections = []
        self._transmission_key = None; self._transmissions = []
        self._projection_key = self._projection_values = None
        self._orbit_paths = {}; self._icons = (); self._labels = []; self._labels_key = None
        self._clocks = {}; self._dash_offsets = None; self._drag = None; self.hovered_ground_id = ''; self.net = None; QToolTip.hideText()
