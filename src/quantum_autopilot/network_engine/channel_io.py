"""Common paired-port intake for author simulation and raw acquisition replay.

QuantumPort carries optical *statistical records*, not qubits or quantum states.
ClassicalPort carries the receiver's observations and control messages. No driver,
socket, device credential or physical transmitter is implemented here. Replaying a
file is evidence replay, never a new physical quantum transmission.
"""
from dataclasses import asdict, replace
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path

import numpy as np

from ..control_plane.channels import ChannelPair, identifier
from .sources import (Commissioning, DomainNote, InputFrame, LinkCommand, LinkInput,
                      PolarisationInput, QkdInput, SESSION_COLUMNS, SyncInput)
from .state import SourceInfo
from ..control_plane import polarisation_plant


def _number(value, name, lower=None, upper=None):
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError(f"{name} must be a finite number")
    if (lower is not None and value < lower) or (upper is not None and value > upper):
        raise ValueError(f"{name} is outside its permitted range")


def _unique(pairs):
    out = {}
    for key, value in pairs:
        if key in out:
            raise ValueError(f"duplicate acquisition field: {key}")
        out[key] = value
    return out


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def observation_record(item):
    """Export raw receiver observables; never a node state or a processed result."""
    value = asdict(replace(item, reference={}, quantum_record=None))
    value.pop("reference")
    value.pop("quantum_record")
    for name in ("time_s", "measured", "covariates"):
        value["polarisation"][name] = value["polarisation"][name].tolist()
    return value


def observation_from_record(value):
    if not isinstance(value, dict) or set(value) != {
        "link_id", "a", "b", "distance_km", "epoch_tick", "period", "polarisation", "sync", "qkd", "domain_notes"}:
        raise ValueError("raw observations require the full receiver acquisition, not ready-made node metrics")
    try:
        polarisation = dict(value["polarisation"])
        for name in ("time_s", "measured", "covariates"):
            polarisation[name] = np.array(polarisation[name], dtype=float)
        qkd = dict(value["qkd"])
        qkd["commissioning"] = Commissioning(**qkd["commissioning"])
        return LinkInput(**{**value, "polarisation": PolarisationInput(**polarisation),
                            "sync": SyncInput(**value["sync"]), "qkd": QkdInput(**qkd),
                            "domain_notes": tuple(DomainNote(**note) for note in value["domain_notes"])})
    except (TypeError, KeyError, OverflowError) as exc:
        raise ValueError("invalid receiver acquisition fields") from exc


def validated_observation(item, link):
    if not isinstance(item, LinkInput) or (item.link_id, item.a, item.b) != (
            link.link_id, link.left_node_id, link.right_node_id):
        raise ValueError("observations do not identify the configured link and endpoints")
    if item.distance_km != link.distance_km:
        raise ValueError("acquisition length differs from the configured optical span")
    for name in ("epoch_tick", "period"):
        if type(getattr(item, name)) is not int or getattr(item, name) < 0:
            raise ValueError(f"{name} must be a nonnegative integer")
    p = item.polarisation
    if p.operating_point not in polarisation_plant.OPERATING_POINTS:
        raise ValueError("unknown monitor operating point")
    _number(p.control_period_s, "monitor control period", 1e-12)
    _number(p.latency_s, "monitor latency", 0)
    if type(p.photons_per_basis) is not int or p.photons_per_basis < 1:
        raise ValueError("monitor photon count must be a positive integer")
    arrays = {name: np.array(getattr(p, name), dtype=float, copy=True)
              for name in ("time_s", "measured", "covariates")}
    n = len(arrays["time_s"])
    if not 1 <= n <= 12000 or arrays["time_s"].shape != (n,) or arrays["measured"].shape != (n, 3, 3) \
            or arrays["covariates"].shape != (n, 3):
        raise ValueError("polarisation acquisition arrays have inconsistent shapes")
    if any(not np.isfinite(array).all() for array in arrays.values()) or (np.diff(arrays["time_s"]) <= 0).any():
        raise ValueError("sensor samples must be finite and chronologically ordered")
    rotations = arrays["measured"]
    if not np.allclose(rotations @ rotations.transpose(0, 2, 1), np.eye(3), atol=1e-5) \
            or not np.allclose(np.linalg.det(rotations), 1, atol=1e-5):
        raise ValueError("polarisation monitor requires measured SO(3) rotations")
    for array in arrays.values():
        array.setflags(write=False)
    q = item.qkd
    if set(q.row) != set(SESSION_COLUMNS):
        raise ValueError("QKD acquisition requires the 17 receiver columns")
    for name, value in q.row.items():
        _number(value, name)
        if name.endswith(("_gain", "_error")):
            _number(value, name, 0, 1)
    if q.row["period"] != item.period or q.row["length_km"] != link.distance_km \
            or q.row["pulses"] != q.clock_rate_hz * q.period_s:
        raise ValueError("receiver period, span or pulse count differs from acquisition metadata")
    for name in ("signal_probability", "vacuum_probability"):
        _number(getattr(q, name), name, 0, 1)
    if q.signal_probability + q.vacuum_probability >= 1:
        raise ValueError("the decoy probability must be positive")
    for name in ("clock_rate_hz", "period_s"):
        _number(getattr(q, name), name, 1e-12)
    for name, value in asdict(item.sync).items():
        _number(value, "sync." + name)
    _number(item.sync.update_period_s, "sync update period", 1e-12)
    _number(item.sync.latency_s, "sync latency", 0)
    if type(item.sync.photons) is not int or item.sync.photons < 1:
        raise ValueError("sync photon count must be a positive integer")
    for name, value in asdict(q.commissioning).items():
        _number(value, "commissioning." + name, 0)
    copied = replace(item, polarisation=replace(p, **arrays), qkd=replace(q, row=dict(q.row)),
                     quantum_record=None, reference={})
    # Hash arrays as binary buffers: no large JSON serialization in the live loop.
    metadata = asdict(replace(copied, polarisation=replace(p, time_s=(), measured=(), covariates=())))
    body = _json(metadata)
    digest = hashlib.sha256(body)
    size = len(body)
    for array in arrays.values():
        digest.update(array.tobytes())
        size += array.nbytes
    return copied, size, digest.hexdigest()


class QuantumPort:
    def send_receive(self, record, pair, item):
        if not pair.quantum.enabled:
            raise ValueError("quantum port is disabled")
        required = {"acquisition_id", "channel_id", "associated_classical_id", "transmitter_id", "receiver_id", "emitted_pulses", "signal_gain",
                    "decoy_gain", "vacuum_gain", "scope", "physical_transmission"}
        if not isinstance(record, dict) or set(record) != required:
            raise ValueError("no complete quantum acquisition record was received")
        identifier(record["acquisition_id"])
        if (record["channel_id"], record["associated_classical_id"]) != (
                pair.quantum.channel_id, pair.classical.channel_id):
            raise ValueError("the optical record belongs to a different channel association")
        if (record["transmitter_id"], record["receiver_id"]) != (item.a, pair.quantum.receiver_id):
            raise ValueError("quantum record identifies different endpoints")
        if record["scope"] not in ("statistical_simulation", "recorded_measurement") \
                or record["physical_transmission"] is not False:
            raise ValueError("this port cannot attest a new physical quantum transmission")
        _number(record["emitted_pulses"], "emitted pulses", 1)
        if record["emitted_pulses"] != item.qkd.row["pulses"]:
            raise ValueError("optical and classical pulse counts differ")
        for name in ("signal_gain", "decoy_gain", "vacuum_gain"):
            _number(record[name], name, 0, 1)
            if record[name] != item.qkd.row[name]:
                raise ValueError("optical activity and receiver observations differ")
        encoded = _json(record)
        return json.loads(encoded), hashlib.sha256(encoded).hexdigest()


class ClassicalPort:
    def send_receive(self, item, link, pair, *, sent_at_s, received_at_s):
        if not pair.classical.enabled:
            raise ValueError("classical port is disabled")
        if not 0 <= received_at_s - sent_at_s <= pair.classical.max_age_s:
            raise ValueError("classical message is stale or from the future")
        copied, size, digest = validated_observation(item, link)
        header = _json({"association_id": pair.association_id, "channel_id": pair.classical.channel_id,
                        "quantum_channel_id": pair.quantum.channel_id,
                        "acquisition_id": item.quantum_record["acquisition_id"]})
        digest = hashlib.sha256(header + digest.encode("ascii")).hexdigest()
        size += len(header)
        if size > pair.classical.max_message_bytes:
            raise ValueError("receiver observations exceed the classical message limit")
        return copied, size, digest


class ChannelIntake:
    def __init__(self):
        self.quantum, self.classical = QuantumPort(), ClassicalPort()
        self.receipts, self._last = {}, {}
        self._control_counts = {}

    def decorate(self, links):
        return {key: replace(live, provenance={**live.provenance,
                    "channels": deepcopy(self.receipts.get(key, {}))}) for key, live in links.items()}

    def ingest(self, frame, topology):
        links, receipts = {}, {}
        current = {link.link_id for link in topology.links}
        self._last = {key: value for key, value in self._last.items() if key in current}
        self._control_counts = {key: value for key, value in self._control_counts.items() if key in current}
        for link in topology.links:
            item = frame.links.get(link.link_id)
            receipt = {"configuration": link.channels, "source": frame.kind, "tick": frame.tick,
                       "scope": "software_statistical_ports", "physical_transmission": False,
                       "stages": [], "status": "no_input", "error": None,
                       "control_messages": self._control_counts.get(link.link_id, 0)}
            receipts[link.link_id] = receipt
            if not link.active or item is None:
                continue
            try:
                pair = ChannelPair.from_dict(link.channels)
                pair.validate_link(link.link_id, link.right_node_id)
                if pair.quantum.protocol != 'bb84-statistical-record-v1':
                    raise ValueError('this intake processes BB84 statistical records; operation records require a registered modular session')
                receipt["stages"].append("input_validation")
                quantum, qdigest = self.quantum.send_receive(item.quantum_record, pair, item)
                receipt["stages"].extend(("quantum_send", "quantum_receive"))
                key = (item.epoch_tick, item.period)
                if link.link_id in self._last and key <= self._last[link.link_id]:
                    raise ValueError("duplicate or out-of-order quantum acquisition; no fresh communication")
                if (frame.kind == "simulated") != (quantum["scope"] == "statistical_simulation"):
                    raise ValueError("the optical evidence origin differs from the input source declaration")
                observation, size, cdigest = self.classical.send_receive(
                    item, link, pair, sent_at_s=frame.network_time_s, received_at_s=frame.tick * 1.0)
                receipt["stages"].extend(("classical_send", "classical_receive", "observation_validation"))
                receipt.update(status="received", acquisition_id=quantum["acquisition_id"],
                               quantum_record_sha256=qdigest, classical_payload_sha256=cdigest,
                               classical_payload_bytes=size)
                self._last[link.link_id] = key
                links[link.link_id] = observation
            except (ValueError, TypeError, KeyError, AttributeError, OverflowError) as exc:
                receipt.update(status="rejected", error=str(exc))
        self.receipts = receipts
        return replace(frame, links=links)

    def actuator(self, source, topology):
        intake = self

        class Actuator:
            def actuate(self, commands):
                allowed = {}
                for link in topology.links:
                    command = commands.get(link.link_id)
                    if command is None or link.channels is None:
                        continue
                    pair = ChannelPair.from_dict(link.channels)
                    if not pair.classical.enabled or not isinstance(command, LinkCommand):
                        continue
                    payload = _json(asdict(command))
                    if len(payload) <= pair.classical.max_message_bytes:
                        allowed[link.link_id] = LinkCommand(**json.loads(payload))
                source.actuate(allowed)
                for link_id in allowed:
                    intake._control_counts[link_id] = intake._control_counts.get(link_id, 0) + 1
                    if link_id in intake.receipts:
                        intake.receipts[link_id]["control_messages"] = intake._control_counts[link_id]

        return Actuator()


class AcquisitionFileSource:
    """Replay an explicit raw channel capture. No metrics-only feed or hardware actuation."""
    def __init__(self, path, *, expected_kind="measured"):
        self.path = Path(path).expanduser().resolve(strict=True)
        if self.path.stat().st_size > 64 * 1024 * 1024:
            raise ValueError("raw acquisition capture exceeds 64 MiB")
        content = self.path.read_bytes()
        capture = json.loads(content, object_pairs_hook=_unique)
        if not isinstance(capture, dict) or set(capture) != {"schema", "source_id", "kind", "frames"} \
                or capture["schema"] != "orion.channel-capture.v1":
            raise ValueError("expected orion.channel-capture.v1 raw acquisitions")
        if capture["kind"] not in ("measured", "simulated") or capture["kind"] != expected_kind:
            raise ValueError("the declared capture origin differs from the selected input kind")
        identifier(capture["source_id"])
        frames = capture["frames"]
        if not isinstance(frames, list) or not 1 <= len(frames) <= 3600:
            raise ValueError("expected 1..3600 acquisition frames")
        self.kind, self.source_id = capture["kind"], capture["source_id"]
        self.label = f"Raw acquisition replay · {self.kind} declaration · {self.source_id}"
        self.digest = hashlib.sha256(content).hexdigest()
        self._frames = {}
        for frame in frames:
            if not isinstance(frame, dict) or set(frame) != {"tick", "links"} \
                    or type(frame["tick"]) is not int or frame["tick"] < 0 or frame["tick"] in self._frames:
                raise ValueError("capture ticks must be unique nonnegative integers")
            if not isinstance(frame["links"], list) or len(frame["links"]) > 256:
                raise ValueError("a capture frame supports at most 256 links")
            items = {}
            for entry in frame["links"]:
                if not isinstance(entry, dict) or set(entry) != {"observation", "quantum_record"}:
                    raise ValueError("each acquisition requires separate optical and classical records")
                item = observation_from_record(entry["observation"])
                if item.link_id in items:
                    raise ValueError("duplicate acquisition link")
                items[item.link_id] = replace(item, quantum_record=entry["quantum_record"])
            self._frames[frame["tick"]] = items
        self._offset = None

    def frame(self, tick, topology):
        if self._offset is None:
            self._offset = tick - min(self._frames)
        return InputFrame(tick, float(tick), self.kind, self._frames.get(tick - self._offset, {}))

    def info(self):
        return SourceInfo(kind=self.kind, label=self.label,
                          note=f"File replay only; declared origin is not authenticated. SHA256 {self.digest}. "
                               "No new quantum transmission or physical action is performed.")

    def inject(self, event):
        raise ValueError("acquisition replay is read-only; input events cannot modify recordings")

    def actuate(self, commands):
        # Commands can be received for audit but never change historical acquisitions.
        return None
