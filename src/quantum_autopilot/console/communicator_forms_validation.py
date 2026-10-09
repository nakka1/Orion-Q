"""Bundled regression for communicator forms using the actual isolated provider."""
import json
from pathlib import Path


def validate_communicator_forms(destination, provider):
    from PySide6.QtWidgets import QApplication
    from ..control_plane.communicators import TYPES, TYPE_TECHNOLOGIES
    from .screens.communicators import CommunicatorsScreen

    out=Path(destination);out.mkdir(parents=True,exist_ok=True)
    app=QApplication.instance();screen=CommunicatorsScreen();screen.resize(1280,900)
    screen.apply(provider.snapshot());screen.show();app.processEvents()
    sent=[];screen.command.connect(lambda *args:sent.append(args));checks={};clone=None
    report={'passed':False,'checks':checks,'physical_action_performed':False,
            'qt_platform':app.platformName(),'scope':'Qt form interactions and durable backend readback; isolated simulated inventory'}

    def kind(value):screen.fields['physical_type'].setCurrentIndex(screen.fields['physical_type'].findData(value))
    def name(value):
        for field in ('device_id','node_id','name'):screen.fields[field].setText(value)
    def visible(group):return screen.editor_tabs.isTabVisible(screen.group_pages[group]._tab_index)

    try:
        transitions=[]
        for target in (t for t in TYPES if t!='satellite'):
            screen.new_record();name('FORM-TYPE-CHECK');kind('satellite');kind(target)
            payload=screen.payload()
            transitions.append(not visible('orbit') and payload['orbit']=={}
                and set(payload['technologies'])==set(TYPE_TECHNOLOGIES[target])
                and visible('satellite_doppler')==(target=='ground_station'))
        checks['six_type_changes_remove_automatic_satellite_fields']=all(transitions)
        screen.new_record();name('FORM-TECH-CHECK');kind('measurement_station');screen.techs['tf_qkd'].setChecked(True)
        receiver=screen.payload();screen.group_fields['tf_qkd']['device_role'].setCurrentText('sender');sender=screen.payload()
        checks['manual_technologies_associate_correct_protocol_role']=(receiver['protocols']==['SNS-TF']
            and set(receiver['capabilities'])=={'interferometer','single_photon_detector'}
            and set(sender['capabilities'])=={'coherent_sender','phase_reference'})
        screen.new_record();name('FORM-LOCATION-CHECK');screen.fields['latitude'].setText('-22,9')
        before=len(sent);screen.save.click()
        checks['partial_coordinates_refused_without_zero_or_command']=len(sent)==before and 'latitude e longitude' in screen.message.text()
        screen.fields['longitude'].setText('-43,2');screen.group_fields['optical']['detector_efficiency'].setText('0,7')
        checks['decimal_comma_is_validated']=screen.payload()['location']['longitude']==-43.2 and screen.payload()['optical']['detector_efficiency']==.7
        screen.new_record();existing=provider.snapshot().live.communicators['CBPF'];name(existing['device_id']);screen.fields['node_id'].setText(existing['node_id'])
        before=len(sent);screen.save.click();screen.apply(provider.snapshot())
        checks['new_duplicate_cannot_overwrite_or_false_ack']=len(sent)==before and 'já cadastrado' in screen.message.text()
        screen.new_record();name('FORM-DURABLE-CHECK');kind('quantum_memory');screen.save.click()
        waiting=screen.pending;screen.apply(provider.snapshot())
        pending_safe=waiting=='FORM-DURABLE-CHECK' and screen.pending==waiting and not screen.save.isEnabled() and 'PENDENTE' in screen.message.text()
        state=screen.save_state();provider.save_communicator(sent[-1][1])
        clone=CommunicatorsScreen();clone.apply(provider.snapshot());clone.restore_state(state);clone.apply(provider.snapshot())
        checks['pending_save_survives_rebuild_and_acknowledges_readback']=pending_safe and clone.pending is None and 'CONFIRMADO' in clone.message.text()
        screen.apply(provider.snapshot());screen.session.setCurrentIndex(1);loaded=bool(screen.session_id.text()) and screen.channel_table.rowCount()>0
        screen.session.setCurrentIndex(0)
        checks['new_connection_does_not_reuse_old_session']=loaded and screen.session_id.text()=='' and screen.path.text()=='' and screen.channel_table.rowCount()==0
        screen.new_record();name('FORM-ORBIT-CHECK');kind('satellite');screen.group_fields['orbit']['compatible_stations'].setText('CBPF, UFRJ')
        screen.group_fields['orbit']['state_vectors'].setText('[');satellite=screen.payload()
        kind('installation');terrestrial=screen.payload()
        stream=screen.fields['mode'];stream_enabled=stream.model().item(stream.findData('measurement_stream')).isEnabled()
        checks['inactive_orbits_and_ogs_do_not_apply_to_terrestrial_form']=(satellite['orbit']['state_vectors']==[] and terrestrial['orbit']=={} and not stream_enabled)
        kind('quantum_memory');screen.editor_tabs.setCurrentIndex(screen.group_pages['quantum_memory']._tab_index)
        for size in ((640,480),(1280,900),(3440,1440)):
            screen.resize(*size);app.processEvents();screen.grab().save(str(out/f'memory-form-{size[0]}x{size[1]}.png'))
        screen.resize(1280,900);screen.editor_tabs.setCurrentIndex(0);app.processEvents();screen.grab().save(str(out/'terrestrial-form-no-orbit.png'))
        report['passed']=all(checks.values());report['check_count']=len(checks)
    except Exception as exc:report['error']=f'{type(exc).__name__}: {exc}'
    finally:
        for widget in (screen,clone):
            if widget is not None:widget.shutdown();widget.close();widget.deleteLater()
        app.processEvents()
    (out/'report.json').write_text(json.dumps(report,indent=2,ensure_ascii=False),encoding='utf-8')
    return report
