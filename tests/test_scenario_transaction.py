"""A scenario confirms complete durable state, never partly written steps."""
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest

from quantum_autopilot.control_plane.journal import ControlJournal
from quantum_autopilot.control_plane.runtime import ControllerRuntime
from quantum_autopilot.control_plane.topology import (
    SimulatedTopologyStore, TopologyScenarioRunner, resilience_demo_scenario,
)


@pytest.mark.unit
@pytest.mark.parametrize("failure", ["second_cycle","final_receipt"])
@pytest.mark.parametrize("existing", [False, True])
def test_failed_scenario_rolls_back_cycles_topology_events_and_receipt(tmp_path, monkeypatch, failure, existing):
    path=tmp_path/'scenario.sqlite3'
    runtime=ControllerRuntime(path)
    store=SimulatedTopologyStore(path)
    runner=TopologyScenarioRunner(runtime,store)
    if existing:
        runner.run(resilience_demo_scenario('previous'))
    # The preset's default topology ID is part of its frozen definition.
    scenario=resilience_demo_scenario('failing')
    before_topology=store.get(scenario.topology_id)
    before_events=store.list_events(scenario.topology_id) if before_topology else []
    before_cycles=runtime.journal.list_recent()
    if failure == 'second_cycle':
        cycle=runtime.cycle
        count=0
        def fail_after_one(*args, **kwargs):
            nonlocal count
            count+=1
            if count==2:
                raise RuntimeError('injected cycle failure')
            return cycle(*args,**kwargs)
        monkeypatch.setattr(runtime,'cycle',fail_after_one)
    else:
        def fail_receipt(*args, **kwargs):
            raise RuntimeError('injected receipt failure')
        monkeypatch.setattr(store,'append_scenario_run',fail_receipt)
    with pytest.raises(RuntimeError,match='injected'):
        runner.run(scenario)
    reopened_store=SimulatedTopologyStore(path)
    after_topology=reopened_store.get(scenario.topology_id)
    assert (after_topology.to_dict() if after_topology else None)==(before_topology.to_dict() if before_topology else None)
    assert reopened_store.list_events(scenario.topology_id)==before_events
    assert ControlJournal(path).list_recent()==before_cycles
    assert reopened_store.scenario_run(scenario.scenario_id) is None


@pytest.mark.unit
def test_scenario_and_store_update_finish_without_cross_thread_lock_inversion(tmp_path, monkeypatch):
    path=tmp_path/'concurrent.sqlite3'
    runtime=ControllerRuntime(path)
    store=SimulatedTopologyStore(path)
    runner=TopologyScenarioRunner(runtime,store)
    scenario=resilience_demo_scenario('concurrent')
    entered,release=Event(),Event()
    original=runtime.cycle
    def pause_once(*args,**kwargs):
        if not entered.is_set():
            entered.set()
            assert release.wait(3)
        return original(*args,**kwargs)
    monkeypatch.setattr(runtime,'cycle',pause_once)
    with ThreadPoolExecutor(max_workers=2) as pool:
        scenario_future=pool.submit(runner.run,scenario)
        assert entered.wait(3)
        update_future=pool.submit(store.reset,scenario.topology_id)
        release.set()
        result=scenario_future.result(timeout=5)
        updated=update_future.result(timeout=5)
    assert result['record']['final_topology']['topology_version']==6
    assert updated.topology_version==7
    assert result['record']['physical_action_performed'] is False


@pytest.mark.unit
def test_schema_reopen_inside_scenario_does_not_commit_borrowed_connection(tmp_path):
    path=tmp_path/'borrowed.sqlite3'
    runtime=ControllerRuntime(path)
    store=SimulatedTopologyStore(path)
    with pytest.raises(RuntimeError,match='rollback'):
        with runtime.scenario_transaction(path):
            store.reset('rollback-topology')
            SimulatedTopologyStore(path)
            ControlJournal(path)
            raise RuntimeError('rollback')
    assert SimulatedTopologyStore(path).get('rollback-topology') is None
