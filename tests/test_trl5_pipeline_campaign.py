"""Production runtime witness and contract negatives; no GUI or physical device."""
from copy import deepcopy
import json

import pytest

from tools.run_trl5_pipeline_campaign import FAULTS, SCHEMA, campaign_fingerprint, run_campaign, verify_receipts

pytestmark = pytest.mark.unit


@pytest.fixture(scope="module")
def campaign(tmp_path_factory):
    destination = tmp_path_factory.mktemp("trl5-pipeline") / "campaign"
    report = run_campaign(destination)
    return destination, report


def test_integrated_campaign_stays_within_explicit_software_scope(campaign):
    directory, report = campaign
    assert report["passed"], (report["errors"], {key: value for key, value in report["checks"].items() if not value})
    assert json.loads((directory / "report.json").read_text(encoding="utf-8")) == report
    assert report["schema"] == SCHEMA
    assert report["coverage"]["frontend"] == "snapshot_contract_only"
    assert report["coverage"]["full_global_network"] is False
    assert report["physical_action_performed"] is False
    assert report["operator_state_accessed"] is False
    assert report["environment"]["gui_started"] is False
    assert report["ml_control_enabled"] is False
    assert all(type(value) is bool for value in report["checks"].values())


def test_nominal_repeated_scientific_results_are_deterministic(campaign):
    _, report = campaign
    assert report["reproducibility"]["first"] == report["reproducibility"]["second"]
    assert report["checks"]["nominal.five_technologies_processed"]
    assert report["checks"]["nominal.delivery_outcome_assessed"]
    communication = report["communication"]["end_to_end_entanglement"]
    assert communication["demonstrated"] is (communication["delivered_pairs"] > 0)
    if not communication["demonstrated"]:
        assert communication["impediment"]
        assert communication["rejection_evidence"]


def test_campaign_fingerprint_excludes_host_paths_and_timing_but_detects_outcome_changes():
    report = {"seed": 4, "duration_simulated_s": 35,
              "scenarios": {"nominal": {"scientific_fingerprint": "abc", "event_counts": {"acquired": 2},
                                          "completed_commands": 2, "snapshot_path": "one/snapshot.json"}},
              "elapsed_s": 1, "environment": {"machine": "one"}}
    changed = deepcopy(report)
    changed["elapsed_s"] = 99
    changed["environment"] = {"machine": "two"}
    changed["scenarios"]["nominal"]["snapshot_path"] = "two/snapshot.json"
    assert campaign_fingerprint(changed) == campaign_fingerprint(report)
    changed["scenarios"]["nominal"]["completed_commands"] = 1
    assert campaign_fingerprint(changed) != campaign_fingerprint(report)


def test_degradation_and_outage_change_backend_results(campaign):
    _, report = campaign
    assert report["checks"]["degraded.physical_losses_reduce_transmission"]
    assert report["checks"]["unavailable.no_success_without_observations"]
    assert report["checks"]["unavailable.explicit_timeout_and_retry"]
    assert report["checks"]["recovery.outage_then_causal_resumption"]


def test_synthetic_file_replay_preserves_results_without_claiming_measurement(campaign):
    _, report = campaign
    assert report["checks"]["replay.identical_processing_and_scientific_results"]
    assert report["replay"]["original_origin"] == "simulated"
    assert report["replay"]["replay_transport_origin"] == "measurement_file"
    assert report["replay"]["physical_measurement"] is False


@pytest.mark.parametrize("fault", tuple(FAULTS))
def test_faults_never_publish_protocol_success(campaign, fault):
    _, report = campaign
    result = report["negative_cases"][fault]
    assert result["passed"], result
    assert result["expected_code"] in result["observed_codes"]
    assert result["completed_commands"] == 0


@pytest.mark.parametrize("scenario", ("nominal", "degraded", "unavailable", "recovery"))
def test_receipt_chain_detects_corruption_and_missing_records(campaign, scenario):
    directory, _ = campaign
    rows = [json.loads(line) for line in (directory / scenario / "receipts.jsonl").read_text(encoding="utf-8").splitlines()]
    assert verify_receipts(rows)
    corrupted = deepcopy(rows)
    corrupted[-1]["at_s"] += 1
    assert not verify_receipts(corrupted)
    assert not verify_receipts(rows[1:])


def test_existing_directory_is_not_modified(tmp_path):
    sentinel = tmp_path / "keep.txt"
    sentinel.write_text("preserve", encoding="utf-8")
    with pytest.raises(FileExistsError):
        run_campaign(tmp_path)
    assert sentinel.read_text(encoding="utf-8") == "preserve"
    assert sorted(path.name for path in tmp_path.iterdir()) == ["keep.txt"]


@pytest.mark.parametrize("parameters", ({"seed": -1}, {"seed": True}, {"seconds": 29},
                                       {"seconds": 61}, {"timeout_s": 0}, {"timeout_s": True}))
def test_bad_parameters_are_rejected_before_creating_evidence(tmp_path, parameters):
    destination = tmp_path / "not-created"
    with pytest.raises(ValueError):
        run_campaign(destination, **parameters)
    assert not destination.exists()
