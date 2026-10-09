"""Bounded, no-extraction verification of an untrusted operational page ZIP."""
import json
import re
import stat
import zlib
from io import BytesIO
from pathlib import Path
from zipfile import BadZipFile, ZIP_DEFLATED, ZIP_STORED, ZipFile

from .attempt_batch import MAX_BATCH_ATTEMPTS
from .attempt_file import MAX_ATTEMPT_FILE_BYTES, _hex, _nonfinite, _unique, _utc, verify_attempt_snapshot
from .campaign_attempts import validate_attempt_period, validate_attempt_status
from .temporal_campaign import validate_history_prefix
from .topology import _sha256


MAX_ATTEMPT_ARCHIVE_BYTES = 2 * 1024 * 1024
_MANIFEST_KEYS = {'schema', 'scope', 'physical_action_performed', 'page_only', 'count',
                  'exported_at_utc', 'filters', 'members'}
_FILTER_KEYS = {'campaign_prefix', 'status', 'requested_from', 'requested_before'}


def _read_json(bundle, info):
    with bundle.open(info) as stream:
        raw = stream.read(MAX_ATTEMPT_FILE_BYTES + 1)
    if len(raw) > MAX_ATTEMPT_FILE_BYTES or len(raw) != info.file_size:
        raise ValueError('invalid or oversized ZIP member')
    return json.loads(raw.decode('utf-8-sig'), object_pairs_hook=_unique, parse_constant=_nonfinite)


def _verify(bundle):
    infos = bundle.infolist()
    if not 2 <= len(infos) <= MAX_BATCH_ATTEMPTS + 1:
        raise ValueError('ZIP must contain a manifest and 1..50 attempts')
    by_name = {}
    for info in infos:
        name = info.filename
        if (name != info.orig_filename or name in by_name
                or (name != 'manifest.json' and not re.fullmatch(r'attempt-[0-9a-f]{32}\.json', name))
                or info.is_dir() or info.external_attr & 0x10
                or stat.S_IFMT(info.external_attr >> 16) not in (0, stat.S_IFREG)):
            raise ValueError('unsafe, duplicate or unexpected ZIP member')
        if (info.flag_bits & 1 or info.compress_type not in (ZIP_STORED, ZIP_DEFLATED)
                or not 0 <= info.file_size <= MAX_ATTEMPT_FILE_BYTES):
            raise ValueError('encrypted, unsupported or oversized ZIP member')
        by_name[name] = info
    if 'manifest.json' not in by_name:
        raise ValueError('missing operational manifest')
    wrapper = _read_json(bundle, by_name['manifest.json'])
    if not isinstance(wrapper, dict) or set(wrapper) != {'manifest', 'manifest_sha256'}:
        raise ValueError('exact manifest wrapper required')
    manifest = wrapper['manifest']
    if (not isinstance(manifest, dict) or set(manifest) != _MANIFEST_KEYS
            or manifest['schema'] != 'orion.campaign_attempt_page.v1'
            or manifest['scope'] != 'native_console_software_execution'
            or manifest['physical_action_performed'] is not False or manifest['page_only'] is not True
            or type(manifest['count']) is not int or not 1 <= manifest['count'] <= MAX_BATCH_ATTEMPTS
            or not isinstance(manifest['members'], list) or len(manifest['members']) != manifest['count']
            or not _hex(wrapper['manifest_sha256'], 64)):
        raise ValueError('invalid operational manifest schema, count or scope')
    _utc(manifest['exported_at_utc'])
    filters = manifest['filters']
    if not isinstance(filters, dict) or set(filters) != _FILTER_KEYS:
        raise ValueError('exact manifest filters required')
    prefix = validate_history_prefix(filters['campaign_prefix'])
    status = validate_attempt_status(filters['status'])
    since, until = validate_attempt_period(filters['requested_from'], filters['requested_before'])
    if _sha256(manifest) != wrapper['manifest_sha256']:
        raise ValueError('manifest SHA256 mismatch')
    expected = {'manifest.json'}
    for member in manifest['members']:
        if (not isinstance(member, dict) or set(member) != {'file', 'attempt_id', 'record_sha256'}
                or not _hex(member['attempt_id'], 32) or not _hex(member['record_sha256'], 64)
                or member['file'] != f"attempt-{member['attempt_id']}.json"):
            raise ValueError('invalid manifest member identity')
        name = member['file']
        if name in expected or name not in by_name:
            raise ValueError('duplicate or missing manifest member')
        expected.add(name)
    if expected != set(by_name):
        raise ValueError('ZIP contents differ from manifest')
    for member in manifest['members']:
        snapshot = verify_attempt_snapshot(_read_json(bundle, by_name[member['file']]))
        record = snapshot['record']
        if record['attempt_id'] != member['attempt_id'] or snapshot['record_sha256'] != member['record_sha256']:
            raise ValueError('member identity/hash differs from manifest')
        day = record['requested_at_utc'][:10]
        if (not record['campaign_id'].startswith(prefix) or (status and record['status'] != status)
                or (since and day < since) or (until and day >= until)):
            raise ValueError('attempt does not match declared filters')
    # Return the bounded manifest, not a large concatenation of every detail.
    # Success is emitted only after every member has been read and verified.
    return wrapper


def verify_attempt_archive(path):
    """Read <=2 MiB, check <=51 members of <=32 KiB each, never extract/import.

    CRC/JSON/schema/hash checks apply to the read snapshot and declared members.
    They do not authenticate the container, author, journal or physical execution.
    """
    with Path(path).open('rb') as stream:
        raw = stream.read(MAX_ATTEMPT_ARCHIVE_BYTES + 1)
    if len(raw) > MAX_ATTEMPT_ARCHIVE_BYTES:
        raise ValueError('operational ZIP exceeds 2 MiB')
    try:
        with ZipFile(BytesIO(raw)) as bundle:
            return _verify(bundle)
    except (BadZipFile, RuntimeError, NotImplementedError, UnicodeError, RecursionError, zlib.error) as exc:
        raise ValueError('invalid operational ZIP: ' + type(exc).__name__) from exc
