"""Frozen allocator campaign v1: the reviewed allocator in closed loop on the author's plant.

Gates first. With the plant exactly as the export's ``plant.json`` declares
(finite-block key), the ORION-Q loop must regenerate (1) the four whole
held-out episodes the export ships (``examples/episodes_summary.csv``: demand
stream and plant drawn from the episode seed) and (2) the author's re-traced
finite-key frontier point of the published persistence configuration
(``runs/realism/finite_key_frontier.csv``: demand stream of seed 0, episode seeds
500-505). Both to 1e-12.

Then 20 new episode seeds (1000-1019) under both conventions (A: demand stream of
seed 0, as the author's evaluation; B: demand stream of the episode seed, as the
export). Primary hypotheses, per replay configuration and convention, Holm over
the eight: the allocator carries more classical traffic than quantum-first (the
policy that silences classical traffic wherever key is owed), and serves key no
worse than quantum-first by more than two percentage points. Secondary: classical
throughput against the static-admission frontier at matched key service, the
tuned reactive threshold, and ridge against persistence.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
from pathlib import Path

import numpy as np

from qnet_network.allocation import ClassicalFirst, QuantumFirst, ReactiveThreshold, StaticAdmission

from . import allocation_loop as al
from .comparative_evaluation import holm_adjust, paired_sign_flip_test
from ..model_intake.artifacts import ALLOCATION_ROOT, PINS, ReviewedArchive
from ..model_intake.sync_session import _constant, _unique

FRONTIER = "external/qnet_network/runs/realism/finite_key_frontier.csv"
STATIC_LEVELS = [0.0, 0.25, 0.5, 0.75, 1.0]
REACTIVE_GRID = [[low, high] for low in (0.02, 0.035, 0.05, 0.07) for high in (0.04, 0.06, 0.08, 0.10) if high > low]
PROTOCOL = {
    "schema": "orion.allocation-closed-loop-protocol.v1", "target_id": al.TARGET_ID,
    "evidence": "simulated", "mode": "simulation",
    "plant": {"scenario": al.SCENARIO, "control_period_s": al.CONTROL_PERIOD_S, "duration_s": al.DURATION_S,
              "classical_power_full_w": al.CLASSICAL_POWER_W, "finite_key": True,
              "declared_by": "model/plant.json of the exported allocator"},
    "model_ids": list(al.MODEL_IDS), "max_faults": 3, "fallback": "quantum-first",
    "evaluation_seeds": list(range(1000, 1020)),
    "conventions": {"A": "demand stream of seed 0, plant of the episode seed (author's evaluation)",
                    "B": "demand stream and plant of the episode seed (export's episodes)"},
    "author_seed_sets": {"training": "0-11", "validation": "100-105", "test": "500-511"},
    "baselines": {"quantum_first": "QuantumFirst", "classical_first": "ClassicalFirst",
                  "static_levels": STATIC_LEVELS, "reactive_grid": REACTIVE_GRID,
                  "reactive_tuning": {"seeds": [100, 101, 102], "convention": "A",
                                      "criterion": "served key rounded to 3 decimals, ties by classical (author)"}},
    "reproduction": {"export_episodes": ALLOCATION_ROOT + "examples/episodes_summary.csv",
                     "frontier": FRONTIER, "frontier_sha256": "6b2f8ebd025727b1ac7fc20f5a46e551c4053c5bf8e8ca33fad22f7121023890",
                     "frontier_seeds": [500, 501, 502, 503, 504, 505], "tolerance": 1e-12},
    "primary": {"superiority": "served classical fraction above quantum-first",
                "non_inferiority": "served key fraction above quantum-first minus the margin",
                "margin": 0.02},
    "test": "exact one-sided paired sign-flip test", "multiplicity": "Holm over the 8 primary tests",
    "alpha": 0.05, "bootstrap": {"resamples": 2000, "seed": 20261022, "confidence": 0.95},
    "interpretation": "simulated plant of the author; not a deployed network or permission to actuate",
}
SOURCES = tuple(f"quantum_autopilot/{path}" for path in (
    "control_plane/allocation_campaign.py", "control_plane/allocation_loop.py", "control_plane/comparative_evaluation.py",
    "model_intake/allocation.py", "model_intake/artifacts.py", "model_intake/inference.py")) + tuple(
    f"qnet_network/allocation/{name}" for name in ("env.py", "policies.py", "rollout.py", "forecast.py", "scenario.py",
                                                   "state.py"))


def load_protocol(path):
    with Path(path).open("rb") as stream:
        raw = stream.read(16385)
    if len(raw) > 16384:
        raise ValueError("protocol exceeds 16 KiB")
    parsed = json.loads(raw, object_pairs_hook=_unique, parse_constant=_constant)
    if json.dumps(parsed, sort_keys=True) != json.dumps(PROTOCOL, sort_keys=True):
        raise ValueError("protocol differs from frozen v1; create a separately reviewed version")
    return raw, PROTOCOL


# --------------------------------------------------------------------------
# Gates
# --------------------------------------------------------------------------

def reproduce(archive, root, protocol=PROTOCOL, progress=None):
    config = protocol["reproduction"]
    rows = []
    summary = list(csv.DictReader(io.StringIO(archive.read(config["export_episodes"]).decode("utf-8"))))
    for row in summary:
        variant, seed = row["configuration"], int(row["seed"])
        record = al.run(al.make_env(seed), al.ReplayAllocator(archive, variant), seed, demand_seed=seed)
        errors = [abs(getattr(record, key) - float(row[key])) for key in ("served_qkd", "served_classical", "mean_admission")]
        rows.append({"gate": "export_episode", "model_id": f"qnet.allocator.{variant}", "seed": seed,
                     "served_qkd": record.served_qkd, "served_classical": record.served_classical,
                     "max_absolute_error": max(errors), "passed": max(errors) <= config["tolerance"]})
    raw = (Path(root) / config["frontier"]).read_bytes()
    if hashlib.sha256(raw).hexdigest() != config["frontier_sha256"]:
        raise ValueError("author's finite-key frontier record differs from the vendored reference")
    frontier = next(row for row in csv.DictReader(io.StringIO(raw.decode("utf-8")))
                    if row["family"] == "preditivo" and float(row["knob"]) == 1.0)
    env = al.make_env(0)
    records = [al.run(env, al.ReplayAllocator(archive, "persistence"), seed, demand_seed=0)
               for seed in config["frontier_seeds"]]
    key = float(np.mean([record.served_qkd for record in records]))
    classical = float(np.mean([record.served_classical for record in records]))
    error = max(abs(key - float(frontier["served_qkd"])), abs(classical - float(frontier["served_classical"])))
    rows.append({"gate": "finite_key_frontier", "model_id": "qnet.allocator.persistence",
                 "seeds": config["frontier_seeds"], "served_qkd": key, "served_classical": classical,
                 "author_served_qkd": float(frontier["served_qkd"]),
                 "author_served_classical": float(frontier["served_classical"]),
                 "max_absolute_error": error, "passed": error <= config["tolerance"]})
    if progress:
        progress(f"Reproduction: {sum(row['passed'] for row in rows)}/{len(rows)} gates regenerated "
                 "(four exported episodes and the author's finite-key frontier point).")
    if not all(row["passed"] for row in rows):
        raise ValueError("closed loop does not regenerate the author's allocator results; no campaign")
    return rows


def tune_reactive(protocol=PROTOCOL):
    """Author's rule (evaluate_phase4.tune_reactive), on the finite-key plant."""
    tuning = protocol["baselines"]["reactive_tuning"]
    env = al.make_env(0)
    grid, best, best_key = [], None, (-np.inf, -np.inf)
    for low, high in protocol["baselines"]["reactive_grid"]:
        records = [al.run(env, ReactiveThreshold(low=low, high=high), seed, demand_seed=0) for seed in tuning["seeds"]]
        key = float(np.mean([record.served_qkd for record in records]))
        classical = float(np.mean([record.served_classical for record in records]))
        grid.append({"low": low, "high": high, "served_qkd": key, "served_classical": classical})
        score = (round(key, 3), classical)
        if score > best_key:
            best, best_key = {"low": low, "high": high}, score
    return {"selected": best, "grid": grid}


# --------------------------------------------------------------------------
# Statistics
# --------------------------------------------------------------------------

def matched_static_classical(key, static_keys, static_classical):
    """Classical throughput of the static frontier at a given key service (linear in key)."""
    keys, classical = np.asarray(static_keys, dtype=float), np.asarray(static_classical, dtype=float)
    order = np.argsort(keys)
    keys, classical = keys[order], classical[order]
    if key > keys[-1]:
        return None  # no static level serves this much key
    return float(np.interp(key, keys, classical))


def primary_tests(values, protocol=PROTOCOL):
    margin = protocol["primary"]["margin"]
    rows = []
    for convention in protocol["conventions"]:
        quantum = values[convention]["baseline.quantum_first"]
        for model_id in protocol["model_ids"]:
            model = values[convention][model_id]
            classical = np.asarray(model["served_classical"]) - np.asarray(quantum["served_classical"])
            key = np.asarray(model["served_qkd"]) - np.asarray(quantum["served_qkd"]) + margin
            for hypothesis, differences in (("classical_superiority", classical), ("key_non_inferiority", key)):
                test = paired_sign_flip_test(differences, alternative="greater")
                rows.append({"convention": convention, "model_id": model_id, "hypothesis": hypothesis,
                             "baseline": "baseline.quantum_first", "n_seeds": len(differences),
                             "mean_difference": test["mean_difference"], "wins": int(np.sum(differences > 0)),
                             "p_value": test["p_value"], "method": test["method"]})
    for row, value in zip(rows, holm_adjust([row["p_value"] for row in rows])):
        row["holm_p_value"] = float(value)
        row["significant"] = bool(value < protocol["alpha"])
    return rows


def secondary(values, protocol=PROTOCOL):
    generator = np.random.default_rng(protocol["bootstrap"]["seed"])
    config = protocol["bootstrap"]
    lower = (1 - config["confidence"]) / 2
    rows = []
    for convention in protocol["conventions"]:
        block = values[convention]
        static = [block[f"baseline.static_{level:g}"] for level in STATIC_LEVELS]
        n = len(block["baseline.quantum_first"]["served_qkd"])
        for model_id in protocol["model_ids"]:
            model = block[model_id]

            def matched(index):
                key = float(np.mean(np.asarray(model["served_qkd"])[index]))
                keys = [float(np.mean(np.asarray(level["served_qkd"])[index])) for level in static]
                classical = [float(np.mean(np.asarray(level["served_classical"])[index])) for level in static]
                reference = matched_static_classical(key, keys, classical)
                ours = float(np.mean(np.asarray(model["served_classical"])[index]))
                return None if not reference else ours / reference

            everything = np.arange(n)
            draws = [matched(generator.integers(0, n, size=n)) for _ in range(config["resamples"])]
            finite = np.asarray([value for value in draws if value is not None], dtype=float)
            reactive = block["baseline.reactive"]
            rows.append({
                "convention": convention, "model_id": model_id,
                "served_qkd": float(np.mean(model["served_qkd"])), "served_classical": float(np.mean(model["served_classical"])),
                "classical_vs_static_at_matched_key": matched(everything),
                "matched_ratio_ci95": ([float(np.quantile(finite, lower)), float(np.quantile(finite, 1 - lower))]
                                       if len(finite) else None),
                "resamples_without_static_match": int(len(draws) - len(finite)),
                "vs_reactive": {"served_qkd_difference": float(np.mean(np.asarray(model["served_qkd"])
                                                                       - np.asarray(reactive["served_qkd"]))),
                                "served_classical_difference": float(np.mean(np.asarray(model["served_classical"])
                                                                             - np.asarray(reactive["served_classical"])))},
            })
        ridge, persistence = block["qnet.allocator.ridge"], block["qnet.allocator.persistence"]
        for metric in ("served_qkd", "served_classical"):
            differences = np.asarray(ridge[metric]) - np.asarray(persistence[metric])
            test = paired_sign_flip_test(differences, alternative="two-sided")
            rows.append({"convention": convention, "comparison": f"ridge - persistence, {metric}",
                         "mean_difference": test["mean_difference"], "p_value_two_sided": test["p_value"],
                         "ridge_wins": int(np.sum(differences > 0)), "ties": int(np.sum(differences == 0))})
    return rows


# --------------------------------------------------------------------------
# Campaign
# --------------------------------------------------------------------------

def _export(text, path):
    with Path(path).open("xb") as stream:
        stream.write(text.encode("utf-8"))


def _hashes(root):
    return {path: hashlib.sha256((root / "src" / path).read_bytes()).hexdigest() for path in SOURCES}


def run_campaign(archive_path, protocol_path, output, *, progress=None):
    raw, protocol = load_protocol(protocol_path)
    root = Path(__file__).resolve().parents[3]
    archive = ReviewedArchive(archive_path, "qnet")
    manifest = {"protocol": protocol, "protocol_file_sha256": hashlib.sha256(raw).hexdigest(),
                "archive_sha256": archive.sha256, "expected_archive_sha256": PINS["qnet"],
                "source_sha256": _hashes(root)}
    output = Path(output)
    output.mkdir(parents=False, exist_ok=False)
    _export(json.dumps(manifest, indent=2, allow_nan=False), output / "frozen-protocol.json")

    contracts = {"finite_key": al.check_contract(archive, al.make_env(0)),
                 "asymptotic_probe": al.check_contract(archive, al.make_env(0, finite_key=False))}
    if contracts["finite_key"]["status"] != "admitted" or contracts["asymptotic_probe"]["status"] != "refused":
        raise ValueError("contract did not admit exactly the exported plant")
    reproduction = reproduce(archive, root, protocol, progress)
    reactive = tune_reactive(protocol)
    if progress:
        progress(f"Reactive threshold tuned: {reactive['selected']}")

    records, values = [], {}
    for convention in protocol["conventions"]:
        shared = al.make_env(0) if convention == "A" else None
        for seed in protocol["evaluation_seeds"]:
            env = shared if shared is not None else al.make_env(seed)
            policies = {model_id: al.ReplayAllocator(archive, model_id.split(".")[-1], max_faults=protocol["max_faults"])
                        for model_id in protocol["model_ids"]}
            policies.update({"baseline.quantum_first": QuantumFirst(), "baseline.classical_first": ClassicalFirst(),
                             "baseline.reactive": ReactiveThreshold(**reactive["selected"])})
            policies.update({f"baseline.static_{level:g}": StaticAdmission(level) for level in STATIC_LEVELS})
            for controller_id, policy in policies.items():
                learned = controller_id in protocol["model_ids"]
                record = al.run(env, policy, seed, demand_seed=0 if convention == "A" else seed,
                                controller_id=controller_id, archive_sha256=archive.sha256 if learned else None)
                row = {"convention": convention, **record.to_dict()}
                records.append(row)
                entry = values.setdefault(convention, {}).setdefault(controller_id, {"served_qkd": [], "served_classical": []})
                entry["served_qkd"].append(record.served_qkd)
                entry["served_classical"].append(record.served_classical)
        if progress:
            block = values[convention]
            progress(f"Convention {convention}: " + ", ".join(
                f"{name.split('.')[-1]} {np.mean(block[name]['served_qkd']):.3f}/{np.mean(block[name]['served_classical']):.3f}"
                for name in (*protocol["model_ids"], "baseline.quantum_first", "baseline.reactive")))

    text = "".join(json.dumps(row, allow_nan=False, sort_keys=True) + "\n" for row in records)
    _export(text, output / "allocation-runs.jsonl")
    if _hashes(root) != manifest["source_sha256"]:
        raise ValueError("source changed during campaign; no completion summary")
    learned = [row for row in records if row["controller_id"] in protocol["model_ids"]]
    summary = {
        "schema": "orion.allocation-closed-loop-results.v1", "status": "complete", "manifest": manifest,
        "contracts": contracts, "reproduction": reproduction, "reactive": reactive,
        "means": {convention: {controller_id: {metric: float(np.mean(series)) for metric, series in entry.items()}
                               for controller_id, entry in block.items()} for convention, block in values.items()},
        "primary": primary_tests(values, protocol), "secondary": secondary(values, protocol),
        "supervisor": {"faults": int(sum(row["faults"] or 0 for row in learned)),
                       "degraded_runs": int(sum(row["degraded_at_step"] is not None for row in learned))},
        "runs": len(records), "artifacts": [{"file": "allocation-runs.jsonl", "sha256": hashlib.sha256(text.encode()).hexdigest()}],
        "policy_activated": False, "physical_action_performed": False, "simulator_action_performed": True,
        "limitations": ["The author's simulated metro plant; not a deployed network.",
                        "Key accounting is the author's finite-block estimate per control period, not a security proof.",
                        "Only the exported configuration (coverage 1.0) exists as a reviewed model; the frontier "
                        "across coverage targets is the author's, not re-run here."],
    }
    _export(json.dumps(summary, indent=2, allow_nan=False), output / "summary.json")
    return summary


__all__ = ["PROTOCOL", "load_protocol", "matched_static_classical", "primary_tests", "reproduce", "run_campaign",
           "secondary", "tune_reactive"]
