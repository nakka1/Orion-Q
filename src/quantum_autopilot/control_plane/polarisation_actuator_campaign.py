"""Frozen actuator campaign v1: the polarisation loop through the author's bounded device.

The closed-loop campaigns assume an ideal actuator. This one puts the author's
retarder cascade (``qnet_network.compensation.actuator``: X-Y-X elements, finite
slew, 4π of travel, endless reset) between the supervisor and the fibre, and
sweeps the slew rate from a motorised waveplate (30 rad/s) to a lithium-niobate
controller (10 000 rad/s) on both operating points.

Gate first: at the development point, seeds 500-503, the loop must reproduce the
author's own slew sweep (``external/qnet_network/runs/actuator/slew_sweep.csv``)
for all four compensators and nine device settings. Then, on the 20 evaluation
seeds of the closed-loop campaigns, each learned checkpoint is compared with an
alpha-beta filter retuned for every device (the author kept one tuning for all),
with exact two-sided paired sign-flip tests, Holm over every comparison.
"""
from __future__ import annotations

import csv
import hashlib
import io
import itertools
import json
import math
from pathlib import Path

import numpy as np

from qnet_network.compensation.actuator import ActuatorConfig, PolarisationActuator

from . import polarisation_loop as loop
from . import polarisation_plant as plant
from .comparative_evaluation import holm_adjust, paired_sign_flip_test
from ..model_intake.artifacts import PINS, ReviewedArchive
from ..model_intake.sync_session import _constant, _unique

REFERENCE = "external/qnet_network/runs/actuator/slew_sweep.csv"
REFERENCE_NAMES = {"persistência": "baseline.persistence", "filtro alfa-beta": "baseline.alpha_beta",
                   "ridge (Wiener)": "qnet.compensation.desenvolvimento.ridge", "MLP": "qnet.compensation.desenvolvimento.mlp"}
SLEWS = [30.0, 60.0, 120.0, 250.0, 500.0, 1000.0, 3000.0, 10000.0]
PROTOCOL = {
    "schema": "orion.polarisation-actuator-protocol.v1", "target_id": loop.TARGET_ID,
    "evidence": "simulated", "mode": "simulation",
    "actuator": {"model": "qnet_network.compensation.actuator.PolarisationActuator", "range_rad": 4 * math.pi,
                 "reset_slew_multiplier": 3.0},
    "slew_rates_rad_per_s": [None, *SLEWS], "slew_note": "null = ideal actuator (no device in the loop)",
    "operating_points": {
        "desenvolvimento": ["qnet.compensation.desenvolvimento.ridge", "qnet.compensation.desenvolvimento.mlp"],
        "calibrado": ["qnet.compensation.calibrado.ridge", "qnet.compensation.calibrado.mlp"]},
    "duration_s": 20.0, "evaluation_plant_seeds": list(range(1000, 1020)), "monitoring_seed_offset": 6000,
    "baselines": ["baseline.alpha_beta", "baseline.none"],
    "alpha_beta_grid": {"alpha": [0.01, 0.02, 0.03, 0.05, 0.1, 0.2, 0.5, 1.0], "beta": [0.0, 0.001, 0.005, 0.02]},
    "tuning_plant_seeds": [100, 101, 102, 103], "tuning_monitoring_seeds": [7, 8, 9, 10], "tuning_duration_s": 10.0,
    "tuning_rule": "alpha-beta retuned for every operating point and every device",
    "supervisor": {"max_command_step_rad": math.pi / 2, "max_consecutive_faults": 8, "rotation_tolerance": 1e-7},
    "reproduction": {"reference": REFERENCE, "reference_sha256": "ca4943155e2b462e738c6ee02ed3844254499abade411f5e2960c12eaea51d0d",
                     "operating_point": "desenvolvimento", "plant_seeds": [500, 501, 502, 503],
                     "monitoring_seeds": [7000, 7001, 7002, 7003], "alpha_beta": {"alpha": 0.05, "beta": 0.005},
                     "compensators": REFERENCE_NAMES, "tolerance": 1e-9,
                     "author_script": "external/qnet_network/scripts/evaluate_actuator.py (slew sweep)"},
    "primary_endpoint": "per-seed mean QBER over the whole run",
    "test": "exact two-sided paired sign-flip test, learned checkpoint vs retuned alpha-beta, per point and device",
    "multiplicity": "Holm over all learned-versus-alpha-beta comparisons", "alpha": 0.05,
    "bootstrap": {"resamples": 10000, "seed": 20261022, "confidence": 0.95},
    "interpretation": "simulated device model from the author's package; not a characterised physical controller",
}
SOURCES = tuple(f"quantum_autopilot/control_plane/{name}" for name in (
    "polarisation_actuator_campaign.py", "polarisation_loop.py", "polarisation_plant.py",
    "comparative_evaluation.py")) + tuple(f"quantum_autopilot/model_intake/{name}" for name in (
        "compensation.py", "artifacts.py", "catalog.py", "inference.py")) + (
    "qnet_network/compensation/actuator.py",)


def load_protocol(path):
    with Path(path).open("rb") as stream:
        raw = stream.read(16385)
    if len(raw) > 16384:
        raise ValueError("protocol exceeds 16 KiB")
    parsed = json.loads(raw, object_pairs_hook=_unique, parse_constant=_constant)
    if json.dumps(parsed, sort_keys=True) != json.dumps(PROTOCOL, sort_keys=True):
        raise ValueError("protocol differs from frozen v1; create a separately reviewed version")
    return raw, PROTOCOL


def _device(slew, protocol=PROTOCOL):
    if slew is None:
        return None
    config = protocol["actuator"]
    return PolarisationActuator(ActuatorConfig(range_rad=config["range_rad"], slew_rate_rad_per_s=float(slew),
                                               reset_slew_multiplier=config["reset_slew_multiplier"]))


def _supervisor(protocol=PROTOCOL):
    return loop.PolarisationSupervisor(loop.PolarisationSupervisorConfig(**protocol["supervisor"]))


def _controller(controller_id, archive, tuned=None):
    if controller_id == "baseline.none":
        return loop.NoCompensation()
    if controller_id == "baseline.persistence":
        return loop.Persistence()
    if controller_id == "baseline.alpha_beta":
        return loop.AlphaBeta(tuned["alpha"], tuned["beta"])
    return loop.LearnedCheckpoint(controller_id, archive)


def _run(trajectory, controller, point, seed, monitor, slew, protocol=PROTOCOL, fallback=None, archive_sha256=None):
    return loop.run_loop(trajectory, controller, monitoring_seed=int(monitor), operating_point=point, plant_seed=int(seed),
                         supervisor=_supervisor(protocol), fallback=fallback, actuator=_device(slew, protocol),
                         archive_sha256=archive_sha256)


def read_reference(root, protocol=PROTOCOL):
    config = protocol["reproduction"]
    raw = (Path(root) / config["reference"]).read_bytes()
    if hashlib.sha256(raw).hexdigest() != config["reference_sha256"]:
        raise ValueError("author's slew-sweep record differs from the vendored reference")
    table = {}
    for row in csv.DictReader(io.StringIO(raw.decode("utf-8"))):
        slew = None if row["slew_rate"] == "inf" else float(row["slew_rate"])
        table[(slew, config["compensators"][row["compensator"]])] = {
            "mean_qber": float(row["mean_qber"]), "travel_rad": float(row["actuator_travel_rad"]),
            "resets": float(row["actuator_resets"])}
    return table


def reproduce(archive, root, protocol=PROTOCOL, progress=None):
    """The author's slew sweep, regenerated through the ORION-Q loop, to the declared tolerance."""
    config = protocol["reproduction"]
    reference = read_reference(root, protocol)
    runs = [plant.simulate(plant.OPERATING_POINTS[config["operating_point"]], protocol["duration_s"], seed)
            for seed in config["plant_seeds"]]
    rows = []
    for slew in protocol["slew_rates_rad_per_s"]:
        for controller_id in config["compensators"].values():
            controller = _controller(controller_id, archive, config["alpha_beta"])
            records = [_run(run, controller, config["operating_point"], seed, monitor, slew, protocol)
                       for run, seed, monitor in zip(runs, config["plant_seeds"], config["monitoring_seeds"])]
            value = math.fsum(record.summary["mean_qber"] for record in records) / len(records)
            travel = (math.fsum(record.actuator["travel_rad"] for record in records) / len(records)
                      if slew is not None else 0.0)
            expected = reference[(slew, controller_id)]
            rows.append({"slew_rate_rad_per_s": slew, "controller_id": controller_id, "mean_qber": value,
                         "author_mean_qber": expected["mean_qber"], "absolute_difference": abs(value - expected["mean_qber"]),
                         "travel_rad": travel, "author_travel_rad": expected["travel_rad"],
                         "passed": abs(value - expected["mean_qber"]) <= config["tolerance"]
                         and abs(travel - expected["travel_rad"]) <= 1e-6 * max(1.0, expected["travel_rad"])})
        if progress:
            progress(f"Reproduction at {'ideal' if slew is None else f'{slew:g} rad/s'}: "
                     f"{sum(row['passed'] for row in rows[-4:])}/4 compensators match the author's record.")
    if not all(row["passed"] for row in rows):
        raise ValueError("loop with the bounded actuator does not reproduce the author's slew sweep; no campaign")
    return rows


def tune(point, slew, protocol=PROTOCOL):
    runs = [plant.simulate(plant.OPERATING_POINTS[point], protocol["tuning_duration_s"], seed)
            for seed in protocol["tuning_plant_seeds"]]
    grid = []
    for alpha, beta in itertools.product(protocol["alpha_beta_grid"]["alpha"], protocol["alpha_beta_grid"]["beta"]):
        scores = [_run(run, loop.AlphaBeta(alpha, beta), point, seed, monitor, slew, protocol).summary["mean_qber"]
                  for run, seed, monitor in zip(runs, protocol["tuning_plant_seeds"], protocol["tuning_monitoring_seeds"])]
        grid.append({"alpha": alpha, "beta": beta, "mean_qber": math.fsum(scores) / len(scores)})
    best = min(grid, key=lambda row: (row["mean_qber"], row["alpha"], row["beta"]))
    alphas, betas = protocol["alpha_beta_grid"]["alpha"], protocol["alpha_beta_grid"]["beta"]
    return {"operating_point": point, "slew_rate_rad_per_s": slew, "selected": best, "grid": grid,
            "alpha_on_grid_edge": best["alpha"] in (alphas[0], alphas[-1]),
            "beta_on_grid_edge": best["beta"] in (betas[0], betas[-1])}


def statistics(per_seed, protocol=PROTOCOL):
    """``per_seed[(point, slew, controller_id)]`` -> list of mean QBER; two-sided tests, Holm over all."""
    generator = np.random.default_rng(protocol["bootstrap"]["seed"])
    config = protocol["bootstrap"]
    lower = (1 - config["confidence"]) / 2
    rows = []
    for point, models in protocol["operating_points"].items():
        for slew in protocol["slew_rates_rad_per_s"]:
            baseline = np.asarray(per_seed[(point, slew, "baseline.alpha_beta")], dtype=float)
            for model_id in models:
                learned = np.asarray(per_seed[(point, slew, model_id)], dtype=float)
                test = paired_sign_flip_test(baseline - learned, alternative="two-sided")
                draws = generator.integers(0, len(learned), size=(config["resamples"], len(learned)))
                ratios = baseline[draws].mean(axis=1) / learned[draws].mean(axis=1)
                rows.append({"operating_point": point, "slew_rate_rad_per_s": slew, "model_id": model_id,
                             "baseline": "baseline.alpha_beta", "n_seeds": len(learned),
                             "model_mean_qber": float(learned.mean()), "baseline_mean_qber": float(baseline.mean()),
                             "baseline_to_model_qber_ratio": float(baseline.mean() / learned.mean()),
                             "ratio_ci95": [float(np.quantile(ratios, lower)), float(np.quantile(ratios, 1 - lower))],
                             "model_wins": int(np.sum(learned < baseline)), "p_value": test["p_value"],
                             "method": test["method"]})
    for row, value in zip(rows, holm_adjust([row["p_value"] for row in rows])):
        row["holm_p_value"] = float(value)
        row["verdict"] = ("learned_better" if value < protocol["alpha"] and row["baseline_to_model_qber_ratio"] > 1
                          else "alpha_beta_better" if value < protocol["alpha"] else "no_significant_difference")
    return rows


def _export(text, path):
    with Path(path).open("xb") as stream:
        stream.write(text.encode("utf-8"))


def _hashes(root):
    return {path: hashlib.sha256((root / "src" / path).read_bytes()).hexdigest() for path in SOURCES}


def evaluate_block(archive, point, slew, protocol=PROTOCOL):
    """Tune the alpha-beta for one device, then run every controller on every evaluation seed."""
    tuned = tune(point, slew, protocol)
    selected = tuned["selected"]
    models = protocol["operating_points"][point]
    controllers = {controller_id: _controller(controller_id, archive, selected)
                   for controller_id in [*models, *protocol["baselines"]]}
    per_seed, records = {}, []
    for seed in protocol["evaluation_plant_seeds"]:
        trajectory = plant.simulate(plant.OPERATING_POINTS[point], protocol["duration_s"], seed)
        for controller_id, controller in controllers.items():
            learned = controller_id in models
            record = _run(trajectory, controller, point, seed, seed + protocol["monitoring_seed_offset"], slew,
                          protocol, fallback=loop.AlphaBeta(selected["alpha"], selected["beta"]) if learned else None,
                          archive_sha256=archive.sha256 if learned else None)
            per_seed.setdefault(controller_id, []).append(record.summary["mean_qber"])
            records.append({"operating_point": point, "slew_rate_rad_per_s": slew, "controller_id": controller_id,
                            "plant_seed": seed, "summary": record.summary, "supervisor": record.supervisor,
                            "actuator": record.actuator, "alpha_beta": selected})
    return tuned, per_seed, records


def _evaluate_block(args):
    return evaluate_block(*args)


def _report(progress, block, result):
    _, point, slew, _ = block
    tuned, values, _ = result
    selected = tuned["selected"]
    learned = ", ".join(f"{model_id.split('.')[-1]} {np.mean(values[model_id]):.4f}"
                        for model_id in block[3]["operating_points"][point])
    progress(f"{point} {'ideal' if slew is None else f'{slew:g} rad/s'}: alpha-beta "
             f"({selected['alpha']:g}, {selected['beta']:g}) {np.mean(values['baseline.alpha_beta']):.4f}; {learned}")
    return result


def run_campaign(archive_path, protocol_path, output, *, progress=None, workers=1):
    raw, protocol = load_protocol(protocol_path)
    root = Path(__file__).resolve().parents[3]
    archive = ReviewedArchive(archive_path, "qnet")
    manifest = {"protocol": protocol, "protocol_file_sha256": hashlib.sha256(raw).hexdigest(),
                "archive_sha256": archive.sha256, "expected_archive_sha256": PINS["qnet"],
                "source_sha256": _hashes(root)}
    output = Path(output)
    output.mkdir(parents=False, exist_ok=False)
    _export(json.dumps(manifest, indent=2, allow_nan=False), output / "frozen-protocol.json")

    for point, models in protocol["operating_points"].items():
        decisions = [loop.check_checkpoint(model_id, archive, point) for model_id in models]
        if any(decision.status != "ADMITTED" for decision in decisions):
            raise ValueError(f"contract refused a checkpoint on the {point} plant")
    reproduction = reproduce(archive, root, protocol, progress)

    tuning, per_seed, records = [], {}, []
    blocks = [(archive, point, slew, protocol) for point in protocol["operating_points"]
              for slew in protocol["slew_rates_rad_per_s"]]
    if workers > 1:
        from concurrent.futures import ProcessPoolExecutor

        with ProcessPoolExecutor(max_workers=workers) as pool:
            results = pool.map(_evaluate_block, blocks)
            outcomes = list(zip(blocks, results)) if progress is None else [
                (block, _report(progress, block, result)) for block, result in zip(blocks, results)]
    else:
        outcomes = [(block, _report(progress, block, evaluate_block(*block)) if progress else evaluate_block(*block))
                    for block in blocks]
    for (_, point, slew, _), (tuned, values, rows) in outcomes:
        tuning.append(tuned)
        records.extend(rows)
        for controller_id, scores in values.items():
            per_seed[(point, slew, controller_id)] = scores

    text = "".join(json.dumps(row, allow_nan=False, sort_keys=True) + "\n" for row in records)
    _export(text, output / "actuator-runs.jsonl")
    _export(json.dumps(tuning, indent=2, allow_nan=False), output / "alpha-beta-tuning.json")
    if _hashes(root) != manifest["source_sha256"]:
        raise ValueError("source changed during campaign; no completion summary")
    comparisons = statistics(per_seed, protocol)
    device = {}
    for row in records:
        if row["actuator"] is None:
            continue
        key = f"{row['operating_point']}|{row['slew_rate_rad_per_s']:g}|{row['controller_id']}"
        entry = device.setdefault(key, {"travel_rad": [], "resets_per_minute": [], "resetting_fraction": []})
        entry["travel_rad"].append(row["actuator"]["travel_rad"])
        entry["resets_per_minute"].append(row["actuator"]["resets"] * 60.0 / protocol["duration_s"])
        entry["resetting_fraction"].append(row["actuator"]["resetting_fraction"])
    summary = {
        "schema": "orion.polarisation-actuator-results.v1", "status": "complete", "manifest": manifest,
        "reproduction": reproduction,
        "alpha_beta": [{key: row[key] for key in ("operating_point", "slew_rate_rad_per_s", "selected",
                                                   "alpha_on_grid_edge", "beta_on_grid_edge")} for row in tuning],
        "mean_qber": {f"{point}|{'ideal' if slew is None else f'{slew:g}'}|{controller_id}": float(np.mean(values))
                      for (point, slew, controller_id), values in per_seed.items()},
        "comparisons": comparisons,
        "device": {key: {name: float(np.mean(values)) for name, values in entry.items()} for key, entry in device.items()},
        "supervisor": {"rate_limited": sum(row["supervisor"]["rate_limited"] for row in records),
                       "faults": sum(row["supervisor"]["faults"] for row in records),
                       "degraded_runs": sum(row["supervisor"]["degraded_at_s"] is not None for row in records)},
        "runs": len(records), "artifacts": [{"file": "actuator-runs.jsonl", "sha256": hashlib.sha256(text.encode()).hexdigest()}],
        "policy_activated": False, "physical_action_performed": False, "simulator_action_performed": True,
        "limitations": ["Device model is the author's simulated retarder cascade, not a characterised controller.",
                        "Learned checkpoints were trained with an ideal actuator; they are not retrained per device.",
                        "20 s runs: resets that need long drift accumulation are rare at 4π of travel."],
    }
    _export(json.dumps(summary, indent=2, allow_nan=False), output / "summary.json")
    return summary


__all__ = ["PROTOCOL", "load_protocol", "reproduce", "run_campaign", "statistics", "tune"]
