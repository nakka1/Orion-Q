"""The console's view model must not invent, convert or round evidence."""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from quantum_autopilot.console.service_provider import ServiceProvider  # noqa: E402


class FakeService:
    """Minimal stand-in exposing the AssuranceDashboardService surface."""

    def __init__(self, **overrides):
        self._overrides = overrides
        self.cycles: list[dict] = []

    def _value(self, name, default):
        value = self._overrides.get(name, default)
        if isinstance(value, Exception):
            raise value
        return value

    def health(self):
        return self._value("health", {
            "status": "ok",
            "api_version": "1.3.0",
            "autopilot_version": "0.18.0",
            "physical_read_only": True,
            "automation_allowed": False,
            "control_plane_sandbox_available": True,
            "wdm_model_bundle_configured": False,
            "local_mutation_scope": "sandbox_journal_and_simulated_topology_only",
            "generated_at": "2026-01-01T00:00:00+00:00",
        })

    def control_plane_topology(self):
        return self._value("topology", {
            "persisted": True,
            "physical_topology": False,
            "topology": {
                "topology_id": "sandbox-line-3",
                "topology_version": 6,
                "schema_version": "1.0.0",
                "updated_at_utc": "2026-01-01T00:00:00+00:00",
                "nodes": [
                    {"node_id": "alice", "role": "endpoint", "state_version": 0,
                     "memory_state": "IDLE", "available_pair_fidelities": [], "memory_ages_s": []},
                    {"node_id": "repeater-a", "role": "repeater", "state_version": 2,
                     "memory_state": "STORING", "available_pair_fidelities": [0.91, 0.62],
                     "memory_ages_s": [0.2, 6.1]},
                    {"node_id": "bob", "role": "endpoint", "state_version": 0,
                     "memory_state": "IDLE", "available_pair_fidelities": [], "memory_ages_s": []},
                ],
                "links": [
                    {"link_id": "alice-repeater-a", "left_node_id": "alice",
                     "right_node_id": "repeater-a", "distance_km": 20.0,
                     "nominal_pair_fidelity": 0.9, "active": True},
                    {"link_id": "repeater-a-bob", "left_node_id": "repeater-a",
                     "right_node_id": "bob", "distance_km": 20.0,
                     "nominal_pair_fidelity": 0.9, "active": False},
                ],
            },
        })

    def control_plane_events(self, limit=20):
        return self._value("events", {"events": [
            {"sequence": 2, "mode": "shadow", "action": "HALT", "status": "not_executed",
             "record_sha256": "a" * 64, "completed_at_utc": "2026-01-01T00:00:02+00:00"},
            {"sequence": 1, "mode": "simulation", "action": "SWAP", "status": "executed",
             "record_sha256": "b" * 64, "completed_at_utc": "2026-01-01T00:00:01+00:00"},
        ]})

    def wdm_model_status(self):
        return self._value("model", {"configured": False, "valid": False,
                                     "reason": "no_server_configured_bundle"})

    def evidence_lanes(self):
        return self._value("lanes", {"lane_count": 3, "lanes": [
            {"lane": "wdm_optical_health", "evidence_kind": "measured",
             "decision_scope": "monitoring only", "control_plane_ingress_allowed": False},
            {"lane": "qkd_assurance", "evidence_kind": "measured",
             "decision_scope": "decision support", "control_plane_ingress_allowed": False},
            {"lane": "bell_fidelity_control", "evidence_kind": "simulated",
             "decision_scope": "non-physical sandbox", "control_plane_ingress_allowed": True},
        ]})

    def summary(self):
        return self._value("summary", {
            "integrity": {"valid": True, "registry_sha256": "c" * 64, "grid_config_sha256": "d" * 64,
                          "check_count": 40, "failed_check_count": 0, "evidence_item_count": 91},
            "operating_grid": {
                "grid_id": "irelandqci-v1", "dataset_id": "ds-1", "doi": "10.0/x",
                "license": "CC-BY", "expected_cell_count": 12, "interpolation_allowed": False,
                "qber_unit_status": "percent", "limitations": [],
                "source_artifact": {"path": "data/reference/x.csv", "sha256": "e" * 64},
                "campaigns": [
                    {"campaign_id": "c-dark", "fiber_length_km": 10.0, "external_signal_present": False,
                     "external_signal_power_dbm": None, "voa_attenuation_db": [0.0, 3.0],
                     "source_file_sha256": "f" * 64},
                ],
            },
        })

    def example_decision(self):
        return self._value("example", {"record": {}, "source_cell": {
            "qber_reported": 0.0231, "secure_key_rate_bps": 1450.0}})

    def run_wdm_model_replay(self, request):
        return self._value("replay", ValueError("no server-configured bundle"))

    def run_control_cycle(self, request):
        self.cycles.append(request)
        return {"record": {"safety": {"effective_action": "HALT"}}}

    @property
    def controller_runtime(self):
        return self._value("controller_runtime", _FakeRuntime())


class _FakeJournal:
    def append_safety_event(self, kind, actor, at_utc, detail):
        return "f" * 64

    def list_recent_safety_events(self, limit=50):
        return [
            {"sequence": 2, "event_sha256": "a" * 64, "kind": "interlock_auto_engaged",
             "actor": "system", "at_utc": "2026-01-01T00:00:05+00:00",
             "detail": {"reason": "watchdog_timeout"}},
            {"sequence": 1, "event_sha256": "b" * 64, "kind": "interlock_released",
             "actor": "OP-2291", "at_utc": "2026-01-01T00:00:01+00:00", "detail": {}},
        ]


class _FakeRuntime:
    journal = _FakeJournal()


def build(**overrides):
    return ServiceProvider(FakeService(**overrides), poll_ms=10_000).build()


@pytest.mark.unit
def test_sandbox_links_never_borrow_qkd_metrics():
    snapshot = build()

    assert snapshot.topology.ring == "sandbox-line-3"
    assert [link.qber for link in snapshot.topology.links] == ["not in this lane"] * 2
    assert [link.skr for link in snapshot.topology.links] == ["not in this lane"] * 2
    assert snapshot.topology.links_up == "1/2"


@pytest.mark.unit
def test_absent_model_bundle_renders_no_trace_instead_of_an_invented_one():
    snapshot = build()

    assert snapshot.telemetry.fidelity.series == []
    assert snapshot.telemetry.window_note == "MODEL REPLAY UNAVAILABLE"
    assert "No prediction" in snapshot.telemetry.claim.claim


@pytest.mark.unit
def test_registry_failure_degrades_the_keys_screen_with_a_reason():
    snapshot = build(summary=ValueError("operating grid failed integrity validation"))

    assert snapshot.keys.status == "GRID UNAVAILABLE"
    assert "integrity" in snapshot.keys.sessions_note
    assert snapshot.keys.pipeline == []


@pytest.mark.unit
def test_journal_entries_carry_their_record_hash_and_action_tone():
    snapshot = build()
    entry = snapshot.ledger.entries[0]

    assert entry.digest == "a" * 16
    assert entry.action.startswith("HALT")
    assert entry.tone == "red"
    assert snapshot.ledger.chain_verified == "40/40 REGISTRY CHECKS"


@pytest.mark.unit
def test_safety_events_appear_in_the_ledger_with_a_reason_specific_tone():
    snapshot = build()
    kinds = {e.action: e.tone for e in snapshot.ledger.entries}

    watchdog_entry = next(a for a in kinds if "watchdog" in a.lower())
    assert kinds[watchdog_entry] == "red"
    released_entry = next(a for a in kinds if "released" in a.lower())
    assert kinds[released_entry] == "amber"
    assert "SAFETY EVENTS" in [kv.key for kv in snapshot.ledger.sessions]


@pytest.mark.unit
def test_node_detail_flags_memory_age_beyond_the_safety_limit():
    provider = ServiceProvider(FakeService(), poll_ms=10_000)
    provider.select_node("repeater-a")
    node = provider.build().node

    assert node.id == "repeater-a"
    assert [meter.tone for meter in node.calibration] == ["amber", "red"]
    assert node.registers == ["violet", "red"]


@pytest.mark.unit
def test_console_commands_never_request_a_physical_action():
    service = FakeService()
    provider = ServiceProvider(service, poll_ms=10_000)

    provider.recalibrate("repeater-a")
    provider.isolate("repeater-a")

    assert [request["mode"] for request in service.cycles] == ["shadow"]
    assert service.cycles[0]["snapshot"]["provenance"]["evidence_kind"] == "simulated"


@pytest.mark.unit
def test_unimplemented_decision_source_is_refused_without_success_record(monkeypatch):
    appended = []
    monkeypatch.setattr(
        _FakeRuntime.journal,
        "append_safety_event",
        lambda kind, actor, at_utc, detail: appended.append((kind, actor, at_utc, detail)),
    )
    provider = ServiceProvider(FakeService(), poll_ms=10_000)

    for transition in ("ml:deterministic", "physical:ml"):
        with pytest.raises(ValueError, match="Runtime policy binding unavailable"):
            provider.set_decision_source(transition)
    assert appended == []
