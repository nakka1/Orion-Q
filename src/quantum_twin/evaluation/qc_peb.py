"""Dataset capability matrix for the Quantum-Classical Predictive Evidence Base."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from quantum_twin.evaluation.optical_campaign import git_state, sha256_file


def build_qc_peb_capability_matrix() -> pd.DataFrame:
    """Return explicit capabilities without collapsing them into a validity score."""
    common_false = {
        "real_wdm_timeseries": False,
        "classical_optical_intervention_or_condition": False,
        "measured_qber": False,
        "measured_secure_key_rate": False,
        "measured_communication_rate": False,
        "measured_bell_fidelity": False,
        "absolute_utc_timestamps": False,
        "relative_temporal_order": False,
        "supports_optical_forecast": False,
        "supports_contemporaneous_classical_quantum_effect": False,
        "supports_quantum_own_history_forecast": False,
        "supports_incremental_wdm_quantum_forecast": False,
        "supports_measured_fidelity_forecast": False,
    }

    def row(**values) -> dict:
        return {**common_false, **values}

    records = [
        row(
            dataset_id="original-synthetic-nisq-twin",
            source="repository simulator",
            doi_or_url="local simulation",
            license="Apache-2.0 code; generated data",
            origin="simulated",
            protocol="simulated NISQ repeater channel",
            relative_temporal_order=True,
            status="available; simulation evidence only",
            scientific_role="mechanism and pipeline development",
        ),
        row(
            dataset_id="santanna-optical-failure",
            source="ARNO optical testbed",
            doi_or_url="github.com/Network-And-Services/optical-failure-dataset",
            license="confirm with dataset authors before redistribution",
            origin="recorded hardware",
            protocol="classical coherent optical network",
            real_wdm_timeseries=True,
            absolute_utc_timestamps=True,
            relative_temporal_order=True,
            supports_optical_forecast=True,
            status="available and evaluated",
            scientific_role="external optical forecasting validation",
        ),
        row(
            dataset_id="bristol-ndff-multivendor-wdm",
            source="Bristol 339-km NDFF field link",
            doi_or_url=(
                "github.com/hpn-bristol/Open-Source-Data-for-Multi-Domain-Network-"
                "Monitoring-and-Sensing-in-Optical-and-Wireless-Networks"
            ),
            license="academic research and non-commercial use",
            origin="recorded hardware",
            protocol="classical multivendor WDM field telemetry",
            real_wdm_timeseries=True,
            relative_temporal_order=True,
            supports_optical_forecast=True,
            status="available and evaluated; timestamps have no declared timezone",
            scientific_role="real-WDM temporal replay and negative control",
        ),
        row(
            dataset_id="irelandqci-toshiba-coexistence",
            source="IrelandQCI/Toshiba controlled sweeps",
            doi_or_url="10.5281/zenodo.21132088",
            license="CC-BY-4.0",
            origin="recorded hardware",
            protocol="QKD coexistence attenuation/injection sweeps",
            classical_optical_intervention_or_condition=True,
            measured_qber=True,
            measured_secure_key_rate=True,
            supports_contemporaneous_classical_quantum_effect=True,
            status="available and evaluated; no timestamps",
            scientific_role="matched real-QKD coexistence association",
        ),
        row(
            dataset_id="bristol-hcf-qkd-coexistence",
            source="Bristol four-node hollow-core-fibre network",
            doi_or_url="10.5523/bris.61193flfovbn2puknndsr3qm5",
            license="Non-Commercial Government Licence",
            origin="recorded hardware",
            protocol="entanglement-based QKD with DWDM coexistence On/Off",
            classical_optical_intervention_or_condition=True,
            measured_qber=True,
            measured_secure_key_rate=True,
            relative_temporal_order=True,
            supports_contemporaneous_classical_quantum_effect=True,
            status="available, evaluated, and robustness-audited; no WDM vector",
            scientific_role="controlled real-hardware mechanistic bridge",
        ),
        row(
            dataset_id="dryad-stike-168h",
            source="104.8-km STIKE prototype",
            doi_or_url="10.5061/dryad.n2z34tn70",
            license="CC0-1.0",
            origin="recorded hardware",
            protocol="STIKE one-way quasi-QSDC",
            measured_qber=True,
            measured_communication_rate=True,
            relative_temporal_order=True,
            supports_quantum_own_history_forecast=True,
            status="public metadata verified; file endpoint currently rejects anonymous download",
            scientific_role="real-quantum temporal baseline; not a WDM test",
        ),
        row(
            dataset_id="gavignet-dwdm-published-aggregate",
            source="Gavignet et al. 30/60-channel and multispan DWDM experiments",
            doi_or_url="arXiv:2312.11924; DOI:10.1109/JLT.2023.3324840",
            license="no standalone dataset license; numeric facts transcribed with attribution",
            origin="published hardware aggregates",
            protocol="Toshiba MU/LD-QKD co-propagating with up to 60 DWDM channels",
            classical_optical_intervention_or_condition=True,
            measured_qber=True,
            measured_secure_key_rate=True,
            status="published condition means only; no machine-readable source measurements",
            scientific_role="third-platform descriptive directional corroboration",
        ),
        row(
            dataset_id="stathis-last-mile-published-aggregate",
            source="Stathis et al. hybrid free-space/fibre QKD coexistence",
            doi_or_url="10.1038/s41598-025-34184-z",
            license="CC-BY-4.0 article; underlying data available only on request",
            origin="published hardware aggregates",
            protocol="polarization QKD with an added co-propagating C-band CW channel",
            classical_optical_intervention_or_condition=True,
            measured_qber=True,
            measured_secure_key_rate=True,
            status="five published condition values; no public source measurements",
            scientific_role="additional descriptive optical-power dose corroboration",
        ),
        row(
            dataset_id="strict-paired-quantum-wdm-profile",
            source="future acquisition or newly discovered public release",
            doi_or_url="not yet available",
            license="to be determined",
            origin="required experimental design",
            protocol="synchronized WDM plus quantum outcomes",
            real_wdm_timeseries=True,
            classical_optical_intervention_or_condition=True,
            measured_qber=True,
            measured_secure_key_rate=True,
            measured_bell_fidelity=True,
            absolute_utc_timestamps=True,
            relative_temporal_order=True,
            supports_optical_forecast=True,
            supports_contemporaneous_classical_quantum_effect=True,
            supports_quantum_own_history_forecast=True,
            supports_incremental_wdm_quantum_forecast=True,
            supports_measured_fidelity_forecast=True,
            status="contract implemented; no qualifying public dataset located",
            scientific_role="decisive prospective validation target",
        ),
    ]
    columns = [
        "dataset_id", "source", "doi_or_url", "license", "origin", "protocol",
        *common_false.keys(), "status", "scientific_role",
    ]
    return pd.DataFrame.from_records(records)[columns]


def validate_qc_peb_capability_matrix(matrix: pd.DataFrame) -> list[str]:
    """Reject capability claims that are unsupported by their required fields."""
    errors: list[str] = []
    for row in matrix.to_dict(orient="records"):
        dataset = row["dataset_id"]
        measured_quantum = bool(
            row["measured_qber"] or row["measured_secure_key_rate"]
            or row["measured_communication_rate"] or row["measured_bell_fidelity"]
        )
        if row["supports_optical_forecast"] and not (
            row["real_wdm_timeseries"] and row["relative_temporal_order"]
        ):
            errors.append(f"{dataset}: optical forecast lacks ordered real WDM telemetry")
        if row["supports_contemporaneous_classical_quantum_effect"] and not (
            row["classical_optical_intervention_or_condition"] and measured_quantum
        ):
            errors.append(f"{dataset}: coexistence effect lacks condition or quantum target")
        if row["supports_quantum_own_history_forecast"] and not (
            row["relative_temporal_order"] and measured_quantum
        ):
            errors.append(f"{dataset}: quantum temporal baseline lacks ordered quantum target")
        if row["supports_incremental_wdm_quantum_forecast"] and not (
            row["real_wdm_timeseries"]
            and row["relative_temporal_order"]
            and measured_quantum
        ):
            errors.append(f"{dataset}: incremental WDM claim lacks paired ordered data")
        if row["supports_measured_fidelity_forecast"] and not row["measured_bell_fidelity"]:
            errors.append(f"{dataset}: fidelity forecast lacks measured fidelity")
    return errors


def build_qc_peb_evidence_ledger(matrix: pd.DataFrame | None = None) -> pd.DataFrame:
    matrix = build_qc_peb_capability_matrix() if matrix is None else matrix
    errors = validate_qc_peb_capability_matrix(matrix)
    if errors:
        raise ValueError("; ".join(errors))
    return pd.DataFrame.from_records([
        {
            "evidence_id": "E1-simulation",
            "claim": "The original simulated WDM/fidelity mechanism is reproducible.",
            "dataset_id": "original-synthetic-nisq-twin",
            "result": "positive by construction/model assumptions",
            "boundary": "not experimental validation",
        },
        {
            "evidence_id": "E2-optical-temporal",
            "claim": "Real WDM telemetry supports optical forecasting evaluation.",
            "dataset_id": "santanna-optical-failure; bristol-ndff-multivendor-wdm",
            "result": "mixed; frozen cross-campaign and permutation controls retained",
            "boundary": "no measured quantum target",
        },
        {
            "evidence_id": "E3-measured-coexistence",
            "claim": "Classical optical conditions are associated with measured QKD degradation.",
            "dataset_id": "irelandqci-toshiba-coexistence; bristol-hcf-qkd-coexistence",
            "result": (
                "directionally replicated for QBER and log1p(SKR); Holm partial-conjunction "
                "p=0.001495 and 0.005737"
            ),
            "boundary": (
                "contemporaneous association, not future WDM prediction; magnitude highly "
                "heterogeneous across two studies"
            ),
        },
        {
            "evidence_id": "E4-bristol-robustness",
            "claim": "The Bristol HCF direction is not driven by one segment, link, or pair.",
            "dataset_id": "bristol-hcf-qkd-coexistence",
            "result": "QBER and log1p(SKR) directionally stable across audited families",
            "boundary": "post-inspection descriptive sensitivities; deterministic On/Off order",
        },
        {
            "evidence_id": "E5-real-quantum-temporal",
            "claim": "Small-sample real-quantum forecasting can be tested without WDM.",
            "dataset_id": "dryad-stike-168h",
            "result": "pipeline ready; execution blocked by public file endpoint",
            "boundary": "STIKE communication rate is not SKR; no WDM features",
        },
        {
            "evidence_id": "E6-decisive-gap",
            "claim": "Incremental WDM information predicts future measured quantum performance.",
            "dataset_id": "strict-paired-quantum-wdm-profile",
            "result": "not yet tested on a qualifying dataset",
            "boundary": "central discovery claim remains open",
        },
        {
            "evidence_id": "E7-published-cross-platform-aggregates",
            "claim": "Additional physical platforms show the same coexistence-degradation direction.",
            "dataset_id": (
                "gavignet-dwdm-published-aggregate; "
                "stathis-last-mile-published-aggregate"
            ),
            "result": (
                "descriptive direction agrees for measured QBER and SKR; the Gavignet "
                "multispan means also expose a classical/quantum performance tradeoff"
            ),
            "boundary": (
                "published condition aggregates only; no independent-unit inference, "
                "uncertainty reconstruction, or temporal forecasting"
            ),
        },
        {
            "evidence_id": "E8-classical-quantum-pareto",
            "claim": "Classical and quantum coexistence objectives require a Pareto treatment.",
            "dataset_id": (
                "irelandqci-toshiba-coexistence; gavignet-dwdm-published-aggregate"
            ),
            "result": (
                "all three Gavignet multispan means are four-objective non-dominated; "
                "conventional TriHead Pareto recovery does not beat condition permutation; "
                "the exploratory Pareto-aware ordering result is tracked separately"
            ),
            "boundary": (
                "exploratory reused predictions and published aggregates; decision-layer "
                "justification, not validated predictive optimization"
            ),
        },
    ])


def write_qc_peb_artifacts(output_dir: str | Path) -> dict:
    directory = Path(output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    matrix = build_qc_peb_capability_matrix()
    errors = validate_qc_peb_capability_matrix(matrix)
    if errors:
        raise ValueError("; ".join(errors))
    ledger = build_qc_peb_evidence_ledger(matrix)
    matrix_path = directory / "dataset_capability_matrix.csv"
    ledger_path = directory / "evidence_ledger.csv"
    report_path = directory / "report.json"
    matrix.to_csv(matrix_path, index=False)
    ledger.to_csv(ledger_path, index=False)
    report = {
        "study": "Quantum-Classical Predictive Evidence Base (QC-PEB)",
        "dataset_count": int(len(matrix)),
        "capability_validation_errors": errors,
        "decisive_claim_supported_dataset_count": int(
            matrix["supports_incremental_wdm_quantum_forecast"].sum()
            - matrix["dataset_id"].eq("strict-paired-quantum-wdm-profile").sum()
        ),
        "central_conclusion": (
            "No currently available dataset in the evidence base jointly supplies real "
            "time-varying WDM telemetry and a measured future quantum target. Datasets are "
            "therefore analyzed in separate evidence roles and are never row-joined."
        ),
        "invalid_operations": [
            "Do not join unrelated WDM and quantum datasets by synthetic timestamps.",
            "Do not relabel QBER or communication rate as Bell fidelity.",
            "Do not count the desired acquisition profile as observed evidence.",
            "Do not interpret optical-only forecasting as quantum prediction.",
        ],
        "git": git_state(),
    }
    report_path.write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    artifacts = [matrix_path, ledger_path, report_path]
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
    "build_qc_peb_capability_matrix",
    "build_qc_peb_evidence_ledger",
    "validate_qc_peb_capability_matrix",
    "write_qc_peb_artifacts",
]
