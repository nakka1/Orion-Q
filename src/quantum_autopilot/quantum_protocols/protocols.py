"""Replaceable processing adapters. Inputs are observations, outputs are estimates.

TF and satellite postprocessing exposes conditional budgets, not certified secret
keys: an independently reviewed finite-key proof/source trust adapter is absent.
"""
import math

from .contracts import ProtocolConfig, ProtocolError, number
from .physics import aged_fidelity, bbpssw, entropy, swap_fidelity, tomography_fidelity


def capabilities(topology, ident):
    try:
        node = topology.node(ident)
    except (KeyError, ValueError):
        raise ProtocolError('missing_node', ident) from None
    return set(node.telemetry.get('technology_capabilities', ()))


def count(raw, key, maximum=None):
    value = raw.get(key)
    if type(value) is not int or value < 0 or maximum is not None and value > maximum:
        raise ProtocolError('invalid_counts', f'{key}: non-negative bounded integer required')
    return value


def optional_number(raw, key, low, high):
    if key in raw:
        number(raw[key], key, low, high)


def optional_pair(raw, key, low, high):
    if key in raw:
        values = raw[key]
        if not isinstance(values, (list, tuple)) or len(values) != 2:
            raise ProtocolError('invalid_observation', f'{key}: two arm values required')
        for value in values:
            number(value, key, low, high)


class MemoryAdapter:
    technology_id = 'quantum_memory'
    required_capabilities = ('quantum_memory',)

    def validate(self, config, topology):
        p = config.parameters
        number(p.get('coherence_s', 1.), 'coherence_s', 1e-6, 1e6)
        number(p.get('memory_write_efficiency', .7), 'memory_write_efficiency', 0., 1.)
        number(p.get('memory_read_efficiency', .7), 'memory_read_efficiency', 0., 1.)
        if type(p.get('memory_slots', 4)) is not int or not 2 <= p.get('memory_slots', 4) <= 1024:
            raise ProtocolError('invalid_parameter', 'memory_slots: integer [2,1024] required')
        for ident in config.path:
            if not set(self.required_capabilities) <= capabilities(topology, ident):
                raise ProtocolError('incompatible_node', f'{ident}: memory capability absent')

    def process(self, raw, context):
        elapsed = number(context['elapsed_s'], 'storage_duration_s')
        return {'fidelity': aged_fidelity(context['fidelity'], elapsed, context['coherence_s']),
                'storage_duration_s': elapsed, 'retrieval_success': count(raw,'readout',1) == 1}


class SwappingAdapter:
    technology_id = 'entanglement_swapping'
    required_capabilities = ('bell_measurement',)

    def validate(self, config, topology):
        if 'quantum_memory' not in config.technologies:
            raise ProtocolError('incompatible_protocol', 'this heralded repeater driver requires memories')
        for ident in config.path[1:-1]:
            if not set(self.required_capabilities) <= capabilities(topology, ident):
                raise ProtocolError('incompatible_node', f'{ident}: BSM capability absent')
        number(config.parameters.get('bsm_success', .5), 'bsm_success', 0., 1.)
        if config.parameters.get('bsm_kind','linear_optics_unassisted') not in ('linear_optics_unassisted','abstract_heralded'):
            raise ProtocolError('invalid_parameter','unsupported bsm_kind')
        if config.parameters.get('bsm_kind','linear_optics_unassisted') == 'linear_optics_unassisted' and config.parameters.get('bsm_success',.5) > .5:
            raise ProtocolError('invalid_parameter','unassisted linear optical BSM success cannot exceed 0.5')
        number(config.parameters.get('operation_error', .005), 'operation_error', 0., 1.)
        number(config.parameters.get('attenuation_db_km', .2), 'attenuation_db_km', 0., 10.)
        number(config.parameters.get('route_factor', 1.2), 'route_factor', 1., 10.)

    def process(self, raw, context):
        if context['operation'] == 'generate':
            attempted = count(raw, 'attempts')
            generated = count(raw, 'heralds', attempted)
            optional_number(raw, 'optical_transmission', 0., 1.)
            optional_number(raw, 'effective_length_km', 0., 1e6)
            if 'memory_writes' in raw:
                count(raw, 'memory_writes', generated)
            if generated == 0:
                if raw.get('correlations'):
                    raise ProtocolError('invalid_counts', 'calibration counts without generated pairs')
                return {'generated_pairs':0, 'tomography_consumed':0, 'fidelity':None, 'available_pairs':0}
            samples = 0
            for axis in ('xx', 'yy', 'zz'):
                row = raw.get('correlations', {}).get(axis, {})
                n = count(row, 'equal') + count(row, 'opposite')
                if n == 0 and generated:
                    raise ProtocolError('missing_calibration', 'tomography sample absent')
                samples += n
            if samples > generated:
                raise ProtocolError('invalid_counts', 'tomography consumes more pairs than generated')
            if raw.get('memory_writes', 0) > generated - samples:
                raise ProtocolError('invalid_counts', 'memory writes exceed pairs left after tomography')
            return {'generated_pairs': generated, 'tomography_consumed': samples,
                    'fidelity': tomography_fidelity(raw) if samples else None,
                    'available_pairs': generated - samples}
        success = count(raw, 'bsm_success', 1) == 1
        outcome = count(raw, 'bell_outcome', 3) if success else None
        if success and context.get('parameters',{}).get('bsm_kind','linear_optics_unassisted')=='linear_optics_unassisted' and outcome not in (2,3):
            raise ProtocolError('invalid_observation','unassisted linear optics only heralds the configured psi outcomes')
        first=context.get('first_frame',(0,0));second=context.get('second_frame',(0,0))
        frame=(first[0]^second[0]^(outcome//2),first[1]^second[1]^(outcome%2)) if success else None
        return {'success': success, 'bell_outcome':outcome,
                'pauli_frame_bits':frame,'pauli_frame': {'x':frame[0], 'z':frame[1]} if success else None,
                'fidelity': swap_fidelity(context['first_fidelity'], context['second_fidelity'],
                                                           context['operation_error']) if success else None}


class PurificationAdapter:
    technology_id = 'purification'
    required_capabilities = ('bilateral_cnot', 'quantum_memory')

    def validate(self, config, topology):
        if 'quantum_memory' not in config.technologies:
            raise ProtocolError('incompatible_protocol', 'purification requires stored pairs')
        for ident in config.path:
            if not set(self.required_capabilities) <= capabilities(topology, ident):
                raise ProtocolError('incompatible_node', f'{ident}: local purification operations absent')
        number(config.parameters.get('purification_operation_error', .01), 'purification_operation_error', 0., 1.)

    def process(self, raw, context):
        first, second = context['first_fidelity'], context['second_fidelity']
        a, b = count(raw, 'alice_outcome', 1), count(raw, 'bob_outcome', 1)
        probability, fidelity = bbpssw(first, second, context['operation_error'])
        first_frame=context.get('first_frame',(0,0));second_frame=context.get('second_frame',(0,0))
        accepted = (a^b)==(first_frame[0]^second_frame[0]) and first > .5 and second > .5
        frame=(first_frame[0],first_frame[1]^second_frame[1])
        return {'accepted': accepted, 'success_probability': probability, 'fidelity': fidelity if accepted else None,
                'pauli_frame_bits':frame if accepted else None,
                'pairs_consumed': 2, 'pairs_retained': int(accepted), 'protocol': 'BBPSSW unequal Werner pairs'}


class TwinFieldAdapter:
    technology_id = 'tf_qkd'
    required_capabilities = ('coherent_sender', 'phase_reference')

    def validate(self, config, topology):
        if len(config.path) != 3:
            raise ProtocolError('incompatible_protocol', 'SNS TF requires two senders and one central interference node')
        for ident in config.endpoints:
            if not set(self.required_capabilities) <= capabilities(topology, ident):
                raise ProtocolError('incompatible_node', f'{ident}: coherent sender/phase reference absent')
        if not {'interferometer', 'single_photon_detector'} <= capabilities(topology, config.path[1]):
            raise ProtocolError('incompatible_node', 'central station needs interference and detectors')
        p = config.parameters
        mu, nu = p.get('signal_mu', .4), p.get('decoy_mu', .1)
        number(mu, 'signal_mu', .001, 2.); number(nu, 'decoy_mu', .001, 1.)
        if nu >= mu:
            raise ProtocolError('invalid_parameter', 'decoy_mu < signal_mu required')
        number(p.get('send_probability', .08), 'send_probability', .001, .5)
        number(p.get('phase_sigma_rad', .05), 'phase_sigma_rad', 0., 10.)

    def process(self, raw, context):
        optional_number(raw, 'phase_reference_residual_rad', -1e9, 1e9)
        optional_pair(raw, 'arm_transmissions', 0., 1.)
        optional_pair(raw, 'effective_arm_lengths_km', 0., 1e6)
        trials = count(raw, 'z_trials'); clicks = count(raw, 'z_clicks', trials)
        errors = count(raw, 'z_errors', clicks)
        x_clicks = count(raw, 'x_clicks', count(raw, 'x_trials'))
        x_errors = count(raw, 'x_errors', x_clicks)
        vacuum = raw.get('vacuum', {}); weak = raw.get('weak', {}); strong = raw.get('strong', {})
        estimates = []
        for row in (vacuum, weak, strong):
            n = count(row, 'trials'); k = count(row, 'clicks', n)
            if n == 0:
                raise ProtocolError('invalid_counts', 'decoy trials required')
            estimates.append((k / n, n))
        q0, qnu, qmu = (x[0] for x in estimates)
        p = context['parameters']; mu, nu = p.get('signal_mu', .4), p.get('decoy_mu', .1)
        # Standard two-intensity+vacuum decoy lower estimate under independent
        # phase-randomized, symmetric one-arm SNS sources. It is an asymptotic
        # component, not a composable finite-size SNS proof.
        y1 = max(0., min(1., mu / (mu * nu - nu * nu) * (
            qnu * math.exp(nu) - qmu * math.exp(mu) * nu * nu / (mu * mu)
            - (mu * mu - nu * nu) / (mu * mu) * q0)))
        qber = errors / clicks if clicks else None
        phase = x_errors / x_clicks if x_clicks else .5
        send = p.get('send_probability', .08)
        untagged = min(clicks, trials * 2. * send * (1. - send) * mu * math.exp(-mu) * y1)
        leak = math.ceil(clicks * p.get('ec_efficiency', 1.16) * entropy(qber or 0.))
        verification = math.ceil(math.log2(1. / p.get('verification_error', 1e-10)))
        budget = max(0, math.floor(untagged * (1. - entropy(phase)) - leak - verification)) if x_clicks else 0
        return {'protocol': 'SNS-TF reduced asymptotic model', 'qber': qber, 'sifted_bits': clicks,
                'x_phase_error_observed': phase, 'single_photon_yield_lower_estimate': y1,
                'untagged_bits_lower_estimate': untagged, 'error_correction_leak_bits': leak,
                'verification_tag_bits': verification, 'privacy_amplification_budget_bits': budget,
                'conditional_key_estimate_bits': budget, 'certified_secret_bits': 0,
                'security_status': 'not_certified: finite-key SNS phase-error proof and authenticated hardware adapter absent',
                'phase_error_assumption': 'phase-sliced X error used as surrogate, not a proven Z phase-error bound',
                'postprocessing': ['sifting', 'decoy_estimation', 'ec_leakage_accounting', 'verification_budget', 'privacy_amplification_budget']}


class SatelliteAdapter:
    technology_id = 'satellite_doppler'
    required_capabilities = ('optical_ground_terminal', 'tracking', 'time_tagger')

    def validate(self, config, topology):
        for ident in config.endpoints:
            if not set(self.required_capabilities) <= capabilities(topology, ident):
                raise ProtocolError('incompatible_node', f'{ident}: optical station capability absent')
        if len(config.path) != 3 or 'satellite_source' not in capabilities(topology, config.path[1]):
            raise ProtocolError('incompatible_node', 'two ground stations and one explicit satellite required')
        p = config.parameters
        number(p.get('altitude_km', 500.), 'altitude_km', 100., 100000.)
        number(p.get('min_elevation_deg', 20.), 'min_elevation_deg', 0., 90.)
        number(p.get('pointing_jitter_urad', 2.), 'pointing_jitter_urad', 0., 1000.)
        inclination = number(p.get('inclination_deg', 53.), 'inclination_deg', 0., 180.)
        number(p.get('orbit_phase_rad', 0.), 'orbit_phase_rad', -1e6, 1e6)
        number(p.get('doppler_residual_hz', 5e6), 'doppler_residual_hz', -1e15, 1e15)
        if p.get('orbit_record'):
            from .orbits import validate_orbit
            validate_orbit(p['orbit_record'])
            return
        from ..control_plane.geodesy import node_location
        reference = node_location(topology.node(config.endpoints[0]))
        if reference is None:
            raise ProtocolError('incompatible_node', 'orbital reference station requires a location')
        reachable = min(inclination, 180. - inclination)
        if abs(reference['latitude']) > reachable + 1e-10:
            raise ProtocolError('invalid_parameter', 'orbit inclination cannot reach the reference latitude')

    def process(self, raw, context):
        if raw.get('satellite_schema'):
            from .satellite_processing import process_observation
            return process_observation(raw,context)
        emitted = count(raw, 'emitted_pairs')
        coincident = count(raw, 'coincidences', emitted)
        errors = count(raw, 'errors', coincident)
        if 'geometry' not in raw or len(raw['geometry']) != 2:
            raise ProtocolError('invalid_acquisition', 'two ground-station ephemeris observations required')
        for item in raw['geometry']:
            number(item.get('range_km'), 'range_km', 0., 1e6)
            number(item.get('elevation_deg'), 'elevation_deg', -90., 90.)
            for key, low, high in [('channel_transmission', 0., 1.),
                    ('radial_velocity_km_s', -100., 100.), ('doppler_shift_hz', -1e15, 1e15),
                    ('doppler_residual_hz', -1e15, 1e15), ('flight_time_s', 0., 1e4)]:
                optional_number(item, key, low, high)
            if type(item.get('visible')) is not bool:
                raise ProtocolError('invalid_acquisition', 'explicit visibility required')
        qber = errors / coincident if coincident else None
        if not all(x['visible'] for x in raw['geometry']) and coincident:
            raise ProtocolError('invalid_acquisition', 'coincidences outside simultaneous visibility')
        sifted = coincident // 2
        leak = math.ceil(sifted * context['parameters'].get('ec_efficiency', 1.16) * entropy(qber or 0.))
        budget = max(0, math.floor(sifted * (1. - entropy(qber or 0.)) - leak - 34)) if coincident else 0
        return {'emitted_pairs':emitted, 'coincidences': coincident, 'pair_rate_hz': coincident / context['duration_s'],
                'qber': qber, 'sifted_bits': sifted, 'error_correction_leak_bits': leak,
                'conditional_key_estimate_bits': budget, 'certified_secret_bits': 0,
                'security_status': 'not_certified: asymptotic BBM92 error symmetry assumed; finite-key proof absent',
                'geometry': raw['geometry'], 'available': all(x['visible'] for x in raw['geometry']),
                'fidelity': tomography_fidelity(raw) if coincident and raw.get('correlations') else None,
                'postprocessing': ['coincidence_matching', 'basis_sifting', 'parameter_estimation', 'ec_leakage_accounting', 'privacy_amplification_budget']}
