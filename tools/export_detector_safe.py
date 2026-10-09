"""Rebuild the author's QKD attack detector from the author's own pipeline and export it without pickle.

The pinned ``modelo_qkd_ml.zip`` ships the detector only as ``detector_boosting.pkl``,
which ORION-Q refuses to deserialise. This tool regenerates it instead: the author's
session generator (seed 0, 24 episodes per class, the benchmark link), the author's
feature pipeline, episode split and ``fit_boosted`` (seed 0), and the author's
false-alarm calibration of the decision threshold — exactly ``scripts/train_detector.py``.
It then proves identity **without unpickling the author's file**: the retrained model,
serialised the way the export did (``pickle.dump``), must be byte-identical to the
archive member, and the standardiser, classes and threshold must equal the exported
``detector_config.json``. Only then are the trees written as plain arrays (NPZ, no
object arrays) plus a JSON manifest, and a numpy traversal is checked against
scikit-learn on every held-out row.

Byte identity needs the author's environment: Python 3.11, numpy 2.4.4, pandas 3.0.2,
scikit-learn 1.8.0 (``detector_config.json`` records the scikit-learn version).

    python tools/export_detector_safe.py --qkd-zip modelo_qkd_ml.zip
"""
import argparse
from copy import deepcopy
import hashlib
import io
import json
from pathlib import Path
import pickle
import sys
import zipfile

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "external" / "qnet_qkd" / "scripts"))

from task5 import (BASELINE_PERIODS, DATASET_SEED, DETECTOR_SEED, FEATURE_WINDOW,  # noqa: E402
                   LENGTHS_KM, LINK, PERIOD_S, RESIDUAL_FRACTION, SESSION_DURATION_S, SETTINGS,
                   UNTRAINABLE_LABELS)
from qnet_qkd.datagen.qkd import generate_qkd_session_dataset  # noqa: E402
from qnet_qkd.qkd.detection import build_detection_features, episode_split, fit_boosted  # noqa: E402

MEMBER = "modelo_qkd_ml/modelos/detector_boosting.pkl"
CONFIG = "modelo_qkd_ml/modelos/detector_config.json"
OUT = ROOT / "src" / "quantum_autopilot" / "model_intake" / "derived"


def author_split(frame):
    """train_detector.py, verbatim in effect: features, split, fit and calibration masks."""
    features, labels, index = build_detection_features(frame, FEATURE_WINDOW)
    train, test = episode_split(index, 0.7, seed=DETECTOR_SEED)
    trainable = train & ~np.isin(index["episode_label"].to_numpy(), UNTRAINABLE_LABELS)
    episodes = index["episode"].to_numpy()
    shuffled = np.array(np.unique(episodes[trainable]), copy=True)
    np.random.default_rng(DETECTOR_SEED).shuffle(shuffled)
    cut = max(int(round(0.8 * len(shuffled))), 1)
    fit_mask = trainable & np.isin(episodes, shuffled[:cut])
    calibration_mask = trainable & np.isin(episodes, shuffled[cut:])
    return features, labels, index, train, test, fit_mask, calibration_mask


def author_dataset(seed=DATASET_SEED, episodes=24, scratch=None):
    dataset = generate_qkd_session_dataset(
        episodes_per_class=episodes, duration_s=SESSION_DURATION_S, period_s=PERIOD_S,
        baseline_periods=BASELINE_PERIODS, lengths_km=LENGTHS_KM, settings=SETTINGS, link=LINK,
        residual_fraction=RESIDUAL_FRACTION, adversarial=True, seed=seed)
    buffer = io.StringIO()
    dataset.frame.to_csv(buffer, index=False)   # the author's run read its dataset back from CSV
    text = buffer.getvalue()
    return pd.read_csv(io.StringIO(text)), hashlib.sha256(text.encode()).hexdigest()


def tree_arrays(model):
    """Every tree of the fitted HistGradientBoostingClassifier as flat numeric arrays."""
    trees = [(iteration, k, predictor.nodes) for iteration, per_class in enumerate(model._predictors)
             for k, predictor in enumerate(per_class)]
    if any(nodes["is_categorical"].any() for _, _, nodes in trees):
        raise ValueError("categorical splits are not part of the reviewed export")
    offsets = np.cumsum([0] + [len(nodes) for _, _, nodes in trees]).astype(np.int64)
    stack = np.concatenate([nodes for _, _, nodes in trees])
    return {
        "tree_offsets": offsets,
        "tree_class": np.array([k for _, k, _ in trees], dtype=np.int32),
        "feature_idx": stack["feature_idx"].astype(np.int32),
        "num_threshold": stack["num_threshold"].astype(np.float64),
        "missing_go_to_left": stack["missing_go_to_left"].astype(np.uint8),
        "left": stack["left"].astype(np.int32),
        "right": stack["right"].astype(np.int32),
        "is_leaf": stack["is_leaf"].astype(np.uint8),
        "value": stack["value"].astype(np.float64),
        "baseline_prediction": np.asarray(model._baseline_prediction, dtype=np.float64).reshape(-1),
    }


def fit_stage(scratch):
    """Child process: train_detector.py fit, written to our own scratch file (the author wrote detectors.pkl)."""
    frame, dataset_sha256 = author_dataset()
    features, labels, index, train, test, fit_mask, calibration_mask = author_split(frame)
    boosted = fit_boosted(features, labels, fit_mask, seed=DETECTOR_SEED)
    gated = deepcopy(boosted)
    gated.calibrate_threshold(features, labels, calibration_mask, 0.005)
    with open(scratch, "wb") as handle:
        pickle.dump(gated, handle)
    Path(str(scratch) + ".json").write_text(json.dumps({
        "dataset_csv_sha256": dataset_sha256, "dataset_rows": int(len(frame)), "fit_rows": int(fit_mask.sum()),
        "calibration_rows": int(calibration_mask.sum())}), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--qkd-zip")
    parser.add_argument("--fit-stage", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.fit_stage:
        return fit_stage(args.fit_stage)
    if not args.qkd_zip:
        parser.error("--qkd-zip is required")
    import subprocess
    import tempfile
    import sklearn

    raw_zip = Path(args.qkd_zip).read_bytes()
    with zipfile.ZipFile(io.BytesIO(raw_zip)) as archive:
        reference = archive.read(MEMBER)          # bytes only; never unpickled
        config = json.loads(archive.read(CONFIG))

    # Two processes, like the author's train_detector.py and export_models.py: the fitted
    # detector is written by one and read back by the other before the model is serialised.
    with tempfile.TemporaryDirectory() as scratch_dir:
        scratch = Path(scratch_dir) / "detectors.pkl"
        subprocess.run([sys.executable, __file__, "--fit-stage", str(scratch)], check=True)
        facts = json.loads(Path(str(scratch) + ".json").read_text(encoding="utf-8"))
        with open(scratch, "rb") as handle:       # our own file, written by the child above
            reloaded = pickle.load(handle)
    frame, dataset_sha256 = author_dataset()
    if dataset_sha256 != facts["dataset_csv_sha256"]:
        raise SystemExit("dataset regeneration is not deterministic")
    features, labels, index, train, test, fit_mask, calibration_mask = author_split(frame)
    gated = reloaded
    rebuilt = pickle.dumps(reloaded.model)
    checks = {
        "pickle_bytes_identical": rebuilt == reference,
        "standardiser_mean": list(gated.standardiser.mean) == config["standardiser_mean"],
        "standardiser_scale": list(gated.standardiser.scale) == config["standardiser_scale"],
        "classes": list(gated.classes) == config["classes"],
        "attack_threshold": float(gated.attack_threshold) == config["attack_threshold"],
    }
    print(json.dumps(checks, indent=1))
    if not all(checks.values()):
        raise SystemExit("rebuilt detector differs from the reviewed export; nothing written "
                         f"(scikit-learn {sklearn.__version__}; the export used {config['sklearn_version']})")

    arrays = tree_arrays(reloaded.model)
    # Loaded by path: the rebuild environment need not import the whole ORION-Q package.
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "orion_detector", ROOT / "src" / "quantum_autopilot" / "model_intake" / "detector.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["orion_detector"] = module
    spec.loader.exec_module(module)
    raw_predictions, softmax = module.raw_predictions, module.softmax

    scaled = gated.standardiser.transform(features[test])
    ours = softmax(raw_predictions(arrays, scaled, len(gated.classes)))
    theirs = gated.model.predict_proba(scaled)
    deviation = float(np.max(np.abs(ours - theirs)))
    print("numpy traversal vs scikit-learn on", int(test.sum()), "held-out rows: max |dp| =", deviation)
    if deviation > 1e-12 or not np.array_equal(np.argmax(ours, axis=1), np.argmax(theirs, axis=1)):
        raise SystemExit("numpy traversal disagrees with scikit-learn; nothing written")

    OUT.mkdir(parents=True, exist_ok=True)
    buffer = io.BytesIO()
    np.savez_compressed(buffer, **arrays)
    npz = buffer.getvalue()
    (OUT / "qkd_detector_boosting_v1.npz").write_bytes(npz)
    manifest = {
        "schema": "orion.safe-model-export.v1", "model_id": "qkd.detector.boosting",
        "format": "flat HistGradientBoosting trees (numeric splits only), float64 values, no object arrays",
        "npz_sha256": hashlib.sha256(npz).hexdigest(),
        "source_archive_sha256": hashlib.sha256(raw_zip).hexdigest(),
        "source_member": MEMBER, "source_member_sha256": hashlib.sha256(reference).hexdigest(),
        "identity_proof": "retrained with the author's pipeline; pickle.dumps(model) byte-identical to the member, "
                          "which was never deserialised",
        "rebuild": {"dataset_seed": DATASET_SEED, "detector_seed": DETECTOR_SEED, "dataset_rows": int(len(frame)),
                    "dataset_csv_sha256": dataset_sha256, "fit_rows": int(fit_mask.sum()),
                    "calibration_rows": int(calibration_mask.sum()), "false_alarm_target": 0.005,
                    "scikit_learn": sklearn.__version__, "numpy": np.__version__, "pandas": pd.__version__,
                    "python": sys.version.split()[0], "script": "tools/export_detector_safe.py"},
        "classes": list(gated.classes), "feature_names": config["feature_names"],
        "standardiser_mean": config["standardiser_mean"], "standardiser_scale": config["standardiser_scale"],
        "attack_threshold": config["attack_threshold"], "sustain_periods": config["sustain_periods"],
        "window": config["window"], "baseline_periods": config["baseline_periods"], "period_s": config["period_s"],
        "iterations": len(gated.model._predictors), "trees": int(len(arrays["tree_class"])),
        "nodes": int(len(arrays["value"])), "numpy_traversal_max_deviation": deviation,
    }
    (OUT / "qkd_detector_boosting_v1.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
                                                        encoding="utf-8")
    print("wrote", OUT / "qkd_detector_boosting_v1.npz", manifest["npz_sha256"])


if __name__ == "__main__":
    main()
