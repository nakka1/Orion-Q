"""Frozen, JSON-serialisable records of the live network state (design §2.3).

Everything the interface shows about the running network is one ``LiveState``.
The records are plain data: they hold what the backend processing produced and
nothing else. Three rules are enforced here rather than left to the callers:

* a value that was not produced is ``None`` — a number is never invented, and a
  non-finite float (NaN/inf) is refused instead of being serialised;
* enumerated fields (link status, decision kind, model state, ...) only accept
  the values the design names;
* every ``Decision`` carries ``physical_action_performed = False`` and a
  SHA-256 over its own canonical JSON, so a journal row can be re-verified.

``to_dict()`` produces plain JSON types (tuples become lists) and
``from_dict()`` is its exact inverse: ``X.from_dict(x.to_dict()) == x``.
Nothing in this module imports Qt, numpy or the physics packages.
"""
from __future__ import annotations

from dataclasses import dataclass, field, fields, is_dataclass
import hashlib
import json
import math
from typing import Any, Mapping

SCHEMA = "orion.network-live.v1"

LINK_STATUSES = ("up", "degraded", "down", "disabled", "isolated", "calibrating", "compromised", "no_input")
#: Statuses the *caller* imposes (topology / isolation / recalibration); the per-link
#: processing never produces them on its own and never fabricates observables under them.
IMPOSED_LINK_STATUSES = ("disabled", "isolated", "calibrating")
NODE_STATUSES = ("up", "degraded", "down", "isolated", "calibrating")
DEMAND_STATUSES = ("routed", "rerouted", "no_route", "blocked", "idle")
DECISION_KINDS = ("reroute", "compensation", "sync", "keyrate", "detector", "allocation", "calibration", "no_route", "ml_control")
MODEL_STATES = ("active", "fallback", "unavailable", "disabled")
MODEL_ROLES = ("compensation", "sync", "keyrate", "detector", "routing", "allocation")
SOURCE_KINDS = ("simulated", "measured", "none")
EVENT_SEVERITIES = ("info", "warning", "critical")
CALIBRATION_STATUSES = ("running", "complete", "failed")
#: Order of the full recalibration procedure (design §2.4).
CALIBRATION_PHASES = ("queued", "polarisation", "sync", "commissioning", "memory", "report", "complete")


# --------------------------------------------------------------------------
# canonical JSON and hashing
# --------------------------------------------------------------------------

def canonical_json(payload: Any) -> str:
    """Stable JSON: sorted keys, no whitespace, UTF-8 kept, NaN/inf refused."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def canonical_sha256(payload: Any) -> str:
    """SHA-256 (hex) of ``canonical_json(payload)``; the hash used for ``Decision`` records."""
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------
# generic (de)serialisation of the frozen records
# --------------------------------------------------------------------------

def _plain(value: Any) -> Any:
    # Most leaves of a live frame are plain scalars. Avoid dataclass/typing
    # introspection for each sample while retaining the generic fallback below
    # for subclasses and custom mappings. Nonfinite rejection is unchanged.
    kind = type(value)
    if kind in (str, int, bool, type(None)):
        return value
    if kind is float:
        if not math.isfinite(value):
            raise ValueError("non-finite number in a live record; a missing value must be None")
        return value
    if kind is dict:
        return {str(key): _plain(item) for key, item in value.items()}
    if kind in (tuple, list):
        return [_plain(item) for item in value]
    if is_dataclass(value) and not isinstance(value, type):
        return {item.name: _plain(getattr(value, item.name)) for item in fields(value)}
    if isinstance(value, (tuple, list)):
        return [_plain(item) for item in value]
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("non-finite number in a live record; a missing value must be None")
    return value


def _freeze(value: Any) -> Any:
    """Lists inside tuple fields become tuples, so ``from_dict(to_dict(x)) == x``."""
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    return value


def _number(value: Any, name: str, *, lower: float | None = None, upper: float | None = None) -> None:
    if value is None:
        return
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{name} must be a finite number or None")
    if (lower is not None and value < lower) or (upper is not None and value > upper):
        raise ValueError(f"{name} outside [{lower}, {upper}]")


def _choice(value: Any, allowed: tuple, name: str, *, optional: bool = False) -> None:
    if optional and value is None:
        return
    if value not in allowed:
        raise ValueError(f"{name} must be one of {allowed}, got {value!r}")


class _Record:
    """Mixin: ``to_dict`` / ``from_dict`` for the frozen dataclasses below.

    ``_tuples`` names the fields stored as tuples, ``_nested`` maps a field to the
    record class it holds (``_many`` when the field is a tuple of them).
    """

    _tuples: tuple = ()
    _nested: dict = {}
    _many: dict = {}

    def to_dict(self) -> dict:
        return _plain(self)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]):
        if not isinstance(data, Mapping):
            raise ValueError(f"{cls.__name__}: mapping required")
        names = {item.name for item in fields(cls) if item.init}
        constant = {item.name for item in fields(cls) if not item.init}
        unknown = set(data) - names - constant
        if unknown:
            raise ValueError(f"{cls.__name__}: unknown fields {sorted(unknown)}")
        values = {}
        for name in names:
            if name not in data:
                continue
            value = data[name]
            if name in cls._nested and value is not None:
                value = cls._nested[name].from_dict(value)
            elif name in cls._many:
                value = tuple(cls._many[name].from_dict(item) for item in value)
            elif name in cls._tuples:
                value = _freeze(value)
            values[name] = value
        record = cls(**values)
        for name in constant:
            if name in data and data[name] != getattr(record, name):
                raise ValueError(f"{cls.__name__}: {name} differs from the fixed value")
        return record

    def _tuple_fields(self) -> None:
        for name in self._tuples + tuple(self._many):
            value = getattr(self, name)
            if isinstance(value, list):
                object.__setattr__(self, name, _freeze(value))
            elif not isinstance(value, tuple):
                raise ValueError(f"{type(self).__name__}.{name} must be a tuple")


# --------------------------------------------------------------------------
# source, models, decisions, events
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class SourceInfo(_Record):
    """Where the raw observables come from. ``kind='none'`` means no feed at all."""

    kind: str
    label: str
    seed: int | None = None
    scenario: str | None = None
    note: str = ""

    def __post_init__(self):
        _choice(self.kind, SOURCE_KINDS, "source kind")


@dataclass(frozen=True)
class ModelStatus(_Record):
    """One model role and who is actually deciding for it.

    ``active``: the learned model (verified weights) is in use. ``fallback``: verified
    weights exist but the named baseline is acting (contract refusal or runtime faults).
    ``unavailable``: no verified weights; the named baseline is acting.
    ``disabled``: operator blocked learned control; the reference controller acts.
    """

    model_id: str
    role: str
    state: str
    reason: str = ""
    archive_sha256: str | None = None
    in_contract: bool | None = None
    inferences: int = 0
    faults: int = 0
    last_latency_ms: float | None = None

    def __post_init__(self):
        _choice(self.role, MODEL_ROLES, "model role")
        _choice(self.state, MODEL_STATES, "model state")
        _number(self.last_latency_ms, "last_latency_ms", lower=0.0)
        if type(self.inferences) is not int or type(self.faults) is not int or min(self.inferences, self.faults) < 0:
            raise ValueError("inferences and faults must be non-negative integers")


@dataclass(frozen=True)
class Decision(_Record):
    """Something a controller decided. Build with :meth:`create`; check with :meth:`verify`."""

    decision_id: str
    tick: int
    at_utc: str
    kind: str
    controller_id: str
    model_state: str
    subject: str
    summary: str
    inputs_sha256: str | None
    outcome: str
    in_contract: bool
    fallback_used: bool
    record_sha256: str
    physical_action_performed: bool = field(default=False, init=False)

    def __post_init__(self):
        _choice(self.kind, DECISION_KINDS, "decision kind")
        _choice(self.model_state, MODEL_STATES, "model state")
        if type(self.tick) is not int or self.tick < 0:
            raise ValueError("tick must be a non-negative integer")
        if type(self.in_contract) is not bool or type(self.fallback_used) is not bool:
            raise ValueError("in_contract and fallback_used must be booleans")

    def _body(self) -> dict:
        body = _plain(self)
        body.pop("record_sha256")
        return body

    @classmethod
    def create(cls, *, tick: int, at_utc: str, kind: str, controller_id: str, model_state: str, subject: str,
               summary: str, inputs_sha256: str | None, outcome: str, in_contract: bool,
               fallback_used: bool) -> "Decision":
        """A decision with a content-derived id and its record hash filled in."""
        content = {"tick": tick, "at_utc": at_utc, "kind": kind, "controller_id": controller_id,
                   "model_state": model_state, "subject": subject, "summary": summary,
                   "inputs_sha256": inputs_sha256, "outcome": outcome, "in_contract": in_contract,
                   "fallback_used": fallback_used}
        draft = cls(decision_id="dec-" + canonical_sha256(content)[:20], record_sha256="", **content)
        return cls(decision_id=draft.decision_id, record_sha256=canonical_sha256(draft._body()), **content)

    def verify(self) -> bool:
        """True when ``record_sha256`` is the hash of this record's own content."""
        return self.physical_action_performed is False and self.record_sha256 == canonical_sha256(self._body())


@dataclass(frozen=True)
class NetEvent(_Record):
    """A line of the network event feed (topology change, input event, status transition)."""

    event_id: str
    tick: int
    at_utc: str
    kind: str
    subject: str
    severity: str = "info"
    message: str = ""
    source: str = "engine"

    def __post_init__(self):
        _choice(self.severity, EVENT_SEVERITIES, "event severity")

    @classmethod
    def create(cls, *, tick: int, at_utc: str, kind: str, subject: str, severity: str = "info",
               message: str = "", source: str = "engine") -> "NetEvent":
        content = {"tick": tick, "at_utc": at_utc, "kind": kind, "subject": subject, "severity": severity,
                   "message": message, "source": source}
        return cls(event_id="evt-" + canonical_sha256(content)[:20], **content)


# --------------------------------------------------------------------------
# links
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class LinkHistory(_Record):
    """1 Hz rolling history (last 300 s). Parallel tuples; ``None`` where there was no value."""

    t_s: tuple = ()
    qber: tuple = ()
    fidelity: tuple = ()
    skr_bps: tuple = ()
    _tuples = ("t_s", "qber", "fidelity", "skr_bps")

    def __post_init__(self):
        self._tuple_fields()
        if len({len(self.t_s), len(self.qber), len(self.fidelity), len(self.skr_bps)}) != 1:
            raise ValueError("history series must have the same length")
        for name in ("t_s", "qber", "fidelity", "skr_bps"):
            for value in getattr(self, name):
                _number(value, f"history.{name}")


@dataclass(frozen=True)
class DetectorLive(_Record):
    """Attack detector output for the last settled period.

    ``label`` is the gated per-period class; ``alarm`` is the sustained *attack* alarm
    (three equal consecutive attack calls); ``baseline_alarm`` is the QBER-threshold alarm.
    All ``None`` while the session is still taking its own commissioning baseline.
    """

    label: str | None = None
    attack_probability: float | None = None
    alarm: bool | None = None
    baseline_alarm: bool | None = None

    def __post_init__(self):
        _number(self.attack_probability, "attack_probability", lower=0.0, upper=1.0)


@dataclass(frozen=True)
class QkdLive(_Record):
    protocol: str = "decoy_state_bb84"
    signal_intensity: float | None = None
    decoy_intensity: float | None = None
    clock_rate_hz: float | None = None
    signal_gain: float | None = None
    decoy_gain: float | None = None
    key_pool_bits: float | None = None
    discarded_bits: float | None = None
    last_action: str | None = None

    def __post_init__(self):
        for name in ("signal_intensity", "decoy_intensity", "clock_rate_hz", "signal_gain", "decoy_gain",
                     "key_pool_bits", "discarded_bits"):
            _number(getattr(self, name), name, lower=0.0)


@dataclass(frozen=True)
class LinkLive(_Record):
    link_id: str
    a: str
    b: str
    distance_km: float
    status: str
    qber: float | None = None
    fidelity: float | None = None
    skr_bps: float | None = None
    sifted_rate_hz: float | None = None
    sync_residual_ps: float | None = None
    gate_efficiency: float | None = None
    polarisation_residual_mrad: float | None = None
    stability: float | None = None
    wave: tuple = ()
    history: LinkHistory = field(default_factory=LinkHistory)
    detector: DetectorLive = field(default_factory=DetectorLive)
    qkd: QkdLive = field(default_factory=QkdLive)
    controllers: dict = field(default_factory=dict)
    classical_admission: Any = None
    provenance: dict = field(default_factory=dict)
    _tuples = ("wave",)
    _nested = {"history": LinkHistory, "detector": DetectorLive, "qkd": QkdLive}

    def __post_init__(self):
        self._tuple_fields()
        _choice(self.status, LINK_STATUSES, "link status")
        _number(self.distance_km, "distance_km", lower=0.0)
        _number(self.qber, "qber", lower=0.0, upper=1.0)
        _number(self.fidelity, "fidelity", lower=0.0, upper=1.0)
        _number(self.skr_bps, "skr_bps", lower=0.0)
        _number(self.sifted_rate_hz, "sifted_rate_hz", lower=0.0)
        _number(self.sync_residual_ps, "sync_residual_ps", lower=0.0)
        _number(self.gate_efficiency, "gate_efficiency", lower=0.0, upper=1.0)
        _number(self.polarisation_residual_mrad, "polarisation_residual_mrad", lower=0.0)
        _number(self.stability, "stability", lower=0.0, upper=1.0)
        for value in self.wave:
            _number(value, "wave sample")
        if not isinstance(self.controllers, dict) or not isinstance(self.provenance, dict):
            raise ValueError("controllers and provenance must be dicts")


# --------------------------------------------------------------------------
# nodes and demands (filled by the routing / calibration modules)
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class CalibrationLive(_Record):
    """A full recalibration, while it runs (``status='running'``) and once it is reported."""

    calibration_id: str
    status: str = "running"
    phase: str = "queued"
    progress: float | None = None
    started_at_tick: int | None = None
    completed_at_utc: str | None = None
    duration_s: float | None = None
    links: dict = field(default_factory=dict)
    memory_reset: bool | None = None
    report_sha256: str | None = None

    def __post_init__(self):
        _choice(self.status, CALIBRATION_STATUSES, "calibration status")
        _choice(self.phase, CALIBRATION_PHASES, "calibration phase")
        _number(self.progress, "progress", lower=0.0, upper=1.0)
        _number(self.duration_s, "duration_s", lower=0.0)
        if not isinstance(self.links, dict):
            raise ValueError("calibration links must be a dict keyed by link id")


@dataclass(frozen=True)
class NodeLive(_Record):
    node_id: str
    role: str
    kind: str | None
    status: str
    located: bool = False
    latitude: float | None = None
    longitude: float | None = None
    place: str | None = None
    links: tuple = ()
    memory_state: str | None = None
    pairs: tuple = ()
    temperature_c: float | None = None
    calibration: CalibrationLive | None = None
    calibration_count: int = 0
    routes_through: tuple = ()
    address: str | None = None
    port: int | None = None
    communicator: dict = field(default_factory=dict)
    _tuples = ("links", "pairs", "routes_through")
    _nested = {"calibration": CalibrationLive}

    def __post_init__(self):
        self._tuple_fields()
        _choice(self.status, NODE_STATUSES, "node status")
        _number(self.latitude, "latitude", lower=-90.0, upper=90.0)
        _number(self.longitude, "longitude", lower=-180.0, upper=180.0)
        _number(self.temperature_c, "temperature_c")
        if self.located != (self.latitude is not None and self.longitude is not None):
            raise ValueError("located must be True exactly when latitude and longitude are both set")
        for value in self.pairs:
            _number(value, "pair fidelity", lower=0.0, upper=1.0)
        if type(self.calibration_count) is not int or self.calibration_count < 0:
            raise ValueError("calibration_count must be a non-negative integer")


@dataclass(frozen=True)
class DemandLive(_Record):
    demand_id: str
    a: str
    b: str
    status: str
    route: tuple = ()
    route_links: tuple = ()
    fidelity: float | None = None
    success_rate: float | None = None
    policy_id: str | None = None
    in_contract: bool = False
    fallback_used: bool = False
    reason: str = ""
    changed_at_tick: int | None = None
    #: Fidelity threshold of the request (the planner's ``Request.min_fidelity``).
    min_fidelity: float | None = None
    #: The operator's end-to-end pair of this demand (``request_pair`` / ``purify_pair`` /
    #: ``consume_pair``), or ``None`` when none was requested. Plain JSON data.
    pair: dict | None = None
    #: Numbers of the last plan (episodes, steps, latency, deadline, trigger, blockers,
    #: contract reasons, Werner chain over the per-fibre estimates). Plain JSON data.
    detail: dict = field(default_factory=dict)
    _tuples = ("route", "route_links")

    def __post_init__(self):
        self._tuple_fields()
        _choice(self.status, DEMAND_STATUSES, "demand status")
        _number(self.fidelity, "fidelity", lower=0.0, upper=1.0)
        _number(self.success_rate, "success_rate", lower=0.0, upper=1.0)
        _number(self.min_fidelity, "min_fidelity", lower=0.0, upper=1.0)
        if self.pair is not None and not isinstance(self.pair, dict):
            raise ValueError("demand pair must be a dict or None")
        if not isinstance(self.detail, dict):
            raise ValueError("demand detail must be a dict")


# --------------------------------------------------------------------------
# the whole state
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class LiveState(_Record):
    tick: int
    at_utc: str
    network_time_s: float
    topology_version: int
    source: SourceInfo
    links: tuple = ()
    nodes: tuple = ()
    demands: tuple = ()
    decisions: tuple = ()
    models: tuple = ()
    events: tuple = ()
    #: Declared thresholds the RUNNING engine applies (``NetworkEngine.declared_policy``): the
    #: interface reads them here instead of importing a default from the backend modules.
    policy: dict = field(default_factory=dict)
    technologies: dict = field(default_factory=dict)
    communicators: dict = field(default_factory=dict)
    satellites: dict = field(default_factory=dict)
    #: Publication ordering only; independent of the scientific/simulation clock.
    #: Missing on older stored records and explicit legacy fixtures.
    engine_session_id: str | None = None
    publication_revision: int | None = None
    schema: str = field(default=SCHEMA, init=False)
    _nested = {"source": SourceInfo}
    _many = {"links": LinkLive, "nodes": NodeLive, "demands": DemandLive, "decisions": Decision,
             "models": ModelStatus, "events": NetEvent}

    def __post_init__(self):
        self._tuple_fields()
        if self.engine_session_id is not None and (not isinstance(self.engine_session_id, str)
                or len(self.engine_session_id) != 32
                or any(character not in "0123456789abcdef" for character in self.engine_session_id)):
            raise ValueError("engine_session_id must be a 32-character hexadecimal UUID")
        if self.publication_revision is not None and (type(self.publication_revision) is not int
                or self.publication_revision < 1):
            raise ValueError("publication_revision must be a positive integer")
        if type(self.tick) is not int or self.tick < 0:
            raise ValueError("tick must be a non-negative integer")
        _number(self.network_time_s, "network_time_s", lower=0.0)
        if not isinstance(self.policy, dict):
            raise ValueError("LiveState.policy must be a dict of declared thresholds")
        if not isinstance(self.technologies, dict):
            raise ValueError("LiveState.technologies must hold backend protocol records")
        for name, value in self.policy.items():
            if name == "ml_control_enabled":
                if type(value) is not bool:
                    raise ValueError("policy.ml_control_enabled must be boolean")
            elif name == "ml_control_revision":
                if type(value) is not int or value < 0:
                    raise ValueError("policy.ml_control_revision must be a non-negative integer")
            elif not isinstance(value, str):
                _number(value, f"policy.{name}")
        for name, kind in self._many.items():
            if any(not isinstance(item, kind) for item in getattr(self, name)):
                raise ValueError(f"LiveState.{name} must hold {kind.__name__} records")

    def to_json(self) -> str:
        return canonical_json(self.to_dict())

    @classmethod
    def from_json(cls, text: str) -> "LiveState":
        return cls.from_dict(json.loads(text))


__all__ = ["CALIBRATION_PHASES", "CALIBRATION_STATUSES", "DECISION_KINDS", "DEMAND_STATUSES", "EVENT_SEVERITIES",
           "IMPOSED_LINK_STATUSES", "LINK_STATUSES", "MODEL_ROLES", "MODEL_STATES", "NODE_STATUSES", "SCHEMA",
           "SOURCE_KINDS", "CalibrationLive", "Decision", "DemandLive", "DetectorLive", "LinkHistory", "LinkLive",
           "LiveState", "ModelStatus", "NetEvent", "NodeLive", "QkdLive", "SourceInfo", "canonical_json",
           "canonical_sha256"]
