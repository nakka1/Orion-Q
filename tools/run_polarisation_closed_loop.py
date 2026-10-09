"""Run the frozen closed-loop polarisation campaign v1 into a new directory."""
import argparse
from quantum_autopilot.control_plane.polarisation_campaign import run_campaign


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--qnet-zip", required=True)
    parser.add_argument("--protocol", default="configs/optional_models/polarisation_closed_loop_v1.json")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    report = run_campaign(args.qnet_zip, args.protocol, args.output, progress=lambda text: print(text, flush=True))
    for row in report["comparisons"]:
        print(f"{row['model_id']} vs {row['baseline']}: ratio {row['baseline_to_model_qber_ratio']:.2f}, "
              f"wins {row['wins']}/{row['n_seeds']}, Holm p {row['holm_p_value']:.2g}", flush=True)
    print(f"Completed {report['runs']} supervised simulated runs; no policy activated, no physical action.", flush=True)


if __name__ == "__main__":
    main()
