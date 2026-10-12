"""Golden ANSI tests plus a real curses NO_COLOR initialization comparison."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    if sys.platform != "linux" or os.geteuid() == 0 or sys.flags.optimize or args.output.exists():
        raise RuntimeError("unprivileged Linux, no optimization, and a new report path required")
    path = Path(__file__).with_name("terminal_dashboard_pty_probe.py")
    spec = importlib.util.spec_from_file_location("parser_probe", path)
    probe = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(probe)
    report = {"schema": "orion.terminal-dashboard.parser-qualification.v2", "status": "running",
              "parser": probe.qualify_parser(), "application_modules_replaced": False,
              "fixture_only": True, "physical_action_performed": False, "hardware_qualified": False,
              "probe_sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "no_color_wrapper_comparison": []}
    child = '''import curses,time
from quantum_autopilot.service.terminal_dashboard import style_attributes
def body(screen, default_colors=False):
    if default_colors: curses.use_default_colors()
    roles=style_attributes(curses)
    screen.addstr(0,0,'NO_COLOR_TITLE',roles['title'])
    screen.addstr(1,0,'NO_COLOR_SELECTION',roles['selected'])
    screen.refresh()
    screen.nodelay(True)
    deadline=time.monotonic()+10
    while time.monotonic()<deadline:
        if screen.getch()==ord('q'): break
        time.sleep(.01)
MODE
'''
    modes = {
        "stdlib_wrapper": "curses.wrapper(body)",
        "stdlib_wrapper_default_colors": "curses.wrapper(lambda screen:body(screen,True))",
        "manual_lifecycle_without_start_color": '''screen=curses.initscr()
try:
    curses.noecho();curses.cbreak();screen.keypad(True);body(screen)
finally:
    screen.keypad(False);curses.echo();curses.nocbreak();curses.endwin()'''}
    for name, code in modes.items():
        terminal = probe._PTY([sys.executable, "-I", "-c", child.replace("MODE", code)], no_color=True)
        value = {"name": name}
        try:
            value["visible"] = terminal.wait(lambda: "NO_COLOR_SELECTION" in terminal.text(), deadline_s=10)
            terminal.settle(.1)
            value["foregrounds"] = sorted({cell.fg for row in terminal.screen.buffer.values() for cell in row.values()})
            value["backgrounds"] = sorted({cell.bg for row in terminal.screen.buffer.values() for cell in row.values()})
            value["sgr_counts"] = dict(terminal.sgr_counts)
            terminal.send("q")
            terminal.process.wait(timeout=10)
            value["exit_code"] = terminal.process.returncode
        finally:
            value["termios_restored"] = terminal.close()
        report["no_color_wrapper_comparison"].append(value)
    rows = {row["name"]: row for row in report["no_color_wrapper_comparison"]}
    report["checks"] = [
        {"name": "stdlib_wrapper_explicit_white_black_reproduced", "passed": rows["stdlib_wrapper"]["foregrounds"] == ["white"]
         and rows["stdlib_wrapper"]["backgrounds"] == ["black"]},
        {"name": "manual_without_start_color_preserves_defaults", "passed": rows["manual_lifecycle_without_start_color"]["foregrounds"] == ["default"]
         and rows["manual_lifecycle_without_start_color"]["backgrounds"] == ["default"]},
        {"name": "all_real_curses_sessions_restore_termios", "passed": all(row["visible"] and row["exit_code"] == 0
         and row["termios_restored"] for row in rows.values())}]
    report["status"] = "PASS" if all(row["passed"] for row in report["checks"]) else "FAIL"
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
