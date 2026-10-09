"""Safe (pickle-free) export of the author's QKD attack detector and the frozen detector campaign."""
import hashlib
import io
import json
import os
from pathlib import Path
import shutil

import numpy as np
import pandas as pd
import pytest

from quantum_autopilot.control_plane import detector_campaign as campaign
from quantum_autopilot.model_intake import detector as safe_detector
from quantum_autopilot.model_intake.artifacts import QKD_ROOT, ReviewedArchive

pytestmark = pytest.mark.unit
ROOT = Path(__file__).resolve().parents[1]
PROTOCOL = ROOT / "configs/optional_models/detector_evaluation_v1.json"
QKD_ZIP = os.environ.get("ORION_QKD_ARCHIVE", "")


def real_archive():
    if not QKD_ZIP or not Path(QKD_ZIP).exists():
        pytest.skip("pinned modelo_qkd_ml.zip not supplied (set ORION_QKD_ARCHIVE)")
    return ReviewedArchive(QKD_ZIP, "qkd")


def test_export_is_the_reviewed_one_and_records_its_proof():
    export = safe_detector.load_export()
    manifest = export.manifest
    assert export.npz_sha256 == safe_detector.EXPORT_NPZ_SHA256
    assert manifest["source_member_sha256"] == safe_detector.SOURCE_MEMBER_SHA256
    assert "byte-identical" in manifest["identity_proof"] and "never deserialised" in manifest["identity_proof"]
    assert manifest["numpy_traversal_max_deviation"] == 0.0 and manifest["trees"] == 2400
    assert manifest["classes"] == ["detector_blinding", "intercept_resend", "loss_event", "normal", "pns",
                                   "thermal_drift"]
    assert len(manifest["feature_names"]) == 17 and manifest["sustain_periods"] == 3
    with np.load(safe_detector.EXPORT_NPZ, allow_pickle=False) as data:   # no object arrays anywhere
        assert all(data[name].dtype != object for name in data.files)


def test_tampered_arrays_or_manifest_are_refused(tmp_path):
    npz, manifest = tmp_path / "d.npz", tmp_path / "d.json"
    shutil.copy(safe_detector.EXPORT_NPZ, npz)
    shutil.copy(safe_detector.EXPORT_JSON, manifest)
    raw = bytearray(npz.read_bytes())
    raw[-100] ^= 1
    npz.write_bytes(bytes(raw))
    with pytest.raises(ValueError):
        safe_detector.load_export(manifest, npz)
    shutil.copy(safe_detector.EXPORT_NPZ, npz)
    changed = json.loads(manifest.read_text(encoding="utf-8"))
    changed["model_id"] = "qkd.detector.other"
    manifest.write_text(json.dumps(changed), encoding="utf-8")
    with pytest.raises(ValueError, match="not the reviewed"):
        safe_detector.load_export(manifest, npz)


def test_probabilities_gate_and_hysteresis():
    detector = safe_detector.SafeDetector()
    rows = np.random.default_rng(3).normal(size=(64, 17)) * detector.scale + detector.mean
    proba = detector.probabilities(rows)
    assert proba.shape == (64, 6) and np.allclose(proba.sum(axis=1), 1.0) and (proba >= 0).all()
    ungated, gated = detector.predict(rows, gated=False), detector.predict(rows)
    attack = 1.0 - proba[:, detector.classes.index("normal")]
    assert np.array_equal(gated, np.where(attack < detector.attack_threshold, "normal", ungated))
    calls = np.array(["normal", "pns", "pns", "pns", "pns", "loss_event", "pns", "pns", "pns"])
    assert detector.sustained(calls).tolist() == ["normal"] * 3 + ["pns", "pns"] + ["normal"] * 3 + ["pns"]
    with pytest.raises(ValueError, match="17 detector features"):
        detector.probabilities(rows[:, :16])


def test_traversal_matches_a_hand_built_tree():
    arrays = {"tree_offsets": np.array([0, 3]), "tree_class": np.array([1]), "feature_idx": np.array([0, 0, 0]),
              "num_threshold": np.array([0.5, 0.0, 0.0]), "missing_go_to_left": np.array([1, 0, 0], dtype=np.uint8),
              "left": np.array([1, 0, 0]), "right": np.array([2, 0, 0]), "is_leaf": np.array([0, 1, 1], dtype=np.uint8),
              "value": np.array([0.0, -1.0, 2.0]), "baseline_prediction": np.array([0.1, 0.2])}
    raw = safe_detector.raw_predictions(arrays, np.array([[0.0], [0.5], [0.7], [np.nan]]), 2)
    assert raw[:, 0].tolist() == [0.1] * 4 and raw[:, 1].tolist() == [-0.8, -0.8, 2.2, -0.8]


def test_protocol_file_is_the_frozen_protocol(tmp_path):
    assert campaign.load_protocol(PROTOCOL) == (PROTOCOL.read_bytes(), campaign.PROTOCOL)
    changed = json.loads(PROTOCOL.read_text(encoding="utf-8"))
    changed["evaluation_dataset_seeds"] = [0, 1]
    path = tmp_path / "changed.json"
    path.write_text(json.dumps(changed), encoding="utf-8")
    with pytest.raises(ValueError, match="differs from frozen v1"):
        campaign.run_campaign("never-read.zip", path, tmp_path / "out")
    assert not (tmp_path / "out").exists()
    # Episode i of dataset s draws from generator s + i; the author's runs used 0-167.
    streams = {seed + i for seed in campaign.PROTOCOL["evaluation_dataset_seeds"]
               for i in range(7 * campaign.PROTOCOL["episodes_per_class"])}
    assert not streams & set(range(168)) and len(streams) == 2 * 168


def test_author_records_are_vendored_byte_for_byte():
    for name, digest in campaign.PROTOCOL["reproduction"]["records"].items():
        assert hashlib.sha256((ROOT / campaign.RECORDS / name).read_bytes()).hexdigest() == digest
    settings = json.loads((ROOT / campaign.RECORDS / "settings.json").read_text(encoding="utf-8"))
    export = safe_detector.load_export().manifest
    assert settings["attack_threshold"] == export["attack_threshold"]
    assert settings["feature_names"] == export["feature_names"] and settings["sustain"] == export["sustain_periods"]


def test_benchmark_is_the_authors_task5():
    from tests import author_scripts

    task5 = author_scripts.load("qnet_qkd", "task5")
    ours = campaign.benchmark()
    for name in ("LINK", "SETTINGS", "FEATURE_WINDOW", "UNTRAINABLE_LABELS", "LENGTHS_KM", "RESIDUAL_FRACTION"):
        assert getattr(ours, name) == getattr(task5, name)


def _toy():
    rng = np.random.default_rng(7)
    rows, labels, predicted = [], [], []
    for episode, label in enumerate(["normal", "pns", "loss_event", "pns", "normal", "loss_event"]):
        onset = np.nan if label == "normal" else 20.0
        for period in rng.permutation(40):
            active = label != "normal" and period >= onset
            rows.append({"episode": episode, "time_s": float(period), "period": period, "onset_s": onset,
                         "episode_label": label})
            labels.append(label if active else "normal")
            predicted.append(rng.choice(["normal", "pns", "loss_event", "attack"], p=[0.6, 0.2, 0.1, 0.1]))
    return pd.DataFrame(rows), np.array(labels), np.array(predicted)


def test_episode_statistics_reproduce_evaluate_detector():
    from qnet_qkd.qkd.detection import evaluate_detector

    index, labels, predicted = _toy()
    mask = index["episode"].to_numpy() != 5

    class Fixed:
        name = "fixed"

        def predict(self, features):
            return predicted[features[:, 0].astype(int)]

    features = np.arange(len(labels), dtype=float)[:, None]
    report = evaluate_detector(Fixed(), features, labels, index, mask)
    stats = campaign.episode_statistics(predicted, labels, index, mask, sorted(set(labels) | {"attack"}))
    point = campaign.headline(stats)
    for key in ("macro_f1", "false_alarm_rate", "false_alarms_per_hour", "precision_at_prior"):
        assert point[key] == pytest.approx(getattr(report, key), abs=1e-12)
    assert len(stats["episodes"]) == 5 and np.isnan(stats["event_flagged"][0])


def test_sign_test_is_exact():
    assert campaign._sign_test([1.0] * 10) == (10, 0, 2 / 1024)
    assert campaign._sign_test([1, -1, 0]) == (1, 1, 1.0)
    assert campaign._sign_test([0, 0]) == (0, 0, 1.0)


def test_exported_example_sessions_byte_for_byte():
    archive = real_archive()
    detector = safe_detector.SafeDetector(archive=archive)
    assert detector.archive_member_sha256 == safe_detector.SOURCE_MEMBER_SHA256
    for label in ("normal", "pns"):
        member = f"{QKD_ROOT}exemplos/detector_{label}_"
        public = pd.read_csv(io.BytesIO(archive.read(member + "entrada.csv")))
        out = io.StringIO()
        detector.classify_session(public).to_csv(
            out, index=False, float_format="%.6g", lineterminator="\n"
        )
        assert out.getvalue().encode() == archive.read(member + "saida.csv")


def test_bind_refuses_the_network_archive():
    class Other:
        kind = "network"

    with pytest.raises(ValueError, match="QKD archive"):
        safe_detector.bind_archive(safe_detector.load_export(), Other())


def test_results_summary_is_the_frozen_run():
    summary = json.loads((ROOT / "docs/results/orion_q_detector_evaluation_20260929_summary.json")
                         .read_text(encoding="utf-8"))
    assert summary["manifest"]["protocol_file_sha256"] == hashlib.sha256(PROTOCOL.read_bytes()).hexdigest()
    assert all(row["passed"] for row in summary["reproduction"]["examples"])
    assert all(gate["passed"] for gate in summary["reproduction"]["records"].values())
    assert summary["policy_activated"] is False and summary["physical_action_performed"] is False
    assert [row["seed"] for row in summary["evaluation"]] == campaign.PROTOCOL["evaluation_dataset_seeds"]
