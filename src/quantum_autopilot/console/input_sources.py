"""Read-only physical telemetry plug. Measurements never imply execution authority."""
from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from uuid import uuid4

from PySide6.QtCore import QTimer, Signal

from .contract import (Artefact, Boot, Chart, ClaimCheck, KV, KeySession, Keys, Ledger,
    LedgerEntry, Link, LogLine, Metric, Node, NodeDetail, Snapshot, Telemetry, Topology)
from .providers import DataProvider
from .observations import OPTIONAL_LINK_METADATA, QUANTITIES, finite_number, measured_link_observations


def empty_snapshot(source: str, reason: str, kind="unavailable") -> Snapshot:
    at = datetime.now(timezone.utc).isoformat()
    scope = "READ-ONLY INPUT · PHYSICAL ACTUATION LOCKED"
    return Snapshot(at, source, "input-unavailable", [],
        Boot("OrionQ", "OrionQ", reason, [], [], [], scope, source),
        Topology(source, [], [], [], [], "0/0", [], reason),
        Telemetry([], Chart([], 0, 1), [], "No memory measurement supplied.", [],
            ClaimCheck("exceeds", at, "NO PREDICTIVE CLAIM", reason), [scope], [], reason),
        Keys(source, [], "NO KEY MATERIAL", [], [], [], reason, [], scope, [], [], scope),
        NodeDetail("UNAVAILABLE", "", scope, None, [], [], [], [], [], ("", "", ""), [], reason, [], source),
        Ledger([], [], [], "UNSIGNED", 0, "No verified audit chain supplied.", [], scope), input_kind=kind)


def _text(value, name):
    if not isinstance(value, str) or not value.strip() or len(value) > 160 or any(ord(c) < 32 or c in "<>" for c in value):
        raise ValueError(f"{name} must be nonempty text (maximum 160 characters)")
    return value


def _unique_fields(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON field: {key}")
        result[key] = value
    return result


def _time(value, now, max_age_s):
    _text(value, "timestamp_utc")
    stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if stamp.utcoffset() is None:
        raise ValueError("timestamp_utc requires an explicit timezone")
    if not -5 <= (now - stamp).total_seconds() <= max_age_s:
        raise ValueError("telemetry timestamp is stale or in the future")
    return stamp


def validate_frame(frame, *, now=None, max_age_s=30):
    """Validate declared measurements; not an authenticity/certification check.

    BER/QBER/fidelity are fractions; SKR is bits/s, OSNR dB, power dBm.
    No transformation from optical metrics to quantum fidelity is performed.
    """
    now = now or datetime.now(timezone.utc)
    required = {"schema", "source_id", "campaign_id", "evidence_kind", "timestamp_utc", "nodes", "links"}
    if not isinstance(frame, dict) or set(frame) != required:
        raise ValueError("invalid telemetry envelope fields")
    if frame["schema"] != "orion.telemetry.v1" or frame["evidence_kind"] != "measured":
        raise ValueError("physical input requires orion.telemetry.v1 and measured evidence; simulated fidelity is refused")
    _text(frame["source_id"], "source_id")
    _text(frame["campaign_id"], "campaign_id")
    stamp = _time(frame["timestamp_utc"], now, max_age_s)
    nodes, links = frame["nodes"], frame["links"]
    if not isinstance(nodes, list) or not 1 <= len(nodes) <= 32:
        raise ValueError("expected 1..32 nodes")
    if not isinstance(links, list) or not 1 <= len(links) <= 256:
        raise ValueError("expected 1..256 measured links")
    ids = set()
    for node in nodes:
        fields = set(node) if isinstance(node, dict) else set()
        if fields not in ({"id", "x", "y"}, {"id", "x", "y", "latitude", "longitude"}):
            raise ValueError("nodes require id, x and y; latitude/longitude are an optional pair")
        ident = _text(node["id"], "node id")
        if ident in ids:
            raise ValueError("duplicate node id")
        ids.add(ident)
        for key in ("x", "y"):
            val = node[key]
            if not 0 <= finite_number(val, key) <= 1:
                raise ValueError("node coordinates must be finite fractions")
        if "latitude" in node:
            for key, lower, upper in (("latitude", -90, 90), ("longitude", -180, 180)):
                val = node[key]
                if not lower <= finite_number(val, key) <= upper:
                    raise ValueError(f"{key} must be a finite geographic coordinate")
    link_ids = set()
    base = {"id", "a", "b", "channel", "device_id", "measurement_id", "timestamp_utc", "evidence_kind"}
    metrics = {"ber", "osnr_db", "power_dbm", "qber", "skr_bps", "fidelity"}
    for link in links:
        if not isinstance(link, dict) or not base <= set(link) or set(link) - base - metrics - OPTIONAL_LINK_METADATA:
            raise ValueError("invalid measured link fields")
        for key in base - {"timestamp_utc", "evidence_kind"}:
            _text(link[key], key)
        if link["id"] in link_ids or link["a"] not in ids or link["b"] not in ids or link["a"] == link["b"]:
            raise ValueError("duplicate link id or invalid endpoints")
        link_ids.add(link["id"])
        if link["evidence_kind"] != "measured":
            raise ValueError("simulated/derived values cannot enter the physical measurement plug")
        sample_time = _time(link["timestamp_utc"], now, max_age_s)
        if sample_time > stamp:
            raise ValueError("measurement cannot be newer than its envelope")
        if not metrics.intersection(link):
            raise ValueError("link requires at least one measured metric")
        for key in metrics.intersection(link):
            val = link[key]
            finite_number(val, key)
            if key in {"ber", "qber", "fidelity"} and not 0 <= val <= 1:
                raise ValueError(f"{key} must be a fraction in [0,1]")
            if key == "skr_bps" and val < 0:
                raise ValueError("skr_bps must be nonnegative")
        if "fidelity" in link:
            if link.get("fidelity_method") not in {"state_tomography", "measured_fidelity"}:
                raise ValueError("fidelity requires state_tomography or measured_fidelity; simulated/model-derived fidelity is refused")
        elif "fidelity_method" in link:
            raise ValueError("fidelity_method requires a measured fidelity")
        measured_link_observations(frame, link, now.isoformat())
    return frame


def measurement_snapshot(frame, digest, selected=None, selected_link=None, *, observations=None):
    snap = empty_snapshot(frame["source_id"], "DECLARED MEASUREMENTS · READ ONLY", "measured")
    if observations is None:
        received = datetime.now(timezone.utc).isoformat()
        observations = tuple(o for row in frame["links"] for o in measured_link_observations(frame, row, received))
    snap.observations = observations
    readings = {(o.entity_id, o.quantity): o for o in observations}
    def display(row, key, spec, unit=None):
        item = readings.get((row["id"], key))
        return item.formatted(spec, unit=unit) if item is not None else "—"
    snap.at, snap.campaign = frame["timestamp_utc"], "measured:" + frame["campaign_id"]
    snap.boot.subtitle = "Measured input declared by the acquisition adapter; authenticity is not independently verified."
    snap.boot.checks = [LogLine(snap.at[11:19], "Telemetry schema and freshness validated", "VALID", "green")]
    snap.topology = Topology(frame["campaign_id"],
        [Node(n["id"], "REPORTED ENDPOINT · HEALTH UNKNOWN", "unknown", n["x"], n["y"],
              latitude=n.get("latitude"), longitude=n.get("longitude")) for n in frame["nodes"]],
        [Link(row["a"], row["b"], row["id"] + " · " + row["channel"],
            display(row, "fidelity", ".4f"),
            display(row, "qber", ".2f", "%") + "%" if "qber" in row else "—",
            display(row, "skr_bps", ".4g"), tone="violet", id=row["id"],
            provenance="measured") for row in frame["links"]],
        [], [], f"{len(frame['links'])} REPORTED", [],
        "Measured input is declarative, not authenticated. Logical positions; endpoint health is not measured. No actuation.")
    matching = [row for row in frame["links"] if selected in (row["a"], row["b"])]
    by_link = [row for row in frame["links"] if row["id"] == selected_link]
    link = (by_link or matching or frame["links"])[0]
    selected_observations = [o for o in observations if o.entity_id == link["id"]]
    values = [Metric(QUANTITIES[o.quantity][0], o.formatted(), o.unit, provenance="measured",
        uncertainty=f"u={o.standard_uncertainty:.5g}" if o.standard_uncertainty is not None else "",
        note=f"Declared measurement · {link['channel']} · {link['device_id']}", tone="violet")
        for o in selected_observations]
    snap.topology.metrics = values[:3]
    snap.telemetry.headline = values[:3]
    snap.telemetry.conditions = [KV(v.label, f"{v.value} {v.unit}") for v in values] + [
        KV("CHANNEL", link["channel"]), KV("MEASUREMENT", link["measurement_id"])]
    timing = selected_observations[0]
    snap.telemetry.conditions += [KV("ACQUIRED AT", timing.acquired_at),
        KV("FIRST HOST READ", timing.received_at_utc or "NOT SUPPLIED"),
        KV("CLOCK", timing.clock_id or "NOT SUPPLIED"), KV("TIME QUALITY", timing.time_quality),
        KV("INTEGRATION", f"{timing.integration_start} / {timing.integration_end}" if timing.integration_start else "NOT SUPPLIED")]
    if "fidelity" in link:
        snap.telemetry.conditions += [KV("FIDELITY METHOD", link["fidelity_method"]),
                                     KV("FIDELITY REFERENCE", link.get("fidelity_reference", "NOT SUPPLIED"))]
    snap.telemetry.window_note = "MEASURED INPUT · LATEST SAMPLE · NO FORECAST"
    snap.telemetry.limitations = ["Provenance is declared by the source, not cryptographically authenticated.",
        "No interpolation, prediction or BER-to-fidelity conversion.", "Current samples do not establish temporal predictive validity.",
        "Host read time is not instrument arrival time. Declared clock IDs do not prove synchronization.",
        "Absent integration windows, uncertainties and fidelity references remain unknown."]
    snap.telemetry.artefacts = [Artefact("canonical frame SHA256", digest, "JSON")]
    snap.keys.sessions = [KeySession(row["measurement_id"], row["channel"],
        display(row, "skr_bps", ".5g"), display(row, "qber", ".5g"),
        "DECLARED MEASURED", digest[:16], "violet") for row in frame["links"] if "qber" in row or "skr_bps" in row]
    snap.keys.sessions_note = "QBER fractions; SKR in bit/s. Measured outcomes, not generated key material."
    snap.node.id = selected or link["a"]
    snap.node.subtitle = "REPORTED PHYSICAL ENDPOINT · READ ONLY"
    snap.node.equipment = [KV("DEVICE", link["device_id"]), KV("CHANNEL", link["channel"])]
    snap.node.calibration_note = "No memory, calibration or control interface supplied."
    snap.ledger.entries = [LedgerEntry(snap.at[11:19], frame["source_id"], "Accepted measured telemetry frame", digest)]
    return snap


class MeasuredFileProvider(DataProvider):
    """Poll a local, atomically replaced JSON file written by an acquisition adapter."""
    error = Signal(str)
    progress = Signal(str)
    is_read_only = True
    source_label = "MEASURED INPUT · READ ONLY"

    @property
    def capabilities(self):
        from .capabilities import SourceCapabilities
        available = self.frame is not None
        return SourceCapabilities(
            adapter_id="measured-json", mode="read_only", health="ready" if available else "unavailable",
            operations=("reload", "export_record", "select_node", "select_link") if available else ("reload", "export_record"),
            limits=(("frame_bytes", 2_000_000), ("nodes", 32), ("links", 256), ("tracked_link_ids", 4096), ("max_age_s", self.max_age_s)),
            note="Declared measurements from a read-only local acquisition bridge; no simulation or device commands.")

    def __init__(self, path, state_root, *, max_age_s=30):
        super().__init__()
        if state_root is None:
            from quantum_autopilot.desktop import default_state_root
            state_root = default_state_root() / "inputs" / uuid4().hex
        self.path, self.state_root = Path(path), Path(state_root)
        if str(self.path).startswith("\\\\"):
            raise ValueError("use a local acquisition bridge file, not a network share")
        if not 1 <= max_age_s <= 3600:
            raise ValueError("max_age_s must be between 1 and 3600")
        self.max_age_s, self.selected, self.selected_link = max_age_s, None, None
        self.frame, self.digest, self._last_time, self._identity = None, None, None, None
        self._link_history = {}  # Bounded acquisition-order guard, not a telemetry archive.
        self._snap = empty_snapshot(self.source_label, "Waiting for measured input", "measured")
        self._available = False
        self.timer = QTimer(self)
        self.timer.setInterval(1000)
        self.timer.timeout.connect(self.refresh)

    def start(self):
        self.refresh()
        self.timer.start()

    def stop(self):
        self.timer.stop()

    def refresh(self):
        try:
            if not self.path.is_file():
                raise ValueError("a readable local regular acquisition file is required")
            with self.path.open("rb") as handle:
                raw = handle.read(2_000_001)
            if len(raw) > 2_000_000:
                raise ValueError("telemetry frame exceeds 2 MB")
            received = datetime.now(timezone.utc)
            frame = validate_frame(json.loads(raw, object_pairs_hook=_unique_fields), now=received, max_age_s=self.max_age_s)
            stamp = datetime.fromisoformat(frame["timestamp_utc"].replace("Z", "+00:00"))
            digest = hashlib.sha256(json.dumps(frame, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()
            identity = (frame["source_id"], frame["campaign_id"])
            if self._identity is not None and identity != self._identity:
                raise ValueError("source/campaign changed; reconnect the input explicitly")
            if self._last_time is not None and stamp < self._last_time:
                raise ValueError("out-of-order telemetry frame")
            if stamp == self._last_time and digest != self.digest:
                raise ValueError("conflicting payload for the same frame timestamp")
            history = dict(self._link_history)
            observations = []
            for row in frame["links"]:
                signature = (row["a"], row["b"], row["channel"], row["device_id"], row.get("clock_id"))
                acquired = datetime.fromisoformat(row["timestamp_utc"].replace("Z", "+00:00"))
                row_digest = hashlib.sha256(json.dumps(row, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()
                first_read = received.isoformat()
                previous = history.get(row["id"])
                if previous is not None:
                    if signature != previous[0]:
                        raise ValueError("link channel/device/clock identity changed; reconnect explicitly")
                    if acquired < previous[1]:
                        raise ValueError("out-of-order per-link acquisition timestamp")
                    if acquired == previous[1]:
                        if row_digest != previous[2]:
                            raise ValueError("conflicting observation for the same link timestamp")
                        first_read = previous[3]
                history[row["id"]] = (signature, acquired, row_digest, first_read)
                observations.extend(measured_link_observations(frame, row, first_read))
            if len(history) > 4096:
                raise ValueError("link identity history exceeds 4096 entries; reconnect explicitly")
            if self.selected not in {n["id"] for n in frame["nodes"]}:
                self.selected = None
            if self.selected_link not in {row["id"] for row in frame["links"]}:
                self.selected_link = None
            snap = measurement_snapshot(frame, digest, self.selected, self.selected_link, observations=tuple(observations))
            self.frame, self.digest, self._last_time, self._identity = frame, digest, stamp, identity
            self._link_history, self._snap = history, snap
            if not self._available:
                self.progress.emit("Measured input available · declared provenance · read only")
            self._available = True
        except (OSError, ValueError, TypeError, RecursionError) as exc:
            self.frame = None
            self._available = False
            self._snap = empty_snapshot(self.source_label, f"INPUT UNAVAILABLE: {exc}", "measured")
            self.error.emit(str(exc))
        self.publish_snapshot()

    def dispatch(self, verb, target=""):
        verb = "export_record" if verb == "export" else verb
        self.capabilities.require(verb)
        if verb == "reload":
            self.refresh()
        elif verb == "select_node":
            if self.frame is None or target not in {n["id"] for n in self.frame["nodes"]}:
                raise ValueError("unknown measured endpoint")
            self.selected = target
            self.selected_link = None
            self._snap = measurement_snapshot(self.frame, self.digest, target, observations=self._snap.observations)
            self.publish_snapshot()
        elif verb == "select_link":
            if self.frame is None or target not in {row["id"] for row in self.frame["links"]}:
                raise ValueError("unknown measured channel/link")
            self.selected, self.selected_link = None, target
            self._snap = measurement_snapshot(self.frame, self.digest, selected_link=target, observations=self._snap.observations)
            self.publish_snapshot()
        elif verb in {"export", "export_record"}:
            self.state_root.mkdir(parents=True, exist_ok=True)
            path = self.state_root / f"measured-input-{uuid4().hex}.json"
            from .evidence_destination import export_destination
            path = export_destination(target, path)
            with path.open("x", encoding="utf-8") as handle:
                json.dump({"scope": "declared_measurements_read_only", "frame": self.frame,
                    "snapshot": asdict(self._snap)}, handle, allow_nan=False)
            self.progress.emit(f"Exported input record: {path}")
            self.export_completed.emit(str(path.resolve()))
        else:
            raise ValueError("measured input is read-only; simulation and hardware commands are unavailable")
        return True
