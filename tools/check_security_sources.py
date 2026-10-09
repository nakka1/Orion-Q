"""Detect embedded private-key material and credential assignments without printing values."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import subprocess


PRIVATE_KEY = re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----\s+[A-Za-z0-9+/=\r\n]{80,}")
CREDENTIAL = re.compile(r"(?im)^\s*(?:api[_-]?key|client[_-]?secret|access[_-]?token|password)\s*[:=]\s*['\"]"
                        r"([A-Za-z0-9_+/.=-]{32,})['\"]")


def scan(root):
    root = Path(root).resolve()
    result = subprocess.run(["rg", "--files", "--hidden", "-g", "!.git", "-g", "!.audit-winvenv",
        "-g", "!outputs", "-g", "!release", "-g", "!build", "-g", "!dist", "-g", "!archives",
        "-g", "!node_modules", "-g", "!*.lock", "-g", "!work", "-g", "!_to_delete"],
        cwd=root, capture_output=True, text=True, check=True)
    findings, count = [], 0
    for name in result.stdout.splitlines():
        path = root / name
        if path.suffix.lower() not in {".py", ".json", ".yaml", ".yml", ".toml", ".env", ".ps1", ".ts", ".tsx"}:
            continue
        if path.stat().st_size > 2097152:
            continue
        count += 1
        value = path.read_text(encoding="utf-8", errors="replace")
        for pattern, reason in ((PRIVATE_KEY, "embedded_private_key"), (CREDENTIAL, "credential_literal")):
            for match in pattern.finditer(value):
                findings.append({"file": name.replace("\\", "/"), "line": value[:match.start()].count("\n") + 1,
                                 "reason": reason})
    return {"schema": "orion.security.source-scan.v1", "passed": not findings,
            "files_scanned": count, "findings": findings,
            "scope": "Selected text sources/configuration; heuristic scan, not a certification or full history scan"}


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
