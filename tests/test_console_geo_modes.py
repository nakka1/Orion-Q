"""Renderer lifecycle, input and shared observations, including adverse swaps.

Small synthetic observation fixtures exercise geometry only; protocol/physical
correctness remains covered by the real satellite and communicator campaigns.
"""
import math
import os
from dataclasses import replace
from types import SimpleNamespace

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import pytest
pytest.importorskip('PySide6.QtWidgets')
import shiboken6
from PySide6.QtCore import QPoint, QPointF, QCoreApplication, QEvent, Qt
from PySide6.QtGui import QImage, QPainter, QWheelEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from quantum_autopilot.console import theme
from quantum_autopilot.console.contract import Node, Link
from quantum_autopilot.console.widgets.geo_map import GeoMap, MapRenderer
from quantum_autopilot.console.widgets.geo_view import MAP_TO_GLOBE_SPAN_M, GLOBE_TO_MAP_SPAN_M
from quantum_autopilot.console.widgets.network_views import build_net_model
from quantum_autopilot.console.widgets.orbit_globe import OrbitGlobe, SATELLITE_ICON_PX, SATELLITE_HIT_PX


@pytest.fixture(scope='module')
def app():
    app = QApplication.instance() or QApplication([])
    theme.set_theme('dark'); theme.install(app)
    return app


def settle(app):
    app.processEvents()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    app.processEvents(); QTest.qWait(2)


def model(count=4, coincident=False):
    nodes = [Node('OGS', 'CORE', latitude=-22.95, longitude=-43.17, network_role='core'),
             Node('UNKNOWN', 'ENDPOINT', network_role='endpoint')]
    states = {}; records = {}
    for i in range(count):
        ident = f'SAT-{i}'
        nodes.append(Node(ident, 'REPEATER', physical_type='satellite', network_role='repeater',
                          kind='satellite', name=f'Satellite {i}'))
        phase = 0 if coincident else i*math.pi/2
        orbit = [[6900*math.cos(t+phase), 6900*math.sin(t+phase)*math.cos(i*.3), 6900*math.sin(t+phase)*math.sin(i*.3)]
                 for t in [n*math.pi/96 for n in range(193)]]
        states[ident] = {'device_id': ident, 'ecef_km': orbit[0], 'orbit_ecef_km': orbit,
                         'at_network_s': 1., 'period_s': 5400., 'origin': 'synthetic geometry fixture',
                         'transmitting_links': [{'link_id': 'TEST-'+ident, 'station_id': 'OGS'}]}
        records[ident] = {'physical_type': 'satellite'}
    snap = SimpleNamespace(topology=SimpleNamespace(nodes=nodes, links=[Link('OGS', 'UNKNOWN', 'no geographic line')]), live=None)
    return replace(build_net_model(snap), satellites=states, communicators=records, network_time_s=1.)


@pytest.fixture
def geo(app):
    geo = GeoMap(); geo.resize(1200, 800); geo.set_model(model()); geo.show(); settle(app)
    yield geo
    geo.shutdown(); geo.close(); geo.deleteLater(); settle(app)


def test_all_satellites_orbits_and_selection_preserve_camera(geo, app):
    original = geo.renderer
    geo.mode_button.click(); settle(app)
    assert not shiboken6.isValid(original) and isinstance(geo.renderer, OrbitGlobe)
    globe = geo.globe; globe.set_camera(0, 0, .5); globe.grab()
    items = globe.orbital_items()
    assert {i['id'] for i in items} == set(geo.net.satellites)
    assert set(globe._orbit_paths) == set(geo.net.satellites)
    assert all(not front.isEmpty() or not back.isEmpty() for front, back in globe._orbit_paths.values())
    earth_builds = globe.earth_builds; orbit_builds = globe.orbit_builds
    camera = globe.camera_state(); received = []
    geo.node_selected.connect(received.append)
    for item in items:
        QTest.mouseClick(globe, Qt.MouseButton.LeftButton, pos=item['centre'].toPoint()); globe.grab()
        assert globe.satellite_id == item['id'] and geo.scene.satellite_id == item['id']
        assert globe.camera_state() == camera and len(globe.orbital_items()) == 4
    assert received == [item['id'] for item in items]
    assert globe.earth_builds == earth_builds and globe.orbit_builds == orbit_builds
    assert len(globe.transmission_geometry()) == 4


def test_campus_hit_uses_current_size_before_first_show(app):
    geo = GeoMap(); geo.resize(1000, 650); geo.set_model(model())
    try:
        geo.focus_node.setCurrentIndex(geo.focus_node.findData('OGS'))
        point = geo.node_position('OGS')
        assert point == QPointF(500, 325) and geo.hit(point) == ('node', 'OGS')
    finally: geo.shutdown(); geo.close(); geo.deleteLater(); settle(app)


@pytest.mark.parametrize('size', [(640, 480), (1200, 800), (3440, 1440)])
@pytest.mark.parametrize('zoom', [.5, 1., 4., 6.])
def test_icons_remain_large_and_clickable_with_hover(geo, app, size, zoom):
    geo.resize(*size); geo.set_mode('globe'); settle(app)
    globe = geo.globe; globe.set_camera(0, 0, zoom); globe.grab()
    for item in globe.orbital_items():
        assert item['size_px'] >= SATELLITE_ICON_PX == 32
        assert item['hit'].width() == SATELLITE_HIT_PX == 48
        assert globe.rect().contains(item['hit'].toRect())
        QTest.mouseMove(globe, item['centre'].toPoint()); app.processEvents()
        assert globe.hovered_id == item['id']
        assert globe.cursor().shape() == Qt.CursorShape.PointingHandCursor
        assert globe.hit_satellite(item['centre']+QPointF(21, 0)) == item['id']
    assert geo.mode_button.isVisible() and geo.controls.geometry().right() < geo.width()


def test_overlapping_satellites_have_separate_handles_with_truthful_leaders(geo, app):
    geo.set_model(model(coincident=True)); geo.set_mode('globe'); settle(app)
    globe = geo.globe; globe.set_camera(0, 0); items = globe.orbital_items()
    assert len({(i['centre'].x(), i['centre'].y()) for i in items}) == 4
    assert len({(i['physical'].x(), i['physical'].y()) for i in items}) == 1
    assert all(globe.hit_satellite(i['centre']) == i['id'] for i in items)


def test_hover_routes_through_qwindow_with_overlapping_test_windows(geo, app):
    from PySide6.QtWidgets import QWidget
    other = QWidget(); other.setGeometry(geo.geometry()); other.show()
    geo.set_mode('globe'); settle(app)
    try:
        for item in geo.globe.orbital_items():
            QTest.mouseMove(geo.windowHandle(), item['centre'].toPoint()); app.processEvents()
            assert geo.globe.hovered_id == item['id']
    finally: other.close(); other.deleteLater(); settle(app)


def wheel(target, delta, at=None):
    at = at or QPointF(target.width()/2, target.height()/2)
    event = QWheelEvent(at, QPointF(target.mapToGlobal(at.toPoint())), QPoint(), QPoint(0, delta),
                        Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier, Qt.ScrollPhase.NoScrollPhase, False)
    QApplication.sendEvent(target, event)


def test_button_and_scroll_hysteresis_preserve_focal_region(geo, app):
    geo.set_view_state((-43.17, -22.95, geo.width()/(MAP_TO_GLOBE_SPAN_M*.99)))
    assert geo.mode_button.text() == 'Mapa → Globo'
    wheel(geo.renderer, -120); settle(app)
    assert geo.mode == 'globe' and geo.mode_button.text() == 'Globo → Mapa'
    assert geo.globe.camera_state()[:2] == pytest.approx((-43.17, -22.95))
    wheel(geo.globe, 600); settle(app)
    assert geo.mode == 'globe'
    for _ in range(4):
        wheel(geo.globe, 120); settle(app)
        assert geo.mode == 'globe'
    wheel(geo.globe, 120); settle(app)
    assert geo.mode == 'map' and geo.view_state()[:2] == pytest.approx((-43.17, -22.95))
    assert geo.metres_per_px*geo.width() == pytest.approx(GLOBE_TO_MAP_SPAN_M)
    wheel(geo.renderer, -120); settle(app)
    assert geo.mode == 'map'  # return threshold is substantially inside entry
    previous = geo.view_state(); geo.mode_button.click(); settle(app)
    geo.mode_button.click(); settle(app)
    assert geo.view_state() == pytest.approx(previous)


def test_globe_drag_and_offcentre_zoom_keep_focused_region(geo, app):
    geo.set_mode('globe', camera=(40, 20, 1.)); settle(app)
    globe = geo.globe
    start = QPoint(globe.width()//2, globe.height()//2)
    QTest.mousePress(globe, Qt.MouseButton.LeftButton, pos=start)
    QTest.mouseMove(globe, start+QPoint(100, 40))
    QTest.mouseRelease(globe, Qt.MouseButton.LeftButton, pos=start+QPoint(100, 40))
    assert globe.camera_state()[:2] == pytest.approx((10., 32.))
    point = QPointF(globe.width()/2+35, globe.height()/2)
    focus = globe.lonlat_at(point)
    wheel(globe, 600, point); settle(app)
    for _ in range(4):
        wheel(globe, 120, point); settle(app)
    wheel(globe, 120, point); settle(app)
    assert geo.mode == 'map' and geo.view_state()[:2] == pytest.approx(focus)


def test_backend_updates_during_retirement_and_no_invented_positions(geo, app):
    latest = model(); satellites = dict(latest.satellites)
    satellites['SAT-0'] = {**satellites['SAT-0'], 'ecef_km': [6700, 1200, 200], 'at_network_s': 2.}
    latest = replace(latest, satellites=satellites, selection=('node', 'SAT-2'), layer='entanglement', route=('OGS',), demand_id='DEMAND')
    geo.set_mode('globe'); geo.set_model(latest); settle(app)
    assert geo.net is latest and geo.globe.net is latest and geo.scene.satellite_id == 'SAT-2'
    assert geo.globe.satellite_position('SAT-0') == geo.globe.project([6700, 1200, 200])[0]
    satellites = dict(satellites); satellites['SAT-3'] = {'origin': 'no observations'}
    no_position = replace(latest, satellites=satellites)
    geo.set_model(no_position)
    assert geo.globe.satellite_position('SAT-3') is None and len(geo.globe.orbital_items()) == 3
    assert geo.focus_node.findData('SAT-3') >= 0 and geo.focus_node.findData('UNKNOWN') >= 0
    # Telemetry updates without geometry changes refresh the hover data too.
    satellites['SAT-0'] = {**satellites['SAT-0'], 'origin': 'updated observation source'}
    geo.set_model(replace(no_position, satellites=satellites))
    assert next(i for i in geo.globe.orbital_items() if i['id'] == 'SAT-0')['state']['origin'] == 'updated observation source'


def test_dash_clock_survives_swaps_without_predicting_satellite_position(geo, app):
    latest = replace(geo.net, satellites={key: {**state, 'at_network_s': 2.} for key, state in geo.net.satellites.items()}, network_time_s=2.)
    geo.set_model(latest); clocks = geo.scene.orbit_clocks
    geo.set_mode('globe'); settle(app); before = geo.globe.dash_offset('SAT-0')
    point = geo.globe.satellite_position('SAT-0'); QTest.qWait(20)
    assert geo.globe.dash_offset('SAT-0') < before
    assert geo.globe.satellite_position('SAT-0') == point
    geo.set_mode('map'); settle(app); geo.set_mode('globe'); settle(app)
    assert geo.scene.orbit_clocks is clocks and geo.globe._clocks == clocks
    assert geo.globe.dash_offset('SAT-0') < before and geo.globe.satellite_position('SAT-0') == point


def test_repeated_swaps_release_qobjects_caches_timers_and_preserve_state(geo, app):
    geo.set_model(replace(geo.net, selection=('node', 'SAT-1'), layer='entanglement', route=('OGS', 'SAT-1'), demand_id='D-1'))
    settle(app)
    geo.set_alerts([('D-1', 'Limited link', 'Observed loss')]); alerts = geo.alert_host
    net = geo.net; released = []
    for index in range(100):
        old = geo.renderer
        geo.mode_button.click()
        assert old._base is None
        if isinstance(old, OrbitGlobe):
            assert not old._timer.isActive() and old.net is None and not old._orbit_paths
        else: assert not old._resize_settle.isActive() and old._paths is None
        settle(app); released.append(old)
        stats = geo.resource_stats()
        assert not shiboken6.isValid(old) and stats['renderer_instances'] == stats['active_renderers'] == 1
        assert geo.net is net and geo.renderer.net is net and geo.net.communicators is net.communicators
        assert geo.net.selection == ('node', 'SAT-1') and geo.net.demand_id == 'D-1'
        assert geo.alert_host is alerts and geo.alerts[0][0] == 'D-1'
    assert not any(shiboken6.isValid(old) for old in released)
    QTest.qWait(380)
    assert not geo.resource_stats()['transition_raster'] and not geo._fade_animation.state().value
    geo.shutdown(); settle(app)
    assert geo.resource_stats()['renderer_instances'] == 0


def test_rapid_pending_swaps_activate_only_final_mode(geo, app):
    created = geo.resource_stats()['created']
    for index in range(201): geo.toggle_mode()
    assert geo.mode == 'globe'
    settle(app)
    assert geo.resource_stats()['renderer_instances'] == 1
    assert geo.resource_stats()['created'] <= created+1
    assert isinstance(geo.renderer, OrbitGlobe)
    geo.hide(); app.processEvents(); assert not geo.globe._timer.isActive()
    geo.show(); app.processEvents(); assert geo.globe._timer.isActive()


def test_camera_selection_and_pick_survive_modes_and_theme_rebuild(geo, app):
    net = geo.net
    geo.set_view_state((-43.17, -22.95, .01)); saved_map = geo.view_state()
    geo.set_mode('globe', camera=(20., 15., .7)); settle(app)
    geo.focus_node.setCurrentIndex(geo.focus_node.findData('SAT-2'))
    assert geo.globe.camera_state() == (20., 15., .7)
    state = geo.save_scene_state()
    other = GeoMap(); other.resize(1200, 800); other.set_model(net); other.restore_scene_state(state); other.show(); settle(app)
    try:
        assert other.mode == 'globe' and other.globe.camera_state() == (20., 15., .7)
        assert other.scene.satellite_id == 'SAT-2' and other.view_state() == saved_map
        locations = []; other.location_picked.connect(lambda *args: locations.append(args))
        other.start_pick('new ground device'); other.set_mode('map'); settle(app)
        assert other.picking and other.renderer._pick == 'new ground device'
        other.set_mode('globe'); settle(app)
        point = QPoint(other.width()//2, other.height()//2)
        expected = other.globe.lonlat_at(QPointF(point))
        QTest.mouseClick(other.globe, Qt.MouseButton.LeftButton, pos=point)
        assert not other.picking and locations[0] == pytest.approx((expected[1], expected[0]))
        other.focus_node.setCurrentIndex(other.focus_node.findData('OGS')); settle(app)
        assert other.mode == 'map' and other.view_state()[:2] == pytest.approx((-43.17, -22.95))
        assert other.metres_per_px == pytest.approx(1.5)
    finally: other.shutdown(); other.close(); other.deleteLater(); settle(app)


def test_communicators_sidebar_order_icon_and_selected_style(app):
    from quantum_autopilot.console.widgets.chrome import IconRail, paint_nav_icon, ROUTES
    from quantum_autopilot.console.screens import SCREENS
    rail = IconRail(); rail.resize(60, 650); rail.show(); app.processEvents()
    try:
        keys = list(rail.buttons)
        assert keys[keys.index('node')+1] == 'communicators'
        assert [screen.key for screen in SCREENS] == keys
        assert 'protocolos' in rail.buttons['communicators'].toolTip()
        rail.set_active('communicators'); assert rail.buttons['communicators'].active
        images = []
        for key in ('node', 'communicators'):
            image = QImage(32, 32, QImage.Format.Format_ARGB32); image.fill(0)
            painter = QPainter(image)
            from PySide6.QtCore import QRectF
            paint_nav_icon(painter, key, QRectF(4, 4, 24, 24), theme.C.ink); painter.end(); images.append(image)
        assert images[0] != images[1]
    finally: rail.close(); rail.deleteLater(); settle(app)
