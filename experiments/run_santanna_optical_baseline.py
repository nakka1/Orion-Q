"""Leakage-safe optical benchmark on the public Sant'Anna telemetry.

This experiment validates the optical forecasting layer only. It does not
create or claim measured quantum-fidelity labels.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import platform
import subprocess

import numpy as np
import pandas as pd
import sklearn
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import HistGradientBoostingClassifier, HistGradientBoostingRegressor
from sklearn.impute import SimpleImputer
from sklearn.metrics import average_precision_score, brier_score_loss, mean_absolute_error, mean_squared_error
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from quantum_twin.optical.santanna import SantAnnaOpticalFailureSource


FEATURE_COLUMNS = ["osnr_db", "log10_pre_fec_ber"]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def git_state() -> dict:
    def run(*args):
        result = subprocess.run(args, capture_output=True, text=True, check=False)
        return result.stdout.strip() if result.returncode == 0 else None

    return {
        "commit": run("git", "rev-parse", "HEAD"),
        "dirty": bool(run("git", "status", "--porcelain")),
    }


def add_features(frame: pd.DataFrame, ber_floor: float = 1e-12) -> pd.DataFrame:
    result = frame.copy()
    ber = result["pre_fec_ber"].clip(lower=ber_floor)
    result["log10_pre_fec_ber"] = np.log10(ber)
    return result


def purged_device_split(frame: pd.DataFrame, test_fraction: float = 0.2,
                        purge_steps: int = 20) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    """Chronological split within each device, with a gap before test."""
    if not 0.0 < test_fraction < 1.0:
        raise ValueError("test_fraction must be between 0 and 1")
    train_parts, test_parts, manifest = [], [], {}
    for device, group in frame.groupby("device_id", sort=True):
        group = group.sort_values("timestamp_utc").reset_index(drop=True)
        split = int(len(group) * (1.0 - test_fraction))
        train_end = max(split - purge_steps, 0)
        if train_end == 0 or split >= len(group):
            raise ValueError(f"not enough rows for purged split on device {device!r}")
        train, test = group.iloc[:train_end].copy(), group.iloc[split:].copy()
        train_parts.append(train)
        test_parts.append(test)
        manifest[str(device)] = {
            "train_rows": int(len(train)), "purged_rows": int(split - train_end),
            "test_rows": int(len(test)),
            "train_end_utc": train["timestamp_utc"].max().isoformat(),
            "test_start_utc": test["timestamp_utc"].min().isoformat(),
        }
    return pd.concat(train_parts), pd.concat(test_parts), manifest


def run_benchmark(csv_path: str, target: str = "osnr_db", horizon_steps: int = 1,
                  test_fraction: float = 0.2, purge_steps: int = 20,
                  blank_failure_is_normal: bool = False, seed: int = 42) -> dict:
    source = SantAnnaOpticalFailureSource(
        csv_path, blank_failure_is_normal=blank_failure_is_normal
    )
    canonical = source.read()
    forecast = source.build_optical_forecast_frame(
        canonical, target=target, horizon_steps=horizon_steps
    )
    forecast = add_features(forecast)
    target_column = f"target_{target}"
    if target == "pre_fec_ber":
        forecast[target_column] = np.log10(forecast[target_column].clip(lower=1e-12))

    train, test, split_manifest = purged_device_split(
        forecast, test_fraction=test_fraction, purge_steps=purge_steps
    )
    numeric_pipeline = Pipeline([
        ("imputer", SimpleImputer(strategy="median", add_indicator=True)),
        ("scale", StandardScaler()),
    ])
    preprocessor = ColumnTransformer([("numeric", numeric_pipeline, FEATURE_COLUMNS)])

    is_classifier = target == "wdm_failure"
    estimator = (
        HistGradientBoostingClassifier(random_state=seed)
        if is_classifier else HistGradientBoostingRegressor(random_state=seed)
    )
    model = Pipeline([("preprocess", preprocessor), ("model", estimator)])
    model.fit(train[FEATURE_COLUMNS], train[target_column])

    if is_classifier:
        predictions = model.predict_proba(test[FEATURE_COLUMNS])[:, 1]
        persistence = test["wdm_failure"].fillna(0.0).to_numpy(dtype=float)
        y_true = test[target_column].to_numpy(dtype=float)
        metrics = {
            "model_brier": float(brier_score_loss(y_true, predictions)),
            "persistence_brier": float(brier_score_loss(y_true, persistence)),
            "model_auprc": (
                float(average_precision_score(y_true, predictions))
                if len(np.unique(y_true)) > 1 else None
            ),
            "positive_rate_test": float(np.mean(y_true)),
        }
    else:
        predictions = model.predict(test[FEATURE_COLUMNS])
        persistence_source = "log10_pre_fec_ber" if target == "pre_fec_ber" else target
        persistence = test[persistence_source].to_numpy(dtype=float)
        y_true = test[target_column].to_numpy(dtype=float)
        metrics = {
            "model_mae": float(mean_absolute_error(y_true, predictions)),
            "model_rmse": float(mean_squared_error(y_true, predictions) ** 0.5),
            "persistence_mae": float(mean_absolute_error(y_true, persistence)),
            "persistence_rmse": float(mean_squared_error(y_true, persistence) ** 0.5),
        }

    path = Path(csv_path)
    return {
        "claim_scope": "recorded optical telemetry forecasting; no measured quantum target",
        "dataset": {
            "path": str(path.resolve()), "sha256": sha256_file(path),
            "quality": source.quality_report(canonical),
        },
        "task": {
            "target": target, "horizon_steps": horizon_steps,
            "features": FEATURE_COLUMNS, "target_provenance": "recorded_optical",
        },
        "split": {
            "strategy": "chronological_per_device_with_purge",
            "test_fraction": test_fraction, "purge_steps": purge_steps,
            "devices": split_manifest,
        },
        "metrics": metrics,
        "reproducibility": {
            **git_state(), "seed": seed, "python": platform.python_version(),
            "numpy": np.__version__, "pandas": pd.__version__, "sklearn": sklearn.__version__,
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--csv", required=True)
    parser.add_argument("--target", choices=["osnr_db", "pre_fec_ber", "wdm_failure"],
                        default="osnr_db")
    parser.add_argument("--horizon-steps", type=int, default=1)
    parser.add_argument("--test-fraction", type=float, default=0.2)
    parser.add_argument("--purge-steps", type=int, default=20)
    parser.add_argument("--blank-failure-is-normal", action="store_true")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = run_benchmark(
        args.csv, target=args.target, horizon_steps=args.horizon_steps,
        test_fraction=args.test_fraction, purge_steps=args.purge_steps,
        blank_failure_is_normal=args.blank_failure_is_normal, seed=args.seed,
    )
    rendered = json.dumps(report, indent=2, ensure_ascii=False)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)


if __name__ == "__main__":
    main()
