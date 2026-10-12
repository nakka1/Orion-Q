"""Read-only, source-faithful terminal presentation of authenticated core data.

Only GET requests run on the single bounded worker. Rendering does not advance
the engine, resample histories or derive missing quantum results. curses is
imported only for an interactive POSIX terminal; finite reports work headless.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import math
import os
from shutil import get_terminal_size
import sys
import textwrap
from threading import Condition, Thread
import time
import unicodedata

from .terminal_brand import brand_art_spans, brand_spans, choose_art, color_attributes, header_brand, requires_ascii
from .terminal_effect import CONNECTED, TerminalDisplay, footer_text, render_display


VIEWS = ("overview", "links", "nodes", "satellites", "reservations", "audit", "raw", "mixed")
STATIC_FIELDS = ("model", "summary", "evidence_lanes")


def clean(value):
    return "".join(character if character.isprintable() else " " for character in str(value))


def clip(value, columns):
    parts, occupied = [], 0
    for character in clean(value):
        width = 0 if unicodedata.combining(character) else 2 if unicodedata.east_asian_width(character) in {"W", "F"} else 1
        if occupied + width > columns:
            break
        parts.append(character)
        occupied += width
    return "".join(parts)


def number(value):
    try:
        return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) else None
    except OverflowError:
        return None


def metric(value, *, percent=False, unit=""):
    value = number(value)
    if value is None:
        return "no input"
    return f"{value * 100 if percent else value:.5g}" + (" %" if percent else (" " + unit if unit else ""))


def sparkline(values, width, *, unicode=True):
    """One glyph per actual backend sample, preserving gaps and sample order."""
    values = list(values)[-max(1, width):]
    valid = [number(value) for value in values if number(value) is not None]
    if not valid:
        return "no input"
    low, high = min(valid), max(valid)
    glyphs = "▁▂▃▄▅▆▇█" if unicode else ".:-=+*#@"
    return "".join(
        ("·" if unicode else "?") if number(value) is None else
        glyphs[0 if high == low else min(len(glyphs) - 1, int((number(value) - low) / (high - low) * (len(glyphs) - 1)))]
        for value in values)


def bounded_json(value, *, budget=16000):
    result, remaining = [], budget
    for chunk in json.JSONEncoder(indent=2, sort_keys=True, allow_nan=False).iterencode(value):
        result.append(chunk[:remaining])
        remaining -= len(chunk)
        if remaining <= 0:
            result.append("\n[bounded display; use evidence for the full authorized record]")
            break
    return "".join(result).splitlines()


def _objects(value):
    return isinstance(value, list) and all(isinstance(row, dict) for row in value)


def _endpoints_valid(row):
    return all(row.get(name) is None or isinstance(row[name], str) for name in ("a", "b"))


def valid_mixed(value):
    if not (isinstance(value, dict) and value.get("schema") == "orion.mixed-controller.state.v1"
            and value.get("physical_action_performed") is False
            and isinstance(value.get("nodes"), dict)
            and all(isinstance(row, dict) for row in value["nodes"].values())
            and _objects(value.get("reservations", []))):
        return False
    configuration = value.get("configuration")
    return (configuration is None or isinstance(configuration, dict)
            and _objects(configuration.get("nodes", [])) and _objects(configuration.get("links", []))
            and all(_endpoints_valid(row) for row in configuration.get("links", [])))


def valid_console_records(raw, live):
    """Validate the nested shapes used by every view before replacing a frame."""
    if (not all(_objects(live.get(name, [])) for name in ("links", "nodes", "events"))
            or not _objects(raw.get("commands", []))
            or any(not isinstance(row.get("record", {}), dict) for row in raw.get("commands", []))
            or not all(isinstance(live.get(name, {}), dict) for name in ("policy", "source", "technologies"))
            or any(not isinstance(row.get("links", []), list) for row in live.get("nodes", []))):
        return False
    for row in live.get("links", []):
        history, provenance = row.get("history", {}), row.get("provenance", {})
        if (not _endpoints_valid(row) or not isinstance(history, dict) or not isinstance(provenance, dict)
                or not all(isinstance(history.get(name, []), list) for name in ("t_s", "fidelity", "qber", "skr_bps"))
                or not isinstance(provenance.get("status_reasons", []), list)
                or any(not isinstance(reason, str) for reason in provenance.get("status_reasons", []))):
            return False
        technology = provenance.get("technology")
        if technology is not None and not isinstance(technology, dict):
            return False
    series = live.get("technologies", {}).get("protocol_series", [])
    if (not _objects(series) or any(not _objects(row.get("samples", []))
                                 or not isinstance(row.get("node_ids", []), list)
                                 or any(not isinstance(node, str) for node in row.get("node_ids", [])) for row in series)):
        return False
    satellites = live.get("satellites", {})
    return (isinstance(satellites, dict) and all(isinstance(row, dict)
            and _objects(row.get("bridges", [])) and isinstance(row.get("transmitting_links", []), list)
            for row in satellites.values()))


@dataclass(frozen=True)
class Publication:
    snapshot: dict | None
    mixed: dict | None
    error: bool
    mixed_unavailable: bool
    include_static: bool
    epoch: int
    received_at: float
    mixed_only: bool = False


class SnapshotPump:
    """One request or result slot; no growing queue and no mutation methods."""
    def __init__(self, client):
        self.client = client
        self._condition = Condition()
        self._request = self._result = None
        self._busy = self._closed = False
        self._thread = Thread(target=self._work, name="orion-terminal-read", daemon=True)
        self._thread.start()

    def request(self, *, include_static, reservations, epoch=0, mixed_only=False):
        with self._condition:
            if self._closed or self._busy or self._request is not None or self._result is not None:
                return False
            self._request = (include_static, reservations, epoch, mixed_only)
            self._condition.notify()
            return True

    def take(self):
        with self._condition:
            result, self._result = self._result, None
            return result

    def _work(self):
        while True:
            with self._condition:
                self._condition.wait_for(lambda: self._closed or self._request is not None)
                if self._closed:
                    return
                include_static, reservations, epoch, mixed_only = self._request
                self._request, self._busy = None, True
            raw = mixed = None
            error = mixed_unavailable = False
            try:
                if not mixed_only:
                    raw = self.client.console_snapshot(include_static=include_static)
            except Exception:
                # Exception text and HTTP error bodies are never rendered/logged.
                error = True
            with self._condition:
                if self._closed:
                    self._busy = False
                    return
            if (reservations or mixed_only) and not error:
                try:
                    mixed = self.client.mixed_status()
                except Exception:
                    mixed_unavailable = True
                    error = mixed_only
            result = Publication(raw, mixed, error, mixed_unavailable, include_static, epoch, time.monotonic(), mixed_only)
            with self._condition:
                self._busy = False
                if self._closed:
                    return
                self._result = result

    def close(self, *, timeout_s=.1):
        with self._condition:
            self._closed = True
            self._request = self._result = None
            self._condition.notify()
        self._thread.join(timeout_s)
        # A pending bounded GET may finish later, but cannot publish or start
        # another request. Daemon lifetime never holds terminal exit hostage.
        return not self._thread.is_alive()


class TerminalState:
    def __init__(self, *, view="overview", selected=None):
        if view not in VIEWS:
            raise ValueError("unknown terminal view")
        self.view = view
        self.selections = {view: selected} if selected else {}
        self.snapshot = self.mixed = None
        self._console_stale = self._mixed_stale = True
        self.mixed_unavailable = False
        self._console_received_at = self._mixed_received_at = None
        self.force_static = True
        self.epoch = 0
        self._signature = None
        self._static = {}
        self.paused = self.details = self.help = False
        self.scroll = 0
        self.scroll_limit = None
        self._paused_snapshot = self._paused_mixed = None
        self.display = TerminalDisplay()

    @property
    def stale(self):
        if self.view == "reservations":
            return self._console_stale or self._mixed_stale
        return self._mixed_stale if self.view == "mixed" else self._console_stale

    @property
    def received_at(self):
        if self.view == "reservations":
            if self._console_received_at is None or self._mixed_received_at is None:
                return None
            return min(self._console_received_at, self._mixed_received_at)
        return self._mixed_received_at if self.view == "mixed" else self._console_received_at

    def reload(self):
        self.force_static = True
        self.epoch += 1

    def admit(self, publication):
        raw = publication.snapshot
        if publication.error:
            if publication.mixed_only:
                self._mixed_stale = True
            else:
                self._console_stale = True
            return False
        try:
            if publication.mixed_only:
                mixed = publication.mixed
                if not valid_mixed(mixed):
                    raise ValueError("invalid mixed controller readback")
                self.mixed, self.mixed_unavailable = mixed, False
                self._mixed_received_at, self._mixed_stale = publication.received_at, False
                return True
            health, identity, live = raw["health"], raw["security"], raw["live"]
            if (raw.get("schema") != "orion.console.remote.v1" or health.get("physical_read_only") is not True
                    or health.get("automation_allowed") is not False
                    or identity.get("source") not in {"service", "certificate", "oidc"}
                    or not isinstance(identity.get("subject"), str) or not identity["subject"]
                    or not isinstance(live, dict) or not isinstance(raw.get("topology"), dict)):
                raise ValueError("invalid authenticated operational snapshot")
            if not valid_console_records(raw, live):
                raise ValueError("invalid backend records")
            old = (self.snapshot or {}).get("live", {})
            if (old.get("engine_session_id") is not None and old.get("engine_session_id") == live.get("engine_session_id")
                    and type(old.get("publication_revision")) is int and type(live.get("publication_revision")) is int
                    and live["publication_revision"] < old["publication_revision"]):
                raise ValueError("stale backend publication")
            signature = (live.get("engine_session_id"), live.get("topology_version"),
                         live.get("policy", {}).get("ml_control_revision"), health.get("autopilot_version"),
                         health.get("model_configuration_revision"))
            if self._signature is not None and signature != self._signature:
                self._static.clear()
                self.force_static = True
            self._signature = signature
            if publication.include_static:
                self._static.update({name: raw[name] for name in STATIC_FIELDS if name in raw})
                if publication.epoch == self.epoch:
                    self.force_static = False
            self.snapshot = {**self._static, **raw}
            if publication.mixed is not None or publication.mixed_unavailable:
                self.mixed_unavailable = publication.mixed_unavailable or not valid_mixed(publication.mixed)
                self._mixed_stale = self.mixed_unavailable
                if not self.mixed_unavailable:
                    self.mixed = publication.mixed
                    self._mixed_received_at = publication.received_at
            self._console_received_at, self._console_stale = publication.received_at, False
            return True
        except (KeyError, TypeError, ValueError, AttributeError):
            if publication.mixed_only:
                self._mixed_stale = True
            else:
                self._console_stale = True
            return False

    @property
    def visible(self):
        return (self._paused_snapshot if self.paused else self.snapshot) or {}

    def records(self):
        live = self.visible.get("live", {})
        if self.view in {"links", "nodes"}:
            return [(str(row.get("link_id" if self.view == "links" else "node_id", "unknown")), row)
                    for row in live.get(self.view, [])]
        if self.view == "satellites":
            return sorted(live.get("satellites", {}).items())
        if self.view == "reservations":
            mixed = (self._paused_mixed if self.paused else self.mixed) or {}
            return [(str(row.get("request_id", "unknown")), row) for row in mixed.get("reservations", [])]
        if self.view == "mixed":
            mixed = (self._paused_mixed if self.paused else self.mixed) or {}
            return sorted(mixed.get("nodes", {}).items())
        if self.view == "audit":
            rows = [(str(row.get("event_id", index)), row) for index, row in enumerate(live.get("events", []))]
            rows += [("command:" + str(row.get("record", {}).get("command_id", index)), row)
                     for index, row in enumerate(self.visible.get("commands", []))]
            return rows
        return []

    def selected(self):
        rows = self.records()
        identifier = self.selections.get(self.view)
        if identifier is None and rows:
            self.selections[self.view] = identifier = rows[0][0]
        return next((row for key, row in rows if key == identifier), None)

    def key(self, key):
        if self.display.prompt:
            self.display.key(key)
            return True
        if key in {"l", "L"}:
            self.display.open()
            return True
        if key in {"u", "U"} and self.display.active:
            self.display.unplug()
            return True
        if key in {"q", "Q"}:
            return False
        if len(key) == 1 and key in "12345678":
            self.view, self.details, self.scroll = VIEWS[int(key) - 1], False, 0
            self.scroll_limit = None
        elif key in {"\t", "tab"}:
            self.view, self.details, self.scroll = VIEWS[(VIEWS.index(self.view) + 1) % len(VIEWS)], False, 0
            self.scroll_limit = None
        elif key in {"r", "R"}:
            self.reload()
        elif key in {"h", "?"}:
            self.help = not self.help
        elif key in {"p", " "}:
            self.paused = not self.paused
            self._paused_snapshot = self.snapshot if self.paused else None
            self._paused_mixed = self.mixed if self.paused else None
        elif key in {"enter", "\n", "\r"}:
            self.details, self.scroll = not self.details, 0
            self.scroll_limit = None
        elif key in {"up", "down"}:
            delta = -1 if key == "up" else 1
            if self.details or self.view == "raw":
                self.scroll = max(0, self.scroll + delta)
                if self.scroll_limit is not None:
                    self.scroll = min(self.scroll, self.scroll_limit)
            else:
                rows = self.records()
                if rows:
                    identifiers = [item[0] for item in rows]
                    selected = self.selections.get(self.view)
                    index = identifiers.index(selected) if selected in identifiers else 0
                    self.selections[self.view] = identifiers[(index + delta) % len(identifiers)]
        return True


def _histories(record, width):
    history = record.get("history", {})
    lines = ["Observed history; sample order, not a uniform time grid"]
    for key, label in (("fidelity", "Fidelity"), ("qber", "QBER"), ("skr_bps", "Key bit/s")):
        values = history.get(key, [])
        lines.append(f"{label:10} {sparkline(values, max(1, width - 12))}")
    times = history.get("t_s", [])
    if times:
        lines.append(f"Backend interval: {metric(times[0])} .. {metric(times[-1])} s; {len(times)} samples")
    return lines


def _body(state, width):
    raw, live = state.visible, state.visible.get("live", {})
    if state.help:
        return ["1 Overview  2 Links  3 Nodes  4 Satellites  5 Reservations  6 Audit  7 Raw  8 Mixed",
                "Tab: next view | arrows: select / scroll | Enter: details",
                "p/space: pause presentation (backend continues) | r: refetch static metadata",
                "h/?: help | v: dashboard/plain (TTY) | l: display login | u: unplug display | q: quit | resize: reflow without losing selection",
                "All requests are GETs. Missing samples stay missing. TLS failures retain explicitly stale data."]
    if not raw and state.view != "mixed":
        return ["Waiting for an authenticated backend snapshot. No local fallback or generated values."]
    if state.view == "raw":
        return bounded_json(raw)
    if state.view == "reservations" and state._mixed_stale:
        return ["Mixed status STALE / unavailable; retained reservations are not current."]
    if state.details:
        row = state.selected()
        if row is None:
            return ["Selected component is absent from the latest backend publication."]
        lines = ["Selected: " + str(state.selections.get(state.view))]
        if state.view == "links":
            lines += _histories(row, width)
        if state.view == "nodes":
            for link in live.get("links", []):
                if state.selections.get(state.view) in {link.get("a"), link.get("b")}:
                    lines += [f"Link {link.get('link_id')} | {link.get('status')}"] + _histories(link, width)
        protocol_lines = []
        if state.view in {"nodes", "links", "satellites"}:
            identifier = state.selections.get(state.view)
            link_session = ((row.get("provenance", {}).get("technology") or {}).get("session_id")
                            if state.view == "links" else None)
            for series in live.get("technologies", {}).get("protocol_series", []):
                if (identifier in series.get("node_ids", []) or identifier == series.get("session_id")
                        or link_session is not None and link_session == series.get("session_id")):
                    protocol_lines += [f"{series.get('technology')} {series.get('metric')} [{series.get('unit', '')}] | session {series.get('session_id')}",
                                       sparkline([point.get("value") for point in series.get("samples", [])], width)]
        if state.view == "satellites":
            lines += ["Housekeeping reception valid: " + str(row.get("housekeeping_valid", "not published")),
                      "Position scope: " + str(row.get("position_scope", "not published")),
                      "HOUSEKEEPING / SCIENCE / operational links (backend records)"]
            lines += bounded_json(row.get("housekeeping", {"status": "no received housekeeping"}), budget=6000)
            for bridge in row.get("bridges", []):
                lines += [f"Bridge {bridge.get('session_id')} | {bridge.get('status')} | {bridge.get('reason', '')}",
                          "Science metrics | " + str(bridge.get("origin", "origin not published"))]
                lines += bounded_json(bridge.get("metrics", {"status": "no science input"}), budget=6000)
        lines += protocol_lines
        if state.view == "mixed":
            mixed = (state._paused_mixed if state.paused else state.mixed) or {}
            configuration = mixed.get("configuration") or {}
            identifier = state.selections.get("mixed")
            node = next((item for item in configuration.get("nodes", []) if item.get("node_id") == identifier), None)
            links = [item for item in configuration.get("links", []) if identifier in {item.get("a"), item.get("b")}]
            lines += ["Registered session / clock / capabilities and paired channels (backend configuration)"]
            lines += bounded_json({"node": node, "links": links, "audit_origin_counts": mixed.get("audit_origin_counts", {})})
            lines += ["Observed acquisition state; coincidence counts do not certify entanglement or keys"]
        return lines + bounded_json(row)
    if state.view == "overview":
        source = live.get("source", {})
        lines = [f"Input: {source.get('kind', 'no input')} | {source.get('label', 'source not published')}",
                 f"Backend tick {live.get('tick', '?')} | scientific time {metric(live.get('network_time_s'), unit='s')}",
                 f"Nodes {len(live.get('nodes', []))} | links {len(live.get('links', []))} | satellites {len(live.get('satellites', {}))}",
                 "ML: " + str(live.get("policy", {}).get("ml_control_enabled", "not published")),
                 "Physical actuation LOCKED | keys and estimates retain the backend qualification"]
        for link in live.get("links", [])[:3]:
            lines += [f"{link.get('link_id')} | {link.get('status')} | F={metric(link.get('fidelity'))} QBER={metric(link.get('qber'), percent=True)}"]
            lines += _histories(link, width)
        series = live.get("technologies", {}).get("protocol_series", [])
        for row in series[:8]:
            samples = row.get("samples", [])
            lines += [f"{row.get('session_id')} {row.get('metric')} [{row.get('unit', '')}]",
                      sparkline([point.get("value") for point in samples], width)]
        return lines
    rows = state.records()
    if not rows:
        return ["Reservations unavailable or not published." if state.view == "reservations" else "No backend records for this view."]
    lines = []
    selected = state.selections.get(state.view)
    for identifier, row in rows:
        mark = ">" if identifier == selected else " "
        if state.view == "links":
            reason = row.get("provenance", {}).get("status_reasons", [])
            lines.append(f"{mark} {identifier} {row.get('a')} -> {row.get('b')} {row.get('status')} F:{metric(row.get('fidelity'))} QBER:{metric(row.get('qber'), percent=True)} key:{metric(row.get('skr_bps'), unit='bit/s')} {'; '.join(reason)}")
        elif state.view == "satellites":
            lines.append(f"{mark} {identifier} lat:{metric(row.get('sat_latitude'))} lon:{metric(row.get('sat_longitude'))} HK:{row.get('housekeeping_valid', 'no input')} Tx:{len(row.get('transmitting_links', []))} {row.get('position_scope', 'no received position')}")
        elif state.view == "nodes":
            lines.append(f"{mark} {identifier} {row.get('status', 'not published')} links:{len(row.get('links', []))} {row.get('reason', '')}")
        elif state.view == "mixed":
            lines.append(f"{mark} {identifier} {row.get('status', 'not published')} seq:{row.get('sequence', 'no input')} tags:{row.get('time_tag_count', 'no input')} lost:{row.get('lost_events', 'no input')} resources:{row.get('available', 'no input')}")
        else:
            lines.append(f"{mark} {identifier} " + json.dumps(row, sort_keys=True, allow_nan=False))
    return lines


def render_frame(state, *, columns=100, rows=30, now=None, ascii_only=False):
    """Plain text plus separate branding spans; no backend execution on repaint."""
    if state.display.prompt:
        return render_display(state.display, columns=columns, rows=rows, ascii_only=ascii_only)
    columns, rows = max(1, int(columns)), max(1, int(rows))
    state.selected()
    identity = {} if state.view == "mixed" else state.visible.get("security", {})
    age = "no sample" if state.received_at is None else f"{max(0, (time.monotonic() if now is None else now) - state.received_at):.1f}s"
    status = "STALE / connection unavailable" if state.stale else "authenticated backend"
    prefix = "PAUSED / cached presentation | " if state.paused else ""
    header = [f"OrionQ TERMINAL | {state.view.upper()} | {prefix}{status}",
              f"Peer: {identity.get('subject', 'identity not published by mixed status' if state.view == 'mixed' else 'not authenticated')} | received age {age} | physical locked",
              "1 overview 2 links 3 nodes 4 satellites 5 reservations 6 audit 7 raw 8 mixed"]
    # Full artwork uses only help/waiting space below this operational header.
    header = header_brand(header, max(0, columns - 1), rows, ascii_only=ascii_only)
    if state.display.active:
        header.append(CONNECTED + " | cosmetic display")
    body = _body(state, columns)
    available = max(0, rows - len(header) - 2)
    decorative = state.help or not state.visible and state.view != "mixed"
    if decorative:
        body = [part for line in body for part in textwrap.wrap(
            line, max(1, columns - 1), break_on_hyphens=False)]
    if state.help:
        offset = 0
    elif (not state.details and state.view in {"links", "nodes", "satellites", "reservations", "audit", "mixed"}
          and not (state.view == "reservations" and state._mixed_stale)):
        identifiers = [key for key, _ in state.records()]
        selected = state.selections.get(state.view)
        offset = max(0, identifiers.index(selected) - available + 1) if selected in identifiers else 0
    else:
        state.scroll_limit = max(0, len(body) - max(1, available))
        offset = min(state.scroll, state.scroll_limit)
        state.scroll = offset
    footer = ["Mixed controller: observed state/slot reservations, no inferred F/QBER" if state.view == "mixed" else
              "Static metadata pending refresh" if state.force_static else "Only backend samples; no interpolation or generated results",
              footer_text(state.display)]
    displayed = body[offset:offset + available]
    spans = list(brand_spans(header))
    if state.display.active:
        spans.append((3, 0, header[3], "accent"))
    if decorative and len(displayed) == len(body):
        art = choose_art(max(0, columns - 1), available - len(displayed) - 1,
                         ascii_only=ascii_only)
        if art is not None:
            left = (columns - 1 - art.width) // 2
            top = len(header) + len(displayed) + 1
            displayed = displayed + [""] + [" " * left + line for line in art.lines]
            spans += [(top + row, left + column, text, role)
                      for row, column, text, role in brand_art_spans(art)]
    # ASCII terminals still receive every value: unsupported text is escaped,
    # never transliterated in the admitted snapshot or passed to strict output.
    lines = tuple(clip(line.encode("ascii", "backslashreplace").decode("ascii") if ascii_only else line,
                       columns) for line in (header + displayed + footer)[:rows])
    spans = tuple((row, column, clip(text, max(0, columns - column)), role)
                  for row, column, text, role in spans if row < len(lines) and column < columns)
    return lines, spans


def render(state, *, columns=100, rows=30, now=None, ascii_only=False):
    """Pure responsive presentation; finite text reports contain no ANSI."""
    lines, _spans = render_frame(state, columns=columns, rows=rows, now=now, ascii_only=ascii_only)
    return "\n".join(lines)


def _consume(pump, state):
    publication = pump.take()
    if publication is not None:
        state.admit(publication)
    return publication is not None


def watch(client, *, interval_s=1., frames=None, view="overview", selected=None, stream=None, layout="auto"):
    if (isinstance(interval_s, bool) or not isinstance(interval_s, (int, float))
            or not math.isfinite(interval_s) or not .1 <= interval_s <= 60
            or frames is not None and (type(frames) is not int or not 1 <= frames <= 100000)):
        raise ValueError("bounded terminal cadence and frame count required")
    if layout not in {"auto", "dashboard", "plain"}:
        raise ValueError("unsupported terminal layout")
    stream = sys.stdout if stream is None else stream
    interactive = os.name == "posix" and sys.stdin.isatty() and stream.isatty()
    if not interactive and frames is None:
        raise ValueError("noninteractive watch requires --frames")
    state, pump = TerminalState(view=view, selected=selected), SnapshotPump(client)
    try:
        if interactive:
            import curses
            return curses.wrapper(lambda screen: _interactive(screen, pump, state, interval_s, frames,
                                                             layout="plain" if layout == "plain" else "dashboard"))
        count, next_request = 0, 0.
        while count < frames:
            now = time.monotonic()
            if now >= next_request:
                if pump.request(include_static=state.force_static, reservations=state.view == "reservations", epoch=state.epoch,
                                mixed_only=state.view == "mixed"):
                    next_request = now + interval_s
            if _consume(pump, state):
                size = get_terminal_size((100, 30))
                ascii_only = requires_ascii(getattr(stream, "encoding", None))
                if layout == "dashboard":
                    from .terminal_dashboard import render_dashboard
                    output = render_dashboard(state, columns=size.columns, rows=size.lines,
                                              ascii_only=ascii_only).text()
                else:
                    output = render(state, columns=size.columns, rows=size.lines, ascii_only=ascii_only)
                stream.write(output + "\n\n")
                stream.flush()
                count += 1
            time.sleep(.01)
        return 3 if state.stale else 0
    except KeyboardInterrupt:
        return 130
    finally:
        state.display.shutdown()
        pump.close()


def _paint_frame(screen, frame, attributes, previous, curses):
    """Paint changed rows only; one batched refresh, no recurring full erase."""
    rows, columns = screen.getmaxyx()
    if previous is None:
        screen.erase()
    old_lines = previous.lines if previous else ()
    old_spans = previous.spans if previous else ()
    by_row, old_by_row = {}, {}
    for row, column, value, role in frame.spans:
        by_row.setdefault(row, []).append((column, value, role))
    for row, column, value, role in old_spans:
        old_by_row.setdefault(row, []).append((column, value, role))
    for row in range(min(rows, max(len(old_lines), len(frame.lines)))):
        line = frame.lines[row] if row < len(frame.lines) else ""
        if (previous is not None and row < len(old_lines) and old_lines[row] == line
                and by_row.get(row) == old_by_row.get(row)):
            continue
        try:
            screen.move(row, 0)
            screen.clrtoeol()
            screen.addnstr(row, 0, line, max(1, columns - 1))
            for column, value, role in by_row.get(row, []):
                if column < columns - 1:
                    screen.addnstr(row, column, clip(value, columns - 1 - column),
                                   max(1, len(value)), attributes.get(role, 0))
        except curses.error:
            pass
    screen.noutrefresh()
    curses.doupdate()


def _preserve_terminal_palette(curses):
    # curses.wrapper initializes pair 0 as white-on-black when color is
    # available. NO_COLOR must retain the user's terminal defaults instead.
    if "NO_COLOR" in os.environ or os.environ.get("TERM") == "dumb":
        try:
            curses.use_default_colors()
        except curses.error:
            pass  # A terminal without color support already uses its defaults.


def _configure_escape_delay(curses):
    """Bound isolated Escape latency; respect explicitly configured slow links."""
    if "ESCDELAY" in os.environ:
        return
    setter = getattr(curses, "set_escdelay", None)
    if setter is not None:
        try:
            setter(100)
        except (curses.error, ValueError):
            pass


def _interactive(screen, pump, state, interval_s, frames, *, layout="dashboard"):
    import curses
    from .terminal_dashboard import DashboardFrame, dashboard_key, render_dashboard, style_attributes
    screen.nodelay(True)
    try:
        curses.curs_set(0)
    except curses.error:
        pass
    _configure_escape_delay(curses)
    _preserve_terminal_palette(curses)
    count, next_request, next_paint, running = 0, 0., 0., True
    ascii_only = requires_ascii(getattr(screen, "encoding", None))
    palette = style_attributes if layout == "dashboard" else color_attributes
    attributes = palette(curses, eva_mode=True) if state.display.active else palette(curses)
    previous, geometry, display_mode = None, None, (layout, state.display.active)
    keys = {curses.KEY_UP: "up", curses.KEY_DOWN: "down", curses.KEY_ENTER: "enter", 10: "enter", 13: "enter", 9: "tab",
            ord("j"): "down", ord("k"): "up", curses.KEY_RIGHT: "tab"}
    while running and (frames is None or count < frames):
        now = time.monotonic()
        if now >= next_request and pump.request(include_static=state.force_static, reservations=state.view == "reservations", epoch=state.epoch,
                                               mixed_only=state.view == "mixed"):
            next_request = now + interval_s
        published = _consume(pump, state)
        if published:
            count += 1
        # Typematic keys/bursts must not queue one expensive repaint per key.
        # Drain a bounded batch in order, then present once; polling remains fair.
        input_seen = False
        for _ in range(32):
            code = screen.getch()
            if code == -1:
                break
            input_seen = True
            if code == curses.KEY_RESIZE:
                continue
            if state.display.prompt:
                controls = {10: "enter", 13: "enter", 9: "tab", 27: "escape", 8: "backspace", 127: "backspace",
                            curses.KEY_ENTER: "enter", getattr(curses, "KEY_BACKSPACE", -2): "backspace"}
                raw_key = controls.get(code, chr(code) if 32 <= code <= 126 else "")
                if raw_key:
                    state.display.key(raw_key)
                continue
            key = keys.get(code, chr(code) if 0 <= code <= 255 else "")
            if key == "v":
                layout = "plain" if layout == "dashboard" else "dashboard"
            elif key:
                running = dashboard_key(state, key) if layout == "dashboard" else state.key(key)
            if not running:
                break
        mode = (layout, state.display.active)
        if mode != display_mode:
            palette = style_attributes if layout == "dashboard" else color_attributes
            attributes = palette(curses, eva_mode=True) if state.display.active else palette(curses)
            previous, display_mode = None, mode
        # Apply the theme to ordinary cells as well as semantic spans. Reset it
        # on Unplug; differential painting must not retain the preceding palette.
        for method in ("bkgd", "attrset"):
            setter = getattr(screen, method, None)
            if setter is not None and (previous is None):
                try:
                    if method == "bkgd":
                        setter(" ", attributes.get("plain", 0))
                    else:
                        setter(attributes.get("plain", 0))
                except curses.error:
                    pass
        if now >= next_paint or input_seen or published:
            rows, columns = screen.getmaxyx()
            if geometry != (rows, columns):
                previous, geometry = None, (rows, columns)
            if layout == "dashboard":
                frame = render_dashboard(state, columns=columns, rows=rows, ascii_only=ascii_only)
            else:
                lines, spans = render_frame(state, columns=columns, rows=rows, ascii_only=ascii_only)
                frame = DashboardFrame(lines, spans)
            _paint_frame(screen, frame, attributes, previous, curses)
            previous = frame
            next_paint = now + 1.
        time.sleep(.01)
    return 3 if state.stale else 0
