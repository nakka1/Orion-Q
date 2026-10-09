"""Actor and synchronization sessions must retain distinct schemas and lifecycle."""
from dataclasses import replace
import json
import os
import time
from concurrent.futures import Future

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import QPoint, QRect, Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from quantum_autopilot.console import theme  # noqa: E402
from quantum_autopilot.console.widgets.keyrate_session import KeyRateSession  # noqa: E402
from quantum_autopilot.console.widgets.model_catalog import ModelCatalog  # noqa: E402
from quantum_autopilot.console.widgets.sync_session import QFileDialog  # noqa: E402
from quantum_autopilot.model_intake.keyrate_session import demo_keyrate_input  # noqa: E402
from quantum_autopilot.model_intake.sync_session import demo_sync_input  # noqa: E402
from quantum_autopilot.model_intake.verification import verify_model_archive  # noqa: E402
from tests import test_optional_keyrate_session as actor_test_helpers  # noqa: E402

pytestmark = pytest.mark.unit
actor_archive = actor_test_helpers.actor_archive


@pytest.fixture(scope="module")
def app():
    instance = QApplication.instance() or QApplication([])
    theme.install(instance)
    return instance


@pytest.fixture
def package(actor_archive):
    return verify_model_archive("qkd.keyrate.actor", actor_archive)


@pytest.fixture
def session(app, package):
    dialog = KeyRateSession()
    dialog.set_package(package)
    dialog.show()
    app.processEvents()
    yield dialog
    dialog.close()
    dialog.deleteLater()
    app.processEvents()


def run(app, session):
    QTest.mouseClick(session.run_button, Qt.MouseButton.LeftButton)
    deadline = time.monotonic() + 10
    while session.job.active and time.monotonic() < deadline:
        app.processEvents()
        QTest.qWait(10)
    assert not session.job.active


def test_actor_executes_only_explicitly_and_exports_without_overwrite(app, session, tmp_path, monkeypatch):
    assert not session.run_button.isEnabled() and not session.job.active
    QTest.mouseClick(session.demo_button, Qt.MouseButton.LeftButton)
    assert not session.job.active and session.job.result is None
    run(app, session)
    assert session.job.result is not None, session.job.error
    assert session.job.result.physical_action_performed is False
    assert "HOLD-CURRENT BASELINE" in session.output.toPlainText()
    assert "No next-period key-rate forecast" in session.output.toPlainText()
    path = tmp_path / "recommendation.json"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a: (str(path), ""))
    QTest.mouseClick(session.export_button, Qt.MouseButton.LeftButton)
    raw = path.read_bytes()
    assert json.loads(raw)["model_id"] == "qkd.keyrate.actor"
    QTest.mouseClick(session.export_button, Qt.MouseButton.LeftButton)
    assert path.read_bytes() == raw and "EXPORT REFUSED" in session.output.toPlainText()


@pytest.mark.parametrize("payload", [demo_sync_input(), demo_keyrate_input().replace("simulated_offline", "measured")],
                         ids=["wrong-function", "measured"])
def test_wrong_domain_or_function_refused(app, session, payload):
    session.editor.setPlainText(payload)
    run(app, session)
    assert session.job.result is None
    assert "REFUSED" in session.output.toPlainText()
    assert not session.export_button.isEnabled()


def test_seed_edit_invalidates_result_and_replays_explicit_seed(app, session):
    session.editor.setPlainText(demo_keyrate_input())
    run(app, session)
    previous = session.job.result
    request = json.loads(demo_keyrate_input())
    request["seed"] = 0
    session.editor.setPlainText(json.dumps(request))
    assert session.job.result is None
    run(app, session)
    assert session.job.result.seed == 0
    assert session.job.result.input_sha256 != previous.input_sha256
    assert session.job.result.recommendation["sampled_index"] != previous.recommendation["sampled_index"]


def test_catalog_switches_dialog_type_without_reusing_input(app, package):
    catalog = ModelCatalog()
    try:
        catalog.show()
        catalog.table.selectRow(1)
        catalog.checker.result = package
        catalog.checker.changed.emit()
        QTest.mouseClick(catalog.evaluate_button, Qt.MouseButton.LeftButton)
        actor = catalog.offline_session
        assert isinstance(actor, KeyRateSession)
        actor.editor.setPlainText(demo_keyrate_input())
        catalog.table.selectRow(2)
        assert actor.package is None and not actor.isVisible()
        catalog.checker.result = replace(package, model_id="qkd.sync.ridge.1s.h2s")
        catalog.checker.changed.emit()
        QTest.mouseClick(catalog.evaluate_button, Qt.MouseButton.LeftButton)
        assert catalog.offline_session.model_id == "qkd.sync.ridge.1s.h2s"
        assert not isinstance(catalog.offline_session, KeyRateSession)
        assert not catalog.offline_session.editor.toPlainText()
        catalog.table.selectRow(1)
        catalog.checker.result = package
        catalog.checker.changed.emit()
        QTest.mouseClick(catalog.evaluate_button, Qt.MouseButton.LeftButton)
        assert isinstance(catalog.offline_session, KeyRateSession)
        assert not catalog.offline_session.editor.toPlainText()
    finally:
        catalog.close()
        catalog.deleteLater()


def test_edit_during_run_discards_late_result(app, session):
    future = Future()
    future.set_running_or_notify_cancel()
    session.job._future = future
    session.job._pending_generation = session.job.generation
    session.job.changed.emit()
    session.editor.setPlainText(demo_keyrate_input())
    future.set_result(object())
    session.job.poll()
    assert session.job.result is None and not session.export_button.isEnabled()


@pytest.mark.parametrize("size", [(640, 480), (1060, 760)])
def test_actor_session_reflows_with_all_controls(app, session, size):
    session.resize(*size)
    session.editor.setPlainText(demo_keyrate_input())
    app.processEvents()
    assert (session.width(), session.height()) == size
    for button in (session.load_button, session.demo_button, session.run_button, session.export_button, session.close_button):
        assert session.rect().contains(QRect(button.mapTo(session, QPoint()), button.size()))
