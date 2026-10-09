"""Validate approved rollback and uncertain-commit recovery in a NEW isolated simulator."""
import argparse
import hashlib
import json
from pathlib import Path
from unittest.mock import patch

from quantum_autopilot.control_plane import policy_activation as activation
from quantum_autopilot.control_plane.policy_approval import APPROVAL_CONFIRMATION
from quantum_autopilot.control_plane.policy_preparation import POLICY_ID
from quantum_autopilot.control_plane.policy_recovery import RECOVERY_CONFIRMATION
from quantum_autopilot.control_plane.topology import default_simulated_topology
from quantum_autopilot.qkd.dashboard_api import AssuranceDashboardService


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', required=True, help='New diagnostic directory; must not exist')
    out = Path(parser.parse_args(argv).output_dir)
    out.mkdir(parents=True, exist_ok=False)
    service = AssuranceDashboardService(control_journal_path=out/'simulation.sqlite')
    runtime = service.controller_runtime
    service.topology_store.initialize(default_simulated_topology())
    checks = []

    def save(stage, receipt):
        target = out/(stage+'.json')
        with target.open('x', encoding='utf-8') as handle:
            json.dump(receipt, handle, indent=2)
        checks.append(dict(stage=stage, file=target.name, sha256=hashlib.sha256(target.read_bytes()).hexdigest()))

    def candidate():
        context = service.policy_preparation_context()
        return service.prepare_policy_change(dict(mode='simulation', policy_id=POLICY_ID, threshold=.7,
            optional_model_id=None, expected_context_sha256=context['record']['context_sha256']))

    def approve(proposal):
        review = service.review_policy_change(proposal['record']['proposal_id'], proposal['record_sha256'])
        approval = service.approve_policy_change(dict(mode='simulation', proposal_id=proposal['record']['proposal_id'],
            expected_proposal_sha256=proposal['record_sha256'], expected_review_sha256=review['record_sha256'],
            operator_label='automated diagnostic fixture', confirmation=APPROVAL_CONFIRMATION))
        return dict(mode='simulation', approval_id=approval['record']['approval_id'],
            expected_approval_sha256=approval['record_sha256'], confirmation=activation.ACTIVATION_CONFIRMATION)

    first = service.activate_policy_change(approve(candidate()))
    proposal = service.prepare_policy_rollback(first['record']['activation_id'], first['record_sha256'])
    assert runtime.policy.engine.threshold == .7
    save('rollback_prepared', proposal)
    rolled = service.activate_policy_change(approve(proposal))
    assert runtime.policy.engine.threshold == .65 and rolled['record']['transition_kind'] == 'rollback'
    save('rollback_committed', rolled)
    retained = runtime.policy
    request = approve(candidate())

    def uncertain_commit(connection):
        connection.commit()
        raise RuntimeError('diagnostic injection: commit persisted but response lost')

    # Injection is restricted to this standalone diagnostic process and its new SQLite fixture.
    with patch.object(activation, '_commit', uncertain_commit):
        try:
            service.activate_policy_change(request)
        except RuntimeError as exc:
            assert 'diagnostic injection' in str(exc)
        else:
            raise AssertionError('fault injection did not run')
    assert service.policy_activation_status()['record']['status'] == 'UNRESOLVED'
    context = service.policy_recovery_context()
    assert context['record']['context']['failed_record_observation'] == 'present_and_verified'
    save('recovery_review', context)
    recovered = service.recover_policy_change(dict(mode='simulation', fault_id=context['record']['context']['fault_id'],
        expected_recovery_sha256=context['record_sha256'], operator_label='automated diagnostic fixture',
        confirmation=RECOVERY_CONFIRMATION))
    assert runtime.policy is retained and retained.engine.threshold == .65
    assert not recovered['record']['policy_replaced']
    assert service.policy_activation_status()['record']['status'] == 'ACTIVE'
    save('recovery_committed', recovered)
    assert runtime.journal.list_recent() == []
    report = dict(passed=True, evidence='automated rollback/recovery fixture in NEW isolated simulator', checks=checks,
        real_operator_activation=False, policy_activated_in_fixture=True, optional_models_activated=False,
        physical_action_performed=False, control_cycles_executed=0, retained_threshold=.65)
    (out/'report.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
