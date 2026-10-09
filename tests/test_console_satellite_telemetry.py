"""Satellite inspection remains readable across live updates and window sizes."""
import copy
import json
import os

os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import pytest
from PySide6.QtWidgets import QApplication

from quantum_autopilot.console.widgets.satellite_telemetry import SatelliteLivePanel, SatelliteTelemetryDialog

pytestmark=pytest.mark.unit


@pytest.fixture(scope='module')
def app():
    from quantum_autopilot.console.theme import install
    application=QApplication.instance() or QApplication([]);install(application);return application


@pytest.fixture
def metrics():
    return {'observation_origin':'physical','observation_source_id':'OGS-RIO',
        'housekeeping':{'at_s':10.,'valid_until_s':15.,'at_utc':'2026-10-06T00:00:10+00:00',
            'ecef_km':[6371.,0.,500.],'velocity_ecef_km_s':[0.,7.,0.],'source':'received OGS vectors'},
        'geometry':[{'azimuth_deg':120.,'elevation_deg':32.,'range_km':700.,'point_ahead_urad':47.,
            'carrier_doppler_ghz':2.7,'link_budget_db':{'geometric':35.,'total':45.}}],
        'tracking':[{'station_id':'CBPF'}], 'qber':.023,'coincidences':123,
        'finite_key':{'abort_reason':'insufficient_parameter_sample','phase_error_upper':.5}}


def all_text(table):return '\n'.join(table.item(r,c).text() for r in range(table.rowCount()) for c in range(table.columnCount()))


@pytest.fixture
def mission_ingress():
    """Decoded engineering fixture; no RF receiver, calibration or QKD proof."""
    return {'source_id':'mission_gateway','origin':'measurement_file','state':'listening',
        'last_received_at_utc':'2026-10-08T12:00:00+00:00','accepted':1,'quarantined':2,
        'latest':{'satellite_id':'SUOMI-100','mission_id':'suomi100','station':'fixture-ground-station',
            'received_at_utc':'2026-10-08T12:00:00+00:00','origin':'measurement_file',
            'frame_sha256':'a'*64,'status':'decoded_not_qualified','decode':{
                'decoder_id':'suomi100-stdlib','decoder_version':'1',
                'header':{'source':1},'payload':{
                    'eps':{'batt_v':7321,'temp':[12,13],'timestamp_unix_s':1700000000},
                    'com':{'rssi':-80},'obc':{'cur':[8,9]}},
                'integrity':{'verified':True,'authenticated':False},
                'clock':{'qualified':False,'onboard_timestamps':{'eps':{'unix_s':1700000000}},'received_at_inferred':False},
                'qualification':{'status':'decoded_not_qualified','ogs_validated':False,'qkd_validated':False},
                'units':{'qualified':False,'status':'raw_unscaled','physical_units':None}}}}


def test_housekeeping_science_units_source_and_freshness(app,metrics):
    dialog=SatelliteTelemetryDialog();dialog.set_metrics(metrics,session_id='RIO-SATELLITE',clock_s=12.)
    dialog.show();app.processEvents()
    try:
        hk=all_text(dialog.housekeeping);science=all_text(dialog.science)
        assert 'ECEF' in hk and 'km/s' in hk and 'µrad' in hk and 'GHz' in hk
        assert 'OGS-RIO' in hk and '123' in science and '0.023' in science and 'dB' in science
        assert not dialog.start.isEnabled()
        dialog.set_metrics(metrics,session_id='RIO-SATELLITE',clock_s=16.)
        assert 'VENCIDA' in dialog.summary.text()
    finally:dialog.close();dialog.deleteLater();app.processEvents()


def test_capture_stable_during_live_updates_and_scroll(app,metrics):
    dialog=SatelliteTelemetryDialog();dialog.resize(640,480)
    dialog.set_metrics(metrics);dialog.show();app.processEvents();dialog.capture()
    original=dialog.evidence.toPlainText()
    changed=copy.deepcopy(metrics);changed['coincidences']=234;changed['qber']=.04
    dialog.set_metrics(changed);app.processEvents()
    try:
        assert dialog.evidence.toPlainText()==original and '234' in all_text(dialog.science)
        bar=dialog.science.verticalScrollBar();bar.setValue(bar.maximum());app.processEvents()
        assert bar.value()==bar.maximum()
        assert dialog.science.item(dialog.science.rowCount()-1,0) is not None
        dialog.resize(1100,750);app.processEvents()
        assert dialog.housekeeping.columnWidth(1)>50
    finally:dialog.close();dialog.deleteLater();app.processEvents()


def test_ogs_controls_emit_reference_only_and_stop(app,metrics):
    dialog=SatelliteTelemetryDialog();sent=[];dialog.command.connect(lambda verb,payload:sent.append((verb,json.loads(payload))))
    dialog.set_metrics(metrics,session_id='RIO-SATELLITE',allowed=True)
    assert dialog.start.isEnabled()
    dialog.credential.setText('vault:mission-ogs-session');dialog.port.setValue(8766);dialog.start.click()
    assert sent==[('configure_technology_source',{'kind':'ogs_websocket','session_id':'RIO-SATELLITE','credential_ref':'vault:mission-ogs-session','port':8766})]
    dialog.set_metrics(metrics,session_id='RIO-SATELLITE',allowed=True,ingress={'ogs_gateway':{'pending_packets':2}})
    assert not dialog.start.isEnabled() and dialog.stop.isEnabled()
    dialog.stop.click();assert sent[-1][1]=={'kind':'stop_ogs'}
    dialog.close();dialog.deleteLater();app.processEvents()


@pytest.mark.parametrize('reference',('env:MISSION_OGS_TOKEN','a-secret-token-that-must-not-be-sent','vault:','vault:bad\nreference'))
def test_ogs_refuses_environment_or_token_even_when_invoked_directly(app,metrics,reference):
    dialog=SatelliteTelemetryDialog();sent=[];dialog.command.connect(lambda *args:sent.append(args))
    try:
        dialog.set_metrics(metrics,session_id='RIO-SATELLITE',allowed=True)
        dialog.credential.setText(reference);dialog._start()
        assert not sent and not dialog.start.isEnabled()
        assert 'vault:' in dialog.ogs_error.text()
        if reference!='vault:':assert reference not in dialog.ogs_error.text()
    finally:dialog.close();dialog.deleteLater();app.processEvents()


def test_packaged_satellite_validator_accepts_vault_reference_defaults(app,metrics,tmp_path):
    from quantum_autopilot.console.satellite_validation import validate_satellite_ui

    destination=tmp_path/'satellite-ogs-ui'
    report=validate_satellite_ui(destination,metrics)
    assert report['passed'],report['checks']
    assert report['checks']['credentials_are_references']
    assert json.loads((destination/'report.json').read_text(encoding='utf-8'))==report


@pytest.mark.parametrize('field',('credential','mission_credential'))
@pytest.mark.parametrize('reference',('env:LEGACY_SESSION','a-secret-token','vault:','vault:bad\nreference'))
def test_packaged_satellite_validator_rejects_invalid_reference_defaults(app,metrics,tmp_path,monkeypatch,field,reference):
    from quantum_autopilot.console.satellite_validation import validate_satellite_ui
    from quantum_autopilot.console.widgets import satellite_telemetry

    class InvalidCredentialDialog(SatelliteTelemetryDialog):
        def __init__(self):
            super().__init__()
            getattr(self,field).setText(reference)

    monkeypatch.setattr(satellite_telemetry,'SatelliteTelemetryDialog',InvalidCredentialDialog)
    report=validate_satellite_ui(tmp_path/'satellite-ogs-ui',metrics)
    assert not report['passed'] and not report['checks']['credentials_are_references']
    assert all(result for check,result in report['checks'].items() if check!='credentials_are_references')


def test_mission_raw_provenance_does_not_populate_orbit_or_quantum_science(app,mission_ingress):
    dialog=SatelliteTelemetryDialog()
    try:
        dialog.set_metrics({},satellite_id='SUOMI-100',mission_ingress=mission_ingress)
        rf=all_text(dialog.mission)
        assert 'EPS · batt_v' in rf and '7321' in rf and 'RAW' in rf
        assert 'COM · rssi' in rf and 'OBC · cur' in rf
        assert 'fixture-ground-station' in rf and 'measurement_file' in rf and 'suomi100-stdlib' in rf
        assert 'quarentena' in rf and '1700000000' in rf and 'CRC/integridade' in rf
        assert '7321' not in all_text(dialog.housekeeping) and '7321' not in all_text(dialog.science)
        assert 'SUOMI-100' not in all_text(dialog.science)
        position=next(r for r in range(dialog.housekeeping.rowCount()) if dialog.housekeeping.item(r,0).text().startswith('Posição ECEF'))
        assert dialog.housekeeping.item(position,1).text()=='—'
        battery=next(r for r in range(dialog.mission.rowCount()) if dialog.mission.item(r,0).text()=='EPS · batt_v')
        assert dialog.mission.item(battery,2).text()=='RAW'
        dialog.capture();captured=json.loads(dialog.evidence.toPlainText())
        assert captured['optical_session']=={} and captured['mission_rf']['latest']['decode']['payload']['eps']['batt_v']==7321
        dialog.set_metrics({},satellite_id='ANOTHER-SATELLITE',mission_ingress=mission_ingress)
        assert '7321' not in all_text(dialog.mission) and 'Nenhum quadro deste satélite' in all_text(dialog.mission)
        assert json.loads(dialog.evidence.toPlainText())==captured
    finally:dialog.close();dialog.deleteLater();app.processEvents()


def test_identical_mission_snapshots_reuse_cells_and_do_not_invalidate_table(app,mission_ingress):
    dialog=SatelliteTelemetryDialog()
    try:
        dialog.set_metrics({},satellite_id='SUOMI-100',mission_ingress=mission_ingress)
        cells=[dialog.mission.item(r,c) for r in range(dialog.mission.rowCount()) for c in range(4)]
        changed=[];dialog.mission.model().dataChanged.connect(lambda *args:changed.append(args))
        for _ in range(20):dialog.set_metrics({},satellite_id='SUOMI-100',mission_ingress=copy.deepcopy(mission_ingress))
        assert not changed
        assert cells==[dialog.mission.item(r,c) for r in range(dialog.mission.rowCount()) for c in range(4)]
        updated=copy.deepcopy(mission_ingress);updated['latest']['decode']['payload']['eps']['batt_v']=7400
        dialog.set_metrics({},satellite_id='SUOMI-100',mission_ingress=updated)
        assert '7400' in all_text(dialog.mission) and changed
        assert cells==[dialog.mission.item(r,c) for r in range(dialog.mission.rowCount()) for c in range(4)]
    finally:dialog.close();dialog.deleteLater();app.processEvents()


def test_mission_controls_vault_origin_ids_and_running_state(app,mission_ingress):
    dialog=SatelliteTelemetryDialog();sent=[];dialog.command.connect(lambda verb,payload:sent.append((verb,json.loads(payload))))
    try:
        dialog.set_metrics({},satellite_id='SUOMI-100',allowed=True,mission_ingress={'state':'stopped','latest':None})
        assert dialog.mission_start.isEnabled() and not dialog.mission_stop.isEnabled()
        assert dialog.mission_origin.currentText()=='physical'
        dialog.mission_origin.setCurrentText('measurement_file');dialog.mission_satellite_ids.setText('SUOMI-100, SAT-2, SUOMI-100')
        dialog.mission_start.click()
        assert sent==[('configure_technology_source',{'kind':'mission_websocket','source_id':'mission_gateway',
            'origin':'measurement_file','satellite_ids':['SUOMI-100','SAT-2'],'credential_ref':'vault:orion-mission-session','port':8766})]
        dialog.set_metrics({},satellite_id='SUOMI-100',allowed=True,mission_ingress=mission_ingress)
        assert not dialog.mission_start.isEnabled() and dialog.mission_stop.isEnabled()
        assert dialog.mission_satellite_ids.text()=='SUOMI-100, SAT-2, SUOMI-100'
        dialog._start_mission();assert len(sent)==1
        dialog.mission_stop.click();assert sent[-1]==('configure_technology_source',{'kind':'stop_mission'})
        dialog.set_metrics({},satellite_id='SUOMI-100',allowed=False,allowed_provision=False)
        dialog._start_mission();dialog._stop('mission');dialog._provision('mission');assert len(sent)==2
    finally:dialog.close();dialog.deleteLater();app.processEvents()


def test_mission_requires_source_satellite_and_vault_reference(app):
    dialog=SatelliteTelemetryDialog();sent=[];dialog.command.connect(lambda *args:sent.append(args))
    try:
        dialog.set_metrics({},allowed=True)
        assert not dialog.mission_start.isEnabled()
        dialog._start_mission();assert not sent and 'obrigatórios' in dialog.mission_error.text()
        dialog.mission_satellite_ids.setText('SAT-A');dialog.mission_credential.setText('env:MISSION_TOKEN')
        dialog._start_mission();assert not sent and not dialog.mission_start.isEnabled()
        dialog.mission_credential.setText('vault:mission-session');dialog.mission_source.clear()
        dialog._start_mission();assert not sent and not dialog.mission_start.isEnabled()
    finally:dialog.close();dialog.deleteLater();app.processEvents()


@pytest.mark.parametrize('active,state,transport',((True,'unavailable','listening'),(False,'decoded','stopped')))
def test_mission_controls_follow_listener_not_last_frame_state(app,mission_ingress,active,state,transport):
    dialog=SatelliteTelemetryDialog();sent=[];dialog.command.connect(lambda verb,payload:sent.append((verb,json.loads(payload))))
    try:
        snapshot=copy.deepcopy(mission_ingress)
        snapshot.update(listener_active=active,state=state,transport_state=transport)
        if active:snapshot['latest']=None
        dialog.set_metrics({},satellite_id='SUOMI-100',allowed=True,allowed_provision=True,mission_ingress=snapshot)
        assert dialog.mission_stop.isEnabled()==active
        assert dialog.mission_start.isEnabled()==(not active)
        assert dialog.mission_provision.isEnabled()==(not active)
        assert transport in dialog.mission_status.text()
        if active:
            dialog.mission_stop.click();assert sent==[('configure_technology_source',{'kind':'stop_mission'})]
        else:
            dialog.mission_start.click();assert sent[0][1]['kind']=='mission_websocket'
    finally:dialog.close();dialog.deleteLater();app.processEvents()


def test_durable_store_aliases_preserve_provenance_without_raw_frame_or_secrets(app,mission_ingress):
    dialog=SatelliteTelemetryDialog()
    try:
        latest=copy.deepcopy(mission_ingress['latest']);decoded=latest.pop('decode');station=latest.pop('station')
        latest.update(state='decoded',decoded=decoded,historical_replay=True,
                      envelope={'station':station,'frame_hex':'RAW_FRAME_MUST_STAY_OUT_OF_GUI',
                                'provenance':{'provider':'SatNOGS Network','observation_id':123,
                                              'url':'https://network.satnogs.org/observations/123/',
                                              'access_token':'SECRET_MUST_STAY_OUT_OF_GUI'}})
        snapshot={'source_id':'mission_gateway','origin':'measurement_file','state':'decoded','latest':latest,
                  'listener_active':False,'transport_state':'stopped',
                  'counters':{'accepted':1,'rejected':2},'counts':{'quarantined':2},
                  'credential_ref':'vault:private-reference','token':'ANOTHER_SECRET'}
        dialog.set_metrics({},satellite_id='SUOMI-100',mission_ingress=snapshot)
        assert '7321' in all_text(dialog.mission) and 'SatNOGS Network' in all_text(dialog.mission)
        replay=next(r for r in range(dialog.mission.rowCount()) if dialog.mission.item(r,0).text()=='Replay histórico')
        assert dialog.mission.item(replay,1).text()=='Sim'
        dialog.capture();text=dialog.evidence.toPlainText()
        assert 'RAW_FRAME_MUST_STAY_OUT_OF_GUI' not in text and 'SECRET' not in text and 'private-reference' not in text
        assert 'observation_id' in text and 'https://network.satnogs.org/observations/123/' in text
    finally:dialog.close();dialog.deleteLater();app.processEvents()


def test_provision_is_separate_admin_action_and_never_emits_a_token(app,metrics):
    dialog=SatelliteTelemetryDialog();sent=[];dialog.command.connect(lambda verb,payload:sent.append((verb,json.loads(payload))))
    try:
        dialog.set_metrics(metrics,session_id='RIO-SATELLITE',allowed=True)
        assert not dialog.provision.isEnabled() and not dialog.mission_provision.isEnabled()
        dialog._provision('ogs');dialog._provision('mission');assert not sent
        dialog.set_metrics(metrics,session_id='RIO-SATELLITE',allowed=False,allowed_provision=True)
        assert dialog.provision.isEnabled() and dialog.mission_provision.isEnabled() and not dialog.start.isEnabled()
        dialog.provision.click();dialog.mission_provision.click()
        assert sent==[('provision_satellite_session',{'source_id':'ogs_gateway','session_reference':'orion-ogs-session','ttl_seconds':3600}),
                      ('provision_satellite_session',{'source_id':'mission_gateway','session_reference':'orion-mission-session','ttl_seconds':3600})]
        assert all('token' not in data and 'credential' not in data for _,data in sent)
    finally:dialog.close();dialog.deleteLater();app.processEvents()


def test_floating_mission_section_is_lazy_incremental_and_removes_other_source(app,mission_ingress):
    panel=SatelliteLivePanel()
    try:
        state={'node_id':'SUOMI-100','origin':'simulated','clock_s':3.,'satellite_mission_ingress':mission_ingress}
        panel.set_state(state,{'name':'SUOMI-100'},presentation=False)
        assert panel._tables is None and 'mission' in panel.presentation_keys()
        panel.present_section('mission');table=panel._tables['mission']
        assert set(panel._tables)=={'mission'} and '7321' in '\n'.join(table.texts().values())
        changes=[];resets=[]
        table.model().dataChanged.connect(lambda *args:changes.append(args));table.model().modelReset.connect(lambda:resets.append(True))
        for _ in range(20):
            panel.set_state(copy.deepcopy(state),{'name':'SUOMI-100'},presentation=False);panel.present_section('mission')
        assert not changes and not resets and panel._tables['mission'] is table
        assert 'measurement_file' in panel._table_labels['mission'].text()
        panel.set_state({'node_id':'SAT-B','origin':'simulated'}, {'name':'SAT-B'})
        assert 'mission' not in panel.presentation_keys() and table.isHidden()
        assert 'mission' not in panel.content_texts()[0]
    finally:panel.close();panel.deleteLater();app.processEvents()


@pytest.mark.parametrize('session_id,optical', (('',{}),('SAT-A-SAT-B',{'isl_geometry':{'range_km':1200.}})))
def test_rf_only_and_isl_hide_ogs_without_disabling_mission_or_erasing_drafts(app,metrics,session_id,optical):
    dialog=SatelliteTelemetryDialog();sent=[];dialog.command.connect(lambda *args:sent.append(args))
    stopped={'state':'not_configured','transport_state':'stopped','listener_active':False,'latest':None}
    try:
        dialog.credential.setText('vault:preserved-ogs-session');dialog.port.setValue(9001)
        dialog.set_metrics(optical,session_id=session_id,satellite_id='SUOMI-100',mission_ingress=stopped,
                           allowed=True,allowed_provision=True)
        assert not dialog.tabs.isTabVisible(dialog._ogs_tab) and not dialog.ogs_page.isEnabled()
        assert not dialog.start.isEnabled() and not dialog.stop.isEnabled() and not dialog.provision.isEnabled()
        assert dialog.mission_start.isEnabled() and dialog.mission_provision.isEnabled()
        assert dialog.tabs.currentWidget() is dialog.mission and dialog.mission_satellite_ids.text()=='SUOMI-100'
        dialog._start();dialog._provision('ogs');assert not sent
        dialog.set_metrics(optical,session_id=session_id,satellite_id='SUOMI-100',mission_ingress=stopped,
                           ingress={'listener_active':True},allowed=True,allowed_provision=True)
        dialog._stop('ogs');assert not sent and not dialog.stop.isEnabled()
        dialog.tabs.setCurrentWidget(dialog.science)
        for _ in range(20):
            dialog.set_metrics(optical,session_id=session_id,satellite_id='SUOMI-100',mission_ingress=copy.deepcopy(stopped),
                               allowed=True,allowed_provision=True)
        assert dialog.tabs.currentWidget() is dialog.science
        dialog.set_metrics(metrics,session_id='RIO-SATELLITE',satellite_id='SUOMI-100',mission_ingress=stopped,
                           allowed=True,allowed_provision=True)
        assert dialog.tabs.isTabVisible(dialog._ogs_tab) and dialog.ogs_page.isEnabled()
        assert dialog.start.isEnabled() and dialog.provision.isEnabled()
        assert dialog.credential.text()=='vault:preserved-ogs-session' and dialog.port.value()==9001
    finally:dialog.close();dialog.deleteLater();app.processEvents()


def test_limited_mission_projection_keeps_flags_cause_and_separate_reception_times(app,mission_ingress):
    dialog=SatelliteTelemetryDialog()
    try:
        snapshot=copy.deepcopy(mission_ingress)
        snapshot['latest'].update(decode=None,status='quarantined',reason_code='unknown_decoder',
                                  ingested_at_utc='2026-10-08T12:01:02+00:00',presentation_limited=True,
                                  presentation_omitted=['decode.payload','decode.units'],details_available=True)
        dialog.set_metrics({},satellite_id='SUOMI-100',mission_ingress=snapshot)
        rf=all_text(dialog.mission)
        assert 'unknown_decoder' in rf and 'decode.payload' in rf and 'decode.units' in rf
        assert 'Projeção da interface limitada' in rf and 'Evidência completa disponível' in rf
        assert 'Omissão visual não equivale a ausência de observação' in rf
        assert 'frames.latest / frames.query' in rf
        assert '2026-10-08T12:00:00+00:00' in rf and '2026-10-08T12:01:02+00:00' in rf
        assert '7321' not in rf
        dialog.capture();captured=json.loads(dialog.evidence.toPlainText())['mission_rf']['latest']
        assert captured['presentation_limited'] is True and captured['details_available'] is True
        assert captured['presentation_omitted']==['decode.payload','decode.units']
        assert captured['reason_code']=='unknown_decoder' and captured['ingested_at_utc']=='2026-10-08T12:01:02+00:00'
        cells=[dialog.mission.item(r,c) for r in range(dialog.mission.rowCount()) for c in range(4)]
        changed=[];dialog.mission.model().dataChanged.connect(lambda *args:changed.append(args))
        for _ in range(20):dialog.set_metrics({},satellite_id='SUOMI-100',mission_ingress=copy.deepcopy(snapshot))
        assert not changed and cells==[dialog.mission.item(r,c) for r in range(dialog.mission.rowCount()) for c in range(4)]
        unavailable={'state':'unavailable','reason_code':'mission_source_rejected','error_type':'ValueError','latest':None}
        dialog.set_metrics({},satellite_id='SUOMI-100',mission_ingress=unavailable)
        assert 'mission_source_rejected' in all_text(dialog.mission)
    finally:dialog.close();dialog.deleteLater();app.processEvents()


def test_mission_received_summary_distinguishes_replay_from_absent_optical_observation(app,mission_ingress):
    dialog=SatelliteTelemetryDialog();panel=SatelliteLivePanel()
    try:
        snapshot=copy.deepcopy(mission_ingress);snapshot['latest']['historical_replay']=True
        dialog.set_metrics({},satellite_id='SUOMI-100',mission_ingress=snapshot)
        summary=dialog.summary.text()
        assert 'Missão / RF: quadro recebido' in summary and 'suomi100' in summary
        assert 'decoded_not_qualified' in summary and 'measurement_file' in summary and 'mission_gateway' in summary
        assert 'REPLAY HISTÓRICO' in summary and 'Camada óptica: Sem observação' in summary
        assert 'não comprova fótons' in summary
        state={'node_id':'SUOMI-100','satellite_mission_ingress':snapshot}
        panel.set_state(state,{'name':'SUOMI-100'},presentation=False)
        assert panel.details.isEnabled() and panel._tables is None
        summary=panel.summary.text()
        assert 'Missão / RF: quadro recebido' in summary and 'decoded_not_qualified' in summary
        assert 'REPLAY HISTÓRICO' in summary and 'measurement_file' in summary and 'suomi100' in summary
        assert 'Camada óptica: sem observação' in summary
        state['satellite_mission_ingress']={'state':'not_configured','listener_active':False,'latest':None}
        panel.set_state(state,{'name':'SUOMI-100'},presentation=False)
        assert panel.details.isEnabled() and 'mission' in panel.presentation_keys()
        assert 'quadro recebido' not in panel.summary.text()
        dialog.set_metrics({},satellite_id='SUOMI-100',mission_ingress=state['satellite_mission_ingress'])
        assert 'Missão / RF: not_configured' in dialog.summary.text()
        assert 'nenhuma sessão cadastrada' in dialog.summary.text()
    finally:
        dialog.close();dialog.deleteLater();panel.close();panel.deleteLater();app.processEvents()
