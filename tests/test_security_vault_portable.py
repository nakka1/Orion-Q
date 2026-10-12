"""Portable vault contract: actual AES-GCM; explicitly synthetic key sources."""
from concurrent.futures import ThreadPoolExecutor
import base64
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
from types import SimpleNamespace

import pytest

from quantum_autopilot.security.secret_providers import (
    AESGCMSecretProvider, MountedKeySource, MountedSecretProvider,
)
from quantum_autopilot.security.vault import SecretVault, VaultError


class SyntheticKeyService:
    """Independent provider fixture, not an operational credential fallback."""

    def __init__(self, keys):
        self.keys = dict(keys)
        self.available = True

    def read_key(self, key_id, version):
        if not self.available:
            raise RuntimeError("synthetic credential must not appear in errors")
        return self.keys[(key_id, version)]


def provider(source=None, *, version=1):
    return AESGCMSecretProvider(source or SyntheticKeyService({("core", 1): bytes(range(32))}),
                               active_key_id="core", active_version=version)


def test_operational_requires_explicit_provider_on_every_platform(tmp_path):
    with pytest.raises(VaultError, match="explicitly provisioned"):
        SecretVault(tmp_path / "absent" / "vault.json", profile="operational")
    assert not (tmp_path / "absent").exists()


def test_encrypted_vault_restart_uses_explicit_key_and_never_dpapi(tmp_path, monkeypatch):
    from quantum_autopilot.security import vault as module

    def forbidden(*args, **kwargs):
        raise AssertionError("portable core must not call DPAPI")

    monkeypatch.setattr(module, "_dpapi", forbidden)
    source = SyntheticKeyService({("core", 1): bytes(range(32))})
    path = tmp_path / "vault.json"
    first = SecretVault(path, profile="operational", provider=provider(source))
    value = first.get_or_create("persistent-key")
    first.put_bytes("fixture", b"recognizable-synthetic-secret")
    record = json.loads(path.read_bytes())
    assert record["schema"] == "orion.security.vault.v2"
    assert record["key_id"] == "core" and record["key_version"] == 1
    assert b"recognizable-synthetic-secret" not in path.read_bytes()
    assert b"persistent-key" not in base64.b64decode(record["protected"])
    reopened = SecretVault(path, profile="operational", provider=provider(source))
    assert reopened.get_bytes("persistent-key") == value
    assert reopened.get_bytes("fixture") == b"recognizable-synthetic-secret"
    assert reopened.mode == "mounted-or-service-aes256-gcm"


@pytest.mark.parametrize("field,replacement", [
    ("mode", "unix-0600-local-only"), ("schema", "orion.security.vault.v1"),
    ("key_id", "other"), ("key_version", 2), ("nonce", base64.b64encode(b"x" * 12).decode()),
    ("protected", base64.b64encode(b"x" * 32).decode()),
])
def test_authenticated_envelope_rejects_metadata_and_ciphertext_changes(tmp_path, field, replacement):
    # Same actual key for both references: changing metadata must still break
    # the authenticated AAD, independent of whether key lookup succeeds.
    key = bytes(range(32))
    source = SyntheticKeyService({("core", 1): key, ("other", 1): key, ("core", 2): key})
    vault = SecretVault(tmp_path / "vault.json", profile="operational", provider=provider(source))
    vault.put_bytes("fixture", b"not-for-logs")
    record = json.loads(vault.path.read_bytes())
    record[field] = replacement
    vault.path.write_text(json.dumps(record))
    with pytest.raises(VaultError):
        vault.get_bytes("fixture")


def test_key_rotation_keeps_old_version_readable_then_reencrypts(tmp_path):
    source = SyntheticKeyService({("core", 1): bytes(range(32)), ("core", 2): b"r" * 32})
    path = tmp_path / "vault.json"
    old = SecretVault(path, profile="operational", provider=provider(source))
    value = old.get_or_create("consumption-ledger-key")
    newer = SecretVault(path, profile="operational", provider=provider(source, version=2))
    assert newer.get_bytes("consumption-ledger-key") == value
    with newer.transaction() as rows:
        assert "consumption-ledger-key" in rows
    assert json.loads(path.read_bytes())["key_version"] == 2
    source.keys.pop(("core", 1))
    assert newer.get_bytes("consumption-ledger-key") == value


def test_missing_old_key_never_overwrites_existing_store(tmp_path):
    path = tmp_path / "vault.json"
    first = SecretVault(path, profile="operational", provider=provider())
    first.put_bytes("fixture", b"stable")
    original = path.read_bytes()
    source = SyntheticKeyService({("core", 2): b"r" * 32})
    newer = SecretVault(path, profile="operational", provider=provider(source, version=2))
    with pytest.raises(VaultError):
        newer.get_or_create("replacement")
    assert path.read_bytes() == original


def test_provider_outage_blocks_read_and_write_without_secrets_in_errors(tmp_path, capsys, caplog):
    source = SyntheticKeyService({("core", 1): bytes(range(32))})
    vault = SecretVault(tmp_path / "vault.json", profile="operational", provider=provider(source))
    vault.put_bytes("fixture", b"stable")
    original = vault.path.read_bytes()
    source.available = False
    for operation in (lambda: vault.get_bytes("fixture"), lambda: vault.put_bytes("fixture", b"replacement")):
        with pytest.raises(VaultError) as error:
            operation()
        assert "synthetic credential" not in str(error.value)
        assert error.value.__cause__ is None
    assert vault.path.read_bytes() == original
    assert "synthetic credential" not in caplog.text + capsys.readouterr().out


def test_vault_concurrent_transactions_preserve_every_secret(tmp_path):
    path = tmp_path / "vault.json"
    source = SyntheticKeyService({("core", 1): bytes(range(32))})

    def write(index):
        vault = SecretVault(path, profile="operational", provider=provider(source))
        vault.put_bytes("fixture-" + str(index), str(index).encode())

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(write, range(40)))
    reopened = SecretVault(path, profile="operational", provider=provider(source))
    assert [reopened.get_bytes("fixture-" + str(index)) for index in range(40)] == [str(i).encode() for i in range(40)]


def test_independent_service_processes_share_encrypted_state_without_windows_bindings(tmp_path):
    path = tmp_path / "vault.json"
    program = """
import importlib.abc,json,sys
class BlockDesktop(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == 'ctypes.wintypes' or fullname.startswith(('PySide6','PyQt')):
            raise RuntimeError('desktop binding was requested by portable service')
sys.meta_path.insert(0, BlockDesktop())
from quantum_autopilot.security.secret_providers import AESGCMSecretProvider
from quantum_autopilot.security import vault as module
class SyntheticKeyService:
    def read_key(self,key_id,version):
        assert (key_id,version)==('core',1)
        return bytes(range(32))
provider=AESGCMSecretProvider(SyntheticKeyService(),active_key_id='core',active_version=1)
vault=module.SecretVault(sys.argv[1],profile='operational',provider=provider)
for index in range(8):
    vault.put_bytes(sys.argv[2]+':'+str(index),b'public-synthetic-value')
assert module._dpapi_bindings is None and module._data_blob_type is None
print(json.dumps({'desktop_loaded':False,'writes':8}))
"""
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
    workers = [subprocess.Popen([sys.executable, "-c", program, str(path), str(index)], env=environment,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
               for index in range(3)]
    for worker in workers:
        stdout, stderr = worker.communicate(timeout=30)
        assert worker.returncode == 0, stderr
        assert json.loads(stdout) == {"desktop_loaded": False, "writes": 8}
    reopened = SecretVault(path, profile="operational", provider=provider())
    for index in range(3):
        for row in range(8):
            assert reopened.get_bytes(str(index) + ":" + str(row)) == b"public-synthetic-value"


def test_portable_provider_does_not_admit_legacy_plaintext_store(tmp_path):
    path = tmp_path / "vault.json"
    legacy = {"schema": "orion.security.vault.v1", "mode": "unix-0600-local-only",
              "protected": base64.b64encode(b'{"secret":"cHVibGlj"}').decode()}
    path.write_text(json.dumps(legacy))
    if os.name == "posix":
        path.chmod(0o600)
    vault = SecretVault(path, profile="operational", provider=provider())
    with pytest.raises(VaultError, match="profile mismatch"):
        vault.get_bytes("secret")


def test_provider_rejects_extra_envelope_fields_and_invalid_key_sizes():
    active = provider()
    record = active.seal(b"public")
    with pytest.raises(VaultError):
        active.open({**record, "unexpected": "value"})
    for value in (b"x" * 16, b"x" * 33, "x" * 32):
        with pytest.raises(VaultError):
            provider(SyntheticKeyService({("core", 1): value}))


def test_unreadable_decrypted_rows_do_not_expose_parser_input(tmp_path, caplog, capsys):
    active = provider()
    path = tmp_path / "vault.json"
    path.write_text(json.dumps(active.seal(b"\xffsynthetic-secret-in-invalid-json")))
    if os.name == "posix":
        path.chmod(0o600)
    vault = SecretVault(path, profile="operational", provider=active)
    with pytest.raises(VaultError) as error:
        vault.get_bytes("fixture")
    assert error.value.__cause__ is None
    assert "synthetic-secret" not in str(error.value) + caplog.text + capsys.readouterr().out


@pytest.mark.parametrize("key_id,version", [("", 1), ("a\nb", 1), ("a", True), ("a", 0), ("a", "1")])
def test_provider_rejects_invalid_key_references(key_id, version):
    with pytest.raises(ValueError):
        AESGCMSecretProvider(SyntheticKeyService({}), active_key_id=key_id, active_version=version)


def test_mounted_key_missing_or_wrong_size_does_not_create_key(tmp_path):
    path = tmp_path / "nonexistent.key"
    source = MountedKeySource({("core", 1): path})
    with pytest.raises(VaultError):
        source.read_key("core", 1)
    assert not path.exists()
    path.write_bytes(b"wrong-size")
    path.chmod(0o600)
    source = MountedKeySource({("core", 1): path}, permissions_validator=lambda *_: None)
    with pytest.raises(VaultError, match="exactly 32"):
        source.read_key("core", 1)


def test_mounted_key_rejects_symlink_before_permission_validation(tmp_path):
    path = tmp_path / "original.key"
    path.write_bytes(bytes(range(32)))
    alias = tmp_path / "alias.key"
    try:
        alias.symlink_to(path)
    except OSError:
        pytest.skip("host does not grant symlink creation")
    source = MountedKeySource({("core", 1): alias}, permissions_validator=lambda *_: None)
    with pytest.raises(VaultError, match="symlink"):
        source.read_key("core", 1)


def test_mounted_source_rejects_nonregular_file_before_open(tmp_path, monkeypatch):
    source = MountedKeySource({("core", 1): tmp_path})
    def forbidden(*args, **kwargs):
        raise AssertionError("must not open a directory, device or FIFO")
    monkeypatch.setattr(os, "open", forbidden)
    with pytest.raises(VaultError, match="regular"):
        source.read_key("core", 1)


def test_mounted_source_requests_nonblocking_open_when_available(tmp_path, monkeypatch):
    path = tmp_path / "provided.key"
    path.write_bytes(bytes(range(32)))
    path.chmod(0o600)
    source = MountedKeySource({("core", 1): path}, permissions_validator=lambda *_: None)
    original_open = os.open
    # Verify the guard on every test host; this is not a POSIX FIFO qualification.
    nonblocking = getattr(os, "O_NONBLOCK", 1 << 29)
    monkeypatch.setattr(os, "O_NONBLOCK", nonblocking, raising=False)

    def guarded(target, flags, *args, **kwargs):
        assert flags & nonblocking
        return original_open(target, flags & ~nonblocking, *args, **kwargs)

    monkeypatch.setattr(os, "open", guarded)
    assert source.read_key("core", 1) == bytes(range(32))


@pytest.mark.skipif(os.name != "posix", reason="requires a real POSIX FIFO")
def test_posix_mounted_source_rejects_fifo_swap_without_blocking(tmp_path):
    code = """
import os, sys
from pathlib import Path
from quantum_autopilot.security.secret_providers import MountedKeySource
from quantum_autopilot.security.vault import VaultError
path = Path(sys.argv[1]) / 'provided.key'
path.write_bytes(bytes(range(32)))
path.chmod(0o600)
original_open = os.open
def replaced(target, flags, *args, **kwargs):
    path.replace(path.with_suffix('.previous'))
    os.mkfifo(path, 0o600)
    return original_open(target, flags, *args, **kwargs)
os.open = replaced
try:
    MountedKeySource({('core', 1): path}).read_key('core', 1)
except VaultError:
    print('rejected')
else:
    raise AssertionError('replacement FIFO was admitted')
"""
    # A broken implementation is killed by timeout, never hangs the test runner.
    result = subprocess.run([sys.executable, "-c", code, str(tmp_path)], timeout=5,
                            check=True, text=True, capture_output=True)
    assert result.stdout.strip() == "rejected"


@pytest.mark.parametrize("mode,uid,allowed", [
    (0o400, 1234, True), (0o600, 1234, True), (0o400, 0, True),
    (0o644, 1234, False), (0o440, 1234, False), (0o700, 1234, False),
    (0o600, 9999, False),
])
def test_posix_permission_policy_unit_cases_are_independent_of_test_host(tmp_path, monkeypatch, mode, uid, allowed):
    from quantum_autopilot.security import secret_providers as module
    # Test the POSIX policy itself on Windows, separately from the actual
    # filesystem tests below. This does not qualify a Linux installation.
    source = MountedKeySource({("core", 1): tmp_path / "provided.key"})
    monkeypatch.setattr(module, "os", SimpleNamespace(name="posix", geteuid=lambda: 1234))
    metadata = SimpleNamespace(st_mode=stat.S_IFREG | mode, st_uid=uid)
    if allowed:
        source._check_permissions(tmp_path, metadata)
    else:
        with pytest.raises(VaultError):
            source._check_permissions(tmp_path, metadata)


def test_mounted_source_rejects_file_replaced_between_lstat_and_open(tmp_path, monkeypatch):
    path = tmp_path / "provided.key"
    path.write_bytes(bytes(range(32)))
    source = MountedKeySource({("core", 1): path}, permissions_validator=lambda *_: None)
    original_open = os.open

    def replaced(target, flags, *args, **kwargs):
        Path(target).replace(tmp_path / "old.key")
        Path(target).write_bytes(b"r" * 32)
        return original_open(target, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", replaced)
    with pytest.raises(VaultError, match="changed during acquisition"):
        source.read_key("core", 1)


def test_external_acl_validator_failure_is_sanitized(tmp_path):
    path = tmp_path / "provided.key"
    path.write_bytes(bytes(range(32)))
    path.chmod(0o600)
    def unavailable(*args):
        raise RuntimeError("synthetic-credential-in-validator-error")
    source = MountedKeySource({("core", 1): path}, permissions_validator=unavailable)
    with pytest.raises(VaultError, match="permission verification failed") as error:
        source.read_key("core", 1)
    assert error.value.__cause__ is None
    assert "synthetic-credential" not in str(error.value)


def test_external_acl_validator_explicit_denial_fails_closed(tmp_path):
    path = tmp_path / "provided.key"
    path.write_bytes(bytes(range(32)))
    path.chmod(0o600)
    source = MountedKeySource({("core", 1): path}, permissions_validator=lambda *_: False)
    with pytest.raises(VaultError, match="permission verification failed"):
        source.read_key("core", 1)


def test_mounted_public_configuration_contains_only_references_and_rejects_duplicates(tmp_path):
    path = tmp_path / "provided.key"
    path.write_bytes(bytes(range(32)))
    path.chmod(0o600)
    config = {"active_key_id": "core", "active_version": 1,
              "keys": [{"key_id": "core", "version": 1, "path": str(path)}]}
    duplicate = {**config, "keys": config["keys"] * 2}
    with pytest.raises(ValueError, match="duplicate"):
        MountedSecretProvider.from_config(duplicate)
    if os.name != "posix":
        with pytest.raises(VaultError, match="ACL validator"):
            MountedSecretProvider.from_config(config)
        return
    mounted = MountedSecretProvider.from_config(config)
    public = mounted.public_configuration()
    assert public == config
    assert bytes(range(32)).hex() not in json.dumps(public)
    reconstructed = MountedSecretProvider.from_config(public)
    assert reconstructed.open(mounted.seal(b"cross-worker")) == b"cross-worker"


@pytest.mark.skipif(os.name != "posix", reason="actual POSIX filesystem permission validation")
def test_mounted_source_enforces_permissions_again_after_creation(tmp_path):
    path = tmp_path / "provided.key"
    path.write_bytes(bytes(range(32)))
    path.chmod(0o400)
    mounted = MountedSecretProvider({("core", 1): path}, active_key_id="core", active_version=1)
    path.chmod(0o644)
    with pytest.raises(VaultError, match="permissions"):
        mounted.seal(b"public")


@pytest.mark.skipif(os.name != "posix", reason="actual POSIX filesystem permission validation")
def test_mounted_source_rejects_untrusted_owner_and_nonregular_files(tmp_path, monkeypatch):
    path = tmp_path / "provided.key"
    path.write_bytes(bytes(range(32)))
    path.chmod(0o600)
    source = MountedKeySource({("core", 1): path})
    metadata = path.stat()
    values = list(metadata)
    values[4] = os.geteuid() + 12345
    with pytest.raises(VaultError, match="owner"):
        source._check_permissions(path, os.stat_result(values))
    values[0] = stat.S_IFDIR | 0o700
    with pytest.raises(VaultError, match="regular"):
        source._check_permissions(path, os.stat_result(values))
