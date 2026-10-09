"""Bounded, seeded, discrete-time two-link acquisition and density-matrix storage."""
import math
from dataclasses import dataclass, fields
from importlib.metadata import version

from .workspace_path import WorkspacePathRequest, selected_path


@dataclass(frozen=True)
class TemporalPathRequest(WorkspacePathRequest):
    protocol: str = "memory_single_swap_v1"
    success_probability_left: float = .5
    success_probability_right: float = .5
    attempt_interval_s: float = .000001
    max_attempts: int = 100
    memory_t1_s: float = .00005
    memory_t2_s: float = .00003
    memory_ttl_steps: int = 20
    seed: int = 7

    def __post_init__(self):
        base = {field.name: getattr(self, field.name) for field in fields(WorkspacePathRequest)}
        base["protocol"] = "werner_single_swap_v1"
        WorkspacePathRequest(**base)
        if self.protocol != "memory_single_swap_v1":
            raise ValueError("unsupported temporal protocol")
        for name in ("success_probability_left", "success_probability_right"):
            value = getattr(self, name)
            if type(value) not in (int, float) or not 0 <= value <= 1 or not math.isfinite(value):
                raise ValueError(f"{name} must be finite in [0, 1]")
        for name, maximum in (("attempt_interval_s", 1), ("memory_t1_s", 1000), ("memory_t2_s", 1000)):
            value = getattr(self, name)
            if type(value) not in (int, float) or not 1e-9 <= value <= maximum or not math.isfinite(value):
                raise ValueError(f"{name} must be finite in [1e-9, {maximum}]")
        if self.memory_t2_s > 2 * self.memory_t1_s:
            raise ValueError("T2 must be <= 2*T1")
        for name, low, high in (("max_attempts", 1, 1000), ("memory_ttl_steps", 1, 1000), ("seed", 0, 2**32-1)):
            value = getattr(self, name)
            if type(value) is not int or not low <= value <= high:
                raise ValueError(f"{name} must be an integer in [{low}, {high}]")


def evaluate_temporal_path(topology, request):
    import numpy as np
    route, links = selected_path(topology, request)
    rng = np.random.Generator(np.random.PCG64(request.seed))
    born = [None, None]
    generated, expired, attempted = [0, 0], [0, 0], [0, 0]
    probabilities = [request.success_probability_left, request.success_probability_right]
    timeline = []
    for tick in range(1, request.max_attempts + 1):
        # Expiry at this tick precedes new arrivals; occupied slots do not attempt.
        removed = [False, False]
        for side in range(2):
            if born[side] is not None and tick - born[side] >= request.memory_ttl_steps:
                expired[side] += 1
                born[side], removed[side] = None, True
        draws = rng.random(2).tolist()  # Fixed draw schedule even for occupied slots.
        attempts = [value is None for value in born]
        arrivals = [False, False]
        for side in range(2):
            if attempts[side]:
                attempted[side] += 1
                if draws[side] < probabilities[side]:
                    born[side] = tick
                    generated[side] += 1
                    arrivals[side] = True
        ages = [None if value is None else (tick-value)*request.attempt_interval_s for value in born]
        timeline.append({"tick": tick, "time_s": tick*request.attempt_interval_s,
                         "draws": draws, "attempted": attempts, "arrived": arrivals,
                         "expired": removed, "pair_born_ticks": list(born), "memory_ages_s": ages})
        if all(value is not None for value in born):
            break
    available = all(value is not None for value in born)
    fidelity, met, quantum = None, None, None
    if available:
        from quantum_twin.quantum.channel import QuantumChannel
        from quantum_twin.core.config import PhysicsConfig
        from quantum_twin.quantum.swapping import WernerStateSwapping, werner_state, _IDEAL_BELL
        from qiskit.quantum_info import state_fidelity
        config = PhysicsConfig(T1=request.memory_t1_s, T2=request.memory_t2_s)
        channel = QuantumChannel(config)
        states = [werner_state(link.nominal_pair_fidelity) for link in links]
        aged = [state if age == 0 else channel.apply_decoherence_to_state(state, depol_prob=0, exposure_time=age)
                for state, age in zip(states, ages)]
        aged_fidelities = [float(state_fidelity(state, _IDEAL_BELL)) for state in aged]
        from .contracts import NetworkStateSnapshot, ControlIntent, ControlAction, ControlMode
        from .safety import SafetySupervisor
        from .topology import _utc_now
        from quantum_autopilot.core import EvidenceKind, Provenance
        now = _utc_now()
        snapshot = NetworkStateSnapshot(request.run_id, request.repeater, now, tick,
            min(aged_fidelities), 0, 0, "STORING", tuple(aged_fidelities), tuple(ages),
            integrity_verified=True, operator_interlock=True,
            provenance=Provenance(evidence_kind=EvidenceKind.SIMULATED, source="temporal_configured_path",
                timestamp=now, metadata={"evidence_lane": "bell_fidelity_control", "physical_calibration": False}),
            metadata={"prediction_semantics": "stored_state_fidelity_not_model_prediction", "virtual_time_s": tick*request.attempt_interval_s})
        intent = ControlIntent(request.run_id+":swap", request.run_id, request.repeater, ControlAction.SWAP,
            tick, now, request.protocol, {"fixed_protocol": True, "uses_wdm_model": False})
        safety = SafetySupervisor().assess(ControlMode.SIMULATION, intent, snapshot)
        if not safety.authorized:
            raise ValueError("sandbox supervisor refused temporal swap")
        swap = WernerStateSwapping().swap_states(*aged)
        fidelity = swap["F_t"]
        if not math.isfinite(fidelity) or not 0 <= fidelity <= 1 or swap["success"] is not True:
            raise ValueError("invalid temporal quantum result")
        met = fidelity >= request.target_fidelity
        quantum = {"backend": "QuantumChannel + WernerStateSwapping.swap_states",
                   "input_fidelities": [link.nominal_pair_fidelity for link in links],
                   "aged_pair_fidelities": aged_fidelities,
                   "aged_density_matrices": [{"real": state.data.real.tolist(), "imag": state.data.imag.tolist()} for state in aged],
                   "memory_ages_s": ages, "swap": swap, "consumed_pairs": 2,
                   "density_matrices_preserved": True, "safety": safety.to_dict(),
                   "snapshot": snapshot.to_dict(), "intent": intent.to_dict(), "physical_action_performed": False}
    return {"route": list(route), "links": [link.to_dict() for link in links],
            "total_distance_km": sum(link.distance_km for link in links),
            "conditional_fidelity": fidelity, "target_fidelity": request.target_fidelity,
            "conditional_target_met": met, "pairs_available": available,
            "outcome": ("target_met" if met else "target_not_met") if available else "acquisition_timeout",
            "elapsed_s": tick*request.attempt_interval_s, "ticks": tick, "timeline": timeline,
            "memory_ages_s": ages, "quantum_execution": quantum,
            "accounting": {"attempts_per_link": attempted, "generated_per_link": generated,
                "expired_per_link": expired, "consumed_per_link": [1, 1] if available else [0, 0],
                "discarded_at_end_per_link": [0, 0] if available else [int(value is not None) for value in born],
                "final_occupied_slots": 0},
            "implementation": {"protocol": request.protocol, "rng": "numpy.PCG64",
                "numpy_version": version("numpy"), "qiskit_version": version("qiskit"), "qiskit_aer_version": version("qiskit-aer")},
            "assumptions": ["Independent Bernoulli acquisition; input probabilities include unspecified generation/loss efficiencies",
                "Simultaneous discrete attempts; no trials on occupied slots; fixed two-draw PCG64 schedule",
                "One slot per link, expire before acquisition when age_ticks >= memory_ttl_steps",
                "Nominal Werner states at successful arrival; equal T1/T2 noise on BOTH qubits of each stored pair",
                "Existing thermal relaxation and pure dephasing channel, zero added depolarization; no Werner re-projection",
                "Ideal instantaneous BSM/Pauli corrections; stop at first swap even if target is missed",
                "Distance not converted to loss/delay; no WDM inference, physical action or network service-rate estimate",
                "Isolated temporal memory; workspace memory unchanged; leftover pairs discarded on timeout"]}
