"""The packaged chart validator must exercise raster evidence in both themes."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from quantum_autopilot.console.fluency_validation import validate_wave_continuity


def test_packaged_chart_validator_checks_the_delivery_boundary(tmp_path):
    app = QApplication.instance() or QApplication([])
    report = validate_wave_continuity(tmp_path)
    assert report["passed"], report["checks"]
    assert report["check_count"] == 16
    assert report["physical_action_performed"] is False
    assert app is QApplication.instance()
    assert {path.name for path in tmp_path.glob("*.png")} == {
        "dark-before.png", "dark-after.png", "light-before.png", "light-after.png"}
