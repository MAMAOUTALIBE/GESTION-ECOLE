"""Module 2A + 2B — Router HTTP du module Projections.

Endpoints
---------
Module 2A — Transitions :
* ``POST   /api/projections/transitions/compute`` — recalcul (admin central).
* ``GET    /api/projections/transitions``         — lecture filtrée.
* ``GET    /api/projections/transitions/outliers``— rates flaggés outlier.

Module 2B — Projections :
* ``POST   /api/projections/run``                 — lance une projection.
* ``GET    /api/projections``                     — lecture filtrée.
* ``POST   /api/projections/scenarios``           — crée un scénario.
* ``GET    /api/projections/scenarios``           — liste les scénarios.

RBAC
----
* compute / run / création scénario : NATIONAL_ADMIN / MINISTRY_ADMIN.
* list / outliers / get_projections / list_scenarios : authentifiés
  (scope territorial appliqué).
"""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, status

from app.modules.auth.models import User
from app.modules.enrollment.enums import EnrollmentClassLevel
from app.modules.projections.enums import (
    CapacityScope,
    CapacitySeverity,
    RecommendationStatus,
    StaffingSeverity,
    TransitionScope,
)
from app.modules.projections.schemas import (
    CapacityDemandFilters,
    CapacityDemandRequest,
    CapacityDemandResponse,
    CapacityDemandRow,
    ComputeStaffingRequest,
    ComputeStaffingResponse,
    ComputeTransitionsRequest,
    ComputeTransitionsResponse,
    ProjectedEnrollmentRead,
    ProjectionFilters,
    ProjectionScenarioCreate,
    ProjectionScenarioRead,
    ReviewRecommendationRequest,
    RunProjectionRequest,
    RunProjectionResponse,
    StaffingFilters,
    TeacherStaffingSnapshotRead,
    TeacherTransferRecommendationRead,
    TransitionRateFilters,
    TransitionRateRead,
)
from app.modules.projections.service import (
    CapacityDemandService,
    ProjectionService,
    TeacherStaffingService,
    TransitionRateService,
)
from app.shared.deps import DbSession, get_current_user
from app.shared.enums import Gender, UserRole
from app.shared.permissions import require_roles

# Rôles HTTP autorisés à déclencher le recalcul / projection (écriture).
COMPUTE_TRANSITIONS_HTTP_ROLES = (
    UserRole.NATIONAL_ADMIN,
    UserRole.MINISTRY_ADMIN,
)
PROJECTION_WRITE_HTTP_ROLES = (
    UserRole.NATIONAL_ADMIN,
    UserRole.MINISTRY_ADMIN,
)


def _service(session: DbSession) -> TransitionRateService:
    return TransitionRateService(session)


def _projection_service(session: DbSession) -> ProjectionService:
    return ProjectionService(session)


def _capacity_service(session: DbSession) -> CapacityDemandService:
    return CapacityDemandService(session)


def _staffing_service(session: DbSession) -> TeacherStaffingService:
    return TeacherStaffingService(session)


TransitionSvc = Annotated[TransitionRateService, Depends(_service)]
ProjectionSvc = Annotated[ProjectionService, Depends(_projection_service)]
CapacitySvc = Annotated[CapacityDemandService, Depends(_capacity_service)]
StaffingSvc = Annotated[TeacherStaffingService, Depends(_staffing_service)]
CurrentUserDep = Annotated[User, Depends(get_current_user)]

router = APIRouter(tags=["projections"])


@router.post(
    "/transitions/compute",
    response_model=ComputeTransitionsResponse,
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(require_roles(*COMPUTE_TRANSITIONS_HTTP_ROLES))],
    summary="Recalcule + persiste les taux de transition par cohortes",
)
async def compute_transitions(
    payload: ComputeTransitionsRequest,
    user: CurrentUserDep,
    service: TransitionSvc,
) -> ComputeTransitionsResponse:
    return await service.compute_transitions(
        payload.schoolYearFromIds, user,
    )


@router.get(
    "/transitions",
    response_model=list[TransitionRateRead],
    summary="Liste les taux de transition (filtres + scope territorial)",
)
async def list_transitions(
    user: CurrentUserDep,
    service: TransitionSvc,
    scope: TransitionScope | None = None,
    entityId: Annotated[str | None, Query(max_length=30)] = None,
    schoolYearFromId: Annotated[str | None, Query(max_length=30)] = None,
    classLevelFrom: EnrollmentClassLevel | None = None,
    gender: Gender | None = None,
) -> list[TransitionRateRead]:
    filters = TransitionRateFilters(
        scope=scope,
        entityId=entityId,
        schoolYearFromId=schoolYearFromId,
        classLevelFrom=classLevelFrom,
        gender=gender,
    )
    return await service.list_rates(filters, user)


@router.get(
    "/transitions/outliers",
    response_model=list[TransitionRateRead],
    summary="Liste les taux de transition outliers (rate > 2 ou < 0)",
)
async def list_outliers(
    user: CurrentUserDep,
    service: TransitionSvc,
    schoolYearFromId: Annotated[str | None, Query(max_length=30)] = None,
) -> list[TransitionRateRead]:
    return await service.get_outliers(
        user, school_year_from_id=schoolYearFromId,
    )


# ===========================================================================
# Module 2B — Projections horizon 5 ans
# ===========================================================================
@router.post(
    "/run",
    response_model=RunProjectionResponse,
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(require_roles(*PROJECTION_WRITE_HTTP_ROLES))],
    summary="Lance une projection horizon multi-années (max 10 ans)",
)
async def run_projection(
    payload: RunProjectionRequest,
    user: CurrentUserDep,
    service: ProjectionSvc,
) -> RunProjectionResponse:
    return await service.run_projection(payload, user)


@router.get(
    "",
    response_model=list[ProjectedEnrollmentRead],
    summary="Liste les projections (filtres + scope territorial + pagination)",
)
async def list_projections(
    user: CurrentUserDep,
    service: ProjectionSvc,
    baseSchoolYearId: Annotated[str | None, Query(max_length=30)] = None,
    projectedYear: int | None = None,
    scope: TransitionScope | None = None,
    entityId: Annotated[str | None, Query(max_length=30)] = None,
    classLevel: EnrollmentClassLevel | None = None,
    gender: Gender | None = None,
    scenarioId: Annotated[str | None, Query(max_length=30)] = None,
    limit: Annotated[int, Query(ge=1, le=1000)] = 200,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[ProjectedEnrollmentRead]:
    filters = ProjectionFilters(
        baseSchoolYearId=baseSchoolYearId,
        projectedYear=projectedYear,
        scope=scope,
        entityId=entityId,
        classLevel=classLevel,
        gender=gender,
        scenarioId=scenarioId,
        limit=limit,
        offset=offset,
    )
    return await service.get_projections(filters, user)


@router.post(
    "/scenarios",
    response_model=ProjectionScenarioRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_roles(*PROJECTION_WRITE_HTTP_ROLES))],
    summary="Crée un scénario de projection (paramétrage admin central)",
)
async def create_scenario(
    payload: ProjectionScenarioCreate,
    user: CurrentUserDep,
    service: ProjectionSvc,
) -> ProjectionScenarioRead:
    return await service.create_scenario(payload, user)


@router.get(
    "/scenarios",
    response_model=list[ProjectionScenarioRead],
    summary="Liste les scénarios de projection",
)
async def list_scenarios(
    user: CurrentUserDep,
    service: ProjectionSvc,
) -> list[ProjectionScenarioRead]:
    # `user` est résolu pour garantir l'authentification ; les scénarios
    # sont visibles à tous les rôles (information de paramétrage public).
    _ = user
    return await service.list_scenarios()


# ===========================================================================
# Module 2C — Capacité vs demande projetée
# ===========================================================================
CAPACITY_WRITE_HTTP_ROLES = (
    UserRole.NATIONAL_ADMIN,
    UserRole.MINISTRY_ADMIN,
)


@router.post(
    "/capacity-demand/compute",
    response_model=CapacityDemandResponse,
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(require_roles(*CAPACITY_WRITE_HTTP_ROLES))],
    summary="Calcule + persiste les snapshots capacité vs demande projetée",
)
async def compute_capacity_demand(
    payload: CapacityDemandRequest,
    user: CurrentUserDep,
    service: CapacitySvc,
) -> CapacityDemandResponse:
    return await service.compute_capacity_demand(payload, user)


@router.get(
    "/capacity-demand",
    response_model=list[CapacityDemandRow],
    summary="Liste les snapshots capacité (filtres + scope territorial)",
)
async def list_capacity_demand(
    user: CurrentUserDep,
    service: CapacitySvc,
    baseSchoolYearId: Annotated[str | None, Query(max_length=30)] = None,
    projectedYear: int | None = None,
    scope: CapacityScope | None = None,
    entityId: Annotated[str | None, Query(max_length=30)] = None,
    severity: CapacitySeverity | None = None,
    scenarioId: Annotated[str | None, Query(max_length=30)] = None,
    limit: Annotated[int, Query(ge=1, le=1000)] = 200,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[CapacityDemandRow]:
    filters = CapacityDemandFilters(
        baseSchoolYearId=baseSchoolYearId,
        projectedYear=projectedYear,
        scope=scope,
        entityId=entityId,
        severity=severity,
        scenarioId=scenarioId,
        limit=limit,
        offset=offset,
    )
    return await service.list_capacity_demand(filters, user)


@router.get(
    "/capacity-demand/critical-schools",
    response_model=list[CapacityDemandRow],
    summary="Top écoles CRITICAL (input Module 3C investissement)",
)
async def list_critical_schools(
    user: CurrentUserDep,
    service: CapacitySvc,
    baseSchoolYearId: Annotated[str | None, Query(max_length=30)] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
) -> list[CapacityDemandRow]:
    return await service.list_critical_schools_for_investment(
        user, limit=limit, base_school_year_id=baseSchoolYearId,
    )


# ===========================================================================
# Module 2D — Recommandation transferts enseignants
# ===========================================================================
STAFFING_WRITE_HTTP_ROLES = (
    UserRole.NATIONAL_ADMIN,
    UserRole.MINISTRY_ADMIN,
)
RECOMMENDATION_REVIEW_HTTP_ROLES = (
    UserRole.NATIONAL_ADMIN,
    UserRole.MINISTRY_ADMIN,
    UserRole.REGIONAL_ADMIN,
)


@router.post(
    "/staffing/compute",
    response_model=ComputeStaffingResponse,
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(require_roles(*STAFFING_WRITE_HTTP_ROLES))],
    summary="Calcule + persiste les snapshots staffing enseignants",
)
async def compute_staffing(
    payload: ComputeStaffingRequest,
    user: CurrentUserDep,
    service: StaffingSvc,
) -> ComputeStaffingResponse:
    return await service.compute_staffing_snapshots(
        payload.schoolYearId, user,
    )


@router.post(
    "/recommendations/generate",
    response_model=ComputeStaffingResponse,
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(require_roles(*STAFFING_WRITE_HTTP_ROLES))],
    summary="Génère les recommandations de transferts enseignants",
)
async def generate_recommendations(
    payload: ComputeStaffingRequest,
    user: CurrentUserDep,
    service: StaffingSvc,
) -> ComputeStaffingResponse:
    return await service.generate_recommendations(
        payload.schoolYearId, user,
    )


@router.get(
    "/staffing",
    response_model=list[TeacherStaffingSnapshotRead],
    summary="Liste les snapshots staffing (filtres + scope territorial)",
)
async def list_staffing(
    user: CurrentUserDep,
    service: StaffingSvc,
    schoolYearId: Annotated[str | None, Query(max_length=30)] = None,
    schoolId: Annotated[str | None, Query(max_length=30)] = None,
    severity: StaffingSeverity | None = None,
    limit: Annotated[int, Query(ge=1, le=1000)] = 200,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[TeacherStaffingSnapshotRead]:
    filters = StaffingFilters(
        schoolYearId=schoolYearId,
        schoolId=schoolId,
        severity=severity,
        limit=limit,
        offset=offset,
    )
    return await service.list_staffing(filters, user)


@router.get(
    "/recommendations",
    response_model=list[TeacherTransferRecommendationRead],
    summary="Liste les recommandations transferts (filtres + scope)",
)
async def list_recommendations(
    user: CurrentUserDep,
    service: StaffingSvc,
    schoolYearId: Annotated[str | None, Query(max_length=30)] = None,
    regionId: Annotated[str | None, Query(max_length=30)] = None,
    prefectureId: Annotated[str | None, Query(max_length=30)] = None,
    status_: Annotated[
        RecommendationStatus | None, Query(alias="status"),
    ] = None,
    limit: Annotated[int, Query(ge=1, le=1000)] = 200,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[TeacherTransferRecommendationRead]:
    filters = StaffingFilters(
        schoolYearId=schoolYearId,
        regionId=regionId,
        prefectureId=prefectureId,
        status=status_,
        limit=limit,
        offset=offset,
    )
    return await service.list_recommendations(filters, user)


@router.patch(
    "/recommendations/{recommendation_id}/review",
    response_model=TeacherTransferRecommendationRead,
    dependencies=[
        Depends(require_roles(*RECOMMENDATION_REVIEW_HTTP_ROLES)),
    ],
    summary="Revue d'une recommandation transferts (REGIONAL_ADMIN+)",
)
async def review_recommendation(
    recommendation_id: str,
    payload: ReviewRecommendationRequest,
    user: CurrentUserDep,
    service: StaffingSvc,
) -> TeacherTransferRecommendationRead:
    return await service.review_recommendation(
        recommendation_id, payload, user,
    )


__all__ = [
    "CAPACITY_WRITE_HTTP_ROLES",
    "COMPUTE_TRANSITIONS_HTTP_ROLES",
    "PROJECTION_WRITE_HTTP_ROLES",
    "RECOMMENDATION_REVIEW_HTTP_ROLES",
    "STAFFING_WRITE_HTTP_ROLES",
    "router",
]
