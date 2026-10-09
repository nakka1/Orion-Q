"""Read-only projection of timestamped protocol histories published by the backend."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from math import isfinite


@dataclass(frozen=True)
class ProtocolSeries:
    key: str
    session_id: str
    technology: str
    metric: str
    label: str
    unit: str
    points: tuple
    bounds: tuple = (None, None)
    origin: str = "not_published"
    source_id: str = "not_published"
    scope: str = "session"
    time_domain: str = "not_published"
    clock_kind: str = "not_published"
    reason: str = ""
    window_s: float = 120.


def finite(value):
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) and isfinite(value) else None


def protocol_series_from_state(state, *, node_id=None) -> tuple[ProtocolSeries, ...]:
    """No history accumulation, resampling, interpolation or metric derivation."""
    if not isinstance(state, Mapping):
        return ()
    raw = state.get("protocol_series", ())
    if not isinstance(raw, (list, tuple)):
        return ()
    result = []
    for item in raw:
        if not isinstance(item, Mapping) or (node_id is not None and node_id not in item.get("node_ids", ())):
            continue
        if not item.get("key") or not item.get("metric") or not item.get("unit"):
            continue
        points, reason = [], ""
        for sample in item.get("samples", ()):
            if not isinstance(sample, Mapping):
                continue
            at_s = finite(sample.get("at_s"))
            if at_s is None or (points and at_s <= points[-1][0]):
                continue
            value = finite(sample.get("value"))
            points.append((at_s, value))
            reason = str(sample.get("reason") or ("observável ausente" if value is None else ""))
        bounds = item.get("bounds", (None, None))
        if not isinstance(bounds, (list, tuple)) or len(bounds) != 2:
            bounds = (None, None)
        result.append(ProtocolSeries(
            key=str(item["key"]), session_id=str(item.get("session_id", "")),
            technology=str(item.get("technology", "")), metric=str(item["metric"]),
            label=str(item.get("label") or item["metric"]), unit=str(item["unit"]), points=tuple(points),
            bounds=tuple(finite(bound) for bound in bounds), origin=str(item.get("origin", "not_published")),
            source_id=str(item.get("source_id", "not_published")), scope=str(item.get("scope", "session")),
            time_domain=str(item.get("time_domain", "not_published")), clock_kind=str(item.get("clock_kind", "not_published")),
            reason=reason, window_s=finite(item.get("window_s")) or 120.))
    return tuple(result)
