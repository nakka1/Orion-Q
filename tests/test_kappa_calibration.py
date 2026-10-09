import json

import numpy as np
import pandas as pd
import pytest

from quantum_twin.core.config import PhysicsConfig
from quantum_twin.evaluation.kappa_calibration import (
    KappaCalibrationConfig,
    calibrate_kappa_from_measured_fidelity,
)
from quantum_twin.quantum.paired_telemetry import DEFAULT_PAIRED_MANIFEST


def _measured_frame(*, target_provenance="measured_quantum"):
    physics = PhysicsConfig()
    ber = np.linspace(0.0005, 0.01, 60)
    true_kappa = 20.0
    depolarization = physics.DEPOLARIZATION_P + true_kappa * ber
    fidelity = 1.0 - 0.5 * depolarization
    frame = pd.DataFrame({
        "pre_fec_ber": ber,
        "bell_fidelity_measured": fidelity,
        "alignment_error_ms": 1.0,
        "timestamp_utc": pd.date_range("2026-01-01", periods=60, freq="s", tz="UTC"),
        "campaign_id": "c1",
        "device_id": "q1",
        "channel_id": "ch37",
    })
    manifest = json.loads(json.dumps(DEFAULT_PAIRED_MANIFEST))
    manifest["target_provenance"]["bell_fidelity_measured"] = target_provenance
    manifest["synchronization"]["clock_source"] = "PTP"
    frame.attrs["paired_quantum_manifest"] = manifest
    return frame


def test_kappa_is_selected_on_train_and_scored_on_held_out_measured_fidelity():
    data = _measured_frame()
    result = calibrate_kappa_from_measured_fidelity(
        data,
        train_indices=np.arange(40),
        test_indices=np.arange(40, 60),
        config=KappaCalibrationConfig(
            kappa_max=40.0,
            candidate_count=81,
            fidelity_grid_size=21,
            bootstrap_replicates=30,
            seed=3,
        ),
        fidelity_predictor=lambda depolarization: 1.0 - 0.5 * depolarization,
    )
    assert result.report["selected_kappa"] == pytest.approx(20.0)
    assert result.report["held_out_test_mae"] < 1e-12
    assert result.report["mutated_physics_configuration"] is False
    assert len(result.test_predictions) == 20


def test_kappa_calibration_rejects_simulated_fidelity_provenance():
    data = _measured_frame(target_provenance="simulated_from_ber")
    with pytest.raises(ValueError, match="simulated_from_ber"):
        calibrate_kappa_from_measured_fidelity(
            data,
            train_indices=np.arange(40),
            test_indices=np.arange(40, 60),
            config=KappaCalibrationConfig(bootstrap_replicates=2),
            fidelity_predictor=lambda depolarization: 1.0 - depolarization,
        )
