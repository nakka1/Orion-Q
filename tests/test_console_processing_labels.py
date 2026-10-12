"""Received algorithm provenance, never institutions/toggle or invented activity."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import os

import pytest

from quantum_autopilot.console.contract import LiveController, LiveDetector, LiveLink
from quantum_autopilot.console.processing_labels import link_processing_labels

pytestmark = pytest.mark.unit


def observed(*controllers, source="simulated", detector_state="monitoring"):
    return LiveLink("LINK", "IBM-WATSON", "CIMATEC", 10., "up",
        controllers=tuple(controllers), input=source, detector=LiveDetector(state=detector_state))


def protocol(technology="tf_qkd", implementation="TwinFieldAdapter", *, received=True, source="simulated"):
    metrics = {"last_observed_at_s": 5.} if received else {}
    link = LiveLink("ARM", "GROUND", "SAT", 1000., "up" if received else "no_input",
        provenance={"technology": {"technologies": [technology], "session_id": "S"},
                    "source": source, "protocol_metrics": metrics,
                    "channels": {"status": "received" if received else "waiting"}})
    registry = {"registry": {"adapters": {technology: {"implementation": implementation}}}}
    return link, registry


def test_data_origin_is_independent_from_actual_control_ids_and_node_names():
    controllers = (LiveController("compensation", "baseline.alpha_beta", "unavailable"),
                   LiveController("keyrate", "baseline.static", "disabled"))
    simulated = link_processing_labels(observed(*controllers))
    measured = link_processing_labels(observed(*controllers, source="measured"))
    assert simulated.badge == measured.badge == "REFERENCE"
    assert simulated.controls == measured.controls
    assert simulated.data_source == "simulated" and measured.data_source == "measured"
    renamed = replace(observed(*controllers), a="US-GOOG-QA", b="SAT-OTHER")
    assert link_processing_labels(renamed) == simulated


def test_project_control_does_not_replace_reference_physics_label():
    result = link_processing_labels(observed(
        LiveController("compensation", "qnet.compensation.calibrado.ridge", "active"),
        LiveController("keyrate", "baseline.static", "disabled")))
    assert result.badge == "REF + MODEL"
    assert "Werner" in result.physics and "reference" in result.physics
    assert "project model" in result.controls and "reference control" in result.controls
    assert "qnet.compensation.calibrado.ridge" in result.detail


def test_reviewed_detector_is_labelled_even_without_learned_control():
    result = link_processing_labels(observed(
        LiveController("compensation", "baseline.alpha_beta", "disabled"),
        LiveController("detector", "qkd.detector.boosting", "active")))
    assert result.badge == "REF + MODEL"
    assert "detector: qkd.detector.boosting" in result.controls


def test_detector_warmup_is_not_claimed_as_completed_model_processing():
    result = link_processing_labels(observed(
        LiveController("compensation", "baseline.alpha_beta", "disabled"),
        LiveController("detector", "qkd.detector.boosting", "active"),
        detector_state="session baseline (3 of 60 periods)"))
    assert result.badge == "REF / WARMUP"
    assert "waiting for detector baseline" in result.controls


def test_effective_baseline_and_explicit_fallback_are_preserved():
    result = link_processing_labels(observed(LiveController("sync", "baseline.alpha_beta", "fallback")))
    assert result.fallback and result.badge == "REFERENCE / FALLBACK"
    assert "sync: baseline.alpha_beta" in result.detail
    assert "state=fallback" in result.detail


def test_project_id_with_missing_effective_fallback_identity_remains_unknown():
    result = link_processing_labels(observed(LiveController("sync", "qkd.sync.ridge.1s.h2s", "fallback")))
    assert result.badge == "UNREPORTED / FALLBACK"
    assert "origin unreported" in result.controls


@pytest.mark.parametrize("source", ["none", "withheld", "unreported"] )
def test_configured_ids_without_received_data_do_not_create_activity(source):
    result = link_processing_labels(observed(LiveController("keyrate", "qkd.keyrate.actor", "active"), source=source))
    assert result.badge == "NO SAMPLE" and not result.observed
    assert "configured IDs are not activity" in result.detail


@pytest.mark.parametrize("state", ["disabled", "unavailable", "rejected", None])
def test_known_model_id_without_active_state_does_not_claim_model_execution(state):
    result = link_processing_labels(observed(LiveController("keyrate", "qkd.keyrate.actor", state)))
    assert result.badge == "UNREPORTED"
    assert "origin unreported" in result.controls


def test_unknown_algorithm_is_never_classified_as_reference_or_project():
    result = link_processing_labels(observed(LiveController("compensation", "unregistered.plugin", "active")))
    assert result.badge == "UNREPORTED"
    assert "effective algorithm origin unreported" in result.controls


@pytest.mark.parametrize("technology,implementation", [
    ("tf_qkd", "TwinFieldAdapter"), ("satellite_doppler", "SatelliteAdapter"),
    ("satellite_isl", "InterSatelliteAdapter"), ("orbital_relay", "OrbitalRelayAdapter"),
    ("swapping", "SwappingAdapter"), ("quantum_memory", "MemoryAdapter"),
    ("purification", "PurificationAdapter")])
def test_registered_reference_protocols_do_not_inherit_terrestrial_models(technology, implementation):
    link, registry = protocol(technology, implementation, source="measurement_file")
    result = link_processing_labels(link, registry)
    assert result.badge == "REFERENCE" and result.data_source == "measurement_file"
    assert implementation in result.physics
    assert "no learned controller reported" in result.controls


def test_protocol_configuration_alone_is_not_activity_or_a_reference_implementation():
    link, registry = protocol(received=False)
    assert link_processing_labels(link, registry).badge == "NO SAMPLE"
    link, registry = protocol(implementation="CustomUnqualifiedAdapter")
    assert link_processing_labels(link, registry).badge == "UNREPORTED"
    assert link_processing_labels(link).badge == "UNREPORTED"


def test_label_projection_preserves_record_and_registry_and_performs_no_io(monkeypatch):
    link, registry = protocol()
    before = deepcopy((link, registry))
    from pathlib import Path
    monkeypatch.setattr(Path, "open", lambda *a, **k: pytest.fail("labels must not read disk"))
    assert link_processing_labels(link, registry).badge == "REFERENCE"
    assert (link, registry) == before


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(scope="module")
def app():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def snapshot(link, technologies=None):
    from quantum_autopilot.console.contract import Link, LiveView, Node
    from quantum_autopilot.console.providers import MockProvider
    snap = MockProvider().snapshot()
    # Manufactured presentation fixture; no communications/hardware evidence.
    topology = replace(snap.topology, nodes=[Node(link.a, "ENDPOINT", "up", .2, .5),
        Node(link.b, "ENDPOINT", "up", .8, .5)],
        links=[Link(link.a, link.b, "0.950", id=link.id, distance_km=link.distance_km)])
    live = LiveView(tick=1, network_time_s=1., source_kind=link.input, source_label="presentation fixture",
                    topology_version=1, links=(link,), technologies=technologies or {})
    return replace(snap, topology=topology, live=live)


def test_net_matrix_and_inspector_present_actual_algorithms_separately_from_data(app):
    from quantum_autopilot.console.widgets.network_views import build_net_model, _matrix_cell
    from quantum_autopilot.console.widgets.inspector import CommandStatus, Inspector
    link = observed(LiveController("keyrate", "baseline.static", "fallback"), source="measured")
    snap = snapshot(link)
    net = build_net_model(snap)
    cell = _matrix_cell(net.nodes[0], net.nodes[1], net.links)
    assert "Data source: measured" in cell.tooltip and "FALLBACK" in cell.tooltip
    panel = Inspector()
    try:
        panel.link_panel.show_link(snap, net, net.links[0], CommandStatus())
        assert panel.link_panel.kv.value("Data origin") == "measured"
        assert panel.link_panel.kv.value("Processing") == "REFERENCE / FALLBACK"
        assert "baseline.static" in panel.link_panel.kv.value("Acting controllers")
    finally:
        panel.close()


def test_links_table_adds_processing_without_changing_existing_metric_columns(app):
    from quantum_autopilot.console.screens.topology import TopologyScreen
    view = TopologyScreen()
    try:
        view._set_links([("LINK", ".95", "2.00%", "1000", "ink", "ink", "UP", "REFERENCE | SIMULATED")])
        rows = view.link_table.records.rows
        assert [cell[0] for cell in rows[0]][:5] == ["LINK", ".95", "2.00%", "1000", "UP"]
        assert view.link_table.model().columnCount() == 6
        assert rows[0][5][0] == "REFERENCE | SIMULATED"
    finally:
        view.shutdown()
        view.close()


def test_actual_link_processor_reports_reference_controls_and_detector_transition():
    from quantum_autopilot.network_engine import LinkBank, ModelRegistry, SimulatedInputSource
    from quantum_autopilot.console.service_provider import _live_link
    from tests.test_network_engine_sources import topology

    network = topology([10])
    source = SimulatedInputSource(501, "quiet", session_periods=None)
    bank = LinkBank(ModelRegistry(environ={}))
    bank.sync(network.links)
    first = last = None
    for tick in range(62):
        step = bank.step(source.frame(tick, network), at_utc="2026-10-12T12:00:00+00:00")
        source.actuate(step.commands)
        published = _live_link(step.links["F10"].to_dict(), {"F10": True})
        if first is None:
            first = published
        last = published
    assert first.input == last.input == "simulated"
    assert {role.controller_id for role in first.controllers if role.role in {"compensation", "sync", "keyrate"}} == {"baseline.alpha_beta", "baseline.static"}
    assert link_processing_labels(first).badge == "REF / WARMUP"
    assert last.detector.label is not None
    assert link_processing_labels(last).badge == "REF + MODEL"
    assert "qkd.detector.boosting" in link_processing_labels(last).controls


@pytest.fixture
def modular_demo(tmp_path, app):
    from pathlib import Path
    from quantum_autopilot.console.service_provider import ServiceProvider
    from quantum_autopilot.console.screens.topology import TopologyScreen
    from quantum_autopilot.network_engine.institutional import PRESET_ID
    from quantum_autopilot.qkd.dashboard_api import demo_service

    service = demo_service(Path(__file__).resolve().parents[1], tmp_path, preset=PRESET_ID, scenario="quiet")
    for _ in range(3):
        service.network_step()
    provider = ServiceProvider(service, state_root=tmp_path / "provider")
    provider.refresh()
    view = TopologyScreen()
    view.resize(1200, 900)
    view.apply(provider.snapshot())
    view.show()
    app.processEvents()
    try:
        yield view, provider
    finally:
        view.shutdown()
        view.close()
        provider.stop()
        app.processEvents()


def test_demo_tf_link_presents_received_reference_processing_and_simulated_origin(modular_demo):
    from quantum_autopilot.console.widgets.inspector import CommandStatus

    view, provider = modular_demo
    snap = provider.snapshot()
    raw = snap.live.link("TECH-RIO-TF-01")
    assert "last_observed_at_s" in raw.provenance["protocol_metrics"]
    projected = next(link for link in view._net.links if link.id == raw.id)
    assert projected.processing_label == "REFERENCE" and projected.data_source == "simulated"
    assert "TwinFieldAdapter" in projected.processing_detail
    view.inspector.link_panel.show_link(snap, view._net, projected, CommandStatus())
    assert view.inspector.link_panel.kv.value("Processing") == "REFERENCE"
    assert "SNS-TF-QKD" in view.inspector.link_panel.kv.value("Physics / protocol")
    row = view._link_rows.index(projected.key)
    assert view.link_table.records.rows[row][5][0] == "REFERENCE | SIMULATED"


def test_full_connection_readback_checks_received_processing_labels_and_rejects_omission(app):
    from quantum_autopilot.console.performance_validation import _connection_expected, _node_widget_content
    from quantum_autopilot.console.widgets.network_views import build_net_model
    from quantum_autopilot.console.widgets.node_window import NodeWindow, _FibreCard

    received = observed(LiveController("keyrate", "baseline.static", "disabled"))
    net = build_net_model(snapshot(received))
    projected = net.links[0]
    window = NodeWindow(received.a)
    card = _FibreCard(window, projected.key)
    window._cards[projected.key] = card
    try:
        card.show_link(received.a, projected, received, clock_s=1.)
        expected = _connection_expected(received.a, projected, received)
        actual = _node_widget_content(window)["cards"][projected.key]
        assert actual == expected
        assert f"processing: {projected.processing_label}; data: {projected.data_source}" in expected["controllers"]
        assert "physics: " + projected.processing_physics in expected["controllers"]
        card.controllers.setText("Controllers · provenance omitted")
        assert _node_widget_content(window)["cards"][projected.key] != expected
    finally:
        window.close()
