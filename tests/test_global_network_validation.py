"""Acceptance scope and executable evidence are explicit, including failures."""
import hashlib
import json
import sys

import pytest

from quantum_autopilot.network_engine.global_validation import (
    FULL_OPERATIONAL_SCENARIOS, _executable_identity, _resource_summary, validate_global_network,
)

pytestmark = pytest.mark.unit


@pytest.mark.parametrize('steps', [True, 5, 121, 6.0, '6'])
def test_validation_rejects_invalid_steps_before_creating_artifacts(tmp_path, steps):
    with pytest.raises(ValueError, match='steps must'):
        validate_global_network(tmp_path / 'invalid', steps=steps)
    assert not (tmp_path / 'invalid').exists()


@pytest.mark.parametrize('full', [None, 0, 1, 'False'])
def test_validation_requires_boolean_scope(tmp_path, full):
    with pytest.raises(ValueError, match='full must'):
        validate_global_network(tmp_path / 'invalid', full=full)
    assert not (tmp_path / 'invalid').exists()


def test_frozen_identity_hashes_actual_running_executable(tmp_path, monkeypatch):
    executable = tmp_path / 'ORION-Q.exe'
    executable.write_bytes(b'acceptance executable evidence\x00\xff')
    monkeypatch.setattr(sys, 'frozen', True, raising=False)
    monkeypatch.setattr(sys, 'executable', str(executable))
    assert _executable_identity() == {
        'frozen': True,
        'executable_sha256': hashlib.sha256(executable.read_bytes()).hexdigest(),
    }
    executable.write_bytes(b'different executable bytes')
    assert _executable_identity()['executable_sha256'] == hashlib.sha256(executable.read_bytes()).hexdigest()


def test_source_execution_is_not_reported_as_frozen(monkeypatch):
    monkeypatch.delattr(sys, 'frozen', raising=False)
    assert _executable_identity() == {'frozen': False, 'executable_sha256': None}


def test_resource_summary_distinguishes_session_bank_local_equipment_and_rates():
    def component(ident, memory, pair, pulse):
        return {'component': ident, 'simulation_resource_allocation': {'banks': [{
            'resource_bank_id': 'sim-resource:SESSION', 'memory_modes': memory,
            'pair_sources': pair, 'pulse_sources': pulse, 'optical_terminals': 1,
        }], 'totals': {'memory_modes': memory}}}
    summary = _resource_summary({'SESSION': {'config': {'parameters': {
        'pair_source_hz': 20000., 'pulse_rate_hz': 10000000., 'attempt_rate_hz': 250000.,
    }}}}, [component('A', 4, 1, 0), component('B', 8, 0, 1)])
    assert summary['protocol_session_banks'] == 1
    assert summary['local_device_allocations'] == 2
    assert summary['totals'] == {'memory_modes': 12, 'pair_sources': 1, 'pulse_sources': 1, 'optical_terminals': 2}
    assert summary['declared_source_rates_hz_by_session']['SESSION'] == {
        'pair_source_hz': 20000., 'pulse_rate_hz': 10000000., 'attempt_rate_hz': 250000.,
    }
    assert summary['physical_capacity_validated'] is False
    assert summary['shared_hardware_arbitration_implemented'] is False


def test_quick_failed_acquisition_retains_honest_scope_and_error(tmp_path, monkeypatch):
    from quantum_autopilot.qkd import dashboard_api
    monkeypatch.delattr(sys, 'frozen', raising=False)
    def unavailable(**kwargs):
        raise RuntimeError('acquisition unavailable for scope regression')
    monkeypatch.setattr(dashboard_api, 'demo_service', unavailable)
    result = validate_global_network(tmp_path / 'quick', steps=6, full=False)
    assert result['mode'] == 'quick'
    assert result['full_campaign'] is False
    assert result['full_operational_scenarios_executed'] is False
    assert result['skipped_scenarios'] == list(FULL_OPERATIONAL_SCENARIOS)
    assert result['passed'] is False
    assert 'acquisition unavailable' in result['failed_checks'][0]['detail']['exception']
    assert json.loads((tmp_path / 'quick/report.json').read_text(encoding='utf-8')) == result
    assert 'rápido (escopo parcial)' in (tmp_path / 'quick/report.md').read_text(encoding='utf-8')
