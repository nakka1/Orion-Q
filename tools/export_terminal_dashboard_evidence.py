"""Export a whitelisted dashboard qualification projection, never raw state.

The source is a private campaign made by validate_terminal_dashboard_linux.py.
Only counts, timing/check results, source hashes and explicitly identity-redacted
simulated viewports are exported. Runtime logs, captures, TLS files, databases,
process metadata, and authenticated audit bodies remain private.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path, help="Private validation work/public directory")
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--attempt", action="store_true", help="Preserve a failed attempt as FAIL")
    args = parser.parse_args(argv)
    if os.name != "posix" or os.geteuid() == 0 or args.output.exists():
        raise ValueError("unprivileged Linux and new public destination required")
    source = args.source.resolve(strict=True)
    raw = json.loads((source / "terminal-campaign.json").read_text())
    if raw.get("fixture_only") is not True or raw.get("hardware_qualified") is not False:
        raise ValueError("isolated simulated fixture provenance is required")
    if raw.get("physical_action_performed") is not False or raw.get("artifact_version") != "5.64.2":
        raise ValueError("read-only current-version fixture is required")
    if raw["status"] != "PASS" and not args.attempt:
        raise ValueError("failed evidence must be explicitly identified as an attempt")
    tool = Path(__file__).with_name("validate_terminal_dashboard_linux.py")
    spec = importlib.util.spec_from_file_location("_dashboard_evidence_projection", tool)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    value = module.public_campaign(raw)
    value["source_private_report_sha256"] = hashlib.sha256((source / "terminal-campaign.json").read_bytes()).hexdigest()
    value["projection_tool_sha256"] = hashlib.sha256(tool.read_bytes()).hexdigest()
    if args.attempt:
        value["qualification"] = "historical attempt; not a delivery approval"
    data = (json.dumps(value, indent=2, allow_nan=False) + "\n").encode()
    prohibited = module.MARKERS + (b"terminal-campaign-observer", b"terminal-campaign-core", b"terminal-campaign-returning-operator",
                                    b"/var/tmp/", b"certificate_serial", b"private_key", b"password_ref")
    if len(data) > 3_000_000 or any(marker in data for marker in prohibited):
        raise ValueError("whitelist projection public-output guard failed")
    args.output.mkdir(parents=True)
    (args.output / "terminal-campaign.json").write_bytes(data)
    print(json.dumps({"status": raw["status"], "pty_status": value["pty"]["status"],
                      "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest(),
                      "identity_redacted": True, "private_state_exported": False}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
