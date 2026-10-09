"""Reviewed recommendations preserve negative results, domains and evidence identity."""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

from quantum_autopilot.control_plane import controller_evidence as evidence

ROOT = Path(__file__).resolve().parents[1]


def profile(slew, model="qnet.compensation.calibrado.ridge"):
    return next(p for p in evidence.load_catalog()["profiles"]
                if p["model_id"] == model and p["scenario"]["slew_rate_rad_per_s"] == slew)


@pytest.mark.parametrize("slew,status", [(30, "BASELINE_RECOMMENDED"), (60, "BASELINE_RECOMMENDED"),
                                        (120, "INCONCLUSIVE"), (250, "MODEL_RECOMMENDED"),
                                        (None, "MODEL_RECOMMENDED")])
def test_real_reviewed_profiles(slew, status):
    selected = profile(slew)
    report = evidence.recommend(evidence.profile_request(selected))
    record = evidence.verify_report(report)
    assert record["status"] == status
    assert record["comparison"] == selected["comparison"]
    assert record["physical_action_performed"] is False and record["policy_activated"] is False
    assert record["weights_verification"] == "historical_identity_only"


@pytest.mark.parametrize("mutation", ["speed", "weights", "latency", "point", "bool", "missing"])
def test_unknown_or_incompatible_profile_is_not_interpolated(mutation):
    request = evidence.profile_request(profile(250))
    if mutation == "speed":
        request["scenario"]["slew_rate_rad_per_s"] = 200.0
    elif mutation == "weights":
        request["checkpoint_sha256"]["npz"] = "0" * 64
    elif mutation == "latency":
        request["scenario"]["latency_s"] = 0.01
    elif mutation == "point":
        request["scenario"]["operating_point"] = "desenvolvimento"
    elif mutation == "bool":
        request["scenario"]["slew_rate_rad_per_s"] = True
    else:
        request["scenario"].pop("supervisor")
    record = evidence.recommend(request)["record"]
    assert record["status"] == "NO_EVIDENCE" and record["comparison"] is None


def test_changed_catalog_and_rehashed_fabricated_report_are_refused(tmp_path):
    raw = evidence.files(evidence.__package__).joinpath("data/controller_evidence_v1.json").read_bytes()
    target = tmp_path / "changed.json"
    target.write_bytes(raw + b" ")
    with pytest.raises(ValueError, match="reviewed catalog"):
        evidence.load_catalog(target)
    report = evidence.recommend(evidence.profile_request(profile(30)))
    forged = copy.deepcopy(report["record"])
    forged["status"] = "MODEL_RECOMMENDED"
    with pytest.raises(ValueError, match="reviewed evidence"):
        evidence.verify_report(evidence.receipt(forged))


def test_export_preserves_statistics_and_refuses_overwrite(tmp_path):
    report = evidence.recommend(evidence.profile_request(profile(120)))
    destination = tmp_path / "export"
    evidence.export_report(report, destination)
    assert json.loads((destination / "comparison.json").read_text(encoding="utf-8")) == report
    catalog = evidence.load_catalog(destination / "controller_evidence_v1.json")
    source_profile = next(p for p in catalog["profiles"] if p["id"] == report["record"]["profile_id"])
    assert source_profile["comparison"] == report["record"]["comparison"]
    text = (destination / "comparison.md").read_text(encoding="utf-8")
    assert "INCONCLUSIVE" in text and "not evidence of equivalence" in text
    manifest = json.loads((destination / "manifest.json").read_text(encoding="utf-8"))
    for name, digest in manifest["files"].items():
        assert hashlib.sha256((destination / name).read_bytes()).hexdigest() == digest
    with pytest.raises(FileExistsError):
        evidence.export_report(report, destination)


def test_catalog_is_derived_from_reviewed_campaigns_byte_for_byte():
    spec = importlib.util.spec_from_file_location("catalog_builder", ROOT / "tools/build_controller_evidence.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    raw = module.build()
    assert raw == module.OUTPUT.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == evidence.CATALOG_SHA256


def test_non_finite_inputs_are_refused():
    request = evidence.profile_request(profile(250))
    request["scenario"]["slew_rate_rad_per_s"] = float("nan")
    with pytest.raises(ValueError):
        evidence.recommend(request)
