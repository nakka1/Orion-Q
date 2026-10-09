"""Record the source provenance embedded in a frozen runtime diagnostic."""
import hashlib
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FILES = [f"src/quantum_autopilot/control_plane/{name}.py" for name in
         ("runtime_diagnostic", "runtime", "policy", "safety", "executors", "journal", "contracts", "sqlite_transaction")]
OUTPUT = ROOT / "src/quantum_autopilot/control_plane/data/runtime_diagnostic_manifest_v1.json"


def build():
    revision = subprocess.run(["git", "-c", f"safe.directory={ROOT.as_posix()}", "rev-parse", "HEAD"],
                              cwd=ROOT, capture_output=True, text=True, check=False).stdout.strip() or None
    return {"schema": "orion.runtime-diagnostic-build.v1", "revision": revision,
            "source_sha256": {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in FILES}}


if __name__ == "__main__":
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(build(), indent=2) + "\n", encoding="utf-8")
