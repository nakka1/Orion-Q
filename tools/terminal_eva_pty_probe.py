"""Exercise the installed Linux CLI's local EVA appearance through a real PTY."""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import time


_path = Path(__file__).with_name("terminal_dashboard_pty_probe.py")
_spec = importlib.util.spec_from_file_location("eva_pty_helper", _path)
_helper = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_helper)
_PTY, _ProbeFailure = _helper._PTY, _helper._ProbeFailure
CONNECTED = "Connected to E.V.A 0-1"
UNPLUG = "Unplug from E.V.A"
MESSAGE = "YOU SHOULD NOT BE ABLE TO CONNECT TO EVA"
EVA_COLORS = {"afffaf", "87ff00", "af5fff", "8700ff", "ff8700", "ff5f5f", "8a8a8a"}


def probe(command, *, snapshot, pause_check, outage=None, mixed_expected=None):
    from quantum_autopilot.service.client import CoreClient
    from quantum_autopilot.service.operator_cli import build_tls_context, parser
    from quantum_autopilot.service.terminal_dashboard import short_identifier

    started = time.monotonic()
    report = {"schema": "orion.terminal-eva.pty.v1", "status": "running", "checks": [],
              "actual_curses": True, "fixture_only": True, "hardware_qualified": False,
              "physical_action_performed": False, "public_previews": [], "viewport_sizes": [],
              "screen_parser": "pyte 0.8.2 + harness CSI b REP and S/T SU/SD support",
              "screen_assertions": "current viewport; real installed CLI; local appearance never authenticates",
              "input_privacy_scope": "masked current viewports; input values omitted from reports",
              "palette": {}, "backend_independence": {}, "ascii_output": {}}
    terminals, terminal, typed_characters = [], None, 0
    subject = snapshot["security"]["subject"]
    identifier = snapshot["live"]["links"][1]["link_id"]

    def require(condition, name):
        report["checks"].append({"name": name, "passed": bool(condition)})
        if not condition:
            raise _ProbeFailure(name)

    def key(value, predicate, name):
        require(terminal.key(value, predicate, label=name, deadline_s=5), name)

    def type_field(value, predicate, name):
        nonlocal typed_characters
        typed_characters += len(value)
        key(value, predicate, name)

    def peer():
        return terminal.screen.display[1].startswith("Peer: " + subject + " | received age ")

    def selected():
        width = terminal.columns - 1
        split = width * 46 // 100 if width >= 112 else width
        alias = short_identifier(identifier, min(22, max(8, (split - 4) // 3)))
        pattern = re.compile(r"^\s*>\s+" + re.escape(alias) + r"(?:\s|$)")
        return any(pattern.search(line.strip(" │")) for line in terminal.screen.display[3:-2])

    def form():
        return "DISPLAY SIGN-IN" in terminal.screen.display[0]

    def mask(length):
        return any(line.rstrip() == ("Password: " + "*" * length).rstrip() for line in terminal.screen.display)

    def notice():
        return (terminal.screen.display[0].rstrip() == "EMERGENCY" and MESSAGE in terminal.text()
                and CONNECTED in terminal.text())

    def active():
        return CONNECTED in terminal.text() and UNPLUG in terminal.screen.display[-1] and not form() and not notice()

    def resize(columns, rows, predicate, footer, name):
        previous = terminal.bytes_read
        terminal.resize(columns, rows)
        require(terminal.wait(lambda: predicate() and footer in terminal.screen.display[rows - 1],
                              fresh_after=previous, deadline_s=5), name)
        report["viewport_sizes"].append([columns, rows])

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

    def palette(name, no_color=False):
        def actual():
            cells = [cell for line in terminal.screen.buffer.values() for cell in line.values()]
            return {cell.fg for cell in cells}, {cell.bg for cell in cells}

        def complete():
            fg, bg = actual()
            return (fg == {"default"} and bg == {"default"}) if no_color else (
                fg <= EVA_COLORS and {"87ff00", "af5fff", "ff8700"} <= fg and bg == {"1c1c1c"})

        require(terminal.wait(complete, deadline_s=5), name + (
            "_terminal_defaults_preserved" if no_color else "_eva_accents_background_and_semantic_colors"))
        fg, bg = actual()
        report["palette"][name] = {"foregrounds": sorted(fg), "backgrounds": sorted(bg)}

    def enter_form(account, phrase, name):
        key("l", lambda: form() and mask(0), name + "_open_empty_form")
        type_field(account + "\r", lambda: form() and mask(0) and any(
            line.startswith("Display login: " + account) for line in terminal.screen.display)
            and terminal.screen.buffer[3][0].reverse, name + "_account")
        type_field(phrase, lambda: form() and mask(len(phrase)), name + "_masked_phrase")
        require(phrase not in terminal.text(), name + "_phrase_not_visible")

    def accept(name, no_color=False):
        enter_form("  S. iKArI  ", "thirdpilot", name)
        key("\r", notice, name + "_exact_notice_and_marker")
        require("thirdpilot" not in terminal.text() and "Display login:" not in terminal.text()
                and not any(line.startswith("Password:") for line in terminal.screen.display), name + "_fields_cleared")
        palette(name + "_notice", no_color=no_color)

    def security(value):
        return {name: value["security"].get(name) for name in ("subject", "source", "roles", "scopes")}

    try:
        cli_args = parser().parse_args(command[4:])
        reader = CoreClient(cli_args.url, build_tls_context(cli_args.ca, cli_args.cert, cli_args.key), timeout_s=15)
        before = reader.console_snapshot(include_static=False)
        require(security(before) == security(snapshot), "initial_mtls_identity_matches_authentic_snapshot")
        terminal = _PTY(command)
        terminals.append(terminal)
        require(terminal.wait(lambda: "authenticated backend" in terminal.screen.display[0] and peer(), deadline_s=30),
                "normal_startup_uses_real_mtls_identity")
        initial_q = terminal.screen.buffer[0][5].fg
        capture("eva standard 120x40")
        key(_helper.DOWN, selected, "select_real_second_link")
        enter_form("Shinji Ikari", "thirdpilot", "wrong_account")
        key("\r", lambda: form() and "Display access phrase not accepted." in terminal.text() and mask(0),
            "wrong_account_rejected_and_phrase_cleared")
        require(CONNECTED not in terminal.text(), "wrong_account_does_not_activate_theme")
        key("\x1b", lambda: peer() and selected() and CONNECTED not in terminal.text(), "cancel_wrong_account")
        enter_form("S. Ikari", "THIRDPILOT", "wrong_case_phrase")
        key("\r", lambda: form() and "Display access phrase not accepted." in terminal.text() and mask(0),
            "phrase_matching_is_case_sensitive")
        key("\x1b", lambda: peer() and selected(), "cancel_wrong_phrase")
        enter_form("S. Ikari", "pqrhvk", "global_shortcut_letters")
        require(terminal.process.poll() is None and "PAUSED" not in terminal.text()
                and "KEYBOARD HELP" not in terminal.text() and CONNECTED not in terminal.text(),
                "raw_form_letters_do_not_invoke_navigation_pause_or_quit")
        key("\x1b", lambda: peer() and selected(), "cancel_clears_masked_form")
        key("L", lambda: form() and mask(0) and any(line.rstrip() == "Display login:" for line in terminal.screen.display),
            "uppercase_login_opens_empty_account")
        key("\t", lambda: terminal.screen.buffer[3][0].reverse, "tab_focuses_password")
        key("\t", lambda: terminal.screen.buffer[2][0].reverse, "tab_returns_to_account")
        type_field("  S. iKArI  \r", lambda: form() and mask(0) and terminal.screen.buffer[3][0].reverse,
                   "valid_casefolded_account")
        type_field("thirdpilot", lambda: form() and mask(10), "valid_phrase_masked")
        require("thirdpilot" not in terminal.text(), "valid_phrase_not_visible")
        capture("eva masked form 120x40")
        key("\r", notice, "valid_local_trigger_exact_notice")
        require("thirdpilot" not in terminal.text() and "Display login:" not in terminal.text()
                and not any(line.startswith("Password:") for line in terminal.screen.display), "accepted_fields_cleared")
        palette("colored_notice")
        capture("eva notice 120x40")
        resize(200, 60, notice, "Enter/Esc return to dashboard", "notice_resize200x60_fresh")
        palette("wide_notice")
        capture("eva notice 200x60")
        key("\r", lambda: active() and peer() and selected(), "notice_dismiss_preserves_real_peer_and_selection")
        require(terminal.screen.display[3].strip() == CONNECTED + " | cosmetic display",
                "active_marker_has_own_dashboard_row_and_cosmetic_qualifier")
        require(UNPLUG in terminal.screen.display[-1], "unplug_action_is_visible")
        for columns, rows in ((80, 24), (120, 40), (200, 60), (120, 40)):
            resize(columns, rows, lambda: active() and peer() and selected(), UNPLUG,
                   f"active_resize{columns}x{rows}_selection_preserved")
        capture("eva dashboard 120x40")
        key("\r", lambda: "Selected: " + identifier in terminal.text() and active(), "eva_enter_canonical_selected_record")
        key("\r", lambda: active() and selected(), "eva_close_canonical_record")
        key("v", lambda: active() and peer() and "Tab/arrows" in terminal.screen.display[-1],
            "eva_plain_layout_retains_marker_and_peer")
        require(any(line.strip() == CONNECTED + " | cosmetic display" for line in terminal.screen.display),
                "plain_marker_retains_cosmetic_qualifier")
        capture("eva plain 120x40")
        key("v", lambda: active() and selected() and "1-8 views" in terminal.screen.display[-1],
            "eva_dashboard_layout_restores_selection")
        key("l", lambda: form() and mask(0), "reopen_while_eva_active")
        key("\x1b", lambda: active() and peer() and selected(), "cancel_form_keeps_active_theme")
        after = reader.console_snapshot(include_static=False)
        require(security(before) == security(after), "local_mode_does_not_replace_mtls_identity_or_roles")
        require(after["live"]["tick"] > before["live"]["tick"], "backend_clock_continues_during_local_display_sequence")
        require(after["health"]["physical_read_only"] is True, "local_theme_does_not_unlock_physical_operation")
        report["backend_independence"] = {"identity_and_roles_unchanged": True, "physical_read_only": True,
                                          "tick_before": before["live"]["tick"], "tick_after": after["live"]["tick"],
                                          "readback_deadline_s": 15, "local_trigger_used_for_backend_auth": False}
        key("U", lambda: peer() and selected() and CONNECTED not in terminal.text() and UNPLUG not in terminal.text(),
            "uppercase_unplug_restores_standard_display")
        require(terminal.screen.buffer[0][5].fg == initial_q, "unplug_restores_original_brand_accent")
        require({cell.bg for line in terminal.screen.buffer.values() for cell in line.values()} == {"default"},
                "unplug_restores_default_background_without_eva_residue")
        capture("eva unplugged 120x40")
        key("l", lambda: form() and mask(0) and any(line.rstrip() == "Display login:" for line in terminal.screen.display),
            "unplug_does_not_persist_form_fields")
        key("\x1b", peer, "cancel_after_unplug")
        terminal.send("q")
        terminal.process.wait(timeout=30)
        require(terminal.process.returncode == 0, "colored_q_exit_zero")
        terminal = _PTY(command, no_color=True)
        terminals.append(terminal)
        require(terminal.wait(lambda: "authenticated backend" in terminal.screen.display[0] and peer(), deadline_s=30),
                "no_color_real_authenticated_startup")
        accept("no_color", no_color=True)
        capture("eva no color notice 120x40")
        key("\x1b", lambda: active() and peer(), "no_color_escape_dismisses_notice_keeps_theme")
        palette("no_color_dashboard", no_color=True)
        key("u", lambda: peer() and CONNECTED not in terminal.text() and UNPLUG not in terminal.text(), "no_color_unplug")
        terminal.send("q")
        terminal.process.wait(timeout=30)
        require(terminal.process.returncode == 0, "no_color_q_exit_zero")
        ascii_command = ["/usr/bin/env", "LC_ALL=C", "LANG=C", command[0], "-X", "utf8=0", *command[1:]]
        terminal = _PTY(ascii_command, no_color=True)
        terminals.append(terminal)
        require(terminal.wait(lambda: "authenticated backend" in terminal.screen.display[0] and peer(), deadline_s=30),
                "ascii_real_authenticated_startup")
        accept("ascii", no_color=True)
        require(all(ord(char) < 128 for char in terminal.text()), "ascii_notice_current_viewport_intact")
        capture("eva ascii notice 120x40")
        key("\r", lambda: active() and peer(), "ascii_notice_dismiss")
        require(all(ord(char) < 128 for char in terminal.text()), "ascii_active_dashboard_intact")
        key("u", lambda: peer() and CONNECTED not in terminal.text(), "ascii_unplug")
        terminal.send("q")
        terminal.process.wait(timeout=30)
        require(terminal.process.returncode == 0, "ascii_q_exit_zero")
        env = {**os.environ, "LC_ALL": "C", "LANG": "C"}
        value = subprocess.run([command[0], "-I", "-X", "utf8=0", "-c",
            "import json,locale,sys;print(json.dumps({'stdout':sys.stdout.encoding,'locale':locale.getencoding(),'utf8_mode':sys.flags.utf8_mode}))"],
            shell=False, env=env, capture_output=True, timeout=5)
        measured = json.loads(value.stdout)
        require(value.returncode == 0 and measured["stdout"] == "ascii" and measured["utf8_mode"] == 0,
                "ascii_child_encoding_control")
        report["ascii_output"] = {"all_current_viewport_ascii": True, "measured_child_encoding": measured,
                                  "screen_encoding_scope": "same launch configuration control; operator not instrumented"}
        terminal = _PTY(command)
        terminals.append(terminal)
        require(terminal.wait(lambda: "authenticated backend" in terminal.screen.display[0] and peer(), deadline_s=30),
                "interrupt_real_authenticated_startup")
        enter_form("S. Ikari", "thirdpilot", "interrupt_masked_form")
        terminal.send(b"\x03")
        terminal.process.wait(timeout=30)
        require(terminal.process.returncode == 130, "ctrl_c_from_masked_form_exit130")
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
            if value.process.poll() is None:
                value.send("\x1b")
                value.settle(.1)
            okay = value.close()
            restored.append(okay)
            report["checks"].append({"name": f"session{index + 1}_termios_restored", "passed": okay})
            if not okay:
                report.update(status="FAIL", failed_check=f"session{index + 1}_termios_restored")
        observations = [item for value in terminals for item in value.latencies]
        report.update(termios_restored=all(restored) if terminals else None, sessions=len(terminals),
                      key_presses=sum(value.key_count for value in terminals), key_exchanges=sum(value.exchange_count for value in terminals),
                      typed_characters=typed_characters, ansi_bytes_consumed=sum(value.bytes_read for value in terminals),
                      key_observations=observations, key_latency=_helper._distribution([item["duration_ms"] for item in observations]),
                      exit_codes=[value.process.returncode for value in terminals], duration_s=time.monotonic() - started)
    return report, []
