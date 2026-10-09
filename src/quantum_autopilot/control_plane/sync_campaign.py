"""Fixed, descriptive multi-seed synchronization stress protocol; never a policy."""
from collections import deque
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
from pathlib import Path
import random

from .contracts import NetworkStateSnapshot
from .optional_sync import MODEL_ID, SyncSample, SyncShadowWindow, TELEMETRY_KEY, evaluate_optional_sync
from .sync_temporal import TemporalSyncEvaluation, export_temporal_report
from quantum_autopilot.core import Provenance
from quantum_autopilot.model_intake.sync_session import _constant, _unique
from quantum_autopilot.model_intake.artifacts import PINS

SCENARIOS = ("smooth", "independent_noise", "drift_reversal", "step_shift", "fast_oscillation", "packet_loss")
PROTOCOL = {
    "schema": "orion.sync-campaign-protocol.v1", "evidence": "simulated", "frames": 128,
    "seeds": list(range(100, 110)), "scenarios": list(SCENARIOS), "model_id": MODEL_ID,
    "baselines": ["persistence", "past_window_mean"], "min_relative_mae_reduction": .05,
    "min_seed_wins": 8, "min_target_coverage": .9,
    "interpretation": "descriptive engineering checks, not significance, experimental validation or activation authority",
}


def sample(scenario, seed, tick):
    if scenario not in SCENARIOS or type(seed) is not int or not 0 <= seed < 2**32:
        raise ValueError("unknown scenario or invalid seed")
    if type(tick) is not int or not 0 <= tick < 128:
        raise ValueError("tick must be in 0..127")
    if scenario == "packet_loss" and tick in (48, 49, 50, 51, 88):
        return None
    phase = random.Random(f"sync-campaign-phase:{seed}").uniform(0., 2 * math.pi)
    noise = random.Random(f"sync-campaign-noise:{seed}:{tick}").uniform(-.02, .02)
    signal = {
        "smooth": .2 * math.sin(tick / 13 + phase),
        "packet_loss": .2 * math.sin(tick / 13 + phase),
        "independent_noise": 0.,
        "drift_reversal": .005 * min(tick, 128 - tick),
        "step_shift": .3 if tick >= 64 else 0.,
        "fast_oscillation": .2 * math.sin(tick / 2 + phase),
    }[scenario]
    offset = (signal + noise * (10 if scenario == "independent_noise" else 1)) * 1e-9
    stamp = (datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(seconds=tick)).isoformat()
    return SyncSample(stamp, offset, 20 + .3 * math.sin(tick / 40 + phase))


def run_case(archive, scenario, seed):
    if scenario not in SCENARIOS or type(seed) is not int or not 0 <= seed < 2**32:
        raise ValueError("invalid scenario or seed")
    tracker = TemporalSyncEvaluation(max_predictions=121)
    history = deque(maxlen=8)
    campaign = f"sync-campaign-v1/{scenario}/{seed}"
    acquired = 0
    for tick in range(128):
        current = sample(scenario, seed, tick)
        if current is None:
            history.clear()
            continue
        acquired += 1
        history.append(current)
        if len(history) < 8:
            continue
        window = SyncShadowWindow("artificial-sync-campaign-v1", campaign, campaign + "/timing",
                                  "demo-node", campaign, tuple(history))
        state = NetworkStateSnapshot(
            request_id=f"{campaign}/{tick}", node_id=window.node_id, timestamp_utc=current.timestamp_utc,
            state_version=tick, predicted_fidelity=.5, prediction_uncertainty=1., telemetry_age_s=0.,
            memory_state="IDLE", integrity_verified=True, telemetry={TELEMETRY_KEY: window.to_dict()},
            provenance=Provenance("simulated", window.source_id, timestamp=current.timestamp_utc),
            metadata={"campaign_id": campaign, "clock_id": campaign,
                      "scope": "artificial timing; fidelity fields are unused envelope placeholders"})
        tracker.observe(state)
        prediction = evaluate_optional_sync(state, model_id=MODEL_ID, archive_path=archive)
        if prediction.status != "evaluated":
            raise ValueError(prediction.reason + ": " + prediction.detail)
        tracker.register(prediction)
    report = tracker.report()
    mean_errors = []
    for row in report["records"]:
        offsets = json.loads(row["forecast"]["evaluation"]["input_json"])["offsets_s"]
        baseline = math.fsum(offsets) / len(offsets)
        row["past_window_mean_offset_s"] = baseline
        if row["status"] == "paired":
            error = abs(baseline - row["reference_offset_s"])
            row["past_window_mean_absolute_error_s"] = error
            mean_errors.append(error)
    report.update({"scenario": scenario, "seed": seed, "emitted_tick_count": 128,
                   "acquired_samples": acquired, "eligible_target_opportunities": 119,
                   "target_coverage": report["paired"] / 119,
                   "past_window_mean_mae_s": math.fsum(mean_errors) / len(mean_errors),
                   "past_window_mean_rmse_s": math.hypot(*mean_errors) / math.sqrt(len(mean_errors))})
    return report


def aggregate(runs):
    expected = {(scenario, seed) for scenario in SCENARIOS for seed in PROTOCOL["seeds"]}
    actual = [(row["scenario"], row["seed"]) for row in runs]
    if len(actual) != len(set(actual)) or set(actual) != expected:
        raise ValueError("all prescribed scenarios/seeds required exactly once")
    summaries = []
    for scenario in SCENARIOS:
        rows = sorted((row for row in runs if row["scenario"] == scenario), key=lambda row: row["seed"])
        model = math.fsum(row["model_mae_s"] for row in rows) / len(rows)
        comparisons = {}
        for baseline in PROTOCOL["baselines"]:
            values = [row[baseline + "_mae_s"] for row in rows]
            average = math.fsum(values) / len(values)
            wins = sum(row["model_mae_s"] < value for row, value in zip(rows, values))
            ties = sum(row["model_mae_s"] == value for row, value in zip(rows, values))
            reduction = 1 - model / average if average > 0 else None
            comparisons[baseline] = {
                "mean_seed_mae_s": average, "model_wins": wins, "ties": ties, "model_losses": len(rows) - wins - ties,
                "relative_mae_reduction": reduction,
                "descriptive_filter_met": reduction is not None and reduction >= PROTOCOL["min_relative_mae_reduction"]
                and wins >= PROTOCOL["min_seed_wins"],
            }
        coverage = min(row["target_coverage"] for row in rows)
        summaries.append({"scenario": scenario, "mean_seed_model_mae_s": model,
                          "min_seed_model_mae_s": min(row["model_mae_s"] for row in rows),
                          "max_seed_model_mae_s": max(row["model_mae_s"] for row in rows),
                          "min_target_coverage": coverage, "comparisons": comparisons,
                          "descriptive_filter_met": coverage >= PROTOCOL["min_target_coverage"]
                          and all(row["descriptive_filter_met"] for row in comparisons.values())})
    return summaries


def run_campaign(archive, protocol_path, output, *, progress=None):
    with Path(protocol_path).open("rb") as stream:
        raw = stream.read(16385)
    if len(raw) > 16384:
        raise ValueError("protocol exceeds 16 KiB")
    parsed = json.loads(raw, object_pairs_hook=_unique, parse_constant=_constant)
    if json.dumps(parsed, sort_keys=True) != json.dumps(PROTOCOL, sort_keys=True):
        raise ValueError("protocol differs from frozen v1; create a separately reviewed version")
    output = Path(output)
    output.mkdir(parents=False, exist_ok=False)
    root = Path(__file__).resolve().parents[3]
    paths = ["src/quantum_autopilot/control_plane/sync_campaign.py",
             "src/quantum_autopilot/control_plane/optional_sync.py",
             "src/quantum_autopilot/control_plane/sync_temporal.py",
             "src/quantum_autopilot/model_intake/inference.py",
             "src/quantum_autopilot/model_intake/artifacts.py"]
    manifest = {"protocol": PROTOCOL, "protocol_file_sha256": hashlib.sha256(raw).hexdigest(),
                "expected_archive_sha256": PINS["qkd"],
                "source_sha256": {path: hashlib.sha256((root / path).read_bytes()).hexdigest() for path in paths},
                "scope": "artificial stress evaluation; not independent experimental validation"}
    export_temporal_report(json.dumps(manifest, indent=2), output / "frozen-protocol.json")
    runs, artifacts = [], []
    for scenario in SCENARIOS:
        for seed in PROTOCOL["seeds"]:
            report = run_case(archive, scenario, seed)
            text = json.dumps(report, indent=2, allow_nan=False)
            name = f"{scenario}-{seed}.json"
            export_temporal_report(text, output / name)
            artifacts.append({"file": name, "sha256": hashlib.sha256(text.encode()).hexdigest()})
            runs.append({key: value for key, value in report.items() if key != "records"})
        if progress is not None:
            progress(f"Completed {scenario}: 10 seeds; no tuning or activation.")
    identities = {row["archive_sha256"] for row in runs}
    if identities != {manifest["expected_archive_sha256"]}:
        raise ValueError("mixed model artifacts in campaign")
    if any(hashlib.sha256((root / path).read_bytes()).hexdigest() != digest
           for path, digest in manifest["source_sha256"].items()):
        raise ValueError("source files changed during campaign; refusing a completion report")
    summary = {"schema": "orion.sync-campaign-results.v1", "status": "complete", "manifest": manifest,
               "archive_sha256": identities.pop(), "scenarios": aggregate(runs), "runs": runs,
               "artifacts": artifacts, "policy_activated": False, "physical_action_performed": False,
               "limitations": ["Descriptive engineering filters only; no statistical significance claim.",
                               "Artificial offset/temperature, not WDM or quantum fidelity.",
                               "Fixed sequential replay; not UI latency or closed-loop utility."]}
    export_temporal_report(json.dumps(summary, indent=2, allow_nan=False), output / "summary.json")
    return summary
