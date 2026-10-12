"""EVA scenery preserves geographic observations, selection and renderer ownership."""
from copy import deepcopy
from dataclasses import replace
import os
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import pytest

pytest.importorskip("PySide6.QtWidgets")
from PySide6.QtCore import QCoreApplication, QEvent, QPointF, QRectF, Qt
from PySide6.QtGui import QImage, QPainter
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from quantum_autopilot.console import theme
from quantum_autopilot.console.contract import Link, Node
from quantum_autopilot.console.geodata import BBox, BaseMap
from quantum_autopilot.console.widgets.geo_map import GeoMap, MapRenderer
from quantum_autopilot.console.widgets.network_views import build_net_model
from quantum_autopilot.console.widgets.orbit_globe import OrbitGlobe
from quantum_autopilot.console.widgets.signal_geography import (
    SIGNAL_CROSS_LONLAT, SIGNAL_CROSS_OPACITY, globe_cross_rect, map_cross_rect, paint_signal_cross,
    signal_cross_path, signal_cross_vector, signal_map_ocean,
)


@pytest.fixture
def app():
    instance = QApplication.instance() or QApplication([])
    original = theme.current_theme()
    theme.set_theme("dark")
    theme.install(instance)
    yield instance
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    theme.set_theme(original)
    theme.install(instance)


def basemap():
    return BaseMap((), (), (), (), (), BBox(-20, -10, 20, 10), "Synthetic UI scenery fixture", {})


def model():
    nodes = [Node("A", "CORE", latitude=0, longitude=-5, network_role="core"),
             Node("B", "ENDPOINT", latitude=0, longitude=5, network_role="endpoint"),
             Node("SAT", "REPEATER", physical_type="satellite", network_role="repeater", kind="satellite")]
    snap = SimpleNamespace(topology=SimpleNamespace(nodes=nodes,
                           links=[Link("A", "B", "Received fibre", id="FIBRE", distance_km=123.4),
                                  Link("A", "SAT", "Registered downlink", id="SPACE")]), live=None)
    satellite = {"SAT": {"device_id": "SAT", "ecef_km": [6900.0, 2500.0, 500.0],
                        "orbit_ecef_km": [[6900.0, 0, 0], [0, 6900.0, 0], [-6900.0, 0, 0]],
                        "at_network_s": 1.0, "period_s": 5400.0, "origin": "Synthetic UI geometry fixture",
                        "transmitting_links": [{"link_id": "SPACE", "station_id": "A"}]}}
    return replace(build_net_model(snap), satellites=satellite, network_time_s=1.0,
                   selection=("node", "A"), route=("A", "B"), badges=(("A", "END"),))


def settle(app):
    for _ in range(3):
        app.processEvents()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    app.processEvents()


def retire(widget, app):
    widget.shutdown()
    widget.close()
    widget.deleteLater()
    settle(app)


def color_at(image, point):
    ratio = image.devicePixelRatio()
    return image.pixelColor(round(point.x() * ratio), round(point.y() * ratio))


def test_cross_vector_has_transparency_thin_base_tapered_tips_and_inner_glow(app):
    image = QImage(160, 180, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setOpacity(0.8)
    paint_signal_cross(painter, QRectF(20, 10, 120, 160))
    assert painter.opacity() == 0.8
    painter.end()
    assert image.pixelColor(0, 0).alpha() == 0
    centre_alpha = image.pixelColor(80, 110).alpha()
    assert centre_alpha == pytest.approx(255 * SIGNAL_CROSS_OPACITY * 0.8, abs=1)
    path = signal_cross_path(QRectF(0, 0, 100, 100))
    assert path.contains(QPointF(46, 99)) and path.contains(QPointF(54, 99))
    assert not path.contains(QPointF(38, 99)) and not path.contains(QPointF(62, 99))
    assert not path.contains(QPointF(38, 50))
    assert path.contains(QPointF(50, 2)) and not path.contains(QPointF(45, 2))
    assert path.contains(QPointF(2, 29)) and not path.contains(QPointF(2, 25))
    inner = image.pixelColor(80, 56)
    outer = image.pixelColor(32, 56)
    assert inner.alpha() == pytest.approx(outer.alpha(), abs=1)
    assert inner.green() > outer.green() + 20
    assert image.pixelColor(90, 110).alpha() == 0  # no glow outside the silhouette


def test_cross_scales_with_geographic_zoom_and_keeps_its_base_on_the_anchor(app):
    anchor = QPointF(400, 300)
    small = map_cross_rect(anchor, 1 / 50_000)
    wide = map_cross_rect(anchor, 1 / 25_000)
    assert wide.height() == small.height() * 2
    assert small.center().x() == wide.center().x() == anchor.x()
    assert small.bottom() == wide.bottom() == anchor.y()
    north = globe_cross_rect(anchor, 150)
    larger = globe_cross_rect(anchor, 300)
    assert north.center().x() == larger.center().x() == anchor.x()
    assert north.bottom() == larger.bottom() == anchor.y()
    assert larger.height() == north.height() * 2
    assert globe_cross_rect(anchor, 20).height() >= 54  # retain visibility with distant GEO orbits


@pytest.mark.parametrize("mode", ["dark", "light"])
def test_map_ocean_and_cross_restore_exact_normal_raster_without_model_changes(app, mode):
    theme.set_theme(mode)
    theme.install(app)
    renderer = MapRenderer(basemap=basemap())
    renderer.resize(800, 500)
    net = model()
    before = deepcopy(net)
    renderer.set_model(net)
    renderer.set_view_state((0, 55, 1 / 20_000))
    renderer._resize_settle.stop()
    try:
        transform = renderer.transform()
        normal = renderer._base_layer(transform).toImage()
        points = {node.id: renderer.node_position(node.id) for node in net.located}
        segments = renderer.segments(transform)
        renderer.set_signal_mode(True)
        red = renderer._base_layer(transform).toImage()
        sample = QPointF(20, 250)
        assert color_at(red, sample) == theme.qc(signal_map_ocean())
        assert color_at(red, sample) != color_at(normal, sample)
        cross = map_cross_rect(QPointF(*transform.to_px(*SIGNAL_CROSS_LONLAT)), transform.px_per_m)
        assert color_at(red, QPointF(cross.center().x(), cross.top() + cross.height() * 0.65)) != color_at(red, sample)
        key = renderer._base.cacheKey()
        renderer.set_signal_mode(True)
        assert renderer._base_layer(transform).cacheKey() == key
        assert renderer.net is net and renderer.net == before
        assert {node.id: renderer.node_position(node.id) for node in net.located} == points
        assert renderer.segments(transform) == segments
        renderer.set_signal_mode(False)
        assert renderer._base_layer(transform).toImage() == normal
    finally:
        retire(renderer, app)


@pytest.mark.parametrize("mode", ["dark", "light"])
def test_globe_restores_ocean_palette_and_retains_orbits_positions_and_overlay(app, mode):
    theme.set_theme(mode)
    theme.install(app)
    globe = OrbitGlobe()
    globe.resize(800, 600)
    net = model()
    before = deepcopy(net)
    globe.set_model(net)
    globe.set_camera(0, 0, 1)
    try:
        normal = globe._earth().toImage()
        camera = globe.camera_state()
        icons = globe.orbital_items()
        geometry = [(item["id"], item["centre"], item["physical"], item["hit"]) for item in icons]
        orbit_keys = globe._orbit_key
        overlay = globe._overlay_layer(icons)
        globe.set_signal_mode(True)
        red = globe._earth().toImage()
        radius = globe.radius()
        sample = QPointF(globe.width() / 2 + radius * 0.7, globe.height() / 2)
        assert color_at(red, sample) != color_at(normal, sample)
        anchor, depth = globe.project(signal_cross_vector())
        assert depth > 0
        cross = globe_cross_rect(anchor, radius)
        cross_point = QPointF(cross.center().x(), cross.top() + cross.height() * 0.65)
        assert color_at(red, cross_point) != color_at(normal, cross_point)
        assert globe.camera_state() == camera
        assert globe._orbit_key == orbit_keys
        assert globe._overlay_layer(icons).cacheKey() == overlay.cacheKey()
        assert [(item["id"], item["centre"], item["physical"], item["hit"])
                for item in globe.orbital_items()] == geometry
        assert globe.net is net and net == before
        builds = globe.earth_builds
        globe.set_signal_mode(True)
        globe._earth()
        assert globe.earth_builds == builds
        globe.set_signal_mode(False)
        assert globe._earth().toImage() == normal
    finally:
        retire(globe, app)


def test_map_cross_stays_on_one_geographic_point_after_drag_zoom_resize_and_pan_out(app, monkeypatch):
    import quantum_autopilot.console.widgets.geo_map as map_module

    painted = []
    draw = map_module.paint_signal_cross
    monkeypatch.setattr(map_module, "paint_signal_cross", lambda p, r: (painted.append(QRectF(r)), draw(p, r)))
    renderer = MapRenderer(basemap=basemap())
    renderer.resize(800, 500)
    net = model()
    before = deepcopy(net)
    renderer.set_model(net)
    renderer.set_view_state((0, 55, 1 / 35_000))
    renderer.set_signal_mode(True)
    renderer.show()
    settle(app)

    def geographic_base():
        renderer._base_layer(renderer.transform())
        cross = painted[-1]
        point = QPointF(cross.center().x(), cross.bottom())
        assert renderer.transform().to_lonlat(point.x(), point.y()) == pytest.approx(SIGNAL_CROSS_LONLAT)
        return cross, point

    try:
        original, first = geographic_base()
        QTest.mousePress(renderer, Qt.MouseButton.LeftButton, pos=QPointF(650, 320).toPoint())
        QTest.mouseMove(renderer, QPointF(730, 355).toPoint())
        QTest.mouseRelease(renderer, Qt.MouseButton.LeftButton, pos=QPointF(730, 355).toPoint())
        moved, second = geographic_base()
        assert second != first and moved.height() == pytest.approx(original.height())
        renderer.zoom_by(1.5)
        zoomed, third = geographic_base()
        assert zoomed.height() == pytest.approx(moved.height() * 1.5)
        renderer.resize(1024, 640)
        resized, fourth = geographic_base()
        assert fourth != third and resized.height() == pytest.approx(zoomed.height())
        renderer.set_view_state((-120, -45, 1 / 5_000))
        painted.clear()
        renderer._base_layer(renderer.transform())
        assert not painted  # off-screen scenery is culled, not pinned to the window
        assert net == before and renderer.net is net
    finally:
        retire(renderer, app)


def test_globe_cross_tracks_one_surface_point_and_is_hidden_on_the_far_side(app, monkeypatch):
    import quantum_autopilot.console.widgets.orbit_globe as globe_module

    painted = []
    draw = globe_module.paint_signal_cross
    monkeypatch.setattr(globe_module, "paint_signal_cross", lambda p, r: (painted.append(QRectF(r)), draw(p, r)))
    globe = OrbitGlobe()
    globe.resize(800, 600)
    net = model()
    before = deepcopy(net)
    globe.set_model(net)
    globe.set_signal_mode(True)

    def geographic_base():
        globe._earth()
        cross = painted[-1]
        point = QPointF(cross.center().x(), cross.bottom())
        assert globe.lonlat_at(point) == pytest.approx(SIGNAL_CROSS_LONLAT, abs=1e-8)
        return cross, point

    try:
        globe.set_camera(0, 0, 1)
        original, first = geographic_base()
        globe.set_camera(20, 30, 1)
        moved, second = geographic_base()
        assert second != first and moved.height() == pytest.approx(original.height())
        globe.zoom_by(2)
        zoomed, third = geographic_base()
        assert third != second and zoomed.height() == pytest.approx(moved.height() * 2)
        globe.resize(1024, 768)
        resized, fourth = geographic_base()
        assert fourth != third and resized.height() > zoomed.height()
        globe.set_camera(180, -55, 1)
        painted.clear()
        globe._earth()
        assert not painted  # the Earth occludes this fixed geographic anchor
        assert net == before and globe.net is net
    finally:
        retire(globe, app)


def test_signal_flag_survives_exclusive_swaps_pending_toggle_and_theme_reconstruction(app):
    net = model()
    before = deepcopy(net)
    geo = GeoMap(basemap=basemap())
    geo.resize(1000, 700)
    geo.set_model(net)
    geo.show()
    settle(app)
    try:
        count = geo.resource_stats()["created"]
        renderer = geo.renderer
        geo.set_signal_mode(True)
        assert geo.renderer is renderer and geo.resource_stats()["created"] == count
        assert geo.signal_mode and renderer.signal_mode
        geo.set_mode("globe", camera=(0, 0, 1))
        geo.set_signal_mode(False)
        assert geo._fade.image is None
        settle(app)
        assert isinstance(geo.renderer, OrbitGlobe) and not geo.renderer.signal_mode
        geo.set_signal_mode(True)
        for target in ("map", "globe", "map"):
            geo.set_mode(target)
            settle(app)
            assert geo.signal_mode and geo.renderer.signal_mode
            assert geo.resource_stats()["renderer_instances"] == 1
            assert len(geo.findChildren(MapRenderer)) + len(geo.findChildren(OrbitGlobe)) == 1
            assert geo.net is net and geo.net == before
        saved = geo.save_scene_state()
        assert saved["signal_mode"] is True
        theme.set_theme("light")
        theme.install(app)
        rebuilt = GeoMap(basemap=basemap())
        rebuilt.resize(1000, 700)
        try:
            rebuilt.set_model(net)
            rebuilt.restore_scene_state(saved)
            settle(app)
            assert rebuilt.signal_mode and rebuilt.renderer.signal_mode
            assert rebuilt.net is net and rebuilt.net == before
            assert rebuilt.resource_stats()["renderer_instances"] == 1
            rebuilt.set_signal_mode(False)
            assert not rebuilt.signal_mode and not rebuilt.renderer.signal_mode
        finally:
            retire(rebuilt, app)
        geo.hide()
        assert not geo.resource_stats()["orbit_timer_active"]
    finally:
        retire(geo, app)


def test_decorative_cross_leaves_map_controls_node_link_and_globe_satellite_clicks_intact(app):
    geo = GeoMap(basemap=basemap())
    geo.resize(1000, 700)
    net = model()
    before = deepcopy(net)
    geo.set_model(net)
    geo.show()
    settle(app)
    nodes, links = [], []
    geo.node_selected.connect(nodes.append)
    geo.link_selected.connect(links.append)
    try:
        children = len(geo.children())
        geo.set_signal_mode(True)
        assert len(geo.children()) == children
        point = geo.node_position("A")
        assert geo.hit(point) == ("node", "A")
        QTest.mouseClick(geo.renderer, Qt.MouseButton.LeftButton, pos=point.toPoint())
        assert nodes[-1] == "A"
        fibre, start, end, _queue = next(segment for segment in geo.renderer.segments(geo.transform())
                                      if segment[0].id == "FIBRE")
        middle = (start + end) / 2
        assert geo.hit(middle) == ("link", fibre.key)
        QTest.mouseClick(geo.renderer, Qt.MouseButton.LeftButton, pos=middle.toPoint())
        assert links[-1] == fibre.key
        QTest.mouseClick(geo.mode_button, Qt.MouseButton.LeftButton)
        settle(app)
        assert geo.mode == "globe" and geo.renderer.signal_mode
        globe = geo.globe
        globe.set_camera(0, 0, 1)
        satellite = globe.orbital_items()[0]
        assert globe.hit(satellite["centre"]) == ("node", "SAT")
        QTest.mouseClick(globe, Qt.MouseButton.LeftButton, pos=satellite["centre"].toPoint())
        assert nodes[-1] == "SAT"
        assert geo.net is net and geo.net == before
        geo.set_signal_mode(False)
        assert globe.hit(satellite["centre"]) == ("node", "SAT")
    finally:
        retire(geo, app)
