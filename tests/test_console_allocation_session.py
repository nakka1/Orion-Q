"""Optional native allocator sessions preserve explicit offline lifecycle."""
from concurrent.futures import Future
import json
import os

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import QPoint, QRect, Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402

from quantum_autopilot.console.widgets.allocation_session import AllocationSession  # noqa: E402
from quantum_autopilot.console.widgets.model_catalog import ModelCatalog  # noqa: E402
from quantum_autopilot.console.widgets.sync_session import QFileDialog  # noqa: E402
from quantum_autopilot.model_intake.allocation_session import MODEL_IDS, demo_allocation_input  # noqa: E402
from quantum_autopilot.model_intake.verification import verify_model_archive  # noqa: E402
from tests import test_optional_allocation_session as helpers  # noqa: E402
from tests import test_console_compensation_session as ui  # noqa: E402

pytestmark = pytest.mark.unit
make_archive = helpers.make_archive
allocation_package = helpers.allocation_package
app = ui.app


@pytest.mark.parametrize("model_id", MODEL_IDS)
def test_explicit_run_export_and_edit_refusal(app, allocation_package, model_id, tmp_path, monkeypatch):
    dialog = AllocationSession(model_id)
    try:
        dialog.set_package(verify_model_archive(model_id, allocation_package))
        dialog.show()
        assert not dialog.run_button.isEnabled() and not dialog.job.active
        QTest.mouseClick(dialog.demo_button, Qt.MouseButton.LeftButton)
        assert dialog.job.result is None and not dialog.job.active
        QTest.mouseClick(dialog.run_button, Qt.MouseButton.LeftButton)
        ui.settle(app, dialog.job)
        assert dialog.job.result.model_id == model_id, dialog.job.error
        assert "SELECTED / PERSISTENCE" in dialog.output.toPlainText()
        path = tmp_path / "allocation-result.json"
        monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a: (str(path), ""))
        QTest.mouseClick(dialog.export_button, Qt.MouseButton.LeftButton)
        raw = path.read_bytes()
        QTest.mouseClick(dialog.export_button, Qt.MouseButton.LeftButton)
        assert path.read_bytes() == raw and "EXPORT REFUSED" in dialog.output.toPlainText()
        request = json.loads(dialog.editor.toPlainText())
        request["protocol"] = "decoy_state_bb84"
        dialog.editor.setPlainText(json.dumps(request))
        assert dialog.job.result is None and not dialog.export_button.isEnabled()
        QTest.mouseClick(dialog.run_button, Qt.MouseButton.LeftButton)
        ui.settle(app, dialog.job)
        assert "REFUSED" in dialog.output.toPlainText() and dialog.job.result is None
    finally:
        dialog.close()
        dialog.deleteLater()


def test_catalog_switches_and_forgets_snapshot(app, allocation_package):
    catalog = ModelCatalog()
    try:
        catalog.show()
        for model_id in MODEL_IDS:
            catalog.table.selectRow(next(i for i, o in enumerate(catalog.options) if o.model_id == model_id))
            assert not catalog.evaluate_button.isEnabled()
            catalog.checker.result = verify_model_archive(model_id, allocation_package)
            catalog.checker.changed.emit()
            QTest.mouseClick(catalog.evaluate_button, Qt.MouseButton.LeftButton)
            assert isinstance(catalog.offline_session, AllocationSession)
            assert catalog.offline_session.model_id == model_id
            assert not catalog.offline_session.editor.toPlainText()
            catalog.offline_session.editor.setPlainText(demo_allocation_input(model_id))
        catalog.checker.clear()
        assert not catalog.offline_session.isVisible() and catalog.offline_session.package is None
    finally:
        catalog.close()
        catalog.deleteLater()


def test_late_result_discarded_after_close(app, allocation_package):
    dialog = AllocationSession(MODEL_IDS[1])
    dialog.set_package(verify_model_archive(MODEL_IDS[1], allocation_package))
    dialog.show()
    future = Future()
    future.set_running_or_notify_cancel()
    dialog.job._future = future
    dialog.job._pending_generation = dialog.job.generation
    dialog.close()
    future.set_result(object())
    dialog.job.poll()
    assert dialog.job.result is None and dialog.package is None
    dialog.deleteLater()


@pytest.mark.parametrize("size", [(640, 480), (1060, 760)])
def test_controls_fit(app, allocation_package, size):
    dialog = AllocationSession(MODEL_IDS[1])
    try:
        dialog.set_package(verify_model_archive(MODEL_IDS[1], allocation_package))
        dialog.show()
        dialog.resize(*size)
        app.processEvents()
        assert (dialog.width(), dialog.height()) == size
        for button in (dialog.load_button, dialog.demo_button, dialog.run_button, dialog.export_button, dialog.close_button):
            assert dialog.rect().contains(QRect(button.mapTo(dialog, QPoint()), button.size()))
    finally:
        dialog.close()
        dialog.deleteLater()
