"""Detect embedded private-key material and credential assignments without printing values."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re


PRIVATE_KEY = re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----\s+[A-Za-z0-9+/=\r\n]{80,}")
CREDENTIAL = re.compile(r"(?im)^\s*(?:api[_-]?key|client[_-]?secret|access[_-]?token|password)\s*[:=]\s*['\"]"
                        r"([A-Za-z0-9_+/.=-]{32,})['\"]")


# Explicit generated-directory exclusions keep the same scan usable in a Git
# checkout or a source ZIP. Do not use .gitignore to hide source/configuration.
EXCLUDED_DIRECTORIES = frozenset({
    ".git", ".audit-winvenv", "outputs", "release", "build", "dist", "archives",
    "node_modules", "work", "_to_delete", ".venv", "venv", "__pycache__",
    ".pytest_cache", ".mypy_cache", ".ruff_cache",
})
SOURCE_SUFFIXES = frozenset({".py", ".json", ".yaml", ".yml", ".toml", ".env", ".ps1", ".ts", ".tsx"})
MAX_SOURCE_BYTES = 2 * 1024 * 1024


def _linked(path):
    # Windows junctions also need pruning; is_junction is available in 3.12+.
    return path.is_symlink() or getattr(path, "is_junction", lambda: False)()


def _source_paths(root):
    def unreadable(error):
        raise error  # An incomplete enumeration must not produce a green report.

    for directory, directories, files in os.walk(root, followlinks=False, onerror=unreadable):
        parent = Path(directory)
        directories[:] = sorted(name for name in directories
                                if name not in EXCLUDED_DIRECTORIES
                                and not name.startswith((".audit-", ".sites-", ".pytest_"))
                                and not _linked(parent / name))
        for name in sorted(files):
            path = parent / name
            if _linked(path) or (path.suffix.lower() not in SOURCE_SUFFIXES and name != ".env"):
                continue
            if path.stat().st_size <= MAX_SOURCE_BYTES:
                yield path


def scan(root):
    root = Path(root).resolve()
    if not root.exists():
        raise FileNotFoundError("Source scan root does not exist")
    if not root.is_dir():
        raise NotADirectoryError("Source scan root must be a directory")
    findings, count = [], 0
    for path in _source_paths(root):
        name = path.relative_to(root).as_posix()
        count += 1
        value = path.read_text(encoding="utf-8", errors="replace")
        for pattern, reason in ((PRIVATE_KEY, "embedded_private_key"), (CREDENTIAL, "credential_literal")):
            for match in pattern.finditer(value):
                findings.append({"file": name.replace("\\", "/"), "line": value[:match.start()].count("\n") + 1,
                                 "reason": reason})
    return {"schema": "orion.security.source-scan.v1", "passed": not findings,
            "files_scanned": count, "findings": findings,
            "scope": "Selected text sources/configuration with explicit directory exclusions; .gitignore is not applied; "
                     "heuristic scan, not a certification or full history scan"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = scan(args.root)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, sort_keys=True, indent=2), encoding="utf-8")
    print(json.dumps({"passed": result["passed"], "files_scanned": result["files_scanned"],
                      "finding_count": len(result["findings"])}))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
