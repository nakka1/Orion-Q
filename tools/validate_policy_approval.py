"""Exercise explicit Q05 approval in a NEW diagnostic simulator, never activate."""
import argparse
import hashlib
import json
from pathlib import Path

from quantum_autopilot.control_plane.policy_approval import APPROVAL_CONFIRMATION
from quantum_autopilot.control_plane.policy_preparation import POLICY_ID
from quantum_autopilot.control_plane.policy import RepeaterSandboxPolicy
from quantum_autopilot.control_plane.topology import default_simulated_topology
from quantum_autopilot.qkd.dashboard_api import AssuranceDashboardService


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', required=True, help='New diagnostic directory; must not exist')
    args=parser.parse_args(argv)
    out=Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=False)
    service=AssuranceDashboardService(control_journal_path=out/'simulation.sqlite')
    runtime=service.controller_runtime
    service.topology_store.initialize(default_simulated_topology())
    before=runtime.journal.path.read_bytes()
    baseline=runtime.policy
    context=service.policy_preparation_context()
    proposal=service.prepare_policy_change(dict(mode='simulation', policy_id=POLICY_ID, threshold=.7,
        optional_model_id=None, expected_context_sha256=context['record']['context_sha256']))
    key=proposal['record']['proposal_id'],proposal['record_sha256']
    review=service.review_policy_change(*key)
    request=dict(mode='simulation', proposal_id=key[0], expected_proposal_sha256=key[1],
        expected_review_sha256=review['record_sha256'], operator_label='automated diagnostic fixture',
        confirmation=APPROVAL_CONFIRMATION)
    approval=service.approve_policy_change(request)
    target=out/'approval.json'
    service.export_policy_approval(approval['record']['approval_id'],approval['record_sha256'],target)
    assert approval['record']['operator_approval_recorded'] is True
    assert not approval['record']['activation_authority']
    assert runtime.policy is baseline and baseline.engine.threshold==.65
    checks=[dict(stage='recorded_fixture_approval',status=approval['record']['status'],file=target.name,
        sha256=hashlib.sha256(target.read_bytes()).hexdigest())]
    try:
        service.approve_policy_change(request)
    except ValueError:
        checks.append(dict(stage='duplicate',refused=True))
    else:
        raise AssertionError('duplicate approval accepted')
    review=service.review_policy_change(*key)
    request['expected_review_sha256']=review['record_sha256']
    runtime.policy=RepeaterSandboxPolicy(.8)  # Explicit diagnostic setup, not approval side effect.
    try:
        service.approve_policy_change(request)
    except ValueError:
        checks.append(dict(stage='context_changed',refused=True))
    else:
        raise AssertionError('changed policy accepted')
    assert runtime.policy.engine.threshold==.8
    service.invalidate_policy_preparation()
    try:
        service.export_policy_approval(approval['record']['approval_id'],approval['record_sha256'],out/'old.json')
    except ValueError:
        checks.append(dict(stage='session_invalidated',refused=True))
    else:
        raise AssertionError('invalidated approval exported')
    assert runtime.journal.path.read_bytes()==before
    report=dict(passed=True,evidence='automated fixture in isolated simulator, not a real operator approval',
        checks=checks,journal_unchanged=True,policy_activated_by_approval=False,physical_action_performed=False,
        commands_emitted_by_approval=0)
    (out/'report.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps(report,indent=2))
    return 0


if __name__=='__main__':
    raise SystemExit(main())
