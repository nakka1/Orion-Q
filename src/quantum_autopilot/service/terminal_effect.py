"""Local terminal display mode; never an authentication provider or core input.

The access phrase selects an appearance only. The authenticated peer remains
the independently provisioned TLS identity, including while this form is open.
No animation, sound, service request or credential persistence is performed.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import os
import textwrap

from .terminal_brand import brand_art_spans, choose_art


CONNECTED = "Connected to E.V.A 0-1"
UNPLUG = "Unplug from E.V.A"
EMERGENCY_MESSAGE = "YOU SHOULD NOT BE ABLE TO CONNECT TO EVA"


@dataclass(slots=True)
class TerminalDisplay:
    active: bool = False
    prompt: str | None = None
    account: str = field(default="", repr=False)
    _password: str = field(default="", repr=False)
    feedback: str = ""

    @property
    def password_mask(self):
        return "*" * len(self._password)

    def open(self):
        self.close()
        self.prompt = "account"

    def close(self):
        self.account = self._password = self.feedback = ""
        self.prompt = None

    def unplug(self):
        self.active = False
        self.close()

    def shutdown(self):
        self.unplug()

    def key(self, key):
        if key in {"escape", "\x1b"}:
            self.close()
            return
        if self.prompt == "notice":
            if key in {"enter", "\n", "\r"}:
                self.close()
            return
        if self.prompt not in {"account", "password"}:
            return
        if key in {"tab", "\t"}:
            self.prompt = "password" if self.prompt == "account" else "account"
        elif key in {"enter", "\n", "\r"}:
            if self.prompt == "account":
                self.prompt = "password"
            else:
                accepted = (self.account.strip().casefold() == "s. ikari"
                            and self._password == "thirdpilot")
                self._password = ""
                if accepted:
                    self.close()
                    self.active, self.prompt = True, "notice"
                else:
                    self.feedback = "Display access phrase not accepted."
        elif key in {"backspace", "\b", "\x7f"}:
            if self.prompt == "account":
                self.account = self.account[:-1]
            else:
                self._password = self._password[:-1]
        elif len(key) == 1 and 32 <= ord(key) <= 126:
            name, limit = ("account", 96) if self.prompt == "account" else ("_password", 64)
            value = getattr(self, name)
            if len(value) < limit:
                setattr(self, name, value + key)
                self.feedback = ""
            else:
                self.feedback = "Display field limit reached."


def footer_text(display, *, dashboard=False):
    action = "u " + UNPLUG + " | q quit | l display login" if display.active else "l display login | q quit"
    navigation = ("1-8 views | Tab next | arrows select/scroll | Enter details | p pause | r reload | h help | v layout"
                  if dashboard else "Tab/arrows Enter details | p pause | r reload | h help")
    return action + " | " + navigation


def render_display(display, *, columns, rows, ascii_only=False):
    """Bounded form/notice, with a masked phrase and complete optional artwork."""
    width, height = max(1, min(511, int(columns) - 1)), max(1, min(200, int(rows)))
    if display.prompt == "notice":
        content = [("EMERGENCY", "warn"), (EMERGENCY_MESSAGE, "title"),
                   (CONNECTED, "accent"),
                   ("Appearance only; backend identity and permissions are unchanged.", "muted")]
        footer = "Enter/Esc return to dashboard"
    else:
        content = [("OrionQ TERMINAL | DISPLAY SIGN-IN", "title"),
                   ("Local appearance only; mTLS identity and permissions stay unchanged.", "muted"),
                   ("Display login: " + display.account, "selected" if display.prompt == "account" else "plain"),
                   ("Password: " + display.password_mask, "selected" if display.prompt == "password" else "plain"),
                   (display.feedback or "The form does not send credentials to the core.",
                    "warn" if display.feedback else "muted")]
        footer = "Enter next/submit | Tab field | Esc cancel"
    lines, spans = [], []
    for value, role in content:
        for part in textwrap.wrap(value, width, break_on_hyphens=False) or [""]:
            if len(lines) >= height - 1:
                break
            spans.append((len(lines), 0, part, role))
            lines.append(part)
    art = choose_art(width, height - len(lines) - 2, ascii_only=ascii_only)
    if art is not None:
        lines.append("")
        top, left = len(lines), (width - art.width) // 2
        lines += [" " * left + line for line in art.lines]
        spans += [(top + row, left + column, text, role)
                  for row, column, text, role in brand_art_spans(art)]
    lines += [""] * (height - len(lines) - 1)
    lines.append(footer[:width])
    spans.append((height - 1, 0, lines[-1], "title"))
    return tuple(lines), tuple(spans)


def eva_attributes(curses):
    """EVA-01 accents with semantic faults, 8-color fallback and NO_COLOR."""
    bold, reverse = curses.A_BOLD, getattr(curses, "A_REVERSE", 0)
    palette = (("plain", 157, "COLOR_WHITE"), ("title", 118, "COLOR_GREEN"),
               ("wordmark", 118, "COLOR_GREEN"), ("accent", 135, "COLOR_MAGENTA"),
               ("border", 93, "COLOR_MAGENTA"), ("chart", 118, "COLOR_GREEN"),
               ("good", 118, "COLOR_GREEN"), ("warn", 208, "COLOR_YELLOW"),
               ("bad", 203, "COLOR_RED"), ("muted", 245, "COLOR_WHITE"),
               ("selected", 118, "COLOR_GREEN"))
    roles = {role: bold if role in {"title", "wordmark", "accent", "good", "warn", "bad", "selected"} else 0
             for role, _indexed, _basic in palette}
    roles["selected"] |= reverse
    if "NO_COLOR" in os.environ or os.environ.get("TERM") == "dumb":
        return roles
    try:
        curses.start_color()
        if not curses.has_colors():
            return roles
        colors = getattr(curses, "COLORS", 0)
        background = 234 if colors >= 256 else getattr(curses, "COLOR_BLACK", 0)
        pairs = {}
        for role, indexed, basic in palette:
            foreground = indexed if colors >= 256 else getattr(curses, basic, 0)
            pair = pairs.get(foreground)
            if pair is None:
                pair = len(pairs) + 1
                if pair >= getattr(curses, "COLOR_PAIRS", 64):
                    continue
                curses.init_pair(pair, foreground, background)
                pairs[foreground] = pair
            roles[role] |= curses.color_pair(pair)
    except curses.error:
        pass
    return roles
