"""Robustness and falsification audits for the Bristol HCF coexistence study.

The analyses in this module are sensitivity analyses.  They do not turn the
deterministic Bristol On/Off sequence into a randomized experiment, and they do
not test future prediction from WDM telemetry.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import itertools
import json
from pathlib import Path

import numpy as np
import pandas as pd

from quantum_twin.evaluation.bristol_hcf_coexistence import (
    BristolHCFCoexistenceConfig,
    BristolHCFCoexistenceResult,
    compute_link_pair_effects,
    compute_switch_pair_effects,
    run_bristol_hcf_coexistence_campaign,
)
from quantum_twin.evaluation.optical_campaign import git_state, sha256_file
from quantum_twin.quantum.bristol_hcf_coexistence import (
    BRISTOL_HCF_ALICE_LINKS,
    BRISTOL_HCF_LINKS,
)


@dataclass(frozen=True)
class BristolHCFRobustnessConfig:
    bootstrap_replicates: int = 50_000
    familywise_alpha: float = 0.05
    seed: int = 42

    def validate(self) -> None:
        BristolHCFCoexistenceConfig(
            bootstrap_replicates=self.bootstrap_replicates,
            familywise_alpha=self.familywise_alpha,
            seed=self.seed,
        ).validate()

    def primary_config(self) -> BristolHCFCoexistenceConfig:
        self.validate()
        return BristolHCFCoexistenceConfig(
            bootstrap_replicates=self.bootstrap_replicates,
            familywise_alpha=self.familywise_alpha,
            seed=self.seed,
        )


@dataclass
class BristolHCFRobustnessResult:
    report: dict
    primary: BristolHCFCoexistenceResult
    segment_results: pd.DataFrame
    leave_one_segment_out: pd.DataFrame
    leave_one_link_out: pd.DataFrame
    leave_one_pair_out: pd.DataFrame
    spatial_exposure_placebos: pd.DataFrame
    robustness_summary: pd.DataFrame
    periodicity_audit: dict


def _pairs_with_segments(
    telemetry: pd.DataFrame, switch_pair_effects: pd.DataFrame
) -> pd.DataFrame:
    chunk_segments = telemetry[
        ["relative_chunk_index", "sequence_segment"]
    ].drop_duplicates()
    if chunk_segments["relative_chunk_index"].duplicated().any():
        raise ValueError("a published chunk belongs to more than one sequence segment")
    result = switch_pair_effects.merge(
        chunk_segments,
        left_on="on_chunk_index",
        right_on="relative_chunk_index",
        how="left",
        validate="many_to_one",
    ).drop(columns="relative_chunk_index")
    if result["sequence_segment"].isna().any():
        raise ValueError("could not map every complete switch pair to a sequence segment")
    result["sequence_segment"] = result["sequence_segment"].astype(int)
    return result


def compute_segment_results(pair_effects: pd.DataFrame) -> pd.DataFrame:
    """Estimate the DiD separately in every contiguous published segment."""
    return (
        pair_effects.groupby(["target", "target_unit", "sequence_segment"], observed=True)
        ["difference_in_differences_degradation"]
        .agg(complete_switch_pairs="size", mean_did_degradation="mean",
             median_did_degradation="median", minimum_pair_effect="min",
             maximum_pair_effect="max")
        .reset_index()
        .assign(inference_status="descriptive sensitivity analysis")
    )


def compute_leave_one_segment_out(pair_effects: pd.DataFrame) -> pd.DataFrame:
    records: list[dict] = []
    for target, target_frame in pair_effects.groupby("target", sort=True, observed=True):
        for segment in sorted(target_frame["sequence_segment"].unique()):
            retained = target_frame.loc[target_frame["sequence_segment"].ne(segment)]
            if retained.empty:
                continue
            records.append({
                "target": str(target),
                "omitted_sequence_segment": int(segment),
                "retained_segments": int(retained["sequence_segment"].nunique()),
                "complete_switch_pairs": int(len(retained)),
                "mean_did_degradation": float(
                    retained["difference_in_differences_degradation"].mean()
                ),
                "inference_status": "descriptive sensitivity analysis",
            })
    return pd.DataFrame.from_records(records)


def _complete_link_pair_groups(link_pair_effects: pd.DataFrame) -> list[pd.DataFrame]:
    complete: list[pd.DataFrame] = []
    expected = set(BRISTOL_HCF_LINKS)
    for _, group in link_pair_effects.groupby(
        ["target", "switch_pair_index"], sort=True, observed=True
    ):
        if set(group["link_id"].astype(str)) == expected:
            complete.append(group)
    return complete


def compute_leave_one_link_out(link_pair_effects: pd.DataFrame) -> pd.DataFrame:
    """Recompute the exposed-control contrast after removing each physical link."""
    complete_groups = _complete_link_pair_groups(link_pair_effects)
    records: list[dict] = []
    targets = sorted(link_pair_effects["target"].astype(str).unique())
    for target in targets:
        target_groups = [g for g in complete_groups if str(g["target"].iloc[0]) == target]
        for omitted_link in BRISTOL_HCF_LINKS:
            effects: list[float] = []
            for group in target_groups:
                retained = group.loc[group["link_id"].ne(omitted_link)]
                exposed = retained.loc[retained["alice_link"], "link_degradation"]
                controls = retained.loc[~retained["alice_link"], "link_degradation"]
                effects.append(float(exposed.mean() - controls.mean()))
            records.append({
                "target": target,
                "omitted_link": omitted_link,
                "omitted_group": (
                    "documented_exposed" if omitted_link in BRISTOL_HCF_ALICE_LINKS
                    else "contemporaneous_control"
                ),
                "complete_switch_pairs": int(len(effects)),
                "mean_did_degradation": float(np.mean(effects)),
                "inference_status": "descriptive influence analysis",
            })
    return pd.DataFrame.from_records(records)


def compute_leave_one_pair_out(pair_effects: pd.DataFrame) -> pd.DataFrame:
    records: list[dict] = []
    for target, target_frame in pair_effects.groupby("target", sort=True, observed=True):
        for omitted_pair in target_frame["switch_pair_index"]:
            retained = target_frame.loc[target_frame["switch_pair_index"].ne(omitted_pair)]
            if retained.empty:
                continue
            records.append({
                "target": str(target),
                "omitted_switch_pair_index": int(omitted_pair),
                "complete_switch_pairs": int(len(retained)),
                "mean_did_degradation": float(
                    retained["difference_in_differences_degradation"].mean()
                ),
                "inference_status": "descriptive jackknife influence analysis",
            })
    return pd.DataFrame.from_records(records)


def compute_spatial_exposure_placebos(link_pair_effects: pd.DataFrame) -> pd.DataFrame:
    """Enumerate every three-versus-three spatial exposure assignment.

    The documented Alice grouping is fixed by topology, not randomized.  The
    resulting assignment tail fraction is therefore a sensitivity ranking and
    must not be reported as a randomization-test p-value.
    """
    records: list[dict] = []
    complete_groups = _complete_link_pair_groups(link_pair_effects)
    documented = frozenset(BRISTOL_HCF_ALICE_LINKS)
    assignments = [frozenset(group) for group in itertools.combinations(BRISTOL_HCF_LINKS, 3)]
    for target in sorted(link_pair_effects["target"].astype(str).unique()):
        target_groups = [g for g in complete_groups if str(g["target"].iloc[0]) == target]
        target_records: list[dict] = []
        for assignment in assignments:
            effects: list[float] = []
            for group in target_groups:
                assigned = group["link_id"].isin(assignment)
                effects.append(float(
                    group.loc[assigned, "link_degradation"].mean()
                    - group.loc[~assigned, "link_degradation"].mean()
                ))
            target_records.append({
                "target": target,
                "assigned_exposed_links": ",".join(sorted(assignment)),
                "is_documented_assignment": bool(assignment == documented),
                "complete_switch_pairs": int(len(effects)),
                "mean_did_degradation": float(np.mean(effects)),
            })
        observed = next(
            row["mean_did_degradation"]
            for row in target_records if row["is_documented_assignment"]
        )
        tolerance = np.finfo(float).eps * max(1.0, abs(observed)) * 8.0
        two_sided_tail = float(np.mean([
            abs(row["mean_did_degradation"]) >= abs(observed) - tolerance
            for row in target_records
        ]))
        ordered_values = sorted(
            (row["mean_did_degradation"] for row in target_records), reverse=True
        )
        rank = 1 + sum(value > observed + tolerance for value in ordered_values)
        for row in target_records:
            row.update({
                "documented_assignment_one_sided_rank": int(rank),
                "assignment_count": int(len(assignments)),
                "documented_assignment_two_sided_tail_fraction": two_sided_tail,
                "inference_status": (
                    "spatial sensitivity ranking only; exposure assignment was not randomized"
                ),
            })
        records.extend(target_records)
    return pd.DataFrame.from_records(records)


def audit_switch_periodicity(telemetry: pd.DataFrame) -> dict:
    """Show whether a temporal label-permutation control is identifiable."""
    sequence = telemetry[
        ["sequence_segment", "relative_chunk_index", "coexistence_active"]
    ].drop_duplicates().sort_values(["sequence_segment", "relative_chunk_index"])
    lag_records: list[dict] = []
    for lag in (1, 2):
        comparisons: list[tuple[int, int]] = []
        for _, segment in sequence.groupby("sequence_segment", sort=True, observed=True):
            values = segment["coexistence_active"].to_numpy(dtype=int)
            comparisons.extend(zip(values[lag:], values[:-lag]))
        same = np.mean([current == previous for current, previous in comparisons])
        flipped = np.mean([current != previous for current, previous in comparisons])
        lag_records.append({
            "lag": lag,
            "comparisons": int(len(comparisons)),
            "same_state_fraction": float(same),
            "flipped_state_fraction": float(flipped),
        })
    lag_one = lag_records[0]
    lag_two = lag_records[1]
    deterministic_alternation = bool(
        np.isclose(lag_one["flipped_state_fraction"], 1.0)
        and np.isclose(lag_two["same_state_fraction"], 1.0)
    )
    return {
        "sequence_segments": int(sequence["sequence_segment"].nunique()),
        "observed_chunks": int(len(sequence)),
        "lag_audit": lag_records,
        "deterministic_alternation_detected": deterministic_alternation,
        "temporal_permutation_control_identifiable": not deterministic_alternation,
        "reason": (
            "Within every contiguous segment, lag 1 swaps all On/Off labels and lag 2 "
            "reproduces them. A shifted-label placebo is therefore the treatment contrast "
            "or the original labels, not an independent temporal falsification."
            if deterministic_alternation else
            "The observed sequence is not perfectly period-2 within segments."
        ),
    }


def _range_for(frame: pd.DataFrame, target: str) -> tuple[float, float]:
    values = frame.loc[frame["target"].eq(target), "mean_did_degradation"].to_numpy(float)
    if not len(values):
        return float("nan"), float("nan")
    return float(values.min()), float(values.max())


def summarize_robustness(
    primary: BristolHCFCoexistenceResult,
    segment_results: pd.DataFrame,
    leave_one_segment_out: pd.DataFrame,
    leave_one_link_out: pd.DataFrame,
    leave_one_pair_out: pd.DataFrame,
    spatial_placebos: pd.DataFrame,
) -> pd.DataFrame:
    records: list[dict] = []
    for row in primary.primary_results.to_dict(orient="records"):
        target = str(row["target"])
        segment_range = _range_for(segment_results, target)
        segment_loo_range = _range_for(leave_one_segment_out, target)
        link_loo_range = _range_for(leave_one_link_out, target)
        pair_loo_range = _range_for(leave_one_pair_out, target)
        documented = spatial_placebos.loc[
            spatial_placebos["target"].eq(target)
            & spatial_placebos["is_documented_assignment"]
        ].iloc[0]
        minima = [
            segment_range[0], segment_loo_range[0], link_loo_range[0], pair_loo_range[0]
        ]
        records.append({
            "target": target,
            "primary_mean_did_degradation": float(row["mean_did_degradation"]),
            "segment_effect_min": segment_range[0],
            "segment_effect_max": segment_range[1],
            "leave_one_segment_min": segment_loo_range[0],
            "leave_one_segment_max": segment_loo_range[1],
            "leave_one_link_min": link_loo_range[0],
            "leave_one_link_max": link_loo_range[1],
            "leave_one_pair_min": pair_loo_range[0],
            "leave_one_pair_max": pair_loo_range[1],
            "documented_spatial_rank": int(
                documented["documented_assignment_one_sided_rank"]
            ),
            "spatial_assignment_count": int(documented["assignment_count"]),
            "spatial_two_sided_tail_fraction": float(
                documented["documented_assignment_two_sided_tail_fraction"]
            ),
            "directionally_stable": bool(
                row["mean_did_degradation"] > 0.0
                and all(np.isfinite(value) and value > 0.0 for value in minima)
            ),
            "inference_status": (
                "robustness summary; confirmatory inference remains in primary_results.csv"
            ),
        })
    return pd.DataFrame.from_records(records)


def run_bristol_hcf_robustness_campaign(
    dataset_dir: str | Path,
    *,
    config: BristolHCFRobustnessConfig = BristolHCFRobustnessConfig(),
) -> BristolHCFRobustnessResult:
    config.validate()
    primary = run_bristol_hcf_coexistence_campaign(
        dataset_dir, config=config.primary_config()
    )
    link_pair_effects = compute_link_pair_effects(primary.telemetry)
    pair_effects = _pairs_with_segments(
        primary.telemetry, compute_switch_pair_effects(link_pair_effects)
    )
    segment_results = compute_segment_results(pair_effects)
    leave_one_segment_out = compute_leave_one_segment_out(pair_effects)
    leave_one_link_out = compute_leave_one_link_out(link_pair_effects)
    leave_one_pair_out = compute_leave_one_pair_out(pair_effects)
    spatial_placebos = compute_spatial_exposure_placebos(link_pair_effects)
    periodicity = audit_switch_periodicity(primary.telemetry)
    summary = summarize_robustness(
        primary, segment_results, leave_one_segment_out, leave_one_link_out,
        leave_one_pair_out, spatial_placebos,
    )
    report = {
        "study": "Bristol HCF coexistence robustness and falsification audit",
        "configuration": asdict(config),
        "dataset": primary.report["dataset"],
        "primary_claim": primary.report["claim_scope"],
        "primary_results": json.loads(primary.primary_results.to_json(orient="records")),
        "sensitivity_families": [
            "effects within each contiguous published sequence segment",
            "leave-one-segment-out stability",
            "leave-one-physical-link-out influence",
            "leave-one-switch-pair-out influence",
            "all 20 three-versus-three spatial exposure assignments",
            "period-2 treatment-label audit",
        ],
        "directionally_stable_target_count": int(summary["directionally_stable"].sum()),
        "target_count": int(len(summary)),
        "periodicity_audit": periodicity,
        "interpretation": (
            "Directional stability reduces the chance that the Bristol effect is driven "
            "by one segment, link, or switch pair. It does not establish randomized "
            "causality, WDM-feature incrementality, future prediction, or Bell fidelity."
        ),
        "statistical_boundary": (
            "Sensitivity tables are descriptive and were added after inspecting the public "
            "data. No new confirmatory p-values are assigned to these families."
        ),
        "git": git_state(),
    }
    return BristolHCFRobustnessResult(
        report=report,
        primary=primary,
        segment_results=segment_results,
        leave_one_segment_out=leave_one_segment_out,
        leave_one_link_out=leave_one_link_out,
        leave_one_pair_out=leave_one_pair_out,
        spatial_exposure_placebos=spatial_placebos,
        robustness_summary=summary,
        periodicity_audit=periodicity,
    )


def write_bristol_hcf_robustness_artifacts(
    result: BristolHCFRobustnessResult, output_dir: str | Path
) -> dict:
    directory = Path(output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    tables = {
        "primary_results.csv": result.primary.primary_results,
        "segment_results.csv": result.segment_results,
        "leave_one_segment_out.csv": result.leave_one_segment_out,
        "leave_one_link_out.csv": result.leave_one_link_out,
        "leave_one_pair_out.csv": result.leave_one_pair_out,
        "spatial_exposure_placebos.csv": result.spatial_exposure_placebos,
        "robustness_summary.csv": result.robustness_summary,
    }
    artifacts: list[Path] = []
    for name, frame in tables.items():
        path = directory / name
        frame.to_csv(path, index=False)
        artifacts.append(path)
    for name, payload in (
        ("periodicity_audit.json", result.periodicity_audit),
        ("report.json", result.report),
    ):
        path = directory / name
        path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False, allow_nan=False) + "\n",
            encoding="utf-8",
        )
        artifacts.append(path)
    manifest = {
        "claim_scope": result.report["primary_claim"],
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
    "BristolHCFRobustnessConfig",
    "BristolHCFRobustnessResult",
    "audit_switch_periodicity",
    "compute_leave_one_link_out",
    "compute_leave_one_pair_out",
    "compute_leave_one_segment_out",
    "compute_segment_results",
    "compute_spatial_exposure_placebos",
    "run_bristol_hcf_robustness_campaign",
    "summarize_robustness",
    "write_bristol_hcf_robustness_artifacts",
]
