"""Offline Salvador base map: packaged data, point-in-polygon and the local projection."""
import json
import math
import os
import subprocess
import sys
from pathlib import Path

import pytest

from quantum_autopilot.console import geodata
from quantum_autopilot.console.geodata import BBox, LocalProjection, fit, load_salvador

pytestmark = pytest.mark.unit
ROOT = Path(__file__).resolve().parents[1]
JSON_PATH = geodata.GEO_DIR / geodata.SALVADOR_FILE

# (lat, lon) of well-known places, approximate published coordinates.
FAROL_DA_BARRA = (-13.0104, -38.5325)
AEROPORTO = (-12.9086, -38.3225)
IGUATEMI = (-12.9790, -38.4553)
COMERCIO = (-12.9730, -38.5130)
MID_BAY = (-12.85, -38.62)
OPEN_OCEAN = (-13.06, -38.40)


@pytest.fixture(scope="module")
def basemap():
    return load_salvador()


def all_rings(basemap):
    for polygon in basemap.land + basemap.municipality:
        for ring in polygon.rings:
            yield polygon, ring


def test_packaged_file_loads_once_and_is_within_the_size_budget(basemap):
    assert JSON_PATH.is_file()
    assert 150_000 <= JSON_PATH.stat().st_size <= 1_500_000
    assert load_salvador() is basemap  # cached
    assert basemap.land and basemap.municipality and basemap.roads and basemap.places
    assert {label.name for label in basemap.water_labels} == {"Baía de Todos os Santos", "Oceano Atlântico"}
    assert basemap.meta["sources"] and basemap.meta["retrieved"] and basemap.meta["simplification"]
    assert (ROOT / "src/quantum_autopilot/console/geo/SOURCES.md").is_file()


def test_openstreetmap_attribution_is_carried(basemap):
    assert geodata.OSM_ATTRIBUTION in basemap.attribution
    assert "IBGE" in basemap.attribution
    licences = {source["id"]: source["licence"] for source in basemap.meta["sources"]}
    assert "ODbL" in licences["osm"]


def test_rings_are_closed_and_inside_a_sane_range(basemap):
    extent = basemap.data_extent
    assert BBox(-39.5, -13.6, -37.6, -12.2).contains(*extent.center)
    count = 0
    for polygon, ring in all_rings(basemap):
        assert len(ring) >= 4 and ring[0] == ring[-1], polygon.name
        for lon, lat in ring:
            assert extent.contains(lon, lat)
            assert round(lon, 5) == lon and round(lat, 5) == lat
        count += 1
    assert count >= 8
    # The peninsula is not a coarse sketch.
    mainland = basemap.municipality[0]
    assert mainland.name == "Salvador" and len(mainland.outer) >= 1000
    assert 250.0 < sum(p.area_km2 for p in basemap.municipality) < 330.0  # IBGE: ~307 km2 of land


def test_view_box_holds_the_whole_municipality(basemap):
    for polygon in basemap.municipality:
        box = polygon.bbox
        assert basemap.in_view(box.west, box.south) and basemap.in_view(box.east, box.north)
    extent = basemap.data_extent
    assert extent.west < basemap.bbox.west and extent.east > basemap.bbox.east
    assert extent.south < basemap.bbox.south and extent.north > basemap.bbox.north


@pytest.mark.parametrize("lat, lon", [FAROL_DA_BARRA, AEROPORTO, IGUATEMI, COMERCIO])
def test_known_landmarks_are_on_land(basemap, lat, lon):
    assert geodata.contains_land(lon, lat)
    assert basemap.in_municipality(lon, lat)
    assert geodata.in_view(lon, lat)


def test_farol_da_barra_is_on_or_next_to_land(basemap):
    lat, lon = FAROL_DA_BARRA
    projection = LocalProjection(lon, lat)
    near = [projection.to_lonlat(dx, dy) for dx in (-150.0, 0.0, 150.0) for dy in (-150.0, 0.0, 150.0)]
    assert any(basemap.contains_land(x, y) for x, y in near)
    # ...and it is the tip of the peninsula: 1.4 km to the south-west is open water.
    assert not basemap.contains_land(*projection.to_lonlat(-1000.0, -1000.0))


@pytest.mark.parametrize("lat, lon", [MID_BAY, OPEN_OCEAN])
def test_bay_and_ocean_are_not_land(basemap, lat, lon):
    assert not geodata.contains_land(lon, lat)
    assert not basemap.in_municipality(lon, lat)
    assert geodata.in_view(lon, lat)


def test_water_labels_are_in_water_and_in_view(basemap):
    for label in basemap.water_labels:
        assert not basemap.contains_land(label.lon, label.lat)
        assert basemap.in_view(label.lon, label.lat)


def test_points_outside_the_view_or_the_data_are_reported(basemap):
    assert not geodata.in_view(-46.63, -23.55)  # São Paulo
    assert not geodata.contains_land(-46.63, -23.55)  # outside the data extent: unknown, so not land


def test_places_cover_the_reference_neighbourhoods(basemap):
    names = {place.name for place in basemap.places}
    wanted = {"Barra", "Ondina", "Rio Vermelho", "Pituba", "Iguatemi", "Comércio", "Pelourinho", "Cabula",
              "CAB", "Piatã", "Itapuã", "Aeroporto", "Paripe", "Ribeira", "Brotas", "Pirajá", "Stella Maris",
              "Imbuí", "Patamares", "SENAI CIMATEC", "Farol da Barra", "Ilha de Maré", "Lauro de Freitas"}
    assert wanted <= names
    kinds = {"neighbourhood", "landmark", "airport", "island", "municipality"}
    extent = basemap.data_extent
    for place in basemap.places:
        assert place.kind in kinds and place.rank in (1, 2) and place.source
        assert extent.contains(place.lon, place.lat)
        assert basemap.contains_land(place.lon, place.lat), place.name
    by_name = {place.name: place for place in basemap.places}
    cimatec = by_name["SENAI CIMATEC"]
    assert basemap.in_municipality(cimatec.lon, cimatec.lat)
    assert abs(cimatec.lon - (-38.3866)) < 0.01 and abs(cimatec.lat - (-12.9391)) < 0.01  # INEP record


def test_roads_are_real_named_arterials_on_land(basemap):
    assert {road.road_class for road in basemap.roads} <= {"motorway", "trunk", "primary", "secondary"}
    assert {road.source for road in basemap.roads} <= {"osm", "dnit_snv"}
    refs = {road.ref for road in basemap.roads}
    aliases = {road.alias for road in basemap.roads}
    names = {road.name for road in basemap.roads}
    assert {"BR-324", "BA-526", "BA-099"} <= refs
    assert {"Avenida Paralela", "Avenida Bonocô", "Avenida ACM", "Avenida Suburbana"} <= aliases
    assert {"Avenida Luís Viana", "Avenida Octávio Mangabeira", "Avenida Vasco da Gama",
            "Avenida Anita Garibaldi", "Avenida Antônio Carlos Magalhães", "Avenida Afrânio Peixoto"} <= names
    extent = basemap.data_extent
    total = on_land = 0
    for road in basemap.roads:
        assert road.lines
        for line in road.lines:
            assert len(line) >= 2
            assert all(extent.contains(lon, lat) for lon, lat in line)
            for lon, lat in line[::3]:
                total += 1
                on_land += basemap.contains_land(lon, lat)
    # Roads (OpenStreetMap, DNIT) and land (IBGE) are independent datasets; they must agree.
    assert total > 1500 and on_land / total > 0.99


def test_projection_round_trips_within_a_metre(basemap):
    projection = LocalProjection.for_bbox(basemap.bbox)
    assert (projection.lon0, projection.lat0) == basemap.bbox.center
    assert projection.to_xy(projection.lon0, projection.lat0) == (0.0, 0.0)
    box = basemap.data_extent
    for lon in (box.west, basemap.bbox.west, projection.lon0, basemap.bbox.east, box.east):
        for lat in (box.south, basemap.bbox.south, projection.lat0, basemap.bbox.north, box.north):
            x_m, y_m = projection.to_xy(lon, lat)
            back_lon, back_lat = projection.to_lonlat(x_m, y_m)
            again = projection.to_xy(back_lon, back_lat)
            assert math.hypot(again[0] - x_m, again[1] - y_m) < 1e-3  # metres
            assert abs(back_lon - lon) < 1e-9 and abs(back_lat - lat) < 1e-9
    # East is +x, north is +y.
    assert projection.to_xy(projection.lon0 + 0.1, projection.lat0)[0] > 0
    assert projection.to_xy(projection.lon0, projection.lat0 + 0.1)[1] > 0


def test_projection_metres_agree_with_the_geodesic(basemap):
    projection = LocalProjection.for_bbox(basemap.bbox)
    lon0, lat0 = basemap.bbox.center
    east = projection.to_lonlat(10_000.0, 0.0)
    north = projection.to_lonlat(0.0, 10_000.0)
    assert geodata.geodesic_km(lat0, lon0, east[1], east[0]) == pytest.approx(10.0, rel=1e-4)
    assert geodata.geodesic_km(lat0, lon0, north[1], north[0]) == pytest.approx(10.0, rel=1e-4)
    # Across the whole view box the display projection stays within 0.1 % of the geodesic.
    box = basemap.bbox
    for a, b in (((box.west, box.south), (box.east, box.north)), ((box.west, box.north), (box.east, box.north)),
                 ((box.west, box.south), (box.east, box.south)), ((box.west, box.south), (box.west, box.north))):
        ax, ay = projection.to_xy(*a)
        bx, by = projection.to_xy(*b)
        planar_km = math.hypot(bx - ax, by - ay) / 1000.0
        assert planar_km == pytest.approx(geodata.geodesic_km(a[1], a[0], b[1], b[0]), rel=1e-3)


def test_fit_uses_one_scale_for_both_axes(basemap):
    width, height, margin = 1000.0, 600.0, 20.0
    view = fit(basemap.bbox, width, height, margin)
    projection = view.projection
    lon0, lat0 = basemap.bbox.center
    cx, cy = view.to_px(lon0, lat0)
    assert (cx, cy) == pytest.approx((width / 2.0, height / 2.0))
    ex, ey = view.to_px(*projection.to_lonlat(10_000.0, 0.0))
    nx, ny = view.to_px(*projection.to_lonlat(0.0, 10_000.0))
    east_px, north_px = math.hypot(ex - cx, ey - cy), math.hypot(nx - cx, ny - cy)
    assert east_px == pytest.approx(north_px, rel=0.005)
    assert east_px == pytest.approx(10_000.0 / view.metres_per_px, rel=1e-9)
    assert ex > cx and ey == pytest.approx(cy)  # east is to the right
    assert ny < cy and nx == pytest.approx(cx)  # north is up
    # The box fits inside the margins and touches them on the limiting axis (here: height).
    left, bottom = view.to_px(basemap.bbox.west, basemap.bbox.south)
    right, top = view.to_px(basemap.bbox.east, basemap.bbox.north)
    assert left >= margin - 1e-6 and right <= width - margin + 1e-6
    assert top == pytest.approx(margin) and bottom == pytest.approx(height - margin)
    assert (right - left) / (bottom - top) == pytest.approx(
        (projection.to_xy(basemap.bbox.east, lat0)[0] - projection.to_xy(basemap.bbox.west, lat0)[0])
        / (projection.to_xy(lon0, basemap.bbox.north)[1] - projection.to_xy(lon0, basemap.bbox.south)[1]))
    # Pixel round trip and the batch helper.
    for lon, lat in ((-38.5325, -13.0104), (-38.3225, -12.9086), (-38.62, -12.85)):
        px, py = view.to_px(lon, lat)
        back = view.to_lonlat(px, py)
        assert back == pytest.approx((lon, lat), abs=1e-9)
        assert view.points_to_px([(lon, lat)])[0] == pytest.approx((px, py))
    visible = view.visible_bbox()
    assert visible.west < basemap.bbox.west and visible.east > basemap.bbox.east
    # A tall viewport is limited by its width instead.
    tall = fit(basemap.bbox, 400.0, 900.0, 10.0)
    assert tall.to_px(basemap.bbox.west, lat0)[0] == pytest.approx(10.0)
    assert tall.to_px(basemap.bbox.east, lat0)[0] == pytest.approx(390.0)
    # The method form with an explicit centre gives the same transform as the shortcut.
    same = LocalProjection.for_bbox(basemap.bbox).fit(basemap.bbox.as_tuple(), width, height, margin)
    assert same.to_px(-38.5, -12.95) == pytest.approx(view.to_px(-38.5, -12.95))


def test_scale_bar_picks_the_longest_round_length_that_fits(basemap):
    view = fit(basemap.bbox, 1000.0, 600.0, 20.0)
    length_km, length_px = view.scale_bar(120.0)
    assert length_km == 5.0  # ~67 m/px: 5 km is 74 px, 10 km would be 149 px
    assert length_px == pytest.approx(length_km * 1000.0 / view.metres_per_px)
    assert length_px <= 120.0 < 10_000.0 / view.metres_per_px
    for max_px in (40.0, 80.0, 200.0, 400.0):
        km, px = view.scale_bar(max_px)
        assert km in geodata.SCALE_BAR_LENGTHS_KM and px <= max_px
        assert px == pytest.approx(km * 1000.0 * view.px_per_m)
        longer = [c for c in geodata.SCALE_BAR_LENGTHS_KM if c > km]
        assert not longer or longer[0] * 1000.0 * view.px_per_m > max_px
    assert {1.0, 2.0, 5.0, 10.0, 20.0} <= set(geodata.SCALE_BAR_LENGTHS_KM)
    # Zoomed far in, the shortest round length is returned even though it is wider than asked.
    close = fit((-38.4010, -12.9410, -38.4000, -12.9400), 1000.0, 1000.0)
    km, px = close.scale_bar(100.0)
    assert km == geodata.SCALE_BAR_LENGTHS_KM[0] and px > 100.0
    with pytest.raises(ValueError):
        view.scale_bar(0.0)


def test_invalid_boxes_and_viewports_are_rejected(basemap):
    with pytest.raises(ValueError):
        BBox(-38.2, -13.0, -38.7, -12.7)
    with pytest.raises(ValueError):
        fit(basemap.bbox, 30.0, 600.0, 20.0)
    with pytest.raises(ValueError):
        LocalProjection(-38.5, 89.9)
    with pytest.raises(ValueError):
        geodata.parse_basemap({"schema": "something-else"})


def test_polygon_holes_are_excluded():
    square = ((0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0), (0.0, 0.0))
    hole = ((4.0, 4.0), (6.0, 4.0), (6.0, 6.0), (4.0, 6.0), (4.0, 4.0))
    polygon = geodata.LandPolygon("x", (square, hole), BBox(0.0, 0.0, 10.0, 10.0))
    assert polygon.contains(1.0, 1.0) and not polygon.contains(5.0, 5.0) and not polygon.contains(11.0, 5.0)
    assert polygon.outer == square and polygon.holes == (hole,)


def test_geodesic_is_the_control_plane_implementation():
    from quantum_autopilot.control_plane import geodesy

    args = (-13.0104, -38.5325, -12.9086, -38.3225)
    assert geodata.geodesic_km(*args) == geodesy.geodesic_km(*args)
    assert 25.0 < geodata.geodesic_km(*args) < 26.5  # Farol da Barra to the airport


def test_module_needs_neither_qt_nor_numpy():
    code = ("import sys; from quantum_autopilot.console import geodata as g; m = g.load_salvador(); "
            "v = g.fit(m.bbox, 800, 600, 10); v.scale_bar(100); g.contains_land(-38.5, -12.97); "
            "assert 15.0 < g.geodesic_km(-13.0, -38.5, -12.9, -38.4) < 16.0; "
            "bad = [n for n in ('PySide6', 'numpy', 'pandas', 'torch', 'socket') if n in sys.modules]; print(bad)")
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60,
                            cwd=str(ROOT), env=dict(os.environ, PYTHONPATH=str(ROOT / "src")))
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "[]"


def test_data_ships_with_the_package():
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    line = next(row for row in text.splitlines() if row.startswith('"quantum_autopilot.console"'))
    assert '"geo/*.json"' in line and '"geo/*.md"' in line
    document = json.loads(JSON_PATH.read_text(encoding="utf-8"))
    assert document["schema"] == geodata.BASEMAP_SCHEMA
