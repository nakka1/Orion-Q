"""Numerical and adversarial checks of SNS qualification, not hardware evidence."""
from copy import deepcopy
from dataclasses import replace
import importlib.util
import json
import math
from pathlib import Path
import sys

import pytest
from scipy.optimize import brentq
from scipy.stats import binom

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "tools") not in sys.path:
    sys.path.insert(0, str(ROOT / "tools"))
spec = importlib.util.spec_from_file_location("sns_validation_reference", ROOT / "tools/sns_validation_reference.py")
reference = importlib.util.module_from_spec(spec)
spec.loader.exec_module(reference)
spec = importlib.util.spec_from_file_location("sns_validation_campaign", ROOT / "tools/sns_validation_campaign.py")
campaign = importlib.util.module_from_spec(spec)
spec.loader.exec_module(campaign)


@pytest.fixture
def parameters():
    return json.loads((ROOT / "configs/qualification/sns_validation_v1.json").read_text(encoding="utf-8"))["parameters"]


@pytest.mark.parametrize("value", [-1, True, 1.2, float("nan")])
def test_noninteger_count_is_rejected(value):
    with pytest.raises(ValueError):
        reference.expected_count_interval(value, 100)


@pytest.mark.parametrize("failure", [0, 1, -0.1, True, float("nan")])
def test_invalid_failure_allocation_is_rejected(failure):
    with pytest.raises(ValueError):
        reference.expected_count_interval(1, 100, failure)


@pytest.mark.parametrize("observed,trials", [(1, 100), (30, 100), (100, 100), (2000000, 100000000)])
def test_inverse_chernoff_roots_match_independent_scipy_solver(observed, trials):
    xi = 1e-10
    lo, hi = reference.expected_count_interval(observed, trials, xi)
    def root_function(mu):
        return mu - observed + observed * math.log(observed / mu) - math.log(2 / xi)
    independent_lo = brentq(root_function, observed * 1e-15, observed, xtol=1e-14)
    assert lo == pytest.approx(independent_lo, rel=1e-9, abs=1e-12)
    if hi < trials:
        independent_hi = brentq(root_function, observed, max(trials, observed * 10), xtol=1e-12)
        assert hi == pytest.approx(independent_hi, rel=1e-9, abs=1e-12)


def test_zero_observed_count_retains_nonzero_dark_count_upper_bound():
    lo, hi = reference.expected_count_interval(0, 10000, 1e-10)
    assert lo == 0 and hi == pytest.approx(math.log(2e10))


@pytest.mark.parametrize("prob", [0.001, 0.1, 0.5, 0.99])
def test_inverse_chernoff_exact_binomial_coverage(prob):
    trials, xi = 100, 0.01
    uncovered = 0.0
    for observed in range(trials + 1):
        lo, hi = reference.expected_count_interval(observed, trials, xi)
        if not lo <= trials * prob <= hi:
            uncovered += binom.pmf(observed, trials, prob)
    assert uncovered <= xi


@pytest.mark.parametrize("prob", [0.001, 0.1, 0.5, 0.99])
def test_forward_chernoff_exact_binomial_coverage(prob):
    trials, xi = 100, 0.01
    lo, hi = reference.observed_count_interval(trials * prob, trials, xi)
    uncovered = sum(binom.pmf(x, trials, prob) for x in range(trials + 1) if not lo <= x <= hi)
    assert uncovered <= xi


@pytest.mark.parametrize("eta", [0.001, 0.01, 0.5])
def test_poisson_linear_program_and_analytic_bound_below_known_truth(eta):
    intensities = [0, 0.1, 0.4]
    gains = [1 - math.exp(-mu * eta) for mu in intensities]
    lp = reference.poisson_yield_lp(intensities, [(q, q) for q in gains])
    analytic = reference.asymptotic_decoy_yield(*gains, 0.1, 0.4)
    assert lp["feasible"]
    assert analytic <= lp["yield_lower"] + 2e-7 <= eta + 4e-7


def test_poisson_incompatible_vacuum_gain_rejected():
    result = reference.poisson_yield_lp([0, 0.1, 0.4], [(0.99, 0.99), (0, 0), (0, 0)])
    assert not result["feasible"] and result["yield_lower"] is None


def test_reference_imports_no_orion_implementation():
    source = (ROOT / "tools/sns_validation_reference.py").read_text(encoding="utf-8")
    assert "quantum_autopilot" not in source


def test_missing_strata_rejects_current_aggregate_contract(parameters):
    observation = reference.synthetic_observation(parameters, 101)
    raw = campaign.reduced_observation(observation)
    with pytest.raises(ValueError, match="aggregate X QBER is insufficient"):
        reference.finite_sns_estimate(raw, parameters)


def test_reference_uses_phase_wrong_detector_trials_not_aggregate_x_qber(parameters):
    observation = reference.synthetic_observation(parameters, 101)
    baseline = reference.finite_sns_estimate(observation, parameters)
    changed = deepcopy(observation)
    changed["x_errors"] = 0
    changed["x_clicks"] = 1000000000
    assert reference.finite_sns_estimate(changed, parameters) == baseline
    changed["phase_slice"]["wrong_detector_clicks"] = changed["phase_slice"]["clicks"]
    assert reference.finite_sns_estimate(changed, parameters)["conditional_budget_bits"] == 0


@pytest.mark.parametrize("assumption", ["symmetric_sources", "phase_randomized", "trusted_source_preparation",
                                      "independent_choices", "one_detector_heralding", "phase_slice_selection"])
def test_finite_proof_missing_assumption_blocks_budget(parameters, assumption):
    observation = reference.synthetic_observation(parameters, 101)
    observation["assumptions"][assumption] = False
    with pytest.raises(ValueError, match="assumptions"):
        reference.finite_sns_estimate(observation, parameters)


def test_asymmetry_is_not_silently_admitted_to_symmetric_proof(parameters):
    parameters["arm_lengths_km"] = [50, 200]
    observation = reference.synthetic_observation(parameters, 101)
    with pytest.raises(ValueError, match="assumptions"):
        reference.finite_sns_estimate(observation, parameters)


@pytest.mark.parametrize("path", ["signal", "00", "phase", "phase_wrong_above_clicks", "phase_clicks_above_trials"])
def test_tampered_counts_cannot_enter_estimator(parameters, path):
    observation = reference.synthetic_observation(parameters, 101)
    if path == "phase":
        observation["phase_slice"]["wrong_detector_clicks"] = observation["phase_slice"]["trials"] + 1
    elif path == "phase_wrong_above_clicks":
        observation["phase_slice"]["wrong_detector_clicks"] = observation["phase_slice"]["clicks"] + 1
    elif path == "phase_clicks_above_trials":
        observation["phase_slice"]["clicks"] = observation["phase_slice"]["trials"] + 1
    elif path == "signal":
        observation["signal"]["errors"] = observation["signal"]["clicks"] + 1
    else:
        observation["strata"][path]["clicks"] = observation["strata"][path]["trials"] + 1
    with pytest.raises(ValueError):
        reference.finite_sns_estimate(observation, parameters)


@pytest.mark.parametrize("key,value", [("weak_mu", float("nan")), ("strong_mu", True),
                                      ("ec_efficiency", 0.5), ("eps_cor", 0)])
def test_invalid_physical_and_security_parameters_are_rejected(parameters, key, value):
    observation = reference.synthetic_observation(parameters, 101)
    parameters[key] = value
    with pytest.raises(ValueError):
        reference.finite_sns_estimate(observation, parameters)


def test_seeded_fixture_replays_exactly(parameters):
    first = reference.synthetic_observation(parameters, 101)
    assert first == reference.synthetic_observation(parameters, 101)
    assert first != reference.synthetic_observation(parameters, 202)


@pytest.mark.parametrize("change,code", [("classic_channel", "wrong_channel"), ("quantum_channel", "wrong_channel"),
                                       ("stale", "replay"), ("clock", "unsynchronized"),
                                       ("source", "wrong_source"), ("future", "causality"),
                                       ("timeout", "timeout")])
def test_existing_backend_contract_blocks_invalid_sns_admission(change, code):
    from quantum_autopilot.quantum_protocols.contracts import Acquisition, DeviceCommand, ProtocolError, validate_binding
    command = DeviceCommand("cmd", "session", "tf_block", ("A", "C", "B"), ("QA", "QB"),
                            ("CA", "CB"), 0.0, 2.0, 1)
    observation = Acquisition("cmd", "session", "tf_block", "synthetic", "simulated", 1,
                              1.0, 0.0, ("QA", "QB"), ("CA", "CB"), {})
    last, now = 0, 1.0
    if change == "classic_channel":
        observation = replace(observation, classical_channel_ids=("BAD", "CB"))
    elif change == "quantum_channel":
        observation = replace(observation, quantum_channel_ids=("BAD", "QB"))
    elif change == "stale":
        last = 1
    elif change == "clock":
        observation = replace(observation, clock_offset_s=1.0)
    elif change == "source":
        observation = replace(observation, source_id="untrusted")
    elif change == "future":
        observation = replace(observation, observed_at_s=1.5)
    elif change == "timeout":
        now = 3.0
    with pytest.raises(ProtocolError) as rejected:
        validate_binding(observation, command, last_sequence=last, source_id="synthetic", origin="simulated",
                         now_s=now, sync_tolerance_s=1e-6)
    assert rejected.value.code == code


def test_published_equations_produce_conditional_nominal_budget(parameters):
    observation = reference.synthetic_observation(parameters, 101)
    result = reference.finite_sns_estimate(observation, parameters)
    assert result["conditional_budget_bits"] > 0
    assert result["single_photon_yield_lower"] <= observation["derived_physical_truth"]["single_photon_yield"]
    assert result["phase_error_expected_upper"] >= parameters["misalignment_error"]
    assert result["certified_secret_bits"] == 0 and not result["security_certified"]


def test_no_z_detection_cannot_create_untagged_bits_or_budget(parameters):
    observation = reference.synthetic_observation(parameters, 101)
    observation["signal"]["clicks"] = observation["signal"]["errors"] = 0
    result = reference.finite_sns_estimate(observation, parameters)
    assert result["untagged_bits_lower"] == 0 and result["conditional_budget_bits"] == 0


@pytest.mark.parametrize("overrides", [{"pulses": 100000}, {"detector_efficiency": 0},
                                      {"arm_lengths_km": [500, 500]}, {"dark_per_gate": 0.001},
                                      {"misalignment_error": 0.49}, {"send_probability": 0.08}])
def test_limitations_abort_reference_budget(parameters, overrides):
    parameters.update(overrides)
    result = reference.finite_sns_estimate(reference.synthetic_observation(parameters, 101), parameters)
    assert result["conditional_budget_bits"] == 0


def test_full_campaign_preserves_sources_and_reports_partial_scope(tmp_path):
    output = tmp_path / "sns"
    report = campaign.run_campaign(output, seeds=(101,))
    assert report["status"] == "partial_verified" and all(row["passed"] for row in report["checks"])
    assert report["case_count"] == 9
    assert report["physical_hardware_validated"] is False and report["finite_sns_backend_validated"] is False
    before = (output / "report.json").read_bytes()
    with pytest.raises(FileExistsError):
        campaign.run_campaign(output, seeds=(101,))
    assert (output / "report.json").read_bytes() == before


@pytest.mark.parametrize("seeds", [(), (101, 101), (True,), (-1,), (1.5,)])
def test_campaign_requires_nonempty_distinct_nonnegative_integer_seeds(tmp_path, seeds):
    with pytest.raises(ValueError, match="seeds"):
        campaign.run_campaign(tmp_path / "invalid", seeds=seeds)
    assert not (tmp_path / "invalid").exists()


def test_existing_incomplete_campaign_directory_is_preserved(tmp_path):
    output = tmp_path / "incomplete"
    output.mkdir()
    artifact = output / "observation.json"
    artifact.write_text("preserved", encoding="utf-8")
    with pytest.raises(FileExistsError):
        campaign.run_campaign(output, seeds=(101,))
    assert artifact.read_text(encoding="utf-8") == "preserved"
