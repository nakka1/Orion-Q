"""Native approval requires explicit confirmation bound to a live review selection."""

from dataclasses import replace
import json
import os
from pathlib import Path
from threading import Event, get_ident
import time

import pytest

from tests import test_console_policy_review as shared
from quantum_autopilot.console.widgets import policy_review as ui
from quantum_autopilot.console.policy_review_binding import PolicyReviewBinding
from quantum_autopilot.control_plane.policy import RepeaterSandboxPolicy
from quantum_autopilot.control_plane.topology import _sha256

app, provider, panel = shared.app, shared.provider, shared.panel
click, wait, review = shared.click, shared.wait, shared.review


def confirm(view, label="local operator"):
    click(view.approve_button)
    dialog = view.confirmation
    assert dialog is not None and not dialog.record_button.isEnabled()
    dialog.operator_label.setText(label)
    dialog.confirmed.setChecked(True)
    return dialog


def approve(panel, app):
    view = panel[0]
    review(panel, app)
    dialog = confirm(view)
    click(dialog.record_button)
    wait(app, lambda: not view.job.active)
    assert view.approval, view.status.text()
    return view.approval


def test_explicit_confirmation_background_approval_and_no_activation(panel, app, monkeypatch):
    view, _, service = panel
    runtime = service.controller_runtime
    before = runtime.journal.path.read_bytes()

    def forbidden(*args, **kwargs):
        pytest.fail("approval cannot execute or decide")

    monkeypatch.setattr(runtime, "cycle", forbidden)
    monkeypatch.setattr(runtime.policy, "decide", forbidden)
    review(panel, app)
    captured = json.loads(json.dumps(view.review))
    assert view.approval is None and not view.export_approval_button.isEnabled()
    dialog = confirm(view)
    assert captured["record_sha256"] in dialog.details.toPlainText()
    assert "Baseline threshold: 0.65" in dialog.details.toPlainText()
    assert "Proposed threshold: 0.7" in dialog.details.toPlainText()
    assert not view.review_button.isEnabled() and not view.export_button.isEnabled()
    if folder := os.environ.get("ORION_POLICY_APPROVAL_EVIDENCE"):
        out = Path(folder)
        out.mkdir(parents=True, exist_ok=True)
        dialog.resize(600, 480)
        app.processEvents()
        assert dialog.grab().save(str(out / "confirmation-600.png"))
    threads = []
    original = PolicyReviewBinding.run

    def run(self, *args):
        threads.append(get_ident())
        return original(self, *args)

    monkeypatch.setattr(PolicyReviewBinding, "run", run)
    click(dialog.record_button)
    wait(app, lambda: not view.job.active)
    approval = view.approval
    assert approval and approval["record"]["approved_review"] == captured
    assert threads and all(t != get_ident() for t in threads)
    assert approval["record_sha256"] == _sha256(approval["record"])
    assert approval["record"]["operator_approval_recorded"] is True
    assert not approval["record"]["activation_authority"] and not approval["record"]["policy_activated"]
    assert runtime.policy.engine.threshold == 0.65 and runtime.journal.path.read_bytes() == before
    assert not view.approve_button.isEnabled() and view.export_approval_button.isEnabled()
    assert "APPROVAL RECORDED" in view.comparison.toPlainText()
    assert json.loads(view.receipt.toPlainText()) == approval
    for width, height in ((640, 500), (1000, 700)):
        view.resize(width, height)
        app.processEvents()
        assert view.comparison.height() > 130
        assert (
            view.export_approval_button.mapTo(view, view.export_approval_button.rect().bottomRight()).y()
            < view.height()
        )
        if folder:
            assert view.grab().save(str(out / f"approval-{width}.png"))
    if folder:
        (out / "approval.json").write_text(json.dumps(approval, indent=2), encoding="utf-8")


@pytest.mark.parametrize("label", ["", " label", "label ", "x\x00y"])
def test_invalid_operator_label_cannot_confirm(panel, app, label):
    view = panel[0]
    review(panel, app)
    dialog = confirm(view, label)
    assert not dialog.record_button.isEnabled()
    dialog.accept()
    assert view.approval is None and not view.job.active and dialog.isVisible()


def test_unchecked_confirmation_and_cancel_leave_review_unchanged(panel, app):
    view = panel[0]
    review(panel, app)
    captured = view.review
    click(view.approve_button)
    dialog = view.confirmation
    assert dialog.operator_label.text() == "" and not dialog.confirmed.isChecked()
    dialog.operator_label.setText("operator")
    dialog.accept()
    assert not view.job.active and dialog.isVisible()
    click(dialog.cancel_button)
    assert view.confirmation is None and view.review == captured and view.approval is None
    assert view.approve_button.isEnabled()


@pytest.mark.parametrize("change", ["source", "close", "draft", "expiry", "capability"])
def test_confirmation_is_dismissed_on_context_invalidation(panel, app, monkeypatch, change):
    view, wrapper, _ = panel
    review(panel, app)
    dialog = confirm(view)
    if change == "source":
        wrapper._generation += 1
    elif change == "close":
        view.close()
    elif change == "draft":
        view.threshold.setText(".8")
    elif change == "expiry":
        view.deadline = time.monotonic() - 1
    else:
        caps = wrapper.active.capabilities
        monkeypatch.setattr(
            type(wrapper.active),
            "capabilities",
            property(
                lambda self: replace(
                    caps, operations=tuple(op for op in caps.operations if op != "approve_policy_proposal")
                )
            ),
        )
    view._tick()
    assert view.confirmation is None and not dialog.isVisible()
    dialog.accept()  # A delayed accepted signal cannot bypass the captured epoch.
    assert not view.job.active and view.approval is None


def test_policy_changed_during_confirmation_is_refused_by_backend(panel, app):
    view, _, service = panel
    review(panel, app)
    dialog = confirm(view)
    service.controller_runtime.policy = RepeaterSandboxPolicy(0.8)
    click(dialog.record_button)
    wait(app, lambda: not view.job.active)
    assert view.approval is None and view.context is None
    assert "REFUSED" in view.status.text()
    assert service.controller_runtime.policy.engine.threshold == 0.8


def test_stale_review_cannot_open_confirmation(panel, app):
    view, _, service = panel
    shared.prepare(panel, app)
    service.controller_runtime.policy = RepeaterSandboxPolicy(0.8)
    click(view.review_button)
    wait(app, lambda: not view.job.active)
    assert view.review["record"]["status"] == "STALE" and not view.approve_button.isEnabled()
    view.confirm_approval()
    assert view.confirmation is None


@pytest.mark.parametrize("change", ["close", "source", "draft"])
def test_late_approval_result_is_discarded_without_queue(panel, app, monkeypatch, change):
    view, wrapper, _ = panel
    review(panel, app)
    dialog = confirm(view)
    entered, release = Event(), Event()
    original = PolicyReviewBinding.run

    def slow(self, *args):
        entered.set()
        assert release.wait(5)
        return original(self, *args)

    monkeypatch.setattr(PolicyReviewBinding, "run", slow)
    click(dialog.record_button)
    wait(app, entered.is_set)
    try:
        if change == "close":
            view.close()
        elif change == "source":
            wrapper._generation += 1
            view._tick()
        else:
            view.threshold.setText(".8")
        view.load_baseline()
        view.confirm_approval()
        assert view.confirmation is None
    finally:
        release.set()
    wait(app, lambda: not view.job.active)
    assert view.approval is None and view.context is None


def test_new_review_clears_approval_and_requires_fresh_confirmation(panel, app):
    view = panel[0]
    old = approve(panel, app)
    click(view.review_button)
    wait(app, lambda: not view.job.active)
    assert view.approval is None and not view.export_approval_button.isEnabled()
    assert view.approve_button.isEnabled()
    click(view.approve_button)
    dialog = view.confirmation
    assert dialog.operator_label.text() == "" and not dialog.confirmed.isChecked()
    assert view.review["record_sha256"] != old["record"]["approved_review"]["record_sha256"]


@pytest.mark.parametrize("change", ["cancel", "draft", "source", "expiry", "close"])
def test_export_picker_context_changes_prevent_approval_export(panel, app, monkeypatch, tmp_path, change):
    view, wrapper, _ = panel
    approve(panel, app)
    target = tmp_path / "refused.json"

    def choose(*args):
        if change == "draft":
            view.threshold.setText(".8")
        elif change == "source":
            wrapper._generation += 1
        elif change == "expiry":
            view.deadline = time.monotonic() - 1
        elif change == "close":
            view.close()
        return ("" if change == "cancel" else str(target)), "JSON"

    monkeypatch.setattr(ui.QFileDialog, "getSaveFileName", choose)
    click(view.export_approval_button)
    assert not target.exists() and not view.export_job.active


def test_export_is_captured_background_and_exclusive(panel, app, monkeypatch, tmp_path):
    view, _, service = panel
    captured = json.loads(json.dumps(approve(panel, app)))
    target = tmp_path / "approval.json"
    threads = []
    original = ui._export_captured

    def write(*args):
        threads.append(get_ident())
        return original(*args)

    monkeypatch.setattr(ui, "_export_captured", write)

    def choose(*args):
        service.controller_runtime.policy = RepeaterSandboxPolicy(0.8)
        return str(target), "JSON"

    monkeypatch.setattr(ui.QFileDialog, "getSaveFileName", choose)
    click(view.export_approval_button)
    wait(app, lambda: not view.export_job.active)
    assert json.loads(target.read_text(encoding="utf-8")) == captured
    assert threads[0] != get_ident()
    previous = target.read_bytes()
    click(view.export_approval_button)
    wait(app, lambda: not view.export_job.active)
    assert target.read_bytes() == previous and "EXPORT REFUSED" in view.status.text()


def test_accepted_export_finishes_after_close_without_restoring_approval(panel, app, monkeypatch, tmp_path):
    view = panel[0]
    captured = approve(panel, app)
    target = tmp_path / "accepted.json"
    entered, release = Event(), Event()
    original = ui._export_captured

    def slow(*args):
        entered.set()
        assert release.wait(5)
        return original(*args)

    monkeypatch.setattr(ui, "_export_captured", slow)
    monkeypatch.setattr(ui.QFileDialog, "getSaveFileName", lambda *args: (str(target), "JSON"))
    click(view.export_approval_button)
    wait(app, entered.is_set)
    try:
        view.close()
        view.show()
        view.load_baseline()
        assert not view.job.active
    finally:
        release.set()
    wait(app, lambda: not view.export_job.active)
    assert json.loads(target.read_text(encoding="utf-8")) == captured
    assert view.approval is None and view.export_job.result is None


def test_approval_capability_is_separate_and_unavailable_read_only(provider, app):
    from quantum_autopilot.console.capabilities import SourceCapabilities, synthetic_capabilities

    assert not provider.capabilities.allows("approve_policy_proposal")
    provider.service.controller_runtime
    assert provider.capabilities.allows("approve_policy_proposal")
    assert not synthetic_capabilities().allows("approve_policy_proposal")
    with pytest.raises(ValueError):
        SourceCapabilities(mode="read_only", operations=("approve_policy_proposal",))


def test_repeated_cancel_disposes_dialog_without_late_callbacks(panel, app):
    from PySide6.QtCore import QCoreApplication, QEvent
    import shiboken6

    view = panel[0]
    review(panel, app)
    original = view.review
    for _ in range(5):
        dialog = confirm(view)
        click(dialog.cancel_button)
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        app.processEvents()
        assert not shiboken6.isValid(dialog)
        assert view.confirmation is None and view.review == original and view.approval is None


def test_stop_during_approval_read_does_not_block_or_restore_receipt(panel, app, monkeypatch):
    from quantum_autopilot.control_plane import policy_preparation as backend

    view, _, service = panel
    review(panel, app)
    dialog = confirm(view)
    entered, release = Event(), Event()
    original = backend._read_topology

    def slow(*args):
        entered.set()
        assert release.wait(5)
        return original(*args)

    monkeypatch.setattr(backend, "_read_topology", slow)
    click(dialog.record_button)
    wait(app, entered.is_set)
    try:
        started = time.monotonic()
        service.invalidate_policy_preparation()
        view._tick()
        assert time.monotonic() - started < 0.5
        assert view.approval is None
    finally:
        release.set()
    wait(app, lambda: not view.job.active)
    assert view.approval is None and view.context is None
