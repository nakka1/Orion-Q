"""Polarisation batches from the author's plant on independent 2 ms virtual clocks.

Each display publication emits 16 new monitoring estimates per regime (32 ms
virtual), not 500 GUI updates/s. The samples are what the compensator sees in the
author's loop: the fibre of ``polarisation_plant`` (qnet_autopilot, bit-identical)
at the calibrated or development point, measured with 400 photons per basis and
reprojected into SO(3), with the plant's temperature and vibration covariates.
A 30 s plant segment feeds 937 publications; the next segment opens a new clock.
"""
from datetime import datetime, timedelta, timezone

from quantum_autopilot.control_plane.contracts import NetworkStateSnapshot
from quantum_autopilot.control_plane.optional_compensation import (
    CONVENTION, REGIMES, TELEMETRY_KEY, CompensationShadowWindow, PolarisationSample,
)
from quantum_autopilot.control_plane.author_feeds import polarisation_segment
from quantum_autopilot.core import Provenance

BATCH_SIZE = 16


class SyntheticCompensationStream:
    def __init__(self):
        self.reset()

    def reset(self):
        self.context = self.last_tick = None
        self.states = ()

    def push(self, snapshot, seed, tick):
        if snapshot.input_kind != "synthetic" or not snapshot.topology.nodes:
            self.reset()
            return ()
        node = snapshot.topology.nodes[0].id
        context = (snapshot.campaign, seed, node)
        if context != self.context or (self.last_tick is not None and tick != self.last_tick + 1):
            if context == self.context and tick == self.last_tick:
                return self.states
            self.reset()
        origin = datetime(2026, 1, 1, tzinfo=timezone.utc)
        states = []
        for point, (frequency, drift) in REGIMES.items():
            first = polarisation_segment(point, seed, 0)
            per_segment = first.updates // BATCH_SIZE
            segment, local = divmod(tick, per_segment)
            feed = first if segment == 0 else polarisation_segment(point, seed, segment)
            samples = []
            for offset in range(BATCH_SIZE):
                index = tick * BATCH_SIZE + offset
                position = local * BATCH_SIZE + offset
                temperature, rate, vibration = (float(value) for value in feed.covariates[position])
                samples.append(PolarisationSample((origin + timedelta(milliseconds=index * 2)).isoformat(),
                                                  tuple(tuple(float(value) for value in row) for row in feed.measured[position]),
                                                  temperature, rate, vibration))
            clock = snapshot.campaign + "/polarisation/" + point + (f"/segment-{segment}" if segment else "")
            window = CompensationShadowWindow("author-plant-polarisation", snapshot.campaign,
                                              clock + "/stream", node, clock, "synthetic-author-export-frame",
                                              point, frequency, drift, tuple(samples))
            states.append(NetworkStateSnapshot(
                request_id=f"{clock}/batch/{tick}", node_id=node, timestamp_utc=samples[-1].timestamp_utc,
                state_version=tick, predicted_fidelity=.5, prediction_uncertainty=1., telemetry_age_s=0.,
                memory_state="IDLE", integrity_verified=True, telemetry={TELEMETRY_KEY: window.to_dict()},
                provenance=Provenance("simulated", window.source_id, timestamp=samples[-1].timestamp_utc),
                metadata={"campaign_id": snapshot.campaign, "clock_id": clock,
                          "rotation_frame_id": window.rotation_frame_id, "rotation_convention": CONVENTION,
                          "compensation_operating_point": point, "batch_size": BATCH_SIZE,
                          "plant_seed": feed.plant_seed, "monitoring_seed": feed.monitoring_seed,
                          "scope": "author's plant (qnet_autopilot fibre and 400-photon monitoring) on a virtual clock: "
                                   "32 ms per display batch; not real-time control; fidelity fields unused"}))
        self.context, self.last_tick, self.states = context, tick, tuple(states)
        return self.states
