"""Qt presentation client for a separately running authenticated OrionQ core.

No local application service, network engine, security runtime or journal is
constructed. TLS requests execute on bounded workers; failed mutations are not
retried and unavailable connections never select an in-process fallback.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import json
import hashlib
from pathlib import Path
from uuid import uuid4

from PySide6.QtCore import QTimer, Signal, Slot

from .capabilities import LIVE_NETWORK_OPERATIONS, SourceCapabilities
from .contract import Boot, CommandView, KV, LogLine, Snapshot, WorkspacePathResult
from .input_sources import empty_snapshot
from .providers import DataProvider
from .service_provider import ServiceProvider, _clock, live_view_from_state


NETWORK_OPERATIONS = tuple(name for name in LIVE_NETWORK_OPERATIONS if name not in {
    "set_model_archive", "export_technology_events", "provision_satellite_session"})
REMOTE_OPERATIONS = (*NETWORK_OPERATIONS, "run_workspace_path", "run_temporal_path", "run_lab_experiment",
                     "configure_laboratory", "run_scenario", "replay_model")
TRACKED = {"add_network_node", "set_node_location", "set_node_isolation", "set_link_active", "recalibrate_node"}


class _RemotePresentation:
    """Reuse existing formatting functions only, without ServiceProvider init."""

    _counts = ServiceProvider._counts
    _topology = ServiceProvider._topology
    _topology_caveat = ServiceProvider._topology_caveat
    _event = ServiceProvider._event
    _telemetry = ServiceProvider._telemetry
    _keys = ServiceProvider._keys
    _node = ServiceProvider._node
    _ledger = ServiceProvider._ledger
    _live_entries = ServiceProvider._live_entries
    _safety_tone = ServiceProvider._safety_tone
    _safety_label = ServiceProvider._safety_label

    def __init__(self, raw, selected):
        self.raw, self._selected_node = raw, selected
        # Published-cell assessment is an operation, never a side effect of a
        # client-side paint. An example exists only if the backend supplied it.
        self._example = raw.get("example", {})

    def replay(self):
        replay = self.raw.get("replay")
        return replay if isinstance(replay, dict) and replay.get("available") is not False else None

    def replay_error(self):
        return (self.raw.get("replay") or {}).get("reason", "No model replay supplied by the remote core.")

    def summary_error(self):
        return (self.raw.get("summary") or {}).get("reason", "No validated claim registry supplied by the remote core.")


def remote_snapshot(raw: dict, *, endpoint: str, selected=None, capabilities=None) -> Snapshot:
    required = {"health", "topology", "model", "evidence_lanes", "security"}
    if (not isinstance(raw, dict) or raw.get("schema") != "orion.console.remote.v1"
            or any(not isinstance(raw.get(key), dict) for key in required)
            or not isinstance(raw.get("events", []), list)
            or raw["health"].get("physical_read_only") is not True
            or raw["health"].get("automation_allowed") is not False):
        raise ValueError("remote core snapshot contract or execution boundary is invalid")
    identity = raw["security"]
    if (not isinstance(identity.get("subject"), str) or not identity["subject"]
            or identity.get("source") not in {"certificate", "service", "oidc"}
            or not isinstance(identity.get("roles"), list) or not isinstance(identity.get("scopes"), list)):
        raise ValueError("remote core did not identify the authenticated peer")
    events = raw.get("events", [])
    health, payload, model, lanes = (raw[name] for name in ("health", "topology", "model", "evidence_lanes"))
    live_raw = raw.get("live")
    if live_raw is not None and not isinstance(live_raw, dict):
        raise ValueError("invalid remote live state")
    live = live_view_from_state(live_raw, payload) if live_raw else None
    mapper = _RemotePresentation(raw, selected)
    topology = mapper._topology(payload, events, live)
    summary = raw.get("summary")
    if not isinstance(summary, dict) or summary.get("available") is False:
        summary = None
    ledger = mapper._ledger(events, lanes, summary, raw.get("safety_events", []), live, payload)
    commands = raw.get("commands", [])
    if not isinstance(commands, list) or len(commands) > 50:
        raise ValueError("invalid remote command history")
    ledger.commands = tuple(_command_view(envelope) for envelope in commands)
    ledger.commands_note = ("Latest remote inventory command receipts; canonical hashes checked. "
                            "REQUESTED/RUNNING remain unresolved after a restart; they do not prove activity.")
    ledger.sessions = [replace(row, value="remote core durable journal; no client journal")
                       if row.key == "JOURNAL" else row for row in ledger.sessions]
    clock = _clock(health.get("generated_at"))
    boot = Boot("OrionQ", "OrionQ", "Authenticated remote core · TLS 1.3",
        [LogLine(clock, "Server certificate and hostname verified by TLS transport", "VERIFIED", "green"),
         LogLine(clock, "Peer identity confirmed by the backend", identity["subject"], "green"),
         LogLine(clock, "Physical actuation remains locked on the core", "LOCKED", "amber")],
        [KV("REMOTE CORE", endpoint), KV("PEER", identity["subject"]),
         KV("ROLES", ", ".join(identity["roles"]))],
        [KV("AUTHENTICATION", "mutual TLS; backend permissions per call")],
        "Processing, routing, acquisition, keys and durable receipts live on the remote core. "
        "This desktop presents backend records and sends authenticated requests; it does not generate protocol data.",
        f"core {health.get('autopilot_version', 'version unavailable')}")
    return Snapshot(clock, identity.get("display_name") or identity["subject"],
        (summary or {}).get("operating_grid", {}).get("grid_id", "remote-core"),
        mapper._counts(topology, events, health, live), boot, topology,
        mapper._telemetry(model), mapper._keys(summary, (summary or {}).get("operating_grid", {})),
        mapper._node(payload, events, live), ledger, input_kind="service",
        capabilities=capabilities or SourceCapabilities(), live=live,
        laboratory=raw.get("laboratory"), security=identity,
        workspace_path=_workspace_path_result(raw.get("workspace_path")),
        presentation_stats={"remote_core": True, "local_protocol_processing": False})


def _workspace_path_result(envelope):
    if envelope is None:
        return None
    record = envelope["record"]
    canonical = lambda value: json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    digest = lambda value: hashlib.sha256(canonical(value)).hexdigest()
    request, values = record["request"], record["result"]
    if (record.get("physical_action_performed") is not False
            or digest(record) != envelope["record_sha256"]
            or digest(record["source_topology"]) != record["source_topology_sha256"]):
        raise ValueError("remote workspace path evidence is invalid")
    return WorkspacePathResult(request["run_id"], request["expected_version"], tuple(values["route"]),
        values["conditional_fidelity"], values["target_fidelity"], values["conditional_target_met"],
        record["source_topology_sha256"], envelope["record_sha256"], protocol=request["protocol"],
        request_hash=digest(request), outcome=values.get("outcome", "conditional_result"),
        elapsed_s=values.get("elapsed_s"), ticks=values.get("ticks"),
        memory_ages_s=tuple(values.get("memory_ages_s", ())),
        generated_pairs=sum(values.get("accounting", {}).get("generated_per_link", ())),
            expired_pairs=sum(values.get("accounting", {}).get("expired_per_link", ())))


def _command_view(envelope):
    from quantum_autopilot.control_plane.console_commands import _verify
    record = _verify(envelope["record"], envelope["record_sha256"])
    return CommandView(record["command_id"], record["verb"], record["events"][-1]["state"],
                       record["events"][0]["at_utc"], json.dumps(envelope, indent=2, ensure_ascii=False))


class RemoteProvider(DataProvider):
    busy_changed = Signal(bool)
    error = Signal(str)
    progress = Signal(str)
    _finished = Signal(object)

    is_remote = True
    is_synthetic = False
    is_demo = False
    is_read_only = False
    dispatch_validate_backend_permissions = True

    def __init__(self, client, *, endpoint: str, poll_ms=1000, export_directory=None, parent=None):
        super().__init__(parent)
        if type(poll_ms) is not int or not 250 <= poll_ms <= 60000:
            raise ValueError("remote polling must be between 250 and 60000 ms")
        self.client, self.endpoint = client, endpoint
        self.export_directory = Path(export_directory) if export_directory is not None else Path.home() / "OrionQ-exports"
        self.security = None
        self.running = self.ready = self.busy = False
        self._raw = None
        self._static = {}
        self._selected_node = None
        self._generation = 0
        self._static_epoch = 0
        self._refresh_after_poll = False
        self._poll_future = self._command_future = None
        self._poll_executor = self._command_executor = None
        self._capabilities = SourceCapabilities(adapter_id="authenticated-remote-core", note="Waiting for an authenticated core snapshot.")
        self._snap = empty_snapshot(self.source_label, "Connecting to the remote core; no local fallback.")
        self._timer = QTimer(self)
        self._timer.setInterval(poll_ms)
        self._timer.timeout.connect(self.request_refresh)
        self._finished.connect(self._complete)
        self.last_network_result = None
        self.last_command = None
        self._active_verb = None
        self._last_replay = None
        self.loading_state = {"completed": 0, "total": 2, "message": "Waiting for TLS connection and backend snapshot."}

    @property
    def source_label(self):
        return "REMOTE CORE · TLS 1.3 · " + self.endpoint

    @property
    def capabilities(self):
        return self._capabilities

    def start(self):
        if self.running:
            return
        self.running = True
        self._generation += 1
        self._poll_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="orion-remote-read")
        self._command_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="orion-remote-command")
        self.request_refresh()
        self._timer.start()

    def stop(self):
        self.running = False
        self._generation += 1
        self._timer.stop()
        for executor in (self._poll_executor, self._command_executor):
            if executor is not None:
                executor.shutdown(wait=False, cancel_futures=True)
        self._poll_executor = self._command_executor = None
        self._poll_future = self._command_future = None
        self._refresh_after_poll = False
        self.busy = False
        self.busy_changed.emit(False)

    def request_refresh(self):
        if not self.running:
            return False
        if self._poll_future is not None:
            # Coalesce refresh requests into one subsequent read, rather than
            # dropping a mutation readback or growing an executor queue.
            self._refresh_after_poll = True
            return False
        generation = self._generation
        self._poll_future = self._poll_executor.submit(self._collect, dict(self._static), self._static_epoch,
                                                      self._last_replay)
        self._poll_future.add_done_callback(lambda future: self._notify_completion(generation, "poll", future))
        return True

    def _notify_completion(self, generation, kind, future):
        if not self.running or generation != self._generation:
            return
        try:
            self._finished.emit((generation, kind, future))
        except RuntimeError:
            # Qt may have destroyed the owning window while a bounded HTTP
            # operation was still finishing. No callback may resurrect it.
            pass

    def _prepare(self, raw):
        roles = raw.get("security", {}).get("roles", [])
        operation_names = ["select_node", "select_link", "reload", "read_network_live", "read_console_commands"]
        can_control = bool(set(roles) & {"administrator", "operator"})
        can_simulate = can_control or "researcher" in roles
        if can_simulate:
            operation_names.append("export_record")
        if can_control:
            operation_names += list(REMOTE_OPERATIONS)
        elif can_simulate:
            operation_names += ["run_workspace_path", "run_temporal_path", "run_scenario", "replay_model"]
        model = raw.get("model", {})
        if model.get("configured") is not True or model.get("valid") is not True:
            operation_names = [operation for operation in operation_names if operation != "replay_model"]
        capabilities = SourceCapabilities(adapter_id="authenticated-remote-core", mode="simulation" if can_simulate else "read_only",
            health="ready", operations=tuple(dict.fromkeys(operation_names)),
            note="UI affordances only. The remote core authenticates the certificate and authorizes every request; physical actuation is locked.")
        snap = remote_snapshot(raw, endpoint=self.endpoint, selected=self._selected_node, capabilities=capabilities)
        return raw, snap, capabilities

    def _collect(self, current_static, epoch, replay):
        raw = self.client.console_snapshot(include_static=not bool(current_static))
        static_names = {"model", "summary", "evidence_lanes", "replay", "example"}
        current_static.update({name: raw[name] for name in static_names if name in raw})
        merged = dict(current_static, **raw)
        if replay is not None and replay.get("bundle_sha256") == merged.get("model", {}).get("bundle_sha256"):
            # An explicit protocol run's immutable backend response may remain
            # visible; polling must never execute another replay as a side effect.
            merged["replay"] = replay
        result = self._prepare(merged)
        return (*result, current_static, epoch)

    def _present(self, prepared):
        raw, snap, capabilities, static, epoch = prepared
        if self._selected_node and snap.node.id != self._selected_node:
            mapper = _RemotePresentation(raw, self._selected_node)
            snap = replace(snap, node=mapper._node(raw["topology"], raw.get("events", []), snap.live))
        if self.last_command is not None:
            # A successful read after reconnect is not an acknowledgement of
            # the preceding mutation. Retain its correlation and uncertainty.
            snap = replace(snap, presentation_stats={**snap.presentation_stats,
                "last_remote_command": dict(self.last_command),
                "mutation_outcome_uncertain": self.last_command.get("outcome") == "uncertain"})
        self._raw, self._snap, self._capabilities = raw, snap, capabilities
        if epoch == self._static_epoch:
            self._static = static
        self.ready = True
        self.loading_state = {"completed": 2, "total": 2, "message": "Authenticated backend snapshot admitted."}
        self.loading_progress.emit(self.loading_state)
        self.snapshot_changed.emit(snap)

    @Slot(object)
    def _complete(self, item):
        generation, kind, future = item
        if generation != self._generation or not self.running:
            return
        if kind == "poll":
            self._poll_future = None
        else:
            self._command_future = None
            verb, self._active_verb = self._active_verb, None
            self.busy = False
            self.busy_changed.emit(False)
        try:
            result = future.result()
            if kind == "poll":
                self._present(result)
            else:
                if (result.get("physical_action_performed", result.get("record", {}).get("physical_action_performed"))
                        is not False):
                    raise ValueError("remote command safety confirmation is missing")
                self.last_network_result = result
                if self.last_command is not None:
                    self.last_command = {**self.last_command, "outcome": "response_received"}
                    receipt = result.get("command", {}).get("record", {})
                    if isinstance(receipt.get("command_id"), str):
                        self.last_command["backend_command_id"] = receipt["command_id"]
                if verb == "replay_model":
                    self._last_replay = result
                self._refresh_after_poll = self._poll_future is not None
                self.request_refresh()
        except Exception as exc:
            from quantum_autopilot.service.client import CoreRequestError
            uncertain = isinstance(exc, CoreRequestError) and exc.uncertain
            if kind == "command" and self.last_command is not None:
                self.last_command = {**self.last_command,
                    "outcome": "uncertain" if uncertain else "rejected_or_unavailable"}
            self.ready = False
            self._capabilities = SourceCapabilities(adapter_id="authenticated-remote-core",
                note="Remote core unavailable or rejected the request; reconnect using the configured TLS identity.")
            if self._snap is not None:
                self._snap = replace(self._snap, capabilities=self._capabilities,
                    presentation_stats={"remote_core": True, "connection_unavailable": True,
                                        "last_snapshot_only": True, "mutation_outcome_uncertain": uncertain})
                self.snapshot_changed.emit(self._snap)
            self.error.emit("Remote mutation outcome is uncertain; inspect backend receipts before retrying." if uncertain
                            else "Remote core rejected the request or is unavailable. No local fallback was started.")
        if kind == "poll" and self._refresh_after_poll:
            self._refresh_after_poll = False
            self.request_refresh()

    def dispatch(self, verb, target=""):
        if verb in {"recalibrate", "isolate"}:
            actual = "recalibrate_node" if verb == "recalibrate" else "set_node_isolation"
            self.capabilities.require(actual)
            node_id = target or self._selected_node
            if not any(node.id == node_id for node in self._snap.topology.nodes):
                raise ValueError("selected remote component is unavailable")
            payload = {"node_id": node_id}
            if verb == "isolate":
                payload["isolated"] = True
            return self.dispatch(actual, json.dumps(payload))
        self.capabilities.require(verb)
        if verb in {"select_node", "select_link"}:
            inventory = self._snap.topology.nodes if verb == "select_node" else self._snap.topology.links
            if not any(item.id == target for item in inventory):
                raise ValueError("selected remote component is unavailable")
            if verb == "select_node":
                self._selected_node = target
                mapper = _RemotePresentation(self._raw, target)
                self._snap = replace(self._snap, node=mapper._node(self._raw["topology"], self._raw.get("events", []), self._snap.live))
                self.snapshot_changed.emit(self._snap)
            return True
        if verb == "reload":
            self._static = {}
            self._static_epoch += 1
            self._refresh_after_poll = self._poll_future is not None
            return self.request_refresh()
        if verb in {"read_network_live", "read_console_commands"}:
            return self.request_refresh()
        if verb == "export_record":
            return self._schedule_command(lambda: self._export(target), verb=verb)
        if verb not in REMOTE_OPERATIONS:
            raise ValueError("this operation requires a remote endpoint that is not available")
        if not isinstance(target, str) or len(target) > 65536:
            raise ValueError("bounded remote JSON command required")
        from quantum_autopilot.security.http_boundary import strict_object
        if verb == "run_scenario" and not target:
            from quantum_autopilot.control_plane import DEFAULT_SCENARIO_PRESET
            payload = {"scenario_id": "remote-console-" + uuid4().hex,
                       "topology_id": self._snap.topology.id, "preset": DEFAULT_SCENARIO_PRESET}
        elif verb == "replay_model" and not target:
            payload = {"limit": 40}
        else:
            payload = strict_object(target.encode("utf-8"))
        if verb == "run_temporal_path":
            if payload.get("protocol") != "memory_single_swap_v1":
                raise ValueError("temporal action requires memory_single_swap_v1")
            verb = "run_workspace_path"
        if verb in TRACKED:
            payload.setdefault("expected_version", self._snap.topology.version)
            payload.setdefault("topology_id", self._snap.topology.id)
            if verb in {"add_network_node", "set_node_location"}:
                for name, value in (("latitude", None), ("longitude", None), ("place", "")):
                    payload.setdefault(name, value)
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
        metadata = {"verb": verb, "client_correlation_id": "remote-console-" + uuid4().hex,
                    "request_id": payload.get("request_id", payload.get("run_id")),
                    "payload_sha256": hashlib.sha256(encoded).hexdigest()}
        console_verbs = TRACKED | {"run_workspace_path", "run_lab_experiment", "configure_laboratory", "run_scenario", "replay_model"}
        return self._schedule_command(lambda: self.client.console_command(verb, payload) if verb in console_verbs
                                     else self.client.network_command(verb, payload), verb=verb, metadata=metadata)

    def _schedule_command(self, callback, *, verb, metadata=None):
        if not self.running or self._command_future is not None:
            return False
        self.busy = True
        self._active_verb = verb
        self.last_command = metadata
        self.busy_changed.emit(True)
        generation = self._generation
        self._command_future = self._command_executor.submit(callback)
        self._command_future.add_done_callback(lambda future: self._notify_completion(generation, "command", future))
        return True

    def _export(self, scope):
        # Evidence is generated/authorized/audited by the core. Saving the
        # downloaded JSON is a user-visible desktop file operation only.
        if not isinstance(scope, str) or len(scope) > 32768:
            raise ValueError("bounded evidence export request required")
        path = None
        if scope.lstrip().startswith("{"):
            from quantum_autopilot.security.http_boundary import strict_object
            from .evidence_destination import export_destination
            request = strict_object(scope.encode("utf-8"))
            path = export_destination(scope, Path())
            scope = request["scope"]
        if not scope.strip() or len(scope) > 128:
            raise ValueError("bounded evidence scope required")
        result = self.client.console_evidence(scope)
        if (result.get("schema") != "orion.console.remote-evidence.v1"
                or result.get("scope") != scope or result.get("physical_action_performed") is not False
                or not isinstance(result.get("evidence"), dict)):
            raise ValueError("remote evidence response is invalid")
        canonical = json.dumps(result["evidence"], sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
        if result.get("record_sha256") != hashlib.sha256(canonical).hexdigest():
            raise ValueError("remote evidence digest is invalid")
        if path is None:
            self.export_directory.mkdir(parents=True, exist_ok=True)
            path = self.export_directory / ("remote-evidence-" + uuid4().hex + ".json")
        with path.open("x", encoding="utf-8") as handle:
            handle.write(json.dumps(result, sort_keys=True, indent=2, allow_nan=False))
        self.export_completed.emit(str(path))
        return dict(result, physical_action_performed=False)

    def select_node(self, target):
        return self.dispatch("select_node", target)

    def select_link(self, target):
        return self.dispatch("select_link", target)
