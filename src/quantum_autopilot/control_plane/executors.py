"""Non-physical executors for shadow and simulated control cycles."""

from __future__ import annotations

import hashlib
import math
from typing import Protocol

from quantum_autopilot.control_plane.contracts import (
    ControlAction,
    ControlExecutionResult,
    ControlIntent,
    ControlMode,
    NetworkStateSnapshot,
)


class SandboxExecutor(Protocol):
    def execute(
        self,
        intent: ControlIntent,
        snapshot: NetworkStateSnapshot,
        *,
        mode: ControlMode,
    ) -> ControlExecutionResult: ...


class DryRunExecutor:
    """Record an action without changing even simulated network state."""

    def execute(
        self,
        intent: ControlIntent,
        snapshot: NetworkStateSnapshot,
        *,
        mode: ControlMode,
    ) -> ControlExecutionResult:
        return ControlExecutionResult(
            status=(
                "SHADOW_RECORDED"
                if mode is ControlMode.SHADOW
                else "ASSURANCE_RECORDED"
            ),
            action=intent.action,
            performed_in_simulator=False,
            physical_action_performed=False,
            details={
                "node_id": snapshot.node_id,
                "state_version": snapshot.state_version,
                "would_execute": intent.action.value,
                "mode": mode.value,
            },
        )


class SimulatedQuantumExecutor:
    """Execute a guarded intent only against the existing Qiskit-backed twin."""

    @staticmethod
    def _deterministic_draw(request_id: str) -> float:
        digest = hashlib.sha256(request_id.encode("utf-8")).digest()
        return int.from_bytes(digest[:8], "big") / float(2**64)

    def execute(
        self,
        intent: ControlIntent,
        snapshot: NetworkStateSnapshot,
        *,
        mode: ControlMode,
    ) -> ControlExecutionResult:
        if mode is not ControlMode.SIMULATION:
            raise ValueError("SimulatedQuantumExecutor accepts simulation mode only")

        details: dict[str, object]
        if intent.action is ControlAction.HALT:
            details = {
                "released_simulated_pairs": len(snapshot.available_pair_fidelities),
                "resulting_memory_state": "IDLE",
                "resulting_pair_fidelities": [],
                "resulting_memory_ages_s": [],
            }
        elif intent.action is ControlAction.WAIT:
            raw_wait_time = snapshot.metadata.get("wait_time_s", 1e-6)
            if isinstance(raw_wait_time, bool) or not isinstance(
                raw_wait_time, (int, float)
            ):
                raise ValueError("metadata.wait_time_s must be numeric")
            wait_time_s = float(raw_wait_time)
            if not math.isfinite(wait_time_s) or wait_time_s < 0.0:
                raise ValueError("metadata.wait_time_s must be finite and non-negative")
            details = {
                "wait_time_s": wait_time_s,
                "resulting_pair_fidelities": list(snapshot.available_pair_fidelities),
                "resulting_memory_ages_s": [
                    age + wait_time_s for age in snapshot.memory_ages_s
                ],
                "resulting_memory_state": "STORING",
            }
        elif intent.action is ControlAction.PURIFY:
            from quantum_twin.simulation.network import QuantumRepeaterNode

            node = QuantumRepeaterNode(seed=7)
            details = node.run_purification_event(
                fidelity_pair_1=snapshot.available_pair_fidelities[0],
                fidelity_pair_2=snapshot.available_pair_fidelities[1],
                memory_age_1_s=snapshot.memory_ages_s[0],
                memory_age_2_s=snapshot.memory_ages_s[1],
                random_value=self._deterministic_draw(snapshot.request_id),
                pair_1_id=f"{snapshot.node_id}-pair-0",
                pair_2_id=f"{snapshot.node_id}-pair-1",
            )
            if details["sampled_success"]:
                details["resulting_memory_state"] = "STORING"
                details["resulting_pair_fidelities"] = [details["F_after"]]
                details["resulting_memory_ages_s"] = [0.0]
            else:
                details["resulting_memory_state"] = "IDLE"
                details["resulting_pair_fidelities"] = []
                details["resulting_memory_ages_s"] = []
        elif intent.action is ControlAction.SWAP:
            from quantum_twin.quantum.swapping import WernerStateSwapping

            details = WernerStateSwapping().swap(
                {"F_t": snapshot.available_pair_fidelities[0], "success": True},
                {"F_t": snapshot.available_pair_fidelities[1], "success": True},
            )
            details["resulting_memory_state"] = "IDLE"
            details["resulting_pair_fidelities"] = []
            details["resulting_memory_ages_s"] = []
            details["input_pair_ids"] = [
                f"{snapshot.node_id}-pair-0",
                f"{snapshot.node_id}-pair-1",
            ]
        else:  # pragma: no cover - ControlAction validates this boundary
            raise ValueError(f"unsupported simulated action: {intent.action.value}")

        details.update(
            {
                "backend": "quantum_twin_simulator",
                "node_id": snapshot.node_id,
                "input_state_version": snapshot.state_version,
            }
        )
        return ControlExecutionResult(
            status="SIMULATED",
            action=intent.action,
            performed_in_simulator=True,
            physical_action_performed=False,
            details=details,
        )


__all__ = ["DryRunExecutor", "SandboxExecutor", "SimulatedQuantumExecutor"]
