"""Persistent simulated topology and deterministic scenario runner."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import closing, nullcontext
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from threading import RLock
from typing import Any, Mapping
from uuid import uuid4

from quantum_autopilot.control_plane.geodesy import link_geodesic_km, node_location
from quantum_autopilot.control_plane.sqlite_transaction import connect

from quantum_autopilot.control_plane.contracts import (
    ControlAction,
    ControlCycleRecord,
    ControlMode,
    NetworkStateSnapshot,
    _aware_timestamp,
    _finite,
    _nonempty,
)
from quantum_autopilot.core import EvidenceKind, Provenance
from quantum_autopilot.core.contracts import JSONSerializableMixin


TOPOLOGY_SCHEMA_VERSION = "1.0.0"
DEFAULT_TOPOLOGY_ID = "sandbox-line-3"
DEFAULT_SCENARIO_PRESET = "resilience_demo"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _canonical_json(payload: Mapping[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256(payload: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


MAX_CALIBRATION_REPORT_BYTES = 16 * 1024
_SIMULATOR_ONLY = {"scope": "simulator_only", "physical_action_performed": False}


def _console_control(node: "SimulatedNodeState") -> dict[str, Any]:
    control = node.telemetry.get("console_control", {})
    return dict(control) if isinstance(control, Mapping) else {}


def _is_isolated(node: "SimulatedNodeState") -> bool:
    return _console_control(node).get("isolated") is True


def node_memory_generation(node: "SimulatedNodeState") -> int:
    """Persistent reset counter, independent of location and other node edits."""
    value = _console_control(node).get("memory_generation", 0)
    return value if type(value) is int and value >= 0 else 0


def _isolation_record(control: Mapping[str, Any]) -> list[str]:
    """Link ids that an isolation of this node switched off, in recorded order."""

    recorded = control.get("isolation_disabled_links", [])
    if not isinstance(recorded, (list, tuple)):
        return []
    result: list[str] = []
    for item in recorded:
        if isinstance(item, str) and item not in result:
            result.append(item)
    return result


def _json_data(value: Any, *, depth: int = 0) -> Any:
    """Copy ``value`` as plain finite JSON data; refuse anything json would coerce."""

    if depth > 8:
        raise ValueError("calibration report is nested too deeply")
    if value is None or isinstance(value, (bool, str)):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            raise ValueError("calibration report numbers must be finite")
        return value
    if isinstance(value, Mapping):
        if any(not isinstance(key, str) for key in value):
            raise ValueError("calibration report keys must be strings")
        return {key: _json_data(item, depth=depth + 1) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_data(item, depth=depth + 1) for item in value]
    raise ValueError(f"calibration report holds a non-JSON value: {type(value).__name__}")


def _calibration_report(report: Any) -> dict[str, Any]:
    """Validate an engine report and return it with ``report_sha256`` added."""

    if not isinstance(report, Mapping) or not report:
        raise ValueError("calibration report must be a non-empty JSON object")
    body = _json_data(report)
    supplied = body.pop("report_sha256", None)
    if not body:
        raise ValueError("calibration report must be a non-empty JSON object")
    size = len(_canonical_json(body).encode("utf-8"))
    if size > MAX_CALIBRATION_REPORT_BYTES:
        raise ValueError(
            f"calibration report exceeds {MAX_CALIBRATION_REPORT_BYTES} bytes of canonical JSON: {size}"
        )
    digest = _sha256(body)
    if supplied is not None and supplied != digest:
        raise ValueError("calibration report_sha256 does not match the canonical report")
    return {**body, "report_sha256": digest}


class NodeRole(str, Enum):
    ENDPOINT = "endpoint"
    REPEATER = "repeater"


@dataclass(frozen=True)
class SimulatedNodeState(JSONSerializableMixin):
    node_id: str
    role: NodeRole | str
    state_version: int
    memory_state: str
    available_pair_fidelities: tuple[float, ...] = field(default_factory=tuple)
    memory_ages_s: tuple[float, ...] = field(default_factory=tuple)
    telemetry: dict[str, Any] = field(default_factory=dict)
    updated_at_utc: str = field(default_factory=_utc_now)
    schema_version: str = TOPOLOGY_SCHEMA_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(self, "node_id", _nonempty("node_id", self.node_id))
        object.__setattr__(self, "role", NodeRole(self.role))
        if isinstance(self.state_version, bool) or not isinstance(self.state_version, int):
            raise ValueError("state_version must be an integer")
        if self.state_version < 0:
            raise ValueError("state_version must be >= 0")
        if self.memory_state not in {"IDLE", "STORING"}:
            raise ValueError("memory_state must be IDLE or STORING")
        pairs = tuple(
            _finite(
                f"available_pair_fidelities[{index}]",
                value,
                minimum=0.0,
                maximum=1.0,
            )
            for index, value in enumerate(self.available_pair_fidelities)
        )
        ages = tuple(
            _finite(f"memory_ages_s[{index}]", value, minimum=0.0)
            for index, value in enumerate(self.memory_ages_s)
        )
        if len(pairs) != len(ages):
            raise ValueError("memory_ages_s must align with available_pair_fidelities")
        if len(pairs) > 2:
            raise ValueError("the line-topology repeater supports at most two stored pairs")
        if self.memory_state == "IDLE" and pairs:
            raise ValueError("IDLE nodes cannot retain simulated pairs")
        if self.memory_state == "STORING" and not pairs:
            raise ValueError("STORING nodes must retain at least one simulated pair")
        if self.role is NodeRole.ENDPOINT and pairs:
            raise ValueError("endpoint nodes cannot retain repeater-memory pairs")
        if not isinstance(self.telemetry, Mapping):
            raise ValueError("telemetry must be an object")
        object.__setattr__(self, "available_pair_fidelities", pairs)
        object.__setattr__(self, "memory_ages_s", ages)
        object.__setattr__(self, "telemetry", dict(self.telemetry))
        object.__setattr__(
            self,
            "updated_at_utc",
            _aware_timestamp("updated_at_utc", self.updated_at_utc),
        )
        if self.schema_version != TOPOLOGY_SCHEMA_VERSION:
            raise ValueError("unsupported topology schema_version")

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "SimulatedNodeState":
        values = dict(payload)
        for name in ("available_pair_fidelities", "memory_ages_s"):
            if name in values:
                values[name] = tuple(values[name])
        return cls(**values)


@dataclass(frozen=True)
class SimulatedLink(JSONSerializableMixin):
    link_id: str
    left_node_id: str
    right_node_id: str
    distance_km: float
    nominal_pair_fidelity: float
    active: bool = True
    schema_version: str = TOPOLOGY_SCHEMA_VERSION
    channels: dict | None | str = "local-defaults"
    technology: dict | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "link_id", _nonempty("link_id", self.link_id))
        object.__setattr__(self, "left_node_id", _nonempty("left_node_id", self.left_node_id))
        object.__setattr__(self, "right_node_id", _nonempty("right_node_id", self.right_node_id))
        if self.left_node_id == self.right_node_id:
            raise ValueError("a simulated link must join two different nodes")
        object.__setattr__(
            self,
            "distance_km",
            _finite("distance_km", self.distance_km, minimum=0.0),
        )
        object.__setattr__(
            self,
            "nominal_pair_fidelity",
            _finite(
                "nominal_pair_fidelity",
                self.nominal_pair_fidelity,
                minimum=0.0,
                maximum=1.0,
            ),
        )
        if not isinstance(self.active, bool):
            raise ValueError("active must be boolean")
        if self.schema_version != TOPOLOGY_SCHEMA_VERSION:
            raise ValueError("unsupported topology schema_version")
        from .channels import ChannelPair, local_pair
        if self.channels == "local-defaults":
            object.__setattr__(self, "channels", local_pair(self.link_id, self.right_node_id))
        if self.channels is not None:
            pair = ChannelPair.from_dict(self.channels)
            pair.validate_link(self.link_id, self.right_node_id)
            object.__setattr__(self, "channels", pair.to_dict())
        if self.technology is not None:
            if not isinstance(self.technology, dict) or not self.technology.get("session_id"):
                raise ValueError("technology link requires an explicit session_id")
            object.__setattr__(self, "technology", _json_data(self.technology))

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "SimulatedLink":
        # Old inventories remain readable and are not rewritten or silently
        # presented as having operational channels.
        return cls(**{"channels": None, **dict(payload)})

    def to_dict(self):
        value = super().to_dict()
        if self.channels is None:
            value.pop("channels")
        if self.technology is None:
            value.pop("technology")
        return value


@dataclass(frozen=True)
class SimulatedTopology(JSONSerializableMixin):
    topology_id: str
    topology_version: int
    nodes: tuple[SimulatedNodeState, ...]
    links: tuple[SimulatedLink, ...]
    updated_at_utc: str = field(default_factory=_utc_now)
    schema_version: str = TOPOLOGY_SCHEMA_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(self, "topology_id", _nonempty("topology_id", self.topology_id))
        if isinstance(self.topology_version, bool) or not isinstance(self.topology_version, int):
            raise ValueError("topology_version must be an integer")
        if self.topology_version < 0:
            raise ValueError("topology_version must be >= 0")
        nodes = tuple(
            item if isinstance(item, SimulatedNodeState) else SimulatedNodeState.from_dict(item)
            for item in self.nodes
        )
        links = tuple(
            item if isinstance(item, SimulatedLink) else SimulatedLink.from_dict(item)
            for item in self.links
        )
        if not nodes:
            raise ValueError("a simulated topology requires at least one node")
        node_ids = [item.node_id for item in nodes]
        link_ids = [item.link_id for item in links]
        if len(node_ids) != len(set(node_ids)):
            raise ValueError("simulated node identifiers must be unique")
        if len(link_ids) != len(set(link_ids)):
            raise ValueError("simulated link identifiers must be unique")
        known_nodes = set(node_ids)
        channel_ids = set()
        for link in links:
            if link.left_node_id not in known_nodes or link.right_node_id not in known_nodes:
                raise ValueError("every simulated link endpoint must exist in the topology")
            if link.channels is not None:
                for name in ("classical", "quantum"):
                    channel_id = link.channels[name]["channel_id"]
                    if channel_id in channel_ids:
                        raise ValueError("channel identifiers must be unique in the topology")
                    channel_ids.add(channel_id)
        object.__setattr__(self, "nodes", nodes)
        object.__setattr__(self, "links", links)
        object.__setattr__(
            self,
            "updated_at_utc",
            _aware_timestamp("updated_at_utc", self.updated_at_utc),
        )
        if self.schema_version != TOPOLOGY_SCHEMA_VERSION:
            raise ValueError("unsupported topology schema_version")

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "SimulatedTopology":
        values = dict(payload)
        values["nodes"] = tuple(values.get("nodes", ()))
        values["links"] = tuple(values.get("links", ()))
        return cls(**values)

    def node(self, node_id: str) -> SimulatedNodeState:
        for item in self.nodes:
            if item.node_id == node_id:
                return item
        raise ValueError(f"node_id is outside topology {self.topology_id!r}: {node_id!r}")

    def to_dict(self):
        value = super().to_dict()
        value["links"] = [link.to_dict() for link in self.links]
        return value


@dataclass(frozen=True)
class ScenarioStep(JSONSerializableMixin):
    step_id: str
    node_id: str
    predicted_fidelity: float
    prediction_uncertainty: float = 0.01
    telemetry_age_s: float = 0.1
    injected_pair_fidelities: tuple[float, ...] = field(default_factory=tuple)
    injected_memory_ages_s: tuple[float, ...] = field(default_factory=tuple)
    integrity_verified: bool = True
    operator_interlock: bool = True
    out_of_distribution: bool = False
    telemetry: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "step_id", _nonempty("step_id", self.step_id))
        object.__setattr__(self, "node_id", _nonempty("node_id", self.node_id))
        object.__setattr__(
            self,
            "predicted_fidelity",
            _finite("predicted_fidelity", self.predicted_fidelity, minimum=0.0, maximum=1.0),
        )
        object.__setattr__(
            self,
            "prediction_uncertainty",
            _finite("prediction_uncertainty", self.prediction_uncertainty, minimum=0.0),
        )
        object.__setattr__(
            self,
            "telemetry_age_s",
            _finite("telemetry_age_s", self.telemetry_age_s, minimum=0.0),
        )
        pairs = tuple(
            _finite(
                f"injected_pair_fidelities[{index}]",
                value,
                minimum=0.0,
                maximum=1.0,
            )
            for index, value in enumerate(self.injected_pair_fidelities)
        )
        ages = tuple(
            _finite(f"injected_memory_ages_s[{index}]", value, minimum=0.0)
            for index, value in enumerate(self.injected_memory_ages_s)
        )
        if ages and len(ages) != len(pairs):
            raise ValueError("injected memory ages must align with injected pairs")
        if len(pairs) > 2:
            raise ValueError("a scenario step may inject at most two simulated pairs")
        if not ages:
            ages = tuple(0.0 for _ in pairs)
        object.__setattr__(self, "injected_pair_fidelities", pairs)
        object.__setattr__(self, "injected_memory_ages_s", ages)
        for name in ("integrity_verified", "operator_interlock", "out_of_distribution"):
            if not isinstance(getattr(self, name), bool):
                raise ValueError(f"{name} must be boolean")
        if not isinstance(self.telemetry, Mapping) or not isinstance(self.metadata, Mapping):
            raise ValueError("telemetry and metadata must be objects")
        object.__setattr__(self, "telemetry", dict(self.telemetry))
        object.__setattr__(self, "metadata", dict(self.metadata))

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "ScenarioStep":
        values = dict(payload)
        for name in ("injected_pair_fidelities", "injected_memory_ages_s"):
            if name in values:
                values[name] = tuple(values[name])
        return cls(**values)


@dataclass(frozen=True)
class ScenarioDefinition(JSONSerializableMixin):
    scenario_id: str
    topology_id: str
    steps: tuple[ScenarioStep, ...]
    reset_topology: bool = False
    preset: str = "custom"
    schema_version: str = TOPOLOGY_SCHEMA_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(self, "scenario_id", _nonempty("scenario_id", self.scenario_id))
        object.__setattr__(self, "topology_id", _nonempty("topology_id", self.topology_id))
        object.__setattr__(self, "preset", _nonempty("preset", self.preset))
        steps = tuple(
            item if isinstance(item, ScenarioStep) else ScenarioStep.from_dict(item)
            for item in self.steps
        )
        if not 1 <= len(steps) <= 50:
            raise ValueError("a scenario requires between 1 and 50 steps")
        step_ids = [item.step_id for item in steps]
        if len(step_ids) != len(set(step_ids)):
            raise ValueError("scenario step identifiers must be unique")
        if not isinstance(self.reset_topology, bool):
            raise ValueError("reset_topology must be boolean")
        object.__setattr__(self, "steps", steps)
        if self.schema_version != TOPOLOGY_SCHEMA_VERSION:
            raise ValueError("unsupported scenario schema_version")

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "ScenarioDefinition":
        values = dict(payload)
        values["steps"] = tuple(values.get("steps", ()))
        return cls(**values)


def default_simulated_topology(topology_id: str = DEFAULT_TOPOLOGY_ID) -> SimulatedTopology:
    """Return the reproducible three-node line used by the local product demo."""

    now = _utc_now()
    return SimulatedTopology(
        topology_id=topology_id,
        topology_version=0,
        nodes=(
            SimulatedNodeState("alice", NodeRole.ENDPOINT, 0, "IDLE", updated_at_utc=now),
            SimulatedNodeState(
                "repeater-a",
                NodeRole.REPEATER,
                0,
                "STORING",
                available_pair_fidelities=(0.90, 0.90),
                memory_ages_s=(0.0, 0.0),
                telemetry={"source": "default_simulated_topology"},
                updated_at_utc=now,
            ),
            SimulatedNodeState("bob", NodeRole.ENDPOINT, 0, "IDLE", updated_at_utc=now),
        ),
        links=(
            SimulatedLink("alice-r1", "alice", "repeater-a", 25.0, 0.90),
            SimulatedLink("r1-bob", "repeater-a", "bob", 25.0, 0.90),
        ),
        updated_at_utc=now,
    )


def resilience_demo_scenario(
    scenario_id: str,
    *,
    topology_id: str = DEFAULT_TOPOLOGY_ID,
    reset_topology: bool = True,
) -> ScenarioDefinition:
    """Create a nominal -> uncertainty trip -> recovery demonstration."""

    common = {"source": "resilience_demo", "synthetic_pair_injection": True}
    return ScenarioDefinition(
        scenario_id=scenario_id,
        topology_id=topology_id,
        reset_topology=reset_topology,
        preset=DEFAULT_SCENARIO_PRESET,
        steps=(
            ScenarioStep(
                "nominal-swap",
                "repeater-a",
                0.90,
                injected_pair_fidelities=(0.90, 0.90),
                telemetry={**common, "phase": "nominal"},
            ),
            ScenarioStep(
                "uncertainty-trip",
                "repeater-a",
                0.84,
                prediction_uncertainty=0.35,
                injected_pair_fidelities=(0.84, 0.83),
                telemetry={**common, "phase": "uncertainty_trip"},
            ),
            ScenarioStep(
                "recovered-swap",
                "repeater-a",
                0.88,
                prediction_uncertainty=0.02,
                injected_pair_fidelities=(0.88, 0.87),
                telemetry={**common, "phase": "recovered"},
            ),
        ),
    )


class SimulatedTopologyStore:
    """SQLite-backed, version-checked topology and scenario journal."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()
        with closing(self._connect()) as connection, connection:
            if not connection.in_transaction:
                connection.execute("BEGIN IMMEDIATE")
            schema = """
                CREATE TABLE IF NOT EXISTS simulated_topologies (
                    topology_id TEXT PRIMARY KEY,
                    topology_version INTEGER NOT NULL,
                    updated_at_utc TEXT NOT NULL,
                    state_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS topology_events (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_id TEXT NOT NULL UNIQUE,
                    topology_id TEXT NOT NULL,
                    version_before INTEGER,
                    version_after INTEGER NOT NULL,
                    event_kind TEXT NOT NULL,
                    event_sha256 TEXT NOT NULL,
                    created_at_utc TEXT NOT NULL,
                    event_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS scenario_runs (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    scenario_id TEXT NOT NULL UNIQUE,
                    request_sha256 TEXT NOT NULL,
                    record_sha256 TEXT NOT NULL,
                    completed_at_utc TEXT NOT NULL,
                    record_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_topology_events_topology_sequence
                ON topology_events(topology_id, sequence DESC);
                CREATE TABLE IF NOT EXISTS workspace_path_runs (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL UNIQUE,
                    request_sha256 TEXT NOT NULL,
                    record_sha256 TEXT NOT NULL,
                    record_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS temporal_path_campaigns (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    campaign_id TEXT NOT NULL UNIQUE,
                    request_sha256 TEXT NOT NULL,
                    record_sha256 TEXT NOT NULL,
                    record_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS temporal_campaign_attempts (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    attempt_id TEXT NOT NULL UNIQUE,
                    record_sha256 TEXT NOT NULL,
                    record_json TEXT NOT NULL
                );
                """
            # These are fixed DDL statements; executescript would implicitly
            # commit an existing scenario transaction before creating tables.
            for statement in schema.split(";"):
                if statement.strip():
                    connection.execute(statement)
            connection.execute("PRAGMA optimize")

    def _connect(self) -> sqlite3.Connection:
        return connect(self.path)

    @staticmethod
    def _event(
        topology: SimulatedTopology,
        *,
        version_before: int | None,
        event_kind: str,
        payload: Mapping[str, Any],
    ) -> tuple[Any, ...]:
        created_at = _utc_now()
        event = {
            "event_id": str(uuid4()),
            "topology_id": topology.topology_id,
            "version_before": version_before,
            "version_after": topology.topology_version,
            "event_kind": event_kind,
            "created_at_utc": created_at,
            "payload": dict(payload),
        }
        serialized = _canonical_json(event)
        return (
            event["event_id"],
            topology.topology_id,
            version_before,
            topology.topology_version,
            event_kind,
            hashlib.sha256(serialized.encode("utf-8")).hexdigest(),
            created_at,
            serialized,
        )

    def initialize(self, topology: SimulatedTopology) -> tuple[SimulatedTopology, bool]:
        with self._lock, closing(self._connect()) as connection, connection:
            if not connection.in_transaction:
                connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT state_json FROM simulated_topologies WHERE topology_id = ?",
                (topology.topology_id,),
            ).fetchone()
            if row is not None:
                return SimulatedTopology.from_dict(json.loads(row["state_json"])), False
            connection.execute(
                """
                INSERT INTO simulated_topologies
                    (topology_id, topology_version, updated_at_utc, state_json)
                VALUES (?, ?, ?, ?)
                """,
                (
                    topology.topology_id,
                    topology.topology_version,
                    topology.updated_at_utc,
                    _canonical_json(topology.to_dict()),
                ),
            )
            connection.execute(
                """
                INSERT INTO topology_events
                    (event_id, topology_id, version_before, version_after,
                     event_kind, event_sha256, created_at_utc, event_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                self._event(
                    topology,
                    version_before=None,
                    event_kind="topology_initialized",
                    payload={"simulated": True},
                ),
            )
        return topology, True

    def get(self, topology_id: str) -> SimulatedTopology | None:
        topology_id = _nonempty("topology_id", topology_id)
        with self._lock, closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT state_json FROM simulated_topologies WHERE topology_id = ?",
                (topology_id,),
            ).fetchone()
        return None if row is None else SimulatedTopology.from_dict(json.loads(row["state_json"]))

    def _replace_existing(
        self,
        topology: SimulatedTopology,
        *,
        expected_version: int,
        event_kind: str,
        payload: Mapping[str, Any],
    ) -> SimulatedTopology:
        with self._lock, closing(self._connect()) as connection, connection:
            if not connection.in_transaction:
                connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT topology_version FROM simulated_topologies WHERE topology_id = ?",
                (topology.topology_id,),
            ).fetchone()
            if row is None:
                raise ValueError(f"unknown simulated topology: {topology.topology_id!r}")
            actual_version = int(row["topology_version"])
            if actual_version != expected_version:
                raise ValueError(
                    "simulated topology version conflict: "
                    f"expected {expected_version}, found {actual_version}"
                )
            updated = connection.execute(
                """
                UPDATE simulated_topologies
                SET topology_version = ?, updated_at_utc = ?, state_json = ?
                WHERE topology_id = ? AND topology_version = ?
                """,
                (
                    topology.topology_version,
                    topology.updated_at_utc,
                    _canonical_json(topology.to_dict()),
                    topology.topology_id,
                    expected_version,
                ),
            )
            if updated.rowcount != 1:
                raise ValueError("simulated topology update lost an optimistic-lock race")
            connection.execute(
                """
                INSERT INTO topology_events
                    (event_id, topology_id, version_before, version_after,
                     event_kind, event_sha256, created_at_utc, event_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                self._event(
                    topology,
                    version_before=expected_version,
                    event_kind=event_kind,
                    payload=payload,
                ),
            )
        return topology

    def reset(self, topology_id: str = DEFAULT_TOPOLOGY_ID) -> SimulatedTopology:
        baseline = default_simulated_topology(topology_id)
        current = self.get(topology_id)
        if current is None:
            return self.initialize(baseline)[0]
        now = _utc_now()
        current_nodes = {item.node_id: item for item in current.nodes}
        nodes = tuple(
            replace(
                item,
                state_version=current_nodes.get(item.node_id, item).state_version + 1,
                updated_at_utc=now,
            )
            for item in baseline.nodes
        )
        reset_topology = replace(
            baseline,
            topology_version=current.topology_version + 1,
            nodes=nodes,
            updated_at_utc=now,
        )
        return self._replace_existing(
            reset_topology,
            expected_version=current.topology_version,
            event_kind="topology_reset",
            payload={"simulated": True},
        )

    def add_inventory_node(self, request) -> SimulatedTopology:
        """Read/version-check/insert/event in one cross-process SQLite transaction."""
        from .inventory import AddSimulatedNode
        if not isinstance(request, AddSimulatedNode):
            raise ValueError("validated AddSimulatedNode request required")
        node = request.node()
        with self._lock, closing(self._connect()) as connection, connection:
            if not connection.in_transaction:
                connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT state_json FROM simulated_topologies WHERE topology_id = ?",
                                     (request.topology_id,)).fetchone()
            current = SimulatedTopology.from_dict(json.loads(row["state_json"])) if row else default_simulated_topology(request.topology_id)
            if current.topology_version != request.expected_version:
                raise ValueError(f"topology version conflict: expected {request.expected_version}, found {current.topology_version}; reload before editing")
            if any(n.node_id == node.node_id for n in current.nodes):
                raise ValueError("duplicate simulated node_id")
            if len(current.nodes) >= 32:
                raise ValueError("inventory editor supports at most 32 simulated nodes")
            updated = replace(current, nodes=(*current.nodes, node),
                              topology_version=current.topology_version + 1, updated_at_utc=_utc_now())
            connection.execute("""INSERT INTO simulated_topologies
                (topology_id, topology_version, updated_at_utc, state_json) VALUES (?, ?, ?, ?)
                ON CONFLICT(topology_id) DO UPDATE SET topology_version=excluded.topology_version,
                updated_at_utc=excluded.updated_at_utc, state_json=excluded.state_json""",
                (updated.topology_id, updated.topology_version, updated.updated_at_utc, _canonical_json(updated.to_dict())))
            connection.execute("""INSERT INTO topology_events
                (event_id, topology_id, version_before, version_after, event_kind, event_sha256, created_at_utc, event_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)""", self._event(updated,
                    version_before=current.topology_version, event_kind="simulated_node_added",
                    payload={"node_id": node.node_id, "role": node.role.value,
                             "simulated": True, "physical_action_performed": False}))
        return updated

    def configure_inventory_link(self, request) -> SimulatedTopology:
        """Configure a simple undirected graph atomically; not a quantum operation."""
        from .inventory import ConfigureSimulatedLink
        if not isinstance(request, ConfigureSimulatedLink):
            raise ValueError("validated ConfigureSimulatedLink request required")
        link = request.link()
        with self._lock, closing(self._connect()) as connection, connection:
            if not connection.in_transaction:
                connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT state_json FROM simulated_topologies WHERE topology_id = ?",
                                     (request.topology_id,)).fetchone()
            current = SimulatedTopology.from_dict(json.loads(row["state_json"])) if row else default_simulated_topology(request.topology_id)
            if current.topology_version != request.expected_version:
                raise ValueError("topology version conflict; reload before editing")
            known = {node.node_id for node in current.nodes}
            if link.left_node_id not in known or link.right_node_id not in known:
                raise ValueError("both endpoints must exist in persisted inventory or the default blueprint")
            previous = next((item for item in current.links if item.link_id == link.link_id), None)
            # Between two located nodes the fibre length IS the geodesic (design Â§1);
            # the operator's figure only stands while an endpoint has no location.
            geodesic = link_geodesic_km(current, link)
            if geodesic is not None:
                link = replace(link, distance_km=geodesic)
            if request.mode == "create" and previous is not None:
                raise ValueError("duplicate link_id; use update explicitly")
            if request.mode == "update" and previous is None:
                raise ValueError("cannot update an unknown link_id")
            if previous and (previous.left_node_id, previous.right_node_id) != (link.left_node_id, link.right_node_id):
                raise ValueError("updating link endpoints is not supported")
            if previous and previous.technology is not None:
                if link.channels is None or link.channels['quantum']['protocol'] != 'quantum-operation-record-v1':
                    raise ValueError('a modular protocol link requires quantum-operation-record-v1; configure its protocol session explicitly')
                link = replace(link, technology=previous.technology)
            if any(item.link_id != link.link_id and {item.left_node_id, item.right_node_id} ==
                   {link.left_node_id, link.right_node_id} for item in current.links):
                raise ValueError("parallel or reversed duplicate links are not supported by this editor")
            if previous is None and len(current.links) >= 256:
                raise ValueError("inventory editor supports at most 256 simulated links")
            if previous == link:
                raise ValueError("configuration unchanged")
            links = tuple(link if item.link_id == link.link_id else item for item in current.links)
            if previous is None:
                links = (*links, link)
            nodes = current.nodes
            if link.active:
                nodes = tuple(replace(node, state_version=node.state_version + 1, updated_at_utc=_utc_now(),
                    telemetry={**node.telemetry, 'console_control': {
                        **node.telemetry['console_control'], 'isolated': False,
                        # Reconnecting by hand ends the isolation without restoring the
                        # other fibres, so its restore bookkeeping no longer applies.
                        **({'isolation_disabled_links': []}
                           if 'isolation_disabled_links' in node.telemetry['console_control'] else {})}})
                    if node.node_id in (link.left_node_id, link.right_node_id)
                    and node.telemetry.get('console_control', {}).get('isolated') is True else node
                    for node in nodes)
            updated = replace(current, links=links, nodes=nodes, topology_version=current.topology_version + 1,
                              updated_at_utc=_utc_now())
            connection.execute("""INSERT INTO simulated_topologies
                (topology_id, topology_version, updated_at_utc, state_json) VALUES (?, ?, ?, ?)
                ON CONFLICT(topology_id) DO UPDATE SET topology_version=excluded.topology_version,
                updated_at_utc=excluded.updated_at_utc, state_json=excluded.state_json""",
                (updated.topology_id, updated.topology_version, updated.updated_at_utc, _canonical_json(updated.to_dict())))
            connection.execute("""INSERT INTO topology_events
                (event_id, topology_id, version_before, version_after, event_kind, event_sha256, created_at_utc, event_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)""", self._event(updated,
                    version_before=current.topology_version,
                    event_kind="simulated_link_created" if previous is None else "simulated_link_updated",
                    payload={"before": previous.to_dict() if previous else None, "after": link.to_dict(),
                             "requested_distance_km": float(request.distance_km),
                             "distance_source": "operator_configured" if geodesic is None
                             else "geodesic_from_node_locations",
                             "scope": "simulator_configuration_only", "physical_action_performed": False}))
        return updated

    def apply_inventory_node_action(self, request) -> SimulatedTopology:
        """Change simulator state and incident links together, with an audit event."""
        from .inventory import SimulatedNodeAction
        if not isinstance(request, SimulatedNodeAction):
            raise ValueError('validated SimulatedNodeAction required')
        with self._lock, closing(self._connect()) as connection, connection:
            if not connection.in_transaction:
                connection.execute('BEGIN IMMEDIATE')
            row = connection.execute('SELECT state_json FROM simulated_topologies WHERE topology_id = ?',
                                     (request.topology_id,)).fetchone()
            current = SimulatedTopology.from_dict(json.loads(row['state_json'])) if row else default_simulated_topology(request.topology_id)
            if current.topology_version != request.expected_version:
                raise ValueError('topology version conflict; reload before acting on a node')
            node = current.node(request.node_id)
            control = dict(node.telemetry.get('console_control', {}))
            links = current.links
            changed_links = []
            now = _utc_now()
            if request.action == 'isolate':
                changed_links = [link.link_id for link in links if link.active
                                 and node.node_id in (link.left_node_id, link.right_node_id)]
                if control.get('isolated') is True and not changed_links:
                    raise ValueError('node is already isolated in the simulator')
                # Recorded so that set_node_isolation(isolated=False) can undo exactly this.
                recorded = _isolation_record(control) if control.get('isolated') is True else []
                control['isolation_disabled_links'] = recorded + [
                    link_id for link_id in changed_links if link_id not in recorded]
                control['isolated'] = True
                links = tuple(replace(link, active=False) if node.node_id in
                              (link.left_node_id, link.right_node_id) else link for link in links)
            else:
                control['calibration_count'] = int(control.get('calibration_count', 0)) + 1
                control['calibrated_at_utc'] = now
            control.update(last_action=request.action, last_command_sha256=_sha256(asdict(request)),
                           scope='simulator_only', physical_action_performed=False,
                           memory_generation=node_memory_generation(node) + 1, memory_reset_at_utc=now)
            # Recalibration resets simulated memory; it cannot improve a fidelity
            # or re-enable a disconnected link. Isolation discards the node's pairs.
            updated_node = replace(node, state_version=node.state_version + 1,
                                   memory_state='IDLE', available_pair_fidelities=(), memory_ages_s=(),
                                   telemetry={**node.telemetry, 'console_control': control}, updated_at_utc=now)
            updated = replace(current, nodes=tuple(updated_node if item.node_id == node.node_id else item
                                                   for item in current.nodes), links=links,
                              topology_version=current.topology_version + 1, updated_at_utc=now)
            connection.execute('''INSERT INTO simulated_topologies
                (topology_id, topology_version, updated_at_utc, state_json) VALUES (?, ?, ?, ?)
                ON CONFLICT(topology_id) DO UPDATE SET topology_version=excluded.topology_version,
                updated_at_utc=excluded.updated_at_utc, state_json=excluded.state_json''',
                (updated.topology_id, updated.topology_version, now, _canonical_json(updated.to_dict())))
            connection.execute('''INSERT INTO topology_events
                (event_id, topology_id, version_before, version_after, event_kind, event_sha256, created_at_utc, event_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)''', self._event(updated,
                    version_before=current.topology_version, event_kind=f'simulated_node_{request.action}d',
                    payload={'node_id': node.node_id, 'action': request.action,
                             'disabled_links': changed_links, 'before': node.to_dict(),
                             'after': updated_node.to_dict(), 'scope': 'simulator_only',
                             'physical_action_performed': False}))
        return updated

    # ------------------------------------------------------------------
    # Live-network inventory verbs (design Â§1). Every method is one SQLite
    # transaction, one topology version increment and one audit event.
    # ------------------------------------------------------------------

    def _inventory_mutation(self, request, build) -> SimulatedTopology:
        """Read, version-check, apply ``build`` and append its event atomically.

        ``build(current, now)`` returns ``(nodes, links, event_kind, payload)`` or
        raises ``ValueError`` to refuse the command without touching the store.
        """

        with self._lock, closing(self._connect()) as connection, connection:
            if not connection.in_transaction:
                connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT state_json FROM simulated_topologies WHERE topology_id = ?",
                                     (request.topology_id,)).fetchone()
            current = (SimulatedTopology.from_dict(json.loads(row["state_json"])) if row
                       else default_simulated_topology(request.topology_id))
            if current.topology_version != request.expected_version:
                raise ValueError(f"topology version conflict: expected {request.expected_version}, "
                                 f"found {current.topology_version}; reload before editing")
            now = _utc_now()
            nodes, links, event_kind, payload = build(current, now)
            updated = replace(current, nodes=tuple(nodes), links=tuple(links),
                              topology_version=current.topology_version + 1, updated_at_utc=now)
            connection.execute("""INSERT INTO simulated_topologies
                (topology_id, topology_version, updated_at_utc, state_json) VALUES (?, ?, ?, ?)
                ON CONFLICT(topology_id) DO UPDATE SET topology_version=excluded.topology_version,
                updated_at_utc=excluded.updated_at_utc, state_json=excluded.state_json""",
                (updated.topology_id, updated.topology_version, now, _canonical_json(updated.to_dict())))
            connection.execute("""INSERT INTO topology_events
                (event_id, topology_id, version_before, version_after, event_kind, event_sha256, created_at_utc, event_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)""", self._event(updated,
                    version_before=current.topology_version, event_kind=event_kind,
                    payload={**payload, **_SIMULATOR_ONLY}))
        return updated

    def add_network_node(self, request) -> SimulatedTopology:
        """Add a simulator node, with or without an operator-declared location."""
        from .inventory import AddNetworkNode
        if not isinstance(request, AddNetworkNode):
            raise ValueError("validated AddNetworkNode request required")

        def build(current, now):
            if any(item.node_id == request.node_id for item in current.nodes):
                raise ValueError("duplicate simulated node_id")
            if len(current.nodes) >= 256:
                raise ValueError("inventory editor supports at most 256 simulated nodes")
            node = replace(request.node(now), updated_at_utc=now)
            return (*current.nodes, node), current.links, "network_node_added", {
                "node_id": node.node_id, "role": node.role.value, "located": request.located,
                "before": None, "after": node.to_dict(), "simulated": True}

        return self._inventory_mutation(request, build)

    def set_node_location(self, request) -> SimulatedTopology:
        """Place a node on the map, or return it to the unlocated queue.

        Only ``telemetry["location"]`` of the node changes; memory, inventory
        metadata, isolation state and every link are preserved. Links whose two
        endpoints are located take the geodesic length in this same transaction.
        """
        from .inventory import SetNodeLocation
        if not isinstance(request, SetNodeLocation):
            raise ValueError("validated SetNodeLocation request required")

        def build(current, now):
            node = current.node(request.node_id)
            before = node.telemetry.get("location")
            if request.located:
                existing = node_location(node)
                if existing is not None and (existing["latitude"], existing["longitude"], existing["place"]) == (
                        request.latitude, request.longitude, request.place):
                    raise ValueError("node location unchanged")
                after = request.location(now)
                telemetry = {**node.telemetry, "location": after}
            else:
                if "location" not in node.telemetry:
                    raise ValueError("node has no location to clear")
                after = None
                telemetry = {key: value for key, value in node.telemetry.items() if key != "location"}
            moved = replace(node, state_version=node.state_version + 1, telemetry=telemetry, updated_at_utc=now)
            nodes = tuple(moved if item.node_id == node.node_id else item for item in current.nodes)
            candidate = replace(current, nodes=nodes)
            links, recomputed = [], []
            for link in current.links:
                geodesic = link_geodesic_km(candidate, link)
                incident = node.node_id in (link.left_node_id, link.right_node_id)
                if geodesic is not None and (incident or geodesic != link.distance_km):
                    recomputed.append({"link_id": link.link_id, "distance_km_before": link.distance_km,
                                       "distance_km_after": geodesic})
                    link = replace(link, distance_km=geodesic)
                links.append(link)
            return nodes, links, "node_location_set", {
                "node_id": node.node_id, "before": before, "after": after, "recomputed_links": recomputed}

        return self._inventory_mutation(request, build)

    def set_node_isolation(self, request) -> SimulatedTopology:
        """Isolate a node, or restore exactly the fibres its isolation switched off.

        Isolation disables the incident links that are active at that moment and
        records them in ``console_control.isolation_disabled_links``. Restoration
        re-enables only recorded links that still exist and are still inactive.
        A recorded link whose other endpoint is isolated too is not re-enabled:
        the record moves to that neighbour, whose own restoration brings it back.
        """
        from .inventory import SetNodeIsolation
        if not isinstance(request, SetNodeIsolation):
            raise ValueError("validated SetNodeIsolation request required")
        command_sha256 = _sha256(asdict(request))

        def build(current, now):
            node = current.node(request.node_id)
            control = _console_control(node)
            was_isolated = control.get("isolated") is True
            handed_over: dict[str, list[str]] = {}
            if request.isolated:
                disabled = [link.link_id for link in current.links if link.active
                            and node.node_id in (link.left_node_id, link.right_node_id)]
                if was_isolated and not disabled:
                    raise ValueError("node is already isolated in the simulator")
                recorded = _isolation_record(control) if was_isolated else []
                control["isolation_disabled_links"] = recorded + [
                    link_id for link_id in disabled if link_id not in recorded]
                control["isolated"] = True
                changed = set(disabled)
                detail: dict[str, Any] = {"disabled_links": disabled}
                # Isolation discards the node's simulated pairs, as the legacy action does.
                memory = {"memory_state": "IDLE", "available_pair_fidelities": (), "memory_ages_s": ()}
                control.update(memory_generation=node_memory_generation(node) + 1, memory_reset_at_utc=now)
            else:
                if not was_isolated:
                    raise ValueError("node is not isolated in the simulator; nothing to restore")
                by_id = {link.link_id: link for link in current.links}
                isolated_nodes = {item.node_id for item in current.nodes if _is_isolated(item)}
                restored, deferred, skipped = [], [], []
                for link_id in _isolation_record(control):
                    link = by_id.get(link_id)
                    if link is None or link.active or node.node_id not in (link.left_node_id, link.right_node_id):
                        skipped.append(link_id)  # removed, or already enabled again by other means
                        continue
                    other = link.right_node_id if link.left_node_id == node.node_id else link.left_node_id
                    if other in isolated_nodes:
                        handed_over.setdefault(other, []).append(link_id)
                        deferred.append({"link_id": link_id, "held_by": other})
                    else:
                        restored.append(link_id)
                control["isolation_disabled_links"] = []
                control["isolated"] = False
                changed = set(restored)
                detail = {"restored_links": restored, "deferred_links": deferred, "skipped_links": skipped}
                memory = {}
            control.update(last_action="isolate" if request.isolated else "restore",
                           last_command_sha256=command_sha256, **_SIMULATOR_ONLY)
            updated_node = replace(node, state_version=node.state_version + 1, updated_at_utc=now,
                                   telemetry={**node.telemetry, "console_control": control}, **memory)
            nodes = []
            for item in current.nodes:
                if item.node_id == node.node_id:
                    item = updated_node
                elif item.node_id in handed_over:
                    held = _console_control(item)
                    record = _isolation_record(held)
                    held["isolation_disabled_links"] = record + [
                        link_id for link_id in handed_over[item.node_id] if link_id not in record]
                    item = replace(item, state_version=item.state_version + 1, updated_at_utc=now,
                                   telemetry={**item.telemetry, "console_control": held})
                nodes.append(item)
            links = tuple(replace(link, active=not request.isolated) if link.link_id in changed else link
                          for link in current.links)
            return nodes, links, "node_isolated" if request.isolated else "node_restored", {
                "node_id": node.node_id, "isolated": request.isolated, **detail,
                "before": node.to_dict(), "after": updated_node.to_dict()}

        return self._inventory_mutation(request, build)

    def set_link_active(self, request) -> SimulatedTopology:
        """Switch one fibre on or off; node isolation state is never touched."""
        from .inventory import SetLinkActive
        if not isinstance(request, SetLinkActive):
            raise ValueError("validated SetLinkActive request required")

        def build(current, now):
            link = next((item for item in current.links if item.link_id == request.link_id), None)
            if link is None:
                raise ValueError(f"link_id is outside topology {current.topology_id!r}: {request.link_id!r}")
            if link.active is request.active:
                raise ValueError(f"link is already {'active' if link.active else 'inactive'}")
            if request.active:
                blocked = [node_id for node_id in (link.left_node_id, link.right_node_id)
                           if _is_isolated(current.node(node_id))]
                if blocked:
                    raise ValueError(f"cannot activate link {link.link_id!r} while node "
                                     f"{' and '.join(repr(item) for item in blocked)} is isolated; "
                                     "restore the node first")
            updated_link = replace(link, active=request.active)
            links = tuple(updated_link if item.link_id == link.link_id else item for item in current.links)
            return current.nodes, links, "link_activated" if request.active else "link_deactivated", {
                "link_id": link.link_id, "active": request.active,
                "before": link.to_dict(), "after": updated_link.to_dict()}

        return self._inventory_mutation(request, build)

    def record_node_calibration(self, request, report) -> SimulatedTopology:
        """Persist the report of a full recalibration that the network engine finished.

        The store does not calibrate anything: it validates and stores the engine's
        report (bounded canonical JSON), counts it and resets the node's memory.
        """
        from .inventory import RecalibrateNode
        if not isinstance(request, RecalibrateNode):
            raise ValueError("validated RecalibrateNode request required")
        calibration = _calibration_report(report)
        command_sha256 = _sha256(asdict(request))

        def build(current, now):
            node = current.node(request.node_id)
            control = _console_control(node)
            previous = control.get("calibration")
            control["calibration"] = calibration
            control["calibration_count"] = int(control.get("calibration_count", 0)) + 1
            control["calibrated_at_utc"] = now
            control.update(last_action="recalibrate", last_command_sha256=command_sha256,
                           memory_generation=node_memory_generation(node) + 1, memory_reset_at_utc=now,
                           **_SIMULATOR_ONLY)
            updated_node = replace(node, state_version=node.state_version + 1, memory_state="IDLE",
                                   available_pair_fidelities=(), memory_ages_s=(), updated_at_utc=now,
                                   telemetry={**node.telemetry, "console_control": control})
            nodes = tuple(updated_node if item.node_id == node.node_id else item for item in current.nodes)
            return nodes, current.links, "node_recalibrated", {
                "node_id": node.node_id, "report_sha256": calibration["report_sha256"],
                "previous_report_sha256": previous.get("report_sha256") if isinstance(previous, Mapping) else None,
                "calibration_count": control["calibration_count"],
                "before": node.to_dict(), "after": updated_node.to_dict()}

        return self._inventory_mutation(request, build)

    def inject_pairs(
        self,
        topology_id: str,
        node_id: str,
        fidelities: tuple[float, ...],
        ages_s: tuple[float, ...],
        *,
        telemetry: Mapping[str, Any],
        scenario_id: str,
        step_id: str,
    ) -> SimulatedTopology:
        current = self.get(topology_id)
        if current is None:
            raise ValueError(f"unknown simulated topology: {topology_id!r}")
        node = current.node(node_id)
        if node.role is not NodeRole.REPEATER:
            raise ValueError("simulated pairs may be injected into repeater nodes only")
        if not fidelities:
            raise ValueError("simulated pair injection requires at least one pair")
        now = _utc_now()
        updated_node = SimulatedNodeState(
            node_id=node.node_id,
            role=node.role,
            state_version=node.state_version + 1,
            memory_state="STORING",
            available_pair_fidelities=fidelities,
            memory_ages_s=ages_s,
            telemetry={**node.telemetry, **dict(telemetry)},
            updated_at_utc=now,
        )
        updated_topology = replace(
            current,
            topology_version=current.topology_version + 1,
            nodes=tuple(updated_node if item.node_id == node_id else item for item in current.nodes),
            updated_at_utc=now,
        )
        return self._replace_existing(
            updated_topology,
            expected_version=current.topology_version,
            event_kind="simulated_pairs_injected",
            payload={
                "scenario_id": scenario_id,
                "step_id": step_id,
                "node_id": node_id,
                "pair_count": len(fidelities),
            },
        )

    def apply_cycle(self, topology_id: str, cycle_payload: Mapping[str, Any]) -> SimulatedTopology:
        record_payload = cycle_payload.get("record", cycle_payload)
        if not isinstance(record_payload, Mapping):
            raise ValueError("cycle record must be an object")
        record = ControlCycleRecord.from_dict(record_payload)
        current = self.get(topology_id)
        if current is None:
            raise ValueError(f"unknown simulated topology: {topology_id!r}")
        if record.mode is not ControlMode.SIMULATION or not record.execution.performed_in_simulator:
            return current
        node = current.node(record.snapshot.node_id)
        if node.state_version != record.snapshot.state_version:
            raise ValueError(
                "cycle snapshot no longer matches the persisted simulated node version"
            )
        details = record.execution.details
        action = record.execution.action
        if action in {ControlAction.HALT, ControlAction.SWAP}:
            memory_state = "IDLE"
            pairs: tuple[float, ...] = ()
            ages: tuple[float, ...] = ()
        elif action is ControlAction.WAIT:
            memory_state = "STORING"
            pairs = node.available_pair_fidelities
            ages = tuple(details["resulting_memory_ages_s"])
        elif action is ControlAction.PURIFY and details.get("sampled_success"):
            memory_state = "STORING"
            pairs = (float(details["F_after"]),)
            ages = (0.0,)
        else:
            memory_state = "IDLE"
            pairs = ()
            ages = ()
        now = _utc_now()
        updated_node = SimulatedNodeState(
            node_id=node.node_id,
            role=node.role,
            state_version=node.state_version + 1,
            memory_state=memory_state,
            available_pair_fidelities=pairs,
            memory_ages_s=ages,
            telemetry=dict(record.snapshot.telemetry),
            updated_at_utc=now,
        )
        updated_topology = replace(
            current,
            topology_version=current.topology_version + 1,
            nodes=tuple(
                updated_node if item.node_id == updated_node.node_id else item
                for item in current.nodes
            ),
            updated_at_utc=now,
        )
        return self._replace_existing(
            updated_topology,
            expected_version=current.topology_version,
            event_kind="control_cycle_applied",
            payload={
                "cycle_id": record.cycle_id,
                "request_id": record.snapshot.request_id,
                "node_id": node.node_id,
                "action": action.value,
                "physical_action_performed": False,
            },
        )

    def list_events(self, topology_id: str, limit: int = 20) -> list[dict[str, Any]]:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
            raise ValueError("limit must be an integer in [1, 100]")
        with self._lock, closing(self._connect()) as connection:
            rows = connection.execute(
                """
                SELECT sequence, topology_id, version_before, version_after,
                       event_kind, event_sha256, created_at_utc
                FROM topology_events
                WHERE topology_id = ?
                ORDER BY sequence DESC LIMIT ?
                """,
                (topology_id, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def scenario_run(self, scenario_id: str) -> dict[str, Any] | None:
        with self._lock, closing(self._connect()) as connection:
            row = connection.execute(
                """
                SELECT request_sha256, record_sha256, record_json
                FROM scenario_runs WHERE scenario_id = ?
                """,
                (scenario_id,),
            ).fetchone()
        if row is None:
            return None
        return {
            "request_sha256": row["request_sha256"],
            "record_sha256": row["record_sha256"],
            "record": json.loads(row["record_json"]),
        }

    def append_scenario_run(
        self,
        scenario_id: str,
        request_sha256: str,
        record: Mapping[str, Any],
    ) -> str:
        serialized = _canonical_json(record)
        record_sha256 = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
        with self._lock, closing(self._connect()) as connection, connection:
            connection.execute(
                """
                INSERT INTO scenario_runs
                    (scenario_id, request_sha256, record_sha256,
                     completed_at_utc, record_json)
                VALUES (?, ?, ?, ?, ?)
                """,
                (scenario_id, request_sha256, record_sha256, _utc_now(), serialized),
            )
        return record_sha256


class TopologyScenarioRunner:
    """Run sequential simulated control cycles against persisted node state."""

    def __init__(self, runtime: Any, store: SimulatedTopologyStore) -> None:
        self.runtime = runtime
        self.store = store
        self._lock = RLock()

    def describe(self, topology_id: str = DEFAULT_TOPOLOGY_ID) -> dict[str, Any]:
        persisted = self.store.get(topology_id)
        topology = persisted or default_simulated_topology(topology_id)
        return {
            "topology": topology.to_dict(),
            "persisted": persisted is not None,
            "physical_topology": False,
            "available_presets": [DEFAULT_SCENARIO_PRESET],
            "recent_events": self.store.list_events(topology_id) if persisted else [],
        }

    def run(self, scenario: ScenarioDefinition | Mapping[str, Any]) -> dict[str, Any]:
        if isinstance(scenario, Mapping):
            scenario = ScenarioDefinition.from_dict(scenario)
        if not isinstance(scenario, ScenarioDefinition):
            raise ValueError("scenario must be a ScenarioDefinition or mapping")
        request_payload = scenario.to_dict()
        request_sha256 = _sha256(request_payload)
        transaction = getattr(self.runtime, "scenario_transaction", None)
        scope = transaction(self.store.path) if transaction else nullcontext()
        with self._lock, self.store._lock, scope:
            existing = self.store.scenario_run(scenario.scenario_id)
            if existing is not None:
                if existing["request_sha256"] != request_sha256:
                    raise ValueError("scenario_id was already used with a different payload")
                return {**existing, "idempotent_replay": True}

            if scenario.reset_topology:
                candidate_topology = default_simulated_topology(scenario.topology_id)
            else:
                candidate_topology = self.store.get(
                    scenario.topology_id
                ) or default_simulated_topology(scenario.topology_id)
            for step in scenario.steps:
                candidate_node = candidate_topology.node(step.node_id)
                if candidate_node.role is not NodeRole.REPEATER:
                    raise ValueError("control scenario steps may target repeater nodes only")

            if scenario.reset_topology:
                topology = self.store.reset(scenario.topology_id)
            else:
                topology, _ = self.store.initialize(candidate_topology)
            started_at = _utc_now()
            timeline: list[dict[str, Any]] = []
            for index, step in enumerate(scenario.steps):
                if step.injected_pair_fidelities:
                    topology = self.store.inject_pairs(
                        scenario.topology_id,
                        step.node_id,
                        step.injected_pair_fidelities,
                        step.injected_memory_ages_s,
                        telemetry=step.telemetry,
                        scenario_id=scenario.scenario_id,
                        step_id=step.step_id,
                    )
                node = topology.node(step.node_id)
                if node.role is not NodeRole.REPEATER:
                    raise ValueError("control scenario steps may target repeater nodes only")
                timestamp = _utc_now()
                snapshot = NetworkStateSnapshot(
                    request_id=f"{scenario.scenario_id}:{index:03d}:{step.step_id}",
                    node_id=node.node_id,
                    timestamp_utc=timestamp,
                    state_version=node.state_version,
                    predicted_fidelity=step.predicted_fidelity,
                    prediction_uncertainty=step.prediction_uncertainty,
                    telemetry_age_s=step.telemetry_age_s,
                    memory_state=node.memory_state,
                    available_pair_fidelities=node.available_pair_fidelities,
                    memory_ages_s=node.memory_ages_s,
                    telemetry={
                        **node.telemetry,
                        **step.telemetry,
                        "scenario_id": scenario.scenario_id,
                        "step_id": step.step_id,
                    },
                    out_of_distribution=step.out_of_distribution,
                    integrity_verified=step.integrity_verified,
                    operator_interlock=step.operator_interlock,
                    provenance=Provenance(
                        evidence_kind=EvidenceKind.SIMULATED,
                        source="persistent_simulated_topology_scenario",
                        timestamp=timestamp,
                        metadata={
                            "topology_id": scenario.topology_id,
                            "preset": scenario.preset,
                            "evidence_lane": "bell_fidelity_control",
                            "target_semantics": "simulated_future_bell_fidelity",
                            "physical_calibration": False,
                        },
                    ),
                    metadata={**step.metadata, "scenario_step_index": index},
                )
                topology_version_before = topology.topology_version
                cycle = self.runtime.cycle(snapshot, mode=ControlMode.SIMULATION)
                topology = self.store.apply_cycle(scenario.topology_id, cycle)
                timeline.append(
                    {
                        "step_id": step.step_id,
                        "topology_version_before": topology_version_before,
                        "topology_version_after": topology.topology_version,
                        "cycle": cycle,
                        "node_after": topology.node(step.node_id).to_dict(),
                    }
                )
            record = {
                "scenario_id": scenario.scenario_id,
                "preset": scenario.preset,
                "topology_id": scenario.topology_id,
                "started_at_utc": started_at,
                "completed_at_utc": _utc_now(),
                "request_sha256": request_sha256,
                "physical_action_performed": False,
                "timeline": timeline,
                "final_topology": topology.to_dict(),
                "schema_version": TOPOLOGY_SCHEMA_VERSION,
            }
            record_sha256 = self.store.append_scenario_run(
                scenario.scenario_id,
                request_sha256,
                record,
            )
            return {
                "request_sha256": request_sha256,
                "record_sha256": record_sha256,
                "record": record,
                "idempotent_replay": False,
            }


__all__ = [
    "DEFAULT_SCENARIO_PRESET",
    "DEFAULT_TOPOLOGY_ID",
    "MAX_CALIBRATION_REPORT_BYTES",
    "NodeRole",
    "ScenarioDefinition",
    "ScenarioStep",
    "SimulatedLink",
    "SimulatedNodeState",
    "SimulatedTopology",
    "SimulatedTopologyStore",
    "TOPOLOGY_SCHEMA_VERSION",
    "TopologyScenarioRunner",
    "default_simulated_topology",
    "resilience_demo_scenario",
]
