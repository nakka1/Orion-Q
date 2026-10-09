"""Numeric observations before presentation. No Qt, inference or device authority."""
from dataclasses import dataclass
from datetime import datetime, timezone
import math


# Wire names define canonical units; percentages and kbit/s are presentation only.
QUANTITIES = {
    "fidelity": ("FIDELITY", "fraction"), "qber": ("QBER", "fraction"),
    "skr_bps": ("SECRET KEY RATE", "bit/s"), "ber": ("BER", "fraction"),
    "osnr_db": ("OSNR", "dB"), "power_dbm": ("POWER", "dBm"),
}
OPTIONAL_LINK_METADATA = {
    "fidelity_method", "fidelity_reference", "clock_id", "integration_start_utc",
    "integration_end_utc", "standard_uncertainties",
}


def text_identifier(value, name):
    if (not isinstance(value, str) or not value.strip() or len(value) > 160
            or any(ord(c) < 32 or c in "<>" for c in value)):
        raise ValueError(f"{name} must be bounded nonempty text")
    return value


def timestamp(value):
    text_identifier(value, "timestamp")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.utcoffset() is None:
        raise ValueError("timestamp requires an explicit timezone")
    return parsed.astimezone(timezone.utc)


def finite_number(value, name):
    try:
        valid = type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        valid = False
    if not valid:
        raise ValueError(f"{name} must be a finite number, not a boolean or formatted string")
    return value


@dataclass(frozen=True)
class Observation:
    quantity: str
    value: float
    unit: str
    source_id: str
    campaign_id: str
    entity_id: str
    channel: str
    device_id: str
    measurement_id: str
    acquired_at: str
    provenance: str
    clock_domain: str = "utc"
    clock_id: str | None = None
    received_at_utc: str | None = None
    integration_start: str | None = None
    integration_end: str | None = None
    standard_uncertainty: float | None = None
    fidelity_method: str | None = None
    fidelity_reference: str | None = None
    schema: str = "orion.observation.v1"

    def __post_init__(self):
        if self.schema != "orion.observation.v1" or self.quantity not in QUANTITIES:
            raise ValueError("unsupported observation schema or quantity")
        if self.unit != QUANTITIES[self.quantity][1]:
            raise ValueError("observation requires the canonical unit")
        finite_number(self.value, "value")
        if self.unit == "fraction" and not 0 <= self.value <= 1:
            raise ValueError("fractions must be in [0,1]")
        if self.quantity == "skr_bps" and self.value < 0:
            raise ValueError("SKR must be nonnegative")
        for name in ("source_id", "campaign_id", "entity_id", "channel", "device_id", "measurement_id"):
            text_identifier(getattr(self, name), name)
        if self.provenance not in {"measured", "simulated", "derived", "predicted"}:
            raise ValueError("invalid observation provenance")
        if self.clock_domain not in {"utc", "simulation"}:
            raise ValueError("invalid clock domain")
        if self.clock_id is not None:
            text_identifier(self.clock_id, "clock_id")
        acquired = timestamp(self.acquired_at)
        if self.received_at_utc is not None:
            timestamp(self.received_at_utc)
        if self.provenance == "measured" and (self.clock_domain != "utc" or self.received_at_utc is None):
            raise ValueError("measured observations require UTC acquisition and host receipt timestamps")
        if (self.integration_start is None) != (self.integration_end is None):
            raise ValueError("integration window requires both endpoints")
        if self.integration_start is not None:
            start, end = timestamp(self.integration_start), timestamp(self.integration_end)
            if not start <= end <= acquired:
                raise ValueError("integration must end no later than the acquisition completion timestamp")
        if self.standard_uncertainty is not None:
            if finite_number(self.standard_uncertainty, "standard uncertainty") < 0:
                raise ValueError("standard uncertainty must be nonnegative")
        if self.quantity == "fidelity":
            if self.fidelity_method is not None:
                text_identifier(self.fidelity_method, "fidelity_method")
            if self.fidelity_reference is not None:
                text_identifier(self.fidelity_reference, "fidelity_reference")
            if self.provenance == "measured" and self.fidelity_method not in {"state_tomography", "measured_fidelity"}:
                raise ValueError("measured fidelity requires an explicit measurement method")
        elif self.fidelity_method is not None or self.fidelity_reference is not None:
            raise ValueError("fidelity metadata cannot describe another quantity")

    @property
    def receipt_delay_s(self):
        """Signed host-read minus source time, not calibrated network latency."""
        if self.clock_domain != "utc" or self.received_at_utc is None:
            return None
        return (timestamp(self.received_at_utc) - timestamp(self.acquired_at)).total_seconds()

    @property
    def time_quality(self):
        if self.clock_domain == "simulation":
            return "simulation_clock"
        if self.receipt_delay_s is not None and self.receipt_delay_s < 0:
            return "source_clock_ahead"
        return "clock_declared_unverified" if self.clock_id else "clock_not_supplied"

    def formatted(self, spec=".5g", *, unit=None):
        """Explicit, limited display conversion; never mutate the stored value."""
        unit = self.unit if unit is None else unit
        factor = 1
        if unit != self.unit:
            if self.unit == "fraction" and unit == "%":
                factor = 100
            elif self.unit == "bit/s" and unit == "kb/s":
                factor = .001
            else:
                raise ValueError("unsupported display conversion")
        return format(self.value * factor, spec)


def measured_link_observations(frame, row, received_at_utc):
    uncertainty = row.get("standard_uncertainties", {})
    if not isinstance(uncertainty, dict) or set(uncertainty) - (set(row) & set(QUANTITIES)):
        raise ValueError("standard_uncertainties must reference supplied metrics only")
    for value in uncertainty.values():
        finite_number(value, "standard uncertainty")
    # Explicit nulls are not a declaration; omit unknown metadata instead.
    for key in OPTIONAL_LINK_METADATA.intersection(row) - {"standard_uncertainties"}:
        text_identifier(row[key], key)
    if "fidelity_reference" in row and "fidelity" not in row:
        raise ValueError("fidelity_reference requires fidelity")
    return tuple(Observation(
        quantity=key, value=row[key], unit=unit, source_id=frame["source_id"],
        campaign_id=frame["campaign_id"], entity_id=row["id"], channel=row["channel"],
        device_id=row["device_id"], measurement_id=row["measurement_id"],
        acquired_at=row["timestamp_utc"], received_at_utc=received_at_utc, provenance="measured",
        clock_id=row.get("clock_id"), integration_start=row.get("integration_start_utc"),
        integration_end=row.get("integration_end_utc"), standard_uncertainty=uncertainty.get(key),
        fidelity_method=row.get("fidelity_method") if key == "fidelity" else None,
        fidelity_reference=row.get("fidelity_reference") if key == "fidelity" else None,
    ) for key, (_, unit) in QUANTITIES.items() if key in row)


def require_declared_alignment(observations, *, max_skew_s):
    """Conservative precondition for future pairing, not proof of synchronization."""
    if not observations or finite_number(max_skew_s, "max skew") < 0:
        raise ValueError("alignment requires observations and a nonnegative tolerance")
    first = observations[0]
    identity = (first.source_id, first.campaign_id, first.entity_id, first.channel, first.device_id, first.clock_id)
    for item in observations:
        if item.provenance != "measured" or item.clock_domain != "utc" or not item.clock_id:
            raise ValueError("alignment requires measured observations and an explicit declared clock")
        if (item.source_id, item.campaign_id, item.entity_id, item.channel, item.device_id, item.clock_id) != identity:
            raise ValueError("incompatible campaign, channel, device or clock for alignment")
        if tuple(timestamp(v) if v else None for v in (item.integration_start, item.integration_end)) != tuple(
                timestamp(v) if v else None for v in (first.integration_start, first.integration_end)):
            raise ValueError("incompatible integration windows")
    times = [timestamp(item.acquired_at) for item in observations]
    if (max(times) - min(times)).total_seconds() > max_skew_s:
        raise ValueError("acquisition timestamps exceed alignment tolerance")
