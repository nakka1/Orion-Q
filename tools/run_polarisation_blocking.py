"""Run the frozen key-blocking campaign v1 (author's blocking controller on the ORION-Q loop) into a new directory."""
import argparse
import time

from quantum_autopilot.control_plane.polarisation_blocking import run_campaign


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--qnet-zip", required=True, help="pinned qnet_network.zip")
    parser.add_argument("--protocol", default="configs/optional_models/polarisation_blocking_v1.json")
    parser.add_argument("--output", required=True)
    parser.add_argument("--workers", type=int, default=1, help="processes for the points and the stress gate")
    args = parser.parse_args()
    start = time.monotonic()
    report = run_campaign(args.qnet_zip, args.protocol, args.output, workers=args.workers,
                          progress=lambda text: print(f"[{time.monotonic() - start:6.0f}s] {text}", flush=True))
    for row in report["primary"]["persistence"] + report["primary"]["learned"]:
        print(f"{row['point']} {row['controller_id']}: {row['ratio_to_best_timer']:.3f}x the best timer "
              f"({row['best_timer']}), Holm p {row['holm_p_value']:.2g}", flush=True)
    print("No policy activated, no physical action.", flush=True)


if __name__ == "__main__":
    main()
