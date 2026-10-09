"""Explicit eight-case integration diagnostic; artificial rotations, no actuation."""
import argparse
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

from quantum_autopilot.control_plane.contracts import NetworkStateSnapshot
from quantum_autopilot.control_plane.optional_compensation import (
    CONVENTION, MODEL_IDS, REGIMES, TELEMETRY_KEY, CompensationShadowWindow, PolarisationSample,
    evaluate_optional_compensation, export_compensation_shadow,
)
from quantum_autopilot.core import Provenance
from quantum_autopilot.model_intake.compensation_session import demo_compensation_input, evaluate_compensation_session


def demo_snapshot(model_id, count):
    if model_id not in MODEL_IDS or type(count) is not int or not 1 <= count <= 16:
        raise ValueError("supported checkpoint and 1..16 samples required")
    point = model_id.split(".")[2]
    origin = datetime(2026, 9, 23, tzinfo=timezone.utc)
    demo = json.loads(demo_compensation_input(model_id))
    samples = tuple(PolarisationSample((origin + timedelta(seconds=row["timestamp_s"])).isoformat(),
                                       row["rotation"], **row["covariates"]) for row in demo["samples"][:count])
    window = CompensationShadowWindow("hand-authored/polarisation", "demo-polarisation", "demo-polarisation/stream",
                                      "demo-node", "virtual-clock", "author-export-frame", point,
                                      *REGIMES[point], samples)
    return NetworkStateSnapshot(
        request_id=f"demo/{model_id}/{count}", node_id=window.node_id, timestamp_utc=samples[-1].timestamp_utc,
        state_version=count, predicted_fidelity=.5, prediction_uncertainty=1., telemetry_age_s=0.,
        memory_state="IDLE", integrity_verified=True, telemetry={TELEMETRY_KEY: window.to_dict()},
        provenance=Provenance("simulated", window.source_id, timestamp=samples[-1].timestamp_utc),
        metadata={"campaign_id": window.campaign_id, "clock_id": window.clock_id,
                  "rotation_frame_id": window.rotation_frame_id, "rotation_convention": CONVENTION,
                  "compensation_operating_point": point,
                  "scope": "hand-authored fixture; not measured or certified in-domain; fidelity fields unused"})


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--qnet-zip", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    args.output.mkdir(parents=False, exist_ok=False)
    checks = []
    for model_id in MODEL_IDS:
        for count in (1, 16):
            state = demo_snapshot(model_id, count)
            result = evaluate_optional_compensation(state, model_id=model_id, archive_path=args.qnet_zip)
            if result.status != "evaluated":
                raise ValueError(result.reason + ": " + result.detail)
            evaluation = json.loads(result.evaluation_json)
            offline = evaluate_compensation_session(model_id, args.qnet_zip, evaluation["input_json"])
            expected = "model" if count == 16 else "warmup_persistence"
            if (result.final_output_status != expected or evaluation["reference_rotation"] is not None
                    or evaluation["steps"] != json.loads(offline.serialized())["steps"]):
                raise ValueError("bridge/offline mismatch")
            export_compensation_shadow(result, args.output / f"{model_id}-{count}.json")
            checks.append({"model_id": model_id, "samples": count, "status": result.final_output_status,
                           "archive_sha256": evaluation["archive_sha256"], "offline_parity": True})
    report = {"schema": "orion.compensation-shadow-diagnostic.v1", "passed": True, "checks": checks,
              "scope": "software wiring parity on artificial windows, not performance, physics or closed-loop gain",
              "physical_action_performed": False, "policy_activated": False}
    with (args.output / "report.json").open("x", encoding="utf-8") as stream:
        json.dump(report, stream, indent=2, allow_nan=False)
    print("8 checks passed; warm-up and full windows for all four checkpoints; no actuation.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
