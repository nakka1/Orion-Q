"""Routing and reallocation of end-to-end demands on the effective graph (design §2.2, step 6).

Nothing here is simulated beyond what the author's own repeater environment is: a plan
is the outcome of real episodes of ``qnet_network.env.QuantumNetworkEnv`` driven by the
routing agent (reviewed replay when a verified archive exists, the author's
``GreedyFidelityPolicy`` otherwise or as hot standby), on the fibres that are usable now.

Availability rule (the single place it is written: :func:`block_reasons`)
------------------------------------------------------------------------
A fibre is in the effective graph when ``link.active`` AND no endpoint is isolated AND
no endpoint is being recalibrated AND its live status is not ``compromised`` (held
attack alarm), and is not measured ``down``. ``degraded`` fibres stay in the graph.
Measured loss of usable key removes that hop; its classical acquisition remains visible.
A fibre without observables (``no_input``, idle source) is usable and carries its
configured ``nominal_pair_fidelity``.

The planning plant (:meth:`Router.plant`) — every parameter and where it comes from
-------------------------------------------------------------------------------------
``EnvironmentConfig`` is rebuilt per plan from the connected component of the source in
the effective graph, with the real fibre lengths. Everything else is the author's
benchmark of ``control_plane.routing_loop.make_config(variant)`` unchanged:

* memory: coherence 1.0 s, raw fidelity 0.90, cutoff ratio 0.1; six slots per node;
* gate / measurement fidelity 0.999; coupling 0.7; detector efficiency 0.85;
* heralding charged at 4.1 span traversals (measured against SeQUeNCe);
* ``generate_budget_s`` 4 ms, ``wait_slot_s`` 2 ms, ``max_steps`` 120, three pairs per
  link, attempt cost of the variant (0.01 nominal, 0.0025 prazo_longo).

The author's environment has ONE raw fidelity for every heralded pair; the per-fibre
fidelity estimate (``1 − 3·QBER/2`` of the QKD session) is a different quantity and is
not fed into it. It is carried on the edges and reported next to the plan as
``link_estimate_fidelity`` (Werner chain with the author's ``bell_diagonal.swap``).

Only two request parameters cannot be the benchmark's, because the benchmark is one
fixed chain:

* ``min_fidelity`` is the demand's (default 0.70, the lowest of the author's three
  choices 0.70 / 0.74 / 0.78: three swapped hops at raw 0.90 give about 0.73);
* ``deadline_s`` = ``max(D · T_path / T_ref, 2 · hops · generate_budget_s)`` where ``D``
  is the midpoint of the author's deadline range for the variant (65 ms nominal),
  ``T = Σ round_trip / p_success`` is the expected heralding time of the shortest path
  and ``T_ref`` the same sum on the benchmark chain. The floor (two full generation
  budgets per hop) is what binds on metropolitan spans: without it the deadline would
  be shorter than the action granularity the author fixed.

``in_contract`` is True only when the plant *is* the reviewed one (4-node 20 km chain,
endpoints "0"→"3", threshold and deadline inside the author's ranges). On any other
topology it is False, whoever acts — the heuristic was benchmarked there too.
"""
from __future__ import annotations

from collections import Counter
from contextlib import closing
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timezone
import json
import math
import re
import time
from typing import Any, Iterable, Mapping

import networkx as nx
import numpy as np

from qnet_network.agents import GreedyFidelityPolicy
from qnet_network.env import EnvironmentConfig, QuantumNetworkEnv, Request
from qnet_network.env.actions import ActionKind
from qnet_network.physics import bell_diagonal as bds

from ..control_plane import routing_loop as rl
from ..control_plane.sqlite_transaction import connect
from . import models as mdl
from .state import ModelStatus, canonical_sha256

MAX_DEMANDS = 16
#: Lowest of the author's three request thresholds (``routing_loop.make_request``).
DEFAULT_MIN_FIDELITY = 0.70
AUTHOR_MIN_FIDELITIES = (0.70, 0.74, 0.78)
AUTHOR_DEADLINE_RANGE_S = (0.040, 0.090)
#: A zero-length fibre has no propagation delay and the author's generation loop divides by it.
MIN_PLANT_LENGTH_KM = 1e-3
_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}")
PAIR_STATUSES = ("delivered", "consumed", "failed", "purification_failed", "invalidated")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


# --------------------------------------------------------------------------
# effective graph
# --------------------------------------------------------------------------

def _isolated(node) -> bool:
    control = node.telemetry.get("console_control", {})
    return isinstance(control, Mapping) and control.get("isolated") is True


def block_reasons(topology, link_live: Mapping[str, Any] | None = None,
                  calibrating_nodes: Iterable[str] = ()) -> dict:
    """``link_id -> tuple of reasons`` why a fibre is NOT usable (empty tuple: usable).

    The one statement of the availability rule; ``effective_graph`` and the bank's
    imposed flags are both derived from it.
    """
    live = link_live or {}
    calibrating = set(calibrating_nodes)
    isolated = {node.node_id for node in topology.nodes if _isolated(node)}
    unavailable={node.node_id for node in topology.nodes if node.telemetry.get('communicator',{}).get('operational_state','enabled')!='enabled'
                 or (node.telemetry.get('communicator',{}).get('revision',0)>0 and node.telemetry.get('communicator',{}).get('integration',{}).get('mode')=='physical')}
    out = {}
    for link in topology.links:
        reasons = []
        ends = (link.left_node_id, link.right_node_id)
        for node_id in ends:
            if node_id in isolated:
                reasons.append(f"node {node_id} isolated")
            if node_id in unavailable:
                reasons.append(f"communicator {node_id} disabled")
        if not link.active and not any(node_id in isolated for node_id in ends):
            reasons.append(f"fibre {link.link_id} disabled")
        for node_id in ends:
            if node_id in calibrating:
                reasons.append(f"node {node_id} calibrating")
        state = live.get(link.link_id)
        if link.technology is not None:
            reasons.append(f"fibre {link.link_id}: optional technology uses its resource protocol; legacy BB84/router admission unavailable")
        if state is not None and getattr(state, "status", None) == "compromised":
            reasons.append(f"fibre {link.link_id} compromised (attack alarm held)")
        if state is not None and getattr(state, "status", None) == "down":
            reasons.append(f"fibre {link.link_id} down (no usable key in the measured period)")
        pair = link.channels
        if pair is None:
            reasons.append(f"fibre {link.link_id}: both channels are not configured")
        elif not pair["classical"]["enabled"] or not pair["quantum"]["enabled"]:
            reasons.append(f"fibre {link.link_id}: a required channel is disabled")
        receipt = {} if state is None else state.provenance.get("channels", {})
        if receipt and receipt.get("source") != "none" and receipt.get("status") != "received":
            reasons.append(f"fibre {link.link_id}: no validated paired-channel acquisition")
        out[link.link_id] = tuple(reasons)
    return out


def imposed_flags(topology, calibrating_nodes: Iterable[str] = ()) -> dict:
    """The flags ``LinkBank.step`` takes: ``disabled | isolated | calibrating`` per fibre."""
    flags = {}
    for link_id, reasons in block_reasons(topology, None, calibrating_nodes).items():
        if any(reason.endswith("isolated") for reason in reasons):
            flags[link_id] = "isolated"
        elif any(reason.endswith("disabled") for reason in reasons):
            flags[link_id] = "disabled"
        elif any(reason.endswith("calibrating") for reason in reasons):
            flags[link_id] = "calibrating"
    return flags


def _edge(link, state) -> dict:
    fidelity = None if state is None else getattr(state, "fidelity", None)
    source = "estimated"
    if fidelity is None:
        fidelity, source = float(link.nominal_pair_fidelity), "nominal"
    return {"length": max(float(link.distance_km), MIN_PLANT_LENGTH_KM), "distance_km": float(link.distance_km),
            "link_id": link.link_id, "fidelity": float(fidelity), "fidelity_source": source,
            "status": None if state is None else state.status}


def _add(graph: nx.Graph, link, state) -> None:
    data = _edge(link, state)
    if graph.has_edge(link.left_node_id, link.right_node_id) \
            and graph[link.left_node_id][link.right_node_id]["length"] <= data["length"]:
        return                                   # parallel fibres: the shortest one represents the hop
    graph.add_edge(link.left_node_id, link.right_node_id, **data)


def configured_graph(topology, link_live: Mapping[str, Any] | None = None) -> nx.Graph:
    """Every configured fibre, whatever its state (used to explain a missing route)."""
    graph = nx.Graph()
    graph.add_nodes_from(node.node_id for node in topology.nodes)
    for link in topology.links:
        _add(graph, link, (link_live or {}).get(link.link_id))
    return graph


def effective_graph(topology, link_live: Mapping[str, Any] | None = None,
                    calibrating_nodes: Iterable[str] = ()) -> nx.Graph:
    """The fibres usable now. Edges carry ``length`` (km), ``link_id``, ``fidelity``
    (current estimate, or the configured nominal value when there is none) and
    ``fidelity_source``. ``graph.graph['blocked']`` maps every unusable fibre to its reasons."""
    reasons = block_reasons(topology, link_live, calibrating_nodes)
    graph = nx.Graph()
    graph.add_nodes_from(node.node_id for node in topology.nodes)
    for link in topology.links:
        if not reasons[link.link_id]:
            _add(graph, link, (link_live or {}).get(link.link_id))
    graph.graph["blocked"] = {link_id: why for link_id, why in reasons.items() if why}
    graph.graph["topology_version"] = int(topology.topology_version)
    return graph


def route_links(graph: nx.Graph, route) -> tuple | None:
    """Link ids along ``route`` in ``graph``, or ``None`` when a hop is not there."""
    out = []
    for a, b in zip(route, route[1:]):
        if not graph.has_edge(a, b):
            return None
        out.append(graph[a][b]["link_id"])
    return tuple(out)


def explain_no_route(topology, graph: nx.Graph, a: str, b: str,
                     link_live: Mapping[str, Any] | None = None) -> dict:
    """Why ``a`` and ``b`` are disconnected in the effective graph.

    ``cut`` is the set of configured fibres that leave the part of the network still
    reachable from ``a``: every one of them is unusable (that is why the part ends
    there) and every path to ``b`` must cross one of them, so restoring none of them
    leaves the demand without a route. ``shortest_path`` is the fewest-kilometres path
    of the configured network, with the unusable elements on it.
    """
    blocked = graph.graph.get("blocked", {})
    full = configured_graph(topology, link_live)
    if a not in full or b not in full:
        return {"reason": f"endpoint {a if a not in full else b} is not in the topology", "cut": [],
                "shortest_path": [], "blockers": []}
    if not nx.has_path(full, a, b):
        return {"reason": f"no fibre path between {a} and {b} is configured in the topology", "cut": [],
                "shortest_path": [], "blockers": []}
    reachable = nx.node_connected_component(graph, a)
    cut = []
    for link in topology.links:
        inside = (link.left_node_id in reachable, link.right_node_id in reachable)
        if inside[0] != inside[1]:
            cut.append({"link_id": link.link_id, "reasons": list(blocked.get(link.link_id, ()))})
    cut.sort(key=lambda item: item["link_id"])
    path = nx.shortest_path(full, a, b, weight="length")
    on_path = []
    for u, v in zip(path, path[1:]):
        link_id = full[u][v]["link_id"]
        if blocked.get(link_id):
            on_path.append({"link_id": link_id, "reasons": list(blocked[link_id])})
    blockers = sorted({reason for item in cut for reason in item["reasons"]})
    shown = "; ".join(blockers[:8]) + (f"; and {len(blockers) - 8} more" if len(blockers) > 8 else "")
    return {"reason": f"no usable route between {a} and {b}: every path crosses an unavailable element ({shown})",
            "cut": cut, "shortest_path": list(path), "shortest_path_blockers": on_path, "blockers": blockers}


# --------------------------------------------------------------------------
# demands (persisted in the journal file)
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class Demand:
    """One end-to-end entanglement demand: deliver pairs between ``a`` and ``b`` at ``min_fidelity``."""

    demand_id: str
    a: str
    b: str
    min_fidelity: float = DEFAULT_MIN_FIDELITY

    def __post_init__(self):
        for name in ("demand_id", "a", "b"):
            value = getattr(self, name)
            if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
                raise ValueError(f"{name} must be 1..64 ASCII letters, digits, dots, underscores or hyphens")
        if self.a == self.b:
            raise ValueError("a demand needs two different endpoints")
        value = self.min_fidelity
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0.5 < float(value) < 1.0:
            raise ValueError("min_fidelity must be a number in (0.5, 1)")
        object.__setattr__(self, "min_fidelity", float(value))

    def to_dict(self) -> dict:
        return asdict(self)


class DemandStore:
    """``network_demands`` table of the journal file: the demands of one topology and their pair."""

    def __init__(self, path, topology_id: str):
        self.path, self.topology_id = path, str(topology_id)
        with closing(connect(self.path)) as connection, connection:
            connection.execute("""CREATE TABLE IF NOT EXISTS network_demands (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                topology_id TEXT NOT NULL,
                demand_id TEXT NOT NULL,
                a TEXT NOT NULL,
                b TEXT NOT NULL,
                min_fidelity REAL NOT NULL,
                created_at_utc TEXT NOT NULL,
                pair_json TEXT,
                UNIQUE(topology_id, demand_id))""")

    def list(self) -> tuple:
        with closing(connect(self.path)) as connection:
            rows = connection.execute("SELECT demand_id, a, b, min_fidelity FROM network_demands WHERE topology_id=? "
                                      "ORDER BY sequence", (self.topology_id,)).fetchall()
        return tuple(Demand(row["demand_id"], row["a"], row["b"], row["min_fidelity"]) for row in rows)

    def define(self, demand: Demand, topology) -> Demand:
        """Create a demand, or replace the endpoints/threshold of an existing one (its pair is dropped)."""
        if not isinstance(demand, Demand):
            raise ValueError("Demand required")
        nodes = {node.node_id for node in topology.nodes}
        for node_id in (demand.a, demand.b):
            if node_id not in nodes:
                raise ValueError(f"demand endpoint {node_id!r} is not a node of topology {topology.topology_id!r}")
        with closing(connect(self.path)) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            known = {row["demand_id"] for row in connection.execute(
                "SELECT demand_id FROM network_demands WHERE topology_id=?", (self.topology_id,))}
            if demand.demand_id in known:
                connection.execute("UPDATE network_demands SET a=?, b=?, min_fidelity=?, pair_json=NULL "
                                   "WHERE topology_id=? AND demand_id=?",
                                   (demand.a, demand.b, demand.min_fidelity, self.topology_id, demand.demand_id))
            else:
                if len(known) >= MAX_DEMANDS:
                    raise ValueError(f"at most {MAX_DEMANDS} demands are supported")
                connection.execute("INSERT INTO network_demands(topology_id, demand_id, a, b, min_fidelity, "
                                   "created_at_utc) VALUES (?, ?, ?, ?, ?, ?)",
                                   (self.topology_id, demand.demand_id, demand.a, demand.b, demand.min_fidelity,
                                    _utc_now()))
        return demand

    def remove(self, demand_id: str) -> None:
        with closing(connect(self.path)) as connection, connection:
            cursor = connection.execute("DELETE FROM network_demands WHERE topology_id=? AND demand_id=?",
                                        (self.topology_id, demand_id))
            if cursor.rowcount != 1:
                raise ValueError(f"unknown demand {demand_id!r}")

    def pair(self, demand_id: str) -> dict | None:
        with closing(connect(self.path)) as connection:
            row = connection.execute("SELECT pair_json FROM network_demands WHERE topology_id=? AND demand_id=?",
                                     (self.topology_id, demand_id)).fetchone()
        if row is None:
            raise ValueError(f"unknown demand {demand_id!r}")
        return None if row["pair_json"] is None else json.loads(row["pair_json"])

    def pairs(self) -> dict:
        with closing(connect(self.path)) as connection:
            rows = connection.execute("SELECT demand_id, pair_json FROM network_demands WHERE topology_id=?",
                                      (self.topology_id,)).fetchall()
        return {row["demand_id"]: json.loads(row["pair_json"]) for row in rows if row["pair_json"] is not None}

    def set_pair(self, demand_id: str, pair: Mapping[str, Any] | None, *,
                 memory_generations: Mapping[str, int] | None = None) -> None:
        raw = None if pair is None else json.dumps(pair, sort_keys=True, allow_nan=False)
        with closing(connect(self.path)) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            if memory_generations is not None:
                # Check the same persisted reset counters while holding SQLite's write lock:
                # another process cannot reset memory between this check and the pair update.
                from ..control_plane.topology import (SimulatedTopology, default_simulated_topology,
                                                      node_memory_generation)
                row = connection.execute("SELECT state_json FROM simulated_topologies WHERE topology_id=?",
                                         (self.topology_id,)).fetchone()
                topology = SimulatedTopology.from_dict(json.loads(row["state_json"])) if row else \
                    default_simulated_topology(self.topology_id)
                nodes = {node.node_id: node for node in topology.nodes}
                if any(node_id not in nodes or node_memory_generation(nodes[node_id]) != generation
                       or _isolated(nodes[node_id]) for node_id, generation in memory_generations.items()):
                    raise ValueError("node memory changed during the pair operation; request a new pair")
            cursor = connection.execute("UPDATE network_demands SET pair_json=? WHERE topology_id=? AND demand_id=?",
                                        (raw, self.topology_id, demand_id))
            if cursor.rowcount != 1:
                raise ValueError(f"unknown demand {demand_id!r}")


# --------------------------------------------------------------------------
# planning records
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class RouterConfig:
    """Declared planning limits (none of them is a physical model)."""

    episodes: int = 24                  # episodes per plan, unless the time budget ends first
    min_episodes: int = 3               # always run this many, whatever the budget
    time_budget_s: float = 0.12         # wall-clock budget of one plan (episodes stop when it is spent)
    variant: str = "nominal"            # reviewed routing export variant: nominal | prazo_longo
    max_faults: int = rl.ReplayRouter.__init__.__kwdefaults__["max_faults"]
    replan_period_ticks: int = 30       # every demand is planned again at least this often

    def __post_init__(self):
        if self.variant not in rl.VARIANTS:
            raise ValueError(f"routing variant must be one of {tuple(rl.VARIANTS)}")
        for name in ("episodes", "min_episodes", "max_faults", "replan_period_ticks"):
            if type(getattr(self, name)) is not int or getattr(self, name) < 1:
                raise ValueError(f"{name} must be a positive integer")
        if self.min_episodes > self.episodes or not self.time_budget_s > 0:
            raise ValueError("min_episodes must not exceed episodes and time_budget_s must be positive")


@dataclass(frozen=True)
class EpisodeResult:
    """One real episode of the author's environment."""

    delivered: bool
    fidelity: float | None
    path: tuple
    swaps_at: tuple
    hops: int | None
    purifications: int
    steps: int
    latency_s: float
    memory_wait_s: float | None
    attempts: int
    faults: int
    state: tuple | None                 # Bell-diagonal state of the delivered pair
    outcome: str                        # delivered | deadline | step_cap


@dataclass(frozen=True)
class RoutePlan:
    demand_id: str
    a: str
    b: str
    status: str                         # routed | no_route | blocked
    route: tuple = ()
    route_links: tuple = ()
    success_rate: float | None = None
    mean_fidelity: float | None = None
    mean_steps: float | None = None
    mean_latency_ms: float | None = None
    episodes: int = 0
    controller_id: str = mdl.ROUTING_BASELINE_ID
    model_state: str = "unavailable"
    in_contract: bool = False
    contract_reasons: tuple = ()
    fallback_used: bool = True
    faults: int = 0
    reason: str = ""
    min_fidelity: float | None = None
    deadline_s: float | None = None
    planning_ms: float = 0.0
    seed: int = 0
    inputs_sha256: str | None = None
    explanation: dict = field(default_factory=dict)
    route_share: float | None = None    # fraction of the delivered pairs that used ``route``

    def numbers(self) -> dict:
        """Plain JSON summary of the plan (what a decision record carries)."""
        return {"status": self.status, "route": list(self.route), "route_links": list(self.route_links),
                "success_rate": self.success_rate, "mean_fidelity": self.mean_fidelity,
                "mean_steps": self.mean_steps, "mean_latency_ms": self.mean_latency_ms, "episodes": self.episodes,
                "route_share": self.route_share, "min_fidelity": self.min_fidelity, "deadline_s": self.deadline_s,
                "planning_ms": round(self.planning_ms, 3), "controller_id": self.controller_id,
                "model_state": self.model_state, "in_contract": self.in_contract,
                "contract_reasons": list(self.contract_reasons), "fallback_used": self.fallback_used,
                "faults": self.faults, "reason": self.reason}


def _join(left: tuple, right: tuple, node: str) -> tuple:
    """Node path of the pair a swap at ``node`` makes from two pairs that meet there."""
    if left[-1] != node:
        left = left[::-1]
    if right[0] != node:
        right = right[::-1]
    if left[-1] != node or right[0] != node:
        raise ValueError("swap inputs do not meet at the swapping node")
    return left + right[1:]


def run_episode(env: QuantumNetworkEnv, request: Request, policy, seed: int) -> EpisodeResult:
    """One episode, with the lineage of every pair traced from the action history.

    The environment does not say which elementary links a delivered pair was built
    from, so the node path of each pair is reconstructed here: a successful GENERATE
    creates ``(u, v)``; a successful SWAP at ``n`` joins the paths of its two inputs;
    a PURIFY keeps the kept pair's path. The route is the path of the delivered pair.
    """
    observation, _ = env.reset(request, memory_size=rl.MEMORY_SIZE, rng=int(seed))
    policy.reset()
    faults_before = getattr(policy, "faults", 0)
    lineage: dict = {}
    path: tuple = ()
    result = None
    while True:
        action = observation[2][int(policy.act(observation))]
        before = set(env.state.pairs)
        joined = None
        if action.kind is ActionKind.SWAP:
            left, right = (lineage.get(pair_id) for pair_id in action.pair_ids)
            joined = _join(left, right, action.node) if left and right else None
        elif action.kind is ActionKind.DELIVER:
            path = lineage.get(action.pair_ids[0], ())
        observation, result = env.step(action)
        created = set(env.state.pairs) - before
        if created:
            pair_id = next(iter(created))
            if action.kind is ActionKind.GENERATE:
                lineage[pair_id] = tuple(action.link)
            elif action.kind is ActionKind.SWAP and joined is not None:
                lineage[pair_id] = joined
        if result.done:
            break
    faults = getattr(policy, "faults", 0) - faults_before
    if not result.terminated:
        outcome = "deadline" if env.state.now_s >= request.deadline_s else "step_cap"
        return EpisodeResult(False, None, (), (), None, 0, env.steps, float(env.state.now_s), None,
                             int(env.state.attempts), faults, None, outcome)
    pair = env.state.delivered[-1]
    if path and path[0] != request.source:
        path = path[::-1]
    return EpisodeResult(True, float(result.info["fidelity"]), tuple(path), tuple(path[1:-1]), int(pair.hops),
                         int(pair.purifications), env.steps, float(env.state.now_s), float(result.info["age_s"]),
                         int(env.state.attempts), faults, tuple(float(value) for value in pair.state), "delivered")


def chain_estimate(graph: nx.Graph, route, config: EnvironmentConfig) -> dict | None:
    """Werner chain over the per-fibre fidelity estimates of ``route`` (author's
    ``bell_diagonal.swap`` with the plant's gate and measurement fidelity, no memory
    decay). A reading of the live fibre estimates, NOT the environment's delivered fidelity."""
    values, sources = [], set()
    for a, b in zip(route, route[1:]):
        if not graph.has_edge(a, b):
            return None
        values.append(float(graph[a][b]["fidelity"]))
        sources.add(graph[a][b]["fidelity_source"])
    if not values:
        return None
    state = bds.werner(values[0])
    for value in values[1:]:
        state = bds.swap(state, bds.werner(value), gate_fid=config.gate_fid, meas_fid=config.meas_fid,
                         is_twirled=config.twirl_on_swap)
    return {"fidelity": float(np.clip(bds.fidelity(state), 0.0, 1.0)), "links": values,
            "source": "estimated" if sources == {"estimated"} else "nominal" if sources == {"nominal"} else "mixed"}


# --------------------------------------------------------------------------
# the router
# --------------------------------------------------------------------------

def outcome_counts(counts: Mapping[str, int]) -> str:
    """``{"deadline": 3, "max_steps": 1}`` as ``"deadline × 3, max steps × 1"`` (most frequent first).

    Every outcome and its count is kept; only the notation is made readable for the operator.
    """
    ordered = sorted(counts.items(), key=lambda item: (-int(item[1]), str(item[0])))
    return ", ".join(f"{str(name).replace('_', ' ')} × {int(count)}" for name, count in ordered) or "none"


class UnviablePlant(ValueError):
    """The configured probabilities cannot yield a finite planning request."""


class Router:
    """Plans a demand on the effective graph with the routing agent under supervision."""

    def __init__(self, registry: mdl.ModelRegistry | None = None, config: RouterConfig | None = None):
        self.registry = registry if registry is not None else mdl.ModelRegistry()
        self.config = config or RouterConfig()
        self._replay = None
        self._replay_archive = None
        self._replay_error = None
        reference = rl.make_config(self.config.variant)
        self._reference = reference
        self._reference_time_s = sum(reference.link_round_trip_s[key] / reference.link_probability[key]
                                     for key in reference.length_km)
        self.model_id = f"qnet.routing.{self.config.variant}"
        self._last = {"in_contract": None, "reason": ""}
        self.publish_status()

    @property
    def reference(self) -> EnvironmentConfig:
        """The author's benchmark configuration of the variant (``routing_loop.make_config``)."""
        return self._reference

    # -- controller -------------------------------------------------------------------

    def _controller(self, rng: np.random.Generator):
        """``(policy, controller_id, model_state, reason)``: the reviewed replay behind
        the author's supervisor (``ReplayRouter``) when a verified archive exists."""
        if not self.registry.control_enabled:
            return GreedyFidelityPolicy(), mdl.ROUTING_BASELINE_ID, "disabled", "ML control disabled by operator; baseline.greedy_fidelity acting"
        archive = self.registry.routing_archive()
        if archive is None:
            self._replay = self._replay_archive = None
            state = self.registry.archive_state("qnet")
            why = f"qnet archive rejected ({state.reason})" if state.state == "rejected" else \
                f"qnet archive not provided ({mdl.ARCHIVE_ENV['qnet']} or set_archive)"
            return GreedyFidelityPolicy(), mdl.ROUTING_BASELINE_ID, "unavailable", \
                f"{why}; {mdl.ROUTING_BASELINE_ID} acting"
        if archive is not self._replay_archive:
            self._replay_archive, self._replay, self._replay_error = archive, None, None
            try:
                self._replay = rl.ReplayRouter(archive, self.config.variant, rng,
                                               max_faults=self.config.max_faults)
            except (ValueError, KeyError, OSError) as exc:
                self._replay_error = f"{type(exc).__name__}: {exc}"
        if self._replay is None:
            return GreedyFidelityPolicy(), mdl.ROUTING_BASELINE_ID, "fallback", \
                f"routing export refused ({self._replay_error}); {mdl.ROUTING_BASELINE_ID} acting"
        self._replay.rng = rng
        return self._replay, self._replay.controller_id, "active", "reviewed replay under the greedy-fidelity standby"

    def publish_status(self) -> ModelStatus:
        """Tell the registry who is routing (``ModelRegistry.status()`` then reports it)."""
        _, controller_id, state, reason = self._controller(np.random.default_rng(0))
        if self.registry.enforce_control_contracts and state == "active" and self._last["in_contract"] is False:
            state = "fallback"
            reason = f"institutional topology outside reviewed routing contract; {mdl.ROUTING_BASELINE_ID} acting"
        archive = self.registry.routing_archive()
        if self._last["reason"]:
            reason = f"{reason}; {self._last['reason']}"
        status = ModelStatus(self.model_id, "routing", state, reason,
                             None if archive is None else str(getattr(archive, "sha256", "")) or None,
                             self._last["in_contract"])
        self.registry.set_role_status(status)
        return status

    # -- plant ----------------------------------------------------------------------

    def plant(self, graph: nx.Graph, demand: Demand) -> tuple:
        """``(EnvironmentConfig, Request, contract_reasons)`` for a demand whose endpoints
        are connected in ``graph`` (see the module docstring for every parameter)."""
        reference, variant = self._reference, rl.VARIANTS[self.config.variant]
        component = graph.subgraph(nx.node_connected_component(graph, demand.a)).copy()
        config = EnvironmentConfig(
            graph=component, heralding_round_trips=reference.heralding_round_trips, memory=reference.memory,
            gate_fid=reference.gate_fid, meas_fid=reference.meas_fid,
            coupling_efficiency=reference.coupling_efficiency, detector_efficiency=reference.detector_efficiency,
            generate_budget_s=reference.generate_budget_s, wait_slot_s=reference.wait_slot_s,
            attempt_cost=reference.attempt_cost, max_steps=reference.max_steps,
            max_pairs_per_link=reference.max_pairs_per_link, shaping_weight=reference.shaping_weight,
            gamma=reference.gamma)
        # Keep the author's probabilities unchanged. Unrepresentable generation times
        # make a fibre unavailable to this plant, rather than inventing a probability floor.
        unusable = []
        for u, v in component.edges():
            key = config.key(u, v)
            probability = config.link_probability[key]
            if probability <= 0.0 or not math.isfinite(probability) \
                    or not math.isfinite(config.link_round_trip_s[key] / probability):
                unusable.append((u, v))
        if unusable:
            component.remove_edges_from(unusable)
            if not nx.has_path(component, demand.a, demand.b):
                fibres = ", ".join(graph[u][v]["link_id"] for u, v in unusable)
                raise UnviablePlant(f"no viable route between {demand.a} and {demand.b}: generation probability "
                                   f"is zero or its expected time is non-finite on fibres {fibres}")
            component = component.subgraph(nx.node_connected_component(component, demand.a)).copy()
            config = replace(config, graph=component)
        path = nx.shortest_path(component, demand.a, demand.b, weight="length")
        hops = len(path) - 1
        heralding = sum(config.link_round_trip_s[config.key(u, v)] / config.link_probability[config.key(u, v)]
                        for u, v in zip(path, path[1:]))
        midpoint = 0.5 * sum(AUTHOR_DEADLINE_RANGE_S) * variant.deadline_scale
        deadline = max(midpoint * heralding / self._reference_time_s, 2.0 * hops * config.generate_budget_s)
        if not math.isfinite(deadline):
            raise UnviablePlant("generation probability gives an unrepresentable route deadline")
        request = Request(source=demand.a, destination=demand.b, min_fidelity=float(demand.min_fidelity),
                          deadline_s=float(deadline))
        return config, request, self._contract(config, request)

    def _contract(self, config: EnvironmentConfig, request: Request) -> tuple:
        """Reasons why this plant is not the reviewed benchmark (empty: it is)."""
        reference, variant = self._reference, rl.VARIANTS[self.config.variant]
        reasons = []
        def edges(graph):
            return {frozenset((str(u), str(v))): float(data["length"]) for u, v, data in graph.edges(data=True)}

        if edges(config.graph) != edges(reference.graph) \
                or sorted(map(str, config.graph.nodes())) != sorted(map(str, reference.graph.nodes())):
            reasons.append("repeater topology differs from the reviewed 4-node 20 km chain")
        if (request.source, request.destination) != ("0", "3"):
            reasons.append("request endpoints differ from the reviewed request (0 -> 3)")
        if request.min_fidelity not in AUTHOR_MIN_FIDELITIES:
            reasons.append("fidelity threshold outside the reviewed choices 0.70 / 0.74 / 0.78")
        low, high = (value * variant.deadline_scale for value in AUTHOR_DEADLINE_RANGE_S)
        if not low <= request.deadline_s <= high:
            reasons.append("deadline outside the reviewed range")
        archive = self.registry.routing_archive()
        if archive is not None and not reasons:
            try:
                verdict = rl.check_contract(archive, self.config.variant, QuantumNetworkEnv(config, rng=0))
                reasons.extend(verdict["reasons"])
            except (ValueError, KeyError, OSError) as exc:
                reasons.append(f"export contract unreadable ({type(exc).__name__})")
        return tuple(reasons)

    # -- planning ------------------------------------------------------------------

    def _episodes(self, config, request, policy, seed: int, budget_s: float) -> list:
        env = QuantumNetworkEnv(config, rng=int(seed))
        started, out = time.perf_counter(), []
        for index in range(self.config.episodes):
            if index >= self.config.min_episodes and time.perf_counter() - started >= budget_s:
                break
            out.append(run_episode(env, request, policy, int(seed) + index))
        return out

    def plan(self, demand: Demand, graph: nx.Graph, *, seed: int, topology=None,
             link_live: Mapping[str, Any] | None = None) -> RoutePlan:
        """Plan ``demand`` on ``graph`` (an :func:`effective_graph`).

        ``topology`` (and ``link_live``) are only used to explain a missing route.
        """
        started = time.perf_counter()
        base = {"demand_id": demand.demand_id, "a": demand.a, "b": demand.b, "seed": int(seed),
                "min_fidelity": float(demand.min_fidelity)}
        rng = np.random.default_rng(int(seed))
        policy, controller_id, state, reason = self._controller(rng)
        if demand.a not in graph or demand.b not in graph or not nx.has_path(graph, demand.a, demand.b):
            explanation = explain_no_route(topology, graph, demand.a, demand.b, link_live) if topology is not None \
                else {"reason": f"no usable route between {demand.a} and {demand.b}", "cut": [], "blockers": []}
            return self._finish(RoutePlan(
                status="no_route", controller_id=controller_id, model_state=state, in_contract=False,
                contract_reasons=("no plant: source and destination are disconnected",),
                fallback_used=state != "active", reason=explanation["reason"], explanation=explanation,
                planning_ms=(time.perf_counter() - started) * 1e3,
                inputs_sha256=canonical_sha256({"edges": [], "demand": demand.to_dict(), "seed": int(seed)}),
                **base), reason)
        try:
            config, request, contract = self.plant(graph, demand)
        except UnviablePlant as exc:
            why = str(exc)
            return self._finish(RoutePlan(
                status="blocked", controller_id=controller_id, model_state=state, in_contract=False,
                contract_reasons=(why,), fallback_used=state != "active", reason=why,
                explanation={"reason": why, "cut": [], "blockers": []},
                planning_ms=(time.perf_counter() - started) * 1e3,
                inputs_sha256=canonical_sha256({"edges": sorted(
                    [min(u, v), max(u, v), data["link_id"], data["length"]]
                    for u, v, data in graph.edges(data=True)), "demand": demand.to_dict(), "seed": int(seed)}),
                **base), reason)
        inputs = canonical_sha256({
            "edges": sorted([min(u, v), max(u, v), data["link_id"], data["length"]]
                            for u, v, data in config.graph.edges(data=True)),
            "demand": demand.to_dict(), "deadline_s": request.deadline_s, "seed": int(seed),
            "variant": self.config.variant})
        learned = state == "active"
        if learned and contract and self.registry.enforce_control_contracts:
            policy, controller_id, state, learned = GreedyFidelityPolicy(), mdl.ROUTING_BASELINE_ID, "fallback", False
            reason = "routing contract refused: " + "; ".join(contract) + f"; {mdl.ROUTING_BASELINE_ID} acting"
        episodes = self._episodes(config, request, policy, seed, self.config.time_budget_s)
        faults = sum(episode.faults for episode in episodes)
        steps = sum(episode.steps for episode in episodes)
        fallback = (not learned) or faults > 0
        note = ""
        if learned and not any(episode.delivered for episode in episodes):
            # Plan-level supervision: the agent delivered nothing on a connected graph; the
            # reference heuristic is given the same request before the demand is called blocked.
            standby = self._episodes(config, request, GreedyFidelityPolicy(), seed, self.config.time_budget_s)
            if any(episode.delivered for episode in standby):
                note = (f"{controller_id} delivered 0 of {len(episodes)} episodes; "
                        f"{mdl.ROUTING_BASELINE_ID} standby planned the route")
                episodes, controller_id, fallback = standby, mdl.ROUTING_BASELINE_ID, True
                steps += sum(episode.steps for episode in standby)
        elapsed_ms = (time.perf_counter() - started) * 1e3
        if learned:
            self.registry.note("routing", inferences=steps, faults=faults, latency_ms=elapsed_ms / max(steps, 1))
        delivered = [episode for episode in episodes if episode.delivered and episode.path]
        common = dict(base, episodes=len(episodes), controller_id=controller_id, model_state=state,
                      in_contract=not contract, contract_reasons=contract, fallback_used=fallback, faults=faults,
                      deadline_s=float(request.deadline_s), planning_ms=elapsed_ms, inputs_sha256=inputs,
                      mean_steps=float(np.mean([episode.steps for episode in episodes])),
                      mean_latency_ms=float(np.mean([episode.latency_s for episode in episodes]) * 1e3))
        if not delivered:
            why = Counter(episode.outcome for episode in episodes)
            text = (f"{demand.a} and {demand.b} are connected but no episode delivered a pair at fidelity >= "
                    f"{demand.min_fidelity:g} within {request.deadline_s * 1e3:.1f} ms "
                    f"(0 of {len(episodes)} episodes delivered; episodes ended by: {outcome_counts(why)})")
            return self._finish(RoutePlan(status="blocked", success_rate=0.0, reason=text, **common), reason)
        tally = Counter(episode.path for episode in delivered)
        best = max(tally, key=lambda path: (tally[path], np.mean([episode.fidelity for episode in delivered
                                                                    if episode.path == path]), path))
        on_route = [episode for episode in delivered if episode.path == best]
        links = route_links(config.graph, best) or ()
        plan = RoutePlan(status="routed", route=tuple(best), route_links=tuple(links),
                         success_rate=len(delivered) / len(episodes),
                         mean_fidelity=float(np.mean([episode.fidelity for episode in on_route])),
                         route_share=len(on_route) / len(delivered), reason=note, **common)
        return self._finish(plan, reason)

    def _finish(self, plan: RoutePlan, controller_reason: str) -> RoutePlan:
        contract = "plant is the reviewed benchmark" if plan.in_contract else \
            "outside the reviewed contract: " + "; ".join(plan.contract_reasons)
        self._last = {"in_contract": plan.in_contract, "reason": contract}
        self.publish_status()
        return plan

    # -- one real episode on a fixed route (operator pair request) ---------------------------

    def episode(self, demand: Demand, graph: nx.Graph, route, *, seed: int) -> tuple:
        """Run ONE episode restricted to the fibres of ``route``; returns
        ``(EpisodeResult, EnvironmentConfig, Request, controller_id)``."""
        links = route_links(graph, route)
        if links is None:
            raise ValueError("the route is not in the effective graph; wait for the reallocation")
        path = nx.Graph()
        for a, b in zip(route, route[1:]):
            path.add_edge(a, b, **graph[a][b])
        config, request, contract = self.plant(path, demand)
        policy, controller_id, state, _ = self._controller(np.random.default_rng(int(seed)))
        if state == "active" and contract and self.registry.enforce_control_contracts:
            policy, controller_id = GreedyFidelityPolicy(), mdl.ROUTING_BASELINE_ID
        result = run_episode(QuantumNetworkEnv(config, rng=int(seed)), request, policy, int(seed))
        if getattr(policy, "faults", 0):
            self.registry.note("routing", faults=result.faults)
        return result, config, request, controller_id


def purify(kept_state, second_state, config: EnvironmentConfig, rng: np.random.Generator) -> dict:
    """One purification round on two delivered pairs with the author's own closed form
    (``bell_diagonal.dejmps`` / ``bbpssw``, the protocol and gate noise of the plant)."""
    protocol = bds.dejmps if config.purification == "dejmps" else bds.bbpssw
    p_success, purified = protocol(np.asarray(kept_state, dtype=float), np.asarray(second_state, dtype=float),
                                   gate_fid=config.gate_fid, meas_fid=config.meas_fid)
    p_success = float(np.clip(p_success, 0.0, 1.0))
    succeeded = bool(rng.random() < p_success)
    return {"protocol": config.purification, "success_probability": p_success, "succeeded": succeeded,
            "state": tuple(float(value) for value in np.asarray(purified, dtype=float)),
            "fidelity": float(np.clip(bds.fidelity(purified), 0.0, 1.0))}


__all__ = ["AUTHOR_MIN_FIDELITIES", "DEFAULT_MIN_FIDELITY", "Demand", "DemandStore", "EpisodeResult",
           "MAX_DEMANDS", "PAIR_STATUSES", "RoutePlan", "Router", "RouterConfig", "block_reasons", "chain_estimate",
           "configured_graph", "effective_graph", "explain_no_route", "imposed_flags", "purify", "route_links",
           "run_episode"]
