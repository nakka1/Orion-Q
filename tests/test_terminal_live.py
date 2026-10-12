"""Read-only terminal scheduling/presentation and actual authenticated core IO."""
from copy import deepcopy
import io
import os
import subprocess
import sys
import time
from threading import Event

import pytest

from quantum_autopilot.service.client import CoreClient
from quantum_autopilot.service.terminal_brand import brand_spans, color_attributes, header_brand
from quantum_autopilot.service.terminal_live import (
    Publication, SnapshotPump, TerminalState, clip, render, sparkline, watch,
)
from tests.test_portable_core_service import provisioned, serving  # noqa: F401


def frame(revision=1, topology=1, *, include_static=True):
    raw = {"schema": "orion.console.remote.v1",
           "health": {"physical_read_only": True, "automation_allowed": False, "autopilot_version": "fixture-v1"},
           "security": {"subject": "fixture-peer", "source": "certificate"}, "topology": {},
           "live": {"tick": revision, "engine_session_id": "fixture-session", "publication_revision": revision,
                    "topology_version": topology, "network_time_s": revision,
                    "source": {"kind": "simulated", "label": "explicit scheduling fixture"}, "policy": {},
                    "links": [{"link_id": "A-B", "a": "A", "b": "B", "status": "degraded",
                               "fidelity": None, "qber": .02, "skr_bps": None,
                               "history": {"t_s": [0, 1, 4], "fidelity": [.9, None, .95],
                                           "qber": [.01, .03, .02], "skr_bps": [None, None, None]}}],
                    "nodes": [{"node_id": "A", "status": "no_input", "links": ["A-B"]},
                              {"node_id": "B", "status": "degraded", "links": ["A-B"]}],
                    "satellites": {"SAT": {"housekeeping_valid": False,
                        "position_scope": "configured prediction; no received engineering input",
                        "housekeeping": {}, "transmitting_links": [],
                        "bridges": [{"session_id": "pass-1", "status": "waiting", "origin": "simulated",
                                     "metrics": {"qber": None, "conditional_key_estimate_bps": None}}]}},
                    "events": [{"event_id": "event-1", "message": "fixture event"}],
                    "technologies": {"protocol_series": []}}}
    if include_static:
        raw.update(model={"configured": False}, summary={"fixture": True}, evidence_lanes={})
    return raw


def admit(state, raw, *, static=True, mixed=None):
    return state.admit(Publication(raw, mixed, False, False, static, state.epoch, time.monotonic()))


def wait(predicate, timeout=5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = predicate()
        if result:
            return result
        time.sleep(.002)
    raise AssertionError("bounded terminal operation did not finish")


class ControlledClient:
    """Identified scheduler fixture; never a hardware/runtime replacement."""
    def __init__(self):
        self.calls = []
        self.entered, self.release = Event(), Event()
        self.block = False
        self.error = False
        self.mixed_calls = 0

    def console_snapshot(self, *, include_static):
        self.calls.append(include_static)
        self.entered.set()
        if self.block:
            self.release.wait(5)
        if self.error:
            raise RuntimeError("private-error-canary")
        return frame(len(self.calls), include_static=include_static)

    def mixed_status(self):
        self.mixed_calls += 1
        return {"schema": "orion.mixed-controller.state.v1", "physical_action_performed": False,
                "configuration": {"nodes": [{"node_id": "A", "session_id": "fixture-capture-A", "clock_domain": "aligned-clock"}],
                    "links": [{"link_id": "A-B", "a": "A", "b": "B", "classical_channel_id": "classical-AB", "quantum_channel_id": "quantum-AB"}]},
                "nodes": {"A": {"status": "available", "sequence": 12, "time_tag_count": 21,
                    "lost_events": 2, "available": 8,
                    "analysis": {"coincidence_pairs": 3, "event_count": 21, "window_ps": 200}}},
                "reservations": [{"request_id": "demand-A-B", "status": "blocked", "quantum_result": None}]}


def test_bounded_worker_allows_fast_render_and_cancellation_without_late_publication():
    client = ControlledClient()
    client.block = True
    pump, state = SnapshotPump(client), TerminalState()
    assert pump.request(include_static=True, reservations=True)
    assert client.entered.wait(2)
    start = time.monotonic()
    for _ in range(100):
        assert not pump.request(include_static=True, reservations=False)
        assert "Waiting for an authenticated" in render(state)
    assert len(client.calls) == 1 and time.monotonic() - start < .5
    start = time.monotonic()
    assert not pump.close(timeout_s=.02)
    assert time.monotonic() - start < .2
    client.release.set()
    pump._thread.join(2)
    assert not pump._thread.is_alive() and pump.take() is None
    assert client.mixed_calls == 0
    assert not pump.request(include_static=True, reservations=False)


def test_static_cache_revision_ordering_and_failed_poll_are_explicitly_stale():
    state = TerminalState(view="links", selected="A-B")
    assert admit(state, frame()) and not state.force_static
    assert admit(state, frame(2, include_static=False), static=False)
    assert state.snapshot["summary"]["fixture"]
    assert admit(state, frame(3, topology=2, include_static=False), static=False)
    assert state.force_static and "summary" not in state.snapshot
    assert admit(state, frame(4, topology=2)) and not state.force_static
    assert not admit(state, frame(2, topology=2))
    assert state.stale and state.snapshot["live"]["publication_revision"] == 4
    assert state.selections["links"] == "A-B"
    assert not state.admit(Publication(None, None, True, False, False, 0, time.monotonic()))
    assert "STALE" in render(state) and "degraded" in render(state)


@pytest.mark.parametrize("columns,rows", [(1, 1), (20, 8), (80, 24), (170, 44)])
def test_resizes_views_selection_and_details_keep_missing_metrics_honest(columns, rows):
    state = TerminalState(view="links", selected="A-B")
    assert admit(state, frame())
    assert "no input" in render(state, columns=170, rows=24)
    for key in "12345678":
        state.key(key)
        text = render(state, columns=columns, rows=rows)
        assert len(text.splitlines()) <= rows and all(len(line) <= columns for line in text.splitlines())
    state.key("2")
    state.key("enter")
    assert state.selections["links"] == "A-B"
    text = render(state, columns=170, rows=24)
    assert "0 .. 4 s; 3 samples" in text and "no input" in text
    state.key("4")
    state.key("enter")
    text = render(state, columns=170, rows=40)
    assert "HOUSEKEEPING / SCIENCE" in text and "configured prediction" in text
    assert "waiting" in text and "null" in text


def test_pause_only_freezes_presentation_keyboard_and_ansi_input_are_safe():
    state = TerminalState(view="nodes", selected="A")
    assert admit(state, frame())
    state.key("p")
    assert admit(state, frame(2))
    assert state.visible["live"]["tick"] == 1 and state.snapshot["live"]["tick"] == 2
    assert "PAUSED" in render(state)
    state.key("p")
    assert state.visible["live"]["tick"] == 2
    state.key("down")
    assert state.selections["nodes"] == "B"
    state.key("h")
    assert "All requests are GETs" in render(state)
    state.key("h")
    state.snapshot["security"]["subject"] = "peer\x1b[2J"
    assert "\x1b" not in render(state)
    state.key("r")
    assert state.force_static and not state.key("q")


def test_sparklines_preserve_gaps_and_do_not_pad_resample_or_fake_counters():
    assert sparkline([None, None], 40) == "no input"
    text = sparkline([1, None, 3, 5], 40)
    assert len(text) == 4 and text[1] == "·"
    assert len(sparkline([1, 2, 3, 4, 5], 3, unicode=False)) == 3
    assert sparkline([False, float("nan"), float("inf")], 10) == "no input"
    assert clip("水水ABCDE", 5) == "水水A"
    assert clip("e\u0301水A", 3) == "e\u0301水"


def brand_headers():
    return ["OrionQ TERMINAL | OVERVIEW | authenticated backend",
            "Peer: fixture-peer | received age 0.0s | physical locked",
            "1 overview 2 links 3 nodes 4 satellites 5 reservations 6 audit 7 raw 8 mixed"]


@pytest.mark.parametrize("ascii_only", [False, True])
def test_terminal_brand_keeps_existing_header_text_without_terminal_controls(ascii_only):
    header = brand_headers()
    branded = header_brand(header, 180, 24, ascii_only=ascii_only)
    assert len(branded) == len(header) == 3
    assert branded == header
    assert all(clip(line, 180) == line for line in branded)
    assert "\x1b" not in "\n".join(branded)
    assert all(character.isprintable() for line in branded for character in line)
    if ascii_only:
        assert all(line.isascii() for line in branded)


@pytest.mark.parametrize("ascii_only", [False, True])
def test_terminal_brand_does_not_insert_art_into_header_rows_with_spare_space(ascii_only):
    header = [brand_headers()[0], "P" * 119, "N" * 119]
    branded = header_brand(header, 120, 24, ascii_only=ascii_only)
    assert len(branded) == 3 and branded == header
    assert all(clip(line, 120) == line for line in branded)
    if ascii_only:
        assert all(line.isascii() for line in branded)


@pytest.mark.parametrize("columns,rows", [(1, 1), (5, 8), (40, 24), (180, 7)])
def test_terminal_brand_omits_decoration_when_there_is_no_room(columns, rows):
    header = brand_headers()
    assert header_brand(header, columns, rows) == header


def test_terminal_brand_preserves_wide_header_text_and_cell_width():
    header = ["水" * 45 + "e\u0301", "Peer: fixture-peer", "1 overview 2 links"]
    branded = header_brand(header, 100, 24)
    assert len(branded) == 3 and branded == header
    assert all(clip(line, 100) == line for line in branded)


@pytest.mark.parametrize("ascii_only", [False, True])
def test_render_brand_keeps_header_status_identity_navigation_and_body_positions(ascii_only):
    state = TerminalState(view="nodes", selected="B")
    assert admit(state, frame())
    plain = render(state, columns=180, rows=7, now=0, ascii_only=ascii_only).splitlines()
    branded = render(state, columns=180, rows=24, now=0, ascii_only=ascii_only).splitlines()
    assert branded[0].startswith(plain[0]) and "| NODES |" in branded[0]
    assert branded[1].startswith(plain[1]) and "fixture-peer" in branded[1]
    assert branded[2].startswith(plain[2]) and "8 mixed" in branded[2]
    assert branded[3:] == plain[3:]
    assert branded[3].startswith("  A ") and branded[4].startswith("> B ")
    state.key("p")
    assert not state.admit(Publication(None, None, True, False, False, 0, time.monotonic()))
    lines = render(state, columns=180, rows=24, now=0, ascii_only=ascii_only).splitlines()
    assert lines[0].startswith("OrionQ TERMINAL | NODES | PAUSED / cached presentation | STALE / connection unavailable")
    assert "fixture-peer" in lines[1] and "8 mixed" in lines[2]
    assert state.selections["nodes"] == "B"
    assert "\x1b" not in "\n".join(lines)


def test_render_brand_resize_keeps_body_selection_and_header_row():
    state = TerminalState(view="nodes", selected="B")
    assert admit(state, frame())
    original = render(state, columns=180, rows=24, now=0)
    for columns, rows in ((80, 24), (40, 10), (1, 1), (180, 24)):
        text = render(state, columns=columns, rows=rows, now=0)
        assert len(text.splitlines()) <= rows
        assert all(clip(line, columns) == line for line in text.splitlines())
        assert state.selections["nodes"] == "B"
        if columns >= 80:
            assert "| NODES |" in text.splitlines()[0]
            assert "> B" in text
    assert text == original


@pytest.mark.parametrize("ascii_only", [False, True])
@pytest.mark.parametrize("compact", [False, True])
def test_brand_color_spans_describe_existing_text_without_escapes_or_layout_changes(ascii_only, compact):
    original = brand_headers()
    if compact:
        original[1:] = ["P" * 119, "N" * 119]
    columns = 120 if compact else 180
    header = header_brand(original, columns, 24, ascii_only=ascii_only)
    before = list(header)
    spans = brand_spans(header)
    assert header == before
    assert (0, 0, "Orion", "wordmark") in spans and (0, 5, "Q", "accent") in spans
    assert spans == [(0, 0, "Orion", "wordmark"), (0, 5, "Q", "accent")]
    for row, column, value, role in spans:
        assert 0 <= row < 3 and 0 <= column < columns
        assert role in {"wordmark", "accent"} and value
        prefix = clip(header[row], column)
        assert header[row][len(prefix):len(prefix) + len(value)] == value
        assert clip(prefix + value, columns) == prefix + value
        assert all(character.isprintable() for character in value) and "\x1b" not in value


def test_brand_color_spans_do_not_reinterpret_wide_header_text_as_art():
    original = ["水" * 45 + "e\u0301", "Peer: fixture-peer", "1 overview 2 links"]
    header = header_brand(original, 100, 24)
    spans = brand_spans(header)
    assert header == original and spans == []
    assert all(clip(line, 100) == line for line in header)


class FakeBrandCurses:
    A_BOLD = 1
    COLOR_RED = 2
    COLOR_BLACK = 0

    class error(Exception):
        pass

    def __init__(self, *, colors=256, has_colors=True, failure=None):
        self.COLORS, self.available, self.failure = colors, has_colors, failure
        self.calls, self.pairs = [], []

    def call(self, name):
        self.calls.append(name)
        if self.failure == name:
            raise self.error("fixture terminal does not support this operation")

    def start_color(self):
        self.call("start_color")

    def has_colors(self):
        self.call("has_colors")
        return self.available

    def use_default_colors(self):
        self.call("use_default_colors")

    def init_pair(self, pair, foreground, background):
        self.call("init_pair")
        self.pairs.append((pair, foreground, background))

    def color_pair(self, pair):
        self.call("color_pair")
        return pair << 8


@pytest.mark.parametrize("no_color,term", [("", "xterm-256color"), ("1", "xterm-256color"), (None, "dumb")])
def test_terminal_brand_no_color_and_dumb_term_do_not_initialize_colors(monkeypatch, no_color, term):
    monkeypatch.delenv("NO_COLOR", raising=False)
    if no_color is not None:
        monkeypatch.setenv("NO_COLOR", no_color)
    monkeypatch.setenv("TERM", term)
    curses = FakeBrandCurses()
    assert color_attributes(curses) == {"wordmark": curses.A_BOLD, "accent": curses.A_BOLD}
    assert not curses.calls


@pytest.mark.parametrize("colors,foreground", [(256, 173), (8, FakeBrandCurses.COLOR_RED)])
def test_terminal_brand_uses_available_palette_without_redefining_colors(monkeypatch, colors, foreground):
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setenv("TERM", "xterm-256color")
    curses = FakeBrandCurses(colors=colors)
    attributes = color_attributes(curses)
    assert attributes["wordmark"] == curses.A_BOLD
    assert attributes["accent"] & curses.A_BOLD and attributes["accent"] != curses.A_BOLD
    assert curses.pairs == [(1, foreground, -1)]


@pytest.mark.parametrize("available,failure", [(False, None), (True, "start_color"), (True, "init_pair")])
def test_terminal_brand_unsupported_colors_keep_plain_bold_attributes(monkeypatch, available, failure):
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setenv("TERM", "xterm-256color")
    curses = FakeBrandCurses(has_colors=available, failure=failure)
    assert color_attributes(curses) == {"wordmark": curses.A_BOLD, "accent": curses.A_BOLD}
    assert "color_pair" not in curses.calls


def test_terminal_brand_uses_basic_background_when_default_colors_are_unsupported(monkeypatch):
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setenv("TERM", "xterm-256color")
    curses = FakeBrandCurses(colors=8, failure="use_default_colors")
    attributes = color_attributes(curses)
    assert curses.pairs == [(1, curses.COLOR_RED, curses.COLOR_BLACK)]
    assert attributes["accent"] != curses.A_BOLD and attributes["wordmark"] == curses.A_BOLD


def test_noninteractive_watch_is_finite_and_has_only_one_pending_read():
    client, stream = ControlledClient(), io.StringIO()
    assert watch(client, frames=3, interval_s=.1, stream=stream, view="reservations") == 0
    assert client.calls == [True, False, False]
    text = stream.getvalue()
    assert "fixture-peer" in text and "demand-A-B" in text and "blocked" in text
    with pytest.raises(ValueError):
        watch(client, frames=None, stream=io.StringIO())
    client.error = True
    stream = io.StringIO()
    assert watch(client, frames=1, interval_s=.1, stream=stream) == 3
    assert "STALE" in stream.getvalue() and "private-error-canary" not in stream.getvalue()


def test_real_tls_watch_presents_backend_without_advancing_scientific_time(provisioned):  # noqa: F811
    config, runtime, *_rest, operator_context = provisioned
    with serving(config, runtime) as server:
        client = CoreClient(f"https://localhost:{server.server_port}", operator_context, timeout_s=5)
        client.network_command("configure_network_input", {"kind": "simulated", "seed": 17, "scenario": "quiet"})
        before = client.network_step(metadata_only=True)
        stream = io.StringIO()
        assert watch(client, frames=2, interval_s=.1, view="links", stream=stream) == 0
        text = stream.getvalue()
        assert "orion-core" in text and "authenticated backend" in text and "QBER" in text
        assert client.network_state(metadata_only=True)["tick"] == before["tick"]
        stream = io.StringIO()
        assert watch(client, frames=1, interval_s=.1, view="mixed", stream=stream) == 0
        assert "identity not published by mixed status" in stream.getvalue()
        assert client.network_state(metadata_only=True)["tick"] == before["tick"]
        assert runtime.audit.verify()["valid"]


def test_mixed_view_reads_only_controller_status_and_preserves_independent_freshness():
    client, stream = ControlledClient(), io.StringIO()
    assert watch(client, frames=2, interval_s=.1, stream=stream, view="mixed", selected="A") == 0
    assert client.calls == [] and client.mixed_calls == 2
    text = stream.getvalue()
    assert "seq:12 tags:21 lost:2 resources:8" in text
    assert "identity not published by mixed status" in text and "no inferred F/QBER" in text
    state = TerminalState(view="mixed", selected="A")
    timestamp = time.monotonic()
    publication = Publication(None, client.mixed_status(), False, False, False, 0, timestamp, True)
    assert state.admit(publication) and not state.stale and state.received_at == timestamp
    state.key("enter")
    detail = render(state, rows=60)
    assert "coincidence_pairs" in detail and "aligned-clock" in detail
    assert "classical-AB" in detail and "quantum-AB" in detail
    state.key("2")
    assert state.stale and state.received_at is None
    assert admit(state, frame()) and not state.stale and not state.force_static
    state.key("8")
    assert not state.stale and state.received_at == timestamp and state.selections["mixed"] == "A"
    assert not state.admit(Publication(None, None, True, True, False, 0, timestamp, True))
    assert state.stale and "STALE" in render(state)
    state.key("2")
    assert not state.stale and "degraded" in render(state)


@pytest.mark.parametrize("change", [{"nodes": ["invalid"]}, {"reservations": ["invalid"]},
                                   {"physical_action_performed": True}])
def test_invalid_mixed_publications_remain_stale_without_inferred_results(change):
    raw = ControlledClient().mixed_status()
    raw.update(change)
    state = TerminalState(view="mixed")
    assert not state.admit(Publication(None, raw, False, False, False, 0, time.monotonic(), True))
    assert state.stale and state.mixed is None
    assert "no inferred F/QBER" in render(state)


@pytest.mark.parametrize("change", [
    {"schema": "wrong"}, {"health": {"physical_read_only": False}},
    {"security": {"subject": "os:forged", "source": "os"}},
    {"live": {"links": ["invalid"], "nodes": []}},
])
def test_invalid_or_physical_snapshot_has_no_local_fallback(change):
    raw = deepcopy(frame())
    raw.update(change)
    state = TerminalState()
    assert not admit(state, raw) and state.snapshot is None and state.stale


@pytest.mark.parametrize("rows", [8, 12, 24])
def test_help_remains_visible_after_selecting_a_record_beyond_the_viewport(rows):
    raw = frame()
    raw["live"]["nodes"] = [{"node_id": f"N{index:02d}"} for index in range(40)]
    state = TerminalState(view="nodes", selected="N39")
    assert admit(state, raw)
    render(state, rows=rows)
    state.key("h")
    text = render(state, rows=rows)
    assert "1 Overview" in text and "arrows: select / scroll" in text
    assert state.selections["nodes"] == "N39"
    state.key("h")
    assert "> N39" in render(state, rows=rows)


@pytest.mark.parametrize("view,details", [("raw", False), ("links", True)])
def test_scroll_reverses_immediately_after_repeated_down_at_the_bottom(view, details):
    state = TerminalState(view=view, selected="A-B" if details else None)
    assert admit(state, frame())
    if details:
        state.key("enter")
    render(state, columns=120, rows=12, now=0)
    for _ in range(250):
        state.key("down")
        bottom = render(state, columns=120, rows=12, now=0)
    state.key("up")
    assert render(state, columns=120, rows=12, now=0) != bottom


@pytest.mark.parametrize("columns,rows", [(1, 1), (2, 2), (5, 4), (12, 6)])
def test_navigation_at_minimum_terminal_sizes_preserves_selection_on_restore(columns, rows):
    raw = frame()
    raw["live"]["nodes"] = [{"node_id": f"N{index:02d}"} for index in range(40)]
    state = TerminalState(view="nodes", selected="N25")
    assert admit(state, raw)
    for key in ("down", "up"):
        state.key(key)
        text = render(state, columns=columns, rows=rows)
        assert len(text.splitlines()) <= rows
        assert all(len(line) <= columns for line in text.splitlines())
    assert state.selections["nodes"] == "N25"
    assert "> N25" in render(state, columns=100, rows=24)


def test_mixed_view_does_not_attribute_a_cached_console_identity_to_its_readback():
    state = TerminalState()
    assert admit(state, frame())
    state.key("8")
    mixed = ControlledClient().mixed_status()
    assert state.admit(Publication(None, mixed, False, False, False, 0, time.monotonic(), True))
    text = render(state)
    assert "identity not published by mixed status" in text
    assert "fixture-peer" not in text
    state.key("1")
    assert "fixture-peer" in render(state)


@pytest.mark.parametrize("kind", ["protocol_series", "commands", "history", "node_links", "link_endpoint"])
def test_malformed_nested_console_records_retain_the_last_valid_frame_as_stale(kind):
    view = "audit" if kind == "commands" else "nodes" if kind in {"node_links", "link_endpoint"} else "overview"
    state = TerminalState(view=view, selected="A" if view == "nodes" else None)
    assert admit(state, frame())
    last_valid = state.snapshot
    malformed = frame(2)
    if kind == "protocol_series":
        malformed["live"]["technologies"]["protocol_series"] = [None]
    elif kind == "commands":
        malformed["commands"] = ["invalid"]
    elif kind == "history":
        malformed["live"]["links"][0]["history"] = None
    elif kind == "node_links":
        malformed["live"]["nodes"][0]["links"] = None
    else:
        malformed["live"]["links"][0]["a"] = []
    assert not admit(state, malformed)
    assert state.stale and state.snapshot is last_valid
    if kind == "link_endpoint":
        state.key("enter")
    text = render(state, rows=60)
    assert "STALE" in text and "fixture-peer" in text
    assert state.snapshot["live"]["publication_revision"] == 1


@pytest.mark.parametrize("configuration", [
    {"nodes": [None]}, {"links": [None]}, "invalid", {"links": [{"a": [], "b": "A"}]},
])
def test_malformed_mixed_configuration_retains_valid_details_as_stale(configuration):
    state = TerminalState(view="mixed", selected="A")
    mixed = ControlledClient().mixed_status()
    timestamp = time.monotonic()
    assert state.admit(Publication(None, mixed, False, False, False, 0, timestamp, True))
    malformed = deepcopy(mixed)
    malformed["configuration"] = configuration
    assert not state.admit(Publication(None, malformed, False, False, False, 0, timestamp + 1, True))
    assert state.stale and state.mixed is mixed and state.received_at == timestamp
    state.key("enter")
    text = render(state, rows=60)
    assert "STALE" in text and "aligned-clock" in text and "coincidence_pairs" in text


@pytest.mark.parametrize("view,selected", [("nodes", "A"), ("satellites", "SAT"), ("links", "A-SAT")])
def test_component_details_show_backend_protocol_series_without_renaming_node_ids(view, selected):
    from quantum_autopilot.network_engine.protocol_history import ProtocolHistory

    history = ProtocolHistory()
    protocol = {"clock_s": 1., "sessions": {
        "fixture-protocol": {"config": {"technologies": ["tf_qkd"], "path": ["A", "SAT"]},
            "metrics": {"last_observed_at_s": 1., "phase_reference_residual_rad": .17},
            "status": "operational", "source": {"origin": "simulated"}},
        "unrelated-protocol": {"config": {"technologies": ["entanglement_swapping"], "path": ["X", "Y"]},
            "metrics": {"last_observed_at_s": 1., "delivered_pair_rate_hz": 4.},
            "status": "operational", "source": {"origin": "simulated"}},
    }}
    series = history.update(protocol)
    target = next(row for row in series if row["metric"] == "phase_reference_residual_rad")
    assert target["node_ids"] == ["A", "SAT"] and "nodes" not in target and "link_ids" not in target
    raw = frame()
    raw["live"]["technologies"]["protocol_series"] = series
    raw["live"]["links"][0].update(link_id="A-SAT", a="A", b="SAT",
        provenance={"technology": {"session_id": "fixture-protocol", "technologies": ["tf_qkd"]}})
    raw["live"]["nodes"][0]["links"] = ["A-SAT"]
    state = TerminalState(view=view, selected=selected)
    assert admit(state, raw)
    state.key("enter")
    text = render(state, columns=170, rows=120)
    assert "tf_qkd phase_reference_residual_rad [rad]" in text
    assert "delivered_pair_rate_hz" not in text
    if view == "links":
        without_association = deepcopy(raw)
        without_association["live"]["links"][0]["provenance"].pop("technology")
        assert admit(state, without_association)
        assert "phase_reference_residual_rad" not in render(state, columns=170, rows=120)


def test_reservations_keep_mixed_failure_visible_after_switching_from_mixed_view():
    state = TerminalState(view="reservations")
    mixed = ControlledClient().mixed_status()
    timestamp = time.monotonic()
    assert state.admit(Publication(frame(), mixed, False, False, True, 0, timestamp))
    state.key("8")
    assert not state.admit(Publication(None, None, True, True, False, 0, timestamp + 1, True))
    state.key("5")
    assert "STALE" in render(state, now=timestamp + 2)
    assert state.records()[0][0] == "demand-A-B"
    assert state.admit(Publication(frame(2), mixed, False, False, False, 0, timestamp + 3))
    assert "STALE" not in render(state, now=timestamp + 3)
    assert "demand-A-B" in render(state, now=timestamp + 3)


@pytest.mark.skipif(os.name != "posix", reason="curses PTY interaction is qualified on the Linux target")
def test_linux_pty_keyboard_resize_and_exit_restore_terminal():
    """Actual curses/PTY lifecycle using clearly identified scheduling data."""
    import fcntl
    import select
    import signal
    import struct
    import termios

    raw = repr(frame())
    code = f"""from copy import deepcopy
from quantum_autopilot.service.terminal_live import watch
raw = {raw}
class Client:
    def console_snapshot(self, *, include_static):
        result = deepcopy(raw)
        if not include_static:
            for key in ('model', 'summary', 'evidence_lanes'): result.pop(key, None)
        return result
    def mixed_status(self):
        return {{'schema':'orion.mixed-controller.state.v1','physical_action_performed':False,'nodes':{{}},'reservations':[]}}
raise SystemExit(watch(Client(), interval_s=.1, view='links'))
"""
    master, slave = os.openpty()
    initial = termios.tcgetattr(slave)
    fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 24, 110, 0, 0))
    process = subprocess.Popen([sys.executable, "-c", code], stdin=slave, stdout=slave, stderr=slave,
                               env={**os.environ, "TERM": "xterm-256color"}, close_fds=True)
    received = bytearray()

    def read_until(marker):
        def read():
            if select.select([master], [], [], .05)[0]:
                received.extend(os.read(master, 65536))
                if len(received) > 131072:
                    del received[:-65536]
            return marker in received
        wait(read, timeout=12)

    try:
        # The colored Q splits the wordmark with SGR attributes in the wire
        # stream. The unchanged view/status suffix remains a contiguous run.
        read_until(b" TERMINAL | LINKS |")
        read_until(b"fixture-peer")
        os.write(master, b"4\n")
        read_until(b"HOUSEKEEPING / SCIENCE")
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 40, 140, 0, 0))
        os.kill(process.pid, signal.SIGWINCH)
        os.write(master, b"p")
        read_until(b"PAUSED")
        os.write(master, b"q")
        assert process.wait(timeout=3) == 0
        assert termios.tcgetattr(slave) == initial
        assert b"no input" in received or b"null" in received
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)
        os.close(master)
        os.close(slave)
