"""Bind the console to the in-process AssuranceDashboardService.

This is the only module that knows both worlds. It calls the same application
service the loopback API serves, and it maps those payloads onto
`console.contract.Snapshot`. No screen ever sees a dict.

Mapping rules that are not negotiable, because the repository's evidence
boundary depends on them:

* Optical, QKD and Bell lanes stay separate. A sandbox link carries a
  simulated nominal pair fidelity; it does NOT carry QBER or SKR, so those
  fields render as "not in this lane" rather than borrowing a QKD number.
* Predictions render as intervals. `predicted_fidelity ± uncertainty` becomes
  a `Metric.uncertainty`, never a rounded point value.
* Absence is stated. Every section degrades to an explicit reason string when
  the underlying source is unconfigured or fails validation.
* Nothing here performs a physical action. The command methods run shadow
  cycles or write files, and say so.

Live network (docs/product/orion_q_live_network_design.md). When the service
carries a network engine, `Snapshot.live` is the engine's last published state
and the topology strings are formatted from it. The same provider class serves
normal operation and the demonstration; the only difference is the service it
is given (`demo_service(...)`: own journal, reference topology, simulated INPUT).

Threads of a started provider:

* UI thread — owns the Qt objects, publishes snapshots, never steps the engine;
* `console-service` worker (one) — serialises snapshot builds and commands;
* `console-network-step` daemon — calls `service.network_step()` once per
  network second, and nothing else. It is held while a full recalibration runs
  (that call drives its own ticks) and joined by `stop()`.
* A lazy local process evaluates the unchanged SO(3) AlphaBeta baseline for
  those workers. It owns only numerical state; the service, supervisor, model
  admission and command journal stay in the parent process.

While a command occupies the worker, the poll timer still publishes the
engine's state (`network_live_state()` is lock-free), so the phases of a
recalibration are visible while it runs.
"""
from __future__ import annotations

import json
import hashlib
import math
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager, nullcontext
from contextvars import copy_context
from dataclasses import dataclass, replace
from functools import wraps
from uuid import uuid4
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from PySide6.QtCore import QTimer, Signal, Slot

from .contract import (
    LINK_STATUS_TONE, LINK_STATUSES_OUT_OF_GRAPH, SEVERITY_TONE,
    Alert, Artefact, Boot, Chart, ClaimCheck, Event, KV, KeySession, Keys, Ledger,
    LedgerEntry, Link, LiveAdmission, LiveCalibration, LiveCalibrationLink, LiveController,
    LiveDecision, LiveDemand, LiveDetector, LiveDomainNote, LiveEvent, LiveHistory, LiveLink,
    LiveModel, LiveNode, LiveQkd, LiveView, LogLine, Meter, Metric, Node, NodeDetail, Role,
    Series, Snapshot, Stage, Telemetry, Topology,
)
from .providers import DataProvider

_UNSPECIFIED_IDENTITY = object()


def _guarded(action=None, *, batch=False, deferred=False):
    """UI view caches and worker threads retain the backend-authenticated identity."""
    def decorate(method):
        @wraps(method)
        def call(self, *args, **kwargs):
            if deferred and self._executor is not None:
                # Presentation admission only. The queued worker enters the
                # captured identity scope and authorizes before any operation.
                return method(self, *args, **kwargs)
            with self._identity_context():
                audit = self.security.audit.batch() if batch and self.security is not None else nullcontext()
                with audit:
                    if action:
                        self._authorize(action)
                    return method(self, *args, **kwargs)
        return call
    return decorate


@dataclass(frozen=True)
class _Publication:
    """One private presentation photograph, delivered after its worker's audit.

    These counters describe publication order, never the scientific clock. In
    particular, a newer photograph can legitimately restart the network tick.
    """
    epoch: int
    sequence: int
    snapshot: Snapshot
    inputs: tuple | None


def _command_action(verb):
    from quantum_autopilot.security.service_boundary import CONFIGURE_VERBS, METHOD_ACTIONS
    if verb in {"select_node", "select_link", "reload"}:
        return "network.read"
    if verb == 'provision_satellite_session':
        return 'security.manage'
    if verb in {"export_record", "export_technology_events"}:
        return "evidence.export"
    if verb in CONFIGURE_VERBS or verb == "configure_simulated_link":
        return "network.configure"
    if verb in {"replay_model", "run_scenario", "run_temporal_path", "run_temporal_campaign", "recalibrate"}:
        return "simulation.run"
    return METHOD_ACTIONS.get(verb, "network.operate")

ACTION_TONE = {"SWAP": "green", "PURIFY": "violet", "WAIT": "amber", "HALT": "red"}
LANE_TITLE = {
    "wdm_optical_health": "WDM OPTICAL HEALTH",
    "qkd_assurance": "QKD ASSURANCE",
    "bell_fidelity_control": "BELL FIDELITY CONTROL",
}


def _clock(iso: str | None = None) -> str:
    if not iso:
        return datetime.now(timezone.utc).strftime("%H:%M:%S")
    try:
        return datetime.fromisoformat(iso).strftime("%H:%M:%S")
    except ValueError:
        return iso[11:19] or iso


def _short(digest: str | None, width: int = 12) -> str:
    return (digest or "—")[:width]


def _fmt(value: Any, spec: str = ".4f") -> str:
    if value is None:
        return "—"
    try:
        return format(float(value), spec)
    except (TypeError, ValueError):
        return str(value)


# ------------------------------------------------------------- live network
LIVE_POLL_MS = 1000            # snapshot cadence when the service carries a network engine
COMMAND_LIVE_POLL_MS = 200     # live-state cadence while a command holds the worker (recalibration phases)
DEFAULT_POLL_MS = 4000         # cadence of a service without one
STEP_PERIOD_S = 1.0            # one network second per wall-clock second
WAVE_RATE_HZ = 20.0            # schema value of the `LinkLive.wave` sample rate (design §2.2 step 5); the
                               # running engine's own figure (`LiveState.policy["wave_rate_hz"]`) replaces it
MAX_LIVE_LEDGER_ENTRIES = 120

#: Tracked topology verbs of the live network (REQUESTED -> RUNNING -> CONFIRMED | UNKNOWN).
LIVE_TRACKED_VERBS = ("add_network_node", "set_node_location", "set_node_isolation", "set_link_active",
                      "recalibrate_node")
#: Untracked engine verbs, executed through `service.network_command`.
LIVE_ENGINE_VERBS = ("define_pair", "remove_pair", "request_pair", "purify_pair", "consume_pair",
                     "set_model_archive", "inject_input_event", "set_ml_control", "configure_network_input",
                     "configure_technology", "configure_technology_source", "export_technology_events",
                     "save_communicator", "configure_communicator_connection", "provision_satellite_session",
                     "register_fpga_device", "configure_fpga_device", "set_fpga_connection", "inject_fpga_fault")
#: Fields a tracked request may leave out; the provider fills them before validation.
_LOCATION_DEFAULTS = {"latitude": None, "longitude": None, "place": ""}
#: Topology-store event kinds shown in the Events list and the journal.
TOPOLOGY_EVENT_KINDS = frozenset({
    "simulated_node_added", "simulated_link_created", "simulated_link_updated",
    "simulated_node_recalibrated", "simulated_node_isolated",
    "network_node_added", "node_location_set", "node_isolated", "node_restored",
    "link_activated", "link_deactivated", "node_recalibrated"})
#: Engine events that restate a topology-store record already listed with its hash.
_ENGINE_TOPOLOGY_MIRRORS = frozenset({
    "node_added", "node_isolated", "node_restored", "node_location_set", "fibre_added",
    "fibre_enabled", "fibre_disabled", "fibre_removed"})
_NODE_HEALTH = {"up": "up", "degraded": "degraded", "calibrating": "degraded", "down": "down", "isolated": "down"}
_DECISION_TONE = {"reroute": "green", "no_route": "red", "calibration": "violet", "detector": "amber"}
_CALIBRATION_FIELDS = ("qber_before", "qber_after", "sync_residual_ps_before", "sync_residual_ps_after",
                       "signal_intensity", "decoy_intensity", "key_rate_hz_after")


def _number(value: Any) -> float | None:
    """A finite backend number, or None. Never a default."""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return None
    return float(value)


def _numbers(values: Any) -> tuple:
    return tuple(_number(item) for item in (values or ()))


def _text_or_none(value: Any) -> str | None:
    return value if isinstance(value, str) else None


def format_fidelity(value: float | None) -> str:
    return "—" if value is None else f"{value:.4f}"


def format_qber(value: float | None) -> str:
    return "—" if value is None else f"{value * 100:.2f} %"


def format_rate(value: float | None) -> str:
    """Secret key rate in bit/s with an SI prefix; the number itself is the backend's."""
    if value is None:
        return "—"
    if value >= 1e6:
        return f"{value / 1e6:.3f} Mbit/s"
    if value >= 1e3:
        return f"{value / 1e3:.2f} kbit/s"
    return f"{value:.1f} bit/s"


def _display_provenance(link_input: str) -> str:
    return {"simulated": "simulated", "measured": "measured"}.get(link_input, "assumed")


def _live_link(item: Mapping[str, Any], configured: Mapping[str, Any],
               wave_rate_hz: float = WAVE_RATE_HZ) -> LiveLink:
    history, detector, qkd = (item.get(name) or {} for name in ("history", "detector", "qkd"))
    provenance = dict(item.get("provenance") or {})
    controllers = item.get("controllers") or {}
    admission = item.get("classical_admission")
    domain = provenance.get("domain") if isinstance(provenance.get("domain"), Mapping) else {}
    link_input = str(provenance.get("input", "none"))
    status = str(item.get("status", "no_input"))
    link_id = str(item.get("link_id", ""))
    active = configured.get(link_id)
    return LiveLink(
        id=link_id, a=str(item.get("a", "")), b=str(item.get("b", "")),
        distance_km=_number(item.get("distance_km")), status=status,
        configured_active=active if isinstance(active, bool) else None,
        effective=status not in LINK_STATUSES_OUT_OF_GRAPH and not (
            provenance.get("channels", {}).get("source") not in (None, "none") and
            provenance.get("channels", {}).get("status") != "received") and not (
            provenance.get("channels") and provenance["channels"].get("configuration") is None),
        qber=_number(item.get("qber")), fidelity=_number(item.get("fidelity")),
        skr_bps=_number(item.get("skr_bps")), sifted_rate_hz=_number(item.get("sifted_rate_hz")),
        sync_residual_ps=_number(item.get("sync_residual_ps")),
        gate_efficiency=_number(item.get("gate_efficiency")),
        polarisation_residual_mrad=_number(item.get("polarisation_residual_mrad")),
        stability=_number(item.get("stability")),
        wave=tuple(float(sample) for sample in (item.get("wave") or ()) if _number(sample) is not None),
        wave_rate_hz=wave_rate_hz,
        history=LiveHistory(_numbers(history.get("t_s")), _numbers(history.get("qber")),
                            _numbers(history.get("fidelity")), _numbers(history.get("skr_bps"))),
        detector=LiveDetector(
            _text_or_none(detector.get("label")), _number(detector.get("attack_probability")),
            detector.get("alarm") if isinstance(detector.get("alarm"), bool) else None,
            detector.get("baseline_alarm") if isinstance(detector.get("baseline_alarm"), bool) else None,
            _text_or_none(provenance.get("detector_state")),
            provenance.get("detector_in_contract") if isinstance(provenance.get("detector_in_contract"), bool)
            else None),
        qkd=LiveQkd(
            str(qkd.get("protocol", "decoy_state_bb84")), _number(qkd.get("signal_intensity")),
            _number(qkd.get("decoy_intensity")), _number(qkd.get("clock_rate_hz")),
            _number(qkd.get("signal_gain")), _number(qkd.get("decoy_gain")),
            _number(qkd.get("key_pool_bits")), _number(qkd.get("discarded_bits")),
            _text_or_none(qkd.get("last_action"))),
        controllers=tuple(
            LiveController(str(role), str(controller), _text_or_none(controllers.get(f"{role}.model_state")))
            for role, controller in controllers.items() if not str(role).endswith(".model_state")),
        classical_admission=None if not isinstance(admission, Mapping) else LiveAdmission(
            _number(admission.get("level")), str(admission.get("controller_id", "")),
            _text_or_none(admission.get("model_state")),
            admission.get("in_contract") if isinstance(admission.get("in_contract"), bool) else None,
            admission.get("fallback_used") if isinstance(admission.get("fallback_used"), bool) else None,
            _number(admission.get("backlog_bits")), str(admission.get("reason", "")),
            _text_or_none(admission.get("learned_model")), _text_or_none(admission.get("learned_model_reason"))),
        input=link_input, display_provenance=_display_provenance(link_input),
        status_reasons=tuple(str(reason) for reason in provenance.get("status_reasons") or ()),
        domain_notes=tuple(
            LiveDomainNote(str(role), note.get("in_domain") if isinstance(note.get("in_domain"), bool) else None,
                           str(note.get("detail", "")))
            for role, note in domain.items() if isinstance(note, Mapping)),
        provenance=provenance)


def _live_calibration(item: Mapping[str, Any] | None) -> LiveCalibration | None:
    if not isinstance(item, Mapping):
        return None
    links = item.get("links") if isinstance(item.get("links"), Mapping) else {}
    return LiveCalibration(
        calibration_id=str(item.get("calibration_id", "")), status=str(item.get("status", "running")),
        phase=str(item.get("phase", "queued")), progress=_number(item.get("progress")),
        started_at_tick=item.get("started_at_tick") if type(item.get("started_at_tick")) is int else None,
        completed_at=_text_or_none(item.get("completed_at_utc")), duration_s=_number(item.get("duration_s")),
        links=tuple(LiveCalibrationLink(
            str(link_id), str(record.get("status", "")), *(_number(record.get(name)) for name in _CALIBRATION_FIELDS),
            detail=dict(record)) for link_id, record in links.items() if isinstance(record, Mapping)),
        memory_reset=item.get("memory_reset") if isinstance(item.get("memory_reset"), bool) else None,
        report_sha256=_text_or_none(item.get("report_sha256")))


def live_view_from_state(state: Mapping[str, Any], topology_payload: Mapping[str, Any] | None = None) -> LiveView:
    """Map `LiveState.to_dict()` onto the UI contract. Renames and regroups; computes nothing.

    `topology_payload` (`service.control_plane_topology()`) only supplies the operator's
    `active` flag of each fibre, which the engine folds into the link status.
    """

    raw_links = ((topology_payload or {}).get("topology", {}) or {}).get("links", ())
    configured = {str(link.get("link_id")): link.get("active") for link in raw_links}
    source = state.get("source") or {}
    # Declared thresholds of the running engine, as published; only numbers and labels are kept.
    policy = {str(name): value for name, value in (state.get("policy") or {}).items()
              if isinstance(value, str) or _number(value) is not None or (name == "ml_control_enabled" and type(value) is bool)}
    wave_rate = _number(policy.get("wave_rate_hz")) or WAVE_RATE_HZ
    return LiveView(
        tick=int(state.get("tick", 0)), network_time_s=float(state.get("network_time_s", 0.0)),
        source_kind=str(source.get("kind", "none")), source_label=str(source.get("label", "")),
        source_note=str(source.get("note", "")),
        nodes=tuple(LiveNode(
            id=str(node.get("node_id", "")), role=str(node.get("role", "")), kind=_text_or_none(node.get("kind")),
            status=str(node.get("status", "")), located=node.get("located") is True,
            latitude=_number(node.get("latitude")), longitude=_number(node.get("longitude")),
            place=_text_or_none(node.get("place")), links=tuple(str(item) for item in node.get("links") or ()),
            memory_state=_text_or_none(node.get("memory_state")),
            pairs=tuple(float(item) for item in node.get("pairs") or ()),
            temperature_c=_number(node.get("temperature_c")), calibration=_live_calibration(node.get("calibration")),
            calibration_count=node.get("calibration_count") if type(node.get("calibration_count")) is int else 0,
            routes_through=tuple(str(item) for item in node.get("routes_through") or ()),
            address=_text_or_none(node.get("address")),
            port=node.get("port") if type(node.get("port")) is int else None,
            communicator=dict(node.get('communicator') or {})) for node in state.get("nodes") or ()),
        links=tuple(_live_link(link, configured, wave_rate) for link in state.get("links") or ()),
        demands=tuple(LiveDemand(
            id=str(demand.get("demand_id", "")), a=str(demand.get("a", "")), b=str(demand.get("b", "")),
            status=str(demand.get("status", "idle")), route=tuple(str(item) for item in demand.get("route") or ()),
            route_links=tuple(str(item) for item in demand.get("route_links") or ()),
            fidelity=_number(demand.get("fidelity")), success_rate=_number(demand.get("success_rate")),
            policy_id=_text_or_none(demand.get("policy_id")), in_contract=demand.get("in_contract") is True,
            fallback_used=demand.get("fallback_used") is True, reason=str(demand.get("reason", "")),
            changed_at_tick=demand.get("changed_at_tick") if type(demand.get("changed_at_tick")) is int else None,
            min_fidelity=_number(demand.get("min_fidelity")),
            pair=dict(demand["pair"]) if isinstance(demand.get("pair"), Mapping) else None,
            detail=dict(demand.get("detail") or {})) for demand in state.get("demands") or ()),
        decisions=tuple(LiveDecision(
            id=str(item.get("decision_id", "")), tick=int(item.get("tick", 0)), at=str(item.get("at_utc", "")),
            kind=str(item.get("kind", "")), controller_id=str(item.get("controller_id", "")),
            model_state=str(item.get("model_state", "")), subject=str(item.get("subject", "")),
            summary=str(item.get("summary", "")), outcome=str(item.get("outcome", "")),
            in_contract=item.get("in_contract") is True, fallback_used=item.get("fallback_used") is True,
            record_sha256=str(item.get("record_sha256", "")), inputs_sha256=_text_or_none(item.get("inputs_sha256")))
            for item in state.get("decisions") or ()),
        models=tuple(LiveModel(
            id=str(item.get("model_id", "")), role=str(item.get("role", "")), state=str(item.get("state", "")),
            reason=str(item.get("reason", "")), archive_sha256=_text_or_none(item.get("archive_sha256")),
            in_contract=item.get("in_contract") if isinstance(item.get("in_contract"), bool) else None,
            inferences=item.get("inferences") if type(item.get("inferences")) is int else 0,
            faults=item.get("faults") if type(item.get("faults")) is int else 0,
            last_latency_ms=_number(item.get("last_latency_ms"))) for item in state.get("models") or ()),
        events=tuple(LiveEvent(
            id=str(item.get("event_id", "")), tick=int(item.get("tick", 0)), at=str(item.get("at_utc", "")),
            kind=str(item.get("kind", "")), subject=str(item.get("subject", "")),
            severity=str(item.get("severity", "info")), message=str(item.get("message", "")),
            source=str(item.get("source", "engine"))) for item in state.get("events") or ()),
        at=str(state.get("at_utc", "")),
        topology_version=state.get("topology_version") if type(state.get("topology_version")) is int else None,
        source_seed=source.get("seed") if type(source.get("seed")) is int else None,
        source_scenario=_text_or_none(source.get("scenario")),
        schema=str(state.get("schema", "orion.network-live.v1")), policy=policy,
        technologies=dict(state.get("technologies") or {}),communicators=dict(state.get('communicators') or {}),
        satellites=dict(state.get('satellites') or {}),
        engine_session_id=_text_or_none(state.get('engine_session_id')),
        publication_revision=state.get('publication_revision')
        if type(state.get('publication_revision')) is int else None)


def _same_live_publication(state, live):
    if live is None:
        return False
    session, revision = state.get('engine_session_id'), state.get('publication_revision')
    if session is not None and revision is not None:
        return (session, revision) == (live.engine_session_id, live.publication_revision)
    return (state.get('tick'), state.get('at_utc')) == (live.tick, live.at)


def _newer_live_publication(current, candidate):
    """Compare engine commits, including a same-source reset of network time."""
    return (current is not None and candidate is not None
            and current.engine_session_id is not None
            and current.engine_session_id == candidate.engine_session_id
            and current.publication_revision is not None and candidate.publication_revision is not None
            and current.publication_revision > candidate.publication_revision)


class ServiceProvider(DataProvider):
    """Feed the console from `AssuranceDashboardService`.

    Cost discipline: `summary()` validates the claim registry and `run_wdm_model_replay()`
    runs the model, so both are fetched once and cached. The poll timer only
    refreshes the cheap calls — topology, journal events, health and the engine's
    last published live state.

    `poll_ms=None` selects the cadence from the service: 1 s when it carries a
    network engine (live mode), 4 s otherwise.
    """

    busy_changed = Signal(bool)
    # Presentation wrappers may precheck the committed frame; this dispatcher
    # always applies fresh backend permissions again, including queued calls.
    dispatch_validate_backend_permissions = True
    error = Signal(str)
    progress = Signal(str)
    _completed = Signal(object)
    _live_completed = Signal(object)
    _selection_completed = Signal(object)

    def __init__(
        self,
        service,
        *,
        poll_ms: int | None = None,
        replay_limit: int = 40,
        state_root: str | Path | None = None,
        step_period_s: float = STEP_PERIOD_S,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.service = service
        self.security = getattr(service, "security", None)
        self._identity = self.security.current_identity() if self.security is not None else None
        # Declared by the service object itself, never by a payload.
        self.is_demo = getattr(service, "is_demo", False) is True
        self.has_network_engine = all(callable(getattr(service, name, None)) for name in (
            "network_live_state", "network_step", "network_command"))
        if poll_ms is None:
            poll_ms = LIVE_POLL_MS if self.has_network_engine else DEFAULT_POLL_MS
        if not isinstance(step_period_s, (int, float)) or step_period_s <= 0:
            raise ValueError("step_period_s must be a positive number of seconds")
        self.step_period_s = float(step_period_s)
        self._stepper: threading.Thread | None = None
        self._step_stop: threading.Event | None = None
        self._step_hold = threading.Event()     # set while a blocking recalibration drives the ticks
        self._step_error: str | None = None
        self.steps_taken = 0
        self._snap_lock = threading.Lock()
        self._last_inputs: tuple | None = None  # (health, topology payload, cycle events) of the last build
        self._build_context = threading.local()
        self._publication_epoch = 0
        self._publication_sequence = 0
        self._delivered_publication_sequence = 0
        self._read_sequence = self._committed_read_sequence = 0
        self._committed_full_read_sequence = 0
        self._boot_at: str | None = None
        self.last_network_result: dict[str, Any] | None = None
        self.replay_limit = replay_limit
        self.state_root = Path(state_root) if state_root else None
        self._selected_node: str | None = None
        self._executor = None
        self._live_executor = None
        self._live_future = None
        self._selection_executor = None
        self._selection_futures = set()
        self._selection_generation = 0
        self._selection_requested_node = None
        self._presentation_stats = {"coalesced_gui_polls": 0, "coalesced_selection_frames": 0,
                                    "coalesced_publication_frames": 0,
                                    "selection_queue_rejections": 0, "selection_jobs_pending": 0,
                                    "network_step_overruns": 0, "last_network_step_wall_ms": None,
                                    "network_step_period_s": self.step_period_s}
        self._baseline_worker = None
        self._future = None
        self._future_background = False
        self._foreground_busy = False
        self._last_full_poll_at = 0.0
        self._example = None
        self._completed.connect(self._on_completed)
        self._live_completed.connect(self._on_live_completed)
        self._selection_completed.connect(self._on_selection_completed)
        self._summary: dict[str, Any] | None = None
        self._replay: dict[str, Any] | None = None
        self._notes: list[LogLine] = []
        self._timer = QTimer(self)
        self._timer.setInterval(poll_ms)
        self._timer.timeout.connect(self._poll_presentation)
        # Runs only while a command is busy: a blocking recalibration advances several network
        # seconds per wall-clock second, and each published tick must reach the screens.
        self._live_timer = QTimer(self)
        self._live_timer.setInterval(COMMAND_LIVE_POLL_MS)
        self._live_timer.timeout.connect(self._publish_live_only)

    # ------------------------------------------------------------- lifecycle
    def _calling_identity(self):
        if self.security is None:
            return None
        if self.security.has_identity_scope():
            capture = getattr(self.security, "capture_identity_for_dispatch", None)
            return capture() if callable(capture) else self.security.current_identity()
        # A thread without ContextVar must not replace a low-role provider with
        # the administrative OS identity of the process that hosts it.
        return self._identity

    @contextmanager
    def _identity_context(self, principal=_UNSPECIFIED_IDENTITY):
        if self.security is None:
            # Explicit developer FakeService fixtures have no live backend.
            yield
        else:
            identity = self._calling_identity() if principal is _UNSPECIFIED_IDENTITY else principal
            with self.security.identity_scope(identity):
                yield

    def _authorize(self, action):
        if self.security is not None:
            from quantum_autopilot.security.service_boundary import service_resource
            return self.security.authorize(action, service_resource(self.service))
        return None

    def _security_metadata(self):
        if self.security is None:
            return None
        identity = self.security.current_identity()
        return {"subject": identity.subject, "display_name": identity.display_name,
                "roles": sorted(identity.roles), "scopes": list(identity.scopes), "source": identity.source,
                "profile": self.security.profile, "vault_mode": self.security.vault.mode,
                "physical_qualification": False}

    @_guarded("network.read", batch=True)
    def snapshot(self):
        return super().snapshot()

    @property
    def source_label(self) -> str:
        engine = getattr(self.service, "_network_engine", None)
        if engine is not None:
            kind = engine.live_state().source.kind
            if kind != "simulated":
                if kind == "measured" or self.is_demo:
                    prefix = "LOCAL DEMO WORKSPACE" if self.is_demo else "SERVICE"
                    return prefix + " · " + ("RAW ACQUISITION REPLAY" if kind == "measured" else "NO INPUT")
        return ("DEMONSTRATION · SIMULATED INPUT / REAL PIPELINE" if self.is_demo
                else "SERVICE · MIXED EVIDENCE / SIMULATION")

    @property
    @_guarded("network.read", batch=True)
    def capabilities(self):
        from .capabilities import SourceCapabilities
        health = self._safe(self.service.health, {})
        model = self._safe(self.service.wdm_model_status, {})
        ready = health.get("status") == "ok"
        operations = ["reload", "export_record"]
        if ready:
            operations += ["read_console_commands", "read_policy_decisions"]
            if health.get("laboratory_emulation_available") is True:
                operations.append("run_lab_experiment")
                operations.append("configure_laboratory")
            if health.get("policy_preparation_available") is True:
                operations += ["observe_policy_context", "review_policy_preparation", "approve_policy_proposal",
                               "activate_simulation_policy", "read_active_policy",
                               "prepare_policy_rollback", "recover_simulation_policy"]
            operations += ["select_node", "acknowledge", "stage_node", "remove_staged_node"]
            if self.has_network_engine:
                operations.append("select_link")
            if health.get("workspace_temporal_campaign_available") is True:
                operations.append("read_temporal_campaign_history")
            if health.get("simulated_inventory_editor_available") is True:
                operations.remove("stage_node")
                operations.remove("remove_staged_node")
                operations.append("add_simulated_node")
                if callable(getattr(self.service, 'apply_simulated_node_action', None)):
                    operations.append('apply_simulated_node_action')
            if health.get("simulated_link_editor_available") is True:
                operations.append("configure_simulated_link")
            if health.get("workspace_path_available") is True:
                operations.append("run_workspace_path")
                if health.get("workspace_temporal_path_available") is True:
                    operations.append("run_temporal_path")
                    from quantum_autopilot.worker_runtime import worker_supported
                    if worker_supported() and health.get("workspace_temporal_campaign_available") is True:
                        operations.append("run_temporal_campaign")
            if health.get("control_plane_sandbox_available") is True:
                operations += ["run_scenario", "recalibrate"]
            if (model.get("configured") is True and model.get("valid") is True
                    and model.get("replay_available") is True):
                operations += ["replay_model"]
            if self.has_network_engine:
                from .capabilities import DEMO_ONLY_OPERATIONS, LIVE_NETWORK_OPERATIONS
                operations += [name for name in LIVE_NETWORK_OPERATIONS
                               if callable(getattr(self.service, name, None)) or name not in LIVE_TRACKED_VERBS]
                if self.is_demo and self._network_metadata().get("source", {}).get("kind") == "simulated":
                    operations += list(DEMO_ONLY_OPERATIONS)
        if self.is_demo:
            adapter_id = "assurance-demo-service"
            note = ("Local demonstration workspace: input origin is identified in each live frame. "
                    "Only raw input observables are generated or replayed; every result, decision and command effect comes "
                    "from the same backend processing as normal operation, on the demonstration's own journal. "
                    "Commands affect local simulation only.")
        else:
            adapter_id = "assurance-local-service"
            note = ("Mixed evidence; commands affect local simulation/shadow only. Existing-policy threshold "
                    "activation requires explicit confirmation. Optional models remain OFF.")
        if 'provision_satellite_session' in operations:
            from quantum_autopilot.security.policy import permits
            from quantum_autopilot.security.service_boundary import service_resource
            principal = self.security.current_identity() if self.security is not None else None
            if principal is None or not permits(principal.roles, principal.scopes, 'security.manage', service_resource(self.service)):
                operations.remove('provision_satellite_session')
        return SourceCapabilities(
            adapter_id=adapter_id, mode="simulation" if ready else "unavailable",
            health="ready" if ready else "unavailable", operations=tuple(operations),
            limits=(("replay_cycles", self.replay_limit), ("inventory_nodes", 256 if self.has_network_engine else 32), ("inventory_links", 256)),
            note=note)

    def policy_decision_store_path(self):
        self.capabilities.require("read_policy_decisions")
        return self.service.control_journal_path

    def policy_context_source(self):
        self.capabilities.require("observe_policy_context")
        return self.service

    def policy_review_source(self):
        """Expose the existing local service only for explicitly allowed Q05 review."""
        self.capabilities.require("review_policy_preparation")
        return self.service

    def command_reconciliation_store_path(self):
        """Read-only command query boundary; does not initialize a journal."""
        self.capabilities.require("read_console_commands")
        return self.service.control_journal_path

    def temporal_campaign_store_path(self):
        """Process runner boundary; input payloads cannot choose a database."""
        self.capabilities.require("run_temporal_campaign")
        return self.service.topology_store.path

    def temporal_campaign_history_store_path(self):
        """Read-only campaign discovery does not require an executable worker."""
        self.capabilities.require("read_temporal_campaign_history")
        return self.service.topology_store.path

    def start(self) -> None:
        if self.has_network_engine:
            from ..network_engine.baseline_worker import BaselineWorker
            self._baseline_worker = BaselineWorker()
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="console-service")
        self._live_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="console-live-read")
        self._selection_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="console-selection")
        self.request_refresh()
        self._timer.start()
        self._start_stepper()

    def stop(self) -> None:
        self._timer.stop()
        self._live_timer.stop()
        with self._snap_lock:
            self._publication_epoch += 1
        stop, stepper = self._step_stop, self._stepper
        if stop is not None:
            stop.set()                      # no tick is started after this line
        invalidate = getattr(self.service, "invalidate_policy_preparation", None)
        if callable(invalidate):
            try:
                with self._identity_context():
                    invalidate()
            except PermissionError:
                # Revocation never prevents releasing threads/device sessions.
                pass
        if self._executor:
            # A command accepted behind a read must finish even if its window
            # closes. Only unneeded background reads may be cancelled.
            self._executor.shutdown(wait=True, cancel_futures=not self._foreground_busy)
            self._executor = None
        if self._live_executor:
            self._live_executor.shutdown(wait=True, cancel_futures=True)
            self._live_executor = None
        self._live_future = None
        if self._selection_executor:
            # Accepted selections retain their individual fresh permission
            # decisions/receipts even if their presentation is no longer needed.
            self._selection_executor.shutdown(wait=True, cancel_futures=False)
            self._selection_executor = None
        self._selection_futures.clear()
        self._presentation_stats["selection_jobs_pending"] = 0
        if stepper is not None:
            stepper.join(timeout=30.0)      # at most the tick that was already running
            if not stepper.is_alive():
                self._stepper = self._step_stop = None
        if self._baseline_worker is not None:
            self._baseline_worker.close()
            self._baseline_worker = None
        engine=getattr(self.service,'_network_engine',None)
        fpga = getattr(engine, "fpga", None)
        if not self.stepping and fpga is not None and callable(getattr(fpga, "close", None)):
            fpga.close()
        if engine is not None and callable(getattr(engine,'close_satellite_receivers',None)):
            engine.close_satellite_receivers()
        elif engine is not None and callable(getattr(engine,'close_ogs',None)):
            engine.close_ogs()
        self._step_hold.clear()
        self._future = None
        self._future_background = False
        self._set_busy(False)

    # ------------------------------------------------------- network stepping
    @property
    def stepping(self) -> bool:
        """True while the engine-stepping thread of this provider is alive."""
        return self._stepper is not None and self._stepper.is_alive()

    def _start_stepper(self) -> None:
        if not self.has_network_engine or self.stepping:
            return
        self._step_stop = threading.Event()
        self._stepper = threading.Thread(target=self._step_loop, args=(self._step_stop,),
                                         name="console-network-step", daemon=True)
        self._stepper.start()

    def _step_loop(self, stop: threading.Event) -> None:
        """One network second per period. The only caller of `network_step()` in the console."""
        next_at = time.monotonic()
        while not stop.is_set():
            if not self._step_hold.is_set():
                try:
                    started = time.perf_counter()
                    result = self._run_worker(self._network_step)
                    elapsed = (time.perf_counter() - started) * 1000
                    with self._snap_lock:
                        self._presentation_stats["last_network_step_wall_ms"] = elapsed
                        if elapsed > self.step_period_s * 1000:
                            self._presentation_stats["network_step_overruns"] += 1
                        if isinstance(result, Mapping):
                            for key in ("protocol_pending_events", "protocol_clock_s", "protocol_backlog_s", "pending_classical_hops"):
                                if key in result:
                                    self._presentation_stats[key] = result[key]
                    self.steps_taken += 1
                    self._step_error = None
                except Exception as exc:      # shown in the boot checks; the loop keeps its cadence
                    self._step_error = f"{type(exc).__name__}: {exc}"
            next_at += self.step_period_s
            delay = next_at - time.monotonic()
            if delay < 0:                     # a slow tick is not caught up by a burst of fast ones
                next_at, delay = time.monotonic(), 0.0
            if stop.wait(delay):
                break

    def _network_metadata(self):
        if getattr(self.service, "network_metadata_supported", False) is True:
            return self.service.network_live_state(metadata_only=True)
        return self.service.network_live_state()

    def _network_step(self):
        if getattr(self.service, "network_metadata_supported", False) is True:
            return self.service.network_step(metadata_only=True)
        return self.service.network_step()

    def _set_busy(self, busy: bool) -> None:
        if self._foreground_busy != busy:
            self._foreground_busy = busy
            if busy and self.has_network_engine and self._executor is not None:
                self._live_timer.start()
            else:
                self._live_timer.stop()
            self.busy_changed.emit(busy)

    def _run_worker(self, callback, principal=_UNSPECIFIED_IDENTITY):
        with self._identity_context(self._identity if principal is _UNSPECIFIED_IDENTITY else principal):
            if self._baseline_worker is None:
                return callback()
            from ..network_engine.baseline_worker import use_baseline_worker
            with use_baseline_worker(self._baseline_worker):
                return callback()

    def _schedule(self, callback, *, background: bool = False, label: str | None = None) -> bool:
        if self._future is not None and (background or not self._future_background):
            return False
        # The single worker serializes reads and commands. An explicit command
        # can queue behind a background read; a second command is still refused.
        self._future_background = background
        if not background:
            self._set_busy(True)
            self.progress.emit(label or "Reading and validating the local service…")
        principal = self._calling_identity()
        context = copy_context()
        def prepare_delivery():
            callback()
            # Authorization, backend reads and every authenticated audit record
            # finish on this worker before Qt receives the presentation frame.
            return self._prepare_publication() if self._snap is not None else None
        self._future = self._executor.submit(context.run, self._run_worker, prepare_delivery, principal)
        self._future.add_done_callback(self._completed.emit)
        return True

    @Slot(object)
    def _on_completed(self, future) -> None:
        # A read superseded by a queued command, or a stopped source, must not
        # publish an old frame or clear the newer command's busy state.
        if future is not self._future:
            return
        try:
            snap = future.result()
            if snap is not None:
                # The future completes only after the guarded worker has made
                # its audit batch durable. Rendering does not query services.
                self._deliver_publication(snap)
        except Exception as exc:
            self.error.emit(str(exc))
            failed = True
            if isinstance(exc, PermissionError):
                self._timer.stop()
                self._live_timer.stop()
                if self._step_stop is not None:
                    self._step_stop.set()
                failed = False  # do not retry revoked authorization in a busy loop
        else:
            failed = False
        finally:
            # Snapshot listeners can dispatch while a background read finishes.
            # Keep a newly accepted command's state until its own completion.
            if self._future is future:
                self._future = None
                self._future_background = False
                self._set_busy(False)
                if failed and self.has_network_engine and self._executor is not None:
                    # A refused command changed the command journal (UNKNOWN) and possibly the
                    # engine's events: read them now instead of at the next poll.
                    self._schedule(self.refresh, background=self._snap is not None)

    def request_refresh(self) -> None:
        if not self._executor:
            self.refresh()
        elif not self._schedule(self.refresh, background=self._snap is not None):
            with self._snap_lock:
                self._presentation_stats["coalesced_gui_polls"] += 1
            self._publish_live_only()

    def _poll_presentation(self) -> None:
        """Read live metrics each poll and static evidence at its own cadence.

        Explicit refreshes and completed commands retain the full readback.
        Coalescing affects presentation only, never scientific acquisition,
        processing or durable records.
        """
        if not self.has_network_engine or self._snap is None:
            self.request_refresh()
            return
        if time.monotonic() - self._last_full_poll_at >= DEFAULT_POLL_MS / 1000:
            if self._schedule(self.refresh, background=True):
                self._last_full_poll_at = time.monotonic()
            else:
                with self._snap_lock:
                    self._presentation_stats["coalesced_gui_polls"] += 1
        self._publish_live_only()

    def _publish_live_only(self) -> None:
        """Coalesce live delivery independently of static reads and commands."""
        if (not self.has_network_engine or self._snap is None or self._delivered_publication_sequence == 0
                or self._live_executor is None or self._live_future is not None):
            return
        command_future = self._future if self._foreground_busy else None
        if command_future is not None and command_future.done():
            return
        principal, context = self._calling_identity(), copy_context()
        self._live_future = self._live_executor.submit(
            context.run, self._run_worker,
            lambda: (command_future, self._collect_live_publication(command_future)), principal)
        self._live_future.add_done_callback(self._live_completed.emit)

    @_guarded("network.read", batch=True)
    def _collect_live_publication(self, command_future):
        # Acquisition of published backend state, permission validation and the
        # complete audit commit all run outside Qt, even during recalibration.
        epoch, read_sequence = self._begin_read()
        if getattr(self.service, "network_metadata_supported", False) is True:
            metadata = self.service.network_live_state(metadata_only=True)
            with self._snap_lock:
                current = self._snap
            if current is not None and _same_live_publication(metadata, current.live):
                return
        state = self.service.network_live_state()
        with self._snap_lock:
            snap, inputs, future = self._snap, self._last_inputs, self._future
            if snap is None or inputs is None or (command_future is not None and
                    (future is not command_future or future.done())):
                return
            if epoch != self._publication_epoch or read_sequence < self._committed_read_sequence:
                return
            if _same_live_publication(state, snap.live):
                return
        # Formatting a full topology/wave history can be expensive. Do not hold
        # the cache mutex that Qt needs for dispatch while doing that work.
        health, payload, events = inputs
        live = live_view_from_state(state, payload)
        topology = self._topology(payload, events, live)
        counts = self._counts(topology, events, health, live)
        with self._snap_lock:
            if (epoch != self._publication_epoch or read_sequence < self._committed_read_sequence
                    or self._last_inputs is not inputs or self._snap is None
                    or (command_future is not None and
                        (self._future is not command_future or command_future.done()))):
                return
            self._snap = replace(self._snap, live=live, topology=topology, at=_clock(state.get("at_utc")), counts=counts)
            self._committed_read_sequence = read_sequence
        return self._prepare_publication(refresh_capabilities=False)

    @Slot(object)
    def _on_live_completed(self, future):
        if future is not self._live_future:
            return
        try:
            command_future, snap = future.result()
            if snap is not None and (command_future is None or
                    (command_future is self._future and not command_future.done())):
                self._deliver_publication(snap)
        except Exception as exc:
            self.error.emit(str(exc))
            if isinstance(exc, PermissionError):
                self._timer.stop()
                self._live_timer.stop()
                if self._step_stop is not None:
                    self._step_stop.set()
        finally:
            self._live_future = None

    @_guarded(deferred=True)
    def dispatch(self, verb: str, target: str = "") -> bool:
        allowed = {"select_node", "select_link", "recalibrate", "isolate", "acknowledge", "set_decision_source",
                   "run_lab_experiment", "configure_laboratory",
                   "export_record", "run_scenario", "replay_model", "reload", "add_simulated_node", "configure_simulated_link", "apply_simulated_node_action", "run_workspace_path", "run_temporal_path",
                   *LIVE_TRACKED_VERBS, *LIVE_ENGINE_VERBS}
        if verb not in allowed:
            raise ValueError("unsupported console command")
        # Cheap presentation admission preserves immediate refusal of invalid
        # requests, without querying the SDK or authenticating on the Qt thread.
        # Accepted jobs still repeat all backend checks with their caller.
        with self._snap_lock:
            presented = self._snap
        if self._executor and presented is None:
            return False
        if presented is not None:
            if self._executor:
                presented.capabilities.require(verb)
            if verb == "select_node" and not any(node.id == target for node in presented.topology.nodes):
                raise ValueError(f"node unavailable: {target}")
            if verb == "select_link" and not any(target in (link.id, link.label)
                                                  for link in presented.topology.links):
                raise ValueError(f"link unavailable: {target}")
        def command():
            # Every accepted call revalidates the captured caller and commits
            # its complete audit on a worker; no SDK/fsync runs in Qt dispatch.
            self._authorize(_command_action(verb))
            self.capabilities.require(verb)
            if verb == "select_node" and self._select_passively(target, publish=False):
                return
            handler = getattr(self, verb)
            handler() if verb == "reload" else handler(target)
        if self._executor:
            if verb in {"select_node", "select_link"}:
                return self._schedule_selection(command, verb, target)
            return self._schedule(command, label=f"Backend is executing {verb} · local simulation only…")
        command()
        if self._snap is not None:
            self.publish_snapshot()
        return True

    def _schedule_selection(self, callback, verb, target):
        if self._selection_executor is None:
            return False
        # Bounded jobs, each with its own principal. No accepted authorization
        # is dropped; only obsolete presentation frames may be coalesced.
        if len(self._selection_futures) >= 16:
            self._presentation_stats["selection_queue_rejections"] += 1
            self.error.emit("Selection queue is full; retry after pending inspections complete.")
            return False
        principal, context = self._calling_identity(), copy_context()
        self._selection_generation += 1
        generation = self._selection_generation
        if verb == "select_node":
            self._selection_requested_node = target
        def prepare():
            callback()
            return generation, self._prepare_publication() if self._snap is not None else None
        future = self._selection_executor.submit(context.run, self._run_selection_worker, prepare, principal)
        self._selection_futures.add(future)
        self._presentation_stats["selection_jobs_pending"] = len(self._selection_futures)
        future.add_done_callback(self._selection_completed.emit)
        return True

    def _run_selection_worker(self, callback, principal):
        # Read-only presentation jobs can group their records, retaining every
        # fresh permission decision and making all MACs/checkpoints durable
        # before the returned frame reaches Qt. Key/device side effects never
        # use this batching context.
        with self.security.audit.batch() if self.security is not None else nullcontext():
            if self.security is not None:
                from quantum_autopilot.security.service_boundary import service_resource
                self.security.authorize("network.read", service_resource(self.service), principal=principal)
            return self._run_worker(callback, principal)

    @Slot(object)
    def _on_selection_completed(self, future):
        if future not in self._selection_futures:
            return
        self._selection_futures.discard(future)
        self._presentation_stats["selection_jobs_pending"] = len(self._selection_futures)
        try:
            generation, snap = future.result()
            if snap is not None and generation == self._selection_generation:
                self._deliver_publication(snap)
            elif snap is not None:
                self._presentation_stats["coalesced_selection_frames"] += 1
        except Exception as exc:
            self.error.emit(str(exc))
            if isinstance(exc, PermissionError):
                self._timer.stop()
                self._live_timer.stop()
                if self._step_stop is not None:
                    self._step_stop.set()

    def _presentation_frame(self, snap):
        """Format only data already authorized and committed by the worker."""
        # An old photograph must never borrow a newer topology's inputs. Legacy
        # direct fixtures without an envelope keep their existing node detail.
        inputs = snap.inputs if isinstance(snap, _Publication) else None
        snap = snap.snapshot if isinstance(snap, _Publication) else snap
        with self._snap_lock:
            selected = self._selection_requested_node
            stats = dict(self._presentation_stats)
        nodes = ((inputs[1].get("topology", {}) or {}).get("nodes", ())
                 if inputs is not None else ())
        if selected and snap.node.id != selected and any(node.get("node_id") == selected for node in nodes):
            snap = replace(snap, node=self._node(inputs[1], inputs[2], snap.live, selected_node_id=selected))
        return replace(snap, presentation_stats=stats)

    def _deliver_publication(self, publication):
        """One Qt admission point for completed service/live/selection workers."""
        if not isinstance(publication, _Publication):
            raise TypeError("a prepared publication is required")
        with self._snap_lock:
            if (publication.epoch != self._publication_epoch or
                    publication.sequence < self._delivered_publication_sequence):
                self._presentation_stats["coalesced_publication_frames"] += 1
                return False
            self._delivered_publication_sequence = publication.sequence
        self.snapshot_changed.emit(self._presentation_frame(publication))
        return True

    @_guarded("network.read", batch=True)
    def _prepare_publication(self, *, refresh_capabilities=True):
        capabilities = self.capabilities if refresh_capabilities else None
        with self._snap_lock:               # a build finishing on the worker must not be overwritten
            if self._snap is None:
                raise RuntimeError("provider has no snapshot yet")
            self._snap = snap = replace(self._snap, capabilities=capabilities or self._snap.capabilities,
                                        presentation_stats=dict(self._presentation_stats))
            self._publication_sequence += 1
            return _Publication(self._publication_epoch, self._publication_sequence, snap, self._last_inputs)

    def publish_snapshot(self):
        # Explicit synchronous callers retain the same authorization boundary.
        # The streaming completion path prepares this frame on its worker.
        snap = self._prepare_publication()
        self._deliver_publication(snap)

    def _begin_read(self):
        with self._snap_lock:
            self._read_sequence += 1
            return self._publication_epoch, self._read_sequence

    def refresh(self) -> None:
        epoch, read_sequence = self._begin_read()
        # Keep build()'s public full-Snapshot contract and existing audit guard.
        # Its inputs stay local until the complete read/audit succeeds, including
        # wrappers used by callers to profile or pause that public operation.
        self._build_context.capture = True
        self._build_context.inputs = None
        try:
            snap = self.build()
            inputs = self._build_context.inputs
        finally:
            self._build_context.capture = False
            self._build_context.inputs = None
        while True:
            with self._snap_lock:
                # A live-only frame cannot discard a full command receipt.
                if epoch != self._publication_epoch or read_sequence < self._committed_full_read_sequence:
                    self._presentation_stats["coalesced_publication_frames"] += 1
                    return
                current, cached_inputs, selected = self._snap, self._last_inputs, self._selected_node
                if (current is not None and current.live is not None and snap.live is not None
                        and current.live.engine_session_id is not None and snap.live.engine_session_id is not None
                        and current.live.engine_session_id != snap.live.engine_session_id
                        and read_sequence < self._committed_read_sequence):
                    # A newer lightweight read saw a replaced engine. Its
                    # metrics cannot be combined with the previous engine's
                    # inventory photograph; the next full read reconciles it.
                    self._presentation_stats["coalesced_publication_frames"] += 1
                    return
            commit_inputs = inputs if inputs is not None else cached_inputs
            candidate = snap
            # Conversely, an older full batch must not roll back already shown
            # engine metrics. Merge its fresh inventory/ledger with the newer
            # causally identified engine publication, outside the Qt cache lock.
            if current is not None and _newer_live_publication(current.live, snap.live):
                live = current.live
                if commit_inputs is not None:
                    health, payload, events = commit_inputs
                    topology = self._topology(payload, events, live)
                    candidate = replace(candidate, live=live, topology=topology, at=_clock(live.at),
                                        counts=self._counts(topology, events, health, live),
                                        node=self._node(payload, events, live, selected_node_id=selected))
            elif selected and snap.node.id != selected and commit_inputs is not None:
                candidate = replace(candidate, node=self._node(commit_inputs[1], commit_inputs[2], snap.live,
                                                               selected_node_id=selected))
            with self._snap_lock:
                if self._snap is not current or self._last_inputs is not cached_inputs or self._selected_node != selected:
                    continue
                if epoch != self._publication_epoch or read_sequence < self._committed_full_read_sequence:
                    self._presentation_stats["coalesced_publication_frames"] += 1
                    return
                self._snap, self._last_inputs = candidate, commit_inputs
                self._committed_full_read_sequence = read_sequence
                self._committed_read_sequence = max(self._committed_read_sequence, read_sequence)
                break
        if self._executor is None:
            self.publish_snapshot()

    def _select_passively(self, node_id: str, *, publish: bool = True) -> bool:
        """Selection is view state: show the node's detail from the records already read.

        No backend call is made and the provider does not become busy, so a click on a node
        never disables the screens. The node detail is rebuilt from the last topology payload,
        the last cycle events and the engine state of the current snapshot — the same
        `_node` mapping a full build uses. Returns False when nothing was read yet (the
        caller then takes the ordinary command path)."""
        with self._snap_lock:
            snap, inputs = self._snap, self._last_inputs
            if snap is None or inputs is None:
                return False
            _health, payload, events = inputs
            ids = {n.get("node_id") for n in (payload.get("topology", {}) or {}).get("nodes", [])}
            if node_id not in ids:
                raise ValueError(f"node unavailable: {node_id}")
            self._selected_node = node_id
            self._snap = replace(snap, node=self._node(payload, events, snap.live))
        if publish:
            self.publish_snapshot()
        return True

    @_guarded("network.read", batch=True)
    def select_link(self, link_id: str) -> None:
        # Link selection changes only the shared presentation selection. The
        # dispatch worker has already authenticated network.read for this view.
        with self._snap_lock:
            if self._snap is None or self._snap.live is None or self._snap.live.link(link_id) is None:
                raise ValueError(f"link unavailable: {link_id}")

    def select_node(self, node_id: str) -> None:
        payload = self.service.control_plane_topology()
        ids = {n["node_id"] for n in payload.get("topology", {}).get("nodes", [])}
        if node_id not in ids:
            raise ValueError(f"node unavailable: {node_id}")
        self._selected_node = node_id
        self.refresh()

    # ---------------------------------------------------------------- cached
    def summary(self) -> dict[str, Any] | None:
        if self._summary is None:
            try:
                self._summary = self.service.summary()
                integrity = self._summary.get("integrity", {})
                if not integrity.get("valid") or not integrity.get("grid_source_artifact_verified", True):
                    raise ValueError("published evidence failed integrity validation")
            except Exception as exc:  # integrity failure is a legitimate UI state
                self._summary = {"__error__": str(exc)}
        return None if "__error__" in self._summary else self._summary

    def summary_error(self) -> str | None:
        self.summary()
        return (self._summary or {}).get("__error__")

    def replay(self) -> dict[str, Any] | None:
        if self._replay is None:
            try:
                self._replay = self.service.run_wdm_model_replay({"limit": self.replay_limit})
            except Exception as exc:
                self._replay = {"__error__": str(exc)}
        return None if "__error__" in self._replay else self._replay

    def replay_error(self) -> str | None:
        self.replay()
        return (self._replay or {}).get("__error__")

    def reload(self) -> None:
        """Drop the cached expensive payloads and rebuild."""

        self._summary = None
        self._replay = None
        self._example = None
        self.refresh()

    # ------------------------------------------------------------- assembly
    @_guarded("network.read", batch=True)
    def build(self) -> Snapshot:
        first_read = self._snap is None
        def progress(completed, message):
            if first_read:
                self.loading_progress.emit({'completed': completed, 'total': 5, 'message': message})
        health = self._safe(self.service.health, {})
        progress(1, "Service health received; reading topology and event records…")
        topology_payload = self._safe(self.service.control_plane_topology, {})
        events = self._safe(lambda: self.service.control_plane_events(limit=100), {}).get("events", [])
        model = self._safe(self.service.wdm_model_status, {})
        lanes = self._safe(self.service.evidence_lanes, {})
        progress(2, "Topology, model metadata and evidence lanes received…")
        summary = self.summary()
        live = self._live(topology_payload)
        progress(3, "Evidence registry and live protocol state read; preparing presentation…")
        inputs = (health, topology_payload, events)

        topology = self._topology(topology_payload, events, live)
        node = self._node(topology_payload, events, live)
        grid = (summary or {}).get("operating_grid", {})

        ledger = self._ledger(events, lanes, summary, self._safety_events(), live, topology_payload)
        ledger.commands, ledger.commands_note = self._command_history()
        security = self._security_metadata()
        progress(4, "Identity metadata and journal read; validating the first snapshot…")
        snap = Snapshot(
            at=_clock(health.get("generated_at")),
            operator=(security.get("display_name") or security["subject"]) if security else (
                "DEMONSTRATION" if self.is_demo else "LOCAL SANDBOX"),
            campaign=grid.get("grid_id", "—"),
            counts=self._counts(topology, events, health, live),
            boot=self._boot(health, summary, lanes, live, topology),
            topology=topology,
            telemetry=self._telemetry(model),
            keys=self._keys(summary, grid),
            node=node,
            ledger=ledger,
            input_kind="demo" if self.is_demo else "service",
            capabilities=self.capabilities,
            workspace_path=self._workspace_path_result(),
            laboratory=self._safe(getattr(self.service, "laboratory_state", lambda: None), None),
            live=live,
            security=security,
            presentation_stats=dict(self._presentation_stats),
        )
        if getattr(self._build_context, "capture", False):
            self._build_context.inputs = inputs
        return snap

    def _live(self, topology_payload: Mapping[str, Any]) -> LiveView | None:
        """The engine's last published state, or None for a service without an engine.

        Reading never advances the network; only the stepping thread does."""
        if not self.has_network_engine:
            return None
        try:
            return live_view_from_state(self.service.network_live_state(), topology_payload)
        except Exception as exc:
            self._step_error = self._step_error or f"live state unavailable · {type(exc).__name__}: {exc}"
            return None

    def _command_history(self):
        from .contract import CommandView
        from quantum_autopilot.control_plane.console_commands import read_commands
        try:
            records = read_commands(self.service.controller_runtime.journal.path)
            views = tuple(CommandView(item['record']['command_id'], item['record']['verb'],
                item['record']['events'][-1]['state'], item['record']['events'][0]['at_utc'],
                json.dumps(item, indent=2, ensure_ascii=False)) for item in records)
            return views, "Latest 50 local inventory commands · hash checked, not authenticated. REQUESTED/RUNNING after restart are unresolved, not proof of activity."
        except Exception as exc:
            return (), f"Command history unavailable · {type(exc).__name__}; no records accepted."

    def _workspace_path_result(self):
        from .contract import WorkspacePathResult
        from quantum_autopilot.control_plane.topology import _sha256
        reader = getattr(self.service, "workspace_path_result", None)
        if reader is None:
            return None
        result = reader(getattr(self, "_path_run_id", None))
        if result is None:
            return None
        record = result["record"]
        request, values = record["request"], record["result"]
        return WorkspacePathResult(request["run_id"], request["expected_version"], tuple(values["route"]),
            values["conditional_fidelity"], values["target_fidelity"], values["conditional_target_met"],
            record["source_topology_sha256"], result["record_sha256"],
            protocol=request["protocol"], request_hash=_sha256(request),
            outcome=values.get("outcome", "conditional_result"), elapsed_s=values.get("elapsed_s"),
            ticks=values.get("ticks"), memory_ages_s=tuple(values.get("memory_ages_s", ())),
            generated_pairs=sum(values.get("accounting", {}).get("generated_per_link", ())),
            expired_pairs=sum(values.get("accounting", {}).get("expired_per_link", ())))

    @staticmethod
    def _safe(call, fallback):
        try:
            return call()
        except Exception:
            return fallback

    # ------------------------------------------------------------------ chrome
    def _counts(self, topology: Topology, events: list, health: Mapping[str, Any],
                live: LiveView | None = None) -> list[KV]:
        up = sum(1 for n in topology.nodes if n.health == "up")
        if live is not None:
            in_service = sum(1 for link in live.links if link.effective)
            routed = sum(1 for demand in live.demands if demand.status in ("routed", "rerouted"))
            return [
                KV(f"{up}/{len(live.nodes)} NODES UP", "", "green" if up == len(live.nodes) and up else "amber"),
                KV(f"{in_service}/{len(live.links)} FIBRES IN SERVICE", "",
                   "ink" if in_service == len(live.links) else "amber"),
                KV(f"{routed}/{len(live.demands)} DEMANDS ROUTED", "",
                   "ink" if routed == len(live.demands) else "red"),
                KV("PHYSICAL ACTUATION LOCKED", "", "red" if health.get("automation_allowed") else "amber"),
            ]
        return [
            KV(f"{up}/{len(topology.nodes)} NODES STORING", "", "green" if up else "muted"),
            KV(f"{topology.links_up} LINKS ENABLED", "", "ink"),
            KV(f"{len(events)} CYCLES JOURNALLED", "", "muted"),
            KV("PHYSICAL ACTUATION LOCKED", "", "red" if health.get("automation_allowed") else "amber"),
        ]

    # -------------------------------------------------------------------- boot
    def _boot(self, health: Mapping[str, Any], summary: Mapping[str, Any] | None, lanes: Mapping[str, Any],
              live: LiveView | None = None, topology: Topology | None = None) -> Boot:
        integrity = (summary or {}).get("integrity", {})
        checks: list[LogLine] = []
        if self._boot_at is None:           # when this source was first read; steady across polls
            self._boot_at = _clock(health.get("generated_at"))
        boot_at = self._boot_at

        def check(body: str, ok: bool | None, note: str) -> None:
            checks.append(LogLine(
                at=boot_at,
                body=body,
                status=note,
                tone="green" if ok else ("red" if ok is False else "muted"),
            ))

        check("application service", health.get("status") == "ok", str(health.get("status", "unreachable")).upper())
        check("physical actuation path", health.get("physical_read_only") is True, "LOCKED")
        check("automation", health.get("automation_allowed") is False, "DISABLED")
        check("control-plane sandbox", health.get("control_plane_sandbox_available") is True, "READY")
        check("WDM model bundle", health.get("wdm_model_bundle_configured") is True,
              "CONFIGURED" if health.get("wdm_model_bundle_configured") else "NOT CONFIGURED")
        if summary is None:
            check("claim registry", False, (self.summary_error() or "validation failed")[:48].upper())
        else:
            check("claim registry", integrity.get("valid") is True,
                  f"{integrity.get('check_count', 0)} CHECKS · {integrity.get('failed_check_count', 0)} FAILED")
        check("evidence lanes", bool(lanes.get("lane_count")), f"{lanes.get('lane_count', 0)} LANES SEPARATED")
        if self.has_network_engine:
            self._network_checks(check, live, topology)

        return Boot(
            title="QUANTUM NETWORK CONSOLE",
            title_local="OrionQ",
            subtitle=(self.source_label if self.is_demo
                      else "Local assurance and non-physical control sandbox"),
            checks=checks,
            fields=[
                KV("API VERSION", str((lanes.get("api_version") or health.get("api_version") or "—"))),
                KV("AUTOPILOT", str(health.get("autopilot_version", "—"))),
                KV("REGISTRY SHA256", _short(integrity.get("registry_sha256"), 16)),
                KV("GRID CONFIG SHA256", _short(integrity.get("grid_config_sha256"), 16)),
            ],
            session=[
                KV("SCOPE", str(health.get("local_mutation_scope", "—"))),
                KV("EVIDENCE ITEMS", str(integrity.get("evidence_item_count", "—"))),
            ],
            notice=((
                "Local demonstration workspace: input origin is identified in every live frame. "
                "Raw acquisitions come from the local simulator or an explicitly identified file replay. "
                "Routing, QKD, detection, recalibration and every command run the same backend code as "
                "normal operation, on the demonstration's own journal. Physical actuation is locked."
            ) if self.is_demo else (
                "Control scope is local simulation, shadow evaluation and assurance. "
                "Physical actuation is locked and no measured Bell-fidelity claim is made here."
            )),
            build=f"console {health.get('autopilot_version', '')}".strip(),
        )

    def _network_checks(self, check, live: LiveView | None, topology: Topology | None) -> None:
        """Login rows about the live network, from the engine's own state."""

        journal = Path(self.service.control_journal_path) if getattr(self.service, "control_journal_path", None) else None
        if journal is not None:
            exists = journal.is_file()
            check("demonstration journal" if self.is_demo else "operator journal", exists if exists else None,
                  f"{journal.parent.name}/{journal.name}".upper() if exists else "NOT CREATED YET")
        if live is None:
            check("network engine", False, (self._step_error or "no live state")[:48].upper())
            return
        # Steady text: the login rows are not rebuilt on every network second.
        check("network engine", self._step_error is None,
              ("READY · STEPPING" if self.stepping else "READY · NOT STEPPING")
              if self._step_error is None else self._step_error[:48].upper())
        kind = live.source_kind
        check("input source", True if kind != "none" else None,
              {"simulated": "SIMULATED INPUT · BACKEND ENGINE", "measured": "MEASURED FILE REPLAY · DECLARED ORIGIN",
               "none": "NO INPUT FEED · CONFIGURATION ONLY"}.get(kind, kind.upper()))
        states = [model.state for model in live.models]
        active, fallback, missing = (states.count(name) for name in ("active", "fallback", "unavailable"))
        check("model registry", True if active else None,
              f"{active} ACTIVE · {fallback} FALLBACK · {missing} BASELINE ONLY" +
              (f" · {states.count('disabled')} ML OFF" if "disabled" in states else ""))
        located = sum(1 for node in live.nodes if node.located)
        persisted = topology is not None and topology.persisted
        check("network topology", True if persisted else None,
              f"{len(live.nodes)} NODES ({located} LOCATED) · {len(live.links)} FIBRES · "
              + (f"V{live.topology_version}" if persisted else "NOT PERSISTED"))

    # ---------------------------------------------------------------- topology
    def _topology(self, payload: Mapping[str, Any], events: list, live: LiveView | None = None) -> Topology:
        topo = payload.get("topology", {}) or {}
        raw_nodes = list(topo.get("nodes", ()))
        raw_links = list(topo.get("links", ()))
        persisted = payload.get("persisted")

        nodes: list[Node] = []
        span = max(len(raw_nodes) - 1, 1)
        columns = max(1, math.ceil(math.sqrt(len(raw_nodes))))
        rows = max(1, math.ceil(len(raw_nodes) / columns))
        live_nodes = {node.id: node for node in live.nodes} if live is not None else {}
        live_links = {link.id: link for link in live.links} if live is not None else {}
        for i, item in enumerate(raw_nodes):
            role = str(item.get("role", "node"))
            telemetry = item.get("telemetry", {}) or {}
            inventory = telemetry.get("inventory", {}) or {}
            control = telemetry.get('console_control', {}) or {}
            location = telemetry.get("location") if isinstance(telemetry.get("location"), Mapping) else None
            latitude = _number(location.get("latitude")) if location else None
            longitude = _number(location.get("longitude")) if location else None
            located = latitude is not None and longitude is not None
            isolated = control.get('isolated') is True
            storing = item.get("memory_state") == "STORING"
            node_id = str(item.get("node_id", f"node-{i}"))
            engine = live_nodes.get(node_id)
            communicator=telemetry.get('communicator',{})
            network_role=communicator.get('network_role',role)
            physical_type=communicator.get('physical_type',inventory.get('kind','installation'))
            if engine is not None and physical_type=='satellite':
                latitude,longitude,located=engine.latitude,engine.longitude,engine.located
            if engine is not None:          # health is the ENGINE's node status
                state_text, health = engine.status.upper(), _NODE_HEALTH.get(engine.status, "unknown")
            else:
                state_text = 'ISOLATED' if isolated else str(item.get('memory_state', '—'))
                health = "down" if isolated else "up" if storing else "degraded"
            nodes.append(Node(
                id=node_id,
                role=f"{role.upper()} · {state_text}",
                health=health,
                x=(i % columns + .5) / columns if len(raw_nodes) > 8 else 0.5 + 0.38 * math.cos(2 * math.pi * i / len(raw_nodes)) if len(raw_nodes) > 3 or len(raw_links) > 2 else 0.10 + 0.80 * (i / span),
                y=(i // columns + .5) / rows if len(raw_nodes) > 8 else 0.5 + 0.36 * math.sin(2 * math.pi * i / len(raw_nodes)) if len(raw_nodes) > 3 or len(raw_links) > 2 else 0.5,
                kind="satellite" if physical_type=='satellite' else "core" if network_role=='core' or inventory.get("kind") == "core"
                else "repeater" if physical_type=='quantum_repeater' or network_role=='repeater' else "node",
                latitude=latitude if located else None, longitude=longitude if located else None,
                connection_address=inventory.get("address"), connection_port=inventory.get("port"),
                inventory_origin=inventory.get("origin"),
                isolated=isolated, calibration_count=control.get('calibration_count', 0),
                located=located, place=engine.place if engine is not None and physical_type=='satellite' else _text_or_none((location or {}).get("place")) if located else None,
                status=engine.status if engine is not None else "",
                network_role=network_role,physical_type=physical_type,display_id=communicator.get('device_id',node_id),
                name=communicator.get('name',''),category=communicator.get('category',''),
            ))

        links: list[Link] = []
        active = 0
        for item in raw_links:
            is_active = bool(item.get("active", True))
            active += int(is_active)
            engine = live_links.get(str(item.get("link_id", "")))
            if engine is not None:
                # Live values are the engine's estimates for this fibre; a value that was not
                # produced renders "—". The configured nominal pair fidelity stays in its own field.
                values = dict(
                    fidelity=format_fidelity(engine.fidelity), qber=format_qber(engine.qber),
                    skr=format_rate(engine.skr_bps),
                    kind="fibre" if engine.effective and engine.status != "down" else "fault",
                    weight=2.0 if engine.effective else 1.0, tone=LINK_STATUS_TONE.get(engine.status, "muted"),
                    provenance=engine.display_provenance, status=engine.status, effective=engine.effective,
                    stability=engine.stability)
            else:
                values = dict(
                    fidelity=_fmt(item.get("nominal_pair_fidelity")), qber="not in this lane",
                    skr="not in this lane", kind="fibre" if is_active else "fault",
                    weight=2.0 if is_active else 1.0, tone="amber" if is_active else "red",
                    provenance="simulated")
            links.append(Link(
                a=str(item.get("left_node_id")),
                b=str(item.get("right_node_id")),
                label=f"{item.get('link_id', '—')} · {_fmt(item.get('distance_km'), '.1f')} km",
                id=str(item.get("link_id", "")), distance_km=item.get("distance_km"),
                nominal_pair_fidelity=item.get("nominal_pair_fidelity"), configured_active=is_active,
                channels=item.get("channels"),
                **values,
            ))

        metrics = [
            Metric("TOPOLOGY VERSION", str(topo.get("topology_version", "—")),
                   provenance="derived", note="increments on every persisted sandbox mutation"),
            Metric("NODES", str(len(raw_nodes)), provenance="derived"),
            Metric("LINKS ENABLED", f"{active}/{len(raw_links)}", provenance="derived", note="configuration flag, not observed health"),
            Metric("PERSISTED", "yes" if persisted else "no",
                   provenance="runtime", tone="ink" if persisted else "amber",
                   note="an unpersisted blueprint has not touched the journal"),
        ]
        if live is not None:
            in_service = sum(1 for link in live.links if link.effective)
            routed = sum(1 for demand in live.demands if demand.status in ("routed", "rerouted"))
            provenance = "simulated" if live.source_kind == "simulated" else \
                "measured" if live.source_kind == "measured" else "runtime"
            metrics += [
                Metric("FIBRES IN SERVICE", f"{in_service}/{len(live.links)}", provenance="runtime",
                       tone="ink" if in_service == len(live.links) else "amber",
                       note="in the effective routing graph: enabled, no end isolated or calibrating, no attack alarm"),
                Metric("DEMANDS ROUTED", f"{routed}/{len(live.demands)}", provenance="runtime",
                       tone="ink" if routed == len(live.demands) else "red",
                       note="end-to-end demands with a route planned by the backend router"),
                Metric("INPUT", live.source_kind.upper(), provenance=provenance,
                       tone="amber" if live.source_kind == "none" else "ink", note=live.source_label),
                Metric("NETWORK TIME", f"{live.network_time_s:.0f}", unit="s", provenance="runtime",
                       note="ticks of the backend engine; one tick is one network second"),
            ]

        event_rows = [(str(item.get("completed_at_utc", "")), self._event(item)) for item in events]
        for item in payload.get("recent_events", []):
            if item.get("event_kind") in TOPOLOGY_EVENT_KINDS:
                event_rows.append((str(item.get("created_at_utc", "")), Event(
                    kind=item["event_kind"].upper(), subject="SIMULATOR INVENTORY",
                    at=_clock(item.get("created_at_utc")),
                    body=f"Configuration version {item.get('version_before')} → {item.get('version_after')} · record {_short(item.get('event_sha256'))}. No physical action.",
                    tone="amber")))
        if live is not None:
            # Engine events; the ones that restate a store record above are left to `live.events`.
            for item in live.events:
                if item.kind not in _ENGINE_TOPOLOGY_MIRRORS:
                    event_rows.append((item.at, Event(
                        kind=item.kind.upper(), subject=item.subject, at=_clock(item.at), body=item.message,
                        tone=SEVERITY_TONE.get(item.severity, "ink"))))
        event_rows.sort(key=lambda row: row[0], reverse=True)
        return Topology(
            ring="Brasil · rede institucional" if any("demonstration_site" in node.get("telemetry", {}) for node in raw_nodes) else str(topo.get("topology_id", "—")),
            version=topo.get("topology_version"), persisted=bool(persisted),
            id=topo.get("topology_id"),
            nodes=nodes,
            links=links,
            metrics=metrics,
            events=[event for _, event in event_rows[:6]],
            links_up=f"{active}/{len(raw_links)}",
            footer=[
                KV("SCHEMA", str(topo.get("schema_version", "—"))),
                KV("UPDATED", _clock(topo.get("updated_at_utc"))),
                KV("PHYSICAL TOPOLOGY", "false", "amber"),
            ],
            caveat=self._topology_caveat(live),
        )

    def _topology_caveat(self, live: LiveView | None) -> str:
        if live is None:
            return ("Simulated sandbox topology. Node positions are logical, not geographic, and "
                    "link entries carry a simulated nominal pair fidelity only — QBER and SKR belong "
                    "to the measured QKD lane and are not interchangeable with it.")
        if live.source_kind == "simulated":
            return ("Simulated input · real pipeline. Only the raw observables of each fibre are generated "
                    "(by the backend engine); QBER, fidelity, key rate, alarms, routes and calibration results "
                    "are computed from them by the backend processing. Node coordinates are operator-declared; "
                    "fibre lengths between located nodes are geodesic lower bounds, not surveyed cable routes. "
                    "Campus references do not claim installed quantum equipment or institutional connections. No physical action is performed.")
        if live.source_kind == "none":
            return ("No input feed is configured: fibres show their configuration only and every measured "
                    "value is absent. Routes are planned by the backend on the configured values. Node "
                    "coordinates are operator-declared. No physical action is performed.")
        return ("Values are computed by the backend processing from the configured input source. Node "
                "coordinates are operator-declared. No physical action is performed.")

    def _event(self, item: Mapping[str, Any]) -> Event:
        action = str(item.get("action", "—"))
        return Event(
            kind=action,
            subject=f"seq {item.get('sequence', '—')} · {item.get('mode', '—')}",
            at=_clock(item.get("completed_at_utc")),
            body=f"{item.get('status', '—')} · record {_short(item.get('record_sha256'))}",
            tone=ACTION_TONE.get(action, "ink"),
        )

    # --------------------------------------------------------------- telemetry
    def _telemetry(self, model: Mapping[str, Any]) -> Telemetry:
        replay = self.replay()
        if replay is None:
            reason = self.replay_error() or model.get("reason", "replay unavailable")
            return Telemetry(
                headline=[Metric("MODEL REPLAY", "unavailable", provenance="assumed", tone="amber", note=str(reason))],
                fidelity=Chart(series=[], y_min=0.0, y_max=1.0),
                coherence=[],
                coherence_note=str(reason),
                conditions=[KV("CONFIGURED", "yes" if model.get("configured") else "no",
                               "ink" if model.get("configured") else "amber")],
                claim=ClaimCheck(
                    verdict="supported",
                    at=_clock(),
                    claim="No prediction is displayed.",
                    reasoning=(
                        "The local server has no validated model bundle to replay, so the console "
                        "shows no fidelity trace rather than an invented one."
                    ),
                ),
                limitations=[str(reason)],
                artefacts=[],
                window_note="MODEL REPLAY UNAVAILABLE",
            )

        rows = replay.get("rows", [])
        predicted = [float(r["predicted_fidelity"]) for r in rows]
        lower = [float(r["interval_lower"]) for r in rows]
        upper = [float(r["interval_upper"]) for r in rows]
        actual = [float(r["simulated_actual_fidelity"]) for r in rows
                  if r.get("simulated_actual_fidelity") is not None]
        pool = predicted + lower + upper + actual or [0.0, 1.0]
        y_min = max(0.0, min(pool) - 0.02)
        y_max = min(1.0, max(pool) + 0.02)

        series = [
            Series("PREDICTED MEAN", predicted, tone="amber", width=2.0),
            Series("INTERVAL LOWER", lower, tone="dim", dashed=True, width=1.0),
            Series("INTERVAL UPPER", upper, tone="dim", dashed=True, width=1.0),
        ]
        if len(actual) == len(predicted) and actual:
            series.append(Series("SIMULATED ACTUAL", actual, tone="violet", width=1.6))

        mean_unc = sum(float(r["uncertainty"]) for r in rows) / len(rows) if rows else 0.0
        ood = sum(1 for r in rows if r.get("out_of_distribution")) / len(rows) if rows else 0.0
        actions = replay.get("action_counts", {})
        total = sum(actions.values()) or 1
        status = replay.get("scientific_status", {})

        headline = [
            Metric("CYCLES REPLAYED", str(replay.get("returned_count", 0)), unit="of "
                   f"{replay.get('trace_record_count', 0)}", provenance="runtime",
                   note="bounded server-side trace, shadow mode"),
            Metric("MEAN UNCERTAINTY", _fmt(mean_unc), uncertainty="model-reported",
                   provenance="derived", tone="amber" if mean_unc > 0.2 else "ink",
                   note="the control plane abstains above its configured limit"),
            Metric("OUT OF DISTRIBUTION", f"{ood * 100:.1f}", unit="%", provenance="derived",
                   tone="red" if ood > 0.1 else "ink"),
            Metric("PHYSICAL ACTION", "none", provenance="runtime", tone="green",
                   note="shadow replay cannot reach an executor"),
        ]

        coherence = [
            Meter(label=action, fraction=count / total, value=f"{count}", tone=ACTION_TONE.get(action, "ink"))
            for action, count in actions.items()
        ]

        return Telemetry(
            headline=headline,
            fidelity=Chart(
                series=series,
                y_min=y_min,
                y_max=y_max,
                x_labels=[str(r["index"]) for r in rows[:: max(1, len(rows) // 6)]],
                threshold=None,
                threshold_label="Policy thresholds are bundle-specific; see decision records.",
            ),
            coherence=coherence,
            coherence_note="Effective actions after the independent safety supervisor, not intents.",
            conditions=[
                KV("MODE", str(replay.get("mode", "shadow"))),
                KV("BUNDLE SHA256", _short(replay.get("bundle_sha256"), 16)),
                KV("POLICY SPEC", _short(replay.get("policy_spec_sha256"), 16)),
                KV("ABSTENTIONS", ", ".join(f"{k}:{v}" for k, v in replay.get("abstention_counts", {}).items()) or "none"),
            ],
            claim=ClaimCheck(
                verdict="supported",
                at=_clock(replay.get("generated_at")),
                claim=str(status.get("target_semantics", "simulated future Bell fidelity")),
                reasoning=str(status.get("prediction_status",
                                         "Simulation validation only; no measured Bell-fidelity claim.")),
            ),
            limitations=[
                "Shadow replay of a frozen bundle — not a live measurement.",
                "Uncertainty is the model's own interval, not an experimental error bar.",
                "Optical and QKD metrics cannot be converted into this target.",
            ],
            artefacts=[
                Artefact("model bundle", _short(replay.get("bundle_sha256"), 16), "sha256"),
                Artefact("model weights", _short(replay.get("model_sha256"), 16), "sha256"),
                Artefact("policy spec", _short(replay.get("policy_spec_sha256"), 16), "sha256"),
            ],
            window_note=f"SHADOW REPLAY · {replay.get('returned_count', 0)} CYCLES",
        )

    # -------------------------------------------------------------------- keys
    def _keys(self, summary: Mapping[str, Any] | None, grid: Mapping[str, Any]) -> Keys:
        if not summary:
            reason = self.summary_error() or "operating grid unavailable"
            return Keys(
                pipeline_link="—",
                pipeline=[],
                pool_label="OPERATING GRID UNAVAILABLE",
                pool_blocks=[],
                pool_legend=[],
                sessions=[],
                sessions_note=str(reason),
                finite_key=[],
                scope_note=str(reason),
                consumers=[],
                provenance=[],
                status="GRID UNAVAILABLE",
            )

        campaigns = list(grid.get("campaigns", ()))
        source = grid.get("source_artifact", {})
        cells = sum(len(c.get("voa_attenuation_db", ())) for c in campaigns)

        pipeline = [
            Stage("01", "REVIEWED SOURCE", source.get("path", "—").split("/")[-1], "", "hash-verified artefact"),
            Stage("02", "OPERATING GRID", str(grid.get("expected_cell_count", "—")), "cells",
                  "exact-grid assurance only", emphasis=True),
            Stage("03", "CAMPAIGNS", str(len(campaigns)), "", "fibre length × coexistence condition"),
            Stage("04", "INTERPOLATION",
                  "allowed" if grid.get("interpolation_allowed") else "refused", "",
                  "points between published cells are not assessed",
                  tone="green" if not grid.get("interpolation_allowed") else "red"),
        ]

        blocks: list[str] = []
        for campaign in campaigns:
            tone = "violet" if campaign.get("external_signal_present") else "amber"
            blocks.extend([tone] * len(campaign.get("voa_attenuation_db", ())))

        sessions = [
            KeySession(
                id=str(c.get("campaign_id", "—")),
                link=f"{_fmt(c.get('fiber_length_km'), '.0f')} km",
                skr="see cell",
                qber="see cell",
                state="COEXISTING" if c.get("external_signal_present") else "DARK FIBRE",
                fingerprint=_short(c.get("source_file_sha256")),
                tone="violet" if c.get("external_signal_present") else "ink",
            )
            for c in campaigns
        ]

        if self._example is None and campaigns and campaigns[0].get("voa_attenuation_db"):
            self._example = self._safe(lambda: self.service.assess_published_cell({
                "campaign_id": campaigns[0]["campaign_id"],
                "voa_attenuation_db": campaigns[0]["voa_attenuation_db"][0],
            }), {})
        example = self._example or {}
        record = example.get("record", {})
        cell = example.get("source_cell", {})
        finite_key = [
            KV("EXAMPLE POINT", str(campaigns[0]["campaign_id"]) if campaigns else "unavailable"),
            KV("QBER REPORTED", _fmt(cell.get("qber_reported"), ".4f") if cell else "—"),
            KV("QBER UNIT", str(cell.get("qber_unit_status", "not published"))),
            KV("SKR", f"{_fmt(cell.get('secure_key_rate_bps'), '.1f')} bps" if cell else "—"),
            KV("DECISION", str(record.get("decision", {}).get("action", "—"))),
        ]

        return Keys(
            pipeline_link=str(grid.get("dataset_id", "—")),
            pipeline=pipeline,
            pool_label=f"PUBLISHED CELLS · {cells}",
            pool_blocks=blocks,
            pool_legend=[
                KV("COEXISTING", "external signal present", "violet"),
                KV("DARK FIBRE", "no external signal", "amber"),
            ],
            sessions=sessions,
            sessions_note=(
                "Campaigns from the reviewed operating grid. QBER and SKR are read from the "
                "hash-verified source cell at assessment time, never from user input."
            ),
            finite_key=finite_key,
            scope_note=str(grid.get("qber_unit_status", "—")),
            consumers=[],
            provenance=[
                KV("DOI", str(grid.get("doi", "—"))),
                KV("LICENSE", str(grid.get("license", "—"))),
                KV("SOURCE SHA256", _short(source.get("sha256"), 16)),
            ],
            status=f"GRID {grid.get('grid_id', '—')} · {len(campaigns)} CAMPAIGNS",
        )

    # -------------------------------------------------------------------- node
    def _node(self, payload: Mapping[str, Any], events: list, live: LiveView | None = None,
              *, selected_node_id: str | None = None) -> NodeDetail:
        raw_nodes = list((payload.get("topology", {}) or {}).get("nodes", ()))
        ident = selected_node_id or self._selected_node
        selected = next((n for n in raw_nodes if n.get("node_id") == ident), None) if ident else (raw_nodes[0] if raw_nodes else None)
        if selected is None:
            return NodeDetail(
                id="—", name_local="NO SANDBOX TOPOLOGY", subtitle="nothing persisted or blueprinted",
                alert=None, equipment=[], subsystems=[], registers=[], register_legend=[],
                throughput=[], throughput_labels=("", "", ""), calibration=[],
                calibration_note="", log=[], breadcrumb="—",
            )
        pairs = list(selected.get("available_pair_fidelities", ()))
        ages = list(selected.get("memory_ages_s", ()))
        storing = selected.get("memory_state") == "STORING"

        alert = None
        if not storing:
            alert = Alert(
                headline="MEMORY IDLE",
                body="No stored pair. PURIFY and SWAP fail closed to HALT until the simulated memory is occupied.",
                tone="amber",
                actions=("RUN SHADOW CYCLE",),
            )

        registers = ["violet" if f >= 0.9 else "amber" if f >= 0.65 else "red" for f in pairs]
        node_events = [e for e in events if e.get("node_id") == selected["node_id"]][:8]
        engine = live.node(str(selected.get("node_id"))) if live is not None else None
        engine_rows: list[KV] = []
        engine_log: list[LogLine] = []
        if engine is not None:
            calibration = engine.calibration
            engine_rows = [
                KV("NETWORK STATUS", engine.status.upper(),
                   {"up": "green", "degraded": "amber", "calibrating": "violet"}.get(engine.status, "red")),
                KV("LOCATION", f"{engine.latitude:.5f}, {engine.longitude:.5f}" if engine.located else "not located",
                   "ink" if engine.located else "amber"),
                KV("PLACE", engine.place or "—"),
                KV("FIBRES", ", ".join(engine.links) or "none"),
                KV("DEMANDS THROUGH", ", ".join(engine.routes_through) or "none"),
                KV("CALIBRATIONS", str(engine.calibration_count)),
            ]
            if calibration is not None:
                engine_rows.append(KV(
                    "LAST CALIBRATION", f"{calibration.status} · {calibration.phase}" if calibration.status != "complete"
                    else f"complete · report {_short(calibration.report_sha256)}",
                    "violet" if calibration.status == "running" else "red" if calibration.status == "failed" else "green"))
            engine_log = [LogLine(_clock(item.at), item.message, item.kind.upper(), SEVERITY_TONE.get(item.severity, "ink"))
                          for item in reversed(live.events) if item.subject == engine.id][:8]

        return NodeDetail(
            id=str(selected.get("node_id", "—")),
            name_local=str(selected.get("role", "—")).upper(),
            subtitle=f"state version {selected.get('state_version', '—')} · {selected.get('memory_state', '—')}",
            alert=alert,
            equipment=[
                KV("ROLE", str(selected.get("role", "—"))),
                KV("MEMORY STATE", str(selected.get("memory_state", "—")),
                   "green" if storing else "amber"),
                KV("STATE VERSION", str(selected.get("state_version", "—"))),
                KV("UPDATED", _clock(selected.get("updated_at_utc"))),
            ] + engine_rows,
            subsystems=[KV(str(k).upper(), str(v)) for k, v in (selected.get("telemetry") or {}).items()][:6],
            registers=registers,
            register_legend=[
                KV("≥ 0.90", "usable pair", "violet"),
                KV("≥ 0.65", "above task threshold", "amber"),
                KV("< 0.65", "below threshold", "red"),
            ],
            throughput=[(f, "violet" if f >= 0.9 else "amber") for f in pairs],
            throughput_labels=("PAIR FIDELITIES", "0.0", "1.0"),
            calibration=[
                Meter(label=f"MEMORY AGE {i}", fraction=min(1.0, age / 5.0), value=f"{age:.2f} s",
                      tone="red" if age > 5.0 else "amber", tick=1.0)
                for i, age in enumerate(ages)
            ],
            calibration_note="Simulated memory ages. Telemetry freshness is a separate gate; these bars are not device calibration.",
            log=engine_log + [LogLine(_clock(e.get("completed_at_utc")), f"{e.get('mode', '—')} cycle",
                         f"{e.get('action', '—')} · {e.get('status', '—')}",
                         ACTION_TONE.get(str(e.get("action")), "ink"))
                 for e in node_events],
            breadcrumb=f"{payload.get('topology', {}).get('topology_id', '—')} / {selected.get('node_id', '—')}",
        )

    # ------------------------------------------------------------------ ledger
    def _ledger(self, events: list, lanes: Mapping[str, Any], summary: Mapping[str, Any] | None,
                safety_events: list | None = None, live: LiveView | None = None,
                topology_payload: Mapping[str, Any] | None = None) -> Ledger:
        integrity = (summary or {}).get("integrity", {})
        checks = int(integrity.get("check_count", 0) or 0)
        failed = int(integrity.get("failed_check_count", 0) or 0)
        fraction = ((checks - failed) / checks) if checks else 0.0

        roles = [
            Role(
                name=LANE_TITLE.get(lane.get("lane"), str(lane.get("lane", "—")).upper()),
                count=str(lane.get("evidence_kind", "—")),
                body=str(lane.get("decision_scope", "—")),
                active=bool(lane.get("control_plane_ingress_allowed")),
            )
            for lane in lanes.get("lanes", ())
        ]

        cycle_entries = [
            LedgerEntry(
                at=_clock(e.get("completed_at_utc")),
                actor=str(e.get("mode", "—")),
                action=f"{e.get('action', '—')} · {e.get('status', '—')}",
                digest=_short(e.get("record_sha256"), 16),
                category="calibration" if e.get("action") in {"PURIFY", "SWAP"} else "system",
                tone=ACTION_TONE.get(str(e.get("action")), "ink"),
            )
            for e in events
        ]
        safety_entries = [
            LedgerEntry(
                at=_clock(e.get("at_utc")),
                actor=str(e.get("actor", "—")),
                action=self._safety_label(e.get("kind", ""), e.get("detail", {})),
                digest=_short(e.get("event_sha256"), 16),
                category="export" if e.get("kind") == "console_exported" else "system",
                tone=self._safety_tone(e.get("kind", ""), e.get("detail", {})),
            )
            for e in (safety_events or [])
        ]
        entries = cycle_entries + safety_entries
        sessions_extra: list[KV] = []
        status_extra = ""
        if live is not None:
            entries = self._live_entries(events, cycle_entries, safety_events or [], safety_entries, live,
                                         topology_payload or {})
            sessions_extra = [KV("NETWORK DECISIONS", f"{len(live.decisions)} in window · journalled with hashes"),
                              KV("NETWORK EVENTS", str(len(live.events)))]
            status_extra = f" · {len(live.decisions)} NETWORK DECISIONS · {len(live.events)} NETWORK EVENTS"

        return Ledger(
            roles=roles,
            sessions=[
                KV("JOURNAL", "local SQLite, per-record hashes"),
                KV("IDEMPOTENCY", "request_id + payload hash"),
                KV("CYCLES", str(len(events))),
                KV("SAFETY EVENTS", str(len(safety_events or []))),
            ] + sessions_extra,
            entries=entries,
            chain_verified=f"{checks - failed}/{checks} REGISTRY CHECKS" if checks else "REGISTRY UNVERIFIED",
            chain_fraction=fraction,
            chain_note=(
                "Each cycle record is hashed over its canonical JSON; replaying a request id with a "
                "different payload is rejected rather than appended."
            ),
            export=[
                KV("REGISTRY SHA256", _short(integrity.get("registry_sha256"), 16)),
                KV("GRID SHA256", _short(integrity.get("grid_config_sha256"), 16)),
                KV("EVIDENCE ITEMS", str(integrity.get("evidence_item_count", "—"))),
            ],
            status=f"{len(events)} JOURNALLED CYCLES · {len(safety_events or [])} SAFETY EVENTS" + status_extra,
        )

    def _live_entries(self, events: list, cycle_entries: list, safety_events: list, safety_entries: list,
                      live: LiveView, topology_payload: Mapping[str, Any]) -> list[LedgerEntry]:
        """Backend records of every kind, newest first: control cycles, safety events,
        topology-store events, engine decisions and engine events. Nothing is synthesised."""

        rows = [(str(raw.get("completed_at_utc", "")), entry) for raw, entry in zip(events, cycle_entries)]
        rows += [(str(raw.get("at_utc", "")), entry) for raw, entry in zip(safety_events, safety_entries)]
        for item in topology_payload.get("recent_events", []) or []:
            if item.get("event_kind") in TOPOLOGY_EVENT_KINDS:
                rows.append((str(item.get("created_at_utc", "")), LedgerEntry(
                    at=_clock(item.get("created_at_utc")), actor="topology store",
                    action=f"{item['event_kind']} · version {item.get('version_before')} → {item.get('version_after')}",
                    digest=_short(item.get("event_sha256"), 16),
                    category="calibration" if item["event_kind"].endswith("recalibrated") else "system",
                    tone="amber")))
        for decision in live.decisions:
            contract = "" if decision.in_contract else " · outside validated domain"
            rows.append((decision.at, LedgerEntry(
                at=_clock(decision.at), actor=decision.controller_id,
                action=f"{decision.kind} · {decision.subject} · {decision.outcome}{contract}",
                digest=_short(decision.record_sha256, 16),
                category="calibration" if decision.kind == "calibration" else "key" if decision.kind == "keyrate"
                else "system",
                tone=_DECISION_TONE.get(decision.kind, "ink"))))
        for item in live.events:
            rows.append((item.at, LedgerEntry(
                at=_clock(item.at), actor=item.source.replace("_", " "), action=f"{item.kind} · {item.message}",
                digest=item.id, category="system", tone=SEVERITY_TONE.get(item.severity, "ink"))))
        rows.sort(key=lambda row: row[0], reverse=True)
        return [entry for _, entry in rows[:MAX_LIVE_LEDGER_ENTRIES]]

    def _safety_events(self) -> list[dict]:
        """Interlock and authorization lifecycle facts, read from the same
        journal a control cycle is written to. Distinct DB table from
        `control_plane_events` — this call reaches the runtime directly since
        the loopback API has no route for it (there's no reason for a
        browser client to see interlock/authorization detail; the console
        can, because it runs in the same process)."""

        return self._safe(
            lambda: self.service.controller_runtime.journal.list_recent_safety_events(limit=50),
            [],
        )

    def _safety_tone(self, kind: str, detail: Mapping[str, Any]) -> str:
        if kind in {"interlock_released", "physical_authorization_created"}:
            return "amber"
        if kind == "interlock_auto_engaged" and detail.get("reason") == "watchdog_timeout":
            return "red"
        if kind == "physical_authorization_revoked":
            return "red"
        return "violet"

    def _safety_label(self, kind: str, detail: Mapping[str, Any]) -> str:
        if kind.startswith("console_"):
            return str(detail.get("message", kind))
        labels = {
            "interlock_released": "Hardware interlock released",
            "interlock_kept_alive": "Hardware interlock heartbeat",
            "interlock_auto_engaged": (
                "Interlock auto-engaged · watchdog timeout"
                if detail.get("reason") == "watchdog_timeout"
                else "Interlock re-engaged"
            ),
            "physical_authorization_created": "Two-person authorization granted",
            "physical_authorization_revoked": "Two-person authorization revoked",
        }
        return labels.get(kind, kind.replace("_", " "))

    def _notes_as_entries(self) -> list[LedgerEntry]:
        return [LedgerEntry(n.at, "console", n.body, "local", "system", n.tone) for n in self._notes]

    def _note(self, body: str, tone: str = "ink", *, kind: str = "console_command") -> None:
        self.service.controller_runtime.journal.append_safety_event(
            kind, "local-console", datetime.now(timezone.utc).isoformat(),
            {"message": body, "tone": tone, "operation_id": str(uuid4())})
        self._notes.insert(0, LogLine(_clock(), body, "", tone))
        del self._notes[8:]

    def run_scenario(self, target: str = "") -> None:
        from quantum_autopilot.control_plane.topology import default_simulated_topology
        # Initialization preserves an existing workspace, including concurrent edits.
        self.service.topology_store.initialize(default_simulated_topology())
        demo_id = f"console-demo-{uuid4()}"
        self._note(f"Requested isolated simulated resilience scenario: {demo_id}")
        self.service.run_control_scenario({
            "scenario_id": f"console-{uuid4()}", "preset": "resilience_demo",
            "topology_id": demo_id,
            "reset_topology": True})
        self._note("Isolated simulated scenario completed; workspace topology preserved", "green")
        self.refresh()

    def add_simulated_node(self, target: str) -> None:
        self._tracked_inventory("add_simulated_node", target)

    def configure_simulated_link(self, target: str) -> None:
        self._tracked_inventory("configure_simulated_link", target)

    def apply_simulated_node_action(self, target: str) -> None:
        self._tracked_inventory('apply_simulated_node_action', target)

    # ------------------------------------------------- live network commands
    # Tracked topology verbs: REQUESTED -> RUNNING -> CONFIRMED | UNKNOWN, with readback.
    def add_network_node(self, target: str) -> None:
        self._tracked_inventory("add_network_node", target)

    def set_node_location(self, target: str) -> None:
        self._tracked_inventory("set_node_location", target)

    def set_node_isolation(self, target: str) -> None:
        self._tracked_inventory("set_node_isolation", target)

    def set_link_active(self, target: str) -> None:
        self._tracked_inventory("set_link_active", target)

    def recalibrate_node(self, target: str) -> None:
        """Full recalibration executed by the backend engine (blocking, off the UI thread).

        `service.recalibrate_node` drives its own network ticks, so the stepping thread is
        held meanwhile; the phases stay visible through the live polls."""
        self._step_hold.set()
        try:
            self._tracked_inventory("recalibrate_node", target)
        finally:
            self._step_hold.clear()

    def _complete_request(self, verb: str, request: Any) -> Any:
        """Fill the bookkeeping fields a live request may leave out, from the current snapshot."""
        if verb not in LIVE_TRACKED_VERBS or not isinstance(request, dict):
            return request
        if "expected_version" not in request or "topology_id" not in request:
            snap = self._snap
            version = snap.topology.version if snap is not None else None
            topology_id = snap.topology.id if snap is not None else None
            if type(version) is not int or not isinstance(topology_id, str):
                current = (self.service.control_plane_topology().get("topology", {}) or {})
                version, topology_id = current.get("topology_version"), current.get("topology_id")
            request.setdefault("expected_version", version)
            request.setdefault("topology_id", topology_id)
        if verb in ("add_network_node", "set_node_location"):
            for name, value in _LOCATION_DEFAULTS.items():
                request.setdefault(name, value)
        return request

    def _engine_command(self, verb: str, target: str) -> None:
        """An untracked engine verb: executed by `service.network_command`, effect read back
        from the engine's own events and state."""
        from .input_sources import _unique_fields
        self.capabilities.require(verb)
        if verb == "inject_input_event" and not self.is_demo:
            raise ValueError("inject_input_event refused: input events can only be injected into the simulated "
                             "input of the demonstration")
        limit=32768 if verb in ('save_communicator','configure_communicator_connection') else 4096
        if not isinstance(target, str) or len(target) > limit:
            raise ValueError("bounded JSON command payload required")
        payload = json.loads(target, object_pairs_hook=_unique_fields)
        if not isinstance(payload, dict):
            raise ValueError(f"{verb} takes a JSON object")
        result = self.service.network_command(verb, payload)
        if result.get("physical_action_performed") is not False:
            raise ValueError("safe network command confirmation missing")
        self.last_network_result = result
        if verb == "set_model_archive":
            self._note(f"Model archive {payload.get('kind')}: {result.get('status')}",
                       "green" if result.get("status") == "verified" else "amber")
        self.refresh()

    def define_pair(self, target: str) -> None:
        self._engine_command("define_pair", target)

    def remove_pair(self, target: str) -> None:
        self._engine_command("remove_pair", target)

    def request_pair(self, target: str) -> None:
        self._engine_command("request_pair", target)

    def purify_pair(self, target: str) -> None:
        self._engine_command("purify_pair", target)

    def consume_pair(self, target: str) -> None:
        self._engine_command("consume_pair", target)

    def set_model_archive(self, target: str) -> None:
        self._engine_command("set_model_archive", target)

    def set_ml_control(self, target: str) -> None:
        self._engine_command("set_ml_control", target)

    def configure_network_input(self, target: str) -> None:
        self._engine_command("configure_network_input", target)

    def configure_technology(self, target: str) -> None:
        self._engine_command("configure_technology", target)

    def configure_technology_source(self, target: str) -> None:
        self._engine_command("configure_technology_source", target)

    def provision_satellite_session(self, target: str) -> None:
        self._engine_command("provision_satellite_session", target)

    def export_technology_events(self, target: str) -> None:
        self._engine_command("export_technology_events", target)

    def save_communicator(self,target: str) -> None:
        self._engine_command('save_communicator',target)

    def configure_communicator_connection(self,target: str) -> None:
        self._engine_command('configure_communicator_connection',target)

    def inject_input_event(self, target: str) -> None:
        self._engine_command("inject_input_event", target)

    def register_fpga_device(self, target: str) -> None:
        self._engine_command("register_fpga_device", target)

    def configure_fpga_device(self, target: str) -> None:
        self._engine_command("configure_fpga_device", target)

    def set_fpga_connection(self, target: str) -> None:
        self._engine_command("set_fpga_connection", target)

    def inject_fpga_fault(self, target: str) -> None:
        self._engine_command("inject_fpga_fault", target)

    def _tracked_inventory(self, verb: str, target: str) -> None:
        from .input_sources import _unique_fields
        from quantum_autopilot.control_plane.console_commands import ConsoleCommandJournal, validated_request
        from quantum_autopilot.control_plane.topology import _sha256
        self.capabilities.require(verb)
        if not isinstance(target, str) or len(target) > 4096:
            raise ValueError("bounded JSON inventory request required")
        request = validated_request(verb, self._complete_request(
            verb, json.loads(target, object_pairs_hook=_unique_fields)))
        journal = ConsoleCommandJournal(self.service.controller_runtime.journal.path)
        command_id = journal.begin(verb, request)
        try:
            self.capabilities.require(verb)
        except ValueError:
            journal.transition(command_id, "REJECTED", {"reason": "capability_revoked_before_dispatch"})
            raise
        journal.transition(command_id, "RUNNING")  # Failure here must prevent invocation.
        try:
            if verb == "recalibrate_node" and self._stepper is not None:
                # The procedure drives its own network seconds: keep the cadence of the stepping
                # thread it replaces meanwhile, so every phase is published for one period.
                result = self.service.recalibrate_node(request, tick_interval_s=min(self.step_period_s, 10.0))
            else:
                result = getattr(self.service, verb)(request)
            persisted = self.service.topology_store.get(request['topology_id'])
            if (result.get('status') != 'persisted' or result.get('physical_action_performed') is not False
                    or persisted is None or persisted.topology_version != request['expected_version'] + 1
                    or persisted.to_dict() != result.get('topology')):
                raise ValueError('inventory readback did not confirm this response')
        except Exception as exc:
            journal.transition(command_id, "UNKNOWN", {"reason": "execution_or_readback_failed", "error_type": type(exc).__name__})
            raise ValueError(f'Command {command_id}: outcome UNKNOWN; inspect topology before retrying. {exc}') from exc
        journal.transition(command_id, "CONFIRMED", {"topology_version": persisted.topology_version,
            "topology_sha256": _sha256(persisted.to_dict())})
        self.refresh()  # UI refresh failure cannot undo a confirmed persistence operation.

    def run_temporal_path(self, target: str) -> None:
        from .input_sources import _unique_fields
        self.capabilities.require("run_temporal_path")
        if not isinstance(target, str) or len(target) > 4096:
            raise ValueError("bounded temporal path JSON required")
        request = json.loads(target, object_pairs_hook=_unique_fields)
        if not isinstance(request, dict) or request.get("protocol") != "memory_single_swap_v1":
            raise ValueError("temporal action requires memory_single_swap_v1")
        self.run_workspace_path(target)

    def run_workspace_path(self, target: str) -> None:
        from .input_sources import _unique_fields
        self.capabilities.require("run_workspace_path")
        if not isinstance(target, str) or len(target) > 4096:
            raise ValueError("bounded workspace path JSON required")
        request = json.loads(target, object_pairs_hook=_unique_fields)
        if isinstance(request, dict) and request.get("protocol") == "memory_single_swap_v1":
            self.capabilities.require("run_temporal_path")
        result = self.service.run_workspace_path(request)
        if result["record"]["physical_action_performed"] is not False:
            raise ValueError("safe workspace path confirmation missing")
        self._path_run_id = request["run_id"]
        self.refresh()  # Read the hashed persisted result, not just the command response.

    def run_lab_experiment(self, target: str) -> None:
        from quantum_autopilot.lab.service import unique_fields
        self.capabilities.require("run_lab_experiment")
        if not isinstance(target, str) or len(target) > 256:
            raise ValueError("bounded laboratory request JSON required")
        request = json.loads(target, object_pairs_hook=unique_fields)
        result = self.service.run_lab_experiment(request)
        if result.get("physical_action_performed") is not False or result.get("mode") != "emulation":
            raise ValueError("laboratory execution scope differs from console capability")
        self._note(f"Laboratory {result['run_id']} · {result['status']} · emulation", "green")
        self.refresh()

    def configure_laboratory(self, target: str) -> None:
        from quantum_autopilot.lab.service import unique_fields
        self.capabilities.require("configure_laboratory")
        if not isinstance(target, str) or len(target) > 16384:
            raise ValueError("bounded laboratory configuration JSON required")
        request = json.loads(target, object_pairs_hook=unique_fields)
        result = self.service.configure_laboratory(request)
        if result.get("physical_action_performed") is not False:
            raise ValueError("laboratory configuration scope mismatch")
        self._note(f"Laboratory configuration revision {result['revision']} validated", "green")
        self.refresh()

    def replay_model(self, target: str = "") -> None:
        self._note("Requested frozen model replay in shadow mode")
        self._replay = self.service.run_wdm_model_replay({"limit": self.replay_limit})
        self._note(f"Shadow model replay completed: {self._replay.get('returned_count', 0)} cycles", "green")

    def set_decision_source(self, transition: str) -> None:
        raise ValueError("Runtime policy binding unavailable; no decision-source change was performed")

    # ---------------------------------------------------------------- commands
    def recalibrate(self, target: str) -> None:
        """Run one *shadow* cycle against the selected node. Never physical."""

        self._note(f"Requested diagnostic shadow cycle: {target}; fixed F=0.90, uncertainty=0.01")
        payload = self._safe(self.service.control_plane_topology, {})
        nodes = list((payload.get("topology", {}) or {}).get("nodes", ()))
        node = next((n for n in nodes if n.get("node_id") == (target or self._selected_node)), None)
        if node is None:
            self._note("shadow cycle refused: unknown node", "red")
            return self.refresh()
        now = datetime.now(timezone.utc).isoformat()
        request = {
            "mode": "shadow",
            "snapshot": {
                "request_id": f"console-{now}",
                "node_id": node["node_id"],
                "timestamp_utc": now,
                "state_version": node.get("state_version", 0),
                "predicted_fidelity": 0.90,
                "prediction_uncertainty": 0.01,
                "telemetry_age_s": 0.1,
                "memory_state": node.get("memory_state", "IDLE"),
                "available_pair_fidelities": list(node.get("available_pair_fidelities", ())),
                "memory_ages_s": list(node.get("memory_ages_s", ())),
                "telemetry": {"source": "console"},
                "integrity_verified": True,
                "operator_interlock": True,
                "provenance": {
                    "evidence_kind": "simulated",
                    "source": "console-fixed-input-diagnostic",
                    "timestamp": now,
                    "metadata": {
                        "evidence_lane": "bell_fidelity_control",
                        "target_semantics": "simulated_future_bell_fidelity",
                    },
                },
            },
        }
        try:
            result = self.service.run_control_cycle(request)
            effective = result["record"]["safety"]["effective_action"]
            self._note(f"shadow cycle {node['node_id']} → {effective}", ACTION_TONE.get(effective, "ink"))
        except Exception as exc:
            self._note(f"shadow cycle rejected: {exc}"[:96], "red")
        self.refresh()

    def isolate(self, target: str) -> None:
        self._note("isolate refused: no physical actuation path exists", "red")
        self.refresh()

    def acknowledge(self, target: str) -> None:
        self._note(f"acknowledged {target}", "muted")
        self.refresh()

    @_guarded("evidence.export")
    def export_captured_evidence(self, captured: str, destination: str) -> str:
        """Revalidate authority in the worker immediately before captured export."""
        from .evidence_destination import export_destination
        if not isinstance(captured, str) or not captured.strip() or len(captured) > 16 * 1024 * 1024:
            raise ValueError("bounded captured evidence required")
        # Export evidence records only, with strict JSON validity and no NaN.
        json.loads(captured, parse_constant=lambda _: (_ for _ in ()).throw(ValueError("nonfinite evidence")))
        path = export_destination(json.dumps({"scope": "captured", "destination": destination}), Path())
        self._authorize("evidence.export")
        with path.open("x", encoding="utf-8") as handle:
            handle.write(captured)
        if self.security is not None:
            from quantum_autopilot.security.service_boundary import service_resource
            self.security.audit.record(self.security.current_identity(), "evidence.export",
                                       service_resource(self.service), "completed",
                                       {"artifact_sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
        self.export_completed.emit(str(path))
        return str(path)

    @_guarded("evidence.export")
    def export_record(self, scope: str) -> None:
        """Export authorized evidence to an explicit new file or the journal directory."""

        destination = None
        if not isinstance(scope, str) or len(scope) > 32768:
            raise ValueError("bounded evidence export request required")
        if scope.lstrip().startswith("{"):
            from .input_sources import _unique_fields
            request = json.loads(scope, object_pairs_hook=_unique_fields)
            if not isinstance(request, dict) or set(request) != {"scope", "destination"}:
                raise ValueError("export requires scope and destination")
            scope, destination = request["scope"], request["destination"]
            if not isinstance(destination, str) or not destination.strip() or len(destination) > 32000:
                raise ValueError("export destination must be a nonempty file path")
            destination = Path(destination).expanduser().resolve()
            if destination.suffix.lower() != ".json" or not destination.parent.is_dir():
                raise ValueError("export requires a .json file in an existing directory")
        if not isinstance(scope, str) or not scope.strip() or len(scope) > 128:
            raise ValueError("evidence export scope required")

        root = self.state_root or Path.cwd() / "outputs" / "console"
        root.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + "-" + uuid4().hex
        path = destination or root / f"console_export_{stamp}.json"
        payload = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "scope": scope,
            "health": self._safe(self.service.health, {}),
            "topology": self._safe(self.service.control_plane_topology, {}),
            "events": self._safe(lambda: self.service.control_plane_events(limit=100), {}),
            "model": self._safe(self.service.wdm_model_status, {}),
            "model_replay": self._replay,
            "qkd_example": self._example,
            "format": "unsigned_console_evidence_export_v1",
            "evidence_lanes": self._safe(self.service.evidence_lanes, {}),
            "safety_events": self._safety_events(),
        }
        path_reader = getattr(self.service, "workspace_path_result", None)
        payload["workspace_path"] = path_reader(getattr(self, "_path_run_id", None)) if callable(path_reader) else None
        self._authorize("evidence.export")
        with path.open("x", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
        if self.security is not None:
            from quantum_autopilot.security.service_boundary import service_resource
            self.security.audit.record(self.security.current_identity(), "evidence.export",
                                       service_resource(self.service), "completed",
                                       {"artifact_sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
        self._note(f"Exported unsigned JSON: {path}", "green", kind="console_exported")
        self.export_completed.emit(str(path))
        self.refresh()


# Protect native-only boundaries that read journal paths or cached evidence;
# their services cannot otherwise see the UI's passive/cache operations.
for _name, _action in {
    "policy_decision_store_path": "policy.read", "policy_context_source": "policy.read",
    "policy_review_source": "policy.read", "command_reconciliation_store_path": "evidence.read",
    "temporal_campaign_store_path": "simulation.run", "temporal_campaign_history_store_path": "evidence.read",
    "select_node": "network.read", "summary": "evidence.read", "replay": "simulation.run",
    "reload": "network.read", "run_scenario": "simulation.run",
}.items():
    setattr(ServiceProvider, _name, _guarded(_action, batch=_action.endswith(".read"))(getattr(ServiceProvider, _name)))


__all__ = ["DEFAULT_POLL_MS", "LIVE_ENGINE_VERBS", "LIVE_POLL_MS", "LIVE_TRACKED_VERBS", "ServiceProvider",
           "format_fidelity", "format_qber", "format_rate", "live_view_from_state"]
