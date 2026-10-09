"""Software fixtures only: preservation and rejection, not experimental evidence."""
from copy import deepcopy
from dataclasses import asdict, replace, FrozenInstanceError
from datetime import datetime, timedelta, timezone
import json
import os

import pytest

from quantum_autopilot.console.observations import (
    Observation, measured_link_observations, require_declared_alignment,
)


def observation(**overrides):
    values = dict(quantity="qber", value=.021234567891, unit="fraction", source_id="test-source",
        campaign_id="test-campaign", entity_id="ab", channel="C21", device_id="test-device",
        measurement_id="test-1", acquired_at="2026-09-12T10:00:00+00:00",
        received_at_utc="2026-09-12T10:00:02+00:00", provenance="measured")
    values.update(overrides)
    return Observation(**values)


@pytest.mark.parametrize("overrides", [
    {"value": True}, {"value": "2.1%"}, {"value": float("nan")}, {"value": float("inf")},
    {"value": 2.1}, {"value": -0.1}, {"unit": "%"}, {"quantity": "unknown"},
    {"acquired_at": "2026-09-12T10:00:00"}, {"received_at_utc": None},
    {"clock_domain": "simulation"}, {"clock_domain": "unknown"},
    {"integration_start": "2026-09-12T09:59:59Z"},
    {"integration_start": "2026-09-12T09:59:59Z", "integration_end": "2026-09-12T10:00:01Z"},
    {"standard_uncertainty": -1}, {"standard_uncertainty": True}, {"fidelity_reference": "phi_plus"},
    {"schema": "bad"}, {"channel": ""},
])
def test_observation_rejects_invalid_values_and_metadata(overrides):
    with pytest.raises(ValueError):
        observation(**overrides)


def test_canonical_values_are_not_rounded_or_mutated_by_display():
    item = observation()
    assert item.formatted(".2f", unit="%") == "2.12"
    assert item.value == .021234567891 and item.unit == "fraction"
    assert json.loads(json.dumps(asdict(item)))["value"] == item.value
    with pytest.raises(FrozenInstanceError):
        item.value = .1
    with pytest.raises(ValueError):
        item.formatted(unit="dB")
    assert observation(quantity="skr_bps", unit="bit/s", value=1250).formatted(".2f", unit="kb/s") == "1.25"


def test_clock_quality_is_declared_not_verified():
    item = observation()
    assert item.receipt_delay_s == 2
    assert item.time_quality == "clock_not_supplied"
    assert replace(item, clock_id="test-clock").time_quality == "clock_declared_unverified"
    assert replace(item, received_at_utc="2026-09-12T09:59:59Z").time_quality == "source_clock_ahead"
    sim = replace(item, provenance="simulated", clock_domain="simulation", received_at_utc=None)
    assert sim.receipt_delay_s is None and sim.time_quality == "simulation_clock"


@pytest.mark.parametrize("change", [
    {"channel": "C22"}, {"campaign_id": "other"}, {"device_id": "other"},
    {"entity_id": "other"}, {"source_id": "other"}, {"clock_id": "other"},
    {"clock_id": None}, {"provenance": "simulated"},
    {"acquired_at": "2026-09-12T10:00:02Z"},
    {"integration_start": "2026-09-12T09:59:59Z", "integration_end": "2026-09-12T10:00:00Z"},
])
def test_alignment_refuses_incompatible_context(change):
    first = observation(clock_id="declared-clock")
    with pytest.raises(ValueError):
        require_declared_alignment([first, replace(first, **change)], max_skew_s=1)


def test_alignment_is_only_a_conservative_precondition():
    first = observation(clock_id="declared-clock")
    second = replace(first, quantity="ber", value=1e-6)
    require_declared_alignment([first, second], max_skew_s=0)
    with pytest.raises(ValueError):
        require_declared_alignment([observation()], max_skew_s=0)


# UI/provider tests use the installed optional Qt dependency.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(scope="module")
def app():
    QtWidgets = pytest.importorskip("PySide6.QtWidgets")
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def data_frame():
    from tests.test_console_input_sources import frame
    data = frame()
    stamp = (datetime.now(timezone.utc) - timedelta(seconds=5)).isoformat()
    data["timestamp_utc"] = data["links"][0]["timestamp_utc"] = stamp
    return data


def shifted(value, seconds):
    return (datetime.fromisoformat(value) + timedelta(seconds=seconds)).isoformat()


def read_fixture(tmp_path, data):
    from quantum_autopilot.console.input_sources import MeasuredFileProvider
    path = tmp_path / "input.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    provider = MeasuredFileProvider(path, tmp_path / "exports")
    provider.refresh()
    return provider, path


def test_measured_precision_absence_metadata_and_export(app, tmp_path):
    data = data_frame()
    row = data["links"][0]
    row.update(qber=.021234567891, fidelity=.931234567891, fidelity_method="state_tomography",
        standard_uncertainties={"qber": .000123}, clock_id="declared-test-clock",
        integration_start_utc=shifted(row["timestamp_utc"], -1), integration_end_utc=row["timestamp_utc"])
    provider, path = read_fixture(tmp_path, data)
    snap = provider.snapshot()
    readings = {o.quantity: o for o in snap.observations}
    assert readings["qber"].value == row["qber"]
    assert readings["qber"].standard_uncertainty == .000123
    assert readings["fidelity"].fidelity_reference is None
    assert snap.topology.links[0].qber == "2.12%"
    assert any(m.label == "FIDELITY" for m in snap.telemetry.headline)
    assert not any(m.label == "BELL FIDELITY" for m in snap.telemetry.headline)
    provider.dispatch("export_record")
    exported = json.loads(next((tmp_path / "exports").glob("*.json")).read_text())
    stored = next(o for o in exported["snapshot"]["observations"] if o["quantity"] == "qber")
    assert stored["value"] == row["qber"] and stored["unit"] == "fraction"
    path.write_text('{"invalid":true}')
    provider.refresh()
    assert provider.snapshot().observations == ()


@pytest.mark.parametrize("metadata", [
    {"standard_uncertainties": {"missing": .1}}, {"standard_uncertainties": {"qber": None}},
    {"standard_uncertainties": {"qber": -.1}}, {"clock_id": None},
    {"fidelity_reference": "bell_phi_plus"}, {"integration_end_utc": "2026-09-12T10:00:00Z"},
])
def test_optional_wire_metadata_is_validated(app, metadata):
    from quantum_autopilot.console.input_sources import validate_frame
    data = data_frame()
    data["links"][0].update(metadata)
    with pytest.raises(ValueError):
        validate_frame(data)


def test_first_receipt_survives_poll_selection_and_new_envelope(app, tmp_path):
    data = data_frame()
    provider, path = read_fixture(tmp_path, data)
    original = provider.snapshot().observations
    provider.refresh()
    provider.dispatch("select_link", "ab")
    provider.dispatch("select_node", "a")
    assert provider.snapshot().observations == original
    data["timestamp_utc"] = shifted(data["timestamp_utc"], 1)
    path.write_text(json.dumps(data))
    provider.refresh()
    assert provider.snapshot().observations == original


@pytest.mark.parametrize("fault", ["regression", "same_time_conflict", "channel", "device", "clock"])
def test_new_envelope_cannot_hide_link_regression_or_identity_change(app, tmp_path, fault):
    data = data_frame()
    provider, path = read_fixture(tmp_path, data)
    history = dict(provider._link_history)
    data["timestamp_utc"] = shifted(data["timestamp_utc"], 1)
    row = data["links"][0]
    if fault == "regression":
        row["timestamp_utc"] = shifted(row["timestamp_utc"], -1)
    elif fault == "same_time_conflict":
        row["qber"] = .03
    else:
        row[{"channel": "channel", "device": "device_id", "clock": "clock_id"}[fault]] = "changed"
    path.write_text(json.dumps(data))
    provider.refresh()
    assert provider.frame is None and not provider.snapshot().observations
    assert provider._link_history == history


def test_advance_accepts_new_sample_and_history_updates_atomically(app, tmp_path):
    data = data_frame()
    second = deepcopy(data["links"][0])
    second.update(id="ab-2", channel="C22")
    data["links"].append(second)
    provider, path = read_fixture(tmp_path, data)
    original_history = dict(provider._link_history)
    data["timestamp_utc"] = shifted(data["timestamp_utc"], 2)
    data["links"][0]["timestamp_utc"] = data["timestamp_utc"]
    data["links"][1]["qber"] = .03  # Same-time conflict after a valid first row.
    path.write_text(json.dumps(data))
    provider.refresh()
    assert provider._link_history == original_history
    data["links"][1]["qber"] = .02
    data["links"][0]["timestamp_utc"] = shifted(data["timestamp_utc"], -1)
    path.write_text(json.dumps(data))
    provider.refresh()
    assert provider.frame is not None
    assert {o.channel for o in provider.snapshot().observations} == {"C21", "C22"}


def test_synthetic_uses_same_contract_without_wall_clock_freshness(app):
    from quantum_autopilot.console.synthetic import artificial_snapshot
    snap = artificial_snapshot("load", seed=42)
    assert len(snap.observations) == 3 + 3 * 256
    assert all(o.provenance == "simulated" and o.clock_domain == "simulation" for o in snap.observations)
    assert all(o.receipt_delay_s is None for o in snap.observations)
    assert asdict(snap) == asdict(artificial_snapshot("load", seed=42))
    assert not artificial_snapshot("load", seed=42, dropout=True).observations
    assert not artificial_snapshot("faults", tick=5).observations
    first = snap.topology.links[0]
    qber = next(o for o in snap.observations if o.entity_id == first.id and o.quantity == "qber")
    assert first.qber == qber.formatted(".2f", unit="%") + "%"


def test_enormous_numeric_payload_is_refused_without_crashing(app, tmp_path):
    data = data_frame()
    data["links"][0]["ber"] = 10 ** 400
    provider, _ = read_fixture(tmp_path, data)
    assert provider.frame is None and provider.snapshot().observations == ()


def test_history_is_bounded_and_failed_frame_does_not_commit(app, tmp_path):
    data = data_frame()
    provider, path = read_fixture(tmp_path, data)
    previous = provider._link_history["ab"]
    provider._link_history = {f"previous-{i}": previous for i in range(4096)}
    provider.refresh()
    assert provider.frame is None and len(provider._link_history) == 4096
    assert "4096" in provider.snapshot().topology.caveat


def test_hot_switch_discards_observations_from_previous_source(app, tmp_path):
    from quantum_autopilot.console.source_switch import SwitchableProvider
    from quantum_autopilot.console.synthetic import SyntheticProvider
    from quantum_autopilot.console.input_sources import MeasuredFileProvider
    from pathlib import Path
    plug = SwitchableProvider(SyntheticProvider(Path(__file__).resolve().parents[1], tmp_path / "sim"))
    plug.start()
    try:
        previous = plug.snapshot()
        callback = plug._connections[0][1]
        assert previous.observations
        plug.switch(lambda: MeasuredFileProvider(tmp_path / "absent.json", tmp_path / "measured"))
        callback(previous)
        assert plug.snapshot().observations == ()
    finally:
        plug.stop()


def test_long_metadata_wraps_inside_scrollable_conditions(app, tmp_path):
    from quantum_autopilot.console.screens.telemetry import TelemetryScreen
    data = data_frame()
    data["links"][0]["clock_id"] = "clock-" + "a" * 154
    provider, _ = read_fixture(tmp_path, data)
    screen = TelemetryScreen()
    screen.apply(provider.snapshot())
    screen.resize(1280, 800)
    screen.show()
    try:
        for _ in range(4):
            app.processEvents()
        values = [screen.condition_lay.itemAt(i).widget().layout().itemAt(2).widget()
                  for i in range(screen.condition_lay.count())]
        assert all(v.wordWrap() and v.toolTip() for v in values)
        assert screen.condition_scroll.width() < screen.width()
        assert screen.condition_scroll.widgetResizable()
    finally:
        screen.close()


def test_repeated_status_updates_detach_old_labels_immediately(app):
    from PySide6.QtWidgets import QLabel
    from quantum_autopilot.console.screens.base import ScreenHeader
    bar = ScreenHeader("Fidelity & decoherence")
    bar.set_status("old source")
    old = bar.status_lay.itemAt(0).widget()
    bar.set_status("current source")
    assert old.parent() is None
    assert len(bar.status_host.findChildren(QLabel)) == 1
    previous = bar.status_lay.itemAt(0).widget()
    bar.set_status_widgets([QLabel("current only")])
    assert previous.parent() is None
    assert len(bar.status_host.findChildren(QLabel)) == 1
    bar.close()
