"""Bounded, source-faithful CLI dashboard, independent of curses and the core.

The renderer only reads an admitted TerminalState. Dense charts rasterize actual
timestamped samples into Braille subcells, without interpolation. Missing data
remain gaps; absent or ambiguous clocks explicitly fall back to sample order.
Native curses owns input/painting on Linux, without a GUI or extra runtime packages.
"""
from __future__ import annotations

from dataclasses import dataclass
import os
import time
import unicodedata

from .terminal_brand import brand_art_spans, brand_spans, choose_art, header_brand
from .terminal_effect import CONNECTED, footer_text, render_display
from .terminal_live import _body, bounded_json, clean, clip, metric, number, render_frame, sparkline


@dataclass(frozen=True)
class DashboardFrame:
    lines: tuple[str, ...]
    spans: tuple[tuple[int, int, str, str], ...]

    def text(self):
        return "\n".join(self.lines)


@dataclass(frozen=True)
class Chart:
    lines: tuple[str, ...]
    low: float | None
    high: float | None
    sample_count: int
    missing_count: int


@dataclass(frozen=True)
class TimeChart:
    lines: tuple[str, ...]
    low: float | None
    high: float | None
    sample_count: int
    missing_count: int
    invalid_count: int
    plotted_count: int
    occupied_dots: int
    collisions: int
    excluded_count: int
    mode: str
    start_s: float | None
    end_s: float | None
    first_sample_s: float | None
    last_sample_s: float | None


def time_chart(values, width, height, *, times=None, end_s=None, window_s=60.,
               bounds=None, ascii_only=False):
    """Rasterize received points, never interpolate, repeat or clamp measurements.

    Unicode cells have two horizontal and four vertical dot positions. Multiple
    measurements at the same dot are counted as collisions, not new points.
    The time window is relative to the caller's source clock, never wall time.
    """
    width, height = min(512, max(1, int(width))), min(200, max(1, int(height)))
    window = number(window_s)
    anchor = number(end_s)
    if window is None or window <= 0 or (end_s is not None and anchor is None):
        raise ValueError("Chart window and explicit clock must be finite; window must be positive")
    if bounds is None:
        lower, upper = None, None
    else:
        if not isinstance(bounds, (tuple, list)) or len(bounds) != 2:
            raise ValueError("Chart bounds must contain a lower and upper limit")
        lower, upper = (number(value) for value in bounds)
        if any(raw is not None and finite is None for raw, finite in zip(bounds, (lower, upper))):
            raise ValueError("Chart bounds must be finite or absent")
        if lower is not None and upper is not None and lower > upper:
            raise ValueError("Chart lower bound exceeds upper bound")
    values = list(values)
    timestamps = [number(value) for value in times] if times is not None else []
    timed = bool(timestamps) and len(timestamps) == len(values) and all(
        value is not None for value in timestamps) and all(
            right >= left for left, right in zip(timestamps, timestamps[1:]))
    subwidth, subheight = (width, height) if ascii_only else (2 * width, 4 * height)
    start = None
    if timed:
        anchor = timestamps[-1] if anchor is None else anchor
        start = number(anchor - window)
        if start is None:
            raise ValueError("Chart time window exceeds finite clock range")
        # Relative ages also work when a large absolute clock cannot represent
        # anchor-window exactly. No point outside the window may wrap the grid.
        selected = [(at, value) for at, value in zip(timestamps, values) if 0 <= anchor - at <= window]
        mode = "time"
    else:
        selected = [(None, value) for value in values[-subwidth:]]
        anchor, mode = None, "samples"
    parsed = [(at, number(value)) for at, value in selected]
    valid = [(at, value) for at, value in parsed if value is not None
             and (lower is None or value >= lower) and (upper is None or value <= upper)]
    missing = sum(value is None for _at, value in parsed)
    invalid = len(parsed) - missing - len(valid)
    finite = [value for _at, value in valid]
    low = lower if lower is not None else min(finite) if finite else None
    high = upper if upper is not None else max(finite) if finite else None
    # A nonnegative all-zero series belongs on the zero baseline, not at midscale.
    if lower == 0 and upper is None and high == 0:
        high = 1.
    if upper == 0 and lower is None and low == 0:
        low = -1.
    grid = [[0 for _ in range(width)] for _ in range(height)]
    occupied = set()
    for index, (at, value) in enumerate(parsed):
        if value is None or (lower is not None and value < lower) or (upper is not None and value > upper):
            continue
        column = (round((1. - (anchor - at) / window) * (subwidth - 1)) if timed else
                  subwidth - len(parsed) + index)
        scale = max(abs(low), abs(high), 1.)
        position = (subheight - 1) // 2 if high == low else round(
            (high / scale - value / scale) / (high / scale - low / scale) * (subheight - 1))
        occupied.add((column, position))
        if ascii_only:
            grid[position][column] = 1
        else:
            bit = ((0, 1, 2, 6), (3, 4, 5, 7))[column % 2][position % 4]
            grid[position // 4][column // 2] |= 1 << bit
    lines = tuple("".join(("*" if value else " ") if ascii_only else
                          (chr(0x2800 + value) if value else " ") for value in row) for row in grid)
    return TimeChart(lines, low, high, len(parsed), missing, invalid, len(valid), len(occupied),
                     len(valid) - len(occupied), len(values) - len(parsed), mode, start, anchor,
                     selected[0][0] if timed and selected else None,
                     selected[-1][0] if timed and selected else None)


def sample_chart(values, width, height, *, ascii_only=False):
    """One column per sample, no interpolation, connecting lines or padding data."""
    width, height = max(1, int(width)), max(1, int(height))
    values = values[-width:]
    samples = [number(value) for value in values]
    finite = [value for value in samples if value is not None]
    low, high = (min(finite), max(finite)) if finite else (None, None)
    grid = [[" " for _ in samples] for _ in range(height)]
    for column, value in enumerate(samples):
        if value is None:
            grid[-1][column] = "?" if ascii_only else "·"
        else:
            scale = max(abs(low), abs(high), 1.)
            position = (height - 1) // 2 if high == low else round(
                (high / scale - value / scale) / (high / scale - low / scale) * (height - 1))
            grid[position][column] = "*" if ascii_only else "●"
    return Chart(tuple("".join(row) for row in grid), low, high, len(samples), samples.count(None))


def cells(value):
    return sum(0 if unicodedata.combining(ch) else 2 if unicodedata.east_asian_width(ch) in {"W", "F"} else 1
               for ch in clean(value))


def _wrap(value, width):
    """Wrap by display cells so authorized long records remain readable."""
    value = clean(value)
    result, part, occupied = [], [], 0
    for character in value:
        size = cells(character)
        if size > width:
            character, size = "?", 1
        if occupied + size > width:
            result.append("".join(part))
            part, occupied = [], 0
        part.append(character)
        occupied += size
    if part:
        result.append("".join(part))
    return result or [""]


class _Canvas:
    def __init__(self, width, height, ascii_only):
        self.width, self.height, self.ascii_only = width, height, ascii_only
        self.characters = [[" " for _ in range(width)] for _ in range(height)]
        self.roles = [["plain" for _ in range(width)] for _ in range(height)]

    def put(self, x, y, text, role="plain", limit=None):
        if not 0 <= y < self.height or x < 0 or x >= self.width:
            return
        if self.ascii_only:
            # Represent unsupported characters without changing backend records.
            text = clean(text).encode("ascii", "backslashreplace").decode("ascii")
        text = clip(text, min(self.width - x, self.width - x if limit is None else max(0, limit)))
        column = x
        for character in text:
            size = cells(character)
            if size == 0:
                if column > x:
                    previous = column - 1
                    if self.characters[y][previous] == "" and previous > 0:
                        previous -= 1
                    self.characters[y][previous] += character
                continue
            # Replacing either cell of a wide glyph must remove the whole glyph.
            for target in range(column, column + size):
                old = self.characters[y][target]
                if old == "" and target > 0:
                    self.characters[y][target - 1] = " "
                elif cells(old) == 2 and target + 1 < self.width:
                    self.characters[y][target + 1] = " "
            self.characters[y][column] = character
            self.roles[y][column] = role
            if size == 2:
                self.characters[y][column + 1] = ""
                self.roles[y][column + 1] = role
            column += size

    def box(self, x, y, width, height, title, *, subtitle=None):
        if width < 4 or height < 3:
            return
        tl, tr, bl, br, horizontal, vertical = ("+", "+", "+", "+", "-", "|") if self.ascii_only else ("╭", "╮", "╰", "╯", "─", "│")
        self.put(x, y, tl + horizontal * (width - 2) + tr, "border")
        self.put(x, y + height - 1, bl + horizontal * (width - 2) + br, "border")
        for row in range(y + 1, y + height - 1):
            self.put(x, row, vertical, "border")
            self.put(x + width - 1, row, vertical, "border")
        self.put(x + 2, y, " " + title + " ", "title", width - 4)
        if subtitle:
            self.put(x + 2, y + height - 1, " " + subtitle + " ", "muted", width - 4)

    def frame(self):
        lines, spans = [], []
        for row, (characters, roles) in enumerate(zip(self.characters, self.roles)):
            lines.append("".join(characters).rstrip())
            visible_width = cells(lines[-1])
            start = 0
            while start < visible_width:
                end = start + 1
                while end < visible_width and roles[end] == roles[start]:
                    end += 1
                text = "".join(characters[start:end])
                if roles[start] != "plain" and text.strip():
                    spans.append((row, start, text, roles[start]))
                start = end
        return DashboardFrame(tuple(lines), tuple(spans))


def _status_role(value):
    value = clean(value).lower()
    if value in {"up", "available", "ready", "active", "success", "transmitting", "completed", "consumed"}:
        return "good"
    if any(word in value for word in ("down", "fault", "fail", "blocked", "isolated", "error", "invalid", "no_input")):
        return "bad"
    return "warn"


def _mixed(state):
    return (state._paused_mixed if state.paused else state.mixed) or {}


def _records(state):
    if state.view == "overview":
        return [(str(row.get("link_id", "unknown")), row) for row in state.visible.get("live", {}).get("links", [])]
    return state.records()


def _selected(state, records):
    key = "links" if state.view == "overview" else state.view
    selected = state.selections.get(key)
    return next(((identifier, row) for identifier, row in records if identifier == selected), records[0] if records else (None, None))


def short_identifier(identifier, width, *, ascii_only=False):
    """Display abbreviation only: retain numbered suffixes, never change IDs."""
    identifier = clean(identifier)
    if cells(identifier) <= width:
        return identifier
    suffix = clip(identifier[-4:], min(4, max(1, width // 3)))
    prefix = clip(identifier, max(0, width - cells(suffix) - 1))
    return prefix + ("~" if ascii_only else "…") + suffix


def _cards(canvas, state, y, width):
    live = state.visible.get("live", {})
    mixed = _mixed(state)
    if state.view == "mixed":
        cards = [("OBSERVED NODES", str(len(mixed.get("nodes", {}))), "controller readback"),
                 ("RESERVATIONS", str(len(mixed.get("reservations", []))), "slots; no inferred keys"),
                 ("CLASSICAL / QUANTUM", str(len((mixed.get("configuration") or {}).get("links", []))), "paired channel records")]
    else:
        links = live.get("links", [])
        states = {name: sum(row.get("status") == name for row in links) for name in ("up", "degraded", "down")}
        cards = [("NODES", str(len(live.get("nodes", []))), "registered in publication"),
                 ("LINKS", str(len(links)), f"UP {states['up']} DEG {states['degraded']} DOWN {states['down']}"),
                 ("SATELLITES", str(len(live.get("satellites", {}))), "received / predicted scoped"),
                 ("NETWORK CLOCK", metric(live.get("network_time_s"), unit="s"), "backend tick " + clean(live.get("tick", "no input")))]
    if (not state.visible and state.view != "mixed") or (state.view == "mixed" and not mixed):
        cards = [(title, "no input", "waiting for backend") for title, _, _ in cards]
    count = len(cards)
    for index, (title, value, hint) in enumerate(cards):
        x = index * width // count
        end = (index + 1) * width // count
        canvas.box(x, y, end - x, 5, title)
        canvas.put(x + 2, y + 1, value, "accent", end - x - 4)
        canvas.put(x + 2, y + 2, hint, "muted", end - x - 4)


def _table(canvas, state, records, x, y, width, height):
    label = "NETWORK LINKS" if state.view == "overview" else state.view.upper()
    selected, _ = _selected(state, records)
    title = label + f"  {len(records)}" + (" / STALE" if state.view == "reservations" and state._mixed_stale else "")
    available = max(0, height - 4)
    identifiers = [key for key, _ in records]
    index = identifiers.index(selected) if selected in identifiers else 0
    offset = max(0, index - available + 1)
    canvas.box(x, y, width, height, title, subtitle=f"{offset + 1 if records else 0}-{min(len(records), offset + available)} / {len(records)}")
    table_width = width - 4
    id_width = min(22, max(8, table_width // 3))
    heading = "COMPONENT".ljust(id_width + 2) + "STATE / OBSERVATIONS"
    canvas.put(x + 2, y + 1, heading, "muted", table_width)
    if not records:
        canvas.put(x + 2, y + 3, "No backend records / no input", "muted", table_width)
    for position, (identifier, row) in enumerate(records[offset:offset + available]):
        line = y + 2 + position
        is_selected = identifier == selected
        prefix = "> " if is_selected else "  "
        role = "selected" if is_selected else "plain"
        canvas.put(x + 1, line, " " * (width - 2), role)
        displayed = short_identifier(identifier, id_width, ascii_only=canvas.ascii_only)
        canvas.put(x + 2, line, prefix + displayed + " " * (id_width - cells(displayed)), role, table_width)
        status = row.get("status", "not published")
        if state.view == "satellites":
            status = ("HK valid" if row.get("housekeeping_valid") is True else "HK absent")
            tail = f"{status} | Tx {len(row.get('transmitting_links', []))}"
        elif state.view == "mixed":
            tail = f"{status} | tags {row.get('time_tag_count', 'no input')} | lost {row.get('lost_events', 'no input')}"
        elif state.view == "audit":
            event = row.get("record", row)
            tail = str(event.get("message", event.get("action", event.get("kind", event.get("verb", "backend event")))))
        else:
            tail = str(status)
            if state.view in {"overview", "links"}:
                tail += f" | F {metric(row.get('fidelity'))}"
            elif state.view == "nodes":
                tail += f" | {len(row.get('links', []))} links"
            else:
                tail += " | " + str(row.get("reason", row.get("operation", "")))
        start = x + 5 + id_width
        canvas.put(start, line, tail, role if is_selected else _status_role(status), x + width - 2 - start)


def _chart(canvas, x, y, width, height, label, values, *, times=None, unit="", percent=False,
           end_s=None, bounds=None):
    axis_width = min(10, max(4, width // 5))
    plot_x, plot_width = x + axis_width + 1, max(1, width - axis_width - 1)
    plot_height = max(1, height - 4)
    chart = time_chart(values, plot_width, plot_height, times=times, end_s=end_s,
                       bounds=bounds, ascii_only=canvas.ascii_only)
    latest = values[-1] if values else None
    if chart.mode == "time":
        visible = [value for at, value in zip(times, values) if 0 <= chart.end_s - number(at) <= 60.]
        latest = visible[-1] if visible else None
    canvas.put(x, y, f"{label}  {metric(latest, percent=percent, unit=unit)}", "title", width)
    vertical, corner, horizontal = ("|", "+", "-") if canvas.ascii_only else ("│", "└", "─")
    for index, line in enumerate(chart.lines):
        if index == 0:
            value = chart.high
        elif index == len(chart.lines) - 1:
            value = chart.low
        else:
            value = None
        axis = (f"{value * 100:.4g}%" if percent else metric(value)) if value is not None else ""
        canvas.put(x, y + 1 + index, axis.rjust(axis_width), "muted", axis_width)
        canvas.put(plot_x - 1, y + 1 + index, vertical, "muted", 1)
        canvas.put(plot_x, y + 1 + index, line, "chart", plot_width)
    if chart.plotted_count == 0:
        canvas.put(plot_x, y + 1, "no input", "muted", plot_width)
    if height >= 5:
        if plot_height == 1 and chart.low is not None:
            low = f"{chart.low * 100:.4g}%" if percent else metric(chart.low)
            canvas.put(x, y + 1 + plot_height, low.rjust(axis_width), "muted", axis_width)
        canvas.put(plot_x - 1, y + 1 + plot_height, corner + horizontal * plot_width, "muted", plot_width + 1)
        left = "60s" if chart.mode == "time" else "older samples"
        right = "0s" if chart.mode == "time" else "latest"
        canvas.put(plot_x, y + height - 2, left, "muted", plot_width)
        canvas.put(plot_x + max(0, plot_width - len(right)), y + height - 2, right, "muted", len(right))
    details = [f"{chart.sample_count} samples", f"gaps {chart.missing_count}"]
    if chart.invalid_count:
        details.append(f"invalid {chart.invalid_count}")
    if chart.collisions:
        details.append(f"overlap {chart.collisions}")
    if chart.mode == "time":
        details.append(f"t {metric(chart.first_sample_s)}..{metric(chart.last_sample_s)}s")
    else:
        details.insert(1, "sample order")
    canvas.put(x, y + height - 1, " | ".join(details), "muted", width)


def _link_charts(canvas, row, x, y, width, height, *, end_s=None):
    history = row.get("history", {})
    height_per = min(12, max(3, height // 3))
    used = y
    for index, (key, label, unit, percent, bounds) in enumerate((("fidelity", "FIDELITY", "", True, (0., 1.)),
                                                               ("qber", "QBER", "", True, (0., 1.)),
                                                               ("skr_bps", "SECRET KEY RATE", "bit/s", False, (0., None)))):
        position = y + index * height_per
        if position + height_per > y + height:
            break
        _chart(canvas, x, position, width, height_per, label, history.get(key, []),
               times=history.get("t_s", []), unit=unit, percent=percent, end_s=end_s, bounds=bounds)
        used = position + height_per
    return used


def _protocol_charts(canvas, state, identifier, row, x, y, width, bottom):
    provenance = row.get("provenance")
    technology = provenance.get("technology") if isinstance(provenance, dict) else None
    session = technology.get("session_id") if isinstance(technology, dict) else None
    for series in state.visible.get("live", {}).get("technologies", {}).get("protocol_series", []):
        if identifier not in series.get("node_ids", []) and identifier != series.get("session_id") and (session is None or session != series.get("session_id")):
            continue
        if bottom - y < 5:
            break
        samples = series.get("samples", [])
        canvas.put(x, y, "INPUT " + clean(series.get("origin", "not published")) + " / " + clean(series.get("source_id", "not published")),
                   "muted", width)
        y += 1
        raw_bounds = series.get("bounds")
        bounds = tuple(raw_bounds) if isinstance(raw_bounds, (list, tuple)) and len(raw_bounds) == 2 and all(
            value is None or number(value) is not None for value in raw_bounds) and not (
                number(raw_bounds[0]) is not None and number(raw_bounds[1]) is not None
                and raw_bounds[0] > raw_bounds[1]) else None
        chart_height = min(8, bottom - y)
        _chart(canvas, x, y, width, chart_height,
               clean(series.get("technology", "protocol")) + " / " + clean(series.get("metric", "metric")),
               [sample.get("value") for sample in samples],
               times=[sample.get("at_s", sample.get("t_s")) for sample in samples],
               unit=clean(series.get("unit", "")), bounds=bounds,
               percent=bounds == (0., 1.) and series.get("unit") == "1")
        # at_s belongs to the protocol clock. Never align it with network_time_s.
        y += chart_height
    return y


def _inspector(canvas, state, records, x, y, width, height):
    identifier, row = _selected(state, records)
    canvas.box(x, y, width, height, "INSPECTOR" + (" / " + identifier if identifier else ""),
               subtitle="Enter: complete record / scroll" if state.view != "overview" else "2: browse links / Enter: details")
    x, y, width, height = x + 2, y + 1, width - 4, height - 2
    if row is None:
        canvas.put(x, y, "No selected backend record / no input", "muted", width)
        return
    if state.view in {"overview", "links"}:
        canvas.put(x, y, f"{row.get('a')} -> {row.get('b')} | {row.get('status')}", _status_role(row.get("status")), width)
        used = _link_charts(canvas, row, x, y + 1, width, height - 2,
                            end_s=number(state.visible.get("live", {}).get("network_time_s")))
        reasons = row.get("provenance", {}).get("status_reasons", [])
        if reasons and used < y + height:
            canvas.put(x, used, "; ".join(reasons), "warn", width)
            used += 1
        _protocol_charts(canvas, state, identifier, row, x, used, width, y + height)
        return
    live = state.visible.get("live", {})
    if state.view == "nodes":
        canvas.put(x, y, f"{row.get('status', 'not published')} | {row.get('reason', '')}", _status_role(row.get("status")), width)
        link = next((item for item in live.get("links", []) if identifier in {item.get("a"), item.get("b")}), None)
        if link and height >= 12:
            canvas.put(x, y + 1, "LINK " + str(link.get("link_id")), "muted", width)
            used = _link_charts(canvas, link, x, y + 2, width, height - 3,
                                end_s=number(live.get("network_time_s")))
            _protocol_charts(canvas, state, identifier, row, x, used, width, y + height)
            return
        if _protocol_charts(canvas, state, identifier, row, x, y + 1, width, y + height) > y + 1:
            return
    if state.view == "satellites":
        lines = ["HOUSEKEEPING / SCIENCE",
                 "HK received: " + str(row.get("housekeeping_valid", "not published")),
                 "Position: " + str(row.get("position_scope", "no input")),
                 f"Latitude {metric(row.get('sat_latitude'))} | longitude {metric(row.get('sat_longitude'))}",
                 f"Transmitting links: {len(row.get('transmitting_links', []))}"]
        for bridge in row.get("bridges", [])[:4]:
            lines += [f"{bridge.get('session_id')} | {bridge.get('status')} | {bridge.get('origin', 'not published')}"]
            metrics = bridge.get("metrics")
            metrics = metrics if isinstance(metrics, dict) else {}
            lines += [f"QBER {metric(metrics.get('qber'), percent=True)} | key estimate {metric(metrics.get('conditional_key_estimate_bps'), unit='bit/s')}"]
        housekeeping = row.get("housekeeping")
        if isinstance(housekeeping, dict):
            lines += bounded_json(housekeeping, budget=6000)
    else:
        lines = bounded_json(row, budget=6000)
    wrapped = [part for line in lines for part in _wrap(line, width)]
    for index, line in enumerate(wrapped[:height - 1]):
        canvas.put(x, y + index, line, "plain", width)


def _art_panel(canvas, x, y, width, height):
    art = choose_art(width, height, ascii_only=canvas.ascii_only)
    if art is None:
        return
    x, y = x + (width - art.width) // 2, y + (height - art.height) // 2
    for index, line in enumerate(art.lines):
        canvas.put(x, y + index, line, "plain", art.width)
    for row, column, text, role in brand_art_spans(art):
        canvas.put(x + column, y + row, text, role)


def _full_record(canvas, state, x, y, width, height):
    title = "KEYBOARD HELP" if state.help else "RAW PUBLICATION" if state.view == "raw" else "DETAILS / " + str(state.selections.get(state.view, "absent"))
    content = [part for line in _body(state, max(1, width - 4)) for part in _wrap(line, max(1, width - 4))]
    available = max(1, height - 2)
    if not state.help:
        state.scroll_limit = max(0, len(content) - available)
    offset = 0 if state.help else min(state.scroll, state.scroll_limit)
    if not state.help:
        state.scroll = offset
    canvas.box(x, y, width, height, title, subtitle=f"{offset + 1}-{min(len(content), offset + available)} / {len(content)} lines | arrows scroll")
    for index, line in enumerate(content[offset:offset + available]):
        canvas.put(x + 2, y + 1 + index, line, "plain", width - 4)
    if state.help and len(content) < available:
        art_y = y + 2 + len(content)
        _art_panel(canvas, x + 2, art_y, width - 4, y + height - 1 - art_y)


def dashboard_key(state, key):
    """Overview navigates its displayed links, sharing selection with Links."""
    if state.display.prompt:
        return state.key(key)
    if state.view == "overview" and key in {"up", "down", "enter"} and not state.help:
        state.view = "links"
        result = state.key(key)
        if key != "enter":
            state.view = "overview"
        return result
    return state.key(key)


def _compact_history(canvas, state, records, y, width):
    identifier, selected = _selected(state, records)
    live = state.visible.get("live", {})
    row = selected if state.view in {"overview", "links"} else next(
        (link for link in live.get("links", []) if identifier in {link.get("a"), link.get("b")}), None)
    if row is None:
        return
    canvas.put(1, y, "HISTORY " + str(row.get("link_id")) + " / sample order; gaps preserved", "muted", width - 2)
    history = row.get("history", {})
    for index, (name, label) in enumerate((("fidelity", "F"), ("qber", "QBER"), ("skr_bps", "KEY"))):
        x, end = index * width // 3, (index + 1) * width // 3
        canvas.put(x + 1, y + 1, label + " ", "title", end - x - 2)
        canvas.put(x + len(label) + 2, y + 1,
                   sparkline(history.get(name, []), max(1, end - x - len(label) - 3), unicode=not canvas.ascii_only),
                   "chart", max(0, end - x - len(label) - 3))


def _events(canvas, state, y, width, height):
    canvas.box(0, y, width, height, "EVENT JOURNAL / BACKEND", subtitle="Read-only observer; protocol processing continues independently")
    events = state.visible.get("live", {}).get("events", [])
    available = max(1, height - 2)
    if not events:
        canvas.put(2, y + 1, "No published events", "muted", width - 4)
    for index, row in enumerate(events[-available:]):
        stamp = row.get("at_utc", row.get("network_time_s", row.get("t_s", row.get("timestamp", ""))))
        if not stamp and row.get("tick") is not None:
            stamp = "tick " + clean(row["tick"])
        message = row.get("message", row.get("reason", row.get("kind", row.get("type", "backend event"))))
        subject = row.get("subject", row.get("node_id", row.get("link_id", "")))
        canvas.put(2, y + 1 + index, f"{stamp} {subject} {message}", "muted", width - 4)


def render_dashboard(state, *, columns=100, rows=30, now=None, ascii_only=False):
    """Responsive dashboard with a narrow-terminal/plain fallback and safe spans."""
    if state.display.prompt:
        return DashboardFrame(*render_display(state.display, columns=columns, rows=rows, ascii_only=ascii_only))
    columns, rows = max(1, int(columns)), max(1, int(rows))
    # Reserve curses' last cell; bound accidental huge terminal geometries.
    width, height = min(512, max(1, columns - 1)), min(200, rows)
    if width < 64 or height < 18:
        lines, spans = render_frame(state, columns=width, rows=height, now=now, ascii_only=ascii_only)
        return DashboardFrame(lines, spans)
    state.selected()
    canvas = _Canvas(width, height, ascii_only)
    identity = {} if state.view == "mixed" else state.visible.get("security", {})
    age = "no sample" if state.received_at is None else f"{max(0, (time.monotonic() if now is None else now) - state.received_at):.1f}s"
    status = "STALE / connection unavailable" if state.stale else "authenticated backend"
    prefix = "PAUSED / cached presentation | " if state.paused else ""
    header = [f"OrionQ TERMINAL | {state.view.upper()} | {prefix}{status}",
              f"Peer: {identity.get('subject', 'identity not published by mixed status' if state.view == 'mixed' else 'not authenticated')} | received age {age} | physical locked",
              "1 overview 2 links 3 nodes 4 satellites 5 reservations 6 audit 7 raw 8 mixed"]
    # Identity, status and navigation keep their original three header rows.
    header = header_brand(header, width, height, ascii_only=ascii_only)
    for row, line in enumerate(header):
        canvas.put(0, row, line, "muted" if row == 1 else "title")
    if state.stale:
        canvas.put(0, 0, header[0], "bad")
    elif state.paused:
        canvas.put(0, 0, header[0], "warn")
    for row, column, text, role in brand_spans(header):
        canvas.put(column, row, text, role)
    if state.display.active:
        canvas.put(0, 3, CONNECTED + " | cosmetic display", "accent", width)
    active = str(("overview", "links", "nodes", "satellites", "reservations", "audit", "raw", "mixed").index(state.view) + 1) + " " + state.view
    if active in header[2]:
        canvas.put(header[2].index(active), 2, active, "selected")
    if not state.help:
        _cards(canvas, state, 4, width)
    main_y = 4 if state.help else 10
    journal_height = 5 if height >= 36 and state.view != "mixed" and not state.help else 0
    main_height = height - main_y - 3 - journal_height
    if state.help or state.details or state.view == "raw":
        _full_record(canvas, state, 0, main_y, width, main_height)
    elif not state.visible and state.view != "mixed":
        canvas.box(0, main_y, width, main_height, "AUTHENTICATED CORE / WAITING")
        waiting = _wrap("Waiting for an authenticated backend snapshot. No local fallback or generated values.", width - 4)
        for index, line in enumerate(waiting[:max(0, main_height - 3)]):
            canvas.put(2, main_y + 1 + index, line, "muted", width - 4)
        art_y = main_y + 2 + len(waiting)
        _art_panel(canvas, 2, art_y, width - 4, main_y + main_height - 1 - art_y)
    else:
        records = _records(state)
        if width >= 112 and main_height >= 9:
            split = width * 46 // 100
            _table(canvas, state, records, 0, main_y, split, main_height)
            _inspector(canvas, state, records, split + 1, main_y, width - split - 1, main_height)
        else:
            compact = state.view in {"overview", "links", "nodes"} and main_height >= 8
            _table(canvas, state, records, 0, main_y, width, main_height - (3 if compact else 0))
            if compact:
                _compact_history(canvas, state, records, main_y + main_height - 2, width)
    if journal_height:
        _events(canvas, state, height - 3 - journal_height, width, journal_height)
    live = state.visible.get("live", {})
    source = (live.get("source") or {})
    age = "no sample" if state.received_at is None else f"{max(0, (time.monotonic() if now is None else now) - state.received_at):.1f}s"
    qualification = "observed slots; no inferred F/QBER" if state.view == "mixed" else source.get("label", source.get("kind", "no input"))
    pending = "Static metadata pending refresh | " if state.force_static and state.view != "mixed" else ""
    canvas.put(0, height - 2, f"{pending}SOURCE {qualification} | age {age} | physical LOCKED", "muted", width)
    canvas.put(0, height - 1, footer_text(state.display, dashboard=True), "title", width)
    return canvas.frame()


def style_attributes(curses, *, eva_mode=False):
    """Semantic terminal palette; no palette redefinition or ANSI injection."""
    if eva_mode:
        from .terminal_effect import eva_attributes
        return eva_attributes(curses)
    bold = curses.A_BOLD
    reverse = getattr(curses, "A_REVERSE", 0)
    roles = {name: 0 for name in ("plain", "border", "muted", "chart")}
    roles.update({name: bold for name in ("title", "accent", "wordmark", "good", "warn", "bad")})
    roles["selected"] = bold | reverse
    if "NO_COLOR" in os.environ or os.environ.get("TERM") == "dumb":
        return roles
    try:
        if not curses.has_colors():
            return roles
        curses.start_color()
        background = getattr(curses, "COLOR_BLACK", 0)
        try:
            curses.use_default_colors()
            background = -1
        except curses.error:
            pass
        colors = getattr(curses, "COLORS", 0)
        palette = (("border", 61, "COLOR_BLUE"), ("title", 159, "COLOR_CYAN"),
                   ("muted", 245, "COLOR_WHITE"), ("good", 114, "COLOR_GREEN"),
                   ("warn", 215, "COLOR_YELLOW"), ("bad", 203, "COLOR_RED"),
                   ("chart", 170, "COLOR_MAGENTA"), ("accent", 173, "COLOR_RED"))
        for pair, (role, indexed, basic) in enumerate(palette, start=1):
            if pair >= getattr(curses, "COLOR_PAIRS", 64):
                break
            foreground = indexed if colors >= 256 else getattr(curses, basic, 0)
            curses.init_pair(pair, foreground, background)
            roles[role] |= curses.color_pair(pair)
        roles["selected"] |= roles["title"]
    except curses.error:
        pass
    return roles
