"""Identity checks remain data-only, pinned, bounded and distinct from installation."""
import hashlib
import pickle
import zipfile

import pytest

from quantum_autopilot.model_intake import artifacts, catalog
from quantum_autopilot.model_intake.verification import verify_model_archive

pytestmark = pytest.mark.unit


@pytest.fixture
def archive(tmp_path, monkeypatch):
    path = tmp_path / "fixture.zip"
    with zipfile.ZipFile(path, "w") as target:
        target.writestr("weights.npz", b"opaque numeric export; do not deserialize during identity check")
        target.writestr("detector.pkl", b"untrusted")
        target.writestr("run.py", b"raise RuntimeError('must never execute')")
    monkeypatch.setitem(artifacts.PINS, "qkd", hashlib.sha256(path.read_bytes()).hexdigest())
    return path


def test_verified_identity_is_not_installation_or_activation(archive, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("model construction or deserialization during identity check")

    monkeypatch.setattr(pickle, "load", forbidden)
    monkeypatch.setattr(pickle, "loads", forbidden)
    monkeypatch.setattr(catalog, "open_optional_model", forbidden)
    monkeypatch.setattr(artifacts.ReviewedArchive, "arrays", forbidden)
    monkeypatch.setattr(artifacts.ReviewedArchive, "json", forbidden)
    before = archive.read_bytes()
    result = verify_model_archive("qkd.keyrate.actor", archive)
    assert result.sha256 == hashlib.sha256(before).hexdigest()
    assert result.member_count == 3
    assert result.blocked_pickle_members == ("detector.pkl",)
    assert result.adapter_status == "offline_only"
    assert result.path == str(archive.resolve())
    assert result.checked_at_utc.endswith("+00:00")
    assert "NOT INSTALLED; NOT ACTIVE" in result.describe()
    assert "verify its bytes again" in result.describe()
    assert archive.read_bytes() == before
    assert list(archive.parent.iterdir()) == [archive]  # no extraction/install


def test_detector_package_check_still_never_deserialises_the_pickle(archive):
    result = verify_model_archive("qkd.detector.boosting", archive)
    assert result.adapter_status == "offline_only"   # admitted through the safe export, not the pickle
    assert result.blocked_pickle_members == ("detector.pkl",)
    assert "deserialization blocked" in result.describe()


def test_wrong_family_refused(archive):
    with pytest.raises(ValueError, match="hash differs"):
        verify_model_archive("qnet.routing.nominal", archive)


def test_file_changed_after_check_requires_new_verification(archive):
    previous = verify_model_archive("qkd.keyrate.actor", archive)
    archive.write_bytes(b"replaced since snapshot")
    with pytest.raises(ValueError, match="hash differs"):
        verify_model_archive("qkd.keyrate.actor", archive)
    assert previous.sha256 == artifacts.PINS["qkd"]  # only a historical snapshot


@pytest.mark.parametrize("path", ["", "//server/share/file.zip", r"\\server\share\file.zip",
                                  r"\\.\pipe\model", "https://example.invalid/model.zip"])
def test_nonlocal_paths_refused_before_filesystem_access(monkeypatch, path):
    monkeypatch.setattr(artifacts.Path, "is_file", lambda *a: pytest.fail("stat nonlocal input"))
    with pytest.raises(ValueError, match="local ZIP"):
        verify_model_archive("qkd.keyrate.actor", path)


def test_missing_or_directory_refused(tmp_path):
    for path in (tmp_path, tmp_path / "missing.zip"):
        with pytest.raises(ValueError, match="existing regular"):
            verify_model_archive("qkd.keyrate.actor", path)


def test_unknown_model_refused_before_file_read(monkeypatch):
    monkeypatch.setattr(artifacts.Path, "is_file", lambda *a: pytest.fail("stat unknown model"))
    with pytest.raises(ValueError, match="unknown optional"):
        verify_model_archive("unknown", "not-read.zip")


def test_inventory_detects_crc_damage_even_with_test_pin(archive, monkeypatch):
    # Test-only pin isolates the CRC guard; production never updates a failed pin.
    raw = archive.read_bytes().replace(b"untrusted", b"UNTRUSTED", 1)
    archive.write_bytes(raw)
    monkeypatch.setitem(artifacts.PINS, "qkd", hashlib.sha256(raw).hexdigest())
    with pytest.raises(zipfile.BadZipFile, match="CRC"):
        verify_model_archive("qkd.keyrate.actor", archive)


def test_size_bound_reused(archive, monkeypatch):
    monkeypatch.setattr(artifacts, "MAX_ARCHIVE_BYTES", 1)
    with pytest.raises(ValueError, match="size limit"):
        verify_model_archive("qkd.keyrate.actor", archive)
