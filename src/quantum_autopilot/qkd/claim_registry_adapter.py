"""Read-only bridge from the formal TCC claim registry to decision records."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from quantum_autopilot.core import (
    CandidatePlan,
    ConstraintViolation,
    DecisionRecord,
    EvidenceKind,
    GenericThresholdPolicy,
    Provenance,
    SystemSnapshot,
    Workload,
    make_audited_decision,
)
from quantum_autopilot.qkd.operating_point import (
    CONDITION_SCOPES,
    GRID_CONFIG_RELATIVE_PATH,
    IrelandQCIGridClassifier,
    QKDOperatingPoint,
)
from quantum_twin.evaluation.thesis_claims import (
    load_claim_registry,
    validate_claim_registry,
)


REQUESTED_USES = frozenset({"display", "decision_support", "automation"})
ACTION_BY_USE = {
    "display": "DISPLAY_EVIDENCE",
    "decision_support": "PROVIDE_DECISION_SUPPORT",
    "automation": "AUTOMATE_CONTROL",
}
MODEL_VERSION = "qkd-claim-guard-v2"

# The registry predates the product adapter and does not contain an explicit
# evidence-kind field. This project-specific mapping is deliberately narrow
# and auditable; unknown claims never inherit a measured label.
CLAIM_EVIDENCE_KINDS = {
    "C-MEASURED": EvidenceKind.MEASURED,
    "L-SESSION": EvidenceKind.MEASURED,
    "L-DOSE": EvidenceKind.MEASURED,
    "L-PARETO": EvidenceKind.MEASURED,
    "L-TRANSPORT": EvidenceKind.MEASURED,
    "B-FIDELITY": EvidenceKind.SIMULATED,
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_assurance_workload(
    claim_id: str,
    *,
    requested_use: str = "display",
    operating_point: QKDOperatingPoint | Mapping[str, Any] | None = None,
    workload_id: str = "",
) -> Workload:
    """Build and validate a request consumed by :class:`ClaimRegistryAdapter`."""

    if not isinstance(claim_id, str) or not claim_id.strip():
        raise ValueError("claim_id cannot be empty")
    if requested_use not in REQUESTED_USES:
        raise ValueError(f"requested_use must be one of {sorted(REQUESTED_USES)}")
    if isinstance(operating_point, Mapping):
        operating_point = QKDOperatingPoint.from_dict(operating_point)
    if operating_point is not None and not isinstance(operating_point, QKDOperatingPoint):
        raise ValueError("operating_point must be a QKDOperatingPoint, mapping, or null")
    return Workload(
        kind="qkd_claim_assurance",
        workload_id=workload_id,
        payload={
            "claim_id": claim_id,
            "requested_use": requested_use,
            "operating_point": operating_point.to_dict() if operating_point else None,
        },
    )


class ClaimRegistryAdapter:
    """Validate and expose the shipped claim registry without modifying it.

    The adapter has no executor method. It can display evidence or recommend
    decision support only when the registered status, provenance, and
    operating-grid scope permit that use. Automation is controlled solely by
    the registry's explicit ``product_behavior.automation_allowed`` flag.
    """

    def __init__(
        self,
        repository_root: str | Path | None = None,
        registry_path: str | Path | None = None,
        grid_path: str | Path | None = None,
    ) -> None:
        default_root = Path(__file__).resolve().parents[3]
        self.repository_root = Path(repository_root or default_root).resolve()
        requested_path = Path(registry_path or "docs/thesis/tcc_claim_registry.json")
        self.registry_path = (
            requested_path.resolve()
            if requested_path.is_absolute()
            else (self.repository_root / requested_path).resolve()
        )
        try:
            self.registry_path.relative_to(self.repository_root)
        except ValueError as exc:
            raise ValueError("registry_path must remain inside repository_root") from exc
        self.grid_classifier = IrelandQCIGridClassifier(
            repository_root=self.repository_root,
            grid_path=grid_path or GRID_CONFIG_RELATIVE_PATH,
        )

    @staticmethod
    def _request(workload: Workload) -> tuple[str, str, QKDOperatingPoint | None]:
        if workload.kind != "qkd_claim_assurance":
            raise ValueError("workload.kind must be 'qkd_claim_assurance'")
        claim_id = workload.payload.get("claim_id")
        requested_use = workload.payload.get("requested_use")
        operating_point = workload.payload.get("operating_point")
        validated = build_assurance_workload(
            claim_id,
            requested_use=requested_use,
            operating_point=operating_point,
            workload_id=workload.workload_id,
        )
        validated_point = validated.payload["operating_point"]
        return (
            validated.payload["claim_id"],
            validated.payload["requested_use"],
            QKDOperatingPoint.from_dict(validated_point) if validated_point else None,
        )

    def observe(self, workload: Workload) -> SystemSnapshot:
        """Read and validate the registry and all referenced evidence hashes."""

        claim_id, requested_use, operating_point = self._request(workload)
        registry: dict[str, Any] | None = None
        validation_report: dict[str, Any] = {}
        validation_error: dict[str, str] | None = None
        try:
            registry = load_claim_registry(self.registry_path)
            validation = validate_claim_registry(
                self.registry_path,
                repository_root=self.repository_root,
            )
            validation_report = dict(validation.report)
            registry_valid = bool(validation.report["valid"])
        except Exception as exc:
            registry_valid = False
            validation_error = {"type": type(exc).__name__, "message": str(exc)}

        claim = None
        if registry is not None:
            claim = next((item for item in registry["claims"] if item["id"] == claim_id), None)
        evidence_kind = CLAIM_EVIDENCE_KINDS.get(claim_id)
        grid_classification = self.grid_classifier.classify_optional(operating_point)
        observed = {
            "read_only": True,
            "registry_path": self.registry_path.relative_to(self.repository_root).as_posix(),
            "registry_valid": registry_valid,
            "validation_report": validation_report,
            "validation_error": validation_error,
            "claim_id": claim_id,
            "claim_exists": claim is not None,
            "claim": claim,
            "claim_evidence_kind": evidence_kind.value if evidence_kind else None,
            "requested_use": requested_use,
            "operating_point": operating_point.to_dict() if operating_point else None,
            "condition_scope": grid_classification.scope,
            "grid_classification": grid_classification.to_dict(),
            "headline_claim": registry.get("headline_claim") if registry else None,
            "original_hypothesis_disposition": (
                registry.get("original_hypothesis_disposition") if registry else None
            ),
        }
        return SystemSnapshot(
            domain="qkd_claim_assurance",
            observed=observed,
            source=observed["registry_path"],
        )

    @staticmethod
    def _gate_reasons(snapshot: SystemSnapshot) -> list[dict[str, Any]]:
        observed = snapshot.observed
        claim = observed.get("claim")
        requested_use = observed["requested_use"]
        reasons: list[dict[str, Any]] = []

        if not observed.get("registry_valid"):
            reasons.append(
                {
                    "code": "claim_registry_integrity_failed",
                    "constraint": "registry_valid",
                    "required": True,
                    "actual": False,
                    "detail": "registry schema, evidence hashes, or evidence assertions failed",
                }
            )
        if claim is None:
            reasons.append(
                {
                    "code": "claim_not_registered",
                    "constraint": "claim_exists",
                    "required": True,
                    "actual": False,
                    "detail": "requested claim identifier is absent from the formal registry",
                }
            )
            return reasons

        if requested_use == "display":
            return reasons

        evidence_kind = observed.get("claim_evidence_kind")
        if evidence_kind != EvidenceKind.MEASURED.value:
            reasons.append(
                {
                    "code": (
                        "simulation_only_evidence"
                        if evidence_kind == EvidenceKind.SIMULATED.value
                        else "measured_evidence_absent"
                    ),
                    "constraint": "claim_evidence_kind",
                    "required": EvidenceKind.MEASURED.value,
                    "actual": evidence_kind,
                    "detail": "decision support requires a measured quantum target",
                }
            )
        if claim["status"] != "supported_exploratory":
            reasons.append(
                {
                    "code": "claim_status_not_supported_for_decision",
                    "constraint": "claim_status",
                    "required": "supported_exploratory",
                    "actual": claim["status"],
                    "detail": "negative, open, or simulation-only claims remain visible but cannot recommend",
                }
            )

        condition_scope = observed["condition_scope"]
        if condition_scope != "inside_validated_grid":
            reasons.append(
                {
                    "code": (
                        "condition_outside_validated_grid"
                        if condition_scope == "outside_validated_grid"
                        else "condition_scope_not_verified"
                    ),
                    "constraint": "condition_scope",
                    "required": "inside_validated_grid",
                    "actual": condition_scope,
                    "detail": "current evidence does not support transport beyond the measured grid",
                }
            )
            reasons.extend(observed["grid_classification"].get("violations", []))

        if requested_use == "automation" and not bool(
            claim["product_behavior"].get("automation_allowed", False)
        ):
            reasons.append(
                {
                    "code": "automation_not_authorized_by_claim_registry",
                    "constraint": "automation_allowed",
                    "required": True,
                    "actual": False,
                    "detail": "the formal product behavior permits display/support only",
                }
            )
        return reasons

    def propose_plans(
        self,
        snapshot: SystemSnapshot,
        workload: Workload,
    ) -> list[CandidatePlan]:
        """Create one requested-use plan and attach its evidence gate."""

        _, requested_use, _ = self._request(workload)
        reasons = self._gate_reasons(snapshot)
        claim = snapshot.observed.get("claim") or {}
        return [
            CandidatePlan(
                action=ACTION_BY_USE[requested_use],
                estimated_cost=0.0,
                extra={
                    "claim_id": snapshot.observed["claim_id"],
                    "claim_status": claim.get("status"),
                    "badge": (claim.get("product_behavior") or {}).get("badge"),
                    "message": (claim.get("product_behavior") or {}).get("message"),
                    "requested_use": requested_use,
                    "condition_scope": snapshot.observed["condition_scope"],
                    "grid_classification": snapshot.observed["grid_classification"],
                    "claim_gate": {"allowed": not reasons, "violations": reasons},
                },
            )
        ]

    @staticmethod
    def candidate_gate(plan: CandidatePlan) -> list[ConstraintViolation]:
        """Translate the adapter's explicit claim gate into core violations."""

        gate = plan.extra.get("claim_gate") or {}
        return [
            ConstraintViolation(
                constraint=item["constraint"],
                reason_code=item["code"],
                required=item.get("required"),
                actual=item.get("actual"),
                detail=item.get("detail", ""),
            )
            for item in gate.get("violations", [])
        ]

    def assess(self, workload: Workload) -> DecisionRecord:
        """Return a self-contained, read-only assurance decision record."""

        snapshot = self.observe(workload)
        plans = self.propose_plans(snapshot, workload)
        claim = snapshot.observed.get("claim") or {}
        evidence_hashes = {
            str(item["id"]): str(item["sha256"])
            for item in claim.get("evidence", [])
            if item.get("id") and item.get("sha256")
        }
        registry_hash = _sha256(self.registry_path) if self.registry_path.is_file() else ""
        grid_classification = snapshot.observed["grid_classification"]
        grid_hash = str(grid_classification.get("grid_config_sha256", ""))
        if grid_hash:
            evidence_hashes["irelandqci-operating-grid-config"] = grid_hash
        matched_cell = grid_classification.get("matched_cell") or {}
        if matched_cell.get("source_file_sha256"):
            evidence_hashes["operating-point-campaign-source"] = str(
                matched_cell["source_file_sha256"]
            )
        point_payload = snapshot.observed.get("operating_point")
        point_provenance = (point_payload or {}).get("provenance") or {}
        config_hash = hashlib.sha256(
            json.dumps(
                {"grid_config_sha256": grid_hash, "registry_sha256": registry_hash},
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        provenance = Provenance(
            evidence_kind=EvidenceKind.SYNTHESIZED,
            source=snapshot.source,
            config_hash=config_hash,
            dataset_hash=str(point_provenance.get("dataset_hash", "")),
            artifact_hashes=evidence_hashes,
            metadata={
                "read_only": True,
                "registry_sha256": registry_hash,
                "grid_config_sha256": grid_hash,
                "grid_source_artifact_verified": grid_classification.get(
                    "source_artifact_verified", False
                ),
                "claim_id": snapshot.observed["claim_id"],
                "claim_status": claim.get("status"),
                "upstream_evidence_kind": snapshot.observed.get("claim_evidence_kind"),
                "requested_use": snapshot.observed["requested_use"],
                "condition_scope_source": "computed_exact_cell_classifier",
                "operating_point_provenance": point_provenance or None,
            },
        )
        return make_audited_decision(
            GenericThresholdPolicy(),
            plans,
            slo=None,
            budget=None,
            snapshot=snapshot,
            model_version=MODEL_VERSION,
            provenance=provenance,
            candidate_gate=self.candidate_gate,
        )
