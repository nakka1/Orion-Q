"""Run the frozen allocator closed-loop campaign v1 (author's metro plant) into a new directory."""
import argparse
import time

from quantum_autopilot.control_plane.allocation_campaign import run_campaign


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--qnet-zip", required=True)
    parser.add_argument("--protocol", default="configs/optional_models/allocation_closed_loop_v1.json")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    start = time.monotonic()
    report = run_campaign(args.qnet_zip, args.protocol, args.output,
                          progress=lambda text: print(f"[{time.monotonic() - start:6.0f}s] {text}", flush=True))
    for row in report["primary"]:
        print(f"{row['convention']} {row['model_id']} {row['hypothesis']}: mean difference "
              f"{row['mean_difference']:+.4f}, wins {row['wins']}/{row['n_seeds']}, Holm p {row['holm_p_value']:.2g}",
              flush=True)
    print("No policy activated, no physical action.", flush=True)


if __name__ == "__main__":
    main()
