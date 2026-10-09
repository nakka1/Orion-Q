"""Adapter for the IrelandQCI/Toshiba experimental QKD coexistence dataset.

The source contains measured QBER and secure-key-rate outcomes from controlled
attenuation sweeps with and without an injected classical optical signal.  It
therefore supplies genuine quantum-system targets, but not Bell-pair fidelity
and not a timestamped forecasting sequence.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import numpy as np
import pandas as pd

from quantum_twin.optical.interface import (
    ColumnRole,
    ColumnSpec,
    DataProvenance,
    TelemetrySchema,
    TelemetrySource,
    ValidationResult,
)


QKD_COEXISTENCE_SCHEMA = TelemetrySchema(
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
            "sample_order", "int64", "index",
            provenance=DataProvenance.IDENTIFIER, role=ColumnRole.IDENTIFIER,
        ),
        ColumnSpec("external_signal_present", "int8", "binary", 0.0, 1.0),
        ColumnSpec(
            "external_signal_power_dbm", "float64", "dBm", -30.0, 30.0,
            nullable=True,
        ),
        ColumnSpec("fiber_length_km", "float64", "km", 0.0, None),
        ColumnSpec("voa_attenuation_db", "float64", "dB", 0.0, 100.0),
        ColumnSpec(
            "qber_reported", "float64", "publisher scale: ratio or percent (ambiguous)",
            0.0, 100.0,
            provenance=DataProvenance.LABEL, role=ColumnRole.TARGET,
        ),
        ColumnSpec(
            "secure_key_rate_bps", "float64", "bit/s", 0.0, None,
            provenance=DataProvenance.LABEL, role=ColumnRole.TARGET,
        ),
    ],
    timestamp_required=False,
    requires_regular_sampling=False,
)


class QKDCoexistenceSource(TelemetrySource):
    """Read all measurement CSVs from Zenodo record ``21132088``."""

    RAW_COLUMNS = {
        "Noise_dBm", "Sweep_km", "VOA_dB", "QBER", "SecureKeyRate_bps",
    }

    def __init__(
        self,
        dataset_dir: str | Path,
        dataset_id: str = "irelandqci-toshiba-qkd-coexistence-21132088",
    ):
        self.dataset_dir = Path(dataset_dir)
        self.dataset_id = str(dataset_id)

    def schema(self) -> TelemetrySchema:
        return QKD_COEXISTENCE_SCHEMA

    def measurement_files(self) -> list[Path]:
        if not self.dataset_dir.exists():
            raise FileNotFoundError(f"QKD coexistence directory not found: {self.dataset_dir}")
        files = sorted(self.dataset_dir.glob("*.csv"))
        if not files:
            raise FileNotFoundError(f"no measurement CSVs found in {self.dataset_dir}")
        return files

    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def read(self) -> pd.DataFrame:
        frames: list[pd.DataFrame] = []
        hashes: dict[str, str] = {}
        for path in self.measurement_files():
            raw = pd.read_csv(path)
            missing = sorted(self.RAW_COLUMNS.difference(raw.columns))
            if missing:
                raise ValueError(f"{path.name} is missing QKD columns: {missing}")
            noise_numeric = pd.to_numeric(raw["Noise_dBm"], errors="coerce")
            normalized_noise = raw["Noise_dBm"].astype("string").str.strip().str.casefold()
            # Pandas may parse the publisher's ``None`` baseline token as
            # missing.  Both representations mean no injected signal.
            baseline = raw["Noise_dBm"].isna() | normalized_noise.isin(
                ["", "none", "nan", "null"]
            )
            signal_present = ~baseline
            if noise_numeric[signal_present].isna().any():
                raise ValueError(f"{path.name} contains an invalid injected-signal level")
            qber = pd.to_numeric(raw["QBER"], errors="coerce")
            frame = pd.DataFrame({
                "dataset_id": self.dataset_id,
                "campaign_id": path.stem,
                "source_file": path.name,
                "sample_order": np.arange(len(raw), dtype=np.int64),
                "external_signal_present": signal_present.astype(np.int8),
                "external_signal_power_dbm": noise_numeric.astype(float),
                "fiber_length_km": pd.to_numeric(raw["Sweep_km"], errors="coerce"),
                "voa_attenuation_db": pd.to_numeric(raw["VOA_dB"], errors="coerce"),
                # The publisher's dictionary says "ratio (or %)", so the
                # scalar is preserved without silently choosing a scale.
                "qber_reported": qber,
                "secure_key_rate_bps": pd.to_numeric(
                    raw["SecureKeyRate_bps"], errors="coerce"
                ),
            })
            frames.append(frame)
            hashes[path.name] = self._sha256(path)

        result = pd.concat(frames, ignore_index=True)
        result = result.sort_values(
            ["fiber_length_km", "external_signal_present",
             "external_signal_power_dbm", "voa_attenuation_db", "sample_order"],
            kind="stable", na_position="first",
        ).reset_index(drop=True)
        result.attrs["telemetry_provenance"] = {
            "source_type": "recorded_hardware",
            "dataset_id": self.dataset_id,
            "doi": "10.5281/zenodo.21132088",
            "license": "CC-BY-4.0",
            "quantum_target_present": True,
            "quantum_target_kind": "measured_qkd_qber_and_secure_key_rate",
            "qber_unit_status": (
                "ambiguous: publisher data dictionary states 'ratio (or %)'; "
                "qber_reported preserves the published scalar without conversion"
            ),
            "bell_fidelity_present": False,
            "timestamp_present": False,
            "experimental_design": "controlled attenuation sweeps",
            "file_sha256": hashes,
        }
        return result

    def validate(self, data: pd.DataFrame) -> ValidationResult:
        return super().validate(data)

    def quality_report(self, data: pd.DataFrame | None = None) -> dict:
        frame = self.read() if data is None else data
        validation = self.validate(frame)
        cells = frame.groupby(
            ["fiber_length_km", "voa_attenuation_db", "external_signal_present",
             "external_signal_power_dbm"],
            dropna=False,
            observed=True,
        ).ngroups
        return {
            "rows": int(len(frame)),
            "campaigns": int(frame["campaign_id"].nunique()),
            "experimental_cells": int(cells),
            "fiber_lengths_km": sorted(
                float(value) for value in frame["fiber_length_km"].unique()
            ),
            "voa_levels": int(frame["voa_attenuation_db"].nunique()),
            "injected_signal_levels_dbm": sorted(
                float(value) for value in frame["external_signal_power_dbm"].dropna().unique()
            ),
            "qber_reported_range": [
                float(frame["qber_reported"].min()),
                float(frame["qber_reported"].max()),
            ],
            "secure_key_rate_range_bps": [
                float(frame["secure_key_rate_bps"].min()),
                float(frame["secure_key_rate_bps"].max()),
            ],
            "validation_valid": validation.is_valid,
            "validation_issues": [issue.__dict__ for issue in validation.issues],
        }


__all__ = ["QKD_COEXISTENCE_SCHEMA", "QKDCoexistenceSource"]
