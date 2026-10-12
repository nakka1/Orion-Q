"""SNS source/detector observations through the ordinary acquisition interface.

The generator emits source strata, herald detector counts, phase-slice counts and
private Z preparation records. It emits no yield, phase-bound, final key, QBER or
success status. Binomial thinning integrates independent Poisson threshold
events without allocating every lost pulse. Source trust is an explicit simulated
assumption, never evidence of qualified physical preparation or authentication.
"""
from __future__ import annotations

import math

import numpy as np
from scipy.integrate import quad
from scipy.special import i0

from .contracts import ProtocolError, number
from .physics import transmission
from .tf_processing import SCHEMA, integer, parameters as validate_parameters


def _single_click(left_mean, right_mean, dark):
    left_clear = (1 - dark) * math.exp(-left_mean)
    right_clear = (1 - dark) * math.exp(-right_mean)
    return left_clear * (1 - right_clear) + right_clear * (1 - left_clear)


def generate_observation(rng, command, config, environment, p, *, source_id, origin, sequence):
    selected = validate_parameters(p)
    start = command.emitted_at_s
    duration = number(command.arguments.get('duration_s'), 'duration_s', 1e-9, 60.)
    pulses = integer(int(duration * p.get('pulse_rate_hz', 20000000.)), 'emitted_pulses')
    pz = number(p.get('tf_z_probability', .5), 'tf_z_probability', .01, .99)
    p0 = number(p.get('tf_vacuum_probability', .3), 'tf_vacuum_probability', .001, .99)
    p1 = number(p.get('tf_weak_probability', .4), 'tf_weak_probability', .001, .99)
    if p0 + p1 >= 1:
        raise ProtocolError('invalid_parameter', 'TF decoy probabilities must leave a strong-source category')
    send = selected['send_probability']
    weak, strong, signal = selected['weak_mu'], selected['strong_mu'], selected['signal_mu']
    delta = selected['delta_rad']
    degraded = environment.get('condition', config.scenario) == 'degraded'
    length = [number(value, 'arm_length_km', 0., 1e6) * p.get('route_factor', 1.2)
              for value in command.arguments['arm_lengths_km']]
    if len(length) != 2:
        raise ProtocolError('invalid_parameter', 'two TF arm lengths required')
    jitter = p.get('timing_jitter_ps', 50.) * (8 if degraded else 1)
    gate = math.erf(p.get('gate_half_width_ps', 200.) / (math.sqrt(2) * max(jitter, 1e-6)))
    eta = [transmission(distance, p.get('attenuation_db_km', .2), p.get('insertion_loss_db', 2.) +
                        p.get('extra_loss_db', 0.) + (12 if degraded else 0)) * p.get('detector_efficiency', .8) * gate
           for distance in length]
    dark = number(p.get('dark_probability', 1e-7), 'dark_probability', 0., .1)
    residual = float(rng.normal(0, p.get('phase_sigma_rad', .05) * (8 if degraded else 1)))
    visibility = math.cos(residual) * math.cos(p.get('polarization_error_rad', .03)) ** 2
    intrinsic = number(p.get('tf_misalignment_error', .005), 'tf_misalignment_error', 0., .5)
    # Independent sender source/window choices: each counted stratum includes
    # only vacuum+one-arm decoy states as defined in Jiang Eq.13. Conditional
    # allocation counts are exposed separately from the herald detector counts.
    vacuum_trials = int(((1 - pz) ** 2 * p0 ** 2 + 2 * (1 - pz) * pz * p0 * (1 - send)) * pulses)
    trials = {'00': vacuum_trials}
    for suffix, chance in (('1', p1), ('2', 1 - p0 - p1)):
        count = int(((1 - pz) ** 2 * p0 * chance + (1 - pz) * pz * (1 - send) * chance) * pulses)
        trials['0' + suffix] = trials[suffix + '0'] = count
    gains = {'00': _single_click(0, 0, dark)}
    for suffix, intensity in (('1', weak), ('2', strong)):
        gains['0' + suffix] = _single_click(intensity * eta[1] / 2, intensity * eta[1] / 2, dark)
        gains[suffix + '0'] = _single_click(intensity * eta[0] / 2, intensity * eta[0] / 2, dark)
    strata = {name: {'trials': count, 'clicks': int(rng.binomial(count, gains[name]))}
              for name, count in trials.items()}
    per_slice = int(delta / (2 * math.pi) * (1 - pz) ** 2 * p1 ** 2 * pulses)
    if min(trials.values()) == 0 or per_slice == 0:
        raise ProtocolError('insufficient_tf_sample', 'finite SNS block has no trial in required decoy/phase strata')

    def phase_probability(phi, wrong_only):
        total = weak * sum(eta)
        interference = 2 * weak * math.sqrt(math.prod(eta)) * visibility * math.cos(phi)
        bright, faint = (total + interference) / 2, (total - interference) / 2
        wrong = (1 - dark) * math.exp(-bright) * (1 - (1 - dark) * math.exp(-faint))
        right = (1 - dark) * math.exp(-faint) * (1 - (1 - dark) * math.exp(-bright))
        return (1 - intrinsic) * wrong + intrinsic * right if wrong_only else wrong + right

    wrong_gain = quad(lambda phi: phase_probability(phi, True), -delta / 2, delta / 2, epsabs=1e-14)[0] / delta
    total_gain = quad(lambda phi: phase_probability(phi, False), -delta / 2, delta / 2, epsabs=1e-14)[0] / delta
    slice_counts = [rng.multinomial(per_slice, [wrong_gain, total_gain - wrong_gain, 1 - total_gain]) for _ in range(2)]
    phase_errors = sum(int(values[0]) for values in slice_counts)
    phase_clicks = sum(int(values[0] + values[1]) for values in slice_counts)
    z_trials = int(pz ** 2 * pulses)
    if not z_trials:
        raise ProtocolError('insufficient_tf_sample', 'finite SNS requires observed signal trials')
    choices = rng.multinomial(z_trials, [(1 - send) ** 2, send * (1 - send), send * (1 - send), send ** 2])
    total_mean = signal * sum(eta)
    both_gain = 2 * (1 - dark) * math.exp(-total_mean / 2) * float(i0(signal * math.sqrt(math.prod(eta))))
    both_gain -= 2 * (1 - dark) ** 2 * math.exp(-total_mean)
    gains_z = [gains['00'], _single_click(signal * eta[0] / 2, signal * eta[0] / 2, dark),
               _single_click(signal * eta[1] / 2, signal * eta[1] / 2, dark), min(1., max(0., both_gain))]
    counts = [int(rng.binomial(int(allocated), gain)) for allocated, gain in zip(choices, gains_z)]
    z_clicks = sum(counts)
    if z_clicks > selected['maximum']:
        raise ProtocolError('tf_backpressure', 'SNS detector block exceeds processing capacity; reduce block duration or source rate')
    # Observed preparations define the raw bits; no matching/final bit strings
    # are inserted. Both-send and neither-send detector clicks remain errors.
    prep_a = np.concatenate([np.full(n, value, dtype=np.uint8) for n, value in zip(counts, (0, 1, 0, 1))])
    prep_b = np.concatenate([np.full(n, value, dtype=np.uint8) for n, value in zip(counts, (0, 0, 1, 1))])
    order = rng.permutation(z_clicks)
    ids = np.sort(rng.choice(z_trials, size=z_clicks, replace=False)).astype(np.int64)
    times = np.floor((ids.astype(float) + .5) / z_trials * duration * 1e12).astype(np.int64)
    public_seed = rng.integers(0, 256, 16, dtype=np.uint8).tobytes().hex()
    return {'tf_schema': SCHEMA, 'source_id': source_id, 'origin': origin, 'sequence': sequence,
            'start_s': start, 'end_s': start + duration, 'emitted_pulses': pulses,
            'strata': strata, 'phase_slice': {'trials': 2 * per_slice, 'clicks': phase_clicks,
                'wrong_detector_clicks': phase_errors, 'delta_rad': delta,
                'selection_rule': 'wrapped_relative_phase_near_zero_or_pi',
                'plus_right_errors': int(slice_counts[0][0]), 'minus_left_errors': int(slice_counts[1][0])},
            'signal': {'trials': z_trials, 'clicks': z_clicks, 'errors': counts[0] + counts[3]},
            'signal_records': {'preparation_basis': 'Z', 'clock_reference': 'network_simulated_clock',
                'trial_id': ids.tolist(), 'time_ps': times.tolist(), 'alice_send': prep_a[order].tolist(),
                'bob_send': prep_b[order].tolist(), 'charlie_detector': rng.integers(0, 2, z_clicks).tolist()},
            'clock': {'reference': 'network_simulated_clock', 'uncertainty_ps': round(jitter),
                      'offset_ps': round(p.get('clock_offset_s', 0.) * 1e12)},
            'source_preparation': {'phase_randomized': True, 'independent_choices': True,
                'one_detector_heralding': True, 'phase_slice_selection': True, 'signal_phases_disclosed': False,
                'trust_scope': 'simulated declared source preparation; no hardware certification',
                'senders': [{'node_id': node, 'signal_mu': signal, 'weak_mu': weak, 'strong_mu': strong,
                             'send_probability': send} for node in config.endpoints]},
            'public_randomness': {'seed_hex': public_seed, 'origin': 'seeded simulated public randomness; not a qualified QRNG'},
            'phase_reference_residual_rad': residual, 'arm_transmissions': eta,
            'effective_arm_lengths_km': length, 'timing_jitter_ps': jitter,
            'parameter_provenance': {'protocol': 'SNS four-intensity symmetric source reference: arXiv:1904.00192v3',
                'optics': 'two Poisson threshold detectors and coherent 50:50 interference; engineering assumptions',
                'source': source_id, 'origin': origin, 'counts_scope': 'conditional source/window counts and detected events',
                'data_classification': 'private synthetic preparation evidence; never public Z disclosure'}}
