"""Read reviewed, pinned archives as data, without extracting/importing their code.

Hashes identify reviewed bytes, not scientific validity or permission to actuate.
Changing an export requires a new review, never an automatic hash update.
"""
from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import stat
import zipfile

import numpy as np

PINS = {
    "qkd": "94352f15b6dcb132a3c03559b9d94b28f42b6e9adb8b2cdaab18f46720a55a79",
    "qnet": "6b8563209f54a2d566f1a4229b140b75fda93555085ff2bf6bfcad8f07abd938",
}
QKD_ROOT = "modelo_qkd_ml/"
ROUTING_ROOT = "qnet_network/qnet_network-roteamento/qnet-roteamento/"
COMPENSATION_ROOT = "qnet_network/qnet_network_compensador/qnet-compensador/"
ALLOCATION_ROOT = "qnet_network/qnet_network-alocador/qnet-alocador/"
MAX_ARCHIVE_BYTES = 32 * 1024 * 1024
MAX_EXPANDED_BYTES = 64 * 1024 * 1024


def _reject_constant(value: str):
    raise ValueError(f"non-finite JSON constant: {value}")


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _validate_members(archive: zipfile.ZipFile, *, limit: int, count: int) -> None:
    entries = archive.infolist()
    if len(entries) > count or sum(e.file_size for e in entries) > limit:
        raise ValueError("archive expansion limit exceeded")
    seen = set()
    for item in entries:
        # ZipInfo normalizes Windows separators (and truncates NULs). Inspect
        # the stored spelling, not just its platform-normalized filename.
        name = item.orig_filename
        path = PurePosixPath(name)
        if (name in seen or path.is_absolute() or ".." in path.parts or ":" in name
                or "\\" in name or "\x00" in name or item.flag_bits & 1
                or stat.S_ISLNK(item.external_attr >> 16)):
            raise ValueError("unsafe or duplicate archive member")
        seen.add(name)


class ReviewedArchive:
    """Immutable in-memory snapshot: hash checking and reads use the same bytes."""

    def __init__(self, path: str | Path, kind: str):
        if kind not in PINS:
            raise ValueError("unknown reviewed archive kind")
        with Path(path).open("rb") as stream:
            raw = stream.read(MAX_ARCHIVE_BYTES + 1)
        if len(raw) > MAX_ARCHIVE_BYTES:
            raise ValueError("archive size limit exceeded")
        self.sha256 = hashlib.sha256(raw).hexdigest()
        if self.sha256 != PINS[kind]:
            raise ValueError("archive hash differs from reviewed export; review required")
        self.kind = kind
        self._raw = raw
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            _validate_members(archive, limit=MAX_EXPANDED_BYTES, count=200)

    def read(self, name: str) -> bytes:
        # Never interpret scripts, pickle/joblib, RTL or executables as models.
        if PurePosixPath(name).suffix not in {".json", ".npz", ".csv"}:
            raise ValueError("only JSON, NPZ and CSV data may be read")
        with zipfile.ZipFile(io.BytesIO(self._raw)) as archive:
            return archive.read(name)

    def json(self, name: str):
        return json.loads(self.read(name), parse_constant=_reject_constant,
                          object_pairs_hook=_unique_object)

    def arrays(self, name: str, *, boolean_keys: frozenset[str] = frozenset(),
               positive_infinity_keys: frozenset[str] = frozenset()) -> dict[str, np.ndarray]:
        if boolean_keys & positive_infinity_keys:
            raise ValueError("array cannot be both a mask and an infinity-bearing field")
        raw = self.read(name)
        with zipfile.ZipFile(io.BytesIO(raw)) as nested:
            _validate_members(nested, limit=4 * 1024 * 1024, count=32)
        with np.load(io.BytesIO(raw), allow_pickle=False) as arrays:
            result = {}
            for key in arrays.files:
                value = arrays[key]
                if key in boolean_keys:
                    if value.dtype.kind != "b":
                        raise ValueError("declared boolean masks must have boolean dtype")
                elif key in positive_infinity_keys:
                    if value.dtype.kind not in "fi" or np.isnan(value).any() or np.isneginf(value).any():
                        raise ValueError("only finite numbers and positive infinity allowed in declared field")
                elif value.dtype.kind not in "fi" or not np.isfinite(value).all():
                    raise ValueError("weights must be finite real numeric arrays")
                result[key] = value.copy()
            if not (boolean_keys | positive_infinity_keys).issubset(result):
                raise ValueError("missing declared special array")
            return result

    def inventory(self) -> list[dict]:
        with zipfile.ZipFile(io.BytesIO(self._raw)) as archive:
            return [
                {"name": e.filename, "bytes": e.file_size,
                 "sha256": hashlib.sha256(archive.read(e)).hexdigest(),
                 "deserialization_blocked": PurePosixPath(e.filename).suffix in {".pkl", ".pickle"}}
                for e in archive.infolist() if not e.is_dir()
            ]
