"""Adapter from the existing repeater policy to sandbox control intents."""

from __future__ import annotations

from typing import Any, Mapping

from quantum_autopilot.control_plane.contracts import ControlAction, NetworkStateSnapshot


class RepeaterSandboxPolicy:
    """Reuse the original HALT/WAIT/PURIFY/SWAP utility policy unchanged."""

    name = "quantum_twin.control.PolicyEngine"

    def __init__(self, threshold: float = 0.65) -> None:
        from quantum_twin.control.policy import PolicyEngine

        self.engine = PolicyEngine(threshold=threshold)

    def decide(self, snapshot: NetworkStateSnapshot) -> tuple[ControlAction, dict[str, Any]]:
        from quantum_twin.control.policy import RepeaterState

        partner = (
            {"partner_pair_fidelity": snapshot.available_pair_fidelities[1]}
            if len(snapshot.available_pair_fidelities) >= 2
            else None
        )
        state = RepeaterState(
            telemetry=dict(snapshot.telemetry),
            predicted_fidelity=snapshot.predicted_fidelity,
            uncertainty=snapshot.prediction_uncertainty,
            memory_state=snapshot.memory_state,
            repeater_state=partner,
        )
        decision = self.engine.decide(state)
        action = ControlAction(decision["action"].value)
        utilities = {
            candidate.value: float(value)
            for candidate, value in decision["utilities"].items()
        }
        inference = snapshot.metadata.get("wdm_model_inference")
        model_driven = isinstance(inference, Mapping)
        return action, {
            "utilities": utilities,
            "prediction_source": (
                "wdm_model_inference_adapter"
                if model_driven
                else "caller_supplied_sandbox_state"
            ),
            "prediction_model": (
                {
                    "model_name": inference.get("model_name"),
                    "model_version": inference.get("model_version"),
                    "output_kind": inference.get("output_kind"),
                    "window_sha256": inference.get("window_sha256"),
                }
                if model_driven
                else None
            ),
            "physical_calibration": False,
        }


__all__ = ["RepeaterSandboxPolicy"]
