"""Durable key ownership, actual process termination and no secret evidence."""
from concurrent.futures import ThreadPoolExecutor
import json
import multiprocessing
import sqlite3
import threading
from uuid import uuid4

import pytest

pytest.importorskip("cryptography")

from quantum_autopilot.lab.kms import KeyDeliveryError
from quantum_autopilot.lab.persistent_keys import KeyLedger


PROVENANCE = {"experiment_id": "test-fixture", "model_id": "identified-test-bytes.v1"}
WRAPPING_KEY = bytes(range(32))


def ledger(path, identity="kme-alice"):
    return KeyLedger(path, WRAPPING_KEY, identity=identity, **PROVENANCE)


def provision(store, count=4):
    pairs = [(str(uuid4()), bytes([index % 256]) * 32) for index in range(count)]
    store.provision(pairs)
    return pairs


@pytest.mark.parametrize("key_request", [{"number": True}, {"number": 0}, {"number": 65},
                                     {"size": 128}, {"number": float("nan")}, {"extra": 1}])
def test_entire_invalid_request_leaves_all_keys_available(tmp_path, key_request):
    store = ledger(tmp_path / "keys.sqlite")
    provision(store)
    with pytest.raises(KeyDeliveryError):
        store.reserve(key_request, "request-token")
    assert store.summary()["counts"] == {"available": 4}
    assert len(store.summary()["events"]) == 1


def test_reservation_is_irrevocable_and_restart_invalidates(tmp_path):
    path = tmp_path / "keys.sqlite"
    store = ledger(path)
    provision(store)
    store.reserve({}, "lost-request")
    restarted = ledger(path)
    assert restarted.recover() == 1
    assert restarted.summary()["counts"] == {"available": 3, "invalidated": 1}
    assert restarted.delivery_status("lost-request")["material_recoverable"] is False
    with pytest.raises(KeyDeliveryError) as duplicate:
        restarted.reserve({}, "lost-request")
    assert duplicate.value.status == 409
    assert restarted.summary()["counts"] == {"available": 3, "invalidated": 1}


def test_atomic_peer_validation_and_late_authorization_cannot_revive_invalidated(tmp_path):
    alice, bob = ledger(tmp_path / "alice.sqlite"), ledger(tmp_path / "bob.sqlite", "kme-bob")
    pairs = provision(alice)
    bob.provision(pairs)
    ids = alice.reserve({"number": 2}, "paired-request")
    with pytest.raises(KeyDeliveryError):
        bob.reserve_peer(ids + [str(uuid4())], "paired-request")
    assert bob.summary()["counts"] == {"available": 4}
    bob.reserve_peer(ids, "paired-request")
    bob.invalidate("paired-request")
    with pytest.raises(KeyDeliveryError):
        bob.authorize_peer(ids, "paired-request")
    assert bob.summary()["counts"] == {"available": 2, "invalidated": 2}


def test_committed_issue_consumes_before_response_and_status_cannot_recover_bytes(tmp_path):
    store = ledger(tmp_path / "keys.sqlite")
    provision(store)
    ids = store.reserve({}, "issued-request")
    container = store.commit_issue("issued-request")
    assert container["keys"][0]["key_ID"] == ids[0]
    assert store.summary()["counts"] == {"available": 3, "issued": 1}
    with sqlite3.connect(store.path) as db:
        assert db.execute("SELECT sealed FROM keys WHERE key_id=?", (ids[0],)).fetchone() == (None,)
    with pytest.raises(KeyDeliveryError):
        store.commit_issue("issued-request")
    status = store.delivery_status("issued-request")
    assert not status["retry_material_allowed"] and "key" not in status and "key_ID" not in status
    assert ledger(store.path).recover() == 1


def test_consumption_requires_remote_authorization_and_complete_batch(tmp_path):
    a, b = ledger(tmp_path / "a.sqlite"), ledger(tmp_path / "b.sqlite", "kme-bob")
    pairs = provision(a)
    b.provision(pairs)
    ids = a.reserve({"number": 2}, "paired")
    request = {"key_IDs": [{"key_ID": key_id} for key_id in ids]}
    with pytest.raises(KeyDeliveryError):
        b.consume(request, "bob-request")
    b.reserve_peer(ids, "paired")
    with pytest.raises(KeyDeliveryError):
        b.consume(request, "bob-request")
    issued = a.commit_issue("paired")
    b.authorize_peer(ids, "paired")
    with pytest.raises(KeyDeliveryError):
        b.consume({"key_IDs": request["key_IDs"] + [{"key_ID": str(uuid4())}]}, "bob-invalid")
    assert b.summary()["counts"].get("consumed", 0) == 0
    assert b.consume(request, "bob-request") == issued
    with pytest.raises(KeyDeliveryError):
        b.consume(request, "bob-repeated")
    assert b.summary()["counts"]["consumed"] == 2


def test_fixture_reprovisioning_and_endpoint_provenance_mismatch_rejected(tmp_path):
    store = ledger(tmp_path / "keys.sqlite")
    pairs = provision(store)
    store.reserve({}, "unused-but-burned")
    with pytest.raises(ValueError):
        store.provision(pairs)
    with pytest.raises(ValueError):
        ledger(store.path, "kme-bob")
    assert store.summary()["capacity"] == 4


def test_wrong_wrapping_credential_rejected_before_kme_readiness(tmp_path):
    store = ledger(tmp_path / "keys.sqlite")
    provision(store)
    with pytest.raises(ValueError, match="wrapping credential rejected"):
        KeyLedger(store.path, bytes([19]) * 32, identity="kme-alice", **PROVENANCE)
    assert store.summary()["counts"] == {"available": 4}


def test_concurrent_connections_never_assign_duplicate_or_partial_batches(tmp_path):
    path = tmp_path / "keys.sqlite"
    provision(ledger(path), 24)
    def reserve(index):
        try:
            return ledger(path).reserve({"number": 3}, "parallel-" + str(index))
        except KeyDeliveryError as error:
            assert error.status == 503
            return []
    with ThreadPoolExecutor(max_workers=8) as workers:
        assigned = list(workers.map(reserve, range(10)))
    ids = [key_id for batch in assigned for key_id in batch]
    assert len(ids) == len(set(ids)) == 24
    assert sorted(map(len, assigned)) == [0, 0] + [3] * 8
    assert ledger(path).summary()["counts"] == {"reserved": 24}


def _reserve_process(path, number, endpoint):
    result = []
    for index in range(10):
        try:
            result.extend(ledger(path).reserve({}, f"process-{number}-{index}"))
        except KeyDeliveryError as error:
            if error.status != 503:
                raise
    endpoint.send(result)
    endpoint.close()


def test_actual_multi_process_sqlite_assignment(tmp_path):
    path = tmp_path / "keys.sqlite"
    provision(ledger(path), 24)
    context = multiprocessing.get_context("spawn")
    processes, readers = [], []
    try:
        for index in range(4):
            parent, child = context.Pipe()
            process = context.Process(target=_reserve_process, args=(path, index, child))
            process.start()
            child.close()
            processes.append(process)
            readers.append(parent)
        assigned = []
        for reader in readers:
            assert reader.poll(30)
            assigned.extend(reader.recv())
        assert len(assigned) == len(set(assigned)) == 24
        assert ledger(path).summary()["counts"] == {"reserved": 24}
    finally:
        for process in processes:
            process.join(timeout=10)
            if process.is_alive():
                process.terminate()
                process.join(timeout=5)
        for reader in readers:
            reader.close()


def _crash_writer(path, endpoint, phase):
    store = ledger(path)
    if phase == "uncommitted":
        with store.transaction() as db:
            db.execute("UPDATE keys SET state='reserved'")
            endpoint.send({"reached": phase})
            threading.Event().wait(30)
    else:
        store.reserve({}, "crashed-issued-request")
        store.commit_issue("crashed-issued-request")
        endpoint.send({"reached": phase})
        threading.Event().wait(30)


@pytest.mark.parametrize("phase", ["uncommitted", "committed-before-response"])
def test_process_killed_at_sqlite_boundaries_has_explicit_safe_recovery(tmp_path, phase):
    path = tmp_path / "keys.sqlite"
    provision(ledger(path), 1)
    parent, child = multiprocessing.get_context("spawn").Pipe()
    process = multiprocessing.get_context("spawn").Process(target=_crash_writer, args=(path, child, phase))
    try:
        process.start()
        child.close()
        assert parent.poll(20)
        assert parent.recv()["reached"] == phase
        process.terminate()
        process.join(timeout=10)
        assert not process.is_alive()
        recovered = ledger(path)
        recovered.recover()
        if phase == "uncommitted":
            assert recovered.summary()["counts"] == {"available": 1}
        else:
            assert recovered.summary()["counts"] == {"invalidated": 1}
            assert recovered.summary()["pending_peer_invalidations"] == 1
    finally:
        if process.is_alive():
            process.terminate()
            process.join(timeout=5)
        parent.close()


def test_ledger_evidence_contains_no_material_ids_or_wrapping_key(tmp_path):
    store = ledger(tmp_path / "keys.sqlite")
    pairs = provision(store)
    store.reserve({}, "not-secret-delivery-token")
    serialized = json.dumps(store.summary())
    assert '"key"' not in serialized and '"key_ID"' not in serialized
    assert all(key_id not in serialized for key_id, _ in pairs)
    assert WRAPPING_KEY.hex() not in serialized
