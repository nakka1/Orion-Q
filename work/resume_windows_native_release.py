"""Resume the official finite Windows build after its failed live-network gate.

The original PowerShell tail is retained verbatim except for one fresh pytest
basetemp. Earlier successful JUnits, failed evidence, and source bindings are
recorded before execution. This tool never invents a successful build result.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import uuid
import xml.etree.ElementTree as ET


def digest(path):
    hasher = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def artifact(path):
    return {"path": str(path), "bytes": path.stat().st_size, "sha256": digest(path)}


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def junit(path, *, approved):
    tree = ET.parse(path).getroot()
    if tree.tag not in {"testsuite", "testsuites"}:
        raise ValueError("Invalid JUnit root: " + str(path))
    cases = list(tree.iter("testcase"))
    if not cases:
        raise ValueError("Empty JUnit: " + str(path))
    counts = Counter()
    for case in cases:
        status = ("errors" if case.find("error") is not None else
                  "failures" if case.find("failure") is not None else
                  "skipped" if case.find("skipped") is not None else "passed")
        counts[status] += 1
    declared_failure = any(int(suite.get(key, "0")) for suite in tree.iter("testsuite")
                           for key in ("failures", "errors"))
    failed = bool(counts["failures"] or counts["errors"] or declared_failure)
    if failed == approved:
        raise ValueError("Unexpected JUnit approval state: " + str(path))
    return {**artifact(path), "mtime_ns": path.stat().st_mtime_ns,
            "executions": len(cases),
            "counts": {key: counts[key] for key in ("passed", "failures", "errors", "skipped")}}


def source_inputs(repository):
    paths = []
    for folder in ("src", "packaging"):
        for path in (repository / folder).rglob("*"):
            if path.is_file() and not any(
                part == "__pycache__" or part.endswith(".egg-info") for part in path.parts
            ) and path.suffix not in {".pyc", ".pyo"}:
                if path.is_symlink():
                    raise ValueError("Symlink in build inputs: " + str(path))
                paths.append(path)
    paths.append(repository / "pyproject.toml")
    return [{"path": path.relative_to(repository).as_posix(),
             "bytes": path.stat().st_size, "sha256": digest(path)}
            for path in sorted(paths)]


def copy_verified(source, destination):
    if destination.exists():
        raise ValueError("Historical evidence already exists: " + str(destination))
    shutil.copyfile(source, destination)
    if digest(source) != digest(destination):
        raise ValueError("Evidence copy changed bytes")
    return artifact(destination)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--release-root", type=Path, required=True)
    parser.add_argument("--context", type=Path, required=True)
    parser.add_argument("--evidence-output", type=Path, required=True)
    parser.add_argument("--expected-version", default="5.64.2")
    args = parser.parse_args()
    if sys.flags.optimize:
        raise ValueError("Optimized qualification execution is unsupported")
    repository = args.repository.resolve(strict=True)
    release = args.release_root.resolve(strict=True)
    evidence = args.evidence_output.resolve()
    if not evidence.is_relative_to(repository / "outputs"):
        raise ValueError("Public evidence must be inside repository outputs")
    if evidence.exists():
        raise ValueError("Evidence output must be new")
    context = json.loads(args.context.read_text(encoding="utf-8-sig"))
    base = Path(context["release_base"]).resolve(strict=True)
    if release.parent != base or not release.name.startswith(args.expected_version + "-native-"):
        raise ValueError("Canonical release root does not match the original build")
    script = repository / "tools/build_windows_console.ps1"
    provider = repository / "src/quantum_autopilot/console/service_provider.py"
    for path, key in ((script, "build_script_sha256"), (provider, "service_provider_sha256")):
        if digest(path) != context[key]:
            raise ValueError("Original production/script binding changed: " + str(path))
    python = Path(context["python"]).resolve(strict=True)
    raw = script.read_bytes()
    text = raw.decode("utf-8")
    names = re.findall(r'--junitxml\s+\(Join-Path\s+\$releaseRoot\s+"([^"/\\]+\.xml)"\)', text)
    if len(names) != 26 or len(set(names)) != 26 or names[20] != "pytest-live-network-results.xml":
        raise ValueError("Official 26-corpus order changed")
    previous = [junit(release / name, approved=True) for name in names[:20]]
    failed_path = release / names[20]
    failed = junit(failed_path, approved=False)
    for name in names[21:]:
        if (release / name).exists():
            raise ValueError("A later corpus already has evidence: " + name)
    match = re.search(r'^    & \$Python -m pytest[^\r\n]*--junitxml \(Join-Path \$releaseRoot "pytest-live-network-results.xml"\)',
                      text, re.MULTILINE)
    if match is None:
        raise ValueError("Official continuation offset not found")
    tail = text[match.start():]
    old_basetemp = '(Join-Path $releaseRoot "pytest-live-network")'
    if tail.count(old_basetemp) != 1:
        raise ValueError("Live-network basetemp substitution is ambiguous")
    new_name = "pytest-live-network-bootstrap-" + uuid.uuid4().hex
    changed_tail = tail.replace(old_basetemp, '(Join-Path $releaseRoot "' + new_name + '")')
    if changed_tail.replace('(Join-Path $releaseRoot "' + new_name + '")', old_basetemp) != tail:
        raise ValueError("Tail contains an unauthorized change")
    product_start = text.index('$productJson = & $Python -c ')
    product_end = text.index('# Source and runtime-test storage', product_start)
    product_read = text[product_start:product_end]
    header = '''param([string]$Python, [string]$repositoryRoot, [string]$releaseRoot)
$ErrorActionPreference = "Stop"
Set-Location -LiteralPath $repositoryRoot
$Python = (Resolve-Path -LiteralPath $Python).Path
'''
    header += product_read
    header += 'if ($version -ne "' + args.expected_version + '") { throw "Resume product version differs" }\n'
    header += '''$SkipInstaller = $true
$previousPythonPath = $env:PYTHONPATH
$previousQtPlatform = $env:QT_QPA_PLATFORM
try {
    $env:PYTHONPATH = Join-Path $repositoryRoot "src"
    $env:QT_QPA_PLATFORM = "offscreen"
'''
    inputs = source_inputs(repository)
    evidence.mkdir(parents=True)
    generated = evidence / "official-tail.ps1"
    generated.write_bytes((header + changed_tail).encode("utf-8"))
    history = evidence / "failed-attempt"
    history.mkdir()
    historical_xml = copy_verified(failed_path, history / failed_path.name)
    original_log = args.context.parent / "build-bootstrap.log"
    historical_log = copy_verified(original_log, history / original_log.name)
    intervention_path = args.context.parent / "live-network-interruption.json"
    intervention = json.loads(intervention_path.read_text(encoding="utf-8-sig"))
    interrupted_at = datetime.fromisoformat(intervention["interrupted_at_utc"].replace("Z", "+00:00"))
    xml_at = datetime.fromtimestamp(failed["mtime_ns"] / 1e9, timezone.utc)
    resolution = {
        "schema": "orion.windows-failed-attempt-resolution.v1", "status": "FAILED",
        "automatic_passed": False, "complete_junit_available": True,
        "junit_completed_before_process_intervention": xml_at < interrupted_at,
        "junit": failed, "public_copy": historical_xml, "log": historical_log,
        "original_intervention_receipt": artifact(intervention_path),
        "explanation": "The failed workload wrote a complete JUnit before the authorized process-tree intervention during finalization. The earlier conservative interruption receipt remains unchanged.",
    }
    write_json(evidence / "failed-attempt-resolution.json", resolution)
    command = ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(generated),
               "-Python", str(python), "-repositoryRoot", str(repository), "-releaseRoot", str(release)]
    proof = {
        "schema": "orion.windows-official-build-continuation.v1", "status": "PREPARED",
        "created_at_utc": datetime.now(timezone.utc).isoformat(), "canonical_release_root": str(release),
        "original_context": artifact(args.context), "original_script": artifact(script),
        "service_provider": artifact(provider), "original_tail_byte_offset": len(text[:match.start()].encode("utf-8")),
        "original_tail_line": text[:match.start()].count("\n") + 1,
        "original_tail_sha256": hashlib.sha256(tail.encode("utf-8")).hexdigest(),
        "substitutions": [{"original": old_basetemp, "replacement": '(Join-Path $releaseRoot "' + new_name + '")'}],
        "generated_script": artifact(generated), "command": command, "reused_approved_junits": previous,
        "production_inputs_before": inputs, "failed_attempt": str(evidence / "failed-attempt-resolution.json"),
        "test_change": artifact(repository / "tests/test_console_live_provider.py"),
        "scope": "20 original successful corpora retained; complete corpus21 rerun in a fresh private basetemp, followed by literal original corpora22-26 and all native/package/acceptance gates. No claims about an unrecorded historical full-source inventory.",
    }
    write_json(evidence / "provenance.json", proof)
    if digest(failed_path) != historical_xml["sha256"]:
        raise ValueError("Failed canonical JUnit changed before replacement")
    failed_path.unlink()
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    log = evidence / "resume.log"
    with log.open("wb") as stream:
        completed = subprocess.run(command, cwd=repository, stdout=stream, stderr=subprocess.STDOUT,
                                   shell=False, creationflags=flags, check=False)
    (evidence / "exit-code.txt").write_text(str(completed.returncode) + "\n", encoding="ascii")
    after = source_inputs(repository)
    originals_unchanged = all(digest(Path(row["path"])) == row["sha256"] for row in previous)
    proof.update({"exit_code": completed.returncode, "finished_at_utc": datetime.now(timezone.utc).isoformat(),
                  "production_inputs_after": after, "production_inputs_unchanged": after == inputs,
                  "reused_approved_junits_unchanged": originals_unchanged, "log": artifact(log)})
    valid = completed.returncode == 0 and after == inputs and originals_unchanged
    if valid:
        proof["final_junits"] = [junit(release / name, approved=True) for name in names]
    proof["status"] = "PASS" if valid else "FAIL"
    write_json(evidence / "provenance.json", proof)
    print(json.dumps({"status": proof["status"], "exit_code": completed.returncode,
                      "provenance": str(evidence / "provenance.json")}))
    return 0 if valid else 1


if __name__ == "__main__":
    raise SystemExit(main())
