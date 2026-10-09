"""Native single-window evaluation, no automatic inference or policy activation."""
from concurrent.futures import Future
import hashlib
import json
import os
import time
import zipfile

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import QPoint, QRect, Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from quantum_autopilot.console import theme  # noqa: E402
from quantum_autopilot.console.widgets.model_catalog import ModelCatalog  # noqa: E402
from quantum_autopilot.console.widgets import sync_session as ui  # noqa: E402
from quantum_autopilot.model_intake import artifacts  # noqa: E402
from quantum_autopilot.model_intake.sync_session import demo_sync_input  # noqa: E402
from quantum_autopilot.model_intake.verification import verify_model_archive  # noqa: E402

pytestmark = pytest.mark.unit


@pytest.fixture(scope="module")
def app():
    instance = QApplication.instance() or QApplication([])
    theme.install(instance)
    return instance


@pytest.fixture
def package(tmp_path, monkeypatch):
    path = tmp_path / "qkd.zip"
    with zipfile.ZipFile(path, "w") as target:
        target.writestr(artifacts.QKD_ROOT + "modelos/sincronizador_ridge.json", json.dumps({
            "window": 8, "scale": 1e-9, "update_period_s": 1., "horizon_s": 2.,
            "coefficients": [0.] * 9, "intercept": .5}))
    monkeypatch.setitem(artifacts.PINS, "qkd", hashlib.sha256(path.read_bytes()).hexdigest())
    return verify_model_archive("qkd.sync.ridge.1s.h2s", path)


@pytest.fixture
def session(app, package):
    dialog = ui.SyncSession()
    dialog.set_package(package)
    dialog.show()
    app.processEvents()
    yield dialog
    dialog.close()
    dialog.deleteLater()
    app.processEvents()


def settle(app, job):
    deadline = time.monotonic() + 10
    while job.active and time.monotonic() < deadline:
        app.processEvents()
        QTest.qWait(10)
    assert not job.active


def run_demo(app, session):
    QTest.mouseClick(session.demo_button, Qt.MouseButton.LeftButton)
    assert session.job.result is None and not session.job.active
    QTest.mouseClick(session.run_button, Qt.MouseButton.LeftButton)
    settle(app, session.job)
    assert session.job.result is not None, session.job.error


def test_explicit_run_only_and_retains_baseline(app, session):
    assert not session.run_button.isEnabled()
    assert not session.export_button.isEnabled()
    assert not session.editor.toPlainText()
    run_demo(app, session)
    assert session.job.result.model_absolute_error_s > session.job.result.persistence_absolute_error_s
    assert "NO CONTROL" in session.output.toPlainText()
    assert session.export_button.isEnabled()


def test_bad_input_refuses_without_leaving_previous_result(app, session):
    run_demo(app, session)
    session.editor.setPlainText(demo_sync_input().replace("simulated_offline", "measured"))
    assert session.job.result is None and not session.export_button.isEnabled()
    QTest.mouseClick(session.run_button, Qt.MouseButton.LeftButton)
    settle(app, session.job)
    assert session.job.result is None
    assert "REFUSED" in session.output.toPlainText()


@pytest.mark.parametrize("change", ["edit", "close", "package"])
def test_running_result_discarded_after_context_change(app, session, change):
    session.editor.setPlainText(demo_sync_input())
    pending = Future()
    pending.set_running_or_notify_cancel()
    session.job._future = pending
    session.job._pending_generation = session.job.generation
    session.job.changed.emit()
    assert not session.run_button.isEnabled()
    if change == "edit":
        session.editor.setPlainText("edited")
    elif change == "close":
        session.close()
    else:
        session.invalidate_package()
    pending.set_result(object())
    session.job.poll()
    assert session.job.result is None
    assert not session.export_button.isEnabled()


def test_catalog_only_opens_session_after_verified_sync(app, package):
    catalog = ModelCatalog()
    try:
        catalog.show()
        catalog.table.selectRow(2)
        assert not catalog.evaluate_button.isEnabled()
        catalog.checker.result = package
        catalog.checker.changed.emit()
        assert catalog.evaluate_button.isEnabled()
        QTest.mouseClick(catalog.evaluate_button, Qt.MouseButton.LeftButton)
        assert catalog.offline_session.isVisible()
        assert catalog.offline_session.package is package
        catalog.offline_session.editor.setPlainText(demo_sync_input())
        QTest.mouseClick(catalog.evaluate_button, Qt.MouseButton.LeftButton)
        assert catalog.offline_session.editor.toPlainText() == demo_sync_input()
        catalog.checker.clear()
        assert not catalog.offline_session.isVisible()
        assert catalog.offline_session.package is None
        catalog.table.selectRow(1)
        assert not catalog.evaluate_button.isEnabled()
    finally:
        catalog.close()
        catalog.deleteLater()


def test_export_creates_new_report_and_refuses_overwrite(app, session, tmp_path, monkeypatch):
    run_demo(app, session)
    path = tmp_path / "result.json"
    monkeypatch.setattr(ui.QFileDialog, "getSaveFileName", lambda *a: (str(path), ""))
    QTest.mouseClick(session.export_button, Qt.MouseButton.LeftButton)
    raw = path.read_bytes()
    assert json.loads(raw)["physical_action_performed"] is False
    QTest.mouseClick(session.export_button, Qt.MouseButton.LeftButton)
    assert path.read_bytes() == raw
    assert "EXPORT REFUSED" in session.output.toPlainText()


def test_stale_export_chooser_cannot_save_old_result(app, session, tmp_path, monkeypatch):
    run_demo(app, session)
    path = tmp_path / "stale.json"

    def choose(*args):
        session.editor.setPlainText("changed while chooser open")
        return str(path), ""

    monkeypatch.setattr(ui.QFileDialog, "getSaveFileName", choose)
    QTest.mouseClick(session.export_button, Qt.MouseButton.LeftButton)
    assert not path.exists()


def test_stale_load_chooser_does_not_read(session, monkeypatch):
    def choose(*args):
        session.invalidate_package()
        return "never-read.json", ""

    monkeypatch.setattr(ui.QFileDialog, "getOpenFileName", choose)
    monkeypatch.setattr(ui, "load_sync_input", lambda *a: pytest.fail("stale load"))
    QTest.mouseClick(session.load_button, Qt.MouseButton.LeftButton)


@pytest.mark.parametrize("size", [(640, 480), (1060, 760)])
def test_small_session_keeps_controls_and_scrollable_editors(app, session, size):
    session.resize(*size)
    session.editor.setPlainText(demo_sync_input())
    app.processEvents()
    assert (session.width(), session.height()) == size
    for button in (session.run_button, session.load_button, session.demo_button, session.export_button, session.close_button):
        assert session.rect().contains(QRect(button.mapTo(session, QPoint()), button.size()))
    assert session.output.isReadOnly()
