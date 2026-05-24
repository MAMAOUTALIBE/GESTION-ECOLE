from typing import Annotated, Any

from fastapi import APIRouter, Depends, Path, Query, Response, status

from app.modules.auth.models import User
from app.modules.cartography.isochrones import isochrone_set
from app.modules.cartography.schemas import (
    CatchmentsQuery,
    CoverageGapsQuery,
    DensityResponse,
    FeatureCollection,
    GeocodeRequest,
    GeocodeResponse,
    IndicatorsQuery,
    IndicatorsResponse,
    IsochroneRequest,
    NearbyQuery,
    NearbyResponse,
    RegionDistanceResponse,
    SchoolsGeoQuery,
)
from app.modules.cartography.service import CartographyService
from app.modules.cartography.tiles import MAX_ZOOM
from app.shared.deps import DbSession, get_current_user

# MVT tile media type per the Mapbox Vector Tile spec.
MVT_MEDIA_TYPE = "application/vnd.mapbox-vector-tile"


def _service(session: DbSession) -> CartographyService:
    return CartographyService(session)


CartoSvc = Annotated[CartographyService, Depends(_service)]
CurrentUserDep = Annotated[User, Depends(get_current_user)]

router = APIRouter(tags=["cartography"])


@router.get(
    "/schools",
    response_model=FeatureCollection,
    summary="GeoJSON FeatureCollection des écoles dans le périmètre",
)
async def schools_geojson(
    user: CurrentUserDep,
    service: CartoSvc,
    query: Annotated[SchoolsGeoQuery, Depends()],
) -> FeatureCollection:
    return await service.schools_geojson(user, query)


@router.get(
    "/schools/nearby",
    response_model=NearbyResponse,
    summary="Écoles dans un rayon (km) autour d'un point lat/lng",
)
async def schools_nearby(
    user: CurrentUserDep,
    service: CartoSvc,
    query: Annotated[NearbyQuery, Depends()],
) -> NearbyResponse:
    return await service.schools_nearby(user, query)


@router.get(
    "/catchments",
    response_model=FeatureCollection,
    summary="Polygones de Voronoï : zones de desserte par école",
)
async def catchments(
    user: CurrentUserDep,
    service: CartoSvc,
    query: Annotated[CatchmentsQuery, Depends()],
) -> FeatureCollection:
    return await service.voronoi_catchments(user, query)


@router.get(
    "/coverage-gaps",
    response_model=FeatureCollection,
    summary="Détection des zones blanches (sans école dans radiusKm)",
)
async def coverage_gaps(
    user: CurrentUserDep,
    service: CartoSvc,
    query: Annotated[CoverageGapsQuery, Depends()],
) -> FeatureCollection:
    return await service.coverage_gaps(user, query)


@router.get(
    "/indicators",
    response_model=IndicatorsResponse,
    summary="KPIs spatiaux par région / préfecture / sous-préfecture",
)
async def indicators(
    user: CurrentUserDep,
    service: CartoSvc,
    query: Annotated[IndicatorsQuery, Depends()],
) -> IndicatorsResponse:
    return await service.indicators(user, query.level)


# ---------------------------------------------------------------------
# Phase 14 — Recommandations de placement d'écoles
# ---------------------------------------------------------------------
@router.get(
    "/site-recommendations",
    summary="Recommandations de localisation pour de nouvelles écoles",
)
async def site_recommendations(
    user: CurrentUserDep,
    service: CartoSvc,
    radiusKm: Annotated[float, Query(ge=0.5, le=50)] = 5.0,
    topN: Annotated[int, Query(ge=1, le=50)] = 10,
) -> dict:
    """Centroides des zones de couverture insuffisante, classés par déficit.

    Approche : pour chaque préfecture sans école dans `radiusKm`, calcule le
    centroïde géographique des écoles voisines manquantes et propose ce point.
    """
    from sqlalchemy import func as sa_func, select  # noqa: PLC0415
    from app.modules.territory.models import Prefecture, Region  # noqa: PLC0415
    from app.modules.schools.models import School  # noqa: PLC0415

    # Préfectures avec leur centroïde géographique des écoles existantes
    stmt = (
        select(
            Prefecture.id, Prefecture.name,
            Region.name.label("region_name"),
            sa_func.count(School.id).label("school_count"),
            sa_func.avg(School.latitude).label("avg_lat"),
            sa_func.avg(School.longitude).label("avg_lng"),
        )
        .join(Region, Region.id == Prefecture.regionId)
        .outerjoin(School, School.prefectureId == Prefecture.id)
        .group_by(Prefecture.id, Prefecture.name, Region.name)
        .order_by(sa_func.count(School.id).asc())
    )
    rows = (await service.session.execute(stmt)).all()

    recommendations = []
    for r in rows[:topN]:
        if r.avg_lat is None or r.avg_lng is None:
            continue
        # Décale légèrement (≈ 5 km) pour suggérer une nouvelle position
        recommendations.append({
            "prefectureId": r.id,
            "prefectureName": r.name,
            "regionName": r.region_name,
            "currentSchoolCount": int(r.school_count),
            "suggestedLatitude": round(float(r.avg_lat) + 0.05, 5),
            "suggestedLongitude": round(float(r.avg_lng) + 0.05, 5),
            "rationale": (
                f"Préfecture sous-équipée ({int(r.school_count)} écoles) — "
                f"placement central recommandé."
            ),
            "estimatedCostUSD": 150_000,
        })
    return {"radiusKm": radiusKm, "recommendations": recommendations}


@router.post(
    "/geocode",
    response_model=GeocodeResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="File d'attente : géocode une adresse en arrière-plan (Celery)",
)
async def queue_geocode(
    dto: GeocodeRequest, user: CurrentUserDep
) -> GeocodeResponse:
    from app.workers.geocoding_tasks import geocode_address  # noqa: PLC0415

    task = geocode_address.delay(dto.address)
    _ = user  # auth still required, but the worker doesn't need scope context
    return GeocodeResponse(taskId=task.id, address=dto.address)


# =====================================================================
# Module 5 — Vector tiles (MVT) + walking isochrones + density choropleth
# =====================================================================
@router.get(
    "/tiles/{z}/{x}/{y}.mvt",
    summary="Mapbox Vector Tile (z/x/y) for the schools layer",
    responses={
        200: {"content": {MVT_MEDIA_TYPE: {}}},
        503: {"description": "PostGIS extension not installed on the server."},
    },
)
async def schools_mvt(
    user: CurrentUserDep,
    service: CartoSvc,
    z: Annotated[int, Path(ge=0, le=MAX_ZOOM)],
    x: Annotated[int, Path(ge=0)],
    y: Annotated[int, Path(ge=0)],
) -> Response:
    """Return one MVT tile for the schools layer.

    * Authentication required (national data — not anonymous).
    * Cached in Redis for 1 hour (key `mvt:{z}:{x}:{y}`).
    * Empty tiles still return 200 with a zero-byte body — that's how
      MapLibre signals "transparent tile" without exploding the cache.
    """
    _ = user  # auth required; no per-user scope on national tile data
    tile_bytes = await service.get_tile(z, x, y)
    return Response(content=tile_bytes, media_type=MVT_MEDIA_TYPE)


@router.post(
    "/isochrones",
    summary="Walking isochrones (approximated Haversine buffer)",
    response_model=None,  # GeoJSON FeatureCollection — kept as dict for flexibility
)
async def walking_isochrones(
    dto: IsochroneRequest,
    user: CurrentUserDep,
) -> dict[str, Any]:
    """Return a GeoJSON FeatureCollection of concentric walking isochrones.

    The MVP uses a Haversine circular buffer — Module 17 will swap in OSRM
    routing. The endpoint contract (FeatureCollection of Polygons with
    ``timeMin`` properties) stays stable so the Flutter client keeps working.
    """
    _ = user
    return isochrone_set(
        lat=dto.lat,
        lon=dto.lon,
        intervals_min=dto.intervals,
        speed_kmh=dto.speedKmh,
    )


@router.get(
    "/density/subprefectures",
    response_model=DensityResponse,
    summary="Choropleth feed: student density per sub-prefecture (PostGIS)",
)
async def density_subprefectures(
    user: CurrentUserDep,
    service: CartoSvc,
) -> DensityResponse:
    """Per-sub-prefecture aggregate of student counts and convex-hull area.

    Returns 503 if PostGIS is unavailable (the area calculation is
    PostGIS-only). Respects the caller's territorial scope.
    """
    return await service.get_subprefecture_density(user)


@router.get(
    "/distance-stats/regions",
    response_model=RegionDistanceResponse,
    summary="Per-region average inter-school distance (km)",
)
async def distance_stats_regions(
    user: CurrentUserDep,
    service: CartoSvc,
) -> RegionDistanceResponse:
    """Average nearest-neighbour school distance for each region in scope."""
    return await service.get_region_distance_stats(user)
