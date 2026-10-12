"""Fetch the pinned public MIT author archive; never mutate previous evidence."""
from __future__ import annotations

import argparse
import hashlib
import json
import urllib.request
import zipfile
from pathlib import Path

from space_validation_reference import AUTHOR_ARCHIVE_SHA256, AUTHOR_COMMIT, sha256

REPO = Path(__file__).resolve().parents[1]
URL = "https://zenodo.org/records/6384558/files/jwallnoefer/multisat_qrepeater_sim_archive-v1.1.0.zip?download=1"
DEFAULT = REPO / "outputs/space_validation_preparation_20261010"


def prepare(destination=DEFAULT):
    output = Path(destination).resolve()
    output.mkdir(parents=True, exist_ok=True)
    archive = output / "zenodo-6384558-v1.1.0.zip"
    if not archive.is_file():
        request = urllib.request.Request(URL, headers={"User-Agent": "OrionQ-independent-qualification/1"})
        with urllib.request.urlopen(request, timeout=60) as response:
            payload = response.read(2_000_001)
        if len(payload) > 2_000_000 or hashlib.sha256(payload).hexdigest() != AUTHOR_ARCHIVE_SHA256:
            raise ValueError("author archive failed size/hash admission; no unverified file persisted")
        archive.write_bytes(payload)
    if sha256(archive) != AUTHOR_ARCHIVE_SHA256:
        raise ValueError("existing archive differs: refusing overwrite or unverified reference")
    extraction = output / "author-v1.1.0"
    hashes = {}
    with zipfile.ZipFile(archive) as z:
        for member in z.infolist():
            if member.is_dir():
                continue
            # Unix zips use slash paths; require every resolved target within
            # the dedicated extraction root, including malicious traversal.
            target = (extraction / member.filename).resolve()
            if not target.is_relative_to(extraction.resolve()):
                raise ValueError("unsafe archive member path")
            data = z.read(member)
            if target.exists():
                if target.read_bytes() != data:
                    raise ValueError("existing author file differs; preserve and refuse overwrite")
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
            hashes[str(target.relative_to(output))] = sha256(target)
    result = {"schema": "orion.space-author-archive.v1", "url": URL, "accessed": "2026-10-10",
              "version": "v1.1.0", "license": "MIT", "commit": AUTHOR_COMMIT,
              "archive_sha256": sha256(archive), "source_hashes": hashes,
              "scope": "Original author reference code, no Orion physics imports or upstream patches"}
    manifest = output / "author-archive-manifest.json"
    serialized = json.dumps(result, indent=2) + "\n"
    if manifest.exists() and manifest.read_text(encoding="utf-8") != serialized:
        raise ValueError("existing archive manifest differs; preserve previous evidence")
    manifest.write_text(serialized, encoding="utf-8")
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT)
    args = parser.parse_args()
    result = prepare(args.output)
    print(json.dumps({"source_files": len(result["source_hashes"]), "sha256": result["archive_sha256"]}))


if __name__ == "__main__":
    main()
