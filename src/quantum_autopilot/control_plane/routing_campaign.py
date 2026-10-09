"""Frozen routing campaign v1: the reviewed repeater agent in closed loop on the author's environment.

Gates first, against what the export itself declares: the 24 recorded episodes of
each variant must be regenerated action for action (sampling generator
``default_rng(policy_rng_seed)``, as the export's note says), and the held-out
tables in ``model/policy.json`` — nominal: 400 episodes from seed 500000, the
agent's generator as in the author's ``evaluate_phase2`` (``ActorCriticPolicy(rng=0)``);
long deadline: 160 episodes from seed 510000, greedy — must be regenerated for the
agent and every heuristic to 1e-12.

Then 1000 new episodes per variant (seeds 600000-600999), grouped into 20 blocks of
50 consecutive episodes. The author reports a tie with the best heuristic, so the
primary tests are two-sided: agent against greedy fidelity on success rate and on
raw return, per variant, exact paired sign-flip over the 20 block means, Holm over
the four.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

from qnet_network.agents import GreedyFidelityPolicy, RandomPolicy, ShortestPathPolicy, ThresholdPolicy
from qnet_network.agents.actor_critic import ActorCriticPolicy
from qnet_network.agents.baselines import DoNothingPolicy
from qnet_network.env import Request

from . import routing_loop as rl
from .comparative_evaluation import holm_adjust, paired_sign_flip_test
from ..model_intake.artifacts import PINS, ROUTING_ROOT, ReviewedArchive
from ..model_intake.sync_session import _constant, _unique

PROTOCOL = {
    "schema": "orion.routing-closed-loop-protocol.v1", "target_id": rl.TARGET_ID,
    "evidence": "simulated", "mode": "simulation",
    "variants": {name: {"attempt_cost": variant.attempt_cost, "deadline_scale": variant.deadline_scale,
                        "temperature": variant.temperature} for name, variant in rl.VARIANTS.items()},
    "heralding_round_trips": rl.HERALDING_ROUND_TRIPS, "memory_slots": rl.MEMORY_SIZE, "max_faults": 3,
    "standby": "greedy_fidelity",
    "evaluation_seed": 600000, "episodes": 1000, "blocks": 20,
    "policy_generator": "numpy.random.default_rng(0), one stream over the episodes in order (export convention)",
    "baselines": ["greedy_fidelity", "shortest_path", "nao_fazer_nada", "random", "threshold_0.90"],
    "author_seed_sets": {"training": "1-3000", "validation": "900000-900099", "test": "500000-500399",
                         "attempt_cost_study": "510000-"},
    "reproduction": {"exported_episodes": "examples/episodes.json of each variant",
                     "held_out": {"nominal": {"seed": 500000, "episodes": 400,
                                              "agent_generator": "ActorCriticPolicy(rng=0).rng (evaluate_phase2)"},
                                  "prazo_longo": {"seed": 510000, "episodes": 160, "agent_generator": "greedy"}},
                     "tolerance": 1e-12},
    "primary": "agent vs greedy fidelity, success rate and raw return, per variant (two-sided)",
    "test": "exact paired sign-flip test over 20 block means of 50 episodes",
    "multiplicity": "Holm over the 4 primary tests", "alpha": 0.05,
    "interpretation": "the author's analytic repeater environment; not a deployed network or permission to act",
}
SOURCES = tuple(f"quantum_autopilot/{path}" for path in (
    "control_plane/routing_campaign.py", "control_plane/routing_loop.py", "control_plane/comparative_evaluation.py",
    "model_intake/inference.py", "model_intake/artifacts.py")) + tuple(
    f"qnet_network/{path}" for path in ("env/network_env.py", "env/features.py", "env/actions.py", "env/state.py",
                                         "agents/baselines.py", "agents/rollout.py"))


def load_protocol(path):
    with Path(path).open("rb") as stream:
        raw = stream.read(16385)
    if len(raw) > 16384:
        raise ValueError("protocol exceeds 16 KiB")
    parsed = json.loads(raw, object_pairs_hook=_unique, parse_constant=_constant)
    if json.dumps(parsed, sort_keys=True) != json.dumps(PROTOCOL, sort_keys=True):
        raise ValueError("protocol differs from frozen v1; create a separately reviewed version")
    return raw, PROTOCOL


def baselines(variant, *, held_out=False):
    """The author's heuristics; the held-out tables list thresholds only for the nominal variant."""
    policies = [GreedyFidelityPolicy(), ShortestPathPolicy(), DoNothingPolicy(), RandomPolicy(rng=0)]
    if not held_out or variant == "nominal":
        policies.append(ThresholdPolicy(name="threshold_0.90", target_fidelity=0.90))
    if held_out and variant == "nominal":
        policies.append(ThresholdPolicy(name="threshold_0.94", target_fidelity=0.94))
    return policies


def reproduce(archive, protocol=PROTOCOL, progress=None):
    tolerance = protocol["reproduction"]["tolerance"]
    rows = []
    for variant in rl.VARIANTS:
        recorded = archive.json(ROUTING_ROOT + variant + "/examples/episodes.json")
        router = rl.ReplayRouter(archive, variant, np.random.default_rng(recorded["policy_rng_seed"]))
        env = rl.make_env(variant)
        mismatches = steps = outcome_mismatches = 0
        for episode in recorded["episodes"]:
            observation, _ = env.reset(request=Request(**episode["request"]), memory_size=rl.MEMORY_SIZE,
                                       rng=episode["seed"])
            router.reset()
            succeeded = False
            for step in episode["steps"]:
                index = router.act(observation)
                mismatches += index != step["chosen_index"]
                steps += 1
                observation, result = env.step(observation[2][index])
                if result.done:
                    succeeded = bool(result.terminated)
                    break
            outcome_mismatches += succeeded != episode["succeeded"]
        rows.append({"gate": "exported_episodes", "model_id": router.controller_id, "episodes": len(recorded["episodes"]),
                     "decisions": steps, "chosen_index_mismatches": int(mismatches),
                     "outcome_mismatches": int(outcome_mismatches), "passed": mismatches == 0 and outcome_mismatches == 0})

        held_out = archive.json(ROUTING_ROOT + variant + "/model/policy.json")["held_out"]
        config = protocol["reproduction"]["held_out"][variant]
        generator = (ActorCriticPolicy(rng=0).rng if variant == "nominal" else np.random.default_rng(0))
        policies = [rl.ReplayRouter(archive, variant, generator), *baselines(variant, held_out=True)]
        frame = rl.run(variant, policies, config["seed"], config["episodes"])
        means = frame.groupby("policy")[["succeeded", "raw_return"]].mean()
        expected = ({name.replace("actor_critic (T=1)", router.controller_id): value
                     for name, value in held_out["by_policy"].items()} if variant == "nominal" else
                    {router.controller_id: next(item for item in held_out["agent"] if item["temperature"] == 0.0),
                     **held_out["heuristics"]})
        errors = {name: max(abs(float(means.loc[name, "succeeded"]) - value["success"]),
                            abs(float(means.loc[name, "raw_return"]) - value["raw_return"]))
                  for name, value in expected.items()}
        rows.append({"gate": "held_out_table", "model_id": router.controller_id, "episodes": config["episodes"],
                     "seed": config["seed"], "reproduced": {name: {"success": float(means.loc[name, "succeeded"]),
                                                                   "raw_return": float(means.loc[name, "raw_return"])}
                                                            for name in expected},
                     "max_absolute_error": max(errors.values()), "passed": max(errors.values()) <= tolerance})
        if progress:
            progress(f"{variant}: {steps} recorded decisions regenerated with {mismatches} mismatches; held-out "
                     f"table max error {max(errors.values()):.1e}")
    if not all(row["passed"] for row in rows):
        raise ValueError("closed loop does not regenerate the exported routing results; no campaign")
    return rows


def block_means(values, blocks):
    return np.asarray(values, dtype=float).reshape(blocks, -1).mean(axis=1)


def compare(frame, model, reference, protocol=PROTOCOL, alternative="two-sided"):
    rows = []
    for metric in ("succeeded", "raw_return"):
        mine = frame[frame["policy"] == model].sort_values("seed")[metric].to_numpy(dtype=float)
        theirs = frame[frame["policy"] == reference].sort_values("seed")[metric].to_numpy(dtype=float)
        differences = block_means(mine - theirs, protocol["blocks"])
        test = paired_sign_flip_test(differences, alternative=alternative)
        rows.append({"metric": "success_rate" if metric == "succeeded" else "raw_return", "model_id": model,
                     "baseline": reference, "model_mean": float(mine.mean()), "baseline_mean": float(theirs.mean()),
                     "mean_difference": float((mine - theirs).mean()), "blocks_won": int(np.sum(differences > 0)),
                     "blocks_lost": int(np.sum(differences < 0)), "p_value": test["p_value"],
                     "alternative": alternative, "method": test["method"]})
    return rows


def _export(text, path):
    with Path(path).open("xb") as stream:
        stream.write(text.encode("utf-8"))


def _hashes(root):
    return {path: hashlib.sha256((root / "src" / path).read_bytes()).hexdigest() for path in SOURCES}


def run_campaign(archive_path, protocol_path, output, *, progress=None):
    raw, protocol = load_protocol(protocol_path)
    root = Path(__file__).resolve().parents[3]
    archive = ReviewedArchive(archive_path, "qnet")
    manifest = {"protocol": protocol, "protocol_file_sha256": hashlib.sha256(raw).hexdigest(),
                "archive_sha256": archive.sha256, "expected_archive_sha256": PINS["qnet"],
                "source_sha256": _hashes(root)}
    output = Path(output)
    output.mkdir(parents=False, exist_ok=False)
    _export(json.dumps(manifest, indent=2, allow_nan=False), output / "frozen-protocol.json")

    contracts = {variant: rl.check_contract(archive, variant, rl.make_env(variant)) for variant in rl.VARIANTS}
    if any(contract["status"] != "admitted" for contract in contracts.values()):
        raise ValueError("contract refused a routing variant on its declared environment")
    reproduction = reproduce(archive, protocol, progress)

    frames, primary, secondary, supervisor = [], [], [], {}
    for variant in rl.VARIANTS:
        router = rl.ReplayRouter(archive, variant, np.random.default_rng(0), max_faults=protocol["max_faults"])
        policies = [router, *baselines(variant)]
        frame = rl.run(variant, policies, protocol["evaluation_seed"], protocol["episodes"])
        frame.insert(0, "variant", variant)
        frames.append(frame)
        supervisor[variant] = {"faults": router.faults, "degraded_episodes": router.degraded_episodes}
        for row in compare(frame, router.controller_id, "greedy_fidelity", protocol):
            primary.append({"variant": variant, **row})
        for reference in ("shortest_path", "nao_fazer_nada", "random", "threshold_0.90"):
            for row in compare(frame, router.controller_id, reference, protocol):
                secondary.append({"variant": variant, **row})
        if progress:
            rates = frame.groupby("policy")["succeeded"].mean()
            progress(f"{variant}: success " + ", ".join(f"{name} {rate:.3f}" for name, rate in rates.items()))
    for row, value in zip(primary, holm_adjust([row["p_value"] for row in primary])):
        row["holm_p_value"] = float(value)
        row["significant"] = bool(value < protocol["alpha"])

    text = pd.concat(frames, ignore_index=True).to_csv(index=False, lineterminator="\n")
    _export(text, output / "routing-episodes.csv")
    if _hashes(root) != manifest["source_sha256"]:
        raise ValueError("source changed during campaign; no completion summary")
    summary = {
        "schema": "orion.routing-closed-loop-results.v1", "status": "complete", "manifest": manifest,
        "contracts": contracts, "reproduction": reproduction, "primary": primary, "secondary": secondary,
        "supervisor": supervisor,
        "artifacts": [{"file": "routing-episodes.csv", "sha256": hashlib.sha256(text.encode()).hexdigest()}],
        "policy_activated": False, "physical_action_performed": False, "simulator_action_performed": True,
        "limitations": ["The author's analytic repeater chain (four nodes), checked against SeQUeNCe by the author; "
                        "not SeQUeNCe itself in the loop.",
                        "One topology and one request pair; contention between concurrent requests not tested.",
                        "Success is binary per episode; blocks of 50 episodes carry the paired test."],
    }
    _export(json.dumps(summary, indent=2, allow_nan=False), output / "summary.json")
    return summary


__all__ = ["PROTOCOL", "baselines", "block_means", "compare", "load_protocol", "reproduce", "run_campaign"]
