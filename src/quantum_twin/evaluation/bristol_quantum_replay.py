"""Hybrid replay of recorded Bristol WDM telemetry through the quantum model.

The output fidelity is simulated by the repository's original Qiskit/Aer
channel and is therefore not a measured Bristol quantum target.  The useful,
non-tautological question is whether *other recorded WDM channels* help
predict a target channel's future simulated fidelity beyond that target's own
fidelity history.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from pathlib import Path

import numpy as np
import pandas as pd

from quantum_twin.core.config import PhysicsConfig
from quantum_twin.evaluation.optical_campaign import (
    git_state,
    rolling_origin_splits,
    sha256_file,
)
from quantum_twin.evaluation.wdm_information import (
    WDMInformationConfig,
    WindowedOpticalData,
    build_evidence_table,
    compare_incremental_information,
    compute_information_metrics,
    evaluate_incremental_fold,
)
from quantum_twin.optical.bristol import BristolNDFFTelemetrySource
from quantum_twin.quantum.channel import QuantumChannel


@dataclass(frozen=True)
class BristolQuantumReplayConfig:
    horizons: tuple[int, ...] = (1, 4)
    models: tuple[str, ...] = ("ridge", "edge_lstm", "edge_gru", "edge_tcn")
    seeds: tuple[int, ...] = (42,)
    window_size: int = 20
    n_splits: int = 2
    min_train_fraction: float = 0.5
    alignment_tolerance_s: float = 1_200.0
    bootstrap_replicates: int = 20_000
    bootstrap_block_length: int = 16
    familywise_alpha: float = 0.05
    ber_floor: float = 1e-12
    aer_grid_points: int = 257
    hidden_size: int = 8
    max_epochs: int = 12
    batch_size: int = 512
    early_stopping_patience: int = 4
    learning_rate: float = 1e-3

    def validate(self) -> None:
        if not self.horizons or any(value < 1 for value in self.horizons):
            raise ValueError("horizons must be non-empty and >= 1")
        if self.window_size < 2 or self.n_splits < 1:
            raise ValueError("window_size must be >= 2 and n_splits >= 1")
        if self.alignment_tolerance_s < 0 or self.aer_grid_points < 3:
            raise ValueError("alignment tolerance must be >= 0 and grid points >= 3")
        if self.bootstrap_replicates < 1 or self.bootstrap_block_length < 1:
            raise ValueError("bootstrap settings must be >= 1")


@dataclass
class BristolQuantumReplayResult:
    report: dict
    replay_frame: pd.DataFrame
    predictions: pd.DataFrame
    metrics: pd.DataFrame
    comparisons: pd.DataFrame
    evidence: pd.DataFrame


def simulate_bell_fidelity_from_recorded_ber(
    frame: pd.DataFrame,
    *,
    physics: PhysicsConfig | None = None,
    grid_points: int = 257,
) -> tuple[pd.DataFrame, dict]:
    """Map recorded BER to conditional Bell fidelity using the original model.

    Aer is evaluated on a dense depolarization grid and interpolation is
    checked at cell midpoints.  This accelerates replay while still deriving
    the map from ``QuantumChannel.simulate_fidelity``.
    """
    if grid_points < 3:
        raise ValueError("grid_points must be >= 3")
    config = physics or PhysicsConfig()
    result = frame.copy()
    ber = pd.to_numeric(result["pre_fec_ber"], errors="coerce").to_numpy(dtype=float)
    if not np.isfinite(ber).all():
        raise ValueError("pre_fec_ber must be finite for quantum replay")
    depolarization = np.clip(
        config.DEPOLARIZATION_P + config.KAPPA_DEPOL_FROM_BER * ber,
        0.0,
        0.5,
    )
    grid = np.linspace(float(depolarization.min()), float(depolarization.max()), int(grid_points))
    channel = QuantumChannel(config.with_overrides())
    exposure = config.TRANSMISSION_EXPOSURE_TIME + config.STORAGE_TIME
    grid_fidelity = np.asarray([
        channel.simulate_fidelity(float(value), exposure) for value in grid
    ])
    interpolated = np.interp(depolarization, grid, grid_fidelity)

    midpoints = (grid[:-1] + grid[1:]) / 2.0
    audit_points = midpoints[np.linspace(0, len(midpoints) - 1, min(17, len(midpoints)), dtype=int)]
    exact = np.asarray([
        channel.simulate_fidelity(float(value), exposure) for value in audit_points
    ])
    approximate = np.interp(audit_points, grid, grid_fidelity)
    max_error = float(np.max(np.abs(exact - approximate)))

    result["effective_depolarization_simulated"] = depolarization
    result["simulated_bell_fidelity"] = interpolated
    result.attrs.update(frame.attrs)
    result.attrs["quantum_replay_provenance"] = {
        "target_provenance": "simulated_quantum_conditioned_on_recorded_wdm",
        "quantum_engine": "QuantumChannel.simulate_fidelity (Qiskit Aer density matrix)",
        "mapping": "clip(DEPOLARIZATION_P + KAPPA_DEPOL_FROM_BER * recorded_BER, 0, 0.5)",
        "kappa_depol_from_ber": config.KAPPA_DEPOL_FROM_BER,
        "kappa_is_hardware_calibrated": False,
        "conditional_on_pair_survival": True,
        "aer_grid_points": int(grid_points),
        "maximum_interpolation_absolute_error": max_error,
    }
    if np.ptp(ber) > 1e-15 and np.ptp(interpolated) > 1e-12:
        direct_correlation: float | None = float(np.corrcoef(ber, interpolated)[0, 1])
    else:
        direct_correlation = None
    manifest = {
        **result.attrs["quantum_replay_provenance"],
        "recorded_ber_simulated_fidelity_pearson": direct_correlation,
        "null_kappa_zero_fidelity_std": 0.0,
        "direct_ber_fidelity_association_is_by_construction": True,
    }
    return result, manifest


def build_bristol_replay_windows(
    source: BristolNDFFTelemetrySource,
    replay: pd.DataFrame,
    *,
    horizon: int,
    window_size: int,
    tolerance_s: float,
    ber_floor: float,
) -> WindowedOpticalData:
    panels = []
    for anchor in sorted(replay["channel_id"].astype(str).unique()):
        panel = source.build_causal_channel_panel(
            replay, anchor_channel=anchor, tolerance_s=tolerance_s
        )
        panel = panel.sort_values("timestamp_local_unzoned", kind="stable").reset_index(drop=True)
        panel["target_value"] = panel["simulated_bell_fidelity"].shift(-int(horizon))
        panel["target_timestamp_utc"] = panel["timestamp_local_unzoned"].shift(-int(horizon))
        panel["timestamp_utc"] = panel["timestamp_local_unzoned"]
        panel["device_id"] = panel["anchor_channel_id"].astype(str)
        panel["current_target_value"] = panel["simulated_bell_fidelity"]
        panel["target_unit"] = "Bell-state fidelity (simulated)"
        panels.append(panel.dropna(subset=["target_value"]))
    data = pd.concat(panels, ignore_index=True, sort=False)

    for column in [name for name in data if name.startswith("pre_fec_ber__")]:
        suffix = column[len("pre_fec_ber"):]
        data[f"log10_pre_fec_ber{suffix}"] = np.log10(
            pd.to_numeric(data[column], errors="coerce").clip(lower=ber_floor)
        )
    candidates = [
        column for column in data
        if column.startswith("log10_pre_fec_ber__")
        or column.startswith("q_factor_db__")
        or column.startswith("received_power_dbm__")
        or column.startswith("age_s__")
    ]
    cross_features = [
        column for column in dict.fromkeys(candidates)
        if not data[column].isna().all() and data[column].nunique(dropna=True) > 1
    ]
    if not cross_features:
        raise ValueError("no cross-channel Bristol WDM features are available")
    feature_columns = ["simulated_bell_fidelity"] + cross_features

    windows: list[np.ndarray] = []
    metadata: list[dict] = []
    for device, group in data.groupby("device_id", sort=True, observed=True):
        ordered = group.sort_values("timestamp_utc", kind="stable").reset_index(drop=True)
        values = ordered[feature_columns].to_numpy(dtype=float)
        for end in range(int(window_size) - 1, len(ordered)):
            row = ordered.iloc[end]
            if not np.isfinite(row["target_value"]):
                continue
            windows.append(values[end - int(window_size) + 1:end + 1])
            metadata.append({
                "device_id": str(device),
                "timestamp_utc": row["timestamp_utc"],
                "target_timestamp_utc": row["target_timestamp_utc"],
                "target_value": float(row["target_value"]),
                "current_target_value": float(row["current_target_value"]),
                "target_unit": row["target_unit"],
            })
    metadata_frame = pd.DataFrame(metadata).reset_index(drop=True)
    return WindowedOpticalData(
        metadata=metadata_frame,
        X=np.stack(windows),
        feature_columns=feature_columns,
        target_feature="simulated_bell_fidelity",
        wdm_feature_columns=cross_features,
    )


def run_bristol_quantum_replay(
    dataset_dir: str | Path,
    *,
    config: BristolQuantumReplayConfig = BristolQuantumReplayConfig(),
    physics: PhysicsConfig | None = None,
) -> BristolQuantumReplayResult:
    config.validate()
    physics_config = physics or PhysicsConfig()
    source = BristolNDFFTelemetrySource(dataset_dir)
    recorded = source.read()
    replay, mapping_manifest = simulate_bell_fidelity_from_recorded_ber(
        recorded, physics=physics_config, grid_points=config.aer_grid_points
    )
    prediction_frames = []
    fold_manifests = []
    feature_manifests = []
    evaluation_config = WDMInformationConfig(
        models=config.models,
        seeds=config.seeds,
        window_size=config.window_size,
        n_splits=config.n_splits,
        min_train_fraction=config.min_train_fraction,
        bootstrap_replicates=config.bootstrap_replicates,
        bootstrap_block_length=config.bootstrap_block_length,
        familywise_alpha=config.familywise_alpha,
        hidden_size=config.hidden_size,
        max_epochs=config.max_epochs,
        batch_size=config.batch_size,
        early_stopping_patience=config.early_stopping_patience,
        learning_rate=config.learning_rate,
    )
    evaluation_config.validate()
    for horizon in config.horizons:
        windowed = build_bristol_replay_windows(
            source,
            replay,
            horizon=horizon,
            window_size=config.window_size,
            tolerance_s=config.alignment_tolerance_s,
            ber_floor=config.ber_floor,
        )
        purge = config.window_size + int(horizon)
        splits = rolling_origin_splits(
            windowed.metadata,
            n_splits=config.n_splits,
            min_train_fraction=config.min_train_fraction,
            purge_steps=purge,
        )
        for number, split in enumerate(splits, start=1):
            split["fold_number"] = number
            fold_manifests.append({
                "horizon_steps": int(horizon),
                "fold": split["fold"],
                "purge_steps": purge,
                "devices": split["devices"],
            })
        feature_manifests.append({
            "horizon_steps": int(horizon),
            "target_history": windowed.target_feature,
            "cross_channel_recorded_wdm_features": windowed.wdm_feature_columns,
            "samples": int(len(windowed.metadata)),
        })
        for seed in config.seeds:
            for split in splits:
                prediction_frames.append(evaluate_incremental_fold(
                    windowed,
                    split,
                    target="simulated_bell_fidelity",
                    horizon=horizon,
                    seed=seed,
                    config=evaluation_config,
                ))
    predictions = pd.concat(prediction_frames, ignore_index=True)
    metrics = compute_information_metrics(predictions)
    comparisons = compare_incremental_information(
        predictions,
        block_length=config.bootstrap_block_length,
        replicates=config.bootstrap_replicates,
        seed=config.seeds[0],
        familywise_alpha=config.familywise_alpha,
    )
    evidence = build_evidence_table(comparisons)
    # The shared evaluator uses ``*_utc`` internally because Sant'Anna really
    # is UTC. Bristol's publisher does not state a timezone, so do not leak
    # that implementation detail into exported evidence.
    predictions = predictions.rename(columns={
        "timestamp_utc": "timestamp_local_unzoned",
        "target_timestamp_utc": "target_timestamp_local_unzoned",
    })
    report = {
        "study": "Bristol recorded-WDM replay through the original Bell-fidelity simulator",
        "claim_scope": (
            "Whether other real WDM channel histories add predictive information for future "
            "simulation-derived Bell fidelity beyond the target channel's own simulated history."
        ),
        "status": "hybrid real-telemetry replay; quantum target is simulated",
        "not_claimed": [
            "Bristol contains no measured quantum fidelity.",
            "The BER-to-depolarization coefficient is phenomenological and uncalibrated.",
            "Direct recorded-BER/fidelity correlation is imposed by the simulator equation.",
        ],
        "dataset": {
            "quality": source.quality_report(recorded),
            "provenance": recorded.attrs["telemetry_provenance"],
        },
        "timestamp_semantics": (
            "chronological local timestamps exactly as published; timezone unknown, no UTC conversion"
        ),
        "configuration": asdict(config),
        "physics_configuration": asdict(physics_config),
        "mapping_audit": mapping_manifest,
        "non_circular_estimand": (
            "MAE(target simulated-fidelity history) - MAE(target history + other recorded channels)"
        ),
        "folds": fold_manifests,
        "features": feature_manifests,
        "evidence_positive_count": int(
            evidence["evidence_for_incremental_wdm_information"].sum()
        ),
        "evidence_test_count": int(len(evidence)),
        "git": git_state(),
    }
    return BristolQuantumReplayResult(
        report=report,
        replay_frame=replay,
        predictions=predictions,
        metrics=metrics,
        comparisons=comparisons,
        evidence=evidence,
    )


def _plot_replay(result: BristolQuantumReplayResult, directory: Path) -> list[Path]:
    import matplotlib

    matplotlib.use("Agg", force=True)
    import matplotlib.pyplot as plt

    generated = []
    figure, axis = plt.subplots(figsize=(7, 5))
    for channel, group in result.replay_frame.groupby("channel_id", sort=True, observed=True):
        axis.scatter(
            np.log10(group["pre_fec_ber"].clip(lower=1e-12)),
            group["simulated_bell_fidelity"],
            s=6,
            alpha=0.35,
            label=str(channel),
        )
    axis.set_xlabel("log10(BER WDM registrado)")
    axis.set_ylabel("Fidelidade Bell simulada")
    axis.set_title("Mapeamento direto imposto pelo replay (não é validação)")
    axis.legend(fontsize=7, ncol=2)
    axis.grid(alpha=0.25)
    figure.tight_layout()
    path = directory / "recorded_ber_to_simulated_fidelity.png"
    figure.savefig(path, dpi=180)
    plt.close(figure)
    generated.append(path)

    for horizon, group in result.comparisons.groupby("horizon_steps", observed=True):
        subset = group.loc[group["contrast"].eq("incremental_over_autoregressive")]
        figure, axis = plt.subplots(figsize=(7, 4))
        axis.bar(subset["model"], subset["absolute_improvement"])
        axis.axhline(0.0, color="black", linewidth=1)
        axis.set_ylabel("Melhora MAE: AR - combinado")
        axis.set_title(f"Informação WDM cruzada, horizonte {int(horizon)}")
        axis.tick_params(axis="x", rotation=25)
        axis.grid(axis="y", alpha=0.25)
        figure.tight_layout()
        path = directory / f"cross_channel_incremental_h{int(horizon)}.png"
        figure.savefig(path, dpi=180)
        plt.close(figure)
        generated.append(path)
    return generated


def write_bristol_quantum_replay_artifacts(
    result: BristolQuantumReplayResult,
    output_dir: str | Path,
    *,
    include_predictions: bool = True,
    make_plots: bool = True,
) -> dict:
    directory = Path(output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    artifacts: list[Path] = []
    tables = {
        "replay_telemetry.csv": result.replay_frame,
        "fold_device_metrics.csv": result.metrics,
        "incremental_comparisons.csv": result.comparisons,
        "evidence_summary.csv": result.evidence,
    }
    if include_predictions:
        tables["out_of_fold_predictions.csv"] = result.predictions
    for name, frame in tables.items():
        path = directory / name
        frame.to_csv(path, index=False)
        artifacts.append(path)
    report_path = directory / "report.json"
    report_path.write_text(
        json.dumps(result.report, indent=2, ensure_ascii=False, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    artifacts.append(report_path)
    if make_plots:
        artifacts.extend(_plot_replay(result, directory))
    manifest = {
        "claim_scope": result.report["claim_scope"],
        "artifacts": [
            {"file": path.name, "sha256": sha256_file(path), "bytes": path.stat().st_size}
            for path in artifacts
        ],
    }
    manifest_path = directory / "artifact_manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return manifest


__all__ = [
    "BristolQuantumReplayConfig",
    "BristolQuantumReplayResult",
    "build_bristol_replay_windows",
    "run_bristol_quantum_replay",
    "simulate_bell_fidelity_from_recorded_ber",
    "write_bristol_quantum_replay_artifacts",
]
