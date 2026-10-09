"""Stdlib-only acceptance entry point, before Qt/scientific imports.

Local hashes detect corruption, not authenticity. No operator state is opened.
The same host running this code cannot attest independent-machine validation.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path, PureWindowsPath

from .product import EXECUTABLE_NAME, PRODUCT_NAME

def digest(path):
    value = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(chunk)
    return value.hexdigest()


def plain(path):
    # Reject both symlinks and Windows junctions, including directory ancestors
    # inside the package; do not follow them during recursive discovery.
    info = path.lstat()
    if path.is_symlink() or getattr(info, 'st_file_attributes', 0) & 0x400:
        raise ValueError(f'Reparse point not accepted: {path.name}')


def verify_package(root):
    root = Path(root).absolute()
    plain(root)
    manifest = root / 'SHA256SUMS.txt'
    plain(manifest)
    if manifest.stat().st_size > 5 * 1024 * 1024:
        raise ValueError('Checksum manifest too large')
    entries = manifest.read_text(encoding='utf-8-sig').splitlines()
    if not 1 <= len(entries) <= 20000:
        raise ValueError('Checksum entry count outside limits')
    expected = set()
    for line in entries:
        match = re.fullmatch(r'([0-9a-fA-F]{64})  (.+)', line)
        if not match:
            raise ValueError('Malformed checksum entry')
        sha, relative = match.groups()
        relative = relative.replace('\\', '/')
        parts = relative.split('/')
        if (PureWindowsPath(relative).is_absolute() or re.search(r'[:\x00-\x1f]', relative)
                or any(p in ('', '.', '..') or p.endswith(('.', ' ')) for p in parts)):
            raise ValueError(f'Unsafe checksum path: {relative}')
        key = relative.casefold()
        if key == 'sha256sums.txt' or key in expected:
            raise ValueError('Duplicate or self-referencing checksum entry')
        expected.add(key)
        path = root
        for part in parts:
            path /= part
            plain(path)
        if not path.is_file() or digest(path).lower() != sha.lower():
            raise ValueError(f'Checksum mismatch: {relative}')
    executable = next((root / name for name in (EXECUTABLE_NAME, 'ORION-Q.exe')
                       if name.casefold() in expected), None)
    if executable is None or not (root / '_internal').is_dir():
        raise ValueError(f'Complete {EXECUTABLE_NAME} and _internal directory required')
    for directory, dirs, files in os.walk(root, followlinks=False):
        for name in dirs + files:
            path = Path(directory) / name
            plain(path)
            if path.is_file():
                relative = path.relative_to(root).as_posix().casefold()
                if relative != 'sha256sums.txt' and relative not in expected:
                    raise ValueError(f'Undeclared package file: {relative}')
    return {'file_count': len(expected), 'executable': executable.name,
            'executable_sha256': digest(executable),
            'manifest_sha256': digest(manifest)}


def invoke(exe, args, output, name, timeout):
    env = os.environ.copy()
    for key in list(env):
        if key.upper() in {'PYTHONPATH', 'PYTHONHOME', 'QT_PLUGIN_PATH', 'QT_QPA_PLATFORM_PLUGIN_PATH'}:
            del env[key]
    env['QT_QPA_PLATFORM'] = 'offscreen'
    system = env.get('SystemRoot', r'C:\Windows')
    env['PATH'] = os.pathsep.join((str(Path(system) / 'System32'), system))
    # Reset PyInstaller's child environment when starting a fresh instance;
    # the diagnostic child is not a multiprocessing worker.
    env['PYINSTALLER_RESET_ENVIRONMENT'] = '1'
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0
    with (output / f'{name}.stdout.txt').open('xb') as stdout, (output / f'{name}.stderr.txt').open('xb') as stderr:
        # subprocess.run kills and waits for this exact child on timeout.
        subprocess.run([str(exe), *map(str, args)], cwd=output, env=env, stdout=stdout,
                       stderr=stderr, timeout=timeout, check=True, creationflags=flags)


def accept(root, output=None, *, integrity_only=False, timeout=180):
    if type(timeout) is not int or not 30 <= timeout <= 3600:
        raise ValueError('Timeout must be an integer in [30, 3600] seconds')
    root = Path(root).absolute()
    if output is None:
        output = Path(tempfile.mkdtemp(prefix='ORION-Q-acceptance-'))
    else:
        output = Path(output).resolve()
        if output == root.resolve() or root.resolve() in output.parents:
            raise ValueError('Output must be outside the package')
        output.mkdir(parents=True, exist_ok=False)
    report = {'schema': 'orion.windows_acceptance.v1', 'started_at_utc': datetime.now(timezone.utc).isoformat(),
              'scope': 'local_software_checks_only', 'automatic_passed': False,
              'acceptance_status': 'failed', 'independent_machine_verified': False,
              'manual_review': 'pending', 'integrity_only': bool(integrity_only),
              'environment': {'os': platform.system(), 'release': platform.release(),
                              'version': platform.version(), 'machine': platform.machine()},
              'checks': {}, 'error': None}
    code = 2
    try:
        report['package'] = verify_package(root)
        report['checks']['integrity'] = True
        if integrity_only:
            report['acceptance_status'] = 'integrity_only_not_application_acceptance'
        else:
            if sys.platform != 'win32':
                raise ValueError('Full application acceptance requires Windows')
            exe = root / report['package']['executable']
            self_test = output / 'self-test.json'
            invoke(exe, ['--self-test', self_test, '--state-root', output / 'self-test-state'], output, 'self-test', timeout)
            result = json.loads(self_test.read_text(encoding='utf-8'))
            if result.get('passed') is not True:
                raise ValueError('Self-test report was not approved')
            report['checks']['self_test'] = True
            invoke(exe, ['--stress-test', output / 'stress', '--stress-rounds', '20', '--seed', '1337'], output, 'stress', timeout)
            reports = list((output / 'stress').rglob('report.json'))
            if len(reports) != 1:
                raise ValueError('Expected exactly one stress report')
            result = json.loads(reports[0].read_text(encoding='utf-8'))
            if (result.get('passed') is not True or result.get('rounds_requested') != 20
                    or result.get('physical_action_performed') is not False):
                raise ValueError('Stress report was not approved')
            report['checks']['synthetic_stress_20_rounds'] = True
            report['automatic_passed'] = True
            report['acceptance_status'] = 'manual_review_required'
        code = 0
    except Exception as exc:
        report['error'] = f'{type(exc).__name__}: {exc}'[:2000]
    report['finished_at_utc'] = datetime.now(timezone.utc).isoformat()
    with (output / 'acceptance-report.json').open('x', encoding='utf-8') as stream:
        json.dump(report, stream, indent=2, allow_nan=False)
    return code, output, report


def main(argv=None, *, package_root=None):
    parser = argparse.ArgumentParser(description=f'Local {PRODUCT_NAME} package checks; independent acceptance remains manual.')
    parser.add_argument('--output-dir', type=Path)
    parser.add_argument('--timeout-seconds', type=int, default=180)
    parser.add_argument('--integrity-only', action='store_true')
    if package_root is None:  # source-side developer harness only
        parser.add_argument('--package-root', required=True, type=Path)
    args = parser.parse_args(argv)
    code, output, report = accept(package_root or args.package_root, args.output_dir,
                                  integrity_only=args.integrity_only, timeout=args.timeout_seconds)
    if sys.stdout is not None:
        print(f"Status: {report['acceptance_status']}\nReport: {output / 'acceptance-report.json'}")
    return code


if __name__ == '__main__':
    raise SystemExit(main())
