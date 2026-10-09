import json

from quantum_twin.evaluation.qc_peb import (
    build_qc_peb_capability_matrix,
    validate_qc_peb_capability_matrix,
    write_qc_peb_artifacts,
)


def test_qc_peb_exposes_the_decisive_gap_without_scoring_datasets():
    matrix = build_qc_peb_capability_matrix()

    assert validate_qc_peb_capability_matrix(matrix) == []
    assert "validity_score" not in matrix.columns
    observed = matrix.loc[
        ~matrix["dataset_id"].eq("strict-paired-quantum-wdm-profile")
    ]
    assert not observed["supports_incremental_wdm_quantum_forecast"].any()
    dryad = matrix.set_index("dataset_id").loc["dryad-stike-168h"]
    assert dryad["measured_qber"]
    assert dryad["supports_quantum_own_history_forecast"]
    assert not dryad["real_wdm_timeseries"]
    assert not dryad["measured_secure_key_rate"]


def test_qc_peb_artifacts_prohibit_cross_dataset_synthetic_join(tmp_path):
    manifest = write_qc_peb_artifacts(tmp_path)
    report = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))

    assert len(manifest["artifacts"]) == 3
    assert report["decisive_claim_supported_dataset_count"] == 0
    assert "never row-joined" in report["central_conclusion"]
    assert "synthetic timestamps" in report["invalid_operations"][0]
