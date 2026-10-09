"""Restore pinned runtime assets for a clean checkout; never overwrite differing data."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil


def prepare(root: Path) -> None:
    root = root.resolve()
    assets = root / "runtime_assets/native/v1"
    manifest = json.loads((assets / "manifest.json").read_text(encoding="utf-8"))
    for item in manifest["files"]:
        source = (assets / item["source"]).resolve()
        destination = (root / item["destination"]).resolve()
        source.relative_to(assets.resolve())
        destination.relative_to(root)
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        if digest != item["sha256"]:
            raise ValueError(f"Invalid pinned runtime asset: {source.name}")
        if destination.exists():
            if hashlib.sha256(destination.read_bytes()).hexdigest() != digest:
                raise ValueError(f"Existing resource differs; review it before replacing: {destination}")
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)


if __name__ == "__main__":
    prepare(Path(__file__).resolve().parents[1])
