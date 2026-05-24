"""Module 2A — Router HTTP du module Projections (taux de transition).

Endpoints
---------
* ``POST   /api/projections/transitions/compute`` — recalcul (admin central).
* ``GET    /api/projections/transitions``         — lecture filtrée.
* ``GET    /api/projections/transitions/outliers``— rates flaggés outlier.

RBAC
----
* compute : NATIONAL_ADMIN / MINISTRY_ADMIN uniquement.
* list / outliers : tous les rôles authentifiés. Le service applique
  automatiquement le scope territorial — un REGIONAL_ADMIN ne voit que
  les rates NATIONAL + ceux de sa région.
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
    TransitionRateFilters,
    TransitionRateRead,
)
from app.modules.projections.service import TransitionRateService
from app.shared.deps import DbSession, get_current_user
from app.shared.enums import Gender, UserRole
from app.shared.permissions import require_roles

# Rôles HTTP autorisés à déclencher le recalcul (écriture).
COMPUTE_TRANSITIONS_HTTP_ROLES = (
    UserRole.NATIONAL_ADMIN,
    UserRole.MINISTRY_ADMIN,
)


def _service(session: DbSession) -> TransitionRateService:
    return TransitionRateService(session)


TransitionSvc = Annotated[TransitionRateService, Depends(_service)]
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


__all__ = ["COMPUTE_TRANSITIONS_HTTP_ROLES", "router"]
