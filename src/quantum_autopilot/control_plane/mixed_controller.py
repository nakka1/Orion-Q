"""Persistent headless routing/reservation using source-neutral observations.

Reservations allocate control slots, never fabricate pairs, keys or hardware
operations. Adapter provenance is persisted for audit outside the route policy.
"""
from __future__ import annotations

from contextlib import contextmanager, closing
import json
import os
from pathlib import Path
import time
import uuid
from threading import RLock

import networkx as nx

from .mixed_contracts import canonical, digest, exact, identifier, integer, validate_configuration, validate_record
from .sqlite_transaction import connect


def route_policy(configuration, states, *, source, target, operation, now_ns):
    """Pure policy: no driver name, data origin, credentials or desktop state."""
    if not isinstance(operation, str) or operation not in {"classical_control", "correlation", "entanglement"}:
        raise ValueError("unsupported reservation operation")
    nodes = {node["node_id"]: node for node in configuration["nodes"]}
    if source not in nodes or target not in nodes or source == target:
        raise ValueError("demand endpoints invalid")
    cutoff = now_ns - configuration["freshness_s"] * 10**9
    blocked_nodes = {}
    for node_id, node in nodes.items():
        state = states[node_id]
        reasons = []
        if state["status"] != "ready":
            reasons.append(state["status"])
        if state["received_ns"] < cutoff:
            reasons.append("stale_observations")
        if state["available"] <= 0:
            reasons.append("no_observed_resources")
        if state.get("resources_received_ns", 0) < cutoff:
            reasons.append("stale_resource_observation")
        if operation == "entanglement" and "entanglement_memory" not in node["capabilities"]:
            reasons.append("entanglement_memory_missing")
        if operation == "entanglement" and node_id not in {source, target} and "bsm" not in node["capabilities"]:
            reasons.append("intermediate_bsm_missing")
        if operation == "correlation" and node_id in {source, target}:
            if not {"time_tagging", "correlation"} <= set(node["capabilities"]):
                reasons.append("correlation_capability_missing")
            if nodes[source]["clock_domain"] != nodes[target]["clock_domain"]:
                reasons.append("unaligned_clock_domains")
        if reasons:
            blocked_nodes[node_id] = reasons
    graph = nx.Graph()
    graph.add_nodes_from(nodes)
    blocked_links = {}
    for link in configuration["links"]:
        reasons = []
        for node_id in (link["a"], link["b"]):
            reasons.extend(blocked_nodes.get(node_id, []))
            observation = states[node_id]["links"].get(link["link_id"], {})
            if states[node_id].get("links_received_ns", {}).get(link["link_id"], 0) < cutoff:
                reasons.append("stale_channel_observation")
            if observation.get("classical_state") != "up":
                reasons.append("classical_channel_not_observed_up")
            if operation == "entanglement" and observation.get("quantum_state") != "up":
                reasons.append("quantum_channel_not_observed_up")
        if reasons:
            blocked_links[link["link_id"]] = sorted(set(reasons))
        elif not graph.has_edge(link["a"], link["b"]):
            graph.add_edge(link["a"], link["b"], link_id=link["link_id"])
    if blocked_nodes.get(source) or blocked_nodes.get(target) or not nx.has_path(graph, source, target):
        return {"status": "blocked", "route": [], "links": [],
                "blocked_nodes": blocked_nodes, "blocked_links": blocked_links}
    # Stable sorted graph traversal makes equivalent input independent of source/order.
    graph = nx.Graph(sorted(graph.edges(data=True), key=lambda row: (row[0], row[1], row[2]["link_id"])))
    route = nx.shortest_path(graph, source, target)
    if operation == "entanglement" and any("bsm" not in nodes[n]["capabilities"] for n in route[1:-1]):
        return {"status": "blocked", "route": [], "links": [], "blocked_nodes": {},
                "blocked_links": {}, "reason": "intermediate_bsm_missing"}
    return {"status": "planned", "route": route,
            "links": [graph[a][b]["link_id"] for a, b in zip(route, route[1:])],
            "blocked_nodes": blocked_nodes, "blocked_links": blocked_links}


class MixedController:
    """Single service authority with SQLite transactions and conservative recovery."""
    def __init__(self, path, *, resource_authority=None):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.owner = uuid.uuid4().hex
        self.resource_authority = resource_authority
        self._resource_leases = {}
        self._lock = RLock()
        try:
            self._initialize()
        except BaseException:
            for lease in self._resource_leases.values():
                lease.revoke()
            self._resource_leases = {}
            raise

    def _initialize(self):
        with closing(connect(self.path)) as db, db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS mixed_authority (id INTEGER PRIMARY KEY, pid INTEGER, owner TEXT);
                CREATE TABLE IF NOT EXISTS mixed_config (id INTEGER PRIMARY KEY, revision INTEGER, body TEXT);
                CREATE TABLE IF NOT EXISTS mixed_nodes (node_id TEXT PRIMARY KEY, body TEXT);
                CREATE TABLE IF NOT EXISTS mixed_events (event_id TEXT PRIMARY KEY, node_id TEXT,
                    session_id TEXT, sequence INTEGER, body TEXT, provenance TEXT, received_ns INTEGER,
                    UNIQUE(node_id,session_id,sequence));
                CREATE TABLE IF NOT EXISTS mixed_reservations (request_id TEXT PRIMARY KEY, body TEXT);
                CREATE TABLE IF NOT EXISTS mixed_audit_outbox (
                    request_id TEXT, receipt_sha256 TEXT, operation TEXT, state TEXT,
                    PRIMARY KEY(request_id,receipt_sha256,operation));
            """)
            db.execute("BEGIN IMMEDIATE")
            previous = db.execute("SELECT pid FROM mixed_authority WHERE id=1").fetchone()
            if previous is not None:
                from quantum_autopilot.lab.capture import _process_alive
                if _process_alive(previous["pid"]):
                    raise RuntimeError("mixed controller already has a live process authority")
            db.execute("INSERT OR REPLACE INTO mixed_authority VALUES (1,?,?)", (os.getpid(), self.owner))
            old_config = db.execute("SELECT body FROM mixed_config WHERE id=1").fetchone()
            if old_config:
                self._resource_leases = self._claim_resources(json.loads(old_config["body"]))
            self._invalidate(db, "invalidated_restart")
            for row in db.execute("SELECT node_id,body FROM mixed_nodes").fetchall():
                state = json.loads(row["body"])
                state["status"] = "reverification_required"
                state["links"] = {}
                state["available"] = 0
                db.execute("UPDATE mixed_nodes SET body=? WHERE node_id=?", (canonical(state), row["node_id"]))

    @contextmanager
    def _db(self, admit=None, *, validate_resources=True):
        with self._lock, closing(connect(self.path)) as db, db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT owner FROM mixed_authority WHERE id=1").fetchone()
            if row is None or row["owner"] != self.owner:
                raise RuntimeError("mixed controller authority is not active")
            if admit is not None:
                admit()
            for lease in self._resource_leases.values() if validate_resources else ():
                lease.require(owner=self.owner, session_id=self.owner)
                lease.renew()
            yield db

    def _claim_resources(self, configuration):
        """Conservative controller-level fencing, independent of its state DB.

        Instrument acquisition retains its separate authenticated driver permit.
        This lock prevents independent controllers from treating the same device
        capacity as separate pools. It never manufactures observed resources.
        """
        from quantum_autopilot.security.equipment import PhysicalIdentity
        claimed = {}
        try:
            for node in configuration["nodes"]:
                identity = node.get("resource_identity")
                if identity is None:
                    continue  # Unbound nodes are isolated simulation resources.
                if self.resource_authority is None:
                    raise ValueError("bound resources require an explicitly shared resource authority")
                physical = PhysicalIdentity(**identity)
                # A separate namespace fences controllers, without taking the
                # acquisition lease away from the same service's adapter.
                lock_identity = PhysicalIdentity(physical.serial, physical.endpoint,
                                                 "controller-resource:" + physical.namespace)
                old = self._resource_leases.get(node["node_id"])
                if old is not None and old.identity == lock_identity:
                    old.require(owner=self.owner, session_id=self.owner)
                    claimed[node["node_id"]] = old
                else:
                    claimed[node["node_id"]] = self.resource_authority.acquire(
                        lock_identity, owner=self.owner, session_id=self.owner, ttl_s=3600)
            return claimed
        except BaseException:
            for node_id, lease in claimed.items():
                if self._resource_leases.get(node_id) is not lease:
                    lease.revoke()
            raise

    @staticmethod
    def _pending_audit(db, result, operation):
        db.execute("INSERT OR IGNORE INTO mixed_audit_outbox VALUES (?,?,?,'pending')",
                   (result["request_id"], result["receipt_sha256"], operation))

    def audit_completed(self, request_id, receipt_sha256, operation):
        """Only the trusted service calls this after authenticated audit success."""
        with self._db() as db:
            db.execute("UPDATE mixed_audit_outbox SET state='completed' "
                       "WHERE request_id=? AND receipt_sha256=? AND operation=?",
                       (request_id, receipt_sha256, operation))

    def pending_audit_for_request(self, request_id):
        """Trusted recovery projection, preserving the original committed hash."""
        identifier(request_id)
        with self._db() as db:
            return [dict(row) for row in db.execute(
                "SELECT request_id,receipt_sha256,operation FROM mixed_audit_outbox "
                "WHERE request_id=? AND state='pending'", (request_id,))]

    @staticmethod
    def _receipt_status(item, status):
        item["status"] = status
        item.pop("receipt_sha256", None)
        item["receipt_sha256"] = digest(item)

    @staticmethod
    def _invalidate(db, reason, nodes=None):
        for row in db.execute("SELECT request_id,body FROM mixed_reservations").fetchall():
            item = json.loads(row["body"])
            if item["status"] == "reserved" and (nodes is None or set(item["route"]) & set(nodes)):
                MixedController._receipt_status(item, reason)
                db.execute("UPDATE mixed_reservations SET body=? WHERE request_id=?", (canonical(item), row["request_id"]))

    @staticmethod
    def _configuration(db):
        row = db.execute("SELECT body FROM mixed_config WHERE id=1").fetchone()
        if row is None:
            raise ValueError("mixed controller is not configured")
        return json.loads(row["body"])

    @staticmethod
    def _states(db):
        return {row["node_id"]: json.loads(row["body"]) for row in db.execute("SELECT node_id,body FROM mixed_nodes")}

    @staticmethod
    def _expire(db, now_ns):
        for row in db.execute("SELECT request_id,body FROM mixed_reservations").fetchall():
            item = json.loads(row["body"])
            if item["status"] == "reserved" and item["expires_ns"] <= now_ns:
                MixedController._receipt_status(item, "expired")
                db.execute("UPDATE mixed_reservations SET body=? WHERE request_id=?", (canonical(item), row["request_id"]))
        config = db.execute("SELECT body FROM mixed_config WHERE id=1").fetchone()
        if config is None:
            return
        configuration = json.loads(config["body"])
        cutoff = now_ns - configuration["freshness_s"] * 10**9
        states = MixedController._states(db)
        stale = {n for n, s in states.items() if s["received_ns"] < cutoff
                 or s.get("resources_received_ns", 0) < cutoff}
        MixedController._invalidate(db, "invalidated_stale_resources", stale)
        links = {link["link_id"]: link for link in configuration["links"]}
        for row in db.execute("SELECT request_id,body FROM mixed_reservations").fetchall():
            item = json.loads(row["body"])
            if item["status"] == "reserved" and any(
                    states[node_id].get("links_received_ns", {}).get(link_id, 0) < cutoff
                    for link_id in item["links"] for node_id in (links[link_id]["a"], links[link_id]["b"])):
                MixedController._receipt_status(item, "invalidated_stale_channel")
                db.execute("UPDATE mixed_reservations SET body=? WHERE request_id=?", (canonical(item), row["request_id"]))

    def configure(self, request, *, admit=None):
        configuration = validate_configuration(request)
        with self._lock:
            return self._configure(configuration, admit=admit)

    def _configure(self, configuration, *, admit=None):
        claimed = {}
        old_leases = self._resource_leases
        try:
            with self._db(admit) as db:
                result, changed = self._configure_locked(configuration, db, claimed)
        except BaseException:
            for node_id, lease in (claimed or {}).items():
                if old_leases.get(node_id) is not lease:
                    lease.revoke()
            raise
        if changed:
            self._resource_leases = claimed
            for node_id, lease in old_leases.items():
                if claimed.get(node_id) is not lease:
                    lease.revoke()
        return result

    def _configure_locked(self, configuration, db, claimed):
        old = db.execute("SELECT revision,body FROM mixed_config WHERE id=1").fetchone()
        if old and json.loads(old["body"]) == configuration:
            return {"status": "unchanged", "revision": old["revision"], "configuration_sha256": digest(configuration)}, False
        for node in configuration["nodes"]:
            if db.execute("SELECT 1 FROM mixed_events WHERE node_id=? AND session_id=? LIMIT 1",
                          (node["node_id"], node["session_id"])).fetchone():
                raise ValueError("reconfiguration requires new acquisition sessions")
        claimed.update(self._claim_resources(configuration))
        revision = 1 if old is None else old["revision"] + 1
        self._invalidate(db, "invalidated_reconfiguration")
        db.execute("DELETE FROM mixed_nodes")
        for node in configuration["nodes"]:
            state = {"status": "no_input", "sequence": -1, "timestamp_ps": -1, "available": 0,
                     "received_ns": 0, "links": {}, "time_tag_count": 0, "lost_events": 0,
                     "configuration_id": None, "resources_received_ns": 0, "links_received_ns": {}, "analysis": None}
            db.execute("INSERT INTO mixed_nodes VALUES (?,?)", (node["node_id"], canonical(state)))
        db.execute("INSERT OR REPLACE INTO mixed_config VALUES (1,?,?)", (revision, canonical(configuration)))
        return {"status": "configured", "revision": revision, "configuration_sha256": digest(configuration),
                "physical_action_performed": False}, True

    def ingest(self, request, *, admit=None):
        exact(request, {"records"})
        if not isinstance(request["records"], list) or not 1 <= len(request["records"]) <= 256:
            raise ValueError("ingestion requires 1..256 bounded records")
        records = [validate_record(value) for value in request["records"]]
        results = []
        with self._db(admit) as db:
            configuration = self._configuration(db)
            nodes = {node["node_id"]: node for node in configuration["nodes"]}
            states = self._states(db)
            for observation, provenance in records:
                node_id = observation.node_id
                if node_id not in nodes:
                    raise ValueError("observation node not registered")
                node, state = nodes[node_id], states[node_id]
                if provenance["origin"] == "physical" and node_id not in self._resource_leases:
                    raise ValueError("physical observations require a bound resource and shared authority")
                raw = canonical(observation.to_dict())
                previous = db.execute("SELECT body,provenance FROM mixed_events WHERE event_id=?",
                                      (observation.event_id,)).fetchone()
                if previous:
                    if previous["body"] != raw or previous["provenance"] != canonical(provenance):
                        raise ValueError("conflicting observation id")
                    results.append({"event_id": observation.event_id, "status": "duplicate"})
                    continue
                if (observation.session_id != node["session_id"] or observation.clock_domain != node["clock_domain"]
                        or observation.sequence <= state["sequence"]):
                    raise ValueError("observation session, clock or sequence rejected")
                if observation.kind == "link_state":
                    link = next((link for link in configuration["links"] if link["link_id"] == observation.data["link_id"]), None)
                    if link is None or node_id not in {link["a"], link["b"]}:
                        raise ValueError("node cannot report another node's channel")
                if observation.kind == "resources" and observation.data["available"] > node["capacity"]:
                    raise ValueError("resource report exceeds configured capacity")
                if observation.kind == "timetags" and "time_tagging" not in node["capabilities"]:
                    raise ValueError("node lacks time-tagging capability")
                if observation.kind == "correlation" and ("correlation" not in node["capabilities"] or
                        observation.data["configuration_id"] != state["configuration_id"]):
                    raise ValueError("correlation capability/configuration does not match verified session")
                gap = observation.sequence != state["sequence"] + 1
                regression = observation.timestamp_ps < state["timestamp_ps"]
                if gap or regression:
                    state["status"] = "uncertain_sequence" if gap else "uncertain_clock"
                elif observation.kind == "session_started":
                    # A faulted session cannot be resurrected by another verification message.
                    if state["sequence"] == -1 and observation.sequence == 0 and observation.data["verified"]:
                        state["status"] = "ready"
                        state["configuration_id"] = observation.data["configuration_id"]
                    else:
                        state["status"] = "configuration_unverified"
                elif observation.kind == "fault":
                    # A source's diagnostic identifier is never an operational state.
                    state["status"] = "fault"
                    state["fault_reason"] = observation.data["reason"]
                elif state["status"] == "ready":
                    if observation.kind == "link_state":
                        state["links"][observation.data["link_id"]] = observation.data
                        state["links_received_ns"][observation.data["link_id"]] = time.time_ns()
                        if observation.data["classical_state"] != "up" or observation.data["quantum_state"] == "down":
                            self._invalidate(db, "invalidated_channel", {node_id})
                        elif observation.data["quantum_state"] != "up":
                            for row in db.execute("SELECT request_id,body FROM mixed_reservations").fetchall():
                                item = json.loads(row["body"])
                                if (item["status"] == "reserved" and item["request"]["operation"] == "entanglement"
                                        and node_id in item["route"]):
                                    self._receipt_status(item, "invalidated_quantum_channel")
                                    db.execute("UPDATE mixed_reservations SET body=? WHERE request_id=?",
                                               (canonical(item), row["request_id"]))
                    elif observation.kind == "resources":
                        state["available"] = observation.data["available"]
                        state["resources_received_ns"] = time.time_ns()
                        held = sum(node_id in json.loads(row["body"])["route"]
                                   for row in db.execute("SELECT body FROM mixed_reservations")
                                   if json.loads(row["body"])["status"] == "reserved")
                        if held > state["available"]:
                            self._invalidate(db, "invalidated_resource_change", {node_id})
                    elif observation.kind == "timetags":
                        state["time_tag_count"] += len(observation.data["events"])
                        state["lost_events"] += observation.data["lost_events"]
                        if observation.data["lost_events"]:
                            state["status"] = "uncertain_data_loss"
                    elif observation.kind == "correlation":
                        state["analysis"] = observation.data
                if state["status"] != "ready":
                    self._invalidate(db, "invalidated_observation", {node_id})
                state["sequence"] = observation.sequence
                state["timestamp_ps"] = observation.timestamp_ps
                state["received_ns"] = time.time_ns()
                db.execute("INSERT INTO mixed_events VALUES (?,?,?,?,?,?,?)",
                           (observation.event_id, node_id, observation.session_id, observation.sequence,
                            raw, canonical(provenance), state["received_ns"]))
                db.execute("UPDATE mixed_nodes SET body=? WHERE node_id=?", (canonical(state), node_id))
                results.append({"event_id": observation.event_id, "status": "accepted", "node_state": state["status"]})
        return {"status": "recorded", "records": results, "receipt_sha256": digest(results),
                "physical_action_performed": False}

    def demand(self, request, *, admit=None):
        exact(request, {"request_id", "source", "target", "operation", "ttl_s"})
        for name in ("request_id", "source", "target"):
            identifier(request[name])
        integer(request["ttl_s"], minimum=1, maximum=3600)
        with self._db(admit) as db:
            now = time.time_ns()
            self._expire(db, now)
            previous = db.execute("SELECT body FROM mixed_reservations WHERE request_id=?", (request["request_id"],)).fetchone()
            if previous:
                receipt = json.loads(previous["body"])
                if receipt["request"] != request:
                    raise ValueError("request id reused with a different demand")
                self._pending_audit(db, receipt, "reserve_mixed_route")
                return receipt
            configuration, states = self._configuration(db), self._states(db)
            for row in db.execute("SELECT body FROM mixed_reservations"):
                active = json.loads(row["body"])
                if active["status"] == "reserved":
                    for node_id in active["route"]:
                        states[node_id]["available"] -= 1
            decision = route_policy(configuration, states, source=request["source"], target=request["target"],
                                    operation=request["operation"], now_ns=now)
            result = {**decision, "request": json.loads(canonical(request)), "request_id": request["request_id"],
                      "status": "reserved" if decision["status"] == "planned" else "blocked",
                      "created_ns": now, "expires_ns": now + request["ttl_s"] * 10**9,
                      "physical_action_performed": False, "quantum_result": None}
            # This is a durable reservation receipt, not proof of a pair/bit delivery.
            result["receipt_sha256"] = digest(result)
            db.execute("INSERT INTO mixed_reservations VALUES (?,?)", (request["request_id"], canonical(result)))
            self._pending_audit(db, result, "reserve_mixed_route")
        return result

    def release(self, request, *, admit=None):
        exact(request, {"request_id"})
        identifier(request["request_id"])
        with self._db(admit) as db:
            self._expire(db, time.time_ns())
            row = db.execute("SELECT body FROM mixed_reservations WHERE request_id=?", (request["request_id"],)).fetchone()
            if row is None:
                raise ValueError("unknown reservation")
            result = json.loads(row["body"])
            if result["status"] == "reserved":
                self._receipt_status(result, "released")
                db.execute("UPDATE mixed_reservations SET body=? WHERE request_id=?", (canonical(result), request["request_id"]))
            self._pending_audit(db, result, "release_mixed_reservation")
            return result

    def status(self, *, admit=None):
        with self._db(admit, validate_resources=False) as db:
            from quantum_autopilot.security.equipment import EquipmentDenied
            authority_faults = []
            for node_id, lease in self._resource_leases.items():
                try:
                    lease.require(owner=self.owner, session_id=self.owner)
                    lease.renew()
                except EquipmentDenied:
                    authority_faults.append(node_id)
            if authority_faults:
                self._invalidate(db, "invalidated_resource_authority", authority_faults)
                for node_id in authority_faults:
                    row = db.execute("SELECT body FROM mixed_nodes WHERE node_id=?", (node_id,)).fetchone()
                    state = json.loads(row["body"])
                    state.update(status="resource_authority_unavailable", available=0, links={})
                    db.execute("UPDATE mixed_nodes SET body=? WHERE node_id=?", (canonical(state), node_id))
            now = time.time_ns()
            self._expire(db, now)
            config = db.execute("SELECT revision,body FROM mixed_config WHERE id=1").fetchone()
            states = self._states(db)
            reservations = [json.loads(row["body"]) for row in db.execute("SELECT body FROM mixed_reservations ORDER BY request_id")]
            if config:
                cutoff = now - json.loads(config["body"])["freshness_s"] * 10**9
                stale = [n for n, s in states.items() if s["status"] == "ready" and s["received_ns"] < cutoff]
                if stale:
                    self._invalidate(db, "invalidated_stale", stale)
                    reservations = [json.loads(row["body"]) for row in db.execute("SELECT body FROM mixed_reservations ORDER BY request_id")]
                for n in stale:
                    states[n]["status"] = "stale_observations"
            origin_counts = {}
            for row in db.execute("SELECT provenance FROM mixed_events"):
                origin = json.loads(row["provenance"])["origin"]
                origin_counts[origin] = origin_counts.get(origin, 0) + 1
            return {"schema": "orion.mixed-controller.state.v1", "configured": config is not None,
                    "revision": config["revision"] if config else 0,
                    "configuration": json.loads(config["body"]) if config else None,
                    "nodes": states, "reservations": reservations,
                    "audit_origin_counts": origin_counts, "source_blind_policy": True,
                    "resource_authority": {"scope": "shared_local_authority" if self.resource_authority else "isolated_simulation",
                        "bound_nodes": sorted(self._resource_leases),
                        "fences": {n: lease.fence for n, lease in self._resource_leases.items()},
                        "unavailable_nodes": authority_faults,
                        "multi_host_coordination": False},
                    "audit_pending": [dict(row) for row in db.execute(
                        "SELECT request_id,receipt_sha256,operation FROM mixed_audit_outbox "
                        "WHERE state='pending' ORDER BY request_id,receipt_sha256,operation LIMIT 200")],
                    "audit_pending_count": db.execute(
                        "SELECT COUNT(*) FROM mixed_audit_outbox WHERE state='pending'").fetchone()[0],
                    "physical_action_performed": False, "certified_key_bits": 0}

    def close(self):
        with self._lock:
            with self._db(validate_resources=False) as db:
                self._invalidate(db, "invalidated_shutdown")
                db.execute("DELETE FROM mixed_authority WHERE owner=?", (self.owner,))
            for lease in self._resource_leases.values():
                lease.revoke()
            self._resource_leases = {}
