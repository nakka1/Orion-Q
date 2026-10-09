"""The native decision audit reads captured simulation evidence without control authority."""

import json
import os
from pathlib import Path
from threading import Event, get_ident

import pytest

from tests import test_console_policy_review as shared
from tests import test_policy_decisions as backend_tests
from tests import test_control_plane_sandbox as sandbox
from quantum_autopilot.console.source_switch import SwitchableProvider
from quantum_autopilot.console.widgets import policy_decisions as ui
from quantum_autopilot.control_plane.topology import default_simulated_topology

app, provider = shared.app, shared.provider
click, wait = shared.click, shared.wait


@pytest.fixture
def panel(provider, app):
    from quantum_autopilot.console.theme import install

    install(app)
    runtime = provider.service.controller_runtime
    provider.service.topology_store.initialize(default_simulated_topology())
    runtime.cycle(sandbox.snapshot(request_id="baseline"))
    backend_tests.activate(provider.service)
    runtime.cycle(sandbox.snapshot(request_id="veto", prediction_uncertainty=0.8))
    wrapper = SwitchableProvider(provider)
    wrapper.running = True
    view = ui.PolicyDecisionAudit()
    view.attach(wrapper)
    view.show()
    app.processEvents()
    yield view, wrapper, provider.service
    view.close()
    wait(app, lambda: not view.job.active and not view.export_job.active)
    wrapper.running = False


def load(view, app):
    click(view.load_button)
    wait(app, lambda: not view.job.active)
    assert view.history, view.status.text()


def explain(view, app):
    load(view, app)
    click(view.explain_button)
    wait(app, lambda: not view.job.active)
    assert view.explanation, view.status.text()
    return view.explanation


@pytest.mark.parametrize('pane', ['details', 'receipt'])
def test_scroll_changes_visible_content_and_survives_watchdog(panel, app, pane):
    from PySide6.QtCore import QEvent, QObject, QPointF, QPoint, Qt
    from PySide6.QtGui import QWheelEvent
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QTabWidget

    view = panel[0]
    explain(view, app)
    edit = getattr(view, pane)
    view.findChild(QTabWidget).setCurrentWidget(edit)
    app.processEvents()
    paints = []

    class PaintObserver(QObject):
        def eventFilter(self, watched, event):
            if event.type() == QEvent.Type.Paint:
                paints.append(event.region().boundingRect())
            return False

    observer = PaintObserver(edit)
    edit.viewport().installEventFilter(observer)
    before = edit.firstVisibleBlock().blockNumber()
    revision = edit.document().revision()
    point = edit.viewport().rect().center()
    event = QWheelEvent(QPointF(point), QPointF(edit.viewport().mapToGlobal(point)), QPoint(), QPoint(0, -720),
                        Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier, Qt.ScrollPhase.NoScrollPhase, False)
    app.sendEvent(edit.viewport(), event)
    QTest.qWait(20)
    value = edit.verticalScrollBar().value()
    assert value > 0 and edit.firstVisibleBlock().blockNumber() > before
    assert paints and any(rect.height() >= edit.viewport().height() for rect in paints)
    for _ in range(12):
        view._tick()
    QTest.qWait(300)
    assert edit.verticalScrollBar().value() == value
    assert edit.document().revision() == revision


def test_explicit_background_explanation_and_captured_receipt(panel, app, monkeypatch):
    view, _, service = panel
    runtime = service.controller_runtime
    before = runtime.journal.path.read_bytes()

    def forbidden(*args, **kwargs):
        pytest.fail("audit cannot decide or execute")

    monkeypatch.setattr(runtime, "cycle", forbidden)
    monkeypatch.setattr(runtime.policy, "decide", forbidden)
    threads = []
    original = ui.explain_decision

    def run(*args):
        threads.append(get_ident())
        return original(*args)

    monkeypatch.setattr(ui, "explain_decision", run)
    assert view.history is None and view.explanation is None and not view.job.active
    receipt = explain(view, app)
    assert receipt["record"]["request_id"] == "veto"
    assert receipt["record"]["policy_evidence"]["status"] == "VERIFIED_RECORDED_HASH"
    assert "SAFETY AUTHORIZED: False" in view.details.toPlainText()
    assert "SAFETY EFFECTIVE: HALT" in view.details.toPlainText()
    assert "prediction_uncertainty_too_high" in view.details.toPlainText()
    assert json.loads(view.receipt.toPlainText()) == receipt
    assert threads and threads[0] != get_ident() and runtime.journal.path.read_bytes() == before
    if folder := os.environ.get("ORION_POLICY_DECISION_EVIDENCE"):
        out = Path(folder)
        out.mkdir(parents=True, exist_ok=True)
        (out / "explanation.json").write_text(json.dumps(receipt, indent=2), encoding="utf-8")
        for w, h in ((640, 480), (1000, 700)):
            view.resize(w, h)
            app.processEvents()
            assert view.details.height() > 200
            assert view.export_button.mapTo(view, view.export_button.rect().bottomRight()).x() < view.width()
            assert view.grab().save(str(out / f"audit-{w}.png"))


def test_baseline_selection_clears_old_explanation_and_never_uses_active_policy(panel, app):
    view = panel[0]
    explain(view, app)
    view.selection.setCurrentIndex(1)
    assert view.explanation is None and not view.export_button.isEnabled()
    click(view.explain_button)
    wait(app, lambda: not view.job.active)
    assert view.explanation["record"]["policy_evidence"]["status"] == "NOT_RECORDED"
    assert "Recorded threshold:" not in view.details.toPlainText()


@pytest.mark.parametrize("change", ["close", "source", "stop", "selection", "path"])
def test_late_read_is_discarded_on_invalidation(panel, app, monkeypatch, tmp_path, change):
    view, wrapper, service = panel
    load(view, app)
    entered, release = Event(), Event()
    original = ui.explain_decision

    def slow(*args):
        entered.set()
        assert release.wait(5)
        return original(*args)

    monkeypatch.setattr(ui, "explain_decision", slow)
    click(view.explain_button)
    wait(app, entered.is_set)
    try:
        if change == "close":
            view.close()
        elif change == "source":
            wrapper._generation += 1
        elif change == "stop":
            wrapper.running = False
        elif change == "path":
            service.control_journal_path = tmp_path / "other.sqlite"
        else:
            view.selection.setCurrentIndex(1)
        view._tick()
        assert view.explanation is None and not view.export_button.isEnabled()
    finally:
        release.set()
    wait(app, lambda: not view.job.active)
    assert view.explanation is None


@pytest.mark.parametrize("change", ["source", "selection", "close", "cancel"])
def test_export_dialog_revalidates_selection(panel, app, monkeypatch, tmp_path, change):
    view, wrapper, _ = panel
    explain(view, app)
    target = tmp_path / "receipt.json"

    def choose(*args):
        if change == "source":
            wrapper._generation += 1
        elif change == "selection":
            view.selection.setCurrentIndex(1)
        elif change == "close":
            view.close()
        return ("" if change == "cancel" else str(target)), "JSON"

    monkeypatch.setattr(ui.QFileDialog, "getSaveFileName", choose)
    click(view.export_button)
    assert not target.exists() and not view.export_job.active


def test_export_is_captured_background_exclusive(panel, app, monkeypatch, tmp_path):
    view, _, service = panel
    captured = explain(view, app)
    target = tmp_path / "receipt.json"
    threads = []
    original = ui._export_captured

    def write(*args):
        threads.append(get_ident())
        return original(*args)

    monkeypatch.setattr(ui, "_export_captured", write)
    monkeypatch.setattr(ui.QFileDialog, "getSaveFileName", lambda *args: (str(target), "JSON"))
    click(view.export_button)
    wait(app, lambda: not view.export_job.active)
    assert json.loads(target.read_text()) == captured and threads[0] != get_ident()
    before = target.read_bytes()
    click(view.export_button)
    wait(app, lambda: not view.export_job.active)
    assert target.read_bytes() == before and "EXPORT REFUSED" in view.status.text()
    assert len(service.controller_runtime.journal.list_recent()) == 2


def test_changed_journal_record_is_refused_without_exportable_explanation(panel, app):
    import sqlite3

    view, _, service = panel
    load(view, app)
    with sqlite3.connect(service.control_journal_path) as con:
        con.execute("UPDATE control_cycles SET record_json='{}' WHERE request_id='veto'")
    click(view.explain_button)
    wait(app, lambda: not view.job.active)
    assert "READ REFUSED" in view.status.text() and view.explanation is None
    assert not view.export_button.isEnabled()


def test_missing_store_does_not_initialize_runtime(provider, app, tmp_path):
    wrapper = SwitchableProvider(provider)
    wrapper.running = True
    provider.service.control_journal_path = tmp_path / "absent.sqlite"
    view = ui.PolicyDecisionAudit()
    view.attach(wrapper)
    view.show()
    app.processEvents()
    try:
        click(view.load_button)
        wait(app, lambda: not view.job.active)
        assert "READ REFUSED" in view.status.text()
        assert (
            not provider.service.control_journal_path.exists()
            and provider.service._controller_runtime is None
        )
    finally:
        view.close()
        wrapper.running = False


def test_synthetic_source_cannot_read_local_decision_journal(app, tmp_path):
    from quantum_autopilot.console.synthetic import SyntheticProvider
    from quantum_autopilot.console.capabilities import synthetic_capabilities

    assert not synthetic_capabilities().allows("read_policy_decisions")
    provider = SyntheticProvider(Path.cwd(), tmp_path / "synthetic")
    wrapper = SwitchableProvider(provider)
    wrapper.running = True
    view = ui.PolicyDecisionAudit()
    view.attach(wrapper)
    view.show()
    app.processEvents()
    try:
        assert not view.load_button.isEnabled()
        view.load()
        assert not view.job.active
    finally:
        view.close()
        wrapper.running = False
