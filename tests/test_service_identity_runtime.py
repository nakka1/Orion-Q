"""Service authority is proven and revocable without an interactive OS account."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import json
import os
from threading import Event, Thread
import time

import pytest

from quantum_autopilot.security import (AuthenticationError, AuthorizationError, Principal,
                                       SecurityRuntime, VaultError)
from quantum_autopilot.security.secret_providers import AESGCMSecretProvider, MountedSecretProvider
from quantum_autopilot.security.service_identity import (ServiceIdentityBinding, ServiceIdentityProvision,
                                                       read_mounted_service_credential)
from quantum_autopilot.security.vault import WindowsDPAPIProvider


ADMIN_CREDENTIAL = b"fixture-only-admin-credential-32-bytes-or-more"
WORKER_CREDENTIAL = b"fixture-only-worker-credential-32-bytes-or-more"
KEY = b"0123456789abcdef0123456789abcdef"


class FixtureKeySource:
    def read_key(self, key_id, version):
        if (key_id, version) != ("fixture", 1):
            raise VaultError("unavailable fixture key")
        return KEY


def provider():
    return AESGCMSecretProvider(FixtureKeySource(), active_key_id="fixture", active_version=1)


def provision_runtime(tmp_path, monkeypatch):
    def forbidden():
        raise AssertionError("service profile consulted interactive OS identity")
    monkeypatch.setattr("quantum_autopilot.security.runtime.operating_system_identity", forbidden)
    runtime = SecurityRuntime(tmp_path / "security", profile="operational", identity_profile="service",
        secret_provider=provider(), bootstrap=True,
        service_bootstrap=ServiceIdentityProvision("orion-admin", ("administrator",), ("*",), ADMIN_CREDENTIAL))
    admin = runtime.bind_service_identity(ServiceIdentityBinding("orion-admin", lambda: ADMIN_CREDENTIAL))
    return runtime, admin


def test_service_requires_explicit_protection_and_offline_provisioning(tmp_path):
    with pytest.raises(VaultError):
        SecurityRuntime(tmp_path / "without-provider", identity_profile="service")
    with pytest.raises(VaultError):
        SecurityRuntime(tmp_path / "desktop-provider", identity_profile="service", secret_provider=WindowsDPAPIProvider())
    with pytest.raises(AuthenticationError):
        SecurityRuntime(tmp_path / "unprovisioned", profile="operational", identity_profile="service", secret_provider=provider())
    with pytest.raises(AuthenticationError):
        SecurityRuntime(tmp_path / "implicit-bootstrap", identity_profile="service", secret_provider=provider(), bootstrap=True)
    with pytest.raises(AuthenticationError):
        SecurityRuntime(tmp_path / "non-admin", identity_profile="service", secret_provider=provider(), bootstrap=True,
            service_bootstrap=ServiceIdentityProvision("operator", ("operator",), ("*",), WORKER_CREDENTIAL))


def test_noninteractive_startup_and_restart_do_not_consult_os_identity(tmp_path, monkeypatch):
    runtime, admin = provision_runtime(tmp_path, monkeypatch)
    assert admin.source == "service" and admin.session_id is not None
    assert runtime.current_identity().subject == "orion-admin"
    restarted = SecurityRuntime(runtime.root, profile="operational", identity_profile="service", secret_provider=provider())
    with pytest.raises(AuthenticationError):
        restarted.current_identity()
    with pytest.raises(AuthenticationError):
        restarted.local_identity()
    with pytest.raises(AuthenticationError):
        restarted.authorize("network.read", "network:all", admin)
    restarted.bind_service_identity(ServiceIdentityBinding("orion-admin", lambda: ADMIN_CREDENTIAL))
    assert restarted.authorize("network.read", "network:all").subject == "orion-admin"
    with pytest.raises(AuthenticationError):
        SecurityRuntime(runtime.root, profile="operational", identity_profile="service", secret_provider=provider(), bootstrap=True,
            service_bootstrap=ServiceIdentityProvision("attacker", ("administrator",), ("*",), ADMIN_CREDENTIAL))


def test_environment_headers_and_subjects_cannot_authenticate_service(tmp_path, monkeypatch):
    runtime, _ = provision_runtime(tmp_path, monkeypatch)
    runtime.grant_service_identity("core", ("operator",), ("network:one*",), WORKER_CREDENTIAL)
    monkeypatch.setenv("USERNAME", "orion-admin")
    monkeypatch.setenv("ORION_SERVICE_SUBJECT", "orion-admin")
    candidate = Principal("orion-admin", roles=frozenset({"administrator"}), scopes=("*",), source="service")
    with pytest.raises(AuthenticationError):
        runtime.authorize("network.configure", "network:one", candidate)
    with pytest.raises(AuthenticationError):
        runtime.authenticate_service("core", ADMIN_CREDENTIAL)
    with pytest.raises(AuthenticationError):
        runtime.authenticate_service("unknown", WORKER_CREDENTIAL)
    with runtime.identity_scope(None), pytest.raises(AuthenticationError):
        runtime.authorize("network.read", "network:one")
    authenticated = runtime.authenticate_service("core", WORKER_CREDENTIAL)
    with runtime.identity_scope(replace(authenticated, roles=frozenset({"administrator"}), scopes=("*",))):
        assert runtime.authorize("network.configure", "network:one").roles == frozenset({"operator"})
        with pytest.raises(AuthorizationError):
            runtime.authorize("security.manage", "network:one")
        with pytest.raises(AuthorizationError):
            runtime.authorize("network.read", "network:other")


def test_service_binding_failure_clears_old_process_authority(tmp_path, monkeypatch):
    runtime, original = provision_runtime(tmp_path, monkeypatch)
    with pytest.raises(AuthenticationError):
        runtime.bind_service_identity(ServiceIdentityBinding("orion-admin", lambda: WORKER_CREDENTIAL))
    with pytest.raises(AuthenticationError):
        runtime.authorize("network.read", "network:all")
    with pytest.raises(AuthenticationError):
        runtime.authorize("network.read", "network:all", original)


def test_service_revocation_is_rechecked_after_dispatch_and_in_open_scope(tmp_path, monkeypatch):
    runtime, admin = provision_runtime(tmp_path, monkeypatch)
    runtime.grant_service_identity("core", ("operator",), ("network:*",), WORKER_CREDENTIAL)
    worker = runtime.authenticate_service("core", WORKER_CREDENTIAL)
    with runtime.identity_scope(worker):
        captured = runtime.capture_identity_for_dispatch()
        runtime.authorize("network.read", "network:one")
        with runtime.identity_scope(admin):
            runtime.revoke_identity("core")
        with pytest.raises(AuthenticationError):
            runtime.current_identity()
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(runtime.authorize, "network.configure", "network:one", captured)
        with pytest.raises(AuthenticationError):
            future.result()


def test_service_credential_rotation_revokes_prior_sessions_and_changes_secret(tmp_path, monkeypatch):
    runtime, _ = provision_runtime(tmp_path, monkeypatch)
    runtime.grant_service_identity("core", ("operator",), ("*",), WORKER_CREDENTIAL)
    old = runtime.authenticate_service("core", WORKER_CREDENTIAL)
    replacement = b"replacement-fixture-service-credential-at-least-32"
    runtime.grant_service_identity("core", ("auditor",), ("*",), replacement)
    with pytest.raises(AuthenticationError):
        runtime.authorize("network.read", "network:all", old)
    with pytest.raises(AuthenticationError):
        runtime.authenticate_service("core", WORKER_CREDENTIAL)
    new = runtime.authenticate_service("core", replacement)
    assert new.roles == frozenset({"auditor"})
    with runtime.identity_scope(new), pytest.raises(AuthorizationError):
        runtime.authorize("network.configure", "network:all")


def test_service_device_authority_is_explicit_session_and_never_renews_silently(tmp_path, monkeypatch):
    runtime, admin = provision_runtime(tmp_path, monkeypatch)
    runtime.grant_service_identity("core", ("operator",), ("*",), WORKER_CREDENTIAL)
    worker = runtime.authenticate_service("core", WORKER_CREDENTIAL)
    with runtime.identity_scope(worker):
        device_identity = runtime.device_session_identity()
        assert device_identity.session_id == worker.session_id
    with runtime.identity_scope(admin):
        runtime.grant_identity("core", ["operator"], ["*"], source="service")
    with pytest.raises(AuthenticationError):
        runtime.device_session_identity()
    fresh = runtime.authenticate_service("core", WORKER_CREDENTIAL)
    with runtime.identity_scope(fresh):
        assert runtime.device_session_identity(new_session=True).session_id == fresh.session_id
    with runtime.identity_scope(runtime.principal("core")), pytest.raises(AuthenticationError):
        runtime.device_session_identity(new_session=True)


def test_expiry_secret_logging_and_credential_representation(tmp_path, monkeypatch):
    runtime, _ = provision_runtime(tmp_path, monkeypatch)
    runtime.grant_service_identity("core", ("operator",), ("*",), WORKER_CREDENTIAL,
                                   expires_at=time.time() + 100)
    principal = runtime.authenticate_service("core", WORKER_CREDENTIAL)
    runtime._sessions[principal.session_id]["expires"] = time.time() - 1
    with pytest.raises(AuthenticationError):
        runtime.authorize("network.read", "network:all", principal)
    audit = runtime.audit.path.read_bytes()
    assert ADMIN_CREDENTIAL not in audit and WORKER_CREDENTIAL not in audit
    assert ADMIN_CREDENTIAL not in runtime.vault.path.read_bytes()
    assert ADMIN_CREDENTIAL.decode() not in repr(ServiceIdentityProvision("admin", ("administrator",), ("*",), ADMIN_CREDENTIAL))
    assert runtime.audit.verify()["valid"]


def test_secret_reader_failures_do_not_expose_credentials_or_keep_authority(tmp_path, monkeypatch):
    runtime, _ = provision_runtime(tmp_path, monkeypatch)
    def unavailable_secret_service():
        raise RuntimeError("SDK included credential: " + ADMIN_CREDENTIAL.decode())
    with pytest.raises(AuthenticationError) as failure:
        runtime.bind_service_identity(ServiceIdentityBinding("orion-admin", unavailable_secret_service))
    assert ADMIN_CREDENTIAL.decode() not in str(failure.value)
    assert failure.value.__suppress_context__
    with pytest.raises(AuthenticationError):
        runtime.current_identity()


def test_worker_configuration_contains_only_public_references_and_no_ambient_identity(tmp_path, monkeypatch):
    runtime, _ = provision_runtime(tmp_path, monkeypatch)
    key_file = tmp_path / "kek.bin"
    key_file.write_bytes(KEY)
    if os.name != "nt":
        key_file.chmod(0o600)
    if os.name == "nt":
        class PublicReferenceFixtureProvider(AESGCMSecretProvider):
            def public_configuration(self):
                return {"active_key_id": "fixture", "active_version": 1,
                        "keys": [{"key_id": "fixture", "version": 1, "path": str(key_file)}]}
        runtime._secret_provider = PublicReferenceFixtureProvider(FixtureKeySource(), active_key_id="fixture", active_version=1)
    else:
        runtime._secret_provider = MountedSecretProvider({("fixture", 1): key_file}, active_key_id="fixture", active_version=1)
    configuration = runtime.worker_configuration()
    serialized = json.dumps(configuration)
    assert configuration["identity_profile"] == "service"
    assert ADMIN_CREDENTIAL.decode() not in serialized and KEY.decode() not in serialized
    assert "credential_reader" not in serialized and "service_identity" not in serialized
    if os.name == "nt":
        # Windows mode bits do not validate ACLs; no accidental DPAPI fallback.
        with pytest.raises(VaultError):
            SecurityRuntime.from_worker_configuration(configuration)
    else:
        reconstructed = SecurityRuntime.from_worker_configuration(configuration)
        with pytest.raises(AuthenticationError):
            reconstructed.current_identity()
        reconstructed.authenticate_service("orion-admin", ADMIN_CREDENTIAL)
    with pytest.raises(ValueError):
        SecurityRuntime.from_worker_configuration(dict(configuration, service_identity="orion-admin"))


def test_service_credential_mount_requires_verified_permissions_and_bounded_material(tmp_path):
    path = tmp_path / "identity.bin"
    path.write_bytes(WORKER_CREDENTIAL)
    if os.name == "nt":
        with pytest.raises(AuthenticationError):
            read_mounted_service_credential(path)
        assert read_mounted_service_credential(path, permissions_validator=lambda *_: True) == WORKER_CREDENTIAL
    else:
        path.chmod(0o600)
        assert read_mounted_service_credential(path) == WORKER_CREDENTIAL
        path.chmod(0o644)
        with pytest.raises(AuthenticationError):
            read_mounted_service_credential(path)
    path.write_bytes(b"short")
    with pytest.raises(AuthenticationError):
        read_mounted_service_credential(path, permissions_validator=lambda *_: True)


@pytest.mark.parametrize("kind", ["directory", "oversized", "fifo"])
def test_service_credential_mount_rejects_nonregular_or_oversized_before_open(tmp_path, monkeypatch, kind):
    import quantum_autopilot.security.service_identity as identity_module
    path = tmp_path / "unsafe-credential"
    if kind == "directory":
        path.mkdir()
    elif kind == "oversized":
        path.write_bytes(b"x" * 4097)
    else:
        if not hasattr(os, "mkfifo"):
            pytest.skip("FIFO creation is available on the Linux deployment target")
        os.mkfifo(path)
    def forbidden_open(*_args, **_kwargs):
        raise AssertionError("unsafe credential must be rejected before opening")
    monkeypatch.setattr(identity_module.os, "open", forbidden_open)
    with pytest.raises(AuthenticationError):
        read_mounted_service_credential(path, permissions_validator=lambda *_: True)


def test_grant_rotation_during_service_authentication_cannot_accept_old_secret(tmp_path, monkeypatch):
    runtime, _ = provision_runtime(tmp_path, monkeypatch)
    runtime.grant_service_identity("core", ("operator",), ("*",), WORKER_CREDENTIAL)
    original = runtime._issue_session
    replacement = b"replacement-fixture-service-credential-at-least-32"
    def rotate_before_issue(identity, lifetime, **kwargs):
        runtime.grant_service_identity("core", ("administrator",), ("*",), replacement)
        return original(identity, lifetime, **kwargs)
    monkeypatch.setattr(runtime, "_issue_session", rotate_before_issue)
    with pytest.raises(AuthenticationError):
        runtime.authenticate_service("core", WORKER_CREDENTIAL)


@pytest.mark.parametrize("subject,roles,scopes,credential", [
    ("os:uid:123", ("administrator",), ("*",), ADMIN_CREDENTIAL),
    ("core\nadmin", ("administrator",), ("*",), ADMIN_CREDENTIAL),
    ("core", ("unknown",), ("*",), WORKER_CREDENTIAL),
    ("core", ("operator",), ("bad*middle",), WORKER_CREDENTIAL),
    ("core", ("operator",), ("*",), b"weak"),
])
def test_service_provision_rejects_invalid_grant_or_credential(subject, roles, scopes, credential):
    with pytest.raises((ValueError, AuthenticationError)):
        ServiceIdentityProvision(subject, roles, scopes, credential)


class ProcessCredentialFixture:
    def __init__(self):
        self.value = WORKER_CREDENTIAL
        self.calls = 0
        self.on_read = None

    def read(self):
        self.calls += 1
        if self.on_read is not None:
            self.on_read()
        return self.value


def renewing_process(tmp_path, monkeypatch, *, ttl_seconds=10, grant_expiry=None):
    now = [10_000.0]
    monkeypatch.setattr("quantum_autopilot.security.runtime.time.time", lambda: now[0])
    administrator, admin = provision_runtime(tmp_path, monkeypatch)
    with administrator.identity_scope(admin):
        administrator.grant_service_identity("renewing-core", ("operator",), ("*",), WORKER_CREDENTIAL,
            expires_at=grant_expiry)
    reader = ProcessCredentialFixture()
    binding = ServiceIdentityBinding("renewing-core", reader.read, ttl_seconds=ttl_seconds)
    runtime = SecurityRuntime(administrator.root, profile="operational", identity_profile="service",
        secret_provider=provider(), bootstrap=False, service_identity=binding, process_identity_renewal=True)
    return runtime, administrator, admin, reader, now


def test_process_renewal_retains_old_equipment_and_inflight_authority(tmp_path, monkeypatch):
    runtime, _, _, reader, now = renewing_process(tmp_path, monkeypatch)
    previous = runtime.current_identity()
    assert runtime.device_session_identity().session_id == previous.session_id
    entered, release = Event(), Event()
    result = []
    def in_flight():
        with runtime.identity_scope(previous):
            entered.set()
            assert release.wait(2)
            result.append(runtime.authorize("network.operate", "network:topology").session_id)
    thread = Thread(target=in_flight)
    thread.start()
    try:
        assert entered.wait(1)
        now[0] += 6
        refreshed = runtime.maintain_process_identity()
        assert refreshed.session_id != previous.session_id
        assert refreshed.subject == previous.subject and refreshed.roles == previous.roles
        assert reader.calls == 2
        assert not runtime._sessions[previous.session_id]["revoked"]
        assert runtime.device_session_identity().session_id == previous.session_id
    finally:
        release.set()
        thread.join(2)
    assert result == [previous.session_id]
    now[0] += 5
    assert runtime.current_identity().session_id != previous.session_id
    with pytest.raises(AuthenticationError):
        runtime.device_session_identity()
    assert runtime._device_service_identity.session_id == previous.session_id
    with pytest.raises(AuthenticationError):
        runtime.authorize("network.operate", "network:topology", previous)
    # Reconnection remains an explicit decision. Renewal never takes it.
    assert runtime.device_session_identity(new_session=True).session_id != previous.session_id
    assert runtime.audit.verify()["valid"]


def test_process_renewal_is_single_under_concurrent_authorized_calls(tmp_path, monkeypatch):
    runtime, _, _, reader, now = renewing_process(tmp_path, monkeypatch)
    previous = runtime.current_identity()
    now[0] += 6
    with ThreadPoolExecutor(max_workers=8) as pool:
        identities = list(pool.map(lambda _: runtime.authorize("network.read", "network:topology"), range(32)))
    assert len({identity.session_id for identity in identities}) == 1
    assert identities[0].session_id != previous.session_id
    assert reader.calls == 2
    evidence = [row for row in runtime.audit.path.read_text().splitlines() if '"service.process-renew"' in row]
    assert len(evidence) == 1


@pytest.mark.parametrize("change", ["revoked-session", "expired-session", "revoked-identity", "changed-grant"])
def test_process_invalid_authority_blocks_before_credential_and_never_resurrects(tmp_path, monkeypatch, change):
    runtime, administrator, admin, reader, now = renewing_process(tmp_path, monkeypatch)
    previous = runtime.current_identity()
    now[0] += 6
    if change == "revoked-session":
        with runtime._session_lock:
            runtime._sessions[previous.session_id]["revoked"] = True
    elif change == "expired-session":
        now[0] = previous.expires_at
    else:
        with administrator.identity_scope(admin):
            if change == "revoked-identity":
                administrator.revoke_identity(previous.subject)
            else:
                administrator.grant_identity(previous.subject, ("auditor",), ("*",), source="service")
    with pytest.raises(AuthenticationError):
        runtime.maintain_process_identity()
    assert reader.calls == 1
    # Even a subsequent valid grant cannot restart a blocked process implicitly.
    with administrator.identity_scope(admin):
        administrator.grant_service_identity(previous.subject, ("operator",), ("*",), WORKER_CREDENTIAL)
    with pytest.raises(AuthenticationError):
        runtime.current_identity()
    assert reader.calls == 1 and runtime._process_identity is None
    recovered = runtime.bind_service_identity(ServiceIdentityBinding(previous.subject, reader.read, ttl_seconds=10))
    assert recovered.session_id != previous.session_id and reader.calls == 2


@pytest.mark.parametrize("failure", ["mismatch", "reader-error", "rotated-vault-secret"])
def test_process_credential_failure_latches_without_secret_logs_or_retry(tmp_path, monkeypatch, failure):
    runtime, administrator, admin, reader, now = renewing_process(tmp_path, monkeypatch)
    previous = runtime.current_identity()
    now[0] += 6
    if failure == "mismatch":
        reader.value = b"incorrect-renewal-fixture-credential-at-least-32"
    elif failure == "reader-error":
        def fail():
            raise RuntimeError("private-reader-fixture-do-not-log")
        reader.on_read = fail
    else:
        from quantum_autopilot.security.service_identity import service_credential_reference
        with administrator.identity_scope(admin):
            administrator.recover_secret(service_credential_reference(previous.subject),
                b"rotated-renewal-fixture-credential-at-least-32")
    with pytest.raises(AuthenticationError, match="explicit recovery"):
        runtime.current_identity()
    assert reader.calls == 2
    reader.value, reader.on_read = WORKER_CREDENTIAL, None
    with pytest.raises(AuthenticationError):
        runtime.current_identity()
    assert reader.calls == 2
    audit = runtime.audit.path.read_text()
    assert "service.process-renew" in audit and "authentication_failed" in audit
    assert "private-reader-fixture" not in audit and WORKER_CREDENTIAL.decode() not in audit
    assert runtime.audit.verify()["valid"]


@pytest.mark.parametrize("race", ["session-revocation", "grant-change", "identity-revocation", "grant-change-after-issue"])
def test_process_renewal_rechecks_authority_after_credential_io(tmp_path, monkeypatch, race):
    runtime, administrator, admin, reader, now = renewing_process(tmp_path, monkeypatch)
    previous = runtime.current_identity()
    existing = set(runtime._sessions)
    now[0] += 6
    def change_during_read():
        if race == "session-revocation":
            with runtime._session_lock:
                runtime._sessions[previous.session_id]["revoked"] = True
        else:
            with administrator.identity_scope(admin):
                if race == "grant-change":
                    administrator.grant_identity(previous.subject, ("administrator",), ("*",), source="service")
                else:
                    administrator.revoke_identity(previous.subject)
    if race == "grant-change-after-issue":
        original_record = runtime.audit.record
        def change_after_issue(identity, operation, *args, **kwargs):
            original_record(identity, operation, *args, **kwargs)
            if operation == "session.issue":
                with administrator.identity_scope(admin):
                    administrator.grant_identity(previous.subject, ("auditor",), ("*",), source="service")
        monkeypatch.setattr(runtime.audit, "record", change_after_issue)
    else:
        reader.on_read = change_during_read
    with pytest.raises(AuthenticationError):
        runtime.current_identity()
    assert reader.calls == 2 and runtime._process_identity is None
    assert not set(runtime._sessions) - existing
    with pytest.raises(AuthenticationError):
        runtime.current_identity()
    assert reader.calls == 2


def test_process_renewal_never_extends_identity_grant_expiry(tmp_path, monkeypatch):
    runtime, _, _, reader, now = renewing_process(tmp_path, monkeypatch, grant_expiry=10_012.0)
    now[0] += 6
    renewed = runtime.current_identity()
    assert renewed.expires_at == 10_012.0
    assert runtime.process_identity_maintenance_delay() == 6
    now[0] = 10_012.0
    with pytest.raises(AuthenticationError):
        runtime.current_identity()
    assert reader.calls == 2


def test_process_sessions_are_bounded_and_cannot_reuse_old_tokens(tmp_path, monkeypatch):
    runtime, _, _, reader, now = renewing_process(tmp_path, monkeypatch)
    original = runtime.current_identity()
    runtime.device_session_identity()
    for _ in range(40):
        now[0] += 6
        runtime.current_identity()
        assert len(runtime._sessions) <= 3
    assert reader.calls == 41
    assert runtime._device_service_identity.session_id == original.session_id
    with pytest.raises(AuthenticationError):
        runtime.device_session_identity()
    with pytest.raises(AuthenticationError):
        runtime.authorize("network.read", "network:topology", original)
    assert runtime.audit.verify()["valid"]


def test_process_renewal_cannot_authenticate_anonymous_or_extend_explicit_caller(tmp_path, monkeypatch):
    runtime, _, _, reader, now = renewing_process(tmp_path, monkeypatch)
    human_like_explicit = runtime.authenticate_service("renewing-core", WORKER_CREDENTIAL, ttl_seconds=1)
    with runtime.identity_scope(human_like_explicit):
        now[0] += 6
        with pytest.raises(AuthenticationError):
            runtime.current_identity()
        with pytest.raises(AuthenticationError):
            runtime.maintain_process_identity()
    with runtime.identity_scope(None):
        with pytest.raises(AuthenticationError):
            runtime.current_identity()
    assert reader.calls == 1
    assert runtime.current_identity().session_id != human_like_explicit.session_id
    assert reader.calls == 2


def test_process_renewal_is_not_enabled_for_desktop_or_unbound_workers(tmp_path):
    with pytest.raises(ValueError):
        SecurityRuntime(tmp_path, process_identity_renewal=True)


def test_failed_session_issue_audit_drops_provisional_process_session(tmp_path, monkeypatch):
    runtime, _, _, reader, now = renewing_process(tmp_path, monkeypatch)
    existing = set(runtime._sessions)
    record = runtime.audit.record
    def failed_issue(identity, operation, *args, **kwargs):
        if operation == "session.issue":
            raise RuntimeError("private-audit-fixture-must-not-leak")
        record(identity, operation, *args, **kwargs)
    monkeypatch.setattr(runtime.audit, "record", failed_issue)
    now[0] += 6
    with pytest.raises(AuthenticationError):
        runtime.current_identity()
    assert reader.calls == 2 and runtime._process_identity is None
    assert not set(runtime._sessions) - existing
    assert "private-audit-fixture" not in runtime.audit.path.read_text()
