"""Platform claim registry: closed loops, detector and RTL, checked against the campaign summaries."""
import importlib.util
from pathlib import Path

import pytest

from quantum_twin.evaluation.thesis_claims import load_claim_registry, validate_claim_registry

pytestmark = pytest.mark.unit
ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "docs/thesis/orion_q_platform_claim_registry.json"


def _builder():
    spec = importlib.util.spec_from_file_location("orion_platform_evidence", ROOT / "tools/build_platform_evidence.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_evidence_table_is_rebuilt_from_the_summaries_byte_for_byte():
    builder = _builder()
    assert builder.build() == builder.OUTPUT.read_bytes()


def test_platform_registry_validates_every_check():
    payload = load_claim_registry(REGISTRY)
    assert {claim["id"] for claim in payload["claims"]} == {
        "P-REPRO", "P-SEGURANCA", "P-COMP-LF", "P-COMP-ATU", "P-COMP-PF", "P-BLOC", "P-BLOC-LF", "P-TAXA-LF", "P-TAXA-80", "P-SINC-LF", "P-ALOC-LF",
        "P-ROT-LONGO", "P-ROT-NOMINAL", "P-ROT-SEQ", "P-ROT-GEMEO", "P-DET", "P-DET-CAUSAL", "P-WINDOWS", "P-RTL",
        "P-RTL-DEFEITOS"}
    result = validate_claim_registry(REGISTRY, repository_root=ROOT)
    assert result.report["valid"] is True and result.report["failed_check_count"] == 0
    assert result.report["check_count"] == 795


def test_negative_results_stay_registered_as_such():
    payload = load_claim_registry(REGISTRY)
    status = {claim["id"]: claim["status"] for claim in payload["claims"]}
    assert status["P-TAXA-80"] == "not_supported"
    assert all(claim["product_behavior"]["automation_allowed"] is False for claim in payload["claims"])
