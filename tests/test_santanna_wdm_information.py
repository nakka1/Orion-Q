import json

import numpy as np
import pandas as pd
import pytest
import torch

from quantum_twin.evaluation.wdm_information import (
    WDMInformationConfig,
    _permuted_cross_wdm,
    build_device_local_windows,
    run_wdm_information_campaign,
    write_wdm_information_artifacts,
)
from quantum_twin.ml.architectures import EdgeGRU, EdgeTCN
from quantum_twin.ml.lstm import EdgeLSTM
from quantum_twin.ml.optical_sequence import (
    make_optical_sequence_model,
    predict_optical_sequence_model,
    train_optical_sequence_model,
)
from quantum_twin.optical.santanna import SantAnnaOpticalFailureSource


def _recorded_like_csv(tmp_path, rows_per_device=80):
    rows = []
    for step in range(rows_per_device):
        timestamp = 1_700_000_000 + step * 4
        common = np.sin(step / 7.0) + 0.01 * step
        for offset, device in enumerate(["SPO1/18/11", "SPO2/18/11"]):
            rows.append({
                "Timestamp": timestamp,
                "Type": "Devices",
                "ID": device,
                "BER": 1e-6 * np.exp(-0.05 * common + offset * 0.1),
                "OSNR": 22.0 + offset + 0.7 * common,
                "InputPower": np.nan,
                "OutputPower": np.nan,
                "Failure": 0,
            })
        for offset, amplifier in enumerate(["Ampli1", "Ampli2"]):
            rows.append({
                "Timestamp": timestamp,
                "Type": "Infrastructure",
                "ID": amplifier,
                "BER": np.nan,
                "OSNR": np.nan,
                "InputPower": -25.0 + common + offset,
                "OutputPower": 0.5 + 0.2 * common + offset,
                "Failure": 0,
            })
    path = tmp_path / "HardFailure_dataset.csv"
    pd.DataFrame(rows).to_csv(path, index=False)
    return path


@pytest.mark.parametrize(
    ("name", "base"),
    [("edge_lstm", EdgeLSTM), ("edge_gru", EdgeGRU), ("edge_tcn", EdgeTCN)],
)
def test_optical_models_reuse_original_backbones_without_fidelity_sigmoid(name, base):
    model = make_optical_sequence_model(name, input_size=3, hidden_size=4)
    assert isinstance(model, base)
    with torch.no_grad():
        for parameter in model.parameters():
            parameter.zero_()
        model.head.bias.fill_(2.0)
    prediction = model(torch.zeros(2, 8, 3))
    assert torch.allclose(prediction, torch.full((2, 1), 2.0))


def test_sequence_trainer_and_prediction_have_a_regression_interface():
    rng = np.random.default_rng(3)
    X = rng.normal(size=(24, 6, 2)).astype(np.float32)
    y = X[:, -1, 0]
    model = make_optical_sequence_model("edge_gru", input_size=2, hidden_size=4)
    model, summary = train_optical_sequence_model(
        model, X, y, seed=3, max_epochs=3, batch_size=8, patience=2
    )
    prediction = predict_optical_sequence_model(model, X[:5])
    assert prediction.shape == (5,)
    assert np.isfinite(prediction).all()
    assert 1 <= summary.epochs_trained <= 3


def test_windows_are_device_local_and_control_preserves_target_history(tmp_path):
    path = _recorded_like_csv(tmp_path, rows_per_device=30)
    source = SantAnnaOpticalFailureSource(str(path))
    panel = source.build_causal_forecast_panel(target="osnr_db", horizon_steps=2)
    windowed = build_device_local_windows(panel, target="osnr_db", window_size=5)
    assert windowed.X.shape[1] == 5
    assert set(windowed.metadata["device_id"]) == {"SPO1/18/11", "SPO2/18/11"}
    permuted = _permuted_cross_wdm(
        windowed.X, windowed.metadata["device_id"], seed=7
    )
    np.testing.assert_array_equal(permuted[:, :, 0], windowed.X[:, :, 0])
    assert not np.array_equal(permuted[:, :, 1:], windowed.X[:, :, 1:])


def test_small_incremental_campaign_writes_auditable_outputs(tmp_path):
    path = _recorded_like_csv(tmp_path)
    config = WDMInformationConfig(
        targets=("osnr_db",),
        horizons=(1,),
        models=("ridge",),
        seeds=(7,),
        window_size=5,
        n_splits=2,
        bootstrap_replicates=50,
        bootstrap_block_length=4,
    )
    result = run_wdm_information_campaign(path, config=config)
    assert set(result.predictions["feature_set"]) == {
        "persistence", "autoregressive", "wdm_only", "combined", "permuted_wdm"
    }
    assert set(result.comparisons["contrast"]) == {
        "incremental_over_autoregressive", "aligned_over_permuted_control"
    }
    assert result.report["dataset"]["quantum_target_present"] is False
    assert result.report["causal_alignment"]["future_observation_allowed"] is False

    output = tmp_path / "information"
    manifest = write_wdm_information_artifacts(
        result, output, include_predictions=True, make_plots=False
    )
    report = json.loads((output / "report.json").read_text(encoding="utf-8"))
    assert report["central_estimand"].startswith("MAE(autoregressive)")
    assert (output / "incremental_comparisons.csv").exists()
    assert len(manifest["artifacts"]) == 5
