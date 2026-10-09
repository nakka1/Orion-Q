"""Run the loopback-only API consumed by the local assurance dashboard."""

from __future__ import annotations

import argparse
from pathlib import Path

from quantum_autopilot.qkd.dashboard_api import DEFAULT_HOST, DEFAULT_PORT, create_server


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--host", default=DEFAULT_HOST, choices=("127.0.0.1", "localhost"))
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument(
        "--control-journal",
        type=Path,
        default=None,
        help="optional SQLite path for sandbox control-cycle records",
    )
    parser.add_argument(
        "--optical-wdm-csv",
        type=Path,
        default=None,
        help="optional Sant'Anna CSV exposed only through the measured optical-health lane",
    )
    parser.add_argument(
        "--optical-evidence-report",
        type=Path,
        default=None,
        help="optional hash-bound WDM forecasting report associated with the optical CSV",
    )
    parser.add_argument(
        "--expected-optical-dataset-sha256",
        default=None,
        help="optional expected SHA-256 for the configured optical CSV",
    )
    parser.add_argument(
        "--wdm-model-bundle",
        type=Path,
        default=None,
        help="optional safe .npz model bundle exposed for bounded shadow replay",
    )
    parser.add_argument(
        "--expected-wdm-bundle-sha256",
        default=None,
        help="optional external SHA-256 pin for the configured WDM model bundle",
    )
    parser.add_argument(
        "--wdm-replay-max-cycles",
        type=int,
        default=100,
        help="server-side maximum number of model-replay rows per request",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    server = create_server(
        repository_root=args.repository_root,
        host=args.host,
        port=args.port,
        control_journal_path=args.control_journal,
        optical_wdm_csv=args.optical_wdm_csv,
        optical_evidence_report_path=args.optical_evidence_report,
        expected_optical_dataset_sha256=args.expected_optical_dataset_sha256,
        wdm_model_bundle_path=args.wdm_model_bundle,
        expected_wdm_bundle_sha256=args.expected_wdm_bundle_sha256,
        wdm_replay_max_cycles=args.wdm_replay_max_cycles,
    )
    print(
        f"WDM·QKD Assurance API: http://{args.host}:{server.server_port}/api/v1/health",
        flush=True,
    )
    print(
        "Physical automation unavailable; sandbox modes: simulation/shadow/assurance.",
        flush=True,
    )
    print(
        "Three evidence lanes, safe WDM bundle replay and simulated topology available via API v1.3.",
        flush=True,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
