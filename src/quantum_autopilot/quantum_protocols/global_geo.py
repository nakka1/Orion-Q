"""Hypothetical GEO access/ISL constellation; links require received operations.

Existing LEO/MEO/TLE/measurement records remain unchanged. The new terminals
are demanding engineering assumptions, not equipment at the named institutes.
Measured BBM92 links are not quantum repeaters or trusted-key relays.
"""
from contextlib import closing
from dataclasses import replace
from datetime import datetime, timezone

from ..control_plane.channels import local_pair
from ..control_plane.topology import SimulatedLink
from .contracts import ProtocolConfig
from .orbits import geo_reference_orbit
from .topology import _node

GEO_EVENT = 'global_geo_constellation_v1'
PROFILES = (
    ('GEO-AMERICAS-SIM', -100., (
        ('IBM-WATSON','GOOGLE-QAI'), ('IQMP','IQC'), ('IQC','XANADU'))),
    ('GEO-ATLANTIC-SIM', -20., (
        ('UFRJ','BSC'), ('CIMATEC','CIQUANTA'), ('CBPF','USP'),
        ('IME-USP','UNICAMP'), ('PUC-RIO','IME-RIO'))),
    ('GEO-EUROPE-SIM', 20., (('BSC','MS-QUANTUM'),)),
    ('GEO-ASIA-SIM', 120., (('CN-BEIJING','CN-HEFEI'), ('CN-HEFEI','CN-SHANGHAI'))),
)


def expand_geo_constellation(topology):
    if not any(n.node_id=='CIMATEC' and 'demonstration_site' in n.telemetry for n in topology.nodes):
        return topology, {'added':False,'reason':'not an institutional demonstration'}
    occupied={n.node_id for n in topology.nodes}|{l.link_id for l in topology.links}|{
        r['session_id'] for n in topology.nodes for r in n.telemetry.get('technology_sessions',())}
    def unique(base):
        candidate=base;index=1
        while candidate in occupied:
            index+=1;candidate=f'{base}-{index}'
        occupied.add(candidate);return candidate
    known={n.node_id for n in topology.nodes};nodes=list(topology.nodes);links=list(topology.links)
    satellites=[];orbits={};sessions=[];skipped=[]
    for base,longitude,pairs in PROFILES:
        usable=[pair for pair in pairs if set(pair)<=known]
        if not usable:
            skipped.append({'satellite':base,'reason':'associated reference stations absent'});continue
        satellite=unique(base);satellites.append(satellite)
        stations=list(dict.fromkeys(n for pair in usable for n in pair))
        orbit=geo_reference_orbit(longitude,compatible_stations=stations);orbits[satellite]=orbit
        raw=[]
        for index,endpoints in enumerate(usable,1):
            ident=unique(f'{base}-DOWNLINK-{index:02d}')
            link_ids=[]
            for ground in endpoints:
                link_id=unique(f'TECH-{ident}-{len(link_ids)+1:02d}')
                pair=local_pair(link_id,ground);pair['quantum']['protocol']='quantum-operation-record-v1'
                links.append(SimulatedLink(link_id,satellite,ground,0.,.9,channels=pair,
                    technology={'session_id':ident,'technologies':['satellite_doppler'],
                        'infrastructure':'proposed_simulation_only','physical_connection_to_orion':False,
                        'distance_scope':'no fabricated terrestrial length; received orbital slant range required'}))
                link_ids.append(link_id)
            parameters={**orbit,'orbit_record':orbit,'block_s':2.,'timeout_s':4.,
                'pair_source_hz':5e6,'wavelength_nm':850.,'min_elevation_deg':20.,
                'divergence_urad':2.,'pointing_jitter_urad':.3,'receiver_diameter_m':4.,
                'optical_efficiency':.5,'detector_efficiency':.8,'insertion_loss_db':2.,
                'zenith_optical_depth':.2,'timing_jitter_ps':50.,'doppler_compensation':True,
                'background_cps':1.,'dark_count_cps':5.,
                'global_demand_service':True,'classical_required':True}
            config=ProtocolConfig(ident,('satellite_doppler',),tuple(endpoints),
                (endpoints[0],satellite,endpoints[1]),tuple(link_ids),parameters=parameters,
                parameter_sources=('micius2017','satellite_ogs_v2','engineering_assumptions_v1'),
                infrastructure='proposed_simulation_only')
            raw.append(config.to_dict());sessions.append(config.to_dict())
        node=_node(satellite,f'GEO simulado {longitude:g}° · acesso óptico e ISL hipotéticos',0.,longitude,kind='satellite')
        nodes.append(replace(node,telemetry={**node.telemetry,'technology_sessions':raw,
            'orbit_record':orbit,'orbital_assumptions':{
                'stationary_scope':'explicit simulated geostationary model; not a real satellite orbit',
                'downlink_terminals':'4 m ground receiver; 2 urad divergence; 0.3 urad tracking; hypothetical',
                'background_scope':'night/filtered 1 cps background and 5 cps dark per detector assumptions',
                'no_global_quantum_relay':'BBM92 photon detection consumes pairs; no orbital QND memory/BSM driver',
                'physical_action_performed':False}}))
    # Four explicit associations; no automatic complete graph between satellites.
    # On current positions the short and long links still undergo LOS/APT/budget
    # checks at acquisition and receive. Other candidate links remain absent.
    if len(satellites)==4:
        for index,(a,b) in enumerate(zip(satellites,satellites[1:]+satellites[:1]),1):
            ident=unique(f'GLOBAL-GEO-ISL-{index:02d}');link_id=unique(f'TECH-{ident}-01')
            pair=local_pair(link_id,b);pair['quantum']['protocol']='quantum-operation-record-v1'
            terminal={'pair_source_hz':20000.,'wavelength_nm':850.,'transmitter_diameter_m':2.,
                'receiver_diameter_m':4.,'divergence_urad':.4,'pointing_jitter_urad':.05,
                'terminal_fov_urad':20.,'max_range_km':90000.,'earth_limb_margin_km':80.,
                'tracking_latency_s':.001,'pointing_enabled':True,'optical_efficiency':.5,
                'insertion_loss_db':2.,'detector_efficiency':.8,'local_detection_efficiency':.8,
                'timing_jitter_ps':50.,'background_cps':1.,'dark_count_cps':5.,
                'detector_dead_time_ns':50.,'propagation_compensation':True}
            config=ProtocolConfig(ident,('satellite_isl',),(a,b),(a,b),(link_id,),
                parameters={**terminal,'block_s':2.,'timeout_s':4.,'global_demand_service':True,
                    'satellite_orbits':{a:orbits[a],b:orbits[b]},
                    'device_parameters':{a:terminal,b:terminal}},
                parameter_sources=('engineering_assumptions_v1',),infrastructure='proposed_simulation_only')
            links.append(SimulatedLink(link_id,a,b,0.,.9,channels=pair,
                technology={'session_id':ident,'technologies':['satellite_isl'],
                    'infrastructure':'proposed_simulation_only','physical_connection_to_orion':False,
                    'distance_scope':'received normalized ECEF vectors; Earth obstruction and optical budget checked'}))
            nodes=[replace(n,telemetry={**n.telemetry,'technology_sessions':[
                *n.telemetry.get('technology_sessions',()),config.to_dict()]}) if n.node_id==a else n for n in nodes]
            sessions.append(config.to_dict())
    return replace(topology,nodes=tuple(nodes),links=tuple(links)),{
        'added':bool(satellites),'satellites':satellites,'sessions':[s['session_id'] for s in sessions],
        'skipped':skipped,'scope':'hypothetical GEO downlink and dedicated ISL; no quantum relay claim',
        'physical_action_performed':False}


def upgrade_geo_constellation_workspace(store,topology):
    with closing(store._connect()) as conn:
        done=conn.execute('SELECT 1 FROM topology_events WHERE topology_id=? AND event_kind=?',
                          (topology.topology_id,GEO_EVENT)).fetchone()
    if done:return topology
    expanded,payload=expand_geo_constellation(topology)
    if not payload['added']:return topology
    expanded=replace(expanded,topology_version=topology.topology_version+1,
        updated_at_utc=datetime.now(timezone.utc).isoformat())
    return store._replace_existing(expanded,expected_version=topology.topology_version,
        event_kind=GEO_EVENT,payload={**payload,'before':topology.to_dict()})
