"""Pydantic schemas for the cartography module.

Models are aligned with the GeoJSON spec (RFC 7946) so any Leaflet/Mapbox
client can consume them directly. Extra metadata travels in the `properties`
dict of each Feature.
"""
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, confloat, field_validator

# Guinea geographic bounds (matches tests/integration/factories.py constants).
# Used by validators on isochrone/density inputs to reject obviously wrong
# coordinates (Module 5 — refuse anything outside the national perimeter).
GUINEA_LAT_MIN: float = 7.0
GUINEA_LAT_MAX: float = 13.0
GUINEA_LON_MIN: float = -15.5
GUINEA_LON_MAX: float = -7.5


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


# ---------------------------------------------------------------------
# Module 5 — Walking isochrones (approximated by Haversine buffer)
# ---------------------------------------------------------------------
class IsochroneRequest(BaseModel):
    """POST body for `/api/cartography/isochrones`.

    Validators enforce Guinea bounds because the MVP is national-only — a
    coordinate outside the country is almost always a typo or a bug in the
    client and we'd rather fail loudly than render a circle in the ocean.
    """

    model_config = ConfigDict(extra="forbid")

    lat: float = Field(
        ...,
        ge=-90,
        le=90,
        description="Latitude WGS84 — must fall within Guinea bounds.",
    )
    lon: float = Field(
        ...,
        ge=-180,
        le=180,
        description="Longitude WGS84 — must fall within Guinea bounds.",
    )
    intervals: list[int] = Field(
        default_factory=lambda: [15, 30, 45, 60],
        min_length=1,
        max_length=12,
        description="Walking durations in minutes (>= 1, <= 120 per item).",
    )
    speedKmh: float = Field(
        default=5.0,
        gt=0,
        le=15.0,
        description="Walking pace; default 5 km/h matches WHO baseline.",
    )

    @field_validator("lat")
    @classmethod
    def _lat_in_guinea(cls, v: float) -> float:
        if not (GUINEA_LAT_MIN <= v <= GUINEA_LAT_MAX):
            raise ValueError(
                f"latitude {v} outside Guinea bounds "
                f"[{GUINEA_LAT_MIN}, {GUINEA_LAT_MAX}]"
            )
        return v

    @field_validator("lon")
    @classmethod
    def _lon_in_guinea(cls, v: float) -> float:
        if not (GUINEA_LON_MIN <= v <= GUINEA_LON_MAX):
            raise ValueError(
                f"longitude {v} outside Guinea bounds "
                f"[{GUINEA_LON_MIN}, {GUINEA_LON_MAX}]"
            )
        return v

    @field_validator("intervals")
    @classmethod
    def _intervals_bounded(cls, v: list[int]) -> list[int]:
        for m in v:
            if m < 1 or m > 120:
                raise ValueError(
                    f"each interval must be between 1 and 120 minutes (got {m})"
                )
        return v


# ---------------------------------------------------------------------
# Module 5 — Extended spatial indicators
# ---------------------------------------------------------------------
class DensityFeature(BaseModel):
    """One sub-prefecture in the student-density choropleth."""

    subPrefectureId: str
    name: str
    regionId: str
    prefectureId: str
    studentCount: int
    areaKm2: float
    density: float  # students per square kilometre


class DensityResponse(BaseModel):
    items: list[DensityFeature]
    unit: Literal["students_per_km2"] = "students_per_km2"


class RegionDistanceStat(BaseModel):
    regionId: str
    regionName: str
    avgSchoolDistanceKm: float | None = None
    schoolCount: int
    studentCount: int


class RegionDistanceResponse(BaseModel):
    items: list[RegionDistanceStat]
    unit: Literal["kilometers"] = "kilometers"
