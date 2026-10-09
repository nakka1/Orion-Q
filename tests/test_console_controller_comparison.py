import json
import os
import time

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from quantum_autopilot.console.widgets import controller_comparison as module  # noqa: E402
from quantum_autopilot.console.widgets.polarisation_control import PolarisationControl  # noqa: E402


def settle(app, dialog):
    deadline = time.monotonic() + 10
    while dialog.job.active and time.monotonic() < deadline:
        app.processEvents()
        QTest.qWait(10)
    app.processEvents()
    assert not dialog.job.active


def test_compare_export_and_selection_invalidation(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    dialog = module.ControllerComparison()
    try:
        dialog.show()
        index = next(i for i in range(dialog.profile.count())
                     if dialog.profile.itemData(i)["id"] == "qnet.compensation.calibrado.ridge|30")
        dialog.profile.setCurrentIndex(index)
        dialog.compare_button.click()
        settle(app, dialog)
        assert dialog.report["record"]["status"] == "BASELINE_RECOMMENDED"
        assert dialog.export_button.isEnabled()
        monkeypatch.setattr(module.QFileDialog, "getExistingDirectory", lambda *a: str(tmp_path))
        report = dialog.report
        dialog.export_button.click()
        settle(app, dialog)
        target = tmp_path / f"orion-comparison-{report['record_sha256'][:12]}"
        assert json.loads((target / "comparison.json").read_text(encoding="utf-8")) == report
        dialog.profile.setCurrentIndex(index + 1)
        assert dialog.report is None and not dialog.export_button.isEnabled()
        dialog.compare_button.click()
        dialog.hide()
        settle(app, dialog)
        assert dialog.report is None
    finally:
        dialog.close()
        dialog.deleteLater()


def test_parent_comparison_does_not_construct_or_mutate_a_live_slot(tmp_path):
    app = QApplication.instance() or QApplication([])
    parent = PolarisationControl(journal_path=tmp_path / "journal.sqlite")
    try:
        assert parent.comparison_button.isEnabled() and parent.slot is None
        dialog = module.ControllerComparison(parent)
        dialog.compare_button.click()
        settle(app, dialog)
        assert parent.slot is None and not (tmp_path / "journal.sqlite").exists()
        dialog.close()
        dialog.deleteLater()
    finally:
        parent.close()
        parent.deleteLater()
