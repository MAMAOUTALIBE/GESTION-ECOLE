"""Pydantic schemas for the cartography module.

Models are aligned with the GeoJSON spec (RFC 7946) so any Leaflet/Mapbox
client can consume them directly. Extra metadata travels in the `properties`
dict of each Feature.
"""
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, confloat


# ---------------------------------------------------------------------
# GeoJSON primitives
# ---------------------------------------------------------------------
class PointGeometry(BaseModel):
    type: Literal["Point"] = "Point"
    coordinates: tuple[float, float]  # [longitude, latitude] per RFC 7946


class PolygonGeometry(BaseModel):
    type: Literal["Polygon"] = "Polygon"
    coordinates: list[list[tuple[float, float]]]  # one outer ring + holes


class MultiPolygonGeometry(BaseModel):
    type: Literal["MultiPolygon"] = "MultiPolygon"
    coordinates: list[list[list[tuple[float, float]]]]


class Feature(BaseModel):
    """Generic GeoJSON Feature."""
    model_config = ConfigDict(arbitrary_types_allowed=True)

    type: Literal["Feature"] = "Feature"
    id: str | None = None
    geometry: PointGeometry | PolygonGeometry | MultiPolygonGeometry | None
    properties: dict[str, Any]


class FeatureCollection(BaseModel):
    type: Literal["FeatureCollection"] = "FeatureCollection"
    features: list[Feature]
    # Non-standard but practical: a top-level `meta` block for client UIs
    meta: dict[str, Any] | None = None


# ---------------------------------------------------------------------
# Query parameters
# ---------------------------------------------------------------------
class SchoolsGeoQuery(BaseModel):
    """Filters for `GET /api/cartography/schools`."""
    model_config = ConfigDict(str_strip_whitespace=True)

    regionId: str | None = None
    prefectureId: str | None = None
    subPrefectureId: str | None = None
    onlyApproved: bool = True


class NearbyQuery(BaseModel):
    """Filters for `GET /api/cartography/schools/nearby`."""
    lat: confloat(ge=-90, le=90)  # type: ignore[valid-type]
    lng: confloat(ge=-180, le=180)  # type: ignore[valid-type]
    radiusKm: confloat(gt=0, le=200) = 5.0  # type: ignore[valid-type]
    limit: int = Field(default=20, ge=1, le=200)


class CatchmentsQuery(BaseModel):
    """Voronoi catchment areas filters."""
    regionId: str | None = None
    prefectureId: str | None = None


class CoverageGapsQuery(BaseModel):
    """Detect zones blanches (areas without any school within radiusKm)."""
    regionId: str | None = None
    radiusKm: confloat(gt=0, le=50) = 10.0  # type: ignore[valid-type]
    gridStepKm: confloat(gt=0, le=20) = 5.0  # type: ignore[valid-type]


class IndicatorsQuery(BaseModel):
    level: Literal["region", "prefecture", "subPrefecture"] = "region"


# ---------------------------------------------------------------------
# Spatial indicators (per territory)
# ---------------------------------------------------------------------
class TerritoryIndicator(BaseModel):
    territoryId: str
    territoryName: str
    parentId: str | None = None
    parentName: str | None = None
    schools: int
    geolocatedSchools: int
    students: int
    teachers: int
    studentsPerTeacher: float
    avgDistanceToNearestSchoolKm: float | None = None
    coverageRate: float  # 0–100, percent of schools with geolocation


class IndicatorsResponse(BaseModel):
    level: Literal["region", "prefecture", "subPrefecture"]
    items: list[TerritoryIndicator]


# ---------------------------------------------------------------------
# Nearest schools result
# ---------------------------------------------------------------------
class NearbySchool(BaseModel):
    id: str
    name: str
    code: str
    latitude: float
    longitude: float
    distanceKm: float
    students: int = 0
    teachers: int = 0


class NearbyResponse(BaseModel):
    origin: tuple[float, float]
    radiusKm: float
    items: list[NearbySchool]


# ---------------------------------------------------------------------
# Geocoding
# ---------------------------------------------------------------------
class GeocodeRequest(BaseModel):
    """Trigger geocoding of an address (async via Celery)."""
    model_config = ConfigDict(str_strip_whitespace=True)
    address: str = Field(min_length=3, max_length=512)


class GeocodeResponse(BaseModel):
    """Returned synchronously when requesting a geocode job."""
    taskId: str
    status: Literal["queued"] = "queued"
    address: str
