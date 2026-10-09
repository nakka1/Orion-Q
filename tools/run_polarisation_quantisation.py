"""Run the frozen quantisation campaign v1 (the compensator in the author's fixed point) into a new directory."""
import argparse
import time

from quantum_autopilot.control_plane.polarisation_quantisation import run_campaign


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--qnet-zip", required=True, help="pinned qnet_network.zip")
    parser.add_argument("--protocol", default="configs/optional_models/polarisation_quantisation_v1.json")
    parser.add_argument("--output", required=True)
    parser.add_argument("--workers", type=int, default=1, help="one process per operating point at most")
    args = parser.parse_args()
    start = time.monotonic()
    report = run_campaign(args.qnet_zip, args.protocol, args.output, workers=args.workers,
                          progress=lambda text: print(f"[{time.monotonic() - start:6.0f}s] {text}", flush=True))
    for row in report["comparisons"]:
        extra = f", Holm p {row['holm_p_value']:.2g}" if "holm_p_value" in row else ""
        print(f"{row['operating_point']:<16} {row['model']:<6} {row['configuration']:<14} fixed/float "
              f"{row['fixed_to_float_ratio']:.4f} {row['ratio_ci95']}{extra}", flush=True)
    print("No policy activated, no physical action.", flush=True)


if __name__ == "__main__":
    main()
