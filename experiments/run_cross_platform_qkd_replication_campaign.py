"""Run cross-platform replication using prior IrelandQCI and Bristol artifacts."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from quantum_twin.evaluation.cross_platform_qkd_replication import (
    CrossPlatformQKDReplicationConfig,
    run_cross_platform_qkd_replication_campaign,
    write_cross_platform_qkd_replication_artifacts,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ireland-campaign-dir", type=Path, required=True)
    parser.add_argument("--bristol-campaign-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--familywise-alpha", type=float, default=0.05)
    parser.add_argument("--skip-input-manifest-verification", action="store_true")
    args = parser.parse_args()

    result = run_cross_platform_qkd_replication_campaign(
        args.ireland_campaign_dir,
        args.bristol_campaign_dir,
        config=CrossPlatformQKDReplicationConfig(
            familywise_alpha=args.familywise_alpha,
            verify_input_manifests=not args.skip_input_manifest_verification,
        ),
    )
    manifest = write_cross_platform_qkd_replication_artifacts(
        result, args.output_dir
    )
    print(json.dumps({
        "output_dir": str(args.output_dir.resolve()),
        "replication_results": json.loads(
            result.replication_results.to_json(orient="records")
        ),
        "artifacts": manifest["artifacts"],
    }, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
