"""Read-only observations of unresolved inventory commands; never confirms execution."""
from contextlib import closing
from datetime import datetime, timezone
import json
from pathlib import Path
import re
from uuid import UUID

from .campaign_read_store import ReadOnlyCampaignStore
from .console_commands import _decode
from .geodesy import link_geodesic_km, node_location
from .inventory import (AddNetworkNode, AddSimulatedNode, ConfigureSimulatedLink, RecalibrateNode,
                        SetLinkActive, SetNodeIsolation, SetNodeLocation, SimulatedNodeAction)
from .topology import SimulatedTopology, _sha256

MAX_TOPOLOGY_BYTES = 2 * 1024 * 1024


def _strict_json(raw):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('duplicate JSON field')
            result[key] = value
        return result

    def invalid(value):
        raise ValueError('non-finite JSON number')

    result = json.loads(raw, object_pairs_hook=unique, parse_constant=invalid)
    # Also catches finite-looking literals that overflow a float, such as 1e999.
    json.dumps(result, allow_nan=False)
    return result


def _read_command(connection, command_id, expected_sha256):
    row = connection.execute(
        'SELECT command_id, substr(record_json, 1, 32769) AS record_json, '
        'length(CAST(record_json AS BLOB)) AS size, record_sha256 '
        'FROM console_commands WHERE command_id=?', (command_id,)).fetchone()
    if row is None:
        raise ValueError('command no longer exists')
    if row['size'] > 32768:
        raise ValueError('command record exceeds 32 KiB')
    _strict_json(row['record_json'])
    command = _decode(row)
    if command['record_sha256'] != expected_sha256:
        raise ValueError('command changed since selection; refresh before checking')
    if command['record']['events'][-1]['state'] not in {'REQUESTED', 'RUNNING', 'UNKNOWN'}:
        raise ValueError('only unresolved inventory commands can be reconciled')
    return command


def _read_topology(connection, topology_id):
    row = connection.execute(
        'SELECT topology_id, topology_version, updated_at_utc, '
        'substr(state_json, 1, ?) AS state_json, length(CAST(state_json AS BLOB)) AS size '
        'FROM simulated_topologies WHERE topology_id=?',
        (MAX_TOPOLOGY_BYTES + 1, topology_id)).fetchone()
    if row is None:
        return None
    if row['size'] > MAX_TOPOLOGY_BYTES:
        raise ValueError('topology exceeds 2 MiB')
    payload = _strict_json(row['state_json'])
    topology = SimulatedTopology.from_dict(payload)
    if (topology.to_dict() != payload or topology.topology_id != row['topology_id']
            or topology.topology_version != row['topology_version']
            or topology.updated_at_utc != row['updated_at_utc']):
        raise ValueError('topology index/schema mismatch')
    return payload


def _declared(location):
    """The operator-declared part of a location; the timestamp is not requested."""
    return None if location is None else {key: location[key] for key in ('latitude', 'longitude', 'place')}


def _comparison(record, topology):
    request = record['request']
    expected_version = request['expected_version']
    verb = record['verb']

    def node(node_id):
        return next((item for item in topology['nodes'] if item['node_id'] == node_id), None) if topology else None

    def link(link_id):
        return next((item for item in topology['links'] if item['link_id'] == link_id), None) if topology else None

    def control(target):
        value = target['telemetry'].get('console_control', {})
        return value if isinstance(value, dict) else {}

    if verb == 'add_simulated_node':
        typed = AddSimulatedNode.from_dict(request)
        expected = {'node_id': typed.node_id, 'role': typed.role,
                    'inventory': typed.node().telemetry['inventory']}
        target = node(typed.node_id)
        observed = None if target is None else {
            'node_id': target['node_id'], 'role': target['role'],
            'inventory': target['telemetry'].get('inventory')}
    elif verb == 'add_network_node':
        typed = AddNetworkNode.from_dict(request)
        expected = {'node_id': typed.node_id, 'role': typed.role,
                    'inventory': typed.node('1970-01-01T00:00:00+00:00').telemetry['inventory'],
                    'location': _declared(typed.location(None))}
        target = node(typed.node_id)
        observed = None if target is None else {
            'node_id': target['node_id'], 'role': target['role'],
            'inventory': target['telemetry'].get('inventory'),
            'location': _declared(node_location(target))}
    elif verb == 'set_node_location':
        typed = SetNodeLocation.from_dict(request)
        expected = {'node_id': typed.node_id, 'location': _declared(typed.location(None))}
        target = node(typed.node_id)
        observed = None if target is None else {
            'node_id': target['node_id'], 'location': _declared(node_location(target))}
    elif verb == 'set_node_isolation':
        typed = SetNodeIsolation.from_dict(request)
        expected = {'node_id': typed.node_id, 'isolated': typed.isolated}
        target = node(typed.node_id)
        observed = None if target is None else {
            'node_id': target['node_id'], 'isolated': control(target).get('isolated') is True}
    elif verb == 'set_link_active':
        typed = SetLinkActive.from_dict(request)
        expected = {'link_id': typed.link_id, 'active': typed.active}
        target = link(typed.link_id)
        observed = None if target is None else {'link_id': target['link_id'], 'active': target['active']}
    elif verb in {'apply_simulated_node_action', 'recalibrate_node'}:
        typed = (SimulatedNodeAction if verb == 'apply_simulated_node_action' else RecalibrateNode).from_dict(request)
        expected = {'node_id': typed.node_id, 'last_command_sha256': _sha256(request)}
        target = node(typed.node_id)
        observed = None if target is None else {'node_id': target['node_id'],
            'last_command_sha256': control(target).get('last_command_sha256')}
    else:
        typed = ConfigureSimulatedLink.from_dict(request)
        expected = typed.link().to_dict()
        # Between two located nodes the store persists the geodesic length, not the
        # requested figure (design §1); compare against what a correct save holds.
        geodesic = None if topology is None else link_geodesic_km(topology, expected)
        if geodesic is not None:
            expected['distance_km'] = geodesic
        observed = link(typed.link_id)
    version = None if topology is None else topology['topology_version']
    relation = ('unavailable' if version is None else
                'before_expected' if version < expected_version else
                'at_expected' if version == expected_version else
                'at_expected_plus_one' if version == expected_version + 1 else 'later')
    return dict(
        target_status='topology_absent' if topology is None else
                      'target_absent' if observed is None else
                      'matches_current_state' if observed == expected else 'differs_current_state',
        expected_fields=expected, observed_fields=observed,
        expected_version=expected_version, observed_version=version, version_relation=relation,
        command_effect_proven=False, command_outcome='UNRESOLVED',
        interpretation='Current state is an observation, not evidence that this command executed. '
                       'Matching, absent or different state cannot establish success or failure.')


def reconcile_command(path, command_id, expected_sha256):
    """Read one selected receipt and current topology in one SQLite snapshot.

    The caller must supply the hash shown at selection. The original journal is
    never changed, even if the desired fields match at exactly expected + 1.
    Missing/corrupt schema is an error, not evidence of an absent topology.
    """
    if not isinstance(command_id, str) or str(UUID(command_id)) != command_id:
        raise ValueError('canonical command UUID required')
    if not isinstance(expected_sha256, str) or not re.fullmatch('[0-9a-f]{64}', expected_sha256):
        raise ValueError('selected command SHA-256 required')
    store = ReadOnlyCampaignStore(path)
    with closing(store._connect()) as connection:
        connection.execute('BEGIN')
        command = _read_command(connection, command_id, expected_sha256)
        topology = _read_topology(connection, command['record']['request']['topology_id'])
        observed_at = datetime.now(timezone.utc).isoformat()
    report = dict(
        schema='orion.command_reconciliation.v1', observed_at_utc=observed_at,
        evidence='simulation', command=command,
        original_state=command['record']['events'][-1]['state'],
        topology=topology, topology_sha256=None if topology is None else _sha256(topology),
        comparison=_comparison(command['record'], topology),
        commands_emitted=0, physical_action_performed=False,
        journal_mutated=False, policy_activated=False)
    return {'record': report, 'record_sha256': _sha256(report)}


def export_reconciliation(path, command_id, expected_sha256, destination):
    """Capture once, then export to a new file; never overwrites or replays."""
    report = reconcile_command(path, command_id, expected_sha256)
    raw = json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False) + '\n'
    with Path(destination).open('x', encoding='utf-8', newline='\n') as stream:
        stream.write(raw)
    return report
