"""Native polarisation loop: baseline runs, evidence-bound activation, fault fallback and rollback."""
import json
import os
import time

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication, QDialog  # noqa: E402

from quantum_autopilot.console import theme  # noqa: E402
from quantum_autopilot.console.widgets import polarisation_control as panel_module  # noqa: E402
from quantum_autopilot.console.widgets.polarisation_control import (  # noqa: E402
    PolarisationActivationConfirmation, PolarisationApprovalConfirmation, PolarisationControl,
)
from tests import test_polarisation_activation as helpers  # noqa: E402
from tests import test_polarisation_loop as loop_tests  # noqa: E402

pytestmark = pytest.mark.unit


@pytest.fixture(scope="module")
def app():
    instance = QApplication.instance() or QApplication([])
    theme.install(instance)
    return instance


@pytest.fixture
def short(monkeypatch):
    monkeypatch.setattr(panel_module, "DURATION_S", 0.5)
    monkeypatch.setattr(panel_module, "FAULT_STEPS", range(20, 40))


def settle(app, dialog):
    deadline = time.monotonic() + 30
    while dialog.job.active and time.monotonic() < deadline:
        app.processEvents()
        QTest.qWait(10)
    app.processEvents()
    assert not dialog.job.active


def click(app, dialog, button):
    assert button.isEnabled(), dialog.output.toPlainText()
    QTest.mouseClick(button, Qt.MouseButton.LeftButton)
    settle(app, dialog)


@pytest.fixture
def confirm(monkeypatch):
    def approve(self):
        self.operator_label.setText("operador-teste")
        self.confirmed.setChecked(True)
        return QDialog.DialogCode.Accepted

    def activate(self):
        self.confirmed.setChecked(True)
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(PolarisationApprovalConfirmation, "exec", approve)
    monkeypatch.setattr(PolarisationActivationConfirmation, "exec", activate)


def choose(monkeypatch, *paths):
    queue = list(paths)
    monkeypatch.setattr(panel_module.QFileDialog, "getOpenFileName", lambda *a, **k: (str(queue.pop(0)), "JSON"))


def test_baseline_runs_without_any_model(app, tmp_path, short):
    dialog = PolarisationControl(journal_path=tmp_path / "journal.sqlite")
    try:
        dialog.show()
        assert dialog.slot is None and not dialog.prepare_button.isEnabled()
        click(app, dialog, dialog.run_button)
        assert dialog.runs[-1].controller_id == "baseline.alpha_beta"
        assert dialog.chart.series[0][0] == list(dialog.runs[-1].qber_trace_100ms)
        assert "grey = classical" in dialog.legend.text()
        assert "RUN RECORDED" in dialog.output.toPlainText() and "NO_LOCAL_ACTIVATION" in dialog.output.toPlainText()
        assert not dialog.rollback_button.isEnabled()
        assert (tmp_path / "journal.sqlite").is_file()
    finally:
        dialog.close()
        dialog.deleteLater()


def test_activation_fault_and_rollback_through_the_panel(app, tmp_path, monkeypatch, short, confirm):
    archive = loop_tests._archive(tmp_path, monkeypatch)
    evidence, _ = helpers.evidence(tmp_path, archive)
    dialog = PolarisationControl(journal_path=tmp_path / "journal.sqlite")
    try:
        dialog.show()
        choose(monkeypatch, tmp_path / "qnet.zip", evidence)
        click(app, dialog, dialog.package_button)
        assert dialog.package is not None and dialog.slot.archive_path == dialog.package.path
        click(app, dialog, dialog.evidence_button)
        click(app, dialog, dialog.prepare_button)
        assert "PROPOSAL CAPTURED" in dialog.output.toPlainText() and "3.30x lower QBER" in dialog.output.toPlainText()
        assert dialog._live() == "baseline.alpha_beta" and not dialog.activate_button.isEnabled()
        click(app, dialog, dialog.review_button)
        click(app, dialog, dialog.approve_button)
        click(app, dialog, dialog.activate_button)
        assert "COMMITTED · ACTIVATION" in dialog.output.toPlainText()
        click(app, dialog, dialog.read_button)
        assert "READBACK · ACTIVE" in dialog.output.toPlainText()
        dialog.fault.setChecked(True)
        click(app, dialog, dialog.run_button)
        run = dialog.runs[-1]
        assert run.controller_id == helpers.RIDGE and run.supervisor["faults"] == 8
        assert run.supervisor["degraded_at_s"] is not None and "0.054 s" in dialog.output.toPlainText()
        assert len(run.qber_trace_100ms) == 5 and len(dialog.chart.series) == 1
        assert "fault injected" in dialog.legend.text()
        click(app, dialog, dialog.rollback_button)
        assert "ROLLBACK" in dialog.output.toPlainText()
        click(app, dialog, dialog.review_button)
        click(app, dialog, dialog.approve_button)
        click(app, dialog, dialog.activate_button)
        assert "COMMITTED · ROLLBACK" in dialog.output.toPlainText() and dialog._live() == "baseline.alpha_beta"
        exported = tmp_path / "receipt.json"
        monkeypatch.setattr(panel_module.QFileDialog, "getSaveFileName", lambda *a, **k: (str(exported), "JSON"))
        QTest.mouseClick(dialog.export_button, Qt.MouseButton.LeftButton)
        assert json.loads(exported.read_text(encoding="utf-8"))["record"]["transition_kind"] == "rollback"
        QTest.mouseClick(dialog.export_button, Qt.MouseButton.LeftButton)
        assert "EXPORT REFUSED" in dialog.output.toPlainText()  # never overwrites
    finally:
        dialog.close()
        dialog.deleteLater()


def test_contract_refusal_and_selection_invalidation(app, tmp_path, monkeypatch, short, confirm):
    archive = loop_tests._archive(tmp_path, monkeypatch)
    evidence, _ = helpers.evidence(tmp_path, archive)
    dialog = PolarisationControl(journal_path=tmp_path / "journal.sqlite")
    try:
        dialog.show()
        choose(monkeypatch, tmp_path / "qnet.zip", evidence)
        click(app, dialog, dialog.package_button)
        click(app, dialog, dialog.evidence_button)
        dialog.checkpoint.setCurrentText("qnet.compensation.desenvolvimento.ridge")
        click(app, dialog, dialog.prepare_button)
        assert "REFUSED · PREPARE · contract refuses" in dialog.output.toPlainText()
        dialog.checkpoint.setCurrentText(helpers.RIDGE)
        click(app, dialog, dialog.prepare_button)
        click(app, dialog, dialog.review_button)
        assert dialog.approve_button.isEnabled()
        dialog.checkpoint.setCurrentText("qnet.compensation.calibrado.mlp")
        assert dialog.proposal is None and not dialog.approve_button.isEnabled()
        dialog.hide()
        assert dialog.slot._proposal is None and dialog._live() == "baseline.alpha_beta"
    finally:
        dialog.close()
        dialog.deleteLater()


def test_confirmations_require_explicit_declarations(app, tmp_path, monkeypatch):
    archive = loop_tests._archive(tmp_path, monkeypatch)
    evidence, digest = helpers.evidence(tmp_path, archive)
    slot = helpers.slot_for(tmp_path, archive)
    proposal = slot.prepare(helpers.RIDGE, evidence_path=evidence, evidence_sha256=digest)
    review = slot.review(proposal["record"]["proposal_id"], proposal["record_sha256"])
    approval_dialog = PolarisationApprovalConfirmation(proposal, review)
    assert not approval_dialog.accept_button.isEnabled()
    approval_dialog.confirmed.setChecked(True)
    assert not approval_dialog.accept_button.isEnabled()  # label required
    approval_dialog.operator_label.setText(" padded")
    assert not approval_dialog.accept_button.isEnabled()
    approval_dialog.operator_label.setText("operador")
    approval = slot.approve(approval_dialog.request())
    activation_dialog = PolarisationActivationConfirmation(proposal, approval)
    with pytest.raises(ValueError):
        activation_dialog.request()
    activation_dialog.confirmed.setChecked(True)
    assert slot.activate(activation_dialog.request())["record"]["status"] == "COMMITTED"
    for dialog in (approval_dialog, activation_dialog):
        dialog.deleteLater()


def test_edge_control_offers_the_panel(app):
    from quantum_autopilot.console.screens.fpga import EdgeControlScreen

    screen = EdgeControlScreen()
    try:
        assert screen.polarisation_button.isEnabled() and screen.polarisation_control.slot is None
        QTest.mouseClick(screen.polarisation_button, Qt.MouseButton.LeftButton)
        assert screen.polarisation_control.isVisible()
    finally:
        screen.polarisation_control.close()
        screen.deleteLater()
