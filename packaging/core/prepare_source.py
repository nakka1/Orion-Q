"""Create a new GUI-free wheel staging tree without modifying the checkout.

The distribution name/version remain compatible with product_version(); this
deployment variant has its own manifest and dependency envelope. No secret,
runtime database, native desktop launcher or Qt package is copied into it.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import tomllib


EXCLUDED_PARTS = {"console", "__pycache__", ".git", ".security", "site-packages"}
EXCLUDED_SUFFIXES = {".pyc", ".pyo", ".pem", ".key", ".pfx", ".p12", ".sqlite", ".sqlite3", ".db"}


def prepare(repository: Path, destination: Path) -> dict:
    repository, destination = repository.resolve(), destination.resolve()
    if not (repository / "pyproject.toml").is_file() or not (repository / "src").is_dir():
        raise ValueError("project source required")
    if destination == repository or repository / "src" in destination.parents:
        raise ValueError("staging must not overwrite project source")
    if {".git", ".agents", ".codex", ".aws"}.intersection(destination.parts):
        raise ValueError("staging must not enter protected metadata directories")
    if destination.exists():
        raise ValueError("staging destination must be new")
    project = tomllib.loads((repository / "pyproject.toml").read_text(encoding="utf-8"))
    dependencies = [line.strip() for line in
                    (repository / "packaging/core/requirements.txt").read_text(encoding="utf-8").splitlines()
                    if line.strip() and not line.lstrip().startswith("#")]
    if any(line.startswith(("-", "http:", "https:", "git+")) for line in dependencies):
        raise ValueError("core dependency envelope must contain package requirements only")
    destination.mkdir(parents=True)
    copied = []
    for source in sorted((repository / "src").rglob("*")):
        relative = source.relative_to(repository / "src")
        if EXCLUDED_PARTS.intersection(relative.parts) or source.suffix.lower() in EXCLUDED_SUFFIXES:
            continue
        if relative == Path("quantum_autopilot/desktop.py"):
            continue
        if source.is_symlink():
            raise ValueError("symlinks are not permitted in the core source artifact")
        if not source.is_file():
            continue
        target = destination / "src" / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        copied.append(relative.as_posix())
    metadata = project["project"]
    text = (
        '[build-system]\nrequires = ["setuptools>=61.0", "wheel"]\nbuild-backend = "setuptools.build_meta"\n\n'
        '[project]\n'
        f'name = {json.dumps(metadata["name"])}\n'
        f'version = {json.dumps(metadata["version"])}\n'
        f'requires-python = {json.dumps(metadata["requires-python"])}\n'
        'description = "OrionQ headless control and acquisition service deployment profile"\n'
        f'dependencies = {json.dumps(dependencies)}\n\n'
        '[project.scripts]\norion-core = "quantum_autopilot.service.cli:main"\n'
        'orion-operator = "quantum_autopilot.service.operator_cli:main"\n'
        'orion-pl-gateway = "quantum_autopilot.service.pl_gateway_cli:main"\n\n'
        '[tool.setuptools.packages.find]\nwhere = ["src"]\n'
        'exclude = ["quantum_autopilot.console", "quantum_autopilot.console.*"]\n\n'
    )
    package_data = project.get("tool", {}).get("setuptools", {}).get("package-data", {})
    if package_data:
        text += '[tool.setuptools.package-data]\n'
        text += ''.join(f'{json.dumps(package)} = {json.dumps(patterns)}\n'
                        for package, patterns in sorted(package_data.items())
                        if not package.startswith("quantum_autopilot.console"))
    (destination / "pyproject.toml").write_text(text, encoding="utf-8")
    manifest = {"schema": "orion.core.artifact.v1", "distribution": metadata["name"],
                "version": metadata["version"], "profile": "headless-service",
                "excluded": ["quantum_autopilot.console", "quantum_autopilot.desktop"],
                "optional_absent": ["PySide6", "torch", "scikit-learn"],
                "dependencies": dependencies, "source_files": copied}
    (destination / "core-artifact.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    args = parser.parse_args(argv)
    manifest = prepare(args.repository, args.destination)
    print(json.dumps({"profile": manifest["profile"], "version": manifest["version"],
                      "source_files": len(manifest["source_files"])}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
