import json

import numpy as np
import pandas as pd
import pytest

from quantum_twin.quantum.paired_telemetry import (
    DEFAULT_PAIRED_MANIFEST,
    PAIRED_QUANTUM_TELEMETRY_COLUMNS,
    PairedQuantumProfile,
    PairedQuantumTelemetrySource,
    create_paired_quantum_acquisition_bundle,
)


def _manifest(dataset_id="paired-test"):
    value = json.loads(json.dumps(DEFAULT_PAIRED_MANIFEST))
    value["dataset_id"] = dataset_id
    value["license"] = "test-only"
    value["synchronization"]["clock_source"] = "PTP"
    return value


def _frame(rows=8, *, timestamps_with_zone=True):
    timestamps = pd.date_range("2026-01-01", periods=rows, freq="s", tz="UTC")
    if timestamps_with_zone:
        timestamp_values = timestamps.strftime("%Y-%m-%dT%H:%M:%SZ")
    else:
        timestamp_values = timestamps.strftime("%Y-%m-%d %H:%M:%S")
    return pd.DataFrame({
        "dataset_id": "paired-test",
        "campaign_id": "campaign-1",
        "device_id": "qkd-1",
        "channel_id": "ch-37",
        "timestamp_utc": timestamp_values,
        "pre_fec_ber": np.linspace(1e-7, 1e-5, rows),
        "osnr_db": np.linspace(26.0, 22.0, rows),
        "rx_power_dbm": np.linspace(-12.0, -14.0, rows),
        "tx_power_dbm": 0.0,
        "qber_ratio": np.linspace(0.01, 0.04, rows),
        "secure_key_rate_bps": np.linspace(100_000.0, 50_000.0, rows),
        "bell_fidelity_measured": np.nan,
        "fidelity_standard_error": np.nan,
        "fidelity_shots": np.nan,
        "fidelity_measurement_method": None,
        "alignment_error_ms": 2.0,
    })


def _write(tmp_path, frame=None, manifest=None):
    data_path = tmp_path / "paired.csv"
    frame = _frame() if frame is None else frame
    frame.to_csv(data_path, index=False)
    manifest_path = data_path.with_suffix(".csv.manifest.json")
    manifest_path.write_text(
        json.dumps(_manifest() if manifest is None else manifest), encoding="utf-8"
    )
    return data_path, manifest_path


def test_strict_paired_qkd_source_accepts_only_explicit_measured_contract(tmp_path):
    data_path, _ = _write(tmp_path)
    source = PairedQuantumTelemetrySource(data_path)
    data = source.require_valid()

    assert list(data.columns) == list(PAIRED_QUANTUM_TELEMETRY_COLUMNS)
    assert str(data["timestamp_utc"].dt.tz) == "UTC"
    assert source.quality_report(data)["validation_valid"] is True
    assert data.attrs["telemetry_provenance"]["target_provenance"]["qber_ratio"] == (
        "measured_quantum"
    )


def test_paired_source_rejects_naive_time_and_simulated_targets(tmp_path):
    manifest = _manifest()
    manifest["target_provenance"]["qber_ratio"] = "simulated_from_ber"
    data_path, _ = _write(
        tmp_path, frame=_frame(timestamps_with_zone=False), manifest=manifest
    )
    source = PairedQuantumTelemetrySource(data_path)
    validation = source.validate(source.read())
    issue_types = {issue.issue_type for issue in validation.issues}
    assert "timezone_not_explicit" in issue_types
    assert "simulated_target_in_experimental_campaign" in issue_types
    with pytest.raises(ValueError, match="simulated_from_ber"):
        source.require_valid()


def test_bell_profile_requires_measured_fidelity_and_measurement_metadata(tmp_path):
    data_path, _ = _write(tmp_path)
    source = PairedQuantumTelemetrySource(
        data_path, profile=PairedQuantumProfile.MEASURED_BELL_FIDELITY
    )
    validation = source.validate(source.read())
    assert not validation.is_valid
    assert any(
        issue.issue_type == "insufficient_measured_fidelity_coverage"
        for issue in validation.issues
    )


def test_acquisition_bundle_is_directly_discoverable_by_source(tmp_path):
    bundle = create_paired_quantum_acquisition_bundle(
        tmp_path, formats=("csv",), dataset_id="paired-test"
    )
    assert bundle.manifest_path.exists()
    assert bundle.dictionary_path.exists()
    source = PairedQuantumTelemetrySource(bundle.data_files[0])
    assert source.manifest_path == bundle.manifest_path
    assert source._read_manifest()["dataset_id"] == "paired-test"
