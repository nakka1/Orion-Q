import json

import numpy as np
import pandas as pd

from quantum_twin.evaluation.measured_quantum_prediction import (
    MeasuredQuantumPredictionConfig,
    build_paired_quantum_windows,
    run_measured_quantum_prediction_campaign,
)
from quantum_twin.quantum.paired_telemetry import DEFAULT_PAIRED_MANIFEST


def _write_paired(tmp_path, rows_per_series=70):
    rows = []
    for campaign_offset, campaign in enumerate(("c1", "c2")):
        for device_offset, device in enumerate(("q1", "q2")):
            for step in range(rows_per_series):
                timestamp = pd.Timestamp("2026-01-01", tz="UTC") + pd.Timedelta(seconds=step)
                optical = np.sin(step / 6.0) + 0.02 * step + 0.2 * campaign_offset
                ber = 1e-6 * np.exp(0.3 * optical + 0.1 * device_offset)
                qber = 0.02 + 0.004 * optical + 0.0005 * device_offset
                skr = max(120_000.0 - 900_000.0 * qber, 0.0)
                rows.append({
                    "dataset_id": "paired-temporal-test",
                    "campaign_id": campaign,
                    "device_id": device,
                    "channel_id": "ch37",
                    "timestamp_utc": timestamp.isoformat().replace("+00:00", "Z"),
                    "pre_fec_ber": ber,
                    "osnr_db": 25.0 - optical,
                    "rx_power_dbm": -12.0 - 0.2 * optical,
                    "tx_power_dbm": 0.1 * campaign_offset,
                    "qber_ratio": qber,
                    "secure_key_rate_bps": skr,
                    "bell_fidelity_measured": np.nan,
                    "fidelity_standard_error": np.nan,
                    "fidelity_shots": np.nan,
                    "fidelity_measurement_method": None,
                    "alignment_error_ms": 1.0,
                })
    path = tmp_path / "paired.csv"
    pd.DataFrame(rows).to_csv(path, index=False)
    manifest = json.loads(json.dumps(DEFAULT_PAIRED_MANIFEST))
    manifest["dataset_id"] = "paired-temporal-test"
    manifest["license"] = "test-only"
    manifest["synchronization"]["clock_source"] = "PTP"
    manifest_path = path.with_suffix(".csv.manifest.json")
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    return path


def test_paired_windows_never_cross_entity_or_use_future_target_as_feature(tmp_path):
    path = _write_paired(tmp_path, rows_per_series=30)
    from quantum_twin.quantum.paired_telemetry import PairedQuantumTelemetrySource

    data = PairedQuantumTelemetrySource(path).require_valid()
    windowed = build_paired_quantum_windows(
        data, target="qber_ratio", horizon=2, window_size=5
    )
    assert windowed.X.shape[1:] == (5, 5)
    assert windowed.metadata["device_id"].nunique() == 4
    assert (
        windowed.metadata["target_timestamp_utc"] > windowed.metadata["timestamp_utc"]
    ).all()
    np.testing.assert_allclose(
        windowed.X[:, -1, 0], windowed.metadata["current_target_value"]
    )


def test_small_measured_quantum_campaign_includes_required_controls_and_transfer(tmp_path):
    result = run_measured_quantum_prediction_campaign(
        _write_paired(tmp_path),
        config=MeasuredQuantumPredictionConfig(
            targets=("qber_ratio",),
            horizons=(1,),
            models=("ridge",),
            seeds=(7,),
            window_size=5,
            n_splits=1,
            bootstrap_replicates=20,
            bootstrap_block_length=3,
        ),
    )
    assert set(result.predictions["feature_set"]) == {
        "persistence", "autoregressive", "wdm_only", "combined", "permuted_wdm",
    }
    assert set(result.predictions["evaluation"]) == {
        "rolling_origin", "campaign_holdout", "device_holdout",
    }
    assert set(result.comparisons["contrast"]) == {
        "incremental_over_autoregressive", "aligned_over_permuted_control",
    }
    assert result.comparisons["hypothesis_family_size"].nunique() == 1
    assert set(result.predictions[["campaign_id", "source_device_id", "channel_id"]]) == {
        "campaign_id", "source_device_id", "channel_id"
    }
    assert result.report["dataset"]["quality"]["validation_valid"] is True
