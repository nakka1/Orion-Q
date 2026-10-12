"""Original branding stays complete, optional and separate from backend data."""
from copy import deepcopy
from dataclasses import FrozenInstanceError
import hashlib
import io
from types import MappingProxyType, SimpleNamespace

import pytest

from quantum_autopilot.service import terminal_dashboard, terminal_live
from quantum_autopilot.service.terminal_brand import (
    brand_art_spans, brand_spans, choose_art, color_attributes, header_brand, requires_ascii,
)
from quantum_autopilot.service.terminal_brand_art import BRAND_ART, BrandArt
from quantum_autopilot.service.terminal_dashboard import DashboardFrame, render_dashboard, style_attributes
from quantum_autopilot.service.terminal_live import Publication, TerminalState, VIEWS, render, render_frame
from tests.test_terminal_dashboard import admitted_state, assert_frame_geometry, snapshot
from tests.test_terminal_live import FakeBrandCurses


# Golden hashes cover every retained cell, including shared bounding-box padding.
# They bind the supplied/recovered originals; tests do not redraw their shapes.
ORIGINALS = {
    "Q30": (27, 14, 0, "5180234ba2b215080db57f15a987717d095f89cb98bc131f556607d1ffb80da1"),
    "Q50": (46, 23, 0, "ffa858a824d695c36cc7c0e68d499de3f8793caef53fcc6395995c01a1ee7423"),
    "Q60": (56, 27, 0, "ce630a9c86ad15b9affb5ebc26ca998a914c8a706fbe4fded7f345078e7bb9f5"),
    "Q90": (83, 42, 0, "ed2e4a072809494e56c959a9c43f573c702f60fd7ed33d2f1d7a7bf59baf527d"),
    "Q110": (103, 52, 0, "6e9892698b8652e22c3a1657ce4981f78ee111692357fdef7e45d67707365b1b"),
    "Q160": (149, 74, 0, "e37ed70ac2282192a8ef3c7508209c4a65f27ff04258795d17cf863bb22b3d9e"),
    "WORDMARK110": (105, 15, 75, "6aad7f5f27c573441aa6dd4ca8bb28f5414907094c3bedb8f8f9ff47d85ae798"),
    "WORDMARK160": (156, 21, 109, "0a13e8d198fb13ff01a214e65b7e81e6d1270f8ef296ce2c39c190b4c785d44d"),
}
NARROWER = {
    "Q30": None, "Q50": "Q30", "Q60": "Q50", "Q90": "Q60",
    "Q110": "Q90", "Q160": "Q110", "WORDMARK110": "Q30", "WORDMARK160": "WORDMARK110",
}


def displayed_art(key, ascii_only=False):
    art = BRAND_ART[key]
    return BrandArt(art.key, tuple(line.replace("█", "#") for line in art.lines),
                    art.q_start_col, art.provenance) if ascii_only else art


def presentation(layout, state, columns, rows, ascii_only=False):
    if layout == "dashboard":
        return render_dashboard(state, columns=columns, rows=rows, now=105., ascii_only=ascii_only)
    return DashboardFrame(*render_frame(state, columns=columns, rows=rows, now=105., ascii_only=ascii_only))


def locate_art(frame, art, columns):
    """Find the whole original rectangle, rather than an identifying fragment."""
    first = art.lines[0]
    needle = first.strip()
    leading = len(first) - len(first.lstrip())
    for top, line in enumerate(frame.lines[:max(0, len(frame.lines) - art.height + 1)]):
        position = line.find(needle)
        while position >= 0:
            left = position - leading
            if (0 <= left and left + art.width <= columns and
                    all(frame.lines[top + row].ljust(columns)[left:left + art.width] == value
                        for row, value in enumerate(art.lines))):
                return top, left
            position = line.find(needle, position + 1)
    return None


def body_brand_spans(frame):
    # Dashboard card values share the coral accent. Artwork uses only its
    # canonical ink glyphs; color alone must never classify a card as a logo.
    return tuple(span for span in frame.spans if span[0] >= 3 and span[3] in {"wordmark", "accent"}
                 and span[2] and all(char in "=█#" for char in span[2]))


@pytest.mark.parametrize("key", ORIGINALS)
def test_whole_original_artwork_cells_and_metadata_are_bound(key):
    art = BRAND_ART[key]
    width, height, q_start, digest = ORIGINALS[key]
    assert (art.width, art.height, art.q_start_col) == (width, height, q_start)
    assert isinstance(art.lines, tuple) and len(art.lines) == height
    assert {len(line) for line in art.lines} == {width}
    assert hashlib.sha256("\n".join(art.lines).encode("utf-8")).hexdigest() == digest
    assert art.provenance and "Publicidade" not in "\n".join(art.lines)
    assert all(char in " =█" for line in art.lines for char in line)
    with pytest.raises(FrozenInstanceError):
        art.lines = ()


def test_registry_is_immutable_and_contains_only_the_eight_original_variants():
    assert isinstance(BRAND_ART, MappingProxyType)
    assert set(BRAND_ART) == set(ORIGINALS)
    with pytest.raises(TypeError):
        BRAND_ART["generated"] = BrandArt("generated", ("=",))


@pytest.mark.parametrize("key", ORIGINALS)
@pytest.mark.parametrize("ascii_only", [False, True])
def test_each_original_fits_exactly_without_scaling_or_crop(key, ascii_only):
    original = BRAND_ART[key]
    chosen = choose_art(original.width, original.height, ascii_only=ascii_only)
    assert chosen == displayed_art(key, ascii_only)


@pytest.mark.parametrize("key", ORIGINALS)
@pytest.mark.parametrize("axis", ["width", "height"])
def test_one_missing_cell_selects_a_smaller_complete_art_in_both_axes(key, axis):
    art = BRAND_ART[key]
    chosen = choose_art(art.width - (axis == "width"), art.height - (axis == "height"))
    expected = NARROWER[key]
    assert (chosen.key if chosen is not None else None) == expected
    if chosen is not None:
        assert chosen == BRAND_ART[expected]


@pytest.mark.parametrize("columns,rows", [(0, 100), (200, 0), (-1, 100), (200, -1), (26, 100), (240, 13)])
def test_no_art_is_returned_when_no_complete_original_fits(columns, rows):
    assert choose_art(columns, rows) is None


def test_largest_area_wins_over_nominal_name_and_wordmark_preference():
    assert choose_art(110, 30).key == "WORDMARK110"  # 1575 cells beats Q60's 1512.
    assert choose_art(160, 45).key == "Q90"  # 3486 cells beats WORDMARK160's 3276.
    assert choose_art(240, 100).key == "Q160"


@pytest.mark.parametrize("key", ORIGINALS)
def test_ascii_fallback_preserves_all_original_cell_positions_and_registry(key):
    original = BRAND_ART[key]
    before = dict(BRAND_ART)
    art = choose_art(original.width, original.height, ascii_only=True)
    assert art.lines == tuple(line.replace("█", "#") for line in original.lines)
    assert (art.width, art.height, art.q_start_col, art.provenance) == (
        original.width, original.height, original.q_start_col, original.provenance)
    assert all(line.isascii() for line in art.lines)
    assert dict(BRAND_ART) == before
    assert choose_art(original.width, original.height).lines == original.lines


@pytest.mark.parametrize("key", ORIGINALS)
@pytest.mark.parametrize("ascii_only", [False, True])
def test_color_spans_cover_each_original_ink_cell_once_without_escape_or_spaces(key, ascii_only):
    art = displayed_art(key, ascii_only)
    before = art.lines
    spans = brand_art_spans(art)
    assert isinstance(spans, tuple) and spans
    covered = {}
    for row, column, text, role in spans:
        assert text and " " not in text and all(char.isprintable() for char in text)
        assert art.lines[row][column:column + len(text)] == text
        assert 0 <= row < art.height and 0 <= column < column + len(text) <= art.width
        for offset, char in enumerate(text):
            coordinate = row, column + offset
            assert coordinate not in covered
            covered[coordinate] = char
            expected = "wordmark" if key.startswith("WORDMARK") and column + offset < art.q_start_col else "accent"
            assert role == expected
    assert covered == {(row, column): char for row, line in enumerate(art.lines)
                       for column, char in enumerate(line) if char != " "}
    assert art.lines == before


def test_wordmark_span_splits_contiguous_ink_at_the_q_boundary():
    art = BrandArt("WORDMARK-fixture", ("██== =",), q_start_col=2)
    assert brand_art_spans(art) == ((0, 0, "██", "wordmark"), (0, 2, "==", "accent"), (0, 5, "=", "accent"))


@pytest.mark.parametrize("encoding,expected", [
    ("ascii", True), ("cp437", True), ("latin-1", True), ("unknown-codec", True),
    ("utf-8", False), ("utf-16", False), (None, False),
])
def test_output_encoding_checks_both_blocks_and_braille_before_selecting_ascii(encoding, expected):
    assert requires_ascii(encoding) is expected


@pytest.mark.parametrize("ascii_only", [False, True])
def test_header_has_only_existing_wordmark_spans_with_no_inserted_microglyph(ascii_only):
    header = ["OrionQ TERMINAL | PAUSED | STALE", "Peer: 水e\u0301 fixture", "1 overview 8 mixed"]
    original = list(header)
    for columns, rows in ((1, 1), (65, 18), (240, 100)):
        branded = header_brand(header, columns, rows, ascii_only=ascii_only)
        assert branded == original and len(branded) == 3
        assert brand_spans(branded) == [(0, 0, "Orion", "wordmark"), (0, 5, "Q", "accent")]
    assert header == original


# These expected variants follow actual space remaining AFTER all instructions.
PANELS = [
    ("plain", False, 120, 40, "WORDMARK110"), ("plain", False, 200, 60, "Q110"),
    ("plain", False, 200, 90, "Q160"), ("dashboard", False, 120, 40, "WORDMARK110"),
    ("dashboard", False, 200, 60, "WORDMARK160"), ("dashboard", False, 200, 90, "Q110"),
    ("plain", True, 120, 40, "WORDMARK110"), ("plain", True, 200, 60, "Q90"),
    ("plain", True, 200, 90, "Q160"), ("dashboard", True, 120, 40, "WORDMARK110"),
    ("dashboard", True, 200, 60, "Q90"), ("dashboard", True, 200, 90, "Q160"),
]


@pytest.mark.parametrize("layout,help_open,columns,rows,key", PANELS)
@pytest.mark.parametrize("ascii_only", [False, True])
def test_help_and_waiting_show_the_complete_largest_art_after_operational_text(
        layout, help_open, columns, rows, key, ascii_only):
    state = admitted_state() if help_open else TerminalState(view="links")
    state.help = help_open
    before = deepcopy(state.snapshot)
    output = presentation(layout, state, columns, rows, ascii_only)
    assert_frame_geometry(output, columns, rows)
    art = displayed_art(key, ascii_only)
    location = locate_art(output, art, columns)
    assert location is not None, (layout, help_open, key)
    top, left = location
    assert top >= 4 and top + art.height <= len(output.lines) - 2
    assert left + art.width < columns
    text_before_art = "\n".join(output.lines[:top])
    if help_open:
        assert "All requests are GETs." in text_before_art
        assert "TLS failures retain explicitly stale data." in text_before_art
        assert "backend continues" in text_before_art
    else:
        assert "Waiting for an authenticated backend snapshot." in text_before_art
        assert "No local fallback or generated values." in text_before_art
        assert "not authenticated" in output.lines[1] and "STALE" in output.lines[0]
        assert not any(role == "chart" for _, _, _, role in output.spans)
    body_spans = body_brand_spans(output)
    assert body_spans
    assert all(top <= row < top + art.height and left <= col < left + art.width
               for row, col, _, _ in body_spans)
    assert state.snapshot == before
    if ascii_only:
        assert output.text().isascii()


@pytest.mark.parametrize("layout", ["plain", "dashboard"])
@pytest.mark.parametrize("help_open", [False, True])
def test_small_viewport_omits_art_and_restore_keeps_identity_and_selection(layout, help_open):
    state = admitted_state("nodes", "B") if help_open else TerminalState(view="nodes", selected="B")
    state.help = help_open
    for columns, rows in ((1, 1), (28, 8), (65, 18), (240, 100), (65, 18)):
        output = presentation(layout, state, columns, rows)
        assert_frame_geometry(output, columns, rows)
        if rows <= 18:
            assert not body_brand_spans(output)
        else:
            assert body_brand_spans(output)
        assert state.selections["nodes"] == "B"
        if help_open and columns >= 65:
            assert "fixture-peer" in output.lines[1]


@pytest.mark.parametrize("layout", ["plain", "dashboard"])
@pytest.mark.parametrize("view", VIEWS)
def test_normal_data_views_do_not_select_art_or_displace_backend_records(monkeypatch, layout, view):
    def forbidden(*_args, **_kwargs):
        raise AssertionError("normal data presentation must not request decorative artwork")
    monkeypatch.setattr(terminal_live, "choose_art", forbidden)
    monkeypatch.setattr(terminal_dashboard, "choose_art", forbidden)
    state = admitted_state(view)
    before_snapshot, before_mixed = deepcopy(state.snapshot), deepcopy(state.mixed)
    output = presentation(layout, state, 240, 100)
    assert_frame_geometry(output, 240, 100)
    assert not body_brand_spans(output)
    assert state.snapshot == before_snapshot and state.mixed == before_mixed
    if view == "mixed":
        assert "identity not published by mixed status" in output.lines[1]
        assert "fixture-peer" not in output.text()
        assert "no inferred F/QBER" in output.text()
    else:
        assert "fixture-peer" in output.lines[1]


@pytest.mark.parametrize("layout", ["plain", "dashboard"])
def test_loading_art_disappears_after_actual_publication_and_help_closes(layout):
    state = TerminalState(view="links", selected="A-B")
    waiting = presentation(layout, state, 200, 90)
    assert body_brand_spans(waiting)
    assert state.admit(Publication(snapshot(), None, False, False, True, 0, 100.))
    current = presentation(layout, state, 200, 90)
    assert not body_brand_spans(current)
    assert "fixture-peer" in current.text() and "A-B" in current.text()
    original = deepcopy(state.snapshot)
    state.key("h")
    state.scroll = 1000
    help_frame = presentation(layout, state, 200, 90)
    assert body_brand_spans(help_frame) and "All requests are GETs." in help_frame.text()
    state.key("h")
    restored = presentation(layout, state, 200, 90)
    assert restored == current
    assert state.snapshot == original and state.selections["links"] == "A-B"


@pytest.mark.parametrize("layout", ["plain", "dashboard"])
def test_art_never_changes_paused_stale_state_or_backend_missing_metrics(layout):
    state = admitted_state("links", "A-B")
    state.key("p")
    assert state.admit(Publication(snapshot(2), None, False, False, False, 0, 101.))
    assert not state.admit(Publication(None, None, True, False, False, 0, 102.))
    state.help = True
    output = presentation(layout, state, 200, 90)
    assert body_brand_spans(output)
    assert "PAUSED" in output.lines[0] and "STALE" in output.lines[0]
    assert "fixture-peer" in output.lines[1] and "8 mixed" in output.lines[2]
    assert state.visible["live"]["tick"] == 1 and state.snapshot["live"]["tick"] == 2
    state.help = False
    current = presentation(layout, state, 200, 90)
    assert not body_brand_spans(current)
    assert "no input" in current.text() and state.visible["live"]["links"][0]["fidelity"] is None


@pytest.mark.parametrize("no_color,term", [("", "xterm-256color"), ("1", "xterm-256color"), (None, "dumb")])
def test_no_color_preserves_whole_art_and_roles_without_color_initialization(monkeypatch, no_color, term):
    state = admitted_state()
    state.help = True
    before = presentation("dashboard", state, 200, 90)
    monkeypatch.delenv("NO_COLOR", raising=False)
    if no_color is not None:
        monkeypatch.setenv("NO_COLOR", no_color)
    monkeypatch.setenv("TERM", term)
    curses = FakeBrandCurses()
    assert color_attributes(curses) == {"wordmark": curses.A_BOLD, "accent": curses.A_BOLD}
    assert style_attributes(curses)["accent"] == curses.A_BOLD
    assert not curses.calls
    assert presentation("dashboard", state, 200, 90) == before


def test_plain_render_frame_exports_the_same_text_and_valid_whole_body_color_spans():
    state = admitted_state()
    state.help = True
    lines, spans = render_frame(state, columns=200, rows=90, now=105.)
    output = DashboardFrame(lines, spans)
    assert_frame_geometry(output, 200, 90)
    assert render(state, columns=200, rows=90, now=105.) == output.text()
    assert locate_art(output, BRAND_ART["Q160"], 200) is not None
    assert body_brand_spans(output) and "\x1b" not in output.text()


def unicode_snapshot():
    raw = snapshot()
    raw["live"]["source"]["label"] = "source · · 水 e\u0301"
    raw["security"]["subject"] = "peer-水e\u0301"
    return raw


@pytest.mark.parametrize("layout", ["plain", "dashboard"])
@pytest.mark.parametrize("help_open", [False, True])
def test_non_ascii_backend_text_is_reversibly_escaped_only_for_ascii_output(layout, help_open):
    state = admitted_state("overview", raw=unicode_snapshot())
    state.help = help_open
    original = deepcopy(state.snapshot)
    unicode_output = presentation(layout, state, 240, 100)
    assert "peer-水e\u0301" in unicode_output.lines[1]
    if not help_open:
        assert "source · · 水 e\u0301" in unicode_output.text()
    output = presentation(layout, state, 240, 100, ascii_only=True)
    assert output.text().encode("ascii").decode("ascii") == output.text()
    assert_frame_geometry(output, 240, 100)
    assert r"peer-\u6c34e\u0301" in output.lines[1]
    if not help_open:
        assert r"source \xb7 \xb7 \u6c34 e\u0301" in output.text()
    else:
        assert locate_art(output, displayed_art("Q160", ascii_only=True), 240) is not None
        assert body_brand_spans(output)
    assert state.snapshot == original
    assert presentation(layout, state, 240, 100) == unicode_output


class StrictAsciiStream(io.StringIO):
    @property
    def encoding(self):
        return "ascii"

    def write(self, value):
        value.encode("ascii", errors="strict")
        return super().write(value)


@pytest.mark.parametrize("layout", ["plain", "dashboard"])
def test_finite_watch_writes_unicode_backend_records_to_a_strict_ascii_stream(monkeypatch, layout):
    raw = unicode_snapshot()
    original = deepcopy(raw)

    class Client:
        def __init__(self):
            self.calls = []

        def console_snapshot(self, *, include_static):
            self.calls.append(include_static)
            return raw

        def mixed_status(self):
            raise AssertionError("overview output must not request mixed data")

    monkeypatch.setattr(terminal_live, "get_terminal_size", lambda *_args: SimpleNamespace(columns=240, lines=100))
    client, stream = Client(), StrictAsciiStream()
    assert terminal_live.watch(client, frames=1, interval_s=.1, view="overview", stream=stream, layout=layout) == 0
    output = stream.getvalue()
    assert output.isascii() and "\x1b" not in output
    assert r"source \xb7 \xb7 \u6c34 e\u0301" in output
    assert r"peer-\u6c34e\u0301" in output
    assert client.calls == [True] and raw == original


def test_repeated_art_render_does_not_read_original_attachments_or_backend(monkeypatch):
    state = admitted_state()
    state.help = True
    def forbidden(*_args, **_kwargs):
        raise AssertionError("art repaint must use immutable local cells only")
    monkeypatch.setattr("builtins.open", forbidden)
    monkeypatch.setattr("socket.socket", forbidden)
    first = presentation("dashboard", state, 200, 90)
    assert body_brand_spans(first)
    for _ in range(5):
        assert presentation("dashboard", state, 200, 90) == first
        assert body_brand_spans(presentation("plain", state, 200, 90))


class GridTerminal:
    """In-memory painter oracle; this is not a terminal-emulation campaign."""
    def __init__(self, rows=90, columns=200):
        self.height, self.width = rows, columns
        self.rows = [" " * columns for _ in range(rows)]
        self.row = 0
        self.erasures = 0

    def getmaxyx(self):
        return self.height, self.width

    def erase(self):
        self.erasures += 1
        self.rows = [" " * self.width for _ in range(self.height)]

    def move(self, row, column):
        assert column == 0
        self.row = row

    def clrtoeol(self):
        self.rows[self.row] = " " * self.width

    def addnstr(self, row, column, text, limit, *_attributes):
        value = text[:limit]
        assert column + len(value) <= self.width
        line = self.rows[row]
        self.rows[row] = line[:column] + value + line[column + len(value):]

    def noutrefresh(self):
        pass


class PaintCurses:
    error = RuntimeError

    @staticmethod
    def doupdate():
        pass


@pytest.mark.parametrize("layout", ["plain", "dashboard"])
@pytest.mark.parametrize("transition", ["publication", "close-help"])
def test_painter_clears_every_old_art_cell_when_live_data_returns(layout, transition):
    state = TerminalState(view="links") if transition == "publication" else admitted_state()
    state.help = transition == "close-help"
    previous = presentation(layout, state, 200, 90)
    assert body_brand_spans(previous)
    if transition == "publication":
        assert state.admit(Publication(snapshot(), None, False, False, True, 0, 100.))
    else:
        state.key("h")
    current = presentation(layout, state, 200, 90)
    screen = GridTerminal()
    terminal_live._paint_frame(screen, previous, {"wordmark": 1, "accent": 2}, None, PaintCurses)
    terminal_live._paint_frame(screen, current, {"wordmark": 1, "accent": 2}, previous, PaintCurses)
    assert screen.rows == [line.ljust(200) for line in current.lines] + [" " * 200] * (90 - len(current.lines))
    assert screen.erasures == 1 and not body_brand_spans(current)
