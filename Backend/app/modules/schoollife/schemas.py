"""Pydantic schemas — Vie scolaire (Phase 13)."""
from datetime import date, datetime, time

from pydantic import BaseModel, ConfigDict, Field

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
    occurredAt: datetime
    recordedById: str | None = None
    createdAt: datetime
    updatedAt: datetime


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
