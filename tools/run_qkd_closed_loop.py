"""Run the frozen QKD closed-loop campaign v1 (key-rate actor and synchroniser) into a new directory."""
import argparse
import time

from quantum_autopilot.control_plane.qkd_campaign import run_campaign


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--qkd-zip", required=True, help="pinned modelo_qkd_ml.zip")
    parser.add_argument("--protocol", default="configs/optional_models/qkd_closed_loop_v1.json")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    start = time.monotonic()
    report = run_campaign(args.qkd_zip, args.protocol, args.output,
                          progress=lambda text: print(f"[{time.monotonic() - start:6.0f}s] {text}", flush=True))
    for row in report["keyrate"]["comparisons"]:
        print(f"key rate {row['length_km']:g} km: actor/static {row['model_to_baseline_ratio']:.3f}, "
              f"wins {row['wins']}/{row['n_seeds']}, Holm p {row['holm_p_value']:.2g}", flush=True)
    for row in report["sync"]["comparisons"]:
        print(f"sync {row['gate_width_ps']:g} ps, dark {row['dark_count_rate_hz']:g} Hz: ridge/alpha-beta "
              f"{row['model_to_baseline_ratio']:.4f}, wins {row['wins']}/{row['n_seeds']}, Holm p {row['holm_p_value']:.2g}",
              flush=True)
    print("No policy activated, no physical action.", flush=True)


if __name__ == "__main__":
    main()
