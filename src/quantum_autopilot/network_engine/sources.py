"""Input sources of the network engine: the only place where anything is simulated.

Product rule: *the only simulation allowed is the generation of input data*. An
``InputSource`` delivers, per tick (1 s of network time) and per fibre, the raw
observables a real link would publish; everything downstream (``links.py``) is the
reviewed processing and never learns whether a frame was simulated or measured.

What ``SimulatedInputSource`` generates, per fibre, from the author's plants
----------------------------------------------------------------------------
* **Polarisation monitor** — the aerial-fibre plant of ``control_plane.polarisation_plant``
  (thermal Ornstein-Uhlenbeck drift, dT/dt coupling, 60 Hz vibration with harmonics,
  Poisson jumps) at its native 1 ms sampling; published as the monitored rotation
  estimates (two probe states, binomial photon counts) and sensor covariates every 2 ms.
  The plant has no span parameter in the author's code, so it is the same operating
  point for every fibre length (none is invented here).
* **Timing** — ``qnet_qkd.qkd.timing.ClockModel`` at the fibre's real length, driven by
  the cable temperature; published as the offset estimate of a 2000-photon histogram.
* **Decoy-state BB84 session** — one settling period per tick: link conditions of the
  author's benchmark (``keyrate_loop.LINK``) at the fibre's real length, optical
  misalignment from the author's drift model driven by the cable temperature
  (``session._misalignment_series``, residual fraction 0.2), the receiver model of the
  key-rate plant (gate, inter-symbol error, dead time, afterpulsing), the active
  event/attack applied to the per-photon-number yields (``qnet_qkd.qkd.attacks``), and
  finite counting statistics (``sample_observables``). Published as the 17 public
  columns of ``model_intake.detector_session.COLUMNS``.

The three plants run side by side, as the author validated them; they are *not*
coupled to each other (the session carries the author's own model of what the
compensator leaves behind). Two things connect them, both the author's own terms:
a ``thermal_drift`` event raises the one cable temperature that drives the session
misalignment, the clock's thermal delay and — through the polarisation plant's dT/dt
coupling — the monitored fibre rotation; and the transmitter setting chosen by the
key-rate controller is fed back (``actuate``), exactly as in ``KeyRateEnv``.

Streaming. The author's plants simulate a whole run at once. Here each plant keeps its
state between ticks; the random draws are made segment by segment in the author's
order, so the first segment of a stream is bit-identical to the author's function with
the same generator (tests check this) and later segments continue the same state.

Session episodes. The author's session takes the optical misalignment as 20 % of the
QBER an *uncompensated* fibre would show since the start of the episode, and the
detector takes the first 60 periods of the episode as its baseline; both were studied
on 600-period episodes (``SessionConfig.duration_s``). Left running, the uncompensated
rotation random-walks without bound and the session QBER wanders to 5-20 % within
15-30 minutes — outside anything the author validated. The stream is therefore a
sequence of author episodes: after ``session_periods`` (600) periods a fibre's session
is commissioned anew (new ``epoch_tick``, aligned state, new detector baseline), as a
deployed link is realigned on a schedule. The cable temperature and a relaxing thermal
excursion carry over; a rollover waits for an attack or loss event in progress to end.
The first episode of each fibre is shortened by a per-fibre amount (hash of seed and
fibre id) so that the fibres of a network do not all take their baseline in the same
minute. ``session_periods=None`` keeps one unbounded session (the author's model left
running); a recalibration then restores the link.

Determinism. Every random stream of a fibre is seeded from
``sha256(seed, link_id, epoch_tick, purpose)``: a fibre produces the same frames
whatever other fibres exist and in whatever order they are visited. ``epoch_tick`` is
the tick at which the fibre was (re)commissioned: when it first appears, when its
length changes, when it is reactivated, or when recalibration asks for it.

Demo scenario (``scenario="demo"``): a timeline of INPUT events that repeats every
``DEMO_LOOP_S`` = 345 s of network time. Fibres are ranked by ``sha256(seed, link_id)``
and event *i* goes to the fibre of rank ``i mod n`` (any topology with one fibre or more):

    t =  65 s  thermal_drift      rank 0   12 K excursion, 60 s rise      for 60 s
    t = 135 s  loss_event         rank 1   3 dB extra loss                for 30 s
    t = 185 s  intercept_resend   rank 2   24.4 % of pulses intercepted   for 40 s
    t = 245 s  pns                rank 3   56 % of single photons blocked for 35 s
    t = 295 s  detector_blinding  rank 4   forced clicks 1.38x the gain   for 30 s

with quiet periods in between (the first 65 s cover the 60 periods every session needs
as its own commissioning baseline). A scheduled event that would fall inside a fibre's
session baseline waits until that baseline is complete; operator injections
(``inject``) are applied at the next tick.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
import hashlib
import math
from typing import Any, Mapping, Protocol, Sequence, runtime_checkable

import numpy as np

from qnet_qkd.physics.decoy import (apply_detector_saturation, decoy_key_rate_hz, sample_observables,
                                    single_photon_bounds)
from qnet_qkd.physics.fiber import PolarizationDriftModel, stokes_qber
from qnet_qkd.qkd.attacks import BeamSplitting, DetectorBlinding, InterceptResend, PhotonNumberSplitting
from qnet_qkd.qkd.session import Event, SessionConfig
from qnet_qkd.qkd.timing import ClockModel, MeasurementConfig, gate_efficiency, intersymbol_error, timing_spread

from ..control_plane import keyrate_loop as kr
from ..control_plane import polarisation_plant as plant
from ..control_plane import sync_loop
from ..model_intake import detector_session
from .state import SourceInfo

TICK_S = 1.0
SESSION_COLUMNS = detector_session.COLUMNS
EVENT_KINDS = ("thermal_drift", "loss_event", "intercept_resend", "pns", "detector_blinding", "beam_splitting")
ATTACK_KINDS = ("intercept_resend", "pns", "detector_blinding", "beam_splitting")
EVENT_PROFILES = ("step", "ramp", "intermittent")
#: Periods at the start of every session that are its own calibration (the detector's baseline).
BASELINE_PERIODS = 60
#: The author's session study: ``SessionConfig`` defaults (episode length, residual fraction, ambient).
_SESSION = SessionConfig()
SESSION_SEGMENT_PERIODS = int(round(_SESSION.duration_s / _SESSION.period_s))
RESIDUAL_FRACTION = _SESSION.residual_fraction
POLARISATION_STRIDE = int(round(plant.CONTROL_PERIOD_S / plant.SAMPLE_INTERVAL_S))
POLARISATION_SAMPLES = int(round(TICK_S / plant.SAMPLE_INTERVAL_S))
DEMO_LOOP_S = 345


# --------------------------------------------------------------------------
# public records
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class InputEvent:
    """Something that happens to the INPUT of one fibre (never to the processing).

    ``strength`` in (0, 1] places the event inside the severity range the author's
    dataset generator draws from (``qnet_qkd.datagen.qkd.draw_event``):

    * ``thermal_drift``      4 … 14 K temperature excursion (rise time ``THERMAL_RISE_S``)
    * ``loss_event``         1 … 5 dB extra loss
    * ``intercept_resend``   2 … 30 % of the pulses intercepted
    * ``pns``                20 … 80 % of the single-photon pulses blocked
    * ``detector_blinding``  forced click probability 1.05 … 1.60 x the honest gain
    * ``beam_splitting``     20 … 80 % tapped (no observable signature, by construction)

    ``profile`` is the author's ``Event.profile`` (step, ramp, intermittent). The event
    ends after ``duration_s``; a thermal excursion then relaxes with its own rise time.
    """

    kind: str
    link_id: str
    strength: float = 0.5
    duration_s: float = 60.0
    profile: str = "step"

    def __post_init__(self):
        if self.kind not in EVENT_KINDS:
            raise ValueError(f"unknown input event {self.kind!r}; expected one of {EVENT_KINDS}")
        if not isinstance(self.link_id, str) or not self.link_id:
            raise ValueError("link_id required")
        if self.profile not in EVENT_PROFILES:
            raise ValueError(f"unknown profile {self.profile!r}; expected one of {EVENT_PROFILES}")
        for name, value in (("strength", self.strength), ("duration_s", self.duration_s)):
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise ValueError(f"{name} must be a finite number")
        if not 0.0 < self.strength <= 1.0:
            raise ValueError("strength must be in (0, 1]")
        if not 1.0 <= self.duration_s <= 3600.0:
            raise ValueError("duration_s must be between 1 s and 1 h")


@dataclass(frozen=True)
class TimelineEvent:
    """One entry of a scenario: at ``at_s`` of every loop, an event on the fibre of rank ``rank``."""

    at_s: int
    kind: str
    rank: int
    strength: float
    duration_s: float
    profile: str = "step"


@dataclass(frozen=True)
class ActiveEvent:
    """An input event as the frame reports it (ground truth of the input, never a feature)."""

    kind: str
    link_id: str
    strength: float
    profile: str
    origin: str               # "timeline" | "operator"
    started_tick: int
    ends_tick: int
    severity: float           # in the event's own units (K, dB, fraction, gain multiple)
    weight: float             # the author's Event.strength in force this tick, 0..1
    phase: str                # "started" | "active" | "ended"


@dataclass(frozen=True)
class DomainNote:
    """Whether this fibre is inside the domain a component was validated on."""

    component: str            # "detector" | "keyrate" | "sync" | "compensation" | "session"
    in_domain: bool
    detail: str


@dataclass(frozen=True)
class Commissioning:
    """What commissioning a fibre of this length leaves behind (``keyrate_loop.make_link``)."""

    length_km: float
    signal_intensity: float
    decoy_intensity: float
    vacuum_probability: float
    signal_probability: float
    clock_rate_hz: float
    calibration_gain: float
    calibration_rate_hz: float
    dark_count_probability: float
    misalignment_error: float
    dead_time_s: float
    gate_width_s: float
    timing_spread_s: float
    sifting_factor: float

    def to_dict(self) -> dict:
        return {name: getattr(self, name) for name in self.__dataclass_fields__}


@dataclass(frozen=True)
class PolarisationInput:
    """One tick of the polarisation monitor, at the control cadence (2 ms)."""

    time_s: np.ndarray            # (n,) seconds since the fibre was commissioned
    measured: np.ndarray          # (n, 3, 3) monitored rotation estimates, each in SO(3)
    covariates: np.ndarray        # (n, 3) temperature (C), dT/dt (K/s), vibration RMS (rad)
    operating_point: str
    control_period_s: float = plant.CONTROL_PERIOD_S
    latency_s: float = plant.LATENCY_S
    photons_per_basis: int = plant.PHOTONS_PER_BASIS


@dataclass(frozen=True)
class SyncInput:
    """One offset estimate of the arrival-time histogram, and the cable thermometer."""

    time_s: float
    measured_offset_s: float
    temperature_c: float
    update_period_s: float = sync_loop.SYNC_PERIOD_S
    latency_s: float = sync_loop.SYNC_LATENCY_S
    photons: int = sync_loop.PHOTONS


@dataclass(frozen=True)
class QkdInput:
    """One settling period of the decoy-state session: the 17 public columns, plus the
    transmitter's own state and the commissioning record every deployed link has."""

    row: dict
    signal_probability: float
    vacuum_probability: float
    clock_rate_hz: float
    period_s: float
    commissioning: Commissioning


@dataclass(frozen=True)
class LinkInput:
    link_id: str
    a: str
    b: str
    distance_km: float
    epoch_tick: int               # tick at which this fibre was (re)commissioned
    period: int                   # settling periods since then (0, 1, 2, ...)
    polarisation: PolarisationInput
    sync: SyncInput
    qkd: QkdInput
    domain_notes: tuple = ()
    #: Hidden truth of the simulated plant, for validation and scoring only. The
    #: processing in ``links.py`` never reads it (a test enforces that).
    reference: dict = field(default_factory=dict, compare=False, repr=False)
    # Optical activity evidence belongs to the quantum port, not the telemetry
    # payload. These are statistical model/record events, never physical qubits.
    quantum_record: dict | None = field(default=None, compare=False)


@dataclass(frozen=True)
class InputFrame:
    tick: int
    network_time_s: float
    kind: str                     # "simulated" | "measured" | "none"
    links: dict = field(default_factory=dict)        # link_id -> LinkInput
    events: tuple = ()            # ActiveEvent records (input timeline; not a feature)


@dataclass(frozen=True)
class LinkCommand:
    """What the processing asks of one fibre's transmitter for the next period."""

    link_id: str
    keyrate_action: int = 0       # index into qnet_qkd.qkd.keyrate.ACTIONS; 0 = hold
    recommission: bool = False    # recalibration: restart the session at the commissioned settings


@runtime_checkable
class InputSource(Protocol):
    kind: str
    label: str

    def frame(self, tick: int, topology) -> InputFrame: ...

    def inject(self, event: InputEvent) -> None: ...

    def actuate(self, commands: Mapping[str, LinkCommand]) -> None: ...

    def info(self) -> SourceInfo: ...


# --------------------------------------------------------------------------
# commissioning and validated domains
# --------------------------------------------------------------------------

_COMMISSIONED: dict = {}


def _settings_for(length_km: float):
    """Commissioned transmitter settings and calibration of a span (cached per length)."""
    key = float(length_km)
    if key not in _COMMISSIONED:
        if len(_COMMISSIONED) >= 512:
            _COMMISSIONED.pop(next(iter(_COMMISSIONED)))
        env = kr.make_link(key)                      # author's environment at the benchmark point
        calibration = kr.calibration(env)
        settings = env.settings
        record = Commissioning(
            length_km=key, signal_intensity=float(settings.signal_intensity),
            decoy_intensity=float(settings.decoy_intensity),
            vacuum_probability=float(settings.vacuum_probability),
            signal_probability=float(settings.signal_probability), clock_rate_hz=float(settings.clock_rate_hz),
            calibration_gain=float(calibration.calibration_gain),
            calibration_rate_hz=float(calibration.calibration_rate_hz),
            dark_count_probability=float(calibration.dark_count_probability),
            misalignment_error=float(calibration.misalignment_error), dead_time_s=float(calibration.dead_time_s),
            gate_width_s=float(env.link.gate_width_s), timing_spread_s=timing_spread(kr.BUDGET, key),
            sifting_factor=float(settings.sifting_factor))
        _COMMISSIONED[key] = (settings, record)
    return _COMMISSIONED[key]


def commission(length_km: float) -> Commissioning:
    """Decoy-BB84 commissioning of a span of this length.

    The author's procedure (``keyrate_loop.commissioned`` / ``make_link``): a joint grid
    search of signal intensity and clock against the link budget, then the calibration
    gain and key rate of that operating point. Deterministic; no random draw.
    """
    if isinstance(length_km, bool) or not isinstance(length_km, (int, float)) or not math.isfinite(length_km) \
            or length_km <= 0:
        raise ValueError("length_km must be a positive finite number")
    return _settings_for(length_km)[1]


def domain_notes(length_km: float, commissioning: Commissioning | None = None) -> tuple:
    """Honest statement of where this span sits relative to each validated domain.

    Nothing is clamped: a 7 km metro fibre is simulated and processed at 7 km, and the
    notes say which models were never validated there.
    """
    commissioning = commissioning or commission(length_km)
    length = float(length_km)
    low, high = detector_session.SPAN_KM
    trained = detector_session.TRAINED
    reasons = []
    if not low <= length <= high:
        reasons.append(f"span {length:g} km outside the trained {low:g}-{high:g} km")
    pulses = commissioning.clock_rate_hz * TICK_S
    if (commissioning.signal_intensity != trained["signal_intensity"]
            or commissioning.decoy_intensity != trained["decoy_intensity"] or pulses != trained["pulses"]):
        reasons.append(f"transmitter at the commissioned point (mu {commissioning.signal_intensity:.3g}, "
                       f"nu {commissioning.decoy_intensity:.3g}, {pulses:.3g} pulses/period) instead of the "
                       f"training configuration (mu {trained['signal_intensity']:g}, nu "
                       f"{trained['decoy_intensity']:g}, {trained['pulses']:g} pulses/period)")
    # The session stream uses the receiver of the key-rate plant (so that the transmitter can be
    # commissioned and controlled); the detector's training sessions had neither dead time nor
    # afterpulsing. Afterpulsing lifts the vacuum yield from the dark-count level (~1e-8) to about 1 % of
    # the gain, which is where the blinding attack leaves its signature.
    reasons.append("receiver with dead time and afterpulsing (key-rate plant), absent from the detector's "
                   "training sessions")
    notes = [DomainNote("detector", False, "; ".join(reasons))]
    low, high = kr.SPAN_RANGE_KM
    inside = low <= length <= high
    notes.append(DomainNote("keyrate", inside, "inside the 25-80 km actor contract" if inside else
                            f"span {length:g} km outside the {low:g}-{high:g} km actor contract"))
    inside = length == sync_loop.TRAINED_SPAN_KM
    notes.append(DomainNote("sync", inside, "the 50 km span the synchroniser was trained on" if inside else
                            f"span {length:g} km differs from the {sync_loop.TRAINED_SPAN_KM:g} km the "
                            "synchroniser was trained on"))
    notes.append(DomainNote("compensation", True, "the author's polarisation plant has no span parameter; the "
                                                  "declared operating point is used for every fibre"))
    return tuple(notes)


# --------------------------------------------------------------------------
# seeds
# --------------------------------------------------------------------------

def stream_seed(seed: int, link_id: str, epoch_tick: int, purpose: str) -> int:
    """Stable 63-bit seed of one random stream of one fibre (no Python hash randomisation)."""
    digest = hashlib.sha256(f"orion.network-input.v1|{int(seed)}|{link_id}|{int(epoch_tick)}|{purpose}"
                            .encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") >> 1


def link_rank_order(seed: int, link_ids: Sequence[str]) -> tuple:
    """Fibres ordered by ``sha256(seed, link_id)``: how scenarios pick their targets."""
    return tuple(sorted(link_ids, key=lambda link_id: hashlib.sha256(
        f"orion.network-input.v1|{int(seed)}|rank|{link_id}".encode("utf-8")).hexdigest()))


# --------------------------------------------------------------------------
# streaming form of the author's polarisation drift model
# --------------------------------------------------------------------------

def session_drift_parameters() -> plant.FibreParameters:
    """The drift model the author's session uses (``SessionConfig().drift``), as plant parameters."""
    model = PolarizationDriftModel()
    if model.thermal_components:
        raise ValueError("session drift with thermal_components is not supported by the stream")
    return plant.FibreParameters(
        thermal_drift_rate_rad_per_s=float(model.thermal_drift_rate_rad_per_s),
        thermal_correlation_time_s=float(model.thermal_correlation_time_s),
        vibration_amplitude_rad=float(model.vibration_amplitude_rad),
        vibration_frequency_hz=float(model.vibration_frequency_hz),
        vibration_bandwidth_hz=float(model.vibration_bandwidth_hz),
        vibration_num_tones=int(model.vibration_num_tones), jump_rate_per_s=float(model.jump_rate_per_s),
        jump_scale_rad=float(model.jump_scale_rad),
        thermal_per_kelvin_rad_per_s=float(model.thermal_per_kelvin_rad_per_s),
        vibration_harmonics=tuple(float(value) for value in model.vibration_harmonics))


class DriftStream:
    """The author's drift model (``polarisation_plant.simulate`` / ``PolarizationDriftModel``), streamed.

    Same equations and, per segment of ``segment_samples``, the same draws in the same
    order as the author's whole-run function: OU start and shocks, the dT/dt axis, the
    vibration tones, the Poisson jumps (and, when the temperature is synthesised, its
    slow noise first). The first segment therefore equals ``simulate(parameters,
    segment_samples * dt_s, seed)`` exactly; afterwards the rate, the tones, the
    accumulated rotation and the temperature walk carry on.

    ``external_temperature=False``: the temperature is the author's synthetic series and
    a whole segment is produced per call (``advance()``).
    ``external_temperature=True``: the caller supplies the temperature sample by sample,
    with the next one (``advance_one``), as the session does with the cable thermometer.
    """

    def __init__(self, parameters: plant.FibreParameters, dt_s: float, segment_samples: int,
                 generator: np.random.Generator, *, external_temperature: bool = False):
        if type(parameters) is not plant.FibreParameters or dt_s <= 0 or segment_samples < 2:
            raise ValueError("typed parameters, positive step and a segment of two samples or more required")
        self.parameters, self.dt_s, self.segment = parameters, float(dt_s), int(segment_samples)
        self.generator, self.external = generator, bool(external_temperature)
        self.index = 0                              # global sample index
        self._quaternion = (1.0, 0.0, 0.0, 0.0)
        self._current = None                        # OU angular velocity
        self._axis = None                           # fixed birefringence axis of the dT/dt drive
        self._tones = None                          # [(weight, frequencies, phases), ...]
        self._jumps: list = []                      # pending (time_s, quaternion)
        self._shocks = None
        self._segment_start = 0
        self._walk = 0.0                            # raw temperature random walk (synthetic mode)
        self._walk_offset = None
        self._last_temperature = None
        self._previous_external = None
        self._decay = float(np.exp(-self.dt_s / parameters.thermal_correlation_time_s))
        self._increment_std = parameters.thermal_drift_rate_rad_per_s * np.sqrt(max(1.0 - self._decay ** 2, 0.0))

    # -- draws, in the author's order ----------------------------------------

    def _begin_segment(self):
        parameters, generator, count = self.parameters, self.generator, self.segment
        first = self._current is None
        self._segment_start = self.index
        temperature = None
        if not self.external:
            walk = np.cumsum(generator.normal(0.0, 0.01, size=count))
            if first:
                self._walk_offset = float(walk.mean())
            noise = self._walk + walk - self._walk_offset
            self._walk = float(self._walk + walk[-1])
            time_s = np.arange(self.index, self.index + count, dtype=float) * self.dt_s
            temperature = 20.0 + 3.0 * np.sin(2 * np.pi * time_s / 86400.0) + 0.1 * noise
        if first:
            self._current = generator.normal(0.0, parameters.thermal_drift_rate_rad_per_s, size=3)
        self._shocks = generator.normal(0.0, self._increment_std, size=(count, 3))
        if first:
            if parameters.thermal_per_kelvin_rad_per_s:
                axis = generator.normal(size=3)
                self._axis = axis / np.linalg.norm(axis)
            self._tones = []
            amplitude, tones = parameters.vibration_amplitude_rad, parameters.vibration_num_tones
            if amplitude > 0:
                weights = np.asarray(parameters.vibration_harmonics, dtype=float)
                weights = weights / float(np.sqrt(np.sum(weights ** 2)))
                for order, weight in enumerate(weights, start=1):
                    if weight <= 0:
                        continue
                    frequencies = (parameters.vibration_frequency_hz * order + parameters.vibration_bandwidth_hz
                                   * (generator.random((3, tones)) - 0.5))
                    phases = generator.uniform(0.0, 2 * np.pi, size=(3, tones))
                    self._tones.append((weight, frequencies, phases))
        if parameters.jump_rate_per_s > 0:
            duration = count * self.dt_s
            jumps = generator.poisson(parameters.jump_rate_per_s * duration)
            if jumps:
                times = np.sort(generator.uniform(0.0, duration, size=jumps)) + self._segment_start * self.dt_s
                axes = generator.normal(size=(jumps, 3))
                quaternions = plant._quaternions(axes, generator.exponential(parameters.jump_scale_rad, size=jumps))
                self._jumps.extend((float(time), tuple(float(v) for v in quaternion))
                                   for time, quaternion in zip(times, quaternions))
        return temperature

    # -- the model -------------------------------------------------------------

    def _rates(self, offset: int, count: int) -> np.ndarray:
        """Exactly discretised OU angular velocity for ``count`` samples of the open segment."""
        decay = self._decay
        c0, c1, c2 = (float(value) for value in self._current)
        rows = self._shocks[offset:offset + count].tolist()
        out = []
        for s0, s1, s2 in rows:
            c0, c1, c2 = decay * c0 + s0, decay * c1 + s1, decay * c2 + s2
            out.append((c0, c1, c2))
        self._current = np.array([c0, c1, c2])
        return np.array(out, dtype=float)

    def _accumulate(self, step_vectors: np.ndarray, time_s: np.ndarray) -> np.ndarray:
        increments = plant._quaternions(step_vectors, np.linalg.norm(step_vectors, axis=-1)).tolist()
        times = time_s.tolist()
        w, x, y, z = self._quaternion
        jumps = self._jumps
        cumulative = []
        for (aw, ax, ay, az), now in zip(increments, times):
            w, x, y, z = (aw * w - ax * x - ay * y - az * z,
                          aw * x + ax * w + ay * z - az * y,
                          aw * y - ax * z + ay * w + az * x,
                          aw * z + ax * y - ay * x + az * w)
            while jumps and jumps[0][0] <= now:
                jw, jx, jy, jz = jumps.pop(0)[1]
                w, x, y, z = (jw * w - jx * x - jy * y - jz * z,
                              jw * x + jx * w + jy * z - jz * y,
                              jw * y - jx * z + jy * w + jz * x,
                              jw * z + jx * y - jy * x + jz * w)
            cumulative.append((w, x, y, z))
        self._quaternion = (w, x, y, z)
        return np.array(cumulative, dtype=float)

    def _vibration(self, time_s: np.ndarray) -> np.ndarray:
        vibration = np.zeros((3, time_s.size), dtype=float)
        amplitude, tones = self.parameters.vibration_amplitude_rad, self.parameters.vibration_num_tones
        for weight, frequencies, phases in self._tones:
            phase = 2 * np.pi * frequencies[:, :, None] * time_s[None, None, :] + phases[:, :, None]
            vibration += amplitude * weight * np.sqrt(2.0 / tones) * np.sin(phase).sum(axis=1)
        return vibration.T

    def advance(self, temperature_offset=None) -> dict:
        """One whole segment with the synthetic temperature (the kilohertz polarisation plant).

        ``temperature_offset`` (K, one value per sample) is an excursion added to the
        author's synthetic temperature before its gradient is taken: it reaches the fibre
        only through the author's own dT/dt coupling. ``None`` leaves the author's series.
        """
        if self.external:
            raise ValueError("this stream takes its temperature from the caller; use advance_one")
        temperature = self._begin_segment()
        count, dt_s = self.segment, self.dt_s
        if temperature_offset is not None:
            temperature = temperature + np.asarray(temperature_offset, dtype=float).reshape(count)
        time_s = np.arange(self.index, self.index + count, dtype=float) * dt_s
        if self._last_temperature is None:
            gradient = np.gradient(temperature, dt_s)
        else:
            gradient = np.gradient(np.concatenate([[self._last_temperature], temperature]), dt_s)[1:]
        self._last_temperature = float(temperature[-1])
        rate = self._rates(0, count)
        if self._axis is not None:
            rate += self.parameters.thermal_per_kelvin_rad_per_s * gradient[:, None] * self._axis
        quaternions = self._accumulate(rate * dt_s, time_s)
        vibration = self._vibration(time_s)
        self.index += count
        return {"time_s": time_s, "quaternion": quaternions, "vibration": vibration, "temperature_c": temperature,
                "temperature_rate": gradient, "vibration_rms": np.linalg.norm(vibration, axis=-1)}

    def advance_one(self, temperature_c: float, next_temperature_c: float) -> dict:
        """One sample driven by the caller's thermometer; ``next_temperature_c`` gives dT/dt its
        central difference, as ``np.gradient`` does over a whole episode."""
        if not self.external:
            raise ValueError("this stream synthesises its own temperature; use advance")
        if self._shocks is None or self.index - self._segment_start >= self.segment:
            self._begin_segment()
        dt_s = self.dt_s
        time_s = np.array([self.index], dtype=float) * dt_s
        if self._previous_external is None:
            gradient = (next_temperature_c - temperature_c) / dt_s
        else:
            gradient = (next_temperature_c - self._previous_external) / (2.0 * dt_s)
        self._previous_external = float(temperature_c)
        rate = self._rates(self.index - self._segment_start, 1)
        if self._axis is not None:
            rate += self.parameters.thermal_per_kelvin_rad_per_s * np.array([gradient])[:, None] * self._axis
        quaternions = self._accumulate(rate * dt_s, time_s)
        vibration = self._vibration(time_s)
        self.index += 1
        return {"time_s": time_s, "quaternion": quaternions, "vibration": vibration}


def rotation_from(block: dict) -> np.ndarray:
    """The fibre's full Stokes rotation at each sample (the plant's ``rotation``)."""
    return np.stack([plant._rotate_about_axis(plant._rotate_by_quaternion(block["quaternion"], axis),
                                              block["vibration"]) for axis in plant.IDENTITY], axis=-1)


def launch_qber_from(block: dict) -> np.ndarray:
    """Misalignment QBER of the launch state (1, 0, 0), as ``PolarizationTrajectory.qber``."""
    launch = np.array([1.0, 0.0, 0.0])
    stokes = plant._rotate_about_axis(plant._rotate_by_quaternion(block["quaternion"], launch), block["vibration"])
    return stokes_qber(stokes, launch)


# --------------------------------------------------------------------------
# events
# --------------------------------------------------------------------------

THERMAL_RISE_S = 60.0           # the fastest rise the author's generator draws (60 … 300 s)
_SEVERITY = {"thermal_drift": (4.0, 14.0), "loss_event": (1.0, 5.0), "intercept_resend": (0.02, 0.30),
             "pns": (0.2, 0.8), "detector_blinding": (1.05, 1.60), "beam_splitting": (0.2, 0.8)}
_VACUUM_LEAK = (5.0e-5, 5.0e-4)


def event_severity(kind: str, strength: float) -> float:
    """``strength`` in (0, 1] mapped linearly onto the author's severity range of that class."""
    low, high = _SEVERITY[kind]
    return low + (high - low) * float(strength)


def build_author_event(event: InputEvent, honest_gain: float) -> tuple:
    """The author's ``Event`` for an input event, and its severity in the event's own units."""
    severity = event_severity(event.kind, event.strength)
    shape = {"profile": event.profile, "ramp_s": min(180.0, max(event.duration_s / 2.0, 1.0)),
             "cycle_s": min(60.0, max(event.duration_s / 2.0, 2.0)), "duty_cycle": 0.5}
    if event.kind == "thermal_drift":
        return Event(label=event.kind, temperature_ramp_k=severity, temperature_ramp_s=THERMAL_RISE_S), severity
    if event.kind == "loss_event":
        return Event(label=event.kind, excess_loss_step_db=severity, **shape), severity
    if event.kind == "intercept_resend":
        return Event(label=event.kind, attack=InterceptResend(fraction=severity), **shape), severity
    if event.kind == "pns":
        return Event(label=event.kind, attack=PhotonNumberSplitting(blocked_fraction=severity), **shape), severity
    if event.kind == "detector_blinding":
        leak = _VACUUM_LEAK[0] + (_VACUUM_LEAK[1] - _VACUUM_LEAK[0]) * float(event.strength)
        attack = DetectorBlinding(click_probability=min(honest_gain * severity, 1.0), vacuum_leak=leak)
        return Event(label=event.kind, attack=attack, **shape), severity
    return Event(label=event.kind, attack=BeamSplitting(tapped_fraction=severity), attack_is_detectable=False,
                 **shape), severity


@dataclass
class _RunningEvent:
    request: InputEvent
    author: Event
    severity: float
    origin: str
    onset_period: int             # in the fibre's own session time
    end_period: int
    started_tick: int
    reported_start: bool = False

    def weight(self, period: int) -> float:
        if not self.onset_period <= period < self.end_period:
            return 0.0
        return float(self.author.strength(np.array([float(period)]), float(self.onset_period))[0])

    def excursion(self, period: float) -> float:
        """Temperature excursion (K): the author's saturating rise, then relaxation after the end."""
        rise, tau = self.author.temperature_ramp_k, max(self.author.temperature_ramp_s, 1e-9)
        if not rise or period < self.onset_period:
            return 0.0
        if period < self.end_period:
            return rise * (1.0 - math.exp(-(period - self.onset_period) / tau))
        peak = rise * (1.0 - math.exp(-(self.end_period - self.onset_period) / tau))
        return peak * math.exp(-(period - self.end_period) / tau)

    def finished(self, period: int) -> bool:
        if period < self.end_period:
            return False
        return abs(self.excursion(period)) < 1e-3


def replace_event(event: "_RunningEvent", elapsed: int) -> "_RunningEvent":
    """The same event seen from a session that started ``elapsed`` periods later."""
    return _RunningEvent(event.request, event.author, event.severity, event.origin, event.onset_period - elapsed,
                         event.end_period - elapsed, event.started_tick, True)


DEMO_TIMELINE = (
    TimelineEvent(65, "thermal_drift", 0, 0.8, 60.0),
    TimelineEvent(135, "loss_event", 1, 0.5, 30.0),
    TimelineEvent(185, "intercept_resend", 2, 0.8, 40.0),
    TimelineEvent(245, "pns", 3, 0.6, 35.0),
    TimelineEvent(295, "detector_blinding", 4, 0.6, 30.0),
)
SCENARIOS = {"demo": (DEMO_TIMELINE, DEMO_LOOP_S), "quiet": ((), DEMO_LOOP_S)}


# --------------------------------------------------------------------------
# one fibre's plants
# --------------------------------------------------------------------------

class _LinkPlant:
    """The three author plants of one fibre, from one commissioning to the next."""

    def __init__(self, seed: int, link, epoch_tick: int, operating_point: str, episode_periods=None,
                 previous: "_LinkPlant | None" = None):
        self.link_id, self.a, self.b = link.link_id, link.left_node_id, link.right_node_id
        self.distance_km = float(link.distance_km)
        self.epoch_tick, self.operating_point = int(epoch_tick), operating_point
        self.period = 0
        #: Periods after which this session is commissioned anew (``None``: never).
        self.episode_periods = episode_periods
        self.deferred: list = []          # scheduled events waiting for the session baseline to complete
        self.settings, self.commissioning = _settings_for(self.distance_km)
        self.notes = domain_notes(self.distance_km, self.commissioning)
        self.link = replace(kr.LINK, length_km=self.distance_km)
        self.env = kr.make_link(self.distance_km)        # the transmitter's action box (KeyRateEnv.apply)
        self.events: list = []
        self._expected = {}

        def generator(purpose):
            return np.random.default_rng(stream_seed(seed, self.link_id, self.epoch_tick, purpose))

        # polarisation monitor (aerial plant, 1 kHz)
        self.fibre = DriftStream(plant.OPERATING_POINTS[operating_point], plant.SAMPLE_INTERVAL_S,
                                 POLARISATION_SAMPLES, generator("polarisation-plant"))
        self.monitor = generator("polarisation-monitoring")
        # cable thermometer (session._temperature_series)
        self.thermometer = generator("cable-temperature")
        self.phase = float(self.thermometer.uniform(0.0, 2.0 * np.pi))
        self.walk, self.elapsed = 0.0, 0
        if previous is not None:
            # A new session of the same fibre: the cable does not change temperature because the
            # transmitter was recommissioned. The thermometer and any relaxing excursion carry over.
            self.phase, self.walk, self.elapsed = previous.phase, previous.walk_now, previous.elapsed + previous.period
            self.events = [replace_event(event, previous.period) for event in previous.events
                           if not event.finished(previous.period)]
            self.deferred = list(previous.deferred)
        self.walk_now = self.walk
        self.temperature = self._temperature(0)
        self.next_temperature = self._temperature(1)
        self.first_temperature = self.temperature
        # session misalignment (session._misalignment_series)
        self.drift = DriftStream(session_drift_parameters(), _SESSION.period_s, SESSION_SEGMENT_PERIODS,
                                 generator("session-drift"), external_temperature=True)
        # counting statistics
        self.counts = generator("session-counts")
        # clock
        self.clock_model = ClockModel(length_km=self.distance_km)
        self.clock = generator("clock-trajectory")
        self.clock_measurement = generator("clock-measurement")
        self.fractional_walk, self.clock_offset = 0.0, 0.0
        self.measurement = MeasurementConfig(photons=sync_loop.PHOTONS,
                                             spread_s=timing_spread(kr.BUDGET, self.distance_km))

    # -- environment -----------------------------------------------------------

    def _temperature(self, period: int) -> float:
        """Cable temperature at a period: diurnal cycle, slow sensor walk, event excursions."""
        self.walk_now = self.walk          # the walk as of the sample before this one (for a rollover)
        self.walk += float(self.thermometer.standard_normal())
        time_s = (self.elapsed + period) * _SESSION.period_s
        diurnal = _SESSION.diurnal_amplitude_k * np.sin(2.0 * np.pi * time_s / 86400.0 + self.phase)
        noise = 0.02 * self.walk * np.sqrt(_SESSION.period_s / 60.0)
        excursion = sum(event.excursion(period) for event in self.events)
        return float(_SESSION.ambient_temperature_c + diurnal + noise + excursion)

    def busy(self) -> bool:
        """An attack, a loss event or the rise of a thermal event is in progress."""
        return any(self.period < event.end_period for event in self.events)

    def start(self, request: InputEvent, origin: str, tick: int):
        author, severity = build_author_event(request, self.commissioning.calibration_gain)
        self.events.append(_RunningEvent(request, author, severity, origin, self.period,
                                         self.period + int(round(request.duration_s / TICK_S)), tick))

    # -- the channel -------------------------------------------------------------

    def _gated(self, conditions, settings):
        """The link with the receiver's gate and inter-symbol error folded in
        (the first half of ``qnet_qkd.qkd.keyrate.receiver_response``)."""
        spread_s = timing_spread(kr.BUDGET, conditions.length_km)
        efficiency = float(gate_efficiency(0.0, conditions.gate_width_s, spread_s))
        crosstalk = float(intersymbol_error(settings.clock_rate_hz, spread_s))
        return replace(conditions, coupling_efficiency=conditions.coupling_efficiency * efficiency,
                       misalignment_error=float(np.clip(conditions.misalignment_error + crosstalk, 0.0, 0.5)))

    def channel(self, conditions, settings, attacks=()):
        """Honest channel -> attacks on the per-photon-number yields -> the receiver's own limits."""
        response = self._gated(conditions, settings).response()
        for attack in attacks:
            response = attack.apply(response, settings)
        return apply_detector_saturation(response, settings, kr.DEAD_TIME_S, kr.AFTERPULSE_PROBABILITY)

    def expected_gain(self, settings) -> float:
        """Signal gain the commissioning calibration predicts for the settings in force."""
        if settings not in self._expected:
            if len(self._expected) > 64:
                self._expected.clear()
            self._expected[settings] = float(self.channel(self.link, settings).gain(settings.signal_intensity))
        return self._expected[settings]

    # -- one tick ----------------------------------------------------------------

    def step(self, tick: int) -> tuple:
        period, time_s = self.period, self.period * TICK_S
        # polarisation monitor (a thermal event reaches it through the plant's own dT/dt coupling)
        excursion = None
        if any(event.author.temperature_ramp_k for event in self.events):
            instants = period + np.arange(POLARISATION_SAMPLES) * plant.SAMPLE_INTERVAL_S / TICK_S
            excursion = np.array([sum(event.excursion(float(instant)) for event in self.events)
                                  for instant in instants])
        block = self.fibre.advance(excursion)
        rotation = rotation_from(block)
        indices = np.arange(0, POLARISATION_SAMPLES, POLARISATION_STRIDE)
        measured = plant.measure_rotation(rotation[indices], self.monitor)
        covariates = np.stack([block["temperature_c"], block["temperature_rate"], block["vibration_rms"]],
                              axis=-1)[indices]
        polarisation = PolarisationInput(block["time_s"][indices], measured, covariates, self.operating_point)

        # session: conditions, events, attacks
        temperature, upcoming = self.temperature, self.next_temperature
        drift = self.drift.advance_one(temperature, upcoming)
        misalignment = float(np.clip(self.link.misalignment_error
                                     + RESIDUAL_FRACTION * float(launch_qber_from(drift)[0]), 0.0, 0.5))
        records, attacks, loss_db = [], [], self.link.excess_loss_db
        for event in self.events:
            weight = event.weight(period)
            phase = "active"
            if not event.reported_start:
                event.reported_start, phase = True, "started"
            elif period >= event.end_period:
                phase = "ended" if period == event.end_period else "relaxing"
            if phase != "relaxing":
                records.append(ActiveEvent(event.request.kind, self.link_id, event.request.strength,
                                           event.request.profile, event.origin, event.started_tick,
                                           event.started_tick + (event.end_period - event.onset_period),
                                           event.severity, weight, phase))
            if weight > 0.0:
                loss_db += event.author.excess_loss_step_db * weight
                if event.author.attack is not None:
                    attacks.append(event.author.attack.with_strength(weight))
        settings = self.settings
        conditions = replace(self.link, excess_loss_db=loss_db, misalignment_error=misalignment)
        response = self.channel(conditions, settings, attacks)
        pulses = settings.clock_rate_hz * TICK_S
        observables = sample_observables(response, settings, pulses, rng=self.counts)
        yield_lower, error_upper = single_photon_bounds(observables)
        row = {"time_s": float(time_s), "period": int(period),
               "signal_gain": float(observables.signal_gain), "decoy_gain": float(observables.decoy_gain),
               "vacuum_gain": float(observables.vacuum_gain), "signal_error": float(observables.signal_error),
               "decoy_error": float(observables.decoy_error), "vacuum_error": float(observables.vacuum_error),
               "signal_intensity": float(settings.signal_intensity),
               "decoy_intensity": float(settings.decoy_intensity),
               "single_photon_yield_lower": float(yield_lower), "single_photon_error_upper": float(error_upper),
               "key_rate_hz": float(decoy_key_rate_hz(observables)), "temperature_c": float(temperature),
               "expected_gain": self.expected_gain(settings), "pulses": float(pulses),
               "length_km": self.distance_km}
        qkd = QkdInput(row, float(settings.signal_probability), float(settings.vacuum_probability),
                       float(settings.clock_rate_hz), TICK_S, self.commissioning)

        # clock (ClockModel.simulate, one sample at a time, driven by the cable temperature)
        model = self.clock_model
        thermal = model.thermal_coefficient_s_per_km_k * model.length_km * (temperature - self.first_temperature)
        self.fractional_walk += float(self.clock.standard_normal())
        fractional = model.fractional_offset + model.frequency_diffusion * np.sqrt(TICK_S) * self.fractional_walk
        self.clock_offset += float(fractional * TICK_S)
        offset = float(thermal + self.clock_offset)
        noise = float(self.clock_measurement.normal(0.0, self.measurement.noise_s))
        sync = SyncInput(float(time_s), offset + noise, float(temperature))

        reference = {"true_rotation": rotation, "true_rotation_time_s": block["time_s"],
                     "true_misalignment": misalignment, "true_offset_s": offset,
                     "true_excess_loss_db": float(loss_db),
                     "active_labels": tuple(event.request.kind for event in self.events if event.weight(period) > 0)}
        frame = LinkInput(self.link_id, self.a, self.b, self.distance_km, self.epoch_tick, period, polarisation,
                          sync, qkd, self.notes, reference)

        # advance the environment for the next tick
        self.events = [event for event in self.events if not event.finished(period)]
        self.period += 1
        self.temperature, self.next_temperature = upcoming, self._temperature(self.period + 1)
        return frame, records


# --------------------------------------------------------------------------
# sources
# --------------------------------------------------------------------------

class SimulatedInputSource:
    """Raw observables of every active fibre, from the author's plants (see the module docstring)."""

    kind = "simulated"

    def __init__(self, seed: int, scenario: Any = "demo", *, loop_s: int | None = None,
                 operating_point: str = "calibrado", session_periods: int | None = SESSION_SEGMENT_PERIODS):
        if type(seed) is not int or seed < 0:
            raise ValueError("explicit non-negative integer seed required")
        if operating_point not in plant.OPERATING_POINTS:
            raise ValueError("unknown polarisation operating point")
        if isinstance(scenario, str):
            if scenario not in SCENARIOS:
                raise ValueError(f"unknown scenario {scenario!r}; expected one of {tuple(SCENARIOS)} or a timeline")
            timeline, default_loop = SCENARIOS[scenario]
            self.scenario = scenario
        else:
            timeline, default_loop, self.scenario = tuple(scenario), DEMO_LOOP_S, "custom"
            for entry in timeline:
                if not isinstance(entry, TimelineEvent):
                    raise ValueError("a custom scenario is a sequence of TimelineEvent")
                InputEvent(entry.kind, "validation", entry.strength, entry.duration_s, entry.profile)
        self.loop_s = int(loop_s if loop_s is not None else default_loop)
        if self.loop_s < 1 or any(not 0 <= entry.at_s < self.loop_s for entry in timeline):
            raise ValueError("timeline entries must fall inside the loop")
        self.seed, self.timeline, self.operating_point = seed, timeline, operating_point
        if session_periods is not None and (type(session_periods) is not int
                                            or session_periods < 2 * BASELINE_PERIODS):
            raise ValueError("session_periods must be None or an integer of at least two baselines")
        self.session_periods = session_periods
        self.label = f"Simulated input (author plants) · seed {seed} · scenario {self.scenario}"
        self._plants: dict = {}
        self._pending: list = []
        self._recommission: set = set()
        self._last_tick: int | None = None
        self._last_frame: InputFrame | None = None

    def info(self) -> SourceInfo:
        return SourceInfo(kind=self.kind, label=self.label, seed=self.seed, scenario=self.scenario,
                          note="Only the raw observables are simulated (polarisation monitor, clock offset, "
                               "decoy-BB84 session); every result is computed by the processing from them.")

    # -- operator ------------------------------------------------------------------

    def inject(self, event: InputEvent) -> None:
        """Start an input event on a fibre at the next tick."""
        if not isinstance(event, InputEvent):
            raise ValueError("InputEvent required")
        if self._last_frame is not None and event.link_id not in self._plants:
            raise ValueError(f"fibre {event.link_id!r} has no active input stream")
        self._pending.append(event)

    def actuate(self, commands: Mapping[str, LinkCommand]) -> None:
        """Apply the processing's transmitter commands; they take effect in the next period."""
        for link_id, command in commands.items():
            fibre = self._plants.get(link_id)
            if fibre is None:
                continue
            if command.recommission:
                self._recommission.add(link_id)
            elif command.keyrate_action:
                fibre.env.settings = fibre.settings
                fibre.settings = fibre.env.apply(int(command.keyrate_action))

    # -- frames ----------------------------------------------------------------------

    def _first_episode(self, link_id: str):
        """Length of a fibre's first session: shortened per fibre so the rollovers are spread."""
        if self.session_periods is None:
            return None
        span = self.session_periods - 2 * BASELINE_PERIODS + 1
        return 2 * BASELINE_PERIODS + stream_seed(self.seed, link_id, 0, "first-episode") % span

    def _scheduled(self, tick: int, ranked: tuple) -> list:
        if not ranked:
            return []
        at = tick % self.loop_s
        return [InputEvent(entry.kind, ranked[entry.rank % len(ranked)], entry.strength, entry.duration_s,
                           entry.profile) for entry in self.timeline if entry.at_s == at]

    def frame(self, tick: int, topology) -> InputFrame:
        if type(tick) is not int or tick < 0:
            raise ValueError("tick must be a non-negative integer")
        if self._last_frame is not None and tick == self._last_tick:
            return self._last_frame
        if self._last_tick is not None and tick != self._last_tick + 1:
            # A break in the acquisition (or a restart): every session is commissioned anew.
            self._plants.clear()
            self._recommission.clear()
        active = {link.link_id: link for link in topology.links if link.active}
        for link_id in list(self._plants):
            link = active.get(link_id)
            if link is None or float(link.distance_km) != self._plants[link_id].distance_km:
                del self._plants[link_id]
            elif link_id in self._recommission:
                # Recalibration: a new session of the same fibre. Whatever is happening to the fibre
                # (cable temperature, an attack in progress) does not stop because it was recalibrated.
                self._plants[link_id] = _LinkPlant(self.seed, link, tick, self.operating_point,
                                                   self.session_periods, self._plants[link_id])
        self._recommission.clear()
        for link_id, link in active.items():
            if link_id not in self._plants:
                self._plants[link_id] = _LinkPlant(self.seed, link, tick, self.operating_point,
                                                   self._first_episode(link_id))
            elif self._plants[link_id].episode_periods is not None \
                    and self._plants[link_id].period >= self._plants[link_id].episode_periods \
                    and not self._plants[link_id].busy():
                # End of an author episode: the session is commissioned anew (see the module docstring).
                self._plants[link_id] = _LinkPlant(self.seed, link, tick, self.operating_point,
                                                   self.session_periods, self._plants[link_id])
        ranked = link_rank_order(self.seed, tuple(active))
        for request in self._scheduled(tick, ranked):
            fibre = self._plants[request.link_id]
            fibre.deferred.append(request)
        for fibre in self._plants.values():
            if fibre.deferred and fibre.period >= BASELINE_PERIODS:
                # A scheduled event never falls inside a session's own baseline: it waits for it.
                for request in fibre.deferred:
                    fibre.start(request, "timeline", tick)
                fibre.deferred = []
        pending, self._pending = self._pending, []
        for request in pending:
            if request.link_id in self._plants:
                self._plants[request.link_id].start(request, "operator", tick)
        links, events = {}, []
        for link_id in sorted(self._plants):
            link_input, records = self._plants[link_id].step(tick)
            link_input = replace(link_input, quantum_record={
                "acquisition_id": f"{link_id}.{link_input.epoch_tick}.{link_input.period}",
                "channel_id": (active[link_id].channels or {}).get("quantum", {}).get("channel_id"),
                "associated_classical_id": (active[link_id].channels or {}).get("classical", {}).get("channel_id"),
                "transmitter_id": link_input.a, "receiver_id": link_input.b,
                "emitted_pulses": float(link_input.qkd.row["pulses"]),
                "signal_gain": float(link_input.qkd.row["signal_gain"]),
                "decoy_gain": float(link_input.qkd.row["decoy_gain"]),
                "vacuum_gain": float(link_input.qkd.row["vacuum_gain"]),
                "scope": "statistical_simulation", "physical_transmission": False})
            links[link_id] = link_input
            events.extend(records)
        self._last_tick = tick
        self._last_frame = InputFrame(tick, tick * TICK_S, self.kind, links, tuple(events))
        return self._last_frame


class IdleInputSource:
    """No feed: normal operation without an input source shows configuration only."""

    kind = "none"
    label = "No input source"

    def info(self) -> SourceInfo:
        return SourceInfo(kind=self.kind, label=self.label, note="No observables are delivered; no series is shown.")

    def frame(self, tick: int, topology) -> InputFrame:
        if type(tick) is not int or tick < 0:
            raise ValueError("tick must be a non-negative integer")
        return InputFrame(tick, tick * TICK_S, self.kind, {}, ())

    def inject(self, event: InputEvent) -> None:
        raise ValueError("the idle source has no input to disturb; select the simulated source to inject events")

    def actuate(self, commands: Mapping[str, LinkCommand]) -> None:
        return None


__all__ = ["ATTACK_KINDS", "ActiveEvent", "BASELINE_PERIODS", "Commissioning", "DEMO_LOOP_S", "DEMO_TIMELINE",
           "DomainNote", "DriftStream", "EVENT_KINDS", "EVENT_PROFILES", "IdleInputSource", "InputEvent",
           "InputFrame", "InputSource", "LinkCommand", "LinkInput", "PolarisationInput", "QkdInput", "SCENARIOS",
           "SESSION_COLUMNS", "SimulatedInputSource", "SyncInput", "THERMAL_RISE_S", "TICK_S", "TimelineEvent",
           "build_author_event", "commission", "domain_notes", "event_severity", "launch_qber_from",
           "link_rank_order", "rotation_from", "session_drift_parameters", "stream_seed"]
