"""Exercise an installed operator CLI in a real, bounded Linux PTY.

Assertions inspect pyte's current viewport, never accumulated ANSI output.
The supplied snapshot identifies fixture records; no application modules are
replaced and no backend mutation or hardware operation is performed here.
"""
from __future__ import annotations

from collections import deque
import codecs
import errno
import json
import os
import platform
import re
import select
import signal
import statistics
import struct
import subprocess
import time
from typing import Callable, ContextManager


VIEWS = ("overview", "links", "nodes", "satellites", "reservations", "audit", "raw", "mixed")
UP, DOWN = b"\x1bOA", b"\x1bOB"  # xterm application cursor sequences
CAPTURE_LIMIT = 262144


class _ProbeFailure(Exception):
    pass


class _PTY:
    def __init__(self, command):
        import fcntl
        import pty
        import termios
        import pyte

        self.fcntl, self.termios = fcntl, termios
        self.master, self.slave = pty.openpty()
        self.original_termios = termios.tcgetattr(self.slave)
        self.columns, self.rows = 120, 40
        self.screen = pyte.Screen(self.columns, self.rows)
        self.stream = pyte.Stream(self.screen)
        self.decoder = codecs.getincrementaldecoder("utf-8")("replace")
        self.bytes_read = self.key_count = self.exchange_count = 0
        self.latencies = deque(maxlen=2048)
        self._set_size(self.columns, self.rows)
        env = {**os.environ, "TERM": "xterm-256color", "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8"}
        try:
            self.process = subprocess.Popen(command, stdin=self.slave, stdout=self.slave,
                                            stderr=self.slave, env=env, shell=False,
                                            start_new_session=True, close_fds=True,
                                            # Own a controlling terminal so an actual ^C
                                            # byte invokes the foreground terminal signal.
                                            preexec_fn=lambda: fcntl.ioctl(0, termios.TIOCSCTTY, 0))
        except BaseException:
            os.close(self.master)
            os.close(self.slave)
            raise
        flags = fcntl.fcntl(self.master, fcntl.F_GETFL)
        fcntl.fcntl(self.master, fcntl.F_SETFL, flags | os.O_NONBLOCK)

    def _set_size(self, columns, rows):
        self.fcntl.ioctl(self.slave, self.termios.TIOCSWINSZ, struct.pack("HHHH", rows, columns, 0, 0))

    def text(self):
        return "\n".join(line.rstrip() for line in self.screen.display)

    def body(self):
        return tuple(line.rstrip() for line in self.screen.display[3:-2])

    def pump(self, timeout=.02):
        if not select.select([self.master], [], [], max(0, timeout))[0]:
            return
        # A drain has a finite byte/iteration budget; ANSI history is discarded.
        for _ in range(16):
            try:
                chunk = os.read(self.master, 65536)
            except OSError as error:
                if error.errno in {errno.EAGAIN, errno.EWOULDBLOCK, errno.EIO}:
                    return
                raise
            if not chunk:
                return
            self.bytes_read += len(chunk)
            self.stream.feed(self.decoder.decode(chunk))

    def wait(self, predicate, *, deadline_s=20., fresh_after=None):
        deadline = time.monotonic() + min(30., deadline_s)
        while time.monotonic() < deadline:
            if predicate() and (fresh_after is None or self.bytes_read > fresh_after):
                return True
            if self.process.poll() is not None:
                self.pump(0)
                return bool(predicate() and (fresh_after is None or self.bytes_read > fresh_after))
            self.pump(min(.02, max(0, deadline - time.monotonic())))
        return False

    def settle(self, duration_s):
        deadline = time.monotonic() + min(30., duration_s)
        while time.monotonic() < deadline:
            self.pump(min(.02, max(0, deadline - time.monotonic())))

    def send(self, key, *, repeats=1):
        data = key.encode() if isinstance(key, str) else key
        os.write(self.master, data * repeats)
        self.key_count += repeats
        self.exchange_count += 1

    def key(self, key, predicate, *, label, deadline_s=20.):
        start, previous = time.monotonic(), self.bytes_read
        self.send(key)
        result = self.wait(predicate, deadline_s=deadline_s, fresh_after=previous)
        self.latencies.append({"key": label, "duration_ms": round((time.monotonic() - start) * 1000, 3),
                               "responded": result})
        return result

    def resize(self, columns, rows):
        self.columns, self.rows = columns, rows
        self.screen.resize(lines=rows, columns=columns)
        self._set_size(columns, rows)
        os.kill(self.process.pid, signal.SIGWINCH)

    def close(self):
        if self.process.poll() is None:
            try:
                self.send("q")
                self.process.wait(timeout=5)
            except (OSError, subprocess.TimeoutExpired):
                self.process.terminate()
                try:
                    self.process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait(timeout=2)
        restored = self.termios.tcgetattr(self.slave) == self.original_termios
        os.close(self.master)
        os.close(self.slave)
        return restored


def _distribution(values):
    values = sorted(values)
    if not values:
        return {"count": 0}
    return {"count": len(values), "p50_ms": statistics.median(values),
            "p95_ms": values[min(len(values) - 1, (len(values) * 95 + 99) // 100 - 1)],
            "max_ms": max(values)}


def _tick(value):
    if isinstance(value, dict):
        value = value.get("live", value).get("tick")
    if type(value) is not int:
        raise _ProbeFailure("backend_tick_contract")
    return value


def probe(command: list[str], *, snapshot: dict,
          pause_check: Callable[[], int | dict],
          outage: Callable[[], ContextManager] | None = None,
          mixed_expected: dict | None = None) -> tuple[dict, list[str]]:
    """Return bounded public evidence for a real curses operator session.

    ``command`` must invoke the installed operator's interactive watch command
    without a finite frame limit. Arguments are never included in the report.
    ``pause_check`` performs a backend read and returns its tick or snapshot.
    ``outage``, when supplied, temporarily suspends the independent fixture
    server and must restore it in the context manager's finally block.
    ``mixed_expected`` is an authenticated mixed_status() readback after the
    optional native fixture was admitted. It supplies literal expected records,
    never inferred quantum results.
    """
    started = time.monotonic()
    report = {"schema": "orion.terminal-pty-probe.v1", "status": "running",
              "screen_parser": "pyte 0.8.2", "screen_assertions": "current viewport",
              "actual_curses": True, "fixture_only": True, "physical_action_performed": False,
              "hardware_qualified": False, "checks": [], "viewport_sizes": [],
              "key_latency_capacity": 2048, "capture_character_limit": CAPTURE_LIMIT}
    captures, captured = [], 0
    terminal = interrupt_terminal = None

    def require(condition, name):
        report["checks"].append({"name": name, "passed": bool(condition)})
        if not condition:
            raise _ProbeFailure(name)

    def capture(label):
        nonlocal captured
        if terminal is None:
            return
        value = f"\n--- PTY current viewport: {label} {terminal.columns}x{terminal.rows} ---\n" + terminal.text()
        room = CAPTURE_LIMIT - captured
        if len(captures) >= 64 or room <= 0:
            report["captures_truncated"] = True
            return
        value = value[:min(12000, room)]
        captures.append(value)
        captured += len(value)
        if label in {"startup", "digit satellites", "Mixed eleven admitted native nodes"}:
            # Preserve real curses color attributes for public image rendering.
            # This is viewport evidence, not a reconstructed backend snapshot.
            frame = {"label": label, "columns": terminal.columns, "rows": terminal.rows,
                     "lines": []}
            for row, line in enumerate(terminal.screen.display):
                runs = []
                for column in range(terminal.columns):
                    cell = terminal.screen.buffer[row][column]
                    style = (cell.fg, cell.bold)
                    if runs and (runs[-1]["fg"], runs[-1]["bold"]) == style:
                        runs[-1]["length"] += 1
                    else:
                        runs.append({"column": column, "length": 1,
                                     "fg": cell.fg, "bold": cell.bold})
                frame["lines"].append({"text": line, "runs": runs})
            report.setdefault("public_previews", []).append(frame)
            from quantum_autopilot.service.terminal_brand import brand_spans
            colored = 0
            for row, column, text, role in brand_spans(terminal.screen.display[:3]):
                if role == "accent":
                    cells = [terminal.screen.buffer[row][column + offset]
                             for offset, character in enumerate(text) if not character.isspace()]
                    require(bool(cells) and all(cell.fg != "default" for cell in cells),
                            "colored_brand_" + label.replace(" ", "_"))
                    colored += len(cells)
            require(colored > 0, "visible_brand_" + label.replace(" ", "_"))
            # pyte pads each viewport row, so independently inspect the visible
            # mark at the right edge rather than trusting suffix detection.
            for row in range(3):
                mark_cells = [terminal.screen.buffer[row][column]
                              for column in range(terminal.columns - 20, terminal.columns)
                              if not terminal.screen.buffer[row][column].data.isspace()]
                require(any(cell.fg != "default" for cell in mark_cells),
                        f"colored_mark_row_{row}_" + label.replace(" ", "_"))

    def view(name):
        return f"| {name.upper()} |" in terminal.screen.display[0]

    def selected(identifier):
        pattern = re.compile(r"^\s*>\s+" + re.escape(identifier) + r"(?:\s|$)")
        return any(pattern.search(line) for line in terminal.screen.display[3:-2])

    def details(identifier):
        return any(line.strip() == "Selected: " + identifier for line in terminal.screen.display[3:-2])

    def visible_tick():
        match = re.search(r"Backend tick (\d+)", terminal.text())
        return int(match.group(1)) if match else None

    def key(value, predicate, label, *, deadline_s=20.):
        require(terminal.key(value, predicate, label=label, deadline_s=deadline_s), label)

    def choose(name):
        key(str(VIEWS.index(name) + 1), lambda: view(name), "view_" + name)

    def scroll_to_end(label):
        original, previous = terminal.body(), terminal.body()
        deadline, presses, end = time.monotonic() + 30., 0, None
        while time.monotonic() < deadline and presses < 2048:
            start = time.monotonic()
            terminal.send(DOWN, repeats=16)
            # The application consumes at most one key per loop (~10 ms).
            terminal.settle(.3)
            current = terminal.body()
            presses += 16
            terminal.latencies.append({"key": label + "_down_batch_16",
                                       "duration_ms": round((time.monotonic() - start) * 1000, 3),
                                       "responded": current != previous})
            if current == previous:
                end = current
                break
            previous = current
        require(end is not None, label + "_end_reached_within_deadline")
        require(end != original, label + "_viewport_scrolled")
        capture(label + " end")
        key(UP, lambda: terminal.body() != end, label + "_up_from_end", deadline_s=3.)
        capture(label + " up from end")
        return {"down_presses": presses, "up_from_end_changed_viewport": True}

    def inspect_json_fields(fields, label):
        patterns = {name: re.compile(r'^\s*' + re.escape(json.dumps(name)) + r':\s*'
                                    + re.escape(json.dumps(value)) + r',?\s*$')
                    for name, value in fields.items()}
        found, deadline, presses = set(), time.monotonic() + 30., 0
        while time.monotonic() < deadline and presses <= 2048:
            # Each assertion refers to a currently visible JSON line. Only the
            # names of completed checks survive viewport changes, not ANSI text.
            for name, pattern in patterns.items():
                if name not in found and any(pattern.search(line) for line in terminal.screen.display[3:-2]):
                    require(True, label + "_received_" + name)
                    capture(label + " received " + name)
                    found.add(name)
            if len(found) == len(patterns):
                return presses
            previous = terminal.body()
            terminal.send(DOWN, repeats=8)
            terminal.settle(.2)
            presses += 8
            if terminal.body() == previous:
                break
        for name in patterns.keys() - found:
            require(False, label + "_received_" + name)
        return presses

    try:
        require(platform.system() == "Linux" and isinstance(command, list) and bool(command)
                and all(isinstance(item, str) for item in command), "linux_command_contract")
        import importlib.metadata
        require(importlib.metadata.version("pyte") == "0.8.2", "pinned_screen_parser")
        satellites = sorted(snapshot["live"].get("satellites", {}))
        nodes = [str(row["node_id"]) for row in snapshot["live"].get("nodes", [])]
        require(0 < len(satellites) <= 512 and bool(nodes), "fixture_records_available")
        terminal = _PTY(command)
        require(terminal.wait(lambda: view("overview") and "authenticated backend" in terminal.screen.display[0]
                              and visible_tick() is not None, deadline_s=30.), "authenticated_curses_startup")
        flags = terminal.termios.tcgetattr(terminal.slave)[3]
        require(not flags & (terminal.termios.ECHO | terminal.termios.ICANON), "curses_cbreak_noecho_mode")
        capture("startup")
        for name in VIEWS:
            choose(name)
            if name == "mixed":
                require(terminal.wait(lambda: "authenticated backend" in terminal.screen.display[0],
                                      deadline_s=20.), "mixed_authenticated_readback")
            require(any(line.strip() for line in terminal.screen.display[3:-2]), "body_" + name)
            capture("digit " + name)
        report["digit_views"] = list(VIEWS)
        for name in VIEWS:
            key("\t", lambda name=name: view(name), "tab_" + name)
            capture("Tab " + name)
        report["tab_views"] = list(VIEWS)

        if mixed_expected is not None:
            mixed_ids = sorted(mixed_expected["nodes"])
            expected_ids = [f"N{index:02d}" for index in range(1, 12)]
            reservations = sorted(mixed_expected["reservations"], key=lambda row: row["request_id"])
            require(mixed_ids == expected_ids, "mixed_fixture_eleven_registered_nodes")
            require({row["request_id"] for row in reservations}
                    == {"terminal-native-control", "terminal-unavailable-bridge"}, "mixed_fixture_two_reservations")
            require({row["status"] for row in reservations} == {"reserved", "blocked"}
                    and all(row.get("quantum_result", "not published") is None for row in reservations),
                    "mixed_fixture_received_receipts_without_quantum_result")
            choose("mixed")
            list_patterns = [re.compile(r'^\s*[> ]\s+' + re.escape(identifier) + r'(?:\s|$)')
                             for identifier in mixed_ids]
            require(terminal.wait(lambda: "authenticated backend" in terminal.screen.display[0]
                                  and all(any(pattern.search(line) for line in terminal.screen.display[3:-2])
                                          for pattern in list_patterns), deadline_s=20.), "mixed_eleven_nodes_current_list")
            require(selected("N01"), "mixed_n01_initial_selection")
            capture("Mixed eleven admitted native nodes")
            key("p", lambda: "PAUSED / cached presentation" in terminal.screen.display[0], "mixed_fixture_pause")
            key("\r", lambda: details("N01"), "mixed_n01_enter")
            require("Registered session / clock / capabilities" in terminal.text(), "mixed_registered_session_clock_body")
            configuration = next(row for row in mixed_expected["configuration"]["nodes"] if row["node_id"] == "N01")
            analysis = mixed_expected["nodes"]["N01"].get("analysis") or {}
            coincidence_pairs = analysis.get("coincidence_pairs")
            require(type(coincidence_pairs) is int and coincidence_pairs >= 0, "mixed_fixture_received_coincidence_count")
            mixed_scroll = inspect_json_fields({"clock_domain": configuration["clock_domain"],
                                               "session_id": configuration["session_id"],
                                               "coincidence_pairs": coincidence_pairs}, "mixed_n01")
            key("\r", lambda: selected("N01"), "mixed_n01_details_close")
            choose("reservations")
            require(terminal.wait(lambda: all(any(re.search(r'^\s*[> ]\s+' + re.escape(row["request_id"])
                                                           + r'(?:\s|$)', line)
                                                   for line in terminal.screen.display[3:-2])
                                               for row in reservations), deadline_s=20.), "reservations_two_received_ids_current_list")
            capture("Reservations received reserved and blocked receipts")
            receipt_checks = []
            for index, row in enumerate(reservations):
                identifier = row["request_id"]
                if index:
                    key(DOWN, lambda identifier=identifier: selected(identifier), "reservation_application_down")
                require(selected(identifier), "reservation_selected_" + identifier)
                key("\r", lambda identifier=identifier: details(identifier), "reservation_enter_" + identifier)
                receipt_scroll = inspect_json_fields({"quantum_result": None, "status": row["status"]},
                                                     "reservation_" + identifier)
                key("\r", lambda identifier=identifier: selected(identifier), "reservation_close_" + identifier)
                receipt_checks.append({"request_id": identifier, "received_status": row["status"],
                                       "quantum_result": None, "detail_scroll_down_presses": receipt_scroll})
            key("p", lambda: "PAUSED" not in terminal.screen.display[0], "mixed_fixture_resume")
            report["mixed_fixture"] = {"node_ids": mixed_ids, "selected_node": "N01",
                                       "received_session_id": configuration["session_id"],
                                       "received_clock_domain": configuration["clock_domain"],
                                       "received_coincidence_pairs": coincidence_pairs,
                                       "mixed_detail_scroll_down_presses": mixed_scroll,
                                       "reservation_receipts": receipt_checks,
                                       "quantum_result_inferred": False}

        choose("satellites")
        require(selected(satellites[0]), "satellite_initial_selection")
        visited = []
        for index, identifier in enumerate(satellites):
            if index:
                key(DOWN, lambda identifier=identifier: selected(identifier), "satellite_down")
            require(selected(identifier), "satellite_list_" + identifier)
            key("\r", lambda identifier=identifier: details(identifier), "satellite_enter")
            require("HOUSEKEEPING / SCIENCE" in terminal.text(), "satellite_details_" + identifier)
            capture("satellite " + identifier + " details")
            visited.append(identifier)
            key("\r", lambda identifier=identifier: selected(identifier), "satellite_details_close")
        key(DOWN, lambda: selected(satellites[0]), "application_down_wrap")
        key(UP, lambda: selected(satellites[-1]), "application_up_wrap")
        report["satellite_ids_visited"] = visited

        choose("nodes")
        require(selected(nodes[0]), "node_initial_selection")
        key(UP, lambda: selected(nodes[-1]), "distant_node_application_up")
        capture("distant node selection")
        key("h", lambda: "Tab: next view" in terminal.text(), "help_after_distant_selection")
        require("p/space: pause presentation" in terminal.text(), "help_body_visible")
        capture("help after distant node")
        key("h", lambda: selected(nodes[-1]), "help_close_preserves_selection")
        key("?", lambda: "Tab: next view" in terminal.text(), "question_mark_help")
        key("?", lambda: selected(nodes[-1]), "question_mark_help_close")

        choose("satellites")
        require(selected(satellites[-1]), "satellite_selection_retained_across_views")
        for columns, rows in ((80, 24), (120, 40), (200, 60), (40, 10), (1, 1), (120, 40)):
            previous = terminal.bytes_read
            terminal.resize(columns, rows)
            if (columns, rows) == (1, 1):
                terminal.settle(.3)
                require(terminal.process.poll() is None, "resize_1x1_process_alive")
            else:
                require(terminal.wait(lambda: view("satellites") and selected(satellites[-1]),
                                      deadline_s=5., fresh_after=previous), f"resize_{columns}x{rows}_selection")
                require(len(terminal.screen.display) == rows
                        and all(len(line) <= columns for line in terminal.screen.display), f"resize_{columns}x{rows}_viewport")
                capture(f"resize {columns}x{rows}")
            report["viewport_sizes"].append([columns, rows])
        report["selection_preserved_on_resize"] = True

        choose("overview")
        require(visible_tick() is not None, "pause_tick_visible")
        key("p", lambda: "PAUSED / cached presentation" in terminal.screen.display[0], "pause_p")
        held = visible_tick()
        before = _tick(pause_check())
        after, deadline = before, time.monotonic() + 20.
        while after <= before and time.monotonic() < deadline:
            terminal.settle(.25)
            after = _tick(pause_check())
        require(after > before, "backend_advances_while_curses_paused")
        require(visible_tick() == held, "pause_holds_current_viewport_tick")
        capture("paused with advancing backend")
        key("p", lambda: "PAUSED" not in terminal.screen.display[0]
            and visible_tick() is not None and visible_tick() > held, "resume_p")
        report["pause"] = {"held_display_tick": held, "backend_tick_before": before,
                           "backend_tick_after": after, "resumed_display_tick": visible_tick()}

        key("r", lambda: "Static metadata pending refresh" in terminal.text(), "reload_r_pending", deadline_s=3.)
        require(terminal.wait(lambda: "Static metadata pending refresh" not in terminal.text()
                              and "authenticated backend" in terminal.screen.display[0], deadline_s=20.), "reload_static_completed")
        report["reload_static_completed"] = True

        key("p", lambda: "PAUSED / cached presentation" in terminal.screen.display[0], "scroll_pause")
        terminal.resize(80, 24)
        require(terminal.wait(lambda: view("overview") and "PAUSED" in terminal.screen.display[0], deadline_s=5.), "scroll_resize_ready")
        choose("satellites")
        key("\r", lambda: details(satellites[-1]), "details_scroll_enter")
        capture("details scroll initial")
        report["details_scroll"] = scroll_to_end("details_scroll")
        key("\r", lambda: selected(satellites[-1]), "details_scroll_close")
        choose("raw")
        require('"live"' in terminal.text() or '"commands"' in terminal.text()
                or '"evidence_lanes"' in terminal.text(), "raw_json_current_viewport")
        capture("raw scroll initial")
        report["raw_scroll"] = scroll_to_end("raw_scroll")
        key("p", lambda: "PAUSED" not in terminal.screen.display[0], "scroll_resume")
        terminal.resize(120, 40)
        choose("overview")

        if outage is not None:
            require(terminal.wait(lambda: "authenticated backend" in terminal.screen.display[0]
                                  and visible_tick() is not None, deadline_s=20.), "pre_outage_current")
            before_outage = visible_tick()
            with outage():
                # A view change must remain responsive while the GET is pending.
                choose("links")
                require(terminal.wait(lambda: "STALE / connection unavailable" in terminal.screen.display[0],
                                      deadline_s=20.), "outage_explicit_stale")
                key("h", lambda: "Tab: next view" in terminal.text(), "outage_help_responsive", deadline_s=3.)
                capture("outage stale and responsive help")
                key("h", lambda: "Tab: next view" not in terminal.text(), "outage_help_close", deadline_s=3.)
                require(terminal.process.poll() is None, "outage_operator_alive")
            choose("overview")
            require(terminal.wait(lambda: "authenticated backend" in terminal.screen.display[0]
                                  and visible_tick() is not None and visible_tick() > before_outage,
                                  deadline_s=20.), "outage_read_recovered")
            capture("outage recovery")
            report["outage"] = {"stale_visible": True, "key_response_while_offline": True,
                                "display_tick_before": before_outage, "display_tick_after": visible_tick(),
                                "recovered_without_operator_restart": True}

        capture("before quit")
        terminal.send("q")
        terminal.process.wait(timeout=30)
        require(terminal.process.returncode == 0, "q_exit_code_zero")
        interrupt_terminal = _PTY(command)
        require(interrupt_terminal.wait(lambda: "| OVERVIEW |" in interrupt_terminal.screen.display[0]
                                        and "authenticated backend" in interrupt_terminal.screen.display[0],
                                        deadline_s=30.), "interrupt_session_authenticated_startup")
        interrupt_terminal.send(b"\x03")
        interrupt_terminal.process.wait(timeout=30)
        interrupt_exit_code = interrupt_terminal.process.returncode
        require(interrupt_exit_code == 130, "keyboard_ctrl_c_exit_code_130")
        interrupt_keys = interrupt_terminal.key_count
        interrupt_exchanges = interrupt_terminal.exchange_count
        interrupt_bytes = interrupt_terminal.bytes_read
        interrupt_restored = interrupt_terminal.close()
        interrupt_terminal = None
        require(interrupt_restored, "keyboard_ctrl_c_restores_termios")
        report["keyboard_interrupt"] = {"input": "PTY Ctrl+C byte", "exit_code": interrupt_exit_code,
                                        "termios_restored": interrupt_restored, "key_presses": interrupt_keys,
                                        "key_exchanges": interrupt_exchanges, "ansi_bytes_consumed": interrupt_bytes}
        report["status"] = "PASS"
    except _ProbeFailure as error:
        report.update(status="FAIL", failed_check=str(error))
        capture("failed check")
    except Exception as error:
        # Never emit exception text, CLI arguments, certificate paths or secrets.
        report.update(status="FAIL", error_type=type(error).__name__)
        capture("unexpected failure")
    finally:
        if interrupt_terminal is not None:
            interrupt_restored = interrupt_terminal.close()
            report["keyboard_interrupt"] = {"exit_code": interrupt_terminal.process.returncode,
                                            "termios_restored": interrupt_restored,
                                            "key_presses": interrupt_terminal.key_count,
                                            "key_exchanges": interrupt_terminal.exchange_count,
                                            "ansi_bytes_consumed": interrupt_terminal.bytes_read}
        if terminal is not None:
            restored = terminal.close()
            secondary = report.get("keyboard_interrupt", {})
            report.update(exit_code=terminal.process.returncode, termios_restored=restored,
                          sessions=1 + bool(secondary),
                          key_presses=terminal.key_count + secondary.get("key_presses", 0),
                          key_exchanges=terminal.exchange_count + secondary.get("key_exchanges", 0),
                          ansi_bytes_consumed=terminal.bytes_read + secondary.get("ansi_bytes_consumed", 0),
                          key_latency=_distribution([row["duration_ms"] for row in terminal.latencies]),
                          key_observations=list(terminal.latencies))
            report["checks"].append({"name": "termios_restored_after_exit", "passed": restored})
            if not restored:
                report.update(status="FAIL", failed_check="termios_restored_after_exit")
        report.update(duration_s=round(time.monotonic() - started, 3),
                      captures=len(captures), captured_characters=captured)
    return report, captures
