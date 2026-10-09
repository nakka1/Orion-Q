"""Multi-objective audit for classical/quantum coexistence trade-offs.

IrelandQCI model predictions are evaluated without refitting.  Gavignet's
published condition means are loaded only after the internal model audit and
are never treated as training rows or independent measurement samples.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from pathlib import Path

import numpy as np
import pandas as pd

from quantum_twin.evaluation.optical_campaign import git_state, sha256_file
from quantum_twin.evaluation.public_qkd_evidence import (
    analyze_published_aggregate_observations,
    load_published_aggregate_observations,
)


GAVIGNET_AGGREGATE_SHA256 = (
    "21a859bd4bd3e24b8413160e59e19590c8cc5ee3b3cff23c8bb37f0bf74a0d1d"
)


@dataclass(frozen=True)
class ClassicalQuantumParetoConfig:
    classical_weight_grid: tuple[float, ...] = tuple(
        float(value) for value in np.linspace(0.0, 1.0, 21)
    )
    bootstrap_replicates: int = 20_000
    bootstrap_seed: int = 2042
    confidence_level: float = 0.95
    minimum_pareto_jaccard: float = 0.5
    minimum_dominance_pair_accuracy: float = 0.5
    verify_input_manifest: bool = True
    expected_aggregate_sha256: str = GAVIGNET_AGGREGATE_SHA256

    def validate(self) -> None:
        if not self.classical_weight_grid:
            raise ValueError("classical_weight_grid cannot be empty")
        if any(not 0.0 <= value <= 1.0 for value in self.classical_weight_grid):
            raise ValueError("all classical weights must be between zero and one")
        if tuple(sorted(set(self.classical_weight_grid))) != self.classical_weight_grid:
            raise ValueError("classical_weight_grid must be sorted and unique")
        if self.bootstrap_replicates < 1:
            raise ValueError("bootstrap_replicates must be >= 1")
        if not 0.0 < self.confidence_level < 1.0:
            raise ValueError("confidence_level must be between zero and one")
        if not 0.0 <= self.minimum_pareto_jaccard <= 1.0:
            raise ValueError("minimum_pareto_jaccard must be between zero and one")
        if not 0.0 <= self.minimum_dominance_pair_accuracy <= 1.0:
            raise ValueError("minimum_dominance_pair_accuracy must be between zero and one")
        if len(self.expected_aggregate_sha256) != 64:
            raise ValueError("expected_aggregate_sha256 must be a SHA-256 hex digest")


@dataclass
class ClassicalQuantumParetoResult:
    report: dict
    ireland_pareto_cells: pd.DataFrame
    pareto_recovery_by_stratum: pd.DataFrame
    pareto_recovery_summary: pd.DataFrame
    joint_model_comparisons: pd.DataFrame
    external_pareto_points: pd.DataFrame
    scalarization_sensitivity: pd.DataFrame
    external_direction_checks: pd.DataFrame
    input_integrity: dict


def pareto_front_mask(
    frame: pd.DataFrame,
    *,
    minimize: tuple[str, ...] = (),
    maximize: tuple[str, ...] = (),
) -> np.ndarray:
    """Return a non-dominated mask with explicit objective directions."""
    if not minimize and not maximize:
        raise ValueError("at least one Pareto objective is required")
    columns = list(minimize + maximize)
    missing = sorted(set(columns).difference(frame.columns))
    if missing:
        raise ValueError(f"Pareto frame is missing objectives: {missing}")
    if frame[columns].isna().any().any():
        raise ValueError("Pareto objectives cannot contain missing values")
    utilities = np.column_stack([
        -pd.to_numeric(frame[column]).to_numpy(dtype=float) for column in minimize
    ] + [
        pd.to_numeric(frame[column]).to_numpy(dtype=float) for column in maximize
    ])
    result = np.ones(len(frame), dtype=bool)
    for index in range(len(frame)):
        weakly_better = np.all(utilities >= utilities[index], axis=1)
        strictly_better = np.any(utilities > utilities[index], axis=1)
        result[index] = not bool(np.any(weakly_better & strictly_better))
    return result


def _verify_manifest(directory: Path, required: tuple[str, ...]) -> dict:
    manifest_path = directory / "artifact_manifest.json"
    if not manifest_path.is_file():
        raise FileNotFoundError(f"artifact manifest not found: {manifest_path}")
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    entries = {str(item["file"]): item for item in payload.get("artifacts", [])}
    checks: dict[str, dict] = {}
    for name in required:
        path = directory / name
        if not path.is_file():
            raise FileNotFoundError(f"required input artifact not found: {path}")
        if name not in entries:
            raise ValueError(f"{name} is absent from {manifest_path}")
        expected = str(entries[name].get("sha256", ""))
        observed = sha256_file(path)
        matches = expected == observed
        checks[name] = {
            "expected_sha256": expected,
            "observed_sha256": observed,
            "matches": matches,
        }
        if not matches:
            raise ValueError(f"artifact hash mismatch for {path}")
    return checks


def _read_qkd_artifacts(directory: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    cells_path = directory / "experimental_cells.csv"
    predictions_path = directory / "out_of_stratum_predictions.csv"
    cells = pd.read_csv(cells_path)
    predictions = pd.read_csv(predictions_path)
    cell_columns = {
        "stratum_id", "noise_level", "external_signal_power_dbm",
        "qber_reported", "secure_key_rate_bps", "log1p_secure_key_rate",
    }
    prediction_columns = {
        "model", "seed", "stratum_id", "noise_level", "target", "y_true",
        "prediction",
    }
    missing_cells = sorted(cell_columns.difference(cells.columns))
    missing_predictions = sorted(prediction_columns.difference(predictions.columns))
    if missing_cells:
        raise ValueError(f"experimental_cells.csv is missing columns: {missing_cells}")
    if missing_predictions:
        raise ValueError(
            f"out_of_stratum_predictions.csv is missing columns: {missing_predictions}"
        )
    return cells, predictions


def _ireland_observed_front(cells: pd.DataFrame) -> pd.DataFrame:
    records: list[pd.DataFrame] = []
    for stratum, group in cells.groupby("stratum_id", sort=True, observed=True):
        audited = group.copy()
        audited["observed_pareto"] = pareto_front_mask(
            audited,
            minimize=("qber_reported",),
            maximize=("secure_key_rate_bps",),
        )
        qber = audited["qber_reported"].to_numpy(dtype=float)
        skr = audited["secure_key_rate_bps"].to_numpy(dtype=float)
        dominated_by = []
        for index in range(len(audited)):
            dominates = (
                (qber <= qber[index])
                & (skr >= skr[index])
                & ((qber < qber[index]) | (skr > skr[index]))
            )
            dominated_by.append(int(dominates.sum()))
        audited["dominated_by_condition_count"] = dominated_by
        audited["pareto_front_size_in_stratum"] = int(audited["observed_pareto"].sum())
        audited["analysis_level"] = "measured condition cell within fibre/VOA stratum"
        audited["stratum_id"] = str(stratum)
        records.append(audited)
    return pd.concat(records, ignore_index=True)


def _wide_joint_predictions(predictions: pd.DataFrame) -> pd.DataFrame:
    targets = ("qber_reported", "log1p_secure_key_rate_given_available")
    selected = predictions.loc[predictions["target"].isin(targets)].copy()
    keys = [
        "model", "seed", "stratum_id", "fiber_length_km",
        "voa_attenuation_db", "noise_level",
    ]
    wide = selected.pivot_table(
        index=keys,
        columns="target",
        values=["y_true", "prediction"],
        aggfunc="first",
    ).reset_index()
    wide.columns = [
        "_".join(value).strip("_") if isinstance(value, tuple) else str(value)
        for value in wide.columns
    ]
    required = [
        "y_true_qber_reported",
        "y_true_log1p_secure_key_rate_given_available",
        "prediction_qber_reported",
        "prediction_log1p_secure_key_rate_given_available",
    ]
    if not set(required).issubset(wide.columns):
        raise ValueError("predictions do not contain joint QBER/conditional-SKR outputs")
    return wide.dropna(subset=required).reset_index(drop=True)


def _dominance_accuracy(group: pd.DataFrame) -> tuple[int, int, float]:
    true_qber = group["y_true_qber_reported"].to_numpy(dtype=float)
    true_skr = group["y_true_log1p_secure_key_rate_given_available"].to_numpy(dtype=float)
    pred_qber = group["prediction_qber_reported"].to_numpy(dtype=float)
    pred_skr = group[
        "prediction_log1p_secure_key_rate_given_available"
    ].to_numpy(dtype=float)

    def dominates(qber: np.ndarray, skr: np.ndarray, left: int, right: int) -> bool:
        return bool(
            qber[left] <= qber[right]
            and skr[left] >= skr[right]
            and (qber[left] < qber[right] or skr[left] > skr[right])
        )

    comparable = 0
    correct = 0
    for left in range(len(group)):
        for right in range(left + 1, len(group)):
            true_direction = 1 if dominates(true_qber, true_skr, left, right) else (
                -1 if dominates(true_qber, true_skr, right, left) else 0
            )
            if true_direction == 0:
                continue
            predicted_direction = 1 if dominates(pred_qber, pred_skr, left, right) else (
                -1 if dominates(pred_qber, pred_skr, right, left) else 0
            )
            comparable += 1
            correct += int(predicted_direction == true_direction)
    accuracy = float(correct / comparable) if comparable else float("nan")
    return comparable, correct, accuracy


def _pareto_recovery(predictions: pd.DataFrame) -> pd.DataFrame:
    wide = _wide_joint_predictions(predictions)
    records: list[dict] = []
    for (model, seed, stratum), group in wide.groupby(
        ["model", "seed", "stratum_id"], sort=True, observed=True
    ):
        if len(group) < 2:
            continue
        true_front = pareto_front_mask(
            group,
            minimize=("y_true_qber_reported",),
            maximize=("y_true_log1p_secure_key_rate_given_available",),
        )
        predicted_front = pareto_front_mask(
            group,
            minimize=("prediction_qber_reported",),
            maximize=("prediction_log1p_secure_key_rate_given_available",),
        )
        union = int(np.sum(true_front | predicted_front))
        intersection = int(np.sum(true_front & predicted_front))
        comparable, correct, dominance_accuracy = _dominance_accuracy(group)
        records.append({
            "model": str(model),
            "seed": int(seed),
            "stratum_id": str(stratum),
            "positive_key_conditions": int(len(group)),
            "true_front_size": int(true_front.sum()),
            "predicted_front_size": int(predicted_front.sum()),
            "front_intersection": intersection,
            "front_union": union,
            "pareto_jaccard": float(intersection / union) if union else 1.0,
            "true_dominance_pairs": comparable,
            "correct_dominance_pairs": correct,
            "dominance_pair_accuracy": dominance_accuracy,
            "evaluation": "leave-one-stratum-out predictions; positive-key conditions only",
        })
    return pd.DataFrame.from_records(records)


def _recovery_summary(recovery: pd.DataFrame) -> pd.DataFrame:
    return recovery.groupby(["model", "seed"], as_index=False, observed=True).agg(
        evaluated_strata=("stratum_id", "nunique"),
        mean_pareto_jaccard=("pareto_jaccard", "mean"),
        median_pareto_jaccard=("pareto_jaccard", "median"),
        mean_dominance_pair_accuracy=("dominance_pair_accuracy", "mean"),
        comparable_dominance_pairs=("true_dominance_pairs", "sum"),
        correct_dominance_pairs=("correct_dominance_pairs", "sum"),
    )


def _bootstrap_mean_interval(
    values: np.ndarray,
    *,
    replicates: int,
    seed: int,
    confidence_level: float,
) -> tuple[float, float]:
    data = np.asarray(values, dtype=float)
    data = data[np.isfinite(data)]
    if not len(data):
        return float("nan"), float("nan")
    rng = np.random.default_rng(int(seed))
    draws = rng.choice(data, size=(int(replicates), len(data)), replace=True).mean(axis=1)
    alpha = 1.0 - confidence_level
    return (
        float(np.quantile(draws, alpha / 2.0)),
        float(np.quantile(draws, 1.0 - alpha / 2.0)),
    )


def _joint_model_comparisons(
    recovery: pd.DataFrame,
    *,
    config: ClassicalQuantumParetoConfig,
) -> pd.DataFrame:
    definitions = (
        (
            "aligned_trihead_over_permuted_condition",
            "trihead_qkd",
            "trihead_permuted_condition",
        ),
        (
            "aligned_pareto_trihead_over_permuted_condition",
            "trihead_pareto_qkd",
            "trihead_pareto_permuted_condition",
        ),
        (
            "pareto_trihead_over_conventional_trihead",
            "trihead_pareto_qkd",
            "trihead_qkd",
        ),
    )
    tasks: list[tuple[str, str, str, str, pd.DataFrame]] = []
    for contrast, candidate, reference in definitions:
        selected = recovery.loc[recovery["model"].isin((candidate, reference))].copy()
        if set(selected["model"].unique()) != {candidate, reference}:
            continue
        for metric in ("pareto_jaccard", "dominance_pair_accuracy"):
            pivot = selected.pivot_table(
                index=["seed", "stratum_id"], columns="model", values=metric,
                aggfunc="first",
            ).dropna(subset=[candidate, reference])
            if pivot.empty:
                continue
            tasks.append((contrast, candidate, reference, metric, pivot))

    records: list[dict] = []
    family_size = max(len(tasks), 1)
    familywise_alpha = 1.0 - config.confidence_level
    corrected_alpha = familywise_alpha / family_size
    simultaneous_confidence = 1.0 - corrected_alpha
    for offset, (contrast, candidate, reference, metric, pivot) in enumerate(tasks):
        pivot["improvement"] = pivot[candidate] - pivot[reference]
        by_stratum = pivot.groupby("stratum_id", observed=True)["improvement"].mean()
        raw_low, raw_high = _bootstrap_mean_interval(
            by_stratum.to_numpy(dtype=float),
            replicates=config.bootstrap_replicates,
            seed=config.bootstrap_seed + 100 + offset,
            confidence_level=config.confidence_level,
        )
        low, high = _bootstrap_mean_interval(
            by_stratum.to_numpy(dtype=float),
            replicates=config.bootstrap_replicates,
            seed=config.bootstrap_seed + offset,
            confidence_level=simultaneous_confidence,
        )
        records.append({
            "contrast": contrast,
            "metric": metric,
            "candidate_model": candidate,
            "reference_model": reference,
            "evaluated_strata": int(len(by_stratum)),
            "seeds_averaged_within_stratum": int(
                pivot.index.get_level_values("seed").nunique()
            ),
            "candidate_mean": float(pivot[candidate].mean()),
            "reference_mean": float(pivot[reference].mean()),
            "candidate_minus_reference": float(by_stratum.mean()),
            "unadjusted_ci_low": raw_low,
            "unadjusted_ci_high": raw_high,
            "bootstrap_ci_low": low,
            "bootstrap_ci_high": high,
            "familywise_confidence_level": config.confidence_level,
            "simultaneous_confidence_level": simultaneous_confidence,
            "hypothesis_family_size": family_size,
            "multiple_comparison_correction": "Bonferroni",
            "unadjusted_positive_beyond_interval": bool(raw_low > 0.0),
            "positive_beyond_interval": bool(low > 0.0),
            "inference_status": (
                "exploratory paired-stratum bootstrap; source was previously inspected"
            ),
        })
    return pd.DataFrame.from_records(records)


def _minmax_benefit(values: pd.Series, *, higher_is_better: bool) -> np.ndarray:
    numeric = pd.to_numeric(values).to_numpy(dtype=float)
    if not higher_is_better:
        numeric = -numeric
    span = float(numeric.max() - numeric.min())
    if span <= 0.0:
        return np.ones(len(numeric), dtype=float)
    return (numeric - numeric.min()) / span


def _external_pareto(
    observations: pd.DataFrame,
    *,
    config: ClassicalQuantumParetoConfig,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    contrasts, diagnostics = analyze_published_aggregate_observations(observations)
    points = observations.loc[
        observations["design_id"].eq("multi-span-mu-50km")
    ].sort_values("wdm_total_power_dbm", kind="stable").copy()
    points["classical_osnr_utility"] = _minmax_benefit(
        points["osnr_db_0p1nm"], higher_is_better=True
    )
    points["classical_ber_utility"] = _minmax_benefit(
        np.log10(points["pre_fec_ber"]), higher_is_better=False
    )
    points["quantum_qber_utility"] = _minmax_benefit(
        points["qber_percent"], higher_is_better=False
    )
    points["quantum_skr_utility"] = _minmax_benefit(
        np.log1p(points["secure_key_rate_kbps"]), higher_is_better=True
    )
    points["classical_utility"] = points[
        ["classical_osnr_utility", "classical_ber_utility"]
    ].mean(axis=1)
    points["quantum_utility"] = points[
        ["quantum_qber_utility", "quantum_skr_utility"]
    ].mean(axis=1)
    points["four_objective_pareto"] = pareto_front_mask(
        points,
        maximize=(
            "classical_osnr_utility", "classical_ber_utility",
            "quantum_qber_utility", "quantum_skr_utility",
        ),
    )
    points["external_used_for_training"] = False
    points["inference_level"] = "three approximate published 24-hour condition means"

    scalar_records: list[dict] = []
    for weight in config.classical_weight_grid:
        scores = (
            float(weight) * points["classical_utility"]
            + (1.0 - float(weight)) * points["quantum_utility"]
        )
        maximum = float(scores.max())
        for (_, row), score in zip(points.iterrows(), scores, strict=True):
            scalar_records.append({
                "classical_weight": float(weight),
                "quantum_weight": float(1.0 - weight),
                "condition_id": str(row["condition_id"]),
                "wdm_total_power_dbm": float(row["wdm_total_power_dbm"]),
                "classical_utility": float(row["classical_utility"]),
                "quantum_utility": float(row["quantum_utility"]),
                "weighted_score": float(score),
                "selected_by_scalarization": bool(np.isclose(score, maximum)),
                "policy_status": "illustrative weight sensitivity; weights are not calibrated",
            })
    scalarization = pd.DataFrame.from_records(scalar_records)
    direction = pd.concat([
        contrasts.assign(check_family="single_span_baseline_contrast"),
        pd.DataFrame.from_records([{
            "study_id": diagnostics["study_id"].iloc[0],
            "design_id": diagnostics["design_id"].iloc[0],
            "contrast": "ordered-power-monotonicity",
            "qber_degraded": bool(diagnostics["qber_all_adjacent_steps_degrade"].iloc[0]),
            "skr_degraded": bool(diagnostics["skr_all_adjacent_steps_degrade"].iloc[0]),
            "inference_level": diagnostics["interpretation"].iloc[0],
            "check_family": "multi_span_ordered_aggregate",
        }]),
    ], ignore_index=True, sort=False)
    return points, scalarization, direction


def run_classical_quantum_pareto_campaign(
    qkd_multihead_dir: str | Path,
    aggregate_csv: str | Path,
    *,
    config: ClassicalQuantumParetoConfig = ClassicalQuantumParetoConfig(),
) -> ClassicalQuantumParetoResult:
    config.validate()
    qkd_directory = Path(qkd_multihead_dir)
    aggregate_path = Path(aggregate_csv)
    required = ("experimental_cells.csv", "out_of_stratum_predictions.csv")
    manifest_checks = (
        _verify_manifest(qkd_directory, required)
        if config.verify_input_manifest
        else {}
    )
    aggregate_hash = sha256_file(aggregate_path)
    if aggregate_hash.lower() != config.expected_aggregate_sha256.lower():
        raise ValueError(
            "published aggregate hash mismatch; update the frozen protocol only after "
            "auditing the source transcription"
        )
    cells, predictions = _read_qkd_artifacts(qkd_directory)
    required_joint_models = {
        "trihead_qkd",
        "trihead_permuted_condition",
        "trihead_pareto_qkd",
        "trihead_pareto_permuted_condition",
    }
    missing_joint_models = sorted(
        required_joint_models.difference(predictions["model"].astype(str).unique())
    )
    if missing_joint_models:
        raise ValueError(
            "Pareto protocol v2 requires missing prediction models: "
            f"{missing_joint_models}; rerun run_qkd_multihead_campaign.py"
        )
    observations = load_published_aggregate_observations(aggregate_path)
    ireland = _ireland_observed_front(cells)
    recovery = _pareto_recovery(predictions)
    summary = _recovery_summary(recovery)
    comparison = _joint_model_comparisons(recovery, config=config)
    external, scalarization, direction = _external_pareto(
        observations, config=config
    )
    trihead = summary.loc[summary["model"].eq("trihead_qkd")]
    permuted = summary.loc[summary["model"].eq("trihead_permuted_condition")]
    pareto_trihead = summary.loc[summary["model"].eq("trihead_pareto_qkd")]
    pareto_permuted = summary.loc[
        summary["model"].eq("trihead_pareto_permuted_condition")
    ]
    conventional_alignment = comparison.loc[comparison["contrast"].eq(
        "aligned_trihead_over_permuted_condition"
    )]
    pareto_alignment = comparison.loc[comparison["contrast"].eq(
        "aligned_pareto_trihead_over_permuted_condition"
    )]
    pareto_over_conventional = comparison.loc[comparison["contrast"].eq(
        "pareto_trihead_over_conventional_trihead"
    )]
    pareto_alignment_dominance = pareto_alignment.loc[
        pareto_alignment["metric"].eq("dominance_pair_accuracy")
    ].iloc[0]
    pareto_conventional_dominance = pareto_over_conventional.loc[
        pareto_over_conventional["metric"].eq("dominance_pair_accuracy")
    ].iloc[0]
    winners = scalarization.loc[scalarization["selected_by_scalarization"]]
    report = {
        "study": "Classical-quantum Pareto and joint-head recovery audit",
        "status": "frozen exploratory protocol; all source datasets were previously inspected",
        "versioned_hypotheses": {
            "H1_internal_joint_recovery": (
                "TriHead reaches mean Pareto Jaccard and dominance-pair accuracy of at "
                "least 0.5 on out-of-stratum measured QBER/SKR conditions."
            ),
            "H2_alignment_control": (
                "Aligned TriHead improves Pareto recovery over within-stratum permuted conditions."
            ),
            "H3_pareto_aware_loss": (
                "A fixed train-only dominance penalty improves joint Pareto recovery over "
                "the conventional TriHead and remains superior to its permuted-WDM control."
            ),
            "H4_external_structure": (
                "The held-out Gavignet aggregates preserve adverse quantum direction and "
                "an antagonistic classical/quantum Pareto trade-off."
            ),
        },
        "training_and_holdout_boundary": (
            "No model is fitted in this campaign. IrelandQCI predictions come from the prior "
            "leave-one-stratum-out MultiHead run; Gavignet is loaded only for external "
            "structure checks and scalarization sensitivity."
        ),
        "multiple_comparison_correction": (
            "Bonferroni over all joint model/metric contrasts; paired-stratum bootstrap"
        ),
        "ireland": {
            "measured_cells": int(len(ireland)),
            "strata": int(ireland["stratum_id"].nunique()),
            "observed_pareto_cells": int(ireland["observed_pareto"].sum()),
            "twelve_dbm_dominated_strata": int(ireland.loc[
                ireland["noise_level"].astype(str).eq("12"),
                "observed_pareto",
            ].eq(False).sum()),
            "joint_positive_key_strata_evaluated": int(recovery["stratum_id"].nunique()),
            "trihead_mean_pareto_jaccard": float(trihead["mean_pareto_jaccard"].mean()),
            "trihead_mean_dominance_pair_accuracy": float(
                trihead["mean_dominance_pair_accuracy"].mean()
            ),
            "permuted_mean_pareto_jaccard": float(permuted["mean_pareto_jaccard"].mean()),
            "pareto_trihead_mean_pareto_jaccard": float(
                pareto_trihead["mean_pareto_jaccard"].mean()
            ),
            "pareto_trihead_mean_dominance_pair_accuracy": float(
                pareto_trihead["mean_dominance_pair_accuracy"].mean()
            ),
            "pareto_permuted_mean_pareto_jaccard": float(
                pareto_permuted["mean_pareto_jaccard"].mean()
            ),
            "trihead_joint_recovery_threshold_passed": bool(
                trihead["mean_pareto_jaccard"].mean() >= config.minimum_pareto_jaccard
                and trihead["mean_dominance_pair_accuracy"].mean()
                >= config.minimum_dominance_pair_accuracy
            ),
            "pareto_trihead_joint_recovery_threshold_passed": bool(
                pareto_trihead["mean_pareto_jaccard"].mean()
                >= config.minimum_pareto_jaccard
                and pareto_trihead["mean_dominance_pair_accuracy"].mean()
                >= config.minimum_dominance_pair_accuracy
            ),
            "aligned_condition_pareto_evidence": bool(
                len(conventional_alignment) == 2
                and conventional_alignment["positive_beyond_interval"].all()
            ),
            "pareto_aligned_condition_evidence": bool(
                len(pareto_alignment) == 2
                and pareto_alignment["positive_beyond_interval"].all()
            ),
            "pareto_over_conventional_evidence": bool(
                len(pareto_over_conventional) == 2
                and pareto_over_conventional["positive_beyond_interval"].all()
            ),
            "pareto_dominance_accuracy_alignment_control": {
                "aligned_minus_permuted": float(
                    pareto_alignment_dominance["candidate_minus_reference"]
                ),
                "bonferroni_bootstrap_ci": [
                    float(pareto_alignment_dominance["bootstrap_ci_low"]),
                    float(pareto_alignment_dominance["bootstrap_ci_high"]),
                ],
                "positive_beyond_interval": bool(
                    pareto_alignment_dominance["positive_beyond_interval"]
                ),
            },
            "pareto_dominance_accuracy_over_conventional": {
                "pareto_minus_conventional": float(
                    pareto_conventional_dominance["candidate_minus_reference"]
                ),
                "bonferroni_bootstrap_ci": [
                    float(pareto_conventional_dominance["bootstrap_ci_low"]),
                    float(pareto_conventional_dominance["bootstrap_ci_high"]),
                ],
                "positive_beyond_interval": bool(
                    pareto_conventional_dominance["positive_beyond_interval"]
                ),
            },
        },
        "gavignet_external": {
            "published_condition_means": int(len(external)),
            "all_conditions_four_objective_pareto": bool(
                external["four_objective_pareto"].all()
            ),
            "all_direction_checks_consistent": bool(
                direction[["qber_degraded", "skr_degraded"]].all().all()
            ),
            "scalarization_distinct_winners": sorted(
                winners["condition_id"].astype(str).unique().tolist()
            ),
            "external_used_for_training": False,
        },
        "scientific_conclusion": (
            "The fixed Pareto-aware loss raises dominance-pair accuracy and remains above "
            "its permuted-WDM control after correction, providing exploratory evidence that "
            "aligned coexistence conditions help order measured QBER/SKR outcomes. It does "
            "not recover the complete Pareto front or establish superiority over the "
            "conventional TriHead. The external hardware aggregate independently supports a "
            "real classical/quantum trade-off; temporal predictive superiority remains untested."
        ),
        "not_claimed": [
            "No temporal WDM-to-quantum forecast is tested.",
            "Published Gavignet means are not independent samples.",
            "Scalarization weights are illustrative and not operator-calibrated.",
            "IrelandQCI's ambiguous positive QBER scale is not converted.",
        ],
        "configuration": asdict(config),
        "git": git_state(),
    }
    integrity = {
        "qkd_multihead_manifest_verified": config.verify_input_manifest,
        "qkd_multihead_artifacts": manifest_checks,
        "aggregate_expected_sha256": config.expected_aggregate_sha256,
        "aggregate_observed_sha256": aggregate_hash,
        "aggregate_matches": True,
    }
    return ClassicalQuantumParetoResult(
        report=report,
        ireland_pareto_cells=ireland,
        pareto_recovery_by_stratum=recovery,
        pareto_recovery_summary=summary,
        joint_model_comparisons=comparison,
        external_pareto_points=external,
        scalarization_sensitivity=scalarization,
        external_direction_checks=direction,
        input_integrity=integrity,
    )


def write_classical_quantum_pareto_artifacts(
    result: ClassicalQuantumParetoResult,
    output_dir: str | Path,
) -> dict:
    directory = Path(output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    tables = {
        "ireland_pareto_cells.csv": result.ireland_pareto_cells,
        "pareto_recovery_by_stratum.csv": result.pareto_recovery_by_stratum,
        "pareto_recovery_summary.csv": result.pareto_recovery_summary,
        "joint_model_comparisons.csv": result.joint_model_comparisons,
        "external_pareto_points.csv": result.external_pareto_points,
        "scalarization_sensitivity.csv": result.scalarization_sensitivity,
        "external_direction_checks.csv": result.external_direction_checks,
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
        "artifacts": [
            {"file": path.name, "sha256": sha256_file(path), "bytes": path.stat().st_size}
            for path in artifacts
        ]
    }
    (directory / "artifact_manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return manifest


__all__ = [
    "ClassicalQuantumParetoConfig",
    "ClassicalQuantumParetoResult",
    "GAVIGNET_AGGREGATE_SHA256",
    "pareto_front_mask",
    "run_classical_quantum_pareto_campaign",
    "write_classical_quantum_pareto_artifacts",
]
