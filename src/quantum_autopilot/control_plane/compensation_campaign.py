"""Frozen descriptive SO(3) stress campaign. Never trains, selects or activates models."""
from dataclasses import replace
import hashlib
import json
import math
from pathlib import Path
import random
from types import SimpleNamespace

import numpy as np

from .compensation_temporal import TemporalCompensationEvaluation, angular_error, export_compensation_temporal_report
from .optional_compensation import MODEL_IDS, REGIMES, TELEMETRY_KEY, evaluate_optional_compensation
from .synthetic_compensation import SyntheticCompensationStream
from ..model_intake.artifacts import PINS
from ..model_intake.compensation import _exp_map, _log_map
from ..model_intake.sync_session import _constant, _unique

SCENARIOS = ("smooth", "rotation_noise", "thermal_reversal", "rotation_step", "packet_loss", "late_result")
PROTOCOL = {
    "schema": "orion.compensation-campaign-protocol.v1", "evidence": "simulated",
    "batches": 32, "samples_per_batch": 16, "period_s": .002, "horizon_s": .04,
    "seeds": list(range(100, 110)), "scenarios": list(SCENARIOS), "model_ids": list(MODEL_IDS),
    "baselines": ["persistence", "constant_velocity"],
    "noise_axis_std_rad": .002, "step_rad": .02, "transition_sample": 256,
    "missing_batches": [10, 11, 20, 24], "late_result_modulus": 4, "late_result_remainder": 1,
    "min_relative_mean_angle_reduction": .05, "min_seed_wins": 8, "min_target_coverage": .9,
    "interpretation": "descriptive engineering filters; not significance, physical domain certification or activation authority",
}


def batches(scenario, seed, model_id):
    if scenario not in SCENARIOS or model_id not in MODEL_IDS or seed not in PROTOCOL["seeds"] or type(seed) is not int:
        raise ValueError("prescribed scenario, seed and checkpoint required")
    point = model_id.split(".")[2]
    campaign = f"compensation-campaign-v1/{scenario}/{seed}"
    envelope = SimpleNamespace(input_kind="synthetic", campaign=campaign,
                               topology=SimpleNamespace(nodes=[SimpleNamespace(id="artificial-node")]))
    producer = SyntheticCompensationStream()
    for tick in range(PROTOCOL["batches"]):
        if scenario == "packet_loss" and tick in PROTOCOL["missing_batches"]:
            yield tick, None
            continue
        state = next(state for state in producer.push(envelope, seed, tick)
                     if state.metadata["compensation_operating_point"] == point)
        window = state.telemetry[TELEMETRY_KEY]
        for index, sample in enumerate(window["samples"]):
            absolute = tick * 16 + index
            time_s = absolute * .002
            correction = np.zeros(3)
            if scenario == "rotation_noise":
                rng = random.Random(f"compensation-noise-v1:{seed}:{absolute}")
                correction = np.array([rng.gauss(0., PROTOCOL["noise_axis_std_rad"]) for _ in range(3)])
            elif scenario == "rotation_step" and absolute >= PROTOCOL["transition_sample"]:
                correction[1] = PROTOCOL["step_rad"]
            elif scenario == "thermal_reversal":
                triangular_time = min(time_s, 2 * PROTOCOL["transition_sample"] * .002 - time_s)
                correction[2] = REGIMES[point][1] * (triangular_time - time_s)
                sample["temperature_c"] = 20. + .1 * triangular_time
                sample["temperature_rate_k_s"] = .1 if absolute < PROTOCOL["transition_sample"] else -.1
            sample["rotation"] = (_exp_map(correction) @ np.asarray(sample["rotation"])).tolist()
        state = replace(state, metadata={**state.metadata, "stress_scenario": scenario,
                                        "scope": "nominal checkpoint regime plus explicit artificial perturbations; "
                                                 "OOD is not estimated; no calibrated plant or photon noise; "
                                                 "reference is later observed rotation, not hidden noiseless truth"})
        yield tick, state


def constant_velocity(input_json):
    """Past-only mean left-invariant increment, extrapolated for 20 sample periods."""
    samples = json.loads(input_json)["samples"]
    rotations = [np.asarray(sample["rotation"]) for sample in samples]
    increments = [_log_map(current @ previous.T) for previous, current in zip(rotations, rotations[1:])]
    velocity = np.mean(increments, axis=0) / .002
    return (_exp_map(velocity * .04) @ rotations[-1]).tolist()


def run_case(archive, scenario, seed, model_id):
    tracker = TemporalCompensationEvaluation(max_predictions=32)
    deferred = None
    late_refused = acquired = 0
    baselines = {}
    for tick, state in batches(scenario, seed, model_id):
        if state is None:
            continue
        acquired += 1
        tracker.observe(state)
        if deferred is not None:
            try:
                tracker.register(deferred)
            except ValueError as exc:
                if "late registration" not in str(exc):
                    raise
                late_refused += 1
            else:
                raise ValueError("late forecast incorrectly admitted")
            deferred = None
        record = evaluate_optional_compensation(state, model_id=model_id, archive_path=archive)
        if record.status != "evaluated":
            raise ValueError(record.reason + ": " + record.detail)
        # Compute baseline now, before any reference arrives, even for late/discarded predictions.
        baseline = constant_velocity(json.loads(record.evaluation_json)["input_json"])
        if scenario == "late_result" and tick % PROTOCOL["late_result_modulus"] == PROTOCOL["late_result_remainder"]:
            deferred = record
        else:
            tracker.register(record)
            baselines[record.snapshot_sha256] = baseline
    report = tracker.report()
    errors = []
    for row in report["records"]:
        baseline = baselines[row["forecast"]["snapshot_sha256"]]
        row["constant_velocity_rotation"] = baseline
        if row["status"] == "paired":
            error = angular_error(baseline, row["reference_rotation"])
            row["constant_velocity_angular_error_rad"] = error
            errors.append(error)
    report.update({"scenario": scenario, "seed": seed, "emitted_batches": 32, "acquired_batches": acquired,
                   "late_results_refused": late_refused, "late_results_unresolved": int(deferred is not None),
                   "eligible_target_opportunities": 30, "target_coverage": report["paired"] / 30,
                   "constant_velocity_mean_angle_rad": math.fsum(errors) / len(errors) if errors else None,
                   "constant_velocity_rms_angle_rad": math.hypot(*errors) / math.sqrt(len(errors)) if errors else None})
    return report


def aggregate(runs):
    expected = {(scenario, seed, model) for scenario in SCENARIOS for seed in PROTOCOL["seeds"] for model in MODEL_IDS}
    actual = [(row["scenario"], row["seed"], row["model_id"]) for row in runs]
    if len(actual) != len(set(actual)) or set(actual) != expected:
        raise ValueError("all prescribed scenarios/seeds/checkpoints required exactly once")
    summaries = []
    for scenario in SCENARIOS:
        for model in MODEL_IDS:
            rows = sorted((row for row in runs if (row["scenario"], row["model_id"]) == (scenario, model)),
                          key=lambda row: row["seed"])
            keys = ["model_mean_angle_rad", "persistence_mean_angle_rad", "constant_velocity_mean_angle_rad", "target_coverage"]
            if any(type(row[key]) not in (int, float) or not math.isfinite(row[key]) or row[key] < 0
                   for row in rows for key in keys):
                raise ValueError("finite nonnegative scores and coverage required for every prescribed run")
            if any(row["target_coverage"] > 1 or any(row[key] > math.pi for key in keys[:-1]) for row in rows):
                raise ValueError("coverage must be <= 1 and angular errors <= pi")
            mean = math.fsum(row["model_mean_angle_rad"] for row in rows) / len(rows)
            comparisons = {}
            for baseline in PROTOCOL["baselines"]:
                values = [row[baseline + "_mean_angle_rad"] for row in rows]
                baseline_mean = math.fsum(values) / len(values)
                wins = sum(row["model_mean_angle_rad"] < value for row, value in zip(rows, values))
                ties = sum(row["model_mean_angle_rad"] == value for row, value in zip(rows, values))
                reduction = 1 - mean / baseline_mean if baseline_mean > 0 else None
                comparisons[baseline] = {"mean_seed_angle_rad": baseline_mean, "model_wins": wins, "ties": ties,
                                         "model_losses": len(rows) - wins - ties, "relative_mean_angle_reduction": reduction,
                                         "descriptive_filter_met": reduction is not None
                                         and reduction >= PROTOCOL["min_relative_mean_angle_reduction"]
                                         and wins >= PROTOCOL["min_seed_wins"]}
            coverage = min(row["target_coverage"] for row in rows)
            summaries.append({"scenario": scenario, "model_id": model, "mean_seed_model_angle_rad": mean,
                              "min_seed_model_angle_rad": min(row["model_mean_angle_rad"] for row in rows),
                              "max_seed_model_angle_rad": max(row["model_mean_angle_rad"] for row in rows),
                              "min_target_coverage": coverage, "comparisons": comparisons,
                              "descriptive_filter_met": coverage >= PROTOCOL["min_target_coverage"]
                              and all(value["descriptive_filter_met"] for value in comparisons.values())})
    return summaries


def run_campaign(archive, protocol_path, output, *, progress=None):
    with Path(protocol_path).open("rb") as stream:
        raw = stream.read(16385)
    if len(raw) > 16384:
        raise ValueError("protocol exceeds 16 KiB")
    parsed = json.loads(raw, object_pairs_hook=_unique, parse_constant=_constant)
    if json.dumps(parsed, sort_keys=True) != json.dumps(PROTOCOL, sort_keys=True):
        raise ValueError("protocol differs from frozen v1; create a separately reviewed version")
    output = Path(output)
    root = Path(__file__).resolve().parents[3]
    paths = ["src/quantum_autopilot/" + path for path in (
        "control_plane/compensation_campaign.py", "control_plane/compensation_temporal.py",
        "control_plane/optional_compensation.py", "control_plane/synthetic_compensation.py",
        "model_intake/compensation_session.py", "model_intake/compensation.py",
        "model_intake/compensation_evaluation.py", "model_intake/artifacts.py",
        "control_plane/optional_sync.py", "control_plane/contracts.py", "core/contracts.py", "core/__init__.py",
        "model_intake/catalog.py", "model_intake/inference.py", "model_intake/sync_session.py")]
    manifest = {"protocol": PROTOCOL, "protocol_file_sha256": hashlib.sha256(raw).hexdigest(),
                "expected_archive_sha256": PINS["qnet"],
                "source_sha256": {path: hashlib.sha256((root / path).read_bytes()).hexdigest() for path in paths},
                "scope": "artificial stress; no statistical significance, independent preregistration or physical validation"}
    output.mkdir(parents=False, exist_ok=False)
    export_compensation_temporal_report(json.dumps(manifest, indent=2), output / "frozen-protocol.json")
    runs, artifacts = [], []
    for scenario in SCENARIOS:
        for model_id in MODEL_IDS:
            for seed in PROTOCOL["seeds"]:
                report = run_case(archive, scenario, seed, model_id)
                text = json.dumps(report, indent=2, allow_nan=False)
                name = f"{scenario}-{model_id}-{seed}.json"
                export_compensation_temporal_report(text, output / name)
                artifacts.append({"file": name, "sha256": hashlib.sha256(text.encode()).hexdigest()})
                runs.append({key: value for key, value in report.items() if key != "records"})
            if progress:
                progress(f"Completed {scenario} / {model_id}: 10 seeds; no tuning.")
    identities = {row["archive_sha256"] for row in runs}
    if identities != {manifest["expected_archive_sha256"]}:
        raise ValueError("mixed artifacts in campaign")
    if any(hashlib.sha256((root / path).read_bytes()).hexdigest() != digest
           for path, digest in manifest["source_sha256"].items()):
        raise ValueError("source changed during campaign; no completion summary")
    summary = {"schema": "orion.compensation-campaign-results.v1", "status": "complete", "manifest": manifest,
               "archive_sha256": identities.pop(), "comparisons": aggregate(runs), "runs": runs, "artifacts": artifacts,
               "policy_activated": False, "physical_action_performed": False,
               "limitations": ["Descriptive engineering filters, not multiplicity-corrected hypothesis tests.",
                               "One short artificial family; nominal regime declarations do not certify OOD status.",
                               "Later observed rotations, not hidden noiseless truth or Bell fidelity.",
                               "Late-result schedule tests arrival order, not measured computation latency.",
                               "No closed-loop plant, photons, tuning or automatic model selection."]}
    export_compensation_temporal_report(json.dumps(summary, indent=2, allow_nan=False), output / "summary.json")
    return summary
