# Cartography module (Module 5)

PostGIS-backed spatial endpoints for the national school map. Built to feed
a MapLibre GL JS client that needs to render 3M+ schools without melting
the browser.

## Endpoints

All endpoints live under `/api/cartography/*` and require authentication
(`Authorization: Bearer <jwt>`). Territorial scope is enforced per role
(national, regional, prefecture, sub-prefecture, school).

| Method | Path | Purpose |
| ------ | ---- | ------- |
| `GET`  | `/schools` | GeoJSON FeatureCollection of in-scope schools. |
| `GET`  | `/schools/nearby` | K-nearest schools around a point. |
| `GET`  | `/catchments` | Voronoi catchment polygons (PostGIS). |
| `GET`  | `/coverage-gaps` | Empty grid cells (no school within radius). |
| `GET`  | `/indicators` | KPI by territory level. |
| `GET`  | `/site-recommendations` | Suggested locations for new schools. |
| `POST` | `/geocode` | Async geocoding job (Celery). |
| `GET`  | `/tiles/{z}/{x}/{y}.mvt` | **Module 5** — Mapbox Vector Tile. |
| `POST` | `/isochrones` | **Module 5** — Walking isochrones. |
| `GET`  | `/density/subprefectures` | **Module 5** — Student density choropleth. |
| `GET`  | `/distance-stats/regions` | **Module 5** — Avg inter-school distance per region. |

## Vector tiles (MVT)

```bash
# Request the world tile at zoom 0 (single tile spans the entire planet)
curl -H "Authorization: Bearer $TOKEN" \
     "http://localhost:8000/api/cartography/tiles/0/0/0.mvt" \
     --output world.mvt
```

* **Why MVT?** A FeatureCollection for 3M schools is ~600 MB JSON; a single
  MVT is a few kilobytes (protobuf, clipped, quantised to a 4096 grid).
* **Pipeline.** PostGIS does everything in one SQL statement using
  `ST_TileEnvelope` + `ST_AsMVTGeom` + `ST_AsMVT`. No external services
  (tippecanoe, t-rex, martin) required.
* **Cache.** Tiles are cached in Redis under `mvt:{z}:{x}:{y}` with a 1h
  TTL. Flush the namespace after a bulk import:
  `redis-cli --scan --pattern 'mvt:*' | xargs redis-cli DEL`
* **Auth.** Tiles require a valid JWT. RBAC is *not* applied per-tile (the
  tile data is national); the assumption is "any authenticated user can see
  the national map".
* **Response body.** Empty tiles return HTTP 200 with a 0-byte body — that's
  the MVT convention for "nothing visible here".
* **Coordinate range.** `z` ∈ `[0, 22]`, `x`/`y` ∈ `[0, 2^z − 1]`. Anything
  outside ⇒ HTTP 422 with code `tile_coordinates_invalid`.

### PostGIS missing

When the running Postgres does not have PostGIS installed (current state of
the local dev machine, see `project_local_env.md`), the endpoint returns
**HTTP 503**:

```json
{
  "code": "postgis_unavailable",
  "message": "PostGIS extension is not installed on this Postgres instance — vector tiles are unavailable. Install it with `CREATE EXTENSION postgis;`.",
  "extra": {"z": 0, "x": 0, "y": 0}
}
```

The full test suite uses the `@pytest.mark.postgis` marker (see
`tests/integration/conftest.py`) to auto-skip MVT tests when the extension
is unavailable. Pure-Python tests (validation, RBAC, isochrone math) still
run.

## Walking isochrones

```bash
curl -X POST -H "Authorization: Bearer $TOKEN" \
     -H "Content-Type: application/json" \
     -d '{"lat": 9.6412, "lon": -13.5784, "intervals": [15, 30, 45, 60]}' \
     http://localhost:8000/api/cartography/isochrones
```

* **MVP approximation.** No OSRM / Valhalla in local stack — we approximate
  each isochrone as a **circle** with radius `(minutes / 60) * speed_kmh *
  1000` metres. The polygon has 64 vertices.
* **Bounds.** `lat`/`lon` must fall inside the Guinea bounding box
  (`lat ∈ [7.0, 13.0]`, `lon ∈ [−15.5, −7.5]`) or the request is rejected
  with HTTP 422.
* **Intervals.** 1–12 values per request, each in `[1, 120]` minutes.
* **Speed.** Default 5 km/h (WHO baseline for an adult on flat ground);
  configurable up to 15 km/h.
* **Contract.** GeoJSON FeatureCollection of Polygon features, plus a
  top-level `meta.note` explaining the approximation. Module 17 (Flutter)
  will swap the implementation for routed isochrones via Valhalla; the
  contract stays stable.

## Density choropleth

```bash
curl -H "Authorization: Bearer $TOKEN" \
     http://localhost:8000/api/cartography/density/subprefectures
```

* **Numerator.** Student count per sub-prefecture.
* **Denominator.** Convex hull area of the in-scope schools, in km²
  (computed via `ST_ConvexHull` + `ST_Area`). Sub-prefectures without a
  proper geometry collection (fewer than 3 schools) return `areaKm2 = 0`
  and `density = 0`; the UI hides those rows.
* **PostGIS required.** Returns 503 if the extension is missing.

## Distance stats per region

```bash
curl -H "Authorization: Bearer $TOKEN" \
     http://localhost:8000/api/cartography/distance-stats/regions
```

* For each in-scope region, returns the **average nearest-neighbour
  distance between schools** in km. Coarse proxy for "how spread out are the
  schools in this region".
* Not the same as "average distance student-to-school" (student home
  coordinates are not in the schema yet — that's Module 6 backlog).

## Runtime knobs

| Env var / constant | Default | Purpose |
| ------------------ | ------- | ------- |
| `TILE_CACHE_TTL_SECONDS` (service.py) | `3600` | MVT cache TTL. |
| `MAX_ZOOM` (tiles.py) | `22` | Reject silly `z` values. |
| `_POLYGON_VERTICES` (isochrones.py) | `64` | Smoother = bigger payload. |
| `MAX_MINUTES` (isochrones.py) | `120` | Caps isochrone radius at ≈ 10 km. |

## Module 5.1 backlog

* Replace Haversine circle isochrones with routed OSRM / Valhalla output
  (depends on Module 17 mobile sprint).
* Add a `territory` layer to MVT (one tile, multiple layers — schools +
  prefecture boundaries) once Postgres ships territory geom columns.
* Tile cache pre-warming for zoom ≤ 8 (whole-country view).
* Smarter cache invalidation: subscribe to a `school.updated` event bus
  rather than rely on TTL alone.
