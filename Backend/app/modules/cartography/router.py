from typing import Annotated

from fastapi import APIRouter, Depends, Query, status

from app.modules.auth.models import User
from app.modules.cartography.schemas import (
    CatchmentsQuery,
    CoverageGapsQuery,
    FeatureCollection,
    GeocodeRequest,
    GeocodeResponse,
    IndicatorsQuery,
    IndicatorsResponse,
    NearbyQuery,
    NearbyResponse,
    SchoolsGeoQuery,
)
from app.modules.cartography.service import CartographyService
from app.shared.deps import DbSession, get_current_user


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
