"""Run the frozen synchronization campaign into a new directory only."""
import argparse
from quantum_autopilot.control_plane.sync_campaign import run_campaign


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--qkd-zip", required=True)
    parser.add_argument("--protocol", default="configs/optional_models/sync_campaign_v1.json")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    result = run_campaign(args.qkd_zip, args.protocol, args.output, progress=lambda text: print(text, flush=True))
    for scenario in result["scenarios"]:
        print(scenario["scenario"], "filter:", scenario["descriptive_filter_met"], flush=True)


if __name__ == "__main__":
    main()
