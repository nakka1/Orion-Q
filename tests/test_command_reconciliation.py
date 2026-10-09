"""Q04 evidence must remain read-only, snapshot-consistent and causally modest."""
from dataclasses import asdict, replace
import json
import sqlite3
from uuid import uuid4

import pytest

from quantum_autopilot.control_plane import command_reconciliation as reconciliation
from quantum_autopilot.control_plane.channels import local_pair
from quantum_autopilot.control_plane.console_commands import ConsoleCommandJournal, read_commands
from quantum_autopilot.control_plane.inventory import AddSimulatedNode, ConfigureSimulatedLink
from quantum_autopilot.control_plane.topology import (
    SimulatedTopologyStore, default_simulated_topology, _sha256,
)
from tools.reconcile_console_command import main


@pytest.fixture
def workspace(tmp_path):
    path = tmp_path / 'control.sqlite'
    store = SimulatedTopologyStore(path)
    journal = ConsoleCommandJournal(path)
    return path, store, journal


def node(**changes):
    return AddSimulatedNode(**dict(dict(node_id='extra', role='endpoint', expected_version=0,
                                       address='127.0.0.1', port=7411), **changes))


def link(**changes):
    return ConfigureSimulatedLink(**dict(dict(mode='create', link_id='direct', left_node_id='alice',
        right_node_id='bob', distance_km=12.5, nominal_pair_fidelity=.93, active=True,
        expected_version=0), **changes))


def begin(workspace, request=None, state='UNKNOWN'):
    path, _, journal = workspace
    request = node() if request is None else request
    verb = 'add_simulated_node' if isinstance(request, AddSimulatedNode) else 'configure_simulated_link'
    command_id = journal.begin(verb, asdict(request))
    if state in {'RUNNING', 'UNKNOWN', 'CONFIRMED'}:
        journal.transition(command_id, 'RUNNING')
    if state == 'UNKNOWN':
        journal.transition(command_id, state, dict(reason='execution_or_readback_failed', error_type='OSError'))
    if state == 'REJECTED':
        journal.transition(command_id, state, dict(reason='capability_revoked_before_dispatch'))
    if state == 'CONFIRMED':
        journal.transition(command_id, state, dict(topology_version=request.expected_version + 1, topology_sha256='a'*64))
    return command_id, read_commands(path)[0]['record_sha256']


@pytest.mark.parametrize('state', ['REQUESTED', 'RUNNING', 'UNKNOWN'])
@pytest.mark.parametrize('effect', ['absent', 'missing_target', 'matching', 'later', 'different'])
def test_observations_never_resolve_original_receipts(workspace, state, effect):
    path, store, _ = workspace
    selected = begin(workspace, state=state)
    if effect == 'missing_target':
        store.initialize(default_simulated_topology())
    elif effect != 'absent':
        store.add_inventory_node(node(port=7412) if effect == 'different' else node())
        if effect == 'later':
            store.add_inventory_node(node(node_id='other', expected_version=1))
    before = path.read_bytes()
    report = reconciliation.reconcile_command(path, *selected)
    assert path.read_bytes() == before
    record = report['record']
    assert report['record_sha256'] == _sha256(record)
    assert record['command'] == read_commands(path)[0]
    assert record['original_state'] == state
    expected_status = dict(absent='topology_absent', missing_target='target_absent',
        matching='matches_current_state', later='matches_current_state', different='differs_current_state')
    assert record['comparison']['target_status'] == expected_status[effect]
    assert record['comparison']['version_relation'] == dict(absent='unavailable', missing_target='at_expected',
        matching='at_expected_plus_one', later='later', different='at_expected_plus_one')[effect]
    assert record['comparison']['command_outcome'] == 'UNRESOLVED'
    assert record['comparison']['command_effect_proven'] is False
    assert record['commands_emitted'] == 0
    assert not any(record[k] for k in ('physical_action_performed', 'policy_activated', 'journal_mutated'))


@pytest.mark.parametrize('mode', ['create', 'update'])
@pytest.mark.parametrize('change', ['none', 'active', 'distance', 'fidelity', 'endpoints'])
def test_link_checks_all_requested_fields(workspace, mode, change):
    path, store, _ = workspace
    request = link()
    if mode == 'update':
        store.configure_inventory_link(request)
        request = replace(request, mode='update', expected_version=1, active=False)
    selected_request = replace(request, left_node_id='bob', right_node_id='alice',
                               channels=local_pair(request.link_id, 'alice')) if change == 'endpoints' else request
    selected = begin(workspace, selected_request)
    store.configure_inventory_link(request)
    if change not in {'none', 'endpoints'}:
        changes = dict(active=dict(active=not request.active), distance=dict(distance_km=12.6),
            fidelity=dict(nominal_pair_fidelity=.92), endpoints=dict(left_node_id='bob', right_node_id='alice'))
        store.configure_inventory_link(replace(request, mode='update',
            expected_version=request.expected_version+1, **changes[change]))
    report = reconciliation.reconcile_command(path, *selected)['record']
    assert report['comparison']['target_status'] == ('matches_current_state' if change == 'none' else 'differs_current_state')
    assert report['comparison']['command_outcome'] == 'UNRESOLVED'


def test_future_expected_version_does_not_imply_failure(workspace):
    path, store, _ = workspace
    selected = begin(workspace, node(expected_version=5))
    store.initialize(default_simulated_topology())
    result = reconciliation.reconcile_command(path, *selected)['record']['comparison']
    assert result['version_relation'] == 'before_expected'
    assert result['command_outcome'] == 'UNRESOLVED'


@pytest.mark.parametrize('state', ['CONFIRMED', 'REJECTED'])
def test_resolved_command_is_refused(workspace, state):
    selected = begin(workspace, state=state)
    with pytest.raises(ValueError, match='only unresolved'):
        reconciliation.reconcile_command(workspace[0], *selected)


def test_stale_selection_and_removed_receipt_refused(workspace):
    path, _, journal = workspace
    selected = begin(workspace, state='REQUESTED')
    journal.transition(selected[0], 'RUNNING')
    with pytest.raises(ValueError, match='changed since selection'):
        reconciliation.reconcile_command(path, *selected)
    with sqlite3.connect(path) as con:
        con.execute('DELETE FROM console_commands')
    with pytest.raises(ValueError, match='no longer exists'):
        reconciliation.reconcile_command(path, *selected)


@pytest.mark.parametrize('kind', ['command_hash', 'command_duplicate', 'command_large',
    'topology_version', 'topology_id', 'topology_stamp', 'topology_duplicate',
    'topology_nonfinite', 'topology_overflow', 'topology_large', 'topology_missing_field'])
def test_corruption_refused_without_export(workspace, tmp_path, kind):
    path, store, _ = workspace
    selected = begin(workspace)
    store.add_inventory_node(node())
    with sqlite3.connect(path) as con:
        raw = con.execute('SELECT state_json FROM simulated_topologies').fetchone()[0]
        if kind == 'command_hash':
            con.execute("UPDATE console_commands SET record_sha256=?", ('0'*64,))
        elif kind == 'command_duplicate':
            command = con.execute('SELECT record_json FROM console_commands').fetchone()[0]
            con.execute('UPDATE console_commands SET record_json=?', ('{"schema":"bad",'+command[1:],))
        elif kind == 'command_large':
            con.execute('UPDATE console_commands SET record_json=?', (' '*32769,))
        elif kind in {'topology_version', 'topology_id', 'topology_stamp'}:
            column, value = dict(topology_version=('topology_version', 99), topology_id=('state_json', raw.replace('sandbox-line-3','other')),
                topology_stamp=('updated_at_utc', '2026-01-01T00:00:00+00:00'))[kind]
            con.execute(f'UPDATE simulated_topologies SET {column}=?', (value,))
        else:
            if kind == 'topology_duplicate':
                raw = '{"topology_id":"other",'+raw[1:]
            elif kind in {'topology_nonfinite', 'topology_overflow'}:
                raw = raw.replace('"telemetry":{', '"telemetry":{"bad":'+('NaN' if kind=='topology_nonfinite' else '1e999')+',', 1)
            elif kind == 'topology_large':
                raw = ' ' * (reconciliation.MAX_TOPOLOGY_BYTES + 1)
            else:
                payload = json.loads(raw)
                del payload['nodes'][0]['telemetry']
                raw = json.dumps(payload)
            con.execute('UPDATE simulated_topologies SET state_json=?', (raw,))
    before = path.read_bytes()
    output = tmp_path / 'refused.json'
    with pytest.raises((ValueError, TypeError)):
        reconciliation.export_reconciliation(path, *selected, output)
    assert not output.exists() and path.read_bytes() == before


def test_missing_database_or_schema_never_initialized(tmp_path, workspace):
    missing = tmp_path / 'missing.sqlite'
    with pytest.raises(FileNotFoundError):
        reconciliation.reconcile_command(missing, str(uuid4()), 'a'*64)
    assert not missing.exists()
    path, _, _ = workspace
    before=path.read_bytes()
    with pytest.raises(sqlite3.OperationalError):
        reconciliation.reconcile_command(path, str(uuid4()), 'a'*64)
    assert path.read_bytes() == before
    selected = begin(workspace)
    with sqlite3.connect(path) as con:
        con.execute('DROP TABLE simulated_topologies')
    with pytest.raises(sqlite3.OperationalError):
        reconciliation.reconcile_command(path, *selected)


def test_one_wal_snapshot_and_next_read_observes_commit(workspace, monkeypatch):
    path, store, journal = workspace
    selected = begin(workspace, state='RUNNING')
    store.initialize(default_simulated_topology())
    with sqlite3.connect(path) as con:
        con.execute('PRAGMA journal_mode=WAL')
    original = reconciliation._read_topology
    def concurrent_commit(connection, topology_id):
        store.add_inventory_node(node())
        journal.transition(selected[0], 'UNKNOWN', dict(reason='execution_or_readback_failed', error_type='OSError'))
        return original(connection, topology_id)
    monkeypatch.setattr(reconciliation, '_read_topology', concurrent_commit)
    first = reconciliation.reconcile_command(path, *selected)['record']
    assert first['original_state'] == 'RUNNING'
    assert first['comparison']['target_status'] == 'target_absent'
    assert first['topology']['topology_version'] == 0
    monkeypatch.setattr(reconciliation, '_read_topology', original)
    with pytest.raises(ValueError, match='changed since selection'):
        reconciliation.reconcile_command(path, *selected)
    new_hash = read_commands(path)[0]['record_sha256']
    second = reconciliation.reconcile_command(path, selected[0], new_hash)['record']
    assert second['original_state'] == 'UNKNOWN'
    assert second['comparison']['target_status'] == 'matches_current_state'


def test_cli_export_and_no_overwrite(workspace, tmp_path, capsys):
    path, _, _ = workspace
    selected = begin(workspace)
    output = tmp_path / 'evidence.json'
    args = ['--journal', str(path), '--command-id', selected[0], '--command-sha256', selected[1], '--output', str(output)]
    before=path.read_bytes()
    assert main(args) == 0
    report=json.loads(output.read_text(encoding='utf-8'))
    assert report['record_sha256'] == _sha256(report['record'])
    assert json.loads(capsys.readouterr().out)['record_sha256'] == report['record_sha256']
    captured=output.read_bytes()
    with pytest.raises(SystemExit) as exc:
        main(args)
    assert exc.value.code == 2
    assert output.read_bytes() == captured and path.read_bytes() == before
    # Even an explicitly requested destination equal to the journal cannot overwrite it.
    with pytest.raises(FileExistsError):
        reconciliation.export_reconciliation(path, *selected, path)
    assert path.read_bytes() == before


@pytest.mark.parametrize('command_id,digest', [('bad', 'a'*64), (str(uuid4()), 'bad'),
    (str(uuid4()).upper(), 'a'*64), (str(uuid4()), 'A'*64)])
def test_invalid_selection_refused_before_read(workspace, command_id, digest):
    with pytest.raises(ValueError):
        reconciliation.reconcile_command(workspace[0], command_id, digest)


def test_connection_rejects_writes(workspace, monkeypatch):
    path, _, _ = workspace
    selected = begin(workspace)
    before = path.read_bytes()
    def attempt_write(connection, topology_id):
        connection.execute('CREATE TABLE forbidden(value)')
    monkeypatch.setattr(reconciliation, '_read_topology', attempt_write)
    with pytest.raises(sqlite3.OperationalError, match='readonly'):
        reconciliation.reconcile_command(path, *selected)
    assert path.read_bytes() == before
