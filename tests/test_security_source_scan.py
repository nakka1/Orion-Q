"""Security scanning must run in bare Python CI without external executables."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import os
import subprocess
import sys

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "tools/check_security_sources.py"
SPEC = importlib.util.spec_from_file_location("security_source_scan", SCRIPT)
scanner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(scanner)
pytestmark = pytest.mark.unit


def put(root, name, text):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def synthetic_secret():
    return "CI_FIXTURE_" + "z" * 48


def test_scan_needs_no_external_executables(tmp_path, monkeypatch):
    put(tmp_path, "src/clean.py", "answer = 42\n")
    monkeypatch.setenv("PATH", "")
    def denied(*args, **kwargs):
        raise AssertionError("Source scan must not invoke external executables")
    monkeypatch.setattr(subprocess, "run", denied)
    result = scanner.scan(tmp_path)
    assert result["passed"] and result["files_scanned"] == 1


@pytest.mark.parametrize("with_secret", [False, True])
def test_cli_empty_path_retains_exit_code_and_redacts_evidence(tmp_path, with_secret):
    secret = synthetic_secret()
    source = 'password = ' + repr(secret) + '\n' if with_secret else 'answer = 42\n'
    put(tmp_path, "src/input.py", source)
    output = tmp_path / "outputs/security/source-scan.json"
    env = {**os.environ, "PATH": "", "PYTHONUTF8": "1"}
    result = subprocess.run([sys.executable, "-I", "-B", str(SCRIPT),
                             "--root", str(tmp_path), "--output", str(output)],
                            env=env, capture_output=True, text=True, timeout=20)
    assert result.returncode == (1 if with_secret else 0), result.stderr
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["passed"] is not with_secret
    assert report["files_scanned"] == 1
    assert len(report["findings"]) == int(with_secret)
    assert secret not in result.stdout + result.stderr + output.read_text(encoding="utf-8")


@pytest.mark.parametrize("field", ["api_key", "client-secret", "access_token", "password"])
def test_credentials_detected_without_values(tmp_path, field):
    secret = synthetic_secret()
    put(tmp_path, "src/input.py", '# fixture\n' + field + ' = ' + repr(secret) + '\n')
    result = scanner.scan(tmp_path)
    assert result["findings"] == [{"file": "src/input.py", "line": 2,
                                  "reason": "credential_literal"}]
    assert not result["passed"] and secret not in json.dumps(result)


@pytest.mark.parametrize("kind", ["", "RSA ", "EC ", "OPENSSH "])
def test_private_key_headers_still_detected_and_redacted(tmp_path, kind):
    material = "K" * 100
    put(tmp_path, "src/input.py", '# fixture\n-----BEGIN ' + kind +
        'PRIVATE KEY-----\n' + material + '\n')
    result = scanner.scan(tmp_path)
    assert result["findings"] == [{"file": "src/input.py", "line": 2,
                                  "reason": "embedded_private_key"}]
    assert material not in json.dumps(result)


@pytest.mark.parametrize("directory", [".git", ".audit-winvenv", ".audit-new-run",
    ".sites-cache", "outputs", "release", "build", "dist", "archives",
    "node_modules", "work", "_to_delete", ".venv", "venv", "__pycache__"])
def test_generated_directories_are_pruned(tmp_path, directory):
    put(tmp_path, directory + "/input.py", 'password = ' + repr(synthetic_secret()))
    put(tmp_path, "src/clean.py", "answer = 42\n")
    result = scanner.scan(tmp_path)
    assert result["passed"] and result["files_scanned"] == 1


def test_hidden_source_and_env_are_checked_even_if_gitignored(tmp_path):
    put(tmp_path, ".gitignore", ".env\n.github/\n")
    put(tmp_path, ".env", 'password = ' + repr(synthetic_secret()))
    put(tmp_path, ".github/workflows/check.yml", 'client_secret: ' + repr(synthetic_secret()))
    result = scanner.scan(tmp_path)
    assert not result["passed"] and result["files_scanned"] == 2
    assert {f["file"] for f in result["findings"]} == {".env", ".github/workflows/check.yml"}


def test_output_order_is_stable_and_paths_use_forward_slashes(tmp_path):
    for name in ["src/z.py", "src/a.py", "configs/z.json", "configs/a.json"]:
        put(tmp_path, name, 'password = ' + repr(synthetic_secret()))
    first = scanner.scan(tmp_path)
    assert first == scanner.scan(tmp_path)
    assert [f["file"] for f in first["findings"]] == [
        "configs/a.json", "configs/z.json", "src/a.py", "src/z.py"]


def test_non_source_and_oversized_files_do_not_expand_scan(tmp_path):
    secret = 'password = ' + repr(synthetic_secret())
    put(tmp_path, "blob.bin", secret)
    put(tmp_path, "large.py", secret + " " * (2 * 1024 * 1024))
    put(tmp_path, "src/clean.py", "answer = 42\n")
    result = scanner.scan(tmp_path)
    assert result["passed"] and result["files_scanned"] == 1


def test_nonexistent_root_is_not_a_successful_empty_scan(tmp_path):
    with pytest.raises(FileNotFoundError):
        scanner.scan(tmp_path / "missing")


def test_directory_read_failure_is_not_silently_reported_as_pass(tmp_path, monkeypatch):
    def failed_walk(*args, **kwargs):
        kwargs["onerror"](PermissionError("synthetic enumeration failure"))
        return iter(())
    monkeypatch.setattr(os, "walk", failed_walk)
    with pytest.raises(PermissionError, match="enumeration failure"):
        scanner.scan(tmp_path)


def test_symlinks_do_not_scan_external_files_or_loop(tmp_path):
    root = tmp_path / "scan"
    root.mkdir()
    external = put(tmp_path, "outside/input.py", 'password = ' + repr(synthetic_secret()))
    try:
        (root / "linked.py").symlink_to(external)
        (root / "outside").symlink_to(external.parent, target_is_directory=True)
        (root / "loop").symlink_to(root, target_is_directory=True)
    except OSError as error:
        pytest.skip("Symlink creation unavailable: " + type(error).__name__)
    put(root, "src/clean.py", "answer = 42\n")
    result = scanner.scan(root)
    assert result["passed"] and result["files_scanned"] == 1
