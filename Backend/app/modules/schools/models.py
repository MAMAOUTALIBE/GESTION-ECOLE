from datetime import datetime
from typing import TYPE_CHECKING

from geoalchemy2 import Geography, WKBElement
from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.shared.base import Base, TimestampMixin, cuid_pk
from app.shared.enums import (
    BuildingCondition,
    ElectricitySource,
    SchoolAffiliation,
    ValidationStatus,
    WaterSource,
)

if TYPE_CHECKING:
    from app.modules.academics.models import (
        Assessment,
        Grade,
        ReportCard,
        SchoolYear,
    )
    from app.modules.attendance.models import AttendanceRecord
    from app.modules.auth.models import User
    from app.modules.census.models import Student, StudentTransfer, Teacher
    from app.modules.inspections.models import Inspection
    from app.modules.library.models import LibraryInventory
    from app.modules.territory.models import Prefecture, Region, SubPrefecture


class School(Base, TimestampMixin):
    __tablename__ = "School"
    __table_args__ = (
        Index("ix_School_regionId_status", "regionId", "status"),
        Index("ix_School_prefectureId_status", "prefectureId", "status"),
        Index("ix_School_subPrefectureId_status", "subPrefectureId", "status"),
    )

    id: Mapped[str] = cuid_pk()
    name: Mapped[str] = mapped_column(String, nullable=False)
    code: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    regionId: Mapped[str] = mapped_column(String(30), ForeignKey("Region.id"), nullable=False)
    prefectureId: Mapped[str | None] = mapped_column(
        String(30), ForeignKey("Prefecture.id"), nullable=True
    )
    subPrefectureId: Mapped[str | None] = mapped_column(
        String(30), ForeignKey("SubPrefecture.id"), nullable=True
    )

    address: Mapped[str | None] = mapped_column(String, nullable=True)
    # Free-text legacy fields kept for backward compatibility with NestJS payloads
    prefecture: Mapped[str | None] = mapped_column(String, nullable=True)
    commune: Mapped[str | None] = mapped_column(String, nullable=True)
    type: Mapped[str | None] = mapped_column(String, nullable=True)
    phone: Mapped[str | None] = mapped_column(String, nullable=True)
    latitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    longitude: Mapped[float | None] = mapped_column(Float, nullable=True)

    # PostGIS geography Point (auto-synced from lat/lon by Postgres trigger
    # `trg_school_sync_geom` — see Alembic 0003). `deferred=True` keeps it
    # out of default SELECTs since most code paths never need the WKB.
    geom: Mapped[WKBElement | None] = mapped_column(
        Geography(geometry_type="POINT", srid=4326),
        nullable=True,
        deferred=True,
    )

    status: Mapped[ValidationStatus] = mapped_column(
        Enum(ValidationStatus, name="ValidationStatus", native_enum=True),
        default=ValidationStatus.APPROVED,
        nullable=False,
    )
    rejectionReason: Mapped[str | None] = mapped_column(String, nullable=True)
    createdById: Mapped[str | None] = mapped_column(String(30), nullable=True)
    approvedById: Mapped[str | None] = mapped_column(String(30), nullable=True)
    approvedAt: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # ==================================================================
    # Phase 10 — Infrastructure structurée (eau, élec, toilettes, bâti)
    # Tous nullables pour ne pas casser les écoles existantes ; remplis
    # progressivement par les inspecteurs ou via imports masse.
    # ==================================================================
    waterSource: Mapped[WaterSource | None] = mapped_column(
        Enum(WaterSource, name="WaterSource", native_enum=True), nullable=True
    )
    electricitySource: Mapped[ElectricitySource | None] = mapped_column(
        Enum(ElectricitySource, name="ElectricitySource", native_enum=True),
        nullable=True,
    )
    internetAvailable: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False, server_default="false"
    )
    toiletsBoys: Mapped[int | None] = mapped_column(Integer, nullable=True)
    toiletsGirls: Mapped[int | None] = mapped_column(Integer, nullable=True)
    toiletsAccessible: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False, server_default="false"
    )
    classroomsTotal: Mapped[int | None] = mapped_column(Integer, nullable=True)
    classroomsUsable: Mapped[int | None] = mapped_column(Integer, nullable=True)
    buildingCondition: Mapped[BuildingCondition | None] = mapped_column(
        Enum(BuildingCondition, name="BuildingCondition", native_enum=True),
        nullable=True,
    )
    buildingYear: Mapped[int | None] = mapped_column(Integer, nullable=True)
    multiShift: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False, server_default="false"
    )
    distanceToHealthCenterKm: Mapped[float | None] = mapped_column(Float, nullable=True)
    affiliation: Mapped[SchoolAffiliation | None] = mapped_column(
        Enum(SchoolAffiliation, name="SchoolAffiliation", native_enum=True),
        nullable=True,
    )

    region: Mapped["Region"] = relationship(back_populates="schools", lazy="raise")
    prefectureRef: Mapped["Prefecture | None"] = relationship(
        back_populates="schools",
        foreign_keys=[prefectureId],
        lazy="raise",
    )
    subPrefecture: Mapped["SubPrefecture | None"] = relationship(
        back_populates="schools", lazy="raise"
    )

    classes: Mapped[list["ClassRoom"]] = relationship(back_populates="school", lazy="raise")
    students: Mapped[list["Student"]] = relationship(back_populates="school", lazy="raise")
    teachers: Mapped[list["Teacher"]] = relationship(back_populates="school", lazy="raise")
    users: Mapped[list["User"]] = relationship(back_populates="school", lazy="raise")
    attendances: Mapped[list["AttendanceRecord"]] = relationship(
        back_populates="school", lazy="raise"
    )
    transfersOut: Mapped[list["StudentTransfer"]] = relationship(
        back_populates="fromSchool",
        foreign_keys="StudentTransfer.fromSchoolId",
        lazy="raise",
    )
    transfersIn: Mapped[list["StudentTransfer"]] = relationship(
        back_populates="toSchool",
        foreign_keys="StudentTransfer.toSchoolId",
        lazy="raise",
    )
    libraryInventory: Mapped[list["LibraryInventory"]] = relationship(
        back_populates="school", lazy="raise"
    )
    inspections: Mapped[list["Inspection"]] = relationship(
        back_populates="school", lazy="raise"
    )


class ClassRoom(Base, TimestampMixin):
    __tablename__ = "ClassRoom"
    __table_args__ = (UniqueConstraint("schoolId", "name", name="uq_ClassRoom_schoolId_name"),)

    id: Mapped[str] = cuid_pk()
    name: Mapped[str] = mapped_column(String, nullable=False)
    level: Mapped[str | None] = mapped_column(String, nullable=True)
    maxStudents: Mapped[int | None] = mapped_column(Integer, nullable=True)
    schoolYear: Mapped[str | None] = mapped_column(String, nullable=True)
    schoolYearId: Mapped[str | None] = mapped_column(
        String(30), ForeignKey("SchoolYear.id"), nullable=True
    )
    schoolId: Mapped[str] = mapped_column(String(30), ForeignKey("School.id"), nullable=False)

    school: Mapped["School"] = relationship(back_populates="classes", lazy="raise")
    academicYear: Mapped["SchoolYear | None"] = relationship(
        back_populates="classes", lazy="raise"
    )
    students: Mapped[list["Student"]] = relationship(back_populates="classRoom", lazy="raise")
    teachers: Mapped[list["Teacher"]] = relationship(
        secondary="_ClassRoomTeacher", back_populates="classes", lazy="raise"
    )
    transfersOut: Mapped[list["StudentTransfer"]] = relationship(
        back_populates="fromClassRoom",
        foreign_keys="StudentTransfer.fromClassRoomId",
        lazy="raise",
    )
    transfersIn: Mapped[list["StudentTransfer"]] = relationship(
        back_populates="toClassRoom",
        foreign_keys="StudentTransfer.toClassRoomId",
        lazy="raise",
    )
    assessments: Mapped[list["Assessment"]] = relationship(
        back_populates="classRoom", lazy="raise"
    )
    grades: Mapped[list["Grade"]] = relationship(back_populates="classRoom", lazy="raise")
    reportCards: Mapped[list["ReportCard"]] = relationship(
        back_populates="classRoom", lazy="raise"
    )


# Implicit many-to-many table generated by Prisma for Teacher <-> ClassRoom.
# Prisma names this table "_ClassRoomToTeacher" by default; we mirror that exactly.
from sqlalchemy import Column, Table  # noqa: E402
from sqlalchemy import ForeignKey as FK

class_room_teacher_table = Table(
    "_ClassRoomTeacher",
    Base.metadata,
    Column("A", String(30), FK("ClassRoom.id"), primary_key=True),
    Column("B", String(30), FK("Teacher.id"), primary_key=True),
)
