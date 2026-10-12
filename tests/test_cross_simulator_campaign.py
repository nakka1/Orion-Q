"""Adversarial evaluator fixtures are not simulated physical evidence."""
from copy import deepcopy
import importlib.util
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
spec = importlib.util.spec_from_file_location("cross_simulator_campaign_contract", ROOT / "tools/run_cross_simulator_campaign.py")
campaign = importlib.util.module_from_spec(spec)
spec.loader.exec_module(campaign)


def plan():
    return campaign.load(campaign.DEFAULT_PROTOCOL)


def fixture_report(protocol, backend, seed):
    """Discardable manufactured rows solely to attack evaluator acceptance."""
    request = campaign.payload(protocol, seed)
    metadata = {"seed": seed, "request_sha256": campaign.digest(request), "commit": protocol["sequence_commit"],
                "source_sha256": {"fixture.py": "fixture-only"}, "shims": [], "product_physics_imported": False}
    rows, originals = [], []
    for case in request["cases"]:
        metrics = {metric: .8 if metric != "delay_s" else .001 for metric in campaign.METRICS[case["kind"]]}
        if case["id"] == "loss_100km":
            metrics["transmission_probability"] = .01
        originals.append({"id": case["id"], "kind": case["kind"], "metrics": dict(metrics)})
        if case["kind"] in {"transmission", "purification"}:
            metrics["trials"] = case["parameters"]["trials"]
            name = "transmission_probability" if case["kind"] == "transmission" else "success_probability"
            metrics["transmitted" if case["kind"] == "transmission" else "accepted"] = round(metrics["trials"]*metrics[name])
        rows.append({**case, "status": "pass", "metrics": metrics,
                     "units": {name: "s" if name == "delay_s" else "1" for name in metrics},
                     "events": [{"event": "discardable-evaluator-fixture"}], "limitations": []})
    return {"schema": "orionq.cross-simulator-output.v1", "backend": backend, "seed": seed,
            "metadata": metadata, "cases": rows}, originals


def test_frozen_protocol_has_exact_types_and_prespecified_family():
    protocol = plan()
    campaign.protocol_validate(protocol)
    assert len(protocol["cases"]) == 22
    assert len(protocol["seeds"]) == 3
    assert protocol["statistical_family_size"] == 20


@pytest.mark.parametrize("change", ["seed", "family", "nan", "margin", "noise", "fiber_speed", "threshold"])
def test_invalid_plan_rejected_before_external_execution(change):
    protocol = plan()
    if change == "seed":
        protocol["seeds"][0] = True
    elif change == "family":
        protocol["statistical_family_size"] = 10
    elif change == "nan":
        protocol["deterministic_absolute_tolerance"] = float("nan")
    elif change == "margin":
        protocol["equivalence_margins"]["undeclared"] = .04
    elif change == "noise":
        next(case for case in protocol["cases"] if case["kind"] == "purification")["parameters"]["operation_error"] = .01
    elif change == "threshold":
        next(case for case in protocol["cases"] if case["kind"] == "purification")["parameters"]["first"] = .5
    else:
        next(case for case in protocol["cases"] if case["kind"] == "classical_delay")["parameters"]["light_speed_km_s"] = 200000.
    with pytest.raises(ValueError):
        campaign.protocol_validate(protocol)


@pytest.mark.parametrize("change", ["schema", "backend", "seed", "commit", "shim", "missing", "duplicate", "parameters", "nan", "units", "counts", "causality", "unexecuted_metrics", "no_events"])
def test_invalid_reference_evidence_is_not_accepted(change):
    protocol = plan()
    seed = protocol["seeds"][0]
    report, _ = fixture_report(protocol, "sequence", seed)
    row = report["cases"][0]
    if change in {"schema", "backend"}:
        report[change] = "wrong"
    elif change == "seed":
        report["seed"] += 1
    elif change == "commit":
        report["metadata"]["commit"] = "wrong"
    elif change == "shim":
        report["metadata"]["shims"] = ["substitute"]
    elif change == "missing":
        report["cases"].pop()
    elif change == "duplicate":
        report["cases"][1] = deepcopy(row)
    elif change == "parameters":
        row["parameters"] = {}
    elif change == "nan":
        row["metrics"]["transmission_probability"] = float("nan")
    elif change == "units":
        row["units"]["transmission_probability"] = "dB"
    elif change == "counts":
        row["metrics"]["transmitted"] = True
    elif change == "causality":
        next(case for case in report["cases"] if case["kind"] == "classical_delay")["metrics"]["received_before_arrival"] = 1
    elif change == "unexecuted_metrics":
        row["status"] = "not_demonstrated"
    else:
        row["events"] = []
    with pytest.raises(ValueError):
        campaign.validate_result(report, "sequence", campaign.payload(protocol, seed))


def reports(protocol, backend="sequence"):
    result = [fixture_report(protocol, backend, seed)[0] for seed in protocol["seeds"]]
    return result, fixture_report(protocol, backend, protocol["seeds"][0])[1]


def test_exact_probabilities_and_observations_have_separate_criteria():
    protocol = plan()
    references, original = reports(protocol)
    result = campaign.compare(protocol, "sequence", references, original)
    assert result["status"] == "pass"
    assert result["passed_cases"] == 22
    assert result["cases"][0]["statistical_equivalence"]["trials"] == 6144
    references[1]["cases"][1]["metrics"]["transmission_probability"] += 1e-5
    assert campaign.compare(protocol, "sequence", references, original)["status"] == "fail"


def test_netsquid_sample_proportion_not_compared_with_deterministic_tolerance():
    protocol = plan()
    references, original = reports(protocol, "netsquid")
    for report in references:
        report["cases"][0]["metrics"]["transmission_probability"] = .8001
    result = campaign.compare(protocol, "netsquid", references, original)
    assert result["status"] == "pass"
    assert result["cases"][0]["deterministic_checks"] == []


def test_non_significant_or_matching_mean_does_not_prove_equivalence():
    protocol = plan()
    protocol["trials_per_seed"] = 10
    references, original = reports(protocol)
    result = campaign.compare(protocol, "sequence", references, original)
    assert result["status"] == "fail"
    assert not result["cases"][0]["statistical_equivalence"]["passed"]


def test_missing_sdk_or_one_missing_repeat_remains_not_demonstrated():
    protocol = plan()
    references, original = reports(protocol, "netsquid")
    for report in references:
        for row in report["cases"]:
            row.update(status="not_demonstrated", metrics={}, events=[])
    result = campaign.compare(protocol, "netsquid", references, original)
    assert result["status"] == "not_demonstrated"
    assert result["passed_cases"] == 0
    with pytest.raises(ValueError):
        campaign.compare(protocol, "netsquid", references[:1], original)


def test_wrong_repeat_binding_refused_for_netsquid():
    protocol = plan()
    report, _ = fixture_report(protocol, "netsquid", protocol["seeds"][0])
    with pytest.raises(ValueError, match="request/seed"):
        campaign.validate_result(report, "netsquid", campaign.payload(protocol, protocol["seeds"][1]))


def test_low_transmission_probability_does_not_accept_zero_received_events():
    protocol = plan()
    references, original = reports(protocol)
    for report in references:
        next(case for case in report["cases"] if case["id"] == "loss_100km")["metrics"]["transmitted"] = 0
    result = campaign.compare(protocol, "sequence", references, original)
    assert next(case for case in result["cases"] if case["id"] == "loss_100km")["status"] == "fail"


@pytest.mark.parametrize("value,accepted", [(1.0000000000000002, True), (-1e-16, True), (1.00000001, False), (-1e-8, False)])
def test_roundoff_is_distinct_from_out_of_domain_scientific_value(value, accepted):
    protocol = plan()
    seed = protocol["seeds"][0]
    report, _ = fixture_report(protocol, "sequence", seed)
    report["cases"][0]["metrics"]["transmission_probability"] = value
    if accepted:
        campaign.validate_result(report, "sequence", campaign.payload(protocol, seed))
    else:
        with pytest.raises(ValueError):
            campaign.validate_result(report, "sequence", campaign.payload(protocol, seed))


@pytest.mark.parametrize("successes,trials", [(True, 10), (1, 0), (-1, 10), (11, 10)])
def test_invalid_statistical_event_count(successes, trials):
    with pytest.raises(ValueError):
        campaign.interval(successes, trials, .001)


def test_confidence_endpoint_is_conservative():
    low, high = campaign.interval(6144, 6144, .00005)
    assert .99 < low < 1 and high == 1


def test_json_duplicate_and_nonfinite_rejected(tmp_path):
    path = tmp_path / "invalid.json"
    for text in ('{"a":1,"a":2}', '{"a":NaN}'):
        path.write_text(text)
        with pytest.raises(ValueError):
            campaign.load(path)


def test_prepare_is_immutable_and_detects_source_changed_during_freeze(tmp_path, monkeypatch):
    monkeypatch.setattr(campaign, "ROOT", tmp_path)
    protocol_path = tmp_path / "config.json"
    campaign.write(protocol_path, plan())
    calls = iter(({"one.py": "a"}, {"one.py": "b"}))
    monkeypatch.setattr(campaign, "source_inventory", lambda: next(calls))
    output = tmp_path / "attempt"
    with pytest.raises(ValueError, match="changed during freeze"):
        campaign.prepare(output, protocol_path)
    assert output.is_dir() and not (output / "freeze.json").exists()
    monkeypatch.setattr(campaign, "source_inventory", lambda: {"one.py": "a"})
    with pytest.raises(FileExistsError):
        campaign.prepare(output, protocol_path)
