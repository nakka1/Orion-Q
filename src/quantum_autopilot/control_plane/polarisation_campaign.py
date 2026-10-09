"""Frozen closed-loop polarisation campaign v1. Tunes only the classical baseline; never trains models.

Protocol (``configs/optional_models/polarisation_closed_loop_v1.json``, frozen
below): the calibrated aerial plant, 20 new plant seeds (1000-1019) that no
checkpoint was trained, validated or tested on, the two calibrated checkpoints
against three baselines, the alpha-beta filter retuned on separate seeds, and
an exact one-sided paired sign-flip test per comparison with Holm correction.
Before anything is scored the run must reproduce, from the same archive, the
held-out QBER each checkpoint's own metadata declares; a campaign whose plant
or replay path drifted from the author's therefore stops instead of reporting.
"""
from __future__ import annotations

from dataclasses import asdict
import hashlib
import itertools
import json
import math
from pathlib import Path

import numpy as np

from . import polarisation_loop as loop
from . import polarisation_plant as plant
from .comparative_evaluation import holm_adjust, paired_sign_flip_test
from ..model_intake.artifacts import COMPENSATION_ROOT, PINS, ReviewedArchive
from ..model_intake.sync_session import _constant, _unique

LEARNED = ["qnet.compensation.calibrado.ridge", "qnet.compensation.calibrado.mlp"]
PROTOCOL = {
    "schema": "orion.polarisation-closed-loop-protocol.v1", "target_id": loop.TARGET_ID,
    "evidence": "simulated", "mode": "simulation", "operating_point": "calibrado",
    "duration_s": 20.0, "sample_interval_s": plant.SAMPLE_INTERVAL_S, "control_period_s": plant.CONTROL_PERIOD_S,
    "latency_s": plant.LATENCY_S, "monitoring_photons_per_basis": plant.PHOTONS_PER_BASIS,
    "detector_efficiency": plant.DETECTOR_EFFICIENCY, "sifted_rate_hz": plant.SIFTED_RATE_HZ,
    "evaluation_plant_seeds": list(range(1000, 1020)), "monitoring_seed_offset": 6000,
    "learned_model_ids": LEARNED,
    "contract_probe_model_ids": ["qnet.compensation.desenvolvimento.ridge", "qnet.compensation.desenvolvimento.mlp"],
    "baselines": list(loop.BASELINE_IDS),
    "alpha_beta_grid": {"alpha": [0.005, 0.01, 0.02, 0.03, 0.05, 0.1, 0.2, 0.35, 0.5, 0.7, 1.0],
                        "beta": [0.0, 0.0005, 0.001, 0.002, 0.005, 0.02, 0.05, 0.1]},
    "tuning_plant_seeds": [100, 101, 102, 103], "tuning_monitoring_seeds": [7, 8, 9, 10], "tuning_duration_s": 10.0,
    "supervisor": {"max_command_step_rad": math.pi / 2, "max_consecutive_faults": 8, "rotation_tolerance": 1e-7},
    "fallback": "baseline.alpha_beta",
    "reproduction": {"plant_seeds": [500, 501, 502, 503, 504, 505], "monitoring_seeds": [7000, 7001, 7002, 7003, 7004, 7005],
                     "tolerance": 1e-9, "reference": "held_out_mean_qber.estimate in each checkpoint's metadata"},
    "fault_injection": {"model_id": LEARNED[0], "start_step": 2500, "steps": 50},
    "primary_endpoint": "per-seed mean QBER over the whole run",
    "test": "exact one-sided paired sign-flip test, H1: learned mean QBER lower than baseline",
    "multiplicity": "Holm over the six learned-versus-baseline comparisons", "alpha": 0.05,
    "bootstrap": {"resamples": 10000, "seed": 20261022, "confidence": 0.95},
    "interpretation": "simulated closed loop with an ideal actuator; not physical validation or permission to actuate",
}
#: The same frozen design on the development point (40 Hz, 0.05 rad/s), with its own checkpoints.
#: The reproduction gate uses the author's eight held-out seeds, as the checkpoint metadata declares.
PROTOCOL_DEV = json.loads(json.dumps(PROTOCOL))
PROTOCOL_DEV.update(
    schema="orion.polarisation-closed-loop-protocol.v1.desenvolvimento", operating_point="desenvolvimento",
    learned_model_ids=["qnet.compensation.desenvolvimento.ridge", "qnet.compensation.desenvolvimento.mlp"],
    contract_probe_model_ids=["qnet.compensation.calibrado.ridge", "qnet.compensation.calibrado.mlp"],
    reproduction=dict(PROTOCOL["reproduction"], plant_seeds=list(range(500, 508)),
                      monitoring_seeds=list(range(7000, 7008))),
    fault_injection=dict(PROTOCOL["fault_injection"], model_id="qnet.compensation.desenvolvimento.ridge"))
PROTOCOLS = {"calibrado": PROTOCOL, "desenvolvimento": PROTOCOL_DEV}
SOURCES = ("control_plane/polarisation_campaign.py", "control_plane/polarisation_loop.py",
           "control_plane/polarisation_plant.py", "control_plane/comparative_evaluation.py",
           "model_intake/compensation.py", "model_intake/artifacts.py", "model_intake/catalog.py",
           "model_intake/inference.py", "model_intake/sync_session.py")


def load_protocol(path):
    """Return the raw bytes and the frozen protocol they spell; anything else is refused."""
    with Path(path).open("rb") as stream:
        raw = stream.read(16385)
    if len(raw) > 16384:
        raise ValueError("protocol exceeds 16 KiB")
    parsed = json.loads(raw, object_pairs_hook=_unique, parse_constant=_constant)
    for candidate in PROTOCOLS.values():
        if json.dumps(parsed, sort_keys=True) == json.dumps(candidate, sort_keys=True):
            return raw, candidate
    raise ValueError("protocol differs from frozen v1; create a separately reviewed version")


def _supervisor(protocol):
    return loop.PolarisationSupervisor(loop.PolarisationSupervisorConfig(**protocol["supervisor"]))


def _parameters(protocol):
    return plant.OPERATING_POINTS[protocol["operating_point"]]


def tune_alpha_beta(progress=None, protocol=PROTOCOL):
    """Grid search on the tuning seeds by closed-loop mean QBER, as the author tuned it."""
    runs = [plant.simulate(_parameters(protocol), protocol["tuning_duration_s"], seed) for seed in protocol["tuning_plant_seeds"]]
    grid = []
    for alpha, beta in itertools.product(protocol["alpha_beta_grid"]["alpha"], protocol["alpha_beta_grid"]["beta"]):
        scores = [loop.run_loop(run, loop.AlphaBeta(alpha, beta), monitoring_seed=monitor, plant_seed=seed,
                                operating_point=protocol["operating_point"], supervisor=_supervisor(protocol)).summary["mean_qber"]
                  for run, seed, monitor in zip(runs, protocol["tuning_plant_seeds"], protocol["tuning_monitoring_seeds"])]
        grid.append({"alpha": alpha, "beta": beta, "mean_qber": math.fsum(scores) / len(scores)})
    best = min(grid, key=lambda row: (row["mean_qber"], row["alpha"], row["beta"]))
    alphas, betas = protocol["alpha_beta_grid"]["alpha"], protocol["alpha_beta_grid"]["beta"]
    if progress:
        progress(f"alpha-beta tuned: alpha={best['alpha']:g}, beta={best['beta']:g}")
    return {"selected": best, "grid": grid,
            "alpha_on_grid_edge": best["alpha"] in (alphas[0], alphas[-1]),
            "beta_on_grid_edge": best["beta"] in (betas[0], betas[-1])}


def _controller(controller_id, archive, tuned):
    if controller_id == "baseline.none":
        return loop.NoCompensation()
    if controller_id == "baseline.persistence":
        return loop.Persistence()
    if controller_id == "baseline.alpha_beta":
        return loop.AlphaBeta(tuned["alpha"], tuned["beta"])
    return loop.LearnedCheckpoint(controller_id, archive)


def reproduce(archive, protocol=PROTOCOL):
    """Each admitted checkpoint must reproduce its own declared held-out QBER before scoring."""
    config = protocol["reproduction"]
    runs = [plant.simulate(_parameters(protocol), protocol["duration_s"], seed) for seed in config["plant_seeds"]]
    rows = []
    for model_id in protocol["learned_model_ids"]:
        _, _, point, kind = model_id.split(".")
        declared = archive.json(f"{COMPENSATION_ROOT}{point}/model/{kind}.json")["held_out_mean_qber"]
        if declared.get("n_seeds") != len(runs):
            raise ValueError("declared reproduction seeds differ from protocol")
        controller = loop.LearnedCheckpoint(model_id, archive)
        scores = [loop.run_loop(run, controller, monitoring_seed=monitor, plant_seed=seed, supervisor=_supervisor(protocol),
                                operating_point=protocol["operating_point"]).summary["mean_qber"]
                  for run, seed, monitor in zip(runs, config["plant_seeds"], config["monitoring_seeds"])]
        value = math.fsum(scores) / len(scores)
        rows.append({"model_id": model_id, "declared_mean_qber": declared["estimate"], "reproduced_mean_qber": value,
                     "absolute_difference": abs(value - declared["estimate"]),
                     "passed": abs(value - declared["estimate"]) <= config["tolerance"]})
    if not all(row["passed"] for row in rows):
        raise ValueError("replay does not reproduce the checkpoint's declared held-out QBER; no campaign")
    return rows


def _bootstrap(values, generator, protocol):
    config = protocol["bootstrap"]
    values = np.asarray(values, dtype=float)
    draws = generator.integers(0, len(values), size=(config["resamples"], len(values)))
    lower = (1 - config["confidence"]) / 2
    return values[draws], lower


def statistics(per_seed, protocol=PROTOCOL):
    """Paired comparisons of the primary endpoint, Holm-corrected, with bootstrap intervals."""
    generator = np.random.default_rng(protocol["bootstrap"]["seed"])
    controllers = {}
    for controller_id, values in per_seed.items():
        samples, lower = _bootstrap(values, generator, protocol)
        means = samples.mean(axis=1)
        controllers[controller_id] = {"mean_qber": float(np.mean(values)),
                                      "ci95": [float(np.quantile(means, lower)), float(np.quantile(means, 1 - lower))],
                                      "min": float(np.min(values)), "max": float(np.max(values))}
    comparisons = []
    for model_id in protocol["learned_model_ids"]:
        for baseline in protocol["baselines"]:
            learned, reference = np.asarray(per_seed[model_id]), np.asarray(per_seed[baseline])
            test = paired_sign_flip_test(reference - learned, alternative="greater")
            draws = generator.integers(0, len(learned), size=(protocol["bootstrap"]["resamples"], len(learned)))
            ratios = reference[draws].mean(axis=1) / learned[draws].mean(axis=1)
            lower = (1 - protocol["bootstrap"]["confidence"]) / 2
            comparisons.append({"model_id": model_id, "baseline": baseline, "wins": int(np.sum(learned < reference)),
                                "ties": int(np.sum(learned == reference)), "n_seeds": len(learned),
                                "baseline_to_model_qber_ratio": float(reference.mean() / learned.mean()),
                                "ratio_ci95": [float(np.quantile(ratios, lower)), float(np.quantile(ratios, 1 - lower))],
                                "mean_qber_reduction": test["mean_difference"], "p_value": test["p_value"],
                                "method": test["method"]})
    adjusted = holm_adjust([row["p_value"] for row in comparisons])
    for row, value in zip(comparisons, adjusted):
        row["holm_p_value"] = float(value)
        row["significant"] = bool(value < protocol["alpha"])
    return controllers, comparisons


def _export(text, path):
    with Path(path).open("xb") as stream:
        stream.write(text.encode("utf-8"))


def run_campaign(archive_path, protocol_path, output, *, progress=None):
    raw, protocol = load_protocol(protocol_path)
    root = Path(__file__).resolve().parents[1]
    archive = ReviewedArchive(archive_path, "qnet")
    manifest = {"protocol": protocol, "protocol_file_sha256": hashlib.sha256(raw).hexdigest(),
                "archive_sha256": archive.sha256, "expected_archive_sha256": PINS["qnet"],
                "source_sha256": {path: hashlib.sha256((root / path).read_bytes()).hexdigest() for path in SOURCES}}
    output = Path(output)
    output.mkdir(parents=False, exist_ok=False)
    _export(json.dumps(manifest, indent=2, allow_nan=False), output / "frozen-protocol.json")

    contracts = [asdict(loop.check_checkpoint(model_id, archive, protocol["operating_point"]))
                 for model_id in protocol["learned_model_ids"] + protocol["contract_probe_model_ids"]]
    admitted = {row["model_id"] for row in contracts if row["status"] == "ADMITTED"}
    if admitted != set(protocol["learned_model_ids"]):
        raise ValueError("contract admitted a different set than the protocol's learned checkpoints")
    if progress:
        progress(f"Contract on the {protocol['operating_point']} plant: {sorted(admitted)} admitted; "
                 f"{sorted(set(protocol['contract_probe_model_ids']) - admitted)} refused.")
    reproduction = reproduce(archive, protocol)
    if progress:
        progress("Reproduction of declared held-out QBER passed for both checkpoints.")
    tuning = tune_alpha_beta(progress, protocol)
    tuned = tuning["selected"]
    _export(json.dumps(tuning, indent=2, allow_nan=False), output / "alpha-beta-tuning.json")

    per_seed = {controller_id: [] for controller_id in protocol["learned_model_ids"] + protocol["baselines"]}
    runs, artifacts = [], []
    controllers = {controller_id: _controller(controller_id, archive, tuned) for controller_id in per_seed}
    for seed in protocol["evaluation_plant_seeds"]:
        trajectory = plant.simulate(_parameters(protocol), protocol["duration_s"], seed)
        cases = [(controller_id, ()) for controller_id in per_seed]
        cases.append((protocol["fault_injection"]["model_id"], tuple(range(
            protocol["fault_injection"]["start_step"],
            protocol["fault_injection"]["start_step"] + protocol["fault_injection"]["steps"]))))
        for controller_id, faults in cases:
            learned = controller_id in protocol["learned_model_ids"]
            record = loop.run_loop(trajectory, controllers[controller_id], plant_seed=seed,
                                   monitoring_seed=seed + protocol["monitoring_seed_offset"],
                                   operating_point=protocol["operating_point"], supervisor=_supervisor(protocol),
                                   fallback=loop.AlphaBeta(tuned["alpha"], tuned["beta"]) if learned else None,
                                   fault_steps=faults, archive_sha256=archive.sha256 if learned else None)
            text = record.serialized()
            name = f"{seed}-{controller_id}{'-fault' if faults else ''}.json"
            _export(text, output / name)
            artifacts.append({"file": name, "sha256": hashlib.sha256(text.encode()).hexdigest()})
            runs.append(json.loads(text))
            if not faults:
                per_seed[controller_id].append(record.summary["mean_qber"])
        if progress:
            progress(f"Plant seed {seed}: {len(cases)} supervised runs.")

    if any(hashlib.sha256((root / path).read_bytes()).hexdigest() != digest
           for path, digest in manifest["source_sha256"].items()):
        raise ValueError("source changed during campaign; no completion summary")
    controller_stats, comparisons = statistics(per_seed, protocol)
    clean = [row for row in runs if not row["fault_steps"]]
    faulted = [row for row in runs if row["fault_steps"]]
    summary = {
        "schema": "orion.polarisation-closed-loop-results.v1", "status": "complete", "manifest": manifest,
        "contracts": contracts, "reproduction": reproduction, "alpha_beta": tuned,
        "controllers": controller_stats, "comparisons": comparisons,
        "secondary": {controller_id: {key: float(np.mean([row["summary"][key] for row in clean
                                                           if row["controller_id"] == controller_id]))
                                      for key in ("p95_qber", "uptime", "mean_key_rate_hz", "mean_residual_mrad")}
                      for controller_id in per_seed},
        "supervisor": {"rate_limited": sum(row["supervisor"]["rate_limited"] for row in clean),
                       "faults": sum(row["supervisor"]["faults"] for row in clean),
                       "degraded_runs": sum(row["supervisor"]["degraded_at_s"] is not None for row in clean),
                       "commands": sum(row["supervisor"]["commands"] for row in clean)},
        "fault_injection": {"runs": len(faulted),
                            "degraded_runs": sum(row["supervisor"]["degraded_at_s"] is not None for row in faulted),
                            "degraded_at_s": sorted({row["supervisor"]["degraded_at_s"] for row in faulted}),
                            "mean_qber": float(np.mean([row["summary"]["mean_qber"] for row in faulted]))},
        "runs": len(runs), "artifacts": artifacts,
        "policy_activated": False, "physical_action_performed": False, "simulator_action_performed": True,
        "limitations": ["Simulated plant parameterised by published field statistics, not recorded fibre data.",
                        "Ideal actuator here: the correction appears exactly one latency later; the author's bounded "
                        "device is evaluated separately (polarisation_actuator_v1).",
                        f"Checkpoints of the {protocol['operating_point']} point only; the other point's checkpoints do "
                        "not transfer and the contract refuses them.",
                        "Python replay timing is not hardware latency; the 40 ms latency is a simulation parameter."],
    }
    _export(json.dumps(summary, indent=2, allow_nan=False), output / "summary.json")
    return summary


__all__ = ["PROTOCOL", "PROTOCOLS", "PROTOCOL_DEV", "load_protocol", "reproduce", "run_campaign", "statistics", "tune_alpha_beta"]
