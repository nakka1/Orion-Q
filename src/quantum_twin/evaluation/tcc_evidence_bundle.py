"""Build a portable, license-aware evidence bundle for the TCC claim registry.

The experimental outputs are intentionally ignored by Git.  This module extracts
only the aggregate rows referenced by the formal claim registry, records their
provenance and licences, and rewrites the registry to reference tracked files.
It never copies raw measurements or fitted model weights.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
from typing import Any

import numpy as np
import pandas as pd

from quantum_twin.evaluation.thesis_claims import (
    load_claim_registry,
    validate_claim_registry,
)


IRELAND_QCI_SOURCE = {
    "name": "IrelandQCI/Toshiba measured QKD coexistence data",
    "doi": "10.5281/zenodo.21132088",
    "license": "CC-BY-4.0",
    "attribution_required": True,
    "reuse_boundary": "Derived aggregate rows with source attribution.",
}

BRISTOL_HCF_SOURCE = {
    "name": "Bristol HCF measured-QKD coexistence data",
    "doi": "10.5523/bris.61193flfovbn2puknndsr3qm5",
    "license": "Non-Commercial Government Licence",
    "attribution_required": True,
    "reuse_boundary": (
        "Derived aggregate rows for academic, research and non-commercial use; "
        "review the source licence before any broader redistribution or use."
    ),
}


_SOURCE_POLICIES = {
    "outputs/qkd_coexistence_measured/": [IRELAND_QCI_SOURCE],
    "outputs/qkd-hierarchical-robustness/": [IRELAND_QCI_SOURCE],
    "outputs/classical-quantum-pareto/": [IRELAND_QCI_SOURCE],
    "outputs/qkd-pareto-falsification/": [IRELAND_QCI_SOURCE],
    "outputs/qkd-dose-order-falsification/": [IRELAND_QCI_SOURCE],
    "outputs/cross-platform-qkd-replication/": [
        IRELAND_QCI_SOURCE,
        BRISTOL_HCF_SOURCE,
    ],
}


_BUNDLE_README = """# Pacote portável de evidências do TCC

Este diretório contém somente as linhas agregadas citadas pelo registro formal
de reivindicações. Ele não contém medições brutas, linhas individuais nem pesos
de modelos. `bundle_manifest.json` fixa os hashes e limites do conteúdo;
`provenance.json` liga cada tabela compacta ao artefato experimental de origem,
à fonte pública e à licença aplicável.

O arquivo `portable_claim_registry.json` é uma cópia auditável do registro
publicado em `docs/thesis/tcc_claim_registry.json`. O arquivo
`generation_registry.json` conserva as referências aos resultados completos em
`outputs/` e serve apenas para reconstruir o pacote quando esses resultados
locais estiverem disponíveis.

Fontes:

- IrelandQCI/Toshiba, DOI 10.5281/zenodo.21132088, CC BY 4.0;
- Bristol HCF, DOI 10.5523/bris.61193flfovbn2puknndsr3qm5,
  Non-Commercial Government Licence.

O componente Bristol deste pacote destina-se ao TCC e a uso acadêmico, de
pesquisa e não comercial. A licença da fonte deve ser reavaliada antes de
redistribuição ou uso mais amplo.
"""


@dataclass(frozen=True)
class TCCEvidenceBundleResult:
    """Summary of a completed portable evidence-bundle build."""

    bundle_dir: Path
    portable_registry_path: Path
    manifest_path: Path
    table_count: int
    selected_row_count: int
    evidence_item_count: int
    validation_report: dict[str, Any]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def _inside_root(path: Path, root: Path, *, label: str) -> Path:
    resolved = path.resolve()
    try:
        return resolved.relative_to(root)
    except ValueError as error:
        raise ValueError(f"{label} escapes repository root: {path}") from error


def _resolve_source_registry(path: Path, root: Path) -> tuple[Path, dict[str, Any]]:
    """Resolve the immutable generation registry when given a portable one."""

    payload = load_claim_registry(path)
    metadata = payload.get("evidence_bundle")
    if not isinstance(metadata, dict) or not metadata.get("generation_registry"):
        return path, payload
    generation_path = (root / str(metadata["generation_registry"])).resolve()
    _inside_root(generation_path, root, label="generation registry")
    return generation_path, load_claim_registry(generation_path)


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


def _source_policy(relative_artifact: str) -> list[dict[str, Any]]:
    normalized = Path(relative_artifact).as_posix()
    for prefix, policies in _SOURCE_POLICIES.items():
        if normalized.startswith(prefix):
            return deepcopy(policies)
    raise ValueError(
        "no explicit dataset licence policy is registered for evidence artifact: "
        f"{normalized}"
    )


def _table_filename(relative_artifact: str) -> str:
    path = Path(relative_artifact)
    parent = path.parent.name or "root"
    stem = path.stem
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "-", f"{parent}__{stem}")
    return f"{safe}.csv"


def build_tcc_evidence_bundle(
    source_registry_path: str | Path,
    *,
    repository_root: str | Path,
    bundle_dir: str | Path = "evidence/tcc/v1.0.0",
    portable_registry_path: str | Path = "docs/thesis/tcc_claim_registry.json",
    bundle_version: str = "1.0.0",
    generated_at: str = "2026-09-03",
) -> TCCEvidenceBundleResult:
    """Extract, write and validate the portable evidence bundle.

    The source registry must validate against the full local experiment outputs.
    When ``source_registry_path`` already points to a portable registry, its
    recorded immutable generation registry is used automatically.
    """

    root = Path(repository_root).resolve()
    requested_source = Path(source_registry_path)
    if not requested_source.is_absolute():
        requested_source = root / requested_source
    requested_relative = _inside_root(
        requested_source, root, label="source registry"
    ).as_posix()
    resolved_source, source_registry = _resolve_source_registry(
        requested_source.resolve(), root
    )
    resolved_source_relative = _inside_root(
        resolved_source, root, label="resolved source registry"
    ).as_posix()

    source_validation = validate_claim_registry(
        resolved_source, repository_root=root
    )
    if not source_validation.report["valid"]:
        raise ValueError(
            "source claim registry failed evidence validation; refusing to bundle"
        )
    source_registry_hash = _sha256(resolved_source)

    target_bundle = Path(bundle_dir)
    if not target_bundle.is_absolute():
        target_bundle = root / target_bundle
    bundle_relative = _inside_root(
        target_bundle, root, label="bundle directory"
    )
    if bundle_relative.parts and bundle_relative.parts[0].lower() == "outputs":
        raise ValueError("portable evidence bundle cannot be written under outputs/")

    target_registry = Path(portable_registry_path)
    if not target_registry.is_absolute():
        target_registry = root / target_registry
    target_registry_relative = _inside_root(
        target_registry, root, label="portable registry"
    ).as_posix()

    generation_registry_path = target_bundle / "generation_registry.json"
    portable_copy_path = target_bundle / "portable_claim_registry.json"
    provenance_path = target_bundle / "provenance.json"
    manifest_path = target_bundle / "bundle_manifest.json"
    readme_path = target_bundle / "README.md"
    tables_dir = target_bundle / "tables"
    tables_dir.mkdir(parents=True, exist_ok=True)

    # Preserve the complete output-referencing registry needed for a future
    # rebuild before replacing the public registry with its portable form.
    _write_json(generation_registry_path, source_registry)
    readme_path.write_text(_BUNDLE_README, encoding="utf-8")

    references: dict[str, list[tuple[str, dict[str, Any]]]] = {}
    for claim in source_registry["claims"]:
        for evidence in claim["evidence"]:
            artifact = Path(str(evidence["artifact"])).as_posix()
            references.setdefault(artifact, []).append((str(evidence["id"]), evidence))

    portable_registry = deepcopy(source_registry)
    artifact_replacements: dict[str, dict[str, str]] = {}
    provenance_entries: list[dict[str, Any]] = []
    filenames: set[str] = set()
    selected_row_count = 0

    for relative_artifact in sorted(references):
        source_artifact = (root / relative_artifact).resolve()
        _inside_root(source_artifact, root, label="source evidence artifact")
        frame = pd.read_csv(source_artifact)
        selected_parts: list[pd.DataFrame] = []
        evidence_ids: list[str] = []
        for evidence_id, evidence in references[relative_artifact]:
            selected = _select_rows(frame, evidence.get("selector", {}))
            expected_rows = int(evidence.get("expected_rows", 1))
            if len(selected) != expected_rows:
                raise ValueError(
                    f"evidence {evidence_id} selected {len(selected)} rows; "
                    f"expected {expected_rows}"
                )
            selected_parts.append(selected)
            evidence_ids.append(evidence_id)

        compact = pd.concat(selected_parts, ignore_index=True).drop_duplicates(
            ignore_index=True
        )
        filename = _table_filename(relative_artifact)
        if filename in filenames:
            raise ValueError(f"portable evidence table filename collision: {filename}")
        filenames.add(filename)
        compact_path = tables_dir / filename
        compact.to_csv(compact_path, index=False, lineterminator="\n")
        compact_relative = compact_path.relative_to(root).as_posix()
        compact_hash = _sha256(compact_path)
        selected_row_count += len(compact)
        artifact_replacements[relative_artifact] = {
            "artifact": compact_relative,
            "sha256": compact_hash,
        }
        provenance_entries.append({
            "source_artifact": relative_artifact,
            "source_sha256": _sha256(source_artifact),
            "portable_artifact": compact_relative,
            "portable_sha256": compact_hash,
            "portable_rows": int(len(compact)),
            "evidence_items": sorted(evidence_ids),
            "source_datasets": _source_policy(relative_artifact),
        })

    for claim in portable_registry["claims"]:
        for evidence in claim["evidence"]:
            original = Path(str(evidence["artifact"])).as_posix()
            evidence.update(artifact_replacements[original])

    generation_registry_relative = generation_registry_path.relative_to(root).as_posix()
    manifest_relative = manifest_path.relative_to(root).as_posix()
    portable_copy_relative = portable_copy_path.relative_to(root).as_posix()
    portable_registry["schema_version"] = "1.3.0"
    portable_registry["evidence_bundle"] = {
        "bundle_version": bundle_version,
        "generated_at": generated_at,
        "portable": True,
        "bundle_directory": bundle_relative.as_posix(),
        "manifest": manifest_relative,
        "generation_registry": generation_registry_relative,
        "portable_registry_copy": portable_copy_relative,
    }
    _write_json(portable_copy_path, portable_registry)
    _write_json(target_registry, portable_registry)

    portable_validation = validate_claim_registry(
        target_registry, repository_root=root
    )
    if not portable_validation.report["valid"]:
        raise RuntimeError("generated portable claim registry failed validation")

    provenance = {
        "schema_version": "1.0.0",
        "bundle_version": bundle_version,
        "generated_at": generated_at,
        "requested_source_registry": requested_relative,
        "resolved_source_registry": resolved_source_relative,
        "source_registry_sha256": source_registry_hash,
        "generation_registry_snapshot": {
            "file": generation_registry_relative,
            "sha256": _sha256(generation_registry_path),
        },
        "portable_registry": target_registry_relative,
        "portable_registry_sha256": _sha256(target_registry),
        "derivation": (
            "Exact selector-matched aggregate evidence rows extracted from "
            "validated experimental result tables; duplicate rows removed."
        ),
        "artifacts": provenance_entries,
    }
    _write_json(provenance_path, provenance)

    tracked_files = [
        generation_registry_path,
        portable_copy_path,
        provenance_path,
        readme_path,
        *(tables_dir / name for name in sorted(filenames)),
    ]
    manifest = {
        "schema_version": "1.0.0",
        "bundle_version": bundle_version,
        "generated_at": generated_at,
        "purpose": "Portable evidence for the formal TCC scientific claims.",
        "content_boundaries": {
            "contains_only_selector_matched_aggregate_rows": True,
            "contains_raw_measurements": False,
            "contains_model_weights": False,
            "contains_individual_level_rows": False,
            "intended_use": "Academic, research and non-commercial TCC assurance.",
            "bristol_non_commercial_restriction_applies": True,
        },
        "counts": {
            "claims": len(portable_registry["claims"]),
            "evidence_items": sum(
                len(claim["evidence"]) for claim in portable_registry["claims"]
            ),
            "tables": len(filenames),
            "selected_unique_rows": selected_row_count,
        },
        "published_registry": {
            "file": target_registry_relative,
            "sha256": _sha256(target_registry),
        },
        "files": [
            {
                "file": path.relative_to(target_bundle).as_posix(),
                "sha256": _sha256(path),
                "bytes": path.stat().st_size,
            }
            for path in sorted(tracked_files, key=lambda item: item.as_posix())
        ],
    }
    _write_json(manifest_path, manifest)

    return TCCEvidenceBundleResult(
        bundle_dir=target_bundle,
        portable_registry_path=target_registry,
        manifest_path=manifest_path,
        table_count=len(filenames),
        selected_row_count=selected_row_count,
        evidence_item_count=manifest["counts"]["evidence_items"],
        validation_report=portable_validation.report,
    )


__all__ = [
    "BRISTOL_HCF_SOURCE",
    "IRELAND_QCI_SOURCE",
    "TCCEvidenceBundleResult",
    "build_tcc_evidence_bundle",
]
