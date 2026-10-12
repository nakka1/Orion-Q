"""Durable pair lifecycle races; quantum observations remain simulated."""
from contextlib import closing
from dataclasses import replace
import sqlite3

import pytest

from quantum_autopilot.control_plane.sqlite_transaction import connect
from quantum_autopilot.control_plane.topology import SimulatedTopologyStore
from quantum_autopilot.network_engine import ModelRegistry, NetworkEngine
from tests.test_network_engine_engine import demand, make, triangle

pytestmark = pytest.mark.unit


def independent_engine(path):
    return NetworkEngine(SimulatedTopologyStore(path), path,
                         registry=ModelRegistry(environ={}), seed=7)


def test_failed_second_copy_receipt_tracks_concurrent_consumption(tmp_path, monkeypatch):
    engine, _, path = make(tmp_path, triangle(), demands=(("P1", "A", "B"),))
    first = engine.command("request_pair", {"demand_id": "P1"})["pair"]
    competitor = independent_engine(path)
    real_episode = engine._pair_episode

    def second_copy_lost(demand_record, purpose):
        plan, ran = real_episode(demand_record, purpose)
        assert purpose == "purify" and ran is not None
        result, *rest = ran
        competitor.command("consume_pair", {"demand_id": "P1"})
        # Explicit failure injection at the second acquisition, after another
        # connection commits consumption of the first stored pair.
        return plan, (replace(result, delivered=False, state=None, fidelity=None,
                              outcome="deadline"), *rest)

    monkeypatch.setattr(engine, "_pair_episode", second_copy_lost)
    result = engine.command("purify_pair", {"demand_id": "P1"})
    durable = engine.demands.pair("P1")
    assert result["status"] == "no_second_copy"
    assert durable["pair_id"] == first["pair_id"] and durable["status"] == "consumed"
    assert result["pair"] == demand(engine.live_state()).pair == durable
    assert result["affected_demands"][0]["pair"] == durable
    assert result["physical_action_performed"] is False


def test_failed_database_update_rolls_back_consumption_and_allows_retry(tmp_path):
    engine, _, path = make(tmp_path, triangle(), demands=(("P1", "A", "B"),))
    original = engine.command("request_pair", {"demand_id": "P1"})["pair"]
    with closing(connect(path)) as db, db:
        db.execute("CREATE TRIGGER reject_consumption BEFORE UPDATE OF pair_json "
                   "ON network_demands WHEN NEW.pair_json LIKE '%consumed%' "
                   "BEGIN SELECT RAISE(ABORT, 'injected durable write failure'); END")
    with pytest.raises(sqlite3.IntegrityError, match="injected durable write failure"):
        engine.command("consume_pair", {"demand_id": "P1"})
    assert engine.demands.pair("P1") == original
    assert not any(event.kind == "pair_consumed" for event in engine.live_state().events)
    with closing(connect(path)) as db, db:
        db.execute("DROP TRIGGER reject_consumption")
    consumed = independent_engine(path).command("consume_pair", {"demand_id": "P1"})
    assert consumed["pair"]["pair_id"] == original["pair_id"]
    assert engine.demands.pair("P1")["status"] == "consumed"


def test_postcommit_publication_failure_cannot_reactivate_pair(tmp_path, monkeypatch):
    engine, _, path = make(tmp_path, triangle(), demands=(("P1", "A", "B"),))
    original = engine.command("request_pair", {"demand_id": "P1"})["pair"]
    refresh = engine._refresh

    def publication_failed(trigger, **kwargs):
        if trigger == "pair consumed":
            raise OSError("injected publication interruption after durable commit")
        return refresh(trigger, **kwargs)

    monkeypatch.setattr(engine, "_refresh", publication_failed)
    with pytest.raises(OSError, match="after durable commit"):
        engine.command("consume_pair", {"demand_id": "P1"})
    durable = engine.demands.pair("P1")
    assert durable["status"] == "consumed" and durable["pair_id"] == original["pair_id"]
    restarted = independent_engine(path)
    assert demand(restarted.live_state()).pair == durable
    with pytest.raises(ValueError, match="no delivered pair to consume"):
        restarted.command("consume_pair", {"demand_id": "P1"})
