"""Dashboard geometry, safe styling and faithful backend sample presentation."""
from copy import deepcopy
import io
import math
import os
from pathlib import Path
import subprocess
import sys
import unicodedata

import pytest

from quantum_autopilot.service import terminal_dashboard, terminal_live
from quantum_autopilot.service.terminal_dashboard import dashboard_key, render_dashboard, sample_chart, style_attributes
from quantum_autopilot.service.terminal_live import Publication, TerminalState, VIEWS, render


ROLES = {"plain", "border", "title", "muted", "good", "warn", "bad", "selected", "accent", "wordmark", "chart"}


def snapshot(revision=1):
    """Public local fixture only; no acquisition, instrument or live backend."""
    return {
        "schema": "orion.console.remote.v1",
        "health": {"physical_read_only": True, "automation_allowed": False, "autopilot_version": "dashboard-fixture"},
        "security": {"subject": "fixture-peer", "source": "certificate"}, "topology": {},
        "live": {
            "tick": revision, "publication_revision": revision, "topology_version": 1,
            "engine_session_id": "dashboard-fixture-session", "network_time_s": revision * 7,
            "source": {"kind": "simulated", "label": "explicit dashboard interaction fixture"}, "policy": {},
            "links": [{"link_id": "A-B", "a": "A", "b": "B", "status": "degraded",
                       "fidelity": None, "qber": 0., "skr_bps": None,
                       "history": {"t_s": [0, 1, 4, 10], "fidelity": [0., None, .6, .9],
                                   "qber": [0., .02, None, .01], "skr_bps": [None] * 4}}],
            "nodes": [{"node_id": "A", "status": "no_input", "links": ["A-B"]},
                      {"node_id": "B", "status": "degraded", "links": ["A-B"]}],
            "satellites": {"SAT": {"housekeeping_valid": False,
                "position_scope": "configured prediction; no received position",
                "housekeeping": {}, "transmitting_links": [],
                "bridges": [{"session_id": "fixture-pass", "status": "waiting", "origin": "simulated",
                             "metrics": {"qber": None, "conditional_key_estimate_bps": None}}]}},
            "events": [{"event_id": "fixture-event", "message": "explicit interaction fixture"}],
            "technologies": {"protocol_series": []},
        },
    }


def mixed_snapshot():
    return {
        "schema": "orion.mixed-controller.state.v1", "physical_action_performed": False,
        "configuration": {"nodes": [{"node_id": "A", "session_id": "fixture-capture-A", "clock_domain": "fixture-clock"}],
                          "links": [{"link_id": "A-B", "a": "A", "b": "B",
                                     "classical_channel_id": "control-AB", "quantum_channel_id": "quantum-AB"}]},
        "nodes": {"A": {"status": "available", "sequence": 12, "time_tag_count": 21,
                        "lost_events": 2, "available": 8, "analysis": {"coincidence_pairs": 3}}},
        "reservations": [{"request_id": "fixture-reserved", "status": "reserved", "quantum_result": None},
                         {"request_id": "fixture-blocked", "status": "blocked", "quantum_result": None}],
    }


def admitted_state(view="links", selected=None, raw=None):
    state = TerminalState(view=view, selected=selected)
    assert state.admit(Publication(snapshot() if raw is None else raw, mixed_snapshot(), False, False, True, 0, 100.))
    return state


def cells(value):
    return sum(0 if unicodedata.combining(char) else
               2 if unicodedata.east_asian_width(char) in {"W", "F"} else 1 for char in value)


def assert_frame_geometry(frame, columns, rows):
    assert isinstance(frame.lines, tuple) and isinstance(frame.spans, tuple)
    assert frame.text() == "\n".join(frame.lines)
    assert 1 <= len(frame.lines) <= rows
    assert all(cells(line) <= columns for line in frame.lines)
    assert all(not any(unicodedata.category(char) == "Cc" for char in line) for line in frame.lines)
    offsets = []
    for line in frame.lines:
        positions, occupied = {}, 0
        for offset, char in enumerate(line):
            positions.setdefault(occupied, []).append(offset)
            occupied += cells(char)
        positions.setdefault(occupied, []).append(len(line))
        offsets.append(positions)
    for row, column, text, role in frame.spans:
        assert type(row) is int and type(column) is int and role in ROLES
        assert 0 <= row < len(frame.lines) and 0 <= column <= columns
        assert column + cells(text) <= columns
        line = frame.lines[row]
        assert any(line[offset:offset + len(text)] == text for offset in offsets[row].get(column, [])), (row, column, text, line)
        assert not any(unicodedata.category(char) == "Cc" for char in text)


@pytest.mark.parametrize("columns", range(1, 241))
def test_all_widths_and_views_stay_in_display_cells_without_losing_selection(columns):
    raw = snapshot()
    raw["security"]["subject"] = "水e\u0301-fixture"
    raw["live"]["nodes"][0]["reason"] = "水水 e\u0301 backend text"
    state = admitted_state(raw=raw)
    for index, view in enumerate(VIEWS, 1):
        state.key(str(index))
        for rows in (1, 8, 24):
            output = render_dashboard(state, columns=columns, rows=rows, now=105., ascii_only=columns % 2 == 0)
            assert_frame_geometry(output, columns, rows)
            assert output.lines[0].startswith("OrionQ"[:min(6, max(1, columns - 1))])
            assert state.view == view


def test_ascii_fallback_is_ascii_when_backend_identifiers_are_ascii():
    state = admitted_state()
    for index in range(1, 9):
        state.key(str(index))
        output = render_dashboard(state, columns=160, rows=50, now=105., ascii_only=True)
        assert output.text().isascii()
        assert all(text.isascii() for _, _, text, _ in output.spans)
        assert_frame_geometry(output, 160, 50)
    state.key("2")
    text = render_dashboard(state, columns=160, rows=50, now=105., ascii_only=True).text()
    assert "fixture-peer" in text and "A-B" in text and "physical" in text.lower()


@pytest.mark.parametrize("view", VIEWS)
def test_untrusted_terminal_controls_never_reach_lines_or_spans(view):
    payload = "canary\x1b[2J\x1b]52;c;clipboard\x07\r\n\x9b31m"
    raw = snapshot()
    raw["security"]["subject"] = payload
    raw["live"]["source"]["label"] = payload
    raw["live"]["nodes"][0]["reason"] = payload
    raw["live"]["links"][0]["provenance"] = {"status_reasons": [payload]}
    raw["live"]["satellites"]["SAT"]["position_scope"] = payload
    raw["live"]["events"][0]["message"] = payload
    state = admitted_state(view, raw=raw)
    state.mixed["nodes"]["A"]["reason"] = payload
    original_snapshot, original_mixed = deepcopy(state.snapshot), deepcopy(state.mixed)
    for details in (False, True):
        state.details = details
        output = render_dashboard(state, columns=120, rows=40, now=105.)
        assert_frame_geometry(output, 120, 40)
        assert "\x1b" not in output.text() and "\x9b" not in output.text()
    assert state.snapshot == original_snapshot and state.mixed == original_mixed


def test_waiting_state_does_not_claim_an_authenticated_peer_or_make_samples():
    state = TerminalState(view="links")
    output = render_dashboard(state, columns=160, rows=40, now=105.)
    assert "STALE" in output.text() and "not authenticated" in output.text()
    assert "authenticated backend" not in output.lines[0]
    assert "fixture-peer" not in output.text()
    assert not any(role == "chart" for _, _, _, role in output.spans)
    assert state.snapshot is None


def test_pause_freezes_visible_tick_while_publications_and_stale_state_continue():
    state = admitted_state("overview")
    state.key("p")
    before = render_dashboard(state, columns=160, rows=40, now=105.)
    assert state.admit(Publication(snapshot(2), None, False, False, False, 0, 106.))
    paused = render_dashboard(state, columns=160, rows=40, now=107.)
    assert "PAUSED" in paused.text()
    assert state.visible["live"]["tick"] == 1 and state.snapshot["live"]["tick"] == 2
    # Header age can change, but backend cards must still show the frozen tick/time.
    assert before.lines[3:-2] == paused.lines[3:-2]
    assert not state.admit(Publication(None, None, True, False, False, 0, 108.))
    stale = render_dashboard(state, columns=160, rows=40, now=109.)
    assert "PAUSED" in stale.text() and "STALE" in stale.text()
    state.key("p")
    resumed = render_dashboard(state, columns=160, rows=40, now=110.)
    assert state.visible["live"]["tick"] == 2 and before.lines[3:] != resumed.lines[3:]


def test_selected_link_and_its_chart_remain_visible_after_resize_and_long_list():
    raw = snapshot()
    template = raw["live"]["links"][0]
    raw["live"]["links"] = [dict(deepcopy(template), link_id=f"L{index:02d}") for index in range(40)]
    state = admitted_state("links", "L39", raw)
    for columns, rows in ((120, 40), (20, 8), (1, 1), (240, 60), (100, 24)):
        output = render_dashboard(state, columns=columns, rows=rows, now=105.)
        assert_frame_geometry(output, columns, rows)
        assert state.selections["links"] == "L39"
        if columns >= 100:
            assert any("L39" in text and role == "selected" for _, _, text, role in output.spans)
            assert any(role == "chart" for _, _, _, role in output.spans)


def test_details_scroll_is_reversible_and_help_does_not_inherit_list_position():
    state = admitted_state("satellites", "SAT")
    state.snapshot["live"]["satellites"]["SAT"]["housekeeping"] = {f"entry-{index:03d}": index for index in range(90)}
    state.key("enter")
    first = render_dashboard(state, columns=120, rows=24, now=105.)
    for _ in range(250):
        state.key("down")
        render_dashboard(state, columns=120, rows=24, now=105.)
    bottom = render_dashboard(state, columns=120, rows=24, now=105.)
    scroll = state.scroll
    state.key("up")
    prior = render_dashboard(state, columns=120, rows=24, now=105.)
    assert state.scroll == scroll - 1 and first.text() != bottom.text() and prior.text() != bottom.text()
    state.key("h")
    help_frame = render_dashboard(state, columns=120, rows=24, now=105.)
    assert "arrows: select / scroll" in help_frame.text() and "All requests are GETs" in help_frame.text()
    assert state.selections["satellites"] == "SAT"


@pytest.mark.parametrize("columns", [64, 65])
def test_small_dashboard_help_keeps_backend_and_navigation_instructions_visible(columns):
    state = admitted_state("links", "A-B")
    state.scroll = 200
    state.key("h")
    text = render_dashboard(state, columns=columns, rows=18, now=105.).text()
    assert "All requests are GETs" in text and "backend continues" in text
    assert "arrows: select / scroll" in text and "Enter: details" in text
    assert state.selections["links"] == "A-B"


def test_overview_keyboard_selects_real_links_and_enter_opens_their_details():
    raw = snapshot()
    raw["live"]["links"].append(dict(deepcopy(raw["live"]["links"][0]), link_id="C-D", a="C", b="D"))
    state = admitted_state("overview", raw=raw)
    render_dashboard(state, columns=120, rows=40, now=105.)
    assert dashboard_key(state, "down") and state.view == "overview"
    assert state.selections["links"] == "C-D"
    assert dashboard_key(state, "enter") and state.view == "links" and state.details
    assert "C-D" in render_dashboard(state, columns=120, rows=40, now=105.).text()
    assert not dashboard_key(state, "q")


def test_stale_mixed_reservations_do_not_appear_current_when_retained_records_exist():
    state = admitted_state("reservations", "fixture-reserved")
    assert not state.admit(Publication(None, None, True, False, False, 0, 101., True))
    frame = render_dashboard(state, columns=160, rows=40, now=105.)
    assert "STALE" in frame.lines[0]
    assert any("RESERVATIONS" in line and "STALE" in line for line in frame.lines[3:])
    assert state.mixed["reservations"][0]["status"] == "reserved"


def test_satellite_housekeeping_is_bounded_before_wrapping_large_input(monkeypatch):
    state = admitted_state("satellites", "SAT")
    state.snapshot["live"]["satellites"]["SAT"]["housekeeping"] = {"fixture_blob": "x" * 200000}
    actual_wrap, lengths = terminal_dashboard._wrap, []

    def bounded_wrap(value, width):
        lengths.append(len(str(value)))
        return actual_wrap(value, width)

    monkeypatch.setattr(terminal_dashboard, "_wrap", bounded_wrap)
    output = render_dashboard(state, columns=160, rows=40, now=105.)
    assert_frame_geometry(output, 160, 40)
    assert lengths and max(lengths) <= 6100


@pytest.mark.parametrize("layout,expected", [("auto", "PLAIN"), ("plain", "PLAIN"), ("dashboard", "DASHBOARD")])
def test_finite_watch_keeps_plain_default_and_explicit_dashboard_without_ansi(monkeypatch, layout, expected):
    class LocalClient:
        def __init__(self):
            self.calls = []

        def console_snapshot(self, *, include_static):
            self.calls.append(include_static)
            return snapshot()

        def step(self, *args, **kwargs):
            raise AssertionError("painting must never advance the backend")

    client, stream = LocalClient(), io.StringIO()
    monkeypatch.setattr(terminal_live, "get_terminal_size", lambda _: os.terminal_size((120, 40)))
    monkeypatch.setattr(terminal_live, "render", lambda *args, **kwargs: "PLAIN")
    monkeypatch.setattr(terminal_dashboard, "render_dashboard",
                        lambda *args, **kwargs: terminal_dashboard.DashboardFrame(("DASHBOARD",), ()))
    assert terminal_live.watch(client, frames=1, interval_s=.1, stream=stream, layout=layout) == 0
    assert stream.getvalue() == expected + "\n\n" and client.calls == [True]


def test_protocol_chart_uses_published_at_s_and_only_associated_nodes():
    raw = snapshot()
    raw["live"]["links"] = []
    raw["live"]["technologies"]["protocol_series"] = [
        {"session_id": "fixture-protocol", "node_ids": ["A"], "technology": "tf_qkd",
         "metric": "phase_reference_residual_rad", "unit": "rad",
         "samples": [{"at_s": 1.25, "value": .1}, {"at_s": 4.5, "value": None}, {"at_s": 11.75, "value": .2}]},
        {"session_id": "unrelated", "node_ids": ["X"], "technology": "fixture-only",
         "metric": "unrelated-canary", "unit": "Hz", "samples": [{"at_s": 8., "value": 12.}]},
    ]
    output = render_dashboard(admitted_state("nodes", "A", raw), columns=240, rows=60, now=105.)
    assert "phase_reference_residual_rad" in output.text()
    assert "1.25" in output.text() and "11.75" in output.text()
    assert "unrelated-canary" not in output.text()


@pytest.mark.parametrize("has_timestamp", [False, True])
def test_journal_uses_published_net_event_timestamp_or_tick_and_subject(has_timestamp):
    raw = snapshot()
    raw["live"]["events"] = [{"event_id": "fixture-net-event", "at_utc": "2026-10-11T12:34:56Z",
                              "tick": 42, "subject": "A-B", "kind": "link_state", "message": "fixture NetEvent"}]
    if not has_timestamp:
        raw["live"]["events"][0].pop("at_utc")
    frame = render_dashboard(admitted_state(raw=raw), columns=160, rows=40, now=105.)
    event = next(line for line in frame.lines if "fixture NetEvent" in line)
    assert "A-B" in event
    assert ("2026-10-11T12:34:56Z" if has_timestamp else "42") in event


def test_optional_provenance_and_null_science_metrics_remain_no_input():
    raw = snapshot()
    raw["live"]["links"][0]["provenance"] = {"technology": None}
    raw["live"]["nodes"][0]["provenance"] = "fixture unstructured optional annotation"
    raw["live"]["satellites"]["SAT"]["bridges"][0]["metrics"] = None
    for view, selected in (("links", "A-B"), ("nodes", "A"), ("satellites", "SAT")):
        state = admitted_state(view, selected, raw)
        output = render_dashboard(state, columns=160, rows=50, now=105.)
        assert_frame_geometry(output, 160, 50)
        assert not state.stale
        if view == "satellites":
            assert "no input" in output.text().lower() and "fixture-pass" in output.text()


def test_mixed_identity_cannot_be_inherited_from_console_or_local_session():
    state = admitted_state("links")
    state.key("8")
    output = render_dashboard(state, columns=160, rows=50, now=105.)
    assert "identity not published by mixed status" in output.text()
    assert "fixture-peer" not in output.text() and "no inferred F/QBER" in output.text()
    assert not any(role == "chart" for _, _, _, role in output.spans)
    state.key("enter")
    details = render_dashboard(state, columns=160, rows=70, now=105.).text()
    assert "fixture-capture-A" in details and "fixture-clock" in details
    assert "coincidence_pairs" in details


@pytest.mark.parametrize("selected,status", [("fixture-reserved", "reserved"), ("fixture-blocked", "blocked")])
def test_reservation_status_and_null_quantum_result_remain_literal(selected, status):
    state = admitted_state("reservations", selected)
    state.key("enter")
    text = render_dashboard(state, columns=160, rows=50, now=105.).text()
    assert selected in text and status in text and '"quantum_result": null' in text
    assert not any(role == "chart" for _, _, _, role in render_dashboard(state, columns=160, rows=50).spans)


def test_repainting_does_not_mutate_backend_records_or_advance_the_clock():
    state = admitted_state("links", "A-B")
    original_snapshot, original_mixed = deepcopy(state.snapshot), deepcopy(state.mixed)
    first = render_dashboard(state, columns=120, rows=40, now=105.)
    for _ in range(10):
        assert render_dashboard(state, columns=120, rows=40, now=105.) == first
    assert state.snapshot == original_snapshot and state.mixed == original_mixed
    assert state.snapshot["live"]["tick"] == 1 and state.snapshot["live"]["network_time_s"] == 7
    assert isinstance(render(state), str) and "\x1b" not in render(state)


@pytest.mark.parametrize("ascii_only", [False, True])
def test_chart_preserves_real_sample_columns_and_gap_without_interpolation(ascii_only):
    chart = sample_chart([2., None, 0., 4.], 40, 5, ascii_only=ascii_only)
    assert chart.sample_count == 4 and chart.missing_count == 1
    assert (chart.low, chart.high) == (0., 4.)
    point = "*" if ascii_only else "●"
    assert sum(line.count(point) for line in chart.lines) == 3
    assert not any(len(line) > 1 and line[1] == point for line in chart.lines)
    assert any(len(line) > 1 and line[1] in {"·", "?"} for line in chart.lines)
    assert all(line.index(point) < 4 for line in chart.lines if point in line)
    assert len(chart.lines) <= 5 and all(cells(line) <= 40 for line in chart.lines)


def test_chart_autoscale_and_counts_use_only_last_actual_visible_samples():
    chart = sample_chart([1000., None, 1., 2.], 3, 5)
    assert chart.sample_count == 3 and chart.missing_count == 1
    assert (chart.low, chart.high) == (1., 2.)
    assert sum(line.count("●") for line in chart.lines) == 2
    assert any(line.startswith("·") for line in chart.lines)


def test_zero_is_a_measured_sample_and_constant_range_does_not_crash():
    chart = sample_chart([0., 0., None, 0.], 40, 4)
    assert chart.sample_count == 4 and chart.missing_count == 1
    assert chart.low == chart.high == 0.
    assert sum(line.count("●") for line in chart.lines) == 3
    assert "no input" not in "\n".join(chart.lines)


@pytest.mark.parametrize("values", [[-1e308, None, 1e308, 0.], [5e-324, 1e-323, None, 0.]])
def test_extreme_finite_chart_ranges_do_not_overflow_or_drop_real_samples(values):
    chart = sample_chart(values, 40, 5)
    assert chart.sample_count == 4 and chart.missing_count == 1
    assert math.isfinite(chart.low) and math.isfinite(chart.high)
    assert chart.low == min(value for value in values if value is not None)
    assert chart.high == max(value for value in values if value is not None)
    assert sum(line.count("●") for line in chart.lines) == 3


def test_nonfinite_boolean_and_non_numeric_values_stay_missing():
    values = [False, True, "0.1", float("nan"), float("inf"), None, 10**1000]
    chart = sample_chart(values, 40, 4)
    assert chart.sample_count == len(values) and chart.missing_count == len(values)
    assert chart.low is None and chart.high is None
    assert not any("●" in line for line in chart.lines)
    raw = snapshot()
    raw["live"]["links"][0]["history"] = {"t_s": [0, 1], "fidelity": [None, None], "qber": [None, None], "skr_bps": []}
    text = render_dashboard(admitted_state(raw=raw), columns=120, rows=40, now=105.).text()
    assert "no input" in text.lower()


@pytest.mark.parametrize("width,height", [(1, 1), (2, 2), (8, 4), (40, 6), (240, 12)])
def test_chart_geometry_and_range_are_finite_at_small_and_large_sizes(width, height):
    values = [math.sin(index) for index in range(300)]
    chart = sample_chart(values, width, height)
    assert chart.sample_count == min(len(values), width)
    assert chart.missing_count == 0 and math.isfinite(chart.low) and math.isfinite(chart.high)
    assert len(chart.lines) <= height and all(cells(line) <= width for line in chart.lines)
    assert sum(line.count("●") for line in chart.lines) == chart.sample_count


class FakeCurses:
    A_NORMAL, A_BOLD, A_DIM, A_REVERSE = 0, 1, 2, 4
    COLOR_BLACK, COLOR_RED, COLOR_GREEN, COLOR_YELLOW, COLOR_CYAN, COLOR_WHITE = 0, 1, 2, 3, 6, 7
    COLORS, COLOR_PAIRS = 256, 64

    class error(Exception):
        pass

    def __init__(self, *, colors=True, fail=None):
        self.colors, self.fail, self.calls = colors, fail, []

    def call(self, name, *args):
        self.calls.append((name, args))
        if self.fail == name:
            raise self.error("unavailable terminal feature")

    def start_color(self):
        self.call("start_color")

    def has_colors(self):
        self.call("has_colors")
        return self.colors

    def use_default_colors(self):
        self.call("use_default_colors")

    def init_pair(self, *args):
        self.call("init_pair", *args)

    def color_pair(self, pair):
        self.call("color_pair", pair)
        return pair << 8


@pytest.fixture
def color_environment(monkeypatch):
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setenv("TERM", "xterm-256color")


@pytest.mark.parametrize("setting", ["NO_COLOR", "TERM"])
def test_explicit_monochrome_never_calls_color_apis(monkeypatch, color_environment, setting):
    monkeypatch.setenv(setting, "" if setting == "NO_COLOR" else "dumb")
    curses = FakeCurses()
    attributes = style_attributes(curses)
    assert ROLES <= attributes.keys() and all(type(value) is int for value in attributes.values())
    assert curses.calls == []


def test_term_without_color_has_readable_attributes_without_color_pairs(color_environment):
    curses = FakeCurses(colors=False)
    attributes = style_attributes(curses)
    assert ROLES <= attributes.keys()
    assert all(value < 256 for value in attributes.values())
    assert not any(name in {"init_pair", "color_pair", "use_default_colors"} for name, _ in curses.calls)


@pytest.mark.parametrize("colors", [8, 256])
def test_semantic_colors_are_available_with_basic_and_extended_palettes(color_environment, colors):
    curses = FakeCurses()
    curses.COLORS = colors
    attributes = style_attributes(curses)
    assert ROLES <= attributes.keys()
    assert len({attributes["good"], attributes["warn"], attributes["bad"]}) == 3
    pairs = [args for name, args in curses.calls if name == "init_pair"]
    assert pairs and all(0 <= foreground < colors for _, foreground, _ in pairs)


@pytest.mark.parametrize("failure", ["start_color", "init_pair"])
def test_unavailable_curses_features_preserve_safe_monochrome_attributes(color_environment, failure):
    curses = FakeCurses(fail=failure)
    attributes = style_attributes(curses)
    assert ROLES <= attributes.keys() and all(type(value) is int for value in attributes.values())
    assert not any(value >= 256 for value in attributes.values())


def test_default_background_failure_uses_basic_background(color_environment):
    curses = FakeCurses(fail="use_default_colors")
    attributes = style_attributes(curses)
    assert ROLES <= attributes.keys()
    pairs = [args for name, args in curses.calls if name == "init_pair"]
    assert pairs and all(background == curses.COLOR_BLACK for _, _, background in pairs)


def test_dashboard_import_does_not_load_gui_models_or_open_network():
    root = Path(__file__).resolve().parents[1]
    env = dict(os.environ, PYTHONPATH=str(root / "src"), PYTHONDONTWRITEBYTECODE="1")
    script = """
import socket
import sys
def forbidden(*args, **kwargs):
    raise AssertionError('renderer import must not open a network connection')
socket.socket = forbidden
from quantum_autopilot.service.terminal_dashboard import render_dashboard
from quantum_autopilot.service.terminal_live import TerminalState
assert render_dashboard(TerminalState(), columns=80, rows=24).lines
assert not any(name.startswith(('PySide6', 'PyQt', 'torch', 'quantum_autopilot.console')) for name in sys.modules)
"""
    result = subprocess.run([sys.executable, "-c", script], env=env, capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
