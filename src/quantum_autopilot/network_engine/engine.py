"""``NetworkEngine``: one network second per ``step()`` (design Â§2.2 â€“ Â§2.5).

The same object, the same code path, for every input source::

    topology (store) -> input frame (source) -> per-fibre processing (LinkBank)
      -> recalibration procedure -> allocation -> effective graph -> routing / reallocation
      -> node aggregation -> events -> decisions journal -> LiveState

Only ``source`` differs between the demonstration (``SimulatedInputSource``) and normal
operation (``IdleInputSource`` by default): nothing in this module branches on the kind
of source. With no observables the fibres are ``no_input``, routing runs on the
configured values and every measured field is ``None``.

Threading. ``step()`` and ``command()`` take one re-entrant lock; ``live_state()``
returns the last published immutable ``LiveState`` without taking it, so a reader on
another thread never waits for a tick. One thread should call ``step()`` once per
network second; a tick is counted, not timed, so stepping faster only makes network
time run faster.

Nothing here actuates anything physical: every decision row carries
``physical_action_performed: False``. An OGS listener opens only on an explicit
operator command; it receives classical observations on an authenticated local socket.
"""
from __future__ import annotations

from collections import deque
from contextlib import closing
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import json
from threading import RLock
import time
from typing import Any, Mapping
from uuid import uuid4

import numpy as np

from ..control_plane.geodesy import node_location
from ..control_plane.inventory import AddNetworkNode, RecalibrateNode, SetLinkActive, SetNodeIsolation, SetNodeLocation
from ..control_plane.sqlite_transaction import connect
from ..control_plane.topology import DEFAULT_TOPOLOGY_ID, default_simulated_topology, node_memory_generation
from . import models as mdl
from . import routing as rt
from .allocation import Allocator
from .calibration import CalibrationManager
from .channel_io import AcquisitionFileSource, ChannelIntake
from .control_mode import CONTROL_ROLES, read_mode, write_mode
from .links import LinkBank, LinkPolicy, werner_fidelity
from .sources import TICK_S, IdleInputSource, InputEvent, SimulatedInputSource
from .state import (Decision, DemandLive, DetectorLive, LinkLive, LiveState, NetEvent, NodeLive, QkdLive,
                    canonical_json, canonical_sha256)

TOPOLOGY_VERBS = ("set_node_isolation", "set_link_active", "set_node_location", "add_network_node")
VERBS = TOPOLOGY_VERBS + ("recalibrate_node", "define_pair", "remove_pair", "request_pair", "purify_pair",
                          "consume_pair", "inject_input_event", "set_model_archive", "configure_network_input", "set_ml_control",
                          "configure_technology", "configure_technology_source", "export_technology_events",
                          "save_communicator", "configure_communicator_connection", "provision_satellite_session",
                          "register_fpga_device", "configure_fpga_device", "set_fpga_connection", "inject_fpga_fault")
_CONTRACTS = {"set_node_isolation": SetNodeIsolation, "set_link_active": SetLinkActive,
              "set_node_location": SetNodeLocation, "add_network_node": AddNetworkNode}
#: Ticks a blocking recalibration may take before the caller is told it did not finish.
MAX_CALIBRATION_TICKS = 40


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _control(node) -> Mapping[str, Any]:
    control = node.telemetry.get("console_control", {})
    return control if isinstance(control, Mapping) else {}


def _arrow(route) -> str:
    return " -> ".join(route) if route else "(none)"


class DecisionJournal:
    """``network_decisions`` table of the journal file: append-only, one hash per row."""

    def __init__(self, path, *, retention: int = 50_000):
        self.path, self.retention = path, int(retention)
        with closing(connect(self.path)) as connection, connection:
            connection.execute("""CREATE TABLE IF NOT EXISTS network_decisions (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                decision_id TEXT NOT NULL UNIQUE,
                tick INTEGER NOT NULL,
                kind TEXT NOT NULL,
                subject TEXT NOT NULL,
                record_json TEXT NOT NULL,
                record_sha256 TEXT NOT NULL,
                detail_json TEXT)""")
        self._appended = 0

    def append(self, decisions, details: Mapping[str, Any] | None = None) -> int:
        """Append decisions (duplicates by id are ignored); returns how many rows were written."""
        if not decisions:
            return 0
        details = details or {}
        rows = []
        for decision in decisions:
            if not decision.verify():
                raise ValueError(f"decision {decision.decision_id} does not verify; refusing to journal it")
            detail = details.get(decision.decision_id)
            rows.append((decision.decision_id, decision.tick, decision.kind, decision.subject,
                         canonical_json(decision.to_dict()), decision.record_sha256,
                         None if detail is None else canonical_json(detail)))
        with closing(connect(self.path)) as connection, connection:
            cursor = connection.executemany(
                "INSERT OR IGNORE INTO network_decisions(decision_id, tick, kind, subject, record_json, "
                "record_sha256, detail_json) VALUES (?, ?, ?, ?, ?, ?, ?)", rows)
            written = cursor.rowcount
            self._appended += len(rows)
            if self._appended >= 2_000:
                self._appended = 0
                connection.execute("DELETE FROM network_decisions WHERE sequence <= (SELECT MAX(sequence) FROM "
                                   "network_decisions) - ?", (self.retention,))
        return written

    def recent(self, limit: int = 50) -> list:
        """The newest ``limit`` rows (1..500), newest first, each re-verified against its hash."""
        if type(limit) is not int or not 1 <= limit <= 500:
            raise ValueError("decision limit must be 1..500")
        with closing(connect(self.path)) as connection:
            rows = connection.execute("SELECT sequence, decision_id, record_json, record_sha256, detail_json FROM "
                                      "network_decisions ORDER BY sequence DESC LIMIT ?", (limit,)).fetchall()
        out = []
        for row in rows:
            decision = Decision.from_dict(json.loads(row["record_json"]))
            if not decision.verify() or decision.record_sha256 != row["record_sha256"] \
                    or decision.decision_id != row["decision_id"]:
                raise ValueError(f"network decision {row['decision_id']} fails its hash")
            out.append({"sequence": row["sequence"], "record": decision.to_dict(),
                        "record_sha256": row["record_sha256"],
                        "detail": None if row["detail_json"] is None else json.loads(row["detail_json"])})
        return out


class NetworkEngine:
    """The live network: per-fibre processing, routing, allocation, recalibration, journal."""

    def __init__(self, store, journal_path, *, source=None, registry: mdl.ModelRegistry | None = None,
                 topology_id: str = DEFAULT_TOPOLOGY_ID, seed: int = 0, router_config: rt.RouterConfig | None = None,
                 link_policy: LinkPolicy | None = None, max_decisions: int = 200, max_events: int = 200,
                 security=None):
        if type(seed) is not int or seed < 0:
            raise ValueError("explicit non-negative integer seed required")
        self.store, self.journal_path, self.topology_id, self.seed = store, journal_path, str(topology_id), seed
        self.security = security
        self._engine_session_id = uuid4().hex
        self._publication_revision = 0
        self.registry = registry if registry is not None else mdl.ModelRegistry()
        self.registry.control_enabled, self._ml_revision = read_mode(journal_path, self.topology_id)
        self.source = source if source is not None else IdleInputSource()
        from ..quantum_protocols.runtime import ProtocolRuntime
        from ..quantum_protocols.sources import SimulatedAcquisitionSource, NoAcquisitionSource
        self.technologies = ProtocolRuntime(journal_path, str(topology_id),
            source=SimulatedAcquisitionSource(seed) if self.source.kind == "simulated" else NoAcquisitionSource())
        from ..control_plane.communicators import CommunicatorStore
        self.communicators=CommunicatorStore(store,self.topology_id)
        self._satellites={}
        self.global_operations=None
        self._global_operations_state={}
        self._global_component_index={}
        self._ogs_server=None
        from .mission_receiver import MissionReceiverController
        self.mission_receiver = MissionReceiverController(self)
        self.channel_intake = ChannelIntake()
        from ..control_plane.fpga_devices import FpgaDeviceRuntime
        from .protocol_history import ProtocolHistory
        self.fpga = FpgaDeviceRuntime(journal_path, self.topology_id, security=security)
        self._protocol_history = ProtocolHistory()
        self._link_policy = link_policy
        self.bank = LinkBank(self.registry, policy=link_policy, seed=seed)
        self.router = rt.Router(self.registry, router_config)
        self.allocator = Allocator(self.registry)
        self.calibration = CalibrationManager(store, self.bank)
        self.demands = rt.DemandStore(journal_path, self.topology_id)
        self.journal = DecisionJournal(journal_path)
        self._lock = RLock()
        self.tick = 0
        self._decisions: deque = deque(maxlen=int(max_decisions))
        self._events: deque = deque(maxlen=int(max_events))
        self._plans: dict = {}            # demand_id -> RoutePlan
        self._meta: dict = {}             # demand_id -> {"status", "changed", "planned", "trigger", "previous"}
        self._links: dict = {}            # link_id -> LinkLive of the last tick
        self._previous_status: dict = {}  # link_id -> status of the tick before
        self._temperatures: dict = {}     # link_id -> cable temperature of the last frame
        self._topology = None
        self._graph = None
        self._graph_key = None
        self._plant_key = None
        self._blocked_key = None
        self._pair_requests = 0
        self.timings: dict = {"last_step_ms": None, "last_plan_ms": None}
        self._state: LiveState | None = None
        with self._lock:
            at_utc = _utc_now()
            topology = self._read_topology()
            demonstration=any('demonstration_site' in n.telemetry or 'demonstration_repeater' in n.telemetry for n in topology.nodes)
            if demonstration and self.store.get(self.topology_id) is not None:
                topology=self.communicators.seed(topology,'simulated')
                from .global_operations import GlobalOperationManager
                self.global_operations=GlobalOperationManager(journal_path,self.topology_id,seed=seed)
            # Ordinary inventory reads must preserve previously confirmed hashes.
            # Its legacy records are enrolled by an explicit communicator save.
            self.registry.enforce_control_contracts = any("demonstration_site" in node.telemetry for node in topology.nodes)
            self._topology = topology
            self.technologies.sync(topology)
            self.bank.sync(tuple(link for link in topology.links if link.technology is None))
            frame = IdleInputSource().frame(0, topology)          # tick 0: configuration only, nothing processed
            frame = self.channel_intake.ingest(frame, topology)
            links = self.channel_intake.decorate(self.bank.step(frame, self._legacy_flags(topology), at_utc=at_utc).links)
            self._links = {**links, **self._technology_links(topology)}
            self._previous_status = {link_id: live.status for link_id, live in links.items()}
            decisions, events, details = self._reallocate(topology, 0, at_utc, label="engine start", force=True)
            self._publish(topology, 0, at_utc, decisions, events, details)

    # ------------------------------------------------------------------------------------
    # small helpers
    # ------------------------------------------------------------------------------------

    def _read_topology(self):
        topology = self.store.get(self.topology_id)
        return topology if topology is not None else default_simulated_topology(self.topology_id)

    def _legacy_flags(self,topology,calibrating_nodes=()):
        legacy=replace(topology,links=tuple(link for link in topology.links if link.technology is None))
        return rt.imposed_flags(legacy,calibrating_nodes)

    def _seed(self, purpose: str, *parts) -> int:
        digest = hashlib.sha256(("orion.network-engine.v1|" + "|".join(map(str, (self.seed, purpose) + parts)))
                                .encode("utf-8")).digest()
        return int.from_bytes(digest[:8], "big") >> 1

    def _event(self, tick, at_utc, kind, subject, message, severity="info", source="engine") -> NetEvent:
        return NetEvent.create(tick=int(tick), at_utc=at_utc, kind=kind, subject=subject, severity=severity,
                               message=message, source=source)

    # ------------------------------------------------------------------------------------
    # public read API
    # ------------------------------------------------------------------------------------

    def live_state(self) -> LiveState:
        """The last published state. Never steps, never blocks on a running tick."""
        return self._state

    def declared_policy(self) -> dict:
        """The thresholds this engine instance applies, as numbers (published in every ``LiveState``).

        They are read from the objects that use them â€” the link bank's ``LinkPolicy`` and the
        router's ``RouterConfig`` â€” so a non-default engine reports its own values.
        """
        policy, router = self.bank.policy, self.router.config
        return {
            "qber_down": float(policy.qber_down),
            "fidelity_at_qber_down": werner_fidelity(policy.qber_down),
            "degraded_key_fraction": float(policy.degraded_key_fraction),
            "degraded_gate_fraction": float(policy.degraded_gate_fraction),
            "alarm_clear_periods": int(policy.alarm_clear_periods),
            "stability_window_periods": int(policy.stability_window),
            "wave_rate_hz": int(policy.wave_hz),
            "wave_window_s": int(policy.wave_seconds),
            "history_window_s": int(policy.history_seconds),
            "replan_period_ticks": int(router.replan_period_ticks),
            "default_min_fidelity": float(rt.DEFAULT_MIN_FIDELITY),
            "tick_s": float(TICK_S),
            "ml_control_enabled": self.registry.control_enabled,
            "ml_control_revision": self._ml_revision,
        }

    def recent_decisions(self, limit: int = 50) -> list:
        """Newest journal rows (bounded, hash-verified): ``[{sequence, record, record_sha256, detail}]``."""
        return self.journal.recent(limit)

    def topology(self):
        with self._lock:
            return self._topology

    # ------------------------------------------------------------------------------------
    # one network second
    # ------------------------------------------------------------------------------------

    def step(self) -> LiveState:
        with self._lock:
            started = time.perf_counter()
            self.tick += 1
            tick, at_utc = self.tick, _utc_now()
            topology = self._read_topology()
            events = self._topology_events(self._topology, topology, tick, at_utc)
            self._topology = topology
            legacy = replace(topology, links=tuple(link for link in topology.links if link.technology is None))
            self.bank.sync(legacy.links)
            actuator = self.channel_intake.actuator(self.source, topology)
            self.calibration.before_frame(actuator, tick=tick)
            raw_frame = self.fpga.transfer_frame(self.source.frame(tick, legacy), legacy)
            frame = self.channel_intake.ingest(raw_frame, legacy)
            banked = self.bank.step(frame, self._legacy_flags(topology, self.calibration.nodes()), at_utc=at_utc)
            self.fpga.processing_feedback(self.channel_intake.receipts, banked.links, banked.decisions)
            actuator.actuate(banked.commands)
            decisions = list(banked.decisions)
            self._temperatures = {link_id: float(item.sync.temperature_c) for link_id, item in frame.links.items()}
            outcome = self.calibration.after_bank(tick=tick, at_utc=at_utc)
            decisions.extend(outcome.decisions)
            events.extend(outcome.events)
            if outcome.topology_changed:
                self._topology = topology = self._read_topology()
            links, allocated = self.allocator.step(banked.links, tick=tick, at_utc=at_utc)
            links = self.channel_intake.decorate(links)
            if self.global_operations is not None:
                self.global_operations.prepare(topology,self.technologies,self._links,tick)
            self.technologies.advance(topology, tick * TICK_S)
            links = {**links, **self._technology_links(topology)}
            decisions.extend(allocated)
            events.extend(self._link_events(links, tick, at_utc))
            events.extend(self._input_events(frame, tick, at_utc))
            self._links = dict(links)
            ended = outcome.finished is not None
            routed, route_events, details = self._reallocate(
                topology, tick, at_utc, force=ended,
                label=f"node {outcome.node_id} recalibration ended" if ended else None)
            decisions.extend(routed)
            events.extend(route_events)
            state = self._publish(topology, tick, at_utc, decisions, events, details)
            self.timings["last_step_ms"] = (time.perf_counter() - started) * 1e3
            return state

    # -- events ---------------------------------------------------------------------------

    def _topology_events(self, before, after, tick, at_utc) -> list:
        """Operator-visible topology changes, whoever made them (engine command or the store)."""
        if before is None or before.topology_version == after.topology_version:
            return []
        events = []
        old_nodes = {node.node_id: node for node in before.nodes}
        old_links = {link.link_id: link for link in before.links}
        for node in after.nodes:
            previous = old_nodes.get(node.node_id)
            if previous is None:
                events.append(self._event(tick, at_utc, "node_added", node.node_id,
                                          f"node {node.node_id} added ({node.role.value}, "
                                          f"{'located' if node_location(node) else 'no location'})"))
                continue
            was, now = _control(previous).get("isolated") is True, _control(node).get("isolated") is True
            if was != now:
                events.append(self._event(tick, at_utc, "node_isolated" if now else "node_restored", node.node_id,
                                          f"node {node.node_id} " + ("isolated: its fibres are out of the effective "
                                                                     "graph" if now else "restored"),
                                          "warning" if now else "info"))
            if node_location(previous) != node_location(node):
                place = node_location(node)
                events.append(self._event(tick, at_utc, "node_location_set", node.node_id,
                                          f"node {node.node_id} " + ("returned to the unlocated queue" if place is None
                                                                     else f"placed at {place['latitude']:.5f}, "
                                                                          f"{place['longitude']:.5f}")))
        for link in after.links:
            previous = old_links.get(link.link_id)
            if previous is None:
                events.append(self._event(tick, at_utc, "fibre_added", link.link_id,
                                          f"fibre {link.link_id} added ({link.distance_km:g} km)"))
            elif previous.active != link.active:
                events.append(self._event(tick, at_utc, "fibre_enabled" if link.active else "fibre_disabled",
                                          link.link_id, f"fibre {link.link_id} " +
                                          ("enabled" if link.active else "disabled"),
                                          "info" if link.active else "warning"))
        for link_id in set(old_links) - {link.link_id for link in after.links}:
            events.append(self._event(tick, at_utc, "fibre_removed", link_id, f"fibre {link_id} removed", "warning"))
        return events

    def _link_events(self, links: Mapping[str, LinkLive], tick, at_utc) -> list:
        events = []
        for link_id, live in sorted(links.items()):
            before, now = self._previous_status.get(link_id), live.status
            if before == now:
                continue
            if now == "compromised":
                label = live.detector.label or "attack"
                events.append(self._event(tick, at_utc, "attack_alarm_raised", link_id,
                                          f"attack alarm on {link_id} (detector: {label}); key discarded, fibre out "
                                          "of the effective graph", "critical"))
            elif before == "compromised":
                events.append(self._event(tick, at_utc, "attack_alarm_cleared", link_id,
                                          f"attack alarm on {link_id} released; fibre status {now}"))
            if now == "down":
                events.append(self._event(tick, at_utc, "link_down", link_id,
                                          f"fibre {link_id} yields no key (QBER {live.qber if live.qber is None else format(live.qber, '.3f')})",
                                          "critical"))
            elif now == "degraded" and before in ("up", None, "no_input"):
                reasons = live.provenance.get("status_reasons") or []
                events.append(self._event(tick, at_utc, "link_degraded", link_id,
                                          f"fibre {link_id} degraded" + (f": {reasons[0]}" if reasons else ""),
                                          "warning"))
            elif now == "up" and before in ("down", "degraded", "compromised"):
                events.append(self._event(tick, at_utc, "link_recovered", link_id, f"fibre {link_id} recovered"))
        self._previous_status = {link_id: live.status for link_id, live in links.items()}
        return events

    def _input_events(self, frame, tick, at_utc) -> list:
        """What the INPUT source says happened to the input (never a result of the processing)."""
        events = []
        for item in frame.events:
            if item.phase not in ("started", "ended"):
                continue
            events.append(self._event(tick, at_utc, f"input_event_{item.phase}", item.link_id,
                                      f"input {item.kind} on {item.link_id} {item.phase} ({item.origin}; severity "
                                      f"{item.severity:g})", "info", "input_source"))
        return events

    # -- routing / reallocation --------------------------------------------------------------

    def _route_trigger(self, plan, graph, topology) -> str | None:
        """Why the current route of a routed demand is no longer in the effective graph."""
        blocked = graph.graph.get("blocked", {})
        known = {link.link_id for link in topology.links}
        reasons = []
        for (a, b), link_id in zip(zip(plan.route, plan.route[1:]), plan.route_links):
            if graph.has_edge(a, b) and graph[a][b]["link_id"] == link_id:
                continue
            if link_id not in known:
                reasons.append(f"fibre {link_id} removed")
            elif blocked.get(link_id):
                reasons.extend(blocked[link_id])
            elif not graph.has_edge(a, b):
                reasons.append(f"fibre {link_id} no longer joins {a} and {b}")
        return "; ".join(dict.fromkeys(reasons)) or None

    def _reallocate(self, topology, tick, at_utc, *, label: str | None = None, force: bool = False) -> tuple:
        """Plan every demand that needs it. Returns ``(decisions, events, details)``.

        A demand is planned when it has no plan, when its route left the effective graph
        (the trigger then names what took it out), when the usable fibres changed while it
        had no route or was blocked, on the periodic re-plan, or â€” ``force`` â€” because a
        command or a finished recalibration may have opened a better path. ``label`` is the
        trigger recorded when the engine itself has nothing more specific to say.
        """
        started = time.perf_counter()
        graph = rt.effective_graph(topology, self._links, self.calibration.nodes())
        self._graph = graph
        usable = frozenset(data["link_id"] for _, _, data in graph.edges(data=True))
        gained = sorted(usable - self._graph_key) if self._graph_key is not None else []
        lost = sorted(self._graph_key - usable) if self._graph_key is not None else []
        lost_reasons = "; ".join(dict.fromkeys(
            reason for link_id in lost
            for reason in graph.graph["blocked"].get(link_id, (f"fibre {link_id} removed",)))) or None
        graph_changed, self._graph_key = usable != self._graph_key, usable
        plant_key = tuple(sorted((data["link_id"], data["length"]) for _, _, data in graph.edges(data=True)))
        lengths_changed = self._plant_key is not None and plant_key != self._plant_key and not graph_changed
        self._plant_key = plant_key
        if lengths_changed:
            force, label = True, label or "fibre lengths changed"
        blocked = canonical_sha256(graph.graph["blocked"])
        blocked_changed, self._blocked_key = blocked != self._blocked_key, blocked
        if gained and not force:
            # A fibre came back (alarm released, node restored, fibre enabled): every demand is planned
            # again at once, so a route can return to a better path without waiting for the periodic plan.
            force, label = True, label or ("fibre " + ", ".join(gained) + " usable again")
        demands = self.demands.list()
        for stale in set(self._plans) - {demand.demand_id for demand in demands}:
            self._plans.pop(stale, None)
            self._meta.pop(stale, None)
        period = self.router.config.replan_period_ticks
        decisions, events, details = [], [], {}
        for index, demand in enumerate(demands):
            plan, meta = self._plans.get(demand.demand_id), self._meta.get(demand.demand_id)
            trigger = None
            if plan is None or (plan.a, plan.b, plan.min_fidelity) != (demand.a, demand.b, demand.min_fidelity):
                trigger = label or "demand defined"
            elif plan.status == "routed":
                trigger = self._route_trigger(plan, graph, topology)
            elif graph_changed or (plan.status == "no_route" and blocked_changed):
                # No episode is run for a demand without a path: this only refreshes its explanation.
                trigger = label or lost_reasons or "usable fibres changed"
            if trigger is None and force:
                trigger = label or "re-plan requested"
            if trigger is None and meta is not None and tick - meta["planned"] >= period:
                trigger = "periodic re-plan"
            if trigger is None:
                continue
            new = self.router.plan(demand, graph, seed=self._seed("plan", demand.demand_id, tick),
                                   topology=topology, link_live=self._links)
            # Staggered so that the periodic re-plans of several demands do not share a tick.
            planned = tick if meta is not None else tick - (index * period) // max(len(demands), 1)
            changed = plan is None or (plan.status, plan.route) != (new.status, new.route)
            status = new.status
            if new.status == "routed" and plan is not None and plan.status == "routed" and plan.route != new.route:
                status = "rerouted"
            elif new.status == "routed" and not changed and meta is not None and meta["status"] == "rerouted" \
                    and trigger != "periodic re-plan":
                status = "rerouted"
            self._plans[demand.demand_id] = new
            self._meta[demand.demand_id] = {
                "status": status, "planned": planned,
                "changed": tick if changed else (meta["changed"] if meta else tick),
                "trigger": trigger if changed else (meta["trigger"] if meta else trigger),
                "previous": (list(plan.route) if plan is not None else []) if changed else
                            (meta["previous"] if meta else [])}
            if not changed:
                continue
            old_route = () if plan is None else plan.route
            kind = "reroute" if new.status == "routed" else "no_route"
            if new.status == "routed":
                summary = (f"{demand.demand_id} {demand.a}<->{demand.b}: route {_arrow(old_route)} => "
                           f"{_arrow(new.route)}; trigger: {trigger}; success {new.success_rate:.2f} over "
                           f"{new.episodes} episodes, delivered fidelity {new.mean_fidelity:.3f}, "
                           f"{new.mean_latency_ms:.1f} ms mean; controller {new.controller_id}")
            else:
                summary = (f"{demand.demand_id} {demand.a}<->{demand.b}: {new.status.upper()} (was "
                           f"{_arrow(old_route)}); trigger: {trigger}; {new.reason}; controller {new.controller_id}")
            decision = Decision.create(
                tick=int(tick), at_utc=at_utc, kind=kind, controller_id=new.controller_id,
                model_state=new.model_state, subject=demand.demand_id, summary=summary,
                inputs_sha256=new.inputs_sha256, outcome=status, in_contract=new.in_contract,
                fallback_used=new.fallback_used)
            decisions.append(decision)
            details[decision.decision_id] = {"trigger": trigger, "old_route": list(old_route),
                                             "new_route": list(new.route), "plan": new.numbers(),
                                             "explanation": new.explanation,
                                             "physical_action_performed": False}
            severity = "info" if new.status == "routed" else "critical" if new.status == "no_route" else "warning"
            events.append(self._event(tick, at_utc, "reroute" if new.status == "routed" else new.status,
                                      demand.demand_id, summary, severity))
        self.timings["last_plan_ms"] = (time.perf_counter() - started) * 1e3
        return decisions, events, details

    # -- publication ----------------------------------------------------------------------------

    def _demand_live(self, demand, pairs) -> DemandLive:
        plan, meta = self._plans.get(demand.demand_id), self._meta.get(demand.demand_id)
        if plan is None:
            return DemandLive(demand.demand_id, demand.a, demand.b, "idle", reason="not planned yet",
                              min_fidelity=demand.min_fidelity, pair=pairs.get(demand.demand_id))
        detail = plan.numbers()
        detail.update(trigger=meta["trigger"], previous_route=list(meta["previous"]), planned_at_tick=meta["planned"],
                      explanation=plan.explanation)
        if plan.status == "routed" and self._graph is not None:
            detail["link_estimate_fidelity"] = rt.chain_estimate(self._graph, plan.route, self.router.reference)
        return DemandLive(
            demand.demand_id, demand.a, demand.b, meta["status"], tuple(plan.route), tuple(plan.route_links),
            plan.mean_fidelity, plan.success_rate, plan.controller_id, plan.in_contract, plan.fallback_used,
            plan.reason, meta["changed"], demand.min_fidelity, pairs.get(demand.demand_id), detail)

    def _node_live(self, node, topology, demands) -> NodeLive:
        incident = [link for link in topology.links if node.node_id in (link.left_node_id, link.right_node_id)]
        statuses = [self._links[link.link_id].status for link in incident if link.link_id in self._links]
        blocked = {} if self._graph is None else self._graph.graph["blocked"]
        unusable = [(link.technology is None and link.link_id in blocked) or
                    link.link_id not in self._links or
                    self._links[link.link_id].status == "down" or
                    (link.technology is not None and self._links[link.link_id].status in ("disabled", "no_input"))
                    for link in incident]
        control = _control(node)
        if control.get("isolated") is True:
            status = "isolated"
        elif node.node_id in self.calibration.nodes():
            status = "calibrating"
        elif unusable and all(unusable):
            status = "down"                   # no usable fibre left (availability rule, or no key on any)
        elif any(item in ("degraded", "down", "compromised") for item in statuses):
            status = "degraded"
        else:
            status = "up"
        location = node_location(node)
        inventory = node.telemetry.get("inventory", {})
        inventory = inventory if isinstance(inventory, Mapping) else {}
        kind = inventory.get("kind") if isinstance(inventory.get("kind"), str) else \
            ("repeater" if node.role.value == "repeater" else "node")
        record=node.telemetry.get('communicator',{})
        physical=record.get('physical_type',kind)
        if physical=='satellite':
            kind='satellite'
            orbit=self._satellites.get(node.node_id)
            if orbit and "sat_latitude" in orbit and "sat_longitude" in orbit:location={'latitude':orbit['sat_latitude'],'longitude':orbit['sat_longitude'],'place':record.get('name',node.node_id)}
            else:location=None
        if record.get('operational_state','enabled')!='enabled': status='down'
        temperatures = [self._temperatures[link.link_id] for link in incident if link.link_id in self._temperatures]
        count = control.get("calibration_count", 0)
        port = inventory.get("port")
        return NodeLive(
            node_id=node.node_id, role=node.role.value, kind=kind, status=status, located=location is not None,
            latitude=None if location is None else location["latitude"],
            longitude=None if location is None else location["longitude"],
            place=None if location is None else location["place"],
            links=tuple(link.link_id for link in incident), memory_state=node.memory_state,
            pairs=tuple(node.available_pair_fidelities),
            temperature_c=float(np.mean(temperatures)) if temperatures else None,
            calibration=self.calibration.live(node), calibration_count=count if type(count) is int and count >= 0 else 0,
            routes_through=tuple(item.demand_id for item in demands if node.node_id in item.route),
            address=inventory.get("address") if isinstance(inventory.get("address"), str) else None,
            port=port if type(port) is int else None, communicator=dict(record))

    def _publish(self, topology, tick, at_utc, decisions, events, details) -> LiveState:
        site_nodes = {node.node_id for node in topology.nodes if "demonstration_site" in node.telemetry}
        repeater_nodes = {node.node_id for node in topology.nodes if "demonstration_repeater" in node.telemetry}
        for ident, link in tuple(self._links.items()):
            if link.provenance.get("technology") is not None:
                continue
            if link.a in site_nodes | repeater_nodes and link.b in site_nodes | repeater_nodes:
                self._links[ident] = replace(link, provenance={**link.provenance,
                    "fibre_geometry": ("hypothetical repeater span; WGS84 geodesic lower bound, no surveyed route"
                                       if link.a in repeater_nodes or link.b in repeater_nodes else
                                       "hypothetical direct span; WGS84 geodesic lower bound, no surveyed route"),
                    "equipment_parameters": "published simulation reference, not institution hardware",
                    "site_positions": "approximate campuses and hypothetical interpolated repeaters; not surveyed terminals",
                    "repeater_scope": "per-span acquisition and QKD only; end-to-end entanglement requires a successful routing episode"})
        self.journal.append(decisions, details)
        self._decisions.extend(decisions)
        events = list(events) + self._reconcile_pairs(topology, tick, at_utc)
        self._events.extend(events)
        pairs = self.demands.pairs()
        demands = tuple(self._demand_live(demand, pairs) for demand in self.demands.list())
        technology_state=self.technologies.snapshot()
        technology_state['fpga_devices'] = self.fpga.snapshot()
        if self.global_operations is not None:
            self._global_operations_state=self.global_operations.observe(
                topology,self.technologies,self._links,tick,legacy_demands=demands)
            self._global_component_index={row['component']:row for row in self._global_operations_state.get('components',())}
            technology_state['global_operations']=self._global_operations_state
        self._update_satellites(topology)
        mission = self.mission_receiver.descriptor()
        for node in topology.nodes:
            record = node.telemetry.get('communicator', {})
            state = self._satellites.get(node.node_id)
            if state is not None and 'satellite_mission_ingress' not in state and record.get('mission') and 'classical_telemetry' in record.get('capabilities', []):
                state['satellite_mission_ingress'] = {'state': 'not_configured',
                    'transport_state': 'stopped', 'listener_active': False, 'latest': None,
                    'source_id': None, 'physical_actuation': False, 'physical_adapter_validated': False}
        if mission is not None:
            technology_state['satellite_mission_ingress'] = mission
        order = [link.link_id for link in topology.links if link.link_id in self._links]
        source_info = self.source.info()
        technology_state['protocol_series'] = self._protocol_history.update(
            technology_state, source_kind=source_info.kind, network_time_s=float(tick) * TICK_S, at_utc=at_utc)
        publication_revision = self._publication_revision + 1
        state = LiveState(
            tick=int(tick), at_utc=at_utc, network_time_s=float(tick) * TICK_S,
            topology_version=int(topology.topology_version), source=source_info,
            links=tuple(self._links[link_id] for link_id in order),
            nodes=tuple(self._node_live(node, topology, demands) for node in topology.nodes),
            demands=demands, decisions=tuple(self._decisions), models=self.registry.status(),
            events=tuple(self._events), policy=self.declared_policy(), technologies=technology_state,
            communicators={n.node_id:n.telemetry['communicator'] for n in topology.nodes if 'communicator' in n.telemetry},
            satellites=dict(self._satellites), engine_session_id=self._engine_session_id,
            publication_revision=publication_revision)
        self._publication_revision = publication_revision
        self._state = state
        return state

    def _technology_links(self, topology):
        """Read derived protocol observations; keep estimates out of admitted key pools."""
        out = {}
        snapshot = self.technologies.snapshot()
        for link in topology.links:
            if link.technology is None:
                continue
            session = snapshot["sessions"].get(link.technology["session_id"], {})
            metrics = session.get("metrics", {})
            state = session.get("status", "configured")
            fidelity = None
            distance = link.distance_km
            segment = metrics.get("segments", {}).get(f"{link.left_node_id}|{link.right_node_id}")
            if segment:
                fidelity = segment.get("fidelity_estimate")
            status = "no_input" if "last_observed_at_s" not in metrics else "up"
            if state in ("incompatible", "unavailable", "error", "disabled"):
                status = "down" if link.active else "disabled"
            elif not link.active:
                status = "disabled"
            elif state == "operational" and "tf_qkd" in link.technology["technologies"]:
                status = "degraded"  # transport observed; security admission remains withheld
            runtime=self.technologies.sessions.get(link.technology['session_id'])
            controls_available=runtime is not None and self.technologies._available(runtime)
            if not controls_available:
                status='disabled' if not link.active or runtime is not None and not runtime.config.enabled else 'down'
            if segment and not segment.get("heralds"):
                status = "down"
            if status in ("down", "disabled", "no_input"):
                fidelity = None
            geometry = metrics.get('geometry', ())
            if geometry:
                endpoints = session['config']['endpoints']
                ground = next((n for n in endpoints if n in (link.left_node_id,link.right_node_id)),None)
                if ground is not None:
                    distance = geometry[endpoints.index(ground)]['range_km']
                if status == 'up': fidelity = metrics.get('fidelity')
            reasons=[] if controls_available else ['session, node or paired channel unavailable']
            if 'satellite_isl' in link.technology['technologies']:
                fidelity=None
                config=session.get('config',{});clock=self.technologies.clock_s
                housekeeping=metrics.get('satellite_housekeeping',{})
                fresh=bool(housekeeping) and all(clock<=hk.get('valid_until_s',-1) for hk in housekeeping.values()) and (
                    0<=clock-metrics.get('last_observed_at_s',-1e12)<=config.get('parameters',{}).get('block_s',2.)+config.get('parameters',{}).get('timeout_s',4.))
                geo=metrics.get('isl_geometry',{})
                if geo.get('range_km') is not None:distance=geo['range_km']
                runtime=self.technologies.sessions.get(link.technology['session_id'])
                if not link.active or runtime is None or not self.technologies._available(runtime):
                    status='disabled' if not link.active else 'down';reasons.append('session, node or paired channel unavailable')
                elif not fresh:
                    status='no_input';reasons.append('fresh engineering/tracking/TDC observations from both satellites required')
                elif not metrics.get('available') or not geo.get('visible'):
                    status='down';reasons.extend(geo.get('block_reasons') or [metrics.get('reason','ISL geometry / optical terminal unavailable')])
                else:
                    status='up' if metrics.get('coincidences',0)>0 else 'degraded'
                    if status!='up':reasons.append('terminals available; no joint detections in the latest received block')
            if 'satellite_doppler' in link.technology['technologies']:
                # A joint BBM92 estimate is not the fidelity of an individual
                # optical arm. Each arm's state requires fresh received data.
                fidelity=None
                config=session.get('config',{});hk=metrics.get('housekeeping',{})
                clock=self.technologies.clock_s
                fresh=bool(hk) and clock<=hk.get('valid_until_s',-1) and 0<=clock-metrics.get('last_observed_at_s',-1e12)<=config.get('parameters',{}).get('block_s',2.)+config.get('parameters',{}).get('timeout_s',4.)
                endpoint=next((n for n in config.get('endpoints',()) if n in (link.left_node_id,link.right_node_id)),None)
                index=config.get('endpoints',[]).index(endpoint) if endpoint is not None else -1
                geo=geometry[index] if 0<=index<len(geometry) else {}
                runtime=self.technologies.sessions.get(link.technology['session_id'])
                if not link.active or runtime is None or not self.technologies._available(runtime):
                    status='disabled' if not link.active else 'down';reasons.append('session, node or paired channel unavailable')
                elif not fresh:
                    status='no_input';reasons.append('fresh housekeeping/tracking/TDC observation required')
                elif not geo.get('visible') or geo.get('channel_transmission',0)<=0:
                    status='down';reasons.append('no operational optical visibility / tracking / atmospheric path')
                else:
                    clicks=next((t.get('clicks',0) for t in metrics.get('tdc_summary',[]) if t.get('station_id')==endpoint),0)
                    status='up' if metrics.get('coincidences',0)>0 and metrics.get('available') else 'degraded' if clicks>0 else 'no_input'
                    if status!='up':reasons.append('no joint coincidence service attested in the latest block')
            out[link.link_id] = LinkLive(link.link_id, link.left_node_id, link.right_node_id, distance,
                status, fidelity=fidelity, provenance={"technology": link.technology,"status_reasons":reasons,
                    "session_status": state, "source": metrics.get('observation_origin',snapshot["origin"]),
                    "source_id": metrics.get('observation_source_id',snapshot["source_id"]), "physical_actuation": False,
                    "security_status": metrics.get("security_status", "entanglement estimates; not QKD key material"),
                    "protocol_metrics": metrics,
                     "channels": {"configuration":link.channels,
                                 "status":"received" if "last_observed_at_s" in metrics else "waiting",
                                 "source":metrics.get('observation_origin',snapshot["origin"]), "association_id":(link.channels or {}).get("association_id")}})
        return out

    def _cmd_configure_technology(self, payload):
        config = self.technologies.configure(payload["session_id"],
            **{key: payload[key] for key in ("parameters", "enabled", "scenario", "seed") if key in payload})
        self._refresh("technology configuration changed", replan=False)
        return {"status":"configured", "config":config, "physical_action_performed":False}

    def _cmd_save_communicator(self,payload):
        self.mission_receiver.guard_communicator_change(payload)
        topology=self.communicators.save(payload)
        self._refresh('communicator saved',force=True)
        return {'status':'saved','topology_version':topology.topology_version,'physical_action_performed':False}

    def _cmd_configure_communicator_connection(self,payload):
        topology=self.communicators.connection(payload,self.technologies.registry,self.technologies._validate_parameters)
        getattr(self.technologies,'_paused_device_sessions',set()).discard(payload['config']['session_id'])
        self._refresh('communicator connection configured',force=True)
        return {'status':'configured','topology_version':topology.topology_version,'physical_action_performed':False}

    def _update_satellites(self,topology):
        from ..quantum_protocols.orbits import satellite_state
        states={};clock=self.technologies.clock_s
        for node in topology.nodes:
            record=node.telemetry.get('communicator',{})
            if record.get('physical_type')!='satellite': continue
            try:state=satellite_state(record,topology,clock)
            except ValueError as exc:
                states[node.node_id]={'device_id':record['device_id'],'node_id':node.node_id,
                    'origin':record['integration']['data_origin'],'stations':{},'orbit_ecef_km':[],
                    'transmitting_links':[],'physical_tracking_validated':False,'error':str(exc)}
                continue
            state['clock_s']=clock
            state['acceleration_label']=f'{self.technologies.clock_rate:g} simulated seconds per network second'
            state['bridges']=[]
            state['isl_links']=[]
            state['housekeeping']={}
            state['housekeeping_valid']=False
            state['awaiting_received_telemetry']=True
            state['position_scope']='configured orbital prediction; awaiting validated engineering reception'
            state['global_demands']=[d for d in self._global_operations_state.get('demands',())
                if node.node_id in (d.get('route') or d.get('declared_path',())) or node.node_id in (d.get('a'),d.get('b'))]
            latest_housekeeping_at=-1.
            for session in self.technologies.sessions.values():
                if node.node_id not in session.config.path:continue
                metrics=session.metrics
                fresh=0<=clock-metrics.get('last_observed_at_s',-1e12)<=session.config.parameters.get('block_s',2.)+session.config.parameters.get('timeout_s',4.)
                try:source_origin=self.technologies._source_for(session).origin
                except ValueError:source_origin='unavailable'
                is_isl='satellite_isl' in session.config.technologies
                hk=(metrics.get('satellite_housekeeping',{}).get(node.node_id,{}) if is_isl else metrics.get('housekeeping',{}))
                association=[]
                for link in topology.links:
                    if link.link_id not in session.config.link_ids:continue
                    station=link.right_node_id if link.left_node_id==node.node_id else link.left_node_id
                    pair=link.channels or {}
                    association.append({'link_id':link.link_id,'station_id':station,'active':link.active,
                        'peer_id':station,'endpoint_kind':'satellite' if is_isl else 'ground_station',
                        'classical':pair.get('classical',{}),'quantum':pair.get('quantum',{}),
                        'association_id':pair.get('association_id')})
                controls_available=self.technologies._available(session)
                valid=fresh and bool(hk) and clock<=hk['valid_until_s']
                if is_isl:
                    valid=valid and len(metrics.get('satellite_housekeeping',{}))==2 and all(
                        clock<=peer_hk.get('valid_until_s',-1) for peer_hk in metrics['satellite_housekeeping'].values())
                bridge_status=('disabled' if not session.config.enabled else
                    'blocked' if not controls_available or session.status in ('unavailable','incompatible','error') else
                    'waiting' if not valid else 'blocked' if not metrics.get('available',False) else
                    'observed_pairs' if metrics.get('coincidences',0)>0 else 'sampling')
                state['bridges'].append({'session_id':session.config.session_id,
                    'endpoints':list(session.config.endpoints),'path':list(session.config.path),
                    'technologies':list(session.config.technologies),'protocol':metrics.get('protocol','BBM92'),
                    'status':bridge_status,'session_status':session.status,
                    'reason':('session, node or paired channel unavailable' if not controls_available else
                              session.reason if session.status in ('unavailable','incompatible','error') else
                              'fresh housekeeping/tracking/TDC observation required' if not valid else session.reason),
                    'origin':metrics.get('observation_origin',source_origin),'metrics':dict(metrics),
                    'connections':association,'last_observed_at_s':metrics.get('last_observed_at_s'),
                    'security_scope':'observed entanglement/QKD test; no certified or usable key material',
                    'resource_scope':metrics.get('resource_scope','observed BBM92 photons are detected, not stored relay resources')})
                if is_isl:
                    geometry=metrics.get('isl_geometry',{})
                    if not geometry:
                        try:
                            from ..quantum_protocols.isl import geometry_at
                            _,geometry=geometry_at(session.config.parameters,session.config.endpoints,clock)
                        except (ValueError,KeyError) as exc:
                            geometry={'visible':False,'block_reasons':[str(exc)]}
                    received=bool(metrics.get('isl_geometry')) and valid
                    reason=('session, node or paired channel unavailable' if not controls_available else
                        ', '.join(geometry.get('block_reasons',())) or session.reason)
                    for item in association:
                        peer=item['station_id']
                        state['isl_links'].append({'link_id':item['link_id'],'session_id':session.config.session_id,
                            'peer_id':peer,'endpoint_kind':'satellite','association_id':item['association_id'],
                            'classical':item['classical'],'quantum':item['quantum'],
                            'status':bridge_status,'reason':reason,
                            'origin':metrics.get('observation_origin','configured_prediction'),
                            'at_s':metrics.get('last_observed_at_s'),
                            'geometry':dict(geometry),'metrics':{**metrics,'isl_geometry':dict(geometry)},
                            'geometry_scope':'received validated engineering/TDC block' if received else 'configured prediction; awaiting fresh reception',
                            'photon_sender':session.config.endpoints[0],
                            'photon_receiver':session.config.endpoints[1]})
                        if (received and session.config.enabled and controls_available and
                            metrics.get('available') and geometry.get('visible') and metrics.get('emitted_pairs',0)>0 and
                            node.node_id==session.config.endpoints[0]):
                            state['transmitting_links'].append({'link_id':item['link_id'],'peer_id':peer,
                                'station_id':peer,'endpoint_kind':'satellite','source_origin':source_origin,
                                'observed_at_s':metrics['last_observed_at_s'],
                                'scope':'latest validated source emission/peer detection block; no stored relay resource'})
                if not session.config.enabled:continue
                if hk and hk.get('at_s',-1)>=latest_housekeeping_at:
                    # The map consumes the same received engineering data for
                    # every origin; simulation has no direct-state shortcut.
                    from ..quantum_protocols.orbits import ecef_geodetic
                    r=hk['ecef_km']
                    latest_housekeeping_at=hk['at_s'];state['housekeeping']=dict(hk)
                    state['housekeeping_valid']=clock<=hk['valid_until_s']
                    state['awaiting_received_telemetry']=not state['housekeeping_valid']
                    state['position_scope']=('received validated engineering position' if state['housekeeping_valid'] else
                        'last received position; engineering telemetry expired; not current tracking')
                    state.update(ecef_km=r,velocity_ecef_km_s=hk['velocity_ecef_km_s'],
                        **ecef_geodetic(r),
                        at_utc=hk.get('at_utc'),at_network_s=hk['at_s'],
                        origin=metrics.get('observation_origin',source_origin),
                        ephemeris_source=hk['source'],model='received ECEF observations',
                        observation_age_s=clock-hk['at_s'],physical_tracking_validated=False)
                    if not is_isl:
                        state['stations'].update({endpoint:g for endpoint,g in zip(session.config.endpoints,metrics.get('geometry',[]))})
                    # No extrapolated orbit or transmit overlay after received state expires.
                    if clock>hk['valid_until_s']:state['stations']={};fresh=False
                # Draw only source-reported emission blocks, never visibility alone.
                if is_isl:continue
                if not valid or session.status not in ('operational','unavailable') or metrics.get('emitted_pairs',0)<=0:continue
                for link in topology.links:
                    if link.link_id not in session.config.link_ids or node.node_id not in (link.left_node_id,link.right_node_id):continue
                    station=link.right_node_id if link.left_node_id==node.node_id else link.left_node_id
                    # Engineering updates from another session cannot validate
                    # this emission or supply its arm's optical geometry.
                    index=session.config.endpoints.index(station)
                    geometry=metrics.get('geometry',())
                    geo=geometry[index] if index<len(geometry) else {}
                    pair=link.channels
                    if geo.get('visible') and geo.get('channel_transmission',0)>0 and link.active and pair and pair['quantum']['enabled'] and pair['classical']['enabled'] and self.technologies._available(session):
                        state['transmitting_links'].append({'link_id':link.link_id,'station_id':station,
                            'source_origin':source_origin,'observed_at_s':metrics['last_observed_at_s'],
                            'scope':'latest received photon emission block; not an instantaneous physical measurement'})
            states[node.node_id]=state
        mission = self.mission_receiver.descriptor()
        if mission is not None:
            for satellite_id, state in states.items():
                if self.mission_receiver.store is not None and satellite_id not in self.mission_receiver.store.allowed_satellite_ids:
                    continue
                latest = mission.get('latest_by_satellite', {}).get(satellite_id)
                if latest is None and (mission.get('latest') or {}).get('satellite_id') == satellite_id:
                    latest = mission['latest']
                # Classical radio telemetry cannot supply optical tracking or QKD observations.
                state['satellite_mission_ingress'] = {
                    **{k: v for k, v in mission.items() if k not in ('latest', 'latest_by_satellite')},
                    'latest': latest}
        self._satellites=states

    def _cmd_provision_satellite_session(self, payload):
        return self.mission_receiver.provision(payload)

    def close_satellite_receivers(self):
        self.close_ogs()
        self.mission_receiver.close()

    def _cmd_configure_technology_source(self, payload):
        from ..quantum_protocols.sources import MeasurementFileSource, NoAcquisitionSource, SimulatedAcquisitionSource
        kind = payload.get("kind")
        if kind == 'mission_websocket':
            result = self.mission_receiver.configure(payload)
            self._refresh('classical satellite mission receiver configured', replan=False)
            return result
        if kind == 'stop_mission':
            self.mission_receiver.close()
            self._refresh('classical satellite mission receiver stopped', replan=False)
            return {'status': 'stopped', 'physical_action_performed': False}
        if kind=='ogs_websocket':
            from ..quantum_protocols.satellite_ingress import OGSStore, OGSWebSocketServer, SatelliteStreamSource
            session_id=payload.get('session_id');session=self.technologies.sessions.get(session_id)
            if session is None or 'satellite_doppler' not in session.config.technologies:
                raise ValueError('select a configured satellite session for OGS reception')
            if self._ogs_server is not None:raise ValueError('OGS listener already running; stop before changing configuration')
            if self.security is None and payload.get('origin','physical') != 'simulated':
                raise ValueError('physical OGS ingress requires the common authenticated security runtime')
            store=OGSStore(self.journal_path,source_id='ogs_gateway',origin=payload.get('origin','physical'))
            server=OGSWebSocketServer(store,lambda:self.technologies.clock_s,
                port=payload.get('port',8765),credential_ref=payload.get('credential_ref',''), security=self.security)
            source=SatelliteStreamSource(store,lambda:self.technologies.clock_s)
            self.technologies.registry.register_source(source,replace=True)
            self.technologies.external_sources[source.source_id]=source
            try:self.technologies.configure(session_id,parameters={'source_override':True,
                'acquisition_source':{'mode':'measurement_stream','source_id':source.source_id}})
            except Exception:
                server.close();self.technologies.external_sources.pop(source.source_id,None);raise
            self._ogs_server=server
            self._refresh('read-only OGS ingress configured',replan=False)
            return {'status':'listening','source_id':source.source_id,'origin':source.origin,
                    'url':f'ws://127.0.0.1:{server.port}/ogs/v2/','session_id':session_id,
                    'physical_adapter_validated':False,'physical_action_performed':False}
        if kind=='stop_ogs':
            self.close_ogs()
            self._refresh('OGS ingress stopped',replan=False)
            return {'status':'stopped','physical_action_performed':False}
        if kind == "simulated":
            source = SimulatedAcquisitionSource(payload.get("seed", self.seed))
        elif kind == "measurement_file":
            source = MeasurementFileSource(payload["path"], payload.get("source_id", "measurement_file"))
        elif kind == "none":
            source = NoAcquisitionSource()
        else:
            raise ValueError("implemented technology sources: simulated, measurement_file, none; no hardware executor")
        self.technologies.set_source(source)
        self._refresh("technology source changed", replan=False)
        return {"status":"configured", "source_id":source.source_id,"origin":source.origin,"physical_action_performed":False}

    def close_ogs(self):
        if self._ogs_server is not None:
            self._ogs_server.close();self._ogs_server=None
        self.technologies.external_sources.clear()
        self.technologies.registry.sources.pop('ogs_gateway',None)

    def _cmd_export_technology_events(self, payload):
        return self.technologies.export_events(payload["path"])

    # ------------------------------------------------------------------------------------
    # commands
    # ------------------------------------------------------------------------------------

    def _refresh(self, trigger: str, *, tick_events=(), decisions=(), force: bool = False,
                 replan: bool = True) -> tuple:
        """Re-read the topology, reallocate now and publish a state for the SAME tick, so the
        effect of a command is visible before the next network second."""
        at_utc = _utc_now()
        before, topology = self._topology, self._read_topology()
        events = list(tick_events) + self._topology_events(before, topology, self.tick, at_utc)
        self._topology = topology
        self.bank.sync(tuple(link for link in topology.links if link.technology is None))
        self.technologies.sync(topology)
        flags = self._legacy_flags(topology, self.calibration.nodes())
        links = {}
        for link in topology.links:
            live, flag = self._links.get(link.link_id), flags.get(link.link_id)
            if live is None:
                live = LinkLive(link.link_id, link.left_node_id, link.right_node_id, float(link.distance_km),
                                flag or "no_input", provenance={"input": "none", "status_reasons": [
                                    "added by a command; processed from the next network second"],
                                    "physical_action_performed": False})
            elif flag is not None and live.status != flag:
                # The fibre was just taken out of service: nothing measured is shown under an imposed status.
                live = LinkLive(live.link_id, link.left_node_id, link.right_node_id, float(link.distance_km), flag,
                                history=live.history, detector=DetectorLive(),
                                qkd=QkdLive(key_pool_bits=live.qkd.key_pool_bits,
                                            discarded_bits=live.qkd.discarded_bits),
                                provenance={"input": "withheld", "status_reasons": [f"imposed: {flag}"],
                                            "physical_action_performed": False})
            elif float(link.distance_km) != live.distance_km:
                live = replace(live, distance_km=float(link.distance_km))
            links[link.link_id] = live
        self._links = {**links, **self._technology_links(topology)}
        self._previous_status = {link_id: live.status for link_id, live in links.items()}
        routed, route_events, details = ([], [], {}) if not replan else \
            self._reallocate(topology, self.tick, at_utc, label=trigger, force=force)
        all_decisions = list(decisions) + list(routed)
        state = self._publish(topology, self.tick, at_utc, all_decisions, events + route_events, details)
        affected = sorted({decision.subject for decision in routed})
        return state, all_decisions, affected

    def _result(self, verb: str, status: str, state: LiveState, decisions=(), affected=(), **extra) -> dict:
        by_id = {demand.demand_id: demand for demand in state.demands}
        return {"verb": verb, "status": status, "physical_action_performed": False, "tick": state.tick,
                "topology_version": state.topology_version,
                "affected_demands": [by_id[demand_id].to_dict() for demand_id in affected if demand_id in by_id],
                "decisions": [decision.to_dict() for decision in decisions], **extra}

    def _complete(self, payload: Mapping[str, Any], defaults: Mapping[str, Any]) -> dict:
        """The payload with the two bookkeeping fields filled from the current topology when absent."""
        if not isinstance(payload, Mapping):
            raise ValueError("command payload must be an object")
        out = {**defaults, **dict(payload)}
        out.setdefault("expected_version", self._read_topology().topology_version)
        out.setdefault("topology_id", self.topology_id)
        if out["topology_id"] != self.topology_id:
            raise ValueError(f"this engine serves topology {self.topology_id!r}")
        return out

    def command(self, verb: str, payload: Mapping[str, Any] | None = None) -> dict:
        """Execute one operator command. Returns a result dict; raises ``ValueError`` when the
        command is invalid or refused (nothing is changed in that case)."""
        if verb not in VERBS:
            raise ValueError(f"unknown network command {verb!r}; expected one of {VERBS}")
        payload = {} if payload is None else payload
        with self._lock:
            handler = getattr(self, "_cmd_" + verb)
            return handler(payload)

    # -- topology verbs: the store's own methods, then an immediate reallocation ----------------

    def _topology_command(self, verb: str, payload, defaults=None) -> dict:
        hold = self.calibration.hold_reason()
        if hold is not None:
            raise ValueError(f"{verb} refused: {hold}")
        request = _CONTRACTS[verb].from_dict(self._complete(payload, defaults or {}))
        topology = getattr(self.store, verb)(request)
        subject = getattr(request, "link_id", None) or request.node_id
        trigger = {"set_link_active": lambda: f"fibre {subject} " + ("enabled" if request.active else "disabled"),
                   "set_node_isolation": lambda: f"node {subject} " + ("isolated" if request.isolated else "restored"),
                   "set_node_location": lambda: f"node {subject} location changed",
                   "add_network_node": lambda: f"node {subject} added"}[verb]()
        # A change that can only remove fibres re-plans the demands it invalidates; one that may
        # add fibres re-plans every demand, so a route can move back to a better path at once.
        widening = (verb == "set_link_active" and request.active) or \
                   (verb == "set_node_isolation" and not request.isolated) or verb == "set_node_location"
        state, decisions, affected = self.notify_topology_changed(trigger, replan_all=widening)
        return self._result(verb, "persisted", state, decisions, affected, topology=topology.to_dict(),
                            trigger=trigger)

    def notify_topology_changed(self, trigger: str = "topology changed", *, replan_all: bool = False) -> tuple:
        """The topology was changed in the store (by this engine or by anybody else): adopt
        it and reallocate now, without waiting for the next network second. Demands whose
        route left the effective graph are always planned again; ``replan_all`` plans every
        demand. Returns ``(state, decisions, affected_demand_ids)``."""
        with self._lock:
            return self._refresh(trigger, force=replan_all)

    def _cmd_set_node_isolation(self, payload):
        return self._topology_command("set_node_isolation", payload)

    def seed_demo_fpga(self):
        """Explicit demo bootstrap; source observables still use the ordinary pipeline."""
        with self._lock:
            count = self.fpga.seed_demo(self._topology)
            if count:
                self._refresh("simulated FPGA devices registered", replan=False)
            return count

    def _fpga_command(self, verb, payload):
        if not isinstance(payload, Mapping):
            raise ValueError("FPGA command payload must be an object")
        if verb == "inject_fpga_fault" and self.source.kind != "simulated":
            raise ValueError("FPGA fault injection requires the active simulated input source")
        method = {"register_fpga_device": self.fpga.register, "configure_fpga_device": self.fpga.configure,
                  "set_fpga_connection": self.fpga.connection, "inject_fpga_fault": self.fpga.fault}[verb]
        receipt = method(dict(payload), self._topology) if verb != "inject_fpga_fault" else method(dict(payload))
        # A changed device needs a new handshake and fresh receiver observations.
        # Invalidate its previous instantaneous metrics in the command snapshot,
        # without deleting historical samples or previously accumulated key pools.
        if verb != "inject_fpga_fault":
            device_id = receipt["device_id"]
            for device in self.fpga.snapshot()["devices"]:
                if device["configuration"]["device_id"] != device_id:
                    continue
                for link_id in device["state"]["bound_links"]:
                    live = self._links.get(link_id)
                    if live is not None:
                        self._links[link_id] = LinkLive(
                            live.link_id, live.a, live.b, live.distance_km, "no_input", history=live.history,
                            qkd=QkdLive(key_pool_bits=live.qkd.key_pool_bits, discarded_bits=live.qkd.discarded_bits),
                            provenance={"input": "withheld", "physical_action_performed": False,
                                        "status_reasons": ["FPGA configuration changed; awaiting fresh acquisition"]})
        state, decisions, affected = self._refresh(f"FPGA {verb}")
        return self._result(verb, "persisted", state, decisions, affected,
                            receipt_sha256=receipt["record_sha256"], fpga_receipt=receipt,
                            fpga_devices=state.technologies['fpga_devices'])

    def _cmd_register_fpga_device(self, payload):
        return self._fpga_command("register_fpga_device", payload)

    def _cmd_configure_fpga_device(self, payload):
        return self._fpga_command("configure_fpga_device", payload)

    def _cmd_set_fpga_connection(self, payload):
        return self._fpga_command("set_fpga_connection", payload)

    def _cmd_inject_fpga_fault(self, payload):
        return self._fpga_command("inject_fpga_fault", payload)

    def _cmd_set_link_active(self, payload):
        return self._topology_command("set_link_active", payload)

    def _cmd_set_node_location(self, payload):
        return self._topology_command("set_node_location", payload, {"latitude": None, "longitude": None, "place": ""})

    def _cmd_add_network_node(self, payload):
        return self._topology_command("add_network_node", payload,
                                      {"latitude": None, "longitude": None, "place": ""})

    # -- recalibration ---------------------------------------------------------------------------

    def _cmd_recalibrate_node(self, payload):
        """Start the procedure; it advances one phase per ``step()``. See ``run_calibration``
        for the blocking form."""
        request = RecalibrateNode.from_dict(self._complete(payload, {}))
        at_utc = _utc_now()
        self._topology = self._read_topology()
        outcome = self.calibration.start(request, self._topology, tick=self.tick, at_utc=at_utc)
        self.technologies.reset_node(request.node_id)
        trigger = f"node {request.node_id} calibrating"
        state, decisions, affected = self._refresh(trigger, tick_events=outcome.events, decisions=outcome.decisions)
        node = next(item for item in state.nodes if item.node_id == request.node_id)
        return self._result("recalibrate_node", "started", state, decisions, affected,
                            calibration=node.calibration.to_dict(), trigger=trigger)

    def run_calibration(self, payload: Mapping[str, Any], *, max_ticks: int = MAX_CALIBRATION_TICKS,
                        tick_interval_s: float = 0.0) -> dict:
        """Blocking recalibration: start it and step the engine until it has ended.

        The lock is taken per tick, not for the whole procedure, so ``live_state()`` on
        another thread shows every phase. Network time advances one second per tick taken
        here (it is counted, not timed). ``tick_interval_s`` is the wall-clock pause between
        those ticks: a caller that normally steps the engine once per period passes that
        period, so the network keeps its cadence while a node is recalibrated (and each
        phase stays published for one period). Returns the result with ``status``
        ``persisted`` and the persisted topology, or raises ``ValueError`` with the reason
        of the failure.
        """
        if isinstance(tick_interval_s, bool) or not isinstance(tick_interval_s, (int, float)) \
                or not 0.0 <= tick_interval_s <= 10.0:
            raise ValueError("tick_interval_s must be a number of seconds in [0, 10]")
        started = self.command("recalibrate_node", payload)
        node_id = payload["node_id"]
        calibration_id = started["calibration"]["calibration_id"]
        for _ in range(int(max_ticks)):
            if tick_interval_s:
                time.sleep(float(tick_interval_s))
            state = self.step()
            node = next((item for item in state.nodes if item.node_id == node_id), None)
            live = None if node is None else node.calibration
            if live is None or live.calibration_id != calibration_id or live.status == "running":
                continue
            if live.status == "failed":
                raise ValueError(self.calibration.last_error.get(node_id, "recalibration failed"))
            with self._lock:
                topology = self._topology
            decisions = [decision for decision in state.decisions
                         if decision.kind == "calibration" and decision.subject == node_id][-8:]
            return self._result("recalibrate_node", "persisted", state, decisions, (),
                                topology=topology.to_dict(), calibration=live.to_dict())
        raise ValueError(f"recalibration of {node_id} did not end within {max_ticks} network seconds")

    # -- demands and pairs -------------------------------------------------------------------------

    def _memory_generations(self, demand) -> dict:
        return {node_id: node_memory_generation(self._topology.node(node_id))
                for node_id in (demand.a, demand.b)}

    def _reconcile_pairs(self, topology, tick, at_utc) -> list:
        """Retain historical results, but invalidate stock whose endpoint memory was reset.

        Reset counters are persisted by the topology transaction, so this also detects
        reset/restore between polls and resets made while the engine was stopped.
        Older records without those counters are preserved as non-consumable history.
        """
        pairs, nodes = self.demands.pairs(), {node.node_id: node for node in topology.nodes}
        events = []
        for demand in self.demands.list():
            pair = pairs.get(demand.demand_id)
            if pair is None or pair.get("status") != "delivered":
                continue
            generations = pair.get("memory_generations")
            unknown = not isinstance(generations, Mapping) or set(generations) != {demand.a, demand.b}
            invalid = [node_id for node_id in (demand.a, demand.b)
                       if unknown or node_id not in nodes or _control(nodes[node_id]).get("isolated") is True
                       or type(generations.get(node_id)) is not int
                       or generations[node_id] != node_memory_generation(nodes[node_id])]
            if not invalid:
                continue
            reason = ("Stored pair cannot be tied to the current node memory; request a new pair." if unknown else
                      f"Stored pair lost after memory reset or removal of node {', '.join(invalid)}.")
            updated = dict(pair, status="invalidated", invalidated_nodes=invalid, invalidation_reason=reason,
                           invalidated_at_tick=tick, invalidated_at_utc=at_utc)
            self.demands.set_pair(demand.demand_id, updated)
            events.append(self._event(tick, at_utc, "pair_invalidated", demand.demand_id, reason, "warning"))
        return events

    def _refresh_pair_state(self) -> None:
        changed = self._read_topology().topology_version != self._topology.topology_version
        self._refresh("pair memory checked", replan=changed, force=changed)

    def ensure_demands(self, demands) -> int:
        """Define ``demands`` when the workspace has none (first start of a demo workspace)."""
        with self._lock:
            if self.demands.list():
                return 0
            for demand in demands:
                self.demands.define(demand, self._topology)
            self._refresh("demand defined")
            return len(self.demands.list())

    def _cmd_define_pair(self, payload):
        if not isinstance(payload, Mapping) or not {"demand_id", "a", "b"} <= set(payload) \
                or set(payload) - {"demand_id", "a", "b", "min_fidelity"}:
            raise ValueError("define_pair takes demand_id, a, b and optionally min_fidelity")
        demand = rt.Demand(payload["demand_id"], payload["a"], payload["b"],
                           payload.get("min_fidelity", rt.DEFAULT_MIN_FIDELITY))
        self.demands.define(demand, self._topology)
        self._plans.pop(demand.demand_id, None)
        self._meta.pop(demand.demand_id, None)
        state, decisions, affected = self._refresh("demand defined")
        return self._result("define_pair", "persisted", state, decisions, [demand.demand_id],
                            demand=demand.to_dict())

    def _demand(self, payload) -> rt.Demand:
        if not isinstance(payload, Mapping) or set(payload) != {"demand_id"}:
            raise ValueError("the command takes exactly one field: demand_id")
        for demand in self.demands.list():
            if demand.demand_id == payload["demand_id"]:
                return demand
        raise ValueError(f"unknown demand {payload['demand_id']!r}")

    def _cmd_remove_pair(self, payload):
        demand = self._demand(payload)
        self.demands.remove(demand.demand_id)
        self._plans.pop(demand.demand_id, None)
        self._meta.pop(demand.demand_id, None)
        state, decisions, _ = self._refresh("demand removed", replan=False)
        return self._result("remove_pair", "persisted", state, decisions, (), demand=demand.to_dict())

    def _pair_episode(self, demand, purpose: str) -> tuple:
        plan = self._plans.get(demand.demand_id)
        if plan is None or plan.status != "routed":
            return plan, None
        self._pair_requests += 1
        seed = self._seed(purpose, demand.demand_id, self.tick, self._pair_requests)
        graph = rt.effective_graph(self._topology, self._links, self.calibration.nodes())
        result, config, request, controller_id = self.router.episode(demand, graph, plan.route, seed=seed)
        return plan, (result, config, request, controller_id, seed)

    def _cmd_request_pair(self, payload):
        """ONE real episode of the author's environment on the demand's current route."""
        demand = self._demand(payload)
        self._refresh_pair_state()
        generations = self._memory_generations(demand)
        at_utc = _utc_now()
        plan, ran = self._pair_episode(demand, "pair")
        if ran is None:
            reason = "not planned yet" if plan is None else plan.reason
            event = self._event(self.tick, at_utc, "pair_refused", demand.demand_id,
                                f"request_pair {demand.demand_id}: {'no plan' if plan is None else plan.status} â€” "
                                f"{reason}", "warning")
            state, _, _ = self._refresh("pair requested", tick_events=[event], replan=False)
            return self._result("request_pair", "no_route" if plan is None else plan.status, state, (),
                                [demand.demand_id], reason=reason, pair=None)
        result, config, request, controller_id, seed = ran
        pair = {"status": "delivered" if result.delivered else "failed", "fidelity": result.fidelity,
                "min_fidelity": demand.min_fidelity, "route": list(plan.route), "route_links": list(plan.route_links),
                "hops": result.hops, "swaps_at": list(result.swaps_at), "purifications_in_episode": result.purifications,
                "rounds": 0, "copies_consumed": 1 if result.delivered else 0,
                "memory_wait_ms": None if result.memory_wait_s is None else result.memory_wait_s * 1e3,
                "latency_ms": result.latency_s * 1e3, "steps": result.steps, "attempts": result.attempts,
                "deadline_ms": request.deadline_s * 1e3, "outcome": result.outcome,
                "state": None if result.state is None else list(result.state), "controller_id": controller_id,
                "in_contract": plan.in_contract, "requested_at_tick": self.tick, "requested_at_utc": at_utc,
                "seed": seed, "physical_action_performed": False, "memory_generations": generations}
        self.demands.set_pair(demand.demand_id, pair, memory_generations=generations)
        if result.delivered:
            message = (f"{demand.demand_id} {demand.a}<->{demand.b}: pair delivered over {_arrow(plan.route)}, "
                       f"fidelity {result.fidelity:.3f}, swaps at {', '.join(result.swaps_at) or 'none'}, memory "
                       f"wait {pair['memory_wait_ms']:.2f} ms, {result.steps} steps")
        else:
            message = (f"{demand.demand_id} {demand.a}<->{demand.b}: episode ended without a pair "
                       f"({result.outcome} after {result.steps} steps, {pair['latency_ms']:.1f} ms)")
        event = self._event(self.tick, at_utc, "pair_delivered" if result.delivered else "pair_failed",
                            demand.demand_id, message, "info" if result.delivered else "warning")
        state, _, _ = self._refresh("pair requested", tick_events=[event], replan=False)
        return self._result("request_pair", pair["status"], state, (), [demand.demand_id], pair=pair)

    def _cmd_purify_pair(self, payload):
        """One purification round: a second real episode supplies the second copy, then the
        author's closed form (``bell_diagonal``, the plant's protocol and gate noise) is applied."""
        demand = self._demand(payload)
        self._refresh_pair_state()
        pair = self.demands.pair(demand.demand_id)
        if pair is None or pair.get("status") != "delivered" or pair.get("state") is None:
            raise ValueError(f"demand {demand.demand_id} holds no delivered pair to purify; request one first")
        at_utc = _utc_now()
        plan, ran = self._pair_episode(demand, "purify")
        if ran is None:
            raise ValueError(f"demand {demand.demand_id} has no usable route for the second copy: "
                             f"{'not planned' if plan is None else plan.reason}")
        result, config, _, _, seed = ran
        if not result.delivered:
            event = self._event(self.tick, at_utc, "purification_refused", demand.demand_id,
                                f"{demand.demand_id}: no second copy was delivered ({result.outcome}); the stored "
                                "pair is unchanged", "warning")
            state, _, _ = self._refresh("pair purification", tick_events=[event], replan=False)
            return self._result("purify_pair", "no_second_copy", state, (), [demand.demand_id], pair=pair,
                                reason=f"the second episode ended without a pair ({result.outcome})")
        outcome = rt.purify(pair["state"], result.state, config, np.random.default_rng(seed))
        before = pair["fidelity"]
        updated = dict(pair, rounds=int(pair.get("rounds", 0)) + 1,
                       copies_consumed=int(pair.get("copies_consumed", 1)) + 1,
                       last_purification={"protocol": outcome["protocol"],
                                          "success_probability": outcome["success_probability"],
                                          "succeeded": outcome["succeeded"], "fidelity_before": before,
                                          "second_copy_fidelity": result.fidelity, "at_tick": self.tick, "seed": seed})
        if outcome["succeeded"]:
            updated.update(fidelity=outcome["fidelity"], state=list(outcome["state"]))
            message = (f"{demand.demand_id}: {outcome['protocol']} round {updated['rounds']} succeeded "
                       f"(p = {outcome['success_probability']:.2f}); fidelity {before:.3f} -> "
                       f"{outcome['fidelity']:.3f}")
        else:
            updated.update(status="purification_failed", fidelity=None, state=None)
            message = (f"{demand.demand_id}: {outcome['protocol']} round {updated['rounds']} failed "
                       f"(p = {outcome['success_probability']:.2f}); both copies are lost")
        self.demands.set_pair(demand.demand_id, updated, memory_generations=pair["memory_generations"])
        event = self._event(self.tick, at_utc, "pair_purified" if outcome["succeeded"] else "purification_failed",
                            demand.demand_id, message, "info" if outcome["succeeded"] else "warning")
        state, _, _ = self._refresh("pair purification", tick_events=[event], replan=False)
        return self._result("purify_pair", updated["status"], state, (), [demand.demand_id], pair=updated)

    def _cmd_consume_pair(self, payload):
        demand = self._demand(payload)
        self._refresh_pair_state()
        pair = self.demands.pair(demand.demand_id)
        if pair is None or pair.get("status") != "delivered":
            raise ValueError(f"demand {demand.demand_id} holds no delivered pair to consume")
        at_utc = _utc_now()
        updated = dict(pair, status="consumed", consumed_at_tick=self.tick, consumed_at_utc=at_utc)
        if any(node_id in self.calibration.nodes() for node_id in (demand.a, demand.b)):
            raise ValueError("endpoint recalibration is running; wait before consuming the stored pair")
        self.demands.set_pair(demand.demand_id, updated, memory_generations=pair["memory_generations"])
        event = self._event(self.tick, at_utc, "pair_consumed", demand.demand_id,
                            f"{demand.demand_id}: pair (fidelity {pair['fidelity']:.3f}) consumed by the application")
        state, _, _ = self._refresh("pair consumed", tick_events=[event], replan=False)
        return self._result("consume_pair", "consumed", state, (), [demand.demand_id], pair=updated)

    # -- input and models ----------------------------------------------------------------------------

    def _cmd_set_ml_control(self, payload):
        if not isinstance(payload, Mapping) or set(payload) != {"enabled", "expected_revision", "confirmed"}:
            raise ValueError("set_ml_control takes enabled, expected_revision and confirmed")
        enabled, revision = payload["enabled"], payload["expected_revision"]
        if type(enabled) is not bool or type(revision) is not int or revision < 0 or payload["confirmed"] is not True:
            raise ValueError("boolean enabled, non-negative revision and explicit confirmation required")
        if self.source.info().kind != "simulated":
            raise ValueError("ML control switching is available only with simulated network input")
        if self.calibration.hold_reason() is not None:
            raise ValueError("wait for node recalibration before changing ML control")
        if revision != self._ml_revision:
            raise ValueError("ML control changed since confirmation; refresh and confirm again")
        if enabled == self.registry.control_enabled:
            return self._result("set_ml_control", "unchanged", self._state)
        self._ml_revision = write_mode(self.journal_path, self.topology_id, enabled, revision)
        self.registry.control_enabled = enabled
        for link_id in self.bank.link_ids:
            processor = self.bank.processor(link_id)
            processor.rebind_controls()
            if link_id in self._links:
                self._links[link_id] = replace(self._links[link_id], controllers=processor._controllers())
        self.router.publish_status()
        self.allocator.publish_status()
        at_utc = _utc_now()
        message = ("ML control permitted; verified weights and each model's contract still required" if enabled else
                   "ML control disabled; reference controllers acting; detector and safety rules preserved")
        learned_active = any(status.state == "active" and status.role in CONTROL_ROLES for status in self.registry.status())
        receipt = Decision.create(tick=self.tick, at_utc=at_utc, kind="ml_control", controller_id="operator",
            model_state=("active" if learned_active else "fallback") if enabled else "disabled", subject=self.topology_id, summary=message,
            inputs_sha256=canonical_sha256(dict(payload)), outcome="enabled" if enabled else "disabled",
            in_contract=True, fallback_used=not (enabled and learned_active))
        event = self._event(self.tick, at_utc, "ml_control_changed", self.topology_id, message, source="operator")
        state, decisions, affected = self._refresh(message, tick_events=[event], decisions=[receipt], force=True)
        return self._result("set_ml_control", "persisted", state, decisions, affected,
                            enabled=enabled, revision=self._ml_revision, receipt=receipt.to_dict())

    def _cmd_inject_input_event(self, payload):
        """Disturb the INPUT of one fibre (simulated source only); the processing then reacts."""
        allowed = {"kind", "link_id", "strength", "duration_s", "profile"}
        if not isinstance(payload, Mapping) or not {"kind", "link_id"} <= set(payload) or set(payload) - allowed:
            raise ValueError("inject_input_event takes kind, link_id and optionally strength, duration_s, profile")
        event = InputEvent(**dict(payload))
        if event.link_id not in {link.link_id for link in self._topology.links}:
            raise ValueError(f"unknown fibre {event.link_id!r}")
        self.source.inject(event)
        at_utc = _utc_now()
        note = self._event(self.tick, at_utc, "input_event_injected", event.link_id,
                           f"operator injected input event {event.kind} on {event.link_id} (strength "
                           f"{event.strength:g}, {event.duration_s:g} s, {event.profile}); it starts at the next "
                           "network second", "info", "operator")
        state, _, _ = self._refresh("input event injected", tick_events=[note], replan=False)
        return self._result("inject_input_event", "accepted", state, (), (),
                            event={"kind": event.kind, "link_id": event.link_id, "strength": event.strength,
                                   "duration_s": event.duration_s, "profile": event.profile})

    def _cmd_set_model_archive(self, payload):
        if not isinstance(payload, Mapping) or set(payload) != {"kind", "path"}:
            raise ValueError("set_model_archive takes kind (qnet | qkd) and path (or null to remove)")
        archive = self.registry.set_archive(payload["kind"], payload["path"])
        self.router.publish_status()
        self.allocator.publish_status()
        state, decisions, affected = self._refresh(f"model archive {payload['kind']} {archive.state}", force=True)
        return self._result("set_model_archive", archive.state, state, decisions, affected,
                            archive=archive.to_dict(),
                            note="routing and allocation use the archive from this plan on; the per-fibre "
                                 "controllers adopt it when their session is next commissioned")

    def set_source(self, source) -> None:
        """Swap the input source. The per-fibre processing starts afresh (new sessions)."""
        with self._lock:
            self.source = source
            from ..quantum_protocols.sources import NoAcquisitionSource, SimulatedAcquisitionSource
            self.technologies.set_source(SimulatedAcquisitionSource(self.seed) if source.kind == "simulated" else NoAcquisitionSource())
            self.channel_intake = ChannelIntake()
            self.fpga.reset_acquisition()
            self.bank = LinkBank(self.registry, policy=self._link_policy, seed=self.seed)
            self.calibration.bank = self.bank
            self.bank.sync(tuple(link for link in self._topology.links if link.technology is None))
            self._temperatures = {}
            self._links = {}
            at_utc = _utc_now()
            frame = IdleInputSource().frame(self.tick, self._topology)
            frame = self.channel_intake.ingest(frame, self._topology)
            self._links = self.channel_intake.decorate(self.bank.step(frame,
                                              self._legacy_flags(self._topology, self.calibration.nodes()),
                                              at_utc=at_utc).links)
            self._previous_status = {link_id: live.status for link_id, live in self._links.items()}

    def _cmd_configure_network_input(self, payload):
        allowed = {"kind", "seed", "scenario", "path"}
        if not isinstance(payload, Mapping) or "kind" not in payload or set(payload) - allowed:
            raise ValueError("configure_network_input takes kind and optionally seed, scenario, path")
        kind = payload["kind"]
        if self.calibration.running:
            raise ValueError("the input source cannot be changed while a node is being recalibrated")
        if kind == "simulated":
            seed = payload.get("seed")
            source = SimulatedInputSource(self.seed if seed is None else seed, payload.get("scenario") or "demo")
        elif kind == "none":
            if any(payload.get(name) is not None for name in ("seed", "scenario", "path")):
                raise ValueError("the idle source takes no seed, scenario or path")
            source = IdleInputSource()
        elif kind == "measured":
            if not isinstance(payload.get("path"), str) or not payload["path"].strip() \
                    or payload.get("seed") is not None or payload.get("scenario") is not None:
                raise ValueError("measured acquisition replay requires only a local capture path")
            source = AcquisitionFileSource(payload["path"])
        else:
            raise ValueError("input kind must be simulated, none or measured")
        self.set_source(source)
        at_utc = _utc_now()
        note = self._event(self.tick, at_utc, "input_source_changed", self.topology_id,
                           f"input source: {source.label}; per-fibre sessions start afresh")
        state, decisions, affected = self._refresh("input source changed", tick_events=[note], force=True)
        return self._result("configure_network_input", "configured", state, decisions, affected,
                            source=state.source.to_dict())


__all__ = ["DecisionJournal", "MAX_CALIBRATION_TICKS", "NetworkEngine", "TOPOLOGY_VERBS", "VERBS"]
