"""Leakage-safe evaluation utilities for recorded optical telemetry.

The campaign in this module evaluates recorded optical targets only.  It is
deliberately separate from the quantum simulator so that success on OSNR, BER
or WDM-failure forecasting cannot be presented as hardware validation of Bell
fidelity or of a quantum controller.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
from pathlib import Path
import platform
import subprocess
from typing import Sequence

import numpy as np
import pandas as pd
import sklearn
from sklearn.ensemble import HistGradientBoostingClassifier, HistGradientBoostingRegressor
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression, Ridge
from sklearn.metrics import average_precision_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from quantum_twin.optical.santanna import SantAnnaOpticalFailureSource


SUPPORTED_TARGETS = ("osnr_db", "pre_fec_ber", "wdm_failure")
DEFAULT_MODELS = ("ewma", "kalman", "ridge", "hist_gradient_boosting")
REGRESSION_MODELS = {"ridge", "hist_gradient_boosting"}
CLASSIFICATION_MODELS = {"logistic", "hist_gradient_boosting"}


@dataclass(frozen=True)
class CampaignConfig:
    """Configuration recorded in every campaign manifest."""

    targets: tuple[str, ...] = ("osnr_db", "pre_fec_ber")
    horizons: tuple[int, ...] = (1, 3, 10, 30)
    seeds: tuple[int, ...] = (42,)
    lag_steps: tuple[int, ...] = (1, 2, 3, 5, 10, 30)
    rolling_windows: tuple[int, ...] = (3, 5, 10, 30)
    models: tuple[str, ...] = DEFAULT_MODELS
    n_splits: int = 3
    min_train_fraction: float = 0.5
    requested_purge_steps: int = 20
    holdout_test_fraction: float = 0.2
    include_device_holdout: bool = True
    bootstrap_replicates: int = 2_000
    bootstrap_block_length: int = 30
    familywise_alpha: float = 0.05
    ber_floor: float = 1e-12

    def validate(self) -> None:
        unknown_targets = set(self.targets).difference(SUPPORTED_TARGETS)
        if unknown_targets:
            raise ValueError(f"unsupported target(s): {sorted(unknown_targets)}")
        unknown_models = set(self.models).difference(DEFAULT_MODELS + ("logistic",))
        if unknown_models:
            raise ValueError(f"unsupported model(s): {sorted(unknown_models)}")
        if not self.targets or not self.horizons or not self.seeds:
            raise ValueError("targets, horizons and seeds cannot be empty")
        if any(value < 1 for value in self.horizons):
            raise ValueError("all horizons must be >= 1")
        if any(value < 1 for value in self.lag_steps + self.rolling_windows):
            raise ValueError("lag steps and rolling windows must be >= 1")
        if self.n_splits < 1:
            raise ValueError("n_splits must be >= 1")
        if not 0.0 < self.min_train_fraction < 1.0:
            raise ValueError("min_train_fraction must be between 0 and 1")
        if not 0.0 < self.holdout_test_fraction < 1.0:
            raise ValueError("holdout_test_fraction must be between 0 and 1")
        if self.requested_purge_steps < 0:
            raise ValueError("requested_purge_steps must be >= 0")
        if self.bootstrap_replicates < 1 or self.bootstrap_block_length < 1:
            raise ValueError("bootstrap settings must be >= 1")
        if not 0.0 < self.familywise_alpha < 1.0:
            raise ValueError("familywise_alpha must be between 0 and 1")


@dataclass
class OpticalCampaignResult:
    """In-memory campaign result and its tidy analysis tables."""

    report: dict
    predictions: pd.DataFrame
    metrics: pd.DataFrame
    comparisons: pd.DataFrame
    selections: pd.DataFrame
    tcc_table: pd.DataFrame


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def git_state() -> dict:
    def run(*args: str) -> str | None:
        result = subprocess.run(args, capture_output=True, text=True, check=False)
        return result.stdout.strip() if result.returncode == 0 else None

    return {
        "commit": run("git", "rev-parse", "HEAD"),
        "dirty": bool(run("git", "status", "--porcelain")),
    }


def _log10_ber(values: pd.Series, floor: float) -> pd.Series:
    numeric = pd.to_numeric(values, errors="coerce")
    return np.log10(numeric.clip(lower=floor))


def add_causal_temporal_features(
    frame: pd.DataFrame,
    *,
    target: str,
    lag_steps: Sequence[int] = (1, 2, 3, 5, 10, 30),
    rolling_windows: Sequence[int] = (3, 5, 10, 30),
    ber_floor: float = 1e-12,
) -> tuple[pd.DataFrame, list[str]]:
    """Create features using only each device's observations at or before t.

    Rolling statistics intentionally include the current observation, which is
    available when a forecast is issued.  No centered window, backward fill,
    target-derived future value or cross-device aggregation is used.
    """
    if target not in SUPPORTED_TARGETS:
        raise ValueError(f"unsupported target {target!r}")
    required = {"device_id", "timestamp_utc", f"target_{target}", target}
    missing = required.difference(frame.columns)
    if missing:
        raise ValueError(f"forecast frame is missing columns: {sorted(missing)}")

    result = frame.copy().sort_values(
        ["device_id", "timestamp_utc"], kind="stable"
    ).reset_index(drop=True)
    result["_sample_id"] = np.arange(len(result), dtype=np.int64)
    result["log10_pre_fec_ber"] = _log10_ber(result["pre_fec_ber"], ber_floor)
    if target == "pre_fec_ber":
        result["target_value"] = _log10_ber(result[f"target_{target}"], ber_floor)
        result["current_target_value"] = result["log10_pre_fec_ber"]
        result["target_unit"] = "log10(BER)"
    else:
        result["target_value"] = pd.to_numeric(result[f"target_{target}"], errors="coerce")
        result["current_target_value"] = pd.to_numeric(result[target], errors="coerce")
        result["target_unit"] = "probability" if target == "wdm_failure" else "dB"

    signals = ["osnr_db", "log10_pre_fec_ber"]
    if target == "wdm_failure":
        signals.append("wdm_failure")
    feature_columns: list[str] = []
    grouped = result.groupby("device_id", sort=False, observed=True)

    elapsed = grouped["timestamp_utc"].diff().dt.total_seconds()
    result["elapsed_since_previous_s"] = elapsed
    feature_columns.append("elapsed_since_previous_s")

    for signal in signals:
        result[signal] = pd.to_numeric(result[signal], errors="coerce")
        feature_columns.append(signal)
        lag_one = grouped[signal].shift(1)
        delta_name = f"{signal}__delta_1"
        result[delta_name] = result[signal] - lag_one
        feature_columns.append(delta_name)

        for lag in sorted(set(int(value) for value in lag_steps)):
            name = f"{signal}__lag_{lag}"
            result[name] = grouped[signal].shift(lag)
            feature_columns.append(name)

        for window in sorted(set(int(value) for value in rolling_windows)):
            rolling = grouped[signal].rolling(window=window, min_periods=1)
            for statistic, values in (
                ("mean", rolling.mean()),
                ("std", rolling.std(ddof=0)),
                ("min", rolling.min()),
                ("max", rolling.max()),
            ):
                name = f"{signal}__rolling_{window}_{statistic}"
                result[name] = values.reset_index(level=0, drop=True).sort_index()
                feature_columns.append(name)

    return result, feature_columns


def effective_purge_steps(config: CampaignConfig, horizon: int) -> int:
    """Conservative embargo: requested gap or max lag plus horizon."""
    max_lag = max(config.lag_steps, default=0)
    return max(config.requested_purge_steps, max_lag + int(horizon))


def rolling_origin_splits(
    frame: pd.DataFrame,
    *,
    n_splits: int,
    min_train_fraction: float,
    purge_steps: int,
) -> list[dict]:
    """Expanding-window folds built independently within every device."""
    if n_splits < 1:
        raise ValueError("n_splits must be >= 1")
    if not 0.0 < min_train_fraction < 1.0:
        raise ValueError("min_train_fraction must be between 0 and 1")

    devices: dict[str, pd.DataFrame] = {
        str(device): group.sort_values("timestamp_utc", kind="stable")
        for device, group in frame.groupby("device_id", sort=True, observed=True)
    }
    folds: list[dict] = []
    for fold in range(n_splits):
        train_indices: list[int] = []
        test_indices: list[int] = []
        manifest: dict[str, dict] = {}
        for device, group in devices.items():
            n_rows = len(group)
            first_test = int(np.floor(n_rows * min_train_fraction))
            boundaries = np.linspace(first_test, n_rows, n_splits + 1, dtype=int)
            test_start, test_end = int(boundaries[fold]), int(boundaries[fold + 1])
            train_end = test_start - purge_steps
            if train_end < 2 or test_end <= test_start:
                raise ValueError(
                    f"not enough rows for fold {fold} on {device!r}: "
                    f"n={n_rows}, train_end={train_end}, test=[{test_start}, {test_end})"
                )
            train = group.iloc[:train_end]
            test = group.iloc[test_start:test_end]
            if "target_timestamp_utc" in train and (
                train["target_timestamp_utc"].max() >= test["timestamp_utc"].min()
            ):
                raise ValueError("purge is insufficient: a training label reaches the test period")
            train_indices.extend(train.index.tolist())
            test_indices.extend(test.index.tolist())
            manifest[device] = {
                "train_rows": int(len(train)),
                "purged_rows": int(test_start - train_end),
                "test_rows": int(len(test)),
                "train_target_end_utc": train["target_timestamp_utc"].max().isoformat(),
                "test_feature_start_utc": test["timestamp_utc"].min().isoformat(),
                "test_feature_end_utc": test["timestamp_utc"].max().isoformat(),
            }
        folds.append({
            "evaluation": "rolling_origin",
            "fold": f"rolling_{fold + 1}",
            "train_indices": np.asarray(train_indices, dtype=int),
            "test_indices": np.asarray(test_indices, dtype=int),
            "devices": manifest,
        })
    return folds


def device_holdout_splits(
    frame: pd.DataFrame,
    *,
    test_fraction: float,
) -> list[dict]:
    """Leave one device out while also preventing future-time training data."""
    if not 0.0 < test_fraction < 1.0:
        raise ValueError("test_fraction must be between 0 and 1")
    splits: list[dict] = []
    for device, heldout in frame.groupby("device_id", sort=True, observed=True):
        heldout = heldout.sort_values("timestamp_utc", kind="stable")
        test_start = int(np.floor(len(heldout) * (1.0 - test_fraction)))
        test = heldout.iloc[test_start:]
        if test.empty:
            raise ValueError(f"device holdout test is empty for {device!r}")
        cutoff = test["timestamp_utc"].min()
        train = frame.loc[
            (frame["device_id"] != device)
            & (frame["target_timestamp_utc"] < cutoff)
        ]
        if len(train) < 2:
            raise ValueError(f"device holdout train is too small for {device!r}")
        splits.append({
            "evaluation": "device_holdout",
            "fold": f"holdout_{device}",
            "train_indices": train.index.to_numpy(dtype=int),
            "test_indices": test.index.to_numpy(dtype=int),
            "devices": {
                str(device): {
                    "train_devices": sorted(str(value) for value in train["device_id"].unique()),
                    "train_rows": int(len(train)),
                    "test_rows": int(len(test)),
                    "train_target_end_utc": train["target_timestamp_utc"].max().isoformat(),
                    "test_feature_start_utc": cutoff.isoformat(),
                    "test_feature_end_utc": test["timestamp_utc"].max().isoformat(),
                }
            },
        })
    return splits


def ewma_filter(values: pd.Series, devices: pd.Series, alpha: float) -> np.ndarray:
    temporary = pd.DataFrame({"value": values, "device": devices})
    prediction = temporary.groupby("device", sort=False, observed=True)["value"].transform(
        lambda series: series.ewm(alpha=alpha, adjust=False, ignore_na=True).mean()
    )
    return prediction.to_numpy(dtype=float)


def kalman_local_level_filter(
    values: pd.Series,
    devices: pd.Series,
    process_to_measurement_ratio: float,
) -> np.ndarray:
    prediction = np.full(len(values), np.nan, dtype=float)
    temporary = pd.DataFrame({"value": values.to_numpy(), "device": devices.to_numpy()})
    for _, group in temporary.groupby("device", sort=False, observed=True):
        state = np.nan
        variance = 1.0
        for position in group.index:
            observation = float(temporary.at[position, "value"])
            variance += process_to_measurement_ratio
            if np.isfinite(observation):
                if not np.isfinite(state):
                    state = observation
                    variance = 1.0
                else:
                    gain = variance / (variance + 1.0)
                    state = state + gain * (observation - state)
                    variance = (1.0 - gain) * variance
            prediction[position] = state
    return prediction


def primary_loss(y_true: np.ndarray, prediction: np.ndarray, classifier: bool) -> np.ndarray:
    if classifier:
        return (y_true - np.clip(prediction, 0.0, 1.0)) ** 2
    return np.abs(y_true - prediction)


def tune_causal_filter(
    frame: pd.DataFrame,
    train_indices: np.ndarray,
    *,
    method: str,
    classifier: bool,
) -> tuple[np.ndarray, float]:
    if method == "ewma":
        candidates = (0.05, 0.1, 0.2, 0.35, 0.5, 0.75, 1.0)
        generate = lambda value: ewma_filter(  # noqa: E731 - concise grid-search closure
            frame["current_target_value"], frame["device_id"], value
        )
    elif method == "kalman":
        candidates = (1e-4, 1e-3, 1e-2, 0.05, 0.1, 0.5, 1.0)
        generate = lambda value: kalman_local_level_filter(  # noqa: E731
            frame["current_target_value"], frame["device_id"], value
        )
    else:
        raise ValueError(f"unknown filter {method!r}")

    y_train = frame.loc[train_indices, "target_value"].to_numpy(dtype=float)
    best_score, best_parameter, best_prediction = np.inf, None, None
    for parameter in candidates:
        prediction = generate(parameter)
        score = float(np.nanmean(primary_loss(y_train, prediction[train_indices], classifier)))
        if score < best_score:
            best_score, best_parameter, best_prediction = score, parameter, prediction
    if best_prediction is None or best_parameter is None:
        raise RuntimeError(f"could not tune {method}")
    return best_prediction, float(best_parameter)


def build_optical_model_pipeline(model_name: str, classifier: bool, seed: int) -> Pipeline:
    preprocessing = [
        ("imputer", SimpleImputer(strategy="median", add_indicator=True)),
        ("scale", StandardScaler()),
    ]
    if model_name == "ridge" and not classifier:
        estimator = Ridge(alpha=1.0)
    elif model_name == "logistic" and classifier:
        estimator = LogisticRegression(C=1.0, max_iter=2_000, random_state=seed)
    elif model_name == "hist_gradient_boosting" and classifier:
        estimator = HistGradientBoostingClassifier(
            max_iter=150, learning_rate=0.06, l2_regularization=1.0, random_state=seed
        )
    elif model_name == "hist_gradient_boosting" and not classifier:
        estimator = HistGradientBoostingRegressor(
            max_iter=150, learning_rate=0.06, l2_regularization=1.0, random_state=seed
        )
    else:
        raise ValueError(f"model {model_name!r} is incompatible with this task")
    return Pipeline(preprocessing + [("model", estimator)])


def _evaluate_split(
    frame: pd.DataFrame,
    feature_columns: Sequence[str],
    split: dict,
    *,
    target: str,
    horizon: int,
    seed: int,
    models: Sequence[str],
) -> pd.DataFrame:
    train_indices = split["train_indices"]
    test_indices = split["test_indices"]
    classifier = target == "wdm_failure"
    train = frame.loc[train_indices]
    test = frame.loc[test_indices]
    y_train = train["target_value"].to_numpy(dtype=float)
    y_test = test["target_value"].to_numpy(dtype=float)
    if classifier and len(np.unique(y_train)) < 2:
        raise ValueError(f"classification train split {split['fold']!r} has only one class")

    predictions: dict[str, tuple[np.ndarray, str]] = {
        "persistence": (
            test["current_target_value"].to_numpy(dtype=float),
            "no fitted parameter",
        )
    }
    for model_name in models:
        if model_name in {"ewma", "kalman"}:
            prediction, parameter = tune_causal_filter(
                frame, train_indices, method=model_name, classifier=classifier
            )
            parameter_name = "alpha" if model_name == "ewma" else "process_measurement_ratio"
            predictions[model_name] = (
                prediction[test_indices], f"{parameter_name}={parameter:g} (train-only grid)",
            )
            continue

        effective_name = "logistic" if classifier and model_name == "ridge" else model_name
        valid = CLASSIFICATION_MODELS if classifier else REGRESSION_MODELS
        if effective_name not in valid:
            raise ValueError(f"model {model_name!r} is incompatible with target {target!r}")
        pipeline = build_optical_model_pipeline(effective_name, classifier, seed)
        pipeline.fit(train[list(feature_columns)], y_train)
        if classifier:
            prediction = pipeline.predict_proba(test[list(feature_columns)])[:, 1]
        else:
            prediction = pipeline.predict(test[list(feature_columns)])
        predictions[effective_name] = (prediction, "hyperparameters fixed before test")

    records: list[pd.DataFrame] = []
    for model_name, (prediction, fit_note) in predictions.items():
        prediction = np.asarray(prediction, dtype=float)
        if classifier:
            prediction = np.clip(prediction, 0.0, 1.0)
        result = pd.DataFrame({
            "evaluation": split["evaluation"],
            "target": target,
            "target_unit": test["target_unit"].to_numpy(),
            "horizon_steps": int(horizon),
            "seed": int(seed),
            "fold": split["fold"],
            "device_id": test["device_id"].astype(str).to_numpy(),
            "timestamp_utc": test["timestamp_utc"].to_numpy(),
            "target_timestamp_utc": test["target_timestamp_utc"].to_numpy(),
            "model": model_name,
            "fit_note": fit_note,
            "y_true": y_test,
            "prediction": prediction,
            "absolute_error": np.abs(y_test - prediction),
            "squared_error": (y_test - prediction) ** 2,
        })
        result["primary_loss"] = (
            result["squared_error"] if classifier else result["absolute_error"]
        )
        records.append(result)
    return pd.concat(records, ignore_index=True)


def compute_metrics(predictions: pd.DataFrame) -> pd.DataFrame:
    """Return per-fold, per-device metrics plus an explicit pooled row."""
    keys = ["evaluation", "target", "target_unit", "horizon_steps", "seed", "fold", "model"]
    records: list[dict] = []
    for key, group in predictions.groupby(keys, sort=True, observed=True):
        common = dict(zip(keys, key))
        for device, subset in list(group.groupby("device_id", sort=True, observed=True)) + [
            ("__all__", group)
        ]:
            row = {
                **common,
                "device_id": str(device),
                "n": int(len(subset)),
                "mae": float(subset["absolute_error"].mean()),
                "rmse": float(np.sqrt(subset["squared_error"].mean())),
                "brier": None,
                "auprc": None,
                "positive_rate": None,
            }
            if common["target"] == "wdm_failure":
                y_true = subset["y_true"].to_numpy(dtype=float)
                row["brier"] = float(subset["squared_error"].mean())
                row["positive_rate"] = float(y_true.mean())
                if len(np.unique(y_true)) > 1:
                    row["auprc"] = float(
                        average_precision_score(y_true, subset["prediction"].to_numpy())
                    )
            records.append(row)
    return pd.DataFrame.from_records(records)


def paired_moving_block_bootstrap(
    differences: Sequence[np.ndarray],
    *,
    block_length: int,
    replicates: int,
    seed: int,
    alpha: float,
) -> tuple[float, float]:
    """Percentile interval for a paired loss improvement under autocorrelation."""
    groups = [np.asarray(values, dtype=float) for values in differences if len(values)]
    if not groups:
        raise ValueError("at least one non-empty difference series is required")
    rng = np.random.default_rng(seed)
    bootstrap_means = np.empty(replicates, dtype=float)
    for replicate in range(replicates):
        total, count = 0.0, 0
        for values in groups:
            n_values = len(values)
            current_block = min(block_length, n_values)
            blocks_needed = int(np.ceil(n_values / current_block))
            maximum_start = n_values - current_block
            starts = rng.integers(0, maximum_start + 1, size=blocks_needed)
            sampled = np.concatenate([
                values[start:start + current_block] for start in starts
            ])[:n_values]
            total += float(sampled.sum())
            count += n_values
        bootstrap_means[replicate] = total / count
    return (
        float(np.quantile(bootstrap_means, alpha / 2.0)),
        float(np.quantile(bootstrap_means, 1.0 - alpha / 2.0)),
    )


def compare_against_persistence(
    predictions: pd.DataFrame,
    *,
    block_length: int,
    replicates: int,
    seed: int,
    familywise_alpha: float,
) -> pd.DataFrame:
    """Paired model comparisons with Bonferroni family-wise intervals.

    Losses from repeated model seeds are averaged for each physical sample
    before resampling.  This avoids pretending that the same observation is a
    new independent datum merely because a stochastic model was refitted.
    """
    sample_keys = [
        "evaluation", "target", "target_unit", "horizon_steps", "fold",
        "device_id", "timestamp_utc", "target_timestamp_utc",
    ]
    averaged = predictions.groupby(sample_keys + ["model"], observed=True, as_index=False)[
        "primary_loss"
    ].mean()
    pivot = averaged.pivot(index=sample_keys, columns="model", values="primary_loss").reset_index()
    if "persistence" not in pivot:
        raise ValueError("persistence predictions are required for paired comparisons")

    records: list[dict] = []
    group_keys = ["evaluation", "target", "target_unit", "horizon_steps"]
    for key, task in pivot.groupby(group_keys, sort=True, observed=True):
        candidate_models = sorted(set(task.columns).difference(sample_keys + ["persistence"]))
        corrected_alpha = familywise_alpha / max(len(candidate_models), 1)
        for offset, model in enumerate(candidate_models):
            paired = task.dropna(subset=["persistence", model]).copy()
            paired["improvement"] = paired["persistence"] - paired[model]
            series = [
                group.sort_values("timestamp_utc")["improvement"].to_numpy(dtype=float)
                for _, group in paired.groupby(["fold", "device_id"], sort=True, observed=True)
            ]
            low, high = paired_moving_block_bootstrap(
                series,
                block_length=block_length,
                replicates=replicates,
                seed=seed + offset,
                alpha=corrected_alpha,
            )
            baseline_loss = float(paired["persistence"].mean())
            model_loss = float(paired[model].mean())
            improvement = baseline_loss - model_loss
            records.append({
                **dict(zip(group_keys, key)),
                "model": model,
                "primary_metric": "brier" if key[1] == "wdm_failure" else "mae",
                "n_unique_samples": int(len(paired)),
                "model_loss": model_loss,
                "persistence_loss": baseline_loss,
                "absolute_improvement": improvement,
                "relative_improvement_percent": (
                    100.0 * improvement / baseline_loss if baseline_loss != 0.0 else np.nan
                ),
                "ci_low": low,
                "ci_high": high,
                "familywise_alpha": familywise_alpha,
                "per_comparison_alpha": corrected_alpha,
                "multiple_comparison_correction": "Bonferroni",
                "statistically_better_than_persistence": bool(improvement > 0.0 and low > 0.0),
            })
    return pd.DataFrame.from_records(records)


def select_winners(comparisons: pd.DataFrame) -> pd.DataFrame:
    """Select a candidate only when its corrected CI excludes no improvement."""
    keys = ["evaluation", "target", "target_unit", "horizon_steps", "primary_metric"]
    records: list[dict] = []
    for key, group in comparisons.groupby(keys, sort=True, observed=True):
        eligible = group.loc[group["statistically_better_than_persistence"]]
        best_observed = group.sort_values("model_loss", kind="stable").iloc[0]
        if eligible.empty:
            winner = "persistence"
            winner_loss = float(group["persistence_loss"].iloc[0])
            supported = False
            improvement = 0.0
            ci_low = ci_high = np.nan
        else:
            selected = eligible.sort_values("model_loss", kind="stable").iloc[0]
            winner = str(selected["model"])
            winner_loss = float(selected["model_loss"])
            supported = True
            improvement = float(selected["relative_improvement_percent"])
            ci_low, ci_high = float(selected["ci_low"]), float(selected["ci_high"])
        records.append({
            **dict(zip(keys, key)),
            "winner": winner,
            "winner_loss": winner_loss,
            "statistically_supported": supported,
            "relative_improvement_percent": improvement,
            "ci_low": ci_low,
            "ci_high": ci_high,
            "best_observed_candidate": str(best_observed["model"]),
            "best_observed_candidate_loss": float(best_observed["model_loss"]),
            "persistence_loss": float(group["persistence_loss"].iloc[0]),
            "decision_rule": "candidate only if Bonferroni-corrected paired block-bootstrap CI > 0",
        })
    return pd.DataFrame.from_records(records)


def build_tcc_table(selections: pd.DataFrame, comparisons: pd.DataFrame) -> pd.DataFrame:
    rolling = selections.loc[selections["evaluation"] == "rolling_origin"].copy()
    holdout = selections.loc[selections["evaluation"] == "device_holdout", [
        "target", "horizon_steps", "winner", "statistically_supported"
    ]].rename(columns={
        "winner": "device_holdout_winner",
        "statistically_supported": "device_holdout_supported",
    })
    table = rolling.merge(holdout, on=["target", "horizon_steps"], how="left")
    table["claim"] = np.where(
        table["statistically_supported"],
        "supera persistencia em telemetria optica gravada",
        "sem evidencia corrigida de superioridade; manter persistencia",
    )
    columns = [
        "target", "target_unit", "horizon_steps", "primary_metric", "winner",
        "winner_loss", "persistence_loss", "relative_improvement_percent",
        "ci_low", "ci_high", "statistically_supported", "device_holdout_winner",
        "device_holdout_supported", "claim",
    ]
    return table.reindex(columns=columns).sort_values(["target", "horizon_steps"])


def _records(frame: pd.DataFrame) -> list[dict]:
    return json.loads(frame.to_json(orient="records", date_format="iso"))


def run_optical_campaign(
    csv_path: str | Path,
    *,
    config: CampaignConfig | None = None,
    blank_failure_is_normal: bool = False,
) -> OpticalCampaignResult:
    """Run the full recorded-optical campaign without writing files."""
    config = config or CampaignConfig()
    config.validate()
    if "wdm_failure" in config.targets and not blank_failure_is_normal:
        raise ValueError(
            "wdm_failure forecasting requires blank_failure_is_normal=True; "
            "the label-policy assumption must be explicit"
        )

    path = Path(csv_path)
    source = SantAnnaOpticalFailureSource(
        str(path), blank_failure_is_normal=blank_failure_is_normal, ber_censor_floor=config.ber_floor
    )
    canonical = source.read()
    validation = source.validate(canonical)
    if not validation.is_valid:
        raise ValueError(f"canonical Sant'Anna telemetry failed validation: {validation.summary()}")

    prediction_parts: list[pd.DataFrame] = []
    split_manifest: list[dict] = []
    feature_manifests: list[dict] = []
    for target in config.targets:
        for horizon in config.horizons:
            forecast = source.build_optical_forecast_frame(
                canonical, target=target, horizon_steps=horizon
            )
            engineered, feature_columns = add_causal_temporal_features(
                forecast,
                target=target,
                lag_steps=config.lag_steps,
                rolling_windows=config.rolling_windows,
                ber_floor=config.ber_floor,
            )
            engineered = engineered.dropna(
                subset=["target_value", "current_target_value"]
            ).reset_index(drop=True)
            engineered["_sample_id"] = np.arange(len(engineered), dtype=np.int64)
            purge_steps = effective_purge_steps(config, horizon)
            splits = rolling_origin_splits(
                engineered,
                n_splits=config.n_splits,
                min_train_fraction=config.min_train_fraction,
                purge_steps=purge_steps,
            )
            if config.include_device_holdout:
                splits.extend(device_holdout_splits(
                    engineered, test_fraction=config.holdout_test_fraction
                ))
            feature_manifests.append({
                "target": target,
                "horizon_steps": horizon,
                "rows": int(len(engineered)),
                "feature_count": len(feature_columns),
                "feature_columns": feature_columns,
                "effective_purge_steps": purge_steps,
            })
            for split in splits:
                split_manifest.append({
                    "target": target,
                    "horizon_steps": horizon,
                    "evaluation": split["evaluation"],
                    "fold": split["fold"],
                    "devices": split["devices"],
                })
                for seed in config.seeds:
                    prediction_parts.append(_evaluate_split(
                        engineered,
                        feature_columns,
                        split,
                        target=target,
                        horizon=horizon,
                        seed=seed,
                        models=config.models,
                    ))

    predictions = pd.concat(prediction_parts, ignore_index=True)
    metrics = compute_metrics(predictions)
    comparisons = compare_against_persistence(
        predictions,
        block_length=config.bootstrap_block_length,
        replicates=config.bootstrap_replicates,
        seed=config.seeds[0],
        familywise_alpha=config.familywise_alpha,
    )
    selections = select_winners(comparisons)
    tcc_table = build_tcc_table(selections, comparisons)
    report = {
        "claim_scope": (
            "forecasting of recorded optical telemetry only; no measured quantum target, "
            "quantum-hardware validation or physical closed loop"
        ),
        "dataset": {
            "path": str(path.resolve()),
            "sha256": sha256_file(path),
            "quality": source.quality_report(canonical),
            "blank_failure_is_normal": blank_failure_is_normal,
        },
        "configuration": asdict(config),
        "methodology": {
            "feature_causality": "device-local values at or before forecast issue time",
            "preprocessing": "median imputation and scaling fit within each training fold only",
            "validation": "expanding rolling origin with embargo; optional chronological device holdout",
            "uncertainty": "paired moving-block bootstrap over unique physical samples",
            "multiple_comparisons": "Bonferroni family-wise correction per target/horizon/evaluation",
            "automatic_selection": (
                "candidate selected only when corrected CI for loss improvement over persistence is > 0"
            ),
        },
        "features": feature_manifests,
        "splits": split_manifest,
        "selections": _records(selections),
        "tcc_table": _records(tcc_table),
        "reproducibility": {
            **git_state(),
            "python": platform.python_version(),
            "numpy": np.__version__,
            "pandas": pd.__version__,
            "sklearn": sklearn.__version__,
        },
    }
    return OpticalCampaignResult(
        report=report,
        predictions=predictions,
        metrics=metrics,
        comparisons=comparisons,
        selections=selections,
        tcc_table=tcc_table,
    )


def _plot_campaign(result: OpticalCampaignResult, output_dir: Path) -> list[Path]:
    import matplotlib

    # Experiment scripts also run on headless CI/SSH nodes.  Selecting the
    # non-interactive backend before pyplot avoids a hidden Tk dependency.
    matplotlib.use("Agg", force=True)
    import matplotlib.pyplot as plt

    generated: list[Path] = []
    rolling = result.comparisons.loc[result.comparisons["evaluation"] == "rolling_origin"]
    for target, group in rolling.groupby("target", sort=True, observed=True):
        pivot = group.pivot(index="horizon_steps", columns="model", values="model_loss")
        persistence = group.groupby("horizon_steps")["persistence_loss"].first()
        pivot.insert(0, "persistence", persistence)
        axis = pivot.plot(marker="o", figsize=(8.0, 4.8))
        metric = str(group["primary_metric"].iloc[0]).upper()
        axis.set_title(f"Sant'Anna: {metric} por horizonte — {target}")
        axis.set_xlabel("Horizonte (passos de telemetria)")
        axis.set_ylabel(metric)
        axis.grid(alpha=0.25)
        axis.legend(title="Modelo", fontsize=8)
        figure = axis.get_figure()
        figure.tight_layout()
        path = output_dir / f"{target}_loss_by_horizon.png"
        figure.savefig(path, dpi=180)
        plt.close(figure)
        generated.append(path)
    return generated


def write_campaign_artifacts(
    result: OpticalCampaignResult,
    output_dir: str | Path,
    *,
    include_predictions: bool = True,
    make_plots: bool = True,
) -> dict:
    """Write report, tidy tables, plots and a checksummed artifact manifest."""
    directory = Path(output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    artifacts: list[Path] = []
    tables = {
        "fold_device_metrics.csv": result.metrics,
        "paired_comparisons.csv": result.comparisons,
        "model_selections.csv": result.selections,
        "tcc_results.csv": result.tcc_table,
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
        artifacts.extend(_plot_campaign(result, directory))

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
    "CampaignConfig",
    "OpticalCampaignResult",
    "add_causal_temporal_features",
    "build_tcc_table",
    "compare_against_persistence",
    "compute_metrics",
    "device_holdout_splits",
    "effective_purge_steps",
    "ewma_filter",
    "kalman_local_level_filter",
    "paired_moving_block_bootstrap",
    "primary_loss",
    "rolling_origin_splits",
    "run_optical_campaign",
    "select_winners",
    "build_optical_model_pipeline",
    "tune_causal_filter",
    "write_campaign_artifacts",
]
