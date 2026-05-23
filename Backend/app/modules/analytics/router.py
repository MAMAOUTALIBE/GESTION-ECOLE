import csv
import io
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response

from app.modules.analytics.schemas import (
    AttendanceTrends,
    AuditLogPage,
    AuditLogQuery,
    CohortReport,
    EnrollmentTrends,
    EquityResponse,
    NationalKpis,
    PolicySimulationRequest,
    PolicySimulationResponse,
    QualityResponse,
    TerritoriesResponse,
    TerritoryLevel,
    TopMetric,
    TopSchoolsResponse,
)
from app.modules.analytics.service import AnalyticsService
from app.modules.auth.models import User
from app.shared.deps import DbSession, get_current_user
from app.shared.enums import UserRole
from app.shared.permissions import require_roles

ADMIN_AUDIT_ROLES = (UserRole.NATIONAL_ADMIN, UserRole.MINISTRY_ADMIN)

ExportType = Literal["national", "territories", "top-schools", "quality"]


def _service(session: DbSession) -> AnalyticsService:
    return AnalyticsService(session)


AnSvc = Annotated[AnalyticsService, Depends(_service)]
CurrentUserDep = Annotated[User, Depends(get_current_user)]

router = APIRouter(tags=["analytics"])


# ---------------------------------------------------------------------
# JSON KPI endpoints
# ---------------------------------------------------------------------
@router.get(
    "/national",
    response_model=NationalKpis,
    summary="KPIs nationaux dans le scope du caller (national si admin)",
)
async def national(user: CurrentUserDep, service: AnSvc) -> NationalKpis:
    return await service.national(user)


@router.get(
    "/territories",
    response_model=TerritoriesResponse,
    summary="Comparaison territoriale (region / prefecture / sub-prefecture)",
)
async def territories(
    user: CurrentUserDep,
    service: AnSvc,
    level: Annotated[TerritoryLevel, Query()] = "region",
) -> TerritoriesResponse:
    return await service.territories(user, level)


@router.get(
    "/attendance/trends",
    response_model=AttendanceTrends,
    summary="Série temporelle attendance (par jour, fenêtre paramétrable)",
)
async def attendance_trends(
    user: CurrentUserDep,
    service: AnSvc,
    days: Annotated[int, Query(ge=1, le=365)] = 30,
) -> AttendanceTrends:
    return await service.attendance_trends(user, days)


@router.get(
    "/enrollment/trends",
    response_model=EnrollmentTrends,
    summary="Évolution des effectifs (par mois, fenêtre paramétrable)",
)
async def enrollment_trends(
    user: CurrentUserDep,
    service: AnSvc,
    months: Annotated[int, Query(ge=1, le=60)] = 12,
) -> EnrollmentTrends:
    return await service.enrollment_trends(user, months)


@router.get(
    "/top-schools",
    response_model=TopSchoolsResponse,
    summary="Classement écoles (effectifs / présence / GPS / ratio)",
)
async def top_schools(
    user: CurrentUserDep,
    service: AnSvc,
    metric: Annotated[TopMetric, Query()] = "students",
    limit: Annotated[int, Query(ge=1, le=100)] = 10,
) -> TopSchoolsResponse:
    return await service.top_schools(user, metric, limit)


@router.get(
    "/quality",
    response_model=QualityResponse,
    summary="Score de qualité données + détail des champs manquants",
)
async def quality(user: CurrentUserDep, service: AnSvc) -> QualityResponse:
    return await service.quality(user)


# ---------------------------------------------------------------------
# Phase 14 — Forecasts (projections d'effectifs)
# ---------------------------------------------------------------------
@router.get(
    "/enrollment/forecast",
    summary="Projection d'effectifs sur N années (régression linéaire)",
)
async def enrollment_forecast(
    user: CurrentUserDep,
    service: AnSvc,
    horizonYears: Annotated[int, Query(ge=1, le=10)] = 5,
) -> dict:
    return await service.enrollment_forecast(user, horizonYears)


# ---------------------------------------------------------------------
# Phase 10 — Cohort, Equity, Policy Simulator (decisional power)
# ---------------------------------------------------------------------
@router.get(
    "/cohorts",
    response_model=CohortReport,
    summary="Cohort analysis : effectifs par niveau (CP1→Tle), genre, redoublants",
)
async def cohorts(
    user: CurrentUserDep,
    service: AnSvc,
    schoolYearId: Annotated[str | None, Query()] = None,
) -> CohortReport:
    return await service.cohorts(user, schoolYearId)


@router.get(
    "/equity",
    response_model=EquityResponse,
    summary="Index d'équité par région : GPI + couvertures infra (toilettes filles, élec, eau)",
)
async def equity(user: CurrentUserDep, service: AnSvc) -> EquityResponse:
    return await service.equity(user)


@router.post(
    "/policy-simulator",
    response_model=PolicySimulationResponse,
    summary="Simulateur politique : impact estimé d'investissements (écoles/enseignants/infra)",
)
async def policy_simulator(
    dto: PolicySimulationRequest,
    user: CurrentUserDep,
    service: AnSvc,
) -> PolicySimulationResponse:
    return await service.policy_simulate(user, dto)


# ---------------------------------------------------------------------
# Audit logs (cross-cutting observability — national admins only)
# ---------------------------------------------------------------------
@router.get(
    "/audit-logs",
    response_model=AuditLogPage,
    summary="Lecture paginée des audit logs (admins nationaux uniquement)",
    dependencies=[Depends(require_roles(*ADMIN_AUDIT_ROLES))],
)
async def list_audit_logs(
    user: CurrentUserDep,
    service: AnSvc,
    query: Annotated[AuditLogQuery, Depends()],
) -> AuditLogPage:
    _ = user
    return await service.list_audit_logs(query)


# ---------------------------------------------------------------------
# CSV export — same datasets, downloadable
# ---------------------------------------------------------------------
@router.get(
    "/export",
    summary="Export CSV (UTF-8 BOM) d'un dataset analytics",
    responses={200: {"content": {"text/csv": {}}}},
)
async def export_csv(
    user: CurrentUserDep,
    service: AnSvc,
    type: Annotated[ExportType, Query()],
    level: Annotated[TerritoryLevel | None, Query()] = None,
    metric: Annotated[TopMetric | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
) -> Response:
    buf = io.StringIO()
    writer = csv.writer(buf, delimiter=";")  # ; for Excel-FR friendliness

    if type == "national":
        kpis = await service.national(user)
        writer.writerow(["metric", "value"])
        for k, v in kpis.model_dump().items():
            writer.writerow([k, v])
        filename = "national-kpis.csv"
    elif type == "territories":
        resp = await service.territories(user, level or "region")
        writer.writerow([
            "id", "name", "parentId", "parentName",
            "schools", "students", "teachers", "classes",
            "geolocatedSchools", "gpsCoverageRate",
            "studentsPerTeacher", "studentsPerSchool",
        ])
        for r in resp.rows:
            writer.writerow([
                r.id, r.name, r.parentId or "", r.parentName or "",
                r.schools, r.students, r.teachers, r.classes,
                r.geolocatedSchools, r.gpsCoverageRate,
                r.studentsPerTeacher, r.studentsPerSchool,
            ])
        filename = f"territories-{resp.level}.csv"
    elif type == "top-schools":
        resp = await service.top_schools(user, metric or "students", limit)
        writer.writerow([
            "id", "code", "name", "regionName",
            "students", "teachers", "classes",
            "presenceRateLast7Days", "gpsCoverageRate",
        ])
        for r in resp.rows:
            writer.writerow([
                r.id, r.code, r.name, r.regionName or "",
                r.students, r.teachers, r.classes,
                "" if r.presenceRateLast7Days is None else r.presenceRateLast7Days,
                "" if r.gpsCoverageRate is None else r.gpsCoverageRate,
            ])
        filename = f"top-schools-{metric or 'students'}.csv"
    elif type == "quality":
        q = await service.quality(user)
        writer.writerow(["metric", "value"])
        for k, v in q.model_dump().items():
            writer.writerow([k, v])
        filename = "quality.csv"
    else:
        raise HTTPException(status_code=400, detail="type invalide")

    # UTF-8 BOM so Excel-FR opens accents correctly
    body = "﻿" + buf.getvalue()
    return Response(
        content=body,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
