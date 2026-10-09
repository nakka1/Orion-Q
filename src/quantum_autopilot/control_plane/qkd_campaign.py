"""Frozen QKD closed-loop campaign v1: key-rate actor and synchroniser on the author's plants.

Neither model is trained or tuned here. Each family first has to pass its
reproduction gates — the manuscript's own numbers regenerated through the
ORION-Q replay — and only then is scored on new seeds that no training,
validation or test set of the author used:

* key rate: five spans (25-80 km), 20 new episode seeds (950000-950019), actor at
  T=0.25 with ``default_rng(seed)`` (the export's convention), against the
  commissioned static point and, as a reference only, the author's oracle;
* synchronisation: 50 km (the span the ridge was trained on), four gate widths,
  two detector dark rates, 20 new one-hour runs (seeds 600-619), against no
  correction, 60 s recalibration and the alpha-beta tracker.

Primary endpoints and tests are fixed in ``PROTOCOL`` (mirrored by
``configs/optional_models/qkd_closed_loop_v1.json``): exact one-sided paired
sign-flip tests, Holm within each family.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np

from . import keyrate_loop as kr
from . import sync_loop as sy
from .comparative_evaluation import holm_adjust, paired_sign_flip_test
from ..model_intake.artifacts import PINS, ReviewedArchive
from ..model_intake.inference import SyncReplayModel
from ..model_intake.keyrate import KeyRateReplayModel
from ..model_intake.sync_session import _constant, _unique

# Published values (Mbit/s, mean and sample standard deviation over 24 test episodes),
# QKD manuscript Tabela 7 = TCC tab:taxa.
PUBLISHED_KEYRATE = {
    "25": {"baseline.static": [3.180, 0.451], "qkd.keyrate.actor": [3.782, 0.506], "reference.oracle": [3.817, 0.584]},
    "35": {"baseline.static": [2.732, 0.425], "qkd.keyrate.actor": [3.092, 0.397], "reference.oracle": [3.059, 0.417]},
    "50": {"baseline.static": [1.899, 0.319], "qkd.keyrate.actor": [2.128, 0.312], "reference.oracle": [2.152, 0.353]},
    "65": {"baseline.static": [1.038, 0.169], "qkd.keyrate.actor": [1.138, 0.213], "reference.oracle": [1.220, 0.244]},
    "80": {"baseline.static": [0.489, 0.072], "qkd.keyrate.actor": [0.532, 0.113], "reference.oracle": [0.713, 0.107]},
}
# Published key rate at 50 km (Mbit/s, mean of 12 one-hour runs), TCC tab:sinc.
PUBLISHED_SYNC = {
    "10": {"200": [0.136, 1.001, 1.464, 1.508], "500": [0.309, 1.631, 1.958, 1.971], "1000": [0.530, 1.924, 2.007, 2.007]},
    "1000000": {"200": [0.000, 0.429, 0.948, 0.998], "350": [0.000, 0.536, 1.064, 1.099],
                "1000": [0.000, 0.128, 0.253, 0.253]},
}
SYNC_ORDER = [*sy.BASELINE_IDS, sy.MODEL_ID]

PROTOCOL = {
    "schema": "orion.qkd-closed-loop-protocol.v1", "evidence": "simulated", "mode": "simulation",
    "plants": "qnet_qkd (vendored author QKD source that produced the pinned modelo_qkd_ml.zip)",
    "keyrate": {
        "target_id": kr.TARGET_ID, "spans_km": [25.0, 35.0, 50.0, 65.0, 80.0], "periods": kr.STEPS,
        "period_s": kr.PERIOD_S, "evaluation_plant_seeds": list(range(950000, 950020)),
        "policy_generator": "numpy.random.default_rng(plant_seed), as in the author's export",
        "model_id": kr.ACTOR_ID, "baseline": kr.STATIC_ID, "reference": kr.ORACLE_ID,
        "supervisor": {"max_faults": 8},
        "fault_injection": {"length_km": 50.0, "plant_seed": 950000, "periods": list(range(100, 108))},
        "author_seed_sets": {"training": "0-9999", "validation": "700000-", "test": "900000-900047"},
        "reproduction": {"export_episode": kr.EXAMPLE, "published_table": PUBLISHED_KEYRATE,
                         "published_seeds": list(range(900000, 900024)), "tolerance_mbit_s": 0.0005,
                         "policy_generator": "KeyRatePolicy(rng=0).rng per span, as the author's evaluation"},
        "primary_endpoint": "per-episode mean provable key rate",
        "hypothesis": "actor key rate higher than static commissioning, per span",
    },
    "sync": {
        "target_id": sy.TARGET_ID, "length_km": sy.TRAINED_SPAN_KM, "duration_s": sy.SYNC_DURATION_S,
        "gate_widths_ps": [200.0, 350.0, 500.0, 1000.0], "dark_count_rates_hz": [10.0, 1000000.0],
        "evaluation_seeds": list(range(600, 620)), "trajectory_seed_offset": sy.TRAJECTORY_SEED_OFFSET,
        "model_id": sy.MODEL_ID, "baselines": list(sy.BASELINE_IDS), "comparator": "baseline.alpha_beta",
        "warmup": "author's LearnedSync rule: alpha-beta tracker for the first seven updates",
        "author_seed_sets": {"training": "0-7", "test": "500-511"},
        "reproduction": {"published_table": PUBLISHED_SYNC, "published_seeds": list(range(500, 512)),
                         "column_order": SYNC_ORDER, "tolerance_mbit_s": 0.0005},
        "primary_endpoint": "per-run key rate left by the synchroniser",
        "hypothesis": "ridge key rate higher than the alpha-beta tracker, per gate width and dark rate",
    },
    "test": "exact one-sided paired sign-flip test", "multiplicity": "Holm within each family", "alpha": 0.05,
    "bootstrap": {"resamples": 10000, "seed": 20261022, "confidence": 0.95},
    "interpretation": "simulated closed loops on the author's plants; not physical validation or permission to actuate",
}
SOURCES = tuple(f"quantum_autopilot/{path}" for path in (
    "control_plane/qkd_campaign.py", "control_plane/keyrate_loop.py", "control_plane/sync_loop.py",
    "control_plane/comparative_evaluation.py", "model_intake/keyrate.py", "model_intake/inference.py",
    "model_intake/artifacts.py")) + ("qnet_qkd/qkd/keyrate.py", "qnet_qkd/qkd/timing.py",
                                     "qnet_qkd/physics/decoy.py", "qnet_qkd/qkd/session.py")


def load_protocol(path):
    with Path(path).open("rb") as stream:
        raw = stream.read(32769)
    if len(raw) > 32768:
        raise ValueError("protocol exceeds 32 KiB")
    parsed = json.loads(raw, object_pairs_hook=_unique, parse_constant=_constant)
    if json.dumps(parsed, sort_keys=True) != json.dumps(PROTOCOL, sort_keys=True):
        raise ValueError("protocol differs from frozen v1; create a separately reviewed version")
    return raw, PROTOCOL


def _span_key(span):
    return f"{span:g}"


# --------------------------------------------------------------------------
# Reproduction gates
# --------------------------------------------------------------------------

def reproduce_keyrate_table(model, protocol=PROTOCOL, progress=None):
    config = protocol["keyrate"]["reproduction"]
    rows = []
    for span_text, published in config["published_table"].items():
        span = float(span_text)
        env = kr.make_link(span)
        controllers = {kr.ACTOR_ID: kr.ActorController(model, kr.author_policy_generator()),
                       kr.STATIC_ID: kr.StaticController(), kr.ORACLE_ID: kr.OracleController()}
        for controller_id, controller in controllers.items():
            rates = np.array([kr.run_episode(span, seed, controller, env=env).mean_key_rate_hz
                              for seed in config["published_seeds"]]) / 1e6
            mean, std = float(rates.mean()), float(rates.std(ddof=1))
            expected = published[controller_id]
            rows.append({"length_km": span, "controller_id": controller_id, "mean_mbit_s": mean, "std_mbit_s": std,
                         "published_mean": expected[0], "published_std": expected[1],
                         "passed": abs(round(mean, 3) - expected[0]) <= config["tolerance_mbit_s"]
                         and abs(round(std, 3) - expected[1]) <= config["tolerance_mbit_s"]})
        if progress:
            progress(f"Key-rate table at {span:g} km: "
                     + ", ".join(f"{row['controller_id']} {row['mean_mbit_s']:.3f}" for row in rows[-3:]))
    return rows


def reproduce_sync_table(model, protocol=PROTOCOL, progress=None):
    config = protocol["sync"]
    published = config["reproduction"]["published_table"]
    widths = sorted({float(width) for table in published.values() for width in table})
    darks = [float(dark) for dark in published]
    results = {}
    for seed in config["reproduction"]["published_seeds"]:
        path = sy.trajectory(seed, config["length_km"])
        for width_ps in widths:
            predictors = {**sy.baselines(), sy.MODEL_ID: sy.ReplaySync(model)}
            for controller_id, predictor in predictors.items():
                record = sy.run(seed, controller_id, predictor, width_ps * 1e-12, darks, path=path)
                for dark, rate in record.key_rate_hz.items():
                    results.setdefault((dark, f"{width_ps:g}", controller_id), []).append(rate)
    rows = []
    for dark, table in published.items():
        for width, values in table.items():
            for controller_id, expected in zip(config["reproduction"]["column_order"], values):
                mean = float(np.mean(results[(f"{float(dark):g}", width, controller_id)])) / 1e6
                rows.append({"dark_count_rate_hz": float(dark), "gate_width_ps": float(width),
                             "controller_id": controller_id, "mean_mbit_s": mean, "published_mean": expected,
                             "passed": abs(round(mean, 3) - expected) <= config["reproduction"]["tolerance_mbit_s"]})
    if progress:
        progress(f"Synchroniser table: {sum(row['passed'] for row in rows)}/{len(rows)} published entries reproduced.")
    return rows


# --------------------------------------------------------------------------
# Statistics
# --------------------------------------------------------------------------

def _interval(generator, draws_fn, protocol):
    config = protocol["bootstrap"]
    values = draws_fn(generator, config["resamples"])
    lower = (1 - config["confidence"]) / 2
    return [float(np.nanquantile(values, lower)), float(np.nanquantile(values, 1 - lower))]


def compare(model_values, baseline_values, generator, protocol=PROTOCOL, reference_values=None):
    """Paired comparison where higher is better (key rate)."""
    model, baseline = np.asarray(model_values, dtype=float), np.asarray(baseline_values, dtype=float)
    test = paired_sign_flip_test(model - baseline, alternative="greater")

    def ratio(generator, resamples):
        draws = generator.integers(0, len(model), size=(resamples, len(model)))
        return model[draws].mean(axis=1) / baseline[draws].mean(axis=1)

    row = {"n_seeds": len(model), "wins": int(np.sum(model > baseline)), "ties": int(np.sum(model == baseline)),
           "model_mean": float(model.mean()), "baseline_mean": float(baseline.mean()),
           "model_to_baseline_ratio": float(model.mean() / baseline.mean()) if baseline.mean() > 0 else None,
           "ratio_ci95": _interval(generator, ratio, protocol) if baseline.mean() > 0 else None,
           "mean_difference": test["mean_difference"], "p_value": test["p_value"], "method": test["method"]}
    if reference_values is not None:
        reference = np.asarray(reference_values, dtype=float)

        def gap(generator, resamples):
            draws = generator.integers(0, len(model), size=(resamples, len(model)))
            closed = model[draws].mean(axis=1) - baseline[draws].mean(axis=1)
            available = reference[draws].mean(axis=1) - baseline[draws].mean(axis=1)
            return closed / np.where(available == 0, np.nan, available)

        row["reference_mean"] = float(reference.mean())
        row["gap_fraction_closed"] = float((model.mean() - baseline.mean()) / (reference.mean() - baseline.mean()))
        row["gap_fraction_ci95"] = _interval(generator, gap, protocol)
    return row


def holm(rows, protocol=PROTOCOL):
    for row, value in zip(rows, holm_adjust([row["p_value"] for row in rows])):
        row["holm_p_value"] = float(value)
        row["significant"] = bool(value < protocol["alpha"])
    return rows


# --------------------------------------------------------------------------
# Campaign
# --------------------------------------------------------------------------

class _Faulty:
    """Makes the wrapped controller's contract refuse on chosen periods (fault injection)."""

    def __init__(self, inner, periods):
        self.inner, self.periods, self.controller_id = inner, set(periods), inner.controller_id

    def decide(self, env, span_km):
        if env.step_index in self.periods:
            raise ValueError("injected contract refusal")
        return self.inner.decide(env, span_km)


def _export(text, path):
    with Path(path).open("xb") as stream:
        stream.write(text.encode("utf-8"))


def _source_hashes(root):
    return {path: hashlib.sha256((root / path).read_bytes()).hexdigest() for path in SOURCES}


def run_keyrate(model, archive, protocol=PROTOCOL, progress=None):
    config = protocol["keyrate"]
    records, per_span = [], {}
    for span in config["spans_km"]:
        env = kr.make_link(span)
        contract = kr.check_contract(model, archive, env, span)
        if contract["status"] != "admitted":
            raise ValueError(f"contract refused the actor at {span:g} km: {contract['reasons']}")
        values = {kr.ACTOR_ID: [], kr.STATIC_ID: [], kr.ORACLE_ID: []}
        for seed in config["evaluation_plant_seeds"]:
            controllers = {kr.ACTOR_ID: kr.ActorController(model, np.random.default_rng(seed)),
                           kr.STATIC_ID: kr.StaticController(), kr.ORACLE_ID: kr.OracleController()}
            for controller_id, controller in controllers.items():
                record = kr.run_episode(span, seed, controller, env=env, policy_seed=seed if controller_id == kr.ACTOR_ID else None,
                                        supervisor=kr.KeyRateSupervisorConfig(**config["supervisor"]),
                                        archive_sha256=archive.sha256 if controller_id == kr.ACTOR_ID else None)
                records.append(record.to_dict())
                values[controller_id].append(record.mean_key_rate_hz)
        per_span[_span_key(span)] = values
        if progress:
            progress(f"Key rate {span:g} km: actor {np.mean(values[kr.ACTOR_ID]) / 1e6:.3f}, static "
                     f"{np.mean(values[kr.STATIC_ID]) / 1e6:.3f}, oracle {np.mean(values[kr.ORACLE_ID]) / 1e6:.3f} Mbit/s")
    fault = config["fault_injection"]
    faulted = kr.run_episode(fault["length_km"], fault["plant_seed"],
                             _Faulty(kr.ActorController(model, np.random.default_rng(fault["plant_seed"])), fault["periods"]),
                             policy_seed=fault["plant_seed"], supervisor=kr.KeyRateSupervisorConfig(**config["supervisor"]),
                             archive_sha256=archive.sha256)
    return records, per_span, faulted.to_dict()


def run_sync(model, archive, protocol=PROTOCOL, progress=None):
    config = protocol["sync"]
    contract = sy.check_contract(archive, sy.make_gate(), config["length_km"])
    if contract["status"] != "admitted":
        raise ValueError(f"contract refused the synchroniser: {contract['reasons']}")
    records, values = [], {}
    for seed in config["evaluation_seeds"]:
        path = sy.trajectory(seed, config["length_km"])
        for width_ps in config["gate_widths_ps"]:
            predictors = {**sy.baselines(), sy.MODEL_ID: sy.ReplaySync(model)}
            for controller_id, predictor in predictors.items():
                record = sy.run(seed, controller_id, predictor, width_ps * 1e-12, config["dark_count_rates_hz"],
                                length_km=config["length_km"], path=path,
                                archive_sha256=archive.sha256 if controller_id == sy.MODEL_ID else None)
                records.append(record.to_dict())
                for dark, rate in record.key_rate_hz.items():
                    values.setdefault(f"{width_ps:g}|{dark}", {}).setdefault(controller_id, []).append(rate)
        if progress and (seed - config["evaluation_seeds"][0]) % 5 == 4:
            progress(f"Synchroniser: {seed - config['evaluation_seeds'][0] + 1}/{len(config['evaluation_seeds'])} seeds.")
    return records, values, contract


def run_campaign(archive_path, protocol_path, output, *, progress=None):
    raw, protocol = load_protocol(protocol_path)
    root = Path(__file__).resolve().parents[2]
    archive = ReviewedArchive(archive_path, "qkd")
    manifest = {"protocol": protocol, "protocol_file_sha256": hashlib.sha256(raw).hexdigest(),
                "archive_sha256": archive.sha256, "expected_archive_sha256": PINS["qkd"],
                "source_sha256": _source_hashes(root)}
    output = Path(output)
    output.mkdir(parents=False, exist_ok=False)
    _export(json.dumps(manifest, indent=2, allow_nan=False), output / "frozen-protocol.json")

    actor, synchroniser = KeyRateReplayModel(archive), SyncReplayModel(archive)
    export_gate = kr.reproduce_export_episode(archive)
    if not export_gate["passed"]:
        raise ValueError("closed loop does not regenerate the exported key-rate episode; no campaign")
    if progress:
        progress("Exported 40-period key-rate episode regenerated: actions, settings and key bits identical.")
    keyrate_table = reproduce_keyrate_table(actor, protocol, progress)
    sync_table = reproduce_sync_table(synchroniser, protocol, progress)
    if not all(row["passed"] for row in keyrate_table + sync_table):
        raise ValueError("replay does not reproduce the published tables; no campaign")

    keyrate_records, keyrate_values, faulted = run_keyrate(actor, archive, protocol, progress)
    sync_records, sync_values, sync_contract = run_sync(synchroniser, archive, protocol, progress)
    artifacts = []
    for name, rows in (("keyrate-runs.jsonl", keyrate_records), ("sync-runs.jsonl", sync_records)):
        text = "".join(json.dumps(row, allow_nan=False, sort_keys=True) + "\n" for row in rows)
        _export(text, output / name)
        artifacts.append({"file": name, "records": len(rows), "sha256": hashlib.sha256(text.encode()).hexdigest()})

    if _source_hashes(root) != manifest["source_sha256"]:
        raise ValueError("source changed during campaign; no completion summary")
    generator = np.random.default_rng(protocol["bootstrap"]["seed"])
    keyrate_rows = holm([{"length_km": float(span), "model_id": kr.ACTOR_ID, "baseline": kr.STATIC_ID,
                          **compare(values[kr.ACTOR_ID], values[kr.STATIC_ID], generator, protocol,
                                    reference_values=values[kr.ORACLE_ID])}
                         for span, values in keyrate_values.items()], protocol)
    sync_rows = holm([{"gate_width_ps": float(key.split("|")[0]), "dark_count_rate_hz": float(key.split("|")[1]),
                       "model_id": sy.MODEL_ID, "baseline": protocol["sync"]["comparator"],
                       **compare(values[sy.MODEL_ID], values[protocol["sync"]["comparator"]], generator, protocol)}
                      for key, values in sync_values.items()], protocol)
    sync_means = {key: {controller_id: float(np.mean(rates)) for controller_id, rates in values.items()}
                  for key, values in sync_values.items()}
    actor_records = [row for row in keyrate_records if row["controller_id"] == kr.ACTOR_ID]
    summary = {
        "schema": "orion.qkd-closed-loop-results.v1", "status": "complete", "manifest": manifest,
        "reproduction": {"export_episode": export_gate, "keyrate_table": keyrate_table, "sync_table": sync_table},
        "keyrate": {"comparisons": keyrate_rows,
                    "zero_key_periods": {controller_id: int(sum(row["zero_key_periods"] for row in keyrate_records
                                                                if row["controller_id"] == controller_id))
                                         for controller_id in (kr.ACTOR_ID, kr.STATIC_ID, kr.ORACLE_ID)},
                    "proposals_verified": int(sum(row["proposals_verified"] for row in actor_records)),
                    "actor_periods": int(sum(row["periods"] for row in actor_records)),
                    "supervisor_faults": int(sum(row["faults"] for row in actor_records)),
                    "fault_injection": {key: faulted[key] for key in ("faults", "degraded_at_period", "mean_key_rate_hz",
                                                                       "proposals_verified", "periods")}},
        "sync": {"contract": sync_contract, "comparisons": sync_rows, "mean_key_rate_hz": sync_means},
        "artifacts": artifacts,
        "policy_activated": False, "physical_action_performed": False, "simulator_action_performed": True,
        "limitations": ["Author's simulated plants (decoy-state link, thermal delay drift), not recorded link data.",
                        "Key rates are the author's per-period provable rates on finite counts; no finite-key security proof.",
                        "The synchroniser is admitted only at 50 km, the span it was trained on.",
                        "The oracle reads the true channel and is a reference, not a deployable baseline."],
    }
    _export(json.dumps(summary, indent=2, allow_nan=False), output / "summary.json")
    return summary


__all__ = ["PROTOCOL", "load_protocol", "reproduce_keyrate_table", "reproduce_sync_table", "run_campaign"]
