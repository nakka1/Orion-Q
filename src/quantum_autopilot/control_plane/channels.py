"""Paired software ports. A declaration is never evidence of physical connectivity."""
from dataclasses import asdict, dataclass
import math
import re


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,95}", value):
        raise ValueError("channel identifiers must be 1..96 bounded ASCII characters")
    return value


@dataclass(frozen=True)
class ClassicalChannel:
    channel_id: str
    protocol: str = "local-envelope-v1"
    max_message_bytes: int = 512 * 1024
    max_age_s: float = 2.0
    enabled: bool = True

    def __post_init__(self):
        identifier(self.channel_id)
        if self.protocol != "local-envelope-v1":
            raise ValueError("only the local classical envelope port is implemented; no socket is opened")
        if type(self.max_message_bytes) is not int or not 1024 <= self.max_message_bytes <= 8 * 1024 * 1024:
            raise ValueError("classical message limit must be 1 KiB..8 MiB")
        if type(self.max_age_s) not in (int, float) or not math.isfinite(self.max_age_s) or not 0 < self.max_age_s <= 30:
            raise ValueError("classical maximum age must be a finite value in (0,30] seconds")
        if type(self.enabled) is not bool:
            raise ValueError("classical enabled must be boolean")


@dataclass(frozen=True)
class QuantumChannel:
    channel_id: str
    receiver_id: str
    protocol: str = "bb84-statistical-record-v1"
    enabled: bool = True

    def __post_init__(self):
        identifier(self.channel_id)
        identifier(self.receiver_id)
        if self.protocol not in ("bb84-statistical-record-v1", "quantum-operation-record-v1"):
            raise ValueError("only statistical optical records are supported; physical quantum transmission is unavailable")
        if type(self.enabled) is not bool:
            raise ValueError("quantum enabled must be boolean")


@dataclass(frozen=True)
class ChannelPair:
    association_id: str
    classical: ClassicalChannel
    quantum: QuantumChannel

    def __post_init__(self):
        identifier(self.association_id)
        if not isinstance(self.classical, ClassicalChannel) or not isinstance(self.quantum, QuantumChannel):
            raise ValueError("both classical and quantum channel configurations are required")
        if self.classical.channel_id == self.quantum.channel_id:
            raise ValueError("classical and quantum channels require distinct IDs")

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, value):
        if not isinstance(value, dict) or set(value) != {"association_id", "classical", "quantum"}:
            raise ValueError("both channel configurations and their association_id are required")
        try:
            return cls(value["association_id"], ClassicalChannel(**value["classical"]), QuantumChannel(**value["quantum"]))
        except TypeError as exc:
            raise ValueError("invalid classical or quantum channel parameters") from exc

    def validate_link(self, link_id, receiver_id):
        if self.association_id != link_id or self.quantum.receiver_id != receiver_id:
            raise ValueError("the channel pair must identify this link and its receiving endpoint")


def local_pair(link_id, receiver_id):
    """Explicit defaults for newly constructed software blueprints, never legacy readback."""
    return ChannelPair(link_id, ClassicalChannel(link_id + ".classical"),
                       QuantumChannel(link_id + ".quantum", receiver_id)).to_dict()
