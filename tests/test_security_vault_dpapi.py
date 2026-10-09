"""DPAPI type lifetime, atomic prototypes and failures; native crypto on Windows."""
from concurrent.futures import ThreadPoolExecutor
import base64
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
from types import SimpleNamespace

import pytest

from quantum_autopilot.security import vault


PUBLIC_LEGACY_PAYLOAD = b"ORION-Q public synthetic legacy DPAPI fixture v1"


@pytest.fixture(scope="module")
def legacy_ciphertext():
    """Public fixture only; optionally use the ciphertext made before this edit."""
    if os.name != "nt":
        pytest.skip("native Windows CurrentUser DPAPI required")
    pre_edit_fixture = os.environ.get("ORION_DPAPI_LEGACY_TEST_FIXTURE")
    if pre_edit_fixture:
        record = json.loads(Path(pre_edit_fixture).read_text(encoding="utf-8"))
        assert record["schema"] == "orion.dpapi-public-legacy-fixture.v1"
        assert record["public_synthetic_payload"] is True
        assert record["legacy_source_sha256"] == "84eaadcefd0cfe824f88d6c1619936c4f10f1d6c2f11ffd75027d84a20237cc4"
        assert record["scope"] == "CurrentUser" and record["flags"] == 1
        assert base64.b64decode(record["public_payload_b64"], validate=True) == PUBLIC_LEGACY_PAYLOAD
        return base64.b64decode(record["ciphertext_b64"], validate=True)

    # Independent pre-fix DATA_BLOB/crypt32 invocation provides a portable
    # regression when the host-bound, pre-edit fixture is unavailable.
    class LegacyBlob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_ubyte))]

    crypt = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    operation = crypt.CryptProtectData
    operation.argtypes = [ctypes.POINTER(LegacyBlob), ctypes.c_void_p, ctypes.POINTER(LegacyBlob),
                          ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(LegacyBlob)]
    operation.restype = wintypes.BOOL
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    source = ctypes.create_string_buffer(PUBLIC_LEGACY_PAYLOAD)
    entropy = ctypes.create_string_buffer(b"ORION-Q:CurrentUser:security-v1")
    source_blob = LegacyBlob(len(PUBLIC_LEGACY_PAYLOAD), ctypes.cast(source, ctypes.POINTER(ctypes.c_ubyte)))
    entropy_blob = LegacyBlob(len(entropy) - 1, ctypes.cast(entropy, ctypes.POINTER(ctypes.c_ubyte)))
    output_blob = LegacyBlob()
    assert operation(ctypes.byref(source_blob), None, ctypes.byref(entropy_blob), None, None, 1,
                     ctypes.byref(output_blob))
    try:
        return ctypes.string_at(output_blob.pbData, output_blob.cbData)
    finally:
        assert not kernel.LocalFree(ctypes.cast(output_blob.pbData, ctypes.c_void_p))


class Function:
    def __init__(self, callback):
        self.callback = callback
        self.argtypes = None
        self.restype = None

    def __call__(self, *args):
        assert self.argtypes is not None and self.restype is not None
        return self.callback(*args)


def fake_bindings(monkeypatch, *, operation_success=True, release_result=None, invalid_output=False):
    calls, buffers, loads = [], [], []
    lock = threading.Lock()

    def operation(source, description, entropy, reserved, prompt, flags, output):
        assert description is reserved is prompt is None
        assert flags == 1
        incoming = ctypes.cast(source, ctypes.POINTER(vault._DataBlob)).contents
        supplied_entropy = ctypes.cast(entropy, ctypes.POINTER(vault._DataBlob)).contents
        assert ctypes.string_at(supplied_entropy.pbData, supplied_entropy.cbData) == b"ORION-Q:CurrentUser:security-v1"
        result = ctypes.cast(output, ctypes.POINTER(vault._DataBlob)).contents
        with lock:
            calls.append("operation")
        if not operation_success:
            return False
        if invalid_output:
            result.cbData = 3
            return True
        value = ctypes.string_at(incoming.pbData, incoming.cbData)
        buffer = ctypes.create_string_buffer(value)
        with lock:
            buffers.append(buffer)  # Emulated native allocation, isolated fixture only.
        result.cbData = len(value)
        result.pbData = ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte))
        return True

    def release(pointer):
        with lock:
            calls.append("release")
        return release_result

    crypt = SimpleNamespace(CryptProtectData=Function(operation), CryptUnprotectData=Function(operation))
    kernel = SimpleNamespace(LocalFree=Function(release))

    def load(name, **kwargs):
        assert kwargs == {"use_last_error": True}
        with lock:
            loads.append(name)
        time.sleep(.001)  # Widen concurrent initialization without a real DLL.
        return crypt if name == "crypt32" else kernel

    monkeypatch.setattr(vault, "_dpapi_bindings", None)
    monkeypatch.setattr(vault.ctypes, "WinDLL", load, raising=False)
    return calls, loads, crypt, kernel


def test_data_blob_layout_has_fixed_windows_dword():
    assert vault._DataBlob._fields_[0][1] is ctypes.c_uint32
    pointer_alignment = ctypes.alignment(ctypes.c_void_p)
    assert vault._DataBlob.pbData.offset == ((4 + pointer_alignment - 1) // pointer_alignment) * pointer_alignment


def test_concurrent_initialization_publishes_complete_binding_once(monkeypatch):
    calls, loads, crypt, kernel = fake_bindings(monkeypatch)
    payload = b"public-synthetic-concurrency-fixture"
    with ThreadPoolExecutor(max_workers=16) as pool:
        values = list(pool.map(lambda index: vault._dpapi(payload, decrypt=bool(index % 2)), range(256)))
    assert values == [payload] * 256
    assert loads == ["crypt32", "kernel32"]
    assert calls.count("operation") == calls.count("release") == 256
    assert crypt.CryptProtectData.argtypes == crypt.CryptUnprotectData.argtypes
    assert len(crypt.CryptProtectData.argtypes) == 7
    assert kernel.LocalFree.restype is ctypes.c_void_p
    assert vault._dpapi_bindings == (crypt, kernel, crypt.CryptProtectData, crypt.CryptUnprotectData, kernel.LocalFree)


def test_failed_dll_initialization_does_not_publish_partial_bindings(monkeypatch):
    monkeypatch.setattr(vault, "_dpapi_bindings", None)
    def unavailable(*args, **kwargs):
        raise OSError("synthetic-untrusted-detail")
    monkeypatch.setattr(vault.ctypes, "WinDLL", unavailable, raising=False)
    with pytest.raises(vault.VaultError, match="secret protection unavailable") as error:
        vault._dpapi(b"public", decrypt=False)
    assert vault._dpapi_bindings is None
    assert "synthetic-untrusted-detail" not in str(error.value)


def test_native_failure_is_sanitized_and_not_presented_as_success(monkeypatch, capsys, caplog):
    calls, _, _, _ = fake_bindings(monkeypatch, operation_success=False)
    marker = b"synthetic-marker-not-for-logs"
    with pytest.raises(vault.VaultError, match="secret protection failed") as error:
        vault._dpapi(marker, decrypt=True)
    assert calls == ["operation"]
    assert marker.decode() not in str(error.value) + capsys.readouterr().out + caplog.text


def test_local_free_failure_blocks_return_without_logging_payload(monkeypatch, capsys, caplog):
    calls, _, _, _ = fake_bindings(monkeypatch, release_result=123)
    marker = b"synthetic-marker-not-for-logs"
    with pytest.raises(vault.VaultError, match="buffer release failed") as error:
        vault._dpapi(marker, decrypt=False)
    assert calls == ["operation", "release"]
    assert marker.decode() not in str(error.value) + capsys.readouterr().out + caplog.text


def test_copy_failure_still_frees_buffer_and_sanitizes_details(monkeypatch):
    calls, _, _, _ = fake_bindings(monkeypatch)
    original_read = vault.ctypes.string_at
    reads = 0
    def unreadable(pointer, length):
        # Input/entropy reads belong to the emulated native fixture above.
        nonlocal reads
        reads += 1
        if reads <= 2:
            return original_read(pointer, length)
        raise ValueError("synthetic-untrusted-copy-detail")
    monkeypatch.setattr(vault.ctypes, "string_at", unreadable)
    with pytest.raises(vault.VaultError, match="buffer read failed") as error:
        vault._dpapi(b"public", decrypt=False)
    assert calls == ["operation", "release"]
    assert "synthetic-untrusted-copy-detail" not in str(error.value)


def test_invalid_null_output_does_not_dereference_pointer(monkeypatch):
    calls, _, _, _ = fake_bindings(monkeypatch, invalid_output=True)
    with pytest.raises(vault.VaultError, match="invalid output"):
        vault._dpapi(b"public", decrypt=False)
    assert calls == ["operation", "release"]


@pytest.mark.skipif(os.name != "nt", reason="native Windows CurrentUser DPAPI required")
def test_real_windows_roundtrip_and_pointer_cache_bound_in_fresh_process():
    source = Path(vault.__file__).resolve()
    program = f"""
import ctypes,gc,importlib.util,json
s=importlib.util.spec_from_file_location('_native_dpapi_regression',{str(source)!r})
v=importlib.util.module_from_spec(s);s.loader.exec_module(v)
payload=b'public-synthetic-native-dpapi-regression'
for index in range(1000):
    protected=v._dpapi(payload,decrypt=False)
    assert v._dpapi(protected,decrypt=True)==payload
del protected
gc.collect()
entries=sum(isinstance(k,type) and k.__module__==s.name and k.__name__=='_DataBlob' for k in ctypes._pointer_type_cache)
print(json.dumps({{'roundtrips':1000,'blob_pointer_entries':entries}}))
"""
    result = subprocess.run([sys.executable, "-"], input=program, text=True, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {"roundtrips": 1000, "blob_pointer_entries": 1}


def test_real_windows_reads_ciphertext_from_previous_implementation(legacy_ciphertext):
    assert vault._dpapi(legacy_ciphertext, decrypt=True) == PUBLIC_LEGACY_PAYLOAD


def test_real_windows_corrupted_ciphertext_is_rejected(legacy_ciphertext, capsys, caplog):
    corrupted = bytearray(legacy_ciphertext)
    corrupted[len(corrupted) // 2] ^= 1
    with pytest.raises(vault.VaultError, match="secret protection failed") as error:
        vault._dpapi(bytes(corrupted), decrypt=True)
    captured = capsys.readouterr()
    assert PUBLIC_LEGACY_PAYLOAD.decode() not in str(error.value) + captured.out + captured.err + caplog.text


@pytest.mark.skipif(os.name != "nt", reason="native Windows CurrentUser DPAPI required")
def test_real_windows_concurrent_roundtrips_keep_one_blob_type():
    def roundtrip(index):
        payload = b"public-synthetic-native-concurrent-" + str(index).encode("ascii")
        protected = vault._dpapi(payload, decrypt=False)
        return vault._dpapi(protected, decrypt=True) == payload

    with ThreadPoolExecutor(max_workers=16) as pool:
        assert all(pool.map(roundtrip, range(512)))
    assert sum(kind is vault._DataBlob for kind in ctypes._pointer_type_cache) == 1
