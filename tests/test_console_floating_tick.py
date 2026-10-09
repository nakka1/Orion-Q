"""Continuous received ticks yield presentation without losing the latest data.

Inputs are synthetic UI observations; they attest no physical transmission.
"""
from __future__ import annotations

import os
import json
from copy import deepcopy
from dataclasses import replace

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import pytest
pytest.importorskip('PySide6.QtWidgets')
from PySide6.QtCore import QCoreApplication, QEvent, QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QWidget

from quantum_autopilot.console import theme
from quantum_autopilot.console.widgets.inspector import CommandStatus
from quantum_autopilot.console.widgets.network_views import build_net_model
from quantum_autopilot.console.widgets.node_window import NodeWindow, NodeWindows
from tests.test_console_node_incremental import snapshot, wait_for
from tests.test_console_satellite_incremental import satellite_snapshot


def changing_snapshot(degree=24, *, tick=1):
    """Both identity/status and state/temperature change, with every link."""
    snap=snapshot(degree,tick=tick)
    live_node=replace(snap.live.nodes[0],status='degraded' if tick%2 else 'up')
    snap.live=replace(snap.live,nodes=(live_node,))
    return snap


@pytest.fixture
def panel():
    app=QApplication.instance() or QApplication([])
    previous=theme.current_theme();theme.set_theme('dark');theme.install(app)
    host=QWidget();host.resize(1200,800);host.show()
    registry=NodeWindows(host,incremental=True)
    created=[]
    def opened(degree=24):
        first=changing_snapshot(degree,tick=1);net=build_net_model(first)
        window=registry.open('HUB',prepare=lambda w:w.refresh(first,net,CommandStatus()))
        wait_for(lambda:window.presentation_status()['ready'],timeout=8000)
        window.chart.freeze(1.)
        created.append(window)
        return window
    yield app,host,registry,opened
    registry.shutdown();host.close();host.deleteLater()
    QCoreApplication.sendPostedEvents(None,QEvent.Type.DeferredDelete)
    app.processEvents();theme.set_theme(previous);theme.install(app)


def test_50ms_ticks_update_every_existing_link_without_starvation_or_rebuild(panel,monkeypatch):
    _app,host,_registry,opened=panel;window=opened()
    cards=dict(window._cards);legends=dict(window._legend)
    observed={key:[] for key in cards};gates=[]
    window.materialized.connect(lambda:gates.append(window._generation))
    for key,card in cards.items():
        original=card.show_link
        def updated(node_id,link,live_link,*,clock_s=None,ident=key,method=original):
            result=method(node_id,link,live_link,clock_s=clock_s)
            observed[ident].append(live_link.skr_bps)
            return result
        monkeypatch.setattr(card,'show_link',updated)
    initial_generation=window._generation
    for tick in range(2,22):
        latest=changing_snapshot(tick=tick);net=build_net_model(latest)
        assert window.refresh(latest,net,CommandStatus())
        progress=window.presentation_status()
        assert progress['latest_tick']==tick and not progress['ready']
        assert window._generation==initial_generation+tick-1
        assert window.presentation_snapshot() is None
        queued=[(kind,key) for kind,key,_index in window._pending_widgets]
        assert len(queued)==len(set(queued))
        assert len(queued)<=len(cards)+len(progress['expected_sections'])
        QTest.qWait(50)
    # A forever-changing core section must not jump ahead of existing link work.
    assert all(values for values in observed.values())
    assert all(values[-1]>124 for values in observed.values())
    assert all(window._cards[key] is card for key,card in cards.items())
    assert all(window._legend[key] is row for key,row in legends.items())
    assert not gates
    wait_for(lambda:window.presentation_status()['ready'],timeout=8000)
    assert window.presentation_snapshot()==(latest,net)
    assert all(values[-1]==123+21 for values in observed.values())
    reference=NodeWindow('HUB',host)
    try:
        assert reference.refresh(latest,net,CommandStatus())
        for key in ('identity','state','calibration','calibration_links','routes','legend','cards'):
            assert window.content_texts()[key]==reference.content_texts()[key]
        assert window.chart.series==reference.chart.series
        assert window.presentation_status()['completed_link_keys']==tuple(cards)
        assert window.presentation_status()['pending_items']==0 and not gates
    finally:reference.close();reference.deleteLater()


def test_existing_link_refresh_error_is_explicit_and_retries_latest_generation(panel,monkeypatch):
    _app,_host,_registry,opened=panel;window=opened(3)
    key=next(iter(window._cards));card=window._cards[key];original=card.show_link
    def failure(*_args,**_kwargs):raise ValueError('secret=must-never-be-disclosed')
    monkeypatch.setattr(card,'show_link',failure)
    failed=changing_snapshot(3,tick=2)
    window.refresh(failed,build_net_model(failed),CommandStatus())
    wait_for(lambda:window.presentation_status()['state']=='error')
    progress=window.presentation_status()
    assert not progress['ready'] and progress['presented_tick'] is None
    assert window.presentation_snapshot() is None and key not in progress['completed_link_keys']
    assert not window._presentation_timer.isActive()
    assert 'ValueError' in progress['error'] and key in progress['error']
    assert 'must-never-be-disclosed' not in str(progress)+str(window.content_texts())
    assert any(kind=='refresh' and ident==key for kind,ident,_index in window._pending_widgets)
    monkeypatch.setattr(card,'show_link',original)
    latest=changing_snapshot(3,tick=9);net=build_net_model(latest)
    window.refresh(latest,net,CommandStatus())
    wait_for(lambda:window.presentation_status()['ready'])
    assert window._cards[key] is card and window.presentation_snapshot()==(latest,net)
    assert window.presentation_status()['presented_tick']==9
    assert not window.presentation_status()['error']


def test_hidden_panel_accepts_latest_observation_without_widget_updates_until_show(panel,monkeypatch):
    app,_host,_registry,opened=panel;window=opened(4)
    window.hide();app.processEvents()
    before=window.content_texts();writes=[]
    tracked=[(window.chart,'set_series'),(window.marker,'set_node'),(window.chips,'set_chips'),
             (window.status,'set_status'),(window.presentation_line,'setText')]
    tracked.extend((window._core_widget(key),'set_rows') for key in window._expected_sections)
    tracked.extend((card,'show_link') for card in window._cards.values())
    for target,name in tracked:
        original=getattr(target,name)
        def record(*args,method=original,ident=name,**kwargs):
            writes.append(ident);return method(*args,**kwargs)
        monkeypatch.setattr(target,name,record)
    for tick in (2,3,8):
        latest=changing_snapshot(4,tick=tick);net=build_net_model(latest)
        window.refresh(latest,net,CommandStatus());app.processEvents()
        assert window.presentation_status()['latest_tick']==tick
        assert not window.presentation_status()['ready']
        assert not window._presentation_timer.isActive()
    assert not writes
    assert window.content_texts()==before
    assert window.presentation_status()['state']=='paused'
    window.show()
    wait_for(lambda:window.presentation_status()['ready'])
    assert writes and window.presentation_snapshot()==(latest,net)
    assert window.presentation_status()['presented_tick']==8


def test_satellite_existing_tables_and_stations_do_not_emit_new_control_gate_per_tick(panel):
    _app,_host,registry,_opened=panel;first=satellite_snapshot();gates=[]
    def prepare(window):
        window.materialized.connect(lambda:gates.append(window._generation))
        window.refresh(first,build_net_model(first),CommandStatus())
    window=registry.open('HUB',prepare=prepare)
    wait_for(lambda:window.presentation_status()['ready'])
    assert len(gates)==8  # Four newly built section tables and four stations.
    tables=dict(window.satellite_panel._tables);stations=dict(window.satellite_panel.stations)
    gates.clear()
    for tick in (2,3,9):
        latest=satellite_snapshot(tick=tick);net=build_net_model(latest)
        window.refresh(latest,net,CommandStatus())
        assert not window.presentation_status()['ready']
        wait_for(lambda:window.presentation_status()['ready'])
        assert window.presentation_snapshot()==(latest,net)
        assert all(window.satellite_panel._tables[key] is table for key,table in tables.items())
        assert all(window.satellite_panel.stations[key] is table for key,table in stations.items())
    assert not gates


def test_long_peer_and_link_ids_fit_384px_document_with_complete_source_and_reachable_actions(panel,tmp_path):
    app,host,registry,_opened=panel;host.resize(400,520)
    snap=changing_snapshot(3,tick=7)
    peers={f'PEER-{index}':f'PEER-{index}-'+('IDENTIFICADOR_INSTITUCIONAL_SEM_ABREVIACAO_'*5)
           for index in range(3)}
    links={f'FIBRE-{index}':f'FIBRE-{index}-'+('ENLACE_DECLARADO_ID_COMPLETO_'*6)
           for index in range(3)}
    for node in snap.topology.nodes:
        if node.id in peers:node.id=peers[node.id]
    for link in snap.topology.links:
        link.b=peers[link.b];link.id=links[link.id];link.configured_active=True
    snap.live=replace(snap.live,
        nodes=(replace(snap.live.nodes[0],links=tuple(links.values())),),
        links=tuple(replace(link,b=peers[link.b],id=links[link.id]) for link in snap.live.links))
    untouched=deepcopy(snap);net=build_net_model(snap)
    window=registry.open('HUB',prepare=lambda w:w.refresh(snap,net,CommandStatus()))
    wait_for(lambda:window.presentation_status()['ready'],timeout=8000)
    window.chart.freeze(1.)
    for _ in range(6):app.processEvents()
    assert window.width()==384
    viewport=window.scroll.viewport();document=window.scroll.widget()
    assert document.width()<=viewport.width()
    for card in window._cards.values():
        assert card.width()<=viewport.width()
        assert card.identity.value('Identificador do enlace')==card.key
        peer=net.link(card.key).b
        assert card.identity.value('Ponto conectado')==peer
        for row in card.identity.rows.values():
            label=row.value_label
            assert label.height()>=label.heightForWidth(label.contentsRect().width())
            assert label.full_text==label.text()==label.toolTip()
        assert card.peer.full_text.endswith(peer)
    last=list(window._cards.values())[-1]
    window.scroll.ensureWidgetVisible(last.toggle,0,0)
    for _ in range(6):app.processEvents()
    point=last.toggle.mapTo(viewport,QPoint(0,0))
    assert 0<=point.x()<=viewport.width()-last.toggle.width()
    assert 0<=point.y()<=viewport.height()-last.toggle.height()
    assert window.scroll.verticalScrollBar().value()>0
    actions=[];window.action.connect(lambda *args:actions.append(args))
    QTest.mouseClick(last.toggle,Qt.MouseButton.LeftButton)
    assert actions==[('set_link_active','link',last.key,{'active':False})]
    assert snap==untouched and window.presentation_snapshot()==(snap,net)
    geometry={'panel_width':window.width(),'panel_height':window.height(),
              'viewport_width':viewport.width(),'viewport_height':viewport.height(),
              'document_width':document.width(),'document_height':document.height(),
              'card_heights':[card.height() for card in window._cards.values()],
              'full_peer_lengths':[len(peer) for peer in peers.values()],
              'full_link_lengths':[len(link) for link in links.values()]}
    print(json.dumps(geometry))
    (tmp_path/'long-ids-geometry.json').write_text(json.dumps(geometry,indent=2),encoding='utf-8')


def test_source_and_physical_type_changes_hide_previous_core_until_latest_section_is_presented(panel):
    _app,host,_registry,opened=panel;window=opened(2)
    keys=('identity','state','calibration','calibration_links','routes')
    original_rows={key:dict(window._core_widget(key).rows) for key in keys}
    latest=snapshot(2,tick=9,source='fixture-B',kind='measured');net=build_net_model(latest)
    assert window.refresh(latest,net,CommandStatus())
    assert not window.presentation_status()['ready']
    assert window.presentation_status()['source_label']=='fixture-B'
    assert window.presentation_snapshot() is None
    # Equal field names do not make an old origin's values safe to display.
    assert all(window._core_widget(key).isHidden() for key in keys)
    wait_for(lambda:window.presentation_status()['ready'])
    assert window.presentation_snapshot()==(latest,net)
    assert all(window._core_widget(key).rows==rows for key,rows in original_rows.items())

    satellite=snapshot(2,tick=10,source='fixture-B',kind='measured')
    satellite.topology.nodes[0].physical_type='satellite'
    assert window.refresh(satellite,build_net_model(satellite),CommandStatus())
    wait_for(lambda:window.presentation_status()['ready'])
    assert window.presentation_status()['presentation_kind']=='satellite'

    latest=snapshot(2,tick=13,source='fixture-B',kind='measured');untouched=deepcopy(latest)
    net=build_net_model(latest)
    assert window.refresh(latest,net,CommandStatus())
    assert not window.presentation_status()['ready']
    # The mode switch must not reshow old calibration/routes while deferred.
    assert all(window._core_widget(key).isHidden() for key in keys)
    wait_for(lambda:window.presentation_status()['ready'])
    assert window.presentation_snapshot()==(latest,net) and latest==untouched
    reference=NodeWindow('HUB',host)
    try:
        assert reference.refresh(latest,net,CommandStatus())
        for key in keys:
            assert window.content_texts()[key]==reference.content_texts()[key]
    finally:reference.close();reference.deleteLater()


def test_three_visible_panels_coalesce_fast_ticks_with_bounded_independent_queues(panel):
    _app,host,registry,opened=panel;hub=opened(6)
    first,net=hub.presentation_snapshot();windows=[hub]
    for node_id in ('PEER-0','PEER-1'):
        window=registry.open(node_id,prepare=lambda w:w.refresh(first,net,CommandStatus()))
        windows.append(window)
    wait_for(lambda:all(window.presentation_status()['ready'] for window in windows),timeout=8000)
    for window in windows:window.chart.freeze(1.)
    retained={window.node_id:(dict(window._cards),dict(window._legend),window._generation)
              for window in windows}
    gates=[]
    for window in windows:
        window.materialized.connect(lambda ident=window.node_id:gates.append(ident))
    for tick in range(2,12):
        latest=changing_snapshot(6,tick=tick);untouched=deepcopy(latest);net=build_net_model(latest)
        for window in windows:
            assert window.refresh(latest,net,CommandStatus())
            progress=window.presentation_status()
            assert progress['latest_tick']==tick
            assert window._generation==retained[window.node_id][2]+tick-1
            queued=[(kind,key) for kind,key,_index in window._pending_widgets]
            assert len(queued)==len(set(queued))
            assert len(queued)<=len(progress['expected_link_keys'])+len(progress['expected_sections'])
        QTest.qWait(50)
        assert latest==untouched
    wait_for(lambda:all(window.presentation_status()['ready'] for window in windows),timeout=8000)
    assert not gates
    for window in windows:
        cards,legends,_generation=retained[window.node_id]
        assert all(window._cards[key] is card for key,card in cards.items())
        assert all(window._legend[key] is row for key,row in legends.items())
        assert window.presentation_snapshot()==(latest,net)
        assert window.presentation_status()['pending_items']==0
        reference=NodeWindow(window.node_id,host)
        try:
            assert reference.refresh(latest,net,CommandStatus())
            for key in ('identity','state','calibration','calibration_links','routes','legend','cards'):
                assert window.content_texts()[key]==reference.content_texts()[key]
        finally:reference.close();reference.deleteLater()


def test_same_card_readiness_recovery_requests_one_permission_gate_without_direct_enable(panel,monkeypatch):
    _app,_host,registry,_opened=panel
    first=changing_snapshot(1,tick=1);first.topology.links[0].configured_active=True
    net=build_net_model(first)
    window=registry.open('HUB',prepare=lambda w:w.refresh(first,net,CommandStatus()))
    wait_for(lambda:window.presentation_status()['ready']);window.chart.freeze(1.)
    key=next(iter(window._cards));card=window._cards[key];toggle=card.toggle
    assert toggle.property('ready') is True
    gates=[];enabled_requests=[];actions=[]
    window.materialized.connect(lambda:gates.append(
        (window._generation,toggle.property('ready'),toggle.isEnabled())))
    window.action.connect(lambda *args:actions.append(args))
    original_enabled=toggle.setEnabled
    def enabled(value):
        enabled_requests.append(value);original_enabled(value)
    monkeypatch.setattr(toggle,'setEnabled',enabled)

    for tick in (2,3):
        unknown=changing_snapshot(1,tick=tick);net=build_net_model(unknown)
        assert net.link(key).configured_active is None
        window.refresh(unknown,net,CommandStatus())
        wait_for(lambda:window.presentation_status()['ready'])
        assert window._cards[key] is card and toggle.property('ready') is False
        assert not toggle.isEnabled() and not gates
    assert enabled_requests==[False,False]
    enabled_requests.clear()

    latest=changing_snapshot(1,tick=4);latest.topology.links[0].configured_active=True
    net=build_net_model(latest);window.refresh(latest,net,CommandStatus())
    wait_for(lambda:window.presentation_status()['ready'])
    recovered_generation=window._generation
    assert window._cards[key] is card
    assert gates==[(recovered_generation,True,False)]
    assert toggle.property('ready') is True and not toggle.isEnabled()
    assert not enabled_requests
    QTest.mouseClick(toggle,Qt.MouseButton.LeftButton)
    assert not actions
    for tick in (5,6):
        latest=changing_snapshot(1,tick=tick);latest.topology.links[0].configured_active=True
        untouched=deepcopy(latest);net=build_net_model(latest)
        window.refresh(latest,net,CommandStatus())
        wait_for(lambda:window.presentation_status()['ready'])
        assert gates==[(recovered_generation,True,False)]
        assert not enabled_requests and not toggle.isEnabled()
        assert window._cards[key] is card and window.presentation_snapshot()==(latest,net)
        assert latest==untouched

    # Only the separate permission policy may enable this backend operation.
    toggle.setEnabled(True)
    assert enabled_requests==[True] and toggle.isEnabled()
    QTest.mouseClick(toggle,Qt.MouseButton.LeftButton)
    assert actions==[('set_link_active','link',key,{'active':False})]
