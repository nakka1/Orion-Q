"""Four explicit allocation shadow checks on artificial fixed-plant snapshots."""
import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path

from quantum_autopilot.control_plane.contracts import NetworkStateSnapshot
from quantum_autopilot.control_plane.optional_allocation import (
    MODEL_IDS, TELEMETRY_KEY, AllocationDriftSample, AllocationSample, AllocationShadowWindow,
    evaluate_optional_allocation, export_allocation_shadow,
)
from quantum_autopilot.core import Provenance
from quantum_autopilot.model_intake.allocation_session import demo_allocation_input, evaluate_allocation_session
from quantum_autopilot.model_intake.artifacts import ALLOCATION_ROOT, ReviewedArchive


def demo_snapshot(plant_sha256, history_count):
    if type(history_count) is not int or not 0 <= history_count <= 5:
        raise ValueError("history_count must be 0..5")
    data = json.loads(demo_allocation_input(MODEL_IDS[1]))
    origin = datetime(2026, 9, 24, tzinfo=timezone.utc)
    obs = data['observation']
    stamp = (origin + timedelta(seconds=obs.pop('time_s'))).isoformat()
    history = tuple(AllocationDriftSample((origin + timedelta(seconds=t)).isoformat(), row)
                    for t, row in zip(data['history_times_s'], data['drift_history']))
    window = AllocationShadowWindow(
        'hand-authored/allocation', 'allocation-fixture', 'allocation-fixture/stream', 'allocation-node',
        'allocation-virtual-clock', plant_sha256, origin.isoformat(), AllocationSample(stamp, **obs),
        history[-history_count:] if history_count else ())
    return NetworkStateSnapshot(
        request_id=f'allocation-fixture/{history_count}', node_id=window.node_id, timestamp_utc=stamp,
        state_version=history_count, predicted_fidelity=.5, prediction_uncertainty=1., telemetry_age_s=0.,
        memory_state='IDLE', integrity_verified=True, telemetry={TELEMETRY_KEY: window.to_dict()},
        provenance=Provenance('simulated', window.source_id, timestamp=stamp),
        metadata={'campaign_id': window.campaign_id, 'clock_id': window.clock_id,
                  'allocation_stream_id': window.stream_id, 'allocation_plant_sha256': window.plant_sha256,
                  'allocation_origin_timestamp_utc': window.origin_timestamp_utc,
                  'scope': 'hand-authored fixture; no measured data, certified domain or fidelity inference'})


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--qnet-zip', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(argv)
    args.output.mkdir(parents=False, exist_ok=False)
    archive = ReviewedArchive(args.qnet_zip, 'qnet')
    plant = hashlib.sha256(archive.read(ALLOCATION_ROOT + 'model/plant.json')).hexdigest()
    checks = []
    for model_id in MODEL_IDS:
        for count in (0, 5):
            state = demo_snapshot(plant, count)
            before = state.to_json()
            result = evaluate_optional_allocation(state, model_id=model_id, archive_path=args.qnet_zip)
            if result.status != 'evaluated':
                raise ValueError(result.reason + ': ' + result.detail)
            evaluation = json.loads(result.evaluation_json)
            offline = json.loads(evaluate_allocation_session(model_id, args.qnet_zip,
                                                             evaluation['input_json']).serialized())
            expected = 'persistence' if model_id == MODEL_IDS[0] else 'ridge' if count == 5 else 'warmup_persistence'
            if (result.final_output_status != expected or state.to_json() != before
                    or evaluation['recommendation'] != offline['recommendation']
                    or evaluation['persistence'] != offline['persistence']
                    or any((result.queue_mutated, result.policy_activated, result.physical_action_performed,
                            result.simulator_action_performed))):
                raise ValueError('shadow/offline parity or non-actuation invariant failed')
            name = f'{model_id}-{count}'
            export_allocation_shadow(result, args.output / f'{name}.json')
            with (args.output / f'{name}-input.json').open('x', encoding='utf-8') as stream:
                stream.write(before)
            checks.append({'model_id': model_id, 'history_samples': count, 'status': result.final_output_status,
                           'archive_sha256': evaluation['archive_sha256'], 'plant_sha256': plant,
                           'offline_parity': True, 'snapshot_unchanged': True,
                           'report_sha256': hashlib.sha256(result.serialized().encode()).hexdigest()})
    report = {'schema': 'orion.allocation-shadow-diagnostic.v1', 'passed': True, 'checks': checks,
              'scope': 'software wiring parity on artificial inputs; not control gain or delivered keys',
              'physical_action_performed': False, 'policy_activated': False, 'queue_mutated': False}
    with (args.output / 'report.json').open('x', encoding='utf-8') as stream:
        json.dump(report, stream, indent=2, allow_nan=False)
    print('4 checks passed; persistence/Ridge, warm-up/full history; no actuation or queue mutation.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
