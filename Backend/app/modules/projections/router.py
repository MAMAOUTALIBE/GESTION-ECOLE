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
from app.modules.projections.enums import TransitionScope
from app.modules.projections.schemas import (
    ComputeTransitionsRequest,
    ComputeTransitionsResponse,
    ProjectedEnrollmentRead,
    ProjectionFilters,
    ProjectionScenarioCreate,
    ProjectionScenarioRead,
    RunProjectionRequest,
    RunProjectionResponse,
    TransitionRateFilters,
    TransitionRateRead,
)
from app.modules.projections.service import (
    ProjectionService,
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


TransitionSvc = Annotated[TransitionRateService, Depends(_service)]
ProjectionSvc = Annotated[ProjectionService, Depends(_projection_service)]
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


__all__ = [
    "COMPUTE_TRANSITIONS_HTTP_ROLES",
    "PROJECTION_WRITE_HTTP_ROLES",
    "router",
]
