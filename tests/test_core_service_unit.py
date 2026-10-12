"""Reviewable boot unit: no GUI authority, implicit secrets or provisioning.

Artifact checks are not a claim of starting systemd. Functional checks exercise
the existing CLI/runtime and scheduler with explicit test-only identities.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import time

import pytest

from quantum_autopilot.service.config import CoreConfig


ROOT = Path(__file__).resolve().parents[1]
UNIT = ROOT / "packaging/core/systemd/orionq-core.service"
GUIDE = ROOT / "docs/product/orionq_background_service.md"


def read_unit():
    result, section = {}, None
    for raw in UNIT.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1]
            assert section not in result, "duplicate service section"
            result[section] = {}
            continue
        assert section is not None and "=" in line
        key, value = line.split("=", 1)
        if key != "LoadCredential":
            assert key not in result[section], "duplicate directive masks review"
        result[section].setdefault(key, []).append(value)
    return result


def documented_configuration():
    examples = re.findall(r"```json\n(.*?)\n```", GUIDE.read_text(encoding="utf-8"), re.S)
    assert len(examples) == 1
    return json.loads(examples[0])


def localized_configuration(tmp_path):
    value = documented_configuration()
    value["repository_root"] = str(ROOT)
    value["state_root"] = str(tmp_path / "state")
    value["identity"]["credential_file"] = str(tmp_path / "missing-service-secret")
    value["vault"]["key_file"] = str(tmp_path / "missing-vault-secret")
    value["tls"] = {"root": str(tmp_path / "state/certificates"),
                    "server_leaf": str(tmp_path / "server-leaf.json")}
    value["http"]["host"] = "127.0.0.1"
    value["http"]["port"] = 0
    return value


def test_boot_unit_invokes_only_preflight_and_foreground_core():
    from quantum_autopilot.service.cli import parser
    unit = read_unit()
    service = unit["Service"]
    for field, command in (("ExecStartPre", "check"), ("ExecStart", "serve")):
        argv = shlex.split(service[field][0])
        assert argv[:4] == ["/opt/orionq/venv/bin/python", "-I", "-m", "quantum_autopilot.service.cli"]
        args = parser().parse_args(argv[4:])
        assert args.command == command and args.config.as_posix() == "/etc/orionq/core.json"
    assert "ExecStop" not in service  # SIGTERM uses the existing bounded drain.
    assert not any(name in service for name in ("Environment", "EnvironmentFile", "ExecCondition"))
    assert unit["Install"]["WantedBy"] == ["multi-user.target"]
    assert service["StandardInput"] == ["null"]
    assert service["Type"] == ["exec"]  # exec success is not application readiness.


def test_unit_credentials_are_external_and_match_runtime_references():
    service = read_unit()["Service"]
    references = dict(value.split(":", 1) for value in service["LoadCredential"])
    assert references == {"orion-service": "/etc/orionq/secrets/orion-service",
                          "orion-vault": "/etc/orionq/secrets/orion-vault"}
    config = documented_configuration()
    assert "provisioning_identity" not in config
    assert config["identity"]["credential_file"] == "/run/credentials/orionq-core.service/orion-service"
    assert config["vault"]["key_file"] == "/run/credentials/orionq-core.service/orion-vault"
    assert config["vault"]["previous_keys"] == {}
    assert config["state_root"] == "/var/lib/" + service["StateDirectory"][0]
    assert service["User"] == ["orionq"] and service["Group"] == ["orionq"]
    assert not any("admin" in value for value in references.values())


def test_restart_and_shutdown_are_bounded_and_preserve_fail_closed_exit():
    unit = read_unit()
    service = unit["Service"]
    assert unit["Unit"]["StartLimitIntervalSec"] == ["300"]
    assert unit["Unit"]["StartLimitBurst"] == ["3"]
    assert service["Restart"] == ["on-failure"]
    assert service["RestartSec"] == ["10s"]
    assert service["RestartPreventExitStatus"] == ["2"]
    assert service["KillSignal"] == ["SIGTERM"] and service["KillMode"] == ["control-group"]
    assert service["SendSIGKILL"] == ["yes"]
    assert float(service["TimeoutStopSec"][0][:-1]) > documented_configuration()["clock"]["stop_timeout_s"]
    assert service["LimitCORE"] == ["0"]
    assert service["UMask"] == ["0077"] and service["StateDirectoryMode"] == ["0700"]
    for key, value in {"NoNewPrivileges": "yes", "ProtectSystem": "strict", "ProtectHome": "yes",
                       "PrivateTmp": "yes", "PrivateDevices": "yes", "CapabilityBoundingSet": "",
                       "AmbientCapabilities": ""}.items():
        assert service[key] == [value]


def test_documented_service_configuration_parses_without_operator_login(tmp_path):
    config = CoreConfig.parse(localized_configuration(tmp_path))
    assert config.identity.subject == "orion-core"
    assert config.provisioning_identity is None
    assert config.clock_settings == {"enabled": True, "period_s": 1.0, "stop_timeout_s": 10.0}
    assert config.http_policy.max_concurrency == 8


@pytest.mark.parametrize("change", [
    {"enabled": "true", "period_s": 1.0, "stop_timeout_s": 10.0},
    {"enabled": True, "period_s": 0, "stop_timeout_s": 10.0},
    {"enabled": True, "period_s": 1.0, "stop_timeout_s": 91},
])
def test_invalid_boot_clock_fails_before_runtime(tmp_path, change):
    value = localized_configuration(tmp_path)
    value["clock"] = change
    with pytest.raises(ValueError):
        CoreConfig.parse(value)
    assert not (tmp_path / "state").exists()


@pytest.mark.parametrize("command", ["check", "serve"])
def test_missing_boot_secrets_block_cli_without_listener_or_desktop(tmp_path, monkeypatch, capsys, command):
    from quantum_autopilot.service import cli
    import quantum_autopilot.security.runtime as security_runtime
    import quantum_autopilot.security.vault as vault
    value = localized_configuration(tmp_path)
    config_path = tmp_path / "core.json"
    config_path.write_text(json.dumps(value), encoding="utf-8")

    def forbidden(*_args, **_kwargs):
        raise AssertionError("boot attempted a listener, provisioning or interactive authority")

    monkeypatch.setattr(cli, "create_core_server", forbidden)
    monkeypatch.setattr(cli, "_administration", forbidden)
    monkeypatch.setattr(security_runtime, "operating_system_identity", forbidden)
    monkeypatch.setattr(vault, "_dpapi", forbidden)
    assert cli.main(["--config", str(config_path), command]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert json.loads(captured.err)["status"] == "blocked"
    assert "missing-service-secret" not in captured.err and "missing-vault-secret" not in captured.err
    assert not (tmp_path / "state/security/vault.json").exists()


def test_service_clock_advances_without_login_and_does_not_activate_a_source(tmp_path, monkeypatch):
    from quantum_autopilot.qkd.dashboard_api import AssuranceDashboardService
    from quantum_autopilot.security import SecurityRuntime
    import quantum_autopilot.security.runtime as security_runtime
    from quantum_autopilot.security.secret_providers import AESGCMSecretProvider
    from quantum_autopilot.security.service_identity import ServiceIdentityBinding, ServiceIdentityProvision
    from quantum_autopilot.service.clock import HeadlessClock
    from quantum_autopilot.service.runtime import build_runtime

    def forbidden(*_args, **_kwargs):
        raise AssertionError("service requested an interactive identity")

    monkeypatch.setattr(security_runtime, "operating_system_identity", forbidden)
    config = CoreConfig.parse(localized_configuration(tmp_path))

    class TestOnlyKeySource:
        def __init__(self):
            self.key = os.urandom(32)

        def read_key(self, key_id, version):
            assert (key_id, version) == ("test-only", 1)
            return self.key

    provider = AESGCMSecretProvider(TestOnlyKeySource(), active_key_id="test-only", active_version=1)
    admin_secret, service_secret = os.urandom(48), os.urandom(48)
    administration = SecurityRuntime(config.security_root, profile="operational", identity_profile="service",
        secret_provider=provider, bootstrap=True,
        service_bootstrap=ServiceIdentityProvision("unit-test-admin", ("administrator",), ("*",), admin_secret))
    admin = administration.authenticate_service("unit-test-admin", admin_secret)
    with administration.identity_scope(admin):
        administration.grant_service_identity("orion-core", ("operator",), ("*",), service_secret)
    runtime = build_runtime(config, secret_provider=provider,
        identity_binding=ServiceIdentityBinding("orion-core", lambda: service_secret))
    service = AssuranceDashboardService(ROOT, control_journal_path=config.journal, security=runtime)
    clock = HeadlessClock(service, period_s=.02, stop_timeout_s=5)
    try:
        before = service.network_live_state()
        clock.start()
        deadline = time.monotonic() + 5
        while clock.status()["processed_steps"] < 2 and time.monotonic() < deadline:
            time.sleep(.01)
        clock.stop()
        after = service.network_live_state()
        assert clock.status()["processed_steps"] >= 2
        assert after["tick"] > before["tick"]
        assert after["source"]["kind"] == before["source"]["kind"] == "none"
        assert runtime.current_identity().subject == "orion-core"
        assert service.health()["physical_read_only"] is True
        assert runtime.audit.verify()["valid"]
    finally:
        clock.stop()
        service._release_controller_io_resources()


@pytest.mark.skipif(os.name != "posix" or shutil.which("systemd-analyze") is None,
                    reason="systemd syntax parser needs Linux; no service is installed or started")
def test_systemd_255_unit_syntax_without_starting_service(tmp_path):
    # The deployment path is not installed in CI. Replace only the executable
    # for parser admission; argv, credentials and sandbox directives stay intact.
    source = UNIT.read_text(encoding="utf-8")
    test_unit = tmp_path / UNIT.name
    test_unit.write_text(source.replace("/opt/orionq/venv/bin/python", "/usr/bin/true"), encoding="utf-8")
    result = subprocess.run(["systemd-analyze", "verify", "--man=no", str(test_unit)],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
