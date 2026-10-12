# Offline base maps — sources, licences, attribution

## National demonstration (2026-10-05)

`brazil_v1.json` contains simplified IBGE state boundaries from the official
[Malhas API](https://servicodados.ibge.gov.br/api/docs/malhas?versao=3), downloaded once using
`/api/v3/malhas/paises/BR?formato=application/vnd.geo%2Bjson&qualidade=intermediaria&intrarregiao=UF`.
The retrieval date, source URL and SHA-256 of the decompressed GeoJSON are in its metadata.
The 27 states produce 47 polygon parts; no cable routes are encoded. The view is a display
projection; backend distances use WGS84 geodesics, independently of screen zoom or projection.

Campus reference positions are documented in `network_engine/institutional.py` and in the
node's `demonstration_site` telemetry. OpenStreetMap points/centres are approximate cartographic
references under ODbL, not surveyed quantum equipment positions. Institution addresses were
checked on institutional/government pages. CBPF publishes its own geographic reference;
IME-USP's reference comes from its embedded campus map. CIQUANTA uses the Estação das Artes
Luciano Agra building identified by the Paraíba government, not the Secretariat's postal address.
The node roles, IP addresses, equipment and fibres are entirely hypothetical. Reuse of OSM-derived
coordinates is subject to ODbL, with attribution to © OpenStreetMap contributors.

## Salvador local base map

`salvador_v1.json` is the offline vector base map read by `quantum_autopilot.console.geodata`.
It was assembled on **2026-10-04** from the public datasets listed here. No coordinate in the
file was drawn or estimated by hand, with one stated exception: the two `water_labels` anchors
are label positions chosen inside the water, not surveyed points.

The console never opens a socket; everything below was downloaded once, at build time.

## Attribution the UI must show

Wherever the map is drawn, show this line (it is also `BaseMap.attribution`):

> Base cartográfica: IBGE, Censo 2022 (via geobr/IPEA) · Vias © OpenStreetMap contributors (ODbL) · BR-324: DNIT/SNV · Natural Earth · INEP

The part **`© OpenStreetMap contributors`** is a licence condition (ODbL 1.0 §4.3), not a
courtesy; `geodata.OSM_ATTRIBUTION` holds it. If space is short the minimum is
`IBGE · © OpenStreetMap contributors`.

## Licence consequence of the roads layer

The `roads` entries with `"source": "osm"` and the four `places` with `"source": "osm"` are a
database derived from OpenStreetMap, so that part of the file is under the
**Open Database License 1.0** (share-alike): anyone who receives the application may extract and
reuse those entries under ODbL, and a modified version that is distributed must stay under ODbL.
The other layers are independent of it (a "collective database" in ODbL terms) and keep their own
terms. Removing every `"source": "osm"` entry removes the ODbL obligation.

## Layers

| Layer | Content | Source id |
|---|---|---|
| `land` | 35 polygons, 13 535 points: all land in the data extent (Salvador, Lauro de Freitas, Simões Filho, Camaçari, Candeias, Madre de Deus, São Francisco do Conde, Itaparica, Vera Cruz and others), dissolved | `ibge_setores_2022` |
| `municipality` | 7 rings, 3 357 points: the land of the municipality of Salvador (mainland 2 158 points; Ilha dos Frades, Ilha de Maré, Ilha de Bom Jesus dos Passos, islets) | `ibge_setores_2022`, names from `ibge_bairros_2022` |
| `roads` | 266 roads, 769 polylines, 5 503 points: motorway, trunk, primary, secondary | `osm` (265), `dnit_snv` (1) |
| `places` | 187 label points | see "Places" |
| `water_labels` | 2 label anchors | chosen by hand, checked to be in water |

Coordinates are longitude, latitude in degrees, 5 decimals (about 1.1 m). The IBGE and DNIT data
are in SIRGAS 2000 (EPSG:4674) and OpenStreetMap is in WGS-84; the two frames differ by well under
a metre, so no datum shift was applied.

## Sources

### `ibge_setores_2022` — land and municipality
- **What:** IBGE, *Malha de Setores Censitários, Censo Demográfico 2022*, Bahia, as repackaged by
  the geobr project (IPEA) as a GeoPackage.
- **URL:** <https://github.com/ipeaGIT/geobr/releases/download/v1.7.0/29census_tract_2022.gpkg>
  (115 478 528 bytes, sha256 `5aa37180c75d51e9ddc656a4cab930b9b1c0b64cca25c4b74e82e9778d485ca5`)
- **Why census tracts and not the municipal mesh:** the IBGE municipal polygon of Salvador is the
  legal limit and includes part of the bay, so it has no coastline. Census tracts separate land
  from water: tracts with no `situacao` are water bodies (the bay, estuaries, reservoirs).
  Land = union of every tract that has a `situacao`.
- **Processing:** tracts intersecting the data extent (lon −38.86…−38.12, lat −13.16…−12.62);
  union for Salvador (code 2927408) and union for all other municipalities; clip to the extent;
  simplify both together with GEOS `CoverageSimplifier` (Visvalingam–Whyatt, tolerance 12 m =
  square root of the triangle area, shared borders kept identical); snap to the 1e-5° grid; drop
  islets under 0.25 ha and enclosed water under 2 ha; `land` is the union of the two.
- **Licence:** IBGE publishes its geoscience products for free use provided the source is cited
  ("Fonte: IBGE"). geobr is MIT-licensed (cite: Pereira, R.H.M. et al., *geobr: Download Official
  Spatial Data Sets of Brazil*, IPEA).

### `ibge_bairros_2022` — neighbourhood label points and island names
- **What:** IBGE, *Malha de Bairros, Censo Demográfico 2022* (170 bairros of Salvador), via geobr.
- **URL:** <https://github.com/ipeaGIT/geobr/releases/download/v1.7.0/neighborhoods_2022.gpkg>
  (sha256 `ae059fb96bebf63436a40fccd3c5acbaabe53ff2958e7a12436e2acc375d5172`)
- **Processing:** one point per bairro: the centroid when it falls inside the polygon, otherwise
  the pole of inaccessibility. Three bairro names are shortened for display: "Centro
  Administrativo da Bahia - CAB" → "CAB", "Beiru / Tancredo Neves" → "Tancredo Neves", "Vila Ruy
  Barbosa / Jardim Cruzeiro" → "Jardim Cruzeiro". The island bairros become `kind: island`.
  `rank` (1 = show first) is an editorial choice, not data.
- **Licence:** as above (IBGE, cite the source).

### `ibge_sedes_2010` — neighbouring municipal seats
- **What:** IBGE, *sedes municipais* 2010, via geobr; names taken from the 2022 tract table.
- **URL:** <https://github.com/ipeaGIT/geobr/releases/download/v1.7.0/municipal_seat_2010.gpkg>
  (sha256 `f29fa73e442017646896ca47d563bb03095640ec7baeb66d1ce04d54ec8538fc`)
- **Licence:** as above.

### `osm` — roads (and four landmark points)
- **What:** OpenStreetMap. The file is an Overpass API answer of **2019-01-18T16:23:02Z** holding
  the drivable street network of Salvador (82 032 nodes, 17 581 ways), saved by OSMnx as a cache
  file in the public repository `mnunes/osmnx` (MIT, Marcus Nunes, 2019).
- **URL:** <https://raw.githubusercontent.com/mnunes/osmnx/146810bab6308418fc356e7b2a4543a89279bbce/notebooks/cache/2c1cea4699d4986d98411f696a336603.json>
  (9 707 149 bytes, sha256 `590438d7920c7470273af05f04fea3990f660495fd9aafd8f8ac244885f6faa1`)
- **Processing:** ways with `highway` = motorway, trunk, primary or secondary; ramps (`*_link`)
  left out; ways merged end to end per (class, name); Douglas–Peucker 4 m; `ref` from the `ref`
  tag; `alias` from the `alt_name` tag when at least 3 ways and 25 % of the ways of the street
  carry it (this is where "Avenida Paralela", "Avenida Bonocô", "Avenida ACM", "Avenida Suburbana"
  come from). Different OSM spellings of one street were unified to the commonest one (table
  `NAME_VARIANTS` in the build script, e.g. "Avenida Luiz Viana" and "Avenida Luís Viana Filho" →
  "Avenida Luís Viana", "Avenida Paulo 6" → "Avenida Paulo VI").
  Ways without `name` take their `ref`; with neither they keep `name: ""` (connector segments:
  draw, do not label).
- **Coverage:** the municipality of Salvador plus about 500 m. Roads in Lauro de Freitas, Simões
  Filho, Camaçari and on Itaparica are **not** in the file. The data is a 2019 snapshot.
- **Licence:** Open Database License 1.0. Attribution: **© OpenStreetMap contributors**
  (<https://www.openstreetmap.org/copyright>).

### `dnit_snv` — BR-324 north of Salvador
- **What:** DNIT, *Sistema Nacional de Viação*, shapefile **SNV 202602A**, as mirrored in the
  public repository `gelobs/GoPro_SNV_Analyzer` (the repository's own code is MIT). Original
  publisher: DNIT Geo, <https://www.gov.br/dnit/pt-br/assuntos/planejamento-e-pesquisa/dnit-geo>.
- **URL:** <https://raw.githubusercontent.com/gelobs/GoPro_SNV_Analyzer/355055b756e46ab04d172794f6041ae01a3ce80c/data/snv/202602A/SNV_202602A.shp>
  (+ `.dbf`, `.shx`; `.shp` sha256 `2e734223e0063e3c5af44b1b731620be7ea5f1d7545ff936057eb80006a6a360`)
- **Processing:** BR-324 segments in service (planned and ferry segments left out), only the part
  outside the municipality of Salvador and not already covered by the OSM extract: one line,
  20.96 km. Inside Salvador, where both datasets exist, the DNIT centreline is a median 2.0 m
  (95th percentile 10.5 m) from the OSM carriageways (93 samples).
- **Licence:** federal open government data published by DNIT. The mirror carries no separate
  licence text for the shapefile; cite "DNIT/SNV". If that is not acceptable for a release,
  delete the single road with `"source": "dnit_snv"`.

### `inep_escolas_2023` — SENAI CIMATEC
- **What:** INEP, *Catálogo de Escolas* 2023, via geobr. Record 29460581, "SENAI CIMATEC",
  "AV. ORLANDO GOMES, 1845 PIATA", point −38.38661, −12.93913.
- **URL:** <https://github.com/ipeaGIT/geobr/releases/download/v1.7.0/schools_2023.gpkg>
  (sha256 `d2087646d60b96c2870879b8106f2b5b1d92f64c9fcb0a8f027f1bc3eb8421c4`)
- **Licence:** federal open government data (INEP); cite the source.

### `natural_earth_airports` — airport point
- **What:** Natural Earth 1:10m airports, feature "Deputado Luis Eduardo Magalhaes Int'l" (SSA).
- **URL:** <https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/geojson/ne_10m_airports.geojson>
  (retrieved 2026-10-04, sha256 `28892a892f670f1cf654c62253c6169fdc2504b92c956addb4ac8224f50cebe0`)
- **Licence:** public domain.

## Places: where each kind comes from

| `kind` | Count | Source | How the point was obtained |
|---|---|---|---|
| `neighbourhood` | 167 | `ibge_bairros_2022` | label point of the IBGE bairro polygon |
| `island` | 4 | `ibge_bairros_2022` (Maré, Frades, Bom Jesus dos Passos), `ibge_setores_2022` (Itaparica) | label point of the bairro polygon; for Itaparica, of the land polygon that holds the municipal seat of Itaparica |
| `municipality` | 10 | `ibge_sedes_2010` | IBGE municipal seat point |
| `landmark` Farol da Barra | 1 | `osm` | midpoint of way 439923607, "Largo do Farol da Barra", the street at the lighthouse, not the tower itself |
| `landmark` Pelourinho | 1 | `osm` | midpoint of way 122790587, "Largo do Pelourinho" |
| `landmark` Elevador Lacerda | 1 | `osm` | midpoint of way 59224731, "Elevador Lacerda" (`highway=elevator`) |
| `landmark` Iguatemi | 1 | `osm` | node at the Iguatemi end of the ways named "Ligação Iguatemi-Paralela", where they meet Avenida Tancredo Neves (−38.46444, −12.97977) |
| `landmark` SENAI CIMATEC | 1 | `inep_escolas_2023` | INEP record 29460581 |
| `airport` | 1 | `natural_earth_airports` | Natural Earth point for SSA |

No coordinate was taken from Wikipedia, Google Maps or memory.

## What is not in the file

- Roads outside the municipality of Salvador, except BR-324 (DNIT).
- Roads opened after January 2019, ramps, and anything below `secondary`.
- Inland water (Dique do Tororó, Lagoa do Abaeté, Pituaçu, the Cobre and Ipitanga reservoirs…):
  IBGE has no separate water tract for them, so they appear as land.
- The legal municipal limit over the bay. `municipality` is land only.

## Rebuilding and refreshing

The file is produced by `build_salvador.py` (with `geolib.py` and `gpkg.py`). These scripts are
not part of the package because they need shapely and pyshp; `geodata.py` needs neither. To replace
the roads with a current OpenStreetMap extract for the whole data extent, save the answer of this
Overpass query and run `merge_osm.py <file>`:

```
[out:json][timeout:180][bbox:-13.16,-38.86,-12.62,-38.12];way["highway"~"^(motorway|trunk|primary|secondary)$"];out tags geom;
```


## Global view and institutional detail (2026-10-06)

`world_v1.json`: Natural Earth 110m admin-0 polygons, public domain. Upstream: https://github.com/nvkelso/natural-earth-vector/blob/master/geojson/ne_110m_admin_0_countries.geojson . Source SHA256: 6866c877d39cba9c357620878839b336d569f8c662d3cfab4cb1dbe2d39c977f . Terms: https://www.naturalearthdata.com/about/terms-of-use/ .

Campus footprints and address references are recorded in `quantum_protocols/data/sites_v1.json`, including OSM object links and retrieval dates. © OpenStreetMap contributors, ODbL 1.0, https://www.openstreetmap.org/copyright . IQMP is a developing parcel; Google is an approximate access reference without a fabricated boundary; Chinese municipal references are not surveyed network terminals. Global display projection is approximate; operational lengths use WGS84. Proposed intermediate components are hypothetical.
