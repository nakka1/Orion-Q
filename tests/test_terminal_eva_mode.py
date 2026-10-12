"""Local EVA appearance never becomes backend identity or authorization."""
from copy import deepcopy
import io
import json
import ssl
import sys
from types import SimpleNamespace

import pytest

from quantum_autopilot.service import operator_cli, terminal_live
from quantum_autopilot.service.client import CoreClient
from quantum_autopilot.service.terminal_brand import color_attributes
from quantum_autopilot.service.terminal_dashboard import DashboardFrame, render_dashboard, style_attributes
from quantum_autopilot.service.terminal_effect import TerminalDisplay, render_display
from quantum_autopilot.service.terminal_live import Publication, TerminalState, render_frame
from tests.test_terminal_ascii_brand import displayed_art, locate_art
from tests.test_terminal_dashboard import admitted_state, assert_frame_geometry, snapshot


CONNECTED = "Connected to E.V.A 0-1"
UNPLUG = "Unplug from E.V.A"
WARNING = "YOU SHOULD NOT BE ABLE TO CONNECT TO EVA"
SECRET = "hidden-display-phrase-fixture"
BASE = ["--url", "https://localhost:443", "--ca", "external-ca.pem",
        "--cert", "external-client.pem", "--key", "external-client.key"]


def type_text(target, value):
    for char in value:
        target.key(char)


def connect(display, account="S. Ikari", password="thirdpilot", *, close_notice=False):
    display.open()
    type_text(display, account)
    display.key("\n")
    type_text(display, password)
    display.key("\n")
    if close_notice:
        display.key("\n")


def frame_for(layout, state, *, columns=200, rows=90, ascii_only=False):
    if layout == "dashboard":
        return render_dashboard(state, columns=columns, rows=rows, now=105., ascii_only=ascii_only)
    return DashboardFrame(*render_frame(state, columns=columns, rows=rows, now=105., ascii_only=ascii_only))


def backend_and_navigation(state):
    return deepcopy({key: value for key, value in vars(state).items() if key != "display"})


@pytest.mark.parametrize("account", ["S. Ikari", "  S. iKArI  ", "s. ikari"])
def test_exact_phrase_and_casefolded_account_activate_only_local_notice(account):
    display = TerminalDisplay()
    connect(display, account)
    assert display.active and display.prompt == "notice"
    assert display.account == display._password == display.password_mask == ""
    notice = DashboardFrame(*render_display(display, columns=120, rows=40))
    assert WARNING in notice.text() and CONNECTED in notice.text()
    assert "Appearance only" in notice.text()
    assert "thirdpilot" not in notice.text() + repr(display)
    display.key("\n")
    assert display.active and display.prompt is None


@pytest.mark.parametrize("account,password", [
    ("", "thirdpilot"), ("Shinji Ikari", "thirdpilot"), ("EVA operator", "thirdpilot"),
    ("S.Ikari", "thirdpilot"), ("S. Ikari!", "thirdpilot"),
    ("S. Ikari", ""), ("S. Ikari", "wrong"), ("S. Ikari", "THIRDPILOT"),
    ("S. Ikari", " thirdpilot "), ("S. Ikari", "thirdpilotx"),
])
def test_wrong_display_credentials_are_rejected_and_phrase_is_erased(account, password):
    display = TerminalDisplay()
    connect(display, account, password)
    assert not display.active and display.prompt == "password"
    assert display._password == display.password_mask == ""
    assert display.feedback == "Display access phrase not accepted."
    text = "\n".join(render_display(display, columns=120, rows=40)[0])
    assert display.feedback in text and CONNECTED not in text
    if password:
        assert password not in text + repr(display)


def test_wrong_phrase_can_be_corrected_without_retaining_the_first_phrase():
    display = TerminalDisplay()
    connect(display, password=SECRET)
    assert SECRET not in repr(display) and display._password == ""
    type_text(display, "thirdpilot")
    display.key("enter")
    assert display.active and display.prompt == "notice"
    assert display.account == display._password == display.feedback == ""


@pytest.mark.parametrize("backspace", ["backspace", "\b", "\x7f"])
def test_backspace_edits_each_field_without_echoing_the_phrase(backspace):
    display = TerminalDisplay()
    display.open()
    type_text(display, "S. Ikarix")
    display.key(backspace)
    assert display.account == "S. Ikari"
    display.key("enter")
    type_text(display, "thirdpilotx")
    display.key(backspace)
    assert display.password_mask == "*" * 10
    assert "thirdpilot" not in repr(display)
    display.key("enter")
    assert display.active and display._password == ""


def test_field_limits_and_ascii_filter_bound_input_without_terminal_controls():
    display = TerminalDisplay()
    display.open()
    type_text(display, "A" * 150)
    assert display.account == "A" * 96
    assert display.feedback == "Display field limit reached."
    display.key("tab")
    type_text(display, "P" * 100)
    assert display.password_mask == "*" * 64
    before = display.account, display._password
    for key in ("水", "é", "\u0301", "\x00", "\x9b", "up", "down"):
        display.key(key)
    assert (display.account, display._password) == before
    display.key("\t")
    assert display.prompt == "account"
    display.key("\t")
    assert display.prompt == "password"
    text = "\n".join(render_display(display, columns=120, rows=40)[0])
    assert "P" * 64 not in text + repr(display)
    assert all(char.isprintable() or char == "\n" for char in text)


@pytest.mark.parametrize("active", [False, True])
@pytest.mark.parametrize("cancel", ["escape", "\x1b", "close"])
def test_cancel_clears_all_fields_and_retains_the_previous_appearance(active, cancel):
    display = TerminalDisplay(active=active)
    display.open()
    type_text(display, "private-account-fixture")
    display.key("enter")
    type_text(display, SECRET)
    assert SECRET not in repr(display)
    if cancel == "close":
        display.close()
    else:
        display.key(cancel)
    assert display.active is active and display.prompt is None
    assert display.account == display._password == display.feedback == ""


@pytest.mark.parametrize("method", ["unplug", "shutdown"])
def test_unplug_and_shutdown_reset_theme_and_sensitive_fields_idempotently(method):
    display = TerminalDisplay()
    connect(display)
    display.open()
    display.key("tab")
    type_text(display, SECRET)
    getattr(display, method)()
    getattr(display, method)()
    assert not display.active and display.prompt is None
    assert display.account == display._password == display.feedback == ""


def test_modal_credentials_do_not_execute_navigation_pause_help_reload_or_quit():
    state = admitted_state("links", "A-B")
    state.help, state.details, state.paused = True, True, True
    before = backend_and_navigation(state)
    state.key("l")
    type_text(state, "S. Ikari")
    state.key("enter")
    for char in "qvjrhp":
        assert state.key(char) is True
    assert state.display.password_mask == "*" * 6
    assert backend_and_navigation(state) == before
    state.key("escape")
    assert state.display._password == ""
    state.key("L")
    type_text(state, "S. Ikari")
    state.key("enter")
    type_text(state, "thirdpilot")
    state.key("enter")
    assert state.display.active and state.display.prompt == "notice"
    assert backend_and_navigation(state) == before
    state.key("enter")
    state.key("U")
    assert not state.display.active and backend_and_navigation(state) == before


@pytest.mark.parametrize("layout", ["plain", "dashboard"])
@pytest.mark.parametrize("ascii_only", [False, True])
def test_theme_banner_and_unplug_restore_frames_without_changing_backend_identity(layout, ascii_only):
    state = admitted_state("links", "A-B")
    original = frame_for(layout, state, ascii_only=ascii_only)
    before = backend_and_navigation(state)
    connect(state.display, close_notice=True)
    active = frame_for(layout, state, ascii_only=ascii_only)
    assert CONNECTED in active.lines[3] and UNPLUG in active.lines[-1]
    assert active.lines[:3] == original.lines[:3]
    assert "fixture-peer" in active.lines[1] and "authenticated backend" in active.lines[0]
    assert_frame_geometry(active, 200, 90)
    assert backend_and_navigation(state) == before
    state.key("u")
    assert frame_for(layout, state, ascii_only=ascii_only) == original
    assert backend_and_navigation(state) == before


@pytest.mark.parametrize("layout", ["plain", "dashboard"])
def test_local_connection_does_not_authenticate_missing_or_failed_backend(layout):
    state = TerminalState(view="links")
    connect(state.display, close_notice=True)
    output = frame_for(layout, state)
    assert CONNECTED in output.text()
    assert "STALE" in output.lines[0] and "not authenticated" in output.lines[1]
    assert state.stale and state.snapshot is None
    assert not state.admit(Publication(None, None, True, False, False, 0, 100.))
    assert state.stale and state.snapshot is None and state.display.active


@pytest.mark.parametrize("layout", ["plain", "dashboard"])
def test_mixed_view_does_not_inherit_console_identity_from_the_local_persona(layout):
    state = admitted_state("mixed", "A")
    original = deepcopy(state.mixed)
    connect(state.display, close_notice=True)
    output = frame_for(layout, state)
    assert CONNECTED in output.text() and "identity not published by mixed status" in output.lines[1]
    assert "fixture-peer" not in output.text() and "S. Ikari" not in output.text()
    assert "no inferred F/QBER" in output.text() and state.mixed == original


def test_published_data_and_stale_state_continue_during_password_entry_and_pause():
    state = admitted_state("links", "A-B")
    state.key("p")
    state.key("l")
    type_text(state, "S. Ikari")
    state.key("enter")
    type_text(state, SECRET)
    assert state.admit(Publication(snapshot(2), None, False, False, False, 0, 101.))
    assert state.snapshot["live"]["tick"] == 2 and state.visible["live"]["tick"] == 1
    assert state.display.prompt == "password" and state.display.password_mask == "*" * len(SECRET)
    assert not state.admit(Publication(None, None, True, False, False, 0, 102.))
    assert state.stale
    state.key("escape")
    state.key("p")
    assert state.visible["live"]["tick"] == 2 and state.stale and state.display._password == ""


@pytest.mark.parametrize("columns,rows", [(1, 1), (2, 2), (12, 6), (40, 10), (65, 18), (120, 40), (200, 90), (240, 100)])
@pytest.mark.parametrize("ascii_only", [False, True])
def test_all_modal_stages_are_bounded_masked_and_safe_at_small_and_large_sizes(columns, rows, ascii_only):
    display = TerminalDisplay()
    display.open()
    type_text(display, "S. Ikari")
    for stage in ("account", "password", "notice"):
        if stage == "password":
            display.key("enter")
            type_text(display, "thirdpilot")
        elif stage == "notice":
            display.key("enter")
        assert display.prompt == stage
        output = DashboardFrame(*render_display(display, columns=columns, rows=rows, ascii_only=ascii_only))
        assert_frame_geometry(output, columns, rows)
        assert "thirdpilot" not in output.text() + repr(display)
        if ascii_only:
            assert output.text().isascii()
        if columns >= 200 and rows >= 90:
            assert locate_art(output, displayed_art("Q160", ascii_only), columns) is not None


class PaletteCurses:
    A_BOLD, A_REVERSE = 1, 4
    COLOR_BLACK, COLOR_RED, COLOR_GREEN, COLOR_YELLOW = 0, 1, 2, 3
    COLOR_BLUE, COLOR_MAGENTA, COLOR_CYAN, COLOR_WHITE = 4, 5, 6, 7
    COLOR_PAIRS = 64
    KEY_UP, KEY_DOWN, KEY_ENTER, KEY_RIGHT, KEY_RESIZE, KEY_BACKSPACE = 259, 258, 343, 261, 410, 263
    error = RuntimeError

    def __init__(self, colors=256, available=True):
        self.COLORS, self.available = colors, available
        self.calls, self.pairs = [], []
        self.escape_delays = []

    def set_escdelay(self, value):
        self.calls.append("set_escdelay")
        self.escape_delays.append(value)

    def start_color(self):
        self.calls.append("start_color")

    def has_colors(self):
        self.calls.append("has_colors")
        return self.available

    def use_default_colors(self):
        self.calls.append("use_default_colors")

    def init_pair(self, pair, foreground, background):
        self.calls.append("init_pair")
        self.pairs.append((pair, foreground, background))

    def color_pair(self, pair):
        return pair << 8

    def init_color(self, *_args):
        raise AssertionError("local theme must not redefine the terminal palette")

    def curs_set(self, *_args):
        pass


EVA_PALETTE = {"plain": 157, "title": 118, "wordmark": 118, "accent": 135, "border": 93,
               "chart": 118, "good": 118, "warn": 208, "bad": 203, "muted": 245, "selected": 118}


@pytest.fixture
def color_environment(monkeypatch):
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setenv("TERM", "xterm-256color")


def test_escape_delay_reduces_the_unconfigured_default_without_mutating_environment(monkeypatch):
    monkeypatch.delenv("ESCDELAY", raising=False)
    curses = PaletteCurses()
    terminal_live._configure_escape_delay(curses)
    assert curses.escape_delays == [100]
    assert "ESCDELAY" not in terminal_live.os.environ


@pytest.mark.parametrize("value", ["", "0", "1000"])
def test_escape_delay_preserves_every_explicit_environment_setting(monkeypatch, value):
    monkeypatch.setenv("ESCDELAY", value)
    curses = PaletteCurses()
    terminal_live._configure_escape_delay(curses)
    assert curses.escape_delays == [] and curses.calls == []
    assert terminal_live.os.environ["ESCDELAY"] == value


def test_escape_delay_is_optional_on_curses_without_the_setter(monkeypatch):
    monkeypatch.delenv("ESCDELAY", raising=False)
    terminal_live._configure_escape_delay(SimpleNamespace(error=RuntimeError))


@pytest.mark.parametrize("failure", [RuntimeError, ValueError])
def test_escape_delay_tolerates_unsupported_setter_errors(monkeypatch, failure):
    monkeypatch.delenv("ESCDELAY", raising=False)
    calls = []

    def unsupported(value):
        calls.append(value)
        raise failure("fixture curses cannot configure ESC delay")

    terminal_live._configure_escape_delay(SimpleNamespace(error=RuntimeError, set_escdelay=unsupported))
    assert calls == [100]


def test_escape_delay_does_not_hide_unrelated_setter_defects(monkeypatch):
    monkeypatch.delenv("ESCDELAY", raising=False)

    def defective(_value):
        raise AssertionError("fixture unexpected defect")

    with pytest.raises(AssertionError, match="fixture unexpected defect"):
        terminal_live._configure_escape_delay(SimpleNamespace(error=RuntimeError, set_escdelay=defective))


@pytest.mark.parametrize("palette", [color_attributes, style_attributes])
def test_eva_256_palette_has_declared_accents_and_semantic_fault_colors(color_environment, palette):
    curses = PaletteCurses()
    attributes = palette(curses, eva_mode=True)
    pairs = {pair: (foreground, background) for pair, foreground, background in curses.pairs}
    assert set(attributes) == set(EVA_PALETTE)
    for role, foreground in EVA_PALETTE.items():
        assert pairs[attributes[role] >> 8] == (foreground, 234)
    assert attributes["selected"] & curses.A_REVERSE
    assert attributes["bad"] != attributes["good"]


@pytest.mark.parametrize("palette", [color_attributes, style_attributes])
def test_basic_palette_and_unsupported_color_terminals_remain_usable(color_environment, palette):
    curses = PaletteCurses(colors=8)
    attributes = palette(curses, eva_mode=True)
    assert set(attributes) == set(EVA_PALETTE)
    assert all(0 <= foreground < 8 and background == curses.COLOR_BLACK
               for _, foreground, background in curses.pairs)
    assert attributes["selected"] & curses.A_REVERSE
    unsupported = PaletteCurses(available=False)
    roles = palette(unsupported, eva_mode=True)
    assert not unsupported.pairs and roles["title"] & unsupported.A_BOLD


@pytest.mark.parametrize("palette", [color_attributes, style_attributes])
@pytest.mark.parametrize("no_color,term", [("", "xterm-256color"), ("1", "xterm-256color"), (None, "dumb")])
def test_monochrome_retains_local_mode_text_without_initializing_colors(monkeypatch, palette, no_color, term):
    monkeypatch.delenv("NO_COLOR", raising=False)
    if no_color is not None:
        monkeypatch.setenv("NO_COLOR", no_color)
    monkeypatch.setenv("TERM", term)
    curses = PaletteCurses()
    attributes = palette(curses, eva_mode=True)
    assert not curses.calls and not curses.pairs
    assert attributes["title"] & curses.A_BOLD and attributes["selected"] & curses.A_REVERSE
    state = admitted_state()
    connect(state.display, close_notice=True)
    for layout in ("plain", "dashboard"):
        output = frame_for(layout, state, ascii_only=True)
        assert CONNECTED in output.text() and UNPLUG in output.text() and output.text().isascii()


@pytest.mark.parametrize("palette", [color_attributes, style_attributes])
def test_default_palette_can_be_restored_exactly_after_unplug(color_environment, palette):
    curses = PaletteCurses()
    original = palette(curses)
    active = palette(curses, eva_mode=True)
    restored = palette(curses)
    assert restored == original and active["accent"] != original["accent"]


def test_local_persona_never_satisfies_the_client_tls_identity_requirement():
    display = TerminalDisplay()
    connect(display, close_notice=True)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    context.minimum_version = context.maximum_version = ssl.TLSVersion.TLSv1_3
    with pytest.raises(ValueError, match="verified TLS 1.3"):
        CoreClient("https://localhost:443", context)
    assert display.active


def test_cli_tls_failure_remains_fatal_without_opening_local_display(monkeypatch, capsys):
    def unavailable(*_args, **_kwargs):
        raise ValueError(SECRET)
    def forbidden(*_args, **_kwargs):
        raise AssertionError("invalid TLS must not create a client or run watch")
    monkeypatch.setattr(operator_cli, "build_tls_context", unavailable)
    monkeypatch.setattr(operator_cli, "CoreClient", forbidden)
    monkeypatch.setattr(terminal_live, "watch", forbidden)
    assert operator_cli.main(BASE + ["watch", "--frames", "1"]) == operator_cli.EXIT_INPUT
    captured = capsys.readouterr()
    assert captured.out == "" and SECRET not in captured.err
    assert json.loads(captured.err)["status"] == "invalid_input_or_tls_identity"


def test_cli_has_no_password_argument_or_echo_for_local_cosmetic_credentials(monkeypatch, capsys):
    def forbidden(*_args, **_kwargs):
        raise AssertionError("invalid argv must not read TLS files or run watch")
    monkeypatch.setattr(operator_cli, "build_tls_context", forbidden)
    assert operator_cli.main(BASE + ["watch", "--password", SECRET]) == operator_cli.EXIT_INPUT
    assert SECRET not in capsys.readouterr().err


@pytest.mark.parametrize("layout", ["plain", "dashboard"])
def test_active_display_watch_uses_only_existing_core_gets_and_real_published_identity(monkeypatch, layout):
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.minimum_version = context.maximum_version = ssl.TLSVersion.TLSv1_3
    client = CoreClient("https://localhost:443", context)
    requests, states = [], []
    original_state = TerminalState

    def transport(method, path, payload=None):
        requests.append((method, path, payload))
        assert method == "GET" and path.startswith("/api/v1/console/snapshot?")
        assert payload is None
        return snapshot(len(requests))

    def local_display(**kwargs):
        state = original_state(**kwargs)
        connect(state.display, close_notice=True)
        states.append(state)
        return state

    monkeypatch.setattr(client, "_request", transport)
    monkeypatch.setattr(terminal_live, "TerminalState", local_display)
    monkeypatch.setattr(terminal_live, "get_terminal_size", lambda *_args: SimpleNamespace(columns=200, lines=90))
    stream = io.StringIO()
    assert terminal_live.watch(client, frames=2, interval_s=.1, view="links", stream=stream, layout=layout) == 0
    assert requests == [("GET", "/api/v1/console/snapshot?include_static=true", None),
                        ("GET", "/api/v1/console/snapshot?include_static=false", None)]
    output = stream.getvalue()
    assert CONNECTED in output and "fixture-peer" in output and "thirdpilot" not in output
    assert "S. Ikari" not in output and "thirdpilot" not in repr(requests)
    assert states[0].snapshot["security"] == {"subject": "fixture-peer", "source": "certificate"}
    assert states[0].snapshot["live"]["tick"] == 2 and not states[0].display.active
    assert client.context is context and context.verify_mode == ssl.CERT_REQUIRED and context.check_hostname


@pytest.mark.parametrize("layout", ["plain", "dashboard"])
@pytest.mark.parametrize("outcome", ["success", "backend-error", "write-error", "interrupt"])
def test_watch_finally_clears_display_fields_and_mode_on_all_exit_paths(monkeypatch, layout, outcome):
    captured_states = []
    original_state = TerminalState

    def capture_state(**kwargs):
        state = original_state(**kwargs)
        state.display.active = True
        state.display.open()
        state.display.key("tab")
        type_text(state.display, SECRET)
        captured_states.append(state)
        return state

    class Client:
        def __init__(self):
            self.calls = 0

        def console_snapshot(self, *, include_static):
            self.calls += 1
            if outcome == "backend-error":
                raise RuntimeError(SECRET)
            return snapshot()

    class Stream(io.StringIO):
        def write(self, text):
            assert SECRET not in text
            if outcome == "write-error":
                raise OSError("fixture output unavailable")
            if outcome == "interrupt":
                raise KeyboardInterrupt
            return super().write(text)

    monkeypatch.setattr(terminal_live, "TerminalState", capture_state)
    monkeypatch.setattr(terminal_live, "get_terminal_size", lambda *_args: SimpleNamespace(columns=200, lines=90))
    client, stream = Client(), Stream()
    if outcome == "write-error":
        with pytest.raises(OSError, match="fixture output unavailable"):
            terminal_live.watch(client, frames=1, interval_s=.1, stream=stream, layout=layout)
    else:
        result = terminal_live.watch(client, frames=1, interval_s=.1, stream=stream, layout=layout)
        assert result == {"success": 0, "backend-error": 3, "interrupt": 130}[outcome]
    assert len(captured_states) == 1 and client.calls == 1
    display = captured_states[0].display
    assert not display.active and display.prompt is None
    assert display.account == display._password == display.feedback == ""
    assert SECRET not in stream.getvalue()


@pytest.mark.parametrize("escape_delay", [None, "1000"])
def test_interactive_raw_credential_keys_preserve_backend_and_redraw_each_theme_transition(monkeypatch, color_environment, escape_delay):
    monkeypatch.delenv("ESCDELAY", raising=False)
    if escape_delay is not None:
        monkeypatch.setenv("ESCDELAY", escape_delay)
    curses = PaletteCurses()
    monkeypatch.setitem(sys.modules, "curses", curses)
    # Batch separators allow observing notice, dashboard, plain and restored theme.
    batches = ["lS. Ikari\nthirdpilot\n", "\n", "v", "u", "q"]
    inputs = [code for batch in batches for code in [*map(ord, batch), -1]]

    class Screen:
        encoding = "utf-8"

        def __init__(self):
            self.inputs = iter(inputs)
            self.backgrounds, self.defaults = [], []

        def nodelay(self, value):
            assert value is True

        def getch(self):
            return next(self.inputs, -1)

        def getmaxyx(self):
            return 90, 200

        def bkgd(self, value, attribute):
            assert value == " "
            self.backgrounds.append(attribute)

        def attrset(self, attribute):
            self.defaults.append(attribute)

    class Pump:
        def __init__(self):
            self.requests = []

        def request(self, **options):
            self.requests.append(options)
            return True

        def take(self):
            return None

    paints = []
    def capture_paint(_screen, frame, attributes, previous, _curses):
        paints.append((frame, dict(attributes), previous))
    monkeypatch.setattr(terminal_live, "_paint_frame", capture_paint)
    monkeypatch.setattr(terminal_live.time, "sleep", lambda _seconds: None)
    state = admitted_state("links", "A-B")
    original = backend_and_navigation(state)
    screen, pump = Screen(), Pump()
    assert terminal_live._interactive(screen, pump, state, .1, None) == 0
    assert curses.escape_delays == ([100] if escape_delay is None else [])
    if escape_delay is None:
        assert curses.calls[0] == "set_escdelay"  # Configure once, before either palette.
    assert backend_and_navigation(state) == original
    assert not state.display.active and state.display._password == ""
    assert any(WARNING in frame.text() for frame, _, _ in paints)
    assert any(CONNECTED in frame.text() and "fixture-peer" in frame.text() for frame, _, _ in paints)
    assert paints[0][2] is None  # First accepted mode forces a complete repaint.
    assert paints[2][2] is None and paints[3][2] is None  # v then Unplug invalidate old colors.
    assert paints[0][1]["plain"] != 0 and paints[-1][1].get("plain", 0) == 0
    assert screen.backgrounds[-1] == screen.defaults[-1] == 0
    assert pump.requests and all(row["mixed_only"] is False for row in pump.requests)
    assert all("thirdpilot" not in frame.text() for frame, _, _ in paints)
