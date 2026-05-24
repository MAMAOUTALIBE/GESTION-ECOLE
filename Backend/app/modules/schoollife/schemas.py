"""Pydantic schemas — Vie scolaire (Phase 13 + Module 7)."""
from datetime import date, datetime, time
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.modules.schoollife.enums import (
    AllergyCategory,
    AllergySeverity,
    BusSubscriptionStatus,
    IncidentStatus,
    MealAttendanceStatus,
    VaccinationStatus,
)
from app.shared.enums import (
    DayOfWeek,
    HealthVisitStatus,
    HealthVisitType,
    IncidentSanction,
    IncidentSeverity,
    IncidentType,
    MealServiceType,
    TransportRouteStatus,
)


# =============================================================
# Briefs partagés
# =============================================================
class _SchoolBrief(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    name: str
    code: str


class _StudentBrief(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    firstName: str
    lastName: str
    uniqueCode: str


class _SubjectBrief(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    name: str
    code: str


class _TeacherBrief(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    firstName: str
    lastName: str


class _ClassRoomBrief(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    name: str
    level: str | None = None


# =============================================================
# INCIDENTS (discipline)
# =============================================================
class CreateIncidentRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    schoolId: str
    studentId: str | None = None
    type: IncidentType
    severity: IncidentSeverity = IncidentSeverity.LOW
    description: str = Field(min_length=3, max_length=2000)
    sanction: IncidentSanction = IncidentSanction.NONE
    occurredAt: datetime
    status: IncidentStatus = IncidentStatus.OPEN


class UpdateIncidentRequest(BaseModel):
    """PATCH: champs optionnels — sanction / statut / sévérité / description."""

    model_config = ConfigDict(str_strip_whitespace=True)

    severity: IncidentSeverity | None = None
    description: str | None = Field(default=None, min_length=3, max_length=2000)
    sanction: IncidentSanction | None = None
    status: IncidentStatus | None = None


class IncidentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    schoolId: str
    school: _SchoolBrief | None = None
    studentId: str | None = None
    student: _StudentBrief | None = None
    type: IncidentType
    severity: IncidentSeverity
    description: str
    sanction: IncidentSanction
    status: IncidentStatus
    occurredAt: datetime
    recordedById: str | None = None
    createdAt: datetime
    updatedAt: datetime


class IncidentStatsResponse(BaseModel):
    """Compteurs simples : incidents par sévérité / sanction / statut."""

    total: int
    bySeverity: dict[str, int]
    bySanction: dict[str, int]
    byStatus: dict[str, int]


# =============================================================
# HEALTH VISITS (santé scolaire)
# =============================================================
class CreateHealthVisitRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    schoolId: str
    studentId: str | None = None
    type: HealthVisitType
    description: str = Field(min_length=3, max_length=2000)
    visitDate: date
    nurseName: str | None = Field(default=None, max_length=200)
    status: HealthVisitStatus = HealthVisitStatus.REPORTED


class HealthVisitRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    schoolId: str
    school: _SchoolBrief | None = None
    studentId: str | None = None
    student: _StudentBrief | None = None
    type: HealthVisitType
    description: str
    visitDate: date
    nurseName: str | None = None
    status: HealthVisitStatus
    createdAt: datetime
    updatedAt: datetime


# =============================================================
# BUS ROUTES (transport scolaire)
# =============================================================
class CreateBusRouteRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    schoolId: str
    name: str = Field(min_length=2, max_length=200)
    capacity: int = Field(default=40, ge=4, le=200)
    departureTime: str = Field(pattern=r"^\d{2}:\d{2}$")  # HH:MM
    returnTime: str = Field(pattern=r"^\d{2}:\d{2}$")
    driverName: str | None = None
    driverPhone: str | None = None
    plate: str | None = None
    studentsAssigned: int = Field(default=0, ge=0)
    status: TransportRouteStatus = TransportRouteStatus.ACTIVE


class BusRouteRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    schoolId: str
    school: _SchoolBrief | None = None
    name: str
    capacity: int
    departureTime: str
    returnTime: str
    driverName: str | None = None
    driverPhone: str | None = None
    plate: str | None = None
    studentsAssigned: int
    status: TransportRouteStatus
    createdAt: datetime
    updatedAt: datetime


# =============================================================
# MEAL SERVICES (cantines)
# =============================================================
class CreateMealServiceRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    schoolId: str
    type: MealServiceType = MealServiceType.LUNCH
    serviceDate: date
    mealsPlanned: int = Field(ge=0)
    mealsServed: int = Field(ge=0)
    costPerMealGNF: float = Field(ge=0)
    notes: str | None = Field(default=None, max_length=2000)


class MealServiceRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    schoolId: str
    school: _SchoolBrief | None = None
    type: MealServiceType
    serviceDate: date
    mealsPlanned: int
    mealsServed: int
    costPerMealGNF: float
    notes: str | None = None
    createdAt: datetime
    updatedAt: datetime


# =============================================================
# TIMETABLE (emploi du temps)
# =============================================================
class CreateTimetableSlotRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    classRoomId: str
    dayOfWeek: DayOfWeek
    startTime: time
    endTime: time
    subjectId: str | None = None
    teacherId: str | None = None
    room: str | None = None


class TimetableSlotRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    classRoomId: str
    classRoom: _ClassRoomBrief | None = None
    dayOfWeek: DayOfWeek
    startTime: time
    endTime: time
    subjectId: str | None = None
    subject: _SubjectBrief | None = None
    teacherId: str | None = None
    teacher: _TeacherBrief | None = None
    room: str | None = None
    createdAt: datetime
    updatedAt: datetime


# =============================================================
# MODULE 7 — Vaccinations
# =============================================================
class CreateVaccinationRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    studentId: str
    vaccine: str = Field(min_length=2, max_length=120)
    dateAdministered: date
    batchNumber: str | None = Field(default=None, max_length=80)
    administeredBy: str | None = Field(default=None, max_length=200)
    status: VaccinationStatus = VaccinationStatus.ADMINISTERED
    notes: str | None = Field(default=None, max_length=2000)


class VaccinationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    studentId: str
    student: _StudentBrief | None = None
    vaccine: str
    dateAdministered: date
    batchNumber: str | None = None
    administeredBy: str | None = None
    status: VaccinationStatus
    notes: str | None = None
    recordedById: str | None = None
    createdAt: datetime
    updatedAt: datetime


# =============================================================
# MODULE 7 — StudentAllergy
# =============================================================
class CreateAllergyRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    studentId: str
    allergen: str = Field(min_length=2, max_length=200)
    category: AllergyCategory = AllergyCategory.FOOD
    severity: AllergySeverity = AllergySeverity.MILD
    notes: str | None = Field(default=None, max_length=2000)


class AllergyRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    studentId: str
    student: _StudentBrief | None = None
    allergen: str
    category: AllergyCategory
    severity: AllergySeverity
    notes: str | None = None
    recordedById: str | None = None
    createdAt: datetime
    updatedAt: datetime


# =============================================================
# MODULE 7 — MealMenu (rattaché à un MealService)
# =============================================================
class CreateMealMenuRequest(BaseModel):
    """Menu du jour : on créé d'abord un MealService puis on le complète.

    Si ``mealServiceId`` est null, on crée un nouveau MealService avec les
    champs ``schoolId`` + ``mealDate`` + ``mealType`` fournis.
    """

    model_config = ConfigDict(str_strip_whitespace=True)

    mealServiceId: str | None = None
    schoolId: str | None = None
    mealDate: date | None = None
    mealType: MealServiceType = MealServiceType.LUNCH
    items: list[str] = Field(default_factory=list, max_length=50)
    allergens: list[str] = Field(default_factory=list, max_length=50)
    estimatedCostGNF: float | None = Field(default=None, ge=0)


class MealMenuRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    mealServiceId: str
    items: list[str] | Any
    allergens: list[str] | Any | None = None
    estimatedCostGNF: float | None = None
    createdAt: datetime
    updatedAt: datetime


# =============================================================
# MODULE 7 — Présences cantine (bulk)
# =============================================================
class MealAttendanceEntry(BaseModel):
    studentId: str
    status: MealAttendanceStatus = MealAttendanceStatus.PRESENT


class BulkMealAttendanceRequest(BaseModel):
    """Saisie de la cantine par batch (un enseignant valide la classe en
    1 requête)."""

    mealServiceId: str
    entries: list[MealAttendanceEntry] = Field(min_length=1, max_length=2000)


class MealAttendanceRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    mealServiceId: str
    studentId: str
    status: MealAttendanceStatus
    recordedById: str | None = None
    createdAt: datetime
    updatedAt: datetime


class MealAttendanceStatsResponse(BaseModel):
    mealServiceId: str
    totalPlanned: int
    totalRecorded: int
    present: int
    absent: int
    excused: int


# =============================================================
# MODULE 7 — Bus stops + abonnements
# =============================================================
class CreateBusStopRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    routeId: str
    name: str = Field(min_length=2, max_length=200)
    lat: float | None = Field(default=None, ge=-90.0, le=90.0)
    lon: float | None = Field(default=None, ge=-180.0, le=180.0)
    pickupTime: str | None = Field(default=None, pattern=r"^\d{2}:\d{2}$")
    dropoffTime: str | None = Field(default=None, pattern=r"^\d{2}:\d{2}$")
    stopOrder: int = Field(default=0, ge=0)


class BusStopRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    routeId: str
    name: str
    lat: float | None = None
    lon: float | None = None
    pickupTime: str | None = None
    dropoffTime: str | None = None
    stopOrder: int
    createdAt: datetime
    updatedAt: datetime


class CreateBusSubscriptionRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    studentId: str
    routeId: str
    stopId: str | None = None
    startDate: date
    endDate: date | None = None
    status: BusSubscriptionStatus = BusSubscriptionStatus.ACTIVE
    monthlyFeeGNF: float | None = Field(default=None, ge=0)
    notes: str | None = Field(default=None, max_length=2000)


class BusSubscriptionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    studentId: str
    student: _StudentBrief | None = None
    routeId: str
    stopId: str | None = None
    startDate: date
    endDate: date | None = None
    status: BusSubscriptionStatus
    monthlyFeeGNF: float | None = None
    notes: str | None = None
    createdAt: datetime
    updatedAt: datetime


class RouteStudentsResponse(BaseModel):
    routeId: str
    totalActiveSubscriptions: int
    students: list[_StudentBrief]
