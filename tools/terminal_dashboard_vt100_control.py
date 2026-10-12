"""Independent stock-pyte control for the installed Linux dashboard.

Compose this test helper as ``terminal_pty_probe.py`` beside the unchanged
``terminal_dashboard_pty_probe.py`` and the existing live-campaign runner.
The operator/backend remain installed application processes, not substitutes.
"""
from __future__ import annotations

import json
import importlib.util
from pathlib import Path
import re
import time

_path = Path(__file__).with_name("terminal_dashboard_pty_probe.py")
_spec = importlib.util.spec_from_file_location("dashboard_pty_control_helper", _path)
_helper = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_helper)
DOWN, UP, VIEWS = _helper.DOWN, _helper.UP, _helper.VIEWS
_PTY, _ProbeFailure, _distribution = _helper._PTY, _helper._ProbeFailure, _helper._distribution


def probe(command, *, snapshot, pause_check, outage=None, mixed_expected=None):
    """Repeat Mixed -> full JSON -> Reservations and scroll bursts on vt100."""
    from quantum_autopilot.service.terminal_dashboard import short_identifier

    started = time.monotonic()
    report = {"schema": "orion.terminal-dashboard.vt100-control.v1", "status": "running",
              "actual_curses": True, "fixture_only": True, "hardware_qualified": False,
              "physical_action_performed": False, "screen_parser": "unmodified pyte 0.8.2",
              "screen_assertions": "current viewport", "term": "vt100", "checks": []}
    terminal, captures = None, []

    def require(condition, name):
        report["checks"].append({"name": name, "passed": bool(condition)})
        if not condition:
            raise _ProbeFailure(name)

    def view(name):
        return f"| {name.upper()} |" in terminal.screen.display[0]

    def key(value, predicate, name, deadline_s=20):
        require(terminal.key(value, predicate, label=name, deadline_s=deadline_s), name)

    def choose(name):
        key(str(VIEWS.index(name) + 1), lambda: view(name), "vt100_view_" + name)

    def selected(identifier):
        split = (terminal.columns - 1) * 46 // 100
        alias = short_identifier(identifier, min(22, max(8, (split - 4) // 3)))
        pattern = re.compile(r"^\s*>\s+" + re.escape(alias) + r"(?:\s|$)")
        return any(pattern.search(line.strip(" │")) for line in terminal.screen.display[3:-2])

    def details(identifier):
        return any(line.strip(" │") == "Selected: " + identifier for line in terminal.screen.display[3:-2])

    def inspect(fields, label):
        found, deadline, presses = set(), time.monotonic() + 30, 0
        patterns = {name: re.compile(r'^\s*' + re.escape(json.dumps(name)) + r':\s*'
                    + re.escape(json.dumps(value)) + r',?\s*$') for name, value in fields.items()}
        while time.monotonic() < deadline and presses <= 2048:
            for name, pattern in patterns.items():
                if name not in found and any(pattern.search(line.strip(" │")) for line in terminal.screen.display[3:-2]):
                    require(True, label + "_received_" + name)
                    found.add(name)
            if len(found) == len(patterns):
                return
            before = terminal.body()
            terminal.send(DOWN, repeats=8)
            terminal.settle(.2)
            presses += 8
            if terminal.body() == before:
                break
        for name in patterns.keys() - found:
            require(False, label + "_received_" + name)

    def burst_to_end(label):
        original = previous = terminal.body()
        end, presses, deadline = None, 0, time.monotonic() + 30
        while time.monotonic() < deadline and presses < 2048:
            terminal.send(DOWN, repeats=16)
            terminal.settle(.3)
            current = terminal.body()
            presses += 16
            if current == previous:
                end = current
                break
            previous = current
        require(end is not None and end != original, label + "_end_scrolled")
        key(UP, lambda: terminal.body() != end, label + "_up_from_end", deadline_s=3)
        return {"down_presses": presses, "up_from_end_changed_viewport": True}

    try:
        terminal = _PTY(command, term="vt100", parser_rep=False)
        require(terminal.wait(lambda: view("overview") and "authenticated backend" in terminal.screen.display[0],
                              deadline_s=30), "vt100_authenticated_startup")
        for name in VIEWS:
            choose(name)
        for name in VIEWS:
            key("\t", lambda name=name: view(name), "vt100_tab_" + name)
        require(mixed_expected is not None, "vt100_native_mixed_fixture_available")
        choose("mixed")
        mixed_ids = sorted(mixed_expected["nodes"])
        require(mixed_ids == [f"N{index:02d}" for index in range(1, 12)], "vt100_eleven_mixed_nodes_readback")
        require(terminal.wait(lambda: all(any(re.search(r'^\s*>?\s*' + re.escape(identifier) + r'(?:\s|$)',
                    line.strip(" │")) for line in terminal.screen.display[3:-2]) for identifier in mixed_ids)),
                "vt100_eleven_mixed_nodes_current_list")
        key("p", lambda: "PAUSED / cached presentation" in terminal.screen.display[0], "vt100_pause")
        key("\r", lambda: details("N01"), "vt100_n01_full_record")
        configuration = next(row for row in mixed_expected["configuration"]["nodes"] if row["node_id"] == "N01")
        analysis = mixed_expected["nodes"]["N01"].get("analysis") or {}
        inspect({"clock_domain": configuration["clock_domain"], "session_id": configuration["session_id"],
                 "coincidence_pairs": analysis["coincidence_pairs"]}, "vt100_n01")
        key("\r", lambda: selected("N01"), "vt100_n01_close")
        before = terminal.csi_counts.copy()
        choose("reservations")
        reservations = sorted(mixed_expected["reservations"], key=lambda row: row["request_id"])
        id_width = min(22, max(8, ((terminal.columns - 1) * 46 // 100 - 4) // 3))
        require(terminal.wait(lambda: all(any(re.search(r'^\s*>?\s*'
                    + re.escape(short_identifier(row["request_id"], id_width)) + r'(?:\s|$)', line.strip(" │"))
                    for line in terminal.screen.display[3:-2]) for row in reservations)),
                "vt100_reservations_two_received_ids_current_list")
        report["reservation_transition_csi_counts"] = dict(terminal.csi_counts - before)
        for index, row in enumerate(reservations):
            identifier = row["request_id"]
            if index:
                key(DOWN, lambda identifier=identifier: selected(identifier), "vt100_reservation_down")
            require(selected(identifier), "vt100_received_receipt_selected_" + str(index))
            key("\r", lambda identifier=identifier: details(identifier), "vt100_receipt_canonical_detail_" + str(index))
            inspect({"quantum_result": None, "status": row["status"]}, "vt100_receipt_" + str(index))
            key("\r", lambda identifier=identifier: selected(identifier), "vt100_receipt_close_" + str(index))
        choose("satellites")
        identifier = sorted(snapshot["live"]["satellites"])[0]
        key("\r", lambda: details(identifier), "vt100_satellite_canonical_detail")
        report["details_scroll"] = burst_to_end("vt100_details_scroll")
        key("\r", lambda: selected(identifier), "vt100_satellite_close")
        choose("raw")
        report["raw_scroll"] = burst_to_end("vt100_raw_scroll")
        terminal.send("q")
        terminal.process.wait(timeout=30)
        require(terminal.process.returncode == 0, "vt100_q_exit_zero")
        report["status"] = "PASS"
    except _ProbeFailure as error:
        report.update(status="FAIL", failed_check=str(error))
        if terminal is not None:
            captures.append("\n--- vt100 failed current viewport ---\n" + terminal.text())
    except Exception as error:
        report.update(status="FAIL", error_type=type(error).__name__)
    finally:
        if terminal is not None:
            restored = terminal.close()
            report.update(termios_restored=restored, exit_code=terminal.process.returncode,
                          key_presses=terminal.key_count, key_exchanges=terminal.exchange_count,
                          ansi_bytes_consumed=terminal.bytes_read, csi_sequences_consumed=dict(terminal.csi_counts),
                          key_latency=_distribution([row["duration_ms"] for row in terminal.latencies]))
            report["checks"].append({"name": "vt100_termios_restored", "passed": restored})
            if not restored:
                report.update(status="FAIL", failed_check="vt100_termios_restored")
        report["duration_s"] = round(time.monotonic() - started, 3)
    return report, captures
