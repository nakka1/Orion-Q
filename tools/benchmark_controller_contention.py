"""Isolated Linux benchmark of actual policy, durable control-slot reservations and mTLS admission.

This stress fixture borrows the deployed topology shape. Its synthetic ready
observations/capacity are explicitly not evidence of quantum memory or link viability.
No production module/configuration is edited and no physical action is enabled.
"""
from __future__ import annotations

import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
import cProfile
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import platform
import pstats
import random
import resource
import sqlite3
import statistics
from threading import Barrier
import time
import tracemalloc

import networkx as nx

from quantum_autopilot.control_plane.mixed_contracts import ControllerObservation, digest
from quantum_autopilot.control_plane.mixed_controller import MixedController, route_policy
from quantum_autopilot.control_plane.topology import SimulatedTopologyStore
from quantum_autopilot.network_engine.institutional import ensure_institutional_workspace


def distribution(values):
    data = sorted(values)
    return {"count": len(data), "p50_ms": statistics.median(data),
            "p95_ms": data[(len(data) * 95 + 99) // 100 - 1], "max_ms": max(data)}


def observations(config):
    records = []
    for node in config["nodes"]:
        items = [("session_started", {"configuration_id": "a" * 64, "verified": True}),
                 ("resources", {"available": node["capacity"]})]
        items.extend(("link_state", {"link_id": link["link_id"], "classical_state": "up", "quantum_state": "up"})
                     for link in config["links"] if node["node_id"] in (link["a"], link["b"]))
        for sequence, (kind, data) in enumerate(items):
            observation = ControllerObservation(f"{node['session_id']}-{sequence}", node["node_id"],
                node["session_id"], sequence, sequence * 100, node["clock_domain"], kind, data).to_dict()
            records.append({"observation": observation, "provenance": {"origin": "simulated",
                "source_id": "benchmark-stress-fixture", "adapter_id": "benchmark-stress-fixture",
                "record_sha256": digest(observation)}})
    return records


def initialize(core, config):
    core.configure(config)
    records = observations(config)
    for offset in range(0, len(records), 128):
        core.ingest({"records": records[offset:offset + 128]})


def batch(call, requests, *, concurrent):
    barrier = Barrier(len(requests)) if concurrent else None
    def execute(request):
        if barrier:
            barrier.wait(timeout=15)
        start = time.perf_counter_ns()
        cpu = time.thread_time_ns()
        try:
            receipt = call(request)
            return {"request_id": request["request_id"], "status": receipt["status"],
                    "quantum_result": receipt.get("quantum_result"), "route_hops": len(receipt.get("route", [])) - 1,
                    "latency_ms": (time.perf_counter_ns() - start) / 1e6,
                    "calling_thread_cpu_ms": (time.thread_time_ns() - cpu) / 1e6,
                    "start_ns": start, "end_ns": time.perf_counter_ns()}
        except Exception as error:
            return {"request_id": request["request_id"], "status": "error", "error_type": type(error).__name__,
                    "http_status": getattr(error, "status", None), "latency_ms": (time.perf_counter_ns() - start) / 1e6,
                    "start_ns": start, "end_ns": time.perf_counter_ns()}
    cpu, start = time.process_time_ns(), time.perf_counter_ns()
    if concurrent:
        with ThreadPoolExecutor(max_workers=len(requests)) as pool:
            rows = list(pool.map(execute, requests))
    else:
        rows = [execute(request) for request in requests]
    cpu_ms = (time.process_time_ns() - cpu) / 1e6
    total_ms = (time.perf_counter_ns() - start) / 1e6
    first, last = min(row["start_ns"] for row in rows), max(row["end_ns"] for row in rows)
    return {"total_including_thread_setup_ms": total_ms, "allocation_window_ms": (last - first) / 1e6,
            "start_skew_ms": (max(row["start_ns"] for row in rows) - first) / 1e6,
            "process_cpu_ms": cpu_ms, "cpu_percent_one_core": cpu_ms / total_ms * 100,
            "latency": distribution([row["latency_ms"] for row in rows]),
            "statuses": dict(Counter(row["status"] for row in rows)),
            "quantum_result_inferred": any(row.get("quantum_result") is not None for row in rows),
            "requests": [{k: v for k, v in row.items() if k not in ("start_ns", "end_ns")} for row in rows]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--repository", type=Path, required=True)
    parser.add_argument("--trials", type=int, default=10)
    parser.add_argument("--api", action="store_true")
    args = parser.parse_args()
    if platform.system() != "Linux" or args.output.exists() or not 2 <= args.trials <= 30:
        parser.error("Linux, fresh output and 2..30 trials required")
    os.umask(0o077)
    args.output.mkdir(parents=True)
    private, public = args.output / "private", args.output / "public"
    private.mkdir(mode=0o700)
    public.mkdir()
    topology, _ = ensure_institutional_workspace(SimulatedTopologyStore(private / "blueprint.sqlite"))
    nodes = sorted(node.node_id for node in topology.nodes)
    config = {"schema": "orion.mixed-controller.configuration.v1", "freshness_s": 600,
              "nodes": [{"node_id": n, "session_id": f"benchmark-{n}", "clock_domain": "stress-clock",
                         "capacity": 38, "capabilities": ["classical_control", "entanglement_memory", "bsm"]} for n in nodes],
              "links": [{"link_id": link.link_id, "a": link.left_node_id, "b": link.right_node_id,
                         "classical_channel_id": "C-" + link.link_id, "quantum_channel_id": "Q-" + link.link_id}
                        for link in topology.links]}
    rng = random.Random(20261011)
    graph = nx.Graph()
    graph.add_nodes_from(nodes)
    graph.add_edges_from((link["a"], link["b"]) for link in config["links"])
    components = sorted((sorted(c) for c in nx.connected_components(graph) if len(c) > 1), key=lambda c: c[0])
    endpoints = [rng.sample(rng.choice(components), 2) for _ in range(38)]
    requests = [{"request_id": f"R{i:02}", "source": a, "target": b, "operation": "entanglement", "ttl_s": 60}
                for i, (a, b) in enumerate(endpoints)]
    report = {"schema": "orion.controller-contention-benchmark.v1", "version": __import__("importlib.metadata", fromlist=["version"]).version("quantum-twin"),
              "fixture_only": True, "physical_action_performed": False, "quantum_memory_allocation_validated": False,
              "hardware": {"platform": platform.platform(), "python": platform.python_version(), "cpu_count": os.cpu_count(),
                           "uid": os.geteuid(), "sqlite": sqlite3.sqlite_version},
              "settings": {"nodes": len(nodes), "links": len(config["links"]), "requests": 38, "trials": args.trials,
                           "seed": 20261011, "logical_capacity_per_node": 38, "all_channels_up_is_synthetic": True,
                           "connected_component_sizes": [len(c) for c in components],
                           "observations_admitted_through_contract": True, "tls_and_user_authorization_in_direct_measurements": False},
              "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              "configuration_sha256": digest(config), "request_plan": requests,
              "production_hashes": {str(Path(p).relative_to(args.repository)): hashlib.sha256(Path(p).read_bytes()).hexdigest()
                   for p in (args.repository / "src/quantum_autopilot/control_plane").glob("mixed_*.py")}}
    core = MixedController(private / "pure.sqlite")
    initialize(core, config)
    states = core.status()["nodes"]
    samples, totals = [], []
    for _ in range(args.trials * 10):
        begin = time.perf_counter_ns()
        for request in requests:
            start = time.perf_counter_ns()
            decision = route_policy(config, states, source=request["source"], target=request["target"],
                                    operation=request["operation"], now_ns=time.time_ns())
            samples.append((time.perf_counter_ns() - start) / 1e6)
            assert decision["status"] == "planned"
        totals.append((time.perf_counter_ns() - begin) / 1e6)
    report["pure_policy"] = {"individual": distribution(samples), "batch38": distribution(totals)}
    core.close()
    report["direct"] = {}
    for mode in ("sequential", "concurrent"):
        runs = []
        for trial in range(args.trials):
            core = MixedController(private / f"{mode}-{trial}.sqlite")
            initialize(core, config)
            result = batch(core.demand, requests, concurrent=mode == "concurrent")
            assert result["statuses"] == {"reserved": 38} and not result["quantum_result_inferred"]
            readback = core.status()
            assert len(readback["reservations"]) == 38
            assert all(sum(n in r["route"] for r in readback["reservations"]) <= 38 for n in nodes)
            result["readback_reservations"] = len(readback["reservations"])
            runs.append(result)
            core.close()
        report["direct"][mode] = {"runs": runs,
            "latency": distribution([row["latency_ms"] for run in runs for row in run["requests"]]),
            "batch38": distribution([run["allocation_window_ms"] for run in runs]),
            "process_cpu": distribution([run["process_cpu_ms"] for run in runs])}
    core = MixedController(private / "memory-profile.sqlite")
    initialize(core, config)
    tracemalloc.start()
    batch(core.demand, requests, concurrent=True)
    current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    report["memory"] = {"measurement_separate_from_timing": True, "python_traced_current_bytes": current,
                        "python_traced_peak_bytes": peak, "process_peak_rss_kib_including_setup_imports": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss}
    core.close()
    core = MixedController(private / "cpu-profile.sqlite")
    initialize(core, config)
    profiler = cProfile.Profile()
    profiler.enable()
    batch(core.demand, requests, concurrent=False)
    profiler.disable()
    stats = pstats.Stats(profiler)
    report["profile"] = [{"file": key[0], "line": key[1], "function": key[2], "calls": value[1],
                           "self_s": value[2], "cumulative_s": value[3]}
                         for key, value in sorted(stats.stats.items(), key=lambda item: item[1][3], reverse=True)[:20]]
    core.close()
    report["status"] = "direct_measurements_complete"
    destination = public / "report.json"
    destination.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    if args.api:
        import pytest
        from quantum_autopilot.service.client import CoreClient
        fixture_path = args.repository / "tests/test_portable_core_service.py"
        spec = importlib.util.spec_from_file_location("_orion_controller_benchmark_fixture", fixture_path)
        fixture = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(fixture)
        original_settings = fixture.settings
        def settings(path):
            data = original_settings(path)
            data["http"]["requests_per_minute"] = 10000
            return data
        fixture.settings = settings
        api_private = private / "api"
        api_private.mkdir()
        with pytest.MonkeyPatch.context() as monkeypatch:
            service_config, runtime, authority, _, _, _, _, context = fixture.provisioned.__wrapped__(api_private, monkeypatch)
            with fixture.serving(service_config, runtime) as server:
                client = CoreClient(f"https://localhost:{server.server_port}", context, timeout_s=20)
                runs = []
                for trial in range(min(args.trials, 3)):
                    current_config = json.loads(json.dumps(config))
                    for n in current_config["nodes"]:
                        n["session_id"] += f"-api-{trial}"
                    client.mixed_configure(current_config)
                    records = observations(current_config)
                    for offset in range(0, len(records), 128):
                        client.mixed_ingest({"records": records[offset:offset + 128]})
                    current_requests = [{**row, "request_id": row["request_id"] + f"-api-{trial}"} for row in requests]
                    run = batch(client.mixed_demand, current_requests, concurrent=True)
                    readback = client.mixed_status()
                    issued = {row["request_id"] for row in current_requests}
                    reserved = [row["request_id"] for row in readback["reservations"]
                                if row["request_id"] in issued and row["status"] == "reserved"]
                    acknowledged = {row["request_id"] for row in run["requests"] if row["status"] == "reserved"}
                    run["durable_reserved_count"] = len(reserved)
                    run["durable_reserved_without_client_receipt"] = sorted(set(reserved) - acknowledged)
                    runs.append(run)
                    for identifier in reserved:
                        client.mixed_release({"request_id": identifier})
                report["api"] = {"tls": "TLSv1.3-mTLS", "max_concurrency": server._transport_policy.max_concurrency,
                    "requests_per_minute": server._transport_policy.requests_per_minute, "new_connection_per_request": True,
                    "runs": runs, "latency": distribution([row["latency_ms"] for run in runs for row in run["requests"]]),
                    "audit_valid": authority.audit.verify()["valid"], "fixture_sha256": hashlib.sha256(fixture_path.read_bytes()).hexdigest()}
                report["api"]["all_requests_reserved"] = all(run["statuses"] == {"reserved": 38} for run in runs)
                server.service._release_controller_io_resources()
    report["status"] = "complete"
    destination.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps({"status": report["status"], "report": str(destination), "nodes": len(nodes), "links": len(config["links"])}))


if __name__ == "__main__":
    main()
