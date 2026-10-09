"""Geodesic distances between operator-declared node locations (pure stdlib).

Coordinates are declarations made by the operator in the simulator inventory;
nothing here measures a fibre, opens a socket or locates a device.
"""
from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any

# WGS-84 ellipsoid.
WGS84_A_M = 6378137.0
WGS84_F = 1.0 / 298.257223563
WGS84_B_M = WGS84_A_M * (1.0 - WGS84_F)
MEAN_EARTH_RADIUS_KM = 6371.0088
MAX_PLACE_CHARS = 80
LOCATION_SOURCE = "operator_declared"
# Stored link lengths are rounded to the millimetre; Vincenty itself is good to ~0.5 mm.
DISTANCE_DECIMALS_KM = 6
_MAX_ITERATIONS = 200
_TOLERANCE = 1e-12


def validate_coordinates(latitude: Any, longitude: Any) -> tuple[float, float]:
    """Return ``(latitude, longitude)`` as floats or raise ``ValueError``."""

    values = []
    for name, value, limit in (("latitude", latitude, 90.0), ("longitude", longitude, 180.0)):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"{name} must be a number in decimal degrees")
        numeric = float(value)
        if not math.isfinite(numeric) or not -limit <= numeric <= limit:
            raise ValueError(f"{name} must be a finite number in [-{limit:g}, {limit:g}]")
        values.append(numeric)
    return values[0], values[1]


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance on the mean-radius sphere (fallback only)."""

    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    half_dphi = (phi2 - phi1) / 2.0
    half_dlambda = math.radians(lon2 - lon1) / 2.0
    h = math.sin(half_dphi) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(half_dlambda) ** 2
    return 2.0 * MEAN_EARTH_RADIUS_KM * math.asin(math.sqrt(min(1.0, max(0.0, h))))


def _vincenty_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float | None:
    """Vincenty inverse solution; ``None`` when the iteration does not converge."""

    u1 = math.atan((1.0 - WGS84_F) * math.tan(math.radians(lat1)))
    u2 = math.atan((1.0 - WGS84_F) * math.tan(math.radians(lat2)))
    big_l = math.radians(lon2 - lon1)
    # Longitude differences are taken the short way round the ellipsoid.
    big_l = (big_l + math.pi) % (2.0 * math.pi) - math.pi
    sin_u1, cos_u1 = math.sin(u1), math.cos(u1)
    sin_u2, cos_u2 = math.sin(u2), math.cos(u2)
    lam = big_l
    for _ in range(_MAX_ITERATIONS):
        sin_lam, cos_lam = math.sin(lam), math.cos(lam)
        sin_sigma = math.hypot(cos_u2 * sin_lam, cos_u1 * sin_u2 - sin_u1 * cos_u2 * cos_lam)
        if sin_sigma == 0.0:
            return 0.0  # coincident points
        cos_sigma = sin_u1 * sin_u2 + cos_u1 * cos_u2 * cos_lam
        sigma = math.atan2(sin_sigma, cos_sigma)
        sin_alpha = cos_u1 * cos_u2 * sin_lam / sin_sigma
        cos_sq_alpha = 1.0 - sin_alpha ** 2
        # Both points on the equator: cos_sq_alpha == 0 and the 2σm term vanishes.
        cos_2sigma_m = cos_sigma - 2.0 * sin_u1 * sin_u2 / cos_sq_alpha if cos_sq_alpha > 0.0 else 0.0
        c = WGS84_F / 16.0 * cos_sq_alpha * (4.0 + WGS84_F * (4.0 - 3.0 * cos_sq_alpha))
        previous = lam
        lam = big_l + (1.0 - c) * WGS84_F * sin_alpha * (
            sigma + c * sin_sigma * (cos_2sigma_m + c * cos_sigma * (-1.0 + 2.0 * cos_2sigma_m ** 2)))
        if abs(lam) > math.pi:
            return None  # nearly antipodal: the classic formula does not converge
        if abs(lam - previous) < _TOLERANCE:
            break
    else:
        return None
    u_sq = cos_sq_alpha * (WGS84_A_M ** 2 - WGS84_B_M ** 2) / WGS84_B_M ** 2
    big_a = 1.0 + u_sq / 16384.0 * (4096.0 + u_sq * (-768.0 + u_sq * (320.0 - 175.0 * u_sq)))
    big_b = u_sq / 1024.0 * (256.0 + u_sq * (-128.0 + u_sq * (74.0 - 47.0 * u_sq)))
    delta_sigma = big_b * sin_sigma * (cos_2sigma_m + big_b / 4.0 * (
        cos_sigma * (-1.0 + 2.0 * cos_2sigma_m ** 2)
        - big_b / 6.0 * cos_2sigma_m * (-3.0 + 4.0 * sin_sigma ** 2) * (-3.0 + 4.0 * cos_2sigma_m ** 2)))
    metres = WGS84_B_M * big_a * (sigma - delta_sigma)
    if not math.isfinite(metres) or metres < 0.0:
        return None
    return metres / 1000.0


def geodesic_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """WGS-84 geodesic length in kilometres (Vincenty, haversine when it fails)."""

    lat1, lon1 = validate_coordinates(lat1, lon1)
    lat2, lon2 = validate_coordinates(lat2, lon2)
    if lat1 == lat2 and lon1 == lon2:
        return 0.0
    distance = _vincenty_km(lat1, lon1, lat2, lon2)
    return haversine_km(lat1, lon1, lat2, lon2) if distance is None else distance


def _field(item: Any, name: str) -> Any:
    return item.get(name) if isinstance(item, Mapping) else getattr(item, name, None)


def node_location(node: Any) -> dict[str, Any] | None:
    """Return a copy of ``telemetry["location"]`` or ``None`` when absent/invalid.

    Accepts a ``SimulatedNodeState`` or its ``to_dict()`` form. A node without a
    usable location is simply unlocated; this never raises for stored data.
    """

    telemetry = _field(node, "telemetry")
    if not isinstance(telemetry, Mapping):
        return None
    location = telemetry.get("location")
    if not isinstance(location, Mapping):
        return None
    try:
        latitude, longitude = validate_coordinates(location.get("latitude"), location.get("longitude"))
    except ValueError:
        return None
    place = location.get("place", "")
    if not isinstance(place, str) or len(place) > MAX_PLACE_CHARS:
        return None
    return {**dict(location), "latitude": latitude, "longitude": longitude, "place": place}


def link_geodesic_km(topology: Any, link: Any) -> float | None:
    """Geodesic length of ``link`` or ``None`` unless BOTH endpoints are located.

    ``topology``/``link`` may be the dataclasses or their ``to_dict()`` forms.
    The result is rounded to the millimetre, the value persisted as ``distance_km``.
    """

    nodes = _field(topology, "nodes") or ()
    by_id = {_field(item, "node_id"): item for item in nodes}
    ends = []
    for name in ("left_node_id", "right_node_id"):
        node = by_id.get(_field(link, name))
        location = None if node is None else node_location(node)
        if location is None:
            return None
        ends.append(location)
    return round(geodesic_km(ends[0]["latitude"], ends[0]["longitude"],
                             ends[1]["latitude"], ends[1]["longitude"]), DISTANCE_DECIMALS_KM)


__all__ = [
    "DISTANCE_DECIMALS_KM",
    "LOCATION_SOURCE",
    "MAX_PLACE_CHARS",
    "MEAN_EARTH_RADIUS_KM",
    "geodesic_km",
    "haversine_km",
    "link_geodesic_km",
    "node_location",
    "validate_coordinates",
]
