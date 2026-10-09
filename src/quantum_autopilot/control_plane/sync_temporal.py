"""Bounded causal scoring of committed synchronization forecasts, without inference.

Only the latest sample of a subsequently received frame can settle a forecast.
Historical samples in a later window never fill a missed target retrospectively.
"""
from copy import deepcopy
from datetime import timedelta
import json
import math
from pathlib import Path

from .contracts import NetworkStateSnapshot
from .optional_sync import (
    MAX_SNAPSHOT_BYTES, MODEL_ID, TELEMETRY_KEY, SyncShadowRecord, SyncShadowWindow,
    _hash, _json, _time,
)


def _window(snapshot):
    if not isinstance(snapshot, NetworkStateSnapshot):
        raise ValueError("typed sandbox snapshot required")
    text = _json(snapshot.to_dict())
    if len(text.encode("utf-8")) > MAX_SNAPSHOT_BYTES:
        raise ValueError("snapshot exceeds 128 KiB")
    detached = NetworkStateSnapshot.from_dict(json.loads(text))
    window = SyncShadowWindow.from_dict(detached.telemetry.get(TELEMETRY_KEY))
    window.validate_context(detached)
    if not math.isfinite(window.samples[-1].offset_s * 1e9):
        raise ValueError("reference outside finite display range")
    return window, text


def _context(window):
    return (window.source_id, window.campaign_id, window.stream_id, window.node_id, window.clock_id)


class TemporalSyncEvaluation:
    """One explicit run and artifact. No training, commands or automatic persistence.

    Registration must precede receipt of any newer frame. Arrival order is local
    process evidence, not a signed timestamp or an independent preregistration.
    """
    def __init__(self, *, max_predictions=256):
        if type(max_predictions) is not int or not 1 <= max_predictions <= 4096:
            raise ValueError("max_predictions must be an integer in 1..4096")
        self.max_predictions = max_predictions
        self._rows = []
        self._context = None
        self._last_time = None
        self._last_text = None
        self._archive_sha256 = None
        self._observations = 0

    def observe(self, snapshot):
        window, text = _window(snapshot)
        context = _context(window)
        stamp = _time(window.samples[-1].timestamp_utc)
        if self._context is not None and context != self._context:
            raise ValueError("source/campaign/stream/node/clock changed; start a new evaluation")
        if self._last_time is not None and stamp <= self._last_time:
            if stamp == self._last_time and text == self._last_text:
                return False
            raise ValueError("observation must be newer; conflicting or out-of-order frame")
        updates = []
        for index, row in enumerate(self._rows):
            if row["status"] != "pending":
                continue
            target = _time(row["forecast"]["target_timestamp_utc"])
            if stamp < target:
                continue
            if stamp > target:
                updates.append((index, {"status": "missed_target"}))
                continue
            actual = window.samples[-1].offset_s
            prediction = row["forecast"]["evaluation"]
            model_error = abs(prediction["predicted_offset_s"] - actual)
            baseline_error = abs(prediction["persistence_offset_s"] - actual)
            if not all(math.isfinite(value * 1e9) for value in (model_error, baseline_error)):
                raise ValueError("scoring outside finite display range")
            updates.append((index, {
                "status": "paired", "reference_offset_s": actual,
                "reference_snapshot_json": text, "reference_snapshot_sha256": _hash(text),
                "reference_observation_index": self._observations + 1,
                "model_absolute_error_s": model_error, "persistence_absolute_error_s": baseline_error,
            }))
        # Validate first, mutate second: a rejected observation cannot partially score a run.
        for index, values in updates:
            self._rows[index].update(values)
        self._context, self._last_time, self._last_text = context, stamp, text
        self._observations += 1
        return True

    def register(self, record):
        if not isinstance(record, SyncShadowRecord) or record.status != "evaluated" or record.evaluation is None:
            raise ValueError("only completed shadow forecasts can be registered")
        evaluation = record.evaluation
        if record.snapshot_json != self._last_text or record.snapshot_sha256 != _hash(record.snapshot_json):
            raise ValueError("forecast must match the latest observed frame; late registration refused")
        snapshot = NetworkStateSnapshot.from_dict(json.loads(record.snapshot_json))
        window, _ = _window(snapshot)
        expected_input = window.model_input()
        target = _time(window.samples[-1].timestamp_utc) + timedelta(seconds=2)
        if (record.selected_model_id != MODEL_ID or evaluation.model_id != MODEL_ID
                or record.mode != "shadow" or record.physical_action_performed
                or record.simulator_action_performed or record.policy_activated
                or evaluation.physical_action_performed
                or evaluation.evidence != "simulated_offline"
                or evaluation.input_json != expected_input or evaluation.input_sha256 != _hash(expected_input)
                or evaluation.stream_id != window.stream_id
                or evaluation.target_timestamp_s != json.loads(expected_input)["timestamps_s"][-1] + 2.
                or _time(record.target_timestamp_utc) != target
                or evaluation.persistence_offset_s != window.samples[-1].offset_s
                or any(value is not None for value in (evaluation.reference_offset_s,
                           evaluation.model_absolute_error_s, evaluation.persistence_absolute_error_s))):
            raise ValueError("forecast contract mismatch or future reference contamination")
        for value in (evaluation.predicted_offset_s, evaluation.persistence_offset_s):
            if type(value) not in (int, float) or not math.isfinite(value * 1e9):
                raise ValueError("invalid prediction")
        digest = evaluation.archive_sha256
        if not isinstance(digest, str) or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            raise ValueError("invalid archive identity")
        if self._archive_sha256 is not None and digest != self._archive_sha256:
            raise ValueError("artifact changed; start a new evaluation")
        encoded = json.loads(record.serialized())
        for row in self._rows:
            if row["forecast"]["snapshot_sha256"] == record.snapshot_sha256:
                if row["forecast"] == encoded:
                    return False
                raise ValueError("conflicting forecast for the same frame")
        if len(self._rows) >= self.max_predictions:
            raise ValueError("evaluation capacity reached; export and start a new run")
        self._archive_sha256 = digest
        self._rows.append({"status": "pending", "committed_observation_index": self._observations,
                           "forecast": encoded})
        return True

    def report(self, *, include_records=True):
        paired = [row for row in self._rows if row["status"] == "paired"]
        model = [row["model_absolute_error_s"] for row in paired]
        baseline = [row["persistence_absolute_error_s"] for row in paired]
        count = len(paired)
        model_mae = math.fsum(model) / count if count else None
        baseline_mae = math.fsum(baseline) / count if count else None
        return {
            "schema": "orion.sync-temporal-evaluation.v1", "evidence": "simulated",
            "scope": "descriptive errors on paired artificial samples; not control gain or experimental validation",
            "pairing": "exact latest acquired timestamp, same context; no retrospective backfill",
            "context": dict(zip(("source_id", "campaign_id", "stream_id", "node_id", "clock_id"),
                                self._context)) if self._context else None,
            "archive_sha256": self._archive_sha256, "max_predictions": self.max_predictions,
            "observed_frames": self._observations, "registered_predictions": len(self._rows),
            "paired": count, "pending": sum(row["status"] == "pending" for row in self._rows),
            "missed_target": sum(row["status"] == "missed_target" for row in self._rows),
            "model_mae_s": model_mae, "persistence_mae_s": baseline_mae,
            "model_rmse_s": math.hypot(*model) / math.sqrt(count) if count else None,
            "persistence_rmse_s": math.hypot(*baseline) / math.sqrt(count) if count else None,
            "mae_delta_model_minus_persistence_s": model_mae - baseline_mae if count else None,
            "limitations": ["Overlapping windows are dependent; no p-value or generalization claim.",
                            "Frames without committed predictions do not enter error metrics; inspect coverage.",
                            "Arrival order is enforced in-process, not cryptographically timestamped.",
                            "No tuning, selection or training is performed by this evaluator."],
            "physical_action_performed": False, "policy_activated": False,
            **({"records": deepcopy(self._rows)} if include_records else {}),
        }

    def serialized(self):
        return json.dumps(self.report(), ensure_ascii=False, indent=2, allow_nan=False)


def export_temporal_report(text, path):
    """Export an already captured report; never overwrite inputs or prior runs."""
    with Path(path).open("xb") as stream:
        stream.write(text.encode("utf-8"))
