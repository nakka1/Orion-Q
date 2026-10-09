"""Timing stream from the author's clock model: current samples only, never backfill a missing past.

Each 1 s virtual tick publishes one offset estimate of ``qnet_qkd``'s ``ClockModel``
at 50 km (the synchroniser's training span), measured from 2000 photons exactly as
``run_sync_loop`` does, with the fibre temperature. One simulated hour per segment.
"""
from collections import deque
from datetime import datetime

from quantum_autopilot.control_plane.contracts import NetworkStateSnapshot
from quantum_autopilot.control_plane.optional_sync import SyncSample, SyncShadowWindow, TELEMETRY_KEY
from quantum_autopilot.control_plane.author_feeds import CLOCK_SEGMENT_S, clock_segment
from quantum_autopilot.core import Provenance


class SyntheticSyncStream:
    def __init__(self):
        self.samples = deque(maxlen=8)
        self.context = None
        self.last_tick = None
        self.last_state = None

    def reset(self):
        self.samples.clear()
        self.context = self.last_tick = self.last_state = None

    def push(self, snapshot, seed, tick):
        if snapshot.input_kind != "synthetic" or not snapshot.topology.nodes:
            self.reset()
            return None
        node = snapshot.topology.nodes[0].id
        context = (snapshot.campaign, seed, node)
        if context != self.context or (self.last_tick is not None and tick not in (self.last_tick, self.last_tick + 1)):
            self.reset()
        if tick == self.last_tick:
            return self.last_state
        self.context, self.last_tick = context, tick
        segment, index = divmod(tick, CLOCK_SEGMENT_S)
        feed = clock_segment(seed, segment)
        sample = SyncSample(snapshot.at, float(feed.measured_offset_s[index]), float(feed.temperature_c[index]))
        if index == 0:
            self.samples.clear()  # a new simulated hour is a new trajectory, not a continuation
        if self.samples and (datetime.fromisoformat(sample.timestamp_utc) -
                             datetime.fromisoformat(self.samples[-1].timestamp_utc)).total_seconds() != 1.:
            self.samples.clear()
        self.samples.append(sample)
        self.last_state = None
        if len(self.samples) < 8:
            return None
        window = SyncShadowWindow("author-plant-timing", snapshot.campaign,
                                  snapshot.campaign + "/timing", node, snapshot.campaign, tuple(self.samples))
        self.last_state = NetworkStateSnapshot(
            request_id=f"{snapshot.campaign}/sync/{tick}", node_id=node, timestamp_utc=snapshot.at,
            state_version=tick, predicted_fidelity=.5, prediction_uncertainty=1., telemetry_age_s=0.,
            memory_state="IDLE", integrity_verified=True, telemetry={TELEMETRY_KEY: window.to_dict()},
            provenance=Provenance("simulated", "author-plant-timing", timestamp=snapshot.at),
            metadata={"campaign_id": snapshot.campaign, "clock_id": snapshot.campaign,
                      "clock_segment": segment, "trajectory_seed": feed.trajectory_seed,
                      "scope": "author's ClockModel (qnet_qkd) at 50 km, 2000-photon offset estimates; "
                               "fidelity fields are unused envelope placeholders, not predictions"})
        return self.last_state
