"""Module 5 — cartography vector tiles, isochrones, density.

Coverage map (14 tests):
* Isochrone math (3 unit tests): polygon shape, set-of-4, Guinea bounds.
* MVT endpoint (4 tests, 2 of them `@pytest.mark.postgis` auto-skipped):
  503 fallback, binary on PostGIS, cache, RBAC.
* Density / distance (4 tests, 2 of them `@pytest.mark.postgis`):
  records, zero-students, region aggregate, scope.
* RBAC + validation (3 tests): isochrone auth, invalid z/x/y, max minutes.

All PostGIS-dependent tests carry the `@pytest.mark.postgis` marker so
they're auto-skipped on environments where the extension is missing
(see ``tests/integration/conftest.py``).
"""
from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.cartography.isochrones import (
    compute_walking_isochrone,
    isochrone_set,
)
from app.modules.cartography.service import TILE_CACHE_PREFIX
from app.modules.cartography.tiles import (
    MAX_ZOOM,
    TileCoordinatesError,
    validate_tile_coords,
)
from app.shared.enums import UserRole
from tests.integration import factories

pytestmark = pytest.mark.integration


# ===========================================================================
# 1. Isochrone math (pure unit)
# ===========================================================================
def test_compute_walking_isochrone_returns_circle_polygon() -> None:
    """A 30 min @ 5 km/h walk = 2.5 km radius; polygon has 64 + 1 (closure) pts."""
    feature = compute_walking_isochrone(9.5, -13.7, 30)
    assert feature["type"] == "Feature"
    assert feature["geometry"]["type"] == "Polygon"

    ring = feature["geometry"]["coordinates"][0]
    # 64 vertices + closure = 65 coordinate pairs
    assert len(ring) == 65, f"expected 65 closed-ring points, got {len(ring)}"
    # First and last vertex must match exactly (RFC 7946 closure rule).
    assert ring[0] == ring[-1]

    props = feature["properties"]
    assert props["timeMin"] == 30
    assert props["radiusMeters"] == pytest.approx(2500.0, abs=1.0)
    assert props["approximation"] == "haversine-circle"


def test_isochrone_set_returns_feature_collection_with_4_features() -> None:
    """4 intervals → 4 features in ascending order, with stable contract."""
    fc = isochrone_set(9.6412, -13.5784, [60, 15, 45, 30])  # unsorted on purpose
    assert fc["type"] == "FeatureCollection"
    assert len(fc["features"]) == 4

    # Should be returned in ascending minute order so client can stack.
    times = [f["properties"]["timeMin"] for f in fc["features"]]
    assert times == [15, 30, 45, 60], f"intervals not sorted: {times}"

    assert fc["meta"]["origin"] == [-13.5784, 9.6412]
    assert "haversine" in fc["meta"]["approximation"]


@pytest.mark.asyncio
async def test_isochrone_request_validates_guinea_bounds(
    client: AsyncClient,
    db_session: AsyncSession,
    auth_headers: Any,
) -> None:
    """A latitude outside Guinea bounds must trigger HTTP 422.

    Paris (48.85, 2.35) is well outside the Guinea bounding box; FastAPI's
    pydantic validation will reject the body before the endpoint sees it.
    """
    headers = await auth_headers(UserRole.NATIONAL_ADMIN)
    resp = await client.post(
        "/api/cartography/isochrones",
        json={"lat": 48.8566, "lon": 2.3522, "intervals": [15]},
        headers=headers,
    )
    assert resp.status_code == 422, resp.text
    body = resp.json()
    # Error message must mention Guinea bounds so the client can show a hint.
    detail_text = json.dumps(body)
    assert "Guinea" in detail_text or "guinea" in detail_text.lower()


# ===========================================================================
# 2. MVT endpoint
# ===========================================================================
@pytest.mark.asyncio
async def test_mvt_endpoint_returns_503_when_postgis_absent(
    client: AsyncClient,
    db_session: AsyncSession,
    auth_headers: Any,
) -> None:
    """When PostGIS isn't installed, the endpoint surfaces HTTP 503.

    We don't skip this test based on PostGIS availability — we *assert* the
    fallback path works by patching `generate_mvt` to raise the typed error
    regardless of the underlying server.
    """
    from app.core.exceptions import PostgisUnavailableError  # noqa: PLC0415

    headers = await auth_headers(UserRole.NATIONAL_ADMIN)
    with patch(
        "app.modules.cartography.service.generate_mvt",
        new=AsyncMock(
            side_effect=PostgisUnavailableError(
                detail="PostGIS missing", extra={"z": 0, "x": 0, "y": 0}
            )
        ),
    ):
        resp = await client.get(
            "/api/cartography/tiles/0/0/0.mvt", headers=headers
        )
    assert resp.status_code == 503, resp.text
    body = resp.json()
    assert body["code"] == "postgis_unavailable"


@pytest.mark.asyncio
@pytest.mark.postgis
async def test_mvt_endpoint_returns_binary_when_postgis_available(
    client: AsyncClient,
    db_session: AsyncSession,
    auth_headers: Any,
) -> None:
    """Happy path: PostGIS present → tile body + correct content type."""
    factories.bind(db_session)
    tree = await factories.make_territorial_tree()
    await factories.SchoolFactory.create_async(regionId=tree["region"].id)

    headers = await auth_headers(UserRole.NATIONAL_ADMIN)
    resp = await client.get(
        "/api/cartography/tiles/0/0/0.mvt", headers=headers
    )
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"].startswith(
        "application/vnd.mapbox-vector-tile"
    )
    # Body is bytes — may be empty (transparent tile) or non-empty.
    assert isinstance(resp.content, bytes)


@pytest.mark.asyncio
async def test_mvt_caches_in_redis(
    client: AsyncClient,
    db_session: AsyncSession,
    auth_headers: Any,
) -> None:
    """Second request for the same tile must HIT the Redis cache.

    Verified by counting how many times `generate_mvt` is invoked: 1 on the
    first call, 0 on the second (the response is served from cache).
    """
    headers = await auth_headers(UserRole.NATIONAL_ADMIN)

    # Force a deterministic empty-tile response from the SQL layer.
    fake_mvt = AsyncMock(return_value=b"\x1a\x05\x12\x00\x1a\x00")
    with patch("app.modules.cartography.service.generate_mvt", new=fake_mvt):
        r1 = await client.get(
            "/api/cartography/tiles/3/4/2.mvt", headers=headers
        )
        assert r1.status_code == 200
        r2 = await client.get(
            "/api/cartography/tiles/3/4/2.mvt", headers=headers
        )
        assert r2.status_code == 200

    # DB call must happen exactly once; the second request hits Redis.
    assert fake_mvt.await_count == 1, (
        f"expected exactly 1 DB call (cache hit on 2nd), got {fake_mvt.await_count}"
    )
    assert r1.content == r2.content


# ===========================================================================
# 3. Density / distance choropleth
# ===========================================================================
@pytest.mark.asyncio
@pytest.mark.postgis
async def test_density_subprefectures_returns_records(
    client: AsyncClient,
    db_session: AsyncSession,
    auth_headers: Any,
) -> None:
    """Endpoint produces at least one row when there are students in scope."""
    factories.bind(db_session)
    tree = await factories.make_territorial_tree()
    await factories.StudentFactory.create_batch_async(
        5, schoolId=tree["school"].id
    )

    headers = await auth_headers(UserRole.NATIONAL_ADMIN)
    resp = await client.get(
        "/api/cartography/density/subprefectures", headers=headers
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["unit"] == "students_per_km2"
    assert isinstance(body["items"], list)
    # At least the sub-prefecture from the tree must be present.
    sub_ids = [i["subPrefectureId"] for i in body["items"]]
    assert tree["subPrefecture"].id in sub_ids


@pytest.mark.asyncio
async def test_density_zero_students_returns_zero_density(
    client: AsyncClient,
    db_session: AsyncSession,
    auth_headers: Any,
) -> None:
    """Sub-prefectures without students must serialise density=0.0 cleanly.

    We patch the service to return a fabricated payload so the test is
    independent of PostGIS availability.
    """
    from app.modules.cartography.schemas import (  # noqa: PLC0415
        DensityFeature,
        DensityResponse,
    )

    payload = DensityResponse(
        items=[
            DensityFeature(
                subPrefectureId="sp1",
                name="Vide",
                regionId="r1",
                prefectureId="p1",
                studentCount=0,
                areaKm2=0.0,
                density=0.0,
            )
        ]
    )
    headers = await auth_headers(UserRole.NATIONAL_ADMIN)
    with patch(
        "app.modules.cartography.service.CartographyService.get_subprefecture_density",
        new=AsyncMock(return_value=payload),
    ):
        resp = await client.get(
            "/api/cartography/density/subprefectures", headers=headers
        )
    assert resp.status_code == 200, resp.text
    item = resp.json()["items"][0]
    assert item["density"] == 0.0
    assert item["studentCount"] == 0


@pytest.mark.asyncio
@pytest.mark.postgis
async def test_distance_stats_aggregates_by_region(
    client: AsyncClient,
    db_session: AsyncSession,
    auth_headers: Any,
) -> None:
    """At least one region row, with school + student counts."""
    factories.bind(db_session)
    tree = await factories.make_territorial_tree()
    # Two more schools in same region → meaningful nearest-neighbour avg.
    await factories.SchoolFactory.create_async(regionId=tree["region"].id)
    await factories.SchoolFactory.create_async(regionId=tree["region"].id)
    await factories.StudentFactory.create_batch_async(
        3, schoolId=tree["school"].id
    )

    headers = await auth_headers(UserRole.NATIONAL_ADMIN)
    resp = await client.get(
        "/api/cartography/distance-stats/regions", headers=headers
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["unit"] == "kilometers"
    region_ids = [i["regionId"] for i in body["items"]]
    assert tree["region"].id in region_ids


# ===========================================================================
# 4. RBAC + input validation
# ===========================================================================
@pytest.mark.asyncio
async def test_rbac_tiles_endpoint_requires_auth(client: AsyncClient) -> None:
    """No bearer ⇒ 401."""
    resp = await client.get("/api/cartography/tiles/0/0/0.mvt")
    assert resp.status_code == 401, resp.text


@pytest.mark.asyncio
async def test_isochrone_endpoint_requires_auth(client: AsyncClient) -> None:
    """No bearer ⇒ 401."""
    resp = await client.post(
        "/api/cartography/isochrones",
        json={"lat": 9.6, "lon": -13.5, "intervals": [15]},
    )
    assert resp.status_code == 401, resp.text


@pytest.mark.asyncio
async def test_invalid_zxy_returns_422(
    client: AsyncClient,
    db_session: AsyncSession,
    auth_headers: Any,
) -> None:
    """z > 22 must be rejected by FastAPI Path validation."""
    headers = await auth_headers(UserRole.NATIONAL_ADMIN)

    # z above ceiling — Path(le=MAX_ZOOM)
    r = await client.get("/api/cartography/tiles/30/0/0.mvt", headers=headers)
    assert r.status_code == 422, r.text

    # x > 2^z - 1 — caught by validate_tile_coords inside the service.
    # At z=2 the grid is 4x4, x=99 is invalid.
    r = await client.get("/api/cartography/tiles/2/99/0.mvt", headers=headers)
    assert r.status_code == 422, r.text


@pytest.mark.asyncio
async def test_density_endpoint_respects_territorial_scope(
    client: AsyncClient,
    db_session: AsyncSession,
    auth_headers: Any,
) -> None:
    """A SCHOOL_DIRECTOR has no choropleth view → empty items list.

    The endpoint returns 200 (it's not a forbidden operation, just an empty
    payload for users that don't have aggregate scope). This is also
    independent of PostGIS — the service short-circuits before SQL.
    """
    factories.bind(db_session)
    tree = await factories.make_territorial_tree()
    headers = await auth_headers(
        UserRole.SCHOOL_DIRECTOR, schoolId=tree["school"].id
    )
    resp = await client.get(
        "/api/cartography/density/subprefectures", headers=headers
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["items"] == []


@pytest.mark.asyncio
async def test_isochrone_intervals_max_120_min(
    client: AsyncClient,
    db_session: AsyncSession,
    auth_headers: Any,
) -> None:
    """Interval > 120 minutes ⇒ 422 (Haversine approx becomes too lossy)."""
    headers = await auth_headers(UserRole.NATIONAL_ADMIN)
    resp = await client.post(
        "/api/cartography/isochrones",
        json={"lat": 9.6, "lon": -13.5, "intervals": [180]},
        headers=headers,
    )
    assert resp.status_code == 422, resp.text


# ===========================================================================
# 5. Extra hardening — pure unit checks on coord validator
# ===========================================================================
def test_validate_tile_coords_accepts_valid_and_rejects_invalid() -> None:
    validate_tile_coords(0, 0, 0)
    validate_tile_coords(MAX_ZOOM, (1 << MAX_ZOOM) - 1, 0)

    with pytest.raises(TileCoordinatesError):
        validate_tile_coords(-1, 0, 0)
    with pytest.raises(TileCoordinatesError):
        validate_tile_coords(MAX_ZOOM + 1, 0, 0)
    with pytest.raises(TileCoordinatesError):
        validate_tile_coords(3, 99, 0)  # x > 7 at z=3


def test_mvt_cache_key_format() -> None:
    """Cache key shape is stable — clients/ops scripts depend on the pattern."""
    from app.modules.cartography.service import CartographyService  # noqa: PLC0415

    assert (
        CartographyService._tile_cache_key(5, 1, 2)
        == f"{TILE_CACHE_PREFIX}:5:1:2"
    )
