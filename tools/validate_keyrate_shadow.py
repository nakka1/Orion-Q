"""Original QKD actor parity on explicit artificial shadow windows; no control."""
import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path

from quantum_autopilot.control_plane.contracts import NetworkStateSnapshot
from quantum_autopilot.control_plane.optional_keyrate import (
    MODEL_ID, TELEMETRY_KEY, UNITS, WINDOW_SCHEMA, KeyRateShadowWindow, evaluate_optional_keyrate, export_keyrate_shadow,
)
from quantum_autopilot.control_plane.optional_sync import _hash, _json
from quantum_autopilot.core import Provenance
from quantum_autopilot.model_intake.keyrate_session import demo_keyrate_input, evaluate_keyrate_session

CASES = ("bootstrap", "previous", "zero_rate", "upper_clock")


def demo_snapshot(case="previous", seed=1234):
    if case not in CASES:
        raise ValueError("unknown artificial fixture")
    request = json.loads(demo_keyrate_input())
    origin = datetime(2026, 9, 24, tzinfo=timezone.utc)
    decision = origin if case == "bootstrap" else origin + timedelta(seconds=1)
    settings = request["settings"]
    if case == "upper_clock":
        settings["clock_rate_hz"] = 5e9
    if case == "zero_rate":
        request["previous"]["key_rate_hz"] = 0.
    context = dict(link_id="fixture-link", transmitter_id="fixture-tx", receiver_id="fixture-rx",
                   protocol="decoy_state_bb84", link_span_km=25. if case == "bootstrap" else 80. if case == "zero_rate" else 50.,
                   settings_version=1, settings_timestamp_utc=origin.isoformat(), settings=settings,
                   calibration_id="fixture-calibration", calibration_timestamp_utc=(origin - timedelta(seconds=1)).isoformat(),
                   calibration_valid_until_utc=(origin + timedelta(seconds=60)).isoformat(), calibration=request["calibration"])
    previous = None if case == "bootstrap" else dict(start_timestamp_utc=origin.isoformat(),
                   end_timestamp_utc=decision.isoformat(), settings_sha256=_hash(_json(settings)), observables=request["previous"])
    window = KeyRateShadowWindow.from_dict(dict(
        schema=WINDOW_SCHEMA, evidence="simulated", source_id="hand-authored/keyrate", campaign_id="keyrate-fixture",
        stream_id="keyrate-fixture/stream", node_id="fixture-tx", clock_id="keyrate-clock",
        origin_timestamp_utc=origin.isoformat(), decision_timestamp_utc=decision.isoformat(), seed=seed, units=UNITS,
        observation_mode="calibration_bootstrap" if case == "bootstrap" else "previous_period", context=context, previous=previous))
    return NetworkStateSnapshot(
        request_id=f"keyrate-fixture/{case}/{seed}", node_id="fixture-tx", timestamp_utc=decision.isoformat(), state_version=1,
        predicted_fidelity=.5, prediction_uncertainty=1., telemetry_age_s=0., memory_state="IDLE", integrity_verified=True,
        telemetry={TELEMETRY_KEY: window.to_dict()}, provenance=Provenance("simulated", "hand-authored/keyrate", timestamp=decision.isoformat()),
        metadata=dict(campaign_id="keyrate-fixture", clock_id="keyrate-clock", keyrate_stream_id="keyrate-fixture/stream",
                      keyrate_origin_timestamp_utc=origin.isoformat(), keyrate_context_sha256=window.context_sha256,
                      scope="Hand-authored artificial observables for software parity; no measured rates or applied settings."))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--qkd-zip", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    args.output.mkdir(parents=False, exist_ok=False)
    checks = []
    for case in CASES:
        for seed in (7, 1234):
            state = demo_snapshot(case, seed)
            before = state.to_json()
            result = evaluate_optional_keyrate(state, model_id=MODEL_ID, archive_path=args.qkd_zip)
            if result.status != "evaluated":
                raise ValueError(result.reason + ": " + result.detail)
            evaluation = json.loads(result.evaluation_json)
            offline = json.loads(evaluate_keyrate_session(args.qkd_zip, evaluation["input_json"]).serialized())
            comparison = dict(evaluation)
            del comparison["evaluated_at_utc"]
            del offline["evaluated_at_utc"]
            if (comparison != offline or state.to_json() != before or evaluation["baseline_settings"] !=
                    state.telemetry[TELEMETRY_KEY]["context"]["settings"] or any((result.settings_mutated,
                    result.policy_activated, result.physical_action_performed, result.simulator_action_performed))):
                raise ValueError("offline parity or read-only invariant failed")
            name = f"{case}-{seed}"
            export_keyrate_shadow(result, args.output / f"{name}.json")
            with (args.output / f"{name}-input.json").open("x", encoding="utf-8") as stream:
                stream.write(before)
            checks.append(dict(case=case, seed=seed, observation_source=result.observation_source,
                               sampled_index=evaluation["recommendation"]["sampled_index"],
                               greedy_index=evaluation["recommendation"]["greedy_index"],
                               archive_sha256=evaluation["archive_sha256"], context_sha256=result.context_sha256,
                               offline_parity=True, snapshot_unchanged=True,
                               report_sha256=hashlib.sha256(result.serialized().encode()).hexdigest()))
    with (args.output / "report.json").open("x", encoding="utf-8") as stream:
        json.dump(dict(schema="orion.keyrate-shadow-diagnostic.v1", passed=True, checks=checks,
                       physical_action_performed=False, settings_mutated=False, policy_activated=False,
                       scope="Eight software parity fixtures, not key-rate outcomes or operational gain."), stream, indent=2)
    print("8 QKD actor shadow checks passed; bootstrap/previous/zero-rate/clock-bound, no settings applied.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
