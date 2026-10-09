"""Model registry of the network engine: who decides, with which weights, and why.

For every model role the registry answers three questions honestly:

* are reviewed weights present? Learned weights for compensation, synchronisation,
  key rate, routing and allocation live only in the two reviewed ZIP exports pinned by
  SHA-256 in ``model_intake.artifacts.PINS`` (``qnet`` and ``qkd``). An archive is
  taken from ``set_archive(kind, path)`` or from the environment variables
  ``ORION_QNET_ARCHIVE`` / ``ORION_QKD_ARCHIVE`` and is used only after the existing
  pinned-hash machinery accepted it (``verification.verify_model_archive`` and
  ``artifacts.ReviewedArchive``);
* does the model's own contract admit this plant? (``polarisation_loop.check_checkpoint``,
  ``sync_loop.check_contract``, ``keyrate_loop.check_contract``);
* if not, which named baseline acts instead (``baseline.alpha_beta`` for polarisation and
  for the gate, ``baseline.static`` for the transmitter).

The attack detector is different: its reviewed safe export is part of the repository
(``model_intake/derived``), so it is always available; what an archive adds is the
hash binding of that export to the archive's own (never deserialised) pickle member.

``status()`` reports all six roles. Nothing here activates a model silently, and a
test archive (duck-typed object, no pinned hash) is refused unless the registry was
built with ``allow_unverified=True`` — which production code never does.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
import hashlib
import os
import time
from typing import Any, Mapping

import numpy as np

from qnet_qkd.qkd.timing import AlphaBetaTracker

from ..control_plane import keyrate_loop as kr
from ..control_plane import polarisation_loop as pol
from ..control_plane import polarisation_plant as plant
from ..control_plane import sync_loop
from ..model_intake import detector_session
from ..model_intake.artifacts import PINS
from ..model_intake.detector import SafeDetector, softmax
from ..model_intake.detector import MODEL_ID as DETECTOR_ID
from ..model_intake.keyrate import (KeyRateReplayModel, LinkCalibration, PeriodObservables, TransmitterSettings,
                                    _preview)
from .state import MODEL_ROLES, ModelStatus

ARCHIVE_ENV = {"qnet": "ORION_QNET_ARCHIVE", "qkd": "ORION_QKD_ARCHIVE"}
#: A model of each archive, used only to address the archive in ``verify_model_archive``.
_PROBE_MODEL = {"qnet": "qnet.routing.nominal", "qkd": "qkd.keyrate.actor"}
#: Author's tuned alpha-beta gains of the calibrated plant (the baseline that matters).
BASELINE_ALPHA_BETA = (0.01, 0.0)
COMPENSATION_BASELINE_ID = "baseline.alpha_beta"
SYNC_BASELINE_ID = "baseline.alpha_beta"
SYNC_MODEL_ID = sync_loop.MODEL_ID
KEYRATE_MODEL_ID = kr.ACTOR_ID
KEYRATE_BASELINE_ID = kr.STATIC_ID
QBER_ALARM_ID = "baseline.qber_threshold"
ROUTING_MODEL_ID = "qnet.routing.nominal"
ROUTING_BASELINE_ID = "baseline.greedy_fidelity"
ALLOCATION_MODEL_ID = "qnet.allocator.ridge"
ALLOCATION_BASELINE_ID = "baseline.quantum_first"
COMPENSATION_ARCHITECTURES = ("ridge", "mlp")
#: The evidence domain the reviewed replay models admit. A frame of any other kind is
#: passed on as it is and the models' own contract refuses it (the baseline then acts).
OFFLINE_DOMAIN = "simulated_offline"


# --------------------------------------------------------------------------
# archives
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class ArchiveState:
    """What the registry knows about one reviewed archive."""

    kind: str                     # "qnet" | "qkd"
    state: str                    # "verified" | "absent" | "rejected" | "test_unverified"
    path: str | None = None
    sha256: str | None = None
    reason: str = ""
    origin: str = ""              # "set_archive" | "environment" | "test" | ""

    def to_dict(self) -> dict:
        return {name: getattr(self, name) for name in self.__dataclass_fields__}


# --------------------------------------------------------------------------
# controllers the registry hands out
# --------------------------------------------------------------------------

class StaticKeyRate:
    """Hold the commissioned transmitter settings (``keyrate_loop.StaticController``)."""

    controller_id = KEYRATE_BASELINE_ID
    learned = False

    def reset(self):
        pass

    def decide(self, previous, settings, calibration, *, span_km, domain):
        return 0, None


class KeyRateActor:
    """The reviewed actor, called exactly as ``keyrate_loop.ActorController`` calls it,
    but fed from the published observables instead of a simulator object.

    The caller owns the sampling generator (the replay refuses an implicit greedy
    deployment); it is seeded per fibre so the stream of one fibre does not depend on
    the others.
    """

    controller_id = KEYRATE_MODEL_ID
    learned = True

    def __init__(self, model: KeyRateReplayModel, seed: int):
        self.model, self.seed = model, int(seed)
        self.reset()

    def reset(self):
        self.rng = np.random.default_rng(self.seed)

    def decide(self, previous: PeriodObservables | None, settings: TransmitterSettings,
               calibration: LinkCalibration, *, span_km: float, domain: str):
        result = self.model.recommend(previous, settings, calibration, domain=domain, link_span_km=span_km,
                                      protocol="decoy_state_bb84", rng=self.rng)
        return result.sampled_index, result


def preview_settings(settings: TransmitterSettings, action_index: int) -> TransmitterSettings:
    """The bounded settings an action leads to (the reviewed box; never applies them)."""
    return _preview(settings, int(action_index))


class FastSafeDetector(SafeDetector):
    """The reviewed detector, with all trees walked together instead of one at a time.

    Same reviewed arrays, same comparisons, and the per-class sum is accumulated in the
    fitted tree order (``numpy.cumsum`` is sequential), so the probabilities are
    bit-identical to ``SafeDetector.probabilities`` — a test checks equality, not
    closeness. Only ``probabilities`` is replaced; gating and hysteresis are inherited.
    One row costs about 1 ms instead of about 80 ms.
    """

    def __init__(self, export=None, archive=None):
        super().__init__(export, archive)
        arrays = self.export.arrays
        self._base = np.asarray(arrays["tree_offsets"][:-1], dtype=np.int64)
        tree_class = np.asarray(arrays["tree_class"], dtype=np.int64)
        self._by_class = [np.flatnonzero(tree_class == label) for label in range(len(self.classes))]
        self._baseline = np.asarray(arrays["baseline_prediction"], dtype=np.float64)

    def probabilities(self, features):
        features = np.ascontiguousarray(features, dtype=np.float64)
        if features.ndim != 2 or features.shape[1] != len(self.feature_names):
            raise ValueError("17 detector features per row required")
        key = (features.shape, hashlib.sha256(features.tobytes()).digest())
        if key not in self._cache:
            if len(self._cache) >= 4:
                self._cache.pop(next(iter(self._cache)))
            self._cache[key] = self._evaluate(features)
        return self._cache[key].copy()

    def _evaluate(self, features):
        arrays = self.export.arrays
        feature, threshold, missing_left = arrays["feature_idx"], arrays["num_threshold"], arrays["missing_go_to_left"]
        left, right, leaf, value = arrays["left"], arrays["right"], arrays["is_leaf"], arrays["value"]
        scaled = (features - self.mean) / self.scale
        rows, trees = len(scaled), len(self._base)
        node = np.zeros((rows, trees), dtype=np.int64)
        row_index = np.broadcast_to(np.arange(rows)[:, None], (rows, trees))
        while True:
            at = self._base[None, :] + node
            open_ = leaf[at] == 0
            if not open_.any():
                break
            position = at[open_]
            x = scaled[row_index[open_], feature[position]]
            go_left = np.where(np.isnan(x), missing_left[position] == 1, x <= threshold[position])
            node[open_] = np.where(go_left, left[position], right[position])
        leaves = value[self._base[None, :] + node]
        raw = np.empty((rows, len(self.classes)), dtype=np.float64)
        for label, members in enumerate(self._by_class):
            terms = np.concatenate([np.full((rows, 1), self._baseline[label]), leaves[:, members]], axis=1)
            raw[:, label] = np.cumsum(terms, axis=1)[:, -1]
        return softmax(raw)


# --------------------------------------------------------------------------
# the registry
# --------------------------------------------------------------------------

class ModelRegistry:
    """Locates and verifies the reviewed archives and hands out controllers per role.

    Every factory returns ``(controller, ModelStatus)``: the learned replay model when its
    archive is verified and its contract admits the plant, otherwise the named baseline
    with ``state`` and ``reason`` saying why. ``subject`` (a fibre id) lets ``status()``
    report on how many fibres a model is really acting.
    """

    def __init__(self, *, allow_unverified: bool = False, environ: Mapping[str, str] | None = None,
                 compensation_architecture: str = "ridge"):
        if compensation_architecture not in COMPENSATION_ARCHITECTURES:
            raise ValueError(f"compensation architecture must be one of {COMPENSATION_ARCHITECTURES}")
        self.allow_unverified = bool(allow_unverified)
        self.compensation_architecture = compensation_architecture
        self.control_enabled = True
        self.enforce_control_contracts = False
        self._archives: dict = {}
        self._states = {kind: ArchiveState(kind, "absent", reason=f"no {kind} archive given (set_archive or "
                                                                    f"{ARCHIVE_ENV[kind]})") for kind in PINS}
        self._assignments = {role: {} for role in MODEL_ROLES}  # subject -> (state, reason, contract, id, archive hash)
        self._overrides: dict = {}                                  # role -> ModelStatus set by another module
        self._counters = {role: {"inferences": 0, "faults": 0, "last_latency_ms": None} for role in MODEL_ROLES}
        self._models: dict = {}
        self._detector = None
        environ = os.environ if environ is None else environ
        for kind, name in ARCHIVE_ENV.items():
            if environ.get(name):
                self._load(kind, environ[name], "environment")

    # -- archives ------------------------------------------------------------------

    def _load(self, kind: str, path, origin: str) -> ArchiveState:
        from ..model_intake.artifacts import ReviewedArchive
        from ..model_intake.verification import verify_model_archive

        if kind not in PINS:
            raise ValueError(f"unknown archive kind {kind!r}; expected one of {tuple(PINS)}")
        self._archives.pop(kind, None)
        self._models = {key: value for key, value in self._models.items() if key[0] != kind}
        if kind == "qkd":
            self._detector = None
        try:
            verification = verify_model_archive(_PROBE_MODEL[kind], path)
            archive = ReviewedArchive(verification.path, kind)
        except (ValueError, OSError) as exc:
            state = ArchiveState(kind, "rejected", str(path), None, f"{type(exc).__name__}: {exc}", origin)
        else:
            self._archives[kind] = archive
            state = ArchiveState(kind, "verified", verification.path, archive.sha256,
                                 "SHA-256 equals the pinned reviewed export", origin)
        self._states[kind] = state
        return state

    def set_archive(self, kind: str, path) -> ArchiveState:
        """Verify ``path`` against the pinned hash of ``kind`` and adopt it (or record why not).

        ``path=None`` removes the archive. Controllers already handed out keep the
        model they were built with; ask the factories again to pick up the change.
        """
        if kind not in PINS:
            raise ValueError(f"unknown archive kind {kind!r}; expected one of {tuple(PINS)}")
        if path is None:
            self._archives.pop(kind, None)
            self._models = {key: value for key, value in self._models.items() if key[0] != kind}
            if kind == "qkd":
                self._detector = None
            self._states[kind] = ArchiveState(kind, "absent", reason=f"no {kind} archive given (set_archive or "
                                                                     f"{ARCHIVE_ENV[kind]})")
            return self._states[kind]
        return self._load(kind, path, "set_archive")

    def register_test_archive(self, kind: str, archive_obj: Any) -> ArchiveState:
        """TEST ONLY: adopt a duck-typed archive that no pinned hash vouches for."""
        if not self.allow_unverified:
            raise PermissionError("unverified archives are refused; only a registry built with "
                                  "allow_unverified=True (tests) accepts them")
        if kind not in PINS or getattr(archive_obj, "kind", None) != kind:
            raise ValueError("archive object must declare the kind it is registered as")
        self._archives[kind] = archive_obj
        self._models = {key: value for key, value in self._models.items() if key[0] != kind}
        if kind == "qkd":
            self._detector = None
        self._states[kind] = ArchiveState(kind, "test_unverified", None, str(getattr(archive_obj, "sha256", "")),
                                          "TEST ARCHIVE: not hash-verified, never a production state", "test")
        return self._states[kind]

    def archive(self, kind: str):
        """The adopted archive object of ``kind`` or ``None``."""
        return self._archives.get(kind)

    def archive_state(self, kind: str) -> ArchiveState:
        return self._states[kind]

    def archive_states(self) -> tuple:
        return tuple(self._states[kind] for kind in sorted(self._states))

    def routing_archive(self):
        """Verified ``qnet`` archive for the routing module (``RoutingReplayModel``) or ``None``."""
        return self._archives.get("qnet")

    def allocator_archive(self):
        """Verified ``qnet`` archive for the allocation module (``AllocationReplayModel``) or ``None``."""
        return self._archives.get("qnet")

    def _sha(self, kind: str):
        return self._states[kind].sha256 if kind in self._archives else None

    def _unavailable(self, kind: str) -> str:
        state = self._states[kind]
        if state.state == "rejected":
            return f"{kind} archive rejected ({state.reason})"
        return f"{kind} archive not provided ({ARCHIVE_ENV[kind]} or set_archive)"

    # -- bookkeeping -----------------------------------------------------------------

    def _assign(self, role: str, subject, status: ModelStatus) -> ModelStatus:
        if subject is not None:
            self._assignments[role][str(subject)] = (status.state, status.reason, status.in_contract,
                                                     status.model_id, status.archive_sha256)
        return status

    def report(self, role: str, subject: str, state: str, reason: str, in_contract: bool | None = None) -> None:
        """A processor tells the registry what is really acting on a fibre (e.g. after
        the supervisor dropped a learned controller for its standby)."""
        previous = self._assignments[role].get(str(subject), (None, "", None, None, None))
        ModelStatus(model_id=previous[3] or role, role=role, state=state)        # validates role and state
        self._assignments[role][str(subject)] = (state, reason, previous[2] if in_contract is None else in_contract,
                                                 previous[3], previous[4])

    def release(self, subject: str) -> None:
        """Forget a fibre that left the topology."""
        for assignments in self._assignments.values():
            assignments.pop(str(subject), None)

    def note(self, role: str, *, inferences: int = 0, faults: int = 0, latency_ms: float | None = None) -> None:
        """Accumulate usage counters of a role (shown in ``status()``)."""
        counters = self._counters[role]
        counters["inferences"] += int(inferences)
        counters["faults"] += int(faults)
        if latency_ms is not None:
            counters["last_latency_ms"] = float(latency_ms)

    def set_role_status(self, status: ModelStatus) -> None:
        """Another module (routing, allocation) states its own role's status."""
        if not isinstance(status, ModelStatus):
            raise ValueError("ModelStatus required")
        self._overrides[status.role] = status

    # -- factories ---------------------------------------------------------------------

    def baseline_compensator(self):
        """A fresh ``baseline.alpha_beta`` at the author's tuned gains (also the hot standby)."""
        return pol.AlphaBeta(*BASELINE_ALPHA_BETA)

    def compensator(self, operating_point: str = "calibrado", *, subject: str | None = None):
        """Polarisation controller for a plant operating point: ``(controller, ModelStatus)``."""
        if operating_point not in plant.OPERATING_POINTS:
            raise ValueError("unknown plant operating point")
        model_id = f"qnet.compensation.{operating_point}.{self.compensation_architecture}"
        if not self.control_enabled:
            return self.baseline_compensator(), self._assign("compensation", subject,
                ModelStatus(model_id, "compensation", "disabled", "ML control disabled by operator; baseline.alpha_beta acting"))
        archive = self._archives.get("qnet")
        if archive is None:
            status = ModelStatus(model_id, "compensation", "unavailable",
                                 f"{self._unavailable('qnet')}; {COMPENSATION_BASELINE_ID} acting")
            return self.baseline_compensator(), self._assign("compensation", subject, status)
        try:
            decision = pol.check_checkpoint(model_id, archive, operating_point)
            reasons = decision.reasons
            controller = pol.LearnedCheckpoint(model_id, archive) if decision.status == "ADMITTED" else None
        except (ValueError, KeyError, OSError) as exc:
            controller, reasons = None, (f"{type(exc).__name__}: {exc}",)
        if controller is None:
            status = ModelStatus(model_id, "compensation", "fallback",
                                 "checkpoint contract refused: " + ", ".join(reasons)
                                 + f"; {COMPENSATION_BASELINE_ID} acting", self._sha("qnet"), False)
            return self.baseline_compensator(), self._assign("compensation", subject, status)
        status = ModelStatus(model_id, "compensation", "active",
                             "checkpoint admitted for this plant; supervised, standby "
                             f"{COMPENSATION_BASELINE_ID}", self._sha("qnet"), True)
        return controller, self._assign("compensation", subject, status)

    def baseline_synchroniser(self):
        """A fresh author ``AlphaBetaTracker`` (default gains)."""
        return AlphaBetaTracker()

    def synchroniser(self, length_km: float | None = None, *, subject: str | None = None):
        """Gate predictor for a span: ``(predictor, ModelStatus)``.

        The reviewed ridge is admitted only on the span it was trained on (50 km); every
        other span gets the author's alpha-beta tracker, and the status says so.
        """
        if not self.control_enabled:
            return self.baseline_synchroniser(), self._assign("sync", subject,
                ModelStatus(SYNC_MODEL_ID, "sync", "disabled", "ML control disabled by operator; baseline.alpha_beta acting"))
        archive = self._archives.get("qkd")
        if archive is None:
            status = ModelStatus(SYNC_MODEL_ID, "sync", "unavailable",
                                 f"{self._unavailable('qkd')}; {SYNC_BASELINE_ID} acting")
            return self.baseline_synchroniser(), self._assign("sync", subject, status)
        span = sync_loop.TRAINED_SPAN_KM if length_km is None else float(length_km)
        try:
            contract = sync_loop.check_contract(archive, sync_loop.make_gate(), span)
            reasons = contract["reasons"]
            model = self._model("qkd", "sync", lambda: sync_loop.SyncReplayModel(archive)) \
                if contract["status"] == "admitted" else None
        except (ValueError, KeyError, OSError) as exc:
            model, reasons = None, [f"{type(exc).__name__}: {exc}"]
        if model is None:
            status = ModelStatus(SYNC_MODEL_ID, "sync", "fallback", "contract refused: " + "; ".join(reasons)
                                 + f"; {SYNC_BASELINE_ID} acting", self._sha("qkd"), False)
            return self.baseline_synchroniser(), self._assign("sync", subject, status)
        status = ModelStatus(SYNC_MODEL_ID, "sync", "active",
                             "contract admitted (1 s cadence, 2 s horizon, trained span)", self._sha("qkd"), True)
        return sync_loop.ReplaySync(model), self._assign("sync", subject, status)

    def keyrate_actor(self, length_km: float | None = None, *, subject: str | None = None, seed: int = 0):
        """Transmitter controller for a span: ``(controller, ModelStatus)``.

        ``controller.decide(previous, settings, calibration, span_km=, domain=)`` returns
        ``(action_index, recommendation_or_None)``. The actor's contract is a 25-80 km
        span and the reviewed action box; outside it the transmitter holds its
        commissioned settings (``baseline.static``).
        """
        if not self.control_enabled:
            return StaticKeyRate(), self._assign("keyrate", subject,
                ModelStatus(KEYRATE_MODEL_ID, "keyrate", "disabled", "ML control disabled by operator; baseline.static acting"))
        archive = self._archives.get("qkd")
        if archive is None:
            status = ModelStatus(KEYRATE_MODEL_ID, "keyrate", "unavailable",
                                 f"{self._unavailable('qkd')}; {KEYRATE_BASELINE_ID} acting (commissioned "
                                 "settings held)")
            return StaticKeyRate(), self._assign("keyrate", subject, status)
        span = 50.0 if length_km is None else float(length_km)
        try:
            model = self._model("qkd", "keyrate", lambda: KeyRateReplayModel(archive))
            contract = kr.check_contract(model, archive, kr.make_link(span), span)
            reasons = contract["reasons"]
        except (ValueError, KeyError, OSError) as exc:
            model, reasons = None, [f"{type(exc).__name__}: {exc}"]
        if model is None or reasons:
            status = ModelStatus(KEYRATE_MODEL_ID, "keyrate", "fallback", "contract refused: " + "; ".join(reasons)
                                 + f"; {KEYRATE_BASELINE_ID} acting (commissioned settings held)",
                                 self._sha("qkd"), False)
            return StaticKeyRate(), self._assign("keyrate", subject, status)
        status = ModelStatus(KEYRATE_MODEL_ID, "keyrate", "active",
                             "contract admitted (25-80 km span, reviewed action box, T=0.25 sampling)",
                             self._sha("qkd"), True)
        return KeyRateActor(model, seed), self._assign("keyrate", subject, status)

    def detector(self, length_km: float | None = None, *, subject: str | None = None):
        """The attack detector (always available): ``(FastSafeDetector, ModelStatus)``.

        ``in_contract`` is False when the span is outside the 20-70 km the detector was
        trained on; the detector still runs and every record says so.
        """
        if self._detector is None:
            archive, bound = self._archives.get("qkd"), "no archive bound: the in-repository safe export is used " \
                                                        "(its own SHA-256 is pinned in model_intake.detector)"
            detector = None
            if archive is not None and self._states["qkd"].state == "verified":
                try:
                    detector = FastSafeDetector(archive=archive)
                    bound = "safe export bound by hash to the pickle member of the verified qkd archive"
                except (ValueError, KeyError, OSError) as exc:
                    bound = f"archive binding failed ({type(exc).__name__}: {exc}); in-repository safe export used"
            self._detector = (detector or FastSafeDetector(), bound)
        detector, bound = self._detector
        low, high = detector_session.SPAN_KM
        inside = None if length_km is None else bool(low <= float(length_km) <= high)
        reason = bound
        if inside is False:
            reason += f"; span {float(length_km):g} km outside the trained {low:g}-{high:g} km"
        status = ModelStatus(DETECTOR_ID, "detector", "active", reason, detector.archive_sha256, inside)
        return detector, self._assign("detector", subject, status)

    def _model(self, kind: str, name: str, build):
        key = (kind, name)
        if key not in self._models:
            self._models[key] = build()
        return self._models[key]

    # -- status -----------------------------------------------------------------------------

    def _role_status(self, role: str, model_id: str, kind: str, baseline: str) -> ModelStatus:
        counters = self._counters[role]
        if not self.control_enabled:
            return ModelStatus(model_id, role, "disabled", f"ML control disabled by operator; {baseline} acting", **counters)
        assignments = self._assignments[role]
        ids = {entry[3] for entry in assignments.values() if entry[3]}
        if len(ids) == 1:
            model_id = next(iter(ids))
        active = [subject for subject, entry in assignments.items() if entry[0] == "active"]
        contracts = [entry[2] for entry in assignments.values() if entry[2] is not None]
        in_contract = None if not contracts else all(contracts)
        if active:
            reason = f"learned model acting on {len(active)} of {len(assignments)} fibres"
            hashes = {assignments[subject][4] for subject in active}
            archive_sha256 = next(iter(hashes)) if len(hashes) == 1 else None
            if kind not in self._archives:
                reason += (f"; {self._unavailable(kind)}; existing controllers retain their adopted weights "
                           "until recommissioning")
            others = sorted({entry[1] for entry in assignments.values() if entry[0] != "active"})
            if others:
                reason += "; elsewhere: " + " | ".join(others)
            return ModelStatus(model_id, role, "active", reason, archive_sha256, in_contract, **counters)
        if kind not in self._archives:
            return ModelStatus(model_id, role, "unavailable", f"{self._unavailable(kind)}; {baseline} acting",
                               None, None, **counters)
        if not assignments:
            return ModelStatus(model_id, role, "active", "weights adopted; no fibre assigned yet", self._sha(kind),
                               None, **counters)
        reasons = sorted({entry[1] for entry in assignments.values()})
        return ModelStatus(model_id, role, "fallback", " | ".join(reasons), self._sha(kind), in_contract, **counters)

    def status(self) -> tuple:
        """One ``ModelStatus`` per role, in the order of ``state.MODEL_ROLES``."""
        compensation_id = f"qnet.compensation.calibrado.{self.compensation_architecture}"
        out = {
            "compensation": self._role_status("compensation", compensation_id, "qnet", COMPENSATION_BASELINE_ID),
            "sync": self._role_status("sync", SYNC_MODEL_ID, "qkd", SYNC_BASELINE_ID),
            "keyrate": self._role_status("keyrate", KEYRATE_MODEL_ID, "qkd", KEYRATE_BASELINE_ID),
            "routing": self._role_status("routing", ROUTING_MODEL_ID, "qnet", ROUTING_BASELINE_ID),
            "allocation": self._role_status("allocation", ALLOCATION_MODEL_ID, "qnet", ALLOCATION_BASELINE_ID),
        }
        detector, _ = self.detector()
        assignments = self._assignments["detector"]
        contracts = [entry[2] for entry in assignments.values() if entry[2] is not None]
        outside = sum(1 for value in contracts if value is False)
        reason = self._detector[1]
        if outside:
            reason += f"; {outside} of {len(contracts)} fibres outside the trained span or transmitter domain"
        out["detector"] = ModelStatus(DETECTOR_ID, "detector", "active", reason, detector.archive_sha256,
                                      None if not contracts else all(contracts), **self._counters["detector"])
        for role, status in self._overrides.items():
            out[role] = replace(status, **{key: value for key, value in self._counters[role].items()
                                           if key != "last_latency_ms" or value is not None})
        return tuple(out[role] for role in MODEL_ROLES)


def clock_ms() -> float:
    """Monotonic milliseconds, for inference latency."""
    return time.perf_counter() * 1e3


def digest(*parts) -> str:
    """SHA-256 over raw numeric inputs (arrays as bytes, anything else as text)."""
    sha = hashlib.sha256()
    for part in parts:
        if isinstance(part, np.ndarray):
            sha.update(np.ascontiguousarray(part, dtype=np.float64).tobytes())
        else:
            sha.update(repr(part).encode("utf-8"))
    return sha.hexdigest()


__all__ = ["ALLOCATION_BASELINE_ID", "ALLOCATION_MODEL_ID", "ARCHIVE_ENV", "ArchiveState", "BASELINE_ALPHA_BETA",
           "COMPENSATION_BASELINE_ID", "DETECTOR_ID", "FastSafeDetector", "KEYRATE_BASELINE_ID", "KEYRATE_MODEL_ID",
           "KeyRateActor", "ModelRegistry", "OFFLINE_DOMAIN", "QBER_ALARM_ID", "ROUTING_BASELINE_ID",
           "ROUTING_MODEL_ID", "SYNC_BASELINE_ID", "SYNC_MODEL_ID", "StaticKeyRate", "clock_ms", "digest",
           "preview_settings"]
