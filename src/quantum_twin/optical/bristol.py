"""Adapter for the University of Bristol one-month NDFF telemetry release."""

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


BRISTOL_NDFF_SCHEMA = TelemetrySchema(
    columns=[
        ColumnSpec(
            "dataset_id", "string", "identifier",
            provenance=DataProvenance.IDENTIFIER, role=ColumnRole.IDENTIFIER,
        ),
        ColumnSpec(
            "vendor", "string", "category",
            provenance=DataProvenance.IDENTIFIER, role=ColumnRole.IDENTIFIER,
        ),
        ColumnSpec(
            "channel_id", "string", "identifier",
            provenance=DataProvenance.IDENTIFIER, role=ColumnRole.IDENTIFIER,
        ),
        ColumnSpec("pre_fec_ber", "float64", "unitless", 0.0, 1.0),
        ColumnSpec("q_factor_db", "float64", "dB", -20.0, 50.0),
        ColumnSpec(
            "received_power_dbm", "float64", "dBm", -80.0, 20.0,
            required=False, nullable=True,
        ),
    ],
    timestamp_column="timestamp_local_unzoned",
    timestamp_required=True,
    requires_regular_sampling=False,
    expected_sampling_period_s=900.0,
)


class BristolNDFFTelemetrySource(TelemetrySource):
    """Canonicalize the three public 15-minute multivendor CSVs.

    The source README does not declare a timezone, so timestamps deliberately
    remain timezone-naive and are named ``timestamp_local_unzoned``.
    """

    FILES = {
        "ADVA": "ADVA_onemonth_15minsampling.csv",
        "Ciena": "Ciena_onemonth_15minsampling.csv",
        "Voyager": "Voyager_onemonth_15minsampling.csv",
    }

    def __init__(
        self,
        dataset_dir: str | Path,
        dataset_id: str = "bristol-ndff-one-month-339km",
    ):
        self.dataset_dir = Path(dataset_dir)
        self.dataset_id = str(dataset_id)

    def schema(self) -> TelemetrySchema:
        return BRISTOL_NDFF_SCHEMA

    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    @staticmethod
    def _timestamps(raw: pd.DataFrame, path: Path) -> pd.Series:
        values = pd.to_datetime(raw["Date"], format="%d/%m/%Y %H:%M", errors="coerce")
        if values.isna().any():
            raise ValueError(f"{path.name} contains unparseable Date values")
        return values

    def _read_adva(self, path: Path) -> pd.DataFrame:
        raw = pd.read_csv(path)
        required = {"Date", "BER1", "Qfactor1", "BER2", "Qfactor2"}
        if not required.issubset(raw.columns):
            raise ValueError(f"{path.name} is missing columns: {sorted(required - set(raw))}")
        timestamps = self._timestamps(raw, path)
        frames = []
        for channel in (1, 2):
            frames.append(pd.DataFrame({
                "timestamp_local_unzoned": timestamps,
                "vendor": "ADVA",
                "channel_id": f"ADVA-{channel}",
                "pre_fec_ber": pd.to_numeric(raw[f"BER{channel}"], errors="coerce"),
                "q_factor_db": pd.to_numeric(raw[f"Qfactor{channel}"], errors="coerce"),
                "received_power_dbm": np.nan,
            }))
        return pd.concat(frames, ignore_index=True)

    def _read_ciena(self, path: Path) -> pd.DataFrame:
        raw = pd.read_csv(path)
        required = {"Date", "OPR-OCH", "PREBER Value", "QAVG Value"}
        if not required.issubset(raw.columns):
            raise ValueError(f"{path.name} is missing columns: {sorted(required - set(raw))}")
        return pd.DataFrame({
            "timestamp_local_unzoned": self._timestamps(raw, path),
            "vendor": "Ciena",
            "channel_id": "Ciena-1",
            "pre_fec_ber": pd.to_numeric(raw["PREBER Value"], errors="coerce"),
            "q_factor_db": pd.to_numeric(raw["QAVG Value"], errors="coerce"),
            "received_power_dbm": pd.to_numeric(raw["OPR-OCH"], errors="coerce"),
        })

    def _read_voyager(self, path: Path) -> pd.DataFrame:
        raw = pd.read_csv(path)
        required = {"Date"} | {f"channel{i}" for i in range(1, 5)} | {
            f"Qfactor{i}" for i in range(1, 5)
        }
        if not required.issubset(raw.columns):
            raise ValueError(f"{path.name} is missing columns: {sorted(required - set(raw))}")
        timestamps = self._timestamps(raw, path)
        frames = []
        for channel in range(1, 5):
            frames.append(pd.DataFrame({
                "timestamp_local_unzoned": timestamps,
                "vendor": "Voyager",
                "channel_id": f"Voyager-{channel}",
                "pre_fec_ber": pd.to_numeric(raw[f"channel{channel}"], errors="coerce"),
                "q_factor_db": pd.to_numeric(raw[f"Qfactor{channel}"], errors="coerce"),
                "received_power_dbm": np.nan,
            }))
        return pd.concat(frames, ignore_index=True)

    def read(self) -> pd.DataFrame:
        readers = {"ADVA": self._read_adva, "Ciena": self._read_ciena, "Voyager": self._read_voyager}
        frames: list[pd.DataFrame] = []
        hashes: dict[str, str] = {}
        for vendor, name in self.FILES.items():
            path = self.dataset_dir / name
            if not path.exists():
                raise FileNotFoundError(f"Bristol NDFF file not found: {path}")
            frame = readers[vendor](path)
            frame["source_file"] = name
            frames.append(frame)
            hashes[name] = self._sha256(path)
        result = pd.concat(frames, ignore_index=True)
        result.insert(0, "dataset_id", self.dataset_id)
        result = result.sort_values(
            ["timestamp_local_unzoned", "channel_id"], kind="stable"
        ).reset_index(drop=True)
        result.attrs["telemetry_provenance"] = {
            "source_type": "recorded_hardware",
            "dataset_id": self.dataset_id,
            "field_trial_length_km": 339.0,
            "timestamp_timezone": "unknown/unzoned",
            "sampling": "raw 10 seconds; publisher-provided 15-minute aggregate",
            "quantum_target_present": False,
            "license": "academic research and non-commercial use",
            "file_sha256": hashes,
        }
        return result

    def validate(self, data: pd.DataFrame) -> ValidationResult:
        base = super().validate(data)
        issues = [issue for issue in base.issues if issue.issue_type != "duplicate_timestamp"]
        keys = ["timestamp_local_unzoned", "channel_id"]
        if all(column in data for column in keys):
            duplicates = int(data.duplicated(keys).sum())
            if duplicates:
                issues.append(ValidationIssue(
                    "+".join(keys), "duplicate_entity_timestamp",
                    f"{duplicates} duplicate channel timestamp(s)",
                ))
        return ValidationResult(is_valid=not issues, issues=issues)

    def quality_report(self, data: pd.DataFrame | None = None) -> dict:
        frame = self.read() if data is None else data
        validation = self.validate(frame)
        return {
            "rows": int(len(frame)),
            "channels": int(frame["channel_id"].nunique()),
            "vendors": sorted(frame["vendor"].unique().tolist()),
            "start_local_unzoned": frame["timestamp_local_unzoned"].min().isoformat(),
            "end_local_unzoned": frame["timestamp_local_unzoned"].max().isoformat(),
            "ber_range": [
                float(frame["pre_fec_ber"].min()), float(frame["pre_fec_ber"].max())
            ],
            "q_factor_db_range": [
                float(frame["q_factor_db"].min()), float(frame["q_factor_db"].max())
            ],
            "received_power_coverage": float(frame["received_power_dbm"].notna().mean()),
            "validation_valid": validation.is_valid,
            "validation_issues": [issue.__dict__ for issue in validation.issues],
        }

    @staticmethod
    def _safe_suffix(value: str) -> str:
        return re.sub(r"[^a-zA-Z0-9]+", "_", str(value)).strip("_").lower()

    def build_causal_channel_panel(
        self,
        data: pd.DataFrame,
        *,
        anchor_channel: str,
        tolerance_s: float = 1_200.0,
    ) -> pd.DataFrame:
        """Align all other channels backward to one anchor channel.

        ``data`` must contain ``simulated_bell_fidelity`` when this helper is
        used for quantum replay.  Other channels contribute recorded optical
        telemetry only; their simulated fidelity is never exposed as a model
        feature.
        """
        if tolerance_s < 0:
            raise ValueError("tolerance_s must be >= 0")
        if "simulated_bell_fidelity" not in data:
            raise ValueError("data must contain simulated_bell_fidelity")
        channels = set(data["channel_id"].astype(str).unique())
        if anchor_channel not in channels:
            raise ValueError(f"anchor_channel {anchor_channel!r} not found")
        timestamp = "timestamp_local_unzoned"
        anchor = data.loc[data["channel_id"].astype(str).eq(anchor_channel), [
            timestamp, "vendor", "channel_id", "simulated_bell_fidelity",
        ]].copy()
        anchor = anchor.rename(columns={
            "vendor": "anchor_vendor", "channel_id": "anchor_channel_id"
        }).sort_values(timestamp, kind="stable")
        panel = anchor
        for channel in sorted(channels - {anchor_channel}):
            suffix = self._safe_suffix(channel)
            observed_at = f"observed_at__{suffix}"
            metrics = ["pre_fec_ber", "q_factor_db", "received_power_dbm"]
            other = data.loc[data["channel_id"].astype(str).eq(channel), [timestamp] + metrics].copy()
            other = other.rename(columns={
                timestamp: observed_at,
                **{metric: f"{metric}__{suffix}" for metric in metrics},
            }).sort_values(observed_at, kind="stable")
            panel = pd.merge_asof(
                panel.sort_values(timestamp, kind="stable"),
                other,
                left_on=timestamp,
                right_on=observed_at,
                direction="backward",
                tolerance=pd.Timedelta(seconds=float(tolerance_s)),
                allow_exact_matches=True,
            )
            panel[f"age_s__{suffix}"] = (
                panel[timestamp] - panel[observed_at]
            ).dt.total_seconds()
        observed_columns = [
            column for column in panel if column.startswith("observed_at__")
        ]
        for column in observed_columns:
            matched = panel[column].notna()
            if (panel.loc[matched, column] > panel.loc[matched, timestamp]).any():
                raise RuntimeError(f"future Bristol sample selected in {column}")
        return panel.reset_index(drop=True)


__all__ = ["BRISTOL_NDFF_SCHEMA", "BristolNDFFTelemetrySource"]
