"""Explicit native activation and timestamped readback, restricted to simulation fixtures."""

from dataclasses import replace
import json
import os
from pathlib import Path
from threading import Event, get_ident
import time

import pytest

from tests import test_console_policy_approval as approvals
from tests import test_console_policy_review as shared
from quantum_autopilot.console.widgets import policy_review as ui
from quantum_autopilot.console.policy_review_binding import PolicyReviewBinding
from quantum_autopilot.control_plane import policy_activation as backend
from quantum_autopilot.control_plane import policy_preparation as preparation
from quantum_autopilot.control_plane.policy import RepeaterSandboxPolicy
from quantum_autopilot.control_plane.topology import _sha256

app, provider, panel = shared.app, shared.provider, shared.panel
click, wait = shared.click, shared.wait


def confirm(view):
    click(view.activate_button)
    dialog = view.confirmation
    assert dialog is not None and not dialog.activate_button.isEnabled()
    dialog.confirmed.setChecked(True)
    return dialog


def activate(panel, app):
    approvals.approve(panel, app)
    view = panel[0]
    click(confirm(view).activate_button)
    wait(app, lambda: not view.job.active)
    assert view.activation, view.status.text()
    return view.activation


def read_active(view, app):
    view.tabs.setCurrentIndex(2)
    click(view.read_active_button)
    wait(app, lambda: not view.job.active)
    assert view.active_observation, view.status.text()
    return view.active_observation


def test_activation_explicit_background_confirmation_and_readback(panel, app, monkeypatch):
    view, _, service = panel
    approval = approvals.approve(panel, app)
    runtime = service.controller_runtime
    old_policy = runtime.policy

    def forbidden(*args, **kwargs):
        pytest.fail("activation must not execute cycles or decide")

    monkeypatch.setattr(runtime, "cycle", forbidden)
    monkeypatch.setattr(old_policy, "decide", forbidden)
    dialog = confirm(view)
    assert approval["record_sha256"] in dialog.details.toPlainText()
    assert "Previous threshold: 0.65" in dialog.details.toPlainText()
    assert "Approved threshold: 0.7" in dialog.details.toPlainText()
    assert not view.load_button.isEnabled() and not view.read_active_button.isEnabled()
    assert runtime.policy is old_policy
    folder = os.environ.get("ORION_POLICY_ACTIVATION_EVIDENCE")
    if folder:
        out = Path(folder)
        out.mkdir(parents=True, exist_ok=True)
        dialog.resize(600, 450)
        app.processEvents()
        assert dialog.grab().save(str(out / "confirmation-600.png"))
    threads = []
    original = PolicyReviewBinding.run

    def run(self, *args):
        threads.append(get_ident())
        return original(self, *args)

    monkeypatch.setattr(PolicyReviewBinding, "run", run)
    click(dialog.activate_button)
    wait(app, lambda: not view.job.active)
    result = view.activation
    assert result and result["record"]["approval"] == approval
    assert result["record_sha256"] == _sha256(result["record"])
    assert runtime.policy.engine.threshold == 0.7 and runtime.policy is not old_policy
    assert runtime.journal.list_recent() == []
    assert (
        not result["record"]["optional_models_activated"]
        and not result["record"]["physical_action_performed"]
    )
    assert view.approval is None and view.deadline is None and not view.activate_button.isEnabled()
    assert "COMMITTED" in view.active_details.toPlainText() and view.active_observation is None
    state = read_active(view, app)
    assert state["record"]["status"] == "ACTIVE" and state["record"]["activation"] == result
    assert state["record"]["observed_at_utc"] in view.active_details.toPlainText()
    assert json.loads(view.receipt.toPlainText()) == state
    assert threads and all(t != get_ident() for t in threads)
    for width, height in ((640, 500), (1000, 700)):
        view.resize(width, height)
        app.processEvents()
        assert view.active_details.height() > 90
        assert (
            view.export_state_button.mapTo(view, view.export_state_button.rect().bottomRight()).x()
            < view.width()
        )
        if folder:
            assert view.grab().save(str(out / f"active-{width}.png"))
    if folder:
        (out / "activation.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
        (out / "readback.json").write_text(json.dumps(state, indent=2), encoding="utf-8")


def test_unchecked_and_cancel_do_not_activate(panel, app):
    view, _, service = panel
    approval = approvals.approve(panel, app)
    before = service.controller_runtime.journal.path.read_bytes()
    click(view.activate_button)
    dialog = view.confirmation
    assert not dialog.confirmed.isChecked()
    dialog.accept()
    assert dialog.isVisible() and not view.job.active
    click(dialog.cancel_button)
    assert view.approval == approval and view.confirmation is None
    assert service.controller_runtime.journal.path.read_bytes() == before


@pytest.mark.parametrize("change", ["source", "close", "draft", "expiry", "capability", "stop", "runtime"])
def test_activation_confirmation_dismissed_on_invalidation(panel, app, monkeypatch, change):
    view, wrapper, service = panel
    approvals.approve(panel, app)
    runtime = service.controller_runtime
    dialog = confirm(view)
    if change == "source":
        wrapper._generation += 1
    elif change == "close":
        view.close()
    elif change == "draft":
        view.threshold.setText(".8")
    elif change == "expiry":
        view.deadline = time.monotonic() - 1
    elif change == "stop":
        service.invalidate_policy_preparation()
    elif change == "runtime":
        service._controller_runtime = None
    else:
        caps = wrapper.active.capabilities
        monkeypatch.setattr(
            type(wrapper.active),
            "capabilities",
            property(
                lambda self: replace(
                    caps, operations=tuple(op for op in caps.operations if op != "activate_simulation_policy")
                )
            ),
        )
    view._tick()
    assert view.confirmation is None and not dialog.isVisible()
    dialog.accept()
    assert not view.job.active and view.activation is None
    assert runtime.policy.engine.threshold == 0.65


@pytest.mark.parametrize("change", ["policy", "topology", "expired"])
def test_activation_revalidates_after_confirmation(panel, app, monkeypatch, change):
    from quantum_autopilot.control_plane.inventory import AddSimulatedNode

    view, _, service = panel
    approvals.approve(panel, app)
    dialog = confirm(view)
    if change == "policy":
        service.controller_runtime.policy = RepeaterSandboxPolicy(0.8)
    elif change == "topology":
        service.topology_store.add_inventory_node(AddSimulatedNode("extra", "endpoint", 0, "127.0.0.1", 7411))
    else:
        view.binding.session._started -= preparation.MAX_PROPOSAL_AGE_S
    click(dialog.activate_button)
    wait(app, lambda: not view.job.active)
    assert view.activation is None and "REFUSED" in view.status.text()
    assert service.controller_runtime.policy.engine.threshold == (0.8 if change == "policy" else 0.65)
    assert service.policy_activation_status()["record"]["status"] == "NO_LOCAL_ACTIVATION"


@pytest.mark.parametrize("change", ["close", "source", "stop"])
def test_cancel_during_activation_read_prevents_commit_without_blocking_ui(panel, app, monkeypatch, change):
    view, wrapper, service = panel
    approvals.approve(panel, app)
    dialog = confirm(view)
    entered, release = Event(), Event()
    original = preparation._read_topology

    def slow(*args):
        entered.set()
        assert release.wait(5)
        return original(*args)

    monkeypatch.setattr(preparation, "_read_topology", slow)
    click(dialog.activate_button)
    wait(app, entered.is_set)
    try:
        started = time.monotonic()
        if change == "close":
            view.close()
        elif change == "source":
            wrapper._generation += 1
        else:
            service.invalidate_policy_preparation()
        view._tick()
        assert time.monotonic() - started < 0.5
    finally:
        release.set()
    wait(app, lambda: not view.job.active)
    assert service.controller_runtime.policy.engine.threshold == 0.65
    assert service.policy_activation_status()["record"]["status"] == "NO_LOCAL_ACTIVATION"
    assert view.activation is None


def test_close_after_commit_started_discards_result_but_readback_exposes_committed_policy(
    panel, app, monkeypatch
):
    view, _, service = panel
    approvals.approve(panel, app)
    dialog = confirm(view)
    entered, release = Event(), Event()
    original = backend._commit

    def slow(con):
        entered.set()
        assert release.wait(5)
        original(con)

    monkeypatch.setattr(backend, "_commit", slow)
    click(dialog.activate_button)
    wait(app, entered.is_set)
    try:
        view.close()
        view.show()
        assert view.activation is None and not view.read_active_button.isEnabled()
    finally:
        release.set()
    wait(app, lambda: not view.job.active)
    assert view.activation is None
    assert service.controller_runtime.policy.engine.threshold == 0.7
    assert read_active(view, app)["record"]["status"] == "ACTIVE"


@pytest.mark.parametrize("committed", [False, True])
def test_uncertain_commit_is_not_reported_as_active(panel, app, monkeypatch, committed):
    view, _, service = panel
    approvals.approve(panel, app)
    dialog = confirm(view)

    def uncertain(con):
        if committed:
            con.commit()
        raise RuntimeError("injected commit uncertainty")

    monkeypatch.setattr(backend, "_commit", uncertain)
    click(dialog.activate_button)
    wait(app, lambda: not view.job.active)
    assert view.activation is None and service.controller_runtime.policy.engine.threshold == 0.65
    state = read_active(view, app)
    assert state["record"]["status"] == "UNRESOLVED"
    assert "Cycles blocked" in view.active_details.toPlainText()
    assert not view.activate_button.isEnabled()


@pytest.mark.parametrize("stage", ["baseline", "mismatch", "restart"])
def test_readback_observes_actual_state_without_baseline_preparation(panel, app, stage):
    from quantum_autopilot.qkd.dashboard_api import AssuranceDashboardService

    view, wrapper, service = panel
    if stage != "baseline":
        activate(panel, app)
    if stage == "mismatch":
        service.controller_runtime.policy = RepeaterSandboxPolicy(0.8)
    elif stage == "restart":
        other = AssuranceDashboardService(control_journal_path=service.controller_runtime.journal.path)
        other.controller_runtime
        wrapper.active.service = other
        view._tick()
    state = read_active(view, app)
    assert state["record"]["status"] == ("UNVERIFIED" if stage == "mismatch" else "NO_LOCAL_ACTIVATION")
    assert view.context is None


@pytest.mark.parametrize("change", ["source", "close", "cancel"])
def test_state_export_revalidates_dialog_selection(panel, app, monkeypatch, tmp_path, change):
    view, wrapper, _ = panel
    read_active(view, app)
    target = tmp_path / "state.json"

    def choose(*args):
        if change == "source":
            wrapper._generation += 1
        elif change == "close":
            view.close()
        return ("" if change == "cancel" else str(target)), "JSON"

    monkeypatch.setattr(ui.QFileDialog, "getSaveFileName", choose)
    click(view.export_state_button)
    assert not target.exists() and not view.export_job.active


def test_state_export_captured_background_exclusive(panel, app, monkeypatch, tmp_path):
    view, _, service = panel
    activate(panel, app)
    captured = read_active(view, app)
    target = tmp_path / "state.json"
    threads = []
    original = ui._export_captured

    def write(*args):
        threads.append(get_ident())
        return original(*args)

    def choose(*args):
        service.controller_runtime.policy = RepeaterSandboxPolicy(0.8)
        return str(target), "JSON"

    monkeypatch.setattr(ui, "_export_captured", write)
    monkeypatch.setattr(ui.QFileDialog, "getSaveFileName", choose)
    click(view.export_state_button)
    wait(app, lambda: not view.export_job.active)
    assert json.loads(target.read_text()) == captured and threads[0] != get_ident()
    before = target.read_bytes()
    click(view.export_state_button)
    wait(app, lambda: not view.export_job.active)
    assert target.read_bytes() == before and "EXPORT REFUSED" in view.status.text()


def test_activation_capability_requires_existing_simulation(provider, app):
    from quantum_autopilot.console.capabilities import SourceCapabilities, synthetic_capabilities

    for op in ("activate_simulation_policy", "read_active_policy"):
        assert not provider.capabilities.allows(op) and not synthetic_capabilities().allows(op)
    provider.service.controller_runtime
    assert provider.capabilities.allows("activate_simulation_policy")
    with pytest.raises(ValueError):
        SourceCapabilities(mode="read_only", operations=("activate_simulation_policy",))


def test_delayed_readback_discarded_after_close(panel, app, monkeypatch):
    from quantum_autopilot.console import policy_review_binding as binding

    view = panel[0]
    entered, release = Event(), Event()
    original = binding.activation_status

    def slow(runtime):
        entered.set()
        assert release.wait(5)
        return original(runtime)

    monkeypatch.setattr(binding, "activation_status", slow)
    view.tabs.setCurrentIndex(2)
    click(view.read_active_button)
    wait(app, entered.is_set)
    try:
        view.close()
    finally:
        release.set()
    wait(app, lambda: not view.job.active)
    assert view.active_observation is None


@pytest.mark.parametrize("change", ["close", "stop"])
def test_invalidation_while_waiting_for_cycle_lock_prevents_activation(panel, app, monkeypatch, change):
    from quantum_autopilot.console import policy_review_binding as binding

    view, _, service = panel
    approvals.approve(panel, app)
    dialog = confirm(view)
    runtime = service.controller_runtime
    entered = Event()
    original = binding.activate

    def entered_activate(*args, **kwargs):
        entered.set()
        return original(*args, **kwargs)

    monkeypatch.setattr(binding, "activate", entered_activate)
    runtime._cycle_lock.acquire()
    try:
        click(dialog.activate_button)
        wait(app, entered.is_set)
        if change == "close":
            view.close()
        else:
            service.invalidate_policy_preparation()
            view._tick()
    finally:
        runtime._cycle_lock.release()
    wait(app, lambda: not view.job.active)
    assert runtime.policy.engine.threshold == 0.65
    assert service.policy_activation_status()["record"]["status"] == "NO_LOCAL_ACTIVATION"


def test_readback_keeps_approval_and_explicit_refresh_exposes_mismatch(panel, app):
    view, _, service = panel
    approval = approvals.approve(panel, app)
    assert read_active(view, app)["record"]["status"] == "NO_LOCAL_ACTIVATION"
    assert view.approval == approval and view.activate_button.isEnabled()
    click(confirm(view).activate_button)
    wait(app, lambda: not view.job.active)
    first = read_active(view, app)
    assert first["record"]["status"] == "ACTIVE"
    service.controller_runtime.policy = RepeaterSandboxPolicy(0.8)
    view._tick()
    assert view.active_observation == first
    assert first["record"]["observed_at_utc"] in view.active_details.toPlainText()
    second = read_active(view, app)
    assert second["record"]["status"] == "UNVERIFIED" and second["record_sha256"] != first["record_sha256"]


def test_repeated_activation_cancel_disposes_dialog_and_preserves_approval(panel, app):
    from PySide6.QtCore import QCoreApplication, QEvent
    import shiboken6

    view = panel[0]
    approval = approvals.approve(panel, app)
    for _ in range(5):
        dialog = confirm(view)
        click(dialog.cancel_button)
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        app.processEvents()
        assert not shiboken6.isValid(dialog)
        assert view.confirmation is None and view.approval == approval and view.activation is None
