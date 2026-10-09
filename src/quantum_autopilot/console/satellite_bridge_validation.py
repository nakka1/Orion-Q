"""Operational satellite bridge and floating-window regression, source or frozen."""
import json
import sqlite3
from pathlib import Path


def validate_satellite_bridge_view(destination,provider):
    from PySide6.QtCore import QEvent, QCoreApplication, QPoint, QPointF, Qt
    from PySide6.QtGui import QWheelEvent
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication, QLabel
    from .responsive_validation import wait_for_node_presentation
    from .screens.topology import TopologyScreen, NODE_LEGEND
    from .widgets.geo_view import GLOBE_TO_MAP_ZOOM

    app=QApplication.instance();out=Path(destination);out.mkdir(parents=True,exist_ok=True)
    checks={};screen=TopologyScreen();screen.resize(1280,900);screen.show();app.processEvents()
    report={'schema':'orion.satellite-bridge-validation.v1','passed':False,'checks':checks,
            'qt_platform':app.platformName(),'physical_action_performed':False,
            'scope':'causal simulated OGS operations and actual Qt interactions; experimental MEO assumptions'}
    report['presentation_waits']=[]
    def presented(window,phase):
        evidence=wait_for_node_presentation(window,screen._snap,screen._net,timeout_ms=5000)
        report['presentation_waits'].append({'node_id':window.node_id,'phase':phase,**evidence})
        return evidence
    def refresh(steps=0):
        for _ in range(steps):provider.service.network_step()
        provider.refresh();screen.apply(provider.snapshot());app.processEvents()
        for opened in tuple(screen.node_windows.windows.values()):presented(opened,'refresh')
        return provider.snapshot().live.satellites['SAT-ATLANTIC-MEO']
    try:
        sat=refresh(5)
        bridge=sat['bridges'][0];ident=bridge['session_id'];metrics=bridge['metrics']
        checks['two_networks_actual_protocol_receipts']=bridge['endpoints']==['UFRJ','BSC'] and bridge['status']=='observed_pairs' and metrics.get('coincidences',0)>0
        checks['received_clicks_and_separate_channels']=len(metrics.get('tdc_summary',[]))==2 and all(
            c['classical']['channel_id']!=c['quantum']['channel_id'] and c['association_id'] for c in bridge['connections'])
        checks['leo_comparison_preserved']=not provider.snapshot().live.technologies['sessions']['ATLANTIC-SATELLITE']['metrics'].get('available',False)
        with sqlite3.connect(provider.service.control_journal_path) as connection:
            records=[json.loads(row[0]) for row in connection.execute('SELECT payload_json FROM technology_events WHERE session_id=?',(ident,))]
        events={r['event'] for r in records}
        checks['acquisition_validation_protocol_and_classical_receipt']={'acquired','validated','protocol_processed','classical_received','metrics_computed'}<=events
        screen.geo.set_mode('globe');app.processEvents();QCoreApplication.sendPostedEvents(None,QEvent.Type.DeferredDelete);app.processEvents();QTest.qWait(30)
        globe=screen.geo.globe
        marker=next(i for i in globe.orbital_items() if i['id']=='SAT-ATLANTIC-MEO')
        QTest.mouseClick(globe,Qt.MouseButton.LeftButton,pos=marker['centre'].toPoint());app.processEvents()
        window=screen.node_windows.windows['SAT-ATLANTIC-MEO'];presented(window,'open')
        panel=window.satellite_panel
        checks['floating_window_is_satellite_specific']=panel.isVisible() and not window.chart.isVisible() and not window.recalibrate.isVisible() and not window.locate_button.isVisible()
        checks['satellite_inspector_uses_orbital_device_controls']=('Satélite' in screen.inspector.node_panel.role.text()
            and not screen.inspector.node_panel.recalibrate.isVisible() and not screen.inspector.node_panel.locate.isVisible())
        checks['live_bridge_and_station_metrics_displayed']=(
            'Pares entre redes' in panel.bridge.texts()['Estado da ponte'] and set(panel.stations)=={c['link_id'] for c in bridge['connections']})
        before=panel.orbital.texts()['Posição ECEF · km'];latest=refresh(3)
        checks['received_orbit_updates_floating_window']=panel.orbital.texts()['Posição ECEF · km']!=before
        window.scroll.verticalScrollBar().setValue(window.scroll.verticalScrollBar().maximum());app.processEvents()
        QTest.mouseClick(panel.details,Qt.MouseButton.LeftButton);app.processEvents()
        checks['satellite_scrolling_reaches_live_station_details']=not panel.details.visibleRegion().isEmpty() and not panel.stations[bridge['connections'][-1]['link_id']].visibleRegion().isEmpty()
        checks['detailed_science_receives_shared_snapshot']=window._satellite_dialog.isVisible() and window._satellite_dialog._metrics==latest['bridges'][0]['metrics']
        window._satellite_dialog.close();screen.grab().save(str(out/'satellite-live-window.png'))
        window.scroll.verticalScrollBar().setValue(0);app.processEvents();screen.grab().save(str(out/'satellite-orbit-window.png'))
        screen.node_windows.close_all();screen.grab().save(str(out/'bridge-and-legend.png'))
        point=None;zoom_steps=0;pan_steps=0;pick_attempts=[]
        for attempt in range(5):
            # Always use the active renderer. Zoom buttons legitimately change
            # Globe to Map at the threshold; retired geometry is not clickable.
            globe=screen.geo.globe
            if globe is None:break
            item=next(i for i in globe.connection_geometry() if i[0].key==bridge['connections'][0]['link_id'])
            line,a,b,visible=item
            # A short MEO downlink can have only a few pixels between the
            # satellite's 48 px hit target and the ground markers. Sparse
            # seven-point sampling misses those pixels on smaller viewports.
            candidates=[a+(b-a)*step/100 for step in range(5,96)] if visible else []
            hits=[screen.geo.hit(candidate.toPoint()) for candidate in candidates]
            point=next((candidate for candidate,hit in zip(candidates,hits) if hit==('link',line.key)),None)
            pick_attempts.append({'mode':screen.geo.mode,'zoom':globe._zoom,'visible':visible,
                'camera':list(globe.camera_state()),
                'viewport':[globe.width(),globe.height()], 'candidate_count':len(candidates),
                'target_hits':sum(hit==('link',line.key) for hit in hits)})
            if point is not None:break
            if attempt==4:break
            if attempt==3:
                # Looking nearly along a downlink can project the entire line
                # underneath the prioritised satellite hit targets. Rotate the
                # camera through actual mouse events rather than shrinking the
                # targets or inventing different orbital coordinates.
                start=QPoint(round(globe.width()*.8),round(globe.height()*.5))
                end=start-QPoint(50,0)
                QTest.mousePress(globe,Qt.MouseButton.LeftButton,pos=start)
                QTest.mouseMove(globe,end);app.processEvents()
                QTest.mouseRelease(globe,Qt.MouseButton.LeftButton,pos=end)
                app.processEvents();pan_steps+=1
                continue
            if globe._zoom*1.5<GLOBE_TO_MAP_ZOOM:
                screen.geo.zoom_in.click()
            else:
                # A real wheel step refines the zoom below the mode threshold.
                # Do not bypass the controller or keep clicking a retired view.
                anchor=QPointF(globe.rect().center())
                wheel=QWheelEvent(anchor,QPointF(globe.mapToGlobal(anchor.toPoint())),QPoint(),QPoint(0,120),
                    Qt.MouseButton.NoButton,Qt.KeyboardModifier.NoModifier,Qt.ScrollPhase.NoScrollPhase,False)
                QCoreApplication.sendEvent(globe,wheel)
            app.processEvents();zoom_steps+=1
        if point is not None:QTest.mouseClick(globe,Qt.MouseButton.LeftButton,pos=point.toPoint());app.processEvents()
        report['link_inspection']={'target':line.key,'clickable_point_found':point is not None,
            'selected':screen.inspector.link_panel.link_key,'role':screen.inspector.link_panel.role.text(),'zoom_steps':zoom_steps,
            'pan_steps':pan_steps,'attempts':pick_attempts}
        readback=screen.inspector.link_panel.kv.texts()
        received=provider.snapshot().live.link(line.id)
        joint=(received.provenance or {}).get('protocol_metrics',{}) if received is not None else {}
        expected_range=f'{received.distance_km:.3f} km' if received is not None and received.distance_km is not None else None
        checks['orbital_link_selection_shows_channels_and_joint_metrics']=(point is not None
            and screen.inspector.link_panel.link_key==line.key and 'Enlace satélite' in screen.inspector.link_panel.role.text()
            and readback.get('Canal clássico')==bridge['connections'][0]['classical']['channel_id']
            and readback.get('Payload quântico')==bridge['connections'][0]['quantum']['channel_id']
            and readback.get('Associação')==bridge['connections'][0]['association_id']
            and readback.get('Coincidências conjuntas')==str(joint.get('coincidences'))
            and readback.get('Distância oblíqua')==expected_range)
        report['link_inspection']['readback']=readback
        report['link_inspection']['received_range_km']=received.distance_km if received is not None else None
        screen.grab().save(str(out/'orbital-link-inspection.png'))
        legend_texts={w.text().casefold() for w in screen.legend.findChildren(QLabel)}
        checks['component_legend_present']=all(label.casefold() in legend_texts for _,label,_ in NODE_LEGEND)
        provider.configure_technology(json.dumps({'session_id':ident,'scenario':'unavailable'}));blocked=refresh(5)
        report['blocked_state']={'status':blocked['bridges'][0]['status'],'reason':blocked['bridges'][0]['reason'],
                                 'transmitting_links':blocked['transmitting_links']}
        checks['blocked_link_removes_emissions_but_keeps_associations']=not blocked['transmitting_links'] and blocked['bridges'][0]['status']=='blocked' and len(blocked['bridges'][0]['connections'])==2
        associated={c['link_id'] for c in blocked['bridges'][0]['connections']}
        checks['blocked_links_are_configured_dotted_lines']=associated<={l.key for l,*_ in screen.geo.globe.connection_geometry()} and all(screen._net.link(k).style=='space_config' for k in associated)
        provider.configure_technology(json.dumps({'session_id':ident,'scenario':'nominal'}));recovered=refresh(5)
        checks['recovery_requires_new_received_operations']=recovered['bridges'][0]['status']=='observed_pairs' and len(recovered['transmitting_links'])==2 and recovered['bridges'][0]['last_observed_at_s']>bridge['last_observed_at_s']
        checks['no_certified_keys_or_physical_action']=recovered['bridges'][0]['metrics']['certified_secret_bits']==0 and provider.snapshot().live.technologies['physical_actuation'] is False
        report['nominal_metrics']=metrics;report['blocked_metrics']=blocked['bridges'][0]['metrics'];report['recovery_metrics']=recovered['bridges'][0]['metrics']
        report['passed']=all(checks.values());report['check_count']=len(checks)
    except Exception as exc:report['error']=f'{type(exc).__name__}: {exc}'
    finally:screen.shutdown();screen.close();screen.deleteLater();app.processEvents()
    (out/'report.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    return report
