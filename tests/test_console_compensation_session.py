"""Native compensation checkpoints keep distinct, explicit offline contexts."""
from concurrent.futures import Future
from dataclasses import replace
import json
import os
import time

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import QPoint, QRect, Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from quantum_autopilot.console import theme  # noqa: E402
from quantum_autopilot.console.widgets.compensation_session import CompensationSession  # noqa: E402
from quantum_autopilot.console.widgets.model_catalog import ModelCatalog  # noqa: E402
from quantum_autopilot.console.widgets.sync_session import QFileDialog  # noqa: E402
from quantum_autopilot.model_intake.compensation_session import MODEL_IDS, demo_compensation_input  # noqa: E402
from quantum_autopilot.model_intake.verification import verify_model_archive  # noqa: E402
from tests import test_optional_compensation_session as helpers  # noqa: E402

pytestmark = pytest.mark.unit
compensation_archive = helpers.compensation_archive


@pytest.fixture(scope="module")
def app():
    instance = QApplication.instance() or QApplication([])
    theme.install(instance)
    return instance


def settle(app, job):
    deadline = time.monotonic() + 10
    while job.active and time.monotonic() < deadline:
        app.processEvents()
        QTest.qWait(10)
    assert not job.active


@pytest.mark.parametrize("model_id", MODEL_IDS)
def test_each_checkpoint_runs_explicitly_with_matching_demo(app, compensation_archive, model_id):
    dialog = CompensationSession(model_id)
    try:
        dialog.set_package(verify_model_archive(model_id, compensation_archive))
        dialog.show()
        assert not dialog.run_button.isEnabled() and not dialog.job.active
        QTest.mouseClick(dialog.demo_button, Qt.MouseButton.LeftButton)
        assert not dialog.job.active and dialog.job.result is None
        assert json.loads(dialog.editor.toPlainText())["model_id"] == model_id
        QTest.mouseClick(dialog.run_button, Qt.MouseButton.LeftButton)
        settle(app, dialog.job)
        assert dialog.job.result.model_id == model_id, dialog.job.error
        assert "warm-up" in dialog.output.toPlainText().lower()
        assert "learned outputs: 1" in dialog.output.toPlainText()
        assert "No closed-loop gain" in dialog.output.toPlainText()
    finally:
        dialog.close()
        dialog.deleteLater()


def test_catalog_switches_all_regimes_without_reusing_inputs(app, compensation_archive):
    catalog = ModelCatalog()
    try:
        catalog.show()
        for model_id in MODEL_IDS:
            row = next(i for i, option in enumerate(catalog.options) if option.model_id == model_id)
            catalog.table.selectRow(row)
            assert not catalog.evaluate_button.isEnabled()
            catalog.checker.result = verify_model_archive(model_id, compensation_archive)
            catalog.checker.changed.emit()
            QTest.mouseClick(catalog.evaluate_button, Qt.MouseButton.LeftButton)
            assert catalog.offline_session.model_id == model_id
            assert not catalog.offline_session.editor.toPlainText()
            catalog.offline_session.editor.setPlainText(demo_compensation_input(model_id))
        catalog.checker.clear()
        assert not catalog.offline_session.isVisible()
        assert catalog.offline_session.package is None
    finally:
        catalog.close()
        catalog.deleteLater()


def test_short_series_refusal_export_and_edit_invalidation(app, compensation_archive, tmp_path, monkeypatch):
    model_id = MODEL_IDS[0]
    dialog = CompensationSession(model_id)
    try:
        package = verify_model_archive(model_id, compensation_archive)
        dialog.set_package(package)
        dialog.show()
        request = json.loads(demo_compensation_input(model_id))
        request["samples"] = request["samples"][:3]
        request["reference"] = None
        dialog.editor.setPlainText(json.dumps(request))
        QTest.mouseClick(dialog.run_button, Qt.MouseButton.LeftButton)
        settle(app, dialog.job)
        assert "learned outputs: 0" in dialog.output.toPlainText()
        assert "angular errors unavailable" in dialog.output.toPlainText()
        path = tmp_path / "result.json"
        monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a: (str(path), ""))
        QTest.mouseClick(dialog.export_button, Qt.MouseButton.LeftButton)
        raw = path.read_bytes()
        QTest.mouseClick(dialog.export_button, Qt.MouseButton.LeftButton)
        assert path.read_bytes() == raw and "EXPORT REFUSED" in dialog.output.toPlainText()
        dialog.editor.setPlainText(demo_compensation_input(MODEL_IDS[1]))
        assert dialog.job.result is None and not dialog.export_button.isEnabled()
        QTest.mouseClick(dialog.run_button, Qt.MouseButton.LeftButton)
        settle(app, dialog.job)
        assert "REFUSED" in dialog.output.toPlainText()
        assert dialog.job.result is None
        with pytest.raises(ValueError):
            dialog.set_package(replace(package, model_id=MODEL_IDS[1]))
    finally:
        dialog.close()
        dialog.deleteLater()


def test_late_result_discarded_after_close(app, compensation_archive):
    dialog = CompensationSession(MODEL_IDS[0])
    dialog.set_package(verify_model_archive(MODEL_IDS[0], compensation_archive))
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
def test_long_checkpoint_name_and_controls_fit(app, compensation_archive, size):
    dialog = CompensationSession(MODEL_IDS[-1])
    try:
        dialog.set_package(verify_model_archive(MODEL_IDS[-1], compensation_archive))
        dialog.show()
        dialog.resize(*size)
        app.processEvents()
        assert (dialog.width(), dialog.height()) == size
        for button in (dialog.load_button, dialog.demo_button, dialog.run_button, dialog.export_button, dialog.close_button):
            assert dialog.rect().contains(QRect(button.mapTo(dialog, QPoint()), button.size()))
    finally:
        dialog.close()
        dialog.deleteLater()
