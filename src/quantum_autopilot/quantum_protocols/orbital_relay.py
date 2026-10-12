"""Admission/processing of coherent orbital memory-pair observations.

Architecture and simulated validation only. The state model is a Werner
ensemble with an explicit logical Bell frame, not hardware tomography or a
general density-matrix simulator. Measured BBM92/TDC records are incompatible.
"""
from __future__ import annotations

from .contracts import ProtocolError, number
from .isl import terminal_parameters
from .orbits import validate_orbit
from .orbital_relay_simulation import ENCODING, ORBITAL_SCHEMA, capture_geometry, validate_housekeeping
from .protocols import MemoryAdapter, SwappingAdapter, capabilities, count


class OrbitalRelayAdapter:
    technology_id = 'orbital_relay'
    required_capabilities = ('quantum_memory', 'heralded_photon_capture', 'tracking')

    def validate(self, config, topology):
        if not {'quantum_memory', 'entanglement_swapping'} <= set(config.technologies):
            raise ProtocolError('incompatible_protocol', 'orbital relay requires memories and swapping')
        if set(config.technologies) & {'satellite_isl', 'satellite_doppler', 'tf_qkd'}:
            raise ProtocolError('incompatible_protocol', 'destructive measured QKD cannot masquerade as a memory relay')
        MemoryAdapter().validate(config, topology)
        SwappingAdapter().validate(config, topology)
        p = config.parameters
        bank_id = p.get('resource_bank_id')
        if (not isinstance(bank_id, str) or not bank_id or
                p.get('resource_bank_scope') != 'dedicated_per_session'):
            raise ProtocolError('incompatible_resource_bank', 'explicit dedicated resource bank required')
        for ident in config.path:
            banks = topology.node(ident).telemetry.get('simulation_resource_banks', ())
            if not isinstance(banks, (list, tuple)):
                raise ProtocolError('incompatible_resource_bank', f'{ident}: resource bank inventory must be a list')
            claims = [bank for bank in banks if isinstance(bank, dict)
                      and bank.get('resource_bank_id') == bank_id]
            # This driver owns an explicitly dedicated bank, not a shared or
            # distributed allocator. A second session must register its own
            # equipment instead of acquiring the same modes in another list.
            if (len(claims) != 1 or claims[0].get('session_id') != config.session_id or
                    claims[0].get('exclusive_device') is not True):
                raise ProtocolError('incompatible_resource_bank', f'{ident}: resource bank not owned by this session')
            modes = claims[0].get('memory_modes')
            requested = terminal_parameters(p, ident).get('memory_slots', 4)
            if (type(requested) is not int or not 2 <= requested <= 1024 or
                    type(modes) is not int or modes < requested):
                raise ProtocolError('incompatible_resource_bank', f'{ident}: resource bank memory capacity exceeded')
        orbits = p.get('satellite_orbits', {})
        if not isinstance(orbits, dict) or not orbits or not set(orbits) <= set(config.path):
            raise ProtocolError('invalid_ephemeris', 'explicit orbital record required for each relay satellite')
        for orbit in orbits.values():
            validate_orbit(orbit)
        if p.get('memory_heralding_kind') != 'ancillary_capture_herald':
            raise ProtocolError('incompatible_protocol', 'explicit non-destructive ancillary capture herald required')
        for ident in config.path:
            caps = capabilities(topology, ident)
            terminal = 'optical_space_terminal' if ident in orbits else 'optical_ground_terminal'
            if not set(self.required_capabilities + (terminal,)) <= caps:
                raise ProtocolError('incompatible_node', f'{ident}: memory capture/tracking/optical terminal absent')
            q = terminal_parameters(p, ident)
            if q.get('quantum_encoding') != ENCODING or q.get('optical_duplex') != 'dedicated_quantum_and_classical':
                raise ProtocolError('incompatible_protocol', f'{ident}: separate quantum/classical memory-pair terminals required')
            for field, default, low, high in (
                ('transmitter_diameter_m', .5, .01, 20.), ('receiver_diameter_m', .5, .01, 40.),
                ('wavelength_nm', 850., 200., 2000.), ('divergence_urad', 1., .001, 10000.),
                ('optical_efficiency', .5, 0., 1.), ('detector_efficiency', .8, 0., 1.),
                ('herald_detector_efficiency', .8, 0., 1.), ('memory_write_efficiency', .7, 0., 1.),
                ('memory_read_efficiency', .7, 0., 1.), ('pointing_jitter_urad', .1, 0., 10000.),
                ('timing_jitter_ps', 50., 0., 1e9), ('herald_dark_probability', 1e-9, 0., 1.),
            ):
                number(q.get(field, default), field, low, high)
            if 'satellite_source' not in caps and ident in orbits:
                raise ProtocolError('incompatible_node', f'{ident}: retained-half entangled source absent')
        for a, b in zip(config.path, config.path[1:]):
            if not ({a, b} & set(orbits)):
                raise ProtocolError('unsupported_composition', 'this driver requires an orbital endpoint in every segment')
            if abs(terminal_parameters(p, a).get('wavelength_nm', 850.) -
                   terminal_parameters(p, b).get('wavelength_nm', 850.)) > .1:
                raise ProtocolError('incompatible_protocol', 'memory optical wavelengths incompatible')
        samples = p.get('tomography_max_per_axis', 300)
        if type(samples) is not int or not 1 <= samples <= 100000:
            raise ProtocolError('invalid_parameter', 'tomography_max_per_axis: integer [1,100000] required')

    def process(self, raw, context):
        if raw.get('orbital_relay_schema') != ORBITAL_SCHEMA or raw.get('satellite_schema') or raw.get('isl_schema'):
            raise ProtocolError('incompatible_observation', 'memory acquisition schema required; measured BBM92 is consumed')
        if any(key in raw for key in ('tdc', 'sifted_bits', 'final_fidelity', 'final_key', 'secure_key')):
            raise ProtocolError('incompatible_observation', 'memory relay cannot admit destructively measured or final-result records')
        operation = context['operation']
        if raw.get('operation', operation) != operation:
            raise ProtocolError('invalid_observation', 'acquisition operation disagrees with bound command')
        p = context['parameters']
        housekeeping = raw.get('housekeeping', {})
        if not isinstance(housekeeping, dict) or set(housekeeping) != set(p.get('satellite_orbits', {})):
            raise ProtocolError('invalid_binding', 'one received state per configured relay satellite required')
        if any(not isinstance(state, dict) for state in housekeeping.values()):
            raise ProtocolError('invalid_ephemeris', 'received orbital states must be dictionaries')
        grounds = raw.get('ground_stations', {})
        if not isinstance(grounds, dict):
            raise ProtocolError('invalid_station', 'OGS records must be a dictionary')
        registered = context.get('environment', {}).get('node_positions', {})
        for ident, station in grounds.items():
            expected = registered.get(ident)
            if expected is not None and (not isinstance(station, dict) or any(
                abs(number(station.get(field), field, low, high) - expected[field]) > 1e-7
                for field, low, high in (('latitude', -90., 90.), ('longitude', -180., 180.))
            )):
                raise ProtocolError('invalid_binding', 'reported OGS coordinates disagree with registered installation')
        start = number(raw.get('start_s'), 'orbital_observation_start_s')
        if abs(start - context.get('command_emitted_at_s', start)) > p.get('sync_tolerance_s', 1e-6):
            raise ProtocolError('invalid_binding', 'ephemeris epoch does not match the bound command')
        for state in raw.get('housekeeping', {}).values():
            validate_housekeeping(state)
            if abs(number(state.get('at_s'), 'ephemeris_epoch_s') - start) > p.get('sync_tolerance_s', 1e-6):
                raise ProtocolError('unsynchronized', 'received orbital vectors do not share command epoch')
            number(state.get('valid_until_s'), 'ephemeris_valid_until_s', start)
            if not isinstance(state.get('source'), str) or not state['source']:
                raise ProtocolError('invalid_ephemeris', 'orbital state provenance required')
        if operation == 'generate':
            endpoints = raw.get('segment_endpoints')
            if not isinstance(endpoints, list) or len(endpoints) != 2 or endpoints[0] == endpoints[1]:
                raise ProtocolError('invalid_observation', 'two distinct memory segment endpoints required')
            expected = context.get('observed_endpoints')
            if expected is not None and tuple(endpoints) != tuple(expected):
                raise ProtocolError('invalid_binding', 'capture endpoints do not match the requested segment')
            if raw.get('heralding_kind') != 'ancillary_capture_herald':
                raise ProtocolError('incompatible_observation', 'destructive detector clicks are not memory-capture heralds')
            attempts = count(raw, 'attempts')
            count(raw, 'source_emission_triggers', attempts)
            heralds = count(raw, 'heralds', attempts)
            candidates = count(raw, 'optical_capture_candidates', attempts)
            rejected = count(raw, 'capture_capacity_rejections', candidates)
            if candidates != heralds + rejected:
                raise ProtocolError('invalid_counts', 'capture candidates must equal admitted heralds plus capacity rejections')
            slots = min(terminal_parameters(p, n).get('memory_slots', 4) for n in endpoints)
            budget = count(raw, 'capture_mode_budget', slots)
            requested = context.get('capture_mode_budget')
            if requested is not None and budget != requested:
                raise ProtocolError('invalid_binding', 'reported memory budget disagrees with the reserved command modes')
            if count(raw, 'memory_writes', heralds) > budget:
                raise ProtocolError('invalid_counts', 'reported memory writes exceed the exclusive capture budget')
            discarded = count(raw, 'uncharacterized_pairs_discarded', heralds)
            if 'detector_dark_clicks' in raw:
                count(raw, 'detector_dark_clicks', candidates)
            g = capture_geometry(endpoints, raw.get('housekeeping', {}), raw.get('ground_stations', {}),
                                 p, raw.get('tracking', {}), end_s=raw.get('end_s'))
            # Additional channel losses are observations of the optical train,
            # never permission to report arrivals for an obstructed path.
            eta = number(raw.get('optical_transmission'), 'optical_transmission', 0., 1.)
            if eta > g['channel_transmission'] + 1e-12:
                raise ProtocolError('invalid_observation', 'reported channel transmission exceeds admitted optical budget')
            if not g['visible'] and heralds:
                raise ProtocolError('invalid_observation', 'capture heralds reported on an unavailable optical segment')
            flight = number(raw.get('quantum_flight_s'), 'quantum_flight_s', 0., 10.)
            if abs(flight - g['flight_time_s']) > p.get('sync_tolerance_s', 1e-6):
                raise ProtocolError('invalid_observation', 'quantum flight time disagrees with received ephemeris')
            number(raw.get('effective_length_km'), 'effective_length_km', 0., 1e6)
            if abs(raw['effective_length_km'] - g['range_km']) > 1e-6:
                raise ProtocolError('invalid_observation', 'orbital length must be slant range, not ground distance')
            end = number(raw.get('end_s'), 'orbital_observation_end_s', start)
            if abs(end - start - context.get('duration_s', end - start)) > p.get('sync_tolerance_s', 1e-6):
                raise ProtocolError('invalid_binding', 'capture interval does not match the command duration')
            if context.get('observation_at_s', end + flight) + p.get('sync_tolerance_s', 1e-6) < end + flight:
                raise ProtocolError('causality_violation', 'capture report precedes quantum payload flight')
            if not raw.get('correlations'):
                if count(raw, 'memory_writes', heralds) or discarded != heralds:
                    raise ProtocolError('missing_calibration', 'uncalibrated heralds must be discarded without admitted memories')
                result = {'generated_pairs': heralds, 'tomography_consumed': discarded,
                          'available_pairs': 0, 'fidelity': None, 'calibration_available': False}
            else:
                if discarded:
                    raise ProtocolError('invalid_counts', 'calibrated and discarded populations must not overlap')
                correlations = raw['correlations']
                if not isinstance(correlations, dict) or any(not isinstance(correlations.get(axis), dict) for axis in ('xx', 'yy', 'zz')):
                    raise ProtocolError('invalid_counts', 'three dictionary-valued tomography axes required')
                result = SwappingAdapter().process(raw, context)
                result['calibration_available'] = True
            g['channel_transmission'] = eta
            return {**result, 'geometry': [g], 'available': g['visible'],
                    'reason': ','.join(g.get('block_reasons', [])),
                    'satellite_housekeeping': dict(raw['housekeeping']),
                    'resource_model': 'Werner calibration estimate with explicit logical Bell frames'}
        if operation != 'bsm':
            raise ProtocolError('unsupported_operation', 'orbital relay adapter supports generate and bsm only')
        parents = raw.get('parent_ids')
        expected = list(context.get('parent_ids', []))
        if not isinstance(parents, list) or len(parents) != 2 or any(not isinstance(n, str) or not n for n in parents) or len(set(parents)) != 2 or parents != expected:
            raise ProtocolError('invalid_binding', 'BSM requires exactly two command-bound, distinct reserved parent resources')
        reads, clicks = raw.get('memory_readouts'), raw.get('detector_clicks')
        for values in (reads, clicks):
            if not isinstance(values, list) or len(values) != 2 or any(type(x) is not int or x not in (0, 1) for x in values):
                raise ProtocolError('invalid_observation', 'two binary retrieval/detection outcomes required')
        if any(c > r for c, r in zip(clicks, reads)):
            raise ProtocolError('invalid_observation', 'BSM clicks cannot appear without a memory retrieval')
        if count(raw, 'bsm_success', 1) and not all(clicks):
            raise ProtocolError('invalid_observation', 'a successful BSM requires both retrieved photons')
        if not raw['bsm_success'] and raw.get('bell_outcome') is not None:
            raise ProtocolError('invalid_observation', 'failed Bell measurements have no acknowledged outcome')
        for key in ('first_frame', 'second_frame'):
            frame = context.get(key, (0, 0))
            if not isinstance(frame, (list, tuple)) or len(frame) != 2 or any(type(v) is not int or v not in (0, 1) for v in frame):
                raise ProtocolError('invalid_frame', 'two binary Pauli frame bits required')
        result = SwappingAdapter().process(raw, context)
        geometries = []
        expected_path = context.get('path')
        if p.get('satellite_orbits') and (not isinstance(raw.get('geometries'), list) or not raw['geometries']):
            raise ProtocolError('missing_geometry', 'classical Bell ACK requires the orbital route geometry')
        if any(not isinstance(g, dict) or not isinstance(g.get('segment_endpoints'), list) or len(g['segment_endpoints']) != 2
               for g in raw.get('geometries', [])):
            raise ProtocolError('invalid_observation', 'Bell ACK route requires two-ended geometry records')
        if expected_path and [tuple(g.get('segment_endpoints', [])) for g in raw.get('geometries', [])] != list(zip(expected_path, expected_path[1:])):
            raise ProtocolError('invalid_binding', 'Bell ACK geometries disagree with the configured route')
        for received in raw.get('geometries', []):
            geometries.append(capture_geometry(received['segment_endpoints'], raw.get('housekeeping', {}),
                                               raw.get('ground_stations', {}), p, raw.get('tracking', {})))
        return {**result, 'geometry': geometries, 'parents_consumed': list(parents),
                'satellite_housekeeping': dict(raw.get('housekeeping', {})),
                'resource_model': 'two reserved parents consumed; output eligible only after classical Pauli ACK'}
