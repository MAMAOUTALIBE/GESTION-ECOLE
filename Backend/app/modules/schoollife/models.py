"""Vie scolaire — Phase 13 + Module 7 (4 sous-domaines).

Phase 13 (héritage) :
    Incident         — discipline / sanctions
    HealthVisit      — santé scolaire (passages infirmerie, visites médicales)
    BusRoute         — transport scolaire (lignes de bus)
    MealService      — cantines (repas servis quotidiennement)
    TimetableSlot    — emploi du temps par classe

Module 7 (greenfield) :
    Vaccination               — vaccins administrés à un élève
    StudentAllergy            — allergies générales / alimentaires
    MealAttendance            — présence d'un élève à un service de cantine
    BusStop                   — points d'arrêt rattachés à une route
    StudentBusSubscription    — abonnement d'un élève à une route
    IncidentStatusField       — colonne status ajoutée à Incident

Toutes les tables sont scope-aware (rattachement direct ou transitif à
``School``) et utilisent ``TimestampMixin`` pour ``createdAt`` / ``updatedAt``.
"""
from datetime import date, datetime, time
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    JSON,
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
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.modules.schoollife.enums import (
    AllergyCategory,
    AllergySeverity,
    BusSubscriptionStatus,
    IncidentStatus,
    MealAttendanceStatus,
    VaccinationStatus,
)
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
    # Module 7 — statut administratif de l'incident (peut être null pour les
    # incidents importés depuis la phase 13, qui n'avaient pas ce champ).
    status: Mapped[IncidentStatus] = mapped_column(
        Enum(IncidentStatus, name="IncidentStatus", native_enum=True),
        default=IncidentStatus.OPEN, nullable=False,
        server_default="OPEN",
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


# =============================================================
# MODULE 7 — Vaccinations
# =============================================================
class Vaccination(Base, TimestampMixin):
    """Trace d'une vaccination administrée à un élève.

    MVP : on stocke le vaccin (texte libre, ex. "BCG", "Pentavalent dose 1"),
    la date d'administration, un éventuel numéro de lot, le statut, et le
    nom du soignant. Un vrai "calendrier vaccinal" (PEV Guinée) avec doses
    automatisées est laissé au backlog 7.1.
    """

    __tablename__ = "Vaccination"
    __table_args__ = (
        Index("ix_Vaccination_studentId", "studentId"),
        Index(
            "ix_Vaccination_vaccine_dateAdministered",
            "vaccine", "dateAdministered",
        ),
    )

    id: Mapped[str] = cuid_pk()
    studentId: Mapped[str] = mapped_column(
        String(30), ForeignKey("Student.id"), nullable=False
    )
    vaccine: Mapped[str] = mapped_column(String(120), nullable=False)
    dateAdministered: Mapped[date] = mapped_column(Date, nullable=False)
    batchNumber: Mapped[str | None] = mapped_column(String(80), nullable=True)
    administeredBy: Mapped[str | None] = mapped_column(String(200), nullable=True)
    status: Mapped[VaccinationStatus] = mapped_column(
        Enum(VaccinationStatus, name="VaccinationStatus", native_enum=True),
        default=VaccinationStatus.ADMINISTERED, nullable=False,
        server_default="ADMINISTERED",
    )
    notes: Mapped[str | None] = mapped_column(String, nullable=True)
    recordedById: Mapped[str | None] = mapped_column(
        String(30), ForeignKey("User.id"), nullable=True
    )

    student: Mapped["Student"] = relationship(lazy="raise")


# =============================================================
# MODULE 7 — Allergies générales
# =============================================================
class StudentAllergy(Base, TimestampMixin):
    """Allergie déclarée pour un élève (alimentaire, médicamenteuse, etc.).

    On ne fait pas de FK vers une table d'allergènes — la valeur reste libre
    (`allergen`), c'est le plus pragmatique pour le terrain. Les allergies
    alimentaires servent aussi à filtrer les présences cantine.
    """

    __tablename__ = "StudentAllergy"
    __table_args__ = (
        Index("ix_StudentAllergy_studentId", "studentId"),
        Index("ix_StudentAllergy_category", "category"),
    )

    id: Mapped[str] = cuid_pk()
    studentId: Mapped[str] = mapped_column(
        String(30), ForeignKey("Student.id"), nullable=False
    )
    allergen: Mapped[str] = mapped_column(String(200), nullable=False)
    category: Mapped[AllergyCategory] = mapped_column(
        Enum(AllergyCategory, name="AllergyCategory", native_enum=True),
        default=AllergyCategory.FOOD, nullable=False,
        server_default="FOOD",
    )
    severity: Mapped[AllergySeverity] = mapped_column(
        Enum(AllergySeverity, name="AllergySeverity", native_enum=True),
        default=AllergySeverity.MILD, nullable=False,
        server_default="MILD",
    )
    notes: Mapped[str | None] = mapped_column(String, nullable=True)
    recordedById: Mapped[str | None] = mapped_column(
        String(30), ForeignKey("User.id"), nullable=True
    )

    student: Mapped["Student"] = relationship(lazy="raise")


# =============================================================
# MODULE 7 — Présences cantine (MealAttendance)
# =============================================================
class MealAttendance(Base, TimestampMixin):
    """Présence d'un élève à un service de cantine.

    Clé composite logique : (mealServiceId, studentId) — un élève ne peut
    être présent qu'une fois par service.
    """

    __tablename__ = "MealAttendance"
    __table_args__ = (
        UniqueConstraint(
            "mealServiceId", "studentId",
            name="uq_MealAttendance_mealServiceId_studentId",
        ),
        Index("ix_MealAttendance_studentId", "studentId"),
    )

    id: Mapped[str] = cuid_pk()
    mealServiceId: Mapped[str] = mapped_column(
        String(30), ForeignKey("MealService.id"), nullable=False
    )
    studentId: Mapped[str] = mapped_column(
        String(30), ForeignKey("Student.id"), nullable=False
    )
    status: Mapped[MealAttendanceStatus] = mapped_column(
        Enum(MealAttendanceStatus, name="MealAttendanceStatus", native_enum=True),
        default=MealAttendanceStatus.PRESENT, nullable=False,
        server_default="PRESENT",
    )
    recordedById: Mapped[str | None] = mapped_column(
        String(30), ForeignKey("User.id"), nullable=True
    )

    mealService: Mapped["MealService"] = relationship(lazy="raise")
    student: Mapped["Student"] = relationship(lazy="raise")


# =============================================================
# MODULE 7 — Menu (JSONB) attaché au MealService
# =============================================================
# On utilise une table satellite plutôt que d'altérer MealService pour
# ne pas casser le schema existant. Un menu peut être enrichi ultérieurement
# (allergènes auto-détectés, calorimétrie, …).
class MealMenu(Base, TimestampMixin):
    __tablename__ = "MealMenu"
    __table_args__ = (
        UniqueConstraint(
            "mealServiceId",
            name="uq_MealMenu_mealServiceId",
        ),
    )

    id: Mapped[str] = cuid_pk()
    mealServiceId: Mapped[str] = mapped_column(
        String(30), ForeignKey("MealService.id"), nullable=False
    )
    # Sur SQLite (peu probable ici mais on reste prudent) on retombe sur JSON.
    items: Mapped[Any] = mapped_column(JSON().with_variant(JSONB(), "postgresql"),
                                       nullable=False, default=list)
    allergens: Mapped[Any] = mapped_column(JSON().with_variant(JSONB(), "postgresql"),
                                           nullable=True, default=list)
    estimatedCostGNF: Mapped[float | None] = mapped_column(Float, nullable=True)

    mealService: Mapped["MealService"] = relationship(lazy="raise")


# =============================================================
# MODULE 7 — Bus stops + abonnements
# =============================================================
class BusStop(Base, TimestampMixin):
    """Point d'arrêt d'une tournée de bus.

    Coordonnées lat/lon stockées en Float plain (pas de PostGIS ici — c'est
    déjà géré sur ``School``). Les heures sont notées en chaîne ``HH:MM``.
    """

    __tablename__ = "BusStop"
    __table_args__ = (
        Index("ix_BusStop_routeId_order", "routeId", "stopOrder"),
        UniqueConstraint(
            "routeId", "name",
            name="uq_BusStop_routeId_name",
        ),
    )

    id: Mapped[str] = cuid_pk()
    routeId: Mapped[str] = mapped_column(
        String(30), ForeignKey("BusRoute.id"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    lon: Mapped[float | None] = mapped_column(Float, nullable=True)
    pickupTime: Mapped[str | None] = mapped_column(String(5), nullable=True)  # HH:MM
    dropoffTime: Mapped[str | None] = mapped_column(String(5), nullable=True)
    stopOrder: Mapped[int] = mapped_column(Integer, nullable=False, default=0,
                                           server_default="0")

    route: Mapped["BusRoute"] = relationship(lazy="raise")


class StudentBusSubscription(Base, TimestampMixin):
    """Abonnement d'un élève à une route de bus, à un point d'arrêt précis."""

    __tablename__ = "StudentBusSubscription"
    __table_args__ = (
        Index("ix_StudentBusSubscription_studentId", "studentId"),
        Index("ix_StudentBusSubscription_routeId_status", "routeId", "status"),
        UniqueConstraint(
            "studentId", "routeId", "startDate",
            name="uq_StudentBusSubscription_student_route_start",
        ),
    )

    id: Mapped[str] = cuid_pk()
    studentId: Mapped[str] = mapped_column(
        String(30), ForeignKey("Student.id"), nullable=False
    )
    routeId: Mapped[str] = mapped_column(
        String(30), ForeignKey("BusRoute.id"), nullable=False
    )
    stopId: Mapped[str | None] = mapped_column(
        String(30), ForeignKey("BusStop.id"), nullable=True
    )
    startDate: Mapped[date] = mapped_column(Date, nullable=False)
    endDate: Mapped[date | None] = mapped_column(Date, nullable=True)
    status: Mapped[BusSubscriptionStatus] = mapped_column(
        Enum(BusSubscriptionStatus, name="BusSubscriptionStatus", native_enum=True),
        default=BusSubscriptionStatus.ACTIVE, nullable=False,
        server_default="ACTIVE",
    )
    monthlyFeeGNF: Mapped[float | None] = mapped_column(Float, nullable=True)
    notes: Mapped[str | None] = mapped_column(String, nullable=True)

    student: Mapped["Student"] = relationship(lazy="raise")
    route: Mapped["BusRoute"] = relationship(lazy="raise")
    stop: Mapped["BusStop | None"] = relationship(lazy="raise")
