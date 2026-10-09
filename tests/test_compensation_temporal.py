"""Causal target matching, immutable forecasts, strict SO(3) and context isolation."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math

import pytest

from quantum_autopilot.control_plane.compensation_temporal import (
    TemporalCompensationEvaluation, angular_error, export_compensation_temporal_report,
)
from quantum_autopilot.control_plane.contracts import NetworkStateSnapshot
from quantum_autopilot.control_plane.optional_compensation import MODEL_IDS, TELEMETRY_KEY, evaluate_optional_compensation
from tests import test_optional_compensation_session as fixtures
from tools.validate_compensation_shadow import demo_snapshot

compensation_archive = fixtures.compensation_archive
pytestmark = pytest.mark.unit


def rotation(angle):
    c, s = math.cos(angle), math.sin(angle)
    return [[c, -s, 0.], [s, c, 0.], [0., 0., 1.]]


def snapshot(batch=0, model_id=MODEL_IDS[0], start=None):
    data = demo_snapshot(model_id, 16).to_dict()
    origin = datetime(2026, 1, 1, tzinfo=timezone.utc)
    for i, sample in enumerate(data["telemetry"][TELEMETRY_KEY]["samples"]):
        index = (batch * 16 if start is None else start) + i
        sample["timestamp_utc"] = (origin + timedelta(milliseconds=index * 2)).isoformat()
        sample["rotation"] = rotation(index * .001)
    data["timestamp_utc"] = data["telemetry"][TELEMETRY_KEY]["samples"][-1]["timestamp_utc"]
    data["request_id"] = f"batch/{batch}/{start}"
    return NetworkStateSnapshot.from_dict(data)


def forecast(archive, batch=0, model_id=MODEL_IDS[0]):
    return evaluate_optional_compensation(snapshot(batch, model_id), model_id=model_id, archive_path=archive)


@pytest.mark.parametrize("model_id", MODEL_IDS)
def test_exact_target_inside_later_batch(compensation_archive, model_id):
    tracker = TemporalCompensationEvaluation()
    tracker.observe(snapshot(model_id=model_id))
    prediction = forecast(compensation_archive, model_id=model_id)
    original = prediction.serialized()
    assert tracker.register(prediction) and not tracker.register(prediction)
    tracker.observe(snapshot(1, model_id))
    assert tracker.report()["pending"] == 1 and tracker.report()["model_mean_angle_rad"] is None
    tracker.observe(snapshot(2, model_id))
    report = tracker.report()
    assert report["paired"] == 1 and report["pending"] == 0 and report["coverage_of_resolved_targets"] == 1.
    assert report["model_mean_angle_rad"] == pytest.approx(0., abs=1e-7)
    assert report["persistence_mean_angle_rad"] == pytest.approx(.02)
    assert report["persistence_rms_angle_rad"] == pytest.approx(.02)
    row = report["records"][0]
    assert row["reference_timestamp_utc"] == prediction.target_timestamp_utc
    assert row["reference_observation_index"] > row["committed_observation_index"]
    assert row["reference_snapshot_sha256"] == hashlib.sha256(row["reference_snapshot_json"].encode()).hexdigest()
    assert prediction.serialized() == original
    assert json.loads(row["forecast"]["evaluation_json"])["reference_rotation"] is None
    row["forecast"]["status"] = "tampered"
    assert tracker.report()["records"][0]["forecast"]["status"] == "evaluated"


def test_missing_batch_no_interpolation_or_backfill(compensation_archive):
    tracker = TemporalCompensationEvaluation()
    tracker.observe(snapshot())
    tracker.register(forecast(compensation_archive))
    tracker.observe(snapshot(3))
    report = tracker.serialized()
    assert tracker.report()["missed_target"] == 1 and tracker.report()["paired"] == 0
    assert tracker.report()["coverage_of_resolved_targets"] == 0.
    with pytest.raises(ValueError, match="disjoint"):
        tracker.observe(snapshot(2))
    assert tracker.serialized() == report


@pytest.mark.parametrize("batch", [1, 2, 3])
def test_late_registration_refused(compensation_archive, batch):
    tracker = TemporalCompensationEvaluation()
    tracker.observe(snapshot())
    prediction = forecast(compensation_archive)
    tracker.observe(snapshot(batch))
    with pytest.raises(ValueError, match="latest"):
        tracker.register(prediction)
    assert tracker.report()["registered_predictions"] == 0


@pytest.mark.parametrize("fault", ["overlap", "duplicate", "frame", "stream", "regime", "clock", "campaign", "measured", "rotation"])
def test_invalid_observation_is_atomic(compensation_archive, fault):
    tracker = TemporalCompensationEvaluation()
    initial = snapshot()
    tracker.observe(initial)
    assert not tracker.observe(initial)
    tracker.register(forecast(compensation_archive))
    before = tracker.serialized()
    changed = snapshot(2)
    if fault == "overlap":
        changed = snapshot(start=8)
    elif fault == "duplicate":
        changed = replace(initial, request_id="conflicting")
    elif fault == "regime":
        changed = snapshot(2, MODEL_IDS[2])
    elif fault == "measured":
        data = changed.to_dict()
        data["provenance"]["evidence_kind"] = "measured"
        changed = NetworkStateSnapshot.from_dict(data)
    elif fault == "rotation":
        changed.telemetry[TELEMETRY_KEY]["samples"][3]["rotation"] = [[0., 0., 0.]] * 3
    else:
        field = {"frame": "rotation_frame_id", "clock": "clock_id", "campaign": "campaign_id", "stream": "stream_id"}[fault]
        changed.telemetry[TELEMETRY_KEY][field] = "changed"
        if fault != "stream":
            changed.metadata[field] = "changed"
    with pytest.raises(ValueError):
        tracker.observe(changed)
    assert tracker.serialized() == before


@pytest.mark.parametrize("fault", ["reference", "error", "hash", "input", "baseline", "rotation", "target", "identity", "schema", "steps"])
def test_contaminated_forecast_refused(compensation_archive, fault):
    tracker = TemporalCompensationEvaluation()
    tracker.observe(snapshot())
    prediction = forecast(compensation_archive)
    data = json.loads(prediction.evaluation_json)
    if fault == "reference":
        data["reference_rotation"] = rotation(.035)
    elif fault == "error":
        data["model_angular_error_rad"] = 0.
    elif fault == "hash":
        data["input_sha256"] = "0" * 64
    elif fault == "input":
        data["input_json"] = "{}"
    elif fault == "baseline":
        data["steps"][-1]["persistence"] = rotation(.1)
    elif fault == "rotation":
        data["steps"][-1]["predicted"] = [[0., 0., 0.]] * 3
    elif fault == "target":
        data["steps"][-1]["target_timestamp_s"] += .002
    elif fault == "identity":
        data["steps"][-1]["archive_sha256"] = "a" * 64
    elif fault == "schema":
        data["schema"] = "incorrect"
    else:
        data["steps"] = []
    with pytest.raises(ValueError):
        tracker.register(replace(prediction, evaluation_json=json.dumps(data)))
    assert tracker.report()["registered_predictions"] == 0


def test_checkpoint_and_capacity_isolation(compensation_archive):
    tracker = TemporalCompensationEvaluation(max_predictions=1)
    tracker.observe(snapshot())
    tracker.register(forecast(compensation_archive))
    tracker.observe(snapshot(1))
    with pytest.raises(ValueError, match="checkpoint"):
        tracker.register(forecast(compensation_archive, 1, MODEL_IDS[1]))
    with pytest.raises(ValueError, match="capacity"):
        tracker.register(forecast(compensation_archive, 1))
    assert tracker.report()["registered_predictions"] == 1


def test_warmup_not_scored(compensation_archive):
    short = demo_snapshot(MODEL_IDS[0], 1)
    tracker = TemporalCompensationEvaluation()
    tracker.observe(short)
    result = evaluate_optional_compensation(short, model_id=MODEL_IDS[0], archive_path=compensation_archive)
    with pytest.raises(ValueError, match="warm-up"):
        tracker.register(result)


@pytest.mark.parametrize("value", [0, -1, 4097, True, 1.5])
def test_capacity_validation(value):
    with pytest.raises(ValueError):
        TemporalCompensationEvaluation(max_predictions=value)


@pytest.mark.parametrize("angle", [0., .002, .8, math.pi - 1e-7, math.pi])
def test_known_angular_distances(angle):
    assert angular_error(rotation(0.), rotation(angle)) == pytest.approx(angle, abs=2e-8)


def test_export_is_captured_and_exclusive(compensation_archive, tmp_path):
    tracker = TemporalCompensationEvaluation()
    tracker.observe(snapshot())
    tracker.register(forecast(compensation_archive))
    captured = tracker.serialized()
    tracker.observe(snapshot(2))
    path = tmp_path / "report.json"
    export_compensation_temporal_report(captured, path)
    assert path.read_text(encoding="utf-8") == captured
    with pytest.raises(FileExistsError):
        export_compensation_temporal_report(tracker.serialized(), path)


def test_worse_prediction_is_not_filtered(compensation_archive):
    tracker = TemporalCompensationEvaluation()
    tracker.observe(snapshot())
    result = forecast(compensation_archive)
    evaluation = json.loads(result.evaluation_json)
    evaluation["steps"][-1]["predicted"] = rotation(.5)
    tracker.register(replace(result, evaluation_json=json.dumps(evaluation)))
    tracker.observe(snapshot(2))
    report = tracker.report()
    assert report["model_mean_angle_rad"] == pytest.approx(.465)
    assert report["persistence_mean_angle_rad"] == pytest.approx(.02)
    assert report["mean_angle_delta_model_minus_persistence_rad"] > 0


def test_new_artifact_cannot_share_run(compensation_archive):
    tracker = TemporalCompensationEvaluation()
    tracker.observe(snapshot())
    tracker.register(forecast(compensation_archive))
    tracker.observe(snapshot(1))
    result = forecast(compensation_archive, 1)
    evaluation = json.loads(result.evaluation_json)
    evaluation["archive_sha256"] = "a" * 64
    for step in evaluation["steps"]:
        step["archive_sha256"] = "a" * 64
    with pytest.raises(ValueError, match="artifact changed"):
        tracker.register(replace(result, evaluation_json=json.dumps(evaluation)))
