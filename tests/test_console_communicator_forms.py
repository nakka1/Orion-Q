"""User workflows: type changes, relevant fields, durable saves and safe drafts."""
import json
import os
from pathlib import Path

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import pytest
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QCoreApplication, QEvent

from quantum_autopilot.console.screens.communicators import CommunicatorsScreen, DEFAULT_ROLES
from quantum_autopilot.control_plane.communicators import Communicator, TYPE_TECHNOLOGIES, TYPES

pytestmark = pytest.mark.unit


@pytest.fixture(scope='module')
def app():
    from quantum_autopilot.console.theme import install
    application = QApplication.instance() or QApplication([])
    install(application)
    return application


@pytest.fixture
def form(app):
    screen = CommunicatorsScreen(); screen.resize(1280, 900); screen.show(); app.processEvents()
    for key in ('device_id', 'node_id', 'name'): screen.fields[key].setText('FORM-NEW')
    yield screen
    screen.shutdown(); screen.close(); screen.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def kind(form, value):
    form.fields['physical_type'].setCurrentIndex(form.fields['physical_type'].findData(value))


def technologies(form): return {t for t,w in form.techs.items() if w.isChecked()}


def visible(form, group): return form.editor_tabs.isTabVisible(form.group_pages[group]._tab_index)


@pytest.mark.parametrize('target', [t for t in TYPES if t != 'satellite'])
def test_leaving_satellite_removes_only_automatic_satellite_technology(form, target):
    kind(form, 'satellite'); kind(form, target)
    assert technologies(form) == set(TYPE_TECHNOLOGIES[target])
    assert not visible(form, 'orbit')
    assert visible(form, 'satellite_doppler') == (target == 'ground_station')
    payload = form.payload()
    assert payload['orbit'] == {}
    assert 'satellite_source' not in payload['capabilities']


@pytest.mark.parametrize('target', TYPES)
def test_new_type_suggests_role_and_dependencies_without_manual_association(form, target):
    kind(form, target)
    if target == 'satellite': form.group_fields['orbit']['compatible_stations'].setText('CBPF, UFRJ')
    payload = form.payload()
    assert payload['network_role'] == DEFAULT_ROLES[target]
    assert set(payload['technologies']) == set(TYPE_TECHNOLOGIES[target])
    if target == 'satellite':
        assert set(payload['capabilities']) == {'satellite_source', 'tracking', 'classical_forward'}
    assert visible(form, 'orbit') == (target == 'satellite')


def test_explicit_terrestrial_satellite_terminal_keeps_no_orbit(form):
    form.techs['satellite_doppler'].setChecked(True)
    assert visible(form, 'satellite_doppler') and not visible(form, 'orbit')
    payload = form.payload()
    assert payload['orbit'] == {}
    assert 'optical_ground_terminal' in payload['capabilities']
    assert 'satellite_source' not in payload['capabilities']
    kind(form, 'ground_station'); kind(form, 'installation')
    assert form.techs['satellite_doppler'].isChecked()  # Explicit capability retained.


def test_manual_technologies_and_logical_role_survive_type_changes(form):
    form.fields['network_role'].setCurrentIndex(form.fields['network_role'].findData('core'))
    form.techs['purification'].setChecked(True)
    kind(form, 'quantum_memory'); kind(form, 'installation')
    assert technologies(form) == {'purification'}
    assert form.payload()['network_role'] == 'core'
    assert form.payload()['protocols'] == ['BBPSSW']


def test_technology_checkbox_populates_protocol_and_keeps_operator_extras(form):
    form.fields['protocols'].setText('BB84')
    form.techs['quantum_memory'].setChecked(True)
    payload = form.payload()
    assert payload['protocols'] == ['BB84', 'memory_readout']
    assert set(payload['capabilities']) == {'quantum_memory','endpoint_send','endpoint_receive'}
    form.techs['quantum_memory'].setChecked(False)
    assert form.payload()['protocols'] == ['BB84']
    assert set(form.payload()['capabilities']) == {'endpoint_send','endpoint_receive'}


def test_tf_sender_and_measurement_use_their_own_capabilities(form):
    kind(form, 'measurement_station'); form.techs['tf_qkd'].setChecked(True)
    assert set(form.payload()['capabilities']) == {'interferometer', 'single_photon_detector'}
    form.group_fields['tf_qkd']['device_role'].setCurrentText('sender')
    assert set(form.payload()['capabilities']) == {'coherent_sender', 'phase_reference'}
    form.group_fields['tf_qkd']['device_role'].setCurrentText('central_measurement')
    assert set(form.payload()['capabilities']) == {'interferometer', 'single_photon_detector'}
    form.group_fields['tf_qkd']['device_role'].setCurrentText('sender')
    assert set(form.payload()['capabilities']) == {'coherent_sender', 'phase_reference'}


def test_global_operation_lock_cannot_be_overridden_by_form_actions(form, provider):
    form.apply(provider.snapshot()); form.commands_busy=True; form.apply_command_capabilities()
    assert not form.save.isEnabled() and not form.connection_save.isEnabled()
    form.commands_busy=False; form.apply_command_capabilities()
    assert form.save.isEnabled() and form.connection_save.isEnabled()


def test_inactive_orbit_inputs_do_not_block_current_model_and_draft_survives(form):
    kind(form, 'satellite'); orbit = form.group_fields['orbit']
    orbit['compatible_stations'].setText('CBPF, UFRJ')
    orbit['state_vectors'].setText('['); orbit['ephemeris_max_age_s'].setText('unfinished')
    orbit['tle_line1'].setText('unfinished TLE')
    payload = form.payload()
    assert payload['orbit']['state_vectors'] == []
    assert payload['orbit']['tle_line1'] == ''
    assert orbit['state_vectors'].text() == '['
    orbit['model'].setCurrentText('sgp4_tle')
    with pytest.raises(ValueError, match='Validade|TLE'): form.payload()
    kind(form, 'installation')
    assert form.payload()['orbit'] == {}


def test_new_record_resets_satellite_selection_and_general_page(form):
    kind(form, 'satellite'); form.editor_tabs.setCurrentIndex(form.group_pages['orbit']._tab_index)
    form.new_record()
    assert technologies(form) == set()
    assert not visible(form, 'orbit') and not visible(form, 'satellite_doppler')
    assert form.editor_tabs.currentIndex() == 0
    assert form.table.currentRow() == -1 and not form.geo.isEnabled() and not form.reload.isEnabled()


@pytest.mark.parametrize('field', ('latitude', 'longitude'))
def test_partial_coordinates_are_refused_instead_of_inventing_zero(form, field):
    form.fields[field].setText('-22.9')
    with pytest.raises(ValueError, match='latitude e longitude'): form.payload()
    assert form.fields[field].text() == '-22.9'


def test_decimal_comma_and_optional_numeric_input(form):
    form.fields['latitude'].setText('-22,9'); form.fields['longitude'].setText('-43,2')
    form.group_fields['optical']['detector_efficiency'].setText('0,7')
    form.group_fields['optical']['dark_count_cps'].clear()
    payload = form.payload()
    assert payload['location'] == {'latitude': -22.9, 'longitude': -43.2, 'altitude_m': 0.}
    assert payload['optical']['detector_efficiency'] == .7
    assert 'dark_count_cps' not in payload['optical']


def test_invalid_active_numeric_input_names_the_field(form):
    form.group_fields['optical']['detector_efficiency'].setText('unfinished')
    with pytest.raises(ValueError, match='Eficiência do detector'): form.payload()


def test_ogs_stream_option_only_available_for_a_satellite_link(form):
    combo = form.fields['mode']; index = combo.findData('measurement_stream')
    assert not combo.model().item(index).isEnabled()
    kind(form, 'ground_station')
    assert combo.model().item(index).isEnabled() and not visible(form, 'orbit')
    combo.setCurrentIndex(index)
    assert '*' in form._where_form.labelForField(form.fields['credential_ref']).text()
    kind(form, 'installation')
    assert not combo.model().item(index).isEnabled()
    assert combo.currentData() == 'measurement_stream'  # Never silently substitute a simulated source.
    with pytest.raises(ValueError, match='tecnologia satelital'): form.payload()


def test_integration_modes_keep_their_own_drafts_and_inactive_credentials_do_not_block(form):
    combo=form.fields['mode'];combo.setCurrentIndex(combo.findData('physical'))
    form.fields['service_address'].setText('unfinished service');form.fields['credential_ref'].setText('unfinished reference')
    combo.setCurrentIndex(combo.findData('simulated'))
    assert form.payload()['integration']['credential_ref']==''
    assert form.payload()['integration']['service_address']==''
    combo.setCurrentIndex(combo.findData('physical'))
    assert form.fields['service_address'].text()=='unfinished service'
    assert form.fields['credential_ref'].text()=='unfinished reference'
    with pytest.raises(ValueError,match='credencial'):form.payload()


def test_mode_drafts_and_custom_interface_survive_theme_restore(form, app):
    combo=form.fields['mode'];combo.setCurrentIndex(combo.findData('measurement_file'))
    form.fields['service_address'].setText('operator-file.jsonl');form.fields['interface'].setText('jsonl_file')
    combo.setCurrentIndex(combo.findData('simulated'));form.fields['interface'].setText('operator_local_queue')
    other=CommunicatorsScreen()
    try:
        other.restore_state(form.save_state())
        assert other.payload()['integration']['interface']=='operator_local_queue'
        other.fields['mode'].setCurrentIndex(other.fields['mode'].findData('measurement_file'))
        assert other.fields['service_address'].text()=='operator-file.jsonl'
    finally:other.close();other.deleteLater();app.processEvents()


def test_theme_restore_keeps_invalid_draft_and_never_opens_hidden_orbit(form, app):
    kind(form, 'quantum_memory'); form.group_fields['quantum_memory']['coherence_s'].setText('unfinished')
    state = form.save_state(); state['editor_tab'] = form.group_pages['orbit']._tab_index
    other = CommunicatorsScreen()
    try:
        other.restore_state(state)
        assert other.group_fields['quantum_memory']['coherence_s'].text() == 'unfinished'
        assert not visible(other, 'orbit') and other.editor_tabs.currentIndex() == 0
        kind(other, 'installation')
        assert technologies(other) == set() and other.payload()['orbit'] == {}
    finally: other.close(); other.deleteLater(); app.processEvents()


def test_backend_refuses_orbits_on_terrestrial_devices(form):
    payload = form.payload(); payload['orbit'] = {'model': 'circular_reference'}
    with pytest.raises(ValueError, match='exclusivas'): Communicator.from_dict(payload)


def test_geo_form_normalizes_stationary_orbit_and_keeps_reference_longitude(form):
    kind(form,'satellite');orbit=form.group_fields['orbit']
    orbit['compatible_stations'].setText('CBPF, UFRJ')
    orbit['model'].setCurrentText('geostationary_reference')
    orbit['reference_longitude'].setText('-20')
    payload=form.payload()
    assert payload['orbit']['orbital_class']=='GEO'
    assert payload['orbit']['inclination_deg']==0 and payload['orbit']['eccentricity']==0
    assert payload['orbit']['reference_longitude']==-20
    assert not orbit['orbital_class'].isEnabled()
    orbit['model'].setCurrentText('circular_reference')
    assert orbit['orbital_class'].isEnabled()


def test_isl_only_satellite_requires_no_fictitious_ground_station(form):
    kind(form,'satellite');form.techs['satellite_isl'].setChecked(True)
    form.techs['satellite_doppler'].setChecked(False)
    payload=form.payload()
    assert payload['technologies']==['satellite_isl'] and payload['orbit']['compatible_stations']==[]
    assert {'optical_space_terminal','tracking','time_tagger','single_photon_detector'}<=set(payload['capabilities'])
    assert visible(form,'satellite_isl') and not visible(form,'satellite_doppler')
    form.group_fields['satellite_isl']['point_ahead_compensation'].setChecked(False)
    assert form.payload()['configuration']['satellite_isl']['point_ahead_compensation'] is False


def test_isl_refuses_terrestrial_terminal_and_overrated_emission(form):
    form.techs['satellite_isl'].setChecked(True)
    with pytest.raises(ValueError,match='tipo Satélite'):form.payload()
    kind(form,'satellite');form.techs['satellite_doppler'].setChecked(False)
    form.group_fields['optical']['pair_source_hz'].setText('10')
    form.group_fields['optical']['operational_limit_hz'].setText('100')
    with pytest.raises(ValueError,match='emissão ISL'):form.payload()


@pytest.mark.parametrize('group', ('location','configuration','orbit'))
@pytest.mark.parametrize('value', (None, []))
def test_registration_rejects_malformed_objects_before_they_can_break_editor(form, group, value):
    payload=form.payload();payload[group]=value
    with pytest.raises(ValueError,match=group+': objeto obrigatório'):Communicator.from_dict(payload)


@pytest.fixture
def provider(tmp_path, app):
    from quantum_autopilot.qkd.dashboard_api import demo_service
    from quantum_autopilot.network_engine.institutional import PRESET_ID
    from quantum_autopilot.console.service_provider import ServiceProvider
    service = demo_service(Path(__file__).resolve().parents[1], tmp_path, preset=PRESET_ID, scenario='quiet')
    for _ in range(5): service.network_step()
    backend = ServiceProvider(service, state_root=tmp_path/'provider'); backend.refresh()
    yield backend
    backend.stop(); app.processEvents()


def test_all_types_register_edit_and_reload_in_real_backend(form, provider):
    form.apply(provider.snapshot()); sent = []; form.command.connect(lambda *args: sent.append(args))
    original_links = tuple(link.id for link in provider.snapshot().topology.links)
    for index, physical_type in enumerate(TYPES):
        form.new_record(); ident = f'FORM-TYPE-{index}'
        for field in ('device_id', 'node_id', 'name'): form.fields[field].setText(ident)
        kind(form, physical_type)
        if physical_type == 'satellite': form.group_fields['orbit']['compatible_stations'].setText('BR-CBPF, BR-UFRJ')
        form.save.click(); assert sent[-1][0] == 'save_communicator'
        provider.save_communicator(sent[-1][1]); form.apply(provider.snapshot())
        assert 'CONFIRMADO' in form.message.text()
        assert provider.snapshot().live.communicators[ident]['physical_type'] == physical_type
        form.fields['name'].setText('Updated '+ident); form.save.click()
        provider.save_communicator(sent[-1][1]); form.apply(provider.snapshot()); form.reload.click()
        assert form.fields['name'].text() == 'Updated '+ident
        assert provider.snapshot().live.communicators[ident]['revision'] == 1
    assert tuple(link.id for link in provider.snapshot().topology.links) == original_links


def test_new_duplicate_identifier_never_emits_upsert_or_reports_confirmation(form, provider):
    form.apply(provider.snapshot()); record = provider.snapshot().live.communicators['CBPF']
    form.fields['device_id'].setText(record['device_id']); form.fields['node_id'].setText(record['node_id'])
    sent = []; form.command.connect(lambda *args: sent.append(args)); form.submit(); form.apply(provider.snapshot())
    assert sent == [] and 'já cadastrado' in form.message.text()
    assert form.fields['name'].text() == 'FORM-NEW'
    assert provider.snapshot().live.communicators['CBPF'] == record


def test_pending_record_does_not_ack_an_old_snapshot_and_survives_theme(form, provider, app):
    form.apply(provider.snapshot()); form.load_record(provider.snapshot().live.communicators['CBPF'])
    form.fields['name'].setText('Pending new name'); sent = []; form.command.connect(lambda *args: sent.append(args))
    form.submit(); form.apply(provider.snapshot())
    assert form.pending == 'CBPF' and form.fields['name'].text() == 'Pending new name'
    assert 'PENDENTE' in form.message.text() and not form.save.isEnabled()
    other = CommunicatorsScreen(); other.apply(provider.snapshot()); other.restore_state(form.save_state())
    try:
        provider.save_communicator(sent[-1][1]); other.apply(provider.snapshot())
        assert 'CONFIRMADO' in other.message.text() and other.pending is None
        assert other.fields['name'].text() == 'Pending new name' and other.save.isEnabled()
    finally: other.close(); other.deleteLater(); app.processEvents()


def test_edits_made_while_save_is_pending_remain_in_draft(form, provider):
    form.apply(provider.snapshot()); sent = []; form.command.connect(lambda *args: sent.append(args))
    form.submit(); form.fields['name'].setText('Later operator draft')
    provider.save_communicator(sent[-1][1]); form.apply(provider.snapshot())
    assert 'CONFIRMADO' in form.message.text() and 'rascunho' in form.message.text()
    assert form.fields['name'].text() == 'Later operator draft'
    assert provider.snapshot().live.communicators['FORM-NEW']['name'] == 'FORM-NEW'
    form.submit(); provider.save_communicator(sent[-1][1]); form.apply(provider.snapshot())
    assert provider.snapshot().live.communicators['FORM-NEW']['name'] == 'Later operator draft'


def test_selecting_new_connection_clears_old_session_and_channels(form, provider):
    form.apply(provider.snapshot()); form.session.setCurrentIndex(1)
    assert form.session_id.text() and form.channel_table.rowCount() > 0
    form.session.setCurrentIndex(0)
    assert form.session_id.text() == '' and form.path.text() == ''
    assert form.channel_table.rowCount() == 0 and not any(w.isChecked() for w in form.connection_techs.values())
    assert 'Nova conexão' in form.connection_note.text()


def test_other_topology_change_cannot_ack_connection_edit(form, provider):
    form.apply(provider.snapshot())
    selected = next(i for i in range(1,form.session.count()) if provider.snapshot().live.technologies['sessions'][form.session.itemData(i)]['config']['technologies'] == ['tf_qkd'])
    form.session.setCurrentIndex(selected); form.connection_scenario.setCurrentText('degraded')
    sent = []; form.command.connect(lambda *args: sent.append(args)); form.submit_connection()
    record = provider.snapshot().live.communicators['CBPF']
    provider.save_communicator(json.dumps({'expected_version':provider.snapshot().topology.version, 'record':{**record,'description':'Unrelated edit'}}))
    form.apply(provider.snapshot())
    assert form._pending_connection is not None and 'PENDENTE' in form.connection_note.text()
