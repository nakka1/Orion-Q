"""Acceptance waits follow backend readbacks and actual incremental widgets."""
from dataclasses import replace
import json
import os
from types import SimpleNamespace

import pytest


@pytest.fixture(scope='module')
def app():
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    from PySide6.QtWidgets import QApplication
    from quantum_autopilot.console import theme
    application = QApplication.instance() or QApplication([])
    theme.install(application)
    return application


def idle_window(**changes):
    """Only the production-owned progress attributes; no SDK reads or refresh."""
    active = SimpleNamespace(_future=None, _selection_futures=set())
    provider = SimpleNamespace(active=active, busy=False)
    nodes = SimpleNamespace(_pending_add=None, _pending_stage=None, _pending_network=None)
    topology = SimpleNamespace(_pending=None, link_editor=SimpleNamespace(pending=None),
                               node_windows=SimpleNamespace(windows={}),
                               _command_status=SimpleNamespace(text=''))
    for key, value in changes.items():
        setattr(nodes, key, value)
    return SimpleNamespace(provider=provider, screens={'node': nodes, 'topology': topology})


@pytest.mark.parametrize('owner,field', [('node', '_pending_add'), ('node', '_pending_stage'),
    ('node', '_pending_network'), ('topology', '_pending'), ('editor', 'pending')])
def test_idle_backend_is_not_a_configuration_readback(app, owner, field):
    from PySide6.QtCore import QTimer
    from quantum_autopilot.console.topology_validation import _settle
    window = idle_window()
    target = (window.screens['topology'].link_editor if owner == 'editor' else window.screens[owner])
    setattr(target, field, object())
    observed = []

    def received():
        observed.append('readback')
        setattr(target, field, None)

    QTimer.singleShot(20, received)
    _settle(window, timeout=1)
    assert observed == ['readback']


def test_selection_callback_is_received_before_following_operation(app):
    from PySide6.QtCore import QTimer
    from quantum_autopilot.console.topology_validation import _settle
    window = idle_window()
    job = object()
    window.provider.active._selection_futures.add(job)
    completed = []

    def receive_selection():
        completed.append('selection received')
        window.provider.active._selection_futures.remove(job)

    QTimer.singleShot(20, receive_selection)
    _settle(window, timeout=1)
    assert completed == ['selection received']


def test_unreceived_configuration_is_a_timeout_not_success(app):
    from quantum_autopilot.console.topology_validation import _settle
    window = idle_window(_pending_network=object())
    with pytest.raises(TimeoutError, match='node/link operation did not complete'):
        _settle(window, timeout=.015)
    assert window.screens['node']._pending_network is not None


@pytest.mark.parametrize('matching', [True, False])
def test_link_editor_waits_for_delayed_readback_and_preserves_refusal(app, matching):
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QPushButton
    from quantum_autopilot.console import topology_validation as journey
    from quantum_autopilot.console.contract import Link, Node
    from quantum_autopilot.console.synthetic import artificial_snapshot
    from quantum_autopilot.console.widgets.link_editor import LinkEditor
    from quantum_autopilot.control_plane.topology import DEFAULT_TOPOLOGY_ID

    peer = 'fixture-peer'
    link = Link(peer, journey.NODE_ID, journey.LINK_ID, id=journey.LINK_ID,
                distance_km=3.2, nominal_pair_fidelity=.93, configured_active=True)
    snap = artificial_snapshot(seed=7)
    snap = replace(snap, topology=replace(snap.topology, id=DEFAULT_TOPOLOGY_ID,
        version=0, nodes=[Node(peer, 'ENDPOINT'), Node(journey.NODE_ID, 'ENDPOINT')], links=[link]))
    editor = LinkEditor()
    editor.set_snapshot(snap)
    window = idle_window()
    topology = window.screens['topology']
    topology.link_editor = editor
    topology.inspector = SimpleNamespace(set_tab=lambda tab: None)
    topology.edit_links = QPushButton('Configure')
    topology.edit_links.clicked.connect(editor.begin)
    observations = []

    def submitted(verb, payload):
        assert verb == 'configure_simulated_link'
        request = json.loads(payload)
        assert request['active'] is False
        assert request['expected_version'] == 0

        def readback():
            assert editor.isVisible(), 'the acceptance harness closed a pending editor'
            assert editor.pending is not None
            changed = replace(link, configured_active=False if matching else True,
                              channels=request['channels'])
            editor.set_snapshot(replace(snap, topology=replace(snap.topology, version=1, links=[changed])))
            observations.append({'pending': editor.pending, 'note': editor.note.text()})

        QTimer.singleShot(20, readback)

    editor.command.connect(submitted)
    try:
        assert journey._configure_link(window, topology, app, peer, active=False, existing=True) is matching
        assert len(observations) == 1
        assert observations[0]['pending'] is None
        assert observations[0]['note'].startswith('CONFIRMED' if matching else 'UNCONFIRMED')
        assert editor.pending is None
        assert not editor.isVisible()
    finally:
        editor.close()
        editor.deleteLater()
        topology.edit_links.deleteLater()
        app.processEvents()


def test_resolved_waits_for_real_node_presentation_after_receipt(app, monkeypatch):
    from PySide6.QtCore import QTimer
    from quantum_autopilot.console import topology_validation as journey
    window = idle_window()
    topology = window.screens['topology']
    topology._pending = object()
    topology._snap, topology._net = object(), object()
    visible = SimpleNamespace(isVisible=lambda: True)
    hidden = SimpleNamespace(isVisible=lambda: False)
    topology.node_windows.windows = {'shown': visible, 'hidden': hidden}
    calls = []

    def confirm():
        topology._pending = None
        topology._command_status.text = 'CONFIRMED'

    def presentation(opened, snapshot, net, **options):
        assert topology._pending is None
        assert topology._command_status.text == 'CONFIRMED'
        assert opened is visible
        assert callable(snapshot) and snapshot() is topology._snap
        assert callable(net) and net() is topology._net
        calls.append(opened)
        return {'complete': True}

    monkeypatch.setattr(journey, 'wait_for_node_presentation', presentation)
    QTimer.singleShot(20, confirm)
    journey._resolved(window, topology, timeout=1)
    assert calls == [visible]


def test_each_presentation_uses_only_the_remaining_global_budget(app, monkeypatch):
    from quantum_autopilot.console import topology_validation as journey
    window = idle_window()
    topology = window.screens['topology']
    topology._snap, topology._net = object(), object()
    first = SimpleNamespace(isVisible=lambda: True)
    second = SimpleNamespace(isVisible=lambda: True)
    topology.node_windows.windows = {'first': first, 'second': second}
    clock = SimpleNamespace(now=8.75)
    monkeypatch.setattr(journey, 'monotonic', lambda: clock.now)
    budgets = []

    def presentation(opened, snapshot, net, *, timeout_ms):
        budgets.append(timeout_ms)
        clock.now = 9.75 if opened is first else 9.8
        return {'complete': True}

    monkeypatch.setattr(journey, 'wait_for_node_presentation', presentation)
    journey._wait_node_windows(window, deadline=10, timeout=10)
    assert budgets == [1250, 250]


def test_presentation_completion_after_original_deadline_is_rejected(app, monkeypatch):
    from quantum_autopilot.console import topology_validation as journey
    window = idle_window()
    topology = window.screens['topology']
    topology._snap, topology._net = object(), object()
    topology.node_windows.windows = {'shown': SimpleNamespace(isVisible=lambda: True)}
    clock = SimpleNamespace(now=0)
    monkeypatch.setattr(journey, 'monotonic', lambda: clock.now)
    budgets = []

    def presentation(opened, snapshot, net, *, timeout_ms):
        budgets.append(timeout_ms)
        clock.now = 10.001
        return {'complete': True}

    monkeypatch.setattr(journey, 'wait_for_node_presentation', presentation)
    with pytest.raises(TimeoutError, match='node/link operation did not complete'):
        journey._wait_node_windows(window, deadline=10, timeout=10)
    assert budgets == [5000]


def test_ready_idle_case_does_not_skip_an_expired_deadline(app, monkeypatch):
    from quantum_autopilot.console import topology_validation as journey
    window = idle_window()
    readings = iter((0, 1))
    monkeypatch.setattr(journey, 'monotonic', lambda: next(readings))
    with pytest.raises(TimeoutError, match='node/link operation did not complete'):
        journey._settle(window, timeout=1)


def test_synthetic_channels_read_back_and_allow_channel_only_updates(app, tmp_path):
    from dataclasses import asdict
    from pathlib import Path
    from quantum_autopilot.console.synthetic import SyntheticProvider
    from quantum_autopilot.control_plane.inventory import ConfigureSimulatedLink
    from quantum_autopilot.control_plane.channels import ChannelPair, ClassicalChannel, QuantumChannel

    provider = SyntheticProvider(Path(__file__).resolve().parents[1], tmp_path / 'developer-fixture', seed=7)
    node_id, link_id = 'channel-fixture-node', 'channel-fixture-link'
    provider.dispatch('stage_node', json.dumps({'id': node_id, 'role': 'ENDPOINT',
        'address': '127.0.0.1', 'port': 7411, 'protocol': 'QNET-TLS'}))
    pair = ChannelPair(link_id, ClassicalChannel(link_id + '.classical'),
                       QuantumChannel(link_id + '.quantum', node_id))
    request = ConfigureSimulatedLink('create', link_id, 'N-01', node_id, 3.2, .93, True,
                                    provider.snapshot().topology.version, channels=pair.to_dict())
    provider.dispatch('configure_simulated_link', json.dumps(asdict(request)))
    received = next(link for link in provider.snapshot().topology.links if link.id == link_id)
    assert received.channels == pair.to_dict()
    assert received.channels['classical']['channel_id'] != received.channels['quantum']['channel_id']
    changed_pair = ChannelPair(link_id, ClassicalChannel(link_id + '.control-v2'),
                               QuantumChannel(link_id + '.payload-v2', node_id))
    before_version = provider.snapshot().topology.version
    update = replace(request, mode='update', expected_version=before_version, channels=changed_pair.to_dict())
    provider.dispatch('configure_simulated_link', json.dumps(asdict(update)))
    assert provider.snapshot().topology.version == before_version + 1
    changed = next(link for link in provider.snapshot().topology.links if link.id == link_id)
    assert changed.channels == changed_pair.to_dict()
    assert (changed.distance_km, changed.nominal_pair_fidelity, changed.configured_active) == (3.2, .93, True)
    provider.advance()
    after_tick = next(link for link in provider.snapshot().topology.links if link.id == link_id)
    assert after_tick == changed
    unchanged = replace(update, expected_version=provider.snapshot().topology.version)
    with pytest.raises(ValueError, match='configuration unchanged'):
        provider.dispatch('configure_simulated_link', json.dumps(asdict(unchanged)))
    assert provider.snapshot().topology.version == before_version + 1
    assert next(link for link in provider.snapshot().topology.links if link.id == link_id) == changed
    provider.stop()
