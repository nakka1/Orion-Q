"""Vendor the author's two qnet_autopilot versions into this repository, reproducibly.

The network models (routing, compensator, allocator) were trained with one version
of ``qnet_autopilot`` and the QKD models (detector, key-rate actor, synchroniser)
with another; the two diverge (``physics/blocks.py``, ``compensation/predictors.py``
and others). Each is vendored whole under its own top-level name so both can be
imported in one process:

* ``src/qnet_network`` ← ``qnet_autopilot/`` of ``Qnet-Network ML.zip``;
* ``src/qnet_qkd``     ← ``qnet_autopilot/`` of the QKD source tree that produced
  the pinned ``modelo_qkd_ml.zip`` (``scripts/export_models.py``);
* ``external/<name>/runs`` ← the author's own result tables and logs, the
  references ORION-Q's reproduction gates compare against;
* ``external/qnet_network/rtl`` ← the compensator's generated Verilog, weight memories
  and golden vectors, simulated by ``tools/verify_author_rtl.py``.

Package files are copied byte for byte except for the rare absolute
``from qnet_autopilot…`` import statement, renamed to the new top level (internal
imports are otherwise relative). Scripts and tests go to ``external/<name>/`` with
every ``qnet_autopilot`` name replaced. ``external/VENDORED.json`` records every file's
hash, so anyone can check the copy against the original archives.

    python tools/vendor_qnet.py --network "Qnet-Network ML.zip" --qkd qnet_qkd_source.zip --qkd-runs "Qnet-QKD ML.zip"

The QKD source tree has no ``runs/``; its records (detector scores, key-rate logs)
come from the author's published ``Qnet-QKD ML.zip`` and are recorded as such.
"""
import argparse
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import zipfile

ROOT = Path(__file__).resolve().parents[1]
SKIP_PARTS = {"__pycache__", "reports", "vendor", "paper", "Claude outputs", ".pytest_cache"}
#: The author's result records under ``runs/`` are kept as reference data for ORION-Q's
#: reproduction gates: tables and logs only, never pickles or weights, at most 1 MiB each.
RUN_RECORDS = {".csv", ".json", ".log"}
MAX_RUN_RECORD_BYTES = 1 << 20
EXTRA = {"scripts", "tests", "exportacao", "dados_cambridge", "artigo", "runs", "rtl"}
TOP_FILES = {"README.md", "pyproject.toml", "requirements.txt", "requirements-dev.txt"}
IMPORT = re.compile(rb"\bqnet_autopilot\b")
STATEMENT = re.compile(rb"\b(from|import)(\s+)qnet_autopilot\b")


def _members(archive):
    """Map archive paths to paths relative to the author's repository root."""
    names = [info for info in archive.infolist() if not info.is_dir()]
    roots = {PurePosixPath(info.filename).parts[0] for info in names}
    strip = 1 if len(roots) == 1 and not any(PurePosixPath(i.filename).parts[0] == "qnet_autopilot" for i in names) else 0
    for info in names:
        parts = PurePosixPath(info.filename).parts[strip:]
        if not parts or SKIP_PARTS & set(parts) or parts[-1].endswith((".pyc", ".pkl")):
            continue
        if parts[0] == "runs" and (PurePosixPath(parts[-1]).suffix not in RUN_RECORDS
                                   or info.file_size > MAX_RUN_RECORD_BYTES):
            continue
        yield PurePosixPath(*parts), info


def vendor(zip_path, name, runs_zip=None):
    raw = Path(zip_path).read_bytes()
    record = {"source_archive": Path(zip_path).name, "source_sha256": hashlib.sha256(raw).hexdigest(),
              "package": f"src/{name}", "extras": f"external/{name}", "files": {}}
    package, extras = ROOT / "src" / name, ROOT / "external" / name
    for target in (package, extras):
        if target.exists():
            shutil.rmtree(target)
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        for relative, info in _members(archive):
            data = archive.read(info)
            original = hashlib.sha256(data).hexdigest()
            if relative.parts[0] == "qnet_autopilot":
                destination = package.joinpath(*relative.parts[1:])
                if relative.suffix == ".py":
                    # Internal imports are relative; the rare absolute one is renamed, prose is not.
                    data = STATEMENT.sub(rb"\1\2" + name.encode(), data)
            elif relative.parts[0] in EXTRA or (len(relative.parts) == 1 and relative.name in TOP_FILES):
                destination = extras.joinpath(*relative.parts)
                if relative.suffix in {".py", ".toml", ".sh"}:
                    data = IMPORT.sub(name.encode(), data)
            else:
                continue
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(data)
            record["files"][destination.relative_to(ROOT).as_posix()] = {
                "from": relative.as_posix(), "source_sha256": original,
                "sha256": hashlib.sha256(data).hexdigest(), "renamed_imports": original != hashlib.sha256(data).hexdigest()}
    if runs_zip is not None:
        # The QKD source tree carries no runs/; the author's published QKD release does.
        runs_raw = Path(runs_zip).read_bytes()
        record["runs_archive"] = Path(runs_zip).name
        record["runs_archive_sha256"] = hashlib.sha256(runs_raw).hexdigest()
        with zipfile.ZipFile(io.BytesIO(runs_raw)) as archive:
            for relative, info in _members(archive):
                if relative.parts[0] != "runs":
                    continue
                data = archive.read(info)
                destination = extras.joinpath(*relative.parts)
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(data)
                digest = hashlib.sha256(data).hexdigest()
                record["files"][destination.relative_to(ROOT).as_posix()] = {
                    "from": f"{record['runs_archive']}:{relative.as_posix()}", "source_sha256": digest,
                    "sha256": digest, "renamed_imports": False}
    return record


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--network", required=True)
    parser.add_argument("--qkd", required=True)
    parser.add_argument("--qkd-runs", default=None,
                        help="the author's QKD release (Qnet-QKD ML.zip) whose runs/ records to keep")
    args = parser.parse_args()
    manifest = {"schema": "orion.vendored-qnet.v1",
                "rule": "package: absolute import statements renamed; scripts/tests: qnet_autopilot -> name; "
                        "runs: author's result tables/logs (<= 1 MiB, no pickles) kept byte for byte",
                "vendored": [vendor(args.network, "qnet_network"), vendor(args.qkd, "qnet_qkd", args.qkd_runs)]}
    out = ROOT / "external" / "VENDORED.json"
    out.write_text(json.dumps(manifest, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    for item in manifest["vendored"]:
        print(item["package"], len(item["files"]), "files from", item["source_archive"], item["source_sha256"][:16])


if __name__ == "__main__":
    main()
