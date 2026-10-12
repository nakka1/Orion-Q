"""Terminal client contracts and real authenticated HTTP; no GUI or hardware."""
import io
import json
import os
import ssl
import subprocess
import sys
import time

import pytest

from quantum_autopilot.service import operator_cli
from quantum_autopilot.service.client import CoreRequestError
from tests.test_portable_core_service import provisioned, serving  # noqa: F401


BASE = ["--url", "https://localhost:443", "--ca", "external-ca.pem",
        "--cert", "external-client.pem", "--key", "external-client.key"]


class RecordingClient:
    def __init__(self, *_args, **_kwargs):
        self.calls = []
        self.error = None

    def _call(self, name, *args, **kwargs):
        self.calls.append((name, args, kwargs))
        if self.error is not None:
            raise self.error
        return {"status": "fixture-response", "physical_action_performed": False}

    def health(self):
        return self._call("health")

    def topology(self):
        return self._call("topology")

    def network_state(self, **kwargs):
        return self._call("network_state", **kwargs)

    def network_step(self, **kwargs):
        return self._call("network_step", **kwargs)

    def network_command(self, *args):
        return self._call("network_command", *args)

    def console_command(self, *args):
        return self._call("console_command", *args)

    def console_evidence(self, *args):
        return self._call("console_evidence", *args)

    def _request(self, *args):
        return self._call("request", *args)

    def mixed_status(self):
        return self._request("GET", "/api/v1/mixed-controller/status")

    def mixed_configure(self, payload):
        return self._request("POST", "/api/v1/mixed-controller/configure", payload)

    def mixed_ingest(self, payload):
        return self._request("POST", "/api/v1/mixed-controller/observations", payload)

    def mixed_demand(self, payload):
        return self._request("POST", "/api/v1/mixed-controller/demands", payload)

    def mixed_release(self, payload):
        return self._request("POST", "/api/v1/mixed-controller/release", payload)


@pytest.fixture
def recorded(monkeypatch):
    client = RecordingClient()
    monkeypatch.setattr(operator_cli, "build_tls_context", lambda *_args: object())
    monkeypatch.setattr(operator_cli, "CoreClient", lambda *_args, **_kwargs: client)
    return client


@pytest.mark.parametrize("arguments,name,values", [
    (["health"], "health", ()),
    (["topology"], "topology", ()),
    (["state", "--metadata-only"], "network_state", ()),
    (["step"], "network_step", ()),
    (["network-command", "set_ml_control", "--input", "-"], "network_command", ("set_ml_control", {"enabled": False})),
    (["console-command", "recalibrate_node", "--input", "-"], "console_command", ("recalibrate_node", {"enabled": False})),
    (["evidence", "--scope", "network"], "console_evidence", ("network",)),
    (["mixed-status"], "request", ("GET", "/api/v1/mixed-controller/status")),
    (["mixed-configure", "--input", "-"], "request", ("POST", "/api/v1/mixed-controller/configure", {"enabled": False})),
    (["mixed-ingest", "--input", "-"], "request", ("POST", "/api/v1/mixed-controller/observations", {"enabled": False})),
    (["mixed-demand", "--input", "-"], "request", ("POST", "/api/v1/mixed-controller/demands", {"enabled": False})),
    (["mixed-release", "--input", "-"], "request", ("POST", "/api/v1/mixed-controller/release", {"enabled": False})),
])
def test_operator_routes_one_explicit_request(recorded, monkeypatch, capsys, arguments, name, values):
    monkeypatch.setattr(sys, "stdin", io.StringIO('{"enabled":false}'))
    assert operator_cli.main(BASE + arguments) == 0
    assert len(recorded.calls) == 1
    actual, args, kwargs = recorded.calls[0]
    assert (actual, args) == (name, values)
    if name in {"network_state", "network_step"}:
        assert kwargs == {"metadata_only": "--metadata-only" in arguments}
    assert json.loads(capsys.readouterr().out)["physical_action_performed"] is False


@pytest.mark.parametrize("payload", [b"", b"[]", b'{"enabled":true,"enabled":false}', b'{"x":NaN}',
                                    b'{"x":1e400}', b"{" + b" " * operator_cli.MAX_INPUT_BYTES + b"}",
                                    ('{"x":' * 27 + '0' + '}' * 27).encode()],
                         ids=["empty", "array", "duplicate", "nan", "infinite", "oversized", "deep"])
def test_invalid_input_cannot_send_a_mutation(recorded, monkeypatch, capsys, payload):
    monkeypatch.setattr(sys, "stdin", io.BytesIO(payload))
    assert operator_cli.main(BASE + ["mixed-demand", "--input", "-"]) == operator_cli.EXIT_INPUT
    assert not recorded.calls
    captured = capsys.readouterr()
    assert captured.out == ""
    assert json.loads(captured.err)["status"] == "invalid_input_or_tls_identity"


def test_input_file_is_bounded_regular_and_symlinks_are_rejected(tmp_path):
    path = tmp_path / "request.json"
    path.write_text('{"id":"test"}', encoding="utf-8")
    assert operator_cli.read_payload(path) == {"id": "test"}
    with pytest.raises(ValueError):
        operator_cli.read_payload(tmp_path)
    oversized = tmp_path / "too-large.json"
    oversized.write_bytes(b" " * (operator_cli.MAX_INPUT_BYTES + 1))
    with pytest.raises(ValueError):
        operator_cli.read_payload(oversized)
    if os.name == "posix":
        link = tmp_path / "request-link.json"
        link.symlink_to(path)
        with pytest.raises(ValueError):
            operator_cli.read_payload(link)
        fifo = tmp_path / "request-fifo.json"
        os.mkfifo(fifo)
        with pytest.raises(ValueError):
            operator_cli.read_payload(fifo)


@pytest.mark.parametrize("status,uncertain,exit_code", [(403, False, 3), (None, False, 3), (503, True, 4), (None, True, 4)])
def test_rejected_or_uncertain_request_has_no_retry(recorded, monkeypatch, capsys, status, uncertain, exit_code):
    recorded.error = CoreRequestError(status, uncertain=uncertain)
    monkeypatch.setattr(sys, "stdin", io.StringIO('{"enabled":false}'))
    assert operator_cli.main(BASE + ["network-command", "set_ml_control", "--input", "-"]) == exit_code
    assert len(recorded.calls) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    message = json.loads(captured.err)
    assert message["automatic_retry"] is False
    if uncertain:
        assert message["status"] == "mutation_outcome_uncertain" and "receipts" in message["guidance"]


def test_exception_and_argument_secrets_never_enter_diagnostics(recorded, monkeypatch, capsys):
    secret = "credential-canary-must-never-be-logged"
    recorded.error = RuntimeError(secret)
    assert operator_cli.main(BASE + ["health"]) == operator_cli.EXIT_REQUEST
    assert secret not in capsys.readouterr().err
    assert operator_cli.main(BASE + ["--password", secret, "health"]) == operator_cli.EXIT_INPUT
    assert secret not in capsys.readouterr().err
    monkeypatch.setattr(operator_cli, "build_tls_context", lambda *_args: (_ for _ in ()).throw(ValueError(secret)))
    assert operator_cli.main(BASE + ["health"]) == operator_cli.EXIT_INPUT
    assert secret not in capsys.readouterr().err


def test_output_is_exclusive_and_output_loss_does_not_repeat_a_mutation(recorded, monkeypatch, tmp_path, capsys):
    destination = tmp_path / "evidence.json"
    assert operator_cli.main(BASE + ["--output", str(destination), "health"]) == 0
    assert json.loads(destination.read_text())["status"] == "fixture-response"
    assert capsys.readouterr().out == ""
    before = destination.read_bytes()
    monkeypatch.setattr(sys, "stdin", io.StringIO('{"enabled":false}'))
    assert operator_cli.main(BASE + ["--output", str(destination), "network-command", "set_ml_control", "--input", "-"]) == 5
    assert destination.read_bytes() == before and len(recorded.calls) == 2
    assert json.loads(capsys.readouterr().err)["status"] == "response_received_output_unavailable"


def test_tls_helper_never_prompts_for_encrypted_private_key(provisioned):
    _config, _runtime, authority_runtime, _administrator, _pki, leaf, *_ = provisioned
    start = time.monotonic()
    with pytest.raises(ssl.SSLError):
        operator_cli.build_tls_context(leaf["ca"], leaf["certificate"], leaf["private_key"])
    assert time.monotonic() - start < 3
    context = operator_cli.build_tls_context(leaf["ca"], leaf["certificate"], leaf["private_key"],
        key_password_reader=lambda: authority_runtime.vault.get_bytes(leaf["password_ref"]))
    assert context.minimum_version == context.maximum_version == ssl.TLSVersion.TLSv1_3
    assert context.check_hostname and context.verify_mode == ssl.CERT_REQUIRED


def test_real_tls_terminal_commands_use_backend_identity_and_authorization(provisioned, monkeypatch, tmp_path, capsys):
    config, runtime, _authority, _admin, _pki, _leaf, auditor_context, operator_context = provisioned
    with serving(config, runtime) as server:
        args = list(BASE)
        args[1] = f"https://localhost:{server.server_port}"
        monkeypatch.setattr(operator_cli, "build_tls_context", lambda *_args: operator_context)
        assert operator_cli.main(args + ["health"]) == 0
        assert json.loads(capsys.readouterr().out)["physical_read_only"] is True
        monkeypatch.setattr(sys, "stdin", io.StringIO('{"kind":"simulated","seed":17,"scenario":"quiet"}'))
        assert operator_cli.main(args + ["network-command", "configure_network_input", "--input", "-"]) == 0
        assert json.loads(capsys.readouterr().out)["source"]["kind"] == "simulated"
        assert operator_cli.main(args + ["step", "--metadata-only"]) == 0
        tick = json.loads(capsys.readouterr().out)["tick"]
        assert tick == 1
        assert operator_cli.main(args + ["state", "--metadata-only"]) == 0
        assert json.loads(capsys.readouterr().out)["tick"] == tick
        destination = tmp_path / "terminal-evidence.json"
        assert operator_cli.main(args + ["--output", str(destination), "evidence", "--scope", "network"]) == 0
        artifact = json.loads(destination.read_text())
        assert artifact["evidence"]["security"]["subject"] == "orion-core"
        assert artifact["physical_action_performed"] is False
        capsys.readouterr()
        monkeypatch.setattr(operator_cli, "build_tls_context", lambda *_args: auditor_context)
        assert operator_cli.main(args + ["state", "--metadata-only"]) == 0
        assert json.loads(capsys.readouterr().out)["tick"] == tick
        assert operator_cli.main(args + ["step"]) == operator_cli.EXIT_REQUEST
        assert json.loads(capsys.readouterr().err)["http_status"] == 403
        assert operator_cli.main(args + ["state", "--metadata-only"]) == 0
        assert json.loads(capsys.readouterr().out)["tick"] == tick


def test_terminal_import_has_no_gui_or_windows_binding_dependency():
    code = "import sys; import quantum_autopilot.service.operator_cli; assert not any(n.startswith(('quantum_autopilot.console','PySide6','tkinter','ctypes.wintypes')) for n in sys.modules)"
    completed = subprocess.run([sys.executable, "-c", code], capture_output=True, timeout=20, check=False)
    assert completed.returncode == 0, completed.stderr.decode(errors="replace")


def test_terminal_anonymous_and_revoked_certificates_fail_closed(provisioned, monkeypatch, capsys):
    config, runtime, authority, administrator, pki, leaf, auditor_context, _operator = provisioned
    with serving(config, runtime) as server:
        args = list(BASE)
        args[1] = f"https://localhost:{server.server_port}"
        anonymous = ssl.create_default_context(cafile=str(leaf["ca"]))
        anonymous.minimum_version = anonymous.maximum_version = ssl.TLSVersion.TLSv1_3
        monkeypatch.setattr(operator_cli, "build_tls_context", lambda *_args: anonymous)
        assert operator_cli.main(args + ["health"]) == operator_cli.EXIT_REQUEST
        assert capsys.readouterr().out == ""
        monkeypatch.setattr(operator_cli, "build_tls_context", lambda *_args: auditor_context)
        assert operator_cli.main(args + ["health"]) == 0
        assert json.loads(capsys.readouterr().out)["physical_read_only"] is True
        with authority.identity_scope(administrator):
            pki.revoke(leaf["serial"])
        assert operator_cli.main(args + ["health"]) == operator_cli.EXIT_REQUEST
        captured = capsys.readouterr()
        assert captured.out == "" and json.loads(captured.err)["http_status"] == 401


@pytest.mark.parametrize("layout", [None, "auto", "dashboard", "plain"])
def test_watch_routes_to_headless_presentation_without_a_mutation(recorded, monkeypatch, capsys, layout):
    from quantum_autopilot.service import terminal_live
    calls = []
    monkeypatch.setattr(terminal_live, "watch", lambda client, **options: calls.append((client, options)) or 0)
    arguments = BASE + ["watch", "--frames", "2", "--interval-s", ".5", "--view", "satellites", "--select", "SAT"]
    if layout is not None:
        arguments += ["--layout", layout]
    assert operator_cli.main(arguments) == 0
    assert calls == [(recorded, {"interval_s": .5, "frames": 2, "view": "satellites", "selected": "SAT", "layout": layout or "auto"})]
    assert not recorded.calls and capsys.readouterr().out == ""


def test_watch_output_failure_is_sanitized_and_does_not_dispatch_or_retry(recorded, monkeypatch, tmp_path, capsys):
    from quantum_autopilot.service import terminal_live
    destination = tmp_path / "existing.txt"
    destination.write_text("preserved", encoding="utf-8")
    calls = []
    monkeypatch.setattr(terminal_live, "watch", lambda *_args, **_kwargs: calls.append(True) or 0)
    assert operator_cli.main(BASE + ["--output", str(destination), "watch", "--frames", "1"]) == 5
    assert destination.read_text(encoding="utf-8") == "preserved" and calls == []
    assert recorded.calls == []
    secret = "private-terminal-error-canary"
    monkeypatch.setattr(terminal_live, "watch", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError(secret)))
    assert operator_cli.main(BASE + ["watch", "--frames", "1"]) == 5
    captured = capsys.readouterr()
    assert "terminal_output_unavailable" in captured.err and secret not in captured.err
    assert recorded.calls == []
