"""Activate a threshold only in a NEW isolated diagnostic simulator; no physical action."""
import argparse
import hashlib
import json
from pathlib import Path

from quantum_autopilot.control_plane.policy_activation import ACTIVATION_CONFIRMATION
from quantum_autopilot.control_plane.policy_approval import APPROVAL_CONFIRMATION
from quantum_autopilot.control_plane.policy_preparation import POLICY_ID
from quantum_autopilot.control_plane.policy import RepeaterSandboxPolicy
from quantum_autopilot.control_plane.topology import default_simulated_topology
from quantum_autopilot.qkd.dashboard_api import AssuranceDashboardService


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', required=True, help='New isolated diagnostic directory; must not exist')
    args = parser.parse_args(argv)
    out = Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=False)
    service = AssuranceDashboardService(control_journal_path=out/'simulation.sqlite')
    runtime = service.controller_runtime
    service.topology_store.initialize(default_simulated_topology())
    context = service.policy_preparation_context()
    proposal = service.prepare_policy_change(dict(mode='simulation', policy_id=POLICY_ID, threshold=.7,
        optional_model_id=None, expected_context_sha256=context['record']['context_sha256']))
    review = service.review_policy_change(proposal['record']['proposal_id'], proposal['record_sha256'])
    approval = service.approve_policy_change(dict(mode='simulation', proposal_id=proposal['record']['proposal_id'],
        expected_proposal_sha256=proposal['record_sha256'], expected_review_sha256=review['record_sha256'],
        operator_label='automated diagnostic fixture', confirmation=APPROVAL_CONFIRMATION))
    assert runtime.policy.engine.threshold == .65
    request = dict(mode='simulation', approval_id=approval['record']['approval_id'],
                   expected_approval_sha256=approval['record_sha256'], confirmation=ACTIVATION_CONFIRMATION)
    result = service.activate_policy_change(request)
    assert runtime.policy.engine.threshold == .7
    target = out/'activation.json'
    service.export_policy_activation(result['record']['activation_id'], result['record_sha256'], target)
    checks = [dict(stage='activation', threshold_before=.65, threshold_after=.7, file=target.name,
                   sha256=hashlib.sha256(target.read_bytes()).hexdigest())]
    for stage in ('active', 'mismatch', 'restart'):
        if stage == 'mismatch':
            runtime.policy = RepeaterSandboxPolicy(.8)  # Test setup outside the activation API.
        if stage == 'restart':
            service = AssuranceDashboardService(control_journal_path=runtime.journal.path)
            assert service.controller_runtime.policy.engine.threshold == .65
        status = service.policy_activation_status()
        assert status['record']['status'] == {'active':'ACTIVE','mismatch':'UNVERIFIED','restart':'NO_LOCAL_ACTIVATION'}[stage]
        destination = out/(stage+'.json')
        destination.write_text(json.dumps(status,indent=2),encoding='utf-8')
        checks.append(dict(stage=stage,status=status['record']['status'],file=destination.name,
                           sha256=hashlib.sha256(destination.read_bytes()).hexdigest()))
    report = dict(passed=True, evidence='automated activation fixture in NEW isolated simulator', checks=checks,
                  real_operator_activation=False, policy_activated_in_fixture=True, optional_models_activated=False,
                  physical_action_performed=False, control_cycles_executed=0)
    assert runtime.journal.list_recent() == []
    (out/'report.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps(report,indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
