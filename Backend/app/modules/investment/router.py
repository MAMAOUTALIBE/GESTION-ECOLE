"""Module 3C — Router HTTP du score d'investissement.

Endpoints
---------
* ``POST /api/investment/compute-scores``        — recalcul global (NATIONAL/MINISTRY).
* ``GET  /api/investment/priorities``            — listing filtré (scope RBAC).
* ``GET  /api/investment/top-priorities``        — top N (par défaut 100).
* ``GET  /api/investment/schools/{schoolId}``    — détail breakdown.
"""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, status

from app.modules.auth.models import User
from app.modules.investment.enums import PriorityCategory
from app.modules.investment.schemas import (
    ComputeScoresRequest,
    ComputeScoresResponse,
    InvestmentScoreRead,
)
from app.modules.investment.service import InvestmentService
from app.shared.deps import DbSession, get_current_user
from app.shared.enums import UserRole
from app.shared.permissions import require_roles

# Rôles autorisés à déclencher un recalcul. NATIONAL_ADMIN /
# MINISTRY_ADMIN — opération coûteuse à portée nationale.
INVESTMENT_COMPUTE_HTTP_ROLES = (
    UserRole.NATIONAL_ADMIN,
    UserRole.MINISTRY_ADMIN,
)


def _service(session: DbSession) -> InvestmentService:
    return InvestmentService(session)


InvestmentSvc = Annotated[InvestmentService, Depends(_service)]
CurrentUserDep = Annotated[User, Depends(get_current_user)]

router = APIRouter(tags=["investment"])


@router.post(
    "/compute-scores",
    response_model=ComputeScoresResponse,
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(require_roles(*INVESTMENT_COMPUTE_HTTP_ROLES))],
    summary="Recalcule le score d'investissement de toutes les écoles",
)
async def compute_scores(
    payload: ComputeScoresRequest,
    user: CurrentUserDep,
    service: InvestmentSvc,
) -> ComputeScoresResponse:
    return await service.compute_priority_scores(
        payload.baseSchoolYearId, user,
    )


@router.get(
    "/priorities",
    response_model=list[InvestmentScoreRead],
    summary="Liste des scores (filtrable + scope RBAC + pagination)",
)
async def list_priorities(
    user: CurrentUserDep,
    service: InvestmentSvc,
    category: Annotated[PriorityCategory | None, Query()] = None,
    regionId: Annotated[str | None, Query(max_length=30)] = None,
    baseSchoolYearId: Annotated[str | None, Query(max_length=30)] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[InvestmentScoreRead]:
    return await service.list_priorities(
        user,
        category=category,
        region_id=regionId,
        base_school_year_id=baseSchoolYearId,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/top-priorities",
    response_model=list[InvestmentScoreRead],
    summary="Top N écoles par score (par défaut 100)",
)
async def top_priorities(
    user: CurrentUserDep,
    service: InvestmentSvc,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> list[InvestmentScoreRead]:
    return await service.top_priorities(user, limit=limit)


@router.get(
    "/schools/{school_id}",
    response_model=InvestmentScoreRead,
    summary="Détail breakdown du score d'une école",
)
async def get_school_priority(
    school_id: str,
    user: CurrentUserDep,
    service: InvestmentSvc,
) -> InvestmentScoreRead:
    return await service.get_school_priority(school_id, user)


__all__ = ["INVESTMENT_COMPUTE_HTTP_ROLES", "router"]
