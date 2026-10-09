"""QKD attack detector from a safe, reviewed export; numpy only, never pickle.

The pinned ``modelo_qkd_ml.zip`` carries the detector as a pickle, which ORION-Q
refuses to deserialise. ``tools/export_detector_safe.py`` rebuilds it with the
author's own pipeline and proves identity by byte comparison of the serialised
model with the archive member (the member itself is never unpickled); the trees
are then stored here as plain arrays. This module loads those arrays with
``allow_pickle=False``, checks their pinned hash and structure, and can bind to a
reviewed archive by comparing the pickle member's SHA-256 — again without
deserialising it.

Scope: offline, simulated sessions. The detector classifies; it does not act.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import io
import json
from pathlib import Path
import zipfile

import numpy as np

DERIVED = Path(__file__).with_name("derived")
EXPORT_JSON = DERIVED / "qkd_detector_boosting_v1.json"
EXPORT_NPZ = DERIVED / "qkd_detector_boosting_v1.npz"
MODEL_ID = "qkd.detector.boosting"
#: Pinned identities: the reviewed safe arrays, and the archive member they were proven equal to.
EXPORT_NPZ_SHA256 = "5a7442e906cae5f5c00246a42d3009b68ca60e752fcff34f409486766d1f68fe"
SOURCE_MEMBER_SHA256 = "900d4d16e0790fafd10d06281c9619b350889349df0eff78996c408c4cc5a3e1"
ARRAYS = {"tree_offsets": "i", "tree_class": "i", "feature_idx": "i", "num_threshold": "f",
          "missing_go_to_left": "u", "left": "i", "right": "i", "is_leaf": "u", "value": "f",
          "baseline_prediction": "f"}


def softmax(raw):
    shifted = raw - np.max(raw, axis=1, keepdims=True)
    exponentiated = np.exp(shifted)
    return exponentiated / np.sum(exponentiated, axis=1, keepdims=True)


def raw_predictions(arrays, scaled, n_classes):
    """Baseline plus every tree, summed in the fitted order (iteration, class)."""
    scaled = np.asarray(scaled, dtype=np.float64)
    rows = np.arange(len(scaled))
    raw = np.tile(np.asarray(arrays["baseline_prediction"], dtype=np.float64), (len(scaled), 1))
    offsets, classes = arrays["tree_offsets"], arrays["tree_class"]
    feature, threshold, missing_left = arrays["feature_idx"], arrays["num_threshold"], arrays["missing_go_to_left"]
    left, right, leaf, value = arrays["left"], arrays["right"], arrays["is_leaf"], arrays["value"]
    for tree in range(len(classes)):
        base = int(offsets[tree])
        node = np.zeros(len(scaled), dtype=np.int64)
        while True:
            at = base + node
            open_ = leaf[at] == 0
            if not open_.any():
                break
            which = rows[open_]
            position = at[open_]
            x = scaled[which, feature[position]]
            go_left = np.where(np.isnan(x), missing_left[position] == 1, x <= threshold[position])
            node[open_] = np.where(go_left, left[position], right[position])
        raw[:, int(classes[tree])] += value[base + node]
    if n_classes != raw.shape[1]:
        raise ValueError("class count differs from the export")
    return raw


@dataclass(frozen=True)
class DetectorExport:
    manifest: dict
    arrays: dict
    npz_sha256: str


def load_export(json_path=EXPORT_JSON, npz_path=EXPORT_NPZ) -> DetectorExport:
    manifest = json.loads(Path(json_path).read_text(encoding="utf-8"))
    raw = Path(npz_path).read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if manifest.get("schema") != "orion.safe-model-export.v1" or manifest.get("model_id") != MODEL_ID:
        raise ValueError("not the reviewed detector export")
    if digest != manifest["npz_sha256"] or digest != EXPORT_NPZ_SHA256 \
            or manifest.get("source_member_sha256") != SOURCE_MEMBER_SHA256:
        raise ValueError("detector arrays differ from the reviewed export")
    with np.load(io.BytesIO(raw), allow_pickle=False) as data:
        if set(data.files) != set(ARRAYS):
            raise ValueError("unexpected detector arrays")
        arrays = {name: data[name] for name in ARRAYS}
    for name, kind in ARRAYS.items():
        if arrays[name].dtype.kind != kind or arrays[name].ndim != 1:
            raise ValueError(f"invalid detector array {name}")
    n_classes = len(manifest["classes"])
    offsets = arrays["tree_offsets"]
    nodes = len(arrays["value"])
    if (offsets[0] != 0 or offsets[-1] != nodes or np.any(np.diff(offsets) < 1)
            or len(arrays["tree_class"]) != len(offsets) - 1 or len(arrays["baseline_prediction"]) != n_classes
            or np.any(arrays["tree_class"] < 0) or np.any(arrays["tree_class"] >= n_classes)
            or not np.isfinite(arrays["value"]).all() or not np.isfinite(arrays["baseline_prediction"]).all()):
        raise ValueError("inconsistent detector trees")
    sizes = np.repeat(np.diff(offsets), np.diff(offsets))
    internal = arrays["is_leaf"] == 0
    features = len(manifest["feature_names"])
    if (np.any(arrays["feature_idx"][internal] < 0) or np.any(arrays["feature_idx"][internal] >= features)
            or np.any(arrays["left"][internal] <= 0) or np.any(arrays["right"][internal] <= 0)
            or np.any(arrays["left"][internal] >= sizes[internal]) or np.any(arrays["right"][internal] >= sizes[internal])):
        raise ValueError("detector tree links out of range")
    return DetectorExport(manifest, arrays, digest)


def bind_archive(export: DetectorExport, archive) -> str:
    """The reviewed archive must hold exactly the pickle the export was proven equal to (hashed, not loaded)."""
    if archive.kind != "qkd":
        raise ValueError("QKD archive required")
    with zipfile.ZipFile(io.BytesIO(archive._raw)) as members:
        digest = hashlib.sha256(members.read(export.manifest["source_member"])).hexdigest()
    if digest != export.manifest["source_member_sha256"]:
        raise ValueError("archive's detector differs from the model the safe export reproduces")
    return digest


class SafeDetector:
    """Gated gradient boosting (+ hysteresis), identical in decisions to the author's detector."""

    def __init__(self, export: DetectorExport | None = None, archive=None):
        self.export = export or load_export()
        manifest = self.export.manifest
        self.classes = tuple(manifest["classes"])
        self.feature_names = tuple(manifest["feature_names"])
        self.mean = np.asarray(manifest["standardiser_mean"], dtype=np.float64)
        self.scale = np.asarray(manifest["standardiser_scale"], dtype=np.float64)
        self.attack_threshold = float(manifest["attack_threshold"])
        self.sustain = int(manifest["sustain_periods"])
        self.window, self.baseline_periods = int(manifest["window"]), int(manifest["baseline_periods"])
        self.archive_member_sha256 = None if archive is None else bind_archive(self.export, archive)
        self.archive_sha256 = None if archive is None else archive.sha256
        self.name = "gradient boosting + limiar"
        self._cache = {}

    def probabilities(self, features):
        features = np.ascontiguousarray(features, dtype=np.float64)
        if features.ndim != 2 or features.shape[1] != len(self.feature_names):
            raise ValueError("17 detector features per row required")
        # A pure function of the rows: the last few results are kept, keyed by the rows' bytes.
        key = (features.shape, hashlib.sha256(features.tobytes()).digest())
        if key not in self._cache:
            if len(self._cache) >= 4:
                self._cache.pop(next(iter(self._cache)))
            self._cache[key] = softmax(raw_predictions(self.export.arrays, (features - self.mean) / self.scale,
                                                       len(self.classes)))
        return self._cache[key].copy()

    def predict(self, features, *, gated=True):
        proba = self.probabilities(features)
        best = np.asarray(self.classes, dtype=object)[np.argmax(proba, axis=1)]
        if not gated or self.attack_threshold <= 0.0:
            return best.astype(str)
        attack_mass = 1.0 - proba[:, self.classes.index("normal")]
        quiet = (best != "normal") & (attack_mass < self.attack_threshold)
        return np.where(quiet, "normal", best).astype(str)

    def sustained(self, calls):
        calls = np.asarray(calls, dtype=object)
        out = np.full(len(calls), "normal", dtype=object)
        run_label, run_length = None, 0
        for position, call in enumerate(calls):
            if call == run_label:
                run_length += 1
            else:
                run_label, run_length = call, 1
            if call != "normal" and run_length >= self.sustain:
                out[position] = call
        return out.astype(str)

    def session_features(self, frame):
        """The author's per-period features (qnet_qkd), from the public log only."""
        from qnet_qkd.qkd.detection import FeatureWindow, build_detection_features

        public = frame.copy()
        public["episode"], public["label"], public["onset_s"] = 0, "normal", np.nan
        features, _, index = build_detection_features(public, FeatureWindow(window=self.window,
                                                                            baseline_periods=self.baseline_periods))
        return features, index[["period", "time_s"]].reset_index(drop=True)

    def classify_session(self, frame):
        """One row per scored period: argmax, attack probability, gated call, alarm, class probabilities."""
        features, index = self.session_features(frame)
        proba = self.probabilities(features)
        out = index.copy()
        out["argmax"] = np.asarray(self.classes, dtype=object)[np.argmax(proba, axis=1)]
        out["attack_probability"] = 1.0 - proba[:, self.classes.index("normal")]
        out["per_period"] = self.predict(features)
        out["alarm"] = self.sustained(out["per_period"].to_numpy())
        for position, label in enumerate(self.classes):
            out[f"p_{label}"] = proba[:, position]
        return out


class SafeAttackDetector:
    """Adapter with the author's ``AttackDetector`` interface, for the author's scoring functions."""

    def __init__(self, detector: SafeDetector, *, gated=True, name=None):
        self.detector, self.gated = detector, gated
        self.name = name or ("gradient boosting + limiar" if gated else "gradient boosting")
        self.feature_names = detector.feature_names
        self.classes = detector.classes
        self.attack_threshold = detector.attack_threshold if gated else 0.0

    def predict(self, features):
        return self.detector.predict(features, gated=self.gated)

    def attack_probability(self, features):
        return 1.0 - self.detector.probabilities(features)[:, self.classes.index("normal")]

    def importances(self):
        return None


__all__ = ["MODEL_ID", "SafeAttackDetector", "SafeDetector", "bind_archive", "load_export", "raw_predictions",
           "softmax"]
