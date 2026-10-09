r"""Benchmark the ordinary OrionQ console, with fresh state and real demo input.

Normal measurements and the heavier --profile pass must use separate outputs.
No product source, model weight, protocol parameter or operator workspace is
changed. Qt pointer/wheel events drive the existing UI; no OS input is injected.

Example (Windows, run only after coordinating exclusive benchmark access)::

    .audit-winvenv\Scripts\python.exe tools\benchmark_console_performance.py \
        --output outputs\performance_20261007\ui\baseline --duration 20 --soak 0

Use --soak 3 or longer for the final continuity measurement. A parent watchdog
and an independent psutil sampler remain responsive if the Qt loop blocks.
"""
from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
import importlib.metadata
import importlib.util
import json
import math
import os
from pathlib import Path
import subprocess
import shutil
import sys
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
USER_OWNED_PIDS = (5956, 25772)


sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tools"))
if os.environ.get("ORION_BENCH_ARCHIVE_PYZ"):
    # This top-level bootstrap also executes in Windows multiprocessing spawn.
    # Own modules are all old compiled code, including the physics worker.
    from benchmark_archive_importer import install_archive_importer
    install_archive_importer(os.environ["ORION_BENCH_ARCHIVE_PYZ"], os.environ["ORION_BENCH_ARCHIVE_ASSETS"])
runtime_spec = importlib.util.spec_from_file_location("_orion_performance_harness",
    ROOT / "src" / "quantum_autopilot" / "console" / "performance_validation.py")
runtime = importlib.util.module_from_spec(runtime_spec)
sys.modules[runtime_spec.name] = runtime
runtime_spec.loader.exec_module(runtime)
_write_json, _versions, _summary, _run_ui = runtime._write_json, runtime._versions, runtime._summary, runtime._run_ui
runtime_source_hashes = runtime._source_hashes


def _source_hashes(args=None):
    if args is not None and args.archive_pyz:
        return runtime._archive_source_hashes(args.archive_pyz, args.archive_assets, args.archive_manifest, ROOT)
    return runtime_source_hashes(ROOT)


def _sample_gpu(output, stop):
    """Shared device context; never attribute other applications' GPU use."""
    executable = shutil.which("nvidia-smi")
    columns = ("timestamp", "index", "name", "driver_version", "utilization.gpu", "utilization.memory",
               "memory.used", "memory.total", "power.draw", "temperature.gpu")
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    with Path(output).open("w", encoding="utf-8") as stream:
        if executable is None:
            stream.write(json.dumps({"wall_unix": time.time(), "available": False,
                "scope": "shared-device-context", "attributed_to_orion_q": False,
                "reason": "nvidia-smi is not available"}) + "\n")
            return
        while not stop.is_set():
            row = {"wall_unix": time.time(), "scope": "shared-device-context", "attributed_to_orion_q": False,
                   "query_interval_s": 5, "units": {"utilization.gpu": "percent", "utilization.memory": "percent",
                       "memory.used": "MiB", "memory.total": "MiB", "power.draw": "W", "temperature.gpu": "C"}}
            try:
                result = subprocess.run([executable, "--query-gpu=" + ",".join(columns), "--format=csv,noheader,nounits"],
                    capture_output=True, text=True, timeout=3, creationflags=flags)
                row.update(available=result.returncode == 0, exit_code=result.returncode,
                    devices=[dict(zip(columns, (value.strip() for value in values)))
                             for values in csv.reader(result.stdout.splitlines())] if result.returncode == 0 else [])
            except (OSError, subprocess.TimeoutExpired) as exc:
                row.update(available=False, error=type(exc).__name__, devices=[])
            stream.write(json.dumps(row, separators=(",", ":")) + "\n")
            stream.flush()
            stop.wait(5)


def _sample_process(pid, output, stop_file, host_pids):
    """Independent process: cannot be delayed by the target's Python GIL."""
    import psutil
    target = psutil.Process(pid)
    tracked = {}
    began = time.perf_counter()
    psutil.cpu_percent(None)
    gpu_stop = threading.Event()
    gpu_thread = threading.Thread(target=_sample_gpu, args=(Path(output).with_name("gpu-context.jsonl"), gpu_stop),
                                  name="SharedGpuContextSampler", daemon=True)
    gpu_thread.start()
    with Path(output).open("w", encoding="utf-8") as stream:
        while not Path(stop_file).exists():
            try:
                family = [target, *target.children(recursive=True)]
                row = {"elapsed_s": time.perf_counter() - began, "wall_unix": time.time(),
                       "cpu_percent": 0., "rss_bytes": 0, "threads": 0, "processes": [],
                       "host_cpu_percent": psutil.cpu_percent(None), "host_available_bytes": psutil.virtual_memory().available,
                       "user_owned_orion_processes": []}
                for process in family:
                    try:
                        key = (process.pid, process.create_time())
                        if key not in tracked:
                            tracked[key] = process
                            process.cpu_percent(None)
                        process = tracked[key]
                        rss = process.memory_info().rss
                        cpu = process.cpu_percent(None)
                        row["cpu_percent"] += cpu
                        row["rss_bytes"] += rss
                        row["threads"] += process.num_threads()
                        row["processes"].append({"pid": process.pid, "name": process.name(), "cpu_percent": cpu, "rss_bytes": rss})
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        pass
                for host_pid in host_pids:
                    try:
                        host = psutil.Process(host_pid)
                        key = (host.pid, host.create_time())
                        if key not in tracked:
                            tracked[key] = host
                            host.cpu_percent(None)
                        host = tracked[key]
                        row["user_owned_orion_processes"].append({"pid": host.pid, "name": host.name(),
                            "cpu_percent": host.cpu_percent(None), "rss_bytes": host.memory_info().rss})
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        pass
                stream.write(json.dumps(row, separators=(",", ":")) + "\n")
                stream.flush()
            except (psutil.NoSuchProcess, psutil.ZombieProcess):
                break
            time.sleep(.5)
    gpu_stop.set()
    gpu_thread.join(timeout=4)
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--output", type=Path, help="new output directory; never an operator workspace")
    parser.add_argument("--duration", type=float, default=20, help="seconds for each map/globe/tabs/swaps phase (default 20)")
    parser.add_argument("--soak", type=float, default=3, help="additional continuity phase in minutes; final measurement requires >=3")
    parser.add_argument("--size", default="3440x1440", help="native Qt client size, default 3440x1440")
    parser.add_argument("--profile", action="store_true", help="separate diagnostic pass: cProfile and selected method timings")
    parser.add_argument("--local-profile", action="store_true", help="separate show/layout/GC spans with original QApplication")
    parser.add_argument("--switch-interval", type=float, help="diagnostic Python thread interval in seconds, restored after run")
    parser.add_argument("--executable", type=Path, help="OrionQ.exe (or a preserved legacy executable) with --performance-test support")
    parser.add_argument("--archive-pyz", type=Path, help="preserved trusted before build; own modules only, same dependencies")
    parser.add_argument("--archive-assets", type=Path, help="matching preserved OrionQ/_internal (or legacy ORION-Q/_internal) directory")
    parser.add_argument("--archive-manifest", type=Path, help="matching preserved source-snapshot.json")
    parser.add_argument("--host-pid", action="append", type=int, default=[], help="existing process to observe, never stop")
    parser.add_argument("--ui-child", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--sampler-pid", type=int, help=argparse.SUPPRESS)
    parser.add_argument("--sampler-stop", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--sampler-output", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.sampler_pid:
        return _sample_process(args.sampler_pid, args.sampler_output, args.sampler_stop, args.host_pid)
    if args.output is None:
        parser.error("--output is required")
    if args.profile and args.local_profile:
        parser.error("global and localized profile passes must be separate")
    if not math.isfinite(args.duration) or args.duration < 1 or not math.isfinite(args.soak) or args.soak < 0:
        parser.error("duration must be >=1 second and soak >=0 minutes")
    if args.switch_interval is not None and (not math.isfinite(args.switch_interval) or not 0 < args.switch_interval <= .1):
        parser.error("switch interval must be finite in (0, 0.1] seconds")
    if args.switch_interval is not None and args.executable:
        parser.error("experimental switch interval applies only to the external source/archive harness")
    try:
        args.width, args.height = (int(value) for value in args.size.lower().split("x"))
    except (TypeError, ValueError):
        parser.error("--size must be WIDTHxHEIGHT")
    if not (320 <= args.width <= 7680 and 240 <= args.height <= 4320):
        parser.error("size is outside 320x240..7680x4320")
    args.output = args.output.resolve()
    if args.ui_child:
        child_configuration = json.loads((args.output / "configuration.json").read_text())
        args.source_hashes_start = child_configuration["source_hashes_start"]
        args.external_watchdog = child_configuration.get("external_watchdog")
        args.repository_root = Path(os.environ["ORION_BENCH_ARCHIVE_ASSETS"]) if os.environ.get("ORION_BENCH_ARCHIVE_PYZ") else ROOT
        return _run_ui(args)
    if any((args.archive_pyz, args.archive_assets, args.archive_manifest)):
        if args.executable or not all((args.archive_pyz, args.archive_assets, args.archive_manifest)):
            parser.error("archive mode requires PYZ, assets and manifest, and cannot use --executable")
        if not args.archive_pyz.is_file() or not args.archive_assets.is_dir() or not args.archive_manifest.is_file():
            parser.error("archive inputs must be existing preserved build files")
    try:
        import psutil  # noqa: F401 - required sampler; do not silently omit CPU/RSS evidence
    except ImportError:
        parser.error("psutil is required for the independent sampler")
    args.output.mkdir(parents=True, exist_ok=False)
    watchdog = runtime._external_watchdog_budget(args.duration, args.soak)
    configuration = {"schema": "orion.console-performance-config.v1", "created_at": datetime.now(timezone.utc).isoformat(),
        "command": sys.argv, "versions": _versions(), "duration_s": args.duration, "soak_minutes": args.soak,
        "size": [args.width, args.height], "profile": args.profile, "local_profile": args.local_profile,
        "source_hashes_start": _source_hashes(args), "external_watchdog": watchdog,
        "switch_interval_s": args.switch_interval if args.switch_interval is not None else sys.getswitchinterval(),
        "sampler_interval_s": .5, "heartbeat_interval_ms": 10, "provider": "demo", "protocol_parameters_modified": False,
        "gpu_sampler_interval_s": 5, "gpu_scope": "shared device; no per-application attribution",
        "paint_recording_scope": "all-widget-profile" if args.profile else runtime.NORMAL_PAINT_SCOPE,
        "user_owned_pids_observed_only": list(dict.fromkeys((*USER_OWNED_PIDS, *args.host_pid)))}
    _write_json(args.output / "configuration.json", configuration)
    child_command = [sys.executable, str(Path(__file__).resolve()), "--ui-child", "--output", str(args.output),
                     "--duration", str(args.duration), "--soak", str(args.soak), "--size", args.size]
    if args.profile:
        child_command.append("--profile")
    if args.local_profile:
        child_command.append("--local-profile")
    if args.switch_interval is not None:
        child_command += ["--switch-interval", str(args.switch_interval)]
    if args.executable is not None:
        if not args.executable.is_file():
            parser.error("--executable must be an existing OrionQ or legacy executable")
        child_command = [str(args.executable.resolve()), "--performance-test", str(args.output),
            "--performance-duration", str(args.duration), "--performance-soak", str(args.soak),
            "--performance-size", args.size]
        if args.profile:
            child_command.append("--performance-profile")
        if args.local_profile:
            child_command.append("--performance-local-profile")
    environment = dict(os.environ)
    if args.archive_pyz:
        environment.update(ORION_BENCH_ARCHIVE_PYZ=str(args.archive_pyz.resolve()),
            ORION_BENCH_ARCHIVE_ASSETS=str(args.archive_assets.resolve()),
            ORION_BENCH_ARCHIVE_MANIFEST=str(args.archive_manifest.resolve()),
            ORION_BENCH_HARNESS_ROOT=str(ROOT))
        configuration["code_origin"] = "archived-compiled-before"
        configuration["archive_manifest"] = str(args.archive_manifest.resolve())
        _write_json(args.output / "configuration.json", configuration)
    environment["QT_QPA_PLATFORM"] = "windows" if sys.platform == "win32" else environment.get("QT_QPA_PLATFORM", "offscreen")
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    stop_file = args.output / ".sampler-stop"
    timed_out = False
    with (args.output / "stdout.log").open("w", encoding="utf-8") as stdout, (args.output / "stderr.log").open("w", encoding="utf-8") as stderr:
        child = subprocess.Popen(child_command, cwd=ROOT, env=environment, stdout=stdout, stderr=stderr, creationflags=flags)
        sample_command = [sys.executable, str(Path(__file__).resolve()), "--sampler-pid", str(child.pid),
                          "--sampler-output", str(args.output / "resources.jsonl"), "--sampler-stop", str(stop_file)]
        for pid in configuration["user_owned_pids_observed_only"]:
            sample_command += ["--host-pid", str(pid)]
        sampler = subprocess.Popen(sample_command, cwd=ROOT, stdout=stdout, stderr=stderr, creationflags=flags)
        try:
            code = child.wait(timeout=watchdog["timeout_s"])
        except subprocess.TimeoutExpired:
            timed_out = True
            # Only this freshly spawned diagnostic process. User-owned ORION-Q PIDs are never touched.
            child.terminate()
            code = child.wait(timeout=15)
        finally:
            stop_file.write_text("stop\n", encoding="ascii")
            try:
                sampler.wait(timeout=5)
            except subprocess.TimeoutExpired:
                sampler.terminate()
                sampler.wait(timeout=5)
    launcher = {"ui_pid": child.pid, "sampler_pid": sampler.pid, "exit_code": code, "external_timeout": timed_out,
                "external_watchdog": watchdog,
                "user_owned_processes_modified": False, "source_hashes_end": _source_hashes(args), "executable": str(args.executable.resolve()) if args.executable else None}
    _write_json(args.output / "launcher.json", launcher)
    report_path = args.output / "report.json"
    resources_path = args.output / "resources.jsonl"
    if report_path.is_file() and resources_path.is_file():
        report = json.loads(report_path.read_text(encoding="utf-8"))
        resources = [json.loads(line) for line in resources_path.read_text(encoding="utf-8").splitlines() if line]
        for interval in report["phase_windows"]:
            rows = [row for row in resources if interval["start_s"] <= row["wall_unix"] - report["time_origin_unix"] <= interval["end_s"]]
            report["phases"][interval["phase"]]["process_tree_cpu_percent"] = _summary([row["cpu_percent"] for row in rows])
            report["phases"][interval["phase"]]["process_tree_rss_mib"] = _summary([row["rss_bytes"] / 1048576 for row in rows])
            report["phases"][interval["phase"]]["host_cpu_percent"] = _summary([row["host_cpu_percent"] for row in rows])
            gui_pid = report.get("ui_process_id")
            gui_rows = [process for row in rows for process in row["processes"] if process["pid"] == gui_pid]
            report["phases"][interval["phase"]]["ui_process_rss_mib"] = _summary([row["rss_bytes"] / 1048576 for row in gui_rows])
            report["phases"][interval["phase"]]["ui_process_cpu_percent"] = _summary([row["cpu_percent"] for row in gui_rows])
            report["phases"][interval["phase"]]["non_ui_processes_rss_mib"] = _summary([
                sum(process["rss_bytes"] for process in row["processes"] if process["pid"] != gui_pid) / 1048576 for row in rows])
        report["resources_sampler_independent_process"] = True
        report["resources_sample_count"] = len(resources)
        report["resource_sampler_exit_code"] = sampler.returncode
        gpu_path = args.output / "gpu-context.jsonl"
        gpu_samples = [json.loads(line) for line in gpu_path.read_text(encoding="utf-8").splitlines() if line] if gpu_path.is_file() else []
        report["gpu_context"] = {"file": gpu_path.name, "scope": "shared-device-context",
            "attributed_to_orion_q": False, "sample_count": len(gpu_samples),
            "available_samples": sum(bool(row["available"]) for row in gpu_samples),
            "sampler_independent_of_ui_and_cpu_rss_loop": True}
        _write_json(report_path, report)
    print(json.dumps({"output": str(args.output), "exit_code": code, "external_timeout": timed_out}, indent=2))
    return 1 if timed_out else code


if __name__ == "__main__":
    import multiprocessing
    multiprocessing.freeze_support()
    raise SystemExit(main())
