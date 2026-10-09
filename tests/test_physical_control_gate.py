"""PHYSICAL mode must stay unreachable until an adapter is certified,
released, and dual-authorized — and the sandbox supervisor must refuse it
on its own, independent of the executor's own gate (defence in depth)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from quantum_autopilot.control_plane.contracts import ControlAction, ControlMode
from quantum_autopilot.control_plane.journal import ControlJournal
from quantum_autopilot.control_plane.physical import (
    HardwareInterlock,
    InterlockState,
    PHYSICAL_ACTION_GATES,
    PhysicalAuthorization,
    PhysicalExecutor,
    REALTIME_TIER_ACTIONS,
    authorize_physical_action,
)
from quantum_autopilot.control_plane.runtime import ControllerRuntime
from quantum_autopilot.core import EvidenceKind, Provenance


def _snapshot(node_id="repeater-a", request_id="physical-cycle"):
    now = datetime.now(timezone.utc).isoformat()
    return {
        "request_id": request_id,
        "node_id": node_id,
        "timestamp_utc": now,
        "state_version": 0,
        "predicted_fidelity": 0.9,
        "prediction_uncertainty": 0.01,
        "telemetry_age_s": 0.1,
        "memory_state": "STORING",
        "available_pair_fidelities": (0.9, 0.9),
        "memory_ages_s": (0.0, 0.0),
        "integrity_verified": True,
        "operator_interlock": True,
        "provenance": Provenance(
            evidence_kind=EvidenceKind.SIMULATED,
            source="test",
            timestamp=now,
            metadata={"evidence_lane": "bell_fidelity_control"},
        ),
    }


def _authorization(active=True):
    now = datetime.now(timezone.utc)
    expires = now + (timedelta(minutes=5) if active else -timedelta(minutes=5))
    return PhysicalAuthorization(
        operator_id="OP-2291",
        cosigner_id="KC-0330",
        authorized_at_utc=(now if active else now - timedelta(minutes=10)).isoformat(),
        expires_at_utc=expires.isoformat(),
    )


@pytest.mark.unit
def test_authorization_rejects_a_single_signer():
    with pytest.raises(ValueError, match="two different people"):
        PhysicalAuthorization(
            operator_id="OP-2291", cosigner_id="OP-2291",
            authorized_at_utc=datetime.now(timezone.utc).isoformat(),
            expires_at_utc=datetime.now(timezone.utc).isoformat(),
        )


@pytest.mark.unit
def test_interlock_defaults_engaged_and_requires_a_named_operator():
    interlock = HardwareInterlock()
    assert interlock.state is InterlockState.ENGAGED
    with pytest.raises(ValueError):
        interlock.release("")
    interlock.release("OP-2291")
    assert interlock.state is InterlockState.RELEASED


@pytest.mark.unit
def test_interlock_watchdog_auto_engages_without_a_heartbeat():
    interlock = HardwareInterlock(timeout_s=0.05)
    interlock.release("OP-2291")
    assert interlock.state is InterlockState.RELEASED
    import time
    time.sleep(0.08)
    assert interlock.state is InterlockState.ENGAGED


@pytest.mark.unit
def test_interlock_keep_alive_resets_the_watchdog():
    interlock = HardwareInterlock(timeout_s=0.1)
    interlock.release("OP-2291")
    import time
    time.sleep(0.06)
    interlock.keep_alive()
    time.sleep(0.06)
    assert interlock.state is InterlockState.RELEASED   # renewed before either window elapsed


@pytest.mark.unit
def test_keep_alive_refuses_on_an_engaged_interlock():
    interlock = HardwareInterlock()
    with pytest.raises(ValueError, match="not released"):
        interlock.keep_alive()


@pytest.mark.unit
def test_authorization_revocation_is_a_new_fact_not_an_edit():
    original = _authorization()
    revoked = original.revoke()
    assert original.revoked_at_utc is None      # the journalled record is untouched
    assert revoked.revoked_at_utc is not None
    assert revoked.active() is False


@pytest.mark.unit
def test_halt_only_requires_a_certified_adapter():
    """HALT must never be gated behind an interlock release or dual-auth —
    that would make the abort action as hard to reach as the risky one."""

    class FakeAdapter:
        CERTIFIED = True

    executor = PhysicalExecutor(adapter=FakeAdapter())   # interlock stays ENGAGED, no auth
    with pytest.raises(NotImplementedError):
        executor.execute(
            intent=_fake_intent(action=ControlAction.HALT), snapshot=_fake_snapshot(),
            mode=ControlMode.PHYSICAL,
        )


@pytest.mark.unit
def test_realtime_tier_actions_are_categorically_refused():
    """WAIT/PURIFY/SWAP must refuse no matter how certified/released/
    authorized the executor is — no combination of gates can make this
    software fast enough to own a per-shot decision inside memory coherence
    time (see ROADMAP.md item 4)."""

    class FakeAdapter:
        CERTIFIED = True

    interlock = HardwareInterlock()
    interlock.release("OP-2291")
    executor = PhysicalExecutor(adapter=FakeAdapter(), interlock=interlock)

    for action in (ControlAction.WAIT, ControlAction.PURIFY, ControlAction.SWAP):
        result = executor.execute(
            intent=_fake_intent(action=action), snapshot=_fake_snapshot(),
            mode=ControlMode.PHYSICAL, authorization=_authorization(),
        )
        assert result.details["reason_code"] == "action_belongs_to_realtime_tier_not_this_software"
        assert result.details["realtime_tier_action"] is True


@pytest.mark.unit
def test_gate_table_matches_the_documented_tiering():
    assert set(PHYSICAL_ACTION_GATES) == {ControlAction.HALT}
    assert PHYSICAL_ACTION_GATES[ControlAction.HALT] == {
        "requires_interlock_released": False, "requires_authorization": False}
    assert REALTIME_TIER_ACTIONS == {ControlAction.WAIT, ControlAction.PURIFY, ControlAction.SWAP}


@pytest.mark.unit
def test_executor_refuses_with_no_adapter_registered():
    executor = PhysicalExecutor()
    result = executor.execute(
        intent=_fake_intent(action=ControlAction.HALT), snapshot=_fake_snapshot(),
        mode=ControlMode.PHYSICAL, authorization=_authorization(),
    )
    assert result.physical_action_performed is False
    assert result.details["reason_code"] == "physical_adapter_not_certified"


@pytest.mark.unit
def test_action_gates_are_overridable_for_a_deployment_that_tightens_halt():
    """The open question in PHYSICAL_ACTION_GATES's docstring — should HALT
    require the full chain in some deployments — is a constructor override,
    not a fork of this file."""

    class FakeAdapter:
        CERTIFIED = True

    tightened = {ControlAction.HALT: {"requires_interlock_released": True, "requires_authorization": True}}
    executor = PhysicalExecutor(adapter=FakeAdapter(), action_gates=tightened)

    engaged = executor.execute(
        intent=_fake_intent(action=ControlAction.HALT), snapshot=_fake_snapshot(),
        mode=ControlMode.PHYSICAL, authorization=_authorization(),
    )
    assert engaged.details["reason_code"] == "hardware_interlock_engaged"

    executor.interlock.release("OP-2291")
    unauthorized = executor.execute(
        intent=_fake_intent(action=ControlAction.HALT), snapshot=_fake_snapshot(),
        mode=ControlMode.PHYSICAL,
    )
    assert unauthorized.details["reason_code"] == "physical_authorization_missing"

    expired = executor.execute(
        intent=_fake_intent(action=ControlAction.HALT), snapshot=_fake_snapshot(),
        mode=ControlMode.PHYSICAL, authorization=_authorization(active=False),
    )
    assert expired.details["reason_code"] == "physical_authorization_expired"

    revoked = executor.execute(
        intent=_fake_intent(action=ControlAction.HALT), snapshot=_fake_snapshot(),
        mode=ControlMode.PHYSICAL, authorization=_authorization().revoke(),
    )
    assert revoked.details["reason_code"] == "physical_authorization_revoked"

    with pytest.raises(NotImplementedError):
        executor.execute(
            intent=_fake_intent(action=ControlAction.HALT), snapshot=_fake_snapshot(),
            mode=ControlMode.PHYSICAL, authorization=_authorization(),
        )


@pytest.mark.unit
def test_runtime_cycle_in_physical_mode_always_halts(tmp_path):
    runtime = ControllerRuntime(tmp_path / "journal.sqlite")
    result = runtime.cycle(
        _snapshot(), mode="physical", physical_authorization=_authorization(),
    )
    record = result["record"]

    assert record["safety"]["authorized"] is False
    assert record["safety"]["effective_action"] == "HALT"
    assert record["safety"]["violations"][0]["reason_code"] == (
        "physical_mode_not_authorized_by_sandbox_supervisor"
    )
    assert record["execution"]["physical_action_performed"] is False
    assert record["execution"]["details"]["reason_code"] == "physical_adapter_not_certified"


@pytest.mark.unit
def test_runtime_status_reports_the_interlock_and_certification_state(tmp_path):
    runtime = ControllerRuntime(tmp_path / "journal.sqlite")
    status = runtime.status()

    assert status["physical_executor_certified"] is False
    assert status["physical_interlock_state"] == "engaged"
    assert "physical" not in status["available_modes"]
    assert status["recognized_but_refused_modes"] == ["physical"]
    assert status["physical_executor_available"] is False


@pytest.mark.unit
def test_interlock_state_changes_are_journalled(tmp_path):
    journal = ControlJournal(tmp_path / "journal.sqlite")
    interlock = HardwareInterlock(journal=journal)

    interlock.release("OP-2291")
    interlock.keep_alive()
    interlock.engage("OP-2291")

    events = journal.list_recent_safety_events()
    kinds = [e["kind"] for e in events]
    assert "interlock_released" in kinds
    assert "interlock_kept_alive" in kinds
    assert "interlock_auto_engaged" in kinds   # engage() routes through _auto_engage
    assert all(len(e["event_sha256"]) == 64 for e in events)


@pytest.mark.unit
def test_watchdog_timeout_is_journalled_as_system_actor(tmp_path):
    journal = ControlJournal(tmp_path / "journal.sqlite")
    interlock = HardwareInterlock(timeout_s=0.05, journal=journal)
    interlock.release("OP-2291")

    import time
    time.sleep(0.08)
    assert interlock.state is InterlockState.ENGAGED

    events = journal.list_recent_safety_events()
    watchdog_event = next(e for e in events if e["kind"] == "interlock_auto_engaged")
    assert watchdog_event["actor"] == "system"
    assert watchdog_event["detail"]["reason"] == "watchdog_timeout"


@pytest.mark.unit
def test_authorization_lifecycle_is_journalled(tmp_path):
    journal = ControlJournal(tmp_path / "journal.sqlite")
    authorization = authorize_physical_action("OP-2291", "KC-0330", ttl_s=60, journal=journal)
    authorization.revoke(journal=journal, revoked_by="KC-0330")

    events = journal.list_recent_safety_events()
    kinds = [e["kind"] for e in events]
    assert "physical_authorization_created" in kinds
    assert "physical_authorization_revoked" in kinds
    revoked_event = next(e for e in events if e["kind"] == "physical_authorization_revoked")
    assert revoked_event["actor"] == "KC-0330"


@pytest.mark.unit
def test_runtime_auto_wires_its_own_interlock_to_its_own_journal(tmp_path):
    runtime = ControllerRuntime(tmp_path / "journal.sqlite")
    runtime.physical_executor.interlock.release("OP-2291")

    events = runtime.journal.list_recent_safety_events()
    assert any(e["kind"] == "interlock_released" for e in events)
    assert runtime.status()["recent_safety_events"]


def _fake_snapshot():
    from quantum_autopilot.control_plane.contracts import NetworkStateSnapshot

    return NetworkStateSnapshot.from_dict(_snapshot())


def _fake_intent(action=ControlAction.HALT):
    from quantum_autopilot.control_plane.contracts import ControlIntent

    now = datetime.now(timezone.utc).isoformat()
    return ControlIntent(
        intent_id="i-1", request_id="physical-cycle", node_id="repeater-a",
        action=action, expected_state_version=0,
        issued_at_utc=now, policy_name="test",
    )
