from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, Enum, ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.shared.base import Base, TimestampMixin, cuid_pk
from app.shared.enums import Gender, ValidationStatus

if TYPE_CHECKING:
    from app.modules.academics.models import (
        Assessment,
        Grade,
        ParentCommunication,
        ReportCard,
        StudentParent,
    )
    from app.modules.attendance.models import AttendanceRecord, QrCredential
    from app.modules.auth.models import User
    from app.modules.library.models import LibraryLoan
    from app.modules.schools.models import ClassRoom, School


class Student(Base, TimestampMixin):
    __tablename__ = "Student"
    __table_args__ = (Index("ix_Student_schoolId", "schoolId"),)

    id: Mapped[str] = cuid_pk()
    uniqueCode: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    firstName: Mapped[str] = mapped_column(String, nullable=False)
    lastName: Mapped[str] = mapped_column(String, nullable=False)
    birthDate: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    gender: Mapped[Gender] = mapped_column(
        Enum(Gender, name="Gender", native_enum=True), nullable=False
    )
    photoUrl: Mapped[str | None] = mapped_column(String, nullable=True)
    guardianName: Mapped[str | None] = mapped_column(String, nullable=True)
    guardianPhone: Mapped[str | None] = mapped_column(String, nullable=True)
    schoolId: Mapped[str] = mapped_column(String(30), ForeignKey("School.id"), nullable=False)
    classRoomId: Mapped[str | None] = mapped_column(
        String(30), ForeignKey("ClassRoom.id"), nullable=True
    )

    school: Mapped["School"] = relationship(back_populates="students", lazy="raise")
    classRoom: Mapped["ClassRoom | None"] = relationship(back_populates="students", lazy="raise")
    qrCredential: Mapped["QrCredential | None"] = relationship(
        back_populates="student", uselist=False, lazy="raise"
    )
    attendances: Mapped[list["AttendanceRecord"]] = relationship(
        back_populates="student", lazy="raise"
    )
    transferHistory: Mapped[list["StudentTransfer"]] = relationship(
        back_populates="student", lazy="raise"
    )
    parents: Mapped[list["StudentParent"]] = relationship(
        back_populates="student", lazy="raise"
    )
    grades: Mapped[list["Grade"]] = relationship(back_populates="student", lazy="raise")
    reportCards: Mapped[list["ReportCard"]] = relationship(
        back_populates="student", lazy="raise"
    )
    communications: Mapped[list["ParentCommunication"]] = relationship(
        back_populates="student", lazy="raise"
    )
    libraryLoans: Mapped[list["LibraryLoan"]] = relationship(
        back_populates="student", lazy="raise"
    )


class Teacher(Base, TimestampMixin):
    __tablename__ = "Teacher"
    __table_args__ = (
        Index("ix_Teacher_schoolId", "schoolId"),
        Index("ix_Teacher_status", "status"),
    )

    id: Mapped[str] = cuid_pk()
    uniqueCode: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    firstName: Mapped[str] = mapped_column(String, nullable=False)
    lastName: Mapped[str] = mapped_column(String, nullable=False)
    birthDate: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    gender: Mapped[Gender] = mapped_column(
        Enum(Gender, name="Gender", native_enum=True), nullable=False
    )
    photoUrl: Mapped[str | None] = mapped_column(String, nullable=True)
    phone: Mapped[str | None] = mapped_column(String, nullable=True)
    subject: Mapped[str | None] = mapped_column(String, nullable=True)
    diploma: Mapped[str | None] = mapped_column(String, nullable=True)
    schoolId: Mapped[str] = mapped_column(String(30), ForeignKey("School.id"), nullable=False)

    status: Mapped[ValidationStatus] = mapped_column(
        Enum(ValidationStatus, name="ValidationStatus", native_enum=True),
        default=ValidationStatus.APPROVED,
        nullable=False,
    )
    rejectionReason: Mapped[str | None] = mapped_column(String, nullable=True)
    createdById: Mapped[str | None] = mapped_column(String(30), nullable=True)
    approvedById: Mapped[str | None] = mapped_column(String(30), nullable=True)
    approvedAt: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    school: Mapped["School"] = relationship(back_populates="teachers", lazy="raise")
    classes: Mapped[list["ClassRoom"]] = relationship(
        secondary="_ClassRoomTeacher", back_populates="teachers", lazy="raise"
    )
    qrCredential: Mapped["QrCredential | None"] = relationship(
        back_populates="teacher", uselist=False, lazy="raise"
    )
    attendances: Mapped[list["AttendanceRecord"]] = relationship(
        back_populates="teacher", lazy="raise"
    )
    assessments: Mapped[list["Assessment"]] = relationship(
        back_populates="teacher", lazy="raise"
    )


class StudentTransfer(Base):
    __tablename__ = "StudentTransfer"
    __table_args__ = (
        Index("ix_StudentTransfer_studentId_transferredAt", "studentId", "transferredAt"),
        Index("ix_StudentTransfer_fromSchoolId", "fromSchoolId"),
        Index("ix_StudentTransfer_toSchoolId", "toSchoolId"),
    )

    id: Mapped[str] = cuid_pk()
    studentId: Mapped[str] = mapped_column(
        String(30), ForeignKey("Student.id"), nullable=False
    )
    fromSchoolId: Mapped[str] = mapped_column(
        String(30), ForeignKey("School.id"), nullable=False
    )
    toSchoolId: Mapped[str] = mapped_column(
        String(30), ForeignKey("School.id"), nullable=False
    )
    fromClassRoomId: Mapped[str | None] = mapped_column(
        String(30), ForeignKey("ClassRoom.id"), nullable=True
    )
    toClassRoomId: Mapped[str | None] = mapped_column(
        String(30), ForeignKey("ClassRoom.id"), nullable=True
    )
    reason: Mapped[str | None] = mapped_column(String, nullable=True)
    actorId: Mapped[str | None] = mapped_column(
        String(30), ForeignKey("User.id"), nullable=True
    )
    transferredAt: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    student: Mapped["Student"] = relationship(back_populates="transferHistory", lazy="raise")
    fromSchool: Mapped["School"] = relationship(
        back_populates="transfersOut", foreign_keys=[fromSchoolId], lazy="raise"
    )
    toSchool: Mapped["School"] = relationship(
        back_populates="transfersIn", foreign_keys=[toSchoolId], lazy="raise"
    )
    fromClassRoom: Mapped["ClassRoom | None"] = relationship(
        back_populates="transfersOut", foreign_keys=[fromClassRoomId], lazy="raise"
    )
    toClassRoom: Mapped["ClassRoom | None"] = relationship(
        back_populates="transfersIn", foreign_keys=[toClassRoomId], lazy="raise"
    )
    actor: Mapped["User | None"] = relationship(
        back_populates="studentTransfers", lazy="raise"
    )
