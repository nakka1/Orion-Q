"""Independent worker frames preserve order and their own data photograph."""
from __future__ import annotations

from concurrent.futures import Future
from dataclasses import replace

import pytest

pytest.importorskip('PySide6.QtWidgets')
from PySide6.QtWidgets import QApplication  # noqa: E402

from quantum_autopilot.console.contract import CommandView, LiveNode, LiveView, Node  # noqa: E402
from quantum_autopilot.console.input_sources import empty_snapshot  # noqa: E402
from quantum_autopilot.console.service_provider import ServiceProvider  # noqa: E402

pytestmark = pytest.mark.unit


class FixtureService:
    def health(self):
        return {'status': 'ok'}

    def wdm_model_status(self):
        return {}


@pytest.fixture
def provider():
    app = QApplication.instance() or QApplication([])
    instance = ServiceProvider(FixtureService())
    yield instance
    # No executor/stepper was started. Keep QObject deletion on the Qt thread.
    instance.deleteLater()
    app.processEvents()


def committed_fixture(provider, tick, version, *, node='A', label='ordered-fixture'):
    payload = {'topology': {'topology_id': 'fixed-topology', 'topology_version': version,
        'nodes': [{'node_id': ident, 'role': 'endpoint', 'state_version': version,
                   'memory_state': 'IDLE', 'updated_at_utc': '2026-10-07T12:00:00+00:00'}
                  for ident in ('A', 'B')]}}
    live = LiveView(tick, float(tick), 'simulated', label,
                    nodes=tuple(LiveNode(ident, 'endpoint', 'node', 'up') for ident in ('A', 'B')),
                    at=f'2026-10-07T12:00:{version:02d}+00:00', topology_version=version,
                    source_seed=17, source_scenario='fixed-fixture')
    base = empty_snapshot('fixture', 'no hardware/no scientific execution', 'demo')
    topology = replace(base.topology, id='fixed-topology', version=version,
                       nodes=[Node(ident, 'ENDPOINT') for ident in ('A', 'B')])
    snap = replace(base, topology=topology, live=live,
                   node=provider._node(payload, [], live, selected_node_id=node))
    inputs = ({'status': 'ok'}, payload, [])
    return snap, inputs


def place_cache(provider, snap, inputs):
    # Represents the completed refresh/cache commit, not a device/source update.
    with provider._snap_lock:
        provider._snap, provider._last_inputs = snap, inputs


def finished(value):
    future = Future()
    future.set_result(value)
    return future


def service_delivery(provider, snap):
    future = finished(snap)
    provider._future = future
    provider._on_completed(future)


def selection_delivery(provider, snap, generation=1):
    provider._selection_generation = generation
    future = finished((generation, snap))
    provider._selection_futures.add(future)
    provider._on_selection_completed(future)


def test_delayed_latest_selection_cannot_replace_newer_committed_service_frame(provider):
    frames = []
    provider.snapshot_changed.connect(frames.append)
    old, old_inputs = committed_fixture(provider, 10, 1)
    place_cache(provider, old, old_inputs)
    delayed_selection = provider._prepare_publication()
    # The selection generation is current; a different worker publishes later.
    newer, new_inputs = committed_fixture(provider, 11, 2)
    place_cache(provider, newer, new_inputs)
    service_delivery(provider, provider._prepare_publication())
    selection_delivery(provider, delayed_selection)
    assert frames[0].live.tick == 11
    assert frames[-1].live.tick == 11, 'same-origin old selection frame replaced the newer frame'


def test_delayed_service_callback_cannot_replace_newer_committed_selection_frame(provider):
    frames = []
    provider.snapshot_changed.connect(frames.append)
    old, old_inputs = committed_fixture(provider, 10, 1)
    place_cache(provider, old, old_inputs)
    delayed_service = provider._prepare_publication()
    newer, new_inputs = committed_fixture(provider, 11, 2)
    place_cache(provider, newer, new_inputs)
    selection_delivery(provider, provider._prepare_publication())
    service_delivery(provider, delayed_service)
    assert frames[0].live.tick == 11
    assert frames[-1].live.tick == 11, 'same-origin old service frame replaced the newer frame'


def test_latest_selected_node_is_mapped_from_the_inputs_of_its_own_photograph(provider):
    old, old_inputs = committed_fixture(provider, 10, 1)
    place_cache(provider, old, old_inputs)
    delayed = provider._prepare_publication()
    newer, new_inputs = committed_fixture(provider, 11, 2)
    place_cache(provider, newer, new_inputs)
    provider._selection_requested_node = 'B'
    projected = provider._presentation_frame(delayed)
    assert projected.node.id == 'B'
    state_version = next(item.value for item in projected.node.equipment if item.key == 'STATE VERSION')
    assert state_version == str(projected.live.topology_version), (
        'node uses the latest topology inputs while live/topology belong to an earlier photograph')


def test_ordered_same_origin_reset_may_reduce_tick_without_becoming_an_old_delivery(provider):
    frames = []
    provider.snapshot_changed.connect(frames.append)
    old, old_inputs = committed_fixture(provider, 10, 1)
    place_cache(provider, old, old_inputs)
    service_delivery(provider, provider._prepare_publication())
    reset, reset_inputs = committed_fixture(provider, 0, 2)
    place_cache(provider, reset, reset_inputs)
    service_delivery(provider, provider._prepare_publication())
    assert [frame.live.tick for frame in frames] == [10, 0]
    assert frames[-1].live.topology_version == 2


def test_actual_build_keeps_cached_inputs_associated_until_refresh_finishes(monkeypatch):
    from copy import deepcopy
    from tests.test_console_service_provider import FakeService
    service = FakeService()
    app = QApplication.instance() or QApplication([])
    instance = ServiceProvider(service)
    instance.refresh()
    old_snapshot, old_inputs = instance._snap, instance._last_inputs
    newer_payload = deepcopy(service.control_plane_topology())
    newer_payload['topology']['nodes'][0]['state_version'] = 77
    monkeypatch.setattr(service, 'control_plane_topology', lambda: newer_payload)
    observations = []
    original = instance._node
    def inspect_association(*args, **kwargs):
        observations.append((instance._snap, instance._last_inputs))
        return original(*args, **kwargs)
    monkeypatch.setattr(instance, '_node', inspect_association)
    try:
        instance.refresh()
        assert observations and observations[0][0] is old_snapshot
        assert observations[0][1] is old_inputs
        assert instance._last_inputs[1] is newer_payload
        assert next(item.value for item in instance._snap.node.equipment
                    if item.key == 'STATE VERSION') == '77'
        assert instance._snap is not old_snapshot
    finally:
        instance.deleteLater()
        app.processEvents()


def test_older_in_progress_build_cannot_overwrite_newer_readback(provider, monkeypatch):
    old, old_inputs = committed_fixture(provider, 10, 1)
    place_cache(provider, old, old_inputs)
    newer, new_inputs = committed_fixture(provider, 11, 2)
    def delayed_build():
        # Another full readback finished its later ticket while this build was
        # in progress. No selection or scientific operation is being cancelled.
        epoch, ticket = provider._begin_read()
        with provider._snap_lock:
            assert epoch == provider._publication_epoch
            provider._snap, provider._last_inputs = newer, new_inputs
            provider._committed_read_sequence = ticket
            provider._committed_full_read_sequence = ticket
        provider._build_context.inputs = old_inputs
        return old
    monkeypatch.setattr(provider, 'build', delayed_build)
    provider.refresh()
    assert provider._snap is newer and provider._last_inputs is new_inputs
    assert provider._presentation_stats['coalesced_publication_frames'] == 1


@pytest.mark.parametrize('verb', ['set_node_isolation', 'set_link_active'])
def test_live_only_read_during_full_command_readback_cannot_discard_receipt_or_inventory(
        provider, monkeypatch, verb):
    from quantum_autopilot.console import service_provider
    old, old_inputs = committed_fixture(provider, 10, 1)
    old_inputs[1]['topology']['nodes'][0]['telemetry'] = {'console_control': {'isolated': True}}
    old = replace(old, topology=replace(old.topology,
        nodes=[replace(node, isolated=node.id == 'A') for node in old.topology.nodes]))
    place_cache(provider, old, old_inputs)
    complete, complete_inputs = committed_fixture(provider, 11, 2)
    complete_inputs[1]['topology']['nodes'][0]['telemetry'] = {'console_control': {'isolated': False}}
    receipt = CommandView('fixture-command', verb, 'CONFIRMED', '2026-10-07T12:00:02+00:00', '{}')
    complete = replace(complete, ledger=replace(complete.ledger, commands=(receipt,)))
    command_future = Future()
    provider._future = command_future
    monkeypatch.setattr(provider.service, 'network_live_state',
        lambda: {'tick': 11, 'at_utc': complete.live.at}, raising=False)
    monkeypatch.setattr(service_provider, 'live_view_from_state', lambda state, payload: complete.live)
    light_frames = []
    frames = []
    provider.snapshot_changed.connect(frames.append)

    def readback_interleaved_with_live_read():
        # Exercise the real lightweight collector after the complete read has
        # taken its ticket. That collector intentionally preserves old receipts
        # and inventory inputs while updating the live engine photograph.
        light = provider._collect_live_publication(command_future)
        assert light is not None
        light_frames.append(light)
        assert light.snapshot.ledger.commands == ()
        assert light.snapshot.topology.nodes[0].isolated is True
        provider._build_context.inputs = complete_inputs
        return complete

    monkeypatch.setattr(provider, 'build', readback_interleaved_with_live_read)
    provider.refresh()
    assert light_frames and provider._committed_read_sequence == 2
    assert provider._committed_full_read_sequence == 1
    assert provider._snap.ledger.commands == (receipt,)
    assert provider._snap.topology.version == 2
    assert provider._snap.topology.nodes[0].isolated is False
    assert provider._last_inputs is complete_inputs
    assert frames[-1].ledger.commands == (receipt,)
    assert frames[-1].topology.version == 2


def test_stop_invalidates_old_epoch_without_changing_snapshot_contract(provider):
    frames = []
    provider.snapshot_changed.connect(frames.append)
    snap, inputs = committed_fixture(provider, 10, 1)
    place_cache(provider, snap, inputs)
    delayed = provider._prepare_publication()
    provider.stop()
    assert not provider._deliver_publication(delayed)
    assert not frames
    reset, reset_inputs = committed_fixture(provider, 0, 2)
    place_cache(provider, reset, reset_inputs)
    provider.publish_snapshot()
    assert frames[-1].live.tick == 0
    assert provider.snapshot().live.tick == 0


@pytest.mark.parametrize('latest_tick', [11, 0])
def test_full_receipts_merge_with_newer_engine_revision_including_reset(provider, monkeypatch, latest_tick):
    old, old_inputs = committed_fixture(provider, 10, 1)
    old = replace(old, live=replace(old.live, engine_session_id='a' * 32, publication_revision=10))
    receipt = CommandView('merge-command', 'set_link_active', 'CONFIRMED', old.live.at, '{}')
    complete = replace(old, ledger=replace(old.ledger, commands=(receipt,)))
    newer, new_inputs = committed_fixture(provider, latest_tick, 1)
    newer = replace(newer, live=replace(newer.live, engine_session_id='a' * 32, publication_revision=11))
    place_cache(provider, old, old_inputs)
    frames = []
    provider.snapshot_changed.connect(frames.append)

    def interleaved_build():
        place_cache(provider, newer, new_inputs)
        provider.publish_snapshot()
        provider._build_context.inputs = old_inputs
        return complete

    monkeypatch.setattr(provider, 'build', interleaved_build)
    provider.refresh()
    assert [frame.live.tick for frame in frames] == [latest_tick, latest_tick]
    assert provider._snap.live.publication_revision == 11
    assert provider._snap.ledger.commands == (receipt,)
    assert provider._last_inputs is old_inputs
    assert frames[-1].ledger.commands == (receipt,)


def test_new_engine_session_is_not_merged_with_old_metrics(provider, monkeypatch):
    old, old_inputs = committed_fixture(provider, 10, 1)
    old = replace(old, live=replace(old.live, engine_session_id='a' * 32, publication_revision=100))
    fresh, fresh_inputs = committed_fixture(provider, 0, 1)
    fresh = replace(fresh, live=replace(fresh.live, engine_session_id='b' * 32, publication_revision=1))
    place_cache(provider, old, old_inputs)
    monkeypatch.setattr(provider, 'build', lambda: fresh)
    provider._last_inputs = fresh_inputs
    provider.refresh()
    assert provider._snap.live.engine_session_id == 'b' * 32
    assert provider._snap.live.tick == 0


def test_old_full_batch_cannot_restore_an_engine_replaced_by_newer_live_read(provider, monkeypatch):
    old, old_inputs = committed_fixture(provider, 10, 1)
    old = replace(old, live=replace(old.live, engine_session_id='a' * 32, publication_revision=10))
    fresh, fresh_inputs = committed_fixture(provider, 0, 1)
    fresh = replace(fresh, live=replace(fresh.live, engine_session_id='b' * 32, publication_revision=1))
    place_cache(provider, old, old_inputs)

    def late_old_engine_build():
        _, newer_ticket = provider._begin_read()
        place_cache(provider, fresh, fresh_inputs)
        provider._committed_read_sequence = newer_ticket
        provider._build_context.inputs = old_inputs
        return old

    monkeypatch.setattr(provider, 'build', late_old_engine_build)
    provider.refresh()
    assert provider._snap is fresh and provider._last_inputs is fresh_inputs


def test_removed_selection_is_not_injected_from_another_photograph(provider):
    snap, inputs = committed_fixture(provider, 10, 1)
    inputs[1]['topology']['nodes'] = inputs[1]['topology']['nodes'][:1]
    snap = replace(snap, topology=replace(snap.topology, nodes=snap.topology.nodes[:1]))
    place_cache(provider, snap, inputs)
    delayed = provider._prepare_publication()
    provider._selection_requested_node = 'B'
    projected = provider._presentation_frame(delayed)
    assert projected.node.id == 'A'
    assert projected.topology.nodes[0].id == 'A'
    assert projected.live.topology_version == 1

