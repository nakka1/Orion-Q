"""Typed measured QKD point and exact IrelandQCI grid classification.

The classifier intentionally performs no interpolation.  It identifies only
published campaign/fibre/VOA/coexistence cells whose source hashes are known.
Measured QBER and SKR are validated as outcomes, but are never used to decide
whether their own experimental condition belongs to the grid.
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

from quantum_autopilot.core import EvidenceKind, Provenance


GRID_CONFIG_RELATIVE_PATH = Path(
    "src/quantum_autopilot/qkd/data/ireland_qci_grid_v1.json"
)
GRID_CONFIG_SHA256 = "d2843852a6373661a92cad887a26ab86c51bc57d6d0a38baa6988f2698d4038c"
CONDITION_SCOPES = frozenset(
    {"inside_validated_grid", "outside_validated_grid", "unknown"}
)
QBER_UNIT_STATUSES = frozenset(
    {"ratio", "percent", "publisher_scale_ambiguous"}
)
TIME_BASES = frozenset({"measurement_time", "not_published"})
OBSERVATION_LEVELS = frozenset({"single_measurement", "published_cell_mean"})


def _nonempty(name: str, value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} cannot be empty")
    return value.strip()


def _finite(name: str, value: Any, *, minimum: float | None = None) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be numeric")
    numeric = float(value)
    if not math.isfinite(numeric):
        raise ValueError(f"{name} must be finite")
    if minimum is not None and numeric < minimum:
        raise ValueError(f"{name} must be >= {minimum}")
    return numeric


def _aware_timestamp(name: str, value: str) -> str:
    value = _nonempty(name, value)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{name} must be a valid ISO-8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{name} must include a timezone")
    return value


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _valid_sha256(value: str) -> bool:
    return len(value) == 64 and all(character in "0123456789abcdefABCDEF" for character in value)


@dataclass
class QKDOperatingPoint:
    """One measured cross-sectional QKD observation with explicit semantics.

    ``observation_time_utc`` may be absent only when ``time_basis`` explicitly
    says that the source did not publish measurement timestamps.  This escape
    hatch preserves the IrelandQCI evidence boundary; it does not make the
    point eligible for temporal-prediction claims.
    """

    dataset_id: str
    campaign_id: str
    device_id: str
    channel_id: str
    external_signal_present: bool
    external_signal_power_dbm: float | None
    fiber_length_km: float
    voa_attenuation_db: float
    qber_reported: float
    qber_unit_status: str
    secure_key_rate_bps: float
    observation_level: str
    provenance: Provenance | Mapping[str, Any]
    observation_time_utc: str | None = None
    time_basis: str = "measurement_time"
    schema_version: str = "1.0.0"

    def __post_init__(self) -> None:
        if self.schema_version != "1.0.0":
            raise ValueError("unsupported QKDOperatingPoint schema_version")
        self.dataset_id = _nonempty("dataset_id", self.dataset_id)
        self.campaign_id = _nonempty("campaign_id", self.campaign_id)
        self.device_id = _nonempty("device_id", self.device_id)
        self.channel_id = _nonempty("channel_id", self.channel_id)
        if not isinstance(self.external_signal_present, bool):
            raise ValueError("external_signal_present must be boolean")
        if self.external_signal_present:
            if self.external_signal_power_dbm is None:
                raise ValueError(
                    "external_signal_power_dbm is required when an external signal is present"
                )
            self.external_signal_power_dbm = _finite(
                "external_signal_power_dbm", self.external_signal_power_dbm
            )
        elif self.external_signal_power_dbm is not None:
            raise ValueError(
                "baseline points must use null external_signal_power_dbm; 0 dBm is not baseline"
            )

        self.fiber_length_km = _finite(
            "fiber_length_km", self.fiber_length_km, minimum=0.0
        )
        self.voa_attenuation_db = _finite(
            "voa_attenuation_db", self.voa_attenuation_db, minimum=0.0
        )
        self.qber_reported = _finite("qber_reported", self.qber_reported, minimum=0.0)
        self.secure_key_rate_bps = _finite(
            "secure_key_rate_bps", self.secure_key_rate_bps, minimum=0.0
        )
        if self.observation_level not in OBSERVATION_LEVELS:
            raise ValueError(
                f"observation_level must be one of {sorted(OBSERVATION_LEVELS)}"
            )
        if self.qber_unit_status not in QBER_UNIT_STATUSES:
            raise ValueError(
                f"qber_unit_status must be one of {sorted(QBER_UNIT_STATUSES)}"
            )
        upper_bound = 1.0 if self.qber_unit_status == "ratio" else 100.0
        if self.qber_reported > upper_bound:
            raise ValueError(
                f"qber_reported must be <= {upper_bound} for {self.qber_unit_status}"
            )

        if self.time_basis not in TIME_BASES:
            raise ValueError(f"time_basis must be one of {sorted(TIME_BASES)}")
        if self.time_basis == "measurement_time":
            if self.observation_time_utc is None:
                raise ValueError(
                    "observation_time_utc is required when time_basis is measurement_time"
                )
            self.observation_time_utc = _aware_timestamp(
                "observation_time_utc", self.observation_time_utc
            )
        elif self.observation_time_utc is not None:
            raise ValueError(
                "observation_time_utc must be null when time_basis is not_published"
            )

        if isinstance(self.provenance, Mapping):
            self.provenance = Provenance.from_dict(self.provenance)
        if not isinstance(self.provenance, Provenance):
            raise ValueError("provenance must be a Provenance object or mapping")
        if self.provenance.evidence_kind is not EvidenceKind.MEASURED:
            raise ValueError("QKD operating points require measured provenance")
        _nonempty("provenance.source", self.provenance.source)
        hashes = [self.provenance.dataset_hash, *self.provenance.artifact_hashes.values()]
        hashes = [digest for digest in hashes if digest]
        if not hashes:
            raise ValueError("measured provenance requires a dataset or artifact SHA-256")
        if any(not _valid_sha256(digest) for digest in hashes):
            raise ValueError("measured provenance hashes must be 64-character SHA-256 values")

    def to_dict(self) -> dict[str, Any]:
        return {
            "dataset_id": self.dataset_id,
            "campaign_id": self.campaign_id,
            "device_id": self.device_id,
            "channel_id": self.channel_id,
            "external_signal_present": self.external_signal_present,
            "external_signal_power_dbm": self.external_signal_power_dbm,
            "fiber_length_km": self.fiber_length_km,
            "voa_attenuation_db": self.voa_attenuation_db,
            "qber_reported": self.qber_reported,
            "qber_unit_status": self.qber_unit_status,
            "secure_key_rate_bps": self.secure_key_rate_bps,
            "observation_level": self.observation_level,
            "provenance": self.provenance.to_dict(),
            "observation_time_utc": self.observation_time_utc,
            "time_basis": self.time_basis,
            "schema_version": self.schema_version,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "QKDOperatingPoint":
        return cls(**dict(payload))


@dataclass(frozen=True)
class GridClassification:
    """Auditable result of matching an operating point to a published cell."""

    scope: str
    grid_id: str
    grid_valid: bool
    grid_config_sha256: str
    source_artifact_available: bool
    source_artifact_verified: bool
    matched_cell: dict[str, Any] | None = None
    violations: tuple[dict[str, Any], ...] = field(default_factory=tuple)
    limitations: tuple[str, ...] = field(default_factory=tuple)
    classification_axes: tuple[str, ...] = field(default_factory=tuple)
    schema_version: str = "1.0.0"

    def __post_init__(self) -> None:
        if self.scope not in CONDITION_SCOPES:
            raise ValueError(f"unsupported condition scope: {self.scope!r}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "scope": self.scope,
            "grid_id": self.grid_id,
            "grid_valid": self.grid_valid,
            "grid_config_sha256": self.grid_config_sha256,
            "source_artifact_available": self.source_artifact_available,
            "source_artifact_verified": self.source_artifact_verified,
            "matched_cell": self.matched_cell,
            "violations": [dict(item) for item in self.violations],
            "limitations": list(self.limitations),
            "classification_axes": list(self.classification_axes),
            "schema_version": self.schema_version,
        }


class IrelandQCIGridClassifier:
    """Fail-closed, exact-cell classifier for the shipped IrelandQCI grid."""

    def __init__(
        self,
        repository_root: str | Path | None = None,
        grid_path: str | Path | None = None,
    ) -> None:
        default_root = Path(__file__).resolve().parents[3]
        self.repository_root = Path(repository_root or default_root).resolve()
        if grid_path is None and repository_root is None:
            repository_candidate = self.repository_root / GRID_CONFIG_RELATIVE_PATH
            packaged_candidate = Path(__file__).resolve().parent / "data" / (
                "ireland_qci_grid_v1.json"
            )
            requested = (
                repository_candidate if repository_candidate.is_file() else packaged_candidate
            )
        else:
            requested = Path(grid_path or GRID_CONFIG_RELATIVE_PATH)
        self.grid_path = (
            requested.resolve()
            if requested.is_absolute()
            else (self.repository_root / requested).resolve()
        )
        try:
            self.grid_path.relative_to(self.repository_root)
        except ValueError as exc:
            raise ValueError("grid_path must remain inside repository_root") from exc

    @staticmethod
    def _violation(
        code: str,
        constraint: str,
        required: Any,
        actual: Any,
        detail: str,
    ) -> dict[str, Any]:
        return {
            "code": code,
            "constraint": constraint,
            "required": required,
            "actual": actual,
            "detail": detail,
        }

    @staticmethod
    def _same(left: float | None, right: float | None, tolerance: float) -> bool:
        if left is None or right is None:
            return left is None and right is None
        return math.isclose(float(left), float(right), rel_tol=0.0, abs_tol=tolerance)

    def _load_grid(self) -> tuple[dict[str, Any], dict[str, Any]]:
        if not self.grid_path.is_file():
            raise FileNotFoundError(f"operating-grid config not found: {self.grid_path}")
        config_hash = _sha256(self.grid_path)
        if config_hash != GRID_CONFIG_SHA256:
            raise ValueError(
                "operating-grid config hash differs from the reviewed package contract"
            )
        payload = json.loads(self.grid_path.read_text(encoding="utf-8"))
        required = {
            "schema_version",
            "grid_id",
            "claim_id",
            "dataset_id",
            "expected_cell_count",
            "interpolation_allowed",
            "numeric_absolute_tolerance",
            "classification_axes",
            "required_source_sentinels",
            "qber_unit_status",
            "voa_by_fiber_length_km",
            "campaigns",
            "source_artifact",
            "limitations",
        }
        missing = sorted(required.difference(payload))
        if missing:
            raise ValueError(f"operating-grid config is missing fields: {missing}")
        if payload["schema_version"] != "1.0.0" or payload["claim_id"] != "C-MEASURED":
            raise ValueError("unsupported operating-grid schema or claim")
        if payload["interpolation_allowed"] is not False:
            raise ValueError("the reviewed operating grid must prohibit interpolation")
        tolerance = _finite(
            "numeric_absolute_tolerance",
            payload["numeric_absolute_tolerance"],
            minimum=0.0,
        )
        campaigns = payload["campaigns"]
        if not isinstance(campaigns, list) or not campaigns:
            raise ValueError("operating-grid campaigns must be a non-empty list")
        campaign_ids = [item.get("campaign_id") for item in campaigns]
        if len(set(campaign_ids)) != len(campaign_ids):
            raise ValueError("operating-grid campaign identifiers must be unique")
        for campaign in campaigns:
            if not _valid_sha256(str(campaign.get("source_file_sha256", ""))):
                raise ValueError("every operating-grid campaign requires a source SHA-256")
            if not isinstance(campaign.get("external_signal_present"), bool):
                raise ValueError("campaign external_signal_present must be boolean")
            signal_present = campaign["external_signal_present"]
            power = campaign.get("external_signal_power_dbm")
            if signal_present != (power is not None):
                raise ValueError("campaign signal presence and power are inconsistent")
            _finite("campaign.fiber_length_km", campaign.get("fiber_length_km"), minimum=0.0)
            if power is not None:
                _finite("campaign.external_signal_power_dbm", power)

        voa_by_fiber = payload["voa_by_fiber_length_km"]
        calculated_cells = 0
        for campaign in campaigns:
            fiber_key = f"{float(campaign['fiber_length_km']):g}"
            if fiber_key not in voa_by_fiber:
                raise ValueError(f"no VOA grid is registered for fibre length {fiber_key}")
            voa_values = voa_by_fiber[fiber_key]
            if len(set(voa_values)) != len(voa_values):
                raise ValueError(f"VOA grid for fibre length {fiber_key} contains duplicates")
            for value in voa_values:
                _finite("voa_attenuation_db", value, minimum=0.0)
            calculated_cells += len(voa_values)
        if calculated_cells != payload["expected_cell_count"]:
            raise ValueError(
                "operating-grid expected_cell_count does not match campaign/VOA cells"
            )

        source = payload["source_artifact"]
        source_path = (self.repository_root / source["path"]).resolve()
        try:
            source_path.relative_to(self.repository_root)
        except ValueError as exc:
            raise ValueError("grid source artifact must remain inside repository_root") from exc
        source_available = source_path.is_file()
        source_verified = False
        if source_available:
            if _sha256(source_path) != source["sha256"]:
                raise ValueError("operating-grid source artifact hash mismatch")
            with source_path.open("r", encoding="utf-8-sig", newline="") as handle:
                rows = sum(1 for _ in csv.DictReader(handle))
            if rows != source["rows"] or rows != payload["expected_cell_count"]:
                raise ValueError("operating-grid source artifact row count mismatch")
            source_verified = True
        integrity = {
            "grid_config_sha256": config_hash,
            "source_artifact_available": source_available,
            "source_artifact_verified": source_verified,
            "tolerance": tolerance,
        }
        return payload, integrity

    def classify(self, point: QKDOperatingPoint) -> GridClassification:
        if not isinstance(point, QKDOperatingPoint):
            raise ValueError("point must be a QKDOperatingPoint")
        try:
            grid, integrity = self._load_grid()
        except Exception as exc:
            return GridClassification(
                scope="unknown",
                grid_id="",
                grid_valid=False,
                grid_config_sha256=(
                    _sha256(self.grid_path) if self.grid_path.is_file() else ""
                ),
                source_artifact_available=False,
                source_artifact_verified=False,
                violations=(
                    self._violation(
                        "operating_grid_integrity_failed",
                        "operating_grid_valid",
                        True,
                        False,
                        f"{type(exc).__name__}: {exc}",
                    ),
                ),
            )

        violations: list[dict[str, Any]] = []
        tolerance = integrity["tolerance"]
        if point.dataset_id != grid["dataset_id"]:
            violations.append(
                self._violation(
                    "dataset_outside_validated_grid",
                    "dataset_id",
                    grid["dataset_id"],
                    point.dataset_id,
                    "cross-dataset or cross-platform transport is not validated",
                )
            )
        campaign = next(
            (item for item in grid["campaigns"] if item["campaign_id"] == point.campaign_id),
            None,
        )
        if campaign is None:
            violations.append(
                self._violation(
                    "campaign_outside_validated_grid",
                    "campaign_id",
                    [item["campaign_id"] for item in grid["campaigns"]],
                    point.campaign_id,
                    "unseen campaigns cannot inherit within-session evidence",
                )
            )
        else:
            checks = (
                (
                    "fiber_length_km",
                    campaign["fiber_length_km"],
                    point.fiber_length_km,
                    "fibre length does not match the published campaign",
                ),
                (
                    "external_signal_present",
                    campaign["external_signal_present"],
                    point.external_signal_present,
                    "signal-presence condition does not match the published campaign",
                ),
                (
                    "external_signal_power_dbm",
                    campaign["external_signal_power_dbm"],
                    point.external_signal_power_dbm,
                    "power is unobserved for this campaign; interpolation is prohibited",
                ),
            )
            for name, required, actual, detail in checks:
                equal = (
                    required == actual
                    if isinstance(required, bool)
                    else self._same(required, actual, tolerance)
                )
                if not equal:
                    violations.append(
                        self._violation(
                            "condition_outside_validated_grid", name, required, actual, detail
                        )
                    )

            fiber_key = f"{float(campaign['fiber_length_km']):g}"
            allowed_voa = grid["voa_by_fiber_length_km"][fiber_key]
            if not any(
                self._same(float(value), point.voa_attenuation_db, tolerance)
                for value in allowed_voa
            ):
                violations.append(
                    self._violation(
                        "condition_outside_validated_grid",
                        "voa_attenuation_db",
                        allowed_voa,
                        point.voa_attenuation_db,
                        "VOA value is not an exact observed cell for this fibre length",
                    )
                )

        if point.qber_unit_status != grid["qber_unit_status"]:
            violations.append(
                self._violation(
                    "qber_scale_outside_validated_grid",
                    "qber_unit_status",
                    grid["qber_unit_status"],
                    point.qber_unit_status,
                    "the publisher's ambiguous QBER scalar cannot be silently rescaled",
                )
            )

        for field_name, required in grid["required_source_sentinels"].items():
            actual = getattr(point, field_name)
            if actual != required:
                violations.append(
                    self._violation(
                        "source_dimension_not_published",
                        field_name,
                        required,
                        actual,
                        "this dimension was not published and cannot be matched to the grid",
                    )
                )

        if campaign is not None:
            provenance_hashes = {
                point.provenance.dataset_hash.lower(),
                *(digest.lower() for digest in point.provenance.artifact_hashes.values()),
            }
            provenance_hashes.discard("")
            if campaign["source_file_sha256"] not in provenance_hashes:
                violations.append(
                    self._violation(
                        "campaign_source_hash_not_verified",
                        "provenance.source_file_sha256",
                        campaign["source_file_sha256"],
                        sorted(provenance_hashes),
                        "cell membership requires provenance tied to the published campaign file",
                    )
                )

        matched_cell = None
        if not violations and campaign is not None:
            matched_cell = {
                "cell_id": (
                    f"{grid['dataset_id']}|{campaign['campaign_id']}|"
                    f"voa={point.voa_attenuation_db:g}"
                ),
                "campaign_id": campaign["campaign_id"],
                "fiber_length_km": point.fiber_length_km,
                "voa_attenuation_db": point.voa_attenuation_db,
                "external_signal_present": point.external_signal_present,
                "external_signal_power_dbm": point.external_signal_power_dbm,
                "source_file_sha256": campaign["source_file_sha256"],
                "outcomes_used_for_membership": False,
            }
        return GridClassification(
            scope=("inside_validated_grid" if matched_cell else "outside_validated_grid"),
            grid_id=grid["grid_id"],
            grid_valid=True,
            grid_config_sha256=integrity["grid_config_sha256"],
            source_artifact_available=integrity["source_artifact_available"],
            source_artifact_verified=integrity["source_artifact_verified"],
            matched_cell=matched_cell,
            violations=tuple(violations),
            limitations=tuple(str(item) for item in grid["limitations"]),
            classification_axes=tuple(str(item) for item in grid["classification_axes"]),
        )

    def classify_optional(
        self, point: QKDOperatingPoint | None
    ) -> GridClassification:
        """Classify a point, or verify the grid and return explicit unknown."""

        if point is not None:
            return self.classify(point)
        try:
            grid, integrity = self._load_grid()
        except Exception as exc:
            return GridClassification(
                scope="unknown",
                grid_id="",
                grid_valid=False,
                grid_config_sha256=(
                    _sha256(self.grid_path) if self.grid_path.is_file() else ""
                ),
                source_artifact_available=False,
                source_artifact_verified=False,
                violations=(
                    self._violation(
                        "operating_grid_integrity_failed",
                        "operating_grid_valid",
                        True,
                        False,
                        f"{type(exc).__name__}: {exc}",
                    ),
                ),
            )
        return GridClassification(
            scope="unknown",
            grid_id=grid["grid_id"],
            grid_valid=True,
            grid_config_sha256=integrity["grid_config_sha256"],
            source_artifact_available=integrity["source_artifact_available"],
            source_artifact_verified=integrity["source_artifact_verified"],
            violations=(
                self._violation(
                    "operating_point_missing",
                    "operating_point",
                    "measured point with exact published cell provenance",
                    None,
                    "grid membership is unknown until a typed measured point is supplied",
                ),
            ),
            limitations=tuple(str(item) for item in grid["limitations"]),
            classification_axes=tuple(str(item) for item in grid["classification_axes"]),
        )


__all__ = [
    "CONDITION_SCOPES",
    "GRID_CONFIG_RELATIVE_PATH",
    "GRID_CONFIG_SHA256",
    "GridClassification",
    "IrelandQCIGridClassifier",
    "OBSERVATION_LEVELS",
    "QBER_UNIT_STATUSES",
    "QKDOperatingPoint",
    "TIME_BASES",
]
