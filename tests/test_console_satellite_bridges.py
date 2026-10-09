"""Preservation and operational two-network bridges through the real OGS flow."""
import os
from pathlib import Path

os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import pytest
from PySide6.QtWidgets import QApplication
from quantum_autopilot.control_plane.topology import SimulatedTopologyStore
from quantum_autopilot.network_engine.institutional import PRESET_ID,brazil_institutions
from quantum_autopilot.quantum_protocols.topology import expand_global
from quantum_autopilot.quantum_protocols.satellite_bridges import upgrade_satellite_bridge_workspace
from quantum_autopilot.qkd.dashboard_api import demo_service
from quantum_autopilot.console.service_provider import ServiceProvider
from quantum_autopilot.console.satellite_bridge_validation import validate_satellite_bridge_view

pytestmark=pytest.mark.unit


def test_additive_bridge_migration_preserves_operator_data_and_removals(tmp_path):
    old=expand_global(brazil_institutions())[0]
    store=SimulatedTopologyStore(tmp_path/'journal.sqlite');store.initialize(old)
    new=upgrade_satellite_bridge_workspace(store,old)
    assert new.nodes[:-1]==old.nodes and new.links[:-2]==old.links
    assert upgrade_satellite_bridge_workspace(store,new).to_dict()==new.to_dict()
    # An operator removal must not be undone on the next application start.
    from dataclasses import replace
    removed=replace(new,nodes=old.nodes,links=old.links,topology_version=new.topology_version+1)
    store._replace_existing(removed,expected_version=new.topology_version,event_kind='operator_removed_bridge',payload={})
    assert upgrade_satellite_bridge_workspace(store,removed).to_dict()==removed.to_dict()


def test_satellite_window_tracks_protocol_blocks_failure_and_recovery(tmp_path):
    from quantum_autopilot.console import theme
    app=QApplication.instance() or QApplication([]);theme.install(app)
    service=demo_service(Path(__file__).resolve().parents[1],tmp_path,preset=PRESET_ID,scenario='quiet')
    provider=ServiceProvider(service,state_root=tmp_path/'provider')
    try:
        provider.refresh()
        initial=provider.snapshot().live.satellites['SAT-ATLANTIC-MEO']
        assert initial['bridges'][0]['status']=='waiting' and initial['transmitting_links']==[]
        report=validate_satellite_bridge_view(tmp_path/'evidence',provider)
        assert report['passed'],report
        assert report['nominal_metrics']['conditional_key_estimate_bits']==0
        waits=report['presentation_waits']
        assert [item['phase'] for item in waits]==['open','refresh']
        assert all(item['ready'] and item['complete'] and all(item['checks'].values()) for item in waits)
        assert all(item['source_kind']=='simulated' for item in waits)
        assert all(item['presentation']['expected_sections']==item['presentation']['completed_sections'] for item in waits)
        assert all(item['presented_tick']==item['presentation']['presented_tick']
                   and item['network_time_s']==item['presentation']['network_time_s'] for item in waits)
        assert waits[1]['presented_tick']>waits[0]['presented_tick']
    finally:provider.stop();app.processEvents()


@pytest.mark.parametrize('channel', ['classical', 'quantum'])
def test_bridge_requires_both_channels_and_new_receipts_after_reactivation(tmp_path,channel):
    import copy
    from dataclasses import replace
    service=demo_service(Path(__file__).resolve().parents[1],tmp_path,preset=PRESET_ID,scenario='quiet')
    engine=service.network_engine
    for _ in range(5):engine.step()
    before=engine.live_state().satellites['SAT-ATLANTIC-MEO']['bridges'][0]
    assert before['status']=='observed_pairs'
    old=engine.topology();ident=before['connections'][0]['link_id']
    links=[]
    for link in old.links:
        if link.link_id==ident:
            ports=copy.deepcopy(link.channels);ports[channel]['enabled']=False
            link=replace(link,channels=ports)
        links.append(link)
    updated=replace(old,links=tuple(links),topology_version=old.topology_version+1)
    engine.store._replace_existing(updated,expected_version=old.topology_version,event_kind='operator_channel_disabled',payload={})
    for _ in range(5):engine.step()
    satellite=engine.live_state().satellites['SAT-ATLANTIC-MEO']
    assert satellite['transmitting_links']==[] and satellite['bridges'][0]['status']=='blocked'
    assert not next(c for c in satellite['bridges'][0]['connections'] if c['link_id']==ident)[channel]['enabled']
    associated={c['link_id'] for c in before['connections']}
    arms=[l for l in engine.live_state().links if l.link_id in associated]
    assert len(arms)==2 and all(l.fidelity is None and l.status=='down' for l in arms)
    restored=replace(old,topology_version=updated.topology_version+1)
    engine.store._replace_existing(restored,expected_version=updated.topology_version,event_kind='operator_channel_enabled',payload={})
    for _ in range(5):engine.step()
    recovered=engine.live_state().satellites['SAT-ATLANTIC-MEO']
    assert recovered['bridges'][0]['status']=='observed_pairs' and len(recovered['transmitting_links'])==2
    assert recovered['bridges'][0]['last_observed_at_s']>before['last_observed_at_s']
