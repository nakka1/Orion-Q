import json

import numpy as np
import pandas as pd

from quantum_twin.evaluation.bristol_hcf_coexistence import (
    BristolHCFCoexistenceConfig,
    compute_link_pair_effects,
    compute_primary_results,
    compute_switch_pair_effects,
    run_bristol_hcf_coexistence_campaign,
    write_bristol_hcf_coexistence_artifacts,
)
from quantum_twin.quantum.bristol_hcf_coexistence import (
    BRISTOL_HCF_LINKS,
    BristolHCFCoexistenceSource,
)


def _write_hcf_release(tmp_path, *, pairs=8):
    directory = tmp_path / "4UserNetworkData"
    directory.mkdir(parents=True, exist_ok=True)
    qber_base = {"AB": 0.04, "AC": 0.05, "AD": 0.06,
                 "BC": 0.045, "BD": 0.055, "CD": 0.065}
    log_skr_base = {"AB": 0.7, "AC": 0.5, "AD": 0.4,
                    "BC": 2.0, "BD": 2.5, "CD": 2.2}
    rows = []
    for chunk in range(2 * pairs):
        active = chunk % 2 == 0
        pair = chunk // 2
        row = {
            "chunk": chunk,
            "Coexistence": "On" if active else "Off",
            "Counts A": 10_000.0 if active else 5_000.0,
            "Counts B": 100_000.0,
            "Counts C": 110_000.0,
            "Counts D": 120_000.0,
        }
        for number, link in enumerate(BRISTOL_HCF_LINKS):
            alice_link = link.startswith("A")
            pair_variation = (pair % 3 - 1) * 0.0003
            off_qber = qber_base[link] + pair * 0.0001
            qber_harm = (0.018 + pair_variation) if alice_link else 0.002
            qber = off_qber + qber_harm if active else off_qber
            if link == "AD" and chunk == 0:
                qber = np.nan
            row[f"QBER {link}"] = qber
            row[f"QBER {link} err"] = np.nan if np.isnan(qber) else 0.001
            off_log_skr = log_skr_base[link] - pair * 0.002
            log_harm = (0.12 + 10.0 * pair_variation) if alice_link else 0.01
            log_skr = off_log_skr - log_harm if active else off_log_skr
            row[f"SKR {link}"] = np.expm1(log_skr)
        rows.append(row)
    pd.DataFrame(rows).to_csv(directory / "4U_OnOff.csv", index=False)
    return tmp_path


def test_bristol_hcf_adapter_preserves_relative_order_and_target_provenance(tmp_path):
    source = BristolHCFCoexistenceSource(_write_hcf_release(tmp_path))
    frame = source.read()

    assert len(frame) == 16 * 6
    assert source.validate(frame).is_valid
    assert frame["relative_chunk_index"].nunique() == 16
    assert "timestamp_utc" not in frame
    assert "osnr_db" not in frame
    assert "ber" not in frame
    assert frame["qber_measured"].isna().sum() == 1
    assert frame.loc[frame["link_id"].eq("AB"), "alice_link"].eq(1).all()
    assert frame.loc[frame["link_id"].eq("BC"), "alice_link"].eq(0).all()
    provenance = frame.attrs["telemetry_provenance"]
    assert provenance["source_type"] == "recorded_hardware"
    assert provenance["bell_fidelity_present"] is False
    assert provenance["timestamp_present"] is False
    assert provenance["time_varying_wdm_telemetry_present"] is False


def test_bristol_hcf_validation_rejects_duplicate_chunk_link(tmp_path):
    source = BristolHCFCoexistenceSource(_write_hcf_release(tmp_path))
    frame = source.read()
    duplicated = pd.concat([frame, frame.iloc[[0]]], ignore_index=True)
    validation = source.validate(duplicated)

    assert not validation.is_valid
    assert any(issue.issue_type == "duplicate_measurement" for issue in validation.issues)


def test_switch_pair_did_recovers_exposed_quantum_degradation(tmp_path):
    frame = BristolHCFCoexistenceSource(_write_hcf_release(tmp_path)).read()
    link_effects = compute_link_pair_effects(frame)
    pair_effects = compute_switch_pair_effects(link_effects)
    results = compute_primary_results(
        pair_effects,
        config=BristolHCFCoexistenceConfig(bootstrap_replicates=500, seed=7),
    )

    pairs = results.set_index("target")["complete_switch_pairs"].to_dict()
    assert pairs == {"qber_measured": 7, "log1p_secure_key_rate": 8}
    assert (results["mean_did_degradation"] > 0.0).all()
    assert results["corrected_positive_evidence"].all()
    assert (results["bonferroni_adjusted_p_value"] <= 0.05).all()


def test_bristol_hcf_campaign_writes_auditable_artifacts(tmp_path):
    dataset = _write_hcf_release(tmp_path / "dataset")
    result = run_bristol_hcf_coexistence_campaign(
        dataset,
        config=BristolHCFCoexistenceConfig(bootstrap_replicates=200, seed=11),
    )
    assert result.report["primary_positive_count"] == 2
    assert "future prediction" in result.report["not_claimed"][3]
    assert result.report["dataset"]["provenance"]["bell_fidelity_present"] is False

    output = tmp_path / "outputs"
    manifest = write_bristol_hcf_coexistence_artifacts(
        result, output, make_plots=False
    )
    report = json.loads((output / "report.json").read_text(encoding="utf-8"))
    assert report["dataset"]["doi"] == "10.5523/bris.61193flfovbn2puknndsr3qm5"
    assert (output / "switch_pair_effects.csv").exists()
    assert len(manifest["artifacts"]) == 6
