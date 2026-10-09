import numpy as np

from quantum_twin.quantum.dryad_stike import DryadSTIKETelemetrySource


def _write_release(tmp_path, *, samples=168):
    tmp_path.mkdir(parents=True, exist_ok=True)
    qber = 3.6 + 0.2 * np.sin(np.arange(samples) / 12.0)
    rate = 2.38 - 0.08 * np.sin(np.arange(samples) / 12.0)
    (tmp_path / "QBER_168h.txt").write_text(
        "\n".join(f"{value:.6f}" for value in qber) + "\n", encoding="utf-8"
    )
    (tmp_path / "Rate_168h.txt").write_text(
        "\n".join(f"{value:.6f}" for value in rate) + "\n", encoding="utf-8"
    )
    return tmp_path


def test_dryad_stike_adapter_preserves_protocol_units_and_relative_time(tmp_path):
    source = DryadSTIKETelemetrySource(
        _write_release(tmp_path), verify_publisher_hashes=False
    )
    frame = source.read()

    assert source.validate(frame).is_valid
    assert len(frame) == 168
    assert frame["relative_hour"].iloc[-1] == 167.0
    assert np.allclose(frame["qber_measured"], frame["qber_reported_percent"] / 100.0)
    assert np.allclose(
        frame["communication_rate_bps"],
        frame["communication_rate_reported_kbps"] * 1000.0,
    )
    assert "timestamp_utc" not in frame
    provenance = frame.attrs["telemetry_provenance"]
    assert provenance["protocol"] == "STIKE one-way quasi-QSDC"
    assert provenance["secure_key_rate_present"] is False
    assert provenance["time_varying_wdm_telemetry_present"] is False


def test_dryad_stike_adapter_rejects_non_168_hour_file(tmp_path):
    dataset = _write_release(tmp_path, samples=167)
    source = DryadSTIKETelemetrySource(dataset, verify_publisher_hashes=False)

    try:
        source.read()
    except ValueError as error:
        assert "exactly 168 hourly values" in str(error)
    else:
        raise AssertionError("expected a sample-count error")

    unverified_release = _write_release(tmp_path / "unknown-digest")
    try:
        DryadSTIKETelemetrySource(unverified_release).read()
    except ValueError as error:
        assert "published version-4 SHA-256" in str(error)
    else:
        raise AssertionError("expected a publisher-integrity error")
