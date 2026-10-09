"""Exploratory Dual/TriHead transfer on measured QKD coexistence cells."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression, Ridge
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from quantum_twin.evaluation.optical_campaign import git_state, sha256_file
from quantum_twin.evaluation.qkd_coexistence import aggregate_experimental_cells
from quantum_twin.ml.qkd_multihead import (
    QKDDualHead,
    QKDSingleTaskMLP,
    QKDTriHead,
    build_qkd_dominance_pairs,
    predict_qkd_multihead,
    predict_qkd_single_task,
    train_qkd_multihead,
    train_qkd_single_task,
)
from quantum_twin.quantum.qkd_coexistence import QKDCoexistenceSource


QKD_MULTIHEAD_TARGETS = (
    "secure_key_available",
    "log1p_secure_key_rate_given_available",
    "qber_reported",
)


@dataclass(frozen=True)
class QKDMultiHeadConfig:
    seeds: tuple[int, ...] = (13, 42, 97)
    hidden_size: int = 10
    epochs: int = 180
    learning_rate: float = 3e-3
    weight_decay: float = 1e-4
    pareto_dominance_weight: float = 0.25
    pareto_margin: float = 0.05
    bootstrap_replicates: int = 20_000
    familywise_alpha: float = 0.05

    def validate(self) -> None:
        if not self.seeds:
            raise ValueError("seeds cannot be empty")
        if self.hidden_size < 1 or self.epochs < 1:
            raise ValueError("hidden_size and epochs must be >= 1")
        if self.learning_rate <= 0.0 or self.weight_decay < 0.0:
            raise ValueError("learning_rate must be positive and weight_decay non-negative")
        if self.pareto_dominance_weight < 0.0 or self.pareto_margin < 0.0:
            raise ValueError("Pareto dominance weight and margin must be non-negative")
        if self.bootstrap_replicates < 1:
            raise ValueError("bootstrap_replicates must be >= 1")
        if not 0.0 < self.familywise_alpha < 1.0:
            raise ValueError("familywise_alpha must be between zero and one")


@dataclass
class QKDMultiHeadResult:
    report: dict
    cells: pd.DataFrame
    predictions: pd.DataFrame
    metrics: pd.DataFrame
    comparisons: pd.DataFrame


def qkd_condition_design(cells: pd.DataFrame) -> tuple[np.ndarray, list[str]]:
    """Controlled path and coexistence design, with no invented time axis."""
    voa = cells["voa_attenuation_db"].to_numpy(dtype=float)
    distance = cells["fiber_length_km"].to_numpy(dtype=float) / 50.0
    signal = cells["external_signal_present"].to_numpy(dtype=float)
    power = cells["external_signal_power_dbm"].fillna(0.0).to_numpy(dtype=float) / 12.0
    columns = [voa, voa ** 2, distance, voa * distance]
    names = ["voa_db", "voa_db_squared", "distance_50km", "voa_x_distance"]
    condition_columns = [signal, power, signal * distance, power * distance, power * voa]
    condition_names = [
        "signal_present", "signal_power_12dbm", "signal_x_distance",
        "power_x_distance", "power_x_voa",
    ]
    return np.column_stack(columns + condition_columns), names + condition_names


def _permuted_condition_design(
    X: np.ndarray,
    strata: pd.Series,
    *,
    seed: int,
) -> np.ndarray:
    """Preserve path features/marginals but break condition-to-target pairing."""
    result = np.asarray(X, dtype=float).copy()
    values = strata.astype(str).to_numpy()
    rng = np.random.default_rng(int(seed))
    for stratum in sorted(set(values)):
        positions = np.flatnonzero(values == stratum)
        result[positions, 4:] = result[rng.permutation(positions), 4:]
    return result


def _scale_train_test(
    X_train: np.ndarray,
    X_test: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    mean = X_train.mean(axis=0)
    scale = X_train.std(axis=0)
    scale[scale < 1e-8] = 1.0
    return (X_train - mean) / scale, (X_test - mean) / scale


def _target_scale(values: np.ndarray) -> tuple[np.ndarray, float, float]:
    mean = float(np.mean(values))
    scale = float(np.std(values))
    if scale < 1e-8:
        scale = 1.0
    return (values - mean) / scale, mean, scale


def qkd_prediction_frame(
    test_cells: pd.DataFrame,
    *,
    model: str,
    seed: int,
    availability: np.ndarray,
    log_skr: np.ndarray,
    qber: np.ndarray | None,
) -> pd.DataFrame:
    records = []
    common = {
        "model": model,
        "seed": int(seed),
        "stratum_id": test_cells["stratum_id"].astype(str).to_numpy(),
        "fiber_length_km": test_cells["fiber_length_km"].to_numpy(),
        "voa_attenuation_db": test_cells["voa_attenuation_db"].to_numpy(),
        "noise_level": test_cells["noise_level"].astype(str).to_numpy(),
    }
    available_true = test_cells["secure_key_available"].to_numpy(dtype=float)
    probability = np.clip(np.asarray(availability, dtype=float), 1e-7, 1.0 - 1e-7)
    records.append(pd.DataFrame({
        **common,
        "target": "secure_key_available",
        "target_unit": "probability",
        "y_true": available_true,
        "prediction": probability,
        "loss": (available_true - probability) ** 2,
        "loss_name": "Brier",
    }))

    positive = test_cells["secure_key_available"].eq(1).to_numpy()
    positive_common = {
        name: value if np.isscalar(value) else np.asarray(value)[positive]
        for name, value in common.items()
    }
    records.append(pd.DataFrame({
        **positive_common,
        "target": "log1p_secure_key_rate_given_available",
        "target_unit": "log1p(bit/s)",
        "y_true": test_cells.loc[positive, "log1p_secure_key_rate"].to_numpy(dtype=float),
        "prediction": np.asarray(log_skr, dtype=float)[positive],
        "loss_name": "absolute_error",
    }).assign(
        loss=lambda frame: np.abs(frame["y_true"] - frame["prediction"])
    ))
    if qber is not None:
        qber_true = test_cells["qber_reported"].to_numpy(dtype=float)
        qber_prediction = np.asarray(qber, dtype=float)
        records.append(pd.DataFrame({
            **common,
            "target": "qber_reported",
            "target_unit": "publisher-reported QBER scale (ratio or % ambiguous)",
            "y_true": qber_true,
            "prediction": qber_prediction,
            "loss": np.abs(qber_true - qber_prediction),
            "loss_name": "absolute_error",
        }))
    return pd.concat(records, ignore_index=True)


def _fit_independent_baseline(
    train: pd.DataFrame,
    test: pd.DataFrame,
    X_train: np.ndarray,
    X_test: np.ndarray,
    *,
    seed: int,
) -> pd.DataFrame:
    available_train = train["secure_key_available"].to_numpy(dtype=int)
    classifier = make_pipeline(
        StandardScaler(),
        LogisticRegression(C=1.0, max_iter=2_000, random_state=int(seed)),
    )
    classifier.fit(X_train, available_train)
    availability = classifier.predict_proba(X_test)[:, 1]
    positive = available_train == 1
    skr = make_pipeline(StandardScaler(), Ridge(alpha=1.0))
    skr.fit(X_train[positive], train.loc[positive, "log1p_secure_key_rate"])
    qber = make_pipeline(StandardScaler(), Ridge(alpha=1.0))
    qber.fit(X_train, train["qber_reported"])
    return qkd_prediction_frame(
        test,
        model="independent_linear",
        seed=seed,
        availability=availability,
        log_skr=skr.predict(X_test),
        qber=qber.predict(X_test),
    )


def fit_qkd_multihead_fold(
    train: pd.DataFrame,
    test: pd.DataFrame,
    X_train: np.ndarray,
    X_test: np.ndarray,
    *,
    model_name: str,
    seed: int,
    config: QKDMultiHeadConfig,
) -> pd.DataFrame:
    X_train_scaled, X_test_scaled = _scale_train_test(X_train, X_test)
    available = train["secure_key_available"].to_numpy(dtype=float)
    log_skr = train["log1p_secure_key_rate"].to_numpy(dtype=float)
    positive = available == 1
    _, skr_mean, skr_scale = _target_scale(log_skr[positive])
    skr_scaled = (log_skr - skr_mean) / skr_scale
    qber = train["qber_reported"].to_numpy(dtype=float)
    qber_scaled, qber_mean, qber_scale = _target_scale(qber)
    pareto_aware = model_name.startswith("trihead_pareto")
    if model_name.startswith("dualhead"):
        model = QKDDualHead(X_train.shape[1], hidden_size=config.hidden_size)
        qber_for_training = None
    else:
        model = QKDTriHead(X_train.shape[1], hidden_size=config.hidden_size)
        qber_for_training = qber_scaled
    dominance_pairs = None
    if pareto_aware:
        dominance_pairs = build_qkd_dominance_pairs(
            available,
            skr_scaled,
            qber_scaled,
            train["stratum_id"].to_numpy(),
        )
    model, training_summary = train_qkd_multihead(
        model,
        X_train_scaled,
        available,
        skr_scaled,
        qber_scaled=qber_for_training,
        seed=seed,
        epochs=config.epochs,
        learning_rate=config.learning_rate,
        weight_decay=config.weight_decay,
        dominance_pairs=dominance_pairs,
        dominance_weight=(config.pareto_dominance_weight if pareto_aware else 0.0),
        dominance_margin=(config.pareto_margin if pareto_aware else 0.0),
    )
    outputs = predict_qkd_multihead(model, X_test_scaled)
    availability = outputs[0]
    predicted_skr = outputs[1] * skr_scale + skr_mean
    predicted_qber = None if len(outputs) == 2 else outputs[2] * qber_scale + qber_mean
    frame = qkd_prediction_frame(
        test,
        model=model_name,
        seed=seed,
        availability=availability,
        log_skr=predicted_skr,
        qber=predicted_qber,
    )
    frame["training_dominance_pairs"] = training_summary.dominance_pair_count
    frame["pareto_dominance_weight"] = (
        config.pareto_dominance_weight if pareto_aware else 0.0
    )
    return frame


def _fit_independent_mlp(
    train: pd.DataFrame,
    test: pd.DataFrame,
    X_train: np.ndarray,
    X_test: np.ndarray,
    *,
    seed: int,
    config: QKDMultiHeadConfig,
) -> pd.DataFrame:
    """Capacity-matched control: three trunks fitted without shared weights."""
    X_train_scaled, X_test_scaled = _scale_train_test(X_train, X_test)
    available = train["secure_key_available"].to_numpy(dtype=float)
    positive = available == 1
    log_skr = train["log1p_secure_key_rate"].to_numpy(dtype=float)
    skr_scaled, skr_mean, skr_scale = _target_scale(log_skr[positive])
    qber = train["qber_reported"].to_numpy(dtype=float)
    qber_scaled, qber_mean, qber_scale = _target_scale(qber)

    availability_model = train_qkd_single_task(
        QKDSingleTaskMLP(X_train.shape[1], config.hidden_size, probability=True),
        X_train_scaled,
        available,
        seed=seed,
        epochs=config.epochs,
        learning_rate=config.learning_rate,
        weight_decay=config.weight_decay,
    )
    skr_model = train_qkd_single_task(
        QKDSingleTaskMLP(X_train.shape[1], config.hidden_size),
        X_train_scaled[positive],
        skr_scaled,
        seed=seed + 1,
        epochs=config.epochs,
        learning_rate=config.learning_rate,
        weight_decay=config.weight_decay,
    )
    qber_model = train_qkd_single_task(
        QKDSingleTaskMLP(X_train.shape[1], config.hidden_size),
        X_train_scaled,
        qber_scaled,
        seed=seed + 2,
        epochs=config.epochs,
        learning_rate=config.learning_rate,
        weight_decay=config.weight_decay,
    )
    return qkd_prediction_frame(
        test,
        model="independent_mlp",
        seed=seed,
        availability=predict_qkd_single_task(availability_model, X_test_scaled),
        log_skr=(
            predict_qkd_single_task(skr_model, X_test_scaled) * skr_scale + skr_mean
        ),
        qber=(
            predict_qkd_single_task(qber_model, X_test_scaled) * qber_scale + qber_mean
        ),
    )


def _metrics(predictions: pd.DataFrame) -> pd.DataFrame:
    return predictions.groupby(
        ["target", "target_unit", "loss_name", "model", "seed"],
        as_index=False,
        observed=True,
    ).agg(n=("loss", "size"), mean_loss=("loss", "mean"))


def _bootstrap_stratum_means(
    values: pd.DataFrame,
    *,
    replicates: int,
    seed: int,
    alpha: float,
) -> tuple[float, float]:
    means = values.groupby("stratum_id", observed=True)["improvement"].mean().to_numpy()
    rng = np.random.default_rng(int(seed))
    draws = rng.choice(means, size=(int(replicates), len(means)), replace=True).mean(axis=1)
    return float(np.quantile(draws, alpha / 2.0)), float(np.quantile(draws, 1.0 - alpha / 2.0))


def compare_qkd_multihead(
    predictions: pd.DataFrame,
    *,
    config: QKDMultiHeadConfig,
) -> pd.DataFrame:
    sample_keys = [
        "target", "target_unit", "loss_name", "stratum_id",
        "fiber_length_km", "voa_attenuation_db", "noise_level", "model",
    ]
    averaged = predictions.groupby(sample_keys, as_index=False, observed=True)["loss"].mean()
    pivot = averaged.pivot(
        index=sample_keys[:-1], columns="model", values="loss"
    ).reset_index()
    definitions = [
        ("dualhead_over_independent", "independent_linear", "dualhead_qkd"),
        ("trihead_over_independent", "independent_linear", "trihead_qkd"),
        ("dualhead_over_capacity_matched_independent", "independent_mlp", "dualhead_qkd"),
        ("trihead_over_capacity_matched_independent", "independent_mlp", "trihead_qkd"),
        ("trihead_over_dualhead", "dualhead_qkd", "trihead_qkd"),
        ("aligned_trihead_over_permuted_condition", "trihead_permuted_condition", "trihead_qkd"),
        ("pareto_trihead_over_conventional_trihead", "trihead_qkd", "trihead_pareto_qkd"),
        (
            "aligned_pareto_trihead_over_permuted_condition",
            "trihead_pareto_permuted_condition",
            "trihead_pareto_qkd",
        ),
    ]
    tasks = []
    for key, target in pivot.groupby(["target", "target_unit", "loss_name"], observed=True):
        for contrast, reference, candidate in definitions:
            if reference in target and candidate in target and target[[reference, candidate]].notna().any().all():
                tasks.append((key, target, contrast, reference, candidate))
    family_size = max(len(tasks), 1)
    corrected_alpha = config.familywise_alpha / family_size
    records = []
    for offset, (key, task, contrast, reference, candidate) in enumerate(tasks):
        paired = task.dropna(subset=[reference, candidate]).copy()
        paired["improvement"] = paired[reference] - paired[candidate]
        low, high = _bootstrap_stratum_means(
            paired,
            replicates=config.bootstrap_replicates,
            seed=config.seeds[0] + 10_000 + offset,
            alpha=corrected_alpha,
        )
        improvement = float(paired["improvement"].mean())
        records.append({
            "target": key[0],
            "target_unit": key[1],
            "loss_name": key[2],
            "contrast": contrast,
            "reference_model": reference,
            "candidate_model": candidate,
            "cells": int(len(paired)),
            "strata": int(paired["stratum_id"].nunique()),
            "reference_loss": float(paired[reference].mean()),
            "candidate_loss": float(paired[candidate].mean()),
            "absolute_improvement": improvement,
            "ci_low": low,
            "ci_high": high,
            "familywise_alpha": config.familywise_alpha,
            "per_comparison_alpha": corrected_alpha,
            "hypothesis_family_size": family_size,
            "multiple_comparison_correction": "Bonferroni",
            "significant_positive_improvement": bool(improvement > 0.0 and low > 0.0),
        })
    return pd.DataFrame(records)


def run_qkd_multihead_campaign(
    dataset_dir: str | Path,
    *,
    config: QKDMultiHeadConfig = QKDMultiHeadConfig(),
) -> QKDMultiHeadResult:
    config.validate()
    source = QKDCoexistenceSource(dataset_dir)
    raw = source.read()
    cells = aggregate_experimental_cells(raw)
    cells["secure_key_available"] = cells["secure_key_rate_bps"].gt(0.0).astype(np.int8)
    X, feature_names = qkd_condition_design(cells)
    prediction_frames = []
    for fold_number, stratum in enumerate(sorted(cells["stratum_id"].unique()), start=1):
        test_mask = cells["stratum_id"].eq(stratum).to_numpy()
        train_mask = ~test_mask
        train = cells.loc[train_mask].reset_index(drop=True)
        test = cells.loc[test_mask].reset_index(drop=True)
        X_train, X_test = X[train_mask], X[test_mask]
        prediction_frames.append(_fit_independent_baseline(
            train, test, X_train, X_test, seed=config.seeds[0]
        ))
        for seed in config.seeds:
            prediction_frames.append(_fit_independent_mlp(
                train, test, X_train, X_test, seed=seed, config=config,
            ))
            prediction_frames.append(fit_qkd_multihead_fold(
                train, test, X_train, X_test,
                model_name="dualhead_qkd", seed=seed, config=config,
            ))
            prediction_frames.append(fit_qkd_multihead_fold(
                train, test, X_train, X_test,
                model_name="trihead_qkd", seed=seed, config=config,
            ))
            prediction_frames.append(fit_qkd_multihead_fold(
                train, test, X_train, X_test,
                model_name="trihead_pareto_qkd", seed=seed, config=config,
            ))
            permuted_train = _permuted_condition_design(
                X_train, train["stratum_id"], seed=seed + fold_number * 101
            )
            permuted_test = _permuted_condition_design(
                X_test, test["stratum_id"], seed=seed + fold_number * 101 + 1
            )
            prediction_frames.append(fit_qkd_multihead_fold(
                train, test, permuted_train, permuted_test,
                model_name="trihead_permuted_condition", seed=seed, config=config,
            ))
            prediction_frames.append(fit_qkd_multihead_fold(
                train, test, permuted_train, permuted_test,
                model_name="trihead_pareto_permuted_condition", seed=seed, config=config,
            ))
    predictions = pd.concat(prediction_frames, ignore_index=True)
    metrics = _metrics(predictions)
    comparisons = compare_qkd_multihead(predictions, config=config)
    pareto_training_pairs = predictions.loc[
        predictions["model"].eq("trihead_pareto_qkd"),
        ["stratum_id", "seed", "training_dominance_pairs"],
    ].drop_duplicates()
    report = {
        "study": "DualHead/TriHead transfer to measured QKD coexistence outcomes",
        "protocol_version": "2.0.0",
        "status": "exploratory; dataset was inspected before protocol definition",
        "claim_scope": (
            "Tests whether the availability/conditional-quality factorization discovered in "
            "simulation transfers to measured secure-key availability, conditional SKR and QBER, "
            "and whether a train-only dominance penalty improves joint outcome ordering."
        ),
        "not_claimed": [
            "The curated sweeps are not a timestamped forecasting dataset.",
            "QBER is not Bell-state fidelity.",
            "Rows are aggregated to 80 cells before fitting; repeated readings are not independent units.",
            "The publisher's QBER ratio-versus-percent scale remains ambiguous.",
            "The fixed Pareto weight and margin are exploratory and not externally optimized.",
        ],
        "dataset": {
            "quality": source.quality_report(raw),
            "provenance": raw.attrs["telemetry_provenance"],
            "independent_experimental_cells": int(len(cells)),
            "secure_key_unavailable_cells": int(cells["secure_key_available"].eq(0).sum()),
        },
        "configuration": asdict(config),
        "features": feature_names,
        "models": {
            "independent_linear": "Logistic availability + separate Ridge SKR/QBER",
            "independent_mlp": "three capacity-matched MLP trunks fitted separately",
            "dualhead_qkd": "shared MLP; P(SKR>0) + conditional log-SKR",
            "trihead_qkd": "DualHead-QKD plus measured-QBER head",
            "trihead_permuted_condition": "TriHead with coexistence features permuted within stratum",
            "trihead_pareto_qkd": (
                "TriHead plus train-only within-stratum QBER/SKR dominance constraints"
            ),
            "trihead_pareto_permuted_condition": (
                "Pareto-aware TriHead with coexistence features permuted within stratum"
            ),
        },
        "validation": "leave-one-fibre/VOA-stratum-out; all five conditions held out together",
        "pareto_training_constraints": {
            "scope": "positive-key condition pairs within training strata only",
            "minimum_pairs_per_fold": int(
                pareto_training_pairs["training_dominance_pairs"].min()
            ),
            "maximum_pairs_per_fold": int(
                pareto_training_pairs["training_dominance_pairs"].max()
            ),
            "test_target_pairs_used_for_training": 0,
        },
        "multiple_comparison_correction": "Bonferroni over every reported model/target contrast",
        "significant_positive_count": int(comparisons["significant_positive_improvement"].sum()),
        "aligned_condition_evidence_count": int(comparisons.loc[
            comparisons["contrast"].eq("aligned_trihead_over_permuted_condition"),
            "significant_positive_improvement",
        ].sum()),
        "pareto_aligned_condition_evidence_count": int(comparisons.loc[
            comparisons["contrast"].eq(
                "aligned_pareto_trihead_over_permuted_condition"
            ),
            "significant_positive_improvement",
        ].sum()),
        "pareto_over_conventional_evidence_count": int(comparisons.loc[
            comparisons["contrast"].eq("pareto_trihead_over_conventional_trihead"),
            "significant_positive_improvement",
        ].sum()),
        "comparison_count": int(len(comparisons)),
        "git": git_state(),
    }
    return QKDMultiHeadResult(report, cells, predictions, metrics, comparisons)


def write_qkd_multihead_artifacts(
    result: QKDMultiHeadResult,
    output_dir: str | Path,
    *,
    include_predictions: bool = True,
) -> dict:
    directory = Path(output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    artifacts = []
    tables = {
        "experimental_cells.csv": result.cells,
        "model_metrics.csv": result.metrics,
        "corrected_comparisons.csv": result.comparisons,
    }
    if include_predictions:
        tables["out_of_stratum_predictions.csv"] = result.predictions
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
    "QKD_MULTIHEAD_TARGETS",
    "QKDMultiHeadConfig",
    "QKDMultiHeadResult",
    "compare_qkd_multihead",
    "fit_qkd_multihead_fold",
    "qkd_prediction_frame",
    "qkd_condition_design",
    "run_qkd_multihead_campaign",
    "write_qkd_multihead_artifacts",
]
