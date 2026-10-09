"""Rollback is approved explicitly; recovery retains the restored policy and audits uncertainty."""

import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest

from quantum_autopilot.control_plane import policy_activation as activation
from quantum_autopilot.control_plane import policy_recovery as recovery
from quantum_autopilot.control_plane import policy_preparation as preparation
from quantum_autopilot.control_plane.policy import RepeaterSandboxPolicy
from quantum_autopilot.control_plane.inventory import AddSimulatedNode
from quantum_autopilot.qkd.dashboard_api import AssuranceDashboardService
from tests import test_policy_activation as shared
from tests import test_policy_approval as approvals
from tests import test_policy_preparation as proposals
from tests import test_control_plane_sandbox as sandbox

local = shared.local


def fail_commit(service, monkeypatch, *, committed=False):
    approved = shared.approved(service)
    original = activation._commit

    def uncertain(connection):
        if committed:
            connection.commit()
        raise RuntimeError("injected uncertain commit")

    with monkeypatch.context() as patch:
        patch.setattr(activation, "_commit", uncertain)
        with pytest.raises(RuntimeError):
            service.activate_policy_change(shared.request(approved))
    assert activation._commit is original
    return service._controller_runtime._policy_activation_recovery


def recovery_request(context, **changes):
    return dict(
        dict(
            mode="simulation",
            fault_id=context["record"]["context"]["fault_id"],
            expected_recovery_sha256=context["record_sha256"],
            operator_label="recovery test fixture",
            confirmation=recovery.RECOVERY_CONFIRMATION,
        ),
        **changes,
    )


def activate_rollback(service, original):
    proposal = service.prepare_policy_rollback(original["record"]["activation_id"], original["record_sha256"])
    review = service.review_policy_change(*proposals.selection(proposal))
    approval = service.approve_policy_change(approvals.request(review))
    return service.activate_policy_change(shared.request(approval))


def test_rollback_prepares_exact_previous_configuration_requires_approval_and_preserves_history(local):
    service, runtime = local
    first = service.activate_policy_change(shared.request(shared.approved(service)))
    before = shared.rows(runtime, "policy_activations")
    proposal = service.prepare_policy_rollback(first["record"]["activation_id"], first["record_sha256"])
    assert runtime.policy.engine.threshold == 0.7 and shared.rows(runtime, "policy_activations") == before
    assert proposal["record"]["proposed_policy"] == first["record"]["previous_policy"]
    assert proposal["record"]["rollback_of"]["activation_id"] == first["record"]["activation_id"]
    with pytest.raises(ValueError):
        service.activate_policy_change(shared.request(first["record"]["approval"]))
    reviewed = service.review_policy_change(*proposals.selection(proposal))
    approved = service.approve_policy_change(approvals.request(reviewed))
    result = service.activate_policy_change(shared.request(approved))
    assert result["record"]["transition_kind"] == "rollback"
    assert result["record"]["rollback_of"] == proposal["record"]["rollback_of"]
    assert runtime.policy.engine.threshold == 0.65
    assert shared.rows(runtime, "policy_activations")[:1] == before
    assert service.policy_activation_status()["record"]["activation"] == result
    assert shared.rows(runtime, "control_cycles") == []


@pytest.mark.parametrize(
    "change", ["hash", "older", "replacement", "expired", "changed_policy", "no_activation"]
)
def test_rollback_wrong_or_stale_selection_is_refused(local, monkeypatch, change):
    service, runtime = local
    if change == "no_activation":
        with pytest.raises(ValueError):
            service.prepare_policy_rollback("x", "0" * 64)
        return
    clock = [1000.0]
    monkeypatch.setattr(preparation.time, "monotonic", lambda: clock[0])
    first = service.activate_policy_change(shared.request(shared.approved(service)))
    if change == "hash":
        with pytest.raises(ValueError):
            service.prepare_policy_rollback(first["record"]["activation_id"], "0" * 64)
        return
    if change == "replacement":
        runtime.policy = RepeaterSandboxPolicy(0.7)
        with pytest.raises(ValueError):
            service.prepare_policy_rollback(first["record"]["activation_id"], first["record_sha256"])
        return
    proposal = service.prepare_policy_rollback(first["record"]["activation_id"], first["record_sha256"])
    reviewed = service.review_policy_change(*proposals.selection(proposal))
    approved = service.approve_policy_change(approvals.request(reviewed))
    if change == "expired":
        clock[0] += preparation.MAX_PROPOSAL_AGE_S
    elif change == "changed_policy":
        runtime.policy = RepeaterSandboxPolicy(0.8)
    else:
        activate_rollback(service, first)
    before = runtime.policy
    with pytest.raises(ValueError):
        service.activate_policy_change(shared.request(approved))
    assert runtime.policy is before


@pytest.mark.parametrize("committed", [False, True])
def test_recovery_persists_observation_retains_policy_and_unblocks_only_after_explicit_checkpoint(
    local, monkeypatch, tmp_path, committed
):
    service, runtime = local
    baseline = runtime.policy
    fault = fail_commit(service, monkeypatch, committed=committed)
    before = runtime.journal.path.read_bytes()
    context = service.policy_recovery_context()
    assert runtime.journal.path.read_bytes() == before
    expected = "present_and_verified" if committed else "absent_at_observation"
    assert context["record"]["context"]["failed_record_observation"] == expected
    assert service.policy_activation_status()["record"]["status"] == "UNRESOLVED"
    with pytest.raises(ValueError):
        runtime.cycle(sandbox.snapshot())
    result = service.recover_policy_change(recovery_request(context))
    assert runtime.policy is baseline and baseline.engine.threshold == 0.65
    assert result["record"]["transition_kind"] == "recovery_checkpoint"
    assert result["record"]["failed_attempt"] == fault["failed_receipt"]
    assert result["record"]["validated_context"]["failed_record_observation"] == expected
    assert not result["record"]["policy_replaced"] and result["record"]["commands_emitted"] == 0
    assert service.policy_activation_status()["record"]["status"] == "ACTIVE"
    assert runtime._policy_activation_fault is None
    assert service._policy_preparation is None
    assert len(shared.rows(runtime, "policy_activations")) == (2 if committed else 1)
    assert shared.rows(runtime, "control_cycles") == []
    cycle = runtime.cycle(sandbox.snapshot())
    assert (
        cycle["record"]["intent"]["policy_details"]["policy_activation"]["activation_id"]
        == result["record"]["activation_id"]
    )
    target = tmp_path / "recovered.json"
    assert (
        service.export_policy_activation(result["record"]["activation_id"], result["record_sha256"], target)
        == result
    )
    assert json.loads(target.read_text(encoding="utf-8")) == result
    with pytest.raises(ValueError):
        service.recover_policy_change(recovery_request(context))
    with pytest.raises(ValueError, match="previous"):
        service.prepare_policy_rollback(result["record"]["activation_id"], result["record_sha256"])


@pytest.mark.parametrize(
    "changes",
    [
        {"mode": "physical"},
        {"confirmation": True},
        {"confirmation": "RETRY"},
        {"operator_label": ""},
        {"operator_label": " bad"},
        {"operator_label": "x\x00y"},
        {"operator_label": "x" * 81},
        {"expected_recovery_sha256": "bad"},
        {"expected_recovery_sha256": "0" * 64},
        {"fault_id": "bad"},
        {"fault_id": "00000000-0000-0000-0000-000000000000"},
        {"threshold": 0.7},
        {"optional_model_id": "ridge"},
    ],
)
def test_invalid_recovery_does_not_clear_fault_or_write(local, monkeypatch, changes):
    service, runtime = local
    fail_commit(service, monkeypatch)
    context = service.policy_recovery_context()
    before = runtime.journal.path.read_bytes()
    with pytest.raises(ValueError):
        service.recover_policy_change(recovery_request(context, **changes))
    assert runtime.journal.path.read_bytes() == before and runtime._policy_activation_fault is not None
    assert runtime.policy.engine.threshold == 0.65
    service.recover_policy_change(recovery_request(context))


@pytest.mark.parametrize(
    "change",
    ["new_review", "source_stop", "expiry", "topology", "policy", "equivalent_policy", "journal", "evidence"],
)
def test_recovery_review_is_bound_and_revalidated(local, monkeypatch, tmp_path, change):
    service, runtime = local
    clock = [1000.0]
    monkeypatch.setattr(preparation.time, "monotonic", lambda: clock[0])
    fail_commit(service, monkeypatch, committed=True)
    context = service.policy_recovery_context()
    if change == "new_review":
        service.policy_recovery_context()
    elif change == "source_stop":
        service.invalidate_policy_preparation()
    elif change == "expiry":
        clock[0] += preparation.MAX_PROPOSAL_AGE_S
    elif change == "topology":
        service.topology_store.add_inventory_node(AddSimulatedNode("new", "endpoint", 0, "127.0.0.1", 7411))
    elif change == "policy":
        runtime.policy.engine.threshold = 0.8
    elif change == "equivalent_policy":
        runtime.policy = RepeaterSandboxPolicy(0.65)
    elif change == "journal":
        runtime.journal.path = tmp_path / "missing.sqlite"
    else:
        with sqlite3.connect(runtime.journal.path) as con:
            con.execute("UPDATE policy_activations SET record_json='{}'")
    with pytest.raises((ValueError, sqlite3.Error, FileNotFoundError)):
        service.recover_policy_change(recovery_request(context))
    assert runtime._policy_activation_fault is not None
    with pytest.raises(ValueError):
        runtime.cycle(sandbox.snapshot())


@pytest.mark.parametrize("stage", ["insert", "commit_before", "commit_after", "expiry"])
def test_recovery_failure_keeps_cycles_blocked_and_new_explicit_review_can_recover(local, monkeypatch, stage):
    service, runtime = local
    clock = [1000.0]
    monkeypatch.setattr(preparation.time, "monotonic", lambda: clock[0])
    first_fault = fail_commit(service, monkeypatch)
    context = service.policy_recovery_context()

    def fail(*args):
        raise RuntimeError("injected recovery failure")

    with monkeypatch.context() as patch:
        if stage == "insert":
            patch.setattr(activation, "_schema", fail)
        else:

            def uncertain(con):
                if stage != "commit_before":
                    con.commit()
                if stage == "expiry":
                    clock[0] += preparation.MAX_PROPOSAL_AGE_S
                else:
                    fail()

            patch.setattr(activation, "_commit", uncertain)
        with pytest.raises((RuntimeError, ValueError)):
            service.recover_policy_change(recovery_request(context))
    assert runtime.policy.engine.threshold == 0.65
    assert service.policy_activation_status()["record"]["status"] == "UNRESOLVED"
    with pytest.raises(ValueError):
        runtime.cycle(sandbox.snapshot())
    if stage != "insert":
        assert runtime._policy_activation_recovery["fault_id"] != first_fault["fault_id"]
    with pytest.raises(ValueError):
        service.recover_policy_change(recovery_request(context))
    new = service.policy_recovery_context()
    final = service.recover_policy_change(recovery_request(new))
    assert final["record"]["active_policy"]["threshold"] == 0.65
    assert service.policy_activation_status()["record"]["status"] == "ACTIVE"


def test_recovery_of_failed_second_activation_keeps_earlier_rollback_target(local, monkeypatch):
    service, runtime = local
    first = service.activate_policy_change(shared.request(shared.approved(service)))
    context = service.policy_preparation_context()
    proposal = service.prepare_policy_change(proposals.payload(context, threshold=0.8))
    review = service.review_policy_change(*proposals.selection(proposal))
    approved = service.approve_policy_change(approvals.request(review))
    with monkeypatch.context() as patch:

        def uncertain(con):
            con.commit()
            raise RuntimeError("late commit failure")

        patch.setattr(activation, "_commit", uncertain)
        with pytest.raises(RuntimeError):
            service.activate_policy_change(shared.request(approved))
    retained = runtime.policy
    context = service.policy_recovery_context()
    recovered = service.recover_policy_change(recovery_request(context))
    assert runtime.policy is retained and runtime.policy.engine.threshold == 0.7
    assert recovered["record"]["rollback_policy"] == first["record"]["previous_policy"]
    rolled = activate_rollback(service, recovered)
    assert rolled["record"]["transition_kind"] == "rollback" and runtime.policy.engine.threshold == 0.65


def test_recovery_serializes_with_cycle_and_cannot_be_applied_twice(local, monkeypatch):
    service, runtime = local
    fail_commit(service, monkeypatch)
    context = service.policy_recovery_context()
    entered, release = Event(), Event()
    commit = activation._commit

    def slow(con):
        entered.set()
        assert release.wait(5)
        commit(con)

    monkeypatch.setattr(activation, "_commit", slow)
    with ThreadPoolExecutor(max_workers=3) as pool:
        first = pool.submit(service.recover_policy_change, recovery_request(context))
        assert entered.wait(2)
        cycle = pool.submit(runtime.cycle, sandbox.snapshot())
        second = pool.submit(service.recover_policy_change, recovery_request(context))
        assert not cycle.done() and not second.done()
        release.set()
        result = first.result(timeout=5)
        assert (
            cycle.result(timeout=5)["record"]["intent"]["policy_details"]["policy_activation"][
                "activation_id"
            ]
            == result["record"]["activation_id"]
        )
        with pytest.raises(ValueError):
            second.result(timeout=5)


def test_restart_does_not_import_recovery_selection_or_checkpoint(local, monkeypatch):
    service, runtime = local
    fail_commit(service, monkeypatch, committed=True)
    context = service.policy_recovery_context()
    other = AssuranceDashboardService(control_journal_path=runtime.journal.path)
    other.controller_runtime
    with pytest.raises(ValueError):
        other.recover_policy_change(recovery_request(context))
    assert other.policy_activation_status()["record"]["status"] == "NO_LOCAL_ACTIVATION"
    service.recover_policy_change(recovery_request(context))
    assert other.policy_activation_status()["record"]["status"] == "NO_LOCAL_ACTIVATION"


def test_capture_detects_change_during_recovery_read(local, monkeypatch):
    service, runtime = local
    fail_commit(service, monkeypatch)
    original = recovery._read_topology

    def changed(*args):
        result = original(*args)
        runtime.policy = RepeaterSandboxPolicy(0.65)
        return result

    monkeypatch.setattr(recovery, "_read_topology", changed)
    with pytest.raises(ValueError, match="changed"):
        service.policy_recovery_context()
    assert runtime._policy_recovery_selection is None


@pytest.mark.parametrize(
    "payload", [None, [], {}, {"mode": "simulation"}], ids=["null", "list", "empty", "missing"]
)
def test_recovery_requires_existing_runtime_without_initializing_storage(tmp_path, payload):
    path = tmp_path / "never-created.sqlite"
    service = AssuranceDashboardService(control_journal_path=path)
    with pytest.raises(ValueError):
        service.policy_recovery_context()
    with pytest.raises(ValueError):
        service.recover_policy_change(payload)
    assert service._controller_runtime is None and not path.exists()


def test_recovery_without_fault_is_refused(local):
    service, runtime = local
    before = runtime.journal.path.read_bytes()
    with pytest.raises(ValueError):
        service.policy_recovery_context()
    assert runtime._policy_recovery_selection is None and runtime.journal.path.read_bytes() == before


def test_corrupted_recovery_checkpoint_blocks_cycle(local, monkeypatch):
    service, runtime = local
    fail_commit(service, monkeypatch)
    service.recover_policy_change(recovery_request(service.policy_recovery_context()))
    with sqlite3.connect(runtime.journal.path) as con:
        con.execute("UPDATE policy_activations SET record_json='{}'")
    assert service.policy_activation_status()["record"]["status"] == "UNVERIFIED"
    with pytest.raises(ValueError):
        runtime.cycle(sandbox.snapshot())
    assert shared.rows(runtime, "control_cycles") == []


def test_slow_recovery_observation_expires_without_selecting_context(local, monkeypatch):
    service, runtime = local
    clock = [1000.0]
    monkeypatch.setattr(preparation.time, "monotonic", lambda: clock[0])
    fail_commit(service, monkeypatch)
    original = recovery._read_topology

    def slow(*args):
        result = original(*args)
        clock[0] += preparation.MAX_PROPOSAL_AGE_S
        return result

    monkeypatch.setattr(recovery, "_read_topology", slow)
    with pytest.raises(ValueError, match="expired"):
        service.policy_recovery_context()
    assert runtime._policy_recovery_selection is None


def test_uncertain_rollback_can_recover_retained_policy_then_explicitly_rollback(local, monkeypatch):
    service, runtime = local
    first = service.activate_policy_change(shared.request(shared.approved(service)))
    with monkeypatch.context() as patch:

        def uncertain(con):
            con.commit()
            raise RuntimeError("rollback commit response lost")

        patch.setattr(activation, "_commit", uncertain)
        with pytest.raises(RuntimeError):
            activate_rollback(service, first)
    retained = runtime.policy
    assert retained.engine.threshold == 0.7
    restored = service.recover_policy_change(recovery_request(service.policy_recovery_context()))
    assert runtime.policy is retained
    assert restored["record"]["failed_attempt"]["record"]["transition_kind"] == "rollback"
    activate_rollback(service, restored)
    assert runtime.policy.engine.threshold == 0.65
