"""Measure wrapped detail construction and first Qt layout with both ownership orders."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import statistics
import sys
import time

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from PySide6.QtCore import QCoreApplication, QEvent, qVersion
from PySide6.QtWidgets import QApplication, QWidget
from quantum_autopilot.console import theme
from quantum_autopilot.console.widgets.prims import KVRow, scoped_style, vbox


def measure(app, final_owner, trials=40):
    durations = []
    for trial in range(trials + 3):
        host = QWidget()
        host.resize(500, 650)
        scoped_style(host, f'background:{theme.C.bg};border:1px solid {theme.C.soft};')
        layout = vbox(host, (12, 10, 12, 10), 6)
        started = time.perf_counter()
        rows = []
        for index in range(24):
            row = KVRow(f'Measured field {index}', f'{trial + index / 100:.4f} km · received channel',
                        size=12.5, wrap=True, parent=host if final_owner else None)
            rows.append(row)
            layout.addWidget(row)
        host.show()
        app.processEvents()
        elapsed = (time.perf_counter() - started) * 1000
        if trial >= 3:
            durations.append(elapsed)
        for row in rows:
            if row.value_label.text() != row.value_label.full_text:
                raise RuntimeError('full received value was changed')
        host.close()
        host.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        app.processEvents()
    values = sorted(durations)
    return {'samples': len(values), 'median_ms': statistics.median(values),
            'p95_ms': values[int((len(values)-1)*.95)], 'max_ms': values[-1]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    target = parser.parse_args().output
    if target.exists():
        raise FileExistsError(target)
    app = QApplication([])
    theme.install(app)
    result = {'schema': 'orion.console-kv-layout-benchmark.v1',
              'scope': 'Qt offscreen construction + first layout, 24 wrapped fields, same fonts and values',
              'rows': 24, 'trials_per_variant': 40, 'warmup_trials_per_variant': 3,
              'protocol_processing_changed': False, 'physical_action_performed': False,
              'runtime': {'platform': platform.platform(), 'python': sys.version, 'qt': qVersion()},
              'source_sha256': {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
                               for p in (Path(__file__), ROOT / 'src/quantum_autopilot/console/widgets/prims.py')},
              'reparent_after_construction': measure(app, False),
              'construct_in_final_owner': measure(app, True)}
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
