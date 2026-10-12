"""Independent, pinned SeQUeNCe network runner and classical-hop observer.

This module imports no OrionQ implementation. RouterNetTopo, native RSVP,
resource rules, Barrett--Kok photon generation, memory, purification and swapping
execute on the real framework timeline. A census is not a validation claim:
unsupported technologies and parallel device banks remain explicitly uncovered.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict, deque
from dataclasses import asdict
from decimal import Decimal
import gzip
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

PINNED_COMMIT = "3133c66d80a898c6c3cc9af9eec3fa94fdbe2233"
INPUT_SCHEMA = "orionq.sequence-network-input.v1"
OUTPUT_SCHEMA = "orionq.sequence-network-output.v1"
PS_PER_SECOND = 10**12
NATIVE_PROTOCOLS = {"entanglement", "repeater", "entanglement_swapping", "bbpssw", "quantum_memory", "barrett_kok"}


def _number(data, name, *, minimum=0, maximum=None, default=None):
    value = data.get(name, default)
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError(f"{name} must be a finite number")
    if value < minimum or (maximum is not None and value > maximum):
        raise ValueError(f"{name} outside valid range")
    return float(value)


def _integer(data, name, minimum, maximum, default=None):
    value = data.get(name, default)
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError(f"{name} must be an integer in [{minimum}, {maximum}]")
    return value


def _id(value, label):
    if not isinstance(value, str) or not 1 <= len(value) <= 200 or "\n" in value:
        raise ValueError(f"{label} must be a nonempty identifier")
    return value


def _ps(seconds):
    return int((Decimal(str(seconds)) * PS_PER_SECOND).to_integral_value())


def validate_payload(payload):
    if not isinstance(payload, dict) or payload.get("schema") != INPUT_SCHEMA:
        raise ValueError(f"input schema must be {INPUT_SCHEMA}")
    _id(payload.get("scenario_id"), "scenario_id")
    topology_hash = payload.get("topology_sha256")
    if not isinstance(topology_hash, str) or len(topology_hash) != 64 or any(c not in "0123456789abcdef" for c in topology_hash):
        raise ValueError("topology_sha256 must be a lowercase SHA256")
    _integer(payload, "seed", 0, 2**63-1)
    if payload.get("formalism", "density_matrix") not in {"density_matrix", "ket_vector"}:
        raise ValueError("formalism must be density_matrix or ket_vector")
    duration = _number(payload, "duration_s", minimum=1e-6, maximum=3600)
    nodes = payload.get("nodes")
    links = payload.get("links")
    demands = payload.get("demands")
    if not isinstance(nodes, list) or not 1 <= len(nodes) <= 1000:
        raise ValueError("nodes must contain 1..1000 entries")
    if not isinstance(links, list) or len(links) > 5000:
        raise ValueError("links must contain at most 5000 entries")
    if not isinstance(demands, list) or len(demands) > 10000:
        raise ValueError("demands must contain at most 10000 entries")
    names = set()
    for node in nodes:
        if not isinstance(node, dict):
            raise ValueError("each node must be an object")
        name = _id(node.get("id"), "node.id")
        if name in names:
            raise ValueError("duplicate node id")
        names.add(name)
        if type(node.get("native_eligible", True)) is not bool:
            raise ValueError("native_eligible must be a bool")
        if node.get("native_eligible", True):
            _integer(node, "memory_count", 0, 1000)
            _number(node, "memory_frequency_hz", minimum=1e-9)
            _number(node, "memory_coherence_s", minimum=1e-12)
            _number(node, "memory_efficiency", maximum=1)
            _number(node, "raw_fidelity", maximum=1)
            _number(node, "gate_fidelity", maximum=1, default=1)
            _number(node, "measurement_fidelity", maximum=1, default=1)
            _number(node, "swapping_success_probability", maximum=1, default=1)
            _number(node, "wavelength_nm", minimum=1e-12, default=1550)
    ids = set()
    for link in links:
        if not isinstance(link, dict):
            raise ValueError("each link must be an object")
        name = _id(link.get("id"), "link.id")
        if name in ids:
            raise ValueError("duplicate link id")
        ids.add(name)
        if link.get("a") not in names or link.get("b") not in names or link["a"] == link["b"]:
            raise ValueError("link endpoints must be distinct known nodes")
        for flag in ("enabled", "classical_enabled", "selected"):
            if flag in link and type(link[flag]) is not bool:
                raise ValueError(f"{flag} must be a bool")
        _id(link.get("protocol"), "link.protocol")
        _number(link, "length_km")
        _number(link, "attenuation_db_km")
        _number(link, "insertion_loss_db", default=0)
        _number(link, "classical_delay_s", minimum=1e-12)
    ids = set()
    for demand in demands:
        if not isinstance(demand, dict):
            raise ValueError("each demand must be an object")
        name = _id(demand.get("id"), "demand.id")
        if name in ids:
            raise ValueError("duplicate demand id")
        ids.add(name)
        if demand.get("source") not in names or demand.get("target") not in names or demand["source"] == demand["target"]:
            raise ValueError("demand endpoints must be distinct known nodes")
        request = _number(demand, "request_s", default=0)
        start = _number(demand, "start_s")
        span = _number(demand, "duration_s", minimum=1e-12)
        if request >= start or start + span >= duration:
            raise ValueError("request must precede start and demand must finish before timeline stop")
        _integer(demand, "memory_count", 1, 1000)
        _number(demand, "target_fidelity", minimum=1e-12, maximum=1)
    disruptions = payload.get("disruptions", [])
    if not isinstance(disruptions, list):
        raise ValueError("disruptions must be a list")
    for disruption in disruptions:
        if not isinstance(disruption, dict):
            raise ValueError("each disruption must be an object")
        if disruption.get("node_id") not in names or type(disruption.get("down")) is not bool:
            raise ValueError("disruption must identify a known node and boolean down")
        _number(disruption, "time_s", maximum=duration)
    detector = payload.get("detector", {})
    if not isinstance(detector, dict):
        raise ValueError("detector must be an object")
    _number(detector, "efficiency", maximum=1, default=.8)
    _number(detector, "dark_count_hz", default=0)
    _number(detector, "count_rate_hz", minimum=1e-12, default=5e7)
    _integer(detector, "time_resolution_ps", 1, 10**12, default=100)


def _git(source, *arguments):
    return subprocess.check_output(["git", "-c", f"safe.directory={source.as_posix()}", "-C", str(source), *arguments],
                                   text=True, stderr=subprocess.STDOUT, timeout=30).strip()


def load_native(source=None):
    """Load the real, clean pinned checkout; never install a compatibility shim."""
    if any(name == "quantum_autopilot" or name.startswith("quantum_autopilot.") for name in sys.modules):
        raise RuntimeError("product implementation is loaded; independent native process required")
    source = Path(source or os.environ.get("SEQUENCE_SRC", "C:/SeQUeNCe")).resolve()
    if not (source / "sequence" / "__init__.py").is_file():
        raise RuntimeError("SeQUeNCe checkout is unavailable")
    if _git(source, "rev-parse", "HEAD") != PINNED_COMMIT:
        raise RuntimeError("SeQUeNCe commit does not match the frozen reference")
    if _git(source, "status", "--porcelain", "--untracked-files=no"):
        raise RuntimeError("SeQUeNCe tracked source is modified")
    sys.path.insert(0, str(source))
    module = importlib.import_module("sequence")
    if Path(module.__file__).resolve() != source / "sequence" / "__init__.py":
        raise RuntimeError("another SeQUeNCe distribution was already imported")
    names = {"topology": "sequence.topology.router_net_topo", "app": "sequence.app.request_app",
             "timeline": "sequence.kernel.timeline", "event": "sequence.kernel.event", "process": "sequence.kernel.process",
             "node": "sequence.topology.node", "channel": "sequence.components.optical_channel",
             "message": "sequence.message", "metrics": "sequence.utils.metrics", "constants": "sequence.constants",
             "metric_types": "sequence.utils.metrics.metric_types", "networkx": "networkx", "qutip": "qutip"}
    api = {key: importlib.import_module(name) for key, name in names.items()}
    metadata = {"commit": PINNED_COMMIT, "version": module.__version__, "source": str(source), "python": sys.version,
                "dependencies": {n: importlib.metadata.version(n) for n in ("numpy", "scipy", "networkx", "qutip", "qutip-qip", "gmpy2", "stim")},
                "source_sha256": {p.relative_to(source).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                                  for p in sorted((source / "sequence").rglob("*.py"))},
                "product_imports": [], "product_physics_imported": False, "shims": [], "physical_action_performed": False}
    return api, metadata


def run_classical_probe(request, api=None, source=None):
    """Observe a real ClassicalChannel hop from supplied latency terms.

    Latency terms are inputs, not independently qualified radio/optical physics.
    The lossless native channel neither fabricates quantum detections nor keys.
    """
    if not isinstance(request, dict):
        raise ValueError("probe request must be an object")
    for name in ("command_id", "sender", "receiver"):
        _id(request.get(name), name)
    if request["sender"] == request["receiver"]:
        raise ValueError("probe endpoints must be distinct")
    emitted = _number(request, "emitted_at_s")
    propagation = _number(request, "propagation_s")
    serialization = _number(request, "serialization_s")
    jitter = _number(request, "jitter_s", minimum=-10, default=0)
    delay = propagation + serialization + jitter
    if delay < 0:
        raise ValueError("total channel delay cannot be negative")
    if not isinstance(request.get("payload"), (dict, str)):
        raise ValueError("probe payload must be a JSON object or serialized envelope string")
    # JSON compatibility and finiteness are checked before any native event.
    payload = json.loads(json.dumps(request["payload"], allow_nan=False))
    api = api or load_native(source)[0]
    emitted_ps, delay_ps = _ps(emitted), _ps(delay)
    timeline = api["timeline"].Timeline(stop_time=emitted_ps + delay_ps + 1000,
                                        formalism=api["constants"].KET_VECTOR_FORMALISM)
    events = []

    class ProbeNode(api["node"].Node):
        def receive_message(self, src, message):
            events.append({"event": "classical_received", "time_ps": int(timeline.now()), "sender": src,
                           "receiver": self.name, "command_id": request["command_id"], "payload": message.payload})

        def emit_probe(self, message):
            events.append({"event": "classical_sent", "time_ps": int(timeline.now()), "sender": self.name,
                           "receiver": request["receiver"], "command_id": request["command_id"]})
            self.send_message(request["receiver"], message)

    sender = ProbeNode(request["sender"], timeline, seed=0)
    receiver = ProbeNode(request["receiver"], timeline, seed=1)
    channel = api["channel"].ClassicalChannel("observed-control-hop", timeline, distance=0, delay=delay_ps)
    channel.set_ends(sender, receiver.name)
    message = api["message"].Message("probe", "sequence-network-probe")
    message.payload = payload
    timeline.init()
    timeline.schedule(api["event"].Event(emitted_ps, api["process"].Process(sender, "emit_probe", [message])))
    timeline.run()
    if len(events) != 2 or events[1]["time_ps"] != emitted_ps + delay_ps:
        raise RuntimeError("native classical probe did not produce a causal receipt")
    return {"schema": "orionq.sequence-classical-probe.v1", "command_id": request["command_id"],
            "sent_at_s": events[0]["time_ps"] / PS_PER_SECOND, "received_at_s": events[1]["time_ps"] / PS_PER_SECOND,
            "sent_at_ps": events[0]["time_ps"], "received_at_ps": events[1]["time_ps"], "payload": events[1]["payload"],
            "events": events, "native_module": "sequence.components.optical_channel.ClassicalChannel",
            "native_commit": PINNED_COMMIT, "timeline_run_counter": timeline.run_counter}


def select_links(payload):
    """One explicitly selected bank per pair; census retains every exclusion."""
    nodes = {n["id"]: n for n in payload["nodes"]}
    grouped, census = defaultdict(list), []
    for link in payload["links"]:
        reason = None
        if not link.get("enabled", True):
            reason = "disabled_quantum_connection"
        elif not link.get("classical_enabled", True):
            reason = "paired_classical_channel_disabled"
        elif not all(nodes[x].get("native_eligible", True) for x in (link["a"], link["b"])):
            reason = "endpoint_has_no_native_technology_mapping"
        elif link["protocol"] not in NATIVE_PROTOCOLS:
            reason = "unsupported_native_protocol"
        elif link["length_km"] == 0:
            reason = "zero_length_midpoint_not_supported"
        item = {"id": link["id"], "a": link["a"], "b": link["b"], "protocol": link["protocol"], "status": "not_demonstrated", "reason": reason,
                "physical_a": link.get("physical_a"), "physical_b": link.get("physical_b"), "resource_bank_id": link.get("resource_bank_id")}
        census.append(item)
        if reason is None:
            grouped[tuple(sorted((link["a"], link["b"])))].append((link, item))
    selected = []
    for items in grouped.values():
        explicit = [item for item in items if item[0].get("selected", False)]
        if len(explicit) > 1:
            raise ValueError("multiple selected native banks for the same endpoint pair")
        winner = explicit[0] if explicit else min(items, key=lambda x: (x[0]["length_km"], x[0]["id"]))
        selected.append(winner[0])
        for item in items:
            if item is winner:
                item[1].update(status="native_instantiated", reason=None, bsm=f"BSM.{item[0]['a']}.{item[0]['b']}")
            else:
                item[1]["reason"] = "parallel_device_bank_not_supported_in_single_RouterNetTopo"
    return selected, census


def run_network(payload, output_path, api=None, metadata=None):
    validate_payload(payload)
    started = time.perf_counter()
    if api is None:
        api, metadata = load_native()
    metadata = metadata or {"commit": PINNED_COMMIT}
    selected, link_census = select_links(payload)
    eligible = [node for node in payload["nodes"] if node.get("native_eligible", True)]
    nx = api["networkx"]
    graph = nx.Graph()
    graph.add_nodes_from(n["id"] for n in eligible)
    graph.add_edges_from((link["a"], link["b"], {"weight": link["classical_delay_s"], "id": link["id"]}) for link in selected)
    templates = {}
    native_nodes = []
    for index, node in enumerate(eligible):
        name = f"node-{index}"
        templates[name] = {"MemoryArray": {"fidelity": node["raw_fidelity"], "frequency": node["memory_frequency_hz"],
                                           "efficiency": node["memory_efficiency"], "coherence_time": node["memory_coherence_s"],
                                           "wavelength": node.get("wavelength_nm", 1550)},
                           "EntanglementSwapping": {"swapping_success_prob": node.get("swapping_success_probability", 1)}}
        native_nodes.append({"name": node["id"], "type": "QuantumRouter", "seed": (payload["seed"] + index) % (2**63),
                             "memo_size": node["memory_count"], "template": name})
    classical = []
    for component in nx.connected_components(graph):
        for origin in sorted(component):
            distances = nx.single_source_dijkstra_path_length(graph, origin)
            for target in sorted(component):
                if origin < target:
                    classical.append({"node1": origin, "node2": target, "delay": max(1, _ps(distances[target]))})
    qconnections = []
    for index, link in enumerate(selected):
        distance_m = link["length_km"] * 1000
        # Extra insertion loss is distributed as an equivalent scalar survival
        # over both half fibres, not assigned to an unmodelled device location.
        attenuation = (link["attenuation_db_km"] * link["length_km"] + link.get("insertion_loss_db", 0)) / distance_m
        qconnections.append({"node1": link["a"], "node2": link["b"], "attenuation": attenuation,
                             "distance": distance_m, "type": "meet_in_the_middle", "seed": (payload["seed"] + 1000 + index) % (2**63)})
    formalism = payload.get("formalism", api["constants"].DENSITY_MATRIX_FORMALISM)
    config = {"nodes": native_nodes, "qconnections": qconnections, "cconnections": classical,
              "qchannels": [], "cchannels": [],
              "stop_time": _ps(payload["duration_s"]), "formalism": formalism,
              "templates": templates}
    topology = api["topology"].RouterNetTopo(config)
    timeline = topology.get_timeline()
    routers = topology.get_nodes_by_type("QuantumRouter")
    by_name = {node.name: node for node in routers}
    for node in routers:
        declared = next(n for n in eligible if n["id"] == node.name)
        node.gate_fid = declared.get("gate_fidelity", 1)
        node.meas_fid = declared.get("measurement_fidelity", 1)
    detector = payload.get("detector", {})
    for node in topology.get_nodes_by_type("BSMNode"):
        bsm = node.get_components_by_type("SingleAtomBSM")[0]
        for name, key, default in (("efficiency", "efficiency", .8), ("dark_count", "dark_count_hz", 0),
                                   ("count_rate", "count_rate_hz", 5e7), ("time_resolution", "time_resolution_ps", 100)):
            bsm.update_detectors_params(name, detector.get(key, default))
    metrics = api["metrics"]
    metrics.configure()
    class CaptureMetric(api["metric_types"].Metric):
        @property
        def event_types(self):
            return frozenset(metrics.list_event_types())

        @property
        def output_keys(self):
            return frozenset()

        def on_record(self, event_type, owner_name, entry):
            data = asdict(entry.data) if entry.data is not None else None
            record("native_metric", native_event=event_type.name, time_ps=int(entry.sim_time), owner=owner_name, data=data)
            observations.append({"event": event_type.name, "time_ps": int(entry.sim_time), "owner": owner_name, "data": data})

        def collect(self, owner_name, storage):
            return {}

    metrics.enable([CaptureMetric(), metrics.EG_METRIC, metrics.EP_METRIC, metrics.ES_METRIC, metrics.ADMISSION_RATE_METRIC,
                    metrics.THROUGHPUT_METRIC, metrics.MEMORY_UTILIZATION_RATIO_METRIC, metrics.MEMORY_DECOHERENCE_RATE_METRIC,
                    api["metric_types"].EventAttributeMetric(key="requests", event=metrics.EventTypes.RESERVATION_REQUESTED, extractor=lambda record: record.data.identity),
                    api["metric_types"].EventAttributeMetric(key="hop_rejects", event=metrics.EventTypes.RESERVATION_HOP_REJECT, extractor=lambda record: record.owner_name)])
    output_path = Path(output_path).resolve()
    event_path = output_path.with_name(output_path.stem + ".events.jsonl.gz")
    event_path.parent.mkdir(parents=True, exist_ok=True)
    counts, event_count, pending = Counter(), 0, defaultdict(deque)
    control_delays = []
    max_delay_error_ps = 0
    outstanding_messages = 0
    deliveries = defaultdict(list)
    purification_consumption = []
    reservations = {}
    observations = []
    sink = gzip.open(event_path, "wt", encoding="utf-8", newline="\n")

    def record(event, **values):
        nonlocal event_count
        entry = {"index": event_count, "event": event, "time_ps": int(timeline.now()), **values}
        sink.write(json.dumps(entry, ensure_ascii=False, allow_nan=False, separators=(",", ":")) + "\n")
        event_count += 1
        counts[event] += 1

    def bind_observation(node):
        native_send, native_receive = node.send_message, node.receive_message
        native_quantum_send, native_quantum_receive = node.send_qubit, node.receive_qubit

        def send(dst, message, priority=math.inf, sender_delay=0):
            nonlocal outstanding_messages
            if getattr(node, "down", False):
                record("classical_send_blocked_node_down", source=node.name, target=dst)
            else:
                message_id = event_count
                pending[(id(message), node.name, dst)].append((message_id, int(timeline.now()), node.cchannels[dst].delay + sender_delay))
                outstanding_messages += 1
                record("classical_sent", message_id=message_id, source=node.name, target=dst,
                       protocol=type(message).__name__, receiver=getattr(message, "receiver", None),
                       delay_ps=int(node.cchannels[dst].delay + sender_delay))
            return native_send(dst, message, priority, sender_delay)

        def receive(src, message):
            nonlocal max_delay_error_ps, outstanding_messages
            queue = pending[(id(message), src, node.name)]
            if not queue:
                raise RuntimeError("native receive has no corresponding classical send")
            message_id, sent_time, expected_delay = queue.popleft()
            outstanding_messages -= 1
            delay = int(timeline.now()) - sent_time
            max_delay_error_ps = max(max_delay_error_ps, abs(delay - expected_delay))
            control_delays.append(delay)
            record("classical_received_dropped_node_down" if getattr(node, "down", False) else "classical_received",
                   message_id=message_id, source=src, target=node.name, observed_delay_ps=delay,
                   expected_delay_ps=int(expected_delay), protocol=type(message).__name__)
            return native_receive(src, message)

        def quantum_send(dst, photon):
            record("photon_sent", source=node.name, target=dst, photon=photon.name)
            return native_quantum_send(dst, photon)

        def quantum_receive(src, photon):
            record("photon_received", source=src, target=node.name, photon=photon.name)
            return native_quantum_receive(src, photon)

        node.send_message, node.receive_message = send, receive
        node.send_qubit, node.receive_qubit = quantum_send, quantum_receive

    for node in [*routers, *topology.get_nodes_by_type("BSMNode")]:
        bind_observation(node)

    def bind_resource_observation(node):
        manager = node.resource_manager
        native_update = manager.update

        def update(protocol, memory, state):
            info = manager.memory_manager.get_info_by_memory(memory)
            record("resource_update_requested", owner=node.name, memory=memory.name, previous_state=info.state,
                   requested_state=state, protocol=type(protocol).__name__ if protocol is not None else None)
            measured = (protocol is not None and getattr(protocol, "protocol_type", None) == "bbpssw"
                        and state == "RAW" and memory is getattr(protocol, "meas_memo", None))
            if measured:
                # These are the two actual native input memory resources; the
                # measured half is reset by the framework and cannot be reused
                # as the old pair. A failed protocol also discards the kept half.
                observation = {"time_ps": int(timeline.now()), "owner": node.name, "protocol": protocol.name,
                               "kept_memory": protocol.kept_memo.name, "measured_memory": protocol.meas_memo.name,
                               "input_memory_count": len(protocol.memories), "requested_measured_state": state}
                if observation["kept_memory"] == observation["measured_memory"] or observation["input_memory_count"] != 2:
                    raise RuntimeError("native purification did not consume two distinct input resources")
                purification_consumption.append(observation)
                record("purification_input_resources", **observation)
            return native_update(protocol, memory, state)

        manager.update = update

    for node in routers:
        bind_resource_observation(node)

    class DetectionObserver:
        def trigger(self, detector, info):
            record("detector_click", detector=detector.name, owner=detector.owner.name, rounded_time_ps=int(info["time"]))

    detector_observer = DetectionObserver()
    for bsm_node in topology.get_nodes_by_type("BSMNode"):
        for detector_component in bsm_node.get_components_by_type("SingleAtomBSM")[0].detectors:
            detector_component.attach(detector_observer)

    def observe_retained_density(info):
        """Read the actual framework state before native application consumption.

        A remote correction can still be in flight at the initiator callback.
        Bell-basis overlaps describe that instantaneous conditional state;
        maximum overlap does not certify a shared Pauli frame or teleportation.
        """
        manager = timeline.quantum_manager
        state = manager.get(info.memory.qstate_key)
        remote_name = info.memory.entangled_memory.get("memo_id")
        remote_memory = timeline.get_entity_by_name(remote_name) if remote_name else None
        if remote_memory is None or remote_memory.qstate_key not in state.keys:
            return {"status": "not_demonstrated", "reason": "native_metadata_has_no_shared_retained_density_state",
                    "native_state_keys": list(state.keys)}
        indices = [state.keys.index(info.memory.qstate_key), state.keys.index(remote_memory.qstate_key)]
        is_ket = formalism == api["constants"].KET_VECTOR_FORMALISM
        dimensions = [[2]*len(state.keys), [1 if is_ket else 2]*len(state.keys)]
        native_state = api["qutip"].Qobj(state.state, dims=dimensions)
        density = native_state.proj() if is_ket else native_state
        matrix = density.ptrace(indices).full()
        # Standard Bell projectors, evaluated directly against the native state.
        inv_sqrt_2 = 1 / math.sqrt(2)
        bells = ((inv_sqrt_2, 0, 0, inv_sqrt_2), (inv_sqrt_2, 0, 0, -inv_sqrt_2),
                 (0, inv_sqrt_2, inv_sqrt_2, 0), (0, inv_sqrt_2, -inv_sqrt_2, 0))
        fidelities = {label: float(sum(vector[row]*matrix[row, column]*vector[column]
                                      for row in range(4) for column in range(4)).real)
                      for label, vector in zip(("phi_plus", "phi_minus", "psi_plus", "psi_minus"), bells)}
        return {"status": "observed", "state_model": "conditional_pure_trajectory" if is_ket else "density_matrix",
                "native_memory_names": [info.memory.name, remote_memory.name],
                "native_state_keys": list(state.keys), "bell_fidelities": fidelities,
                "max_bell_fidelity": max(fidelities.values()), "trace": float(matrix.trace().real),
                "density_matrix_real": matrix.real.tolist(), "density_matrix_imag": matrix.imag.tolist(),
                "pauli_frame_status": "both_end_corrections_not_independently_acknowledged_by_runner"}

    class EvidenceApp(api["app"].RequestApp):
        def begin(self, demand, identity):
            record("application_requested", demand_id=demand["id"], identity=identity, source=self.node.name, target=demand["target"])
            if demand["target"] not in by_name or not nx.has_path(graph, self.node.name, demand["target"]):
                reservations[identity] = {"demand_id": demand["id"], "status": "blocked_no_native_route", "path": []}
                record("application_blocked_no_native_route", demand_id=demand["id"])
                return
            self.node.reserve_net_resource(demand["target"], _ps(demand["start_s"]),
                                           _ps(demand["start_s"] + demand["duration_s"]), demand["memory_count"],
                                           demand["target_fidelity"], identity=identity)

        def get_reservation_result(self, reservation, result):
            demand = payload["demands"][reservation.identity]
            reservations[reservation.identity] = {"demand_id": demand["id"], "status": "approved" if result else "rejected",
                                                  "path": list(reservation.path), "observed_at_ps": int(timeline.now())}
            record("application_reservation_result", demand_id=demand["id"], identity=reservation.identity,
                   accepted=result, path=list(reservation.path), owner=self.node.name)
            super().get_reservation_result(reservation, result)

        def get_memory(self, info):
            reservation = self.memo_to_reservation.get(info.index)
            if reservation is not None and info.state in ("ENTANGLED", "PURIFIED") and info.fidelity >= reservation.fidelity:
                if self.node.name == reservation.initiator and info.remote_node == reservation.responder:
                    delivered = {"time_ps": int(timeline.now()), "fidelity": float(info.fidelity), "remote_node": info.remote_node,
                                 "native_metadata_fidelity": float(info.fidelity), "retained_density": observe_retained_density(info),
                                 "source": self.node.name, "target": reservation.responder,
                                 "memory_index": info.index, "reservation_identity": reservation.identity, "path": list(reservation.path)}
                    deliveries[reservation.identity].append(delivered)
                    record("application_entanglement_received", **delivered, owner=self.node.name)
            super().get_memory(info)

    apps = {node.name: EvidenceApp(node) for node in routers}

    class Controller:
        def disrupt(self, event):
            if event["node_id"] in by_name:
                by_name[event["node_id"]].set_down(event["down"])
                record("node_status_changed", node_id=event["node_id"], down=event["down"])
            else:
                record("unsupported_node_status_not_applied", node_id=event["node_id"], down=event["down"])

    controller = Controller()
    try:
        timeline.init()
        for identity, demand in enumerate(payload["demands"]):
            if demand["source"] not in apps:
                reservations[identity] = {"demand_id": demand["id"], "status": "unsupported_source", "path": []}
                continue
            timeline.schedule(api["event"].Event(_ps(demand.get("request_s", 0)), api["process"].Process(apps[demand["source"]], "begin", [demand, identity])))
        for disruption in payload.get("disruptions", []):
            timeline.schedule(api["event"].Event(_ps(disruption["time_s"]), api["process"].Process(controller, "disrupt", [disruption])))
        timeline.run()
        if len(observations) != len(metrics.storage.get_all()):
            raise RuntimeError("native scientific-event capture differs from framework storage")
    finally:
        sink.close()
    demand_results = []
    for identity, demand in enumerate(payload["demands"]):
        reservation = reservations.get(identity, {"demand_id": demand["id"], "status": "pending_no_response", "path": []})
        pairs = deliveries[identity]
        demand_results.append({**reservation, "identity": identity, "source": demand["source"], "target": demand["target"],
                               "delivered_pairs": len(pairs), "delivery_observations": pairs,
                               "first_wait_s": (pairs[0]["time_ps"] - _ps(demand["start_s"])) / PS_PER_SECOND if pairs else None,
                               "throughput_pairs_s": len(pairs) / demand["duration_s"]})
    node_census = []
    for node in payload["nodes"]:
        native = by_name.get(node["id"])
        if native is None:
            node_census.append({"id": node["id"], "physical_node_id": node.get("physical_node_id"), "resource_bank_id": node.get("resource_bank_id"),
                                "status": "not_demonstrated", "reason": node.get("support_reason", "unsupported_native_node"), "memory_states": {}})
            continue
        memory_states = Counter(info.state for info in native.resource_manager.memory_manager)
        node_census.append({"id": node["id"], "physical_node_id": node.get("physical_node_id"), "resource_bank_id": node.get("resource_bank_id"),
                            "status": "native_instantiated", "reason": None, "down": native.down,
                            "memory_count": len(native.resource_manager.memory_manager), "memory_states": dict(memory_states),
                            "participating_reservations": [d["demand_id"] for d in demand_results if node["id"] in d["path"]]})
    for item in link_census:
        item["participating_reservations"] = [d["demand_id"] for d in demand_results
                                               if any({left, right} == {item["a"], item["b"]} for left, right in zip(d["path"], d["path"][1:]))]
        if item["status"] != "native_instantiated":
            item["participating_reservations"] = []
    native_counts = Counter(entry["event"] for entry in observations)
    result = {"schema": OUTPUT_SCHEMA, "scenario_id": payload["scenario_id"], "seed": payload["seed"],
              "topology_sha256": payload["topology_sha256"],
              "request_sha256": hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest(),
              "status": "executed", "backend": "SeQUeNCe", "state_formalism": formalism, "provenance": metadata,
              "node_census": node_census, "link_census": link_census, "demands": demand_results,
              "native_config": config, "native_metrics": dict(native_counts), "event_counts": dict(counts),
              "purification_consumption_records": purification_consumption,
              "native_observations": observations, "classical_receipts": {"count": len(control_delays), "max_delay_error_ps": max_delay_error_ps,
                                                                         "outstanding_messages": outstanding_messages,
                                                                         "minimum_delay_ps": min(control_delays) if control_delays else None,
                                                                         "maximum_delay_ps": max(control_delays) if control_delays else None},
              "event_log": {"path": event_path.name, "count": event_count, "sha256": hashlib.sha256(event_path.read_bytes()).hexdigest()},
              "timeline": {"stop_time_ps": timeline.stop_time, "last_event_time_ps": int(timeline.now()),
                           "scheduled_events": timeline.schedule_counter, "executed_events": timeline.run_counter, "remaining_events": len(timeline.events)},
              "wall_time_s": time.perf_counter() - started, "complete_network_validation": False, "hardware_qualified": False,
              "certified_secret_bits": 0,
              "limitations": ["Native Barrett--Kok experiments are alternatives, not throughput parity with OrionQ's aggregate optical samples.",
                              "Native circuit memory coherence_time supplies expiration, not continuous visibility damping equivalence with OrionQ pair coherence.",
                              "Ket-vector states are conditional pure trajectories; their observed per-trajectory Bell overlaps are not ensemble noise calibration or fidelity equivalence.",
                              "Pinned density manager retains measured keys across remote corrections and can fail in long repeated memory reuse; this mode is not qualified for the full repeated network.",
                              "One BSM/memory bank per endpoint pair; every unsupported parallel bank is retained in the census.",
                              "Classical full-mesh control links have shortest-path supplied delays; no bandwidth contention, message authentication, or classical packet loss is inferred.",
                              "No native TF-QKD, satellite geometric/atmospheric link, ISL terminal, or certified QKD security integration is claimed.",
                              "Native application admission uses scalar MemoryInfo fidelity: circuit swapping metadata f1*f2*degradation and BBPSSW Werner recurrence need not equal the retained density-state fidelity.",
                              "Retained density is observed at the initiator callback before consumption; remote Pauli correction may be pending, and maximum Bell overlap is not acknowledged endpoint-frame equivalence.",
                              "Node outages do not make static routing dynamically converge; recovery requires new native requests.",
                              "Fixed insertion loss is mapped into total scalar fibre attenuation; component location is not modelled."]}
    output_path.write_text(json.dumps(result, indent=2, ensure_ascii=False, allow_nan=False), encoding="utf-8")
    return result


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--sequence-src", type=Path, default=Path(os.environ.get("SEQUENCE_SRC", "C:/SeQUeNCe")))
    args = parser.parse_args(argv)
    payload = json.loads(args.input.read_text(encoding="utf-8-sig"), object_pairs_hook=_unique_object,
                         parse_constant=lambda value: (_ for _ in ()).throw(ValueError(f"nonfinite JSON value {value}")))
    validate_payload(payload)
    api, metadata = load_native(args.sequence_src)
    result = run_network(payload, args.output, api, metadata)
    print(json.dumps({"status": result["status"], "scenario_id": result["scenario_id"], "native_metrics": result["native_metrics"],
                      "delivered_pairs": sum(d["delivered_pairs"] for d in result["demands"]), "wall_time_s": result["wall_time_s"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
