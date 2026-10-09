"""Run the frozen actuator campaign v1 (author's bounded retarder cascade, both operating points)."""
import argparse
import time

from quantum_autopilot.control_plane.polarisation_actuator_campaign import run_campaign


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--qnet-zip", required=True)
    parser.add_argument("--protocol", default="configs/optional_models/polarisation_actuator_v1.json")
    parser.add_argument("--output", required=True)
    parser.add_argument("--workers", type=int, default=1, help="parallel processes for the device blocks")
    args = parser.parse_args()
    start = time.monotonic()
    report = run_campaign(args.qnet_zip, args.protocol, args.output, workers=args.workers,
                          progress=lambda text: print(f"[{time.monotonic() - start:6.0f}s] {text}", flush=True))
    for row in report["comparisons"]:
        slew = "ideal" if row["slew_rate_rad_per_s"] is None else f"{row['slew_rate_rad_per_s']:g} rad/s"
        print(f"{row['operating_point']} {slew} {row['model_id'].split('.')[-1]}: alpha-beta/model "
              f"{row['baseline_to_model_qber_ratio']:.2f}, Holm p {row['holm_p_value']:.2g}, {row['verdict']}", flush=True)
    print(f"Completed {report['runs']} simulated runs; no policy activated, no physical action.", flush=True)


if __name__ == "__main__":
    main()
