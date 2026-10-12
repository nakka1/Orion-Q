"""Strict, non-secret deployment configuration for the portable core."""
from __future__ import annotations

from dataclasses import dataclass
import math
from pathlib import Path
import re
from typing import Any

from quantum_autopilot.security.http_boundary import HTTPPolicy, strict_object
from quantum_autopilot.security.identity import OIDCConfig
from quantum_autopilot.security.service_identity import service_credential_reference


def _fields(value: Any, required: set[str], optional: set[str] | None = None) -> dict:
    if not isinstance(value, dict) or not required <= set(value) or set(value) - required - (optional or set()):
        raise ValueError("invalid service configuration fields")
    return value


def _text(value: Any) -> str:
    if not isinstance(value, str) or not value or len(value) > 4096 or any(c in value for c in "\r\n\x00"):
        raise ValueError("invalid service configuration value")
    return value


def _path(value: Any) -> Path:
    result = Path(_text(value))
    if not result.is_absolute():
        raise ValueError("service paths must be absolute")
    # Do not resolve secret file symlinks here: the protected reader must see
    # and reject the configured path itself. Canonicalize resource roots only.
    return result


@dataclass(frozen=True)
class IdentitySettings:
    subject: str
    credential_file: Path

    @classmethod
    def parse(cls, value: dict) -> "IdentitySettings":
        _fields(value, {"subject", "credential_file"})
        subject = _text(value["subject"])
        service_credential_reference(subject)  # Validate before offline provisioning creates state.
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", subject):
            raise ValueError("deployment identity must be compatible with certificate bindings")
        return cls(subject, _path(value["credential_file"]))


def _oidc(value: Any) -> dict | None:
    if value is None:
        return None
    value = _fields(value, {"issuer", "audience", "jwks"},
                    {"algorithms", "clock_skew_seconds", "max_token_lifetime_seconds"})
    _text(value["issuer"])
    _text(value["audience"])
    if (not isinstance(value["jwks"], dict) or set(value["jwks"]) != {"keys"}
            or not isinstance(value["jwks"]["keys"], list)
            or any(not isinstance(key, dict) or set(key) & {"d", "p", "q", "dp", "dq", "qi", "oth", "k"}
                   for key in value["jwks"]["keys"])):
        raise ValueError("OIDC requires public trusted signing keys")
    algorithms = value.get("algorithms", ["RS256", "ES256"])
    if not isinstance(algorithms, (list, tuple)) or any(not isinstance(item, str) for item in algorithms):
        raise ValueError("invalid OIDC algorithms")
    for name in ("clock_skew_seconds", "max_token_lifetime_seconds"):
        if name in value and type(value[name]) is not int:
            raise ValueError("invalid OIDC time limits")
    OIDCConfig(**{**value, "algorithms": tuple(algorithms)})
    return dict(value)


@dataclass(frozen=True)
class CoreConfig:
    repository_root: Path
    state_root: Path
    identity: IdentitySettings
    vault: dict
    http: dict
    tls: dict
    provisioning_identity: IdentitySettings | None = None
    oidc: dict | None = None
    clock: dict | None = None
    resource_authority_root: Path | None = None

    @property
    def clock_settings(self) -> dict:
        return self.clock or {"enabled": True, "period_s": 1.0, "stop_timeout_s": 10.0}

    @property
    def security_root(self) -> Path:
        return self.state_root / "security"

    @property
    def journal(self) -> Path:
        return self.state_root / "workspace.sqlite"

    @property
    def http_policy(self) -> HTTPPolicy:
        return HTTPPolicy(**{key: tuple(value) if key in {"allowed_hosts", "allowed_origins"} else value
                             for key, value in self.http.items() if key not in {"host", "port"}})

    @classmethod
    def parse(cls, value: dict) -> "CoreConfig":
        _fields(value, {"schema", "repository_root", "state_root", "identity", "vault", "http", "tls"},
                {"provisioning_identity", "oidc", "clock", "resource_authority_root"})
        if value["schema"] != "orion.core-service.v1":
            raise ValueError("unsupported core service schema")
        vault = _fields(value["vault"], {"key_id", "key_file"}, {"previous_keys"})
        _text(vault["key_id"])
        _path(vault["key_file"])
        previous = vault.get("previous_keys", {})
        if not isinstance(previous, dict) or len(previous) > 16 or vault["key_id"] in previous:
            raise ValueError("invalid previous secret key references")
        for name, path in previous.items():
            _text(name)
            _path(path)
        http = _fields(value["http"], {"host", "port", "allowed_hosts"},
                       {"allowed_origins", "requests_per_minute", "max_concurrency",
                        "body_timeout_seconds", "operation_timeout_seconds"})
        _text(http["host"])
        if type(http["port"]) is not int or not 0 <= http["port"] <= 65535:
            raise ValueError("invalid service port")
        for name in ("allowed_hosts", "allowed_origins"):
            rows = http.get(name, [])
            if not isinstance(rows, list) or any(not isinstance(row, str) for row in rows):
                raise ValueError("HTTP allowlists must be explicit lists")
        if http["host"] not in http["allowed_hosts"]:
            raise ValueError("bind host must be explicitly allowed")
        tls = _fields(value["tls"], {"root", "server_leaf"})
        _path(tls["root"])
        _path(tls["server_leaf"])
        root, state = _path(value["repository_root"]).resolve(), _path(value["state_root"]).resolve()
        if root == state or state in root.parents:
            raise ValueError("mutable service state must not contain application resources")
        identity = IdentitySettings.parse(value["identity"])
        provisioning_value = value.get("provisioning_identity")
        provisioning = IdentitySettings.parse(provisioning_value) if provisioning_value is not None else None
        if provisioning is not None and provisioning.subject == identity.subject:
            raise ValueError("provisioning and running service identities must be distinct")
        clock = value.get("clock")
        if clock is not None:
            _fields(clock, {"enabled", "period_s", "stop_timeout_s"})
            if type(clock["enabled"]) is not bool:
                raise ValueError("clock.enabled must be a boolean")
            for name, maximum in (("period_s", 3600.0), ("stop_timeout_s", 60.0)):
                seconds = clock[name]
                if (isinstance(seconds, bool) or not isinstance(seconds, (int, float))
                        or not math.isfinite(seconds) or not .001 <= seconds <= maximum):
                    raise ValueError("invalid processing clock interval")
            clock = dict(clock)
        config = cls(root, state, identity, dict(vault), dict(http), dict(tls),
                     provisioning, _oidc(value.get("oidc")), clock,
                     _path(value["resource_authority_root"]).resolve() if "resource_authority_root" in value else None)
        config.http_policy  # Validate quotas/deadlines before touching persistent state.
        return config


def load_config(path: Path | str) -> CoreConfig:
    path = Path(path)
    if path.stat().st_size > 65536:
        raise ValueError("service configuration too large")
    return CoreConfig.parse(strict_object(path.read_bytes()))
