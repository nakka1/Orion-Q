"""Fixture-only Linux CLI/mTLS smoke: private state, real processes, no hardware."""
from __future__ import annotations

import argparse
import base64
import json
import os
from pathlib import Path
import platform
import select
import subprocess
import sys
import time

from quantum_autopilot.security import SecurityRuntime
from quantum_autopilot.security.pki import OperationalPKI
from quantum_autopilot.service.client import CoreClient
from quantum_autopilot.service.config import load_config
from quantum_autopilot.service.runtime import mounted_provider
from quantum_autopilot.service.tls import load_leaf


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", required=True, type=Path)
    parser.add_argument("--state", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    args = parser.parse_args(argv)
    if platform.system() != "Linux" or args.state.exists():
        parser.error("actual Linux and a new private fixture directory are required")
    os.umask(0o077)
    state = args.state.resolve()
    state.mkdir(mode=0o700)
    private = state / "fixture-secrets"
    private.mkdir(mode=0o700)
    fixture_secrets = []
    for name, length in (("admin", 48), ("service", 48), ("master", 32)):
        value = os.urandom(length)
        fixture_secrets.append(value)
        (private / name).write_bytes(value)
        (private / name).chmod(0o600)
    config_path, client_leaf = state / "core.json", state / "client-leaf.json"
    backend = state / "backend"
    scope = "workspace:" + backend.as_posix()
    settings = {"schema": "orion.core-service.v1", "repository_root": str(args.repository.resolve()),
                "state_root": str(backend), "identity": {"subject": "cli-core", "credential_file": str(private / "service")},
                "provisioning_identity": {"subject": "cli-admin", "credential_file": str(private / "admin")},
                "vault": {"key_id": "fixture-master", "key_file": str(private / "master")},
                "http": {"host": "127.0.0.1", "port": 0, "allowed_hosts": ["127.0.0.1", "localhost"],
                         "allowed_origins": [], "requests_per_minute": 120},
                "tls": {"root": str(state / "certificates"), "server_leaf": str(state / "server-leaf.json")}}
    config_path.write_text(json.dumps(settings), encoding="utf-8")
    cli = [sys.executable, "-m", "quantum_autopilot.service.cli", "--config", str(config_path)]
    output = []
    process = None
    report = {"schema": "orion.core-linux-cli-smoke.v1", "status": "running", "uid": os.geteuid(),
              "gid": os.getegid(), "fixture_only": True, "physical_action_performed": False}

    def run(*arguments):
        result = subprocess.run([*cli, *arguments], capture_output=True, text=True, timeout=30)
        output.extend((result.stdout, result.stderr))
        if result.returncode:
            raise RuntimeError("fixture CLI command failed")
        return json.loads(result.stdout)

    try:
        run("provision", "--server-names", "localhost", "127.0.0.1")
        run("grant-certificate", "cli-observer", "--roles", "operator", "--scopes", scope, scope + "/*",
            "security:identity", "--services", "dashboard", "--output", str(client_leaf))
        config = load_config(config_path)
        admin = SecurityRuntime(config.security_root, profile="operational", identity_profile="service",
                                bootstrap=False, secret_provider=mounted_provider(config))
        administrator = admin.authenticate_service("cli-admin", (private / "admin").read_bytes())
        with admin.identity_scope(administrator):
            context = OperationalPKI(config.tls["root"], admin).client_context(load_leaf(client_leaf))
        # Administration is an offline setup phase, absent from serve's config.
        settings.pop("provisioning_identity")
        config_path.write_text(json.dumps(settings), encoding="utf-8")
        checked = run("check")
        assert checked["identity_profile"] == "service" and checked["audit_valid"]
        process = subprocess.Popen([*cli, "serve"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        deadline, ready = time.monotonic() + 15, None
        while time.monotonic() < deadline and process.poll() is None:
            readable, _, _ = select.select([process.stdout], [], [], .1)
            if readable:
                line = process.stdout.readline()
                output.append(line)
                ready = json.loads(line)
                break
        assert ready is not None and ready["status"] == "listening"
        assert ready["processing_clock_enabled"] is True
        url = f"https://localhost:{ready['port']}"
        client = CoreClient(url, context)
        health = client.health()
        assert health["processing_clock"]["period_s"] == 1
        assert health["processing_clock"]["nominal_time_scale"] == 1
        client.network_command("configure_network_input", {"kind": "simulated", "seed": 17, "scenario": "quiet"})
        deadline = time.monotonic() + 10
        snapshot = None
        while time.monotonic() < deadline:
            snapshot = client.console_snapshot(include_static=False)
            if snapshot["live"]["tick"] >= 2:
                break
            time.sleep(.1)
        assert snapshot is not None and snapshot["live"]["tick"] >= 2
        assert any(link["qber"] is not None for link in snapshot["live"]["links"])
        before = snapshot["live"]["tick"]
        del client
        time.sleep(1.5)
        returned = CoreClient(url, context).console_snapshot(include_static=False)
        assert returned["live"]["tick"] > before
        assert returned["security"]["subject"] == "cli-observer"
        assert returned["health"]["physical_read_only"] is True
        started_shutdown = time.monotonic()
        process.terminate()
        stdout, stderr = process.communicate(timeout=12)
        output.extend((stdout, stderr))
        assert process.returncode == 0
        logs = "".join(output)
        assert all(value.hex() not in logs and base64.b64encode(value).decode() not in logs for value in fixture_secrets)
        report.update(status="PASS", artifact_version=health["autopilot_version"], server_pid=process.pid,
                      tls="TLSv1.3-mTLS", period_s=1, source="simulated", tick_before_disconnect=before,
                      tick_after_disconnect=returned["live"]["tick"], shutdown_exit_code=process.returncode,
                      shutdown_duration_s=time.monotonic() - started_shutdown, secrets_in_logs=False)
    except Exception as error:
        report.update(status="blocked", error_type=type(error).__name__)
    finally:
        if process is not None and process.poll() is None:
            process.kill()
            process.communicate(timeout=5)
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report))
    return 0 if report["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
