"""Run the recorded eleven-node campaign through real Linux broker processes.

Authentication certificates/private state remain in native temporary storage.
Only public measurements, receipts and manifests are exported. No connected
instrument, FPGA, DMA driver or real-time deadline is qualified by this run.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import tempfile
import time

from quantum_autopilot.service.pl_gateway_cli import software_image_digest


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", required=True, type=Path)
    parser.add_argument("--native", required=True, type=Path)
    parser.add_argument("--evidence", required=True, type=Path)
    args = parser.parse_args(argv)
    if platform.system() != "Linux" or os.geteuid() == 0:
        parser.error("actual non-root Linux execution is required")
    if args.evidence.exists():
        parser.error("evidence directory must be new")
    args.evidence.mkdir(parents=True)
    report = {"schema": "orion.pspl-linux-campaign.v1", "status": "running",
        "started_at": datetime.now(timezone.utc).isoformat(), "linux_executed": True,
        "architecture": platform.machine(), "kernel": platform.release(), "uid": os.geteuid(),
        "python": platform.python_version(), "image": software_image_digest(), "campaigns": [],
        "physical_action_performed": False, "hardware_validated": False, "arm_executed": False,
        "petalinux_image_built": False, "physical_dma_executed": False, "hard_realtime_qualified": False}
    try:
        with tempfile.TemporaryDirectory(prefix="oq-pspl-", dir="/var/tmp") as temporary:
            private = Path(temporary)
            private.chmod(0o700)
            for index, mode in enumerate(("cpu-external", "emulated-pl-external")):
                worker_dir = private / str(index)
                worker_dir.mkdir(mode=0o700)
                config = {"schema": "orion.pl-gateway.v1", "socket_path": str(worker_dir / "worker.sock"),
                    "state_directory": str(worker_dir / "state"),
                    "backend": {"backend_id": f"campaign-{index}", "mode": mode, "generation": 1,
                                "image_sha256": report["image"]["image_sha256"], "max_events": 65_536},
                    "allowed_uid": os.geteuid(), "request_timeout_s": 5}
                config_file = worker_dir / "gateway.json"
                Path(config["state_directory"]).mkdir(mode=0o700)
                config_file.write_text(json.dumps(config), encoding="utf-8")
                config_file.chmod(0o600)
                command = [sys.executable, "-I", "-m", "quantum_autopilot.service.pl_gateway_cli",
                           "--config", str(config_file), "serve"]
                process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                started = time.monotonic()
                try:
                    until = started + 10
                    while not Path(config["socket_path"]).exists() and process.poll() is None and time.monotonic() < until:
                        time.sleep(.02)
                    if not Path(config["socket_path"]).is_socket():
                        raise RuntimeError("gateway did not start")
                    load_time = time.monotonic() - started
                    output = args.evidence / mode
                    result = subprocess.run([sys.executable, args.repository / "tools/run_mixed_controller_campaign.py",
                        "--repository", args.repository, "--native", args.native, "--output", output,
                        "--pl-gateway-config", config_file], capture_output=True, text=True, timeout=180)
                    (args.evidence / f"{mode}.log").write_text(result.stdout + result.stderr, encoding="utf-8")
                    if result.returncode:
                        raise RuntimeError("mixed campaign failed; inspect public log")
                    campaign = json.loads((output / "report.json").read_text(encoding="utf-8"))
                    if campaign["status"] != "PASS":
                        raise RuntimeError("mixed campaign criteria failed")
                    comparisons = []
                    for node, receipt in campaign["processing"].items():
                        local = json.loads((output / "node-processing" / f"{node}-cpu-reference-receipt.json").read_text())
                        comparisons.append({"node": node, "events": receipt["metrics"]["observations"],
                            "coincidence_pairs": receipt["metrics"]["coincidence_pairs"],
                            "local_pipeline_s": local["wall_elapsed_s"],
                            "external_pipeline_with_cpu_reference_s": receipt["wall_elapsed_s"],
                            "transport_s": receipt["processing"]["accelerator_transport"]["last_exchange"]["wall_elapsed_s"]})
                    report["campaigns"].append({"mode": mode, "status": campaign["status"],
                        "checks": campaign["checks"], "source_version": campaign["source_version"],
                        "observations": campaign["observation_count"], "nodes": len(campaign["processing"]),
                        "tls": campaign["tls"], "gateway_start_s": load_time, "timings": comparisons})
                finally:
                    stopped = time.monotonic()
                    process.terminate()
                    stdout, stderr = process.communicate(timeout=10)
                    (args.evidence / f"{mode}-gateway.log").write_text(stdout + stderr, encoding="utf-8")
                    if process.returncode or Path(config["socket_path"]).exists():
                        raise RuntimeError("gateway shutdown failed or left its socket")
                    if report["campaigns"]:
                        report["campaigns"][-1]["gateway_stop_s"] = time.monotonic() - stopped
        report["status"] = "PASS"
    except Exception as error:
        report.update(status="FAIL", error_type=type(error).__name__, error=str(error))
    report["finished_at"] = datetime.now(timezone.utc).isoformat()
    (args.evidence / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": report["status"], "campaigns": len(report["campaigns"]),
                      "hardware_validated": False}))
    return 0 if report["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
