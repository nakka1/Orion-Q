"""Frozen key-blocking campaign v1: the author's blocking controller on the key stream of the ORION-Q loop.

The author's blocking controller (``qnet_network.physics.blocks.adaptive_blocks``)
chooses where to close the privacy-amplification blocks of the polarisation link: it
sees only the bits the protocol publishes for parameter estimation and, at every
0.1 s segment, compares closing the block, carrying it, or (``pooling=True``)
skipping the segment. The author measured it on the development point only, outside
ORION-Q, against timers and two hindsight oracles. Here it settles up the key of the
ORION-Q supervised loop — the same replay, supervisor and fallback as the closed-loop
campaigns — on both operating points.

Gates, before any scoring:

* ``runs/realism/adaptive_blocks.csv`` and its summary: the author's twelve test seeds
  of the development point, with the four compensators of the author's study
  (persistence, alpha-beta 0.05/0.005, Ridge and MLP), run through the ORION-Q loop
  and settled up by the author's timers, controllers and oracles;
* ``runs/realism/blocking_stress.csv`` and its summary: the author's MLP under
  vibration of 0.3 to 1.2 rad, with the author's ``run_closed_loop`` (the study
  function transcribed below); the MLP is rebuilt from the exported arrays;
* the ORION-Q loop on the evaluation seeds equals the closed-loop campaigns v1 in mean
  QBER, compensator by compensator.

Then the closed-loop campaigns' 20 seeds at each point. Primary: on the link
compensated by persistence (QBER near the 11% abort line), the pooled controller
against the best timer chosen with hindsight (one-sided, Holm over the two points);
with the learned compensators, the pooled controller non-inferior to the best timer
with a 1% margin (one-sided, Holm over four). Exact paired sign-flip tests over seeds.
"""
from __future__ import annotations

import hashlib
import io
import json
import math
from dataclasses import asdict
from pathlib import Path

import numpy as np
import pandas as pd

from . import polarisation_loop as loop
from . import polarisation_plant as plant
from .comparative_evaluation import holm_adjust, paired_sign_flip_test
from .polarisation_campaign import PROTOCOL as CLOSED_LOOP
from .polarisation_quantisation import author_compensator, task3
from ..model_intake.artifacts import PINS, ReviewedArchive
from ..model_intake.sync_session import _constant, _unique

ROOT = Path(__file__).resolve().parents[3]
AUTHOR_RUNS = ROOT / "external" / "qnet_network" / "runs" / "realism"
POINTS = ("calibrado", "desenvolvimento")
TIMERS_S = (0.1, 1.0, 5.0, 10.0, 20.0)
AUTHOR_NAMES = {"baseline.persistence": "persistência", "baseline.alpha_beta": "filtro alfa-beta",
                "ridge": "ridge (Wiener)", "mlp": "MLP"}
PROTOCOL = {
    "schema": "orion.polarisation-blocking-protocol.v1", "target_id": loop.TARGET_ID,
    "evidence": "simulated", "mode": "simulation",
    "controller": "qnet_network.physics.blocks.adaptive_blocks(qber, sifted_rate_hz, dt_s, max_block_s=run, "
                  "rng=seed + blocking_seed_offset, pooling=True): 10% of the bits published, 0.1 s segments, "
                  "abort at 11%, no tuning constant",
    "strategies": ["fixo 0.1 s", "fixo 1 s", "fixo 5 s", "fixo 10 s", "fixo 20 s", "adaptativo",
                   "adaptativo agrupado", "oráculo", "oráculo agrupado"],
    "operating_points": list(POINTS), "duration_s": 20.0, "sifted_rate_hz": plant.SIFTED_RATE_HZ,
    "evaluation_plant_seeds": CLOSED_LOOP["evaluation_plant_seeds"],
    "monitoring_seed_offset": CLOSED_LOOP["monitoring_seed_offset"], "blocking_seed_offset": 8000,
    "compensators": ["baseline.persistence", "baseline.alpha_beta", "learned ridge", "learned mlp"],
    "alpha_beta": {"calibrado": {"alpha": 0.01, "beta": 0.0}, "desenvolvimento": {"alpha": 0.03, "beta": 0.005},
                   "source": "selected on the tuning seeds by the closed-loop campaigns v1 (their summaries)"},
    "supervisor": CLOSED_LOOP["supervisor"], "fallback": "baseline.alpha_beta",
    "reproduction": {
        "adaptive_blocks": {"tables": ["runs/realism/adaptive_blocks.csv", "runs/realism/adaptive_blocks_summary.csv"],
                            "point": "desenvolvimento", "plant_seeds": list(range(500, 512)),
                            "monitoring_seeds": "7000 + index", "blocking_seeds": "9000 + index",
                            "alpha_beta": {"alpha": 0.05, "beta": 0.005},
                            "path": "ORION-Q loop (replay, supervisor) and the author's block functions"},
        "blocking_stress": {"tables": ["runs/realism/blocking_stress.csv", "runs/realism/blocking_stress_summary.csv"],
                            "plant_seeds": list(range(500, 506)), "vibration_amplitude_rad": [0.3, 0.6, 0.9, 1.2],
                            "path": "the author's run_closed_loop and MLP rebuilt from the exported arrays "
                                    "(evaluate_realism.study_blocking_stress, transcribed)"},
        "closed_loop": {"source": "docs/results/orion_q_polarisation_closed_loop*_20260929_summary.json",
                        "tolerance": 1e-12},
        "relative_tolerance": 1e-9},
    "primary": {"persistence": "pooled controller key rate above the best timer (duration chosen with hindsight "
                               "on the evaluation seeds), per point, one-sided",
                "learned": "pooled controller key rate at least 0.99 times the best timer, per point and model, "
                           "one-sided (difference controller - 0.99 x timer)"},
    "non_inferiority_margin": 0.01,
    "test": "exact paired sign-flip test over the 20 seeds",
    "multiplicity": "Holm over the 2 persistence tests; Holm over the 4 learned tests", "alpha": 0.05,
    "bootstrap": {"resamples": 10000, "seed": 20261022, "confidence": 0.95, "unit": "seed"},
    "interpretation": "key accounting on the simulated supervised loop; finite-key bound of the author, no "
                      "signalling between the ends and no security proof for non-contiguous blocks",
}
SOURCES = tuple(f"quantum_autopilot/{path}" for path in (
    "control_plane/polarisation_blocking.py", "control_plane/polarisation_loop.py",
    "control_plane/polarisation_plant.py", "control_plane/polarisation_campaign.py",
    "control_plane/polarisation_quantisation.py", "control_plane/comparative_evaluation.py",
    "model_intake/compensation.py", "model_intake/artifacts.py")) + (
    "qnet_network/physics/blocks.py", "qnet_network/physics/link.py", "qnet_network/compensation/closed_loop.py")
CLOSED_LOOP_SUMMARIES = {"calibrado": "orion_q_polarisation_closed_loop_20260929_summary.json",
                         "desenvolvimento": "orion_q_polarisation_closed_loop_dev_20260929_summary.json"}


def load_protocol(path):
    with Path(path).open("rb") as stream:
        raw = stream.read(32769)
    if len(raw) > 32768:
        raise ValueError("protocol exceeds 32 KiB")
    parsed = json.loads(raw, object_pairs_hook=_unique, parse_constant=_constant)
    if json.dumps(parsed, sort_keys=True) != json.dumps(PROTOCOL, sort_keys=True):
        raise ValueError("protocol differs from frozen v1; create a separately reviewed version")
    return raw, PROTOCOL


# ---------------------------------------------------------------------------
# the author's study (scripts/evaluate_realism.py), as it settles up one run
# ---------------------------------------------------------------------------

def plans_for(qber, dt_s, run_s, rng, sifted_rate_hz=plant.SIFTED_RATE_HZ):
    """``study_adaptive_blocks``: the timers, the two controllers and the two oracles, in the author's order."""
    from qnet_network.physics.blocks import adaptive_blocks, fixed_blocks, optimal_blocks, pooled_optimal_blocks

    plans = [fixed_blocks(qber, sifted_rate_hz, dt_s, duration) for duration in TIMERS_S]
    plans.append(adaptive_blocks(qber, sifted_rate_hz, dt_s, max_block_s=run_s, rng=rng))
    plans.append(adaptive_blocks(qber, sifted_rate_hz, dt_s, max_block_s=run_s, rng=rng, pooling=True))
    plans.append(optimal_blocks(qber, sifted_rate_hz, dt_s))
    plans.append(pooled_optimal_blocks(qber, sifted_rate_hz, dt_s))
    return plans


def plan_row(plan, qber):
    return {"strategy": plan.strategy, "mean_qber": float(np.mean(qber)), "key_rate_hz": plan.mean_key_rate_hz,
            "blocks": plan.count, "duty": plan.duty, "empty_fraction": plan.empty_fraction,
            "pieces_per_block": plan.pieces / plan.count if plan.count else 0.0,
            "mean_block_s": float(np.mean(plan.durations_s)) if plan.count else 0.0}


def summarise_adaptive_blocks(frame):
    """The author's ``summarise_adaptive_blocks``: per compensator and strategy, against the pooled oracle."""
    summary = frame.groupby(["compensator", "strategy"], sort=False).agg(
        mean_qber=("mean_qber", "mean"), key_rate_hz=("key_rate_hz", "mean"), blocks=("blocks", "mean"),
        duty=("duty", "mean"), empty_fraction=("empty_fraction", "mean"),
        pieces_per_block=("pieces_per_block", "mean"), mean_block_s=("mean_block_s", "mean")).reset_index()
    oracle = summary[summary["strategy"] == "oráculo agrupado"].set_index("compensator")
    summary["of_oracle"] = [row.key_rate_hz / oracle.loc[row.compensator, "key_rate_hz"]
                            if oracle.loc[row.compensator, "key_rate_hz"] > 0 else np.nan
                            for row in summary.itertuples()]
    return summary


def study_blocking_stress(compensator, seeds, amplitudes):
    """The author's ``study_blocking_stress`` (scripts/evaluate_realism.py), on the author's closed loop."""
    from qnet_network.compensation import run_closed_loop
    from qnet_network.physics.blocks import adaptive_blocks, fixed_blocks, optimal_blocks, pooled_optimal_blocks

    t3 = task3()
    control = t3.make_control()
    rate = control.sifted_rate_hz
    rows = []
    for amplitude in amplitudes:
        trajectories = t3.make_trajectories(seeds, vibration_amplitude_rad=amplitude)
        for index, trajectory in enumerate(trajectories):
            result = run_closed_loop(trajectory, compensator, control, rng=7000 + index)
            dt = float(result.time_s[1] - result.time_s[0])
            run_s = float(result.time_s.size * dt)
            timers = {duration: fixed_blocks(result.qber, rate, dt, duration).mean_key_rate_hz
                      for duration in TIMERS_S}
            best = max(timers, key=timers.get)
            controller = adaptive_blocks(result.qber, rate, dt, max_block_s=run_s, rng=9000 + index)
            pooling = adaptive_blocks(result.qber, rate, dt, max_block_s=run_s, rng=9000 + index, pooling=True)
            oracle = optimal_blocks(result.qber, rate, dt)
            pooled_oracle = pooled_optimal_blocks(result.qber, rate, dt)
            rows.append({"vibration_amplitude_rad": amplitude, "seed": int(seeds[index]),
                         "mean_qber": float(np.mean(result.qber)), "best_timer_s": best,
                         "best_timer_hz": timers[best], "timer_1s_hz": timers[1.0],
                         "adaptive_hz": controller.mean_key_rate_hz, "pooling_hz": pooling.mean_key_rate_hz,
                         "oracle_hz": oracle.mean_key_rate_hz, "pooled_oracle_hz": pooled_oracle.mean_key_rate_hz,
                         "adaptive_duty": controller.duty, "pooling_duty": pooling.duty,
                         "pooling_pieces_per_block": pooling.pieces / pooling.count if pooling.count else 0.0})
    return pd.DataFrame(rows)


def summarise_blocking_stress(frame):
    """The author's ``summarise_blocking_stress``."""
    summary = frame.groupby("vibration_amplitude_rad").agg(
        mean_qber=("mean_qber", "mean"), median_best_timer_s=("best_timer_s", "median"),
        best_timer_hz=("best_timer_hz", "mean"), timer_1s_hz=("timer_1s_hz", "mean"),
        adaptive_hz=("adaptive_hz", "mean"), pooling_hz=("pooling_hz", "mean"), oracle_hz=("oracle_hz", "mean"),
        pooled_oracle_hz=("pooled_oracle_hz", "mean"), adaptive_duty=("adaptive_duty", "mean"),
        pooling_duty=("pooling_duty", "mean"), pieces_per_block=("pooling_pieces_per_block", "mean")).reset_index()
    summary["over_best_timer"] = np.where(summary["best_timer_hz"] > 0,
                                          summary["pooling_hz"] / summary["best_timer_hz"], np.nan)
    summary["pooling_gain"] = np.where(summary["adaptive_hz"] > 0,
                                       summary["pooling_hz"] / summary["adaptive_hz"], np.nan)
    summary["of_oracle"] = np.where(summary["pooled_oracle_hz"] > 0,
                                    summary["pooling_hz"] / summary["pooled_oracle_hz"], np.nan)
    return summary


# ---------------------------------------------------------------------------
# gates
# ---------------------------------------------------------------------------

def table_errors(ours, theirs, keys, tolerance):
    """Worst relative error per column after aligning on ``keys``; missing or extra rows fail."""
    ours = pd.read_csv(io.StringIO(ours.to_csv(index=False)))    # the author's CSV round trip
    merged = theirs.merge(ours, on=keys, how="outer", suffixes=("_author", "_orion"), indicator=True)
    unmatched = int((merged["_merge"] != "both").sum())
    columns = {}
    for column in (name for name in theirs.columns if name not in keys):
        author = merged[column + "_author"].to_numpy(float)
        orion = merged[column + "_orion"].to_numpy(float)
        both_nan = np.isnan(author) & np.isnan(orion)
        scale = np.where(author == 0.0, 1.0, np.abs(author))
        error = np.nan_to_num(np.where(both_nan, 0.0, np.abs(author - orion) / scale), nan=np.inf)
        columns[column] = float(error.max()) if error.size else 0.0
    worst = max(columns.values(), default=0.0)
    return {"rows": int(len(theirs)), "unmatched_rows": unmatched, "max_relative_error": worst,
            "passed": unmatched == 0 and worst <= tolerance, "columns": columns}


def _supervisor(protocol):
    return loop.PolarisationSupervisor(loop.PolarisationSupervisorConfig(**protocol["supervisor"]))


def _controllers(point, archive, alpha_beta):
    learned = {kind: f"qnet.compensation.{point}.{kind}" for kind in ("ridge", "mlp")}
    return {"baseline.persistence": (loop.Persistence(), None),
            "baseline.alpha_beta": (loop.AlphaBeta(alpha_beta["alpha"], alpha_beta["beta"]), None),
            **{model_id: (loop.LearnedCheckpoint(model_id, archive), "fallback") for model_id in learned.values()}}


def settle(point, archive, seeds, monitoring, blocking, alpha_beta, fallback, protocol):
    """Every compensator through the ORION-Q loop on ``seeds``; one row per compensator, seed and strategy."""
    rows, per_seed_qber = [], {}
    trajectories = [plant.simulate(plant.OPERATING_POINTS[point], protocol["duration_s"], seed) for seed in seeds]
    for controller_id, (controller, learned) in _controllers(point, archive, alpha_beta).items():
        for index, (seed, trajectory) in enumerate(zip(seeds, trajectories)):
            record, qber = loop.run_loop(
                trajectory, controller, monitoring_seed=monitoring(index, seed), operating_point=point,
                plant_seed=seed, supervisor=_supervisor(protocol), return_qber=True,
                fallback=loop.AlphaBeta(fallback["alpha"], fallback["beta"]) if learned else None,
                archive_sha256=archive.sha256 if learned else None)
            dt = float(trajectory.time_s[1] - trajectory.time_s[0])
            run_s = float(trajectory.time_s.size * dt)
            per_seed_qber.setdefault(controller_id, []).append(record.summary["mean_qber"])
            for plan in plans_for(qber, dt, run_s, blocking(index, seed), protocol["sifted_rate_hz"]):
                rows.append({"point": point, "controller_id": controller_id, "seed": int(seed), **plan_row(plan, qber),
                             "supervisor_faults": int(record.supervisor["faults"])})
    return pd.DataFrame(rows), per_seed_qber


def _author_name(controller_id):
    return AUTHOR_NAMES.get(controller_id) or AUTHOR_NAMES[controller_id.rsplit(".", 1)[1]]


def reproduce_adaptive_blocks(archive, protocol=PROTOCOL):
    config = protocol["reproduction"]["adaptive_blocks"]
    frame, _ = settle(config["point"], archive, config["plant_seeds"], lambda index, seed: 7000 + index,
                      lambda index, seed: 9000 + index, config["alpha_beta"], config["alpha_beta"], protocol)
    frame = frame.assign(compensator=frame["controller_id"].map(_author_name))
    columns = ["compensator", "seed", "strategy", "mean_qber", "key_rate_hz", "blocks", "duty", "empty_fraction",
               "pieces_per_block", "mean_block_s"]
    tolerance = protocol["reproduction"]["relative_tolerance"]
    return [
        {"gate": "adaptive_blocks.csv", **table_errors(frame[columns], pd.read_csv(AUTHOR_RUNS / "adaptive_blocks.csv"),
                                                        ["compensator", "seed", "strategy"], tolerance)},
        {"gate": "adaptive_blocks_summary.csv",
         **table_errors(summarise_adaptive_blocks(frame[columns]),
                        pd.read_csv(AUTHOR_RUNS / "adaptive_blocks_summary.csv"), ["compensator", "strategy"],
                        tolerance)}]


def reproduce_blocking_stress(archive, protocol=PROTOCOL):
    config = protocol["reproduction"]["blocking_stress"]
    compensator = author_compensator(archive, "desenvolvimento", "mlp")
    frame = study_blocking_stress(compensator, config["plant_seeds"], config["vibration_amplitude_rad"])
    tolerance = protocol["reproduction"]["relative_tolerance"]
    return [
        {"gate": "blocking_stress.csv", **table_errors(frame, pd.read_csv(AUTHOR_RUNS / "blocking_stress.csv"),
                                                        ["vibration_amplitude_rad", "seed"], tolerance)},
        {"gate": "blocking_stress_summary.csv",
         **table_errors(summarise_blocking_stress(frame), pd.read_csv(AUTHOR_RUNS / "blocking_stress_summary.csv"),
                        ["vibration_amplitude_rad"], tolerance)}]


def closed_loop_gate(point, per_seed_qber, protocol=PROTOCOL):
    """Mean QBER per compensator on the evaluation seeds equals the published closed-loop campaign."""
    published = json.loads((ROOT / "docs" / "results" / CLOSED_LOOP_SUMMARIES[point]).read_text(encoding="utf-8"))
    errors = {controller_id: abs(math.fsum(values) / len(values) - published["controllers"][controller_id]["mean_qber"])
              for controller_id, values in per_seed_qber.items()}
    return {"gate": "closed_loop_mean_qber", "point": point, "absolute_errors": errors,
            "max_absolute_error": max(errors.values()),
            "passed": max(errors.values()) <= protocol["reproduction"]["closed_loop"]["tolerance"]}


# ---------------------------------------------------------------------------
# evaluation
# ---------------------------------------------------------------------------

def evaluate_point(args):
    archive_path, point, protocol, pin = args
    PINS["qnet"] = pin
    archive = ReviewedArchive(archive_path, "qnet")
    contracts = [asdict(loop.check_checkpoint(f"qnet.compensation.{point}.{kind}", archive, point))
                 for kind in ("ridge", "mlp")]
    if any(row["status"] != "ADMITTED" for row in contracts):
        raise ValueError(f"contract refused a checkpoint on its own plant ({point})")
    offset, blocking = protocol["monitoring_seed_offset"], protocol["blocking_seed_offset"]
    alpha_beta = protocol["alpha_beta"][point]
    frame, per_seed_qber = settle(point, archive, protocol["evaluation_plant_seeds"],
                                  lambda index, seed: seed + offset, lambda index, seed: seed + blocking,
                                  alpha_beta, alpha_beta, protocol)
    return {"point": point, "contracts": contracts, "gate": closed_loop_gate(point, per_seed_qber, protocol),
            "frame_csv": frame.to_csv(index=False, lineterminator="\n")}


def _stress_gate(args):
    archive_path, protocol, pin = args
    PINS["qnet"] = pin
    return reproduce_blocking_stress(ReviewedArchive(archive_path, "qnet"), protocol)


def _series(frame, point, controller_id, strategy):
    rows = frame[(frame["point"] == point) & (frame["controller_id"] == controller_id) & (frame["strategy"] == strategy)]
    return rows.sort_values("seed")["key_rate_hz"].to_numpy(dtype=float)


def _ratio_ci(numerator, denominator, protocol, generator):
    bootstrap = protocol["bootstrap"]
    values = []
    for _ in range(bootstrap["resamples"]):
        pick = generator.integers(0, len(numerator), len(numerator))
        total = float(np.sum(denominator[pick]))
        values.append(float(np.sum(numerator[pick])) / total if total > 0 else np.nan)
    lower = (1 - bootstrap["confidence"]) / 2
    return [float(np.nanquantile(values, lower)), float(np.nanquantile(values, 1 - lower))]


def compare(frame, point, controller_id, protocol, generator):
    timers = {name: _series(frame, point, controller_id, name) for name in protocol["strategies"]
              if name.startswith("fixo")}
    best = max(timers, key=lambda name: float(np.mean(timers[name])))
    pooled = _series(frame, point, controller_id, "adaptativo agrupado")
    learned = not controller_id.startswith("baseline.")
    margin = 1.0 - protocol["non_inferiority_margin"] if learned else 1.0
    difference = pooled - margin * timers[best]
    test = paired_sign_flip_test(difference, alternative="greater")
    oracle = _series(frame, point, controller_id, "oráculo agrupado")
    return {"point": point, "controller_id": controller_id,
            "hypothesis": "non_inferior_to_best_timer" if learned else "above_best_timer",
            "best_timer": best, "best_timer_hz": float(np.mean(timers[best])),
            "controller_hz": float(np.mean(pooled)), "ratio_to_best_timer": float(np.sum(pooled) / np.sum(timers[best]))
            if np.sum(timers[best]) > 0 else None,
            "ratio_ci95": _ratio_ci(pooled, timers[best], protocol, generator),
            "of_pooled_oracle": float(np.sum(pooled) / np.sum(oracle)) if np.sum(oracle) > 0 else None,
            "seeds_won": int(np.sum(difference > 0)), "seeds_lost": int(np.sum(difference < 0)),
            "margin_factor": margin, "p_value": test["p_value"], "method": test["method"]}


def strategy_table(frame):
    table = frame.groupby(["point", "controller_id", "strategy"], sort=False).agg(
        mean_qber=("mean_qber", "mean"), key_rate_hz=("key_rate_hz", "mean"), blocks=("blocks", "mean"),
        duty=("duty", "mean"), empty_fraction=("empty_fraction", "mean"),
        pieces_per_block=("pieces_per_block", "mean")).reset_index()
    return json.loads(table.to_json(orient="records"))


def _export(text, path):
    with Path(path).open("xb") as stream:
        stream.write(text.encode("utf-8"))


def _hashes():
    return {path: hashlib.sha256((ROOT / "src" / path).read_bytes()).hexdigest() for path in SOURCES}


def run_campaign(archive_path, protocol_path, output, *, progress=None, workers=1):
    raw, protocol = load_protocol(protocol_path)
    archive = ReviewedArchive(archive_path, "qnet")
    manifest = {"protocol": protocol, "protocol_file_sha256": hashlib.sha256(raw).hexdigest(),
                "archive_sha256": archive.sha256, "expected_archive_sha256": PINS["qnet"], "source_sha256": _hashes()}
    output = Path(output)
    output.mkdir(parents=False, exist_ok=False)
    _export(json.dumps(manifest, indent=2, allow_nan=False), output / "frozen-protocol.json")

    tasks = [(str(archive_path), point, protocol, PINS["qnet"]) for point in POINTS]
    if workers > 1:
        from concurrent.futures import ProcessPoolExecutor

        with ProcessPoolExecutor(max_workers=min(workers, 3)) as pool:
            gate_future = pool.submit(_stress_gate, (str(archive_path), protocol, PINS["qnet"]))
            point_futures = [pool.submit(evaluate_point, task) for task in tasks]
            gates = reproduce_adaptive_blocks(archive, protocol)
            gates += gate_future.result()
            results = [future.result() for future in point_futures]
    else:
        gates = reproduce_adaptive_blocks(archive, protocol) + reproduce_blocking_stress(archive, protocol)
        results = [evaluate_point(task) for task in tasks]
    gates += [result["gate"] for result in results]
    if progress:
        for row in gates:
            detail = (f"{row['rows']} rows, max relative error {row['max_relative_error']:.1e}" if "rows" in row else
                      f"{row['point']}: max absolute error {row['max_absolute_error']:.1e}")
            progress(f"gate {row['gate']}: {detail}")
    if not all(row["passed"] for row in gates):
        _export(json.dumps({"status": "gate_failed", "gates": gates}, indent=2, default=float),
                output / "gate-failed.json")
        raise ValueError("the author's blocking tables or the closed-loop QBER were not reproduced; no campaign")

    text = "".join(result["frame_csv"] if index == 0 else result["frame_csv"].split("\n", 1)[1]
                   for index, result in enumerate(results))
    frame = pd.read_csv(io.StringIO(text))
    generator = np.random.default_rng(protocol["bootstrap"]["seed"])
    persistence, learned, secondary = [], [], []
    for point in POINTS:
        persistence.append(compare(frame, point, "baseline.persistence", protocol, generator))
        for kind in ("ridge", "mlp"):
            learned.append(compare(frame, point, f"qnet.compensation.{point}.{kind}", protocol, generator))
        secondary.append(compare(frame, point, "baseline.alpha_beta", protocol, generator))
    for family in (persistence, learned):
        for row, value in zip(family, holm_adjust([row["p_value"] for row in family])):
            row["holm_p_value"] = float(value)
            row["significant"] = bool(value < protocol["alpha"])
    if progress:
        for row in persistence + learned:
            progress(f"{row['point']} {row['controller_id']}: controller {row['controller_hz']:.0f} Hz vs "
                     f"{row['best_timer']} {row['best_timer_hz']:.0f} Hz, Holm p {row['holm_p_value']:.2g}")
    _export(text, output / "blocking-runs.csv")
    if _hashes() != manifest["source_sha256"]:
        raise ValueError("source changed during campaign; no completion summary")
    summary = {
        "schema": "orion.polarisation-blocking-results.v1", "status": "complete", "manifest": manifest,
        "contracts": [row for result in results for row in result["contracts"]], "reproduction": gates,
        "primary": {"persistence": persistence, "learned": learned}, "secondary": {"alpha_beta": secondary},
        "strategies": strategy_table(frame),
        "supervisor_faults": int(frame["supervisor_faults"].sum()),
        "artifacts": [{"file": "blocking-runs.csv", "sha256": hashlib.sha256(text.encode()).hexdigest()}],
        "policy_activated": False, "physical_action_performed": False, "simulator_action_performed": True,
        "limitations": ["Key accounting with the author's finite-key bound; the signalling the two ends need to "
                        "agree on a block is not modelled, and non-contiguous blocks have no security proof.",
                        "Ideal actuator; the author's plant at its two operating points."],
    }
    _export(json.dumps(summary, indent=2, allow_nan=False, default=float), output / "summary.json")
    return summary


__all__ = ["PROTOCOL", "compare", "load_protocol", "plans_for", "reproduce_adaptive_blocks",
           "reproduce_blocking_stress", "run_campaign", "settle", "summarise_adaptive_blocks",
           "summarise_blocking_stress", "table_errors"]
