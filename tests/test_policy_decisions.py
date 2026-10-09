"""Historical decision explanations verify recorded facts without runtime initialization or execution."""

import json
import sqlite3

import pytest

from quantum_autopilot.control_plane import policy_decisions as audit
from quantum_autopilot.control_plane.topology import _sha256
from quantum_autopilot.qkd.dashboard_api import AssuranceDashboardService
from tests import test_policy_activation as activation
from tests import test_policy_recovery as recovery
from tests import test_control_plane_sandbox as sandbox

local = activation.local


def activate(service):
    return service.activate_policy_change(activation.request(activation.approved(service)))


def cycle(local, **changes):
    service, runtime = local
    policy = activate(service)
    result = runtime.cycle(sandbox.snapshot(**changes))
    return policy, result


def rewrite(runtime, record):
    with sqlite3.connect(runtime.journal.path) as con:
        con.execute(
            "UPDATE control_cycles SET record_json=?,record_sha256=?", (json.dumps(record), _sha256(record))
        )


def test_verified_intent_safety_executor_and_recorded_policy_are_read_only(local, monkeypatch):
    service, runtime = local
    policy, result = cycle(local, prediction_uncertainty=0.8)
    before = runtime.journal.path.read_bytes()

    def forbidden(*args, **kwargs):
        pytest.fail("audit cannot execute or re-decide")

    monkeypatch.setattr(runtime, "cycle", forbidden)
    monkeypatch.setattr(runtime.policy, "decide", forbidden)
    listing = service.policy_decision_history()
    item = listing["record"]["items"][0]
    explained = service.explain_policy_decision(item["request_id"], item["record_sha256"])
    record = explained["record"]
    assert record["cycle"] == {k: result[k] for k in ("record", "record_sha256")}
    assert record["policy_evidence"]["status"] == "VERIFIED_RECORDED_HASH"
    assert record["policy_evidence"]["activation"] == policy
    assert record["intended_action"] == result["record"]["intent"]["action"]
    assert not record["authorized"] and record["effective_action"] == "HALT"
    assert (
        record["execution"] == result["record"]["execution"] and record["execution"]["performed_in_simulator"]
    )
    assert record["safety_violations"] == result["record"]["safety"]["violations"]
    assert not record["current_activity_observed"] and not record["decision_recomputed"]
    assert runtime.journal.path.read_bytes() == before


def test_old_baseline_replay_never_acquires_later_policy_reference(local):
    service, runtime = local
    state = sandbox.snapshot()
    first = runtime.cycle(state)
    activate(service)
    replay = runtime.cycle(state)
    assert replay["idempotent_replay"] and replay["record"] == first["record"]
    record = service.explain_policy_decision(state.request_id)["record"]
    assert record["policy_evidence"]["status"] == "NOT_RECORDED"
    assert record["policy_evidence"]["activation"] is None


def test_restart_and_rollback_do_not_replace_historical_policy(local):
    service, runtime = local
    first, result = cycle(local)
    recovery.activate_rollback(service, first)
    other = AssuranceDashboardService(control_journal_path=runtime.journal.path)
    record = other.explain_policy_decision(result["record"]["snapshot"]["request_id"])["record"]
    assert other._controller_runtime is None
    assert record["policy_evidence"]["activation"] == first
    assert record["policy_evidence"]["activation"]["record"]["active_policy"]["threshold"] == 0.7
    assert runtime.policy.engine.threshold == 0.65


def test_legacy_reference_is_explicitly_weaker(local):
    service, runtime = local
    _, result = cycle(local)
    raw = result["record"]
    del raw["intent"]["policy_details"]["policy_activation"]["activation_record_sha256"]
    rewrite(runtime, raw)
    record = service.explain_policy_decision(raw["snapshot"]["request_id"])["record"]
    assert record["policy_evidence"]["status"] == "LEGACY_REFERENCE_MATCH"


@pytest.mark.parametrize(
    "fault", ["missing", "corrupt", "revision", "runtime", "receipt_hash", "config_hash"]
)
def test_bad_policy_evidence_does_not_invent_configuration_or_discard_cycle_facts(local, fault):
    service, runtime = local
    _, result = cycle(local)
    if fault in ("missing", "corrupt"):
        with sqlite3.connect(runtime.journal.path) as con:
            con.execute(
                "DELETE FROM policy_activations"
                if fault == "missing"
                else "UPDATE policy_activations SET record_json='{}'"
            )
    else:
        raw = result["record"]
        ref = raw["intent"]["policy_details"]["policy_activation"]
        key = {
            "revision": "revision",
            "runtime": "runtime_instance_id",
            "receipt_hash": "activation_record_sha256",
            "config_hash": "active_policy_sha256",
        }[fault]
        ref[key] = 999 if fault == "revision" else "0" * 64
        rewrite(runtime, raw)
    explained = service.explain_policy_decision(result["record"]["snapshot"]["request_id"])["record"]
    assert explained["policy_evidence"]["status"] == ("UNAVAILABLE" if fault == "missing" else "UNVERIFIED")
    assert explained["policy_evidence"]["activation"] is None
    assert explained["execution"] == result["record"]["execution"]


@pytest.mark.parametrize(
    "fault",
    [
        "hash",
        "index",
        "request_hash",
        "intended",
        "execution",
        "authorized",
        "schema",
        "nonfinite",
        "duplicate",
        "oversize",
    ],
)
def test_cycle_corruption_is_refused(local, fault):
    service, runtime = local
    result = runtime.cycle(sandbox.snapshot())
    raw = result["record"]
    if fault in ("hash", "index", "request_hash", "nonfinite", "duplicate", "oversize"):
        sql, args = {
            "hash": ("UPDATE control_cycles SET record_sha256=?", ("0" * 64,)),
            "index": ("UPDATE control_cycles SET action=?", ("HALT",)),
            "request_hash": ("UPDATE control_cycles SET request_sha256=?", ("0" * 64,)),
            "nonfinite": ("UPDATE control_cycles SET record_json=?", ('{"x":NaN}',)),
            "duplicate": ("UPDATE control_cycles SET record_json=?", ('{"x":1,"x":2}',)),
            "oversize": ("UPDATE control_cycles SET record_json=?", ("x" * (audit.MAX_CYCLE_BYTES + 1),)),
        }[fault]
        with sqlite3.connect(runtime.journal.path) as con:
            con.execute(sql, args)
    else:
        if fault == "intended":
            raw["safety"]["intended_action"] = "WAIT"
        elif fault == "execution":
            raw["execution"]["action"] = "WAIT"
        elif fault == "schema":
            raw["schema_version"] = "future"
        else:
            raw["safety"]["effective_action"] = "HALT"
            raw["execution"]["action"] = "HALT"
        rewrite(runtime, raw)
    with pytest.raises((ValueError, TypeError)):
        service.explain_policy_decision(raw["snapshot"]["request_id"])


@pytest.mark.parametrize("value", [True, 0, 51, 1.5, "20"])
def test_history_limit_is_bounded_without_initialization(tmp_path, value):
    service = AssuranceDashboardService(control_journal_path=tmp_path / "missing.sqlite")
    with pytest.raises(ValueError):
        service.policy_decision_history(value)
    assert not (tmp_path / "missing.sqlite").exists() and service._controller_runtime is None


def test_missing_store_empty_store_missing_request_and_wrong_selection(local, tmp_path):
    service, runtime = local
    assert service.policy_decision_history()["record"]["items"] == []
    with pytest.raises(ValueError):
        service.explain_policy_decision("missing")
    runtime.cycle(sandbox.snapshot())
    with pytest.raises(ValueError):
        service.explain_policy_decision("request-001", "0" * 64)
    missing = tmp_path / "absent.sqlite"
    with pytest.raises(FileNotFoundError):
        audit.decision_history(missing)
    assert not missing.exists()


def test_only_simulation_cycles_are_exposed(local):
    service, runtime = local
    runtime.cycle(sandbox.snapshot(request_id="shadow"), mode="shadow")
    runtime.cycle(sandbox.snapshot(request_id="simulation"))
    assert [i["request_id"] for i in service.policy_decision_history()["record"]["items"]] == ["simulation"]
    with pytest.raises(ValueError):
        service.explain_policy_decision("shadow")


def test_cycle_and_activation_are_read_in_same_sqlite_snapshot(local, monkeypatch):
    service, runtime = local
    first, result = cycle(local)
    with sqlite3.connect(runtime.journal.path) as con:
        con.execute("PRAGMA journal_mode=WAL")
    original = audit._policy

    def concurrent_delete(connection, record):
        with sqlite3.connect(runtime.journal.path) as writer:
            writer.execute("DELETE FROM policy_activations")
        return original(connection, record)

    monkeypatch.setattr(audit, "_policy", concurrent_delete)
    read = service.explain_policy_decision(result["record"]["snapshot"]["request_id"])
    assert read["record"]["policy_evidence"]["activation"] == first
    monkeypatch.setattr(audit, "_policy", original)
    assert (
        service.explain_policy_decision("request-001")["record"]["policy_evidence"]["status"] == "UNAVAILABLE"
    )


def test_recovery_checkpoint_can_be_linked_by_recorded_hash(local, monkeypatch):
    service, runtime = local
    recovery.fail_commit(service, monkeypatch)
    context = service.policy_recovery_context()
    recovered = service.recover_policy_change(recovery.recovery_request(context))
    runtime.cycle(sandbox.snapshot())
    assert (
        service.explain_policy_decision("request-001")["record"]["policy_evidence"]["activation"] == recovered
    )
