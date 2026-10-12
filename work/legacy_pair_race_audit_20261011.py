"""Reproduce legacy simulated pair race with two engines on one SQLite file."""
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import sys
import tempfile
from threading import Barrier

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT)]

from quantum_autopilot.network_engine import NetworkEngine, ModelRegistry
from quantum_autopilot.control_plane.topology import (
    NodeRole, SimulatedLink, SimulatedNodeState, SimulatedTopology, SimulatedTopologyStore,
)


def make(path):
    journal = path / "journal.sqlite"
    topology = SimulatedTopology("sandbox-line-3", 0,
        tuple(SimulatedNodeState(name, NodeRole.ENDPOINT, 0, "IDLE") for name in "AB"),
        (SimulatedLink("A-B", "A", "B", 5.0, .9),))
    store = SimulatedTopologyStore(journal)
    store.initialize(topology)
    engine = NetworkEngine(store, journal, registry=ModelRegistry(environ={}), seed=7)
    engine.command("define_pair", {"demand_id": "P1", "a": "A", "b": "B"})
    return engine, store, journal


def main():
    with tempfile.TemporaryDirectory(prefix="legacy-pair-audit-", dir=ROOT / "work") as area:
        first, store, journal = make(Path(area))
        pair = first.command("request_pair", {"demand_id": "P1"})["pair"]
        assert pair["status"] == "delivered"
        second = NetworkEngine(store, journal, registry=ModelRegistry(environ={}), seed=7)
        assert second.demands.pair("P1")["status"] == "delivered"
        barrier = Barrier(2)
        at_barrier = []
        for engine in (first, second):
            original = engine.demands.set_pair
            def racing_set_pair(demand_id, update, *, memory_generations=None, original=original, engine=engine):
                if update is not None and update.get("status") == "consumed":
                    at_barrier.append({"engine_session_id": engine._engine_session_id,
                                       "persisted_status_before_write": engine.demands.pair(demand_id)["status"],
                                       "memory_generations": memory_generations})
                    barrier.wait(timeout=10)
                return original(demand_id, update, memory_generations=memory_generations)
            engine.demands.set_pair = racing_set_pair
        def consume(engine):
            return engine.command("consume_pair", {"demand_id": "P1"})
        with ThreadPoolExecutor(max_workers=2) as executor:
            outcomes = list(executor.map(consume, (first, second)))
        assert [row["status"] for row in outcomes] == ["consumed", "consumed"]
        assert [row["persisted_status_before_write"] for row in at_barrier] == ["delivered", "delivered"]
        persisted = first.demands.pair("P1")
        assert persisted["status"] == "consumed"
        output = {"source_root": str(ROOT), "scope": "legacy simulated pair, two independent NetworkEngine instances, same database/topology/demand",
                  "engine_sessions_distinct": first._engine_session_id != second._engine_session_id,
                  "initial_status": pair["status"], "initial_fidelity": pair["fidelity"],
                  "before_updates": at_barrier,
                  "consume_results": [{"status": row["status"], "pair_status": row["pair"]["status"]} for row in outcomes],
                  "persisted_final_status": persisted["status"],
                  "physical_hardware_tested": False}
        single, _, _ = make(Path(area) / "single")
        assert single.command("request_pair", {"demand_id": "P1"})["pair"]["status"] == "delivered"
        start = Barrier(2)
        def same_instance_consume(_):
            start.wait(timeout=10)
            try:
                return single.command("consume_pair", {"demand_id": "P1"})["status"]
            except ValueError as error:
                assert "no delivered pair" in str(error)
                return "ValueError:no_delivered_pair"
        with ThreadPoolExecutor(max_workers=2) as executor:
            control = list(executor.map(same_instance_consume, range(2)))
        assert sorted(control) == ["ValueError:no_delivered_pair", "consumed"]
        output["same_instance_control"] = sorted(control)
    (ROOT / "work" / "legacy_pair_race_audit_20261011.json").write_text(json.dumps(output, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(output, indent=2))


if __name__ == "__main__":
    main()
