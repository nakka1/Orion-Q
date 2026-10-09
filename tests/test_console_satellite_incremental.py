"""All 211 satellite fixture fields are staged, never replaced with ready metadata.

The inputs are explicitly synthetic UI observations and attest no transmission.
"""
from __future__ import annotations

import os
from copy import deepcopy
from dataclasses import replace

import pytest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
pytest.importorskip('PySide6.QtWidgets')

from PySide6.QtCore import QCoreApplication, QEvent, QTimer  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402

from quantum_autopilot.console import theme  # noqa: E402
from quantum_autopilot.console.widgets.inspector import CommandStatus  # noqa: E402
from quantum_autopilot.console.widgets.metric_table import MetricTable  # noqa: E402
from quantum_autopilot.console.widgets.network_views import build_net_model  # noqa: E402
from quantum_autopilot.console.widgets.node_window import NodeWindow, NodeWindows  # noqa: E402
from quantum_autopilot.console.widgets.satellite_telemetry import SatelliteLivePanel  # noqa: E402
from tests.test_console_node_incremental import snapshot, wait_for  # noqa: E402


def satellite_snapshot(*, tick=1, origin='simulated UI observations · source A', stations=4):
    snap = snapshot(4, tick=tick, source=origin)
    snap.topology.nodes[0].physical_type = 'satellite'
    bridges=[]
    for index in range(10):
        geometry=[{'visible':True, 'elevation_deg':30+tick, 'azimuth_deg':17+index,
                   'range_km':38000+tick, 'carrier_doppler_ghz':.003+tick/1000,
                   'point_ahead_urad':2.5, 'link_budget_db':{'total':66+index}}
                  for _ in range(stations)]
        connections=[{'link_id':f'FIBRE-{i}', 'station_id':f'PEER-{i}', 'endpoint_kind':'ground',
            'association_id':f'SAT-OGS-{i}', 'classical':{'channel_id':f'C-{i}', 'protocol':'RF', 'enabled':True},
            'quantum':{'channel_id':f'Q-{i}', 'protocol':'BBM92', 'enabled':True}}
            for i in range(stations)]
        bridges.append({'session_id':f'BRIDGE-{index}', 'path':['PEER-0','HUB','PEER-1'],
            'endpoints':[f'PEER-{i}' for i in range(stations)], 'protocol':'BBM92',
            'technologies':['satellite_doppler'], 'status':'observed_pairs', 'origin':origin,
            'metrics':{'coincidences':3+tick, 'pair_rate_hz':7+tick/10, 'fidelity':.93+tick/10000,
                       'qber':.01+tick/10000, 'sifted_bits':19+tick, 'geometry':geometry,
                       'observation_source_id':'SAT-UI-FIXTURE',
                       'finite_key':{'abort_reason':'identified fixture; no usable key'}},
            'connections':connections})
    state={'device_id':'HUB', 'node_id':'HUB', 'origin':origin, 'orbital_class':'GEO',
        'model':'configured synthetic orbit', 'clock_s':tick/20, 'at_network_s':tick/20,
        'altitude_km':35786., 'ecef_km':[42164.,0.,0.], 'velocity_ecef_km_s':[0.,0.,0.],
        'period_s':86164.1, 'housekeeping':{'at_s':tick/20, 'valid_until_s':tick/20+1},
        'housekeeping_valid':True, 'bridges':bridges, 'isl_links':[
            {'link_id':f'ISL-{i}', 'peer_id':f'SAT-{i}', 'state':'available',
             'origin':origin, 'at_s':tick/20, 'geometry':{'visible':True,'range_km':12000+tick},
             'classical':{'channel_id':f'IC-{i}', 'protocol':'RF', 'enabled':True},
             'quantum':{'channel_id':f'IQ-{i}', 'protocol':'entanglement', 'enabled':True}}
            for i in range(3)], 'global_demands':[
            {'id':f'DEMAND-{i}', 'state':'waiting', 'operation':'entanglement',
             'path':['PEER-0','HUB','PEER-1'], 'reason':'synthetic UI record'} for i in range(11)],
        'transmitting_links':[{'link_id':f'FIBRE-{i}'} for i in range(stations)]}
    snap.live = replace(snap.live, satellites={'HUB':state},
                        communicators={'HUB':{'name':'Identified satellite fixture'}})
    return snap


@pytest.fixture(params=['dark','light'])
def gui(request):
    app=QApplication.instance() or QApplication([])
    theme.set_theme(request.param);theme.install(app)
    host=QWidget();host.resize(1200,800);host.show()
    yield app,host
    host.close();host.deleteLater()
    QCoreApplication.sendPostedEvents(None,QEvent.Type.DeferredDelete)
    theme.set_theme('dark');theme.install(app)


def test_default_satellite_panel_remains_complete_synchronous_211_fields(gui):
    _app,host=gui;snap=satellite_snapshot()
    panel=SatelliteLivePanel(host)
    panel.set_state(snap.live.satellites['HUB'],snap.live.communicators['HUB'])
    sections,stations=panel.content_texts()
    assert sum(map(len,sections.values()))+sum(map(len,stations.values()))==211
    assert len(panel.presentation_keys())==8
    assert not panel.findChildren(QTimer)
    assert 'simulated UI observations' in panel.summary.text()
    assert all('Chave certificada' not in key or val.startswith('0')
               for key,val in sections['bridge'].items())


def test_incremental_satellite_constructs_one_table_per_timer_and_full_current_photo(gui):
    _app,host=gui;snap=satellite_snapshot();net=build_net_model(snap)
    registry=NodeWindows(host,incremental=True)
    window=registry.open('HUB',prepare=lambda w:w.refresh(snap,net,CommandStatus()))
    assert not window.satellite_panel.findChildren(MetricTable)
    assert not window.content_texts()['satellite']
    status=window.presentation_status()
    assert not status['ready'] and status['pending_items']==8
    assert status['expected_sections']==('orbital','bridge','isl','operations',
                                        'station:FIBRE-0','station:FIBRE-1','station:FIBRE-2','station:FIBRE-3')
    assert not status['completed_sections'] and window.presentation_snapshot() is None
    assert not window.satellite_panel.findChildren(QTimer)
    counts=[]
    window.materialized.connect(lambda:counts.append(len(window.satellite_panel.findChildren(MetricTable))))
    wait_for(lambda:window.presentation_status()['ready'])
    assert counts==list(range(1,9))
    assert window.presentation_status()['completed_sections']==status['expected_sections']
    assert window.presentation_snapshot()==(snap,net)
    assert window.connection_texts()=={} and not window.chart.series
    reference=NodeWindow('HUB',host);reference.refresh(snap,net,CommandStatus())
    assert window.content_texts()['satellite']==reference.content_texts()['satellite']
    assert window.content_texts()['stations']==reference.content_texts()['stations']
    sections,stations=window.satellite_panel.content_texts()
    assert sum(map(len,sections.values()))+sum(map(len,stations.values()))==211
    registry.shutdown()


def test_new_satellite_ticks_preserve_pending_order_and_truthful_previous_labels(gui):
    _app,host=gui;snap=satellite_snapshot();registry=NodeWindows(host,incremental=True)
    window=registry.open('HUB',prepare=lambda w:w.refresh(snap,build_net_model(snap),CommandStatus()))
    wait_for(lambda:len(window.presentation_status()['completed_sections'])>=2)
    pending=tuple(window._pending_widgets)
    first_tables=dict(window.satellite_panel._tables)
    latest=satellite_snapshot(tick=9);net=build_net_model(latest)
    window.refresh(latest,net,CommandStatus())
    assert tuple(window._pending_widgets)[:len(pending)]==pending
    assert window.presentation_snapshot() is None
    assert 'atualização pendente' in window.satellite_panel._table_labels['orbital'].text()
    assert '0.050 s' in window.satellite_panel._table_labels['orbital'].text()
    assert window.satellite_panel.orbital.value('Tempo orbital')=='0.050 s'
    wait_for(lambda:window.presentation_status()['ready'])
    assert all(window.satellite_panel._tables[key] is table for key,table in first_tables.items())
    assert window.satellite_panel.orbital.value('Tempo orbital')=='0.450 s'
    assert 'atualização pendente' not in window.satellite_panel._table_labels['orbital'].text()
    assert window.presentation_snapshot()[0] is latest
    assert window.presentation_status()['pending']==window.presentation_status()['pending_items']==0
    registry.shutdown()


def test_satellite_source_change_and_station_removal_cancel_old_presentations(gui):
    _app,host=gui;snap=satellite_snapshot();registry=NodeWindows(host,incremental=True)
    window=registry.open('HUB',prepare=lambda w:w.refresh(snap,build_net_model(snap),CommandStatus()))
    wait_for(lambda:window.presentation_status()['ready'])
    old_frames={key:frame for key,(frame,_label) in window.satellite_panel._station_frames.items()}
    latest=satellite_snapshot(tick=4,origin='simulated UI observations · source B',stations=2)
    window.refresh(latest,build_net_model(latest),CommandStatus())
    assert window.presentation_snapshot() is None
    assert all(table.isHidden() for table in window.satellite_panel._tables.values())
    assert old_frames['FIBRE-3'].isHidden()
    wait_for(lambda:window.presentation_status()['ready'])
    assert set(window.satellite_panel.stations)=={'FIBRE-0','FIBRE-1'}
    assert 'source B' in window.satellite_panel.summary.text()
    assert window.presentation_snapshot()[0] is latest
    registry.close('HUB')
    assert window.presentation_status()['pending']==window.presentation_status()['pending_items']==0
    assert not window._presentation_timer.isActive()


def test_satellite_section_error_preserves_queue_and_recovers_without_secrets(gui,monkeypatch):
    _app,host=gui;snap=satellite_snapshot();registry=NodeWindows(host,incremental=True)
    window=registry.open('HUB',prepare=lambda w:w.refresh(snap,build_net_model(snap),CommandStatus()))
    original=window.satellite_panel.present_section
    def failure(key):
        if key=='bridge':raise ValueError('secret=never-log-me')
        original(key)
    monkeypatch.setattr(window.satellite_panel,'present_section',failure)
    wait_for(lambda:window.presentation_status()['state']=='error')
    status=window.presentation_status()
    assert status['completed_sections']==('orbital',) and status['pending_items']==7
    assert not status['ready'] and status['pending']==0 and window.presentation_snapshot() is None
    assert 'never-log-me' not in str(status)+str(window.content_texts())
    monkeypatch.setattr(window.satellite_panel,'present_section',original)
    latest=satellite_snapshot(tick=3);window.refresh(latest,build_net_model(latest),CommandStatus())
    wait_for(lambda:window.presentation_status()['ready'])
    assert window.presentation_snapshot()[0] is latest
    assert not window.presentation_status()['error']
    registry.shutdown()


def test_satellite_publication_headings_wrap_inside_floating_panel(gui):
    app,host=gui;snap=satellite_snapshot()
    state=deepcopy(snap.live.satellites['HUB'])
    state['origin']='Observações recebidas da estação terrestre · origem completa e validade verificável '
    panel=SatelliteLivePanel(host);panel.resize(518,610)
    panel.set_state(state,snap.live.communicators['HUB']);panel.show();app.processEvents()
    try:
        headings=[*panel._table_labels.values(),
                  *(label for _frame,label in panel._station_frames.values())]
        assert all(label.wordWrap() for label in headings)
        assert all(label.minimumSizeHint().width()<518 for label in headings)
        assert panel.width()==518
        assert all(state['origin'] in label.text() for label in headings)
    finally:panel.close();panel.deleteLater()


def test_satellite_equal_sections_preserve_layout_and_latest_stamps(gui,monkeypatch):
    _app,host=gui;snap=satellite_snapshot()
    panel=SatelliteLivePanel(host)
    panel.set_state(snap.live.satellites['HUB'],snap.live.communicators['HUB'])
    tables={**panel._tables,**{'station:'+key:table for key,table in panel.stations.items()}}
    writes=[];moves=[]
    for key,table in tables.items():
        original=table.set_rows
        def record(rows,*,ident=key,method=original):
            writes.append(ident);return method(rows)
        monkeypatch.setattr(table,'set_rows',record)
    original_insert=panel.station_lay.insertWidget
    def moved(*args):
        moves.append(args);return original_insert(*args)
    monkeypatch.setattr(panel.station_lay,'insertWidget',moved)
    try:
        state=deepcopy(snap.live.satellites['HUB']);state['clock_s']=.15
        panel.set_state(state,snap.live.communicators['HUB'])
        # Only the computed housekeeping age changed; unchanged sections retain
        # their table and layout, but truthfully name the latest publication.
        assert writes==['orbital'] and not moves
        assert all(revision==panel._revision for revision in panel._section_revisions.values())
        assert all(stamp[0]==.15 for stamp in panel._section_stamps.values())
        assert all('0.150 s' in label.text() for label in panel._table_labels.values())
        writes.clear()
        panel.set_state(deepcopy(state),snap.live.communicators['HUB'])
        assert not writes and not moves
        state['altitude_km']=35790.
        panel.set_state(state,snap.live.communicators['HUB'])
        assert writes==['orbital']
        assert panel.orbital.value('Altitude')=='35790.00 km'
        assert all(tables[key] is panel._tables[key] for key in panel._tables)
    finally:panel.close();panel.deleteLater()


def test_satellite_station_layout_tracks_reorder_and_removal(gui):
    _app,host=gui;snap=satellite_snapshot()
    panel=SatelliteLivePanel(host);panel.set_state(snap.live.satellites['HUB'],snap.live.communicators['HUB'])
    frames=dict(panel._station_frames)
    state=deepcopy(snap.live.satellites['HUB'])
    for bridge in state['bridges']:bridge['connections'].reverse()
    try:
        panel.set_state(state,snap.live.communicators['HUB'])
        assert panel._station_keys==('FIBRE-3','FIBRE-2','FIBRE-1','FIBRE-0')
        assert all(panel.station_lay.itemAt(index).widget() is frames[key][0]
                   for index,key in enumerate(panel._station_keys))
        for bridge in state['bridges']:
            bridge['connections']=[item for item in bridge['connections'] if item['link_id']!='FIBRE-2']
        panel.set_state(state,snap.live.communicators['HUB'])
        assert panel.station_lay.count()==3 and frames['FIBRE-2'][0].isHidden()
        assert all(panel.station_lay.itemAt(index).widget() is frames[key][0]
                   for index,key in enumerate(panel._station_keys))
        assert 'station:FIBRE-2' not in panel._section_contents
    finally:panel.close();panel.deleteLater()


def test_satellite_section_diff_refreshes_theme_and_retries_failed_update(gui,monkeypatch):
    _app,host=gui;snap=satellite_snapshot()
    panel=SatelliteLivePanel(host);state=deepcopy(snap.live.satellites['HUB'])
    panel.set_state(state,snap.live.communicators['HUB'])
    table=panel._tables['orbital'];original=table.set_rows;writes=[]
    def record(rows):
        writes.append(rows);return original(rows)
    monkeypatch.setattr(table,'set_rows',record)
    previous=theme.current_theme()
    try:
        theme.set_theme('light' if previous=='dark' else 'dark')
        panel.set_state(state,snap.live.communicators['HUB'])
        assert len(writes)==1
        before=panel._section_contents['orbital']
        state['altitude_km']=35800.
        def failure(_rows):raise ValueError('synthetic table failure')
        monkeypatch.setattr(table,'set_rows',failure)
        with pytest.raises(ValueError,match='synthetic table failure'):
            panel.set_state(state,snap.live.communicators['HUB'])
        assert table.isHidden() and panel._section_contents['orbital']==before
        monkeypatch.setattr(table,'set_rows',record)
        panel.present_section('orbital')
        assert not table.isHidden() and table.value('Altitude')=='35800.00 km'
        assert panel._section_contents['orbital']!=before
    finally:
        theme.set_theme(previous);panel.close();panel.deleteLater()
