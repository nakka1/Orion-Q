"""Additive, once-only demonstration upgrade; never replace operator records."""
from contextlib import closing
from dataclasses import replace
from datetime import datetime, timezone
import math

from ..control_plane.channels import local_pair
from ..control_plane.communicators import seed_record
from ..control_plane.geodesy import geodesic_km
from ..control_plane.topology import SimulatedLink
from .contracts import ProtocolConfig
from .topology import _node

UPGRADE_EVENT = 'hybrid_finite_sns_orbital_memory_v1'


def expand_finite_demo(topology):
    ids={n.node_id for n in topology.nodes}
    session_id='RIO-SNS-FINITE'
    if not {'PUC-RIO','UFRJ'}<=ids:
        return topology, {'added':False,'reason':'Rio reference endpoints absent'}
    if any(r.get('session_id')==session_id for n in topology.nodes for r in n.telemetry.get('technology_sessions',())):
        return topology, {'added':False,'reason':'session already present'}
    endpoints=[topology.node(x) for x in ('PUC-RIO','UFRJ')]
    if any(n.telemetry.get('communicator',{}).get('revision',0)>0 or
           n.telemetry.get('communicator',{}).get('integration',{}).get('mode','simulated')!='simulated' for n in endpoints):
        return topology, {'added':False,'reason':'operator or external source configuration preserved; opt-in enrollment required'}
    a,b=[n.telemetry['location'] for n in endpoints]
    ident='TF-RIO-FINITE-CENTER'
    if ident in ids:
        return topology, {'added':False,'reason':'component identifier occupied; not overwritten'}
    center=_node(ident,'Estação SNS finita · terminal hipotético dedicado',
        (a['latitude']+b['latitude'])/2,(a['longitude']+b['longitude'])/2,kind='untrusted_tf_measurement_station')
    links=[]
    for index,endpoint in enumerate(endpoints,1):
        link_id=f'TECH-{session_id}-{index:02d}'
        if any(link.link_id==link_id for link in topology.links):
            return topology, {'added':False,'reason':'link identifier occupied; not overwritten'}
        channels=local_pair(link_id,ident)
        channels['quantum']['protocol']='quantum-operation-record-v1'
        point=endpoint.telemetry['location']
        where=center.telemetry['location']
        length=geodesic_km(point['latitude'],point['longitude'],where['latitude'],where['longitude'])
        links.append(SimulatedLink(link_id,endpoint.node_id,ident,length,.9,channels=channels,
            technology={'session_id':session_id,'technologies':['tf_qkd'],
                'infrastructure':'dedicated_hypothetical_finite_sns','physical_connection_to_orion':False}))
    p={'tf_estimation_mode':'finite_sns_v2','signal_mu':.15,'decoy_mu':.03,
        'decoy_strong_mu':.15,'send_probability':.005,'pulse_rate_hz':2e7,
        'tf_z_probability':.5,'tf_vacuum_probability':.3,'tf_weak_probability':.4,
        'tf_phase_slice_rad':math.pi/4,'tf_misalignment_error':.005,
        'phase_sigma_rad':.01,'polarization_error_rad':.01,'dark_probability':1e-10,
        'block_s':1.,'timeout_s':120.,'tf_max_sifted_bits':50000,
        'global_demand_service':True,'resource_bank_id':f'sim-resource:{session_id}',
        'resource_bank_scope':'dedicated_per_session','classical_required':True,
        'assumption_scope':'hypothetical symmetric SNS source and receiver; finite numerical model, no certified keys'}
    config=ProtocolConfig(session_id,('tf_qkd',),('PUC-RIO','UFRJ'),('PUC-RIO',ident,'UFRJ'),
        tuple(link.link_id for link in links),p,parameter_sources=('sns2018','sns_finite2019','engineering_assumptions_v1'))
    nodes=list(topology.nodes)+[center]
    nodes=[replace(n,telemetry={**n.telemetry,'technology_sessions':[
        *n.telemetry.get('technology_sessions',()),config.to_dict()]}) if n.node_id=='PUC-RIO' else n for n in nodes]
    expanded=replace(topology,nodes=tuple(nodes),links=tuple(topology.links)+tuple(links))
    # Device settings are specific to this bank, so an older parallel SNS
    # session keeps its own intensities and source cadence.
    banks={}
    for node_id in config.path:
        banks[node_id]={'resource_bank_id':p['resource_bank_id'],'session_id':session_id,
            'node_id':node_id,'resource_bank_scope':'dedicated_per_session','technologies':['tf_qkd'],
            'memory_modes':0,'pair_sources':0,'pulse_sources':int(node_id!=ident),
            'optical_terminals':2 if node_id==ident else 1,'physical_capacity_validated':False,
            'shared_hardware_arbitration_implemented':False}
    new=[]
    for n in expanded.nodes:
        if n.node_id not in config.path:
            new.append(n)
            continue
        telemetry={**n.telemetry,'simulation_resource_banks':[
            *n.telemetry.get('simulation_resource_banks',()),banks[n.node_id]]}
        if n.node_id==ident:
            telemetry['communicator']=seed_record(n,expanded,'simulated').to_dict()
        new.append(replace(n,telemetry=telemetry))
    return replace(expanded,nodes=tuple(new)), {'added':True,'session_id':session_id,
        'scope':p['assumption_scope'],'preserved_parallel_sessions':True}


def upgrade_hybrid_workspace(store,topology):
    if not any(n.node_id=='CIMATEC' and 'demonstration_site' in n.telemetry for n in topology.nodes):
        return topology
    with closing(store._connect()) as conn:
        done=conn.execute('SELECT 1 FROM topology_events WHERE topology_id=? AND event_kind=?',
            (topology.topology_id,UPGRADE_EVENT)).fetchone()
    if done:
        return topology
    from .orbital_relay_topology import LINK_IDS, PATH, expand_orbital_relay
    expanded,finite=expand_finite_demo(topology)
    existing_nodes={node.node_id for node in expanded.nodes}
    existing_links={link.link_id for link in expanded.links}
    if existing_nodes&set(PATH) or existing_links&set(LINK_IDS):
        orbital={'added':False,'reason':'reserved identifiers occupied; operator records preserved; explicit enrollment required'}
    elif not {'CBPF','BSC'}<=existing_nodes:
        orbital={'added':False,'reason':'institutional reference removed; not recreated'}
    else:
        expanded,orbital=expand_orbital_relay(expanded)
    # Keep the inventory's aggregate consistent with the new dedicated banks.
    # Only changed/new nodes are touched; operator records stay intact.
    before_banks={n.node_id:n.telemetry.get('simulation_resource_banks',()) for n in topology.nodes}
    nodes=[]
    for node in expanded.nodes:
        banks=node.telemetry.get('simulation_resource_banks',())
        if banks and banks!=before_banks.get(node.node_id,()):
            totals={'resource_bank_scope':'dedicated_per_session','resource_bank_count':len(banks),
                'session_ids':[bank['session_id'] for bank in banks],
                **{key:sum(bank.get(key,0) for bank in banks) for key in ('memory_modes','pair_sources','pulse_sources','optical_terminals')},
                'sources':sum(bank.get('pair_sources',0)+bank.get('pulse_sources',0) for bank in banks),
                'capacity_scope':'sum of dedicated hypothetical banks, not installed or shared hardware',
                'physical_capacity_validated':False,'shared_hardware_arbitration_implemented':False}
            node=replace(node,telemetry={**node.telemetry,'simulation_resource_totals':totals})
        nodes.append(node)
    expanded=replace(expanded,nodes=tuple(nodes))
    expanded=replace(expanded,topology_version=topology.topology_version+1,
        updated_at_utc=datetime.now(timezone.utc).isoformat())
    return store._replace_existing(expanded,expected_version=topology.topology_version,
        event_kind=UPGRADE_EVENT,payload={'before':topology.to_dict(),'finite_sns':finite,'orbital_relay':orbital,
            'physical_action_performed':False})
