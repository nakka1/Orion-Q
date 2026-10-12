"""Finite SNS backend observations, real postprocessing and independent oracle."""
from copy import deepcopy
import importlib.util
from pathlib import Path

import numpy as np
import pytest

from quantum_autopilot.quantum_protocols.contracts import DeviceCommand, ProtocolConfig, ProtocolError, digest
from quantum_autopilot.quantum_protocols.protocols import TwinFieldAdapter
from quantum_autopilot.quantum_protocols.tf_processing import estimate_bounds, parameters, process_observation
from quantum_autopilot.quantum_protocols.tf_simulation import generate_observation

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('sns_frozen_oracle', ROOT / 'tools/sns_validation_reference.py')
oracle = importlib.util.module_from_spec(spec)
spec.loader.exec_module(oracle)


def fixture(seed=101, **overrides):
    p = {'tf_estimation_mode': 'finite_sns_v2', 'signal_mu': .15, 'decoy_mu': .03,
         'decoy_strong_mu': .15, 'send_probability': .005, 'pulse_rate_hz': 20000000.,
         'phase_sigma_rad': .01, 'polarization_error_rad': .01, 'tf_misalignment_error': .005,
         'dark_probability': 1e-10, **overrides}
    config = ProtocolConfig('TF-TEST', ('tf_qkd',), ('A', 'B'), ('A', 'C', 'B'), ('LA', 'LB'), p, seed)
    command = DeviceCommand('TF-TEST-BLOCK', 'TF-TEST', 'tf_block', ('A', 'C', 'B'),
                            ('QA', 'QB'), ('CA', 'CB'), 0., 120., 1,
                            {'duration_s': 1., 'arm_lengths_km': [2., 2.]})
    raw = generate_observation(np.random.default_rng(seed), command, config, {}, p,
                               source_id='synthetic', origin='simulated', sequence=1)
    return raw, p


def context(p, messages=None):
    messages = messages if messages is not None else []

    def exchange(stage, payload):
        messages.append({'stage': stage, 'payload': deepcopy(payload)})
        return {'accepted': True, 'message_id': f'm-{len(messages)}', 'receipt_sha256': digest(messages[-1]),
                'transmitted_at_s': len(messages) * .001, 'received_at_s': len(messages) * .001 + .0005,
                'authenticated': False, 'origin': 'simulated_transport_fixture'}

    return {'parameters': p, 'endpoints': ('A', 'B'), 'classical_exchange': exchange,
            'require_classical_transport': True}


def reference_input(raw, p):
    observations = {'schema': 'orionq.sns-reference-observation.v1', 'strata': raw['strata'],
                    'phase_slice': raw['phase_slice'], 'signal': raw['signal'],
                    'assumptions': dict.fromkeys(('symmetric_sources', 'phase_randomized', 'trusted_source_preparation',
                                                 'independent_choices', 'one_detector_heralding', 'phase_slice_selection'), True)}
    params = {'weak_mu': p['decoy_mu'], 'strong_mu': p['decoy_strong_mu'], 'signal_mu': p['signal_mu'],
              'send_probability': p['send_probability'], 'failure_probability': p.get('statistical_failure', 1e-10)}
    return observations, params


@pytest.mark.parametrize('seed', [101, 202, 303])
def test_actual_count_bounds_match_frozen_independent_oracle(seed):
    raw, p = fixture(seed)
    observations, params = reference_input(raw, p)
    expected = oracle.finite_sns_estimate(observations, params)
    actual = estimate_bounds(raw, parameters(p))
    for key in ('single_photon_yield_lower', 'phase_error_expected_upper', 'phase_error_observed_upper'):
        assert actual[key] == pytest.approx(expected[key], abs=1e-10, rel=1e-8)
    assert actual['untagged_bits_lower'] == expected['untagged_bits_lower']


@pytest.mark.parametrize('seed', [101, 202, 303])
def test_simulated_counts_execute_real_reconciliation_verification_and_pa(seed):
    raw, p = fixture(seed)
    messages = []
    result = TwinFieldAdapter().process(raw, context(p, messages))
    assert result['reconciliation_verified']
    assert result['conditional_key_estimate_bits'] > 0
    assert result['privacy_amplification_executed_bits'] == result['conditional_key_estimate_bits']
    assert result['error_correction_leak_bits'] > 0
    assert result['reconciliation_corrected_bits'] == raw['signal']['errors']
    assert {message['stage'] for message in messages} >= {'sifting', 'parameter_estimation', 'reconciliation',
                                                       'verification', 'privacy_amplification'}
    assert result['classical_transmission_verified'] and result['classical_exchange_count'] == len(messages)
    assert result['certified_secret_bits'] == 0 and not result['physical_action_performed']
    assert not result['key_material_exported']
    for message in messages:
        assert 'alice_send' not in message['payload'] and 'bob_send' not in message['payload']
    assert all(key not in result for key in ('key', 'secret_key', 'key_bits', 'alice_bits', 'bob_bits'))


def test_identical_input_public_randomness_replays_processing_and_disclosures():
    raw, p = fixture()
    first_messages, second_messages = [], []
    first = process_observation(raw, context(p, first_messages))
    second = process_observation(deepcopy(raw), context(p, second_messages))
    assert first == second and first_messages == second_messages


@pytest.mark.parametrize('field', ['weak_mu', 'strong_mu', 'signal_mu', 'send_probability'])
def test_asymmetric_source_preparation_requires_a_different_proof(field):
    raw, p = fixture()
    raw['source_preparation']['senders'][1][field] *= 1.1
    with pytest.raises(ProtocolError) as caught:
        process_observation(raw, context(p))
    assert caught.value.code == 'unsupported_tf_proof'


@pytest.mark.parametrize('change,code', [('phase_wrong', 'invalid_tf_counts'), ('z_errors', 'invalid_tf_counts'),
                                        ('duplicate_id', 'tf_replay'), ('bad_time', 'invalid_tf_counts'),
                                        ('clock', 'unsynchronized'), ('preparation', 'invalid_tf_counts'),
                                        ('signal_phases', 'unsupported_tf_proof'), ('source', 'wrong_binding'),
                                        ('clock_uncertainty', 'unsynchronized'), ('phase_split', 'invalid_tf_counts')])
def test_invalid_observations_fail_before_classical_disclosures(change, code):
    raw, p = fixture()
    if change == 'phase_wrong':
        raw['phase_slice']['wrong_detector_clicks'] = raw['phase_slice']['clicks'] + 1
    elif change == 'z_errors':
        raw['signal']['errors'] += 1
    elif change == 'duplicate_id':
        raw['signal_records']['trial_id'][1] = raw['signal_records']['trial_id'][0]
    elif change == 'bad_time':
        raw['signal_records']['time_ps'][0] = -1
    elif change == 'clock':
        raw['clock']['offset_ps'] = 10**12
    elif change == 'clock_uncertainty':
        raw['clock']['uncertainty_ps'] = 10**12
    elif change == 'phase_split':
        raw['phase_slice']['plus_right_errors'] += 1
    elif change == 'preparation':
        raw['signal_records']['alice_send'][0] = 2
    elif change == 'signal_phases':
        raw['source_preparation']['signal_phases_disclosed'] = True
    elif change == 'source':
        raw['source_preparation']['senders'][0]['node_id'] = 'OTHER'
    messages = []
    with pytest.raises(ProtocolError) as caught:
        process_observation(raw, context(p, messages))
    assert caught.value.code == code and not messages


@pytest.mark.parametrize('field', ['strata', 'clock', 'source_preparation', 'signal_records', 'phase_slice'])
def test_malformed_nested_payload_produces_protocol_error_not_unhandled_exception(field):
    raw, p = fixture()
    raw[field] = []
    with pytest.raises(ProtocolError):
        process_observation(raw, context(p))


def test_missing_classical_transport_cannot_claim_transmission():
    raw, p = fixture()
    with pytest.raises(ProtocolError, match='classical transport'):
        process_observation(raw, {'parameters': p, 'endpoints': ('A', 'B'), 'require_classical_transport': True})
    diagnostic = process_observation(raw, {'parameters': p, 'endpoints': ('A', 'B')})
    assert diagnostic['classical_transmission_verified'] is False


def test_classical_rejection_prevents_reconciliation_and_key_estimate():
    raw, p = fixture()
    ctx = context(p)
    ctx['classical_exchange'] = lambda stage, payload: {'accepted': False}
    with pytest.raises(ProtocolError) as caught:
        process_observation(raw, ctx)
    assert caught.value.code == 'classical_rejected'


@pytest.mark.parametrize('overrides', [{'pulse_rate_hz': 10000}, {'extra_loss_db': 60},
                                      {'tf_misalignment_error': .49}, {'dark_probability': .001}])
def test_losses_small_sample_and_noise_change_results_and_abort(overrides):
    raw, p = fixture(**overrides)
    result = process_observation(raw, context(p))
    assert result['conditional_key_estimate_bits'] == 0 and result['finite_key']['abort_reason']


def test_capacity_failure_is_explicit_instead_of_silent_event_loss():
    with pytest.raises(ProtocolError) as caught:
        fixture(tf_max_sifted_bits=10)
    assert caught.value.code == 'tf_backpressure'


def test_legacy_payload_remains_explicitly_asymptotic():
    raw = {'z_trials': 1000, 'z_clicks': 100, 'z_errors': 1, 'x_trials': 1000, 'x_clicks': 100, 'x_errors': 1,
           'vacuum': {'trials': 1000, 'clicks': 0}, 'weak': {'trials': 1000, 'clicks': 10},
           'strong': {'trials': 1000, 'clicks': 40}}
    result = TwinFieldAdapter().process(raw, {'parameters': {}})
    assert result['protocol'] == 'SNS-TF reduced asymptotic model' and result['certified_secret_bits'] == 0
    with pytest.raises(ProtocolError):
        TwinFieldAdapter().process(raw, {'parameters': {'tf_estimation_mode': 'finite_sns_v2'}})
