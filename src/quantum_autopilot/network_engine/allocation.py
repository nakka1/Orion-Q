"""Hybrid allocation: classical-traffic admission per fibre, protecting key generation
(design §2.2, step 7).

What runs, and why
------------------
The reviewed learned allocator (``model_intake.allocation.AllocationReplayModel``,
``qnet.allocator.*``) is **not run** on an ORION topology. Its code path is not
fibre-wise: it is one fixed plant of eleven named spans (São Paulo–Rio corridor)
whose per-span profiles (length, sifted rate, counts per gate, classical launch power)
come from the export's ``plant.json``; it requires exactly those link names in that
order, a 5 s control period with 30 s reconfiguration latency, and the
``single_photon_bb84`` protocol — it refuses decoy-state sessions by contract. None of
that holds for a metropolitan topology of decoy-BB84 fibres stepped every second, and
feeding it remapped spans would be inventing a plant. So the role is reported as
``unavailable`` (no verified archive) or ``fallback`` (archive present) with the reason
"plant differs from the reviewed 11-link plant", and the author's baseline
``qnet_network.allocation.QuantumFirst`` decides, through its own ``act``.

``QuantumFirst`` silences the classical channel on a fibre "with quantum work
outstanding": ``backlog_bits > 0`` or a finite ``urgency_s``. The engine has no QKD
demand book, so the observation it hands the policy is built from what the processing
measured, and the mapping is declared here:

* ``backlog_bits`` = key the fibre owes in this period: the shortfall of the period's
  secret key against the commissioning calibration (``commission(length).calibration_rate_hz``
  × 1 s) when the fibre is ``degraded``, ``down`` or ``compromised`` — plus, while an
  attack alarm is held, the period's discarded key. Zero on a healthy (``up``) fibre.
* ``urgency_s`` = ``inf`` (no deadline book exists).

Hence: admission 1.0 on a healthy fibre, 0.0 while its key production is impaired.
A fibre without observables (``no_input``, or imposed ``disabled | isolated |
calibrating``) gets no decision: ``classical_admission`` stays ``None``.
"""
from __future__ import annotations

from dataclasses import replace
from typing import Mapping

import numpy as np

from qnet_network.allocation import QuantumFirst
from qnet_network.allocation.env import Observation

from . import models as mdl
from .sources import TICK_S, commission
from .state import Decision, LinkLive, ModelStatus, canonical_sha256

PLANT_REASON = ("plant differs from the reviewed 11-link plant (fixed named spans, 5 s period, "
                "single-photon BB84); the learned allocator is not run on this topology")
IMPAIRED = ("degraded", "down", "compromised")
LEVELS = (0.0, 1.0)


class _Plant:
    """What ``QuantumFirst.reset`` reads from an environment: the number of links."""

    def __init__(self, num_links: int):
        self.num_links = int(num_links)


class Allocator:
    """Per-tick classical admission with the author's ``QuantumFirst``."""

    controller_id = mdl.ALLOCATION_BASELINE_ID

    def __init__(self, registry: mdl.ModelRegistry | None = None):
        self.registry = registry if registry is not None else mdl.ModelRegistry()
        self.policy = QuantumFirst()
        self._levels: dict = {}
        self._discarded: dict = {}
        self.publish_status()

    def model_state(self) -> tuple:
        """``(state, reason)`` of the learned allocator role."""
        if not self.registry.control_enabled:
            return "disabled", f"ML control disabled by operator; {self.controller_id} acting"
        if self.registry.allocator_archive() is None:
            state = self.registry.archive_state("qnet")
            why = f"qnet archive rejected ({state.reason})" if state.state == "rejected" else \
                f"qnet archive not provided ({mdl.ARCHIVE_ENV['qnet']} or set_archive)"
            return "unavailable", f"{why}; {PLANT_REASON}; {self.controller_id} acting"
        return "fallback", f"{PLANT_REASON}; {self.controller_id} acting"

    def publish_status(self) -> ModelStatus:
        state, reason = self.model_state()
        archive = self.registry.allocator_archive()
        status = ModelStatus(mdl.ALLOCATION_MODEL_ID, "allocation", state, reason,
                             None if archive is None else str(getattr(archive, "sha256", "")) or None, False)
        self.registry.set_role_status(status)
        return status

    def backlog_bits(self, live: LinkLive) -> float | None:
        """Key owed by a fibre this period (see the module docstring); ``None`` without observables."""
        if live.skr_bps is None or live.status not in ("up",) + IMPAIRED:
            return None
        if live.status == "up":
            return 0.0
        owed = max(commission(live.distance_km).calibration_rate_hz * TICK_S - float(live.skr_bps) * TICK_S, 0.0)
        discarded = float(live.qkd.discarded_bits or 0.0)
        owed += max(discarded - self._discarded.get(live.link_id, discarded), 0.0)
        return owed

    def step(self, links: Mapping[str, LinkLive], *, tick: int, at_utc: str) -> tuple:
        """``(links with classical_admission filled, decisions)`` for one tick.

        A decision is recorded when the admission of a fibre changes (and the first
        time it is decided), not every tick."""
        state, reason = self.model_state()
        order = [link_id for link_id in sorted(links) if self.backlog_bits(links[link_id]) is not None]
        backlog = np.array([self.backlog_bits(links[link_id]) for link_id in order], dtype=float)
        out, decisions = dict(links), []
        for link_id in set(self._levels) - set(order):
            del self._levels[link_id]
        if order:
            count = len(order)
            zeros = np.zeros(count)
            observation = Observation(
                time_s=float(tick) * TICK_S,
                measured_qber=np.array([links[link_id].qber or 0.0 for link_id in order]),
                admission=np.array([self._levels.get(link_id, 1.0) for link_id in order]),
                pending_admission=np.zeros((0, count)), raman_qber_estimate=zeros,
                key_pool_bits=np.array([links[link_id].qkd.key_pool_bits or 0.0 for link_id in order]),
                key_rate_hz=np.array([links[link_id].skr_bps or 0.0 for link_id in order]),
                backlog_bits=backlog, urgency_s=np.full(count, np.inf), classical_backlog_bits=zeros)
            self.policy.reset(_Plant(count))
            admission = np.asarray(self.policy.act(observation, int(tick)), dtype=float)
            for index, link_id in enumerate(order):
                live, level = links[link_id], float(admission[index])
                record = {"level": level, "controller_id": self.controller_id, "model_state": state,
                          "in_contract": False, "fallback_used": True, "backlog_bits": float(backlog[index]),
                          "reason": "key owed: classical traffic silenced" if level == 0.0 else
                                    "no key owed: classical traffic admitted",
                          "learned_model": mdl.ALLOCATION_MODEL_ID, "learned_model_reason": PLANT_REASON,
                          "physical_action_performed": False}
                out[link_id] = replace(live, classical_admission=record)
                if self._levels.get(link_id) != level:
                    previous = self._levels.get(link_id)
                    decisions.append(Decision.create(
                        tick=int(tick), at_utc=at_utc, kind="allocation", controller_id=self.controller_id,
                        model_state=state, subject=link_id,
                        summary=(f"classical admission {'—' if previous is None else format(previous, 'g')} -> "
                                 f"{level:g} on {link_id} (status {live.status}, key owed "
                                 f"{backlog[index]:.0f} bit this period); {mdl.ALLOCATION_MODEL_ID} not run: "
                                 "plant differs from the reviewed 11-link plant"),
                        inputs_sha256=canonical_sha256({"link_id": link_id, "status": live.status,
                                                        "skr_bps": live.skr_bps, "qber": live.qber,
                                                        "backlog_bits": float(backlog[index])}),
                        outcome=f"admission={level:g}", in_contract=False, fallback_used=True))
                    self._levels[link_id] = level
        for link_id, live in links.items():
            if live.qkd.discarded_bits is not None:
                self._discarded[link_id] = float(live.qkd.discarded_bits)
        return out, tuple(decisions)


__all__ = ["Allocator", "IMPAIRED", "LEVELS", "PLANT_REASON"]
