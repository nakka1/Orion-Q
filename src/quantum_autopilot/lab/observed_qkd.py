"""BB84 post-processing of identified preparation, analysis and clock records.

All adapters supply ObservationBlock. No final bits are manufactured from time
tags. X-basis observations estimate the phase error of Z-basis key candidates,
under explicitly conditional ideal-single-photon assumptions; no security
certification follows from this implementation or its public simulation seed.
"""
from __future__ import annotations

import math


def sampling_deviation(key_count, sample_count, epsilon):
    """Conservative without-replacement deviation (Serfling-style).

    Tomamichel/Leverrier 2017, Eq. 76, conditional binary sampling bound.
    This function is not by itself a complete quantum security proof.
    """
    if key_count <= 0 or sample_count <= 0:
        return None
    return math.sqrt((key_count + sample_count) * (sample_count + 1) * math.log(1 / epsilon) /
                     (2 * key_count * sample_count * sample_count))


def binary_entropy(value):
    if value <= 0 or value >= 1:
        return 0.0
    return -value * math.log2(value) - (1 - value) * math.log2(1 - value)


def run_observed_bb84(block, config=None, *, authenticated_channel=None,
                      observation_envelope=None, purpose="test"):
    """Validate/acquire/synchronize/sift/reconcile/verify/amplify the SAME records."""
    from .qkd import MODEL_ID, QkdConfig, QkdSession
    import numpy as np
    from quantum_autopilot.quantum_protocols.satellite_processing import reconcile, toeplitz

    from quantum_autopilot.security.protocol import require_operational_qkd_material, ProtocolAuthenticationError
    block.validate()
    require_operational_qkd_material(block, purpose=purpose)
    input_sha256 = block.observation_sha256
    if observation_envelope is not None and authenticated_channel is None:
        raise ProtocolAuthenticationError("observation envelope requires the authenticated session")
    exchange = authenticated_channel.exchange if authenticated_channel is not None else None
    if authenticated_channel is not None:
        if (authenticated_channel.transcript_binding != input_sha256 or
                authenticated_channel.origin != block.origin or authenticated_channel.mode != block.mode):
            raise ProtocolAuthenticationError("QKD observations do not match authenticated source/session binding")
        observation_payload = {"observation_sha256": input_sha256,
            "experiment_id": block.experiment_id, "source_id": block.source_id,
            "configuration_id": block.configuration_id, "clock_domain": block.clock.domain,
            "raw_acquisition_sha256": block.raw_acquisition_sha256}
        if observation_envelope is None:
            exchange("observation", observation_payload)
        else:
            authenticated_channel.receive(observation_envelope, expected_operation="observation",
                                          expected_payload=observation_payload)
    config = config or QkdConfig(emitted_pulses=len(block.observations), acquisition_s=block.duration_ps / 1e12)
    config.validate()
    if config.emitted_pulses != len(block.observations):
        raise ValueError("configured pulse count does not match acquired observation block")
    if round(config.acquisition_s * 1e12) != block.duration_ps:
        raise ValueError("configured acquisition duration does not match observed clock interval")
    rng = np.random.default_rng(config.seed)
    stages = ["observation_acquisition", "provenance_validation", "clock_validation", "temporal_alignment",
              "basis_sifting", "x_basis_parameter_disclosure"]
    detected = [pulse for pulse in block.observations if pulse.detected]
    half_window = config.coincidence_window_ps / 2
    usable = [pulse for pulse in detected
              if abs(pulse.detection_timestamp_ps - pulse.timestamp_ps - config.expected_flight_time_ps) <= half_window]
    synchronized = block.clock.locked and block.clock.alignment_uncertainty_ps <= config.timing_alignment_tolerance_ps
    if not synchronized:
        usable = []
    sifted = [pulse for pulse in usable if pulse.prepared_basis == pulse.analyzed_basis]
    if exchange:
        exchange("basis_sifting", {"observed_bases": [[p.pulse_id, p.prepared_basis, p.analyzed_basis]
                                                      for p in usable],
                                    "sifted_pulse_ids": [p.pulse_id for p in sifted]})
    z_records = [pulse for pulse in sifted if pulse.prepared_basis == 0]
    x_records = [pulse for pulse in sifted if pulse.prepared_basis == 1]
    # Choose an explicit random X test subset. Unrevealed X observations are
    # discarded, never silently recycled into the Z key.
    sample_count = min(len(x_records), int(len(sifted) * config.parameter_sample_fraction))
    test_indices = rng.permutation(len(x_records))[:sample_count]
    sample_errors = sum(x_records[int(index)].prepared_bit != x_records[int(index)].analyzed_bit for index in test_indices)
    if exchange:
        exchange("parameter_estimation", {"x_basis_disclosures": [[x_records[int(i)].pulse_id,
            x_records[int(i)].prepared_bit, x_records[int(i)].analyzed_bit] for i in test_indices],
            "candidate_z_bits": len(z_records)})
    qber = sample_errors / sample_count if sample_count else None
    deviation = sampling_deviation(len(z_records), sample_count, config.parameter_estimation_error)
    phase_upper = min(.5, qber + deviation) if deviation is not None and qber is not None else .5
    alice = np.asarray([pulse.prepared_bit for pulse in z_records], dtype=np.uint8)
    bob = np.asarray([pulse.analyzed_bit for pulse in z_records], dtype=np.uint8)
    leakage = corrected = 0
    verified = False
    if len(alice) and sample_count and phase_upper < .11:
        bob, leakage, corrected = reconcile(alice, bob, rng, max(qber, .001), classical_exchange=exchange)
        tag_seed = rng.integers(0, 2, len(alice) + config.verification_bits - 1, dtype=np.uint8)
        alice_tag = toeplitz(alice, tag_seed, config.verification_bits)
        bob_tag = toeplitz(bob, tag_seed, config.verification_bits)
        if exchange:
            exchange("verification", {"public_toeplitz_seed": tag_seed.tolist(),
                "alice_tag": alice_tag.tolist(), "bob_tag": bob_tag.tolist(),
                "verification_bits": config.verification_bits})
        verified = bool(np.array_equal(alice_tag, bob_tag))
        stages.extend(("shuffled_parity_reconciliation", "toeplitz_verification"))
    privacy_penalty = 2 * math.log2(1 / config.privacy_amplification_error)
    # Charge at least the separately reserved 256-bit MAC key in an authenticated
    # session. HMAC reuses its secret key: tag traffic is reported separately,
    # never represented as OTP key consumption or a composable failure bound.
    authentication_cost = max(config.authentication_cost_bits, 256 if exchange else 0)
    budget = max(0, math.floor(len(alice) * (1 - binary_entropy(phase_upper)) - leakage -
                              config.verification_bits - privacy_penalty - authentication_cost))
    if not verified or phase_upper >= .11 or not synchronized:
        budget = 0
    length = budget // 256 * 256
    material = b""
    if length:
        seed = rng.integers(0, 2, len(alice) + length - 1, dtype=np.uint8)
        if exchange:
            exchange("privacy_amplification", {"public_toeplitz_seed": seed.tolist(), "output_bits": length})
        pa_a, pa_b = toeplitz(alice, seed, length), toeplitz(bob, seed, length)
        if np.array_equal(pa_a, pa_b):
            material = np.packbits(pa_a).tobytes()
            stages.append("toeplitz_privacy_amplification")
        else:
            length = 0
    reason = "" if length else ("clock_not_synchronized" if not synchronized else
        "no_in_window_detections" if not usable else "insufficient_parameter_sample" if not sample_count else
        "parameter_estimation_rejected" if phase_upper >= .11 else "verification_failed" if not verified else
        "conditional_budget_nonpositive")
    duration_s = block.duration_ps / 1e12
    if block.observation_sha256 != input_sha256:
        raise ValueError("observation block changed during protocol processing")
    summary = {
        "schema": "orion.lab.qkd-session.v2", "experiment_id": block.experiment_id,
        "mode": block.mode, "origin": block.origin, "model_id": MODEL_ID,
        "config": dict(vars(config)), "physical_acquisition": block.origin == "physical",
        "source_id": block.source_id, "adapter_id": block.adapter_id,
        "configuration_id": block.configuration_id, "observation_sha256": input_sha256,
        "raw_acquisition_sha256": block.raw_acquisition_sha256, "source_record": block.source_record,
        "clock": {"domain": block.clock.domain, "tick_ps": block.clock.tick_ps,
                  "locked": block.clock.locked, "alignment_uncertainty_ps": block.clock.alignment_uncertainty_ps},
        "protocol": "BB84 ideal single photon; X parameter test, Z candidate key", "stages": stages,
        "emitted_pulses": len(block.observations), "detected_bits": len(detected),
        "temporally_accepted_bits": len(usable), "out_of_window_detections": len(detected) - len(usable),
        "sifted_bits": len(sifted), "z_basis_candidate_bits": len(z_records),
        "x_basis_observed_bits": len(x_records), "unused_x_bits_discarded": len(x_records) - sample_count,
        "parameter_sample_bits": sample_count, "parameter_sample_errors": sample_errors, "qber": qber,
        "sampling_deviation": deviation, "phase_error_upper": phase_upper,
        "bound_reference": "Tomamichel & Leverrier 2017 arXiv:1506.08458 Eq.76; conditional ideal sampling",
        "reconciliation_corrected_bits": corrected, "error_correction_leak_bits": leakage,
        "reconciliation_verified": verified, "verification_tag_bits": config.verification_bits,
        "verification_failure_bound": 2 ** -config.verification_bits,
        "privacy_amplification_penalty_bits": privacy_penalty,
        "authentication_cost_bits": authentication_cost,
        "classical_authentication": authenticated_channel.summary() if authenticated_channel else {
            "status": "unsigned_test_evidence_only", "accepted_messages": 0,
            "operational_admission": False},
        "conditional_privacy_budget_bits": budget, "demo_key_bits": length, "certified_secret_bits": 0,
        "raw_rate_bps": len(detected) / duration_s, "sifted_rate_bps": len(sifted) / duration_s,
        "conditional_key_rate_bps": length / duration_s, "abort_reason": reason,
        "security_status": "not_certified_test_material_only",
        "error_budget": {"parameter_estimation": config.parameter_estimation_error,
                         "verification": 2 ** -config.verification_bits,
                         "privacy_amplification": config.privacy_amplification_error,
                         "authentication_proven": False,
                         "composable_security_claim": False},
        "model_assumptions": ["appropriate ideal single-photon preparation, supplied bases and bits",
            "trusted basis-independent detectors and explicit synchronized clock",
            "X random test applicable to phase error of Z candidates",
            "all transmitted parity responses charged in leakage", "authentication requires separate evidence"],
        "limits": ["central post-processing can see both observation strings; no distributed quantum source proof",
            "public reproducible protocol randomness is not secret randomness",
            "no decoy-state/WCP security proof; no hardware qualification",
            "conditional error budget does not certify secure keys; delivery material is TEST only"],
    }
    return QkdSession(summary, material)
