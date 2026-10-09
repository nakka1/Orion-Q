"""Run the frozen routing-in-SeQUeNCe campaign v1 into a new directory (needs SeQUeNCe: SEQUENCE_SRC or pip)."""
import argparse
import time

from quantum_autopilot.control_plane.routing_sequence import run_campaign


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--qnet-zip", required=True, help="pinned qnet_network.zip (the exported agents)")
    parser.add_argument("--author-zip", required=True,
                        help="the author's repository archive, Qnet-Network ML.zip (phase-2 weights for the gate)")
    parser.add_argument("--protocol", default="configs/optional_models/routing_sequence_v1.json")
    parser.add_argument("--output", required=True)
    parser.add_argument("--workers", type=int, default=1, help="one process per variant at most")
    args = parser.parse_args()
    start = time.monotonic()
    report = run_campaign(args.qnet_zip, args.author_zip, args.protocol, args.output, workers=args.workers,
                          progress=lambda text: print(f"[{time.monotonic() - start:6.0f}s] {text}", flush=True))
    for row in report["primary"]:
        print(f"{row['variant']}: agent {row['model_mean']:.4f} vs greedy {row['baseline_mean']:.4f} in SeQUeNCe, "
              f"blocks {row['blocks_won']}/{row['blocks_lost']}, Holm p {row['holm_p_value']:.2g}", flush=True)
    print("No policy activated, no physical action.", flush=True)


if __name__ == "__main__":
    main()
