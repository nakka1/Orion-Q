"""Optional native inventory must never load models or dispatch control commands."""
import os
import subprocess
import sys
from concurrent.futures import Future
import threading
import time

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication, QAbstractItemView  # noqa: E402

from quantum_autopilot.console import theme  # noqa: E402
from quantum_autopilot.console import model_archive_check  # noqa: E402
from quantum_autopilot.console.providers import MockProvider  # noqa: E402
from quantum_autopilot.console.screens.fpga import EdgeControlScreen  # noqa: E402
from quantum_autopilot.console.synthetic import artificial_snapshot  # noqa: E402
from quantum_autopilot.console.widgets.model_catalog import QFileDialog, ModelCatalog, model_notes  # noqa: E402
from quantum_autopilot.model_intake import catalog  # noqa: E402
from quantum_autopilot.model_intake.verification import ArchiveVerification  # noqa: E402

pytestmark = pytest.mark.unit


@pytest.fixture(scope="module")
def app():
    instance = QApplication.instance() or QApplication([])
    theme.install(instance)
    return instance


@pytest.fixture
def dialog(app):
    widget = ModelCatalog()
    yield widget
    widget.close()
    widget.deleteLater()
    app.processEvents()


def test_catalog_is_complete_and_explicitly_off(dialog):
    assert dialog.options == catalog.model_options()
    assert dialog.table.records.rowCount() == 11
    assert sum(o.status == "offline_only" for o in dialog.options) == 11
    assert not any(o.default_enabled or o.runtime_available for o in dialog.options)
    assert all(row[2][0] == "OFF" and row[3][0] == "NOT INTEGRATED"
               for row in dialog.table.records.rows)
    assert "does not report the active runtime policy" in dialog.summary.text()
    assert dialog.detail.isReadOnly()
    assert dialog.table.editTriggers() == QAbstractItemView.EditTrigger.NoEditTriggers
    assert "NOT CHECKED" in dialog.detail.toPlainText()


@pytest.mark.parametrize("row", range(11))
def test_every_row_has_scientific_boundaries_without_loading(dialog, row, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("catalog must not open a model")

    monkeypatch.setattr(catalog, "open_optional_model", forbidden)
    dialog.table.selectRow(row)
    text = dialog.detail.toPlainText()
    assert dialog.options[row].model_id in text
    assert "NOT CHECKED; no model loaded" in text
    assert "INSPECTION ONLY" in text
    assert "simulated_offline only" in text
    assert "not experimental validation" in text
    assert "No reviewed component notes" not in text


def test_detector_is_offered_only_through_its_safe_export(dialog):
    row = next(i for i, o in enumerate(dialog.options) if o.model_id == "qkd.detector.boosting")
    dialog.table.selectRow(row)
    assert dialog.table.records.rows[row][1][0] == "OFFLINE ONLY"
    text = dialog.detail.toPlainText()
    assert "safe export" in text and "never deserialised" in text and "FPGA detector is a different model" in text


def test_non_ml_baseline_is_identified(dialog):
    option = next(o for o in dialog.options if o.model_id == "qnet.allocator.persistence")
    assert "non-ML baseline" in model_notes(option)


@pytest.mark.parametrize("size", [(640, 480), (1060, 760), (1600, 1000)])
def test_catalog_reflows_and_scrolls_without_clipping(app, dialog, size):
    dialog.show()
    # Exercise arbitrary canvas sizes after the initial work-area fit.
    dialog.resize(*size)
    app.processEvents()
    assert (dialog.width(), dialog.height()) == size
    for widget in (dialog.summary, dialog.splitter, dialog.close_button):
        assert dialog.rect().contains(widget.geometry())
    dialog.table.selectRow(10)
    dialog.table.scrollToBottom()
    dialog.table.horizontalScrollBar().setValue(dialog.table.horizontalScrollBar().maximum())
    app.processEvents()
    last_row = dialog.table.model().index(10, 3)
    assert dialog.table.viewport().rect().intersects(dialog.table.visualRect(last_row))
    assert dialog.detail.verticalScrollBarPolicy() == Qt.ScrollBarPolicy.ScrollBarAsNeeded
    if size == (640, 480):
        assert dialog.table.verticalScrollBar().maximum() > 0
    assert dialog.table.columnWidth(3) >= dialog.table.sizeHintForColumn(3)


def test_entry_is_click_only_and_leaves_policy_and_source_unchanged(app):
    screen = EdgeControlScreen()
    commands = []
    screen.command.connect(lambda *args: commands.append(args))
    try:
        screen.apply(MockProvider().snapshot())
        screen.show()
        app.processEvents()
        assert not screen.model_catalog.isVisible()
        assert screen.catalog_button.isEnabled()
        assert screen.catalog_button.property("operation") is None
        QTest.mouseClick(screen.catalog_button, Qt.MouseButton.LeftButton)
        app.processEvents()
        assert screen.model_catalog.isVisible()
        screen.ml_on = True  # an existing runtime policy is not this catalog's state
        for index in range(len(screen.model_catalog.options)):
            screen.model_catalog.table.selectRow(index)
        assert screen.ml_on is True
        assert not commands
        screen.apply(artificial_snapshot())
        assert not commands
        assert all(not o.default_enabled for o in screen.model_catalog.options)
        assert "NOT CHECKED" in screen.model_catalog.detail.toPlainText()
        QTest.mouseClick(screen.model_catalog.close_button, Qt.MouseButton.LeftButton)
        assert not screen.model_catalog.isVisible()
        QTest.mouseClick(screen.catalog_button, Qt.MouseButton.LeftButton)
        assert screen.model_catalog.isVisible()
        assert not commands
    finally:
        screen.model_catalog.close()
        screen.close()
        screen.deleteLater()


def test_native_catalog_does_not_import_numeric_backends_or_open_artifacts(tmp_path):
    # Fresh process: other tests may already have imported NumPy or model modules.
    # Block numerical adapters, weight formats and connections during construction/use.
    script = '''
import sys
from PySide6.QtWidgets import QApplication
def guard(event, args):
    if event == "open" and str(args[0]).lower().endswith((".zip", ".npz", ".pkl", ".pickle")):
        raise AssertionError("artifact access")
    if event in ("socket.connect", "socket.getaddrinfo"):
        raise AssertionError("network access")
sys.addaudithook(guard)
from quantum_autopilot.console.widgets.model_catalog import ModelCatalog
app = QApplication([])
dialog = ModelCatalog()
for row in range(len(dialog.options)):
    dialog.table.selectRow(row)
assert "numpy" not in sys.modules
assert "torch" not in sys.modules
assert "quantum_autopilot.model_intake.artifacts" not in sys.modules
assert "quantum_autopilot.model_intake.inference" not in sys.modules
dialog.close()
'''
    result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True,
                            timeout=30, cwd=tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr


def checked(model_id="qkd.keyrate.actor"):
    return ArchiveVerification(model_id, "qkd", "C:/test/fixture.zip", "0" * 64,
                               "2026-09-22T00:00:00+00:00", 3, 200, ("detector.pkl",), "offline_only")


def settle(app, checker):
    deadline = time.monotonic() + 10
    while checker.active and time.monotonic() < deadline:
        app.processEvents()
        QTest.qWait(10)
    assert not checker.active, "verification failed to finish"


def test_explicit_chooser_verifies_off_main_thread_without_activating(app, dialog, monkeypatch):
    calls = []
    main_thread = threading.get_ident()

    def verify(model_id, path):
        calls.append((model_id, path, threading.get_ident()))
        return checked(model_id)

    monkeypatch.setattr(model_archive_check, "verify_model_archive", verify)
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a: ("C:/test/fixture.zip", ""))
    dialog.show()
    assert not dialog.verify_button.isEnabled()
    dialog.table.selectRow(1)
    assert dialog.verify_button.isEnabled()
    QTest.mouseClick(dialog.verify_button, Qt.MouseButton.LeftButton)
    settle(app, dialog.checker)
    assert calls == [("qkd.keyrate.actor", "C:/test/fixture.zip", calls[0][2])]
    assert calls[0][2] != main_thread
    assert "IDENTITY VERIFIED" in dialog.summary.text()
    assert "NOT INSTALLED; NOT ACTIVE" in dialog.detail.toPlainText()
    assert all(not (o.default_enabled or o.runtime_available) for o in dialog.options)
    QTest.mouseClick(dialog.clear_button, Qt.MouseButton.LeftButton)
    assert dialog.checker.result is None
    assert "NOT CHECKED" in dialog.detail.toPlainText()


def test_cancel_file_chooser_does_not_read_anything(app, dialog, monkeypatch):
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a: ("", ""))
    monkeypatch.setattr(model_archive_check, "verify_model_archive", lambda *a: pytest.fail("read on cancel"))
    dialog.show()
    dialog.table.selectRow(1)
    QTest.mouseClick(dialog.verify_button, Qt.MouseButton.LeftButton)
    assert not dialog.checker.active
    assert dialog.checker.result is None


def test_error_clears_previous_success_and_is_plain_text(app, dialog, monkeypatch):
    dialog.table.selectRow(1)
    dialog.checker.result = checked()
    dialog.checker.changed.emit()

    def refused(*args):
        raise ValueError("<b>changed hash</b>")

    monkeypatch.setattr(model_archive_check, "verify_model_archive", refused)
    dialog.checker.start("qkd.keyrate.actor", "fixture.zip")
    settle(app, dialog.checker)
    assert dialog.checker.result is None
    assert "PACKAGE REFUSED: <b>changed hash</b>" in dialog.detail.toPlainText()
    assert "IDENTITY VERIFIED" not in dialog.detail.toPlainText()


@pytest.mark.parametrize("action", ["select", "close", "clear"])
def test_late_verification_never_repopulates_invalidated_view(app, dialog, action):
    dialog.show()
    dialog.table.selectRow(1)
    pending = Future()
    pending.set_running_or_notify_cancel()
    dialog.checker._future = pending
    dialog.checker._pending_generation = dialog.checker._generation
    dialog.checker.changed.emit()
    assert not dialog.verify_button.isEnabled()
    if action == "select":
        dialog.table.selectRow(3)
    elif action == "close":
        dialog.close()
    else:
        dialog.checker.clear()
    with pytest.raises(ValueError, match="still finishing"):
        dialog.checker.start("qkd.keyrate.actor", "never-read.zip")
    pending.set_result(checked())
    dialog.checker.poll()
    assert dialog.checker.result is None
    assert not dialog.checker.active
    assert "IDENTITY VERIFIED" not in dialog.detail.toPlainText()


def test_source_change_clears_verification(app):
    screen = EdgeControlScreen()
    try:
        screen.apply(MockProvider().snapshot())
        screen.model_catalog.table.selectRow(1)
        screen.model_catalog.checker.result = checked()
        screen.model_catalog.checker.changed.emit()
        screen.apply(artificial_snapshot())
        assert screen.model_catalog.checker.result is None
        assert "NOT CHECKED" in screen.model_catalog.detail.toPlainText()
    finally:
        screen.close()
        screen.deleteLater()


def test_same_metadata_source_replacement_still_invalidates_check(app):
    from quantum_autopilot.console.shell import ConsoleWindow

    window = ConsoleWindow(MockProvider(), skip_auth=True)
    try:
        checker = window.screens["fpga"].model_catalog.checker
        checker.result = checked()
        assert window.switch_input(MockProvider)
        assert checker.result is None
    finally:
        window.close()


def test_context_change_while_file_chooser_is_open_refuses_read(app, dialog, monkeypatch):
    def choose(*args):
        dialog.checker.clear()  # source replacement while the native chooser owns the loop
        return "fixture.zip", ""

    monkeypatch.setattr(QFileDialog, "getOpenFileName", choose)
    monkeypatch.setattr(model_archive_check, "verify_model_archive", lambda *a: pytest.fail("stale chooser"))
    dialog.show()
    dialog.table.selectRow(1)
    QTest.mouseClick(dialog.verify_button, Qt.MouseButton.LeftButton)
    assert not dialog.checker.active
