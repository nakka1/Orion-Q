"""Leakage-safe prediction from paired WDM and measured quantum telemetry.

This module is deliberately unusable with the current public QKD coexistence
sweeps: those data do not contain timestamps.  It accepts only the strict
``PairedQuantumTelemetrySource`` contract and therefore cannot silently turn a
controlled sweep or a simulated fidelity into experimental forecasting
evidence.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from pathlib import Path

import numpy as np
import pandas as pd

from quantum_twin.evaluation.optical_campaign import (
    git_state,
    rolling_origin_splits,
    sha256_file,
)
from quantum_twin.evaluation.wdm_information import (
    SUPPORTED_MODELS,
    WindowedOpticalData,
    build_evidence_table,
    compare_incremental_information,
    compute_information_metrics,
    evaluate_incremental_fold,
)
from quantum_twin.ml.optical_sequence import OPTICAL_SEQUENCE_MODELS
from quantum_twin.quantum.paired_telemetry import (
    PairedQuantumProfile,
    PairedQuantumTelemetrySource,
)


MEASURED_QUANTUM_TARGETS = (
    "qber_ratio",
    "log1p_secure_key_rate_bps",
)
MEASURED_WDM_FEATURES = (
    "log10_pre_fec_ber",
    "osnr_db",
    "rx_power_dbm",
    "tx_power_dbm",
)


@dataclass(frozen=True)
class MeasuredQuantumPredictionConfig:
    """Predeclared protocol for future QBER/SKR prediction."""

    targets: tuple[str, ...] = MEASURED_QUANTUM_TARGETS
    horizons: tuple[int, ...] = (1, 4)
    models: tuple[str, ...] = OPTICAL_SEQUENCE_MODELS
    seeds: tuple[int, ...] = (42,)
    window_size: int = 20
    n_splits: int = 3
    min_train_fraction: float = 0.5
    requested_purge_steps: int = 0
    cross_group_test_fraction: float = 0.3
    bootstrap_replicates: int = 2_000
    bootstrap_block_length: int = 20
    familywise_alpha: float = 0.05
    ber_floor: float = 1e-12
    hidden_size: int = 16
    max_epochs: int = 40
    batch_size: int = 256
    early_stopping_patience: int = 6
    learning_rate: float = 1e-3

    def validate(self) -> None:
        unknown_targets = set(self.targets).difference(MEASURED_QUANTUM_TARGETS)
        unknown_models = set(self.models).difference(SUPPORTED_MODELS)
        if unknown_targets:
            raise ValueError(f"unsupported measured quantum target(s): {sorted(unknown_targets)}")
        if unknown_models:
            raise ValueError(f"unsupported model(s): {sorted(unknown_models)}")
        if not self.targets or not self.horizons or not self.models or not self.seeds:
            raise ValueError("targets, horizons, models and seeds cannot be empty")
        if any(int(value) < 1 for value in self.horizons):
            raise ValueError("horizons must be >= 1")
        if self.window_size < 2 or self.n_splits < 1:
            raise ValueError("window_size must be >= 2 and n_splits must be >= 1")
        if self.requested_purge_steps < 0:
            raise ValueError("requested_purge_steps must be non-negative")
        if not 0.0 < self.min_train_fraction < 1.0:
            raise ValueError("min_train_fraction must be between zero and one")
        if not 0.0 < self.cross_group_test_fraction < 1.0:
            raise ValueError("cross_group_test_fraction must be between zero and one")
        if self.bootstrap_replicates < 1 or self.bootstrap_block_length < 1:
            raise ValueError("bootstrap settings must be >= 1")
        if not 0.0 < self.familywise_alpha < 1.0:
            raise ValueError("familywise_alpha must be between zero and one")
        if self.hidden_size < 1 or self.max_epochs < 1 or self.batch_size < 1:
            raise ValueError("neural training sizes must be >= 1")
        if self.early_stopping_patience < 1 or self.learning_rate <= 0.0:
            raise ValueError("patience must be >= 1 and learning rate must be positive")


@dataclass
class MeasuredQuantumPredictionResult:
    report: dict
    predictions: pd.DataFrame
    metrics: pd.DataFrame
    comparisons: pd.DataFrame
    evidence: pd.DataFrame


def _target_series(frame: pd.DataFrame, target: str) -> tuple[pd.Series, str]:
    if target == "qber_ratio":
        return pd.to_numeric(frame["qber_ratio"], errors="coerce"), "ratio"
    if target == "log1p_secure_key_rate_bps":
        values = pd.to_numeric(frame["secure_key_rate_bps"], errors="coerce")
        return np.log1p(values), "log1p(bit/s)"
    raise ValueError(f"unsupported measured quantum target {target!r}")


def build_paired_quantum_windows(
    data: pd.DataFrame,
    *,
    target: str,
    horizon: int,
    window_size: int,
    ber_floor: float = 1e-12,
) -> WindowedOpticalData:
    """Build causal windows independently for every campaign/device/channel."""
    if int(horizon) < 1 or int(window_size) < 2:
        raise ValueError("horizon must be >= 1 and window_size must be >= 2")
    prepared = data.copy()
    current_target, target_unit = _target_series(prepared, target)
    prepared["current_target_value"] = current_target
    prepared["log10_pre_fec_ber"] = np.log10(
        pd.to_numeric(prepared["pre_fec_ber"], errors="coerce").clip(lower=float(ber_floor))
    )
    for column in ("osnr_db", "rx_power_dbm", "tx_power_dbm"):
        prepared[column] = pd.to_numeric(prepared[column], errors="coerce")

    series_keys = ["campaign_id", "device_id", "channel_id"]
    windows: list[np.ndarray] = []
    metadata: list[dict] = []
    feature_columns = ["current_target_value", *MEASURED_WDM_FEATURES]
    for key, group in prepared.groupby(series_keys, sort=True, observed=True):
        campaign, device, channel = (str(value) for value in key)
        ordered = group.sort_values("timestamp_utc", kind="stable").reset_index(drop=True)
        ordered["target_value"] = ordered["current_target_value"].shift(-int(horizon))
        ordered["target_timestamp_utc"] = ordered["timestamp_utc"].shift(-int(horizon))
        values = ordered[feature_columns].to_numpy(dtype=float)
        for end in range(int(window_size) - 1, len(ordered) - int(horizon)):
            row = ordered.iloc[end]
            target_value = float(row["target_value"])
            current_value = float(row["current_target_value"])
            if not np.isfinite(target_value) or not np.isfinite(current_value):
                continue
            windows.append(values[end - int(window_size) + 1:end + 1])
            metadata.append({
                "device_id": f"{campaign}|{device}|{channel}",
                "campaign_id": campaign,
                "source_device_id": device,
                "channel_id": channel,
                "timestamp_utc": row["timestamp_utc"],
                "target_timestamp_utc": row["target_timestamp_utc"],
                "target_value": target_value,
                "current_target_value": current_value,
                "target_unit": target_unit,
            })
    if not windows:
        raise ValueError(
            "no causal windows could be built; each series needs at least "
            "window_size + horizon synchronized rows"
        )
    metadata_frame = pd.DataFrame.from_records(metadata).reset_index(drop=True)
    if not (
        metadata_frame["target_timestamp_utc"] > metadata_frame["timestamp_utc"]
    ).all():
        raise RuntimeError("a measured quantum label is not strictly in the future")
    return WindowedOpticalData(
        metadata=metadata_frame,
        X=np.stack(windows).astype(np.float64, copy=False),
        feature_columns=feature_columns,
        target_feature="current_target_value",
        wdm_feature_columns=list(MEASURED_WDM_FEATURES),
    )


def _cross_group_splits(
    metadata: pd.DataFrame,
    *,
    group_column: str,
    evaluation: str,
    test_fraction: float,
) -> tuple[list[dict], list[dict]]:
    """Time-safe held-group evaluation; return explicit skipped-group reasons."""
    groups = sorted(metadata[group_column].astype(str).unique())
    if len(groups) < 2:
        return [], [{"group": groups[0] if groups else None, "reason": "fewer than two groups"}]
    splits: list[dict] = []
    skipped: list[dict] = []
    for held_group in groups:
        held = metadata.loc[metadata[group_column].astype(str).eq(held_group)]
        tests: list[pd.DataFrame] = []
        for _, series in held.groupby("device_id", sort=True, observed=True):
            ordered = series.sort_values("timestamp_utc", kind="stable")
            start = int(np.floor(len(ordered) * (1.0 - float(test_fraction))))
            tests.append(ordered.iloc[start:])
        test = pd.concat(tests, axis=0).sort_values("timestamp_utc", kind="stable")
        if test.empty:
            skipped.append({"group": held_group, "reason": "empty held-group test tail"})
            continue
        cutoff = test["timestamp_utc"].min()
        train = metadata.loc[
            metadata[group_column].astype(str).ne(held_group)
            & metadata["target_timestamp_utc"].lt(cutoff)
        ]
        if len(train) < 4:
            skipped.append({
                "group": held_group,
                "reason": "fewer than four time-safe training windows from other groups",
            })
            continue
        splits.append({
            "evaluation": evaluation,
            "fold": f"holdout_{held_group}",
            "train_indices": train.index.to_numpy(dtype=int),
            "test_indices": test.index.to_numpy(dtype=int),
            "devices": {
                held_group: {
                    "group_column": group_column,
                    "train_rows": int(len(train)),
                    "test_rows": int(len(test)),
                    "train_target_end_utc": train["target_timestamp_utc"].max().isoformat(),
                    "test_feature_start_utc": cutoff.isoformat(),
                    "test_feature_end_utc": test["timestamp_utc"].max().isoformat(),
                }
            },
        })
    return splits, skipped


def run_measured_quantum_prediction_campaign(
    data_path: str | Path,
    *,
    manifest_path: str | Path | None = None,
    config: MeasuredQuantumPredictionConfig = MeasuredQuantumPredictionConfig(),
) -> MeasuredQuantumPredictionResult:
    """Run QBER/SKR forecasts only after strict experimental validation."""
    config.validate()
    path = Path(data_path)
    source = PairedQuantumTelemetrySource(
        path,
        manifest_path=manifest_path,
        profile=PairedQuantumProfile.MEASURED_QKD,
    )
    raw = source.require_valid()
    predictions: list[pd.DataFrame] = []
    fold_manifests: list[dict] = []
    skipped_cross_group: list[dict] = []
    feature_manifests: list[dict] = []

    for target in config.targets:
        for horizon in config.horizons:
            windowed = build_paired_quantum_windows(
                raw,
                target=target,
                horizon=horizon,
                window_size=config.window_size,
                ber_floor=config.ber_floor,
            )
            purge_steps = max(
                int(config.requested_purge_steps),
                int(config.window_size + horizon),
            )
            splits = rolling_origin_splits(
                windowed.metadata,
                n_splits=config.n_splits,
                min_train_fraction=config.min_train_fraction,
                purge_steps=purge_steps,
            )
            campaign_splits, campaign_skips = _cross_group_splits(
                windowed.metadata,
                group_column="campaign_id",
                evaluation="campaign_holdout",
                test_fraction=config.cross_group_test_fraction,
            )
            device_splits, device_skips = _cross_group_splits(
                windowed.metadata,
                group_column="source_device_id",
                evaluation="device_holdout",
                test_fraction=config.cross_group_test_fraction,
            )
            splits.extend(campaign_splits)
            splits.extend(device_splits)
            for number, split in enumerate(splits, start=1):
                split["fold_number"] = number
                fold_manifests.append({
                    "target": target,
                    "horizon_steps": int(horizon),
                    "evaluation": split["evaluation"],
                    "fold": split["fold"],
                    "purge_steps": purge_steps if split["evaluation"] == "rolling_origin" else None,
                    "groups": split["devices"],
                })
            for item in campaign_skips:
                skipped_cross_group.append({
                    "target": target, "horizon_steps": int(horizon),
                    "evaluation": "campaign_holdout", **item,
                })
            for item in device_skips:
                skipped_cross_group.append({
                    "target": target, "horizon_steps": int(horizon),
                    "evaluation": "device_holdout", **item,
                })
            feature_manifests.append({
                "target": target,
                "horizon_steps": int(horizon),
                "target_history_feature": windowed.target_feature,
                "wdm_features": windowed.wdm_feature_columns,
                "window_size": int(config.window_size),
                "samples": int(len(windowed.metadata)),
            })
            for seed in config.seeds:
                for split in splits:
                    frame = evaluate_incremental_fold(
                        windowed,
                        split,
                        target=target,
                        horizon=horizon,
                        seed=seed,
                        config=config,
                    )
                    frame.insert(0, "evaluation", split["evaluation"])
                    predictions.append(frame)

    prediction_frame = pd.concat(predictions, ignore_index=True)
    metrics = compute_information_metrics(prediction_frame)
    comparisons = compare_incremental_information(
        prediction_frame,
        block_length=config.bootstrap_block_length,
        replicates=config.bootstrap_replicates,
        seed=config.seeds[0],
        familywise_alpha=config.familywise_alpha,
    )
    evidence = build_evidence_table(comparisons)
    report = {
        "study": "prediction of measured future QBER/SKR from synchronized WDM telemetry",
        "status": "experimental only after strict paired-source validation",
        "central_estimand": "MAE(target history) - MAE(target history + aligned WDM)",
        "confirmation_rule": (
            "The multiplicity-corrected moving-block-bootstrap lower bound must exceed zero "
            "against both own-target history and temporally permuted WDM."
        ),
        "claim_scope": (
            "Tests whether classical optical conditions carry incremental predictive "
            "information about future measured QBER and secure-key rate."
        ),
        "not_claimed": [
            "QBER is not Bell-state fidelity.",
            "Association is not a mechanistic causal estimate without intervention/randomization.",
            "A result from one device or campaign is not universal until transfer tests replicate it.",
        ],
        "dataset": {
            "path": str(path.resolve()),
            "sha256": sha256_file(path),
            "manifest_path": str(source.manifest_path.resolve()),
            "manifest_sha256": sha256_file(source.manifest_path),
            "quality": source.quality_report(raw),
        },
        "configuration": asdict(config),
        "features": feature_manifests,
        "models": {
            "original_repository_backbones_adapted": list(OPTICAL_SEQUENCE_MODELS),
            "adaptation": "fidelity sigmoid replaced by identity; Huber regression",
            "baselines_and_controls": [
                "persistence", "own-target autoregression", "temporally permuted WDM",
            ],
        },
        "validation": {
            "rolling_origin": "expanding temporal folds with window+horizon purge",
            "campaign_holdout": "unseen campaign, training labels strictly before test issue time",
            "device_holdout": "unseen device, training labels strictly before test issue time",
            "folds": fold_manifests,
            "skipped_cross_group_folds": skipped_cross_group,
        },
        "multiple_comparison_correction": (
            "Bonferroni over targets, horizons, models, evaluation regimes and both contrasts"
        ),
        "evidence_positive_count": int(
            evidence["evidence_for_incremental_wdm_information"].sum()
        ),
        "evidence_test_count": int(len(evidence)),
        "git": git_state(),
    }
    return MeasuredQuantumPredictionResult(
        report=report,
        predictions=prediction_frame,
        metrics=metrics,
        comparisons=comparisons,
        evidence=evidence,
    )


def write_measured_quantum_prediction_artifacts(
    result: MeasuredQuantumPredictionResult,
    output_dir: str | Path,
    *,
    include_predictions: bool = True,
) -> dict:
    directory = Path(output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    artifacts: list[Path] = []
    tables = {
        "fold_metrics.csv": result.metrics,
        "corrected_incremental_comparisons.csv": result.comparisons,
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
    manifest = {
        "claim_scope": result.report["claim_scope"],
        "artifacts": [
            {"file": path.name, "sha256": sha256_file(path), "bytes": path.stat().st_size}
            for path in artifacts
        ],
    }
    (directory / "artifact_manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return manifest


__all__ = [
    "MEASURED_QUANTUM_TARGETS",
    "MEASURED_WDM_FEATURES",
    "MeasuredQuantumPredictionConfig",
    "MeasuredQuantumPredictionResult",
    "build_paired_quantum_windows",
    "run_measured_quantum_prediction_campaign",
    "write_measured_quantum_prediction_artifacts",
]
