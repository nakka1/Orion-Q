"""Qualify classical mission reception in the actual native Windows executable.

Preserved public RF frames pass through an emulated loopback TCP station. This
local software gate does not attest local RF acquisition or physical QKD.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import zlib


SCHEMA = 'orion.frozen-satellite-mission-check.v1'
QUALIFICATION_SCHEMA = 'orion.satellite-mission-qualification.v1'
FRAME_HASHES = (
    '4ae28a9e2a3871c7fe4d2fa7ee70ca253261cd377414f55731cbc7d4515fbc66',
    '464560d47534bebdfe99455aba43aa2c525ec000fbbcc2075136ed473b844d14',
)
EXPECTED_CASES = frozenset((
    'radio_satellite_has_no_quantum_payload',
    'native_buttons_provision_and_start',
    'original_frame_suomi100-frame-20261008-135420.bin',
    'original_frame_suomi100-frame-20261008-135520.bin',
    'tcp_clock_and_partial_disconnect', 'commit_before_lost_response',
    'identical_retry_without_duplicate', 'native_screen_receives_raw_eps_com_obc',
    'onboard_clock_and_units_unqualified', 'no_tracking_or_key_fabrication',
    'corrupted_crc_is_quarantined', 'wrong_satellite_is_quarantined',
    'anonymous_rejected', 'revoked_session_rejected', 'native_stop_keeps_receipts',
    'restart_keeps_receipts_without_listener', 'authenticated_audit_without_secrets',
))
EXPECTED_CHECKS = frozenset((
    'child_exit_zero', 'all_mission_cases', 'frozen_windows_native_qt',
    'preserved_public_frame_hashes', 'mission_receipts_consistent',
    'no_rf_or_qkd_claim', 'native_screen_asset', 'unchanged_executable',
))


def digest(path):
    value = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(chunk)
    return value.hexdigest()


def verify_qualification(report):
    """Check the complete child contract, independently of its overall flag."""
    if (not isinstance(report, dict) or report.get('schema') != QUALIFICATION_SCHEMA
            or report.get('passed') is not True or report.get('frozen') is not True
            or report.get('platform') != 'win32' or report.get('qt_platform') != 'windows'
            or report.get('origin') != 'measurement_file' or report.get('error_type') is not None
            or report.get('live_local_rf_reception') is not False
            or report.get('physical_optical_qkd_qualified') is not False
            or report.get('frame_hashes') != list(FRAME_HASHES)):
        raise ValueError('mission qualification scope or frame identity is invalid')
    cases = report.get('cases')
    if (not isinstance(cases, list) or len(cases) != len(EXPECTED_CASES)
            or any(not isinstance(case, dict) or case.get('passed') is not True
                   or not isinstance(case.get('case'), str) for case in cases)
            or {case['case'] for case in cases} != EXPECTED_CASES):
        raise ValueError('mission qualification cases are incomplete or failed')
    ingress = report.get('ingress')
    if (not isinstance(ingress, dict) or type(ingress.get('frame_count')) is not int
            or ingress['frame_count'] != 4
            or ingress.get('counts') != {'received': 0, 'decoded': 2, 'quarantined': 2, 'unavailable': 0}
            or any(type(value) is not int for value in ingress['counts'].values())
            or ingress.get('source_id') != 'mission_gateway'
            or ingress.get('origin') != 'measurement_file'
            or ingress.get('state') != 'quarantined'
            or ingress.get('reason_code') != 'mission_identity_mismatch'
            or ingress.get('classical_only') is not True
            or any(ingress.get(key) is not False for key in
                   ('ogs_validated', 'qkd_validated', 'physical_hardware_validated', 'physical_actuation'))
            or report.get('outbox') != {'acknowledged': 2}
            or type(report['outbox']['acknowledged']) is not int
            or type(report.get('topology_components')) is not int or report['topology_components'] <= 0):
        raise ValueError('mission receipts or software-only qualifications are inconsistent')
    counters = ingress.get('counters')
    if (counters != {'accepted': 4, 'duplicates': 1, 'rejected': 0, 'sequence_gaps': 0}
            or any(type(value) is not int for value in counters.values())
            or type(ingress.get('pending_bytes')) is not int
            or ingress['pending_bytes'] != 0 or type(ingress.get('persisted_bytes')) is not int
            or ingress['persisted_bytes'] <= 0):
        raise ValueError('mission retry counters are inconsistent')
    latest = ingress.get('latest')
    if (not isinstance(latest, dict) or latest.get('state') != 'quarantined'
            or latest.get('reason_code') != 'mission_identity_mismatch'
            or type(latest.get('journal_id')) is not int or latest['journal_id'] != 4
            or type(latest.get('sequence')) is not int or latest['sequence'] != 3
            or latest.get('decoded') is not None or latest.get('fields_raw') is not None
            or not isinstance(latest.get('qualification'), dict)
            or latest['qualification'].get('classical_only') is not True
            or any(latest['qualification'].get(key) is not False for key in
                   ('decoded', 'ogs_validated', 'qkd_validated', 'physical_hardware_validated',
                    'local_rf_acquisition_validated'))):
        raise ValueError('latest quarantined frame cannot claim qualified mission data')
    envelope = latest.get('envelope', {})
    provenance = envelope.get('provenance', {}) if isinstance(envelope, dict) else {}
    if (not isinstance(provenance, dict) or type(provenance.get('norad_cat_id')) is not int
            or provenance['norad_cat_id'] != 25544):
        raise ValueError('latest quarantine does not exercise an incorrect mission NORAD binding')


def _load(path):
    path = Path(path)
    if path.stat().st_size > 2 * 1024 * 1024:
        raise ValueError('mission evidence report exceeds the bound')
    return json.loads(path.read_text(encoding='utf-8'),
                      parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))


def _native_screen(path):
    path = Path(path)
    if not 24 <= path.stat().st_size <= 16 * 1024 * 1024:
        raise ValueError('native mission screenshot is absent or oversized')
    data = path.read_bytes()
    header = data[:24]
    if header[:16] != b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR':
        raise ValueError('native mission screenshot is not a PNG')
    width, height = struct.unpack('>II', header[16:24])
    if not 100 <= width <= 10000 or not 100 <= height <= 10000:
        raise ValueError('native mission screenshot dimensions are invalid')
    offset, seen_image = 8, False
    while offset + 12 <= len(data):
        length = struct.unpack('>I', data[offset:offset + 4])[0]
        end = offset + 12 + length
        if end > len(data):
            break
        kind = data[offset + 4:offset + 8]
        payload = data[offset + 8:end - 4]
        crc = struct.unpack('>I', data[end - 4:end])[0]
        if zlib.crc32(kind + payload) != crc or offset == 8 and (kind != b'IHDR' or length != 13):
            raise ValueError('native mission PNG checksum or header is invalid')
        if kind == b'IDAT' and length:
            seen_image = True
        if kind == b'IEND' and not length and seen_image and end == len(data):
            return
        offset = end
    raise ValueError('native mission PNG is incomplete')


def verify_gate(report, exe, *, evidence_root):
    """Revalidate executable binding and disk assets before packaging."""
    sha = digest(exe)
    if (not isinstance(report, dict) or report.get('schema') != SCHEMA
            or report.get('passed') is not True or report.get('independent_machine_verified') is not False
            or report.get('executable_sha256') != sha or report.get('executable_sha256_end') != sha
            or report.get('error') is not None or report.get('checks') != dict.fromkeys(EXPECTED_CHECKS, True)
            or any(value is not True for value in report['checks'].values())):
        raise ValueError('frozen mission gate does not approve this executable')
    record = report.get('qualification')
    verify_qualification(record)
    if report.get('cases') != record['cases']:
        raise ValueError('frozen mission gate case evidence differs from the qualification')
    root = Path(evidence_root).resolve(strict=True)
    qualification_path = root / 'reception/report.json'
    screen_path = root / 'reception/native-mission-reception.png'
    for path in (qualification_path, screen_path):
        if path.is_symlink() or path.resolve(strict=True).parent != root / 'reception':
            raise ValueError('mission evidence assets must remain within the isolated gate directory')
    if (report.get('qualification_sha256') != digest(qualification_path)
            or record != _load(qualification_path)
            or report.get('native_screen_sha256') != digest(screen_path)):
        raise ValueError('mission evidence asset hash mismatch')
    _native_screen(screen_path)


def invoke(exe, output, *, timeout=120):
    """Run only the packaged program, with native Qt and no Python assistance."""
    if sys.platform != 'win32':
        raise RuntimeError('the frozen satellite mission gate requires Windows')
    environment = dict(os.environ, QT_QPA_PLATFORM='windows', QT_SCALE_FACTOR='1')
    for key in list(environment):
        if key.upper() in {'PYTHONPATH', 'PYTHONHOME', 'QT_PLUGIN_PATH', 'QT_QPA_PLATFORM_PLUGIN_PATH'}:
            del environment[key]
    system = environment.get('SYSTEMROOT', environment.get('SystemRoot', r'C:\Windows'))
    environment['PATH'] = os.pathsep.join((str(Path(system) / 'System32'), system))
    environment['PYINSTALLER_RESET_ENVIRONMENT'] = '1'
    cwd = output / 'unrelated-working-directory'
    cwd.mkdir()
    startup = subprocess.STARTUPINFO()
    startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startup.wShowWindow = 0
    with (output / 'mission.stdout.log').open('xb') as stdout, (output / 'mission.stderr.log').open('xb') as stderr:
        subprocess.run([str(exe), '--satellite-mission-test', str(output / 'reception')],
                       cwd=cwd, env=environment, stdout=stdout, stderr=stderr, timeout=timeout,
                       check=True, startupinfo=startup, creationflags=subprocess.CREATE_NO_WINDOW)


def validate(exe, output):
    exe, output = Path(exe).resolve(strict=True), Path(output).resolve()
    if not exe.is_file() or output == exe.parent or exe.parent in output.parents:
        raise ValueError('mission gate needs an executable and new output outside its package')
    output.mkdir(parents=True, exist_ok=False)
    report = {'schema': SCHEMA, 'passed': False, 'executable': str(exe),
              'executable_sha256': digest(exe), 'executable_sha256_end': None,
              'independent_machine_verified': False, 'checks': {}, 'cases': [], 'error': None}
    try:
        invoke(exe, output)
        report['checks']['child_exit_zero'] = True
        record = _load(output / 'reception/report.json')
        verify_qualification(record)
        report.update(qualification=record, cases=record['cases'],
                      qualification_sha256=digest(output / 'reception/report.json'))
        for name in ('all_mission_cases', 'frozen_windows_native_qt', 'preserved_public_frame_hashes',
                     'mission_receipts_consistent', 'no_rf_or_qkd_claim'):
            report['checks'][name] = True
        screen = output / 'reception/native-mission-reception.png'
        _native_screen(screen)
        report['native_screen_sha256'] = digest(screen)
        report['checks']['native_screen_asset'] = True
        report['executable_sha256_end'] = digest(exe)
        if report['executable_sha256_end'] != report['executable_sha256']:
            raise ValueError('executable changed during mission qualification')
        report['checks']['unchanged_executable'] = True
        report['passed'] = True
        verify_gate(report, exe, evidence_root=output)
    except Exception as exc:
        report['passed'] = False
        report['error'] = f'{type(exc).__name__}: {exc}'[-2000:]
    (output / 'report.json').write_text(json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False) + '\n', encoding='utf-8')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--exe', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args()
    result = validate(args.exe, args.output_dir)
    print(json.dumps({'passed': result['passed'], 'error': result['error']}), flush=True)
    raise SystemExit(0 if result['passed'] else 1)
