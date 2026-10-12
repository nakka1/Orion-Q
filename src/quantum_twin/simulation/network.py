"""
quantum_twin/simulation/network.py
======================================

REAL implementation of the network topology primitives (nodes, links,
repeaters, and abstract interfaces for entanglement swapping/Bell-state
measurement/purification) -- migrated here from root-level
network_topology.py in the ninety-first addendum (TCC item 6), the
seventh module migrated after the eighty-fifth addendum's lazy-loading
fix. Also contains the REAL implementation of the gate-level BBPSSW/
QuantumRepeaterNode dataplane -- migrated from root-level repeater.py
in the one-hundred-fourth addendum, the nineteenth module migrated.

Root-level network_topology.py and repeater.py are both now thin
re-exports (direction reversed). network_topology.py is kept so its
four pre-existing consumers (causal_chain.py,
run_demo_causal_swapping.py, tests/test_causal_v3.py,
entanglement_swapping.py) continue to work completely UNCHANGED. A real
gap was found and fixed during THAT migration: the PRIOR shim only
re-exported three of this module's six real classes
(QuantumNode/NetworkLink/Repeater), silently omitting
EntanglementSwappingProtocol -- which entanglement_swapping.py actually
imports directly from the ROOT module (not through the shim). The new
root re-export includes all classes actually consumed, not just the
subset the old, already-incomplete shim happened to have covered.

repeater.py is kept so its twenty-six pre-existing consumers continue
to work completely UNCHANGED, including several function-level
(deferred) imports across tests/test_quantum_runtime_profiler.py,
tests/test_physics_validation.py, tests/test_repeater_and_orchestrator.py,
and this project's own tests/test_quantum_twin_lazy_loading.py. Three
sibling shims that also imported QuantumRepeaterNode directly from the
root module (quantum_twin/control/policies.py,
quantum_twin/control/admission.py, and this file's own prior shim form)
were updated to import from THIS new location instead.

Original network_topology.py docstring, preserved from the migrated source:

Modular Node -> Channel -> Repeater -> Channel -> Node structure, per the
roadmap requirement: "Estruturar o canal de forma modular ... Evitar
implementar o código de forma monolítica. Cada enlace deve poder possuir
seus próprios T1, T2, perda, distância, BER, ruído."

Also defines lightweight ABSTRACT INTERFACES for capabilities not yet fully
implemented (entanglement swapping, Bell-state measurement, purification),
so the architecture can be EXTENDED later without restructuring:
"Não é obrigatório implementar tudo nessa etapa, mas o código deve permitir
essa extensão."

Original repeater.py docstring, preserved from the migrated source
(Portuguese):

QuantumRepeaterNode expandido: preserva integralmente o núcleo já validado
(circuito de purificação BBPSSW via Qiskit Aer, modelo de ruído NISQ com
despolarização + T1/T2, relógio lógico de latência via canal de relaxamento
térmico) e adiciona estado interno de "Gêmeo Digital":

    - qualidade atual do enlace (última telemetria física recebida)
    - estado das memórias quânticas (IDLE / STORING)
    - tempo de armazenamento acumulado
    - histórico de fidelidade
    - estatísticas de purificação (tentativas, sucessos, abortos)

Novo ciclo de simulação (orquestrado por orchestrator.py):
    1. Criar par de Bell           -> QuantumNetworkDataset (offline, camada física)
    2. Transmitir pelo canal       -> QuantumNoiseChannel (quantum_channel.py)
    3. Aplicar ruído físico        -> idem
    4. Atualizar telemetria WDM    -> update_telemetry()
    5. Estimar fidelidade          -> F_t (coluna do dataset)
    6. Enviar dados ao EdgeLSTM    -> orchestrator.py
    7. Realizar previsão futura    -> orchestrator.py
    8. Executar controle de admissão -> orchestrator.py
    9. Decidir purificar/abortar   -> record_purification_result()
   10. Registrar resultado         -> idem
"""

import time
from collections import deque

import numpy as np

from qiskit import QuantumCircuit, transpile
from qiskit_aer import AerSimulator
from qiskit_aer.noise import NoiseModel, depolarizing_error, thermal_relaxation_error

from quantum_twin.core.validation import finite_real, integer, probability, relaxation_times


class QuantumRepeaterNode:
    """
    Dataplane quântico virtual de um nó repetidor, emulado via Qiskit Aer,
    agora com estado interno expandido representando um Gêmeo Digital mais
    completo do nó físico.
    """

    def __init__(self, T1: float = 50e-6, T2: float = 30e-6,
                 depol_prob: float = 0.01, shots: int = 512, seed: int = 7,
                 history_maxlen: int = 500):
        relaxation_times(T1, T2)
        probability("depol_prob", depol_prob)
        # This gate model applies 2*p to the two-qubit depolarizing channel,
        # whose completely-positive bound is 4**2/(4**2-1), rather than 1.
        finite_real("depol_prob for the two-qubit gate model", depol_prob, maximum=8.0 / 15.0)
        integer("shots", shots, minimum=1)
        integer("history_maxlen", history_maxlen, minimum=1)
        integer("seed", seed)
        self.T1 = T1
        self.T2 = T2
        self.depol_prob = depol_prob
        self.shots = shots
        self.seed = seed
        self._rng = np.random.default_rng(seed)

        # --- Núcleo de simulação quântica (inalterado / já validado) ---
        self.base_noise_model = self._build_noise_model()
        self.simulator = AerSimulator(noise_model=self.base_noise_model, seed_simulator=seed)
        self._circuit = self.build_bbpssw_circuit()
        self._compiled_circuit = transpile(self._circuit, self.simulator)

        # --- Estado interno do Gêmeo Digital (novo) ---
        self.link_quality: float = None          # última fidelidade física observada (telemetria)
        self.last_telemetry: dict = None          # última linha de telemetria física recebida
        self.memory_state: str = "IDLE"           # IDLE | STORING
        self.storage_time_s: float = 0.0          # tempo de armazenamento acumulado do par atual
        self.fidelity_history: deque = deque(maxlen=history_maxlen)
        self.purification_stats: dict = {
            "attempted": 0, "succeeded": 0, "halted": 0,
        }

    # -----------------------------------------------------------------
    # Núcleo de simulação quântica (BBPSSW + ruído NISQ) -- inalterado
    # -----------------------------------------------------------------
    def _build_noise_model(self, extra_relax_error=None) -> NoiseModel:
        noise_model = NoiseModel()

        error_1q = depolarizing_error(self.depol_prob, 1)
        error_2q = depolarizing_error(self.depol_prob * 2, 2)

        gate_time_1q = 50e-9
        gate_time_2q = 300e-9

        thermal_1q = thermal_relaxation_error(self.T1, self.T2, gate_time_1q)
        thermal_2q_single = thermal_relaxation_error(self.T1, self.T2, gate_time_2q)
        thermal_2q = thermal_2q_single.tensor(thermal_2q_single)

        combined_1q = error_1q.compose(thermal_1q)
        combined_2q = error_2q.compose(thermal_2q)

        noise_model.add_all_qubit_quantum_error(combined_1q, ["u1", "u2", "u3", "x", "h"])
        noise_model.add_all_qubit_quantum_error(combined_2q, ["cx"])

        if extra_relax_error is not None:
            noise_model.add_all_qubit_quantum_error(extra_relax_error, ["id"])

        return noise_model

    def apply_latency_decay(self, latency: float) -> AerSimulator:
        """Relógio lógico de latência: canal de relaxamento térmico e^{-latency/T2}."""
        finite_real("latency (s)", latency, minimum=0.0)
        aging_error = thermal_relaxation_error(self.T1, self.T2, latency) if latency > 0.0 else None
        aged_noise_model = self._build_noise_model(extra_relax_error=aging_error)
        return AerSimulator(noise_model=aged_noise_model, seed_simulator=self.seed)

    @staticmethod
    def build_bbpssw_circuit() -> QuantumCircuit:
        qc = QuantumCircuit(4, 2, name="BBPSSW")
        for a, b in [(0, 1), (2, 3)]:
            qc.h(a)
            qc.cx(a, b)
        qc.barrier()
        for q in range(4):
            qc.id(q)
        qc.barrier()
        qc.cx(0, 2)
        qc.cx(1, 3)
        qc.barrier()
        qc.measure(2, 0)
        qc.measure(3, 1)
        return qc

    def run_purification(self, simulator: AerSimulator = None):
        """Legacy gate-circuit diagnostic.

        This method measures circuit post-selection under node noise but does
        not represent input-pair fidelity. Controller code must call
        :meth:`run_purification_event` instead.
        """
        sim = simulator if simulator is not None else self.simulator
        result = sim.run(self._compiled_circuit, shots=self.shots).result()
        counts = result.get_counts()
        success_counts = counts.get("00", 0) + counts.get("11", 0)
        success_rate = success_counts / self.shots
        return success_rate, counts

    def run_purification_event(self, fidelity_pair_1: float,
                               fidelity_pair_2: float = None,
                               memory_age_1_s: float = 0.0,
                               memory_age_2_s: float = None,
                               random_value: float = None,
                               pair_1_id: str = None,
                               pair_2_id: str = None) -> dict:
        """Purify the actual admitted Werner pairs and return an auditable event.

        The two inputs are aged with Qiskit's joint thermal-relaxation channel,
        then passed through the unequal-input BBPSSW formula. A single supplied
        uniform variate enables common-random-number comparisons across
        controller policies.
        """
        # Deferred import avoids the network <-> swapping protocol interface
        # cycle while keeping the physical implementation centralized.
        from quantum_twin.quantum.purification import (
            age_werner_fidelity,
            bbpssw_analytical_pair,
        )

        f2 = fidelity_pair_1 if fidelity_pair_2 is None else fidelity_pair_2
        age2 = memory_age_1_s if memory_age_2_s is None else memory_age_2_s
        for name, value in (("fidelity_pair_1", fidelity_pair_1), ("fidelity_pair_2", f2)):
            if not np.isfinite(value) or not 0.0 <= float(value) <= 1.0:
                raise ValueError(f"{name} must be a finite value in [0, 1]")
        if memory_age_1_s < 0.0 or age2 < 0.0:
            raise ValueError("memory ages cannot be negative")
        aged_1 = age_werner_fidelity(fidelity_pair_1, self.T1, self.T2, memory_age_1_s)
        aged_2 = age_werner_fidelity(f2, self.T1, self.T2, age2)
        protocol = bbpssw_analytical_pair(aged_1, aged_2)

        # Two bilateral CNOTs suffer the configured node depolarization.  This
        # transparent effective map affects output quality, while BBPSSW's
        # post-selection probability remains separately reported.
        cnot_gate_quality = (1.0 - float(np.clip(self.depol_prob * 2.0, 0.0, 1.0))) ** 2
        f_after_gate_noise = 0.25 + (protocol["F_after"] - 0.25) * cnot_gate_quality
        f_after = age_werner_fidelity(f_after_gate_noise, self.T1, self.T2, 300e-9)

        draw = float(self._rng.random() if random_value is None else random_value)
        if not 0.0 <= draw <= 1.0:
            raise ValueError("random_value must be in [0, 1]")
        sampled_success = draw < protocol["success_probability"]

        return {
            "pair_1_id": pair_1_id,
            "pair_2_id": pair_2_id,
            "F1_before": float(np.clip(fidelity_pair_1, 0.0, 1.0)),
            "F2_before": float(np.clip(f2, 0.0, 1.0)),
            "memory_age_1_s": float(max(memory_age_1_s, 0.0)),
            "memory_age_2_s": float(max(age2, 0.0)),
            "F1_aged": aged_1,
            "F2_aged": aged_2,
            "success_probability": protocol["success_probability"],
            "random_value": draw,
            "sampled_success": sampled_success,
            "F_after_ideal_protocol": protocol["F_after"],
            "F_after": f_after if sampled_success else None,
            "delta_F": (f_after - min(aged_1, aged_2)) if sampled_success else None,
            "qpu_operations": 4,
            "input_assumption": (
                "two_identical_werner_pairs" if fidelity_pair_2 is None
                else "two_explicit_werner_pairs"
            ),
        }

    # -----------------------------------------------------------------
    # Estado interno do Gêmeo Digital (novo)
    # -----------------------------------------------------------------
    def update_telemetry(self, telemetry_row: dict) -> None:
        """
        Atualiza o estado de "qualidade do enlace" e a telemetria física mais
        recente a partir de uma linha do dataset físico (QuantumNetworkDataset).
        """
        self.last_telemetry = telemetry_row
        self.link_quality = telemetry_row.get("F_t")
        self.fidelity_history.append(self.link_quality)

    def store_pair(self, elapsed_time: float) -> None:
        """Registra que um par de Bell está sendo mantido em memória quântica."""
        self.memory_state = "STORING"
        self.storage_time_s += elapsed_time

    def record_purification_result(self, attempted: bool, succeeded: bool, halted: bool) -> None:
        """
        Atualiza as estatísticas de purificação do nó e libera o estado da
        memória quântica (o par foi consumido, seja por purificação ou HALT).
        """
        if halted:
            self.purification_stats["halted"] += 1
        if attempted:
            self.purification_stats["attempted"] += 1
            if succeeded:
                self.purification_stats["succeeded"] += 1
        self.memory_state = "IDLE"
        self.storage_time_s = 0.0

    def get_state_snapshot(self) -> dict:
        """Retorna um retrato do estado interno atual do nó (útil para logging/depuração)."""
        return {
            "link_quality": self.link_quality,
            "memory_state": self.memory_state,
            "storage_time_s": self.storage_time_s,
            "fidelity_history_len": len(self.fidelity_history),
            **self.purification_stats,
        }

from abc import ABC, abstractmethod
from dataclasses import replace

from quantum_twin.core.config import PhysicsConfig
from quantum_twin.quantum.channel import QuantumChannel


class QuantumNode:
    """
    A network endpoint (or intermediate repeater station). Deliberately
    minimal at this stage -- it exists as an explicit topology element so
    that `NetworkLink` and future multi-hop/multi-node topologies have a
    concrete object to attach to, per the modular Node->Channel->Repeater
    structure requested in the roadmap.
    """
    def __init__(self, name: str):
        self.name = name

    def __repr__(self):
        return f"QuantumNode({self.name!r})"


class NetworkLink:
    """
    One physical link segment between two QuantumNodes: `node_a --[channel]-- node_b`.
    Each link owns its OWN PhysicsConfig (own T1, T2, distance, loss,
    depolarization) and its own QuantumChannel instance -- links are never
    forced to share physics, satisfying "Cada enlace deve poder possuir
    seus próprios T1; T2; perda; distância; BER; ruído."
    """

    def __init__(self, node_a: QuantumNode, node_b: QuantumNode, config: PhysicsConfig, seed: int = None):
        self.node_a = node_a
        self.node_b = node_b
        self.config = config if seed is None else replace(config, SEED=seed)
        self.channel = QuantumChannel(self.config, rng=np.random.default_rng(self.config.SEED))

    def __repr__(self):
        return f"NetworkLink({self.node_a.name} <-> {self.node_b.name}, D={self.config.DISTANCE_KM}km)"

    def transmit(self, transmission_exposure_time: float = None, storage_time: float = 0.0,
                 depol_prob_override: float = None) -> dict:
        depol_prob = depol_prob_override if depol_prob_override is not None else self.config.DEPOLARIZATION_P
        exposure = (transmission_exposure_time if transmission_exposure_time is not None
                    else self.config.TRANSMISSION_EXPOSURE_TIME)
        return self.channel.transmit(distance_km=self.config.DISTANCE_KM, depol_prob=depol_prob,
                                      transmission_exposure_time=exposure, storage_time=storage_time)


class Repeater(QuantumNode):
    """
    A QuantumNode specialized as a repeater: sits between two NetworkLinks
    and (eventually) performs entanglement swapping between them. This
    class composes the two `NetworkLink`s it's attached to but does not
    yet perform swapping -- see `EntanglementSwappingProtocol` below for
    the extension point.
    """
    def __init__(self, name: str, link_left: NetworkLink = None, link_right: NetworkLink = None):
        super().__init__(name)
        self.link_left = link_left
        self.link_right = link_right


# ===========================================================================
# Extension points (interfaces) -- NOT fully implemented, intentionally.
# These exist so entanglement swapping / BSM / purification can be added
# later without restructuring the Node/Channel/Repeater topology above.
# ===========================================================================

class EntanglementSwappingProtocol(ABC):
    """
    Interface for a future entanglement-swapping implementation at a
    Repeater: given two independently-generated Bell pairs (one per
    adjacent link), produce a single longer-range entangled pair spanning
    both links' endpoints. Not implemented in this increment -- the
    interface exists so a concrete strategy (e.g., a real BSM circuit
    simulated in Qiskit Aer) can be dropped in without touching
    `NetworkLink` or `Repeater`.
    """

    @abstractmethod
    def swap(self, pair_left: dict, pair_right: dict) -> dict:
        """
        pair_left / pair_right: dicts as returned by NetworkLink.transmit()
        (containing at least 'F_t' and 'success'). Must return a dict of
        the same shape describing the resulting long-range pair.
        """
        raise NotImplementedError


class BellStateMeasurement(ABC):
    """
    Interface for a future Bell-state-measurement implementation (the
    physical operation entanglement swapping is built on: measure two
    qubits, one from each adjacent pair, in the Bell basis). Not
    implemented in this increment.
    """

    @abstractmethod
    def measure(self, qubit_a, qubit_b) -> str:
        """Should return one of the four Bell-basis outcome labels."""
        raise NotImplementedError


class PurificationProtocol(ABC):
    """
    Interface for a purification strategy applied at a node. The BBPSSW
    circuit already implemented in `repeater.py` (v2 core, still valid and
    reused by the orchestrator) is one concrete instance of this interface
    in spirit; formalizing it as an ABC here makes it swappable for
    alternative purification protocols later without touching the
    orchestrator's control-flow logic.
    """

    @abstractmethod
    def purify(self, pair_a: dict, pair_b: dict) -> dict:
        raise NotImplementedError
