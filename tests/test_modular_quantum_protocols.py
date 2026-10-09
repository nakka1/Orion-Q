"""Physics, causal receipts, replaceable acquisition and migration acceptance."""
import json
from dataclasses import replace
from pathlib import Path

import pytest

from quantum_autopilot.control_plane.topology import SimulatedTopologyStore
from quantum_autopilot.network_engine.institutional import brazil_institutions, ensure_institutional_workspace
from quantum_autopilot.quantum_protocols.contracts import Acquisition, ProtocolError, digest
from quantum_autopilot.quantum_protocols.physics import aged_fidelity, bbpssw, satellite_optics, swap_fidelity, transmission
from quantum_autopilot.quantum_protocols.registry import default_registry
from quantum_autopilot.quantum_protocols.runtime import ProtocolRuntime
from quantum_autopilot.quantum_protocols.sources import MeasurementFileSource, NoAcquisitionSource, SimulatedAcquisitionSource
from quantum_autopilot.quantum_protocols.topology import expand_global, upgrade_global_workspace

pytestmark = pytest.mark.unit


@pytest.fixture(scope='module')
def expanded():
    return expand_global(brazil_institutions())[0]


def only(topology, *ids):
    nodes = tuple(replace(n,telemetry={**n.telemetry,'technology_sessions':
                  [c for c in n.telemetry.get('technology_sessions',()) if c['session_id'] in ids]}) for n in topology.nodes)
    return replace(topology,nodes=nodes)


def run(tmp_path, topology, seconds=12, source=None, name='protocol'):
    r=ProtocolRuntime(tmp_path/f'{name}.sqlite',topology.topology_id,source=source or SimulatedAcquisitionSource())
    for tick in range(1,seconds+1): r.advance(topology,tick)
    return r


def events(runtime, tmp_path, name='events'):
    path=tmp_path/f'{name}.jsonl'; result=runtime.export_events(path)
    assert result['physical_action_performed'] is False
    return [json.loads(line) for line in path.read_text().splitlines()]


def test_reference_physics_and_pair_costs():
    probability, fidelity=bbpssw(.8,.8,0.)
    assert probability == pytest.approx(.7688888888888888)
    assert fidelity == pytest.approx(.838150289017341)
    assert swap_fidelity(1.,1.,0.) == 1.
    assert aged_fidelity(1.,1.,1.) < .53
    assert transmission(100.,.2,0.) == pytest.approx(.01)


def test_bell_frames_compose_and_purification_compares_correct_parity():
    from quantum_autopilot.quantum_protocols.protocols import SwappingAdapter, PurificationAdapter
    context={'operation':'bsm','first_fidelity':.9,'second_fidelity':.9,'operation_error':0.,
             'first_frame':(1,0),'second_frame':(0,0)}
    swapped=SwappingAdapter().process({'bsm_success':1,'bell_outcome':2},context)
    assert swapped['pauli_frame_bits']==(0,0)
    assert not SwappingAdapter().process({'bsm_success':0,'bell_outcome':None},context)['success']
    purified=PurificationAdapter().process({'alice_outcome':0,'bob_outcome':1},context)
    assert purified['accepted'] and purified['pauli_frame_bits']==(1,0)
    with pytest.raises(ProtocolError):SwappingAdapter().process({'bsm_success':1,'bell_outcome':0},context)


def test_verified_topology_and_explicit_experimental_china(expanded):
    assert len(expanded.nodes)==66 and len(expanded.links)==71
    ids={n.node_id for n in expanded.nodes}
    assert 'UNIJORGE' not in ids and {'IME-RIO','IME-USP','CBPF','PUC-RIO','UFRJ','IQMP','XANADU'} <= ids
    assert {'CN-BEIJING','CN-JINAN','CN-HEFEI','CN-SHANGHAI'} <= ids
    for link in expanded.links:
        assert link.left_node_id in ids and link.right_node_id in ids
        if link.technology:
            assert link.channels['quantum']['protocol']=='quantum-operation-record-v1'
            assert link.technology['physical_connection_to_orion'] is False
    baseline=brazil_institutions()
    kept={l.link_id:l.to_dict() for l in baseline.links if 'UNIJORGE' not in (l.left_node_id,l.right_node_id)}
    assert all(l.to_dict()==kept[l.link_id] for l in expanded.links if l.link_id in kept)


def test_sparse_generation_does_not_invent_calibration(tmp_path,expanded):
    r=run(tmp_path,only(expanded,'US-REPEATER-EXPERIMENT'),3)
    assert not [e for e in events(r,tmp_path) if e['event']=='processing_error']
    assert r.snapshot()['sessions']['US-REPEATER-EXPERIMENT']['counters'].get('delivered_pairs',0)==0


def test_all_five_have_operational_events_and_causal_receipts(tmp_path,expanded):
    r=run(tmp_path,only(expanded,'RIO-ENTANGLEMENT','RIO-TF','RIO-SATELLITE'),100)
    rows=events(r,tmp_path); kinds={e['event'] for e in rows}
    assert {'resource_stored','resources_reserved_and_consumed','memory_expired','end_to_end_delivered'} <= kinds
    processors={e['detail']['technology'] for e in rows if e['event']=='protocol_processed'}
    assert {'quantum_memory','purification','entanglement_swapping','tf_qkd','satellite_doppler'} <= processors
    messages={}
    previous=''
    for e in rows:
        body={k:v for k,v in e.items() if k!='receipt_sha256'}
        assert digest(body)==e['receipt_sha256'] and e['previous_sha256']==previous
        previous=e['receipt_sha256']
        assert e['origin']=='simulated' and not e['physical_actuation']
        key=e['command_id']
        if key: messages.setdefault(key,[]).append(e)
    for records in messages.values():
        names=[e['event'] for e in records]
        if 'metrics_computed' not in names: continue
        assert names.index('acquired') < names.index('validated') < names.index('synchronized')
        assert names.index('protocol_processed') < names.index('classical_transmitted') < names.index('classical_received') < names.index('metrics_computed')
        sent=next(e for e in records if e['event']=='classical_transmitted')
        received=next(e for e in records if e['event']=='classical_received')
        assert sent['detail']['associated_quantum_ids'] and sent['detail']['classical_channel_ids']
        assert received['at_s']>=sent['detail']['arrival_at_s']>sent['at_s']
        if 'quantum_transmission_observed' in names:
            observation=next(e for e in records if e['event']=='quantum_transmission_observed')
            processed=next(e for e in records if e['event']=='protocol_processed')
            expected=1 if processed['detail']['operation']=='generate' else 2
            assert len(observation['detail']['quantum_channel_ids'])==expected
    s=r.snapshot()['sessions']
    assert s['RIO-ENTANGLEMENT']['counters']['delivered_pairs']>0
    assert s['RIO-ENTANGLEMENT']['metrics']['purification_yield']<=.5
    assert s['RIO-TF']['metrics']['certified_secret_bits']==0
    assert s['RIO-TF']['metrics']['conditional_key_estimate_bits']>0


@pytest.mark.parametrize('session_id',['RIO-TF','RIO-ENTANGLEMENT'])
def test_identical_pipeline_for_file_and_simulator(tmp_path,expanded,session_id):
    topology=only(expanded,session_id); simulated=SimulatedAcquisitionSource()
    first=run(tmp_path,topology,12,simulated,'sim')
    path=tmp_path/'input.jsonl'
    records=[{**r,'origin':'measurement_file','source_id':'measurement_file'} for r in simulated.records]
    path.write_text('\n'.join(json.dumps(r) for r in records)+'\n')
    second=run(tmp_path,topology,12,MeasurementFileSource(path),'file')
    a=first.snapshot()['sessions'][session_id];b=second.snapshot()['sessions'][session_id]
    def origins(value):
        if isinstance(value,dict):return {k:origins(v) for k,v in value.items() if k!='origin'}
        if isinstance(value,list):return [origins(v) for v in value]
        return value
    assert origins(a['metrics'])==origins(b['metrics']) and a['counters']==b['counters']
    assert second.snapshot()['origin']=='measurement_file'
    assert [e['event'] for e in events(first,tmp_path,'sim-events')]==[e['event'] for e in events(second,tmp_path,'file-events')]


@pytest.mark.parametrize('channel', ['quantum','classical'])
def test_each_channel_is_obligatory(tmp_path,expanded,channel):
    t=only(expanded,'RIO-TF')
    link=next(l for l in t.links if l.link_id=='TECH-RIO-TF-01')
    channels=json.loads(json.dumps(link.channels));channels[channel]['enabled']=False
    t=replace(t,links=tuple(replace(l,channels=channels) if l.link_id==link.link_id else l for l in t.links))
    r=run(tmp_path,t,4)
    s=r.snapshot()['sessions']['RIO-TF']
    assert s['status']=='unavailable' and not s['counters'].get('observations')


def test_loss_and_recovery_change_results(tmp_path,expanded):
    t=only(expanded,'RIO-TF'); r=run(tmp_path,t,8)
    first=r.snapshot()['sessions']['RIO-TF']['metrics']['arm_transmissions']
    r.configure('RIO-TF',scenario='degraded');r.advance(t,16)
    second=r.snapshot()['sessions']['RIO-TF']['metrics']['arm_transmissions']
    assert all(b<a/10 for a,b in zip(first,second))
    r.configure('RIO-TF',scenario='unavailable');r.advance(t,22)
    assert r.snapshot()['sessions']['RIO-TF']['status']=='unavailable'
    r.configure('RIO-TF',scenario='nominal');r.advance(t,24)
    assert r.snapshot()['sessions']['RIO-TF']['status']=='operational'


def test_no_source_has_no_success_or_final_metrics(tmp_path,expanded):
    r=run(tmp_path,only(expanded,'RIO-TF'),5,NoAcquisitionSource())
    s=r.snapshot()['sessions']['RIO-TF']
    assert s['status']=='unavailable' and s['counters'].get('errors')
    assert 'qber' not in s['metrics']


def test_memory_reset_does_not_fabricate_observations(tmp_path,expanded):
    r=run(tmp_path,only(expanded,'RIO-ENTANGLEMENT'),2)
    r.reset_node('PUC-RIO');s=r.snapshot()['sessions']['RIO-ENTANGLEMENT']
    assert s['status']=='configured' and not s['metrics']
    assert not r.sessions['RIO-ENTANGLEMENT'].pairs


def test_satellite_geometry_availability_and_doppler(tmp_path,expanded):
    from quantum_autopilot.quantum_protocols.satellite import station_geometry
    reference=(-22.953888889,-43.173611111)
    station=(-22.8606982,-43.2310552)
    overhead=station_geometry(0.,reference,reference,{})
    far=station_geometry(overhead['period_s']/2,reference,reference,{})
    nearby=station_geometry(0.,reference,station,{})
    assert overhead['elevation_deg']>80 and far['elevation_deg']<0
    assert nearby['sat_latitude']==overhead['sat_latitude']
    assert nearby['sat_longitude']==overhead['sat_longitude']
    r=run(tmp_path,only(expanded,'RIO-SATELLITE','ATLANTIC-SATELLITE'),8)
    sessions=r.snapshot()['sessions']
    assert sessions['RIO-SATELLITE']['metrics']['available']
    assert not sessions['ATLANTIC-SATELLITE']['metrics']['available']
    assert sessions['ATLANTIC-SATELLITE']['metrics']['coincidences']==0
    geometry={'range_km':500.,'elevation_deg':80.,'radial_velocity_km_s':5.}
    nominal=satellite_optics(geometry,{'doppler_compensation':True})
    raw=satellite_optics(geometry,{'doppler_compensation':False})
    assert nominal['channel_transmission']>raw['channel_transmission']


def test_adapter_replacement_executes_and_missing_capability_rejects(tmp_path,expanded):
    from quantum_autopilot.quantum_protocols.protocols import TwinFieldAdapter
    class CounterAdapter(TwinFieldAdapter):
        calls=0
        def process(self, raw, context):
            self.calls+=1;return super().process(raw,context)
    registry=default_registry();adapter=CounterAdapter();registry.register(adapter,replace=True)
    t=only(expanded,'RIO-TF')
    r=ProtocolRuntime(tmp_path/'replace.sqlite',t.topology_id,registry=registry,source=SimulatedAcquisitionSource())
    r.advance(t,2);assert adapter.calls>0
    node=t.node('TF-RIO-CENTER');telemetry={**node.telemetry,'technology_capabilities':[]}
    t=replace(t,nodes=tuple(replace(n,telemetry=telemetry) if n.node_id==node.node_id else n for n in t.nodes))
    r.advance(t,3);assert r.snapshot()['sessions']['RIO-TF']['status']=='incompatible'


def test_final_outputs_are_not_an_acquisition_contract():
    with pytest.raises(ProtocolError):
        Acquisition('c','s','tf_block','physical-source','physical',1,0.,0.,('q',),('c',),{'final_key':'fabricated'})
    with pytest.raises(ProtocolError):SimulatedAcquisitionSource(True)


def test_workspace_migration_is_atomic_and_idempotent(tmp_path):
    store=SimulatedTopologyStore(tmp_path/'state.sqlite')
    topology,_=ensure_institutional_workspace(store)
    assert len(topology.nodes)==71 and 'UNIJORGE' not in {n.node_id for n in topology.nodes}
    assert sum(n.telemetry.get('inventory',{}).get('kind')=='satellite' for n in topology.nodes)==9
    before=topology.to_dict()
    again=upgrade_global_workspace(store,topology)
    assert again.to_dict()==before
    assert ensure_institutional_workspace(store)[0].to_dict()==before


def test_source_replacement_drops_previous_source_rates_and_resources(tmp_path,expanded):
    r=run(tmp_path,only(expanded,'RIO-ENTANGLEMENT'),3)
    assert r.snapshot()['sessions']['RIO-ENTANGLEMENT']['counters']['observations']>0
    r.set_source(NoAcquisitionSource())
    s=r.snapshot()['sessions']['RIO-ENTANGLEMENT']
    assert not s['counters'] and not s['metrics'] and not r.sessions['RIO-ENTANGLEMENT'].pairs
    r.advance(r.topology,4)
    assert r.snapshot()['sessions']['RIO-ENTANGLEMENT']['metrics']['generation_pair_rate_hz']==0


@pytest.mark.parametrize('parameters',[{'divergence_urad':0},{'coherence_s':0},{'doppler_compensation':1},
                                        {'insertion_loss_db':-1},{'memory_slots':True}])
def test_invalid_parameters_fail_before_persistence(tmp_path,expanded,parameters):
    r=run(tmp_path,only(expanded,'RIO-SATELLITE'),1)
    before=r.sessions['RIO-SATELLITE'].config.to_dict()
    with pytest.raises(ProtocolError):r.configure('RIO-SATELLITE',parameters=parameters)
    assert r.sessions['RIO-SATELLITE'].config.to_dict()==before


def test_link_editor_preserves_modular_binding_and_rejects_protocol_downgrade(tmp_path,expanded):
    from quantum_autopilot.control_plane.inventory import ConfigureSimulatedLink
    store=SimulatedTopologyStore(tmp_path/'links.sqlite');store.initialize(expanded)
    link=next(l for l in expanded.links if l.link_id=='TECH-RIO-TF-01')
    request=ConfigureSimulatedLink('update',link.link_id,link.left_node_id,link.right_node_id,
                                  link.distance_km,link.nominal_pair_fidelity,False,expanded.topology_version,
                                  topology_id=expanded.topology_id,channels=link.channels)
    updated=store.configure_inventory_link(request)
    assert next(l for l in updated.links if l.link_id==link.link_id).technology==link.technology
    channels=json.loads(json.dumps(link.channels));channels['quantum']['protocol']='bb84-statistical-record-v1'
    with pytest.raises(ValueError,match='modular protocol'):
        store.configure_inventory_link(replace(request,channels=channels,expected_version=updated.topology_version))
