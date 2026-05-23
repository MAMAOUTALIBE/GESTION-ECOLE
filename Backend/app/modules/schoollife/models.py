"""Vie scolaire — Phase 13.

Cinq tables greenfield qui adressent les modules administratifs n'ayant pas
encore de backend dédié :
    Incident         — discipline / sanctions
    HealthVisit      — santé scolaire (passages infirmerie, visites médicales)
    BusRoute         — transport scolaire (lignes de bus)
    MealService      — cantines (repas servis quotidiennement)
    TimetableSlot    — emploi du temps par classe
"""
from datetime import date, datetime, time
from typing import TYPE_CHECKING

from sqlalchemy import (
    Date,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Time,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.shared.base import Base, CreatedAtMixin, TimestampMixin, cuid_pk
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

if TYPE_CHECKING:
    from app.modules.academics.models import Subject
    from app.modules.auth.models import User
    from app.modules.census.models import Student, Teacher
    from app.modules.schools.models import ClassRoom, School


class Incident(Base, TimestampMixin):
    __tablename__ = "Incident"
    __table_args__ = (
        Index("ix_Incident_schoolId_occurredAt", "schoolId", "occurredAt"),
        Index("ix_Incident_studentId", "studentId"),
        Index("ix_Incident_severity", "severity"),
    )

    id: Mapped[str] = cuid_pk()
    schoolId: Mapped[str] = mapped_column(
        String(30), ForeignKey("School.id"), nullable=False
    )
    studentId: Mapped[str | None] = mapped_column(
        String(30), ForeignKey("Student.id"), nullable=True
    )
    type: Mapped[IncidentType] = mapped_column(
        Enum(IncidentType, name="IncidentType", native_enum=True), nullable=False
    )
    severity: Mapped[IncidentSeverity] = mapped_column(
        Enum(IncidentSeverity, name="IncidentSeverity", native_enum=True),
        default=IncidentSeverity.LOW, nullable=False,
    )
    description: Mapped[str] = mapped_column(String, nullable=False)
    sanction: Mapped[IncidentSanction] = mapped_column(
        Enum(IncidentSanction, name="IncidentSanction", native_enum=True),
        default=IncidentSanction.NONE, nullable=False,
    )
    occurredAt: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    recordedById: Mapped[str | None] = mapped_column(
        String(30), ForeignKey("User.id"), nullable=True
    )

    school: Mapped["School"] = relationship(lazy="raise")
    student: Mapped["Student | None"] = relationship(lazy="raise")
    recordedBy: Mapped["User | None"] = relationship(
        foreign_keys=[recordedById], lazy="raise"
    )


class HealthVisit(Base, TimestampMixin):
    __tablename__ = "HealthVisit"
    __table_args__ = (
        Index("ix_HealthVisit_schoolId_visitDate", "schoolId", "visitDate"),
        Index("ix_HealthVisit_studentId", "studentId"),
    )

    id: Mapped[str] = cuid_pk()
    schoolId: Mapped[str] = mapped_column(
        String(30), ForeignKey("School.id"), nullable=False
    )
    studentId: Mapped[str | None] = mapped_column(
        String(30), ForeignKey("Student.id"), nullable=True
    )
    type: Mapped[HealthVisitType] = mapped_column(
        Enum(HealthVisitType, name="HealthVisitType", native_enum=True), nullable=False
    )
    description: Mapped[str] = mapped_column(String, nullable=False)
    visitDate: Mapped[date] = mapped_column(Date, nullable=False)
    nurseName: Mapped[str | None] = mapped_column(String, nullable=True)
    status: Mapped[HealthVisitStatus] = mapped_column(
        Enum(HealthVisitStatus, name="HealthVisitStatus", native_enum=True),
        default=HealthVisitStatus.REPORTED, nullable=False,
    )

    school: Mapped["School"] = relationship(lazy="raise")
    student: Mapped["Student | None"] = relationship(lazy="raise")


class BusRoute(Base, TimestampMixin):
    __tablename__ = "BusRoute"
    __table_args__ = (
        Index("ix_BusRoute_schoolId_status", "schoolId", "status"),
        UniqueConstraint("schoolId", "name", name="uq_BusRoute_schoolId_name"),
    )

    id: Mapped[str] = cuid_pk()
    schoolId: Mapped[str] = mapped_column(
        String(30), ForeignKey("School.id"), nullable=False
    )
    name: Mapped[str] = mapped_column(String, nullable=False)
    capacity: Mapped[int] = mapped_column(Integer, nullable=False, default=40)
    departureTime: Mapped[str] = mapped_column(String(5), nullable=False)  # "07:30"
    returnTime: Mapped[str] = mapped_column(String(5), nullable=False)
    driverName: Mapped[str | None] = mapped_column(String, nullable=True)
    driverPhone: Mapped[str | None] = mapped_column(String, nullable=True)
    plate: Mapped[str | None] = mapped_column(String, nullable=True)
    status: Mapped[TransportRouteStatus] = mapped_column(
        Enum(TransportRouteStatus, name="TransportRouteStatus", native_enum=True),
        default=TransportRouteStatus.ACTIVE, nullable=False,
    )
    studentsAssigned: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    school: Mapped["School"] = relationship(lazy="raise")


class MealService(Base, TimestampMixin):
    __tablename__ = "MealService"
    __table_args__ = (
        Index("ix_MealService_schoolId_serviceDate", "schoolId", "serviceDate"),
    )

    id: Mapped[str] = cuid_pk()
    schoolId: Mapped[str] = mapped_column(
        String(30), ForeignKey("School.id"), nullable=False
    )
    type: Mapped[MealServiceType] = mapped_column(
        Enum(MealServiceType, name="MealServiceType", native_enum=True),
        default=MealServiceType.LUNCH, nullable=False,
    )
    serviceDate: Mapped[date] = mapped_column(Date, nullable=False)
    mealsPlanned: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    mealsServed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    costPerMealGNF: Mapped[float] = mapped_column(Float, nullable=False, default=2500.0)
    notes: Mapped[str | None] = mapped_column(String, nullable=True)

    school: Mapped["School"] = relationship(lazy="raise")


class TimetableSlot(Base, TimestampMixin):
    __tablename__ = "TimetableSlot"
    __table_args__ = (
        Index("ix_TimetableSlot_classRoomId_dayOfWeek",
              "classRoomId", "dayOfWeek"),
    )

    id: Mapped[str] = cuid_pk()
    classRoomId: Mapped[str] = mapped_column(
        String(30), ForeignKey("ClassRoom.id"), nullable=False
    )
    dayOfWeek: Mapped[DayOfWeek] = mapped_column(
        Enum(DayOfWeek, name="DayOfWeek", native_enum=True), nullable=False
    )
    startTime: Mapped[time] = mapped_column(Time, nullable=False)
    endTime: Mapped[time] = mapped_column(Time, nullable=False)
    subjectId: Mapped[str | None] = mapped_column(
        String(30), ForeignKey("Subject.id"), nullable=True
    )
    teacherId: Mapped[str | None] = mapped_column(
        String(30), ForeignKey("Teacher.id"), nullable=True
    )
    room: Mapped[str | None] = mapped_column(String, nullable=True)

    classRoom: Mapped["ClassRoom"] = relationship(lazy="raise")
    subject: Mapped["Subject | None"] = relationship(lazy="raise")
    teacher: Mapped["Teacher | None"] = relationship(lazy="raise")
