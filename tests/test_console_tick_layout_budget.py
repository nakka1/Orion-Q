"""Layout updates keep received values complete without redundant refonting."""
import os
from pathlib import Path
import subprocess
import sys

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import pytest

pytest.importorskip('PySide6.QtWidgets')
from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication

from quantum_autopilot.console import theme
from quantum_autopilot.console.widgets.inspector import KVList
from quantum_autopilot.console.widgets.prims import WrappingLabel


@pytest.fixture
def app():
    current = QApplication.instance() or QApplication([])
    theme.install(current)
    yield current
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


@pytest.mark.parametrize('width', (210, 350, 500))
def test_many_ticks_preserve_rows_full_values_and_fitted_text(app, width):
    panel = KVList(wrap=True)
    panel.resize(width, 640)
    panel.show()
    try:
        identities = None
        for tick in range(20):
            values = [('Received epoch', f'2026-10-11T13:12:{tick:02d}.123456789Z'),
                      ('Classic channel / source', 'CTRL-source-' + str(tick) + '-0123456789'*4)]
            panel.set_rows(values)
            app.processEvents()
            if identities is None:
                identities = dict(panel.rows)
            assert all(panel.rows[key] is row for key, row in identities.items())
            assert panel.texts() == dict(values)
            for row in panel.rows.values():
                field = row.value_label
                assert isinstance(field, WrappingLabel)
                assert field.height() >= field.heightForWidth(field.contentsRect().width())
                assert row.height() >= max(row.key_label.height(), field.height()) + 12
    finally:
        panel.close()
        panel.deleteLater()


def test_profile_wrappers_keep_no_argument_qt_slots_callable(tmp_path):
    """The real clicked(bool) signal must retain Qt's original argument trimming."""
    root = Path(__file__).resolve().parents[1]
    code = '''
from types import SimpleNamespace
from PySide6.QtWidgets import QApplication, QPushButton
from quantum_autopilot.console.performance_validation import _profile_hooks
from quantum_autopilot.console.screens.base import Screen
from quantum_autopilot.console.screens.topology import TopologyScreen
import time
app = QApplication([])
monitor = SimpleNamespace(method_samples=[], phase="diagnostic", created=time.perf_counter())
_profile_hooks(monitor)
screen = Screen()
button = QPushButton()
button.clicked.connect(screen.apply_command_capabilities)
button.click()
app.processEvents()
if not any(row["method"] == "Screen.apply_command_capabilities" for row in monitor.method_samples):
    raise RuntimeError("clicked signal did not execute the original slot")
'''
    environment = dict(os.environ, QT_QPA_PLATFORM='offscreen', PYTHONPATH=str(root / 'src'))
    result = subprocess.run([sys.executable, '-c', code], cwd=root, env=environment,
                            capture_output=True, text=True, timeout=45)
    assert result.returncode == 0, result.stderr
    assert 'TypeError' not in result.stderr
