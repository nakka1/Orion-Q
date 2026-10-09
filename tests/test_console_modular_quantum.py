"""Backend-driven module controls, stable evidence reading and global geography."""
import json
import os
from pathlib import Path
import time

os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import pytest
from PySide6.QtWidgets import QApplication

from quantum_autopilot.console.geodata import load_campus, load_world
from quantum_autopilot.console.screens.topology import TopologyScreen
from quantum_autopilot.console.service_provider import ServiceProvider
from quantum_autopilot.network_engine.institutional import PRESET_ID
from quantum_autopilot.qkd.dashboard_api import demo_service

pytestmark=pytest.mark.unit


@pytest.fixture(scope='module')
def app():
    from quantum_autopilot.console.theme import install
    app=QApplication.instance() or QApplication([]); install(app);return app


@pytest.fixture
def screen(tmp_path,app):
    service=demo_service(Path(__file__).resolve().parents[1],tmp_path,preset=PRESET_ID,scenario='quiet')
    for _ in range(3):service.network_step()
    provider=ServiceProvider(service,state_root=tmp_path/'provider');provider.refresh()
    screen=TopologyScreen();screen.resize(1200,900);screen.apply(provider.snapshot());screen.show();app.processEvents()
    yield screen,provider
    screen.shutdown();screen.close();provider.stop();app.processEvents()


def test_world_and_verifiable_campus_detail(screen,app):
    window,provider=screen
    assert window.geo.basemap is load_world()
    expected={link.id for link in window._net.links if all(window._net.node(x).located for x in (link.a,link.b))}
    assert window.geo.drawn_link_keys()==expected
    window.geo.focus_node.setCurrentIndex(window.geo.focus_node.findData('PUC-RIO'));app.processEvents()
    assert window.geo.basemap is load_campus('PUC-RIO')
    assert window.geo.basemap.municipality and window.geo.basemap.meta['footprint_verified']
    window.geo.focus_node.setCurrentIndex(window.geo.focus_node.findData('GOOGLE-QAI'));app.processEvents()
    assert not window.geo.basemap.municipality
    assert 'approximate' in window.geo.basemap.meta['reference']['coordinate_precision']


def test_backend_readback_and_capture_remain_readable(screen,app):
    window,provider=screen;panel=window.technology_panel;sent=[]
    panel.command.connect(lambda *args:sent.append(args));panel.open();app.processEvents()
    dialog=panel.dialog
    assert dialog.table.rowCount()==len(provider.snapshot().live.technologies['sessions'])
    row=dialog._ids.index('RIO-TF');dialog.table.selectRow(row);app.processEvents()
    dialog.parameters.setPlainText('{"block_s":0.5,"timeout_s":3.0,"signal_mu":0.35}')
    dialog.apply.click();assert sent[-1][0]=='configure_technology'
    provider.configure_technology(sent[-1][1]);window.apply(provider.snapshot());app.processEvents()
    assert json.loads(dialog.parameters.toPlainText())['signal_mu']==.35
    assert provider.snapshot().live.technologies['sessions']['RIO-TF']['config']['parameters']['signal_mu']==.35
    dialog._capture();text=dialog.evidence.toPlainText();bar=dialog.evidence.verticalScrollBar()
    bar.setValue(bar.maximum()//2);position=bar.value()
    provider.service.network_step();provider.refresh();window.apply(provider.snapshot());app.processEvents()
    assert dialog.evidence.toPlainText()==text and bar.value()==position
    # Backend readback must not overwrite unsent operator edits at every tick.
    dialog.parameters.setPlainText('{"draft":true}')
    provider.service.network_step();provider.refresh();window.apply(provider.snapshot());app.processEvents()
    assert dialog.parameters.toPlainText()=='{"draft":true}'


def test_node_connection_inspection_has_paired_channels_and_protocol_metrics(screen,app):
    window,provider=screen
    window.geo.node_selected.emit('PUC-RIO');app.processEvents()
    inspection=window.node_windows.windows['PUC-RIO']
    # Topology opens incremental windows: inspecting one Qt turn after the
    # click can legitimately precede creation of the protocol connection cards.
    deadline=time.monotonic()+5.
    while time.monotonic()<deadline and not inspection.presentation_status()['ready']:
        app.processEvents()
        time.sleep(.005)
    status=inspection.presentation_status()
    assert status['ready'],status
    assert status['presented_tick']==provider.snapshot().live.tick
    assert status['completed_sections']==status['expected_sections']
    assert status['completed_link_keys']==status['expected_link_keys']
    cards=inspection.connection_texts()
    tf=cards['TECH-RIO-TF-01']
    assert tf['Session']=='RIO-TF' and tf['Input origin']=='simulated'
    assert tf['Certified key bits']=='0' and 'Conditional budget' in tf
    assert 'TECH-RIO-TF-01.classical' in tf['controllers']
    assert 'TECH-RIO-TF-01.quantum' in tf['controllers']
    assert 'not configured' not in tf['controllers']
    assert 'BSM success' in cards['TECH-RIO-ENTANGLEMENT-01']


@pytest.mark.parametrize('size',[(640,480),(1280,800),(3440,1440)])
def test_dialog_responsive_controls(screen,app,size):
    window,provider=screen;window.resize(*size);window.technology_panel.open();app.processEvents()
    dialog=window.technology_panel.dialog;dialog.resize(*size);app.processEvents()
    assert dialog.export.isVisible() and dialog.evidence.isVisible()
    assert not dialog.grab().isNull()
