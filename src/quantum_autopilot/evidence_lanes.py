"""Auditable separation between measured optical, measured QKD and Bell lanes.

The product has evidence from three different physical/statistical objects.  A
shared dashboard is useful, but a shared numerical target would be misleading:
recorded optical BER/OSNR is not QBER/SKR, and neither is Bell-state fidelity.
This module makes that boundary executable and exposes recorded WDM telemetry
without granting it quantum-control authority.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Mapping

import pandas as pd

from quantum_autopilot.control_plane.contracts import ControlAction
from quantum_autopilot.core import EvidenceKind, Provenance
from quantum_autopilot.core.contracts import JSONSerializableMixin
from quantum_twin.optical.santanna import SantAnnaOpticalFailureSource


EVIDENCE_LANE_SCHEMA_VERSION = "1.0.0"


class EvidenceLane(str, Enum):
    """Mutually non-interchangeable product evidence lanes."""

    WDM_OPTICAL_HEALTH = "wdm_optical_health"
    QKD_ASSURANCE = "qkd_assurance"
    BELL_FIDELITY_CONTROL = "bell_fidelity_control"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _optional_float(value: Any) -> float | None:
    if value is None or pd.isna(value):
        return None
    numeric = float(value)
    if not math.isfinite(numeric):
        return None
    return numeric


def _optional_string(value: Any) -> str | None:
    if value is None or pd.isna(value):
        return None
    text = str(value).strip()
    return text or None


@dataclass(frozen=True)
class EvidenceLaneDescriptor(JSONSerializableMixin):
    """Scientific and product permissions attached to one evidence lane."""

    lane: EvidenceLane | str
    evidence_kind: EvidenceKind | str
    inputs: tuple[str, ...]
    outputs: tuple[str, ...]
    target_semantics: str
    present_capability: str
    prediction_status: str
    decision_scope: str
    control_plane_ingress_allowed: bool
    physical_action_available: bool = False
    metric_conversion_allowed: bool = False
    prohibited_claims: tuple[str, ...] = field(default_factory=tuple)
    schema_version: str = EVIDENCE_LANE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(self, "lane", EvidenceLane(self.lane))
        object.__setattr__(self, "evidence_kind", EvidenceKind(self.evidence_kind))
        object.__setattr__(self, "inputs", tuple(str(item) for item in self.inputs))
        object.__setattr__(self, "outputs", tuple(str(item) for item in self.outputs))
        object.__setattr__(
            self, "prohibited_claims", tuple(str(item) for item in self.prohibited_claims)
        )
        if not self.inputs or not self.outputs:
            raise ValueError("evidence lane inputs and outputs cannot be empty")
        for name in (
            "target_semantics",
            "present_capability",
            "prediction_status",
            "decision_scope",
        ):
            if not isinstance(getattr(self, name), str) or not getattr(self, name).strip():
                raise ValueError(f"{name} cannot be empty")
        if self.physical_action_available:
            raise ValueError("physical action is unavailable in every current evidence lane")
        if self.lane is not EvidenceLane.BELL_FIDELITY_CONTROL:
            if self.control_plane_ingress_allowed:
                raise ValueError("optical and QKD evidence cannot enter Bell control")
            if self.metric_conversion_allowed:
                raise ValueError("optical/QKD metrics cannot be converted to Bell fidelity")
        if self.schema_version != EVIDENCE_LANE_SCHEMA_VERSION:
            raise ValueError("unsupported evidence-lane schema_version")


@dataclass(frozen=True)
class ControlIngressAssessment(JSONSerializableMixin):
    """A pre-controller semantic gate; it is never final action authorization."""

    lane: EvidenceLane | str
    evidence_kind: EvidenceKind | str
    requested_action: ControlAction | str
    effective_action: ControlAction | str
    sandbox_ingress_allowed: bool
    reason_code: str
    requires_downstream_safety: bool
    final_action_authorized: bool = False
    physical_action_performed: bool = False
    schema_version: str = EVIDENCE_LANE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(self, "lane", EvidenceLane(self.lane))
        object.__setattr__(self, "evidence_kind", EvidenceKind(self.evidence_kind))
        object.__setattr__(self, "requested_action", ControlAction(self.requested_action))
        object.__setattr__(self, "effective_action", ControlAction(self.effective_action))
        if self.final_action_authorized or self.physical_action_performed:
            raise ValueError("the evidence-lane gate cannot authorize or perform an action")
        if not self.sandbox_ingress_allowed and self.effective_action is not ControlAction.HALT:
            raise ValueError("rejected ingress must fail closed to HALT")
        if self.sandbox_ingress_allowed and not self.requires_downstream_safety:
            raise ValueError("accepted ingress still requires the independent safety supervisor")
        if self.schema_version != EVIDENCE_LANE_SCHEMA_VERSION:
            raise ValueError("unsupported evidence-lane schema_version")


@dataclass(frozen=True)
class OpticalDeviceState(JSONSerializableMixin):
    """Latest recorded observation for one Sant'Anna device."""

    device_id: str
    device_type: str
    channel_id: str | None
    observed_at_utc: str
    pre_fec_ber: float | None
    osnr_db: float | None
    input_power_dbm: float | None
    output_power_dbm: float | None
    wdm_failure_label: float | None


@dataclass(frozen=True)
class OpticalHealthSnapshot(JSONSerializableMixin):
    """Measured historical WDM snapshot with no inferred quantum metric."""

    dataset_id: str
    campaign_id: str
    time_basis: str
    quality: dict[str, Any]
    devices: tuple[OpticalDeviceState, ...]
    prediction_status: str
    evidence_positive_count: int | None
    evidence_test_count: int | None
    provenance: Provenance
    generated_at_utc: str = field(default_factory=_utc_now)
    quantum_target_present: bool = False
    bell_fidelity_present: bool = False
    metric_conversion_performed: bool = False
    control_plane_ingress_allowed: bool = False
    physical_action_performed: bool = False
    schema_version: str = EVIDENCE_LANE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.provenance.evidence_kind is not EvidenceKind.MEASURED:
            raise ValueError("optical health requires measured provenance")
        if any(
            (
                self.quantum_target_present,
                self.bell_fidelity_present,
                self.metric_conversion_performed,
                self.control_plane_ingress_allowed,
                self.physical_action_performed,
            )
        ):
            raise ValueError("an optical health snapshot cannot claim quantum/control authority")
        if self.schema_version != EVIDENCE_LANE_SCHEMA_VERSION:
            raise ValueError("unsupported evidence-lane schema_version")


def default_evidence_lane_descriptors() -> tuple[EvidenceLaneDescriptor, ...]:
    """Return the frozen product semantics for the three present lanes."""

    return (
        EvidenceLaneDescriptor(
            lane=EvidenceLane.WDM_OPTICAL_HEALTH,
            evidence_kind=EvidenceKind.MEASURED,
            inputs=("timestamp", "channel/device", "pre-FEC BER", "OSNR", "optical power"),
            outputs=("observed optical state", "data quality", "historical replay status"),
            target_semantics="recorded_classical_optical_kpis",
            present_capability="observational optical-health replay",
            prediction_status=(
                "no validated incremental forecasting gain in the registered Sant'Anna study"
            ),
            decision_scope="monitoring and scientific evidence only",
            control_plane_ingress_allowed=False,
            prohibited_claims=(
                "QBER or SKR was measured",
                "Bell fidelity was measured or predicted",
                "PURIFY or SWAP is authorized",
            ),
        ),
        EvidenceLaneDescriptor(
            lane=EvidenceLane.QKD_ASSURANCE,
            evidence_kind=EvidenceKind.MEASURED,
            inputs=("reviewed coexistence condition", "measured QBER", "measured SKR"),
            outputs=("exact-grid evidence status", "QBER/SKR decision support"),
            target_semantics="measured_qber_and_secure_key_rate",
            present_capability="assurance inside reviewed published operating cells",
            prediction_status="cross-sectional assurance; not temporal prediction",
            decision_scope="non-automated decision support inside the validated grid",
            control_plane_ingress_allowed=False,
            prohibited_claims=(
                "QBER/SKR is Bell fidelity",
                "temporal WDM prediction is validated",
                "PURIFY or SWAP is authorized",
            ),
        ),
        EvidenceLaneDescriptor(
            lane=EvidenceLane.BELL_FIDELITY_CONTROL,
            evidence_kind=EvidenceKind.SIMULATED,
            inputs=("simulated ordered WDM window", "simulated pair/memory state"),
            outputs=("simulated future Bell fidelity", "uncertainty", "sandbox action"),
            target_semantics="simulated_future_bell_fidelity",
            present_capability="shadow or simulated control-plane evaluation",
            prediction_status="simulation validation only",
            decision_scope="non-physical sandbox with independent safety supervision",
            control_plane_ingress_allowed=True,
            prohibited_claims=(
                "measured Bell-fidelity validation",
                "physical quantum-network actuation",
            ),
        ),
    )


class EvidenceLaneRegistry:
    """Describe lanes and prevent semantic target substitution."""

    def __init__(self) -> None:
        self._descriptors = {
            item.lane: item for item in default_evidence_lane_descriptors()
        }

    def describe(self) -> tuple[EvidenceLaneDescriptor, ...]:
        return tuple(self._descriptors[lane] for lane in EvidenceLane)

    def descriptor(self, lane: EvidenceLane | str) -> EvidenceLaneDescriptor:
        return self._descriptors[EvidenceLane(lane)]

    def assess_control_ingress(
        self,
        lane: EvidenceLane | str,
        action: ControlAction | str,
        *,
        evidence_kind: EvidenceKind | str | None = None,
    ) -> ControlIngressAssessment:
        descriptor = self.descriptor(lane)
        requested = ControlAction(action)
        supplied_kind = EvidenceKind(evidence_kind or descriptor.evidence_kind)
        eligible = (
            descriptor.lane is EvidenceLane.BELL_FIDELITY_CONTROL
            and supplied_kind is EvidenceKind.SIMULATED
        )
        if eligible:
            reason = "eligible_for_non_physical_bell_sandbox"
            effective = requested
        elif descriptor.lane is not EvidenceLane.BELL_FIDELITY_CONTROL:
            reason = "evidence_lane_not_bell_fidelity"
            effective = ControlAction.HALT
        else:
            reason = "measured_bell_control_ingress_not_implemented"
            effective = ControlAction.HALT
        return ControlIngressAssessment(
            lane=descriptor.lane,
            evidence_kind=supplied_kind,
            requested_action=requested,
            effective_action=effective,
            sandbox_ingress_allowed=eligible,
            reason_code=reason,
            requires_downstream_safety=eligible,
        )


class MeasuredOpticalHealthAdapter:
    """Expose hash-bound Sant'Anna observations without making a forecast."""

    def __init__(
        self,
        csv_path: str | Path,
        *,
        forecast_report_path: str | Path | None = None,
        expected_dataset_sha256: str | None = None,
    ) -> None:
        self.csv_path = Path(csv_path).resolve()
        self.forecast_report_path = (
            Path(forecast_report_path).resolve() if forecast_report_path else None
        )
        self.expected_dataset_sha256 = (
            str(expected_dataset_sha256).lower() if expected_dataset_sha256 else None
        )

    def _forecast_evidence(self, dataset_sha256: str) -> tuple[str, int | None, int | None, str | None]:
        if self.forecast_report_path is None:
            return "not_evaluated", None, None, None
        if not self.forecast_report_path.is_file():
            raise FileNotFoundError(
                f"optical forecast report not found: {self.forecast_report_path}"
            )
        report = json.loads(self.forecast_report_path.read_text(encoding="utf-8"))
        if not isinstance(report, Mapping):
            raise ValueError("optical forecast report must be a JSON object")
        report_dataset = report.get("dataset")
        if not isinstance(report_dataset, Mapping):
            raise ValueError("optical forecast report is missing dataset provenance")
        if report_dataset.get("sha256") != dataset_sha256:
            raise ValueError("optical forecast report dataset hash does not match the CSV")
        if report_dataset.get("quantum_target_present") is not False:
            raise ValueError("optical forecast report must declare quantum_target_present=false")
        positive = report.get("evidence_positive_count")
        tested = report.get("evidence_test_count")
        if isinstance(positive, bool) or not isinstance(positive, int) or positive < 0:
            raise ValueError("invalid evidence_positive_count in optical forecast report")
        if isinstance(tested, bool) or not isinstance(tested, int) or tested < 1:
            raise ValueError("invalid evidence_test_count in optical forecast report")
        if positive > tested:
            raise ValueError("evidence_positive_count cannot exceed evidence_test_count")
        if positive == 0:
            status = "no_incremental_predictive_value_detected"
        elif positive == tested:
            status = "all_registered_tests_positive"
        else:
            status = "mixed_registered_results"
        return status, positive, tested, _sha256_file(self.forecast_report_path)

    def snapshot(self) -> OpticalHealthSnapshot:
        if not self.csv_path.is_file():
            raise FileNotFoundError(f"optical WDM CSV not found: {self.csv_path}")
        dataset_sha256 = _sha256_file(self.csv_path)
        if (
            self.expected_dataset_sha256 is not None
            and dataset_sha256 != self.expected_dataset_sha256
        ):
            raise ValueError("optical WDM CSV hash does not match expected_dataset_sha256")

        source = SantAnnaOpticalFailureSource(str(self.csv_path))
        frame = source.read()
        quality = source.quality_report(frame)
        if not quality["validation_valid"]:
            raise ValueError("optical WDM CSV failed Sant'Anna schema validation")
        prediction_status, positive, tested, report_sha256 = self._forecast_evidence(
            dataset_sha256
        )

        latest = (
            frame.sort_values(["device_id", "timestamp_utc"], kind="stable")
            .groupby("device_id", sort=True, as_index=False)
            .tail(1)
            .sort_values("device_id", kind="stable")
        )
        devices = tuple(
            OpticalDeviceState(
                device_id=str(row.device_id),
                device_type=str(row.device_type),
                channel_id=_optional_string(row.channel_id),
                observed_at_utc=row.timestamp_utc.isoformat(),
                pre_fec_ber=_optional_float(row.pre_fec_ber),
                osnr_db=_optional_float(row.osnr_db),
                input_power_dbm=_optional_float(row.input_power_dbm),
                output_power_dbm=_optional_float(row.output_power_dbm),
                wdm_failure_label=_optional_float(row.wdm_failure),
            )
            for row in latest.itertuples(index=False)
        )
        artifact_hashes = {"optical_wdm_csv": dataset_sha256}
        if report_sha256 is not None:
            artifact_hashes["optical_forecast_report"] = report_sha256
        provenance = Provenance(
            evidence_kind=EvidenceKind.MEASURED,
            source=f"SantAnnaOpticalFailureSource:{self.csv_path.name}",
            dataset_hash=dataset_sha256,
            artifact_hashes=artifact_hashes,
            metadata={
                "evidence_lane": EvidenceLane.WDM_OPTICAL_HEALTH.value,
                "target_semantics": "recorded_classical_optical_kpis",
                "quantum_target_present": False,
                "historical_recording": True,
            },
        )
        return OpticalHealthSnapshot(
            dataset_id=str(frame["dataset_id"].iloc[0]),
            campaign_id=str(frame["campaign_id"].iloc[0]),
            time_basis="historical_recorded_replay",
            quality=quality,
            devices=devices,
            prediction_status=prediction_status,
            evidence_positive_count=positive,
            evidence_test_count=tested,
            provenance=provenance,
        )


class EvidenceLaneService:
    """Product-facing facade for lane status and optional measured WDM replay."""

    def __init__(self, optical_adapter: MeasuredOpticalHealthAdapter | None = None) -> None:
        self.registry = EvidenceLaneRegistry()
        self.optical_adapter = optical_adapter

    def status(self) -> dict[str, Any]:
        lanes = []
        for descriptor in self.registry.describe():
            item = descriptor.to_dict()
            if descriptor.lane is EvidenceLane.WDM_OPTICAL_HEALTH:
                item["source_configured"] = self.optical_adapter is not None
            else:
                item["source_configured"] = True
            lanes.append(item)
        return {
            "schema_version": EVIDENCE_LANE_SCHEMA_VERSION,
            "lanes": lanes,
            "lane_count": len(lanes),
            "central_hypothesis_status": (
                "open: no qualifying synchronized WDM-to-future-quantum temporal dataset"
            ),
            "cross_metric_conversion_allowed": False,
            "optical_or_qkd_control_ingress_allowed": False,
            "physical_action_available": False,
        }

    def optical_health(self) -> OpticalHealthSnapshot:
        if self.optical_adapter is None:
            raise ValueError(
                "measured optical source is not configured; start the API with --optical-wdm-csv"
            )
        return self.optical_adapter.snapshot()


__all__ = [
    "ControlIngressAssessment",
    "EVIDENCE_LANE_SCHEMA_VERSION",
    "EvidenceLane",
    "EvidenceLaneDescriptor",
    "EvidenceLaneRegistry",
    "EvidenceLaneService",
    "MeasuredOpticalHealthAdapter",
    "OpticalDeviceState",
    "OpticalHealthSnapshot",
    "default_evidence_lane_descriptors",
]
