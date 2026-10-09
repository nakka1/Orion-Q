"""Golden-output replay, not an independent benchmark of scientific advantage."""
from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
import platform

import numpy as np

from .artifacts import QKD_ROOT, ROUTING_ROOT, ReviewedArchive
from .inference import RoutingReplayModel, SyncReplayModel
from .compensation_evaluation import evaluate_compensation
from .keyrate_evaluation import evaluate_keyrate
from .allocation_evaluation import evaluate_allocation

ROUTING_ATOL = 1e-12
SYNC_ATOL_S = 1e-15  # Exported CSV rounded to ten significant digits.


def evaluate_routing(archive: ReviewedArchive, variant: str) -> dict:
    model = RoutingReplayModel(archive, variant)
    base = ROUTING_ROOT + variant + "/examples/"
    examples = archive.json(base + "examples.json")
    episodes = archive.json(base + "episodes.json")
    worst = 0.0
    greedy_mismatches = sampled_mismatches = decisions = 0
    rng = np.random.default_rng(0)
    for example in examples["examples"]:
        inputs, expected = example["input"], example["expected"]
        result = model.evaluate(**inputs, domain="simulated_offline", rng=rng)
        worst = max(worst, float(np.max(np.abs(np.array(result.logits) - expected["logits"]))),
                    float(np.max(np.abs(np.array(result.probabilities) - expected["probabilities"]))),
                    abs(result.critic_return - expected["value"]))
        greedy_mismatches += result.greedy_index != expected["greedy_index"]
    # One continuous generator across episodes, matching the originating export.
    rng = np.random.default_rng(episodes["policy_rng_seed"])
    for episode in episodes["episodes"]:
        for step in episode["steps"]:
            result = model.evaluate(step["state_features"], step["action_features"], step["actions"],
                                    domain="simulated_offline", rng=rng)
            worst = max(worst, float(np.max(np.abs(np.array(result.probabilities) - step["probabilities"]))))
            sampled_mismatches += result.sampled_index != step["chosen_index"]
            decisions += 1
    return {
        "model_id": f"qnet.routing.{variant}", "examples": len(examples["examples"]),
        "episodes": len(episodes["episodes"]), "episode_decisions": decisions,
        "temperature": episodes["temperature"], "policy_rng_seed": episodes["policy_rng_seed"],
        "max_absolute_error": worst, "absolute_tolerance": ROUTING_ATOL,
        "greedy_mismatches": greedy_mismatches, "sampled_mismatches": sampled_mismatches,
        "passed": bool(decisions > 0 and len(examples["examples"]) > 0 and worst < ROUTING_ATOL
                       and greedy_mismatches == 0 and sampled_mismatches == 0),
        "scope": "export parity only; no regenerated environment, new outcomes or baseline comparison",
    }


def evaluate_sync(archive: ReviewedArchive) -> dict:
    model = SyncReplayModel(archive)
    base = QKD_ROOT + "exemplos/"
    rows = list(csv.DictReader(io.StringIO(archive.read(base + "sincronizador_entrada.csv").decode())))
    reference = list(csv.DictReader(io.StringIO(archive.read(base + "sincronizador_saida.csv").decode())))
    timestamps = np.array([float(r["time_s"]) for r in rows])
    offsets = np.array([float(r["offset_medido_s"]) for r in rows])
    temperatures = np.array([float(r["temperatura_c"]) for r in rows])
    if len(reference) != len(rows) - 7:
        raise ValueError("reference does not cover every complete eight-sample window")
    if not np.allclose(np.diff(timestamps), 1.0, rtol=0, atol=1e-6):
        raise ValueError("invalid input replay cadence")
    predictions, expected, truth, holds = [], [], [], []
    for i, row in enumerate(reference, start=7):
        if float(row["time_s"]) != timestamps[i]:
            raise ValueError("input/reference timestamp mismatch")
        result = model.predict(timestamps[i-7:i+1], offsets[i-7:i+1], temperatures[i-7:i+1],
                               domain="simulated_offline")
        predictions.append(result.predicted_offset_s)
        holds.append(result.persistence_offset_s)
        expected.append(float(row["offset_previsto_em_t+2s_s"]))
        truth.append(float(row["offset_verdadeiro_em_t+2s_s"]))
    worst = float(np.max(np.abs(np.array(predictions) - expected)))
    model_rmse = float(np.sqrt(np.mean((np.array(predictions) - truth) ** 2)) * 1e12)
    hold_rmse = float(np.sqrt(np.mean((np.array(holds) - truth) ** 2)) * 1e12)
    return {
        "model_id": "qkd.sync.ridge.1s.h2s", "predictions": len(predictions),
        "warmup_samples_not_predicted": 7, "max_absolute_error_s": worst,
        "absolute_tolerance_s": SYNC_ATOL_S, "passed": bool(worst <= SYNC_ATOL_S),
        "example_model_rmse_ps": model_rmse, "example_persistence_rmse_ps": hold_rmse,
        "scope": "one supplied simulated trajectory; RMSE is descriptive, not a significance test",
    }


def build_report(qkd_path: str | Path, qnet_path: str | Path) -> dict:
    # Validate both archive identities before invoking any numerical model.
    qkd = ReviewedArchive(qkd_path, "qkd")
    qnet = ReviewedArchive(qnet_path, "qnet")
    checks = [evaluate_sync(qkd)] + [evaluate_routing(qnet, v) for v in ("nominal", "prazo_longo")]
    checks.append(evaluate_keyrate(qkd))
    checks.extend(evaluate_allocation(qnet))
    for point in ("calibrado", "desenvolvimento"):
        checks.extend(evaluate_compensation(qnet, point))
    return {
        "schema": "orion-q.author-model-replay.v4",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "mode": "simulated_offline", "physical_action_performed": False,
        "runtime_policy_activated": False, "experimental_validation": False,
        "passed": all(check["passed"] for check in checks),
        "environment": {"python": platform.python_version(), "numpy": np.__version__},
        "adapter_source_sha256": {
            name: hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest()
            for name in ("artifacts.py", "inference.py", "evaluation.py", "compensation.py",
                         "compensation_evaluation.py", "catalog.py", "keyrate.py", "keyrate_evaluation.py",
                         "allocation.py", "allocation_evaluation.py")
        },
        "archives": {
            "qkd": {"sha256": qkd.sha256, "inventory": qkd.inventory()},
            "qnet": {"sha256": qnet.sha256, "inventory": qnet.inventory()},
        },
        "checks": checks,
        "pending": {
            "qkd_detector": "admitted offline through the reviewed safe export (pickle hashed, never loaded); "
                            "see control_plane/detector_campaign.py",
            "qkd_keyrate_agent": "offline parity here; closed loop on the author's link in control_plane/qkd_campaign.py; "
                                 "runtime bridge pending",
            "qnet_compensator": "offline parity here; closed loop (ideal and the author's bounded actuator) in "
                                "control_plane/polarisation_*campaign.py; runtime bridge pending",
            "qnet_allocator": "offline parity here; closed loop with finite-key accounting in "
                              "control_plane/allocation_campaign.py; runtime bridge pending",
            "runtime": "physical feature bridge, legal-action validation and Q05 activation lifecycle pending",
            "distribution": "no redistribution license identified in the two ZIP inventories",
        },
        "limitations": [
            "Golden outputs are supplied by the author, not independently acquired experimental truth.",
            "No new training, closed-loop benchmark, paper-wide replication or WDM-to-Bell validation.",
            "Routing consumes exported simulator features; it does not convert ORION telemetry into them.",
            "Archive pinning establishes byte identity only; it does not establish scientific validity.",
            "All author models are optional and OFF by default; no existing baseline is replaced.",
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--qkd-zip", type=Path, required=True)
    parser.add_argument("--qnet-zip", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True, help="new JSON report, never overwritten")
    args = parser.parse_args(argv)
    if args.output.exists():
        parser.error("output already exists; choose a new report path")
    try:
        report = build_report(args.qkd_zip, args.qnet_zip)
        encoded = json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("x", encoding="utf-8") as handle:
            handle.write(encoded)
    except (OSError, ValueError, KeyError) as error:
        parser.exit(2, f"Replay rejected: {error}\n")
    print(json.dumps({"passed": report["passed"], "report": str(args.output), "checks": report["checks"]}))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
