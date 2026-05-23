"""phase 0 — initial schema (mirror of Prisma schema)

Revision ID: 0001_initial
Revises:
Create Date: 2026-05-05

Crée les 15 enums, 25 tables et la table de jointure _ClassRoomTeacher.
Active aussi l'extension PostGIS pour préparer le module cartography (Phase 3).
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001_initial"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# --------------------------------------------------------------------------
# Enum definitions
# --------------------------------------------------------------------------
USER_ROLE = ("NATIONAL_ADMIN", "MINISTRY_ADMIN", "REGIONAL_ADMIN", "INSPECTOR",
             "PREFECTURE_ADMIN", "SUB_PREFECTURE_ADMIN", "SCHOOL_DIRECTOR",
             "TEACHER", "CENSUS_AGENT")
VALIDATION_STATUS = ("DRAFT", "SUBMITTED", "APPROVED", "REJECTED")
VALIDATION_ENTITY_TYPE = ("PREFECTURE", "SUB_PREFECTURE", "SCHOOL", "TEACHER")
NOTIFICATION_TYPE = ("VALIDATION_REQUEST", "VALIDATION_APPROVED", "VALIDATION_REJECTED",
                     "CORRECTION_REQUIRED", "SYSTEM_ALERT", "MESSAGE")
PERSON_TYPE = ("STUDENT", "TEACHER")
GENDER = ("FEMALE", "MALE", "OTHER")
ATTENDANCE_STATUS = ("PRESENT", "LATE", "ABSENT")
PARENT_RELATION_TYPE = ("FATHER", "MOTHER", "LEGAL_GUARDIAN", "EMERGENCY_CONTACT", "OTHER")
ACADEMIC_PERIOD_TYPE = ("TRIMESTER", "SEMESTER")
ASSESSMENT_TYPE = ("QUIZ", "HOMEWORK", "COMPOSITION", "NATIONAL_EXAM", "ORAL", "PROJECT", "OTHER")
ACADEMIC_VALIDATION_STATUS = ("DRAFT", "SUBMITTED", "VALIDATED", "REJECTED")
COMMUNICATION_CHANNEL = ("SMS", "WHATSAPP", "EMAIL", "PHONE", "IN_APP")
COMMUNICATION_STATUS = ("DRAFT", "SENT", "FAILED", "READ")
LIBRARY_STOCK_STATUS = ("SUFFICIENT", "WATCH", "SHORTAGE")
LIBRARY_LOAN_STATUS = ("BORROWED", "LATE", "RETURNED")


def _enum(name: str, values: tuple[str, ...]) -> postgresql.ENUM:
    return postgresql.ENUM(*values, name=name, create_type=False)


def upgrade() -> None:
    # PostGIS for the cartography module (Phase 3). Made optional in Phase 0:
    # if the PostGIS .so isn't installed at the OS level, we just log and skip.
    # The Phase 3 migration will require it.
    op.execute(
        """
        DO $$
        BEGIN
            CREATE EXTENSION IF NOT EXISTS postgis;
            RAISE NOTICE 'PostGIS extension enabled.';
        EXCEPTION WHEN OTHERS THEN
            RAISE NOTICE 'PostGIS not installed at OS level — skipped (will be required in Phase 3).';
        END $$;
        """
    )

    # --- Enums (created upfront, reused as type=False below) ---
    bind = op.get_bind()
    for name, values in [
        ("UserRole", USER_ROLE),
        ("ValidationStatus", VALIDATION_STATUS),
        ("ValidationEntityType", VALIDATION_ENTITY_TYPE),
        ("NotificationType", NOTIFICATION_TYPE),
        ("PersonType", PERSON_TYPE),
        ("Gender", GENDER),
        ("AttendanceStatus", ATTENDANCE_STATUS),
        ("ParentRelationType", PARENT_RELATION_TYPE),
        ("AcademicPeriodType", ACADEMIC_PERIOD_TYPE),
        ("AssessmentType", ASSESSMENT_TYPE),
        ("AcademicValidationStatus", ACADEMIC_VALIDATION_STATUS),
        ("CommunicationChannel", COMMUNICATION_CHANNEL),
        ("CommunicationStatus", COMMUNICATION_STATUS),
        ("LibraryStockStatus", LIBRARY_STOCK_STATUS),
        ("LibraryLoanStatus", LIBRARY_LOAN_STATUS),
    ]:
        postgresql.ENUM(*values, name=name).create(bind, checkfirst=True)

    timestamps = (
        sa.Column("createdAt", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updatedAt", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    # --- Region ---
    op.create_table(
        "Region",
        sa.Column("id", sa.String(30), primary_key=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("code", sa.String(), nullable=False, unique=True),
        *timestamps,
    )

    # --- Prefecture ---
    op.create_table(
        "Prefecture",
        sa.Column("id", sa.String(30), primary_key=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("code", sa.String(), nullable=False, unique=True),
        sa.Column("regionId", sa.String(30), sa.ForeignKey("Region.id"), nullable=False),
        sa.Column("status", _enum("ValidationStatus", VALIDATION_STATUS), nullable=False, server_default="APPROVED"),
        sa.Column("rejectionReason", sa.String(), nullable=True),
        sa.Column("createdById", sa.String(30), nullable=True),
        sa.Column("approvedById", sa.String(30), nullable=True),
        sa.Column("approvedAt", sa.DateTime(timezone=True), nullable=True),
        *timestamps,
    )
    op.create_index("ix_Prefecture_regionId_status", "Prefecture", ["regionId", "status"])

    # --- SubPrefecture ---
    op.create_table(
        "SubPrefecture",
        sa.Column("id", sa.String(30), primary_key=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("code", sa.String(), nullable=False, unique=True),
        sa.Column("regionId", sa.String(30), nullable=False),
        sa.Column("prefectureId", sa.String(30), sa.ForeignKey("Prefecture.id"), nullable=False),
        sa.Column("status", _enum("ValidationStatus", VALIDATION_STATUS), nullable=False, server_default="APPROVED"),
        sa.Column("rejectionReason", sa.String(), nullable=True),
        sa.Column("createdById", sa.String(30), nullable=True),
        sa.Column("approvedById", sa.String(30), nullable=True),
        sa.Column("approvedAt", sa.DateTime(timezone=True), nullable=True),
        *timestamps,
    )
    op.create_index("ix_SubPrefecture_regionId_status", "SubPrefecture", ["regionId", "status"])
    op.create_index("ix_SubPrefecture_prefectureId_status", "SubPrefecture", ["prefectureId", "status"])

    # --- School ---
    op.create_table(
        "School",
        sa.Column("id", sa.String(30), primary_key=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("code", sa.String(), nullable=False, unique=True),
        sa.Column("regionId", sa.String(30), sa.ForeignKey("Region.id"), nullable=False),
        sa.Column("prefectureId", sa.String(30), sa.ForeignKey("Prefecture.id"), nullable=True),
        sa.Column("subPrefectureId", sa.String(30), sa.ForeignKey("SubPrefecture.id"), nullable=True),
        sa.Column("address", sa.String(), nullable=True),
        sa.Column("prefecture", sa.String(), nullable=True),
        sa.Column("commune", sa.String(), nullable=True),
        sa.Column("type", sa.String(), nullable=True),
        sa.Column("phone", sa.String(), nullable=True),
        sa.Column("latitude", sa.Float(), nullable=True),
        sa.Column("longitude", sa.Float(), nullable=True),
        sa.Column("status", _enum("ValidationStatus", VALIDATION_STATUS), nullable=False, server_default="APPROVED"),
        sa.Column("rejectionReason", sa.String(), nullable=True),
        sa.Column("createdById", sa.String(30), nullable=True),
        sa.Column("approvedById", sa.String(30), nullable=True),
        sa.Column("approvedAt", sa.DateTime(timezone=True), nullable=True),
        *timestamps,
    )
    op.create_index("ix_School_regionId_status", "School", ["regionId", "status"])
    op.create_index("ix_School_prefectureId_status", "School", ["prefectureId", "status"])
    op.create_index("ix_School_subPrefectureId_status", "School", ["subPrefectureId", "status"])

    # --- SchoolYear ---
    op.create_table(
        "SchoolYear",
        sa.Column("id", sa.String(30), primary_key=True),
        sa.Column("name", sa.String(), nullable=False, unique=True),
        sa.Column("startDate", sa.DateTime(timezone=True), nullable=False),
        sa.Column("endDate", sa.DateTime(timezone=True), nullable=False),
        sa.Column("periodType", _enum("AcademicPeriodType", ACADEMIC_PERIOD_TYPE), nullable=False, server_default="TRIMESTER"),
        sa.Column("isActive", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        *timestamps,
    )
    op.create_index("ix_SchoolYear_isActive", "SchoolYear", ["isActive"])

    # --- User ---
    op.create_table(
        "User",
        sa.Column("id", sa.String(30), primary_key=True),
        sa.Column("email", sa.String(), nullable=False, unique=True),
        sa.Column("passwordHash", sa.String(), nullable=False),
        sa.Column("fullName", sa.String(), nullable=False),
        sa.Column("role", _enum("UserRole", USER_ROLE), nullable=False),
        sa.Column("regionId", sa.String(30), sa.ForeignKey("Region.id"), nullable=True),
        sa.Column("prefectureId", sa.String(30), sa.ForeignKey("Prefecture.id"), nullable=True),
        sa.Column("subPrefectureId", sa.String(30), sa.ForeignKey("SubPrefecture.id"), nullable=True),
        sa.Column("schoolId", sa.String(30), sa.ForeignKey("School.id"), nullable=True),
        sa.Column("isActive", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        *timestamps,
    )

    # --- ClassRoom ---
    op.create_table(
        "ClassRoom",
        sa.Column("id", sa.String(30), primary_key=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("level", sa.String(), nullable=True),
        sa.Column("maxStudents", sa.Integer(), nullable=True),
        sa.Column("schoolYear", sa.String(), nullable=True),
        sa.Column("schoolYearId", sa.String(30), sa.ForeignKey("SchoolYear.id"), nullable=True),
        sa.Column("schoolId", sa.String(30), sa.ForeignKey("School.id"), nullable=False),
        *timestamps,
        sa.UniqueConstraint("schoolId", "name", name="uq_ClassRoom_schoolId_name"),
    )

    # --- Teacher ---
    op.create_table(
        "Teacher",
        sa.Column("id", sa.String(30), primary_key=True),
        sa.Column("uniqueCode", sa.String(), nullable=False, unique=True),
        sa.Column("firstName", sa.String(), nullable=False),
        sa.Column("lastName", sa.String(), nullable=False),
        sa.Column("birthDate", sa.DateTime(timezone=True), nullable=True),
        sa.Column("gender", _enum("Gender", GENDER), nullable=False),
        sa.Column("photoUrl", sa.String(), nullable=True),
        sa.Column("phone", sa.String(), nullable=True),
        sa.Column("subject", sa.String(), nullable=True),
        sa.Column("diploma", sa.String(), nullable=True),
        sa.Column("schoolId", sa.String(30), sa.ForeignKey("School.id"), nullable=False),
        sa.Column("status", _enum("ValidationStatus", VALIDATION_STATUS), nullable=False, server_default="APPROVED"),
        sa.Column("rejectionReason", sa.String(), nullable=True),
        sa.Column("createdById", sa.String(30), nullable=True),
        sa.Column("approvedById", sa.String(30), nullable=True),
        sa.Column("approvedAt", sa.DateTime(timezone=True), nullable=True),
        *timestamps,
    )
    op.create_index("ix_Teacher_schoolId", "Teacher", ["schoolId"])
    op.create_index("ix_Teacher_status", "Teacher", ["status"])

    # --- Student ---
    op.create_table(
        "Student",
        sa.Column("id", sa.String(30), primary_key=True),
        sa.Column("uniqueCode", sa.String(), nullable=False, unique=True),
        sa.Column("firstName", sa.String(), nullable=False),
        sa.Column("lastName", sa.String(), nullable=False),
        sa.Column("birthDate", sa.DateTime(timezone=True), nullable=True),
        sa.Column("gender", _enum("Gender", GENDER), nullable=False),
        sa.Column("photoUrl", sa.String(), nullable=True),
        sa.Column("guardianName", sa.String(), nullable=True),
        sa.Column("guardianPhone", sa.String(), nullable=True),
        sa.Column("schoolId", sa.String(30), sa.ForeignKey("School.id"), nullable=False),
        sa.Column("classRoomId", sa.String(30), sa.ForeignKey("ClassRoom.id"), nullable=True),
        *timestamps,
    )
    op.create_index("ix_Student_schoolId", "Student", ["schoolId"])

    # --- _ClassRoomTeacher (M2M) ---
    op.create_table(
        "_ClassRoomTeacher",
        sa.Column("A", sa.String(30), sa.ForeignKey("ClassRoom.id"), primary_key=True),
        sa.Column("B", sa.String(30), sa.ForeignKey("Teacher.id"), primary_key=True),
    )

    # --- StudentTransfer ---
    op.create_table(
        "StudentTransfer",
        sa.Column("id", sa.String(30), primary_key=True),
        sa.Column("studentId", sa.String(30), sa.ForeignKey("Student.id"), nullable=False),
        sa.Column("fromSchoolId", sa.String(30), sa.ForeignKey("School.id"), nullable=False),
        sa.Column("toSchoolId", sa.String(30), sa.ForeignKey("School.id"), nullable=False),
        sa.Column("fromClassRoomId", sa.String(30), sa.ForeignKey("ClassRoom.id"), nullable=True),
        sa.Column("toClassRoomId", sa.String(30), sa.ForeignKey("ClassRoom.id"), nullable=True),
        sa.Column("reason", sa.String(), nullable=True),
        sa.Column("actorId", sa.String(30), sa.ForeignKey("User.id"), nullable=True),
        sa.Column("transferredAt", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_StudentTransfer_studentId_transferredAt", "StudentTransfer", ["studentId", "transferredAt"])
    op.create_index("ix_StudentTransfer_fromSchoolId", "StudentTransfer", ["fromSchoolId"])
    op.create_index("ix_StudentTransfer_toSchoolId", "StudentTransfer", ["toSchoolId"])

    # --- QrCredential ---
    op.create_table(
        "QrCredential",
        sa.Column("id", sa.String(30), primary_key=True),
        sa.Column("token", sa.String(), nullable=False, unique=True),
        sa.Column("payload", sa.String(), nullable=False),
        sa.Column("personType", _enum("PersonType", PERSON_TYPE), nullable=False),
        sa.Column("studentId", sa.String(30), sa.ForeignKey("Student.id"), nullable=True, unique=True),
        sa.Column("teacherId", sa.String(30), sa.ForeignKey("Teacher.id"), nullable=True, unique=True),
        sa.Column("createdAt", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("revokedAt", sa.DateTime(timezone=True), nullable=True),
    )

    # --- AttendanceRecord ---
    op.create_table(
        "AttendanceRecord",
        sa.Column("id", sa.String(30), primary_key=True),
        sa.Column("personType", _enum("PersonType", PERSON_TYPE), nullable=False),
        sa.Column("status", _enum("AttendanceStatus", ATTENDANCE_STATUS), nullable=False, server_default="PRESENT"),
        sa.Column("scannedAt", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("schoolId", sa.String(30), sa.ForeignKey("School.id"), nullable=False),
        sa.Column("studentId", sa.String(30), sa.ForeignKey("Student.id"), nullable=True),
        sa.Column("teacherId", sa.String(30), sa.ForeignKey("Teacher.id"), nullable=True),
    )
    op.create_index("ix_AttendanceRecord_schoolId_scannedAt", "AttendanceRecord", ["schoolId", "scannedAt"])
    op.create_index("ix_AttendanceRecord_studentId_scannedAt", "AttendanceRecord", ["studentId", "scannedAt"])
    op.create_index("ix_AttendanceRecord_teacherId_scannedAt", "AttendanceRecord", ["teacherId", "scannedAt"])

    # --- Subject ---
    op.create_table(
        "Subject",
        sa.Column("id", sa.String(30), primary_key=True),
        sa.Column("code", sa.String(), nullable=False, unique=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("level", sa.String(), nullable=True),
        sa.Column("coefficient", sa.Float(), nullable=False, server_default="1"),
        *timestamps,
    )
    op.create_index("ix_Subject_level", "Subject", ["level"])

    # --- AcademicPeriod ---
    op.create_table(
        "AcademicPeriod",
        sa.Column("id", sa.String(30), primary_key=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("type", _enum("AcademicPeriodType", ACADEMIC_PERIOD_TYPE), nullable=False),
        sa.Column("order", sa.Integer(), nullable=False),
        sa.Column("startDate", sa.DateTime(timezone=True), nullable=True),
        sa.Column("endDate", sa.DateTime(timezone=True), nullable=True),
        sa.Column("schoolYearId", sa.String(30), sa.ForeignKey("SchoolYear.id"), nullable=False),
        *timestamps,
        sa.UniqueConstraint("schoolYearId", "name", name="uq_AcademicPeriod_schoolYearId_name"),
    )
    op.create_index("ix_AcademicPeriod_schoolYearId_order", "AcademicPeriod", ["schoolYearId", "order"])

    # --- Assessment ---
    op.create_table(
        "Assessment",
        sa.Column("id", sa.String(30), primary_key=True),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("type", _enum("AssessmentType", ASSESSMENT_TYPE), nullable=False),
        sa.Column("coefficient", sa.Float(), nullable=False, server_default="1"),
        sa.Column("maxScore", sa.Float(), nullable=False, server_default="20"),
        sa.Column("assessedAt", sa.DateTime(timezone=True), nullable=True),
        sa.Column("schoolYearId", sa.String(30), sa.ForeignKey("SchoolYear.id"), nullable=False),
        sa.Column("periodId", sa.String(30), sa.ForeignKey("AcademicPeriod.id"), nullable=False),
        sa.Column("subjectId", sa.String(30), sa.ForeignKey("Subject.id"), nullable=False),
        sa.Column("classRoomId", sa.String(30), sa.ForeignKey("ClassRoom.id"), nullable=False),
        sa.Column("teacherId", sa.String(30), sa.ForeignKey("Teacher.id"), nullable=True),
        sa.Column("actorId", sa.String(30), nullable=True),
        sa.Column("status", _enum("AcademicValidationStatus", ACADEMIC_VALIDATION_STATUS), nullable=False, server_default="DRAFT"),
        *timestamps,
    )
    op.create_index("ix_Assessment_classRoomId_periodId", "Assessment", ["classRoomId", "periodId"])
    op.create_index("ix_Assessment_subjectId", "Assessment", ["subjectId"])
    op.create_index("ix_Assessment_teacherId", "Assessment", ["teacherId"])

    # --- Grade ---
    op.create_table(
        "Grade",
        sa.Column("id", sa.String(30), primary_key=True),
        sa.Column("assessmentId", sa.String(30), sa.ForeignKey("Assessment.id"), nullable=False),
        sa.Column("studentId", sa.String(30), sa.ForeignKey("Student.id"), nullable=False),
        sa.Column("schoolYearId", sa.String(30), sa.ForeignKey("SchoolYear.id"), nullable=False),
        sa.Column("periodId", sa.String(30), sa.ForeignKey("AcademicPeriod.id"), nullable=False),
        sa.Column("subjectId", sa.String(30), sa.ForeignKey("Subject.id"), nullable=False),
        sa.Column("classRoomId", sa.String(30), sa.ForeignKey("ClassRoom.id"), nullable=True),
        sa.Column("score", sa.Float(), nullable=False),
        sa.Column("appreciation", sa.String(), nullable=True),
        sa.Column("status", _enum("AcademicValidationStatus", ACADEMIC_VALIDATION_STATUS), nullable=False, server_default="DRAFT"),
        sa.Column("recordedAt", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updatedAt", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("assessmentId", "studentId", name="uq_Grade_assessmentId_studentId"),
    )
    op.create_index("ix_Grade_studentId_periodId", "Grade", ["studentId", "periodId"])
    op.create_index("ix_Grade_classRoomId_periodId", "Grade", ["classRoomId", "periodId"])

    # --- ReportCard ---
    op.create_table(
        "ReportCard",
        sa.Column("id", sa.String(30), primary_key=True),
        sa.Column("studentId", sa.String(30), sa.ForeignKey("Student.id"), nullable=False),
        sa.Column("classRoomId", sa.String(30), sa.ForeignKey("ClassRoom.id"), nullable=True),
        sa.Column("schoolYearId", sa.String(30), sa.ForeignKey("SchoolYear.id"), nullable=False),
        sa.Column("periodId", sa.String(30), sa.ForeignKey("AcademicPeriod.id"), nullable=False),
        sa.Column("average", sa.Float(), nullable=True),
        sa.Column("rank", sa.Integer(), nullable=True),
        sa.Column("totalStudents", sa.Integer(), nullable=True),
        sa.Column("teacherComment", sa.String(), nullable=True),
        sa.Column("directorComment", sa.String(), nullable=True),
        sa.Column("verificationCode", sa.String(), nullable=False, unique=True),
        sa.Column("status", _enum("AcademicValidationStatus", ACADEMIC_VALIDATION_STATUS), nullable=False, server_default="DRAFT"),
        sa.Column("issuedAt", sa.DateTime(timezone=True), nullable=True),
        *timestamps,
        sa.UniqueConstraint("studentId", "periodId", name="uq_ReportCard_studentId_periodId"),
    )
    op.create_index("ix_ReportCard_classRoomId_periodId", "ReportCard", ["classRoomId", "periodId"])

    # --- Parent ---
    op.create_table(
        "Parent",
        sa.Column("id", sa.String(30), primary_key=True),
        sa.Column("firstName", sa.String(), nullable=False),
        sa.Column("lastName", sa.String(), nullable=False),
        sa.Column("phone", sa.String(), nullable=False, unique=True),
        sa.Column("email", sa.String(), nullable=True, unique=True),
        sa.Column("profession", sa.String(), nullable=True),
        sa.Column("address", sa.String(), nullable=True),
        sa.Column("preferredLanguage", sa.String(), nullable=True),
        sa.Column("otpVerifiedAt", sa.DateTime(timezone=True), nullable=True),
        *timestamps,
    )
    op.create_index("ix_Parent_lastName_firstName", "Parent", ["lastName", "firstName"])

    # --- StudentParent ---
    op.create_table(
        "StudentParent",
        sa.Column("id", sa.String(30), primary_key=True),
        sa.Column("studentId", sa.String(30), sa.ForeignKey("Student.id"), nullable=False),
        sa.Column("parentId", sa.String(30), sa.ForeignKey("Parent.id"), nullable=False),
        sa.Column("relation", _enum("ParentRelationType", PARENT_RELATION_TYPE), nullable=False),
        sa.Column("isPrimary", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("isEmergencyContact", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        *timestamps,
        sa.UniqueConstraint("studentId", "parentId", "relation", name="uq_StudentParent_studentId_parentId_rel"),
    )
    op.create_index("ix_StudentParent_studentId", "StudentParent", ["studentId"])
    op.create_index("ix_StudentParent_parentId", "StudentParent", ["parentId"])

    # --- ParentCommunication ---
    op.create_table(
        "ParentCommunication",
        sa.Column("id", sa.String(30), primary_key=True),
        sa.Column("parentId", sa.String(30), sa.ForeignKey("Parent.id"), nullable=False),
        sa.Column("studentId", sa.String(30), sa.ForeignKey("Student.id"), nullable=True),
        sa.Column("channel", _enum("CommunicationChannel", COMMUNICATION_CHANNEL), nullable=False),
        sa.Column("status", _enum("CommunicationStatus", COMMUNICATION_STATUS), nullable=False, server_default="DRAFT"),
        sa.Column("subject", sa.String(), nullable=True),
        sa.Column("message", sa.String(), nullable=False),
        sa.Column("sentAt", sa.DateTime(timezone=True), nullable=True),
        sa.Column("createdAt", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_ParentCommunication_parentId_createdAt", "ParentCommunication", ["parentId", "createdAt"])
    op.create_index("ix_ParentCommunication_studentId_createdAt", "ParentCommunication", ["studentId", "createdAt"])

    # --- LibraryInventory ---
    op.create_table(
        "LibraryInventory",
        sa.Column("id", sa.String(30), primary_key=True),
        sa.Column("schoolId", sa.String(30), sa.ForeignKey("School.id"), nullable=False),
        sa.Column("subjectId", sa.String(30), sa.ForeignKey("Subject.id"), nullable=False),
        sa.Column("level", sa.String(), nullable=False),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("stock", sa.Integer(), nullable=False),
        sa.Column("damaged", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("required", sa.Integer(), nullable=False),
        sa.Column("lastInventoryAt", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("status", _enum("LibraryStockStatus", LIBRARY_STOCK_STATUS), nullable=False, server_default="SUFFICIENT"),
        *timestamps,
        sa.UniqueConstraint("schoolId", "subjectId", "level", "title", name="uq_LibraryInventory_schoolId_subjectId_level_title"),
    )
    op.create_index("ix_LibraryInventory_schoolId_status", "LibraryInventory", ["schoolId", "status"])
    op.create_index("ix_LibraryInventory_subjectId", "LibraryInventory", ["subjectId"])
    op.create_index("ix_LibraryInventory_status", "LibraryInventory", ["status"])

    # --- LibraryLoan ---
    op.create_table(
        "LibraryLoan",
        sa.Column("id", sa.String(30), primary_key=True),
        sa.Column("inventoryId", sa.String(30), sa.ForeignKey("LibraryInventory.id"), nullable=False),
        sa.Column("studentId", sa.String(30), sa.ForeignKey("Student.id"), nullable=False),
        sa.Column("borrowedAt", sa.DateTime(timezone=True), nullable=False),
        sa.Column("dueAt", sa.DateTime(timezone=True), nullable=False),
        sa.Column("returnedAt", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", _enum("LibraryLoanStatus", LIBRARY_LOAN_STATUS), nullable=False, server_default="BORROWED"),
        *timestamps,
    )
    op.create_index("ix_LibraryLoan_inventoryId_status", "LibraryLoan", ["inventoryId", "status"])
    op.create_index("ix_LibraryLoan_studentId_status", "LibraryLoan", ["studentId", "status"])
    op.create_index("ix_LibraryLoan_dueAt_status", "LibraryLoan", ["dueAt", "status"])

    # --- ValidationRequest ---
    op.create_table(
        "ValidationRequest",
        sa.Column("id", sa.String(30), primary_key=True),
        sa.Column("entityType", _enum("ValidationEntityType", VALIDATION_ENTITY_TYPE), nullable=False),
        sa.Column("entityId", sa.String(30), nullable=False),
        sa.Column("status", _enum("ValidationStatus", VALIDATION_STATUS), nullable=False, server_default="SUBMITTED"),
        sa.Column("requestedById", sa.String(30), sa.ForeignKey("User.id"), nullable=False),
        sa.Column("reviewerRole", _enum("UserRole", USER_ROLE), nullable=False),
        sa.Column("reviewerRegionId", sa.String(30), nullable=True),
        sa.Column("reviewerPrefectureId", sa.String(30), nullable=True),
        sa.Column("reviewerSubPrefectureId", sa.String(30), nullable=True),
        sa.Column("reviewerUserId", sa.String(30), sa.ForeignKey("User.id"), nullable=True),
        sa.Column("reason", sa.String(), nullable=True),
        sa.Column("reviewedAt", sa.DateTime(timezone=True), nullable=True),
        *timestamps,
    )
    op.create_index("ix_ValidationRequest_entityType_entityId", "ValidationRequest", ["entityType", "entityId"])
    op.create_index("ix_ValidationRequest_status_reviewerRole", "ValidationRequest", ["status", "reviewerRole"])
    op.create_index("ix_ValidationRequest_requestedById_createdAt", "ValidationRequest", ["requestedById", "createdAt"])

    # --- Notification ---
    op.create_table(
        "Notification",
        sa.Column("id", sa.String(30), primary_key=True),
        sa.Column("recipientUserId", sa.String(30), sa.ForeignKey("User.id"), nullable=False),
        sa.Column("senderUserId", sa.String(30), sa.ForeignKey("User.id"), nullable=True),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("message", sa.String(), nullable=False),
        sa.Column("type", _enum("NotificationType", NOTIFICATION_TYPE), nullable=False),
        sa.Column("entityType", _enum("ValidationEntityType", VALIDATION_ENTITY_TYPE), nullable=True),
        sa.Column("entityId", sa.String(30), nullable=True),
        sa.Column("isRead", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("readAt", sa.DateTime(timezone=True), nullable=True),
        sa.Column("createdAt", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_Notification_recipientUserId_isRead_createdAt", "Notification", ["recipientUserId", "isRead", "createdAt"])
    op.create_index("ix_Notification_entityType_entityId", "Notification", ["entityType", "entityId"])

    # --- AuditLog ---
    op.create_table(
        "AuditLog",
        sa.Column("id", sa.String(30), primary_key=True),
        sa.Column("actorId", sa.String(30), sa.ForeignKey("User.id"), nullable=True),
        sa.Column("action", sa.String(), nullable=False),
        sa.Column("entity", sa.String(), nullable=False),
        sa.Column("entityId", sa.String(30), nullable=True),
        sa.Column("metadata", postgresql.JSONB(), nullable=True),
        sa.Column("createdAt", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )


def downgrade() -> None:
    # Drop tables in reverse FK order
    op.drop_table("AuditLog")
    op.drop_table("Notification")
    op.drop_table("ValidationRequest")
    op.drop_table("LibraryLoan")
    op.drop_table("LibraryInventory")
    op.drop_table("ParentCommunication")
    op.drop_table("StudentParent")
    op.drop_table("Parent")
    op.drop_table("ReportCard")
    op.drop_table("Grade")
    op.drop_table("Assessment")
    op.drop_table("AcademicPeriod")
    op.drop_table("Subject")
    op.drop_table("AttendanceRecord")
    op.drop_table("QrCredential")
    op.drop_table("StudentTransfer")
    op.drop_table("_ClassRoomTeacher")
    op.drop_table("Student")
    op.drop_table("Teacher")
    op.drop_table("ClassRoom")
    op.drop_table("User")
    op.drop_table("SchoolYear")
    op.drop_table("School")
    op.drop_table("SubPrefecture")
    op.drop_table("Prefecture")
    op.drop_table("Region")

    # Drop enums
    bind = op.get_bind()
    for name in (
        "LibraryLoanStatus", "LibraryStockStatus", "CommunicationStatus", "CommunicationChannel",
        "AcademicValidationStatus", "AssessmentType", "AcademicPeriodType",
        "ParentRelationType", "AttendanceStatus", "Gender", "PersonType",
        "NotificationType", "ValidationEntityType", "ValidationStatus", "UserRole",
    ):
        postgresql.ENUM(name=name).drop(bind, checkfirst=True)

    # NOTE: PostGIS extension is intentionally NOT dropped — it may be used by other apps.
