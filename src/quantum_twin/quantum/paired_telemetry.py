"""Strict contract for synchronized optical and measured-quantum telemetry.

The contract is intentionally stronger than the public datasets currently
available to this project.  A campaign labelled experimental must be backed by
recorded/live optical telemetry and measured quantum targets.  Simulation
outputs, transparent QBER transforms and timezone-naive timestamps are
rejected rather than silently upgraded into hardware evidence.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import json
from pathlib import Path
import re
from typing import Iterable

import numpy as np
import pandas as pd

from quantum_twin.optical.interface import (
    ColumnRole,
    ColumnSpec,
    DataProvenance,
    TelemetrySchema,
    TelemetrySource,
    ValidationIssue,
    ValidationResult,
)


class PairedQuantumProfile(str, Enum):
    """Measured-target coverage required by a campaign."""

    MEASURED_QKD = "measured_qkd"
    MEASURED_BELL_FIDELITY = "measured_bell_fidelity"


PAIRED_IDENTIFIER_COLUMNS = (
    "dataset_id", "campaign_id", "device_id", "channel_id",
)
PAIRED_OPTICAL_COLUMNS = (
    "pre_fec_ber", "osnr_db", "rx_power_dbm", "tx_power_dbm",
)
PAIRED_QUANTUM_COLUMNS = (
    "qber_ratio", "secure_key_rate_bps", "bell_fidelity_measured",
)
PAIRED_QUANTUM_TELEMETRY_COLUMNS = (
    *PAIRED_IDENTIFIER_COLUMNS,
    "timestamp_utc",
    *PAIRED_OPTICAL_COLUMNS,
    *PAIRED_QUANTUM_COLUMNS,
    "fidelity_standard_error",
    "fidelity_shots",
    "fidelity_measurement_method",
    "alignment_error_ms",
)


PAIRED_QUANTUM_SCHEMA = TelemetrySchema(
    columns=[
        *[
            ColumnSpec(
                name, "string", "identifier",
                provenance=DataProvenance.IDENTIFIER,
                role=ColumnRole.IDENTIFIER,
            )
            for name in PAIRED_IDENTIFIER_COLUMNS
        ],
        ColumnSpec("pre_fec_ber", "float64", "unitless", 0.0, 1.0),
        ColumnSpec("osnr_db", "float64", "dB", -30.0, 100.0),
        ColumnSpec("rx_power_dbm", "float64", "dBm", -100.0, 50.0),
        ColumnSpec(
            "tx_power_dbm", "float64", "dBm", -100.0, 50.0,
            nullable=True,
        ),
        ColumnSpec(
            "qber_ratio", "float64", "unitless ratio", 0.0, 1.0,
            provenance=DataProvenance.LABEL, role=ColumnRole.TARGET,
        ),
        ColumnSpec(
            "secure_key_rate_bps", "float64", "bit/s", 0.0, None,
            provenance=DataProvenance.LABEL, role=ColumnRole.TARGET,
        ),
        ColumnSpec(
            "bell_fidelity_measured", "float64", "unitless", 0.0, 1.0,
            provenance=DataProvenance.LABEL, role=ColumnRole.TARGET,
            nullable=True,
        ),
        ColumnSpec(
            "fidelity_standard_error", "float64", "unitless", 0.0, 1.0,
            provenance=DataProvenance.MEASURED, role=ColumnRole.QUALITY,
            nullable=True,
        ),
        ColumnSpec(
            "fidelity_shots", "float64", "count", 1.0, None,
            provenance=DataProvenance.MEASURED, role=ColumnRole.QUALITY,
            nullable=True,
        ),
        ColumnSpec(
            "fidelity_measurement_method", "string", "category",
            provenance=DataProvenance.MEASURED, role=ColumnRole.QUALITY,
            nullable=True,
        ),
        ColumnSpec(
            "alignment_error_ms", "float64", "ms", 0.0, None,
            provenance=DataProvenance.DERIVED, role=ColumnRole.QUALITY,
        ),
    ],
    timestamp_column="timestamp_utc",
    timestamp_required=True,
    requires_regular_sampling=False,
)


DEFAULT_PAIRED_MANIFEST = {
    "schema_version": "1.0.0",
    "dataset_id": "REPLACE_ME",
    "data_provenance": "recorded_hardware",
    "experimental_campaign": True,
    "target_provenance": {
        "qber_ratio": "measured_quantum",
        "secure_key_rate_bps": "measured_quantum",
        "bell_fidelity_measured": "not_available",
    },
    "synchronization": {
        "paired_on_same_link_and_clock": True,
        "timestamp_timezone": "UTC",
        "clock_source": "REPLACE_ME: NTP/PTP/GPS/instrument clock",
        "maximum_alignment_error_ms": 1000.0,
    },
    "fidelity_measurement": {
        "method": None,
        "uncertainty_reported": False,
        "shots_reported": False,
    },
    "contains_key_material": False,
    "license": "REPLACE_ME",
}


def _manifest_sidecar(data_path: Path) -> Path:
    return data_path.with_suffix(data_path.suffix + ".manifest.json")


def _default_manifest_path(data_path: Path) -> Path:
    sidecar = _manifest_sidecar(data_path)
    shared = data_path.parent / "paired_quantum_telemetry.manifest.json"
    return sidecar if sidecar.exists() or not shared.exists() else shared


def _timestamp_strings_have_zone(values: pd.Series) -> bool:
    nonmissing = values.dropna()
    if nonmissing.empty:
        return False
    pattern = re.compile(r"(?:Z|[+-]\d{2}:?\d{2})$", re.IGNORECASE)
    return bool(nonmissing.astype(str).str.strip().map(lambda value: bool(pattern.search(value))).all())


def experimental_provenance_issues(
    data: pd.DataFrame,
    manifest: dict,
    *,
    required_targets: Iterable[str],
) -> list[ValidationIssue]:
    """Return blocking provenance issues for a measured experiment."""
    issues: list[ValidationIssue] = []
    if manifest.get("experimental_campaign") is not True:
        issues.append(ValidationIssue(
            "experimental_campaign", "campaign_not_declared_experimental",
            "paired measured-quantum validation requires experimental_campaign=true",
        ))
    data_origin = str(manifest.get("data_provenance", "")).casefold()
    if data_origin not in {"recorded_hardware", "live_hardware"}:
        issues.append(ValidationIssue(
            "data_provenance", "non_hardware_experimental_input",
            "experimental campaigns require recorded_hardware or live_hardware inputs",
        ))

    provenance = manifest.get("target_provenance", {})
    targets_to_check = list(required_targets)
    if (
        "bell_fidelity_measured" in data
        and data["bell_fidelity_measured"].notna().any()
        and "bell_fidelity_measured" not in targets_to_check
    ):
        targets_to_check.append("bell_fidelity_measured")
    for target in targets_to_check:
        value = str(provenance.get(target, "missing")).casefold()
        if value.startswith("simulated") or value in {"derived", "synthetic"}:
            issues.append(ValidationIssue(
                target, "simulated_target_in_experimental_campaign",
                f"{target} provenance is {value!r}; measured_quantum is required",
            ))
        elif value != "measured_quantum":
            issues.append(ValidationIssue(
                target, "unverified_quantum_target_provenance",
                f"{target} provenance is {value!r}; measured_quantum is required",
            ))

    forbidden = {
        "simulated_bell_fidelity", "bell_fidelity_simulated", "fidelity_simulated", "f_t_simulated",
    }
    present = sorted(forbidden.intersection(name.casefold() for name in data.columns))
    if present:
        issues.append(ValidationIssue(
            ",".join(present), "simulated_fidelity_column_present",
            "simulated fidelity cannot enter a campaign declared experimental",
        ))
    return issues


def assert_experimental_quantum_targets(
    data: pd.DataFrame,
    *,
    required_targets: Iterable[str],
) -> None:
    """Raise before fitting if target provenance is not genuinely measured."""
    manifest = data.attrs.get("paired_quantum_manifest", {})
    issues = experimental_provenance_issues(
        data, manifest, required_targets=tuple(required_targets)
    )
    if issues:
        detail = "; ".join(f"{issue.column}: {issue.detail}" for issue in issues)
        raise ValueError(f"experimental quantum target validation failed: {detail}")


class PairedQuantumTelemetrySource(TelemetrySource):
    """Read a CSV/Parquet paired-telemetry bundle plus JSON manifest."""

    def __init__(
        self,
        data_path: str | Path,
        *,
        manifest_path: str | Path | None = None,
        profile: PairedQuantumProfile | str = PairedQuantumProfile.MEASURED_QKD,
    ):
        self.data_path = Path(data_path)
        self.manifest_path = (
            Path(manifest_path)
            if manifest_path is not None
            else _default_manifest_path(self.data_path)
        )
        self.profile = PairedQuantumProfile(profile)

    def schema(self) -> TelemetrySchema:
        return PAIRED_QUANTUM_SCHEMA

    def _read_manifest(self) -> dict:
        if not self.manifest_path.exists():
            raise FileNotFoundError(f"paired telemetry manifest not found: {self.manifest_path}")
        return json.loads(self.manifest_path.read_text(encoding="utf-8"))

    def read(self) -> pd.DataFrame:
        if not self.data_path.exists():
            raise FileNotFoundError(f"paired telemetry file not found: {self.data_path}")
        suffix = self.data_path.suffix.casefold()
        if suffix == ".csv":
            raw = pd.read_csv(self.data_path)
            had_explicit_zone = (
                "timestamp_utc" in raw and _timestamp_strings_have_zone(raw["timestamp_utc"])
            )
        elif suffix in {".parquet", ".pq"}:
            raw = pd.read_parquet(self.data_path)
            timestamp = raw.get("timestamp_utc")
            had_explicit_zone = bool(
                timestamp is not None
                and isinstance(getattr(timestamp.dtype, "tz", None), object)
                and getattr(timestamp.dtype, "tz", None) is not None
            )
        else:
            raise ValueError("paired telemetry must be CSV or Parquet")

        result = raw.copy()
        if "timestamp_utc" in result:
            result["timestamp_utc"] = pd.to_datetime(
                result["timestamp_utc"], errors="coerce", utc=True
            )
        numeric = [
            *PAIRED_OPTICAL_COLUMNS,
            *PAIRED_QUANTUM_COLUMNS,
            "fidelity_standard_error", "fidelity_shots", "alignment_error_ms",
        ]
        for column in numeric:
            if column in result:
                result[column] = pd.to_numeric(result[column], errors="coerce")
        ordering = [
            name for name in [*PAIRED_IDENTIFIER_COLUMNS, "timestamp_utc"]
            if name in result
        ]
        if ordering:
            result = result.sort_values(ordering, kind="stable").reset_index(drop=True)
        manifest = self._read_manifest()
        result.attrs["paired_quantum_manifest"] = manifest
        result.attrs["timestamp_input_had_explicit_timezone"] = had_explicit_zone
        result.attrs["telemetry_provenance"] = {
            "source_type": manifest.get("data_provenance"),
            "target_provenance": manifest.get("target_provenance", {}),
            "experimental_campaign": bool(manifest.get("experimental_campaign", True)),
        }
        return result

    def required_targets(self) -> tuple[str, ...]:
        if self.profile is PairedQuantumProfile.MEASURED_BELL_FIDELITY:
            return PAIRED_QUANTUM_COLUMNS
        return ("qber_ratio", "secure_key_rate_bps")

    def validate(self, data: pd.DataFrame) -> ValidationResult:
        base = super().validate(data)
        issues = [
            issue for issue in base.issues
            if issue.issue_type not in {"duplicate_timestamp", "non_monotonic_timestamp"}
        ]
        manifest = data.attrs.get("paired_quantum_manifest", {})
        issues.extend(experimental_provenance_issues(
            data, manifest, required_targets=self.required_targets()
        ))

        synchronization = manifest.get("synchronization", {})
        for field, value in (
            ("dataset_id", manifest.get("dataset_id")),
            ("license", manifest.get("license")),
            ("synchronization.clock_source", synchronization.get("clock_source")),
        ):
            normalized = str(value or "").strip().casefold()
            if not normalized or normalized.startswith("replace_me"):
                issues.append(ValidationIssue(
                    field, "manifest_placeholder_not_resolved",
                    f"manifest field {field} must be completed before acquisition analysis",
                ))
        if synchronization.get("paired_on_same_link_and_clock") is not True:
            issues.append(ValidationIssue(
                "synchronization", "unverified_link_clock_pairing",
                "paired_on_same_link_and_clock=true is required",
            ))
        if str(synchronization.get("timestamp_timezone", "")).upper() != "UTC":
            issues.append(ValidationIssue(
                "timestamp_utc", "manifest_timezone_not_utc",
                "manifest synchronization.timestamp_timezone must be UTC",
            ))

        if not data.attrs.get("timestamp_input_had_explicit_timezone", False):
            issues.append(ValidationIssue(
                "timestamp_utc", "timezone_not_explicit",
                "paired experimental timestamps must carry Z or an explicit UTC offset",
            ))

        keys = [*PAIRED_IDENTIFIER_COLUMNS[1:], "timestamp_utc"]
        if all(column in data for column in keys):
            duplicates = int(data.duplicated(keys).sum())
            if duplicates:
                issues.append(ValidationIssue(
                    "+".join(keys), "duplicate_entity_timestamp",
                    f"{duplicates} duplicate paired entity timestamp(s)",
                ))
            for entity, group in data.groupby(keys[:-1], sort=False, observed=True):
                if not group["timestamp_utc"].is_monotonic_increasing:
                    issues.append(ValidationIssue(
                        "timestamp_utc", "entity_non_monotonic_timestamp",
                        f"timestamps go backward for entity {entity!r}",
                    ))

        if "dataset_id" in data and manifest.get("dataset_id") not in {None, "REPLACE_ME"}:
            mismatched = ~data["dataset_id"].astype(str).eq(str(manifest["dataset_id"]))
            if mismatched.any():
                issues.append(ValidationIssue(
                    "dataset_id", "manifest_data_mismatch",
                    f"{int(mismatched.sum())} row(s) differ from manifest dataset_id",
                ))

        if "alignment_error_ms" in data:
            declared = manifest.get("synchronization", {}).get("maximum_alignment_error_ms")
            if declared is None:
                issues.append(ValidationIssue(
                    "alignment_error_ms", "missing_alignment_bound",
                    "manifest must declare maximum_alignment_error_ms",
                ))
            else:
                exceeded = data["alignment_error_ms"].gt(float(declared)).fillna(False)
                if exceeded.any():
                    issues.append(ValidationIssue(
                        "alignment_error_ms", "alignment_bound_exceeded",
                        f"{int(exceeded.sum())} row(s) exceed declared {float(declared):g} ms",
                    ))

        fidelity_present = (
            "bell_fidelity_measured" in data
            and data["bell_fidelity_measured"].notna()
        )
        if isinstance(fidelity_present, pd.Series) and fidelity_present.any():
            for column in ("fidelity_measurement_method", "fidelity_standard_error", "fidelity_shots"):
                if column not in data:
                    continue
                missing = fidelity_present & data[column].isna()
                if missing.any():
                    issues.append(ValidationIssue(
                        column, "incomplete_fidelity_measurement_metadata",
                        f"{int(missing.sum())} measured-fidelity row(s) lack {column}",
                    ))

        if self.profile is PairedQuantumProfile.MEASURED_BELL_FIDELITY:
            if "bell_fidelity_measured" not in data or data["bell_fidelity_measured"].isna().any():
                issues.append(ValidationIssue(
                    "bell_fidelity_measured", "insufficient_measured_fidelity_coverage",
                    "strict Bell-fidelity profile requires a measured value on every row",
                ))

        if bool(manifest.get("contains_key_material", True)):
            issues.append(ValidationIssue(
                "contains_key_material", "prohibited_sensitive_payload",
                "acquisition bundles may contain performance metrics, never raw keys or key bits",
            ))
        return ValidationResult(is_valid=not issues, issues=issues)

    def require_valid(self, data: pd.DataFrame | None = None) -> pd.DataFrame:
        frame = self.read() if data is None else data
        validation = self.validate(frame)
        if not validation.is_valid:
            raise ValueError(validation.summary())
        assert_experimental_quantum_targets(frame, required_targets=self.required_targets())
        return frame

    def quality_report(self, data: pd.DataFrame | None = None) -> dict:
        frame = self.read() if data is None else data
        validation = self.validate(frame)
        fidelity_coverage = (
            float(frame["bell_fidelity_measured"].notna().mean())
            if "bell_fidelity_measured" in frame and len(frame) else 0.0
        )
        return {
            "rows": int(len(frame)),
            "campaigns": int(frame["campaign_id"].nunique()) if "campaign_id" in frame else 0,
            "devices": int(frame["device_id"].nunique()) if "device_id" in frame else 0,
            "channels": int(frame["channel_id"].nunique()) if "channel_id" in frame else 0,
            "start_utc": frame["timestamp_utc"].min().isoformat() if len(frame) else None,
            "end_utc": frame["timestamp_utc"].max().isoformat() if len(frame) else None,
            "measured_fidelity_coverage": fidelity_coverage,
            "validation_valid": validation.is_valid,
            "validation_issues": [issue.__dict__ for issue in validation.issues],
        }


@dataclass(frozen=True)
class AcquisitionBundle:
    data_files: tuple[Path, ...]
    manifest_path: Path
    dictionary_path: Path


def create_paired_quantum_acquisition_bundle(
    output_dir: str | Path,
    *,
    formats: tuple[str, ...] = ("csv",),
    dataset_id: str = "REPLACE_ME",
) -> AcquisitionBundle:
    """Create empty CSV/Parquet templates, manifest and field dictionary."""
    directory = Path(output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    empty = pd.DataFrame({column: pd.Series(dtype="object") for column in PAIRED_QUANTUM_TELEMETRY_COLUMNS})
    files: list[Path] = []
    normalized = tuple(value.casefold() for value in formats)
    unknown = set(normalized).difference({"csv", "parquet"})
    if unknown:
        raise ValueError(f"unsupported acquisition format(s): {sorted(unknown)}")
    for format_name in normalized:
        path = directory / f"paired_quantum_telemetry_template.{format_name}"
        if format_name == "csv":
            empty.to_csv(path, index=False)
        else:
            try:
                empty.to_parquet(path, index=False)
            except ImportError as exc:
                raise RuntimeError(
                    "Parquet template generation requires the optional pyarrow dependency"
                ) from exc
        files.append(path)

    manifest = json.loads(json.dumps(DEFAULT_PAIRED_MANIFEST))
    manifest["dataset_id"] = str(dataset_id)
    manifest_path = directory / "paired_quantum_telemetry.manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    dictionary_path = directory / "paired_quantum_data_dictionary.md"
    dictionary_path.write_text(
        """# Paired quantum telemetry acquisition dictionary

Every row is one synchronized observation for one campaign/device/channel.
`timestamp_utc` must include `Z` or an explicit UTC offset. `qber_ratio` is a
fraction in [0,1], never a percentage. `secure_key_rate_bps` is a performance
metric; raw keys/key bits are prohibited. `bell_fidelity_measured` may be null
for the measured-QKD profile, but a Bell-fidelity campaign requires method,
standard error and shot count. `alignment_error_ms` is the absolute optical
versus quantum clock/alignment error and must respect the manifest bound.

Optical fields use pre-FEC BER, OSNR in dB and received/transmit power in dBm.
Identifiers must remain stable across the complete acquisition campaign.
""",
        encoding="utf-8",
    )
    return AcquisitionBundle(tuple(files), manifest_path, dictionary_path)


__all__ = [
    "AcquisitionBundle",
    "DEFAULT_PAIRED_MANIFEST",
    "PAIRED_IDENTIFIER_COLUMNS",
    "PAIRED_OPTICAL_COLUMNS",
    "PAIRED_QUANTUM_COLUMNS",
    "PAIRED_QUANTUM_SCHEMA",
    "PAIRED_QUANTUM_TELEMETRY_COLUMNS",
    "PairedQuantumProfile",
    "PairedQuantumTelemetrySource",
    "assert_experimental_quantum_targets",
    "create_paired_quantum_acquisition_bundle",
    "experimental_provenance_issues",
]
