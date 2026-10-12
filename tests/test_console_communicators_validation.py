"""Finite SNS diagnostic ordering and full Qt/backend communicator workflows."""
from copy import deepcopy
import json
import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtCore import QCoreApplication, QEvent  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from quantum_autopilot.console import theme  # noqa: E402
from quantum_autopilot.console.communicators_validation import (  # noqa: E402
    ACTIVATION_STAGE_ORDER, _activation_receipt_summary, validate_communicators_geo,
)
from quantum_autopilot.quantum_protocols.contracts import digest  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
COMMAND_ID = "CHECK-TF:0:1"


@pytest.fixture
def trace():
    events = [{"command_id": COMMAND_ID, "session_id": "CHECK-TF", "event": stage,
               "at_s": float(index), "detail": {}}
              for index, stage in enumerate(ACTIVATION_STAGE_ORDER)]
    events[0]["detail"] = {"command": {"command_id": COMMAND_ID, "session_id": "CHECK-TF",
                                     "deadline_s": 100.0, "classical_channel_ids": ["CA", "CB"],
                                     "quantum_channel_ids": ["QA", "QB"]}}
    message = {"message_id": f"{COMMAND_ID}:public:1", "command_id": COMMAND_ID,
               "session_id": "CHECK-TF", "sequence": 1, "stage": "sifting",
               "classical_channel_ids": ["CA", "CB"], "associated_quantum_ids": ["QA", "QB"],
               "transmitted_at_s": 9.0, "received_at_s": 10.0, "payload": {"public_count": 20},
               "authenticated": False, "transport_scope": "local causal channel emulation"}
    message["receipt_sha256"] = digest(message)
    events[9]["detail"] = deepcopy(message)
    events[10]["detail"] = deepcopy(message)
    events[11]["detail"] = {"classical_exchanges_received": 1}
    return events


def resign(message):
    message["receipt_sha256"] = digest({key: value for key, value in message.items()
                                        if key != "receipt_sha256"})


def two_exchange_trace(trace):
    result = deepcopy(trace)
    sent, received = deepcopy(result[9]), deepcopy(result[10])
    for event in (sent, received):
        event["detail"].update(message_id=f"{COMMAND_ID}:public:2", sequence=2,
                              transmitted_at_s=10.1, received_at_s=10.2)
        resign(event["detail"])
    sent["at_s"], received["at_s"] = 10.1, 10.2
    result[11]["detail"]["classical_exchanges_received"] = 2
    result[11:11] = [sent, received]
    return result


def test_finite_order_requires_classical_receipts_before_protocol_and_metrics(trace):
    summary = _activation_receipt_summary(trace)
    assert summary["completed_command_ids"] == [COMMAND_ID]
    assert summary["completed_commands"][0]["public_exchanges_received"] == 1
    assert "not authenticated physical communication" in summary["scope"]
    summary = _activation_receipt_summary(two_exchange_trace(trace))
    assert summary["completed_command_ids"] == [COMMAND_ID]
    assert summary["completed_commands"][0]["public_exchanges_received"] == 2


@pytest.mark.parametrize("stage", ACTIVATION_STAGE_ORDER)
def test_missing_stage_cannot_be_counted_as_complete(trace, stage):
    assert not _activation_receipt_summary([event for event in trace if event["event"] != stage])[
        "completed_command_ids"]


@pytest.mark.parametrize("first,second", [
    ("classical_received", "protocol_processed"),
    ("postprocessing_classical_received", "protocol_processed"),
    ("metrics_computed", "protocol_processed"),
    ("postprocessing_classical_transmitted", "postprocessing_classical_received"),
])
def test_reordered_stages_are_rejected(trace, first, second):
    positions = {event["event"]: index for index, event in enumerate(trace)}
    a, b = positions[first], positions[second]
    trace[a], trace[b] = trace[b], trace[a]
    assert not _activation_receipt_summary(trace)["completed_command_ids"]


@pytest.mark.parametrize("fault", ["missing_final_receive", "protocol_before_final_receive"])
def test_last_public_receipt_must_precede_admission(trace, fault):
    events = two_exchange_trace(trace)
    if fault == "missing_final_receive":
        events.pop(12)
    else:
        events[12], events[13] = events[13], events[12]
    assert not _activation_receipt_summary(events)["completed_command_ids"]


@pytest.mark.parametrize("field,value", [
    ("command_id", "OTHER"), ("session_id", "OTHER"), ("sequence", 2),
    ("message_id", "unbound-message"), ("classical_channel_ids", ["QB"]),
    ("associated_quantum_ids", []),
])
def test_public_receipts_must_bind_command_session_sequence_and_both_channels(trace, field, value):
    for event in trace[9:11]:
        event["detail"][field] = value
        resign(event["detail"])
    assert not _activation_receipt_summary(trace)["completed_command_ids"]


@pytest.mark.parametrize("fault", ["hash", "different_receive_body", "zero_latency", "deadline", "nan_clock",
                                   "wrong_admitted_count"])
def test_invalid_receipt_integrity_timing_or_admitted_count_is_rejected(trace, fault):
    if fault == "hash":
        for event in trace[9:11]:
            event["detail"]["receipt_sha256"] = "0" * 64
    elif fault == "different_receive_body":
        trace[10]["detail"]["payload"]["public_count"] += 1
    elif fault == "zero_latency":
        for event in trace[9:11]:
            event["detail"]["received_at_s"] = 9.0
            resign(event["detail"])
        trace[10]["at_s"] = 9.0
    elif fault == "deadline":
        trace[0]["detail"]["command"]["deadline_s"] = 10.0
    elif fault == "nan_clock":
        trace[-1]["at_s"] = float("nan")
    else:
        trace[11]["detail"]["classical_exchanges_received"] = 2
    assert not _activation_receipt_summary(trace)["completed_command_ids"]


@pytest.mark.integration
def test_full_communicators_diagnostic_real_qt_backend_and_finite_pipeline(tmp_path):
    app = QApplication.instance() or QApplication([])
    theme.set_theme("dark")
    theme.install(app)
    destination = tmp_path / "communicators-diagnostic"
    assert not destination.exists()
    try:
        report = validate_communicators_geo(destination, ROOT)
        assert report["passed"], report.get("error") or report["checks"]
        assert report["check_count"] == len(report["checks"]) == 22
        assert all(report["checks"].values())
        assert report["checks"]["activation_runs_causal_pipeline"]
        assert report["physical_action_performed"] is False
        assert report["live"]["technologies"]["physical_actuation"] is False
        assert report["live"]["technologies"]["physical_adapter_validated"] is False
        assert report["live"]["technologies"]["sessions"]["CHECK-TF"]["config"]["parameters"][
            "tf_estimation_mode"] == "finite_sns_v2"
        persisted = json.loads((destination / "report.json").read_text(encoding="utf-8"))
        assert persisted == json.loads(json.dumps(report))
        receipts = json.loads((destination / "activation-receipts.json").read_text(encoding="utf-8"))
        summary = _activation_receipt_summary(receipts)
        assert summary == report["activation_receipts"]
        assert len(summary["completed_command_ids"]) >= 1
        assert all(row["public_exchanges_received"] > 0 and row["metrics_at_s"] <= row["deadline_s"]
                   for row in summary["completed_commands"])
    finally:
        app.processEvents()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        app.processEvents()
