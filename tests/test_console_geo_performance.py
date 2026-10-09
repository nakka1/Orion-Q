"""Presentation caches preserve published geometry, state and disposal.

The small geometry fixture is deliberately not evidence of photon transmission
or protocol performance. End-to-end performance uses captured backend models.
"""
import math
import os
from dataclasses import replace
from types import SimpleNamespace

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import pytest
pytest.importorskip('PySide6.QtWidgets')
import shiboken6
from PySide6.QtCore import QCoreApplication, QEvent, QPointF, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from quantum_autopilot.console import theme
from quantum_autopilot.console.contract import Link, Node
from quantum_autopilot.console.widgets.geo_view import GeoMap
from quantum_autopilot.console.widgets.figures import marker_hit_rect, node_rect
from quantum_autopilot.console.widgets.network_views import build_net_model
from quantum_autopilot.console.widgets import orbit_globe as globe_module
from quantum_autopilot.console.widgets.orbit_globe import EARTH_KM, OrbitGlobe, _world_vectors


@pytest.fixture(scope='module')
def app():
    instance = QApplication.instance() or QApplication([])
    theme.set_theme('dark'); theme.install(instance)
    return instance


def settle(app):
    app.processEvents(); QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    app.processEvents()


def model():
    nodes = [Node('OGS', 'ENDPOINT', latitude=0., longitude=0., network_role='endpoint'),
             Node('CORE', 'CORE', latitude=15., longitude=20., network_role='core')]
    states = {}
    for index in range(3):
        ident = f'SAT-{index}'
        nodes.append(Node(ident, 'REPEATER', physical_type='satellite', network_role='repeater'))
        orbit = [[6900.*math.cos(t), 6900.*math.sin(t), 0.]
                 for t in [index*.4+step*math.pi/96 for step in range(193)]]
        states[ident] = {'device_id': ident, 'ecef_km': orbit[0], 'orbit_ecef_km': orbit,
                         'at_network_s': 1., 'period_s': 5400., 'origin': 'geometry-only test fixture',
                         'transmitting_links': [{'link_id': 'OBS-'+ident, 'station_id': 'OGS'}]}
    links = [Link('OGS', f'SAT-{i}', '', id='OBS-'+f'SAT-{i}') for i in range(3)]
    snap = SimpleNamespace(topology=SimpleNamespace(nodes=nodes, links=links), live=None)
    return replace(build_net_model(snap), satellites=states, network_time_s=1.)


@pytest.fixture
def globe(app):
    widget = OrbitGlobe(); widget.resize(1280, 900); widget.set_model(model()); widget.show(); settle(app)
    yield widget
    widget.shutdown(); widget.close(); widget.deleteLater(); settle(app)


def test_globe_animation_reuses_nonanimated_geometry_and_raster(globe, app):
    globe.set_camera(0., 0., 1.); globe.grab()
    counts = (globe.earth_builds, globe.orbit_builds, globe.ground_builds,
              globe.connection_builds, globe.overlay_builds)
    for _ in range(24): globe.repaint(); settle(app)
    assert (globe.earth_builds, globe.orbit_builds, globe.ground_builds,
            globe.connection_builds, globe.overlay_builds) == counts
    assert len(globe.orbital_items()) == len(globe._orbit_paths) == 3
    assert _world_vectors()[0].flags.writeable is False


def test_latest_state_and_photon_evidence_replace_cached_presentation(globe, app, monkeypatch):
    globe.set_camera(0., 0., 1.); first = globe.grab().toImage()
    original = globe.net; paths = dict(globe._orbit_paths); builds = globe.orbit_builds
    nodes = tuple(replace(node, health='down', status='down') if node.id == 'OGS' else node for node in original.nodes)
    states = {key: {**state, 'origin': 'new received observation', 'transmitting_links': [], 'at_network_s': 2.}
              for key, state in original.satellites.items()}
    latest = replace(original, nodes=nodes, satellites=states, network_time_s=2.)
    globe.set_model(latest, 'SAT-0'); second = globe.grab().toImage()
    assert globe.orbit_builds == builds and globe._orbit_paths == paths
    assert next(item for item in globe.ground_marker_geometry() if item['node'].id == 'OGS')['node'] is nodes[0]
    assert all(item['state'] is states[item['id']] for item in globe.orbital_items())
    assert not globe.transmission_geometry() and len(globe.connection_geometry()) == 3
    assert second != first
    # Test the bounded visual interpolation deterministically. An overloaded
    # runner may spend >1 s on painting, after which the intended clamp stops
    # further movement until another backend publication.
    position = globe.satellite_position('SAT-0'); wall = globe._clocks['SAT-0'][2]
    monkeypatch.setattr(globe_module.time, 'monotonic', lambda: wall+.1)
    before = globe.dash_offset('SAT-0')
    monkeypatch.setattr(globe_module.time, 'monotonic', lambda: wall+.2)
    assert globe.dash_offset('SAT-0') < before and globe.satellite_position('SAT-0') == position


def test_satellite_movement_does_not_rebuild_unchanged_orbit(globe):
    globe.grab(); builds = globe.orbit_builds; old = globe.satellite_position('SAT-0')
    states = dict(globe.net.satellites)
    # Same orbital radius keeps the scene extent unchanged. Only the published
    # position changes; the other satellites and track remain untouched.
    angle = .17
    states['SAT-0'] = {**states['SAT-0'], 'ecef_km': [6900*math.cos(angle), 6900*math.sin(angle), 0.]}
    globe.set_model(replace(globe.net, satellites=states)); globe.grab()
    assert globe.orbit_builds == builds
    assert globe.satellite_position('SAT-0') != old
    assert globe.satellite_position('SAT-0') == globe.project(states['SAT-0']['ecef_km'])[0]
    changed = dict(states); track = [list(vector) for vector in changed['SAT-0']['orbit_ecef_km']]
    track[1][2] = 1.
    changed['SAT-0'] = {**changed['SAT-0'], 'orbit_ecef_km': track}
    globe.set_model(replace(globe.net, satellites=changed)); globe.grab()
    assert globe.orbit_builds == builds+1


def test_camera_theme_hover_and_selection_invalidate_only_needed_layers(globe):
    globe.set_camera(0., 0., 1.); globe.grab()
    counts = (globe.earth_builds, globe.orbit_builds, globe.overlay_builds)
    globe.hovered_id = 'SAT-1'; globe.grab()
    assert (globe.earth_builds, globe.orbit_builds, globe.overlay_builds) == (counts[0], counts[1], counts[2]+1)
    globe.satellite_id = 'SAT-2'; globe.grab()
    assert globe.orbit_builds == counts[1]
    globe.set_camera(10., -5., 1.); globe.grab()
    assert globe.earth_builds == counts[0]+1 and globe.orbit_builds == counts[1]+1
    overlay = globe.overlay_builds; earth = globe.earth_builds
    try:
        theme.set_theme('light'); globe.grab()
        assert globe.overlay_builds == overlay+1 and globe.earth_builds == earth+1
    finally: theme.set_theme('dark')


def test_projection_cardinal_geometry_and_inverse_are_preserved(globe):
    centre = QPointF(globe.width()/2, globe.height()/2)
    globe.set_camera(0., 0., 1.); radius = globe.radius()
    assert globe.project((EARTH_KM, 0., 0.)) == (centre, 1.)
    assert globe.project((0., EARTH_KM, 0.)) == (centre+QPointF(radius, 0.), 0.)
    assert globe.project((0., 0., EARTH_KM)) == (centre-QPointF(0., radius), 0.)
    assert globe.project((-EARTH_KM, 0., 0.)) == (centre, -1.)
    for longitude, latitude in ((39., -22.), (-73., 41.), (121., 31.)):
        globe.set_camera(longitude, latitude, 2.)
        lat, lon = math.radians(latitude), math.radians(longitude)
        vector = (EARTH_KM*math.cos(lat)*math.cos(lon), EARTH_KM*math.cos(lat)*math.sin(lon), EARTH_KM*math.sin(lat))
        point, depth = globe.project(vector)
        assert point.x() == pytest.approx(centre.x(), abs=1e-10)
        assert point.y() == pytest.approx(centre.y(), abs=1e-10)
        assert depth == pytest.approx(1., abs=1e-12)
        assert globe.lonlat_at(point) == pytest.approx((longitude, latitude), abs=1e-10)


def test_terrestrial_map_cache_ignores_undrawn_science_but_keeps_latest_model(app):
    geo = GeoMap(); geo.resize(1280, 900); original = model(); geo.set_model(original); geo.show(); settle(app)
    try:
        geo.grab(); surface = geo.renderer._surface; key = geo.renderer._surface_key
        states = {ident: {**state, 'origin': 'latest science', 'at_network_s': 22.} for ident,state in original.satellites.items()}
        latest = replace(original, satellites=states, network_time_s=22., communicators={'new': {'physical_type': 'satellite'}})
        geo.set_model(latest); geo.grab()
        assert geo.renderer.net is latest and geo.net is latest
        assert geo.renderer._surface is surface and geo.renderer._surface_key == key
        nodes = tuple(replace(node, health='down', status='down') if node.id == 'OGS' else node for node in latest.nodes)
        geo.set_model(replace(latest, nodes=nodes)); geo.grab()
        assert geo.renderer._surface is not surface
    finally: geo.shutdown(); geo.close(); geo.deleteLater(); settle(app)


def test_bounded_transition_never_grabs_full_window(app, monkeypatch):
    geo = GeoMap(); geo.resize(3440, 1440); geo.set_model(model()); geo.show(); settle(app)
    try:
        old = geo.renderer
        def forbidden(*args, **kwargs): raise AssertionError('full-size capture invoked')
        monkeypatch.setattr(old, 'grab', forbidden)
        camera = geo.view_state(); geo.set_mode('globe')
        assert geo._fade.image.width() <= 1200 and geo._fade.image.height() <= 800
        settle(app)
        assert geo.scene.map_view == camera and not shiboken6.isValid(old)
        assert geo.resource_stats()['renderer_instances'] == 1
    finally: geo.shutdown(); geo.close(); geo.deleteLater(); settle(app)


def test_eighty_swaps_discard_graphics_and_observation_references(app):
    geo = GeoMap(); geo.resize(640, 480); net = model(); geo.set_model(net); geo.show(); settle(app)
    try:
        for _ in range(80):
            old = geo.renderer; geo.toggle_mode()
            if isinstance(old, OrbitGlobe):
                assert old._overlay is None and old._base is None and not old._timer.isActive()
                assert not old._state_items and not old._nodes and not old._ground_items
                assert not old._connections and not old._transmissions and not old._tracks
                assert old.net is None
            settle(app)
            assert not shiboken6.isValid(old)
            assert geo.resource_stats()['renderer_instances'] == 1
            assert geo.net is net and geo.renderer.net is net
        QTest.qWait(380); settle(app)
        assert not geo.resource_stats()['transition_raster']
    finally:
        geo.shutdown(); settle(app)
        assert geo.resource_stats()['renderer_instances'] == 0
        geo.close(); geo.deleteLater(); settle(app)


def test_physical_marker_precedes_other_institution_label(app, monkeypatch):
    nodes = [Node('IME-USP', 'ENDPOINT', latitude=0., longitude=0., network_role='endpoint'),
             Node('UNICAMP', 'ENDPOINT', latitude=0., longitude=.4, network_role='endpoint')]
    net = build_net_model(SimpleNamespace(topology=SimpleNamespace(nodes=nodes, links=[]), live=None))
    geo = GeoMap(); geo.resize(1000, 650); geo.set_model(net); geo.show(); settle(app)
    try:
        geo.set_view_state((0., 0., .001)); renderer = geo.renderer
        monkeypatch.setattr(renderer, 'label_sides', lambda transform=None: {'IME-USP': 'right', 'UNICAMP': 'above'})
        a = geo.node_position('IME-USP'); b = geo.node_position('UNICAMP')
        assert node_rect(a, net.node('IME-USP'), side='right').contains(b)
        assert not marker_hit_rect(a, net.node('IME-USP')).contains(b)
        assert renderer.hit(b) == ('node', 'UNICAMP')
        selected = []; geo.node_selected.connect(selected.append)
        QTest.mouseClick(renderer, Qt.MouseButton.LeftButton, pos=b.toPoint()); settle(app)
        assert selected[-1] == 'UNICAMP'
        # At campus zoom, both centres remain individually accessible without
        # shifting their verified coordinates.
        geo.set_view_state((0., 0., .004))
        assert renderer.hit(geo.node_position('IME-USP')) == ('node', 'IME-USP')
        assert renderer.hit(geo.node_position('UNICAMP')) == ('node', 'UNICAMP')
        assert [(node.longitude,node.latitude) for node in geo.net.nodes] == [(0.,0.),(.4,0.)]
    finally: geo.shutdown(); geo.close(); geo.deleteLater(); settle(app)


def test_labels_remain_clickable_without_marker_and_coincident_focus_is_explicit(app, monkeypatch):
    nodes = [Node('INSTITUTION-LONG-LABEL', 'ENDPOINT', latitude=0., longitude=0., network_role='endpoint'),
             Node('SAME-LOCATION', 'ENDPOINT', latitude=0., longitude=0., network_role='endpoint')]
    net = build_net_model(SimpleNamespace(topology=SimpleNamespace(nodes=nodes, links=[]), live=None))
    geo = GeoMap(); geo.resize(1000, 650); geo.set_model(net); geo.show(); settle(app)
    try:
        renderer = geo.renderer
        monkeypatch.setattr(renderer, 'label_sides', lambda transform=None: {'INSTITUTION-LONG-LABEL': 'right', 'SAME-LOCATION': 'left'})
        centre = geo.node_position('INSTITUTION-LONG-LABEL'); label = centre+QPointF(60., 0.)
        assert not any(marker_hit_rect(centre,node).contains(label) for node in net.nodes)
        assert renderer.hit(label) == ('node', 'INSTITUTION-LONG-LABEL')
        assert renderer.hit(centre) == ('node', 'INSTITUTION-LONG-LABEL')
        geo.focus_node.setCurrentIndex(geo.focus_node.findData('SAME-LOCATION'))
        assert renderer.hit(geo.node_position('SAME-LOCATION')) == ('node', 'SAME-LOCATION')
        # Exact coincidence cannot promise independent picking at this zoom:
        # the inventory selector disambiguates, without invented map offsets.
        assert geo.node_position('SAME-LOCATION') == geo.node_position('INSTITUTION-LONG-LABEL')
    finally: geo.shutdown(); geo.close(); geo.deleteLater(); settle(app)


def test_hidden_map_metadata_is_fresh_without_layout_or_renderer_work(app, monkeypatch):
    geo = GeoMap(); geo.resize(1000, 650); original = model(); geo.set_model(original); geo.show(); settle(app)
    geo.grab(); old = geo.renderer; surface = old._surface; geo.hide(); settle(app)
    calls = []; apply = geo._apply_model
    def observed_apply():
        calls.append('apply'); return apply()
    monkeypatch.setattr(geo, '_apply_model', observed_apply)
    try:
        nodes = tuple(replace(node, latitude=5., longitude=7., health='down', status='down')
                      if node.id == 'OGS' else node for node in original.nodes)
        new = replace(nodes[0], id='NEW-ENDPOINT', longitude=8.)
        latest = replace(original, nodes=(*nodes, new), selection=('node', 'OGS'), network_time_s=2.)
        stats = geo.resource_stats(); focus_count = geo.focus_node.count()
        geo.set_model_state(latest)
        assert not calls and geo.resource_stats() == stats
        assert geo.net is latest and geo.scene.last_selection == latest.selection
        assert 'NEW-ENDPOINT' in geo.placed_ids() and geo.net.node('OGS').status == 'down'
        point = geo.node_position('NEW-ENDPOINT')
        assert point == QPointF(*geo.transform().to_px(8., 5.))
        assert old._surface is surface and geo.focus_node.count() == focus_count
        assert {link.key for link, *_ in geo.segments()} == geo.drawn_link_keys()
        geo.show(); settle(app)
        assert calls == ['apply'] and geo.renderer is old and old.net is latest
        assert geo.focus_node.count() == len(latest.nodes)+1
        assert geo.focus_node.findData('NEW-ENDPOINT') >= 0
    finally: geo.shutdown(); geo.close(); geo.deleteLater(); settle(app)


def test_hidden_globe_metadata_tracks_orbital_clock_and_positions_without_rebuilding(app):
    geo = GeoMap(); geo.resize(1000, 650); original = model(); geo.set_model(original); geo.show(); settle(app)
    geo.set_mode('globe'); settle(app); geo.grab(); globe = geo.globe; geo.hide(); settle(app)
    try:
        counts = (globe.earth_builds, globe.orbit_builds, globe.overlay_builds)
        for clock in range(2, 8):
            angle = clock*.02
            states = {key: {**state, 'at_network_s': float(clock), 'transmitting_links': []}
                      for key,state in original.satellites.items()}
            states['SAT-0'] = {**states['SAT-0'], 'ecef_km': [6900*math.cos(angle),6900*math.sin(angle),0.]}
            states.pop('SAT-1')
            latest = replace(original, satellites=states, network_time_s=float(clock), selection=('node','SAT-0'))
            geo.set_model_state(latest)
        assert geo.net is latest and geo.scene.satellite_id == 'SAT-0'
        assert geo.scene.orbit_clocks['SAT-0'][:2] == (7., 1.)
        assert not globe._timer.isActive()
        assert (globe.earth_builds, globe.orbit_builds, globe.overlay_builds) == counts
        assert geo.node_position('SAT-0') == globe.project(states['SAT-0']['ecef_km'])[0]
        assert geo.drawn_link_keys() == {'OBS-SAT-0','OBS-SAT-2'}
        geo.show(); settle(app); geo.grab()
        assert geo.globe is globe and globe.net is latest and globe._timer.isActive()
        assert len(globe.orbital_items()) == 2 and not globe.transmission_geometry()
        assert globe._clocks == geo.scene.orbit_clocks
    finally: geo.shutdown(); geo.close(); geo.deleteLater(); settle(app)


def test_hidden_alert_metadata_defers_rows_and_replaces_them_on_reopen(app, monkeypatch):
    geo = GeoMap(); geo.resize(1000, 650); geo.set_model(model()); geo.show(); settle(app)
    first = (('D-1', 'No route', 'Loss exceeds operating budget'),)
    geo.set_alerts(first); old_row = geo.alert_lay.itemAt(0).widget()
    geo.hide(); settle(app); calls = []; layout = geo._place_overlays
    def observed_layout():
        calls.append('layout'); return layout()
    monkeypatch.setattr(geo, '_place_overlays', observed_layout)
    try:
        latest = (('D-2', 'Waiting for resources', 'Memory resources occupied'),
                  ('D-3', 'Link unavailable', 'Station below elevation mask'))
        geo.set_alerts(latest, presentation=False)
        assert geo.alerts == latest and geo._alerts == first and not calls
        assert geo.alert_lay.count() == 1 and geo.alert_lay.itemAt(0).widget() is old_row
        geo.show(); settle(app)
        assert geo._alerts == latest and geo.alert_lay.count() == 2 and calls
        assert geo.alert_lay.itemAt(0).widget() is not old_row
        geo.hide(); settle(app); calls.clear()
        geo.set_alerts([], presentation=False)
        assert geo.alerts == () and geo._alerts == latest and not calls
        geo.show(); settle(app)
        assert geo.alert_lay.count() == 0 and geo._alerts == () and geo._info_height == 0
    finally: geo.shutdown(); geo.close(); geo.deleteLater(); settle(app)
