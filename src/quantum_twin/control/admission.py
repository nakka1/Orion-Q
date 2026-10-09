"""Predictive admission control connected to input-dependent purification.

Measured wall-clock inference time is a benchmark result only. Physical
memory ageing uses configured latencies, and controller comparisons can share
one pre-sampled random variate per arrival (common random numbers).
"""

from __future__ import annotations

import time
from typing import Iterable

import numpy as np
import pandas as pd
import torch
import torch.nn as nn

from quantum_twin.simulation.network import QuantumRepeaterNode


class DigitalTwinOrchestrator:
    """Binary HALT/PURIFY controller for simulated quantum-pair arrivals."""

    def __init__(self, model: nn.Module, quantum_node: QuantumRepeaterNode,
                 threshold: float = 0.65, success_rate_cutoff: float = 0.5,
                 device: torch.device = None, base_control_latency_s: float = 0.0,
                 configured_inference_latency_s: float = 0.0,
                 purification_seed: int = None):
        self.model = model
        self.quantum_node = quantum_node
        self.threshold = float(threshold)
        # Retained for API compatibility. Usefulness now depends on a sampled
        # protocol success and F_after, not a threshold on p_success.
        self.success_rate_cutoff = float(success_rate_cutoff)
        self.device = device if device is not None else torch.device("cpu")
        self.base_control_latency_s = float(base_control_latency_s)
        self.configured_inference_latency_s = float(configured_inference_latency_s)
        self.purification_seed = quantum_node.seed if purification_seed is None else int(purification_seed)
        self.log = []

    @staticmethod
    def _to_optional_array(values: Iterable[float], total: int, name: str):
        if values is None:
            return None
        array = np.asarray(list(values), dtype=float)
        if len(array) != total:
            raise ValueError(f"{name} must contain exactly {total} values")
        return array

    def _uniforms(self, total: int, values: Iterable[float] = None) -> np.ndarray:
        supplied = self._to_optional_array(values, total, "purification_uniforms")
        uniforms = supplied if supplied is not None else np.random.default_rng(
            self.purification_seed
        ).random(total)
        if np.any((uniforms < 0.0) | (uniforms > 1.0)):
            raise ValueError("purification_uniforms must be in [0, 1]")
        return uniforms

    @staticmethod
    def _pair_available(true_fidelity: float, raw_row: dict = None) -> bool:
        if not np.isfinite(true_fidelity) or true_fidelity <= 0.0:
            return False
        if raw_row is None:
            return True
        for field in ("entanglement_success", "channel_available"):
            if field in raw_row and pd.notna(raw_row[field]):
                return bool(raw_row[field])
        return True

    def _purify(self, step: int, true_fidelity: float, second_fidelity: float,
                latency_s: float, uniform: float) -> dict:
        return self.quantum_node.run_purification_event(
            fidelity_pair_1=true_fidelity,
            fidelity_pair_2=second_fidelity,
            memory_age_1_s=latency_s,
            memory_age_2_s=latency_s,
            random_value=uniform,
            pair_1_id=f"step-{step}-pair-1",
            pair_2_id=f"step-{step}-pair-2",
        )

    def run_intelligent(self, X_test: torch.Tensor, y_test: torch.Tensor,
                        raw_test_rows=None, deployment_latency_s: float = None,
                        second_fidelities: Iterable[float] = None,
                        purification_uniforms: Iterable[float] = None) -> dict:
        """Run predictive admission with configured, reproducible physics time.

        ``deployment_latency_s`` is the configured inference contribution.
        If omitted, ``configured_inference_latency_s`` from the constructor is
        used. Measured forward latency is logged but never drives decoherence.
        """
        assert self.model is not None, "run_intelligent requer um modelo treinado."
        self.model.eval()

        total_steps = len(X_test)
        second = self._to_optional_array(second_fidelities, total_steps, "second_fidelities")
        uniforms = self._uniforms(total_steps, purification_uniforms)
        inference_latency = (
            self.configured_inference_latency_s
            if deployment_latency_s is None else float(deployment_latency_s)
        )
        physical_latency = self.base_control_latency_s + inference_latency
        if physical_latency < 0.0:
            raise ValueError("configured physical latency cannot be negative")

        results = []
        useful_pairs = halted = no_pair = attempted = protocol_successes = qpu_operations = 0
        total_forward_latency = 0.0

        with torch.no_grad():
            for i in range(total_steps):
                x_sample = X_test[i:i + 1]
                true_fidelity = float(y_test[i].item())
                raw_row = None
                if raw_test_rows is not None:
                    raw_row = raw_test_rows.iloc[i].to_dict()
                    self.quantum_node.update_telemetry(raw_row)

                if self.device.type == "cuda":
                    torch.cuda.synchronize()
                t0 = time.perf_counter()
                pred_tensor = self.model(x_sample)
                if self.device.type == "cuda":
                    torch.cuda.synchronize()
                measured_latency = time.perf_counter() - t0
                total_forward_latency += measured_latency
                pred_fidelity = float(pred_tensor.item())

                if not self._pair_available(true_fidelity, raw_row):
                    no_pair += 1
                    results.append({
                        "step": i, "action": "NO_PAIR", "pred_fidelity": pred_fidelity,
                        "true_fidelity": None, "latency_s": physical_latency,
                        "measured_inference_latency_s": measured_latency,
                    })
                    continue

                self.quantum_node.store_pair(physical_latency)
                if pred_fidelity < self.threshold:
                    halted += 1
                    self.quantum_node.record_purification_result(False, False, True)
                    results.append({
                        "step": i, "action": "HALT_PURIFICATION",
                        "pred_fidelity": pred_fidelity, "true_fidelity": true_fidelity,
                        "latency_s": physical_latency,
                        "measured_inference_latency_s": measured_latency,
                    })
                    continue

                attempted += 1
                f2 = true_fidelity if second is None else float(second[i])
                event = self._purify(i, true_fidelity, f2, physical_latency, uniforms[i])
                is_useful = bool(
                    event["sampled_success"] and event["F_after"] is not None
                    and event["F_after"] >= self.threshold
                )
                protocol_successes += int(event["sampled_success"])
                useful_pairs += int(is_useful)
                qpu_operations += event["qpu_operations"]
                self.quantum_node.record_purification_result(True, is_useful, False)
                results.append({
                    "step": i, "action": "PURIFY", "pred_fidelity": pred_fidelity,
                    "true_fidelity": true_fidelity, "latency_s": physical_latency,
                    "measured_inference_latency_s": measured_latency,
                    "purification_success_rate": event["success_probability"],
                    "useful": is_useful, **event,
                })

        self.log = results
        return {
            "mode": "intelligent", "total_steps": total_steps,
            "pair_arrivals": total_steps - no_pair, "no_pair": no_pair,
            "useful_pairs": useful_pairs, "halted": halted, "attempted": attempted,
            "purification_successes": protocol_successes,
            "qpu_operations": qpu_operations,
            "avg_classical_latency_s": physical_latency,
            "avg_measured_inference_latency_s": total_forward_latency / max(total_steps, 1),
            "configured_deployment_latency_s": inference_latency,
            "base_control_latency_s": self.base_control_latency_s,
        }

    def run_blind_baseline(self, X_test: torch.Tensor, y_test: torch.Tensor,
                           raw_test_rows=None, control_latency_s: float = None,
                           second_fidelities: Iterable[float] = None,
                           purification_uniforms: Iterable[float] = None) -> dict:
        """Unconditional admission using the same pair physics and random draws."""
        total_steps = len(X_test)
        latency = self.base_control_latency_s if control_latency_s is None else float(control_latency_s)
        if latency < 0.0:
            raise ValueError("configured physical latency cannot be negative")
        second = self._to_optional_array(second_fidelities, total_steps, "second_fidelities")
        uniforms = self._uniforms(total_steps, purification_uniforms)

        results = []
        useful_pairs = no_pair = attempted = protocol_successes = qpu_operations = 0
        for i in range(total_steps):
            true_fidelity = float(y_test[i].item())
            raw_row = None
            if raw_test_rows is not None:
                raw_row = raw_test_rows.iloc[i].to_dict()
                self.quantum_node.update_telemetry(raw_row)
            if not self._pair_available(true_fidelity, raw_row):
                no_pair += 1
                results.append({"step": i, "action": "NO_PAIR", "true_fidelity": None,
                                "latency_s": latency})
                continue

            self.quantum_node.store_pair(latency)
            attempted += 1
            f2 = true_fidelity if second is None else float(second[i])
            event = self._purify(i, true_fidelity, f2, latency, uniforms[i])
            is_useful = bool(
                event["sampled_success"] and event["F_after"] is not None
                and event["F_after"] >= self.threshold
            )
            protocol_successes += int(event["sampled_success"])
            useful_pairs += int(is_useful)
            qpu_operations += event["qpu_operations"]
            self.quantum_node.record_purification_result(True, is_useful, False)
            results.append({
                "step": i, "action": "PURIFY_BLIND", "true_fidelity": true_fidelity,
                "latency_s": latency, "purification_success_rate": event["success_probability"],
                "useful": is_useful, **event,
            })

        self.log = results
        return {
            "mode": "blind", "total_steps": total_steps,
            "pair_arrivals": total_steps - no_pair, "no_pair": no_pair,
            "useful_pairs": useful_pairs, "halted": 0, "attempted": attempted,
            "purification_successes": protocol_successes,
            "qpu_operations": qpu_operations,
            "avg_classical_latency_s": latency,
            "base_control_latency_s": latency,
        }
__all__ = ["DigitalTwinOrchestrator"]
