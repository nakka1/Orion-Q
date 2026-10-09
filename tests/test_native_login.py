"""Native login checks use synthetic identities and mocked Windows APIs."""
from __future__ import annotations

import ctypes
from ctypes import wintypes
from dataclasses import replace
import json
from types import SimpleNamespace

import pytest

from quantum_autopilot.security import AuthenticationError, Principal, SecurityRuntime
from quantum_autopilot.security import native_login
from quantum_autopilot.security import runtime as runtime_module


SUBJECT = "os:sid:S-1-5-21-987654321-123456789-111111111-1001"
RESOURCE = "workspace:native-unit"
PASSWORD = "unit-test-only-not-a-secret"


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    def identity():
        return SUBJECT, "unit-account"

    monkeypatch.setattr(runtime_module, "operating_system_identity", identity)
    monkeypatch.setattr(native_login, "operating_system_identity", identity)
    return SecurityRuntime(tmp_path / "security")


def login(runtime, principal=None, username="unit-account", password=None, resource=RESOURCE):
    return native_login.authenticate_native_login(
        runtime, principal or runtime.local_identity(), username, password, resource)


def test_existing_os_session_requires_the_bound_user_and_fresh_grants(runtime, monkeypatch):
    expected = runtime.local_identity()
    runtime.grant_identity(SUBJECT, ["researcher"], ["workspace:*"],
                           display_name="unit-account", source="os")
    monkeypatch.setattr(native_login, "_verify_windows_password",
                        lambda *_: pytest.fail("session reuse must not submit a password"))
    verified = login(runtime, expected, username=" UNIT-ACCOUNT ")
    assert verified.subject == SUBJECT
    assert verified.roles == frozenset({"researcher"})
    assert verified.scopes == ("workspace:*",)
    assert verified.session_id == expected.session_id
    with pytest.raises(AuthenticationError, match="native authentication rejected"):
        login(runtime, expected, username="different-account")


def test_password_verifies_same_sid_without_provisioning_or_new_session(runtime, monkeypatch):
    token = runtime.issue_local_session()
    expected = runtime.session(token)
    seen = []
    monkeypatch.setattr(native_login, "_verify_windows_password",
                        lambda username, password: seen.append((username, password)) or SUBJECT)
    verified = login(runtime, expected, username="DOMAIN\\unit-account", password=PASSWORD)
    assert seen == [("DOMAIN\\unit-account", PASSWORD)]
    assert verified == expected
    assert verified.session_id == expected.session_id
    assert len(runtime._sessions) == 1


@pytest.mark.parametrize("source", ["service", "oidc", "certificate", "anonymous"])
def test_non_os_principal_cannot_be_replaced_by_password_login(runtime, monkeypatch, source):
    expected = Principal(SUBJECT, "unit-account", source=source)
    monkeypatch.setattr(native_login, "_verify_windows_password",
                        lambda *_: pytest.fail("non-OS identity must be rejected first"))
    with pytest.raises(AuthenticationError):
        login(runtime, expected, password=PASSWORD)


def test_forged_principal_is_not_resolved_into_a_local_administrator(runtime, monkeypatch):
    forged = replace(runtime.local_identity(), _proof="forged")
    monkeypatch.setattr(native_login, "_verify_windows_password",
                        lambda *_: pytest.fail("invalid principal must be rejected first"))
    with pytest.raises(AuthenticationError):
        login(runtime, forged, password=PASSWORD)


def test_password_for_another_os_account_is_rejected_without_secret_audit(runtime, monkeypatch):
    monkeypatch.setattr(native_login, "_verify_windows_password", lambda *_: "os:sid:S-1-5-21-999-1001")
    with pytest.raises(AuthenticationError) as rejected:
        login(runtime, username="different-account", password=PASSWORD)
    assert PASSWORD not in str(rejected.value)
    audit_text = (runtime.root / "audit.jsonl").read_text(encoding="utf-8")
    assert PASSWORD not in audit_text
    assert "different-account" not in audit_text
    record = json.loads(audit_text.splitlines()[-1])
    assert record["operation"] == "native.login"
    assert record["result"] == "denied"
    assert record["details"] == {"reason": "authentication_failed"}


@pytest.mark.parametrize("password", [None, PASSWORD])
def test_current_process_os_identity_must_match_bound_principal(runtime, monkeypatch, password):
    expected = runtime.local_identity()
    monkeypatch.setattr(native_login, "operating_system_identity", lambda: ("os:sid:S-1-5-21-999", "other"))
    monkeypatch.setattr(native_login, "_verify_windows_password",
                        lambda *_: pytest.fail("process mismatch must be rejected first"))
    with pytest.raises(AuthenticationError):
        login(runtime, expected, password=password)


def test_revoked_identity_and_resource_scope_fail_before_password_verification(runtime, monkeypatch):
    expected = runtime.local_identity()
    runtime.grant_identity(SUBJECT, ["researcher"], ["workspace:allowed"],
                           display_name="unit-account", source="os")
    monkeypatch.setattr(native_login, "_verify_windows_password",
                        lambda *_: pytest.fail("resource denial must precede password verification"))
    with pytest.raises(AuthenticationError):
        login(runtime, expected, password=PASSWORD)


def test_revoked_identity_cannot_reuse_existing_os_session(runtime):
    expected = runtime.local_identity()
    runtime.revoke_identity(SUBJECT)
    with pytest.raises(AuthenticationError):
        login(runtime, expected)


def test_revocation_while_windows_is_verifying_password_fails_closed(runtime, monkeypatch):
    token = runtime.issue_local_session()
    expected = runtime.session(token)

    def verify(*_):
        runtime.revoke_session(token)
        return SUBJECT

    monkeypatch.setattr(native_login, "_verify_windows_password", verify)
    with pytest.raises(AuthenticationError):
        login(runtime, expected, password=PASSWORD)


def test_live_role_change_during_verification_is_revalidated(runtime, monkeypatch):
    expected = runtime.local_identity()

    def verify(*_):
        runtime.grant_identity(SUBJECT, ["researcher"], ["workspace:*"],
                               display_name="unit-account", source="os")
        return SUBJECT

    monkeypatch.setattr(native_login, "_verify_windows_password", verify)
    assert login(runtime, expected, password=PASSWORD).roles == frozenset({"researcher"})


def test_process_identity_change_during_verification_fails_closed(runtime, monkeypatch):
    identities = iter([(SUBJECT, "unit-account"), ("os:sid:S-1-5-21-999", "other")])
    monkeypatch.setattr(native_login, "operating_system_identity", lambda: next(identities))
    monkeypatch.setattr(native_login, "_verify_windows_password", lambda *_: SUBJECT)
    with pytest.raises(AuthenticationError):
        login(runtime, password=PASSWORD)


@pytest.mark.parametrize("username,expected", [
    ("unit-account", ("unit-account", ".")),
    ("DOMAIN\\unit-account", ("unit-account", "DOMAIN")),
    ("unit-account@example.test", ("unit-account@example.test", None)),
])
def test_windows_account_syntax_uses_the_correct_domain(username, expected):
    assert native_login._account_parts(username) == expected


@pytest.mark.parametrize("username", [None, "", " ", "bad\x00user", "bad\nuser", "\\user",
                                      "DOMAIN\\", "A\\B\\C", "@domain", "user@", "a@b@c"])
def test_malformed_account_names_do_not_reach_windows(username):
    with pytest.raises(AuthenticationError):
        native_login._account_parts(username)


@pytest.mark.parametrize("accepted,token_failure", [(True, False), (False, False), (True, True)])
def test_native_token_is_closed_and_password_buffer_is_cleared(monkeypatch, accepted, token_failure):
    calls = []
    buffers = []
    closed = []

    def logon(account, domain, buffer, logon_type, provider, token):
        buffers.append(buffer)
        calls.append((account, domain, logon_type, provider, buffer.value))
        token._obj.value = 777
        return accepted

    fake_advapi = SimpleNamespace(LogonUserW=logon)
    fake_kernel = SimpleNamespace(CloseHandle=lambda token: closed.append(token.value) or True)
    monkeypatch.setattr(native_login, "_windows_api", lambda: (fake_advapi, fake_kernel))

    def token_subject(*_):
        if token_failure:
            raise AuthenticationError("token unavailable")
        return SUBJECT

    monkeypatch.setattr(native_login, "_token_subject", token_subject)
    if accepted and not token_failure:
        assert native_login._verify_windows_password("DOMAIN\\unit-account", PASSWORD) == SUBJECT
    else:
        with pytest.raises(AuthenticationError):
            native_login._verify_windows_password("DOMAIN\\unit-account", PASSWORD)
    assert calls == [("unit-account", "DOMAIN", 3, 0, PASSWORD)]
    assert closed == [777]
    assert bytes(buffers[0]) == b"\x00" * ctypes.sizeof(buffers[0])


def test_token_sid_is_read_and_allocated_sid_string_is_freed(monkeypatch):
    sid_buffer = ctypes.create_unicode_buffer(SUBJECT.removeprefix("os:sid:"))
    freed = []

    def token_information(token, information_class, buffer, size, required):
        assert token.value == 777 and information_class == 1
        required._obj.value = ctypes.sizeof(ctypes.c_void_p) + 8
        if buffer is None:
            assert size == 0
            return False
        ctypes.cast(buffer, ctypes.POINTER(ctypes.c_void_p))[0] = 1234
        return True

    def convert_sid(sid, text):
        assert sid == 1234
        text._obj.value = ctypes.cast(sid_buffer, wintypes.LPWSTR).value
        return True

    advapi = SimpleNamespace(GetTokenInformation=token_information, ConvertSidToStringSidW=convert_sid)
    kernel = SimpleNamespace(LocalFree=lambda pointer: freed.append(pointer.value))
    assert native_login._token_subject(advapi, kernel, wintypes.HANDLE(777)) == SUBJECT
    assert len(freed) == 1 and freed[0]


@pytest.mark.parametrize("password", ["", None, "has\x00nul", 123])
def test_invalid_password_values_do_not_reach_windows(monkeypatch, password):
    monkeypatch.setattr(native_login, "_windows_api", lambda: pytest.fail("invalid password reached Windows"))
    with pytest.raises(AuthenticationError):
        native_login._verify_windows_password("unit-account", password)
