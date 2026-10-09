"""Additive GEO installation never replaces existing orbital or operator data."""
from dataclasses import replace
import json

import pytest

from quantum_autopilot.control_plane.topology import SimulatedTopologyStore
from quantum_autopilot.control_plane.communicators import CommunicatorStore
from quantum_autopilot.network_engine.institutional import brazil_institutions, ensure_institutional_workspace
from quantum_autopilot.network_engine.global_operations_topology import institutional_ids
from quantum_autopilot.quantum_protocols.global_geo import expand_geo_constellation, upgrade_geo_constellation_workspace, GEO_EVENT
from quantum_autopilot.quantum_protocols.topology import expand_global

pytestmark=pytest.mark.unit


def test_geo_addition_keeps_all_existing_components_and_explicit_associations():
    before,_=expand_global(brazil_institutions())
    after,report=expand_geo_constellation(before)
    assert after.nodes[:len(before.nodes)]==before.nodes
    assert after.links[:len(before.links)]==before.links
    added=after.nodes[len(before.nodes):]
    assert len(added)==4
    sessions=[raw for node in added for raw in node.telemetry['technology_sessions']]
    unique={raw['session_id']:raw for raw in sessions}
    assert sum('satellite_isl' in raw['technologies'] for raw in unique.values())==4
    assert sum('satellite_doppler' in raw['technologies'] for raw in unique.values())==11
    for node in added:
        orbit=node.telemetry['orbit_record']
        assert orbit['model']=='geostationary_reference' and orbit['inclination_deg']==0 and orbit['eccentricity']==0
        assert node.telemetry['orbital_assumptions']['physical_action_performed'] is False
    for link in after.links[len(before.links):]:
        assert link.channels['classical']['channel_id']!=link.channels['quantum']['channel_id']
        assert link.technology['physical_connection_to_orion'] is False


def test_geo_migration_is_atomic_and_does_not_restore_operator_removal(tmp_path):
    store=SimulatedTopologyStore(tmp_path/'geo.sqlite')
    before,_=expand_global(brazil_institutions());store.initialize(before)
    after=upgrade_geo_constellation_workspace(store,before)
    changed=replace(after,links=after.links[:-1],topology_version=after.topology_version+1)
    store._replace_existing(changed,expected_version=after.topology_version,event_kind='operator_removed_link',payload={})
    assert upgrade_geo_constellation_workspace(store,changed)==changed
    with store._connect() as connection:
        event=json.loads(connection.execute('SELECT event_json FROM topology_events WHERE event_kind=?',(GEO_EVENT,)).fetchone()[0])
    assert event['payload']['before']==before.to_dict()
    assert event['version_before']==before.topology_version
    assert event['version_after']==after.topology_version


def test_institutional_workspace_retains_real_scenario_and_causal_operation_configuration(tmp_path):
    store=SimulatedTopologyStore(tmp_path/'workspace.sqlite')
    topology,_=ensure_institutional_workspace(store)
    # Startup registers the communicator inventory after additive migrations.
    topology=CommunicatorStore(store,topology.topology_id).seed(topology,'simulated')
    before=topology.to_dict()
    assert len(topology.nodes)==71 and len(topology.links)==131
    assert ensure_institutional_workspace(store)[0].to_dict()==before
    institutions=[topology.node(ident) for ident in institutional_ids(topology)]
    assert institutions and all(node.telemetry['communicator']['physical_type']=='installation' for node in institutions)
    assert topology.node('CIMATEC').role.value=='endpoint'
    assert all({'endpoint_send','endpoint_receive'}<=set(node.telemetry['communicator']['capabilities']) for node in institutions)
