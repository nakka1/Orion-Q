"""Native rollback and explicit recovery keep simulation evidence and source boundaries."""

from dataclasses import replace
import json
import os
from pathlib import Path
from threading import Event, get_ident
import time

import pytest

from tests import test_console_policy_activation as active
from tests import test_console_policy_approval as approvals
from tests import test_console_policy_review as shared
from tests import test_policy_recovery as recovery_tests
from tests import test_policy_preparation as prep_tests
from tests import test_policy_approval as approval_tests
from quantum_autopilot.console.policy_review_binding import PolicyReviewBinding
from quantum_autopilot.console.widgets import policy_review as ui
from quantum_autopilot.control_plane import policy_activation as activation
from quantum_autopilot.control_plane import policy_recovery as recovery
from quantum_autopilot.control_plane.policy import RepeaterSandboxPolicy

app, provider, panel = shared.app, shared.provider, shared.panel
click, wait = shared.click, shared.wait


def inspect_fault(panel, app, monkeypatch, *, committed=False):
    view, _, service = panel
    recovery_tests.fail_commit(service, monkeypatch, committed=committed)
    assert active.read_active(view, app)["record"]["status"] == "UNRESOLVED"
    click(view.inspect_recovery_button)
    wait(app, lambda: not view.job.active)
    assert view.recovery_review, view.status.text()
    return view.recovery_review


def confirm(view, label="native recovery test fixture"):
    click(view.recover_button)
    dialog = view.confirmation
    assert dialog is not None and not dialog.recover_button.isEnabled()
    dialog.operator_label.setText(label)
    dialog.confirmed.setChecked(True)
    return dialog


def finish_recovery(view, app):
    click(confirm(view).recover_button)
    wait(app, lambda: not view.job.active)
    assert view.activation, view.status.text()
    return view.activation


def finish_rollback(view, app):
    click(view.review_button)
    wait(app, lambda: not view.job.active)
    dialog = approvals.confirm(view)
    assert "ROLLBACK" in dialog.details.toPlainText()
    click(dialog.record_button)
    wait(app, lambda: not view.job.active)
    dialog = active.confirm(view)
    assert "ROLLBACK" in dialog.details.toPlainText()
    click(dialog.activate_button)
    wait(app, lambda: not view.job.active)
    assert view.activation, view.status.text()
    return view.activation


def test_complete_native_rollback_requires_new_review_approval_activation(panel, app):
    view, _, service = panel
    first = active.activate(panel, app)
    active.read_active(view, app)
    assert view.rollback_button.isEnabled()
    click(view.rollback_button)
    wait(app, lambda: not view.job.active)
    assert view.proposal["record"]["rollback_of"]["activation_id"] == first["record"]["activation_id"]
    assert view.threshold.text() == "0.65" and view.threshold.isReadOnly()
    assert not view.prepare_button.isEnabled() and not view.activate_button.isEnabled()
    assert service.controller_runtime.policy.engine.threshold == 0.7
    assert "ROLLBACK PREPARED" in view.status.text()
    result = finish_rollback(view, app)
    assert result["record"]["transition_kind"] == "rollback"
    assert service.controller_runtime.policy.engine.threshold == 0.65
    assert active.read_active(view, app)["record"]["activation"] == result
    assert service.controller_runtime.journal.list_recent() == []
    if folder := os.environ.get("ORION_POLICY_RECOVERY_EVIDENCE"):
        out = Path(folder)
        out.mkdir(parents=True, exist_ok=True)
        (out / "rollback.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
        view.resize(640, 500)
        app.processEvents()
        assert view.grab().save(str(out / "rollback-640.png"))


@pytest.mark.parametrize("committed", [False, True])
def test_recovery_explicit_worker_keeps_object_invalidates_approvals_and_readback(
    panel, app, monkeypatch, committed
):
    view, _, service = panel
    review = inspect_fault(panel, app, monkeypatch, committed=committed)
    runtime = service.controller_runtime
    retained = runtime.policy
    generation = service._policy_preparation_generation
    dialog = confirm(view)
    assert review["record_sha256"] in dialog.details.toPlainText()
    assert "Retained threshold: 0.65" in dialog.details.toPlainText()
    assert not view.inspect_recovery_button.isEnabled()
    expected = "present_and_verified" if committed else "absent_at_observation"
    assert expected in dialog.details.toPlainText()
    threads = []
    original = recovery.activation._commit

    def commit(con):
        threads.append(get_ident())
        return original(con)

    monkeypatch.setattr(recovery.activation, "_commit", commit)
    folder = os.environ.get("ORION_POLICY_RECOVERY_EVIDENCE")
    if folder and committed:
        out = Path(folder)
        out.mkdir(parents=True, exist_ok=True)
        dialog.resize(600, 500)
        app.processEvents()
        assert dialog.grab().save(str(out / "recovery-confirmation-600.png"))
    click(dialog.recover_button)
    wait(app, lambda: not view.job.active)
    result = view.activation
    assert result and result["record"]["transition_kind"] == "recovery_checkpoint", view.status.text()
    assert result["record"]["reviewed_context"] == review
    assert runtime.policy is retained and retained.engine.threshold == 0.65
    assert (
        not result["record"]["physical_action_performed"]
        and not result["record"]["optional_models_activated"]
    )
    assert service._policy_preparation_generation == generation + 1
    assert view.recovery_review is None and view.deadline is None and view.approval is None
    assert service._policy_preparation is None and runtime._policy_recovery_selection is None
    assert threads and all(t != get_ident() for t in threads)
    state = active.read_active(view, app)
    assert state["record"]["status"] == "ACTIVE" and state["record"]["activation"] == result
    assert not view.rollback_button.isEnabled() and not view.recover_button.isEnabled()
    assert runtime.journal.list_recent() == []
    if folder and committed:
        (out / "recovery.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
        for w, h in ((640, 500), (1000, 700)):
            view.resize(w, h)
            app.processEvents()
            assert view.active_details.height() > 85
            assert (
                view.recover_button.mapTo(view, view.recover_button.rect().bottomRight()).x() < view.width()
            )
            assert view.grab().save(str(out / f"recovered-{w}.png"))


@pytest.mark.parametrize("label", ["", " label", "label ", "x\x00y"])
def test_invalid_label_or_unchecked_recovery_never_commits(panel, app, monkeypatch, label):
    view, _, service = panel
    inspect_fault(panel, app, monkeypatch)
    before = service.controller_runtime.journal.path.read_bytes()
    dialog = confirm(view, label)
    assert not dialog.recover_button.isEnabled()
    dialog.accept()
    assert dialog.isVisible() and not view.job.active
    dialog.operator_label.setText("valid")
    dialog.confirmed.setChecked(False)
    dialog.accept()
    assert dialog.isVisible() and not view.job.active
    click(dialog.cancel_button)
    assert view.recovery_review is not None and view.confirmation is None
    assert service.controller_runtime.journal.path.read_bytes() == before
    assert service.policy_activation_status()["record"]["status"] == "UNRESOLVED"


@pytest.mark.parametrize("change", ["source", "close", "draft", "expiry", "capability", "stop", "runtime"])
def test_recovery_confirmation_invalidates_with_source_selection(panel, app, monkeypatch, change):
    view, wrapper, service = panel
    inspect_fault(panel, app, monkeypatch)
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
                    caps, operations=tuple(op for op in caps.operations if op != "recover_simulation_policy")
                )
            ),
        )
        # Presentation follows the next authenticated committed frame; the
        # timer must not poll this fresh backend property on the Qt thread.
        wrapper.active.refresh()
    view._tick()
    assert view.confirmation is None and not dialog.isVisible()
    dialog.accept()
    assert not view.job.active and view.activation is None
    assert runtime._policy_recovery_selection is None and runtime._policy_activation_fault is not None


@pytest.mark.parametrize("change", ["topology", "policy", "new_review"])
def test_recovery_revalidates_at_commit(panel, app, monkeypatch, change):
    from quantum_autopilot.control_plane.inventory import AddSimulatedNode

    view, _, service = panel
    inspect_fault(panel, app, monkeypatch)
    dialog = confirm(view)
    if change == "topology":
        service.topology_store.add_inventory_node(AddSimulatedNode("extra", "endpoint", 0, "127.0.0.1", 7411))
    elif change == "policy":
        service.controller_runtime.policy = RepeaterSandboxPolicy(0.65)
    else:
        service.policy_recovery_context()
    click(dialog.recover_button)
    wait(app, lambda: not view.job.active)
    assert view.activation is None and "REFUSED" in view.status.text()
    assert service.policy_activation_status()["record"]["status"] == "UNRESOLVED"


@pytest.mark.parametrize("change", ["close", "source", "stop"])
def test_cancel_during_recovery_read_is_nonblocking_and_prevents_commit(panel, app, monkeypatch, change):
    view, wrapper, service = panel
    inspect_fault(panel, app, monkeypatch)
    dialog = confirm(view)
    entered, release = Event(), Event()
    original = recovery._read_topology

    def slow(*args):
        entered.set()
        assert release.wait(5)
        return original(*args)

    monkeypatch.setattr(recovery, "_read_topology", slow)
    click(dialog.recover_button)
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
    assert view.activation is None and service.policy_activation_status()["record"]["status"] == "UNRESOLVED"


def test_close_after_recovery_commit_started_drops_ui_result_but_keeps_checkpoint(panel, app, monkeypatch):
    view, _, service = panel
    inspect_fault(panel, app, monkeypatch)
    dialog = confirm(view)
    entered, release = Event(), Event()
    original = activation._commit

    def slow(con):
        entered.set()
        assert release.wait(5)
        original(con)

    monkeypatch.setattr(activation, "_commit", slow)
    click(dialog.recover_button)
    wait(app, entered.is_set)
    try:
        view.close()
        view.show()
    finally:
        release.set()
    wait(app, lambda: not view.job.active)
    assert view.activation is None
    assert active.read_active(view, app)["record"]["status"] == "ACTIVE"
    assert service.controller_runtime.policy.engine.threshold == 0.65


@pytest.mark.parametrize("committed", [False, True])
def test_recovery_commit_failure_stays_blocked_and_new_review_can_recover(panel, app, monkeypatch, committed):
    view, _, service = panel
    first = inspect_fault(panel, app, monkeypatch)
    dialog = confirm(view)
    with monkeypatch.context() as patch:

        def uncertain(con):
            if committed:
                con.commit()
            raise RuntimeError("injected recovery commit failure")

        patch.setattr(activation, "_commit", uncertain)
        click(dialog.recover_button)
        wait(app, lambda: not view.job.active)
    assert view.activation is None
    assert active.read_active(view, app)["record"]["status"] == "UNRESOLVED"
    click(view.inspect_recovery_button)
    wait(app, lambda: not view.job.active)
    assert view.recovery_review["record"]["context"]["fault_id"] != first["record"]["context"]["fault_id"]
    finish_recovery(view, app)
    assert active.read_active(view, app)["record"]["status"] == "ACTIVE"


def test_selection_is_owned_and_old_panel_cannot_clear_or_recover_new_selection(panel, app, monkeypatch):
    _, _, service = panel
    recovery_tests.fail_commit(service, monkeypatch)
    one, two = PolicyReviewBinding(service, source=panel[1].active), PolicyReviewBinding(service, source=panel[1].active)
    one.run("inspect_recovery")
    review = two.run("inspect_recovery")["receipt"]
    selected = service.controller_runtime._policy_recovery_selection
    with pytest.raises(ValueError, match="replaced"):
        one.run("recover", recovery_tests.recovery_request(review))
    one.cancel()
    assert service.controller_runtime._policy_recovery_selection is selected
    two.run("recover", recovery_tests.recovery_request(review))
    assert service.policy_activation_status()["record"]["status"] == "ACTIVE"


def test_recovery_invalidates_other_private_approval_binding(panel, app, monkeypatch):
    view, _, service = panel
    inspect_fault(panel, app, monkeypatch)
    other = PolicyReviewBinding(service, source=panel[1].active)
    context = other.run("inspect")["receipt"]
    proposal = other.run("prepare", prep_tests.payload(context))["receipt"]
    reviewed = other.run("review", *prep_tests.selection(proposal))["receipt"]
    approved = other.run("approve", approval_tests.request(reviewed))["receipt"]
    finish_recovery(view, app)
    with pytest.raises(ValueError, match="invalidated"):
        other.run("activate", recovery_tests.shared.request(approved))


def test_recovery_review_export_is_captured_exclusive_and_readback_discards_selection(
    panel, app, monkeypatch, tmp_path
):
    view, _, service = panel
    review = inspect_fault(panel, app, monkeypatch)
    target = tmp_path / "review.json"
    monkeypatch.setattr(ui.QFileDialog, "getSaveFileName", lambda *args: (str(target), "JSON"))
    click(view.export_state_button)
    wait(app, lambda: not view.export_job.active)
    assert json.loads(target.read_text()) == review
    before = target.read_bytes()
    click(view.export_state_button)
    wait(app, lambda: not view.export_job.active)
    assert target.read_bytes() == before and "EXPORT REFUSED" in view.status.text()
    active.read_active(view, app)
    assert view.recovery_review is None and not view.recover_button.isEnabled()
    assert service.controller_runtime._policy_recovery_selection is None


def test_new_capabilities_require_existing_runtime_and_are_unavailable_on_synthetic(provider, app):
    from quantum_autopilot.console.capabilities import SourceCapabilities, synthetic_capabilities

    for op in ("prepare_policy_rollback", "recover_simulation_policy"):
        assert not provider.capabilities.allows(op) and not synthetic_capabilities().allows(op)
        with pytest.raises(ValueError):
            SourceCapabilities(mode="read_only", operations=(op,))
    provider.service.controller_runtime
    assert provider.capabilities.allows("prepare_policy_rollback") and provider.capabilities.allows(
        "recover_simulation_policy"
    )


def test_own_recovery_generation_change_does_not_drop_success_receipt(panel, app, monkeypatch):
    view, _, service = panel
    inspect_fault(panel, app, monkeypatch)
    dialog = confirm(view)
    entered, release = Event(), Event()
    original = service._invalidate_policy_preparation

    def slow_invalidate():
        original()
        entered.set()
        assert release.wait(5)

    monkeypatch.setattr(service, "_invalidate_policy_preparation", slow_invalidate)
    click(dialog.recover_button)
    wait(app, entered.is_set)
    try:
        view._tick()
        assert view.recovery_review is not None and not view.binding.cancelled.is_set()
    finally:
        release.set()
    wait(app, lambda: not view.job.active)
    assert view.activation and view.activation["record"]["transition_kind"] == "recovery_checkpoint"


def test_late_inspection_after_close_removes_its_selection(panel, app, monkeypatch):
    view, _, service = panel
    recovery_tests.fail_commit(service, monkeypatch)
    active.read_active(view, app)
    entered, release = Event(), Event()
    original = recovery._read_topology

    def slow(*args):
        entered.set()
        assert release.wait(5)
        return original(*args)

    monkeypatch.setattr(recovery, "_read_topology", slow)
    click(view.inspect_recovery_button)
    wait(app, entered.is_set)
    try:
        view.close()
    finally:
        release.set()
    wait(app, lambda: not view.job.active)
    assert view.recovery_review is None and service.controller_runtime._policy_recovery_selection is None


def test_rollback_refuses_changed_policy_after_readback(panel, app):
    view, _, service = panel
    active.activate(panel, app)
    active.read_active(view, app)
    service.controller_runtime.policy = RepeaterSandboxPolicy(0.8)
    click(view.rollback_button)
    wait(app, lambda: not view.job.active)
    assert view.proposal is None and "REFUSED" in view.status.text()
    assert service.controller_runtime.policy.engine.threshold == 0.8


def test_native_recovery_preserves_earlier_target_for_subsequent_native_rollback(panel, app, monkeypatch):
    view, _, service = panel
    first = active.activate(panel, app)
    context = service.policy_preparation_context()
    proposal = service.prepare_policy_change(prep_tests.payload(context, threshold=0.8))
    reviewed = service.review_policy_change(*prep_tests.selection(proposal))
    approved = service.approve_policy_change(approval_tests.request(reviewed))
    with monkeypatch.context() as patch:

        def uncertain(con):
            con.commit()
            raise RuntimeError("second activation uncertain")

        patch.setattr(activation, "_commit", uncertain)
        with pytest.raises(RuntimeError):
            service.activate_policy_change(recovery_tests.shared.request(approved))
    active.read_active(view, app)
    click(view.inspect_recovery_button)
    wait(app, lambda: not view.job.active)
    recovered = finish_recovery(view, app)
    assert recovered["record"]["rollback_policy"] == first["record"]["previous_policy"]
    assert service.controller_runtime.policy.engine.threshold == 0.7
    active.read_active(view, app)
    click(view.rollback_button)
    wait(app, lambda: not view.job.active)
    finish_rollback(view, app)
    assert service.controller_runtime.policy.engine.threshold == 0.65


def test_closing_old_panel_during_new_inspection_is_nonblocking_and_preserves_new_owner(
    panel, app, monkeypatch
):
    from concurrent.futures import ThreadPoolExecutor

    _, _, service = panel
    recovery_tests.fail_commit(service, monkeypatch)
    old, new = PolicyReviewBinding(service, source=panel[1].active), PolicyReviewBinding(service, source=panel[1].active)
    old.run("inspect_recovery")
    entered, release = Event(), Event()
    original = recovery._read_topology

    def slow(*args):
        entered.set()
        assert release.wait(5)
        return original(*args)

    monkeypatch.setattr(recovery, "_read_topology", slow)
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(new.run, "inspect_recovery")
        assert entered.wait(2)
        try:
            started = time.monotonic()
            old.cancel()
            assert time.monotonic() - started < 0.5
        finally:
            release.set()
        review = pending.result(timeout=5)["receipt"]
    assert service.controller_runtime._policy_recovery_selection is new._recovery_selection
    result = new.run("recover", recovery_tests.recovery_request(review))
    assert result["receipt"]["record"]["transition_kind"] == "recovery_checkpoint"
