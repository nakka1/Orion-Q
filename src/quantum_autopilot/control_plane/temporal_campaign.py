"""Bounded paired-seed experiments on one frozen, simulated two-link path.

No model selection, measured evidence, physical I/O or workspace mutation.
Only completed campaigns are journaled; computation holds no database lock.
"""
import json
import math
import re
from collections.abc import Mapping
from contextlib import closing
from dataclasses import asdict, dataclass
from statistics import NormalDist, fmean

from .temporal_path import TemporalPathRequest, evaluate_temporal_path
from .topology import (
    DEFAULT_TOPOLOGY_ID, SimulatedTopology, default_simulated_topology,
    _canonical_json, _sha256, _utc_now,
)
from .workspace_path import WorkspacePathRequest, selected_path


@dataclass(frozen=True)
class TemporalRegime:
    regime_id: str
    success_probability_left: float
    success_probability_right: float
    memory_t1_s: float
    memory_t2_s: float
    memory_ttl_steps: int

    def __post_init__(self):
        if not isinstance(self.regime_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,31}", self.regime_id):
            raise ValueError("bounded ASCII regime_id required")
        TemporalPathRequest("validate", "left", "middle", "right", 0, .8, **self.parameters())

    def parameters(self):
        return {k: v for k, v in asdict(self).items() if k != "regime_id"}

    @classmethod
    def from_dict(cls, value):
        if not isinstance(value, Mapping) or set(value) != set(cls.__dataclass_fields__):
            raise ValueError("exact regime fields required")
        return cls(**value)


@dataclass(frozen=True)
class TemporalCampaignRequest:
    campaign_id: str
    source: str
    repeater: str
    destination: str
    expected_version: int
    target_fidelity: float
    attempt_interval_s: float
    max_attempts: int
    seeds: tuple[int, ...]
    regimes: tuple[TemporalRegime, ...]
    baseline_id: str
    topology_id: str = DEFAULT_TOPOLOGY_ID
    protocol: str = "paired_temporal_path_v1"

    def __post_init__(self):
        WorkspacePathRequest(self.campaign_id, self.source, self.repeater, self.destination,
                             self.expected_version, self.target_fidelity, self.topology_id)
        if self.protocol != "paired_temporal_path_v1":
            raise ValueError("unsupported campaign protocol")
        if type(self.seeds) is not tuple or not 2 <= len(self.seeds) <= 64:
            raise ValueError("2 to 64 seeds required as an immutable tuple")
        if any(type(seed) is not int or not 0 <= seed <= 2**32-1 for seed in self.seeds) or len(set(self.seeds)) != len(self.seeds):
            raise ValueError("unique uint32 seeds required")
        if type(self.regimes) is not tuple or not 2 <= len(self.regimes) <= 4 or any(type(r) is not TemporalRegime for r in self.regimes):
            raise ValueError("2 to 4 validated regimes required")
        ids = [r.regime_id for r in self.regimes]
        if len(set(ids)) != len(ids) or self.baseline_id not in ids:
            raise ValueError("unique regime IDs and an explicit included baseline required")
        # Route, deadline, target and time grid cannot vary between regimes.
        for regime in self.regimes:
            self.trial_request(regime, self.seeds[0])
        if len(self.seeds) * len(self.regimes) * self.max_attempts > 64000:
            raise ValueError("campaign exceeds the 64000 tick evaluation budget")

    def trial_request(self, regime, seed):
        return TemporalPathRequest(
            run_id=f"trial-{regime.regime_id}-{seed}", source=self.source, repeater=self.repeater,
            destination=self.destination, expected_version=self.expected_version,
            target_fidelity=self.target_fidelity, topology_id=self.topology_id,
            attempt_interval_s=self.attempt_interval_s, max_attempts=self.max_attempts,
            seed=seed, **regime.parameters())

    @classmethod
    def from_dict(cls, value):
        if not isinstance(value, Mapping) or set(value) != set(cls.__dataclass_fields__):
            raise ValueError("exact campaign fields required")
        payload = dict(value)
        if type(payload["seeds"]) is not list or type(payload["regimes"]) is not list:
            raise ValueError("seeds and regimes must be JSON arrays")
        payload["seeds"] = tuple(payload["seeds"])
        payload["regimes"] = tuple(TemporalRegime.from_dict(r) for r in payload["regimes"])
        return cls(**payload)


def _wilson(successes, total):
    """Pointwise 95% Wilson score interval for independent seeded trials."""
    z = NormalDist().inv_cdf(.975)
    p = successes / total
    denominator = 1 + z*z / total
    center = (p + z*z / (2*total)) / denominator
    half = z * math.sqrt(p*(1-p)/total + z*z/(4*total*total)) / denominator
    return [max(0., center-half), min(1., center+half)]


def _summary(trials):
    results = [trial["result"] for trial in trials]
    total = len(results)
    available = [r for r in results if r["pairs_available"]]
    hits = sum(r["outcome"] == "target_met" for r in results)
    return {
        "trials": total, "swaps": len(available), "target_met": hits,
        "target_not_met": len(available)-hits, "timeouts": total-len(available),
        "target_probability": hits/total, "target_probability_wilson_95": _wilson(hits, total),
        "swap_probability": len(available)/total,
        "swap_probability_wilson_95": _wilson(len(available), total),
        "conditional_target_probability": hits/len(available) if available else None,
        "conditional_fidelity_mean": fmean(r["conditional_fidelity"] for r in available) if available else None,
        "conditional_fidelity_n": len(available),
        "termination_time_mean_s_all_trials": fmean(r["elapsed_s"] for r in results),
        "swap_time_mean_s_conditional": fmean(r["elapsed_s"] for r in available) if available else None,
        "generated_pairs_mean_all_trials": fmean(sum(r["accounting"]["generated_per_link"]) for r in results),
        "expired_pairs_mean_all_trials": fmean(sum(r["accounting"]["expired_per_link"]) for r in results),
    }


def _comparisons(grouped, baseline_id):
    """Exact two-sided McNemar tests; one Holm family vs the declared baseline."""
    base = {t["seed"]: t["result"]["outcome"] == "target_met" for t in grouped[baseline_id]}
    rows = []
    for name, trials in grouped.items():
        if name == baseline_id:
            continue
        candidate = {t["seed"]: t["result"]["outcome"] == "target_met" for t in trials}
        wins = sum(candidate[s] and not base[s] for s in base)
        losses = sum(base[s] and not candidate[s] for s in base)
        discordant = wins + losses
        p_value = min(1., 2 * sum(math.comb(discordant, k) for k in range(min(wins, losses)+1)) / 2**discordant) if discordant else 1.
        rows.append({"regime_id": name, "baseline_id": baseline_id, "paired_trials": len(base),
                     "candidate_only_target_met": wins, "baseline_only_target_met": losses,
                     "both_target_met": sum(candidate[s] and base[s] for s in base),
                     "neither_target_met": sum(not candidate[s] and not base[s] for s in base),
                     "target_probability_difference": (wins-losses)/len(base),
                     "p_value_exact_two_sided": p_value})
    # Keep this small binary-outcome analysis independent of the ML evaluator.
    running = 0.
    for rank, row in enumerate(sorted(rows, key=lambda r: r["p_value_exact_two_sided"])):
        running = max(running, min(1., (len(rows)-rank)*row["p_value_exact_two_sided"]))
        row["p_value_holm"] = running
        row["reject_equal_target_probability_at_005"] = running <= .05
    return rows


class CampaignCancelled(Exception):
    """Cooperative stop before accepting a completed campaign."""


def _check_cancel(cancelled):
    if cancelled is not None and cancelled():
        raise CampaignCancelled("campaign cancelled before commit")


def evaluate_temporal_campaign(topology, request, *, progress=None, cancelled=None):
    if type(request) is not TemporalCampaignRequest:
        raise ValueError("validated TemporalCampaignRequest required")
    if topology.topology_id != request.topology_id or topology.topology_version != request.expected_version:
        raise ValueError("topology version or ID conflict; reload the manifest")
    selected_path(topology, request)
    grouped = {}
    completed = 0
    total = len(request.regimes)*len(request.seeds)
    for regime in request.regimes:
        trials = []
        for seed in request.seeds:
            _check_cancel(cancelled)
            trial = request.trial_request(regime, seed)
            # Exceptions abort the campaign; no omission or relabeling as timeout.
            trials.append({"seed": seed, "request": asdict(trial),
                           "result": evaluate_temporal_path(topology, trial)})
            completed += 1
            if progress is not None:
                progress(completed, total, regime.regime_id, seed)
        grouped[regime.regime_id] = trials
    return {
        "primary_metric": "P(first swap meets fidelity target within the fixed tick budget)",
        "deadline_s": request.max_attempts*request.attempt_interval_s,
        "total_trials": len(request.regimes)*len(request.seeds),
        "summaries": {name: _summary(trials) for name, trials in grouped.items()},
        "comparisons": _comparisons(grouped, request.baseline_id), "trials_by_regime": grouped,
        "analysis": {
            "protocol": "binary_paired_mcnemar_holm_v1", "alpha": .05,
            "family": "all nonbaseline regimes vs the declared baseline, primary binary outcome only",
            "intervals": "pointwise 95% Wilson; not simultaneous; seeded Monte Carlo uncertainty only",
            "pairing": "same PCG64 seed and fixed two-draw-per-tick schedule in every regime",
            "selection": "exploratory sensitivity comparison; no winner selection or tuning",
            "descriptive_only": ["conditional fidelity", "conditional swap time", "termination time", "pair costs"],
        },
        "limitations": [
            "All trials, including timeouts, enter the primary denominator; missing fidelity stays null",
            "Different conditional subsets may swap; their fidelity means do not estimate a paired improvement",
            "Distinct seeds must be fixed without outcome-dependent selection; PRNG trials approximate independent repetitions",
            "Common random numbers pair regimes, not measured equipment or independent campaigns",
            "Intervals/tests do not include physical-model, calibration or numerical uncertainty",
            "At most 64 seeds; exploratory small-sample evidence, not proof of equivalence or optimality",
            "Mean termination time includes timeouts and is not successful-delivery latency or service rate",
            "Acquisition probabilities, T1/T2 and retention are assumed; no measured WDM or quantum validation",
            "Same isolated single-swap model as memory_single_swap_v1; no competing requests or physical control",
        ],
    }


def _verified(row):
    try:
        record = json.loads(row["record_json"])
        valid = (
            record["schema"] == "orion.temporal_campaign.v1"
            and record["request"]["campaign_id"] == row["campaign_id"]
            and _sha256(record) == row["record_sha256"]
            and _sha256(record["request"]) == row["request_sha256"]
            and _sha256(record["source_topology"]) == record["source_topology_sha256"]
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("temporal campaign record integrity failure") from exc
    if not valid:
        raise ValueError("temporal campaign record integrity failure")
    return {"record": record, "record_sha256": row["record_sha256"]}


def _existing(connection, request, request_hash):
    row = connection.execute("SELECT * FROM temporal_path_campaigns WHERE campaign_id = ?", (request.campaign_id,)).fetchone()
    if row is None:
        return None
    result = _verified(row)
    if row["request_sha256"] != request_hash:
        raise ValueError("campaign_id already used with a different request")
    return {**result, "idempotent_replay": True}


def run_temporal_campaign(store, request, *, progress=None, cancelled=None):
    if type(request) is not TemporalCampaignRequest:
        raise ValueError("validated TemporalCampaignRequest required")
    payload = asdict(request)
    request_hash = _sha256(payload)
    # Freeze once. Leave the workspace editable throughout expensive evaluation.
    with store._lock, closing(store._connect()) as connection, connection:
        connection.execute("BEGIN")
        previous = _existing(connection, request, request_hash)
        if previous:
            return previous
        row = connection.execute("SELECT state_json FROM simulated_topologies WHERE topology_id = ?", (request.topology_id,)).fetchone()
        topology = SimulatedTopology.from_dict(json.loads(row[0])) if row else default_simulated_topology(request.topology_id)
    frozen = topology.to_dict()
    _check_cancel(cancelled)
    # Preserve the no-hook API used by standalone integrations and diagnostics.
    result = (evaluate_temporal_campaign(topology, request) if progress is None and cancelled is None
              else evaluate_temporal_campaign(topology, request, progress=progress, cancelled=cancelled))
    record = {
        "schema": "orion.temporal_campaign.v1", "request": payload,
        "request_sha256": request_hash, "source_topology": frozen,
        "source_topology_sha256": _sha256(frozen), "source_was_persisted": row is not None,
        "result": result, "completed_at_utc": _utc_now(), "evidence_kind": "simulated",
        "physical_action_performed": False, "workspace_mutated": False,
    }
    digest = _sha256(record)
    # Concurrent duplicates can compute, but only one complete receipt is accepted.
    with store._lock, closing(store._connect()) as connection, connection:
        connection.execute("BEGIN IMMEDIATE")
        previous = _existing(connection, request, request_hash)
        if previous:
            return previous
        _check_cancel(cancelled)
        connection.execute("INSERT INTO temporal_path_campaigns (campaign_id, request_sha256, record_sha256, record_json) VALUES (?, ?, ?, ?)",
                           (request.campaign_id, request_hash, digest, _canonical_json(record)))
    return {"record": record, "record_sha256": digest, "idempotent_replay": False}


def read_temporal_campaign(store, campaign_id=None):
    with store._lock, closing(store._connect()) as connection:
        row = connection.execute("SELECT * FROM temporal_path_campaigns ORDER BY sequence DESC LIMIT 1").fetchone() if campaign_id is None else connection.execute(
            "SELECT * FROM temporal_path_campaigns WHERE campaign_id = ?", (campaign_id,)).fetchone()
    return _verified(row) if row else None


@dataclass(frozen=True)
class CampaignHistoryEntry:
    sequence: int
    campaign_id: str
    record_sha256: str


@dataclass(frozen=True)
class CampaignHistoryPage:
    entries: tuple[CampaignHistoryEntry, ...]
    next_before: int | None


def validate_history_prefix(prefix):
    if not isinstance(prefix, str) or len(prefix) > 128 or any(ord(c) < 32 or ord(c) == 127 for c in prefix):
        raise ValueError("campaign prefix must be text of at most 128 characters, without control characters")
    return prefix


def list_temporal_campaigns(store, *, before_sequence=None, limit=20, campaign_prefix=""):
    """Bounded keyset index, newest first. Metadata is NOT receipt verification.

    Never deserialize every campaign just to render a page. The selected receipt
    must be read and verified separately before displaying scientific results.
    """
    if type(limit) is not int or not 1 <= limit <= 50:
        raise ValueError("history page size must be an integer in [1, 50]")
    if before_sequence is not None and (type(before_sequence) is not int or not 1 <= before_sequence <= 2**63-1):
        raise ValueError("invalid history cursor")
    validate_history_prefix(campaign_prefix)
    query = "SELECT sequence, campaign_id, record_sha256 FROM temporal_path_campaigns"
    args, clauses = [], []
    if campaign_prefix:
        # Literal, case-sensitive prefix; SQL wildcard characters are not patterns.
        clauses.append("instr(campaign_id, ?) = 1")
        args.append(campaign_prefix)
    if before_sequence is not None:
        clauses.append("sequence < ?")
        args.append(before_sequence)
    if clauses:
        query += " WHERE " + " AND ".join(clauses)
    query += " ORDER BY sequence DESC LIMIT ?"
    with store._lock, closing(store._connect()) as connection:
        rows = connection.execute(query, (*args, limit+1)).fetchall()
    entries = tuple(CampaignHistoryEntry(row["sequence"], row["campaign_id"], row["record_sha256"]) for row in rows[:limit])
    return CampaignHistoryPage(entries, entries[-1].sequence if len(rows) > limit else None)
