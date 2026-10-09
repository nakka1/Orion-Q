"""Offline attack-detector session: typed input, domain refusals, the safe model and the console dialog."""
import io
import json
import os
from pathlib import Path
import time

import pandas as pd
import pytest

from quantum_autopilot.model_intake import detector_session as ds
from quantum_autopilot.model_intake.artifacts import QKD_ROOT, ReviewedArchive

pytestmark = pytest.mark.unit
ROOT = Path(__file__).resolve().parents[1]
QKD_ZIP = os.environ.get("ORION_QKD_ARCHIVE", "")


def real_zip():
    if not QKD_ZIP or not Path(QKD_ZIP).exists():
        pytest.skip("pinned modelo_qkd_ml.zip not supplied (set ORION_QKD_ARCHIVE)")
    return QKD_ZIP


@pytest.fixture(scope="module")
def demo():
    return ds.demo_detector_input()


def _changed(text, **changes):
    value = json.loads(text)
    for key, change in changes.items():
        value[key] = change(value[key]) if callable(change) else change
    return json.dumps(value)


def _column(name):
    return ds.COLUMNS.index(name)


def test_demo_is_the_author_plant_and_parses(demo):
    request, column = ds.parse_detector_input(demo)
    assert request["stream_id"].startswith("demo/author-plant/intercept_resend/35km")
    assert len(request["rows"]) == 240 and set(column["length_km"]) == {35.0}
    assert ds.demo_detector_input() == demo   # deterministic


def _set(position, value, rows=slice(None)):
    def change(table):
        for row in table[rows]:
            row[position] = value
        return table
    return change


@pytest.mark.parametrize("changes,message", [
    ({"evidence": "measured"}, "simulated_offline"),
    ({"schema": "orion.optional-detector-input.v0"}, "schema"),
    ({"columns": lambda c: c[::-1]}, "17 public session columns"),
    ({"rows": lambda r: r[:89]}, "between 90 and 900"),
    ({"rows": lambda r: r[1:]}, "periods must be 0, 1, 2"),
    ({"rows": _set(_column("length_km"), 80.0)}, "20-70 km"),
    ({"rows": _set(_column("length_km"), 50.0, slice(0, 10))}, "one span length"),
    ({"rows": _set(_column("signal_intensity"), 0.6)}, "signal_intensity outside"),
    ({"rows": _set(_column("pulses"), 1e8)}, "pulses outside"),
    ({"rows": _set(_column("signal_error"), -0.1, slice(3, 4))}, "nonnegative"),
    ({"rows": _set(_column("signal_gain"), True, slice(3, 4))}, "finite number"),
    ({"stream_id": "a\nb"}, "control characters"),
])
def test_out_of_contract_input_is_refused(demo, changes, message):
    with pytest.raises(ValueError, match=message):
        ds.parse_detector_input(_changed(demo, **changes))


def test_size_limit_and_duplicate_fields(demo, tmp_path):
    with pytest.raises(ValueError, match="256 KiB"):
        ds.parse_detector_input(demo + " " * ds.MAX_INPUT_BYTES)
    with pytest.raises(ValueError, match="duplicate"):
        ds.parse_detector_input('{"schema": 1, "schema": 2}')
    path = tmp_path / "big.json"
    path.write_bytes(b" " * (ds.MAX_INPUT_BYTES + 1))
    with pytest.raises(ValueError, match="exceeds 256 KiB"):
        ds.load_detector_input(path)


def test_author_qber_calibration_is_the_recorded_one():
    settings = json.loads((ROOT / "external/qnet_qkd/runs/detector/settings.json").read_text(encoding="utf-8"))
    assert ds.AUTHOR_QBER_SIGMA == settings["threshold_sigma"]


def test_evaluation_matches_the_exported_example_and_exports_once(tmp_path):
    path = real_zip()
    archive = ReviewedArchive(path, "qkd")
    public = pd.read_csv(io.BytesIO(archive.read(f"{QKD_ROOT}exemplos/detector_pns_entrada.csv")))
    expected = pd.read_csv(io.BytesIO(archive.read(f"{QKD_ROOT}exemplos/detector_pns_saida.csv")))
    text = json.dumps({"schema": ds.SCHEMA, "evidence": "simulated_offline", "stream_id": "author export pns",
                       "columns": list(ds.COLUMNS),
                       "rows": [[float(v) for v in row] for row in public[list(ds.COLUMNS)].itertuples(index=False)]})
    result = ds.evaluate_detector_session(path, text)
    assert result.archive_sha256 == archive.sha256 and result.physical_action_performed is False
    assert result.periods_scored == len(expected) == 540
    assert [row["alarm"] for row in result.periods] == expected["alarm"].tolist()
    assert [row["per_period"] for row in result.periods] == expected["per_period"].tolist()
    assert result.first_alarm_s == 231.0 and result.alarm_periods["pns"] > 300
    assert "CLASSIFICATION ONLY · NO ACTION" in result.describe()
    destination = tmp_path / "out.json"
    ds.export_detector_evaluation(result, destination)
    with pytest.raises(FileExistsError):
        ds.export_detector_evaluation(result, destination)
    assert json.loads(destination.read_text(encoding="utf-8"))["model_id"] == "qkd.detector.boosting"


def test_console_session_runs_the_demo_only_on_request(demo):
    path = real_zip()
    pytest.importorskip("PySide6")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from quantum_autopilot.console import theme
    from quantum_autopilot.console.widgets.detector_session import DetectorSession
    from quantum_autopilot.model_intake.verification import verify_model_archive

    app = QApplication.instance() or QApplication([])
    theme.install(app)
    dialog = DetectorSession()
    try:
        assert not dialog.run_button.isEnabled() and not dialog.demo_button.isEnabled()
        dialog.set_package(verify_model_archive("qkd.detector.boosting", path))
        dialog.show()
        dialog.demo_button.clicked.emit()
        assert dialog.job.result is None and not dialog.job.active
        dialog.run_button.clicked.emit()
        deadline = time.monotonic() + 60
        while dialog.job.active and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.01)
        assert not dialog.job.error and dialog.job.result is not None
        assert dialog.job.result.periods_scored == 180
        assert "QBER alarm baseline" in dialog.output.toPlainText()
        dialog.editor.appendPlainText(" ")
        assert dialog.job.result is None
    finally:
        dialog.close()
        dialog.deleteLater()
        app.processEvents()
