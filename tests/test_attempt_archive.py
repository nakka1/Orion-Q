"""Untrusted page ZIPs: bounded integrity verification, never import or extract."""
import json
import os
import stat
from pathlib import Path
from threading import Event
from zipfile import ZIP_BZIP2, ZIP_DEFLATED, ZIP_STORED, ZipFile, ZipInfo

import pytest

from quantum_autopilot.control_plane.attempt_archive import MAX_ATTEMPT_ARCHIVE_BYTES, verify_attempt_archive
from quantum_autopilot.control_plane.attempt_batch import export_attempt_page
from quantum_autopilot.control_plane.attempt_file import MAX_ATTEMPT_FILE_BYTES
from quantum_autopilot.control_plane.topology import SimulatedTopologyStore, _sha256
from tests import test_console_temporal_campaign as shared
from tests.test_attempt_batch import populate

app, setup, settle = shared.app, shared.setup, shared.settle


def repack(path, entries, compression=ZIP_DEFLATED):
    with ZipFile(path, 'w', compression=compression) as bundle:
        for name, raw in entries:
            bundle.writestr(name, raw)


@pytest.fixture
def exported(tmp_path):
    store = SimulatedTopologyStore(tmp_path / 'external.sqlite')
    _, selection = populate(store)
    path = tmp_path / 'page.zip'
    wrapper = export_attempt_page(store, selection, path, campaign_prefix='batch-')
    with ZipFile(path) as bundle:
        entries = [(name, bundle.read(name)) for name in bundle.namelist()]
    return path, wrapper, entries


def save_manifest(exported, rehash=True):
    path, wrapper, entries = exported
    if rehash:
        wrapper['manifest_sha256'] = _sha256(wrapper['manifest'])
    entries[0] = ('manifest.json', json.dumps(wrapper).encode())
    repack(path, entries)


@pytest.mark.parametrize('count', [1, 50])
@pytest.mark.parametrize('compression', [ZIP_STORED, ZIP_DEFLATED])
def test_roundtrip_bounded_and_no_extraction_or_journal_access(tmp_path, monkeypatch, count, compression):
    store = SimulatedTopologyStore(tmp_path / 'state')
    _, selection = populate(store, count)
    path = tmp_path / 'page.zip'
    expected = export_attempt_page(store, selection, path)
    with ZipFile(path) as bundle:
        entries = [(name, bundle.read(name)) for name in bundle.namelist()]
    repack(path, entries, compression)
    before = {p.name: p.read_bytes() for p in tmp_path.iterdir() if p.is_file()}
    def forbidden(*args, **kwargs):
        raise AssertionError('no extraction or journal access permitted')
    monkeypatch.setattr(ZipFile, 'extract', forbidden)
    monkeypatch.setattr(ZipFile, 'extractall', forbidden)
    monkeypatch.setattr(SimulatedTopologyStore, '_connect', forbidden)
    assert verify_attempt_archive(path) == expected
    assert {p.name: p.read_bytes() for p in tmp_path.iterdir() if p.is_file()} == before


@pytest.mark.parametrize('field,value', [
    ('schema', 'unknown'), ('scope', 'physical'), ('physical_action_performed', True),
    ('page_only', False), ('count', True), ('count', 2), ('count', 0), ('count', 51),
    ('members', {}), ('extra', 1), ('exported_at_utc', 'yesterday'),
    ('exported_at_utc', '2026-09-20T00:00:00-03:00'), ('filters', {}),
])
def test_rehashed_invalid_manifest_refused(exported, field, value):
    exported[1]['manifest'][field] = value
    save_manifest(exported)
    with pytest.raises(ValueError):
        verify_attempt_archive(exported[0])


@pytest.mark.parametrize('field,value', [
    ('campaign_prefix', 'other'), ('campaign_prefix', []), ('campaign_prefix', 'bad\n'),
    ('status', 'CONFIRMED'), ('status', 'bad'), ('status', []),
    ('requested_from', '9999-12-31'), ('requested_before', '0001-01-01'),
    ('requested_from', 'bad'), ('requested_before', False), ('extra', ''),
])
def test_rehashed_filters_must_match_records(exported, field, value):
    exported[1]['manifest']['filters'][field] = value
    save_manifest(exported)
    with pytest.raises(ValueError):
        verify_attempt_archive(exported[0])


@pytest.mark.parametrize('change', ['hash', 'wrapper', 'missing', 'extra', 'duplicate-member',
    'member-id', 'member-hash', 'member-file', 'duplicate-declaration', 'missing-manifest'])
def test_manifest_identity_and_exact_membership(exported, change):
    path, wrapper, entries = exported
    manifest = wrapper['manifest']
    if change == 'hash':
        wrapper['manifest_sha256'] = '0'*64
    elif change == 'wrapper':
        wrapper['extra'] = True
    elif change == 'missing':
        entries.pop()
    elif change == 'extra':
        entries.append(('attempt-' + 'f'*32 + '.json', b'{}'))
    elif change == 'duplicate-member':
        entries.append(entries[-1])
    elif change == 'member-id':
        manifest['members'][0]['attempt_id'] = '0'*32
    elif change == 'member-hash':
        manifest['members'][0]['record_sha256'] = '0'*64
    elif change == 'member-file':
        manifest['members'][0]['file'] = '../escape.json'
    elif change == 'duplicate-declaration':
        manifest['members'][1] = manifest['members'][0]
    else:
        entries.pop(0)
        repack(path, entries)
        with pytest.raises(ValueError):
            verify_attempt_archive(path)
        return
    if change == 'duplicate-member':
        with pytest.warns(UserWarning, match='Duplicate name'):
            save_manifest(exported)
    else:
        save_manifest(exported, rehash=change != 'hash')
    with pytest.raises(ValueError):
        verify_attempt_archive(path)


@pytest.mark.parametrize('name', ['../escape', '/absolute', 'C:\\escape', 'folder/file', 'folder/', 'unlisted.txt'])
def test_unsafe_names_refused(exported, name):
    path, _, entries = exported
    entries.append((name, b'{}'))
    repack(path, entries)
    with pytest.raises(ValueError):
        verify_attempt_archive(path)


@pytest.mark.parametrize('mode', [stat.S_IFLNK, stat.S_IFDIR, stat.S_IFIFO])
def test_special_member_types_refused(exported, mode):
    path, _, entries = exported
    info = ZipInfo(entries[1][0])
    info.create_system = 3
    info.external_attr = (mode | 0o600) << 16
    entries[1] = info, entries[1][1]
    repack(path, entries)
    with pytest.raises(ValueError):
        verify_attempt_archive(path)


@pytest.mark.parametrize('raw', [b'not a ZIP', b'', b'x'*(MAX_ATTEMPT_ARCHIVE_BYTES+1)],
                         ids=['invalid', 'empty', 'oversized'])
def test_invalid_or_oversized_container(tmp_path, raw):
    path = tmp_path / 'bad.zip'
    path.write_bytes(raw)
    with pytest.raises(ValueError):
        verify_attempt_archive(path)


def test_truncation_and_member_limits(exported):
    path, _, entries = exported
    original = path.read_bytes()
    path.write_bytes(original[:-30])
    with pytest.raises(ValueError):
        verify_attempt_archive(path)
    entries[1] = entries[1][0], b' '*(MAX_ATTEMPT_FILE_BYTES+1)
    repack(path, entries)
    assert path.stat().st_size < MAX_ATTEMPT_ARCHIVE_BYTES
    with pytest.raises(ValueError, match='oversized'):
        verify_attempt_archive(path)
    repack(path, [('manifest.json', b'{}')] + [(f'attempt-{i:032x}.json', b'{}') for i in range(51)])
    with pytest.raises(ValueError, match='1..50'):
        verify_attempt_archive(path)


def test_unsupported_compression_encryption_and_crc(exported):
    path, _, entries = exported
    repack(path, entries, ZIP_BZIP2)
    with pytest.raises(ValueError, match='unsupported'):
        verify_attempt_archive(path)
    repack(path, entries, ZIP_STORED)
    raw = bytearray(path.read_bytes())
    central = raw.index(b'PK\x01\x02')
    raw[central+8] |= 1  # central-directory encrypted bit
    path.write_bytes(raw)
    with pytest.raises(ValueError, match='encrypted'):
        verify_attempt_archive(path)
    repack(path, entries, ZIP_STORED)
    raw = bytearray(path.read_bytes())
    raw[raw.index(b'"manifest"')] = ord('X')  # payload byte without updating CRC
    path.write_bytes(raw)
    with pytest.raises(ValueError, match='BadZipFile'):
        verify_attempt_archive(path)


@pytest.mark.parametrize('index', [0, 1])
@pytest.mark.parametrize('raw', [b'null', b'\xff', b'{"a":1,"a":2}', b'{"a":NaN}', b'['*1200+b']'*1200],
                         ids=['null', 'encoding', 'duplicate-key', 'nonfinite', 'deep'])
def test_bad_json_in_any_member_refuses_whole_archive(exported, index, raw):
    path, _, entries = exported
    entries[index] = entries[index][0], raw
    repack(path, entries)
    with pytest.raises(ValueError):
        verify_attempt_archive(path)


def test_rehashed_edit_is_consistency_not_authentication(exported):
    path, wrapper, entries = exported
    snapshot = json.loads(entries[1][1])
    snapshot['record']['detail'] = '<b>edited by a third party</b>'
    entries[1] = entries[1][0], json.dumps(snapshot).encode()
    repack(path, entries)
    with pytest.raises(ValueError, match='SHA256'):
        verify_attempt_archive(path)
    snapshot['record_sha256'] = _sha256(snapshot['record'])
    entries[1] = entries[1][0], json.dumps(snapshot).encode()
    repack(path, entries)
    with pytest.raises(ValueError, match='differs from manifest'):
        verify_attempt_archive(path)
    wrapper['manifest']['members'][0]['record_sha256'] = snapshot['record_sha256']
    save_manifest(exported)
    assert verify_attempt_archive(path) == wrapper  # Hashes cannot establish authorship.


def test_ui_read_only_state_and_failed_preview(setup, app, exported, monkeypatch):
    service, _, job, dialog, manifest = setup
    job.load(manifest)
    request_before, review_before = job.request, job.review_text
    journal_before = service.topology_store.path.read_bytes()
    def forbidden(*args):
        raise AssertionError('no current journal access')
    monkeypatch.setattr(type(job.provider.active), 'temporal_campaign_history_store_path', forbidden)
    job.verify_attempt_archive(exported[0])
    assert job.active and not dialog.attempt_zip_button.isEnabled()
    assert not dialog.attempt_file_button.isEnabled()
    settle(app, lambda: not job.active)
    assert 'PAGE ZIP VERIFIED' in dialog.attempt_file_view.toPlainText()
    assert 'NOT AUTHENTICATED' in job.attempt_file_text and 'NOT SCIENTIFIC EVIDENCE' in job.attempt_file_text
    assert exported[1]['manifest_sha256'] in job.attempt_file_text
    assert job.request is request_before and job.review_text == review_before
    assert job.receipt is None and job.process is None and not job.attempts
    assert service.topology_store.path.read_bytes() == journal_before
    job.verify_attempt_archive(exported[0].parent / 'missing.zip')
    assert exported[1]['manifest_sha256'] not in job.attempt_file_text
    settle(app, lambda: not job.active)
    assert job.attempt_file_text.startswith('ZIP REFUSED')
    assert exported[1]['manifest_sha256'] not in dialog.attempt_file_view.toPlainText()


def test_existing_scientific_receipt_unchanged(setup, app, exported):
    from quantum_autopilot.control_plane.temporal_campaign import run_temporal_campaign
    from tests.test_temporal_campaign import request
    service, _, job, _, _ = setup
    run_temporal_campaign(service.topology_store, request())
    job.browse_history()
    settle(app, lambda: not job.active)
    job.open_history(0)
    settle(app, lambda: not job.active)
    receipt, rows, provenance = job.receipt, job.result_rows, job.provenance_text
    before = service.topology_store.path.read_bytes()
    job.verify_attempt_archive(exported[0])
    settle(app, lambda: not job.active)
    assert job.receipt is receipt and job.result_rows == rows and job.provenance_text == provenance
    assert service.topology_store.path.read_bytes() == before


def test_chooser_cancel_and_source_guard(setup, app, exported, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QFileDialog
    from quantum_autopilot.console.input_sources import MeasuredFileProvider
    _, provider, job, dialog, _ = setup
    before = job.attempt_file_text
    monkeypatch.setattr(QFileDialog, 'getOpenFileName', lambda *args: ('', ''))
    dialog.choose_attempt_archive()
    assert not job.active and job.attempt_file_text == before
    def choose(*args):
        provider.switch(lambda: MeasuredFileProvider(tmp_path / 'absent', tmp_path / 'measured'))
        return str(exported[0]), 'ZIP'
    monkeypatch.setattr(QFileDialog, 'getOpenFileName', choose)
    dialog.choose_attempt_archive()
    assert not job.active and 'source changed while choosing' in dialog.note.text()
    assert not dialog.attempt_zip_button.isEnabled()
    with pytest.raises(ValueError, match='unavailable'):
        job.verify_attempt_archive(exported[0])


@pytest.mark.parametrize('interruption', ['source', 'cancel', 'capability'])
def test_async_read_discards_late_results(setup, app, exported, tmp_path, monkeypatch, interruption):
    from dataclasses import replace
    from quantum_autopilot.console.input_sources import MeasuredFileProvider
    import quantum_autopilot.console.campaign_job as adapter
    _, provider, job, dialog, _ = setup
    entered, release = Event(), Event()
    original = adapter.verify_attempt_archive
    def slow(path):
        entered.set()
        assert release.wait(10)
        return original(path)
    monkeypatch.setattr(adapter, 'verify_attempt_archive', slow)
    job.verify_attempt_archive(exported[0])
    settle(app, entered.is_set)
    try:
        with pytest.raises(ValueError):
            job.verify_attempt_archive(exported[0])
        with pytest.raises(RuntimeError):
            job.shutdown()
        if interruption == 'source':
            provider.switch(lambda: MeasuredFileProvider(tmp_path / 'absent', tmp_path / 'measured'))
        elif interruption == 'cancel':
            job.cancel()
        else:
            caps = provider.active.capabilities
            no_history = replace(caps, operations=tuple(op for op in caps.operations if op != 'read_temporal_campaign_history'))
            monkeypatch.setattr(type(provider.active), 'capabilities', property(lambda _: no_history))
    finally:
        release.set()
    settle(app, lambda: not job.active)
    assert 'PAGE ZIP VERIFIED' not in job.attempt_file_text
    assert exported[1]['manifest_sha256'] not in dialog.attempt_file_view.toPlainText()


def test_archive_layout(setup, app, exported):
    from quantum_autopilot.console.theme import install
    install(app)
    _, _, job, dialog, _ = setup
    job.verify_attempt_archive(exported[0])
    settle(app, lambda: not job.active)
    dialog.tabs.setCurrentIndex(4)
    dialog.show()
    for width, height in ((640, 500), (1080, 740)):
        dialog.resize(width, height)
        app.processEvents()
        assert dialog.attempt_file_view.isReadOnly()
        assert dialog.attempt_file_view.viewport().height() > 60
        assert dialog.rect().contains(dialog.attempt_zip_button.mapTo(dialog, dialog.attempt_zip_button.rect().bottomRight()))
        if folder := os.environ.get('ORION_ATTEMPT_ARCHIVE_EVIDENCE'):
            Path(folder).mkdir(parents=True, exist_ok=True)
            assert dialog.grab().save(str(Path(folder) / f'archive-{width}.png'))
