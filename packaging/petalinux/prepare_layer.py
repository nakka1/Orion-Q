"""Stage a reproducible Yocto layer using the existing GUI-free core profile.

No cross compiler, BSP, secrets or administrator state are supplied by this tool.
The source archive is input to python_setuptools_build_meta, not an ARM wheel.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import re
import shutil
import tarfile
import tempfile


ROOT = Path(__file__).resolve().parents[2]
RESOURCE_PATHS = (
    "docs/thesis/tcc_claim_registry.json",
    "evidence/tcc/v1.0.0/tables",
    "configs/qkd_assurance/irelandqci_example_operating_point.json",
    "src/quantum_autopilot/qkd/data/ireland_qci_grid_v1.json",
    "outputs/qkd_coexistence_measured/experimental_cells.csv",
)
PROVIDERS = {
    "cryptography": "python3-cryptography", "networkx": "python3-networkx",
    "numpy": "python3-numpy", "pandas": "python3-pandas", "pyyaml": "python3-pyyaml",
    "qiskit": "python3-qiskit", "qiskit-aer": "python3-qiskit-aer", "scipy": "python3-scipy",
    "sgp4": "python3-sgp4", "websockets": "python3-websockets",
}


def dependency_map(requirements):
    """Reject unsupported requirements instead of dropping them from the image."""
    result = []
    for requirement in requirements:
        match = re.fullmatch(r"([A-Za-z0-9][A-Za-z0-9_.-]*)([<>=!0-9.,]+)", requirement)
        if match is None:
            raise ValueError("embedded profile requires simple versioned dependencies")
        name, constraints = match.groups()
        name = name.lower().replace("_", "-").replace(".", "-")
        if name not in PROVIDERS:
            raise ValueError("embedded dependency has no reviewed recipe mapping")
        bounds = re.findall(r"(>=|<=|==|>|<)([0-9]+(?:\.[0-9]+)*)", constraints)
        if not bounds or ",".join(operator + version for operator, version in bounds) != constraints:
            raise ValueError("unsupported embedded version constraint")
        minimum = next((version for operator, version in bounds if operator in {">=", "=="}), None)
        result.append({"requirement": requirement, "provider": PROVIDERS[name],
                       "minimum": minimum, "bounds": bounds})
    return result


def _copy_public_file(source, target):
    if source.is_symlink() or not source.is_file() or source.suffix.lower() in {".pem", ".key", ".db", ".sqlite"}:
        raise ValueError("public runtime resources must be regular non-secret files")
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)


def _archive(source, output, prefix):
    with output.open("wb") as stream:
        with gzip.GzipFile(filename="", mode="wb", fileobj=stream, mtime=0) as compressed:
            with tarfile.open(fileobj=compressed, mode="w", format=tarfile.PAX_FORMAT) as archive:
                for item in sorted(source.rglob("*")):
                    if not item.is_file():
                        continue
                    if item.is_symlink():
                        raise ValueError("staging contains a symlink")
                    data = item.read_bytes()
                    info = tarfile.TarInfo(prefix + "/" + item.relative_to(source).as_posix())
                    info.size, info.mode, info.mtime = len(data), 0o644, 0
                    info.uid = info.gid = 0
                    archive.addfile(info, io.BytesIO(data))


def prepare(repository: Path, destination: Path) -> dict:
    repository, destination = repository.resolve(), destination.resolve()
    if destination.exists() or destination == repository or repository / "src" in destination.parents:
        raise ValueError("layer staging destination must be new and outside project source")
    if {".git", ".agents", ".codex", ".aws"}.intersection(destination.parts):
        raise ValueError("protected metadata is not a staging destination")
    spec = importlib.util.spec_from_file_location("orion_core_stage", repository / "packaging/core/prepare_source.py")
    if spec is None or spec.loader is None:
        raise ValueError("headless core staging tool unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with tempfile.TemporaryDirectory(prefix="orion-petalinux-stage-") as temporary:
        source = Path(temporary) / "source"
        core = module.prepare(repository, source)
        version = core["version"]
        if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", version) or core["distribution"] != "quantum-twin":
            raise ValueError("embedded artifact requires the reviewed product distribution and version")
        mapping = dependency_map(core["dependencies"])
        _copy_public_file(repository / "LICENSE", source / "LICENSE")
        for relative in RESOURCE_PATHS:
            original = repository / relative
            if original.is_symlink():
                raise ValueError("public resource directory must not be a symlink")
            files = sorted(original.rglob("*")) if original.is_dir() else [original]
            for item in files:
                if item.is_symlink():
                    raise ValueError("public resource directory contains a symlink")
                if item.is_file():
                    _copy_public_file(item, source / "resources" / item.relative_to(repository))
        if any(not (source / "resources" / relative).exists() for relative in RESOURCE_PATHS):
            raise ValueError("required public runtime resource unavailable")
        shutil.copytree(ROOT / "packaging/petalinux/meta-orionq", destination)
        recipe = destination / "recipes-orionq/orionq-core"
        archive = recipe / "files" / f"orionq-core-{version}.tar.gz"
        _archive(source, archive, f"orionq-core-{version}")
        sha = hashlib.sha256(archive.read_bytes()).hexdigest()
        license_md5 = hashlib.md5((source / "LICENSE").read_bytes()).hexdigest()
        requirements = " ".join(row["provider"] + (f" (>= {row['minimum']})" if row["minimum"] else "")
                                for row in mapping)
        (recipe / "orionq-artifact.inc").write_text(
            f'PV = "{version}"\nORIONQ_SOURCE_SHA256 = "{sha}"\n'
            f'ORIONQ_LICENSE_MD5 = "{license_md5}"\n'
            f'ORIONQ_PYTHON_RUNTIME_DEPENDS = "{requirements}"\n', encoding="utf-8")
        files = {item.relative_to(source).as_posix(): hashlib.sha256(item.read_bytes()).hexdigest()
                 for item in sorted(source.rglob("*")) if item.is_file()}
        report = {"schema": "orion.petalinux.staging.v1", "version": version,
                  "profile": "headless-service", "yocto_series": "scarthgap", "source_archive_sha256": sha,
                  "source_files": files, "source_bytes": sum(item.stat().st_size for item in source.rglob("*")
                                                             if item.is_file()),
                  "runtime_dependencies": mapping, "runtime_resource_paths": list(RESOURCE_PATHS),
                  "cross_compiled": False, "arm_image_built": False, "hardware_validated": False,
                  "services_auto_enabled": False, "build_contains_credentials": False}
        (destination / "orionq-staging.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    args = parser.parse_args(argv)
    report = prepare(args.repository, args.destination)
    print(json.dumps({"version": report["version"], "files": len(report["source_files"]),
                      "arm_image_built": False, "services_auto_enabled": False}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
