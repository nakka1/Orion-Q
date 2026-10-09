"""Delayed labels cannot revise or retrospectively create committed forecasts."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import hashlib
import json
import zipfile

import pytest

from quantum_autopilot.control_plane.contracts import NetworkStateSnapshot
from quantum_autopilot.control_plane.optional_sync import (
    MODEL_ID, SyncSample, SyncShadowWindow, TELEMETRY_KEY, evaluate_optional_sync,
)
from quantum_autopilot.control_plane.sync_temporal import TemporalSyncEvaluation, export_temporal_report
from quantum_autopilot.core import Provenance
from quantum_autopilot.model_intake import artifacts

pytestmark = pytest.mark.unit
ORIGIN = datetime(2026, 1, 1, tzinfo=timezone.utc)


def snapshot(tick=7):
    stamp = (ORIGIN + timedelta(seconds=tick)).isoformat()
    window = SyncShadowWindow("sim", "campaign", "stream", "node", "clock", tuple(
        SyncSample((ORIGIN + timedelta(seconds=i)).isoformat(), i * 1e-10, 20.)
        for i in range(tick - 7, tick + 1)))
    return NetworkStateSnapshot(
        request_id=str(tick), node_id="node", timestamp_utc=stamp, state_version=tick,
        predicted_fidelity=.5, prediction_uncertainty=1., telemetry_age_s=0., memory_state="IDLE",
        integrity_verified=True, provenance=Provenance("simulated", "sim", timestamp=stamp),
        metadata={"campaign_id": "campaign", "clock_id": "clock"},
        telemetry={TELEMETRY_KEY: window.to_dict()})


@pytest.fixture
def archive(tmp_path, monkeypatch):
    path = tmp_path / "sync.zip"
    config = {"window": 8, "scale": 1e-9, "update_period_s": 1., "horizon_s": 2.,
              "coefficients": [0.] * 9, "intercept": .5}
    with zipfile.ZipFile(path, "w") as target:
        target.writestr(artifacts.QKD_ROOT + "modelos/sincronizador_ridge.json", json.dumps(config))
    monkeypatch.setitem(artifacts.PINS, "qkd", hashlib.sha256(path.read_bytes()).hexdigest())
    return path


def forecast(archive, tick=7):
    return evaluate_optional_sync(snapshot(tick), model_id=MODEL_ID, archive_path=archive)


def test_exact_delayed_pair_retains_worse_model(archive):
    tracker = TemporalSyncEvaluation()
    tracker.observe(snapshot())
    prediction = forecast(archive)
    original = prediction.serialized()
    assert tracker.register(prediction)
    assert not tracker.register(prediction)
    assert tracker.report()["paired"] == 0
    assert tracker.report()["model_mae_s"] is None
    tracker.observe(snapshot(8))
    assert tracker.report()["pending"] == 1
    tracker.observe(snapshot(9))
    result = tracker.report()
    assert result["paired"] == 1 and result["pending"] == 0
    assert result["model_mae_s"] == pytest.approx(.3e-9)
    assert result["persistence_mae_s"] == pytest.approx(.2e-9)
    assert result["model_rmse_s"] == pytest.approx(.3e-9)
    assert result["mae_delta_model_minus_persistence_s"] > 0
    row = result["records"][0]
    assert row["reference_observation_index"] > row["committed_observation_index"]
    assert row["reference_snapshot_sha256"] == hashlib.sha256(row["reference_snapshot_json"].encode()).hexdigest()
    assert prediction.serialized() == original
    assert row["forecast"]["evaluation"]["reference_offset_s"] is None


def test_no_retrospective_backfill_from_window_history(archive):
    tracker = TemporalSyncEvaluation()
    tracker.observe(snapshot())
    tracker.register(forecast(archive))
    tracker.observe(snapshot(10))  # This window includes t=9, but its latest sample is t=10.
    report = tracker.report()
    assert report["missed_target"] == 1 and report["paired"] == 0
    assert report["model_mae_s"] is None


@pytest.mark.parametrize("tick", [8, 9, 10])
def test_late_registration_is_refused(archive, tick):
    tracker = TemporalSyncEvaluation()
    prediction = forecast(archive)
    tracker.observe(snapshot())
    tracker.observe(snapshot(tick))
    with pytest.raises(ValueError, match="late registration"):
        tracker.register(prediction)
    assert tracker.report()["registered_predictions"] == 0


@pytest.mark.parametrize("field", ["node_id", "source_id", "campaign_id", "clock_id", "stream_id"])
def test_cross_context_reference_refused_without_partial_mutation(archive, field):
    tracker = TemporalSyncEvaluation()
    tracker.observe(snapshot())
    tracker.register(forecast(archive))
    before = tracker.serialized()
    reference = snapshot(9)
    reference.telemetry[TELEMETRY_KEY][field] = "changed"
    with pytest.raises(ValueError):
        tracker.observe(reference)
    assert tracker.serialized() == before


def test_conflicting_duplicate_and_reversed_observation_refused(archive):
    tracker = TemporalSyncEvaluation()
    tracker.observe(snapshot(8))
    assert not tracker.observe(snapshot(8))
    for state in (snapshot(7), replace(snapshot(8), request_id="conflict")):
        with pytest.raises(ValueError, match="conflicting or out-of-order"):
            tracker.observe(state)
    assert tracker.report()["observed_frames"] == 1


@pytest.mark.parametrize("fault", ["future", "input_hash", "snapshot_hash", "target", "baseline",
                                   "model", "nonfinite", "artifact", "bool", "physical"])
def test_tampered_prediction_refused(archive, fault):
    tracker = TemporalSyncEvaluation()
    tracker.observe(snapshot())
    record = forecast(archive)
    evaluation = record.evaluation
    if fault == "snapshot_hash":
        record = replace(record, snapshot_sha256="0" * 64)
    elif fault == "target":
        record = replace(record, target_timestamp_utc=snapshot(10).timestamp_utc)
    else:
        key, value = {"future": ("reference_offset_s", 1.), "input_hash": ("input_sha256", "0" * 64),
                      "baseline": ("persistence_offset_s", 3.), "model": ("model_id", "other"),
                      "nonfinite": ("predicted_offset_s", float("nan")),
                      "artifact": ("archive_sha256", "bad"), "bool": ("predicted_offset_s", True),
                      "physical": ("physical_action_performed", True)}[fault]
        record = replace(record, evaluation=replace(evaluation, **{key: value}))
    with pytest.raises(ValueError):
        tracker.register(record)
    assert tracker.report()["registered_predictions"] == 0


def test_artifacts_cannot_mix_or_forecasts_be_revised(archive):
    tracker = TemporalSyncEvaluation()
    tracker.observe(snapshot())
    first = forecast(archive)
    tracker.register(first)
    with pytest.raises(ValueError, match="conflicting forecast"):
        tracker.register(replace(first, evaluation=replace(first.evaluation, predicted_offset_s=0.)))
    tracker.observe(snapshot(8))
    second = forecast(archive, 8)
    with pytest.raises(ValueError, match="artifact changed"):
        tracker.register(replace(second, evaluation=replace(second.evaluation, archive_sha256="f" * 64)))


@pytest.mark.parametrize("limit", [0, -1, 4097, True, 1.5])
def test_capacity_configuration(limit):
    with pytest.raises(ValueError):
        TemporalSyncEvaluation(max_predictions=limit)


def test_capacity_is_bounded_without_silent_eviction(archive):
    tracker = TemporalSyncEvaluation(max_predictions=1)
    tracker.observe(snapshot())
    tracker.register(forecast(archive))
    tracker.observe(snapshot(8))
    with pytest.raises(ValueError, match="capacity"):
        tracker.register(forecast(archive, 8))
    tracker.observe(snapshot(9))
    assert tracker.report()["paired"] == tracker.report()["registered_predictions"] == 1


def test_report_copy_and_exclusive_export(archive, tmp_path):
    tracker = TemporalSyncEvaluation()
    tracker.observe(snapshot())
    tracker.register(forecast(archive))
    report = tracker.report()
    report["records"][0]["status"] = "forged"
    assert tracker.report()["records"][0]["status"] == "pending"
    target = tmp_path / "temporal.json"
    export_temporal_report(tracker.serialized(), target)
    original = target.read_bytes()
    tracker.observe(snapshot(9))
    with pytest.raises(FileExistsError):
        export_temporal_report(tracker.serialized(), target)
    assert target.read_bytes() == original


def test_zero_error_baseline_does_not_create_infinite_gain(archive):
    tracker = TemporalSyncEvaluation()
    tracker.observe(snapshot())
    tracker.register(forecast(archive))
    state = snapshot(9)
    state.telemetry[TELEMETRY_KEY]["samples"][-1]["offset_s"] = 7 * 1e-10
    tracker.observe(state)
    result = tracker.report()
    assert result["persistence_mae_s"] == 0
    assert result["mae_delta_model_minus_persistence_s"] > 0
    json.loads(tracker.serialized())


@pytest.mark.parametrize("fault", ["measured", "nonfinite", "overflow", "integrity"])
def test_reference_must_be_valid_simulated_evidence(archive, fault):
    tracker = TemporalSyncEvaluation()
    tracker.observe(snapshot())
    tracker.register(forecast(archive))
    state = snapshot(9)
    if fault == "measured":
        state.provenance.evidence_kind = "measured"
    elif fault == "integrity":
        state = replace(state, integrity_verified=False)
    else:
        state.telemetry[TELEMETRY_KEY]["samples"][-1]["offset_s"] = float("nan") if fault == "nonfinite" else 1e308
    before = tracker.serialized()
    with pytest.raises(ValueError):
        tracker.observe(state)
    assert tracker.serialized() == before
