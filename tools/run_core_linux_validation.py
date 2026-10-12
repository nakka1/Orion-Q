"""Install/test the headless wheel on a native Linux filesystem, retaining evidence.

Run with Linux Python, not Windows Python over DrvFS. The mounted checkout is
only a source of code/public fixtures; private state and test secrets stay in
the new native, mode-0700 work directory. No Docker or OS installation occurs.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET


TESTS = (
    "test_core_deployment.py", "test_security_vault_portable.py",
    "test_service_identity_runtime.py", "test_portable_core_service.py",
    "test_portable_kme.py", "test_headless_clock.py", "test_headless_http_drain.py",
    "test_mixed_controller.py", "test_mixed_controller_service.py", "test_lab_mixed_sources.py",
    "test_lab_pspl.py", "test_lab_processing_node.py", "test_core_operator_cli.py", "test_terminal_live.py",
    "test_background_core_sessions.py", "test_core_service_unit.py",
    "test_pl_accelerator.py", "test_lab_pl_transport.py", "test_service_pl_gateway.py",
    "test_petalinux_deployment.py",
    "test_fpga_rtl_reference.py",
)
RESOURCES = (
    "docs/thesis/tcc_claim_registry.json", "evidence/tcc/v1.0.0/tables",
    "configs/qkd_assurance/irelandqci_example_operating_point.json",
    "src/quantum_autopilot/qkd/data/ireland_qci_grid_v1.json",
    "outputs/qkd_coexistence_measured/experimental_cells.csv",
    "docs/product/orionq_background_service.md",
    "docs/product/orionq_petalinux.md", "LICENSE",
)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", required=True, type=Path)
    parser.add_argument("--evidence", required=True, type=Path)
    args = parser.parse_args(argv)
    if platform.system() != "Linux" or os.name != "posix":
        parser.error("actual Linux execution is required")
    repository, evidence = args.repository.resolve(strict=True), args.evidence.resolve()
    evidence.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="orionq-linux-", dir="/var/tmp"))
    work.chmod(0o700)
    project = work / "project"
    report = {"schema": "orion.core-linux-validation.v1", "status": "running",
              "started_at": datetime.now(timezone.utc).isoformat(), "native_work": str(work),
              "kernel": platform.release(), "architecture": platform.machine(), "uid": os.geteuid(),
              "gid": os.getegid(), "python": platform.python_version(), "logical_cpus": os.cpu_count(),
              "linux_executed": True, "docker_executed": False, "commands": [],
              "os_release": Path("/etc/os-release").read_text(encoding="utf-8"),
              "physical_action_performed": False}
    environment = {key: value for key, value in os.environ.items() if key != "PYTHONPATH"}
    environment["PYTHONNOUSERSITE"] = "1"

    def run(name, command, *, cwd=work, timeout=900):
        print(f"Linux validation: {name}", flush=True)
        started = time.monotonic()
        result = subprocess.run([str(item) for item in command], cwd=cwd, env=environment,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, errors="replace", timeout=timeout)
        (evidence / f"{name}.log").write_text(result.stdout, encoding="utf-8")
        report["commands"].append({"name": name, "exit_code": result.returncode,
                                   "duration_s": time.monotonic() - started})
        if result.returncode:
            raise RuntimeError(f"{name} failed; inspect its evidence log")
        return result.stdout

    try:
        filesystem = run("native-filesystem", ["stat", "-f", "-c", "%T", work]).strip()
        report["filesystem"] = filesystem
        if filesystem in {"9p", "drvfs", "ntfs", "ntfs3", "cifs", "fuseblk"}:
            raise RuntimeError("private state requires a native Linux filesystem")
        run("stage-core", [sys.executable, repository / "packaging/core/prepare_source.py",
                           "--repository", repository, "--destination", project])
        shutil.copytree(repository / "packaging/core", project / "packaging/core",
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        shutil.copytree(repository / "packaging/petalinux", project / "packaging/petalinux",
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        shutil.copytree(repository / "fpga/orionq_pl", project / "fpga/orionq_pl",
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        (project / "tests").mkdir()
        for filename in (*TESTS, "conftest.py", "__init__.py"):
            shutil.copyfile(repository / "tests" / filename, project / "tests" / filename)
        shutil.copyfile(repository / "pytest.ini", project / "pytest.ini")
        (project / "tools").mkdir()
        for filename in ("run_core_linux_validation.py", "run_core_linux_cli_smoke.py",
                         "run_mixed_sequence_nodes.py", "sequence_network_backend.py", "run_mixed_controller_campaign.py",
                         "run_pspl_rtl_validation.py", "run_pspl_linux_campaign.py"):
            shutil.copyfile(repository / "tools" / filename, project / "tools" / filename)
        for relative in RESOURCES:
            source, destination = repository / relative, project / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            if source.is_dir():
                shutil.copytree(source, destination)
            else:
                shutil.copyfile(source, destination)
        hashed = sorted(path for base in (project / "src", project / "tests", project / "packaging/core", project / "tools")
                        for path in base.rglob("*") if path.is_file())
        hashed.extend(sorted(path for path in (project / "packaging/petalinux").rglob("*") if path.is_file()))
        hashed.extend(sorted(path for path in (project / "fpga/orionq_pl").rglob("*") if path.is_file()))
        hashes = {path.relative_to(project).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
                  for path in hashed}
        hashes["pyproject.toml"] = hashlib.sha256((project / "pyproject.toml").read_bytes()).hexdigest()
        hashes["pytest.ini"] = hashlib.sha256((project / "pytest.ini").read_bytes()).hexdigest()
        (evidence / "source-hashes.json").write_text(json.dumps(hashes, indent=2) + "\n", encoding="utf-8")
        report["source_hash_count"] = len(hashes)
        report["repository_pyproject_sha256"] = hashlib.sha256((repository / "pyproject.toml").read_bytes()).hexdigest()
        report["staged_source_hashes_sha256"] = hashlib.sha256((evidence / "source-hashes.json").read_bytes()).hexdigest()
        run("create-venv", [sys.executable, "-m", "venv", work / "venv"])
        python = work / "venv/bin/python"
        run("install-wheel-profile", [python, "-m", "pip", "install", project, "pytest>=7,<10",
                                      "setuptools>=61", "wheel>=0.43"])
        run("dependency-check", [python, "-m", "pip", "check"])
        run("dependency-freeze", [python, "-m", "pip", "freeze"])
        run("artifact-no-desktop", [python, "-c", "import importlib.util; import quantum_autopilot.service.cli; "
            "assert all(importlib.util.find_spec(name) is None for name in "
            "('PySide6','PyQt6','torch','sklearn','quantum_autopilot.console','quantum_autopilot.desktop')); "
            "print('PASS: installed artifact without GUI/ML')"])
        run("pytest", [python, "-m", "pytest", "-q", *(f"tests/{name}" for name in TESTS),
                       "--basetemp", work / "pytest", "--junitxml", evidence / "tests.xml"], cwd=project)
        run("cli-smoke", [python, project / "tools/run_core_linux_cli_smoke.py", "--repository", project,
                          "--state", work / "cli-smoke", "--report", evidence / "cli-smoke.json"], timeout=90)
        report["status"] = "PASS"
    except Exception as error:
        report["status"] = "blocked"
        report["error_type"] = type(error).__name__
        # This is a local tooling error, never a request or credential value.
        report["error"] = str(error)
    finally:
        if (evidence / "tests.xml").is_file():
            suites = ET.parse(evidence / "tests.xml").getroot().findall("testsuite")
            report["tests"] = {field: sum(int(suite.get(field, 0)) for suite in suites)
                               for field in ("tests", "failures", "errors", "skipped")}
        reports = list((work / "pytest").rglob("installed-core-report.json"))
        if len(reports) == 1:
            shutil.copyfile(reports[0], evidence / "installed-core-report.json")
        elif report["status"] == "PASS":
            report["status"], report["error"] = "blocked", "one installed-wheel execution report is required"
        if (project / "core-artifact.json").is_file():
            manifest = json.loads((project / "core-artifact.json").read_text(encoding="utf-8"))
            report["artifact_version"] = manifest["version"]
            shutil.copyfile(project / "core-artifact.json", evidence / "core-artifact.json")
        report["finished_at"] = datetime.now(timezone.utc).isoformat()
        (evidence / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": report["status"], "evidence": str(evidence), "native_work": str(work)}))
    return 0 if report["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
