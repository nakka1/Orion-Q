import pandas as pd

from run_santanna_optical_baseline import purged_device_split


def test_purged_split_is_chronological_and_device_local():
    rows = []
    for device in ["a", "b"]:
        for step in range(20):
            rows.append({
                "device_id": device,
                "timestamp_utc": pd.Timestamp("2026-01-01", tz="UTC") + pd.Timedelta(seconds=step),
                "value": step,
            })
    frame = pd.DataFrame(rows)
    train, test, manifest = purged_device_split(frame, test_fraction=0.25, purge_steps=2)
    for device in ["a", "b"]:
        train_device = train[train["device_id"] == device]
        test_device = test[test["device_id"] == device]
        assert train_device["timestamp_utc"].max() < test_device["timestamp_utc"].min()
        assert manifest[device]["purged_rows"] == 2
