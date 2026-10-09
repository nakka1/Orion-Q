"""Run the frozen causal-monitor campaign v1 (the detector with real-time features) into a new directory."""
import argparse
import time

from quantum_autopilot.control_plane.detector_causal import run_campaign


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--qkd-zip", required=True, help="pinned modelo_qkd_ml.zip")
    parser.add_argument("--protocol", default="configs/optional_models/detector_causal_v1.json")
    parser.add_argument("--output", required=True)
    parser.add_argument("--workers", type=int, default=1, help="one process per dataset at most")
    args = parser.parse_args()
    start = time.monotonic()
    run_campaign(args.qkd_zip, args.protocol, args.output, workers=args.workers,
                 progress=lambda text: print(f"[{time.monotonic() - start:6.0f}s] {text}", flush=True))
    print("No policy activated, no physical action.", flush=True)


if __name__ == "__main__":
    main()
