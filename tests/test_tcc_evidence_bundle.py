import hashlib
import json
from pathlib import Path

import pandas as pd
import pytest

from quantum_twin.evaluation.tcc_evidence_bundle import build_tcc_evidence_bundle
from quantum_twin.evaluation.thesis_claims import validate_claim_registry


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_source_registry(root: Path, artifact: str) -> Path:
    evidence_path = root / artifact
    evidence_path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame([
        {"target": "qber_reported", "mean_degradation": 0.013, "passed": True},
        {"target": "unused", "mean_degradation": -99.0, "passed": False},
    ]).to_csv(evidence_path, index=False)
    registry_path = root / "docs" / "thesis" / "tcc_claim_registry.json"
    registry_path.parent.mkdir(parents=True, exist_ok=True)
    registry_path.write_text(json.dumps({
        "schema_version": "1.2.0",
        "formalized_at": "2026-09-02",
        "formalization_status": "test_fixture",
        "headline_claim": {"claim_id": "C1", "text": "fixture"},
        "claims": [{
            "id": "C1",
            "role": "fixture",
            "statement": "fixture claim",
            "status": "supported_exploratory",
            "estimand": "fixture estimand",
            "evidence": [{
                "id": "E1",
                "artifact": artifact,
                "sha256": _sha256(evidence_path),
                "selector": {"target": "qber_reported"},
                "assertions": [
                    {
                        "field": "mean_degradation",
                        "operator": "greater_than",
                        "expected": 0.0,
                    },
                    {"field": "passed", "operator": "equals", "expected": True},
                ],
            }],
            "boundaries": ["fixture boundary"],
            "permitted_language": ["fixture allowed"],
            "forbidden_language": ["fixture forbidden"],
            "product_behavior": {"badge": "TEST", "automation_allowed": False},
        }],
    }), encoding="utf-8")
    return registry_path


def test_bundle_remains_valid_without_original_outputs(tmp_path):
    artifact = "outputs/qkd_coexistence_measured/primary_matched_contrasts.csv"
    source_registry = _write_source_registry(tmp_path, artifact)
    result = build_tcc_evidence_bundle(
        source_registry,
        repository_root=tmp_path,
        generated_at="2026-09-03",
    )

    (tmp_path / artifact).unlink()
    validation = validate_claim_registry(
        result.portable_registry_path, repository_root=tmp_path
    )
    assert validation.report["valid"] is True
    assert result.table_count == 1
    assert result.selected_row_count == 1

    portable = json.loads(result.portable_registry_path.read_text(encoding="utf-8"))
    bundled_artifact = portable["claims"][0]["evidence"][0]["artifact"]
    compact = pd.read_csv(tmp_path / bundled_artifact)
    assert compact["target"].tolist() == ["qber_reported"]


def test_bundle_records_content_and_licence_boundaries(tmp_path):
    artifact = "outputs/qkd_coexistence_measured/primary_matched_contrasts.csv"
    source_registry = _write_source_registry(tmp_path, artifact)
    result = build_tcc_evidence_bundle(
        source_registry,
        repository_root=tmp_path,
    )

    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    boundaries = manifest["content_boundaries"]
    assert boundaries["contains_raw_measurements"] is False
    assert boundaries["contains_model_weights"] is False
    assert boundaries["contains_only_selector_matched_aggregate_rows"] is True

    provenance = json.loads(
        (result.bundle_dir / "provenance.json").read_text(encoding="utf-8")
    )
    source = provenance["artifacts"][0]["source_datasets"][0]
    assert source["doi"] == "10.5281/zenodo.21132088"
    assert source["license"] == "CC-BY-4.0"


def test_bundle_rejects_artifact_without_explicit_licence_policy(tmp_path):
    artifact = "outputs/unknown-study/results.csv"
    source_registry = _write_source_registry(tmp_path, artifact)
    with pytest.raises(ValueError, match="no explicit dataset licence policy"):
        build_tcc_evidence_bundle(source_registry, repository_root=tmp_path)
