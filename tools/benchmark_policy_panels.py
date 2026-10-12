"""Reproduce controlled source Qt policy-panel timings with real service guards.

This is a source benchmark, not a frozen executable or global-network campaign.
Only the JSON report is public; authenticated service state stays in a separate,
fresh private directory. No shadow implementation changes panel behavior.
"""
from __future__ import annotations
import argparse
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import sys
import threading
import time

ROOT = Path(__file__).resolve().parents[1]


def validate_paths(source_root, output, private_state_root, seconds):
    """Resolve explicit fresh destinations before creating any benchmark files."""
    if not math.isfinite(seconds) or not .1 <= seconds <= 60:
        raise ValueError('seconds must be finite and between 0.1 and 60')
    root = Path(source_root).resolve(strict=True)
    if not root.is_dir() or not (root/'pyproject.toml').is_file():
        raise ValueError('source-root must be an OrionQ source checkout')
    out, state = Path(output).resolve(), Path(private_state_root).resolve()
    if out.exists() or state.exists():
        raise ValueError('output and private-state-root must be fresh paths')
    if out.is_relative_to(state) or state.is_relative_to(out):
        raise ValueError('private state must be separate from the public output')
    for name in ('src', 'tests', 'tools', 'configs', 'runtime_assets', '.git'):
        if out.is_relative_to(root/name) or state.is_relative_to(root/name):
            raise ValueError('benchmark destinations cannot overwrite source or Git trees')
    return root, out, state


def verify_module_origins(root, modules):
    """An editable install or already imported package cannot replace the target."""
    records = {}
    for name in modules:
        path = Path(sys.modules[name].__file__).resolve(strict=True)
        if not path.is_relative_to(root/'src'):
            raise ValueError('module origin is outside the selected source-root: '+name)
        records[name] = {"path": path.relative_to(root).as_posix(),
                         "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
    return records


def stats(values):
    values = sorted(values)
    return {"count": len(values), "median_ms": values[len(values)//2] if values else None,
            "p95_ms": values[math.ceil(.95*len(values))-1] if values else None,
            "max_ms": values[-1] if values else None}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-root", default=str(ROOT))
    parser.add_argument("--output", required=True)
    parser.add_argument("--private-state-root", required=True,
                        help="fresh private service state, outside the public output")
    parser.add_argument("--label", required=True)
    parser.add_argument("--seconds", type=float, default=6)
    args = parser.parse_args()
    root, out, private_state = validate_paths(args.source_root, args.output, args.private_state_root, args.seconds)
    sys.path.insert(0, str(root/"src"))
    sys.dont_write_bytecode = True
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6 import __version__ as qt_binding_version
    from PySide6.QtCore import QTimer, qVersion
    from PySide6.QtWidgets import QApplication
    from quantum_autopilot.qkd.dashboard_api import AssuranceDashboardService
    from quantum_autopilot.console.service_provider import ServiceProvider
    from quantum_autopilot.console.source_switch import SwitchableProvider
    from quantum_autopilot.console.providers import MockProvider
    from quantum_autopilot.console.widgets.policy_decisions import PolicyDecisionAudit
    from quantum_autopilot.console.widgets.policy_context import PolicyContextInspection
    from quantum_autopilot.console.widgets.policy_review import PolicyReview
    from quantum_autopilot.console.theme import install
    from quantum_autopilot.control_plane.topology import default_simulated_topology
    from quantum_autopilot.product import product_version
    origins = verify_module_origins(root, (ServiceProvider.__module__, PolicyDecisionAudit.__module__,
        PolicyContextInspection.__module__, PolicyReview.__module__, AssuranceDashboardService.__module__,
        'quantum_autopilot.product'))
    watched = ["src/quantum_autopilot/console/widgets/"+name+".py" for name in
               ("policy_decisions", "policy_context", "policy_review")]
    watched += ["src/quantum_autopilot/console/service_provider.py", "src/quantum_autopilot/security/runtime.py",
                "src/quantum_autopilot/security/service_boundary.py", "src/quantum_autopilot/security/audit.py"]
    hashes = lambda: {name: hashlib.sha256((root/name).read_bytes()).hexdigest() for name in watched}
    before_hash = hashes()
    # Credentials, vault state, audit and SQLite are intentionally not adjacent
    # to the public report, even if this output later becomes release evidence.
    private_state.mkdir(parents=True, exist_ok=False, mode=0o700)
    out.mkdir(parents=True, exist_ok=False)
    app = QApplication.instance() or QApplication([])
    install(app)
    service = AssuranceDashboardService(root, control_journal_path=private_state/"journal.sqlite")
    service.controller_runtime
    service.topology_store.initialize(default_simulated_topology())
    provider = ServiceProvider(service, state_root=private_state, replay_limit=1)
    # Identical controlled presentation fixture in both independent processes.
    # The actual service/security/vault/audit guard capabilities and actions.
    capabilities = provider.capabilities
    fixture = replace(MockProvider().snapshot(), capabilities=capabilities)
    provider._snap = fixture
    wrapper = SwitchableProvider(provider)
    wrapper.running = True
    wrapper._snap = fixture
    phase = {"name": "setup"}
    cap_calls, renders, ticks, heartbeats, body_updates = [], [], [], [], []
    original_capabilities = ServiceProvider.capabilities.fget
    def measured_capabilities(self):
        begin, cpu = time.perf_counter(), time.thread_time()
        try:
            return original_capabilities(self)
        finally:
            cap_calls.append({"phase": phase["name"], "thread": threading.current_thread().name,
                              "wall_ms": (time.perf_counter()-begin)*1000,
                              "cpu_ms": (time.thread_time()-cpu)*1000})
    # Timing instrumentation delegates to the original property unchanged.
    ServiceProvider.capabilities = property(measured_capabilities)
    panels = [PolicyDecisionAudit(), PolicyContextInspection(), PolicyReview()]
    for panel in panels:
        original_render, original_tick = panel._render, panel._tick
        def render(p=panel, fn=original_render):
            begin = time.perf_counter()
            result = fn()
            renders.append({"phase": phase["name"], "panel": type(p).__name__,
                            "wall_ms": (time.perf_counter()-begin)*1000})
            return result
        def tick(*a, p=panel, fn=original_tick, **kw):
            begin = time.perf_counter()
            try:
                return fn(*a, **kw)
            finally:
                ticks.append({"phase": phase["name"], "panel": type(p).__name__,
                              "wall_ms": (time.perf_counter()-begin)*1000})
        panel._render, panel._tick = render, tick
        panel.watchdog.timeout.disconnect()
        panel.watchdog.timeout.connect(panel._tick)
        panel.attach(wrapper)
        panel.show()
    app.processEvents()
    phase["name"] = "measurement"
    last = {"at": time.perf_counter()}
    pulse = QTimer()
    pulse.setInterval(10)
    def beat():
        now = time.perf_counter()
        heartbeats.append((now-last["at"])*1000)
        last["at"] = now
    pulse.timeout.connect(beat)
    pulse.start()
    body = QTimer()
    body.setInterval(100)
    def update_body():
        now = time.perf_counter()
        for panel in panels:
            panel.message = f"CONTROLLED TELEMETRY PRESENTATION FIXTURE · {now:.3f}"
            panel._render()
        body_updates.append(now)
    body.timeout.connect(update_body)
    body.start()
    begin, cpu = time.perf_counter(), time.process_time()
    end = begin+args.seconds
    while time.perf_counter() < end:
        app.processEvents()
        time.sleep(.002)
    elapsed, process_cpu = time.perf_counter()-begin, time.process_time()-cpu
    body.stop()
    pulse.stop()
    for panel in panels:
        panel.watchdog.stop()
    phase["name"] = "security_checks"
    principal = service.security.grant_identity("watchdog-probe", ["researcher"], ["*"])
    def action(identity):
        with service.security.identity_scope(identity):
            return provider.policy_decision_store_path()
    checks = {}
    with ThreadPoolExecutor(max_workers=1) as pool:
        checks["authorized_action_before_revoke"] = bool(pool.submit(action, principal).result())
        service.security.revoke_identity(principal.subject)
        for name, identity in (("revoked_action_blocked", principal), ("anonymous_action_blocked", None)):
            try:
                pool.submit(action, identity).result()
                checks[name] = False
            except PermissionError:
                checks[name] = True
    checks["stale_display_did_not_grant_authority"] = capabilities.allows("read_policy_decisions") and checks["revoked_action_blocked"]
    checks["source_bytes_unchanged"] = before_hash == hashes()
    checks["module_origins_match_selected_source"] = bool(origins)
    checks["physical_actuation_locked"] = service.health()["physical_read_only"] is True
    selected_caps = [r for r in cap_calls if r["phase"] == "measurement"]
    selected_ticks = [r["wall_ms"] for r in ticks if r["phase"] == "measurement"]
    result = {"schema": "orion.policy_watchdog_source_benchmark.v1", "label": args.label,
              "scope": "controlled source Qt measurement, not native EXE or full global network qualification",
              "runtime": {"os": platform.platform(), "python": platform.python_version(), "pyside": qt_binding_version,
                  "qt": qVersion(), "display": "offscreen", "product_version": product_version(),
                  "identity_profile": service.security.identity_profile, "vault": service.security.vault.mode,
                  "simultaneously_visible": [type(p).__name__ for p in panels], "controller_fixture_nodes": 3,
                  "presentation_fixture_nodes": len(fixture.topology.nodes),
                  "scientific_engine_running": False, "requested_seconds": args.seconds,
                  "elapsed_seconds": elapsed, "process_cpu_seconds": process_cpu},
              "checks": checks, "source_sha256_before": before_hash, "source_sha256_after": hashes(),
              "source_module_origins": origins,
              "source_pyproject_sha256": hashlib.sha256((root/'pyproject.toml').read_bytes()).hexdigest(),
              "reproducer_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              "metrics": {"synchronous_capabilities": stats([r["wall_ms"] for r in selected_caps]),
                  "capabilities_threads": sorted({r["thread"] for r in selected_caps}),
                  "heartbeat": stats(heartbeats), "heartbeat_over_50ms": sum(x>50 for x in heartbeats),
                  "heartbeat_over_250ms": sum(x>250 for x in heartbeats),
                  "body_updates": len(body_updates), "renders": len([r for r in renders if r["phase"] == "measurement"]),
                  "ticks": stats(selected_ticks)},
              "raw": {"capabilities_calls": cap_calls, "tick_calls": ticks, "render_calls": renders,
                      "heartbeat_delays_ms": heartbeats, "body_update_monotonic_seconds": body_updates}}
    (out/"report.json").write_text(json.dumps(result, indent=2)+"\n", encoding="utf-8")
    print(json.dumps({"label": args.label, "metrics": result["metrics"], "checks": checks}, indent=2), flush=True)
    for panel in panels:
        panel.close()
    wrapper.running = False
    ServiceProvider.capabilities = property(original_capabilities)
    return 0 if all(checks.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
