"""Service vie scolaire — Phase 13.

Cinq sous-modules indépendants (incidents / santé / transport / cantines /
emploi du temps), tous scope-aware via le rattachement à `School` (sauf
TimetableSlot qui passe par ClassRoom → School).
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.exceptions import NotFoundError
from app.modules.auth.models import User
from app.modules.schoollife.models import (
    BusRoute,
    HealthVisit,
    Incident,
    MealService,
    TimetableSlot,
)
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
from app.modules.schools.models import ClassRoom, School
from app.shared.enums import IncidentSeverity
from app.shared.permissions import (
    NATIONAL_SCOPE_ROLES,
    PREFECTURE_SCOPE_ROLES,
    REGIONAL_SCOPE_ROLES,
    SUB_PREFECTURE_SCOPE_ROLES,
)


class SchoolLifeService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ==================================================================
    # SCOPE — réutilisable pour tous les sous-modules
    # ==================================================================
    def _scope_school_ids(self, user: User) -> Any:
        stmt = select(School.id)
        if user.role in NATIONAL_SCOPE_ROLES:
            return stmt
        if user.role in REGIONAL_SCOPE_ROLES and user.regionId:
            return stmt.where(School.regionId == user.regionId)
        if user.role in PREFECTURE_SCOPE_ROLES and user.prefectureId:
            return stmt.where(School.prefectureId == user.prefectureId)
        if user.role in SUB_PREFECTURE_SCOPE_ROLES and user.subPrefectureId:
            return stmt.where(School.subPrefectureId == user.subPrefectureId)
        if user.schoolId:
            return stmt.where(School.id == user.schoolId)
        return stmt.where(School.id == "__none__")

    # ==================================================================
    # INCIDENTS (discipline)
    # ==================================================================
    async def list_incidents(
        self, user: User, school_id: str | None, severity: IncidentSeverity | None,
        limit: int = 500,
    ) -> list[IncidentRead]:
        stmt = (
            select(Incident)
            .options(
                selectinload(Incident.school),
                selectinload(Incident.student),
            )
            .where(Incident.schoolId.in_(self._scope_school_ids(user)))
            .order_by(Incident.occurredAt.desc())
            .limit(limit)
        )
        if school_id:
            stmt = stmt.where(Incident.schoolId == school_id)
        if severity:
            stmt = stmt.where(Incident.severity == severity)
        rows = (await self.session.execute(stmt)).scalars().unique().all()
        return [IncidentRead.model_validate(r) for r in rows]

    async def create_incident(
        self, user: User, dto: CreateIncidentRequest,
    ) -> IncidentRead:
        await self._assert_school_in_scope(user, dto.schoolId)
        i = Incident(
            schoolId=dto.schoolId,
            studentId=dto.studentId,
            type=dto.type,
            severity=dto.severity,
            description=dto.description,
            sanction=dto.sanction,
            occurredAt=dto.occurredAt,
            recordedById=user.id,
        )
        self.session.add(i)
        await self.session.flush()
        return await self._load_incident(i.id)

    async def _load_incident(self, incident_id: str) -> IncidentRead:
        i = (await self.session.execute(
            select(Incident)
            .where(Incident.id == incident_id)
            .options(selectinload(Incident.school), selectinload(Incident.student))
        )).scalar_one()
        return IncidentRead.model_validate(i)

    # ==================================================================
    # HEALTH VISITS
    # ==================================================================
    async def list_health_visits(
        self, user: User, school_id: str | None, limit: int = 500,
    ) -> list[HealthVisitRead]:
        stmt = (
            select(HealthVisit)
            .options(
                selectinload(HealthVisit.school),
                selectinload(HealthVisit.student),
            )
            .where(HealthVisit.schoolId.in_(self._scope_school_ids(user)))
            .order_by(HealthVisit.visitDate.desc())
            .limit(limit)
        )
        if school_id:
            stmt = stmt.where(HealthVisit.schoolId == school_id)
        rows = (await self.session.execute(stmt)).scalars().unique().all()
        return [HealthVisitRead.model_validate(r) for r in rows]

    async def create_health_visit(
        self, user: User, dto: CreateHealthVisitRequest,
    ) -> HealthVisitRead:
        await self._assert_school_in_scope(user, dto.schoolId)
        v = HealthVisit(
            schoolId=dto.schoolId,
            studentId=dto.studentId,
            type=dto.type,
            description=dto.description,
            visitDate=dto.visitDate,
            nurseName=dto.nurseName,
            status=dto.status,
        )
        self.session.add(v)
        await self.session.flush()
        return await self._load_health_visit(v.id)

    async def _load_health_visit(self, visit_id: str) -> HealthVisitRead:
        v = (await self.session.execute(
            select(HealthVisit)
            .where(HealthVisit.id == visit_id)
            .options(selectinload(HealthVisit.school), selectinload(HealthVisit.student))
        )).scalar_one()
        return HealthVisitRead.model_validate(v)

    # ==================================================================
    # BUS ROUTES
    # ==================================================================
    async def list_bus_routes(
        self, user: User, school_id: str | None, limit: int = 500,
    ) -> list[BusRouteRead]:
        stmt = (
            select(BusRoute)
            .options(selectinload(BusRoute.school))
            .where(BusRoute.schoolId.in_(self._scope_school_ids(user)))
            .order_by(BusRoute.name.asc())
            .limit(limit)
        )
        if school_id:
            stmt = stmt.where(BusRoute.schoolId == school_id)
        rows = (await self.session.execute(stmt)).scalars().unique().all()
        return [BusRouteRead.model_validate(r) for r in rows]

    async def create_bus_route(
        self, user: User, dto: CreateBusRouteRequest,
    ) -> BusRouteRead:
        await self._assert_school_in_scope(user, dto.schoolId)
        r = BusRoute(
            schoolId=dto.schoolId, name=dto.name, capacity=dto.capacity,
            departureTime=dto.departureTime, returnTime=dto.returnTime,
            driverName=dto.driverName, driverPhone=dto.driverPhone,
            plate=dto.plate, studentsAssigned=dto.studentsAssigned,
            status=dto.status,
        )
        self.session.add(r)
        await self.session.flush()
        return await self._load_bus_route(r.id)

    async def _load_bus_route(self, route_id: str) -> BusRouteRead:
        r = (await self.session.execute(
            select(BusRoute)
            .where(BusRoute.id == route_id)
            .options(selectinload(BusRoute.school))
        )).scalar_one()
        return BusRouteRead.model_validate(r)

    # ==================================================================
    # MEAL SERVICES (cantines)
    # ==================================================================
    async def list_meal_services(
        self, user: User, school_id: str | None, limit: int = 500,
    ) -> list[MealServiceRead]:
        stmt = (
            select(MealService)
            .options(selectinload(MealService.school))
            .where(MealService.schoolId.in_(self._scope_school_ids(user)))
            .order_by(MealService.serviceDate.desc())
            .limit(limit)
        )
        if school_id:
            stmt = stmt.where(MealService.schoolId == school_id)
        rows = (await self.session.execute(stmt)).scalars().unique().all()
        return [MealServiceRead.model_validate(r) for r in rows]

    async def create_meal_service(
        self, user: User, dto: CreateMealServiceRequest,
    ) -> MealServiceRead:
        await self._assert_school_in_scope(user, dto.schoolId)
        m = MealService(
            schoolId=dto.schoolId, type=dto.type,
            serviceDate=dto.serviceDate,
            mealsPlanned=dto.mealsPlanned, mealsServed=dto.mealsServed,
            costPerMealGNF=dto.costPerMealGNF, notes=dto.notes,
        )
        self.session.add(m)
        await self.session.flush()
        return await self._load_meal(m.id)

    async def _load_meal(self, meal_id: str) -> MealServiceRead:
        m = (await self.session.execute(
            select(MealService)
            .where(MealService.id == meal_id)
            .options(selectinload(MealService.school))
        )).scalar_one()
        return MealServiceRead.model_validate(m)

    # ==================================================================
    # TIMETABLE (emploi du temps)
    # ==================================================================
    async def list_timetable_slots(
        self, user: User, class_room_id: str | None, school_id: str | None,
        limit: int = 1000,
    ) -> list[TimetableSlotRead]:
        scoped_classes = (
            select(ClassRoom.id)
            .where(ClassRoom.schoolId.in_(self._scope_school_ids(user)))
        )
        stmt = (
            select(TimetableSlot)
            .options(
                selectinload(TimetableSlot.classRoom),
                selectinload(TimetableSlot.subject),
                selectinload(TimetableSlot.teacher),
            )
            .where(TimetableSlot.classRoomId.in_(scoped_classes))
            .order_by(TimetableSlot.dayOfWeek.asc(), TimetableSlot.startTime.asc())
            .limit(limit)
        )
        if class_room_id:
            stmt = stmt.where(TimetableSlot.classRoomId == class_room_id)
        if school_id:
            stmt = stmt.where(TimetableSlot.classRoomId.in_(
                select(ClassRoom.id).where(ClassRoom.schoolId == school_id)
            ))
        rows = (await self.session.execute(stmt)).scalars().unique().all()
        return [TimetableSlotRead.model_validate(r) for r in rows]

    async def create_timetable_slot(
        self, user: User, dto: CreateTimetableSlotRequest,
    ) -> TimetableSlotRead:
        klass = await self.session.get(ClassRoom, dto.classRoomId)
        if klass is None:
            raise NotFoundError(detail="Classe introuvable")
        await self._assert_school_in_scope(user, klass.schoolId)
        s = TimetableSlot(
            classRoomId=dto.classRoomId, dayOfWeek=dto.dayOfWeek,
            startTime=dto.startTime, endTime=dto.endTime,
            subjectId=dto.subjectId, teacherId=dto.teacherId, room=dto.room,
        )
        self.session.add(s)
        await self.session.flush()
        return await self._load_slot(s.id)

    async def _load_slot(self, slot_id: str) -> TimetableSlotRead:
        s = (await self.session.execute(
            select(TimetableSlot)
            .where(TimetableSlot.id == slot_id)
            .options(
                selectinload(TimetableSlot.classRoom),
                selectinload(TimetableSlot.subject),
                selectinload(TimetableSlot.teacher),
            )
        )).scalar_one()
        return TimetableSlotRead.model_validate(s)

    # ==================================================================
    # SHARED HELPERS
    # ==================================================================
    async def _assert_school_in_scope(self, user: User, school_id: str) -> None:
        if user.role in NATIONAL_SCOPE_ROLES:
            return
        scoped = self._scope_school_ids(user)
        ok = (await self.session.execute(
            select(func.count()).select_from(School)
            .where(School.id == school_id, School.id.in_(scoped))
        )).scalar_one()
        if not ok:
            raise NotFoundError(detail="École hors de votre périmètre")
