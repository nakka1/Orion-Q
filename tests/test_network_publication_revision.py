"""Causal presentation markers remain separate from network clock and durable data."""
from types import SimpleNamespace

import pytest

from quantum_autopilot.console.contract import LiveView
from quantum_autopilot.network_engine.state import LiveState, SourceInfo
from quantum_autopilot.qkd.dashboard_api import AssuranceDashboardService
from tests.test_fpga_devices import make_engine

pytestmark = pytest.mark.unit


def test_legacy_live_records_and_positional_views_default_to_no_publication_markers():
    old = {"tick": 0, "at_utc": "2026-10-08T00:00:00+00:00", "network_time_s": 0,
           "topology_version": 0, "source": {"kind": "none", "label": "no input"}}
    state = LiveState.from_dict(old)
    assert state.engine_session_id is None and state.publication_revision is None
    assert LiveState.from_json(state.to_json()) == state
    view = LiveView(0, 0., "none", "no input")
    assert view.engine_session_id is None and view.publication_revision is None


@pytest.mark.parametrize("changes", [{"engine_session_id": "invalid"}, {"publication_revision": True},
                                    {"publication_revision": 0}])
def test_invalid_publication_markers_rejected(changes):
    with pytest.raises(ValueError):
        LiveState(0, "2026-10-08T00:00:00+00:00", 0., 0, SourceInfo("none", "no input"), **changes)


def test_engine_publication_revision_increases_on_commands_and_source_changes(tmp_path):
    engine = make_engine(tmp_path)
    initial = engine.live_state()
    assert len(initial.engine_session_id) == 32 and initial.publication_revision == 1
    engine.command("configure_network_input", {"kind": "none"})
    commanded = engine.live_state()
    assert commanded.tick == initial.tick == 0
    assert commanded.engine_session_id == initial.engine_session_id
    assert commanded.publication_revision > initial.publication_revision
    stepped = engine.step()
    assert stepped.tick == 1 and stepped.publication_revision > commanded.publication_revision
    engine.command("configure_network_input", {"kind": "simulated", "seed": 19, "scenario": "quiet"})
    changed = engine.live_state()
    assert changed.engine_session_id == initial.engine_session_id
    assert changed.tick == stepped.tick and changed.publication_revision > stepped.publication_revision
    # Publication markers stay causal when an explicit in-memory reset lowers time.
    with engine._lock:
        reset = engine._publish(engine._topology, 0, changed.at_utc, [], [], {})
    assert reset.tick == 0 and reset.publication_revision > changed.publication_revision
    assert engine.live_state() is reset and LiveState.from_json(reset.to_json()) == reset
    restarted = make_engine(tmp_path)
    assert restarted.live_state().engine_session_id != initial.engine_session_id
    assert restarted.live_state().publication_revision == 1


def test_metadata_descriptor_preserves_publication_markers_without_copying_arrays():
    state = LiveState(0, "2026-10-08T00:00:00+00:00", 0., 0, SourceInfo("none", "no input"),
                      engine_session_id="a" * 32, publication_revision=7)
    fixture = SimpleNamespace(network_engine=SimpleNamespace(timings={}))
    metadata = AssuranceDashboardService._network_metadata(fixture, state)
    assert metadata["engine_session_id"] == state.engine_session_id
    assert metadata["publication_revision"] == 7
    assert "links" in metadata and metadata["links"] == 0
    assert "technologies" not in metadata
