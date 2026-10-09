"""Adapter for the Scuola Superiore Sant'Anna optical-failure datasets.

The public CSV is a long, multi-device table: transponders report BER/OSNR
while amplifiers report input/output power.  The adapter preserves that fact
instead of interpreting structural missingness as bad data or averaging
different devices at a shared timestamp.
"""

from __future__ import annotations

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


SANTANNA_SCHEMA = TelemetrySchema(
    columns=[
        ColumnSpec("dataset_id", "string", "identifier", provenance=DataProvenance.IDENTIFIER,
                   role=ColumnRole.IDENTIFIER),
        ColumnSpec("campaign_id", "string", "identifier", provenance=DataProvenance.IDENTIFIER,
                   role=ColumnRole.IDENTIFIER),
        ColumnSpec("link_id", "string", "identifier", provenance=DataProvenance.STATIC,
                   role=ColumnRole.IDENTIFIER),
        ColumnSpec("device_type", "string", "category", provenance=DataProvenance.IDENTIFIER,
                   role=ColumnRole.IDENTIFIER),
        ColumnSpec("device_id", "string", "identifier", provenance=DataProvenance.IDENTIFIER,
                   role=ColumnRole.IDENTIFIER),
        ColumnSpec("channel_id", "string", "identifier", provenance=DataProvenance.IDENTIFIER,
                   role=ColumnRole.IDENTIFIER, required=False, nullable=True),
        ColumnSpec("direction", "string", "category", provenance=DataProvenance.STATIC,
                   role=ColumnRole.IDENTIFIER),
        ColumnSpec("pre_fec_ber", "float64", "unitless", 0.0, 1.0,
                   nullable=True),
        ColumnSpec("osnr_db", "float64", "dB", -20.0, 60.0, nullable=True),
        ColumnSpec("input_power_dbm", "float64", "dBm", -80.0, 20.0, nullable=True),
        ColumnSpec("output_power_dbm", "float64", "dBm", -80.0, 30.0, nullable=True),
        ColumnSpec("wdm_failure", "float64", "binary", 0.0, 1.0,
                   provenance=DataProvenance.LABEL, role=ColumnRole.TARGET,
                   nullable=True, aggregation="max"),
    ],
    timestamp_column="timestamp_utc",
    timestamp_required=True,
    requires_regular_sampling=False,
    expected_sampling_period_s=3.5,
)


class SantAnnaOpticalFailureSource(TelemetrySource):
    """Read and clean ``HardFailure_dataset.csv`` or ``SoftFailure_dataset.csv``.

    ``blank_failure_is_normal`` is intentionally explicit.  Set it to True
    only when adopting the dataset authors' indicator convention that blank
    rows are normal observations; otherwise blanks remain unknown.
    """

    RAW_COLUMNS = {
        "Timestamp", "Type", "ID", "BER", "OSNR",
        "InputPower", "OutputPower", "Failure",
    }

    def __init__(self, csv_path: str, dataset_id: str = "santanna-optical-failure",
                 campaign_id: str = None, link_id: str = "arno-240km",
                 blank_failure_is_normal: bool = False, ber_censor_floor: float = 1e-12):
        self.csv_path = str(csv_path)
        self.dataset_id = dataset_id
        self.campaign_id = campaign_id or Path(csv_path).stem
        self.link_id = link_id
        self.blank_failure_is_normal = bool(blank_failure_is_normal)
        self.ber_censor_floor = float(ber_censor_floor)

    def schema(self) -> TelemetrySchema:
        return SANTANNA_SCHEMA

    def read(self) -> pd.DataFrame:
        path = Path(self.csv_path)
        if not path.exists():
            raise FileNotFoundError(f"SantAnnaOpticalFailureSource: no such file '{path}'")

        raw = pd.read_csv(path, na_values=["", "NA", "NaN", "null"])
        missing = sorted(self.RAW_COLUMNS.difference(raw.columns))
        if missing:
            raise ValueError(f"Sant'Anna CSV is missing raw columns: {missing}")

        timestamp = pd.to_datetime(
            pd.to_numeric(raw["Timestamp"], errors="coerce"), unit="s", utc=True, errors="coerce"
        )
        failure = pd.to_numeric(raw["Failure"], errors="coerce")
        failure_was_blank = failure.isna()
        if self.blank_failure_is_normal:
            failure = failure.fillna(0.0)

        device_id = raw["ID"].astype("string").str.strip()
        is_transponder = raw["Type"].astype("string").str.casefold().eq("devices")
        channel_id = device_id.where(is_transponder).str.extract(r"(\d+/\d+)$", expand=False)

        frame = pd.DataFrame({
            "timestamp_utc": timestamp,
            "dataset_id": self.dataset_id,
            "campaign_id": self.campaign_id,
            "link_id": self.link_id,
            "device_type": np.where(is_transponder, "transponder", "amplifier"),
            "device_id": device_id,
            "channel_id": channel_id.astype("string"),
            # Direction cannot be recovered safely from the identifier alone.
            "direction": "unknown",
            "pre_fec_ber": pd.to_numeric(raw["BER"], errors="coerce"),
            "osnr_db": pd.to_numeric(raw["OSNR"], errors="coerce"),
            "input_power_dbm": pd.to_numeric(raw["InputPower"], errors="coerce"),
            "output_power_dbm": pd.to_numeric(raw["OutputPower"], errors="coerce"),
            "wdm_failure": failure,
            "wdm_failure_was_blank": failure_was_blank.astype(np.int8),
        })

        for column in ("pre_fec_ber", "osnr_db", "input_power_dbm", "output_power_dbm"):
            frame[f"{column}_is_missing"] = frame[column].isna().astype(np.int8)
        frame["pre_fec_ber_is_censored"] = (
            frame["pre_fec_ber"].notna() & (frame["pre_fec_ber"] <= self.ber_censor_floor)
        ).astype(np.int8)

        frame = frame.sort_values(["timestamp_utc", "device_id"], kind="stable").reset_index(drop=True)
        frame.attrs["telemetry_provenance"] = {
            "source_type": "recorded_hardware",
            "dataset_id": self.dataset_id,
            "campaign_id": self.campaign_id,
            "timestamp_timezone": "UTC",
            "sampling_period_s_documented": 3.5,
            "blank_failure_is_normal": self.blank_failure_is_normal,
            "target_provenance": {"wdm_failure": "recorded_optical_label"},
            "quantum_target_present": False,
        }
        return frame

    def validate(self, data: pd.DataFrame) -> ValidationResult:
        result = super().validate(data)
        # Repeated timestamps are expected in a long multi-device table.
        issues = [issue for issue in result.issues if issue.issue_type != "duplicate_timestamp"]
        key = ["timestamp_utc", "device_id"]
        if all(column in data.columns for column in key):
            duplicates = int(data.duplicated(key).sum())
            if duplicates:
                issues.append(ValidationIssue(
                    "+".join(key), "duplicate_entity_timestamp",
                    f"{duplicates} duplicate composite key(s) found",
                ))
        return ValidationResult(is_valid=not issues, issues=issues)

    def quality_report(self, data: pd.DataFrame = None) -> dict:
        frame = self.read() if data is None else data
        validation = self.validate(frame)
        coverage_columns = [
            "pre_fec_ber", "osnr_db", "input_power_dbm",
            "output_power_dbm", "wdm_failure",
        ]
        coverage = {
            column: float(frame[column].notna().mean())
            for column in coverage_columns if column in frame.columns
        }
        return {
            "rows": int(len(frame)),
            "timestamps": int(frame["timestamp_utc"].nunique()),
            "devices": int(frame["device_id"].nunique()),
            "start_utc": frame["timestamp_utc"].min().isoformat(),
            "end_utc": frame["timestamp_utc"].max().isoformat(),
            "coverage": coverage,
            "validation_valid": validation.is_valid,
            "validation_issues": [issue.__dict__ for issue in validation.issues],
        }

    @staticmethod
    def _safe_suffix(value: str) -> str:
        return re.sub(r"[^a-zA-Z0-9]+", "_", str(value)).strip("_").lower()

    def build_device_panel(self, data: pd.DataFrame = None, anchor_device: str = "SPO1/18/11",
                           tolerance_s: float = 2.0) -> pd.DataFrame:
        """Create link snapshots without averaging measurements across devices."""
        frame = self.read() if data is None else data.copy()
        if anchor_device not in set(frame["device_id"].dropna()):
            raise ValueError(f"anchor_device {anchor_device!r} not found")

        metrics = ["pre_fec_ber", "osnr_db", "input_power_dbm", "output_power_dbm", "wdm_failure"]
        anchor = frame.loc[frame["device_id"] == anchor_device, ["timestamp_utc"] + metrics].copy()
        suffix = self._safe_suffix(anchor_device)
        anchor = anchor.rename(columns={column: f"{column}__{suffix}" for column in metrics})
        panel = anchor.sort_values("timestamp_utc")

        for device in sorted(set(frame["device_id"].dropna()) - {anchor_device}):
            suffix = self._safe_suffix(device)
            device_frame = frame.loc[frame["device_id"] == device, ["timestamp_utc"] + metrics].copy()
            device_frame = device_frame.rename(
                columns={column: f"{column}__{suffix}" for column in metrics}
            ).sort_values("timestamp_utc")
            panel = pd.merge_asof(
                panel.sort_values("timestamp_utc"), device_frame,
                on="timestamp_utc", direction="nearest",
                tolerance=pd.Timedelta(seconds=tolerance_s),
            )
        return panel.reset_index(drop=True)

    def build_causal_device_panel(
        self,
        data: pd.DataFrame = None,
        anchor_device: str = "SPO1/18/11",
        tolerance_s: float = 5.0,
    ) -> pd.DataFrame:
        """Create a forecasting panel using only observations available at issue time.

        The anchor transponder defines the forecast timestamps.  Every other
        device is joined with a backward ``merge_asof`` and therefore can
        contribute only a sample whose timestamp is less than or equal to the
        anchor timestamp.  ``observed_at`` and ``age_s`` columns make this
        causal alignment auditable and allow a model to distinguish a fresh
        sensor value from a stale one.

        ``build_device_panel`` remains the symmetric snapshot helper for
        descriptive analyses.  Forecasting code should use this method.
        """
        if tolerance_s < 0:
            raise ValueError("tolerance_s must be >= 0")
        frame = self.read() if data is None else data.copy()
        available_devices = set(frame["device_id"].dropna().astype(str))
        if anchor_device not in available_devices:
            raise ValueError(f"anchor_device {anchor_device!r} not found")
        anchor_type = frame.loc[
            frame["device_id"].astype(str).eq(anchor_device), "device_type"
        ].dropna().unique()
        if len(anchor_type) != 1 or anchor_type[0] != "transponder":
            raise ValueError("anchor_device must identify exactly one transponder")

        metrics = [
            "pre_fec_ber", "osnr_db", "input_power_dbm",
            "output_power_dbm", "wdm_failure",
        ]
        identifiers = [
            "timestamp_utc", "dataset_id", "campaign_id", "link_id",
            "device_id", "channel_id", "direction",
        ]
        anchor = frame.loc[
            frame["device_id"].astype(str).eq(anchor_device), identifiers + metrics
        ].copy()
        anchor = anchor.rename(columns={"device_id": "anchor_device_id"})
        anchor_suffix = self._safe_suffix(anchor_device)
        anchor[f"observed_at__{anchor_suffix}"] = anchor["timestamp_utc"]
        anchor[f"age_s__{anchor_suffix}"] = 0.0
        panel = anchor.sort_values("timestamp_utc", kind="stable")

        for device in sorted(available_devices - {anchor_device}):
            suffix = self._safe_suffix(device)
            observed_at = f"observed_at__{suffix}"
            device_frame = frame.loc[
                frame["device_id"].astype(str).eq(device), ["timestamp_utc"] + metrics
            ].copy()
            device_frame = device_frame.rename(
                columns={
                    "timestamp_utc": observed_at,
                    **{column: f"{column}__{suffix}" for column in metrics},
                }
            ).sort_values(observed_at, kind="stable")
            panel = pd.merge_asof(
                panel.sort_values("timestamp_utc", kind="stable"),
                device_frame,
                left_on="timestamp_utc",
                right_on=observed_at,
                direction="backward",
                tolerance=pd.Timedelta(seconds=float(tolerance_s)),
                allow_exact_matches=True,
            )
            panel[f"age_s__{suffix}"] = (
                panel["timestamp_utc"] - panel[observed_at]
            ).dt.total_seconds()

        observed_columns = [
            column for column in panel.columns if column.startswith("observed_at__")
        ]
        for column in observed_columns:
            matched = panel[column].notna()
            if (panel.loc[matched, column] > panel.loc[matched, "timestamp_utc"]).any():
                raise RuntimeError(f"causal alignment selected a future sample in {column}")
        return panel.reset_index(drop=True)

    def build_causal_forecast_panel(
        self,
        data: pd.DataFrame = None,
        target: str = "osnr_db",
        horizon_steps: int = 1,
        anchor_devices: list[str] | tuple[str, ...] | None = None,
        tolerance_s: float = 5.0,
    ) -> pd.DataFrame:
        """Build causal multi-equipment features and a device-local future label."""
        if horizon_steps < 1:
            raise ValueError("horizon_steps must be >= 1")
        if target not in {"osnr_db", "pre_fec_ber"}:
            raise ValueError("target must be osnr_db or pre_fec_ber")
        frame = self.read() if data is None else data.copy()
        if anchor_devices is None:
            anchors = sorted(
                frame.loc[frame["device_type"].eq("transponder"), "device_id"]
                .dropna().astype(str).unique().tolist()
            )
        else:
            anchors = [str(value) for value in anchor_devices]
        if not anchors:
            raise ValueError("at least one transponder anchor is required")

        panels: list[pd.DataFrame] = []
        for anchor in anchors:
            panel = self.build_causal_device_panel(
                frame, anchor_device=anchor, tolerance_s=tolerance_s
            )
            panel = panel.sort_values("timestamp_utc", kind="stable").reset_index(drop=True)
            panel["target_value_raw"] = panel[target].shift(-horizon_steps)
            panel["target_timestamp_utc"] = panel["timestamp_utc"].shift(-horizon_steps)
            panel["horizon_steps"] = int(horizon_steps)
            panels.append(panel.dropna(subset=["target_value_raw"]))
        return pd.concat(panels, ignore_index=True).sort_values(
            ["anchor_device_id", "timestamp_utc"], kind="stable"
        ).reset_index(drop=True)

    def build_optical_forecast_frame(self, data: pd.DataFrame = None,
                                     target: str = "osnr_db", horizon_steps: int = 1,
                                     device_type: str = "transponder") -> pd.DataFrame:
        """Attach a future optical label without crossing device boundaries."""
        if horizon_steps < 1:
            raise ValueError("horizon_steps must be >= 1")
        frame = self.read() if data is None else data.copy()
        if target not in {"osnr_db", "pre_fec_ber", "wdm_failure"}:
            raise ValueError("target must be osnr_db, pre_fec_ber, or wdm_failure")
        selected = frame.loc[frame["device_type"] == device_type].copy()
        selected = selected.sort_values(["device_id", "timestamp_utc"], kind="stable")
        selected[f"target_{target}"] = selected.groupby("device_id", sort=False)[target].shift(-horizon_steps)
        selected["target_timestamp_utc"] = selected.groupby("device_id", sort=False)[
            "timestamp_utc"
        ].shift(-horizon_steps)
        return selected.dropna(subset=[f"target_{target}"]).reset_index(drop=True)


__all__ = ["SANTANNA_SCHEMA", "SantAnnaOpticalFailureSource"]
