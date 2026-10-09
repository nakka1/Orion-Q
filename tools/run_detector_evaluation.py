"""Run the frozen detector campaign v1 (safe export of the author's QKD attack detector) into a new directory."""
import argparse
import time

from quantum_autopilot.control_plane.detector_campaign import DEPLOYED, run_campaign


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--qkd-zip", required=True, help="pinned modelo_qkd_ml.zip")
    parser.add_argument("--protocol", default="configs/optional_models/detector_evaluation_v1.json")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    start = time.monotonic()
    report = run_campaign(args.qkd_zip, args.protocol, args.output,
                          progress=lambda text: print(f"[{time.monotonic() - start:6.0f}s] {text}", flush=True))
    for row in report["evaluation"]:
        deployed = row["detectors"][DEPLOYED]
        print(f"seed {row['seed']}: macro F1 {deployed['macro_f1']:.3f} {deployed['ci95']['macro_f1']}, "
              f"{deployed['false_alarms_per_hour']:.1f} false alarms/h, flagged {row['primary']['deployed_mean_flagged']:.3f} "
              f"vs {row['primary']['threshold_mean_flagged']:.3f}, Holm p {row['primary']['holm_p_value']:.2g}", flush=True)
    print("No policy activated, no physical action.", flush=True)


if __name__ == "__main__":
    main()
