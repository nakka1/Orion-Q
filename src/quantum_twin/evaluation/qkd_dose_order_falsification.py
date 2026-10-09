"""Falsify QKD joint-order claims against simple WDM dose baselines.

This campaign reuses the frozen leave-one-stratum-out TriHead predictions and
fits deliberately simple monotonic baselines without using targets from the
held-out fibre/VOA stratum.  Its purpose is to distinguish learned joint QKD
structure from the much simpler rule that more injected optical power tends to
increase QBER and reduce secure-key rate.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.isotonic import IsotonicRegression
from sklearn.linear_model import Ridge
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from quantum_twin.evaluation.classical_quantum_pareto import (
    _bootstrap_mean_interval,
    _read_qkd_artifacts,
    _verify_manifest,
    _wide_joint_predictions,
)
from quantum_twin.evaluation.optical_campaign import git_state, sha256_file
from quantum_twin.evaluation.qkd_pareto_falsification import joint_order_metrics


FIXED_DOSE_ORDER = "fixed_monotonic_power_order"
ISOTONIC_DOSE = "isotonic_power_only"
PATH_ISOTONIC_DOSE = "path_adjusted_isotonic_power"
PATH_MONOTONIC_LINEAR = "path_adjusted_monotonic_linear"
MONOTONIC_BASELINES = (
    FIXED_DOSE_ORDER,
    ISOTONIC_DOSE,
    PATH_ISOTONIC_DOSE,
    PATH_MONOTONIC_LINEAR,
)
AUDITED_EXISTING_MODELS = (
    "independent_linear",
    "trihead_qkd",
    "trihead_permuted_condition",
    "trihead_pareto_qkd",
    "trihead_pareto_permuted_condition",
)
CANDIDATE_MODELS = ("trihead_qkd", "trihead_pareto_qkd")
JOINT_METRICS = ("pareto_jaccard", "dominance_pair_accuracy")


@dataclass(frozen=True)
class QKDDoseOrderFalsificationConfig:
    ridge_alpha: float = 1.0
    bootstrap_replicates: int = 20_000
    bootstrap_seed: int = 14_903
    familywise_alpha: float = 0.05
    verify_input_manifest: bool = True

    def validate(self) -> None:
        if self.ridge_alpha < 0.0:
            raise ValueError("ridge_alpha must be non-negative")
        if self.bootstrap_replicates < 1:
            raise ValueError("bootstrap_replicates must be positive")
        if not 0.0 < self.familywise_alpha < 1.0:
            raise ValueError("familywise_alpha must be between zero and one")


@dataclass
class QKDDoseOrderFalsificationResult:
    report: dict
    audited_predictions: pd.DataFrame
    joint_metrics_by_stratum: pd.DataFrame
    model_summary: pd.DataFrame
    corrected_comparisons: pd.DataFrame
    input_integrity: dict


def optical_power_mw(cells: pd.DataFrame) -> np.ndarray:
    """Represent no injected signal as 0 mW and convert injected dBm to mW."""
    required = {"external_signal_present", "external_signal_power_dbm"}
    missing = sorted(required.difference(cells.columns))
    if missing:
        raise ValueError(f"experimental cells are missing dose fields: {missing}")
    present = cells["external_signal_present"].to_numpy(dtype=int) == 1
    dbm = pd.to_numeric(cells["external_signal_power_dbm"], errors="coerce").to_numpy()
    if np.isnan(dbm[present]).any():
        raise ValueError("injected-signal cells require finite external power")
    dose = np.zeros(len(cells), dtype=float)
    dose[present] = np.power(10.0, dbm[present] / 10.0)
    if not np.isfinite(dose).all():
        raise ValueError("external power conversion produced non-finite dose")
    return dose


def _path_design(cells: pd.DataFrame) -> np.ndarray:
    required = {"fiber_length_km", "voa_attenuation_db"}
    missing = sorted(required.difference(cells.columns))
    if missing:
        raise ValueError(f"experimental cells are missing path fields: {missing}")
    voa = cells["voa_attenuation_db"].to_numpy(dtype=float)
    distance = cells["fiber_length_km"].to_numpy(dtype=float) / 50.0
    return np.column_stack((voa, voa ** 2, distance, voa * distance))


def _ridge_path_predictions(
    X_train: np.ndarray,
    y_train: np.ndarray,
    X_test: np.ndarray,
    *,
    alpha: float,
) -> tuple[np.ndarray, np.ndarray]:
    model = make_pipeline(StandardScaler(), Ridge(alpha=float(alpha)))
    model.fit(X_train, y_train)
    return model.predict(X_train), model.predict(X_test)


def _isotonic_predictions(
    train_dose: np.ndarray,
    train_target: np.ndarray,
    test_dose: np.ndarray,
    *,
    increasing: bool,
) -> np.ndarray:
    if len(np.unique(train_dose)) < 2:
        return np.full(len(test_dose), float(np.mean(train_target)))
    model = IsotonicRegression(increasing=increasing, out_of_bounds="clip")
    model.fit(train_dose, train_target)
    return model.predict(test_dose)


def _monotonic_linear_predictions(
    train_dose: np.ndarray,
    train_target: np.ndarray,
    test_dose: np.ndarray,
    *,
    increasing: bool,
) -> np.ndarray:
    centered = train_dose - float(np.mean(train_dose))
    denominator = float(np.dot(centered, centered))
    raw_slope = (
        float(np.dot(centered, train_target - float(np.mean(train_target))) / denominator)
        if denominator > 0.0
        else 0.0
    )
    slope = max(raw_slope, 0.0) if increasing else min(raw_slope, 0.0)
    intercept = float(np.mean(train_target) - slope * np.mean(train_dose))
    return intercept + slope * test_dose


def build_monotonic_baseline_predictions(
    cells: pd.DataFrame,
    *,
    config: QKDDoseOrderFalsificationConfig = QKDDoseOrderFalsificationConfig(),
) -> pd.DataFrame:
    """Fit each baseline with whole physical strata held out.

    Only positive-key test cells enter the joint QBER/conditional-SKR audit.
    Test targets are copied after prediction solely for evaluation and are
    never passed to a fitted baseline.
    """
    config.validate()
    required = {
        "stratum_id", "noise_level", "secure_key_available",
        "log1p_secure_key_rate", "qber_reported", "fiber_length_km",
        "voa_attenuation_db", "external_signal_present",
        "external_signal_power_dbm",
    }
    missing = sorted(required.difference(cells.columns))
    if missing:
        raise ValueError(f"experimental cells are missing baseline fields: {missing}")
    if cells["stratum_id"].nunique() < 2:
        raise ValueError("at least two physical strata are required")

    dose = optical_power_mw(cells)
    path = _path_design(cells)
    frames: list[pd.DataFrame] = []
    for stratum in sorted(cells["stratum_id"].astype(str).unique()):
        test_mask = cells["stratum_id"].astype(str).eq(stratum).to_numpy()
        train_mask = ~test_mask
        train = cells.loc[train_mask].reset_index(drop=True)
        test = cells.loc[test_mask].reset_index(drop=True)
        train_dose, test_dose = dose[train_mask], dose[test_mask]
        X_train, X_test = path[train_mask], path[test_mask]
        train_positive = train["secure_key_available"].eq(1).to_numpy()
        test_positive = test["secure_key_available"].eq(1).to_numpy()
        if train_positive.sum() < 2:
            raise ValueError(f"fold {stratum} has insufficient positive-key training cells")

        qber_train = train["qber_reported"].to_numpy(dtype=float)
        skr_train = train.loc[train_positive, "log1p_secure_key_rate"].to_numpy(dtype=float)
        qber_path_train, qber_path_test = _ridge_path_predictions(
            X_train, qber_train, X_test, alpha=config.ridge_alpha
        )
        skr_path_train, skr_path_test = _ridge_path_predictions(
            X_train[train_positive], skr_train, X_test,
            alpha=config.ridge_alpha,
        )

        predictions = {
            FIXED_DOSE_ORDER: (test_dose, -test_dose, "ordinal score only"),
            ISOTONIC_DOSE: (
                _isotonic_predictions(
                    train_dose, qber_train, test_dose, increasing=True
                ),
                _isotonic_predictions(
                    train_dose[train_positive], skr_train, test_dose,
                    increasing=False,
                ),
                "publisher QBER and log1p(bit/s)",
            ),
            PATH_ISOTONIC_DOSE: (
                qber_path_test + _isotonic_predictions(
                    train_dose, qber_train - qber_path_train, test_dose,
                    increasing=True,
                ),
                skr_path_test + _isotonic_predictions(
                    train_dose[train_positive], skr_train - skr_path_train,
                    test_dose, increasing=False,
                ),
                "publisher QBER and log1p(bit/s)",
            ),
            PATH_MONOTONIC_LINEAR: (
                qber_path_test + _monotonic_linear_predictions(
                    train_dose, qber_train - qber_path_train, test_dose,
                    increasing=True,
                ),
                skr_path_test + _monotonic_linear_predictions(
                    train_dose[train_positive], skr_train - skr_path_train,
                    test_dose, increasing=False,
                ),
                "publisher QBER and log1p(bit/s)",
            ),
        }
        common = test.loc[test_positive, [
            "stratum_id", "fiber_length_km", "voa_attenuation_db", "noise_level",
        ]].reset_index(drop=True)
        for model, (predicted_qber, predicted_skr, prediction_scale) in predictions.items():
            frame = common.copy()
            frame.insert(0, "seed", -1)
            frame.insert(0, "model", model)
            frame["optical_power_mw"] = test_dose[test_positive]
            frame["y_true_qber_reported"] = test.loc[
                test_positive, "qber_reported"
            ].to_numpy(dtype=float)
            frame["y_true_log1p_secure_key_rate_given_available"] = test.loc[
                test_positive, "log1p_secure_key_rate"
            ].to_numpy(dtype=float)
            frame["prediction_qber_reported"] = np.asarray(
                predicted_qber, dtype=float
            )[test_positive]
            frame["prediction_log1p_secure_key_rate_given_available"] = np.asarray(
                predicted_skr, dtype=float
            )[test_positive]
            frame["prediction_scale"] = prediction_scale
            frame["training_strata"] = int(train["stratum_id"].nunique())
            frame["training_rows_qber"] = int(len(train))
            frame["training_rows_skr"] = int(train_positive.sum())
            frame["test_targets_used_for_fit"] = False
            frame["validation_scheme"] = "leave-one-fibre/VOA-stratum-out"
            frames.append(frame)
    return pd.concat(frames, ignore_index=True)


def _existing_joint_predictions(
    cells: pd.DataFrame,
    predictions: pd.DataFrame,
) -> pd.DataFrame:
    wide = _wide_joint_predictions(predictions)
    wide = wide.loc[wide["model"].isin(AUDITED_EXISTING_MODELS)].copy()
    if set(CANDIDATE_MODELS).difference(wide["model"].unique()):
        missing = sorted(set(CANDIDATE_MODELS).difference(wide["model"].unique()))
        raise ValueError(f"frozen predictions are missing candidate models: {missing}")
    condition_dose = cells[[
        "stratum_id", "noise_level", "external_signal_present",
        "external_signal_power_dbm",
    ]].copy()
    condition_dose["optical_power_mw"] = optical_power_mw(condition_dose)
    condition_dose["stratum_id"] = condition_dose["stratum_id"].astype(str)
    condition_dose["noise_level"] = condition_dose["noise_level"].astype(str)
    if condition_dose.duplicated(["stratum_id", "noise_level"]).any():
        raise ValueError("experimental cells contain duplicate stratum/condition rows")
    wide["stratum_id"] = wide["stratum_id"].astype(str)
    wide["noise_level"] = wide["noise_level"].astype(str)
    wide = wide.merge(
        condition_dose[["stratum_id", "noise_level", "optical_power_mw"]],
        on=["stratum_id", "noise_level"], how="left", validate="many_to_one",
    )
    if wide["optical_power_mw"].isna().any():
        raise ValueError("could not match frozen predictions to coexistence dose")
    wide["prediction_scale"] = "publisher QBER and log1p(bit/s)"
    wide["training_strata"] = int(cells["stratum_id"].nunique() - 1)
    wide["training_rows_qber"] = -1
    wide["training_rows_skr"] = -1
    wide["test_targets_used_for_fit"] = False
    wide["validation_scheme"] = "frozen leave-one-fibre/VOA-stratum-out predictions"
    return wide


def evaluate_joint_predictions(predictions: pd.DataFrame) -> pd.DataFrame:
    required = {
        "model", "seed", "stratum_id", "y_true_qber_reported",
        "y_true_log1p_secure_key_rate_given_available",
        "prediction_qber_reported",
        "prediction_log1p_secure_key_rate_given_available",
    }
    missing = sorted(required.difference(predictions.columns))
    if missing:
        raise ValueError(f"joint predictions are missing fields: {missing}")
    records: list[dict] = []
    for (model, seed, stratum), group in predictions.groupby(
        ["model", "seed", "stratum_id"], sort=True, observed=True
    ):
        if len(group) < 2:
            continue
        jaccard, dominance = joint_order_metrics(
            group["y_true_qber_reported"].to_numpy(dtype=float),
            group["y_true_log1p_secure_key_rate_given_available"].to_numpy(dtype=float),
            group["prediction_qber_reported"].to_numpy(dtype=float),
            group["prediction_log1p_secure_key_rate_given_available"].to_numpy(dtype=float),
        )
        records.append({
            "model": str(model),
            "seed": int(seed),
            "stratum_id": str(stratum),
            "positive_key_conditions": int(len(group)),
            "pareto_jaccard": jaccard,
            "dominance_pair_accuracy": dominance,
        })
    return pd.DataFrame.from_records(records)


def _model_summary(metrics: pd.DataFrame) -> pd.DataFrame:
    return metrics.groupby("model", as_index=False, observed=True).agg(
        seeds=("seed", "nunique"),
        evaluated_strata=("stratum_id", "nunique"),
        seed_stratum_evaluations=("stratum_id", "size"),
        mean_pareto_jaccard=("pareto_jaccard", "mean"),
        mean_dominance_pair_accuracy=("dominance_pair_accuracy", "mean"),
    ).sort_values("mean_dominance_pair_accuracy", ascending=False, kind="stable")


def compare_against_monotonic_baselines(
    metrics: pd.DataFrame,
    *,
    config: QKDDoseOrderFalsificationConfig,
) -> pd.DataFrame:
    by_stratum = metrics.groupby(
        ["model", "stratum_id"], as_index=False, observed=True
    )[list(JOINT_METRICS)].mean()
    records: list[dict] = []
    tasks = [
        (candidate, reference, metric)
        for candidate in CANDIDATE_MODELS
        for reference in MONOTONIC_BASELINES
        for metric in JOINT_METRICS
    ]
    family_size = len(tasks)
    corrected_confidence = 1.0 - config.familywise_alpha / family_size
    for offset, (candidate, reference, metric) in enumerate(tasks):
        selected = by_stratum.loc[
            by_stratum["model"].isin((candidate, reference)),
            ["model", "stratum_id", metric],
        ]
        paired = selected.pivot(index="stratum_id", columns="model", values=metric)
        paired = paired.dropna(subset=[candidate, reference])
        if paired.empty:
            raise ValueError(f"no paired strata for {candidate} versus {reference}")
        differences = (paired[candidate] - paired[reference]).to_numpy(dtype=float)
        low, high = _bootstrap_mean_interval(
            differences,
            replicates=config.bootstrap_replicates,
            seed=config.bootstrap_seed + offset,
            confidence_level=corrected_confidence,
        )
        observed = float(np.mean(differences))
        records.append({
            "metric": metric,
            "contrast": f"{candidate}_over_{reference}",
            "reference_model": reference,
            "candidate_model": candidate,
            "strata": int(len(paired)),
            "reference_mean": float(paired[reference].mean()),
            "candidate_mean": float(paired[candidate].mean()),
            "candidate_minus_reference": observed,
            "bootstrap_ci_low": low,
            "bootstrap_ci_high": high,
            "familywise_alpha": config.familywise_alpha,
            "simultaneous_confidence_level": corrected_confidence,
            "hypothesis_family_size": family_size,
            "multiple_comparison_correction": "Bonferroni",
            "candidate_better_corrected": bool(low > 0.0),
            "reference_better_corrected": bool(high < 0.0),
        })
    return pd.DataFrame.from_records(records)


def run_qkd_dose_order_falsification_campaign(
    qkd_multihead_dir: str | Path,
    *,
    config: QKDDoseOrderFalsificationConfig = QKDDoseOrderFalsificationConfig(),
) -> QKDDoseOrderFalsificationResult:
    config.validate()
    directory = Path(qkd_multihead_dir)
    required_artifacts = ("experimental_cells.csv", "out_of_stratum_predictions.csv")
    checks = (
        _verify_manifest(directory, required_artifacts)
        if config.verify_input_manifest
        else {}
    )
    cells, frozen_predictions = _read_qkd_artifacts(directory)
    baseline_predictions = build_monotonic_baseline_predictions(cells, config=config)
    existing_predictions = _existing_joint_predictions(cells, frozen_predictions)
    audited_predictions = pd.concat(
        [existing_predictions, baseline_predictions], ignore_index=True, sort=False
    )
    joint_metrics = evaluate_joint_predictions(audited_predictions)
    summary = _model_summary(joint_metrics)
    comparisons = compare_against_monotonic_baselines(joint_metrics, config=config)

    primary = comparisons.loc[
        comparisons["candidate_model"].eq("trihead_pareto_qkd")
        & comparisons["metric"].eq("dominance_pair_accuracy")
    ]
    pareto_beats_all = bool(primary["candidate_better_corrected"].all())
    baseline_beats_pareto = bool(primary["reference_better_corrected"].any())
    best_baseline = summary.loc[
        summary["model"].isin(MONOTONIC_BASELINES)
    ].sort_values("mean_dominance_pair_accuracy", ascending=False).iloc[0]
    pareto_summary = summary.loc[summary["model"].eq("trihead_pareto_qkd")].iloc[0]
    if pareto_beats_all:
        conclusion = (
            "The frozen Pareto-aware TriHead exceeds every predeclared monotonic dose "
            "baseline for dominance ordering after familywise correction. This supports "
            "residual within-grid structure beyond a simple power-order rule."
        )
    elif baseline_beats_pareto:
        conclusion = (
            "At least one simple monotonic dose baseline exceeds the frozen Pareto-aware "
            "TriHead after familywise correction. The current within-grid ordering result "
            "cannot be attributed to learned structure beyond the power-dose rule."
        )
    else:
        conclusion = (
            "The frozen Pareto-aware TriHead does not exceed every monotonic dose baseline "
            "after familywise correction. Residual ordering information beyond the simple "
            "power-dose rule is not demonstrated."
        )
    report = {
        "study": "QKD monotonic dose-order falsification",
        "protocol_version": "1.0.0",
        "status": "post-discovery falsification/sensitivity analysis; not confirmatory",
        "question": (
            "Does the frozen TriHead/Pareto ordering exceed rules that know only that more "
            "injected WDM power should increase QBER and reduce conditional SKR?"
        ),
        "validation": (
            "Frozen leave-one-fibre/VOA-stratum-out neural predictions; monotonic models "
            "fit only on the other physical strata."
        ),
        "dose_definition": (
            "0 mW for no injected signal; 10**(dBm/10) mW for injected conditions"
        ),
        "baselines": {
            FIXED_DOSE_ORDER: "target-free adverse ordering by injected optical power",
            ISOTONIC_DOSE: "train-only QBER-increasing and conditional-SKR-decreasing fits",
            PATH_ISOTONIC_DOSE: (
                "train-only Ridge path component plus monotonic isotonic dose residual"
            ),
            PATH_MONOTONIC_LINEAR: (
                "train-only Ridge path component plus sign-constrained linear dose residual"
            ),
        },
        "multiplicity": {
            "correction": "Bonferroni simultaneous bootstrap intervals",
            "family_size": int(len(comparisons)),
            "familywise_alpha": config.familywise_alpha,
        },
        "primary_result": {
            "pareto_dominance_accuracy": float(
                pareto_summary["mean_dominance_pair_accuracy"]
            ),
            "best_monotonic_baseline": str(best_baseline["model"]),
            "best_monotonic_baseline_dominance_accuracy": float(
                best_baseline["mean_dominance_pair_accuracy"]
            ),
            "pareto_beats_all_monotonic_baselines_corrected": pareto_beats_all,
            "any_monotonic_baseline_beats_pareto_corrected": baseline_beats_pareto,
        },
        "scientific_conclusion": conclusion,
        "not_claimed": [
            "The audit is post-discovery and is not an independent replication.",
            "QBER and SKR are not Bell-state fidelity.",
            "The dataset has no timestamped classical WDM telemetry for future prediction.",
            (
                "A monotonic baseline can falsify model-specific novelty but cannot "
                "establish causality."
            ),
            "All conclusions remain limited to the five-condition IrelandQCI grid.",
        ],
        "configuration": asdict(config),
        "git": git_state(),
    }
    integrity = {
        "input_manifest_verified": config.verify_input_manifest,
        "input_artifacts": checks,
        "test_target_rows_used_for_baseline_fit": 0,
        "frozen_neural_predictions_refit": False,
    }
    return QKDDoseOrderFalsificationResult(
        report=report,
        audited_predictions=audited_predictions,
        joint_metrics_by_stratum=joint_metrics,
        model_summary=summary,
        corrected_comparisons=comparisons,
        input_integrity=integrity,
    )


def write_qkd_dose_order_falsification_artifacts(
    result: QKDDoseOrderFalsificationResult,
    output_dir: str | Path,
) -> dict:
    directory = Path(output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    tables = {
        "audited_joint_predictions.csv": result.audited_predictions,
        "joint_metrics_by_stratum.csv": result.joint_metrics_by_stratum,
        "model_summary.csv": result.model_summary,
        "corrected_comparisons.csv": result.corrected_comparisons,
    }
    artifacts: list[Path] = []
    for name, frame in tables.items():
        path = directory / name
        frame.to_csv(path, index=False)
        artifacts.append(path)
    for name, payload in (
        ("report.json", result.report),
        ("input_integrity.json", result.input_integrity),
    ):
        path = directory / name
        path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False, allow_nan=False) + "\n",
            encoding="utf-8",
        )
        artifacts.append(path)
    manifest = {
        "claim_scope": result.report["question"],
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
    "AUDITED_EXISTING_MODELS",
    "CANDIDATE_MODELS",
    "FIXED_DOSE_ORDER",
    "ISOTONIC_DOSE",
    "JOINT_METRICS",
    "MONOTONIC_BASELINES",
    "PATH_ISOTONIC_DOSE",
    "PATH_MONOTONIC_LINEAR",
    "QKDDoseOrderFalsificationConfig",
    "QKDDoseOrderFalsificationResult",
    "build_monotonic_baseline_predictions",
    "compare_against_monotonic_baselines",
    "evaluate_joint_predictions",
    "optical_power_mw",
    "run_qkd_dose_order_falsification_campaign",
    "write_qkd_dose_order_falsification_artifacts",
]
