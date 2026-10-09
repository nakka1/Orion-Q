"""A renamed release retains strict integrity and legacy read compatibility."""
from __future__ import annotations

import importlib.metadata
import json
from pathlib import Path
import re
import runpy
from types import SimpleNamespace

import pytest

from quantum_autopilot import windows_acceptance
from quantum_autopilot.product import EXECUTABLE_NAME, PACKAGE_DIRECTORY, PRODUCT_NAME, product_version
from tools import package_console_portable as packager


ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.unit


def create_product(release, directory, executable):
    product = release / directory
    (product / '_internal').mkdir(parents=True)
    (product / executable).write_bytes(executable.encode('ascii'))
    return product, product / executable


def test_new_layout_has_priority_over_preserved_legacy_release(tmp_path):
    legacy = create_product(tmp_path, 'ORION-Q', 'ORION-Q.exe')
    current = create_product(tmp_path, PACKAGE_DIRECTORY, EXECUTABLE_NAME)
    assert packager.release_product(tmp_path) == current
    assert legacy[1].read_bytes() == b'ORION-Q.exe'


def test_preserved_legacy_layout_is_readable_without_renaming_files(tmp_path):
    legacy = create_product(tmp_path, 'ORION-Q', 'ORION-Q.exe')
    assert packager.release_product(tmp_path) == legacy
    assert not (tmp_path / PACKAGE_DIRECTORY).exists()


@pytest.mark.parametrize('worker_exists', [True, False])
def test_legacy_release_cannot_be_repackaged_or_modified(tmp_path, monkeypatch, worker_exists):
    _, executable = create_product(tmp_path, 'ORION-Q', 'ORION-Q.exe')
    worker = tmp_path / 'worker.json'
    if worker_exists:
        worker.write_text(json.dumps({'passed': True, 'sha256': packager.digest(executable)}), encoding='utf-8')
    before = {path.relative_to(tmp_path): path.read_bytes() for path in tmp_path.rglob('*') if path.is_file()}
    monkeypatch.setattr(packager, 'put', lambda *args: pytest.fail('legacy release wrote a file'))
    monkeypatch.setattr(packager, 'ZipFile', lambda *args, **kwargs: pytest.fail('legacy release created an archive'))
    with pytest.raises(ValueError, match='Legacy releases are read-only'):
        packager.package(tmp_path, worker)
    after = {path.relative_to(tmp_path): path.read_bytes() for path in tmp_path.rglob('*') if path.is_file()}
    assert after == before
    assert not (tmp_path / PACKAGE_DIRECTORY).exists()
    assert not list(tmp_path.glob('*.zip'))


def test_partial_new_build_cannot_silently_package_previous_executable(tmp_path):
    create_product(tmp_path, 'ORION-Q', 'ORION-Q.exe')
    (tmp_path / PACKAGE_DIRECTORY).mkdir()
    with pytest.raises(FileNotFoundError, match=EXECUTABLE_NAME):
        packager.release_product(tmp_path)


def test_current_release_requires_branding_evidence_before_creating_archive(tmp_path):
    _, executable = create_product(tmp_path, PACKAGE_DIRECTORY, EXECUTABLE_NAME)
    worker = tmp_path / 'worker.json'
    worker.write_text(json.dumps({'passed': True, 'sha256': packager.digest(executable)}), encoding='utf-8')
    with pytest.raises(FileNotFoundError, match='branding-validation'):
        packager.package(tmp_path, worker)
    assert not list(tmp_path.glob('*.zip'))


def test_installer_upgrade_deletions_cannot_target_operator_external_shortcuts():
    installer = (ROOT / 'packaging/installer.iss').read_text(encoding='utf-8')
    section = re.search(r'(?ms)^\[InstallDelete\]\s*\n(.*?)(?=^\[|\Z)', installer)[1]
    entries = [line for line in section.splitlines() if line.strip() and not line.lstrip().startswith(';')]
    names = [re.search(r'Name:\s*"([^"]+)"', entry)[1] for entry in entries]
    assert names == [r'{app}\ORION-Q.exe']
    assert all(name.startswith('{app}\\') for name in names)
    # The installer creates its new shortcuts without removing legacy targets.
    icons = re.search(r'(?ms)^\[Icons\]\s*\n(.*?)(?=^\[|\Z)', installer)[1]
    assert r'Name: "{group}\{#AppName}"' in icons
    assert r'Name: "{autodesktop}\{#AppName}"' in icons
    assert '#define AppName "OrionQ"' in installer


def test_integrity_gate_chooses_new_verified_executable_when_both_are_declared(tmp_path, monkeypatch):
    product, current = create_product(tmp_path, PACKAGE_DIRECTORY, EXECUTABLE_NAME)
    (product / 'ORION-Q.exe').write_bytes(b'preserved legacy executable')
    manifest = ''.join(f'{windows_acceptance.digest(path)}  {path.name}\n'
                       for path in (current, product / 'ORION-Q.exe'))
    (product / 'SHA256SUMS.txt').write_text(manifest, encoding='utf-8')
    monkeypatch.setattr(windows_acceptance, 'invoke', lambda *args: pytest.fail('integrity-only launched a process'))
    code, _, report = windows_acceptance.accept(product, tmp_path / 'evidence', integrity_only=True)
    assert code == 0
    assert report['package']['executable'] == EXECUTABLE_NAME
    assert report['package']['executable_sha256'] == windows_acceptance.digest(current)
    assert report['automatic_passed'] is False


def test_undeclared_new_executable_cannot_bypass_legacy_manifest(tmp_path):
    product, legacy = create_product(tmp_path, 'ORION-Q', 'ORION-Q.exe')
    (product / 'SHA256SUMS.txt').write_text(f'{windows_acceptance.digest(legacy)}  ORION-Q.exe\n', encoding='utf-8')
    (product / EXECUTABLE_NAME).write_bytes(b'unverified new executable')
    with pytest.raises(ValueError, match='Undeclared'):
        windows_acceptance.verify_package(product)


def run_spec_without_build(monkeypatch, *, installed_version):
    hooks = pytest.importorskip('PyInstaller.utils.hooks')
    pytest.importorskip('PyInstaller.utils.win32.versioninfo')
    metadata = []
    monkeypatch.setattr(hooks, 'collect_data_files', lambda *args: [])
    monkeypatch.setattr(hooks, 'collect_submodules', lambda *args: [])
    monkeypatch.setattr(hooks, 'copy_metadata', lambda name: metadata.append(name) or [])
    monkeypatch.setattr(importlib.metadata, 'version', lambda name: installed_version)
    namespace = runpy.run_path(str(ROOT / 'packaging/console.spec'), init_globals={
        'SPECPATH': str(ROOT / 'packaging'),
        'Analysis': lambda *args, **kwargs: SimpleNamespace(pure=[], scripts=[], binaries=[], datas=[]),
        'PYZ': lambda *args: None,
        'EXE': lambda *args, **kwargs: kwargs,
        'COLLECT': lambda *args, **kwargs: kwargs,
    })
    return namespace, metadata


def test_freeze_spec_embeds_declared_product_version_without_building(monkeypatch):
    namespace, metadata = run_spec_without_build(monkeypatch, installed_version=product_version())
    assert namespace['exe']['name'] == PRODUCT_NAME
    assert namespace['coll']['name'] == PACKAGE_DIRECTORY
    assert 'quantum-twin' in metadata
    encoded = namespace['windows_version'].toRaw()
    for value in (EXECUTABLE_NAME, PRODUCT_NAME, product_version()):
        assert value.encode('utf-16le') in encoded
    parts = tuple(int(part) for part in product_version().split('.'))
    assert namespace['version_tuple'] == parts + (0,) * (4 - len(parts))


def test_freeze_spec_rejects_stale_installed_version_before_build(monkeypatch):
    with pytest.raises(ValueError, match='metadata is stale'):
        run_spec_without_build(monkeypatch, installed_version='5.34.0')
