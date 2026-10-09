"""A stale or inconsistent laboratory report cannot authorize distribution."""
import importlib.util
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location('laboratory_release_gate', Path(__file__).parents[1] / 'tools/package_console_portable.py')
packager = importlib.util.module_from_spec(spec)
spec.loader.exec_module(packager)


@pytest.fixture
def report(tmp_path):
    exe = tmp_path / 'app.exe'
    exe.write_bytes(b'identified-executable-fixture')
    names = ('completed_pipeline', 'photon_campaign', 'continuous_capture',
             'independent_qkd_campaign', 'four_endpoint_application_processes',
             'uncertain_delivery_recovery', 'registered_replay_adapter',
             'replay_preserves_source_and_metrics', 'configuration_binding',
             'reconciled_receipts_after_restart', 'certified_secret_bits_zero',
             'physical_action_blocked')
    return exe, dict(schema='orion.laboratory-validation.v2', passed=True,
        frozen=True, full_campaign=True, checks=dict.fromkeys(names, True),
        executable_sha256=packager.digest(exe), certified_secret_bits=0,
        physical_action_performed=False, hardware_validated=False,
        native_swabian_replay_validated=False,
        security=dict(common_runtime=True, test_principals_explicitly_provisioned=True,
            tls13=True, authenticated_classical_protocol=dict(
                backup_rollback_protected=True, accepted_messages=3),
            audit=dict(valid=True, count=4, checkpoint_count=4, uncheckpointed_records=0)))


def test_approval_bound_to_frozen_executable(report):
    exe, data = report
    packager.validate_laboratory(data, exe)
    exe.write_bytes(b'another-executable')
    with pytest.raises(ValueError):
        packager.validate_laboratory(data, exe)


@pytest.mark.parametrize('field,value', [('passed', 1), ('frozen', False),
    ('full_campaign', False), ('checks', {}), ('checks', None), ('certified_secret_bits', 256),
    ('certified_secret_bits', False), ('physical_action_performed', True),
    ('hardware_validated', True), ('native_swabian_replay_validated', True)])
def test_inconsistent_scope_or_incomplete_campaign_rejected(report, field, value):
    exe, data = report
    data[field] = value
    with pytest.raises(ValueError):
        packager.validate_laboratory(data, exe)


@pytest.mark.parametrize('value', [False, 1, None])
def test_failed_detail_rejects_overall_passed_flag(report, value):
    exe, data = report
    data['checks']['uncertain_delivery_recovery'] = value
    with pytest.raises(ValueError):
        packager.validate_laboratory(data, exe)


@pytest.mark.parametrize('missing_security', [True, False])
def test_campaign_without_security_evidence_cannot_authorize_distribution(report, missing_security):
    exe, data = report
    if missing_security:
        data.pop('security')
    else:
        data['security'] = {}
    with pytest.raises(ValueError):
        packager.validate_laboratory(data, exe)


@pytest.mark.parametrize('field,value', [('backup_rollback_protected', False),
    ('backup_rollback_protected', None), ('accepted_messages', 0), ('accepted_messages', True)])
def test_classical_protocol_without_safe_rollback_or_accepted_messages_rejected(report, field, value):
    exe, data = report
    data['security']['authenticated_classical_protocol'][field] = value
    with pytest.raises(ValueError):
        packager.validate_laboratory(data, exe)


@pytest.mark.parametrize('field,value', [('valid', False), ('count', 0), ('count', True),
    ('checkpoint_count', 3), ('checkpoint_count', None), ('uncheckpointed_records', 1),
    ('uncheckpointed_records', None)])
def test_incomplete_audit_or_checkpoint_evidence_rejected(report, field, value):
    exe, data = report
    data['security']['audit'][field] = value
    with pytest.raises(ValueError):
        packager.validate_laboratory(data, exe)
