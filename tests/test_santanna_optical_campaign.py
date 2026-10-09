import json

import numpy as np
import pandas as pd

from quantum_twin.evaluation.optical_campaign import (
    CampaignConfig,
    add_causal_temporal_features,
    effective_purge_steps,
    paired_moving_block_bootstrap,
    rolling_origin_splits,
    run_optical_campaign,
    write_campaign_artifacts,
)


def _forecast_frame(rows_per_device=80, horizon=2):
    rows = []
    for device_offset, device in enumerate(["SPO1", "SPO2"]):
        for step in range(rows_per_device):
            timestamp = pd.Timestamp("2026-01-01", tz="UTC") + pd.Timedelta(seconds=step)
            osnr = 20.0 + device_offset + 0.02 * step + 0.2 * np.sin(step / 5.0)
            ber = 1e-6 * np.exp(-0.02 * step)
            rows.append({
                "device_id": device,
                "timestamp_utc": timestamp,
                "target_timestamp_utc": timestamp + pd.Timedelta(seconds=horizon),
                "osnr_db": osnr,
                "pre_fec_ber": ber,
                "target_osnr_db": osnr + 0.02 * horizon,
            })
    return pd.DataFrame(rows)


def test_temporal_features_do_not_change_when_future_is_mutated():
    original = _forecast_frame(rows_per_device=40)
    engineered, features = add_causal_temporal_features(
        original, target="osnr_db", lag_steps=(1, 3), rolling_windows=(3, 5)
    )
    changed = original.copy()
    future = changed.groupby("device_id").cumcount() > 20
    changed.loc[future, "osnr_db"] = 10_000.0
    changed_engineered, _ = add_causal_temporal_features(
        changed, target="osnr_db", lag_steps=(1, 3), rolling_windows=(3, 5)
    )
    past = engineered.groupby("device_id").cumcount() <= 20
    pd.testing.assert_frame_equal(
        engineered.loc[past, features].reset_index(drop=True),
        changed_engineered.loc[past, features].reset_index(drop=True),
    )


def test_rolling_origin_has_device_local_embargo_and_label_ordering():
    engineered, _ = add_causal_temporal_features(
        _forecast_frame(rows_per_device=100, horizon=3),
        target="osnr_db",
        lag_steps=(1, 5),
        rolling_windows=(3,),
    )
    folds = rolling_origin_splits(
        engineered, n_splits=3, min_train_fraction=0.5, purge_steps=8
    )
    assert len(folds) == 3
    for fold in folds:
        train = engineered.loc[fold["train_indices"]]
        test = engineered.loc[fold["test_indices"]]
        for device in ["SPO1", "SPO2"]:
            train_device = train.loc[train["device_id"] == device]
            test_device = test.loc[test["device_id"] == device]
            assert train_device["target_timestamp_utc"].max() < test_device["timestamp_utc"].min()
            assert fold["devices"][device]["purged_rows"] == 8


def test_effective_purge_covers_maximum_lag_and_horizon():
    config = CampaignConfig(lag_steps=(1, 5, 30), requested_purge_steps=20)
    assert effective_purge_steps(config, horizon=10) == 40
    assert effective_purge_steps(config, horizon=50) == 80


def test_paired_block_bootstrap_detects_consistent_improvement():
    differences = [np.full(100, 0.2), np.full(80, 0.1)]
    low, high = paired_moving_block_bootstrap(
        differences, block_length=10, replicates=200, seed=7, alpha=0.05
    )
    assert low > 0.0
    assert high >= low


def test_small_campaign_writes_reproducible_artifacts(tmp_path):
    rows = []
    for device_offset, device in enumerate(["SPO1/18/11", "SPO2/18/11"]):
        for step in range(90):
            rows.append({
                "Timestamp": 1_700_000_000 + step * 4,
                "Type": "Devices",
                "ID": device,
                "BER": 1e-5 * np.exp(-step / 100.0),
                "OSNR": 20.0 + device_offset + step * 0.01 + np.sin(step / 8.0),
                "InputPower": np.nan,
                "OutputPower": np.nan,
                "Failure": 0,
            })
    csv_path = tmp_path / "HardFailure_dataset.csv"
    pd.DataFrame(rows).to_csv(csv_path, index=False)
    config = CampaignConfig(
        targets=("osnr_db",),
        horizons=(1,),
        seeds=(7,),
        lag_steps=(1, 2),
        rolling_windows=(3,),
        models=("ewma", "ridge"),
        n_splits=2,
        requested_purge_steps=2,
        bootstrap_replicates=100,
        bootstrap_block_length=5,
    )
    result = run_optical_campaign(csv_path, config=config)
    assert set(result.predictions["model"]) == {"persistence", "ewma", "ridge"}
    assert set(result.predictions["evaluation"]) == {"rolling_origin", "device_holdout"}
    assert not result.comparisons.empty
    assert result.report["claim_scope"].startswith("forecasting of recorded optical telemetry only")

    output_dir = tmp_path / "campaign"
    manifest = write_campaign_artifacts(
        result, output_dir, include_predictions=False, make_plots=True
    )
    assert (output_dir / "tcc_results.csv").exists()
    assert (output_dir / "osnr_db_loss_by_horizon.png").exists()
    assert (output_dir / "artifact_manifest.json").exists()
    report = json.loads((output_dir / "report.json").read_text(encoding="utf-8"))
    assert report["dataset"]["sha256"]
    assert len(manifest["artifacts"]) == 6
