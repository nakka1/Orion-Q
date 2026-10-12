"""Authenticated integration of recorded native SeQUeNCe and emulated bench input.

No connected Swabian exists in this campaign. N01 remains without a verified
classical bridge: it must not magically communicate with the native ten-node ring.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import tempfile
from threading import Thread

from quantum_autopilot.control_plane.mixed_contracts import ControllerObservation, digest
from quantum_autopilot.lab.adapters import EmulatedPhotonAdapter
from quantum_autopilot.lab.mixed_sources import normalize_acquisition, normalize_sequence_callbacks
from quantum_autopilot.lab.photon import PhotonConfig
from quantum_autopilot.lab.processing_node import NodeProcessingPipeline, ProcessingNodeConfig
from quantum_autopilot.lab.pspl import CorrelationRequest, DMAConfig, TimestampBlock, TimestampEvent
from quantum_autopilot.security import SecurityRuntime
from quantum_autopilot.security.pki import OperationalPKI
from quantum_autopilot.security.secret_providers import AESGCMSecretProvider
from quantum_autopilot.security.service_identity import ServiceIdentityBinding, ServiceIdentityProvision
from quantum_autopilot.service.client import CoreClient, CoreRequestError
from quantum_autopilot.service.config import CoreConfig
from quantum_autopilot.service.runtime import create_core_server
from quantum_autopilot.service.tls import leaf_descriptor


class _FixtureSecretSource:
    def __init__(self):
        self.key = os.urandom(32)

    def read_key(self, key_id, version):
        if (key_id, version) != ("fixture", 1):
            raise ValueError("unexpected fixture key")
        return self.key


def _persist_record(path, record):
    """Acknowledge a new campaign record only after flushing its complete bytes."""
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(record, stream, sort_keys=True, allow_nan=False)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())


def process_observations(records, output, *, gateway_config=None):
    """Same timestamp-block pipeline for every node; no source/brand dispatch."""
    output.mkdir()
    publications, receipts, comparisons = [], {}, {}
    node_ids = sorted({item["observation"]["node_id"] for item in records})
    for node_id in node_ids:
        node_records = [item for item in records if item["observation"]["node_id"] == node_id]
        started = next(item["observation"] for item in node_records if item["observation"]["kind"] == "session_started")
        tags = [item for item in node_records if item["observation"]["kind"] == "timetags"]
        events = tuple(TimestampEvent(tag["timestamp_ps"], tag["channel"])
                       for item in tags for tag in item["observation"]["data"]["events"])
        provenance = tags[0]["provenance"] if tags else node_records[0]["provenance"]
        block = TimestampBlock(started["session_id"], 0, started["clock_domain"],
            started["data"]["configuration_id"], 0, max((e.timestamp_ps for e in events), default=0) + 1,
            events, provenance["origin"], provenance["source_id"], provenance["adapter_id"],
            lost_events=sum(item["observation"]["data"]["lost_events"] for item in tags),
            loss_count_exact=True)
        correlation = CorrelationRequest(channel_a=1, channel_b=2)
        results = []
        workers = ("cpu-reference", "external-pl" if gateway_config else "emulated-pl-v1")
        for worker in workers:
            registry = None
            if worker == "external-pl":
                from quantum_autopilot.service.pl_gateway_cli import registry_for_gateway
                registry = registry_for_gateway(gateway_config)
            config = ProcessingNodeConfig(node_id, block.session_id, block.clock_domain,
                block.configuration_id, correlation, worker_id=worker,
                dma=DMAConfig(max_events_per_block=gateway_config.backend.max_events) if gateway_config else DMAConfig())
            sink_path = output / f"{node_id}-{worker}-record.json"
            pipeline = NodeProcessingPipeline(config, registry=registry,
                on_record=lambda record, path=sink_path: _persist_record(path, record))
            try:
                result = pipeline.process(block, now_ps=0, completion_ps=1)
                _persist_record(output / f"{node_id}-{worker}-receipt.json", result)
            finally:
                close = getattr(pipeline.worker, "close", None)
                if callable(close):
                    close()
            results.append(result)
        comparisons[node_id] = results[0]["metrics"] == results[1]["metrics"] and all(r["accepted"] for r in results)
        receipts[node_id] = results[-1] if gateway_config else results[0]
        last = max((item["observation"] for item in node_records), key=lambda item: item["sequence"])
        data = {"block_sha256": results[0]["block_wire_sha256"], "configuration_id": block.configuration_id,
                "first_channel": 1, "second_channel": 2, "window_ps": correlation.radius_ps,
                "event_count": len(events), "coincidence_pairs": results[0]["metrics"]["coincidence_pairs"]}
        observation = ControllerObservation(f"{block.session_id}-analysis", node_id, block.session_id,
            last["sequence"] + 1, last["timestamp_ps"], block.clock_domain, "correlation", data)
        publications.append({"observation": observation.to_dict(),
                             "provenance": {**provenance, "record_sha256": results[0]["block_wire_sha256"]}})
    return publications, receipts, comparisons


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--native", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--repository", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--pl-gateway-config", type=Path,
                        help="Pinned external Linux worker configuration; source does not select this worker")
    args = parser.parse_args(argv)
    if args.output.exists():
        parser.error("output must be a new evidence directory")
    summary = json.loads((args.native / "summary.json").read_text(encoding="utf-8"))
    raw_path = args.native / "native-events.jsonl"
    if (summary["metadata"]["commit"] != "3133c66d80a898c6c3cc9af9eec3fa94fdbe2233"
            or summary["metadata"]["product_imports"] or summary["metadata"]["shims"]
            or hashlib.sha256(raw_path.read_bytes()).hexdigest() != summary["raw_events_sha256"]):
        raise ValueError("independent native execution with intact records is required")
    raw = [json.loads(line) for line in raw_path.read_text(encoding="utf-8").splitlines()]
    configuration = {"schema": "orion.mixed-controller.configuration.v1", "freshness_s": 600,
        "nodes": [{"node_id": f"N{n:02}", "session_id": f"mixed-session-N{n:02}", "clock_domain": "fixture-aligned-clock",
                   "capacity": 4, "capabilities": ["classical_control", "time_tagging", "correlation"]}
                  for n in range(1, 12)],
        "links": [*summary["links"], {"link_id": "L01-02", "a": "N01", "b": "N02",
                   "classical_channel_id": "classical-L01-02", "quantum_channel_id": "quantum-L01-02"}]}
    adapter = EmulatedPhotonAdapter("N01-bench-fixture")
    adapter.connect()
    photon_config = PhotonConfig(seed=2026101101, duration_ps=10**10, clock_domain="fixture-aligned-clock")
    adapter.configure(photon_config)
    adapter.calibrate()
    acquisition = adapter.acquire("mixed-node-one-emulated")
    records = normalize_acquisition(acquisition, node_id="N01", session_id="mixed-session-N01")
    for node_id in summary["node_ids"]:
        records.extend(normalize_sequence_callbacks([item for item in raw if item["node_id"] == node_id],
            node_id=node_id, session_id="mixed-session-" + node_id, configuration_id=summary["configuration_id"],
            clock_domain="fixture-aligned-clock", source_id=summary["source_id"], adapter_id=summary["adapter_id"],
            delivery_evidence=raw))
    args.output.mkdir(parents=True)
    gateway_config = None
    if args.pl_gateway_config:
        from quantum_autopilot.service.pl_gateway_cli import load_config
        gateway_config = load_config(args.pl_gateway_config)
    publications, processing, comparisons = process_observations(records, args.output / "node-processing",
                                                               gateway_config=gateway_config)
    records.extend(publications)
    (args.output / "configuration.json").write_text(json.dumps(configuration, indent=2) + "\n", encoding="utf-8")
    (args.output / "normalized-observations.jsonl").write_text("".join(json.dumps(item, sort_keys=True) + "\n" for item in records), encoding="utf-8")
    (args.output / "emulated-acquisition.json").write_text(json.dumps(asdict(acquisition), indent=2) + "\n", encoding="utf-8")
    report = {"schema": "orion.mixed-controller.campaign.v1", "status": "running", "fixture_only": True,
              "physical_action_performed": False, "swabian_hardware_present": False,
              "swabian_sdk_executed": False, "trl_assigned": False,
              "native_events_sha256": summary["raw_events_sha256"], "native_counters": summary["counters"],
              "configuration_sha256": digest(configuration), "observation_count": len(records),
              "processing": processing, "worker_comparisons": comparisons,
              "checks": {"eleven_nodes_same_processing_pipeline": len(processing) == 11 and all(comparisons.values())}}
    with tempfile.TemporaryDirectory(prefix="orion-mixed-private-") as temporary:
        private = Path(temporary)
        secret_source = _FixtureSecretSource()
        provider = AESGCMSecretProvider(secret_source, active_key_id="fixture", active_version=1)
        admin_secret, core_secret = os.urandom(48), os.urandom(48)
        config = CoreConfig.parse({"schema": "orion.core-service.v1", "repository_root": str(args.repository.resolve()),
            "state_root": str(private / "state"), "identity": {"subject": "mixed-core", "credential_file": str(private / "unused-service-ref")},
            "vault": {"key_id": "fixture", "key_file": str(private / "unused-vault-ref")},
            "http": {"host": "127.0.0.1", "port": 0, "allowed_hosts": ["localhost", "127.0.0.1"],
                     "allowed_origins": [], "requests_per_minute": 600},
            "tls": {"root": str(private / "certificates"), "server_leaf": str(private / "server-leaf.json")}})
        authority = SecurityRuntime(config.security_root, profile="operational", identity_profile="service",
            secret_provider=provider, bootstrap=True,
            service_bootstrap=ServiceIdentityProvision("mixed-admin", ("administrator",), ("*",), admin_secret))
        admin = authority.authenticate_service("mixed-admin", admin_secret)
        with authority.identity_scope(admin):
            authority.grant_service_identity("mixed-core", ("operator",), ("*",), core_secret)
            authority.grant_identity("mixed-observer", ("auditor",), ("*",), source="certificate")
            pki = OperationalPKI(config.tls["root"], authority)
            leaf = pki.provision("mixed-core", kind="service", services=["dashboard"], server=True)
            operator_context = pki.client_context(pki.provision("mixed-core", kind="service", services=["dashboard"]))
            observer_context = pki.client_context(pki.provision("mixed-observer", kind="service", services=["dashboard"]))
        Path(config.tls["server_leaf"]).write_text(json.dumps(leaf_descriptor(leaf)), encoding="utf-8")
        runtime = SecurityRuntime(config.security_root, profile="operational", identity_profile="service", bootstrap=False,
            secret_provider=provider, service_identity=ServiceIdentityBinding("mixed-core", lambda: core_secret))
        server = create_core_server(config, security=runtime)
        thread = Thread(target=server.serve_forever, kwargs={"poll_interval": .02}, daemon=True)
        thread.start()
        try:
            operator = CoreClient(f"https://localhost:{server.server_port}", operator_context)
            observer = CoreClient(f"https://localhost:{server.server_port}", observer_context)
            operator.mixed_configure(configuration)
            for offset in range(0, len(records), 128):
                operator.mixed_ingest({"records": records[offset:offset + 128]})
            state = observer.mixed_status()
            report["checks"]["all_eleven_nodes_ingested"] = len(state["nodes"]) == 11 and all(n["sequence"] >= 1 for n in state["nodes"].values())
            report["checks"]["node_one_raw_tags_preserved"] = state["nodes"]["N01"]["time_tag_count"] == sum(tag.event_type == "TimeTag" for tag in acquisition.events)
            report["checks"]["calculated_metrics_published_by_backend"] = all(
                state["nodes"][node_id]["analysis"]["coincidence_pairs"] == result["metrics"]["coincidence_pairs"]
                and state["nodes"][node_id]["analysis"]["block_sha256"] == result["block_wire_sha256"]
                for node_id, result in processing.items())
            reservations = []
            for n in range(2, 12):
                target = 2 if n == 11 else n + 1
                request = {"request_id": f"native-demand-{n}", "source": f"N{n:02}", "target": f"N{target:02}",
                           "operation": "classical_control", "ttl_s": 60}
                receipt = operator.mixed_demand(request)
                reservations.append(receipt)
                operator.mixed_release({"request_id": request["request_id"]})
            report["checks"]["all_ten_native_nodes_reserved"] = all(r["status"] == "reserved" for r in reservations)
            bridge = operator.mixed_demand({"request_id": "physical-bridge-pending", "source": "N01", "target": "N11",
                                           "operation": "classical_control", "ttl_s": 60})
            report["checks"]["missing_bench_bridge_blocks"] = bridge["status"] == "blocked"
            quantum = operator.mixed_demand({"request_id": "entanglement-not-qualified", "source": "N02", "target": "N11",
                                            "operation": "entanglement", "ttl_s": 60})
            report["checks"]["clicks_do_not_create_entanglement"] = quantum["status"] == "blocked" and quantum["quantum_result"] is None
            try:
                observer.mixed_demand({"request_id": "auditor-denied", "source": "N02", "target": "N03", "operation": "classical_control", "ttl_s": 60})
            except CoreRequestError as error:
                report["checks"]["auditor_mutation_denied"] = error.status == 403
            report["checks"]["authenticated_audit_valid"] = runtime.audit.verify()["valid"]
            report.update(status="PASS" if all(report["checks"].values()) else "FAIL", tls="TLSv1.3-mTLS",
                          node_one_to_network="blocked_pending_physical_classical_bridge", reservations=reservations,
                          missing_bridge=bridge, quantum_admission=quantum,
                          final_state=observer.mixed_status(), source_version=observer.health()["autopilot_version"])
        finally:
            server.shutdown()
            server.begin_draining()
            server.drain_requests(timeout_s=10)
            server.server_close()
            thread.join(5)
            server.service._release_controller_io_resources()
    (args.output / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": report["status"], "checks": report["checks"], "physical_bench_pending": True}))
    return 0 if report["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
