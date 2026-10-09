"""Terminal verbs and palette entries of the live network.

Every entry here is generated from the snapshot the backend delivered and gated
by the capabilities the active source declares. A verb only *names* an operation
and its subject: it builds the JSON request of a backend verb and hands it to
`ConsoleWindow._command`, the same path the buttons use. Nothing is decided,
computed or simulated here — whether an isolation, a recalibration or a fibre
change happens, and what it does to the routes, is the backend's answer, read
back from the next snapshots (`snap.ledger.commands`, `snap.live`).

    isolate <node>            set_node_isolation {isolated: true}
    restore <node>            set_node_isolation {isolated: false}
    recalibrate <node>        recalibrate_node   (full recalibration by the engine)
    enable <link>             set_link_active {active: true}
    disable <link>            set_link_active {active: false}
    locate <node> <lat> <lon> [place]     set_node_location
    unlocate <node>           set_node_location {latitude: null, longitude: null}
    request_pair <demand>     one real episode on the demand's current route
    purify_pair <demand>      one purification round on the stored pair
    consume_pair <demand>     hand the stored pair to the application
    define_pair <id> <a> <b> [min_fidelity]   ·   remove_pair <id>
    model_archive <qnet|qkd> <path|none>      set_model_archive
    inject <kind> <link> [strength] [duration_s]   demonstration only (simulated INPUT)

`register()` installs them; it replaces the interim `recalibrate` / `isolate`
registrations of `widgets.journal` and `widgets.palette`. For a source that does
not declare the live verbs (the synthetic UI fixture, the mock), `recalibrate`
and `isolate` keep dispatching what that source does declare.
"""
from __future__ import annotations

import json

from .widgets import journal as _journal
from .widgets import palette as _palette
from .widgets.journal import Feedback, TerminalVerb, find_link, find_node, normalise, register_verb
from .widgets.palette import PaletteCommand, register_palette_provider

INJECT_GROUP = "inject"     # sorted after the built-in palette groups


# ------------------------------------------------------------------ helpers
def _allows(snapshot, operation: str) -> bool:
    return snapshot is not None and snapshot.capabilities.allows(operation)


def _bookkeeping(snapshot) -> dict:
    """The two fields every tracked topology request carries, from the snapshot the operator sees."""
    return {"expected_version": snapshot.topology.version, "topology_id": snapshot.topology.id}


def _send(window, verb: str, payload: dict, text: str) -> Feedback:
    window._command(verb, json.dumps(payload, ensure_ascii=False))
    return Feedback(text, "DISPATCHED", "muted")


def find_demand(snapshot, target: str):
    wanted = normalise(target)
    live = getattr(snapshot, "live", None)
    if live is None or not wanted:
        return None
    return next((demand for demand in live.demands if normalise(demand.id) == wanted), None)


def input_event_kinds() -> tuple[str, ...]:
    """Input events the backend's simulated source accepts (its own list, not a copy)."""
    from quantum_autopilot.network_engine.sources import EVENT_KINDS
    return tuple(EVENT_KINDS)


def _number(text: str, name: str) -> float:
    try:
        return float(text)
    except ValueError:
        raise ValueError(f"{name} must be a number, got {text!r}") from None


# -------------------------------------------------------------------- nodes
_interim_recalibrate = _journal._node_action("recalibrate")
_interim_isolate = _journal._node_action("isolate")
_interim_operation = _journal._node_action_operation


def _isolation(isolated: bool):
    def run(window, target):
        snapshot = window.snapshot
        if isolated and not _allows(snapshot, "set_node_isolation"):
            return _interim_isolate(window, target)
        node = find_node(snapshot, target)
        if node is None:
            return Feedback(f"unknown node: {target or '—'}", "ERR", "red")
        return _send(window, "set_node_isolation",
                     {"node_id": node.id, "isolated": isolated, **_bookkeeping(snapshot)},
                     f"{'isolate' if isolated else 'restore'} {node.id}")
    return run


def _isolate_operation(window):
    if _allows(window.snapshot, "set_node_isolation"):
        return "set_node_isolation"
    return _interim_operation("isolate")(window)


def _recalibrate(window, target):
    snapshot = window.snapshot
    if not _allows(snapshot, "recalibrate_node"):
        return _interim_recalibrate(window, target)
    node = find_node(snapshot, target)
    if node is None:
        return Feedback(f"unknown node: {target or '—'}", "ERR", "red")
    return _send(window, "recalibrate_node", {"node_id": node.id, **_bookkeeping(snapshot)},
                 f"recalibrate {node.id} · full recalibration")


def _recalibrate_operation(window):
    if _allows(window.snapshot, "recalibrate_node"):
        return "recalibrate_node"
    return _interim_operation("recalibrate")(window)


def _locate(window, target):
    snapshot = window.snapshot
    parts = target.split()
    if len(parts) < 3:
        return Feedback("usage: locate <node> <lat> <lon> [place]", "ERR", "red")
    node = find_node(snapshot, parts[0])
    if node is None:
        return Feedback(f"unknown node: {parts[0]}", "ERR", "red")
    latitude, longitude = _number(parts[1], "latitude"), _number(parts[2], "longitude")
    return _send(window, "set_node_location",
                 {"node_id": node.id, "latitude": latitude, "longitude": longitude, "place": " ".join(parts[3:]),
                  **_bookkeeping(snapshot)}, f"locate {node.id} {latitude:g} {longitude:g}")


def _unlocate(window, target):
    snapshot = window.snapshot
    node = find_node(snapshot, target)
    if node is None:
        return Feedback(f"unknown node: {target or '—'}", "ERR", "red")
    return _send(window, "set_node_location",
                 {"node_id": node.id, "latitude": None, "longitude": None, "place": "", **_bookkeeping(snapshot)},
                 f"unlocate {node.id}")


# -------------------------------------------------------------------- links
def _link_active(active: bool):
    def run(window, target):
        snapshot = window.snapshot
        link = find_link(snapshot, target)
        if link is None or not link.id:
            return Feedback(f"unknown link: {target or '—'}", "ERR", "red")
        return _send(window, "set_link_active", {"link_id": link.id, "active": active, **_bookkeeping(snapshot)},
                     f"{'enable' if active else 'disable'} {link.id}")
    return run


# ------------------------------------------------------------------ demands
def _pair(verb: str):
    def run(window, target):
        demand = find_demand(window.snapshot, target)
        if demand is None:
            return Feedback(f"unknown demand: {target or '—'}", "ERR", "red")
        return _send(window, verb, {"demand_id": demand.id}, f"{verb} {demand.id}")
    return run


def _define_pair(window, target):
    snapshot = window.snapshot
    parts = target.split()
    if len(parts) not in (3, 4):
        return Feedback("usage: define_pair <id> <a> <b> [min_fidelity]", "ERR", "red")
    ends = [find_node(snapshot, part) for part in parts[1:3]]
    if None in ends:
        return Feedback(f"unknown node: {parts[1] if ends[0] is None else parts[2]}", "ERR", "red")
    payload = {"demand_id": parts[0], "a": ends[0].id, "b": ends[1].id}
    if len(parts) == 4:
        payload["min_fidelity"] = _number(parts[3], "min_fidelity")
    return _send(window, "define_pair", payload, f"define_pair {parts[0]} {ends[0].id} {ends[1].id}")


# ---------------------------------------------------------- models and input
def _model_archive(window, target):
    parts = target.split(None, 1)
    if len(parts) != 2 or parts[0].lower() not in ("qnet", "qkd"):
        return Feedback("usage: model_archive <qnet|qkd> <path|none>", "ERR", "red")
    path = None if parts[1].strip().lower() in ("none", "null", "-") else parts[1].strip()
    return _send(window, "set_model_archive", {"kind": parts[0].lower(), "path": path},
                 f"model_archive {parts[0].lower()} {path or 'none'}")


def _inject(window, target):
    snapshot = window.snapshot
    parts = target.split()
    if not 2 <= len(parts) <= 4:
        return Feedback("usage: inject <kind> <link> [strength] [duration_s]", "ERR", "red")
    kinds = input_event_kinds()
    kind = parts[0].lower()
    if kind not in kinds:
        return Feedback(f"unknown input event: {parts[0]} ({' · '.join(kinds)})", "ERR", "red")
    link = find_link(snapshot, parts[1])
    if link is None or not link.id:
        return Feedback(f"unknown link: {parts[1]}", "ERR", "red")
    payload = {"kind": kind, "link_id": link.id}
    if len(parts) > 2:
        payload["strength"] = _number(parts[2], "strength")
    if len(parts) > 3:
        payload["duration_s"] = _number(parts[3], "duration_s")
    return _send(window, "inject_input_event", payload, f"inject {kind} {link.id}")


def _input_source(window, target):
    kind, _, argument = target.strip().partition(" ")
    payload = {"kind": kind}
    if kind == "measured" and argument.strip():
        payload["path"] = argument.strip().strip('"')
    elif kind == "simulated":
        parts = argument.split()
        if len(parts) > 2:
            return Feedback("input simulated [seed] [scenario]", "ERR", "red")
        try:
            if parts:
                payload["seed"] = int(parts[0])
            if len(parts) == 2:
                payload["scenario"] = parts[1]
        except ValueError:
            return Feedback("input seed must be a nonnegative integer", "ERR", "red")
    elif kind != "none" or argument.strip():
        return Feedback('input measured "capture.json" | input simulated [seed] [scenario] | input none', "ERR", "red")
    return _send(window, "configure_network_input", payload, "Input source requested; wait for backend readback")


LIVE_VERBS = (
    TerminalVerb("input", 'input measured "capture.json" | simulated [seed] [scenario] | none',
                 _input_source, "configure_network_input", summary="Select raw acquisition replay, simulation or no input; no physical transmission"),
    TerminalVerb("isolate", "isolate <node>", _isolation(True), _isolate_operation,
                 summary="Isolate a node (backend takes its fibres out of the routing graph)"),
    TerminalVerb("restore", "restore <node>", _isolation(False), "set_node_isolation",
                 summary="Restore an isolated node"),
    TerminalVerb("recalibrate", "recalibrate <node>", _recalibrate, _recalibrate_operation,
                 summary="Full recalibration of a node, executed by the backend engine"),
    TerminalVerb("enable", "enable <link>", _link_active(True), "set_link_active", summary="Enable a fibre"),
    TerminalVerb("disable", "disable <link>", _link_active(False), "set_link_active", summary="Disable a fibre"),
    TerminalVerb("locate", "locate <node> <lat> <lon> [place]", _locate, "set_node_location",
                 summary="Declare a node's position"),
    TerminalVerb("unlocate", "unlocate <node>", _unlocate, "set_node_location",
                 summary="Return a node to the unlocated queue"),
    TerminalVerb("request_pair", "request_pair <demand>", _pair("request_pair"), "request_pair"),
    TerminalVerb("purify_pair", "purify_pair <demand>", _pair("purify_pair"), "purify_pair"),
    TerminalVerb("consume_pair", "consume_pair <demand>", _pair("consume_pair"), "consume_pair"),
    TerminalVerb("define_pair", "define_pair <id> <a> <b> [min_fidelity]", _define_pair, "define_pair"),
    TerminalVerb("remove_pair", "remove_pair <demand>", _pair("remove_pair"), "remove_pair"),
    TerminalVerb("model_archive", "model_archive <qnet|qkd> <path|none>", _model_archive, "set_model_archive"),
    TerminalVerb("inject", "inject <kind> <link> [strength] [duration_s]", _inject, "inject_input_event",
                 summary="Disturb the simulated INPUT of one fibre (demonstration only)"),
)


# ------------------------------------------------------------------ palette
def live_palette(snapshot):
    """Entries for what the backend state allows right now."""

    if snapshot is None:
        return
    capabilities = snapshot.capabilities
    live = getattr(snapshot, "live", None)
    interim = capabilities.allows("apply_simulated_node_action")
    for node in snapshot.topology.nodes:
        if capabilities.allows("recalibrate_node"):
            yield PaletteCommand(f"Recalibrate {node.id} (full)", "command", "recalibrate", node.id,
                                 "recalibrate_node", keywords="calibration node")
        elif interim or capabilities.allows("recalibrate"):
            yield PaletteCommand(f"Recalibrate {node.id}", "command", "recalibrate", node.id,
                                 "apply_simulated_node_action" if interim else "recalibrate")
        if capabilities.allows("set_node_isolation"):
            if node.isolated:
                yield PaletteCommand(f"Restore {node.id}", "command", "restore", node.id, "set_node_isolation")
            else:
                yield PaletteCommand(f"Isolate {node.id}", "command", "isolate", node.id, "set_node_isolation")
        elif interim and not node.isolated:
            yield PaletteCommand(f"Isolate {node.id}", "command", "isolate", node.id, "apply_simulated_node_action")
    if capabilities.allows("set_link_active"):
        for link in snapshot.topology.links:
            if not link.id or link.configured_active is None:
                continue
            action = "Disable" if link.configured_active else "Enable"
            yield PaletteCommand(f"{action} fibre {link.id}", "command", action.lower(), link.id, "set_link_active",
                                 keywords=f"{link.a} {link.b} link")
    if live is not None:
        for demand in live.demands:
            pair = demand.pair or {}
            if capabilities.allows("request_pair"):
                yield PaletteCommand(f"Request pair {demand.id} ({demand.a} ↔ {demand.b})", "command",
                                     "request_pair", demand.id, "request_pair", keywords="demand entanglement")
            if pair.get("status") == "delivered":
                if capabilities.allows("purify_pair"):
                    yield PaletteCommand(f"Purify pair {demand.id}", "command", "purify_pair", demand.id,
                                         "purify_pair", keywords="demand entanglement")
                if capabilities.allows("consume_pair"):
                    yield PaletteCommand(f"Consume pair {demand.id}", "command", "consume_pair", demand.id,
                                         "consume_pair", keywords="demand entanglement")
    if capabilities.allows("inject_input_event"):
        for link in snapshot.topology.links:
            if not link.id:
                continue
            for kind in input_event_kinds():
                yield PaletteCommand(f"Inject input event {kind} on {link.id}", INJECT_GROUP, "inject",
                                     f"{kind} {link.id}", "inject_input_event",
                                     keywords="simulated input demonstration attack")


def register() -> None:
    """Install the live verbs and palette entries (idempotent)."""

    for verb in LIVE_VERBS:
        register_verb(verb)
    interim = getattr(_palette, "_node_actions", None)
    if interim in _palette.PALETTE_PROVIDERS:
        _palette.PALETTE_PROVIDERS.remove(interim)
    register_palette_provider(live_palette)


__all__ = ["INJECT_GROUP", "LIVE_VERBS", "find_demand", "input_event_kinds", "live_palette", "register"]
