"""Offline vector base map of Salvador (Bahia, Brazil) and a local metric projection.

Pure standard library: no Qt, no numpy, no network. The geometry lives in
``geo/salvador_v1.json`` next to this module (provenance and licences in
``geo/SOURCES.md``); it is read once and cached.

Coordinates are WGS-84 decimal degrees. Geometry is ``(lon, lat)`` ordered, like
GeoJSON; :func:`geodesic_km` keeps the ``(lat, lon)`` order of
``control_plane.geodesy`` because it delegates to it.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence, Tuple

GEO_DIR = Path(__file__).resolve().parent / "geo"
SALVADOR_FILE = "salvador_v1.json"
BASEMAP_SCHEMA = "orion.geo-basemap.v1"
# UI rule: this string must be shown wherever the map is drawn (the roads layer is ODbL).
OSM_ATTRIBUTION = "© OpenStreetMap contributors"

WGS84_A_M = 6378137.0
WGS84_F = 1.0 / 298.257223563
_WGS84_E2 = WGS84_F * (2.0 - WGS84_F)
# Round lengths a scale bar may show, in kilometres.
SCALE_BAR_LENGTHS_KM = (0.05, 0.1, 0.2, 0.5, 1.0, 2.0, 5.0, 10.0, 20.0, 50.0, 100.0, 200.0, 500.0, 1000.0)

LonLat = Tuple[float, float]
Ring = Tuple[LonLat, ...]


@dataclass(frozen=True)
class BBox:
    """Geographic rectangle in degrees."""

    west: float
    south: float
    east: float
    north: float

    def __post_init__(self) -> None:
        if not (self.west < self.east and self.south < self.north):
            raise ValueError("bbox needs west < east and south < north")

    @property
    def center(self) -> LonLat:
        return (self.west + self.east) / 2.0, (self.south + self.north) / 2.0

    def contains(self, lon: float, lat: float) -> bool:
        return self.west <= lon <= self.east and self.south <= lat <= self.north

    def as_tuple(self) -> Tuple[float, float, float, float]:
        return self.west, self.south, self.east, self.north


def _as_bbox(value: Any) -> BBox:
    if isinstance(value, BBox):
        return value
    if isinstance(value, Mapping):
        return BBox(float(value["west"]), float(value["south"]), float(value["east"]), float(value["north"]))
    west, south, east, north = value
    return BBox(float(west), float(south), float(east), float(north))


def _ring_contains(ring: Ring, lon: float, lat: float) -> bool:
    """Even-odd ray casting; the ring is closed (first point == last point)."""

    inside = False
    x0, y0 = ring[0]
    for x1, y1 in ring[1:]:
        if (y0 > lat) != (y1 > lat) and lon < (x1 - x0) * (lat - y0) / (y1 - y0) + x0:
            inside = not inside
        x0, y0 = x1, y1
    return inside


@dataclass(frozen=True)
class LandPolygon:
    """One polygon: ``rings[0]`` is the outer boundary, further rings are holes."""

    name: str
    rings: Tuple[Ring, ...]
    bbox: BBox
    area_km2: float = 0.0

    @property
    def outer(self) -> Ring:
        return self.rings[0]

    @property
    def holes(self) -> Tuple[Ring, ...]:
        return self.rings[1:]

    def contains(self, lon: float, lat: float) -> bool:
        if not self.bbox.contains(lon, lat):
            return False
        inside = False
        for ring in self.rings:
            if _ring_contains(ring, lon, lat):
                inside = not inside
        return inside


@dataclass(frozen=True)
class Road:
    """A named road drawn as one or more polylines (dual carriageways are separate lines)."""

    name: str  # '' for unnamed connector segments: draw, do not label
    road_class: str  # motorway | trunk | primary | secondary (JSON key "class")
    lines: Tuple[Ring, ...]
    ref: str = ""  # e.g. BR-324, BA-526
    alias: str = ""  # popular name from the OSM alt_name tag, e.g. Avenida Paralela
    source: str = ""  # osm | dnit_snv
    drawn_km: float = 0.0  # total drawn length; both carriageways counted


@dataclass(frozen=True)
class Place:
    """Label point."""

    name: str
    lon: float
    lat: float
    kind: str  # neighbourhood | landmark | airport | island | municipality
    rank: int = 2  # 1 = show first, 2 = show when zoomed in
    source: str = ""


@dataclass(frozen=True)
class WaterLabel:
    """Anchor for a water-body name (label placement, not a surveyed position)."""

    name: str
    lon: float
    lat: float


@dataclass(frozen=True, eq=False)
class BaseMap:
    land: Tuple[LandPolygon, ...]
    municipality: Tuple[LandPolygon, ...]
    roads: Tuple[Road, ...]
    places: Tuple[Place, ...]
    water_labels: Tuple[WaterLabel, ...]
    bbox: BBox
    attribution: str
    meta: Mapping[str, Any]

    def contains_land(self, lon: float, lat: float) -> bool:
        """True when the point lies on land (any municipality in the data extent)."""

        return any(polygon.contains(lon, lat) for polygon in self.land)

    def in_municipality(self, lon: float, lat: float) -> bool:
        """True when the point lies on the land of the municipality of Salvador."""

        return any(polygon.contains(lon, lat) for polygon in self.municipality)

    def in_view(self, lon: float, lat: float) -> bool:
        """True when the point lies inside the default view box."""

        return self.bbox.contains(lon, lat)

    @property
    def data_extent(self) -> BBox:
        """Rectangle the land was clipped to; outside it nothing is known."""

        return _as_bbox(self.meta["data_extent"])


def _ring(points: Iterable[Sequence[float]]) -> Ring:
    return tuple((float(p[0]), float(p[1])) for p in points)


def _polygon(item: Mapping[str, Any]) -> LandPolygon:
    rings = tuple(_ring(r) for r in item["rings"])
    if not rings or any(len(r) < 4 or r[0] != r[-1] for r in rings):
        raise ValueError("base map polygon rings must be closed and have at least 4 points")
    lons = [p[0] for p in rings[0]]
    lats = [p[1] for p in rings[0]]
    return LandPolygon(name=str(item.get("name", "")), rings=rings,
                       bbox=BBox(min(lons), min(lats), max(lons), max(lats)),
                       area_km2=float(item.get("area_km2", 0.0)))


def parse_basemap(document: Mapping[str, Any]) -> BaseMap:
    """Build a :class:`BaseMap` from the decoded JSON document."""

    if document.get("schema") != BASEMAP_SCHEMA:
        raise ValueError(f"unsupported base map schema: {document.get('schema')!r}")
    roads = tuple(
        Road(name=str(r.get("name", "")), road_class=str(r["class"]),
             lines=tuple(_ring(line) for line in r["lines"]), ref=str(r.get("ref", "")),
             alias=str(r.get("alias", "")), source=str(r.get("source", "")),
             drawn_km=float(r.get("drawn_km", 0.0)))
        for r in document["roads"])
    places = tuple(
        Place(name=str(p["name"]), lon=float(p["lon"]), lat=float(p["lat"]), kind=str(p["kind"]),
              rank=int(p.get("rank", 2)), source=str(p.get("source", "")))
        for p in document["places"])
    water = tuple(WaterLabel(str(w["name"]), float(w["lon"]), float(w["lat"]))
                  for w in document["water_labels"])
    return BaseMap(
        land=tuple(_polygon(item) for item in document["land"]),
        municipality=tuple(_polygon(item) for item in document["municipality"]),
        roads=roads, places=places, water_labels=water, bbox=_as_bbox(document["bbox"]),
        attribution=str(document["attribution"]), meta=dict(document.get("meta", {})))


def _read_packaged(name: str) -> str:
    path = GEO_DIR / name
    if path.is_file():
        return path.read_text(encoding="utf-8")
    # Zip-style imports: no real directory next to the module.
    from importlib import resources

    return resources.files(__package__).joinpath("geo", name).read_text(encoding="utf-8")


@lru_cache(maxsize=1)
def load_salvador() -> BaseMap:
    """The packaged Salvador base map (parsed once, then cached)."""

    return parse_basemap(json.loads(_read_packaged(SALVADOR_FILE)))


@lru_cache(maxsize=1)
def load_brazil() -> BaseMap:
    """Offline simplified IBGE state boundaries; network lengths use WGS84 geodesics."""
    return parse_basemap(json.loads(_read_packaged("brazil_v1.json")))


@lru_cache(maxsize=1)
def load_world() -> BaseMap:
    """Natural Earth overview; display projection is not a surveyed fibre route."""
    return parse_basemap(json.loads(_read_packaged("world_v1.json")))


@lru_cache(maxsize=32)
def load_campus(node_id: str) -> BaseMap | None:
    """Verified OSM footprint, or an explicitly approximate location reference."""
    from dataclasses import replace
    from ..quantum_protocols.topology import site_catalogue
    site = next((s for s in site_catalogue()['sites'] if s['id'] == node_id), None)
    if site is None:
        return None
    lon, lat = site['longitude'], site['latitude']
    geometry = site.get('osm_geometry') or {}
    polygons = []
    if geometry.get('type') == 'Polygon':
        polygons = [_polygon({'name':site['name'], 'rings':geometry['coordinates']})]
    elif geometry.get('type') == 'MultiPolygon':
        polygons = [_polygon({'name':site['name'], 'rings':rings}) for rings in geometry['coordinates']]
    precision = site['coordinate_precision']
    roads = tuple(Road(r.get('name',''),'secondary',(_ring(r['coordinates']),),source='osm')
                  for r in site.get('osm_roads',()) if r.get('type')=='LineString')
    meta = {**load_world().meta, 'title':f"{site['name']} · {precision}",
            'reference':site, 'footprint_verified':bool(polygons)}
    return replace(load_world(), municipality=tuple(polygons), roads=roads,
                   places=(Place(site['name'],lon,lat,'campus',1,site['coordinate_source']),),
                   bbox=BBox(lon-.008,lat-.008,lon+.008,lat+.008),
                   attribution='© OpenStreetMap contributors · Natural Earth · referência, sem conexão física OrionQ', meta=meta)


def contains_land(lon: float, lat: float) -> bool:
    """Point-in-polygon over the land layer of the Salvador base map."""

    return load_salvador().contains_land(lon, lat)


def in_view(lon: float, lat: float) -> bool:
    """True when the point lies inside the default view box of the Salvador base map."""

    return load_salvador().in_view(lon, lat)


@lru_cache(maxsize=1)
def _geodesy() -> Any:
    """``control_plane.geodesy`` without the cost of importing the ``control_plane`` package.

    ``import quantum_autopilot.control_plane`` runs that package's ``__init__``, which imports
    torch, numpy and pandas (about two seconds). ``geodesy.py`` itself is standard library only,
    so when the package is not loaded yet the file is executed on its own. In a frozen build
    there is no ``.py`` file on disk; the ordinary import is used there (the console has the
    control plane loaded by then anyway).
    """

    import sys

    name = "quantum_autopilot.control_plane.geodesy"
    if name in sys.modules:
        return sys.modules[name]
    source = Path(__file__).resolve().parent.parent / "control_plane" / "geodesy.py"
    if "quantum_autopilot.control_plane" not in sys.modules and source.is_file():
        from importlib import util

        spec = util.spec_from_file_location("_orion_geodesy_standalone", source)
        if spec is not None and spec.loader is not None:
            module = util.module_from_spec(spec)
            spec.loader.exec_module(module)
            return module
    from importlib import import_module

    return import_module(name)


def geodesic_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Geodesic distance (WGS-84); the single implementation is ``control_plane.geodesy``."""

    return _geodesy().geodesic_km(lat1, lon1, lat2, lon2)


class LocalProjection:
    """Local equirectangular projection on the WGS-84 ellipsoid, in metres east/north.

    ``x = (lon - lon0) * N(lat0) * cos(lat0)`` and ``y = (lat - lat0) * M(lat0)``, with the
    ellipsoid's prime-vertical (N) and meridional (M) radii of curvature at ``lat0``. Both
    axes are in metres, so one scale factor maps them to pixels without stretching. Within
    the Salvador view box (about 46 km x 38 km) distances differ from the geodesic by less
    than 0.1 %; this is a display projection, link lengths come from :func:`geodesic_km`.
    """

    def __init__(self, lon0: float, lat0: float) -> None:
        if not (-180.0 <= lon0 <= 180.0 and -89.0 <= lat0 <= 89.0):
            raise ValueError("projection centre must be a valid lon/lat (|lat| <= 89)")
        self.lon0 = float(lon0)
        self.lat0 = float(lat0)
        sin_lat = math.sin(math.radians(self.lat0))
        w = math.sqrt(1.0 - _WGS84_E2 * sin_lat * sin_lat)
        self._m_per_deg_lat = math.radians(WGS84_A_M * (1.0 - _WGS84_E2) / w ** 3)
        self._m_per_deg_lon = math.radians(WGS84_A_M / w * math.cos(math.radians(self.lat0)))

    @classmethod
    def for_bbox(cls, bbox: Any) -> "LocalProjection":
        """Projection centred on a view box (``BBox`` or ``(west, south, east, north)``)."""

        lon0, lat0 = _as_bbox(bbox).center
        return cls(lon0, lat0)

    def to_xy(self, lon: float, lat: float) -> Tuple[float, float]:
        return (lon - self.lon0) * self._m_per_deg_lon, (lat - self.lat0) * self._m_per_deg_lat

    def to_lonlat(self, x_m: float, y_m: float) -> LonLat:
        return self.lon0 + x_m / self._m_per_deg_lon, self.lat0 + y_m / self._m_per_deg_lat

    def fit(self, bbox: Any, width_px: float, height_px: float, margin_px: float = 0.0) -> "ViewTransform":
        """Largest uniform scale that shows ``bbox`` inside the viewport, centred."""

        box = _as_bbox(bbox)
        usable_w = float(width_px) - 2.0 * margin_px
        usable_h = float(height_px) - 2.0 * margin_px
        if usable_w <= 0.0 or usable_h <= 0.0:
            raise ValueError("viewport is smaller than its margins")
        x0, y0 = self.to_xy(box.west, box.south)
        x1, y1 = self.to_xy(box.east, box.north)
        px_per_m = min(usable_w / (x1 - x0), usable_h / (y1 - y0))
        origin_x = float(width_px) / 2.0 - (x0 + x1) / 2.0 * px_per_m
        origin_y = float(height_px) / 2.0 + (y0 + y1) / 2.0 * px_per_m
        return ViewTransform(self, px_per_m, origin_x, origin_y, float(width_px), float(height_px))


@dataclass(frozen=True, eq=False)
class ViewTransform:
    """Degrees <-> pixels with one scale for both axes. Pixel y grows downwards (south)."""

    projection: LocalProjection
    px_per_m: float
    origin_x_px: float  # pixel position of the projection centre
    origin_y_px: float
    width_px: float
    height_px: float

    @property
    def metres_per_px(self) -> float:
        return 1.0 / self.px_per_m

    def to_px(self, lon: float, lat: float) -> Tuple[float, float]:
        x_m, y_m = self.projection.to_xy(lon, lat)
        return self.origin_x_px + x_m * self.px_per_m, self.origin_y_px - y_m * self.px_per_m

    def to_lonlat(self, px: float, py: float) -> LonLat:
        return self.projection.to_lonlat((px - self.origin_x_px) / self.px_per_m,
                                         (self.origin_y_px - py) / self.px_per_m)

    def points_to_px(self, points: Iterable[LonLat]) -> list:
        """``to_px`` for a whole ring or polyline (one list of ``(px, py)``)."""

        projection = self.projection
        kx = projection._m_per_deg_lon * self.px_per_m
        ky = projection._m_per_deg_lat * self.px_per_m
        lon0, lat0, ox, oy = projection.lon0, projection.lat0, self.origin_x_px, self.origin_y_px
        return [(ox + (lon - lon0) * kx, oy - (lat - lat0) * ky) for lon, lat in points]

    def visible_bbox(self) -> BBox:
        """Geographic rectangle covered by the whole viewport."""

        west, north = self.to_lonlat(0.0, 0.0)
        east, south = self.to_lonlat(self.width_px, self.height_px)
        return BBox(west, south, east, north)

    def scale_bar(self, max_px: float = 120.0) -> Tuple[float, float]:
        """``(length_km, length_px)``: the longest round length not wider than ``max_px``.

        When even the shortest round length is wider than ``max_px`` that shortest one is
        returned, so the caller always gets a truthful bar.
        """

        if max_px <= 0.0:
            raise ValueError("max_px must be positive")
        best = SCALE_BAR_LENGTHS_KM[0]
        for length_km in SCALE_BAR_LENGTHS_KM:
            if length_km * 1000.0 * self.px_per_m <= max_px:
                best = length_km
        return best, best * 1000.0 * self.px_per_m


def fit(bbox: Any, width_px: float, height_px: float, margin_px: float = 0.0) -> ViewTransform:
    """Shortcut: projection centred on ``bbox``, then :meth:`LocalProjection.fit`."""

    return LocalProjection.for_bbox(bbox).fit(bbox, width_px, height_px, margin_px)


__all__ = [
    "BASEMAP_SCHEMA", "BBox", "BaseMap", "LandPolygon", "LocalProjection", "OSM_ATTRIBUTION", "Place",
    "Road", "SCALE_BAR_LENGTHS_KM", "ViewTransform", "WaterLabel", "contains_land", "fit", "geodesic_km",
    "in_view", "load_brazil", "load_salvador", "parse_basemap",
]
