"""Globe prioritises satellites without changing geographic or protocol data.

Fixtures are explicitly synthetic observations for UI geometry. Backend optical
availability and processed detections are validated by the protocol campaigns.
"""
import math
import os
from dataclasses import replace
from types import SimpleNamespace

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import pytest
pytest.importorskip('PySide6.QtWidgets')
from PySide6.QtCore import QPointF, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from quantum_autopilot.console import theme
from quantum_autopilot.console.contract import Link, LiveLink, Node
from quantum_autopilot.console.widgets.figures import marker_kind, marker_side, marker_hit_rect
from quantum_autopilot.console.widgets.geo_map import GeoMap
from quantum_autopilot.console.widgets.network_views import build_net_model
from quantum_autopilot.console.widgets.orbit_globe import OrbitGlobe
from quantum_autopilot.console.widgets.satellite_telemetry import SatelliteLivePanel, SatelliteTelemetryDialog
from quantum_autopilot.console.widgets.technology_panel import GlobalOperationsView


@pytest.fixture(scope='module')
def app():
    current = QApplication.instance() or QApplication([])
    theme.set_theme('dark'); theme.install(current)
    return current


def globe_model(*, transmitting=True):
    nodes = []
    for kind, count in [('endpoint', 14), ('core', 3), ('node', 5), ('repeater', 20)]:
        for index in range(count):
            position=len(nodes)
            nodes.append(Node(f'{kind}-{index}', kind.upper(), latitude=(position%7-3)*3,
                              longitude=(position//7-3)*6, network_role=kind,
                              kind=kind, name=f'Test ground {kind} {index}'))
    vectors = {'GEO-A': [41000., -9000., 3000.], 'GEO-B': [41000., 9000., 3000.],
               'GEO-HIDDEN': [-42164., 0., 0.]}
    satellites = {}
    for ident, vector in vectors.items():
        nodes.append(Node(ident, 'REPEATER', physical_type='satellite', network_role='repeater', kind='satellite'))
        orbit = [[42164*math.cos(i*math.pi/96), 42164*math.sin(i*math.pi/96), 0.] for i in range(193)]
        satellites[ident] = {'node_id': ident, 'device_id': ident, 'ecef_km': vector,
            'orbit_ecef_km': orbit, 'period_s': 86164., 'orbital_class': 'GEO',
            'at_network_s': 1., 'origin': 'synthetic UI fixture', 'transmitting_links': []}
    if transmitting:
        satellites['GEO-A']['transmitting_links'] = [{'link_id': 'ISL-AB', 'peer_id': 'GEO-B', 'endpoint_kind': 'satellite'}]
        satellites['GEO-B']['transmitting_links'] = [{'link_id': 'ISL-AB', 'peer_id': 'GEO-A', 'endpoint_kind': 'satellite'}]
    links = [Link('GEO-A', 'GEO-B', 'Synthetic received ISL', id='ISL-AB'),
             Link('GEO-A', 'GEO-HIDDEN', 'Earth-obstructed association', id='ISL-BLOCKED')]
    snap = SimpleNamespace(topology=SimpleNamespace(nodes=nodes, links=links), live=None)
    result = replace(build_net_model(snap), satellites=satellites, network_time_s=1.)
    # Styles follow only the announced transmissions; the fixture never runs physics.
    return replace(result, links=tuple(replace(link, style='space' if transmitting and link.id=='ISL-AB' else 'space_config') for link in result.links))


def test_satellite_ranges_use_received_geometry_without_replacing_fibre_configuration():
    nodes=[Node('SAT-A','REPEATER',physical_type='satellite'),Node('SAT-B','REPEATER',physical_type='satellite'),
        Node('GROUND-A','ENDPOINT'),Node('GROUND-B','ENDPOINT')]
    links=[Link('SAT-A','SAT-B','Registered ISL',id='ISL',distance_km=0.),
        Link('SAT-A','GROUND-A','Registered geographic reference',id='DOWNLINK',distance_km=4252.),
        Link('GROUND-A','GROUND-B','Registered fibre',id='LAND',distance_km=200.)]
    live=SimpleNamespace(links=[LiveLink('ISL','SAT-A','SAT-B',54205.,'up'),
        LiveLink('DOWNLINK','SAT-A','GROUND-A',12040.,'up'),LiveLink('LAND','GROUND-A','GROUND-B',999.,'up')],
        satellites={},network_time_s=1.,communicators={})
    snapshot=SimpleNamespace(topology=SimpleNamespace(nodes=nodes,links=links),live=live)
    model=build_net_model(snapshot)
    assert model.link('ISL').distance_km==54205. and model.link('ISL').kind=='satellite_isl'
    assert model.link('DOWNLINK').distance_km==12040. and model.link('DOWNLINK').kind=='satellite'
    assert model.link('LAND').distance_km==200.
    configured=build_net_model(SimpleNamespace(topology=snapshot.topology,live=None))
    assert configured.link('DOWNLINK').distance_km==4252. and configured.link('ISL').distance_km==0.


@pytest.mark.parametrize('size', [(640, 480), (1280, 900), (3440, 1440)])
@pytest.mark.parametrize('zoom', [.5, 1., 4.])
def test_ground_is_discreet_and_satellites_keep_priority(app, size, zoom, tmp_path):
    globe = OrbitGlobe(); globe.resize(*size); globe.set_model(globe_model()); globe.set_camera(0., 0., zoom)
    globe.show(); app.processEvents()
    try:
        ground = globe.ground_marker_geometry(); satellites = globe.orbital_items()
        assert len(ground) == 42 and len(satellites) == 3
        assert all(item['size_px'] < marker_side(item['kind']) for item in ground)
        assert all(item['size_px'] <= 14. and item['hit'].width() <= 18. for item in ground)
        assert all(item['size_px'] >= 32. and item['hit'].width() == 48. for item in satellites)
        for item in satellites:
            assert globe.hit(item['centre']) == ('node', item['id'])
            QTest.mouseMove(globe, item['centre'].toPoint()); app.processEvents()
            assert globe.hovered_id == item['id'] and globe.cursor().shape() == Qt.CursorShape.PointingHandCursor
        labels = globe.label_geometry()
        assert all(node.network_role in ('endpoint', 'core') for node, _, _ in labels)
        assert len(labels) <= 12
        for index, (_, _, rect) in enumerate(labels):
            assert not any(rect.intersects(other) for _, _, other in labels[index+1:])
            assert not any(rect.intersects(item['hit']) for item in satellites)
        assert set(globe._orbit_paths) == {'GEO-A','GEO-B','GEO-HIDDEN'}
        # GEO scale leaves a small Earth; its surface must still be identifiable.
        earth = globe._earth().toImage()
        ratio = earth.devicePixelRatio()
        centre_colour = earth.pixelColor(round(globe.width()*ratio/2), round(globe.height()*ratio/2))
        assert centre_colour.value() - earth.pixelColor(0, 0).value() >= 30
        assert globe.grab().save(str(tmp_path/f'globe-{size[0]}-{zoom}.png'))
    finally:
        globe.shutdown(); globe.close(); globe.deleteLater(); app.processEvents()


def test_ground_can_be_selected_and_map_restores_full_role_symbols(app):
    geo = GeoMap(); geo.resize(1280, 900); model = globe_model(); geo.set_model(model); geo.show(); app.processEvents()
    selected = []; geo.node_selected.connect(selected.append)
    try:
        geo.set_mode('globe', camera=(0., 0., 4.)); app.processEvents(); QTest.qWait(5); app.processEvents(); globe=geo.globe
        item = next(item for item in globe.ground_marker_geometry() if item['node'].id=='core-0')
        # Overlapping institutions are accessible through focus/panels; an exact
        # centre resolves the nearest visible marker rather than arbitrary ID order.
        kind, ident = globe.hit(item['point'])
        assert kind=='node' and geo.net.node(ident).physical_type!='satellite'
        QTest.mouseClick(globe, Qt.MouseButton.LeftButton, pos=item['point'].toPoint()); app.processEvents()
        assert selected and selected[-1]==ident
        ground_size=item['size_px']; model_before=geo.net
        geo.set_mode('map'); app.processEvents(); QTest.qWait(5); app.processEvents()
        assert geo.net is model_before and marker_side(marker_kind(item['node'])) > ground_size
        assert marker_hit_rect(QPointF(), item['node']).width() >= 24.
        geo.focus_node.setCurrentIndex(geo.focus_node.findData('core-0')); app.processEvents()
        assert geo.hit(geo.node_position('core-0'))==('node','core-0')
    finally:
        geo.shutdown(); geo.close(); geo.deleteLater(); app.processEvents()


def test_geo_stacked_header_uses_full_row_and_elides_without_losing_text(app, tmp_path):
    geo = GeoMap(); geo.resize(640, 480); geo.set_model(globe_model()); geo.show(); app.processEvents()
    try:
        geo.set_mode('globe', camera=(0., 0., 1.)); QTest.qWait(350); app.processEvents()
        assert geo.info.y() >= geo.controls.geometry().bottom()
        for label in (geo.title, geo.placed):
            assert label.displayed_text() == label.text()
            assert label.toolTip() == label.text()
        full_title = 'Globo · satélites com identificação completa e informações orbitais de origem verificável'
        geo.title.setText(full_title); geo._place_overlays(); app.processEvents()
        assert geo.title.text() == full_title and geo.title.toolTip() == full_title
        assert geo.title.displayed_text().endswith('…')
        assert geo.title.fontMetrics().horizontalAdvance(geo.title.displayed_text()) <= geo.title.contentsRect().width()
        assert geo.grab().save(str(tmp_path / 'geo-header-elided-640.png'))
    finally:
        geo.shutdown(); geo.close(); geo.deleteLater(); app.processEvents()


def test_isl_is_registered_deduplicated_observed_and_earth_occluded(app):
    globe=OrbitGlobe(); globe.resize(1280,900); globe.set_model(globe_model()); globe.set_camera(0.,0.,1.)
    try:
        config=globe.connection_geometry()
        assert len(config)==2 and {link.kind for link, *_ in config}=={'satellite_isl'}
        assert next(visible for link, _, _, visible in config if link.id=='ISL-AB')
        assert not next(visible for link, _, _, visible in config if link.id=='ISL-BLOCKED')
        active=globe.transmission_geometry()
        assert len(active)==1 and active[0][0]['link_id']=='ISL-AB'
        # Both spacecraft announce one received channel, never two rendered paths.
        globe.set_model(globe_model(transmitting=False))
        assert len(globe.connection_geometry())==2 and not globe.transmission_geometry()
        assert all(link.style=='space_config' for link in globe.net.links)
    finally:
        globe.shutdown(); globe.close(); globe.deleteLater(); app.processEvents()


def test_live_isl_metrics_demands_and_housekeeping_are_received_values(app):
    panel=SatelliteLivePanel()
    state={'node_id':'GEO-A','device_id':'SAT-A','origin':'received fixture','clock_s':3.,
        'housekeeping':{'at_s':2.,'valid_until_s':4.,'ecef_km':[42000.,0.,0.]},
        'bridges':[{'session_id':'BRIDGE-PENDING','metrics':{}},
                   {'session_id':'ISL-READY','technologies':['satellite_isl'],'metrics':{'isl_geometry':{'range_km':9000.}},
                    'connections':[{'link_id':'ISL-1','station_id':'GEO-B','endpoint_kind':'satellite'}]}],
        'isl_links':[{'link_id':'ISL-1','peer_id':'GEO-B','session_id':'ISL-READY','status':'transmitting','reason':'processed receiver detections',
                     'classical':{'channel_id':'CONTROL-1','enabled':True},'quantum':{'channel_id':'QUANTUM-1','enabled':True},
                     'metrics':{'range_km':9000.,'visible':True,'earth_obstructed':False,'detections':7}},
                     {'link_id':'ISL-2','peer_id':'GEO-C','status':'unavailable','reason':'Earth obstruction','metrics':{'visible':False,'earth_obstructed':True}}],
        'global_demands':[{'id':'DEMAND-1','operation':'entanglement','status':'awaiting_resources','path':['A','GEO-A','GEO-B','B'],'reason':'memory expired'}]}
    try:
        panel.set_state(state,{'name':'Received sat'})
        assert 'vetor recebido' in panel.summary.text()
        assert panel.isl.value('ISL 1 · Distância entre satélites')=='9000.00 km'
        assert 'CONTROL-1' in panel.isl.value('ISL 1 · Canal clássico')
        assert 'QUANTUM-1' in panel.isl.value('ISL 1 · Payload quântico')
        assert panel.details.isEnabled() and panel._session=='ISL-READY'
        assert 'ISL-1' not in panel.stations
        assert 'Earth obstruction' in panel.isl.value('ISL 2 · Estado / causa')
        assert 'memory expired' in panel.operations.value('Demanda 1')
        rows=dict(panel.isl.rows)
        state['isl_links'][0]['metrics']['range_km']=8900.
        panel.set_state(state,{'name':'Received sat'})
        assert all(panel.isl.rows[key] is row for key,row in rows.items())
        assert panel.isl.value('ISL 1 · Distância entre satélites')=='8900.00 km'
        state['clock_s']=5.;panel.set_state(state,{'name':'Received sat'})
        assert 'OBSERVAÇÃO VENCIDA' in panel.summary.text()
    finally:
        panel.close(); panel.deleteLater(); app.processEvents()


def test_global_operation_tables_update_without_losing_evidence_or_scroll(app,tmp_path):
    view=GlobalOperationsView();view.resize(1060,700);view.show();app.processEvents()
    state={'clock_s':10.,'demands':[{'demand_id':f'D-{i}','a':'CIMATEC','b':'BSC','operation':'global_entanglement',
        'state':'waiting_resources','reason':'memory pending','route':['CIMATEC','GEO-A','GEO-B','BSC'],
        'session_id':'BBM92 + swapping','receipt_ids':[f'RECEIPT-{i}']} for i in range(40)],
        'components':[{'component':'CIMATEC','capabilities':{'endpoint_send':True,'endpoint_receive':True,'classical_forward':True},
            'configuration':'complete','links':['LINK-1'],'routes':['D-1'],'operation_state':'available_no_traffic','impediments':[],
            'simulation_resource_allocation':{'banks':[{'resource_bank_id':'sim-resource:D-1','session_id':'D-1'}],
                'totals':{'resource_bank_count':1,'memory_modes':8,'sources':1,'optical_terminals':2},
                'physical_capacity_validated':False,'shared_hardware_arbitration_implemented':False}}]}
    try:
        view.set_state(state);app.processEvents()
        assert view.demands.rowCount()==40 and view.components.rowCount()==1
        assert view.demands.item(0,3).text()=='waiting_resources'
        assert view.demands.item(0,7).text()=='RECEIPT-0'
        assert view.components.item(0,0).text()=='CIMATEC'
        assert 'endpoint_send' in view.components.item(0,1).text()
        allocation_item=view.components.item(0,7)
        assert '1 bancos dedicados' in allocation_item.text() and '8 modos de memória' in allocation_item.text()
        assert 'hipótese simulada' in allocation_item.text() and 'sim-resource:D-1' in allocation_item.toolTip()
        view.demands.selectRow(20);bar=view.demands.verticalScrollBar();bar.setValue(bar.maximum()//2)
        position=bar.value();item=view.demands.item(0,3)
        view._capture();captured=view.evidence.toPlainText()
        state['demands'][0]['state']='transmitting';state['clock_s']=11.
        state['components'][0]['simulation_resource_allocation']['totals']['memory_modes']=16
        view.set_state(state);app.processEvents()
        assert view.demands.item(0,3) is item and item.text()=='transmitting'
        assert view.demands.currentRow()==20 and bar.value()==position
        assert view.evidence.toPlainText()==captured
        assert view.components.item(0,7) is allocation_item and '16 modos de memória' in allocation_item.text()
        assert 'sim-resource:D-1' in captured and '"memory_modes": 8' in captured
        view.tabs.setCurrentIndex(1);app.processEvents()
        assert view.grab().save(str(tmp_path/'component-audit.png'))
        view.tabs.setCurrentIndex(0);app.processEvents()
        assert view.grab().save(str(tmp_path/'global-demands.png'))
    finally:
        view.close();view.deleteLater();app.processEvents()


def test_isl_science_dialog_shows_received_terminals_and_no_ogs_gateway(app):
    dialog=SatelliteTelemetryDialog()
    metrics={'protocol':'BBM92-ISL','observation_origin':'simulated','observation_source_id':'isl_sim_fixture',
             'satellite_housekeeping':{'GEO-A':{'at_s':2.,'ecef_km':[42000.,0.,0.]},
                                      'GEO-B':{'at_s':2.,'ecef_km':[40000.,5000.,0.]}},
             'isl_geometry':{'range_km':9000.,'visible':True,'earth_obstructed':False,'sync_jitter_ps':50.,
                             'doppler_shift_hz':12e6,'link_budget_db':{'total':40.},
                             'emitted_photons':200,'detected_photons':7},'coincidences':5,'pair_rate_hz':2.5}
    try:
        dialog.set_metrics(metrics,session_id='ISL-A-B',clock_s=3.,allowed=False)
        fields={dialog.housekeeping.item(row,0).text():dialog.housekeeping.item(row,1).text() for row in range(dialog.housekeeping.rowCount())}
        assert fields['GEO-A · Posição ECEF']=='[42000.0, 0.0, 0.0]'
        assert fields['GEO-B · Posição ECEF']=='[40000.0, 5000.0, 0.0]'
        assert fields['ISL · Distância']=='9000'
        assert float(fields['ISL · Desvio Doppler'])==12e6
        assert not dialog.start.isEnabled() and not dialog.stop.isEnabled()
        assert 'não disponibiliza entrada OGS' in dialog.integration_note.text()
        assert not dialog._ogs_available and not dialog.tabs.isTabVisible(dialog._ogs_tab)
        assert not dialog.ogs_page.isEnabled()
        assert 'recepção Missão / RF é independente da sessão óptica' in dialog.integration_note.text()
    finally:
        dialog.close();dialog.deleteLater();app.processEvents()


def test_global_validator_awaits_every_satellite_full_received_presentation(app,tmp_path):
    from pathlib import Path
    from quantum_autopilot.console.global_globe_validation import validate_global_globe
    from quantum_autopilot.console.service_provider import ServiceProvider
    from quantum_autopilot.network_engine.institutional import PRESET_ID
    from quantum_autopilot.qkd.dashboard_api import demo_service

    service=demo_service(Path(__file__).resolve().parents[1],tmp_path,preset=PRESET_ID,scenario='quiet')
    provider=ServiceProvider(service,state_root=tmp_path/'provider')
    try:
        report=validate_global_globe(tmp_path/'readiness-evidence',provider)
        assert report['passed'],report
        waits=report['presentation_waits']
        all_opened=[item for item in waits if item['phase']=='open-all-satellites']
        assert len(all_opened)==report['counts']['satellites']
        assert len({item['node_id'] for item in all_opened})==len(all_opened)
        assert any(item['phase']=='open-isl' for item in waits)
        assert any(item['phase']=='refresh' for item in waits)
        assert all(item['ready'] and item['complete'] and all(item['checks'].values()) for item in waits)
        assert all(item['source_kind']=='simulated' for item in waits)
        assert all(item['presentation']['expected_sections']==item['presentation']['completed_sections'] for item in waits)
        assert all(item['presented_tick']==item['presentation']['presented_tick']
                   and item['network_time_s']==item['presentation']['network_time_s'] for item in waits)
    finally:
        provider.stop();app.processEvents()
