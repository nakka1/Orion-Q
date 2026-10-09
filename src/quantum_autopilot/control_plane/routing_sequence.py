"""Frozen routing-in-SeQUeNCe campaign v1: the reviewed repeater agent inside SeQUeNCe's event loop.

The routing campaign v1 ran the exported agents on the author's analytic repeater
chain, which the author checked against SeQUeNCe. Here SeQUeNCe itself is the plant:
the author's ``qnet_network.env.sequence_runner`` builds a SeQUeNCe network (quantum
routers with memory arrays, single-heralded BSM nodes, lossy quantum channels,
classical channels with delay) and resolves each of the agent's primitives with
SeQUeNCe's own protocols — a GENERATE succeeds when a detector clicks. ORION-Q's
reviewed replay (``ReplayRouter``, with the greedy heuristic as hot standby) chooses
every action, as in the analytic campaign.

Gates, before any scoring:

* the author's ``runs/sequence``: the 480 rows of ``comparison.csv`` (the phase-2 agent
  and three heuristics, 40 requests, in SeQUeNCe and in the twin at 1 and 4.1 span
  traversals per attempt), its ``summary.csv``, and the heralding round timing of
  ``round_cost.csv``, regenerated with the author's ``scripts/evaluate_sequence.py``;
  the phase-2 weights come from the author's repository archive, pinned by
  ``external/VENDORED.json``;
* the analytic side of the routing campaign v1: the same 1000 requests per variant,
  whose success rates must equal the published summary.

Then, per variant, the same 1000 requests (seeds 600000-600999) in SeQUeNCe, for the
agent and the heuristics. Primary: agent against greedy fidelity on success rate,
two-sided exact paired sign-flip over 20 block means of 50 episodes, Holm over the two
variants. Secondary: each policy in SeQUeNCe against itself in the twin, on the same
requests (Holm over the eight).
"""
from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import tempfile
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd

from . import routing_loop as rl
from .comparative_evaluation import holm_adjust, paired_sign_flip_test
from .routing_campaign import PROTOCOL as ROUTING_PROTOCOL
from ..model_intake.artifacts import PINS, ReviewedArchive
from ..model_intake.sync_session import _constant, _unique

ROOT = Path(__file__).resolve().parents[3]
AUTHOR_RUNS = ROOT / "external" / "qnet_network" / "runs" / "sequence"
AUTHOR_SCRIPT = ROOT / "external" / "qnet_network" / "scripts" / "evaluate_sequence.py"
ROUTING_SUMMARY = ROOT / "docs" / "results" / "orion_q_routing_closed_loop_20260929_summary.json"
POLICIES = ("greedy_fidelity", "shortest_path", "threshold_0.90")
PROTOCOL = {
    "schema": "orion.routing-sequence-protocol.v1", "target_id": rl.TARGET_ID,
    "evidence": "simulated", "mode": "simulation",
    "plant": "SeQUeNCe discrete-event simulator through the author's qnet_network.env.sequence_runner "
             "(run_sequence_episode), configured by routing_loop.make_config(variant)",
    "variants": ROUTING_PROTOCOL["variants"], "memory_slots": rl.MEMORY_SIZE, "max_faults": 3,
    "standby": "greedy_fidelity",
    "evaluation_seed": ROUTING_PROTOCOL["evaluation_seed"], "episodes": ROUTING_PROTOCOL["episodes"],
    "blocks": ROUTING_PROTOCOL["blocks"],
    "requests": "routing_loop.make_request(default_rng(seed), variant); SeQUeNCe nodes seeded with the same seed",
    "policy_generator": ROUTING_PROTOCOL["policy_generator"],
    "baselines": list(POLICIES),
    "reproduction": {
        "author_sequence": {"script": "external/qnet_network/scripts/evaluate_sequence.py",
                            "tables": ["comparison.csv", "summary.csv", "round_cost.csv"],
                            "weights": "runs/agent/policy.npz and runs/agent/temperature.json of the author's "
                                       "repository archive (external/VENDORED.json source_sha256)",
                            "seeds": 40, "heralding_round_trips": [1.0, 4.1], "relative_tolerance": 1e-9},
        "analytic_side": {"source": "docs/results/orion_q_routing_closed_loop_20260929_summary.json",
                          "tolerance": 0.0},
    },
    "primary": "agent vs greedy fidelity, success rate, per variant, in SeQUeNCe (two-sided)",
    "secondary": "each policy in SeQUeNCe vs the same policy in the analytic twin, success rate, same requests",
    "test": "exact paired sign-flip test over 20 block means of 50 episodes",
    "multiplicity": "Holm over the 2 primary tests; Holm over the 8 secondary tests", "alpha": 0.05,
    "interpretation": "SeQUeNCe with the author's hardware parameters; a simulator, not a deployed network or "
                      "permission to act",
}
SOURCES = tuple(f"quantum_autopilot/{path}" for path in (
    "control_plane/routing_sequence.py", "control_plane/routing_loop.py", "control_plane/routing_campaign.py",
    "control_plane/comparative_evaluation.py", "model_intake/inference.py", "model_intake/artifacts.py")) + tuple(
    f"qnet_network/{path}" for path in ("env/sequence_runner.py", "env/network_env.py", "env/features.py",
                                         "env/actions.py", "env/state.py", "agents/baselines.py",
                                         "agents/rollout.py", "compat/__init__.py"))


def load_protocol(path):
    with Path(path).open("rb") as stream:
        raw = stream.read(16385)
    if len(raw) > 16384:
        raise ValueError("protocol exceeds 16 KiB")
    parsed = json.loads(raw, object_pairs_hook=_unique, parse_constant=_constant)
    if json.dumps(parsed, sort_keys=True) != json.dumps(PROTOCOL, sort_keys=True):
        raise ValueError("protocol differs from frozen v1; create a separately reviewed version")
    return raw, PROTOCOL


def simulator():
    """SeQUeNCe through the author's bootstrap; what was loaded, for the manifest."""
    from qnet_network.compat import bootstrap

    status = bootstrap()
    commit = None
    head = Path(status.sequence_path).parent / ".git" / "HEAD"
    if head.is_file():
        reference = head.read_text().strip()
        if reference.startswith("ref: "):
            target = head.parent / reference[5:]
            reference = target.read_text().strip() if target.is_file() else reference
        commit = reference
    return {"sequence_version": status.sequence_version, "sequence_commit": commit,
            "shimmed": list(status.shimmed), "stim_available": bool(status.stim_available)}


def author_module():
    spec = importlib.util.spec_from_file_location("orion_author_evaluate_sequence", AUTHOR_SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def author_archive_sha256():
    manifest = json.loads((ROOT / "external" / "VENDORED.json").read_text(encoding="utf-8"))
    return next(item["source_sha256"] for item in manifest["vendored"] if item["package"] == "src/qnet_network")


def _table_errors(ours, theirs, keys, tolerance):
    """Worst relative error per column after aligning on ``keys``; missing or extra rows fail."""
    merged = theirs.merge(ours, on=keys, how="outer", suffixes=("_author", "_orion"), indicator=True)
    unmatched = int((merged["_merge"] != "both").sum())
    columns, worst = {}, 0.0
    for column in (name for name in theirs.columns if name not in keys):
        author, orion = merged[column + "_author"], merged[column + "_orion"]
        if author.dtype.kind in "fiu" and orion.dtype.kind in "fiu":
            author, orion = author.to_numpy(float), orion.to_numpy(float)
            both_nan = np.isnan(author) & np.isnan(orion)
            scale = np.where(author == 0.0, 1.0, np.abs(author))
            error = np.where(both_nan, 0.0, np.abs(author - orion) / scale)
            error = float(np.nan_to_num(error, nan=np.inf).max()) if len(error) else 0.0
        else:
            error = float((author.astype(str) != orion.astype(str)).sum() > 0)
        columns[column] = error
        worst = max(worst, error)
    return {"rows": int(len(theirs)), "unmatched_rows": unmatched, "max_relative_error": worst,
            "passed": unmatched == 0 and worst <= tolerance, "columns": columns}


def reproduce_author(author_zip, protocol=PROTOCOL):
    """The author's runs/sequence, regenerated with the author's script and the author's phase-2 weights."""
    config = protocol["reproduction"]["author_sequence"]
    digest = hashlib.sha256(Path(author_zip).read_bytes()).hexdigest()
    if digest != author_archive_sha256():
        raise ValueError("author repository archive differs from external/VENDORED.json")
    module = author_module()
    with tempfile.TemporaryDirectory() as scratch, zipfile.ZipFile(author_zip) as archive:
        folder = Path(scratch) / "runs" / "agent"
        folder.mkdir(parents=True)
        for member in ("policy.npz", "temperature.json"):
            (folder / member).write_bytes(archive.read("runs/agent/" + member))
        policies = module.build_policies(folder / "policy.npz")
    frames = []
    for index, overhead in enumerate(config["heralding_round_trips"]):
        frame = module.compare(policies, range(config["seeds"]), module.MEMORY_SIZE, heralding_round_trips=overhead)
        if index > 0:
            frame = frame[frame["simulator"] != "SeQUeNCe"]
        frames.append(frame)
    frame = pd.concat(frames, ignore_index=True)
    frame = pd.read_csv(io.StringIO(frame.to_csv(index=False)))   # as the author's CSV round trip
    summary = frame.groupby(["policy", "simulator"]).agg(
        success=("succeeded", "mean"), fidelity=("mean_fidelity", "mean"),
        elapsed_ms=("elapsed_s", lambda values: 1e3 * values.mean()), attempts=("attempts", "mean"),
        swaps=("swaps", "mean"), purifications=("purifications", "mean"),
        disagreements=("legality_disagreements", "sum")).reset_index()
    cost = module.study_round_cost()
    tolerance = config["relative_tolerance"]
    gates = [
        {"gate": "comparison.csv", "policies": [policy.name for policy in policies],
         **_table_errors(frame, pd.read_csv(AUTHOR_RUNS / "comparison.csv"), ["simulator", "policy", "seed"],
                         tolerance)},
        {"gate": "summary.csv",
         **_table_errors(summary, pd.read_csv(AUTHOR_RUNS / "summary.csv"), ["policy", "simulator"], tolerance)},
        {"gate": "round_cost.csv", **_table_errors(cost, pd.read_csv(AUTHOR_RUNS / "round_cost.csv"), ["span_km"],
                                                     tolerance)},
    ]
    return gates, digest


def policies_for(archive, variant, protocol=PROTOCOL):
    from qnet_network.agents import GreedyFidelityPolicy, ShortestPathPolicy, ThresholdPolicy

    router = rl.ReplayRouter(archive, variant, np.random.default_rng(0), max_faults=protocol["max_faults"])
    return router, [router, GreedyFidelityPolicy(), ShortestPathPolicy(),
                    ThresholdPolicy(name="threshold_0.90", target_fidelity=0.90)]


def run_sequence(archive, variant, protocol=PROTOCOL):
    """Every policy through the same requests inside SeQUeNCe; one row per (policy, episode)."""
    from qnet_network.env.sequence_runner import run_sequence_episode

    config = rl.make_config(variant)
    router, policies = policies_for(archive, variant, protocol)
    rows = []
    for policy in policies:
        for index in range(protocol["episodes"]):
            seed = protocol["evaluation_seed"] + index
            request = rl.make_request(np.random.default_rng(seed), variant)
            record = run_sequence_episode(config, policy, request, seed=seed, memory_size=protocol["memory_slots"])
            rows.append({**record, "policy": policy.name, "elapsed_ms": record["elapsed_s"] * 1e3})
    frame = pd.DataFrame(rows).drop(columns=["elapsed_s"])
    return frame, {"faults": router.faults, "degraded_episodes": router.degraded_episodes}


def run_analytic(archive, variant, protocol=PROTOCOL):
    _, policies = policies_for(archive, variant, protocol)
    return rl.run(variant, policies, protocol["evaluation_seed"], protocol["episodes"])


def analytic_gate(variant, analytic, controller_id):
    """The analytic side must equal the published routing campaign v1, policy by policy."""
    published = json.loads(ROUTING_SUMMARY.read_text(encoding="utf-8"))
    expected = {}
    for row in published["primary"] + published["secondary"]:
        if row["variant"] == variant and row["metric"] == "success_rate":
            expected[row["model_id"]] = row["model_mean"]
            expected[row["baseline"]] = row["baseline_mean"]
    rates = analytic.groupby("policy")["succeeded"].mean()
    names = [controller_id, *POLICIES]
    errors = {name: abs(float(rates[name]) - expected[name]) for name in names}
    return {"gate": "analytic_side", "variant": variant, "episodes": int(len(analytic) // len(names)),
            "success_rates": {name: float(rates[name]) for name in names}, "max_absolute_error": max(errors.values()),
            "passed": max(errors.values()) == 0.0}


def _blocks(values, blocks):
    return np.asarray(values, dtype=float).reshape(blocks, -1).mean(axis=1)


def paired(mine, theirs, blocks):
    differences = _blocks(np.asarray(mine, float) - np.asarray(theirs, float), blocks)
    test = paired_sign_flip_test(differences, alternative="two-sided")
    return {"mean_difference": float(np.mean(np.asarray(mine, float) - np.asarray(theirs, float))),
            "blocks_won": int(np.sum(differences > 0)), "blocks_lost": int(np.sum(differences < 0)),
            "p_value": test["p_value"], "method": test["method"]}


def _series(frame, policy, column="succeeded"):
    return frame[frame["policy"] == policy].sort_values("seed")[column].to_numpy(dtype=float)


def evaluate_variant(args):
    archive_path, variant, protocol = args
    archive = ReviewedArchive(archive_path, "qnet")
    contract = rl.check_contract(archive, variant, rl.make_env(variant))
    if contract["status"] != "admitted":
        raise ValueError(f"contract refused {variant}")
    analytic = run_analytic(archive, variant, protocol)
    sequence, supervisor = run_sequence(archive, variant, protocol)
    controller = f"qnet.routing.{variant}"
    gate = analytic_gate(variant, analytic, controller)
    blocks = protocol["blocks"]
    policies = {}
    for name in (controller, *POLICIES):
        rows = sequence[sequence["policy"] == name]
        delivered = rows["mean_fidelity"].dropna()
        policies[name] = {"success_rate": float(rows["succeeded"].mean()),
                          "twin_success_rate": float(analytic[analytic["policy"] == name]["succeeded"].mean()),
                          "mean_delivered_fidelity": float(delivered.mean()) if len(delivered) else None,
                          "mean_elapsed_ms": float(rows["elapsed_ms"].mean()),
                          "mean_attempts": float(rows["attempts"].mean()), "mean_swaps": float(rows["swaps"].mean()),
                          "mean_purifications": float(rows["purifications"].mean()),
                          "legality_disagreements": int(rows["legality_disagreements"].sum())}
    primary = {"variant": variant, "metric": "success_rate", "model_id": controller, "baseline": "greedy_fidelity",
               "model_mean": policies[controller]["success_rate"],
               "baseline_mean": policies["greedy_fidelity"]["success_rate"],
               **paired(_series(sequence, controller), _series(sequence, "greedy_fidelity"), blocks)}
    transfer = [{"variant": variant, "policy": name, "sequence_mean": policies[name]["success_rate"],
                 "twin_mean": policies[name]["twin_success_rate"],
                 **paired(_series(sequence, name), _series(analytic, name), blocks)}
                for name in (controller, *POLICIES)]
    return {"variant": variant, "contract": contract, "gate": gate, "policies": policies, "primary": primary,
            "transfer": transfer, "supervisor": supervisor,
            "episodes_csv": sequence.assign(variant=variant).to_csv(index=False, lineterminator="\n")}


def _export(text, path):
    with Path(path).open("xb") as stream:
        stream.write(text.encode("utf-8"))


def _hashes():
    return {path: hashlib.sha256((ROOT / "src" / path).read_bytes()).hexdigest() for path in SOURCES}


def run_campaign(archive_path, author_zip, protocol_path, output, *, progress=None, workers=1):
    raw, protocol = load_protocol(protocol_path)
    tools = simulator()
    archive = ReviewedArchive(archive_path, "qnet")
    manifest = {"protocol": protocol, "protocol_file_sha256": hashlib.sha256(raw).hexdigest(),
                "archive_sha256": archive.sha256, "expected_archive_sha256": PINS["qnet"],
                "author_archive_sha256": author_archive_sha256(), "source_sha256": _hashes()}
    output = Path(output)
    output.mkdir(parents=False, exist_ok=False)
    _export(json.dumps({**manifest, "tools": tools}, indent=2, allow_nan=False), output / "frozen-protocol.json")

    author_gates, _ = reproduce_author(author_zip, protocol)
    if progress:
        for row in author_gates:
            progress(f"author {row['gate']}: {row['rows']} rows, max relative error {row['max_relative_error']:.1e}")
    if not all(row["passed"] for row in author_gates):
        _export(json.dumps({"status": "gate_failed", "gates": author_gates}, indent=2), output / "gate-failed.json")
        raise ValueError("the author's SeQUeNCe tables were not regenerated; no campaign")

    tasks = [(str(archive_path), variant, protocol) for variant in rl.VARIANTS]
    if workers > 1:
        from concurrent.futures import ProcessPoolExecutor

        with ProcessPoolExecutor(max_workers=min(workers, len(tasks)), initializer=_worker_init,
                                 initargs=(PINS["qnet"],)) as pool:
            results = list(pool.map(evaluate_variant, tasks))
    else:
        results = [evaluate_variant(task) for task in tasks]
    gates = author_gates + [result["gate"] for result in results]
    if not all(row["passed"] for row in gates):
        _export(json.dumps({"status": "gate_failed", "gates": gates}, indent=2), output / "gate-failed.json")
        raise ValueError("the analytic side differs from the routing campaign v1; no campaign")

    primary = [result["primary"] for result in results]
    for row, value in zip(primary, holm_adjust([row["p_value"] for row in primary])):
        row["holm_p_value"] = float(value)
        row["significant"] = bool(value < protocol["alpha"])
    transfer = [row for result in results for row in result["transfer"]]
    for row, value in zip(transfer, holm_adjust([row["p_value"] for row in transfer])):
        row["holm_p_value"] = float(value)
        row["significant"] = bool(value < protocol["alpha"])
    if progress:
        for row in primary:
            progress(f"{row['variant']}: SeQUeNCe success agent {row['model_mean']:.3f} vs greedy "
                     f"{row['baseline_mean']:.3f}, Holm p {row['holm_p_value']:.2g}")
    text = "".join(result["episodes_csv"] if index == 0 else result["episodes_csv"].split("\n", 1)[1]
                   for index, result in enumerate(results))
    _export(text, output / "sequence-episodes.csv")
    if _hashes() != manifest["source_sha256"]:
        raise ValueError("source changed during campaign; no completion summary")
    summary = {
        "schema": "orion.routing-sequence-results.v1", "status": "complete", "manifest": manifest, "tools": tools,
        "contracts": {result["variant"]: result["contract"] for result in results}, "reproduction": gates,
        "policies": [{"variant": result["variant"], "policy": name, **values}
                     for result in results for name, values in result["policies"].items()],
        "primary": primary, "transfer": transfer,
        "supervisor": {result["variant"]: result["supervisor"] for result in results},
        "artifacts": [{"file": "sequence-episodes.csv", "sha256": hashlib.sha256(text.encode()).hexdigest()}],
        "policy_activated": False, "physical_action_performed": False, "simulator_action_performed": True,
        "limitations": ["SeQUeNCe with the author's hardware parameters and single-heralded generation; a "
                        "simulator, not a network.",
                        "One topology (four-node chain) and one request pair.",
                        "The agent was trained in the analytic twin, not in SeQUeNCe."],
    }
    _export(json.dumps(summary, indent=2, allow_nan=False), output / "summary.json")
    return summary


def _worker_init(pin):
    """Workers inherit the reviewed pin of the parent (the cloud run patches it for a rebuilt archive)."""
    PINS["qnet"] = pin


__all__ = ["PROTOCOL", "analytic_gate", "load_protocol", "reproduce_author", "run_campaign", "run_sequence",
           "simulator"]
