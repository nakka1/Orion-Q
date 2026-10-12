"""User-provided ASCII branding, independent of the desktop renderer.

All glyphs occupy one terminal cell. Complete artwork uses only spare space in
waiting/help screens; it never replaces identity, navigation or scientific data.
"""
from __future__ import annotations

from dataclasses import replace
from functools import lru_cache
import os

from .terminal_brand_art import BRAND_ART


@lru_cache(maxsize=8)
def requires_ascii(encoding: str | None) -> bool:
    """Check the output encoding once, without probing terminal graphics."""
    try:
        "█⣿".encode(encoding or "utf-8")
    except (LookupError, UnicodeEncodeError):
        return True
    return False


def header_brand(header, columns, rows, *, ascii_only=False):
    """Keep operational header rows intact; full art belongs in unused space."""
    return list(header)


@lru_cache(maxsize=2)
def _art_variants(ascii_only):
    return tuple(replace(art, lines=tuple(line.replace("█", "#") for line in art.lines))
                 if ascii_only else art for art in BRAND_ART.values())


def choose_art(columns, rows, *, ascii_only=False):
    """Choose the largest complete supplied artwork that fits both dimensions."""
    candidates = [art for art in _art_variants(bool(ascii_only))
                  if art.width <= columns and art.height <= rows]
    return max(candidates, key=lambda art: (art.width * art.height, art.width, art.height, art.key),
               default=None)


def brand_art_spans(art):
    """Color original ink independently of text; never embed ANSI sequences."""
    spans = []
    wordmark = art.key.casefold().startswith("wordmark")
    for row, line in enumerate(art.lines):
        start = 0
        while start < len(line):
            if line[start] == " ":
                start += 1
                continue
            role = "wordmark" if wordmark and start < art.q_start_col else "accent"
            end = start + 1
            while end < len(line) and line[end] != " " and (
                    not wordmark or (end < art.q_start_col) == (start < art.q_start_col)):
                end += 1
            spans.append((row, start, line[start:end], role))
            start = end
    return tuple(spans)


def brand_spans(header):
    """Describe color runs separately from text: no ANSI in exports or records."""
    spans = []
    if header and header[0].startswith("OrionQ"):
        spans += [(0, 0, "Orion", "wordmark"), (0, 5, "Q", "accent")]
    return spans


def color_attributes(curses, *, eva_mode=False):
    """Use the current coral Q accent where supported, without redefining colors.

    Index 173 is the nearest xterm-256 coral to the desktop's #d97757. Basic
    terminals use their red accent; NO_COLOR retains the mark in plain text.
    """
    if eva_mode:
        from .terminal_effect import eva_attributes
        return eva_attributes(curses)
    attributes = {"wordmark": curses.A_BOLD, "accent": curses.A_BOLD}
    if "NO_COLOR" in os.environ or os.environ.get("TERM") == "dumb":
        return attributes
    try:
        curses.start_color()
        if not curses.has_colors():
            return attributes
        background = -1
        try:
            curses.use_default_colors()
        except curses.error:
            background = curses.COLOR_BLACK
        foreground = 173 if curses.COLORS >= 256 else curses.COLOR_RED
        curses.init_pair(1, foreground, background)
        attributes["accent"] |= curses.color_pair(1)
    except curses.error:
        pass
    return attributes
