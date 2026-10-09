"""One-time, version-checked international demo migration with full before state.

Institution locations are documented. All ORION equipment and new optical links
are hypothetical. CN hubs represent documented macro routes, with a separately
labelled experimental repeater overlay, not a claim to operate China's network.
"""
from __future__ import annotations

import json
import math
from contextlib import closing
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from ..control_plane.channels import local_pair
from ..control_plane.geodesy import LOCATION_SOURCE, geodesic_km, link_geodesic_km
from ..control_plane.topology import NodeRole, SimulatedLink, SimulatedNodeState, SimulatedTopology
from .contracts import ProtocolConfig

GLOBAL_EVENT='institutional_modular_technologies_v1'
CAPABILITIES=('quantum_memory','bell_measurement','bilateral_cnot','coherent_sender','phase_reference',
              'interferometer','single_photon_detector','optical_ground_terminal','tracking','time_tagger')


def site_catalogue():
    return json.loads((Path(__file__).parent/'data/sites_v1.json').read_text(encoding='utf-8'))


def _node(ident,name,latitude,longitude,*,site=None,kind='hypothetical_component'):
    now=datetime.now(timezone.utc).isoformat()
    record=site or {}
    return SimulatedNodeState(ident,NodeRole.REPEATER,0,'IDLE',telemetry={
        'inventory':{'origin':'brazil-institutions-v1','kind':kind,'scope':'metadata_only_no_connection'},
        'location':{'latitude':latitude,'longitude':longitude,'place':name,'source':LOCATION_SOURCE,'set_at_utc':now},
        'demonstration_site':{'name':name,'address':record.get('address','hypothetical component'),
                              'official_source':record.get('official_source',''),
                              'coordinate_source':record.get('coordinate_source','hypothetical interpolation'),
                              'coordinate_precision':record.get('coordinate_precision','hypothetical component at a reference position'),
                              'infrastructure':record.get('maturity','proposed_simulation_only'),
                              'equipment':'hypothetical; no institution hardware verified','physical_connection_to_orion':False},
        'technology_capabilities':list(CAPABILITIES)+(['satellite_source'] if kind=='satellite' else []),
        'capability_scope':'simulated equipment declarations; not validated institutional hardware'},updated_at_utc=now)


def expand_global(topology):
    # Explicit requested removal, including operator-added incident links; the
    # complete old state is retained by the migration event before committing.
    removed_links=[x.link_id for x in topology.links if 'UNIJORGE' in (x.left_node_id,x.right_node_id)]
    nodes=[x for x in topology.nodes if x.node_id!='UNIJORGE']
    links=[x for x in topology.links if x.link_id not in removed_links]
    occupied={x.node_id for x in nodes}
    for row in site_catalogue()['sites']:
        if row['id'] in occupied:
            raise ValueError(f'new institutional identifier conflicts with existing operator node: {row["id"]}; preserved')
        nodes.append(_node(row['id'],row['name'],row['latitude'],row['longitude'],site=row,kind='institution_reference'))
    base_ids={x.node_id for x in nodes}
    # Enrich only explicit hypothetical capability metadata, preserving memory,
    # location, isolation, calibration and the inventory information received.
    nodes=[replace(x,telemetry={**x.telemetry,'technology_capabilities':list(CAPABILITIES),
              'capability_scope':'proposed simulator interfaces, not verified site hardware'})
           if x.node_id in ('CBPF','CIMATEC','USP','IME-USP','UNICAMP','CIQUANTA') else x for x in nodes]
    configs=[];matrix=[]
    by_id={x.node_id:x for x in nodes}

    def component(ident,a,b,fraction=.5,kind='hypothetical_component'):
        if ident in by_id:raise ValueError(f'reserved component id conflict: {ident}; preserved')
        first,second=by_id[a].telemetry['location'],by_id[b].telemetry['location']
        lat=first['latitude']+(second['latitude']-first['latitude'])*fraction
        lon=first['longitude']+(second['longitude']-first['longitude'])*fraction
        node=_node(ident,f'{ident} · simulated {kind}',lat,lon,kind=kind)
        by_id[ident]=node;nodes.append(node)
        return ident

    def session(ident,path,technologies,why,parameters=None,infrastructure='proposed_simulation'):
        ids=[]
        known={x.link_id for x in links}
        for index,(a,b) in enumerate(zip(path,path[1:]),1):
            if 'tf_qkd' in technologies and index == 2:
                a,b=b,a  # both independent senders send toward the central detector
            elif 'satellite_doppler' in technologies and index == 1:
                a,b=b,a  # the satellite source sends photons to each ground receiver
            link_id=f'TECH-{ident}-{index:02d}'
            if link_id in known:raise ValueError(f'reserved link id conflict: {link_id}; preserved')
            ports=local_pair(link_id,b);ports['quantum']['protocol']='quantum-operation-record-v1'
            length=geodesic_km(by_id[a].telemetry['location']['latitude'],by_id[a].telemetry['location']['longitude'],
                               by_id[b].telemetry['location']['latitude'],by_id[b].telemetry['location']['longitude'])
            links.append(SimulatedLink(link_id,a,b,length,.9,channels=ports,
                technology={'session_id':ident,'technologies':list(technologies),'infrastructure':infrastructure,
                            'distance_scope':'geographic lower bound; effective fibre uses route_factor; orbital range is ephemeris-derived',
                            'physical_connection_to_orion':False}))
            ids.append(link_id)
        config=ProtocolConfig(ident,tuple(technologies),(path[0],path[-1]),tuple(path),tuple(ids),
            parameters={'block_s':.5,'timeout_s':3.,'attempt_rate_hz':250000.,'source_depolarization':.04,
                        'memory_slots':8,'coherence_s':1.,'memory_write_efficiency':.7,'memory_read_efficiency':.7,
                        'detector_efficiency':.8,'attenuation_db_km':.2,'route_factor':1.2,
                        'timing_jitter_ps':50.,'phase_sigma_rad':.05,'classical_jitter_s':1e-5,
                        'bsm_success':.5,'operation_error':.005,'purification_operation_error':.01,
                        'min_fidelity':.7,**(parameters or {})},
            parameter_sources=('briegel1998','bennett1996','sns2018','memory2024','detector2013','micius2017','engineering_assumptions_v1'),
            infrastructure=infrastructure)
        configs.append(config)
        matrix.append({'session_id':ident,'endpoints':list(config.endpoints),'path':path,'link_ids':ids,
                       'technologies':list(technologies),'justification':why,'infrastructure':infrastructure,
                       'geographic_span_km':sum(x.distance_km for x in links if x.link_id in ids)})

    combo=('entanglement_swapping','quantum_memory','purification')
    session('RIO-ENTANGLEMENT',['PUC-RIO','IME-RIO','CBPF','UFRJ'],combo,
        'Metropolitan fibre; explicitly proposed memory/BSM/LOCC devices at named campuses. Preserve old CBPF links.')
    center=component('TF-RIO-CENTER','PUC-RIO','UFRJ',kind='untrusted_tf_measurement_station')
    session('RIO-TF',['PUC-RIO',center,'UFRJ'],('tf_qkd',),
        'Independent phase-randomized senders, central single-photon interference, authenticated classical postprocessing.')
    center=component('TF-ONTARIO-CENTER','IQC','XANADU',kind='untrusted_tf_measurement_station')
    session('ONTARIO-TF',['IQC',center,'XANADU'],('tf_qkd',),
        'Regional ~100 km geographic scale; proposed optical interfaces at QNC and documented Toronto office reference, no operational connection claimed.',{'pulse_rate_hz':10000000.})
    center=component('TF-IBM-IQC-CENTER','IBM-WATSON','IQC',kind='untrusted_tf_measurement_station')
    session('IBM-IQC-TF',['IBM-WATSON',center,'IQC'],('tf_qkd',),
        'Long regional fibre reference, phase stabilization and low-noise detectors essential; finite-size/key feasibility deliberately not guaranteed.',{'pulse_rate_hz':10000000.})
    # IQMP is a developing campus, not an existing network endpoint. Its long
    # US connection is an experimental overlay with represented repeaters.
    path=['IQMP']
    for index in range(1,7):path.append(component(f'R-US-EXPERIMENT-{index:02d}','IQMP','IBM-WATSON',index/7.,'experimental_memory_repeater'))
    path.append('IBM-WATSON')
    session('US-REPEATER-EXPERIMENT',path,combo,
        'Long terrestrial route; experimental memory and purification overlay with six represented intermediate stations; not IQMP deployed capability.')
    # China's paper documents a trusted-relay backbone, not an entanglement
    # repeater backbone. Preserve that topology distinction in every record.
    for ident,a,b in [('CN-BEIJING-JINAN','CN-BEIJING','CN-JINAN'),('CN-JINAN-HEFEI','CN-JINAN','CN-HEFEI'),('CN-HEFEI-SHANGHAI','CN-HEFEI','CN-SHANGHAI')]:
        path=[a]
        for index in range(1,4):path.append(component(f'R-{ident}-{index:02d}',a,b,index/4.,'experimental_repeater_overlay'))
        path.append(b)
        session(ident,path,combo,
            'Documented Beijing–Jinan–Hefei–Shanghai macro backbone uses trusted QKD relays. This explicitly experimental entanglement overlay does not claim its real internal stations or operational security.',
            {'block_s':1.},infrastructure='documented_macro_route_with_proposed_experimental_overlay')
    for ident,a,b in [('RIO-SATELLITE','CBPF','UFRJ'),('EUROPE-SATELLITE','BSC','MS-QUANTUM'),('US-SATELLITE','GOOGLE-QAI','IQMP'),('ATLANTIC-SATELLITE','UFRJ','BSC')]:
        sat=component(f'SAT-{ident}',a,b,0.,'satellite')
        session(ident,[a,sat,b],('satellite_doppler',),
            'Explicit hypothetical LEO satellite and optical ground terminals; pass visibility, atmosphere, pointing and Doppler govern measured coincidences. Intercontinental simultaneous LEO visibility may be impossible and must remain unavailable.',
            {'block_s':2.,'timeout_s':4.,'altitude_km':500.,'inclination_deg':60.,
             'min_elevation_deg':10. if ident=='EUROPE-SATELLITE' else 20.,'wavelength_nm':850.,
             'divergence_urad':10.,'pointing_jitter_urad':2.,'receiver_diameter_m':1.,
             'doppler_compensation':True,'doppler_residual_hz':5e6,'filter_sigma_hz':1e9,
             'pair_source_hz':5000000.,'zenith_optical_depth':.2,'optical_efficiency':.5})
    sessions_by_node={}
    for config in configs:sessions_by_node.setdefault(config.path[0],[]).append(config.to_dict())
    nodes=[replace(x,telemetry={**x.telemetry,'technology_sessions':sessions_by_node[x.node_id]})
           if x.node_id in sessions_by_node else x for x in nodes]
    expanded=replace(topology,nodes=tuple(nodes),links=tuple(links),topology_version=topology.topology_version+1,
                     updated_at_utc=datetime.now(timezone.utc).isoformat())
    return expanded,{'removed_nodes':['UNIJORGE'] if 'UNIJORGE' in {x.node_id for x in topology.nodes} else [],
                     'removed_incident_links':removed_links,'new_sites':14,'matrix':matrix,
                     'scope':'geographic references and experimental/proposed simulation only','physical_actuation':False}


def upgrade_global_workspace(store,topology):
    if not any(x.node_id=='CIMATEC' and x.telemetry.get('inventory',{}).get('origin')=='brazil-institutions-v1' for x in topology.nodes):
        return topology
    with closing(store._connect()) as connection:
        marker=connection.execute('SELECT 1 FROM topology_events WHERE topology_id=? AND event_kind=?',
                                  (topology.topology_id,GLOBAL_EVENT)).fetchone()
    if marker:return topology
    expanded,payload=expand_global(topology)
    # Topology, demand cleanup and marker are atomic; full old demand and pair
    # records are retained in the same audit event, before any row is removed.
    with store._lock,closing(store._connect()) as conn,conn:
        conn.execute('BEGIN IMMEDIATE')
        row=conn.execute('SELECT topology_version FROM simulated_topologies WHERE topology_id=?',(topology.topology_id,)).fetchone()
        if row is None or row['topology_version']!=topology.topology_version:
            raise ValueError('global migration version conflict; retry without losing operator edits')
        has_demands = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='network_demands'").fetchone()
        demands=[dict(row) for row in conn.execute('SELECT * FROM network_demands WHERE topology_id=?',(topology.topology_id,))] if has_demands else []
        payload.update(before=topology.to_dict(),before_demands=demands)
        # Retire the local UNIJORGE request; retain NACIONAL's CBPF destination
        # and make CIMATEC its explicit remaining origin.
        if has_demands:
            conn.execute('DELETE FROM network_demands WHERE topology_id=? AND demand_id!=? AND (a=? OR b=?)',
                         (topology.topology_id,'NACIONAL','UNIJORGE','UNIJORGE'))
            conn.execute('DELETE FROM network_demands WHERE topology_id=? AND ((a=? AND b=?) OR (a=? AND b=?))',
                         (topology.topology_id,'UNIJORGE','CIMATEC','CIMATEC','UNIJORGE'))
            conn.execute('UPDATE network_demands SET a=CASE WHEN a=? THEN ? ELSE a END,b=CASE WHEN b=? THEN ? ELSE b END,pair_json=NULL WHERE topology_id=? AND (a=? OR b=?)',
                         ('UNIJORGE','CIMATEC','UNIJORGE','CIMATEC',topology.topology_id,'UNIJORGE','UNIJORGE'))
        conn.execute('UPDATE simulated_topologies SET topology_version=?,updated_at_utc=?,state_json=? WHERE topology_id=? AND topology_version=?',
                     (expanded.topology_version,expanded.updated_at_utc,json.dumps(expanded.to_dict()),topology.topology_id,topology.topology_version))
        conn.execute('INSERT INTO topology_events(event_id,topology_id,version_before,version_after,event_kind,event_sha256,created_at_utc,event_json) VALUES(?,?,?,?,?,?,?,?)',
                     store._event(expanded,version_before=topology.topology_version,event_kind=GLOBAL_EVENT,payload=payload))
    return expanded
