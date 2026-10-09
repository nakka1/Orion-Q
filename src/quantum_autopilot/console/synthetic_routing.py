"""Routing decisions from the author's repeater environment on a 1 s virtual clock.

Each tick is one decision of ``qnet_network``'s ``QuantumNetworkEnv`` in the exported
nominal configuration (four-node chain, 20 km spans, heralding at 4.1 traversals),
driven by the greedy-fidelity heuristic — the incumbent. The window carries the
environment's own eleven state features and 23 features per legal action, the
live pairs and memories as instantaneous resources, and the incumbent's choice as
the declared reference. Recommendations are scored, never applied.
"""
from datetime import datetime, timedelta, timezone
import random

from quantum_autopilot.control_plane.author_feeds import RoutingEpisodes
from quantum_autopilot.control_plane.contracts import NetworkStateSnapshot
from quantum_autopilot.control_plane.optional_routing import (
    RESOURCE_RULESET, TELEMETRY_KEY, WINDOW_SCHEMA, RoutingShadowWindow,
)
from quantum_autopilot.core import Provenance
from quantum_autopilot.model_intake.routing_session import ACTION_NAMES, FEATURE_CONTRACT, KINDS, STATE_NAMES


def _names(host):
    return {"0": host, "1": host + "/routing-node-1", "2": host + "/routing-node-2", "3": host + "/routing-node-3"}


def window_payload(episodes, host, decision, reference_index, version):
    """Map the author's observation and state to the ORION-Q routing window (no invented values)."""
    env = episodes.env
    names = _names(host)
    state, features, actions = episodes.observation
    now = env.state.now_s
    memory = env.config.memory
    cutoff = memory.cutoff_ratio * memory.coherence_time_s
    pairs = []
    for pair in sorted(env.state.pairs.values(), key=lambda item: item.pair_id):
        created = decision - timedelta(seconds=now - pair.created_s)
        pairs.append(dict(pair_id=int(pair.pair_id), endpoints=[names[pair.node_a], names[pair.node_b]],
                          fidelity=float(pair.fidelity_at(now, memory)), created_timestamp_utc=created.isoformat(),
                          expires_timestamp_utc=(created + timedelta(seconds=cutoff)).isoformat(), reserved=False))
    stamp = decision.isoformat()
    resources = dict(
        ruleset=RESOURCE_RULESET, timestamp_utc=stamp, state_version=int(version), enabled_kinds=list(KINDS),
        pairs=pairs, nodes=[dict(node_id=names[node], memory_capacity=int(env.state.memory_size), reserved_slots=0,
                                 available=True, can_swap=True, can_purify=True) for node in sorted(names)],
        links=[dict(endpoints=[names[str(u)], names[str(v)]], available=True) for u, v in env.config.graph.edges()])
    candidates = []
    for index, (action, row) in enumerate(zip(actions, features)):
        candidates.append(dict(
            action_id=f"{index}:{action.kind.name.lower()}",
            action=dict(kind=action.kind.name, link=[names[str(n)] for n in action.link] if action.link else None,
                        pair_ids=[int(item) for item in action.pair_ids],
                        node=names[str(action.node)] if action.node is not None else None),
            features={name: float(value) for name, value in zip(ACTION_NAMES, row)}))
    request = env.request
    return dict(state_features={name: float(value) for name, value in zip(STATE_NAMES, state)},
                candidates=candidates, reference_action_id=candidates[reference_index]["action_id"],
                task=dict(endpoints=[names[request.source], names[request.destination]],
                          minimum_fidelity=float(request.min_fidelity)), resources=resources)


class SyntheticRoutingStream:
    def __init__(self):
        self.segment = 0
        self.reset()

    def reset(self):
        self.context = self.last_tick = self.last_state = self.episodes = None
        self.segment += 1

    def push(self, snapshot, seed, tick):
        if snapshot.input_kind != "synthetic" or not snapshot.topology.nodes:
            self.reset()
            return None
        if type(tick) is not int or tick < 0:
            raise ValueError("nonnegative integer virtual tick required")
        host = snapshot.topology.nodes[0].id
        context = (snapshot.campaign, snapshot.capabilities.adapter_id, seed, host)
        if self.context is not None and (context != self.context or tick not in (self.last_tick, self.last_tick + 1)):
            self.reset()
        if tick == self.last_tick:
            return self.last_state
        self.context, self.last_tick = context, tick
        origin = datetime(2026, 1, 1, tzinfo=timezone.utc)
        decision = origin + timedelta(seconds=tick)
        stamp = decision.isoformat()
        clock = f"{snapshot.campaign}/routing/segment-{self.segment}"
        if self.episodes is None:
            self.episodes = RoutingEpisodes(seed, self.segment)
        else:
            self.episodes.advance()
        reference = self.episodes.decide()
        payload = window_payload(self.episodes, host, decision, reference, tick)
        window = RoutingShadowWindow.from_dict(dict(
            schema=WINDOW_SCHEMA, evidence="simulated", feature_contract=FEATURE_CONTRACT,
            source_id="author-plant-routing", campaign_id=snapshot.campaign, stream_id=clock + "/stream",
            node_id=host, clock_id=clock, origin_timestamp_utc=origin.isoformat(), decision_timestamp_utc=stamp,
            features_timestamp_utc=stamp, seed=random.Random(f"routing-policy:{seed}:{tick}").randrange(2**32),
            **payload))
        self.last_state = NetworkStateSnapshot(
            request_id=f"{clock}/frame/{tick}", node_id=host, timestamp_utc=stamp, state_version=tick,
            predicted_fidelity=.5, prediction_uncertainty=1., telemetry_age_s=0., memory_state="IDLE",
            integrity_verified=True, telemetry={TELEMETRY_KEY: window.to_dict()},
            provenance=Provenance("simulated", "author-plant-routing", timestamp=stamp),
            metadata=dict(campaign_id=snapshot.campaign, clock_id=clock, routing_stream_id=clock + "/stream",
                          routing_origin_timestamp_utc=origin.isoformat(), routing_ruleset=RESOURCE_RULESET,
                          routing_resource_sha256=window.resource_sha256,
                          routing_episode=self.episodes.episode, routing_step=self.episodes.steps,
                          scope="Author's QuantumNetworkEnv (qnet_network), exported nominal configuration, driven by "
                                "the greedy-fidelity heuristic; the environment's own features; the incumbent's choice "
                                "is the declared reference; 1 s virtual per decision; recommendations not applied."))
        return self.last_state
