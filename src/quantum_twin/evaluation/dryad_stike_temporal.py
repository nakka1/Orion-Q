"""Purged temporal baselines for the 168-hour Dryad STIKE series."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.linear_model import Ridge
from sklearn.preprocessing import StandardScaler

from quantum_twin.evaluation.optical_campaign import git_state, sha256_file
from quantum_twin.quantum.dryad_stike import DryadSTIKETelemetrySource


@dataclass(frozen=True)
class DryadSTIKETemporalConfig:
    lookback_hours: int = 24
    horizons_hours: tuple[int, ...] = (1, 6, 24)
    folds: int = 3
    test_hours: int = 12
    minimum_train_samples: int = 48
    ridge_alpha: float = 1.0
    seed: int = 42

    def validate(self) -> None:
        if self.lookback_hours < 2:
            raise ValueError("lookback_hours must be >= 2")
        if not self.horizons_hours or any(value < 1 for value in self.horizons_hours):
            raise ValueError("horizons_hours must contain positive integers")
        if len(set(self.horizons_hours)) != len(self.horizons_hours):
            raise ValueError("horizons_hours must not contain duplicates")
        if self.folds < 1 or self.test_hours < 1:
            raise ValueError("folds and test_hours must be positive")
        if self.minimum_train_samples < 12:
            raise ValueError("minimum_train_samples must be >= 12")
        if self.ridge_alpha < 0.0:
            raise ValueError("ridge_alpha must be non-negative")


@dataclass
class DryadSTIKETemporalResult:
    report: dict
    telemetry: pd.DataFrame
    splits: pd.DataFrame
    predictions: pd.DataFrame
    fold_metrics: pd.DataFrame
    aggregate_metrics: pd.DataFrame
    comparisons: pd.DataFrame


_TARGETS = {
    "qber_measured": "QBER proportion",
    "communication_rate_bps": "bit/s",
}


def _make_windows(
    frame: pd.DataFrame, target: str, other: str, lookback: int, horizon: int
) -> dict[str, np.ndarray]:
    target_values = frame[target].to_numpy(dtype=float)
    other_values = frame[other].to_numpy(dtype=float)
    origins = np.arange(lookback - 1, len(frame) - horizon, dtype=int)
    own = np.asarray([
        target_values[index - lookback + 1:index + 1] for index in origins
    ])
    cross = np.asarray([
        other_values[index - lookback + 1:index + 1] for index in origins
    ])
    return {
        "own": own,
        "cross": cross,
        "target": target_values[origins + horizon],
        "origin": origins,
        "target_index": origins + horizon,
        "persistence": target_values[origins],
    }


def _purged_rolling_splits(
    windows: dict[str, np.ndarray], config: DryadSTIKETemporalConfig
) -> list[tuple[np.ndarray, np.ndarray]]:
    sample_count = len(windows["target"])
    final_start = sample_count - config.test_hours
    starts = [
        final_start - offset * config.test_hours for offset in reversed(range(config.folds))
    ]
    splits: list[tuple[np.ndarray, np.ndarray]] = []
    for start in starts:
        if start < 0:
            continue
        stop = min(start + config.test_hours, sample_count)
        test_indices = np.arange(start, stop, dtype=int)
        first_test_origin = int(windows["origin"][test_indices[0]])
        train_indices = np.flatnonzero(windows["target_index"] < first_test_origin)
        if len(train_indices) < config.minimum_train_samples:
            continue
        splits.append((train_indices, test_indices))
    if len(splits) != config.folds:
        raise ValueError(
            "the requested purged rolling-origin design does not fit the 168-hour series; "
            f"requested {config.folds} folds but constructed {len(splits)}"
        )
    return splits


def _ridge_predict(
    train_x: np.ndarray,
    train_y: np.ndarray,
    test_x: np.ndarray,
    *,
    alpha: float,
) -> np.ndarray:
    scaler = StandardScaler().fit(train_x)
    model = Ridge(alpha=alpha).fit(scaler.transform(train_x), train_y)
    return model.predict(scaler.transform(test_x))


def _metrics(frame: pd.DataFrame, grouping: list[str]) -> pd.DataFrame:
    records: list[dict] = []
    for keys, group in frame.groupby(grouping, sort=True, observed=True):
        keys = keys if isinstance(keys, tuple) else (keys,)
        error = group["prediction"].to_numpy(float) - group["observed"].to_numpy(float)
        record = dict(zip(grouping, keys))
        record.update({
            "samples": int(len(group)),
            "rmse": float(np.sqrt(np.mean(np.square(error)))),
            "mae": float(np.mean(np.abs(error))),
            "bias": float(np.mean(error)),
        })
        records.append(record)
    return pd.DataFrame.from_records(records)


def _comparisons(metrics: pd.DataFrame) -> pd.DataFrame:
    contrast_pairs = (
        ("own_history_ridge", "persistence", "own-history predictability"),
        ("bivariate_history_ridge", "own_history_ridge", "cross-target incrementality"),
        (
            "bivariate_history_ridge", "bivariate_permuted_cross_ridge",
            "ordered cross-target information",
        ),
    )
    records: list[dict] = []
    for (target, horizon), group in metrics.groupby(
        ["target", "horizon_hours"], sort=True, observed=True
    ):
        indexed = group.set_index("model")
        for candidate, reference, estimand in contrast_pairs:
            candidate_rmse = float(indexed.loc[candidate, "rmse"])
            reference_rmse = float(indexed.loc[reference, "rmse"])
            records.append({
                "target": str(target),
                "horizon_hours": int(horizon),
                "candidate_model": candidate,
                "reference_model": reference,
                "estimand": estimand,
                "candidate_rmse": candidate_rmse,
                "reference_rmse": reference_rmse,
                "rmse_delta_candidate_minus_reference": candidate_rmse - reference_rmse,
                "relative_rmse_improvement": (
                    (reference_rmse - candidate_rmse) / reference_rmse
                    if reference_rmse > 0.0 else float("nan")
                ),
                "candidate_wins": bool(candidate_rmse < reference_rmse),
                "inference_status": (
                    "descriptive exploratory contrast; folds share one 168-hour campaign"
                ),
                "supports_wdm_claim": False,
            })
    return pd.DataFrame.from_records(records)


def run_dryad_stike_temporal_campaign(
    dataset_dir: str | Path,
    *,
    config: DryadSTIKETemporalConfig = DryadSTIKETemporalConfig(),
    verify_publisher_hashes: bool = True,
) -> DryadSTIKETemporalResult:
    config.validate()
    source = DryadSTIKETelemetrySource(
        dataset_dir, verify_publisher_hashes=verify_publisher_hashes
    )
    telemetry = source.read()
    validation = source.validate(telemetry)
    if not validation.is_valid:
        raise ValueError(validation.summary())

    prediction_records: list[dict] = []
    split_records: list[dict] = []
    for target_number, target in enumerate(_TARGETS):
        other = next(name for name in _TARGETS if name != target)
        for horizon in config.horizons_hours:
            windows = _make_windows(
                telemetry, target, other, config.lookback_hours, horizon
            )
            splits = _purged_rolling_splits(windows, config)
            for fold, (train_indices, test_indices) in enumerate(splits):
                train_y = windows["target"][train_indices]
                own_train = windows["own"][train_indices]
                own_test = windows["own"][test_indices]
                both_train = np.hstack(
                    [windows["own"][train_indices], windows["cross"][train_indices]]
                )
                both_test = np.hstack(
                    [windows["own"][test_indices], windows["cross"][test_indices]]
                )
                rng = np.random.default_rng(
                    config.seed + 1000 * target_number + 100 * horizon + fold
                )
                permuted_train = np.hstack([
                    own_train,
                    windows["cross"][train_indices][rng.permutation(len(train_indices))],
                ])
                permuted_test = np.hstack([
                    own_test,
                    windows["cross"][test_indices][rng.permutation(len(test_indices))],
                ])
                model_predictions = {
                    "persistence": windows["persistence"][test_indices],
                    "historical_mean": np.full(len(test_indices), train_y.mean()),
                    "own_history_ridge": _ridge_predict(
                        own_train, train_y, own_test, alpha=config.ridge_alpha
                    ),
                    "bivariate_history_ridge": _ridge_predict(
                        both_train, train_y, both_test, alpha=config.ridge_alpha
                    ),
                    "bivariate_permuted_cross_ridge": _ridge_predict(
                        permuted_train, train_y, permuted_test, alpha=config.ridge_alpha
                    ),
                }
                split_records.append({
                    "target": target,
                    "horizon_hours": int(horizon),
                    "fold": int(fold),
                    "train_samples": int(len(train_indices)),
                    "test_samples": int(len(test_indices)),
                    "last_train_origin_index": int(windows["origin"][train_indices[-1]]),
                    "last_train_target_index": int(windows["target_index"][train_indices[-1]]),
                    "first_test_origin_index": int(windows["origin"][test_indices[0]]),
                    "last_test_target_index": int(windows["target_index"][test_indices[-1]]),
                    "purge_valid": bool(
                        windows["target_index"][train_indices[-1]]
                        < windows["origin"][test_indices[0]]
                    ),
                    "scaler_fit_scope": "training rows only",
                })
                for model, predictions in model_predictions.items():
                    for sample, prediction in zip(test_indices, predictions):
                        prediction_records.append({
                            "target": target,
                            "target_unit": _TARGETS[target],
                            "horizon_hours": int(horizon),
                            "fold": int(fold),
                            "model": model,
                            "origin_sample_index": int(windows["origin"][sample]),
                            "target_sample_index": int(windows["target_index"][sample]),
                            "observed": float(windows["target"][sample]),
                            "prediction": float(prediction),
                        })

    splits_frame = pd.DataFrame.from_records(split_records)
    predictions = pd.DataFrame.from_records(prediction_records)
    fold_metrics = _metrics(
        predictions, ["target", "horizon_hours", "fold", "model"]
    )
    aggregate_metrics = _metrics(
        predictions, ["target", "horizon_hours", "model"]
    )
    comparisons = _comparisons(aggregate_metrics)
    report = {
        "study": "Dryad STIKE 168-hour purged temporal baseline",
        "configuration": asdict(config),
        "dataset": {
            "doi": telemetry.attrs["telemetry_provenance"]["doi"],
            "license": telemetry.attrs["telemetry_provenance"]["license"],
            "quality": source.quality_report(telemetry),
            "provenance": telemetry.attrs["telemetry_provenance"],
        },
        "claim_scope": (
            "Whether measured QBER and communication-rate histories in one 168-hour "
            "STIKE prototype run are useful for forecasting their own future values."
        ),
        "scientific_role": (
            "real-quantum temporal-method baseline and sample-size stress test"
        ),
        "central_wdm_thesis_tested": False,
        "reason_wdm_thesis_not_tested": (
            "The release has no temporally paired BER, OSNR, optical power, wavelength, "
            "or other WDM telemetry. The communication rate is not an optical feature."
        ),
        "not_claimed": [
            "The protocol is STIKE/quasi-QSDC, not a conventional QKD campaign.",
            "The published communication rate is not relabeled as secure key rate.",
            "Relative hourly order is not converted to absolute timestamps.",
            "No fidelity is reconstructed from QBER.",
            "No model result is evidence for incremental WDM predictive information.",
            "One 168-hour run does not establish transfer across devices or campaigns.",
        ],
        "validation": {
            "purged_splits_all_valid": bool(splits_frame["purge_valid"].all()),
            "scalers_fit_on_training_rows_only": True,
            "models": sorted(predictions["model"].unique()),
        },
        "git": git_state(),
    }
    return DryadSTIKETemporalResult(
        report=report,
        telemetry=telemetry,
        splits=splits_frame,
        predictions=predictions,
        fold_metrics=fold_metrics,
        aggregate_metrics=aggregate_metrics,
        comparisons=comparisons,
    )


def write_dryad_stike_temporal_artifacts(
    result: DryadSTIKETemporalResult, output_dir: str | Path
) -> dict:
    directory = Path(output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    artifacts: list[Path] = []
    tables = {
        "canonical_telemetry.csv": result.telemetry,
        "purged_splits.csv": result.splits,
        "predictions.csv": result.predictions,
        "fold_metrics.csv": result.fold_metrics,
        "aggregate_metrics.csv": result.aggregate_metrics,
        "model_comparisons.csv": result.comparisons,
    }
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
    manifest_path = directory / "artifact_manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return manifest


__all__ = [
    "DryadSTIKETemporalConfig",
    "DryadSTIKETemporalResult",
    "run_dryad_stike_temporal_campaign",
    "write_dryad_stike_temporal_artifacts",
]
