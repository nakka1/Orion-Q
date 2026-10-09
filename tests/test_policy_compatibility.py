"""Q05 contract inspection cannot grant selection or infer component compatibility."""

import hashlib
import json
import subprocess
import sys

import pytest

from quantum_autopilot.model_intake import catalog
from quantum_autopilot.model_intake import policy_compatibility as compat


def digest(record):
    return hashlib.sha256(
        json.dumps(record, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()


@pytest.mark.parametrize("model_id", [o.model_id for o in catalog.model_options()])
def test_all_optional_components_are_blocked_with_specific_contracts(model_id):
    receipt = compat.inspect_policy_compatibility(model_id)
    r = receipt["record"]
    assert receipt["record_sha256"] == digest(r)
    assert r["status"] == "BLOCKED"
    assert {"input_domain", "action_mapping", "lifecycle"} <= set(r["blocking_criteria"])
    assert r["assessment_scope"] == "DECLARED_CONTRACT_ONLY"
    assert r["declared"]["input_domain"] and r["declared"]["action_semantics"]
    assert not any(
        r[k]
        for k in (
            "runtime_observed",
            "artifact_identity_observed",
            "domain_validated",
            "selection_available",
            "activation_available",
            "operator_approval_recorded",
            "models_loaded",
            "commands_emitted",
            "physical_action_performed",
        )
    )
    assert all(not o.default_enabled and not o.runtime_available for o in catalog.model_options())


def test_routing_shared_names_do_not_translate_resources_or_authorize():
    r = compat.inspect_policy_compatibility("qnet.routing.nominal")["record"]
    assert set(r["declared"]["declared_actions"]) - set(r["target"]["actions"]) == {
        "GENERATE",
        "DELIVER",
        "DISCARD",
    }
    assert "HALT" not in r["declared"]["declared_actions"]
    check = next(c for c in r["criteria"] if c["criterion"] == "action_mapping")
    assert check["status"] == "BLOCKED" and "shared action names" in check["reason"]


def test_detector_has_a_typed_contract_and_stays_blocked_for_the_repeater_target():
    detector = compat.inspect_policy_compatibility("qkd.detector.boosting")["record"]
    assert detector["declared"]["input_contract"] == "orion.optional-detector-input.v1"
    assert detector["declared"]["adapter_status"] == "offline_only"
    assert "safe_export" not in detector["blocking_criteria"] and detector["status"] == "BLOCKED"
    assert "not a repeater control intent" in detector["declared"]["action_semantics"]
    assert compat.inspect_policy_compatibility("qnet.allocator.persistence")["record"]["status"] == "BLOCKED"


def test_existing_workflow_requires_real_context_and_no_optional_promotion():
    r = compat.inspect_policy_compatibility(compat.TARGET_POLICY_ID)["record"]
    assert r["status"] == "CONTEXT_REQUIRED" and not r["selection_available"]
    assert next(c for c in r["criteria"] if c["criterion"] == "lifecycle")["status"] == "THRESHOLD_ONLY"
    assert not r["runtime_observed"] and not r["operator_approval_recorded"]


@pytest.mark.parametrize(
    "value", [None, False, {}, [], 0, "", "unknown", "qnet.routing.new", "qnet.routing.nominal\n", "x" * 161]
)
def test_unknown_or_malformed_candidate_is_refused(value):
    with pytest.raises(ValueError):
        compat.inspect_policy_compatibility(value)


@pytest.mark.parametrize("kwargs", [{"mode": "physical"}, {"mode": "shadow"}, {"target_policy_id": "ML"}])
def test_unsupported_target_is_not_silently_assessed(kwargs):
    with pytest.raises(ValueError):
        compat.inspect_policy_compatibility(compat.TARGET_POLICY_ID, **kwargs)


def test_caller_cannot_supply_self_attested_compatibility_or_hash():
    with pytest.raises(TypeError):
        compat.inspect_policy_compatibility("qnet.routing.nominal", verified=True, model_sha256="0" * 64)
    first = compat.policy_compatibility_catalog()
    first["record"]["candidates"][1]["record"]["selection_available"] = True
    next_receipt = compat.policy_compatibility_catalog()
    assert not next_receipt["record"]["candidates"][1]["record"]["selection_available"]
    assert next_receipt["record_sha256"] == digest(next_receipt["record"])


def test_new_catalog_entry_has_no_implicit_compatibility(monkeypatch):
    original = catalog.model_options()
    monkeypatch.setattr(
        compat,
        "model_options",
        lambda: original + (catalog.ModelOption("qnet.routing.new", "qnet", "offline_only", "new"),),
    )
    with pytest.raises(ValueError, match="no reviewed compatibility"):
        compat.policy_compatibility_catalog()


def test_declarations_agree_with_existing_typed_contracts():
    from quantum_autopilot.model_intake import (
        sync_session,
        routing_session,
        allocation_session,
        compensation_session,
        keyrate_session,
    )
    from quantum_autopilot.control_plane.contracts import ControlAction, CONTROL_PLANE_SCHEMA_VERSION
    from quantum_autopilot.control_plane.policy_preparation import POLICY_ID

    assert compat.TARGET_POLICY_ID == POLICY_ID
    assert compat.TARGET_ACTIONS == tuple(a.value for a in ControlAction)
    assert compat.inspect_policy_compatibility(POLICY_ID)["record"]["target"]["input_contract"].endswith(
        "/" + CONTROL_PLANE_SCHEMA_VERSION
    )
    for model, module in [
        ("qkd.sync.ridge.1s.h2s", sync_session),
        ("qkd.keyrate.actor", keyrate_session),
        ("qnet.routing.nominal", routing_session),
        ("qnet.allocator.ridge", allocation_session),
        ("qnet.compensation.calibrado.mlp", compensation_session),
    ]:
        assert (
            compat.inspect_policy_compatibility(model)["record"]["declared"]["input_contract"]
            == module.SCHEMA
        )
    assert compat.inspect_policy_compatibility("qnet.routing.nominal")["record"]["declared"][
        "declared_actions"
    ] == list(routing_session.KINDS)


def test_inspection_in_fresh_process_has_no_numeric_artifact_database_or_network_access(tmp_path):
    script = """
import sys
from quantum_autopilot.model_intake.policy_compatibility import policy_compatibility_catalog

def guard(event,args):
    if event in ('socket.connect','socket.getaddrinfo','sqlite3.connect'):
        raise AssertionError(event)
    if event=='open' and str(args[0]).lower().endswith(('.zip','.npz','.pkl','.pickle','.sqlite')):
        raise AssertionError('artifact or database opened')
sys.addaudithook(guard)
r=policy_compatibility_catalog()
assert len(r['record']['candidates'])==12
assert not any(m in sys.modules for m in ('numpy','torch','quantum_autopilot.model_intake.artifacts',
                                         'quantum_autopilot.control_plane.runtime'))
"""
    p = subprocess.run(
        [sys.executable, "-c", script], cwd=tmp_path, capture_output=True, text=True, timeout=30
    )
    assert p.returncode == 0, p.stdout + p.stderr


def test_service_queries_preserve_pending_proposal_and_database(tmp_path, monkeypatch):
    from quantum_autopilot.qkd.dashboard_api import AssuranceDashboardService
    from quantum_autopilot.control_plane.topology import default_simulated_topology
    from tests.test_policy_preparation import prepare, review

    service = AssuranceDashboardService(control_journal_path=tmp_path / "fixture.sqlite")
    assert len(service.policy_compatibility_catalog()["record"]["candidates"]) == 12
    assert service._controller_runtime is None and not service.control_journal_path.exists()
    runtime = service.controller_runtime
    service.topology_store.initialize(default_simulated_topology())
    proposal = prepare(service)
    policy = runtime.policy
    before = runtime.journal.path.read_bytes()

    def forbidden(*a, **kw):
        pytest.fail("inspection must not load, decide or execute")

    monkeypatch.setattr(catalog, "open_optional_model", forbidden)
    monkeypatch.setattr(runtime, "cycle", forbidden)
    monkeypatch.setattr(policy, "decide", forbidden)
    service.policy_compatibility_catalog()
    service.inspect_policy_compatibility("qnet.routing.nominal")
    assert runtime.policy is policy and runtime.journal.path.read_bytes() == before
    assert review(service, proposal)["record"]["status"] == "CURRENT"


def test_cli_export_is_exclusive_and_unknown_candidate_writes_nothing(tmp_path, capsys):
    from tools.inspect_policy_compatibility import main

    dest = tmp_path / "catalog.json"
    assert main(["--output", str(dest)]) == 0
    original = dest.read_bytes()
    r = json.loads(original)
    assert r["record_sha256"] == digest(r["record"])
    with pytest.raises(SystemExit) as e:
        main(["--output", str(dest)])
    assert e.value.code == 2 and dest.read_bytes() == original
    missing = tmp_path / "unknown.json"
    with pytest.raises(SystemExit):
        main(["--candidate", "unknown", "--output", str(missing)])
    assert not missing.exists()
    assert main(["--candidate", "qnet.routing.nominal"]) == 0
    assert json.loads(capsys.readouterr().out)["record"]["status"] == "BLOCKED"
    assert not list(tmp_path.glob("*.sqlite"))
