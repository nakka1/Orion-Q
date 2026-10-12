"""Qualify bounded updates independently of a terminal's escape sequences."""
import pytest
from quantum_autopilot.service.terminal_dashboard import DashboardFrame
from quantum_autopilot.service.terminal_live import _paint_frame


class Terminal:
    def __init__(self):
        self.rows = [" " * 40 for _ in range(6)]
        self.row = 0
        self.erasures = self.writes = self.refreshes = 0

    def getmaxyx(self):
        return 6, 40

    def erase(self):
        self.erasures += 1
        self.rows = [" " * 40 for _ in range(6)]

    def move(self, row, column):
        assert column == 0
        self.row = row

    def clrtoeol(self):
        self.rows[self.row] = " " * 40

    def addnstr(self, row, column, value, limit, *attributes):
        self.writes += 1
        value = value[:limit]
        assert column + len(value) <= 40
        old = self.rows[row]
        self.rows[row] = old[:column] + value + old[column + len(value):]

    def noutrefresh(self):
        self.refreshes += 1


class Painter:
    error = RuntimeError

    def __init__(self):
        self.commits = 0

    def doupdate(self):
        self.commits += 1


def test_unchanged_publication_has_no_cell_writes_and_no_repeated_screen_erase():
    terminal, painter = Terminal(), Painter()
    frame = DashboardFrame(("OrionQ", "STATE ready"), ((0, 5, "Q", "accent"),))
    _paint_frame(terminal, frame, {"accent": 1}, None, painter)
    writes = terminal.writes
    for _ in range(20):
        _paint_frame(terminal, frame, {"accent": 1}, frame, painter)
    assert terminal.writes == writes and terminal.erasures == 1
    assert terminal.rows[0].rstrip() == "OrionQ" and terminal.rows[1].rstrip() == "STATE ready"
    assert painter.commits == terminal.refreshes == 21


def test_changed_and_removed_rows_leave_no_old_metric_tail_or_detail_content():
    terminal, painter = Terminal(), Painter()
    previous = DashboardFrame(("OrionQ", "long metric record", "old inspection"), ())
    current = DashboardFrame(("OrionQ", "no input"), ((1, 0, "no input", "warn"),))
    _paint_frame(terminal, previous, {}, None, painter)
    _paint_frame(terminal, current, {"warn": 1}, previous, painter)
    assert terminal.erasures == 1
    assert [row.rstrip() for row in terminal.rows[:3]] == ["OrionQ", "no input", ""]


def test_selection_style_changes_are_painted_even_when_the_record_text_is_unchanged():
    terminal, painter = Terminal(), Painter()
    previous = DashboardFrame(("NODE A",), ((0, 0, "NODE A", "plain"),))
    selected = DashboardFrame(previous.lines, ((0, 0, "NODE A", "selected"),))
    _paint_frame(terminal, previous, {"plain": 0}, None, painter)
    writes = terminal.writes
    _paint_frame(terminal, selected, {"selected": 1}, previous, painter)
    assert terminal.writes > writes and terminal.rows[0].rstrip() == "NODE A"


def test_finite_dashboard_uses_the_authenticated_read_worker_without_a_gui(monkeypatch):
    import io
    from types import SimpleNamespace
    from quantum_autopilot.service import terminal_live
    from tests.test_terminal_live import ControlledClient

    monkeypatch.setattr(terminal_live, "get_terminal_size", lambda *args: SimpleNamespace(columns=120, lines=40))
    client, output = ControlledClient(), io.StringIO()
    assert terminal_live.watch(client, frames=1, interval_s=.1, view="links", stream=output,
                               layout="dashboard") == 0
    assert len(client.calls) == 1 and not client.mixed_calls
    text = output.getvalue()
    assert "INSPECTOR / A-B" in text and "QBER" in text and "fixture-peer" in text
    assert "explicit scheduling fixture" in text and "no input" in text and "\x1b" not in text


def test_invalid_layout_is_rejected_before_any_request():
    import io
    import pytest
    from quantum_autopilot.service.terminal_live import watch
    from tests.test_terminal_live import ControlledClient

    client = ControlledClient()
    with pytest.raises(ValueError, match="unsupported terminal layout"):
        watch(client, frames=1, stream=io.StringIO(), layout="private-canary")
    assert not client.calls


def test_typematic_scroll_burst_at_bottom_reverses_without_an_intermediate_repaint():
    from quantum_autopilot.service.terminal_dashboard import render_dashboard
    from tests.test_terminal_dashboard import admitted_state

    state = admitted_state("satellites", "SAT")
    state.snapshot["live"]["satellites"]["SAT"]["housekeeping"] = {f"reading-{index}": index for index in range(80)}
    state.key("enter")
    render_dashboard(state, columns=120, rows=24, now=105.)
    for _ in range(1000):
        state.key("down")  # Real terminal key bursts are batched before painting.
    bottom = render_dashboard(state, columns=120, rows=24, now=105.)
    for _ in range(100):
        state.key("down")
    state.key("up")
    previous = render_dashboard(state, columns=120, rows=24, now=105.)
    assert previous.text() != bottom.text()


def test_input_burst_is_processed_in_order_with_one_presentation(monkeypatch):
    import sys
    from quantum_autopilot.service import terminal_live
    from tests.test_terminal_dashboard import admitted_state

    class Curses:
        error = RuntimeError
        KEY_UP, KEY_DOWN, KEY_ENTER, KEY_RIGHT, KEY_RESIZE = 259, 258, 343, 261, 410

        @staticmethod
        def curs_set(_value):
            pass

        @staticmethod
        def use_default_colors():
            pass

    class Screen:
        encoding = "utf-8"

        def __init__(self):
            self.inputs = iter(map(ord, "2\np" + "r" * 12 + "q"))

        def nodelay(self, _value):
            pass

        def getch(self):
            return next(self.inputs, -1)

        def getmaxyx(self):
            return 40, 120

    class Pump:
        def request(self, **_options):
            return True

        def take(self):
            return None

    frames = []
    monkeypatch.setitem(sys.modules, "curses", Curses())
    monkeypatch.setattr("quantum_autopilot.service.terminal_dashboard.style_attributes", lambda _curses: {})
    monkeypatch.setattr(terminal_live, "_paint_frame", lambda _screen, frame, *_args: frames.append(frame))
    state = admitted_state()
    assert terminal_live._interactive(Screen(), Pump(), state, 1., None) == 0
    assert state.view == "links" and state.details and state.paused and state.epoch == 12
    assert len(frames) == 1


def test_long_numbered_identifiers_remain_distinguishable_without_changing_backend_ids():
    from quantum_autopilot.service.terminal_dashboard import short_identifier
    identifiers = ["REP-CIMATEC-CIQUANTA-001", "REP-CIMATEC-CIQUANTA-002"]
    for ascii_only in (False, True):
        abbreviations = [short_identifier(value, 16, ascii_only=ascii_only) for value in identifiers]
        assert abbreviations[0] != abbreviations[1]
        assert all(value.endswith(identifier[-4:]) for value, identifier in zip(abbreviations, identifiers))
        assert all(len(value) <= 16 for value in abbreviations)
    assert short_identifier("A-B", 16) == "A-B"


@pytest.mark.parametrize("setting", ["NO_COLOR", "TERM", None])
def test_monochrome_session_preserves_terminal_defaults_after_curses_wrapper(monkeypatch, setting):
    from quantum_autopilot.service.terminal_live import _preserve_terminal_palette

    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setenv("TERM", "xterm-256color")
    if setting:
        monkeypatch.setenv(setting, "" if setting == "NO_COLOR" else "dumb")

    class Curses:
        error = RuntimeError

        def __init__(self):
            self.defaults = 0

        def use_default_colors(self):
            self.defaults += 1

    curses = Curses()
    _preserve_terminal_palette(curses)
    assert curses.defaults == int(setting is not None)


def test_terminal_without_color_support_keeps_monochrome_session_usable(monkeypatch):
    from quantum_autopilot.service.terminal_live import _preserve_terminal_palette

    monkeypatch.setenv("NO_COLOR", "")

    class Curses:
        error = RuntimeError

        @staticmethod
        def use_default_colors():
            raise RuntimeError("no color support")

    _preserve_terminal_palette(Curses())
