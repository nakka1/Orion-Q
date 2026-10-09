"""Presentation optimization preserves orbit clipping and published state."""
from dataclasses import replace
import math
import os
from types import SimpleNamespace

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import pytest
pytest.importorskip('PySide6.QtWidgets')
from PySide6.QtGui import QPainterPath
from PySide6.QtWidgets import QApplication

from quantum_autopilot.console import theme
from quantum_autopilot.console.contract import Link, Node
from quantum_autopilot.console.widgets.geo_view import GeoMap
from quantum_autopilot.console.widgets import orbit_globe as module
from quantum_autopilot.console.widgets.orbit_globe import OrbitGlobe
from quantum_autopilot.console.widgets.network_views import build_net_model


@pytest.fixture(scope='module')
def app():
    instance = QApplication.instance() or QApplication([])
    theme.set_theme('dark'); theme.install(instance)
    return instance


def model(clock=1.):
    nodes = [Node('GROUND', 'ENDPOINT', latitude=0., longitude=0., network_role='endpoint')]
    states, links = {}, []
    for index in range(3):
        ident = f'SAT-{index}'
        nodes.append(Node(ident, 'REPEATER', physical_type='satellite', network_role='repeater'))
        track = [[6900*math.cos(step*math.pi/48), 6900*math.sin(step*math.pi/48),
                  index*150.] for step in range(97)]
        states[ident] = {'ecef_km': track[0], 'orbit_ecef_km': track,
                         'at_network_s': clock, 'period_s': 5400.,
                         'transmitting_links': [{'link_id': 'L-'+ident, 'station_id': 'GROUND'}]}
        links.append(Link('GROUND', ident, '', id='L-'+ident))
    snapshot = SimpleNamespace(topology=SimpleNamespace(nodes=nodes, links=links), live=None)
    return replace(build_net_model(snapshot), satellites=states, network_time_s=clock)


@pytest.fixture
def globe(app):
    widget = OrbitGlobe(); widget.resize(1200, 800); widget.set_model(model())
    yield widget
    widget.shutdown(); widget.close(); widget.deleteLater()


@pytest.mark.parametrize('camera', [(0.,0.,1.), (-43.,-23.,.5), (121.,31.,2.), (179.,80.,6.)])
def test_orbit_clipping_and_satellite_hits_preserve_published_geometry(globe, monkeypatch, camera):
    globe.set_camera(*camera)
    expected = {}
    # Independent reference uses the original double-projection clipping rule.
    for ident, samples in globe._tracks:
        front, back = QPainterPath(), QPainterPath(); last = None
        for vector in samples:
            point, _ = globe.project(vector); visible = not globe._hidden(vector)
            path = front if visible else back
            if visible == last: path.lineTo(point)
            else: path.moveTo(point)
            last = visible
        expected[ident] = (front, back)
    calls = []; original = globe.project
    def project(vector, radius=None):
        calls.append(vector); return original(vector, radius)
    monkeypatch.setattr(globe, 'project', project)
    icons = globe.orbital_items()
    assert globe._orbit_paths == expected
    # One projection per orbital sample plus physical/occlusion projection for
    # each satellite handle. Every icon remains accessible and fully sized.
    assert len(calls) == sum(len(samples) for _, samples in globe._tracks)+2*len(icons)
    assert len(icons) == 3
    for item in icons:
        assert item['size_px'] == 32 and item['hit'].width() == 48
        assert globe.hit_satellite(item['centre']) == item['id']
        assert item['physical'] == original(globe._state_map[item['id']]['ecef_km'])[0]


def test_idle_timer_callbacks_schedule_only_one_identical_phase(globe, monkeypatch):
    globe.orbital_items(); calls = []
    monkeypatch.setattr(globe, 'update', lambda: calls.append('paint'))
    for _ in range(120): globe._animation_tick()
    assert calls == ['paint']


def test_interpolation_and_final_clamped_frame_preserve_position(globe, monkeypatch):
    now = [100.]
    monkeypatch.setattr(module.time, 'monotonic', lambda: now[0])
    globe._clocks = {ident: (2., 1., 100.) for ident, _ in globe._state_items}
    globe.orbital_items(); position = globe.satellite_position('SAT-0'); calls = []
    monkeypatch.setattr(globe, 'update', lambda: calls.append(globe.dash_offset('SAT-0')))
    for value in (100., 100.2, 100.7, 101.2, 102., 103.):
        now[0] = value; globe._animation_tick()
    assert len(calls) == 4 and all(a > b for a,b in zip(calls[:-1],calls[1:]))
    assert globe.satellite_position('SAT-0') == position
    final = globe.dash_offset('SAT-0')
    # A new published clock resumes visual interpolation, without advancing
    # the physical satellite position beyond its received observation.
    states = {key: {**state, 'at_network_s': 3.} for key,state in globe.net.satellites.items()}
    globe.set_model(replace(globe.net, satellites=states, network_time_s=3.))
    now[0] = 103.2; globe._animation_tick()
    assert len(calls) == 6 and calls[-1] < final
    assert globe.satellite_position('SAT-0') == position


def test_new_model_and_camera_invalidation_are_independent_of_timer(globe, monkeypatch):
    globe.orbital_items(); calls = []
    monkeypatch.setattr(globe, 'update', lambda: calls.append('paint'))
    globe._animation_tick(); globe._animation_tick(); assert len(calls) == 1
    states = dict(globe.net.satellites)
    states['SAT-0'] = {**states['SAT-0'], 'ecef_km': [6800., 700., 20.],
                       'origin': 'new received science record', 'transmitting_links': []}
    globe.set_model(replace(globe.net, satellites=states), 'SAT-0')
    assert len(calls) == 2 and globe._state_map['SAT-0'] is states['SAT-0']
    assert globe.satellite_position('SAT-0') == globe.project(states['SAT-0']['ecef_km'])[0]
    assert 'L-SAT-0' not in {item[0]['link_id'] for item in globe.transmission_geometry()}
    globe.set_camera(20., 15., 2.); assert len(calls) == 3


def test_closed_timer_callback_cannot_request_paint(globe, monkeypatch):
    globe.orbital_items(); globe._timer.start()
    assert globe._timer.isActive()
    globe.shutdown(); assert not globe._timer.isActive()
    calls = []; monkeypatch.setattr(globe, 'update', lambda: calls.append('paint'))
    globe._animation_tick(); assert not calls and globe._dash_offsets is None


def test_info_reuses_identical_layout_and_invalidates_mode_resize_inventory(app, monkeypatch):
    geo = GeoMap(); geo.resize(1440, 900); geo.scene.net = model()
    calls = []; original = geo._place_overlays
    def layout():
        calls.append('layout'); return original()
    monkeypatch.setattr(geo, '_place_overlays', layout)
    try:
        for _ in range(40): geo._update_info()
        assert calls == ['layout']
        expected = geo.placed.text()
        geo.title.setText('Globo · todos os satélites do cenário'); geo._update_info()
        assert len(calls) == 2 and geo.placed.text() == expected
        geo.resize(640, 480); geo._update_info()
        assert len(calls) == 3 and geo.controls.geometry().right() < geo.width()
        geo._info_height = 36; geo._update_info()
        assert len(calls) == 4
        geo.scene.net = replace(geo.net, nodes=geo.net.nodes[:-1]); geo._update_info()
        assert len(calls) == 5 and geo.placed.text() != expected
    finally: geo.shutdown(); geo.close(); geo.deleteLater()
