"""Unified telemetry sources.

All sources implement the formal ``read/schema/validate`` contract from
``quantum_twin.optical.interface``. ``generate_dataset`` remains available
as a compatibility alias, but is no longer a second abstraction.

Recorded optical telemetry is deliberately not padded with invented quantum
features. Fidelity training is allowed only when the caller supplies an
explicit measured or simulated ``F_t`` target.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from quantum_twin.core.config import PhysicsConfig
from quantum_twin.optical.interface import (
    CSVTelemetrySource as _FormalCSVTelemetrySource,
    SyntheticWDMSource,
    TelemetrySchema,
    TelemetrySource,
    WDM_TELEMETRY_SCHEMA,
)
from quantum_twin.optical.wdm import QuantumNetworkDatasetV3


class SyntheticTelemetrySource(SyntheticWDMSource):
    """Causal simulator exposed through the same contract as recorded data."""

    def __init__(self, n_steps: int = 4000, config: PhysicsConfig = None):
        super().__init__(n_steps=n_steps, config=config)

    def preprocess(self, df: pd.DataFrame, window_size: int = 20,
                   test_size: float = 0.2, feature_set: str = "full"):
        return self._generator.preprocess(
            df, window_size=window_size, test_size=test_size, feature_set=feature_set
        )


class CSVTelemetrySource(_FormalCSVTelemetrySource):
    """Generic CSV adapter that preserves missingness and provenance.

    Only physically deterministic derivations are performed: ``Loss_dB``
    from a known distance/attenuation coefficient and transmission efficiency
    from loss. Phase, polarization, temperature, OSNR, fidelity and quantum
    memory parameters are never filled with nominal constants.
    """

    def __init__(self, csv_path: str, column_mapping: dict = None,
                 alpha_db_per_km: float = 0.2, timestamp_column: str = None,
                 resample_period_s: float = None, schema: TelemetrySchema = None,
                 group_columns: list = None, binary_columns: list = None):
        super().__init__(
            csv_path=csv_path,
            timestamp_column=timestamp_column,
            resample_period_s=resample_period_s,
            schema=schema,
            column_mapping=column_mapping,
            group_columns=group_columns,
            binary_columns=binary_columns,
        )
        self.alpha_db_per_km = float(alpha_db_per_km)

    def read(self) -> pd.DataFrame:
        frame = super().read()

        if "Loss_dB" not in frame.columns and "Distance_km" in frame.columns:
            distance = pd.to_numeric(frame["Distance_km"], errors="coerce")
            frame["Loss_dB"] = self.alpha_db_per_km * distance
        if "Transmission_Efficiency" not in frame.columns and "Loss_dB" in frame.columns:
            loss = pd.to_numeric(frame["Loss_dB"], errors="coerce")
            frame["Transmission_Efficiency"] = 10.0 ** (-loss / 10.0)

        observed_candidates = [
            "phase_drift", "optical_power_dbm", "osnr_db", "BER",
            "temperature", "polarization_drift",
        ]
        for column in observed_candidates:
            if column in frame.columns:
                frame[f"{column}_is_missing"] = frame[column].isna().astype(np.int8)

        frame.attrs["telemetry_provenance"] = {
            "source_type": "recorded_csv",
            "derived_columns": [
                name for name in ("Loss_dB", "Transmission_Efficiency")
                if name in frame.columns
            ],
            "simulated_quantum_target": False,
        }
        return frame.reset_index(drop=True)

    def generate_dataset(self) -> pd.DataFrame:
        frame = self.read()
        legacy_columns = QuantumNetworkDatasetV3.FEATURE_COLUMNS
        if all(column in frame.columns for column in legacy_columns):
            return frame[legacy_columns].reset_index(drop=True)
        return frame

    def preprocess(self, df: pd.DataFrame, window_size: int = 20,
                   test_size: float = 0.2, feature_set: str = "full"):
        if "F_t" not in df.columns:
            raise ValueError(
                "This CSV contains optical telemetry but no quantum-fidelity target 'F_t'. "
                "Use an optical supervised task or attach an explicitly simulated/measured "
                "quantum target before calling fidelity preprocessing."
            )
        return super().preprocess(
            df, window_size=window_size, test_size=test_size, feature_set=feature_set
        )

RealWDMTelemetrySource = CSVTelemetrySource


__all__ = [
    "TelemetrySource",
    "SyntheticTelemetrySource",
    "CSVTelemetrySource",
    "RealWDMTelemetrySource",
    "WDM_TELEMETRY_SCHEMA",
]
