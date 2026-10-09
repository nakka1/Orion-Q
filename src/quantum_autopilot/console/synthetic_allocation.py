"""Allocation observations from the author's metro plant on a 5 s virtual clock.

Each tick is one control period of ``qnet_network``'s ``HybridAllocationEnv`` on the
exported finite-key plant (eleven São Paulo–Rio spans, Raman coexistence, drift of
the compensated links), driven by quantum-first — the incumbent that silences
classical traffic wherever key is owed. The stream publishes what an allocator
would see; recommendations are scored, never applied. One 30-minute episode per
segment.
"""
from collections import deque
from datetime import datetime, timedelta, timezone

import numpy as np

from quantum_autopilot.control_plane.contracts import NetworkStateSnapshot
from quantum_autopilot.control_plane.optional_allocation import (
    TELEMETRY_KEY, AllocationDriftSample, AllocationSample, AllocationShadowWindow,
)
from quantum_autopilot.control_plane.author_feeds import AllocationEpisode
from quantum_autopilot.core import Provenance

# Identity of the reviewed author plant, not a weight or a discovered installation.
REVIEWED_PLANT_SHA256 = "881213fab3f992e5ba86c66aac82d6cf82df93405e0d450de123c39236137b50"


class SyntheticAllocationStream:
    def __init__(self):
        self.history = deque(maxlen=5)
        self.segment = 0
        self.reset()

    def reset(self):
        self.history.clear()
        self.context = self.last_tick = self.last_state = self.episode = None
        self.segment += 1

    def push(self, snapshot, seed, tick):
        if snapshot.input_kind != "synthetic" or not snapshot.topology.nodes:
            self.reset()
            return None
        node = snapshot.topology.nodes[0].id
        context = (snapshot.campaign, seed, node)
        if self.context is not None and (context != self.context or tick not in (self.last_tick, self.last_tick + 1)
                                        or (tick != self.last_tick and self.episode.done)):
            self.reset()
        if tick == self.last_tick:
            return self.last_state
        if self.last_tick is None:
            self.episode = AllocationEpisode(seed, self.segment)
        else:
            self.episode.advance()
        self.context, self.last_tick = context, tick
        origin = datetime(2026, 1, 1, tzinfo=timezone.utc)
        stamp = (origin + timedelta(seconds=tick * 5)).isoformat()
        seen = self.episode.observation
        raman = tuple(float(value) for value in seen.raman_qber_estimate)
        observation = AllocationSample(
            stamp, tuple(float(value) for value in seen.measured_qber), raman,
            tuple(tuple(float(value) for value in row) for row in seen.pending_admission),
            tuple(float(value) for value in seen.key_pool_bits), tuple(float(value) for value in seen.backlog_bits),
            tuple(float(value) if np.isfinite(value) else None for value in seen.urgency_s))
        clock = f"{snapshot.campaign}/allocation/segment-{self.segment}"
        window = AllocationShadowWindow(
            "author-plant-allocation", snapshot.campaign, clock + "/stream", node, clock,
            REVIEWED_PLANT_SHA256, origin.isoformat(), observation, tuple(self.history))
        # Store only this observed estimate for future calls. Never backfill after a gap.
        self.history.append(AllocationDriftSample(
            stamp, tuple(max(q - r - .005, 0.) for q, r in zip(observation.measured_qber, raman))))
        self.last_state = NetworkStateSnapshot(
            request_id=f"{clock}/frame/{tick}", node_id=node, timestamp_utc=stamp, state_version=tick,
            predicted_fidelity=.5, prediction_uncertainty=1., telemetry_age_s=0., memory_state="IDLE",
            integrity_verified=True, telemetry={TELEMETRY_KEY: window.to_dict()},
            provenance=Provenance("simulated", window.source_id, timestamp=stamp),
            metadata={"campaign_id": snapshot.campaign, "clock_id": clock, "allocation_stream_id": window.stream_id,
                      "allocation_plant_sha256": window.plant_sha256,
                      "allocation_origin_timestamp_utc": origin.isoformat(),
                      "allocation_episode_seed": self.episode.episode_seed, "allocation_step": self.episode.steps,
                      "scope": "author's HybridAllocationEnv (qnet_network), exported finite-key plant, driven by "
                               "quantum-first; 5 s virtual per publication; recommendations not applied; "
                               "fidelity fields unused"})
        return self.last_state
