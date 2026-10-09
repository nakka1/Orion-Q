"""Classical RF inventory cannot imply an optical payload or QKD capability."""
import copy
import json
import os
from dataclasses import replace

os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import pytest
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QCoreApplication, QEvent

from quantum_autopilot.control_plane.communicators import Communicator, CommunicatorStore, RF_ONLY_PAYLOAD, defaults
from quantum_autopilot.control_plane.topology import SimulatedTopologyStore, default_simulated_topology
from quantum_autopilot.console.screens.communicators import CommunicatorsScreen

pytestmark=pytest.mark.unit

MISSION={'mission_id':'suomi100','norad_catalog_id':43804,'decoder_profile':'satnogs-beacon0-dual-crc32c-v1'}


@pytest.fixture
def rf():
    return Communicator('SUOMI-100','SUOMI-100','Suomi 100 RF registration fixture','satellite','intermediate',
        technologies=[],protocols=['CSP1'],capabilities=['classical_telemetry','classical_forward'],
        optical={},configuration={},mission=copy.deepcopy(MISSION),
        orbit={**defaults('orbit'),'optical_payload':RF_ONLY_PAYLOAD},
        integration={'mode':'measurement_stream','adapter':'mission_gateway','interface':'websocket_mission_v1',
                     'service_address':'ws://127.0.0.1:8766/satellite/v1/frames',
                     'credential_ref':'vault:orion-mission-session','data_origin':'physical'})


def test_rf_satellite_has_no_implicit_optical_source_or_protocol_parameters(rf):
    assert rf.technologies==[] and rf.optical=={} and rf.configuration=={}
    assert rf.orbit['compatible_stations']==[]
    assert rf.parameters()=={'orbit_record':rf.orbit}
    assert Communicator.from_dict(rf.to_dict())==rf


@pytest.mark.parametrize('change',(
    {'mission':{}},{'mission':None},{'protocols':[]},{'capabilities':[]},
    {'optical':defaults('optical')},{'configuration':{'satellite_doppler':defaults('satellite_doppler')}},
    {'capabilities':['classical_telemetry','satellite_source']},
))
def test_rf_registration_refuses_missing_or_invented_capabilities(rf,change):
    with pytest.raises(ValueError):Communicator.from_dict({**rf.to_dict(),**change})


@pytest.mark.parametrize('change',(
    {'norad_catalog_id':0},{'norad_catalog_id':True},{'norad_catalog_id':43804.0},
    {'mission_id':''},{'mission_id':'arbitrary mission / unsafe identifier'},
    {'decoder_profile':''},{'decoder_profile':'profile\nwith newline'},
    {'secret':'not a mission field'},
))
def test_mission_metadata_is_bounded_and_rejects_invalid_or_secret_fields(rf,change):
    with pytest.raises(ValueError):replace(rf,mission={**rf.mission,**change})


def test_rf_orbit_remains_mandatory_and_explicitly_without_quantum_payload(rf):
    with pytest.raises(ValueError,match='campo obrigatório'):replace(rf,orbit={})
    with pytest.raises(ValueError,match='optical_payload'):replace(rf,orbit={**rf.orbit,'optical_payload':'entangled photon source'})
    with pytest.raises(ValueError,match='classe LEO'):replace(rf,orbit={**rf.orbit,'altitude_km':35000.})
    with pytest.raises(ValueError,match='coordenadas terrestres'):replace(rf,location={'latitude':0.,'longitude':0.})


@pytest.mark.parametrize('address',(
    'ws://satellite.example:8766/satellite/v1/frames','ws://127.0.0.1:0/',
    'ws://localhost:8766@remote.example:8766/','ws://user:password@127.0.0.1:8766/',
    'ws://127.0.0.1:8766/?token=secret','ws://127.0.0.1:8766/#secret','ws://127.0.0.1:invalid/',
))
def test_mission_stream_rejects_remote_or_credential_bearing_addresses(rf,address):
    with pytest.raises(ValueError,match='endereço WebSocket local'):replace(rf,integration={**rf.integration,'service_address':address})


@pytest.mark.parametrize('reference',('env:ORION_OGS_TOKEN','keyring:mission-session','raw-token-value',''))
def test_mission_stream_requires_authorized_vault_reference(rf,reference):
    with pytest.raises(ValueError,match='vault:'):replace(rf,integration={**rf.integration,'credential_ref':reference})


def test_inventory_allows_extensible_decoder_ids_and_file_replay_without_hardware(rf):
    record=replace(rf,mission={'mission_id':'future-mission','norad_catalog_id':77777,'decoder_profile':'independent-profile-v2'},
        integration={'mode':'measurement_file','adapter':'measurement_file','interface':'jsonl_file',
                     'service_address':'mission-records.jsonl','credential_ref':'','data_origin':'measured'})
    assert record.mission['mission_id']=='future-mission'
    assert record.integration['mode']=='measurement_file'


def test_optical_satellite_can_add_classical_mission_without_changing_quantum_contract():
    original=Communicator('OPT','OPT','Optical fixture','satellite','intermediate',
        technologies=['satellite_doppler'],protocols=['BBM92'],capabilities=['satellite_source','tracking'],
        configuration={'satellite_doppler':defaults('satellite_doppler')},
        orbit={**defaults('orbit'),'compatible_stations':['GROUND']})
    upgraded=replace(original,mission=copy.deepcopy(MISSION),protocols=['BBM92','CSP1'],
        capabilities=['satellite_source','tracking','classical_telemetry'])
    assert original.parameters()==upgraded.parameters() and upgraded.optical==original.optical
    stream={'mode':'measurement_stream','adapter':'ogs_read_only_v2','interface':'websocket_ogs_v2',
            'service_address':'ws://127.0.0.1:8765/ogs/v2','credential_ref':'vault:orion-ogs-session','data_origin':'physical'}
    assert replace(original,integration=stream).integration==stream
    with pytest.raises(ValueError,match='vault:'):replace(original,integration={**stream,'credential_ref':'env:ORION_OGS_TOKEN'})
    assert Communicator.from_dict({key:item for key,item in original.to_dict().items() if key!='mission'}).mission=={}


def test_mission_norad_must_match_registered_tle(rf):
    pytest.importorskip('sgp4')
    orbit={**rf.orbit,'model':'sgp4_tle','source':'Vallado regression fixture',
        'epoch_utc':'2000-06-27T18:50:19+00:00',
        'tle_line1':'1 00005U 58002B   00179.78495062  .00000023  00000-0  28098-4 0  4753',
        'tle_line2':'2 00005  34.2682 348.7242 1859667 331.7664  19.3264 10.82419157413667'}
    record=replace(rf,orbit=orbit,mission={**rf.mission,'mission_id':'fixture','norad_catalog_id':5})
    assert record.mission['norad_catalog_id']==5
    with pytest.raises(ValueError,match='NORAD incompatível'):replace(record,mission=MISSION)


def test_rf_inventory_persists_without_adding_quantum_links(tmp_path,rf):
    topology=default_simulated_topology('rf-inventory-fixture')
    store=SimulatedTopologyStore(tmp_path/'topology.sqlite');store.initialize(topology)
    inventory=CommunicatorStore(store,topology.topology_id)
    saved=inventory.save({'expected_version':topology.topology_version,'record':rf.to_dict()})
    assert saved.node('SUOMI-100').telemetry['communicator']['mission']==MISSION
    assert saved.node('SUOMI-100').telemetry['communicator']['optical']=={}
    assert saved.links==topology.links
    reloaded=SimulatedTopologyStore(tmp_path/'topology.sqlite').get(topology.topology_id)
    assert reloaded.node('SUOMI-100').telemetry['communicator']==rf.to_dict()


@pytest.fixture(scope='module')
def app():
    from quantum_autopilot.console.theme import install
    application=QApplication.instance() or QApplication([]);install(application);return application


@pytest.fixture
def form(app):
    widget=CommunicatorsScreen()
    for key in ('device_id','node_id','name'):widget.fields[key].setText('SUOMI-100')
    widget.fields['physical_type'].setCurrentIndex(widget.fields['physical_type'].findData('satellite'))
    yield widget
    widget.shutdown();widget.close();widget.deleteLater();QCoreApplication.sendPostedEvents(None,QEvent.Type.DeferredDelete)


def mission_fields(form):
    for key,item in MISSION.items():form.group_fields['mission'][key].setText(str(item))


def tab_visible(form,group):return form.editor_tabs.isTabVisible(form.group_pages[group]._tab_index)


def test_rf_form_shows_orbit_and_mission_and_skips_invalid_optical_drafts(form):
    form.group_fields['optical']['detector_efficiency'].setText('unfinished optical draft')
    form.rf_only.setChecked(True);mission_fields(form)
    assert tab_visible(form,'orbit') and tab_visible(form,'mission') and not tab_visible(form,'optical')
    assert not any(widget.isChecked() for widget in form.techs.values())
    assert not form.group_fields['orbit']['optical_payload'].isEnabled() or form.group_fields['orbit']['optical_payload'].isReadOnly()
    payload=form.payload()
    assert payload['mission']==MISSION and payload['optical']=={} and payload['technologies']==[]
    assert payload['protocols']==['CSP1'] and 'satellite_source' not in payload['capabilities']
    form.fields['mode'].setCurrentIndex(form.fields['mode'].findData('measurement_stream'))
    payload=form.payload()
    assert payload['integration']['adapter']=='mission_gateway'
    assert payload['integration']['interface']=='websocket_mission_v1'
    assert payload['integration']['credential_ref']=='vault:orion-mission-session'


def test_rf_form_roundtrip_restore_preserves_drafts_and_reversible_optical_selection(form,app):
    form.group_fields['orbit']['compatible_stations'].setText('GROUND')
    form.group_fields['optical']['detector_efficiency'].setText('0.65')
    form.rf_only.setChecked(True);mission_fields(form)
    state=json.loads(json.dumps(form.save_state()))
    other=CommunicatorsScreen()
    try:
        other.restore_state(state)
        assert other.rf_only.isChecked() and other.payload()==form.payload()
        other.rf_only.setChecked(False)
        assert other.techs['satellite_doppler'].isChecked() and tab_visible(other,'optical')
        assert other.payload()['optical']['detector_efficiency']==.65
        assert other.payload()['mission']=={}
    finally:other.shutdown();other.close();other.deleteLater();app.processEvents()


def test_loading_rf_record_and_leaving_satellite_never_keeps_mission_fields(form,rf):
    form.load_record(rf.to_dict())
    assert form.rf_only.isChecked() and form.payload()==rf.to_dict()
    form.fields['physical_type'].setCurrentIndex(form.fields['physical_type'].findData('installation'))
    assert not tab_visible(form,'mission') and not tab_visible(form,'orbit')
    form.fields['mode'].setCurrentIndex(form.fields['mode'].findData('simulated'))
    assert form.payload()['mission']=={} and form.payload()['orbit']=={}
    assert 'classical_telemetry' not in form.payload()['capabilities']


def test_optical_form_can_select_mission_stream_and_preserve_both_stream_drafts(form):
    form.group_fields['orbit']['compatible_stations'].setText('GROUND')
    form.mission_enabled.setChecked(True);mission_fields(form)
    form.fields['mode'].setCurrentIndex(form.fields['mode'].findData('measurement_stream'))
    form.fields['service_address'].setText('ws://127.0.0.1:8765/ogs/v2')
    assert form.payload()['integration']['adapter']=='ogs_read_only_v2'
    form.mission_stream.setChecked(True)
    form.fields['service_address'].setText('ws://127.0.0.1:9876/satellite/v1/frames')
    assert form.payload()['integration']['adapter']=='mission_gateway'
    form.mission_stream.setChecked(False)
    assert form.fields['service_address'].text()=='ws://127.0.0.1:8765/ogs/v2'
    form.mission_stream.setChecked(True)
    assert form.fields['service_address'].text()=='ws://127.0.0.1:9876/satellite/v1/frames'
