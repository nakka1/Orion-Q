import json

import numpy as np
import pandas as pd

from quantum_twin.core.config import PhysicsConfig
from quantum_twin.evaluation.bristol_quantum_replay import (
    BristolQuantumReplayConfig,
    run_bristol_quantum_replay,
    simulate_bell_fidelity_from_recorded_ber,
    write_bristol_quantum_replay_artifacts,
)
from quantum_twin.optical.bristol import BristolNDFFTelemetrySource


def _write_bristol_aggregates(tmp_path, n=48):
    tmp_path.mkdir(parents=True, exist_ok=True)
    timestamps = pd.date_range("2024-01-01", periods=n, freq="15min")
    dates = timestamps.strftime("%d/%m/%Y %H:%M")
    step = np.arange(n, dtype=float)
    common = np.sin(step / 5.0) + 0.015 * step

    pd.DataFrame({
        "Date": dates,
        "BER1": 1e-4 * np.exp(0.35 * common),
        "Qfactor1": 9.5 - 0.3 * common,
        "BER2": 1.2e-4 * np.exp(0.28 * common),
        "Qfactor2": 9.2 - 0.25 * common,
    }).to_csv(tmp_path / "ADVA_onemonth_15minsampling.csv", index=False)
    pd.DataFrame({
        "Date": dates,
        "OPR-OCH": -12.0 + 0.2 * common,
        "PREBER Value": 9e-5 * np.exp(0.31 * common),
        "QAVG Value": 9.8 - 0.22 * common,
    }).to_csv(tmp_path / "Ciena_onemonth_15minsampling.csv", index=False)
    voyager = {"Date": dates}
    for channel in range(1, 5):
        voyager[f"channel{channel}"] = (
            (0.8 + 0.1 * channel) * 1e-4 * np.exp((0.2 + 0.02 * channel) * common)
        )
        voyager[f"Qfactor{channel}"] = 10.0 - 0.15 * channel - 0.2 * common
    pd.DataFrame(voyager).to_csv(
        tmp_path / "Voyager_onemonth_15minsampling.csv", index=False
    )
    return tmp_path


def test_bristol_adapter_is_long_format_and_timezone_is_not_invented(tmp_path):
    source = BristolNDFFTelemetrySource(_write_bristol_aggregates(tmp_path))
    frame = source.read()

    assert len(frame) == 48 * 7
    assert frame["channel_id"].nunique() == 7
    assert source.validate(frame).is_valid
    assert frame["timestamp_local_unzoned"].dt.tz is None
    provenance = frame.attrs["telemetry_provenance"]
    assert provenance["timestamp_timezone"] == "unknown/unzoned"
    assert provenance["quantum_target_present"] is False


def test_original_quantum_channel_mapping_is_monotone_and_declares_circularity(tmp_path):
    source = BristolNDFFTelemetrySource(_write_bristol_aggregates(tmp_path))
    recorded = source.read()
    replay, audit = simulate_bell_fidelity_from_recorded_ber(recorded, grid_points=17)

    ordered = replay.sort_values("pre_fec_ber")
    assert (np.diff(ordered["simulated_bell_fidelity"]) <= 1e-10).all()
    assert audit["direct_ber_fidelity_association_is_by_construction"] is True
    assert audit["kappa_is_hardware_calibrated"] is False
    assert audit["maximum_interpolation_absolute_error"] < 1e-4

    null_physics = PhysicsConfig().with_overrides(KAPPA_DEPOL_FROM_BER=0.0)
    null_replay, null_audit = simulate_bell_fidelity_from_recorded_ber(
        recorded, physics=null_physics, grid_points=9
    )
    assert np.isclose(null_replay["simulated_bell_fidelity"].std(), 0.0)
    assert null_audit["recorded_ber_simulated_fidelity_pearson"] is None


def test_causal_panel_never_uses_future_or_other_simulated_fidelity(tmp_path):
    source = BristolNDFFTelemetrySource(_write_bristol_aggregates(tmp_path))
    replay, _ = simulate_bell_fidelity_from_recorded_ber(source.read(), grid_points=9)
    panel = source.build_causal_channel_panel(replay, anchor_channel="ADVA-1")

    observed = [column for column in panel if column.startswith("observed_at__")]
    assert observed
    for column in observed:
        matched = panel[column].notna()
        assert (
            panel.loc[matched, column]
            <= panel.loc[matched, "timestamp_local_unzoned"]
        ).all()
    assert not any(
        column.startswith("simulated_bell_fidelity__") for column in panel.columns
    )


def test_small_bristol_replay_uses_incremental_controls_and_writes_outputs(tmp_path):
    dataset = _write_bristol_aggregates(tmp_path / "dataset")
    config = BristolQuantumReplayConfig(
        horizons=(1,),
        models=("ridge",),
        seeds=(5,),
        window_size=5,
        n_splits=1,
        bootstrap_replicates=30,
        bootstrap_block_length=3,
        aer_grid_points=9,
    )
    result = run_bristol_quantum_replay(dataset, config=config)
    assert set(result.predictions["feature_set"]) == {
        "persistence", "autoregressive", "wdm_only", "combined", "permuted_wdm",
    }
    assert "timestamp_local_unzoned" in result.predictions
    assert "timestamp_utc" not in result.predictions
    assert result.report["status"].startswith("hybrid real-telemetry replay")
    assert result.report["mapping_audit"][
        "direct_ber_fidelity_association_is_by_construction"
    ] is True
    assert set(result.comparisons["contrast"]) == {
        "incremental_over_autoregressive", "aligned_over_permuted_control",
    }

    output = tmp_path / "outputs"
    manifest = write_bristol_quantum_replay_artifacts(
        result, output, include_predictions=False, make_plots=False
    )
    report = json.loads((output / "report.json").read_text(encoding="utf-8"))
    assert report["dataset"]["provenance"]["quantum_target_present"] is False
    assert len(manifest["artifacts"]) == 5
