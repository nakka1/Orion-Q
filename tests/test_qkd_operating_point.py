"""Exact-cell and provenance tests for the QKD operating-grid boundary."""

import json
from pathlib import Path

import pytest

from quantum_autopilot.core import EvidenceKind, Provenance
from quantum_autopilot.qkd import (
    GRID_CONFIG_RELATIVE_PATH,
    GRID_CONFIG_SHA256,
    IrelandQCIGridClassifier,
    QKDOperatingPoint,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
CAMPAIGN_ID = "roadm_3dbm_injected_signal_no_added_fibre_full_sweep"
CAMPAIGN_SHA256 = "0c145f7d70356a9449301f96fa28328b55b34ff6b27750d2d58f1bf19bbe19b8"
GRID_SOURCE_SHA256 = "afe5d110e8e32694591a6a454b6e5a84410e88b24992a193d31822133a0afe4b"


def point(**overrides):
    values = {
        "dataset_id": "irelandqci-toshiba-qkd-coexistence-21132088",
        "campaign_id": CAMPAIGN_ID,
        "device_id": "not_published",
        "channel_id": "not_published",
        "external_signal_present": True,
        "external_signal_power_dbm": 3.0,
        "fiber_length_km": 0.0,
        "voa_attenuation_db": 10.0,
        "qber_reported": 0.033308,
        "qber_unit_status": "publisher_scale_ambiguous",
        "secure_key_rate_bps": 302860.36,
        "observation_level": "published_cell_mean",
        "observation_time_utc": None,
        "time_basis": "not_published",
        "provenance": Provenance(
            evidence_kind=EvidenceKind.MEASURED,
            source="doi:10.5281/zenodo.21132088",
            timestamp="2026-08-27T00:00:00+00:00",
            dataset_hash=GRID_SOURCE_SHA256,
            artifact_hashes={"campaign_source": CAMPAIGN_SHA256},
        ),
    }
    values.update(overrides)
    return QKDOperatingPoint(**values)


@pytest.fixture
def classifier():
    return IrelandQCIGridClassifier(repository_root=REPOSITORY_ROOT)


@pytest.mark.unit
def test_exact_published_cell_and_source_hash_are_inside(classifier):
    result = classifier.classify(point())
    assert result.scope == "inside_validated_grid"
    assert result.grid_valid
    assert result.grid_config_sha256 == GRID_CONFIG_SHA256
    assert result.source_artifact_verified
    assert result.matched_cell["outcomes_used_for_membership"] is False
    assert not result.violations


@pytest.mark.unit
@pytest.mark.parametrize(
    ("override", "constraint"),
    [
        ({"external_signal_power_dbm": 5.0}, "external_signal_power_dbm"),
        ({"voa_attenuation_db": 11.0}, "voa_attenuation_db"),
        ({"fiber_length_km": 50.0}, "fiber_length_km"),
        ({"dataset_id": "another-platform"}, "dataset_id"),
        ({"campaign_id": "new-campaign"}, "campaign_id"),
        ({"channel_id": "channel-42"}, "channel_id"),
        ({"device_id": "new-device"}, "device_id"),
        ({"qber_unit_status": "ratio"}, "qber_unit_status"),
    ],
)
def test_unseen_or_unverifiable_dimensions_are_outside(classifier, override, constraint):
    result = classifier.classify(point(**override))
    assert result.scope == "outside_validated_grid"
    assert any(item["constraint"] == constraint for item in result.violations)


@pytest.mark.unit
def test_outcomes_do_not_circularly_define_grid_membership(classifier):
    result = classifier.classify(point(qber_reported=99.0, secure_key_rate_bps=0.0))
    assert result.scope == "inside_validated_grid"
    assert result.matched_cell["outcomes_used_for_membership"] is False


@pytest.mark.unit
def test_wrong_campaign_source_hash_is_outside(classifier):
    wrong_provenance = Provenance(
        evidence_kind=EvidenceKind.MEASURED,
        source="doi:10.5281/zenodo.21132088",
        timestamp="2026-08-27T00:00:00+00:00",
        dataset_hash="a" * 64,
    )
    result = classifier.classify(point(provenance=wrong_provenance))
    assert result.scope == "outside_validated_grid"
    assert any(
        item["code"] == "campaign_source_hash_not_verified"
        for item in result.violations
    )


@pytest.mark.unit
def test_simulated_provenance_is_rejected_before_classification():
    simulated = Provenance(
        evidence_kind=EvidenceKind.SIMULATED,
        source="simulator",
        timestamp="2026-08-27T00:00:00+00:00",
        dataset_hash="a" * 64,
    )
    with pytest.raises(ValueError, match="measured provenance"):
        point(provenance=simulated)


@pytest.mark.unit
def test_provenance_requires_a_real_sha256():
    missing_hash = Provenance(
        evidence_kind=EvidenceKind.MEASURED,
        source="measurement.csv",
        timestamp="2026-08-27T00:00:00+00:00",
    )
    with pytest.raises(ValueError, match="dataset or artifact SHA-256"):
        point(provenance=missing_hash)


@pytest.mark.unit
def test_baseline_is_null_power_not_zero_dbm():
    with pytest.raises(ValueError, match="0 dBm is not baseline"):
        point(external_signal_present=False, external_signal_power_dbm=0.0)


@pytest.mark.unit
def test_measurement_time_requires_timezone_aware_timestamp():
    with pytest.raises(ValueError, match="include a timezone"):
        point(time_basis="measurement_time", observation_time_utc="2026-08-27T10:00:00")


@pytest.mark.unit
def test_point_round_trip_preserves_measured_provenance():
    original = point()
    restored = QKDOperatingPoint.from_dict(original.to_dict())
    assert restored.to_dict() == original.to_dict()
    assert restored.provenance.evidence_kind is EvidenceKind.MEASURED


@pytest.mark.unit
def test_unknown_operating_point_schema_is_rejected():
    with pytest.raises(ValueError, match="schema_version"):
        point(schema_version="2.0.0")


@pytest.mark.unit
def test_grid_config_is_pinned_and_source_cells_are_cross_checked(classifier):
    result = classifier.classify(point())
    assert result.grid_config_sha256 == GRID_CONFIG_SHA256
    assert result.source_artifact_available is True
    assert result.source_artifact_verified is True


@pytest.mark.unit
def test_tampered_grid_fails_closed(tmp_path):
    source = REPOSITORY_ROOT / GRID_CONFIG_RELATIVE_PATH
    payload = json.loads(source.read_text(encoding="utf-8"))
    payload["interpolation_allowed"] = True
    destination = tmp_path / GRID_CONFIG_RELATIVE_PATH
    destination.parent.mkdir(parents=True)
    destination.write_text(json.dumps(payload), encoding="utf-8")
    classifier = IrelandQCIGridClassifier(repository_root=tmp_path)

    result = classifier.classify(point())

    assert result.scope == "unknown"
    assert not result.grid_valid
    assert result.violations[0]["code"] == "operating_grid_integrity_failed"


@pytest.mark.unit
def test_grid_path_cannot_escape_repository(tmp_path):
    with pytest.raises(ValueError, match="inside repository_root"):
        IrelandQCIGridClassifier(
            repository_root=tmp_path,
            grid_path=REPOSITORY_ROOT / GRID_CONFIG_RELATIVE_PATH,
        )
