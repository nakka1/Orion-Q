"""Run the packaged institutional diagnostic against real Qt and demo services."""
import json
import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtCore import QCoreApplication, QEvent  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from quantum_autopilot.console import theme  # noqa: E402
from quantum_autopilot.console.institutional_validation import validate_institutional_demo  # noqa: E402
from quantum_autopilot.quantum_protocols.orbital_relay_topology import SESSION_ID  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.integration
def test_institutional_diagnostic_uses_session_mapping_and_real_demo_backend(tmp_path):
    app = QApplication.instance() or QApplication([])
    theme.set_theme("dark")
    theme.install(app)
    destination = tmp_path / "institutional-diagnostic"
    assert not destination.exists()
    try:
        report = validate_institutional_demo(destination, ROOT)

        assert report["passed"], report["checks"]
        assert report["check_count"] == len(report["checks"])
        assert all(report["checks"].values())
        assert report["checks"]["hybrid_drivers_registered"]
        live = report["live"]
        assert len(live["nodes"]) == 76
        assert len(live["links"]) == 136
        sessions = live["technologies"]["sessions"]
        assert isinstance(sessions, dict)
        assert len(sessions) == 38
        assert {"RIO-SNS-FINITE", SESSION_ID} <= set(sessions)
        for session_id in ("RIO-SNS-FINITE", SESSION_ID):
            assert sessions[session_id]["config"]["session_id"] == session_id
        assert live["technologies"]["origin"] == "simulated"
        assert live["technologies"]["physical_actuation"] is False
        assert live["technologies"]["physical_adapter_validated"] is False
        assert len(report["receipts"]) == 2
        assert all(receipt["physical_action_performed"] is False for receipt in report["receipts"])
        persisted = json.loads((destination / "report.json").read_text(encoding="utf-8"))
        assert persisted == report
    finally:
        app.processEvents()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        app.processEvents()
