"""Source-neutral controller inputs; provenance belongs to the ingestion audit."""
from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
import re


SCHEMA = "orion.controller.observation.v1"
IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}")
HASH = re.compile(r"[a-f0-9]{64}")
CAPABILITIES = frozenset({"classical_control", "time_tagging", "correlation", "entanglement_memory", "bsm"})
MAX_EVENTS = 4096


def identifier(value):
    if not isinstance(value, str) or not IDENTIFIER.fullmatch(value):
        raise ValueError("invalid controller identifier")
    return value


def integer(value, minimum=0, maximum=2**63 - 1):
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError("integer outside controller domain")
    return value


def exact(value, fields):
    if not isinstance(value, dict) or set(value) != set(fields):
        raise ValueError("invalid controller object fields")
    return value


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False, ensure_ascii=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


@dataclass(frozen=True)
class ControllerObservation:
    event_id: str
    node_id: str
    session_id: str
    sequence: int
    timestamp_ps: int
    clock_domain: str
    kind: str
    data: dict
    schema: str = SCHEMA

    def __post_init__(self):
        if self.schema != SCHEMA:
            raise ValueError("unsupported controller observation schema")
        for value in (self.event_id, self.node_id, self.session_id, self.clock_domain):
            identifier(value)
        integer(self.sequence)
        integer(self.timestamp_ps)
        data = self.data
        if self.kind == "session_started":
            exact(data, {"configuration_id", "verified"})
            if not isinstance(data["configuration_id"], str) or not HASH.fullmatch(data["configuration_id"]):
                raise ValueError("configuration hash required")
            if type(data["verified"]) is not bool:
                raise ValueError("configuration verification must be boolean")
        elif self.kind == "link_state":
            exact(data, {"link_id", "classical_state", "quantum_state"})
            identifier(data["link_id"])
            if any(not isinstance(data[k], str) or data[k] not in {"up", "down", "unknown"}
                   for k in ("classical_state", "quantum_state")):
                raise ValueError("channel observation state invalid")
        elif self.kind == "resources":
            exact(data, {"available"})
            integer(data["available"], maximum=65536)
        elif self.kind == "timetags":
            exact(data, {"events", "lost_events"})
            integer(data["lost_events"])
            if not isinstance(data["events"], list) or len(data["events"]) > MAX_EVENTS:
                raise ValueError("time-tag block exceeds the bounded contract")
            previous = -1
            for event in data["events"]:
                exact(event, {"timestamp_ps", "channel"})
                integer(event["timestamp_ps"], maximum=self.timestamp_ps)
                integer(event["channel"], minimum=-(2**31), maximum=2**31 - 1)
                if event["channel"] == 0 or event["timestamp_ps"] < previous:
                    raise ValueError("time tags must be ordered and have a nonzero channel")
                previous = event["timestamp_ps"]
        elif self.kind == "heartbeat":
            exact(data, set())
        elif self.kind == "correlation":
            exact(data, {"block_sha256", "configuration_id", "first_channel", "second_channel",
                         "window_ps", "event_count", "coincidence_pairs"})
            for key in ("block_sha256", "configuration_id"):
                if not isinstance(data[key], str) or not HASH.fullmatch(data[key]):
                    raise ValueError("correlation requires block/configuration hashes")
            for key in ("first_channel", "second_channel"):
                integer(data[key], minimum=-(2**31), maximum=2**31 - 1)
                if data[key] == 0:
                    raise ValueError("correlation channel cannot be zero")
            if data["first_channel"] == data["second_channel"]:
                raise ValueError("correlation requires distinct channels")
            integer(data["window_ps"], minimum=1)
            integer(data["event_count"], maximum=65536)
            integer(data["coincidence_pairs"], maximum=data["event_count"]**2)
        elif self.kind == "fault":
            exact(data, {"reason"})
            identifier(data["reason"])
        else:
            raise ValueError("unsupported controller observation kind")
        # Do not retain mutable caller-owned event/data structures.
        object.__setattr__(self, "data", json.loads(canonical(data)))

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, value):
        exact(value, {"schema", "event_id", "node_id", "session_id", "sequence", "timestamp_ps",
                      "clock_domain", "kind", "data"})
        return cls(**value)


def validate_record(value):
    exact(value, {"observation", "provenance"})
    observation = ControllerObservation.from_dict(value["observation"])
    provenance = exact(value["provenance"], {"source_id", "adapter_id", "origin", "record_sha256"})
    identifier(provenance["source_id"])
    identifier(provenance["adapter_id"])
    if not isinstance(provenance["origin"], str) or provenance["origin"] not in {"physical", "simulated", "replay"}:
        raise ValueError("data origin must be explicit")
    if not isinstance(provenance["record_sha256"], str) or not HASH.fullmatch(provenance["record_sha256"]):
        raise ValueError("raw record hash required")
    return observation, json.loads(canonical(provenance))


def validate_configuration(value):
    exact(value, {"schema", "nodes", "links", "freshness_s"})
    if value["schema"] != "orion.mixed-controller.configuration.v1":
        raise ValueError("unsupported mixed controller configuration")
    integer(value["freshness_s"], minimum=1, maximum=3600)
    if not isinstance(value["nodes"], list) or not 2 <= len(value["nodes"]) <= 256:
        raise ValueError("controller requires 2..256 nodes")
    nodes = {}
    resource_keys = set()
    for node in value["nodes"]:
        required = {"node_id", "capabilities", "capacity", "session_id", "clock_domain"}
        if not isinstance(node, dict) or not required <= set(node) or set(node) - required - {"resource_identity"}:
            raise ValueError("invalid controller node fields")
        if "resource_identity" in node:
            from quantum_autopilot.security.equipment import PhysicalIdentity
            identity = exact(node["resource_identity"], {"namespace", "serial", "endpoint"})
            physical = PhysicalIdentity(**identity)
            physical.validate()
            if physical.key in resource_keys:
                raise ValueError("duplicate resource identity")
            resource_keys.add(physical.key)
        for name in ("node_id", "session_id", "clock_domain"):
            identifier(node[name])
        if node["node_id"] in nodes:
            raise ValueError("duplicate node")
        caps = node["capabilities"]
        if (not isinstance(caps, list) or not caps or any(not isinstance(cap, str) for cap in caps)
                or len(caps) != len(set(caps))
                or not set(caps) <= CAPABILITIES or "classical_control" not in caps):
            raise ValueError("unsupported node capabilities")
        integer(node["capacity"], minimum=1, maximum=65536)
        nodes[node["node_id"]] = node
    if not isinstance(value["links"], list) or not 1 <= len(value["links"]) <= 1024:
        raise ValueError("controller requires bounded configured links")
    links, channels = set(), set()
    for link in value["links"]:
        exact(link, {"link_id", "a", "b", "classical_channel_id", "quantum_channel_id"})
        for field in link:
            identifier(link[field])
        if link["a"] not in nodes or link["b"] not in nodes or link["a"] == link["b"]:
            raise ValueError("link endpoints invalid")
        if link["link_id"] in links:
            raise ValueError("duplicate link")
        links.add(link["link_id"])
        for field in ("classical_channel_id", "quantum_channel_id"):
            if link[field] in channels:
                raise ValueError("each classical/quantum channel must be separately identified")
            channels.add(link[field])
    return json.loads(canonical(value))
