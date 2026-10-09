"""Optional routing observation with declared instantaneous resources; no executor."""
from dataclasses import asdict, dataclass, field
import json
from pathlib import Path

from .contracts import NetworkStateSnapshot
from .optional_sync import MAX_SNAPSHOT_BYTES, _hash, _identifier, _json, _time, load_sync_shadow_snapshot
from quantum_autopilot.core import EvidenceKind
from quantum_autopilot.model_intake.routing_session import (
    FEATURE_CONTRACT, KINDS, MODEL_IDS, SCHEMA, evaluate_routing_session, parse_routing_input,
)
from quantum_autopilot.model_intake.sync_session import _number

TELEMETRY_KEY = "optional_routing_window"
WINDOW_SCHEMA = "orion.sandbox-routing-window.v1"
RESOURCE_RULESET = "orion.routing-instantaneous-resources.v1"
MAX_FEATURE_AGE_S = 1.
WINDOW_FIELDS = {
    "schema", "evidence", "feature_contract", "source_id", "campaign_id", "stream_id", "node_id", "clock_id",
    "origin_timestamp_utc", "decision_timestamp_utc", "features_timestamp_utc", "seed", "state_features",
    "candidates", "reference_action_id", "task", "resources",
}


def _fields(value, names):
    if not isinstance(value, dict) or set(value) != set(names):
        raise ValueError("invalid declared fields: " + ", ".join(sorted(names)))


def _integer(value, maximum):
    if type(value) is not int or not 0 <= value <= maximum:
        raise ValueError("bounded nonnegative integer required")
    return value


def _boolean(value):
    if type(value) is not bool:
        raise ValueError("boolean flag required")


def _rows(value, maximum, minimum=0):
    if not isinstance(value, list) or not minimum <= len(value) <= maximum:
        raise ValueError("bounded resource list required")
    return value


def _endpoints(value, nodes):
    if (not isinstance(value, list) or len(value) != 2
            or any(not isinstance(v, str) or v not in nodes for v in value) or value[0] == value[1]):
        raise ValueError("two distinct declared endpoints required")
    return frozenset(value)


def _fraction(value):
    value = _number(value)
    if not 0 <= value <= 1:
        raise ValueError("fraction must be between zero and one")
    return value


def _request(window, model_id):
    origin = _time(window["origin_timestamp_utc"])
    text = _json({"schema": SCHEMA, "feature_contract": window["feature_contract"],
                  "evidence": "simulated_offline", "model_id": model_id, "stream_id": window["stream_id"],
                  "decision_timestamp_s": (_time(window["decision_timestamp_utc"]) - origin).total_seconds(),
                  "features_timestamp_s": (_time(window["features_timestamp_utc"]) - origin).total_seconds(),
                  **{key: window[key] for key in ("seed", "state_features", "candidates", "reference_action_id")}})
    parse_routing_input(model_id, text)
    return text


def _validate_resources(window):
    """Conservative, instantaneous admission only; candidates remain ordered alternatives."""
    resource = window["resources"]
    _fields(resource, {"ruleset", "timestamp_utc", "state_version", "enabled_kinds", "nodes", "links", "pairs"})
    if resource["ruleset"] != RESOURCE_RULESET:
        raise ValueError("unknown instantaneous resource ruleset")
    stamp = _time(resource["timestamp_utc"])
    decision = _time(window["decision_timestamp_utc"])
    if stamp != _time(window["features_timestamp_utc"]):
        raise ValueError("features and resource acquisition times must match")
    _integer(resource["state_version"], 2**63 - 1)
    kinds = _rows(resource["enabled_kinds"], len(KINDS), 1)
    if any(not isinstance(k, str) or k not in KINDS for k in kinds) or len(set(kinds)) != len(kinds):
        raise ValueError("distinct supported enabled kinds required")
    nodes, free = {}, {}
    for node in _rows(resource["nodes"], 64, 1):
        _fields(node, {"node_id", "memory_capacity", "reserved_slots", "available", "can_swap", "can_purify"})
        identifier = _identifier(node["node_id"], "resource node")
        if identifier in nodes:
            raise ValueError("duplicate resource node")
        capacity = _integer(node["memory_capacity"], 256)
        reserved = _integer(node["reserved_slots"], capacity)
        for key in ("available", "can_swap", "can_purify"):
            _boolean(node[key])
        nodes[identifier], free[identifier] = node, capacity - reserved
    if window["node_id"] not in nodes:
        raise ValueError("snapshot node must belong to declared resources")
    links = {}
    for link in _rows(resource["links"], 256):
        _fields(link, {"endpoints", "available"})
        ends = _endpoints(link["endpoints"], nodes)
        _boolean(link["available"])
        if ends in links:
            raise ValueError("duplicate undirected link")
        links[ends] = link["available"]
    pairs = {}
    for pair in _rows(resource["pairs"], 128):
        _fields(pair, {"pair_id", "endpoints", "fidelity", "created_timestamp_utc", "expires_timestamp_utc", "reserved"})
        identifier = _integer(pair["pair_id"], 2**63 - 1)
        if identifier in pairs:
            raise ValueError("duplicate pair ID")
        ends = _endpoints(pair["endpoints"], nodes)
        _fraction(pair["fidelity"])
        _boolean(pair["reserved"])
        created, expires = _time(pair["created_timestamp_utc"]), _time(pair["expires_timestamp_utc"])
        if created > stamp or expires <= created:
            raise ValueError("pair lifetime must start by acquisition and end after creation")
        for endpoint in ends:
            free[endpoint] -= 1  # Expired/reserved pairs still occupy memory until explicitly removed elsewhere.
        pairs[identifier] = pair
    if any(count < 0 for count in free.values()):
        raise ValueError("pair occupancy plus reserved slots exceeds memory capacity")
    task = window["task"]
    _fields(task, {"endpoints", "minimum_fidelity"})
    target = _endpoints(task["endpoints"], nodes)
    minimum = _fraction(task["minimum_fidelity"])
    for candidate in window["candidates"]:
        action = candidate["action"]
        kind = action["kind"]
        if kind not in kinds:
            raise ValueError("candidate kind disabled by resource context")
        if kind == "WAIT":
            continue
        if kind == "GENERATE":
            ends = _endpoints(action["link"], nodes)
            if not links.get(ends, False) or any(not nodes[n]["available"] or free[n] < 1 for n in ends):
                raise ValueError("GENERATE requires an available link, endpoints and free memory")
            continue
        selected = []
        for identifier in action["pair_ids"]:
            pair = pairs.get(identifier)
            if pair is None or pair["reserved"]:
                raise ValueError("candidate references missing or reserved pair")
            if any(not nodes[n]["available"] for n in pair["endpoints"]):
                raise ValueError("pair endpoints unavailable")
            if kind != "DISCARD" and decision >= _time(pair["expires_timestamp_utc"]):
                raise ValueError("candidate requires unexpired pairs at decision time")
            selected.append(pair)
        ends = [set(pair["endpoints"]) for pair in selected]
        if kind == "PURIFY" and (ends[0] != ends[1] or any(not nodes[n]["can_purify"] for n in ends[0])):
            raise ValueError("PURIFY requires matching pair endpoints with declared capability")
        if kind == "SWAP":
            common = ends[0] & ends[1]
            if common != {action["node"]} or not nodes[action["node"]]["can_swap"]:
                raise ValueError("SWAP requires exactly the declared common capable node")
        if kind == "DELIVER" and (ends[0] != target or selected[0]["fidelity"] < minimum):
            raise ValueError("DELIVER requires task endpoints and minimum declared fidelity")


@dataclass(frozen=True)
class RoutingShadowWindow:
    """Immutable canonical payload; callers receive detached dictionaries."""
    payload_json: str

    def __post_init__(self):
        from quantum_autopilot.model_intake.sync_session import _constant, _unique

        if not isinstance(self.payload_json, str) or len(self.payload_json.encode()) > MAX_SNAPSHOT_BYTES:
            raise ValueError("routing window must be bounded JSON")
        value = json.loads(self.payload_json, object_pairs_hook=_unique, parse_constant=_constant)
        _fields(value, WINDOW_FIELDS)
        if (value["schema"], value["evidence"], value["feature_contract"]) != (WINDOW_SCHEMA, "simulated", FEATURE_CONTRACT):
            raise ValueError("simulated routing schema and reviewed features required")
        for key in ("source_id", "campaign_id", "stream_id", "node_id", "clock_id"):
            _identifier(value[key], key)
        _request(value, MODEL_IDS[0])
        age = (_time(value["decision_timestamp_utc"]) - _time(value["features_timestamp_utc"])).total_seconds()
        if not 0 <= age <= MAX_FEATURE_AGE_S:
            raise ValueError("routing features must be no older than 1 s at decision time")
        _validate_resources(value)
        object.__setattr__(self, "payload_json", _json(value))

    @classmethod
    def from_dict(cls, value):
        return cls(_json(value))

    def to_dict(self):
        return json.loads(self.payload_json)

    def model_input(self, model_id):
        return _request(self.to_dict(), model_id)

    @property
    def resource_sha256(self):
        return _hash(_json(self.to_dict()["resources"]))

    def validate_context(self, snapshot):
        value = self.to_dict()
        if snapshot.provenance.evidence_kind != EvidenceKind.SIMULATED:
            raise ValueError("only simulated snapshot evidence admitted")
        if not snapshot.integrity_verified or snapshot.out_of_distribution:
            raise ValueError("requires verified integrity and no OOD flag")
        expected = (snapshot.node_id, snapshot.provenance.source, snapshot.metadata.get("campaign_id"),
                    snapshot.metadata.get("clock_id"), snapshot.metadata.get("routing_stream_id"))
        if tuple(value[k] for k in ("node_id", "source_id", "campaign_id", "clock_id", "stream_id")) != expected:
            raise ValueError("routing node/source/campaign/clock/stream must match snapshot")
        if (_time(snapshot.metadata.get("routing_origin_timestamp_utc")) != _time(value["origin_timestamp_utc"])
                or _time(snapshot.timestamp_utc) != _time(value["decision_timestamp_utc"])):
            raise ValueError("routing origin and decision time must match snapshot")
        if (snapshot.metadata.get("routing_resource_sha256") != self.resource_sha256
                or snapshot.metadata.get("routing_ruleset") != RESOURCE_RULESET
                or type(snapshot.state_version) is not int
                or snapshot.state_version != value["resources"]["state_version"]):
            raise ValueError("routing resource hash/ruleset/version must match snapshot")
        return (_time(value["decision_timestamp_utc"]) - _time(value["features_timestamp_utc"])).total_seconds()


@dataclass(frozen=True)
class RoutingShadowRecord:
    status: str
    reason: str
    detail: str
    selected_model_id: str | None
    snapshot_json: str
    snapshot_sha256: str
    evaluation_json: str | None = None
    resource_sha256: str | None = None
    feature_age_s: float | None = None
    schema: str = field(default="orion.sandbox-routing-shadow.v1", init=False)
    mode: str = field(default="shadow", init=False)
    resource_ruleset: str = field(default=RESOURCE_RULESET, init=False)
    policy_activated: bool = field(default=False, init=False)
    physical_action_performed: bool = field(default=False, init=False)
    simulator_action_performed: bool = field(default=False, init=False)
    resources_mutated: bool = field(default=False, init=False)

    def serialized(self):
        return json.dumps(asdict(self), ensure_ascii=False, indent=2, allow_nan=False)


def evaluate_optional_routing(snapshot, *, model_id=None, archive_path=None):
    if model_id is None:
        return RoutingShadowRecord("off", "not_selected", "No input or weights read.", None, "", "")
    if model_id not in MODEL_IDS:
        raise ValueError("unsupported routing checkpoint")
    if not isinstance(snapshot, NetworkStateSnapshot):
        raise ValueError("typed NetworkStateSnapshot required")
    text = _json(snapshot.to_dict())
    if len(text.encode("utf-8")) > MAX_SNAPSHOT_BYTES:
        raise ValueError("snapshot exceeds 128 KiB")
    snapshot = NetworkStateSnapshot.from_dict(json.loads(text))

    def record(status, reason, detail, **kwargs):
        return RoutingShadowRecord(status, reason, detail, model_id, text, _hash(text), **kwargs)

    if TELEMETRY_KEY not in snapshot.telemetry:
        return record("unavailable", "missing_routing_window", "Explicit routing features and resources required.")
    try:
        window = RoutingShadowWindow.from_dict(snapshot.telemetry[TELEMETRY_KEY])
        age = window.validate_context(snapshot)
        request = window.model_input(model_id)
    except (ValueError, OverflowError) as exc:
        return record("refused", "incompatible_routing_window", str(exc))
    if archive_path is None:
        return record("unavailable", "missing_archive", "Explicit external QNET archive required.")
    try:
        evaluation = evaluate_routing_session(model_id, archive_path, request)
    except (ValueError, OSError, ArithmeticError) as exc:
        return record("refused", "model_evaluation_failed", str(exc))
    return record("evaluated", "shadow_only", "Declared instantaneous checks only; no action or resource mutation.",
                  evaluation_json=evaluation.serialized(), resource_sha256=window.resource_sha256, feature_age_s=age)


def export_routing_shadow(result, path):
    with Path(path).open("xb") as stream:
        stream.write(result.serialized().encode("utf-8"))


def main(argv=None):
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--model", choices=MODEL_IDS, default=None)
    parser.add_argument("--qnet-zip", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.output.exists():
            raise ValueError("output already exists; select a new path")
        snapshot = None if args.model is None else load_sync_shadow_snapshot(args.input)
        result = evaluate_optional_routing(snapshot, model_id=args.model, archive_path=args.qnet_zip)
        export_routing_shadow(result, args.output)
    except (ValueError, OSError) as exc:
        parser.exit(2, f"Routing shadow refused: {exc}\n")
    print(f"{result.status}: {result.reason}; no action performed. Report: {args.output}")
    return 0 if result.status in {"off", "evaluated"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
