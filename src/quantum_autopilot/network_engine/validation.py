"""Local regression diagnostics shared by source and frozen acceptance runs."""
from pathlib import Path
from tempfile import mkdtemp

from ..control_plane.topology import (DEFAULT_TOPOLOGY_ID, NodeRole, SimulatedLink, SimulatedNodeState,
                                      SimulatedTopology, SimulatedTopologyStore)
from .engine import NetworkEngine
from .models import ModelRegistry
from .sources import SimulatedInputSource


def _engine(directory, *, distance_km=5.0, registry=None, simulated=False):
    directory = Path(directory).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    path = Path(mkdtemp(prefix="run-", dir=directory)) / "journal.sqlite3"
    store = SimulatedTopologyStore(path)
    store.initialize(SimulatedTopology(DEFAULT_TOPOLOGY_ID, 0,
        tuple(SimulatedNodeState(name, NodeRole.ENDPOINT, 0, "IDLE") for name in ("A", "B")),
        (SimulatedLink("A-B", "A", "B", distance_km, 0.9),)))
    engine = NetworkEngine(store, path, registry=registry or ModelRegistry(environ={}), seed=7,
                           source=SimulatedInputSource(3, "quiet") if simulated else None)
    return engine, store, path


def validate_memory_and_distance(directory) -> dict:
    """Exercise the persisted regressions without external models or operator state."""
    engine, store, path = _engine(directory)
    engine.command("define_pair", {"demand_id": "P1", "a": "A", "b": "B"})
    pair = engine.command("request_pair", {"demand_id": "P1"})["pair"]
    checks = {"pair_delivered": pair["status"] == "delivered"}
    for isolated in (True, False):
        engine.command("set_node_isolation", {"node_id": "A", "isolated": isolated})
    checks["isolation_invalidates_stock"] = engine.live_state().demands[0].pair["status"] == "invalidated"
    try:
        engine.command("consume_pair", {"demand_id": "P1"})
    except ValueError:
        checks["consumption_after_reset_refused"] = True
    else:
        checks["consumption_after_reset_refused"] = False
    pair = engine.command("request_pair", {"demand_id": "P1"})["pair"]
    checks["fresh_pair_after_restore_delivered"] = pair["status"] == "delivered"
    engine.run_calibration({"node_id": "B"})
    checks["calibration_invalidates_stock"] = engine.live_state().demands[0].pair["status"] == "invalidated"
    restarted = NetworkEngine(store, path, registry=ModelRegistry(environ={}), seed=7)
    checks["invalidated_stock_survives_restart"] = restarted.live_state().demands[0].pair["status"] == "invalidated"
    for node_id, longitude in (("A", 0.0), ("B", 180.0)):
        restarted.command("set_node_location", {"node_id": node_id, "latitude": 0.0, "longitude": longitude})
    live = restarted.live_state()
    live.to_json()  # rejects non-finite numbers, including a fabricated infinite deadline
    checks["zero_probability_blocks_demand"] = (
        live.demands[0].status == "blocked" and "probability" in live.demands[0].reason)
    checks["persisted_and_published_versions_agree"] = live.topology_version == store.get(DEFAULT_TOPOLOGY_ID).topology_version
    again = NetworkEngine(store, path, registry=ModelRegistry(environ={}), seed=7)
    checks["zero_probability_does_not_break_restart"] = again.step().demands[0].status == "blocked"
    return {"passed": all(checks.values()), "checks": checks, "physical_action_performed": False,
            "workspace": str(path.parent),
            "scope": "isolated nominal routing episodes and persisted memory resets"}


def validate_model_retention(directory, *, qnet_zip, qkd_zip) -> dict:
    """Explicit verified archives only; a simulated fibre, no operator journals or policies."""
    registry = ModelRegistry(environ={})
    states = [registry.set_archive(kind, path) for kind, path in (("qnet", qnet_zip), ("qkd", qkd_zip))]
    if any(state.state != "verified" for state in states):
        raise ValueError("both pinned reviewed archives are required for the retention diagnostic")
    engine, _, _ = _engine(directory, distance_km=50.0, registry=registry, simulated=True)
    first = engine.step()
    roles = ("compensation", "sync", "keyrate")
    before = {status.role: status for status in first.models}
    checks = {"reviewed_controllers_active": all(before[role].state == "active" for role in roles)}
    for kind in ("qnet", "qkd"):
        engine.command("set_model_archive", {"kind": kind, "path": None})
    after = {status.role: status for status in engine.step().models}
    checks["retained_controller_identity_and_hash"] = all(
        after[role].state == "active" and after[role].archive_sha256 == before[role].archive_sha256
        and after[role].inferences > before[role].inferences and "recommission" in after[role].reason
        for role in roles)
    engine.source.actuate({"A-B": engine.bank.reset_link("A-B")})
    reset = {status.role: status for status in engine.step().models}
    checks["recommission_adopts_reference"] = all(
        reset[role].state == "unavailable" and reset[role].archive_sha256 is None for role in roles)
    checks["no_physical_action"] = all(not decision.physical_action_performed for decision in engine.live_state().decisions)
    return {"passed": all(checks.values()), "checks": checks, "physical_action_performed": False,
            "archive_sha256": {state.kind: state.sha256 for state in states}}
