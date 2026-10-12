"""Integration of pager release with journal writes, scenarios and service reuse."""
from __future__ import annotations

import gc
import sqlite3
from threading import RLock
from types import SimpleNamespace
import weakref

import pytest

from quantum_autopilot.control_plane.journal import ControlJournal
from quantum_autopilot.control_plane.runtime import ControllerRuntime
from quantum_autopilot.control_plane import sqlite_transaction
from quantum_autopilot.qkd.dashboard_api import AssuranceDashboardService


@pytest.fixture
def qualified_temporary_disk(monkeypatch, tmp_path):
    if not sqlite_transaction._wal_version_eligible(sqlite3.sqlite_version):
        pytest.skip("WAL regression requires an officially fixed SQLite release")
    # Qualify only this test's temporary disk; production keeps its real probe.
    def qualify(path):
        assert path.resolve().is_relative_to(tmp_path.resolve()), "fixture cannot qualify operator storage"
        return {"local_fixed_storage": True, "storage_reason": "test_temporary_disk"}
    monkeypatch.setattr(sqlite_transaction, "_wal_storage_status", qualify)


def _snapshot(request):
    return {
        "request_id": request, "node_id": "lifecycle-repeater",
        "timestamp_utc": "2026-10-09T12:00:00+00:00", "state_version": 1,
        "predicted_fidelity": 0.9, "prediction_uncertainty": 0.01, "telemetry_age_s": 0.0,
        "memory_state": "STORING", "available_pair_fidelities": [0.9, 0.9],
        "memory_ages_s": [0.0, 0.0], "telemetry": {}, "integrity_verified": True,
        "operator_interlock": True, "out_of_distribution": False,
        "provenance": {"evidence_kind": "simulated", "source": "journal-lifecycle-test",
                       "metadata": {"evidence_lane": "bell_fidelity_control",
                                    "target_semantics": "simulated_future_bell_fidelity"}},
    }


def test_release_is_idempotent_reads_stay_released_and_write_rearms(tmp_path, qualified_temporary_disk):
    runtime = ControllerRuntime(tmp_path / "journal.sqlite")
    policy = runtime.policy
    original = runtime.cycle(_snapshot("first"))
    journal = runtime.journal
    assert journal.storage_profile()["keeper_active"] is True
    journal.release_io_resources()
    journal.release_io_resources()
    assert journal.storage_profile()["keeper_active"] is False
    assert journal.get_by_request_id("first")["record_sha256"] == original["record_sha256"]
    assert runtime.cycle(_snapshot("first"))["idempotent_replay"] is True
    assert journal.storage_profile()["keeper_active"] is False
    runtime.cycle(_snapshot("second"))
    assert journal.storage_profile()["keeper_active"] is True
    assert runtime.policy is policy
    assert len(journal.list_recent()) == 2
    journal.release_io_resources()


def test_finalizer_closes_pager_without_retaining_journal(tmp_path, qualified_temporary_disk):
    journal = ControlJournal(tmp_path / "finalize.sqlite")
    keeper = journal._wal_keeper
    reference = weakref.ref(journal)
    assert keeper is not None and keeper.in_transaction is False
    del journal
    gc.collect()
    assert reference() is None
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        keeper.execute("SELECT 1")


def test_journal_constructed_inside_scenario_does_not_commit_early(tmp_path, qualified_temporary_disk):
    path = tmp_path / "borrowed.sqlite"
    runtime = ControllerRuntime(path)
    with pytest.raises(RuntimeError, match="rollback witness"):
        with runtime.scenario_transaction(path):
            borrowed = ControlJournal(path)
            assert borrowed.storage_profile()["reason"] == "active_scenario_transaction"
            assert borrowed.storage_profile()["keeper_active"] is False
            borrowed.append_safety_event("test", "fixture", "2026-10-09T12:00:00+00:00", {})
            raise RuntimeError("rollback witness")
    assert runtime.journal.list_recent_safety_events() == []
    # A later standalone write can initialize its own pager after the rollback.
    borrowed.append_safety_event("after", "fixture", "2026-10-09T12:00:00+00:00", {})
    assert borrowed.storage_profile()["keeper_active"] is True
    assert len(runtime.journal.list_recent_safety_events()) == 1
    borrowed.release_io_resources()
    runtime.journal.release_io_resources()


def test_service_cleanup_releases_both_existing_runtimes_without_reset(tmp_path, qualified_temporary_disk):
    path = tmp_path / "service.sqlite"
    first, second = ControllerRuntime(path), ControllerRuntime(path)
    first.cycle(_snapshot("first"))
    second.cycle(_snapshot("second"))
    policies = first.policy, second.policy
    # Exercise resource cleanup directly. No authenticated operation is skipped:
    # this method releases handles only and must also work after revocation.
    service = object.__new__(AssuranceDashboardService)
    service._controller_lock = RLock()
    service._mixed_controller_lock = RLock()
    service._mixed_controller = None
    service._controller_runtime = first
    service._wdm_model_controller = SimpleNamespace(runtime=second)
    service._release_controller_io_resources()
    service._release_controller_io_resources()
    assert not first.journal.storage_profile()["keeper_active"]
    assert not second.journal.storage_profile()["keeper_active"]
    assert (first.policy, second.policy) == policies
    assert len(first.journal.list_recent()) == 2
    second.cycle(_snapshot("resumed"))
    assert second.journal.storage_profile()["keeper_active"] is True
    assert len(first.journal.list_recent()) == 3
    service._release_controller_io_resources()


def test_service_cleanup_without_runtimes_does_not_initialize_them():
    service = object.__new__(AssuranceDashboardService)
    service._controller_lock = RLock()
    service._mixed_controller_lock = RLock()
    service._mixed_controller = None
    service._controller_runtime = None
    service._wdm_model_controller = None
    service._release_controller_io_resources()
    assert service._controller_runtime is None and service._wdm_model_controller is None
