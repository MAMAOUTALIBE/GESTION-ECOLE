"""Service vie scolaire — Phase 13 + Module 7.

Phase 13 (héritage) regroupait 5 sous-modules sous une seule classe
``SchoolLifeService``. Module 7 conserve cette classe (rétro-compatibilité)
et la complète, puis expose 4 services métier dédiés :

    DisciplineService — Incident (CRUD, by-student, stats)
    HealthService     — HealthVisit + Vaccination + StudentAllergy
    MealService       — MealMenu + MealAttendance (bulk + stats)
    TransportService  — BusRoute + BusStop + StudentBusSubscription

Tous restent scope-aware via le rattachement à ``School``.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date as date_t
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.exceptions import NotFoundError, ValidationFailedError
from app.modules.auth.models import User
from app.modules.census.models import Student
from app.modules.schoollife.enums import (
    BusSubscriptionStatus,
    IncidentStatus,
    MealAttendanceStatus,
)
from app.modules.schoollife.models import (
    BusRoute,
    BusStop,
    HealthVisit,
    Incident,
    MealAttendance,
    MealMenu,
    MealService,
    StudentAllergy,
    StudentBusSubscription,
    TimetableSlot,
    Vaccination,
)
from app.modules.schoollife.schemas import (
    AllergyRead,
    BulkMealAttendanceRequest,
    BusRouteRead,
    BusStopRead,
    BusSubscriptionRead,
    CreateAllergyRequest,
    CreateBusRouteRequest,
    CreateBusStopRequest,
    CreateBusSubscriptionRequest,
    CreateHealthVisitRequest,
    CreateIncidentRequest,
    CreateMealMenuRequest,
    CreateMealServiceRequest,
    CreateTimetableSlotRequest,
    CreateVaccinationRequest,
    HealthVisitRead,
    IncidentRead,
    IncidentStatsResponse,
    MealAttendanceRead,
    MealAttendanceStatsResponse,
    MealMenuRead,
    MealServiceRead,
    RouteStudentsResponse,
    TimetableSlotRead,
    UpdateIncidentRequest,
    VaccinationRead,
    _StudentBrief,
)
from app.modules.schools.models import ClassRoom, School
from app.shared.enums import IncidentSeverity, MealServiceType
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


# ===================================================================
# MODULE 7 — Helpers communs (factorisés pour les 4 services dédiés)
# ===================================================================
class _ScopedService:
    """Base pour les 4 services métier — réutilise le scope ``School``."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

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

    async def _student_school_id(self, student_id: str) -> str:
        """Récupère ``schoolId`` d'un Student ; 404 sinon."""
        sid = (await self.session.execute(
            select(Student.schoolId).where(Student.id == student_id)
        )).scalar_one_or_none()
        if sid is None:
            raise NotFoundError(detail="Élève introuvable")
        return sid


# ===================================================================
# DiscplineService — Incident CRUD + by-student + stats
# ===================================================================
class DiscplineService(_ScopedService):
    """Service discipline (typo ``Disciple`` voulue par spec — alias plus bas)."""

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
            status=dto.status,
            occurredAt=dto.occurredAt,
            recordedById=user.id,
        )
        self.session.add(i)
        await self.session.flush()
        loaded = await self._load_incident(i.id)
        # Module 13 — publish realtime event (best-effort, ne casse pas le flux).
        try:
            from app.modules.realtime.service import RealtimeService
            from app.modules.schools.models import School as _School

            region_id = (await self.session.execute(
                select(_School.regionId).where(_School.id == dto.schoolId)
            )).scalar_one_or_none()
            await RealtimeService.publish_incident(
                school_id=dto.schoolId,
                region_id=region_id,
                severity=dto.severity.value if hasattr(dto.severity, "value") else str(dto.severity),
                incident_id=i.id,
            )
        except Exception:  # pragma: no cover — best-effort
            pass
        return loaded

    async def list_incidents(
        self, user: User,
        school_id: str | None = None,
        severity: IncidentSeverity | None = None,
        status: IncidentStatus | None = None,
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
        if status:
            stmt = stmt.where(Incident.status == status)
        rows = (await self.session.execute(stmt)).scalars().unique().all()
        return [IncidentRead.model_validate(r) for r in rows]

    async def list_by_student(
        self, user: User, student_id: str, limit: int = 500,
    ) -> list[IncidentRead]:
        school_id = await self._student_school_id(student_id)
        await self._assert_school_in_scope(user, school_id)
        stmt = (
            select(Incident)
            .options(
                selectinload(Incident.school),
                selectinload(Incident.student),
            )
            .where(Incident.studentId == student_id)
            .order_by(Incident.occurredAt.desc())
            .limit(limit)
        )
        rows = (await self.session.execute(stmt)).scalars().unique().all()
        return [IncidentRead.model_validate(r) for r in rows]

    async def update_incident(
        self, user: User, incident_id: str, dto: UpdateIncidentRequest,
    ) -> IncidentRead:
        i = await self.session.get(Incident, incident_id)
        if i is None:
            raise NotFoundError(detail="Incident introuvable")
        await self._assert_school_in_scope(user, i.schoolId)
        if dto.severity is not None:
            i.severity = dto.severity
        if dto.description is not None:
            i.description = dto.description
        if dto.sanction is not None:
            i.sanction = dto.sanction
        if dto.status is not None:
            i.status = dto.status
        await self.session.flush()
        return await self._load_incident(i.id)

    async def stats(
        self, user: User, school_id: str | None = None,
    ) -> IncidentStatsResponse:
        stmt = (
            select(Incident.severity, Incident.sanction, Incident.status)
            .where(Incident.schoolId.in_(self._scope_school_ids(user)))
        )
        if school_id:
            stmt = stmt.where(Incident.schoolId == school_id)
        rows = (await self.session.execute(stmt)).all()

        by_sev: dict[str, int] = defaultdict(int)
        by_san: dict[str, int] = defaultdict(int)
        by_st: dict[str, int] = defaultdict(int)
        for sev, san, st in rows:
            by_sev[sev.value if hasattr(sev, "value") else str(sev)] += 1
            by_san[san.value if hasattr(san, "value") else str(san)] += 1
            by_st[st.value if hasattr(st, "value") else str(st)] += 1
        return IncidentStatsResponse(
            total=len(rows),
            bySeverity=dict(by_sev),
            bySanction=dict(by_san),
            byStatus=dict(by_st),
        )

    async def _load_incident(self, incident_id: str) -> IncidentRead:
        i = (await self.session.execute(
            select(Incident)
            .where(Incident.id == incident_id)
            .options(
                selectinload(Incident.school),
                selectinload(Incident.student),
            )
        )).scalar_one()
        return IncidentRead.model_validate(i)


# Alias gardé pour cohérence — la spec demande ``DiscplineService`` mais
# on accepte aussi ``DisciplineService`` (orthographe correcte FR).
DisciplineService = DiscplineService


# ===================================================================
# HealthService — HealthVisit, Vaccination, StudentAllergy
# ===================================================================
class HealthService(_ScopedService):

    # ---- HealthVisit -------------------------------------------------
    async def create_visit(
        self, user: User, dto: CreateHealthVisitRequest,
    ) -> HealthVisitRead:
        await self._assert_school_in_scope(user, dto.schoolId)
        v = HealthVisit(
            schoolId=dto.schoolId, studentId=dto.studentId,
            type=dto.type, description=dto.description,
            visitDate=dto.visitDate, nurseName=dto.nurseName,
            status=dto.status,
        )
        self.session.add(v)
        await self.session.flush()
        return await self._load_visit(v.id)

    async def list_visits(
        self, user: User, school_id: str | None = None, limit: int = 500,
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

    async def _load_visit(self, visit_id: str) -> HealthVisitRead:
        v = (await self.session.execute(
            select(HealthVisit)
            .where(HealthVisit.id == visit_id)
            .options(
                selectinload(HealthVisit.school),
                selectinload(HealthVisit.student),
            )
        )).scalar_one()
        return HealthVisitRead.model_validate(v)

    # ---- Vaccination -------------------------------------------------
    async def create_vaccination(
        self, user: User, dto: CreateVaccinationRequest,
    ) -> VaccinationRead:
        school_id = await self._student_school_id(dto.studentId)
        await self._assert_school_in_scope(user, school_id)
        vac = Vaccination(
            studentId=dto.studentId,
            vaccine=dto.vaccine,
            dateAdministered=dto.dateAdministered,
            batchNumber=dto.batchNumber,
            administeredBy=dto.administeredBy,
            status=dto.status,
            notes=dto.notes,
            recordedById=user.id,
        )
        self.session.add(vac)
        await self.session.flush()
        return await self._load_vaccination(vac.id)

    async def list_vaccinations(
        self, user: User,
        student_id: str | None = None,
        vaccine: str | None = None,
        limit: int = 500,
    ) -> list[VaccinationRead]:
        # Limite au scope via Student.schoolId.
        stmt = (
            select(Vaccination)
            .join(Student, Student.id == Vaccination.studentId)
            .options(selectinload(Vaccination.student))
            .where(Student.schoolId.in_(self._scope_school_ids(user)))
            .order_by(Vaccination.dateAdministered.desc())
            .limit(limit)
        )
        if student_id:
            stmt = stmt.where(Vaccination.studentId == student_id)
        if vaccine:
            stmt = stmt.where(Vaccination.vaccine.ilike(f"%{vaccine}%"))
        rows = (await self.session.execute(stmt)).scalars().unique().all()
        return [VaccinationRead.model_validate(r) for r in rows]

    async def _load_vaccination(self, vac_id: str) -> VaccinationRead:
        v = (await self.session.execute(
            select(Vaccination)
            .where(Vaccination.id == vac_id)
            .options(selectinload(Vaccination.student))
        )).scalar_one()
        return VaccinationRead.model_validate(v)

    # ---- StudentAllergy ---------------------------------------------
    async def create_allergy(
        self, user: User, dto: CreateAllergyRequest,
    ) -> AllergyRead:
        school_id = await self._student_school_id(dto.studentId)
        await self._assert_school_in_scope(user, school_id)
        a = StudentAllergy(
            studentId=dto.studentId,
            allergen=dto.allergen,
            category=dto.category,
            severity=dto.severity,
            notes=dto.notes,
            recordedById=user.id,
        )
        self.session.add(a)
        await self.session.flush()
        return await self._load_allergy(a.id)

    async def list_allergies_by_student(
        self, user: User, student_id: str,
    ) -> list[AllergyRead]:
        school_id = await self._student_school_id(student_id)
        await self._assert_school_in_scope(user, school_id)
        stmt = (
            select(StudentAllergy)
            .options(selectinload(StudentAllergy.student))
            .where(StudentAllergy.studentId == student_id)
            .order_by(StudentAllergy.severity.desc())
        )
        rows = (await self.session.execute(stmt)).scalars().unique().all()
        return [AllergyRead.model_validate(r) for r in rows]

    async def _load_allergy(self, aid: str) -> AllergyRead:
        a = (await self.session.execute(
            select(StudentAllergy)
            .where(StudentAllergy.id == aid)
            .options(selectinload(StudentAllergy.student))
        )).scalar_one()
        return AllergyRead.model_validate(a)


# ===================================================================
# MealServiceModule — MealMenu + MealAttendance (bulk + stats)
# ===================================================================
class MealServiceModule(_ScopedService):
    """Service cantine — nommé ``MealServiceModule`` pour éviter la collision
    avec la classe modèle ``MealService``.  Alias ``MealsService`` exposé."""

    async def get_or_create_meal_service(
        self, user: User, school_id: str, meal_date: date_t,
        meal_type: MealServiceType,
    ) -> MealService:
        await self._assert_school_in_scope(user, school_id)
        existing = (await self.session.execute(
            select(MealService).where(
                MealService.schoolId == school_id,
                MealService.serviceDate == meal_date,
                MealService.type == meal_type,
            )
        )).scalar_one_or_none()
        if existing is not None:
            return existing
        ms = MealService(
            schoolId=school_id, type=meal_type, serviceDate=meal_date,
            mealsPlanned=0, mealsServed=0, costPerMealGNF=0.0,
        )
        self.session.add(ms)
        await self.session.flush()
        return ms

    async def create_menu(
        self, user: User, dto: CreateMealMenuRequest,
    ) -> MealMenuRead:
        if dto.mealServiceId:
            ms = await self.session.get(MealService, dto.mealServiceId)
            if ms is None:
                raise NotFoundError(detail="Service cantine introuvable")
            await self._assert_school_in_scope(user, ms.schoolId)
        else:
            if not dto.schoolId or not dto.mealDate:
                raise ValidationFailedError(
                    detail="schoolId + mealDate requis si mealServiceId absent",
                )
            ms = await self.get_or_create_meal_service(
                user, dto.schoolId, dto.mealDate, dto.mealType,
            )

        # un seul menu par service (uniqueness).
        existing = (await self.session.execute(
            select(MealMenu).where(MealMenu.mealServiceId == ms.id)
        )).scalar_one_or_none()
        if existing:
            existing.items = list(dto.items)
            existing.allergens = list(dto.allergens) if dto.allergens else []
            existing.estimatedCostGNF = dto.estimatedCostGNF
            await self.session.flush()
            return MealMenuRead.model_validate(existing)

        menu = MealMenu(
            mealServiceId=ms.id,
            items=list(dto.items),
            allergens=list(dto.allergens) if dto.allergens else [],
            estimatedCostGNF=dto.estimatedCostGNF,
        )
        self.session.add(menu)
        await self.session.flush()
        return MealMenuRead.model_validate(menu)

    async def get_menu_by_date(
        self, user: User, school_id: str, meal_date: date_t,
    ) -> list[MealMenuRead]:
        await self._assert_school_in_scope(user, school_id)
        stmt = (
            select(MealMenu)
            .join(MealService, MealService.id == MealMenu.mealServiceId)
            .where(
                MealService.schoolId == school_id,
                MealService.serviceDate == meal_date,
            )
        )
        rows = (await self.session.execute(stmt)).scalars().all()
        return [MealMenuRead.model_validate(r) for r in rows]

    async def record_bulk_attendance(
        self, user: User, dto: BulkMealAttendanceRequest,
    ) -> list[MealAttendanceRead]:
        ms = await self.session.get(MealService, dto.mealServiceId)
        if ms is None:
            raise NotFoundError(detail="Service cantine introuvable")
        await self._assert_school_in_scope(user, ms.schoolId)

        # Insert / upsert chaque entrée. Idempotent : on supprime puis on
        # ré-insère pour simplifier (le volume reste raisonnable: 1 classe).
        student_ids = [e.studentId for e in dto.entries]
        # Drop existantes pour ces studentIds sur ce service (re-saisie OK)
        existing = (await self.session.execute(
            select(MealAttendance).where(
                MealAttendance.mealServiceId == dto.mealServiceId,
                MealAttendance.studentId.in_(student_ids),
            )
        )).scalars().all()
        for row in existing:
            await self.session.delete(row)
        await self.session.flush()

        created: list[MealAttendance] = []
        for entry in dto.entries:
            ma = MealAttendance(
                mealServiceId=dto.mealServiceId,
                studentId=entry.studentId,
                status=entry.status,
                recordedById=user.id,
            )
            self.session.add(ma)
            created.append(ma)
        await self.session.flush()

        # MAJ compteurs MealService
        present_count = sum(
            1 for e in dto.entries if e.status == MealAttendanceStatus.PRESENT
        )
        if ms.mealsServed != present_count:
            ms.mealsServed = present_count
        await self.session.flush()
        return [MealAttendanceRead.model_validate(c) for c in created]

    async def attendance_stats(
        self, user: User, meal_service_id: str,
    ) -> MealAttendanceStatsResponse:
        ms = await self.session.get(MealService, meal_service_id)
        if ms is None:
            raise NotFoundError(detail="Service cantine introuvable")
        await self._assert_school_in_scope(user, ms.schoolId)
        rows = (await self.session.execute(
            select(MealAttendance.status)
            .where(MealAttendance.mealServiceId == meal_service_id)
        )).all()
        present = sum(1 for (s,) in rows if s == MealAttendanceStatus.PRESENT)
        absent = sum(1 for (s,) in rows if s == MealAttendanceStatus.ABSENT)
        excused = sum(1 for (s,) in rows if s == MealAttendanceStatus.EXCUSED)
        return MealAttendanceStatsResponse(
            mealServiceId=meal_service_id,
            totalPlanned=ms.mealsPlanned,
            totalRecorded=len(rows),
            present=present, absent=absent, excused=excused,
        )


MealsService = MealServiceModule  # alias plus parlant côté router


# ===================================================================
# TransportService — BusRoute + BusStop + Subscriptions
# ===================================================================
class TransportService(_ScopedService):

    # ---- Routes ------------------------------------------------------
    async def create_route(
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
        try:
            await self.session.flush()
        except IntegrityError as exc:
            raise ValidationFailedError(detail="Route déjà existante (nom unique)") from exc
        return await self._load_route(r.id)

    async def list_routes(
        self, user: User, school_id: str | None = None, limit: int = 500,
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

    async def _load_route(self, route_id: str) -> BusRouteRead:
        r = (await self.session.execute(
            select(BusRoute).where(BusRoute.id == route_id)
            .options(selectinload(BusRoute.school))
        )).scalar_one()
        return BusRouteRead.model_validate(r)

    # ---- Stops -------------------------------------------------------
    async def create_stop(
        self, user: User, dto: CreateBusStopRequest,
    ) -> BusStopRead:
        route = await self.session.get(BusRoute, dto.routeId)
        if route is None:
            raise NotFoundError(detail="Route bus introuvable")
        await self._assert_school_in_scope(user, route.schoolId)
        s = BusStop(
            routeId=dto.routeId, name=dto.name,
            lat=dto.lat, lon=dto.lon,
            pickupTime=dto.pickupTime, dropoffTime=dto.dropoffTime,
            stopOrder=dto.stopOrder,
        )
        self.session.add(s)
        try:
            await self.session.flush()
        except IntegrityError as exc:
            raise ValidationFailedError(detail="Arrêt déjà existant (nom unique)") from exc
        return BusStopRead.model_validate(s)

    async def list_stops(
        self, user: User, route_id: str,
    ) -> list[BusStopRead]:
        route = await self.session.get(BusRoute, route_id)
        if route is None:
            raise NotFoundError(detail="Route bus introuvable")
        await self._assert_school_in_scope(user, route.schoolId)
        stmt = (
            select(BusStop)
            .where(BusStop.routeId == route_id)
            .order_by(BusStop.stopOrder.asc(), BusStop.name.asc())
        )
        rows = (await self.session.execute(stmt)).scalars().all()
        return [BusStopRead.model_validate(r) for r in rows]

    # ---- Subscriptions ----------------------------------------------
    async def subscribe(
        self, user: User, dto: CreateBusSubscriptionRequest,
    ) -> BusSubscriptionRead:
        route = await self.session.get(BusRoute, dto.routeId)
        if route is None:
            raise NotFoundError(detail="Route bus introuvable")
        await self._assert_school_in_scope(user, route.schoolId)

        school_id = await self._student_school_id(dto.studentId)
        if school_id != route.schoolId:
            raise ValidationFailedError(
                detail="Élève et route doivent appartenir à la même école",
            )

        if dto.stopId:
            stop = await self.session.get(BusStop, dto.stopId)
            if stop is None or stop.routeId != dto.routeId:
                raise ValidationFailedError(detail="Arrêt invalide pour cette route")

        sub = StudentBusSubscription(
            studentId=dto.studentId, routeId=dto.routeId, stopId=dto.stopId,
            startDate=dto.startDate, endDate=dto.endDate,
            status=dto.status, monthlyFeeGNF=dto.monthlyFeeGNF,
            notes=dto.notes,
        )
        self.session.add(sub)
        try:
            await self.session.flush()
        except IntegrityError as exc:
            raise ValidationFailedError(
                detail="Abonnement déjà existant à cette date pour cet élève",
            ) from exc

        if sub.status == BusSubscriptionStatus.ACTIVE:
            route.studentsAssigned = (route.studentsAssigned or 0) + 1
            await self.session.flush()

        return await self._load_sub(sub.id)

    async def list_subscriptions(
        self, user: User,
        route_id: str | None = None,
        student_id: str | None = None,
        limit: int = 500,
    ) -> list[BusSubscriptionRead]:
        stmt = (
            select(StudentBusSubscription)
            .options(selectinload(StudentBusSubscription.student))
            .join(BusRoute, BusRoute.id == StudentBusSubscription.routeId)
            .where(BusRoute.schoolId.in_(self._scope_school_ids(user)))
            .order_by(StudentBusSubscription.startDate.desc())
            .limit(limit)
        )
        if route_id:
            stmt = stmt.where(StudentBusSubscription.routeId == route_id)
        if student_id:
            stmt = stmt.where(StudentBusSubscription.studentId == student_id)
        rows = (await self.session.execute(stmt)).scalars().unique().all()
        return [BusSubscriptionRead.model_validate(r) for r in rows]

    async def students_by_route(
        self, user: User, route_id: str,
    ) -> RouteStudentsResponse:
        route = await self.session.get(BusRoute, route_id)
        if route is None:
            raise NotFoundError(detail="Route bus introuvable")
        await self._assert_school_in_scope(user, route.schoolId)
        stmt = (
            select(Student)
            .join(
                StudentBusSubscription,
                StudentBusSubscription.studentId == Student.id,
            )
            .where(
                StudentBusSubscription.routeId == route_id,
                StudentBusSubscription.status == BusSubscriptionStatus.ACTIVE,
            )
            .order_by(Student.lastName.asc(), Student.firstName.asc())
        )
        students = (await self.session.execute(stmt)).scalars().unique().all()
        return RouteStudentsResponse(
            routeId=route_id,
            totalActiveSubscriptions=len(students),
            students=[_StudentBrief.model_validate(s) for s in students],
        )

    async def _load_sub(self, sub_id: str) -> BusSubscriptionRead:
        s = (await self.session.execute(
            select(StudentBusSubscription)
            .where(StudentBusSubscription.id == sub_id)
            .options(selectinload(StudentBusSubscription.student))
        )).scalar_one()
        return BusSubscriptionRead.model_validate(s)
