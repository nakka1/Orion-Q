"""Physical refusal scaffolding for future adapter design.

There is no hardware command implementation. WAIT/PURIFY/SWAP are reserved
for a future device tier; HALT also cannot execute. Lease and named-signer
records are diagnostics, not hardware interlocks or authenticated identities.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import ClassVar, Protocol
from uuid import uuid4
import math
import time

from quantum_autopilot.control_plane.contracts import (
    ControlAction,
    ControlExecutionResult,
    ControlIntent,
    ControlMode,
    NetworkStateSnapshot,
)
from quantum_autopilot.control_plane.journal import ControlJournal


class InterlockState(str, Enum):
    ENGAGED = "engaged"     # default; blocks every physical command
    RELEASED = "released"   # explicitly opened for one session, by a human


SAFE_MEMORY_STATES = frozenset({"IDLE"})
"""The only `NetworkStateSnapshot.memory_state` values a node may be left in
when the interlock re-engages or an authorization is revoked mid-session.
STORING means a pair is held; a physical adapter must drive the node to a
safe state before actually releasing control, not just stop issuing commands.
"""


class HardwareInterlock:
    """Diagnostic software lease, checked on reads using a monotonic clock.

    This is not an independent hardware watchdog: if the process stops, no
    transition can be executed or journalled. Physical actuation remains
    refused. A future adapter needs an independent device-side watchdog.
    """

    def __init__(self, timeout_s: float = 30.0, journal: ControlJournal | None = None) -> None:
        if not math.isfinite(timeout_s) or timeout_s <= 0:
            raise ValueError("timeout_s must be positive")
        self.timeout_s = timeout_s
        self.journal = journal
        self._state = InterlockState.ENGAGED
        self._released_by: str | None = None
        self._released_at: str | None = None
        self._last_heartbeat: datetime | None = None
        self._last_heartbeat_monotonic: float | None = None

    def _log(self, kind: str, actor: str, detail: dict) -> None:
        if self.journal is not None:
            self.journal.append_safety_event(
                kind, actor, datetime.now(timezone.utc).isoformat(), detail
            )

    @property
    def state(self) -> InterlockState:
        """Resolve a lapsed watchdog before answering — RELEASED is only ever
        reported while the heartbeat is still fresh."""

        if self._state is InterlockState.RELEASED and self._watchdog_expired():
            self._auto_engage("watchdog_timeout")
        return self._state

    def _watchdog_expired(self) -> bool:
        if self._last_heartbeat_monotonic is None:
            return True
        return time.monotonic() - self._last_heartbeat_monotonic > self.timeout_s

    def _auto_engage(self, reason: str) -> None:
        self._state = InterlockState.ENGAGED
        released_by = self._released_by
        self._released_by = None
        self._released_at = None
        self._last_heartbeat = None
        self._last_heartbeat_monotonic = None
        self._last_auto_engage_reason = reason
        self._log("interlock_auto_engaged", "system",
                  {"reason": reason, "previously_released_by": released_by})

    def release(self, operator_id: str) -> None:
        if not operator_id or not operator_id.strip():
            raise ValueError("releasing the interlock requires a named operator_id")
        self._state = InterlockState.RELEASED
        self._released_by = operator_id.strip()
        self._released_at = datetime.now(timezone.utc).isoformat()
        self._last_heartbeat = datetime.now(timezone.utc)
        self._last_heartbeat_monotonic = time.monotonic()
        self._log("interlock_released", self._released_by, {"timeout_s": self.timeout_s})

    def keep_alive(self) -> None:
        """Reset the watchdog. Raises if called while not RELEASED — a
        heartbeat implies an active session, not a request to start one."""

        if self.state is not InterlockState.RELEASED:
            raise ValueError("cannot keep-alive an interlock that is not released")
        self._last_heartbeat = datetime.now(timezone.utc)
        self._last_heartbeat_monotonic = time.monotonic()
        self._log("interlock_kept_alive", self._released_by or "unknown", {})

    def engage(self, operator_id: str) -> None:
        """Re-engage. Always allowed, from any state, with or without a name."""

        self._auto_engage(f"manual:{operator_id or 'unspecified'}")

    def describe(self) -> dict:
        return {
            "state": self.state.value,
            "released_by": self._released_by,
            "released_at": self._released_at,
            "watchdog_timeout_s": self.timeout_s,
            "last_heartbeat_utc": self._last_heartbeat.isoformat() if self._last_heartbeat else None,
        }


@dataclass(frozen=True)
class PhysicalAuthorization:
    """Diagnostic two-name record, not authentication or digital signatures.
    `operator_id` and `cosigner_id` must be distinct strings; this does not
    verify that they represent two people. No hardware command is approved.
    `revoked_at_utc` lets either signer withdraw approval before expiry —
    revocation is checked by `active()` same as expiry, and a revoked
    authorization can never be un-revoked (construct a new one instead)."""

    operator_id: str
    cosigner_id: str
    authorized_at_utc: str
    expires_at_utc: str
    revoked_at_utc: str | None = None
    authorization_id: str = field(default_factory=lambda: str(uuid4()))
    _journal: ControlJournal | None = field(default=None, repr=False, compare=False)
    _revoked_ids: ClassVar[set[str]] = set()

    def __post_init__(self) -> None:
        for name in ("operator_id", "cosigner_id"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} cannot be empty")
        if self.operator_id.strip() == self.cosigner_id.strip():
            raise ValueError("operator_id and cosigner_id must be two different people")
        for name in ("authorized_at_utc", "expires_at_utc", "revoked_at_utc"):
            value = getattr(self, name)
            if value is None and name == "revoked_at_utc":
                continue
            _aware_time(value)
        if _aware_time(self.expires_at_utc) <= _aware_time(self.authorized_at_utc):
            raise ValueError("expires_at_utc must follow authorized_at_utc")
        if not isinstance(self.authorization_id, str) or not self.authorization_id.strip():
            raise ValueError("authorization_id cannot be empty")

    def revoke(
        self,
        at: datetime | None = None,
        *,
        journal: ControlJournal | None = None,
        revoked_by: str | None = None,
    ) -> "PhysicalAuthorization":
        """Return a copy carrying a revocation timestamp. The original,
        already-journalled record is untouched — revocation is a new fact,
        not an edit to history. Pass `journal` to record who revoked it."""

        if self.revoked_at_utc is not None:
            return self
        from dataclasses import replace

        when = at or datetime.now(timezone.utc)
        _aware_time(when.isoformat())
        actor = revoked_by or self.operator_id
        if actor not in {self.operator_id, self.cosigner_id}:
            raise ValueError("revocation requires one of the named signers")
        journal = journal or self._journal
        revoked = replace(self, revoked_at_utc=when.isoformat(), _journal=journal)
        if journal is not None:
            journal.append_safety_event(
                "physical_authorization_revoked",
                revoked_by or self.operator_id,
                when.isoformat(),
                {"operator_id": self.operator_id, "cosigner_id": self.cosigner_id,
                 "authorization_id": self.authorization_id},
            )
        self._revoked_ids.add(self.authorization_id)
        return revoked

    def active(self, at: datetime | None = None, *, journal: ControlJournal | None = None) -> bool:
        if self.revoked_at_utc is not None or self.authorization_id in self._revoked_ids:
            return False
        audit = journal or self._journal
        if audit is not None and audit.is_authorization_revoked(self.authorization_id):
            return False
        now = at or datetime.now(timezone.utc)
        _aware_time(now.isoformat())
        return _aware_time(self.authorized_at_utc) <= now < _aware_time(self.expires_at_utc)


def _aware_time(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError, AttributeError) as exc:
        raise ValueError("authorization timestamps must be ISO-8601") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("authorization timestamps must include a timezone")
    return parsed


def authorize_physical_action(
    operator_id: str,
    cosigner_id: str,
    *,
    ttl_s: float = 300.0,
    journal: ControlJournal | None = None,
) -> PhysicalAuthorization:
    """Construct a `PhysicalAuthorization` and, if a journal is given, record
    its creation — this is the one entry point that should be used to grant
    two-person authorization, so creation is auditable the same way
    revocation is."""

    if not math.isfinite(ttl_s) or ttl_s <= 0:
        raise ValueError("ttl_s must be positive and finite")
    now = datetime.now(timezone.utc)
    authorization = PhysicalAuthorization(
        operator_id=operator_id,
        cosigner_id=cosigner_id,
        authorized_at_utc=now.isoformat(),
        expires_at_utc=(now + timedelta(seconds=ttl_s)).isoformat(),
        _journal=journal,
    )
    if journal is not None:
        journal.append_safety_event(
            "physical_authorization_created",
            operator_id,
            now.isoformat(),
            {"operator_id": operator_id, "cosigner_id": cosigner_id, "ttl_s": ttl_s,
             "authorization_id": authorization.authorization_id},
        )
    return authorization


class PhysicalAdapter(Protocol):
    """Future vendor-adapter shape, with no implementation in this project.

    CERTIFIED is a caller-supplied diagnostic flag, not verified certification.
    It cannot enable actuation: the executor still has no command path.
    """

    CERTIFIED: bool

    def connect(self) -> None: ...
    def disconnect(self) -> None: ...
    def health_check(self) -> dict: ...
    def send_command(self, intent: ControlIntent, timeout_s: float) -> dict:
        """Return {"acked": bool, "detail": str, ...vendor telemetry}."""
        ...


PHYSICAL_ACTION_GATES: dict[ControlAction, dict[str, bool]] = {
    # HALT is the abort action. Requiring a released interlock or a
    # dual-person authorization to issue a STOP is the well-known failure
    # mode of safety systems that make the emergency action as hard to reach
    # as the risky one — so HALT only ever requires a certified adapter.
    ControlAction.HALT: {"requires_interlock_released": False, "requires_authorization": False},
}
"""HALT is the only ControlAction this software may ever gate physically —
see REALTIME_TIER_ACTIONS below for why WAIT/PURIFY/SWAP are excluded
entirely, not merely gated harder.

This tiering is a starting position, not a finding: it belongs to the
"formal hazard analysis" item in ROADMAP.md and must be reviewed (and can be
overridden) by whoever actually performs that analysis for the target
hardware — a repeater's abort action may have consequences a generic
pattern-matched "HALT is always safe" assumption does not capture.

OPEN QUESTION for that analysis, noted rather than resolved here: with
WAIT/PURIFY/SWAP excluded, HALT is the only action `HardwareInterlock` and
`PhysicalAuthorization` can ever protect — and HALT bypasses both by design.
As it stands, the interlock and dual-auth machinery built in this file
currently guards nothing. Two ways to resolve it, both requiring a human
safety decision, not a default from this codebase: (a) split HALT into a
true emergency-stop (bypasses everything, as now) versus an administrative
"take this segment out of service" action that does require the full chain,
or (b) apply the full chain to a not-yet-modeled Tier-1 action such as
pushing a new calibrated parameter set (see ROADMAP.md item 4).
"""

REALTIME_TIER_ACTIONS = frozenset({ControlAction.WAIT, ControlAction.PURIFY, ControlAction.SWAP})
"""WAIT/PURIFY/SWAP manipulate or hold a physical entangled pair on a
timescale set by memory coherence (T1/T2, tens of µs in this project's own
config.yaml) — far below the latency this software stack can offer even in
principle (dataclass validation, SQLite, optionally a neural-net forward
pass). `PhysicalExecutor.execute()` refuses these categorically, before even
checking adapter certification: no adapter, no interlock state and no
authorization can make this software fast enough to own that decision. On
real hardware these actions belong to a dedicated real-time tier (FPGA or
equivalent) driven by parameters this software already validated and pushed
ahead of time — see ROADMAP.md item 4 for the two-tier design this implies.
"""


class PhysicalExecutor:
    """Routes ControlMode.PHYSICAL for the one ControlAction (HALT) this
    prototype considers for future gating — WAIT/PURIFY/SWAP are refused outright, see
    REALTIME_TIER_ACTIONS. Even for HALT, refuses unless every gate
    `action_gates` names for it is satisfied. If those checks pass, it raises
    NotImplementedError; no adapter command is sent. command_timeout_s is
    reserved configuration, not an implemented hardware timeout.

    `action_gates` defaults to the module-level `PHYSICAL_ACTION_GATES` but is
    accepted as a constructor override — the open question left in that
    table's docstring (should HALT require the full chain in some
    deployments?) is a hazard-analysis decision, not a code change, so it is
    exposed here rather than hard-coded."""

    def __init__(
        self,
        adapter: PhysicalAdapter | None = None,
        interlock: HardwareInterlock | None = None,
        command_timeout_s: float = 2.0,
        action_gates: dict[ControlAction, dict[str, bool]] | None = None,
    ) -> None:
        self.adapter = adapter
        self.interlock = interlock or HardwareInterlock()
        self.command_timeout_s = command_timeout_s
        self.action_gates = dict(action_gates) if action_gates is not None else dict(PHYSICAL_ACTION_GATES)

    def execute(
        self,
        intent: ControlIntent,
        snapshot: NetworkStateSnapshot,
        *,
        mode: ControlMode,
        authorization: PhysicalAuthorization | None = None,
    ) -> ControlExecutionResult:
        if mode is not ControlMode.PHYSICAL:
            raise ValueError("PhysicalExecutor accepts ControlMode.PHYSICAL only")

        refusal = self._refusal_reason(intent.action, authorization)
        if refusal is not None:
            gates = self.action_gates.get(intent.action, {"requires_interlock_released": True, "requires_authorization": True})
            return ControlExecutionResult(
                status="PHYSICAL_REFUSED",
                action=intent.action,
                performed_in_simulator=False,
                physical_action_performed=False,
                details={
                    "reason_code": refusal,
                    "node_id": snapshot.node_id,
                    "interlock_state": self.interlock.state.value,
                    "adapter_certified": bool(self.adapter and self.adapter.CERTIFIED),
                    "action_gates": gates,
                    "realtime_tier_action": intent.action in REALTIME_TIER_ACTIONS,
                },
            )

        # Every gate this action requires is satisfied. There is still no
        # certified adapter in this repository, so this branch is
        # intentionally unreachable today.
        raise NotImplementedError(
            "no PhysicalAdapter is registered — implement PhysicalAdapter, set "
            "CERTIFIED=True only after bench validation, and pass it to "
            "PhysicalExecutor(adapter=...)"
        )

    def _refusal_reason(
        self, action: ControlAction, authorization: PhysicalAuthorization | None,
    ) -> str | None:
        if action in REALTIME_TIER_ACTIONS:
            return "action_belongs_to_realtime_tier_not_this_software"
        if self.adapter is None or not getattr(self.adapter, "CERTIFIED", False):
            return "physical_adapter_not_certified"
        gates = self.action_gates.get(
            action, {"requires_interlock_released": True, "requires_authorization": True}
        )
        if gates["requires_interlock_released"] and self.interlock.state is not InterlockState.RELEASED:
            return "hardware_interlock_engaged"
        if gates["requires_authorization"]:
            if authorization is None:
                return "physical_authorization_missing"
            if authorization.revoked_at_utc is not None:
                return "physical_authorization_revoked"
            if not authorization.active(journal=self.interlock.journal):
                return "physical_authorization_expired"
        return None

    def node_left_in_safe_state(self, snapshot: NetworkStateSnapshot) -> bool:
        """A revocation or interlock lapse is only complete once every node it
        covered is back in a safe memory state. An adapter's `disconnect()`
        should not be trusted alone to guarantee this — call this after, and
        treat a False as \"still needs draining\", not as noise."""

        return snapshot.memory_state in SAFE_MEMORY_STATES


__all__ = [
    "HardwareInterlock",
    "InterlockState",
    "PHYSICAL_ACTION_GATES",
    "PhysicalAdapter",
    "PhysicalAuthorization",
    "PhysicalExecutor",
    "REALTIME_TIER_ACTIONS",
    "SAFE_MEMORY_STATES",
    "authorize_physical_action",
]
