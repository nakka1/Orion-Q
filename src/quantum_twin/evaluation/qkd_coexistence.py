"""Measured-QKD analysis for controlled classical/quantum coexistence sweeps."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.linear_model import Ridge
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from quantum_twin.evaluation.optical_campaign import git_state, sha256_file
from quantum_twin.quantum.qkd_coexistence import QKDCoexistenceSource


MEASURED_QKD_TARGETS = ("qber_reported", "log1p_secure_key_rate")
NOISE_LEVELS = (3.0, 7.0, 9.0, 12.0)


@dataclass(frozen=True)
class QKDCoexistenceConfig:
    bootstrap_replicates: int = 50_000
    familywise_alpha: float = 0.05
    ridge_alpha: float = 1.0
    seed: int = 42

    def validate(self) -> None:
        if self.bootstrap_replicates < 1:
            raise ValueError("bootstrap_replicates must be >= 1")
        if not 0.0 < self.familywise_alpha < 1.0:
            raise ValueError("familywise_alpha must be between 0 and 1")
        if self.ridge_alpha <= 0.0:
            raise ValueError("ridge_alpha must be positive")


@dataclass
class QKDCoexistenceResult:
    report: dict
    cells: pd.DataFrame
    primary_contrasts: pd.DataFrame
    dose_contrasts: pd.DataFrame
    predictions: pd.DataFrame
    predictive_comparisons: pd.DataFrame


def aggregate_experimental_cells(frame: pd.DataFrame) -> pd.DataFrame:
    """Use one row per controlled configuration, not each repeated reading."""
    data = frame.copy()
    data["noise_level"] = data["external_signal_power_dbm"].map(
        lambda value: "baseline" if pd.isna(value) else f"{float(value):g}"
    )
    data["log1p_secure_key_rate"] = np.log1p(data["secure_key_rate_bps"])
    keys = [
        "fiber_length_km", "voa_attenuation_db", "external_signal_present",
        "noise_level", "external_signal_power_dbm",
    ]
    cells = data.groupby(keys, dropna=False, observed=True, as_index=False).agg(
        n_measurements=("qber_reported", "size"),
        qber_reported=("qber_reported", "mean"),
        qber_reported_sd=("qber_reported", "std"),
        secure_key_rate_bps=("secure_key_rate_bps", "mean"),
        secure_key_rate_sd=("secure_key_rate_bps", "std"),
        log1p_secure_key_rate=("log1p_secure_key_rate", "mean"),
    )
    cells["stratum_id"] = (
        cells["fiber_length_km"].astype(str)
        + "km|voa=" + cells["voa_attenuation_db"].astype(str)
    )
    return cells.sort_values(
        ["fiber_length_km", "voa_attenuation_db", "external_signal_present",
         "external_signal_power_dbm"],
        kind="stable", na_position="first",
    ).reset_index(drop=True)


def _matched_degradation(
    cells: pd.DataFrame,
    *,
    target: str,
    noise_power_dbm: float | None,
) -> pd.DataFrame:
    baseline = cells.loc[cells["noise_level"].eq("baseline"), [
        "stratum_id", "fiber_length_km", "voa_attenuation_db", target,
    ]].rename(columns={target: "baseline"})
    injected = cells.loc[cells["external_signal_present"].eq(1)].copy()
    if noise_power_dbm is None:
        injected = injected.groupby(
            ["stratum_id", "fiber_length_km", "voa_attenuation_db"],
            observed=True,
            as_index=False,
        )[target].mean()
    else:
        injected = injected.loc[
            injected["external_signal_power_dbm"].eq(float(noise_power_dbm))
        ]
    injected = injected[[
        "stratum_id", "fiber_length_km", "voa_attenuation_db", target,
    ]].rename(columns={target: "injected"})
    matched = baseline.merge(
        injected,
        on=["stratum_id", "fiber_length_km", "voa_attenuation_db"],
        how="inner",
        validate="one_to_one",
    )
    if target == "qber_reported":
        matched["degradation"] = matched["injected"] - matched["baseline"]
        unit = "publisher-reported QBER scale (ratio or % ambiguous)"
    else:
        matched["degradation"] = matched["baseline"] - matched["injected"]
        unit = "log1p(bit/s)"
    matched["target_unit"] = unit
    return matched


def _bootstrap_mean_ci(
    values: np.ndarray,
    *,
    replicates: int,
    seed: int,
    alpha: float,
) -> tuple[float, float]:
    data = np.asarray(values, dtype=float)
    rng = np.random.default_rng(seed)
    samples = rng.choice(data, size=(int(replicates), len(data)), replace=True).mean(axis=1)
    return (
        float(np.quantile(samples, alpha / 2.0)),
        float(np.quantile(samples, 1.0 - alpha / 2.0)),
    )


def _exact_positive_sign_flip_p(values: np.ndarray) -> float:
    """Exact one-sided randomization p-value for a positive mean effect."""
    data = np.asarray(values, dtype=float)
    if len(data) > 20:
        raise ValueError("exact sign-flip enumeration is limited to 20 matched strata")
    observed = float(data.mean())
    combinations = np.arange(1 << len(data), dtype=np.uint32)[:, None]
    bits = (combinations >> np.arange(len(data), dtype=np.uint32)) & 1
    signs = np.where(bits == 1, 1.0, -1.0)
    null_means = (signs * data).mean(axis=1)
    return float(np.mean(null_means >= observed - 1e-15))


def _contrast_record(
    matched: pd.DataFrame,
    *,
    target: str,
    contrast: str,
    noise_power_dbm: float | None,
    replicates: int,
    seed: int,
    familywise_alpha: float,
    family_size: int,
) -> dict:
    values = matched["degradation"].to_numpy(dtype=float)
    corrected_alpha = familywise_alpha / int(family_size)
    low, high = _bootstrap_mean_ci(
        values, replicates=replicates, seed=seed, alpha=corrected_alpha
    )
    raw_p = _exact_positive_sign_flip_p(values)
    corrected_p = min(raw_p * int(family_size), 1.0)
    return {
        "target": target,
        "target_unit": matched["target_unit"].iloc[0],
        "contrast": contrast,
        "noise_power_dbm": noise_power_dbm,
        "matched_strata": int(len(matched)),
        "mean_degradation": float(values.mean()),
        "median_degradation": float(np.median(values)),
        "ci_low": low,
        "ci_high": high,
        "raw_one_sided_exact_p": raw_p,
        "corrected_p": corrected_p,
        "family_size": int(family_size),
        "multiple_comparison_correction": "Bonferroni",
        "significant_measured_quantum_degradation": bool(
            values.mean() > 0.0 and low > 0.0 and corrected_p < familywise_alpha
        ),
        "fiber_0km_mean_degradation": float(
            matched.loc[matched["fiber_length_km"].eq(0.0), "degradation"].mean()
        ),
        "fiber_50km_mean_degradation": float(
            matched.loc[matched["fiber_length_km"].eq(50.0), "degradation"].mean()
        ),
    }


def compute_controlled_contrasts(
    cells: pd.DataFrame,
    *,
    config: QKDCoexistenceConfig,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    primary_family = len(MEASURED_QKD_TARGETS)
    primary = []
    for offset, target in enumerate(MEASURED_QKD_TARGETS):
        matched = _matched_degradation(cells, target=target, noise_power_dbm=None)
        primary.append(_contrast_record(
            matched,
            target=target,
            contrast="average_injected_signal_vs_baseline",
            noise_power_dbm=None,
            replicates=config.bootstrap_replicates,
            seed=config.seed + offset,
            familywise_alpha=config.familywise_alpha,
            family_size=primary_family,
        ))

    dose_family = len(MEASURED_QKD_TARGETS) * len(NOISE_LEVELS)
    dose = []
    offset = 0
    for target in MEASURED_QKD_TARGETS:
        for power in NOISE_LEVELS:
            matched = _matched_degradation(cells, target=target, noise_power_dbm=power)
            dose.append(_contrast_record(
                matched,
                target=target,
                contrast="injected_power_vs_matched_baseline",
                noise_power_dbm=power,
                replicates=config.bootstrap_replicates,
                seed=config.seed + 100 + offset,
                familywise_alpha=config.familywise_alpha,
                family_size=dose_family,
            ))
            offset += 1
    return pd.DataFrame(primary), pd.DataFrame(dose)


def _design_matrix(cells: pd.DataFrame, *, include_coexistence: bool) -> np.ndarray:
    voa = cells["voa_attenuation_db"].to_numpy(dtype=float)
    distance = cells["fiber_length_km"].to_numpy(dtype=float) / 50.0
    columns = [voa, voa ** 2, distance, voa * distance]
    if include_coexistence:
        for power in NOISE_LEVELS:
            indicator = cells["external_signal_power_dbm"].eq(power).to_numpy(dtype=float)
            columns.extend([indicator, indicator * distance, indicator * voa])
    return np.column_stack(columns)


def leave_stratum_out_predictions(
    cells: pd.DataFrame,
    *,
    config: QKDCoexistenceConfig,
) -> pd.DataFrame:
    """Hold out all five coexistence conditions at one distance/VOA stratum."""
    baseline_X = _design_matrix(cells, include_coexistence=False)
    combined_X = _design_matrix(cells, include_coexistence=True)
    records: list[dict] = []
    for target in MEASURED_QKD_TARGETS:
        y = cells[target].to_numpy(dtype=float)
        for stratum in sorted(cells["stratum_id"].unique()):
            test = cells["stratum_id"].eq(stratum).to_numpy()
            train = ~test
            for feature_set, X in (
                ("path_only", baseline_X),
                ("path_plus_coexistence", combined_X),
            ):
                model = make_pipeline(
                    StandardScaler(), Ridge(alpha=float(config.ridge_alpha))
                )
                model.fit(X[train], y[train])
                prediction = model.predict(X[test])
                subset = cells.loc[test]
                true = y[test]
                records.append(pd.DataFrame({
                    "target": target,
                    "target_unit": (
                        "publisher-reported QBER scale (ratio or % ambiguous)"
                        if target == "qber_reported" else "log1p(bit/s)"
                    ),
                    "stratum_id": stratum,
                    "fiber_length_km": subset["fiber_length_km"].to_numpy(),
                    "voa_attenuation_db": subset["voa_attenuation_db"].to_numpy(),
                    "noise_level": subset["noise_level"].to_numpy(),
                    "feature_set": feature_set,
                    "y_true": true,
                    "prediction": prediction,
                    "absolute_error": np.abs(true - prediction),
                }))
    return pd.concat(records, ignore_index=True)


def _cluster_bootstrap_difference(
    group_differences: list[np.ndarray],
    *,
    replicates: int,
    seed: int,
    alpha: float,
) -> tuple[float, float]:
    rng = np.random.default_rng(seed)
    group_means = np.asarray([values.mean() for values in group_differences], dtype=float)
    sampled = rng.choice(
        group_means, size=(int(replicates), len(group_means)), replace=True
    ).mean(axis=1)
    return (
        float(np.quantile(sampled, alpha / 2.0)),
        float(np.quantile(sampled, 1.0 - alpha / 2.0)),
    )


def compare_predictive_information(
    predictions: pd.DataFrame,
    *,
    config: QKDCoexistenceConfig,
) -> pd.DataFrame:
    pivot = predictions.pivot(
        index=[
            "target", "target_unit", "stratum_id", "fiber_length_km",
            "voa_attenuation_db", "noise_level",
        ],
        columns="feature_set",
        values="absolute_error",
    ).reset_index()
    family_size = len(MEASURED_QKD_TARGETS)
    corrected_alpha = config.familywise_alpha / family_size
    records = []
    for offset, (key, task) in enumerate(pivot.groupby(
        ["target", "target_unit"], sort=True, observed=True
    )):
        task = task.copy()
        task["improvement"] = task["path_only"] - task["path_plus_coexistence"]
        groups = [
            group["improvement"].to_numpy(dtype=float)
            for _, group in task.groupby("stratum_id", sort=True, observed=True)
        ]
        low, high = _cluster_bootstrap_difference(
            groups,
            replicates=config.bootstrap_replicates,
            seed=config.seed + 500 + offset,
            alpha=corrected_alpha,
        )
        improvement = float(task["improvement"].mean())
        records.append({
            "target": key[0],
            "target_unit": key[1],
            "held_out_strata": int(task["stratum_id"].nunique()),
            "cells": int(len(task)),
            "path_only_mae": float(task["path_only"].mean()),
            "path_plus_coexistence_mae": float(task["path_plus_coexistence"].mean()),
            "absolute_improvement": improvement,
            "ci_low": low,
            "ci_high": high,
            "family_size": family_size,
            "multiple_comparison_correction": "Bonferroni",
            "coexistence_adds_predictive_information": bool(improvement > 0.0 and low > 0.0),
        })
    return pd.DataFrame(records)


def run_qkd_coexistence_campaign(
    dataset_dir: str | Path,
    *,
    config: QKDCoexistenceConfig = QKDCoexistenceConfig(),
) -> QKDCoexistenceResult:
    config.validate()
    source = QKDCoexistenceSource(dataset_dir)
    frame = source.read()
    cells = aggregate_experimental_cells(frame)
    primary, dose = compute_controlled_contrasts(cells, config=config)
    predictions = leave_stratum_out_predictions(cells, config=config)
    predictive = compare_predictive_information(predictions, config=config)
    report = {
        "study": "measured QKD coexistence dependence on classical optical conditions",
        "claim_scope": (
            "Measured QBER and secure-key-rate response to controlled classical-signal "
            "injection, conditional on fibre length and VOA attenuation."
        ),
        "not_claimed": [
            "Bell-pair fidelity was not measured.",
            "The sweeps have no timestamps, so temporal forecasting is not tested.",
            "QBER is not converted to Bell-state fidelity.",
            "The publisher labels QBER as 'ratio (or %)'; no scale is assumed.",
            "Session order/randomization is not documented; residual session drift is possible.",
        ],
        "status": "exploratory; values were inspected before this protocol was frozen",
        "dataset": {
            "doi": "10.5281/zenodo.21132088",
            "license": "CC-BY-4.0",
            "quality": source.quality_report(frame),
            "provenance": frame.attrs["telemetry_provenance"],
        },
        "configuration": asdict(config),
        "experimental_unit": (
            "mean of repeated readings within each fibre/VOA/signal-power cell"
        ),
        "primary_estimand": (
            "matched quantum degradation under the average injected-signal condition "
            "versus no-signal baseline"
        ),
        "predictive_estimand": (
            "leave-stratum-out MAE(path only) - MAE(path plus coexistence condition)"
        ),
        "primary_positive_count": int(
            primary["significant_measured_quantum_degradation"].sum()
        ),
        "primary_test_count": int(len(primary)),
        "predictive_positive_count": int(
            predictive["coexistence_adds_predictive_information"].sum()
        ),
        "predictive_test_count": int(len(predictive)),
        "git": git_state(),
    }
    return QKDCoexistenceResult(
        report=report,
        cells=cells,
        primary_contrasts=primary,
        dose_contrasts=dose,
        predictions=predictions,
        predictive_comparisons=predictive,
    )


def _plot_qkd(result: QKDCoexistenceResult, directory: Path) -> list[Path]:
    import matplotlib

    matplotlib.use("Agg", force=True)
    import matplotlib.pyplot as plt

    generated = []
    for target, ylabel in (
        ("qber_reported", "QBER medido (escala publicada ambígua)"),
        ("secure_key_rate_bps", "Taxa de chave segura (bit/s)"),
    ):
        figure, axes = plt.subplots(1, 2, figsize=(12, 4.5), sharey=False)
        for axis, distance in zip(axes, (0.0, 50.0)):
            subset = result.cells.loc[result.cells["fiber_length_km"].eq(distance)]
            for noise, group in subset.groupby("noise_level", sort=False, observed=True):
                ordered = group.sort_values("voa_attenuation_db")
                axis.plot(
                    ordered["voa_attenuation_db"], ordered[target], marker="o", label=str(noise)
                )
            axis.set_title(f"{distance:g} km")
            axis.set_xlabel("Atenuação VOA (dB)")
            axis.grid(alpha=0.25)
        axes[0].set_ylabel(ylabel)
        axes[1].legend(title="Sinal injetado (dBm)", fontsize=8)
        figure.tight_layout()
        path = directory / f"measured_{target}_coexistence.png"
        figure.savefig(path, dpi=180)
        plt.close(figure)
        generated.append(path)
    return generated


def write_qkd_coexistence_artifacts(
    result: QKDCoexistenceResult,
    output_dir: str | Path,
    *,
    make_plots: bool = True,
) -> dict:
    directory = Path(output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    artifacts: list[Path] = []
    tables = {
        "experimental_cells.csv": result.cells,
        "primary_matched_contrasts.csv": result.primary_contrasts,
        "dose_matched_contrasts.csv": result.dose_contrasts,
        "leave_stratum_out_predictions.csv": result.predictions,
        "predictive_comparisons.csv": result.predictive_comparisons,
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
    if make_plots:
        artifacts.extend(_plot_qkd(result, directory))
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
    "MEASURED_QKD_TARGETS",
    "NOISE_LEVELS",
    "QKDCoexistenceConfig",
    "QKDCoexistenceResult",
    "aggregate_experimental_cells",
    "compare_predictive_information",
    "compute_controlled_contrasts",
    "leave_stratum_out_predictions",
    "run_qkd_coexistence_campaign",
    "write_qkd_coexistence_artifacts",
]
