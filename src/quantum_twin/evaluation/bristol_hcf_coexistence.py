"""Controlled on/off analysis of the Bristol HCF quantum-network release."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import itertools
import json
from pathlib import Path

import numpy as np
import pandas as pd

from quantum_twin.evaluation.optical_campaign import git_state, sha256_file
from quantum_twin.quantum.bristol_hcf_coexistence import (
    BRISTOL_HCF_ALICE_LINKS,
    BRISTOL_HCF_LINKS,
    BristolHCFCoexistenceSource,
)


@dataclass(frozen=True)
class BristolHCFCoexistenceConfig:
    bootstrap_replicates: int = 50_000
    familywise_alpha: float = 0.05
    seed: int = 42
    maximum_exact_sign_flip_pairs: int = 20

    def validate(self) -> None:
        if self.bootstrap_replicates < 100:
            raise ValueError("bootstrap_replicates must be >= 100")
        if not 0.0 < self.familywise_alpha < 1.0:
            raise ValueError("familywise_alpha must be between 0 and 1")
        if self.maximum_exact_sign_flip_pairs < 1:
            raise ValueError("maximum_exact_sign_flip_pairs must be >= 1")


@dataclass
class BristolHCFCoexistenceResult:
    report: dict
    telemetry: pd.DataFrame
    link_pair_effects: pd.DataFrame
    switch_pair_effects: pd.DataFrame
    primary_results: pd.DataFrame
    link_summary: pd.DataFrame


_TARGETS = {
    "qber_measured": {
        "unit": "QBER proportion",
        "effect": "(On - Off) exposed - (On - Off) control",
        "degradation_sign": 1.0,
    },
    "log1p_secure_key_rate": {
        "unit": "log1p(bit/s)",
        "effect": "(Off - On) exposed - (Off - On) control",
        "degradation_sign": -1.0,
    },
}


def _target_frame(frame: pd.DataFrame, target: str) -> pd.DataFrame:
    selected = frame.copy()
    if target == "qber_measured":
        selected["target_value"] = pd.to_numeric(selected["qber_measured"], errors="coerce")
    elif target == "log1p_secure_key_rate":
        secure_key_rate = pd.to_numeric(selected["secure_key_rate_bps"], errors="coerce")
        selected["target_value"] = np.log1p(secure_key_rate)
    else:
        raise ValueError(f"unknown Bristol HCF target: {target}")
    return selected.dropna(subset=["target_value"])


def compute_link_pair_effects(frame: pd.DataFrame) -> pd.DataFrame:
    """Build link-level On/Off effects using adjacent published chunks only.

    QBER degradation is ``On - Off``.  SKR degradation reverses the sign and
    is calculated on ``log1p(SKR)`` so heterogeneous link rates do not make the
    high-rate control links dominate the estimand.
    """
    records: list[dict] = []
    for target, specification in _TARGETS.items():
        selected = _target_frame(frame, target)
        selected["switch_pair_index"] = selected["relative_chunk_index"] // 2
        for pair_index, pair in selected.groupby("switch_pair_index", sort=True, observed=True):
            chunk_states = pair[["relative_chunk_index", "coexistence_active"]].drop_duplicates()
            if len(chunk_states) != 2 or set(chunk_states["coexistence_active"].astype(int)) != {0, 1}:
                continue
            on_chunk = int(chunk_states.loc[
                chunk_states["coexistence_active"].eq(1), "relative_chunk_index"
            ].iloc[0])
            off_chunk = int(chunk_states.loc[
                chunk_states["coexistence_active"].eq(0), "relative_chunk_index"
            ].iloc[0])
            if abs(on_chunk - off_chunk) != 1:
                continue
            for link, link_pair in pair.groupby("link_id", sort=True, observed=True):
                by_state = link_pair.set_index("coexistence_active")["target_value"]
                if not {0, 1}.issubset(by_state.index):
                    continue
                on_value = float(by_state.loc[1])
                off_value = float(by_state.loc[0])
                degradation = float(
                    specification["degradation_sign"] * (on_value - off_value)
                )
                records.append({
                    "target": target,
                    "target_unit": specification["unit"],
                    "switch_pair_index": int(pair_index),
                    "on_chunk_index": on_chunk,
                    "off_chunk_index": off_chunk,
                    "link_id": str(link),
                    "alice_link": bool(str(link) in BRISTOL_HCF_ALICE_LINKS),
                    "on_value": on_value,
                    "off_value": off_value,
                    "link_degradation": degradation,
                })
    return pd.DataFrame.from_records(records)


def compute_switch_pair_effects(link_pair_effects: pd.DataFrame) -> pd.DataFrame:
    """Aggregate six links to one difference-in-differences per switch pair."""
    records: list[dict] = []
    for (target, pair_index), pair in link_pair_effects.groupby(
        ["target", "switch_pair_index"], sort=True, observed=True
    ):
        observed_links = set(pair["link_id"].astype(str))
        if observed_links != set(BRISTOL_HCF_LINKS):
            continue
        exposed = pair.loc[pair["alice_link"], "link_degradation"].to_numpy(dtype=float)
        controls = pair.loc[~pair["alice_link"], "link_degradation"].to_numpy(dtype=float)
        if len(exposed) != 3 or len(controls) != 3:
            continue
        specification = _TARGETS[str(target)]
        records.append({
            "target": str(target),
            "target_unit": specification["unit"],
            "switch_pair_index": int(pair_index),
            "on_chunk_index": int(pair["on_chunk_index"].iloc[0]),
            "off_chunk_index": int(pair["off_chunk_index"].iloc[0]),
            "mean_exposed_link_degradation": float(exposed.mean()),
            "mean_control_link_degradation": float(controls.mean()),
            "difference_in_differences_degradation": float(
                exposed.mean() - controls.mean()
            ),
            "effect_definition": specification["effect"],
        })
    return pd.DataFrame.from_records(records)


def _exact_or_monte_carlo_sign_flip_pvalue(
    values: np.ndarray,
    *,
    maximum_exact_pairs: int,
    replicates: int,
    seed: int,
) -> tuple[float, str, int]:
    """Two-sided paired sign-flip sensitivity test around a zero mean."""
    values = np.asarray(values, dtype=float)
    observed = abs(float(values.mean()))
    tolerance = np.finfo(float).eps * max(1.0, observed) * 8.0
    if len(values) <= maximum_exact_pairs:
        exceedances = 0
        total = 0
        for signs in itertools.product((-1.0, 1.0), repeat=len(values)):
            statistic = abs(float(np.dot(values, np.asarray(signs)) / len(values)))
            exceedances += int(statistic >= observed - tolerance)
            total += 1
        return float(exceedances / total), "exact paired sign-flip", int(total)
    rng = np.random.default_rng(seed)
    signs = rng.choice((-1.0, 1.0), size=(replicates, len(values)))
    null_statistics = np.abs((signs * values).mean(axis=1))
    p_value = (1.0 + float(np.sum(null_statistics >= observed - tolerance))) / (
        replicates + 1.0
    )
    return float(p_value), "Monte Carlo paired sign-flip", int(replicates)


def compute_primary_results(
    switch_pair_effects: pd.DataFrame,
    *,
    config: BristolHCFCoexistenceConfig = BristolHCFCoexistenceConfig(),
) -> pd.DataFrame:
    """Infer the two primary effects with pair bootstrap and Bonferroni control."""
    config.validate()
    family_size = len(_TARGETS)
    corrected_tail_probability = config.familywise_alpha / (2.0 * family_size)
    records: list[dict] = []
    for target_number, target in enumerate(_TARGETS):
        values = switch_pair_effects.loc[
            switch_pair_effects["target"].eq(target),
            "difference_in_differences_degradation",
        ].to_numpy(dtype=float)
        if len(values) < 2:
            raise ValueError(f"target {target} has fewer than two complete switch pairs")
        rng = np.random.default_rng(config.seed + target_number)
        indices = rng.integers(
            0, len(values), size=(config.bootstrap_replicates, len(values))
        )
        bootstrap_means = values[indices].mean(axis=1)
        ci_low, ci_high = np.quantile(
            bootstrap_means,
            [corrected_tail_probability, 1.0 - corrected_tail_probability],
        )
        p_value, test_method, null_replicates = _exact_or_monte_carlo_sign_flip_pvalue(
            values,
            maximum_exact_pairs=config.maximum_exact_sign_flip_pairs,
            replicates=config.bootstrap_replicates,
            seed=config.seed + 10_000 + target_number,
        )
        adjusted_p_value = min(1.0, p_value * family_size)
        mean_effect = float(values.mean())
        records.append({
            "target": target,
            "target_unit": _TARGETS[target]["unit"],
            "complete_switch_pairs": int(len(values)),
            "mean_did_degradation": mean_effect,
            "median_did_degradation": float(np.median(values)),
            "corrected_ci_low": float(ci_low),
            "corrected_ci_high": float(ci_high),
            "confidence_level": float(1.0 - config.familywise_alpha / family_size),
            "two_sided_p_value": float(p_value),
            "bonferroni_adjusted_p_value": float(adjusted_p_value),
            "family_size": int(family_size),
            "test_method": test_method,
            "null_replicates": int(null_replicates),
            "positive_pair_fraction": float(np.mean(values > 0.0)),
            "corrected_positive_evidence": bool(
                mean_effect > 0.0
                and ci_low > 0.0
                and adjusted_p_value <= config.familywise_alpha
            ),
            "effect_definition": _TARGETS[target]["effect"],
        })
    return pd.DataFrame.from_records(records)


def summarize_links(frame: pd.DataFrame, link_pair_effects: pd.DataFrame) -> pd.DataFrame:
    """Descriptive, non-inferential means for each physical link."""
    records: list[dict] = []
    for target, specification in _TARGETS.items():
        selected = _target_frame(frame, target)
        for link in BRISTOL_HCF_LINKS:
            link_frame = selected.loc[selected["link_id"].eq(link)]
            on_values = link_frame.loc[
                link_frame["coexistence_active"].eq(1), "target_value"
            ].to_numpy(dtype=float)
            off_values = link_frame.loc[
                link_frame["coexistence_active"].eq(0), "target_value"
            ].to_numpy(dtype=float)
            paired = link_pair_effects.loc[
                link_pair_effects["target"].eq(target)
                & link_pair_effects["link_id"].eq(link),
                "link_degradation",
            ].to_numpy(dtype=float)
            records.append({
                "target": target,
                "target_unit": specification["unit"],
                "link_id": link,
                "alice_link": bool(link in BRISTOL_HCF_ALICE_LINKS),
                "on_observations": int(len(on_values)),
                "off_observations": int(len(off_values)),
                "on_mean": float(on_values.mean()),
                "off_mean": float(off_values.mean()),
                "complete_switch_pairs": int(len(paired)),
                "mean_paired_link_degradation": float(paired.mean()),
                "positive_pair_fraction": float(np.mean(paired > 0.0)),
                "inference_status": "descriptive only; no link-wise hypothesis test",
            })
    return pd.DataFrame.from_records(records)


def run_bristol_hcf_coexistence_campaign(
    dataset_dir: str | Path,
    *,
    config: BristolHCFCoexistenceConfig = BristolHCFCoexistenceConfig(),
) -> BristolHCFCoexistenceResult:
    config.validate()
    source = BristolHCFCoexistenceSource(dataset_dir)
    telemetry = source.read()
    validation = source.validate(telemetry)
    if not validation.is_valid:
        raise ValueError(validation.summary())
    link_pair_effects = compute_link_pair_effects(telemetry)
    switch_pair_effects = compute_switch_pair_effects(link_pair_effects)
    primary_results = compute_primary_results(switch_pair_effects, config=config)
    link_summary = summarize_links(telemetry, link_pair_effects)
    positive_count = int(primary_results["corrected_positive_evidence"].sum())
    report = {
        "study": "Bristol HCF measured-QKD coexistence switch analysis",
        "claim_scope": (
            "Whether a documented classical-DWDM on/off condition is associated with "
            "simultaneous degradation of measured QBER and secure key rate on exposed "
            "Alice links, beyond changes in three contemporaneous unexposed links."
        ),
        "status": "exploratory; the public data were inspected before this protocol was frozen",
        "scientific_role": (
            "real-hardware mechanistic bridge for the coexistence effect; not a prospective "
            "WDM-telemetry forecast and not a Bell-fidelity validation"
        ),
        "dataset": {
            "repository": "University of Bristol Research Data Repository",
            "doi": "10.5523/bris.61193flfovbn2puknndsr3qm5",
            "license": "Non-Commercial Government Licence",
            "quality": source.quality_report(telemetry),
            "provenance": telemetry.attrs["telemetry_provenance"],
        },
        "configuration": asdict(config),
        "experimental_unit": "one complete adjacent On/Off switch pair",
        "primary_estimand": (
            "mean exposed-link degradation minus mean unexposed-control-link degradation "
            "within the same adjacent switch pair"
        ),
        "inference": {
            "test": "two-sided paired sign-flip sensitivity test",
            "interval": "switch-pair nonparametric bootstrap",
            "multiplicity": "Bonferroni across QBER and log1p(SKR)",
            "important_caveat": (
                "switching alternated deterministically rather than being randomized; the "
                "control links remove shared short-term drift but not every possible "
                "link-specific time-varying confounder"
            ),
        },
        "not_claimed": [
            "No timestamped BER, OSNR, or per-chunk classical-power telemetry is present.",
            "No Bell-state fidelity time series is present or reconstructed from QBER.",
            "Published chunk indices are not converted into timestamps or durations.",
            "The analysis is contemporaneous and does not establish future prediction.",
            "The deterministic on/off sequence is not treated as a randomized experiment.",
            "The result does not satisfy the strict PairedQuantumTelemetrySource contract.",
        ],
        "primary_positive_count": positive_count,
        "primary_test_count": int(len(primary_results)),
        "central_thesis_interpretation": (
            "Positive results support the narrower claim that a real classical optical "
            "coexistence condition contains information about simultaneous real QKD "
            "performance. The stronger claim that multivariate WDM telemetry predicts "
            "future measured quantum fidelity remains untested."
        ),
        "git": git_state(),
    }
    return BristolHCFCoexistenceResult(
        report=report,
        telemetry=telemetry,
        link_pair_effects=link_pair_effects,
        switch_pair_effects=switch_pair_effects,
        primary_results=primary_results,
        link_summary=link_summary,
    )


def _plot_campaign(result: BristolHCFCoexistenceResult, directory: Path) -> list[Path]:
    import matplotlib

    matplotlib.use("Agg", force=True)
    import matplotlib.pyplot as plt
    from matplotlib.patches import Patch

    generated: list[Path] = []
    figure, axes = plt.subplots(2, 1, figsize=(10, 7), sharex=True)
    target_details = (
        ("qber_measured", "QBER medido"),
        ("secure_key_rate_bps", "SKR medido (bit/s)"),
    )
    color_map = plt.get_cmap("tab10")
    link_colors = {
        link: color_map(index) for index, link in enumerate(BRISTOL_HCF_LINKS)
    }
    for axis, (target, label) in zip(axes, target_details):
        on_chunks = result.telemetry.loc[
            result.telemetry["coexistence_active"].eq(1), "relative_chunk_index"
        ].unique()
        for chunk in on_chunks:
            axis.axvspan(float(chunk) - 0.45, float(chunk) + 0.45, color="gold", alpha=0.08)
        for link, group in result.telemetry.groupby("link_id", sort=True, observed=True):
            for segment_number, (_, segment) in enumerate(
                group.groupby("sequence_segment", sort=True, observed=True)
            ):
                axis.plot(
                    segment["relative_chunk_index"], segment[target], marker="o",
                    markersize=2.5, linewidth=0.9, color=link_colors[str(link)],
                    label=str(link) if segment_number == 0 else None,
                )
        axis.set_ylabel(label)
        axis.grid(alpha=0.25)
    handles, labels = axes[0].get_legend_handles_labels()
    handles.append(Patch(facecolor="gold", alpha=0.2, label="Coexistência On"))
    axes[0].legend(handles=handles, ncol=7, fontsize=8)
    axes[-1].set_xlabel("Índice relativo publicado (lacunas preservadas)")
    figure.suptitle("Rede Bristol HCF: medições quânticas sob coexistência alternada")
    figure.tight_layout()
    path = directory / "measured_quantum_sequence.png"
    figure.savefig(path, dpi=180)
    plt.close(figure)
    generated.append(path)

    figure, axes = plt.subplots(1, 2, figsize=(10, 4.3))
    display_names = {
        "qber_measured": "QBER medido",
        "log1p_secure_key_rate": "log1p(SKR medido)",
    }
    for axis, target in zip(axes, _TARGETS):
        values = result.switch_pair_effects.loc[
            result.switch_pair_effects["target"].eq(target),
            "difference_in_differences_degradation",
        ].to_numpy(dtype=float)
        axis.scatter(np.arange(1, len(values) + 1), values, s=28)
        axis.axhline(0.0, color="black", linewidth=1)
        axis.axhline(values.mean(), color="tab:red", linewidth=1.5, linestyle="--")
        axis.set_title(display_names[target])
        axis.set_xlabel("Par adjacente completo")
        axis.set_ylabel("Degradação DiD")
        axis.grid(alpha=0.25)
    figure.tight_layout()
    path = directory / "switch_pair_did_effects.png"
    figure.savefig(path, dpi=180)
    plt.close(figure)
    generated.append(path)
    return generated


def write_bristol_hcf_coexistence_artifacts(
    result: BristolHCFCoexistenceResult,
    output_dir: str | Path,
    *,
    make_plots: bool = True,
) -> dict:
    directory = Path(output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    artifacts: list[Path] = []
    tables = {
        "canonical_telemetry.csv": result.telemetry,
        "link_pair_effects.csv": result.link_pair_effects,
        "switch_pair_effects.csv": result.switch_pair_effects,
        "primary_results.csv": result.primary_results,
        "link_summary.csv": result.link_summary,
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
        artifacts.extend(_plot_campaign(result, directory))
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
    "BristolHCFCoexistenceConfig",
    "BristolHCFCoexistenceResult",
    "compute_link_pair_effects",
    "compute_primary_results",
    "compute_switch_pair_effects",
    "run_bristol_hcf_coexistence_campaign",
    "summarize_links",
    "write_bristol_hcf_coexistence_artifacts",
]
