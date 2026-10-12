"""Cross-layer regression of finite SNS, orbital resources and GEO publication.

Synthetic sources only; the test exercises the operational contracts, never
authorizes hardware or treats a conditional estimate as a certified key.
Historical qualification campaigns and their independent oracles are intact.
"""
from __future__ import annotations

import json
import math
import os
from collections import Counter
from dataclasses import replace
from pathlib import Path

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import pytest
from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from quantum_autopilot.control_plane.topology import SimulatedTopologyStore
from quantum_autopilot.control_plane.channels import local_pair
from quantum_autopilot.network_engine.institutional import PRESET_ID, brazil_institutions
from quantum_autopilot.quantum_protocols.contracts import ProtocolConfig, ProtocolError
from quantum_autopilot.quantum_protocols.hybrid_upgrade import expand_finite_demo, upgrade_hybrid_workspace
from quantum_autopilot.quantum_protocols.orbital_relay_topology import LINK_IDS, PATH, SESSION_ID, expand_orbital_relay
from quantum_autopilot.quantum_protocols.runtime import PairResource, ProtocolRuntime
from quantum_autopilot.quantum_protocols.sources import SimulatedAcquisitionSource
from quantum_autopilot.quantum_protocols.topology import expand_global

pytestmark = pytest.mark.unit
REPO = Path(__file__).resolve().parents[1]


def advance(runtime, topology, seconds):
    for _ in range(30):
        runtime.advance(topology, seconds)
        if runtime.clock_s >= seconds:
            return
    raise AssertionError('bounded worker did not drain the target clock')


def rows(runtime, destination):
    runtime.export_events(destination)
    return [json.loads(line) for line in destination.read_text(encoding='utf-8').splitlines()]


def isolated(kind, **parameters):
    base = expand_global(brazil_institutions())[0]
    if kind == 'finite':
        value = expand_finite_demo(base)[0]
        ident = 'RIO-SNS-FINITE'
    else:
        value = expand_orbital_relay(base)[0]
        ident = SESSION_ID
    config = next(ProtocolConfig.from_dict(c) for n in value.nodes
                  for c in n.telemetry.get('technology_sessions', ()) if c['session_id'] == ident)
    config = replace(config, parameters={**config.parameters, 'max_attempts': 1, **parameters})
    nodes = tuple(replace(n, telemetry={**n.telemetry,
                  'technology_sessions': [config.to_dict()] if n.node_id == config.path[0] else []})
                  for n in value.nodes if n.node_id in config.path)
    return replace(value, nodes=nodes, links=tuple(link for link in value.links if link.link_id in config.link_ids)), config


def disabled_classical(value, link_id):
    links = []
    for link in value.links:
        if link.link_id == link_id:
            channels = json.loads(json.dumps(link.channels))
            channels['classical']['enabled'] = False
            link = replace(link, channels=channels)
        links.append(link)
    return replace(value, links=tuple(links))


def test_initial_finite_herald_loss_releases_inflight_block_and_recovers(tmp_path):
    value, config = isolated('finite', pulse_rate_hz=10000., classical_latency_s=.2)
    runtime = ProtocolRuntime(tmp_path/'finite.sqlite', value.topology_id, source=SimulatedAcquisitionSource(101))
    advance(runtime, value, 1.01)
    initial = rows(runtime, tmp_path/'before.jsonl')
    assert any(r['event'] == 'classical_transmitted' for r in initial)
    assert not any(r['event'] == 'protocol_proposal_prepared' for r in initial)
    first = next(r['command_id'] for r in initial if r['event'] == 'acquisition_requested')
    session = runtime.sessions[config.session_id]
    assert first in session.processing_commands
    advance(runtime, disabled_classical(value, config.link_ids[0]), 1.6)
    assert first not in session.processing_commands
    assert not any(r['event'] == 'metrics_computed' for r in rows(runtime, tmp_path/'interrupted.jsonl'))
    advance(runtime, value, 6.)
    completed = rows(runtime, tmp_path/'recovered.jsonl')
    assert any(r['event'] == 'late_message_discarded' and r['command_id'] == first for r in completed)
    assert any(r['event'] == 'metrics_computed' and r['command_id'] != first for r in completed)
    assert session.counts['observations'] > 0
    # The small block honestly aborts its finite key bound; recovery is not a
    # claim that this underpowered capture produced secure material.
    assert session.metrics['certified_secret_bits'] == 0


@pytest.mark.parametrize('operation', ['generate', 'bsm'])
def test_received_failure_releases_modes_without_restoring_consumed_parents(tmp_path, monkeypatch, operation):
    value, config = isolated('relay')
    runtime = ProtocolRuntime(tmp_path/'relay.sqlite', value.topology_id, source=SimulatedAcquisitionSource(101))
    original = runtime._received
    original_failure = runtime._failure
    failed = []
    released = []

    def reject_once(session, at_s, command, observation, context, result):
        if command.operation == operation and not failed:
            if operation == 'generate':
                assert command.command_id in session.capture_reservations
            else:
                assert context['resource_key'] in session.held_memories
            failed.append((command.command_id, dict(context)))
            raise ProtocolError('review_injected', 'failure after validated intake, before admission')
        return original(session, at_s, command, observation, context, result)

    def verify_release(session, at_s, command, context, error):
        original_failure(session, at_s, command, context, error)
        if getattr(error, 'code', '') == 'review_injected':
            assert command.command_id not in session.capture_reservations
            assert context.get('resource_key') not in session.held_memories
            assert context.get('resource_key') not in session.operating
            released.append(command.command_id)

    monkeypatch.setattr(runtime, '_received', reject_once)
    monkeypatch.setattr(runtime, '_failure', verify_release)
    advance(runtime, value, 10.)
    assert failed, 'the actual protocol never reached the requested admission operation'
    command_id, context = failed[0]
    session = runtime.sessions[config.session_id]
    assert command_id not in session.capture_reservations
    assert released == [command_id]
    # Later BSM commands legitimately reuse a span's resource key, so the
    # release is inspected immediately after this particular failure above.
    assert set(context.get('parent_ids', ())).isdisjoint(p.pair_id for p in session.pairs)
    occupancy = Counter(n for pair in session.pairs for n in (pair.a, pair.b))
    occupancy.update(n for endpoints in session.held_memories.values() for n in endpoints)
    for endpoints, slots in session.capture_reservations.values():
        for node_id in endpoints:
            occupancy[node_id] += slots
    assert all(occupancy[n] <= runtime._device_parameter(session, n, 'memory_slots', 0) for n in config.path)
    evidence = rows(runtime, tmp_path/'events.jsonl')
    assert any(r['event'] == 'operation_error' and r['command_id'] == command_id for r in evidence)
    assert not any(r['event'] == 'metrics_computed' and r['command_id'] == command_id for r in evidence)
    assert any(r['event'] == 'metrics_computed' and r['command_id'] != command_id for r in evidence)


@pytest.mark.parametrize('case', ['decoherence', 'readout_cutoff', 'bsm_cutoff'])
def test_operation_duration_ages_reserved_resources_and_enforces_cutoff(tmp_path, case):
    # A prepared-resource fixture isolates processing time from generation.
    # The acquisition source itself is the unmodified registered simulator.
    _, initial = isolated('relay')
    profiles = json.loads(json.dumps(initial.parameters['device_parameters']))
    for profile in profiles.values():
        profile.update(coherence_s=.2 if case == 'decoherence' else 100.,
                       memory_cutoff_s=10. if case == 'decoherence' else .5,
                       memory_read_efficiency=1.)
    value, config = isolated('relay', device_parameters=profiles, operation_duration_s=1.)
    runtime = ProtocolRuntime(tmp_path/'duration.sqlite', value.topology_id, source=SimulatedAcquisitionSource(101))
    runtime.sync(value)
    runtime._queue.clear()  # Disable unrelated new captures in this narrow fixture.
    session = runtime.sessions[config.session_id]
    endpoints = (PATH[:2], PATH[1:3]) if case == 'bsm_cutoff' else (config.endpoints,)
    for index, pair_endpoints in enumerate(endpoints):
        runtime._add(session, PairResource(f'prepared-resource-{index}', *pair_endpoints, .88, 0., 0.), 0.)
    runtime._drive(session, 0.)
    assert session.held_memories and not session.pairs
    advance(runtime, value, 3.)
    evidence = rows(runtime, tmp_path/'duration-events.jsonl')
    assert not session.held_memories and not session.operating and not session.pairs
    assert session.counts.get('delivered_pairs', 0) == 0
    assert not any(r['event'] == 'end_to_end_delivered' for r in evidence)
    if case == 'decoherence':
        rejected = next(r for r in evidence if r['event'] == 'delivery_rejected')
        # Two T=.2 s memories give the pair T_eff=.1 s. Before the fix,
        # elapsed=0 incorrectly kept .88 above the .65 delivery criterion.
        expected = .25 + (.88-.25) * math.exp(-1./.1)
        assert rejected['detail']['fidelity_estimate'] == pytest.approx(expected)
        assert rejected['detail']['fidelity_estimate'] < .65
        assert not any(r['event'] == 'operation_error' for r in evidence)
    else:
        assert any(r['event'] == 'operation_error' and r['detail']['code'] == 'resource_expired' for r in evidence)
        assert not any(r['event'] == 'metrics_computed' for r in evidence)


@pytest.mark.parametrize('collision', ['node', 'link', 'removed_reference'])
def test_migration_preserves_operator_collision_and_is_idempotent(tmp_path, collision):
    base = expand_global(brazil_institutions())[0]
    if collision == 'node':
        occupied = replace(base.node('CBPF'), node_id=PATH[1], telemetry={'operator_owned': True})
        base = replace(base, nodes=(*base.nodes, occupied))
    elif collision == 'link':
        occupied = replace(base.links[0], link_id=LINK_IDS[0],
                           channels=local_pair(LINK_IDS[0], base.links[0].right_node_id))
        base = replace(base, links=(*base.links, occupied))
    else:
        base = replace(base, nodes=tuple(n for n in base.nodes if n.node_id != 'BSC'),
                       links=tuple(link for link in base.links if 'BSC' not in (link.left_node_id, link.right_node_id)))
    store = SimulatedTopologyStore(tmp_path/'migration.sqlite')
    store.initialize(base)
    after = upgrade_hybrid_workspace(store, base)
    assert upgrade_hybrid_workspace(store, after) == after
    assert store.get(base.topology_id) == after
    assert not any(c['session_id'] == SESSION_ID for n in after.nodes for c in n.telemetry.get('technology_sessions', ()))
    if collision == 'node':
        assert after.node(PATH[1]) == occupied
    elif collision == 'link':
        assert next(link for link in after.links if link.link_id == LINK_IDS[0]) == occupied
    else:
        assert not any(n.node_id == 'BSC' for n in after.nodes)


def test_migration_preserves_operator_finite_configuration_and_removed_components(tmp_path):
    base = expand_global(brazil_institutions())[0]
    owned = replace(base.node('PUC-RIO'), telemetry={**base.node('PUC-RIO').telemetry,
                    'communicator': {'revision': 4, 'integration': {'mode': 'simulated'}, 'operator_note': 'keep'}})
    base = replace(base, nodes=tuple(owned if n.node_id == owned.node_id else n for n in base.nodes))
    store = SimulatedTopologyStore(tmp_path/'operator.sqlite')
    store.initialize(base)
    after = upgrade_hybrid_workspace(store, base)
    assert after.node('PUC-RIO') == owned
    assert not any(n.node_id == 'TF-RIO-FINITE-CENTER' for n in after.nodes)
    removed = replace(after, topology_version=after.topology_version+1,
                      links=tuple(link for link in after.links if link.link_id != LINK_IDS[0]))
    store._replace_existing(removed, expected_version=after.topology_version,
                            event_kind='review_operator_removal', payload={'operator_edit': True})
    assert upgrade_hybrid_workspace(store, removed) == removed
    assert not any(link.link_id == LINK_IDS[0] for link in store.get(base.topology_id).links)


@pytest.fixture(scope='module')
def app():
    from quantum_autopilot.console.theme import install
    application = QApplication.instance() or QApplication([])
    install(application)
    return application


@pytest.fixture
def provider(tmp_path, app):
    from quantum_autopilot.console.service_provider import ServiceProvider
    from quantum_autopilot.qkd.dashboard_api import demo_service
    service = demo_service(REPO, tmp_path, preset=PRESET_ID, scenario='quiet')
    backend = ServiceProvider(service, state_root=tmp_path/'provider')
    backend.refresh()
    yield backend
    backend.stop()
    app.processEvents()


@pytest.mark.parametrize('physical_type', ['ground_station', 'satellite'])
def test_new_relay_form_persists_combined_capabilities_without_irrelevant_orbits(app, provider, physical_type):
    from quantum_autopilot.console.screens.communicators import CommunicatorsScreen
    screen = CommunicatorsScreen()
    screen.resize(1280, 900)
    screen.show()
    try:
        screen.apply(provider.snapshot())
        screen.new_record()
        ident = 'REVIEW-'+physical_type.upper()
        for key in ('device_id', 'node_id', 'name'):
            screen.fields[key].setText(ident)
        combo = screen.fields['physical_type']
        combo.setCurrentIndex(combo.findData(physical_type))
        for technology, checkbox in screen.techs.items():
            checkbox.setChecked(technology in ('orbital_relay', 'quantum_memory', 'entanglement_swapping'))
        if physical_type == 'ground_station':
            screen.fields['latitude'].setText('-22.9')
            screen.fields['longitude'].setText('-43.2')
        else:
            screen.group_fields['orbit']['model'].setCurrentText('geostationary_reference')
            screen.group_fields['orbit']['reference_longitude'].setText('-60')
        screen.group_fields['orbital_relay']['pointing_jitter_convention'].setCurrentText('gaussian_per_axis')
        commands = []
        screen.command.connect(lambda *args: commands.append(args))
        screen.save.click()
        assert commands and commands[-1][0] == 'save_communicator', screen.message.text()
        provider.save_communicator(commands[-1][1])
        screen.apply(provider.snapshot())
        stored = provider.snapshot().live.communicators[ident]
        assert 'CONFIRMADO' in screen.message.text()
        assert set(stored['technologies']) == {'orbital_relay', 'quantum_memory', 'entanglement_swapping'}
        assert {'quantum_memory', 'heralded_photon_capture', 'tracking'} <= set(stored['capabilities'])
        terminal = 'optical_space_terminal' if physical_type == 'satellite' else 'optical_ground_terminal'
        assert terminal in stored['capabilities']
        assert bool(stored['orbit']) == (physical_type == 'satellite')
        assert screen.editor_tabs.isTabVisible(screen.group_pages['orbit']._tab_index) == (physical_type == 'satellite')
        assert stored['configuration']['orbital_relay']['pointing_jitter_convention'] == 'gaussian_per_axis'
        assert not any(ident in (link.a, link.b) for link in provider.snapshot().topology.links)
    finally:
        screen.shutdown()
        screen.close()
        screen.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_finite_form_persists_estimator_and_maps_parameters_to_runtime(app, provider):
    from quantum_autopilot.console.screens.communicators import CommunicatorsScreen
    from quantum_autopilot.control_plane.communicators import Communicator
    screen = CommunicatorsScreen()
    try:
        screen.apply(provider.snapshot())
        screen.new_record()
        for key in ('device_id', 'node_id', 'name'):
            screen.fields[key].setText('REVIEW-SNS')
        screen.techs['tf_qkd'].setChecked(True)
        fields = screen.group_fields['tf_qkd']
        for key, val in {'signal_mu': '.15', 'decoy_nu': '.03', 'decoy_strong_mu': '.15', 'send_probability': '.005'}.items():
            fields[key].setText(val)
        fields['postprocessing'].setCurrentText('finite_sns_v2')
        fields['tf_phase_slice_rad'].setText(str(.7853981633974483))
        commands = []
        screen.command.connect(lambda *args: commands.append(args))
        screen.submit()
        assert commands, screen.message.text()
        provider.save_communicator(commands[-1][1])
        screen.apply(provider.snapshot())
        stored = provider.snapshot().live.communicators['REVIEW-SNS']
        parameters = Communicator.from_dict(stored).parameters(('tf_qkd',))
        assert parameters['tf_estimation_mode'] == 'finite_sns_v2'
        assert parameters['decoy_mu'] == .03 and parameters['signal_mu'] == .15
        assert parameters['send_probability'] == .005 and parameters['tf_phase_slice_rad'] == pytest.approx(.7853981633974483)
        assert stored['orbit'] == {}
    finally:
        screen.shutdown()
        screen.close()
        screen.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_complete_demo_publishes_actual_orbital_senders_and_releases_renderers(app, provider):
    from quantum_autopilot.console.widgets.geo_view import GeoMap
    from quantum_autopilot.console.widgets.network_views import build_net_model
    for _ in range(8):
        provider.service.network_step()
    provider.refresh()
    snapshot = provider.snapshot()
    assert len(snapshot.topology.nodes) == 76
    assert len(snapshot.live.satellites) == 11
    session = snapshot.live.technologies['sessions'][SESSION_ID]
    assert session['counters']['observations'] > 0
    assert session['metrics']['certified_secret_bits'] == 0
    satellite_states = snapshot.live.satellites
    expected_senders = {LINK_IDS[0]: PATH[1], LINK_IDS[1]: PATH[1], LINK_IDS[2]: PATH[2]}
    publications = {}
    for node_id, state in satellite_states.items():
        for transmission in state.get('transmitting_links', ()):
            link_id = transmission['link_id']
            if link_id in expected_senders:
                assert expected_senders[link_id] == node_id
                bridge = next(row for row in state['bridges'] if row.get('link_id') == link_id)
                assert bridge['geometry']['photon_sender'] == node_id
                assert bridge['geometry']['photon_receiver'] == transmission['peer_id']
                raw_link = next(link for link in provider.service._network_engine.store.get(
                    provider.service._network_engine.topology_id).links if link.link_id == link_id)
                assert raw_link.left_node_id == node_id
                assert raw_link.right_node_id == transmission['peer_id']
                assert raw_link.channels['quantum']['receiver_id'] == transmission['peer_id']
                publications[link_id] = node_id
    assert publications == expected_senders
    assert not any(t['link_id'] == LINK_IDS[1] for t in satellite_states[PATH[2]]['transmitting_links'])
    geo = GeoMap()
    geo.resize(1280, 800)
    geo.show()
    model = build_net_model(snapshot, selection=('node', PATH[1]))
    try:
        geo.set_model(model)
        QTest.qWait(80)
        for _ in range(3):
            geo.set_mode('globe', focus_satellite=PATH[1])
            QTest.qWait(80)
            assert geo.mode == 'globe' and geo.globe is not None
            items = geo.globe.orbital_items()
            assert len(items) == 11
            assert all(geo.globe.hit_satellite(item['centre']) == item['id'] for item in items)
            assert set(geo.globe._orbit_paths) == set(satellite_states)
            assert set(geo.scene.net.satellites) == set(satellite_states)
            assert geo.scene.last_selection == ('node', PATH[1])
            assert geo.resource_stats()['renderer_instances'] == 1
            assert geo.resource_stats()['active_renderers'] == 1
            geo.set_mode('map')
            QTest.qWait(80)
            assert geo.mode == 'map' and geo.globe is None
            assert geo.scene.net is model and geo.scene.last_selection == ('node', PATH[1])
            assert geo.resource_stats()['renderer_instances'] == 1
            assert not geo.resource_stats()['orbit_timer_active']
    finally:
        geo.shutdown()
        geo.close()
        geo.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
