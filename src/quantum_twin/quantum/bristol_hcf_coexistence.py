"""Adapter for the Bristol hollow-core-fibre QKD coexistence release.

The public release contains ordered hardware measurements of QBER and secure
key rate while co-propagating classical DWDM channels were alternately enabled
and disabled.  It does *not* contain timestamped BER/OSNR/power telemetry or a
measured Bell-fidelity time series.  The adapter preserves that boundary: the
published chunk number is an order index, never an invented timestamp.
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
    ValidationIssue,
    ValidationResult,
)


BRISTOL_HCF_LINKS = ("AB", "AC", "AD", "BC", "BD", "CD")
BRISTOL_HCF_ALICE_LINKS = frozenset(("AB", "AC", "AD"))
BRISTOL_HCF_REPORTED_CLASSICAL_POWER_DBM = -3.0


BRISTOL_HCF_COEXISTENCE_SCHEMA = TelemetrySchema(
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
            "relative_chunk_index", "int64", "published order index", 0.0, None,
            provenance=DataProvenance.IDENTIFIER, role=ColumnRole.IDENTIFIER,
        ),
        ColumnSpec(
            "sequence_segment", "int64", "contiguous-segment index", 0.0, None,
            provenance=DataProvenance.DERIVED, role=ColumnRole.IDENTIFIER,
        ),
        ColumnSpec(
            "link_id", "string", "identifier",
            provenance=DataProvenance.IDENTIFIER, role=ColumnRole.IDENTIFIER,
        ),
        ColumnSpec(
            "endpoint_1", "string", "identifier",
            provenance=DataProvenance.IDENTIFIER, role=ColumnRole.IDENTIFIER,
        ),
        ColumnSpec(
            "endpoint_2", "string", "identifier",
            provenance=DataProvenance.IDENTIFIER, role=ColumnRole.IDENTIFIER,
        ),
        ColumnSpec("coexistence_active", "int8", "binary", 0.0, 1.0),
        ColumnSpec("alice_link", "int8", "binary", 0.0, 1.0),
        ColumnSpec(
            "classical_light_on_link", "int8", "binary", 0.0, 1.0,
            provenance=DataProvenance.DERIVED,
        ),
        ColumnSpec(
            "classical_total_power_dbm_reported", "float64", "dBm", -30.0, 30.0,
            provenance=DataProvenance.STATIC, nullable=True,
        ),
        ColumnSpec(
            "qber_measured", "float64", "proportion", 0.0, 1.0,
            provenance=DataProvenance.LABEL, role=ColumnRole.TARGET, nullable=True,
        ),
        ColumnSpec(
            "qber_reported_error", "float64", "proportion", 0.0, 1.0,
            provenance=DataProvenance.LABEL, role=ColumnRole.QUALITY, nullable=True,
        ),
        ColumnSpec(
            "secure_key_rate_bps", "float64", "bit/s", 0.0, None,
            provenance=DataProvenance.LABEL, role=ColumnRole.TARGET,
        ),
        ColumnSpec(
            "endpoint_1_count_rate_cps", "float64", "count/s", 0.0, None,
            provenance=DataProvenance.MEASURED,
        ),
        ColumnSpec(
            "endpoint_2_count_rate_cps", "float64", "count/s", 0.0, None,
            provenance=DataProvenance.MEASURED,
        ),
    ],
    timestamp_required=False,
    requires_regular_sampling=False,
)


class BristolHCFCoexistenceSource(TelemetrySource):
    """Canonicalize ``4UserNetworkData/4U_OnOff.csv`` to long format."""

    RAW_REQUIRED_COLUMNS = {
        "chunk",
        "Coexistence",
        *(f"SKR {link}" for link in BRISTOL_HCF_LINKS),
        *(f"QBER {link}" for link in BRISTOL_HCF_LINKS),
        *(f"QBER {link} err" for link in BRISTOL_HCF_LINKS),
        *(f"Counts {endpoint}" for endpoint in "ABCD"),
    }

    def __init__(
        self,
        dataset_dir: str | Path,
        dataset_id: str = "bristol-hcf-qkd-coexistence-61193flfovbn2puknndsr3qm5",
    ):
        self.dataset_dir = Path(dataset_dir)
        self.dataset_id = str(dataset_id)

    def schema(self) -> TelemetrySchema:
        return BRISTOL_HCF_COEXISTENCE_SCHEMA

    def measurement_file(self) -> Path:
        if self.dataset_dir.is_file():
            if self.dataset_dir.name != "4U_OnOff.csv":
                raise ValueError("the Bristol HCF measurement file must be named 4U_OnOff.csv")
            return self.dataset_dir
        if not self.dataset_dir.exists():
            raise FileNotFoundError(f"Bristol HCF dataset directory not found: {self.dataset_dir}")
        direct = self.dataset_dir / "4UserNetworkData" / "4U_OnOff.csv"
        if direct.is_file():
            return direct
        candidates = sorted(self.dataset_dir.rglob("4U_OnOff.csv"))
        if not candidates:
            raise FileNotFoundError(
                f"4UserNetworkData/4U_OnOff.csv not found below {self.dataset_dir}"
            )
        if len(candidates) > 1:
            raise ValueError(f"multiple 4U_OnOff.csv files found below {self.dataset_dir}")
        return candidates[0]

    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    @staticmethod
    def _sequence_segments(chunks: pd.Series) -> dict[int, int]:
        ordered = sorted(int(value) for value in chunks.unique())
        segments: dict[int, int] = {}
        segment = 0
        previous: int | None = None
        for value in ordered:
            if previous is not None and value != previous + 1:
                segment += 1
            segments[value] = segment
            previous = value
        return segments

    def read(self) -> pd.DataFrame:
        path = self.measurement_file()
        raw = pd.read_csv(path)
        missing = sorted(self.RAW_REQUIRED_COLUMNS.difference(raw.columns))
        if missing:
            raise ValueError(f"{path.name} is missing Bristol HCF columns: {missing}")

        condition = raw["Coexistence"].astype("string").str.strip().str.casefold()
        observed = raw.loc[condition.isin(("on", "off"))].copy()
        observed["_coexistence_active"] = condition.loc[observed.index].eq("on")
        chunks = pd.to_numeric(observed["chunk"], errors="coerce")
        if chunks.isna().any() or not np.equal(chunks, np.floor(chunks)).all():
            raise ValueError("published chunk indices must be finite integers")
        observed["_chunk"] = chunks.astype(np.int64)
        if observed["_chunk"].duplicated().any():
            raise ValueError("published 4U_OnOff.csv contains duplicate observed chunk indices")
        segments = self._sequence_segments(observed["_chunk"])

        records: list[dict] = []
        for row in observed.to_dict(orient="records"):
            active = bool(row["_coexistence_active"])
            chunk_index = int(row["_chunk"])
            for link in BRISTOL_HCF_LINKS:
                endpoint_1, endpoint_2 = link
                alice_link = link in BRISTOL_HCF_ALICE_LINKS
                records.append({
                    "dataset_id": self.dataset_id,
                    "campaign_id": "4-user-on-off",
                    "source_file": path.name,
                    "relative_chunk_index": chunk_index,
                    "sequence_segment": int(segments[chunk_index]),
                    "link_id": link,
                    "endpoint_1": endpoint_1,
                    "endpoint_2": endpoint_2,
                    "coexistence_active": np.int8(active),
                    "alice_link": np.int8(alice_link),
                    "classical_light_on_link": np.int8(active and alice_link),
                    "classical_total_power_dbm_reported": (
                        BRISTOL_HCF_REPORTED_CLASSICAL_POWER_DBM if active else np.nan
                    ),
                    "qber_measured": pd.to_numeric(row[f"QBER {link}"], errors="coerce"),
                    "qber_reported_error": pd.to_numeric(
                        row[f"QBER {link} err"], errors="coerce"
                    ),
                    "secure_key_rate_bps": pd.to_numeric(
                        row[f"SKR {link}"], errors="coerce"
                    ),
                    "endpoint_1_count_rate_cps": pd.to_numeric(
                        row[f"Counts {endpoint_1}"], errors="coerce"
                    ),
                    "endpoint_2_count_rate_cps": pd.to_numeric(
                        row[f"Counts {endpoint_2}"], errors="coerce"
                    ),
                })
        result = pd.DataFrame.from_records(records).sort_values(
            ["relative_chunk_index", "link_id"], kind="stable"
        ).reset_index(drop=True)
        result.attrs["telemetry_provenance"] = {
            "source_type": "recorded_hardware",
            "dataset_id": self.dataset_id,
            "doi": "10.5523/bris.61193flfovbn2puknndsr3qm5",
            "license": "Non-Commercial Government Licence",
            "quantum_target_present": True,
            "quantum_target_kind": "measured_qkd_qber_and_secure_key_rate",
            "bell_fidelity_present": False,
            "timestamp_present": False,
            "relative_order_present": True,
            "classical_condition_present": True,
            "time_varying_wdm_telemetry_present": False,
            "wdm_ber_osnr_power_timeseries_present": False,
            "reported_classical_configuration": (
                "four 200-Gbit/s 16-QAM classical channels; total coexistence power -3 dBm"
            ),
            "sampling_interval_status": (
                "not canonicalized: the repository README and paper describe the plotted "
                "aggregation interval differently; only published chunk order is used"
            ),
            "experimental_design": (
                "classical coexistence alternated on/off; Alice links are exposed and "
                "BC/BD/CD are contemporaneous unexposed controls"
            ),
            "source_file": path.name,
            "source_file_sha256": self._sha256(path),
        }
        return result

    def validate(self, data: pd.DataFrame) -> ValidationResult:
        base = super().validate(data)
        issues = list(base.issues)
        keys = ["relative_chunk_index", "link_id"]
        if all(column in data for column in keys):
            duplicates = int(data.duplicated(keys).sum())
            if duplicates:
                issues.append(ValidationIssue(
                    "+".join(keys), "duplicate_measurement",
                    f"{duplicates} duplicate chunk/link measurement(s)",
                ))
            for chunk, group in data.groupby("relative_chunk_index", observed=True):
                links = set(group["link_id"].astype(str))
                if links != set(BRISTOL_HCF_LINKS):
                    issues.append(ValidationIssue(
                        "link_id", "incomplete_chunk",
                        f"chunk {int(chunk)} has links {sorted(links)}, expected all six links",
                    ))
                if group["coexistence_active"].nunique(dropna=False) != 1:
                    issues.append(ValidationIssue(
                        "coexistence_active", "inconsistent_chunk_condition",
                        f"chunk {int(chunk)} has more than one coexistence state",
                    ))
        if {"link_id", "alice_link"}.issubset(data.columns):
            expected = data["link_id"].astype(str).isin(BRISTOL_HCF_ALICE_LINKS).astype(np.int8)
            mismatches = int((pd.to_numeric(data["alice_link"], errors="coerce") != expected).sum())
            if mismatches:
                issues.append(ValidationIssue(
                    "alice_link", "invalid_exposure_group",
                    f"{mismatches} row(s) disagree with the documented Alice-link group",
                ))
        if {"coexistence_active", "alice_link", "classical_light_on_link"}.issubset(
            data.columns
        ):
            expected = (
                pd.to_numeric(data["coexistence_active"], errors="coerce").astype(bool)
                & pd.to_numeric(data["alice_link"], errors="coerce").astype(bool)
            ).astype(np.int8)
            mismatches = int((pd.to_numeric(
                data["classical_light_on_link"], errors="coerce"
            ) != expected).sum())
            if mismatches:
                issues.append(ValidationIssue(
                    "classical_light_on_link", "invalid_derived_exposure",
                    f"{mismatches} row(s) are inconsistent with state × Alice link",
                ))
        return ValidationResult(is_valid=not issues, issues=issues)

    def quality_report(self, data: pd.DataFrame | None = None) -> dict:
        frame = self.read() if data is None else data
        validation = self.validate(frame)
        chunks = sorted(int(value) for value in frame["relative_chunk_index"].unique())
        complete_pairs = 0
        for _, group in frame.groupby(frame["relative_chunk_index"] // 2, observed=True):
            states = set(group["coexistence_active"].astype(int))
            if states == {0, 1} and group["relative_chunk_index"].nunique() == 2:
                complete_pairs += 1
        return {
            "rows": int(len(frame)),
            "observed_chunks": int(len(chunks)),
            "published_chunk_range": [min(chunks), max(chunks)],
            "sequence_segments": int(frame["sequence_segment"].nunique()),
            "links": sorted(frame["link_id"].astype(str).unique()),
            "coexistence_on_chunks": int(
                frame.loc[frame["coexistence_active"].eq(1), "relative_chunk_index"].nunique()
            ),
            "coexistence_off_chunks": int(
                frame.loc[frame["coexistence_active"].eq(0), "relative_chunk_index"].nunique()
            ),
            "complete_adjacent_switch_pairs": int(complete_pairs),
            "missing_qber_values": int(frame["qber_measured"].isna().sum()),
            "missing_secure_key_rate_values": int(frame["secure_key_rate_bps"].isna().sum()),
            "timestamps_available": False,
            "time_varying_wdm_telemetry_available": False,
            "validation_valid": validation.is_valid,
            "validation_issues": [issue.__dict__ for issue in validation.issues],
        }


__all__ = [
    "BRISTOL_HCF_ALICE_LINKS",
    "BRISTOL_HCF_COEXISTENCE_SCHEMA",
    "BRISTOL_HCF_LINKS",
    "BRISTOL_HCF_REPORTED_CLASSICAL_POWER_DBM",
    "BristolHCFCoexistenceSource",
]
