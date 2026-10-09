import json

import pandas as pd

from quantum_twin.evaluation.cross_platform_qkd_replication import (
    CrossPlatformQKDReplicationConfig,
    run_cross_platform_qkd_replication_campaign,
    write_cross_platform_qkd_replication_artifacts,
)
from quantum_twin.evaluation.optical_campaign import sha256_file


def _manifest(directory, names):
    payload = {
        "artifacts": [
            {
                "file": name,
                "sha256": sha256_file(directory / name),
                "bytes": (directory / name).stat().st_size,
            }
            for name in names
        ]
    }
    (directory / "artifact_manifest.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )


def _write_campaign_artifacts(tmp_path):
    ireland = tmp_path / "ireland"
    bristol = tmp_path / "bristol"
    ireland.mkdir(parents=True)
    bristol.mkdir(parents=True)
    cells = []
    ireland_effects = {
        "qber_reported": [],
        "log1p_secure_key_rate": [],
    }
    for index in range(6):
        stratum = f"s{index}"
        qber_effect = 0.01 + index * 0.001
        skr_effect = 0.10 + index * 0.01
        ireland_effects["qber_reported"].append(qber_effect)
        ireland_effects["log1p_secure_key_rate"].append(skr_effect)
        cells.append({
            "stratum_id": stratum, "noise_level": "baseline",
            "qber_reported": 0.03 + index * 0.001,
            "log1p_secure_key_rate": 2.0 - index * 0.01,
        })
        for power in (3, 7, 9, 12):
            cells.append({
                "stratum_id": stratum, "noise_level": str(power),
                "qber_reported": 0.03 + index * 0.001 + qber_effect,
                "log1p_secure_key_rate": 2.0 - index * 0.01 - skr_effect,
            })
    pd.DataFrame(cells).to_csv(ireland / "experimental_cells.csv", index=False)
    ireland_primary = pd.DataFrame([
        {
            "target": target, "matched_strata": 6,
            "mean_degradation": sum(values) / len(values), "corrected_p": 0.03,
            "significant_measured_quantum_degradation": True,
        }
        for target, values in ireland_effects.items()
    ])
    ireland_primary.to_csv(ireland / "primary_matched_contrasts.csv", index=False)
    _manifest(ireland, ("experimental_cells.csv", "primary_matched_contrasts.csv"))

    pairs = []
    bristol_effects = {
        "qber_measured": [],
        "log1p_secure_key_rate": [],
    }
    for index in range(6):
        for target, base, step, unit in (
            ("qber_measured", 0.02, 0.001, "QBER proportion"),
            ("log1p_secure_key_rate", 0.08, 0.01, "log1p(bit/s)"),
        ):
            effect = base + step * index
            bristol_effects[target].append(effect)
            pairs.append({
                "target": target,
                "target_unit": unit,
                "switch_pair_index": index,
                "difference_in_differences_degradation": effect,
            })
    pd.DataFrame(pairs).to_csv(bristol / "switch_pair_effects.csv", index=False)
    bristol_primary = pd.DataFrame([
        {
            "target": target, "complete_switch_pairs": 6,
            "mean_did_degradation": sum(values) / len(values),
            "bonferroni_adjusted_p_value": 0.03,
            "corrected_positive_evidence": True,
        }
        for target, values in bristol_effects.items()
    ])
    bristol_primary.to_csv(bristol / "primary_results.csv", index=False)
    _manifest(bristol, ("switch_pair_effects.csv", "primary_results.csv"))
    return ireland, bristol


def test_cross_platform_campaign_uses_study_units_and_partial_conjunction(tmp_path):
    ireland, bristol = _write_campaign_artifacts(tmp_path)
    result = run_cross_platform_qkd_replication_campaign(
        ireland, bristol,
        config=CrossPlatformQKDReplicationConfig(familywise_alpha=0.05),
    )

    assert len(result.unit_effects) == 24
    assert set(result.study_summaries["study_units"]) == {6}
    assert result.replication_results["both_study_means_positive"].all()
    assert result.replication_results["replicated_directional_evidence"].all()
    assert result.replication_results[
        "holm_adjusted_partial_conjunction_p"
    ].eq(0.03125).all()
    assert result.replication_results["not_a_wdm_forecast"].all()
    assert not result.leave_one_study_out["replication_identifiable"].any()
    assert not result.replication_results["common_magnitude_supported"].any()
    assert result.report["analysis_level"].startswith("study-specific")


def test_cross_platform_campaign_detects_tampered_input_and_writes_artifacts(tmp_path):
    ireland, bristol = _write_campaign_artifacts(tmp_path / "input")
    result = run_cross_platform_qkd_replication_campaign(ireland, bristol)
    output = tmp_path / "output"
    manifest = write_cross_platform_qkd_replication_artifacts(result, output)

    assert len(manifest["artifacts"]) == 6
    assert (output / "replication_results.csv").exists()
    report = json.loads((output / "report.json").read_text(encoding="utf-8"))
    assert report["replicated_outcome_count"] == 2

    with (ireland / "experimental_cells.csv").open("a", encoding="utf-8") as stream:
        stream.write("tampered\n")
    try:
        run_cross_platform_qkd_replication_campaign(ireland, bristol)
    except ValueError as error:
        assert "hash mismatch" in str(error)
    else:
        raise AssertionError("expected input-integrity failure")
