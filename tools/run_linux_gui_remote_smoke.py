"""Run native Linux GUI/core processes in a fresh private mTLS fixture.

This verifies the actual packaged GUI's authenticated reads in offscreen Qt;
it does not inject application modules, imitate clicks, inspect operator state
or claim native monitor/input qualification. Fixture input is simulated and
physical actuation remains locked. The GUI is terminated explicitly with
SIGTERM after observing its reads; the core's clock must continue independently.
"""
from __future__ import annotations

import argparse
import base64
from collections import Counter
from contextlib import ExitStack
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import select
import subprocess
import sys
import time
import tomllib

from cryptography.hazmat.primitives import serialization
from quantum_autopilot.control_plane.topology import SimulatedTopologyStore
from quantum_autopilot.network_engine.institutional import ensure_institutional_workspace
from quantum_autopilot.security import SecurityRuntime
from quantum_autopilot.security.pki import OperationalPKI
from quantum_autopilot.service.client import CoreClient
from quantum_autopilot.service.config import load_config
from quantum_autopilot.service.runtime import mounted_provider
from quantum_autopilot.service.tls import load_leaf


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def selected_version(explicit=None):
    """Use checkout metadata by default, preserving explicit older artifacts."""
    if explicit is None:
        metadata = Path(__file__).resolve().parents[1] / "pyproject.toml"
        explicit = tomllib.loads(metadata.read_text(encoding="utf-8"))["project"]["version"]
    if not isinstance(explicit, str) or re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", explicit) is None:
        raise ValueError("a declared numeric release version is required")
    return explicit


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--core", required=True, type=Path)
    parser.add_argument("--gui", required=True, type=Path)
    parser.add_argument("--repository", required=True, type=Path, help="packaged core _internal resource root")
    parser.add_argument("--state", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--duration-s", type=float, default=12.0)
    parser.add_argument("--expected-version", help="default: canonical checkout project.version")
    args = parser.parse_args(argv)
    try:
        version = selected_version(args.expected_version)
    except (KeyError, OSError, ValueError) as error:
        parser.error(str(error))
    if sys.flags.optimize or os.environ.get("PYTHONOPTIMIZE", "") not in {"", "0"}:
        parser.error("validation must run without Python optimization so assertion gates remain enabled")
    if (platform.system() != "Linux" or os.geteuid() == 0 or args.state.exists() or args.report.exists()
            or not 8 <= args.duration_s <= 30 or args.state.resolve().parent != Path("/var/tmp")):
        parser.error("Linux, non-root UID, fresh /var/tmp fixture/report and bounded duration required")
    for binary in (args.core, args.gui):
        if binary.read_bytes()[:4] != b"\x7fELF":
            parser.error("actual ELF executables required")
    gui_identity = subprocess.run([str(args.gui.resolve()), "--version"], capture_output=True,
        text=True, timeout=30, shell=False, env=dict(os.environ, QT_QPA_PLATFORM="offscreen"))
    if gui_identity.returncode != 0 or gui_identity.stdout.strip() != "OrionQ " + version:
        parser.error("selected GUI version does not match the expected release")
    os.umask(0o077)
    state = args.state.resolve()
    state.mkdir(mode=0o700)
    private = state / "credentials"
    private.mkdir(mode=0o700)
    secrets = []
    for name, size in (("admin", 48), ("service", 48), ("master", 32)):
        secret = os.urandom(size)
        secrets.append(secret)
        path = private / name
        path.write_bytes(secret)
        path.chmod(0o600)
    backend = state / "backend"
    scope = "workspace:" + backend.as_posix()
    config_path, leaf_path = state / "core.json", state / "client-leaf.json"
    settings = {"schema": "orion.core-service.v1", "repository_root": str(args.repository.resolve()),
                "state_root": str(backend),
                "identity": {"subject": "gui-fixture-core", "credential_file": str(private / "service")},
                "provisioning_identity": {"subject": "gui-fixture-admin", "credential_file": str(private / "admin")},
                "vault": {"key_id": "gui-fixture-master", "key_file": str(private / "master")},
                "http": {"host": "127.0.0.1", "port": 0, "allowed_hosts": ["localhost", "127.0.0.1"],
                         "allowed_origins": [], "requests_per_minute": 600},
                "tls": {"root": str(state / "pki"), "server_leaf": str(state / "server-leaf.json")},
                "clock": {"enabled": True, "period_s": 1.0, "stop_timeout_s": 15.0}}
    config_path.write_text(json.dumps(settings), encoding="utf-8")
    command = [str(args.core.resolve()), "--config", str(config_path)]
    core = gui = None
    logs = []
    began = time.monotonic()
    report = {"schema": "orion.native-linux-gui-remote-smoke.v1", "status": "running", "uid": os.geteuid(),
              "fixture_only": True, "physical_action_performed": False, "hardware_validated": False,
              "display_backend": "offscreen", "physical_display_or_mouse_validated": False,
              "core_sha256": digest(args.core), "gui_sha256": digest(args.gui),
              "script_sha256": digest(Path(__file__)), "application_modules_replaced": False}
    report["expected_version"] = version
    report["gui_version_stdout"] = gui_identity.stdout.strip()

    def offline(*arguments):
        result = subprocess.run([*command, *arguments], capture_output=True, text=True, timeout=30, shell=False)
        logs.extend((result.stdout, result.stderr))
        if result.returncode:
            raise RuntimeError("offline native fixture command failed")
        return json.loads(result.stdout)

    def gui_reads(runtime):
        counts = Counter()
        with runtime.audit.path.open(encoding="utf-8") as stream:
            for line in stream:
                row = json.loads(line)
                if row["identity"] == "gui-fixture-observer":
                    counts[row["operation"]] += 1
        return counts

    try:
        offline("provision", "--server-names", "localhost", "127.0.0.1")
        offline("grant-certificate", "gui-fixture-observer", "--roles", "operator", "--scopes", scope,
                scope + "/*", "security:identity", "--services", "dashboard", "--output", str(leaf_path))
        config = load_config(config_path)
        runtime = SecurityRuntime(config.security_root, profile="operational", identity_profile="service",
                                  bootstrap=False, secret_provider=mounted_provider(config))
        administrator = runtime.authenticate_service("gui-fixture-admin", (private / "admin").read_bytes())
        with runtime.identity_scope(administrator):
            leaf = load_leaf(leaf_path)
            context = OperationalPKI(config.tls["root"], runtime).client_context(leaf)
            key = serialization.load_pem_private_key(Path(leaf["private_key"]).read_bytes(),
                password=runtime.vault.get_bytes(leaf["password_ref"]))
            encoded_key = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                            serialization.NoEncryption())
        key_path = private / "client.key"
        key_path.write_bytes(encoded_key)
        key_path.chmod(0o600)
        secrets.append(encoded_key)
        ensure_institutional_workspace(SimulatedTopologyStore(backend / "sandbox_journal.sqlite"))
        settings.pop("provisioning_identity")
        config_path.write_text(json.dumps(settings), encoding="utf-8")
        core = subprocess.Popen([*command, "serve"], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                text=True, shell=False)
        readable, _, _ = select.select([core.stdout], [], [], 30)
        assert readable and core.poll() is None
        line = core.stdout.readline()
        logs.append(line)
        ready = json.loads(line)
        assert ready["status"] == "listening" and ready["processing_clock_enabled"] is True
        url = f"https://localhost:{ready['port']}"
        client = CoreClient(url, context, timeout_s=10)
        assert client.health()["autopilot_version"] == version
        client.network_command("configure_network_input", {"kind": "simulated", "seed": 17, "scenario": "quiet"})
        before = client.console_snapshot(include_static=False)["live"]["tick"]
        initial_counts = gui_reads(runtime)
        environment = dict(os.environ, QT_QPA_PLATFORM="offscreen", LANG="C.UTF-8", LC_ALL="C.UTF-8",
                           XDG_RUNTIME_DIR=str(state / "runtime"), XDG_CONFIG_HOME=str(state / "config"),
                           XDG_CACHE_HOME=str(state / "cache"), XDG_DATA_HOME=str(state / "data"))
        for variable in ("XDG_RUNTIME_DIR", "XDG_CONFIG_HOME", "XDG_CACHE_HOME", "XDG_DATA_HOME"):
            Path(environment[variable]).mkdir(mode=0o700)
        with ExitStack() as stack:
            stdout = stack.enter_context((state / "gui.stdout").open("wb"))
            stderr = stack.enter_context((state / "gui.stderr").open("wb"))
            gui = subprocess.Popen([str(args.gui.resolve()), "--core-url", url, "--core-ca", leaf["ca"],
                "--core-cert", leaf["certificate"], "--core-key", str(key_path), "--core-timeout-s", "2",
                "--state-root", str(state / "gui-preferences")], cwd=state, env=environment,
                stdout=stdout, stderr=stderr, shell=False)
            deadline = time.monotonic() + args.duration_s
            while time.monotonic() < deadline and gui.poll() is None:
                time.sleep(.1)
            assert gui.poll() is None
            counts = gui_reads(runtime) - initial_counts
            assert counts["network.read"] >= 2
            during = client.console_snapshot(include_static=False)
            assert during["live"]["tick"] > before and during["health"]["physical_read_only"] is True
            gui.terminate()
            gui.wait(timeout=10)
        logs.extend(((state / "gui.stdout").read_text(), (state / "gui.stderr").read_text()))
        deadline = time.monotonic() + 10
        returned = during
        while time.monotonic() < deadline and returned["live"]["tick"] <= during["live"]["tick"]:
            time.sleep(.2)
            returned = client.console_snapshot(include_static=False)
        assert returned["live"]["tick"] > during["live"]["tick"]
        core.terminate()
        stdout, stderr = core.communicate(timeout=20)
        logs.extend((stdout, stderr))
        assert core.returncode == 0
        combined = "".join(logs)
        assert all(value.hex() not in combined and base64.b64encode(value).decode() not in combined for value in secrets)
        assert "-----BEGIN PRIVATE KEY-----" not in combined
        report.update(status="PASS", tls="TLSv1.3-mTLS", artifact_version=version,
                      authenticated_gui_read_operations=dict(counts),
                      gui_alive_through_observation=True, gui_observation_s=args.duration_s,
                      gui_shutdown_method="explicit SIGTERM; not an interactive close test", gui_exit_code=gui.returncode,
                      core_exit_code=core.returncode, backend_tick_before_gui=before,
                      backend_tick_during_gui=during["live"]["tick"],
                      backend_tick_after_gui=returned["live"]["tick"], secrets_in_public_output=False)
    except Exception as error:
        report.update(status="FAIL", error_type=type(error).__name__)
        (state / "fixture-output.log").write_text("".join(logs), encoding="utf-8")
    finally:
        for process in (gui, core):
            if process is not None and process.poll() is None:
                process.kill()
                process.wait(timeout=5)
        report["duration_s"] = time.monotonic() - began
        args.report.parent.mkdir(parents=True, exist_ok=True)
        with args.report.open("x", encoding="utf-8") as stream:
            json.dump(report, stream, indent=2, sort_keys=True, allow_nan=False)
            stream.write("\n")
    print(json.dumps(report, sort_keys=True), flush=True)
    return 0 if report["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
