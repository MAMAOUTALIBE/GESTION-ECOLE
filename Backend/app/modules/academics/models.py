from datetime import datetime
from typing import TYPE_CHECKING

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

from app.shared.base import Base, CreatedAtMixin, TimestampMixin, cuid_pk
from app.shared.enums import (
    AcademicPeriodType,
    AcademicValidationStatus,
    AssessmentType,
    CommunicationChannel,
    CommunicationStatus,
    ParentRelationType,
)

if TYPE_CHECKING:
    from app.modules.census.models import Student, Teacher
    from app.modules.library.models import LibraryInventory
    from app.modules.schools.models import ClassRoom


class Parent(Base, TimestampMixin):
    __tablename__ = "Parent"
    __table_args__ = (Index("ix_Parent_lastName_firstName", "lastName", "firstName"),)

    id: Mapped[str] = cuid_pk()
    firstName: Mapped[str] = mapped_column(String, nullable=False)
    lastName: Mapped[str] = mapped_column(String, nullable=False)
    phone: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    email: Mapped[str | None] = mapped_column(String, unique=True, nullable=True)
    profession: Mapped[str | None] = mapped_column(String, nullable=True)
    address: Mapped[str | None] = mapped_column(String, nullable=True)
    preferredLanguage: Mapped[str | None] = mapped_column(String, nullable=True)
    otpVerifiedAt: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    students: Mapped[list["StudentParent"]] = relationship(
        back_populates="parent", lazy="raise"
    )
    communications: Mapped[list["ParentCommunication"]] = relationship(
        back_populates="parent", lazy="raise"
    )


class StudentParent(Base, TimestampMixin):
    __tablename__ = "StudentParent"
    __table_args__ = (
        UniqueConstraint(
            "studentId", "parentId", "relation", name="uq_StudentParent_studentId_parentId_rel"
        ),
        Index("ix_StudentParent_studentId", "studentId"),
        Index("ix_StudentParent_parentId", "parentId"),
    )

    id: Mapped[str] = cuid_pk()
    studentId: Mapped[str] = mapped_column(
        String(30), ForeignKey("Student.id"), nullable=False
    )
    parentId: Mapped[str] = mapped_column(
        String(30), ForeignKey("Parent.id"), nullable=False
    )
    relation: Mapped[ParentRelationType] = mapped_column(
        Enum(ParentRelationType, name="ParentRelationType", native_enum=True), nullable=False
    )
    isPrimary: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    isEmergencyContact: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    student: Mapped["Student"] = relationship(back_populates="parents", lazy="raise")
    parent: Mapped["Parent"] = relationship(back_populates="students", lazy="raise")


class SchoolYear(Base, TimestampMixin):
    __tablename__ = "SchoolYear"
    __table_args__ = (Index("ix_SchoolYear_isActive", "isActive"),)

    id: Mapped[str] = cuid_pk()
    name: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    startDate: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    endDate: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    periodType: Mapped[AcademicPeriodType] = mapped_column(
        Enum(AcademicPeriodType, name="AcademicPeriodType", native_enum=True),
        default=AcademicPeriodType.TRIMESTER,
        nullable=False,
    )
    isActive: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    periods: Mapped[list["AcademicPeriod"]] = relationship(
        back_populates="schoolYear", lazy="raise"
    )
    classes: Mapped[list["ClassRoom"]] = relationship(
        back_populates="academicYear", lazy="raise"
    )
    assessments: Mapped[list["Assessment"]] = relationship(
        back_populates="schoolYear", lazy="raise"
    )
    grades: Mapped[list["Grade"]] = relationship(back_populates="schoolYear", lazy="raise")
    reportCards: Mapped[list["ReportCard"]] = relationship(
        back_populates="schoolYear", lazy="raise"
    )


class AcademicPeriod(Base, TimestampMixin):
    __tablename__ = "AcademicPeriod"
    __table_args__ = (
        UniqueConstraint("schoolYearId", "name", name="uq_AcademicPeriod_schoolYearId_name"),
        Index("ix_AcademicPeriod_schoolYearId_order", "schoolYearId", "order"),
    )

    id: Mapped[str] = cuid_pk()
    name: Mapped[str] = mapped_column(String, nullable=False)
    type: Mapped[AcademicPeriodType] = mapped_column(
        Enum(AcademicPeriodType, name="AcademicPeriodType", native_enum=True), nullable=False
    )
    order: Mapped[int] = mapped_column(Integer, nullable=False)
    startDate: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    endDate: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    schoolYearId: Mapped[str] = mapped_column(
        String(30), ForeignKey("SchoolYear.id"), nullable=False
    )

    schoolYear: Mapped["SchoolYear"] = relationship(back_populates="periods", lazy="raise")
    assessments: Mapped[list["Assessment"]] = relationship(
        back_populates="period", lazy="raise"
    )
    grades: Mapped[list["Grade"]] = relationship(back_populates="period", lazy="raise")
    reportCards: Mapped[list["ReportCard"]] = relationship(
        back_populates="period", lazy="raise"
    )


class Subject(Base, TimestampMixin):
    __tablename__ = "Subject"
    __table_args__ = (Index("ix_Subject_level", "level"),)

    id: Mapped[str] = cuid_pk()
    code: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False)
    level: Mapped[str | None] = mapped_column(String, nullable=True)
    coefficient: Mapped[float] = mapped_column(Float, default=1.0, nullable=False)

    assessments: Mapped[list["Assessment"]] = relationship(
        back_populates="subject", lazy="raise"
    )
    grades: Mapped[list["Grade"]] = relationship(back_populates="subject", lazy="raise")
    libraryInventory: Mapped[list["LibraryInventory"]] = relationship(
        back_populates="subject", lazy="raise"
    )


class Assessment(Base, TimestampMixin):
    __tablename__ = "Assessment"
    __table_args__ = (
        Index("ix_Assessment_classRoomId_periodId", "classRoomId", "periodId"),
        Index("ix_Assessment_subjectId", "subjectId"),
        Index("ix_Assessment_teacherId", "teacherId"),
    )

    id: Mapped[str] = cuid_pk()
    title: Mapped[str] = mapped_column(String, nullable=False)
    type: Mapped[AssessmentType] = mapped_column(
        Enum(AssessmentType, name="AssessmentType", native_enum=True), nullable=False
    )
    coefficient: Mapped[float] = mapped_column(Float, default=1.0, nullable=False)
    maxScore: Mapped[float] = mapped_column(Float, default=20.0, nullable=False)
    assessedAt: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    schoolYearId: Mapped[str] = mapped_column(
        String(30), ForeignKey("SchoolYear.id"), nullable=False
    )
    periodId: Mapped[str] = mapped_column(
        String(30), ForeignKey("AcademicPeriod.id"), nullable=False
    )
    subjectId: Mapped[str] = mapped_column(
        String(30), ForeignKey("Subject.id"), nullable=False
    )
    classRoomId: Mapped[str] = mapped_column(
        String(30), ForeignKey("ClassRoom.id"), nullable=False
    )
    teacherId: Mapped[str | None] = mapped_column(
        String(30), ForeignKey("Teacher.id"), nullable=True
    )
    actorId: Mapped[str | None] = mapped_column(String(30), nullable=True)
    status: Mapped[AcademicValidationStatus] = mapped_column(
        Enum(AcademicValidationStatus, name="AcademicValidationStatus", native_enum=True),
        default=AcademicValidationStatus.DRAFT,
        nullable=False,
    )

    schoolYear: Mapped["SchoolYear"] = relationship(
        back_populates="assessments", lazy="raise"
    )
    period: Mapped["AcademicPeriod"] = relationship(
        back_populates="assessments", lazy="raise"
    )
    subject: Mapped["Subject"] = relationship(back_populates="assessments", lazy="raise")
    classRoom: Mapped["ClassRoom"] = relationship(
        back_populates="assessments", lazy="raise"
    )
    teacher: Mapped["Teacher | None"] = relationship(
        back_populates="assessments", lazy="raise"
    )
    grades: Mapped[list["Grade"]] = relationship(back_populates="assessment", lazy="raise")


class Grade(Base):
    __tablename__ = "Grade"
    __table_args__ = (
        UniqueConstraint("assessmentId", "studentId", name="uq_Grade_assessmentId_studentId"),
        Index("ix_Grade_studentId_periodId", "studentId", "periodId"),
        Index("ix_Grade_classRoomId_periodId", "classRoomId", "periodId"),
    )

    id: Mapped[str] = cuid_pk()
    assessmentId: Mapped[str] = mapped_column(
        String(30), ForeignKey("Assessment.id"), nullable=False
    )
    studentId: Mapped[str] = mapped_column(
        String(30), ForeignKey("Student.id"), nullable=False
    )
    schoolYearId: Mapped[str] = mapped_column(
        String(30), ForeignKey("SchoolYear.id"), nullable=False
    )
    periodId: Mapped[str] = mapped_column(
        String(30), ForeignKey("AcademicPeriod.id"), nullable=False
    )
    subjectId: Mapped[str] = mapped_column(
        String(30), ForeignKey("Subject.id"), nullable=False
    )
    classRoomId: Mapped[str | None] = mapped_column(
        String(30), ForeignKey("ClassRoom.id"), nullable=True
    )
    score: Mapped[float] = mapped_column(Float, nullable=False)
    appreciation: Mapped[str | None] = mapped_column(String, nullable=True)
    status: Mapped[AcademicValidationStatus] = mapped_column(
        Enum(AcademicValidationStatus, name="AcademicValidationStatus", native_enum=True),
        default=AcademicValidationStatus.DRAFT,
        nullable=False,
    )
    recordedAt: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updatedAt: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    assessment: Mapped["Assessment"] = relationship(back_populates="grades", lazy="raise")
    student: Mapped["Student"] = relationship(back_populates="grades", lazy="raise")
    schoolYear: Mapped["SchoolYear"] = relationship(back_populates="grades", lazy="raise")
    period: Mapped["AcademicPeriod"] = relationship(back_populates="grades", lazy="raise")
    subject: Mapped["Subject"] = relationship(back_populates="grades", lazy="raise")
    classRoom: Mapped["ClassRoom | None"] = relationship(
        back_populates="grades", lazy="raise"
    )


class ReportCard(Base, TimestampMixin):
    __tablename__ = "ReportCard"
    __table_args__ = (
        UniqueConstraint("studentId", "periodId", name="uq_ReportCard_studentId_periodId"),
        Index("ix_ReportCard_classRoomId_periodId", "classRoomId", "periodId"),
    )

    id: Mapped[str] = cuid_pk()
    studentId: Mapped[str] = mapped_column(
        String(30), ForeignKey("Student.id"), nullable=False
    )
    classRoomId: Mapped[str | None] = mapped_column(
        String(30), ForeignKey("ClassRoom.id"), nullable=True
    )
    schoolYearId: Mapped[str] = mapped_column(
        String(30), ForeignKey("SchoolYear.id"), nullable=False
    )
    periodId: Mapped[str] = mapped_column(
        String(30), ForeignKey("AcademicPeriod.id"), nullable=False
    )
    average: Mapped[float | None] = mapped_column(Float, nullable=True)
    rank: Mapped[int | None] = mapped_column(Integer, nullable=True)
    totalStudents: Mapped[int | None] = mapped_column(Integer, nullable=True)
    teacherComment: Mapped[str | None] = mapped_column(String, nullable=True)
    directorComment: Mapped[str | None] = mapped_column(String, nullable=True)
    verificationCode: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    status: Mapped[AcademicValidationStatus] = mapped_column(
        Enum(AcademicValidationStatus, name="AcademicValidationStatus", native_enum=True),
        default=AcademicValidationStatus.DRAFT,
        nullable=False,
    )
    issuedAt: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    student: Mapped["Student"] = relationship(back_populates="reportCards", lazy="raise")
    classRoom: Mapped["ClassRoom | None"] = relationship(
        back_populates="reportCards", lazy="raise"
    )
    schoolYear: Mapped["SchoolYear"] = relationship(
        back_populates="reportCards", lazy="raise"
    )
    period: Mapped["AcademicPeriod"] = relationship(
        back_populates="reportCards", lazy="raise"
    )


class ParentCommunication(Base, CreatedAtMixin):
    __tablename__ = "ParentCommunication"
    __table_args__ = (
        Index("ix_ParentCommunication_parentId_createdAt", "parentId", "createdAt"),
        Index("ix_ParentCommunication_studentId_createdAt", "studentId", "createdAt"),
    )

    id: Mapped[str] = cuid_pk()
    parentId: Mapped[str] = mapped_column(
        String(30), ForeignKey("Parent.id"), nullable=False
    )
    studentId: Mapped[str | None] = mapped_column(
        String(30), ForeignKey("Student.id"), nullable=True
    )
    channel: Mapped[CommunicationChannel] = mapped_column(
        Enum(CommunicationChannel, name="CommunicationChannel", native_enum=True),
        nullable=False,
    )
    status: Mapped[CommunicationStatus] = mapped_column(
        Enum(CommunicationStatus, name="CommunicationStatus", native_enum=True),
        default=CommunicationStatus.DRAFT,
        nullable=False,
    )
    subject: Mapped[str | None] = mapped_column(String, nullable=True)
    message: Mapped[str] = mapped_column(String, nullable=False)
    sentAt: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    parent: Mapped["Parent"] = relationship(back_populates="communications", lazy="raise")
    student: Mapped["Student | None"] = relationship(
        back_populates="communications", lazy="raise"
    )
