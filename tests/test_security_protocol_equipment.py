"""Attack/fault tests with independently persisted state; no hardware claims."""
from dataclasses import replace
import json
from pathlib import Path
import subprocess
import sys
import time

import pytest

from quantum_autopilot.lab.adapters import SwabianSDKAdapter
from quantum_autopilot.lab.adapter_registry import create_photon_adapter
from quantum_autopilot.lab.observations import synthetic_bb84_observations
from quantum_autopilot.lab.observed_qkd import run_observed_bb84
from quantum_autopilot.lab.photon import AcquisitionError, PhotonConfig
from quantum_autopilot.security.equipment import (EquipmentAuthority, EquipmentDenied, PhysicalIdentity,
                                                AuthorizedPhotonAdapter)
from quantum_autopilot.security.protocol import (AuthenticatedClassicalChannel, ClassicalEnvelope,
    ProtocolAuthenticationError)
from quantum_autopilot.security.runtime import SecurityRuntime


class FixtureVault:
    def get_bytes(self, reference):
        assert reference == "fixture-authentication"
        return b"separate-test-mac-material-32-byte"  # Identified fixture, never a credential.


def channel(path, *, session="session-1", binding="a"*64, sender="alice", receiver="bob"):
    return AuthenticatedClassicalChannel(path, session_id=session, sender=sender, receiver=receiver,
        transcript_binding=binding, key_reference="fixture-authentication", vault=FixtureVault(),
        origin="simulated", mode="emulation")


@pytest.mark.parametrize("field,value", [("payload", {"bases": [1]}), ("session_id", "session-other"),
    ("sender", "mallory"), ("receiver", "mallory"), ("sequence", 8),
    ("transcript_binding", "b"*64), ("operation", "verification"),
    ("previous_sha256", "c"*64), ("authentication_tag", "0"*64)])
def test_tamper_and_cross_session_rejected_without_advancing(field, value, tmp_path):
    transport = channel(tmp_path / "state.sqlite")
    envelope = transport.send("basis_sifting", {"bases": [0]})
    with pytest.raises(ProtocolAuthenticationError):
        transport.receive(replace(envelope, **{field: value}), expected_operation="basis_sifting")
    assert transport.summary()["accepted_messages"] == 0
    assert transport.receive(envelope, expected_operation="basis_sifting") == {"bases": [0]}


def test_replay_rejected_after_independent_object_restart(tmp_path):
    original = channel(tmp_path / "state.sqlite")
    message = original.send("observation", {"observation_sha256": "a"*64})
    original.receive(message, expected_operation="observation")
    restarted = channel(tmp_path / "state.sqlite")
    with pytest.raises(ProtocolAuthenticationError, match="replay"):
        restarted.receive(message, expected_operation="observation")
    assert restarted.summary()["accepted_messages"] == 1


def test_out_of_order_and_mismatched_actual_observations_rejected(tmp_path):
    transport = channel(tmp_path / "state.sqlite")
    first = transport.send("observation", {"count": 1})
    second = transport.send("basis_sifting", {"bases": [0]})
    with pytest.raises(ProtocolAuthenticationError, match="out-of-order"):
        transport.receive(second, expected_operation="basis_sifting")
    with pytest.raises(ProtocolAuthenticationError, match="actual protocol"):
        transport.receive(first, expected_operation="observation", expected_payload={"count": 2})
    transport.receive(first, expected_operation="observation", expected_payload={"count": 1})
    transport.receive(second, expected_operation="basis_sifting")
    assert transport.summary()["accepted_messages"] == 2


def test_classical_direction_cannot_be_reflected(tmp_path):
    outbound = channel(tmp_path / "state.sqlite")
    reverse = channel(tmp_path / "state.sqlite", sender="bob", receiver="alice")
    with pytest.raises(ProtocolAuthenticationError, match="binding"):
        reverse.receive(outbound.send("observation", {}), expected_operation="observation")


def test_atomic_local_processing_failure_releases_no_accepted_block(tmp_path):
    transport = channel(tmp_path / "state.sqlite")
    with pytest.raises(RuntimeError, match="computation interrupted"):
        with transport.atomic_processing():
            transport.exchange("observation", {})
            transport.exchange("basis_sifting", {"bases": [0]})
            assert transport.summary()["accepted_messages"] == 2
            raise RuntimeError("computation interrupted before result")
    assert transport.summary()["accepted_messages"] == 0
    with transport.atomic_processing():
        transport.exchange("observation", {})
        transport.exchange("basis_sifting", {"bases": [0]})
    assert channel(tmp_path / "state.sqlite").summary()["accepted_messages"] == 2


def test_classical_schema_and_key_length_do_not_expose_material(tmp_path):
    with pytest.raises(ProtocolAuthenticationError, match="fields"):
        ClassicalEnvelope.from_dict({"token": "secret-must-not-be-logged"})
    class ShortVault:
        def get_bytes(self, name):
            return b"private"
    with pytest.raises(ProtocolAuthenticationError, match="256 bits") as result:
        AuthenticatedClassicalChannel(tmp_path / "state.sqlite", session_id="x", sender="a", receiver="b",
            transcript_binding="a"*64, key_reference="ref", vault=ShortVault(), origin="simulated", mode="emulation")
    assert "private" not in str(result.value)


def test_qkd_authentication_follows_actual_protocol_and_never_qualifies_fixtures(tmp_path):
    block = synthetic_bb84_observations(count=20000, loss_db=0, efficiency=1, error_probability=0)
    transport = channel(tmp_path / "state.sqlite", binding=block.observation_sha256)
    with transport.atomic_processing():
        result = run_observed_bb84(block, authenticated_channel=transport)
    assert result.summary["reconciliation_verified"]
    assert result.summary["demo_key_bits"] > 0
    auth = result.summary["classical_authentication"]
    assert auth["accepted_messages"] > 10  # Actual parity traffic, not one summary.
    assert auth["authentication_transmitted_bits"] == 256 * auth["accepted_messages"]
    assert result.summary["authentication_cost_bits"] == 256
    assert not auth["information_theoretic_authentication"]
    assert result.summary["certified_secret_bits"] == 0
    assert not result.summary["error_budget"]["composable_security_claim"]
    assert FixtureVault().get_bytes("fixture-authentication") not in (tmp_path / "state.sqlite").read_bytes()
    with pytest.raises(ProtocolAuthenticationError, match="unqualified"):
        run_observed_bb84(block, authenticated_channel=transport, purpose="operational")


def test_unsigned_or_different_fixture_cannot_be_admitted_operationally(tmp_path):
    block = synthetic_bb84_observations(count=1000)
    assert run_observed_bb84(block).summary["classical_authentication"]["operational_admission"] is False
    with pytest.raises(ProtocolAuthenticationError, match="binding"):
        run_observed_bb84(block, authenticated_channel=channel(tmp_path / "state.sqlite"))
    with pytest.raises(ProtocolAuthenticationError, match="unqualified"):
        run_observed_bb84(block, purpose="operational")


def test_atomic_classical_receiver_race_accepts_message_once(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    path = tmp_path / "state.sqlite"
    transport = channel(path)
    message = transport.send("observation", {})
    def accept():
        try:
            channel(path).receive(message, expected_operation="observation")
            return True
        except ProtocolAuthenticationError:
            return False
    with ThreadPoolExecutor(max_workers=4) as executor:
        assert sum(executor.map(lambda _: accept(), range(4))) == 1


def test_lease_identity_calibration_clock_and_expiry_required(tmp_path):
    now = [100.]
    authority = EquipmentAuthority(tmp_path, now=lambda: now[0])
    lease = authority.acquire(PhysicalIdentity("fixture-serial", "usb://fixture-serial"), owner="alice", session_id="s", ttl_s=20)
    try:
        with pytest.raises(EquipmentDenied, match="configuration"):
            lease.require(owner="alice", session_id="s", acquisition=True)
        lease.configuration_verified("a"*64)
        lease.calibrated(valid_for_s=5, synchronized=False)
        with pytest.raises(EquipmentDenied, match="synchronization"):
            lease.require(owner="alice", session_id="s", configuration_id="a"*64, acquisition=True)
        lease.configuration_verified("a"*64)
        lease.calibrated(valid_for_s=5, synchronized=True)
        lease.require(owner="alice", session_id="s", configuration_id="a"*64, acquisition=True)
        now[0] += 6
        with pytest.raises(EquipmentDenied, match="calibration"):
            lease.require(owner="alice", session_id="s", configuration_id="a"*64, acquisition=True)
        now[0] += 15
        with pytest.raises(EquipmentDenied, match="expired"):
            lease.renew()
    finally:
        lease.revoke()


def test_live_holder_cannot_be_stolen_even_after_ttl_and_across_endpoints(tmp_path):
    now = [10.]
    authority = EquipmentAuthority(tmp_path, now=lambda: now[0])
    lease = authority.acquire(PhysicalIdentity("same-serial", "usb://same-serial"), owner="alice", session_id="s", ttl_s=1)
    try:
        now[0] += 2
        with pytest.raises(EquipmentDenied, match="another session"):
            authority.acquire(PhysicalIdentity("same-serial", "tcp://instrument:41101"), owner="bob", session_id="t")
    finally:
        lease.revoke()
    replacement = authority.acquire(PhysicalIdentity("same-serial", "tcp://instrument:41101"), owner="bob", session_id="t")
    try:
        assert replacement.fence == lease.fence + 1
        with pytest.raises(EquipmentDenied):
            lease.require(owner="alice", session_id="s")
    finally:
        replacement.revoke()


def test_uncertain_configuration_and_changed_session_revoke_prerequisites(tmp_path):
    authority = EquipmentAuthority(tmp_path)
    lease = authority.acquire(PhysicalIdentity("fixture-serial", "usb://fixture-serial"), owner="alice", session_id="s")
    try:
        lease.configuration_verified("a"*64)
        lease.calibrated(valid_for_s=30, synchronized=True)
        with pytest.raises(EquipmentDenied, match="session changed"):
            lease.require(owner="alice", session_id="other")
        lease.uncertain()
        with pytest.raises(EquipmentDenied, match="configuration"):
            lease.require(owner="alice", session_id="s", configuration_id="a"*64, acquisition=True)
        with pytest.raises(EquipmentDenied, match="readback"):
            lease.calibrated(valid_for_s=30, synchronized=True)
    finally:
        lease.revoke()


def test_process_death_releases_os_authority_and_increments_fencing(tmp_path):
    program = """import sys,time
from quantum_autopilot.security.equipment import EquipmentAuthority,PhysicalIdentity
l=EquipmentAuthority(sys.argv[1]).acquire(PhysicalIdentity('process-fixture','usb://process-fixture'),owner='child',session_id='child-session')
print(l.fence,flush=True)
time.sleep(60)
"""
    child = subprocess.Popen([sys.executable, "-c", program, str(tmp_path)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        assert child.stdout.readline().strip() == "1"
        authority = EquipmentAuthority(tmp_path)
        with pytest.raises(EquipmentDenied, match="another session"):
            authority.acquire(PhysicalIdentity("process-fixture", "usb://process-fixture"), owner="parent", session_id="parent-session")
        child.kill()
        child.wait(timeout=10)
        # Windows can finish handle cleanup shortly after wait() observes
        # process death. Never steal the lock; retry boundedly until OS release.
        deadline = time.monotonic() + 5
        while True:
            try:
                replacement = authority.acquire(PhysicalIdentity("process-fixture", "usb://process-fixture"), owner="parent", session_id="parent-session")
                break
            except EquipmentDenied:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(.02)
        try:
            assert replacement.fence == 2
            with pytest.raises(EquipmentDenied, match="configuration"):
                replacement.require(owner="parent", session_id="parent-session", acquisition=True)
        finally:
            replacement.revoke()
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=10)


def test_physical_registry_and_raw_native_adapter_cannot_bypass_authority():
    with pytest.raises(Exception, match="authenticated runtime"):
        create_photon_adapter("swabian-native", {"serial": "fixture"})
    adapter = SwabianSDKAdapter(serial="fixture")
    adapter.configuration = PhotonConfig()
    adapter.state = "ready"
    with pytest.raises(AcquisitionError, match="authority"):
        adapter.acquire("physical-bypass")
    with pytest.raises(AcquisitionError, match="authority"):
        adapter.acquire_continuous("physical-bypass", "unused.sqlite")


def test_common_runtime_denies_anonymous_adapter_calls_and_revoked_sessions(tmp_path):
    runtime = SecurityRuntime(tmp_path / "security")
    adapter = create_photon_adapter("emulated", security=runtime)
    with runtime.identity_scope(None), pytest.raises(PermissionError):
        adapter.connect()
    token = runtime.issue_local_session()
    principal = runtime.session(token)
    scoped = create_photon_adapter("emulated", security=runtime, principal=principal)
    scoped.connect()
    scoped.configure(PhotonConfig())
    scoped.calibrate()
    assert scoped.acquire("signed-session-demo").origin == "simulated"
    runtime.revoke_session(token)
    with pytest.raises(PermissionError):
        scoped.acquire("revoked-session-demo")


def test_test_only_transport_cannot_operate_installed_sdk():
    from types import SimpleNamespace
    adapter = SwabianSDKAdapter(test_only_transport=True)
    adapter.sdk = SimpleNamespace(__file__="TimeTagger.pyd")
    with pytest.raises(AcquisitionError, match="installed native SDK"):
        adapter._require_acquisition_authority()


def test_authorized_wrapper_revokes_on_disconnect_and_changed_session(tmp_path):
    from types import SimpleNamespace
    class IdentifiedFixture:
        # This double proves software gates; its photons are explicitly simulated.
        adapter_id, serial, address, state = "fixture", "fixture-serial", None, "registered"
        configuration = None
        def __init__(self):
            self.tagger = SimpleNamespace(getReferenceClockState=lambda: SimpleNamespace(enabled=True, is_locked=True))
        def identify(self):
            return {"mode": "hardware", "origin": "simulated", "hardware_connected": False}
        def connect(self):
            self.state = "connected"
            return self.identify()
        def configure(self, cfg):
            self.configuration, self.state = cfg, "configured"
            return {"readback_verified": True, "configuration_id": cfg.configuration_id}
        def calibrate(self):
            self.state = "ready"
            return {"hardware_validated": False}
        def acquire(self, experiment_id, fault=None):
            from quantum_autopilot.lab.adapters import EmulatedPhotonAdapter
            simulated = EmulatedPhotonAdapter()
            simulated.connect(); simulated.configure(self.configuration); simulated.calibrate()
            return simulated.acquire(experiment_id)
        def disconnect(self):
            self.state = "disconnected"
    runtime = SecurityRuntime(tmp_path / "runtime")
    authority = EquipmentAuthority(tmp_path / "authority")
    wrapper = AuthorizedPhotonAdapter(IdentifiedFixture(), runtime, authority=authority)
    wrapper.connect(); wrapper.configure(PhotonConfig()); wrapper.calibrate()
    lease = wrapper.lease
    assert wrapper.acquire("fixture-authorized").origin == "simulated"
    wrapper.disconnect()
    with pytest.raises(EquipmentDenied, match="revoked"):
        lease.require(owner=lease.owner, session_id=lease.session_id)
    wrapper.connect(); wrapper.configure(PhotonConfig()); wrapper.calibrate()
    lease = wrapper.lease
    runtime.device_session_identity(new_session=True)
    with pytest.raises(EquipmentDenied, match="session changed"):
        wrapper.acquire("fixture-changed-session")
    assert wrapper.lease is None
    with pytest.raises(EquipmentDenied, match="revoked"):
        lease.require(owner=lease.owner, session_id=lease.session_id)


def test_classical_common_runtime_logs_outcomes_without_bits_or_mac_key(tmp_path):
    runtime = SecurityRuntime(tmp_path / "runtime")
    runtime.vault.get_or_create_bytes("qkd-auth-ref", size=32)
    secure = AuthenticatedClassicalChannel(tmp_path / "protocol.sqlite", session_id="signed",
        sender="fixture-alice", receiver="fixture-bob", transcript_binding="a"*64,
        key_reference="qkd-auth-ref", vault=runtime.vault, origin="simulated", mode="emulation", runtime=runtime)
    message = secure.send("basis_sifting", {"private_fixture_sentinel": "never-log-observation"})
    with pytest.raises(ProtocolAuthenticationError):
        secure.receive(replace(message, authentication_tag="0"*64), expected_operation="basis_sifting")
    secure.receive(message, expected_operation="basis_sifting")
    log = runtime.audit.path.read_text(encoding="utf-8")
    assert "never-log-observation" not in log
    assert message.authentication_tag not in log
    assert runtime.vault.get_bytes("qkd-auth-ref").hex() not in log
    assert '"classical_message_accepted":false' in log and '"classical_message_accepted":true' in log
    with runtime.identity_scope(None), pytest.raises(PermissionError):
        secure.send("verification", {})


def test_restoring_old_classical_database_is_quarantined_by_protected_checkpoint(tmp_path):
    import sqlite3
    runtime = SecurityRuntime(tmp_path / "runtime")
    runtime.vault.get_or_create_bytes("qkd-auth-ref", size=32)
    options = dict(session_id="backup-test", sender="fixture-alice", receiver="fixture-bob",
        transcript_binding="a"*64, key_reference="qkd-auth-ref", vault=runtime.vault,
        origin="simulated", mode="emulation", runtime=runtime)
    path = tmp_path / "protocol.sqlite"
    secure = AuthenticatedClassicalChannel(path, **options)
    with sqlite3.connect(path) as source, sqlite3.connect(tmp_path / "backup.sqlite") as destination:
        source.backup(destination)
    secure.exchange("observation", {})
    assert secure.summary()["backup_rollback_protected"]
    with sqlite3.connect(tmp_path / "backup.sqlite") as source, sqlite3.connect(path) as destination:
        source.backup(destination)
    with pytest.raises(ProtocolAuthenticationError, match="quarantined"):
        AuthenticatedClassicalChannel(path, **options)


@pytest.mark.parametrize("scenario", ["disconnect_failed", "session_revoked_during_acquisition"])
def test_inflight_native_work_keeps_exclusivity_until_transport_stopped(scenario, tmp_path):
    from types import SimpleNamespace
    runtime = SecurityRuntime(tmp_path / "runtime")
    authority = EquipmentAuthority(tmp_path / "authority")
    token = runtime.issue_local_session()
    principal = runtime.session(token)
    fixture = SimpleNamespace(adapter_id="fixture", serial="busy-fixture", address=None,
        state="registered", configuration=None,
        identify=lambda: {"mode": "hardware", "origin": "simulated", "hardware_connected": False},
        connect=lambda: {"fixture": True})
    def stop():
        if fixture.state == "acquiring":
            raise AcquisitionError("synthetic acquisition still running")
        fixture.state = "disconnected"
    fixture.disconnect = stop
    wrapper = AuthorizedPhotonAdapter(fixture, runtime, principal, authority=authority)
    wrapper.connect()
    lease = wrapper.lease
    fixture.state = "acquiring"
    if scenario == "disconnect_failed":
        with pytest.raises(AcquisitionError, match="still running"):
            wrapper.disconnect()
    else:
        runtime.revoke_session(token)
        with pytest.raises(PermissionError):
            wrapper._native_guard(acquisition=True)
    assert wrapper.lease is lease
    with pytest.raises(EquipmentDenied, match="another session"):
        authority.acquire(PhysicalIdentity("busy-fixture", "usb://busy-fixture"), owner="other", session_id="other")
    fixture.state = "fault"  # Native finally/stop has completed.
    if scenario == "disconnect_failed":
        wrapper.disconnect()
    else:
        wrapper._uncertain()
    assert wrapper.lease is None
    replacement = authority.acquire(PhysicalIdentity("busy-fixture", "usb://busy-fixture"), owner="other", session_id="other")
    replacement.revoke()
