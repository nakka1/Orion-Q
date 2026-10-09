"""
tests/test_telemetry_source.py

Unit tests for telemetry_source.py: SyntheticTelemetrySource and the
functional CSVTelemetrySource (real-data ingestion adapter).
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
import pytest

from quantum_twin.core.config import PhysicsConfig
from quantum_twin.optical.wdm import QuantumNetworkDatasetV3
from quantum_twin.optical.sources import SyntheticTelemetrySource, CSVTelemetrySource, TelemetrySource


def test_telemetry_source_is_abstract():
    with pytest.raises(TypeError):
        TelemetrySource()


def test_synthetic_source_generates_valid_dataset():
    src = SyntheticTelemetrySource(n_steps=100, config=PhysicsConfig(SEED=1))
    df = src.generate_dataset()
    assert len(df) == 100
    assert list(df.columns) == QuantumNetworkDatasetV3.FEATURE_COLUMNS


def test_synthetic_source_preprocess_delegates_correctly():
    src = SyntheticTelemetrySource(n_steps=150, config=PhysicsConfig(SEED=1))
    df = src.generate_dataset()
    X_train, y_train, X_test, y_test, scaler, raw_test = src.preprocess(df, window_size=10, test_size=0.3)
    assert X_train.shape[1:] == (10, src.input_size)


def test_csv_source_missing_file_raises():
    src = CSVTelemetrySource(csv_path="/tmp/definitely_does_not_exist_12345.csv")
    with pytest.raises(FileNotFoundError):
        src.generate_dataset()


def test_csv_source_preserves_optical_only_data_and_fidelity_preprocess_raises(tmp_path):
    csv_path = str(tmp_path / "incomplete.csv")
    pd.DataFrame({"osnr_db": [20.5, 20.1, 19.8]}).to_csv(csv_path, index=False)
    src = CSVTelemetrySource(csv_path=csv_path)
    loaded = src.generate_dataset()
    assert list(loaded["osnr_db"]) == [20.5, 20.1, 19.8]
    assert "F_t" not in loaded.columns
    with pytest.raises(ValueError, match="no quantum-fidelity target"):
        src.preprocess(loaded)


def test_csv_source_column_mapping_and_causal_derivation(tmp_path):
    """
    Regression test for the core roadmap requirement applied to REAL data:
    deterministic derived columns are calculated, but unavailable sensors are
    not replaced by fabricated constants.
    """
    raw = pd.DataFrame({
        "fidelity_measured": [0.7, 0.65, 0.0, 0.72],
        "T1": [50e-6, 51e-6, 49e-6, 50e-6],
        "T2": [30e-6, 29e-6, 30e-6, 31e-6],
        "BER": [0.01, 0.012, 0.5, 0.009],
        "Distance_km": [10.0, 10.0, 10.0, 10.0],
        "depol_p": [0.01, 0.011, 0.01, 0.009],
        "Photon_Rate": [6e5, 6e5, 6e5, 6e5],
        "Latency": [1e-5, 1e-5, 1e-5, 1e-5],
    })
    csv_path = str(tmp_path / "external_feed.csv")
    raw.to_csv(csv_path, index=False)

    src = CSVTelemetrySource(
        csv_path=csv_path,
        column_mapping={"fidelity_measured": "F_t", "depol_p": "Depolarization_Level"},
        alpha_db_per_km=0.2,
    )
    df = src.generate_dataset()

    assert (df["Loss_dB"] == 0.2 * df["Distance_km"]).all()
    expected_eta = 10 ** (-df["Loss_dB"] / 10.0)
    assert (df["Transmission_Efficiency"] - expected_eta).abs().max() < 1e-9
    for invented in ["optical_power_dbm", "osnr_db", "phase_drift", "temperature",
                     "polarization_drift", "channel_available"]:
        assert invented not in df.columns
    assert df.attrs["telemetry_provenance"]["simulated_quantum_target"] is False


def test_csv_source_output_feeds_edge_lstm_unmodified(tmp_path):
    """End-to-end: the ingested-CSV pathway must be usable by the
    UNMODIFIED EdgeLSTM, per the roadmap's 'sem alterar o EdgeLSTM' requirement."""
    from quantum_twin.ml.lstm import EdgeLSTM

    ds = QuantumNetworkDatasetV3(n_steps=100, config=PhysicsConfig(SEED=3))
    df = ds.generate_dataset()
    csv_path = str(tmp_path / "as_if_real.csv")
    df.to_csv(csv_path, index=False)

    src = CSVTelemetrySource(csv_path=csv_path)
    loaded_df = src.generate_dataset()
    X_train, y_train, X_test, y_test, scaler, raw_test = src.preprocess(loaded_df, window_size=10, test_size=0.3)

    model = EdgeLSTM(input_size=src.input_size, hidden_size=8)
    pred = model(X_test[:1])
    assert pred.shape == (1, 1)
