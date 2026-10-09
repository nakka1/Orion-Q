"""Portable diagnostic integrity, isolated state, fail-closed reports and dispatch."""
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

from quantum_autopilot import windows_acceptance as acceptance


@pytest.fixture(params=['OrionQ.exe', 'ORION-Q.exe'])
def package(tmp_path, request):
    root = tmp_path / (request.param[:-4] + ' with spaces')
    (root / '_internal').mkdir(parents=True)
    (root / request.param).write_bytes(b'fixture, not an executable')
    (root / '_internal/data.bin').write_bytes(b'fixture data')
    lines = [f'{acceptance.digest(p)}  {p.relative_to(root).as_posix()}'
             for p in sorted(root.rglob('*')) if p.is_file()]
    (root / 'SHA256SUMS.txt').write_text('\n'.join(lines) + '\n', encoding='utf-8')
    return root


def test_integrity_only_is_not_application_or_independent_acceptance(package, tmp_path, monkeypatch):
    monkeypatch.setattr(acceptance, 'invoke', lambda *a: pytest.fail('integrity-only launched a process'))
    code, output, report = acceptance.accept(package, tmp_path / 'new report with spaces', integrity_only=True)
    assert code == 0
    assert report['checks'] == {'integrity': True}
    assert report['package']['file_count'] == 2
    assert (package / report['package']['executable']).is_file()
    assert report['automatic_passed'] is False and report['independent_machine_verified'] is False
    assert report['manual_review'] == 'pending'
    assert report['acceptance_status'] == 'integrity_only_not_application_acceptance'
    assert json.loads((output / 'acceptance-report.json').read_text()) == report


@pytest.mark.parametrize('damage', ['changed', 'missing', 'extra', 'traversal', 'absolute', 'duplicate', 'empty', 'ads', 'directory', 'trailing_dot', 'self_reference', 'malformed'])
def test_rejects_damaged_or_unsafe_package_before_launch(package, tmp_path, monkeypatch, damage):
    monkeypatch.setattr(acceptance, 'invoke', lambda *a: pytest.fail('bad package launched'))
    manifest = package / 'SHA256SUMS.txt'
    original = manifest.read_text(encoding='utf-8')
    if damage == 'changed':
        (package / '_internal/data.bin').write_bytes(b'changed')
    elif damage == 'missing':
        (package / '_internal/data.bin').unlink()
    elif damage == 'extra':
        (package / 'unexpected.dll').write_bytes(b'extra')
    else:
        paths = {'traversal': '../outside', 'absolute': 'C:/outside', 'ads': 'ORION-Q.exe:stream',
                 'directory': '_internal', 'trailing_dot': 'ORION-Q.exe.', 'self_reference': 'SHA256SUMS.txt'}
        text = original + f'{"0" * 64}  {paths[damage]}\n' if damage in paths else {
            'duplicate': original + original, 'empty': '', 'malformed': 'not a checksum'}[damage]
        manifest.write_text(text, encoding='utf-8')
    code, _, report = acceptance.accept(package, tmp_path / 'result')
    assert code == 2 and report['acceptance_status'] == 'failed'
    assert report['automatic_passed'] is False and report['error']


def test_refuses_existing_output_without_overwrite(package, tmp_path):
    output = tmp_path / 'existing'
    output.mkdir()
    sentinel = output / 'acceptance-report.json'
    sentinel.write_text('sentinel')
    with pytest.raises(FileExistsError):
        acceptance.accept(package, output)
    assert sentinel.read_text() == 'sentinel'


def test_refuses_output_inside_package(package):
    output = package / 'diagnostic'
    with pytest.raises(ValueError, match='outside'):
        acceptance.accept(package, output)
    assert not output.exists()


@pytest.mark.parametrize('timeout', [True, 29, 3601, 1.5])
def test_timeout_bounds(package, tmp_path, timeout):
    with pytest.raises(ValueError, match='Timeout'):
        acceptance.accept(package, tmp_path / 'result', timeout=timeout)


@pytest.mark.parametrize('failure', [None, 'launch', 'timeout', 'self_report', 'stress_report'])
def test_automatic_checks_isolate_state_and_never_claim_manual_acceptance(package, tmp_path, monkeypatch, failure):
    calls = []
    def invoke(exe, args, output, name, timeout):
        calls.append((exe, args))
        assert exe.name in {'OrionQ.exe', 'ORION-Q.exe'} and exe.parent == package
        assert exe.is_file()
        assert output == tmp_path / 'result' and timeout == 180
        if failure == 'launch':
            raise OSError('launch failure')
        if failure == 'timeout':
            raise subprocess.TimeoutExpired(str(exe), timeout)
        if name == 'self-test':
            assert args == ['--self-test', output / 'self-test.json', '--state-root', output / 'self-test-state']
            (output / 'self-test.json').write_text(json.dumps({'passed': 'true' if failure == 'self_report' else True}))
        else:
            assert args == ['--stress-test', output / 'stress', '--stress-rounds', '20', '--seed', '1337']
            (output / 'stress').mkdir()
            (output / 'stress/report.json').write_text(json.dumps({'passed': failure != 'stress_report',
                'rounds_requested': 20, 'physical_action_performed': False}))
    monkeypatch.setattr(acceptance, 'invoke', invoke)
    monkeypatch.setattr(sys, 'platform', 'win32')
    code, _, report = acceptance.accept(package, tmp_path / 'result')
    assert report['independent_machine_verified'] is False and report['manual_review'] == 'pending'
    assert len(calls) == (2 if failure in (None, 'stress_report') else 1)
    assert code == (0 if failure is None else 2)
    assert report['automatic_passed'] is (failure is None)


def test_larger_finite_timeout_reaches_both_full_checks(package, tmp_path, monkeypatch):
    calls = []

    def invoke(exe, args, output, name, timeout):
        assert exe == package / exe.name and timeout == 3600
        calls.append(name)
        if name == 'self-test':
            (output / 'self-test.json').write_text(json.dumps({'passed': True}))
        else:
            (output / 'stress').mkdir()
            (output / 'stress/report.json').write_text(json.dumps({
                'passed': True, 'rounds_requested': 20, 'physical_action_performed': False}))

    monkeypatch.setattr(acceptance, 'invoke', invoke)
    monkeypatch.setattr(sys, 'platform', 'win32')
    code, _, report = acceptance.accept(package, tmp_path / 'result', timeout=3600)
    assert calls == ['self-test', 'stress']
    assert code == 0 and report['automatic_passed'] is True
    assert report['checks'] == {'integrity': True, 'self_test': True, 'synthetic_stress_20_rounds': True}
    assert report['manual_review'] == 'pending' and report['independent_machine_verified'] is False


def test_subprocess_has_clean_environment_without_mutating_parent(tmp_path, monkeypatch):
    monkeypatch.setenv('PYTHONPATH', 'parent-path')
    monkeypatch.setenv('QT_QPA_PLATFORM', 'parent-platform')
    def run(argv, **kwargs):
        assert argv == ['app.exe', '--self-test', 'report.json']
        assert kwargs['cwd'] == tmp_path and kwargs['timeout'] == 90
        assert kwargs['env']['QT_QPA_PLATFORM'] == 'offscreen'
        assert 'PYTHONPATH' not in kwargs['env']
        assert kwargs['env']['PYINSTALLER_RESET_ENVIRONMENT'] == '1'
        assert kwargs['check'] is True
    monkeypatch.setattr(subprocess, 'run', run)
    acceptance.invoke(Path('app.exe'), ['--self-test', 'report.json'], tmp_path, 'check', 90)
    assert acceptance.os.environ['PYTHONPATH'] == 'parent-path'
    assert acceptance.os.environ['QT_QPA_PLATFORM'] == 'parent-platform'


def test_frozen_dispatch_uses_own_package_before_desktop(monkeypatch):
    launcher_path = Path(__file__).resolve().parents[1] / 'packaging/console_launcher.py'
    spec = importlib.util.spec_from_file_location('acceptance_launcher_test', launcher_path)
    launcher = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(launcher)
    calls = []
    monkeypatch.setattr(acceptance, 'main', lambda argv, **kwargs: calls.append((argv, kwargs)) or 17)
    assert launcher.launch(['--acceptance-test', '--integrity-only']) == 17
    assert calls == [(['--integrity-only'], {'package_root': Path(sys.executable).parent})]
