"""OS-protected secrets and atomic state. Never a hardware-qualified HSM."""
from __future__ import annotations

import base64
from contextlib import contextmanager
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import secrets
import threading
import time
from uuid import uuid4


class VaultError(RuntimeError):
    pass


_locks: dict[str, threading.RLock] = {}
_locks_guard = threading.Lock()


@contextmanager
def file_lock(path: Path, *, timeout: float = 15):
    """Serialize local writers across threads AND processes."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with _locks_guard:
        local = _locks.setdefault(str(path.resolve()), threading.RLock())
    with local:
        descriptor = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
        with os.fdopen(descriptor, "r+b", buffering=0) as handle:
            if os.fstat(handle.fileno()).st_size == 0:
                handle.write(b"\x00")
            deadline = time.monotonic() + timeout
            while True:
                try:
                    handle.seek(0)
                    if os.name == "nt":
                        import msvcrt
                        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise VaultError("protected storage lock unavailable") from None
                    time.sleep(.01)
            try:
                yield
            finally:
                if os.name == "nt":
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def atomic_write(path: Path, payload: bytes):
    temp = path.with_name(path.name + "." + uuid4().hex + ".tmp")
    descriptor = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(payload)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temp, path)
        if os.name != "nt":
            os.chmod(path, 0o600)
            directory = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
    finally:
        temp.unlink(missing_ok=True)


class _DataBlob(ctypes.Structure):
    # DATA_BLOB.cbData is a Windows DWORD even when imported on another OS.
    _fields_ = [("cbData", ctypes.c_uint32), ("pbData", ctypes.POINTER(ctypes.c_ubyte))]


_dpapi_bindings = None
_dpapi_bindings_guard = threading.Lock()


def _get_dpapi_bindings():
    """Publish one fully configured binding tuple; never retain input/output."""
    global _dpapi_bindings
    if _dpapi_bindings is None:
        with _dpapi_bindings_guard:
            if _dpapi_bindings is None:
                try:
                    crypt = ctypes.WinDLL("crypt32", use_last_error=True)
                    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
                    blob_pointer = ctypes.POINTER(_DataBlob)
                    signature = (blob_pointer, ctypes.c_void_p, blob_pointer,
                                 ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint32, blob_pointer)
                    protect, unprotect, release = crypt.CryptProtectData, crypt.CryptUnprotectData, kernel.LocalFree
                    for operation in (protect, unprotect):
                        operation.argtypes = signature
                        operation.restype = wintypes.BOOL
                    release.argtypes = (ctypes.c_void_p,)
                    release.restype = ctypes.c_void_p  # HLOCAL: NULL means success.
                except (OSError, AttributeError):
                    raise VaultError("operating system secret protection unavailable") from None
                # Keep DLLs and functions alive, and publish only after BOTH
                # prototypes and LocalFree are complete. Buffers stay per call.
                _dpapi_bindings = (crypt, kernel, protect, unprotect, release)
    return _dpapi_bindings


def _dpapi(payload: bytes, *, decrypt: bool) -> bytes:
    _crypt, _kernel, protect, unprotect, release = _get_dpapi_bindings()
    source = ctypes.create_string_buffer(payload)
    source_blob = _DataBlob(len(payload), ctypes.cast(source, ctypes.POINTER(ctypes.c_ubyte)))
    entropy = ctypes.create_string_buffer(b"ORION-Q:CurrentUser:security-v1")
    entropy_blob = _DataBlob(len(entropy) - 1, ctypes.cast(entropy, ctypes.POINTER(ctypes.c_ubyte)))
    output_blob = _DataBlob()
    operation = unprotect if decrypt else protect
    # CRYPTPROTECT_UI_FORBIDDEN only; never LOCAL_MACHINE.
    if not operation(ctypes.byref(source_blob), None, ctypes.byref(entropy_blob), None, None,
                     0x1, ctypes.byref(output_blob)):
        raise VaultError("operating system secret protection failed")
    try:
        if output_blob.cbData and not output_blob.pbData:
            raise VaultError("operating system secret protection returned invalid output")
        try:
            return ctypes.string_at(output_blob.pbData, output_blob.cbData)
        except (OSError, ValueError, TypeError):
            raise VaultError("operating system protected buffer read failed") from None
    finally:
        if release(ctypes.cast(output_blob.pbData, ctypes.c_void_p)):
            raise VaultError("operating system protected buffer release failed")


class SecretVault:
    def __init__(self, path, *, profile="local"):
        self.path = Path(path)
        self.profile = profile
        if profile not in {"local", "operational"}:
            raise ValueError("unknown security profile")
        if os.name != "nt" and profile == "operational":
            raise VaultError("operational profile requires an OS/service-protected secret vault")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.mode = "windows-dpapi-current-user" if os.name == "nt" else "unix-0600-local-only"

    @contextmanager
    def transaction(self):
        with file_lock(self.path.with_suffix(self.path.suffix + ".lock")):
            rows = self._read()
            yield rows
            self._write(rows)

    def _read(self):
        if not self.path.exists():
            return {}
        try:
            if os.name != "nt" and (self.path.stat().st_mode & 0o077):
                raise VaultError("local secret vault permissions are not private")
            envelope = json.loads(self.path.read_bytes())
            if envelope["mode"] != self.mode or envelope["schema"] != "orion.security.vault.v1":
                raise VaultError("protected storage profile mismatch")
            payload = base64.b64decode(envelope["protected"], validate=True)
            plaintext = _dpapi(payload, decrypt=True) if os.name == "nt" else payload
            rows = json.loads(plaintext)
            if not isinstance(rows, dict):
                raise VaultError("invalid protected storage")
            return rows
        except (ValueError, KeyError, TypeError, OSError) as exc:
            raise VaultError("protected storage is unreadable or modified") from exc

    def _write(self, rows):
        plaintext = json.dumps(rows, separators=(",", ":"), sort_keys=True).encode()
        protected = _dpapi(plaintext, decrypt=False) if os.name == "nt" else plaintext
        envelope = {"schema": "orion.security.vault.v1", "mode": self.mode,
                    "protected": base64.b64encode(protected).decode("ascii")}
        atomic_write(self.path, json.dumps(envelope, separators=(",", ":")).encode())

    @staticmethod
    def _name(name):
        if not isinstance(name, str) or not 1 <= len(name) <= 256 or any(c in name for c in "\x00\r\n"):
            raise ValueError("invalid protected secret reference")

    def get_bytes(self, name: str) -> bytes:
        self._name(name)
        with file_lock(self.path.with_suffix(self.path.suffix + ".lock")):
            rows = self._read()
            if name not in rows:
                raise VaultError("protected secret reference is unavailable")
            return base64.b64decode(rows[name], validate=True)

    def contains(self, name: str) -> bool:
        self._name(name)
        with file_lock(self.path.with_suffix(self.path.suffix + ".lock")):
            return name in self._read()

    def get_or_create_bytes(self, name: str, size: int = 32) -> bytes:
        self._name(name)
        if isinstance(size, bool) or not isinstance(size, int) or not 16 <= size <= 4096:
            raise ValueError("invalid protected secret size")
        with self.transaction() as rows:
            if name not in rows:
                rows[name] = base64.b64encode(secrets.token_bytes(size)).decode("ascii")
            value = base64.b64decode(rows[name], validate=True)
            if len(value) != size:
                raise VaultError("protected secret size mismatch")
            return value

    def get_or_create(self, name: str) -> bytes:
        return self.get_or_create_bytes(name)

    def put_bytes(self, name: str, value: bytes):
        self._name(name)
        if not isinstance(value, bytes) or not 1 <= len(value) <= 4 * 1024 * 1024:
            raise ValueError("invalid protected secret")
        with self.transaction() as rows:
            rows[name] = base64.b64encode(value).decode("ascii")

    def rotate(self, name: str, size: int = 32) -> str:
        self._name(name)
        version = name + ":version:" + uuid4().hex
        value = self.get_or_create_bytes(version, size)
        self.put_bytes(name + ":active", version.encode("ascii"))
        del value
        return version
