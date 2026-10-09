"""SO(3) bridge refuses incompatible telemetry before opening any weights."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import hashlib
import json

import pytest

from quantum_autopilot.control_plane.contracts import NetworkStateSnapshot
from quantum_autopilot.control_plane.optional_compensation import (
    CONVENTION, MODEL_IDS, REGIMES, TELEMETRY_KEY, CompensationShadowWindow, PolarisationSample,
    evaluate_optional_compensation, export_compensation_shadow,
)
from quantum_autopilot.control_plane.runtime import ControllerRuntime
from quantum_autopilot.core import Provenance
from quantum_autopilot.model_intake import catalog
from quantum_autopilot.model_intake.compensation_session import demo_compensation_input, evaluate_compensation_session
from tests import test_optional_compensation_session as fixtures

compensation_archive = fixtures.compensation_archive

pytestmark = pytest.mark.unit
ORIGIN = datetime(2026, 9, 23, tzinfo=timezone.utc)
MODEL = MODEL_IDS[0]


def window(model_id=MODEL, count=16):
    point = model_id.split(".")[2]
    demo = json.loads(demo_compensation_input(model_id))
    samples = tuple(PolarisationSample((ORIGIN + timedelta(seconds=row["timestamp_s"])).isoformat(),
                                       row["rotation"], **row["covariates"]) for row in demo["samples"][:count])
    return CompensationShadowWindow("demo", "campaign", "stream", "node", "clock", "frame-1", point,
                                    *REGIMES[point], samples)


def snapshot(model_id=MODEL, count=16):
    data = window(model_id, count)
    return NetworkStateSnapshot(
        request_id="req", node_id="node", timestamp_utc=data.samples[-1].timestamp_utc, state_version=1,
        predicted_fidelity=.5, prediction_uncertainty=1., telemetry_age_s=0., memory_state="IDLE",
        integrity_verified=True, telemetry={TELEMETRY_KEY: data.to_dict()},
        provenance=Provenance("simulated", "demo", timestamp=ORIGIN.isoformat()),
        metadata={"campaign_id": "campaign", "clock_id": "clock", "rotation_frame_id": "frame-1",
                  "rotation_convention": CONVENTION, "compensation_operating_point": data.operating_point})


def run(state, archive=None, model_id=MODEL):
    return evaluate_optional_compensation(state, model_id=model_id, archive_path=archive)


def forbid_loading(monkeypatch):
    monkeypatch.setattr(catalog, "open_optional_model", lambda *a, **k: pytest.fail("model opened"))


def test_off_inspects_nothing(monkeypatch):
    forbid_loading(monkeypatch)
    assert evaluate_optional_compensation(object(), archive_path="not-read").status == "off"


def test_wdm_and_timing_are_not_polarisation(monkeypatch):
    forbid_loading(monkeypatch)
    state = replace(snapshot(), telemetry={"ber": 1e-6, "offset_s": 0., "optional_sync_window": {}})
    assert run(state).reason == "missing_compensation_window"


@pytest.mark.parametrize("model_id", MODEL_IDS)
def test_all_checkpoints_equal_offline_and_preserve_snapshot(compensation_archive, model_id):
    state = snapshot(model_id)
    before = state.to_json()
    result = run(state, compensation_archive, model_id)
    offline = evaluate_compensation_session(model_id, compensation_archive, window(model_id).model_input(model_id))
    report = json.loads(result.evaluation_json)
    assert result.status == "evaluated" and result.final_output_status == "model"
    assert report["steps"] == json.loads(offline.serialized())["steps"]
    assert report["reference_rotation"] is None and report["model_angular_error_rad"] is None
    assert report["persistence_angular_error_rad"] is None
    assert result.target_timestamp_utc == (ORIGIN + timedelta(milliseconds=70)).isoformat()
    assert state.to_json() == before
    assert result.snapshot_sha256 == hashlib.sha256(result.snapshot_json.encode()).hexdigest()
    assert not result.policy_activated and not result.simulator_action_performed and not result.physical_action_performed


@pytest.mark.parametrize("count", [1, 8, 15])
def test_warmup_never_claims_learned_output(compensation_archive, count):
    result = run(snapshot(count=count), compensation_archive)
    assert result.final_output_status == "warmup_persistence"
    rows = json.loads(result.evaluation_json)["steps"]
    assert all(row["predicted"] == row["persistence"] and row["features"] is None for row in rows)


@pytest.mark.parametrize("field,value", [
    ("evidence", "measured"), ("operating_point", "unknown"), ("operating_point", "desenvolvimento"),
    ("vibration_frequency_hz", 40.), ("thermal_drift_rate_rad_s", .05),
    ("monitoring_photons_per_basis", True), ("monitoring_photons_per_basis", 200),
    ("period_s", 1.), ("horizon_s", 2.), ("temperature_unit", "K"),
    ("temperature_rate_unit", "degC/min"), ("vibration_unit", "degrees"),
    ("rotation_convention", "unknown_axes"), ("rotation_frame_id", "other"),
    ("source_id", "other"), ("campaign_id", "other"), ("node_id", "other"),
    ("clock_id", "other"), ("stream_id", ""), ("samples", []), ("reference", {}),
])
def test_bad_window_refuses_before_weights(field, value, monkeypatch):
    forbid_loading(monkeypatch)
    state = snapshot()
    state.telemetry[TELEMETRY_KEY][field] = value
    result = run(state)
    assert result.status == "refused" and result.reason == "incompatible_compensation_window"


@pytest.mark.parametrize("field,value", [
    ("rotation", [[1, 0, 0], [0, 1, 0], [0, 0, -1]]),
    ("rotation", [[2, 0, 0], [0, 1, 0], [0, 0, 1]]),
    ("rotation", [[True, 0, 0], [0, 1, 0], [0, 0, 1]]),
    ("temperature_c", -274.), ("temperature_c", "20"),
    ("temperature_rate_k_s", False), ("vibration_rms_rad", -.1),
    ("timestamp_utc", "2026-09-23T00:00:00"),
    ("timestamp_utc", "2026-09-23T00:00:00.001000+00:00"),
    ("future_rotation", []),
])
def test_bad_sample_refused(field, value, monkeypatch):
    forbid_loading(monkeypatch)
    state = snapshot()
    state.telemetry[TELEMETRY_KEY]["samples"][0][field] = value
    assert run(state).status == "refused"


@pytest.mark.parametrize("change", ["measured", "integrity", "ood", "metadata", "future", "stale"])
def test_context_refused(change, monkeypatch):
    forbid_loading(monkeypatch)
    state = snapshot()
    if change == "measured":
        state.provenance.evidence_kind = "measured"
    elif change == "integrity":
        state = replace(state, integrity_verified=False)
    elif change == "ood":
        state = replace(state, out_of_distribution=True)
    elif change == "metadata":
        state.metadata.clear()
    else:
        stamp = ORIGIN + timedelta(milliseconds=29 if change == "future" else 32.001)
        state = replace(state, timestamp_utc=stamp.isoformat())
    assert run(state).status == "refused"


def test_selected_checkpoint_never_changes_regime(monkeypatch):
    forbid_loading(monkeypatch)
    assert run(snapshot(), model_id=MODEL_IDS[2]).status == "refused"
    assert run(snapshot()).reason == "missing_archive"
    with pytest.raises(ValueError, match="checkpoint"):
        run(snapshot(), model_id="qkd.sync.ridge.1s.h2s")


def test_canonical_time_and_detached_records(compensation_archive):
    state = snapshot()
    normal = run(state, compensation_archive)
    for row in state.telemetry[TELEMETRY_KEY]["samples"]:
        row["timestamp_utc"] = datetime.fromisoformat(row["timestamp_utc"]).astimezone(timezone(timedelta(hours=-3))).isoformat()
    alternate = run(state, compensation_archive)
    assert json.loads(normal.evaluation_json)["input_json"] == json.loads(alternate.evaluation_json)["input_json"]
    state.telemetry[TELEMETRY_KEY]["samples"][0]["temperature_c"] = 90.
    assert json.loads(normal.snapshot_json)["telemetry"][TELEMETRY_KEY]["samples"][0]["temperature_c"] == 20.


def test_export_and_archive_reverification(compensation_archive, tmp_path):
    result = run(snapshot(), compensation_archive)
    target = tmp_path / "shadow.json"
    export_compensation_shadow(result, target)
    with pytest.raises(FileExistsError):
        export_compensation_shadow(result, target)
    assert json.loads(target.read_text(encoding="utf-8"))["snapshot_sha256"] == result.snapshot_sha256
    compensation_archive.write_bytes(b"changed")
    assert run(snapshot(), compensation_archive).reason == "model_evaluation_failed"


def test_runtime_sidecar_never_runs_control(compensation_archive, tmp_path, monkeypatch):
    runtime = ControllerRuntime(tmp_path / "commands.sqlite")
    before = (tmp_path / "commands.sqlite").read_bytes()
    for target, method in [(runtime, "cycle"), (runtime.policy, "decide"), (runtime.journal, "append"),
                           (runtime.simulated_executor, "execute"), (runtime.physical_executor, "execute"),
                           (runtime.dry_run_executor, "execute")]:
        monkeypatch.setattr(target, method, lambda *a, **k: pytest.fail("control invoked"))
    assert runtime.observe_optional_compensation(object()).status == "off"
    assert runtime.observe_optional_compensation(snapshot(), model_id=MODEL,
                                                archive_path=compensation_archive).status == "evaluated"
    assert (tmp_path / "commands.sqlite").read_bytes() == before


def test_typed_samples_freeze_matrices_and_reject_nonfinite():
    values = [[1., 0., 0.], [0., 1., 0.], [0., 0., 1.]]
    sample = PolarisationSample(ORIGIN.isoformat(), values, 20., 0., .3)
    values[0][0] = 2.
    assert sample.rotation[0][0] == 1.
    with pytest.raises(ValueError):
        replace(sample, temperature_rate_k_s=float("nan"))
    assert CompensationShadowWindow.from_dict(window().to_dict()) == window()


def test_age_boundary_and_ignored_fidelity(compensation_archive):
    state = replace(snapshot(), timestamp_utc=(ORIGIN + timedelta(milliseconds=32)).isoformat())
    first = run(state, compensation_archive)
    second = run(replace(state, predicted_fidelity=.99, telemetry_age_s=99.), compensation_archive)
    assert first.status == "evaluated" and first.window_age_s == .002
    assert json.loads(first.evaluation_json)["steps"] == json.loads(second.evaluation_json)["steps"]


@pytest.mark.parametrize("change", ["too_many", "nan", "large_snapshot", "bool_frequency"])
def test_bounds_before_archive(change, monkeypatch):
    forbid_loading(monkeypatch)
    state = snapshot()
    if change == "large_snapshot":
        state.metadata["oversized"] = "x" * 131073
        with pytest.raises(ValueError, match="128 KiB"):
            run(state)
    elif change == "nan":
        state.telemetry[TELEMETRY_KEY]["samples"][0]["vibration_rms_rad"] = float("nan")
        with pytest.raises(ValueError):
            run(state)
    else:
        payload = state.telemetry[TELEMETRY_KEY]
        if change == "too_many":
            payload["samples"].append(payload["samples"][-1])
        else:
            payload["vibration_frequency_hz"] = True
        assert run(state).status == "refused"
