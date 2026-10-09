"""Machine-readable validation for the TCC scientific claim registry."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


CLAIM_STATUSES = {
    "supported_exploratory",
    "not_supported",
    "refuted_for_evaluated_models",
    "open_not_tested",
    "supported_simulation_only",
}


@dataclass
class ThesisClaimValidationResult:
    report: dict
    checks: pd.DataFrame


def load_claim_registry(path: str | Path) -> dict:
    registry_path = Path(path)
    payload = json.loads(registry_path.read_text(encoding="utf-8"))
    validate_claim_registry_schema(payload)
    return payload


def validate_claim_registry_schema(payload: dict) -> None:
    required_root = {
        "schema_version", "formalized_at", "formalization_status",
        "headline_claim", "claims",
    }
    missing = sorted(required_root.difference(payload))
    if missing:
        raise ValueError(f"claim registry is missing root fields: {missing}")
    if not isinstance(payload["claims"], list) or not payload["claims"]:
        raise ValueError("claim registry must contain at least one claim")
    required_claim = {
        "id", "role", "statement", "status", "estimand", "evidence",
        "boundaries", "permitted_language", "forbidden_language",
        "product_behavior",
    }
    identifiers: list[str] = []
    for claim in payload["claims"]:
        missing_claim = sorted(required_claim.difference(claim))
        if missing_claim:
            raise ValueError(
                f"claim {claim.get('id', '<unknown>')} is missing fields: {missing_claim}"
            )
        identifiers.append(str(claim["id"]))
        if claim["status"] not in CLAIM_STATUSES:
            raise ValueError(
                f"claim {claim['id']} has unknown status {claim['status']}"
            )
        for field in ("boundaries", "permitted_language", "forbidden_language"):
            if not isinstance(claim[field], list) or not claim[field]:
                raise ValueError(f"claim {claim['id']} field {field} cannot be empty")
        if not isinstance(claim["evidence"], list):
            raise ValueError(f"claim {claim['id']} evidence must be a list")
    if len(set(identifiers)) != len(identifiers):
        raise ValueError("claim identifiers must be unique")
    headline_id = str(payload["headline_claim"].get("claim_id", ""))
    if headline_id not in identifiers:
        raise ValueError("headline_claim.claim_id must reference a registered claim")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _select_rows(frame: pd.DataFrame, selector: dict[str, Any]) -> pd.DataFrame:
    selected = frame
    for field, expected in selector.items():
        if field not in selected.columns:
            raise ValueError(f"selector field {field} is absent")
        if isinstance(expected, bool):
            mask = selected[field].astype(str).str.lower().eq(str(expected).lower())
        elif isinstance(expected, (int, float)) and not isinstance(expected, bool):
            numeric = pd.to_numeric(selected[field], errors="coerce")
            mask = np.isclose(numeric, float(expected), rtol=0.0, atol=1e-12)
        else:
            mask = selected[field].astype(str).eq(str(expected))
        selected = selected.loc[mask]
    return selected


def _assertion_result(actual: Any, assertion: dict) -> tuple[bool, str]:
    operator = str(assertion["operator"])
    expected = assertion["expected"]
    if operator == "equals":
        if isinstance(expected, bool):
            observed = str(actual).lower() == "true"
            return observed == expected, str(observed)
        return actual == expected, str(actual)
    numeric = float(actual)
    expected_numeric = float(expected)
    if operator == "approx":
        tolerance = float(assertion.get("absolute_tolerance", 1e-9))
        return abs(numeric - expected_numeric) <= tolerance, repr(numeric)
    if operator == "greater_than":
        return numeric > expected_numeric, repr(numeric)
    if operator == "less_than":
        return numeric < expected_numeric, repr(numeric)
    raise ValueError(f"unknown assertion operator: {operator}")


def validate_claim_registry(
    registry_path: str | Path,
    *,
    repository_root: str | Path | None = None,
) -> ThesisClaimValidationResult:
    path = Path(registry_path).resolve()
    registry = load_claim_registry(path)
    root = (
        Path(repository_root).resolve()
        if repository_root is not None
        else path.parents[2]
    )
    records: list[dict] = []
    for claim in registry["claims"]:
        for evidence in claim["evidence"]:
            evidence_id = str(evidence["id"])
            relative = Path(str(evidence["artifact"]))
            artifact = (root / relative).resolve()
            try:
                artifact.relative_to(root)
            except ValueError as error:
                raise ValueError(
                    f"evidence artifact escapes repository root: {relative}"
                ) from error
            expected_hash = str(evidence["sha256"]).lower()
            exists = artifact.is_file()
            observed_hash = _sha256(artifact) if exists else ""
            hash_valid = exists and observed_hash == expected_hash
            records.append({
                "claim_id": claim["id"],
                "claim_status": claim["status"],
                "evidence_id": evidence_id,
                "artifact": relative.as_posix(),
                "check": "sha256",
                "observed": observed_hash if exists else "missing",
                "expected": expected_hash,
                "valid": hash_valid,
                "detail": "artifact integrity",
            })
            if not hash_valid:
                continue
            if artifact.suffix.lower() != ".csv":
                raise ValueError(f"unsupported evidence artifact type: {artifact.suffix}")
            frame = pd.read_csv(artifact)
            selected = _select_rows(frame, evidence.get("selector", {}))
            expected_rows = int(evidence.get("expected_rows", 1))
            row_count_valid = len(selected) == expected_rows
            records.append({
                "claim_id": claim["id"],
                "claim_status": claim["status"],
                "evidence_id": evidence_id,
                "artifact": relative.as_posix(),
                "check": "selected_rows",
                "observed": str(len(selected)),
                "expected": str(expected_rows),
                "valid": row_count_valid,
                "detail": json.dumps(evidence.get("selector", {}), ensure_ascii=False),
            })
            if not row_count_valid:
                continue
            row = selected.iloc[0]
            for assertion in evidence.get("assertions", []):
                field = str(assertion["field"])
                if field not in row.index:
                    valid, observed = False, "missing field"
                else:
                    valid, observed = _assertion_result(row[field], assertion)
                records.append({
                    "claim_id": claim["id"],
                    "claim_status": claim["status"],
                    "evidence_id": evidence_id,
                    "artifact": relative.as_posix(),
                    "check": field,
                    "observed": observed,
                    "expected": json.dumps(
                        assertion["expected"], ensure_ascii=False
                    ),
                    "valid": valid,
                    "detail": assertion["operator"],
                })
    checks = pd.DataFrame.from_records(records)
    failed = int((~checks["valid"]).sum()) if len(checks) else 0
    report = {
        "study": "TCC formal claim registry validation",
        "schema_version": registry["schema_version"],
        "registry": path.relative_to(root).as_posix(),
        "registry_sha256": _sha256(path),
        "headline_claim_id": registry["headline_claim"]["claim_id"],
        "claim_count": len(registry["claims"]),
        "evidence_item_count": sum(len(claim["evidence"]) for claim in registry["claims"]),
        "check_count": int(len(checks)),
        "failed_check_count": failed,
        "valid": failed == 0,
        "status_counts": pd.Series(
            [claim["status"] for claim in registry["claims"]]
        ).value_counts().sort_index().to_dict(),
    }
    return ThesisClaimValidationResult(report=report, checks=checks)


def write_claim_validation_artifacts(
    result: ThesisClaimValidationResult,
    output_dir: str | Path,
) -> dict:
    directory = Path(output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    checks_path = directory / "claim_evidence_checks.csv"
    report_path = directory / "report.json"
    result.checks.to_csv(checks_path, index=False)
    report_path.write_text(
        json.dumps(result.report, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    manifest = {
        "valid": result.report["valid"],
        "artifacts": [
            {
                "file": artifact.name,
                "sha256": _sha256(artifact),
                "bytes": artifact.stat().st_size,
            }
            for artifact in (checks_path, report_path)
        ],
    }
    (directory / "artifact_manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return manifest


__all__ = [
    "CLAIM_STATUSES",
    "ThesisClaimValidationResult",
    "load_claim_registry",
    "validate_claim_registry",
    "validate_claim_registry_schema",
    "write_claim_validation_artifacts",
]
