"""Exercise the installed graphical dashboard in a real Linux PTY.

Assertions inspect pyte's current viewport, never accumulated ANSI output.
The supplied snapshot identifies fixture records; no application modules are
replaced and no backend mutation or hardware operation is performed here.
"""
from __future__ import annotations

from collections import Counter, deque
import codecs
import errno
import hashlib
import json
import os
import platform
import re
import select
import signal
import statistics
import struct
import subprocess
import sys
import time
from typing import Callable, ContextManager


VIEWS = ("overview", "links", "nodes", "satellites", "reservations", "audit", "raw", "mixed")
UP, DOWN = b"\x1bOA", b"\x1bOB"  # xterm application cursor sequences
CAPTURE_LIMIT = 262144


class _ProbeFailure(Exception):
    pass


def _terminal_parser(columns, rows, *, rep=True):
    import pyte

    if not rep:
        screen = pyte.Screen(columns, rows)
        return screen, pyte.Stream(screen)

    class RepeatScreen(pyte.Screen):
        def reset(self):
            super().reset()
            self.last_graphic = None
            if not hasattr(self, "repeat_sequences"):
                self.repeat_sequences = 0

        def draw(self, data):
            super().draw(data)
            if data:
                self.last_graphic = data[-1]

        def repeat_character(self, count=0):
            # xterm CSI Ps b repeats the preceding graphic character.
            # The base pyte parser already bounds CSI parameters to 9999.
            self.repeat_sequences += 1
            if self.last_graphic is not None:
                super().draw(self.last_graphic * min(9999, max(1, count)))

        def _scroll_region(self, count, *, down):
            # SU/SD shift the scrolling region regardless of cursor position.
            # Cursor coordinates and attributes must remain unchanged.
            top, bottom = self.margins or (0, self.lines - 1)
            count = min(bottom - top + 1, max(1, count))
            if down:
                for row in range(bottom, top + count - 1, -1):
                    self.buffer[row] = self.buffer[row - count]
                blanks = range(top, top + count)
            else:
                for row in range(top, bottom - count + 1):
                    self.buffer[row] = self.buffer[row + count]
                blanks = range(bottom - count + 1, bottom + 1)
            for row in blanks:
                self.buffer.pop(row, None)
                for column in range(self.columns):
                    self.buffer[row][column] = self.cursor.attrs._replace(data=" ")
            self.dirty.update(range(top, bottom + 1))

        def scroll_up(self, count=0):
            self._scroll_region(count, down=False)

        def scroll_down(self, count=0):
            self._scroll_region(count, down=True)

    class RepeatStream(pyte.Stream):
        csi = {**pyte.Stream.csi, "b": "repeat_character", "S": "scroll_up", "T": "scroll_down"}
        events = frozenset((*pyte.Stream.events, "repeat_character", "scroll_up", "scroll_down"))

    screen = RepeatScreen(columns, rows)
    return screen, RepeatStream(screen)


def qualify_parser():
    """Golden protocol cases, independent of any application/backend result."""
    import curses
    import pyte

    cases = []

    def check(value, name):
        cases.append({"name": name, "passed": bool(value)})
        if not value:
            raise _ProbeFailure("parser_" + name)

    curses.setupterm("xterm-256color")
    stock = pyte.Screen(12, 2)
    pyte.Stream(stock).feed("HELLO WORLD!\r \x1b[11b")
    check(pyte.Stream.csi.get("b") is None and stock.display[0] != " " * 12, "stock_pyte_missing_rep_reproduced")
    screen, stream = _terminal_parser(12, 2)
    stream.feed("HELLO WORLD!\r \x1b[11b")
    check(screen.display[0] == " " * 12 and screen.cursor.x == 12, "rep_spaces_erase_prior_text")
    screen.reset()
    stream.feed("A\x1b[3b")
    check(screen.display[0] == "AAAA" + " " * 8 and screen.cursor.x == 4, "rep_explicit_count")
    screen.reset()
    stream.feed("Z\x1b[0b\x1b[1b\x1b[b")
    check(screen.display[0] == "ZZZZ" + " " * 8 and screen.cursor.x == 4, "rep_zero_one_omitted_default")
    screen.reset()
    stream.feed("\x1b[31mR\x1b[2b")
    check(screen.display[0].startswith("RRR") and all(screen.buffer[0][x].fg == "red" for x in range(3)),
          "rep_current_character_attributes")
    screen.reset()
    stream.feed("●\x1b[2b")
    check(screen.display[0].startswith("●●●"), "rep_single_cell_unicode_graphic")
    screen.reset()
    screen.resize(lines=2, columns=5)
    stream.feed("Q\x1b[6b")
    check(screen.display == ["QQQQQ", "QQ   "] and screen.cursor.y == 1 and screen.cursor.x == 2,
          "rep_resize_and_wrap")

    def numbered_screen():
        value, parser = _terminal_parser(4, 5)
        for row, letter in enumerate("ABCDE", 1):
            parser.feed(f"\x1b[{row};1H" + letter * 4)
        parser.feed("\x1b[3;2H\x1b[31;42m")
        return value, parser

    screen, stream = numbered_screen()
    cursor = (screen.cursor.x, screen.cursor.y, screen.cursor.attrs)
    stream.feed("\x1b[S")
    check(screen.display == ["BBBB", "CCCC", "DDDD", "EEEE", "    "], "su_omitted_full_screen")
    check((screen.cursor.x, screen.cursor.y, screen.cursor.attrs) == cursor
          and all(screen.buffer[4][x].bg == "green" for x in range(4)), "su_cursor_attributes_and_blank_background")
    stream.feed("\x1b[2T")
    check(screen.display == ["    ", "    ", "BBBB", "CCCC", "DDDD"], "sd_explicit_full_screen")
    check((screen.cursor.x, screen.cursor.y, screen.cursor.attrs) == cursor, "sd_cursor_preserved")
    screen, stream = numbered_screen()
    stream.feed("\x1b[2;4r\x1b[1;3H\x1b[0S")
    check(screen.display == ["AAAA", "CCCC", "DDDD", "    ", "EEEE"]
          and (screen.cursor.x, screen.cursor.y) == (2, 0), "su_zero_subregion_cursor_outside")
    stream.feed("\x1b[1T")
    check(screen.display == ["AAAA", "    ", "CCCC", "DDDD", "EEEE"]
          and (screen.cursor.x, screen.cursor.y) == (2, 0), "sd_one_subregion_cursor_outside")
    stream.feed("\x1b[99T")
    check(screen.display == ["AAAA", "    ", "    ", "    ", "EEEE"], "sd_count_clamped_to_region")
    screen, stream = numbered_screen()
    stream.feed("\x1b[99S")
    check(screen.display == ["    "] * 5, "su_count_clamped_to_screen")
    screen, stream = numbered_screen()
    stream.feed("\x1b[T")
    check(screen.display == ["    ", "AAAA", "BBBB", "CCCC", "DDDD"], "sd_omitted_count_default")
    screen, stream = numbered_screen()
    stream.feed("\x1b[0T")
    check(screen.display == ["    ", "AAAA", "BBBB", "CCCC", "DDDD"], "sd_zero_count_default")
    return {"parser": "pyte 0.8.2 plus harness-only CSI b REP and S/T SU/SD support", "checks": cases,
            "xterm_rep_capability": repr(curses.tigetstr("rep")),
            "primary_protocol": "https://invisible-island.net/xterm/ctlseqs/ctlseqs.html#h2-Functions-using-CSI-_-ordered-by-the-final-character_s_",
            "application_modules_replaced": False}


class _PTY:
    def __init__(self, command, *, no_color=False, term="xterm-256color", parser_rep=True):
        import fcntl
        import pty
        import termios

        self.fcntl, self.termios = fcntl, termios
        self.master, self.slave = pty.openpty()
        self.original_termios = termios.tcgetattr(self.slave)
        self.columns, self.rows = 120, 40
        self.screen, self.stream = _terminal_parser(self.columns, self.rows, rep=parser_rep)
        self.stock_screen, self.stock_stream = _terminal_parser(self.columns, self.rows, rep=False)
        self.decoder = codecs.getincrementaldecoder("utf-8")("replace")
        self.csi_counts, self.sgr_counts, self._csi_tail = Counter(), Counter(), b""
        self.bytes_read = self.key_count = self.exchange_count = 0
        self.full_screen_clears = 0
        self.latencies = deque(maxlen=2048)
        self._set_size(self.columns, self.rows)
        env = {**os.environ, "TERM": term, "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8"}
        if no_color:
            env["NO_COLOR"] = "1"
        else:
            env.pop("NO_COLOR", None)
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
            self.full_screen_clears += chunk.count(b"\x1b[2J")
            controls = self._csi_tail + chunk
            for match in re.finditer(rb"\x1b\[([0-?]*)([ -/]*)([@-~])", controls):
                self.csi_counts[match.group(3).decode("ascii")] += 1
                if match.group(3) == b"m":
                    self.sgr_counts[match.group(1).decode("ascii")] += 1
            tail = controls[controls.rfind(b"\x1b"):] if b"\x1b" in controls else b""
            self._csi_tail = tail if len(tail) <= 128 and not re.fullmatch(rb"\x1b\[([0-?]*)([ -/]*)([@-~])", tail) else b""
            text = self.decoder.decode(chunk)
            self.stream.feed(text)
            self.stock_stream.feed(text)

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
        self.stock_screen.resize(lines=rows, columns=columns)
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


def _process_cpu_ms(pid):
    from pathlib import Path
    # /proc/PID/stat fields after comm start at field 3; utime/stime are 14/15.
    fields = (Path("/proc") / str(pid) / "stat").read_text().rsplit(")", 1)[1].split()
    return (int(fields[11]) + int(fields[12])) * 1000 / os.sysconf("SC_CLK_TCK")


def benchmark(snapshot, mixed, *, iterations=40):
    """Measure actual full-topology publication rendering, not backend work."""
    from quantum_autopilot.service.terminal_dashboard import cells, render_dashboard
    from quantum_autopilot.service.terminal_live import Publication, TerminalState, render

    before = hashlib.sha256(json.dumps(snapshot, sort_keys=True, allow_nan=False).encode()).hexdigest()
    state = TerminalState()
    now = time.monotonic()
    if not state.admit(Publication(snapshot, mixed, False, False, True, 0, now)):
        raise _ProbeFailure("benchmark_actual_publication_admitted")
    cases = []
    for renderer in ("dashboard", "plain"):
        views = VIEWS if renderer == "dashboard" else ("overview",)
        for view in views:
            for columns, rows in ((80, 24), (120, 40), (200, 60)):
                state.view, state.details, state.help, state.scroll = view, False, False, 0
                walls, cpus = [], []
                for _ in range(iterations):
                    wall, cpu = time.perf_counter(), time.process_time()
                    if renderer == "dashboard":
                        frame = render_dashboard(state, columns=columns, rows=rows, now=now)
                        lines, spans = frame.lines, frame.spans
                    else:
                        lines = render(state, columns=columns, rows=rows, now=now).splitlines()
                        spans = ()
                    cpus.append((time.process_time() - cpu) * 1000)
                    walls.append((time.perf_counter() - wall) * 1000)
                if len(lines) > rows or any(cells(line) > columns for line in lines):
                    raise _ProbeFailure("benchmark_frame_geometry")
                if any(not 0 <= row < rows or column < 0 or column + cells(text) > columns
                       for row, column, text, _ in spans):
                    raise _ProbeFailure("benchmark_spans_geometry")
                cases.append({"renderer": renderer, "view": view, "columns": columns, "rows": rows,
                              "iterations": iterations, "wall": _distribution(walls), "process_cpu": _distribution(cpus),
                              "rendered_lines": len(lines), "style_spans": len(spans)})
    after = hashlib.sha256(json.dumps(snapshot, sort_keys=True, allow_nan=False).encode()).hexdigest()
    if after != before:
        raise _ProbeFailure("benchmark_publication_unchanged")
    live = snapshot["live"]
    return {"schema": "orion.terminal-dashboard.render-benchmark.v1", "fixture_only": True,
            "scope": "pure rendering of one authenticated full-topology backend publication; no stepping or I/O",
            "snapshot_sha256": before, "snapshot_unchanged": before == after, "tick": live["tick"],
            "nodes": len(live.get("nodes", [])), "links": len(live.get("links", [])),
            "satellites": len(live.get("satellites", {})),
            "published_protocol_series": len(live.get("technologies", {}).get("protocol_series", [])),
            "sample_columns_are_published_samples": True, "cases": cases,
            "cadence_note": "Raw render cost is not an end-to-end refresh rate. Dashboard performs more layout work; publication/input/age cadence controls painting.",
            "physical_action_performed": False, "hardware_qualified": False}


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
    report = {"schema": "orion.terminal-dashboard.pty.v1", "status": "running",
              "screen_parser": "pyte 0.8.2 + harness CSI b REP and S/T SU/SD support", "screen_assertions": "current viewport",
              "actual_curses": True, "fixture_only": True, "physical_action_performed": False,
              "hardware_qualified": False, "checks": [], "viewport_sizes": [],
              "key_latency_capacity": 2048, "capture_character_limit": CAPTURE_LIMIT}
    captures, captured = [], 0
    terminal = interrupt_terminal = monochrome_terminal = control_terminal = None

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
        if (label == "startup" or label.startswith("digit ") or label.startswith("resize ")
                or label in {"Mixed eleven admitted native nodes", "failed check", "unexpected failure", "classic layout"}):
            # Preserve real curses color attributes for public image rendering.
            # This is viewport evidence, not a reconstructed backend snapshot.
            frame = {"label": label, "columns": terminal.columns, "rows": terminal.rows,
                     "lines": []}
            for row, line in enumerate(terminal.screen.display):
                runs = []
                for column in range(terminal.columns):
                    cell = terminal.screen.buffer[row][column]
                    style = (cell.fg, cell.bg, cell.bold, cell.reverse, cell.underscore)
                    if runs and (runs[-1]["fg"], runs[-1]["bg"], runs[-1]["bold"], runs[-1]["reverse"], runs[-1]["underscore"]) == style:
                        runs[-1]["length"] += 1
                    else:
                        runs.append({"column": column, "length": 1,
                                     "fg": cell.fg, "bg": cell.bg, "bold": cell.bold,
                                     "reverse": cell.reverse, "underscore": cell.underscore})
                frame["lines"].append({"text": line, "runs": runs})
            report.setdefault("public_previews", []).append(frame)
        if label in {"startup", "digit satellites", "Mixed eleven admitted native nodes"}:
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
        # Long IDs are clipped in the dashboard's bounded table column;
        # opening details below must reveal the complete received identifier.
        dashboard = any("╭" in line for line in terminal.screen.display[3:12])
        width = terminal.columns - 1
        if dashboard:
            from quantum_autopilot.service.terminal_dashboard import short_identifier
            split = width * 46 // 100 if width >= 112 else width
            identifier = short_identifier(identifier, min(22, max(8, (split - 4) // 3)))
        pattern = re.compile(r"^\s*>\s+" + re.escape(identifier) + r"(?:\s|$)")
        return any(pattern.search(line.strip(" │")) for line in terminal.screen.display[3:-2])

    def details(identifier):
        return any(line.strip(" │") == "Selected: " + identifier for line in terminal.screen.display[3:-2])

    def visible_tick():
        match = re.search(r"Backend tick (\d+)", terminal.text(), re.IGNORECASE)
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
            # Preserve the original burst pressure to qualify bounded input
            # draining: no per-batch synchronization or extra settling time.
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
                if name not in found and any(pattern.search(line.strip(" │")) for line in terminal.screen.display[3:-2]):
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
        report["parser_qualification"] = qualify_parser()
        require(all(row["passed"] for row in report["parser_qualification"]["checks"]), "terminal_parser_protocol_qualified")
        satellites = sorted(snapshot["live"].get("satellites", {}))
        nodes = [str(row["node_id"]) for row in snapshot["live"].get("nodes", [])]
        require(0 < len(satellites) <= 512 and bool(nodes), "fixture_records_available")
        report["render_benchmark"] = benchmark(snapshot, mixed_expected)
        require(report["render_benchmark"]["snapshot_unchanged"], "benchmark_render_preserves_publication")
        terminal = _PTY(command)
        require(terminal.wait(lambda: view("overview") and "authenticated backend" in terminal.screen.display[0]
                              and visible_tick() is not None, deadline_s=30.), "authenticated_curses_startup")
        flags = terminal.termios.tcgetattr(terminal.slave)[3]
        require(not flags & (terminal.termios.ECHO | terminal.termios.ICANON), "curses_cbreak_noecho_mode")
        capture("startup")
        require("NETWORK LINKS" in terminal.text() and "INSPECTOR" in terminal.text(), "dashboard_panels_current_viewport")
        require("FIDELITY" in terminal.text() and "QBER" in terminal.text()
                and "SECRET KEY RATE" in terminal.text(), "three_received_metric_charts_visible")
        require("samples; order / autoscale" in terminal.text(), "sample_order_axis_explicit")
        colors = {terminal.screen.buffer[row][column].fg
                  for row in range(terminal.rows) for column in range(terminal.columns)
                  if terminal.screen.buffer[row][column].data.strip()}
        require(len(colors - {"default"}) >= 4, "dashboard_semantic_colors_visible")
        report["current_viewport_foreground_colors"] = sorted(colors)
        key("v", lambda: "NETWORK LINKS" not in terminal.text()
            and "Backend tick" in terminal.text(), "classic_layout_toggle")
        capture("classic layout")
        key("v", lambda: "NETWORK LINKS" in terminal.text(), "dashboard_layout_toggle_back")
        wall_before, cpu_before = time.monotonic(), _process_cpu_ms(terminal.process.pid)
        bytes_before, clears_before, tick_before = terminal.bytes_read, terminal.full_screen_clears, visible_tick()
        terminal.settle(3.)
        idle_wall = time.monotonic() - wall_before
        idle_cpu = _process_cpu_ms(terminal.process.pid) - cpu_before
        require(terminal.full_screen_clears == clears_before, "live_paint_without_full_screen_clear")
        report["live_paint_observation"] = {"wall_s": idle_wall, "operator_process_cpu_ms": idle_cpu,
                                            "one_cpu_average_percent": idle_cpu / (idle_wall * 10),
                                            "ansi_bytes_consumed": terminal.bytes_read - bytes_before,
                                            "full_screen_clear_sequences": terminal.full_screen_clears - clears_before,
                                            "tick_before": tick_before, "tick_after": visible_tick(),
                                            "scope": "operator process including TLS reads and display; fixture core excluded"}
        require(terminal.wait(lambda: visible_tick() > tick_before, deadline_s=20.),
                "backend_publication_eventually_advances_after_paint_observation")
        report["live_paint_observation"]["eventual_tick_after"] = visible_tick()
        link_ids = [str(row["link_id"]) for row in snapshot["live"]["links"]]
        require(bool(link_ids), "overview_link_records_available")
        key(UP, lambda: selected(link_ids[-1]), "overview_application_up_selects_last_link")
        key("\r", lambda: view("links") and details(link_ids[-1]), "overview_enter_opens_full_link_record")
        key("\r", lambda: selected(link_ids[-1]), "overview_link_details_close")
        choose("overview")
        require(selected(link_ids[-1]), "overview_shares_link_selection")
        key(DOWN, lambda: selected(link_ids[0]), "overview_application_down_wraps_links")
        report["overview_link_navigation"] = {"last_link_id": link_ids[-1], "wrapped_link_id": link_ids[0],
                                              "enter_reveals_complete_record": True, "selection_shared_with_links": True}
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
            list_patterns = [re.compile(r'^\s*>?\s*' + re.escape(identifier) + r'(?:\s|$)')
                             for identifier in mixed_ids]
            require(terminal.wait(lambda: "authenticated backend" in terminal.screen.display[0]
                                  and all(any(pattern.search(line.strip(" │")) for line in terminal.screen.display[3:-2])
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
            before_reservations_csi = terminal.csi_counts.copy()
            choose("reservations")
            from quantum_autopilot.service.terminal_dashboard import short_identifier
            id_width = min(22, max(8, ((terminal.columns - 1) * 46 // 100 - 4) // 3))
            require(terminal.wait(lambda: all(any(re.search(r'^\s*>?\s*' + re.escape(short_identifier(row["request_id"], id_width))
                                                           + r'(?:\s|$)', line)
                                                   for line in (line.strip(" │") for line in terminal.screen.display[3:-2]))
                                               for row in reservations), deadline_s=20.), "reservations_two_received_ids_current_list")
            report["reservation_transition_parser"] = {
                "csi_counts": dict(terminal.csi_counts - before_reservations_csi),
                "stock_viewport_differing_rows": sum(a != b for a, b in zip(terminal.screen.display, terminal.stock_screen.display)),
                "qualified_viewport_sha256": hashlib.sha256(terminal.text().encode()).hexdigest(),
                "stock_viewport_sha256": hashlib.sha256("\n".join(terminal.stock_screen.display).encode()).hexdigest(),
                "prior_su_sequences": before_reservations_csi["S"], "prior_sd_sequences": before_reservations_csi["T"],
                "qualified_parser_received_ids_visible": True,
                "stock_parser_received_ids_visible": all(any(re.search(r'^\s*>?\s*'
                    + re.escape(short_identifier(row["request_id"], id_width)) + r'(?:\s|$)', line.strip(" │"))
                    for line in terminal.stock_screen.display[3:-2]) for row in reservations)}
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

        terminal.send("r")
        # Fast HTTPS GETs may finish between two paints; observing the pending
        # marker is optional, while authenticated completion remains required.
        pending_seen = terminal.wait(lambda: "Static metadata pending refresh" in terminal.text(), deadline_s=.5)
        require(terminal.wait(lambda: "Static metadata pending refresh" not in terminal.text()
                              and "authenticated backend" in terminal.screen.display[0], deadline_s=20.), "reload_static_completed")
        report["reload_static_completed"] = True
        report["reload_pending_observed"] = pending_seen

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
        monochrome_terminal = _PTY(command, no_color=True)
        require(monochrome_terminal.wait(lambda: "| OVERVIEW |" in monochrome_terminal.screen.display[0]
                                        and "authenticated backend" in monochrome_terminal.screen.display[0],
                                        deadline_s=30.), "no_color_authenticated_startup")
        require("NETWORK LINKS" in monochrome_terminal.text() and "FIDELITY" in monochrome_terminal.text(),
                "no_color_graphical_panels_retained")
        monochrome_colors = {monochrome_terminal.screen.buffer[row][column].fg
                             for row in range(monochrome_terminal.rows) for column in range(monochrome_terminal.columns)}
        monochrome_backgrounds = {monochrome_terminal.screen.buffer[row][column].bg
                                  for row in range(monochrome_terminal.rows) for column in range(monochrome_terminal.columns)}
        report["no_color"] = {"colors": sorted(monochrome_colors), "backgrounds": sorted(monochrome_backgrounds),
                              "sgr_counts": dict(monochrome_terminal.sgr_counts)}
        require(monochrome_colors == {"default"}, "no_color_no_colored_foreground")
        require(monochrome_backgrounds == {"default"}, "no_color_no_colored_background")
        mono_text = monochrome_terminal.text()
        captures.append("\n--- NO_COLOR current viewport ---\n" + mono_text)
        captured += len(mono_text)
        monochrome_terminal.send("q")
        monochrome_terminal.process.wait(timeout=30)
        require(monochrome_terminal.process.returncode == 0, "no_color_q_exit_zero")
        mono_restored = monochrome_terminal.close()
        require(mono_restored, "no_color_restores_termios")
        report["no_color"] = {"exit_code": monochrome_terminal.process.returncode,
                              "termios_restored": mono_restored, "colors": sorted(monochrome_colors),
                              "backgrounds": sorted(monochrome_backgrounds), "sgr_counts": dict(monochrome_terminal.sgr_counts),
                              "key_presses": monochrome_terminal.key_count,
                              "key_exchanges": monochrome_terminal.exchange_count,
                              "ansi_bytes_consumed": monochrome_terminal.bytes_read}
        monochrome_terminal = None
        # Independent protocol control: the installed operator runs against the
        # same actual HTTPS fixture with vt100 and unmodified stock pyte. This
        # terminal profile does not advertise the ncurses REP capability.
        vt_capability = subprocess.run([sys.executable, "-I", "-c",
            "import curses; curses.setupterm('vt100'); print(curses.tigetstr('rep') is not None)"],
            shell=False, capture_output=True, text=True, timeout=5)
        require(vt_capability.returncode == 0 and vt_capability.stdout.strip() == "False", "vt100_terminfo_without_rep")
        control_terminal = _PTY(command, term="vt100", parser_rep=False)
        require(control_terminal.wait(lambda: "| OVERVIEW |" in control_terminal.screen.display[0]
                                      and "authenticated backend" in control_terminal.screen.display[0], deadline_s=30.),
                "vt100_stock_parser_authenticated_startup")
        require(control_terminal.key("4", lambda: "| SATELLITES |" in control_terminal.screen.display[0]
                                     and f"SATELLITES  {len(satellites)}" in control_terminal.text(),
                                     label="vt100_satellites", deadline_s=20.), "vt100_navigate_satellites")
        require(control_terminal.key("\r", lambda: "Selected: " + satellites[0] in control_terminal.text(),
                                     label="vt100_enter_full_record", deadline_s=20.), "vt100_enter_canonical_satellite_record")
        require("HOUSEKEEPING / SCIENCE" in control_terminal.text(), "vt100_housekeeping_visible")
        require(control_terminal.key("\r", lambda: "DETAILS /" not in control_terminal.text(),
                                     label="vt100_close_details", deadline_s=20.), "vt100_close_record")
        control_terminal.send("q")
        control_terminal.process.wait(timeout=30)
        require(control_terminal.process.returncode == 0, "vt100_q_exit_zero")
        control_restored = control_terminal.close()
        require(control_restored, "vt100_restores_termios")
        report["vt100_control"] = {"term": "vt100", "screen_parser": "unmodified pyte 0.8.2", "rep_capability": None,
                                   "exit_code": control_terminal.process.returncode, "termios_restored": control_restored,
                                   "key_presses": control_terminal.key_count, "key_exchanges": control_terminal.exchange_count,
                                   "ansi_bytes_consumed": control_terminal.bytes_read}
        control_terminal = None
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
        if control_terminal is not None:
            control_restored = control_terminal.close()
            report["vt100_control"] = {"exit_code": control_terminal.process.returncode, "termios_restored": control_restored}
        if monochrome_terminal is not None:
            mono_restored = monochrome_terminal.close()
            report.setdefault("no_color", {}).update(exit_code=monochrome_terminal.process.returncode, termios_restored=mono_restored)
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
            mono = report.get("no_color", {})
            control = report.get("vt100_control", {})
            report.update(exit_code=terminal.process.returncode, termios_restored=restored,
                          sessions=1 + bool(secondary) + bool(mono) + bool(control),
                          key_presses=terminal.key_count + secondary.get("key_presses", 0) + mono.get("key_presses", 0) + control.get("key_presses", 0),
                          key_exchanges=terminal.exchange_count + secondary.get("key_exchanges", 0) + mono.get("key_exchanges", 0) + control.get("key_exchanges", 0),
                          ansi_bytes_consumed=terminal.bytes_read + secondary.get("ansi_bytes_consumed", 0) + mono.get("ansi_bytes_consumed", 0) + control.get("ansi_bytes_consumed", 0),
                          rep_sequences_consumed=terminal.screen.repeat_sequences,
                          csi_sequences_consumed=dict(terminal.csi_counts),
                          key_latency=_distribution([row["duration_ms"] for row in terminal.latencies]),
                          key_observations=list(terminal.latencies))
            report["checks"].append({"name": "termios_restored_after_exit", "passed": restored})
            if not restored:
                report.update(status="FAIL", failed_check="termios_restored_after_exit")
        report.update(duration_s=round(time.monotonic() - started, 3),
                      captures=len(captures), captured_characters=captured)
    return report, captures
