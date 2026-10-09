"""Cross-platform replication of measured classical/QKD coexistence effects.

This module combines study-level, sign-oriented effects from IrelandQCI and
Bristol HCF.  It never joins raw observations or treats the two experimental
designs as interchangeable.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import itertools
import json
from pathlib import Path

import numpy as np
import pandas as pd

from quantum_twin.evaluation.optical_campaign import git_state, sha256_file


IRELAND_STUDY_ID = "irelandqci-toshiba-controlled-injection"
BRISTOL_STUDY_ID = "bristol-hcf-adjacent-switch-did"


@dataclass(frozen=True)
class CrossPlatformQKDReplicationConfig:
    familywise_alpha: float = 0.05
    verify_input_manifests: bool = True

    def validate(self) -> None:
        if not 0.0 < self.familywise_alpha < 1.0:
            raise ValueError("familywise_alpha must be between 0 and 1")


@dataclass
class CrossPlatformQKDReplicationResult:
    report: dict
    unit_effects: pd.DataFrame
    study_summaries: pd.DataFrame
    replication_results: pd.DataFrame
    random_effects_summary: pd.DataFrame
    leave_one_study_out: pd.DataFrame
    input_integrity: dict


def _read_required_csv(directory: Path, name: str, columns: set[str]) -> pd.DataFrame:
    path = directory / name
    if not path.is_file():
        raise FileNotFoundError(f"required campaign artifact not found: {path}")
    frame = pd.read_csv(path)
    missing = sorted(columns.difference(frame.columns))
    if missing:
        raise ValueError(f"{path} is missing columns: {missing}")
    return frame


def _verify_required_artifacts(directory: Path, required: tuple[str, ...]) -> dict:
    manifest_path = directory / "artifact_manifest.json"
    if not manifest_path.is_file():
        raise FileNotFoundError(f"artifact manifest not found: {manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    entries = {entry["file"]: entry for entry in manifest.get("artifacts", [])}
    checks: dict[str, dict] = {}
    for name in required:
        path = directory / name
        entry = entries.get(name)
        if entry is None:
            raise ValueError(f"{name} is absent from {manifest_path}")
        observed = sha256_file(path)
        expected = str(entry.get("sha256", ""))
        matches = observed == expected
        checks[name] = {
            "expected_sha256": expected,
            "observed_sha256": observed,
            "matches": matches,
        }
        if not matches:
            raise ValueError(f"artifact hash mismatch for {path}")
    return checks


def _ireland_unit_effects(cells: pd.DataFrame) -> pd.DataFrame:
    records: list[dict] = []
    target_specs = {
        "qber_reported": ("qber", 1.0, "publisher QBER scale; ratio or percent ambiguous"),
        "log1p_secure_key_rate": ("log1p_secure_key_rate", -1.0, "log1p(bit/s)"),
    }
    for target, (family, sign, unit) in target_specs.items():
        baseline = cells.loc[cells["noise_level"].eq("baseline"), [
            "stratum_id", target,
        ]].rename(columns={target: "baseline_value"})
        if baseline["stratum_id"].duplicated().any():
            raise ValueError("IrelandQCI cells contain duplicate baselines within a stratum")
        injected = (
            cells.loc[~cells["noise_level"].eq("baseline")]
            .groupby("stratum_id", observed=True, as_index=False)[target]
            .mean()
            .rename(columns={target: "exposed_value"})
        )
        matched = baseline.merge(injected, on="stratum_id", validate="one_to_one")
        if len(matched) != cells["stratum_id"].nunique():
            raise ValueError("IrelandQCI has an incomplete baseline/injection stratum")
        matched["degradation"] = sign * (
            matched["exposed_value"] - matched["baseline_value"]
        )
        for row in matched.to_dict(orient="records"):
            records.append({
                "study_id": IRELAND_STUDY_ID,
                "outcome_family": family,
                "study_target": target,
                "unit_id": str(row["stratum_id"]),
                "experimental_unit": "one fibre/VOA stratum",
                "design": "mean of four injected powers versus matched baseline",
                "target_unit": unit,
                "degradation": float(row["degradation"]),
            })
    return pd.DataFrame.from_records(records)


def _bristol_unit_effects(pair_effects: pd.DataFrame) -> pd.DataFrame:
    target_families = {
        "qber_measured": "qber",
        "log1p_secure_key_rate": "log1p_secure_key_rate",
    }
    selected = pair_effects.loc[pair_effects["target"].isin(target_families)].copy()
    if selected.duplicated(["target", "switch_pair_index"]).any():
        raise ValueError("Bristol artifacts contain duplicate target/switch-pair effects")
    records: list[dict] = []
    for row in selected.to_dict(orient="records"):
        records.append({
            "study_id": BRISTOL_STUDY_ID,
            "outcome_family": target_families[str(row["target"])],
            "study_target": str(row["target"]),
            "unit_id": str(int(row["switch_pair_index"])),
            "experimental_unit": "one complete adjacent On/Off switch pair",
            "design": "exposed-minus-control difference-in-differences",
            "target_unit": str(row["target_unit"]),
            "degradation": float(row["difference_in_differences_degradation"]),
        })
    return pd.DataFrame.from_records(records)


def _validate_against_primary(
    unit_effects: pd.DataFrame,
    ireland_primary: pd.DataFrame,
    bristol_primary: pd.DataFrame,
) -> None:
    specifications = (
        (
            IRELAND_STUDY_ID, "qber", ireland_primary, "qber_reported",
            "mean_degradation", "matched_strata",
        ),
        (
            IRELAND_STUDY_ID, "log1p_secure_key_rate", ireland_primary,
            "log1p_secure_key_rate", "mean_degradation", "matched_strata",
        ),
        (
            BRISTOL_STUDY_ID, "qber", bristol_primary, "qber_measured",
            "mean_did_degradation", "complete_switch_pairs",
        ),
        (
            BRISTOL_STUDY_ID, "log1p_secure_key_rate", bristol_primary,
            "log1p_secure_key_rate", "mean_did_degradation", "complete_switch_pairs",
        ),
    )
    for study, family, primary, target, mean_column, count_column in specifications:
        observed = unit_effects.loc[
            unit_effects["study_id"].eq(study)
            & unit_effects["outcome_family"].eq(family),
            "degradation",
        ].to_numpy(dtype=float)
        reference = primary.loc[primary["target"].eq(target)]
        if len(reference) != 1:
            raise ValueError(f"expected one primary row for {study}/{target}")
        if len(observed) != int(reference[count_column].iloc[0]):
            raise ValueError(f"unit count disagrees with primary artifact for {study}/{target}")
        if not np.isclose(
            observed.mean(), float(reference[mean_column].iloc[0]), rtol=1e-10, atol=1e-12
        ):
            raise ValueError(f"unit mean disagrees with primary artifact for {study}/{target}")


def _exact_one_sided_sign_flip(values: np.ndarray) -> float:
    data = np.asarray(values, dtype=float)
    if not 1 <= len(data) <= 20:
        raise ValueError("exact sign-flip replication audit requires 1..20 study units")
    observed = float(data.mean())
    tolerance = np.finfo(float).eps * max(1.0, abs(observed)) * 8.0
    exceedances = 0
    total = 0
    for signs in itertools.product((-1.0, 1.0), repeat=len(data)):
        statistic = float(np.dot(data, np.asarray(signs)) / len(data))
        exceedances += int(statistic >= observed - tolerance)
        total += 1
    return float(exceedances / total)


def _standardized_mean_effect(values: np.ndarray) -> tuple[float, float]:
    data = np.asarray(values, dtype=float)
    standard_deviation = float(data.std(ddof=1))
    if not np.isfinite(standard_deviation) or standard_deviation <= 0.0:
        raise ValueError("study-unit effects need non-zero dispersion for standardization")
    n = len(data)
    correction = 1.0 - 3.0 / (4.0 * n - 5.0)
    hedges_g = correction * float(data.mean()) / standard_deviation
    variance = 1.0 / n + hedges_g ** 2 / (2.0 * (n - 1.0))
    return float(hedges_g), float(variance)


def _source_primary_fields(
    study: str, family: str, ireland_primary: pd.DataFrame, bristol_primary: pd.DataFrame
) -> tuple[float, bool]:
    if study == IRELAND_STUDY_ID:
        target = "qber_reported" if family == "qber" else "log1p_secure_key_rate"
        row = ireland_primary.loc[ireland_primary["target"].eq(target)].iloc[0]
        return float(row["corrected_p"]), bool(row["significant_measured_quantum_degradation"])
    target = "qber_measured" if family == "qber" else "log1p_secure_key_rate"
    row = bristol_primary.loc[bristol_primary["target"].eq(target)].iloc[0]
    return float(row["bonferroni_adjusted_p_value"]), bool(row["corrected_positive_evidence"])


def _summarize_studies(
    unit_effects: pd.DataFrame,
    ireland_primary: pd.DataFrame,
    bristol_primary: pd.DataFrame,
) -> pd.DataFrame:
    records: list[dict] = []
    for (study, family), group in unit_effects.groupby(
        ["study_id", "outcome_family"], sort=True, observed=True
    ):
        values = group["degradation"].to_numpy(dtype=float)
        hedges_g, variance = _standardized_mean_effect(values)
        source_p, source_positive = _source_primary_fields(
            str(study), str(family), ireland_primary, bristol_primary
        )
        records.append({
            "study_id": str(study),
            "outcome_family": str(family),
            "study_units": int(len(values)),
            "target_unit": str(group["target_unit"].iloc[0]),
            "mean_degradation": float(values.mean()),
            "median_degradation": float(np.median(values)),
            "positive_unit_fraction": float(np.mean(values > 0.0)),
            "one_sided_exact_sign_flip_p": _exact_one_sided_sign_flip(values),
            "hedges_g_standardized_degradation": hedges_g,
            "hedges_g_variance": variance,
            "source_family_adjusted_p": source_p,
            "source_corrected_positive": source_positive,
            "inference_status": (
                "study-level sensitivity inference; acquisition was not randomized"
            ),
        })
    return pd.DataFrame.from_records(records)


def _holm_adjust(p_values: np.ndarray) -> np.ndarray:
    values = np.asarray(p_values, dtype=float)
    order = np.argsort(values, kind="stable")
    adjusted = np.empty(len(values), dtype=float)
    running = 0.0
    for rank, index in enumerate(order):
        candidate = min(1.0, (len(values) - rank) * values[index])
        running = max(running, candidate)
        adjusted[index] = running
    return adjusted


def _random_effects(study_summaries: pd.DataFrame) -> pd.DataFrame:
    records: list[dict] = []
    for family, group in study_summaries.groupby(
        "outcome_family", sort=True, observed=True
    ):
        effects = group["hedges_g_standardized_degradation"].to_numpy(float)
        variances = group["hedges_g_variance"].to_numpy(float)
        fixed_weights = 1.0 / variances
        fixed_mean = float(np.sum(fixed_weights * effects) / np.sum(fixed_weights))
        q_statistic = float(np.sum(fixed_weights * np.square(effects - fixed_mean)))
        degrees_freedom = len(effects) - 1
        c_value = float(
            np.sum(fixed_weights)
            - np.sum(np.square(fixed_weights)) / np.sum(fixed_weights)
        )
        tau_squared = max(0.0, (q_statistic - degrees_freedom) / c_value)
        random_weights = 1.0 / (variances + tau_squared)
        pooled = float(np.sum(random_weights * effects) / np.sum(random_weights))
        standard_error = float(np.sqrt(1.0 / np.sum(random_weights)))
        i_squared = (
            max(0.0, (q_statistic - degrees_freedom) / q_statistic) * 100.0
            if q_statistic > 0.0 else 0.0
        )
        records.append({
            "outcome_family": str(family),
            "studies": int(len(effects)),
            "pooled_hedges_g": pooled,
            "normal_ci_low": pooled - 1.96 * standard_error,
            "normal_ci_high": pooled + 1.96 * standard_error,
            "tau_squared_der_simonian_laird": tau_squared,
            "q_statistic": q_statistic,
            "i_squared_percent": float(i_squared),
            "interpretation_status": (
                "exploratory magnitude summary only; heterogeneity is unstable with two studies"
            ),
        })
    return pd.DataFrame.from_records(records)


def _replication_results(
    summaries: pd.DataFrame,
    random_effects: pd.DataFrame,
    *,
    alpha: float,
) -> pd.DataFrame:
    records: list[dict] = []
    for family, group in summaries.groupby("outcome_family", sort=True, observed=True):
        if set(group["study_id"]) != {IRELAND_STUDY_ID, BRISTOL_STUDY_ID}:
            raise ValueError(f"replication requires both studies for {family}")
        conjunction_p = float(group["one_sided_exact_sign_flip_p"].max())
        meta = random_effects.loc[random_effects["outcome_family"].eq(family)].iloc[0]
        records.append({
            "outcome_family": str(family),
            "study_count": 2,
            "both_study_means_positive": bool((group["mean_degradation"] > 0.0).all()),
            "both_source_primary_results_corrected_positive": bool(
                group["source_corrected_positive"].all()
            ),
            "partial_conjunction_raw_p": conjunction_p,
            "minimum_positive_unit_fraction": float(
                group["positive_unit_fraction"].min()
            ),
            "pooled_hedges_g_exploratory": float(meta["pooled_hedges_g"]),
            "random_effects_ci_low": float(meta["normal_ci_low"]),
            "random_effects_ci_high": float(meta["normal_ci_high"]),
            "heterogeneity_i_squared_percent": float(meta["i_squared_percent"]),
        })
    result = pd.DataFrame.from_records(records)
    result["holm_adjusted_partial_conjunction_p"] = _holm_adjust(
        result["partial_conjunction_raw_p"].to_numpy(float)
    )
    result["replicated_directional_evidence"] = (
        result["both_study_means_positive"]
        & result["both_source_primary_results_corrected_positive"]
        & result["holm_adjusted_partial_conjunction_p"].le(alpha)
    )
    result["random_effects_ci_excludes_zero"] = result[
        "random_effects_ci_low"
    ].gt(0.0)
    result["common_magnitude_supported"] = False
    result["direction_replicates_but_magnitude_not_poolable"] = (
        result["replicated_directional_evidence"]
        & ~result["random_effects_ci_excludes_zero"]
    )
    result["claim_scope"] = (
        "same-direction measured coexistence degradation in both public studies"
    )
    result["not_a_wdm_forecast"] = True
    return result


def _leave_one_study_out(summaries: pd.DataFrame) -> pd.DataFrame:
    """Expose that replication is no longer identifiable after omitting one study."""
    records: list[dict] = []
    for family, group in summaries.groupby("outcome_family", sort=True, observed=True):
        for omitted_study in sorted(group["study_id"]):
            retained = group.loc[group["study_id"].ne(omitted_study)]
            if len(retained) != 1:
                raise ValueError("leave-one-study-out expects exactly two source studies")
            row = retained.iloc[0]
            records.append({
                "outcome_family": str(family),
                "omitted_study_id": str(omitted_study),
                "retained_study_id": str(row["study_id"]),
                "retained_study_units": int(row["study_units"]),
                "retained_mean_degradation": float(row["mean_degradation"]),
                "retained_hedges_g": float(row["hedges_g_standardized_degradation"]),
                "retained_one_sided_exact_p": float(row["one_sided_exact_sign_flip_p"]),
                "replication_identifiable": False,
                "interpretation": (
                    "one study remains; direction can be described but replication cannot"
                ),
            })
    return pd.DataFrame.from_records(records)


def run_cross_platform_qkd_replication_campaign(
    ireland_campaign_dir: str | Path,
    bristol_campaign_dir: str | Path,
    *,
    config: CrossPlatformQKDReplicationConfig = CrossPlatformQKDReplicationConfig(),
) -> CrossPlatformQKDReplicationResult:
    config.validate()
    ireland_directory = Path(ireland_campaign_dir)
    bristol_directory = Path(bristol_campaign_dir)
    ireland_required = ("experimental_cells.csv", "primary_matched_contrasts.csv")
    bristol_required = ("switch_pair_effects.csv", "primary_results.csv")
    integrity = {
        "verification_enabled": config.verify_input_manifests,
        "ireland": (
            _verify_required_artifacts(ireland_directory, ireland_required)
            if config.verify_input_manifests else {}
        ),
        "bristol": (
            _verify_required_artifacts(bristol_directory, bristol_required)
            if config.verify_input_manifests else {}
        ),
    }
    ireland_cells = _read_required_csv(
        ireland_directory, "experimental_cells.csv",
        {"stratum_id", "noise_level", "qber_reported", "log1p_secure_key_rate"},
    )
    ireland_primary = _read_required_csv(
        ireland_directory, "primary_matched_contrasts.csv",
        {
            "target", "matched_strata", "mean_degradation", "corrected_p",
            "significant_measured_quantum_degradation",
        },
    )
    bristol_pairs = _read_required_csv(
        bristol_directory, "switch_pair_effects.csv",
        {
            "target", "target_unit", "switch_pair_index",
            "difference_in_differences_degradation",
        },
    )
    bristol_primary = _read_required_csv(
        bristol_directory, "primary_results.csv",
        {
            "target", "complete_switch_pairs", "mean_did_degradation",
            "bonferroni_adjusted_p_value", "corrected_positive_evidence",
        },
    )
    unit_effects = pd.concat(
        [_ireland_unit_effects(ireland_cells), _bristol_unit_effects(bristol_pairs)],
        ignore_index=True,
    )
    _validate_against_primary(
        unit_effects, ireland_primary=ireland_primary, bristol_primary=bristol_primary
    )
    summaries = _summarize_studies(unit_effects, ireland_primary, bristol_primary)
    random_effects = _random_effects(summaries)
    replication = _replication_results(
        summaries, random_effects, alpha=config.familywise_alpha
    )
    leave_one_study_out = _leave_one_study_out(summaries)
    report = {
        "study": "cross-platform replication of measured QKD coexistence degradation",
        "configuration": asdict(config),
        "input_integrity": integrity,
        "claim_scope": (
            "Whether QBER and secure-key-rate degradation have the same positive direction "
            "in two independent real-hardware coexistence datasets."
        ),
        "analysis_level": (
            "study-specific experimental-unit effects; no raw rows are joined across datasets"
        ),
        "replication_test": (
            "maximum of the two one-sided exact sign-flip sensitivity p-values, then Holm "
            "correction across QBER and log1p(SKR)"
        ),
        "replicated_outcome_count": int(
            replication["replicated_directional_evidence"].sum()
        ),
        "outcome_count": int(len(replication)),
        "high_heterogeneity_outcome_count": int(
            replication["heterogeneity_i_squared_percent"].ge(75.0).sum()
        ),
        "not_claimed": [
            "The experimental designs estimate identical causal magnitudes.",
            "Classical exposure was randomized in either public dataset.",
            "A pooled standardized effect is stable with only two studies.",
            "Time-varying WDM telemetry predicts a future quantum target.",
            "QBER or secure key rate is Bell-state fidelity.",
        ],
        "scientific_interpretation": (
            "Positive partial-conjunction results support cross-platform directional "
            "replication of a measured coexistence effect, while high heterogeneity shows "
            "that its magnitude is not transportable between these platforms. This "
            "strengthens the physical middle link of the thesis, not its prospective WDM "
            "forecasting endpoint."
        ),
        "git": git_state(),
    }
    return CrossPlatformQKDReplicationResult(
        report=report,
        unit_effects=unit_effects,
        study_summaries=summaries,
        replication_results=replication,
        random_effects_summary=random_effects,
        leave_one_study_out=leave_one_study_out,
        input_integrity=integrity,
    )


def write_cross_platform_qkd_replication_artifacts(
    result: CrossPlatformQKDReplicationResult, output_dir: str | Path
) -> dict:
    directory = Path(output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    artifacts: list[Path] = []
    for name, frame in (
        ("study_unit_effects.csv", result.unit_effects),
        ("study_summaries.csv", result.study_summaries),
        ("replication_results.csv", result.replication_results),
        ("random_effects_summary.csv", result.random_effects_summary),
        ("leave_one_study_out.csv", result.leave_one_study_out),
    ):
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
    "BRISTOL_STUDY_ID",
    "CrossPlatformQKDReplicationConfig",
    "CrossPlatformQKDReplicationResult",
    "IRELAND_STUDY_ID",
    "run_cross_platform_qkd_replication_campaign",
    "write_cross_platform_qkd_replication_artifacts",
]
