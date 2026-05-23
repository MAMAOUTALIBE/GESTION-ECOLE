from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, Enum, ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.shared.base import Base, CreatedAtMixin, cuid_pk
from app.shared.enums import AttendanceStatus, PersonType

if TYPE_CHECKING:
    from app.modules.census.models import Student, Teacher
    from app.modules.schools.models import School


class QrCredential(Base, CreatedAtMixin):
    __tablename__ = "QrCredential"

    id: Mapped[str] = cuid_pk()
    token: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    payload: Mapped[str] = mapped_column(String, nullable=False)
    personType: Mapped[PersonType] = mapped_column(
        Enum(PersonType, name="PersonType", native_enum=True), nullable=False
    )
    studentId: Mapped[str | None] = mapped_column(
        String(30), ForeignKey("Student.id"), unique=True, nullable=True
    )
    teacherId: Mapped[str | None] = mapped_column(
        String(30), ForeignKey("Teacher.id"), unique=True, nullable=True
    )
    revokedAt: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    student: Mapped["Student | None"] = relationship(
        back_populates="qrCredential", lazy="raise"
    )
    teacher: Mapped["Teacher | None"] = relationship(
        back_populates="qrCredential", lazy="raise"
    )


class AttendanceRecord(Base):
    __tablename__ = "AttendanceRecord"
    __table_args__ = (
        Index("ix_AttendanceRecord_schoolId_scannedAt", "schoolId", "scannedAt"),
        Index("ix_AttendanceRecord_studentId_scannedAt", "studentId", "scannedAt"),
        Index("ix_AttendanceRecord_teacherId_scannedAt", "teacherId", "scannedAt"),
    )

    id: Mapped[str] = cuid_pk()
    personType: Mapped[PersonType] = mapped_column(
        Enum(PersonType, name="PersonType", native_enum=True), nullable=False
    )
    status: Mapped[AttendanceStatus] = mapped_column(
        Enum(AttendanceStatus, name="AttendanceStatus", native_enum=True),
        default=AttendanceStatus.PRESENT,
        nullable=False,
    )
    scannedAt: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    schoolId: Mapped[str] = mapped_column(String(30), ForeignKey("School.id"), nullable=False)
    studentId: Mapped[str | None] = mapped_column(
        String(30), ForeignKey("Student.id"), nullable=True
    )
    teacherId: Mapped[str | None] = mapped_column(
        String(30), ForeignKey("Teacher.id"), nullable=True
    )

    school: Mapped["School"] = relationship(back_populates="attendances", lazy="raise")
    student: Mapped["Student | None"] = relationship(
        back_populates="attendances", lazy="raise"
    )
    teacher: Mapped["Teacher | None"] = relationship(
        back_populates="attendances", lazy="raise"
    )
