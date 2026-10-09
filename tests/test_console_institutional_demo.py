"""GEO access, switch confirmations and readback using the actual simulated service."""
import json
import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import pytest
from PySide6.QtCore import QPointF, QTimer, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from quantum_autopilot.console.geodata import load_brazil, load_world
from quantum_autopilot.console.service_provider import ServiceProvider
from quantum_autopilot.console.widgets.geo_map import GeoMap
from quantum_autopilot.console.widgets.ml_control import MLControlPanel
from quantum_autopilot.console.widgets.network_views import build_net_model
from quantum_autopilot.network_engine.institutional import PRESET_ID, SITES
from quantum_autopilot.qkd.dashboard_api import demo_service

pytestmark = pytest.mark.unit
ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def app():
    from quantum_autopilot.console.theme import install
    instance = QApplication.instance() or QApplication([])
    install(instance)
    return instance


@pytest.fixture
def provider(tmp_path, app):
    service = demo_service(ROOT, tmp_path, preset=PRESET_ID, scenario="quiet")
    service.network_step()
    p = ServiceProvider(service, state_root=tmp_path / "demo")
    p.refresh()
    yield p
    p.stop()


@pytest.mark.parametrize("size", [(660, 460), (1000, 650), (1440, 900)])
def test_national_fit_and_controls_are_reachable(app, provider, size):
    geo = GeoMap(); geo.resize(*size); geo.set_model(build_net_model(provider.snapshot())); geo.show()
    app.processEvents()
    assert geo.basemap is load_world() and geo.metres_per_px > 260
    for ident, *_ in SITES:
        if ident=="UNIJORGE": continue
        node = geo.node_position(ident)
        assert node is not None and geo.rect().contains(node.toPoint())
    assert not geo.info.geometry().intersects(geo.controls.geometry())
    assert geo.rect().contains(geo.controls.geometry())
    net=geo.net
    # Configured orbital associations remain discoverable when transmission
    # is unavailable; their dotted style must not imply received photons.
    expected={l.id for l in net.links if all(net.node(n).located for n in (l.a,l.b))}
    assert geo.drawn_link_keys()==expected
    assert not geo.grab().isNull()
    geo.close()


@pytest.mark.parametrize("ident", [s[0] for s in SITES if s[0]!="UNIJORGE"])
def test_every_institution_can_be_focused_and_selected(app, provider, ident):
    geo = GeoMap(); geo.resize(1000, 650); geo.set_model(build_net_model(provider.snapshot()))
    selected = []; geo.node_selected.connect(selected.append)
    geo.focus_node.setCurrentIndex(geo.focus_node.findData(ident))
    assert selected == [ident]
    centre = geo.node_position(ident)
    assert abs(centre.x() - 500) < 0.01 and abs(centre.y() - 325) < 0.01
    assert geo.hit(centre) == ("node", ident)
    geo.reset.click()
    assert geo.basemap is load_world() and geo.is_default_view
    geo.close()


def test_cancelling_switch_does_not_send_command_or_move_thumb(app, provider):
    panel = MLControlPanel(); panel.set_snapshot(provider.snapshot()); panel.show()
    sent = []; panel.command.connect(lambda *args: sent.append(args))
    QTimer.singleShot(50, lambda: QTest.mouseClick(panel.dialog.cancel_button, Qt.MouseButton.LeftButton))
    QTest.mouseClick(panel.switch, Qt.MouseButton.LeftButton)
    assert sent == [] and panel.switch.enabled_mode is False and panel.switch.isEnabled()
    panel.close()


def test_both_confirmations_dispatch_and_only_readback_moves_switch(app, provider):
    panel = MLControlPanel(); panel.set_snapshot(provider.snapshot()); panel.show()
    sent = []; panel.command.connect(lambda *args: sent.append(args))
    for enabled in (True, False):
        QTimer.singleShot(50, lambda: QTest.mouseClick(panel.dialog.confirm_button, Qt.MouseButton.LeftButton))
        QTest.mouseClick(panel.switch, Qt.MouseButton.LeftButton)
        verb, target = sent[-1]
        assert verb == "set_ml_control" and json.loads(target)["enabled"] is enabled
        assert panel.switch.enabled_mode is not enabled and not panel.switch.isEnabled()
        provider.set_ml_control(target)
        panel.set_snapshot(provider.snapshot())
        assert panel.switch.enabled_mode is enabled and panel.switch.isEnabled()
    assert len(sent) == 2
    panel.close()


def test_backend_refusal_releases_pending_and_keeps_previous_mode(app, provider):
    panel = MLControlPanel(); panel.set_snapshot(provider.snapshot())
    panel._pending = (True, 0); panel.set_snapshot(provider.snapshot())
    panel.command_failed("stale revision")
    assert panel.switch.isEnabled() and not panel.switch.enabled_mode
    assert "refused" in panel.switch.toolTip()


def test_source_without_live_control_cannot_toggle(app):
    from quantum_autopilot.console.providers import MockProvider
    panel = MLControlPanel(); panel.set_snapshot(MockProvider().snapshot())
    assert panel.isHidden() and not panel.switch.isEnabled()


def test_changing_source_while_confirmation_is_open_cancels_command(app, provider):
    from quantum_autopilot.console.providers import MockProvider

    panel = MLControlPanel(); panel.set_snapshot(provider.snapshot()); panel.show()
    sent = []; panel.command.connect(lambda *args: sent.append(args))
    QTimer.singleShot(50, lambda: panel.set_snapshot(MockProvider().snapshot()))
    QTest.mouseClick(panel.switch, Qt.MouseButton.LeftButton)
    assert sent == [] and panel.dialog is None and panel.isHidden()
    assert panel._pending is None and not panel.switch.isEnabled()
    panel.close()


def test_topology_screen_contains_switch_in_responsive_layout(app, provider):
    from quantum_autopilot.console.screens.topology import TopologyScreen
    screen = TopologyScreen(); screen.resize(1180, 800)
    screen._snap = provider.snapshot(); screen.refresh(screen._snap); screen.show(); app.processEvents()
    assert screen.ml_control.switch.isVisible() and screen.ml_control.switch.isEnabled()
    assert screen.strip.rect().contains(screen.ml_control.geometry())
    screen.grab().save(str(ROOT / "outputs/demo_institutional_20261005_v1/topology.png"))
    screen.shutdown(); screen.close()
