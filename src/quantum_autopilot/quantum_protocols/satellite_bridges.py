"""Additive demo bridge; received OGS operations remain the source of results.

The old Atlantic LEO case remains as a physically limited comparison. The new
MEO source and optical terminals are explicit engineering hypotheses, not a
deployed mission, a trusted-key relay or an operational institutional link.
"""
from contextlib import closing
from dataclasses import replace
from datetime import datetime, timezone
import math

from ..control_plane.channels import local_pair
from ..control_plane.geodesy import geodesic_km
from ..control_plane.topology import SimulatedLink
from .contracts import ProtocolConfig
from .topology import _node

BRIDGE_EVENT = 'atlantic_meo_bridge_v1'


def add_atlantic_bridge(topology):
    by_id = {n.node_id: n for n in topology.nodes}
    if not {'UFRJ', 'BSC'} <= by_id.keys():
        return topology, {'added': False, 'reason': 'required reference sites absent'}
    occupied = set(by_id) | {l.link_id for l in topology.links} | {
        c['session_id'] for n in topology.nodes for c in n.telemetry.get('technology_sessions', ())}
    suffix = ''; index = 1
    while any(name+suffix in occupied for name in
              ('SAT-ATLANTIC-MEO', 'ATLANTIC-MEO-BRIDGE', 'TECH-ATLANTIC-MEO-01', 'TECH-ATLANTIC-MEO-02')):
        index += 1; suffix = '-'+str(index)
    satellite = 'SAT-ATLANTIC-MEO'+suffix; session_id = 'ATLANTIC-MEO-BRIDGE'+suffix
    endpoints = ('UFRJ', 'BSC'); positions = [by_id[n].telemetry['location'] for n in endpoints]
    vectors = [(math.cos(math.radians(p['latitude']))*math.cos(math.radians(p['longitude'])),
                math.cos(math.radians(p['latitude']))*math.sin(math.radians(p['longitude'])),
                math.sin(math.radians(p['latitude']))) for p in positions]
    x,y,z = (sum(v[i] for v in vectors) for i in range(3))
    lat = math.degrees(math.atan2(z, math.hypot(x,y))); lon = math.degrees(math.atan2(y,x))
    params = {'block_s':2., 'timeout_s':4., 'altitude_km':10000., 'orbital_class':'MEO',
              'inclination_deg':60., 'reference_latitude':lat, 'reference_longitude':lon,
              'source':'hypothetical Atlantic MEO reference; not a mission ephemeris',
              'wavelength_nm':850., 'min_elevation_deg':20., 'pair_source_hz':5e6,
              'divergence_urad':2., 'pointing_jitter_urad':.3, 'receiver_diameter_m':4., 'detector_efficiency':.8,
              'optical_efficiency':.5, 'insertion_loss_db':2., 'zenith_optical_depth':.2,
              'doppler_compensation':True, 'timing_jitter_ps':50., 'classical_required':True}
    node = _node(satellite, 'Ponte Atlântica MEO · fonte de pares hipotética', lat, lon, kind='satellite')
    links = []
    for i,ground in enumerate(endpoints,1):
        ident = f'TECH-ATLANTIC-MEO-{i:02d}'+suffix
        ports = local_pair(ident, ground); ports['quantum']['protocol'] = 'quantum-operation-record-v1'
        loc = by_id[ground].telemetry['location']
        links.append(SimulatedLink(ident,satellite,ground,
            geodesic_km(lat,lon,loc['latitude'],loc['longitude']),.9,channels=ports,
            technology={'session_id':session_id,'technologies':['satellite_doppler'],
                        'infrastructure':'proposed_simulation_only', 'physical_connection_to_orion':False,
                        'distance_scope':'orbital range derived from received/validated ephemeris; configured distance is geographic only'}))
    config = ProtocolConfig(session_id, ('satellite_doppler',), endpoints,
        (endpoints[0],satellite,endpoints[1]), tuple(l.link_id for l in links), parameters=params,
        parameter_sources=('micius2017','satellite_ogs_v2','engineering_assumptions_v1'),
        infrastructure='proposed_simulation_only')
    node = replace(node,telemetry={**node.telemetry, 'technology_sessions':[config.to_dict()],
        'bridge_assumptions':{'architecture':'BBM92 dual downlink; simultaneous reception required',
                             'scope':'hypothetical MEO orbit, 4 m receivers, 2 urad divergence and 0.3 urad tracking; not measured equipment',
                             'networks':list(endpoints),'certified_secret_bits':0}})
    return replace(topology,nodes=topology.nodes+(node,),links=topology.links+tuple(links)), {
        'added':True, 'satellite':satellite, 'session_id':session_id, 'config':config.to_dict(),
        'retained_leo_comparison':'ATLANTIC-SATELLITE', 'physical_action_performed':False}


def upgrade_satellite_bridge_workspace(store, topology):
    with closing(store._connect()) as connection:
        done = connection.execute('SELECT 1 FROM topology_events WHERE topology_id=? AND event_kind=?',
                                  (topology.topology_id,BRIDGE_EVENT)).fetchone()
    if done: return topology
    expanded,payload = add_atlantic_bridge(topology)
    if not payload['added']: return topology
    expanded = replace(expanded,topology_version=topology.topology_version+1,
                       updated_at_utc=datetime.now(timezone.utc).isoformat())
    return store._replace_existing(expanded,expected_version=topology.topology_version,
        event_kind=BRIDGE_EVENT,payload={**payload,'before':topology.to_dict()})
