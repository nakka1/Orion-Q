"""Security controls are exercised without QKD hardware or a live identity provider."""
from __future__ import annotations

import base64
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import json
import os
import time

import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec, padding, rsa, utils

from quantum_autopilot.security import (AuditIntegrityError, AuthenticationError, AuthorizationError,
    OIDCConfig, OIDCVerifier, Principal, SecretVault, SecurityRuntime, VaultError)
from quantum_autopilot.security.identity import operating_system_identity


@pytest.fixture
def runtime(tmp_path):
    return SecurityRuntime(tmp_path / "security")


def _b64(value: bytes):
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode()


def _number(value):
    return _b64(value.to_bytes((value.bit_length() + 7) // 8, "big"))


@pytest.fixture(scope="module")
def oidc_keys():
    rsa_key = rsa.generate_private_key(65537, 2048)
    ec_key = ec.generate_private_key(ec.SECP256R1())
    rsa_numbers = rsa_key.public_key().public_numbers()
    ec_numbers = ec_key.public_key().public_numbers()
    jwks = {"keys": [{"kty": "RSA", "kid": "rsa-v1", "use": "sig", "alg": "RS256",
                       "n": _number(rsa_numbers.n), "e": _number(rsa_numbers.e)},
                     {"kty": "EC", "crv": "P-256", "kid": "ec-v1", "use": "sig", "alg": "ES256",
                       "x": _number(ec_numbers.x), "y": _number(ec_numbers.y)}]}
    return rsa_key, ec_key, jwks


def _jwt(oidc_keys, *, claims=None, header=None, algorithm="RS256"):
    now = time.time()
    fields = {"iss": "https://identity.example.test", "aud": "orion-api", "sub": "researcher-1",
              "iat": now - 2, "nbf": now - 2, "exp": now + 300,
              "client_id": "orion-institutional", "jti": "assertion-1"}
    fields.update(claims or {})
    jose = {"alg": algorithm, "typ": "at+jwt", "kid": "rsa-v1" if algorithm == "RS256" else "ec-v1"}
    jose.update(header or {})
    segments = [_b64(json.dumps(jose).encode()), _b64(json.dumps(fields).encode())]
    signed = ".".join(segments).encode()
    if algorithm == "RS256":
        signature = oidc_keys[0].sign(signed, padding.PKCS1v15(), hashes.SHA256())
    else:
        der = oidc_keys[1].sign(signed, ec.ECDSA(hashes.SHA256()))
        r, s = utils.decode_dss_signature(der)
        signature = r.to_bytes(32, "big") + s.to_bytes(32, "big")
    return ".".join(segments + [_b64(signature)])


def _verifier(oidc_keys):
    return OIDCVerifier(OIDCConfig("https://identity.example.test", "orion-api", oidc_keys[2]))


def test_local_identity_is_real_os_identity(runtime, monkeypatch):
    original = operating_system_identity()[0]
    monkeypatch.setenv("USERNAME", "forged-admin")
    assert operating_system_identity()[0] == original
    assert runtime.local_identity().subject == original
    assert original.startswith("os:sid:S-") if os.name == "nt" else original.startswith("os:uid:")


def test_explicit_anonymous_scope_denies_instead_of_os_fallback(runtime):
    with runtime.identity_scope(None), pytest.raises(AuthenticationError):
        runtime.authorize("network.read", "network:all")
    assert runtime.authorize("network.read", "network:all").authenticated


@pytest.mark.parametrize("role,allowed,forbidden", [
    ("operator", "adapters.acquire", "security.manage"),
    ("researcher", "simulation.run", "adapters.acquire"),
    ("auditor", "audit.read", "network.configure"),
    ("consumer", "keys.consume", "network.read"),
])
def test_roles_and_resource_scopes_enforced(runtime, role, allowed, forbidden):
    principal = runtime.grant_identity("service:test", [role], ["resource:permitted*"])
    with runtime.identity_scope(principal):
        assert runtime.authorize(allowed, "resource:permitted-one").subject == principal.subject
        with pytest.raises(AuthorizationError):
            runtime.authorize(forbidden, "resource:permitted-one")
        with pytest.raises(AuthorizationError):
            runtime.authorize(allowed, "resource:other")


def test_payload_principal_cannot_grant_itself_authority(runtime):
    with pytest.raises(AuthenticationError):
        runtime.identity_scope(Principal(runtime.local_identity().subject, roles=frozenset({"administrator"}),
                                         scopes=("*",), source="os")).__enter__()
    researcher = runtime.grant_identity("service:research", ["researcher"], ["*"])
    forged_roles = replace(researcher, roles=frozenset({"administrator"}))
    with runtime.identity_scope(forged_roles), pytest.raises(AuthorizationError):
        runtime.authorize("security.manage", "security:identities")


def test_policy_management_is_admin_only(runtime):
    operator = runtime.grant_identity("service:operator", ["operator"], ["*"])
    with runtime.identity_scope(operator), pytest.raises(AuthorizationError):
        runtime.grant_identity("service:evil", ["administrator"], ["*"])


def test_role_changes_rechecked_on_existing_principal(runtime):
    operator = runtime.grant_identity("service:operator", ["operator"], ["*"])
    runtime.grant_identity("service:operator", ["auditor"], ["*"])
    with runtime.identity_scope(operator):
        with pytest.raises(AuthorizationError):
            runtime.authorize("adapters.acquire", "device:test")
        assert runtime.authorize("audit.read", "audit:all").roles == frozenset({"auditor"})


def test_identity_revocation_rechecked_with_open_scope(runtime):
    principal = runtime.grant_identity("service:device", ["operator"], ["*"])
    with runtime.identity_scope(principal):
        runtime.authorize("adapters.acquire", "device:test")
    runtime.revoke_identity(principal.subject)
    with pytest.raises(AuthenticationError):
        runtime.authorize("adapters.acquire", "device:test", principal)
    assert SecurityRuntime(runtime.root).local_identity().authenticated
    with pytest.raises(AuthenticationError):
        SecurityRuntime(runtime.root).principal(principal.subject)


def test_local_bearer_is_opaque_revocable_and_restart_invalidates(runtime):
    token = runtime.issue_local_session()
    assert token.count(".") == 0
    principal = runtime.session(token)
    runtime.revoke_session(token)
    with pytest.raises(AuthenticationError):
        runtime.session(token)
    with pytest.raises(AuthenticationError):
        runtime.authorize("network.read", "network:all", principal)
    with pytest.raises(AuthenticationError):
        SecurityRuntime(runtime.root).session(token)


def test_session_expiration_and_grant_change_fail_closed(runtime):
    token = runtime.issue_local_session()
    principal = runtime.session(token)
    runtime.grant_identity(principal.subject, ["administrator"], ["*"], source="os")
    with pytest.raises(AuthenticationError):
        runtime.session(token)
    token2 = runtime.issue_local_session()
    sid = runtime.session(token2).session_id
    runtime._sessions[sid]["expires"] = time.time() - 1
    with pytest.raises(AuthenticationError):
        runtime.session(token2)


def test_existing_security_root_cannot_bootstrap_a_different_owner(runtime, monkeypatch):
    import quantum_autopilot.security.runtime as runtime_module
    monkeypatch.setattr(runtime_module, "operating_system_identity", lambda: ("os:uid:99999", "other"))
    other = SecurityRuntime(runtime.root)
    with pytest.raises(AuthenticationError):
        other.local_identity()


@pytest.mark.parametrize("algorithm", ["RS256", "ES256"])
def test_rfc9068_signature_is_verified(oidc_keys, algorithm):
    assert _verifier(oidc_keys).verify(_jwt(oidc_keys, algorithm=algorithm))["sub"] == "researcher-1"


@pytest.mark.parametrize("claims,header", [
    ({"iss": "https://attacker.example.test"}, {}),
    ({"aud": "other-api"}, {}),
    ({"exp": "now-100"}, {}),
    ({"nbf": "now+100"}, {}),
    ({"iat": "now+100"}, {}),
    ({"exp": "now+9000"}, {}),
    ({"jti": None}, {}),
    ({"client_id": None}, {}),
    ({"exp": True}, {}),
    ({}, {"alg": "none"}),
    ({}, {"alg": "HS256"}),
    ({}, {"typ": "JWT"}),
    ({}, {"jku": "https://attacker.example.test/jwks"}),
    ({}, {"jwk": {"kty": "RSA"}}),
    ({}, {"kid": "missing-key"}),
])
def test_oidc_rejects_wrong_signature_profile_identity_and_times(oidc_keys, claims, header):
    # Evaluate relative times at execution, not during collection: a long
    # scientific suite must not turn a future-issued token into a valid one.
    now = time.time()
    claims = {key: now + float(value[3:]) if isinstance(value, str) and value.startswith("now") else value
              for key, value in claims.items()}
    with pytest.raises(AuthenticationError):
        _verifier(oidc_keys).verify(_jwt(oidc_keys, claims=claims, header=header))


def test_oidc_tampering_and_untrusted_roles_rejected(runtime, oidc_keys):
    token = _jwt(oidc_keys, claims={"roles": ["administrator"]})
    parts = token.split(".")
    payload = json.loads(base64.urlsafe_b64decode(parts[1] + "=" * (-len(parts[1]) % 4)))
    payload["sub"] = "administrator-1"
    parts[1] = _b64(json.dumps(payload).encode())
    with pytest.raises(AuthenticationError):
        _verifier(oidc_keys).verify(".".join(parts))
    runtime.oidc = _verifier(oidc_keys)
    principal = runtime.grant_identity("oidc:https://identity.example.test:researcher-1",
                                       ["researcher"], ["*"], source="oidc")
    authenticated = runtime.authenticate_oidc(token)
    assert authenticated.roles == principal.roles == frozenset({"researcher"})
    with runtime.identity_scope(authenticated), pytest.raises(AuthorizationError):
        runtime.authorize("security.manage", "security:identities")
    runtime.revoke_identity(principal.subject)
    with pytest.raises(AuthenticationError):
        runtime.authenticate_oidc(token)


def test_oidc_accepts_optional_nbf_and_requires_pinned_https(oidc_keys):
    now = time.time()
    token = _jwt(oidc_keys)
    claims = _verifier(oidc_keys).verify(token)
    assert claims["exp"] > now
    with pytest.raises(ValueError):
        OIDCConfig("http://identity.example.test", "orion-api", oidc_keys[2])
    with pytest.raises(ValueError):
        OIDCConfig("https://identity.example.test", "orion-api", oidc_keys[2], algorithms=("none",))


def test_vault_secret_is_never_plaintext_and_tampering_blocks(runtime):
    material = b"unique-secret-payload-for-test-32"
    runtime.vault.put_bytes("fixture", material)
    assert runtime.vault.get_bytes("fixture") == material
    if os.name == "nt":
        assert material not in runtime.vault.path.read_bytes()
        assert _b64(material).encode() not in runtime.vault.path.read_bytes()
        envelope = json.loads(runtime.vault.path.read_bytes())
        protected = bytearray(base64.b64decode(envelope["protected"]))
        protected[len(protected) // 2] ^= 1
        envelope["protected"] = base64.b64encode(protected).decode()
        runtime.vault.path.write_text(json.dumps(envelope))
        with pytest.raises(VaultError):
            runtime.vault.get_bytes("fixture")
    else:
        assert runtime.vault.path.stat().st_mode & 0o077 == 0
        runtime.vault.path.chmod(0o644)
        with pytest.raises(VaultError):
            runtime.vault.get_bytes("fixture")


def test_vault_rotation_preserves_old_material_and_requires_admin(runtime):
    original = runtime.vault.get_or_create("kek-original")
    first = runtime.rotate_secret("kek")
    first_value = runtime.vault.get_bytes(first)
    second = runtime.rotate_secret("kek")
    assert first != second
    assert runtime.vault.get_bytes(first) == first_value
    assert runtime.vault.get_bytes("kek:active").decode() == second
    assert runtime.vault.get_bytes("kek-original") == original
    observer = runtime.grant_identity("service:observer", ["auditor"], ["*"])
    with runtime.identity_scope(observer), pytest.raises(AuthorizationError):
        runtime.rotate_secret("kek")
    with runtime.identity_scope(observer), pytest.raises(AuthorizationError):
        runtime.recover_secret("kek", b"wrong")


def test_vault_profile_and_absent_reference_are_explicit(tmp_path):
    vault = SecretVault(tmp_path / "vault.json")
    assert not vault.contains("missing")
    vault.put_bytes("present", b"payload")
    assert vault.contains("present")
    if os.name != "nt":
        with pytest.raises(VaultError):
            SecretVault(tmp_path / "operational.json", profile="operational")


def test_authenticated_audit_detects_alteration(runtime):
    runtime.authorize("network.read", "network:all")
    rows = runtime.audit.path.read_text().splitlines()
    row = json.loads(rows[0])
    row["result"] = "denied"
    runtime.audit.path.write_text(json.dumps(row) + "\n")
    with pytest.raises(AuditIntegrityError):
        runtime.audit.verify()
    with pytest.raises(AuditIntegrityError):
        runtime.authorize("network.read", "network:all")


def test_old_audit_backup_cannot_replace_protected_checkpoint(runtime):
    runtime.authorize("network.read", "network:all")
    backup = runtime.audit.path.read_bytes()
    runtime.authorize("network.configure", "network:all")
    runtime.audit.path.write_bytes(backup)
    with pytest.raises(AuditIntegrityError, match="truncated|restored"):
        runtime.audit.verify()
    with pytest.raises(AuditIntegrityError):
        SecurityRuntime(runtime.root)


def test_audit_crash_after_append_before_checkpoint_recovers_without_duplication(runtime, monkeypatch):
    original = runtime.vault.put_bytes
    def crash(name, value):
        if name.endswith(":checkpoint"):
            raise OSError("interrupted protected checkpoint update")
        original(name, value)
    monkeypatch.setattr(runtime.vault, "put_bytes", crash)
    with pytest.raises(OSError):
        runtime.audit.record(runtime.local_identity(), "network.read", "network:all", "success")
    assert runtime.audit.verify()["uncheckpointed_records"] == 1
    monkeypatch.setattr(runtime.vault, "put_bytes", original)
    runtime.audit.record(runtime.local_identity(), "network.read", "network:all", "success")
    status = runtime.audit.verify()
    assert status["count"] == 2 and status["uncheckpointed_records"] == 0


def test_audit_partial_record_and_missing_checkpoint_fail_closed(runtime):
    runtime.authorize("network.read", "network:all")
    runtime.audit.path.write_bytes(runtime.audit.path.read_bytes() + b'{"incomplete":')
    with pytest.raises(AuditIntegrityError):
        runtime.audit.verify()


def test_audit_drops_secrets_request_bodies_and_free_text(runtime):
    token = runtime.issue_local_session()
    secret = "SUPER-SECRET-PRIVATE-KEY-12345"
    runtime.audit.record(runtime.local_identity(), "adapter.configure", "device:tdc", "success", {
        "token": token, "secret": secret, "request_body": {"private_key": secret},
        "error": f"header Bearer {token}", "count": 3, "configuration_sha256": "a" * 64})
    content = runtime.audit.path.read_text()
    assert token not in content and secret not in content and "Bearer" not in content
    details = json.loads(content.splitlines()[-1])["details"]
    assert details["count"] == 3 and details["redacted_fields"] == 4
    assert runtime.audit.verify()["valid"]


def test_parallel_audit_calls_are_serialized_and_authenticated(runtime):
    def write(index):
        runtime.authorize("network.read", f"network:{index}")
    with ThreadPoolExecutor(max_workers=6) as pool:
        list(pool.map(write, range(24)))
    assert runtime.audit.verify()["count"] == 24


def test_context_is_restored_after_exception(runtime):
    principal = runtime.local_identity()
    with pytest.raises(RuntimeError):
        with runtime.identity_scope(None):
            raise RuntimeError("fixture")
    assert runtime.current_identity().subject == principal.subject


def test_device_session_requires_deliberate_reconnect_after_revocation(runtime):
    principal = runtime.device_session_identity()
    assert principal.session_id == runtime.device_session_identity().session_id
    runtime.revoke_session(runtime._device_session_token)
    with pytest.raises(AuthenticationError):
        runtime.device_session_identity()
    assert runtime.device_session_identity(new_session=True).session_id != principal.session_id


def test_service_sessions_bind_roles_and_cannot_issue_os_sessions(runtime):
    service = runtime.grant_identity("service:consumer", ["consumer"], ["keys:kme-alice"])
    token = runtime.issue_service_session(service.subject)
    principal = runtime.session(token)
    with runtime.identity_scope(principal):
        runtime.authorize("keys.consume", "keys:kme-alice")
        with pytest.raises(AuthorizationError):
            runtime.issue_local_session()
        with pytest.raises(AuthorizationError):
            runtime.issue_service_session(service.subject)
    with runtime.identity_scope(None), pytest.raises(AuthenticationError):
        runtime.issue_local_session()


def test_unknown_read_operation_is_denied_by_default(runtime):
    observer = runtime.grant_identity("service:observer", ["auditor"], ["*"])
    with runtime.identity_scope(observer), pytest.raises(AuthorizationError):
        runtime.authorize("unregistered-secret.read", "vault:secrets")


def test_missing_checkpoint_for_existing_log_is_never_reset(runtime):
    runtime.authorize("network.read", "network:all")
    with runtime.vault.transaction() as rows:
        del rows["audit-v1:checkpoint"]
    with pytest.raises(AuditIntegrityError):
        SecurityRuntime(runtime.root)


def test_audit_batch_preserves_every_authorization_and_one_checkpoint(runtime, monkeypatch):
    checkpoints = []
    original = runtime.vault.put_bytes
    def put(name, value):
        if name.endswith(":checkpoint"):
            checkpoints.append(name)
        original(name, value)
    monkeypatch.setattr(runtime.vault, "put_bytes", put)
    with runtime.audit.batch():
        for index in range(24):
            runtime.authorize("network.read", f"network:{index}")
        with runtime.audit.batch():
            runtime.authorize("network.read", "network:nested")
        assert not runtime.audit.path.exists()
    assert len(checkpoints) == 1
    assert runtime.audit.verify()["count"] == 25
    rows = [json.loads(raw) for raw in runtime.audit.path.read_text().splitlines()]
    assert [row["resource"] for row in rows[:-1]] == [f"network:{i}" for i in range(24)]
    assert rows[-1]["resource"] == "network:nested"


def test_audit_batch_persists_denial_before_exception_propagates(runtime):
    with pytest.raises(AuthenticationError):
        with runtime.audit.batch(), runtime.identity_scope(None):
            runtime.authorize("network.read", "network:all")
    row = json.loads(runtime.audit.path.read_text())
    assert row["result"] == "denied" and runtime.audit.verify()["count"] == 1


def test_batch_failure_blocks_completion_and_keeps_recoverable_authentic_tail(runtime, monkeypatch):
    original = runtime.vault.put_bytes
    monkeypatch.setattr(runtime.vault, "put_bytes", lambda *_: (_ for _ in ()).throw(OSError("checkpoint fault")))
    with pytest.raises(OSError):
        with runtime.audit.batch():
            runtime.authorize("network.read", "network:one")
            runtime.authorize("network.read", "network:two")
    assert runtime.audit.verify()["uncheckpointed_records"] == 2
    monkeypatch.setattr(runtime.vault, "put_bytes", original)
    runtime.authorize("network.read", "network:three")
    assert runtime.audit.verify()["count"] == 3
    assert runtime.audit.verify()["uncheckpointed_records"] == 0


@pytest.mark.parametrize("expiry", [float("nan"), float("inf"), float("-inf"), True, "never"])
def test_nonfinite_or_wrong_expiry_is_rejected_before_grant(runtime, expiry):
    with pytest.raises(ValueError):
        runtime.grant_identity("service:invalid-expiry", ["operator"], ["*"], expires_at=expiry)
    with pytest.raises(AuthenticationError):
        runtime.principal("service:invalid-expiry")


@pytest.mark.parametrize("field,value", [("expires_at", float("nan")), ("expires_at", float("inf")),
    ("generation", True), ("revoked", "false"), ("roles", None), ("source", "payload")])
def test_invalid_persisted_identity_policy_fails_closed(runtime, field, value):
    policy = json.loads(runtime.vault.get_bytes("identity-policy-v1"))
    row = policy["identities"][policy["owner"]]
    row[field] = value
    runtime.vault.put_bytes("identity-policy-v1", json.dumps(policy).encode())
    with pytest.raises(AuthenticationError):
        SecurityRuntime(runtime.root)
