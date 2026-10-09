"""Hierarchical robustness audit for raw IrelandQCI QKD coexistence readings.

The published files contain repeated readings inside controlled VOA cells, but
each distance/coexistence condition is stored in a single sweep session.  This
module therefore separates two questions:

* conditional measurement robustness, evaluated by resampling VOA strata and
  moving blocks of readings within every cell; and
* session-level identification, audited from the campaign layout rather than
  manufactured with a random effect that the design cannot estimate.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from pathlib import Path

import numpy as np
import pandas as pd

from quantum_twin.evaluation.optical_campaign import git_state, sha256_file
from quantum_twin.evaluation.qkd_coexistence import NOISE_LEVELS
from quantum_twin.quantum.qkd_coexistence import QKDCoexistenceSource


TARGET_UNITS = {
    "qber_reported": "publisher-reported QBER scale (ratio or % ambiguous)",
    "log1p_secure_key_rate": "log1p(bit/s)",
}


@dataclass(frozen=True)
class QKDHierarchicalRobustnessConfig:
    bootstrap_replicates: int = 20_000
    block_lengths: tuple[int, ...] = (1, 5, 10)
    familywise_alpha: float = 0.05
    seed: int = 24_071

    def validate(self) -> None:
        if self.bootstrap_replicates < 1:
            raise ValueError("bootstrap_replicates must be positive")
        if not self.block_lengths or any(length < 1 for length in self.block_lengths):
            raise ValueError("block_lengths must contain positive integers")
        if len(set(self.block_lengths)) != len(self.block_lengths):
            raise ValueError("block_lengths must be unique")
        if not 0.0 < self.familywise_alpha < 1.0:
            raise ValueError("familywise_alpha must be between zero and one")


@dataclass
class QKDHierarchicalRobustnessResult:
    report: dict
    design_audit: pd.DataFrame
    raw_cell_summary: pd.DataFrame
    stratum_effects: pd.DataFrame
    primary_intervals: pd.DataFrame
    dose_intervals: pd.DataFrame
    influence_analysis: pd.DataFrame
    input_integrity: dict


def prepare_raw_readings(frame: pd.DataFrame) -> pd.DataFrame:
    required = {
        "campaign_id", "sample_order", "external_signal_present",
        "external_signal_power_dbm", "fiber_length_km", "voa_attenuation_db",
        "qber_reported", "secure_key_rate_bps",
    }
    missing = sorted(required.difference(frame.columns))
    if missing:
        raise ValueError(f"raw QKD readings are missing fields: {missing}")
    data = frame.copy()
    data["noise_level"] = data["external_signal_power_dbm"].map(
        lambda value: "baseline" if pd.isna(value) else f"{float(value):g}"
    )
    data["log1p_secure_key_rate"] = np.log1p(
        data["secure_key_rate_bps"].to_numpy(dtype=float)
    )
    data["stratum_id"] = (
        data["fiber_length_km"].astype(str)
        + "km|voa="
        + data["voa_attenuation_db"].astype(str)
    )
    if data[list(TARGET_UNITS)].isna().any().any():
        raise ValueError("raw QKD targets cannot contain missing values")
    return data.sort_values(
        ["campaign_id", "sample_order"], kind="stable"
    ).reset_index(drop=True)


def audit_campaign_design(data: pd.DataFrame) -> pd.DataFrame:
    condition_keys = [
        "external_signal_present", "external_signal_power_dbm",
    ]
    campaign_conditions = data.drop_duplicates(
        ["campaign_id", *condition_keys]
    ).groupby("campaign_id", observed=True).size()
    distance_condition = data.groupby(
        ["fiber_length_km", *condition_keys], dropna=False, observed=True
    )["campaign_id"].nunique()
    cell_campaigns = data.groupby(
        ["stratum_id", "noise_level"], observed=True
    )["campaign_id"].nunique()
    contiguous = []
    for _, group in data.groupby(
        ["campaign_id", "voa_attenuation_db"], sort=False, observed=True
    ):
        order = np.sort(group["sample_order"].to_numpy(dtype=int))
        contiguous.append(bool(len(order) < 2 or np.all(np.diff(order) == 1)))
    session_replication_min = int(distance_condition.min())
    one_condition_per_campaign = bool(campaign_conditions.max() == 1)
    one_campaign_per_distance_condition = bool(distance_condition.max() == 1)
    session_effect_separable = bool(session_replication_min >= 2)
    return pd.DataFrame([{
        "raw_readings": int(len(data)),
        "campaigns": int(data["campaign_id"].nunique()),
        "physical_strata": int(data["stratum_id"].nunique()),
        "controlled_cells": int(data.groupby(
            ["stratum_id", "noise_level"], observed=True
        ).ngroups),
        "minimum_readings_per_cell": int(data.groupby(
            ["stratum_id", "noise_level"], observed=True
        ).size().min()),
        "maximum_readings_per_cell": int(data.groupby(
            ["stratum_id", "noise_level"], observed=True
        ).size().max()),
        "one_condition_per_campaign": one_condition_per_campaign,
        "one_campaign_per_distance_condition": one_campaign_per_distance_condition,
        "minimum_sessions_per_distance_condition": session_replication_min,
        "maximum_campaigns_per_cell": int(cell_campaigns.max()),
        "within_cell_sample_order_contiguous": bool(all(contiguous)),
        "condition_session_aliased": bool(
            one_condition_per_campaign and one_campaign_per_distance_condition
        ),
        "session_effect_separable": session_effect_separable,
        "campaign_clustered_confirmatory_inference_available": bool(
            session_effect_separable and not one_campaign_per_distance_condition
        ),
    }])


def summarize_raw_cells(data: pd.DataFrame) -> pd.DataFrame:
    keys = [
        "stratum_id", "fiber_length_km", "voa_attenuation_db", "noise_level",
        "external_signal_present", "external_signal_power_dbm",
    ]
    cells = data.groupby(keys, dropna=False, observed=True, as_index=False).agg(
        campaign_count=("campaign_id", "nunique"),
        campaign_id=("campaign_id", "first"),
        n_readings=("qber_reported", "size"),
        qber_reported=("qber_reported", "mean"),
        qber_reported_sd=("qber_reported", "std"),
        log1p_secure_key_rate=("log1p_secure_key_rate", "mean"),
        log1p_secure_key_rate_sd=("log1p_secure_key_rate", "std"),
    )
    if not cells["campaign_count"].eq(1).all():
        raise ValueError(
            "this audit requires one published campaign per controlled cell"
        )
    expected = {"baseline", *(f"{power:g}" for power in NOISE_LEVELS)}
    observed = cells.groupby("stratum_id", observed=True)["noise_level"].agg(set)
    invalid = observed.loc[~observed.map(lambda values: values == expected)]
    if not invalid.empty:
        raise ValueError(
            "strata do not contain the complete condition grid: "
            f"{list(invalid.index)}"
        )
    return cells.sort_values(
        ["fiber_length_km", "voa_attenuation_db", "external_signal_present",
         "external_signal_power_dbm"],
        kind="stable", na_position="first",
    ).reset_index(drop=True)


def compute_stratum_effects(cells: pd.DataFrame) -> pd.DataFrame:
    records: list[dict] = []
    for target, unit in TARGET_UNITS.items():
        for stratum, group in cells.groupby("stratum_id", sort=True, observed=True):
            baseline = float(group.loc[group["noise_level"].eq("baseline"), target].iloc[0])
            injected = group.loc[group["external_signal_present"].eq(1)]
            for contrast, selected in [
                ("average_injected_signal_vs_baseline", injected),
                *[
                    (
                        "injected_power_vs_matched_baseline",
                        injected.loc[injected["external_signal_power_dbm"].eq(power)],
                    )
                    for power in NOISE_LEVELS
                ],
            ]:
                injected_mean = float(selected[target].mean())
                degradation = (
                    injected_mean - baseline
                    if target == "qber_reported"
                    else baseline - injected_mean
                )
                records.append({
                    "target": target,
                    "target_unit": unit,
                    "contrast": contrast,
                    "noise_power_dbm": (
                        float(selected["external_signal_power_dbm"].iloc[0])
                        if contrast == "injected_power_vs_matched_baseline"
                        else np.nan
                    ),
                    "stratum_id": str(stratum),
                    "fiber_length_km": float(group["fiber_length_km"].iloc[0]),
                    "voa_attenuation_db": float(group["voa_attenuation_db"].iloc[0]),
                    "baseline_mean": baseline,
                    "injected_mean": injected_mean,
                    "degradation": degradation,
                })
    return pd.DataFrame.from_records(records)


def _moving_block_bootstrap_means(
    values: np.ndarray,
    *,
    replicates: int,
    block_length: int,
    rng: np.random.Generator,
    chunk_size: int = 1_000,
) -> np.ndarray:
    data = np.asarray(values, dtype=float).reshape(-1)
    if not len(data) or not np.isfinite(data).all():
        raise ValueError("moving-block bootstrap requires finite observations")
    length = min(int(block_length), len(data))
    offsets = np.arange(length)
    blocks_per_sample = int(np.ceil(len(data) / length))
    result = np.empty(int(replicates), dtype=float)
    for start in range(0, int(replicates), int(chunk_size)):
        stop = min(start + int(chunk_size), int(replicates))
        starts = rng.integers(
            0, len(data), size=(stop - start, blocks_per_sample)
        )
        indices = (starts[:, :, None] + offsets) % len(data)
        sampled = data[indices].reshape(stop - start, -1)[:, :len(data)]
        result[start:stop] = sampled.mean(axis=1)
    return result


def _cell_bootstrap_means(
    data: pd.DataFrame,
    *,
    target: str,
    block_length: int,
    config: QKDHierarchicalRobustnessConfig,
    seed_offset: int,
) -> dict[tuple[str, str], np.ndarray]:
    rng = np.random.default_rng(config.seed + seed_offset)
    result: dict[tuple[str, str], np.ndarray] = {}
    for (stratum, condition), group in data.groupby(
        ["stratum_id", "noise_level"], sort=True, observed=True
    ):
        ordered = group.sort_values("sample_order", kind="stable")
        result[(str(stratum), str(condition))] = _moving_block_bootstrap_means(
            ordered[target].to_numpy(dtype=float),
            replicates=config.bootstrap_replicates,
            block_length=block_length,
            rng=rng,
        )
    return result


def _effect_matrix(
    strata: pd.DataFrame,
    cell_means: dict[tuple[str, str], np.ndarray],
    *,
    target: str,
    noise_power_dbm: float | None,
) -> np.ndarray:
    rows = []
    for stratum in strata["stratum_id"].astype(str):
        baseline = cell_means[(stratum, "baseline")]
        if noise_power_dbm is None:
            injected = np.mean(
                [cell_means[(stratum, f"{power:g}")] for power in NOISE_LEVELS],
                axis=0,
            )
        else:
            injected = cell_means[(stratum, f"{float(noise_power_dbm):g}")]
        rows.append(injected - baseline if target == "qber_reported" else baseline - injected)
    return np.vstack(rows)


def _stratified_outer_bootstrap(
    effects: np.ndarray,
    strata: pd.DataFrame,
    *,
    seed: int,
) -> np.ndarray:
    replicates = effects.shape[1]
    rng = np.random.default_rng(int(seed))
    replicate_index = np.arange(replicates)[:, None]
    total = np.zeros(replicates, dtype=float)
    total_strata = 0
    for _, group in strata.reset_index().groupby(
        "fiber_length_km", sort=True, observed=True
    ):
        positions = group["index"].to_numpy(dtype=int)
        draws = rng.integers(0, len(positions), size=(replicates, len(positions)))
        group_effects = effects[positions].T
        sampled = group_effects[replicate_index, draws].mean(axis=1)
        total += sampled * len(positions)
        total_strata += len(positions)
    return total / total_strata


def hierarchical_intervals(
    data: pd.DataFrame,
    cells: pd.DataFrame,
    effects: pd.DataFrame,
    *,
    config: QKDHierarchicalRobustnessConfig,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    strata = cells[[
        "stratum_id", "fiber_length_km", "voa_attenuation_db",
    ]].drop_duplicates().sort_values(
        ["fiber_length_km", "voa_attenuation_db"], kind="stable"
    ).reset_index(drop=True)
    primary_records: list[dict] = []
    dose_records: list[dict] = []
    target_names = list(TARGET_UNITS)
    for block_offset, block_length in enumerate(config.block_lengths):
        for target_offset, target in enumerate(target_names):
            cell_means = _cell_bootstrap_means(
                data,
                target=target,
                block_length=block_length,
                config=config,
                seed_offset=block_offset * 10_000 + target_offset * 1_000,
            )
            point = effects.loc[
                effects["target"].eq(target)
                & effects["contrast"].eq("average_injected_signal_vs_baseline")
            ]
            matrix = _effect_matrix(
                strata, cell_means, target=target, noise_power_dbm=None
            )
            distribution = _stratified_outer_bootstrap(
                matrix,
                strata,
                seed=config.seed + 100_000 + block_offset * 1_000 + target_offset,
            )
            primary_alpha = config.familywise_alpha / len(TARGET_UNITS)
            low, high = np.quantile(
                distribution, [primary_alpha / 2.0, 1.0 - primary_alpha / 2.0]
            )
            primary_records.append({
                "target": target,
                "target_unit": TARGET_UNITS[target],
                "contrast": "average_injected_signal_vs_baseline",
                "block_length": int(block_length),
                "matched_strata": int(len(strata)),
                "raw_readings": int(len(data)),
                "point_mean_degradation": float(point["degradation"].mean()),
                "bootstrap_mean_degradation": float(distribution.mean()),
                "ci_low": float(low),
                "ci_high": float(high),
                "familywise_alpha": config.familywise_alpha,
                "family_size": len(TARGET_UNITS),
                "multiple_comparison_correction": "Bonferroni",
                "interval_scope": "conditional on the ten published sweep sessions",
                "positive_conditional_interval": bool(low > 0.0),
            })

            dose_alpha = config.familywise_alpha / (len(TARGET_UNITS) * len(NOISE_LEVELS))
            for power_offset, power in enumerate(NOISE_LEVELS):
                point = effects.loc[
                    effects["target"].eq(target)
                    & effects["contrast"].eq("injected_power_vs_matched_baseline")
                    & effects["noise_power_dbm"].eq(power)
                ]
                matrix = _effect_matrix(
                    strata, cell_means, target=target, noise_power_dbm=power
                )
                distribution = _stratified_outer_bootstrap(
                    matrix,
                    strata,
                    seed=(
                        config.seed + 200_000 + block_offset * 10_000
                        + target_offset * 1_000 + power_offset
                    ),
                )
                low, high = np.quantile(
                    distribution, [dose_alpha / 2.0, 1.0 - dose_alpha / 2.0]
                )
                dose_records.append({
                    "target": target,
                    "target_unit": TARGET_UNITS[target],
                    "contrast": "injected_power_vs_matched_baseline",
                    "noise_power_dbm": float(power),
                    "block_length": int(block_length),
                    "matched_strata": int(len(strata)),
                    "raw_readings": int(len(data)),
                    "point_mean_degradation": float(point["degradation"].mean()),
                    "bootstrap_mean_degradation": float(distribution.mean()),
                    "ci_low": float(low),
                    "ci_high": float(high),
                    "familywise_alpha": config.familywise_alpha,
                    "family_size": len(TARGET_UNITS) * len(NOISE_LEVELS),
                    "multiple_comparison_correction": "Bonferroni",
                    "interval_scope": "conditional on the ten published sweep sessions",
                    "positive_conditional_interval": bool(low > 0.0),
                })
    return pd.DataFrame(primary_records), pd.DataFrame(dose_records)


def influence_analysis(effects: pd.DataFrame) -> pd.DataFrame:
    primary = effects.loc[
        effects["contrast"].eq("average_injected_signal_vs_baseline")
    ]
    records: list[dict] = []
    for target, group in primary.groupby("target", sort=True, observed=True):
        for stratum in sorted(group["stratum_id"].unique()):
            retained = group.loc[~group["stratum_id"].eq(stratum)]
            records.append({
                "target": target,
                "exclusion_type": "leave_one_stratum_out",
                "excluded_unit": str(stratum),
                "retained_units": int(len(retained)),
                "mean_degradation": float(retained["degradation"].mean()),
                "positive_direction": bool(retained["degradation"].mean() > 0.0),
            })
        for distance in sorted(group["fiber_length_km"].unique()):
            retained = group.loc[group["fiber_length_km"].ne(distance)]
            records.append({
                "target": target,
                "exclusion_type": "leave_one_fibre_configuration_out",
                "excluded_unit": f"{distance:g}km",
                "retained_units": int(len(retained)),
                "mean_degradation": float(retained["degradation"].mean()),
                "positive_direction": bool(retained["degradation"].mean() > 0.0),
            })
    return pd.DataFrame.from_records(records)


def run_qkd_hierarchical_robustness_campaign(
    dataset_dir: str | Path,
    *,
    config: QKDHierarchicalRobustnessConfig = QKDHierarchicalRobustnessConfig(),
) -> QKDHierarchicalRobustnessResult:
    config.validate()
    source = QKDCoexistenceSource(dataset_dir)
    raw = source.read()
    provenance = raw.attrs["telemetry_provenance"]
    data = prepare_raw_readings(raw)
    design = audit_campaign_design(data)
    cells = summarize_raw_cells(data)
    effects = compute_stratum_effects(cells)
    primary, dose = hierarchical_intervals(
        data, cells, effects, config=config
    )
    influence = influence_analysis(effects)

    robust_targets = primary.groupby("target", observed=True)[
        "positive_conditional_interval"
    ].all()
    influence_stable = influence.groupby("target", observed=True)[
        "positive_direction"
    ].all()
    conditional_robust = bool(robust_targets.all() and influence_stable.all())
    session_identified = bool(design["session_effect_separable"].iloc[0])
    conclusion = (
        "Conditional on the ten published sweep sessions, both mean QKD degradation "
        "directions remain positive across the hierarchical block-bootstrap and deletion "
        "sensitivities. However, every distance/condition combination has only one session, "
        "so coexistence and session effects cannot be separated and no campaign-clustered "
        "confirmatory p-value is available."
        if conditional_robust and not session_identified
        else (
            "The measured degradation is not stable across every hierarchical/deletion "
            "sensitivity; the aggregate effect requires qualification."
        )
    )
    report = {
        "study": "IrelandQCI raw-reading hierarchical robustness audit",
        "protocol_version": "1.0.0",
        "status": "post-discovery conditional sensitivity analysis; not confirmatory",
        "question": (
            "Does the measured coexistence degradation remain directionally stable after "
            "accounting for between-VOA heterogeneity and within-cell serial dependence?"
        ),
        "experimental_structure": {
            "raw_readings": int(len(data)),
            "controlled_cells": int(len(cells)),
            "physical_strata": int(cells["stratum_id"].nunique()),
            "campaigns": int(data["campaign_id"].nunique()),
            "condition_session_aliased": bool(design["condition_session_aliased"].iloc[0]),
            "session_effect_separable": session_identified,
        },
        "inference": {
            "outer_resampling": "VOA strata resampled within each fibre configuration",
            "inner_resampling": (
                "circular moving blocks within each contiguous campaign/VOA/condition cell"
            ),
            "block_lengths": list(config.block_lengths),
            "primary_family_size": len(TARGET_UNITS),
            "dose_family_size": len(TARGET_UNITS) * len(NOISE_LEVELS),
            "scope": "conditional on the ten observed sweep sessions",
        },
        "primary_result": {
            "all_targets_positive_for_every_block_length": bool(robust_targets.all()),
            "all_deletion_sensitivities_directionally_positive": bool(
                influence_stable.all()
            ),
            "conditional_measurement_robustness": conditional_robust,
            "campaign_clustered_confirmation": False,
        },
        "scientific_conclusion": conclusion,
        "not_claimed": [
            "Repeated readings are not treated as independent experimental units.",
            "The bootstrap does not remove a session-wide offset shared by a sweep file.",
            "Condition and session are aliased within distance because no session is repeated.",
            "The source does not document randomized acquisition order or timestamps.",
            "QBER and SKR are not Bell-state fidelity and no future prediction is tested.",
        ],
        "configuration": asdict(config),
        "git": git_state(),
    }
    integrity = {
        "dataset_quality": source.quality_report(raw),
        "provenance": provenance,
        "raw_file_sha256": provenance["file_sha256"],
        "synthetic_values_substituted": False,
    }
    return QKDHierarchicalRobustnessResult(
        report=report,
        design_audit=design,
        raw_cell_summary=cells,
        stratum_effects=effects,
        primary_intervals=primary,
        dose_intervals=dose,
        influence_analysis=influence,
        input_integrity=integrity,
    )


def write_qkd_hierarchical_robustness_artifacts(
    result: QKDHierarchicalRobustnessResult,
    output_dir: str | Path,
) -> dict:
    directory = Path(output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    tables = {
        "design_audit.csv": result.design_audit,
        "raw_cell_summary.csv": result.raw_cell_summary,
        "stratum_effects.csv": result.stratum_effects,
        "hierarchical_primary_intervals.csv": result.primary_intervals,
        "hierarchical_dose_intervals.csv": result.dose_intervals,
        "influence_analysis.csv": result.influence_analysis,
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
    "QKDHierarchicalRobustnessConfig",
    "QKDHierarchicalRobustnessResult",
    "audit_campaign_design",
    "compute_stratum_effects",
    "hierarchical_intervals",
    "influence_analysis",
    "prepare_raw_readings",
    "run_qkd_hierarchical_robustness_campaign",
    "summarize_raw_cells",
    "write_qkd_hierarchical_robustness_artifacts",
]
