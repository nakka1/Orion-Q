"""Run frozen SO(3) stress protocol into a new directory, preserving all variants."""
import argparse
from quantum_autopilot.control_plane.compensation_campaign import run_campaign


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--qnet-zip", required=True)
    parser.add_argument("--protocol", default="configs/optional_models/compensation_campaign_v1.json")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    report = run_campaign(args.qnet_zip, args.protocol, args.output, progress=lambda text: print(text, flush=True))
    print(f"Completed {len(report['runs'])} runs; results are descriptive, not activation authority.", flush=True)


if __name__ == "__main__":
    main()
