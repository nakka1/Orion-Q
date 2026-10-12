"""Normalize acquisition/native callbacks without teaching the controller brands.

Source identities and raw hashes belong to the audit envelope. Clicks contain
neither preparation bases nor evidence of an entangled pair. Physical admission
requires the existing authenticated instrument lease, current configuration and
an explicitly aligned measured clock; this module never connects equipment.
"""
from __future__ import annotations

from dataclasses import asdict
from typing import Any

from quantum_autopilot.control_plane.mixed_contracts import (
    ControllerObservation, MAX_EVENTS, digest, identifier, integer, validate_record,
)

from .observations import ClockReference
from .photon import Acquisition, AcquisitionError


BLOCKING_QUALITY_FLAGS = frozenset({"overflow", "stream_buffer_saturated", "disconnected",
    "clock_unlocked", "clock_drift", "detector_unavailable", "native_error"})


def _envelope(*, node_id, session_id, sequence, timestamp_ps, clock_domain,
              kind, data, source_id, adapter_id, origin, record_sha256):
    content = {"node_id": node_id, "session_id": session_id, "sequence": sequence,
               "timestamp_ps": timestamp_ps, "clock_domain": clock_domain,
               "kind": kind, "data": data}
    observation = ControllerObservation(event_id="event-" + digest(content), **content)
    value = {"observation": observation.to_dict(), "provenance": {
        "source_id": source_id, "adapter_id": adapter_id, "origin": origin,
        "record_sha256": record_sha256}}
    validate_record(value)
    return value


def _source_identifier(value):
    try:
        return identifier(value)
    except ValueError:
        # Legacy acquisitions retain their full source string in the raw record.
        # The bounded audit envelope uses its stable opaque identifier instead.
        return "source-" + digest({"source_id": value})


def _physical_session(acquisition, adapter, clock_reference):
    # An arbitrary readiness dictionary cannot authorize measured input.
    from quantum_autopilot.lab.adapters import SwabianSDKAdapter
    from quantum_autopilot.security.equipment import AuthorizedPhotonAdapter

    if not isinstance(adapter, AuthorizedPhotonAdapter):
        raise AcquisitionError("physical normalization requires authenticated instrument authority")
    native = adapter._adapter
    if (not isinstance(native, SwabianSDKAdapter) or native.mode != "hardware"
            or native._test_only_transport or not getattr(native.sdk, "__file__", None)):
        raise AcquisitionError("physical normalization requires an actual native hardware transport")
    adapter._native_guard(acquisition=True)
    identity = adapter.identify()
    configuration = adapter.configuration
    if (identity.get("state") != "ready" or not identity.get("identity_verified")
            or not identity.get("hardware_connected") or identity.get("acquisition_blocked")
            or configuration is None or identity.get("device_id") != acquisition.device_id
            or identity.get("applied_configuration_id") != digest(acquisition.configuration)
            or configuration.configuration_id != digest(acquisition.configuration)
            or native._clock_domain != acquisition.clock_domain):
        raise AcquisitionError("physical configuration or instrument session no longer matches acquisition")
    if not isinstance(clock_reference, ClockReference):
        raise AcquisitionError("physical input requires an explicit measured clock reference")
    clock_reference.validate()
    if (not clock_reference.locked or not acquisition.clock_locked
            or clock_reference.domain != acquisition.clock_domain):
        raise AcquisitionError("physical acquisition clock is not aligned and locked")
    # The guard checks calibration expiry, connection fence, reference clock and
    # the authenticated session on every call; nothing grants permission here.


def normalize_acquisition(acquisition: Acquisition, *, node_id: str, session_id: str,
                          sequence: int = 0, clock_reference: ClockReference | None = None,
                          adapter: Any = None, max_events: int = MAX_EVENTS) -> list[dict]:
    """Return bounded raw blocks and explicit faults; never infer link/resources.

    ``sequence`` identifies the session-start event; subsequent envelopes advance
    it. Callers persist the original acquisition separately and submit records
    in order. Replay remains replay even when its original data were measured.
    """
    acquisition.validate()
    identifier(node_id)
    identifier(session_id)
    integer(sequence)
    integer(max_events, minimum=1, maximum=MAX_EVENTS)
    if acquisition.mode == "hardware":
        _physical_session(acquisition, adapter, clock_reference)
        origin = "physical"
    elif acquisition.mode == "replay":
        origin = "replay"
    else:
        origin = "simulated"
    if clock_reference is not None:
        clock_reference.validate()
        if clock_reference.domain != acquisition.clock_domain:
            raise AcquisitionError("clock reference does not identify this acquisition")
    # Admission needs quality evidence, not a repeated histogram/science pass.
    flags = set(acquisition.diagnostics.get("quality_flags", []))
    if any(event.event_type in {"OverflowBegin", "MissedEvents"} for event in acquisition.events):
        flags.add("overflow")
    if any(event.event_type == "Error" for event in acquisition.events):
        flags.add("native_error")
    if not acquisition.clock_locked or clock_reference is not None and not clock_reference.locked:
        flags.add("clock_unlocked")
    start = acquisition.diagnostics.get("capture_start_ps", 0)
    end = integer(start + acquisition.duration_ps - 1)
    if any(not start <= event.timestamp_ps <= end for event in acquisition.events):
        raise AcquisitionError("raw event lies outside the acquisition interval")
    raw_hash = digest(asdict(acquisition))
    common = {"node_id": node_id, "session_id": session_id, "clock_domain": acquisition.clock_domain,
              "source_id": _source_identifier(acquisition.source_id), "adapter_id": acquisition.adapter_id,
              "origin": origin, "record_sha256": raw_hash}
    records = []

    def emit(kind, data, timestamp):
        records.append(_envelope(**common, sequence=sequence + len(records),
                                 timestamp_ps=timestamp, kind=kind, data=data))

    emit("session_started", {"configuration_id": digest(acquisition.configuration),
                             "verified": not bool(flags & BLOCKING_QUALITY_FLAGS)}, start)
    tags = [{"timestamp_ps": event.timestamp_ps, "channel": event.channel}
            for event in acquisition.events if event.event_type == "TimeTag"]
    lost = sum(event.missed_events for event in acquisition.events if event.event_type == "MissedEvents")
    # Lost counts are attached exactly once, even when the raw stream has no clicks.
    if tags:
        for offset in range(0, len(tags), max_events):
            block = tags[offset:offset + max_events]
            emit("timetags", {"events": block, "lost_events": lost if offset == 0 else 0},
                 end)
    else:
        emit("timetags", {"events": [], "lost_events": lost}, end)
    for flag in sorted(flags):
        # Fixed safe identifiers are supplied by acquisition analysis. Unknown
        # flags are conservatively represented, never silently dropped/logged.
        reason = flag if isinstance(flag, str) and flag.replace("_", "").isalnum() else "source_quality_fault"
        emit("fault", {"reason": reason}, end)
    return records


def _delivery_index(events):
    transmitted, received, clicked = set(), set(), set()
    for raw in events:
        if raw.get("event") == "quantum_sent":
            transmitted.add(tuple(raw.get(key) for key in
                ("photon_id", "node_id", "target", "link_id", "quantum_channel_id", "time_ps")))
        elif raw.get("event") == "quantum_received":
            received.add(tuple(raw.get(key) for key in
                ("photon_id", "sender", "receiver", "link_id", "quantum_channel_id", "transmitted_at_ps", "received_at_ps")))
        elif raw.get("event") == "detector_click" and isinstance(raw.get("quantum_delivery"), dict):
            clicked.add((digest(raw["quantum_delivery"]), raw.get("node_id"),
                        raw.get("callback_at_ps", raw.get("time_ps"))))
    return transmitted, received, clicked


def _quantum_delivery_proof(event, node_id, delivery_index):
    proof = event.get("quantum_delivery")
    if not isinstance(proof, dict):
        return False
    fields = {"photon_id", "sender", "receiver", "link_id", "quantum_channel_id",
              "transmitted_at_ps", "received_at_ps", "detected_at_ps"}
    if set(proof) != fields:
        raise AcquisitionError("native quantum callback proof fields incomplete")
    for name in ("photon_id", "sender", "receiver", "link_id", "quantum_channel_id"):
        identifier(proof[name])
    tx, rx, detected = (integer(proof[name]) for name in
                        ("transmitted_at_ps", "received_at_ps", "detected_at_ps"))
    if (proof["sender"] == proof["receiver"] or node_id not in {proof["sender"], proof["receiver"]}
            or proof["link_id"] != event["link_id"] or not tx <= rx <= detected <= event["time_ps"]):
        raise AcquisitionError("native quantum callback is not causally bound to this link")
    if delivery_index is None:
        return False
    # An assertion carried by a control message is insufficient. Require the
    # independent raw source's transmit, receive and actual detector callbacks.
    transmitted, received, clicked = delivery_index
    tx_key = (proof["photon_id"], proof["sender"], proof["receiver"], proof["link_id"], proof["quantum_channel_id"], tx)
    rx_key = (*tx_key, rx)
    if (tx_key not in transmitted or rx_key not in received
            or (digest(proof), proof["receiver"], detected) not in clicked):
        raise AcquisitionError("quantum delivery has no matching native transmit/receive/detector evidence")
    return True


def normalize_sequence_callbacks(events: list[dict], *, node_id: str, session_id: str,
                                 configuration_id: str, clock_domain: str, source_id: str,
                                 adapter_id: str, sequence: int = 0,
                                 delivery_evidence: list[dict] | None = None) -> list[dict]:
    """Convert native observations only, retaining original callbacks in hashes.

    Source execution provenance is established by the independent runner and
    persisted alongside these envelopes. Tests use explicitly identified callback
    fixtures; this function never declares either data source physically measured.
    Native simulation availability concerns communication/slots, not entanglement.
    """
    if not isinstance(events, list) or len(events) > 1_000_000:
        raise AcquisitionError("bounded native callback list required")
    if delivery_evidence is not None and (not isinstance(delivery_evidence, list)
            or len(delivery_evidence) > 1_000_000 or any(not isinstance(raw, dict) for raw in delivery_evidence)):
        raise AcquisitionError("bounded independent delivery evidence required")
    delivery_index = _delivery_index(delivery_evidence) if delivery_evidence is not None else None
    identifier(node_id)
    identifier(session_id)
    integer(sequence)
    common = {"node_id": node_id, "session_id": session_id, "clock_domain": clock_domain,
              "source_id": source_id, "adapter_id": adapter_id, "origin": "simulated"}
    initial_hash = digest({"configuration_id": configuration_id, "clock_domain": clock_domain,
                           "node_id": node_id, "session_id": session_id})
    records = [_envelope(**common, sequence=sequence, timestamp_ps=0, kind="session_started",
                         data={"configuration_id": configuration_id, "verified": True},
                         record_sha256=initial_hash)]
    previous = -1
    for event in events:
        if not isinstance(event, dict) or event.get("node_id") != node_id:
            raise AcquisitionError("native callback belongs to another node")
        timestamp = integer(event.get("time_ps"))
        if timestamp < previous:
            raise AcquisitionError("native callback clock regressed; input is not sorted or repaired")
        previous = timestamp
        kind = event.get("event")
        if kind == "detector_click":
            click = {"timestamp_ps": integer(event.get("timestamp_ps", timestamp)),
                     "channel": event.get("channel")}
            normalized_kind, data = "timetags", {"events": [click], "lost_events": 0}
        elif kind == "classical_received":
            normalized_kind, data = "link_state", {
                "link_id": event["link_id"], "classical_state": "up",
                "quantum_state": "up" if _quantum_delivery_proof(event, node_id, delivery_index) else "unknown"}
        elif kind == "resources_available":
            normalized_kind, data = "resources", {"available": event["available"]}
        elif kind == "fault":
            normalized_kind, data = "fault", {"reason": event["reason"]}
        elif kind == "heartbeat":
            normalized_kind, data = "heartbeat", {}
        elif kind in {"quantum_sent", "quantum_received", "classical_sent"}:
            # Audit retains raw transmission callbacks. A send has no receipt and
            # therefore must not create an operational link or detector click.
            continue
        else:
            raise AcquisitionError("unsupported native callback; no simulated substitute")
        records.append(_envelope(**common, sequence=sequence + len(records), timestamp_ps=timestamp,
                                 kind=normalized_kind, data=data, record_sha256=digest(event)))
    return records
