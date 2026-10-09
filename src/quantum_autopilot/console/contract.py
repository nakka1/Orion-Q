"""The data contract.

These dataclasses are the whole interface between the UI and whatever feeds it.
A real backend has exactly one job: return a `Snapshot`. Nothing in this module
imports Qt or knows about colour — the `tone` fields carry *semantics*
("measured", "fault") that theme.py maps to tokens.

Tones: ink | muted | dim | amber | green | red | violet
"""
from __future__ import annotations

from .capabilities import SourceCapabilities
from .observations import Observation

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal, Mapping, Sequence

if TYPE_CHECKING:
    from quantum_autopilot.control_plane.contracts import NetworkStateSnapshot

Tone = Literal["ink", "muted", "dim", "amber", "green", "red", "violet"]
Health = Literal["up", "degraded", "down", "unknown"]
Provenance = Literal["measured", "derived", "assumed", "runtime", "simulated"]

HEALTH_TONE: dict[str, Tone] = {"up": "green", "degraded": "amber", "down": "red", "unknown": "dim"}


# ------------------------------------------------------------------ metrics
@dataclass
class Metric:
    label: str
    value: str                      # preformatted — the UI never rounds physics
    unit: str = ""
    uncertainty: str = ""           # e.g. "± 0.0031"
    provenance: Provenance = "measured"
    note: str = ""                  # what it means / what it does not mean
    tone: Tone = "ink"


@dataclass
class Series:
    label: str
    points: Sequence[float]         # y in data units
    tone: Tone = "amber"
    dashed: bool = False
    width: float = 2.0


@dataclass
class Chart:
    series: list[Series]
    y_min: float
    y_max: float
    x_labels: Sequence[str] = ()
    threshold: float | None = None
    threshold_label: str = ""


@dataclass
class Meter:
    label: str
    fraction: float                 # 0..1 filled
    value: str
    tone: Tone = "amber"
    tick: float | None = None       # reference mark, 0..1


@dataclass
class KV:
    key: str
    value: str
    tone: Tone = "ink"


# ------------------------------------------------------------------ network
@dataclass
class Node:
    id: str
    role: str                       # "LAB HUB · SPDC"
    health: Health = "up"
    x: float = 0.5                  # normalised logical position, 0..1
    y: float = 0.5
    kind: Literal["node", "repeater", "core", "satellite"] = "node"
    latitude: float | None = None
    longitude: float | None = None
    connection_address: str | None = None  # metadata only; no connection is implied
    connection_port: int | None = None
    inventory_origin: str | None = None
    connection_protocol: str | None = None
    isolated: bool = False
    calibration_count: int = 0
    # Live network (design §1): `located` is True exactly when both coordinates are set
    # (derived here when a provider leaves it None); `place` is the operator's label;
    # `status` is the ENGINE node status (up|degraded|down|isolated|calibrating) or "".
    located: bool | None = None
    place: str | None = None
    status: str = ""
    network_role: str = ""
    physical_type: str = ""
    display_id: str = ""
    name: str = ""
    category: str = ""

    def __post_init__(self):
        if self.located is None:
            self.located = self.latitude is not None and self.longitude is not None


@dataclass
class Link:
    a: str
    b: str
    label: str
    fidelity: str = "—"
    qber: str = "—"
    skr: str = "—"
    kind: Literal["fibre", "satellite", "fault"] = "fibre"
    weight: float = 2.0
    tone: Tone = "amber"
    id: str = ""                   # source-local channel/link identity when supplied
    provenance: Provenance = "assumed"
    distance_km: float | None = None  # configuration, not a measurement
    nominal_pair_fidelity: float | None = None
    configured_active: bool | None = None
    # Live network: ENGINE link status (see LINK_STATUSES) or "" when no engine feeds this
    # source; `effective` = in the effective routing graph; `stability` in 0..1 or None.
    status: str = ""
    effective: bool | None = None
    stability: float | None = None
    channels: dict | None = None


@dataclass
class Event:
    kind: str                       # FAULT / WARN / KEY
    subject: str
    at: str
    body: str
    tone: Tone = "amber"


@dataclass
class LogLine:
    at: str
    body: str
    status: str = ""
    tone: Tone = "ink"


@dataclass
class Alert:
    headline: str
    body: str
    tone: Tone = "amber"
    actions: Sequence[str] = ()


# -------------------------------------------------------------- live network
# UI-side mirror of the backend's `orion.network-live.v1` state (`network_engine.state`).
# Every value here was produced by the backend; the provider only renames and regroups.
# Numbers stay numbers and a value that was not produced is None (rendered "—").
LINK_STATUSES = ("up", "degraded", "down", "disabled", "isolated", "calibrating", "compromised", "no_input")
#: Statuses that take a fibre out of the effective routing graph.
LINK_STATUSES_OUT_OF_GRAPH = ("disabled", "isolated", "calibrating", "compromised")
NODE_STATUSES = ("up", "degraded", "down", "isolated", "calibrating")
DEMAND_STATUSES = ("routed", "rerouted", "no_route", "blocked", "idle")
DECISION_KINDS = ("reroute", "compensation", "sync", "keyrate", "detector", "allocation", "calibration", "no_route", "ml_control")
MODEL_STATES = ("active", "fallback", "unavailable")
CALIBRATION_PHASES = ("queued", "polarisation", "sync", "commissioning", "memory", "report", "complete")
LINK_STATUS_TONE: dict[str, Tone] = {
    "up": "green", "degraded": "amber", "down": "red", "disabled": "dim", "isolated": "dim",
    "calibrating": "violet", "compromised": "red", "no_input": "muted"}
NODE_STATUS_TONE: dict[str, Tone] = {
    "up": "green", "degraded": "amber", "down": "red", "isolated": "dim", "calibrating": "violet"}
DEMAND_STATUS_TONE: dict[str, Tone] = {
    "routed": "green", "rerouted": "amber", "no_route": "red", "blocked": "red", "idle": "dim"}
MODEL_STATE_TONE: dict[str, Tone] = {"active": "green", "fallback": "amber", "unavailable": "dim", "disabled": "dim"}
SEVERITY_TONE: dict[str, Tone] = {"info": "ink", "warning": "amber", "critical": "red"}


@dataclass(frozen=True)
class LiveHistory:
    """1 Hz rolling history of one fibre (last 300 s); parallel tuples, None = no value."""

    t_s: tuple[float, ...] = ()                 # network time, s
    qber: tuple[float | None, ...] = ()         # fraction 0..1
    fidelity: tuple[float | None, ...] = ()     # Werner estimate 0..1
    skr_bps: tuple[float | None, ...] = ()      # bit/s


@dataclass(frozen=True)
class LiveDetector:
    """Attack detector output; all None while the session takes its own baseline."""

    label: str | None = None                    # gated per-period class
    attack_probability: float | None = None     # 0..1
    alarm: bool | None = None                   # sustained attack alarm (fibre becomes compromised)
    baseline_alarm: bool | None = None          # QBER-threshold alarm
    state: str | None = None                    # e.g. "session baseline (6 of 60 periods)"
    in_contract: bool | None = None


@dataclass(frozen=True)
class LiveQkd:
    protocol: str = "decoy_state_bb84"
    signal_intensity: float | None = None       # mean photon number
    decoy_intensity: float | None = None        # mean photon number
    clock_rate_hz: float | None = None
    signal_gain: float | None = None
    decoy_gain: float | None = None
    key_pool_bits: float | None = None          # accumulated secret key, bit
    discarded_bits: float | None = None         # key discarded on alarms, bit
    last_action: str | None = None              # last key-rate controller action


@dataclass(frozen=True)
class LiveController:
    """Who decides one role on one fibre."""

    role: str                                   # compensation | sync | keyrate | detector | qber_alarm
    controller_id: str
    model_state: str | None = None              # active | fallback | unavailable | None (plain baseline)


@dataclass(frozen=True)
class LiveAdmission:
    """Classical-traffic admission decided for one fibre (hybrid allocation)."""

    level: float | None = None                  # admitted fraction 0..1
    controller_id: str = ""
    model_state: str | None = None
    in_contract: bool | None = None
    fallback_used: bool | None = None
    backlog_bits: float | None = None
    reason: str = ""
    learned_model: str | None = None
    learned_model_reason: str | None = None


@dataclass(frozen=True)
class LiveDomainNote:
    """Whether one model role runs inside the domain it was validated in, for one fibre."""

    role: str
    in_domain: bool | None
    detail: str = ""


@dataclass(frozen=True)
class LiveLink:
    id: str
    a: str
    b: str
    distance_km: float | None
    status: str                                 # LINK_STATUSES
    configured_active: bool | None = None       # operator flag (topology), not health
    effective: bool = False                     # in the effective routing graph
    qber: float | None = None                   # fraction 0..1
    fidelity: float | None = None               # Werner estimate 1 - 3*QBER/2
    skr_bps: float | None = None                # secret key rate, bit/s
    sifted_rate_hz: float | None = None
    sync_residual_ps: float | None = None
    gate_efficiency: float | None = None        # 0..1
    polarisation_residual_mrad: float | None = None
    stability: float | None = None              # 0..1
    wave: tuple[float, ...] = ()                # visibility 1 - 2*QBER, oldest first
    wave_rate_hz: float = 20.0                  # samples per network second in `wave`
    history: LiveHistory = LiveHistory()
    detector: LiveDetector = LiveDetector()
    qkd: LiveQkd = LiveQkd()
    controllers: tuple[LiveController, ...] = ()
    classical_admission: LiveAdmission | None = None
    input: str = "none"                         # simulated | measured | none | withheld
    display_provenance: Provenance = "assumed"  # how the numbers above must be labelled
    status_reasons: tuple[str, ...] = ()
    domain_notes: tuple[LiveDomainNote, ...] = ()
    provenance: Mapping[str, Any] = field(default_factory=dict)   # the backend record, unabridged


@dataclass(frozen=True)
class LiveCalibrationLink:
    link_id: str
    status: str = ""
    qber_before: float | None = None
    qber_after: float | None = None
    sync_residual_ps_before: float | None = None
    sync_residual_ps_after: float | None = None
    signal_intensity: float | None = None
    decoy_intensity: float | None = None
    key_rate_hz_after: float | None = None
    detail: Mapping[str, Any] = field(default_factory=dict)       # the backend record, unabridged


@dataclass(frozen=True)
class LiveCalibration:
    """A full recalibration: while it runs (status "running") and once reported."""

    calibration_id: str
    status: str = "running"                     # running | complete | failed
    phase: str = "queued"                       # CALIBRATION_PHASES
    progress: float | None = None               # 0..1
    started_at_tick: int | None = None
    completed_at: str | None = None             # ISO-8601 UTC
    duration_s: float | None = None             # network seconds
    links: tuple[LiveCalibrationLink, ...] = ()
    memory_reset: bool | None = None
    report_sha256: str | None = None


@dataclass(frozen=True)
class LiveNode:
    id: str
    role: str                                   # endpoint | repeater
    kind: str | None                            # node | repeater | core
    status: str                                 # NODE_STATUSES
    located: bool = False
    latitude: float | None = None               # decimal degrees
    longitude: float | None = None
    place: str | None = None
    links: tuple[str, ...] = ()                 # incident link ids
    memory_state: str | None = None
    pairs: tuple[float, ...] = ()               # stored pair fidelities
    temperature_c: float | None = None
    calibration: LiveCalibration | None = None
    calibration_count: int = 0
    routes_through: tuple[str, ...] = ()        # demand ids routed through this node
    address: str | None = None                  # metadata only; no connection is opened
    port: int | None = None
    communicator: dict = field(default_factory=dict)


@dataclass(frozen=True)
class LiveDemand:
    id: str
    a: str
    b: str
    status: str                                 # DEMAND_STATUSES
    route: tuple[str, ...] = ()                 # node ids, end to end
    route_links: tuple[str, ...] = ()           # link ids along `route`
    fidelity: float | None = None               # mean delivered fidelity of the plan
    success_rate: float | None = None           # 0..1 over the plan's episodes
    policy_id: str | None = None                # controller that produced the plan
    in_contract: bool = False
    fallback_used: bool = False
    reason: str = ""                            # backend's reason for no_route / blocked
    changed_at_tick: int | None = None
    min_fidelity: float | None = None
    pair: Mapping[str, Any] | None = None       # operator's end-to-end pair, backend record
    detail: Mapping[str, Any] = field(default_factory=dict)   # numbers of the last plan


@dataclass(frozen=True)
class LiveDecision:
    id: str
    tick: int
    at: str                                     # ISO-8601 UTC
    kind: str                                   # DECISION_KINDS
    controller_id: str
    model_state: str                            # MODEL_STATES
    subject: str                                # link, node or demand id
    summary: str
    outcome: str
    in_contract: bool
    fallback_used: bool
    record_sha256: str
    inputs_sha256: str | None = None


@dataclass(frozen=True)
class LiveModel:
    id: str
    role: str                                   # compensation | sync | keyrate | detector | routing | allocation
    state: str                                  # MODEL_STATES
    reason: str = ""
    archive_sha256: str | None = None
    in_contract: bool | None = None
    inferences: int = 0
    faults: int = 0
    last_latency_ms: float | None = None


@dataclass(frozen=True)
class LiveEvent:
    id: str
    tick: int
    at: str                                     # ISO-8601 UTC
    kind: str
    subject: str
    severity: str = "info"                      # info | warning | critical
    message: str = ""
    source: str = "engine"                      # engine | input_source | operator


@dataclass(frozen=True)
class LiveView:
    """Everything the backend network engine published for its last tick."""

    tick: int
    network_time_s: float
    source_kind: str                            # simulated | measured | none
    source_label: str
    source_note: str = ""
    nodes: tuple[LiveNode, ...] = ()
    links: tuple[LiveLink, ...] = ()
    demands: tuple[LiveDemand, ...] = ()
    decisions: tuple[LiveDecision, ...] = ()    # oldest first, last 200
    models: tuple[LiveModel, ...] = ()
    events: tuple[LiveEvent, ...] = ()          # oldest first, last 200
    at: str = ""                                # ISO-8601 UTC of the tick
    topology_version: int | None = None
    source_seed: int | None = None
    source_scenario: str | None = None
    schema: str = "orion.network-live.v1"
    #: Declared thresholds of the running engine (`LiveState.policy`), numbers as published:
    #: `qber_down`, `fidelity_at_qber_down`, `wave_rate_hz`, `wave_window_s`, `history_window_s`, ...
    policy: dict = field(default_factory=dict)
    technologies: dict = field(default_factory=dict)
    communicators: dict = field(default_factory=dict)
    satellites: dict = field(default_factory=dict)
    # Backend publication ordering; these markers do not measure scientific time.
    engine_session_id: str | None = None
    publication_revision: int | None = None

    def threshold(self, name: str) -> float | None:
        """One declared threshold of the running engine, or None when it did not publish it."""

        value = self.policy.get(name)
        return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None

    def link(self, link_id: str) -> LiveLink | None:
        return next((item for item in self.links if item.id == link_id), None)

    def node(self, node_id: str) -> LiveNode | None:
        return next((item for item in self.nodes if item.id == node_id), None)

    def demand(self, demand_id: str) -> LiveDemand | None:
        return next((item for item in self.demands if item.id == demand_id), None)

    def node_links(self, node_id: str) -> tuple[LiveLink, ...]:
        return tuple(item for item in self.links if node_id in (item.a, item.b))

    @property
    def unlocated_nodes(self) -> tuple[LiveNode, ...]:
        return tuple(item for item in self.nodes if not item.located)


# ------------------------------------------------------------------ screens
@dataclass
class Topology:
    ring: str
    nodes: list[Node]
    links: list[Link]
    metrics: list[Metric]
    events: list[Event]
    links_up: str = ""
    footer: list[KV] = field(default_factory=list)
    caveat: str = ""
    version: int | None = None
    persisted: bool = False
    id: str | None = None


@dataclass
class ClaimCheck:
    verdict: Literal["supported", "exceeds"]
    at: str
    claim: str
    reasoning: str


@dataclass
class Artefact:
    name: str
    digest: str
    size: str


@dataclass
class Telemetry:
    headline: list[Metric]
    fidelity: Chart
    coherence: list[Meter]
    coherence_note: str
    conditions: list[KV]
    claim: ClaimCheck
    limitations: list[str]
    artefacts: list[Artefact]
    window_note: str = ""


@dataclass
class Stage:
    index: str
    name: str
    value: str
    unit: str
    note: str
    tone: Tone = "ink"
    emphasis: bool = False


@dataclass
class KeySession:
    id: str
    link: str
    skr: str
    qber: str
    state: str
    fingerprint: str
    tone: Tone = "ink"


@dataclass
class Keys:
    pipeline_link: str
    pipeline: list[Stage]
    pool_label: str
    pool_blocks: list[Tone]         # one tone per block: violet/amber/dim
    pool_legend: list[KV]
    sessions: list[KeySession]
    sessions_note: str
    finite_key: list[KV]
    scope_note: str
    consumers: list[Meter]
    provenance: list[KV]
    status: str = ""


@dataclass
class NodeDetail:
    id: str
    name_local: str
    subtitle: str
    alert: Alert | None
    equipment: list[KV]
    subsystems: list[KV]
    registers: list[Tone]
    register_legend: list[KV]
    throughput: list[tuple[float, Tone]]
    throughput_labels: tuple[str, str, str]
    calibration: list[Meter]
    calibration_note: str
    log: list[LogLine]
    breadcrumb: str = ""


@dataclass
class Role:
    name: str
    count: str
    body: str
    active: bool = False


@dataclass
class LedgerEntry:
    at: str
    actor: str
    action: str
    digest: str
    category: Literal["calibration", "key", "export", "system"] = "system"
    tone: Tone = "ink"


@dataclass(frozen=True)
class CommandView:
    command_id: str
    verb: str
    state: str
    requested_at: str
    serialized: str


@dataclass
class Ledger:
    roles: list[Role]
    sessions: list[KV]
    entries: list[LedgerEntry]
    chain_verified: str
    chain_fraction: float
    chain_note: str
    export: list[KV]
    status: str = ""
    commands: tuple[CommandView, ...] = ()
    commands_note: str = "Command history unavailable for this source."


@dataclass
class Boot:
    title: str
    title_local: str
    subtitle: str
    checks: list[LogLine]
    fields: list[KV]
    session: list[KV]
    notice: str
    build: str


# ---------------------------------------------------------------- snapshot
@dataclass(frozen=True)
class WorkspacePathResult:
    run_id: str
    version: int
    route: tuple[str, ...]
    fidelity: float | None
    target: float
    target_met: bool | None
    topology_hash: str
    record_hash: str
    protocol: str = "werner_single_swap_v1"
    request_hash: str = ""
    outcome: str = "conditional_result"
    elapsed_s: float | None = None
    ticks: int | None = None
    memory_ages_s: tuple[float | None, ...] = ()
    generated_pairs: int = 0
    expired_pairs: int = 0


@dataclass
class Snapshot:
    at: str
    operator: str
    campaign: str
    counts: list[KV]                # "6 NODES UP" etc. for the chrome
    boot: Boot
    topology: Topology
    telemetry: Telemetry
    keys: Keys
    node: NodeDetail
    ledger: Ledger
    input_kind: Literal["service", "demo", "synthetic", "measured", "unavailable"] = "service"
    capabilities: SourceCapabilities = SourceCapabilities()
    observations: tuple[Observation, ...] = ()
    workspace_path: WorkspacePathResult | None = None
    optional_sync_state: NetworkStateSnapshot | None = None
    optional_compensation_states: tuple[NetworkStateSnapshot, ...] = ()
    optional_allocation_state: NetworkStateSnapshot | None = None
    optional_routing_state: NetworkStateSnapshot | None = None
    optional_keyrate_state: NetworkStateSnapshot | None = None
    # Live network state from the backend engine; None for sources without one.
    live: LiveView | None = None
    laboratory: dict | None = None
    # Backend identity/policy metadata only; never credentials or bearer tokens.
    security: dict | None = None
    # Presentation cadence/backpressure only; separate from scientific metrics.
    presentation_stats: dict = field(default_factory=dict)
