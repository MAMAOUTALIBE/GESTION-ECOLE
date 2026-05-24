"""Router cantines (Module 7) — menus + présence cantine.

Endpoints :
    GET    /menu/{date}?schoolId=X
    POST   /menu                   (créer / mettre à jour menu)
    POST   /attendance             (bulk presence)
    GET    /attendance/stats?mealServiceId=X

RBAC :
    * Écriture menu : SCHOOL_DIRECTOR (rédige) + admins
    * Présence : TEACHER + SCHOOL_DIRECTOR + admins
    * Lecture : tout rôle écolaire
"""
from __future__ import annotations

from datetime import date as date_t
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status

from app.modules.auth.models import User
from app.modules.schoollife.schemas import (
    BulkMealAttendanceRequest,
    CreateMealMenuRequest,
    MealAttendanceRead,
    MealAttendanceStatsResponse,
    MealMenuRead,
)
from app.modules.schoollife.service import MealServiceModule
from app.shared.deps import DbSession, get_current_user
from app.shared.enums import UserRole
from app.shared.permissions import require_roles

router = APIRouter(tags=["schoollife-meals"])

READ_ROLES = (
    UserRole.NATIONAL_ADMIN, UserRole.MINISTRY_ADMIN,
    UserRole.REGIONAL_ADMIN, UserRole.PREFECTURE_ADMIN,
    UserRole.SUB_PREFECTURE_ADMIN, UserRole.SCHOOL_DIRECTOR,
    UserRole.INSPECTOR, UserRole.TEACHER,
)
MENU_WRITE_ROLES = (
    UserRole.NATIONAL_ADMIN, UserRole.MINISTRY_ADMIN,
    UserRole.REGIONAL_ADMIN, UserRole.SCHOOL_DIRECTOR,
)
ATTENDANCE_WRITE_ROLES = (
    UserRole.NATIONAL_ADMIN, UserRole.MINISTRY_ADMIN,
    UserRole.REGIONAL_ADMIN, UserRole.SCHOOL_DIRECTOR,
    UserRole.TEACHER,
)


def _svc(session: DbSession) -> MealServiceModule:
    return MealServiceModule(session)


Svc = Annotated[MealServiceModule, Depends(_svc)]
CurrentUserDep = Annotated[User, Depends(get_current_user)]


@router.get(
    "/menu/{meal_date}",
    response_model=list[MealMenuRead],
    dependencies=[Depends(require_roles(*READ_ROLES))],
)
async def get_menu(
    meal_date: date_t, user: CurrentUserDep, service: Svc,
    schoolId: Annotated[str, Query()] = ...,
) -> list[MealMenuRead]:
    return await service.get_menu_by_date(user, schoolId, meal_date)


@router.post(
    "/menu",
    response_model=MealMenuRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_roles(*MENU_WRITE_ROLES))],
)
async def create_menu(
    dto: CreateMealMenuRequest, user: CurrentUserDep, service: Svc,
) -> MealMenuRead:
    return await service.create_menu(user, dto)


@router.post(
    "/attendance",
    response_model=list[MealAttendanceRead],
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_roles(*ATTENDANCE_WRITE_ROLES))],
)
async def bulk_attendance(
    dto: BulkMealAttendanceRequest, user: CurrentUserDep, service: Svc,
) -> list[MealAttendanceRead]:
    return await service.record_bulk_attendance(user, dto)


@router.get(
    "/attendance/stats",
    response_model=MealAttendanceStatsResponse,
    dependencies=[Depends(require_roles(*READ_ROLES))],
)
async def attendance_stats(
    user: CurrentUserDep, service: Svc,
    mealServiceId: Annotated[str, Query()] = ...,
) -> MealAttendanceStatsResponse:
    return await service.attendance_stats(user, mealServiceId)
