"""Frozen branding gates retain native evidence and loaded source identity."""
from copy import deepcopy
import importlib
import marshal
from pathlib import Path
import sys

import pytest

from quantum_autopilot.console import branding_validation as validation
from quantum_autopilot.product import product_version


def test_code_fingerprint_ignores_filenames_and_marshal_reference_sharing():
    text = "VALUES = frozenset(('alpha', 'beta'))\ndef f(x):\n assert x\n return (x, 1., b'bytes', ...)\n"
    source = compile(text, 'source/location.py', 'exec', dont_inherit=True, optimize=1)
    unpacked = marshal.loads(marshal.dumps(source))
    assert validation.code_fingerprint(source) == validation.code_fingerprint(unpacked)
    assert validation.code_fingerprint(source) == validation.code_fingerprint(source.replace(co_filename='frozen/PYZ.py'))
    changed = compile(text.replace('1.', '2.'), 'source/location.py', 'exec', dont_inherit=True, optimize=1)
    assert validation.code_fingerprint(source) != validation.code_fingerprint(changed)
    assert validation.code_fingerprint(source) != validation.code_fingerprint(compile(text, '', 'exec', optimize=0))


@pytest.mark.parametrize('name', validation.SOURCE_MODULES)
def test_source_compile_and_loader_code_have_identical_canonical_fingerprints(name):
    spec = importlib.import_module(name).__spec__
    code = spec.loader.get_code(spec.name)
    compiled = compile(spec.loader.get_source(spec.name), '', 'exec', dont_inherit=True, optimize=0)
    assert validation.code_fingerprint(code) == validation.code_fingerprint(compiled)
    optimized = compile(spec.loader.get_source(spec.name), 'build/source.py', 'exec', dont_inherit=True, optimize=1)
    assert validation._code_sha256(name) == validation.code_fingerprint(marshal.loads(marshal.dumps(optimized)))


@pytest.fixture
def qualified(tmp_path, monkeypatch):
    from PySide6.QtGui import QImage

    executable = tmp_path / 'OrionQ.exe'
    executable.write_bytes(b'MZ test-only gate fixture; not an executable')
    actual_version = product_version()
    resource = {'ProductName': 'OrionQ', 'OriginalFilename': 'OrionQ.exe',
                'FileVersion': actual_version, 'ProductVersion': actual_version}
    monkeypatch.setattr(validation, 'read_windows_version_resource', lambda _: resource)
    cases = []
    for key in validation.CASES:
        width, height = (int(part) for part in key.rsplit('-', 1)[1].split('x'))
        path = tmp_path / (key + '.png')
        image = QImage(width, height, QImage.Format.Format_ARGB32)
        image.fill(0)
        assert image.save(str(path))
        capture = {'path': path.name, 'sha256': validation._sha256(path),
                   'capture_method': 'QScreen.grabWindow(HWND)'}
        checks = {'wordmark': True, 'release_version': True, 'geometry': True}
        if key.startswith('boot-'):
            checks['sponsor_readable'] = True
        cases.append({'case': key, 'passed': True, 'checks': checks, 'device_pixel_ratio': 1.,
                      'brand_font_family': 'Inter',
                      'screenshots': [capture] * (2 if key.startswith('boot-') else 1)})
    report = {'schema': validation.SCHEMA, 'passed': True, 'frozen': True, 'platform': 'windows',
              'qt_platform': 'windows', 'product_name': 'OrionQ', 'version': actual_version,
              'installed_distribution_version': actual_version, 'installed_distribution_name': 'quantum-twin',
              'operator_state_accessed': False, 'service_started': False, 'physical_action_performed': False,
              'errors': [], 'wordmark_class': validation.WORDMARK_CLASS,
              'source_code_sha256': {name: validation._code_sha256(name) for name in validation.SOURCE_MODULES},
              'brand_assets': validation._brand_assets(),
              'source_code_optimization': 1, 'python_version': list(sys.version_info[:2]),
              'executable': str(executable), 'executable_sha256_before': validation._sha256(executable),
              'executable_sha256_after': validation._sha256(executable),
              'windows_version_resource_validated': True, 'windows_version_info': deepcopy(resource),
              'checks': dict.fromkeys(validation.CHECKS, True), 'cases': cases}
    return report, executable, tmp_path


def test_complete_frozen_report_matches_source_resource_and_evidence(qualified):
    report, executable, evidence = qualified
    validation.validate_native_report(report, executable, evidence_root=evidence)


@pytest.mark.parametrize('change', [
    'source_only', 'platform', 'metadata_version', 'resource_name', 'resource_version',
    'source_code', 'missing_source', 'optimization', 'python_runtime', 'missing_case',
    'arbitrary_case_checks', 'false_case_check', 'missing_global_check', 'operator_state',
    'changed_executable', 'changed_screenshot', 'escaped_screenshot', 'wrong_image_size',
    'brand_assets', 'brand_font_family',
])
def test_incomplete_stale_or_unrelated_frozen_report_is_rejected(qualified, change, tmp_path):
    report, executable, evidence = qualified
    report = deepcopy(report)
    if change == 'source_only':
        report['frozen'] = False
    elif change == 'platform':
        report['qt_platform'] = 'offscreen'
    elif change == 'metadata_version':
        report['installed_distribution_version'] = '0.0.0'
    elif change in ('resource_name', 'resource_version'):
        report['windows_version_info']['ProductName' if change == 'resource_name' else 'ProductVersion'] = 'old'
    elif change == 'source_code':
        report['source_code_sha256'][validation.SOURCE_MODULES[0]] = '0' * 64
    elif change == 'missing_source':
        report['source_code_sha256'].pop(validation.SOURCE_MODULES[0])
    elif change == 'brand_assets':
        report['brand_assets']['Inter.ttf'] = '0' * 64
    elif change == 'brand_font_family':
        report['cases'][0]['brand_font_family'] = 'Quantum'
    elif change == 'optimization':
        report['source_code_optimization'] = 0
    elif change == 'python_runtime':
        report['python_version'] = [1, 0]
    elif change == 'missing_case':
        report['cases'].pop()
    elif change == 'arbitrary_case_checks':
        report['cases'][0]['checks'] = {'invented': True}
    elif change == 'false_case_check':
        report['cases'][0]['checks']['geometry'] = False
    elif change == 'missing_global_check':
        report['checks'].pop('boot_sponsor_exact')
    elif change == 'operator_state':
        report['operator_state_accessed'] = True
    elif change == 'changed_executable':
        executable.write_bytes(b'changed')
    else:
        capture = report['cases'][0]['screenshots'][0]
        path = evidence / capture['path']
        if change == 'changed_screenshot':
            path.write_bytes(b'changed')
        elif change == 'escaped_screenshot':
            capture['path'] = str(evidence.parent / 'outside.png')
        else:
            from PySide6.QtGui import QImage
            image = QImage(1, 1, QImage.Format.Format_ARGB32)
            image.fill(0)
            assert image.save(str(path))
            capture['sha256'] = validation._sha256(path)
    with pytest.raises(ValueError):
        validation.validate_native_report(report, executable, evidence_root=evidence)


@pytest.mark.skipif(sys.platform != 'win32', reason='Native Windows file version API')
def test_windows_version_api_reads_the_current_python_executable():
    result = validation.read_windows_version_resource(Path(sys.executable))
    assert set(result) == {'ProductName', 'OriginalFilename', 'FileVersion', 'ProductVersion'}
    assert all(result.values())
