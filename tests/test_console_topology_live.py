"""The Topology screen against the REAL backend (demonstration service, no mocks).

GEO is the main view and is to scale; the matrix shows the same nodes and fibres;
unlocated nodes wait in the queue with their connections; isolation, recalibration,
fibre toggling and location are executed by the backend and read back; a click on a
node opens the floating window with every backend field and one wave series per
connection. The widget-level tests at the top need no service.
"""
from __future__ import annotations

import math
import os
import time
from pathlib import Path

import pytest

pytest.importorskip("PySide6.QtWidgets")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QEvent, QPointF, Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from quantum_autopilot.console import theme  # noqa: E402
from quantum_autopilot.console.contract import Link, Node  # noqa: E402
from quantum_autopilot.console.geodata import geodesic_km  # noqa: E402
from quantum_autopilot.console.providers import MockProvider  # noqa: E402
from quantum_autopilot.console.screens import SCREENS  # noqa: E402
from quantum_autopilot.console.screens.topology import VIEWS, TopologyScreen  # noqa: E402
from quantum_autopilot.console.service_provider import ServiceProvider  # noqa: E402
from quantum_autopilot.console.shell import ConsoleWindow  # noqa: E402
from quantum_autopilot.console.widgets import Btn  # noqa: E402
from quantum_autopilot.console.widgets.geo_map import GeoMap  # noqa: E402
from quantum_autopilot.console.widgets.network_views import MatrixView, build_net_model  # noqa: E402
from quantum_autopilot.console.widgets.node_window import NODE_FIELDS, NO_WAVE_SOURCE  # noqa: E402
from quantum_autopilot.console.widgets.wave_chart import WaveChart, WaveSeries  # noqa: E402
from quantum_autopilot.qkd.dashboard_api import demo_service  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
#: The demonstration's located nodes: (id, latitude, longitude).
PLACED = (("N-01", -12.938, -38.389), ("N-02", -13.000, -38.505), ("N-03", -12.972, -38.505),
          ("R-07", -12.940, -38.430), ("CORE", -12.977, -38.455), ("N-05", -12.993, -38.455),
          ("N-09", -12.948, -38.462), ("N-11", -12.915, -38.335))


@pytest.fixture(scope="module")
def app():
    instance = QApplication.instance() or QApplication([])
    theme.set_theme("dark")
    theme.install(instance)
    return instance


def pump(app, condition=lambda: True, timeout=40.0):
    """Process events until `condition()` holds (checked right after an event turn)."""

    deadline = time.monotonic() + timeout
    while True:
        app.processEvents()
        if condition():
            return
        assert time.monotonic() < deadline, "condition not reached in time"
        time.sleep(.005)


def flush(app):
    app.processEvents()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    app.processEvents()


def fake_snapshot(nodes, links):
    from types import SimpleNamespace
    return SimpleNamespace(topology=SimpleNamespace(nodes=list(nodes), links=list(links)), live=None)


def placed_model(extra=()):
    nodes = [Node(name, "ENDPOINT", "up", 0.5, 0.5, latitude=lat, longitude=lon) for name, lat, lon in PLACED]
    nodes += list(extra)
    links = [Link("N-01", "N-02", "N01-N02", id="N01-N02"), Link("CORE", "N-05", "CORE-N05", id="CORE-N05")]
    return build_net_model(fake_snapshot(nodes, links))


# ---------------------------------------------------------------- widgets
def test_geo_is_the_first_view_and_the_default(app):
    assert [key for key, _title in VIEWS] == ["GEO", "MATRIX", "GRAPH"]
    screen = TopologyScreen()
    assert screen.view == "GEO" and screen.graph_stack.currentWidget() is screen.geo
    assert list(screen.toggles) == ["GEO", "MATRIX", "GRAPH"]
    assert [button.text() for button in screen.toggles.values()] == ["Geo", "Matrix", "Graph"]
    assert screen.toggles["GEO"].active and not screen.toggles["MATRIX"].active
    assert [key for key in screen.inspector.tabs] == ["insp", "events", "links", "pairs"]
    assert screen.inspector.width() == 330
    screen.close()


@pytest.mark.parametrize("size", [(1014, 380), (700, 520), (1500, 420)])
def test_geo_distances_are_true_to_scale_at_any_size_and_zoom(app, size):
    """Nodes d km apart are drawn d km apart at the scale of the map — one scale for both axes."""

    # two extra nodes exactly 10 km apart along a meridian and along a parallel-ish diagonal
    extra = [Node("TEN-A", "ENDPOINT", "up", 0.5, 0.5, latitude=-12.90, longitude=-38.40)]
    north = -12.90 + 10.0 / 110.574            # refined below against the geodesic itself
    for _ in range(6):
        north += (10.0 - geodesic_km(-12.90, -38.40, north, -38.40)) / 110.574
    extra.append(Node("TEN-B", "ENDPOINT", "up", 0.5, 0.5, latitude=north, longitude=-38.40))
    assert geodesic_km(-12.90, -38.40, north, -38.40) == pytest.approx(10.0, abs=1e-6)
    geo = GeoMap()
    geo.resize(*size)
    geo.set_model(placed_model(extra))
    geo.show()
    app.processEvents()
    coordinates = {name: (lat, lon) for name, lat, lon in PLACED}
    coordinates.update({"TEN-A": (-12.90, -38.40), "TEN-B": (north, -38.40)})
    pairs = [("N-01", "N-02"), ("N-03", "N-11"), ("CORE", "N-05"), ("R-07", "N-09"), ("N-02", "N-11"),
             ("TEN-A", "TEN-B")]

    def check():
        metres_per_px = geo.metres_per_px
        for a, b in pairs:
            pa, pb = geo.node_position(a), geo.node_position(b)
            pixels = math.hypot(pa.x() - pb.x(), pa.y() - pb.y())
            kilometres = geodesic_km(*coordinates[a], *coordinates[b])
            assert pixels * metres_per_px / 1000.0 == pytest.approx(kilometres, rel=0.01), (a, b)
        ten = math.hypot(geo.node_position("TEN-A").x() - geo.node_position("TEN-B").x(),
                         geo.node_position("TEN-A").y() - geo.node_position("TEN-B").y())
        assert ten * metres_per_px == pytest.approx(10_000.0, rel=0.01)
        # the scale bar is read from the same transform and is drawn with that length
        length_km, length_px = geo.scale_bar()
        assert length_px == pytest.approx(length_km * 1000.0 / metres_per_px, rel=1e-9)
        assert length_px <= 120.0 or length_km == 0.05
        geo.grab()                                   # paints the base layer
        assert geo._scale_rect.width() == pytest.approx(length_px, abs=1e-6)
        assert ten / length_px == pytest.approx(10.0 / length_km, rel=0.01)

    assert geo.is_default_view
    check()
    default_scale = geo.metres_per_px
    geo.zoom_by(2.3, QPointF(120, 90))
    assert geo.metres_per_px == pytest.approx(default_scale / 2.3) and not geo.is_default_view
    check()
    QTest.mouseMove(geo, geo.rect().center())
    geo.pan_by(-140, 60)
    check()
    geo.resize(size[0] + 173, size[1] - 61)
    app.processEvents()
    check()
    geo.reset_view()
    assert geo.is_default_view
    check()
    # the default view shows every placed node inside the widget
    for name in coordinates:
        position = geo.node_position(name)
        assert 0 <= position.x() <= geo.width() and 0 <= position.y() <= geo.height(), name
    geo.close()


def test_geo_draws_the_salvador_base_map_with_its_attribution(app):
    from quantum_autopilot.console.geodata import load_salvador
    geo = GeoMap()
    geo.resize(1014, 400)
    geo.set_model(placed_model())
    geo.show()
    app.processEvents()
    image = geo.grab().toImage()
    base = load_salvador()
    assert geo.basemap is base
    # the mandatory attribution of the base map is drawn (elided only when the widget is too narrow)
    assert geo.attribution_text == base.attribution
    transform = geo.transform()
    # water and land are painted where the base map says they are (ocean and bay; Cabula inland)
    def colour(lon, lat):
        x, y = transform.to_px(lon, lat)
        assert 0 <= x < geo.width() and 0 <= y < geo.height(), (lon, lat)
        return image.pixelColor(round(x), round(y)).name()

    seas = [(-38.43, -13.012), (-38.57, -12.985), (-38.36, -12.985)]
    assert not any(base.contains_land(lon, lat) for lon, lat in seas)
    assert [colour(lon, lat) for lon, lat in seas].count(theme.TOKENS["dark"]["water"]) >= 2
    assert base.in_municipality(-38.47, -12.955) and base.in_municipality(-38.40, -12.92)
    assert colour(-38.47, -12.955) != theme.TOKENS["dark"]["water"]
    assert colour(-38.40, -12.92) != theme.TOKENS["dark"]["water"]
    # every demonstration node lies on land of the municipality
    assert all(base.in_municipality(lon, lat) for _name, lat, lon in PLACED)
    # with nothing placed the view falls back to the base map's own box
    geo.set_model(build_net_model(fake_snapshot([Node("X", "ENDPOINT")], [])))
    box = geo.transform().visible_bbox()
    assert box.west <= base.bbox.west and box.east >= base.bbox.east
    assert box.south <= base.bbox.south and box.north >= base.bbox.north
    assert not geo.available
    geo.close()


def test_geo_hit_testing_nodes_and_lines_without_midpoint_targets(app):
    geo = GeoMap()
    geo.resize(1014, 400)
    geo.set_model(placed_model())
    geo.show()
    app.processEvents()
    a, b = geo.node_position("N-01"), geo.node_position("N-02")
    assert geo.hit(a) == ("node", "N-01")
    middle = QPointF((a.x() + b.x()) / 2, (a.y() + b.y()) / 2)
    assert geo.hit(middle) == ("link", "N01-N02")                       # the line's midpoint
    direction=b-a;length=math.hypot(direction.x(),direction.y())
    assert geo.hit(middle+QPointF(-direction.y()/length*9,direction.x()/length*9))[0] is None
    quarter = QPointF(a.x() + (b.x() - a.x()) * .3, a.y() + (b.y() - a.y()) * .3)
    assert geo.hit(quarter) == ("link", "N01-N02")                      # the line itself
    assert geo.hit(QPointF(quarter.x() + 14, quarter.y() - 14))[0] is None
    nodes, links = [], []
    geo.node_selected.connect(nodes.append)
    geo.link_selected.connect(links.append)
    QTest.mouseClick(geo, Qt.MouseButton.LeftButton, pos=a.toPoint())
    QTest.mouseClick(geo, Qt.MouseButton.LeftButton, pos=middle.toPoint())
    assert nodes == ["N-01"] and links == ["N01-N02"]
    # wheel zoom keeps the point under the cursor in place
    before = geo.transform().to_lonlat(300.0, 200.0)
    geo.zoom_by(1.2 ** 3, QPointF(300.0, 200.0))
    after = geo.transform().to_lonlat(300.0, 200.0)
    assert after == pytest.approx(before, abs=1e-9)
    # pick mode: the next click reports the latitude / longitude under it instead of selecting
    picked = []
    geo.location_picked.connect(lambda lat, lon: picked.append((lat, lon)))
    geo.start_pick("N-13")
    assert geo.picking and geo.pick_hint.isVisible()
    QTest.mouseClick(geo, Qt.MouseButton.LeftButton, pos=geo.node_position("N-01").toPoint())
    assert not geo.picking and nodes == ["N-01"]
    lon, lat = geo.transform().to_lonlat(*[float(v) for v in (geo.node_position("N-01").toPoint().x(),
                                                              geo.node_position("N-01").toPoint().y())])
    assert picked == [pytest.approx((lat, lon))]
    geo.close()


def test_wave_chart_draws_only_received_samples(app):
    chart = WaveChart()
    chart.resize(480, 170)
    first = tuple(0.90 + 0.001 * i for i in range(240))
    chart.set_series([WaveSeries("A-B", "B · A-B", first, 20.0), WaveSeries("A-C", "C · A-C", (), 20.0,
                                                                            "no trace · disabled")], 30.0)
    chart.show()
    app.processEvents()
    assert chart.keys() == ["A-B", "A-C"] and chart.labels() == ["B · A-B", "C · A-C"]
    # no data => no trace
    assert chart.visible_samples("A-C") == []
    shown = chart.visible_samples("A-B")
    assert [value for _at, value in shown] == list(first)        # first delivery: everything received
    assert max(at for at, _value in shown) == pytest.approx(30.0)
    assert min(at for at, _value in shown) == pytest.approx(30.0 - 239 / 20.0)

    # a new delivery one network second later: 20 new samples at the end
    second = first[20:] + tuple(0.80 + 0.002 * i for i in range(20))
    chart.set_series([WaveSeries("A-B", "B · A-B", second, 20.0), WaveSeries("A-C", "C · A-C", (), 20.0)], 31.0)
    chart.freeze(0.0)                                            # start of the reveal: newest second held back
    held = chart.visible_samples("A-B")
    assert [value for _at, value in held] == list(first)  # old samples remain until they scroll out
    assert max(at for at, _value in held) <= 30.0 + 1e-9
    chart.freeze(0.5)
    half = chart.visible_samples("A-B")
    assert [value for _at, value in half] == list(first[9:]) + list(second[-20:-10])
    chart.freeze(1.0)
    full = chart.visible_samples("A-B")
    assert [value for _at, value in full] == list(second)        # appended, nothing invented
    assert max(at for at, _value in full) == pytest.approx(31.0)
    # with the clock running, the reveal never passes the last received sample
    chart.freeze(None)
    chart._delivered_at -= 30.0                                  # the next delivery is long overdue
    assert chart.reveal() == 1.0 and chart.display_time() == pytest.approx(31.0)
    late = chart.visible_samples("A-B")
    assert late == full and all(value in second for _at, value in late)
    # the fibre stops producing: the backend sends an empty wave and the trace is gone
    chart.set_series([WaveSeries("A-B", "B · A-B", (), 20.0, "no trace · disabled")], 32.0)
    assert chart.visible_samples("A-B") == []
    chart.grab()
    chart.set_highlight("A-B")
    assert chart.highlight == "A-B"
    chart.close()


def test_topology_renders_for_a_source_without_live_data(app):
    snap = MockProvider().snapshot()
    assert snap.live is None
    screen = TopologyScreen()
    screen.resize(1380, 640)
    screen.apply(snap)
    screen.show()
    app.processEvents()
    ids = [node.id for node in snap.topology.nodes]
    assert screen.view == "GEO" and not screen.geo.available
    assert screen.queue.node_ids() == ids and screen.matrix.node_ids() == ids
    assert [node.id for node in screen.graph.nodes] == ids
    # live-only parts are absent or "—", never invented
    assert screen.cell_qber.value.text() == "—" and screen.cell_skr.value.text() == "—"
    assert not screen.cell_fidelity.spark.isVisible()
    assert "no network engine" in screen.inspector.footer.value("Live tick")
    assert not screen.inspector.pairs_panel.rows
    screen.graph.node_selected.emit(ids[0])
    window = screen.node_windows.windows[ids[0]]
    pump(app, lambda: window.presentation_status()['ready'])
    fields = window.field_texts()
    assert set(fields) == set(NODE_FIELDS)
    assert fields["temperature_c"] == "not supplied by this source" and fields["latitude"] == "no location"
    assert window.chart.keys() == [link.key for link in screen._net.links_of(ids[0])]
    assert all(window.chart.visible_samples(key) == [] for key in window.chart.keys())
    assert all(NO_WAVE_SOURCE in label for label in window.legend_labels())
    # the node operations are not declared by this source: every operation button is disabled
    gated = [button for button in screen.findChildren(Btn) if button.property("operation")]
    assert gated and not any(button.isEnabled() for button in gated)
    screen.shutdown()
    screen.close()
    flush(app)


def test_node_window_is_movable_stays_reachable_and_esc_closes_it(app):
    screen = TopologyScreen()
    screen.resize(1380, 640)
    screen.apply(MockProvider().snapshot())
    screen.show()
    app.processEvents()
    window = screen._open_window("R-07")
    assert window is screen.node_windows.windows["R-07"] and window.isVisible()
    assert window.width() <= 1380 - 16 and window.height() <= 640 - 16          # fits its host
    start = window.pos()
    handle = window.bar.mapTo(window, window.bar.rect().center())
    QTest.mousePress(window, Qt.MouseButton.LeftButton, pos=handle)
    QTest.mouseMove(window, handle + type(handle)(-180, 60))
    QTest.mouseRelease(window, Qt.MouseButton.LeftButton, pos=handle + type(handle)(-180, 60))
    assert window.pos() == start + type(handle)(-180, 60)                      # dragged by its title bar
    window.move_within(type(handle)(5000, 5000))                              # never lost outside the screen
    assert window.x() <= screen.width() - 120 and window.y() <= screen.height() - 44
    other = screen._open_window("CORE")
    assert screen.node_windows._order == ["R-07", "CORE"]
    QTest.mouseClick(window.chart, Qt.MouseButton.LeftButton)                  # a click raises that window
    assert screen.node_windows._order == ["CORE", "R-07"]
    QTest.keyClick(window, Qt.Key.Key_Escape)                                  # Esc closes the focused one
    app.processEvents()
    assert list(screen.node_windows.windows) == ["CORE"] and other.isVisible()
    screen.node_popover.hide()
    assert not screen.node_windows.windows and not screen.node_windows._filtering
    screen.shutdown()
    screen.close()
    flush(app)


def test_matrix_and_geo_are_views_of_one_model(app):
    nodes = [Node(name, "ENDPOINT", "up", 0.5, 0.5, latitude=lat, longitude=lon) for name, lat, lon in PLACED[:4]]
    nodes.append(Node("QUEUED", "ENDPOINT", "up", 0.5, 0.5))
    links = [Link("N-01", "N-02", "N01-N02", fidelity="0.95", id="N01-N02", provenance="simulated"),
             Link("N-03", "QUEUED", "N03-Q", id="N03-Q", configured_active=False),
             Link("N-02", "N-03", "N02-N03", id="N02-N03", status="compromised")]
    net = build_net_model(fake_snapshot(nodes, links), ("link", "N01-N02"))
    matrix, geo = MatrixView(), GeoMap()
    matrix.set_model(net)
    geo.set_model(net)
    assert matrix.node_ids() == ["N-01", "N-02", "N-03", "R-07", "QUEUED"]         # located AND unlocated
    assert matrix.link_keys() == {"N01-N02", "N03-Q", "N02-N03"}
    assert matrix.cell_for("N-01", "N-02").text == "0.950" and matrix.cell_for("N-02", "N-01").link == "N01-N02"
    assert matrix.cell_for("N-03", "QUEUED").text == "OFF" and matrix.cell_for("N-03", "QUEUED").style == "off"
    assert matrix.cell_for("N-02", "N-03").text == "ALARM"
    assert matrix.cell_for("N-01", "N-03").text == "·" and matrix.cell_for("N-01", "N-01").text == "—"
    assert "◌" in matrix.records.headerData(5, Qt.Orientation.Horizontal)          # unlocated node marked
    assert matrix.net.selection == geo.net.selection == ("link", "N01-N02")
    selected = []
    matrix.link_selected.connect(selected.append)
    matrix.node_selected.connect(selected.append)
    matrix._cell_clicked(matrix.index_for("N-02", "N-03"))
    matrix._cell_clicked(matrix.records.index(4, 0))
    matrix._header_clicked(3)
    matrix._cell_clicked(matrix.index_for("N-01", "N-03"))                          # no fibre: nothing selected
    assert selected == ["N02-N03", "QUEUED", "N-03"]


# ------------------------------------------------------------ real backend
class Demo:
    """The real console window over the real demonstration service."""

    def __init__(self, app, state):
        self.app = app
        service = demo_service(ROOT, state, scenario="quiet")
        self.service = service
        self.provider = ServiceProvider(service, state_root=state / "demo")
        self.window = ConsoleWindow(self.provider, state_root=state)
        self.window.resize(1440, 900)
        self.window.show()
        self.errors: list[str] = []
        self.snapshots: list = []
        self.window.provider.error.connect(self.errors.append)
        self.window.provider.snapshot_changed.connect(self.snapshots.append)
        self.window.provider.start()
        pump(app, lambda: self.window.snapshot is not None and self.window.snapshot.live is not None, 120)
        pump(app, lambda: self.window.snapshot.live.tick >= 3, 60)      # the fibres have processed input

    @property
    def topo(self) -> TopologyScreen:
        return self.window.screens["topology"]

    @property
    def snap(self):
        return self.window.snapshot

    @property
    def live(self):
        return self.window.snapshot.live

    def idle(self) -> bool:
        active = self.window.provider.active
        return not self.window.provider.busy and getattr(active, "_future", None) is None

    def settle(self, timeout=60.0):
        # Wait for real QTimer turns; never force missing visual rows to be built.
        pump(self.app, lambda: self.idle() and all(
            window.presentation_status()['ready'] for window in self.topo.node_windows.windows.values()
            if window.isVisible()), timeout)

    def done(self, timeout=60.0):
        """Wait for the dispatched verb to be resolved from the backend readback."""

        try:
            pump(self.app, lambda: self.topo._pending is None and self.idle(), timeout)
        except AssertionError:
            raise AssertionError(f"verb not resolved: pending={self.topo._pending} status="
                                 f"{self.topo._command_status} errors={self.errors[-2:]} bar="
                                 f"{self.window.statusBar().currentMessage()!r} busy={self.window.provider.busy} "
                                 f"future={getattr(self.window.provider.active, '_future', None)} "
                                 f"enabled={self.topo.isEnabled()}") from None
        pump(self.app)

    def ticks(self, count=1, timeout=30.0):
        target = self.live.tick + count
        pump(self.app, lambda: self.live.tick >= target, timeout)

    def click_node(self, node_id):
        self.settle()
        QTest.mouseClick(self.topo.geo, Qt.MouseButton.LeftButton,
                         pos=self.topo.geo.node_position(node_id).toPoint())
        self.settle()

    def select(self, kind, ident):
        self.settle()
        (self.topo._node_clicked if kind == "node" else self.topo._link_clicked)(ident)
        self.settle()


@pytest.fixture(scope="module")
def demo(app, tmp_path_factory):
    harness = Demo(app, tmp_path_factory.mktemp("topology-demo"))
    yield harness
    harness.window.close()
    harness.window.deleteLater()
    flush(app)


def test_demo_opens_on_geo_after_login(demo):
    window = demo.window
    window.splash.finished.emit()
    assert window.current_screen == "boot"                      # the login screen
    assert window._skip_auth is False and not window._login_verified
    assert window.open_workspace() is False                    # anonymous entry stays blocked
    controller = window._login_controller
    assert controller is not None
    verified = []
    controller.succeeded.connect(verified.append)
    assert window.boot.os_session_button.isEnabled()
    window.boot.os_session_button.click()
    pump(demo.app, lambda: bool(verified) and window._login_verified
         and window.current_screen == "topology" and not controller.active, 120)
    assert verified[-1].source == "os"
    assert verified[-1].subject == demo.provider._identity.subject
    assert window.open_workspace()
    assert window.current_screen == "topology" and window.current_widget() is demo.topo
    topo = demo.topo
    assert topo.view == "GEO" and topo.graph_stack.currentWidget() is topo.geo and topo.geo.isVisible()
    assert topo.geo.available and topo.geo.is_default_view
    # header strip: real node states and fibres
    counts = {status: sum(node.status == status for node in demo.live.nodes) for status in ("up", "degraded", "down")}
    full = [text for text, _tone, _tip in topo._health_chips(0)]
    assert full[:3] == [f"● {counts['up']} up", f"▲ {counts['degraded']} degraded", f"■ {counts['down']} down"]
    # what is drawn is one of the compaction levels of the same counts; the words stay in the tooltips
    assert topo._health == topo._health_chips(topo._strip_level)
    assert [tip for _text, _tone, tip in topo._health][:3] == [
        f"{counts[name]} node{'' if counts[name] == 1 else 's'} {name}" for name in ("up", "degraded", "down")]
    for (text, _tone, _tip), name in zip(topo._health, ("up", "degraded", "down")):
        assert text.split()[1] == str(counts[name])
    in_graph = sum(link.effective for link in demo.live.links)
    assert topo.links_up.text() in (f"{in_graph}/{len(demo.live.links)} fibres in graph",
                                    f"{in_graph}/{len(demo.live.links)} fibres")
    assert "effective routing graph" in topo.links_up.toolTip()
    # at 1440 px the header strip is ONE line: the scenario / replay buttons fold behind "⋯"
    assert topo.strip_layout.line_count(topo.strip.width()) == 1
    assert topo.more.isVisible() and not topo.actions.isVisible()
    topo.more.click()
    assert topo.actions.isVisible() and topo.recalibrate.isVisible()
    # opened, the two buttons take whatever room is left: the same line if they fit, else a second one
    fits = topo.strip_layout.needed_width() <= topo.strip.width()
    assert topo.strip_layout.line_count(topo.strip.width()) == (1 if fits else 2)
    topo.more.click()
    assert not topo.actions.isVisible() and topo.strip_layout.line_count(topo.strip.width()) == 1
    assert "SIMULATED INPUT" in topo.ring_label.text() and demo.snap.topology.ring.upper() in topo.ring_label.text()
    # footer: real values
    footer = topo.inspector.footer.texts()
    assert footer["Topology"] == f"{demo.snap.topology.id} · v{demo.snap.topology.version}"
    assert footer["Live tick"].startswith(f"{demo.live.tick} ·")
    assert footer["Data source"] == "demo · simulated input"


def test_geo_places_the_backend_nodes_to_scale(demo):
    topo = demo.topo
    located = [node for node in demo.live.nodes if node.located]
    assert sorted(topo.geo.placed_ids()) == sorted(node.id for node in located) and len(located) == 8
    metres_per_px = topo.geo.metres_per_px
    transform = topo.geo.transform()
    for node in located:
        expected = transform.to_px(node.longitude, node.latitude)
        position = topo.geo.node_position(node.id)
        assert (position.x(), position.y()) == pytest.approx(expected)
    for link in demo.live.links:
        a, b = demo.live.node(link.a), demo.live.node(link.b)
        if not (a.located and b.located):
            continue
        pa, pb = topo.geo.node_position(a.id), topo.geo.node_position(b.id)
        pixels = math.hypot(pa.x() - pb.x(), pa.y() - pb.y())
        # the fibre length the backend holds IS the geodesic distance, and the map draws it to scale
        assert link.distance_km == pytest.approx(geodesic_km(a.latitude, a.longitude, b.latitude, b.longitude),
                                                 abs=1e-5)
        assert pixels * metres_per_px / 1000.0 == pytest.approx(link.distance_km, rel=0.01), link.id
    length_km, length_px = topo.geo.scale_bar()
    assert length_px * metres_per_px == pytest.approx(length_km * 1000.0)


def test_unlocated_nodes_keep_connections_only_in_details_and_logical_views(demo):
    topo = demo.topo
    unlocated = [node.id for node in demo.live.unlocated_nodes]
    assert unlocated == ["N-12", "N-13"]
    assert topo.queue.isVisible() and topo.queue.node_ids() == unlocated
    assert topo.queue.title.text() == "Awaiting location · 2"
    assert not set(unlocated) & set(topo.geo.placed_ids())
    assert topo.geo.node_position("N-13") is None
    # Real connections remain in the inventory; a queue position is not a geographic endpoint.
    assert topo.queue.drawn_link_keys() == set()
    assert topo.queue.wires_for_map() == ({}, {})
    assert "N05-N13" not in topo.geo.drawn_link_keys()
    assert "N05-N13" in topo.matrix.link_keys()
    # N-12 has no fibre: no line at all
    assert all("N-12" not in (link.a, link.b) for link, *_rest in topo.geo.segments())
    assert topo.queue.chips["N-12"].summary.text() == "No location"
    assert topo.queue.chips["N-13"].summary.text() == "No location"
    assert "N-05" not in topo.queue.chips["N-13"].toolTip()
    # the queue takes part in the selection like the map: select + floating window
    demo.settle()
    QTest.mouseClick(topo.queue.chips["N-13"], Qt.MouseButton.LeftButton)
    demo.settle()
    assert demo.window.selection.kind == "node" and demo.window.selection.id == "N-13"
    assert topo.queue.chips["N-13"].on and not topo.queue.chips["N-12"].on
    assert "N-13" in topo.node_windows.windows
    assert topo.node_windows.windows["N-13"].field_texts()["latitude"] == "no location"
    assert "N05-N13" in topo.node_windows.windows["N-13"].connection_texts()
    topo.node_windows.close_all()


def test_matrix_and_geo_show_the_same_nodes_links_and_selection(demo):
    topo = demo.topo
    ids = [node.id for node in demo.snap.topology.nodes]
    link_ids = {link.id for link in demo.snap.topology.links}
    assert topo.matrix.node_ids() == ids                                           # located and unlocated
    assert sorted(topo.geo.placed_ids() + topo.queue.node_ids()) == sorted(ids)
    assert topo.matrix.link_keys() == link_ids
    located = set(topo.geo.placed_ids())
    assert topo.geo.drawn_link_keys() == {link.id for link in demo.snap.topology.links
                                        if link.a in located and link.b in located}
    assert {link.id for link in topo.graph.all_links} == link_ids
    # a click on a fibre handle on the map selects the fibre everywhere
    demo.settle()
    a, b = topo.geo.node_position("R-07"), topo.geo.node_position("CORE")
    QTest.mouseClick(topo.geo, Qt.MouseButton.LeftButton,
                     pos=QPointF((a.x() + b.x()) / 2, (a.y() + b.y()) / 2).toPoint())
    demo.settle()
    assert demo.window.selection.kind == "link" and demo.window.selection.id == "R07-CORE"
    assert topo.geo.net.selection == topo.matrix.net.selection == ("link", "R07-CORE")
    assert topo.graph.selection == ("link", "R07-CORE")
    assert topo.inspector.link_panel.title.full_text == "R07-CORE"
    assert topo.link_table.currentIndex().row() == [link.id for link in demo.snap.topology.links].index("R07-CORE")
    assert topo.cell_fidelity.label.text() == "Bell fidelity · R07-CORE"
    live = demo.live.link("R07-CORE")
    assert topo.matrix.cell_for("R-07", "CORE").text == f"{live.fidelity:.3f}"
    # a click on a matrix cell selects the same way; the map follows
    topo.set_view("MATRIX")
    topo.matrix._cell_clicked(topo.matrix.index_for("N-09", "N-11"))
    demo.settle()
    assert demo.window.selection.id == "N09-N11" and topo.geo.net.selection == ("link", "N09-N11")
    # the shell's selection (palette / terminal) reaches every view
    demo.window.select_node("N-02", navigate=False)
    demo.settle()
    assert topo.matrix.net.selection == topo.geo.net.selection == ("node", "N-02")
    assert topo.inspector.node_panel.title.full_text == "N-02"
    topo.set_view("GEO")


def test_clicking_a_node_opens_the_floating_window_with_every_backend_field(demo):
    topo = demo.topo
    demo.click_node("CORE")
    assert demo.window.selection.kind == "node" and demo.window.selection.id == "CORE"
    window = topo.node_windows.windows["CORE"]
    assert window.isVisible() and window.parentWidget() is topo and not window.isWindow()   # floats, non-modal
    node = demo.live.node("CORE")
    fields = window.field_texts()
    assert set(fields) == set(NODE_FIELDS)
    assert fields["id"] == "CORE" and fields["role"] == node.role == "repeater" and fields["kind"] == "core"
    assert fields["status"] == node.status and fields["located"] == "located"
    assert fields["latitude"] == "12.9770° S" and fields["longitude"] == "38.4550° W"
    assert fields["place"] == node.place == "Iguatemi"
    assert fields["links"] == ", ".join(node.links) and len(node.links) == 4
    assert fields["address"] == node.address and fields["port"] == str(node.port)
    assert fields["memory_state"] == node.memory_state
    assert fields["pairs"] == "none stored" and node.pairs == ()
    assert fields["temperature_c"] == f"{node.temperature_c:.2f} °C"
    assert fields["calibration_count"] == str(node.calibration_count)
    assert fields["routes_through"] == ", ".join(node.routes_through)
    assert fields["calibration"] == "none recorded" and node.calibration is None
    shown = {**window.identity.texts(), **window.state.texts()}
    for key in ("Role", "Kind", "Status", "Isolation", "Place", "Latitude", "Longitude", "Address", "Port",
                "Memory state · legacy inventory", "Stored pairs · legacy inventory", "Temperature",
                "Calibrations", "Routes through", "Fibres"):
        assert shown[key] not in ("", None), key
    assert shown["Memory state · legacy inventory"] == fields["memory_state"]
    assert shown["Stored pairs · legacy inventory"] == fields["pairs"]
    # one wave series per connected fibre; the legend names the peer and the fibre
    links = demo.live.node_links("CORE")
    assert window.chart.keys() == [link.id for link in links]
    labels = window.legend_labels()
    for link, label in zip(links, labels):
        peer = link.b if link.a == "CORE" else link.a
        assert peer in label and link.id in label and f"stability {link.stability:.3f}" in label
    # every connection shows the backend's values
    for link in links:
        texts = window.connection_texts()[link.id]
        assert texts["Fidelity"] == f"{link.fidelity:.4f}" and texts["QBER"] == f"{link.qber * 100:.2f} %"
        assert texts["Distance"] == f"{link.distance_km:.1f} km" and texts["status"] == link.status
        assert texts["Sync residual"] == f"{link.sync_residual_ps:.2f} ps"
        assert texts["Polarisation residual"] == f"{link.polarisation_residual_mrad:.1f} mrad"
        assert texts["Stability"] == f"{link.stability:.3f}" and "compensation:" in texts["controllers"]
    # the chart draws exactly the samples of `LiveLink.wave`, and follows the next delivery
    window.chart.freeze(1.0)
    for link in links:
        assert [value for _at, value in window.chart.visible_samples(link.id)] == list(link.wave)[-240:]
    before = {link.id: link.wave for link in links}
    tick = demo.live.tick
    demo.ticks(2)
    assert topo.node_windows.windows["CORE"] is window and window.isVisible()        # survives the refreshes
    for link in demo.live.node_links("CORE"):
        assert link.wave != before[link.id] and demo.live.tick > tick
        assert [value for _at, value in window.chart.visible_samples(link.id)] == list(link.wave)
    window.chart.freeze(None)
    # hovering / clicking a legend entry highlights its series
    row = window._legend["CORE-N05"]
    row.hovered.emit("CORE-N05")
    assert window.chart.highlight == "CORE-N05" and row.on
    row.hovered.emit("")
    assert window.chart.highlight == ""
    row.click()
    assert window.chart.highlight == "CORE-N05"
    row.click()
    assert window.chart.highlight == ""
    # a second window can be open at the same time; Esc closes the topmost, ✕ closes its own
    demo.click_node("N-05") if topo.geo.hit(topo.geo.node_position("N-05"))[1] == "N-05" else None
    topo._node_clicked("N-05")
    demo.settle()
    assert set(topo.node_windows.windows) == {"CORE", "N-05"}
    QTest.keyClick(demo.window, Qt.Key.Key_Escape)
    pump(demo.app)
    assert set(topo.node_windows.windows) == {"CORE"}
    window.close_button.click()
    pump(demo.app)
    assert not topo.node_windows.windows


def test_disabling_a_fibre_is_the_backends_decision_and_reroutes(demo):
    topo = demo.topo
    demo.select("link", "R07-CORE")
    before = {demand.id: demand for demand in demo.live.demands}
    using = [demand.id for demand in demo.live.demands if "R07-CORE" in demand.route_links]
    assert using and all(before[name].status in ("routed", "rerouted") for name in using)
    version = demo.snap.topology.version
    panel = topo.inspector.link_panel
    assert panel.toggle.text() == "Disable fibre" and panel.toggle.isEnabled()
    panel.toggle.click()
    # nothing is flipped by the click: PENDING until the backend answers
    assert topo._pending is not None and panel.status.text().startswith("PENDING · set_link_active · R07-CORE")
    assert topo._net.link("R07-CORE").configured_active is True and topo._net.link("R07-CORE").style != "off"
    assert not panel.toggle.isEnabled()
    demo.done()
    assert panel.status.text().startswith("CONFIRMED · set_link_active · R07-CORE")
    command = demo.snap.ledger.commands[0] if demo.snap.ledger.commands[0].verb == "set_link_active" else \
        next(item for item in demo.snap.ledger.commands if item.verb == "set_link_active")
    assert command.state == "CONFIRMED" and command.command_id[:8] in panel.status.text()
    assert demo.snap.topology.version == version + 1
    link = demo.live.link("R07-CORE")
    assert link.status == "disabled" and not link.effective and link.wave == ()
    # disabled styling in every view
    assert topo.geo.net.link("R07-CORE").style == "off" and topo.matrix.cell_for("R-07", "CORE").text == "OFF"
    assert topo.matrix.cell_for("R-07", "CORE").style == "off"
    assert panel.toggle.text() == "Enable fibre"
    assert "disabled" in panel.chips.texts() and "out of routing graph" in panel.chips.texts()
    assert topo.links_up.text().startswith(f"{sum(item.effective for item in demo.live.links)}/")
    # the backend re-planned the demands that used the fibre; the Pairs tab shows the new state
    for name in using:
        demand = demo.live.demand(name)
        assert "R07-CORE" not in demand.route_links
        row = topo.inspector.pairs_panel.rows[name]
        assert row.state.text() == demand.status.upper().replace("_", " ")
        if demand.status in ("no_route", "blocked"):
            assert demand.reason and row.reason.text() == demand.reason and not row.reason.isHidden()
        else:
            assert demand.status == "rerouted" and demand.route != before[name].route
            assert " → ".join(demand.route) in row.route.text()
    decisions = [item for item in demo.live.decisions if item.subject in using and item.kind in ("reroute", "no_route")
                 and item.tick >= before[using[0]].changed_at_tick]
    assert decisions
    topo.inspector.show_demand(using[0])
    assert topo.inspector.tab == "pairs" and topo.inspector.pairs_panel.demand_id == using[0]
    assert "tick" in topo.inspector.pairs_panel.decisions.text()
    assert "fibre R07-CORE disabled" in topo.inspector.pairs_panel.kv.value("Trigger")
    unrouted = [demand for demand in demo.live.demands if demand.status in ("no_route", "blocked")]
    assert [item[0] for item in topo.geo.alerts] == [demand.id for demand in unrouted]
    assert topo.inspector.tabs["pairs"].text() == ("Pairs ●" if unrouted else "Pairs")
    for demand in unrouted:                      # the backend's reason, prominently, on the map banner too
        assert any(item[0] == demand.id and item[2] == demand.reason for item in topo.geo.alerts)
    # the node window of an end shows the fibre without a trace and says why
    topo._open_window("R-07")
    window = topo.node_windows.windows["R-07"]
    demo.settle()
    assert window.chart.visible_samples("R07-CORE") == []
    assert "no trace · disabled" in window._legend["R07-CORE"].text()
    assert window.connection_texts()["R07-CORE"]["status"] == "disabled"
    topo.node_windows.close_all()
    # enable it again through the same button
    demo.select("link", "R07-CORE")
    panel.toggle.click()
    demo.done()
    assert panel.status.text().startswith("CONFIRMED")
    demo.ticks(2)
    assert demo.live.link("R07-CORE").status == "up" and demo.live.link("R07-CORE").effective
    assert topo.matrix.cell_for("R-07", "CORE").style == "calm" and panel.toggle.text() == "Disable fibre"
    assert not demo.errors


def test_isolating_a_node_shows_no_route_with_the_backends_reason_and_restores(demo):
    topo = demo.topo
    demo.ticks(1)
    demo.click_node("N-09")
    window = topo.node_windows.windows["N-09"]
    assert window.isolate.text() == "Isolate" and window.isolate.isEnabled()
    window.isolate.click()
    assert window.status.text().startswith("PENDING · set_node_isolation · N-09")
    assert demo.live.node("N-09").status != "isolated"            # not flipped by the click
    demo.done()
    assert window.status.text().startswith("CONFIRMED · set_node_isolation · N-09")
    node = demo.live.node("N-09")
    assert node.status == "isolated"
    assert "isolated" in window.chips.texts() and window.isolate.text() == "Restore"
    assert "isolated" in window.identity.value("Isolation")
    assert topo.geo.net.node("N-09").isolated and "○ 1 isolated" in [text for text, _tone, _tip in topo._health_chips(0)]
    for link_id in node.links:
        assert demo.live.link(link_id).status == "isolated" and topo.geo.net.link(link_id).style == "off"
        assert "no trace · isolated" in window._legend[link_id].text()
    # P4 ends at N-09: the backend reports no route and says why — shown in three places
    demand = demo.live.demand("P4")
    assert demand.status == "no_route" and "N-09" in demand.reason
    assert window.unrouted.text() == f"P4 NO ROUTE — {demand.reason}" and not window.unrouted.isHidden()
    assert ("P4", "■ P4 N-09 ↔ N-03 · NO ROUTE", demand.reason) in topo.geo.alerts
    row = topo.inspector.pairs_panel.rows["P4"]
    assert row.state.text() == "NO ROUTE" and row.reason.text() == demand.reason
    assert topo.inspector.tabs["pairs"].text() == "Pairs ●"
    topo.inspector.show_demand("P4")
    assert topo.inspector.pairs_panel.blocked.text() == f"NO ROUTE — {demand.reason}"
    # a backend refusal is shown with the backend's message: enabling a fibre whose end is isolated
    demo.select("link", "R07-N09")
    panel = topo.inspector.link_panel
    assert panel.toggle.text() == "Enable fibre"
    version = demo.snap.topology.version
    errors = len(demo.errors)
    panel.toggle.click()
    demo.done()
    assert len(demo.errors) == errors + 1 and "isolated" in demo.errors[-1]
    assert panel.status.text().startswith("UNKNOWN · set_link_active") and "isolated" in panel.status.text()
    assert demo.snap.topology.version == version and demo.live.link("R07-N09").status == "isolated"
    assert any(item.verb == "set_link_active" and item.state == "UNKNOWN" for item in demo.snap.ledger.commands)
    # restore from the window: exactly the fibres the isolation switched off come back
    demo.settle()
    window.isolate.click()
    demo.done()
    assert window.status.text().startswith("CONFIRMED · set_node_isolation · N-09")
    demo.ticks(2)
    assert demo.live.node("N-09").status in ("up", "degraded") and window.isolate.text() == "Isolate"
    assert all(demo.live.link(link_id).effective for link_id in demo.live.node("N-09").links)
    assert demo.live.demand("P4").status in ("routed", "rerouted") and not topo.geo.alerts
    assert topo.inspector.tabs["pairs"].text() == "Pairs"
    topo.node_windows.close_all()


def test_recalibration_shows_calibrating_then_the_report(demo):
    topo = demo.topo
    demo.click_node("N-11")
    window = topo.node_windows.windows["N-11"]
    count = demo.live.node("N-11").calibration_count
    seen: list = []

    def record(snapshot):
        node = snapshot.live.node("N-11") if snapshot.live is not None else None
        if node is not None and node.calibration is not None and node.status == "calibrating":
            seen.append((node.calibration.phase, window.calibration_progress.isVisible(),
                         window.calibration_phase.text(), tuple(window.chips.texts()),
                         window.status.text()))

    demo.window.provider.snapshot_changed.connect(record)
    try:
        window.recalibrate.click()
        assert window.status.text().startswith("PENDING · recalibrate_node · N-11")
        demo.done()
    finally:
        demo.window.provider.snapshot_changed.disconnect(record)
    # while the backend ran it: status calibrating, phase and progress on screen
    assert seen, "no calibrating state was published while the recalibration ran"
    phases = [phase for phase, *_rest in seen]
    assert set(phases) <= {"queued", "polarisation", "sync", "commissioning", "memory", "report", "complete"}
    assert len(set(phases)) >= 2
    for phase, visible, text, chips, status in seen:
        assert visible and f"phase {phase}" in text and "calibrating" in chips
        assert status.startswith(("PENDING", "RUNNING"))
    # afterwards: the report the backend persisted, before / after per fibre
    assert window.status.text().startswith("CONFIRMED · recalibrate_node · N-11")
    node = demo.live.node("N-11")
    assert node.calibration_count == count + 1 and node.calibration.status == "complete"
    assert window.field_texts()["calibration_count"] == str(count + 1)
    assert not window.calibration_progress.isVisible()
    # Command confirmation is a backend readback. Wait for the real queued Qt
    # presentation before inspecting its rows; never force materialization.
    demo.settle()
    report = window.calibration_kv.texts()
    assert report["Calibration"] == node.calibration.calibration_id and report["Result"].startswith("complete")
    assert report["Report"].startswith(node.calibration.report_sha256[:16])
    rows = window.calibration_links.texts()
    assert {item.link_id for item in node.calibration.links} == set(node.links)
    for item in node.calibration.links:
        assert rows[f"{item.link_id} · QBER"] == f"{item.qber_before * 100:.2f} % → {item.qber_after * 100:.2f} %"
        assert rows[f"{item.link_id} · sync"].endswith(f"{item.sync_residual_ps_after:.2f} ps")
        assert item.status in rows[f"{item.link_id} · after"]
    assert topo.inspector.node_panel.kv.value("Calibrations") == str(count + 1)
    topo.node_windows.close_all()
    demo.ticks(2)


def test_set_location_moves_a_queued_node_onto_the_map_with_its_links(demo):
    topo = demo.topo
    before = demo.live.node("N-13")
    assert not before.located and before.links == ("N05-N13",)
    configured_km = demo.live.link("N05-N13").distance_km
    demo.settle()
    topo.queue.chips["N-13"].button.click()                     # "Set location…" on the queue item
    dialog = topo.location_dialog
    assert dialog.isVisible() and dialog.node_id == "N-13" and not dialog.isModal()
    assert dialog.fields["latitude"].text() == "" and dialog.apply.isEnabled()
    # pick on map: a click fills latitude / longitude with the point under the cursor
    dialog.pick.click()
    assert topo.geo.picking and topo.view == "GEO" and topo.node_popover.isVisible()
    point = topo.geo.rect().center()
    lon, lat = topo.geo.transform().to_lonlat(float(point.x()), float(point.y()))
    QTest.mouseClick(topo.geo, Qt.MouseButton.LeftButton, pos=point)
    assert not topo.geo.picking and dialog.isVisible()
    assert float(dialog.fields["latitude"].text()) == pytest.approx(lat, abs=1e-6)
    assert float(dialog.fields["longitude"].text()) == pytest.approx(lon, abs=1e-6)
    assert demo.live.node("N-13").located is False               # picking dispatches nothing
    # invalid input is refused before dispatch
    dialog.fields["latitude"].setText("ninety")
    dialog.apply.click()
    assert "Latitude" in dialog.note.text() and topo._pending is None
    dialog.fields["latitude"].setText("-12.9796")
    dialog.fields["longitude"].setText("-38.4296")
    dialog.fields["place"].setText("Boca do Rio")
    assert "municipality of Salvador" in dialog.where.text()
    dialog.apply.click()
    assert topo._pending is not None and topo._pending.verb == "set_node_location"
    demo.done()
    assert not dialog.isVisible()
    # the node left the queue and is on the map at its coordinates, with data and connection preserved
    node = demo.live.node("N-13")
    assert node.located and (node.latitude, node.longitude, node.place) == (-12.9796, -38.4296, "Boca do Rio")
    assert node.links == before.links and node.address == before.address and node.port == before.port
    assert topo.queue.node_ids() == ["N-12"] and "N-13" in topo.geo.placed_ids()
    position = topo.geo.node_position("N-13")
    assert (position.x(), position.y()) == pytest.approx(topo.geo.transform().to_px(-38.4296, -12.9796))
    segment = next(item for item in topo.geo.segments() if item[0].key == "N05-N13")
    assert segment[3] == "" and topo.queue.drawn_link_keys() == set()
    # the backend recomputed the fibre length: geodesic, and the map draws it to scale
    link = demo.live.link("N05-N13")
    peer = demo.live.node("N-05")
    assert link.distance_km != configured_km
    assert link.distance_km == pytest.approx(geodesic_km(node.latitude, node.longitude, peer.latitude,
                                                         peer.longitude), abs=1e-5)
    pixels = math.hypot(position.x() - topo.geo.node_position("N-05").x(),
                        position.y() - topo.geo.node_position("N-05").y())
    assert pixels * topo.geo.metres_per_px / 1000.0 == pytest.approx(link.distance_km, rel=0.01)
    assert topo.matrix.node_ids() == [item.id for item in demo.snap.topology.nodes]
    assert "◌" not in topo.matrix.records.headerData(topo.matrix.node_ids().index("N-13") + 1,
                                                    Qt.Orientation.Horizontal)
    assert topo.geo.placed.text() == "9 instalações localizadas · 0 orbitais · 1 aguardando localização"
    # and back to the queue ("Return to queue" in the same dialog) — the connection is still kept
    topo._locate("N-13")
    assert dialog.unlocate.isVisible() and dialog.fields["place"].text() == "Boca do Rio"
    dialog.unlocate.click()
    demo.done()
    assert topo.queue.node_ids() == ["N-12", "N-13"] and demo.live.node("N-13").links == ("N05-N13",)
    assert topo.queue.drawn_link_keys() == set() and "N05-N13" not in topo.geo.drawn_link_keys()
    dialog.close()
    assert not demo.errors[-1:] or "set_node_location" not in demo.errors[-1]


def test_pair_operations_are_read_back_from_the_backend(demo):
    topo = demo.topo
    demo.ticks(1)
    topo.inspector.show_demand("P2")
    panel = topo.inspector.pairs_panel
    demand = demo.live.demand("P2")
    assert panel.demand_id == "P2" and panel.kv.value("Policy") == demand.policy_id
    assert panel.kv.value("Route") == " → ".join(demand.route)
    assert panel.kv.value("In contract").startswith("no") and demand.in_contract is False
    assert panel.kv.value("Model state") == demand.detail["model_state"]
    assert panel.kv.value("Plan · delivered fidelity") == f"{demand.fidelity:.4f}"
    assert panel.pair_kv.value("Pair") == "not requested"
    assert panel.request.isEnabled() and not panel.purify.isEnabled() and not panel.consume.isEnabled()
    # entanglement layer: the selected demand's route and END / BSM marks
    topo.set_layer("entanglement")
    assert topo.geo.net.route == demand.route
    assert topo.geo.net.badge_map == {"N-03": "END", "N-05": "END", "CORE": "BSM"}
    topo.set_layer("physical")
    assert topo.geo.net.route == ()
    demo.settle()
    panel.request.click()
    assert panel.status.text().startswith("PENDING · request_pair · P2")
    demo.done()
    pair = demo.live.demand("P2").pair
    assert pair is not None and pair["status"] in ("delivered", "failed")
    event = next(item for item in reversed(demo.live.events) if item.subject == "P2" and item.kind.startswith("pair"))
    assert panel.status.text() == f"{event.kind.upper().replace('_', ' ')} · {event.message}"
    assert panel.pair_kv.value("Pair") == pair["status"]
    if pair["status"] == "delivered":
        assert panel.pair_kv.value("Current F") == f"{pair['fidelity']:.4f}"
        assert panel.purify.isEnabled() and panel.consume.isEnabled()
        panel.consume.click()
        demo.done()
        assert demo.live.demand("P2").pair["status"] == "consumed"
        assert panel.pair_kv.value("Pair") == "consumed" and not panel.consume.isEnabled()
        assert panel.status.text().startswith("PAIR CONSUMED")


def test_theme_rebuild_restores_view_state_and_node_windows(demo):
    topo = demo.topo
    demo.click_node("CORE")
    topo._open_window("N-02")
    topo.node_windows.windows["N-02"].move_within(topo.node_windows.windows["N-02"].pos() + type(
        topo.node_windows.windows["N-02"].pos())(-40, 25))
    place = topo.node_windows.windows["N-02"].pos()
    topo.set_layer("entanglement")
    topo.inspector.set_tab("links")
    topo.geo.zoom_by(1.7)
    view = topo.geo.view_state()
    demo.settle()
    assert demo.window.set_theme("light")                 # node windows do not block the rebuild
    pump(demo.app)
    rebuilt = demo.topo
    assert rebuilt is not topo and theme.current_theme() == "light"
    assert rebuilt.view == "GEO" and rebuilt.layer == "entanglement" and rebuilt.inspector.tab == "links"
    assert rebuilt.geo.view_state() == pytest.approx(view)
    assert list(rebuilt.node_windows.windows) == ["CORE", "N-02"]
    assert rebuilt.node_windows.windows["N-02"].pos() == place
    assert rebuilt.node_windows.windows["CORE"].chart.keys() == list(demo.live.node("CORE").links)
    assert not topo.node_windows.windows                  # the old screen closed its own
    assert demo.window.selection.id == "CORE" and rebuilt.geo.net.selection == ("node", "CORE")
    rebuilt.node_windows.close_all()
    rebuilt.set_layer("physical")
    rebuilt.geo.reset_view()
    rebuilt.inspector.set_tab("insp")
    assert demo.window.set_theme("dark")
    pump(demo.app)
    assert demo.topo.view == "GEO" and not demo.topo.node_windows.windows


def test_topology_screen_is_registered_first(app):
    assert SCREENS[0] is TopologyScreen
