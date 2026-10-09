import json
from pathlib import Path

import pandas as pd

from quantum_twin.evaluation.public_qkd_evidence import (
    build_public_qkd_candidate_registry,
    load_published_aggregate_observations,
    run_public_qkd_evidence_audit,
    validate_public_qkd_candidate_registry,
    write_public_qkd_evidence_artifacts,
)


REFERENCE = Path("data/reference/gavignet_2024_published_aggregate.csv")


def test_registry_blocks_aggregate_sources_from_formal_k3_claim():
    registry = build_public_qkd_candidate_registry()
    assert validate_public_qkd_candidate_registry(registry) == []
    assert registry["dataset_id"].is_unique
    assert registry["formal_k3_effect_eligible"].sum() == 2
    gavignet = registry.set_index("dataset_id").loc["gavignet-2024-dwdm"]
    assert gavignet["aggregate_corroboration_eligible"]
    assert not gavignet["formal_k3_effect_eligible"]
    assert not gavignet["public_machine_readable_data"]


def test_gavignet_aggregate_keeps_condition_means_descriptive():
    result = run_public_qkd_evidence_audit(REFERENCE)
    assert len(result.condition_contrasts) == 2
    assert result.condition_contrasts["qber_degraded"].all()
    assert result.condition_contrasts["skr_degraded"].all()
    diagnostic = result.aggregate_diagnostics.iloc[0]
    assert diagnostic["power_vs_qber_spearman"] == 1.0
    assert diagnostic["power_vs_skr_spearman"] == -1.0
    assert diagnostic["classical_quantum_tradeoff_observed"]
    assert not diagnostic["p_values_computed"]
    assert not diagnostic["formal_replication_unit"]
    assert result.report["new_formal_k3_eligible_platform_count"] == 0
    assert result.report["strict_temporal_prediction_eligible_count"] == 0


def test_public_evidence_writer_hashes_six_audit_artifacts(tmp_path):
    result = run_public_qkd_evidence_audit(REFERENCE)
    manifest = write_public_qkd_evidence_artifacts(result, tmp_path)
    assert len(manifest["artifacts"]) == 6
    report = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))
    assert report["formal_k3_status"].startswith("not reached")
    assert (tmp_path / "search_protocol.json").exists()


def test_loader_rejects_rows_disguised_as_raw_measurements(tmp_path):
    frame = pd.read_csv(REFERENCE)
    frame.loc[0, "observation_level"] = "raw_measurement"
    path = tmp_path / "invalid.csv"
    frame.to_csv(path, index=False)
    try:
        load_published_aggregate_observations(path)
    except ValueError as error:
        assert "published aggregate" in str(error)
    else:
        raise AssertionError("expected aggregate-granularity validation failure")
