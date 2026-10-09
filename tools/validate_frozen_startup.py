"""Check ordinary Windows startup at native and fractional monitor scaling."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path


CASES = (('service-100', '1', []), ('service-125', '1.25', []),
         ('service-150', '1.5', []), ('service-200', '2', []),
         ('demo-100', '1', ['--demo']), ('demo-150', '1.5', ['--demo']))


def validate(exe, output):
    exe, output = Path(exe).resolve(strict=True), Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    report = {'schema': 'orion.frozen-startup-check.v1', 'passed': False,
              'executable_sha256': hashlib.sha256(exe.read_bytes()).hexdigest(),
              'independent_machine_verified': False, 'physical_mouse_drag_verified': False,
              'cases': [], 'error': None}
    try:
        if sys.platform != 'win32':
            raise RuntimeError('This gate requires the actual Windows Qt platform')
        for name, scale, extra in CASES:
            target = output / name / 'report.json'
            command = [str(exe), '--startup-test', str(target), *extra]
            if name == 'service-100':
                command += ['--startup-size', '3440x1440']
            environment = dict(os.environ, QT_QPA_PLATFORM='windows', QT_SCALE_FACTOR=scale)
            for key in list(environment):
                if key.upper() in {'PYTHONPATH', 'PYTHONHOME', 'QT_PLUGIN_PATH', 'QT_QPA_PLATFORM_PLUGIN_PATH'}:
                    del environment[key]
            system = environment.get('SystemRoot', r'C:\Windows')
            environment['PATH'] = os.pathsep.join((str(Path(system) / 'System32'), system))
            environment['PYINSTALLER_RESET_ENVIRONMENT'] = '1'
            startup = subprocess.STARTUPINFO()
            startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startup.wShowWindow = 0
            with (output / f'{name}.stdout.log').open('w') as stdout, (output / f'{name}.stderr.log').open('w') as stderr:
                process = subprocess.Popen(command, env=environment, stdout=stdout, stderr=stderr,
                                           startupinfo=startup, creationflags=subprocess.CREATE_NO_WINDOW)
                try:
                    code = process.wait(timeout=90)
                except subprocess.TimeoutExpired:
                    process.terminate(); process.wait(timeout=10)
                    report['cases'].append({'case': name, 'passed': False, 'timeout': True})
                    raise TimeoutError(f'{name}: the ordinary startup loop did not finish')
            if code != 0 or not target.is_file():
                report['cases'].append({'case': name, 'passed': False, 'exit': code})
                raise RuntimeError(f'{name}: startup process failed with exit {code}')
            record = json.loads(target.read_text(encoding='utf-8'))
            valid = (record.get('passed') is True and record.get('platform') == 'windows'
                     and abs(record.get('device_pixel_ratio', 0) - float(scale)) < .01
                     and record.get('source') == ('demo' if extra else 'service'))
            report['cases'].append({'case': name, 'passed': valid, 'report': str(target),
                                   **{key: record[key] for key in ('heartbeat_count', 'heartbeat_max_ms',
                                       'heartbeat_p95_ms', 'canvas_resize_count', 'first_snapshot_after_show_s')}})
            print(json.dumps(report['cases'][-1]), flush=True)
            if not valid:
                raise ValueError(f'{name}: native startup criteria were not met')
        report['passed'] = True
    except Exception as exc:
        report['error'] = f'{type(exc).__name__}: {exc}'
    (output / 'report.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--exe', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args()
    result = validate(args.exe, args.output_dir)
    print(json.dumps({'passed': result['passed'], 'error': result['error']}), flush=True)
    raise SystemExit(0 if result['passed'] else 1)
