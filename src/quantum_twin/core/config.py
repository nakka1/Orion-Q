"""
quantum_twin/core/config.py
==============================

REAL implementation of the centralized physics configuration -- migrated
here from root-level physics_config.py in the one-hundred-seventh
addendum (TCC item 6), the twenty-second and final module migrated in
this series after the eighty-fifth addendum's lazy-loading fix --
the LARGEST consumer count in the entire project (69 consumers),
closing out this project's incremental migration series.

Root-level physics_config.py is now the thin re-export (direction
reversed), kept so all pre-existing consumers that import this module
directly continue to work completely UNCHANGED, including numerous
function-level (deferred) imports (some aliased as FlatPhysicsConfig)
across many test and script files. Eight sibling shims across FOUR
subpackages previously imported directly from root physics_config.py --
quantum_twin/optical/sources.py, quantum_twin/optical/wdm.py,
quantum_twin/quantum/channel.py, quantum_twin/quantum/physics_engine.py,
quantum_twin/quantum/memory.py, quantum_twin/simulation/environment.py,
quantum_twin/simulation/network.py, and this file itself -- the widest
sibling-shim footprint of any migration in this entire series. All
eight updated to import from THIS new location instead.

Original module docstring, preserved from the migrated source:

Centralized physics configuration, per the roadmap requirement: "Adicionar
configuração centralizada ... Permitir alterar os parâmetros sem modificar
o código principal." Every physical parameter that drives the v3 causal
simulation lives here, in one place, with save/load helpers for
reproducibility (config is saved alongside every experiment's results and
dataset).
"""

import json
from dataclasses import dataclass, asdict

from .validation import finite_real, integer, probability, relaxation_times


@dataclass
class PhysicsConfig:
    """
    Single source of truth for every physical parameter used by the causal
    v3 channel simulation (quantum_channel_v3.py) and dataset generator
    (dataset_v3.py). Nothing in the physics core should hardcode a physical
    constant that isn't listed here.
    """
    NUM_QUBITS: int = 2
    T1: float = 50e-6                 # baseline relaxation time (s)
    T2: float = 30e-6                 # baseline dephasing time (s); must be <= 2*T1
    DISTANCE_KM: float = 10.0         # baseline link distance (km)
    ALPHA_DB_PER_KM: float = 0.2      # fiber attenuation coefficient (dB/km) -- swappable for real WDM data
    DEPOLARIZATION_P: float = 0.01    # baseline single-qubit depolarizing probability
    PHOTON_RATE_BASE: float = 1.0e6   # source photon generation rate (Hz), before losses
    STORAGE_TIME: float = 2.0e-6      # baseline quantum-memory storage duration (s), separate from transmission exposure
    TRANSMISSION_EXPOSURE_TIME: float = 1.0e-5   # baseline channel exposure time during transmission (s)
    SEED: int = 42

    # --- NEW: WDM-observable optical causal chain parameters (added for the
    # phase-drift -> optical -> quantum coupling; see dataset_v3.py's module
    # docstring for the full documented equation/hypothesis/limitations set
    # required whenever an approximation is introduced). All have defaults,
    # so no existing PhysicsConfig(...) call site anywhere in the repo needs
    # to change. ---
    TX_POWER_DBM: float = 0.0          # nominal launch power (dBm)
    NOISE_FLOOR_DBM: float = -40.0     # ASE/thermal noise floor in the reference bandwidth (dBm)
    KAPPA_PHASE: float = 1.0           # environmental-stress -> phase-drift coupling (rad per unit theta)
    KAPPA_DEPOL_FROM_BER: float = 15.0  # optical BER -> extra quantum depolarizing-probability coupling
    KAPPA_ENV_T1T2: float = 0.03        # shared-environment -> T1/T2 degradation coupling

    # --- NEW (seventy-sixth addendum, master prompt v5 Secao 1): explicit
    # Ornstein-Uhlenbeck process parameters for the shared environmental
    # driver theta(t), exposed here so domain-shift experiments can vary
    # them WITHOUT touching dataset_v3.py's source code. Defaults match
    # the values that were previously HARDCODED inside
    # dataset_v3.py's _unbounded_mean_reverting_walk() call for theta_series
    # exactly (sigma=0.6, mean_reversion=0.1) -- every existing
    # PhysicsConfig(...) call site anywhere in the repo continues to
    # produce BYTE-IDENTICAL datasets, verified directly (see this
    # addendum's own regression test comparing default-config output
    # against the pre-change hardcoded values). ---
    OU_THETA_SIGMA: float = 0.6         # diffusion coefficient / drift amplitude of theta(t)'s random walk
    OU_THETA_MEAN_REVERSION: float = 0.1  # inversely related to correlation_time: higher = shorter memory
    OU_SAMPLING_INTERVAL_STEPS: int = 1   # every how many simulation steps theta(t) is resampled (1 = every step)

    def __post_init__(self):
        self.validate()

    def validate(self) -> None:
        """Validate physical inputs, including configurations modified in place.

        Explicit guards also run with Python -O and PyInstaller optimize=1.
        Zero source rate/loss/exposure is allowed for controlled limit cases.
        """
        relaxation_times(self.T1, self.T2)
        probability("DEPOLARIZATION_P", self.DEPOLARIZATION_P)
        integer("NUM_QUBITS", self.NUM_QUBITS, minimum=1)
        integer("SEED", self.SEED)
        integer("OU_SAMPLING_INTERVAL_STEPS", self.OU_SAMPLING_INTERVAL_STEPS, minimum=1)
        for name in ("DISTANCE_KM", "ALPHA_DB_PER_KM", "PHOTON_RATE_BASE", "STORAGE_TIME",
                     "TRANSMISSION_EXPOSURE_TIME", "KAPPA_DEPOL_FROM_BER", "KAPPA_ENV_T1T2",
                     "OU_THETA_SIGMA", "OU_THETA_MEAN_REVERSION"):
            finite_real(name, getattr(self, name), minimum=0.0)
        for name in ("TX_POWER_DBM", "NOISE_FLOOR_DBM", "KAPPA_PHASE"):
            finite_real(name, getattr(self, name))

    def save(self, path: str) -> None:
        self.validate()
        with open(path, "w", encoding="utf-8") as f:
            json.dump(asdict(self), f, indent=2)

    @classmethod
    def load(cls, path: str) -> "PhysicsConfig":
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return cls(**data)

    def with_overrides(self, **kwargs) -> "PhysicsConfig":
        """Returns a new PhysicsConfig with the given fields overridden (immutable-style update)."""
        merged = {**asdict(self), **kwargs}
        return PhysicsConfig(**merged)
