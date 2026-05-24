"""Module 19 — Router cockpit ministériel.

RBAC : tous les endpoints requièrent MINISTRY_ADMIN ou NATIONAL_ADMIN.
Ce sont les seuls rôles qui ont une vision pays — un REGIONAL_ADMIN ne
voit pas le cockpit national (il a sa propre vue régionale dans les
autres modules).

Endpoints
---------
* ``GET /api/cockpit/kpis/national`` — KPI live cabinet.
* ``GET /api/cockpit/alerts/top`` — top 10 écoles + top 10 régions.
* ``GET /api/cockpit/timeseries/attendance`` — courbe 90 jours.
* ``GET /api/cockpit/timeseries/anomalies`` — barres 12 semaines.
* ``GET /api/cockpit/briefing/today`` — brief LLM/template.
* ``GET /api/cockpit/comparison/{kpi_key}`` — variation J/J-1.
"""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.modules.cockpit.enums import KpiKey
from app.modules.cockpit.schemas import (
    BriefingResponse,
    ComparisonResponse,
    NationalKpiResponse,
    TimeSeriesResponse,
    TopAlertsResponse,
    UrbanRuralGapResponse,
)
from app.modules.cockpit.service import CockpitService
from app.shared.deps import DbSession
from app.shared.enums import UserRole
from app.shared.permissions import require_roles

router = APIRouter(tags=["cockpit"])


# RBAC ≥ MINISTRY_ADMIN (NATIONAL_ADMIN inclus).
COCKPIT_ROLES = (
    UserRole.NATIONAL_ADMIN,
    UserRole.MINISTRY_ADMIN,
)


def _svc(session: DbSession) -> CockpitService:
    return CockpitService(session)


Svc = Annotated[CockpitService, Depends(_svc)]


@router.get(
    "/kpis/national",
    response_model=NationalKpiResponse,
    dependencies=[Depends(require_roles(*COCKPIT_ROLES))],
    summary="KPI agrégés nationaux (cache 30s)",
)
async def get_national_kpis(service: Svc) -> NationalKpiResponse:
    return await service.get_national_kpis()


@router.get(
    "/alerts/top",
    response_model=TopAlertsResponse,
    dependencies=[Depends(require_roles(*COCKPIT_ROLES))],
    summary="Top alertes (écoles + régions)",
)
async def get_top_alerts(
    service: Svc,
    limit: Annotated[int, Query(ge=1, le=50)] = 10,
) -> TopAlertsResponse:
    return await service.get_top_alerts(limit=limit)


@router.get(
    "/timeseries/attendance",
    response_model=TimeSeriesResponse,
    dependencies=[Depends(require_roles(*COCKPIT_ROLES))],
    summary="Série temporelle taux de présence (jour par jour)",
)
async def get_attendance_timeseries(
    service: Svc,
    days: Annotated[int, Query(ge=1, le=365)] = 90,
) -> TimeSeriesResponse:
    return await service.get_attendance_timeseries(days=days)


@router.get(
    "/timeseries/anomalies",
    response_model=TimeSeriesResponse,
    dependencies=[Depends(require_roles(*COCKPIT_ROLES))],
    summary="Série temporelle anomalies par semaine",
)
async def get_anomaly_timeseries(
    service: Svc,
    weeks: Annotated[int, Query(ge=1, le=52)] = 12,
) -> TimeSeriesResponse:
    return await service.get_anomaly_timeseries(weeks=weeks)


@router.get(
    "/briefing/today",
    response_model=BriefingResponse,
    dependencies=[Depends(require_roles(*COCKPIT_ROLES))],
    summary="Brief quotidien (LLM si dispo, sinon template)",
)
async def get_briefing_today(service: Svc) -> BriefingResponse:
    return await service.generate_briefing()


@router.get(
    "/comparison/{kpi_key}",
    response_model=ComparisonResponse,
    dependencies=[Depends(require_roles(*COCKPIT_ROLES))],
    summary="Variation J/J-1 d'un KPI",
)
async def get_comparison(
    kpi_key: KpiKey,
    service: Svc,
) -> ComparisonResponse:
    return await service.compare_with_yesterday(kpi_key)


# ---------------------------------------------------------------------------
# Module 1C — écart urbain / rural
# ---------------------------------------------------------------------------
@router.get(
    "/kpis/urban-rural-gap",
    response_model=UrbanRuralGapResponse,
    dependencies=[Depends(require_roles(*COCKPIT_ROLES))],
    summary="Écart de GPI urbain vs rural sur une année scolaire (cache 30s)",
)
async def get_urban_rural_gap(
    service: Svc,
    schoolYearId: Annotated[str, Query(max_length=30)],
) -> UrbanRuralGapResponse:
    return await service.get_urban_rural_gap(schoolYearId)


__all__ = ["router"]
