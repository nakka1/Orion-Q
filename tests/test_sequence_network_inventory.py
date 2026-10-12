"""Census/mapping contracts; manufactured fixtures are not scientific evidence."""
from copy import deepcopy
import importlib.util
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
spec = importlib.util.spec_from_file_location("sequence_network_inventory_contract", ROOT / "tools/sequence_network_inventory.py")
inventory = importlib.util.module_from_spec(spec)
spec.loader.exec_module(inventory)


def fixture():
    """One fictional two-span terrestrial session plus unsupported satellite."""
    session_id = "FIXTURE-ENT"
    bank_id = f"sim-resource:{session_id}"
    nodes = [{"node_id": ident, "role": "endpoint" if ident in ("A", "B") else "repeater", "telemetry": {
        "communicator": {"physical_type": "installation", "operational_state": "enabled"},
        "simulation_resource_banks": [{"resource_bank_id": bank_id, "memory_modes": 8}]}}
        for ident in ("A", "R", "B")]
    nodes.append({"node_id": "SAT", "telemetry": {"communicator": {"physical_type": "satellite", "operational_state": "enabled"}}})
    links = [{"link_id": ident, "left_node_id": a, "right_node_id": b, "distance_km": length,
        "active": True, "channels": {"classical": {"enabled": True}, "quantum": {"enabled": True}},
        "technology": {"session_id": session_id, "technologies": ["entanglement_swapping"]}}
        for ident, a, b, length in (("AR", "A", "R", 10.), ("RB", "R", "B", 20.))]
    links.extend({"link_id": ident, "left_node_id": "SAT", "right_node_id": ground, "distance_km": 0.,
                  "active": True, "channels": {"classical": {"enabled": True}, "quantum": {"enabled": True}},
                  "technology": {"session_id": "FIXTURE-SAT", "technologies": ["satellite_doppler"]}}
                 for ident, ground in (("SA", "A"), ("SB", "B")))
    sessions = [{"session_id": session_id, "technologies": ["entanglement_swapping", "quantum_memory", "purification"],
        "endpoints": ["A", "B"], "path": ["A", "R", "B"], "link_ids": ["AR", "RB"], "enabled": True,
        "parameters": {"resource_bank_id": bank_id, "resource_bank_scope": "dedicated_per_session", "memory_slots": 8,
                       "attempt_rate_hz": 250000., "source_depolarization": .04, "coherence_s": 1.,
                       "memory_read_efficiency": .7, "attenuation_db_km": .2, "route_factor": 1.2,
                       "insertion_loss_db": 3., "min_fidelity": .7}},
        {"session_id": "FIXTURE-SAT", "technologies": ["satellite_doppler"], "endpoints": ["A", "B"],
         "path": ["A", "SAT", "B"], "link_ids": ["SA", "SB"], "parameters": {}}]
    return {"schema": inventory.INVENTORY_SCHEMA, "nodes": nodes, "links": links, "sessions": sessions,
            "institutional_endpoints": ["A", "B"]}


def test_native_mapping_is_explicit_and_preserves_unsupported_sessions():
    census = fixture()
    before = deepcopy(census)
    mapping = inventory.native_mapping(census)
    assert mapping["equivalence_claimed"] is False
    assert len(mapping["session_mappings"]) == 1
    assert mapping["session_mappings"][0]["native_path"] == ["A::FIXTURE-ENT", "R::FIXTURE-ENT", "B::FIXTURE-ENT"]
    assert mapping["other_sessions"][0]["native_quantum_validation"] == "not_demonstrated"
    assert "not measured output fidelity" in mapping["conversions"]["source_fidelity"]
    assert census == before


def test_native_request_uses_real_declared_parameters_without_acceleration():
    request = inventory.build_native_request(fixture())
    assert request["schema"] == "orionq.sequence-network-input.v1"
    assert len(request["nodes"]) == 3 and len(request["links"]) == 2 and len(request["demands"]) == 1
    assert all(row["memory_frequency_hz"] == 250000. for row in request["nodes"])
    assert all(row["memory_count"] == 8 and row["raw_fidelity"] == .97 for row in request["nodes"])
    assert all(row["memory_efficiency"] == .7 and row["memory_coherence_s"] == 1. for row in request["nodes"])
    assert [row["length_km"] for row in request["links"]] == [12., 24.]
    assert request["links"][0]["insertion_loss_db"] == 3.
    assert request["links"][0]["classical_delay_s"] == pytest.approx(12. / inventory.FIBRE_SPEED_KM_S + .001)
    assert request["detector"]["dark_count_hz"] == 0.
    assert request["demands"][0]["request_s"] == 0.


def test_request_is_accepted_by_native_contract_without_loading_simulator():
    from sequence_network_backend import validate_payload
    validate_payload(inventory.build_native_request(fixture()))


def test_static_endpoint_census_does_not_claim_dynamic_or_orbital_availability():
    rows = inventory.endpoint_route_census(fixture())
    assert len(rows) == 1 and rows[0]["configured_path_present"]
    assert rows[0]["dynamic_availability"] == "not_evaluated"
    assert rows[0]["global_quantum_driver_available"] is True
    assert rows[0]["dedicated_entanglement_sessions"] == ["FIXTURE-ENT"]
    assert "not measured routing costs" in rows[0]["routing_scope"]


def test_separate_qkd_or_satellite_services_are_not_global_quantum_drivers():
    census = fixture()
    census["sessions"] = census["sessions"][1:]
    rows = inventory.endpoint_route_census(census)
    assert rows[0]["global_quantum_driver_available"] is False
    assert rows[0]["local_protocol_sessions"] == ["FIXTURE-SAT"]
    assert "cannot be concatenated" in rows[0]["quantum_scope"]


def test_disabled_channels_are_preserved_in_native_request():
    census = fixture()
    census["links"][0]["channels"]["classical"]["enabled"] = False
    census["links"][1]["channels"]["quantum"]["enabled"] = False
    request = inventory.build_native_request(census)
    assert request["links"][0]["classical_enabled"] is False
    assert request["links"][1]["enabled"] is False


@pytest.mark.parametrize("damage", ["node_duplicate", "absent_node", "path_link", "missing_bank", "shared_bank", "bank_capacity"])
def test_invalid_or_ambiguous_resource_mapping_is_rejected(damage):
    census = fixture()
    if damage == "node_duplicate":
        census["nodes"].append(deepcopy(census["nodes"][0]))
    elif damage == "absent_node":
        census["links"][0]["left_node_id"] = "ABSENT"
    elif damage == "path_link":
        census["sessions"][0]["link_ids"] = ["AR", "SA"]
    elif damage == "missing_bank":
        census["nodes"][0]["telemetry"]["simulation_resource_banks"] = []
    elif damage == "shared_bank":
        census["sessions"][0]["parameters"]["resource_bank_scope"] = "shared"
    elif damage == "bank_capacity":
        census["nodes"][0]["telemetry"]["simulation_resource_banks"][0]["memory_modes"] = 7
    with pytest.raises(ValueError):
        inventory.build_native_request(census)


@pytest.mark.parametrize("options", [{"seed": True}, {"duration_s": float("nan")},
    {"duration_s": .04}, {"demand_start_s": 0.}, {"demand_memory_count": 5}, {"detector_efficiency": 1.1}])
def test_bad_native_controls_rejected(options):
    with pytest.raises(ValueError):
        inventory.build_native_request(fixture(), **options)


def test_dedicated_session_banks_are_not_merged_at_shared_institution():
    census = fixture()
    second = deepcopy(census["sessions"][0])
    second["session_id"] = "FIXTURE-SECOND"
    second["parameters"]["resource_bank_id"] = "sim-resource:FIXTURE-SECOND"
    second["link_ids"] = ["AR2", "RB2"]
    census["sessions"].append(second)
    for old, new in zip(deepcopy(census["links"][:2]), second["link_ids"]):
        old["link_id"] = new
        old["technology"]["session_id"] = second["session_id"]
        census["links"].append(old)
    for node in census["nodes"][:3]:
        node["telemetry"]["simulation_resource_banks"].append({"resource_bank_id": "sim-resource:FIXTURE-SECOND", "memory_modes": 8})
    request = inventory.build_native_request(census)
    assert len(request["nodes"]) == 6
    assert {node["id"] for node in request["nodes"]} == {f"{physical}::{session}" for physical in ("A", "R", "B") for session in ("FIXTURE-ENT", "FIXTURE-SECOND")}


def test_existing_workspace_is_refused_before_production_bootstrap(tmp_path):
    state = tmp_path / "operator"
    state.mkdir()
    marker = state / "preserve.txt"
    marker.write_text("untouched")
    with pytest.raises(ValueError, match="fresh empty"):
        inventory.build_inventory(state)
    assert marker.read_text() == "untouched" and list(state.iterdir()) == [marker]


@pytest.mark.integration
def test_fresh_production_bootstrap_exports_full_current_demo(tmp_path):
    census = inventory.build_inventory(tmp_path / "isolated-demo", repository_root=ROOT)
    assert census["counts"]["nodes"] == 76 and census["counts"]["links"] == 136
    assert census["counts"]["communicators"] == 76 and census["counts"]["sessions"] == 38
    assert census["counts"]["institutional_endpoints"] == 20 and census["counts"]["global_demand_templates"] == 78
    assert census["counts"]["node_physical_types"] == {
        "installation": 20, "quantum_repeater": 39, "measurement_station": 4,
        "ground_station": 2, "satellite": 11}
    assert len(census["endpoint_route_census"]) == 190
    assert len(census["native_mapping"]["session_mappings"]) == 13
    assert len(census["native_mapping"]["other_sessions"]) == 25
    assert census["observed_network_ticks"] == 0 and not census["dynamic_route_availability_evaluated"]
    assert not census["physical_actuation"] and census["certified_secret_bits"] == 0
    assert "UNIJORGE" not in {node["node_id"] for node in census["nodes"]}
    from sequence_network_backend import validate_payload
    request = inventory.build_native_request(census)
    validate_payload(request)
    assert len(request["demands"]) == 13
