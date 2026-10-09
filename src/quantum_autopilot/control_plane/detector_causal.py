"""Frozen causal-monitor campaign v1: the author's detector with features a real-time monitor could compute.

The author's feature pipeline (``qnet_qkd.qkd.detection._episode_features``) takes the
temperature rate with ``np.gradient``, a central difference: the feature of period *t*
uses the temperature of period *t + 1*. Two of the 17 features depend on it
(``temperature_rate`` and, through ``|dT/dt|``, ``error_temperature_correlation``);
every rolling feature is trailing. A monitor running in real time cannot see the next
period, so here every period's features are computed by the author's own function on
the log *up to that period* and the last row is kept — what a deployed monitor would
compute. Everything else (model, threshold, hysteresis, datasets, scoring) is the
detector campaign's.

Gate: the 15 lookahead-free features must be bitwise identical to the author's, and the
last period of every episode identical in all 17 (both use a one-sided difference
there). Then, on the detector campaign's two datasets, the deployed detector is scored
on both feature sets. Primary question, per dataset: does the share of event periods
flagged change (exact two-sided sign test over episodes with an event, Holm over two)?
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np

from ..model_intake.artifacts import PINS, ReviewedArchive
from ..model_intake.detector import SafeDetector
from ..model_intake.sync_session import _constant, _unique
from . import detector_campaign as base
from .comparative_evaluation import holm_adjust

LOOKAHEAD = ("temperature_rate", "error_temperature_correlation")
PROTOCOL = {
    "schema": "orion.detector-causal-protocol.v1", "model_id": "qkd.detector.boosting",
    "evidence": "simulated", "mode": "offline_monitoring",
    "question": "the author's temperature rate is a central difference (one period of lookahead in "
                "temperature_rate and error_temperature_correlation); a real-time monitor computes each period's "
                "features from the log up to that period",
    "causal_features": "qnet_qkd.qkd.detection._episode_features on frame[:t + 1], last row, for every period t",
    "datasets": {"seeds": [1000, 2000], "episodes_per_class": 24,
                 "source": "the detector campaign v1 (configs/optional_models/detector_evaluation_v1.json)"},
    "gate": "the 15 lookahead-free features bitwise equal to the author's; the last period of every episode equal "
            "in all 17",
    "deployed": base.DEPLOYED, "baseline": base.BASELINE,
    "primary": "per episode with an event (trainable classes): share of event-active periods flagged by the deployed "
               "detector, causal features against the author's",
    "test": "exact two-sided sign test over episodes (ties dropped)", "multiplicity": "Holm over the two datasets",
    "alpha": 0.05, "bootstrap": {"resamples": 2000, "seed": 20261022, "confidence": 0.95, "unit": "episode"},
    "interpretation": "simulated sessions of the author's plant; a monitor, never acting on alarms",
}
SOURCES = base.SOURCES + ("quantum_autopilot/control_plane/detector_causal.py",)


def load_protocol(path):
    with Path(path).open("rb") as stream:
        raw = stream.read(32769)
    if len(raw) > 32768:
        raise ValueError("protocol exceeds 32 KiB")
    parsed = json.loads(raw, object_pairs_hook=_unique, parse_constant=_constant)
    if json.dumps(parsed, sort_keys=True) != json.dumps(PROTOCOL, sort_keys=True):
        raise ValueError("protocol differs from frozen v1; create a separately reviewed version")
    return raw, PROTOCOL


def causal_features(frame):
    """Features in ``build_detection_features`` order, each row computed from the log up to its own period."""
    from qnet_qkd.qkd.detection import FEATURE_NAMES, _episode_features

    window = base.benchmark().FEATURE_WINDOW
    blocks, lookahead, gate = [], [], {"episodes": 0, "last_row_identical": True, "free_features_identical": True}
    free = [position for position, name in enumerate(FEATURE_NAMES) if name not in LOOKAHEAD]
    for _, episode in frame.groupby("episode", sort=True):
        episode = episode.sort_values("period")
        reference, index = _episode_features(episode, window)
        if len(index) == 0:
            continue
        periods = episode["period"].to_numpy()
        rows = []
        for end in np.flatnonzero(periods >= window.baseline_periods):
            partial, _ = _episode_features(episode.iloc[:end + 1], window)
            rows.append(partial[-1])
        causal = np.asarray(rows)
        gate["episodes"] += 1
        gate["last_row_identical"] &= bool(np.array_equal(causal[-1], reference[-1]))
        gate["free_features_identical"] &= bool(np.array_equal(causal[:, free], reference[:, free]))
        blocks.append(causal)
        lookahead.append(reference)
    return np.concatenate(blocks), np.concatenate(lookahead), gate


def evaluate_seed(safe, threshold, seed, protocol=PROTOCOL):
    from qnet_qkd.qkd.detection import evaluate_detector

    frame, digest = base.dataset(seed, protocol["datasets"]["episodes_per_class"])
    data = base.split(frame)
    causal, lookahead, gate = causal_features(frame)
    if not np.array_equal(lookahead, data["features"]):
        raise RuntimeError("per-episode features differ from build_detection_features")
    labels, index = data["labels"], data["index"]
    scored = ~data["untrainable"]
    universe = sorted(set(labels) | {"attack"})
    bootstrap = protocol["bootstrap"]
    generator = np.random.default_rng(bootstrap["seed"] + int(seed))
    lower = (1 - bootstrap["confidence"]) / 2
    result = {"seed": int(seed), "dataset_csv_sha256": digest, "gate": gate, "features": {}}
    stats = {}
    for name, features in (("lookahead", data["features"]), ("causal", causal)):
        chosen = base.detectors(safe, threshold)
        table = {}
        for detector_name, detector in chosen.items():
            report = evaluate_detector(detector, features, labels, index, scored, sustain=3, period_s=1.0)
            statistics = base.episode_statistics(base.sequence(detector, features, index), labels, index, scored,
                                                 universe)
            table[detector_name] = {**base.headline(statistics), "recall": report.recall,
                                    "detection_delay_s": report.detection_delay_s}
            if detector_name == base.DEPLOYED:
                stats[name] = statistics
        result["features"][name] = table
    event = [position for position, label in enumerate(stats["causal"]["episode_label"]) if label != "normal"]
    causal_share = stats["causal"]["event_flagged"][event]
    lookahead_share = stats["lookahead"]["event_flagged"][event]
    difference = causal_share - lookahead_share
    wins, losses, p_value = base._sign_test(difference)
    boot = [float(np.mean(difference[generator.integers(0, len(difference), len(difference))]))
            for _ in range(bootstrap["resamples"])]
    labels_of = stats["causal"]["episode_label"][event]
    result["primary"] = {"episodes_with_event": len(event), "causal_mean_flagged": float(np.mean(causal_share)),
                         "lookahead_mean_flagged": float(np.mean(lookahead_share)),
                         "mean_difference": float(np.mean(difference)),
                         "difference_ci95": [float(np.quantile(boot, lower)), float(np.quantile(boot, 1 - lower))],
                         "causal_higher": wins, "causal_lower": losses, "unchanged": int(len(event) - wins - losses),
                         "p_value": p_value}
    result["per_class_flagged"] = {label: {"causal": float(np.mean(causal_share[labels_of == label])),
                                           "lookahead": float(np.mean(lookahead_share[labels_of == label]))}
                                   for label in sorted(set(labels_of))}
    return result


def _evaluate(args):
    archive_path, seed, protocol = args
    from qnet_qkd.qkd.detection import QberThreshold

    archive = ReviewedArchive(archive_path, "qkd")
    safe = SafeDetector(archive=archive)
    frame, _ = base.dataset(0)
    data = base.split(frame)
    threshold = QberThreshold.calibrate(data["features"], data["labels"], data["trainable"], 0.01)
    return evaluate_seed(safe, threshold, seed, protocol)


def _export(text, path):
    with Path(path).open("xb") as stream:
        stream.write(text.encode("utf-8"))


def _hashes():
    return {path: hashlib.sha256((base.ROOT / "src" / path).read_bytes()).hexdigest() for path in SOURCES}


def run_campaign(archive_path, protocol_path, output, *, progress=None, workers=1):
    raw, protocol = load_protocol(protocol_path)
    archive = ReviewedArchive(archive_path, "qkd")
    manifest = {"protocol": protocol, "protocol_file_sha256": hashlib.sha256(raw).hexdigest(),
                "archive_sha256": archive.sha256, "expected_archive_sha256": PINS["qkd"], "source_sha256": _hashes()}
    output = Path(output)
    output.mkdir(parents=False, exist_ok=False)
    _export(json.dumps(manifest, indent=2, allow_nan=False), output / "frozen-protocol.json")
    tasks = [(archive_path, seed, protocol) for seed in protocol["datasets"]["seeds"]]
    if workers > 1:
        from concurrent.futures import ProcessPoolExecutor

        with ProcessPoolExecutor(max_workers=min(workers, len(tasks))) as pool:
            seeds = list(pool.map(_evaluate, tasks))
    else:
        seeds = [_evaluate(task) for task in tasks]
    if not all(row["gate"]["last_row_identical"] and row["gate"]["free_features_identical"] for row in seeds):
        _export(json.dumps({"status": "gate_failed", "gates": [row["gate"] for row in seeds]}, indent=2),
                output / "gate-failed.json")
        raise ValueError("causal features disagree with the author's where no lookahead is involved")
    for row, value in zip(seeds, holm_adjust([row["primary"]["p_value"] for row in seeds])):
        row["primary"]["holm_p_value"] = float(value)
        row["primary"]["significant"] = bool(value < protocol["alpha"])
        if progress:
            progress(f"Seed {row['seed']}: flagged {row['primary']['causal_mean_flagged']:.3f} causal vs "
                     f"{row['primary']['lookahead_mean_flagged']:.3f} with lookahead; Holm p {value:.2g}")
    if _hashes() != manifest["source_sha256"]:
        raise ValueError("source changed during campaign; no completion summary")
    summary = {"schema": "orion.detector-causal-results.v1", "status": "complete", "manifest": manifest,
               "evaluation": seeds, "policy_activated": False, "physical_action_performed": False,
               "limitations": ["The author's simulated sessions; the model was trained with the lookahead features "
                               "and is not retrained here."]}
    _export(json.dumps(summary, indent=2, allow_nan=True, default=float), output / "summary.json")
    return summary


__all__ = ["LOOKAHEAD", "PROTOCOL", "causal_features", "load_protocol", "run_campaign"]
