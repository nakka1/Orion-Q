"""The detector evaluated with features a real-time monitor could compute (campaign v1)."""
import hashlib
import json
from pathlib import Path

import numpy as np
import pytest

from quantum_autopilot.control_plane import detector_causal as campaign
from quantum_autopilot.control_plane import detector_campaign as base

pytestmark = pytest.mark.unit
ROOT = Path(__file__).resolve().parents[1]
PROTOCOL = ROOT / "configs/optional_models/detector_causal_v1.json"
SUMMARY = ROOT / "docs/results/orion_q_detector_causal_20260930_summary.json"


@pytest.fixture(scope="module")
def short_session():
    from qnet_qkd.datagen.qkd import generate_qkd_session_dataset

    t5 = base.benchmark()
    return generate_qkd_session_dataset(episodes_per_class=1, duration_s=150.0, period_s=1.0, baseline_periods=60,
                                        lengths_km=(35.0,), labels=("thermal_drift",), settings=t5.SETTINGS,
                                        link=t5.LINK, residual_fraction=0.2, adversarial=True, seed=4242).frame


def test_protocol_file_is_the_frozen_protocol(tmp_path):
    assert campaign.load_protocol(PROTOCOL) == (PROTOCOL.read_bytes(), campaign.PROTOCOL)
    changed = json.loads(PROTOCOL.read_text(encoding="utf-8"))
    changed["datasets"]["seeds"] = [1, 2]
    path = tmp_path / "changed.json"
    path.write_text(json.dumps(changed), encoding="utf-8")
    with pytest.raises(ValueError, match="differs from frozen v1"):
        campaign.run_campaign("never-read.zip", path, tmp_path / "out")
    assert campaign.PROTOCOL["datasets"]["seeds"] == base.PROTOCOL["evaluation_dataset_seeds"]


def test_only_the_temperature_rate_features_look_ahead(short_session):
    from qnet_qkd.qkd.detection import FEATURE_NAMES, build_detection_features

    causal, lookahead, gate = campaign.causal_features(short_session)
    reference, _, _ = build_detection_features(short_session, base.benchmark().FEATURE_WINDOW)
    assert np.array_equal(lookahead, reference) and causal.shape == reference.shape
    assert gate == {"episodes": 1, "last_row_identical": True, "free_features_identical": True}
    changed = {FEATURE_NAMES[k] for k in range(len(FEATURE_NAMES)) if not np.array_equal(causal[:, k], reference[:, k])}
    assert changed and changed <= set(campaign.LOOKAHEAD)


def test_results_summary_is_the_frozen_run():
    if not SUMMARY.exists():
        pytest.skip("campaign summary not generated yet")
    summary = json.loads(SUMMARY.read_text(encoding="utf-8"))
    assert summary["manifest"]["protocol_file_sha256"] == hashlib.sha256(PROTOCOL.read_bytes()).hexdigest()
    assert all(row["gate"]["last_row_identical"] and row["gate"]["free_features_identical"]
               for row in summary["evaluation"])
    assert summary["policy_activated"] is False and summary["physical_action_performed"] is False


def test_author_features_reproduce_the_detector_campaign():
    if not SUMMARY.exists():
        pytest.skip("campaign summary not generated yet")
    causal = json.loads(SUMMARY.read_text(encoding="utf-8"))["evaluation"]
    detector = json.loads((ROOT / "docs/results/orion_q_detector_evaluation_20260929_summary.json")
                          .read_text(encoding="utf-8"))["evaluation"]
    for ours, theirs in zip(causal, detector, strict=True):
        assert ours["dataset_csv_sha256"] == theirs["dataset_csv_sha256"]
        for name, row in ours["features"]["lookahead"].items():
            assert {key: row[key] for key in ("macro_f1", "false_alarms_per_hour", "detection_rate")} == {
                key: theirs["detectors"][name][key] for key in ("macro_f1", "false_alarms_per_hour", "detection_rate")}


def test_example_input_gate_tolerates_only_last_bit_differences():
    reference = b"period,temperature_c,label\n1,20.000000000000004,normal\n2,21.5,normal\n"
    assert base._input_error(reference, reference) == 0.0
    last_bit = b"period,temperature_c,label\n1,20.000000000000007,normal\n2,21.5,normal\n"
    assert 0.0 < base._input_error(last_bit, reference) <= 1e-12
    assert base._input_error(b"period,temperature_c,label\n1,20.1,normal\n2,21.5,normal\n", reference) > 1e-12
    assert base._input_error(b"period,temperature_c,label\n1,20.0,attack\n2,21.5,normal\n", reference) == float("inf")
    assert base._input_error(b"period,label\n1,normal\n2,normal\n", reference) == float("inf")
    rate = b"period,key_rate_hz\n1,1989961.5399713996\n"
    windows = b"period,key_rate_hz\n1,1989961.5399714014\n"     # 1.9e-9 Hz apart: the last bit at 2e6 Hz
    assert 0.0 < base._input_error(windows, rate) <= 1e-12
