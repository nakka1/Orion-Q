import hashlib
import json
from pathlib import Path

import pandas as pd

from quantum_twin.evaluation.thesis_claims import (
    load_claim_registry,
    validate_claim_registry,
    write_claim_validation_artifacts,
)


SHIPPED_REGISTRY = Path("docs/thesis/tcc_claim_registry.json")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_registry(root: Path) -> Path:
    evidence = root / "outputs" / "result.csv"
    evidence.parent.mkdir(parents=True)
    pd.DataFrame([{"metric": "ordering", "value": 0.6, "passed": True}]).to_csv(
        evidence, index=False
    )
    registry = root / "docs" / "thesis" / "claims.json"
    registry.parent.mkdir(parents=True)
    registry.write_text(json.dumps({
        "schema_version": "1.0.0",
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
                "artifact": "outputs/result.csv",
                "sha256": _sha256(evidence),
                "selector": {"metric": "ordering"},
                "assertions": [
                    {"field": "value", "operator": "approx", "expected": 0.6},
                    {"field": "passed", "operator": "equals", "expected": True},
                ],
            }],
            "boundaries": ["fixture boundary"],
            "permitted_language": ["fixture allowed"],
            "forbidden_language": ["fixture forbidden"],
            "product_behavior": {"badge": "TEST", "automation_allowed": False},
        }],
    }), encoding="utf-8")
    return registry


def test_shipped_claim_registry_schema_is_valid():
    payload = load_claim_registry(SHIPPED_REGISTRY)
    assert payload["headline_claim"]["claim_id"] == "C-MEASURED"
    assert {claim["id"] for claim in payload["claims"]} == {
        "H-ORIGINAL", "C-MEASURED", "L-SESSION", "L-DOSE", "L-PARETO",
        "L-TRANSPORT", "B-FIDELITY",
    }


def test_shipped_claim_registry_is_portable_and_evidence_validates():
    payload = load_claim_registry(SHIPPED_REGISTRY)
    assert payload["evidence_bundle"]["portable"] is True
    evidence = [item for claim in payload["claims"] for item in claim["evidence"]]
    assert evidence
    assert all(item["artifact"].startswith("evidence/tcc/") for item in evidence)
    result = validate_claim_registry(SHIPPED_REGISTRY, repository_root=Path("."))
    assert result.report["valid"] is True


def test_claim_registry_validates_hashes_selectors_and_assertions(tmp_path):
    registry = _write_registry(tmp_path)
    result = validate_claim_registry(registry, repository_root=tmp_path)
    assert result.report["valid"] is True
    assert result.report["failed_check_count"] == 0
    output = tmp_path / "validation"
    manifest = write_claim_validation_artifacts(result, output)
    assert len(manifest["artifacts"]) == 2
    assert (output / "artifact_manifest.json").exists()


def test_claim_registry_detects_evidence_drift(tmp_path):
    registry = _write_registry(tmp_path)
    (tmp_path / "outputs" / "result.csv").write_text(
        "metric,value,passed\nordering,0.1,false\n", encoding="utf-8"
    )
    result = validate_claim_registry(registry, repository_root=tmp_path)
    assert result.report["valid"] is False
    assert "sha256" in set(result.checks.loc[~result.checks["valid"], "check"])
