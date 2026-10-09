"""External CPU/RSS/GPU context monitor for an unchanged production executable.

Only the process launched by this tool and its descendants are owned. GPU
counters are device-wide context, not attribution to the application.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import platform
import shutil
import subprocess
import threading
import time
from pathlib import Path

import psutil


def stats(values):
    values = sorted(values)
    return {"count": len(values), "min": min(values, default=None),
            "p50": values[int((len(values)-1)*.5)] if values else None,
            "p95": values[int((len(values)-1)*.95)] if values else None,
            "max": max(values, default=None)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--exe", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--timeout", type=float, default=150)
    parser.add_argument("--interval", type=float, default=.5)
    parser.add_argument("args", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if args.interval < .1 or args.timeout < 1:
        parser.error("interval >= 0.1s and timeout >= 1s required")
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    exe = args.exe.resolve(strict=True)
    with exe.open("rb") as stream:
        exe_hash = hashlib.file_digest(stream, "sha256").hexdigest()
    child_args = args.args[1:] if args.args and args.args[0] == "--" else args.args
    gpu, samples, errors = [], [], []
    stop = threading.Event()
    smi = shutil.which("nvidia-smi")

    def sample_gpu():
        if not smi:
            errors.append("device_gpu: nvidia-smi unavailable")
            return
        while not stop.is_set():
            began = time.perf_counter()
            try:
                result = subprocess.run([smi, "--query-gpu=name,driver_version,memory.total,utilization.gpu,utilization.memory,memory.used",
                                         "--format=csv,noheader,nounits"], capture_output=True, text=True,
                                        timeout=5, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), check=True)
                for line in result.stdout.strip().splitlines():
                    parts = [item.strip() for item in line.split(",")]
                    gpu.append({"t_s": time.perf_counter()-start, "name": parts[0], "driver": parts[1],
                                "total_mib": float(parts[2]), "device_util_pct": float(parts[3]),
                                "memory_util_pct": float(parts[4]), "used_mib": float(parts[5]),
                                "query_ms": (time.perf_counter()-began)*1000})
            except (OSError, ValueError, subprocess.SubprocessError) as exc:
                errors.append("device_gpu: " + type(exc).__name__)
                return
            stop.wait(1)

    background = [{"pid": proc.pid, "name": proc.info["name"]}
                  for proc in psutil.process_iter(["name"])
                  if (proc.info["name"] or "").casefold() in {"orionq.exe", "orion-q.exe"}]
    start = time.perf_counter()
    with (out/"process.log").open("w", encoding="utf-8") as log:
        child = subprocess.Popen([str(exe), *child_args], stdout=log, stderr=log,
                                 creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        process = psutil.Process(child.pid)
        gpu_thread = threading.Thread(target=sample_gpu, name="device-gpu-context", daemon=True)
        gpu_thread.start()
        previous = {}
        peak = 0
        timeout = False
        try:
            while child.poll() is None:
                now = time.perf_counter()
                if now-start > args.timeout:
                    timeout = True
                    break
                try:
                    tree = [process, *process.children(recursive=True)]
                except psutil.Error:
                    tree = [process]
                total_cpu = total_rss = root_rss = 0
                pids = []
                for proc in tree:
                    try:
                        times = proc.cpu_times()
                        consumed = times.user + times.system
                        rss = proc.memory_info().rss
                        before = previous.get(proc.pid)
                        cpu = (consumed-before[0])/(now-before[1])*100 if before and now > before[1] else 0
                        previous[proc.pid] = (consumed, now)
                        total_cpu += max(0, cpu)
                        total_rss += rss
                        if proc.pid == process.pid:
                            root_rss = rss
                        pids.append(proc.pid)
                    except psutil.Error:
                        continue
                peak = max(peak, total_rss)
                samples.append({"t_s": now-start, "root_rss_mib": root_rss/2**20,
                                "tree_rss_mib": total_rss/2**20, "tree_cpu_one_core_pct": total_cpu,
                                "tree_cpu_machine_pct": total_cpu/(psutil.cpu_count() or 1),
                                "host_cpu_pct": psutil.cpu_percent(None), "pids": pids})
                stop.wait(args.interval)
        finally:
            if timeout:
                # Only this newly launched tree is ours; existing operator
                # instances are recorded above and never terminated.
                try:
                    owned = process.children(recursive=True)
                except psutil.Error:
                    owned = []
                for proc in reversed(owned):
                    try:
                        proc.terminate()
                    except psutil.Error:
                        pass
                child.terminate()
            child.wait(timeout=15)
            stop.set()
            gpu_thread.join(timeout=6)
    report = {"schema": "orion.external-performance.v1", "passed": child.returncode == 0 and not timeout,
              "executable": str(exe), "executable_sha256": exe_hash, "arguments": child_args,
              "elapsed_s": time.perf_counter()-start, "exit_code": child.returncode,
              "timeout": timeout, "sample_interval_s": args.interval,
              "environment": {"os": platform.platform(), "logical_cpus": psutil.cpu_count(),
                              "physical_cpus": psutil.cpu_count(logical=False),
                              "ram_bytes": psutil.virtual_memory().total, "python": platform.python_version(),
                              "existing_operator_processes": background},
              "cpu_tree_one_core_pct": stats([item["tree_cpu_one_core_pct"] for item in samples]),
              "root_rss_mib": stats([item["root_rss_mib"] for item in samples]),
              "tree_rss_mib": stats([item["tree_rss_mib"] for item in samples]),
              "peak_tree_rss_bytes": peak, "raw_samples": samples, "gpu_device_context": gpu,
              "gpu_scope": "device-wide; contains other applications and desktop composition, not application attribution",
              "physical_actuation": False, "errors": errors}
    (out/"monitor.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({key: report[key] for key in ("passed", "elapsed_s", "exit_code", "cpu_tree_one_core_pct", "root_rss_mib", "tree_rss_mib", "errors")}))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
