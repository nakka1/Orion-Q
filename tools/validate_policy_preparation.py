"""Exercise Q05 preparation in a new isolated simulator; never activates a policy."""
import argparse
import hashlib
import json
from pathlib import Path

from quantum_autopilot.control_plane.inventory import AddSimulatedNode
from quantum_autopilot.control_plane.policy_preparation import POLICY_ID
from quantum_autopilot.control_plane.topology import default_simulated_topology
from quantum_autopilot.qkd.dashboard_api import AssuranceDashboardService


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', required=True, help='New diagnostic directory; must not exist')
    args = parser.parse_args(argv)
    out = Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=False)
    service = AssuranceDashboardService(control_journal_path=out/'simulation.sqlite')
    runtime = service.controller_runtime
    service.topology_store.initialize(default_simulated_topology())
    original = runtime.policy
    context = service.policy_preparation_context()
    proposal = service.prepare_policy_change(dict(mode='simulation', policy_id=POLICY_ID, threshold=.7,
        optional_model_id=None, expected_context_sha256=context['record']['context_sha256']))
    key = proposal['record']['proposal_id'], proposal['record_sha256']
    checks = []
    for stage in ('current', 'topology_changed', 'source_changed'):
        if stage == 'topology_changed':
            service.topology_store.add_inventory_node(AddSimulatedNode('diagnostic','endpoint',0,'127.0.0.1',7411))
        before = runtime.journal.path.read_bytes()
        if stage == 'source_changed':
            service.invalidate_policy_preparation()
            try:
                service.review_policy_change(*key)
            except ValueError:
                checks.append(dict(stage=stage, refused=True, journal_unchanged=runtime.journal.path.read_bytes()==before))
            else:
                raise AssertionError('old source proposal accepted')
        else:
            target = out/(stage+'.json')
            result = service.export_policy_review(*key, target)
            assert result['record']['status'] == ('CURRENT' if stage=='current' else 'STALE')
            checks.append(dict(stage=stage, status=result['record']['status'], file=target.name,
                sha256=hashlib.sha256(target.read_bytes()).hexdigest(), journal_unchanged=runtime.journal.path.read_bytes()==before))
        assert runtime.policy is original and original.engine.threshold == .65
    assert all(item['journal_unchanged'] for item in checks)
    report = dict(passed=True, evidence='controlled software diagnostic in simulation', checks=checks,
                  policy_activated=False, physical_action_performed=False, commands_emitted_by_preparation=0)
    (out/'report.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps(report,indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
