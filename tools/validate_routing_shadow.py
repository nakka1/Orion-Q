"""Routing bridge/offline parity on hand-authored simulated resources and features."""
import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path

from quantum_autopilot.control_plane.contracts import NetworkStateSnapshot
from quantum_autopilot.control_plane.optional_routing import (
    MODEL_IDS, RESOURCE_RULESET, TELEMETRY_KEY, WINDOW_SCHEMA, RoutingShadowWindow,
    evaluate_optional_routing, export_routing_shadow,
)
from quantum_autopilot.core import Provenance
from quantum_autopilot.model_intake.routing_session import (
    ACTION_NAMES, FEATURE_CONTRACT, KINDS, STATE_NAMES, evaluate_routing_session,
)


def demo_snapshot(seed=1234):
    origin = datetime(2026, 9, 24, tzinfo=timezone.utc)
    stamp = (origin + timedelta(seconds=10)).isoformat()
    expires = (origin + timedelta(seconds=100)).isoformat()
    pairs = [dict(pair_id=i, endpoints=ends, fidelity=.9, created_timestamp_utc=origin.isoformat(),
                  expires_timestamp_utc=expires, reserved=False)
             for i, ends in enumerate((['A', 'B'], ['A', 'B'], ['B', 'C'], ['A', 'C']), 1)]
    actions = [dict(kind=kind, link=['A', 'B'] if kind == 'GENERATE' else None,
                    pair_ids={'PURIFY': [1, 2], 'SWAP': [1, 3], 'DELIVER': [4], 'DISCARD': [2]}.get(kind, []),
                    node='B' if kind == 'SWAP' else None) for kind in KINDS]
    candidates = []
    for action in actions:
        features = dict.fromkeys(ACTION_NAMES, 0.)
        features['IS_' + action['kind']] = 1.
        candidates.append(dict(action_id=action['kind'].lower(), action=action, features=features))
    value = dict(schema=WINDOW_SCHEMA, evidence='simulated', feature_contract=FEATURE_CONTRACT,
                 source_id='hand-authored/routing', campaign_id='routing-fixture', stream_id='routing-fixture/stream',
                 node_id='B', clock_id='routing-virtual-clock', origin_timestamp_utc=origin.isoformat(),
                 decision_timestamp_utc=stamp, features_timestamp_utc=stamp, seed=seed,
                 state_features=dict.fromkeys(STATE_NAMES, 0.), candidates=candidates, reference_action_id='wait',
                 task=dict(endpoints=['A', 'C'], minimum_fidelity=.78),
                 resources=dict(ruleset=RESOURCE_RULESET, timestamp_utc=stamp, state_version=1,
                                enabled_kinds=list(KINDS), pairs=pairs,
                                nodes=[dict(node_id=n, memory_capacity=8, reserved_slots=0, available=True,
                                            can_swap=True, can_purify=True) for n in ('A', 'B', 'C')],
                                links=[dict(endpoints=ends, available=True)
                                       for ends in (['A', 'B'], ['B', 'C'], ['A', 'C'])]))
    window = RoutingShadowWindow.from_dict(value)
    return NetworkStateSnapshot(
        request_id=f'routing-fixture/{seed}', node_id='B', timestamp_utc=stamp, state_version=1,
        predicted_fidelity=.5, prediction_uncertainty=1., telemetry_age_s=0., memory_state='IDLE',
        integrity_verified=True, telemetry={TELEMETRY_KEY: window.to_dict()},
        provenance=Provenance('simulated', value['source_id'], timestamp=stamp),
        metadata=dict(campaign_id=value['campaign_id'], clock_id=value['clock_id'],
                      routing_stream_id=value['stream_id'], routing_origin_timestamp_utc=origin.isoformat(),
                      routing_resource_sha256=window.resource_sha256, routing_ruleset=RESOURCE_RULESET,
                      scope='Hand-authored features; software parity only, not calibrated simulator observations.'))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--qnet-zip', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(argv)
    args.output.mkdir(parents=False, exist_ok=False)
    checks = []
    for model_id in MODEL_IDS:
        for seed in (7, 1234):
            state = demo_snapshot(seed)
            before = state.to_json()
            result = evaluate_optional_routing(state, model_id=model_id, archive_path=args.qnet_zip)
            if result.status != 'evaluated':
                raise ValueError(result.reason + ': ' + result.detail)
            evaluation = json.loads(result.evaluation_json)
            offline = json.loads(evaluate_routing_session(model_id, args.qnet_zip,
                                                           evaluation['input_json']).serialized())
            del offline['evaluated_at_utc']
            comparison = dict(evaluation)
            del comparison['evaluated_at_utc']
            if (comparison != offline or state.to_json() != before
                    or any((result.resources_mutated, result.policy_activated, result.physical_action_performed,
                            result.simulator_action_performed))):
                raise ValueError('offline parity or read-only invariant failed')
            name = f'{model_id}-{seed}'
            export_routing_shadow(result, args.output / f'{name}.json')
            with (args.output / f'{name}-input.json').open('x', encoding='utf-8') as stream:
                stream.write(before)
            checks.append(dict(model_id=model_id, seed=seed, recommendation=evaluation['recommendation_action_id'],
                               archive_sha256=evaluation['archive_sha256'], resource_sha256=result.resource_sha256,
                               offline_parity=True, snapshot_unchanged=True,
                               report_sha256=hashlib.sha256(result.serialized().encode()).hexdigest()))
    with (args.output / 'report.json').open('x', encoding='utf-8') as stream:
        json.dump(dict(schema='orion.routing-shadow-diagnostic.v1', passed=True, checks=checks,
                       physical_action_performed=False, resources_mutated=False,
                       scope='Hand-authored features/resources; wiring parity only, no routing performance evidence.'),
                  stream, indent=2, allow_nan=False)
    print('4 routing checks passed; 2 checkpoints, 2 seeds, 6 candidate kinds; no resource consumption.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
