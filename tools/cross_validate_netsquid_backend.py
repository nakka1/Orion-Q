"""Optional *real* NetSquid subprocess backend for reduced cross-simulator cases.

Run with the Python environment in which the operator has lawfully installed
NetSquid. This file does not install packages, accept a licence, read credentials,
import OrionQ models, or replace a missing simulator with analytic formulae.

The public NetSquid overview and Coopmans et al. (2021), DOI
10.1038/s42005-021-00647-8, describe the components used here. Full API
documentation requires registration. Until executed with a supported SDK, the
native API compatibility and numerical results remain NOT DEMONSTRATED.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import importlib
import importlib.metadata
import json
import math
from pathlib import Path
import platform
import sys
from types import SimpleNamespace


INPUT_SCHEMA = "orionq.cross-simulator-input.v1"
OUTPUT_SCHEMA = "orionq.cross-simulator-output.v1"
KINDS = {
    "transmission": {"length_km", "attenuation_db_km", "insertion_db", "trials"},
    "memory": {"fidelity", "elapsed_s", "coherence_s"},
    "swapping": {"first", "second", "operation_error"},
    "purification": {"first", "second", "operation_error", "trials"},
    "classical_delay": {"distance_km", "light_speed_km_s", "send_time_s"},
}
MAX_TRIALS = 100_000
MAX_CASES = 256
MAX_INPUT_BYTES = 1_048_576
COMMON_LIMITATIONS = [
    "Reduced synthetic experiments; no hardware or network deployment is validated.",
    "No TF-QKD security proof, satellite optical model, or complete OrionQ GUI is validated.",
    "Seed values identify independent random streams, not identical samples across simulators.",
]


def _number(value, name, low=0.0, high=1e12):
    if type(value) not in (int, float) or not math.isfinite(value) or not low <= value <= high:
        raise ValueError(f"{name}: finite number in [{low}, {high}] required")
    return float(value)


def validate_request(request):
    """Validate every case before importing or running an external simulator."""
    if not isinstance(request, dict) or set(request) != {"schema", "seed", "cases"}:
        raise ValueError("input requires schema, seed and cases only")
    if request["schema"] != INPUT_SCHEMA:
        raise ValueError("unsupported input schema")
    if type(request["seed"]) is not int or not 0 <= request["seed"] < 2**32:
        raise ValueError("seed: uint32 integer required")
    cases = request["cases"]
    if not isinstance(cases, list) or not 1 <= len(cases) <= MAX_CASES:
        raise ValueError(f"cases: nonempty list with at most {MAX_CASES} entries required")
    identifiers = set()
    for case in cases:
        if not isinstance(case, dict) or set(case) != {"id", "kind", "parameters"}:
            raise ValueError("case requires id, kind and parameters only")
        ident, kind, parameters = case["id"], case["kind"], case["parameters"]
        if not isinstance(ident, str) or not 1 <= len(ident) <= 128 or ident.strip() != ident:
            raise ValueError("case id: nonempty trimmed string of at most 128 characters required")
        if ident in identifiers:
            raise ValueError("duplicate case id")
        identifiers.add(ident)
        if not isinstance(kind, str) or kind not in KINDS:
            raise ValueError("unsupported case kind")
        if not isinstance(parameters, dict) or set(parameters) != KINDS[kind]:
            raise ValueError(f"{kind}: exact parameter fields required")
        for name, value in parameters.items():
            if name == "trials":
                if type(value) is not int or not 1 <= value <= MAX_TRIALS:
                    raise ValueError(f"trials: integer in [1, {MAX_TRIALS}] required")
            elif name in {"fidelity", "first", "second", "operation_error"}:
                _number(value, name, 0.0, 1.0)
            elif name in {"coherence_s", "light_speed_km_s"}:
                _number(value, name, 1e-12, 1e12)
            else:
                _number(value, name)
        if kind == "purification" and (parameters["first"] <= .5 or parameters["second"] <= .5):
            raise ValueError("BBPSSW admission here requires both Werner fidelities greater than 0.5")
    return request


def _load_backend():
    """Load SDK symbols only after validation. No credential access or fallback."""
    ns = importlib.import_module("netsquid")
    qapi = importlib.import_module("netsquid.qubits.qubitapi")
    operators = importlib.import_module("netsquid.qubits.operators")
    components = importlib.import_module("netsquid.components")
    noise = importlib.import_module("netsquid.components.models.qerrormodels")
    formalism = importlib.import_module("netsquid.qubits.qformalism")
    np = importlib.import_module("numpy")
    try:
        version = importlib.metadata.version("netsquid")
    except importlib.metadata.PackageNotFoundError:
        version = str(getattr(ns, "__version__", "unreported"))
    return SimpleNamespace(ns=ns, qapi=qapi, operators=operators, components=components,
                           noise=noise, formalism=formalism, np=np, version=version)


def _reset(backend, seed):
    backend.ns.sim_reset()
    backend.ns.set_random_state(seed=seed)
    backend.ns.set_qstate_formalism(backend.formalism.QFormalism.DM)


def _werner(backend, fidelity):
    """Prepare a density matrix, not a final-fidelity oracle."""
    np = backend.np
    bell = np.array([1., 0., 0., 1.], dtype=complex) / np.sqrt(2.)
    remainder = (1. - fidelity) / 3.
    return remainder * np.eye(4, dtype=complex) + (fidelity - remainder) * np.outer(bell, bell.conj())


def _bell_fidelity(backend, qubits):
    bell = backend.np.array([1., 0., 0., 1.], dtype=complex) / backend.np.sqrt(2.)
    value = float(backend.qapi.fidelity(qubits, bell, squared=True))
    if not math.isfinite(value) or not -1e-12 <= value <= 1. + 1e-12:
        raise ValueError("SDK returned an invalid fidelity")
    return value


def _now(backend):
    return float(backend.ns.sim_time()) / 1e9  # NetSquid default time unit: nanoseconds.


def _transmission(backend, parameters):
    ns, qapi = backend.ns, backend.qapi
    # dB insertion loss is an input conversion for the SDK's loss probability.
    insertion_probability = 1. - 10. ** (-parameters["insertion_db"] / 10.)
    loss = backend.noise.FibreLossModel(p_loss_init=insertion_probability,
                                      p_loss_length=parameters["attenuation_db_km"])
    channel = backend.components.QuantumChannel(
        "cross-validation-fibre", length=parameters["length_km"], delay=1.,
        models={"quantum_loss_model": loss})
    received = 0
    arrival_times = []

    def receive(message):
        nonlocal received
        for qubit in message.items:
            if qubit is not None:
                received += 1
                qapi.discard(qubit)
        arrival_times.append(_now(backend))

    channel.ports["recv"].bind_output_handler(receive)
    sent_at = _now(backend)
    for _ in range(parameters["trials"]):
        channel.ports["send"].tx_input(qapi.create_qubits(1)[0])
    ns.sim_run(duration=2.)
    trials = parameters["trials"]
    return ({"transmission_probability": received / trials, "transmitted": received, "trials": trials},
            {"transmission_probability": "1", "transmitted": "events", "trials": "events"},
            [{"stage": "quantum_emission", "time_s": sent_at, "count": trials},
             {"stage": "quantum_reception", "time_s": max(arrival_times) if arrival_times else None,
              "count": received}],
            ["Transmission validates FibreLossModel counting only; the 1 ns channel delay is not fibre flight time.",
             "No detector efficiency, dark counts, pulse bandwidth or polarization errors are included."])


def _memory(backend, parameters):
    ns, qapi = backend.ns, backend.qapi
    pair = qapi.create_qubits(2)
    qapi.assign_qstate(pair, _werner(backend, parameters["fidelity"]))
    # One local depolarizing channel with rate 1/T gives pair visibility e^(-t/T).
    # This does not claim both independent physical memories each have coherence T.
    memory = backend.components.QuantumMemory(
        "cross-validation-memory", num_positions=2,
        memory_noise_models=[backend.noise.DepolarNoiseModel(depolar_rate=1. / parameters["coherence_s"]),
                             None])
    inserted = _now(backend)
    memory.put(pair, positions=[0, 1])
    ns.sim_run(duration=parameters["elapsed_s"] * 1e9)
    stored = memory.pop([0, 1])
    fidelity = _bell_fidelity(backend, stored)
    read_at = _now(backend)
    for qubit in stored:
        qapi.discard(qubit)
    return ({"fidelity": fidelity}, {"fidelity": "1"},
            [{"stage": "memory_write", "time_s": inserted, "count": 2},
             {"stage": "memory_read", "time_s": read_at, "count": 2}],
            ["Werner input and one local depolarizing memory rate 1/T; not a calibrated T1/T2 hardware model.",
             "Writing and reading are ideal and instantaneous; no resource contention is included."])


def _swapping(backend, parameters):
    qapi, op = backend.qapi, backend.operators
    qubits = qapi.create_qubits(4)
    qapi.assign_qstate(qubits[:2], _werner(backend, parameters["first"]))
    qapi.assign_qstate(qubits[2:], _werner(backend, parameters["second"]))
    qapi.operate([qubits[1], qubits[2]], op.CNOT)
    qapi.operate(qubits[1], op.H)
    z_bit, _ = qapi.measure(qubits[1], observable=op.Z)
    x_bit, _ = qapi.measure(qubits[2], observable=op.Z)
    if x_bit:
        qapi.operate(qubits[3], op.X)
    if z_bit:
        qapi.operate(qubits[3], op.Z)
    qapi.depolarize(qubits[3], prob=parameters["operation_error"])
    fidelity = _bell_fidelity(backend, [qubits[0], qubits[3]])
    measured = _now(backend)
    for qubit in qubits:
        qapi.discard(qubit)
    return ({"fidelity": fidelity, "success_probability": 1.},
            {"fidelity": "1", "success_probability": "1"},
            [{"stage": "bell_measurement", "time_s": measured,
              "outcome": {"x": int(x_bit), "z": int(z_bit)}},
             {"stage": "pauli_correction", "time_s": measured}],
            ["Ideal complete local CNOT/H/Z BSM: all four outcomes are resolved; not unassisted optical BSM.",
             "operation_error is a post-correction local depolarizing probability, not a physical gate error.",
             "Quantum gates and readout are instantaneous; classical signalling is tested in a separate case."])


def _purification(backend, parameters):
    qapi, op = backend.qapi, backend.operators
    accepted = 0
    fidelity_sum = 0.
    parity_counts = {"00": 0, "01": 0, "10": 0, "11": 0}
    for _ in range(parameters["trials"]):
        qubits = qapi.create_qubits(4)
        qapi.assign_qstate(qubits[:2], _werner(backend, parameters["first"]))
        qapi.assign_qstate(qubits[2:], _werner(backend, parameters["second"]))
        qapi.operate([qubits[0], qubits[2]], op.CNOT)
        qapi.operate([qubits[1], qubits[3]], op.CNOT)
        alice, _ = qapi.measure(qubits[2], observable=op.Z)
        bob, _ = qapi.measure(qubits[3], observable=op.Z)
        parity_counts[f"{int(alice)}{int(bob)}"] += 1
        if alice == bob:
            accepted += 1
            qapi.depolarize(qubits[1], prob=parameters["operation_error"])
            fidelity_sum += _bell_fidelity(backend, qubits[:2])
        for qubit in qubits:
            qapi.discard(qubit)
    trials = parameters["trials"]
    return ({"fidelity": fidelity_sum / accepted if accepted else None,
             "success_probability": accepted / trials, "accepted": accepted, "trials": trials},
            {"fidelity": "1", "success_probability": "1", "accepted": "pairs", "trials": "attempts"},
            [{"stage": "bilateral_measurements", "time_s": _now(backend), "parity_counts": parity_counts},
             {"stage": "acceptance", "time_s": _now(backend), "pairs_consumed": 2 * trials,
              "pairs_retained": accepted}],
            ["Single BBPSSW round with independent Werner inputs and ideal bilateral CNOT/Z operations.",
             "The retained Bell-diagonal state is not twirled; the reported overlap with Phi+ is unchanged by twirl.",
             "operation_error is post-acceptance local depolarizing probability; no gate or detector model.",
             "Acceptance is measured parity, not a supplied analytic success probability."])


def _classical_delay(backend, parameters):
    ns = backend.ns
    propagation_s = parameters["distance_km"] / parameters["light_speed_km_s"]
    channel = backend.components.ClassicalChannel("cross-validation-classical", delay=propagation_s * 1e9)
    receptions = []
    marker = "orionq-cross-validation-marker"

    def receive(message):
        if marker in message.items:
            receptions.append(_now(backend))

    channel.ports["recv"].bind_output_handler(receive)
    ns.sim_run(duration=parameters["send_time_s"] * 1e9)
    sent_at = _now(backend)
    channel.ports["send"].tx_input(marker)
    ns.sim_run(duration=propagation_s * 1e9 + 1.)
    if len(receptions) != 1 or receptions[0] < sent_at:
        raise ValueError("classical arrival absent, duplicated or acausal")
    return ({"arrival_time_s": receptions[0], "delay_s": receptions[0] - sent_at},
            {"arrival_time_s": "s", "delay_s": "s"},
            [{"stage": "classical_send", "time_s": sent_at},
             {"stage": "classical_receive", "time_s": receptions[0]}],
            ["ClassicalChannel constant propagation delay and native event clock; no jitter or packet loss.",
             "This verifies a reduced send/receive chronology, not the entire distributed heralding protocol."])


RUNNERS = {"transmission": _transmission, "memory": _memory, "swapping": _swapping,
           "purification": _purification, "classical_delay": _classical_delay}


def _case_shell(case, status, reason=None):
    result = {"id": case["id"], "kind": case["kind"], "status": status,
              "metrics": {}, "units": {}, "events": [], "limitations": list(COMMON_LIMITATIONS)}
    if reason:
        result["reason"] = reason
    return result


def run_request(request):
    validate_request(request)
    raw = json.dumps(request, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    report = {
        "schema": OUTPUT_SCHEMA, "backend": "netsquid",
        "metadata": {"seed": request["seed"], "request_sha256": hashlib.sha256(raw).hexdigest(),
                     "runner_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                     "python_version": platform.python_version(), "platform": platform.platform(),
                     "generated_utc": datetime.now(timezone.utc).isoformat(),
                     "native_api_execution_demonstrated": False,
                     "sdk_version": None, "source": "independent_netSquid_native_components"},
        "cases": [],
    }
    try:
        backend = _load_backend()
    except Exception as exc:
        reason = f"NetSquid SDK unavailable or import failed ({type(exc).__name__}); native execution not demonstrated."
        report["metadata"]["availability"] = "not_demonstrated"
        report["metadata"]["reason"] = reason
        report["cases"] = [_case_shell(case, "not_demonstrated", reason) for case in request["cases"]]
        return report
    report["metadata"]["sdk_version"] = backend.version
    report["metadata"]["availability"] = "imported"
    for index, case in enumerate(request["cases"]):
        result = _case_shell(case, "not_demonstrated")
        case_seed = (request["seed"] + index) % 2**32
        result["seed"] = case_seed
        try:
            _reset(backend, case_seed)
            metrics, units, events, limitations = RUNNERS[case["kind"]](backend, case["parameters"])
            # Serializability/finite numbers are required before presenting results.
            json.dumps(metrics, allow_nan=False)
            json.dumps(events, allow_nan=False)
            result.update(status="pass", metrics=metrics, units=units, events=events,
                          limitations=result["limitations"] + limitations)
        except Exception as exc:
            result["reason"] = (f"Native NetSquid case could not be completed ({type(exc).__name__}); "
                                "API compatibility and results are not demonstrated.")
        finally:
            try:
                backend.ns.sim_reset()
            except Exception:
                result = _case_shell(case, "error", "NetSquid reset failed; no scientific result retained.")
        report["cases"].append(result)
    report["metadata"]["native_api_execution_demonstrated"] = any(
        case["status"] == "pass" for case in report["cases"])
    report["metadata"]["all_cases_executed"] = all(case["status"] == "pass" for case in report["cases"])
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path,
                        help="New JSON evidence file; existing files are never overwritten.")
    args = parser.parse_args(argv)
    if args.input.stat().st_size > MAX_INPUT_BYTES:
        parser.error("input exceeds 1 MiB")
    try:
        request = json.loads(args.input.read_text(encoding="utf-8"))
        report = run_request(request)
    except (ValueError, json.JSONDecodeError) as exc:
        parser.error(str(exc))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(report, stream, indent=2, sort_keys=True, allow_nan=False)
        stream.write("\n")
    # A successful transport is not scientific acceptance: inspect each case status.
    print(json.dumps({"backend": "netsquid", "cases": len(report["cases"]),
                      "statuses": {status: sum(case["status"] == status for case in report["cases"])
                                   for status in ("pass", "not_demonstrated", "error")}}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
