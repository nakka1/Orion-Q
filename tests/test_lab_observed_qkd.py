"""Independent truth calculations and reproducible observation-to-QKD checks."""
from dataclasses import replace
import math

import numpy as np
import pytest

from quantum_autopilot.lab.observations import (ClockReference, ObservationBlock,
    PulseObservation, load_observation_block, persist_observation_block, synthetic_bb84_observations)
from quantum_autopilot.lab.observed_qkd import run_observed_bb84, sampling_deviation
from quantum_autopilot.lab.qkd import QkdConfig, run_bb84
from quantum_autopilot.quantum_protocols.satellite_processing import reconcile, toeplitz


@pytest.mark.parametrize("seed", [7, 21, 93, 117, 409])
def test_known_ideal_inputs_have_correct_sifting_error_and_conditional_budget(seed):
    block = synthetic_bb84_observations(seed=seed, count=12000, loss_db=0, efficiency=1, error_probability=0)
    config = QkdConfig(seed=seed, emitted_pulses=12000, channel_loss_db=0, detector_efficiency=1,
                       bit_error_probability=0, parameter_estimation_error=1e-5, privacy_amplification_error=1e-5)
    session = run_observed_bb84(block, config)
    expected_sifted = sum(p.prepared_basis == p.analyzed_basis for p in block.observations)
    expected_z = sum(p.prepared_basis == p.analyzed_basis == 0 for p in block.observations)
    expected_x = sum(p.prepared_basis == p.analyzed_basis == 1 for p in block.observations)
    s = session.summary
    assert s["detected_bits"] == 12000 and s["sifted_bits"] == expected_sifted
    assert s["z_basis_candidate_bits"] == expected_z and s["x_basis_observed_bits"] == expected_x
    assert s["qber"] == 0 and s["reconciliation_corrected_bits"] == 0
    sample = min(expected_x, int(expected_sifted * config.parameter_sample_fraction))
    independent_delta = math.sqrt((expected_z + sample) * (sample + 1) * math.log(1e5) /
                                  (2 * expected_z * sample**2))
    assert s["sampling_deviation"] == pytest.approx(independent_delta)
    h = -independent_delta * math.log2(independent_delta) - (1 - independent_delta) * math.log2(1 - independent_delta)
    expected_budget = max(0, math.floor(expected_z * (1 - h) - s["error_correction_leak_bits"] - 64 - 2 * math.log2(1e5)))
    assert s["conditional_privacy_budget_bits"] == expected_budget
    assert s["demo_key_bits"] <= expected_budget and s["certified_secret_bits"] == 0


def test_same_persisted_observations_replay_to_exact_protocol_output(tmp_path):
    block = synthetic_bb84_observations(count=20000, efficiency=1, loss_db=0, error_probability=.01)
    target = persist_observation_block(block, tmp_path / "block.json")
    config = QkdConfig(emitted_pulses=20000, channel_loss_db=0, detector_efficiency=1,
                       parameter_estimation_error=1e-5, privacy_amplification_error=1e-5)
    first = run_observed_bb84(block, config)
    second = run_observed_bb84(load_observation_block(target), config)
    assert first.summary == second.summary and first.delivery_material == second.delivery_material
    assert first.summary["observation_sha256"] == block.observation_sha256


@pytest.mark.parametrize("regime", ["clock_unlock", "excessive_jitter", "all_delayed", "no_photons", "high_error"])
def test_operational_and_physical_failures_abort_without_fabricated_material(regime):
    block = synthetic_bb84_observations(count=16000, efficiency=0 if regime == "no_photons" else 1,
        loss_db=0, error_probability=.5 if regime == "high_error" else 0)
    if regime == "clock_unlock":
        block = replace(block, clock=replace(block.clock, locked=False))
    elif regime == "excessive_jitter":
        block = replace(block, clock=replace(block.clock, alignment_uncertainty_ps=1001))
    elif regime == "all_delayed":
        block = replace(block, observations=[replace(p, detection_timestamp_ps=p.timestamp_ps + 10000)
                        if p.detected else p for p in block.observations])
    result = run_observed_bb84(block)
    assert result.delivery_material == b"" and result.summary["demo_key_bits"] == 0
    assert result.summary["abort_reason"]


def test_timestamps_alone_do_not_satisfy_observation_protocol():
    with pytest.raises(TypeError):
        PulseObservation(pulse_id=0, timestamp_ps=5)
    incomplete = ObservationBlock("fixture", "tdc", "BB84-single-photon", "physical", "hardware", "tdc-source",
        ClockReference("clock"), [PulseObservation(0, 0, 0, 0, 0, None, True, 1)], 100,
        "tdc-adapter", "config")
    with pytest.raises(Exception):
        run_observed_bb84(incomplete)


@pytest.mark.parametrize("n,m", [(1, 1), (5, 7), (31, 19), (100, 64)])
def test_toeplitz_against_independent_integer_matrix(n, m):
    rng = np.random.default_rng(n * 1000 + m)
    bits, seed = rng.integers(0, 2, n, dtype=np.uint8), rng.integers(0, 2, n + m - 1, dtype=np.uint8)
    expected = np.asarray([sum(int(bits[col]) * int(seed[n - 1 + row - col]) for col in range(n)) % 2
                           for row in range(m)], dtype=np.uint8)
    assert np.array_equal(toeplitz(bits, seed, m), expected)


@pytest.mark.parametrize("seed,error", [(5, 0), (13, .005), (91, .01), (101, .03)])
def test_reconciliation_corrects_known_errors_and_charges_transcript(seed, error):
    rng = np.random.default_rng(seed)
    alice = rng.integers(0, 2, 12000, dtype=np.uint8)
    bob = alice.copy()
    actual_errors = rng.random(len(alice)) < error
    bob ^= actual_errors.astype(np.uint8)
    result, leakage, corrected = reconcile(alice, bob, rng, max(error, .001))
    assert np.array_equal(alice, result)
    assert corrected == int(actual_errors.sum())
    assert leakage >= 2 * corrected and leakage > 0


def test_budget_accounts_for_authentication_cost_and_verification_failure_probability():
    config = QkdConfig(emitted_pulses=30000, channel_loss_db=0, detector_efficiency=1, bit_error_probability=0,
                       parameter_estimation_error=1e-5, privacy_amplification_error=1e-5)
    base = run_bb84(config)
    taxed = run_bb84(replace(config, authentication_cost_bits=1024))
    assert base.summary["conditional_privacy_budget_bits"] - taxed.summary["conditional_privacy_budget_bits"] == 1024
    assert base.summary["verification_failure_bound"] == 2**-64
    assert not base.summary["error_budget"]["authentication_proven"]
    assert not base.summary["error_budget"]["composable_security_claim"]


def test_sampling_bound_invalid_empty_samples_never_returns_assured_success():
    assert sampling_deviation(100, 0, 1e-5) is None
    assert sampling_deviation(0, 100, 1e-5) is None
    assert sampling_deviation(10000, 100, 1e-5) > sampling_deviation(10000, 1000, 1e-5)
