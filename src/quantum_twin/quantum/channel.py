"""
quantum_twin/quantum/channel.py
===================================

Contains the REAL implementation of both channel models compared
throughout this project: `QuantumChannel` (the causal Aer-reference
core, migrated from root-level quantum_channel_v3.py in the ninety
-fourth addendum) and `QuantumNoiseChannel` (the closed-form
Kraus-algebra fast model, migrated from root-level quantum_channel.py
in the eighty-seventh addendum). Validated to agree with each other to
floating-point precision (twenty-seventh addendum).

Both root-level files (quantum_channel_v3.py, quantum_channel.py) are
now thin re-exports (direction reversed), kept so their pre-existing
consumers continue to work completely UNCHANGED. Four sibling shims
that also imported QuantumChannel directly from the root module
(quantum_twin/quantum/physics_engine.py, quantum_twin/quantum/memory.py,
quantum_twin/simulation/network.py, plus this file's own prior form)
were updated to import from THIS new location instead -- identified
proactively by grepping every quantum_twin/ file referencing
quantum_channel_v3, per the sibling-shim discipline established since
the eighty-sixth addendum.

Original quantum_channel_v3.py docstring, preserved from the migrated source:

CAUSAL rewrite of the physics core, per the roadmap. The central rule this
module exists to satisfy:

    NOT:  parameters -> artificial formula -> F(t)
    YES:  quantum state -> noise/loss channels -> degraded state -> F(t)

Every quantity in the resulting telemetry (Loss_dB, Transmission_Efficiency,
Photon_Rate, BER, F_t) is derived from the same small set of physical
inputs (Distance_km, T1, T2, depolarizing probability, exposure time),
instead of being sampled independently and glued together afterward.

Key differences from the old (v2) quantum_channel.py:
    - F(t) comes from an ACTUAL Qiskit Aer density-matrix simulation of a
      noisy Bell-pair circuit (qiskit_aer.noise.depolarizing_error +
      amplitude_damping_error + phase_damping_error, both natively from
      Qiskit Aer, applied to a real id gate on a real circuit run through
      AerSimulator(method="density_matrix")) -- not closed-form Kraus
      algebra computed by hand.
    - Optical loss is a real erasure event: with probability
      (1 - Transmission_Efficiency), the round fails outright (no pair
      delivered) instead of just scaling a formula.
    - Loss_dB is always derived from Distance_km (never sampled
      independently); Transmission_Efficiency is always derived from
      Loss_dB; Photon_Rate is always derived from Transmission_Efficiency.
    - BER is derived from the SAME depolarizing probability and loss that
      drive F(t) -- coherent by construction, not an independent random
      draw that then artificially subtracts from F(t).
    - Quantum memory storage (T1/T2-driven decoherence while the pair
      waits in memory) is modeled as additional exposure time, separate
      from and in addition to transmission exposure time.

Original quantum_channel.py docstring, preserved from the migrated
source (Portuguese):

Modelos de canais quânticos físicos para o Gêmeo Digital do repetidor.

Substitui a degradação artificial de fidelidade (processo estatístico
Ornstein-Uhlenbeck) por um canal de ruído composto fisicamente motivado:

    Noise_Model = Depolarization + Amplitude_Damping + Phase_Damping

A fidelidade de um par de Bell após a passagem pelo canal é calculada via
operadores de Kraus (qiskit.quantum_info.DensityMatrix / state_fidelity).

Nota de projeto: usamos álgebra de Kraus (matrizes 2x2/4x4) em vez de
simulação via AerSimulator com "shots" para o CÁLCULO DE FIDELIDADE do
dataset, porque o gerador de dados precisa avaliar milhares de instantes de
tempo -- amostragem por shots seria proibitivamente lenta para essa
finalidade e desnecessária (a fidelidade é uma grandeza determinística dado
o canal). A execução REAL do circuito de purificação BBPSSW
(QuantumRepeaterNode, em repeater.py) continua usando AerSimulator com
shots e um NoiseModel completo (gate noise + T1/T2), preservando a
simulação estatística já validada do protocolo de purificação em si.
"""

import numpy as np
from qiskit import QuantumCircuit, transpile
from qiskit_aer import AerSimulator
from qiskit_aer.noise import NoiseModel, depolarizing_error, thermal_relaxation_error
from qiskit.quantum_info import DensityMatrix, state_fidelity

from quantum_twin.core.config import PhysicsConfig


def pure_dephasing_probability(elapsed_time: float, T1: float, T2: float) -> float:
    """Phase-damping probability consistent with *total* coherence time T2.

    Amplitude damping already contributes a coherence factor
    ``exp(-t/(2*T1))``.  The remaining pure-dephasing rate therefore obeys
    ``1/Tphi = 1/T2 - 1/(2*T1)``.  Qiskit's phase-damping Kraus map multiplies
    off-diagonal elements by ``sqrt(1-lambda)``, hence the factor of two in
    ``lambda = 1 - exp(-2*t/Tphi)``.
    """
    if T1 <= 0 or T2 <= 0:
        raise ValueError("T1 and T2 must be positive")
    if T2 > 2.0 * T1 + 1e-15:
        raise ValueError("physical constraint violated: T2 must be <= 2*T1")
    rate = max((1.0 / T2) - (1.0 / (2.0 * T1)), 0.0)
    return float(1.0 - np.exp(-2.0 * elapsed_time * rate))


class QuantumChannel:
    """
    A single physical link segment: fiber of a given distance, with
    time-varying T1/T2/depolarization, causally producing telemetry and a
    Bell-pair fidelity via a REAL Qiskit Aer simulation.
    """

    def __init__(self, config: PhysicsConfig, rng: np.random.Generator = None):
        self.config = config
        self.rng = rng if rng is not None else np.random.default_rng(config.SEED)

        # Pre-build and pre-transpile the circuit ONCE (structure never
        # changes -- only the noise model attached to the 'id' gates does).
        # optimization_level=0 is REQUIRED here: at higher optimization
        # levels the transpiler removes "no-op" id gates before the noise
        # model ever sees them, silently making the channel noiseless.
        self._circuit = self._build_circuit()
        self._base_sim = AerSimulator(method="density_matrix")
        self._compiled_circuit = transpile(self._circuit, self._base_sim, optimization_level=0)

        psi = np.zeros(4, dtype=complex)
        psi[0] = psi[3] = 1.0 / np.sqrt(2)
        self._ideal_bell = DensityMatrix(np.outer(psi, psi.conj()))

    @staticmethod
    def _build_circuit() -> QuantumCircuit:
        """Bell-pair preparation + an idle ('id') gate on each qubit, which
        is where the noise model's error channel is attached -- this idle
        gate represents the qubit's exposure to the channel/memory."""
        qc = QuantumCircuit(2)
        qc.h(0)
        qc.cx(0, 1)
        qc.id(0)
        qc.id(1)
        qc.save_density_matrix()
        return qc

    def loss_db(self, distance_km: float) -> float:
        """Loss_dB = alpha * Distance_km -- ALWAYS derived, never sampled independently."""
        return self.config.ALPHA_DB_PER_KM * distance_km

    def transmission_efficiency(self, distance_km: float) -> float:
        """eta = 10^(-Loss_dB/10) -- ALWAYS derived from Loss_dB, never independent."""
        return float(10 ** (-self.loss_db(distance_km) / 10.0))

    def photon_rate(self, distance_km: float, source_stability_noise: float = 0.0) -> float:
        """Photon_Rate = base_rate * efficiency * (1 + small source jitter)
        -- ALWAYS derived from efficiency (hence from loss, hence from distance)."""
        eta = self.transmission_efficiency(distance_km)
        return max(self.config.PHOTON_RATE_BASE * eta * (1.0 + source_stability_noise), 0.0)

    def bit_error_rate(self, depol_prob: float, distance_km: float) -> float:
        """
        BER causally coupled to the SAME depolarizing probability and the
        SAME transmission efficiency that drive F(t) -- not an independent
        random draw. A depolarizing channel with probability p flips a
        fixed-basis measurement outcome with probability ~p/2; erasure loss
        contributes the remainder (an undetected/lost photon reads as an
        error at the classical layer).
        """
        eta = self.transmission_efficiency(distance_km)
        return float(np.clip(0.5 * depol_prob + 0.5 * (1.0 - eta), 0.0, 1.0))

    def _build_noise_model(self, depol_prob: float, exposure_time: float) -> NoiseModel:
        """
        Composite noise model using Qiskit Aer's NATIVE noise channel
        constructors (not hand-derived Kraus operators):
            - depolarizing_error(p, 1)               -- gate/transmission errors
            - thermal_relaxation_error(T1, T2, t)     -- joint T1/T2 relaxation
        Composed into a single QuantumError and attached to the 'id' gate,
        which is where each qubit "sits" while exposed to the channel.
        """
        depol = depolarizing_error(depol_prob, 1)
        thermal = thermal_relaxation_error(
            self.config.T1, self.config.T2, max(float(exposure_time), 0.0)
        )
        combined = depol.compose(thermal)

        noise_model = NoiseModel()
        noise_model.add_all_qubit_quantum_error(combined, ["id"])
        return noise_model

    def simulate_fidelity(self, depol_prob: float, exposure_time: float) -> float:
        """
        Runs the ACTUAL noisy circuit through AerSimulator and returns the
        fidelity of the resulting density matrix against the ideal Bell
        state -- this is the causal replacement for the old closed-form
        Kraus-algebra shortcut.
        """
        noise_model = self._build_noise_model(depol_prob, exposure_time)
        sim = AerSimulator(method="density_matrix", noise_model=noise_model)
        result = sim.run(self._compiled_circuit).result()
        rho = result.data(0)["density_matrix"]
        return float(state_fidelity(rho, self._ideal_bell))

    def apply_decoherence_to_state(self, input_density_matrix: DensityMatrix,
                                    depol_prob: float, exposure_time: float) -> DensityMatrix:
        """
        Applies this channel's noise model to an ARBITRARY input 2-qubit
        density matrix (not necessarily the ideal Bell state) for the
        given exposure time -- e.g., a pair that has already decohered
        during storage and is now decohering FURTHER, rather than a fresh
        ideal pair. Used by quantum_memory.QuantumMemory for a rigorous
        (density-matrix-level) treatment of repeated/compounding storage
        decay, replacing an earlier scalar-fidelity-multiplication
        approximation.

        Implemented via Qiskit Aer's set_density_matrix instruction
        (initializes the simulator's state to input_density_matrix
        instead of preparing a fresh Bell pair), then applies the same
        noisy 'id' gates as simulate_fidelity.
        """
        qc = QuantumCircuit(2)
        qc.set_density_matrix(input_density_matrix.data)
        qc.id(0)
        qc.id(1)
        qc.save_density_matrix()

        noise_model = self._build_noise_model(depol_prob, exposure_time)
        sim = AerSimulator(method="density_matrix", noise_model=noise_model)
        compiled = transpile(qc, sim, optimization_level=0)
        result = sim.run(compiled).result()
        return result.data(0)["density_matrix"]

    def transmit(self, distance_km: float, depol_prob: float, transmission_exposure_time: float,
                 storage_time: float = 0.0) -> dict:
        """
        A single causal transmission + (optional) storage event.

        1. Optical loss check: with probability (1 - Transmission_Efficiency),
           the photon is lost in transit (erasure) -- the round fails
           OUTRIGHT, and no fidelity is even computed (there is no pair).
        2. If the photon survives, the Bell pair is prepared and exposed to
           the noise channel for (transmission_exposure_time + storage_time).
        3. F(t) is read off the ACTUAL simulated density matrix.
        4. BER is derived from the SAME depol_prob/efficiency (not from F(t)).

        Returns a dict with every derived quantity, causally consistent by
        construction.
        """
        loss_db = self.loss_db(distance_km)
        eta = self.transmission_efficiency(distance_km)
        photon_rate = self.photon_rate(distance_km)
        ber = self.bit_error_rate(depol_prob, distance_km)

        channel_available = self.rng.random() <= eta  # erasure/loss event

        if not channel_available:
            return {
                "F_t": 0.0, "channel_available": 0.0, "success": False,
                "Loss_dB": loss_db, "Transmission_Efficiency": eta, "Photon_Rate": photon_rate,
                "BER": ber, "Distance_km": distance_km,
            }

        total_exposure_time = transmission_exposure_time + storage_time
        fidelity = self.simulate_fidelity(depol_prob, total_exposure_time)

        return {
            "F_t": fidelity, "channel_available": 1.0, "success": True,
            "Loss_dB": loss_db, "Transmission_Efficiency": eta, "Photon_Rate": photon_rate,
            "BER": ber, "Distance_km": distance_km,
        }

# --- Matrizes de Pauli (operadores de base para o canal de despolarização) ---
_I2 = np.eye(2, dtype=complex)
_X = np.array([[0, 1], [1, 0]], dtype=complex)
_Y = np.array([[0, -1j], [1j, 0]], dtype=complex)
_Z = np.array([[1, 0], [0, -1]], dtype=complex)


def _depolarizing_kraus(p: float):
    """Operadores de Kraus do canal de despolarização de 1 qubit (convenção padrão Qiskit)."""
    p = float(np.clip(p, 0.0, 1.0))
    k0 = np.sqrt(max(1 - 3 * p / 4, 0.0)) * _I2
    k1 = np.sqrt(p / 4) * _X
    k2 = np.sqrt(p / 4) * _Y
    k3 = np.sqrt(p / 4) * _Z
    return [k0, k1, k2, k3]


def _amplitude_damping_kraus(gamma: float):
    """Operadores de Kraus do canal de amplitude damping (perda de energia / relaxação T1)."""
    gamma = float(np.clip(gamma, 0.0, 1.0))
    k0 = np.array([[1, 0], [0, np.sqrt(max(1 - gamma, 0.0))]], dtype=complex)
    k1 = np.array([[0, np.sqrt(max(gamma, 0.0))], [0, 0]], dtype=complex)
    return [k0, k1]


def _phase_damping_kraus(lam: float):
    """Operadores de Kraus do canal de phase damping (perda de coerência / T2)."""
    lam = float(np.clip(lam, 0.0, 1.0))
    k0 = np.array([[1, 0], [0, np.sqrt(max(1 - lam, 0.0))]], dtype=complex)
    k1 = np.array([[0, 0], [0, np.sqrt(max(lam, 0.0))]], dtype=complex)
    return [k0, k1]


class QuantumNoiseChannel:
    """
    Canal de ruído quântico físico composto, aplicado localmente e de forma
    independente a cada qubit de um par de Bell (modelo padrão de "canal
    ponto a ponto" para comunicação quântica: cada extremidade do par sofre
    decoerência independente durante o armazenamento/transmissão).

    Combina:
        - Despolarização (probabilidade p, variável no tempo)
        - Amplitude damping (gamma, derivado de T1 e do tempo decorrido)
        - Phase damping (lambda, derivado de T2 e do tempo decorrido)

    A fidelidade resultante é calculada em relação ao estado de Bell ideal
    |Phi+> = (|00> + |11>)/sqrt(2) via qiskit.quantum_info.state_fidelity.
    """

    def __init__(self, T1: float = 50e-6, T2: float = 30e-6, depol_prob: float = 0.01):
        assert T2 <= 2 * T1, "Restrição física: T2 deve ser <= 2*T1"
        self.T1 = T1
        self.T2 = T2
        self.depol_prob = depol_prob

        psi = np.zeros(4, dtype=complex)
        psi[0] = 1.0 / np.sqrt(2)
        psi[3] = 1.0 / np.sqrt(2)
        self._ideal_bell = DensityMatrix(np.outer(psi, psi.conj()))

    def _local_kraus_operators(self, elapsed_time: float, depol_prob_override: float = None):
        """Constrói os operadores de Kraus locais (1 qubit) combinando os três canais."""
        p = depol_prob_override if depol_prob_override is not None else self.depol_prob
        gamma = 1.0 - np.exp(-elapsed_time / self.T1) if self.T1 > 0 else 0.0
        lam = pure_dephasing_probability(elapsed_time, self.T1, self.T2)

        combined_ops = []
        for kd in _depolarizing_kraus(p):
            for ka in _amplitude_damping_kraus(gamma):
                for kp in _phase_damping_kraus(lam):
                    combined_ops.append(kp @ ka @ kd)
        return combined_ops

    def apply(self, elapsed_time: float, depol_prob_override: float = None) -> float:
        """
        Aplica o canal de ruído composto a um par de Bell ideal e retorna a
        fidelidade resultante em relação ao estado ideal, após `elapsed_time`
        segundos de exposição ao canal (transmissão + armazenamento).
        """
        kraus_1q = self._local_kraus_operators(elapsed_time, depol_prob_override)

        rho = self._ideal_bell.data
        rho_out = np.zeros((4, 4), dtype=complex)
        for k_a in kraus_1q:
            for k_b in kraus_1q:
                op = np.kron(k_a, k_b)
                rho_out += op @ rho @ op.conj().T

        rho_final = DensityMatrix(rho_out)
        return float(state_fidelity(rho_final, self._ideal_bell))
