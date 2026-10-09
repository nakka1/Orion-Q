"""Published activity requires this operation's controls and received data."""
import copy
from dataclasses import replace

import pytest

from quantum_autopilot.control_plane.channels import local_pair
from quantum_autopilot.control_plane.communicators import CommunicatorStore
from quantum_autopilot.control_plane.topology import DEFAULT_TOPOLOGY_ID, NodeRole, SimulatedLink, SimulatedNodeState, SimulatedTopology, SimulatedTopologyStore
from quantum_autopilot.network_engine.engine import NetworkEngine
from quantum_autopilot.network_engine.sources import SimulatedInputSource
from quantum_autopilot.quantum_protocols.contracts import ProtocolConfig
from quantum_autopilot.quantum_protocols.orbits import geo_reference_orbit
from quantum_autopilot.quantum_protocols.sources import SimulatedAcquisitionSource

pytestmark=pytest.mark.unit


def engine_for(tmp_path,topology):
    store=SimulatedTopologyStore(tmp_path/'visibility.sqlite');store.initialize(topology)
    topology=CommunicatorStore(store,topology.topology_id).seed(topology,'simulated')
    return NetworkEngine(store,store.path,topology_id=topology.topology_id,source=SimulatedInputSource(42,'quiet'))


def operation_link(ident,a,b,session):
    channels=local_pair(ident,b);channels['quantum']['protocol']='quantum-operation-record-v1'
    return SimulatedLink(ident,a,b,1.,.9,channels=channels,
        technology={'session_id':session.session_id,'technologies':list(session.technologies)})


@pytest.mark.parametrize('blocker',('classical','quantum','isolation'))
def test_terrestrial_modular_link_withholds_fidelity_immediately_on_control_change(tmp_path,blocker):
    config=ProtocolConfig('A-R-B',('entanglement_swapping','quantum_memory'),('A','B'),('A','R','B'),('AR','RB'),
        parameters={'attempt_rate_hz':250000.,'block_s':.1,'timeout_s':2.,'memory_slots':8})
    nodes=tuple(SimulatedNodeState(ident,NodeRole.REPEATER if ident=='R' else NodeRole.ENDPOINT,0,'IDLE',
        telemetry={'inventory':{'kind':'repeater' if ident=='R' else 'installation'},
            'technology_sessions':[config.to_dict()] if ident=='A' else []}) for ident in config.path)
    topology=SimulatedTopology(DEFAULT_TOPOLOGY_ID,1,nodes,
        (operation_link('AR','A','R',config),operation_link('RB','R','B',config)))
    engine=engine_for(tmp_path,topology);before=next(link for link in engine.step().links if link.link_id=='AR')
    assert before.status=='up' and before.fidelity is not None
    if blocker=='isolation':
        engine.command('set_node_isolation',{'node_id':'R','isolated':True,
            'expected_version':engine.topology().topology_version,'topology_id':topology.topology_id})
    else:
        current=engine.topology();pair=copy.deepcopy(current.links[0].channels);pair[blocker]['enabled']=False
        changed=replace(current,links=(replace(current.links[0],channels=pair),current.links[1]),topology_version=current.topology_version+1)
        engine.store._replace_existing(changed,expected_version=current.topology_version,event_kind='operator_disabled_channel',payload={})
        engine._refresh('paired channel disabled')
    after=next(link for link in engine.live_state().links if link.link_id=='AR')
    assert after.status in ('down','disabled') and after.fidelity is None
    assert not engine.technologies._available(engine.technologies.sessions[config.session_id])
    assert 'paired channel unavailable' in ' '.join(after.provenance['status_reasons'])


def test_multiple_satellite_sessions_never_reuse_fresh_peer_housekeeping_for_expired_emission(tmp_path):
    orbit=geo_reference_orbit(-20.,compatible_stations=('A','B','C','D'))
    configs=[];links=[]
    for ident,endpoints in (('FRESH-CD',('C','D')),('INTERRUPTED-AB',('A','B'))):
        link_ids=tuple(f'{ident}-{station}' for station in endpoints)
        config=ProtocolConfig(ident,('satellite_doppler',),endpoints,(endpoints[0],'SAT',endpoints[1]),link_ids,
            parameters={**orbit,'orbit_record':orbit,'pair_source_hz':5e6,'block_s':2.,'timeout_s':4.,
                'receiver_diameter_m':4.,'divergence_urad':2.,'pointing_jitter_urad':.3,
                'background_cps':1.,'dark_count_cps':5.})
        configs.append(config)
        links.extend(operation_link(link_id,'SAT',station,config) for link_id,station in zip(link_ids,endpoints))
    nodes=[]
    for ident in ('C','D','A','B','SAT'):
        telemetry={'inventory':{'kind':'satellite' if ident=='SAT' else 'installation'},
            'technology_sessions':[config.to_dict() for config in configs if config.path[0]==ident]}
        if ident=='SAT':telemetry['orbit_record']=orbit
        else:telemetry['location']={'latitude':-22.95,'longitude':-43.17,'altitude_m':0.}
        nodes.append(SimulatedNodeState(ident,NodeRole.ENDPOINT,0,'IDLE',telemetry=telemetry))
    topology=SimulatedTopology('satellite-freshness',1,tuple(nodes),tuple(links))
    engine=engine_for(tmp_path,topology)
    class InterruptedSource(SimulatedAcquisitionSource):
        def acquire(self,command,config,environment):
            if config.session_id=='INTERRUPTED-AB' and command.emitted_at_s>=2.:return None
            return super().acquire(command,config,environment)
    engine.technologies.set_source(InterruptedSource(42))
    for _ in range(3):engine.step()
    state=engine.live_state().satellites['SAT']
    assert set(configs[1].link_ids)<={item['link_id'] for item in state['transmitting_links']}
    for _ in range(5):engine.step()
    expired=engine.technologies.sessions['INTERRUPTED-AB'];fresh=engine.technologies.sessions['FRESH-CD']
    clock=engine.technologies.clock_s
    assert expired.metrics['emitted_pairs']>0
    assert expired.metrics['housekeeping']['valid_until_s']<clock<=fresh.metrics['housekeeping']['valid_until_s']
    assert 0<=clock-expired.metrics['last_observed_at_s']<=6.
    state=engine.live_state().satellites['SAT'];emitting={item['link_id'] for item in state['transmitting_links']}
    assert not set(configs[1].link_ids)&emitting
    assert set(configs[0].link_ids)<=emitting and state['housekeeping_valid']
    assert next(bridge for bridge in state['bridges'] if bridge['session_id']=='INTERRUPTED-AB')['status'] in ('waiting','blocked')
