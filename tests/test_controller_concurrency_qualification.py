"""Reproduced multi-process races, conservative device fencing and audit recovery.

All device/quantum observations in these tests are explicitly synthetic.
"""
import multiprocessing
import time
from dataclasses import replace
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from quantum_autopilot.control_plane.mixed_controller import MixedController
from quantum_autopilot.control_plane.topology import SimulatedTopologyStore
from quantum_autopilot.network_engine import ModelRegistry, NetworkEngine
from quantum_autopilot.network_engine.routing import PairConflict
from quantum_autopilot.security.equipment import EquipmentAuthority, EquipmentDenied
from quantum_autopilot.service.client import CoreClient, CoreRequestError
from tests.test_mixed_controller import configuration, demand, ready_records
from tests.test_network_engine_engine import make, triangle
from tests.test_portable_core_service import provisioned, serving  # noqa: F401


def _consume_at_barrier(path, barrier, queue):
    engine = NetworkEngine(SimulatedTopologyStore(path), path, registry=ModelRegistry(environ={}), seed=7)
    original = engine.demands.set_pair
    def commit(*args, **kwargs):
        barrier.wait(20)
        return original(*args, **kwargs)
    engine.demands.set_pair = commit
    try:
        result = engine.command("consume_pair", {"demand_id": "P1"})
        queue.put(result["status"])
    except PairConflict:
        queue.put("conflict")


def test_two_processes_read_delivered_pair_but_exactly_one_consumes(tmp_path):
    engine, _, path = make(tmp_path, triangle(), demands=(("P1", "A", "B"),))
    first = engine.command("request_pair", {"demand_id": "P1"})["pair"]
    assert first["status"] == "delivered" and first["pair_id"]
    ctx = multiprocessing.get_context("spawn")
    barrier, queue = ctx.Barrier(2), ctx.Queue()
    children = [ctx.Process(target=_consume_at_barrier, args=(path, barrier, queue)) for _ in range(2)]
    try:
        for child in children:
            child.start()
        for child in children:
            child.join(30)
            assert child.exitcode == 0
        assert sorted(queue.get(timeout=2) for _ in children) == ["conflict", "consumed"]
        assert engine.demands.pair("P1")["status"] == "consumed"
    finally:
        for child in children:
            if child.is_alive():
                child.terminate()
                child.join(5)


@pytest.mark.parametrize("operation", ["purify", "reconcile", "replace"])
def test_stale_pair_transition_cannot_resurrect_consumed_pair(tmp_path, operation):
    engine, _, _ = make(tmp_path, triangle(), demands=(("P1", "A", "B"),))
    pair = engine.command("request_pair", {"demand_id": "P1"})["pair"]
    engine.command("consume_pair", {"demand_id": "P1"})
    status = {"purify": "delivered", "reconcile": "invalidated", "replace": "delivered"}[operation]
    with pytest.raises(PairConflict):
        engine.demands.set_pair("P1", dict(pair, status=status), expected_pair=pair)
    assert engine.demands.pair("P1")["status"] == "consumed"


def bound_configuration(session="bound"):
    config = configuration(session=session)
    config["nodes"][0]["resource_identity"] = {
        "namespace": "synthetic-orbital-memory", "serial": "same-memory-01", "endpoint": "fixture://device"}
    return config


def _hold_resource(path, authority, ready):
    core = MixedController(path, resource_authority=EquipmentAuthority(authority))
    config = bound_configuration()
    core.configure(config)
    core.ingest({"records": ready_records(config)})
    assert core.demand(demand())["status"] == "reserved"
    ready.put(core.status()["resource_authority"]["fences"]["N01"])
    time.sleep(30)
    # Deliberately no close: process termination must drop the OS handle.


def test_different_databases_same_resource_rejected_and_crash_requires_reverification(tmp_path):
    ctx = multiprocessing.get_context("spawn")
    ready = ctx.Queue()
    authority, path = tmp_path / "shared-authority", tmp_path / "owner.sqlite"
    child = ctx.Process(target=_hold_resource, args=(path, authority, ready))
    child.start()
    try:
        first_fence = ready.get(timeout=20)
        competitor = MixedController(tmp_path / "other.sqlite", resource_authority=EquipmentAuthority(authority))
        try:
            with pytest.raises(EquipmentDenied, match="another session"):
                competitor.configure(bound_configuration())
            assert competitor.status()["configured"] is False
        finally:
            competitor.close()
        child.terminate()
        child.join(10)
        assert not child.is_alive()
        recovered = MixedController(path, resource_authority=EquipmentAuthority(authority))
        try:
            status = recovered.status()
            assert status["resource_authority"]["fences"]["N01"] > first_fence
            assert status["reservations"][0]["status"] == "invalidated_restart"
            assert all(node["status"] == "reverification_required" for node in status["nodes"].values())
            assert recovered.demand(demand("after-crash"))["status"] == "blocked"
            recovered.configure(bound_configuration("new-session"))
            assert recovered.demand(demand("before-new-input"))["status"] == "blocked"
            recovered.ingest({"records": ready_records(bound_configuration("new-session"))})
            assert recovered.demand(demand("verified-again"))["status"] == "reserved"
        finally:
            recovered.close()
    finally:
        if child.is_alive():
            child.terminate()
            child.join(5)


def test_bound_resource_requires_shared_authority_and_physical_input_cannot_bypass(tmp_path):
    core = MixedController(tmp_path / "unbound.sqlite")
    try:
        with pytest.raises(ValueError, match="shared resource authority"):
            core.configure(bound_configuration())
        config = configuration()
        core.configure(config)
        records = ready_records(config)
        records[0]["provenance"]["origin"] = "physical"
        with pytest.raises(ValueError, match="physical observations require"):
            core.ingest({"records": records})
        assert core.status()["audit_origin_counts"] == {}
    finally:
        core.close()


def test_resource_configuration_conflict_preserves_old_configuration_and_reservation(tmp_path):
    authority = EquipmentAuthority(tmp_path / "shared")
    first = MixedController(tmp_path / "first.sqlite", resource_authority=authority)
    second = MixedController(tmp_path / "second.sqlite", resource_authority=authority)
    try:
        first.configure(bound_configuration())
        config = configuration()
        second.configure(config)
        second.ingest({"records": ready_records(config)})
        receipt = second.demand(demand())
        with pytest.raises(EquipmentDenied):
            second.configure(bound_configuration("attempt-new"))
        assert second.status()["configuration"] == config
        assert second.demand(demand()) == receipt
    finally:
        second.close()
        first.close()


def test_resource_expiry_never_lets_live_owner_be_stolen_and_allows_safe_close(tmp_path):
    authority = EquipmentAuthority(tmp_path / "authority")
    core = MixedController(tmp_path / "first.sqlite", resource_authority=authority)
    second = MixedController(tmp_path / "other.sqlite", resource_authority=authority)
    try:
        core.configure(bound_configuration())
        core.ingest({"records": ready_records(bound_configuration())})
        core.demand(demand())
        authority.now = lambda: 10**12
        with pytest.raises(EquipmentDenied, match="expired"):
            core.demand(demand())
        with pytest.raises(EquipmentDenied, match="another session"):
            second.configure(bound_configuration())
        state = core.status()
        assert state["nodes"]["N01"]["status"] == "resource_authority_unavailable"
        assert state["reservations"][0]["status"] == "invalidated_resource_authority"
    finally:
        core.close()
        second.close()


def test_api_postcommit_audit_failure_reports_durable_receipt_and_same_id_recovers(provisioned, monkeypatch):  # noqa: F811
    config, runtime, _, _, _, _, _, operator = provisioned
    with serving(config, runtime) as server:
        writer = CoreClient(f"https://localhost:{server.server_port}", operator)
        mixed = configuration()
        writer.mixed_configure(mixed)
        writer.mixed_ingest({"records": ready_records(mixed)})
        original = runtime.audit.record
        def failed(identity, action, resource, outcome, details=None):
            if outcome == "completed" and action == "network.operate":
                raise OSError("synthetic audit storage unavailable")
            return original(identity, action, resource, outcome, details)
        monkeypatch.setattr(runtime.audit, "record", failed)
        with pytest.raises(CoreRequestError) as pending:
            writer.mixed_demand(demand("durable-once"))
        assert pending.value.status == 503 and pending.value.uncertain and pending.value.committed
        assert pending.value.request_id == "durable-once"
        state = writer.mixed_status()
        receipt = state["reservations"][0]
        assert receipt["status"] == "reserved"
        assert pending.value.receipt_sha256 == receipt["receipt_sha256"]
        assert len(state["audit_pending"]) == 1
        monkeypatch.setattr(runtime.audit, "record", original)
        assert writer.mixed_demand(demand("durable-once")) == receipt
        assert writer.mixed_status()["audit_pending"] == []
        assert len(writer.mixed_status()["reservations"]) == 1
        assert runtime.audit.verify()["valid"]
        server.service._release_controller_io_resources()


def test_api_reconciles_original_pending_hash_after_service_controller_restart(provisioned, monkeypatch):  # noqa: F811
    config, runtime, _, _, _, _, _, operator = provisioned
    with serving(config, runtime) as server:
        writer = CoreClient(f"https://localhost:{server.server_port}", operator)
        mixed = configuration()
        writer.mixed_configure(mixed)
        writer.mixed_ingest({"records": ready_records(mixed)})
        original = runtime.audit.record
        def failed(identity, action, resource, outcome, details=None):
            if outcome == "completed" and action == "network.operate":
                raise OSError("synthetic post-commit audit loss")
            return original(identity, action, resource, outcome, details)
        monkeypatch.setattr(runtime.audit, "record", failed)
        with pytest.raises(CoreRequestError) as pending:
            writer.mixed_demand(demand("before-restart"))
        old_hash = pending.value.receipt_sha256
        server.service._release_controller_io_resources()
        monkeypatch.setattr(runtime.audit, "record", original)
        result = writer.mixed_demand(demand("before-restart"))
        assert result["status"] == "invalidated_shutdown" and result["receipt_sha256"] != old_hash
        assert writer.mixed_status()["audit_pending"] == []
        assert writer.mixed_demand(demand("after-restart"))["status"] == "blocked"
        assert runtime.audit.verify()["valid"]
        server.service._release_controller_io_resources()


def test_transaction_rolls_back_when_admission_revoked_and_release_is_idempotent(tmp_path):
    core = MixedController(tmp_path / "controller.sqlite")
    try:
        config = configuration()
        core.configure(config)
        core.ingest({"records": ready_records(config)})
        def revoked():
            raise PermissionError("synthetic revocation")
        with pytest.raises(PermissionError):
            core.demand(demand(), admit=revoked)
        assert core.status()["reservations"] == []
        core.demand(demand())
        released = core.release({"request_id": "demand-1"})
        assert core.release({"request_id": "demand-1"}) == released
        assert core.demand(demand()) == released
        assert core.demand(demand("new-after-release"))["status"] == "reserved"
    finally:
        core.close()


def test_38_simultaneous_demands_capacity_one_have_one_winner_and_no_deadlock(tmp_path):
    core = MixedController(tmp_path / "concurrent.sqlite")
    try:
        config = configuration()
        core.configure(config)
        core.ingest({"records": ready_records(config)})
        barrier = Barrier(38)
        def reserve(index):
            barrier.wait(10)
            return core.demand(demand(f"race-{index}"))
        with ThreadPoolExecutor(max_workers=38) as pool:
            results = list(pool.map(reserve, range(38)))
        assert sum(result["status"] == "reserved" for result in results) == 1
        assert sum(result["status"] == "blocked" for result in results) == 37
        assert len(core.status()["reservations"]) == 38
    finally:
        core.close()


def test_configuration_write_failure_releases_only_new_claims(tmp_path, monkeypatch):
    authority = EquipmentAuthority(tmp_path / "authority")
    core = MixedController(tmp_path / "first.sqlite", resource_authority=authority)
    original = core._invalidate
    def failed(*_args, **_kwargs):
        raise OSError("synthetic database write failure after lock acquisition")
    monkeypatch.setattr(core, "_invalidate", failed)
    with pytest.raises(OSError):
        core.configure(bound_configuration())
    monkeypatch.setattr(core, "_invalidate", original)
    assert core.status()["configured"] is False
    other = MixedController(tmp_path / "other.sqlite", resource_authority=authority)
    try:
        assert other.configure(bound_configuration())["status"] == "configured"
    finally:
        other.close()
        core.close()


def test_audit_pending_reservation_survives_restart_but_cannot_reactivate(tmp_path):
    core = MixedController(tmp_path / "controller.sqlite")
    config = configuration()
    core.configure(config)
    core.ingest({"records": ready_records(config)})
    receipt = core.demand(demand())
    assert core.status()["audit_pending"][0]["receipt_sha256"] == receipt["receipt_sha256"]
    core.close()
    recovered = MixedController(tmp_path / "controller.sqlite")
    try:
        assert recovered.status()["audit_pending"][0]["receipt_sha256"] == receipt["receipt_sha256"]
        result = recovered.demand(demand())
        assert result["status"] == "invalidated_shutdown"
        assert recovered.demand(demand("fresh-after-restart"))["status"] == "blocked"
    finally:
        recovered.close()


def test_redefined_demand_cannot_accept_pair_from_old_endpoints(tmp_path):
    engine, _, _ = make(tmp_path, triangle(), demands=(("P1", "A", "B"),))
    old = engine.demands.list()[0]
    old_pair = engine.demands.pair("P1")
    engine.command("define_pair", {"demand_id": "P1", "a": "A", "b": "C"})
    with pytest.raises(PairConflict):
        engine.demands.set_pair("P1", {"status": "delivered"}, expected_pair=old_pair, expected_demand=old)
    assert engine.demands.pair("P1") is None


def test_api_shared_authority_conflict_is_explicit_and_original_reservation_preserved(provisioned, tmp_path):  # noqa: F811
    config, runtime, _, _, _, _, _, operator = provisioned
    first_config = replace(config, resource_authority_root=tmp_path / "shared-authority")
    second_config = replace(first_config, state_root=tmp_path / "other-state")
    with serving(first_config, runtime) as first, serving(second_config, runtime) as second:
        owner = CoreClient(f"https://localhost:{first.server_port}", operator)
        competitor = CoreClient(f"https://localhost:{second.server_port}", operator)
        mixed = bound_configuration()
        owner.mixed_configure(mixed)
        owner.mixed_ingest({"records": ready_records(mixed)})
        receipt = owner.mixed_demand(demand())
        with pytest.raises(CoreRequestError) as conflict:
            competitor.mixed_configure(mixed)
        assert conflict.value.status == 409 and not conflict.value.uncertain
        assert competitor.mixed_status()["configured"] is False
        assert owner.mixed_demand(demand()) == receipt
        first.service._release_controller_io_resources()
        second.service._release_controller_io_resources()
