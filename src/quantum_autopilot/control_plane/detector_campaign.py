"""Frozen detector campaign v1: the safe detector export on the author's QKD session plant.

The detector is a monitor, not a controller: there is no loop to close. What can be
validated is that ORION-Q's pickle-free replay *is* the author's detector, that the
session plant *is* the author's, and how the detector does on sessions nobody trained,
calibrated or tested on. Gates, in order (all must pass before anything is scored):

1. **Exported examples** (``modelo_qkd_ml.zip``, ``exemplos/detector_*``): the regenerated
   seed-0 dataset yields the seven example answer keys byte for byte and the inputs to the
   protocol tolerance (1e-12, relative to max(1, |value|) — the inputs include rates in
   millions of Hz; byte for byte on Linux, where the export was
   written — another libm may differ in the last bit of the simulated temperature); the safe
   detector's output, written as the export wrote it (``%.6g``), equals every
   ``*_saida.csv`` byte for byte; the example summary (``detector_resumo.json``) is
   recomputed exactly.
2. **The author's detector records** (``runs/detector`` of ``Qnet-QKD ML.zip``, vendored in
   ``external/qnet_qkd/runs/detector``): regenerate seed 0, split and calibrate as
   ``train_detector.py`` does, then reproduce, for the QBER threshold, boosting, gated
   boosting and gated boosting with hysteresis, every per-class row of ``scores.csv``,
   the beam-splitting table, the hardness table and the boosting confusion matrix, plus
   the calibrated ``threshold_sigma`` and ``attack_threshold`` of ``settings.json``.

Then two new datasets (base seeds 1000 and 2000; 24 episodes per class) are
scored with the same four detectors (the QBER threshold keeps its seed-0 calibration, as
a deployed alarm would). The generator gives episode *i* of dataset *s* the random
stream ``s + i``, so a dataset with base seed 1 would replay the author's training
episodes shifted by one; the evaluation streams (1000-1167, 2000-2167) are disjoint from
the author's (0-167). Primary hypothesis, per seed: on episodes with an event, the
deployed detector (gated boosting + hysteresis) flags a larger share of the event's
periods than the QBER threshold (exact two-sided sign test over episodes, Holm over the
two seeds). Everything else is descriptive, with episode-bootstrap intervals.
"""
from __future__ import annotations

from functools import lru_cache
import hashlib
import importlib.util
import io
import json
import math
from pathlib import Path
import sys

import numpy as np
import pandas as pd

from ..model_intake.artifacts import PINS, QKD_ROOT, ReviewedArchive
from ..model_intake.detector import SafeAttackDetector, SafeDetector
from ..model_intake.sync_session import _constant, _unique
from .comparative_evaluation import holm_adjust

ROOT = Path(__file__).resolve().parents[3]
RECORDS = "external/qnet_qkd/runs/detector"
EXAMPLES = ("normal", "thermal_drift", "loss_event", "intercept_resend", "pns", "detector_blinding", "beam_splitting")
DETECTORS = ("limiar de QBER", "gradient boosting", "gradient boosting + limiar", "boosting + limiar + histerese")
DEPLOYED, BASELINE = "boosting + limiar + histerese", "limiar de QBER"
#: export_models.py: what the session log carries that a receiver does not publish.
ANSWER_COLUMNS = ("label", "onset_s", "active", "strength", "true_misalignment", "true_transmittance",
                  "true_single_photon_yield", "severity", "profile", "thermal_cooccurrence")
SCORE_COLUMNS = ("recall", "detection_recall", "detection_delay_s", "false_alarm_rate", "sustained_false_alarm_rate",
                 "false_alarms_per_hour", "precision_at_prior", "prior", "macro_f1", "rows")
PROTOCOL = {
    "schema": "orion.detector-evaluation-protocol.v1", "model_id": "qkd.detector.boosting",
    "evidence": "simulated", "mode": "offline_monitoring",
    "model": "safe export src/quantum_autopilot/model_intake/derived/qkd_detector_boosting_v1.npz, proven equal to "
             "the pinned pickle by byte comparison (never unpickled); bound to the archive by the member's SHA-256",
    "plant": "qnet_qkd.datagen.qkd.generate_qkd_session_dataset at the author's benchmark (scripts/task5.py), "
             "read back from CSV as train_detector.py did",
    "reproduction": {
        "examples": [f"exemplos/detector_{label}_{part}.csv" for label in EXAMPLES
                     for part in ("entrada", "gabarito", "saida")] + ["exemplos/detector_resumo.json"],
        "example_probability_format": "%.6g",
        "records": {"scores.csv": "61d0966a01d4e7f3690646e2fbadda116b776c7192721126a97304987ace3a6a",
                    "beam_splitting.csv": "30a18b4a3f329881250ac812c399a5f3503e67f3b17cc2ff290614261f0b7e0c",
                    "hardness.csv": "1c079651ee857e2eda40f0a96a3f8ff67da9c5d0231d67e5346b567c806b2569",
                    "confusion.csv": "d22943d5a9a4711863fa921f084b82d3d5e1b5d2ac9ccb151c8bb3bf9c589871",
                    "settings.json": "b686e9c392a8c400e2114f2104609b02a3b7541e58fd61fd6ccabe1e079ca393"},
        "records_source": "Qnet-QKD ML.zip: qnet_autopilot_1/runs/detector (vendored in external/qnet_qkd/runs/detector)",
        "dataset_seed": 0, "tolerance": 1e-12},
    "evaluation_dataset_seeds": [1000, 2000], "episodes_per_class": 24,
    "author_seed_sets": {"dataset": "0 (fit, calibration and test episodes all inside it)",
                         "episode_generators": "dataset seed s gives episode i the generator seed s + i: the author's "
                                               "runs (runs/detector, runs/detector_slow) used 0-167; the evaluation "
                                               "uses 1000-1167 and 2000-2167, so no episode shares a random stream"},
    "detectors": list(DETECTORS), "deployed": DEPLOYED, "baseline": BASELINE,
    "primary": "per episode with an event (trainable classes): share of event-active periods flagged as an attack, "
               "deployed detector vs QBER threshold",
    "test": "exact two-sided sign test over episodes (ties dropped)",
    "multiplicity": "Holm over the evaluation seeds", "alpha": 0.05,
    "bootstrap": {"resamples": 2000, "seed": 20261022, "confidence": 0.95, "unit": "episode"},
    "interpretation": "simulated sessions of the author's plant; not field data; alarms are never acted on",
}
SOURCES = tuple(f"quantum_autopilot/{path}" for path in (
    "control_plane/detector_campaign.py", "model_intake/detector.py", "model_intake/artifacts.py",
    "model_intake/derived/qkd_detector_boosting_v1.npz", "model_intake/derived/qkd_detector_boosting_v1.json")) + (
    "qnet_qkd/qkd/detection.py", "qnet_qkd/qkd/session.py", "qnet_qkd/qkd/attacks.py", "qnet_qkd/datagen/qkd.py")


def load_protocol(path):
    with Path(path).open("rb") as stream:
        raw = stream.read(32769)
    if len(raw) > 32768:
        raise ValueError("protocol exceeds 32 KiB")
    parsed = json.loads(raw, object_pairs_hook=_unique, parse_constant=_constant)
    if json.dumps(parsed, sort_keys=True) != json.dumps(PROTOCOL, sort_keys=True):
        raise ValueError("protocol differs from frozen v1; create a separately reviewed version")
    return raw, PROTOCOL


@lru_cache(maxsize=1)
def benchmark():
    """The author's benchmark constants (``scripts/task5.py`` of the QKD tree), under a name of its own."""
    path = ROOT / "external" / "qnet_qkd" / "scripts" / "task5.py"
    spec = importlib.util.spec_from_file_location("orion_qnet_qkd_task5", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def dataset(seed, episodes=24):
    """The author's session generator, read back from CSV as the author's run did; returns (frame, csv sha)."""
    from qnet_qkd.datagen.qkd import generate_qkd_session_dataset

    t5 = benchmark()
    generated = generate_qkd_session_dataset(
        episodes_per_class=episodes, duration_s=t5.SESSION_DURATION_S, period_s=t5.PERIOD_S,
        baseline_periods=t5.BASELINE_PERIODS, lengths_km=t5.LENGTHS_KM, settings=t5.SETTINGS, link=t5.LINK,
        residual_fraction=t5.RESIDUAL_FRACTION, adversarial=True, seed=int(seed))
    buffer = io.StringIO()
    generated.frame.to_csv(buffer, index=False, lineterminator="\n")
    text = buffer.getvalue()
    return pd.read_csv(io.StringIO(text)), hashlib.sha256(text.encode()).hexdigest()


def split(frame):
    """train_detector.py: features, labels, index, episode split and the trainable/scored masks."""
    from qnet_qkd.qkd.detection import build_detection_features, episode_split

    t5 = benchmark()
    features, labels, index = build_detection_features(frame, t5.FEATURE_WINDOW)
    train, test = episode_split(index, 0.7, seed=t5.DETECTOR_SEED)
    untrainable = np.isin(index["episode_label"].to_numpy(), t5.UNTRAINABLE_LABELS)
    return {"features": features, "labels": labels, "index": index, "train": train, "test": test,
            "trainable": train & ~untrainable, "scored": test & ~untrainable, "untrainable": untrainable}


def detectors(safe, threshold):
    from qnet_qkd.qkd.detection import SustainedDetector

    gated = SafeAttackDetector(safe, gated=True, name="gradient boosting + limiar")
    return {BASELINE: threshold, "gradient boosting": SafeAttackDetector(safe, gated=False),
            "gradient boosting + limiar": gated,
            DEPLOYED: SustainedDetector(base=gated, periods=safe.sustain, name=DEPLOYED)}


def sequence(detector, features, index):
    return np.asarray(detector.predict_sequence(features, index) if hasattr(detector, "predict_sequence")
                      else detector.predict(features))


def _close(ours, theirs, tolerance):
    ours, theirs = float(ours), float(theirs)
    if math.isnan(ours) or math.isnan(theirs):
        return math.isnan(ours) and math.isnan(theirs)
    if math.isinf(ours) or math.isinf(theirs):
        return ours == theirs
    return abs(ours - theirs) <= tolerance


# ---------------------------------------------------------------------------
# gate 1: the exported examples
# ---------------------------------------------------------------------------

def _csv(frame, **options):
    buffer = io.StringIO()
    frame.to_csv(buffer, index=False, lineterminator="\n", **options)
    return buffer.getvalue().encode()


def _example_summary(stem, label, episode, log, result):
    """export_models.py's summary entry, recomputed."""
    onset = float(log["onset_s"].dropna().iloc[0]) if log["onset_s"].notna().any() else None
    after = result[result["time_s"] >= onset] if onset is not None else result
    alarms = result[result["alarm"] != "normal"]
    first = alarms["time_s"].min() if len(alarms) else None
    called = after["alarm"][after["alarm"] != "normal"]
    return {
        "exemplo": stem, "classe_verdadeira": label, "episódio": int(episode),
        "vão_km": float(log["length_km"].iloc[0]),
        "perfil": str(log["profile"].iloc[0]) if "profile" in log else None,
        "início_do_evento_s": onset,
        "primeiro_alarme_s": None if first is None or pd.isna(first) else float(first),
        "atraso_s": None if onset is None or first is None or pd.isna(first) else float(first - onset),
        "classe_mais_alarmada_após_início": called.value_counts().index[0] if len(called) else "normal",
        "fração_de_períodos_alarmados_após_início": float((after["alarm"] != "normal").mean()),
        "alarmes_antes_do_início": int(((result["time_s"] < onset) & (result["alarm"] != "normal")).sum())
        if onset is not None else int((result["alarm"] != "normal").sum()),
    }


def _same(ours, theirs):
    if ours is None or theirs is None:
        return ours is None and theirs is None
    if isinstance(ours, float) or isinstance(theirs, float):
        return _close(ours, theirs, 1e-12)
    return ours == theirs


def _input_error(ours, reference):
    """Largest |ours - reference| / max(1, |reference|) between two CSV tables with the same columns and rows.

    Relative above 1, absolute below: the example inputs mix temperatures, gains near 1e-2
    and rates near 2e6 Hz, whose last bit is 2e-10 Hz. Different columns, shapes, missing
    values or labels give infinity.
    """
    mine, theirs = pd.read_csv(io.BytesIO(ours)), pd.read_csv(io.BytesIO(reference))
    if list(mine.columns) != list(theirs.columns) or mine.shape != theirs.shape:
        return math.inf
    worst = 0.0
    for column in theirs.columns:
        if mine[column].dtype.kind in "fiu" and theirs[column].dtype.kind in "fiu":
            a, b = mine[column].to_numpy(float), theirs[column].to_numpy(float)
            if not np.array_equal(np.isnan(a), np.isnan(b)):
                return math.inf
            valid = ~np.isnan(b)
            difference = np.abs(a - b)[valid] / np.maximum(1.0, np.abs(b[valid]))
            worst = max(worst, float(difference.max()) if difference.size else 0.0)
        elif not mine[column].astype(str).equals(theirs[column].astype(str)):
            return math.inf
    return worst


def reproduce_examples(safe, archive, frame, data, protocol=None):
    index, test = data["index"], data["test"]
    test_episodes = set(index.loc[test, "episode"].unique())
    labels = frame.groupby("episode")["label"].first()
    published = json.loads(archive.read(f"{QKD_ROOT}exemplos/detector_resumo.json"))
    tolerance = (protocol or PROTOCOL)["reproduction"]["tolerance"]
    rows = []
    for position, label in enumerate(EXAMPLES):
        episode = sorted(e for e in test_episodes if labels.get(e) == label)[0]
        stem = f"detector_{label}"
        log = frame[frame["episode"] == episode].sort_values("period")
        public = log.drop(columns=[c for c in ANSWER_COLUMNS if c in log.columns])
        result = safe.classify_session(public.drop(columns=["episode"]))
        member = f"{QKD_ROOT}exemplos/{stem}_"
        inputs, reference = _csv(public.drop(columns=["episode"])), archive.read(member + "entrada.csv")
        row = {"example": label, "episode": int(episode), "periods": int(len(result)),
               "input_identical": inputs == reference,
               "input_max_relative_error": _input_error(inputs, reference),
               "answer_key_identical": _csv(log[["period", "time_s", "label", "onset_s", "strength", "profile",
                                                 "thermal_cooccurrence"]]) == archive.read(member + "gabarito.csv"),
               "output_identical": _csv(result, float_format="%.6g") == archive.read(member + "saida.csv"),
               "alarms": int((result["alarm"] != "normal").sum())}
        ours, theirs = _example_summary(stem, label, episode, log, result), published[position]
        row["summary_identical"] = list(ours) == list(theirs) and all(_same(ours[key], theirs[key]) for key in ours)
        row["passed"] = (row["input_max_relative_error"] <= tolerance
                         and all(row[key] for key in ("answer_key_identical", "output_identical", "summary_identical")))
        rows.append(row)
    return rows


# ---------------------------------------------------------------------------
# gate 2: the author's detector records
# ---------------------------------------------------------------------------

def _records(protocol):
    folder = ROOT / RECORDS
    raw = {name: (folder / name).read_bytes() for name in protocol["reproduction"]["records"]}
    for name, digest in protocol["reproduction"]["records"].items():
        if hashlib.sha256(raw[name]).hexdigest() != digest:
            raise ValueError(f"{RECORDS}/{name} differs from the author's record")
    return raw


def _score_rows(report):
    """train_detector.py's _report_frame."""
    return [{"detector": report.name, "label": label, "recall": report.recall[label],
             "detection_recall": report.detection_recall[label],
             "detection_delay_s": report.detection_delay_s.get(label, float("nan")),
             "false_alarm_rate": report.false_alarm_rate,
             "sustained_false_alarm_rate": report.sustained_false_alarm_rate,
             "false_alarms_per_hour": report.false_alarms_per_hour, "precision_at_prior": report.precision_at_prior,
             "prior": report.prior, "macro_f1": report.macro_f1, "rows": report.rows}
            for label in sorted(report.recall)]


def hardness_rows(frame, data, chosen):
    """train_detector.py's hard cases: step, ramp and intermittent attacks, with and without a thermal excursion."""
    index, labels, scored = data["index"], data["labels"], data["scored"]
    profile = frame.groupby("episode")["profile"].first().reindex(index["episode"].to_numpy()).to_numpy()
    cooccurrence = (frame.groupby("episode")["thermal_cooccurrence"].first()
                    .reindex(index["episode"].to_numpy()).to_numpy())
    attacked = scored & (labels != "normal")
    rows = []
    for detector in chosen:
        predicted = sequence(detector, data["features"], index)
        for name, selector in (("degrau", profile == "step"), ("rampa", profile == "ramp"),
                               ("intermitente", profile == "intermittent"),
                               ("com excursão térmica", cooccurrence > 0), ("sem excursão térmica", cooccurrence == 0)):
            here = attacked & selector
            if here.any():
                rows.append({"detector": detector.name, "case": name, "periods": int(here.sum()),
                             "recall": float(np.mean(predicted[here] == labels[here])),
                             "detected": float(np.mean(predicted[here] != "normal"))})
    return rows


def beam_rows(data, chosen, subset):
    """train_detector.py's beam-splitting table: how often an invisible attack is called an attack."""
    index = data["index"]
    active = (index["time_s"].to_numpy() >= index["onset_s"].to_numpy()) & subset
    rows = []
    for detector in chosen:
        predicted = np.asarray(detector.predict(data["features"][active]))
        rows.append({"detector": detector.name, "periods": int(active.sum()),
                     "called_normal": float(np.mean(predicted == "normal")),
                     "called_attack": float(np.mean(predicted != "normal"))})
    return rows


def _compare(ours, theirs, keys, numeric, tolerance):
    table = pd.read_csv(io.BytesIO(theirs), float_precision="round_trip")
    theirs = {tuple(row[key] for key in keys): row for row in table.to_dict("records")}
    checked, worst, missing = 0, 0.0, []
    for row in ours:
        reference = theirs.get(tuple(row[key] for key in keys))
        if reference is None:
            missing.append(tuple(row[key] for key in keys))
            continue
        for column in numeric:
            checked += 1
            if not _close(row[column], reference[column], tolerance):
                missing.append((*[row[key] for key in keys], column, row[column], reference[column]))
            elif all(map(math.isfinite, (float(row[column]), float(reference[column])))):
                worst = max(worst, abs(float(row[column]) - float(reference[column])))
    return {"rows": len(ours), "values_checked": checked, "max_absolute_error": worst,
            "mismatches": [list(map(str, item)) for item in missing[:10]], "passed": not missing and checked > 0}


def reproduce_records(safe, frame, data, protocol=PROTOCOL):
    from qnet_qkd.qkd.detection import QberThreshold, evaluate_detector

    raw = _records(protocol)
    tolerance = protocol["reproduction"]["tolerance"]
    threshold = QberThreshold.calibrate(data["features"], data["labels"], data["trainable"], 0.01)
    chosen = detectors(safe, threshold)
    scores, reports = [], {}
    for name, detector in chosen.items():
        reports[name] = evaluate_detector(detector, data["features"], data["labels"], data["index"], data["scored"],
                                          sustain=3, period_s=1.0)
        scores.extend(_score_rows(reports[name]))
    gates = {"scores.csv": _compare(scores, raw["scores.csv"], ("detector", "label"), SCORE_COLUMNS, tolerance)}
    beam = beam_rows(data, chosen.values(), data["test"] & data["untrainable"])
    gates["beam_splitting.csv"] = _compare(beam, raw["beam_splitting.csv"], ("detector",),
                                           ("periods", "called_normal", "called_attack"), tolerance)
    hard = hardness_rows(frame, data, [chosen[name] for name in DETECTORS[1:]])
    gates["hardness.csv"] = _compare(hard, raw["hardness.csv"], ("detector", "case"),
                                     ("periods", "recall", "detected"), tolerance)
    confusion = pd.DataFrame(evaluate_detector(chosen["gradient boosting"], data["features"], data["labels"],
                                               data["index"], data["scored"]).confusion).T
    buffer = io.StringIO()
    confusion.to_csv(buffer, lineterminator="\n")
    gates["confusion.csv"] = {"passed": buffer.getvalue().encode() == raw["confusion.csv"]}
    settings = json.loads(raw["settings.json"])
    gates["settings.json"] = {"threshold_sigma": threshold.sigma, "attack_threshold": safe.attack_threshold,
                              "passed": threshold.sigma == settings["threshold_sigma"]
                              and safe.attack_threshold == settings["attack_threshold"]
                              and settings["sustain"] == safe.sustain}
    return gates, threshold, reports


# ---------------------------------------------------------------------------
# evaluation on new seeds
# ---------------------------------------------------------------------------

def episode_statistics(predicted, labels, index, mask, universe):
    """Per-episode sufficient statistics of ``evaluate_detector``'s headline metrics."""
    rows = index.loc[mask].reset_index(drop=True)
    predicted, labels = np.asarray(predicted)[mask], np.asarray(labels)[mask]
    code = {name: position for position, name in enumerate(universe)}
    truth = np.array([code[value] for value in labels])
    guess = np.array([code[value] for value in predicted])
    episodes, inverse = np.unique(rows["episode"].to_numpy(), return_inverse=True)
    confusion = np.zeros((len(episodes), len(universe), len(universe)), dtype=np.int64)
    np.add.at(confusion, (inverse, truth, guess), 1)
    onsets = np.zeros(len(episodes), dtype=np.int64)
    healthy = np.zeros(len(episodes), dtype=np.int64)
    flagged = np.full(len(episodes), np.nan)
    episode_label = np.empty(len(episodes), dtype=object)
    for position, (episode, group) in enumerate(rows.groupby("episode", sort=True)):
        if episode != episodes[position]:
            raise RuntimeError("episode order")
        order = group.sort_values("period").index.to_numpy()
        calls, clean = predicted[order] != "normal", labels[order] == "normal"
        healthy[position] = int(clean.sum())
        previous = np.concatenate([[False], calls[:-1]])
        onsets[position] = int(np.sum(clean & calls & ~previous))
        if (~clean).any():
            flagged[position] = float(np.mean(calls[~clean]))
        episode_label[position] = group["episode_label"].iloc[0]
    return {"episodes": episodes, "episode_label": episode_label, "confusion": confusion, "onsets": onsets,
            "healthy": healthy, "event_flagged": flagged, "universe": tuple(universe)}


def headline(stats, take=None, prior=0.01, period_s=1.0):
    take = np.arange(len(stats["episodes"])) if take is None else take
    matrix = stats["confusion"][take].sum(axis=0).astype(float)
    normal = stats["universe"].index("normal")
    f1 = []
    for label in range(len(stats["universe"])):
        if matrix[label].sum() == 0:
            continue
        hit = matrix[label, label]
        if hit == 0:
            f1.append(0.0)
            continue
        precision, sensitivity = hit / matrix[:, label].sum(), hit / matrix[label].sum()
        f1.append(2 * precision * sensitivity / (precision + sensitivity))
    false_alarm = (matrix[normal].sum() - matrix[normal, normal]) / matrix[normal].sum()
    attack = np.delete(matrix, normal, axis=0)
    detection = (attack.sum() - attack[:, normal].sum()) / attack.sum()
    true, false = prior * detection, (1 - prior) * false_alarm
    return {"macro_f1": float(np.mean(f1)), "false_alarm_rate": float(false_alarm),
            "false_alarms_per_hour": float(stats["onsets"][take].sum()
                                           / (stats["healthy"][take].sum() * period_s / 3600)),
            "detection_rate": float(detection), "precision_at_prior": float(true / (true + false))}


def _sign_test(differences):
    differences = np.asarray(differences, dtype=float)
    wins, losses = int(np.sum(differences > 0)), int(np.sum(differences < 0))
    n = wins + losses
    if n == 0:
        return wins, losses, 1.0
    tail = sum(math.comb(n, k) for k in range(0, min(wins, losses) + 1)) / 2 ** n
    return wins, losses, min(1.0, 2 * tail)


def evaluate_seed(safe, threshold, seed, protocol=PROTOCOL, author=None):
    from qnet_qkd.qkd.detection import evaluate_detector

    frame, digest = dataset(seed, protocol["episodes_per_class"])
    data = split(frame)
    features, labels, index = data["features"], data["labels"], data["index"]
    scored = ~data["untrainable"]           # every episode is new: all of them are held out
    universe = sorted(set(labels) | {"attack"})
    chosen = detectors(safe, threshold)
    bootstrap = protocol["bootstrap"]
    generator = np.random.default_rng(bootstrap["seed"] + int(seed))
    stats = {name: episode_statistics(sequence(detector, features, index), labels, index, scored, universe)
             for name, detector in chosen.items()}
    count = len(stats[DEPLOYED]["episodes"])
    draws = [generator.integers(0, count, count) for _ in range(bootstrap["resamples"])]
    lower = (1 - bootstrap["confidence"]) / 2
    table = {}
    for name, detector in chosen.items():
        report = evaluate_detector(detector, features, labels, index, scored, sustain=3, period_s=1.0)
        point = headline(stats[name])
        for key in ("macro_f1", "false_alarm_rate", "false_alarms_per_hour", "precision_at_prior"):
            if not _close(point[key], getattr(report, key), 1e-12):
                raise RuntimeError(f"episode statistics disagree with evaluate_detector on {key}")
        resampled = [headline(stats[name], take) for take in draws]
        table[name] = {**point, "recall": report.recall, "detection_recall": report.detection_recall,
                       "detection_delay_s": report.detection_delay_s,
                       "sustained_false_alarm_rate": report.sustained_false_alarm_rate, "rows": report.rows,
                       "ci95": {key: [float(np.quantile([r[key] for r in resampled], lower)),
                                      float(np.quantile([r[key] for r in resampled], 1 - lower))] for key in point}}
        if author is not None and name in author:
            table[name]["author_seed0_test"] = author[name]
            table[name]["author_value_inside_ci95"] = {
                key: bool(table[name]["ci95"][key][0] <= author[name][key] <= table[name]["ci95"][key][1])
                for key in author[name]}
    beam = beam_rows(data, chosen.values(), data["untrainable"])
    event = [position for position, label in enumerate(stats[DEPLOYED]["episode_label"]) if label != "normal"]
    ours, theirs = stats[DEPLOYED]["event_flagged"][event], stats[BASELINE]["event_flagged"][event]
    difference = ours - theirs
    wins, losses, p_value = _sign_test(difference)
    boot = [float(np.mean(difference[generator.integers(0, len(difference), len(difference))]))
            for _ in range(bootstrap["resamples"])]
    labels_of = stats[DEPLOYED]["episode_label"][event]
    per_class = {label: {"episodes": int(np.sum(labels_of == label)),
                         "deployed": float(np.mean(ours[labels_of == label])),
                         "threshold": float(np.mean(theirs[labels_of == label])),
                         "wins": int(np.sum(difference[labels_of == label] > 0)),
                         "losses": int(np.sum(difference[labels_of == label] < 0))}
                 for label in sorted(set(labels_of))}
    return {"seed": int(seed), "dataset_csv_sha256": digest, "rows": int(len(frame)), "episodes_scored": count,
            "detectors": table, "beam_splitting": beam,
            "primary": {"episodes_with_event": len(event), "deployed_mean_flagged": float(np.mean(ours)),
                        "threshold_mean_flagged": float(np.mean(theirs)), "mean_difference": float(np.mean(difference)),
                        "difference_ci95": [float(np.quantile(boot, lower)), float(np.quantile(boot, 1 - lower))],
                        "wins": wins, "losses": losses, "p_value": p_value},
            "per_class_flagged": per_class}


def _export(text, path):
    with Path(path).open("xb") as stream:
        stream.write(text.encode("utf-8"))


def _hashes():
    return {path: hashlib.sha256((ROOT / "src" / path).read_bytes()).hexdigest() for path in SOURCES}


def run_campaign(archive_path, protocol_path, output, *, progress=None):
    raw, protocol = load_protocol(protocol_path)
    archive = ReviewedArchive(archive_path, "qkd")
    manifest = {"protocol": protocol, "protocol_file_sha256": hashlib.sha256(raw).hexdigest(),
                "archive_sha256": archive.sha256, "expected_archive_sha256": PINS["qkd"], "source_sha256": _hashes()}
    output = Path(output)
    output.mkdir(parents=False, exist_ok=False)
    _export(json.dumps(manifest, indent=2, allow_nan=False), output / "frozen-protocol.json")
    say = progress or (lambda message: None)

    safe = SafeDetector(archive=archive)   # binds to the archive's pickle by hash; never loads it
    frame, digest = dataset(protocol["reproduction"]["dataset_seed"])
    data = split(frame)
    examples = reproduce_examples(safe, archive, frame, data, protocol)
    say(f"Exported examples: {sum(row['passed'] for row in examples)}/{len(examples)} sessions reproduced "
        f"(answer key, output and summary identical; input within "
        f"{max(row['input_max_relative_error'] for row in examples):.1e}).")
    gates, threshold, reports = reproduce_records(safe, frame, data, protocol)
    say("Author's records: " + ", ".join(f"{name} {'ok' if gate['passed'] else 'FAILED'}"
                                         for name, gate in gates.items())
        + f" ({gates['scores.csv']['values_checked']} score values, max error "
          f"{gates['scores.csv']['max_absolute_error']:.1e}).")
    if not all(row["passed"] for row in examples) or not all(gate["passed"] for gate in gates.values()):
        _export(json.dumps({"status": "reproduction_failed", "examples": examples, "records": gates},
                           indent=2, default=str), output / "reproduction-failed.json")
        raise ValueError("safe detector does not reproduce the author's results; no evaluation")
    author = {name: {"macro_f1": report.macro_f1, "false_alarm_rate": report.false_alarm_rate,
                     "false_alarms_per_hour": report.false_alarms_per_hour,
                     "precision_at_prior": report.precision_at_prior} for name, report in reports.items()}

    seeds = []
    for seed in protocol["evaluation_dataset_seeds"]:
        seeds.append(evaluate_seed(safe, threshold, seed, protocol, author))
        row = seeds[-1]
        say(f"Seed {seed}: deployed flags {row['primary']['deployed_mean_flagged']:.3f} of event periods vs "
            f"threshold {row['primary']['threshold_mean_flagged']:.3f} ({row['primary']['wins']} wins, "
            f"{row['primary']['losses']} losses); macro F1 {row['detectors'][DEPLOYED]['macro_f1']:.3f}.")
    for row, value in zip(seeds, holm_adjust([row["primary"]["p_value"] for row in seeds])):
        row["primary"]["holm_p_value"] = float(value)
        row["primary"]["significant"] = bool(value < protocol["alpha"])
    if _hashes() != manifest["source_sha256"]:
        raise ValueError("source changed during campaign; no completion summary")
    summary = {
        "schema": "orion.detector-evaluation-results.v1", "status": "complete", "manifest": manifest,
        "safe_export": {"npz_sha256": safe.export.npz_sha256, "archive_member_sha256": safe.archive_member_sha256,
                        "rebuild": safe.export.manifest.get("rebuild")},
        "reproduction": {"examples": examples, "records": gates, "dataset_seed_0_csv_sha256": digest},
        "threshold_sigma": float(threshold.sigma), "evaluation": seeds,
        "policy_activated": False, "physical_action_performed": False,
        "limitations": ["The author's simulated sessions; no labelled field data with attacks is public.",
                        "One event per episode, at the author's benchmark link and span lengths.",
                        "A monitor: alarms are scored, never acted on."],
    }
    _export(json.dumps(summary, indent=2, allow_nan=True, default=float), output / "summary.json")
    return summary


__all__ = ["DETECTORS", "PROTOCOL", "dataset", "episode_statistics", "headline", "load_protocol",
           "reproduce_examples", "reproduce_records", "run_campaign", "split"]
