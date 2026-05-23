from typing import Annotated

from fastapi import APIRouter, Depends, Query, status

from app.modules.auth.models import User
from app.modules.schoollife.schemas import (
    BusRouteRead,
    CreateBusRouteRequest,
    CreateHealthVisitRequest,
    CreateIncidentRequest,
    CreateMealServiceRequest,
    CreateTimetableSlotRequest,
    HealthVisitRead,
    IncidentRead,
    MealServiceRead,
    TimetableSlotRead,
)
from app.modules.schoollife.service import SchoolLifeService
from app.shared.deps import DbSession, get_current_user
from app.shared.enums import IncidentSeverity, UserRole
from app.shared.permissions import require_roles

router = APIRouter(tags=["schoollife"])

WRITE_ROLES = (
    UserRole.NATIONAL_ADMIN,
    UserRole.MINISTRY_ADMIN,
    UserRole.REGIONAL_ADMIN,
    UserRole.PREFECTURE_ADMIN,
    UserRole.SUB_PREFECTURE_ADMIN,
    UserRole.SCHOOL_DIRECTOR,
    UserRole.INSPECTOR,
    UserRole.TEACHER,
    UserRole.CENSUS_AGENT,
)


def _service(session: DbSession) -> SchoolLifeService:
    return SchoolLifeService(session)


SLSvc = Annotated[SchoolLifeService, Depends(_service)]
CurrentUserDep = Annotated[User, Depends(get_current_user)]


# =============================================================
# INCIDENTS
# =============================================================
@router.get("/incidents", response_model=list[IncidentRead])
async def list_incidents(
    user: CurrentUserDep, service: SLSvc,
    schoolId: Annotated[str | None, Query()] = None,
    severity: Annotated[IncidentSeverity | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=2000)] = 500,
) -> list[IncidentRead]:
    return await service.list_incidents(user, schoolId, severity, limit)


@router.post(
    "/incidents",
    response_model=IncidentRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_roles(*WRITE_ROLES))],
)
async def create_incident(
    dto: CreateIncidentRequest, user: CurrentUserDep, service: SLSvc,
) -> IncidentRead:
    return await service.create_incident(user, dto)


# =============================================================
# HEALTH VISITS
# =============================================================
@router.get("/health-visits", response_model=list[HealthVisitRead])
async def list_health_visits(
    user: CurrentUserDep, service: SLSvc,
    schoolId: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=2000)] = 500,
) -> list[HealthVisitRead]:
    return await service.list_health_visits(user, schoolId, limit)


@router.post(
    "/health-visits",
    response_model=HealthVisitRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_roles(*WRITE_ROLES))],
)
async def create_health_visit(
    dto: CreateHealthVisitRequest, user: CurrentUserDep, service: SLSvc,
) -> HealthVisitRead:
    return await service.create_health_visit(user, dto)


# =============================================================
# BUS ROUTES
# =============================================================
@router.get("/bus-routes", response_model=list[BusRouteRead])
async def list_bus_routes(
    user: CurrentUserDep, service: SLSvc,
    schoolId: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=2000)] = 500,
) -> list[BusRouteRead]:
    return await service.list_bus_routes(user, schoolId, limit)


@router.post(
    "/bus-routes",
    response_model=BusRouteRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_roles(*WRITE_ROLES))],
)
async def create_bus_route(
    dto: CreateBusRouteRequest, user: CurrentUserDep, service: SLSvc,
) -> BusRouteRead:
    return await service.create_bus_route(user, dto)


# =============================================================
# MEALS (cantines)
# =============================================================
@router.get("/meals", response_model=list[MealServiceRead])
async def list_meals(
    user: CurrentUserDep, service: SLSvc,
    schoolId: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=2000)] = 500,
) -> list[MealServiceRead]:
    return await service.list_meal_services(user, schoolId, limit)


@router.post(
    "/meals",
    response_model=MealServiceRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_roles(*WRITE_ROLES))],
)
async def create_meal(
    dto: CreateMealServiceRequest, user: CurrentUserDep, service: SLSvc,
) -> MealServiceRead:
    return await service.create_meal_service(user, dto)


# =============================================================
# TIMETABLE (emploi du temps)
# =============================================================
@router.get("/timetable", response_model=list[TimetableSlotRead])
async def list_timetable(
    user: CurrentUserDep, service: SLSvc,
    classRoomId: Annotated[str | None, Query()] = None,
    schoolId: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=2000)] = 1000,
) -> list[TimetableSlotRead]:
    return await service.list_timetable_slots(user, classRoomId, schoolId, limit)


@router.post(
    "/timetable",
    response_model=TimetableSlotRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_roles(*WRITE_ROLES))],
)
async def create_slot(
    dto: CreateTimetableSlotRequest, user: CurrentUserDep, service: SLSvc,
) -> TimetableSlotRead:
    return await service.create_timetable_slot(user, dto)
