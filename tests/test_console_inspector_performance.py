"""Presentation work follows the visible pane while published evidence stays current.

Synthetic records here exercise only the UI contracts; they attest no optical link.
"""
import os
from dataclasses import replace
from copy import deepcopy

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import pytest
pytest.importorskip("PySide6.QtWidgets")
from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication, QWidget

from quantum_autopilot.console import theme
from quantum_autopilot.console.contract import Link, LiveDemand, LiveEvent, LiveNode, LiveView, Node
from quantum_autopilot.console.providers import MockProvider
from quantum_autopilot.console.widgets.inspector import CommandStatus, Inspector, KVList
from quantum_autopilot.console.widgets.network_views import build_net_model
from quantum_autopilot.console.widgets.node_window import NODE_FIELDS, NodeWindows
from quantum_autopilot.console.widgets.satellite_telemetry import SatelliteLivePanel

pytestmark = pytest.mark.unit


@pytest.fixture(params=["dark", "light"])
def app(request):
    current = QApplication.instance() or QApplication([])
    previous = theme.current_theme()
    theme.set_theme(request.param); theme.install(current)
    yield current
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    theme.set_theme(previous); theme.install(current)


def observation_snapshot():
    provider = MockProvider()
    snap = provider.snapshot()
    topology = replace(snap.topology, nodes=[Node("SAT", "REPEATER", physical_type="satellite"),
        Node("OGS-A", "ENDPOINT"), Node("OGS-B", "ENDPOINT")], links=[
        Link("SAT", "OGS-A", "A", id="A"), Link("SAT", "OGS-B", "B", id="B")], events=[])
    bridge = {"session_id": "BRIDGE", "status": "observed_pairs", "protocol": "BBM92",
        "technologies": ["satellite_doppler"], "path": ["OGS-A", "SAT", "OGS-B"],
        "origin": "synthetic fixture", "endpoints": ["OGS-A", "OGS-B"],
        "metrics": {"fidelity": .9375, "qber": .03125, "coincidences": 7,
                    "geometry": [{"visible": True, "range_km": 1000.},
                                 {"visible": False, "range_km": 3000.}]},
        "connections": [{"link_id": key, "station_id": "OGS-" + key,
            "classical": {"channel_id": "CTRL-" + key, "enabled": True},
            "quantum": {"channel_id": "PAYLOAD-" + key, "enabled": True}} for key in ("A", "B")]}
    state = {"device_id": "SAT", "node_id": "SAT", "clock_s": 3., "origin": "synthetic fixture",
             "at_network_s": 2., "altitude_km": 550., "ecef_km": [6900., 200., 40.],
             "housekeeping": {"at_s": 2., "valid_until_s": 4.}, "bridges": [bridge],
             "transmitting_links": [{"link_id": "A"}]}
    live = LiveView(1, 3., "simulated", "Synthetic UI observations",
        nodes=(LiveNode("SAT", "repeater", "satellite", "up", links=("A", "B"),
                        temperature_c=21.5, calibration_count=3, routes_through=("D",)),),
        satellites={"SAT": state}, communicators={"SAT": {"name": "Fixture satellite"}},
        demands=(LiveDemand("D", "OGS-A", "OGS-B", "no_route", reason="visibility unavailable"),),
        events=(LiveEvent("EVENT", 1, "2026-10-07T00:00:00Z", "link", "A", "warning", "No visibility"),))
    return replace(snap, topology=topology, live=live)


def test_satellite_prepared_before_first_show_has_science_without_hidden_fibre_trees(app):
    host = QWidget(); host.resize(1200, 800); host.show()
    windows = NodeWindows(host)
    snap = observation_snapshot(); model = build_net_model(snap)
    observations = []

    def prepare(window):
        observations.append(window.isVisible())
        assert window.refresh(snap, model, CommandStatus(("node", "SAT"), "CONFIRMED", "green"))

    try:
        window = windows.open("SAT", prepare=prepare)
        app.processEvents()
        assert observations == [False] and window.isVisible()
        assert set(window.field_texts()) == set(NODE_FIELDS)
        assert window.field_texts()["temperature_c"] == "21.50 °C"
        assert window.field_texts()["links"] == "A, B"
        assert window.status.text() == "CONFIRMED"
        assert not window._cards and not window._legend and not window.chart.series
        assert not window.chart._timer.isActive() and window.chart.isHidden()
        panel = window.satellite_panel
        assert panel.bridge.value("Fidelidade conjunta") == "0.9375"
        assert panel.bridge.value("Coincidências / taxa").startswith("7 /")
        assert "CTRL-A" in panel.stations["A"].value("Canal clássico")
        assert "PAYLOAD-A" in panel.stations["A"].value("Payload quântico")
        assert panel.stations["A"].value("Emissão recebida").startswith("Sim")
        state = deepcopy(snap.live.satellites["SAT"])
        state["clock_s"] = 5.; state["transmitting_links"] = []
        state["bridges"][0]["metrics"]["fidelity"] = .8125
        updated = replace(snap, live=replace(snap.live, satellites={"SAT": state}))
        window.refresh(updated, build_net_model(updated), CommandStatus())
        assert panel.bridge.value("Fidelidade conjunta") == "0.8125"
        assert "OBSERVAÇÃO VENCIDA" in panel.summary.text()
        assert panel.stations["A"].value("Emissão recebida").startswith("Não comprovada")
        windows.close("SAT")
        assert not windows.windows and window.parent() is host
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    finally:
        windows.shutdown(); host.close(); host.deleteLater()


def test_inspector_defers_hidden_cards_and_delivers_latest_explicit_readback(app, monkeypatch):
    inspector = Inspector()
    calls = []
    events = inspector._events_panel
    pairs = inspector._pairs_panel
    original_events, original_pairs = events.show_events, pairs.show_pairs
    monkeypatch.setattr(events, "show_events", lambda *a, **k: (calls.append("events"), original_events(*a, **k))[-1])
    monkeypatch.setattr(pairs, "show_pairs", lambda *a, **k: (calls.append("pairs"), original_pairs(*a, **k))[-1])
    snap = observation_snapshot()
    try:
        inspector.refresh(snap, build_net_model(snap), "D", CommandStatus())
        assert not calls and not events.cards and not pairs.rows
        assert inspector.tabs["events"].text() == "Events 1"
        assert inspector.tabs["pairs"].text() == "Pairs ●"
        updated = replace(snap, live=replace(snap.live, demands=(
            replace(snap.live.demands[0], status="blocked", reason="memory expired"),)))
        inspector.refresh(updated, build_net_model(updated), "D", CommandStatus())
        assert not calls
        assert inspector.pairs_panel.rows["D"].reason.text() == "memory expired"
        assert calls == ["pairs"]
        inspector.set_tab("pairs")
        assert calls == ["pairs"]
        inspector.set_tab("events")
        assert calls == ["pairs", "events"] and len(inspector.events_panel.cards) == 1
        inspector.set_tab("events")
        assert calls == ["pairs", "events"]
        styles = []
        button = inspector.tabs["pairs"]
        original_style = button.setStyleSheet
        monkeypatch.setattr(button, "setStyleSheet", lambda style: (styles.append(style), original_style(style))[-1])
        inspector.refresh(updated, build_net_model(updated), "D", CommandStatus())
        inspector.set_tab("events")
        assert not styles
        inspector.set_tab("pairs")
        assert styles and inspector.tabs["pairs"].text() == "Pairs ●"
    finally:
        inspector.close(); inspector.deleteLater()


def test_kv_rows_reuse_identity_and_update_units_tooltip_and_theme(app):
    table = KVList()
    try:
        table.set_rows([("Range", "1000 km", "amber"), ("QBER", "2.00 %", "ink")])
        first = table.rows["Range"]
        table.set_rows([("QBER", "3.00 %", "ink"), ("Range", "800 km", "amber"), ("Origin", "fixture")])
        assert table.rows["Range"] is first
        assert table.value("Range") == first.value_label.toolTip() == "800 km"
        style = first.value_label.styleSheet()
        theme.set_theme("light" if theme.current_theme() == "dark" else "dark")
        table.set_rows([("Range", "800 km", "amber")])
        assert table.rows["Range"] is first and first.value_label.styleSheet() != style
        assert list(table.rows) == ["Range"]
    finally:
        table.close(); table.deleteLater()


def test_satellite_station_addition_preserves_existing_widgets_and_distinct_channels(app):
    panel = SatelliteLivePanel(); snap = observation_snapshot()
    state = deepcopy(snap.live.satellites["SAT"])
    try:
        panel.set_state(state, {"name": "Fixture satellite"})
        first = panel.stations["A"]
        extra = deepcopy(state["bridges"][0]["connections"][0])
        extra.update(link_id="C", station_id="OGS-C")
        state["bridges"][0]["connections"].append(extra)
        panel.set_state(state, {"name": "Fixture satellite"})
        assert panel.stations["A"] is first and set(panel.stations) == {"A", "B", "C"}
        state["bridges"][0]["connections"] = state["bridges"][0]["connections"][1:]
        panel.set_state(state, {"name": "Fixture satellite"})
        assert set(panel.stations) == {"B", "C"}
        assert "CTRL-A" in panel.stations["C"].value("Canal clássico")
        assert "PAYLOAD-A" in panel.stations["C"].value("Payload quântico")
    finally:
        panel.close(); panel.deleteLater()
