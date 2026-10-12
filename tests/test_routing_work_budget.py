"""Planning samples must depend on declared work, never host scheduling."""
from dataclasses import replace

import pytest

from quantum_autopilot.network_engine import Demand, ModelRegistry, Router, RouterConfig, effective_graph
from quantum_autopilot.network_engine import routing as rt
from tests.test_network_engine_routing import square

pytestmark = pytest.mark.unit


class Clock:
    def __init__(self, increment):
        self.value = 0.0
        self.increment = increment

    def __call__(self):
        self.value += self.increment
        return self.value


def test_real_planning_is_identical_under_different_host_clock_costs(monkeypatch):
    config = RouterConfig(episodes=12, min_episodes=3, time_budget_s=0.12)
    graph = effective_graph(square())
    demand = Demand("P1", "A", "C")
    plans = []
    for increment in (0.000001, 0.2):
        monkeypatch.setattr(rt.time, "perf_counter", Clock(increment))
        plan = Router(ModelRegistry(environ={}), config).plan(demand, graph, seed=11)
        plans.append(plan)
    assert [plan.episodes for plan in plans] == [12, 12]
    assert plans[0].numbers() | {"planning_ms": 0} == plans[1].numbers() | {"planning_ms": 0}
    assert plans[0].planning_ms != plans[1].planning_ms


def test_declared_work_is_part_of_input_fingerprint():
    config = RouterConfig(episodes=6)
    demand, graph = Demand("P1", "A", "C"), effective_graph(square())
    first = Router(ModelRegistry(environ={}), config).plan(demand, graph, seed=11)
    second = Router(ModelRegistry(environ={}), replace(config, episodes=12)).plan(demand, graph, seed=11)
    assert first.inputs_sha256 != second.inputs_sha256


@pytest.mark.parametrize("budget", [float("nan"), float("inf"), -1.0, 0.0])
def test_invalid_presentation_timing_target_is_rejected(budget):
    with pytest.raises(ValueError):
        RouterConfig(time_budget_s=budget)
