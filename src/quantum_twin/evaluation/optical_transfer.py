"""Confirmatory cross-campaign validation for Sant'Anna optical telemetry.

Models and hypotheses are frozen from the HardFailure discovery campaign,
fitted only with HardFailure observations, and evaluated once on SoftFailure.
The module never uses the confirmation loss to choose or retune a model.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import platform
from typing import Sequence

import numpy as np
import pandas as pd
import sklearn

from quantum_twin.evaluation.optical_campaign import (
    DEFAULT_MODELS,
    add_causal_temporal_features,
    build_optical_model_pipeline,
    compute_metrics,
    ewma_filter,
    git_state,
    kalman_local_level_filter,
    paired_moving_block_bootstrap,
    sha256_file,
    tune_causal_filter,
)
from quantum_twin.optical.santanna import SantAnnaOpticalFailureSource


@dataclass
class OpticalTransferResult:
    report: dict
    external_predictions: pd.DataFrame
    metrics: pd.DataFrame
    confirmatory_tests: pd.DataFrame
    exploratory_ranking: pd.DataFrame
    domain_shift: pd.DataFrame
    conformal_coverage: pd.DataFrame
    selected_predictions: pd.DataFrame


def load_frozen_protocol(path: str | Path) -> dict:
    protocol_path = Path(path)
    protocol = json.loads(protocol_path.read_text(encoding="utf-8"))
    required = {"protocol_version", "protocol_id", "discovery", "confirmation", "tasks"}
    missing = required.difference(protocol)
    if missing:
        raise ValueError(f"frozen protocol is missing keys: {sorted(missing)}")
    if protocol["protocol_version"] != 1:
        raise ValueError(f"unsupported protocol version {protocol['protocol_version']!r}")
    tasks = protocol["tasks"]
    if not tasks:
        raise ValueError("frozen protocol has no tasks")
    keys = [(task["target"], int(task["horizon_steps"])) for task in tasks]
    if len(keys) != len(set(keys)):
        raise ValueError("frozen protocol contains duplicate target/horizon tasks")
    valid_models = set(DEFAULT_MODELS) | {"persistence"}
    invalid = sorted({task["selected_model"] for task in tasks}.difference(valid_models))
    if invalid:
        raise ValueError(f"frozen protocol contains unsupported models: {invalid}")
    return protocol


def validate_protocol_datasets(
    protocol: dict,
    hard_csv: str | Path,
    soft_csv: str | Path,
) -> dict:
    actual_hard = sha256_file(Path(hard_csv))
    actual_soft = sha256_file(Path(soft_csv))
    expected_hard = str(protocol["discovery"]["dataset_sha256"]).lower()
    expected_soft = str(protocol["confirmation"]["dataset_sha256"]).lower()
    if actual_hard != expected_hard:
        raise ValueError(
            f"HardFailure SHA-256 mismatch: expected {expected_hard}, observed {actual_hard}"
        )
    if actual_soft != expected_soft:
        raise ValueError(
            f"SoftFailure SHA-256 mismatch: expected {expected_soft}, observed {actual_soft}"
        )
    if not protocol["confirmation"].get("must_not_select_or_tune_on_confirmation_data", False):
        raise ValueError("protocol must explicitly prohibit selection/tuning on confirmation data")
    return {"hard_sha256": actual_hard, "soft_sha256": actual_soft}


def hard_train_calibration_split(
    frame: pd.DataFrame,
    *,
    calibration_fraction: float,
    purge_steps: int,
) -> tuple[np.ndarray, np.ndarray, dict]:
    """Chronological HardFailure split with a device-local label embargo."""
    if not 0.0 < calibration_fraction < 1.0:
        raise ValueError("calibration_fraction must be between 0 and 1")
    train_indices: list[int] = []
    calibration_indices: list[int] = []
    manifest: dict[str, dict] = {}
    for device, group in frame.groupby("device_id", sort=True, observed=True):
        group = group.sort_values("timestamp_utc", kind="stable")
        calibration_start = int(np.floor(len(group) * (1.0 - calibration_fraction)))
        train_end = calibration_start - purge_steps
        if train_end < 2 or calibration_start >= len(group):
            raise ValueError(f"not enough HardFailure rows for device {device!r}")
        train = group.iloc[:train_end]
        calibration = group.iloc[calibration_start:]
        if train["target_timestamp_utc"].max() >= calibration["timestamp_utc"].min():
            raise ValueError("HardFailure train labels overlap the calibration period")
        train_indices.extend(train.index.tolist())
        calibration_indices.extend(calibration.index.tolist())
        manifest[str(device)] = {
            "train_rows": int(len(train)),
            "purged_rows": int(calibration_start - train_end),
            "calibration_rows": int(len(calibration)),
            "train_target_end_utc": train["target_timestamp_utc"].max().isoformat(),
            "calibration_feature_start_utc": calibration["timestamp_utc"].min().isoformat(),
            "calibration_feature_end_utc": calibration["timestamp_utc"].max().isoformat(),
        }
    return (
        np.asarray(train_indices, dtype=int),
        np.asarray(calibration_indices, dtype=int),
        manifest,
    )


def external_indices_after_warmup(frame: pd.DataFrame, warmup_steps: int) -> np.ndarray:
    if warmup_steps < 0:
        raise ValueError("warmup_steps must be >= 0")
    position = frame.groupby("device_id", sort=False, observed=True).cumcount()
    indices = frame.index[position >= warmup_steps].to_numpy(dtype=int)
    if not len(indices):
        raise ValueError("confirmation set is empty after device-local warmup")
    return indices


def _prediction_frame(
    frame: pd.DataFrame,
    indices: np.ndarray,
    prediction: np.ndarray,
    *,
    evaluation: str,
    target: str,
    horizon: int,
    seed: int,
    model: str,
    fit_note: str,
) -> pd.DataFrame:
    selected = frame.loc[indices]
    y_true = selected["target_value"].to_numpy(dtype=float)
    prediction = np.asarray(prediction, dtype=float)
    return pd.DataFrame({
        "evaluation": evaluation,
        "target": target,
        "target_unit": selected["target_unit"].to_numpy(),
        "horizon_steps": int(horizon),
        "seed": int(seed),
        "fold": "hard_to_soft" if evaluation == "cross_campaign_soft" else "hard_calibration",
        "device_id": selected["device_id"].astype(str).to_numpy(),
        "timestamp_utc": selected["timestamp_utc"].to_numpy(),
        "target_timestamp_utc": selected["target_timestamp_utc"].to_numpy(),
        "model": model,
        "fit_note": fit_note,
        "y_true": y_true,
        "prediction": prediction,
        "absolute_error": np.abs(y_true - prediction),
        "squared_error": (y_true - prediction) ** 2,
        "primary_loss": np.abs(y_true - prediction),
    })


def _fit_task_models(
    hard: pd.DataFrame,
    soft: pd.DataFrame,
    feature_columns: Sequence[str],
    calibration_train_indices: np.ndarray,
    calibration_indices: np.ndarray,
    final_fit_indices: np.ndarray,
    external_indices: np.ndarray,
    *,
    target: str,
    horizon: int,
    seeds: Sequence[int],
    models: Sequence[str],
) -> tuple[pd.DataFrame, pd.DataFrame]:
    calibration_parts: list[pd.DataFrame] = []
    external_parts: list[pd.DataFrame] = []
    y_calibration_train = hard.loc[
        calibration_train_indices, "target_value"
    ].to_numpy(dtype=float)
    y_final_fit = hard.loc[final_fit_indices, "target_value"].to_numpy(dtype=float)

    for seed in seeds:
        calibration_predictions: dict[str, tuple[np.ndarray, str]] = {
            "persistence": (
                hard.loc[calibration_indices, "current_target_value"].to_numpy(dtype=float),
                "predeclared baseline; no fitted parameter",
            )
        }
        external_predictions: dict[str, tuple[np.ndarray, str]] = {
            "persistence": (
                soft.loc[external_indices, "current_target_value"].to_numpy(dtype=float),
                "predeclared baseline; no fitted parameter",
            )
        }
        for model in models:
            if model in {"ewma", "kalman"}:
                hard_prediction, calibration_parameter = tune_causal_filter(
                    hard, calibration_train_indices, method=model, classifier=False
                )
                _, final_parameter = tune_causal_filter(
                    hard, final_fit_indices, method=model, classifier=False
                )
                if model == "ewma":
                    soft_prediction = ewma_filter(
                        soft["current_target_value"], soft["device_id"], final_parameter
                    )
                    calibration_note = (
                        f"alpha={calibration_parameter:g}; selected on HardFailure prefix only"
                    )
                    external_note = (
                        f"alpha={final_parameter:g}; selected on all HardFailure after protocol freeze"
                    )
                else:
                    soft_prediction = kalman_local_level_filter(
                        soft["current_target_value"], soft["device_id"], final_parameter
                    )
                    calibration_note = (
                        f"process_measurement_ratio={calibration_parameter:g}; "
                        "selected on HardFailure prefix only"
                    )
                    external_note = (
                        f"process_measurement_ratio={final_parameter:g}; "
                        "selected on all HardFailure after protocol freeze"
                    )
                calibration_predictions[model] = (
                    hard_prediction[calibration_indices], calibration_note
                )
                external_predictions[model] = (
                    soft_prediction[external_indices], external_note
                )
                continue

            calibration_pipeline = build_optical_model_pipeline(
                model, classifier=False, seed=seed
            )
            calibration_pipeline.fit(
                hard.loc[calibration_train_indices, list(feature_columns)],
                y_calibration_train,
            )
            calibration_predictions[model] = (
                calibration_pipeline.predict(
                    hard.loc[calibration_indices, list(feature_columns)]
                ),
                "fit and preprocessing use HardFailure prefix only",
            )
            final_pipeline = build_optical_model_pipeline(model, classifier=False, seed=seed)
            final_pipeline.fit(
                hard.loc[final_fit_indices, list(feature_columns)], y_final_fit
            )
            external_predictions[model] = (
                final_pipeline.predict(soft.loc[external_indices, list(feature_columns)]),
                "fit and preprocessing use all HardFailure after protocol freeze; Soft unseen",
            )

        for model, (prediction, note) in calibration_predictions.items():
            calibration_parts.append(_prediction_frame(
                hard,
                calibration_indices,
                prediction,
                evaluation="hard_calibration",
                target=target,
                horizon=horizon,
                seed=seed,
                model=model,
                fit_note=note,
            ))
        for model, (prediction, note) in external_predictions.items():
            external_parts.append(_prediction_frame(
                soft,
                external_indices,
                prediction,
                evaluation="cross_campaign_soft",
                target=target,
                horizon=horizon,
                seed=seed,
                model=model,
                fit_note=note,
            ))
    return pd.concat(calibration_parts, ignore_index=True), pd.concat(external_parts, ignore_index=True)


def _quantile_wasserstein(hard: np.ndarray, soft: np.ndarray) -> float:
    probabilities = np.linspace(0.005, 0.995, 199)
    return float(np.mean(np.abs(np.quantile(hard, probabilities) - np.quantile(soft, probabilities))))


def _population_stability_index(hard: np.ndarray, soft: np.ndarray, bins: int = 10) -> float:
    edges = np.unique(np.quantile(hard, np.linspace(0.0, 1.0, bins + 1)))
    if len(edges) < 3:
        return np.nan
    edges[0], edges[-1] = -np.inf, np.inf
    hard_count = np.histogram(hard, bins=edges)[0].astype(float)
    soft_count = np.histogram(soft, bins=edges)[0].astype(float)
    epsilon = 1e-6
    hard_rate = np.maximum(hard_count / hard_count.sum(), epsilon)
    soft_rate = np.maximum(soft_count / soft_count.sum(), epsilon)
    return float(np.sum((soft_rate - hard_rate) * np.log(soft_rate / hard_rate)))


def domain_shift_diagnostics(
    hard_train: pd.DataFrame,
    soft_external: pd.DataFrame,
    *,
    target: str,
    horizon: int,
    columns: Sequence[str] = ("osnr_db", "log10_pre_fec_ber"),
) -> pd.DataFrame:
    records: list[dict] = []
    for column in columns:
        hard_series = pd.to_numeric(hard_train[column], errors="coerce")
        soft_series = pd.to_numeric(soft_external[column], errors="coerce")
        hard_values = hard_series.dropna().to_numpy(dtype=float)
        soft_values = soft_series.dropna().to_numpy(dtype=float)
        if not len(hard_values) or not len(soft_values):
            continue
        hard_iqr = float(np.subtract(*np.quantile(hard_values, [0.75, 0.25])))
        wasserstein = _quantile_wasserstein(hard_values, soft_values)
        records.append({
            "target": target,
            "horizon_steps": int(horizon),
            "feature": column,
            "hard_train_n": int(len(hard_values)),
            "soft_external_n": int(len(soft_values)),
            "hard_missing_rate": float(hard_series.isna().mean()),
            "soft_missing_rate": float(soft_series.isna().mean()),
            "missing_rate_delta": float(soft_series.isna().mean() - hard_series.isna().mean()),
            "hard_median": float(np.median(hard_values)),
            "soft_median": float(np.median(soft_values)),
            "hard_iqr": hard_iqr,
            "soft_iqr": float(np.subtract(*np.quantile(soft_values, [0.75, 0.25]))),
            "median_shift_in_hard_iqr": (
                float((np.median(soft_values) - np.median(hard_values)) / hard_iqr)
                if hard_iqr > 0.0 else np.nan
            ),
            "quantile_wasserstein": wasserstein,
            "normalized_quantile_wasserstein": (
                float(wasserstein / hard_iqr) if hard_iqr > 0.0 else np.nan
            ),
            "population_stability_index": _population_stability_index(
                hard_values, soft_values
            ),
        })
    return pd.DataFrame.from_records(records)


def confirmatory_transfer_tests(
    external_predictions: pd.DataFrame,
    protocol: dict,
) -> pd.DataFrame:
    inference = protocol["inference_protocol"]
    hypotheses = [task for task in protocol["tasks"] if task["selected_model"] != "persistence"]
    corrected_alpha = float(inference["familywise_alpha"]) / max(len(hypotheses), 1)
    records: list[dict] = []
    sample_keys = ["device_id", "timestamp_utc", "target_timestamp_utc"]

    for offset, task in enumerate(protocol["tasks"]):
        target = task["target"]
        horizon = int(task["horizon_steps"])
        selected_model = task["selected_model"]
        predictions = external_predictions.loc[
            (external_predictions["target"] == target)
            & (external_predictions["horizon_steps"] == horizon)
        ]
        averaged = predictions.groupby(sample_keys + ["model"], as_index=False, observed=True)[
            "primary_loss"
        ].mean()
        pivot = averaged.pivot(index=sample_keys, columns="model", values="primary_loss").reset_index()
        baseline_loss = float(pivot["persistence"].mean())
        if selected_model == "persistence":
            records.append({
                "target": target,
                "target_unit": task["target_unit"],
                "horizon_steps": horizon,
                "predeclared_model": selected_model,
                "n_unique_samples": int(len(pivot)),
                "external_selected_loss": baseline_loss,
                "external_persistence_loss": baseline_loss,
                "absolute_improvement": 0.0,
                "relative_improvement_percent": 0.0,
                "ci_low": np.nan,
                "ci_high": np.nan,
                "familywise_alpha": float(inference["familywise_alpha"]),
                "per_hypothesis_alpha": np.nan,
                "confirmatory_status": "baseline_predeclared_no_superiority_test",
                "transfer_superiority_confirmed": False,
                "discovery_selected_loss": float(task["discovery_selected_loss"]),
                "selected_loss_transfer_gap": (
                    baseline_loss - float(task["discovery_selected_loss"])
                ),
            })
            continue

        paired = pivot.dropna(subset=["persistence", selected_model]).copy()
        paired["improvement"] = paired["persistence"] - paired[selected_model]
        difference_series = [
            group.sort_values("timestamp_utc")["improvement"].to_numpy(dtype=float)
            for _, group in paired.groupby("device_id", sort=True, observed=True)
        ]
        low, high = paired_moving_block_bootstrap(
            difference_series,
            block_length=int(inference["bootstrap_block_length"]),
            replicates=int(inference["bootstrap_replicates"]),
            seed=int(inference["fit_seeds"][0]) + offset,
            alpha=corrected_alpha,
        )
        selected_loss = float(paired[selected_model].mean())
        baseline_loss = float(paired["persistence"].mean())
        improvement = baseline_loss - selected_loss
        confirmed = bool(improvement > 0.0 and low > 0.0)
        records.append({
            "target": target,
            "target_unit": task["target_unit"],
            "horizon_steps": horizon,
            "predeclared_model": selected_model,
            "n_unique_samples": int(len(paired)),
            "external_selected_loss": selected_loss,
            "external_persistence_loss": baseline_loss,
            "absolute_improvement": improvement,
            "relative_improvement_percent": (
                100.0 * improvement / baseline_loss if baseline_loss != 0.0 else np.nan
            ),
            "ci_low": low,
            "ci_high": high,
            "familywise_alpha": float(inference["familywise_alpha"]),
            "per_hypothesis_alpha": corrected_alpha,
            "confirmatory_status": "confirmed" if confirmed else "not_confirmed",
            "transfer_superiority_confirmed": confirmed,
            "discovery_selected_loss": float(task["discovery_selected_loss"]),
            "selected_loss_transfer_gap": (
                selected_loss - float(task["discovery_selected_loss"])
            ),
        })
    return pd.DataFrame.from_records(records)


def summarize_external_models(external_predictions: pd.DataFrame) -> pd.DataFrame:
    """Descriptive SoftFailure ranking; never used to revise the frozen tests."""
    sample_keys = [
        "target", "target_unit", "horizon_steps", "device_id",
        "timestamp_utc", "target_timestamp_utc", "model",
    ]
    averaged = external_predictions.groupby(sample_keys, as_index=False, observed=True)[
        "primary_loss"
    ].mean()
    summary = averaged.groupby(
        ["target", "target_unit", "horizon_steps", "model"],
        as_index=False,
        observed=True,
    ).agg(mean_primary_loss=("primary_loss", "mean"), n_unique_samples=("primary_loss", "size"))
    baseline = summary.loc[summary["model"] == "persistence", [
        "target", "horizon_steps", "mean_primary_loss"
    ]].rename(columns={"mean_primary_loss": "persistence_loss"})
    summary = summary.merge(baseline, on=["target", "horizon_steps"], how="left")
    summary["descriptive_improvement"] = (
        summary["persistence_loss"] - summary["mean_primary_loss"]
    )
    summary["descriptive_improvement_percent"] = np.where(
        summary["persistence_loss"] != 0.0,
        100.0 * summary["descriptive_improvement"] / summary["persistence_loss"],
        np.nan,
    )
    summary["rank_on_soft"] = summary.groupby(
        ["target", "horizon_steps"], observed=True
    )["mean_primary_loss"].rank(method="min").astype(int)
    summary["inference_scope"] = (
        "exploratory_only; SoftFailure ranking cannot revise preregistered model"
    )
    return summary.sort_values(["target", "horizon_steps", "rank_on_soft", "model"])


def _finite_sample_conformal_quantile(residuals: np.ndarray, alpha: float) -> float:
    residuals = np.sort(np.asarray(residuals, dtype=float))
    if not len(residuals):
        raise ValueError("conformal calibration residuals cannot be empty")
    rank = min(int(np.ceil((len(residuals) + 1) * (1.0 - alpha))), len(residuals))
    return float(residuals[rank - 1])


def conformal_transfer_diagnostics(
    calibration_predictions: pd.DataFrame,
    external_predictions: pd.DataFrame,
    protocol: dict,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Evaluate Hard holdout-residual intervals on Soft as a shift diagnostic.

    The final point model is refit on all HardFailure after the residual radius
    is estimated. Together with cross-campaign shift, that means conformal
    exchangeability is not asserted and coverage is not guaranteed.
    """
    alpha = float(protocol["inference_protocol"].get("conformal_alpha", 0.1))
    prediction_records: list[pd.DataFrame] = []
    coverage_records: list[dict] = []
    keys = ["device_id", "timestamp_utc", "target_timestamp_utc", "y_true"]
    for task in protocol["tasks"]:
        target, horizon = task["target"], int(task["horizon_steps"])
        model = task["selected_model"]
        calibration = calibration_predictions.loc[
            (calibration_predictions["target"] == target)
            & (calibration_predictions["horizon_steps"] == horizon)
            & (calibration_predictions["model"] == model)
        ]
        external = external_predictions.loc[
            (external_predictions["target"] == target)
            & (external_predictions["horizon_steps"] == horizon)
            & (external_predictions["model"] == model)
        ]
        calibration_average = calibration.groupby(keys, as_index=False, observed=True)[
            "prediction"
        ].mean()
        residuals = np.abs(
            calibration_average["y_true"].to_numpy()
            - calibration_average["prediction"].to_numpy()
        )
        radius = _finite_sample_conformal_quantile(residuals, alpha)
        external_average = external.groupby(keys, as_index=False, observed=True)[
            "prediction"
        ].mean()
        external_average.insert(0, "target", target)
        external_average.insert(1, "target_unit", task["target_unit"])
        external_average.insert(2, "horizon_steps", horizon)
        external_average.insert(3, "predeclared_model", model)
        external_average["interval_low"] = external_average["prediction"] - radius
        external_average["interval_high"] = external_average["prediction"] + radius
        external_average["covered"] = (
            (external_average["y_true"] >= external_average["interval_low"])
            & (external_average["y_true"] <= external_average["interval_high"])
        )
        external_average["calibration_radius"] = radius
        prediction_records.append(external_average)

        for device, group in list(
            external_average.groupby("device_id", sort=True, observed=True)
        ) + [("__all__", external_average)]:
            coverage_records.append({
                "target": target,
                "target_unit": task["target_unit"],
                "horizon_steps": horizon,
                "predeclared_model": model,
                "device_id": str(device),
                "nominal_coverage": 1.0 - alpha,
                "observed_coverage": float(group["covered"].mean()),
                "coverage_gap": float(group["covered"].mean() - (1.0 - alpha)),
                "calibration_radius": radius,
                "mean_interval_width": 2.0 * radius,
                "n": int(len(group)),
                "interpretation": (
                    "holdout-residual cross-campaign diagnostic; final model refit and "
                    "exchangeability not assumed"
                ),
            })
    return (
        pd.DataFrame.from_records(coverage_records),
        pd.concat(prediction_records, ignore_index=True),
    )


def _records(frame: pd.DataFrame) -> list[dict]:
    return json.loads(frame.to_json(orient="records", date_format="iso"))


def run_santanna_cross_campaign_validation(
    hard_csv: str | Path,
    soft_csv: str | Path,
    protocol_path: str | Path,
) -> OpticalTransferResult:
    protocol = load_frozen_protocol(protocol_path)
    hashes = validate_protocol_datasets(protocol, hard_csv, soft_csv)
    feature_protocol = protocol["feature_protocol"]
    inference_protocol = protocol["inference_protocol"]

    hard_source = SantAnnaOpticalFailureSource(str(hard_csv), campaign_id="HardFailure")
    soft_source = SantAnnaOpticalFailureSource(str(soft_csv), campaign_id="SoftFailure")
    hard_canonical, soft_canonical = hard_source.read(), soft_source.read()
    for label, source, frame in (
        ("HardFailure", hard_source, hard_canonical),
        ("SoftFailure", soft_source, soft_canonical),
    ):
        validation = source.validate(frame)
        if not validation.is_valid:
            raise ValueError(f"{label} canonical telemetry failed validation: {validation.summary()}")

    calibration_parts: list[pd.DataFrame] = []
    external_parts: list[pd.DataFrame] = []
    shift_parts: list[pd.DataFrame] = []
    split_manifest: list[dict] = []
    for task in protocol["tasks"]:
        target, horizon = task["target"], int(task["horizon_steps"])
        if target not in {"osnr_db", "pre_fec_ber"}:
            raise ValueError("confirmatory protocol v1 supports recorded regression targets only")
        hard_forecast = hard_source.build_optical_forecast_frame(
            hard_canonical, target=target, horizon_steps=horizon
        )
        soft_forecast = soft_source.build_optical_forecast_frame(
            soft_canonical, target=target, horizon_steps=horizon
        )
        hard_engineered, hard_features = add_causal_temporal_features(
            hard_forecast,
            target=target,
            lag_steps=tuple(feature_protocol["lag_steps"]),
            rolling_windows=tuple(feature_protocol["rolling_windows"]),
            ber_floor=float(feature_protocol["ber_floor"]),
        )
        soft_engineered, soft_features = add_causal_temporal_features(
            soft_forecast,
            target=target,
            lag_steps=tuple(feature_protocol["lag_steps"]),
            rolling_windows=tuple(feature_protocol["rolling_windows"]),
            ber_floor=float(feature_protocol["ber_floor"]),
        )
        if hard_features != soft_features:
            raise ValueError("discovery and confirmation feature contracts differ")
        hard_engineered = hard_engineered.dropna(
            subset=["target_value", "current_target_value"]
        ).reset_index(drop=True)
        soft_engineered = soft_engineered.dropna(
            subset=["target_value", "current_target_value"]
        ).reset_index(drop=True)
        max_lag = max(feature_protocol["lag_steps"], default=0)
        purge_steps = max(
            int(feature_protocol["requested_purge_steps"]), max_lag + horizon
        )
        calibration_train_indices, calibration_indices, device_split = hard_train_calibration_split(
            hard_engineered,
            calibration_fraction=float(feature_protocol["hard_calibration_fraction"]),
            purge_steps=purge_steps,
        )
        external_indices = external_indices_after_warmup(
            soft_engineered, int(feature_protocol["soft_warmup_steps"])
        )
        final_fit_indices = hard_engineered.index.to_numpy(dtype=int)
        calibration, external = _fit_task_models(
            hard_engineered,
            soft_engineered,
            hard_features,
            calibration_train_indices,
            calibration_indices,
            final_fit_indices,
            external_indices,
            target=target,
            horizon=horizon,
            seeds=tuple(inference_protocol["fit_seeds"]),
            models=DEFAULT_MODELS,
        )
        calibration_parts.append(calibration)
        external_parts.append(external)
        shift_parts.append(domain_shift_diagnostics(
            hard_engineered.loc[final_fit_indices],
            soft_engineered.loc[external_indices],
            target=target,
            horizon=horizon,
        ))
        split_manifest.append({
            "target": target,
            "horizon_steps": horizon,
            "effective_purge_steps": purge_steps,
            "hard_final_fit_rows": int(len(final_fit_indices)),
            "soft_warmup_steps_per_device": int(feature_protocol["soft_warmup_steps"]),
            "hard_devices": device_split,
            "soft_external_rows": int(len(external_indices)),
            "soft_external_start_utc": soft_engineered.loc[
                external_indices, "timestamp_utc"
            ].min().isoformat(),
            "soft_external_end_utc": soft_engineered.loc[
                external_indices, "timestamp_utc"
            ].max().isoformat(),
        })

    calibration_predictions = pd.concat(calibration_parts, ignore_index=True)
    external_predictions = pd.concat(external_parts, ignore_index=True)
    metrics = compute_metrics(external_predictions)
    confirmatory = confirmatory_transfer_tests(external_predictions, protocol)
    exploratory_ranking = summarize_external_models(external_predictions)
    coverage, selected_predictions = conformal_transfer_diagnostics(
        calibration_predictions, external_predictions, protocol
    )
    domain_shift = pd.concat(shift_parts, ignore_index=True)
    protocol_path = Path(protocol_path)
    report = {
        "claim_scope": (
            "confirmatory HardFailure-to-SoftFailure transfer for recorded optical targets; "
            "same testbed, no measured quantum target and no cross-link validation"
        ),
        "protocol": {
            "path": str(protocol_path.resolve()),
            "sha256": sha256_file(protocol_path),
            "frozen_before_confirmation_run": True,
            "content": protocol,
        },
        "datasets": {
            "discovery": {
                "path": str(Path(hard_csv).resolve()),
                "sha256": hashes["hard_sha256"],
                "quality": hard_source.quality_report(hard_canonical),
            },
            "confirmation": {
                "path": str(Path(soft_csv).resolve()),
                "sha256": hashes["soft_sha256"],
                "quality": soft_source.quality_report(soft_canonical),
            },
        },
        "leakage_controls": {
            "model_selection": "predeclared from HardFailure; never changed using SoftFailure",
            "preprocessing_and_fit": "all HardFailure after protocol freeze; SoftFailure unseen",
            "filter_tuning": "all HardFailure only; state reset for SoftFailure",
            "calibration": (
                "held-out HardFailure suffix after a label embargo, using a prefix-fit model; "
                "final point model is then refit on all HardFailure"
            ),
            "confirmation": "SoftFailure used only for final prediction, metrics and shift diagnostics",
            "seed_accounting": "loss averaged per physical observation before inference",
        },
        "inference": {
            "primary_hypotheses": "six predeclared non-persistence tasks",
            "correction": "Bonferroni across the six confirmatory hypotheses",
            "bootstrap": "paired moving blocks within SoftFailure device",
            "conformal_warning": (
                "Hard holdout-residual interval coverage on Soft is diagnostic: the point model "
                "is refit on all Hard and cross-campaign exchangeability is not assumed"
            ),
        },
        "splits": split_manifest,
        "confirmatory_tests": _records(confirmatory),
        "exploratory_soft_winners": _records(
            exploratory_ranking.loc[exploratory_ranking["rank_on_soft"] == 1]
        ),
        "conformal_coverage": _records(coverage.loc[coverage["device_id"] == "__all__"]),
        "reproducibility": {
            **git_state(),
            "python": platform.python_version(),
            "numpy": np.__version__,
            "pandas": pd.__version__,
            "sklearn": sklearn.__version__,
        },
    }
    return OpticalTransferResult(
        report=report,
        external_predictions=external_predictions,
        metrics=metrics,
        confirmatory_tests=confirmatory,
        exploratory_ranking=exploratory_ranking,
        domain_shift=domain_shift,
        conformal_coverage=coverage,
        selected_predictions=selected_predictions,
    )


def _plot_transfer(result: OpticalTransferResult, output_dir: Path) -> list[Path]:
    import matplotlib

    matplotlib.use("Agg", force=True)
    import matplotlib.pyplot as plt

    generated: list[Path] = []
    for target, group in result.confirmatory_tests.groupby("target", sort=True, observed=True):
        group = group.sort_values("horizon_steps")
        horizons = group["horizon_steps"].to_numpy(dtype=int)
        x = np.arange(len(group), dtype=float)
        y = group["absolute_improvement"].to_numpy(dtype=float)
        low = group["ci_low"].to_numpy(dtype=float)
        high = group["ci_high"].to_numpy(dtype=float)
        lower_error = np.where(np.isfinite(low), y - low, 0.0)
        upper_error = np.where(np.isfinite(high), high - y, 0.0)
        figure, axis = plt.subplots(figsize=(8.0, 4.8))
        colors = [
            "#2a9d8f" if status else "#6c757d"
            for status in group["transfer_superiority_confirmed"]
        ]
        axis.bar(x, y, color=colors, width=0.65, alpha=0.9)
        axis.errorbar(x, y, yerr=[lower_error, upper_error], fmt="none", color="black", capsize=4)
        axis.axhline(0.0, color="black", linewidth=1.0)
        axis.set_title(f"Transferência Hard → Soft — {target}")
        axis.set_xlabel("Horizonte (passos de telemetria)")
        axis.set_ylabel("Melhoria absoluta de MAE sobre persistência")
        axis.set_xticks(x, labels=horizons)
        axis.grid(axis="y", alpha=0.25)
        figure.tight_layout()
        path = output_dir / f"{target}_hard_to_soft_improvement.png"
        figure.savefig(path, dpi=180)
        plt.close(figure)
        generated.append(path)
    return generated


def write_transfer_artifacts(
    result: OpticalTransferResult,
    output_dir: str | Path,
    *,
    include_all_predictions: bool = False,
    make_plots: bool = True,
) -> dict:
    directory = Path(output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    tables = {
        "external_fold_device_metrics.csv": result.metrics,
        "confirmatory_transfer_tests.csv": result.confirmatory_tests,
        "exploratory_external_model_ranking.csv": result.exploratory_ranking,
        "domain_shift_diagnostics.csv": result.domain_shift,
        "conformal_transfer_coverage.csv": result.conformal_coverage,
        "selected_external_predictions.csv": result.selected_predictions,
    }
    if include_all_predictions:
        tables["all_external_model_predictions.csv"] = result.external_predictions
    artifacts: list[Path] = []
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
        artifacts.extend(_plot_transfer(result, directory))
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
    "OpticalTransferResult",
    "confirmatory_transfer_tests",
    "conformal_transfer_diagnostics",
    "domain_shift_diagnostics",
    "external_indices_after_warmup",
    "hard_train_calibration_split",
    "load_frozen_protocol",
    "run_santanna_cross_campaign_validation",
    "summarize_external_models",
    "validate_protocol_datasets",
    "write_transfer_artifacts",
]
