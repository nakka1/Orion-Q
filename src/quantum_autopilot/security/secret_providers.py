"""Portable authenticated secret protection with explicitly provisioned keys.

The service core accepts a provider; it never guesses one from the operating
system. Mounted keys are an integration with the deployment's secret service,
not a replacement for a hardware-qualified HSM or an anti-rollback anchor.
"""
from __future__ import annotations

import base64
from collections.abc import Callable, Mapping
import json
import os
from pathlib import Path
import re
import secrets
import stat
from types import MappingProxyType
from typing import Protocol, runtime_checkable

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .vault import VaultError


_SCHEMA = "orion.security.vault.v2"
_MODE = "mounted-or-service-aes256-gcm"
_MAX_PAYLOAD = 64 * 1024 * 1024
_KEY_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z")


def _reference(key_id: str, version: int) -> tuple[str, int]:
    if (not isinstance(key_id, str) or _KEY_ID.fullmatch(key_id) is None
            or isinstance(version, bool) or not isinstance(version, int)
            or not 1 <= version <= 2**31 - 1):
        raise ValueError("invalid provisioned encryption key reference")
    return key_id, version


@runtime_checkable
class VaultProtectionProvider(Protocol):
    """The same protection contract for a mounted key or a secret service."""

    mode: str
    operational_ready: bool

    def seal(self, plaintext: bytes) -> dict: ...

    def open(self, envelope: Mapping) -> bytes: ...


@runtime_checkable
class EncryptionKeySource(Protocol):
    """Return an explicitly provisioned AES-256 key, never create a replacement.

    Implementations may resolve a key through a deployment secret service. They
    must authenticate/authorize that access and fail closed when unavailable.
    """

    def read_key(self, key_id: str, version: int) -> bytes: ...


class MountedKeySource:
    """Read-only key references for POSIX secret mounts.

    POSIX files must be regular, private, owned by this UID or root, and must not
    be symlinks. Windows requires an explicit native ACL validator; no POSIX
    mode bits or Windows login session are assumed to provide protection.
    """

    def __init__(self, key_files: Mapping[tuple[str, int], str | Path], *,
                 permissions_validator: Callable[[Path, os.stat_result], bool | None] | None = None):
        if not isinstance(key_files, Mapping) or not key_files:
            raise ValueError("at least one provisioned encryption key is required")
        references = {}
        for reference, path in key_files.items():
            if not isinstance(reference, tuple) or len(reference) != 2:
                raise ValueError("invalid provisioned encryption key reference")
            _reference(*reference)
            if not isinstance(path, (str, Path)) or not str(path):
                raise ValueError("invalid provisioned encryption key path")
            references[reference] = Path(path).absolute()
        if permissions_validator is not None and not callable(permissions_validator):
            raise ValueError("invalid secret mount permission validator")
        self._key_files = MappingProxyType(references)
        self._permissions_validator = permissions_validator

    def _check_permissions(self, path: Path, metadata: os.stat_result) -> None:
        if not stat.S_ISREG(metadata.st_mode):
            raise VaultError("provisioned encryption key must be a regular file")
        if os.name == "posix":
            if metadata.st_mode & 0o177:
                raise VaultError("provisioned encryption key permissions are not private")
            if metadata.st_uid not in {os.geteuid(), 0}:
                raise VaultError("provisioned encryption key owner is not authorized")
        elif self._permissions_validator is None:
            raise VaultError("secret mount requires an explicit native ACL validator")
        if self._permissions_validator is not None:
            try:
                if self._permissions_validator(path, metadata) is False:
                    raise VaultError("provisioned encryption key permission verification failed")
            except Exception:
                raise VaultError("provisioned encryption key permission verification failed") from None

    def read_key(self, key_id: str, version: int) -> bytes:
        reference = _reference(key_id, version)
        path = self._key_files.get(reference)
        if path is None:
            raise VaultError("provisioned encryption key version is unavailable")
        try:
            original = path.lstat()
            if stat.S_ISLNK(original.st_mode):
                raise VaultError("provisioned encryption key must not be a symlink")
            if not stat.S_ISREG(original.st_mode):
                # Opening a FIFO or device first could block a control worker.
                raise VaultError("provisioned encryption key must be a regular file")
            # A replacement FIFO must not block between lstat and open. On a
            # regular file this flag has no effect; fstat still verifies its type.
            flags = (os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
                     | getattr(os, "O_NONBLOCK", 0))
            descriptor = os.open(path, flags)
            with os.fdopen(descriptor, "rb") as handle:
                metadata = os.fstat(handle.fileno())
                if (original.st_dev, original.st_ino) != (metadata.st_dev, metadata.st_ino):
                    raise VaultError("provisioned encryption key changed during acquisition")
                self._check_permissions(path, metadata)
                key = handle.read(33)
                after = os.fstat(handle.fileno())
                if (metadata.st_size, metadata.st_mtime_ns, metadata.st_ctime_ns) != (
                        after.st_size, after.st_mtime_ns, after.st_ctime_ns):
                    raise VaultError("provisioned encryption key changed during acquisition")
            if len(key) != 32:
                raise VaultError("provisioned encryption key must contain exactly 32 bytes")
            return key
        except VaultError:
            raise
        except (OSError, ValueError, TypeError):
            raise VaultError("provisioned encryption key is unavailable") from None

    def public_references(self) -> list[dict]:
        if self._permissions_validator is not None:
            raise ValueError("native ACL validator cannot be transferred as public configuration")
        return [{"key_id": key_id, "version": version, "path": str(path)}
                for (key_id, version), path in sorted(self._key_files.items())]


class AESGCMSecretProvider:
    """Platform-independent authenticated encryption and explicit key versions."""

    mode = _MODE
    operational_ready = True
    desktop_only = False

    def __init__(self, key_source: EncryptionKeySource, *, active_key_id: str, active_version: int):
        if not isinstance(key_source, EncryptionKeySource):
            raise ValueError("an explicit encryption key source is required")
        self.active_key_id, self.active_version = _reference(active_key_id, active_version)
        self._key_source = key_source
        # Fail before any mutable state is created. This is acquisition, not
        # provisioning: an absent key is never replaced with random material.
        self._key(self.active_key_id, self.active_version)

    def _key(self, key_id: str, version: int) -> bytes:
        try:
            value = self._key_source.read_key(key_id, version)
            if not isinstance(value, bytes) or len(value) != 32:
                raise VaultError("provisioned encryption key must contain exactly 32 bytes")
            return value
        except VaultError:
            raise
        except Exception:
            # Remote-service errors may include credentials or request bodies.
            raise VaultError("provisioned encryption key is unavailable") from None

    @staticmethod
    def _aad(header: Mapping) -> bytes:
        return json.dumps(dict(header), sort_keys=True, separators=(",", ":")).encode("utf-8")

    def seal(self, plaintext: bytes) -> dict:
        if not isinstance(plaintext, bytes) or len(plaintext) > _MAX_PAYLOAD:
            raise VaultError("protected storage payload is invalid")
        header = {"schema": _SCHEMA, "mode": self.mode,
                  "key_id": self.active_key_id, "key_version": self.active_version}
        nonce = secrets.token_bytes(12)
        protected = AESGCM(self._key(self.active_key_id, self.active_version)).encrypt(
            nonce, plaintext, self._aad(header))
        return {**header, "nonce": base64.b64encode(nonce).decode("ascii"),
                "protected": base64.b64encode(protected).decode("ascii")}

    def open(self, envelope: Mapping) -> bytes:
        try:
            if (not isinstance(envelope, Mapping)
                    or set(envelope) != {"schema", "mode", "key_id", "key_version", "nonce", "protected"}
                    or envelope["schema"] != _SCHEMA or envelope["mode"] != self.mode):
                raise VaultError("protected storage profile mismatch")
            key_id, version = _reference(envelope["key_id"], envelope["key_version"])
            if (not isinstance(envelope["nonce"], str) or len(envelope["nonce"]) != 16
                    or not isinstance(envelope["protected"], str)
                    or len(envelope["protected"]) > ((_MAX_PAYLOAD + 16 + 2) // 3) * 4):
                raise VaultError("protected storage payload is invalid")
            nonce = base64.b64decode(envelope["nonce"], validate=True)
            ciphertext = base64.b64decode(envelope["protected"], validate=True)
            if len(nonce) != 12 or not 16 <= len(ciphertext) <= _MAX_PAYLOAD + 16:
                raise VaultError("protected storage payload is invalid")
            header = {"schema": _SCHEMA, "mode": self.mode,
                      "key_id": key_id, "key_version": version}
            return AESGCM(self._key(key_id, version)).decrypt(nonce, ciphertext, self._aad(header))
        except VaultError:
            raise
        except (ValueError, TypeError, KeyError, InvalidTag):
            raise VaultError("protected storage is unreadable or modified") from None


class MountedSecretProvider(AESGCMSecretProvider):
    """Convenience adapter for AES-256 keys provisioned in a secret mount."""

    def __init__(self, key_files: Mapping[tuple[str, int], str | Path], *,
                 active_key_id: str, active_version: int,
                 permissions_validator: Callable[[Path, os.stat_result], bool | None] | None = None):
        super().__init__(MountedKeySource(key_files, permissions_validator=permissions_validator),
                         active_key_id=active_key_id, active_version=active_version)

    def public_configuration(self) -> dict:
        """Worker configuration contains references, never encryption key bytes."""
        return {"active_key_id": self.active_key_id, "active_version": self.active_version,
                "keys": self._key_source.public_references()}

    configuration = public_configuration

    @classmethod
    def from_config(cls, config: Mapping) -> "MountedSecretProvider":
        """Parse public references only; credential values never belong in JSON."""
        if (not isinstance(config, Mapping)
                or set(config) != {"active_key_id", "active_version", "keys"}
                or not isinstance(config["keys"], list) or not config["keys"]):
            raise ValueError("invalid mounted secret provider configuration")
        references = {}
        for entry in config["keys"]:
            if not isinstance(entry, Mapping) or set(entry) != {"key_id", "version", "path"}:
                raise ValueError("invalid mounted secret provider configuration")
            reference = _reference(entry["key_id"], entry["version"])
            if reference in references:
                raise ValueError("duplicate provisioned encryption key reference")
            references[reference] = entry["path"]
        return cls(references, active_key_id=config["active_key_id"], active_version=config["active_version"])
