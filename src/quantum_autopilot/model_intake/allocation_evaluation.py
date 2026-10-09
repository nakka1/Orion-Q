"""Reproduce allocation exports, not throughput or finite-key experiments."""
import numpy as np

from .allocation import AllocationObservation, AllocationReplayModel
from .artifacts import ALLOCATION_ROOT, ReviewedArchive

ATOL = 1e-12
FIELDS = tuple(AllocationObservation.__dataclass_fields__)


def _compare(result, expected, errors):
    for name in errors:
        got, reference = np.asarray(getattr(result, name)), np.asarray(expected[name], dtype=float)
        if got.shape != reference.shape or not np.isfinite(reference).all():
            raise ValueError("invalid allocation reference shape/value")
        errors[name] = max(errors[name], float(np.max(np.abs(got-reference))))


def evaluate_allocation(archive: ReviewedArchive) -> list[dict]:
    base = ALLOCATION_ROOT + "examples/"
    examples = archive.json(base + "examples.json")
    if examples["coverage_target"] != 1.:
        raise ValueError("unsupported example coverage target")
    checks = []
    for variant in ("persistence", "ridge"):
        model = AllocationReplayModel(archive, variant)
        options = {"domain": "simulated_offline", "protocol": "single_photon_bb84",
                   "link_names": tuple(examples["links"])}
        stage_errors = {"drift": 0., "forecast": 0., "admission": 0.}
        snapshots = examples["examples"][variant]
        for example in snapshots:
            observation = AllocationObservation(**example["observation"])
            history = np.asarray(example["drift_history"], dtype=float).reshape(-1, 11)
            times = observation.time_s - np.arange(len(history), 0, -1) * 5.
            result = model.recommend_snapshot(observation, drift_history=history, history_times_s=times, **options)
            _compare(result, example["expected"], stage_errors)
        total_steps = warmup = mismatches = 0
        for seed in (500, 501):
            stream = archive.arrays(base + f"stream_{variant}_s{seed}.npz",
                                    positive_infinity_keys=frozenset({"urgency_s"}))
            if stream["time_s"].shape != (360,) or not np.array_equal(stream["time_s"], np.arange(360)*5.):
                raise ValueError("unexpected allocation stream time axis")
            model.reset()
            for step in range(360):
                observation = AllocationObservation(**{name: stream[name][step] for name in FIELDS})
                result = model.recommend(observation, stream_id=f"{variant}/{seed}", **options)
                expected = {name: stream[name][step] for name in stage_errors}
                _compare(result, expected, stage_errors)
                mismatches += int(np.count_nonzero(np.asarray(result.admission) != expected["admission"]))
                warmup += result.status == "warmup_persistence"
                total_steps += 1
        checks.append({
            "model_id": f"qnet.allocator.{variant}", "learned_forecast": variant == "ridge",
            "snapshots": len(snapshots), "streams": 2, "seeds": [500, 501], "stream_steps": total_steps,
            "links_per_step": 11, "link_admission_mismatches": mismatches, "warmup_steps": warmup,
            "max_stage_absolute_errors": stage_errors, "absolute_tolerance": ATOL,
            "passed": bool(len(snapshots) == 10 and total_steps == 720 and max(stage_errors.values()) < ATOL
                           and mismatches == 0 and warmup == (10 if variant == "ridge" else 0)),
            "scope": "replay of supplied observations and pending queues; no regenerated environment, "
                     "served keys, classical throughput or independent validation of operational gain",
        })
    return checks
