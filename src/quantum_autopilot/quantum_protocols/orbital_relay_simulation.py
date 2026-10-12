"""Raw observations for a hypothetical heralded orbital memory repeater.

The remote photon is captured in a quantum memory, not measured by a TDC.
Ancillary capture heralds travel on the classical channel. Separate destructive
XX/YY/ZZ probes characterize an ensemble; they never become stored resources.
GEO telescopes/memories here are explicit design assumptions, not a mission.
"""
from __future__ import annotations

import math

from .contracts import ProtocolError
from .isl import inter_satellite_geometry, isl_optics, terminal_parameters
from .orbits import position, state_geometry
from .physics import satellite_optics

ORBITAL_SCHEMA = 'orion.orbital-memory-observation.v1'
ENCODING = 'polarization_memory_pair'


def validate_housekeeping(state):
    """Validate every state before publication, even off-segment satellites."""
    if not isinstance(state, dict) or state.get('frame') != 'ECEF':
        raise ProtocolError('invalid_ephemeris', 'relay requires normalized ECEF state dictionaries')
    for field in ('ecef_km', 'velocity_ecef_km_s'):
        values = state.get(field)
        limit = 1e6 if field == 'ecef_km' else 100.
        if not isinstance(values, (list, tuple)) or len(values) != 3 or any(
            type(v) not in (int, float) or not math.isfinite(v) or abs(v) > limit for v in values
        ):
            raise ProtocolError('invalid_ephemeris', f'{field}: three bounded finite coordinates required')
    if type(state.get('at_s')) not in (int, float) or not math.isfinite(state['at_s']):
        raise ProtocolError('invalid_ephemeris', 'state vector epoch required')


def capture_geometry(endpoints, housekeeping, ground_stations, parameters, tracking=None, *, end_s=None):
    """Recompute optical admission from received state vectors and APT records.

    A classical report describes capture/engineering observations, never
    transmits a quantum state. Frames/epoch are checked before using geometry.
    """
    if not isinstance(housekeeping, dict) or not isinstance(ground_stations, dict):
        raise ProtocolError('invalid_observation', 'housekeeping and OGS records must be dictionaries')
    tracking = {} if tracking is None else tracking
    if not isinstance(tracking, dict) or any(not isinstance(row, dict) for row in tracking.values()):
        raise ProtocolError('invalid_observation', 'APT observations must be dictionaries per terminal')
    orbital = [n for n in endpoints if n in housekeeping]
    if not orbital:
        raise ProtocolError('invalid_ephemeris', 'orbital relay segment needs a satellite')
    states = []
    for ident in orbital:
        state = housekeeping[ident]
        validate_housekeeping(state)
        states.append(state)
    first, second = (terminal_parameters(parameters, n) for n in endpoints)
    if any(q.get('quantum_encoding') != ENCODING for q in (first, second)):
        raise ProtocolError('incompatible_protocol', 'memory relay requires polarization_memory_pair terminals')
    if len(orbital) == 2:
        g = isl_optics(inter_satellite_geometry(*states, parameters), first, second,
                       allowed_encodings=(ENCODING,))
        kind = 'satellite_satellite'
        sender, receiver = endpoints
    else:
        satellite = orbital[0]
        ground = next(n for n in endpoints if n != satellite)
        station = ground_stations.get(ground)
        if not isinstance(station, dict):
            raise ProtocolError('missing_station', f'{ground}: OGS position absent')
        lat, lon = station.get('latitude'), station.get('longitude')
        if type(lat) not in (int, float) or not math.isfinite(lat) or not -90 <= lat <= 90:
            raise ProtocolError('invalid_station', 'OGS latitude must be finite [-90,90] degrees')
        if type(lon) not in (int, float) or not math.isfinite(lon) or not -180 <= lon <= 180:
            raise ProtocolError('invalid_station', 'OGS longitude must be finite [-180,180] degrees')
        sat = terminal_parameters(parameters, satellite)
        ogs = terminal_parameters(parameters, ground)
        # Pair source is onboard, one half is retained; the downlink half is
        # captured in the OGS memory. Transmission includes both optical trains.
        p = {**parameters, **sat, 'receiver_diameter_m': ogs.get('receiver_diameter_m', 1.),
             'optical_efficiency': sat.get('optical_efficiency', .5) * ogs.get('optical_efficiency', .5),
             'insertion_loss_db': sat.get('insertion_loss_db', 2.) + ogs.get('insertion_loss_db', 2.),
             'extra_loss_db': sat.get('extra_loss_db', 0.) + ogs.get('extra_loss_db', 0.),
             'min_elevation_deg': ogs.get('min_elevation_deg', parameters.get('min_elevation_deg', 10.)),
             'pointing_enabled': sat.get('pointing_enabled', True) and ogs.get('pointing_enabled', True),
             'receiver_alignment_error_urad': ogs.get('receiver_alignment_error_urad', 0.)}
        g = satellite_optics(state_geometry(states[0], (lat, lon), station.get('altitude_m', 0.)),
                             p, cloud=ogs.get('cloud', parameters.get('cloud', False)))
        g['block_reasons'] = [] if g['visible'] else ['ground_visibility_cloud_or_pointing']
        kind = 'satellite_ground'
        sender, receiver = satellite, ground
    window_blocked = any(
        parameters.get('satellite_orbits', {}).get(n, {}).get('communication_windows') and not any(
            a <= housekeeping[n]['at_s'] < b and (end_s is None or end_s <= b)
            for a, b in parameters['satellite_orbits'][n]['communication_windows']
        ) for n in orbital
    )
    if window_blocked:
        g.update(visible=False, channel_transmission=0.)
        g.setdefault('block_reasons', []).append('communication_window')
    if not all(type(tracking.get(n, {}).get('locked')) is bool for n in endpoints):
        raise ProtocolError('missing_tracking', 'explicit binary APT lock observations required at both terminals')
    if not all(tracking[n]['locked'] for n in endpoints):
        g.update(visible=False, channel_transmission=0.)
        g.setdefault('block_reasons', []).append('tracking_not_locked')
    return {**g, 'kind': kind, 'segment_endpoints': list(endpoints),
            'photon_sender': sender, 'photon_receiver': receiver,
            'resource_semantics': 'ancillary heralds; quantum payload captured unmeasured in memories',
            'physical_connection_to_orion': False}


def _states(config, environment, at_s):
    p = config.parameters
    housekeeping = {n: {**position(orbit, at_s), 'at_s': at_s,
                       'valid_until_s': at_s + p.get('timeout_s', 4.),
                       'source': 'simulated:' + orbit['source'], 'origin': 'simulated'}
                    for n, orbit in p.get('satellite_orbits', {}).items()}
    grounds = {n: loc for n, loc in environment.get('node_positions', {}).items()
               if n in config.path and loc is not None}
    return housekeeping, grounds


def _conditions(parameters, environment):
    p = dict(parameters)
    if environment.get('condition') == 'degraded':
        p['extra_loss_db'] = p.get('extra_loss_db', 0.) + 6.
        p['timing_jitter_ps'] = p.get('timing_jitter_ps', 50.) * 8.
        p['phase_sigma_rad'] = p.get('phase_sigma_rad', .05) * 8.
    return p


def generate_observation(rng, command, config, environment, parameters):
    """Generate capture events, calibration measurements and memory write logs.

    Binomial counts represent multiplexed independent emission/capture attempts;
    occupied slots are admitted by the runtime, which reports rejected capacity.
    No final fidelity or key is generated here. Correlation counts are finite,
    noisy samples and their consumed pairs are accounted for separately.
    """
    p = _conditions(parameters, environment)
    endpoints = tuple(command.arguments.get('device_endpoints', config.endpoints))
    duration = command.arguments.get('duration_s', p.get('block_s', .5))
    housekeeping, grounds = _states(config, environment, command.emitted_at_s)
    tracking = {n: {'locked': terminal_parameters(p, n).get('pointing_enabled', True)} for n in endpoints}
    g = capture_geometry(endpoints, housekeeping, grounds, p, tracking,
                         end_s=command.emitted_at_s + duration)
    if environment.get('condition') == 'unavailable':
        g.update(visible=False, channel_transmission=0., block_reasons=['scenario_unavailable'])
    attempts = max(0, int(duration * p.get('attempt_rate_hz', 100000.)))
    emitted = int(rng.binomial(attempts, p.get('pair_emission_probability', .1)))
    eta = g['channel_transmission']
    gate = math.erf(p.get('gate_half_width_ps', 200.) /
                    (math.sqrt(2.) * max(p.get('timing_jitter_ps', 50.), 1e-6)))
    profiles = [terminal_parameters(p, n) for n in endpoints]
    writes = math.prod(q.get('memory_write_efficiency', .7) for q in profiles)
    herald_detector = p.get('herald_detector_efficiency', p.get('detector_efficiency', .8))
    signal_probability = min(1., eta * writes * herald_detector * gate)
    signal = int(rng.binomial(emitted, signal_probability))
    # False ancillary heralds degrade ensemble correlations, never imply that
    # observed clicks carried a quantum state through the classical channel.
    dark_probability = p.get('herald_dark_probability', 1e-9) if g['visible'] else 0.
    dark = int(rng.binomial(max(0, attempts - signal), dark_probability))
    candidates = signal + dark
    phase = math.exp(-p.get('phase_sigma_rad', .02) ** 2 / 2.)
    rotation = g.get('polarization_residual_rad', p.get('polarization_error_rad', .01))
    visibility = (1. - p.get('source_depolarization', .02)) * phase * math.cos(rotation) ** 2
    visibility *= signal / max(1, candidates)
    # All three axes need at least one separately sacrificed pair. Small
    # uncharacterized blocks are explicitly discarded, not granted F=1.
    per_axis = min(p.get('tomography_max_per_axis', 300), candidates // 12)
    correlations = {}
    if per_axis:
        for axis, sign in (('xx', 1), ('yy', -1), ('zz', 1)):
            equal = int(rng.binomial(per_axis, (1. + sign * visibility) / 2.))
            correlations[axis] = {'equal': equal, 'opposite': per_axis - equal}
    consumed = 3 * per_axis
    eligible = candidates - consumed if correlations else 0
    installed = min(q.get('memory_slots', 4) for q in profiles)
    budget = command.arguments.get('capture_mode_budget', installed)
    if type(budget) is not int or not 0 <= budget <= installed:
        raise ProtocolError('invalid_resource_budget', 'capture budget must be an integer within installed memory capacity')
    available = min(eligible, budget)
    rejected = eligible - available
    # Rejected candidate arrivals never become reported captured/stored pairs.
    # Probe branch is destructive and independent of retained-memory modes.
    heralds = consumed + available if correlations else candidates
    return {'orbital_relay_schema': ORBITAL_SCHEMA, 'operation': 'generate',
            'segment_endpoints': list(endpoints), 'start_s': command.emitted_at_s,
            'end_s': command.emitted_at_s + duration, 'housekeeping': housekeeping,
            'ground_stations': grounds, 'tracking': tracking, 'geometry': g,
            'attempts': attempts, 'source_emission_triggers': emitted, 'heralds': heralds,
            'optical_capture_candidates': candidates, 'capture_capacity_rejections': rejected,
            'capture_mode_budget': budget,
            'correlations': correlations, 'memory_writes': available,
            'uncharacterized_pairs_discarded': heralds if not correlations else 0,
            'detector_dark_clicks': dark, 'timing_jitter_ps': p.get('timing_jitter_ps', 50.),
            'gate_efficiency': gate, 'optical_transmission': eta,
            'effective_length_km': g['range_km'], 'quantum_flight_s': g['flight_time_s'],
            'heralding_kind': 'ancillary_capture_herald',
            'model_scope': 'finite Werner calibration ensemble; hypothetical heralded memory capture; no validated hardware'}


def observe_bsm(rng, command, config, environment, parameters):
    """Two consumed memory readouts and an explicit linear-optical Bell outcome.

    Uniform Bell branches, read losses and ancillary detections are separately
    sampled. No output pair, fidelity or Pauli correction is fabricated here.
    """
    p = terminal_parameters(parameters, command.arguments.get('bsm_node', ''))
    reads = [int(rng.random() < p.get('memory_read_efficiency', .7)) for _ in range(2)]
    detections = [int(rng.random() < p.get('detector_efficiency', .8)) * r for r in reads]
    branch = int(rng.integers(0, 4))
    kind = p.get('bsm_kind', 'linear_optics_unassisted')
    ideal = branch in (2, 3) if kind == 'linear_optics_unassisted' else True
    maximum = .5 if kind == 'linear_optics_unassisted' else 1.
    heralded = all(detections) and ideal and rng.random() < p.get('bsm_success', maximum) / maximum
    housekeeping, grounds = _states(config, environment, command.emitted_at_s)
    tracking = {n: {'locked': terminal_parameters(parameters, n).get('pointing_enabled', True)} for n in config.path}
    geometries = [capture_geometry((a, b), housekeeping, grounds, parameters, tracking)
                  for a, b in zip(config.path, config.path[1:])]
    return {'orbital_relay_schema': ORBITAL_SCHEMA, 'operation': 'bsm',
            'parent_ids': list(command.arguments.get('parent_ids', [])),
            'memory_readouts': reads, 'detector_clicks': detections,
            'bsm_success': int(heralded), 'bell_outcome': branch if heralded else None,
            'housekeeping': housekeeping, 'ground_stations': grounds, 'tracking': tracking,
            'geometries': geometries, 'heralding_kind': 'ancillary_bell_detection',
            'bsm_kind': kind, 'start_s': command.emitted_at_s,
            'model_scope': 'two destructive retrievals; unassisted optical BSM; no output state in acquisition'}
