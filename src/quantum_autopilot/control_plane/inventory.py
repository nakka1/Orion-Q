"""Bounded simulator inventory commands; never connection credentials or drivers."""
import ipaddress
import re
import math
from dataclasses import dataclass
from collections.abc import Mapping

from .geodesy import LOCATION_SOURCE, MAX_PLACE_CHARS, validate_coordinates
from .topology import DEFAULT_TOPOLOGY_ID, SimulatedNodeState, SimulatedLink


@dataclass(frozen=True)
class AddSimulatedNode:
    node_id: str
    role: str
    expected_version: int
    address: str
    port: int
    topology_id: str = DEFAULT_TOPOLOGY_ID

    def __post_init__(self):
        if not isinstance(self.node_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", self.node_id):
            raise ValueError("node_id must be 1..64 ASCII letters, digits, dots, underscores or hyphens")
        if not isinstance(self.role, str) or self.role not in {"endpoint", "repeater"}:
            raise ValueError("only endpoint and repeater roles are implemented")
        if type(self.expected_version) is not int or self.expected_version < 0:
            raise ValueError("expected_version must be a nonnegative integer")
        if self.topology_id != DEFAULT_TOPOLOGY_ID:
            raise ValueError("this inventory editor only changes the default simulator workspace")
        if not isinstance(self.address, str) or len(self.address) > 64:
            raise ValueError("address must be an IP literal, never a URL or hostname")
        ipaddress.ip_address(self.address)
        if type(self.port) is not int or not 1 <= self.port <= 65535:
            raise ValueError("port must be an integer in 1..65535")

    @classmethod
    def from_dict(cls, payload):
        required = {"node_id", "role", "expected_version", "address", "port", "topology_id"}
        if not isinstance(payload, Mapping) or set(payload) != required:
            raise ValueError("invalid add-node fields; credentials and equipment settings are not accepted")
        return cls(**dict(payload))

    def node(self):
        return SimulatedNodeState(self.node_id, self.role, 0, "IDLE", telemetry={
            "inventory": {"origin": "orion-console", "address": self.address, "port": self.port,
                          "scope": "metadata_only_no_connection"}})


@dataclass(frozen=True)
class ConfigureSimulatedLink:
    mode: str
    link_id: str
    left_node_id: str
    right_node_id: str
    distance_km: float
    nominal_pair_fidelity: float
    active: bool
    expected_version: int
    topology_id: str = DEFAULT_TOPOLOGY_ID
    channels: dict | None = None

    def __post_init__(self):
        if self.mode not in ("create", "update"):
            raise ValueError("mode must be create or update")
        for value in (self.link_id, self.left_node_id, self.right_node_id):
            if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", value):
                raise ValueError("link and node IDs must be bounded ASCII identifiers")
        if self.left_node_id == self.right_node_id:
            raise ValueError("a link requires two different endpoints")
        for name, maximum in (("distance_km", 10000), ("nominal_pair_fidelity", 1)):
            value = getattr(self, name)
            if type(value) not in (int, float) or not 0 <= value <= maximum or not math.isfinite(value):
                raise ValueError(f"{name} must be a finite number in [0, {maximum}]")
        if type(self.active) is not bool:
            raise ValueError("active must be boolean")
        if type(self.expected_version) is not int or self.expected_version < 0:
            raise ValueError("expected_version must be a nonnegative integer")
        if self.topology_id != DEFAULT_TOPOLOGY_ID:
            raise ValueError("link editor only changes the default simulator workspace")
        from .channels import ChannelPair, local_pair
        channels = local_pair(self.link_id, self.right_node_id) if self.channels is None else self.channels
        pair = ChannelPair.from_dict(channels)
        pair.validate_link(self.link_id, self.right_node_id)
        object.__setattr__(self, "channels", pair.to_dict())

    @classmethod
    def from_dict(cls, payload):
        required = {"mode", "link_id", "left_node_id", "right_node_id", "distance_km",
                    "nominal_pair_fidelity", "active", "expected_version", "topology_id"}
        if not isinstance(payload, Mapping) or set(payload) not in (required, required | {"channels"}):
            raise ValueError("invalid link configuration fields; no measurements or credentials accepted")
        if "channels" in payload and payload["channels"] is None:
            raise ValueError("both channel configurations are required when explicitly supplied")
        return cls(**dict(payload))

    def link(self):
        return SimulatedLink(self.link_id, self.left_node_id, self.right_node_id,
                             self.distance_km, self.nominal_pair_fidelity, self.active, channels=self.channels)


@dataclass(frozen=True)
class SimulatedNodeAction:
    action: str
    node_id: str
    expected_version: int
    topology_id: str = DEFAULT_TOPOLOGY_ID

    def __post_init__(self):
        if not isinstance(self.action, str) or self.action not in {'recalibrate', 'isolate'}:
            raise ValueError('only simulated recalibrate and isolate actions are supported')
        if not isinstance(self.node_id, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,63}', self.node_id):
            raise ValueError('node_id must be a bounded ASCII identifier')
        if type(self.expected_version) is not int or self.expected_version < 0:
            raise ValueError('expected_version must be a nonnegative integer')
        if self.topology_id != DEFAULT_TOPOLOGY_ID:
            raise ValueError('node actions only change the default simulator workspace')

    @classmethod
    def from_dict(cls, payload):
        if not isinstance(payload, Mapping) or set(payload) != {'action', 'node_id', 'expected_version', 'topology_id'}:
            raise ValueError('invalid simulated node action fields')
        return cls(**dict(payload))


# --- Live-network verbs (design §1). Same strictness: exact field sets, bounded ASCII
# identifiers, optimistic version, default workspace only. Locations are operator
# declarations in decimal degrees; they are never resolved, geocoded or measured.

_IDENTIFIER = r'[A-Za-z0-9][A-Za-z0-9_.-]{0,63}'


def _identifier(name, value):
    if not isinstance(value, str) or not re.fullmatch(_IDENTIFIER, value):
        raise ValueError(f'{name} must be 1..64 ASCII letters, digits, dots, underscores or hyphens')


def _version_and_workspace(expected_version, topology_id):
    if type(expected_version) is not int or expected_version < 0:
        raise ValueError('expected_version must be a nonnegative integer')
    if topology_id != DEFAULT_TOPOLOGY_ID:
        raise ValueError('network inventory verbs only change the default simulator workspace')


def _declared_location(latitude, longitude, place):
    """Return ``(latitude, longitude)`` as floats, or ``(None, None)`` for an unlocated node."""
    if not isinstance(place, str) or len(place) > MAX_PLACE_CHARS or not place.isprintable():
        raise ValueError(f'place must be printable text of at most {MAX_PLACE_CHARS} characters')
    if latitude is None and longitude is None:
        if place:
            raise ValueError('place requires latitude and longitude; send an empty place for an unlocated node')
        return None, None
    if latitude is None or longitude is None:
        raise ValueError('latitude and longitude must both be null or both be numbers')
    return validate_coordinates(latitude, longitude)


def _location_record(latitude, longitude, place, set_at_utc):
    return {'latitude': latitude, 'longitude': longitude, 'place': place,
            'source': LOCATION_SOURCE, 'set_at_utc': set_at_utc}


@dataclass(frozen=True)
class AddNetworkNode:
    node_id: str
    role: str
    address: str
    port: int
    latitude: float | None
    longitude: float | None
    place: str
    expected_version: int
    topology_id: str = DEFAULT_TOPOLOGY_ID

    def __post_init__(self):
        _identifier('node_id', self.node_id)
        if not isinstance(self.role, str) or self.role not in {'endpoint', 'repeater'}:
            raise ValueError('only endpoint and repeater roles are implemented')
        if not isinstance(self.address, str) or len(self.address) > 64:
            raise ValueError('address must be an IP literal, never a URL or hostname')
        ipaddress.ip_address(self.address)
        if type(self.port) is not int or not 1 <= self.port <= 65535:
            raise ValueError('port must be an integer in 1..65535')
        latitude, longitude = _declared_location(self.latitude, self.longitude, self.place)
        object.__setattr__(self, 'latitude', latitude)
        object.__setattr__(self, 'longitude', longitude)
        _version_and_workspace(self.expected_version, self.topology_id)

    @classmethod
    def from_dict(cls, payload):
        required = {'node_id', 'role', 'address', 'port', 'latitude', 'longitude', 'place',
                    'expected_version', 'topology_id'}
        if not isinstance(payload, Mapping) or set(payload) != required:
            raise ValueError('invalid add-network-node fields; credentials and equipment settings are not accepted')
        return cls(**dict(payload))

    @property
    def located(self):
        return self.latitude is not None

    def location(self, set_at_utc):
        """Persisted ``telemetry["location"]`` value, or ``None`` for an unlocated node."""
        return _location_record(self.latitude, self.longitude, self.place, set_at_utc) if self.located else None

    def node(self, set_at_utc=None):
        telemetry = {'inventory': {'origin': 'orion-console', 'address': self.address, 'port': self.port,
                                   'scope': 'metadata_only_no_connection'}}
        if self.located:
            if set_at_utc is None:
                raise ValueError('a located node needs the timestamp of its declaration')
            telemetry['location'] = self.location(set_at_utc)
        return SimulatedNodeState(self.node_id, self.role, 0, 'IDLE', telemetry=telemetry)


@dataclass(frozen=True)
class SetNodeLocation:
    node_id: str
    latitude: float | None
    longitude: float | None
    place: str
    expected_version: int
    topology_id: str = DEFAULT_TOPOLOGY_ID

    def __post_init__(self):
        _identifier('node_id', self.node_id)
        latitude, longitude = _declared_location(self.latitude, self.longitude, self.place)
        object.__setattr__(self, 'latitude', latitude)
        object.__setattr__(self, 'longitude', longitude)
        _version_and_workspace(self.expected_version, self.topology_id)

    @classmethod
    def from_dict(cls, payload):
        required = {'node_id', 'latitude', 'longitude', 'place', 'expected_version', 'topology_id'}
        if not isinstance(payload, Mapping) or set(payload) != required:
            raise ValueError('invalid node-location fields')
        return cls(**dict(payload))

    @property
    def located(self):
        return self.latitude is not None

    def location(self, set_at_utc):
        return _location_record(self.latitude, self.longitude, self.place, set_at_utc) if self.located else None


@dataclass(frozen=True)
class SetNodeIsolation:
    node_id: str
    isolated: bool
    expected_version: int
    topology_id: str = DEFAULT_TOPOLOGY_ID

    def __post_init__(self):
        _identifier('node_id', self.node_id)
        if type(self.isolated) is not bool:
            raise ValueError('isolated must be boolean')
        _version_and_workspace(self.expected_version, self.topology_id)

    @classmethod
    def from_dict(cls, payload):
        if not isinstance(payload, Mapping) or set(payload) != {'node_id', 'isolated', 'expected_version', 'topology_id'}:
            raise ValueError('invalid node-isolation fields')
        return cls(**dict(payload))


@dataclass(frozen=True)
class SetLinkActive:
    link_id: str
    active: bool
    expected_version: int
    topology_id: str = DEFAULT_TOPOLOGY_ID

    def __post_init__(self):
        _identifier('link_id', self.link_id)
        if type(self.active) is not bool:
            raise ValueError('active must be boolean')
        _version_and_workspace(self.expected_version, self.topology_id)

    @classmethod
    def from_dict(cls, payload):
        if not isinstance(payload, Mapping) or set(payload) != {'link_id', 'active', 'expected_version', 'topology_id'}:
            raise ValueError('invalid link-activation fields')
        return cls(**dict(payload))


@dataclass(frozen=True)
class RecalibrateNode:
    node_id: str
    expected_version: int
    topology_id: str = DEFAULT_TOPOLOGY_ID

    def __post_init__(self):
        _identifier('node_id', self.node_id)
        _version_and_workspace(self.expected_version, self.topology_id)

    @classmethod
    def from_dict(cls, payload):
        if not isinstance(payload, Mapping) or set(payload) != {'node_id', 'expected_version', 'topology_id'}:
            raise ValueError('invalid node-recalibration fields; the report is produced by the engine')
        return cls(**dict(payload))
