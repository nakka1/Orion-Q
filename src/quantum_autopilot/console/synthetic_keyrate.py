"""Decoy-state periods from the author's link on a virtual clock; recommendations are never consumed.

Each 1 s virtual tick is one period of ``qnet_qkd``'s ``KeyRateEnv`` at 50 km, the
benchmark link, held at its commissioned settings (the incumbent): the stream
publishes what the receiver measured in the period just ended, with the decoy
bounds and the provable key, plus the link's commissioning calibration. One
240-period episode per segment; recommendations are scored, never applied.
"""
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
import random

from quantum_autopilot.control_plane.contracts import NetworkStateSnapshot
from quantum_autopilot.control_plane.optional_keyrate import TELEMETRY_KEY, UNITS, WINDOW_SCHEMA, KeyRateShadowWindow
from quantum_autopilot.control_plane.optional_sync import _hash, _json
from quantum_autopilot.control_plane.author_feeds import KEYRATE_SPAN_KM, KeyRateEpisode
from quantum_autopilot.core import Provenance


class SyntheticKeyRateStream:
    def __init__(self):
        self.segment = 0
        self.reset()

    def reset(self):
        self.context = self.last_tick = self.last_state = self.origin = self.episode = None
        self.segment += 1

    def push(self, snapshot, seed, tick):
        if snapshot.input_kind != "synthetic" or not snapshot.topology.nodes:
            self.reset()
            return None
        if type(tick) is not int or tick < 0:
            raise ValueError("nonnegative integer virtual tick required")
        host = snapshot.topology.nodes[0].id
        context = (snapshot.campaign, snapshot.capabilities.adapter_id, seed, host)
        decision = datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(seconds=tick)
        if self.context is not None and (context != self.context or tick not in (self.last_tick, self.last_tick + 1)
                                        or decision >= self.origin + timedelta(days=1)
                                        or (tick != self.last_tick and self.episode.finished)):
            self.reset()
        if tick == self.last_tick:
            return self.last_state
        bootstrap = self.last_tick is None
        if bootstrap:
            self.origin = decision
            self.episode = KeyRateEpisode(seed, self.segment)
        else:
            self.episode.advance()
        observed, transmitter, commissioning = self.episode.publish()
        clock = f"{snapshot.campaign}/keyrate/segment-{self.segment}"
        stamp = decision.isoformat()
        settings, calibration = asdict(transmitter), asdict(commissioning)
        link_context = dict(link_id=host + "/keyrate-link", transmitter_id=host, receiver_id=host + "/keyrate-receiver",
                            protocol="decoy_state_bb84", link_span_km=KEYRATE_SPAN_KM, settings_version=self.segment,
                            settings_timestamp_utc=self.origin.isoformat(), settings=settings,
                            calibration_id=clock + "/calibration", calibration=calibration,
                            calibration_timestamp_utc=(self.origin - timedelta(seconds=1)).isoformat(),
                            calibration_valid_until_utc=(self.origin + timedelta(days=1)).isoformat())
        previous = None if bootstrap else dict(
            start_timestamp_utc=(decision - timedelta(seconds=1)).isoformat(), end_timestamp_utc=stamp,
            settings_sha256=_hash(_json(settings)), observables=asdict(observed))
        window = KeyRateShadowWindow.from_dict(dict(
            schema=WINDOW_SCHEMA, evidence="simulated", source_id="author-plant-keyrate",
            campaign_id=snapshot.campaign, stream_id=clock + "/stream", node_id=host, clock_id=clock,
            origin_timestamp_utc=self.origin.isoformat(), decision_timestamp_utc=stamp,
            seed=random.Random(f"keyrate-policy:{seed}:{tick}").randrange(2**32), units=UNITS,
            observation_mode="calibration_bootstrap" if bootstrap else "previous_period",
            context=link_context, previous=previous))
        self.context, self.last_tick = context, tick
        self.last_state = NetworkStateSnapshot(
            request_id=f"{clock}/frame/{tick}", node_id=host, timestamp_utc=stamp, state_version=self.segment,
            predicted_fidelity=.5, prediction_uncertainty=1., telemetry_age_s=0., memory_state="IDLE",
            integrity_verified=True, telemetry={TELEMETRY_KEY: window.to_dict()},
            provenance=Provenance("simulated", "author-plant-keyrate", timestamp=stamp),
            metadata=dict(campaign_id=snapshot.campaign, clock_id=clock, keyrate_stream_id=clock + "/stream",
                          keyrate_origin_timestamp_utc=self.origin.isoformat(), keyrate_context_sha256=window.context_sha256,
                          keyrate_plant_seed=self.episode.plant_seed, keyrate_period=self.episode.periods,
                          scope="Author's KeyRateEnv (qnet_qkd) at 50 km held at commissioned settings; 1 s virtual "
                                "periods; recommendations are not applied. The calibration validity window is declared by the feed."))
        return self.last_state
