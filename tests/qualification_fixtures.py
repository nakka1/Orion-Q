"""Verified archived inputs, extracted only to disposable pytest directories."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path, PurePosixPath
import stat
from zipfile import ZipFile

FIXTURES = Path(__file__).parent / "fixtures/qualification"


def extract_qualification_fixture(filename: str, destination: Path) -> Path:
    """Verify the tracked archive before extracting; never read outputs/work."""
    manifest = json.loads((FIXTURES / "provenance.json").read_text(encoding="utf-8"))
    entries = [entry for entry in manifest["fixtures"] if entry["filename"] == filename]
    if len(entries) != 1:
        raise ValueError("unknown qualification fixture")
    entry = entries[0]
    archive = FIXTURES / filename
    raw = archive.read_bytes()
    if len(raw) != entry["size_bytes"] or hashlib.sha256(raw).hexdigest() != entry["sha256"]:
        raise ValueError("qualification fixture integrity mismatch")
    destination = Path(destination)
    destination.mkdir()
    root = destination.resolve()
    expected = {row["filename"]: row for row in entry.get("members", [])}
    seen = set()
    with ZipFile(archive) as stream:
        for member in stream.infolist():
            relative = PurePosixPath(member.filename)
            if (relative.is_absolute() or ".." in relative.parts or "\\" in member.filename
                    or ":" in member.filename or stat.S_ISLNK(member.external_attr >> 16)):
                raise ValueError("unsafe qualification fixture member")
            target = destination.joinpath(*relative.parts)
            if not target.resolve().is_relative_to(root):
                raise ValueError("qualification fixture escapes temporary directory")
            if member.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            if member.filename in seen:
                raise ValueError("duplicate qualification fixture member")
            seen.add(member.filename)
            payload = stream.read(member)
            if expected:
                record = expected.get(member.filename)
                if (record is None or len(payload) != record["size_bytes"]
                        or hashlib.sha256(payload).hexdigest() != record["sha256"]):
                    raise ValueError("qualification fixture member integrity mismatch")
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("xb") as output:
                output.write(payload)
    if expected and seen != set(expected):
        raise ValueError("incomplete qualification fixture")
    return destination
