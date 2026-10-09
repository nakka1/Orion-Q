"""Read-only, bounded environment inventory; never probes unidentified serial devices."""
from __future__ import annotations

import argparse
import hashlib
from importlib import metadata, util
import json
from pathlib import Path
import platform
import subprocess
import sys
from datetime import datetime, timezone


def inventory(root):
    modules = {}
    for name, package in (("numpy", "numpy"), ("PySide6", "PySide6"), ("TimeTagger", "TimeTagger"),
                          ("serial", "pyserial"), ("pyvisa", "PyVISA"), ("pyvisa_sim", "PyVISA-sim"),
                          ("cryptography", "cryptography"), ("sgp4", "sgp4"),
                          ("laboneq", "laboneq")):
        available = util.find_spec(name) is not None
        try:
            version = metadata.version(package)
        except metadata.PackageNotFoundError:
            version = None
        modules[name] = {"import_available": available, "version": version}
    command = ("Get-PnpDevice -PresentOnly -ErrorAction Stop | Where-Object { $_.FriendlyName -match "
               "'Swabian|TimeTagger|Thorlabs|Single Quantum|ID Quantique|Toshiba|Serial|COM[0-9]' } | "
               "Select-Object Class,FriendlyName,InstanceId | ConvertTo-Json -Compress")
    pnp = {"scope": "Windows present-device name filter; not exhaustive network/USB identification"}
    if sys.platform == "win32":
        try:
            result = subprocess.run(["powershell.exe", "-NoProfile", "-Command", command],
                                    capture_output=True, text=True, timeout=30)
            pnp.update({"exit_code": result.returncode, "devices": json.loads(result.stdout) if result.stdout.strip() else [],
                        "no_device_commands_sent": True})
        except (OSError, ValueError, subprocess.TimeoutExpired) as error:
            pnp["error_type"] = type(error).__name__
    files = []
    for base in ("data/raw", "data/reference", "acquisition", "configs/integrations"):
        for item in sorted((root / base).rglob("*")):
            if item.is_file() and item.suffix in {".csv", ".json", ".ttbin", ".md"}:
                files.append({"path": item.relative_to(root).as_posix(), "bytes": item.stat().st_size,
                              "sha256": hashlib.sha256(item.read_bytes()).hexdigest()})
    return {"schema": "orion.lab.inventory.v1", "observed_at_utc": datetime.now(timezone.utc).isoformat(),
            "python": platform.python_version(), "executable": sys.executable, "modules": modules,
            "hardware_observation": pnp, "data_and_configs": files,
            "instrument_endpoints": "none identified in inspected project configuration; no network scanning performed",
            "hardware_validation": "not performed",
            "native_capture_candidates": [file for file in files if file["path"].endswith(".ttbin")],
            "experimental_replay": "published records available; native .ttbin candidate requires official SDK, license and metadata validation",
            "existing_replay": "public WDM/QKD aggregates and telemetry; per-file data origin must be checked"}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    result = inventory(root)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({"output": str(args.output.resolve()), "catalogued_files": len(result["data_and_configs"]),
                      "hardware_validation": result["hardware_validation"]}))


if __name__ == "__main__":
    main()
