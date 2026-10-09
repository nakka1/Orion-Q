import numpy as np
import pandas as pd

from quantum_twin.evaluation.qkd_dose_order_falsification import (
    FIXED_DOSE_ORDER,
    MONOTONIC_BASELINES,
    QKDDoseOrderFalsificationConfig,
    build_monotonic_baseline_predictions,
    evaluate_joint_predictions,
    run_qkd_dose_order_falsification_campaign,
    write_qkd_dose_order_falsification_artifacts,
)
from quantum_twin.evaluation.qkd_multihead import (
    QKDMultiHeadConfig,
    run_qkd_multihead_campaign,
    write_qkd_multihead_artifacts,
)


def _write_sweeps(directory):
    directory.mkdir(parents=True)
    rows = []
    for distance in (0.0, 50.0):
        for voa in (2.0, 8.0):
            for power in (None, 3.0, 7.0, 9.0, 12.0):
                dose = 0.0 if power is None else 10.0 ** (power / 10.0)
                for repeat in range(2):
                    rows.append({
                        "Noise_dBm": "None" if power is None else power,
                        "Sweep_km": distance,
                        "VOA_dB": voa,
                        "QBER": 0.02 + distance * 2e-4 + voa * 1e-3 + dose * 1e-3,
                        "SecureKeyRate_bps": (
                            0.0
                            if power == 12.0 and voa == 8.0
                            else max(
                                220_000.0 - distance * 1_000.0 - voa * 5_000.0
                                - dose * 2_000.0 - repeat * 5.0,
                                1.0,
                            )
                        ),
                    })
    pd.DataFrame(rows).to_csv(directory / "qkd.csv", index=False)
    return directory


def _cells():
    rows = []
    for distance in (0.0, 50.0):
        for voa in (2.0, 8.0):
            stratum = f"{distance:g}km|voa={voa:g}"
            for power in (None, 3.0, 7.0, 9.0, 12.0):
                dose = 0.0 if power is None else 10.0 ** (power / 10.0)
                rows.append({
                    "stratum_id": stratum,
                    "noise_level": "baseline" if power is None else str(int(power)),
                    "secure_key_available": 1,
                    "log1p_secure_key_rate": 12.0 - 0.01 * dose - 0.02 * voa,
                    "qber_reported": 0.02 + 0.001 * dose + 0.001 * voa,
                    "fiber_length_km": distance,
                    "voa_attenuation_db": voa,
                    "external_signal_present": 0 if power is None else 1,
                    "external_signal_power_dbm": np.nan if power is None else power,
                })
    return pd.DataFrame(rows)


def test_fixed_power_order_recovers_a_monotonic_joint_order():
    predictions = build_monotonic_baseline_predictions(
        _cells(),
        config=QKDDoseOrderFalsificationConfig(bootstrap_replicates=20),
    )
    fixed = predictions.loc[predictions["model"].eq(FIXED_DOSE_ORDER)]
    metrics = evaluate_joint_predictions(fixed)
    assert np.allclose(metrics["pareto_jaccard"], 1.0)
    assert np.allclose(metrics["dominance_pair_accuracy"], 1.0)


def test_baselines_do_not_fit_on_the_held_out_stratum_targets():
    cells = _cells()
    config = QKDDoseOrderFalsificationConfig(bootstrap_replicates=20)
    original = build_monotonic_baseline_predictions(cells, config=config)
    changed = cells.copy()
    held_out = changed["stratum_id"].eq("0km|voa=2")
    changed.loc[held_out, "qber_reported"] += 100.0
    changed.loc[held_out, "log1p_secure_key_rate"] -= 100.0
    mutated = build_monotonic_baseline_predictions(changed, config=config)
    prediction_columns = [
        "model", "noise_level", "prediction_qber_reported",
        "prediction_log1p_secure_key_rate_given_available",
    ]
    original_fold = original.loc[
        original["stratum_id"].eq("0km|voa=2"), prediction_columns
    ].sort_values(["model", "noise_level"]).reset_index(drop=True)
    mutated_fold = mutated.loc[
        mutated["stratum_id"].eq("0km|voa=2"), prediction_columns
    ].sort_values(["model", "noise_level"]).reset_index(drop=True)
    pd.testing.assert_frame_equal(original_fold, mutated_fold)
    assert not original["test_targets_used_for_fit"].any()


def test_dose_order_campaign_writes_corrected_audit(tmp_path):
    qkd_result = run_qkd_multihead_campaign(
        _write_sweeps(tmp_path / "dataset"),
        config=QKDMultiHeadConfig(
            seeds=(7,), hidden_size=4, epochs=2, bootstrap_replicates=20
        ),
    )
    qkd_output = tmp_path / "qkd-output"
    write_qkd_multihead_artifacts(qkd_result, qkd_output)
    result = run_qkd_dose_order_falsification_campaign(
        qkd_output,
        config=QKDDoseOrderFalsificationConfig(bootstrap_replicates=20),
    )
    assert set(MONOTONIC_BASELINES).issubset(result.model_summary["model"])
    assert len(result.corrected_comparisons) == 16
    assert set(result.corrected_comparisons["hypothesis_family_size"]) == {16}
    assert result.input_integrity["test_target_rows_used_for_baseline_fit"] == 0
    assert result.input_integrity["frozen_neural_predictions_refit"] is False

    output = tmp_path / "dose-order-output"
    manifest = write_qkd_dose_order_falsification_artifacts(result, output)
    assert len(manifest["artifacts"]) == 6
    assert (output / "corrected_comparisons.csv").exists()
    assert (output / "input_integrity.json").exists()
