"""Adapter for the public 168-hour Dryad STIKE prototype series.

The experiment is a one-way quasi-QSDC/STIKE demonstration.  Its published
``Rate`` variable is an information/communication rate, not a secure key rate.
The release contains no WDM telemetry and no absolute timestamps.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
import re

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


DRYAD_STIKE_DOI = "10.5061/dryad.n2z34tn70"
DRYAD_STIKE_EXPECTED_SAMPLES = 168
DRYAD_STIKE_VERSION_4_SHA256 = {
    "QBER_168h.txt": "0e13705f05285d848454d1f70625a16f3e61ea611e0838cd44eb37507eb6ddd5",
    "Rate_168h.txt": "a71d900a37afaee6c54e3456d82a836c3e358d01c11018dfdd568340af49c47a",
}
DRYAD_STIKE_SCHEMA = TelemetrySchema(
    columns=[
        ColumnSpec(
            "dataset_id", "string", "identifier",
            provenance=DataProvenance.IDENTIFIER, role=ColumnRole.IDENTIFIER,
        ),
        ColumnSpec(
            "campaign_id", "string", "identifier",
            provenance=DataProvenance.IDENTIFIER, role=ColumnRole.IDENTIFIER,
        ),
        ColumnSpec(
            "sample_index", "int64", "index", 0.0, 167.0,
            provenance=DataProvenance.IDENTIFIER, role=ColumnRole.IDENTIFIER,
        ),
        ColumnSpec(
            "relative_hour", "float64", "hour", 0.0, 167.0,
            provenance=DataProvenance.DERIVED, role=ColumnRole.IDENTIFIER,
        ),
        ColumnSpec(
            "qber_reported_percent", "float64", "percent", 0.0, 100.0,
            provenance=DataProvenance.LABEL, role=ColumnRole.TARGET,
        ),
        ColumnSpec(
            "qber_measured", "float64", "proportion", 0.0, 1.0,
            provenance=DataProvenance.DERIVED, role=ColumnRole.TARGET,
        ),
        ColumnSpec(
            "communication_rate_reported_kbps", "float64", "kbit/s", 0.0, None,
            provenance=DataProvenance.LABEL, role=ColumnRole.TARGET,
        ),
        ColumnSpec(
            "communication_rate_bps", "float64", "bit/s", 0.0, None,
            provenance=DataProvenance.DERIVED, role=ColumnRole.TARGET,
        ),
    ],
    timestamp_required=False,
    requires_regular_sampling=True,
    expected_sampling_period_s=3600.0,
)


_NUMBER = re.compile(r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?")


class DryadSTIKETelemetrySource(TelemetrySource):
    """Read ``QBER_168h.txt`` and ``Rate_168h.txt`` without unit guessing."""

    def __init__(
        self,
        dataset_dir: str | Path,
        dataset_id: str = "dryad-stike-168h-n2z34tn70",
        verify_publisher_hashes: bool = True,
    ):
        self.dataset_dir = Path(dataset_dir)
        self.dataset_id = str(dataset_id)
        self.verify_publisher_hashes = bool(verify_publisher_hashes)

    def schema(self) -> TelemetrySchema:
        return DRYAD_STIKE_SCHEMA

    def _file(self, name: str) -> Path:
        if not self.dataset_dir.exists():
            raise FileNotFoundError(f"Dryad STIKE dataset directory not found: {self.dataset_dir}")
        direct = self.dataset_dir / name
        if direct.is_file():
            return direct
        candidates = sorted(self.dataset_dir.rglob(name))
        if not candidates:
            raise FileNotFoundError(f"{name} not found below {self.dataset_dir}")
        if len(candidates) > 1:
            raise ValueError(f"multiple {name} files found below {self.dataset_dir}")
        return candidates[0]

    @staticmethod
    def _read_vector(path: Path) -> np.ndarray:
        text = path.read_text(encoding="utf-8-sig")
        values = np.asarray([float(token) for token in _NUMBER.findall(text)], dtype=float)
        if len(values) != DRYAD_STIKE_EXPECTED_SAMPLES:
            raise ValueError(
                f"{path.name} must contain exactly 168 hourly values; found {len(values)}"
            )
        if not np.isfinite(values).all():
            raise ValueError(f"{path.name} contains non-finite values")
        return values

    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def read(self) -> pd.DataFrame:
        qber_path = self._file("QBER_168h.txt")
        rate_path = self._file("Rate_168h.txt")
        qber_percent = self._read_vector(qber_path)
        rate_kbps = self._read_vector(rate_path)
        hashes = {
            qber_path.name: self._sha256(qber_path),
            rate_path.name: self._sha256(rate_path),
        }
        if self.verify_publisher_hashes:
            mismatches = {
                name: {"expected": expected, "observed": hashes.get(name)}
                for name, expected in DRYAD_STIKE_VERSION_4_SHA256.items()
                if hashes.get(name) != expected
            }
            if mismatches:
                raise ValueError(
                    "Dryad STIKE files do not match the published version-4 SHA-256 "
                    f"digests: {mismatches}"
                )
        sample_index = np.arange(DRYAD_STIKE_EXPECTED_SAMPLES, dtype=np.int64)
        result = pd.DataFrame({
            "dataset_id": self.dataset_id,
            "campaign_id": "stike-104.8-km-168h",
            "sample_index": sample_index,
            "relative_hour": sample_index.astype(float),
            "qber_reported_percent": qber_percent,
            "qber_measured": qber_percent / 100.0,
            "communication_rate_reported_kbps": rate_kbps,
            "communication_rate_bps": rate_kbps * 1000.0,
        })
        result.attrs["telemetry_provenance"] = {
            "source_type": "recorded_hardware",
            "dataset_id": self.dataset_id,
            "doi": DRYAD_STIKE_DOI,
            "license": "CC0-1.0",
            "protocol": "STIKE one-way quasi-QSDC",
            "fiber_length_km": 104.8,
            "sampling_interval": "one published average per hour",
            "quantum_target_present": True,
            "quantum_target_kind": "measured_qber",
            "communication_target_kind": "measured_information_communication_rate",
            "secure_key_rate_present": False,
            "bell_fidelity_present": False,
            "timestamp_present": False,
            "relative_order_present": True,
            "time_varying_wdm_telemetry_present": False,
            "wdm_ber_osnr_power_timeseries_present": False,
            "source_file_sha256": hashes,
            "publisher_hash_verification_enabled": self.verify_publisher_hashes,
            "unit_basis": (
                "The paper reports 3.60% mean QBER, 2.38 kbit/s mean information rate, "
                "and one-hour averages. Published scalars are preserved before conversion."
            ),
        }
        return result

    def validate(self, data: pd.DataFrame) -> ValidationResult:
        base = super().validate(data)
        issues = list(base.issues)
        if len(data) != DRYAD_STIKE_EXPECTED_SAMPLES:
            issues.append(ValidationIssue(
                "sample_index", "invalid_sample_count",
                f"expected 168 hourly observations, found {len(data)}",
            ))
        if "sample_index" in data:
            observed = pd.to_numeric(data["sample_index"], errors="coerce").to_numpy()
            expected = np.arange(len(data))
            if len(observed) != len(expected) or not np.array_equal(observed, expected):
                issues.append(ValidationIssue(
                    "sample_index", "non_contiguous_relative_order",
                    "sample indices must be exactly 0..167 in publisher order",
                ))
        if "relative_hour" in data and "sample_index" in data:
            if not np.allclose(
                pd.to_numeric(data["relative_hour"], errors="coerce"),
                pd.to_numeric(data["sample_index"], errors="coerce"),
                equal_nan=False,
            ):
                issues.append(ValidationIssue(
                    "relative_hour", "invalid_relative_time",
                    "relative_hour must equal the hourly sample index",
                ))
        return ValidationResult(is_valid=not issues, issues=issues)

    def quality_report(self, data: pd.DataFrame | None = None) -> dict:
        frame = self.read() if data is None else data
        validation = self.validate(frame)
        return {
            "rows": int(len(frame)),
            "duration_hours": int(len(frame)),
            "qber_mean_percent": float(frame["qber_reported_percent"].mean()),
            "communication_rate_mean_kbps": float(
                frame["communication_rate_reported_kbps"].mean()
            ),
            "absolute_timestamps_available": False,
            "wdm_telemetry_available": False,
            "secure_key_rate_available": False,
            "bell_fidelity_available": False,
            "validation_valid": validation.is_valid,
            "validation_issues": [issue.__dict__ for issue in validation.issues],
        }


__all__ = [
    "DRYAD_STIKE_DOI",
    "DRYAD_STIKE_EXPECTED_SAMPLES",
    "DRYAD_STIKE_SCHEMA",
    "DRYAD_STIKE_VERSION_4_SHA256",
    "DryadSTIKETelemetrySource",
]
