"""Realistic integration tests for the public Sant'Anna long-format schema."""

import pandas as pd
import pytest

from quantum_twin.optical.santanna import SantAnnaOpticalFailureSource


@pytest.fixture
def santanna_csv(tmp_path):
    rows = [
        [1623394635, "Infrastructure", "Ampli1", None, None, -23.0, 0.7, None],
        [1623394634, "Devices", "SPO1/18/11", 2.28e-8, 38.5, None, None, None],
        [1623394635, "Devices", "SPO2/18/11", 8.73e-7, 23.5, None, None, None],
        [1623394638, "Infrastructure", "Ampli1", None, None, -30.0, 0.7, 1.0],
        [1623394638, "Devices", "SPO1/18/11", 1.72e-8, 38.6, None, None, 1.0],
        [1623394638, "Devices", "SPO2/18/11", 8.68e-7, 23.5, None, None, 1.0],
    ]
    path = tmp_path / "HardFailure_dataset.csv"
    pd.DataFrame(rows, columns=[
        "Timestamp", "Type", "ID", "BER", "OSNR",
        "InputPower", "OutputPower", "Failure",
    ]).to_csv(path, index=False)
    return path


def test_santanna_adapter_preserves_long_format_and_structural_missingness(santanna_csv):
    source = SantAnnaOpticalFailureSource(str(santanna_csv))
    frame = source.read()
    assert len(frame) == 6
    assert isinstance(frame["timestamp_utc"].dtype, pd.DatetimeTZDtype)
    assert str(frame["timestamp_utc"].dt.tz) == "UTC"
    assert frame.loc[frame["device_type"] == "amplifier", "pre_fec_ber"].isna().all()
    assert frame.loc[frame["device_type"] == "transponder", "input_power_dbm"].isna().all()
    assert source.validate(frame).is_valid
    assert frame.attrs["telemetry_provenance"]["quantum_target_present"] is False


def test_blank_failure_policy_is_explicit(santanna_csv):
    unknown = SantAnnaOpticalFailureSource(str(santanna_csv)).read()
    normal = SantAnnaOpticalFailureSource(
        str(santanna_csv), blank_failure_is_normal=True
    ).read()
    assert unknown["wdm_failure"].isna().sum() == 3
    assert normal["wdm_failure"].isna().sum() == 0
    assert normal.attrs["telemetry_provenance"]["blank_failure_is_normal"] is True


def test_device_panel_uses_nearest_join_without_averaging_devices(santanna_csv):
    source = SantAnnaOpticalFailureSource(str(santanna_csv))
    panel = source.build_device_panel(tolerance_s=2.0)
    assert len(panel) == 2
    assert "osnr_db__spo1_18_11" in panel.columns
    assert "input_power_dbm__ampli1" in panel.columns
    assert panel.loc[0, "input_power_dbm__ampli1"] == -23.0


def test_forecast_target_never_crosses_device_boundary(santanna_csv):
    source = SantAnnaOpticalFailureSource(str(santanna_csv))
    forecast = source.build_optical_forecast_frame(target="osnr_db", horizon_steps=1)
    assert set(forecast["device_id"]) == {"SPO1/18/11", "SPO2/18/11"}
    assert (forecast["target_timestamp_utc"] > forecast["timestamp_utc"]).all()


def test_causal_panel_never_uses_a_future_sensor_sample(santanna_csv):
    source = SantAnnaOpticalFailureSource(str(santanna_csv))
    panel = source.build_causal_device_panel(
        anchor_device="SPO1/18/11", tolerance_s=5.0
    )
    # At t=34 the nearest amplifier sample is t=35, but it is in the future
    # and must not be selected by the forecasting panel.
    assert pd.isna(panel.loc[0, "input_power_dbm__ampli1"])
    observed = [column for column in panel if column.startswith("observed_at__")]
    for column in observed:
        matched = panel[column].notna()
        assert (panel.loc[matched, column] <= panel.loc[matched, "timestamp_utc"]).all()
        age = "age_s__" + column.removeprefix("observed_at__")
        assert (panel.loc[matched, age] >= 0.0).all()


def test_causal_forecast_panel_shifts_labels_within_each_anchor(santanna_csv):
    source = SantAnnaOpticalFailureSource(str(santanna_csv))
    panel = source.build_causal_forecast_panel(
        target="osnr_db", horizon_steps=1, tolerance_s=5.0
    )
    assert set(panel["anchor_device_id"]) == {"SPO1/18/11", "SPO2/18/11"}
    assert (panel["target_timestamp_utc"] > panel["timestamp_utc"]).all()
    assert panel.groupby("anchor_device_id").size().eq(1).all()
