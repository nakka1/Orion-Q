"""One ideal conditional SWAP on a frozen configured path. No physical service."""
import json
import math
import re
from collections.abc import Mapping
from contextlib import closing
from dataclasses import asdict, dataclass

from .topology import DEFAULT_TOPOLOGY_ID, SimulatedTopology, NodeRole, default_simulated_topology, _sha256, _canonical_json, _utc_now


@dataclass(frozen=True)
class WorkspacePathRequest:
    run_id: str
    source: str
    repeater: str
    destination: str
    expected_version: int
    target_fidelity: float
    topology_id: str = DEFAULT_TOPOLOGY_ID
    protocol: str = "werner_single_swap_v1"

    def __post_init__(self):
        for value in (self.run_id, self.source, self.repeater, self.destination):
            if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", value):
                raise ValueError("bounded ASCII run and node IDs required")
        if len({self.source, self.repeater, self.destination}) != 3:
            raise ValueError("source, repeater and destination must be distinct")
        if type(self.expected_version) is not int or self.expected_version < 0:
            raise ValueError("expected_version must be a nonnegative integer")
        if type(self.target_fidelity) not in (int, float) or not 0 <= self.target_fidelity <= 1 or not math.isfinite(self.target_fidelity):
            raise ValueError("target_fidelity must be finite in [0, 1]")
        if self.topology_id != DEFAULT_TOPOLOGY_ID or self.protocol != "werner_single_swap_v1":
            raise ValueError("only the default workspace and werner_single_swap_v1 are supported")

    @classmethod
    def from_dict(cls, payload):
        if not isinstance(payload, Mapping) or set(payload) != set(cls.__dataclass_fields__):
            raise ValueError("exact workspace path fields required; no physical or model overrides")
        return cls(**dict(payload))


def selected_path(topology, request):
    """Validate the explicit two-hop path without choosing an alternate route."""
    route = (request.source, request.repeater, request.destination)
    for ident, role in zip(route, (NodeRole.ENDPOINT, NodeRole.REPEATER, NodeRole.ENDPOINT)):
        if topology.node(ident).role is not role:
            raise ValueError("path must be ENDPOINT → REPEATER → ENDPOINT")
    links = []
    for a, b in zip(route, route[1:]):
        matches = [link for link in topology.links if {link.left_node_id, link.right_node_id} == {a, b}]
        if len(matches) != 1 or not matches[0].active:
            raise ValueError("each selected hop requires exactly one enabled link; no alternate routing")
        links.append(matches[0])
    return route, links


def evaluate_path(topology, request):
    """Pure calculation: existing executor + independent supervisor, fresh nominal pairs."""
    from .contracts import NetworkStateSnapshot, ControlIntent, ControlAction, ControlMode
    from .executors import SimulatedQuantumExecutor
    from .safety import SafetySupervisor
    from quantum_autopilot.core import EvidenceKind, Provenance
    from importlib.metadata import version
    route, links = selected_path(topology, request)
    fidelities = tuple(link.nominal_pair_fidelity for link in links)
    now = _utc_now()
    snapshot = NetworkStateSnapshot(
        request_id=request.run_id, node_id=request.repeater, timestamp_utc=now, state_version=0,
        predicted_fidelity=min(fidelities), prediction_uncertainty=0, telemetry_age_s=0,
        memory_state="STORING", available_pair_fidelities=fidelities, memory_ages_s=(0, 0),
        integrity_verified=True, operator_interlock=True,
        provenance=Provenance(evidence_kind=EvidenceKind.SIMULATED, source="configured_path_nominal_pairs",
            timestamp=now, metadata={"evidence_lane": "bell_fidelity_control", "physical_calibration": False}),
        metadata={"prediction_semantics": "nominal_input_placeholder_not_model_prediction",
                  "pair_endpoints": [[route[0], route[1]], [route[1], route[2]]],
                  "fresh_pairs_assumed": True})
    intent = ControlIntent(request.run_id + ":swap", request.run_id, request.repeater, ControlAction.SWAP,
                           0, now, request.protocol, {"fixed_protocol": True, "uses_wdm_model": False})
    safety = SafetySupervisor().assess(ControlMode.SIMULATION, intent, snapshot)
    if not safety.authorized:
        raise ValueError("sandbox supervisor refused this path calculation")
    execution = SimulatedQuantumExecutor().execute(intent, snapshot, mode=ControlMode.SIMULATION)
    fidelity = execution.details.get("F_t")
    if execution.physical_action_performed or execution.details.get("success") is not True or not isinstance(fidelity, (int, float)) or not math.isfinite(fidelity) or not 0 <= fidelity <= 1:
        raise ValueError("invalid conditional simulator result")
    return {"route": list(route), "links": [link.to_dict() for link in links],
            "total_distance_km": sum(link.distance_km for link in links),
            "conditional_fidelity": fidelity, "target_fidelity": request.target_fidelity,
            "conditional_target_met": fidelity >= request.target_fidelity,
            "snapshot": snapshot.to_dict(), "intent": intent.to_dict(), "safety": safety.to_dict(),
            "execution": execution.to_dict(),
            "implementation": {"executor": "SimulatedQuantumExecutor", "kernel": "WernerStateSwapping",
                "protocol": request.protocol, "qiskit_version": version("qiskit"), "numpy_version": version("numpy")},
            "assumptions": ["Two independent Werner pairs already available; nominal input fidelities",
                "Ideal BSM and ideal Pauli corrections; outcome-weighted density-matrix calculation",
                "Zero memory age; no loss, delay, generation probability or rate model",
                "Distance is recorded only, not converted to noise or latency",
                "Fresh isolated pairs; stored workspace memory is neither consumed nor reused",
                "No WDM inference, purification, routing optimization, multirepeater or physical action"]}


def run_workspace_path(store, request):
    if not isinstance(request, WorkspacePathRequest):
        raise ValueError("validated WorkspacePathRequest required")
    payload = asdict(request)
    request_hash = _sha256(payload)
    from .temporal_path import TemporalPathRequest, evaluate_temporal_path
    temporal = isinstance(request, TemporalPathRequest)
    # One bounded local calculation under the writer lock: no partly persisted runs.
    with store._lock, closing(store._connect()) as connection, connection:
        connection.execute("BEGIN IMMEDIATE")
        existing = connection.execute("SELECT * FROM workspace_path_runs WHERE run_id = ?", (request.run_id,)).fetchone()
        if existing:
            result = _verified(existing)
            if existing["request_sha256"] != request_hash:
                raise ValueError("run_id already used with a different request")
            return {**result, "idempotent_replay": True}
        row = connection.execute("SELECT state_json FROM simulated_topologies WHERE topology_id = ?", (request.topology_id,)).fetchone()
        topology = SimulatedTopology.from_dict(json.loads(row[0])) if row else default_simulated_topology(request.topology_id)
        if topology.topology_version != request.expected_version:
            raise ValueError("topology version conflict; reload the form")
        record = {"schema": "orion.workspace_path.temporal.v1" if temporal else "orion.workspace_path.v1", "request": payload,
                  "source_topology": topology.to_dict(), "source_topology_sha256": _sha256(topology.to_dict()),
                  "source_was_persisted": row is not None,
                  "result": evaluate_temporal_path(topology, request) if temporal else evaluate_path(topology, request),
                  "completed_at_utc": _utc_now(), "physical_action_performed": False,
                  "workspace_mutated": False, "evidence_kind": "simulated"}
        digest = _sha256(record)
        connection.execute("INSERT INTO workspace_path_runs (run_id, request_sha256, record_sha256, record_json) VALUES (?, ?, ?, ?)",
                           (request.run_id, request_hash, digest, _canonical_json(record)))
    return {"record": record, "record_sha256": digest, "idempotent_replay": False}


def _verified(row):
    record = json.loads(row["record_json"])
    if record["request"]["run_id"] != row["run_id"] or _sha256(record) != row["record_sha256"] or _sha256(record["request"]) != row["request_sha256"] or _sha256(record["source_topology"]) != record["source_topology_sha256"]:
        raise ValueError("workspace path record integrity failure")
    return {"record": record, "record_sha256": row["record_sha256"]}


def read_workspace_path(store, run_id=None):
    with store._lock, closing(store._connect()) as connection:
        row = connection.execute("SELECT * FROM workspace_path_runs ORDER BY sequence DESC LIMIT 1").fetchone() if run_id is None else connection.execute("SELECT * FROM workspace_path_runs WHERE run_id = ?", (run_id,)).fetchone()
    return _verified(row) if row else None
