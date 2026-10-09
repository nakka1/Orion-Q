"""The vendored qnet packages are exactly what external/VENDORED.json records."""
import hashlib
import difflib
import json
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit
ROOT = Path(__file__).resolve().parents[1]
MANIFEST = json.loads((ROOT / "external" / "VENDORED.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("entry", MANIFEST["vendored"], ids=lambda entry: entry["package"])
def test_vendored_files_match_manifest(entry):
    changed = [path for path, record in entry["files"].items()
               if hashlib.sha256((ROOT / path).read_bytes()).hexdigest() != record["sha256"]]
    assert not changed, f"vendored files edited after vendoring: {changed[:5]}"
    package = ROOT / entry["package"]
    recorded = {path for path in entry["files"] if path.startswith(entry["package"] + "/")}
    present = {path.relative_to(ROOT).as_posix() for path in package.rglob("*")
               if path.is_file() and "__pycache__" not in path.parts}
    assert present == recorded, "files added to or removed from a vendored package"


def test_both_versions_import_side_by_side():
    import qnet_network.physics.fiber as network_fiber
    import qnet_qkd.physics.decoy as qkd_decoy

    assert network_fiber.__name__.startswith("qnet_network.") and qkd_decoy.__name__.startswith("qnet_qkd.")


def test_local_compatibility_patch_keeps_its_base_bytes_and_exact_diff():
    """Local adaptations are auditable; original author hashes are never replaced."""
    chunks = []
    patch_paths = set()
    recorded = []
    for entry in MANIFEST["vendored"]:
        for path, record in entry["files"].items():
            local = record.get("local_patch")
            if local is None:
                continue
            recorded.append(path)
            base = (ROOT / local["base_file"]).read_bytes()
            assert hashlib.sha256(base).hexdigest() == local["base_vendored_sha256"]
            assert len(record["source_sha256"]) == 64 and len(local["base_commit"]) == 40
            if not record["renamed_imports"]:
                assert local["base_vendored_sha256"] == record["source_sha256"]
            patch = ROOT / local["patch"]
            assert hashlib.sha256(patch.read_bytes()).hexdigest() == local["patch_sha256"]
            patch_paths.add(patch)
            chunks.extend(difflib.unified_diff(
                base.decode("utf-8").splitlines(keepends=True),
                (ROOT / path).read_text(encoding="utf-8").splitlines(keepends=True),
                fromfile="a/" + path, tofile="b/" + path))
    assert set(recorded) == {
        f"src/{package}/compat/{name}" for package in ("qnet_network", "qnet_qkd")
        for name in ("__init__.py", "_gmpy2_shim.py")}
    assert len(patch_paths) == 1
    assert next(iter(patch_paths)).read_bytes() == "".join(chunks).encode("utf-8")
