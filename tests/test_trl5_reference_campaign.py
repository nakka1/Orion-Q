"""Independent scientific checks must reject bad evidence and preserve scope."""
import csv
import importlib.util
import json
from pathlib import Path
import shutil

import pytest

from quantum_autopilot.quantum_protocols.contracts import Acquisition, ProtocolError
from quantum_autopilot.product import product_version

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("trl5_reference_campaign", ROOT / "tools/run_trl5_reference_campaign.py")
campaign = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(campaign)
pytestmark = pytest.mark.unit


def test_campaign_passes_and_explicitly_does_not_claim_physical_calibration():
    report = campaign.run_campaign()
    assert report["schema"] == "orion.trl5.reference-campaign.v1"
    assert report["passed"] and len(report["checks"]) == 35
    assert report["app_version"] == product_version()
    assert report["experimental_calibration"] == "not_demonstrated"
    assert report["classification"] == "analytic_verification"
    assert report["physical_action"] is False and report["trl5_demonstrated"] is False
    assert all(row["experimental_calibration"] == "not_demonstrated" for row in report["checks"])
    assert {row["reference_ids"][0] for row in report["checks"]} == {"db", "light", "bbpssw", "swapping", "depolarizing", "orbit"}
    assert all(not Path(path).is_absolute() and len(sha) == 64 for path, sha in report["source_hashes"].items())
    assert report["experimental_reference_inventory"][0]["rows"] == 6
    assert all(row["origin"] == "simulated" for row in report["acquisition_summaries"])
    json.dumps(report, allow_nan=False)


def test_same_seed_repeats_scientific_fingerprint():
    first, second = campaign.run_campaign(17), campaign.run_campaign(17)
    assert first["scientific_fingerprint_sha256"] == second["scientific_fingerprint_sha256"]
    assert first["checks"] == second["checks"]
    assert first["acquisition_summaries"] == second["acquisition_summaries"]


def test_production_regression_is_detected(monkeypatch):
    monkeypatch.setattr(campaign.physics, "transmission", lambda *_: 1.)
    report = campaign.run_campaign()
    assert report["passed"] is False
    assert any(not row["passed"] for row in report["checks"] if row["name"].startswith("fibre_power"))


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -float("inf")])
def test_nonfinite_comparison_cannot_pass_or_escape_as_json_nan(value):
    row = campaign.comparison("bad", value, 1., "db", "fixture", {})
    assert row["passed"] is False and row["actual"] is None and row["absolute_error"] is None
    json.dumps(row, allow_nan=False)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), True, -1., 1.1])
def test_invalid_werner_inputs_are_rejected(value):
    with pytest.raises(ValueError):
        campaign.independent_bbpssw(value, .8)


@pytest.mark.parametrize("elapsed,coherence", [(float("nan"), 1.), (-1., 1.), (1., 0.), (1., float("inf"))])
def test_invalid_memory_parameters_are_rejected(elapsed, coherence):
    with pytest.raises(ValueError):
        campaign.independent_memory(.8, elapsed, coherence)


@pytest.mark.parametrize("seed", [-1, True, 2 ** 64, 1.2])
def test_invalid_seed_is_rejected(seed):
    with pytest.raises(ValueError):
        campaign.run_campaign(seed)


def test_independent_cnot_reference_has_ideal_and_known_noisy_results():
    assert campaign.independent_bbpssw(1., 1.) == pytest.approx((1., 1.))
    assert campaign.independent_bbpssw(.8, .8) == pytest.approx((.7688888888888888, .838150289017341))
    assert campaign.independent_memory(.25, 3., 1.) == pytest.approx(.25)
    assert campaign.independent_swap(1., 1.) == pytest.approx(1.)


@pytest.mark.parametrize("change", ["missing", "duplicate", "insecure_url", "untrusted_host", "missing_scope"])
def test_invalid_reference_sources_rejected(tmp_path, change):
    document = campaign.load_references()
    if change == "missing":
        document["references"].pop()
    elif change == "duplicate":
        document["references"].append(document["references"][0])
    elif change == "insecure_url":
        document["references"][0]["url"] = "http://www.nist.gov/example"
    elif change == "untrusted_host":
        document["references"][0]["url"] = "https://example.invalid/fake"
    else:
        document["references"][0]["scope"] = ""
    path = tmp_path / "catalog.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(ValueError):
        campaign.load_references(path)


@pytest.mark.parametrize("raw", [{"final_fidelity": .9}, {"readout": float("nan")}])
def test_acquisition_contract_rejects_precomputed_or_nonfinite_evidence(raw):
    with pytest.raises((ProtocolError, ValueError)):
        Acquisition("case", "reference", "readout", "fixture", "simulated", 1, 1., 0., ("Q",), ("C",), raw)


def test_wrong_source_is_not_accepted_as_reference_evidence():
    command = campaign.DeviceCommand("case", "reference", "readout", ("A", "B"), ("Q",), ("C",), 0., 2., 1)
    acquisition = Acquisition("case", "reference", "readout", "imposter", "simulated", 1, 1., 0.,
                              ("Q",), ("C",), {"readout": 1})
    with pytest.raises(ProtocolError) as exc:
        campaign.validate_binding(acquisition, command, last_sequence=0, source_id="analytic-fixture",
                                  origin="simulated", now_s=1., sync_tolerance_s=1e-6)
    assert exc.value.code == "wrong_source"


@pytest.mark.parametrize("change", ["raw_claim", "missing_provenance", "nonfinite", "missing_rows"])
def test_published_aggregate_invalid_evidence_rejected(tmp_path, change):
    destination = tmp_path / "data/reference"
    destination.mkdir(parents=True)
    shutil.copyfile(ROOT / "data/reference/README.md", destination / "README.md")
    original = ROOT / "data/reference/gavignet_2024_published_aggregate.csv"
    with original.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    fields = tuple(rows[0])
    if change == "raw_claim":
        rows[0]["observation_level"] = "raw_physical_click"
    elif change == "missing_provenance":
        rows[0]["source_location"] = ""
    elif change == "nonfinite":
        rows[0]["qber_percent"] = "nan"
    else:
        rows.pop()
    with (destination / original.name).open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    with pytest.raises(ValueError):
        campaign.aggregate_integrity(tmp_path)


def test_cli_requires_new_output_directory_and_emits_report(tmp_path, capsys):
    output = tmp_path / "new"
    assert campaign.main(["--output-dir", str(output), "--seed", "17"]) == 0
    report = json.loads((output / "report.json").read_text(encoding="utf-8"))
    assert report["passed"] and report["seed"] == 17
    assert json.loads(capsys.readouterr().out)["physical_action"] is False
    with pytest.raises(ValueError, match="new output directory"):
        campaign.main(["--output-dir", str(output)])


def test_cli_output_argument_is_required():
    with pytest.raises(SystemExit) as exc:
        campaign.main([])
    assert exc.value.code == 2
