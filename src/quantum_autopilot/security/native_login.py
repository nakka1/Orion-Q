"""Revalidate the bound native OS identity without changing process authority."""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import os
from typing import TYPE_CHECKING

from .identity import AuthenticationError, AuthorizationError, Principal, operating_system_identity

if TYPE_CHECKING:
    from .runtime import SecurityRuntime


_REJECTED = "native authentication rejected"
_LOGON_NETWORK = 3
_PROVIDER_DEFAULT = 0


def _windows_api():
    if os.name != "nt":
        raise AuthenticationError(_REJECTED)
    advapi = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    advapi.LogonUserW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.LPCWSTR,
                                  wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
    advapi.LogonUserW.restype = wintypes.BOOL
    advapi.GetTokenInformation.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p,
                                           wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    advapi.GetTokenInformation.restype = wintypes.BOOL
    advapi.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.LPWSTR)]
    advapi.ConvertSidToStringSidW.restype = wintypes.BOOL
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    return advapi, kernel


def _account_parts(username: str) -> tuple[str, str | None]:
    if not isinstance(username, str) or not 1 <= len(username.strip()) <= 512:
        raise AuthenticationError(_REJECTED)
    account = username.strip()
    if any(character in account for character in "\x00\r\n"):
        raise AuthenticationError(_REJECTED)
    if "\\" in account:
        if account.count("\\") != 1 or "@" in account:
            raise AuthenticationError(_REJECTED)
        domain, account = account.split("\\", 1)
        if not domain or not account:
            raise AuthenticationError(_REJECTED)
        return account, domain
    if "@" in account:
        if account.count("@") != 1 or not all(account.split("@")):
            raise AuthenticationError(_REJECTED)
        return account, None
    return account, "."


def _token_subject(advapi, kernel, token) -> str:
    required = wintypes.DWORD()
    advapi.GetTokenInformation(token, 1, None, 0, ctypes.byref(required))
    if not ctypes.sizeof(ctypes.c_void_p) <= required.value <= 65536:
        raise AuthenticationError(_REJECTED)
    buffer = ctypes.create_string_buffer(required.value)
    if not advapi.GetTokenInformation(token, 1, buffer, len(buffer), ctypes.byref(required)):
        raise AuthenticationError(_REJECTED)
    sid = ctypes.cast(buffer, ctypes.POINTER(ctypes.c_void_p))[0]
    sid_text = wintypes.LPWSTR()
    try:
        if not sid or not advapi.ConvertSidToStringSidW(sid, ctypes.byref(sid_text)):
            raise AuthenticationError(_REJECTED)
        subject = sid_text.value
        if not subject or not subject.startswith("S-"):
            raise AuthenticationError(_REJECTED)
        return "os:sid:" + subject
    finally:
        if sid_text:
            kernel.LocalFree(ctypes.cast(sid_text, ctypes.c_void_p))


def _verify_windows_password(username: str, password: str) -> str:
    account, domain = _account_parts(username)
    if not isinstance(password, str) or not 1 <= len(password) <= 2048 or "\x00" in password:
        raise AuthenticationError(_REJECTED)
    advapi, kernel = _windows_api()
    token = wintypes.HANDLE()
    password_buffer = ctypes.create_unicode_buffer(password)
    try:
        # NETWORK checks credentials without caching them. The returned token is
        # queried only for its SID: it is never used to impersonate or launch.
        if not advapi.LogonUserW(account, domain, password_buffer, _LOGON_NETWORK,
                                _PROVIDER_DEFAULT, ctypes.byref(token)) or not token.value:
            raise AuthenticationError(_REJECTED)
        return _token_subject(advapi, kernel, token)
    except (OSError, ValueError, ctypes.ArgumentError):
        raise AuthenticationError(_REJECTED) from None
    finally:
        ctypes.memset(ctypes.addressof(password_buffer), 0, ctypes.sizeof(password_buffer))
        if token.value:
            kernel.CloseHandle(token)


def authenticate_native_login(runtime: SecurityRuntime, expected_principal: Principal,
                              username: str, password: str | None = None,
                              resource: str = "workspace") -> Principal:
    """Verify the existing OS-bound principal and freshly authorize workspace read.

    ``None`` selects the already authenticated local OS session. A supplied
    password verifies that same Windows SID; it cannot select another account,
    add roles, revive a session, or replace an institutional identity.
    """
    try:
        if not isinstance(expected_principal, Principal) or expected_principal.source != "os":
            raise AuthenticationError(_REJECTED)
        bound = runtime.authorize("network.read", resource, expected_principal)
        current_subject, _ = operating_system_identity()
        if bound.source != "os" or bound.subject != current_subject:
            raise AuthenticationError(_REJECTED)
        if password is None:
            if (not isinstance(username, str) or not bound.display_name
                    or username.strip().casefold() != bound.display_name.casefold()):
                raise AuthenticationError(_REJECTED)
        elif _verify_windows_password(username, password) != bound.subject:
            raise AuthenticationError(_REJECTED)
        if operating_system_identity()[0] != bound.subject:
            raise AuthenticationError(_REJECTED)
        # Recheck after the native call because revocation or policy changes may
        # happen while Windows is verifying the password on the worker thread.
        verified = runtime.authorize("network.read", resource, bound)
        runtime.audit.record(verified, "native.login", resource, "allowed")
        return verified
    except (AuthenticationError, AuthorizationError, OSError):
        actor = expected_principal if isinstance(expected_principal, Principal) else "anonymous"
        runtime.audit.record(actor, "native.login", resource, "denied",
                             {"reason": "authentication_failed"})
        raise AuthenticationError(_REJECTED) from None


__all__ = ["authenticate_native_login"]
