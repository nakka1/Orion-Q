"""Authenticated identities; no authority is accepted from UI/request roles."""
from __future__ import annotations

import base64
import ctypes
from ctypes import wintypes
from dataclasses import dataclass, field
import getpass
import json
import os
import time


class AuthenticationError(PermissionError):
    """Deliberately contains no credentials or token content."""


class AuthorizationError(PermissionError):
    pass


@dataclass(frozen=True)
class Principal:
    subject: str
    display_name: str = ""
    roles: frozenset[str] = field(default_factory=frozenset)
    scopes: tuple[str, ...] = ()
    source: str = "anonymous"
    session_id: str | None = None
    expires_at: float | None = None
    _proof: str = field(default="", repr=False, compare=False)

    @property
    def authenticated(self) -> bool:
        return self.source != "anonymous" and bool(self._proof)


def operating_system_identity() -> tuple[str, str]:
    """Read the process token SID, never USERNAME supplied by the environment."""
    if os.name != "nt":
        import pwd
        uid = os.getuid()
        return f"os:uid:{uid}", pwd.getpwuid(uid).pw_name
    advapi = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    advapi.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
    advapi.GetTokenInformation.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p,
                                           wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    advapi.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.LPWSTR)]
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    token = wintypes.HANDLE()
    if not advapi.OpenProcessToken(kernel.GetCurrentProcess(), 0x0008, ctypes.byref(token)):
        raise AuthenticationError("operating system identity unavailable")
    try:
        required = wintypes.DWORD()
        advapi.GetTokenInformation(token, 1, None, 0, ctypes.byref(required))
        if not required.value or required.value > 65536:
            raise AuthenticationError("operating system identity unavailable")
        buffer = ctypes.create_string_buffer(required.value)
        if not advapi.GetTokenInformation(token, 1, buffer, len(buffer), ctypes.byref(required)):
            raise AuthenticationError("operating system identity unavailable")
        sid = ctypes.cast(buffer, ctypes.POINTER(ctypes.c_void_p))[0]
        sid_string = wintypes.LPWSTR()
        if not advapi.ConvertSidToStringSidW(sid, ctypes.byref(sid_string)):
            raise AuthenticationError("operating system identity unavailable")
        try:
            return "os:sid:" + sid_string.value, getpass.getuser()
        finally:
            kernel.LocalFree(ctypes.cast(sid_string, ctypes.c_void_p))
    finally:
        kernel.CloseHandle(token)


def _decode(value: str) -> bytes:
    if not isinstance(value, str) or not value or len(value) > 65536:
        raise AuthenticationError("invalid signed identity token")
    try:
        return base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True)
    except (ValueError, TypeError) as exc:
        raise AuthenticationError("invalid signed identity token") from exc


@dataclass(frozen=True)
class OIDCConfig:
    issuer: str
    audience: str
    jwks: dict
    algorithms: tuple[str, ...] = ("RS256", "ES256")
    clock_skew_seconds: int = 30
    max_token_lifetime_seconds: int = 3600

    def __post_init__(self):
        from urllib.parse import urlsplit
        parsed = urlsplit(self.issuer)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError("OIDC requires an explicitly configured HTTPS issuer")
        if parsed.query or parsed.fragment or not self.audience:
            raise ValueError("invalid OIDC issuer/audience")
        if not self.algorithms or set(self.algorithms) - {"RS256", "ES256"}:
            raise ValueError("unsupported OIDC signature algorithm")
        if not 0 <= self.clock_skew_seconds <= 60 or not 1 <= self.max_token_lifetime_seconds <= 86400:
            raise ValueError("invalid OIDC time limits")
        if not isinstance(self.jwks, dict) or not isinstance(self.jwks.get("keys"), list):
            raise ValueError("explicit trusted JWKS required")


class OIDCVerifier:
    """Configured JWKS only. Header-supplied keys/URLs are never fetched/trusted.

    This validates signed JWT bearer access assertions. It is not an OIDC browser
    authorization-code flow or a license to accept an ID token as an API token.
    Deployments must configure an API-specific issuer/audience and token profile.
    """
    def __init__(self, config: OIDCConfig):
        self.config = config

    def verify(self, token: str, *, now: float | None = None) -> dict:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import ec, padding, rsa, utils
        try:
            if not isinstance(token, str) or len(token) > 16384:
                raise ValueError()
            parts = token.split(".")
            if len(parts) != 3:
                raise ValueError()
            header, claims = json.loads(_decode(parts[0])), json.loads(_decode(parts[1]))
            if not isinstance(header, dict) or not isinstance(claims, dict):
                raise ValueError()
            if set(header) & {"crit", "jku", "jwk", "x5u", "x5c"}:
                raise ValueError()
            if header.get("typ") != "at+jwt":
                raise ValueError()
            algorithm, kid = header.get("alg"), header.get("kid")
            if algorithm not in self.config.algorithms or not isinstance(kid, str) or not kid:
                raise ValueError()
            keys = [row for row in self.config.jwks["keys"] if row.get("kid") == kid]
            if len(keys) != 1:
                raise ValueError()
            row = keys[0]
            if row.get("use", "sig") != "sig" or row.get("alg", algorithm) != algorithm:
                raise ValueError()
            if "key_ops" in row and "verify" not in row["key_ops"]:
                raise ValueError()
            signed, signature = ".".join(parts[:2]).encode("ascii"), _decode(parts[2])
            if algorithm == "RS256":
                if row.get("kty") != "RSA":
                    raise ValueError()
                key = rsa.RSAPublicNumbers(int.from_bytes(_decode(row["e"]), "big"),
                                          int.from_bytes(_decode(row["n"]), "big")).public_key()
                if key.key_size < 2048:
                    raise ValueError()
                key.verify(signature, signed, padding.PKCS1v15(), hashes.SHA256())
            else:
                if row.get("kty") != "EC" or row.get("crv") != "P-256" or len(signature) != 64:
                    raise ValueError()
                key = ec.EllipticCurvePublicNumbers(int.from_bytes(_decode(row["x"]), "big"),
                    int.from_bytes(_decode(row["y"]), "big"), ec.SECP256R1()).public_key()
                der = utils.encode_dss_signature(int.from_bytes(signature[:32], "big"),
                                                int.from_bytes(signature[32:], "big"))
                key.verify(der, signed, ec.ECDSA(hashes.SHA256()))
            current = time.time() if now is None else now
            skew = self.config.clock_skew_seconds
            if claims.get("iss") != self.config.issuer:
                raise ValueError()
            audiences = claims.get("aud")
            if not isinstance(audiences, (str, list)) or self.config.audience not in (
                    [audiences] if isinstance(audiences, str) else audiences):
                raise ValueError()
            if not isinstance(claims.get("sub"), str) or not 1 <= len(claims["sub"]) <= 256:
                raise ValueError()
            for name in ("client_id", "jti"):
                if not isinstance(claims.get(name), str) or not 1 <= len(claims[name]) <= 256:
                    raise ValueError()
            for name in ("exp", "iat"):
                if isinstance(claims.get(name), bool) or not isinstance(claims.get(name), (int, float)):
                    raise ValueError()
            not_before = claims.get("nbf", claims["iat"])
            if isinstance(not_before, bool) or not isinstance(not_before, (int, float)):
                raise ValueError()
            if not claims["iat"] <= not_before <= claims["exp"]:
                raise ValueError()
            if current >= claims["exp"] + skew or current + skew < not_before:
                raise ValueError()
            if claims["iat"] > current + skew or claims["exp"] - claims["iat"] > (
                    self.config.max_token_lifetime_seconds):
                raise ValueError()
            return claims
        except (AuthenticationError, InvalidSignature, ValueError, TypeError, KeyError,
                AttributeError, OverflowError, UnicodeError) as exc:
            raise AuthenticationError("signed identity assertion rejected") from exc
