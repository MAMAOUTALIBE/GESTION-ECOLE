"""Mapbox Vector Tiles (MVT) generation — PostGIS native pipeline.

Why MVT for school maps
-----------------------
Serving 3M+ schools as a GeoJSON FeatureCollection produces a ~600 MB payload
per request: it cripples the client (parse + render) and saturates the network.
MVT is the industry standard answer: small protobuf tiles, one per (z, x, y),
with geometry already clipped to the tile envelope and quantised to a 4096-unit
integer grid. The client (MapLibre) only fetches tiles in the current viewport.

PostGIS 3 ships `ST_TileEnvelope`, `ST_AsMVTGeom` and `ST_AsMVT` — we don't need
any external tooling (tippecanoe, t-rex, martin) for the MVP. Everything runs
inside one SQL statement.

PostGIS absent
--------------
On environments where the extension is not installed (local dev box per Module
0 environment notes), `generate_mvt` raises :class:`PostgisUnavailableError`,
which the router maps to HTTP 503. Tests gated on `@pytest.mark.postgis` are
auto-skipped (see ``tests/integration/conftest.py``).

References
----------
* https://postgis.net/docs/ST_AsMVT.html
* https://postgis.net/docs/ST_TileEnvelope.html
* https://github.com/mapbox/vector-tile-spec
"""
from __future__ import annotations

from typing import Final

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, ProgrammingError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import PostgisUnavailableError, ValidationFailedError

# Web Mercator tile space caps. Most maps clamp at z=18 (street level); we
# allow up to z=22 for completeness but reject the silly z=30 case that would
# overflow Python ints when computing 2^z.
MAX_ZOOM: Final[int] = 22
DEFAULT_LAYER: Final[str] = "schools"
ALLOWED_LAYERS: Final[frozenset[str]] = frozenset({"schools"})

# ST_AsMVTGeom parameters: 4096 = MVT spec extent, 64 = buffer in tile units
# (so points right outside the tile still render their label/icon when the
# client stitches neighbouring tiles), True = clip geometry to extent.
_TILE_EXTENT: Final[int] = 4096
_TILE_BUFFER: Final[int] = 64


class TileCoordinatesError(ValidationFailedError):
    """Z/X/Y out of bounds — mapped to HTTP 422 by the global handler."""

    code = "tile_coordinates_invalid"


def validate_tile_coords(z: int, x: int, y: int) -> None:
    """Reject (z, x, y) combinations that are not valid Web Mercator tiles.

    The router uses ``Path(ge=0, le=MAX_ZOOM)`` for `z` already; this is a
    defence-in-depth check that also verifies x/y are within the 2^z grid.
    """
    if z < 0 or z > MAX_ZOOM:
        raise TileCoordinatesError(
            detail=f"zoom out of bounds (0..{MAX_ZOOM}), got z={z}",
            extra={"z": z, "x": x, "y": y, "maxZoom": MAX_ZOOM},
        )
    max_index = (1 << z) - 1  # 2^z - 1
    if x < 0 or x > max_index or y < 0 or y > max_index:
        raise TileCoordinatesError(
            detail=(
                f"x/y out of tile grid at z={z} "
                f"(allowed range 0..{max_index})"
            ),
            extra={"z": z, "x": x, "y": y, "maxIndex": max_index},
        )


async def generate_mvt(
    session: AsyncSession,
    z: int,
    x: int,
    y: int,
    layer: str = DEFAULT_LAYER,
) -> bytes:
    """Generate a Mapbox Vector Tile for the requested (z, x, y).

    Returns
    -------
    bytes
        Protobuf-encoded MVT (``application/vnd.mapbox-vector-tile``). An
        empty tile (``b""``) is returned when no school falls inside the
        envelope — clients treat that as a transparent tile.

    Raises
    ------
    PostgisUnavailableError
        If PostGIS is not installed (functions are missing) — caller maps
        to HTTP 503.
    TileCoordinatesError
        If z/x/y are outside the valid Web Mercator grid.
    """
    validate_tile_coords(z, x, y)
    if layer not in ALLOWED_LAYERS:
        raise TileCoordinatesError(
            detail=f"unknown MVT layer '{layer}'",
            extra={"allowedLayers": sorted(ALLOWED_LAYERS)},
        )

    # NOTE: `:layer` is parameter-bound for safety, but we also constrained
    # it via ALLOWED_LAYERS above. ST_AsMVT requires the layer name as a
    # text literal at planning time, so we interpolate it from the allowlist.
    sql = text(
        f"""
        WITH tile AS (
            SELECT ST_TileEnvelope(:z, :x, :y) AS env
        ),
        mvt_data AS (
            SELECT
                s.id AS id,
                s.name AS name,
                s.code AS code,
                s.type AS "schoolType",
                s."regionId" AS "regionId",
                s."prefectureId" AS "prefectureId",
                s.status::text AS status,
                ST_AsMVTGeom(
                    ST_Transform(s.geom::geometry, 3857),
                    tile.env,
                    {_TILE_EXTENT},
                    {_TILE_BUFFER},
                    true
                ) AS geom
            FROM "School" s, tile
            WHERE s.geom IS NOT NULL
              AND ST_Transform(s.geom::geometry, 3857) && tile.env
        )
        SELECT ST_AsMVT(mvt_data, '{layer}', {_TILE_EXTENT}, 'geom')
        FROM mvt_data
        WHERE geom IS NOT NULL;
        """
    )
    try:
        row = await session.execute(sql, {"z": z, "x": x, "y": y})
        result = row.scalar()
    except (ProgrammingError, DBAPIError) as exc:
        # PostGIS missing → "function st_tileenvelope does not exist" or
        # "type \"geography\" does not exist". We map both to 503 — the
        # client should display a friendly maintenance message.
        message = str(exc).lower()
        if (
            "st_tileenvelope" in message
            or "st_asmvt" in message
            or "st_asmvtgeom" in message
            or "geography" in message
            or "postgis" in message
        ):
            raise PostgisUnavailableError(
                detail=(
                    "PostGIS extension is not installed on this Postgres "
                    "instance — vector tiles are unavailable. Install it "
                    "with `CREATE EXTENSION postgis;`."
                ),
                extra={"z": z, "x": x, "y": y},
            ) from exc
        raise

    # ST_AsMVT returns NULL when the input set is empty, and asyncpg/psycopg2
    # surfaces that as Python None. We normalise to bytes for the HTTP layer.
    if result is None:
        return b""
    if isinstance(result, memoryview):
        return bytes(result)
    return bytes(result)
