"""Isolated SQLite persistence prototypes, retaining FULL and real control receipts.

This is a small software diagnostic, not an operational performance qualification.
It never opens operator state. All modes execute the production simulated cycle;
only private diagnostic connections and journal instrumentation are substituted.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from contextlib import closing
import cProfile
import ctypes
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import pstats
import sqlite3
import sys
from time import perf_counter_ns

from quantum_autopilot.control_plane.contracts import NetworkStateSnapshot
from quantum_autopilot.control_plane.journal import ControlJournal
from quantum_autopilot.control_plane.runtime import ControllerRuntime
from quantum_autopilot.control_plane.runtime_diagnostic import percentiles

SCHEMA = "orion.trl5.persistence-profile.v1"
MODES = ("DEFAULT_DELETE", "DELETE", "PERSIST", "TRUNCATE", "WAL", "WAL_KEEPER")
SOURCE_FILES = (
    "tools/profile_trl5_persistence.py",
    "src/quantum_autopilot/control_plane/runtime.py",
    "src/quantum_autopilot/control_plane/journal.py",
    "src/quantum_autopilot/control_plane/sqlite_transaction.py",
    "src/quantum_autopilot/control_plane/runtime_diagnostic.py",
    "src/quantum_autopilot/control_plane/contracts.py",
    "src/quantum_autopilot/control_plane/policy.py",
    "src/quantum_autopilot/control_plane/safety.py",
    "src/quantum_autopilot/control_plane/executors.py",
    "src/quantum_autopilot/security/audit.py",
    "src/quantum_autopilot/security/vault.py",
)
REFERENCES = (
    {"title": "SQLite WAL: last close, backup, local filesystem, WAL-reset fix",
     "url": "https://www.sqlite.org/wal.html", "accessed": "2026-10-09"},
    {"title": "SQLite PRAGMA journal_mode and synchronous",
     "url": "https://www.sqlite.org/pragma.html", "accessed": "2026-10-09"},
    {"title": "SQLite Online Backup API",
     "url": "https://www.sqlite.org/backup.html", "accessed": "2026-10-09"},
)


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _write_json(path, value):
    with Path(path).open("x", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n")


def wal_version_eligible(version):
    """Allow official fixed releases only; unknown/version suffixes fail closed."""
    try:
        parts = tuple(int(part) for part in version.split("."))
    except (AttributeError, ValueError):
        return False
    if len(parts) != 3 or any(part < 0 for part in parts):
        return False
    return (parts >= (3, 51, 3) or
            parts[:2] == (3, 50) and parts[2] >= 7 or
            parts[:2] == (3, 44) and parts[2] >= 6)


def disk_eligibility(path):
    """WAL prototype requires positively identified local storage on Windows.

    On other systems this bounded tool refuses WAL unless local storage can be
    qualified separately; arbitrary mounted filesystems cannot be inferred safe.
    """
    resolved = Path(path).resolve()
    if os.name != "nt":
        return {"wal_local_disk_eligible": False, "reason": "local filesystem not qualified on this OS"}
    if str(resolved).startswith("\\\\"):
        return {"wal_local_disk_eligible": False, "reason": "UNC/network path excluded"}
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetDriveTypeW.argtypes = (ctypes.c_wchar_p,)
    kernel.GetDriveTypeW.restype = ctypes.c_uint
    kind = int(kernel.GetDriveTypeW(resolved.anchor))
    return {"wal_local_disk_eligible": kind == 3, "windows_drive_type": kind,
            "reason": "local fixed drive" if kind == 3 else "not a qualified local fixed drive"}


class Trace:
    def __init__(self):
        self.events = []
        self.phase = "initialization"

    def add(self, name, started, **details):
        self.events.append({"phase": self.phase, "operation": name,
                            "ms": (perf_counter_ns() - started) / 1e6, **details})

    def summary(self, *, phase=None):
        grouped = defaultdict(list)
        for event in self.events:
            if phase is None or event["phase"] == phase:
                grouped[event["operation"]].append(event["ms"])
        return {name: percentiles(values) for name, values in sorted(grouped.items())}


def _sql_operation(sql):
    tokens = sql.strip().upper().split()
    head = tokens[0] if tokens else "EMPTY"
    if head == "PRAGMA":
        safe_name = tokens[1].split("=", 1)[0].split("(", 1)[0] if len(tokens) > 1 else "unknown"
        return "sql.pragma." + safe_name.lower()
    return "sql." + head.lower()


class ProfiledConnection(sqlite3.Connection):
    """Time real SQLite boundaries, including context-manager C-level commit."""
    def __init__(self, *args, trace, **kwargs):
        self.trace = trace
        self._closed = False
        super().__init__(*args, **kwargs)

    def execute(self, sql, parameters=()):
        started = perf_counter_ns()
        try:
            return super().execute(sql, parameters)
        finally:
            self.trace.add(_sql_operation(sql), started)

    def __exit__(self, exc_type, exc, traceback):
        operation = ("context_rollback" if exc_type else "context_commit") if self.in_transaction else "context_noop"
        started = perf_counter_ns()
        try:
            return super().__exit__(exc_type, exc, traceback)
        finally:
            self.trace.add(operation, started)

    def commit(self):
        started = perf_counter_ns()
        try:
            return super().commit()
        finally:
            self.trace.add("explicit_commit", started)

    def rollback(self):
        started = perf_counter_ns()
        try:
            return super().rollback()
        finally:
            self.trace.add("explicit_rollback", started)

    def close(self):
        if self._closed:
            return super().close()
        started = perf_counter_ns()
        try:
            return super().close()
        finally:
            self._closed = True
            self.trace.add("connection_close", started)


class ProfiledJournal(ControlJournal):
    def _ensure_io(self):
        # The diagnostic owns its chosen mode/keeper. The production automatic
        # keeper must not silently override DELETE/PERSIST/TRUNCATE prototypes.
        return None

    def __init__(self, path, trace, mode):
        if mode not in MODES:
            raise ValueError("unsupported prototype journal mode")
        self.trace, self.mode = trace, mode
        self.effective_modes = []
        self.synchronous_values = []
        self.keeper = None
        if mode == "WAL_KEEPER":
            self.keeper = self._open(Path(path))
            # Open the database pager/WAL, not merely a lazy PRAGMA handle.
            # The fully drained read retains no transaction or read lock.
            self.keeper.execute("SELECT name FROM sqlite_schema").fetchall()
        try:
            super().__init__(path)
        except BaseException:
            self.close()
            raise

    def _open(self, path):
        started = perf_counter_ns()
        connection = sqlite3.connect(str(path), timeout=10.0, factory=lambda *args, **kwargs:
                                     ProfiledConnection(*args, trace=self.trace, **kwargs))
        connection.row_factory = sqlite3.Row
        self.trace.add("connection_open", started)
        started = perf_counter_ns()
        try:
            if self.mode != "DEFAULT_DELETE":
                expected = "WAL" if self.mode == "WAL_KEEPER" else self.mode
                effective = connection.execute(f"PRAGMA journal_mode={expected}").fetchone()[0]
                connection.execute("PRAGMA synchronous=FULL")
            else:
                expected = "DELETE"
                effective = connection.execute("PRAGMA journal_mode").fetchone()[0]
            synchronous = connection.execute("PRAGMA synchronous").fetchone()[0]
            self.effective_modes.append(str(effective).upper())
            self.synchronous_values.append(int(synchronous))
            if str(effective).upper() != expected or int(synchronous) != 2:
                raise RuntimeError("prototype did not obtain requested journal mode and FULL")
        except BaseException:
            connection.close()
            raise
        finally:
            self.trace.add("connection_setup_total", started)
        return connection

    def _connect(self):
        return self._open(self.path)

    def get_by_request_id(self, request_id):
        started = perf_counter_ns()
        try:
            return super().get_by_request_id(request_id)
        finally:
            self.trace.add("journal_lookup_total", started)

    def append(self, record):
        started = perf_counter_ns()
        try:
            return super().append(record)
        finally:
            self.trace.add("journal_append_total", started)

    def close(self):
        if self.keeper is not None:
            started = perf_counter_ns()
            try:
                self.keeper.close()
            finally:
                self.keeper = None
                self.trace.add("keeper_last_close_total", started)


def _snapshot(index):
    return NetworkStateSnapshot.from_dict({
        "request_id": f"persistence-profile-{index}", "node_id": "diagnostic-repeater",
        "timestamp_utc": datetime.now(timezone.utc).isoformat(), "state_version": 1,
        "predicted_fidelity": 0.9, "prediction_uncertainty": 0.01, "telemetry_age_s": 0.0,
        "memory_state": "STORING", "available_pair_fidelities": [0.9, 0.9],
        "memory_ages_s": [0.0, 0.0], "telemetry": {"source": "runtime_diagnostic"},
        "integrity_verified": True, "operator_interlock": True, "out_of_distribution": False,
        "provenance": {"evidence_kind": "simulated", "source": "runtime_diagnostic",
                       "metadata": {"evidence_lane": "bell_fidelity_control",
                                    "target_semantics": "simulated_future_bell_fidelity"}}})


def _rows(path):
    with closing(sqlite3.connect(f"{Path(path).resolve().as_uri()}?mode=ro", uri=True)) as connection:
        integrity = connection.execute("PRAGMA quick_check").fetchone()[0]
        rows = connection.execute("SELECT request_id, record_sha256, record_json FROM control_cycles ORDER BY sequence").fetchall()
    return integrity, rows


def _verify_rows(path, expected):
    integrity, rows = _rows(path)
    return {"sqlite_quick_check": integrity == "ok", "record_count": len(rows) == expected,
            "unique_requests": len({row[0] for row in rows}) == expected,
            "all_receipt_sha256_match": all(hashlib.sha256(row[2].encode()).hexdigest() == row[1] for row in rows)}, rows


def _artifacts(directory):
    artifacts = {}
    for path in sorted(Path(directory).glob("*")):
        if path.is_file() and path.suffix not in {".vault", ".lock"}:
            artifacts[path.name] = {"bytes": path.stat().st_size,
                                    "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
    return artifacts


def _profile_summary(profile):
    stats = pstats.Stats(profile)
    selected = sorted(stats.stats.items(), key=lambda item: item[1][3], reverse=True)[:30]
    return [{"function": f"{Path(key[0]).name}:{key[1]}:{key[2]}", "primitive_calls": value[0],
             "calls": value[1], "self_ms": value[2] * 1000, "cumulative_ms": value[3] * 1000}
            for key, value in selected]


def _run_mode(root, mode, samples, warmup):
    directory = root / mode.lower()
    directory.mkdir()
    path = directory / "controller.sqlite"
    trace = Trace()
    started = perf_counter_ns()
    runtime = ControllerRuntime(path)
    production_initialization_ms = (perf_counter_ns() - started) / 1e6
    started = perf_counter_ns()
    if hasattr(runtime.journal, "release_io_resources"):
        runtime.journal.release_io_resources()
    initial_journal_release_ms = (perf_counter_ns() - started) / 1e6
    # WAL mode persists across connections. Reset only this fresh private file,
    # outside measurement, before comparing SQLite's default rollback profile.
    started = perf_counter_ns()
    with closing(sqlite3.connect(str(path), timeout=10)) as setup:
        setup.execute("PRAGMA synchronous=FULL")
        before_reset_mode = str(setup.execute("PRAGMA journal_mode").fetchone()[0]).upper()
        if str(setup.execute("PRAGMA journal_mode=DELETE").fetchone()[0]).upper() != "DELETE":
            raise RuntimeError("isolated prototype mode reset failed")
    isolated_mode_reset_ms = (perf_counter_ns() - started) / 1e6
    journal = ProfiledJournal(path, trace, mode)
    # Same production runtime, policy, supervisor, simulated executor and record.
    # No physical call is performed. The original journal owns no open handle.
    runtime.journal = journal
    attempts = []
    records = []
    profile = cProfile.Profile()
    try:
        for index in range(samples + warmup):
            trace.phase = "warmup" if index < warmup else "measurement"
            snapshot = _snapshot(index)
            first = len(trace.events)
            started = perf_counter_ns()
            if index >= warmup:
                profile.enable()
            try:
                result = runtime.cycle(snapshot, mode="simulation")
            finally:
                if index >= warmup:
                    profile.disable()
            elapsed = (perf_counter_ns() - started) / 1e6
            records.append(result["record"])
            if index >= warmup:
                phase_totals = defaultdict(float)
                phase_counts = Counter()
                for event in trace.events[first:]:
                    phase_totals[event["operation"]] += event["ms"]
                    phase_counts[event["operation"]] += 1
                attempts.append({"index": index - warmup, "cycle_ms": elapsed,
                                 "operations_ms": dict(phase_totals), "counts": dict(phase_counts),
                                 "action": result["record"]["intent"]["action"],
                                 "status": result["record"]["execution"]["status"],
                                 "wal_after_writer_close_bytes": (Path(str(path) + "-wal").stat().st_size
                                                                  if Path(str(path) + "-wal").exists() else 0),
                                 "keeper_in_transaction": journal.keeper.in_transaction
                                 if journal.keeper is not None else None})
        trace.phase = "verification"
        wal_path = Path(str(path) + "-wal")
        wal_before_backup = {"exists": wal_path.exists(),
                             "bytes": wal_path.stat().st_size if wal_path.exists() else 0}
        checks, original_rows = _verify_rows(path, samples + warmup)
        repeat = runtime.cycle(snapshot, mode="simulation")
        checks["idempotent_no_second_record"] = repeat["idempotent_replay"] is True and len(_rows(path)[1]) == len(original_rows)
        checks["full_on_every_connection"] = all(value == 2 for value in journal.synchronous_values)
        checks["requested_mode_on_every_connection"] = all(value == ("DELETE" if mode == "DEFAULT_DELETE" else
                                                                   "WAL" if mode == "WAL_KEEPER" else mode)
                                                          for value in journal.effective_modes)
        if mode == "WAL_KEEPER":
            checks["keeper_keeps_committed_wal_until_shutdown"] = wal_before_backup["exists"] and wal_before_backup["bytes"] > 32
            checks["keeper_wal_retained_after_every_writer_close"] = all(item["wal_after_writer_close_bytes"] > 32
                                                                         for item in attempts)
            checks["keeper_does_not_hold_read_transaction"] = all(item["keeper_in_transaction"] is False
                                                                 for item in attempts)
        checks["no_physical_execution"] = all(item["execution"]["physical_action_performed"] is False for item in records)
        trace.phase = "backup"
        backup_path = directory / "online-backup.sqlite"
        started = perf_counter_ns()
        with closing(sqlite3.connect(str(path), timeout=10)) as source:
            with closing(sqlite3.connect(str(backup_path), timeout=10)) as destination:
                destination.execute("PRAGMA synchronous=FULL")
                if destination.execute("PRAGMA synchronous").fetchone()[0] != 2:
                    raise RuntimeError("backup did not retain FULL")
                source.backup(destination)
        backup_ms = (perf_counter_ns() - started) / 1e6
        backup_checks, backup_rows = _verify_rows(backup_path, samples + warmup)
        checks.update({"backup." + key: value for key, value in backup_checks.items()})
        checks["backup_identical_receipts"] = backup_rows == original_rows
        wal_before_shutdown = {"exists": wal_path.exists(),
                               "bytes": wal_path.stat().st_size if wal_path.exists() else 0}
        # Last close must be accounted for, even though it occurs outside cycles.
        trace.phase = "shutdown"
    finally:
        journal.close()
    started = perf_counter_ns()
    with (directory / "receipts.jsonl").open("x", encoding="utf-8", newline="\n") as handle:
        handle.write("".join(_canonical(record) + "\n" for record in records))
        handle.flush()
        os.fsync(handle.fileno())
    receipt_export_ms = (perf_counter_ns() - started) / 1e6
    semantic = [{"action": item["intent"]["action"], "execution": item["execution"],
                 "safety": item["safety"]} for item in records]
    cycle_stages = {name: percentiles([attempt["operations_ms"].get(name, 0) for attempt in attempts])
                    for name in sorted({key for attempt in attempts for key in attempt["operations_ms"]})}
    _write_json(directory / "connection-events.json", trace.events)
    _write_json(directory / "attempts.json", attempts)
    _write_json(directory / "cprofile-top.json", _profile_summary(profile))
    profile.dump_stats(str(directory / "cycle.prof"))
    return {"mode": mode, "passed": all(checks.values()), "checks": checks,
            "effective_mode_counts": dict(Counter(journal.effective_modes)),
            "synchronous_counts": dict(Counter(journal.synchronous_values)),
            "cycle_ms": percentiles([attempt["cycle_ms"] for attempt in attempts]),
            "per_cycle_operations_ms": cycle_stages, "boundary_ms": trace.summary(phase="measurement"),
            "initialization_ms": production_initialization_ms,
            "initial_journal_release_ms": initial_journal_release_ms,
            "isolated_mode_reset": {"before": before_reset_mode, "after": "DELETE",
                                    "ms": isolated_mode_reset_ms, "included_in_cycles": False},
            "shutdown_ms": trace.summary(phase="shutdown"), "online_backup_ms": backup_ms,
            "wal_before_backup": wal_before_backup, "wal_before_shutdown": wal_before_shutdown,
            "wal_after_shutdown_exists": wal_path.exists(),
            "receipt_export_flush_fsync_ms": receipt_export_ms,
            "scientific_fingerprint_sha256": hashlib.sha256(_canonical(semantic).encode()).hexdigest(),
            "record_count": samples + warmup, "artifacts": _artifacts(directory)}


def _run_audit(root, samples):
    """Separately time authentic audit + protected checkpoint, never batch it."""
    from quantum_autopilot.security.audit import AuthenticatedAudit
    from quantum_autopilot.security.vault import SecretVault

    trace = Trace()

    class ProfiledVault(SecretVault):
        def _read(self):
            started = perf_counter_ns()
            try:
                return super()._read()
            finally:
                trace.add("vault_read", started)

        def _write(self, rows):
            started = perf_counter_ns()
            try:
                return super()._write(rows)
            finally:
                trace.add("vault_write_atomic_flush_fsync", started)

        def put_bytes(self, name, value):
            started = perf_counter_ns()
            try:
                return super().put_bytes(name, value)
            finally:
                trace.add("protected_checkpoint_update_total", started)

    directory = root / "separate-audit"
    directory.mkdir()
    vault = ProfiledVault(directory / "isolated.vault")
    audit = AuthenticatedAudit(directory / "audit.jsonl", vault, name="isolated-profile")
    trace.phase = "measurement"
    profile = cProfile.Profile()
    profile.enable()
    for index in range(samples):
        started = perf_counter_ns()
        audit.record("diagnostic-operator", "profile:simulate", "diagnostic-repeater", "completed",
                     {"mode": "simulated", "count": index})
        trace.add("authenticated_audit_record_total", started)
    profile.disable()
    trace.phase = "verification"
    verification = audit.verify()
    _write_json(directory / "connection-events.json", trace.events)
    _write_json(directory / "cprofile-top.json", _profile_summary(profile))
    profile.dump_stats(str(directory / "audit.prof"))
    return {"performed": True, "samples": samples, "vault_mode": vault.mode,
            "verification": verification, "boundaries_ms": trace.summary(phase="measurement"),
            "artifacts": _artifacts(directory), "not_in_controller_diagnostic": True,
            "secrets_exported": False, "batching_used": False}


def run_profile(output_dir, *, samples=15, warmup=3, modes=MODES, audit_samples=0):
    if type(samples) is not int or not 1 <= samples <= 1000:
        raise ValueError("samples must be an integer between 1 and 1000")
    if type(warmup) is not int or not 0 <= warmup <= 100:
        raise ValueError("warmup must be an integer between 0 and 100")
    if type(audit_samples) is not int or not 0 <= audit_samples <= 100:
        raise ValueError("audit_samples must be an integer between 0 and 100")
    modes = tuple(modes)
    if not modes or len(set(modes)) != len(modes) or any(mode not in MODES for mode in modes):
        raise ValueError("one or more distinct supported modes required")
    output_dir = Path(output_dir).resolve()
    if output_dir.exists():
        raise FileExistsError("profile output directory must be new")
    disk = disk_eligibility(output_dir)
    version_allowed = wal_version_eligible(sqlite3.sqlite_version)
    wal_requested = any(mode.startswith("WAL") for mode in modes)
    if wal_requested and not (disk["wal_local_disk_eligible"] and version_allowed):
        raise ValueError("WAL refused: patched SQLite and qualified local fixed storage required")
    output_dir.mkdir(parents=False, exist_ok=False)
    source_root = Path(__file__).resolve().parents[1]
    source_sha256 = {name: hashlib.sha256((source_root / name).read_bytes()).hexdigest() for name in SOURCE_FILES}
    started = datetime.now(timezone.utc).isoformat()
    results = {}
    for mode in modes:
        results[mode] = _run_mode(output_dir, mode, samples, warmup)
    audit = (_run_audit(output_dir, audit_samples) if audit_samples else
             {"performed": False, "reason": "optional separate audit not requested", "not_in_controller_diagnostic": True})
    checks = {f"{mode}.{name}": value for mode, result in results.items() for name, value in result["checks"].items()}
    checks["same_semantic_cycle_outcomes"] = len({item["scientific_fingerprint_sha256"] for item in results.values()}) == 1
    report = {"schema": SCHEMA, "passed": all(checks.values()), "checks": checks,
              "scope": "isolated_software_persistence_prototype", "samples": samples, "warmup": warmup,
              "started_at_utc": started, "finished_at_utc": datetime.now(timezone.utc).isoformat(),
              "physical_action_performed": False, "operator_state_accessed": False, "ml_control_enabled": False,
              "environment": {"os": platform.platform(), "python": sys.version, "processor": platform.processor(),
                              "machine": platform.machine(), "sqlite": sqlite3.sqlite_version,
                              "wal_reset_fix_eligible": version_allowed, "storage": disk, "gui_started": False},
              "source_sha256": source_sha256,
              "modes": results, "separate_authenticated_audit": audit, "references": REFERENCES,
              "adequate_p99_sampling": samples >= 1000,
              "limitations": [
                  "Instrumented private prototype, sequential modes, small sample; no production improvement established.",
                  "Nested boundaries overlap; do not sum all operation means. SQLite VFS flushes are included in commit/close.",
                  "cProfile and boundary overhead included. Kernel fsync/antivirus/storage causes need independent ETW evidence.",
                  "Same synthetic repeater as runtime_diagnostic; no GUI, global-network scaling or physical timing qualified.",
                  "Bare ControllerRuntime does not invoke authenticated audit; audit is an optional separate measurement.",
                  "Per-connection PRAGMA setup included; rollback journal modes other than DELETE are not persistent.",
                  "Fresh diagnostic database reset to DELETE after releasing its production initializer; setup timed separately.",
                  "WAL close includes last-close checkpoint; keeper close and online backup shown separately.",
                  "WAL requires coordinated lifecycle, local storage, online backup or consistent database+WAL snapshot.",
                  "FULL is retained everywhere; persistence guarantees depend on storage honoring synchronization.",
                  "No power-loss/abrupt-process/concurrency qualification performed by this tool.",
              ]}
    _write_json(output_dir / "report.json", report)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--samples", type=int, default=15)
    parser.add_argument("--warmup", type=int, default=3)
    parser.add_argument("--modes", nargs="+", choices=MODES, default=MODES)
    parser.add_argument("--audit-samples", type=int, default=0)
    args = parser.parse_args(argv)
    report = run_profile(args.output_dir, samples=args.samples, warmup=args.warmup,
                         modes=args.modes, audit_samples=args.audit_samples)
    if sys.stdout is not None:
        print(json.dumps({"passed": report["passed"], "sqlite": report["environment"]["sqlite"],
                          "modes": {key: {"cycle_ms": value["cycle_ms"], "shutdown_ms": value["shutdown_ms"],
                                          "online_backup_ms": value["online_backup_ms"]}
                                    for key, value in report["modes"].items()}}, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
