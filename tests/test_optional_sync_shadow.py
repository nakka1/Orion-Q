"""Typed shadow feature bridge: identity, causality, optionality, no actuation."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import hashlib
import json
import zipfile

import pytest

from quantum_autopilot.control_plane.contracts import NetworkStateSnapshot
from quantum_autopilot.control_plane.optional_sync import (
    MODEL_ID, SyncSample, SyncShadowWindow, TELEMETRY_KEY,
    evaluate_optional_sync, export_sync_shadow, load_sync_shadow_snapshot, main,
)
from quantum_autopilot.control_plane.runtime import ControllerRuntime
from quantum_autopilot.core import Provenance
from quantum_autopilot.model_intake import artifacts, catalog
from quantum_autopilot.model_intake.sync_session import evaluate_sync

pytestmark = pytest.mark.unit
ORIGIN = datetime(2026, 9, 23, tzinfo=timezone.utc)


@pytest.fixture
def archive(tmp_path, monkeypatch):
    path = tmp_path / "sync.zip"
    config = {"window": 8, "scale": 1e-9, "update_period_s": 1., "horizon_s": 2.,
              "coefficients": [0.] * 9, "intercept": .5}
    with zipfile.ZipFile(path, "w") as target:
        target.writestr(artifacts.QKD_ROOT + "modelos/sincronizador_ridge.json", json.dumps(config))
    monkeypatch.setitem(artifacts.PINS, "qkd", hashlib.sha256(path.read_bytes()).hexdigest())
    return path


def window():
    return SyncShadowWindow("sim/source", "campaign-1", "stream-1", "node-1", "sim/clock",
                            tuple(SyncSample((ORIGIN + timedelta(seconds=i)).isoformat(), i * 1e-10, 20.)
                                  for i in range(8)))


def state(**changes):
    args = dict(request_id="req-1", node_id="node-1", timestamp_utc=(ORIGIN + timedelta(seconds=7)).isoformat(),
                state_version=1, predicted_fidelity=.9, prediction_uncertainty=.01, telemetry_age_s=0.,
                memory_state="IDLE", integrity_verified=True,
                provenance=Provenance("simulated", "sim/source", timestamp=ORIGIN.isoformat()),
                metadata={"campaign_id": "campaign-1", "clock_id": "sim/clock"},
                telemetry={TELEMETRY_KEY: window().to_dict(), "osnr_db": 24.})
    return NetworkStateSnapshot(**{**args, **changes})


def run(snapshot, archive=None):
    return evaluate_optional_sync(snapshot, model_id=MODEL_ID, archive_path=archive)


def never_open(monkeypatch):
    monkeypatch.setattr(catalog, "open_optional_model", lambda *a, **k: pytest.fail("weights opened"))


def test_off_reads_neither_snapshot_nor_weights(monkeypatch):
    never_open(monkeypatch)
    result = evaluate_optional_sync(object(), archive_path="not-read.zip")
    assert result.status == "off" and result.evaluation is None
    assert result.snapshot_json == result.snapshot_sha256 == ""


def test_wdm_only_never_substitutes_for_missing_sync(monkeypatch):
    never_open(monkeypatch)
    result = run(state(telemetry={"ber": 1e-6, "temperature_c": 20., "offset_s": 0.}))
    assert result.status == "unavailable" and result.reason == "missing_sync_window"


@pytest.mark.parametrize("field,value", [
    ("source_id", "different"), ("campaign_id", "different"), ("node_id", "different"),
    ("clock_id", "different"), ("stream_id", ""), ("stream_id", "bad\n"),
    ("schema", "unknown"), ("evidence", "measured"), ("evidence", "synthesized"),
    ("offset_unit", "ns"), ("temperature_unit", "K"), ("reference", 0.),
    ("samples", []), ("samples", "invalid"),
])
def test_incompatible_window_refuses_before_loading(field, value, monkeypatch):
    never_open(monkeypatch)
    snapshot = state()
    snapshot.telemetry[TELEMETRY_KEY][field] = value
    result = run(snapshot)
    assert result.status == "refused" and result.reason == "incompatible_sync_window"


@pytest.mark.parametrize("field,value", [
    ("offset_s", True), ("offset_s", "0"), ("temperature_c", False),
    ("timestamp_utc", "not-time"), ("timestamp_utc", "2026-09-23T00:00:00"),
    ("timestamp_utc", "2026-09-23T00:00:02+00:00"), ("future_label", 1),
])
def test_bad_sample_refuses(field, value, monkeypatch):
    never_open(monkeypatch)
    snapshot = state()
    snapshot.telemetry[TELEMETRY_KEY]["samples"][0][field] = value
    assert run(snapshot).status == "refused"


@pytest.mark.parametrize("changes", [
    {"provenance": Provenance("measured", "sim/source")},
    {"provenance": Provenance("synthesized", "sim/source")},
    {"integrity_verified": False}, {"out_of_distribution": True},
    {"metadata": {}}, {"node_id": "wrong-node"},
    {"timestamp_utc": (ORIGIN + timedelta(seconds=6.999999)).isoformat()},
    {"timestamp_utc": (ORIGIN + timedelta(seconds=8.000001)).isoformat()},
])
def test_bad_context_refuses(changes, monkeypatch):
    never_open(monkeypatch)
    assert run(state(**changes)).status == "refused"


def test_unselected_and_invalid_api_inputs(monkeypatch):
    never_open(monkeypatch)
    with pytest.raises(ValueError, match="supports only"):
        evaluate_optional_sync(state(), model_id="qnet.routing.nominal")
    with pytest.raises(ValueError, match="NetworkStateSnapshot"):
        run({})
    assert run(state()).reason == "missing_archive"
    with pytest.raises(ValueError, match="128 KiB"):
        run(state(metadata={"huge": "x" * 131073}))
    with pytest.raises(ValueError):
        run(state(telemetry={"invalid": float("nan")}))


def test_predictions_equal_offline_and_reference_is_absent(archive):
    snapshot = state(timestamp_utc=(ORIGIN + timedelta(seconds=8)).isoformat())
    before = snapshot.to_json()
    result = run(snapshot, archive)
    offline = evaluate_sync(archive, window().model_input())
    assert result.status == "evaluated"
    assert result.evaluation.predicted_offset_s == offline.predicted_offset_s
    assert result.evaluation.persistence_offset_s == offline.persistence_offset_s
    assert result.evaluation.model_absolute_error_s is None
    assert result.evaluation.reference_offset_s is None
    assert result.target_timestamp_utc == (ORIGIN + timedelta(seconds=9)).isoformat()
    assert result.window_age_s == 1.
    assert snapshot.to_json() == before
    assert result.snapshot_sha256 == hashlib.sha256(result.snapshot_json.encode()).hexdigest()
    assert result.evaluation.archive_sha256 == artifacts.PINS["qkd"]
    assert not result.physical_action_performed and not result.policy_activated
    assert not result.simulator_action_performed


def test_equivalent_timezone_uses_same_causal_features(archive):
    original = state()
    shifted = state()
    for row in shifted.telemetry[TELEMETRY_KEY]["samples"]:
        row["timestamp_utc"] = datetime.fromisoformat(row["timestamp_utc"]).astimezone(
            timezone(timedelta(hours=-3))).isoformat()
    assert run(original, archive).evaluation.input_json == run(shifted, archive).evaluation.input_json


def test_changing_wdm_fidelity_or_mutating_original_cannot_change_offset(archive):
    snapshot = state()
    first = run(snapshot, archive)
    snapshot.telemetry["osnr_db"] = 10.
    second = run(replace(snapshot, predicted_fidelity=.1), archive)
    assert first.evaluation.predicted_offset_s == second.evaluation.predicted_offset_s
    assert first.snapshot_sha256 != second.snapshot_sha256
    assert json.loads(first.snapshot_json)["telemetry"]["osnr_db"] == 24.


def test_archive_rechecked_and_failure_is_not_persistence_fallback(archive):
    assert run(state(), archive).status == "evaluated"
    archive.write_bytes(b"changed")
    result = run(state(), archive)
    assert result.status == "refused" and result.evaluation is None
    assert result.reason == "model_evaluation_failed"
    assert run(state(), archive.with_name("missing.zip")).status == "refused"


def test_runtime_sidecar_does_not_touch_policy_executor_or_journal(archive, tmp_path, monkeypatch):
    runtime = ControllerRuntime(tmp_path / "commands.sqlite")
    snapshot = state()
    baseline = runtime.policy.decide(snapshot)
    before = (tmp_path / "commands.sqlite").read_bytes()
    for component, method in [(runtime.policy, "decide"), (runtime.journal, "append"),
                              (runtime.simulated_executor, "execute"), (runtime.dry_run_executor, "execute"),
                              (runtime.physical_executor, "execute"), (runtime, "cycle")]:
        monkeypatch.setattr(component, method, lambda *a, **k: pytest.fail("control invoked"))
    assert runtime.observe_optional_sync(snapshot).status == "off"
    result = runtime.observe_optional_sync(snapshot, model_id=MODEL_ID, archive_path=archive)
    assert result.status == "evaluated"
    assert (tmp_path / "commands.sqlite").read_bytes() == before
    monkeypatch.undo()
    assert runtime.policy.decide(snapshot) == baseline
    assert runtime.journal.list_recent() == []


def test_report_export_is_exclusive_and_replayable(archive, tmp_path):
    result = run(state(), archive)
    path = tmp_path / "shadow.json"
    export_sync_shadow(result, path)
    value = json.loads(path.read_text(encoding="utf-8"))
    replay = run(NetworkStateSnapshot.from_dict(json.loads(value["snapshot_json"])), archive)
    assert replay.snapshot_sha256 == result.snapshot_sha256
    assert replay.evaluation.predicted_offset_s == result.evaluation.predicted_offset_s
    with pytest.raises(FileExistsError):
        export_sync_shadow(result, path)


@pytest.mark.parametrize("values", [(float("nan"), 20.), (0., float("inf")), (10**400, 20.), (False, 20.)])
def test_nonfinite_sample_construction_is_refused(values):
    with pytest.raises(ValueError):
        SyncSample(ORIGIN.isoformat(), *values)


def test_typed_window_freezes_samples_and_checks_contract():
    rows = list(window().samples)
    typed = replace(window(), samples=rows)
    rows.clear()
    assert len(typed.samples) == 8
    assert SyncShadowWindow.from_dict(typed.to_dict()) == typed
    with pytest.raises(ValueError, match="typed SyncSample"):
        replace(typed, samples=[{}] * 8)


@pytest.mark.parametrize("raw", [b'[]', b'{"request_id":1,"request_id":2}', b'{"x": NaN}',
                                  b'{"x": 1e999}', b'\xff', b'{}', b' ' * 131073],
                         ids=["array", "duplicate", "nan", "overflow", "utf8", "missing", "oversize"])
def test_file_loader_is_bounded_strict_json(raw, tmp_path):
    path = tmp_path / "bad.json"
    path.write_bytes(raw)
    with pytest.raises(ValueError):
        load_sync_shadow_snapshot(path)


def test_cli_off_and_opt_in_report(archive, tmp_path, monkeypatch):
    input_path = tmp_path / "snapshot.json"
    output = tmp_path / "report.json"
    never_open(monkeypatch)
    assert main(["--input", str(input_path), "--output", str(output)]) == 0
    assert json.loads(output.read_text())["status"] == "off"
    # Undo just the no-load guard; preserve the synthetic archive pin.
    monkeypatch.undo()
    monkeypatch.setitem(artifacts.PINS, "qkd", hashlib.sha256(archive.read_bytes()).hexdigest())
    input_path.write_text(state().to_json(), encoding="utf-8")
    output2 = tmp_path / "enabled.json"
    args = ["--input", str(input_path), "--output", str(output2), "--model", MODEL_ID,
            "--qkd-zip", str(archive)]
    assert main(args) == 0
    assert json.loads(output2.read_text())["status"] == "evaluated"
    with pytest.raises(SystemExit) as exc:
        main(args)
    assert exc.value.code == 2


def test_cli_missing_features_returns_nonzero_with_report(tmp_path):
    input_path = tmp_path / "snapshot.json"
    output = tmp_path / "report.json"
    input_path.write_text(state(telemetry={}).to_json(), encoding="utf-8")
    assert main(["--input", str(input_path), "--output", str(output), "--model", MODEL_ID]) == 2
    assert json.loads(output.read_text())["reason"] == "missing_sync_window"
