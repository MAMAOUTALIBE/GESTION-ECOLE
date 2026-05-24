"""Router transport (Module 7) — routes / arrêts / abonnements.

Endpoints :
    POST   /routes
    GET    /routes
    POST   /stops
    GET    /stops?routeId=X
    POST   /subscriptions
    GET    /subscriptions
    GET    /routes/{routeId}/students   (élèves abonnés actifs)

RBAC : SCHOOL_DIRECTOR + REGIONAL_ADMIN + admins ; lecture ouverte aux
inspecteurs.
"""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, status

from app.modules.auth.models import User
from app.modules.schoollife.schemas import (
    BusRouteRead,
    BusStopRead,
    BusSubscriptionRead,
    CreateBusRouteRequest,
    CreateBusStopRequest,
    CreateBusSubscriptionRequest,
    RouteStudentsResponse,
)
from app.modules.schoollife.service import TransportService
from app.shared.deps import DbSession, get_current_user
from app.shared.enums import UserRole
from app.shared.permissions import require_roles

router = APIRouter(tags=["schoollife-transport"])

READ_ROLES = (
    UserRole.NATIONAL_ADMIN, UserRole.MINISTRY_ADMIN,
    UserRole.REGIONAL_ADMIN, UserRole.PREFECTURE_ADMIN,
    UserRole.SUB_PREFECTURE_ADMIN, UserRole.SCHOOL_DIRECTOR,
    UserRole.INSPECTOR,
)
WRITE_ROLES = (
    UserRole.NATIONAL_ADMIN, UserRole.MINISTRY_ADMIN,
    UserRole.REGIONAL_ADMIN, UserRole.SCHOOL_DIRECTOR,
)


def _svc(session: DbSession) -> TransportService:
    return TransportService(session)


Svc = Annotated[TransportService, Depends(_svc)]
CurrentUserDep = Annotated[User, Depends(get_current_user)]


# ----- Routes -----
@router.post(
    "/routes", response_model=BusRouteRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_roles(*WRITE_ROLES))],
)
async def create_route(
    dto: CreateBusRouteRequest, user: CurrentUserDep, service: Svc,
) -> BusRouteRead:
    return await service.create_route(user, dto)


@router.get(
    "/routes", response_model=list[BusRouteRead],
    dependencies=[Depends(require_roles(*READ_ROLES))],
)
async def list_routes(
    user: CurrentUserDep, service: Svc,
    schoolId: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=2000)] = 500,
) -> list[BusRouteRead]:
    return await service.list_routes(user, schoolId, limit)


# ----- Stops -----
@router.post(
    "/stops", response_model=BusStopRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_roles(*WRITE_ROLES))],
)
async def create_stop(
    dto: CreateBusStopRequest, user: CurrentUserDep, service: Svc,
) -> BusStopRead:
    return await service.create_stop(user, dto)


@router.get(
    "/stops", response_model=list[BusStopRead],
    dependencies=[Depends(require_roles(*READ_ROLES))],
)
async def list_stops(
    user: CurrentUserDep, service: Svc,
    routeId: Annotated[str, Query()] = ...,
) -> list[BusStopRead]:
    return await service.list_stops(user, routeId)


# ----- Subscriptions -----
@router.post(
    "/subscriptions", response_model=BusSubscriptionRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_roles(*WRITE_ROLES))],
)
async def subscribe(
    dto: CreateBusSubscriptionRequest, user: CurrentUserDep, service: Svc,
) -> BusSubscriptionRead:
    return await service.subscribe(user, dto)


@router.get(
    "/subscriptions", response_model=list[BusSubscriptionRead],
    dependencies=[Depends(require_roles(*READ_ROLES))],
)
async def list_subscriptions(
    user: CurrentUserDep, service: Svc,
    routeId: Annotated[str | None, Query()] = None,
    studentId: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=2000)] = 500,
) -> list[BusSubscriptionRead]:
    return await service.list_subscriptions(user, routeId, studentId, limit)


@router.get(
    "/routes/{route_id}/students",
    response_model=RouteStudentsResponse,
    dependencies=[Depends(require_roles(*READ_ROLES))],
)
async def students_by_route(
    route_id: str, user: CurrentUserDep, service: Svc,
) -> RouteStudentsResponse:
    return await service.students_by_route(user, route_id)
