"""Versioned acquisition contracts shared by simulations and device/file adapters.

Sources report raw counts and local operation outcomes, never final fidelities or
keys. Commands name BOTH channels. No authenticated physical adapter ships here.
"""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass, field
from typing import Protocol, runtime_checkable

SCHEMA = 'orion.quantum-technology.v1'
TECHNOLOGIES = ('entanglement_swapping', 'tf_qkd', 'purification', 'satellite_doppler', 'quantum_memory', 'satellite_isl')
OPERATIONS = ('generate', 'bsm', 'purify', 'readout', 'tf_block', 'satellite_block', 'isl_block')


class ProtocolError(ValueError):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


def canonical(value) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False)


def digest(value) -> str:
    return hashlib.sha256(canonical(value).encode('utf-8')).hexdigest()


_OBSERVATION_SCALARS = frozenset((str, int, float, bool, type(None)))


def _copy_observation(value):
    """Clone raw JSON containers while sharing only immutable scalar leaves."""
    kind = type(value)
    if kind in _OBSERVATION_SCALARS:
        return value
    if kind is dict:
        return {key: item if type(item) in _OBSERVATION_SCALARS else _copy_observation(item)
                for key, item in value.items()}
    if kind is list:
        return [item if type(item) in _OBSERVATION_SCALARS else _copy_observation(item) for item in value]
    if kind is tuple:
        return tuple(item if type(item) in _OBSERVATION_SCALARS else _copy_observation(item) for item in value)
    from copy import deepcopy
    return deepcopy(value)


def number(value, name, minimum=0., maximum=None):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ProtocolError('invalid_parameter', f'{name}: finite number required')
    if value < minimum or (maximum is not None and value > maximum):
        raise ProtocolError('invalid_parameter', f'{name}: outside [{minimum}, {maximum}]')
    return float(value)


@dataclass(frozen=True)
class ProtocolConfig:
    session_id: str
    technologies: tuple[str, ...]
    endpoints: tuple[str, str]
    path: tuple[str, ...]
    link_ids: tuple[str, ...]
    parameters: dict = field(default_factory=dict)
    seed: int = 42
    enabled: bool = True
    scenario: str = 'nominal'
    parameter_sources: tuple[str, ...] = ()
    infrastructure: str = 'proposed_simulation'
    schema: str = SCHEMA

    def __post_init__(self):
        for name in ('technologies', 'endpoints', 'path', 'link_ids', 'parameter_sources'):
            object.__setattr__(self, name, tuple(getattr(self, name)))
        if self.schema != SCHEMA or not self.session_id or not self.technologies:
            raise ProtocolError('invalid_config', 'version, id and technologies required')
        if len(set(self.technologies)) != len(self.technologies) or any(x not in TECHNOLOGIES for x in self.technologies):
            raise ProtocolError('unsupported_technology', 'unknown or duplicate technology')
        if len(self.endpoints) != 2 or self.endpoints[0] == self.endpoints[1]:
            raise ProtocolError('invalid_config', 'two distinct endpoints required')
        if len(self.path) < 2 or len(set(self.path)) != len(self.path) or tuple((self.path[0], self.path[-1])) != self.endpoints:
            raise ProtocolError('invalid_config', 'acyclic path must terminate at both endpoints')
        if len(self.link_ids) != len(self.path) - 1 or len(set(self.link_ids)) != len(self.link_ids):
            raise ProtocolError('invalid_config', 'one distinct configured link per path segment required')
        if type(self.seed) is not int or self.seed < 0 or type(self.enabled) is not bool:
            raise ProtocolError('invalid_config', 'non-negative seed and explicit boolean enable required')
        if self.scenario not in ('nominal', 'degraded', 'unavailable', 'recovery'):
            raise ProtocolError('invalid_config', 'unsupported scenario')
        if not isinstance(self.parameters, dict):
            raise ProtocolError('invalid_config', 'parameters must be an object')
        canonical(self.parameters)

    def to_dict(self):
        return json.loads(canonical(asdict(self)))

    @classmethod
    def from_dict(cls, value):
        return cls(**value)


@dataclass(frozen=True)
class DeviceCommand:
    command_id: str
    session_id: str
    operation: str
    node_ids: tuple[str, ...]
    quantum_channel_ids: tuple[str, ...]
    classical_channel_ids: tuple[str, ...]
    emitted_at_s: float
    deadline_s: float
    attempt: int
    arguments: dict = field(default_factory=dict)

    def __post_init__(self):
        if self.operation not in OPERATIONS or not self.command_id or not self.session_id:
            raise ProtocolError('invalid_command', 'unknown operation or absent identity')
        if not self.quantum_channel_ids or len(self.quantum_channel_ids) != len(self.classical_channel_ids):
            raise ProtocolError('unpaired_channels', 'both associated channels are mandatory')
        if len(set(self.quantum_channel_ids + self.classical_channel_ids)) != 2 * len(self.quantum_channel_ids):
            raise ProtocolError('unpaired_channels', 'channel identifiers must be distinct')
        number(self.emitted_at_s, 'emission time')
        number(self.deadline_s, 'deadline', self.emitted_at_s)
        if type(self.attempt) is not int or self.attempt < 1:
            raise ProtocolError('invalid_command', 'positive attempt required')
        canonical(self.arguments)

    def to_dict(self):
        return asdict(self)


@dataclass(frozen=True)
class Acquisition:
    command_id: str
    session_id: str
    operation: str
    source_id: str
    origin: str
    sequence: int
    observed_at_s: float
    clock_offset_s: float
    quantum_channel_ids: tuple[str, ...]
    classical_channel_ids: tuple[str, ...]
    raw: dict
    schema: str = SCHEMA

    def __post_init__(self):
        object.__setattr__(self, 'quantum_channel_ids', tuple(self.quantum_channel_ids))
        object.__setattr__(self, 'classical_channel_ids', tuple(self.classical_channel_ids))
        if self.schema != SCHEMA or self.origin not in ('simulated', 'measurement_file', 'physical'):
            raise ProtocolError('invalid_acquisition', 'version and declared origin required')
        if not self.source_id or type(self.sequence) is not int or self.sequence < 1:
            raise ProtocolError('invalid_acquisition', 'source id and positive sequence required')
        number(self.observed_at_s, 'observation time')
        number(self.clock_offset_s, 'clock offset', -1e9, 1e9)
        if not isinstance(self.raw, dict) or any(x in self.raw for x in ('final_fidelity', 'final_key', 'secure_key', 'skr_bps')):
            raise ProtocolError('precomputed_result', 'only raw operation observations accepted')
        canonical(self.raw)

    def to_dict(self):
        # Raw observations are already validated JSON-compatible data. Avoid
        # dataclass inspection and generic deepcopy for every integer time tag;
        # retain an independent copy, including tuple/list container types.
        return {'command_id': self.command_id, 'session_id': self.session_id,
                'operation': self.operation, 'source_id': self.source_id, 'origin': self.origin,
                'sequence': self.sequence, 'observed_at_s': self.observed_at_s,
                'clock_offset_s': self.clock_offset_s, 'quantum_channel_ids': self.quantum_channel_ids,
                'classical_channel_ids': self.classical_channel_ids,
                'raw': _copy_observation(self.raw), 'schema': self.schema}

    def canonical_json(self):
        """Serialize immediately; keep ``to_dict``'s independent deep copy API.

        The envelope is small but raw TDC arrays may contain 100,000 tags per
        detector. JSON consumes the original arrays synchronously without a
        redundant recursive dataclass copy; no mutable alias escapes.
        """
        return canonical({
            'command_id':self.command_id,'session_id':self.session_id,
            'operation':self.operation,'source_id':self.source_id,'origin':self.origin,
            'sequence':self.sequence,'observed_at_s':self.observed_at_s,
            'clock_offset_s':self.clock_offset_s,'quantum_channel_ids':self.quantum_channel_ids,
            'classical_channel_ids':self.classical_channel_ids,'raw':self.raw,'schema':self.schema})

    @classmethod
    def from_dict(cls, value):
        return cls(**value)


@runtime_checkable
class AcquisitionSource(Protocol):
    source_id: str
    origin: str
    physical_validated: bool

    def acquire(self, command: DeviceCommand, config: ProtocolConfig, environment: dict) -> Acquisition | None: ...


@runtime_checkable
class ProtocolAdapter(Protocol):
    technology_id: str
    required_capabilities: tuple[str, ...]

    def validate(self, config: ProtocolConfig, topology) -> None: ...

    def process(self, raw: dict, context: dict) -> dict: ...


def validate_binding(acquisition: Acquisition, command: DeviceCommand, *, last_sequence: int,
                     source_id: str, origin: str, now_s: float, sync_tolerance_s: float):
    if (acquisition.command_id, acquisition.session_id, acquisition.operation) != (command.command_id, command.session_id, command.operation):
        raise ProtocolError('wrong_binding', 'observation does not belong to this command')
    if acquisition.source_id != source_id or acquisition.origin != origin:
        raise ProtocolError('wrong_source', 'declared observation source differs from installed adapter')
    if acquisition.sequence <= last_sequence:
        raise ProtocolError('replay', 'observation sequence is stale')
    if acquisition.quantum_channel_ids != command.quantum_channel_ids or acquisition.classical_channel_ids != command.classical_channel_ids:
        raise ProtocolError('wrong_channel', 'observation channel association differs from command')
    if acquisition.observed_at_s < command.emitted_at_s or acquisition.observed_at_s > now_s:
        raise ProtocolError('causality', 'observation before request or in the future')
    if now_s > command.deadline_s:
        raise ProtocolError('timeout', 'observation outside command deadline')
    if abs(acquisition.clock_offset_s) > sync_tolerance_s:
        raise ProtocolError('unsynchronized', 'source clock outside synchronization tolerance')
