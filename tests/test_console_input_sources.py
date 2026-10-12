"""Artificial fixtures exercise measured-input validation; no experimental evidence."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import threading
import time

import pytest

pytest.importorskip("PySide6.QtWidgets")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication  # noqa: E402
from quantum_autopilot.console.input_sources import MeasuredFileProvider, validate_frame  # noqa: E402
from quantum_autopilot.console.shell import ConsoleWindow  # noqa: E402
from quantum_autopilot.console.source_switch import SwitchableProvider  # noqa: E402
from quantum_autopilot.console.synthetic import SyntheticProvider  # noqa: E402
from quantum_autopilot.console.widgets import Btn  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def frame():
    stamp = datetime.now(timezone.utc).isoformat()
    return {"schema": "orion.telemetry.v1", "source_id": "test-fixture-not-a-real-device",
        "campaign_id": "unit-test-only", "timestamp_utc": stamp, "evidence_kind": "measured",
        "nodes": [{"id": "a", "x": .2, "y": .5}, {"id": "b", "x": .8, "y": .5}],
        "links": [{"id": "ab", "a": "a", "b": "b", "channel": "C21", "device_id": "fixture-device",
            "measurement_id": "fixture-1", "timestamp_utc": stamp, "evidence_kind": "measured",
            "ber": 1e-6, "osnr_db": 24.0, "power_dbm": -8.5, "qber": .02, "skr_bps": 1250.0}]}


@pytest.mark.parametrize("fault", ["simulated", "simulated_row", "derived_fidelity", "no_method", "nan", "range",
    "stale", "future", "no_timezone", "dangling", "duplicate", "unknown", "oversize", "missing_device",
    "partial_geo", "bad_geo"])
def test_reject_invalid_measured_payload(fault):
    data = frame()
    row = data["links"][0]
    if fault == "simulated":
        data["evidence_kind"] = "simulated"
    elif fault == "simulated_row":
        row["evidence_kind"] = "simulated"
    elif fault in {"derived_fidelity", "no_method"}:
        row["fidelity"] = .93
        if fault == "derived_fidelity":
            row["fidelity_method"] = "simulated_from_ber"
    elif fault == "nan":
        row["ber"] = float("nan")
    elif fault == "range":
        row["qber"] = 2.0
    elif fault in {"stale", "future"}:
        data["timestamp_utc"] = (datetime.now(timezone.utc) + timedelta(seconds=-60 if fault == "stale" else 60)).isoformat()
    elif fault == "no_timezone":
        data["timestamp_utc"] = "2026-09-09T12:00:00"
    elif fault == "dangling":
        row["a"] = "missing"
    elif fault == "duplicate":
        data["links"].append(deepcopy(row))
    elif fault == "unknown":
        row["execute"] = "PURIFY"
    elif fault == "oversize":
        data["nodes"] *= 17
    elif fault == "missing_device":
        del row["device_id"]
    elif fault == "partial_geo":
        data["nodes"][0]["latitude"] = 41.9
    elif fault == "bad_geo":
        data["nodes"][0].update(latitude=91, longitude=-87.6)
    with pytest.raises(ValueError):
        validate_frame(data)


def test_physical_input_preserves_absence_and_refuses_commands(app, tmp_path):
    path = tmp_path / "input.json"
    path.write_text(json.dumps(frame()))
    provider = MeasuredFileProvider(path, tmp_path / "exports")
    provider.refresh()
    snap = provider.snapshot()
    assert snap.campaign.startswith("measured:")
    assert snap.topology.links[0].fidelity == "—"
    assert all(n.health == "unknown" for n in snap.topology.nodes)
    assert not snap.node.registers and not snap.keys.pool_blocks
    assert not snap.telemetry.fidelity.series
    assert all(m.provenance == "measured" for m in snap.telemetry.headline)
    for command in ("isolate", "run_scenario", "recalibrate", "replay_model", "set_decision_source"):
        with pytest.raises(ValueError, match="read-only"):
            provider.dispatch(command)
    path.write_text('{"incomplete":')
    provider.refresh()
    assert not provider.snapshot().topology.links
    path.write_text(json.dumps(frame()))
    provider.refresh()
    assert len(provider.snapshot().topology.links) == 1


def test_optional_geographic_pair_is_preserved(app, tmp_path):
    data = frame()
    data["nodes"][0].update(latitude=41.90, longitude=-87.60)
    data["nodes"][1].update(latitude=41.91, longitude=-87.58)
    path = tmp_path / "geo-input.json"
    path.write_text(json.dumps(data))
    provider = MeasuredFileProvider(path, tmp_path / "exports")
    provider.refresh()
    nodes = provider.snapshot().topology.nodes
    assert [(n.latitude, n.longitude) for n in nodes] == [(41.90, -87.60), (41.91, -87.58)]


def test_same_window_hot_swap_clears_hidden_screens_and_ignores_old_signals(app, tmp_path):
    source = SyntheticProvider(ROOT, tmp_path / "demo", profile="load")
    window = ConsoleWindow(source, skip_auth=True, state_root=tmp_path)
    window.provider.start()
    window.show_screen("keys")
    screen_ids = {key: id(screen) for key, screen in window.screens.items()}
    old_slot = window.provider._connections[0][1]
    old_snapshot = source.snapshot()
    path = tmp_path / "input.json"
    path.write_text(json.dumps(frame()))
    try:
        assert window.switch_input(lambda: MeasuredFileProvider(path, tmp_path / "read-only"))
        assert not source.timer.isActive()
        assert window._current == "keys"
        assert screen_ids == {key: id(screen) for key, screen in window.screens.items()}
        assert window._snap.campaign.startswith("measured:")
        assert window.screens["topology"].link_table.model().rowCount() == 0  # hidden old demo was cleared
        old_slot(old_snapshot)  # A queued callback from the previous generation must be harmless.
        source.snapshot_changed.emit(old_snapshot)
        app.processEvents()
        assert window._snap.campaign.startswith("measured:")
        window.show_screen("topology")
        assert window.screens["topology"].link_table.model().rowCount() == 1
        assert "MEASURED" in window.lab_button.text()
        buttons = window.screens["topology"].findChildren(Btn)
        assert buttons and not any(button.isEnabled() for button in buttons)
        assert window.switch_input(lambda: SyntheticProvider(ROOT, tmp_path / "demo-2"))
        assert window._snap.campaign.startswith("synthetic-")
        assert any(button.isEnabled() for button in buttons)
    finally:
        window.close()


def test_busy_and_failed_factory_do_not_replace_input(app, tmp_path):
    source = SyntheticProvider(ROOT, tmp_path)
    plug = SwitchableProvider(source)
    plug.busy = True
    with pytest.raises(RuntimeError):
        plug.switch(lambda: SyntheticProvider(ROOT, tmp_path))
    assert plug.active is source
    plug.busy = False
    def broken():
        raise ValueError("invalid source configuration")
    with pytest.raises(ValueError):
        plug.switch(broken)
    assert plug.active is source
    plug.stop()


@pytest.mark.parametrize("return_to_login", [False, True])
def test_async_source_preserves_destination_or_explicit_return_to_login(app, tmp_path, return_to_login):
    """UI fixtures test navigation; the native campaign separately verifies OS login."""
    from quantum_autopilot.console.prepared_source import DeferredProvider

    source = SyntheticProvider(ROOT, tmp_path / "initial", profile="load")
    window = ConsoleWindow(source, skip_auth=True, state_root=tmp_path)
    gate = threading.Event()
    path = tmp_path / "input.json"
    path.write_text(json.dumps(frame()))

    def prepare():
        if not gate.wait(15):
            raise TimeoutError("bounded fixture preparation")
        return path

    candidate = DeferredProvider(prepare, lambda value: MeasuredFileProvider(value, tmp_path / "measured"))
    try:
        window.provider.start()
        window.show_screen("keys")
        assert window.switch_input(lambda: candidate)
        assert window.current_screen == "splash" and not candidate.ready
        if return_to_login:
            window.show_screen("boot")
            assert not window._loading_source
        gate.set()
        deadline = time.monotonic() + 15
        expected = "boot" if return_to_login else "keys"
        while (not candidate.ready or window.current_screen != expected) and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(.005)
        assert candidate.ready and window.current_screen == expected
        assert not window._loading_source and window._source_return_screen is None
        assert window.snapshot.campaign.startswith("measured:")
        if return_to_login:
            # A completed background preparation must not strand later entry.
            assert window.open_workspace(window.active_source_key())
            assert window.current_screen == "topology"
    finally:
        gate.set()
        window.close()


def test_expiration_reorder_and_identity_change_fail_closed(app, tmp_path):
    path = tmp_path / "input.json"
    data = frame()
    path.write_text(json.dumps(data))
    provider = MeasuredFileProvider(path, tmp_path)
    provider.refresh()
    assert provider.frame is not None
    older = deepcopy(data)
    older["timestamp_utc"] = (datetime.fromisoformat(data["timestamp_utc"]) - timedelta(seconds=1)).isoformat()
    older["links"][0]["timestamp_utc"] = older["timestamp_utc"]
    path.write_text(json.dumps(older))
    provider.refresh()
    assert provider.frame is None and not provider.snapshot().topology.links
    changed = deepcopy(data)
    changed["source_id"] = "different-source"
    path.write_text(json.dumps(changed))
    provider.refresh()
    assert provider.frame is None
    path.write_text(json.dumps(data))
    provider.refresh()
    assert provider.frame is not None
    provider._last_time = datetime.now(timezone.utc) + timedelta(seconds=60)
    provider.refresh()
    assert provider.frame is None


def test_duplicate_keys_conflicts_and_valid_idempotent_reformat(app, tmp_path):
    path = tmp_path / "input.json"
    data = frame()
    provider = MeasuredFileProvider(path, tmp_path)
    path.write_text(json.dumps(data))
    provider.refresh()
    digest = provider.digest
    path.write_text(json.dumps(data, indent=4))
    provider.refresh()
    assert provider.frame is not None and provider.digest == digest
    data["links"][0]["ber"] = 2e-6
    path.write_text(json.dumps(data))
    provider.refresh()
    assert provider.frame is None
    path.write_text('{"schema":"orion.telemetry.v1","schema":"different"}')
    provider.refresh()
    assert "duplicate JSON field" in provider.snapshot().topology.caveat


def test_missing_physical_input_retains_read_only_labels(app, tmp_path):
    provider = MeasuredFileProvider(tmp_path / "missing.json", tmp_path)
    window = ConsoleWindow(provider, skip_auth=True)
    try:
        window.provider.start()
        window.show_screen("topology")
        assert window._snap.input_kind == "measured"
        assert "SIMULATED" not in window.screens["topology"].ring_label.text()
        assert "MEASURED" in window.screens["topology"].ring_label.text()
        window.show_screen("boot")
        assert window.boot.boundary_values() == [("Simulation", "UNAVAILABLE"), ("Shadow", "UNAVAILABLE"),
            ("Physical", "LOCKED"), ("Hardware access", "DISABLED")]
    finally:
        window.close()


def test_parallel_channels_select_exact_measured_link(app, tmp_path):
    data = frame()
    second = deepcopy(data["links"][0])
    second.update(id="ab-2", channel="C22", measurement_id="fixture-2", qber=.03)
    data["links"].append(second)
    path = tmp_path / "input.json"
    path.write_text(json.dumps(data))
    window = ConsoleWindow(MeasuredFileProvider(path, tmp_path), skip_auth=True)
    try:
        window.provider.start()
        window.show_screen("topology")
        screen = window.screens["topology"]
        screen._link_selected(screen.link_table.model().index(1, 0))
        assert window._current == "telemetry"
        assert window.provider.active.selected_link == "ab-2"
        assert any(v.key == "CHANNEL" and v.value == "C22" for v in window._snap.telemetry.conditions)
    finally:
        window.close()
