"""Additive hypothetical GEO memory-repeater service at verified site references.

Existing missions, BBM92 receivers, operator records and session banks remain
unchanged. Four additional devices have exclusive memory/optical resources.
Co-location does not imply an installed optical path to an institution's core.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone

from ..control_plane.channels import local_pair
from ..control_plane.geodesy import node_location
from ..control_plane.topology import NodeRole, SimulatedLink, SimulatedNodeState
from .contracts import ProtocolConfig
from .orbital_relay_simulation import ENCODING, capture_geometry
from .orbits import geo_reference_orbit, position

ORBITAL_RELAY_EVENT = 'coherent_orbital_memory_relay_v1'
SESSION_ID = 'orbital-memory-cbpf-bsc-v1'
PATH = ('ORBITAL-OGS-CBPF', 'ORBITAL-GEO-W', 'ORBITAL-GEO-E', 'ORBITAL-OGS-BSC')
LINK_IDS = tuple(f'ORBITAL-MEMORY-{i:02d}' for i in range(1, 4))
TECHNOLOGIES = ('orbital_relay', 'entanglement_swapping', 'quantum_memory')
SOURCES = (
    {'id': 'wallnoefer2022', 'url': 'https://arxiv.org/abs/2110.15806v2',
     'scope': 'satellite quantum repeaters; diffraction, pointing and memory tradeoffs; no reproduction of entire author simulation'},
    {'id': 'briegel1998', 'url': 'https://doi.org/10.1103/PhysRevLett.81.5932',
     'scope': 'heralded segmented entanglement, memories and classical signaling'},
    {'id': 'calsamiglia2001', 'url': 'https://doi.org/10.1007/s003400000484',
     'scope': '50 percent unassisted linear-optical Bell discrimination limit'},
)


def relay_parameters():
    """An openly hypothetical feasible design point, not universal capabilities."""
    return {
        'block_s': .5, 'timeout_s': 4., 'max_attempts': 2, 'clock_rate': 1.,
        'attempt_rate_hz': 200000., 'pair_emission_probability': .1,
        'memory_slots': 16, 'coherence_s': 30., 'memory_cutoff_s': 10.,
        'memory_write_efficiency': .8, 'memory_read_efficiency': .8,
        'memory_heralding_kind': 'ancillary_capture_herald',
        'bsm_kind': 'linear_optics_unassisted', 'bsm_success': .5,
        'operation_error': .005, 'operation_duration_s': .002,
        'min_fidelity': .65, 'source_depolarization': .015,
        'detector_efficiency': .8, 'herald_detector_efficiency': .8,
        'herald_dark_probability': 1e-9, 'timing_jitter_ps': 50.,
        'gate_half_width_ps': 200., 'sync_tolerance_s': 1e-6,
        'phase_sigma_rad': .02, 'polarization_error_rad': .01,
        'tomography_max_per_axis': 300, 'classical_latency_s': .002,
        'classical_jitter_s': 1e-5, 'route_factor': 1.,
        'wavelength_nm': 850., 'quantum_encoding': ENCODING,
        'optical_duplex': 'dedicated_quantum_and_classical',
        'transmitter_diameter_m': 2., 'receiver_diameter_m': 4.,
        'divergence_urad': .4, 'pointing_jitter_urad': .05,
        'pointing_jitter_convention': 'gaussian_per_axis',
        'pointing_enabled': True, 'tracking_latency_s': 0.,
        'terminal_fov_urad': 20., 'point_ahead_compensation': True,
        'point_ahead_calibration_error_urad': .01,
        'doppler_compensation': True, 'doppler_residual_hz': 0.,
        'filter_sigma_hz': 1e9, 'optical_efficiency': .8,
        'insertion_loss_db': 1., 'extra_loss_db': 0.,
        'min_elevation_deg': 10., 'zenith_optical_depth': .15,
        'receiver_coupling': 'bucket', 'earth_limb_margin_km': 80.,
        'resource_bank_id': f'sim-resource:{SESSION_ID}',
        'resource_bank_scope': 'dedicated_per_session',
        'served_institution_endpoints': ['CBPF', 'BSC'],
        'satellite_orbits': {
            PATH[1]: geo_reference_orbit(-60., compatible_stations=(PATH[0],)),
            PATH[2]: geo_reference_orbit(0., compatible_stations=(PATH[3],)),
        },
        'device_parameters': {
            PATH[0]: {'receiver_diameter_m': 6.},
            PATH[3]: {'receiver_diameter_m': 6.},
        },
        'model_assumptions': [
            'Additional hypothetical 2 m space transmitters, 4 m space receivers and 6 m OGS; no mission or installed hardware validated.',
            'Ancillary heralded 850 nm photon-memory capture and retained-half entangled sources; BBM92/TDC detections are a separate destructive capability.',
            'Thirty-second Werner-visibility coherence, sixteen dedicated modes, 0.8 write/read; combined efficiency/coherence is an assumed design point, not a demonstrated device.',
            'A dedicated pair source per emitting span and one optical terminal per incident span; independent multiplexed attempt batches; sampled XX/YY/ZZ probes sacrificed; occupied modes admit at most configured capacity.',
            'Ideal GEO at -60/0 degrees; exact common-epoch ECEF LOS and configured limb margin; no mission attitude, Sun exclusion, retarded trajectory or stationkeeping qualification.',
            'Common-epoch batch optical budget; a quantum batch crossing a declared communication-window edge is discarded conservatively, not a per-emission retarded-trajectory model.',
            'Clear atmosphere and per-axis Gaussian pointing jitter; finite aperture/diffraction/Doppler budget; adverse scenarios reduce yields.',
            'Classical ACK and logical Pauli frame required before output memory resource; no physical Pauli actuation or secret key.'
        ],
    }


def expand_orbital_relay(topology):
    """Return (topology, auditable matrix) without replacing existing records."""
    known = {n.node_id for n in topology.nodes}
    if known & set(PATH) or {link.link_id for link in topology.links} & set(LINK_IDS):
        raise ValueError('orbital relay reserved identifiers conflict; existing topology preserved')
    references = {PATH[0]: topology.node('CBPF'), PATH[3]: topology.node('BSC')}
    now = datetime.now(timezone.utc).isoformat()
    p = relay_parameters()
    cfg = ProtocolConfig(SESSION_ID, TECHNOLOGIES, (PATH[0], PATH[-1]), PATH, LINK_IDS,
                         parameters=p, seed=1001, parameter_sources=tuple(s['id'] for s in SOURCES),
                         infrastructure='hypothetical_additional_orbital_memory_equipment')
    nodes = list(topology.nodes)
    grounds = {}
    for ident in PATH:
        satellite = ident in p['satellite_orbits']
        capabilities = ['quantum_memory', 'heralded_photon_capture', 'tracking',
                        'optical_space_terminal' if satellite else 'optical_ground_terminal',
                        'classical_forward', 'classical_telemetry']
        if satellite:
            capabilities += ['satellite_source', 'bell_measurement', 'single_photon_detector']
        else:
            capabilities += ['endpoint_send', 'endpoint_receive']
        incident_links = [link for link, a, b in zip(LINK_IDS, PATH, PATH[1:]) if ident in (a, b)]
        emitting_links = [link for link, a, b in zip(LINK_IDS, PATH, PATH[1:])
                          if ident == (a if a in p['satellite_orbits'] else b)]
        bank = {'resource_bank_id': p['resource_bank_id'], 'session_id': SESSION_ID,
                'memory_modes': p['memory_slots'], 'exclusive_device': True,
                'quantum_link_ids': incident_links, 'emission_link_ids': emitting_links,
                'pair_sources': len(emitting_links), 'pulse_sources': 0,
                'optical_terminals': len(incident_links),
                'source_capacity_scope': 'one dedicated hypothetical source per emitting span at configured attempt_rate_hz',
                'equipment_scope': 'additional hypothetical exclusive terminal/memory hardware',
                'physical_hardware_validated': False}
        telemetry = {
            'inventory': {'origin': ORBITAL_RELAY_EVENT,
                          'kind': 'satellite' if satellite else 'ground_station',
                          'scope': 'hypothetical_dedicated_equipment'},
            'technology_capabilities': capabilities,
            'operational_capabilities': {'originate_receive': not satellite,
                                        'classical_forwarding': True},
            'simulation_resource_banks': [bank],
            'capability_scope': 'simulated additional equipment; no physical integration',
            'technology_sessions': [cfg.to_dict()] if ident == PATH[0] else [],
            'demonstration_site': {'name': ident, 'infrastructure': 'proposed_simulation_only',
                                   'equipment': 'additional hypothetical memory optical terminal',
                                   'physical_connection_to_orion': False},
        }
        if satellite:
            telemetry['orbit_record'] = p['satellite_orbits'][ident]
        else:
            ref = references[ident]
            loc = node_location(ref)
            if loc is None:
                raise ValueError(f'{ref.node_id}: verified reference location required for co-located OGS')
            grounds[ident] = loc
            telemetry['location'] = dict(loc)
            telemetry['serves_institution'] = ref.node_id
            telemetry['demonstration_site'] = {**ref.telemetry.get('demonstration_site', {}),
                **telemetry['demonstration_site'], 'name': f'{ref.node_id} · hypothetical memory OGS',
                'co_location_scope': 'institutional coordinate reference; OGS placement/equipment unverified'}
        nodes.append(SimulatedNodeState(ident, NodeRole.REPEATER if satellite else NodeRole.ENDPOINT,
                                       0, 'IDLE', telemetry=telemetry, updated_at_utc=now))
    hk = {n: {**position(o, 0.), 'at_s': 0.} for n, o in p['satellite_orbits'].items()}
    links = list(topology.links)
    matrix = []
    for ident, a, b in zip(LINK_IDS, PATH, PATH[1:]):
        geometry = capture_geometry((a, b), hk, grounds, p,
                                    {n: {'locked': True} for n in PATH})
        sender, receiver = geometry['photon_sender'], geometry['photon_receiver']
        ports = local_pair(ident, receiver)
        ports['quantum']['protocol'] = 'quantum-operation-record-v1'
        ports['classical']['max_age_s'] = 4.
        row = {'link_id': ident, 'endpoints': [sender, receiver],
               'logical_path_endpoints': [a, b], 'technologies': list(TECHNOLOGIES),
               'distance_scope': 'ephemeris-derived slant range; not a geographic fibre length',
               'reference_range_km': geometry['range_km'], 'reference_visible': geometry['visible'],
               'reference_transmission': geometry['channel_transmission'],
               'infrastructure': 'hypothetical_additional_orbital_memory_equipment',
               'physical_connection_to_orion': False,
               'justification': 'heralded capture preserves unmeasured pairs; memories bridge classical delays; BSM consumes two resources and ACK establishes logical frame'}
        links.append(SimulatedLink(ident, sender, receiver, geometry['range_km'], .9, channels=ports,
                                  technology={**row, 'session_id': SESSION_ID, 'orbital_relay': True}))
        matrix.append(row)
    return replace(topology, topology_version=topology.topology_version + 1,
                   nodes=tuple(nodes), links=tuple(links), updated_at_utc=now), {
        'schema': 'orion.orbital-relay-migration.v1', 'event': ORBITAL_RELAY_EVENT,
        'added_nodes': list(PATH), 'added_links': list(LINK_IDS),
        'sessions': [cfg.to_dict()], 'connection_matrix': matrix, 'sources': list(SOURCES),
        'demands': [{'demand_id': 'orbital-memory-cbpf-bsc-demand', 'operation': 'entanglement',
                     'endpoints': list(cfg.endpoints), 'served_institutions': ['CBPF', 'BSC'],
                     'session_id': SESSION_ID, 'physical_action_performed': False}],
        'preserved_original_records': True, 'physical_adapter_validated': False,
        'certified_secret_bits': 0,
        'limitations': ['No physical orbital memory/capture adapter.',
                        'Werner ensemble model, not general states or hardware verification.',
                        'Hypothetical GEO payload, memory and telescope design point; no operational mission claim.'],
    }
