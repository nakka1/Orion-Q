"""Post-discovery falsification audit for measured QKD Pareto ordering.

The audit does not tune the Pareto-aware model.  It conditions on the frozen
out-of-stratum predictions for a joint within-stratum randomization test, then
refits the frozen conventional and Pareto-aware TriHeads while holding each
coexistence condition out in turn.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from itertools import permutations
import json
from pathlib import Path

import numpy as np
import pandas as pd

from quantum_twin.evaluation.classical_quantum_pareto import (
    _bootstrap_mean_interval,
    _read_qkd_artifacts,
    _verify_manifest,
    _wide_joint_predictions,
)
from quantum_twin.evaluation.optical_campaign import git_state, sha256_file
from quantum_twin.evaluation.qkd_multihead import (
    QKDMultiHeadConfig,
    fit_qkd_multihead_fold,
    qkd_condition_design,
    qkd_prediction_frame,
)


FALSIFICATION_MODELS = ("trihead_qkd", "trihead_pareto_qkd")
POWER_BASELINE = "within_stratum_target_mean"
JOINT_METRICS = ("pareto_jaccard", "dominance_pair_accuracy")


@dataclass(frozen=True)
class QKDParetoFalsificationConfig:
    seeds: tuple[int, ...] = (13, 42, 97)
    hidden_size: int = 10
    epochs: int = 180
    learning_rate: float = 3e-3
    weight_decay: float = 1e-4
    pareto_dominance_weight: float = 0.25
    pareto_margin: float = 0.05
    randomization_replicates: int = 20_000
    randomization_seed: int = 7_319
    bootstrap_replicates: int = 20_000
    bootstrap_seed: int = 9_317
    familywise_alpha: float = 0.05
    verify_input_manifest: bool = True

    def validate(self) -> None:
        if self.randomization_replicates < 99:
            raise ValueError("randomization_replicates must be at least 99")
        if self.bootstrap_replicates < 1:
            raise ValueError("bootstrap_replicates must be positive")
        if not 0.0 < self.familywise_alpha < 1.0:
            raise ValueError("familywise_alpha must be between zero and one")
        self.multihead_config().validate()

    def multihead_config(self) -> QKDMultiHeadConfig:
        return QKDMultiHeadConfig(
            seeds=self.seeds,
            hidden_size=self.hidden_size,
            epochs=self.epochs,
            learning_rate=self.learning_rate,
            weight_decay=self.weight_decay,
            pareto_dominance_weight=self.pareto_dominance_weight,
            pareto_margin=self.pareto_margin,
            bootstrap_replicates=self.bootstrap_replicates,
            familywise_alpha=self.familywise_alpha,
        )


@dataclass
class QKDParetoFalsificationResult:
    report: dict
    randomization_summary: pd.DataFrame
    randomization_null_distribution: pd.DataFrame
    power_holdout_predictions: pd.DataFrame
    power_holdout_metrics: pd.DataFrame
    power_holdout_comparisons: pd.DataFrame
    input_integrity: dict


def joint_order_metrics(
    true_qber: np.ndarray,
    true_skr: np.ndarray,
    predicted_qber: np.ndarray,
    predicted_skr: np.ndarray,
) -> tuple[float, float]:
    """Compute joint Pareto-set Jaccard and true-dominance ordering accuracy."""
    tq = np.asarray(true_qber, dtype=float).reshape(-1)
    ts = np.asarray(true_skr, dtype=float).reshape(-1)
    pq = np.asarray(predicted_qber, dtype=float).reshape(-1)
    ps = np.asarray(predicted_skr, dtype=float).reshape(-1)
    if len({len(tq), len(ts), len(pq), len(ps)}) != 1 or len(tq) < 2:
        raise ValueError("joint metric inputs must contain at least two aligned cells")
    if not all(np.isfinite(values).all() for values in (tq, ts, pq, ps)):
        raise ValueError("joint metric inputs must be finite")

    def front_mask(qber: np.ndarray, skr: np.ndarray) -> np.ndarray:
        mask = np.ones(len(qber), dtype=bool)
        for index in range(len(qber)):
            dominates = (
                (qber <= qber[index])
                & (skr >= skr[index])
                & ((qber < qber[index]) | (skr > skr[index]))
            )
            mask[index] = not bool(dominates.any())
        return mask

    true_front = front_mask(tq, ts)
    predicted_front = front_mask(pq, ps)
    union = int(np.sum(true_front | predicted_front))
    jaccard = float(np.sum(true_front & predicted_front) / union) if union else 1.0

    comparable = 0
    correct = 0
    for left in range(len(tq)):
        for right in range(left + 1, len(tq)):
            left_true = bool(
                tq[left] <= tq[right]
                and ts[left] >= ts[right]
                and (tq[left] < tq[right] or ts[left] > ts[right])
            )
            right_true = bool(
                tq[right] <= tq[left]
                and ts[right] >= ts[left]
                and (tq[right] < tq[left] or ts[right] > ts[left])
            )
            true_direction = 1 if left_true else (-1 if right_true else 0)
            if true_direction == 0:
                continue
            left_predicted = bool(
                pq[left] <= pq[right]
                and ps[left] >= ps[right]
                and (pq[left] < pq[right] or ps[left] > ps[right])
            )
            right_predicted = bool(
                pq[right] <= pq[left]
                and ps[right] >= ps[left]
                and (pq[right] < pq[left] or ps[right] > ps[left])
            )
            predicted_direction = 1 if left_predicted else (-1 if right_predicted else 0)
            comparable += 1
            correct += int(predicted_direction == true_direction)
    dominance_accuracy = float(correct / comparable) if comparable else float("nan")
    return jaccard, dominance_accuracy


def _randomization_audit(
    predictions: pd.DataFrame,
    *,
    config: QKDParetoFalsificationConfig,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    wide = _wide_joint_predictions(predictions)
    distribution_frames: list[pd.DataFrame] = []
    observed_by_model: dict[str, dict[str, float]] = {}

    for model_offset, model in enumerate(FALSIFICATION_MODELS):
        selected = wide.loc[wide["model"].eq(model)].copy()
        if selected.empty:
            raise ValueError(f"falsification input is missing model {model}")
        rng = np.random.default_rng(config.randomization_seed + model_offset * 100_000)
        null_jaccard = np.zeros(config.randomization_replicates, dtype=float)
        null_dominance = np.zeros(config.randomization_replicates, dtype=float)
        jaccard_groups = 0
        dominance_groups = 0
        observed_jaccard: list[float] = []
        observed_dominance: list[float] = []

        for _, stratum_group in selected.groupby("stratum_id", sort=True, observed=True):
            seed_arrays: list[tuple[np.ndarray, np.ndarray]] = []
            tq: np.ndarray | None = None
            ts: np.ndarray | None = None
            condition_order: np.ndarray | None = None
            stratum_observed: list[tuple[float, float]] = []
            for _, group in stratum_group.groupby("seed", sort=True, observed=True):
                group = group.assign(
                    _condition=group["noise_level"].astype(str)
                ).sort_values("_condition", kind="stable")
                if len(group) < 2:
                    continue
                current_order = group["_condition"].to_numpy()
                current_tq = group["y_true_qber_reported"].to_numpy(dtype=float)
                current_ts = group[
                    "y_true_log1p_secure_key_rate_given_available"
                ].to_numpy(dtype=float)
                if tq is None:
                    tq, ts, condition_order = current_tq, current_ts, current_order
                elif (
                    not np.array_equal(condition_order, current_order)
                    or not np.allclose(tq, current_tq)
                    or not np.allclose(ts, current_ts)
                ):
                    raise ValueError("model seeds do not contain identical physical cells")
                pq = group["prediction_qber_reported"].to_numpy(dtype=float)
                ps = group[
                    "prediction_log1p_secure_key_rate_given_available"
                ].to_numpy(dtype=float)
                seed_arrays.append((pq, ps))
                stratum_observed.append(joint_order_metrics(tq, ts, pq, ps))
            if tq is None or ts is None or not seed_arrays:
                continue

            observed_mean = np.nanmean(np.asarray(stratum_observed), axis=0)
            observed_jaccard.append(float(observed_mean[0]))
            if np.isfinite(observed_mean[1]):
                observed_dominance.append(float(observed_mean[1]))

            orders = np.asarray(list(permutations(range(len(tq)))), dtype=np.int64)
            metrics_by_order = []
            for order in orders:
                seed_metrics = np.asarray([
                    joint_order_metrics(tq, ts, pq[order], ps[order])
                    for pq, ps in seed_arrays
                ])
                metrics_by_order.append(np.nanmean(seed_metrics, axis=0))
            permuted_metrics = np.asarray(metrics_by_order)
            draws = rng.integers(0, len(orders), size=config.randomization_replicates)
            null_jaccard += permuted_metrics[draws, 0]
            jaccard_groups += 1
            if np.isfinite(permuted_metrics[:, 1]).all():
                null_dominance += permuted_metrics[draws, 1]
                dominance_groups += 1

        null_jaccard /= jaccard_groups
        null_dominance /= dominance_groups
        observed_by_model[model] = {
            "pareto_jaccard": float(np.mean(observed_jaccard)),
            "dominance_pair_accuracy": float(np.mean(observed_dominance)),
        }
        distribution_frames.append(pd.DataFrame({
            "replicate": np.arange(1, config.randomization_replicates + 1),
            "model": model,
            "pareto_jaccard": null_jaccard,
            "dominance_pair_accuracy": null_dominance,
            "null_operation": (
                "jointly permute predicted QBER/SKR condition labels within seed/stratum"
            ),
        }))

    distribution = pd.concat(distribution_frames, ignore_index=True)
    family_size = len(FALSIFICATION_MODELS) * len(JOINT_METRICS)
    per_test_alpha = config.familywise_alpha / family_size
    records: list[dict] = []
    for model in FALSIFICATION_MODELS:
        null_rows = distribution.loc[distribution["model"].eq(model)]
        for metric in JOINT_METRICS:
            null = null_rows[metric].to_numpy(dtype=float)
            observed = observed_by_model[model][metric]
            exceedances = int(np.sum(null >= observed))
            empirical_p = float((exceedances + 1) / (len(null) + 1))
            critical = float(np.quantile(null, 1.0 - per_test_alpha, method="higher"))
            records.append({
                "model": model,
                "metric": metric,
                "observed": observed,
                "null_mean": float(null.mean()),
                "null_standard_deviation": float(null.std(ddof=1)),
                "observed_minus_null_mean": float(observed - null.mean()),
                "null_quantile_95": float(np.quantile(null, 0.95)),
                "familywise_critical_value": critical,
                "null_exceedances": exceedances,
                "randomization_replicates": int(len(null)),
                "empirical_p_one_sided": empirical_p,
                "bonferroni_adjusted_p": float(min(1.0, empirical_p * family_size)),
                "familywise_alpha": config.familywise_alpha,
                "per_test_alpha": per_test_alpha,
                "hypothesis_family_size": family_size,
                "multiple_comparison_correction": "Bonferroni",
                "familywise_significant": bool(
                    min(1.0, empirical_p * family_size) <= config.familywise_alpha
                ),
            })
    return pd.DataFrame.from_records(records), distribution


def _condition_sort_key(value: str) -> tuple[int, float]:
    text = str(value)
    if text.lower() == "baseline":
        return 0, float("-inf")
    try:
        return 1, float(text)
    except ValueError:
        return 2, float("inf")


def _power_holdout_predictions(
    cells: pd.DataFrame,
    *,
    config: QKDParetoFalsificationConfig,
) -> pd.DataFrame:
    required = {
        "stratum_id", "noise_level", "secure_key_available",
        "log1p_secure_key_rate", "qber_reported", "fiber_length_km",
        "voa_attenuation_db", "external_signal_present",
        "external_signal_power_dbm",
    }
    missing = sorted(required.difference(cells.columns))
    if missing:
        raise ValueError(f"experimental cells are missing power-holdout fields: {missing}")
    X, _ = qkd_condition_design(cells)
    condition_values = cells["noise_level"].astype(str)
    levels = sorted(condition_values.unique(), key=_condition_sort_key)
    frames: list[pd.DataFrame] = []
    multihead_config = config.multihead_config()
    for level in levels:
        test_mask = condition_values.eq(level).to_numpy()
        train_mask = ~test_mask
        train = cells.loc[train_mask].reset_index(drop=True)
        test = cells.loc[test_mask].reset_index(drop=True)
        by_stratum = train.groupby("stratum_id", observed=True)
        availability_mean = by_stratum["secure_key_available"].mean()
        qber_mean = by_stratum["qber_reported"].mean()
        skr_mean = train.loc[train["secure_key_available"].eq(1)].groupby(
            "stratum_id", observed=True
        )["log1p_secure_key_rate"].mean()
        baseline = qkd_prediction_frame(
            test,
            model=POWER_BASELINE,
            seed=-1,
            availability=test["stratum_id"].map(availability_mean).to_numpy(dtype=float),
            log_skr=test["stratum_id"].map(skr_mean).to_numpy(dtype=float),
            qber=test["stratum_id"].map(qber_mean).to_numpy(dtype=float),
        )
        baseline["held_out_condition"] = str(level)
        baseline["validation_scheme"] = "leave-one-coexistence-condition-out"
        baseline["training_dominance_pairs"] = 0
        baseline["pareto_dominance_weight"] = 0.0
        frames.append(baseline)
        for seed in config.seeds:
            for model in FALSIFICATION_MODELS:
                frame = fit_qkd_multihead_fold(
                    train,
                    test,
                    X[train_mask],
                    X[test_mask],
                    model_name=model,
                    seed=seed,
                    config=multihead_config,
                )
                frame["held_out_condition"] = str(level)
                frame["validation_scheme"] = "leave-one-coexistence-condition-out"
                frames.append(frame)
    return pd.concat(frames, ignore_index=True)


def _power_holdout_metrics(predictions: pd.DataFrame) -> pd.DataFrame:
    return predictions.groupby(
        ["held_out_condition", "target", "target_unit", "loss_name", "model", "seed"],
        as_index=False,
        observed=True,
    ).agg(n=("loss", "size"), mean_loss=("loss", "mean"))


def _power_holdout_comparisons(
    predictions: pd.DataFrame,
    *,
    config: QKDParetoFalsificationConfig,
) -> pd.DataFrame:
    keys = [
        "target", "target_unit", "loss_name", "held_out_condition", "stratum_id", "model",
    ]
    averaged = predictions.groupby(keys, as_index=False, observed=True)["loss"].mean()
    pivot = averaged.pivot(
        index=keys[:-1], columns="model", values="loss"
    ).reset_index()
    definitions = (
        (
            "pareto_trihead_over_conventional_trihead",
            "trihead_qkd",
            "trihead_pareto_qkd",
        ),
        (
            "conventional_trihead_over_within_stratum_mean",
            POWER_BASELINE,
            "trihead_qkd",
        ),
        (
            "pareto_trihead_over_within_stratum_mean",
            POWER_BASELINE,
            "trihead_pareto_qkd",
        ),
    )
    tasks = []
    for target_key, group in pivot.groupby(
        ["target", "target_unit", "loss_name"], observed=True
    ):
        for contrast, reference, candidate in definitions:
            paired = group.dropna(subset=[reference, candidate]).copy()
            if not paired.empty:
                tasks.append((target_key, contrast, reference, candidate, paired))
    family_size = max(len(tasks), 1)
    corrected_confidence = 1.0 - config.familywise_alpha / family_size
    records: list[dict] = []
    for offset, (target_key, contrast, reference, candidate, paired) in enumerate(tasks):
        paired["improvement"] = paired[reference] - paired[candidate]
        by_stratum = paired.groupby("stratum_id", observed=True)["improvement"].mean()
        low, high = _bootstrap_mean_interval(
            by_stratum.to_numpy(dtype=float),
            replicates=config.bootstrap_replicates,
            seed=config.bootstrap_seed + offset,
            confidence_level=corrected_confidence,
        )
        records.append({
            "target": target_key[0],
            "target_unit": target_key[1],
            "loss_name": target_key[2],
            "contrast": contrast,
            "reference_model": reference,
            "candidate_model": candidate,
            "held_out_conditions": int(paired["held_out_condition"].nunique()),
            "strata": int(paired["stratum_id"].nunique()),
            "cells": int(len(paired)),
            "reference_loss": float(paired[reference].mean()),
            "candidate_loss": float(paired[candidate].mean()),
            "absolute_improvement": float(paired["improvement"].mean()),
            "bootstrap_ci_low": low,
            "bootstrap_ci_high": high,
            "familywise_alpha": config.familywise_alpha,
            "simultaneous_confidence_level": corrected_confidence,
            "hypothesis_family_size": family_size,
            "multiple_comparison_correction": "Bonferroni",
            "significant_positive_improvement": bool(low > 0.0),
            "significant_negative_improvement": bool(high < 0.0),
        })
    return pd.DataFrame.from_records(records)


def run_qkd_pareto_falsification_campaign(
    qkd_multihead_dir: str | Path,
    *,
    config: QKDParetoFalsificationConfig = QKDParetoFalsificationConfig(),
) -> QKDParetoFalsificationResult:
    config.validate()
    directory = Path(qkd_multihead_dir)
    required_artifacts = ("experimental_cells.csv", "out_of_stratum_predictions.csv")
    checks = (
        _verify_manifest(directory, required_artifacts)
        if config.verify_input_manifest
        else {}
    )
    cells, predictions = _read_qkd_artifacts(directory)
    randomization_summary, randomization_distribution = _randomization_audit(
        predictions, config=config
    )
    power_predictions = _power_holdout_predictions(cells, config=config)
    power_metrics = _power_holdout_metrics(power_predictions)
    power_comparisons = _power_holdout_comparisons(power_predictions, config=config)

    pareto_dominance = randomization_summary.loc[
        randomization_summary["model"].eq("trihead_pareto_qkd")
        & randomization_summary["metric"].eq("dominance_pair_accuracy")
    ].iloc[0]
    conventional_dominance = randomization_summary.loc[
        randomization_summary["model"].eq("trihead_qkd")
        & randomization_summary["metric"].eq("dominance_pair_accuracy")
    ].iloc[0]
    report = {
        "study": "Post-discovery falsification of Pareto-aware QKD condition ordering",
        "protocol_version": "1.0.0",
        "status": (
            "post-discovery falsification/sensitivity analysis; not confirmatory and no "
            "hyperparameter tuning"
        ),
        "claim_scope": (
            "Tests whether frozen out-of-stratum QBER/SKR predictions align with measured "
            "condition ordering beyond joint within-stratum relabeling, and whether the fixed "
            "Pareto loss generalizes to unseen coexistence-condition levels."
        ),
        "randomization_test": {
            "operation": (
                "QBER and SKR prediction vectors are permuted together within every "
                "physical stratum, preserving their joint geometry and breaking condition "
                "labels; repeated model seeds share the same relabeling."
            ),
            "replicates": config.randomization_replicates,
            "models": list(FALSIFICATION_MODELS),
            "metrics": list(JOINT_METRICS),
            "family_size": int(len(randomization_summary)),
            "correction": "Bonferroni",
            "pareto_dominance_observed": float(pareto_dominance["observed"]),
            "pareto_dominance_null_mean": float(pareto_dominance["null_mean"]),
            "pareto_dominance_adjusted_p": float(
                pareto_dominance["bonferroni_adjusted_p"]
            ),
            "pareto_dominance_familywise_significant": bool(
                pareto_dominance["familywise_significant"]
            ),
            "conventional_dominance_adjusted_p": float(
                conventional_dominance["bonferroni_adjusted_p"]
            ),
            "conventional_dominance_familywise_significant": bool(
                conventional_dominance["familywise_significant"]
            ),
        },
        "power_holdout": {
            "validation": (
                "Each baseline/3/7/9/12 dBm condition is excluded from training in turn; "
                "the same fibre/VOA strata remain represented by other conditions."
            ),
            "conditions": sorted(
                power_predictions["held_out_condition"].astype(str).unique(),
                key=_condition_sort_key,
            ),
            "models": [*FALSIFICATION_MODELS, POWER_BASELINE],
            "baseline": (
                "For each held-out cell, predict from the measured mean of the same target "
                "in the other coexistence conditions of that fibre/VOA stratum."
            ),
            "corrected_positive_by_contrast": {
                str(contrast): int(group["significant_positive_improvement"].sum())
                for contrast, group in power_comparisons.groupby("contrast", observed=True)
            },
            "corrected_negative_by_contrast": {
                str(contrast): int(group["significant_negative_improvement"].sum())
                for contrast, group in power_comparisons.groupby("contrast", observed=True)
            },
            "comparison_count": int(len(power_comparisons)),
        },
        "scientific_conclusion": (
            "Both frozen TriHeads order measured dominance pairs above the joint-relabeling "
            "null after correction, but neither recovers the Pareto set above that null. "
            "When a complete coexistence condition is unseen in training, neither network "
            "beats the within-stratum target-mean baseline and both are corrected-worse for "
            "conditional log-SKR. The signal is therefore limited to condition ordering "
            "inside the observed grid; unseen-power transport and temporal forecasting remain "
            "unvalidated."
        ),
        "not_claimed": [
            "The randomization test is post-discovery and is not an independent replication.",
            "Power holdout retains the same fibre/VOA strata and is not device transfer.",
            "The source has no timestamps, measured Bell fidelity or classical BER/OSNR trace.",
            "No loss weight, margin, architecture or decision threshold is selected here.",
        ],
        "configuration": asdict(config),
        "git": git_state(),
    }
    integrity = {
        "input_manifest_verified": config.verify_input_manifest,
        "input_artifacts": checks,
    }
    return QKDParetoFalsificationResult(
        report=report,
        randomization_summary=randomization_summary,
        randomization_null_distribution=randomization_distribution,
        power_holdout_predictions=power_predictions,
        power_holdout_metrics=power_metrics,
        power_holdout_comparisons=power_comparisons,
        input_integrity=integrity,
    )


def write_qkd_pareto_falsification_artifacts(
    result: QKDParetoFalsificationResult,
    output_dir: str | Path,
) -> dict:
    directory = Path(output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    tables = {
        "randomization_summary.csv": result.randomization_summary,
        "randomization_null_distribution.csv": result.randomization_null_distribution,
        "power_holdout_predictions.csv": result.power_holdout_predictions,
        "power_holdout_metrics.csv": result.power_holdout_metrics,
        "power_holdout_comparisons.csv": result.power_holdout_comparisons,
    }
    artifacts: list[Path] = []
    for name, frame in tables.items():
        path = directory / name
        frame.to_csv(path, index=False)
        artifacts.append(path)
    for name, payload in (("report.json", result.report), ("input_integrity.json", result.input_integrity)):
        path = directory / name
        path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False, allow_nan=False) + "\n",
            encoding="utf-8",
        )
        artifacts.append(path)
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
    "FALSIFICATION_MODELS",
    "JOINT_METRICS",
    "QKDParetoFalsificationConfig",
    "QKDParetoFalsificationResult",
    "joint_order_metrics",
    "run_qkd_pareto_falsification_campaign",
    "write_qkd_pareto_falsification_artifacts",
]
