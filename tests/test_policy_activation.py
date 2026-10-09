"""Transactional simulation activation: evidence, readback, failures and process boundaries."""

from concurrent.futures import ThreadPoolExecutor
import json
import sqlite3
from threading import Event

import pytest

from quantum_autopilot.control_plane import policy_activation as activation
from quantum_autopilot.control_plane import policy_preparation as preparation
from quantum_autopilot.control_plane.policy import RepeaterSandboxPolicy
from quantum_autopilot.control_plane.runtime import ControllerRuntime
from quantum_autopilot.control_plane.topology import _sha256
from quantum_autopilot.control_plane.inventory import AddSimulatedNode
from quantum_autopilot.qkd.dashboard_api import AssuranceDashboardService
from tests import test_policy_approval as approval_tests
from tests import test_control_plane_sandbox as sandbox
from tests import test_policy_preparation as preparation_tests

local = approval_tests.local


def approved(service):
    _, review = approval_tests.reviewed(service)
    return service.approve_policy_change(approval_tests.request(review))


def request(receipt, **changes):
    return dict(
        dict(
            mode="simulation",
            approval_id=receipt["record"]["approval_id"],
            expected_approval_sha256=receipt["record_sha256"],
            confirmation=activation.ACTIVATION_CONFIRMATION,
        ),
        **changes,
    )


def rows(runtime, table):
    with sqlite3.connect(runtime.journal.path) as con:
        return con.execute("SELECT * FROM " + table).fetchall()


def test_activation_preserves_baseline_and_costs_changes_only_threshold_and_audits(
    local, monkeypatch, tmp_path
):
    service, runtime = local
    runtime.policy.engine.energy_cfg.P_MEMORY_HOLD_W *= 2
    runtime.policy.engine._risk_controller.inference_latency_s = 0.123
    runtime.policy.engine._risk_controller.wait_time_s = 0.456
    baseline = runtime.policy
    old, _, _ = preparation._describe(runtime)
    approval = approved(service)
    before = {
        name: rows(runtime, name) for name in ("control_cycles", "safety_events", "simulated_topologies")
    }

    def forbidden(*args, **kwargs):
        pytest.fail("activation does not execute, decide or load optional weights")

    monkeypatch.setattr(runtime, "cycle", forbidden)
    monkeypatch.setattr(RepeaterSandboxPolicy, "decide", forbidden)
    for executor in (runtime.simulated_executor, runtime.dry_run_executor, runtime.physical_executor):
        monkeypatch.setattr(executor, "execute", forbidden)
    from quantum_autopilot.model_intake import catalog

    monkeypatch.setattr(catalog, "open_optional_model", forbidden)
    result = service.activate_policy_change(request(approval))
    record = result["record"]
    assert record["approval"] == approval and record["previous_policy"] == old
    assert record["active_policy"] == dict(old, threshold=0.7)
    assert runtime.policy is not baseline and baseline.engine.threshold == 0.65
    assert runtime.policy.engine.threshold == runtime.policy.engine._risk_controller.threshold == 0.7
    assert runtime.policy.engine.energy_cfg is not baseline.engine.energy_cfg
    assert record["commands_emitted"] == 0 and not record["physical_action_performed"]
    assert not record["optional_models_activated"]
    assert result["record_sha256"] == _sha256(record)
    assert len(rows(runtime, "policy_activations")) == 1
    assert all(rows(runtime, name) == data for name, data in before.items())
    readback = service.policy_activation_status()["record"]
    assert readback["status"] == "ACTIVE" and readback["activation"] == result
    assert readback["current_activity_observed"]
    target = tmp_path / "activation.json"
    assert (
        service.export_policy_activation(record["activation_id"], result["record_sha256"], target) == result
    )
    assert json.loads(target.read_text(encoding="utf-8")) == result
    with pytest.raises(FileExistsError):
        service.export_policy_activation(record["activation_id"], result["record_sha256"], target)
    with pytest.raises(ValueError):
        service.activate_policy_change(request(approval))
    assert len(rows(runtime, "policy_activations")) == 1


@pytest.mark.parametrize(
    "change",
    [
        {"mode": "physical"},
        {"mode": "shadow"},
        {"confirmation": True},
        {"confirmation": False},
        {"confirmation": "ACTIVATE"},
        {"approval_id": "bad"},
        {"approval_id": None},
        {"expected_approval_sha256": "bad"},
        {"expected_approval_sha256": "A" * 64},
        {"expected_approval_sha256": None},
        {"expected_approval_sha256": "0" * 64},
        {"approval_id": "00000000-0000-0000-0000-000000000000"},
        {"threshold": 0.8},
        {"optional_model_id": "ridge"},
        {"approval": {}},
    ],
)
def test_invalid_activation_has_no_write_and_preserves_selection(local, change):
    service, runtime = local
    approval = approved(service)
    before = runtime.journal.path.read_bytes()
    with pytest.raises(ValueError):
        service.activate_policy_change(request(approval, **change))
    assert runtime.policy.engine.threshold == 0.65 and runtime.journal.path.read_bytes() == before
    assert service._policy_preparation._approval_json is not None


def test_missing_fields_nonobjects_and_uninitialized_runtime_refused(local, tmp_path):
    service, _ = local
    approval = approved(service)
    payload = request(approval)
    for field in payload:
        with pytest.raises(ValueError):
            service.activate_policy_change({k: v for k, v in payload.items() if k != field})
    for value in (None, [], "activate"):
        with pytest.raises(ValueError):
            service.activate_policy_change(value)
    missing = AssuranceDashboardService(control_journal_path=tmp_path / "absent.sqlite")
    with pytest.raises(ValueError, match="initialize"):
        missing.activate_policy_change(payload)
    with pytest.raises(ValueError, match="initialize"):
        missing.policy_activation_status()
    assert not missing.control_journal_path.exists()


@pytest.mark.parametrize(
    "change",
    [
        "policy",
        "equivalent_policy",
        "topology",
        "same_version_topology",
        "supervisor",
        "unavailable",
        "expiry",
        "review",
        "session",
        "runtime",
    ],
)
def test_stale_approval_cannot_activate(local, monkeypatch, change):
    service, runtime = local
    clock = [1000.0]
    monkeypatch.setattr(preparation.time, "monotonic", lambda: clock[0])
    approval = approved(service)
    if change in ("policy", "equivalent_policy"):
        runtime.policy = RepeaterSandboxPolicy(0.8 if change == "policy" else 0.65)
    elif change == "topology":
        service.topology_store.add_inventory_node(AddSimulatedNode("new", "endpoint", 0, "127.0.0.1", 7411))
    elif change == "same_version_topology":
        with sqlite3.connect(runtime.journal.path) as con:
            payload = json.loads(con.execute("SELECT state_json FROM simulated_topologies").fetchone()[0])
            payload["links"][0]["distance_km"] += 1
            con.execute("UPDATE simulated_topologies SET state_json=?", (json.dumps(payload),))
    elif change == "supervisor":
        runtime.supervisor = type(runtime.supervisor)()
    elif change == "unavailable":
        runtime.policy = object()
    elif change == "expiry":
        clock[0] += preparation.MAX_PROPOSAL_AGE_S
    elif change == "review":
        proposal = approval["record"]["approved_review"]["record"]["proposal"]
        service.review_policy_change(*preparation_tests.selection(proposal))
    elif change == "session":
        service.invalidate_policy_preparation()
    else:
        service._controller_runtime = ControllerRuntime(runtime.journal.path)
    before = runtime.journal.path.read_bytes()
    policy = runtime.policy
    with pytest.raises(ValueError):
        service.activate_policy_change(request(approval))
    assert runtime.policy is policy and runtime.journal.path.read_bytes() == before


@pytest.mark.parametrize(
    "stage", ["clone", "schema", "readback", "commit_before", "commit_after", "expiry_during_commit"]
)
def test_failed_transition_restores_exact_policy_and_ambiguous_commit_blocks_cycles(
    local, monkeypatch, stage
):
    service, runtime = local
    clock = [1000.0]
    monkeypatch.setattr(preparation.time, "monotonic", lambda: clock[0])
    approval = approved(service)
    original = runtime.policy
    before = runtime.journal.path.read_bytes()

    def fail(*args):
        raise RuntimeError("injected failure")

    if stage == "clone":
        monkeypatch.setattr(activation, "_candidate", fail)
    elif stage == "schema":
        schema = activation._schema

        def faulty(con):
            schema(con)
            fail()

        monkeypatch.setattr(activation, "_schema", faulty)
    elif stage == "readback":
        describe = activation._describe

        def faulty(item):
            if item is runtime and item.policy is not original:
                fail()
            return describe(item)

        monkeypatch.setattr(activation, "_describe", faulty)
    else:

        def faulty(con):
            if stage != "commit_before":
                con.commit()
            if stage == "expiry_during_commit":
                clock[0] += preparation.MAX_PROPOSAL_AGE_S
            else:
                fail()

        monkeypatch.setattr(activation, "_commit", faulty)
    with pytest.raises((RuntimeError, ValueError)):
        service.activate_policy_change(request(approval))
    assert runtime.policy is original and original.engine.threshold == 0.65
    assert service._policy_preparation._approval_json is None
    if stage.startswith("commit") or stage == "expiry_during_commit":
        assert service.policy_activation_status()["record"]["status"] == "UNRESOLVED"
        with pytest.raises(ValueError, match="UNRESOLVED"):
            runtime.cycle(sandbox.snapshot())
    else:
        assert runtime.journal.path.read_bytes() == before
        assert service.policy_activation_status()["record"]["status"] == "NO_LOCAL_ACTIVATION"
    if stage in ("commit_after", "expiry_during_commit"):
        assert len(rows(runtime, "policy_activations")) == 1
    assert rows(runtime, "control_cycles") == []


def test_topology_read_is_inside_write_transaction_and_blocks_other_writer(local, monkeypatch):
    service, runtime = local
    approval = approved(service)
    original = preparation._read_topology
    checked = []

    def locked(connection, *args):
        assert connection.in_transaction
        with sqlite3.connect(runtime.journal.path, timeout=0.01) as writer:
            with pytest.raises(sqlite3.OperationalError, match="locked"):
                writer.execute("UPDATE simulated_topologies SET topology_version=topology_version+1")
        checked.append(True)
        return original(connection, *args)

    monkeypatch.setattr(preparation, "_read_topology", locked)
    service.activate_policy_change(request(approval))
    assert checked == [True]


def test_simulation_cycle_uses_and_records_active_policy_without_physical_mode(local, monkeypatch):
    service, runtime = local
    activated = service.activate_policy_change(request(approved(service)))
    result = runtime.cycle(sandbox.snapshot())
    trace = result["record"]["intent"]["policy_details"]["policy_activation"]
    assert trace["activation_id"] == activated["record"]["activation_id"]
    assert trace["active_policy_sha256"] == activated["record"]["active_policy_sha256"]
    assert trace["runtime_instance_id"] == runtime.instance_id
    for mode in ("physical", "shadow", "assurance"):
        with pytest.raises(ValueError, match="simulation mode"):
            runtime.cycle(sandbox.snapshot(request_id="other"), mode=mode)
    assert len(rows(runtime, "control_cycles")) == 1


@pytest.mark.parametrize(
    "change", ["object", "threshold", "supervisor", "missing_record", "corrupt_record", "index", "journal"]
)
def test_live_mismatch_or_unreadable_audit_blocks_cycle(local, tmp_path, change):
    service, runtime = local
    service.activate_policy_change(request(approved(service)))
    if change == "object":
        runtime.policy = RepeaterSandboxPolicy(0.7)
    elif change == "threshold":
        runtime.policy.engine.threshold = 0.8
    elif change == "supervisor":
        runtime.supervisor = type(runtime.supervisor)()
    elif change == "journal":
        runtime.journal.path = tmp_path / "absent.sqlite"
    else:
        with sqlite3.connect(runtime.journal.path) as con:
            if change == "missing_record":
                con.execute("DELETE FROM policy_activations")
            elif change == "index":
                con.execute("UPDATE policy_activations SET runtime_instance_id='other'")
            else:
                con.execute("UPDATE policy_activations SET record_json='{}'")
    assert service.policy_activation_status()["record"]["status"] == "UNVERIFIED"
    with pytest.raises(ValueError, match="UNVERIFIED"):
        runtime.cycle(sandbox.snapshot())


def test_restart_preserves_history_but_never_restores_policy_or_approval(local, tmp_path):
    service, runtime = local
    result = service.activate_policy_change(request(approved(service)))
    fresh = AssuranceDashboardService(control_journal_path=runtime.journal.path)
    reopened = fresh.controller_runtime
    assert reopened.instance_id != runtime.instance_id and reopened.policy.engine.threshold == 0.65
    assert fresh.policy_activation_status()["record"]["status"] == "NO_LOCAL_ACTIVATION"
    assert (
        fresh.export_policy_activation(
            result["record"]["activation_id"], result["record_sha256"], tmp_path / "old.json"
        )
        == result
    )
    with pytest.raises(ValueError):
        fresh.activate_policy_change(request(result["record"]["approval"]))


def test_second_activation_has_new_revision_preserves_previous_and_does_not_reuse_approval(local):
    service, runtime = local
    first = service.activate_policy_change(request(approved(service)))
    context = service.policy_preparation_context()
    proposal = service.prepare_policy_change(preparation_tests.payload(context, threshold=0.8))
    reviewed = service.review_policy_change(*preparation_tests.selection(proposal))
    approval = service.approve_policy_change(approval_tests.request(reviewed))
    second = service.activate_policy_change(request(approval))
    assert second["record"]["revision"] == first["record"]["revision"] + 1
    assert second["record"]["previous_activation_id"] == first["record"]["activation_id"]
    assert second["record"]["previous_policy"]["threshold"] == 0.7 and runtime.policy.engine.threshold == 0.8
    assert len(rows(runtime, "policy_activations")) == 2


def test_activation_serializes_against_cycle_and_duplicate_requests(local, monkeypatch):
    service, runtime = local
    approval = approved(service)
    entered, release = Event(), Event()
    commit = activation._commit

    def slow(con):
        entered.set()
        assert release.wait(5)
        commit(con)

    monkeypatch.setattr(activation, "_commit", slow)
    with ThreadPoolExecutor(max_workers=3) as pool:
        first = pool.submit(service.activate_policy_change, request(approval))
        assert entered.wait(3)
        cycle = pool.submit(runtime.cycle, sandbox.snapshot())
        second = pool.submit(service.activate_policy_change, request(approval))
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
    assert len(rows(runtime, "policy_activations")) == 1


def test_observation_only_and_provider_stop_do_not_revert_an_active_policy(local):
    service, runtime = local
    before = runtime.journal.path.read_bytes()
    assert service.policy_activation_status()["record"]["status"] == "NO_LOCAL_ACTIVATION"
    assert runtime.journal.path.read_bytes() == before
    service.activate_policy_change(request(approved(service)))
    service.invalidate_policy_preparation()
    assert runtime.policy.engine.threshold == 0.7
    assert service.policy_activation_status()["record"]["status"] == "ACTIVE"


def test_runtimes_sharing_journal_keep_independent_activation_identity(local):
    service, runtime = local
    first = service.activate_policy_change(request(approved(service)))
    other = AssuranceDashboardService(control_journal_path=runtime.journal.path)
    other.controller_runtime
    second = other.activate_policy_change(request(approved(other)))
    assert second["record"]["revision"] > first["record"]["revision"]
    assert second["record"]["runtime_instance_id"] != first["record"]["runtime_instance_id"]
    assert service.policy_activation_status()["record"]["activation"] == first
    assert other.policy_activation_status()["record"]["activation"] == second


@pytest.mark.parametrize(
    "raw", ['{"status":"COMMITTED","status":"COMMITTED"}', '{"x":NaN}', "x" * (256 * 1024 + 1)],
    ids=["duplicate-fields", "nonfinite", "oversized"],
)
def test_untrusted_activation_json_is_bounded_and_refused(local, tmp_path, raw):
    service, runtime = local
    result = service.activate_policy_change(request(approved(service)))
    with sqlite3.connect(runtime.journal.path) as connection:
        connection.execute("UPDATE policy_activations SET record_json=?", (raw,))
    assert service.policy_activation_status()["record"]["status"] == "UNVERIFIED"
    target = tmp_path / "refused.json"
    with pytest.raises(ValueError):
        service.export_policy_activation(result["record"]["activation_id"], result["record_sha256"], target)
    assert not target.exists()


def test_activation_export_cannot_overwrite_journal(local):
    service, runtime = local
    result = service.activate_policy_change(request(approved(service)))
    before = runtime.journal.path.read_bytes()
    with pytest.raises(FileExistsError):
        service.export_policy_activation(
            result["record"]["activation_id"], result["record_sha256"], runtime.journal.path
        )
    assert runtime.journal.path.read_bytes() == before
