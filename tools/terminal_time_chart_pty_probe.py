"""Focused real Linux PTY qualification of timestamped dense CLI charts."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import math
from pathlib import Path
import re
import time


_path = Path(__file__).with_name("terminal_dashboard_pty_probe.py")
_spec = importlib.util.spec_from_file_location("time_chart_pty_helper", _path)
_helper = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_helper)
_PTY, _ProbeFailure, DOWN = _helper._PTY, _helper._ProbeFailure, _helper.DOWN


def _eligible(snapshot):
    def finite(value):
        return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)

    result = []
    for row in snapshot["live"]["links"]:
        history = row.get("history", {})
        times = history.get("t_s", [])
        values = [history.get(name, []) for name in ("fidelity", "qber", "skr_bps")]
        if (len(times) < 64 or any(len(series) != len(times) for series in values)
                or any(not finite(value) for value in times)
                or any(a >= b for a, b in zip(times, times[1:]))
                or sum(all(finite(series[index]) for series in values) for index in range(len(times))) < 64):
            continue
        result.append(row)
    return sorted(result, key=lambda row: (row.get("status") != "up",
                  -(max(value for value in row["history"]["qber"] if finite(value))
                    - min(value for value in row["history"]["qber"] if finite(value))), row["link_id"]))


def _benchmark(snapshot, identifier):
    from quantum_autopilot.service.terminal_dashboard import cells, render_dashboard
    from quantum_autopilot.service.terminal_live import Publication, TerminalState

    source_hash = hashlib.sha256(json.dumps(snapshot, sort_keys=True, allow_nan=False).encode()).hexdigest()
    state = TerminalState()
    if not state.admit(Publication(snapshot, None, False, False, True, 0, time.monotonic())):
        raise _ProbeFailure("benchmark_authentic_publication_admitted")
    state.selections["links"] = identifier
    cases = []
    for columns, rows in ((80, 24), (120, 40), (200, 60)):
        wall, cpu = [], []
        for _ in range(40):
            started, cpu_started = time.perf_counter(), time.process_time()
            frame = render_dashboard(state, columns=columns, rows=rows)
            cpu.append((time.process_time() - cpu_started) * 1000)
            wall.append((time.perf_counter() - started) * 1000)
        if len(frame.lines) > rows or any(cells(line) > columns for line in frame.lines):
            raise _ProbeFailure("benchmark_frame_geometry")
        cases.append({"columns": columns, "rows": rows, "iterations": 40,
                      "wall": _helper._distribution(wall), "process_cpu": _helper._distribution(cpu),
                      "rendered_lines": len(frame.lines), "style_spans": len(frame.spans)})
    after = hashlib.sha256(json.dumps(snapshot, sort_keys=True, allow_nan=False).encode()).hexdigest()
    return {"schema": "orion.terminal-time-chart.render-benchmark.v1", "cases": cases,
            "snapshot_sha256": source_hash, "snapshot_unchanged": source_hash == after,
            "scope": "pure Overview rendering of authentic dense backend publication; no stepping, I/O or UI injection",
            "fixture_only": True, "hardware_qualified": False}


def probe(command, *, snapshot, pause_check, outage=None, mixed_expected=None):
    from quantum_autopilot.service.terminal_dashboard import short_identifier, time_chart

    started = time.monotonic()
    report = {"schema": "orion.terminal-time-chart.pty.v1", "status": "running", "checks": [],
              "actual_curses": True, "fixture_only": True, "physical_action_performed": False,
              "hardware_qualified": False, "screen_assertions": "current viewport",
              "screen_parser": "pyte 0.8.2 + harness CSI b REP and S/T SU/SD support",
              "public_previews": [], "viewport_sizes": [], "key_observations": []}
    terminal, mono = None, None

    def require(condition, name):
        report["checks"].append({"name": name, "passed": bool(condition)})
        if not condition:
            raise _ProbeFailure(name)

    def selected(identifier):
        width = terminal.columns - 1
        split = width * 46 // 100 if width >= 112 else width
        alias = short_identifier(identifier, min(22, max(8, (split - 4) // 3)))
        pattern = re.compile(r"^\s*>\s+" + re.escape(alias) + r"(?:\s|$)")
        return any(pattern.search(line.strip(" │")) for line in terminal.screen.display[3:-2])

    def key(value, predicate, name):
        require(terminal.key(value, predicate, label=name), name)

    def capture(label):
        frame = {"label": label, "columns": terminal.columns, "rows": terminal.rows, "lines": []}
        for row, text in enumerate(terminal.screen.display):
            runs = []
            for column in range(terminal.columns):
                cell = terminal.screen.buffer[row][column]
                attrs = (cell.fg, cell.bg, cell.bold, cell.reverse, cell.underscore)
                if runs and tuple(runs[-1][name] for name in ("fg", "bg", "bold", "reverse", "underscore")) == attrs:
                    runs[-1]["length"] += 1
                else:
                    runs.append(dict(column=column, length=1, fg=cell.fg, bg=cell.bg, bold=cell.bold,
                                     reverse=cell.reverse, underscore=cell.underscore))
            frame["lines"].append({"text": text, "runs": runs})
        report["public_previews"].append(frame)

    def chart_viewport(label):
        text = terminal.text()
        require(all(name in text for name in ("FIDELITY", "QBER", "SECRET KEY RATE")), label + "_three_metrics")
        require(len(re.findall(r"(?<![\d.])60s\b", text)) >= 3
                and len(re.findall(r"(?<![\d.])0s\b", text)) >= 3, label + "_relative_time_axes")
        require("└" in text and "│" in text, label + "_visible_axis_lines")
        counts = [int(value) for value in re.findall(r"\b(\d+) samples\b", text)]
        require(len(counts) >= 3 and min(counts[:3]) >= 60, label + "_at_least60_received_samples_in_window")
        dots = [(row, column, ord(cell.data) - 0x2800, cell.fg)
                for row in range(terminal.rows) for column in range(terminal.columns)
                if len((cell := terminal.screen.buffer[row][column]).data) == 1
                and 0x2801 <= ord(cell.data) <= 0x28ff]
        require(len(dots) >= 30, label + "_dense_braille_cells")
        require({fg for _row, _column, _bits, fg in dots} == {"d75fd7"}, label + "_actual_pink_chart_color")
        occupied = sum(bits.bit_count() for _row, _column, bits, _fg in dots)
        require(occupied <= sum(counts[:3]), label + "_dots_do_not_exceed_received_samples")
        return {"columns": terminal.columns, "rows": terminal.rows, "visible_sample_counts": counts[:3],
                "braille_cells": len(dots), "occupied_dots": occupied, "chart_foregrounds": sorted({fg for *_rest, fg in dots})}

    try:
        candidates = _eligible(snapshot)
        require(bool(candidates), "real_backend_dense_history_available")
        row = candidates[0]
        identifier, history = row["link_id"], row["history"]
        end = snapshot["live"]["network_time_s"]
        metadata = []
        for name, bounds in (("fidelity", (0., 1.)), ("qber", (0., 1.)), ("skr_bps", (0., None))):
            chart = time_chart(history[name], 48, 3, times=history["t_s"], end_s=end, bounds=bounds)
            require(chart.mode == "time" and chart.sample_count >= 60, "received_" + name + "_actual_time_window")
            require(chart.plotted_count == chart.occupied_dots + chart.collisions, "received_" + name + "_dot_accounting")
            metadata.append({"metric": name, "sample_count": chart.sample_count, "plotted_count": chart.plotted_count,
                             "occupied_dots": chart.occupied_dots, "collisions": chart.collisions,
                             "missing_count": chart.missing_count, "invalid_count": chart.invalid_count,
                             "start_s": chart.start_s, "end_s": chart.end_s,
                             "first_sample_s": chart.first_sample_s, "last_sample_s": chart.last_sample_s})
        report["dense_time_chart"] = {"selected_link_id": identifier, "received_timestamps": len(history["t_s"]),
            "first_t_s": history["t_s"][0], "last_t_s": history["t_s"][-1], "backend_tick": snapshot["live"]["tick"],
            "source": "actual independent simulated engine publications", "metadata_from_received_history": metadata,
            "presentation_contract": "operator GET readbacks; independent backend clock; probe never submits a step",
            "viewport_measurements": []}
        report["render_benchmark"] = _benchmark(snapshot, identifier)
        require(report["render_benchmark"]["snapshot_unchanged"], "pure_render_preserves_backend_publication")
        terminal = _PTY(command)
        require(terminal.wait(lambda: "| OVERVIEW |" in terminal.screen.display[0]
                              and "authenticated backend" in terminal.screen.display[0], deadline_s=30), "authenticated_curses_startup")
        ids = [value["link_id"] for value in snapshot["live"]["links"]]
        for target in ids[1:ids.index(identifier) + 1]:
            key(DOWN, lambda target=target: selected(target), "select_received_link")
        require(selected(identifier), "selected_dense_backend_link")
        key("p", lambda: "PAUSED / cached presentation" in terminal.screen.display[0], "pause_dense_viewport")
        for columns, rows in ((120, 40), (200, 60), (80, 24), (120, 40)):
            previous = terminal.bytes_read
            changed = (columns, rows) != (terminal.columns, terminal.rows)
            if changed:
                terminal.resize(columns, rows)
            require(terminal.wait(lambda: selected(identifier) and "PAUSED" in terminal.screen.display[0], deadline_s=5),
                    f"resize_{columns}x{rows}_selection_preserved")
            if changed:
                require(terminal.wait(lambda: selected(identifier) and "PAUSED" in terminal.screen.display[0],
                                      deadline_s=5, fresh_after=previous), f"resize_{columns}x{rows}_fresh_paint")
            if columns >= 112:
                report["dense_time_chart"]["viewport_measurements"].append(chart_viewport(f"chart_{columns}x{rows}"))
            capture(f"time chart {columns}x{rows}")
            report["viewport_sizes"].append([columns, rows])
        key("\r", lambda: "Selected: " + identifier in terminal.text(), "enter_canonical_dense_link_record")
        key("\r", lambda: selected(identifier), "close_dense_record")
        before, deadline = _helper._tick(pause_check()), time.monotonic() + 20
        after = before
        while after <= before and time.monotonic() < deadline:
            terminal.settle(.25)
            after = _helper._tick(pause_check())
        require(after > before, "backend_advances_while_chart_paused")
        key("p", lambda: "PAUSED" not in terminal.screen.display[0], "resume_dense_chart")
        terminal.send("q")
        terminal.process.wait(timeout=30)
        require(terminal.process.returncode == 0, "q_exit_zero")
        mono = _PTY(command, no_color=True)
        require(mono.wait(lambda: "| OVERVIEW |" in mono.screen.display[0]
                          and "authenticated backend" in mono.screen.display[0], deadline_s=30), "no_color_authenticated_startup")
        require(any(0x2801 <= ord(char) <= 0x28ff for char in mono.text()), "no_color_dense_chart_retained")
        foregrounds = {cell.fg for line in mono.screen.buffer.values() for cell in line.values()}
        backgrounds = {cell.bg for line in mono.screen.buffer.values() for cell in line.values()}
        require(foregrounds == {"default"} and backgrounds == {"default"}, "no_color_preserves_default_colors")
        mono.send("q")
        mono.process.wait(timeout=30)
        require(mono.process.returncode == 0, "no_color_q_exit_zero")
        report["no_color"] = {"colors": sorted(foregrounds), "backgrounds": sorted(backgrounds),
                              "sgr_counts": dict(mono.sgr_counts)}
        report["status"] = "PASS"
    except _ProbeFailure as error:
        report.update(status="FAIL", failed_check=str(error))
    except Exception as error:
        report.update(status="FAIL", error_type=type(error).__name__)
    finally:
        if report["status"] == "FAIL" and terminal is not None:
            capture("failed check")  # Retained privately, excluded from the public whitelist.
        terminals = [value for value in (terminal, mono) if value is not None]
        restored_results = []
        for value in terminals:
            restored = value.close()
            restored_results.append(restored)
            require_name = "main_termios_restored" if value is terminal else "no_color_termios_restored"
            report["checks"].append({"name": require_name, "passed": restored})
            if value is mono:
                report.setdefault("no_color", {})["termios_restored"] = restored
            if not restored:
                report.update(status="FAIL", failed_check=require_name)
        report.update(termios_restored=all(restored_results) if terminals else None,
                      sessions=len(terminals), key_presses=sum(value.key_count for value in terminals),
                      key_exchanges=sum(value.exchange_count for value in terminals),
                      ansi_bytes_consumed=sum(value.bytes_read for value in terminals), duration_s=time.monotonic() - started)
        if terminal is not None:
            report.update(exit_code=terminal.process.returncode, key_observations=list(terminal.latencies),
                          key_latency=_helper._distribution([value["duration_ms"] for value in terminal.latencies]),
                          csi_sequences_consumed=dict(terminal.csi_counts))
        if mono is not None:
            report.setdefault("no_color", {}).update(exit_code=mono.process.returncode)
    return report, []
