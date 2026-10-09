"""Audit public QKD coexistence sources and published aggregate evidence.

The strict central claim needs ordered, paired WDM telemetry and future
measured quantum outcomes.  Published condition means can corroborate a
physical direction, but they are never promoted to raw samples or used for
formal replication inference in this module.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path

import numpy as np
import pandas as pd

from quantum_twin.evaluation.optical_campaign import git_state, sha256_file


SEARCH_SNAPSHOT_DATE = "2026-09-02"
ALLOWED_DECISIONS = {
    "already_integrated_formal_effect",
    "published_aggregate_only",
    "excluded_no_public_data",
    "excluded_no_wdm_pairing",
}


@dataclass
class PublicQKDEvidenceResult:
    report: dict
    candidate_registry: pd.DataFrame
    published_observations: pd.DataFrame
    condition_contrasts: pd.DataFrame
    aggregate_diagnostics: pd.DataFrame


def build_public_qkd_candidate_registry() -> pd.DataFrame:
    """Return the frozen rapid-search registry, including negative findings."""
    records = [
        {
            "dataset_id": "irelandqci-toshiba-coexistence",
            "title": "QKD Network Coexistence Datasets with Standardised Performance Metrics",
            "year": 2026,
            "source_url": "https://zenodo.org/records/21132088",
            "platform": "Toshiba QKD controlled attenuation/injection sweeps",
            "classical_condition_measured": True,
            "measured_qber": True,
            "measured_skr": True,
            "ordered_wdm_vector": False,
            "public_machine_readable_data": True,
            "raw_or_measurement_level_data": True,
            "documented_independent_units": True,
            "reusable_data_license": True,
            "formal_k3_effect_eligible": True,
            "strict_temporal_prediction_eligible": False,
            "aggregate_corroboration_eligible": True,
            "decision": "already_integrated_formal_effect",
            "reason": "measurement-level controlled cells; no timestamps or WDM telemetry vector",
        },
        {
            "dataset_id": "bristol-hcf-qkd-coexistence",
            "title": "Coexistence of entanglement-based quantum channels with DWDM classical channels",
            "year": 2025,
            "source_url": "https://doi.org/10.5523/bris.61193flfovbn2puknndsr3qm5",
            "platform": "four-node entanglement QKD over hollow-core fibre",
            "classical_condition_measured": True,
            "measured_qber": True,
            "measured_skr": True,
            "ordered_wdm_vector": False,
            "public_machine_readable_data": True,
            "raw_or_measurement_level_data": True,
            "documented_independent_units": True,
            "reusable_data_license": True,
            "formal_k3_effect_eligible": True,
            "strict_temporal_prediction_eligible": False,
            "aggregate_corroboration_eligible": True,
            "decision": "already_integrated_formal_effect",
            "reason": "ordered On/Off QKD measurements; no multivariate WDM telemetry",
        },
        {
            "dataset_id": "gavignet-2024-dwdm",
            "title": "Co-Propagation of QKD and 6 Tb/s DWDM Channels",
            "year": 2024,
            "source_url": "https://arxiv.org/abs/2312.11924",
            "platform": "Toshiba MU/LD-QKD with 30/60-channel and 55-channel DWDM",
            "classical_condition_measured": True,
            "measured_qber": True,
            "measured_skr": True,
            "ordered_wdm_vector": False,
            "public_machine_readable_data": False,
            "raw_or_measurement_level_data": False,
            "documented_independent_units": False,
            "reusable_data_license": False,
            "formal_k3_effect_eligible": False,
            "strict_temporal_prediction_eligible": False,
            "aggregate_corroboration_eligible": True,
            "decision": "published_aggregate_only",
            "reason": "condition means in prose/figures; arXiv source has no numeric measurement files",
        },
        {
            "dataset_id": "stathis-2026-last-mile-cw",
            "title": "Practical approach to last-mile converged free-space and fiber QKD",
            "year": 2026,
            "source_url": "https://doi.org/10.1038/s41598-025-34184-z",
            "platform": "polarization QKD over hybrid free-space/fibre last mile",
            "classical_condition_measured": True,
            "measured_qber": True,
            "measured_skr": True,
            "ordered_wdm_vector": False,
            "public_machine_readable_data": False,
            "raw_or_measurement_level_data": False,
            "documented_independent_units": False,
            "reusable_data_license": False,
            "formal_k3_effect_eligible": False,
            "strict_temporal_prediction_eligible": False,
            "aggregate_corroboration_eligible": True,
            "decision": "published_aggregate_only",
            "reason": "five exact condition values in Table 1; underlying data available only on request",
        },
        {
            "dataset_id": "wu-2025-field-mcf",
            "title": "Integration of QKD and high-throughput communications in field-deployed MCF",
            "year": 2025,
            "source_url": "https://doi.org/10.1038/s41377-025-01982-z",
            "platform": "field-deployed four-core fibre with 510 classical channels",
            "classical_condition_measured": True,
            "measured_qber": True,
            "measured_skr": True,
            "ordered_wdm_vector": False,
            "public_machine_readable_data": False,
            "raw_or_measurement_level_data": False,
            "documented_independent_units": False,
            "reusable_data_license": False,
            "formal_k3_effect_eligible": False,
            "strict_temporal_prediction_eligible": False,
            "aggregate_corroboration_eligible": False,
            "decision": "excluded_no_public_data",
            "reason": "curves only; article states data and code are available on request",
        },
        {
            "dataset_id": "maragkopoulos-2024-qkd-diagnostics",
            "title": "Real-Time Diagnostics on a QKD Link via QBER Time-Series Analysis",
            "year": 2024,
            "source_url": "https://doi.org/10.3390/e26110922",
            "platform": "Toshiba T12 QKD with induced photon-addition/loss impairments",
            "classical_condition_measured": True,
            "measured_qber": True,
            "measured_skr": True,
            "ordered_wdm_vector": False,
            "public_machine_readable_data": False,
            "raw_or_measurement_level_data": False,
            "documented_independent_units": False,
            "reusable_data_license": False,
            "formal_k3_effect_eligible": False,
            "strict_temporal_prediction_eligible": False,
            "aggregate_corroboration_eligible": False,
            "decision": "excluded_no_public_data",
            "reason": "valuable QBER/SKR time series and labels, but data/programs are request-only",
        },
        {
            "dataset_id": "wang-2023-time-interleaved-cband",
            "title": "Time-Interleaved C-band Co-Propagation of Quantum and Classical Channels",
            "year": 2023,
            "source_url": "https://arxiv.org/abs/2304.13828",
            "platform": "polarization BB84 with 100 Gb/s C-band coexistence",
            "classical_condition_measured": True,
            "measured_qber": True,
            "measured_skr": True,
            "ordered_wdm_vector": False,
            "public_machine_readable_data": False,
            "raw_or_measurement_level_data": False,
            "documented_independent_units": False,
            "reusable_data_license": False,
            "formal_k3_effect_eligible": False,
            "strict_temporal_prediction_eligible": False,
            "aggregate_corroboration_eligible": False,
            "decision": "excluded_no_public_data",
            "reason": "arXiv source archive contains figure PDFs but no numeric measurement files",
        },
        {
            "dataset_id": "dryad-stike-168h",
            "title": "Simultaneous transmission of information and key exchange using photonic states",
            "year": 2025,
            "source_url": "https://doi.org/10.5061/dryad.n2z34tn70",
            "platform": "104.8-km STIKE prototype",
            "classical_condition_measured": False,
            "measured_qber": True,
            "measured_skr": False,
            "ordered_wdm_vector": False,
            "public_machine_readable_data": True,
            "raw_or_measurement_level_data": True,
            "documented_independent_units": False,
            "reusable_data_license": True,
            "formal_k3_effect_eligible": False,
            "strict_temporal_prediction_eligible": False,
            "aggregate_corroboration_eligible": False,
            "decision": "excluded_no_wdm_pairing",
            "reason": "ordered QBER and communication rate, but no WDM telemetry and rate is not SKR",
        },
    ]
    return pd.DataFrame.from_records(records)


def validate_public_qkd_candidate_registry(registry: pd.DataFrame) -> list[str]:
    """Check that eligibility labels cannot outrun the documented capabilities."""
    errors: list[str] = []
    if registry["dataset_id"].duplicated().any():
        errors.append("dataset_id values must be unique")
    for row in registry.to_dict(orient="records"):
        dataset = str(row["dataset_id"])
        if row["decision"] not in ALLOWED_DECISIONS:
            errors.append(f"{dataset}: unsupported decision {row['decision']}")
        if row["formal_k3_effect_eligible"] and not all((
            row["classical_condition_measured"],
            row["measured_qber"],
            row["measured_skr"],
            row["public_machine_readable_data"],
            row["raw_or_measurement_level_data"],
            row["documented_independent_units"],
            row["reusable_data_license"],
        )):
            errors.append(f"{dataset}: formal k>=3 eligibility lacks required evidence")
        if row["strict_temporal_prediction_eligible"] and not all((
            row["ordered_wdm_vector"],
            row["public_machine_readable_data"],
            row["raw_or_measurement_level_data"],
            row["measured_qber"] or row["measured_skr"],
        )):
            errors.append(f"{dataset}: temporal prediction eligibility lacks paired data")
        if row["aggregate_corroboration_eligible"] and not all((
            row["classical_condition_measured"],
            row["measured_qber"],
            row["measured_skr"],
        )):
            errors.append(f"{dataset}: aggregate corroboration lacks measured QKD outcomes")
        if row["decision"] == "published_aggregate_only" and row["formal_k3_effect_eligible"]:
            errors.append(f"{dataset}: published aggregate cannot be formal k>=3 evidence")
    return errors


def load_published_aggregate_observations(path: str | Path) -> pd.DataFrame:
    frame = pd.read_csv(path)
    required = {
        "study_id", "design_id", "condition_id", "condition_order",
        "observation_level", "fiber_length_km", "wdm_channels",
        "wdm_total_power_dbm", "secure_key_rate_kbps", "qber_percent",
        "source_location", "transcription_note",
    }
    missing = sorted(required.difference(frame.columns))
    if missing:
        raise ValueError(f"published aggregate is missing columns: {missing}")
    if frame.duplicated(["study_id", "design_id", "condition_id"]).any():
        raise ValueError("published aggregate has duplicate conditions")
    if not frame["observation_level"].astype(str).str.startswith("published_").all():
        raise ValueError("every row must remain explicitly labelled as a published aggregate")
    if (pd.to_numeric(frame["secure_key_rate_kbps"], errors="coerce") < 0).any():
        raise ValueError("secure key rate cannot be negative")
    qber = pd.to_numeric(frame["qber_percent"], errors="coerce")
    if qber.isna().any() or not qber.between(0.0, 100.0).all():
        raise ValueError("qber_percent must be finite and between 0 and 100")
    return frame.sort_values(["design_id", "condition_order"], kind="stable").reset_index(drop=True)


def _spearman(x: pd.Series, y: pd.Series) -> float:
    x_rank = pd.to_numeric(x).rank(method="average").to_numpy(dtype=float)
    y_rank = pd.to_numeric(y).rank(method="average").to_numpy(dtype=float)
    return float(np.corrcoef(x_rank, y_rank)[0, 1])


def analyze_published_aggregate_observations(
    observations: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Compute descriptive contrasts only; no p-values or pseudo-replicated CIs."""
    single = observations.loc[observations["design_id"].eq("single-span-50km")]
    baseline = single.loc[single["condition_id"].eq("no-wdm")]
    if len(baseline) != 1 or len(single) < 2:
        raise ValueError("single-span design needs one no-WDM baseline and exposed conditions")
    base = baseline.iloc[0]
    contrasts: list[dict] = []
    for row in single.loc[~single["condition_id"].eq("no-wdm")].to_dict(orient="records"):
        qber_change = float(row["qber_percent"] - base["qber_percent"])
        skr_change = float(row["secure_key_rate_kbps"] - base["secure_key_rate_kbps"])
        contrasts.append({
            "study_id": str(row["study_id"]),
            "design_id": str(row["design_id"]),
            "contrast": f"{row['condition_id']}-vs-no-wdm",
            "wdm_channels": int(row["wdm_channels"]),
            "qber_change_percentage_points": qber_change,
            "skr_change_kbps": skr_change,
            "skr_relative_change_percent": 100.0 * skr_change / float(base["secure_key_rate_kbps"]),
            "qber_degraded": bool(qber_change > 0.0),
            "skr_degraded": bool(skr_change < 0.0),
            "inference_level": "descriptive published-condition contrast",
        })
    contrast_frame = pd.DataFrame.from_records(contrasts)

    multi = observations.loc[observations["design_id"].eq("multi-span-mu-50km")].copy()
    multi = multi.sort_values("wdm_total_power_dbm", kind="stable")
    required_numeric = [
        "wdm_total_power_dbm", "secure_key_rate_kbps", "qber_percent",
        "pre_fec_ber", "osnr_db_0p1nm",
    ]
    if len(multi) < 3 or multi[required_numeric].isna().any().any():
        raise ValueError("multi-span audit needs at least three complete paired condition means")
    power = multi["wdm_total_power_dbm"]
    diagnostics = pd.DataFrame.from_records([
        {
            "study_id": str(multi["study_id"].iloc[0]),
            "design_id": "multi-span-mu-50km",
            "published_condition_means": int(len(multi)),
            "power_vs_qber_spearman": _spearman(power, multi["qber_percent"]),
            "power_vs_skr_spearman": _spearman(power, multi["secure_key_rate_kbps"]),
            "osnr_vs_qber_spearman": _spearman(multi["osnr_db_0p1nm"], multi["qber_percent"]),
            "pre_fec_ber_vs_qber_spearman": _spearman(multi["pre_fec_ber"], multi["qber_percent"]),
            "qber_all_adjacent_steps_degrade": bool((multi["qber_percent"].diff().dropna() > 0).all()),
            "skr_all_adjacent_steps_degrade": bool((multi["secure_key_rate_kbps"].diff().dropna() < 0).all()),
            "classical_quantum_tradeoff_observed": bool(
                (multi["osnr_db_0p1nm"].diff().dropna() > 0).all()
                and (multi["pre_fec_ber"].diff().dropna() < 0).all()
                and (multi["qber_percent"].diff().dropna() > 0).all()
                and (multi["secure_key_rate_kbps"].diff().dropna() < 0).all()
            ),
            "p_values_computed": False,
            "formal_replication_unit": False,
            "interpretation": (
                "three approximate 24-hour condition means; descriptive monotonicity only"
            ),
        }
    ])
    return contrast_frame, diagnostics


def build_search_protocol() -> dict:
    return {
        "snapshot_date": SEARCH_SNAPSHOT_DATE,
        "scope": "rapid structured search for public measured QKD/classical coexistence data",
        "sources": [
            "Zenodo", "Dryad", "Figshare", "Mendeley Data", "GitHub",
            "arXiv source archives", "Europe PMC/PMC", "publisher supplements",
        ],
        "query_concepts": [
            "QKD QBER SKR dataset WDM coexistence",
            "QKD QBER SKR source data classical channels",
            "QKD telemetry time series public data",
            "DWDM QKD BER OSNR measured data",
        ],
        "formal_k3_inclusion": [
            "measured QBER and measured SKR",
            "documented classical optical baseline, exposure, or dose",
            "public machine-readable measurement-level values",
            "documented independent experimental units",
            "reusable data license and primary-source provenance",
            "hardware platform distinct from the two integrated studies",
        ],
        "strict_temporal_inclusion": [
            "ordered or timestamped multivariate WDM telemetry",
            "synchronized future measured QBER, SKR, or fidelity",
            "enough independent campaigns/devices for purged and external tests",
        ],
        "exclusions": [
            "simulated or expected quantum outcomes",
            "request-only data",
            "figure-only values without documented independent units",
            "repeated plot points treated as independent treatment units",
            "quantum-only time series without WDM telemetry",
        ],
        "review_type": (
            "auditable rapid search, not a registered systematic review or PRISMA claim"
        ),
    }


def run_public_qkd_evidence_audit(
    aggregate_csv: str | Path,
) -> PublicQKDEvidenceResult:
    registry = build_public_qkd_candidate_registry()
    errors = validate_public_qkd_candidate_registry(registry)
    if errors:
        raise ValueError("; ".join(errors))
    observations = load_published_aggregate_observations(aggregate_csv)
    contrasts, diagnostics = analyze_published_aggregate_observations(observations)
    new_candidates = registry.loc[
        ~registry["decision"].eq("already_integrated_formal_effect")
    ]
    report = {
        "study": "Public measured QKD coexistence evidence audit",
        "search_snapshot_date": SEARCH_SNAPSHOT_DATE,
        "candidate_count": int(len(registry)),
        "new_candidate_count": int(len(new_candidates)),
        "formal_k3_eligible_platform_count_total": int(registry["formal_k3_effect_eligible"].sum()),
        "new_formal_k3_eligible_platform_count": int(new_candidates["formal_k3_effect_eligible"].sum()),
        "strict_temporal_prediction_eligible_count": int(
            registry["strict_temporal_prediction_eligible"].sum()
        ),
        "new_published_aggregate_corroboration_count": int(
            new_candidates["aggregate_corroboration_eligible"].sum()
        ),
        "gavignet_single_span_all_directions_consistent": bool(
            contrasts[["qber_degraded", "skr_degraded"]].all().all()
        ),
        "gavignet_multispan_tradeoff_observed": bool(
            diagnostics["classical_quantum_tradeoff_observed"].iloc[0]
        ),
        "formal_k3_status": (
            "not reached: no newly located source exposes measurement-level public data "
            "with documented independent units"
        ),
        "central_temporal_claim_status": (
            "open: no candidate exposes synchronized ordered WDM telemetry and a future "
            "measured quantum target"
        ),
        "claim_boundary": (
            "Gavignet is a third physical platform and descriptive external corroboration, "
            "not a third inferential study and not a forecasting dataset"
        ),
        "git": git_state(),
    }
    return PublicQKDEvidenceResult(
        report=report,
        candidate_registry=registry,
        published_observations=observations,
        condition_contrasts=contrasts,
        aggregate_diagnostics=diagnostics,
    )


def write_public_qkd_evidence_artifacts(
    result: PublicQKDEvidenceResult,
    output_dir: str | Path,
) -> dict:
    directory = Path(output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    artifacts: list[Path] = []
    for name, frame in (
        ("candidate_registry.csv", result.candidate_registry),
        ("published_aggregate_observations.csv", result.published_observations),
        ("condition_contrasts.csv", result.condition_contrasts),
        ("aggregate_diagnostics.csv", result.aggregate_diagnostics),
    ):
        path = directory / name
        frame.to_csv(path, index=False)
        artifacts.append(path)
    report_path = directory / "report.json"
    report_path.write_text(
        json.dumps(result.report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    artifacts.append(report_path)
    protocol_path = directory / "search_protocol.json"
    protocol_path.write_text(
        json.dumps(build_search_protocol(), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    artifacts.append(protocol_path)
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
    "PublicQKDEvidenceResult",
    "analyze_published_aggregate_observations",
    "build_public_qkd_candidate_registry",
    "build_search_protocol",
    "load_published_aggregate_observations",
    "run_public_qkd_evidence_audit",
    "validate_public_qkd_candidate_registry",
    "write_public_qkd_evidence_artifacts",
]
