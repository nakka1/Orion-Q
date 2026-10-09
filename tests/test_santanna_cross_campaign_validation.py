import json

import numpy as np
import pandas as pd
import pytest

from quantum_twin.evaluation.optical_campaign import add_causal_temporal_features, sha256_file
from quantum_twin.evaluation.optical_transfer import (
    confirmatory_transfer_tests,
    hard_train_calibration_split,
    run_santanna_cross_campaign_validation,
    validate_protocol_datasets,
    write_transfer_artifacts,
)


def _forecast_frame(rows_per_device=100, horizon=3):
    rows = []
    for offset, device in enumerate(["SPO1", "SPO2"]):
        values = 20.0 + offset + np.arange(rows_per_device) * 0.01
        for step in range(rows_per_device - horizon):
            timestamp = pd.Timestamp("2026-01-01", tz="UTC") + pd.Timedelta(seconds=step)
            rows.append({
                "device_id": device,
                "timestamp_utc": timestamp,
                "target_timestamp_utc": timestamp + pd.Timedelta(seconds=horizon),
                "osnr_db": values[step],
                "pre_fec_ber": 1e-6 * np.exp(-step / 100.0),
                "target_osnr_db": values[step + horizon],
            })
    return pd.DataFrame(rows)


def _raw_campaign(start, rows_per_device=120, soft=False):
    rows = []
    for offset, device in enumerate(["SPO1/18/11", "SPO2/18/11"]):
        for step in range(rows_per_device):
            base = 20.0 + offset + 0.01 * step
            osnr = base + (0.35 * np.sin(step / 6.0) if soft else 0.1 * np.sin(step / 9.0))
            rows.append({
                "Timestamp": start + step * 4,
                "Type": "Devices",
                "ID": device,
                "BER": 1e-5 * np.exp(-(step + offset) / 120.0),
                "OSNR": osnr,
                "InputPower": np.nan,
                "OutputPower": np.nan,
                "Failure": 0,
            })
    return pd.DataFrame(rows)


def test_hard_train_calibration_split_embargoes_future_labels():
    engineered, _ = add_causal_temporal_features(
        _forecast_frame(), target="osnr_db", lag_steps=(1, 2), rolling_windows=(3,)
    )
    train_indices, calibration_indices, manifest = hard_train_calibration_split(
        engineered, calibration_fraction=0.2, purge_steps=6
    )
    train, calibration = engineered.loc[train_indices], engineered.loc[calibration_indices]
    for device in ["SPO1", "SPO2"]:
        assert (
            train.loc[train["device_id"] == device, "target_timestamp_utc"].max()
            < calibration.loc[calibration["device_id"] == device, "timestamp_utc"].min()
        )
        assert manifest[device]["purged_rows"] == 6


def test_protocol_hash_mismatch_stops_before_modeling(tmp_path):
    hard, soft = tmp_path / "hard.csv", tmp_path / "soft.csv"
    hard.write_text("hard", encoding="utf-8")
    soft.write_text("soft", encoding="utf-8")
    protocol = {
        "discovery": {"dataset_sha256": "0" * 64},
        "confirmation": {
            "dataset_sha256": sha256_file(soft),
            "must_not_select_or_tune_on_confirmation_data": True,
        },
    }
    with pytest.raises(ValueError, match="HardFailure SHA-256 mismatch"):
        validate_protocol_datasets(protocol, hard, soft)


def test_confirmatory_test_uses_only_predeclared_model_and_corrected_family():
    rows = []
    for target in ["osnr_db", "pre_fec_ber"]:
        for step in range(80):
            y_true = float(step % 7)
            for model, loss in [("persistence", 1.0), ("ridge", 0.2), ("kalman", 0.0)]:
                rows.append({
                    "target": target,
                    "target_unit": "dB" if target == "osnr_db" else "log10(BER)",
                    "horizon_steps": 1,
                    "device_id": "SPO1",
                    "timestamp_utc": pd.Timestamp("2026-01-01", tz="UTC")
                    + pd.Timedelta(seconds=step),
                    "target_timestamp_utc": pd.Timestamp("2026-01-01", tz="UTC")
                    + pd.Timedelta(seconds=step + 1),
                    "model": model,
                    "primary_loss": loss,
                    "y_true": y_true,
                })
    protocol = {
        "tasks": [
            {"target": "osnr_db", "target_unit": "dB", "horizon_steps": 1,
             "selected_model": "persistence", "discovery_selected_loss": 1.0},
            {"target": "pre_fec_ber", "target_unit": "log10(BER)", "horizon_steps": 1,
             "selected_model": "ridge", "discovery_selected_loss": 0.5},
        ],
        "inference_protocol": {
            "familywise_alpha": 0.05,
            "bootstrap_block_length": 8,
            "bootstrap_replicates": 200,
            "fit_seeds": [7],
        },
    }
    result = confirmatory_transfer_tests(pd.DataFrame(rows), protocol)
    baseline = result.loc[result["target"] == "osnr_db"].iloc[0]
    transfer = result.loc[result["target"] == "pre_fec_ber"].iloc[0]
    assert baseline["confirmatory_status"] == "baseline_predeclared_no_superiority_test"
    assert transfer["predeclared_model"] == "ridge"
    assert transfer["transfer_superiority_confirmed"]
    assert transfer["ci_low"] > 0.0


def test_end_to_end_cross_campaign_writes_confirmatory_artifacts(tmp_path):
    hard_csv, soft_csv = tmp_path / "HardFailure_dataset.csv", tmp_path / "SoftFailure_dataset.csv"
    _raw_campaign(1_700_000_000, soft=False).to_csv(hard_csv, index=False)
    _raw_campaign(1_800_000_000, soft=True).to_csv(soft_csv, index=False)
    protocol = {
        "protocol_version": 1,
        "protocol_id": "test-frozen-protocol",
        "discovery": {"dataset_sha256": sha256_file(hard_csv)},
        "confirmation": {
            "dataset_sha256": sha256_file(soft_csv),
            "must_not_select_or_tune_on_confirmation_data": True,
        },
        "feature_protocol": {
            "lag_steps": [1, 2],
            "rolling_windows": [3],
            "ber_floor": 1e-12,
            "hard_calibration_fraction": 0.2,
            "requested_purge_steps": 2,
            "soft_warmup_steps": 3,
        },
        "inference_protocol": {
            "fit_seeds": [7],
            "bootstrap_replicates": 100,
            "bootstrap_block_length": 5,
            "familywise_alpha": 0.05,
            "conformal_alpha": 0.1,
        },
        "tasks": [
            {"target": "osnr_db", "target_unit": "dB", "horizon_steps": 1,
             "selected_model": "persistence", "discovery_selected_loss": 0.2,
             "discovery_persistence_loss": 0.2},
            {"target": "osnr_db", "target_unit": "dB", "horizon_steps": 3,
             "selected_model": "ridge", "discovery_selected_loss": 0.1,
             "discovery_persistence_loss": 0.2},
        ],
    }
    protocol_path = tmp_path / "protocol.json"
    protocol_path.write_text(json.dumps(protocol), encoding="utf-8")

    result = run_santanna_cross_campaign_validation(hard_csv, soft_csv, protocol_path)
    assert set(result.confirmatory_tests["predeclared_model"]) == {"persistence", "ridge"}
    assert set(result.external_predictions["model"]) == {
        "persistence", "ewma", "kalman", "ridge", "hist_gradient_boosting"
    }
    assert result.external_predictions["fit_note"].str.contains(
        "HardFailure|baseline", regex=True
    ).all()
    assert not result.domain_shift.empty
    assert not result.conformal_coverage.empty
    assert not result.exploratory_ranking.empty
    assert result.exploratory_ranking["inference_scope"].str.startswith("exploratory_only").all()

    output = tmp_path / "transfer"
    manifest = write_transfer_artifacts(
        result, output, include_all_predictions=False, make_plots=False
    )
    assert (output / "confirmatory_transfer_tests.csv").exists()
    assert (output / "selected_external_predictions.csv").exists()
    assert (output / "artifact_manifest.json").exists()
    assert len(manifest["artifacts"]) == 7
