"""Small dependency-free receipts and exclusive evidence bundle exports."""
import hashlib
import json
from pathlib import Path


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(",", ":"))


def receipt(record):
    return {"record": record, "record_sha256": hashlib.sha256(canonical(record).encode("utf-8")).hexdigest()}


def export_bundle(report, markdown, destination, *, stem, extra_files=None):
    if report != receipt(report["record"]) or stem not in {"comparison", "laboneq-emulation"}:
        raise ValueError("invalid evidence receipt or export kind")
    members = {f"{stem}.json": (json.dumps(report, ensure_ascii=False, allow_nan=False, indent=2) + "\n").encode("utf-8"),
               f"{stem}.md": markdown.encode("utf-8")}
    for name, data in (extra_files or {}).items():
        if name not in {"device_setup.json", "experiment.json", "compiled_experiment.json", "controller_evidence_v1.json"} or not isinstance(data, bytes):
            raise ValueError("unsupported evidence artifact")
        members[name] = data
    destination = Path(destination)
    destination.mkdir(parents=False, exist_ok=False)
    for name, data in members.items():
        with (destination / name).open("xb") as stream:
            stream.write(data)
    manifest = {"schema": f"orion.{stem}-export.v1", "record_sha256": report["record_sha256"],
                "files": {name: hashlib.sha256(data).hexdigest() for name, data in members.items()}}
    with (destination / "manifest.json").open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(manifest, stream, ensure_ascii=False, indent=2)
    return str(destination)
