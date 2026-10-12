"""Explicit process credentials, independent of an interactive OS session.

Public subjects/configuration are not authentication. A binding must supply a
secret obtained from a separately protected source, and SecurityRuntime checks
it against the provisioned vault reference before granting a revocable session.
Provisioning is an offline trusted deployment operation, never HTTP input.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import os
from pathlib import Path
import stat
from typing import Callable

from .identity import AuthenticationError
from .policy import validate_grant


def _subject(value: str) -> str:
    if (not isinstance(value, str) or not 1 <= len(value) <= 512
            or any(character in value for character in "\r\n\x00")
            or value.startswith("os:")):
        raise ValueError("invalid non-interactive service identity")
    return value


def validate_service_credential(value: bytes) -> bytes:
    if not isinstance(value, bytes) or not 32 <= len(value) <= 4096:
        raise AuthenticationError("protected service credential is unavailable or invalid")
    return value


def service_credential_reference(subject: str) -> str:
    """A separate namespace prevents credentials overwriting unrelated secrets."""
    return "service-auth:" + hashlib.sha256(_subject(subject).encode("utf-8")).hexdigest()


def read_mounted_service_credential(path, *, permissions_validator=None) -> bytes:
    """Read a private, regular mounted credential without following symlinks.

    On Unix the file must belong to this process/root and forbid group/other
    access. On Windows an explicit ACL validator is required: a mounted-service
    profile does not silently fall back to DPAPI or assume POSIX modes are ACLs.
    Deployments can inject a secret-service reader instead of mounting a file.
    """
    credential_path = Path(path)
    descriptor = None
    try:
        original = credential_path.lstat()
        if not stat.S_ISREG(original.st_mode) or not 32 <= original.st_size <= 4096:
            raise AuthenticationError("protected service credential is unavailable or invalid")
        flags = (os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
                 | getattr(os, "O_NONBLOCK", 0))
        descriptor = os.open(credential_path, flags)
        metadata = os.fstat(descriptor)
        if (original.st_dev, original.st_ino) != (metadata.st_dev, metadata.st_ino):
            raise AuthenticationError("protected service credential changed during acquisition")
        if not stat.S_ISREG(metadata.st_mode) or not 32 <= metadata.st_size <= 4096:
            raise AuthenticationError("protected service credential is unavailable or invalid")
        if os.name == "nt":
            if not callable(permissions_validator):
                raise AuthenticationError("protected service credential permissions are unverified")
            try:
                verified = permissions_validator(credential_path, metadata)
            except Exception:
                raise AuthenticationError("protected service credential permissions are unverified") from None
            if verified is False:
                raise AuthenticationError("protected service credential permissions are unverified")
        elif metadata.st_mode & 0o177 or metadata.st_uid not in {os.getuid(), 0}:
            raise AuthenticationError("protected service credential permissions are not private")
        value = os.read(descriptor, 4097)
        if len(value) != metadata.st_size:
            raise AuthenticationError("protected service credential read was incomplete")
        after = os.fstat(descriptor)
        if (metadata.st_size, metadata.st_mtime_ns, metadata.st_ctime_ns) != (
                after.st_size, after.st_mtime_ns, after.st_ctime_ns):
            raise AuthenticationError("protected service credential changed during acquisition")
        return validate_service_credential(value)
    except (OSError, ValueError) as exc:
        raise AuthenticationError("protected service credential is unavailable or invalid") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)


@dataclass(frozen=True)
class ServiceIdentityProvision:
    """Trusted initial grant + secret, admitted only by explicit provisioning."""

    subject: str
    roles: tuple[str, ...]
    scopes: tuple[str, ...]
    credential: bytes = field(repr=False)
    display_name: str = ""

    def __post_init__(self):
        _subject(self.subject)
        roles, scopes = validate_grant(self.roles, self.scopes)
        object.__setattr__(self, "roles", tuple(sorted(roles)))
        object.__setattr__(self, "scopes", scopes)
        validate_service_credential(self.credential)
        if not isinstance(self.display_name, str) or len(self.display_name) > 256:
            raise ValueError("invalid service display name")

    @property
    def credential_reference(self) -> str:
        return service_credential_reference(self.subject)


@dataclass(frozen=True)
class ServiceIdentityBinding:
    """A trusted composition dependency; credentials are never configuration text."""

    subject: str
    credential_reader: Callable[[], bytes] = field(repr=False, compare=False)
    ttl_seconds: int = 3600

    def __post_init__(self):
        _subject(self.subject)
        if not callable(self.credential_reader):
            raise ValueError("protected service credential reader is required")
        if isinstance(self.ttl_seconds, bool) or not isinstance(self.ttl_seconds, int) or not 1 <= self.ttl_seconds <= 86400:
            raise ValueError("service session lifetime must be between 1 and 86400 seconds")

    def read_credential(self) -> bytes:
        try:
            return validate_service_credential(self.credential_reader())
        except Exception:
            # Secret-service SDK exceptions can include request bodies/tokens.
            # Keep that text outside the operational authentication boundary.
            raise AuthenticationError("protected service credential is unavailable or invalid") from None

    @classmethod
    def from_mounted_file(cls, subject: str, path, *, ttl_seconds=3600, permissions_validator=None):
        credential_path = Path(path)
        return cls(subject, lambda: read_mounted_service_credential(
            credential_path, permissions_validator=permissions_validator), ttl_seconds)
