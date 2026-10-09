"""Tests for the measured optical/QKD/simulated-Bell evidence boundary."""

import hashlib
import json
from datetime import datetime, timezone

import pandas as pd
import pytest

from quantum_autopilot.control_plane import (
    ControlAction,
    ControlIntent,
    ControlMode,
    NetworkStateSnapshot,
    SafetySupervisor,
)
from quantum_autopilot.core import EvidenceKind, Provenance
from quantum_autopilot.evidence_lanes import (
    EvidenceLane,
    EvidenceLaneRegistry,
    EvidenceLaneService,
    MeasuredOpticalHealthAdapter,
)


def _write_optical_fixture(tmp_path):
    path = tmp_path / "HardFailure_dataset.csv"
    rows = [
        [1623394634, "Devices", "SPO1/18/11", 2.28e-8, 38.5, None, None, None],
        [1623394635, "Infrastructure", "Ampli1", None, None, -23.0, 0.7, None],
        [1623394635, "Devices", "SPO2/18/11", 8.73e-7, 23.5, None, None, None],
        [1623394638, "Infrastructure", "Ampli1", None, None, -30.0, 0.8, 1.0],
        [1623394638, "Devices", "SPO1/18/11", 1.72e-8, 38.6, None, None, 1.0],
        [1623394638, "Devices", "SPO2/18/11", 8.68e-7, 23.6, None, None, 1.0],
    ]
    pd.DataFrame(
        rows,
        columns=[
            "Timestamp",
            "Type",
            "ID",
            "BER",
            "OSNR",
            "InputPower",
            "OutputPower",
            "Failure",
        ],
    ).to_csv(path, index=False)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    report_path = tmp_path / "report.json"
    report_path.write_text(
        json.dumps(
            {
                "dataset": {"sha256": digest, "quantum_target_present": False},
                "evidence_positive_count": 0,
                "evidence_test_count": 16,
            }
        ),
        encoding="utf-8",
    )
    return path, report_path, digest


@pytest.mark.unit
def test_registry_declares_three_non_interchangeable_lanes():
    registry = EvidenceLaneRegistry()
    descriptors = registry.describe()

    assert [item.lane for item in descriptors] == list(EvidenceLane)
    assert descriptors[0].evidence_kind is EvidenceKind.MEASURED
    assert descriptors[1].target_semantics == "measured_qber_and_secure_key_rate"
    assert descriptors[2].target_semantics == "simulated_future_bell_fidelity"
    assert descriptors[2].control_plane_ingress_allowed is True
    assert all(item.physical_action_available is False for item in descriptors)
    assert all(item.metric_conversion_allowed is False for item in descriptors)


@pytest.mark.unit
@pytest.mark.parametrize(
    "lane",
    [EvidenceLane.WDM_OPTICAL_HEALTH, EvidenceLane.QKD_ASSURANCE],
)
@pytest.mark.parametrize("action", [ControlAction.PURIFY, ControlAction.SWAP])
def test_optical_and_qkd_lanes_fail_closed_before_bell_control(lane, action):
    result = EvidenceLaneRegistry().assess_control_ingress(lane, action)

    assert result.sandbox_ingress_allowed is False
    assert result.effective_action is ControlAction.HALT
    assert result.reason_code == "evidence_lane_not_bell_fidelity"
    assert result.final_action_authorized is False
    assert result.physical_action_performed is False


@pytest.mark.unit
def test_simulated_bell_lane_only_passes_to_independent_safety_gate():
    result = EvidenceLaneRegistry().assess_control_ingress(
        EvidenceLane.BELL_FIDELITY_CONTROL,
        ControlAction.PURIFY,
        evidence_kind=EvidenceKind.SIMULATED,
    )

    assert result.sandbox_ingress_allowed is True
    assert result.effective_action is ControlAction.PURIFY
    assert result.requires_downstream_safety is True
    assert result.final_action_authorized is False


@pytest.mark.unit
def test_measured_bell_lane_is_not_implemented_yet():
    result = EvidenceLaneRegistry().assess_control_ingress(
        EvidenceLane.BELL_FIDELITY_CONTROL,
        ControlAction.SWAP,
        evidence_kind=EvidenceKind.MEASURED,
    )

    assert result.sandbox_ingress_allowed is False
    assert result.effective_action is ControlAction.HALT
    assert result.reason_code == "measured_bell_control_ingress_not_implemented"


@pytest.mark.unit
def test_measured_optical_adapter_exposes_real_kpis_without_quantum_conversion(tmp_path):
    csv_path, report_path, digest = _write_optical_fixture(tmp_path)
    snapshot = MeasuredOpticalHealthAdapter(
        csv_path,
        forecast_report_path=report_path,
        expected_dataset_sha256=digest,
    ).snapshot()

    assert snapshot.provenance.evidence_kind is EvidenceKind.MEASURED
    assert snapshot.provenance.dataset_hash == digest
    assert snapshot.quality["rows"] == 6
    assert snapshot.quality["validation_valid"] is True
    assert snapshot.prediction_status == "no_incremental_predictive_value_detected"
    assert snapshot.evidence_positive_count == 0
    assert snapshot.evidence_test_count == 16
    assert len(snapshot.devices) == 3
    states = {item.device_id: item for item in snapshot.devices}
    assert states["SPO1/18/11"].osnr_db == pytest.approx(38.6)
    assert states["Ampli1"].input_power_dbm == pytest.approx(-30.0)
    assert snapshot.quantum_target_present is False
    assert snapshot.bell_fidelity_present is False
    assert snapshot.metric_conversion_performed is False
    assert snapshot.control_plane_ingress_allowed is False


@pytest.mark.unit
def test_optical_adapter_rejects_report_from_a_different_dataset(tmp_path):
    csv_path, report_path, _ = _write_optical_fixture(tmp_path)
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["dataset"]["sha256"] = "0" * 64
    report_path.write_text(json.dumps(report), encoding="utf-8")

    with pytest.raises(ValueError, match="hash does not match"):
        MeasuredOpticalHealthAdapter(
            csv_path, forecast_report_path=report_path
        ).snapshot()


@pytest.mark.unit
def test_lane_service_reports_unconfigured_optical_source_without_guessing_a_path():
    service = EvidenceLaneService()
    status = service.status()

    assert status["lane_count"] == 3
    assert status["cross_metric_conversion_allowed"] is False
    assert status["lanes"][0]["source_configured"] is False
    with pytest.raises(ValueError, match="not configured"):
        service.optical_health()


@pytest.mark.unit
def test_safety_supervisor_rejects_measured_qkd_for_pair_operation():
    timestamp = datetime.now(timezone.utc).isoformat()
    snapshot = NetworkStateSnapshot(
        request_id="measured-qkd-control-attempt",
        node_id="repeater-a",
        timestamp_utc=timestamp,
        state_version=1,
        predicted_fidelity=0.9,
        prediction_uncertainty=0.01,
        telemetry_age_s=0.1,
        memory_state="STORING",
        available_pair_fidelities=(0.9, 0.9),
        memory_ages_s=(0.0, 0.0),
        integrity_verified=True,
        operator_interlock=True,
        provenance=Provenance(
            evidence_kind=EvidenceKind.MEASURED,
            source="published-qkd-cell",
            metadata={
                "evidence_lane": EvidenceLane.QKD_ASSURANCE.value,
                "target_semantics": "measured_qber_and_secure_key_rate",
            },
        ),
    )
    intent = ControlIntent(
        intent_id="qkd-intent",
        request_id=snapshot.request_id,
        node_id=snapshot.node_id,
        action=ControlAction.PURIFY,
        expected_state_version=snapshot.state_version,
        issued_at_utc=timestamp,
        policy_name="adversarial-test",
    )

    result = SafetySupervisor().assess(ControlMode.SHADOW, intent, snapshot)

    assert result.authorized is False
    assert result.effective_action is ControlAction.HALT
    assert "evidence_lane_not_authorized_for_bell_control" in {
        violation.reason_code for violation in result.violations
    }
