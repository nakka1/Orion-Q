"""Real TLS/processes plus authenticated recovery, without physical QKD claims."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import http.client
import json
import os
from pathlib import Path
import secrets
import shutil
import sqlite3
import ssl
import threading
import time
from uuid import uuid4

import pytest

pytest.importorskip("cryptography")

from cryptography import x509
from cryptography.hazmat.primitives import serialization
from quantum_autopilot.lab.kms import KeyDeliveryError, create_test_pki, client_context, request_kme, serve_kme, DemoKeyPool
from quantum_autopilot.lab.persistent_keys import KeyLedger
from quantum_autopilot.lab.independent_kme import independent_pair
from quantum_autopilot.security.key_lifecycle import ConsumptionJournal, RecoveryIntegrityError
from quantum_autopilot.security.pki import OperationalPKI, CertificateRejected, enforce_tls13
from quantum_autopilot.security.runtime import SecurityRuntime


@pytest.fixture
def runtime(tmp_path):
    return SecurityRuntime(tmp_path / "security")


@pytest.fixture
def authority(tmp_path):
    if os.name != "nt":
        pytest.skip("operational vault profile requires Windows DPAPI or externally qualified service vault")
    runtime = SecurityRuntime(tmp_path / "protected-security", profile="operational")
    runtime.grant_identity("kme-test", ["operator", "consumer"], ["keys:*"])
    runtime.grant_identity("sae-test", ["consumer"], ["keys:kme-test"])
    return OperationalPKI(tmp_path / "certificates", runtime)


def ledger(tmp_path, runtime=None):
    return KeyLedger(tmp_path / "store" / "keys.sqlite", bytes(range(32)), identity="kme-alice",
        experiment_id="identified-fixture", model_id="security-test-only.v1", security_runtime=runtime,
        journal_path=tmp_path / "protected-recovery" / "consumption.jsonl")


def material(store, count=2, **parameters):
    pairs = [(str(uuid4()), secrets.token_bytes(32)) for _ in range(count)]
    store.provision(pairs, **parameters)
    return pairs


def snapshot(store, target):
    with store.connection() as source:
        with sqlite3.connect(target) as backup:
            source.backup(backup)


@pytest.mark.parametrize("saved_phase", ["available", "reserved"])
@pytest.mark.parametrize("final_phase", ["issued", "uncertain", "consumed", "invalidated"])
def test_backup_restore_cannot_reactivate_any_potentially_delivered_material(tmp_path, runtime, saved_phase, final_phase):
    store = ledger(tmp_path, runtime)
    pairs = material(store)
    if saved_phase == "reserved":
        store.reserve({}, "delivery")
    backup = tmp_path / "backup.sqlite"
    snapshot(store, backup)
    if saved_phase == "available":
        store.reserve({}, "delivery")
    if final_phase == "consumed":
        store.authorize_peer([pairs[0][0]], "delivery")
        store.consume({"key_IDs": [{"key_ID": pairs[0][0]}]}, "consumer-delivery")
    elif final_phase == "invalidated":
        store.invalidate("delivery")
    else:
        store.commit_issue("delivery")
        if final_phase == "uncertain":
            store.response_sent("delivery")
    # Backup covers only the key DB, never the independent protected checkpoint.
    shutil.copyfile(backup, store.path)
    restored = ledger(tmp_path, runtime)
    assert restored.summary()["counts"] == {"available": 1, "invalidated": 1}
    assert restored.summary()["pending_peer_invalidations"] == 1
    with pytest.raises(KeyDeliveryError):
        restored.commit_issue("delivery")
    with pytest.raises(ValueError):
        restored.provision([pairs[0]])


def test_journal_commit_before_sqlite_commit_burns_on_real_transaction_abort(tmp_path, runtime):
    store = ledger(tmp_path, runtime)
    pairs = material(store)
    with pytest.raises(RuntimeError):
        with store.transaction() as db:
            store.journal.append([pairs[0][0]], "reserved", "aborted-write")
            db.execute("UPDATE keys SET state='reserved' WHERE key_id=?", (pairs[0][0],))
            raise RuntimeError("fixture crash boundary")
    assert ledger(tmp_path, runtime).summary()["counts"] == {"available": 1, "invalidated": 1}


@pytest.mark.parametrize("alteration", ["truncate", "modify", "remove_checkpoint"])
def test_journal_tampering_or_checkpoint_loss_blocks_delivery(tmp_path, runtime, alteration):
    store = ledger(tmp_path, runtime)
    material(store)
    store.reserve({}, "delivery")
    if alteration == "truncate":
        store.journal.path.write_bytes(b"")
    elif alteration == "modify":
        store.journal.path.write_bytes(store.journal.path.read_bytes().replace(b"reserved", b"consumed"))
    else:
        runtime.vault.put_bytes(store.journal.name, b"{}")
    with pytest.raises(RecoveryIntegrityError):
        ledger(tmp_path, runtime)
    with pytest.raises(RecoveryIntegrityError):
        store.reserve({}, "another-delivery")


def test_expiration_is_terminal_and_survives_restore(tmp_path, runtime):
    store = ledger(tmp_path, runtime)
    deadline = time.time() + 60
    material(store, 1, expires_at=deadline)
    backup = tmp_path / "backup.sqlite"
    snapshot(store, backup)
    assert store.expire(now=deadline + 1) == 1
    assert store.summary()["counts"] == {"expired": 1}
    shutil.copyfile(backup, store.path)
    assert ledger(tmp_path, runtime).summary()["counts"] == {"invalidated": 1}


def test_requests_cannot_promote_emulated_material_to_operational_usage(tmp_path):
    with pytest.raises(ValueError, match="forbidden"):
        KeyLedger(tmp_path / "keys.sqlite", secrets.token_bytes(32), identity="kme-alice",
                  experiment_id="fixture", model_id="emulation", usage="operational")
    store = ledger(tmp_path)
    material(store, 1)
    with pytest.raises(KeyDeliveryError):
        store.reserve({"usage": "operational"}, "request")
    assert store.summary()["counts"] == {"available": 1}


def test_concurrent_restore_reconciliation_preserves_unique_assignment(tmp_path, runtime):
    store = ledger(tmp_path, runtime)
    material(store, 8)
    def reserve(index):
        return ledger(tmp_path, runtime).reserve({}, "concurrent-" + str(index))
    with ThreadPoolExecutor(max_workers=4) as pool:
        identifiers = [key_id for batch in pool.map(reserve, range(8)) for key_id in batch]
    assert len(set(identifiers)) == len(identifiers) == 8
    assert store.summary()["counts"] == {"reserved": 8}


@contextmanager
def tls_service(authority, leaf):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        def log_message(self, *_args):
            pass
        def do_GET(self):
            try:
                authority.policy.authorize(self.connection.getpeercert(binary_form=True), "key-delivery",
                                           expected_kind="sae")
                status = 200
            except CertificateRejected:
                status = 403
            self.send_response(status)
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"{}")
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    server.socket = authority.server_context(leaf).wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": .025}, daemon=True)
    thread.start()
    try:
        yield server.server_address[1]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def issue(authority):
    server = authority.provision("kme-test", kind="kme", services=["key-delivery"], server=True)
    client = authority.provision("sae-test", kind="sae", services=["key-delivery"])
    return server, client


def test_operational_tls13_revokes_an_already_established_session(authority):
    server, client = issue(authority)
    with tls_service(authority, server) as port:
        connection = http.client.HTTPSConnection("127.0.0.1", port, context=authority.client_context(client), timeout=15)
        try:
            connection.request("GET", "/status")
            response = connection.getresponse()
            assert response.status == 200
            response.read()
            socket_before = connection.sock
            assert socket_before.version() == "TLSv1.3"
            authority.revoke(client["serial"])
            connection.request("GET", "/status")
            response = connection.getresponse()
            assert response.status == 403
            response.read()
            assert connection.sock is socket_before
        finally:
            connection.close()


def test_renewal_changes_serial_and_old_certificate_never_returns_after_registry_restore(authority, tmp_path):
    _, client = issue(authority)
    backup = tmp_path / "registry.sqlite"
    shutil.copyfile(authority.policy.path, backup)
    replacement = authority.renew(client["serial"], identity="sae-test", kind="sae", services=["key-delivery"])
    old = x509.load_pem_x509_certificate(client["certificate"].read_bytes()).public_bytes(serialization.Encoding.DER)
    new = x509.load_pem_x509_certificate(replacement["certificate"].read_bytes()).public_bytes(serialization.Encoding.DER)
    assert replacement["serial"] != client["serial"]
    assert authority.policy.authorize(new, "key-delivery")["identity"] == "sae-test"
    shutil.copyfile(backup, authority.policy.path)
    with pytest.raises(CertificateRejected, match="revoked"):
        authority.policy.authorize(old, "key-delivery")


def test_certificate_identity_service_and_registry_tamper_are_rejected(authority):
    _, client = issue(authority)
    der = x509.load_pem_x509_certificate(client["certificate"].read_bytes()).public_bytes(serialization.Encoding.DER)
    with pytest.raises(CertificateRejected):
        authority.policy.authorize(der, "equipment-control")
    with pytest.raises(CertificateRejected):
        authority.policy.authorize(der, "key-delivery", expected_identity="another-sae")
    with sqlite3.connect(authority.policy.path) as db:
        db.execute("UPDATE certificates SET binding=replace(binding,'key-delivery','equipment-control') WHERE serial=?",
                   (client["serial"],))
    with pytest.raises(CertificateRejected):
        authority.policy.authorize(der, "equipment-control")


def test_no_plain_private_key_or_delivery_material_in_audit(authority, tmp_path):
    _, client = issue(authority)
    private = client["private_key"].read_bytes()
    assert b"ENCRYPTED PRIVATE KEY" in private
    with pytest.raises(TypeError):
        serialization.load_pem_private_key(private, password=None)
    audit = (authority.runtime.root / "audit.jsonl").read_text()
    assert "PRIVATE KEY" not in audit and client["password_ref"] not in audit
    assert authority.runtime.audit.verify()["valid"]
    store = ledger(tmp_path, authority.runtime)
    pairs = material(store)
    store.reserve({}, "safe-token")
    evidence = json.dumps(store.summary()) + store.journal.path.read_text() + audit
    assert all(key.hex() not in evidence for _, key in pairs)
    assert bytes(range(32)).hex() not in evidence


def test_emulation_revoked_certificate_denied_without_consuming_any_key(tmp_path):
    pki = create_test_pki(tmp_path / "pki")
    pool = DemoKeyPool(secrets.token_bytes(32), experiment_id="fixture", model_id="test-only")
    cert = x509.load_pem_x509_certificate(pki["sae-alice"][0].read_bytes())
    with serve_kme(pool, pki, "kme-alice") as port:
        assert request_kme(port, client_context(pki), "kme-alice", "GET", "sae-bob", "status")["status"] == 200
        pki["_policy"].revoke(cert.serial_number)
        result = request_kme(port, client_context(pki), "kme-alice", "POST", "sae-bob", "enc_keys", {})
        assert result["status"] == 401
    assert pool.master_consumed == 0


def test_independent_kme_checks_common_resource_grants_and_live_revocation(tmp_path, runtime):
    for identity in ("kme-alice", "kme-bob"):
        runtime.grant_identity(identity, ["operator", "consumer"], ["keys:*"])
    runtime.grant_identity("sae-alice", ["consumer"], ["keys:kme-bob"])
    runtime.grant_identity("sae-bob", ["consumer"], ["keys:kme-bob"])
    pki = create_test_pki(tmp_path / "pki")
    pairs = [(str(uuid4()), secrets.token_bytes(32)) for _ in range(2)]
    with independent_pair(tmp_path / "kmes", pki, pairs, {"experiment_id": "fixture", "model_id": "test"},
                          security_runtime=runtime) as (alice, bob):
        denied = request_kme(alice.port, client_context(pki), "kme-alice", "POST", "sae-bob", "enc_keys", {})
        assert denied["status"] == 403
        assert alice.command("summary")["counts"] == {"available": 2}
        runtime.grant_identity("sae-alice", ["consumer"], ["keys:kme-alice"])
        assert request_kme(alice.port, client_context(pki), "kme-alice", "POST", "sae-bob", "enc_keys", {})["status"] == 200
        cert = x509.load_pem_x509_certificate(pki["sae-alice"][0].read_bytes())
        pki["_policy"].revoke(cert.serial_number)
        assert request_kme(alice.port, client_context(pki), "kme-alice", "POST", "sae-bob", "enc_keys", {})["status"] == 401
        assert alice.command("summary")["counts"] == {"available": 1, "issued": 1}


def test_anonymous_and_mis_scoped_direct_ledger_calls_leave_buffer_unchanged(tmp_path, runtime):
    store = ledger(tmp_path, runtime)
    material(store, 1)
    consumer = runtime.grant_identity("wrong-consumer", ["consumer"], ["keys:another-kme"])
    for principal in (None, consumer):
        with runtime.identity_scope(principal):
            with pytest.raises(PermissionError):
                store.reserve({}, "denied")
            with pytest.raises(PermissionError):
                store.recover()
    assert store.summary()["counts"] == {"available": 1}


def test_missing_or_insufficient_demo_grants_fail_before_output_creation(tmp_path, runtime):
    from quantum_autopilot.lab.independent_kme import check_kme_security, run_independent_qkd_demo
    before = runtime.audit.verify()
    assert check_kme_security(runtime)["missing_identities"] == ["kme-alice", "kme-bob", "sae-alice", "sae-bob"]
    output = tmp_path / "uncreated-campaign"
    with pytest.raises(PermissionError):
        run_independent_qkd_demo(output, security=runtime)
    assert not output.exists()
    assert runtime.audit.verify()["count"] == before["count"]
    for requirement in ("kme-alice", "kme-bob", "sae-alice", "sae-bob"):
        runtime.grant_identity(requirement, ["consumer"], ["keys:another-kme"])
    assert check_kme_security(runtime)["incompatible_identities"] == ["kme-alice", "kme-bob", "sae-alice", "sae-bob"]
    with pytest.raises(PermissionError):
        run_independent_qkd_demo(output, security=runtime)
    assert not output.exists()


def test_nonadministrator_cannot_provision_or_renew_credentials(authority):
    principal = authority.runtime.principal("sae-test")
    _, client = issue(authority)
    before = list(authority.root.glob("*.pem"))
    with authority.runtime.identity_scope(principal):
        with pytest.raises(PermissionError):
            authority.provision("sae-test", kind="sae", services=["key-delivery"])
        with pytest.raises(PermissionError):
            authority.renew(client["serial"], identity="sae-test", kind="sae", services=["key-delivery"])
        with pytest.raises(PermissionError):
            authority.client_context(client)
    assert list(authority.root.glob("*.pem")) == before


def test_upgrade_migrates_legacy_consumption_before_restoring_an_older_backup(tmp_path):
    store = ledger(tmp_path)
    pairs = material(store, 2)
    backup = tmp_path / "old-backup.sqlite"
    snapshot(store, backup)
    store.reserve({}, "legacy-issued")
    store.commit_issue("legacy-issued")
    # Identified pre-journal fixture: keep the old ledger's durable consumption,
    # remove only generated TEST journal files to model the legacy release.
    store.journal.path.unlink()
    store.journal.anchor_path.unlink()
    upgraded = ledger(tmp_path)
    assert upgraded.journal.latest()[pairs[0][0]]["state"] == "issued"
    shutil.copyfile(backup, store.path)
    assert ledger(tmp_path).summary()["counts"] == {"available": 1, "invalidated": 1}
