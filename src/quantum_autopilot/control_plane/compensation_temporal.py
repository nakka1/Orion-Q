"""Causal, bounded angular scoring on newly acquired, disjoint SO(3) batches.

No inference, training or actuation. Registration precedes reception of the batch
containing the exact target sample; observation order is local, not an attestation.
"""
from copy import deepcopy
from datetime import timedelta
import json
import math
from pathlib import Path

from .contracts import NetworkStateSnapshot
from .optional_compensation import (
    MODEL_IDS, TELEMETRY_KEY, CompensationShadowRecord, CompensationShadowWindow,
)
from .optional_sync import MAX_SNAPSHOT_BYTES, _hash, _json, _time
from ..model_intake.compensation_session import _validate_rotation

CONTEXT_FIELDS = ("source_id", "campaign_id", "stream_id", "node_id", "clock_id",
                  "rotation_frame_id", "rotation_convention", "operating_point")


def _window(snapshot):
    if not isinstance(snapshot, NetworkStateSnapshot):
        raise ValueError("typed sandbox snapshot required")
    text = _json(snapshot.to_dict())
    if len(text.encode("utf-8")) > MAX_SNAPSHOT_BYTES:
        raise ValueError("snapshot exceeds 128 KiB")
    detached = NetworkStateSnapshot.from_dict(json.loads(text))
    window = CompensationShadowWindow.from_dict(detached.telemetry.get(TELEMETRY_KEY))
    window.validate_context(detached)
    return window, text


def angular_error(predicted, reference):
    """Geodesic angle in radians, with the same definition as offline scoring."""
    import numpy as np
    from ..model_intake.compensation_evaluation import _angles
    _validate_rotation(predicted)
    _validate_rotation(reference)
    return float(_angles(np.asarray(predicted), np.asarray(reference)))


class TemporalCompensationEvaluation:
    def __init__(self, *, max_predictions=256):
        if type(max_predictions) is not int or not 1 <= max_predictions <= 4096:
            raise ValueError("max_predictions must be an integer in 1..4096")
        self.max_predictions = max_predictions
        self._rows = []
        self._context = self._last_time = self._last_text = None
        self._archive_sha256 = self._model_id = None
        self._observations = 0

    def observe(self, snapshot):
        window, text = _window(snapshot)
        context = tuple(getattr(window, key) for key in CONTEXT_FIELDS)
        first, last = _time(window.samples[0].timestamp_utc), _time(window.samples[-1].timestamp_utc)
        if self._context is not None and context != self._context:
            raise ValueError("source/campaign/stream/node/clock/frame/convention/regime changed")
        if self._last_time is not None and first <= self._last_time:
            if text == self._last_text:
                return False
            raise ValueError("new disjoint batches required; overlap, rollback or conflicting duplicate")
        samples = {_time(sample.timestamp_utc): sample for sample in window.samples}
        updates = []
        for index, row in enumerate(self._rows):
            if row["status"] != "pending":
                continue
            target = _time(row["forecast"]["target_timestamp_utc"])
            if last < target:
                continue
            if target not in samples:
                updates.append((index, {"status": "missed_target"}))
                continue
            actual = [list(values) for values in samples[target].rotation]
            final = json.loads(row["forecast"]["evaluation_json"])["steps"][-1]
            updates.append((index, {"status": "paired", "reference_rotation": actual,
                                   "reference_timestamp_utc": samples[target].timestamp_utc,
                                   "reference_snapshot_json": text, "reference_snapshot_sha256": _hash(text),
                                   "reference_observation_index": self._observations + 1,
                                   "model_angular_error_rad": angular_error(final["predicted"], actual),
                                   "persistence_angular_error_rad": angular_error(final["persistence"], actual)}))
        # All validation/scoring is complete before mutating any run state.
        for index, values in updates:
            self._rows[index].update(values)
        self._context, self._last_time, self._last_text = context, last, text
        self._observations += 1
        return True

    def register(self, record):
        if (not isinstance(record, CompensationShadowRecord) or record.status != "evaluated"
                or record.final_output_status != "model" or not isinstance(record.evaluation_json, str)):
            raise ValueError("only completed learned shadow forecasts can be registered; not warm-up")
        if record.snapshot_json != self._last_text or record.snapshot_sha256 != _hash(record.snapshot_json):
            raise ValueError("forecast must match latest observed batch; late registration refused")
        window, _ = _window(NetworkStateSnapshot.from_dict(json.loads(record.snapshot_json)))
        model_id = record.selected_model_id
        expected = window.model_input(model_id)
        request = json.loads(expected)
        evaluation = json.loads(record.evaluation_json)
        try:
            steps = evaluation["steps"]
            final = steps[-1]
            if (model_id not in MODEL_IDS or evaluation["model_id"] != model_id
                    or evaluation["schema"] != "orion.optional-compensation-evaluation.v1"
                    or record.mode != "shadow" or record.physical_action_performed
                    or record.simulator_action_performed or record.policy_activated
                    or evaluation["physical_action_performed"] is not False
                    or evaluation["evidence"] != "simulated_offline"
                    or evaluation["input_json"] != expected or evaluation["input_sha256"] != _hash(expected)
                    or evaluation["stream_id"] != window.stream_id
                    or len(steps) != 16 or len(window.samples) != 16
                    or [step["status"] for step in steps] != ["warmup_persistence"] * 15 + ["model"]
                    or final["target_timestamp_s"] != request["samples"][-1]["timestamp_s"] + .04
                    or _time(record.target_timestamp_utc) != _time(window.samples[-1].timestamp_utc) + timedelta(milliseconds=40)
                    or final["persistence"] != request["samples"][-1]["rotation"]
                    or any(evaluation[key] is not None for key in ("reference_rotation", "model_angular_error_rad",
                                                                  "persistence_angular_error_rad"))):
                raise ValueError("forecast contract mismatch or future reference contamination")
            _validate_rotation(final["predicted"])
            _validate_rotation(final["persistence"])
            digest = evaluation["archive_sha256"]
            if not isinstance(digest, str) or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
                raise ValueError("invalid archive identity")
            if any(step["archive_sha256"] != digest for step in steps):
                raise ValueError("inconsistent artifact identities")
        except (KeyError, IndexError, TypeError) as exc:
            raise ValueError("invalid forecast evaluation structure") from exc
        if self._archive_sha256 is not None and (digest, model_id) != (self._archive_sha256, self._model_id):
            raise ValueError("checkpoint or artifact changed; start a new evaluation")
        encoded = json.loads(record.serialized())
        for row in self._rows:
            if row["forecast"]["snapshot_sha256"] == record.snapshot_sha256:
                if row["forecast"] == encoded:
                    return False
                raise ValueError("conflicting forecast for same batch")
        if len(self._rows) >= self.max_predictions:
            raise ValueError("evaluation capacity reached; export and start a new run")
        self._archive_sha256, self._model_id = digest, model_id
        self._rows.append({"status": "pending", "committed_observation_index": self._observations,
                           "forecast": encoded})
        return True

    def report(self, *, include_records=True):
        paired = [row for row in self._rows if row["status"] == "paired"]
        missed = sum(row["status"] == "missed_target" for row in self._rows)
        count = len(paired)
        model = [row["model_angular_error_rad"] for row in paired]
        baseline = [row["persistence_angular_error_rad"] for row in paired]
        mae = math.fsum(model) / count if count else None
        baseline_mae = math.fsum(baseline) / count if count else None
        return {
            "schema": "orion.compensation-temporal-evaluation.v1", "evidence": "simulated",
            "scope": "descriptive angular errors on artificial batches; not fidelity or control gain",
            "pairing": "exact target within subsequently received disjoint batch; no interpolation or backfill",
            "context": dict(zip(CONTEXT_FIELDS, self._context)) if self._context else None,
            "model_id": self._model_id, "archive_sha256": self._archive_sha256,
            "max_predictions": self.max_predictions, "observed_batches": self._observations,
            "registered_predictions": len(self._rows), "paired": count, "missed_target": missed,
            "pending": len(self._rows) - count - missed,
            "coverage_of_resolved_targets": count / (count + missed) if count + missed else None,
            "model_mean_angle_rad": mae, "persistence_mean_angle_rad": baseline_mae,
            "model_rms_angle_rad": math.hypot(*model) / math.sqrt(count) if count else None,
            "persistence_rms_angle_rad": math.hypot(*baseline) / math.sqrt(count) if count else None,
            "mean_angle_delta_model_minus_persistence_rad": mae - baseline_mae if count else None,
            "limitations": ["Correlated artificial samples; no statistical significance or generalization claim.",
                            "Observation order enforced in process, not signed acquisition timestamps.",
                            "Reported coverage is conditional on committed predictions; inspect observed batch count.",
                            "No noise, photon model, tuning or physical domain certification by this evaluator."],
            "physical_action_performed": False, "policy_activated": False,
            **({"records": deepcopy(self._rows)} if include_records else {}),
        }

    def serialized(self):
        return json.dumps(self.report(), ensure_ascii=False, indent=2, allow_nan=False)


def export_compensation_temporal_report(text, path):
    with Path(path).open("xb") as stream:
        stream.write(text.encode("utf-8"))
