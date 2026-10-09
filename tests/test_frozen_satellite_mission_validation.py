"""Release approval requires complete native mission evidence for the same EXE.

All subprocesses are mocked: these tests neither open a GUI nor receive RF.
"""
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import struct
import subprocess
import sys
from types import SimpleNamespace
import zlib

import pytest

from tools import package_console_portable as packager
from tools import validate_frozen_satellite_mission as checker


pytestmark = pytest.mark.unit

CASE_NAMES = (
    'radio_satellite_has_no_quantum_payload', 'native_buttons_provision_and_start',
    'original_frame_suomi100-frame-20261008-135420.bin',
    'original_frame_suomi100-frame-20261008-135520.bin',
    'tcp_clock_and_partial_disconnect', 'commit_before_lost_response',
    'identical_retry_without_duplicate', 'native_screen_receives_raw_eps_com_obc',
    'onboard_clock_and_units_unqualified', 'no_tracking_or_key_fabrication',
    'corrupted_crc_is_quarantined', 'wrong_satellite_is_quarantined',
    'anonymous_rejected', 'revoked_session_rejected', 'native_stop_keeps_receipts',
    'restart_keeps_receipts_without_listener', 'authenticated_audit_without_secrets',
)


def qualification():
    return {
        'schema': 'orion.satellite-mission-qualification.v1', 'passed': True,
        'frozen': True, 'platform': 'win32', 'qt_platform': 'windows',
        'origin': 'measurement_file', 'error_type': None,
        'live_local_rf_reception': False, 'physical_optical_qkd_qualified': False,
        'cases': [{'case': name, 'passed': True, 'evidence': None} for name in CASE_NAMES],
        'frame_hashes': [
            '4ae28a9e2a3871c7fe4d2fa7ee70ca253261cd377414f55731cbc7d4515fbc66',
            '464560d47534bebdfe99455aba43aa2c525ec000fbbcc2075136ed473b844d14'],
        'ingress': {
            'frame_count': 4, 'counts': {'received': 0, 'decoded': 2, 'quarantined': 2, 'unavailable': 0},
            'counters': {'accepted': 4, 'duplicates': 1, 'rejected': 0, 'sequence_gaps': 0},
            'pending_bytes': 0, 'persisted_bytes': 1000,
            'source_id': 'mission_gateway', 'origin': 'measurement_file',
            'state': 'quarantined', 'reason_code': 'mission_identity_mismatch',
            'classical_only': True, 'ogs_validated': False, 'qkd_validated': False,
            'physical_hardware_validated': False, 'physical_actuation': False,
            'latest': {
                'journal_id': 4, 'sequence': 3, 'state': 'quarantined',
                'reason_code': 'mission_identity_mismatch', 'decoded': None, 'fields_raw': None,
                'envelope': {'provenance': {'norad_cat_id': 25544}},
                'qualification': {'classical_only': True, 'decoded': False, 'ogs_validated': False,
                    'qkd_validated': False, 'physical_hardware_validated': False,
                    'local_rf_acquisition_validated': False}},
        },
        'outbox': {'acknowledged': 2}, 'topology_components': 30,
    }


def png():
    def chunk(kind, payload):
        return struct.pack('>I', len(payload)) + kind + payload + struct.pack('>I', zlib.crc32(kind + payload))
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', 1100, 720, 8, 2, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress((b'\x00' + b'\x00' * 3300) * 720)) + chunk(b'IEND', b''))


def fake_child(report=None, *, screen=None, mutate_exe=False):
    def invoke(exe, output):
        target = output / 'reception'
        target.mkdir()
        (target / 'report.json').write_text(json.dumps(report or qualification()), encoding='utf-8')
        if screen is not False:
            (target / 'native-mission-reception.png').write_bytes(png() if screen is None else screen)
        if mutate_exe:
            exe.write_bytes(b'changed executable during child execution')
    return invoke


@pytest.fixture
def gate(tmp_path, monkeypatch):
    exe = tmp_path / 'product' / 'ORION-Q.exe'
    exe.parent.mkdir()
    exe.write_bytes(b'fixture executable; never executed')
    output = tmp_path / 'mission-evidence'
    monkeypatch.setattr(checker, 'invoke', fake_child())
    report = checker.validate(exe, output)
    assert report['passed'] is True, report['error']
    return exe, output, report


def test_complete_gate_is_bound_to_executable_and_child_assets(gate):
    exe, output, report = gate
    packager.validate_satellite_mission(report, exe, evidence_root=output)
    assert report['independent_machine_verified'] is False
    assert [case['case'] for case in report['cases']] == list(CASE_NAMES)
    assert report['executable_sha256'] == report['executable_sha256_end'] == checker.digest(exe)


def test_packager_loaded_by_absolute_path_needs_no_tools_import_path(gate, tmp_path, monkeypatch):
    exe, output, report = gate
    spec = importlib.util.spec_from_file_location('external_qualification_packager', Path(packager.__file__))
    external = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(external)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, 'path', [])
    external.validate_satellite_mission(report, exe, evidence_root=output)


@pytest.mark.parametrize('field,value', [
    ('schema', 'wrong'), ('passed', False), ('passed', 1), ('frozen', False), ('frozen', 1),
    ('platform', 'linux'), ('qt_platform', 'offscreen'), ('origin', 'physical'),
    ('error_type', 'TimeoutError'), ('live_local_rf_reception', True),
    ('physical_optical_qkd_qualified', True), ('frame_hashes', []), ('topology_components', True),
    ('outbox', {'acknowledged': 2, 'pending': 1}),
])
def test_child_passed_flag_does_not_override_wrong_scope(field, value):
    report = qualification()
    report[field] = value
    with pytest.raises(ValueError):
        checker.verify_qualification(report)


@pytest.mark.parametrize('defect', ['missing', 'duplicate', 'extra', 'failed', 'integer', 'null'])
def test_all_named_cases_must_pass_exactly_once(defect):
    report = qualification()
    if defect == 'missing':
        report['cases'].pop()
    elif defect == 'duplicate':
        report['cases'][-1] = report['cases'][0]
    elif defect == 'extra':
        report['cases'].append({'case': 'unreviewed', 'passed': True})
    elif defect == 'failed':
        report['cases'][3]['passed'] = False
    elif defect == 'integer':
        report['cases'][3]['passed'] = 1
    else:
        report['cases'] = None
    with pytest.raises(ValueError):
        checker.verify_qualification(report)


@pytest.mark.parametrize('field,value', [
    ('frame_count', 3), ('frame_count', 4.0), ('counts', {'received': 0, 'decoded': 4, 'quarantined': 0, 'unavailable': 0}),
    ('counters', {'accepted': 4, 'duplicates': 0, 'rejected': 0, 'sequence_gaps': 0}),
    ('pending_bytes', 1), ('persisted_bytes', 0), ('qkd_validated', True),
    ('ogs_validated', True), ('physical_hardware_validated', True), ('physical_actuation', True),
    ('reason_code', 'invalid_checksum'),
])
def test_receipts_do_not_turn_quarantine_into_validated_data(field, value):
    report = qualification()
    report['ingress'][field] = value
    with pytest.raises(ValueError):
        checker.verify_qualification(report)


@pytest.mark.parametrize('defect', ['decoded', 'fields', 'reason', 'norad', 'sequence', 'qualified'])
def test_final_quarantine_exercises_binding_before_bad_crc(defect):
    report = qualification()
    latest = report['ingress']['latest']
    if defect == 'decoded':
        latest['decoded'] = {'payload': {'eps': {'batt_v': 7285}}}
    elif defect == 'fields':
        latest['fields_raw'] = {}
    elif defect == 'reason':
        latest['reason_code'] = 'invalid_checksum'
    elif defect == 'norad':
        latest['envelope']['provenance']['norad_cat_id'] = 43804
    elif defect == 'sequence':
        latest['sequence'] = 2
    else:
        latest['qualification']['qkd_validated'] = True
    with pytest.raises(ValueError):
        checker.verify_qualification(report)


@pytest.mark.parametrize('asset', ['executable', 'qualification', 'screen', 'wrapper_cases', 'checks', 'independent'])
def test_packager_rejects_stale_hashes_and_incomplete_evidence(gate, asset):
    exe, output, report = gate
    if asset == 'executable':
        exe.write_bytes(b'another executable')
    elif asset == 'qualification':
        (output / 'reception/report.json').write_text('{}', encoding='utf-8')
    elif asset == 'screen':
        (output / 'reception/native-mission-reception.png').write_bytes(b'corrupted PNG')
    elif asset == 'wrapper_cases':
        report['cases'] = deepcopy(report['cases'][:-1])
    elif asset == 'checks':
        report['checks']['child_exit_zero'] = 1
    else:
        report['independent_machine_verified'] = True
    with pytest.raises(ValueError):
        packager.validate_satellite_mission(report, exe, evidence_root=output)


@pytest.mark.parametrize('screen', [False, b'not a PNG', png()[:24], png()[:-1], png()[:60] + b'bad', png() + b'extra'],
                         ids=['absent', 'wrong-signature', 'header-only', 'truncated', 'bad-crc', 'extra-bytes'])
def test_gate_requires_complete_native_png(tmp_path, monkeypatch, screen):
    exe = tmp_path / 'product' / 'app.exe'
    exe.parent.mkdir()
    exe.write_bytes(b'fixture')
    monkeypatch.setattr(checker, 'invoke', fake_child(screen=screen))
    result = checker.validate(exe, tmp_path / 'evidence')
    assert result['passed'] is False
    assert result['error']


def test_executable_cannot_change_while_child_runs(tmp_path, monkeypatch):
    exe = tmp_path / 'product' / 'app.exe'
    exe.parent.mkdir()
    exe.write_bytes(b'fixture')
    monkeypatch.setattr(checker, 'invoke', fake_child(mutate_exe=True))
    result = checker.validate(exe, tmp_path / 'evidence')
    assert result['passed'] is False
    assert 'changed' in result['error']


def test_nonzero_child_exit_never_qualifies(tmp_path, monkeypatch):
    exe = tmp_path / 'product' / 'app.exe'
    exe.parent.mkdir()
    exe.write_bytes(b'fixture')
    def fail(*args):
        raise subprocess.CalledProcessError(2, ['fixture exe'])
    monkeypatch.setattr(checker, 'invoke', fail)
    assert checker.validate(exe, tmp_path / 'evidence')['passed'] is False


def test_gate_requires_new_output_outside_product(gate):
    exe, output, _ = gate
    with pytest.raises(FileExistsError):
        checker.validate(exe, output)
    with pytest.raises(ValueError):
        checker.validate(exe, exe.parent / 'state')


def test_invocation_uses_actual_exe_native_windows_and_isolated_cwd(tmp_path, monkeypatch):
    exe = tmp_path / 'ORION-Q.exe'
    exe.write_bytes(b'fixture; subprocess mocked')
    output = tmp_path / 'evidence'
    output.mkdir()
    calls = []
    monkeypatch.setattr(checker.sys, 'platform', 'win32')
    monkeypatch.setattr(checker.subprocess, 'STARTUPINFO', lambda: SimpleNamespace(dwFlags=0, wShowWindow=None), raising=False)
    monkeypatch.setattr(checker.subprocess, 'STARTF_USESHOWWINDOW', 1, raising=False)
    monkeypatch.setattr(checker.subprocess, 'CREATE_NO_WINDOW', 8, raising=False)
    monkeypatch.setattr(checker.subprocess, 'run', lambda command, **kwargs: calls.append((command, kwargs)))
    monkeypatch.setenv('PYTHONPATH', 'external python')
    monkeypatch.setenv('QT_PLUGIN_PATH', 'external Qt')
    monkeypatch.setenv('QT_QPA_PLATFORM', 'offscreen')
    monkeypatch.setenv('SYSTEMROOT', str(tmp_path / 'custom-system-root'))
    checker.invoke(exe, output)
    command, args = calls[0]
    assert command == [str(exe), '--satellite-mission-test', str(output / 'reception')]
    assert args['cwd'] == output / 'unrelated-working-directory'
    assert args['env']['QT_QPA_PLATFORM'] == 'windows'
    assert args['env']['PYINSTALLER_RESET_ENVIRONMENT'] == '1'
    assert 'PYTHONPATH' not in args['env'] and 'QT_PLUGIN_PATH' not in args['env']
    assert str(tmp_path / 'custom-system-root' / 'System32') in args['env']['PATH']
    assert args['timeout'] == 120 and args['check'] is True


def test_package_requires_mission_gate_before_writing_any_archive(tmp_path, monkeypatch):
    release = tmp_path / 'release'
    product = release / 'OrionQ'
    product.mkdir(parents=True)
    exe = product / 'OrionQ.exe'
    exe.write_bytes(b'fixture')
    worker = release / 'worker.json'
    worker.write_text(json.dumps({'passed': True, 'sha256': checker.digest(exe)}), encoding='utf-8')
    branding = release / 'branding-validation'
    branding.mkdir()
    (branding / 'branding-report.json').write_text('{}', encoding='utf-8')
    monkeypatch.setattr(packager, 'validate_branding', lambda *args, **kwargs: None)
    with pytest.raises(FileNotFoundError, match='satellite-mission-validation'):
        packager.package(release, worker)
    assert list(release.glob('*.zip')) == []


def test_preserved_frame_fixtures_match_reviewed_hashes():
    fixtures = Path(__file__).parent / 'fixtures/satellite_public'
    assert [checker.digest(fixtures / filename) for filename in (
        'suomi100-frame-20261008-135420.bin', 'suomi100-frame-20261008-135520.bin')
    ] == list(checker.FRAME_HASHES)
