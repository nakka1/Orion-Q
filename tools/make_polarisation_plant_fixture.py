"""Record the author's plant outputs so CI can check ORION-Q's transcription without the package.

Needs the author's qnet_autopilot source tree (not part of this repository):

    python tools/make_polarisation_plant_fixture.py --qnet-src PATH/TO/qnet_autopilot_repo

It imports the author's code only here, offline, to write reference numbers;
ORION-Q itself never imports or executes it.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys

import numpy as np

OUTPUT = Path("tests/fixtures/polarisation_plant_reference_v1.npz")
SEEDS = (0, 500, 1000)
POINTS = {"calibrado": dict(thermal_drift_rate_rad_per_s=0.02, vibration_frequency_hz=60.0,
                            vibration_harmonics=(1.0, 0.5, 0.25)),
          "desenvolvimento": {}}
DURATION_S = 2.0
SUBSAMPLE = 7
LOOP_KEYS = ("mean_qber", "median_qber", "p95_qber", "uptime", "mean_key_rate_hz", "mean_residual_mrad",
             "p95_residual_mrad")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--qnet-src", required=True)
    args = parser.parse_args()
    source = Path(args.qnet_src).resolve()
    sys.path[:0] = [str(source), str(source / "scripts")]
    from task3 import make_control, make_model, SAMPLE_INTERVAL_S
    from qnet_autopilot.compensation.monitoring import MonitoringConfig, measure_rotation
    from qnet_autopilot.compensation.closed_loop import bb84_qber
    from qnet_autopilot.physics.link import bb84_secret_key_rate
    from qnet_autopilot.compensation import AlphaBetaFilter, NoCompensation, Persistence, run_closed_loop

    arrays = {}
    for point, overrides in POINTS.items():
        for seed in SEEDS:
            run = make_model(**overrides).simulate(DURATION_S, SAMPLE_INTERVAL_S, rng=seed, track_rotation=True)
            key = f"{point}_{seed}"
            measured = measure_rotation(run.rotation[::2], MonitoringConfig(photons_per_basis=400), rng=seed + 6000)
            arrays[key + "_rotation"] = run.rotation[::SUBSAMPLE]
            arrays[key + "_temperature_c"] = run.temperature_c[::SUBSAMPLE]
            arrays[key + "_vibration_rms"] = run.vibration_rms[::SUBSAMPLE]
            arrays[key + "_jump_times_s"] = run.jump_times_s
            arrays[key + "_measured"] = measured[::SUBSAMPLE]
            arrays[key + "_qber"] = bb84_qber(measured[::SUBSAMPLE])
            arrays[key + "_key_rate_hz"] = bb84_secret_key_rate(1.0e5, arrays[key + "_qber"])
    # The author's own closed loop, for the three baselines, on one short calibrated run.
    run = make_model(**POINTS["calibrado"]).simulate(DURATION_S, SAMPLE_INTERVAL_S, rng=1000, track_rotation=True)
    for name, compensator in (("none", NoCompensation()), ("persistence", Persistence()),
                              ("alpha_beta", AlphaBetaFilter(alpha=0.05, beta=0.005))):
        summary = run_closed_loop(run, compensator, make_control(), rng=7000).summary()
        arrays["loop_" + name] = np.array([summary[key] for key in LOOP_KEYS])
    meta = {"generator": "qnet_autopilot " + hashlib.sha256(
        (source / "qnet_autopilot/physics/fiber.py").read_bytes()).hexdigest()[:16],
        "duration_s": DURATION_S, "subsample": SUBSAMPLE, "seeds": list(SEEDS), "points": list(POINTS),
        "monitoring_seed": "plant seed + 6000, over every second sample",
        "loop": "calibrado seed 1000, monitoring seed 7000, author's run_closed_loop", "loop_keys": list(LOOP_KEYS)}
    arrays["meta_json"] = np.frombuffer(json.dumps(meta, sort_keys=True).encode(), dtype=np.uint8)
    OUTPUT.parent.mkdir(exist_ok=True)
    np.savez_compressed(OUTPUT, **arrays)
    print(f"wrote {OUTPUT} ({OUTPUT.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
