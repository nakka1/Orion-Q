"""Qualification scope, evidence preservation and real ingress integration."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tools"))
from space_validation_campaign import run_campaign  # noqa: E402

pytestmark = pytest.mark.unit


def test_campaign_preserves_existing_evidence(tmp_path):
    sentinel = tmp_path / "original.txt"
    sentinel.write_text("preserve")
    with pytest.raises(FileExistsError):
        run_campaign(tmp_path, seeds=(7,))
    assert sentinel.read_text() == "preserve"


@pytest.mark.parametrize("seeds", [(), (1, 1), (-1,), (True,)])
def test_campaign_records_bad_seed_configuration(tmp_path, seeds):
    with pytest.raises(ValueError):
        run_campaign(tmp_path, seeds=seeds)
    report = json.loads((tmp_path / "report.json").read_text())
    assert report["status"] == "error"
    assert not report["physical_actuation"]


def test_campaign_replays_raw_observations_preserves_scientific_gaps(tmp_path):
    report = run_campaign(tmp_path, seeds=(7,))
    assert report["status"] == "partial_verified"
    assert all(c["passed"] for c in report["checks"])
    assert len(report["fidelity_gaps"]) == 7
    assert all(not report[k] for k in ("mission_ready", "security_certified", "physical_hardware_validated",
                                      "physical_actuation", "coherent_orbital_relay_validated"))
    assert len(report["author_tree_hashes"]) == 67
    captures = [c for c in report["cases"] if c["group"] == "backend_capture"]
    assert len(captures) == 15
    assert {c["technology"] for c in captures} == {"satellite_doppler", "satellite_isl"}
    assert all(c.get("certified_secret_bits", 0) == 0 for c in captures)
    assert all(c["coincidences"] == 0 for c in captures if c["case_id"].endswith(("cloud-blocked", "earth-obstructed", "pointing-disabled")))
    invalid = [c for c in report["cases"] if c["group"] == "invalid-input"]
    assert len(invalid) == 8 and all(c["expected_error"] == c["observed_error"] for c in invalid)
