import json

import numpy as np
import pandas as pd

from quantum_twin.evaluation.bristol_hcf_robustness import (
    BristolHCFRobustnessConfig,
    audit_switch_periodicity,
    run_bristol_hcf_robustness_campaign,
    write_bristol_hcf_robustness_artifacts,
)
from quantum_twin.quantum.bristol_hcf_coexistence import BRISTOL_HCF_LINKS


def _write_segmented_hcf_release(tmp_path, *, pairs=9):
    directory = tmp_path / "4UserNetworkData"
    directory.mkdir(parents=True, exist_ok=True)
    qber_base = dict(zip(BRISTOL_HCF_LINKS, (0.04, 0.05, 0.06, 0.045, 0.055, 0.065)))
    log_skr_base = dict(zip(BRISTOL_HCF_LINKS, (0.7, 0.5, 0.4, 2.0, 2.5, 2.2)))
    chunks = list(range(0, 6)) + list(range(18, 24)) + list(range(38, 44))
    rows = []
    for logical_chunk, chunk in enumerate(chunks[: 2 * pairs]):
        active = logical_chunk % 2 == 0
        pair = logical_chunk // 2
        row = {
            "chunk": chunk,
            "Coexistence": "On" if active else "Off",
            "Counts A": 10_000.0 if active else 5_000.0,
            "Counts B": 100_000.0,
            "Counts C": 110_000.0,
            "Counts D": 120_000.0,
        }
        for link in BRISTOL_HCF_LINKS:
            alice_link = link.startswith("A")
            pair_variation = (pair % 3 - 1) * 0.0003
            off_qber = qber_base[link] + pair * 0.0001
            qber_harm = (0.018 + pair_variation) if alice_link else 0.002
            row[f"QBER {link}"] = off_qber + qber_harm if active else off_qber
            row[f"QBER {link} err"] = 0.001
            off_log_skr = log_skr_base[link] - pair * 0.002
            log_harm = (0.12 + 10.0 * pair_variation) if alice_link else 0.01
            row[f"SKR {link}"] = np.expm1(
                off_log_skr - log_harm if active else off_log_skr
            )
        rows.append(row)
    pd.DataFrame(rows).to_csv(directory / "4U_OnOff.csv", index=False)
    return tmp_path


def test_robustness_is_directionally_stable_and_spatially_specific(tmp_path):
    result = run_bristol_hcf_robustness_campaign(
        _write_segmented_hcf_release(tmp_path),
        config=BristolHCFRobustnessConfig(bootstrap_replicates=500, seed=7),
    )

    assert len(result.segment_results) == 6
    assert result.robustness_summary["directionally_stable"].all()
    assert result.robustness_summary["documented_spatial_rank"].eq(1).all()
    assert result.robustness_summary["spatial_assignment_count"].eq(20).all()
    assert result.robustness_summary["spatial_two_sided_tail_fraction"].eq(0.1).all()
    assert (result.leave_one_link_out["mean_did_degradation"] > 0.0).all()
    assert (result.leave_one_pair_out["mean_did_degradation"] > 0.0).all()


def test_periodicity_audit_rejects_invalid_shifted_label_placebo(tmp_path):
    result = run_bristol_hcf_robustness_campaign(
        _write_segmented_hcf_release(tmp_path),
        config=BristolHCFRobustnessConfig(bootstrap_replicates=200),
    )
    audit = audit_switch_periodicity(result.primary.telemetry)

    assert audit["deterministic_alternation_detected"] is True
    assert audit["temporal_permutation_control_identifiable"] is False
    assert audit["lag_audit"][0]["flipped_state_fraction"] == 1.0
    assert audit["lag_audit"][1]["same_state_fraction"] == 1.0


def test_robustness_campaign_writes_auditable_artifacts(tmp_path):
    result = run_bristol_hcf_robustness_campaign(
        _write_segmented_hcf_release(tmp_path / "dataset"),
        config=BristolHCFRobustnessConfig(bootstrap_replicates=200),
    )
    output = tmp_path / "outputs"
    manifest = write_bristol_hcf_robustness_artifacts(result, output)

    assert len(manifest["artifacts"]) == 9
    assert (output / "robustness_summary.csv").exists()
    report = json.loads((output / "report.json").read_text(encoding="utf-8"))
    assert report["directionally_stable_target_count"] == 2
    assert "No new confirmatory p-values" in report["statistical_boundary"]
