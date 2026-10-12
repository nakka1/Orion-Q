"""Conditional finite SNS processing from joint-source/phase observations.

Jiang et al. arXiv:1904.00192v3, Eqs. 5 and 15--26. The production numerical
implementation uses root solving in log count space independently of the
qualification oracle. Real parity reconciliation, universal verification and
Toeplitz amplification consume observed send/not-send bits. No resulting material
is certified, exported as a usable key or admitted to an operational KME.
"""
from __future__ import annotations

import math

import numpy as np
from scipy.optimize import brentq

from .contracts import ProtocolError, digest, number
from .physics import entropy
from .satellite_processing import reconcile, toeplitz

SCHEMA = 'orion.tf-sns.v2'
STRATA = ('00', '01', '10', '02', '20')


def integer(value, name, maximum=10**15):
    if type(value) is not int or not 0 <= value <= maximum:
        raise ProtocolError('invalid_tf_counts', f'{name}: bounded nonnegative integer required')
    return value


def row(value, name):
    if not isinstance(value, dict):
        raise ProtocolError('invalid_tf_observation', f'{name}: trial/count object required')
    n = integer(value.get('trials'), name + '.trials')
    k = integer(value.get('clicks'), name + '.clicks', n)
    if n == 0:
        raise ProtocolError('insufficient_tf_sample', name + ': positive trials required')
    return n, k


def parameters(p):
    mu = number(p.get('signal_mu', .15), 'signal_mu', .001, 2.)
    weak = number(p.get('decoy_mu', .03), 'decoy_mu', .001, 1.)
    strong = number(p.get('decoy_strong_mu', mu), 'decoy_strong_mu', .001, 2.)
    if weak >= strong:
        raise ProtocolError('invalid_parameter', 'finite SNS requires decoy_mu < decoy_strong_mu')
    send = number(p.get('send_probability', .005), 'send_probability', 1e-5, .5)
    delta = number(p.get('tf_phase_slice_rad', math.pi / 4), 'tf_phase_slice_rad', 1e-6, math.pi)
    eps = {name: number(p.get(key, 1e-10), key, 1e-15, .01) for name, key in
           (('statistics', 'statistical_failure'), ('verification', 'verification_error'),
            ('privacy', 'privacy_amplification_error'), ('smoothing', 'entropy_smoothing_error'))}
    maximum = integer(p.get('tf_max_sifted_bits', 50000), 'tf_max_sifted_bits', 200000)
    if maximum < 1:
        raise ProtocolError('invalid_parameter', 'tf_max_sifted_bits must be positive')
    number(p.get('tf_z_probability', .5), 'tf_z_probability', .01, .99)
    p0 = number(p.get('tf_vacuum_probability', .3), 'tf_vacuum_probability', .001, .99)
    p1 = number(p.get('tf_weak_probability', .4), 'tf_weak_probability', .001, .99)
    if p0 + p1 >= 1:
        raise ProtocolError('invalid_parameter', 'TF decoy probabilities must leave a strong-source category')
    number(p.get('tf_misalignment_error', .005), 'tf_misalignment_error', 0., .5)
    passes = integer(p.get('reconciliation_passes', 8), 'reconciliation_passes', 16)
    if passes == 0:
        raise ProtocolError('invalid_parameter', 'at least one reconciliation pass required')
    return {'signal_mu': mu, 'weak_mu': weak, 'strong_mu': strong, 'send_probability': send,
            'delta_rad': delta, 'eps': eps, 'maximum': maximum}


def validate_observation(raw, context):
    if not isinstance(raw, dict) or raw.get('tf_schema') != SCHEMA:
        raise ProtocolError('invalid_tf_observation', 'finite SNS observation schema required')
    p = parameters(context['parameters'])
    if not isinstance(raw.get('strata'), dict):
        raise ProtocolError('invalid_tf_observation', 'joint source strata object required')
    for name in STRATA:
        row(raw.get('strata', {}).get(name), name)
    start = number(raw.get('start_s'), 'start_s')
    end = number(raw.get('end_s'), 'end_s', start + 1e-12)
    clock = raw.get('clock', {})
    if not isinstance(clock, dict) or not isinstance(clock.get('reference'), str) or not clock['reference']:
        raise ProtocolError('invalid_tf_clock', 'clock reference required')
    uncertainty = integer(clock.get('uncertainty_ps'), 'clock.uncertainty_ps', 10**12)
    tolerance_ps = round(number(context['parameters'].get('sync_tolerance_s', 1e-6),
                                'sync_tolerance_s', 0.) * 1e12)
    offset = clock.get('offset_ps')
    if type(offset) is not int or abs(offset) + uncertainty > tolerance_ps:
        raise ProtocolError('unsynchronized', 'TF source clock outside admitted synchronization tolerance')
    preparation = raw.get('source_preparation', {})
    if not isinstance(preparation, dict):
        raise ProtocolError('invalid_tf_preparation', 'source preparation object required')
    if any(preparation.get(flag) is not True for flag in
           ('phase_randomized', 'independent_choices', 'one_detector_heralding', 'phase_slice_selection')):
        raise ProtocolError('unsupported_tf_proof', 'required SNS source/selection assumptions not established')
    if preparation.get('signal_phases_disclosed') is not False:
        raise ProtocolError('unsupported_tf_proof', 'SNS Z-window phases must remain undisclosed')
    senders = preparation.get('senders', [])
    endpoints = context.get('endpoints')
    if not isinstance(senders, list) or len(senders) != 2:
        raise ProtocolError('invalid_tf_preparation', 'two sender preparation records required')
    for index, sender in enumerate(senders):
        if not isinstance(sender, dict):
            raise ProtocolError('invalid_tf_preparation', 'sender preparation object required')
        if endpoints and sender.get('node_id') != endpoints[index]:
            raise ProtocolError('wrong_binding', 'TF preparation sender differs from session endpoint')
        for key in ('signal_mu', 'weak_mu', 'strong_mu', 'send_probability'):
            value = number(sender.get(key), 'sender.' + key, 0., 2.)
            if not math.isclose(value, p[key], rel_tol=1e-12, abs_tol=1e-15):
                raise ProtocolError('unsupported_tf_proof', 'symmetric SNS source intensities/probabilities must match configuration')
    phase = raw.get('phase_slice', {})
    phase_n, phase_clicks = row(phase, 'phase_slice')
    wrong = integer(phase.get('wrong_detector_clicks'), 'phase_slice.wrong_detector_clicks', phase_clicks)
    split = ('plus_right_errors', 'minus_left_errors')
    if any(key in phase for key in split):
        parts = [integer(phase.get(key), 'phase_slice.' + key, phase_clicks) for key in split]
        if sum(parts) != wrong:
            raise ProtocolError('invalid_tf_counts', 'phase-slice error decomposition differs from aggregate count')
    delta = number(phase.get('delta_rad'), 'phase_slice.delta_rad', 1e-6, math.pi)
    if not math.isclose(delta, p['delta_rad'], rel_tol=1e-12):
        raise ProtocolError('wrong_binding', 'phase slice differs from configured selection window')
    if phase.get('selection_rule') != 'wrapped_relative_phase_near_zero_or_pi':
        raise ProtocolError('unsupported_tf_proof', 'SNS phase selection rule required')
    signal_n, signal_clicks = row(raw.get('signal'), 'signal')
    if signal_clicks > p['maximum']:
        raise ProtocolError('tf_backpressure', 'observed Z block exceeds bounded processing capacity; no silent truncation')
    records = raw.get('signal_records', {})
    if not isinstance(records, dict):
        raise ProtocolError('invalid_tf_preparation', 'private Z preparation/detection object required')
    columns = ('trial_id', 'time_ps', 'alice_send', 'bob_send', 'charlie_detector')
    if records.get('preparation_basis') != 'Z' or records.get('clock_reference') != clock['reference']:
        raise ProtocolError('invalid_tf_preparation', 'Z preparations and matching clock reference required')
    for key in columns:
        if not isinstance(records.get(key), list) or len(records[key]) != signal_clicks:
            raise ProtocolError('invalid_tf_counts', 'one preparation/detection record per Z click required')
    previous_id = previous_time = -1
    duration_ps = round((end - start) * 1e12)
    for index in range(signal_clicks):
        trial = integer(records['trial_id'][index], 'trial_id', signal_n - 1)
        time = integer(records['time_ps'][index], 'time_ps', duration_ps - 1)
        if trial <= previous_id or time < previous_time:
            raise ProtocolError('tf_replay', 'Z pulse IDs must be unique and chronologically ordered')
        previous_id, previous_time = trial, time
        for key in ('alice_send', 'bob_send', 'charlie_detector'):
            integer(records[key][index], key, 1)
    errors = sum(a == b for a, b in zip(records['alice_send'], records['bob_send']))
    if integer(raw['signal'].get('errors'), 'signal.errors', signal_clicks) != errors:
        raise ProtocolError('invalid_tf_counts', 'declared Z errors differ from observed preparations')
    random = raw.get('public_randomness', {})
    try:
        seed = bytes.fromhex(random['seed_hex'])
        if len(seed) != 16 or not isinstance(random['origin'], str) or not random['origin']:
            raise ValueError()
    except (KeyError, TypeError, ValueError) as exc:
        raise ProtocolError('invalid_randomness', 'captured 128-bit public randomness and origin required') from exc
    return p, errors, int.from_bytes(seed, 'big')


def _divergence(observed, mean):
    if observed == 0:
        return mean
    return mean - observed + observed * math.log(observed / mean)


def expected_interval(observed, trials, epsilon):
    """Chernoff inverse via independent Brent solve; each interval failure <=eps."""
    limit = math.log(2 / epsilon)
    if observed == 0:
        return 0., min(float(trials), limit)
    def root(mean):
        return _divergence(observed, mean) - limit
    lower = brentq(root, max(1e-300, observed * 1e-15), float(observed), xtol=1e-12)
    upper_limit = max(2. * observed, observed + 2. * limit)
    while root(upper_limit) < 0:
        upper_limit *= 2.
    upper = brentq(root, float(observed), upper_limit, xtol=1e-12)
    return lower, min(float(trials), upper)


def realized_interval(mean, maximum, epsilon):
    limit = math.log(2 / epsilon)
    if mean <= 0:
        return 0., 0.
    def root(observed):
        return _divergence(observed, mean) - limit
    lower = 0. if mean <= limit else brentq(root, 0., mean, xtol=1e-12)
    upper_limit = max(mean * 2., mean + 2. * limit)
    while root(upper_limit) < 0:
        upper_limit *= 2.
    upper = brentq(root, mean, upper_limit, xtol=1e-12)
    return lower, min(float(maximum), upper)


def estimate_bounds(raw, p):
    weak, strong = p['weak_mu'], p['strong_mu']
    xi = p['eps']['statistics']
    gains = {}
    for name in STRATA:
        n, k = row(raw['strata'][name], name)
        low, high = expected_interval(k, n, xi)
        gains[name] = (low / n, high / n)
    numerator = strong ** 2 * math.exp(weak) * (gains['01'][0] + gains['10'][0])
    numerator -= weak ** 2 * math.exp(strong) * (gains['02'][1] + gains['20'][1])
    numerator -= 2 * (strong ** 2 - weak ** 2) * gains['00'][1]
    yield_lower = min(1., max(0., numerator / (2 * weak * strong * (strong - weak))))
    phase_n, _ = row(raw['phase_slice'], 'phase_slice')
    _, phase_high = expected_interval(raw['phase_slice']['wrong_detector_clicks'], phase_n, xi)
    denominator = 2 * weak * math.exp(-2 * weak) * yield_lower
    phase_expected = min(.5, max(0., (phase_high / phase_n - .5 * math.exp(-2 * weak) * gains['00'][0]) /
                                        denominator)) if denominator else .5
    z_trials, z_clicks = row(raw['signal'], 'signal')
    mean = z_trials * 2 * p['send_probability'] * (1 - p['send_probability'])
    mean *= p['signal_mu'] * math.exp(-p['signal_mu']) * yield_lower
    n1, _ = realized_interval(mean, z_trials, xi)
    _, phase_count = realized_interval(mean * phase_expected, z_trials, xi)
    return {'single_photon_yield_lower': yield_lower, 'phase_error_expected_upper': phase_expected,
            'phase_error_observed_upper': min(.5, phase_count / mean) if mean else .5,
            'untagged_bits_lower': min(z_clicks, math.floor(n1)), 'gain_intervals': gains,
            'statistical_intervals': 8, 'statistical_union_bound': 8 * xi,
            'bound_reference': 'Jiang et al. arXiv:1904.00192v3 Eqs.15,16,17-26; conditional symmetric SNS sources'}


def process_observation(raw, context):
    p, errors, seed = validate_observation(raw, context)
    exchange_callback = context.get('classical_exchange')
    if context.get('require_classical_transport') and not callable(exchange_callback):
        raise ProtocolError('classical_unavailable', 'finite SNS requires admitted classical transport')
    receipts = []

    def exchange(stage, payload):
        payload_sha = digest({'stage': stage, 'payload': payload})
        if callable(exchange_callback):
            receipt = exchange_callback(stage, payload)
            if not isinstance(receipt, dict) or receipt.get('accepted') is not True:
                raise ProtocolError('classical_rejected', 'SNS public disclosure was not received/admitted')
            for field in ('message_id', 'receipt_sha256'):
                if not isinstance(receipt.get(field), str) or not receipt[field]:
                    raise ProtocolError('classical_rejected', 'SNS transport receipt identity missing')
            sent = number(receipt.get('transmitted_at_s'), 'SNS transmitted_at_s')
            number(receipt.get('received_at_s'), 'SNS received_at_s', sent)
            receipts.append({'stage': stage, 'payload_sha256': payload_sha, **receipt})
        else:
            receipts.append({'stage': stage, 'payload_sha256': payload_sha, 'accepted': False,
                             'scope': 'local diagnostic disclosure; no classical transmission claimed'})

    records = raw['signal_records']
    # Public Charlie herald/sifting data contains pulse IDs and detectors, never
    # Z send/not-send choices which define the private raw key bits.
    exchange('sifting', {'trial_ids': records['trial_id'], 'charlie_detector': records['charlie_detector']})
    exchange('parameter_estimation', {'strata': raw['strata'], 'phase_slice': raw['phase_slice']})
    bound = estimate_bounds(raw, p)
    a = np.asarray(records['alice_send'], dtype=np.uint8)
    b = 1 - np.asarray(records['bob_send'], dtype=np.uint8)
    count_z = len(a)
    verified = False
    leak = corrected = amplified = 0
    verification = math.ceil(math.log2(1 / p['eps']['verification']))
    rng = np.random.default_rng(seed)
    if count_z:
        b, leak, corrected = reconcile(a, b, rng, errors / count_z,
                                        context['parameters'].get('reconciliation_passes', 8), classical_exchange=exchange)
        verify_seed = rng.integers(0, 2, count_z + verification - 1, dtype=np.uint8)
        alice_tag = toeplitz(a, verify_seed, verification)
        exchange('verification', {'seed_bits': verify_seed.tolist(), 'alice_tag': alice_tag.tolist(),
                                  'verification_bits': verification})
        verified = bool(np.array_equal(alice_tag, toeplitz(b, verify_seed, verification)))
    penalty = math.log2(2 / p['eps']['verification'])
    penalty += 2 * math.log2(1 / (math.sqrt(2) * p['eps']['privacy'] * p['eps']['smoothing']))
    budget = max(0, math.floor(bound['untagged_bits_lower'] * (1 - entropy(bound['phase_error_observed_upper'])) - leak - penalty))
    reason = ''
    if not count_z:
        reason = 'no_sifted_key'
    elif not verified:
        reason = 'reconciliation_verification_failed'
    elif budget <= 0:
        reason = 'finite_sns_budget_nonpositive'
    if not reason:
        pa_seed = rng.integers(0, 2, count_z + budget - 1, dtype=np.uint8)
        exchange('privacy_amplification', {'seed_bits': pa_seed.tolist(), 'output_length': budget})
        # Both parties compute the actual Toeplitz output. Only its length is
        # published; the material is discarded and never returned by this API.
        amplified = budget if np.array_equal(toeplitz(a, pa_seed, budget), toeplitz(b, pa_seed, budget)) else 0
        if not amplified:
            reason = 'privacy_amplification_verification_failed'
    stages = ['sifting', 'finite_decoy_estimation', 'finite_phase_error_estimation']
    if count_z:
        stages.extend(('parity_reconciliation', 'universal_hash_verification'))
    if amplified:
        stages.append('toeplitz_privacy_amplification')
    return {'protocol': 'SNS-TF finite-block conditional demonstration', 'estimation_mode': 'finite_sns_v2',
            'qber': errors / count_z if count_z else None, 'sifted_bits': count_z,
            'single_photon_yield_lower_estimate': bound['single_photon_yield_lower'],
            'untagged_bits_lower_estimate': bound['untagged_bits_lower'],
            'phase_error_upper': bound['phase_error_observed_upper'], 'finite_key': {**bound, 'budget_bits': budget,
                'security_penalty_bits': penalty, 'abort_reason': reason},
            'error_correction_leak_bits': leak, 'reconciliation_corrected_bits': corrected,
            'reconciliation_verified': verified, 'verification_tag_bits': verification,
            'conditional_key_estimate_bits': amplified, 'privacy_amplification_executed_bits': amplified,
            'privacy_amplification_budget_bits': budget, 'certified_secret_bits': 0,
            'classical_transmission_verified': bool(exchange_callback) and all(r.get('accepted') is True for r in receipts),
            'classical_exchange_count': len(receipts),
            'classical_receipts_sha256': digest(receipts),
            'security_status': 'not_certified: finite SNS numerical model; composable implementation/source/authentication qualification pending',
            'phase_error_assumption': 'joint-source finite SNS bound; symmetric trusted preparation assumptions, not aggregate X surrogate',
            'randomness_origin': raw['public_randomness']['origin'], 'parameter_provenance': raw.get('parameter_provenance', {}),
            'postprocessing': stages, 'postprocessing_skipped': [] if amplified else
                [{'stage': 'toeplitz_privacy_amplification', 'reason': reason}],
            'physical_action_performed': False, 'key_material_exported': False}
