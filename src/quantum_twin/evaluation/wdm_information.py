"""Test whether WDM telemetry adds predictive information beyond target history.

This campaign operationalizes the optical prerequisite of the thesis as

    I(WDM_past; optical_target_future | optical_target_history) > 0.

It does not claim that the public Sant'Anna data contain a measured quantum
target.  Instead, it asks the narrower falsifiable question of whether aligned
multi-equipment WDM measurements improve future BER/OSNR forecasts after the
strong autoregressive explanation has already been included.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from pathlib import Path
from typing import Sequence

import numpy as np
import pandas as pd

from quantum_twin.evaluation.optical_campaign import (
    build_optical_model_pipeline,
    git_state,
    paired_moving_block_bootstrap,
    rolling_origin_splits,
    sha256_file,
)
from quantum_twin.ml.optical_sequence import (
    OPTICAL_SEQUENCE_MODELS,
    make_optical_sequence_model,
    predict_optical_sequence_model,
    train_optical_sequence_model,
)
from quantum_twin.optical.santanna import SantAnnaOpticalFailureSource


TABULAR_MODELS = ("ridge", "hist_gradient_boosting")
SUPPORTED_MODELS = TABULAR_MODELS + OPTICAL_SEQUENCE_MODELS
FEATURE_SETS = ("autoregressive", "wdm_only", "combined", "permuted_wdm")
SUPPORTED_TARGETS = ("osnr_db", "pre_fec_ber")


@dataclass(frozen=True)
class WDMInformationConfig:
    """Fully recorded protocol for an incremental-information campaign."""

    targets: tuple[str, ...] = SUPPORTED_TARGETS
    horizons: tuple[int, ...] = (1, 3, 10, 30)
    models: tuple[str, ...] = SUPPORTED_MODELS
    seeds: tuple[int, ...] = (42,)
    window_size: int = 20
    n_splits: int = 3
    min_train_fraction: float = 0.5
    requested_purge_steps: int = 0
    alignment_tolerance_s: float = 5.0
    bootstrap_replicates: int = 2_000
    bootstrap_block_length: int = 30
    familywise_alpha: float = 0.05
    ber_floor: float = 1e-12
    hidden_size: int = 16
    max_epochs: int = 40
    batch_size: int = 256
    early_stopping_patience: int = 6
    learning_rate: float = 1e-3

    def validate(self) -> None:
        unknown_targets = set(self.targets).difference(SUPPORTED_TARGETS)
        unknown_models = set(self.models).difference(SUPPORTED_MODELS)
        if unknown_targets:
            raise ValueError(f"unsupported target(s): {sorted(unknown_targets)}")
        if unknown_models:
            raise ValueError(f"unsupported model(s): {sorted(unknown_models)}")
        if not self.targets or not self.horizons or not self.models or not self.seeds:
            raise ValueError("targets, horizons, models and seeds cannot be empty")
        if any(value < 1 for value in self.horizons):
            raise ValueError("horizons must be >= 1")
        if self.window_size < 2:
            raise ValueError("window_size must be >= 2")
        if self.n_splits < 1 or self.requested_purge_steps < 0:
            raise ValueError("n_splits must be >= 1 and purge must be >= 0")
        if not 0.0 < self.min_train_fraction < 1.0:
            raise ValueError("min_train_fraction must be between 0 and 1")
        if self.alignment_tolerance_s < 0:
            raise ValueError("alignment_tolerance_s must be >= 0")
        if self.bootstrap_replicates < 1 or self.bootstrap_block_length < 1:
            raise ValueError("bootstrap settings must be >= 1")
        if not 0.0 < self.familywise_alpha < 1.0:
            raise ValueError("familywise_alpha must be between 0 and 1")
        if self.hidden_size < 1 or self.max_epochs < 1 or self.batch_size < 1:
            raise ValueError("neural training sizes must be >= 1")


@dataclass
class WindowedOpticalData:
    metadata: pd.DataFrame
    X: np.ndarray
    feature_columns: list[str]
    target_feature: str
    wdm_feature_columns: list[str]


@dataclass
class WDMInformationResult:
    report: dict
    predictions: pd.DataFrame
    metrics: pd.DataFrame
    comparisons: pd.DataFrame
    evidence: pd.DataFrame


def _log10(values: pd.Series, floor: float) -> pd.Series:
    numeric = pd.to_numeric(values, errors="coerce")
    return np.log10(numeric.clip(lower=float(floor)))


def _prepare_features(
    panel: pd.DataFrame,
    *,
    target: str,
    ber_floor: float,
) -> tuple[pd.DataFrame, str, list[str]]:
    result = panel.copy()
    ber_columns = [
        column for column in result.columns
        if column == "pre_fec_ber" or column.startswith("pre_fec_ber__")
    ]
    for column in ber_columns:
        transformed = "log10_pre_fec_ber" + column[len("pre_fec_ber"):]
        result[transformed] = _log10(result[column], ber_floor)

    target_feature = "osnr_db" if target == "osnr_db" else "log10_pre_fec_ber"
    if target == "pre_fec_ber":
        result["target_value"] = _log10(result["target_value_raw"], ber_floor)
        target_unit = "log10(BER)"
    else:
        result["target_value"] = pd.to_numeric(result["target_value_raw"], errors="coerce")
        target_unit = "dB"
    result["current_target_value"] = pd.to_numeric(result[target_feature], errors="coerce")
    result["target_unit"] = target_unit
    result["device_id"] = result["anchor_device_id"].astype(str)

    candidates: list[str] = []
    for column in result.columns:
        is_cross_signal = (
            column.startswith("osnr_db__")
            or column.startswith("log10_pre_fec_ber__")
            or column.startswith("input_power_dbm__")
            or column.startswith("output_power_dbm__")
            or column.startswith("age_s__")
        )
        is_anchor_complement = (
            (target != "osnr_db" and column == "osnr_db")
            or (target != "pre_fec_ber" and column == "log10_pre_fec_ber")
        )
        if is_cross_signal or is_anchor_complement:
            candidates.append(column)

    # The anchor age is identically zero and all-null structural columns do
    # not contain information.  Availability is a schema property; no
    # distributional estimate from future values is used here.
    wdm_features = [
        column for column in dict.fromkeys(candidates)
        if column != target_feature
        and not result[column].isna().all()
        and result[column].nunique(dropna=True) > 1
    ]
    if not wdm_features:
        raise ValueError("no non-target WDM features are available")
    return result, target_feature, wdm_features


def build_device_local_windows(
    panel: pd.DataFrame,
    *,
    target: str,
    window_size: int,
    ber_floor: float = 1e-12,
) -> WindowedOpticalData:
    """Build windows independently per anchor, never across device boundaries."""
    prepared, target_feature, wdm_features = _prepare_features(
        panel, target=target, ber_floor=ber_floor
    )
    feature_columns = [target_feature] + wdm_features
    windows: list[np.ndarray] = []
    metadata: list[dict] = []
    for device, group in prepared.groupby("device_id", sort=True, observed=True):
        ordered = group.sort_values("timestamp_utc", kind="stable").reset_index(drop=True)
        values = ordered[feature_columns].to_numpy(dtype=float)
        for end in range(int(window_size) - 1, len(ordered)):
            row = ordered.iloc[end]
            target_value = float(row["target_value"])
            current_value = float(row["current_target_value"])
            if not np.isfinite(target_value) or not np.isfinite(current_value):
                continue
            window = values[end - int(window_size) + 1:end + 1]
            windows.append(window)
            metadata.append({
                "device_id": str(device),
                "timestamp_utc": row["timestamp_utc"],
                "target_timestamp_utc": row["target_timestamp_utc"],
                "target_value": target_value,
                "current_target_value": current_value,
                "target_unit": row["target_unit"],
            })
    if not windows:
        raise ValueError("no windows could be built")
    metadata_frame = pd.DataFrame.from_records(metadata).reset_index(drop=True)
    if not (metadata_frame["target_timestamp_utc"] > metadata_frame["timestamp_utc"]).all():
        raise RuntimeError("a forecast label is not strictly later than its issue time")
    return WindowedOpticalData(
        metadata=metadata_frame,
        X=np.stack(windows).astype(np.float64, copy=False),
        feature_columns=feature_columns,
        target_feature=target_feature,
        wdm_feature_columns=wdm_features,
    )


def _permuted_cross_wdm(
    X: np.ndarray,
    devices: Sequence[str],
    *,
    seed: int,
) -> np.ndarray:
    """Break aligned WDM/target coupling while preserving target histories.

    Entire WDM windows are permuted within each device and split.  Thus the
    control preserves every WDM sequence's autocorrelation and marginal
    distribution, but removes its physical-time alignment with the target.
    """
    result = np.asarray(X).copy()
    device_values = np.asarray(devices, dtype=str)
    rng = np.random.default_rng(int(seed))
    for device in sorted(set(device_values)):
        positions = np.flatnonzero(device_values == device)
        permutation = rng.permutation(positions)
        result[positions, :, 1:] = result[permutation, :, 1:]
    return result


def _training_only_scale_sequences(
    X_train: np.ndarray,
    X_test: np.ndarray,
    y_train: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, float, float, dict]:
    n_features = X_train.shape[2]
    flat_train = X_train.reshape(-1, n_features)
    medians = np.zeros(n_features, dtype=float)
    for index in range(n_features):
        finite = flat_train[np.isfinite(flat_train[:, index]), index]
        medians[index] = float(np.median(finite)) if len(finite) else 0.0

    def impute(values: np.ndarray) -> np.ndarray:
        copy = np.asarray(values, dtype=float).copy()
        missing = ~np.isfinite(copy)
        if missing.any():
            copy[missing] = np.broadcast_to(medians, copy.shape)[missing]
        return copy

    train_imputed = impute(X_train)
    test_imputed = impute(X_test)
    flat_imputed = train_imputed.reshape(-1, n_features)
    means = flat_imputed.mean(axis=0)
    standard_deviations = flat_imputed.std(axis=0)
    standard_deviations[standard_deviations < 1e-8] = 1.0
    train_scaled = (train_imputed - means) / standard_deviations
    test_scaled = (test_imputed - means) / standard_deviations
    target_mean = float(np.mean(y_train))
    target_scale = float(np.std(y_train))
    if target_scale < 1e-8:
        target_scale = 1.0
    y_scaled = (np.asarray(y_train, dtype=float) - target_mean) / target_scale
    manifest = {
        "fit_scope": "training split only",
        "imputation": "per-feature median",
        "scaling": "per-feature z-score",
        "target_scaling": "z-score",
    }
    return train_scaled, test_scaled, y_scaled, target_mean, target_scale, manifest


def _feature_set_arrays(
    X_train: np.ndarray,
    X_test: np.ndarray,
    train_devices: Sequence[str],
    test_devices: Sequence[str],
    *,
    feature_set: str,
    seed: int,
) -> tuple[np.ndarray, np.ndarray]:
    if feature_set == "autoregressive":
        return X_train[:, :, :1], X_test[:, :, :1]
    if feature_set == "wdm_only":
        return X_train[:, :, 1:], X_test[:, :, 1:]
    if feature_set == "combined":
        return X_train, X_test
    if feature_set == "permuted_wdm":
        return (
            _permuted_cross_wdm(X_train, train_devices, seed=seed),
            _permuted_cross_wdm(X_test, test_devices, seed=seed + 1_000_003),
        )
    raise ValueError(f"unknown feature set {feature_set!r}")


def _prediction_records(
    metadata: pd.DataFrame,
    test_indices: np.ndarray,
    prediction: np.ndarray,
    *,
    target: str,
    horizon: int,
    seed: int,
    fold: str,
    model: str,
    feature_set: str,
    fit_note: str,
) -> pd.DataFrame:
    test = metadata.loc[test_indices]
    y_true = test["target_value"].to_numpy(dtype=float)
    predicted = np.asarray(prediction, dtype=float)
    records = {
        "target": target,
        "target_unit": test["target_unit"].to_numpy(),
        "horizon_steps": int(horizon),
        "seed": int(seed),
        "fold": fold,
        "device_id": test["device_id"].astype(str).to_numpy(),
        "timestamp_utc": test["timestamp_utc"].to_numpy(),
        "target_timestamp_utc": test["target_timestamp_utc"].to_numpy(),
        "model": model,
        "feature_set": feature_set,
        "fit_note": fit_note,
        "y_true": y_true,
        "prediction": predicted,
        "absolute_error": np.abs(y_true - predicted),
        "squared_error": (y_true - predicted) ** 2,
    }
    # Measured quantum campaigns carry identifiers that must survive all the
    # way to the out-of-fold artifact.  Optical-only callers need not provide
    # them, which preserves the public Sant'Anna/Bristol APIs.
    for column in ("campaign_id", "source_device_id", "channel_id"):
        if column in test:
            records[column] = test[column].astype(str).to_numpy()
    return pd.DataFrame(records)


def evaluate_incremental_fold(
    windowed: WindowedOpticalData,
    split: dict,
    *,
    target: str,
    horizon: int,
    seed: int,
    config: WDMInformationConfig,
) -> pd.DataFrame:
    train_indices = split["train_indices"]
    test_indices = split["test_indices"]
    metadata = windowed.metadata
    # Interleave devices chronologically so the neural validation tail is a
    # temporal tail of the link rather than the tail of one concatenated device.
    train_order = np.argsort(
        metadata.loc[train_indices, "timestamp_utc"].to_numpy(), kind="stable"
    )
    train_indices = train_indices[train_order]
    X_train_full = windowed.X[train_indices]
    X_test_full = windowed.X[test_indices]
    y_train = metadata.loc[train_indices, "target_value"].to_numpy(dtype=float)
    train_devices = metadata.loc[train_indices, "device_id"].astype(str).to_numpy()
    test_devices = metadata.loc[test_indices, "device_id"].astype(str).to_numpy()

    records = [_prediction_records(
        metadata,
        test_indices,
        metadata.loc[test_indices, "current_target_value"].to_numpy(dtype=float),
        target=target,
        horizon=horizon,
        seed=seed,
        fold=split["fold"],
        model="persistence",
        feature_set="persistence",
        fit_note="current target; no fitted parameter",
    )]
    for model_name in config.models:
        for feature_set in FEATURE_SETS:
            control_seed = int(seed + horizon * 10_000 + split["fold_number"] * 100)
            X_train, X_test = _feature_set_arrays(
                X_train_full,
                X_test_full,
                train_devices,
                test_devices,
                feature_set=feature_set,
                seed=control_seed,
            )
            if model_name in TABULAR_MODELS:
                pipeline = build_optical_model_pipeline(model_name, classifier=False, seed=seed)
                pipeline.fit(X_train.reshape(len(X_train), -1), y_train)
                prediction = pipeline.predict(X_test.reshape(len(X_test), -1))
                fit_note = "fixed hyperparameters; train-only median/scaling"
            else:
                (
                    X_train_scaled,
                    X_test_scaled,
                    y_train_scaled,
                    target_mean,
                    target_scale,
                    preprocessing,
                ) = _training_only_scale_sequences(X_train, X_test, y_train)
                model = make_optical_sequence_model(
                    model_name,
                    input_size=X_train_scaled.shape[2],
                    hidden_size=config.hidden_size,
                )
                model, training = train_optical_sequence_model(
                    model,
                    X_train_scaled,
                    y_train_scaled,
                    seed=seed,
                    max_epochs=config.max_epochs,
                    batch_size=config.batch_size,
                    patience=config.early_stopping_patience,
                    learning_rate=config.learning_rate,
                )
                prediction_scaled = predict_optical_sequence_model(model, X_test_scaled)
                prediction = prediction_scaled * target_scale + target_mean
                fit_note = (
                    f"{preprocessing['fit_scope']}; Huber; best_epoch_count="
                    f"{training.epochs_trained}; early_stopped={training.stopped_early}"
                )
            records.append(_prediction_records(
                metadata,
                test_indices,
                prediction,
                target=target,
                horizon=horizon,
                seed=seed,
                fold=split["fold"],
                model=model_name,
                feature_set=feature_set,
                fit_note=fit_note,
            ))
    return pd.concat(records, ignore_index=True)


def compute_information_metrics(predictions: pd.DataFrame) -> pd.DataFrame:
    evaluation_keys = ["evaluation"] if "evaluation" in predictions else []
    keys = evaluation_keys + [
        "target", "target_unit", "horizon_steps", "seed", "fold",
        "model", "feature_set",
    ]
    records: list[dict] = []
    for key, group in predictions.groupby(keys, sort=True, observed=True):
        common = dict(zip(keys, key))
        for device, subset in list(group.groupby("device_id", sort=True, observed=True)) + [
            ("__all__", group)
        ]:
            records.append({
                **common,
                "device_id": str(device),
                "n": int(len(subset)),
                "mae": float(subset["absolute_error"].mean()),
                "rmse": float(np.sqrt(subset["squared_error"].mean())),
            })
    return pd.DataFrame.from_records(records)


def compare_incremental_information(
    predictions: pd.DataFrame,
    *,
    block_length: int,
    replicates: int,
    seed: int,
    familywise_alpha: float,
) -> pd.DataFrame:
    """Paired, multiplicity-corrected tests of the two required contrasts."""
    learned = predictions.loc[predictions["model"].ne("persistence")].copy()
    evaluation_keys = ["evaluation"] if "evaluation" in learned else []
    sample_keys = evaluation_keys + [
        "target", "target_unit", "horizon_steps", "fold", "device_id",
        "timestamp_utc", "target_timestamp_utc", "model",
    ]
    averaged = learned.groupby(sample_keys + ["feature_set"], as_index=False, observed=True)[
        "absolute_error"
    ].mean()
    pivot = averaged.pivot(
        index=sample_keys, columns="feature_set", values="absolute_error"
    ).reset_index()
    required = {"autoregressive", "combined", "permuted_wdm"}
    if not required.issubset(pivot.columns):
        raise ValueError(f"comparisons require feature sets {sorted(required)}")

    task_keys = evaluation_keys + ["target", "target_unit", "horizon_steps", "model"]
    tasks = list(pivot.groupby(
        task_keys,
        sort=True,
        observed=True,
    ))
    comparisons_per_task = 2
    family_size = max(len(tasks) * comparisons_per_task, 1)
    corrected_alpha = familywise_alpha / family_size
    records: list[dict] = []
    contrast_definitions = (
        ("incremental_over_autoregressive", "autoregressive"),
        ("aligned_over_permuted_control", "permuted_wdm"),
    )
    for task_offset, (key, task) in enumerate(tasks):
        for contrast_offset, (contrast, reference) in enumerate(contrast_definitions):
            paired = task.dropna(subset=[reference, "combined"]).copy()
            paired["improvement"] = paired[reference] - paired["combined"]
            series = [
                group.sort_values("timestamp_utc")["improvement"].to_numpy(dtype=float)
                for _, group in paired.groupby(["fold", "device_id"], sort=True, observed=True)
            ]
            low, high = paired_moving_block_bootstrap(
                series,
                block_length=block_length,
                replicates=replicates,
                seed=seed + task_offset * comparisons_per_task + contrast_offset,
                alpha=corrected_alpha,
            )
            reference_loss = float(paired[reference].mean())
            combined_loss = float(paired["combined"].mean())
            improvement = reference_loss - combined_loss
            records.append({
                **dict(zip(task_keys, key)),
                "contrast": contrast,
                "reference_feature_set": reference,
                "candidate_feature_set": "combined",
                "n_unique_samples": int(len(paired)),
                "reference_mae": reference_loss,
                "combined_mae": combined_loss,
                "absolute_improvement": improvement,
                "relative_improvement_percent": (
                    100.0 * improvement / reference_loss if reference_loss != 0.0 else np.nan
                ),
                "ci_low": low,
                "ci_high": high,
                "familywise_alpha": familywise_alpha,
                "per_comparison_alpha": corrected_alpha,
                "hypothesis_family_size": family_size,
                "multiple_comparison_correction": "Bonferroni",
                "significant_positive_improvement": bool(improvement > 0.0 and low > 0.0),
            })
    return pd.DataFrame.from_records(records)


def build_evidence_table(comparisons: pd.DataFrame) -> pd.DataFrame:
    """Require both incremental gain and victory over the misalignment control."""
    evaluation_keys = ["evaluation"] if "evaluation" in comparisons else []
    index = evaluation_keys + ["target", "target_unit", "horizon_steps", "model"]
    significance = comparisons.pivot(
        index=index, columns="contrast", values="significant_positive_improvement"
    ).reset_index()
    effects = comparisons.pivot(
        index=index, columns="contrast", values="absolute_improvement"
    ).reset_index()
    effects = effects.rename(columns={
        "incremental_over_autoregressive": "incremental_mae_improvement",
        "aligned_over_permuted_control": "alignment_control_mae_improvement",
    })
    result = significance.merge(effects, on=index, how="left")
    for column in ("incremental_over_autoregressive", "aligned_over_permuted_control"):
        if column not in result:
            result[column] = False
    result["evidence_for_incremental_wdm_information"] = (
        result["incremental_over_autoregressive"].astype(bool)
        & result["aligned_over_permuted_control"].astype(bool)
    )
    return result.sort_values(index, kind="stable").reset_index(drop=True)


def run_wdm_information_campaign(
    csv_path: str | Path,
    *,
    config: WDMInformationConfig = WDMInformationConfig(),
) -> WDMInformationResult:
    """Run the complete leakage-safe incremental-information campaign."""
    config.validate()
    path = Path(csv_path)
    source = SantAnnaOpticalFailureSource(str(path))
    raw = source.read()
    predictions: list[pd.DataFrame] = []
    fold_manifests: list[dict] = []
    feature_manifests: list[dict] = []

    for target in config.targets:
        for horizon in config.horizons:
            panel = source.build_causal_forecast_panel(
                raw,
                target=target,
                horizon_steps=horizon,
                tolerance_s=config.alignment_tolerance_s,
            )
            windowed = build_device_local_windows(
                panel,
                target=target,
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
            for number, split in enumerate(splits, start=1):
                split["fold_number"] = number
                fold_manifests.append({
                    "target": target,
                    "horizon_steps": int(horizon),
                    "fold": split["fold"],
                    "purge_steps": purge_steps,
                    "devices": split["devices"],
                })
            feature_manifests.append({
                "target": target,
                "horizon_steps": int(horizon),
                "target_history_feature": windowed.target_feature,
                "wdm_features": windowed.wdm_feature_columns,
                "window_size": config.window_size,
                "samples": int(len(windowed.metadata)),
            })
            for seed in config.seeds:
                for split in splits:
                    predictions.append(evaluate_incremental_fold(
                        windowed,
                        split,
                        target=target,
                        horizon=horizon,
                        seed=seed,
                        config=config,
                    ))

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
    quality = source.quality_report(raw)
    report = {
        "study": "incremental predictive information in recorded WDM telemetry",
        "claim_scope": (
            "Evaluation of whether recorded WDM telemetry predicts future recorded optical "
            "BER/OSNR beyond the target's own history; this tests an optical prerequisite, "
            "not quantum fidelity."
        ),
        "central_estimand": "MAE(autoregressive) - MAE(target history + aligned WDM)",
        "confirmation_rule": (
            "Corrected block-bootstrap lower bounds must exceed zero for both the "
            "autoregressive and within-device temporally permuted-WDM contrasts."
        ),
        "dataset": {
            "path": str(path.resolve()),
            "sha256": sha256_file(path),
            "quality": quality,
            "quantum_target_present": False,
        },
        "configuration": asdict(config),
        "feature_sets": {
            "persistence": "current target only, no fit",
            "autoregressive": "window of the target's own history only",
            "wdm_only": "aligned non-target WDM signals only",
            "combined": "target history plus aligned non-target WDM signals",
            "permuted_wdm": (
                "target history plus whole WDM windows permuted within device and split"
            ),
        },
        "causal_alignment": {
            "join": "backward merge_asof only",
            "tolerance_seconds": config.alignment_tolerance_s,
            "sensor_age_columns_included": True,
            "future_observation_allowed": False,
        },
        "models": {
            "tabular": list(TABULAR_MODELS),
            "original_repository_backbones_adapted": list(OPTICAL_SEQUENCE_MODELS),
            "adaptation": "fidelity sigmoid replaced by identity; Huber regression",
        },
        "folds": fold_manifests,
        "features": feature_manifests,
        "git": git_state(),
        "evidence_positive_count": int(
            evidence["evidence_for_incremental_wdm_information"].sum()
        ),
        "evidence_test_count": int(len(evidence)),
    }
    return WDMInformationResult(
        report=report,
        predictions=prediction_frame,
        metrics=metrics,
        comparisons=comparisons,
        evidence=evidence,
    )


def _plot_incremental_evidence(result: WDMInformationResult, directory: Path) -> list[Path]:
    import matplotlib

    matplotlib.use("Agg", force=True)
    import matplotlib.pyplot as plt

    generated: list[Path] = []
    for target, group in result.comparisons.groupby("target", sort=True, observed=True):
        ordered = group.sort_values(["horizon_steps", "model", "contrast"], kind="stable")
        labels = [
            f"h={row.horizon_steps} {row.model}\n{row.contrast.replace('_', ' ')}"
            for row in ordered.itertuples()
        ]
        values = ordered["absolute_improvement"].to_numpy(dtype=float)
        lower = values - ordered["ci_low"].to_numpy(dtype=float)
        upper = ordered["ci_high"].to_numpy(dtype=float) - values
        colors = [
            "#1b9e77" if value else "#d95f02"
            for value in ordered["significant_positive_improvement"]
        ]
        figure, axis = plt.subplots(figsize=(max(9, len(labels) * 0.7), 5))
        axis.errorbar(
            np.arange(len(values)), values, yerr=np.vstack([lower, upper]),
            fmt="none", ecolor="#444444", capsize=3,
        )
        axis.scatter(np.arange(len(values)), values, c=colors, zorder=3)
        axis.axhline(0.0, color="black", linewidth=1)
        axis.set_xticks(np.arange(len(labels)), labels, rotation=55, ha="right", fontsize=8)
        axis.set_ylabel("Melhora de MAE (referência - combinado)")
        axis.set_title(f"Informação WDM incremental — {target}")
        axis.grid(axis="y", alpha=0.25)
        figure.tight_layout()
        path = directory / f"{target}_incremental_information.png"
        figure.savefig(path, dpi=180)
        plt.close(figure)
        generated.append(path)
    return generated


def write_wdm_information_artifacts(
    result: WDMInformationResult,
    output_dir: str | Path,
    *,
    include_predictions: bool = True,
    make_plots: bool = True,
) -> dict:
    directory = Path(output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    artifacts: list[Path] = []
    tables = {
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
        artifacts.extend(_plot_incremental_evidence(result, directory))
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
    "FEATURE_SETS",
    "SUPPORTED_MODELS",
    "WDMInformationConfig",
    "WDMInformationResult",
    "WindowedOpticalData",
    "build_device_local_windows",
    "build_evidence_table",
    "compare_incremental_information",
    "compute_information_metrics",
    "evaluate_incremental_fold",
    "run_wdm_information_campaign",
    "write_wdm_information_artifacts",
]
