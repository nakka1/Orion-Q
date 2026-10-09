"""Q04 inventory lifecycle, honest uncertainty, persistence and source boundaries."""
import json
import os
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, replace
from pathlib import Path

import pytest

from quantum_autopilot.control_plane.console_commands import ConsoleCommandJournal, read_commands
from quantum_autopilot.control_plane.journal import ControlJournal
from quantum_autopilot.control_plane.topology import _sha256
from tests import test_console_inventory as shared

app, provider, request = shared.app, shared.provider, shared.request


def states(path):
    return [e['state'] for e in read_commands(path)[0]['record']['events']]


@pytest.fixture
def journal(tmp_path):
    path = tmp_path / 'commands.sqlite'
    ControlJournal(path)
    return ConsoleCommandJournal(path)


def test_read_does_not_initialize_schema_or_missing_journal(journal, tmp_path):
    before = journal.path.read_bytes()
    assert read_commands(journal.path) == [] and journal.path.read_bytes() == before
    with pytest.raises(FileNotFoundError):
        read_commands(tmp_path / 'absent.sqlite')
    assert not (tmp_path / 'absent.sqlite').exists()


def test_lifecycle_survives_restart_and_terminal_is_final(journal):
    command = journal.begin('add_simulated_node', asdict(request()))
    assert states(journal.path) == ['REQUESTED']
    journal.transition(command, 'RUNNING')
    reopened = ConsoleCommandJournal(journal.path)
    assert states(reopened.path) == ['REQUESTED', 'RUNNING']
    reopened.transition(command, 'UNKNOWN', {'reason': 'execution_or_readback_failed', 'error_type': 'OSError'})
    before = journal.path.read_bytes()
    with pytest.raises(ValueError):
        reopened.transition(command, 'RUNNING')
    assert states(journal.path)[-1] == 'UNKNOWN' and journal.path.read_bytes() == before


@pytest.mark.parametrize('state', ['CONFIRMED', 'UNKNOWN', 'CANCELLED', 'REQUESTED'])
def test_invalid_transition_refused(journal, state):
    command = journal.begin('add_simulated_node', asdict(request()))
    with pytest.raises(ValueError):
        journal.transition(command, state)
    assert states(journal.path) == ['REQUESTED']


@pytest.mark.parametrize('limit', [True, 0, 51, '5'])
def test_bounded_history(journal, limit):
    with pytest.raises(ValueError):
        read_commands(journal.path, limit)


def test_invalid_requests_never_create_command_table(journal):
    before = journal.path.read_bytes()
    for verb, payload in [('swap', {}), ('add_simulated_node', dict(asdict(request()), token='secret'))]:
        with pytest.raises(ValueError):
            journal.begin(verb, payload)
    assert read_commands(journal.path) == [] and journal.path.read_bytes() == before


def test_history_order_limit_and_tampering(journal):
    ids = [journal.begin('add_simulated_node', asdict(request(node_id=f'N-{i}'))) for i in range(4)]
    assert [r['record']['command_id'] for r in read_commands(journal.path, 2)] == ids[-1:-3:-1]
    with sqlite3.connect(journal.path) as connection:
        connection.execute("UPDATE console_commands SET record_sha256='bad' WHERE command_id=?", (ids[-1],))
    with pytest.raises(ValueError, match='hash'):
        read_commands(journal.path)


@pytest.mark.parametrize('detail', [{}, {'topology_version': True, 'topology_sha256': 'a'*64},
    {'topology_version': 9, 'topology_sha256': 'a'*64}, {'topology_version': 1, 'topology_sha256': 'bad'}])
def test_confirmation_requires_bound_version_and_hash(journal, detail):
    command = journal.begin('add_simulated_node', asdict(request()))
    journal.transition(command, 'RUNNING')
    with pytest.raises(ValueError):
        journal.transition(command, 'CONFIRMED', detail)
    assert states(journal.path)[-1] == 'RUNNING'


def test_concurrent_terminal_transitions_have_one_winner(journal):
    command = journal.begin('add_simulated_node', asdict(request()))
    journal.transition(command, 'RUNNING')
    def finish(_):
        try:
            ConsoleCommandJournal(journal.path).transition(command, 'UNKNOWN', {'reason': 'execution_or_readback_failed', 'error_type': 'OSError'})
            return True
        except ValueError:
            return False
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(finish, range(2))) == [False, True]


def test_provider_confirmation_and_ui_read_only(provider, app, tmp_path):
    from quantum_autopilot.console.screens.audit import AuditScreen
    from quantum_autopilot.console.theme import install
    install(app)
    provider.dispatch('add_simulated_node', json.dumps(asdict(request())))
    path = provider.service.controller_runtime.journal.path
    assert states(path) == ['REQUESTED', 'RUNNING', 'CONFIRMED']
    item = read_commands(path)[0]
    topology = provider.service.topology_store.get(request().topology_id)
    assert item['record']['events'][-1]['detail']['topology_sha256'] == _sha256(topology.to_dict())
    before = path.read_bytes()
    snap = provider.build()
    assert path.read_bytes() == before
    view = AuditScreen()
    view.apply(snap)
    assert view.commands_button.isEnabled()
    dialog = view.command_history
    dialog.show()
    dialog.table.selectRow(0)
    assert item['record']['command_id'] in dialog.detail.toPlainText()
    assert 'CONFIRMED' in dialog.detail.toPlainText()
    for width, height in ((640, 500), (1080, 740)):
        dialog.resize(width, height)
        app.processEvents()
        assert dialog.detail.height() > 100
        if folder := os.environ.get('ORION_COMMAND_EVIDENCE'):
            Path(folder).mkdir(parents=True, exist_ok=True)
            assert dialog.grab().save(str(Path(folder) / f'commands-{width}.png'))
    from quantum_autopilot.console.capabilities import SourceCapabilities
    view.apply(replace(snap, input_kind='measured', capabilities=SourceCapabilities(mode='read_only')))
    assert not view.commands_button.isEnabled() and not dialog.detail.toPlainText() and not dialog.records
    assert path.read_bytes() == before
    dialog.close()
    view.close()


@pytest.mark.parametrize('failure', ['before', 'after', 'response', 'readback'])
def test_unknown_is_not_rollback_or_automatic_retry(provider, monkeypatch, failure):
    service = provider.service
    original = service.add_simulated_node
    calls = []
    def operation(payload):
        calls.append(1)
        if failure == 'before':
            raise OSError('injected before commit')
        result = original(payload)
        if failure == 'after':
            raise OSError('lost reply after commit')
        if failure == 'response':
            result['physical_action_performed'] = True
        if failure == 'readback':
            monkeypatch.setattr(service.topology_store, 'get', lambda *_: None)
        return result
    monkeypatch.setattr(service, 'add_simulated_node', operation)
    with pytest.raises(ValueError, match='UNKNOWN'):
        provider.dispatch('add_simulated_node', json.dumps(asdict(request())))
    assert calls == [1] and states(service.controller_runtime.journal.path)[-1] == 'UNKNOWN'
    if failure in {'after', 'response'}:
        assert service.topology_store.get(request().topology_id).topology_version == 1


@pytest.mark.parametrize('failure_state', ['REQUESTED', 'RUNNING', 'CONFIRMED'])
def test_journal_failure_never_claims_confirmation(provider, monkeypatch, failure_state):
    original_transition, original_begin = ConsoleCommandJournal.transition, ConsoleCommandJournal.begin
    def begin(self, *args):
        if failure_state == 'REQUESTED':
            raise OSError('journal unavailable')
        return original_begin(self, *args)
    def transition(self, command_id, state, detail=None):
        if state == failure_state:
            raise OSError('journal unavailable')
        return original_transition(self, command_id, state, detail)
    monkeypatch.setattr(ConsoleCommandJournal, 'begin', begin)
    monkeypatch.setattr(ConsoleCommandJournal, 'transition', transition)
    with pytest.raises(OSError):
        provider.dispatch('add_simulated_node', json.dumps(asdict(request())))
    path = provider.service.controller_runtime.journal.path
    if failure_state == 'REQUESTED':
        assert read_commands(path) == []
    else:
        assert states(path)[-1] == ('REQUESTED' if failure_state == 'RUNNING' else 'RUNNING')
    topology = provider.service.topology_store.get(request().topology_id)
    assert (topology is not None) == (failure_state == 'CONFIRMED')


def test_capability_revocation_before_dispatch_is_rejected(provider, monkeypatch):
    original = ConsoleCommandJournal.begin
    def begin(self, *args):
        command = original(self, *args)
        caps = provider.capabilities
        caps = replace(caps, operations=tuple(op for op in caps.operations if op != 'add_simulated_node'))
        monkeypatch.setattr(type(provider), 'capabilities', property(lambda _: caps))
        return command
    monkeypatch.setattr(ConsoleCommandJournal, 'begin', begin)
    with pytest.raises(ValueError, match='unavailable'):
        provider.add_simulated_node(json.dumps(asdict(request())))
    assert states(provider.service.controller_runtime.journal.path) == ['REQUESTED', 'REJECTED']
    assert provider.service.topology_store.get(request().topology_id) is None
