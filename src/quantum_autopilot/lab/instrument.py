"""Read-only instrument limits; optical experiments cannot override these facts."""
from dataclasses import asdict, dataclass
import math
from typing import Any

from .photon import AcquisitionError, PhotonConfig


@dataclass(frozen=True)
class InstrumentCapabilities:
    adapter_id: str
    channels: tuple[int, ...]
    trigger_ranges_v: dict[int, tuple[float, float]]
    timestamp_unit: str = "ps"
    origin: str = "simulated"
    hardware_validated: bool = False

    def validate(self):
        if not self.adapter_id or self.timestamp_unit != "ps" or not self.channels:
            raise AcquisitionError("instrument capability contract incomplete")
        if len(set(self.channels)) != len(self.channels) or any(type(c) is not int or c <= 0 for c in self.channels):
            raise AcquisitionError("instrument physical channels must be positive, unique integers")
        for channel in self.channels:
            limits = self.trigger_ranges_v.get(channel)
            if limits is None or len(limits) != 2 or any(isinstance(v, bool) or not isinstance(v, (int, float))
                    or not math.isfinite(v) for v in limits) or limits[0] > limits[1]:
                raise AcquisitionError("invalid native trigger range")

    def validate_configuration(self, config: PhotonConfig):
        self.validate()
        config.validate()
        if not set(config.channels) <= set(self.channels):
            raise AcquisitionError("configured channels are unavailable under the instrument license")
        if config.reference_channel is not None and config.reference_channel not in self.channels:
            raise AcquisitionError("reference channel unavailable under the instrument license")
        for channel, value in zip(config.channels, config.trigger_levels_v):
            low, high = self.trigger_ranges_v[channel]
            if not low <= value <= high:
                raise AcquisitionError("requested trigger is outside the native channel range")
        if any(not -(2**63) <= value < 2**63 for value in config.input_delays_ps):
            raise AcquisitionError("input delay exceeds signed 64-bit picoseconds")
        if not 1 <= round(1e12 / config.reference_frequency_hz) < 2**63:
            raise AcquisitionError("reference clock period exceeds integer ps domain")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value):
        return cls(**{**value, "channels": tuple(value["channels"]),
                     "trigger_ranges_v": {int(k): tuple(v) for k, v in value["trigger_ranges_v"].items()}})
