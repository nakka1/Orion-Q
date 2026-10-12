"""Bundled UI registration, backend admission and orbital rendering diagnostic."""
import json
import math
from pathlib import Path


ACTIVATION_STAGE_ORDER = (
    'acquisition_requested', 'acquired', 'validated', 'synchronized',
    'quantum_transmission_observed', 'protocol_intake_deferred',
    'classical_transmitted', 'classical_received', 'protocol_proposal_prepared',
    'postprocessing_classical_transmitted', 'postprocessing_classical_received',
    'protocol_processed', 'metrics_computed',
)


def _activation_receipt_summary(activation, session_id='CHECK-TF'):
    """Admit finite SNS completion only after every causal public exchange."""
    from ..quantum_protocols.contracts import digest

    by_command = {}
    for event in activation:
        by_command.setdefault(event.get('command_id'), []).append(event)
    completed = []
    command_summaries = []
    for command_id, trace in by_command.items():
        if not command_id or any(event.get('session_id') != session_id for event in trace):
            continue
        stages = [event.get('event') for event in trace]
        if not all(stage in stages for stage in ACTIVATION_STAGE_ORDER):
            continue
        positions = [stages.index(stage) for stage in ACTIVATION_STAGE_ORDER]
        if positions != sorted(positions) or 'operation_error' in stages:
            continue
        try:
            times = [float(event['at_s']) for event in trace]
            if not all(math.isfinite(value) for value in times) or times != sorted(times):
                continue
            command = trace[stages.index('acquisition_requested')]['detail']['command']
            deadline = float(command['deadline_s'])
            if (command['command_id'] != command_id or command['session_id'] != session_id
                    or not math.isfinite(deadline) or times[-1] > deadline):
                continue
            sent = [(index, event) for index, event in enumerate(trace)
                    if event['event'] == 'postprocessing_classical_transmitted']
            received = [(index, event) for index, event in enumerate(trace)
                        if event['event'] == 'postprocessing_classical_received']
            processed_index = stages.index('protocol_processed')
            proposal_index = stages.index('protocol_proposal_prepared')
            if (not sent or len(sent) != len(received)
                    or trace[processed_index]['detail'].get('classical_exchanges_received') != len(sent)):
                continue
            valid = True
            previous_receive = proposal_index
            for sequence, ((sent_index, transmission), (received_index, reception)) in enumerate(
                    zip(sent, received), start=1):
                message = transmission['detail']
                unsigned = {key: value for key, value in message.items() if key != 'receipt_sha256'}
                if (not previous_receive < sent_index < received_index < processed_index
                        or message != reception['detail']
                        or message['command_id'] != command_id or message['session_id'] != session_id
                        or message['sequence'] != sequence
                        or message['message_id'] != f'{command_id}:public:{sequence}'
                        or message['classical_channel_ids'] != command['classical_channel_ids']
                        or message['associated_quantum_ids'] != command['quantum_channel_ids']
                        or not message['classical_channel_ids'] or not message['associated_quantum_ids']
                        or message['receipt_sha256'] != digest(unsigned)
                        or transmission['at_s'] != message['transmitted_at_s']
                        or reception['at_s'] != message['received_at_s']
                        or not message['transmitted_at_s'] < message['received_at_s'] <= deadline):
                    valid = False
                    break
                previous_receive = received_index
            if not valid:
                continue
        except (KeyError, TypeError, ValueError):
            continue
        completed.append(command_id)
        command_summaries.append({'command_id': command_id, 'public_exchanges_received': len(received),
                                  'metrics_at_s': trace[stages.index('metrics_computed')]['at_s'],
                                  'deadline_s': deadline})
    return {'session_id': session_id, 'completed_command_ids': completed,
            'required_stage_order': list(ACTIVATION_STAGE_ORDER), 'events': len(activation),
            'completed_commands': command_summaries,
            'source': 'persisted technology_events; UI preview is bounded',
            'scope': 'finite SNS causal simulated transport; not authenticated physical communication'}


def validate_communicators_geo(destination,repository_root):
    from tempfile import mkdtemp
    from PySide6.QtWidgets import QApplication
    from PySide6.QtTest import QTest
    from ..control_plane.communicators import TYPES
    from ..network_engine.institutional import PRESET_ID
    from ..qkd.dashboard_api import demo_service
    from .screens.communicators import CommunicatorsScreen
    from .service_provider import ServiceProvider
    from .widgets.geo_map import GeoMap
    from .widgets.network_views import build_net_model

    out=Path(destination);out.mkdir(parents=True,exist_ok=True)
    app=QApplication.instance();checks={};screen=None;geo=None
    state_root=Path(mkdtemp(prefix='state-',dir=out))
    service=demo_service(repository_root,state_root,preset=PRESET_ID,scenario='quiet')
    provider=ServiceProvider(service,state_root=state_root/'provider')
    report={'passed':False,'checks':checks,'physical_action_performed':False,
            'scope':'Qt widget interactions and backend readback in frozen runtime; isolated simulated inventory'}
    try:
        for _ in range(5):service.network_step()
        provider.refresh();screen=CommunicatorsScreen();screen.resize(1280,900)
        screen.apply(provider.snapshot());screen.show();app.processEvents()
        sent=[];screen.command.connect(lambda *args:sent.append(args))
        count=len(provider.snapshot().topology.links)
        for index,kind in enumerate(TYPES):
            screen.new_record();ident=f'CHECK-DEVICE-{index}'
            for key,value in (('device_id',ident),('node_id',ident),('name','Diagnostic '+kind)):
                screen.fields[key].setText(value)
            screen.fields['physical_type'].setCurrentIndex(screen.fields['physical_type'].findData(kind))
            if kind=='measurement_station':
                screen.techs['tf_qkd'].setChecked(True)
                screen.group_fields['tf_qkd']['device_role'].setCurrentText('central_measurement')
            if kind=='satellite':screen.group_fields['orbit']['compatible_stations'].setText('CBPF, UFRJ')
            screen.save.click();app.processEvents()
            if not sent or sent[-1][0]!='save_communicator':raise ValueError(screen.message.text())
            provider.save_communicator(sent[-1][1]);screen.apply(provider.snapshot());app.processEvents()
            checks['register_'+kind]=(provider.snapshot().live.communicators[ident]['physical_type']==kind and 'CONFIRMADO' in screen.message.text())
        checks['registration_adds_no_artificial_links']=len(provider.snapshot().topology.links)==count
        screen.tabs.setCurrentIndex(1);screen.session_id.setText('CHECK-TF')
        screen.path.setText('BR-PUCRIO, TF-RIO-CENTER, BR-UFRJ');screen.connection_techs['tf_qkd'].setChecked(True)
        screen.prepare_segments();screen.connection_save.click();app.processEvents()
        provider.configure_communicator_connection(sent[-1][1]);screen.apply(provider.snapshot())
        checks['paired_channel_activation_readback']='CONFIRMADO' in screen.connection_note.text()
        for _ in range(5):service.network_step()
        provider.refresh();screen.apply(provider.snapshot());state=provider.snapshot().live
        # The UI keeps only 256 recent events across all sessions. Audit the
        # durable, session-specific receipts rather than that bounded preview.
        from contextlib import closing
        from ..control_plane.sqlite_transaction import connect
        runtime=service.network_engine.technologies
        with closing(connect(runtime.journal_path)) as conn:
            rows=conn.execute('SELECT payload_json FROM technology_events WHERE topology_id=? AND session_id=? ORDER BY sequence',
                              (runtime.topology_id,'CHECK-TF')).fetchall()
        activation=[json.loads(row[0]) for row in rows]
        report['activation_receipts'] = _activation_receipt_summary(activation)
        checks['activation_runs_causal_pipeline'] = bool(report['activation_receipts']['completed_command_ids'])
        (out/'activation-receipts.json').write_text(json.dumps(activation,indent=2),encoding='utf-8')
        screen.grab().save(str(out/'connection-form.png'))
        screen.load_record(state.communicators['SAT-RIO-SATELLITE']);screen.tabs.setCurrentIndex(0)
        screen.editor_tabs.setCurrentIndex(screen.group_pages['orbit']._tab_index)
        screen.grab().save(str(out/'orbit-form.png'))
        geo=GeoMap();geo.resize(1280,900);net=build_net_model(provider.snapshot());geo.set_model(net);geo.show();app.processEvents()
        checks['only_endpoint_core_highlighted']=set(geo.label_sides(geo.transform()))=={n.id for n in net.located if n.network_role in ('endpoint','core')}
        geo.grab().save(str(out/'geo-labels.png'))
        geo.focus_node.setCurrentIndex(geo.focus_node.findData('SAT-RIO-SATELLITE'))
        QTest.qWait(900);app.processEvents();sat=state.satellites['SAT-RIO-SATELLITE']
        checks['satellite_selection_opens_globe']=geo.globe.isVisible() and geo.globe._timer.isActive()
        radius=geo.globe.radius();geo.zoom_in.click()
        checks['globe_zoom_controls_functional']=geo.globe.radius()>radius
        geo.zoom_out.click()
        checks['globe_zoom_can_restore_scale']=abs(geo.globe.radius()-radius)<1e-6
        checks['orbit_and_transmissions_are_backend_data']=len(sat['orbit_ecef_km'])==193 and len(geo.globe.overlay_geometry()[1])==len(sat['transmitting_links'])==2
        before=sat['ecef_km'];service.network_step();provider.refresh();geo.set_model(build_net_model(provider.snapshot(),selection=('node','SAT-RIO-SATELLITE')))
        checks['orbital_position_updates']=provider.snapshot().live.satellites['SAT-RIO-SATELLITE']['ecef_km']!=before
        geo.grab().save(str(out/'orbit-globe.png'));old=geo.globe;geo.reset_view()
        stopped=not old._timer.isActive() and old._base is None
        QTest.qWait(80)
        import shiboken6
        checks['return_to_map_stops_orbit_timer']=stopped and geo.globe is None and not shiboken6.isValid(old) and geo.resource_stats()['renderer_instances']==1
        from .geo_modes_validation import validate_geo_modes
        report['geo_modes']=validate_geo_modes(out/'geo-modes',build_net_model(provider.snapshot(), selection=('node','SAT-RIO-SATELLITE')))
        checks['geo_modes_satellites_interaction_and_resource_lifecycle']=report['geo_modes']['passed']
        checks['fresh_workspace_registration']=_fresh_registration(out,repository_root,app)
        from .communicator_forms_validation import validate_communicator_forms
        report['forms']=validate_communicator_forms(out/'forms',provider)
        checks['type_specific_forms_and_durable_save_workflows']=report['forms']['passed']
        from .satellite_bridge_validation import validate_satellite_bridge_view
        report['satellite_bridge']=validate_satellite_bridge_view(out/'satellite-bridge',provider)
        checks['live_satellite_bridge_and_floating_window']=report['satellite_bridge']['passed']
        from .global_globe_validation import validate_global_globe
        report['global_globe']=validate_global_globe(out/'global-globe',provider)
        checks['global_globe_satellite_priority_and_shared_evidence']=report['global_globe']['passed']
        report['live']=service.network_live_state();report['passed']=all(checks.values());report['check_count']=len(checks)
    except Exception as exc:report['error']=f'{type(exc).__name__}: {exc}'
    finally:
        if screen is not None:screen.shutdown();screen.close();screen.deleteLater()
        if geo is not None:geo.shutdown();geo.close();geo.deleteLater()
        provider.stop();app.processEvents()
    (out/'report.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    return report


def _fresh_registration(out,repository_root,app):
    from tempfile import mkdtemp
    from ..qkd.dashboard_api import AssuranceDashboardService
    from .screens.communicators import CommunicatorsScreen
    from .service_provider import ServiceProvider
    root=Path(mkdtemp(prefix='fresh-state-',dir=out))
    service=AssuranceDashboardService(repository_root=repository_root,control_journal_path=root/'journal.sqlite')
    provider=ServiceProvider(service,state_root=root/'provider');screen=CommunicatorsScreen()
    try:
        provider.refresh();screen.apply(provider.snapshot());screen.show();app.processEvents()
        for key,value in (('device_id','FIRST-COMM'),('node_id','FIRST-COMM'),('name','First registration')):screen.fields[key].setText(value)
        sent=[];screen.command.connect(lambda *args:sent.append(args));screen.save.click();app.processEvents()
        if not sent:return False
        provider.save_communicator(sent[-1][1]);screen.apply(provider.snapshot());app.processEvents()
        screen.grab().save(str(out/'fresh-registration.png'))
        result=provider.last_network_result
        return 'CONFIRMADO' in screen.message.text() and result['topology_version']==1 and result['physical_action_performed'] is False
    finally:
        screen.shutdown();screen.close();screen.deleteLater();provider.stop();app.processEvents()
