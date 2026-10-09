"""Bounded artificial-input campaign against the existing local controller.

This is software fault injection, not a photonic experiment or evidence for the
WDM hypothesis. It never constructs a hardware adapter or contacts an external
quantum service. IBM Quantum integration is discontinued.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import random
from time import perf_counter
from uuid import uuid4


def run_controller_stress(repository_root, output_root, *, seed=1337, rounds=20, cancel=None):
    from quantum_autopilot.qkd.dashboard_api import AssuranceDashboardService

    if isinstance(rounds, bool) or not isinstance(rounds, int) or not 1 <= rounds <= 200:
        raise ValueError("rounds must be between 1 and 200")
    if isinstance(seed, bool) or not isinstance(seed, int) or not 0 <= seed < 2**32:
        raise ValueError("seed must be a uint32")
    run_id = f"synthetic-controller-{uuid4().hex}"
    root = Path(output_root).resolve() / run_id
    root.mkdir(parents=True, exist_ok=False)
    service = AssuranceDashboardService(repository_root=repository_root,
        control_journal_path=root / "controller.sqlite")
    rng = random.Random(seed)
    checks, latencies, actions, hashes = [], [], {}, []
    started = perf_counter()
    cancelled = False

    def check(name, passed, detail=""):
        checks.append({"name": name, "passed": bool(passed), "detail": str(detail)})

    def request(case, index, **overrides):
        snap = {
            "request_id": f"{run_id}-{index}-{case}", "node_id": "repeater-a",
            "timestamp_utc": datetime.now(timezone.utc).isoformat(), "state_version": 1,
            "predicted_fidelity": 0.9 + rng.uniform(-0.005, 0.005),
            "prediction_uncertainty": 0.01, "telemetry_age_s": 0.1,
            "memory_state": "STORING", "available_pair_fidelities": [0.9, 0.9],
            "memory_ages_s": [0.0, 0.0], "telemetry": {"source": "artificial_stress"},
            "integrity_verified": True, "operator_interlock": True,
            "out_of_distribution": False,
            "provenance": {"evidence_kind": "simulated", "source": run_id,
                "metadata": {"evidence_lane": "bell_fidelity_control",
                             "target_semantics": "simulated_future_bell_fidelity"}},
        }
        snap.update(overrides)
        return {"mode": "shadow", "snapshot": snap}

    cases = [
        ("nominal", {}, False),
        ("stale", {"telemetry_age_s": 60.0}, True),
        ("uncertain", {"prediction_uncertainty": 0.8}, True),
        ("ood", {"out_of_distribution": True}, True),
        ("integrity", {"integrity_verified": False}, True),
        ("interlock", {"operator_interlock": False}, True),
        ("empty_memory", {"memory_state": "IDLE", "available_pair_fidelities": [], "memory_ages_s": []}, False),
    ]
    for index in range(rounds):
        if cancel is not None and cancel.is_set():
            cancelled = True
            break
        for name, overrides, must_halt in cases:
            payload = request(name, index, **overrides)
            tick = perf_counter()
            result = service.run_control_cycle(payload)
            latencies.append((perf_counter() - tick) * 1000)
            record = result["record"]
            effective = record["safety"]["effective_action"]
            actions[effective] = actions.get(effective, 0) + 1
            hashes.append(result["record_sha256"])
            check(f"{index}:{name}:nonphysical", not record["execution"]["physical_action_performed"])
            if must_halt:
                check(f"{index}:{name}:halt", effective == "HALT", effective)
            replay = service.run_control_cycle(payload)
            check(f"{index}:{name}:idempotent", replay["idempotent_replay"] and
                  replay["record_sha256"] == result["record_sha256"])
            conflict = deepcopy(payload)
            conflict["snapshot"]["telemetry_age_s"] += 0.01
            try:
                service.run_control_cycle(conflict)
            except ValueError:
                check(f"{index}:{name}:conflict_refused", True)
            else:
                check(f"{index}:{name}:conflict_refused", False)

    if not cancelled:
        for name, value in (("nan", float("nan")), ("infinite", float("inf")),
                            ("negative", -0.1), ("above_one", 1.1)):
            try:
                service.run_control_cycle(request(name, rounds, predicted_fidelity=value))
            except ValueError:
                check(f"invalid:{name}:refused", True)
            else:
                check(f"invalid:{name}:refused", False)
        physical = request("physical_refusal", rounds)
        physical["mode"] = "physical"
        record = service.run_control_cycle(physical)["record"]
        check("physical_request_refused", record["execution"]["status"] == "PHYSICAL_REFUSED"
              and not record["execution"]["physical_action_performed"])
        scenario = service.run_control_scenario({"scenario_id": run_id, "preset": "resilience_demo",
                                               "reset_topology": True})
        check("scenario_persisted", service.control_plane_topology()["persisted"])
        from quantum_autopilot.control_plane.journal import ControlJournal
        fresh = ControlJournal(root / "controller.sqlite")
        check("journal_reopens", len(fresh.list_recent(limit=20)) > 0)
        from quantum_autopilot.control_plane.contracts import ControlAction, ControlIntent, ControlMode, NetworkStateSnapshot
        state = NetworkStateSnapshot.from_dict(request("executor_probe", rounds)["snapshot"])
        executor_checks = []
        for action in ControlAction:
            intent = ControlIntent(intent_id=f"{run_id}-{action.value}", request_id=state.request_id,
                node_id=state.node_id, action=action, expected_state_version=state.state_version,
                issued_at_utc=state.timestamp_utc, policy_name="explicit_synthetic_executor_probe")
            safety = service.controller_runtime.supervisor.assess(ControlMode.SIMULATION, intent, state)
            check(f"executor:{action.value}:safety", safety.effective_action == action)
            if safety.effective_action == action:
                result = service.controller_runtime.simulated_executor.execute(intent, state, mode=ControlMode.SIMULATION)
                check(f"executor:{action.value}:simulated", result.performed_in_simulator and not result.physical_action_performed)
                executor_checks.append(result.to_dict())
        with (root / "executor_probes.json").open("x", encoding="utf-8") as handle:
            json.dump(executor_checks, handle, indent=2, allow_nan=False)
        # Keep the real scenario record separate from artificial display signals.
        with (root / "scenario.json").open("x", encoding="utf-8") as handle:
            json.dump(scenario, handle, indent=2, allow_nan=False)

    report = {
        "schema": "orion_q_synthetic_controller_stress_v1", "seed": seed, "rounds_requested": rounds,
        "scope": "software_fault_injection_only", "physical_action_performed": False,
        "cancelled": cancelled, "passed": bool(checks) and not cancelled and all(c["passed"] for c in checks),
        "checks": checks, "check_count": len(checks), "cycle_count": len(hashes), "actions": actions,
        "elapsed_s": perf_counter() - started,
        "latency_ms": {"min": min(latencies, default=0), "max": max(latencies, default=0),
                       "mean": sum(latencies) / max(1, len(latencies))},
        "journal": str(root / "controller.sqlite"), "record_hashes": hashes,
        "not_tested": ["live_network", "FPGA", "HSM", "authentication", "hardware_timing"],
    }
    report_path = root / "report.json"
    report["report_path"] = str(report_path)
    with report_path.open("x", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2, allow_nan=False)
    return report
