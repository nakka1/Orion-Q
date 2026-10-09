"""Seeded, explicitly artificial display signals in an isolated local laboratory.

DEVELOPER UI STRESS FIXTURE — NOT THE DEMONSTRATION. Every value this module shows is
generated here, on the UI side, to exercise layouts, load and fault rendering without a
backend. It is reachable only through the developer flag ``--synthetic`` (or
``--provider synthetic``) and from the in-product UI validators; it is not offered on
the login screen or in the command palette.

The product's demonstration mode is something else: ``ServiceProvider`` over
``demo_service(...)`` — the real service and network engine, with only the INPUT data
simulated by the backend (see ``console/service_provider.py`` and
``docs/product/orion_q_live_network_design.md``).
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, replace
from datetime import datetime, timedelta, timezone
import hashlib
import ipaddress
import json
import math
from pathlib import Path
import random
import re
from threading import Event as CancelEvent
from uuid import uuid4

from PySide6.QtCore import QTimer, Signal, Slot

from .contract import (Alert, Artefact, Boot, Chart, ClaimCheck, Event, KV, KeySession, Keys,
    Ledger, LedgerEntry, Link, LogLine, Meter, Metric, Node, NodeDetail, Series, Snapshot, Stage, Telemetry, Topology)
from .providers import DataProvider
from .capabilities import synthetic_capabilities
from .observations import Observation, QUANTITIES

PROFILES = {"reference": (8, 12, 32), "load": (32, 256, 500), "faults": (12, 36, 120)}
REFERENCE_POSITIONS = [(0.19, 0.20), (0.50, 0.15), (0.81, 0.25), (0.26, 0.55),
                       (0.54, 0.52), (0.84, 0.70), (0.38, 0.83), (0.68, 0.80)]


def artificial_snapshot(profile="reference", seed=1337, tick=0, selected=None, dropout=False, entries=()):
    if profile not in PROFILES:
        raise ValueError("unknown synthetic profile")
    if isinstance(seed, bool) or not isinstance(seed, int) or not 0 <= seed < 2**32:
        raise ValueError("seed must be a uint32")
    if isinstance(tick, bool) or not isinstance(tick, int) or tick < 0:
        raise ValueError("tick must be a non-negative integer")
    count, link_count, record_count = PROFILES[profile]
    rng = random.Random(seed)
    campaign = f"synthetic-{profile}-seed-{seed}"
    at = (datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(seconds=tick)).isoformat()
    clock = at[11:19]
    digest = hashlib.sha256(f"{campaign}:{tick}".encode()).hexdigest()
    absent = dropout or (profile == "faults" and tick % 12 in (5, 6))
    names = ["N-01", "N-02", "N-03", "R-07", "CORE", "N-05", "N-09", "N-11"]
    names += [f"S-{i:03d}" for i in range(8, count)]
    width = math.ceil(math.sqrt(count))
    nodes = []
    for i, name in enumerate(names):
        x, y = REFERENCE_POSITIONS[i] if count == 8 else ((i % width + .5) / width, (i // width + .5) / width)
        health = "down" if (i + tick // 3) % 11 == 6 else "degraded" if (i + tick) % 7 == 3 else "up"
        nodes.append(Node(name, "SIMULATED NODE", health, x, y,
                          "core" if name == "CORE" else "repeater" if name == "R-07" else "node",
                          41.90 - y * .04, -87.60 + x * .05))
    edges = [(i, j) for i in range(count) for j in range(i + 1, count)]
    rng.shuffle(edges)
    if profile == "reference":
        edges = [(0,1), (1,2), (0,3), (3,4), (4,2), (4,5), (3,6), (6,7), (7,5), (1,4), (0,5), (6,2)]
    fidelity = 0.9471 + 0.014 * math.sin(tick / 8)
    qber = 2.14 + 0.9 * math.sin(tick / 5)
    def reading(entity, quantity, value):
        return Observation(quantity, value, QUANTITIES[quantity][1], "synthetic-display-generator", campaign,
            entity, "aggregate" if entity == "network-summary" else entity, "synthetic-generator",
            f"{entity}:{tick}", at, "simulated", clock_domain="simulation", clock_id=campaign,
            fidelity_method="synthetic_display_signal" if quantity == "fidelity" else None,
            fidelity_reference="bell_phi_plus" if quantity == "fidelity" else None)
    network = {key: reading("network-summary", key, value)
               for key, value in (("fidelity", fidelity), ("qber", qber / 100), ("skr_bps", 41200.0))}
    observations = list(network.values())
    links = []
    for index, (i, j) in enumerate(edges[:link_count]):
        ident = f"ARTIFICIAL-{index:04d}"
        samples = {key: reading(ident, key, value) for key, value in (
            ("fidelity", fidelity - (index % 8) * .01), ("qber", (qber + index % 5) / 100),
            ("skr_bps", (41.2 - index % 20) * 1000))}
        observations.extend(samples.values())
        links.append(Link(names[i], names[j], ident,
            samples["fidelity"].formatted(".4f"), samples["qber"].formatted(".2f", unit="%") + "%",
            samples["skr_bps"].formatted(".1f", unit="kb/s") + "k",
            "fault" if index % 9 == tick % 9 else "fibre", 1.0,
            "red" if index % 9 == tick % 9 else "amber", provenance="simulated", id=ident))
    metrics = [Metric("BELL FIDELITY", network["fidelity"].formatted(".4f"), provenance="simulated", tone="amber",
                      note="Artificial display signal; not tomography."),
               Metric("QBER", network["qber"].formatted(".2f", unit="%"), "%", provenance="simulated", tone="green",
                      note="Injected percentage; not a measured channel."),
               Metric("SECRET KEY RATE", network["skr_bps"].formatted(".1f", unit="kb/s"), "kb/s", provenance="simulated", tone="violet",
                      note="Test payload; no key is generated.")]
    chart = Chart([Series(label, [.94 + .02 * math.sin((t + tick) / 8 + i) - i * .025 for t in range(120)],
                          colour) for i, (label, colour) in enumerate((("SIM A", "amber"), ("SIM B", "green"), ("SIM C", "red")))], .85, 1.0,
                  ["-120", "-90", "-60", "-30", "NOW"], .9, "ARTIFICIAL THRESHOLD 0.900")
    # All display values below stay in the same artificial campaign lane.
    checks = [LogLine(clock, body, "SIM", "violet") for body in
              ("Seeded display generator", "Isolated test state", "No hardware adapter", "Unsigned local records")]
    boot = Boot("OrionQ", "", "Artificial injection laboratory. No experimental evidence is produced.", checks,
                [], [], "These indicators are generated test signals, not hardware quorum or measured fidelity.", "OrionQ · SYNTHETIC LAB")
    events = [Event("INJECTED FAULT" if i % 3 == 0 else "TEST EVENT", names[i % count], clock,
                    f"Artificial event {i}; seed {seed}. No physical device changed.", "red" if i % 3 == 0 else "amber")
              for i in range(12)]
    topo = Topology(campaign, [] if absent else nodes, [] if absent else links,
        [] if absent else metrics, [] if absent else events,
        "0/0" if absent else f"{sum(link.kind != 'fault' for link in links)}/{len(links)}",
        [KV("SOURCE", "ARTIFICIAL INJECTION", "violet"), KV("SEED", str(seed)), KV("FRAME", str(tick))],
        "SYNTHETIC TEST DATA · Not measured; no WDM-to-quantum scientific inference. Logical graph; no hardware.")
    telemetry = Telemetry([] if absent else metrics, Chart([], 0, 1) if absent else chart,
        [] if absent else [Meter("SIM T1", .71, "12.4 ms"), Meter("SIM T2", .43, "1.84 ms")],
        "Artificial memory signals, not calibration fits.", [KV("SEED", str(seed)), KV("FRAME", str(tick)),
            KV("CLOCK", "SIMULATION · " + campaign), KV("VIRTUAL ACQUISITION", at)],
        ClaimCheck("exceeds", clock, "SYNTHETIC INJECTION", "Artificial signals cannot validate a measured quantum claim."),
        ["No live telemetry or device connection.", "Key material, hardware tokens and signatures are not generated.",
         "Controller tests use a separate three-node simulator and journal."],
        [Artefact("seeded display snapshot", digest[:16], "synthetic")], "ARTIFICIAL SIGNAL WINDOW · 120 FRAMES")
    sessions = [KeySession(f"SIM-{i:05d}-long-session-identifier-for-overflow-test", f"{names[i%count]} ↔ {names[(i+1)%count]}",
        "41.2k", f"{qber:.2f}%", "SIMULATED", hashlib.sha256(str(i).encode()).hexdigest()[:12], "violet")
        for i in range(record_count)]
    keys = Keys(campaign, [Stage(f"0{i+1}", name, val, unit, "Artificial test payload")
        for i, (name, val, unit) in enumerate((("SIFTING", "1.24", "Mb"), ("ERROR EST.", "1.4", "%"),
                                              ("RECONCILIATION", "1.09", "Mb"), ("PRIVACY AMP.", "824", "kb")))],
        "SYNTHETIC POOL · 512 DISPLAY BLOCKS", [] if absent else [rng.choice(("violet", "amber", "dim")) for _ in range(512)],
        [KV("PROVENANCE", "ARTIFICIAL", "violet")], [] if absent else sessions,
        "Test records only: no distillation, key material or HSM operation.", [KV("SOURCE", "ARTIFICIAL"), KV("SEED", str(seed))],
        "Not a cryptographic security calculation.", [Meter("SIM consumer", .6, "12 kb/s", "violet")],
        [KV("CAMPAIGN", campaign), KV("SNAPSHOT", digest[:16])], "SYNTHETIC INJECTION · NO KEY MATERIAL")
    current = selected if selected in names else names[3]
    node = NodeDetail("UNAVAILABLE" if absent else current, "", "SYNTHETIC REPEATER · NO DEVICE",
        Alert("INJECTED DEGRADATION", "Artificial warning to exercise recovery and acknowledgements.", "amber"),
        [KV("SOURCE", "SYNTHETIC GENERATOR"), KV("SEED", str(seed))], [KV("MODE", "TEST ONLY", "violet")],
        [] if absent else [rng.choice(("violet", "amber", "red", "dim")) for _ in range(128)],
        [KV("REGISTERS", "128 ARTIFICIAL", "violet")], [] if absent else [(rng.uniform(.3, 1), "amber") for _ in range(60)],
        ("-60", "INJECTED", "NOW"), [Meter("SIM bias", .47, "0.47", "amber", .6)],
        "Generated parameters; no calibration command is issued.",
        [] if absent else [LogLine(clock, f"Synthetic record {i} for {current}", "SIM", "violet") for i in range(record_count)], campaign)
    audit_rows = [LedgerEntry(clock, "SYNTHETIC", f"Injected event {i}", hashlib.sha256(f"{seed}:{i}".encode()).hexdigest()[:16],
                             ("system", "key", "calibration")[i % 3], "violet") for i in range(record_count)]
    ledger = Ledger([], [KV("SOURCE", "ARTIFICIAL TEST RECORDS")], [] if absent else [*audit_rows, *entries][-2000:],
        "UNSIGNED · SYNTHETIC", 0, "Display hashes are not signatures or a verified ledger chain.",
        [KV("CAMPAIGN", campaign), KV("SEED", str(seed))], "SYNTHETIC INJECTION · NO REAL AUDIT CLAIM")
    return Snapshot(at, "SYNTHETIC LAB", campaign,
                    [KV("SYNTHETIC", str(seed), "violet"), KV("FRAME", str(tick)), KV("NO HARDWARE", "")],
                    boot, topo, telemetry, keys, node, ledger, input_kind="synthetic",
                    capabilities=synthetic_capabilities(degraded=absent),
                    observations=() if absent else tuple(observations))


class SyntheticProvider(DataProvider):
    busy_changed = Signal(bool)
    error = Signal(str)
    progress = Signal(str)
    _finished = Signal(object)
    is_synthetic = True

    @property
    def capabilities(self):
        return synthetic_capabilities(degraded=self.dropout or (self.profile == "faults" and self.tick % 12 in (5, 6)))

    def __init__(self, repository_root, state_root, *, profile="reference", seed=1337, interval_ms=1000):
        super().__init__()
        if not 100 <= interval_ms <= 60000:
            raise ValueError("interval_ms must be between 100 and 60000")
        self.repository_root, self.state_root = Path(repository_root), Path(state_root)
        self.profile, self.seed, self.tick = profile, seed, 0
        self.selected, self.dropout, self.entries = None, False, []
        self._staged_nodes, self._link_overrides, self._node_controls = {}, {}, {}
        self._topology_version = 0
        self.cancel = CancelEvent()
        self.executor, self.future = None, None
        self.timer = QTimer(self)
        self.timer.setInterval(interval_ms)
        self.timer.timeout.connect(self.advance)
        self._finished.connect(self._complete)
        self.last_report = None
        from .synthetic_sync import SyntheticSyncStream
        self.sync_stream = SyntheticSyncStream()
        from .synthetic_compensation import SyntheticCompensationStream
        self.compensation_stream = SyntheticCompensationStream()
        from .synthetic_allocation import SyntheticAllocationStream
        self.allocation_stream = SyntheticAllocationStream()
        from .synthetic_routing import SyntheticRoutingStream
        self.routing_stream = SyntheticRoutingStream()
        from .synthetic_keyrate import SyntheticKeyRateStream
        self.keyrate_stream = SyntheticKeyRateStream()
        self._snap = self._build()

    def _build(self):
        snapshot = artificial_snapshot(self.profile, self.seed, self.tick, self.selected, self.dropout, self.entries)
        from quantum_autopilot.control_plane.topology import DEFAULT_TOPOLOGY_ID
        snapshot.topology.id = DEFAULT_TOPOLOGY_ID
        snapshot.topology.version = self._topology_version
        if snapshot.topology.nodes:
            nodes = [*snapshot.topology.nodes, *self._staged_nodes.values()]
            if self._staged_nodes:
                columns = math.ceil(math.sqrt(len(nodes)))
                rows = math.ceil(len(nodes) / columns)
                nodes = [replace(node, x=(index % columns + .5) / columns,
                                 y=(index // columns + .5) / rows) for index, node in enumerate(nodes)]
            snapshot.topology.nodes = [replace(node, isolated=self._node_controls.get(node.id, {}).get('isolated', False),
                calibration_count=self._node_controls.get(node.id, {}).get('calibration_count', 0),
                health='down' if self._node_controls.get(node.id, {}).get('isolated') else
                       'up' if self._node_controls.get(node.id, {}).get('calibration_count') else node.health)
                for node in nodes]
            links = {link.id: link for link in snapshot.topology.links}
            links.update(self._link_overrides)
            snapshot.topology.links = list(links.values())
            enabled = sum(link.configured_active is not False for link in links.values())
            snapshot.topology.links_up = f'{enabled}/{len(links)}'
            selected = next((node for node in snapshot.topology.nodes if node.id == self.selected), None)
            if selected and snapshot.node.id != selected.id:
                snapshot.node = replace(snapshot.node, id=selected.id, name_local=selected.role,
                    registers=[], throughput=[], log=[], calibration=[],
                    calibration_note='Local simulation node; no measured calibration or hardware.')
        snapshot.optional_sync_state = self.sync_stream.push(snapshot, self.seed, self.tick)
        snapshot.optional_compensation_states = self.compensation_stream.push(snapshot, self.seed, self.tick)
        snapshot.optional_allocation_state = self.allocation_stream.push(snapshot, self.seed, self.tick)
        snapshot.optional_routing_state = self.routing_stream.push(snapshot, self.seed, self.tick)
        snapshot.optional_keyrate_state = self.keyrate_stream.push(snapshot, self.seed, self.tick)
        return snapshot

    def start(self):
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="orion-test")
        self.publish_snapshot()
        self.timer.start()

    def stop(self):
        self.timer.stop()
        self.sync_stream.reset()
        self._snap.optional_sync_state = None
        self.compensation_stream.reset()
        self._snap.optional_compensation_states = ()
        self.allocation_stream.reset()
        self._snap.optional_allocation_state = None
        self.routing_stream.reset()
        self._snap.optional_routing_state = None
        self.keyrate_stream.reset()
        self._snap.optional_keyrate_state = None
        self.cancel.set()
        if self.executor:
            self.executor.shutdown(wait=True, cancel_futures=True)
            self.executor = None

    def advance(self):
        self.tick += 1
        self._snap = self._build()
        self.publish_snapshot()

    def dispatch(self, verb, target=""):
        verb = "export_record" if verb == "export" else verb
        self.capabilities.require(verb)
        if verb in {'stage_node', 'apply_simulated_node_action', 'configure_simulated_link'}:
            if not isinstance(target, str) or len(target) > 4096:
                raise ValueError('bounded JSON simulation request required')
            from .input_sources import _unique_fields
            payload = json.loads(target, object_pairs_hook=_unique_fields)
        if verb == 'stage_node':
            request = payload
            if not isinstance(request, dict) or set(request) != {'id', 'role', 'address', 'port', 'protocol'}:
                raise ValueError('invalid local node fields')
            ident = request['id']
            if not isinstance(ident, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,63}', ident):
                raise ValueError('invalid local node ID')
            if (not isinstance(request['role'], str) or request['role'] not in {'ENDPOINT', 'REPEATER', 'CORE'}
                    or request['protocol'] not in ('QNET-TLS', 'QNET-REPEATER', 'QNET-CORE', 'QNET-SAT')):
                raise ValueError('invalid local node role or protocol')
            if not isinstance(request['address'], str) or len(request['address']) > 64:
                raise ValueError('address must be an IP literal')
            ipaddress.ip_address(request['address'])
            if type(request['port']) is not int or not 1 <= request['port'] <= 65535:
                raise ValueError('invalid local node port')
            if not self._snap.topology.nodes:
                raise ValueError('topology unavailable; wait for input recovery')
            if ident in {node.id for node in self._snap.topology.nodes}:
                raise ValueError('duplicate local node ID')
            if len(self._snap.topology.nodes) >= 32:
                raise ValueError('local inventory supports at most 32 nodes')
            self._staged_nodes[ident] = Node(ident, request['role'], 'degraded', kind={
                'ENDPOINT': 'node', 'REPEATER': 'repeater', 'CORE': 'core'}[request['role']],
                connection_address=request['address'], connection_port=request['port'],
                connection_protocol=request['protocol'], inventory_origin='shadow')
            self._topology_version += 1
        elif verb == 'remove_staged_node':
            if target not in self._staged_nodes:
                raise ValueError('only a locally added node can be removed')
            del self._staged_nodes[target]
            self._node_controls.pop(target, None)
            self._link_overrides = {key: link for key, link in self._link_overrides.items()
                                    if target not in (link.a, link.b)}
            self._topology_version += 1
        elif verb == 'apply_simulated_node_action':
            from quantum_autopilot.control_plane.inventory import SimulatedNodeAction
            request = SimulatedNodeAction.from_dict(payload)
            if request.expected_version != self._topology_version:
                raise ValueError('topology version conflict; refresh before acting')
            if request.node_id not in {node.id for node in self._snap.topology.nodes}:
                raise ValueError('unknown simulated node')
            control = self._node_controls.setdefault(request.node_id, {})
            if request.action == 'isolate':
                if control.get('isolated'):
                    raise ValueError('node is already isolated in the simulator')
                for link in self._snap.topology.links:
                    if request.node_id in (link.a, link.b):
                        self._link_overrides[link.id] = replace(link, configured_active=False, kind='fault', tone='red')
                control['isolated'] = True
            else:
                control['calibration_count'] = control.get('calibration_count', 0) + 1
            self._topology_version += 1
        elif verb == 'configure_simulated_link':
            from quantum_autopilot.control_plane.inventory import ConfigureSimulatedLink
            request = ConfigureSimulatedLink.from_dict(payload)
            if request.expected_version != self._topology_version:
                raise ValueError('topology version conflict; reload the link form')
            if not {request.left_node_id, request.right_node_id} <= {node.id for node in self._snap.topology.nodes}:
                raise ValueError('both link endpoints must exist')
            previous = next((link for link in self._snap.topology.links if link.id == request.link_id), None)
            if (request.mode == 'create') == (previous is not None):
                raise ValueError('link already exists or is missing for update')
            if previous and (previous.a, previous.b) != (request.left_node_id, request.right_node_id):
                raise ValueError('updating link endpoints is not supported')
            if any(link.id != request.link_id and {link.a, link.b} == {request.left_node_id, request.right_node_id}
                   for link in self._snap.topology.links):
                raise ValueError('parallel links are not supported by this editor')
            if previous is None and len(self._snap.topology.links) >= 256:
                raise ValueError('local inventory supports at most 256 links')
            if previous and (previous.distance_km, previous.nominal_pair_fidelity, previous.configured_active) == (
                    request.distance_km, request.nominal_pair_fidelity, request.active) and previous.channels == request.channels:
                raise ValueError('configuration unchanged')
            self._link_overrides[request.link_id] = Link(request.left_node_id, request.right_node_id, request.link_id,
                fidelity=f'{request.nominal_pair_fidelity:.4f}', provenance='simulated', id=request.link_id,
                configured_active=request.active, distance_km=request.distance_km,
                nominal_pair_fidelity=request.nominal_pair_fidelity,
                kind='fibre' if request.active else 'fault', tone='amber' if request.active else 'red',
                channels=request.channels)
            if request.active:
                for ident in (request.left_node_id, request.right_node_id):
                    self._node_controls.setdefault(ident, {})['isolated'] = False
            self._topology_version += 1
        elif verb == "isolate":
            raise ValueError("Physical isolation is unavailable; synthetic display nodes are not devices")
        elif verb == "select_node":
            if target not in {n.id for n in self._snap.topology.nodes}:
                raise ValueError("unknown synthetic node")
            self.selected = target
        elif verb == "pause":
            self.timer.stop() if self.timer.isActive() else self.timer.start()
        elif verb == "dropout":
            self.dropout = not self.dropout
        elif verb == "cancel":
            self.cancel.set()
        elif verb in {"run_scenario", "recalibrate"}:
            if self.future is not None:
                return False
            if self.executor is None:
                raise RuntimeError("start the synthetic provider before running a campaign")
            from .stress import run_controller_stress
            self.cancel.clear()
            self.busy_changed.emit(True)
            self.progress.emit("Testing the existing controller in an isolated journal…")
            self.future = self.executor.submit(run_controller_stress, self.repository_root, self.state_root,
                seed=self.seed, rounds=5, cancel=self.cancel)
            self.future.add_done_callback(self._finished.emit)
            return True
        elif verb in {"export", "export_record"}:
            self.state_root.mkdir(parents=True, exist_ok=True)
            path = self.state_root / f"synthetic-snapshot-{uuid4().hex}.json"
            from .evidence_destination import export_destination
            path = export_destination(target, path)
            with path.open("x", encoding="utf-8") as handle:
                json.dump({"scope": "artificial_ui_injection", "snapshot": asdict(self._snap)}, handle, allow_nan=False)
            self.progress.emit(f"Exported artificial snapshot: {path}")
            self.export_completed.emit(str(path.resolve()))
        elif verb not in {"acknowledge", "reload"}:
            raise ValueError("unsupported synthetic command")
        self.entries.append(LedgerEntry(self._snap.at[11:19], "SYNTHETIC", f"{verb}: {target}", "unsigned", "system"))
        self.entries = self.entries[-500:]
        self.advance()
        return True

    @Slot(object)
    def _complete(self, future):
        self.future = None
        try:
            self.last_report = future.result()
            self.progress.emit(f"Controller test {'PASS' if self.last_report['passed'] else 'FAIL/CANCELLED'}: {self.last_report['report_path']}")
        except Exception as exc:
            self.error.emit(str(exc))
        finally:
            self.busy_changed.emit(False)
            self.advance()
