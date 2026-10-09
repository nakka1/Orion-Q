"""Structural interfaces implemented by future domain adapters."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from quantum_autopilot.core.contracts import (
    Budget,
    CandidatePlan,
    Decision,
    Outcome,
    SLO,
    SystemSnapshot,
    Workload,
)


class Adapter(Protocol):
    def observe(self, workload: Workload) -> SystemSnapshot: ...

    def propose_plans(
        self, snapshot: SystemSnapshot, workload: Workload
    ) -> Sequence[CandidatePlan]: ...


class Policy(Protocol):
    def decide(
        self,
        plans: Sequence[CandidatePlan],
        slo: SLO | None = None,
        budget: Budget | None = None,
    ) -> Decision: ...


class Executor(Protocol):
    def execute(self, decision: Decision, workload: Workload) -> Outcome: ...
