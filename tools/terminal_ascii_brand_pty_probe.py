"""Focused installed-CLI PTY branding probe; no application modules replaced."""
from __future__ import annotations

from dataclasses import replace
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import time


_path = Path(__file__).with_name("terminal_dashboard_pty_probe.py")
_spec = importlib.util.spec_from_file_location("ascii_brand_pty_helper", _path)
_helper = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_helper)
_PTY, _ProbeFailure = _helper._PTY, _helper._ProbeFailure


def headless_probe(command, *, snapshot, pause_check, outage=None, mixed_expected=None):
    """Isolate the real finite CLI read; never claim a PTY was exercised."""
    env = {**os.environ, "LC_ALL": "C", "LANG": "C", "COLUMNS": "200", "LINES": "40", "NO_COLOR": "1"}
    value = subprocess.run([command[0], "-X", "utf8=0", *command[1:], "--frames", "1", "--layout", "dashboard"],
                           env=env, shell=False, capture_output=True, timeout=30)
    diagnostic = {"exit_code": value.returncode, "stdout_bytes": len(value.stdout), "stderr_bytes": len(value.stderr),
                  "all_stdout_ascii": all(byte < 128 for byte in value.stdout),
                  "stdout_sha256": hashlib.sha256(value.stdout).hexdigest(),
                  "stderr_sha256": hashlib.sha256(value.stderr).hexdigest(),
                  "unicode_encode_error": b"UnicodeEncodeError" in value.stderr,
                  "argument_error": b"unrecognized arguments" in value.stderr,
                  "timeout_error": b"TimeoutError" in value.stderr,
                  "stale_frame": b"STALE" in value.stdout}
    try:
        error = json.loads(value.stderr)
    except (ValueError, UnicodeError):
        error = {}
    for name in ("status", "error_type"):
        selected = error.get(name)
        if selected in {"invalid_terminal_configuration", "terminal_output_unavailable", "terminal_unavailable",
                        "invalid_input_or_tls_identity", "UnicodeEncodeError", "ValueError", "TimeoutError",
                        "OSError", "RuntimeError", "TypeError"}:
            diagnostic[name] = selected
    escaped_label = snapshot["live"]["source"]["label"].encode("ascii", "backslashreplace")
    checks = [{"name": "headless_ascii_stdout_intact", "passed": value.returncode == 0 and bool(value.stdout)
               and diagnostic["all_stdout_ascii"]},
              {"name": "headless_ascii_exact_backend_label", "passed": b"SOURCE " + escaped_label + b" | age " in value.stdout}]
    return {"schema": "orion.terminal-ascii-brand.headless-isolation.v1",
            "status": "PASS" if all(row["passed"] for row in checks) else "FAIL", "checks": checks,
            "actual_curses": False, "fixture_only": True, "physical_action_performed": False,
            "hardware_qualified": False, "ascii_output": {"headless_attempt": diagnostic}, "public_previews": []}, []


def probe(command, *, snapshot, pause_check, outage=None, mixed_expected=None):
    from quantum_autopilot.service.terminal_brand_art import BRAND_ART
    from quantum_autopilot.service.terminal_dashboard import _wrap, short_identifier
    from quantum_autopilot.service.terminal_live import TerminalState, _body

    started = time.monotonic()
    report = {"schema": "orion.terminal-ascii-brand.pty.v1", "status": "running", "checks": [],
              "actual_curses": True, "fixture_only": True, "physical_action_performed": False,
              "hardware_qualified": False, "screen_assertions": "current viewport; exact complete supplied art rectangle",
              "screen_parser": "pyte 0.8.2 + harness CSI b REP and S/T SU/SD support",
              "public_previews": [], "viewport_sizes": [], "branding": [], "loading": {}, "no_color": {}}
    terminals, terminal, last_art_ink = [], None, []

    def require(condition, name):
        report["checks"].append({"name": name, "passed": bool(condition)})
        if not condition:
            raise _ProbeFailure(name)

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

    def resize(columns, rows, predicate, name):
        previous = terminal.bytes_read
        terminal.resize(columns, rows)
        require(terminal.wait(lambda: predicate() and "1-8 views | Tab next" in terminal.screen.display[rows - 1],
                              deadline_s=5, fresh_after=previous), name)
        report["viewport_sizes"].append([columns, rows])

    def branding(name, *, help_screen=False, ascii_only=False, no_color=False):
        nonlocal last_art_ink
        columns, rows = terminal.columns, terminal.rows
        width = columns - 1
        main_y, journal = (4, 0) if help_screen else (10, 5 if rows >= 36 else 0)
        main_height = rows - main_y - 3 - journal
        state = TerminalState()
        state.help = help_screen
        content = [part for value in _body(state, max(1, width - 4)) for part in _wrap(value, max(1, width - 4))]
        art_y = main_y + 2 + len(content)
        remaining = main_y + main_height - 1 - art_y
        candidates = [replace(art, lines=tuple(line.replace("█", "#") for line in art.lines))
                      if ascii_only else art for art in BRAND_ART.values()
                      if art.width <= width - 4 and art.height <= remaining]
        art = max(candidates, key=lambda value: (value.width * value.height, value.width, value.height, value.key), default=None)
        observation = {"case": name, "columns": columns, "rows": rows,
                       "available_width": width - 4, "available_height": remaining,
                       "selected_art": art.key if art else None, "ascii_only": ascii_only, "no_color": no_color}
        if art is None:
            require(not any("=" * 5 in line or "█" * 3 in line for line in terminal.screen.display[art_y:rows - 3]),
                    name + "_no_partial_art_when_no_variant_fits")
            report["branding"].append(observation)
            return
        x, y = 2 + (width - 4 - art.width) // 2, art_y + (remaining - art.height) // 2
        # Resize/status bytes may precede the completed artwork repaint. Wait
        # for the same exact rectangle, keeping the assertion and finite bound.
        require(terminal.wait(lambda: all(terminal.screen.display[y + index][x:x + art.width] == line.ljust(art.width)
                                          for index, line in enumerate(art.lines)), deadline_s=5),
                name + "_complete_supplied_art_exact_cells")
        colors = {}
        for row, line in enumerate(art.lines):
            for column, char in enumerate(line):
                if char == " ":
                    continue
                role = "wordmark" if art.key.startswith("WORDMARK") and column < art.q_start_col else "accent"
                colors.setdefault(role, set()).add(terminal.screen.buffer[y + row][x + column].fg)
        require(all(values == {"default" if no_color or role == "wordmark" else "d7875f"}
                    for role, values in colors.items()), name + "_actual_ink_colors")
        observation.update(art_width=art.width, art_height=art.height, x=x, y=y,
                           art_text_sha256=hashlib.sha256("\n".join(art.lines).encode()).hexdigest(),
                           colors={role: sorted(values) for role, values in colors.items()})
        report["branding"].append(observation)
        last_art_ink = [(y + row, x + column, char) for row, line in enumerate(art.lines)
                        for column, char in enumerate(line) if char != " "]

    def removed_branding(name):
        visible_ink = [(row, column, char) for row, column, char in last_art_ink
                       if row < terminal.rows and column < terminal.columns]
        require(bool(visible_ink) and all(terminal.screen.buffer[row][column].data != char
                                         for row, column, char in visible_ink), name)

    def selected(identifier):
        width = terminal.columns - 1
        split = width * 46 // 100 if width >= 112 else width
        alias = short_identifier(identifier, min(22, max(8, (split - 4) // 3)))
        pattern = re.compile(r"^\s*>\s+" + re.escape(alias) + r"(?:\s|$)")
        return any(pattern.search(line.strip(" │")) for line in terminal.screen.display[3:-2])

    try:
        require(outage is not None, "real_backend_outage_context_available")
        with outage():
            terminal = _PTY(command)
            terminals.append(terminal)
            require(terminal.wait(lambda: "AUTHENTICATED CORE / WAITING" in terminal.text()
                                  and "Waiting for an authenticated" in terminal.text(), deadline_s=5), "loading_without_publication")
            require("Peer: not authenticated" in terminal.screen.display[1] and "received age no sample" in terminal.screen.display[1],
                    "loading_does_not_invent_identity_or_sample")
            branding("loading120x40")
            capture("brand loading 120x40")
            resize(200, 60, lambda: "AUTHENTICATED CORE / WAITING" in terminal.text(), "loading_resize200x60_fresh")
            branding("loading200x60")
            capture("brand loading 200x60")
            resize(200, 90, lambda: "AUTHENTICATED CORE / WAITING" in terminal.text(), "loading_resize200x90_fresh")
            branding("loading200x90")
            capture("brand loading 200x90")
            report["loading"] = {"publication_received": False, "server_paused_only_in_test": True,
                                 "physical_action_performed": False}
        require(terminal.wait(lambda: "authenticated backend" in terminal.screen.display[0]
                              and "NETWORK LINKS" in terminal.text(), deadline_s=30), "authenticated_publication_replaces_loading")
        require("AUTHENTICATED CORE / WAITING" not in terminal.text(), "loading_title_cleared_after_publication")
        removed_branding("all_previous_loading_ink_cells_replaced_by_publication")
        identifier = snapshot["live"]["links"][1]["link_id"]
        key(_helper.DOWN, lambda: selected(identifier), "select_real_second_link")
        key("p", lambda: "PAUSED / cached presentation" in terminal.screen.display[0], "pause_brand_viewport")
        for columns, rows in ((120, 40), (80, 24), (200, 60), (120, 40)):
            resize(columns, rows, lambda: selected(identifier) and "PAUSED" in terminal.screen.display[0],
                   f"live_resize{columns}x{rows}_selection_preserved")
        capture("brand overview 120x40")
        key("\r", lambda: "Selected: " + identifier in terminal.text(), "enter_canonical_selected_record")
        key("\r", lambda: selected(identifier), "close_selected_record")
        key("h", lambda: "KEYBOARD HELP" in terminal.text(), "help_after_real_selection")
        branding("help120x40", help_screen=True)
        capture("brand help 120x40")
        resize(200, 90, lambda: "KEYBOARD HELP" in terminal.text(), "help_resize200x90_fresh")
        branding("help200x90", help_screen=True)
        require("All requests are GETs" in terminal.text() and "backend continues" in terminal.text(), "help_read_only_instructions_preserved")
        capture("brand help 200x90")
        resize(65, 18, lambda: "KEYBOARD HELP" in terminal.text(), "help_resize65x18_fresh")
        branding("help65x18", help_screen=True)
        resize(120, 40, lambda: "KEYBOARD HELP" in terminal.text(), "help_restore120x40_fresh")
        key("h", lambda: selected(identifier), "selection_preserved_after_help_and_resize")
        removed_branding("all_previous_help_ink_cells_replaced_by_live_view")
        terminal.send("q")
        terminal.process.wait(timeout=30)
        require(terminal.process.returncode == 0, "colored_q_exit_zero")
        terminal = _PTY(command, no_color=True)
        terminals.append(terminal)
        require(terminal.wait(lambda: "authenticated backend" in terminal.screen.display[0], deadline_s=30), "no_color_authenticated_startup")
        resize(200, 60, lambda: "NETWORK LINKS" in terminal.text(), "no_color_resize200x60_fresh")
        key("h", lambda: "KEYBOARD HELP" in terminal.text(), "no_color_help")
        branding("no_color_help200x60", help_screen=True, no_color=True)
        require({cell.fg for line in terminal.screen.buffer.values() for cell in line.values()} == {"default"}
                and {cell.bg for line in terminal.screen.buffer.values() for cell in line.values()} == {"default"},
                "no_color_all_foregrounds_backgrounds_default")
        capture("brand no color 200x60")
        report["no_color"] = {"foregrounds": ["default"], "backgrounds": ["default"]}
        key("h", lambda: "NETWORK LINKS" in terminal.text(), "no_color_close_help")
        removed_branding("no_color_help_ink_cells_replaced_by_live_view")
        terminal.send("q")
        terminal.process.wait(timeout=30)
        require(terminal.process.returncode == 0, "no_color_q_exit_zero")
        ascii_command = [command[0], "-X", "utf8=0", *command[1:]]
        env = {**os.environ, "LC_ALL": "C", "LANG": "C", "COLUMNS": "200", "LINES": "40", "NO_COLOR": "1"}
        encoding = subprocess.run([command[0], "-I", "-X", "utf8=0", "-c",
            "import json,locale,sys; print(json.dumps({'stdout':sys.stdout.encoding,'locale':locale.getencoding(),'utf8_mode':sys.flags.utf8_mode}))"],
            env=env, shell=False, capture_output=True, timeout=5)
        measured_encoding = json.loads(encoding.stdout)
        require(encoding.returncode == 0 and measured_encoding["stdout"] == "ascii"
                and measured_encoding["utf8_mode"] == 0, "ascii_child_effective_stdout_encoding")
        raw_label = snapshot["live"]["source"]["label"]
        escaped_label = raw_label.encode("ascii", "backslashreplace").decode("ascii")
        label_proof = {"backend_label_utf8_sha256": hashlib.sha256(raw_label.encode("utf-8")).hexdigest(),
                       "expected_escaped_label_sha256": hashlib.sha256(escaped_label.encode("ascii")).hexdigest(),
                       "escape_format": "Python ascii backslashreplace; backend label not transliterated"}
        report["ascii_output"] = {"measured_child_encoding": measured_encoding, "backend_label": label_proof}
        terminal = _PTY(["/usr/bin/env", "LC_ALL=C", "LANG=C", *ascii_command], no_color=True)
        terminals.append(terminal)
        require(terminal.wait(lambda: "authenticated backend" in terminal.screen.display[0], deadline_s=30), "ascii_authenticated_startup")
        resize(200, 40, lambda: "NETWORK LINKS" in terminal.text(), "ascii_resize200x40_fresh")
        key("h", lambda: "KEYBOARD HELP" in terminal.text(), "ascii_help")
        branding("ascii_help200x40", help_screen=True, ascii_only=True, no_color=True)
        require("SOURCE " + escaped_label + " | age " in terminal.screen.display[terminal.rows - 2],
                "ascii_footer_preserves_exact_backend_label_as_reversible_escape")
        label_proof.update(row=terminal.rows - 2, column=terminal.screen.display[terminal.rows - 2].index(escaped_label),
                           exact_escaped_label_observed=True)
        require(all(ord(char) < 128 for char in terminal.text()), "ascii_current_viewport_all_ascii")
        require("#" in terminal.text() and "█" not in terminal.text(), "ascii_wordmark_original_cells_with_explicit_block_fallback")
        capture("brand ascii 200x40")
        key("h", lambda: "NETWORK LINKS" in terminal.text(), "ascii_close_help")
        removed_branding("ascii_help_ink_cells_replaced_by_live_view")
        terminal.send("q")
        terminal.process.wait(timeout=30)
        require(terminal.process.returncode == 0, "ascii_q_exit_zero")
        # Branding has no 2-second backend latency acceptance requirement.
        # Preserve the interactive deadline and give this independent finite
        # read its explicit, bounded 15-second nominal profile.
        finite_command = list(ascii_command)
        finite_command[finite_command.index("--timeout-s") + 1] = "15"
        read_started = time.monotonic()
        value = subprocess.run([*finite_command, "--frames", "1", "--layout", "dashboard"], env=env,
                               shell=False, capture_output=True, timeout=30)
        report["ascii_output"]["headless_attempt"] = {
            "read_deadline_s": 15, "elapsed_ms": round((time.monotonic() - read_started) * 1000, 3),
            "exit_code": value.returncode, "stdout_bytes": len(value.stdout),
            "stderr_bytes": len(value.stderr), "all_stdout_ascii": all(byte < 128 for byte in value.stdout),
            "stdout_sha256": hashlib.sha256(value.stdout).hexdigest(),
            "stderr_sha256": hashlib.sha256(value.stderr).hexdigest(),
            "unicode_encode_error": b"UnicodeEncodeError" in value.stderr,
            "argument_error": b"unrecognized arguments" in value.stderr,
            "timeout_error": b"TimeoutError" in value.stderr}
        require(value.returncode == 0 and bool(value.stdout) and all(byte < 128 for byte in value.stdout), "headless_ascii_stdout_intact")
        require("SOURCE " + escaped_label + " | age " in value.stdout.decode("ascii"),
                "headless_ascii_preserves_exact_backend_label_as_reversible_escape")
        report["ascii_output"] = {"exit_code": value.returncode, "bytes": len(value.stdout),
                                  "sha256": hashlib.sha256(value.stdout).hexdigest(), "all_ascii": True,
                                  "encoding_control": "LC_ALL=C and explicit -X utf8=0 while retaining -I",
                                  "measured_child_encoding": measured_encoding,
                                  "backend_label": label_proof,
                                  "headless_attempt": report["ascii_output"]["headless_attempt"],
                                  "screen_encoding_scope": "not directly instrumented in operator; exact block-to-# fallback observed in real PTY"}
        report["status"] = "PASS"
    except _ProbeFailure as error:
        report.update(status="FAIL", failed_check=str(error))
    except Exception as error:
        report.update(status="FAIL", error_type=type(error).__name__)
    finally:
        if report["status"] == "FAIL" and terminal:
            capture("failed check")
        restored = []
        for index, value in enumerate(terminals):
            okay = value.close()
            restored.append(okay)
            report["checks"].append({"name": f"session{index + 1}_termios_restored", "passed": okay})
            if not okay:
                report.update(status="FAIL", failed_check=f"session{index + 1}_termios_restored")
        observations = [item for value in terminals for item in value.latencies]
        report.update(termios_restored=all(restored) if terminals else None, sessions=len(terminals),
                      key_presses=sum(value.key_count for value in terminals), key_exchanges=sum(value.exchange_count for value in terminals),
                      ansi_bytes_consumed=sum(value.bytes_read for value in terminals), key_observations=observations,
                      key_latency=_helper._distribution([value["duration_ms"] for value in observations]),
                      exit_codes=[value.process.returncode for value in terminals], duration_s=time.monotonic() - started)
    return report, []
