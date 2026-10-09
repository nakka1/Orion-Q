"""UI readback validation rejects divergence from published protocol records.

The numeric expectations come from upstream LiveView records, independently of
the production helper that formats the table. No received record is mutated.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import os
import re

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtCore import QCoreApplication, QEvent  # noqa: E402
from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402

from quantum_autopilot.console import theme  # noqa: E402
from quantum_autopilot.console.performance_validation import _ground_widget_checks  # noqa: E402
from quantum_autopilot.console.widgets.inspector import CommandStatus  # noqa: E402
from quantum_autopilot.console.widgets.network_views import build_net_model  # noqa: E402
from quantum_autopilot.console.widgets.node_window import NodeWindow  # noqa: E402
from quantum_autopilot.console.widgets.wave_chart import WaveSeries  # noqa: E402
from tests.test_console_protocol_observations import NODE_ID, SESSION_IDS, snapshot  # noqa: E402

pytestmark = pytest.mark.unit


@pytest.fixture
def gui():
    app = QApplication.instance() or QApplication([])
    previous = theme.current_theme()
    theme.set_theme("dark"); theme.install(app)
    host = QWidget(); host.resize(1100, 800); host.show()
    yield app, host
    host.close(); host.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    theme.set_theme(previous); theme.install(app)


def panel(gui, snap):
    app, host = gui
    net = build_net_model(snap)
    window = NodeWindow(NODE_ID, host)
    assert window.refresh(snap, net, CommandStatus())
    window.show(); app.processEvents()
    assert window.presentation_status()["ready"]
    return window, net


def verify(window, snap, net):
    checks, body = _ground_widget_checks(window, snap, net)
    assert checks and all(isinstance(value, bool) for value in checks.values())
    return checks, body


def assert_valid(window, snap, net):
    checks, body = verify(window, snap, net)
    assert all(checks.values()), {key: value for key, value in checks.items() if not value}
    return body


def capture_number(pattern, text):
    match = re.search(pattern, text)
    assert match is not None, text
    return float(match.group(1))


def assert_numeric_protocol_readback(window, live):
    """Parse displayed quantities and compare them to received raw records."""
    shown = window.protocols.texts()
    for ident, session in live.technologies["sessions"].items():
        prefix = ident + " · "
        reception = shown[prefix + "Recepção clássica"]
        assert capture_number(r"([0-9]+) observações", reception) == session["counters"]["observations"]
        assert capture_number(r"última ([0-9.]+) s", reception) == pytest.approx(
            session["metrics"]["last_observed_at_s"], abs=5e-7)
        assert shown[prefix + "Estado"].startswith(session["status"])
        assert session["reason"] in shown[prefix + "Estado"]
        published_origin = session["metrics"].get("observation_origin", session["source"]["origin"])
        assert shown[prefix + "Origem"].split(" · ", 1)[0] == published_origin
        technologies = session["config"]["technologies"]
        if "tf_qkd" in technologies:
            qber = capture_number(r"QBER ([0-9.]+) %", shown[prefix + "TF-QKD"])
            assert qber == pytest.approx(session["metrics"]["qber"] * 100, abs=.0051)
            phase = capture_number(r"fase ([0-9.]+) rad", shown[prefix + "TF-QKD"])
            assert phase == pytest.approx(session["metrics"]["phase_reference_residual_rad"], abs=5e-5)
        if "entanglement_swapping" in technologies:
            pairs = shown[prefix + "Pares e operações"]
            for label, key in (("tentativas", "generation_attempts"), ("pares gerados", "generated_pairs"),
                               ("entregues", "delivered_pairs")):
                assert capture_number(label + r" ([0-9]+)", pairs) == session["counters"][key]
        if "satellite_doppler" in technologies:
            coincidences = capture_number(r"coincidências ([0-9]+)", shown[prefix + "Satélite/OGS"])
            assert coincidences == session["metrics"]["coincidences"]


@pytest.mark.parametrize("kind", ("legacy", "protocol", "mixed"))
def test_ground_checks_accept_each_published_connection_contract(gui, kind):
    snap = snapshot(modes=() if kind == "legacy" else ("tf", "memory", "satellite"),
                    legacy=kind != "protocol")
    raw = deepcopy(snap.live)
    window, net = panel(gui, snap)
    body = assert_valid(window, snap, net)
    assert_numeric_protocol_readback(window, snap.live)
    expected_legacy = [link.id for link in snap.live.links if not link.provenance.get("technology")]
    assert window.chart.keys() == expected_legacy
    assert window.chart.isVisible() == bool(expected_legacy)
    for series in window.chart.series:
        received = snap.live.link(series.key)
        assert series.samples == received.wave and series.rate_hz == received.wave_rate_hz
    assert body["protocols"] == window.protocols.texts()
    assert snap.live == raw


@pytest.mark.parametrize("name,old,new", (
    ("TF-QKD", "QBER 3.12 %", "QBER 99.00 %"),
    ("Recepção clássica", "7 observações", "99 observações"),
    ("Recepção clássica", "19.750000 s", "29.750000 s"),
    ("Origem", "simulated", "measured"),
    ("Estado", "operational", "unavailable"),
    ("Chaves · sessão", "bits certificados 0", "bits certificados 999"),
))
def test_ground_checks_reject_corrupted_protocol_fields(gui, name, old, new):
    snap = snapshot(modes=("tf",), legacy=True)
    raw = deepcopy(snap.live)
    window, net = panel(gui, snap)
    assert_valid(window, snap, net)
    key = f"{SESSION_IDS['tf']} · {name}"
    shown = window.protocols.texts()
    assert old in shown[key]
    shown[key] = shown[key].replace(old, new, 1)
    window.protocols.set_rows(list(shown.items()))
    checks, _body = verify(window, snap, net)
    assert not all(checks.values()), "validator accepted a corrupted protocol field"
    assert any(not value for key, value in checks.items() if "protocol" in key)
    assert snap.live == raw


def test_ground_checks_reject_phantom_protocol_visibility_samples(gui):
    snap = snapshot(modes=("tf",), legacy=True)
    raw = deepcopy(snap.live)
    window, net = panel(gui, snap)
    assert_valid(window, snap, net)
    technological = next(link for link in snap.live.links if link.provenance.get("technology"))
    assert technological.wave == ()
    phantom = WaveSeries(technological.id, f"{technological.b} · {technological.id}", (.8, .9), 20.)
    window.chart.set_series([*window.chart.series, phantom], snap.live.network_time_s)
    checks, _body = verify(window, snap, net)
    assert checks["chart_link_keys"] is False and checks["chart_received_values"] is False
    assert snap.live == raw


@pytest.mark.parametrize("change", ("sample", "rate", "timestamp"))
def test_ground_checks_reject_changed_legacy_samples_rate_or_clock(gui, change):
    snap = snapshot(modes=("tf",), legacy=True)
    raw = deepcopy(snap.live)
    window, net = panel(gui, snap)
    assert_valid(window, snap, net)
    series = window.chart.series[0]
    delivered_at = snap.live.network_time_s
    if change == "sample":
        series = replace(series, samples=(.10, *series.samples[1:]))
    elif change == "rate":
        series = replace(series, rate_hz=10.)
    else:
        delivered_at += 1
    window.chart.set_series([series], delivered_at)
    checks, _body = verify(window, snap, net)
    assert checks["chart_network_time" if change == "timestamp" else "chart_received_values"] is False
    assert snap.live == raw


def test_ground_checks_reject_wrong_legacy_legend_style(gui):
    snap = snapshot(legacy=True)
    window, net = panel(gui, snap)
    assert_valid(window, snap, net)
    window._legend["LEGACY-FIBRE"].swatch.index += 1
    checks, _body = verify(window, snap, net)
    assert not all(checks.values()), "validator accepted a legend whose style differs from its trace"


def test_ground_checks_reject_protocol_swatch_presented_as_a_wave(gui):
    snap = snapshot(legacy=True)
    window, net = panel(gui, snap)
    assert_valid(window, snap, net)
    technological = next(link for link in snap.live.links if link.provenance.get("technology"))
    window._legend[technological.id].swatch.show()
    checks, _body = verify(window, snap, net)
    assert not all(checks.values()), "validator accepted a protocol wave swatch without a received wave"


def test_ground_checks_accept_no_input_without_acquired_protocol_quantities(gui):
    snap = snapshot(received=False, session_status="configured", legacy=True)
    raw = deepcopy(snap.live)
    window, net = panel(gui, snap)
    assert_valid(window, snap, net)
    shown = window.protocols.texts()
    for ident in SESSION_IDS.values():
        assert "Aguardando" in shown[ident + " · Recepção clássica"]
        assert "no input" in shown[ident + " · Enlaces locais"]
    assert not any(key.endswith((" · TF-QKD", " · Pares e operações", " · Satélite/OGS")) for key in shown)
    assert all(not link.wave for link in snap.live.links if link.provenance.get("technology"))
    assert snap.live == raw
