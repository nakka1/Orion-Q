"""Source-bound build provenance is stable across unrelated Git commits."""
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess

import pytest

SPEC = importlib.util.spec_from_file_location(
    "runtime_manifest_builder", Path(__file__).resolve().parents[1] / "tools/build_runtime_diagnostic_manifest.py")
manifest = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(manifest)


def git(root, *args):
    return subprocess.run(["git", "-c", f"safe.directory={root.as_posix()}",
                           "-c", "user.name=OrionQ fixture", "-c", "user.email=fixture@example.invalid",
                           *args], cwd=root, capture_output=True, text=True, check=True).stdout.strip()


@pytest.fixture
def repository(tmp_path):
    root = tmp_path / "repository"
    root.mkdir()
    for name in manifest.FILES:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# qualified source\n", encoding="utf-8")
    git(root, "init", "--quiet")
    git(root, "add", ".")
    git(root, "commit", "--quiet", "-m", "source fixture")
    return root, root / "diagnostic.json"


def test_changed_head_with_identical_sources_preserves_creation_bytes_and_mtime(repository):
    root, output = repository
    original = manifest.write_manifest(root=root, output=output)
    output.write_bytes((json.dumps(original, indent=4).replace("\n", "\r\n") + "\r\n").encode())
    before, mtime = output.read_bytes(), output.stat().st_mtime_ns
    (root / "README.md").write_text("unrelated documentation\n", encoding="utf-8")
    git(root, "add", "README.md")
    git(root, "commit", "--quiet", "-m", "documentation only")
    assert git(root, "rev-parse", "HEAD") != original["revision"]
    assert manifest.build(root=root, output=output) == original
    assert manifest.write_manifest(root=root, output=output) == original
    assert output.read_bytes() == before
    assert output.stat().st_mtime_ns == mtime


def test_changed_source_regenerates_hashes_and_creation_revision(repository):
    root, output = repository
    original = manifest.write_manifest(root=root, output=output)
    changed = root / manifest.FILES[0]
    changed.write_text("# changed diagnostic source\n", encoding="utf-8")
    git(root, "add", ".")
    git(root, "commit", "--quiet", "-m", "source change")
    current = manifest.write_manifest(root=root, output=output)
    assert current["revision"] == git(root, "rev-parse", "HEAD") != original["revision"]
    assert current["source_sha256"][manifest.FILES[0]] == hashlib.sha256(changed.read_bytes()).hexdigest()
    assert current["source_sha256"] != original["source_sha256"]
    assert json.loads(output.read_bytes()) == current
    assert not list(output.parent.glob(".runtime-diagnostic-*"))


@pytest.mark.parametrize("fault", ["schema", "revision", "extra", "missing_source", "wrong_source",
                                  "duplicate", "nonfinite", "truncated"])
def test_invalid_cache_is_regenerated_and_never_accepted(repository, fault):
    root, output = repository
    valid = manifest.write_manifest(root=root, output=output)
    invalid = json.loads(json.dumps(valid))
    if fault == "schema":
        invalid["schema"] = "unsupported"
    elif fault == "revision":
        invalid["revision"] = True
    elif fault == "extra":
        invalid["unreviewed"] = "extension"
    elif fault == "missing_source":
        invalid["source_sha256"].pop(manifest.FILES[0])
    elif fault == "wrong_source":
        invalid["source_sha256"][manifest.FILES[0]] = "0" * 64
    raw = json.dumps(invalid)
    if fault == "duplicate":
        raw = raw.replace('"schema":', '"schema": "unsupported", "schema":', 1)
    elif fault == "nonfinite":
        raw = raw.replace(json.dumps(valid["revision"]), "NaN", 1)
    elif fault == "truncated":
        raw = raw[:15]
    output.write_text(raw, encoding="utf-8")
    assert manifest.write_manifest(root=root, output=output) == valid
    assert json.loads(output.read_bytes()) == valid
    assert output.read_text(encoding="utf-8") != raw


def test_no_git_revision_is_explicit_and_stable(tmp_path, monkeypatch):
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path))
    root = tmp_path / "outside-git"
    root.mkdir()
    for name in manifest.FILES:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# source\n", encoding="utf-8")
    output = root / "diagnostic.json"
    record = manifest.write_manifest(root=root, output=output)
    # Prevent discovery of an outer checkout when basetemp lives inside one.
    assert record["revision"] is None
    before = output.read_bytes()
    assert manifest.write_manifest(root=root, output=output) == record
    assert output.read_bytes() == before
