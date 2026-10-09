"""Tests for the loopback-only WDM/QKD assurance dashboard API."""

import http.client
import json
from datetime import datetime, timezone
from pathlib import Path
from threading import Thread

import pytest

from quantum_autopilot.qkd.dashboard_api import (
    AssuranceDashboardService,
    create_server,
)
from tests.test_wdm_model_bundle import _export as export_test_bundle


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
CAMPAIGN_ID = "roadm_3dbm_injected_signal_no_added_fibre_full_sweep"


class AuthenticatedHTTPConnection(http.client.HTTPConnection):
    """Legitimate API cases explicitly authenticate; attack cases live separately."""
    def __init__(self, server, timeout=5):
        super().__init__("127.0.0.1", server.server_port, timeout=timeout)
        self._bearer = server.service.security.issue_local_session()

    def request(self, method, url, body=None, headers=None, **kwargs):
        options = {"Authorization": "Bearer " + self._bearer, **(headers or {})}
        return super().request(method, url, body=body, headers=options, **kwargs)


@pytest.fixture
def service(tmp_path):
    return AssuranceDashboardService(
        repository_root=REPOSITORY_ROOT,
        control_journal_path=tmp_path / "control-plane.sqlite",
    )


def simulated_snapshot(request_id="api-cycle-001"):
    return {
        "request_id": request_id,
        "node_id": "repeater-api",
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "state_version": 1,
        "predicted_fidelity": 0.9,
        "prediction_uncertainty": 0.01,
        "telemetry_age_s": 0.1,
        "memory_state": "STORING",
        "available_pair_fidelities": [0.9, 0.9],
        "memory_ages_s": [0.0, 0.0],
        "telemetry": {"osnr_db": 24.0},
        "out_of_distribution": False,
        "integrity_verified": True,
        "operator_interlock": True,
        "provenance": {
            "evidence_kind": "simulated",
            "source": "dashboard-api-test",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "metadata": {
                "evidence_lane": "bell_fidelity_control",
                "target_semantics": "simulated_future_bell_fidelity",
            },
        },
    }


@pytest.mark.unit
def test_summary_is_built_from_validated_artifacts(service):
    summary = service.summary()

    assert summary["integrity"]["valid"] is True
    assert summary["integrity"]["check_count"] == 69
    assert summary["integrity"]["failed_check_count"] == 0
    assert summary["integrity"]["grid_source_artifact_verified"] is True
    assert summary["product"]["read_only"] is True
    assert summary["product"]["automation_allowed"] is False
    assert len(summary["claims"]) == 7
    assert len(summary["operating_grid"]["campaigns"]) == 10
    assert summary["operating_grid"]["expected_cell_count"] == 80


@pytest.mark.unit
def test_dashboard_exposes_three_evidence_lanes_without_optical_path_guessing(service):
    status = service.evidence_lanes()

    assert status["api_version"] == "1.3.0"
    assert status["lane_count"] == 3
    assert [item["lane"] for item in status["lanes"]] == [
        "wdm_optical_health",
        "qkd_assurance",
        "bell_fidelity_control",
    ]
    assert status["lanes"][0]["source_configured"] is False
    assert status["cross_metric_conversion_allowed"] is False


@pytest.mark.unit
def test_dashboard_reads_only_the_server_configured_optical_source(tmp_path):
    csv_path = tmp_path / "HardFailure_dataset.csv"
    pd_rows = [
        "Timestamp,Type,ID,BER,OSNR,InputPower,OutputPower,Failure",
        "1623394634,Devices,SPO1/18/11,2.28e-8,38.5,,,",
        "1623394635,Infrastructure,Ampli1,,,-23.0,0.7,",
    ]
    csv_path.write_text("\n".join(pd_rows) + "\n", encoding="utf-8")
    configured = AssuranceDashboardService(
        repository_root=REPOSITORY_ROOT,
        control_journal_path=tmp_path / "control.sqlite",
        optical_wdm_csv=csv_path,
    )

    result = configured.optical_health()

    assert result["snapshot"]["quality"]["rows"] == 2
    assert result["snapshot"]["provenance"]["evidence_kind"] == "measured"
    assert result["snapshot"]["metric_conversion_performed"] is False


@pytest.mark.unit
def test_published_cell_is_loaded_server_side_with_verified_provenance(service):
    result = service.assess_published_cell(
        {"campaign_id": CAMPAIGN_ID, "voa_attenuation_db": 10}
    )

    assert result["record"]["decision"]["action"] == "PROVIDE_DECISION_SUPPORT"
    assert result["record"]["snapshot"]["observed"]["condition_scope"] == (
        "inside_validated_grid"
    )
    assert result["source_cell"]["qber_reported"] == pytest.approx(0.033308)
    assert result["source_cell"]["qber_unit_status"] == "publisher_scale_ambiguous"
    assert result["source_cell"]["secure_key_rate_bps"] == pytest.approx(302860.36)
    assert result["source_cell"]["n_measurements"] == 50
    point = result["record"]["snapshot"]["observed"]["operating_point"]
    assert point["provenance"]["evidence_kind"] == "measured"
    assert point["provenance"]["metadata"]["measurement_timestamp_status"] == (
        "not_published"
    )


@pytest.mark.unit
@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ({"campaign_id": "unknown", "voa_attenuation_db": 10}, "campaign_id"),
        ({"campaign_id": CAMPAIGN_ID, "voa_attenuation_db": 11}, "voa"),
        (
            {
                "campaign_id": CAMPAIGN_ID,
                "voa_attenuation_db": 10,
                "qber_reported": 0,
            },
            "unsupported request fields",
        ),
    ],
)
def test_published_cell_endpoint_rejects_unreviewed_or_invented_inputs(
    service, payload, message
):
    with pytest.raises(ValueError, match=message):
        service.assess_published_cell(payload)


@pytest.mark.unit
def test_generic_assessment_rejects_manual_scope_override(service):
    with pytest.raises(ValueError, match="unsupported request fields"):
        service.assess(
            {
                "claim_id": "C-MEASURED",
                "requested_use": "decision_support",
                "condition_scope": "inside_validated_grid",
            }
        )


@pytest.mark.unit
def test_server_refuses_non_loopback_binding():
    with pytest.raises(ValueError, match="loopback"):
        create_server(REPOSITORY_ROOT, host="0.0.0.0")


@pytest.mark.unit
def test_control_plane_api_runs_idempotent_simulated_cycle_and_lists_it(service):
    status = service.control_plane_status()
    point = simulated_snapshot()
    first = service.run_control_cycle(
        {"mode": "simulation", "snapshot": point}
    )
    replay = service.run_control_cycle(
        {"mode": "simulation", "snapshot": point}
    )
    events = service.control_plane_events()

    assert status["physical_mode_available"] is False
    assert status["execution_scope"] == "simulator_or_dry_run_only"
    assert status["physical_executor_available"] is False
    assert first["record"]["execution"]["status"] == "SIMULATED"
    assert first["record"]["execution"]["physical_action_performed"] is False
    assert replay["idempotent_replay"] is True
    assert events["events"][0]["request_id"] == "api-cycle-001"


@pytest.mark.unit
def test_control_plane_api_rejects_missing_or_unknown_snapshot_fields(service):
    with pytest.raises(ValueError, match="snapshot is required"):
        service.run_control_cycle({"mode": "simulation"})
    with pytest.raises(ValueError, match="invalid control-plane snapshot"):
        service.run_control_cycle(
            {"mode": "simulation", "snapshot": {"surprise": True}}
        )


@pytest.mark.unit
def test_model_bundle_status_and_bounded_shadow_replay(tmp_path):
    bundle_path, exported = export_test_bundle(tmp_path)
    configured = AssuranceDashboardService(
        repository_root=REPOSITORY_ROOT,
        control_journal_path=tmp_path / "model-replay.sqlite",
        wdm_model_bundle_path=bundle_path,
        expected_wdm_bundle_sha256=exported["bundle_sha256"],
        wdm_replay_max_cycles=5,
    )

    status = configured.wdm_model_status()
    first = configured.run_wdm_model_replay({"start_index": 0, "limit": 1})
    replay = configured.run_wdm_model_replay({"start_index": 0, "limit": 1})

    assert status["valid"] is True
    assert status["replay_available"] is True
    assert status["bundle_hash_pinned"] is True
    assert status["scientific_status"]["approved_for_control"] is False
    assert first["mode"] == "shadow"
    assert first["action_counts"] == {"PURIFY": 1}
    assert first["rows"][0]["intended_action"] == "PURIFY"
    assert first["rows"][0]["effective_action"] == "PURIFY"
    assert first["rows"][0]["physical_action_performed"] is False
    assert first["rows"][0]["outcome_category"] == "purification_intent_shadow"
    assert first["policy_spec_sha256"] == status["policy"]["policy_spec_sha256"]
    assert replay["rows"][0]["idempotent_replay"] is True
    events = configured.control_plane_events()["events"]
    assert events[0]["record_sha256"] == first["rows"][0]["record_sha256"]


@pytest.mark.unit
def test_model_replay_is_unavailable_without_bundle_and_enforces_bounds(service):
    status = service.wdm_model_status()
    assert status["configured"] is False
    assert status["replay_available"] is False
    with pytest.raises(ValueError, match="no WDM model bundle"):
        service.run_wdm_model_replay({"limit": 1})

    with pytest.raises(ValueError, match="wdm_replay_max_cycles"):
        AssuranceDashboardService(
            repository_root=REPOSITORY_ROOT,
            wdm_replay_max_cycles=251,
        )


@pytest.mark.unit
def test_control_plane_api_runs_persistent_resilience_scenario(service):
    before = service.control_plane_topology()
    first = service.run_control_scenario(
        {"scenario_id": "api-resilience-001", "preset": "resilience_demo"}
    )
    replay = service.run_control_scenario(
        {"scenario_id": "api-resilience-001", "preset": "resilience_demo"}
    )
    after = service.control_plane_topology()
    status = service.control_plane_status()

    assert before["persisted"] is False
    assert after["persisted"] is True
    assert after["physical_topology"] is False
    assert after["topology"]["topology_version"] == 6
    assert [
        step["cycle"]["record"]["safety"]["effective_action"]
        for step in first["record"]["timeline"]
    ] == ["SWAP", "HALT", "SWAP"]
    assert first["record"]["physical_action_performed"] is False
    assert replay["idempotent_replay"] is True
    assert status["persistent_topology_available"] is True
    assert status["scenario_runner_available"] is True


@pytest.mark.unit
@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ({}, "scenario_id"),
        ({"scenario_id": "bad", "preset": "unknown"}, "preset"),
        (
            {
                "scenario_id": "bad",
                "preset": "resilience_demo",
                "steps": [],
            },
            "do not accept custom steps",
        ),
        ({"scenario_id": "bad", "unexpected": True}, "unsupported request fields"),
        (
            {"scenario_id": "bad", "preset": "custom", "steps": 7},
            "invalid control-plane scenario",
        ),
    ],
)
def test_control_plane_scenario_api_rejects_invalid_requests(service, payload, message):
    with pytest.raises(ValueError, match=message):
        service.run_control_scenario(payload)


@pytest.mark.unit
def test_http_api_exposes_health_cell_assessment_and_local_cors(tmp_path):
    server = create_server(
        REPOSITORY_ROOT,
        port=0,
        control_journal_path=tmp_path / "http-control-plane.sqlite",
    )
    worker = Thread(target=server.serve_forever, daemon=True)
    worker.start()
    connection = AuthenticatedHTTPConnection(server)
    try:
        connection.request("GET", "/api/v1/health")
        health_response = connection.getresponse()
        health = json.loads(health_response.read())
        assert health_response.status == 200
        assert health_response.getheader("X-Frame-Options") == "DENY"
        assert health["read_only"] is True
        assert health["automation_allowed"] is False
        assert health["local_mutation_scope"] == (
            "sandbox_journal_simulated_topology_and_laboratory_test_evidence"
        )
        assert health["wdm_model_bundle_configured"] is False

        connection.request("GET", "/api/v1/control-plane/model")
        model_response = connection.getresponse()
        model_status = json.loads(model_response.read())
        assert model_response.status == 200
        assert model_status["configured"] is False
        assert model_status["replay_available"] is False

        connection.request(
            "OPTIONS",
            "/api/v1/assess-cell",
            headers={"Origin": "http://localhost:3000"},
        )
        cors_response = connection.getresponse()
        cors_response.read()
        assert cors_response.status == 204
        assert cors_response.getheader("Access-Control-Allow-Origin") == (
            "http://localhost:3000"
        )

        body = json.dumps(
            {"campaign_id": CAMPAIGN_ID, "voa_attenuation_db": 10}
        )
        connection.request(
            "POST",
            "/api/v1/assess-cell",
            body=body,
            headers={
                "Content-Type": "application/json",
                "Content-Length": str(len(body.encode("utf-8"))),
                "Origin": "http://127.0.0.1:3000",
            },
        )
        decision_response = connection.getresponse()
        decision = json.loads(decision_response.read())
        assert decision_response.status == 200
        assert decision["record"]["decision"]["action"] == (
            "PROVIDE_DECISION_SUPPORT"
        )

        cycle_body = json.dumps(
            {"mode": "shadow", "snapshot": simulated_snapshot("http-cycle-001")}
        )
        connection.request(
            "POST",
            "/api/v1/control-plane/cycles",
            body=cycle_body,
            headers={
                "Content-Type": "application/json",
                "Content-Length": str(len(cycle_body.encode("utf-8"))),
                "Origin": "http://localhost:3000",
            },
        )
        cycle_response = connection.getresponse()
        cycle = json.loads(cycle_response.read())
        assert cycle_response.status == 200
        assert cycle["record"]["execution"]["status"] == "SHADOW_RECORDED"
        assert cycle["record"]["execution"]["physical_action_performed"] is False

        scenario_body = json.dumps(
            {"scenario_id": "http-resilience-001", "preset": "resilience_demo"}
        )
        connection.request(
            "POST",
            "/api/v1/control-plane/scenarios",
            body=scenario_body,
            headers={
                "Content-Type": "application/json",
                "Content-Length": str(len(scenario_body.encode("utf-8"))),
                "Origin": "http://localhost:3000",
            },
        )
        scenario_response = connection.getresponse()
        scenario = json.loads(scenario_response.read())
        assert scenario_response.status == 200
        assert len(scenario["record"]["timeline"]) == 3
        assert scenario["record"]["physical_action_performed"] is False

        connection.request("GET", "/api/v1/control-plane/topology")
        topology_response = connection.getresponse()
        topology = json.loads(topology_response.read())
        assert topology_response.status == 200
        assert topology["persisted"] is True
        assert topology["topology"]["topology_version"] == 6
    finally:
        connection.close()
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)


@pytest.mark.unit
def test_http_api_rejects_invalid_json_and_non_local_cors():
    server = create_server(REPOSITORY_ROOT, port=0)
    worker = Thread(target=server.serve_forever, daemon=True)
    worker.start()
    connection = AuthenticatedHTTPConnection(server)
    try:
        connection.request(
            "OPTIONS",
            "/api/v1/assess",
            headers={"Origin": "https://example.com"},
        )
        cors_response = connection.getresponse()
        cors_response.read()
        assert cors_response.status == 403

        connection.request(
            "POST",
            "/api/v1/assess",
            body="{broken",
            headers={"Content-Length": "7", "Content-Type": "application/json"},
        )
        invalid_response = connection.getresponse()
        invalid = json.loads(invalid_response.read())
        assert invalid_response.status == 400
        assert invalid["error"] == "invalid_json"
    finally:
        connection.close()
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)


@pytest.mark.unit
def test_http_server_can_serve_static_dashboard_on_the_same_loopback_origin(tmp_path):
    dashboard = tmp_path / "dashboard"
    assets = dashboard / "_next" / "static"
    assets.mkdir(parents=True)
    (dashboard / "index.html").write_text(
        "<!doctype html><title>NISQ Predictive Twin</title>",
        encoding="utf-8",
    )
    (assets / "app.js").write_text("globalThis.dashboardReady=true;", encoding="utf-8")
    server = create_server(
        REPOSITORY_ROOT,
        port=0,
        control_journal_path=tmp_path / "desktop.sqlite",
        dashboard_root=dashboard,
    )
    worker = Thread(target=server.serve_forever, daemon=True)
    worker.start()
    connection = AuthenticatedHTTPConnection(server)
    try:
        connection.request("GET", "/")
        index_response = connection.getresponse()
        index_body = index_response.read().decode("utf-8")
        assert index_response.status == 200
        assert "NISQ Predictive Twin" in index_body
        assert index_response.getheader("Content-Security-Policy")
        assert index_response.getheader("X-Frame-Options") == "DENY"

        connection.request("GET", "/_next/static/app.js")
        asset_response = connection.getresponse()
        assert asset_response.status == 200
        assert asset_response.getheader("Cache-Control") == (
            "public, max-age=31536000, immutable"
        )
        assert b"dashboardReady" in asset_response.read()

        connection.request("GET", "/../docs/thesis/tcc_claim_registry.json")
        traversal_response = connection.getresponse()
        traversal_response.read()
        assert traversal_response.status == 404

        connection.request("GET", "/api/v1/health")
        health_response = connection.getresponse()
        assert health_response.status == 200
        assert json.loads(health_response.read())["physical_read_only"] is True
    finally:
        connection.close()
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)
