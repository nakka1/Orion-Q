"""Copy accepted package/public evidence, retaining native build provenance."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sys
from zipfile import ZipFile


def digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def distribution_candidates(source):
    """The verified frozen distribution may carry public CA roots, never state."""
    package = source / "OrionQ"
    if not package.is_dir():
        raise FileNotFoundError("Compiled distribution is missing")
    if package.is_symlink() or getattr(package.stat(follow_symlinks=False), "st_file_attributes", 0) & 0x400:
        raise ValueError("Compiled distribution root is a link")
    private_parts = {".security", ".dpapi", "state", "validation-state"}
    private_suffixes = {".db", ".sqlite", ".sqlite3", ".key", ".pem", ".pfx", ".p12", ".crt", ".cer"}
    selected = set()
    for parent, directories, files in os.walk(package, followlinks=False):
        for name in (*directories, *files):
            path = Path(parent) / name
            relative = path.relative_to(source)
            public_ca = relative.as_posix() == "OrionQ/_internal/certifi/cacert.pem"
            if (path.is_symlink() or getattr(path.stat(follow_symlinks=False), "st_file_attributes", 0) & 0x400
                    or name.lower() in private_parts or (path.suffix.lower() in private_suffixes and not public_ca)
                    or name.lower().endswith(("-wal", "-shm", "-journal"))
                    or name.lower() in {"credentials.json", "token.json", "vault.json", "keys.json"}
                    or name.lower().startswith(".env") and name.lower() not in {".env.example", ".env.template"}
                    or not path.resolve().is_relative_to(package.resolve())):
                raise ValueError("Unexpected private state or link in compiled distribution: " + relative.as_posix())
        selected.update(Path(parent) / name for name in files)
    return selected


def public_candidates(source):
    """Select compiled distribution and exact public gates, never sibling state."""
    repository = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location(
        "native_git_evidence_selector", repository / "tools/package_git_release.py")
    selector = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(selector)
    source = Path(source).resolve()
    selected = distribution_candidates(source)
    # The closure validates every required public child and canonical timestamped
    # GlobalOps/stress run. It does not traverse private runtime/configuration.
    selected.update(selector.release_candidates(source))
    required = {
        "OrionQ-5.64.2-native-windows-x64.zip",
        "OrionQ-5.64.2-native-windows-x64.zip.sha256", "portable-package.json", "self-test.json",
        "branding-validation/branding-report.json", "optional-models-validation/optional-models-report.json",
        "native-acceptance/acceptance-report.json", "native-acceptance/self-test.json",
        *(f"{folder}/report.json" for folder in (
            "desktop-behavior-validation", "roadmap-validation", "startup-validation",
            "laboratory-validation", "satellite-mission-validation", "campaign-validation",
            "satellite-validation", "global-validation")),
    }
    for relative in sorted(required):
        path = selector.safe_release_path(source, relative)
        selected.add(path)
    # Check the entire plan before any destination exists. Also reject junction
    # ancestors inside the compiled distribution, before opening candidate bytes.
    for path in sorted(selected):
        relative = path.relative_to(source).as_posix()
        if relative.startswith("OrionQ/"):
            if relative == "OrionQ/_internal/certifi/cacert.pem":
                selector.secret_check(path)
            continue
        selector.safe_release_path(source, relative)
        if path.suffix not in {".zip", ".sha256"}:
            selector.secret_check(path)
    return sorted(selected)


def qualification_candidates(source, qualification, selected, *, repository,
                             frozen_source_report=None, supplementary_tests=None):
    """Validate the whole accepted-evidence plan before stat/open of its rows."""
    spec = importlib.util.spec_from_file_location(
        "native_qualification_selector", repository / "tools/package_git_release.py")
    selector = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(selector)
    public_root = repository / "outputs/native_release_5_64_2_20261011"
    selected = set(selected)
    build_tool = repository / "tools/build_windows_console.ps1"
    private_parts = {".security", ".dpapi", "state", "validation-state", "runtime"}

    def safe_external(path, *, suffix):
        path = Path(path)
        if (not path.is_absolute() or ".." in path.parts or path.suffix.lower() != suffix
                or any(part.lower() in private_parts for part in path.parts)
                or path.name.lower() in {"credentials.json", "token.json", "vault.json", "keys.json"}):
            raise ValueError("Qualification evidence is outside the public plan")
        boundary = source if path.is_relative_to(source) else repository
        if boundary == repository and not path.is_relative_to(public_root):
            raise ValueError("Qualification evidence is outside the public plan")
        return selector.safe_release_path(boundary, path.relative_to(boundary).as_posix())

    frozen = (safe_external(frozen_source_report, suffix=".json")
              if frozen_source_report is not None else None)
    supplementary = Path(supplementary_tests) if supplementary_tests is not None else None
    if supplementary is not None and (
            not supplementary.is_absolute() or ".." in supplementary.parts
            or not supplementary.is_relative_to(public_root)
            or any(part.lower() in private_parts for part in supplementary.parts)):
        raise ValueError("Supplementary evidence must use its explicit public directory")
    planned = []
    groups = (("artifacts", qualification["artifacts"]),
              ("build", qualification["build_tests"]["reports"]),
              ("supplementary", (qualification.get("supplementary_tests") or {}).get("reports", [])))
    for kind, rows in groups:
        for row in rows:
            path = Path(row["path"])
            if ".." in path.parts:
                raise ValueError("Qualification evidence is outside the public plan")
            if not path.is_absolute():
                path = source / path
            if kind == "artifacts" and path == build_tool:
                checked = selector.safe_release_path(repository, "tools/build_windows_console.ps1")
            elif kind == "artifacts" and frozen is not None and path == frozen:
                checked = frozen
            elif kind == "supplementary" and supplementary is not None and path.parent == supplementary:
                checked = safe_external(path, suffix=".xml")
            elif (path in selected and path.is_relative_to(source)
                  and (kind == "artifacts" or kind == "build"
                       and path.parent == source and path.name in selector.RELEASE_JUNIT_REPORTS)):
                checked = selector.safe_release_path(source, path.relative_to(source).as_posix())
            else:
                raise ValueError("Qualification evidence is outside the public plan")
            planned.append((checked, row))
    for path, _ in planned:
        selector.secret_check(path)
    return planned


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--destination", required=True, type=Path)
    parser.add_argument("--qualification", required=True, type=Path)
    parser.add_argument("--frozen-source-report", type=Path,
                        help="Explicit public static comparison already used for consolidation")
    parser.add_argument("--supplementary-tests", type=Path,
                        help="Explicit public supplementary JUnit directory already used for consolidation")
    args = parser.parse_args()
    source = args.source.resolve(strict=True)
    destination = args.destination.resolve()
    qualification = json.loads(args.qualification.read_text(encoding="utf-8"))
    qualification_sha = digest(args.qualification)
    if (qualification.get("schema") != "orion.native-release-consolidation.v1"
            or qualification.get("passed") is not True or qualification.get("version") != "5.64.2"
            or Path(qualification["release_root"]).resolve() != source):
        raise ValueError("Only the accepted current native build may be published")
    if destination.exists():
        raise FileExistsError("Existing delivery is preserved")
    # Re-bind the accepted reports, JUnit files, runtime manifest and archive
    # to their current bytes before selecting anything for publication.
    repository = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(repository / "src"))
    from quantum_autopilot.windows_acceptance import verify_package
    spec = importlib.util.spec_from_file_location(
        "native_release_reader", repository / "tools/consolidate_native_release.py")
    reader = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(reader)
    selected = public_candidates(source)
    planned = qualification_candidates(
        source, qualification, selected, repository=repository,
        frozen_source_report=args.frozen_source_report.resolve() if args.frozen_source_report is not None else None,
        supplementary_tests=args.supplementary_tests.resolve() if args.supplementary_tests is not None else None)
    if digest(source / "OrionQ/OrionQ.exe") != qualification["executable_sha256"]:
        raise ValueError("Accepted executable changed before publication")
    for path, row in planned:
        if (not path.is_file() or path.stat().st_size != row["bytes"]
                or digest(path) != row["sha256"]):
            raise ValueError("Accepted evidence changed before publication: " + str(path))
    integrity = verify_package(source / "OrionQ")
    if integrity != qualification["package_integrity"]:
        raise ValueError("Accepted runtime package changed before publication")
    portable = reader.verify_portable(
        source, source / "OrionQ", source / "OrionQ/OrionQ.exe", integrity, version="5.64.2")
    if portable != qualification["portable_package"]:
        raise ValueError("Accepted portable archive changed before publication")
    before_hashes = {path: digest(path) for path in selected}
    rows = []
    for path in selected:
        if path.is_symlink() or not path.resolve(strict=True).is_relative_to(source):
            raise ValueError("Package/evidence must be ordinary contained files")
        relative = path.relative_to(source)
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        before = before_hashes[path]
        shutil.copyfile(path, target)
        shutil.copystat(path, target)
        after = digest(target)
        if before != after or before != digest(path):
            raise ValueError("Copy changed bytes: " + relative.as_posix())
        rows.append({"path": relative.as_posix(), "bytes": path.stat().st_size, "sha256": before})
    archive = destination / "OrionQ-5.64.2-native-windows-x64.zip"
    with ZipFile(archive) as bundle:
        if bundle.testzip() is not None:
            raise ValueError("Delivered ZIP CRC failure")
    if digest(args.qualification) != qualification_sha:
        raise ValueError("Qualification changed during publication")
    report = {"schema": "orion.native-package-relocation.v1", "passed": True,
              "source_build": str(source), "destination": str(destination),
              "qualification_sha256": qualification_sha,
              "native_reports_are_byte_preserved": True, "native_report_paths_rewritten": False,
              "private_runtime_copied": False, "zip_crc_passed": True,
              "public_dependency_ca_files": [row["path"] for row in rows
                                               if row["path"] == "OrionQ/_internal/certifi/cacert.pem"],
              "archive_sha256": digest(archive), "files": rows}
    (destination / "delivery-copy.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"passed": True, "files": len(rows), "archive_sha256": report["archive_sha256"]}))


if __name__ == "__main__":
    main()
