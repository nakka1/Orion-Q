"""Contextual observation must remain observational and refuse stale/domain mismatches."""

from datetime import datetime, timedelta, timezone
import json
import sqlite3

import pytest

from quantum_autopilot.control_plane import policy_context as pc
from quantum_autopilot.control_plane.contracts import NetworkStateSnapshot
from quantum_autopilot.control_plane.topology import default_simulated_topology, _sha256
from quantum_autopilot.control_plane.policy import RepeaterSandboxPolicy
from quantum_autopilot.core import Provenance, EvidenceKind
from quantum_autopilot.qkd.dashboard_api import AssuranceDashboardService
from quantum_autopilot.model_intake.catalog import model_options
from tests.test_policy_preparation import prepare, review


@pytest.fixture
def local(tmp_path, monkeypatch):
    service = AssuranceDashboardService(control_journal_path=tmp_path / "simulation.sqlite")
    runtime = service.controller_runtime
    topology, _ = service.topology_store.initialize(default_simulated_topology())
    node = next(n for n in topology.nodes if n.role.value == "repeater")
    now = datetime.now(timezone.utc)
    monkeypatch.setattr(pc, "_now", lambda: now)
    state = NetworkStateSnapshot(
        request_id="context-only",
        node_id=node.node_id,
        timestamp_utc=now.isoformat(),
        state_version=node.state_version,
        predicted_fidelity=0.9,
        prediction_uncertainty=0.01,
        telemetry_age_s=0.1,
        memory_state=node.memory_state,
        available_pair_fidelities=node.available_pair_fidelities,
        memory_ages_s=node.memory_ages_s,
        telemetry=node.telemetry,
        integrity_verified=True,
        operator_interlock=False,
        provenance=Provenance(
            evidence_kind=EvidenceKind.SIMULATED,
            source="isolated-context-fixture",
            timestamp=now.isoformat(),
            metadata=dict(
                topology_id=topology.topology_id,
                evidence_lane="bell_fidelity_control",
                target_semantics="simulated_future_bell_fidelity",
                physical_calibration=False,
            ),
        ),
    )
    context = service.policy_compatibility_context()
    payload = dict(
        mode="simulation",
        candidate_id=pc.TARGET_POLICY_ID,
        expected_context_sha256=context["record"]["context_sha256"],
        snapshot=state.to_dict(),
    )
    return service, runtime, payload


def test_observation_preserves_database_policy_and_pending_proposal(local, monkeypatch, tmp_path):
    service, runtime, payload = local
    proposal = prepare(service)
    policy = runtime.policy
    before = runtime.journal.path.read_bytes()

    def forbidden(*a, **kw):
        pytest.fail("observation cannot decide, execute, load or assess an action")

    monkeypatch.setattr(runtime, "cycle", forbidden)
    monkeypatch.setattr(policy, "decide", forbidden)
    monkeypatch.setattr(runtime.supervisor, "assess", forbidden)
    from quantum_autopilot.model_intake import catalog

    monkeypatch.setattr(catalog, "open_optional_model", forbidden)
    receipt = service.observe_policy_compatibility(payload)
    r = receipt["record"]
    assert r["status"] == "OBSERVED_SUPPORTED_INPUT" and r["target_input_checks_passed"]
    assert receipt["record_sha256"] == _sha256(r)
    assert r["context_sha256"] == payload["expected_context_sha256"] and r["input_sha256"] == _sha256(
        payload["snapshot"]
    )
    assert r["input_origin"] == "caller_supplied" and not r["input_source_authenticated"]
    assert not any(
        r[k]
        for k in (
            "selection_available",
            "activation_available",
            "preparation_available",
            "operator_approval_recorded",
            "per_action_safety_assessed",
            "model_identity_observed",
            "decisions_executed",
            "commands_emitted",
            "physical_action_performed",
        )
    )
    assert runtime.policy is policy and runtime.journal.path.read_bytes() == before
    assert review(service, proposal)["record"]["status"] == "CURRENT"
    # Caller changes cannot alter the session or a subsequent observation.
    receipt["record"]["context"]["baseline"]["threshold"] = 0
    assert service.observe_policy_compatibility(payload)["record"]["context"]["baseline"]["threshold"] == 0.65


@pytest.mark.parametrize("candidate", [o.model_id for o in model_options()])
def test_every_optional_component_remains_blocked_even_when_target_input_passes(local, candidate):
    service, _, payload = local
    payload["candidate_id"] = candidate
    r = service.observe_policy_compatibility(payload)["record"]
    assert r["target_input_checks_passed"] and not r["candidate_domain_validated"]
    assert r["status"] == "BLOCKED"
    assert "CANDIDATE_ACTION_MAPPING_BLOCKED" in {x["code"] for x in r["blocking_reasons"]}


@pytest.mark.parametrize(
    "field,value,code",
    [
        ("node_id", "absent", "REPEATER_REQUIRED"),
        ("state_version", 99, "NODE_STATE_VERSION_MISMATCH"),
        ("memory_state", "IDLE", "NODE_MEMORY_STATE_MISMATCH"),
        ("prediction_uncertainty", 0.9, "UNCERTAINTY_LIMIT"),
        ("telemetry_age_s", 6.0, "STALE_INPUT"),
        ("out_of_distribution", True, "DECLARED_OOD"),
        ("integrity_verified", False, "DECLARED_INTEGRITY_MISSING"),
    ],
)
def test_input_mismatches_are_recorded_without_authority(local, field, value, code):
    service, _, payload = local
    payload["snapshot"][field] = value
    r = service.observe_policy_compatibility(payload)["record"]
    assert r["status"] == "BLOCKED" and code in {x["code"] for x in r["blocking_reasons"]}


@pytest.mark.parametrize(
    "key,value,code",
    [
        ("topology_id", "other", "TOPOLOGY_ID_MISMATCH"),
        ("evidence_lane", "qkd", "INPUT_DOMAIN_UNSUPPORTED"),
        ("target_semantics", "measured_qber", "INPUT_DOMAIN_UNSUPPORTED"),
        ("physical_calibration", True, "INPUT_DOMAIN_UNSUPPORTED"),
    ],
)
def test_provenance_semantics_fail_closed(local, key, value, code):
    service, _, payload = local
    payload["snapshot"]["provenance"]["metadata"][key] = value
    r = service.observe_policy_compatibility(payload)["record"]
    assert code in {x["code"] for x in r["blocking_reasons"]}


@pytest.mark.parametrize("delta,code", [(-6, "STALE_INPUT"), (1, "FUTURE_INPUT")])
def test_timestamp_age_cannot_be_reset_by_claimed_zero_age(local, delta, code):
    service, _, payload = local
    stamp = (pc._now() + timedelta(seconds=delta)).isoformat()
    payload["snapshot"]["timestamp_utc"] = stamp
    payload["snapshot"]["provenance"]["timestamp"] = stamp
    payload["snapshot"]["telemetry_age_s"] = 0
    assert code in {
        x["code"] for x in service.observe_policy_compatibility(payload)["record"]["blocking_reasons"]
    }


def test_measured_provenance_and_clock_mismatch_are_blocked(local):
    service, _, payload = local
    payload["snapshot"]["provenance"]["evidence_kind"] = "measured"
    payload["snapshot"]["provenance"]["timestamp"] = (pc._now() - timedelta(seconds=1)).isoformat()
    codes = {x["code"] for x in service.observe_policy_compatibility(payload)["record"]["blocking_reasons"]}
    assert {"NON_SIMULATED_INPUT", "INPUT_CLOCK_MISMATCH"} <= codes


@pytest.mark.parametrize(
    "change",
    ["mode", "extra", "missing", "bad_hash", "incomplete_snapshot", "nan", "oversize", "unknown_candidate"],
)
def test_malformed_requests_do_not_replace_context(local, change):
    service, _, payload = local
    bad = json.loads(json.dumps(payload))
    if change == "mode":
        bad["mode"] = "physical"
    elif change == "extra":
        bad["verified"] = True
    elif change == "missing":
        del bad["snapshot"]
    elif change == "bad_hash":
        bad["expected_context_sha256"] = "A" * 64
    elif change == "incomplete_snapshot":
        del bad["snapshot"]["integrity_verified"]
    elif change == "nan":
        bad["snapshot"]["predicted_fidelity"] = float("nan")
    elif change == "oversize":
        bad["snapshot"]["metadata"]["padding"] = "x" * 65536
    else:
        bad["candidate_id"] = "unknown"
    with pytest.raises(ValueError):
        service.observe_policy_compatibility(bad)
    assert service.observe_policy_compatibility(payload)["record"]["status"] == "OBSERVED_SUPPORTED_INPUT"


@pytest.mark.parametrize(
    "change", ["threshold", "same_policy", "supervisor", "topology", "runtime", "source", "journal"]
)
def test_context_changes_refuse_old_reference(local, change, tmp_path):
    service, runtime, payload = local
    if change == "threshold":
        runtime.policy = RepeaterSandboxPolicy(0.7)
    elif change == "same_policy":
        runtime.policy = RepeaterSandboxPolicy()
    elif change == "supervisor":
        from quantum_autopilot.control_plane.safety import SafetySupervisor

        runtime.supervisor = SafetySupervisor()
    elif change == "topology":
        with sqlite3.connect(runtime.journal.path) as con:
            row = json.loads(con.execute("select state_json from simulated_topologies").fetchone()[0])
            row["links"][0]["distance_km"] += 1
            con.execute("update simulated_topologies set state_json=?", (json.dumps(row),))
    elif change == "runtime":
        from quantum_autopilot.control_plane.runtime import ControllerRuntime

        service._controller_runtime = ControllerRuntime(runtime.journal.path)
    elif change == "source":
        service.invalidate_policy_preparation()
    else:
        runtime.journal.path = tmp_path / "different.sqlite"
    with pytest.raises((ValueError, FileNotFoundError)):
        service.observe_policy_compatibility(payload)


def test_expired_reference_and_closed_session_refuse(local, monkeypatch):
    service, _, payload = local
    session = service._policy_compatibility
    monkeypatch.setattr(pc.time, "monotonic", lambda: session._started + pc.MAX_PROPOSAL_AGE_S)
    with pytest.raises(ValueError, match="expired"):
        service.observe_policy_compatibility(payload)
    session.invalidate()
    with pytest.raises(ValueError):
        session.observe(payload)


def test_no_runtime_or_topology_is_initialized(tmp_path):
    service = AssuranceDashboardService(control_journal_path=tmp_path / "missing.sqlite")
    with pytest.raises(ValueError, match="existing simulation"):
        service.policy_compatibility_context()
    assert service._controller_runtime is None and not service.control_journal_path.exists()
    runtime = service.controller_runtime
    before = runtime.journal.path.read_bytes()
    with pytest.raises(sqlite3.OperationalError):
        service.policy_compatibility_context()
    assert runtime.journal.path.read_bytes() == before


def test_topology_write_during_snapshot_is_detected(local, monkeypatch):
    service, runtime, payload = local
    with sqlite3.connect(runtime.journal.path) as writer:
        writer.execute("PRAGMA journal_mode=WAL")
    original = pc._read_topology

    def concurrent(reader, topology_id):
        topology = original(reader, topology_id)
        with sqlite3.connect(runtime.journal.path) as writer:
            changed = json.loads(json.dumps(topology))
            changed["links"][0]["distance_km"] += 1
            writer.execute("update simulated_topologies set state_json=?", (json.dumps(changed),))
        return topology

    monkeypatch.setattr(pc, "_read_topology", concurrent)
    with pytest.raises(ValueError, match="during observation"):
        service.observe_policy_compatibility(payload)


def test_fault_is_blocked_and_input_age_is_checked_after_reads(local, monkeypatch):
    service, runtime, payload = local
    runtime._policy_activation_fault = {"fixture": "unresolved"}
    codes = {x["code"] for x in service.observe_policy_compatibility(payload)["record"]["blocking_reasons"]}
    assert "UNRESOLVED_ACTIVATION" in codes
    runtime._policy_activation_fault = None
    stamp = pc._now()
    monkeypatch.setattr(pc, "_now", lambda: stamp + timedelta(seconds=6))
    assert "STALE_INPUT" in {
        x["code"] for x in service.observe_policy_compatibility(payload)["record"]["blocking_reasons"]
    }


@pytest.mark.parametrize(
    "field,value",
    [
        ("metadata", None),
        ("metadata", []),
        ("artifact_hashes", []),
        ("source", ""),
        ("source", False),
        ("schema_version", "unknown"),
    ],
)
def test_incomplete_provenance_is_refused(local, field, value):
    service, _, payload = local
    payload["snapshot"]["provenance"][field] = value
    with pytest.raises(ValueError):
        service.observe_policy_compatibility(payload)
