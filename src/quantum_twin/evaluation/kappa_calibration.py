"""Guarded calibration of the simulated BER-to-depolarization coupling.

``KAPPA_DEPOL_FROM_BER`` is a simulation sensitivity parameter unless this
routine succeeds on synchronized BER and *measured* Bell fidelity.  The
calibration is split-aware, does not mutate ``PhysicsConfig`` and reports
held-out performance rather than relabelling a fitted simulation as evidence.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Callable

import numpy as np
import pandas as pd

from quantum_twin.core.config import PhysicsConfig
from quantum_twin.quantum.channel import QuantumChannel
from quantum_twin.quantum.paired_telemetry import assert_experimental_quantum_targets


@dataclass(frozen=True)
class KappaCalibrationConfig:
    kappa_min: float = 0.0
    kappa_max: float = 100.0
    candidate_count: int = 501
    fidelity_grid_size: int = 101
    bootstrap_replicates: int = 1_000
    alpha: float = 0.05
    seed: int = 42
    maximum_alignment_error_ms: float = 1_000.0

    def validate(self) -> None:
        if self.kappa_min < 0.0 or self.kappa_max <= self.kappa_min:
            raise ValueError("kappa bounds must satisfy 0 <= min < max")
        if self.candidate_count < 2 or self.fidelity_grid_size < 2:
            raise ValueError("candidate_count and fidelity_grid_size must be >= 2")
        if self.bootstrap_replicates < 1:
            raise ValueError("bootstrap_replicates must be >= 1")
        if not 0.0 < self.alpha < 1.0:
            raise ValueError("alpha must be between zero and one")
        if self.maximum_alignment_error_ms < 0.0:
            raise ValueError("maximum_alignment_error_ms must be non-negative")


@dataclass
class KappaCalibrationResult:
    report: dict
    candidate_metrics: pd.DataFrame
    test_predictions: pd.DataFrame


def _validate_split_indices(
    length: int,
    train_indices: np.ndarray,
    test_indices: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    train = np.unique(np.asarray(train_indices, dtype=int))
    test = np.unique(np.asarray(test_indices, dtype=int))
    if not len(train) or not len(test):
        raise ValueError("calibration requires non-empty train and test indices")
    if train.min() < 0 or test.min() < 0 or train.max() >= length or test.max() >= length:
        raise IndexError("calibration split index is outside the paired dataset")
    if np.intersect1d(train, test).size:
        raise ValueError("calibration train and test indices must be disjoint")
    return train, test


def _fidelity_lookup(
    physics: PhysicsConfig,
    *,
    grid_size: int,
    predictor: Callable[[float], float] | None,
) -> tuple[np.ndarray, np.ndarray]:
    depolarization = np.linspace(0.0, 0.5, int(grid_size))
    if predictor is None:
        channel = QuantumChannel(physics, rng=np.random.default_rng(physics.SEED))
        exposure = physics.TRANSMISSION_EXPOSURE_TIME + physics.STORAGE_TIME
        fidelity = np.asarray([
            channel.simulate_fidelity(float(value), exposure)
            for value in depolarization
        ])
    else:
        fidelity = np.asarray([predictor(float(value)) for value in depolarization], dtype=float)
    if fidelity.shape != depolarization.shape or not np.isfinite(fidelity).all():
        raise ValueError("fidelity predictor returned invalid lookup values")
    return depolarization, fidelity


def calibrate_kappa_from_measured_fidelity(
    data: pd.DataFrame,
    *,
    train_indices: np.ndarray,
    test_indices: np.ndarray,
    physics: PhysicsConfig | None = None,
    config: KappaCalibrationConfig = KappaCalibrationConfig(),
    fidelity_predictor: Callable[[float], float] | None = None,
) -> KappaCalibrationResult:
    """Estimate kappa on training rows and evaluate it on untouched rows.

    ``fidelity_predictor`` is an injectable depolarization-to-fidelity lookup
    used for fast verification tests.  Normal campaigns omit it and execute
    the repository's actual ``QuantumChannel`` implementation.
    """
    config.validate()
    physics = physics or PhysicsConfig()
    assert_experimental_quantum_targets(
        data, required_targets=("bell_fidelity_measured",)
    )
    required = {"pre_fec_ber", "bell_fidelity_measured", "alignment_error_ms"}
    missing = sorted(required.difference(data.columns))
    if missing:
        raise ValueError(f"kappa calibration is missing column(s): {missing}")
    manifest = data.attrs.get("paired_quantum_manifest", {})
    synchronization = manifest.get("synchronization", {})
    if synchronization.get("paired_on_same_link_and_clock") is not True:
        raise ValueError("kappa calibration requires verified same-link/same-clock pairing")
    declared_bound = synchronization.get("maximum_alignment_error_ms")
    if declared_bound is None:
        raise ValueError("manifest must declare maximum_alignment_error_ms")
    allowed_alignment = min(
        float(declared_bound), float(config.maximum_alignment_error_ms)
    )

    train, test = _validate_split_indices(len(data), train_indices, test_indices)
    selected = np.concatenate([train, test])
    ber_all = pd.to_numeric(data["pre_fec_ber"], errors="coerce").to_numpy(dtype=float)
    measured_all = pd.to_numeric(
        data["bell_fidelity_measured"], errors="coerce"
    ).to_numpy(dtype=float)
    alignment_all = pd.to_numeric(
        data["alignment_error_ms"], errors="coerce"
    ).to_numpy(dtype=float)
    if not np.isfinite(ber_all[selected]).all() or not np.isfinite(measured_all[selected]).all():
        raise ValueError("calibration rows require finite measured BER and Bell fidelity")
    if not np.isfinite(alignment_all[selected]).all():
        raise ValueError("calibration rows require finite alignment_error_ms")
    if np.any(alignment_all[selected] > allowed_alignment):
        raise ValueError(
            f"calibration alignment exceeds the allowed {allowed_alignment:g} ms"
        )

    depolarization_grid, fidelity_grid = _fidelity_lookup(
        physics,
        grid_size=config.fidelity_grid_size,
        predictor=fidelity_predictor,
    )
    candidates = np.linspace(
        config.kappa_min, config.kappa_max, int(config.candidate_count)
    )

    def candidate_predictions(indices: np.ndarray) -> np.ndarray:
        effective_depolarization = np.clip(
            physics.DEPOLARIZATION_P
            + candidates[:, None] * ber_all[indices][None, :],
            0.0,
            0.5,
        )
        return np.interp(
            effective_depolarization,
            depolarization_grid,
            fidelity_grid,
        )

    train_predictions = candidate_predictions(train)
    train_errors = train_predictions - measured_all[train][None, :]
    train_mse = np.mean(train_errors ** 2, axis=1)
    selected_offset = int(np.argmin(train_mse))
    selected_kappa = float(candidates[selected_offset])

    test_candidate_predictions = candidate_predictions(test)
    test_errors = test_candidate_predictions - measured_all[test][None, :]
    test_mae = np.mean(np.abs(test_errors), axis=1)
    test_rmse = np.sqrt(np.mean(test_errors ** 2, axis=1))
    zero_offset = int(np.argmin(np.abs(candidates - 0.0)))
    default_offset = int(np.argmin(np.abs(candidates - physics.KAPPA_DEPOL_FROM_BER)))

    rng = np.random.default_rng(int(config.seed))
    bootstrap_kappas = np.empty(int(config.bootstrap_replicates), dtype=float)
    for replicate in range(int(config.bootstrap_replicates)):
        positions = rng.integers(0, len(train), size=len(train))
        losses = np.mean(train_errors[:, positions] ** 2, axis=1)
        bootstrap_kappas[replicate] = candidates[int(np.argmin(losses))]
    ci_low, ci_high = np.quantile(
        bootstrap_kappas, [config.alpha / 2.0, 1.0 - config.alpha / 2.0]
    )

    candidate_metrics = pd.DataFrame({
        "kappa_depol_from_ber": candidates,
        "train_mse": train_mse,
        "test_mae": test_mae,
        "test_rmse": test_rmse,
        "selected_on_train": np.arange(len(candidates)) == selected_offset,
    })
    test_predictions = pd.DataFrame({
        "row_index": test,
        "pre_fec_ber": ber_all[test],
        "bell_fidelity_measured": measured_all[test],
        "bell_fidelity_predicted": test_candidate_predictions[selected_offset],
        "absolute_error": np.abs(test_errors[selected_offset]),
    })
    for column in ("timestamp_utc", "campaign_id", "device_id", "channel_id"):
        if column in data:
            test_predictions[column] = data.iloc[test][column].to_numpy()

    report = {
        "status": "calibrated from synchronized measured BER/Bell fidelity",
        "simulation_parameter": "KAPPA_DEPOL_FROM_BER",
        "mapping": "clip(DEPOLARIZATION_P + kappa * measured_pre_fec_BER, 0, 0.5)",
        "selected_kappa": selected_kappa,
        "bootstrap_confidence_interval": [float(ci_low), float(ci_high)],
        "alpha": float(config.alpha),
        "train_rows": int(len(train)),
        "test_rows": int(len(test)),
        "held_out_test_mae": float(test_mae[selected_offset]),
        "held_out_test_rmse": float(test_rmse[selected_offset]),
        "zero_coupling_test_mae": float(test_mae[zero_offset]),
        "configured_default_kappa": float(physics.KAPPA_DEPOL_FROM_BER),
        "configured_default_grid_kappa": float(candidates[default_offset]),
        "configured_default_test_mae": float(test_mae[default_offset]),
        "maximum_alignment_error_ms": allowed_alignment,
        "configuration": asdict(config),
        "physics_configuration": asdict(physics),
        "fit_scope": "kappa selected only on train_indices; metrics reported on test_indices",
        "mutated_physics_configuration": False,
        "limitations": [
            "The one-parameter mapping does not identify every physical noise mechanism.",
            "Calibration supports this apparatus/regime until replicated across devices/campaigns.",
        ],
    }
    return KappaCalibrationResult(report, candidate_metrics, test_predictions)


__all__ = [
    "KappaCalibrationConfig",
    "KappaCalibrationResult",
    "calibrate_kappa_from_measured_fidelity",
]
