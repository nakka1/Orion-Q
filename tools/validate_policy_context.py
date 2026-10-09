"""Capture contextual Q05 observations in a new isolated simulator; never run cycles."""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path

from quantum_autopilot.control_plane.contracts import NetworkStateSnapshot
from quantum_autopilot.control_plane.policy_context import TARGET_POLICY_ID
from quantum_autopilot.control_plane.topology import default_simulated_topology
from quantum_autopilot.core import Provenance, EvidenceKind
from quantum_autopilot.qkd.dashboard_api import AssuranceDashboardService


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True, help="New isolated diagnostic directory")
    args = parser.parse_args(argv)
    args.output_dir.mkdir(parents=True, exist_ok=False)
    service = AssuranceDashboardService(control_journal_path=args.output_dir / "fixture.sqlite")
    runtime = service.controller_runtime
    topology, _ = service.topology_store.initialize(default_simulated_topology())
    node = next(n for n in topology.nodes if n.role.value == "repeater")
    context = service.policy_compatibility_context()
    before = runtime.journal.path.read_bytes()
    records = {}
    for candidate, label in ((TARGET_POLICY_ID, "baseline"), ("qnet.routing.nominal", "routing")):
        stamp = datetime.now(timezone.utc).isoformat()
        state = NetworkStateSnapshot(
            request_id="context-diagnostic",
            node_id=node.node_id,
            timestamp_utc=stamp,
            state_version=node.state_version,
            predicted_fidelity=0.9,
            prediction_uncertainty=0.01,
            telemetry_age_s=0.0,
            memory_state=node.memory_state,
            available_pair_fidelities=node.available_pair_fidelities,
            memory_ages_s=node.memory_ages_s,
            telemetry=node.telemetry,
            integrity_verified=True,
            operator_interlock=False,
            provenance=Provenance(
                evidence_kind=EvidenceKind.SIMULATED,
                source="isolated-context-diagnostic",
                timestamp=stamp,
                metadata=dict(
                    topology_id=topology.topology_id,
                    evidence_lane="bell_fidelity_control",
                    target_semantics="simulated_future_bell_fidelity",
                    physical_calibration=False,
                ),
            ),
        )
        payload = dict(
            mode="simulation",
            candidate_id=candidate,
            snapshot=state.to_dict(),
            expected_context_sha256=context["record"]["context_sha256"],
        )
        result = service.observe_policy_compatibility(payload)
        assert result["record"]["status"] == (
            "OBSERVED_SUPPORTED_INPUT" if label == "baseline" else "BLOCKED"
        )
        assert not result["record"]["selection_available"]
        records[label] = result
        with (args.output_dir / (label + ".json")).open("x", encoding="utf-8") as stream:
            json.dump(result, stream, indent=2)
    service.invalidate_policy_preparation()
    try:
        service.observe_policy_compatibility(payload)
    except ValueError:
        invalidated = True
    else:
        raise AssertionError("old source reference accepted")
    assert runtime.journal.path.read_bytes() == before
    report = dict(
        passed=True,
        evidence="isolated simulated software diagnostic",
        statuses={k: v["record"]["status"] for k, v in records.items()},
        source_invalidation_refused=invalidated,
        journal_unchanged=True,
        cycles_executed=0,
        models_loaded=False,
        physical_action_performed=False,
    )
    with (args.output_dir / "report.json").open("x", encoding="utf-8") as stream:
        json.dump(report, stream, indent=2)
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
