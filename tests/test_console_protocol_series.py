"""Received scalar timelines remain separate from the legacy polarisation wave."""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtWidgets import QApplication  # noqa: E402

from quantum_autopilot.console.widgets.protocol_series import ProtocolSeriesPanel  # noqa: E402
from quantum_autopilot.console.widgets.wave_chart import WaveChart, WaveSeries  # noqa: E402
from quantum_autopilot.network_engine.protocol_history import ProtocolHistory  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def state():
    payload = {"clock_s": 10., "origin": "simulated", "source_id": "published-receipts", "sessions": {"GOOGLE-TF": {
        "config": {"technologies": ["tf_qkd"], "path": ["US-GOOG-QA", "TF-CENTER", "PEER"]},
        "status": "operational", "metrics": {"last_observed_at_s": 9.77, "qber": .025,
        "phase_reference_residual_rad": -.05, "conditional_key_estimate_bps": 2500., "certified_secret_bits": 0}}}}
    payload["protocol_series"] = ProtocolHistory().update(payload)
    return payload


def test_selected_observable_uses_its_real_times_units_bounds_and_origin(app):
    panel = ProtocolSeriesPanel()
    panel.set_state(state(), node_id="US-GOOG-QA")
    panel.show()
    app.processEvents()
    assert panel.isVisible() and panel.selector.count() == 7
    assert "simulated" in panel.metadata.text() and "published-receipts" in panel.metadata.text()
    assert "protocol_clock_s" in panel.metadata.text()
    assert panel.chart.visible_samples(panel.chart.keys()[0]) == [(9.77, .025)]
    assert panel.chart.series[0].rate_hz == 0. and panel.chart.series[0].times_s == (9.77,)
    key = "GOOGLE-TF:tf_qkd:conditional_key_estimate_bps"
    panel.selector.setCurrentIndex(panel.selector.findData(key))
    assert "bit/s" in panel.metadata.text() and panel.chart.value_range()[1] > 2500
    key = "GOOGLE-TF:tf_qkd:phase_reference_residual_rad"
    panel.selector.setCurrentIndex(panel.selector.findData(key))
    assert "rad" in panel.metadata.text() and panel.chart.value_range()[0] < -.05
    assert not panel.grab().isNull()
    panel.close()


def test_timestamped_chart_preserves_gap_and_single_received_point(app):
    chart = WaveChart(animate=False, value_bounds=(0., None), window_s=20.)
    chart.resize(400, 170)
    chart.set_series([WaveSeries("OBS", "Rate", (200., None, 300.), 0., times_s=(1.1, 4.7, 8.9))], 10.)
    assert chart.visible_samples("OBS") == [(1.1, 200.), (4.7, None), (8.9, 300.)]
    assert chart.value_range()[1] > 300.
    assert chart.axis_label(4321.) == "4.32e+03"
    chart.set_value_bounds((0., 1.))
    assert chart.axis_label(.0075) == "0.0075"
    chart.set_value_bounds((0., None))
    chart.show()
    app.processEvents()
    assert not chart.grab().isNull()
    chart.set_series([WaveSeries("OBS", "Rate", (200.,), 0., times_s=(8.3,))], 10.)
    assert chart.visible_samples("OBS") == [(8.3, 200.)]
    assert not chart.grab().isNull()
    chart.close()


def test_protocol_duration_keeps_time_labels_readable(app):
    chart = WaveChart(window_s=120., animate=False)
    assert chart.time_tick_step(400.) == 20.
    assert chart.time_tick_step(180.) == 50.
    chart.window_s = 12.
    assert chart.time_tick_step(400.) == 2.
    chart.close()


def test_selection_persists_on_new_publication_and_foreign_node_hides_panel(app):
    panel = ProtocolSeriesPanel()
    payload = state()
    panel.set_state(payload, node_id="US-GOOG-QA")
    key = "GOOGLE-TF:tf_qkd:phase_reference_residual_rad"
    panel.selector.setCurrentIndex(panel.selector.findData(key))
    panel.set_state(payload, node_id="US-GOOG-QA")
    assert panel.selector.currentData() == key
    panel.set_state(payload, node_id="CIMATEC")
    assert panel.isHidden() and panel.chart.keys() == []


def test_unchanged_satellite_card_keeps_latest_drill_down_record(app):
    from dataclasses import replace

    from PySide6.QtWidgets import QWidget

    from quantum_autopilot.console.contract import LiveLink
    from quantum_autopilot.console.widgets.network_views import NetLink
    from quantum_autopilot.console.widgets.node_window import NodeWindow, _FibreCard

    host = QWidget()
    window = NodeWindow("NODE", host)
    link = NetLink("SAT", "SAT", "NODE", "SATELLITE", "space", "up", "violet",
                   "—", "—", "—", 300., True, "Satellite link")
    metrics = {"qber": .02, "conditional_key_estimate_bps": 50., "certified_secret_bits": 0,
               "coincidences": 10, "housekeeping": {"sequence": 1}}
    live = LiveLink("SAT", "NODE", "SATELLITE", 300., "up",
                    provenance={"technology": {"technologies": ["satellite_doppler"], "session_id": "SAT-S"},
                                "protocol_metrics": metrics})
    card = _FibreCard(window, "SAT")
    try:
        card.show_link("NODE", link, live, clock_s=1.)
        shown = card._shown_signature
        fresh = {**metrics, "housekeeping": {"sequence": 2}}
        card.show_link("NODE", link, replace(live, provenance={**live.provenance, "protocol_metrics": fresh}), clock_s=2.)
        assert card._shown_signature == shown
        assert card._satellite_metrics is fresh
        assert card._satellite_clock == 2.
    finally:
        host.close()
