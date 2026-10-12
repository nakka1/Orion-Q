"""Isolated reference runner using the pinned, unmodified SeQUeNCe framework.

No OrionQ physical model or compatibility shim is imported here.  The input is
``orionq.cross-simulator-input.v1`` and the output is an evidence record, not a
claim that SeQUeNCe agrees with the system under test.  Comparison belongs to
the caller.  Times passed to the framework are integer picoseconds.

Memory conversion is specified before measurement: two isotropic Pauli memories
have pair visibility exp(-8*t/(3*T_framework)); hence T_framework=8*T_pair/3
represents OrionQ's declared pair visibility lifetime, not the same hardware T.
Ideal purification uses bilateral CNOTs and measured outcomes from the actual
density manager.  A nonzero post-selection operation error is not equated with
SeQUeNCe's pre-selection noisy-gate model.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib
import importlib.metadata
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import time
from typing import Any

PINNED_COMMIT = "3133c66d80a898c6c3cc9af9eec3fa94fdbe2233"
INPUT_SCHEMA = "orionq.cross-simulator-input.v1"
OUTPUT_SCHEMA = "orionq.cross-simulator-output.v1"
PS_PER_SECOND = 10**12
SUPPORTED_KINDS = {"transmission", "memory", "swapping", "purification", "classical_delay"}


def _number(parameters, name, *, low=0.0, high=None, default=None):
    value = parameters.get(name, default)
    if type(value) not in (float, int) or not math.isfinite(value):
        raise ValueError(f"{name} must be a finite number")
    if value < low or (high is not None and value > high):
        raise ValueError(f"{name} is outside its valid interval")
    return float(value)


def _trials(parameters):
    value = parameters.get("trials", 2048)
    if type(value) is not int or not 1 <= value <= 100000:
        raise ValueError("trials must be an integer in [1, 100000]")
    return value


def validate_payload(payload):
    """Validate input without importing any numerical or simulator dependency."""
    if not isinstance(payload, dict) or payload.get("schema") != INPUT_SCHEMA:
        raise ValueError(f"input schema must be {INPUT_SCHEMA}")
    if type(payload.get("seed")) is not int or not 0 <= payload["seed"] < 2**63:
        raise ValueError("seed must be an integer in [0, 2**63)")
    cases = payload.get("cases")
    if not isinstance(cases, list) or not 1 <= len(cases) <= 1000:
        raise ValueError("cases must be a nonempty list of at most 1000 entries")
    ids = set()
    for case in cases:
        if not isinstance(case, dict):
            raise ValueError("each case must be an object")
        case_id = case.get("id")
        if not isinstance(case_id, str) or not 1 <= len(case_id) <= 128 or case_id in ids:
            raise ValueError("case IDs must be unique nonempty strings")
        ids.add(case_id)
        if not isinstance(case.get("kind"), str) or not isinstance(case.get("parameters"), dict):
            raise ValueError("case kind and parameters must be a string and object")
        p, kind = case["parameters"], case["kind"]
        if kind not in SUPPORTED_KINDS:
            continue  # Explicitly not demonstrated; never substitute a local model.
        if kind == "transmission":
            _number(p, "length_km")
            _number(p, "attenuation_db_km")
            _number(p, "insertion_db", default=0)
            _trials(p)
        elif kind == "memory":
            _number(p, "fidelity", high=1)
            _number(p, "elapsed_s")
            if _number(p, "coherence_s") <= 0:
                raise ValueError("coherence_s must be positive")
        elif kind in {"swapping", "purification"}:
            _number(p, "first", high=1)
            _number(p, "second", high=1)
            _number(p, "operation_error", high=1, default=0)
            _trials(p)
        elif kind == "classical_delay":
            _number(p, "distance_km")
            if _number(p, "light_speed_km_s") <= 0:
                raise ValueError("light_speed_km_s must be positive")
            _number(p, "send_time_s", default=0)


def _git(source, *arguments):
    return subprocess.check_output(
        ["git", "-c", f"safe.directory={source.as_posix()}", "-C", str(source), *arguments],
        text=True, stderr=subprocess.STDOUT, timeout=30,
    ).strip()


def _load_sequence(source):
    source = Path(source).resolve()
    if not (source / "sequence" / "__init__.py").is_file():
        raise RuntimeError("sequence source checkout not found")
    commit = _git(source, "rev-parse", "HEAD")
    if commit != PINNED_COMMIT:
        raise RuntimeError(f"SeQUeNCe commit mismatch: expected {PINNED_COMMIT}, found {commit}")
    changed = _git(source, "status", "--porcelain", "--untracked-files=no")
    if changed:
        raise RuntimeError("SeQUeNCe checkout contains tracked changes; independent reference refused")
    sys.path.insert(0, str(source))
    module = importlib.import_module("sequence")
    if Path(module.__file__).resolve() != source / "sequence" / "__init__.py":
        raise RuntimeError("a different SeQUeNCe distribution was already imported")
    imports = {
        "timeline": "sequence.kernel.timeline", "constants": "sequence.constants",
        "memory": "sequence.components.memory", "node": "sequence.topology.node",
        "channel": "sequence.components.optical_channel", "photon": "sequence.components.photon",
        "event": "sequence.kernel.event", "process": "sequence.kernel.process",
        "circuit": "sequence.components.circuit", "manager": "sequence.kernel.quantum_manager",
        "utils": "sequence.kernel.quantum_utils", "manager_utils": "sequence.kernel.quantum_manager.utils",
        "swapping": "sequence.entanglement_management.swapping.swapping_circuit",
        "swapping_bds": "sequence.entanglement_management.swapping.swapping_bds",
        "purification": "sequence.entanglement_management.purification.bbpssw_circuit",
        "purification_bds": "sequence.entanglement_management.purification.bbpssw_bds",
        "message": "sequence.message", "numpy": "numpy", "qutip": "qutip",
    }
    api = {key: importlib.import_module(name) for key, name in imports.items()}
    # Loading these real dependencies is intentional; no fallback shim is allowed.
    versions = {name: importlib.metadata.version(name) for name in
                ("numpy", "scipy", "qutip", "qutip-qip", "gmpy2", "stim")}
    hashes = {str(p.relative_to(source).as_posix()): hashlib.sha256(p.read_bytes()).hexdigest()
              for p in sorted((source / "sequence").rglob("*.py"))}
    metadata = {
        "version": module.__version__, "commit": commit, "source": str(source),
        "source_sha256": hashes, "dependencies": versions, "shims": [],
        "python": sys.version, "product_physics_imported": False,
        "memory_mapping": "T_framework_s=(8/3)*coherence_s; independent isotropic Pauli noise on both ends",
        "scope": "Scalar fibre loss, Werner pair memory, ideal density-matrix BSM/BBPSSW, lossless classical latency",
    }
    return api, metadata


def _timeline(api, formalism=None):
    return api["timeline"].Timeline(formalism=formalism or api["constants"].DENSITY_MATRIX_FORMALISM)


def _receiver_type(api):
    class ObservationNode(api["node"].Node):
        def __init__(self, name, timeline, seed):
            super().__init__(name, timeline, seed=seed)
            self.observations = []

        def receive_qubit(self, src, qubit):
            self.observations.append({"event": "photon_received", "time_ps": int(self.timeline.now()),
                                      "source": src, "photon": qubit.name})

        def receive_message(self, src, msg):
            self.observations.append({"event": "classical_received", "time_ps": int(self.timeline.now()),
                                      "source": src, "receiver": msg.receiver})
    return ObservationNode


def _transmission(api, p, seed):
    length_m = p["length_km"] * 1000.0
    insertion = p.get("insertion_db", 0.0)
    if length_m == 0 and insertion != 0:
        return {}, {}, [], ["A linear QuantumChannel cannot encode fixed insertion loss at zero length"], "not_demonstrated"
    attenuation = p["attenuation_db_km"] / 1000.0
    if length_m:
        attenuation += insertion / length_m
    timeline = _timeline(api)
    node_type = _receiver_type(api)
    sender, receiver = node_type("source", timeline, seed), node_type("sink", timeline, seed + 1)
    channel = api["channel"].QuantumChannel("fibre", timeline, attenuation=attenuation, distance=length_m)
    channel.set_ends(sender, receiver.name)
    timeline.init()
    trials = _trials(p)
    # Real framework channel samples photon survival and schedules receive events.
    for index in range(trials):
        photon = api["photon"].Photon(f"photon-{index}", timeline, wavelength=1550, location=sender)
        sender.send_qubit(receiver.name, photon)
    before = len(receiver.observations)
    timeline.run()
    count = len(receiver.observations)
    metrics = {"transmission_probability": float(1.0 - channel.loss), "transmitted": count,
               "trials": trials, "observed_transmission_probability": count / trials,
               "delay_s": channel.delay / PS_PER_SECOND, "received_before_run": before}
    events = [{"event": "photon_emissions", "time_ps": 0, "count": trials},
              {"event": "photon_receive_batch", "time_ps": channel.delay, "count": count},
              {"event": "framework_events_executed", "count": timeline.run_counter}]
    limits = ["Photon pulses are unqueued component-level trials; no detector, heralding or optical BSM is claimed",
              "Fixed insertion loss is distributed over nonzero fibre length solely for scalar probability equivalence"]
    return metrics, {key: "1" for key in metrics} | {"delay_s": "s"}, events, limits, "pass"


def _memory(api, p, seed):
    timeline = _timeline(api, api["constants"].BELL_DIAGONAL_STATE_FORMALISM)
    owner = api["node"].Node("memory-owner", timeline, seed=seed)
    framework_coherence = 8.0 * p["coherence_s"] / 3.0
    memories = [api["memory"].Memory(f"memory-{i}", timeline, fidelity=1, frequency=0,
                    efficiency=1, coherence_time=framework_coherence, wavelength=1550,
                    decoherence_errors=[1/3, 1/3, 1/3], cutoff_flag=False) for i in range(2)]
    for memory in memories:
        memory.owner = owner
    timeline.init()
    state = [p["fidelity"]] + [(1 - p["fidelity"]) / 3] * 3
    timeline.quantum_manager.set([m.qstate_key for m in memories], state)
    for memory in memories:
        memory.last_update_time = 1
    target_ps = 1 + int(round(p["elapsed_s"] * PS_PER_SECOND))
    for memory in memories:
        timeline.schedule(api["event"].Event(target_ps, api["process"].Process(memory, "bds_decohere", [])))
    timeline.run()
    final = list(map(float, timeline.quantum_manager.get(memories[0].qstate_key).state))
    metrics = {"fidelity": final[0], "elapsed_s": (timeline.now() - 1) / PS_PER_SECOND,
               "framework_coherence_s": framework_coherence, "state_normalization": sum(final)}
    events = [{"event": "pair_initialized", "time_ps": 1, "bell_probabilities": state},
              {"event": "both_memory_channels_applied", "time_ps": target_ps, "count": timeline.run_counter,
               "bell_probabilities": final}]
    return metrics, {"fidelity": "1", "elapsed_s": "s", "framework_coherence_s": "s", "state_normalization": "1"}, events, [
        "coherence_s is a pair visibility lifetime, not an unconverted physical single-memory coherence time",
        "No amplitude damping, memory-write/read loss or finite expiry is represented in this reduced case"], "pass"


def _werner_density(api, fidelity):
    np, c = api["numpy"], api["constants"]
    vectors = [np.asarray(v, dtype=complex) for v in (c.PHI_PLUS, c.PHI_MINUS, c.PSI_PLUS, c.PSI_MINUS)]
    weights = [fidelity] + [(1 - fidelity) / 3] * 3
    return sum(w * np.outer(v, v.conj()) for w, v in zip(weights, vectors))


def _dense_setup(api, first, second, kind):
    manager = api["manager"].QuantumManagerDensity()
    keys = [manager.new() for _ in range(4)]
    manager.set(keys[:2], _werner_density(api, first))
    manager.set(keys[2:], _werner_density(api, second))
    Circuit = api["circuit"].Circuit
    if kind == "swapping":
        circuit = api["swapping"].EntanglementSwappingA_Circuit.circuit
        measured = [keys[1], keys[2]]
        retained = [keys[0], keys[3]]
    else:
        circuit = Circuit(4)
        # Bilateral CNOT: source pair controls, sacrifice pair targets.
        for control, target in ((0, 2), (1, 3)):
            for gate, indices, argument in api["purification"].BBPSSWCircuit.circuit.gates:
                if gate != "cx" or argument is not None:
                    raise RuntimeError("upstream BBPSSW circuit no longer matches declared CNOT contract")
                circuit.cx((control, target)[indices[0]], (control, target)[indices[1]])
        circuit.measure(2)
        circuit.measure(3)
        measured, retained = keys[2:], keys[:2]
    circuit_keys = measured if kind == "swapping" else keys
    return manager, keys, circuit, circuit_keys, measured, retained


def _outcome_probabilities(api, manager, circuit, circuit_keys):
    # This is the same pinned framework preparation/projector implementation used
    # by run_circuit, used here for exact branch weights rather than a recurrence.
    state, all_keys, unitary = manager._prepare_circuit(circuit, circuit_keys)
    state = unitary @ state @ unitary.conj().T
    measured = [all_keys[i] for i in circuit.measured_qubits]
    # The pinned framework's swap_qubits has no identity-case shortcut and
    # qutip-qip 0.4.2 returns scalar 1 for an empty gate product. run_circuit's
    # own _measure likewise skips this helper when the keys are already first.
    reordered = all_keys
    if all_keys[:len(measured)] != measured:
        reordered, swap = api["manager_utils"].swap_qubits(all_keys, measured)
        state = swap @ state @ swap.conj().T
    _, probabilities = api["utils"].measure_multiple_with_cache_density(
        tuple(map(tuple, state)), len(measured), len(reordered) - len(measured))
    return list(map(float, probabilities))


def _retained_fidelity(api, manager, retained):
    state = manager.get(retained[0])
    indices = [state.keys.index(key) for key in retained]
    density = api["qutip"].Qobj(state.state, dims=[[2] * len(state.keys)] * 2).ptrace(indices)
    bell = api["qutip"].Qobj(api["constants"].PHI_PLUS, dims=[[2, 2], [1]])
    return float((bell.dag() * density * bell).real)


def _bds_result(api, p, kind, seed):
    timeline = _timeline(api, api["constants"].BELL_DIAGONAL_STATE_FORMALISM)
    owner = api["node"].Node("A", timeline, seed=seed,
               gate_fid=1-p.get("operation_error", 0) if kind == "swapping" else 1, meas_fid=1)
    remote = api["node"].Node("B", timeline, seed=seed + 1)
    memories = [api["memory"].Memory(f"bds-{i}", timeline, fidelity=1, frequency=0,
                    efficiency=1, coherence_time=-1, wavelength=1550) for i in range(4)]
    timeline.init()
    for pair, fidelity in ((memories[:2], p["first"]), (memories[2:], p["second"])):
        timeline.quantum_manager.set([m.qstate_key for m in pair], [fidelity] + [(1-fidelity)/3]*3)
    if kind == "swapping":
        protocol = api["swapping_bds"].EntanglementSwappingA_BDS(owner, "swap", memories[0], memories[2])
        return 1.0, float(protocol.swapping_res()[0])
    protocol = api["purification_bds"].BBPSSW_BDS(owner, "purify", memories[0], memories[2])
    protocol.set_others("remote-purify", remote.name, [memories[1].name, memories[3].name])
    probability, state = protocol.purification_res()
    return float(probability), float(state[0])


def _quantum_operation(api, p, kind, seed):
    error = p.get("operation_error", 0)
    if error != 0 and kind == "purification":
        return {}, {}, [], ["OrionQ post-selection depolarization is not SeQUeNCe pre-selection noisy CNOT/readout; no arbitrary mapping"], "not_demonstrated"
    if error != 0:
        probability, fidelity = _bds_result(api, p, kind, seed)
        return {"fidelity": fidelity, "success_probability": probability}, {"fidelity": "1", "success_probability": "1"}, [
            {"event": "bds_swapping_result", "gate_fidelity": 1-error, "measurement_fidelity": 1}], [
            "Nonzero operation_error uses SeQUeNCe's BDS depolarizing gate model; not a calibrated instrument"], "pass"
    manager, _, circuit, circuit_keys, measured, retained = _dense_setup(api, p["first"], p["second"], kind)
    probabilities = _outcome_probabilities(api, manager, circuit, circuit_keys)
    weighted_fidelity, success, cumulative, branches = 0.0, 0.0, 0.0, []
    for index, probability in enumerate(probabilities):
        sample = cumulative + probability / 2
        cumulative += probability
        if probability <= 0:
            continue
        branch_manager, _, branch_circuit, branch_keys, branch_measured, branch_retained = _dense_setup(
            api, p["first"], p["second"], kind)
        observed = branch_manager.run_circuit(branch_circuit, branch_keys, sample)
        bits = [observed[key] for key in branch_measured]
        accepted = kind == "swapping" or bits[0] == bits[1]
        if kind == "swapping":
            correction = api["circuit"].Circuit(1)
            if bits[1]:
                correction.x(0)
            if bits[0]:
                correction.z(0)
            branch_manager.run_circuit(correction, [branch_retained[1]])
        fidelity = _retained_fidelity(api, branch_manager, branch_retained)
        if accepted:
            success += probability
            weighted_fidelity += probability * fidelity
        branches.append({"outcome": bits, "probability": probability, "accepted": accepted,
                         "retained_fidelity": fidelity})
    if abs(sum(probabilities)-1) > 1e-9 or success <= 0:
        raise RuntimeError("invalid framework outcome distribution")
    fidelity = weighted_fidelity / success
    # Each trial calls SeQUeNCe measurement, rather than drawing a Bernoulli
    # against a final success probability or filling final fidelity directly.
    rng = api["numpy"].random.default_rng(seed)
    accepted_count = 0
    trial_count = _trials(p)
    prepared_states = {key: manager.get(key) for key in manager.states}
    for sample in rng.random(trial_count):
        manager.states = dict(prepared_states)
        outcomes = manager.run_circuit(circuit, circuit_keys, float(sample))
        bits = [outcomes[key] for key in measured]
        accepted_count += int(kind == "swapping" or bits[0] == bits[1])
    bds_probability, bds_fidelity = _bds_result(api, p, kind, seed)
    metrics = {"fidelity": fidelity, "success_probability": success, "accepted": accepted_count,
               "trials": trial_count, "observed_success_probability": accepted_count / trial_count,
               "framework_bds_fidelity": bds_fidelity, "framework_bds_success_probability": bds_probability,
               "framework_formalism_fidelity_difference": abs(bds_fidelity-fidelity)}
    events = [{"event": "input_pairs", "count": 2, "fidelity": [p["first"], p["second"]]},
              {"event": "density_matrix_measurement_branches", "branches": branches},
              {"event": "framework_measurement_trials", "count": trial_count, "accepted": accepted_count},
              {"event": "resource_accounting", "input_pairs_per_trial": 2, "maximum_retained_pairs": 1,
               "discarded_measured_qubits": 2}]
    limits = ["Ideal local gates and complete two-qubit measurement; not a linear-optical BSM efficiency claim",
              "Density-manager circuit/readout validation; full network reservation/routing is outside this component case",
              "Purified output is generally Bell diagonal; its fidelity is preserved by later Werner twirling"]
    return metrics, {key: "1" for key in metrics}, events, limits, "pass"


def _classical_delay(api, p, seed):
    timeline = _timeline(api)
    node_type = _receiver_type(api)
    sender, receiver = node_type("sender", timeline, seed), node_type("receiver", timeline, seed + 1)
    channel = api["channel"].ClassicalChannel("control", timeline, distance=p["distance_km"] * 1000)
    # Upstream ClassicalChannel supports explicit delay and defaults to fibre
    # speed. Set distance/speed before transmission to preserve caller medium.
    channel.delay = int(round(p["distance_km"] / p["light_speed_km_s"] * PS_PER_SECOND))
    channel.set_ends(sender, receiver.name)
    timeline.init()
    send_ps = int(round(p.get("send_time_s", 0) * PS_PER_SECOND))
    message = api["message"].Message("herald", "consumer")
    timeline.schedule(api["event"].Event(send_ps, api["process"].Process(
        sender, "send_message", [receiver.name, message])))
    expected_ps = send_ps + channel.delay
    timeline.stop_time = expected_ps
    timeline.run()
    before = len(receiver.observations)
    timeline.stop_time = expected_ps + 1
    timeline.run()
    if len(receiver.observations) != 1:
        raise RuntimeError("classical message was not received exactly once")
    observed = receiver.observations[0]["time_ps"]
    if observed != expected_ps:
        raise RuntimeError("classical receive timestamp disagrees with scheduled propagation")
    return {"arrival_time_s": observed / PS_PER_SECOND, "delay_s": (observed - send_ps) / PS_PER_SECOND,
            "received_before_arrival": before}, {"arrival_time_s": "s", "delay_s": "s", "received_before_arrival": "1"}, [
        {"event": "classical_sent", "time_ps": send_ps},
        {"event": "before_arrival_barrier", "time_ps": expected_ps, "received": before},
        receiver.observations[0]], ["ClassicalChannel is lossless; encryption, authentication and jitter are not modeled here"], "pass"


def run(payload, sequence_src):
    validate_payload(payload)
    started = time.perf_counter()
    api, metadata = _load_sequence(sequence_src)
    cases = []
    for case in payload["cases"]:
        row: dict[str, Any] = {"id": case["id"], "kind": case["kind"], "parameters": case["parameters"]}
        try:
            kind, p = case["kind"], case["parameters"]
            if kind == "transmission":
                result = _transmission(api, p, payload["seed"])
            elif kind == "memory":
                result = _memory(api, p, payload["seed"])
            elif kind in {"swapping", "purification"}:
                result = _quantum_operation(api, p, kind, payload["seed"])
            elif kind == "classical_delay":
                result = _classical_delay(api, p, payload["seed"])
            else:
                result = ({}, {}, [], ["No compatible component reference in this runner"], "not_demonstrated")
            metrics, units, events, limitations, status = result
            row.update(status=status, metrics=metrics, units=units, events=events, limitations=limitations)
        except Exception as error:
            row.update(status="error", metrics={}, units={}, events=[], limitations=[],
                       error={"type": type(error).__name__, "message": str(error)})
        cases.append(row)
    return {"schema": OUTPUT_SCHEMA, "backend": "sequence", "seed": payload["seed"],
            "metadata": metadata, "cases": cases, "elapsed_s": time.perf_counter()-started,
            "passed": all(row["status"] == "pass" for row in cases),
            "physical_hardware_validated": False, "system_trl_assigned": False}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--sequence-src", default=os.environ.get("SEQUENCE_SRC", "C:/SeQUeNCe"))
    args = parser.parse_args(argv)
    payload = json.loads(args.input.read_text(encoding="utf-8"))
    report = run(payload, args.sequence_src)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    # Evidence is immutable: never overwrite a prior run.
    with args.output.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(report, stream, indent=2, ensure_ascii=False, allow_nan=False)
        stream.write("\n")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
