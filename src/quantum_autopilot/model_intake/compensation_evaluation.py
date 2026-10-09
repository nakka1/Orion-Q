"""Replay exported polarisation windows/streams; not a closed-loop experiment."""
from __future__ import annotations

import numpy as np

from .artifacts import COMPENSATION_ROOT, ReviewedArchive
from .compensation import CompensationReplayModel, HORIZON_S, PERIOD_S, WINDOW, _rotation

ATOL = 1e-9


def _angles(predicted, truth):
    residual = np.swapaxes(predicted, -1, -2) @ truth
    return np.arccos(np.clip((np.trace(residual, axis1=-2, axis2=-1) - 1) / 2, -1, 1))


def evaluate_compensation(archive: ReviewedArchive, operating_point: str) -> list[dict]:
    base = COMPENSATION_ROOT + operating_point + "/examples/"
    examples = archive.json(base + "examples.json")["examples"]
    stream = archive.arrays(base + "stream_seed500.npz", boolean_keys=frozenset({"truth_valid"}))
    time = stream["time_s"]
    n = len(time)
    if (n != 10000 or time.shape != (n,) or stream["horizon_s"].shape != ()
            or float(stream["horizon_s"]) != HORIZON_S
            or not np.allclose(np.diff(time), PERIOD_S, rtol=0, atol=1e-9)
            or stream["covariates"].shape != (n, 3)):
        raise ValueError("unexpected exported compensation stream")
    valid = stream["truth_valid"]
    if valid.shape != (n,) or valid.dtype.kind != "b":
        raise ValueError("invalid truth mask")
    # End-of-record truth is truncated in the export; never score those rows.
    expected_valid = time + HORIZON_S <= time[-1] + 1e-10
    if not np.array_equal(valid, expected_valid):
        raise ValueError("truth mask differs from recorded horizon coverage")
    truth = _rotation(stream["truth"], (n, 3, 3))
    measured = _rotation(stream["measured"], (n, 3, 3))
    persistence_errors = _angles(measured, truth)[valid]
    checks = []
    for architecture in ("ridge", "mlp"):
        model = CompensationReplayModel(archive, operating_point, architecture)
        stage_errors = {stage: 0.0 for stage in ("features", "scaled", "increment", "predicted")}
        for example in examples:
            times = float(example["time_s"]) - np.arange(WINDOW - 1, -1, -1) * PERIOD_S
            result = model.predict_window(times, example["input"]["history"], example["input"]["covariates"],
                                          domain="simulated_offline", operating_point=operating_point)
            for stage in stage_errors:
                got = np.asarray(getattr(result, stage))
                expected = np.asarray(example["expected"][architecture][stage]).reshape(got.shape)
                stage_errors[stage] = max(stage_errors[stage], float(np.max(np.abs(got - expected))))
        expected = _rotation(stream[f"expected_{architecture}"], (n, 3, 3))
        outputs = np.empty_like(expected)
        warmup = 0
        for i in range(n):
            result = model.predict(time[i], measured[i], stream["covariates"][i],
                                   domain="simulated_offline", operating_point=operating_point,
                                   stream_id=f"export/seed500/{operating_point}")
            outputs[i] = result.predicted
            warmup += result.status == "warmup_persistence"
        stream_error = float(np.max(np.abs(outputs - expected)))
        warmup_exact = np.array_equal(outputs[:WINDOW-1], measured[:WINDOW-1])
        errors = _angles(outputs, truth)[valid]
        checks.append({
            "model_id": f"qnet.compensation.{operating_point}.{architecture}",
            "windows": len(examples), "stream_predictions": n, "warmup_predictions": warmup,
            "warmup_matches_persistence": bool(warmup_exact),
            "max_stage_absolute_errors": stage_errors, "max_stream_absolute_error": stream_error,
            "absolute_tolerance": ATOL, "scored_predictions": int(valid.sum()),
            "excluded_truncated_horizons": int((~valid).sum()),
            "example_mean_error_mrad": float(1000 * errors.mean()),
            "example_p95_error_mrad": float(1000 * np.percentile(errors, 95)),
            "example_persistence_mean_error_mrad": float(1000 * persistence_errors.mean()),
            "passed": bool(len(examples) == 12 and max(stage_errors.values()) < ATOL
                           and stream_error < ATOL and warmup == WINDOW-1 and warmup_exact),
            "scope": "one exported simulated trajectory per regime, reused across architectures; "
                     "descriptive rotation error, not independent runs, QBER gain or a closed-loop benchmark",
        })
    return checks
