"""Executable diagnostic for scientific input guards, including frozen builds.

The diagnostic only constructs local simulated channels. It does not connect to
instruments, open an operator journal, or certify simulated key material.
"""
from __future__ import annotations

from dataclasses import fields
import json
from pathlib import Path
import sys


def validate_scientific_inputs() -> dict:
    from quantum_twin.core.config import PhysicsConfig
    from quantum_twin.quantum.channel import QuantumChannel, QuantumNoiseChannel, pure_dephasing_probability
    from quantum_twin.simulation.network import QuantumRepeaterNode

    rows = []

    def reject(name, action):
        try:
            action()
        except ValueError as error:
            rows.append({"name": name, "passed": bool(str(error)), "outcome": "rejected",
                         "error_type": type(error).__name__, "message": str(error)})
        except Exception as error:
            rows.append({"name": name, "passed": False, "outcome": "unexpected_exception",
                         "error_type": type(error).__name__, "message": str(error)})
        else:
            rows.append({"name": name, "passed": False, "outcome": "invalid_input_accepted"})

    for field in fields(PhysicsConfig):
        for label, value in (("nan", float("nan")), ("infinity", float("inf")),
                             ("boolean", True)):
            reject(f"PhysicsConfig.{field.name}.{label}",
                   lambda name=field.name, value=value: PhysicsConfig(**{name: value}))
    for name, value in (("T1", 0.0), ("T2", 0.0), ("DISTANCE_KM", -1.0),
                        ("ALPHA_DB_PER_KM", -0.1), ("PHOTON_RATE_BASE", -1.0),
                        ("STORAGE_TIME", -1.0), ("TRANSMISSION_EXPOSURE_TIME", -1.0),
                        ("OU_THETA_SIGMA", -1.0), ("OU_THETA_MEAN_REVERSION", -1.0),
                        ("KAPPA_ENV_T1T2", -1.0), ("KAPPA_DEPOL_FROM_BER", -1.0),
                        ("DEPOLARIZATION_P", -0.1), ("DEPOLARIZATION_P", 1.1),
                        ("NUM_QUBITS", 0), ("NUM_QUBITS", 2.5), ("SEED", -1),
                        ("SEED", 0.5), ("OU_SAMPLING_INTERVAL_STEPS", 0)):
        reject(f"PhysicsConfig.{name}.{value!r}", lambda name=name, value=value: PhysicsConfig(**{name: value}))
    reject("PhysicsConfig.T2_bound", lambda: PhysicsConfig(T1=1.0, T2=3.0))
    for factory in (QuantumNoiseChannel, QuantumRepeaterNode):
        for kwargs in ({"T1": 1.0, "T2": 3.0}, {"T1": 0.0}, {"T2": float("nan")},
                       {"T1": float("inf")}, {"depol_prob": -0.1}, {"depol_prob": 1.1},
                       {"depol_prob": float("nan")}, {"depol_prob": True}):
            reject(f"{factory.__name__}.{kwargs!r}", lambda factory=factory, kwargs=kwargs: factory(**kwargs))
    for kwargs in ({"shots": 0}, {"shots": 1.5}, {"history_maxlen": 0}, {"seed": True},
                   {"depol_prob": 0.6}):
        reject(f"QuantumRepeaterNode.{kwargs!r}", lambda kwargs=kwargs: QuantumRepeaterNode(**kwargs))
    noise = QuantumNoiseChannel()
    for elapsed in (-1.0, float("nan"), float("inf"), True):
        reject(f"QuantumNoiseChannel.elapsed.{elapsed!r}", lambda elapsed=elapsed: noise.apply(elapsed))
        reject(f"pure_dephasing.elapsed.{elapsed!r}",
               lambda elapsed=elapsed: pure_dephasing_probability(elapsed, 1.0, 2.0))
    for value in (-0.1, 1.1, float("nan"), True):
        reject(f"QuantumNoiseChannel.override.{value!r}", lambda value=value: noise.apply(0.0, value))

    # Validate the same mutable configuration path used by the environment.
    mutated = PhysicsConfig()
    mutated.T2 = 3 * mutated.T1
    reject("QuantumChannel.mutated_config", lambda: QuantumChannel(mutated))
    channel = QuantumChannel(PhysicsConfig(ALPHA_DB_PER_KM=1.0))
    for name, kwargs in (("negative_distance", {"distance_km": -1.0}),
                         ("lost_photon_invalid_probability", {"distance_km": 1e6, "depol_prob": 1.1}),
                         ("negative_exposure", {"transmission_exposure_time": -1.0}),
                         ("nan_storage", {"storage_time": float("nan")}),
                         ("boolean_storage", {"storage_time": True})):
        values = {"distance_km": 0.0, "depol_prob": 0.01, "transmission_exposure_time": 0.0}
        values.update(kwargs)
        reject(f"QuantumChannel.transmit.{name}", lambda values=values: channel.transmit(**values))

    try:
        PhysicsConfig(T1=1.0, T2=2.0, DEPOLARIZATION_P=1.0, DISTANCE_KM=0.0,
                      STORAGE_TIME=0.0, TRANSMISSION_EXPOSURE_TIME=0.0,
                      PHOTON_RATE_BASE=0.0, ALPHA_DB_PER_KM=0.0, OU_THETA_SIGMA=0.0)
        pure_limit = pure_dephasing_probability(1.0, 1.0, 2.0)
        identity_fidelity = QuantumNoiseChannel(T1=1.0, T2=2.0, depol_prob=0.0).apply(0.0)
        mixed_fidelity = QuantumNoiseChannel(T1=1.0, T2=2.0, depol_prob=1.0).apply(0.0)
        # Explicit runtime checks, so this diagnostic itself is optimization-safe.
        # Qiskit's PSD matrix-square-root fidelity at the fully mixed boundary
        # has eigensolver round-off (observed 4.43e-9 on the Windows BLAS
        # reference); this is a numerical tolerance, not a model adjustment.
        fidelity_abs_tolerance = 1e-7
        valid = (pure_limit == 0.0 and abs(identity_fidelity - 1.0) <= fidelity_abs_tolerance
                 and abs(mixed_fidelity - 0.25) <= fidelity_abs_tolerance)
        rows.append({"name": "valid_physical_boundaries", "passed": bool(valid),
                     "outcome": "evaluated", "pure_dephasing": pure_limit,
                     "identity_fidelity": identity_fidelity, "fully_depolarized_fidelity": mixed_fidelity,
                     "fidelity_abs_tolerance": fidelity_abs_tolerance})
    except Exception as error:
        rows.append({"name": "valid_physical_boundaries", "passed": False,
                     "outcome": "unexpected_exception", "error_type": type(error).__name__, "message": str(error)})
    return {"schema": "orion.scientific-input-validation.v1", "passed": all(row["passed"] for row in rows),
            "checks": rows, "checks_count": len(rows), "python_optimization": sys.flags.optimize,
            "frozen": bool(getattr(sys, "frozen", False)), "python_version": sys.version,
            "physical_action_performed": False, "hardware_validated": False,
            "scope": "Input rejection and local simulated-channel boundary checks; not experimental qualification"}


def write_scientific_validation(path) -> dict:
    output = Path(path).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    report = validate_scientific_inputs()
    with output.open("x", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, allow_nan=False, indent=2)
    return report
