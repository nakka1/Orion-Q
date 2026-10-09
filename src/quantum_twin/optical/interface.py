"""
quantum_twin/optical/interface.py
=====================================

REAL implementation of the formal TelemetrySource interface
(read()/schema()/validate()) with real preprocessing utilities --
migrated here from root-level telemetry_interface.py in the one
-hundred-eleventh addendum (TCC item 6, second migration wave). Unlike
the eighty-fifth through one-hundred-seventh addenda's 22 modules,
this file never had a corresponding quantum_twin/ location to invert.

A REAL, PRE-EXISTING NAMING COLLISION, deliberately preserved as-is: this
module's OWN TelemetrySource/CSVTelemetrySource classes share their
names with genuinely DIFFERENT classes already migrated into
quantum_twin/optical/sources.py (from telemetry_source.py, eighty
-eighth addendum) -- the two have always coexisted in this project
(this module's own original docstring explicitly documents the
coexistence: "This is a NEW, formal contract living alongside [...] the
existing telemetry_source.py"), distinguished by which FILE they live
in, not by name alone. To avoid silently overwriting one shim mapping
with the other, `quantum_twin/optical/__init__.py`'s lazy
name-to-submodule mapping was deliberately NOT updated with
TelemetrySource/CSVTelemetrySource from this module -- package-level
access (`quantum_twin.optical.TelemetrySource`) continues resolving to
sources.py's version exactly as before; this module's own classes
remain accessible via their explicit submodule path
(`quantum_twin.optical.interface.TelemetrySource`), preserving full
backward compatibility for both.

Root-level telemetry_interface.py is now the thin re-export (direction
reversed), kept so its one pre-existing consumer
(tests/test_telemetry_interface.py) continues to work completely
UNCHANGED. This module's own internal imports of PhysicsConfig/
QuantumNetworkDatasetV3 were updated to their real
quantum_twin.core.config/quantum_twin.optical.wdm locations instead of
the reversed root shims.

Original module docstring, preserved from the migrated source:

Master prompt Fase 5: a formal TelemetrySource interface
(read() / schema() / validate()) with real preprocessing utilities
for the messy realities of actual telemetry: timestamps, synchronization,
missing values, outliers, irregular sampling, resampling, normalization,
calibration, units, and schema validation.

This is a NEW, formal contract living alongside (not replacing) the
existing telemetry_source.py (generate_dataset()/preprocess()/
input_size), which remains fully functional and used throughout the
rest of this project's dataset pipeline. telemetry_interface.py
provides the specific read/schema/validate shape the master prompt names
explicitly, wrapping the existing dataset generator internally for the
synthetic case rather than duplicating its physics.

    WDM real
    -> TelemetrySource.read()
    -> TelemetrySource.validate() (schema, units, ranges)
    -> preprocessing (missing values, outliers, resampling, normalization)
    -> dataset
    -> model

without the EdgeLSTM/EdgeGRU/EdgeTCN/etc. models ever needing to know
which concrete source produced their input.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field, replace
from enum import Enum
import warnings

import numpy as np
import pandas as pd

from quantum_twin.core.config import PhysicsConfig


def __getattr__(name):
    # CSV/schema consumers do not need the optional Torch/Aer-backed dataset
    # generator. Preserve its explicit legacy export when actually requested.
    if name == "QuantumNetworkDatasetV3":
        from quantum_twin.optical.wdm import QuantumNetworkDatasetV3

        globals()[name] = QuantumNetworkDatasetV3
        return QuantumNetworkDatasetV3
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


class DataProvenance(str, Enum):
    """Origin of a field, kept with the schema to prevent proxy/target confusion."""

    MEASURED = "measured"
    DERIVED = "derived"
    STATIC = "static"
    SIMULATED = "simulated"
    LABEL = "label"
    IDENTIFIER = "identifier"


class ColumnRole(str, Enum):
    FEATURE = "feature"
    TARGET = "target"
    QUALITY = "quality"
    IDENTIFIER = "identifier"


@dataclass(frozen=True)
class ColumnSpec:
    name: str
    dtype: str
    unit: str
    valid_min: float = None
    valid_max: float = None
    provenance: DataProvenance = DataProvenance.MEASURED
    role: ColumnRole = ColumnRole.FEATURE
    required: bool = True
    nullable: bool = False
    aggregation: str = "mean"


@dataclass
class TelemetrySchema:
    columns: list = field(default_factory=list)
    timestamp_column: str = "timestamp"
    requires_regular_sampling: bool = False
    expected_sampling_period_s: float = None
    timestamp_required: bool = False

    def column_names(self) -> list:
        return [c.name for c in self.columns]


WDM_TELEMETRY_SCHEMA = TelemetrySchema(
    columns=[
        ColumnSpec("phase_drift", "float64", "rad", -10.0, 10.0),
        ColumnSpec("optical_power_dbm", "float64", "dBm", -60.0, 10.0),
        ColumnSpec("osnr_db", "float64", "dB", -20.0, 60.0),
        ColumnSpec("BER", "float64", "unitless", 0.0, 1.0),
        ColumnSpec("Loss_dB", "float64", "dB", 0.0, 100.0,
                   provenance=DataProvenance.DERIVED),
        ColumnSpec("Photon_Rate", "float64", "Hz", 0.0, None,
                   provenance=DataProvenance.SIMULATED),
        ColumnSpec("temperature", "float64", "K", 0.0, 500.0),
        ColumnSpec("polarization_drift", "float64", "rad", 0.0, None),
        ColumnSpec("Distance_km", "float64", "km", 0.0, None,
                   provenance=DataProvenance.STATIC),
        ColumnSpec("Transmission_Efficiency", "float64", "unitless", 0.0, 1.0,
                   provenance=DataProvenance.DERIVED),
        ColumnSpec("Latency", "float64", "s", 0.0, None,
                   provenance=DataProvenance.DERIVED),
        ColumnSpec("channel_available", "float64", "unitless", 0.0, 1.0,
                   provenance=DataProvenance.SIMULATED),
    ],
    timestamp_column="timestamp",
    requires_regular_sampling=False,
)


@dataclass
class ValidationIssue:
    column: str
    issue_type: str
    detail: str


@dataclass
class ValidationResult:
    is_valid: bool
    issues: list = field(default_factory=list)

    def summary(self) -> str:
        if self.is_valid:
            return "VALID: no schema issues found."
        lines = [f"INVALID: {len(self.issues)} issue(s) found:"]
        for issue in self.issues:
            lines.append(f"  [{issue.issue_type}] {issue.column}: {issue.detail}")
        return "\n".join(lines)


def detect_missing_values(df: pd.DataFrame, columns: list = None) -> dict:
    columns = columns or df.columns.tolist()
    result = {}
    for col in columns:
        if col not in df.columns:
            continue
        n_missing = int(df[col].isna().sum())
        if n_missing > 0:
            result[col] = n_missing
    return result


def detect_outliers_iqr(series: pd.Series, iqr_multiplier: float = 3.0) -> np.ndarray:
    clean = series.dropna()
    if len(clean) < 4:
        return np.zeros(len(series), dtype=bool)
    q1, q3 = clean.quantile(0.25), clean.quantile(0.75)
    iqr = q3 - q1
    lower = q1 - iqr_multiplier * iqr
    upper = q3 + iqr_multiplier * iqr
    return ((series < lower) | (series > upper)).fillna(False).values


def resample_to_regular_grid(df: pd.DataFrame, timestamp_column: str, target_period_s: float,
                              method: str = "linear", group_columns: list = None,
                              timestamp_unit: str = None, max_gap_periods: int = 3,
                              binary_columns: list = None) -> pd.DataFrame:
    """Resample without mixing devices/channels or fabricating long gaps.

    Numeric Unix timestamps are interpreted as seconds unless an explicit
    ``timestamp_unit`` is supplied.  Interpolation is bounded and only fills
    interior gaps. Binary/event columns are forward-filled and are never
    linearly interpolated.
    """
    frame = df.copy()
    ts = frame[timestamp_column]
    inferred_unit = timestamp_unit
    if inferred_unit is None and pd.api.types.is_numeric_dtype(ts):
        inferred_unit = "s"
    frame[timestamp_column] = pd.to_datetime(ts, errors="coerce", unit=inferred_unit, utc=True)
    if frame[timestamp_column].isna().any():
        raise ValueError(f"{timestamp_column!r} contains unparseable timestamps")

    group_columns = list(group_columns or [])
    missing_groups = [column for column in group_columns if column not in frame.columns]
    if missing_groups:
        raise ValueError(f"group columns not found: {missing_groups}")

    freq = pd.Timedelta(seconds=target_period_s)
    binary = set(binary_columns or [])

    def _resample_one(group: pd.DataFrame) -> pd.DataFrame:
        indexed = group.set_index(timestamp_column).sort_index()
        numeric_columns = indexed.select_dtypes(include=[np.number, "bool"]).columns.tolist()
        continuous = [column for column in numeric_columns if column not in binary]
        event_columns = [column for column in numeric_columns if column in binary]
        pieces = []
        if continuous:
            continuous_frame = indexed[continuous].resample(freq).mean()
            if method == "linear":
                continuous_frame = continuous_frame.interpolate(
                    method="time", limit=max_gap_periods, limit_area="inside"
                )
            pieces.append(continuous_frame)
        if event_columns:
            pieces.append(indexed[event_columns].resample(freq).ffill(limit=max_gap_periods))
        if not pieces:
            result = indexed.resample(freq).asfreq()
        else:
            result = pd.concat(pieces, axis=1)
        return result.reset_index()

    if not group_columns:
        return _resample_one(frame)

    groups = []
    grouper = group_columns[0] if len(group_columns) == 1 else group_columns
    for key, group in frame.groupby(grouper, sort=False, dropna=False):
        result = _resample_one(group.drop(columns=group_columns))
        values = key if isinstance(key, tuple) else (key,)
        for column, value in zip(group_columns, values):
            result[column] = value
        groups.append(result)
    ordered = group_columns + [column for column in groups[0].columns if column not in group_columns]
    return pd.concat(groups, ignore_index=True)[ordered]


def normalize_columns(df: pd.DataFrame, columns: list, fit_mask: np.ndarray = None):
    df = df.copy()
    if fit_mask is None:
        warnings.warn(
            "normalize_columns fitted on the complete frame; pass a training-only fit_mask "
            "for scientific experiments.", RuntimeWarning, stacklevel=2,
        )
    fit_rows = df if fit_mask is None else df[fit_mask]
    fit_min = fit_rows[columns].min()
    fit_max = fit_rows[columns].max()
    span = (fit_max - fit_min).replace(0, 1.0)
    df[columns] = (df[columns] - fit_min) / span
    return df, fit_min, fit_max


class TelemetrySource(ABC):
    @abstractmethod
    def read(self) -> pd.DataFrame:
        raise NotImplementedError

    @abstractmethod
    def schema(self) -> TelemetrySchema:
        raise NotImplementedError

    # Historical callers used generate_dataset()/preprocess().  Keeping
    # those aliases here gives every source one contract instead of two
    # unrelated ABCs with the same name.
    def generate_dataset(self) -> pd.DataFrame:
        return self.read()

    def preprocess(self, df: pd.DataFrame, window_size: int = 20,
                   test_size: float = 0.2, feature_set: str = "full"):
        if "F_t" not in df.columns:
            raise ValueError(
                "Quantum-fidelity preprocessing requires a measured or explicitly simulated "
                "'F_t' target. Optical-only telemetry must use an optical target such as "
                "future OSNR, log-BER, or WDM failure."
            )
        from quantum_twin.optical.wdm import QuantumNetworkDatasetV3

        adapter = QuantumNetworkDatasetV3(n_steps=len(df), config=PhysicsConfig())
        return adapter.preprocess(
            df, window_size=window_size, test_size=test_size, feature_set=feature_set
        )

    @property
    def input_size(self) -> int:
        from quantum_twin.optical.wdm import QuantumNetworkDatasetV3

        return len(QuantumNetworkDatasetV3.FEATURE_COLUMNS)

    def validate(self, data: pd.DataFrame) -> ValidationResult:
        """
        Extended in the seventieth addendum (master prompt v4 Fase 24) to
        add TIMESTAMP validation (presence, monotonicity, duplicates) and
        SAMPLING RATE validation (actual vs. schema().expected_sampling_period_s
        when requires_regular_sampling=True) -- the two checks the
        master prompt names explicitly that the schema already had FIELDS
        for (timestamp_column, requires_regular_sampling,
        expected_sampling_period_s) but validate() never actually USED
        until now.
        """
        schema = self.schema()
        issues = []

        for col_spec in schema.columns:
            if col_spec.name not in data.columns:
                if col_spec.required:
                    issues.append(ValidationIssue(col_spec.name, "missing_column",
                                                   f"expected column '{col_spec.name}' not found"))
                continue

            series = data[col_spec.name]
            n_missing = int(series.isna().sum())
            if n_missing > 0 and not col_spec.nullable:
                issues.append(ValidationIssue(col_spec.name, "missing_values",
                                               f"{n_missing} missing value(s) out of {len(series)}"))

            if col_spec.dtype.startswith(("float", "int")) and not pd.api.types.is_numeric_dtype(series):
                coerced = pd.to_numeric(series, errors="coerce")
                newly_invalid = int((coerced.isna() & series.notna()).sum())
                if newly_invalid:
                    issues.append(ValidationIssue(
                        col_spec.name, "invalid_dtype",
                        f"{newly_invalid} value(s) are not numeric as required by {col_spec.dtype}",
                    ))
                series = coerced

            if col_spec.valid_min is not None:
                n_below = int((series.dropna() < col_spec.valid_min).sum())
                if n_below > 0:
                    issues.append(ValidationIssue(
                        col_spec.name, "out_of_range",
                        f"{n_below} value(s) below valid_min={col_spec.valid_min} ({col_spec.unit})"))
            if col_spec.valid_max is not None:
                n_above = int((series.dropna() > col_spec.valid_max).sum())
                if n_above > 0:
                    issues.append(ValidationIssue(
                        col_spec.name, "out_of_range",
                        f"{n_above} value(s) above valid_max={col_spec.valid_max} ({col_spec.unit})"))

        if schema.timestamp_column in data.columns:
            ts_issues = self._validate_timestamps(data[schema.timestamp_column], schema)
            issues.extend(ts_issues)
        elif schema.timestamp_required:
            issues.append(ValidationIssue(
                schema.timestamp_column, "missing_column",
                f"expected timestamp column '{schema.timestamp_column}' not found",
            ))

        return ValidationResult(is_valid=(len(issues) == 0), issues=issues)

    def _validate_timestamps(self, timestamps: pd.Series, schema: TelemetrySchema) -> list:
        """Real timestamp/sampling-rate checks: monotonicity, duplicates,
        and (when requires_regular_sampling=True) actual vs. expected
        sampling period -- not just column presence."""
        issues = []
        unit = "s" if pd.api.types.is_numeric_dtype(timestamps) else None
        ts = pd.to_datetime(timestamps, errors="coerce", unit=unit, utc=True)

        n_unparseable = int(ts.isna().sum())
        if n_unparseable > 0:
            issues.append(ValidationIssue(schema.timestamp_column, "invalid_timestamp",
                                           f"{n_unparseable} value(s) could not be parsed as timestamps"))

        ts_clean = ts.dropna()
        if len(ts_clean) < 2:
            return issues

        if not ts_clean.is_monotonic_increasing:
            n_non_monotonic = int((ts_clean.diff().dropna() < pd.Timedelta(0)).sum())
            issues.append(ValidationIssue(
                schema.timestamp_column, "non_monotonic_timestamp",
                f"{n_non_monotonic} timestamp(s) go backward relative to the previous row -- "
                f"telemetry must arrive in temporal order."))

        n_duplicates = int(ts_clean.duplicated().sum())
        if n_duplicates > 0:
            issues.append(ValidationIssue(schema.timestamp_column, "duplicate_timestamp",
                                           f"{n_duplicates} duplicate timestamp value(s) found."))

        if schema.requires_regular_sampling and schema.expected_sampling_period_s is not None:
            diffs_s = ts_clean.diff().dropna().dt.total_seconds()
            if len(diffs_s) > 0:
                expected = schema.expected_sampling_period_s
                tolerance = expected * 0.1  # 10% tolerance, an explicit, stated allowance
                n_irregular = int(((diffs_s - expected).abs() > tolerance).sum())
                if n_irregular > 0:
                    issues.append(ValidationIssue(
                        schema.timestamp_column, "irregular_sampling",
                        f"{n_irregular} interval(s) deviate from the expected {expected}s sampling "
                        f"period by more than {tolerance:.4f}s -- consider resample_to_regular_grid()."))

        return issues


class SyntheticWDMSource(TelemetrySource):
    def __init__(self, n_steps: int = 4000, config: PhysicsConfig = None):
        from quantum_twin.optical.wdm import QuantumNetworkDatasetV3

        self.n_steps = n_steps
        self.config = config or PhysicsConfig()
        self._generator = QuantumNetworkDatasetV3(n_steps=n_steps, config=self.config)

    def read(self) -> pd.DataFrame:
        return self._generator.generate_dataset()

    def schema(self) -> TelemetrySchema:
        return WDM_TELEMETRY_SCHEMA


class CSVTelemetrySource(TelemetrySource):
    def __init__(self, csv_path: str, timestamp_column: str = None,
                 resample_period_s: float = None, schema: TelemetrySchema = None,
                 column_mapping: dict = None, group_columns: list = None,
                 binary_columns: list = None):
        self.csv_path = csv_path
        self.timestamp_column = timestamp_column
        self.resample_period_s = resample_period_s
        self.column_mapping = column_mapping or {}
        self.group_columns = list(group_columns or [])
        self.binary_columns = list(binary_columns or [])
        base_schema = schema or WDM_TELEMETRY_SCHEMA
        self._schema = (
            replace(base_schema, timestamp_column=timestamp_column, timestamp_required=True)
            if timestamp_column is not None else base_schema
        )

    def read(self) -> pd.DataFrame:
        df = pd.read_csv(self.csv_path)
        if self.column_mapping:
            df = df.rename(columns=self.column_mapping)
        if self.timestamp_column in df.columns and self.resample_period_s is not None:
            df = resample_to_regular_grid(
                df, self.timestamp_column, self.resample_period_s,
                group_columns=self.group_columns, binary_columns=self.binary_columns,
            )
        return df

    def schema(self) -> TelemetrySchema:
        return self._schema


class ParquetTelemetrySource(TelemetrySource):
    def __init__(self, parquet_path: str, timestamp_column: str = None,
                 resample_period_s: float = None, schema: TelemetrySchema = None):
        self.parquet_path = parquet_path
        self.timestamp_column = timestamp_column
        self.resample_period_s = resample_period_s
        base_schema = schema or WDM_TELEMETRY_SCHEMA
        self._schema = (
            replace(base_schema, timestamp_column=timestamp_column, timestamp_required=True)
            if timestamp_column is not None else base_schema
        )

    def read(self) -> pd.DataFrame:
        df = pd.read_parquet(self.parquet_path)
        if self.timestamp_column in df.columns and self.resample_period_s is not None:
            df = resample_to_regular_grid(df, self.timestamp_column, self.resample_period_s)
        return df

    def schema(self) -> TelemetrySchema:
        return self._schema


class LiveWDMSource(TelemetrySource):
    """Interface placeholder for a live WDM telemetry stream -- exposes
    the SAME read/schema/validate contract, but is NOT connected to any
    real hardware or network socket in this project. Calling read()
    raises NotImplementedError explicitly, rather than silently
    returning synthetic or empty data pretending to be live."""

    def __init__(self, schema: TelemetrySchema = None):
        self._schema = schema or WDM_TELEMETRY_SCHEMA

    def read(self) -> pd.DataFrame:
        raise NotImplementedError(
            "LiveWDMSource is an interface placeholder for a real hardware/network feed -- "
            "not connected to any live telemetry stream in this project. Implement read() "
            "with a real data source before use."
        )

    def schema(self) -> TelemetrySchema:
        return self._schema
