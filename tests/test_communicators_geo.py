"""Device persistence, admission, causal transmission and backend-driven GEO."""
import json
import math
import os
from dataclasses import replace
from pathlib import Path

os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import pytest

from quantum_autopilot.control_plane.communicators import Communicator,TECH_CAPS,TECH_PROTOCOL,defaults
from quantum_autopilot.control_plane.channels import local_pair
from quantum_autopilot.control_plane.topology import SimulatedTopologyStore
from quantum_autopilot.network_engine.engine import NetworkEngine
from quantum_autopilot.network_engine.institutional import brazil_institutions
from quantum_autopilot.network_engine.sources import SimulatedInputSource
from quantum_autopilot.quantum_protocols.topology import expand_global
from quantum_autopilot.quantum_protocols.orbits import position,geometry
from quantum_autopilot.quantum_protocols.contracts import ProtocolConfig

pytestmark=pytest.mark.unit


@pytest.fixture
def engine(tmp_path):
    topology=expand_global(brazil_institutions())[0]
    store=SimulatedTopologyStore(tmp_path/'network.sqlite');store.initialize(topology)
    return NetworkEngine(store,tmp_path/'network.sqlite',topology_id=topology.topology_id,source=SimulatedInputSource(seed=42))


def save(engine,record):
    return engine.command('save_communicator',{'expected_version':engine.live_state().topology_version,'record':record.to_dict() if isinstance(record,Communicator) else record})


def device(ident,kind='installation',techs=(),role='endpoint'):
    caps=list(dict.fromkeys(c for t in techs for c in TECH_CAPS[t]))
    orbit={}
    if kind=='satellite':
        caps.append('satellite_source');orbit={**defaults('orbit'),'compatible_stations':['CBPF','UFRJ'],'reference_latitude':-22.9,'reference_longitude':-43.2}
    return Communicator(ident,ident,'Test '+ident,kind,role,technologies=list(techs),protocols=[TECH_PROTOCOL[t] for t in techs],capabilities=caps,
        configuration={t:defaults(t) for t in techs},location={} if kind=='satellite' else {'latitude':-22.9,'longitude':-43.2,'altitude_m':10.},orbit=orbit)


def connection(engine,path,technologies,ident='TEST-SESSION'):
    ids=[f'{ident}-{i+1}' for i in range(len(path)-1)]
    config=ProtocolConfig(ident,tuple(technologies),(path[0],path[-1]),tuple(path),tuple(ids),seed=42,parameters={'block_s':.5,'timeout_s':3.})
    links=[]
    for i,link_id in enumerate(ids):
        a,b=path[i:i+2]
        if 'tf_qkd' in technologies and i==1:a,b=b,a
        if 'satellite_doppler' in technologies and i==0:a,b=b,a
        pair=local_pair(link_id,b);pair['quantum']['protocol']='quantum-operation-record-v1'
        links.append({'link_id':link_id,'channels':pair})
    return {'config':config.to_dict(),'links':links,'expected_version':engine.live_state().topology_version}


def test_backend_nomenclature_and_idempotent_migration(engine):
    state=engine.live_state();records=state.communicators
    assert records['CBPF']['device_id']=='BR-CBPF' and records['CBPF']['network_role']=='core'
    assert records['PUC-RIO']['device_id']=='BR-PUCRIO' and records['PUC-RIO']['network_role']=='endpoint'
    assert 'IME-USP' in records and records['IME-RIO']['device_id']=='BR-IME'
    assert records['CN-BEIJING']['device_id']=='CN-QCN-BJ'
    assert records['SAT-RIO-SATELLITE']['physical_type']=='satellite'
    assert records['SAT-RIO-SATELLITE']['network_role']=='intermediate'
    before=engine.store.get(engine.topology_id)
    reference=expand_global(brazil_institutions())[0]
    assert before.topology_version==reference.topology_version
    assert {n.node_id:n.state_version for n in before.nodes}=={n.node_id:n.state_version for n in reference.nodes}
    assert engine.communicators.seed(before,'simulated')==before
    from contextlib import closing
    from quantum_autopilot.control_plane.command_reconciliation import _read_topology
    with closing(engine.store._connect()) as connection:
        assert _read_topology(connection,engine.topology_id)==before.to_dict()
    assert len(before.links)==71 and len(before.nodes)==66
    from quantum_autopilot.control_plane.communicators import seed_record
    node=before.node('CBPF');location={**node.telemetry['location'],'altitude_m':42.}
    migrated=seed_record(replace(node,telemetry={**node.telemetry,'location':location}),before,'simulated')
    assert migrated.location['altitude_m']==42. and 'BB84' in migrated.protocols


def test_first_registration_in_empty_workspace_is_atomic(tmp_path):
    from quantum_autopilot.control_plane.topology import DEFAULT_TOPOLOGY_ID,default_simulated_topology
    path=tmp_path/'fresh.sqlite';store=SimulatedTopologyStore(path)
    fresh=NetworkEngine(store,path,topology_id=DEFAULT_TOPOLOGY_ID)
    bad=device('FRESH').to_dict();bad['optical']['detector_efficiency']=2.
    with pytest.raises(ValueError):save(fresh,bad)
    assert store.get(DEFAULT_TOPOLOGY_ID) is None
    save(fresh,device('FRESH'))
    topology=store.get(DEFAULT_TOPOLOGY_ID)
    assert topology.topology_version==1 and len(topology.nodes)==4
    assert topology.links==default_simulated_topology().links
    assert set(fresh.live_state().communicators)=={'alice','repeater-a','bob','FRESH'}


def test_legacy_inventory_enrolment_requires_explicit_registration(tmp_path):
    from quantum_autopilot.control_plane.inventory import AddSimulatedNode
    from quantum_autopilot.control_plane.topology import DEFAULT_TOPOLOGY_ID
    path=tmp_path/'legacy.sqlite';store=SimulatedTopologyStore(path)
    before=store.add_inventory_node(AddSimulatedNode('LEGACY','endpoint',0,'127.0.0.1',7411))
    legacy=NetworkEngine(store,path,topology_id=DEFAULT_TOPOLOGY_ID)
    assert store.get(DEFAULT_TOPOLOGY_ID)==before
    legacy.live_state()
    assert store.get(DEFAULT_TOPOLOGY_ID)==before
    invalid=device('ENROLLED').to_dict();invalid['optical']['detector_efficiency']=2.
    with pytest.raises(ValueError):save(legacy,invalid)
    assert store.get(DEFAULT_TOPOLOGY_ID)==before
    save(legacy,device('ENROLLED'))
    after=store.get(DEFAULT_TOPOLOGY_ID)
    assert after.topology_version==before.topology_version+1 and after.links==before.links
    assert set(legacy.live_state().communicators)=={n.node_id for n in after.nodes}


@pytest.mark.parametrize('kind,techs,role',[
 ('installation',(), 'endpoint'),('quantum_repeater',('entanglement_swapping','quantum_memory'),'repeater'),
 ('measurement_station',('tf_qkd',),'measurement'),('ground_station',('satellite_doppler',),'ground_station'),
 ('satellite',('satellite_doppler',),'intermediate'),('quantum_memory',('quantum_memory',),'service'),
 ('purification_service',('purification',),'service')])
def test_each_type_persists_without_fake_connections(engine,kind,techs,role):
    record=device('NEW-DEVICE',kind,techs,role);save(engine,record)
    stored=engine.store.get(engine.topology_id)
    assert stored.node(record.node_id).telemetry['communicator']==record.to_dict()
    assert len(stored.links)==71 and not next(n for n in engine.live_state().nodes if n.node_id==record.node_id).links
    reopened=SimulatedTopologyStore(engine.journal_path).get(engine.topology_id)
    assert reopened.node(record.node_id).telemetry['communicator']['physical_type']==kind


@pytest.mark.parametrize('change',[
 lambda r:r['optical'].update(detector_efficiency=1.01),
 lambda r:r['integration'].update(credential_ref='literal-secret'),
 lambda r:r.update(network_role='satellite'),
 lambda r:r['configuration']['entanglement_swapping'].update(bsm_success=.9),
 lambda r:r['configuration']['quantum_memory'].update(compatible_wavelength_nm=1550),
 lambda r:r['configuration']['entanglement_swapping'].update(classical_required=False),
])
def test_invalid_device_is_atomic(engine,change):
    record=device('BAD','quantum_repeater',('entanglement_swapping','quantum_memory'),'repeater').to_dict();change(record)
    version=engine.live_state().topology_version
    with pytest.raises(ValueError):save(engine,record)
    assert engine.store.get(engine.topology_id).topology_version==version


def test_stale_revision_refused_preserves_links_and_location(engine):
    record=engine.live_state().communicators['CBPF'];before=list(engine.store.get(engine.topology_id).links)
    save(engine,{**record,'name':'Updated hub'})
    with pytest.raises(ValueError):save(engine,record)
    assert list(engine.store.get(engine.topology_id).links)==before
    assert engine.live_state().communicators['CBPF']['name']=='Updated hub'


def test_full_activation_tf_and_channel_refusal(engine):
    for ident,role in [('NEW-A','endpoint'),('NEW-M','measurement'),('NEW-B','endpoint')]:
        record=device(ident,techs=('tf_qkd',),role=role)
        if role=='measurement':record.configuration['tf_qkd']['device_role']='central_measurement'
        save(engine,record)
    payload=connection(engine,['NEW-A','NEW-M','NEW-B'],('tf_qkd',))
    broken=json.loads(json.dumps(payload));broken['links'][0]['channels']['classical']['channel_id']=broken['links'][0]['channels']['quantum']['channel_id']
    version=engine.live_state().topology_version
    with pytest.raises(ValueError):engine.command('configure_communicator_connection',broken)
    assert engine.store.get(engine.topology_id).topology_version==version
    result=engine.command('configure_communicator_connection',payload)
    assert result['status']=='configured' and result['physical_action_performed'] is False
    for _ in range(5):engine.step()
    session=engine.live_state().technologies['sessions']['TEST-SESSION']
    assert session['metrics']['sifted_bits']>0 and session['metrics']['certified_secret_bits']==0
    kinds={e['event'] for e in engine.live_state().technologies['events'] if e['session_id']=='TEST-SESSION'}
    assert {'acquired','validated','classical_received','protocol_processed'}<=kinds


def test_registered_memory_and_purification_services_execute(engine):
    combo=('entanglement_swapping','quantum_memory','purification')
    for ident,kind,role in [('DEV-A','installation','endpoint'),('DEV-R','quantum_repeater','repeater'),
                            ('DEV-P','purification_service','service'),('DEV-M','quantum_memory','service'),('DEV-B','installation','endpoint')]:
        save(engine,device(ident,kind,combo,role))
    engine.command('configure_communicator_connection',connection(engine,['DEV-A','DEV-R','DEV-P','DEV-M','DEV-B'],combo,'SERVICE-CHAIN'))
    for _ in range(30):engine.step()
    session=engine.live_state().technologies['sessions']['SERVICE-CHAIN']
    assert session['counters']['bsm_attempts']>0 and session['counters']['purification_attempts']>0
    assert session['counters']['generated_pairs']>0
    before=engine.store.get(engine.topology_id).topology_version
    engine.command('configure_technology_source',{'kind':'none'})
    for _ in range(4):engine.step()
    stopped=engine.live_state().technologies['sessions']['SERVICE-CHAIN']
    assert stopped['metrics'].get('last_observed_at_s') is None and stopped['counters'].get('generated_pairs',0)==0
    assert engine.store.get(engine.topology_id).topology_version==before


def test_registered_measurement_file_uses_same_pipeline(engine,tmp_path):
    for ident,role in [('FILE-A','endpoint'),('FILE-M','measurement'),('FILE-B','endpoint')]:
        record=device(ident,techs=('tf_qkd',),role=role)
        if role=='measurement':record.configuration['tf_qkd']['device_role']='central_measurement'
        save(engine,record)
    path=['FILE-A','FILE-M','FILE-B']
    engine.command('configure_communicator_connection',connection(engine,path,('tf_qkd',),'FILE-SESSION'))
    for _ in range(4):engine.step()
    session=engine.technologies.sessions['FILE-SESSION'];source=engine.technologies._source_for(session)
    raw=[{**r,'origin':'measurement_file','source_id':'measurement_file'} for r in source.records]
    filename=tmp_path/'synthetic-replay.jsonl';filename.write_text('\n'.join(json.dumps(r) for r in raw)+'\n',encoding='utf-8')
    topology=expand_global(brazil_institutions())[0];store=SimulatedTopologyStore(tmp_path/'replay.sqlite');store.initialize(topology)
    replay=NetworkEngine(store,tmp_path/'replay.sqlite',topology_id=topology.topology_id,source=SimulatedInputSource(seed=42))
    for ident,role in zip(path,('endpoint','measurement','endpoint')):
        record=device(ident,techs=('tf_qkd',),role=role)
        if role=='measurement':record.configuration['tf_qkd']['device_role']='central_measurement'
        integration={'mode':'measurement_file','adapter':'measurement_file','interface':'jsonl_file','service_address':str(filename),'credential_ref':'','data_origin':'measured'}
        save(replay,replace(record,integration=integration))
    replay.command('configure_communicator_connection',connection(replay,path,('tf_qkd',),'FILE-SESSION'))
    for _ in range(4):replay.step()
    observed=replay.live_state().technologies['sessions']['FILE-SESSION']
    assert observed['metrics']==engine.live_state().technologies['sessions']['FILE-SESSION']['metrics']
    assert observed['source']['origin']=='measurement_file'
    events=[e for e in replay.technologies.events if e['session_id']=='FILE-SESSION']
    assert all(e['origin']=='measurement_file' for e in events)


def test_disabling_communicator_blocks_legacy_and_modular_links(engine):
    record=engine.live_state().communicators['CBPF'];save(engine,{**record,'operational_state':'disabled'})
    for _ in range(3):engine.step()
    assert not engine.live_state().satellites['SAT-RIO-SATELLITE']['transmitting_links']
    legacy=[link for link in engine.live_state().links if 'CBPF' in (link.a,link.b) and not link.provenance.get('technology')]
    assert legacy and all(link.status=='disabled' and link.fidelity is None for link in legacy)


def test_physical_driver_and_wavelength_admission(engine):
    record=engine.live_state().communicators['PUC-RIO']
    integration={**record['integration'],'mode':'physical','adapter':'external_unvalidated','data_origin':'physical'}
    save(engine,{**record,'integration':integration})
    version=engine.live_state().topology_version
    with pytest.raises(ValueError,match='físico'):
        engine.command('configure_communicator_connection',connection(engine,['PUC-RIO','TF-RIO-CENTER','UFRJ'],('tf_qkd',)))
    assert engine.live_state().topology_version==version
    assert engine.live_state().technologies['sessions']['RIO-TF']['status']=='incompatible'


def test_orbit_transmission_window_and_doppler(engine):
    assert not engine.live_state().satellites['SAT-RIO-SATELLITE']['transmitting_links']
    for _ in range(5):engine.step()
    sat=engine.live_state().satellites['SAT-RIO-SATELLITE']
    assert len(sat['transmitting_links'])==2 and len(sat['orbit_ecef_km'])==193
    first=sat['ecef_km'];doppler=sat['stations']['UFRJ']['doppler_shift_hz']
    record=engine.live_state().communicators['SAT-RIO-SATELLITE']
    save(engine,{**record,'orbit':{**record['orbit'],'communication_windows':[[100,200]]}})
    for _ in range(4):engine.step()
    sat=engine.live_state().satellites['SAT-RIO-SATELLITE']
    assert sat['ecef_km']!=first and not sat['transmitting_links']
    assert not sat['stations']['CBPF']['visible'] and sat['stations']['CBPF']['channel_transmission']==0
    assert sat['stations']['UFRJ']['doppler_shift_hz']!=doppler
    session=engine.live_state().technologies['sessions']['RIO-SATELLITE']
    assert session['metrics']['coincidences']==0
    record=engine.live_state().communicators['SAT-RIO-SATELLITE']
    save(engine,{**record,'optical':{**record['optical'],'pair_source_hz':0.},'orbit':{**record['orbit'],'communication_windows':[]}})
    for _ in range(4):engine.step()
    assert engine.live_state().technologies['sessions']['RIO-SATELLITE']['metrics']['emitted_pairs']==0
    assert not engine.live_state().satellites['SAT-RIO-SATELLITE']['transmitting_links']


def test_low_pulse_rate_never_invents_minimum_tf_sample():
    from quantum_autopilot.quantum_protocols.contracts import DeviceCommand
    from quantum_autopilot.quantum_protocols.sources import SimulatedAcquisitionSource
    config=ProtocolConfig('LOW-RATE',('tf_qkd',),('A','B'),('A','M','B'),('AM','BM'),parameters={'pulse_rate_hz':1.})
    command=DeviceCommand('sample','LOW-RATE','tf_block',('A','M','B'),('Q-AM','Q-BM'),('C-AM','C-BM'),0.,3.,1,{'duration_s':.5,'arm_lengths_km':[1.,1.]})
    assert SimulatedAcquisitionSource().acquire(command,config,{}) is None


def test_kepler_orbit_perigee_apogee_and_epoch():
    orbit={**defaults('orbit'),'model':'kepler_elements','eccentricity':.2}
    first=position(orbit,0.);last=position(orbit,first['period_s']/2)
    assert math.sqrt(sum(x*x for x in first['ecef_km']))==pytest.approx(6371.+500.)
    assert math.sqrt(sum(x*x for x in last['ecef_km']))==pytest.approx((6371.+500.)/0.8*1.2)
    shifted=position({**orbit,'epoch_network_s':100},100.)
    assert shifted['ecef_km']==pytest.approx(first['ecef_km'])


@pytest.mark.parametrize('change',[
 {'model':'kepler_elements','eccentricity':.2},
 {'source':'TLE hypothetical'},
 {'epoch_network_s':1e12},
])
def test_orbital_admission_refuses_wrong_class_unsupported_source_and_epoch(change):
    record=device('INVALID-ORBIT','satellite',('satellite_doppler',),'intermediate').to_dict()
    record['orbit'].update(change)
    with pytest.raises(ValueError):Communicator.from_dict(record)


def test_compositions_without_a_resource_driver_are_refused(engine):
    config=replace(engine.technologies.sessions['RIO-SATELLITE'].config,technologies=('satellite_doppler','quantum_memory'))
    from quantum_autopilot.quantum_protocols.contracts import ProtocolError
    with pytest.raises(ProtocolError,match='additional driver'):engine.technologies.registry.validate(config,engine.store.get(engine.topology_id))


def test_public_aliases_and_replacing_session_retires_only_its_old_links(engine):
    satellite=engine.live_state().communicators['SAT-RIO-SATELLITE']
    save(engine,{**satellite,'orbit':{**satellite['orbit'],'compatible_stations':['BR-CBPF','BR-UFRJ']}})
    assert engine.live_state().communicators['SAT-RIO-SATELLITE']['orbit']['compatible_stations']==['CBPF','UFRJ']
    payload=connection(engine,['BR-PUCRIO','TF-RIO-CENTER','BR-UFRJ'],('tf_qkd',),'ALIAS')
    engine.command('configure_communicator_connection',payload)
    assert engine.technologies.sessions['ALIAS'].config.path==('PUC-RIO','TF-RIO-CENTER','UFRJ')
    keep={l.link_id for l in engine.store.get(engine.topology_id).links if l.link_id not in ('ALIAS-1','ALIAS-2')}
    payload=connection(engine,['BR-PUCRIO','TF-RIO-CENTER','BR-UFRJ'],('tf_qkd',),'ALIAS')
    for index,row in enumerate(payload['links']):
        row['link_id']=f'REPLACED-{index}';row['channels']=local_pair(row['link_id'],row['channels']['quantum']['receiver_id'])
        row['channels']['quantum']['protocol']='quantum-operation-record-v1'
    payload['config']['link_ids']=[r['link_id'] for r in payload['links']]
    engine.command('configure_communicator_connection',payload)
    assert {l.link_id for l in engine.store.get(engine.topology_id).links}==keep|{'REPLACED-0','REPLACED-1'}


def test_device_detector_and_memory_parameters_change_operations(engine):
    techs=('entanglement_swapping','quantum_memory')
    for ident in ('PROFILE-A','PROFILE-B'):
        record=device(ident,techs=techs)
        record.configuration['quantum_memory'].update(coherence_s=2. if ident.endswith('A') else 4.,memory_slots=2)
        save(engine,record)
    engine.command('configure_communicator_connection',connection(engine,['PROFILE-A','PROFILE-B'],techs,'PROFILE'))
    for _ in range(6):engine.step()
    session=engine.technologies.sessions['PROFILE']
    assert session.counts['generated_pairs']>0
    from quantum_autopilot.quantum_protocols.runtime import PairResource
    pair=PairResource('test','PROFILE-A','PROFILE-B',.9,0.,0.)
    assert engine.technologies._pair_parameter(session,pair,'coherence_s',1.)==pytest.approx(4./3.)
    record=engine.live_state().communicators['PROFILE-A']
    save(engine,{**record,'optical':{**record['optical'],'detector_efficiency':0.}})
    for _ in range(6):engine.step()
    session=engine.technologies.sessions['PROFILE']
    assert session.counts['generated_pairs']==0


@pytest.fixture(scope='module')
def app():
    from PySide6.QtWidgets import QApplication
    from quantum_autopilot.console.theme import install
    application=QApplication.instance() or QApplication([]);install(application);return application


@pytest.fixture
def ui(tmp_path,app):
    from quantum_autopilot.qkd.dashboard_api import demo_service
    from quantum_autopilot.network_engine.institutional import PRESET_ID
    from quantum_autopilot.console.service_provider import ServiceProvider
    service=demo_service(Path(__file__).resolve().parents[1],tmp_path,preset=PRESET_ID,scenario='quiet')
    for _ in range(5):service.network_step()
    provider=ServiceProvider(service,state_root=tmp_path/'provider');provider.refresh()
    yield provider
    provider.stop();app.processEvents()


def test_geo_labels_from_backend_and_globe_selection(ui,app):
    from quantum_autopilot.console.widgets.geo_map import GeoMap
    from quantum_autopilot.console.widgets.network_views import build_net_model
    from PySide6.QtCore import QPointF
    geo=GeoMap();geo.resize(1200,800);net=build_net_model(ui.snapshot());geo.set_model(net);geo.show();app.processEvents()
    assert net.node('CBPF').labelled and net.node('PUC-RIO').labelled
    assert not net.node('TF-RIO-CENTER').labelled and not net.node('SAT-RIO-SATELLITE').labelled
    assert set(geo.label_sides(geo.transform()))=={n.id for n in net.located if n.labelled}
    geo.focus_node.setCurrentIndex(geo.focus_node.findData('SAT-RIO-SATELLITE'))
    from PySide6.QtTest import QTest
    QTest.qWait(80)
    assert geo.globe.isVisible() and geo.globe._timer.isActive()
    radius=geo.globe.radius();geo.zoom_in.click();assert geo.globe.radius()>radius
    geo.zoom_out.click();assert geo.globe.radius()==pytest.approx(radius)
    state,lines=geo.globe.overlay_geometry();assert len(lines)==2
    assert geo.globe.satellite_id=='SAT-RIO-SATELLITE' and not geo.grab().isNull()
    old=geo.globe;geo.reset_view()
    assert not old._timer.isActive() and old._base is None
    QTest.qWait(80)
    import shiboken6
    assert geo.globe is None and not shiboken6.isValid(old)
    assert geo.resource_stats()['renderer_instances']==1
    geo.shutdown();geo.close()


def test_ui_registration_dynamic_fields_readback_and_stable_draft(ui,app):
    from quantum_autopilot.console.screens.communicators import CommunicatorsScreen
    screen=CommunicatorsScreen();screen.resize(1200,900);screen.apply(ui.snapshot());screen.show();app.processEvents()
    screen.fields['device_id'].setText('NEW-UI');screen.fields['node_id'].setText('NEW-UI');screen.fields['name'].setText('New memory')
    screen.fields['physical_type'].setCurrentIndex(screen.fields['physical_type'].findData('quantum_memory'));screen.associate()
    assert screen.editor_tabs.isTabVisible(screen.group_pages['quantum_memory']._tab_index)
    assert not screen.editor_tabs.isTabVisible(screen.group_pages['orbit']._tab_index)
    sent=[];screen.command.connect(lambda *args:sent.append(args));screen.submit()
    assert sent[-1][0]=='save_communicator'
    ui.save_communicator(sent[-1][1]);screen.apply(ui.snapshot());app.processEvents()
    assert screen.fields['name'].text()=='New memory' and 'CONFIRMADO' in screen.message.text()
    screen.fields['name'].setText('Unsent draft');screen.capture();evidence=screen.evidence.toPlainText()
    ui.service.network_step();ui.refresh();screen.apply(ui.snapshot());app.processEvents()
    assert screen.fields['name'].text()=='Unsent draft' and screen.evidence.toPlainText()==evidence
    screen.shutdown();screen.close()


def test_form_preserves_invalid_draft_and_connection_on_theme_rebuild(ui,app):
    from quantum_autopilot.console.screens.communicators import CommunicatorsScreen
    screen=CommunicatorsScreen();screen.apply(ui.snapshot())
    screen.load_record(ui.snapshot().live.communicators['SAT-RIO-SATELLITE'])
    screen.group_fields['orbit']['epoch_utc'].setText('unfinished epoch')
    screen.group_fields['orbit']['communication_windows'].setText('[')
    screen.session_id.setText('UI-CONNECTION');screen.path.setText('BR-PUCRIO, TF-RIO-CENTER, BR-UFRJ')
    screen.connection_techs['tf_qkd'].setChecked(True);screen.prepare_segments()
    state=screen.save_state();other=CommunicatorsScreen();other.apply(ui.snapshot());other.restore_state(state)
    assert other.group_fields['orbit']['epoch_utc'].text()=='unfinished epoch'
    assert other.group_fields['orbit']['communication_windows'].text()=='['
    assert other.path.text()==screen.path.text() and other.channel_table.rowCount()==2
    sent=[];other.command.connect(lambda *args:sent.append(args));other.submit_connection()
    ui.configure_communicator_connection(sent[-1][1]);other.apply(ui.snapshot())
    assert 'CONFIRMADO' in other.connection_note.text()
    assert 'UI-CONNECTION' in ui.snapshot().live.technologies['sessions']
    screen.shutdown();screen.close();other.shutdown();other.close()


@pytest.mark.parametrize('size',[(640,480),(1280,800),(3440,1440)])
def test_globe_and_forms_render_at_resolutions(ui,app,size):
    from quantum_autopilot.console.widgets.geo_map import GeoMap
    from quantum_autopilot.console.widgets.network_views import build_net_model
    from quantum_autopilot.console.screens.communicators import CommunicatorsScreen
    geo=GeoMap();geo.resize(*size);geo.set_model(build_net_model(ui.snapshot(),selection=('node','SAT-RIO-SATELLITE')));geo.show()
    screen=CommunicatorsScreen();screen.resize(*size);screen.apply(ui.snapshot());screen.show();app.processEvents()
    assert not geo.grab().isNull() and not screen.grab().isNull()
    geo.shutdown();geo.close();screen.shutdown();screen.close()
