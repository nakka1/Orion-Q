"""Record the source provenance embedded in a frozen runtime diagnostic."""
import hashlib
import json
import subprocess
from pathlib import Path
import re
import tempfile

ROOT = Path(__file__).resolve().parents[1]
FILES = [f"src/quantum_autopilot/control_plane/{name}.py" for name in
         ("runtime_diagnostic", "runtime", "policy", "safety", "executors", "journal", "contracts", "sqlite_transaction")]
OUTPUT = ROOT / "src/quantum_autopilot/control_plane/data/runtime_diagnostic_manifest_v1.json"


SCHEMA = "orion.runtime-diagnostic-build.v1"


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate manifest property: " + key)
        result[key] = value
    return result


def _reject_nonfinite(value):
    raise ValueError("nonfinite manifest value: " + value)


def _cached(output, sources):
    """Accept only the exact contract and source bytes, never a partial cache."""
    try:
        value = json.loads(output.read_bytes().decode("utf-8-sig"),
                           object_pairs_hook=_unique_object, parse_constant=_reject_nonfinite)
    except (OSError, UnicodeError, ValueError):
        return None
    if not isinstance(value, dict) or set(value) != {"schema", "revision", "source_sha256"}:
        return None
    revision = value["revision"]
    if (value["schema"] != SCHEMA
            or not (revision is None or isinstance(revision, str) and re.fullmatch(r"[0-9a-f]{40}", revision))
            or not isinstance(value["source_sha256"], dict)
            or value["source_sha256"] != sources):
        return None
    return value


def _inputs(root):
    return {name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in FILES}


def _new_record(root, sources):
    result = subprocess.run(["git", "-c", f"safe.directory={root.as_posix()}", "rev-parse", "HEAD"],
                            cwd=root, capture_output=True, text=True, check=False)
    revision = result.stdout.strip() if result.returncode == 0 else None
    if not isinstance(revision, str) or not re.fullmatch(r"[0-9a-f]{40}", revision):
        revision = None
    return {"schema": SCHEMA, "revision": revision, "source_sha256": sources}


def build(*, root=None, output=None):
    root = Path(root or ROOT)
    output = Path(output or OUTPUT)
    sources = _inputs(root)
    return _cached(output, sources) or _new_record(root, sources)


def write_manifest(*, root=None, output=None):
    """Revision records creation provenance; unchanged source keeps exact bytes."""
    root = Path(root or ROOT)
    output = Path(output or OUTPUT)
    sources = _inputs(root)
    cached = _cached(output, sources)
    if cached is not None:
        return cached  # No serialization or write: preserve bytes and timestamps.
    value = _new_record(root, sources)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="wb", dir=output.parent,
                                         prefix=".runtime-diagnostic-", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write((json.dumps(value, indent=2) + "\n").encode("utf-8"))
        temporary.replace(output)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return value


if __name__ == "__main__":
    write_manifest()
