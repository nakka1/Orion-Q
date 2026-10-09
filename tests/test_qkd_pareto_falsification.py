import numpy as np
import pandas as pd

from quantum_twin.evaluation.qkd_multihead import (
    QKDMultiHeadConfig,
    run_qkd_multihead_campaign,
    write_qkd_multihead_artifacts,
)
from quantum_twin.evaluation.qkd_pareto_falsification import (
    QKDParetoFalsificationConfig,
    joint_order_metrics,
    run_qkd_pareto_falsification_campaign,
    write_qkd_pareto_falsification_artifacts,
)


def _write_sweeps(directory):
    directory.mkdir(parents=True)
    rows = []
    for distance in (0.0, 50.0):
        for voa in (2.0, 8.0):
            for power in (None, 3.0, 7.0, 9.0, 12.0):
                condition = 0.0 if power is None else power / 12.0
                for repeat in range(2):
                    rows.append({
                        "Noise_dBm": "None" if power is None else power,
                        "Sweep_km": distance,
                        "VOA_dB": voa,
                        "QBER": 0.02 + distance * 2e-4 + voa * 1e-3 + 0.03 * condition,
                        "SecureKeyRate_bps": max(
                            (180_000.0 - distance * 1_000.0 - voa * 5_000.0)
                            * (1.0 - condition) - repeat * 5.0,
                            0.0,
                        ),
                    })
    pd.DataFrame(rows).to_csv(directory / "qkd.csv", index=False)
    return directory


def test_joint_order_metrics_detect_correct_and_reversed_ordering():
    qber = np.asarray([0.1, 0.2, 0.3])
    skr = np.asarray([3.0, 2.0, 1.0])
    aligned = joint_order_metrics(qber, skr, qber, skr)
    reversed_order = joint_order_metrics(qber, skr, qber[::-1], skr[::-1])
    assert aligned == (1.0, 1.0)
    assert reversed_order == (0.0, 0.0)


def test_falsification_campaign_randomizes_and_holds_conditions_out(tmp_path):
    qkd_result = run_qkd_multihead_campaign(
        _write_sweeps(tmp_path / "dataset"),
        config=QKDMultiHeadConfig(
            seeds=(7,), hidden_size=4, epochs=2, bootstrap_replicates=20
        ),
    )
    qkd_output = tmp_path / "qkd-output"
    write_qkd_multihead_artifacts(qkd_result, qkd_output)

    result = run_qkd_pareto_falsification_campaign(
        qkd_output,
        config=QKDParetoFalsificationConfig(
            seeds=(7,), hidden_size=4, epochs=2,
            randomization_replicates=99, bootstrap_replicates=20,
        ),
    )
    assert len(result.randomization_summary) == 4
    assert len(result.randomization_null_distribution) == 198
    assert set(result.randomization_summary["hypothesis_family_size"]) == {4}
    assert set(result.power_holdout_predictions["held_out_condition"]) == {
        "baseline", "3", "7", "9", "12",
    }
    assert set(result.power_holdout_comparisons["hypothesis_family_size"]) == {9}
    assert "significant_negative_improvement" in result.power_holdout_comparisons
    assert result.input_integrity["input_manifest_verified"] is True

    output = tmp_path / "falsification-output"
    manifest = write_qkd_pareto_falsification_artifacts(result, output)
    assert len(manifest["artifacts"]) == 7
    assert (output / "randomization_null_distribution.csv").exists()
    assert (output / "power_holdout_comparisons.csv").exists()
