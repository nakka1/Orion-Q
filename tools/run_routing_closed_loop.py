"""Run the frozen routing closed-loop campaign v1 (author's repeater environment) into a new directory."""
import argparse
import time

from quantum_autopilot.control_plane.routing_campaign import run_campaign


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--qnet-zip", required=True)
    parser.add_argument("--protocol", default="configs/optional_models/routing_closed_loop_v1.json")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    start = time.monotonic()
    report = run_campaign(args.qnet_zip, args.protocol, args.output,
                          progress=lambda text: print(f"[{time.monotonic() - start:6.0f}s] {text}", flush=True))
    for row in report["primary"]:
        print(f"{row['variant']} {row['metric']}: agent {row['model_mean']:.4f} vs greedy {row['baseline_mean']:.4f}, "
              f"blocks {row['blocks_won']}/{row['blocks_lost']}, Holm p {row['holm_p_value']:.2g}", flush=True)
    print("No policy activated, no physical action.", flush=True)


if __name__ == "__main__":
    main()
