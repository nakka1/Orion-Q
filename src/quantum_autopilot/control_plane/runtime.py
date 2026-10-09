"""Single-cycle runtime for the Quantum Network Control Plane Sandbox."""

from __future__ import annotations

import hashlib
import json
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from typing import Any, Mapping
from uuid import uuid4

from quantum_autopilot.control_plane.contracts import (
    ControlCycleRecord,
    ControlIntent,
    ControlMode,
    NetworkStateSnapshot,
)
from quantum_autopilot.control_plane.executors import (
    DryRunExecutor,
    SimulatedQuantumExecutor,
)
from quantum_autopilot.control_plane.journal import ControlJournal
from quantum_autopilot.control_plane.physical import HardwareInterlock, PhysicalAuthorization, PhysicalExecutor
from quantum_autopilot.control_plane.policy import RepeaterSandboxPolicy
from quantum_autopilot.control_plane.safety import SafetySupervisor


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _request_hash(mode: ControlMode, snapshot: NetworkStateSnapshot) -> str:
    serialized = json.dumps(
        {"mode": mode.value, "snapshot": snapshot.to_dict()},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


class ControllerRuntime:
    """Run idempotent, fail-closed cycles. PHYSICAL mode routes to
    `physical_executor`, which — absent a certified adapter — always refuses
    before the safety supervisor even runs (the supervisor also refuses
    PHYSICAL unconditionally; see safety.py). Two independent refusals for
    the same mode is intentional, not redundant."""

    def __init__(
        self,
        journal_path: str | Path,
        *,
        policy: Any | None = None,
        supervisor: SafetySupervisor | None = None,
        dry_run_executor: Any | None = None,
        simulated_executor: Any | None = None,
        physical_executor: PhysicalExecutor | None = None,
    ) -> None:
        self.journal = ControlJournal(journal_path)
        self.policy = policy or RepeaterSandboxPolicy()
        self.supervisor = supervisor or SafetySupervisor()
        self.dry_run_executor = dry_run_executor or DryRunExecutor()
        self.simulated_executor = simulated_executor or SimulatedQuantumExecutor()
        self.physical_executor = physical_executor or PhysicalExecutor(
            interlock=HardwareInterlock(journal=self.journal)
        )
        self._cycle_lock = RLock()
        self.instance_id = str(uuid4())
        self._policy_activation_marker = None
        self._policy_activation_fault = None
        self._policy_activation_recovery = None
        self._policy_recovery_selection = None
        # Short selection updates only; never held during SQLite work.
        self._policy_recovery_lock = RLock()

    @contextmanager
    def scenario_transaction(self, topology_path: str | Path):
        """Serialize simulated scenarios with ordinary cycles before SQLite.

        Stores sharing this journal commit together. Separate databases keep
        their existing behavior; there is no cross-database commit guarantee.
        """
        from quantum_autopilot.control_plane.sqlite_transaction import transaction
        with self._cycle_lock:
            if Path(topology_path).resolve() == self.journal.path:
                with self.journal._lock, transaction(self.journal.path):
                    yield
            else:
                yield

    def cycle(
        self,
        snapshot: NetworkStateSnapshot | Mapping[str, Any],
        *,
        mode: ControlMode | str = ControlMode.SIMULATION,
        physical_authorization: PhysicalAuthorization | None = None,
    ) -> dict[str, Any]:
        if isinstance(snapshot, Mapping):
            snapshot = NetworkStateSnapshot.from_dict(snapshot)
        if not isinstance(snapshot, NetworkStateSnapshot):
            raise ValueError("snapshot must be a NetworkStateSnapshot or mapping")
        mode = ControlMode(mode)
        request_sha256 = _request_hash(mode, snapshot)

        with self._cycle_lock:
            activation = None
            if self._policy_activation_marker is not None or self._policy_activation_fault is not None:
                from .policy_activation import ensure_activation_consistent
                if mode is not ControlMode.SIMULATION:
                    raise ValueError('activated policy is restricted to simulation mode')
                activation = ensure_activation_consistent(self)
            existing = self.journal.get_by_request_id(snapshot.request_id)
            if existing is not None:
                if existing["request_sha256"] != request_sha256:
                    raise ValueError(
                        "request_id was already used with a different mode or payload"
                    )
                return {
                    "record": existing["record"],
                    "record_sha256": existing["record_sha256"],
                    "idempotent_replay": True,
                }

            started_at = _utc_now()
            action, policy_details = self.policy.decide(snapshot)
            if activation is not None:
                from .topology import _sha256
                policy_details = dict(policy_details, policy_activation={
                    'activation_record_sha256': _sha256(activation),
                    'activation_id': activation['activation_id'], 'revision': activation['revision'],
                    'active_policy_sha256': activation['active_policy_sha256'], 'runtime_instance_id': self.instance_id})
            intent = ControlIntent(
                intent_id=str(uuid4()),
                request_id=snapshot.request_id,
                node_id=snapshot.node_id,
                action=action,
                expected_state_version=snapshot.state_version,
                issued_at_utc=started_at,
                policy_name=self.policy.name,
                policy_details=policy_details,
            )
            safety = self.supervisor.assess(mode, intent, snapshot)
            effective_intent = replace(intent, action=safety.effective_action)

            if mode is ControlMode.SIMULATION:
                executor = self.simulated_executor
                execution = executor.execute(effective_intent, snapshot, mode=mode)
            elif mode is ControlMode.PHYSICAL:
                execution = self.physical_executor.execute(
                    effective_intent, snapshot, mode=mode,
                    authorization=physical_authorization,
                )
            else:
                execution = self.dry_run_executor.execute(effective_intent, snapshot, mode=mode)

            record = ControlCycleRecord(
                cycle_id=str(uuid4()),
                mode=mode,
                started_at_utc=started_at,
                completed_at_utc=_utc_now(),
                request_sha256=request_sha256,
                snapshot=snapshot,
                intent=intent,
                safety=safety,
                execution=execution,
            )
            record_hash = self.journal.append(record)
            return {
                "record": record.to_dict(),
                "record_sha256": record_hash,
                "idempotent_replay": False,
            }

    def observe_optional_sync(self, snapshot, *, model_id=None, archive_path=None):
        """Explicit shadow sidecar; never runs a cycle or writes a command record."""
        from .optional_sync import evaluate_optional_sync

        return evaluate_optional_sync(snapshot, model_id=model_id, archive_path=archive_path)

    def observe_optional_compensation(self, snapshot, *, model_id=None, archive_path=None):
        """Read-only polarisation sidecar; no policy, actuator or command journal."""
        from .optional_compensation import evaluate_optional_compensation

        return evaluate_optional_compensation(snapshot, model_id=model_id, archive_path=archive_path)

    def observe_optional_allocation(self, snapshot, *, model_id=None, archive_path=None):
        """Read-only allocation sidecar; never mutates queues or invokes control."""
        from .optional_allocation import evaluate_optional_allocation

        return evaluate_optional_allocation(snapshot, model_id=model_id, archive_path=archive_path)

    def observe_optional_routing(self, snapshot, *, model_id=None, archive_path=None):
        """Read-only routing sidecar; no policy, executor or resource consumption."""
        from .optional_routing import evaluate_optional_routing

        return evaluate_optional_routing(snapshot, model_id=model_id, archive_path=archive_path)

    def observe_optional_keyrate(self, snapshot, *, model_id=None, archive_path=None):
        """Read-only decoy-state actor sidecar; no settings, policy or command changes."""
        from .optional_keyrate import evaluate_optional_keyrate

        return evaluate_optional_keyrate(snapshot, model_id=model_id, archive_path=archive_path)

    def status(self) -> dict[str, Any]:
        return {
            "name": "Quantum Network Control Plane Sandbox",
            "available_modes": [item.value for item in ControlMode if item is not ControlMode.PHYSICAL],
            "recognized_but_refused_modes": [ControlMode.PHYSICAL.value],
            "available_actions": ["HALT", "WAIT", "PURIFY", "SWAP"],
            "physical_mode_available": False,
            "physical_executor_available": False,
            "physical_refusal_scaffold_present": True,
            "physical_executor_certified": bool(
                self.physical_executor.adapter
                and getattr(self.physical_executor.adapter, "CERTIFIED", False)
            ),
            "physical_interlock_state": self.physical_executor.interlock.state.value,
            "recent_safety_events": self.journal.list_recent_safety_events(limit=20),
            "fail_closed": True,
            "journal_backend": "sqlite",
            "execution_scope": "simulator_or_dry_run_only",
        }


__all__ = ["ControllerRuntime"]
