"""Phase 3 contract tests — Cartography (PostGIS).

These verify the API surface and Pydantic validation rules. End-to-end tests
that actually run PostGIS queries require a live DB with seed data and live
in tests/integration/ (added once seeding is in place).
"""
import pytest
from httpx import AsyncClient
from pydantic import ValidationError

from app.modules.cartography.schemas import (
    CatchmentsQuery,
    CoverageGapsQuery,
    Feature,
    FeatureCollection,
    GeocodeRequest,
    IndicatorsQuery,
    NearbyQuery,
    NearbyResponse,
    PointGeometry,
    SchoolsGeoQuery,
)


# ---------------------------------------------------------------------
# OpenAPI: every Phase 3 endpoint must be discoverable
# ---------------------------------------------------------------------
@pytest.mark.asyncio
async def test_openapi_exposes_cartography_endpoints(async_client: AsyncClient) -> None:
    response = await async_client.get("/openapi.json")
    assert response.status_code == 200
    paths = response.json()["paths"]

    for url in (
        "/api/cartography/schools",
        "/api/cartography/schools/nearby",
        "/api/cartography/catchments",
        "/api/cartography/coverage-gaps",
        "/api/cartography/indicators",
        "/api/cartography/geocode",
    ):
        assert url in paths, f"Missing endpoint: {url}"


# ---------------------------------------------------------------------
# Pydantic — query validation
# ---------------------------------------------------------------------
def test_nearby_query_validates_coords() -> None:
    NearbyQuery(lat=9.5, lng=-13.7)  # OK
    with pytest.raises(ValidationError):
        NearbyQuery(lat=99, lng=0)
    with pytest.raises(ValidationError):
        NearbyQuery(lat=0, lng=200)
    with pytest.raises(ValidationError):
        NearbyQuery(lat=0, lng=0, radiusKm=0)
    with pytest.raises(ValidationError):
        NearbyQuery(lat=0, lng=0, radiusKm=1000)


def test_coverage_gaps_query_defaults() -> None:
    q = CoverageGapsQuery()
    assert q.radiusKm == 10.0
    assert q.gridStepKm == 5.0
    assert q.regionId is None


def test_coverage_gaps_query_rejects_zero_step() -> None:
    with pytest.raises(ValidationError):
        CoverageGapsQuery(gridStepKm=0)
    with pytest.raises(ValidationError):
        CoverageGapsQuery(radiusKm=0)


def test_indicators_query_levels() -> None:
    IndicatorsQuery(level="region")
    IndicatorsQuery(level="prefecture")
    IndicatorsQuery(level="subPrefecture")
    with pytest.raises(ValidationError):
        IndicatorsQuery(level="commune")  # type: ignore[arg-type]


def test_schools_geo_query_default_only_approved() -> None:
    q = SchoolsGeoQuery()
    assert q.onlyApproved is True


def test_catchments_query_optional_filters() -> None:
    q = CatchmentsQuery()
    assert q.regionId is None and q.prefectureId is None
    q2 = CatchmentsQuery(regionId="r1")
    assert q2.regionId == "r1"


def test_geocode_request_strips_and_validates() -> None:
    dto = GeocodeRequest(address="  Conakry, Kaloum  ")
    assert dto.address == "Conakry, Kaloum"
    with pytest.raises(ValidationError):
        GeocodeRequest(address="ab")  # min_length=3


# ---------------------------------------------------------------------
# Pydantic — GeoJSON shapes
# ---------------------------------------------------------------------
def test_point_geometry_round_trip() -> None:
    geom = PointGeometry(coordinates=(-13.7, 9.5))
    assert geom.type == "Point"
    assert geom.coordinates == (-13.7, 9.5)


def test_feature_accepts_point_polygon_or_none() -> None:
    f1 = Feature(geometry=None, properties={})
    assert f1.geometry is None

    f2 = Feature(
        geometry=PointGeometry(coordinates=(0.0, 0.0)),
        properties={"name": "test"},
    )
    assert f2.geometry.type == "Point"

    # Polygon as plain dict (validated by union)
    f3 = Feature(
        geometry={
            "type": "Polygon",
            "coordinates": [[(0, 0), (1, 0), (1, 1), (0, 1), (0, 0)]],
        },
        properties={},
    )
    assert f3.geometry.type == "Polygon"


def test_feature_collection_serializes() -> None:
    fc = FeatureCollection(
        features=[
            Feature(
                id="s1",
                geometry=PointGeometry(coordinates=(-13.7, 9.5)),
                properties={"name": "École test"},
            )
        ],
        meta={"count": 1},
    )
    dumped = fc.model_dump()
    assert dumped["type"] == "FeatureCollection"
    assert dumped["features"][0]["id"] == "s1"
    assert dumped["meta"]["count"] == 1


def test_nearby_response_contains_distance_in_km() -> None:
    payload = {
        "origin": (9.5, -13.7),
        "radiusKm": 5.0,
        "items": [
            {
                "id": "s1", "name": "École", "code": "E1",
                "latitude": 9.5, "longitude": -13.7,
                "distanceKm": 0.0, "students": 0, "teachers": 0,
            }
        ],
    }
    parsed = NearbyResponse.model_validate(payload)
    assert parsed.items[0].distanceKm == 0.0


# ---------------------------------------------------------------------
# Auth required
# ---------------------------------------------------------------------
@pytest.mark.asyncio
@pytest.mark.parametrize("url", [
    "/api/cartography/schools",
    "/api/cartography/schools/nearby?lat=9.5&lng=-13.7",
    "/api/cartography/catchments",
    "/api/cartography/coverage-gaps",
    "/api/cartography/indicators",
])
async def test_cartography_endpoints_require_bearer_token(
    async_client: AsyncClient, url: str
) -> None:
    response = await async_client.get(url)
    assert response.status_code == 401, f"{url} should return 401 without auth"
    assert response.json()["code"] == "unauthorized"
