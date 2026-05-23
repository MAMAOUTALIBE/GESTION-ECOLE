"""phase 13 — Vie scolaire (discipline + santé + transport + cantines + emploi du temps)

Revision ID: 0006_phase13
Revises: 0005_phase11
Create Date: 2026-05-05

Crée 5 tables greenfield qui équipent les écrans administratifs sans backend
dédié jusqu'ici : Incident, HealthVisit, BusRoute, MealService, TimetableSlot.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0006_phase13"
down_revision: str | Sequence[str] | None = "0005_phase11"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


INCIDENT_TYPE = postgresql.ENUM(
    "LATENESS", "INSUBORDINATION", "FIGHTING", "ABSENCE", "BULLYING",
    "PROPERTY_DAMAGE", "OTHER",
    name="IncidentType", create_type=False,
)
INCIDENT_SEVERITY = postgresql.ENUM(
    "LOW", "MEDIUM", "HIGH", name="IncidentSeverity", create_type=False,
)
INCIDENT_SANCTION = postgresql.ENUM(
    "NONE", "WARNING", "DETENTION", "PARENT_MEETING", "SUSPENSION", "EXPULSION",
    name="IncidentSanction", create_type=False,
)
HEALTH_VISIT_TYPE = postgresql.ENUM(
    "CHECKUP", "ILLNESS", "INJURY", "VACCINATION", "OTHER",
    name="HealthVisitType", create_type=False,
)
HEALTH_VISIT_STATUS = postgresql.ENUM(
    "REPORTED", "TREATED", "REFERRED", "RESOLVED",
    name="HealthVisitStatus", create_type=False,
)
TRANSPORT_ROUTE_STATUS = postgresql.ENUM(
    "ACTIVE", "MAINTENANCE", "INACTIVE",
    name="TransportRouteStatus", create_type=False,
)
MEAL_SERVICE_TYPE = postgresql.ENUM(
    "BREAKFAST", "LUNCH", "SNACK", name="MealServiceType", create_type=False,
)
DAY_OF_WEEK = postgresql.ENUM(
    "MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY", "SATURDAY",
    name="DayOfWeek", create_type=False,
)


def upgrade() -> None:
    bind = op.get_bind()
    for enum_type in (
        INCIDENT_TYPE, INCIDENT_SEVERITY, INCIDENT_SANCTION,
        HEALTH_VISIT_TYPE, HEALTH_VISIT_STATUS,
        TRANSPORT_ROUTE_STATUS, MEAL_SERVICE_TYPE, DAY_OF_WEEK,
    ):
        enum_type.create(bind, checkfirst=True)

    # ---- Incident ---------------------------------------------------
    op.create_table(
        "Incident",
        sa.Column("id", sa.String(length=30), primary_key=True),
        sa.Column("schoolId", sa.String(length=30), nullable=False),
        sa.Column("studentId", sa.String(length=30), nullable=True),
        sa.Column("type", INCIDENT_TYPE, nullable=False),
        sa.Column("severity", INCIDENT_SEVERITY, nullable=False, server_default="LOW"),
        sa.Column("description", sa.String(), nullable=False),
        sa.Column("sanction", INCIDENT_SANCTION, nullable=False, server_default="NONE"),
        sa.Column("occurredAt", sa.DateTime(timezone=True), nullable=False),
        sa.Column("recordedById", sa.String(length=30), nullable=True),
        sa.Column("createdAt", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updatedAt", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["schoolId"], ["School.id"], name="fk_Incident_schoolId_School"),
        sa.ForeignKeyConstraint(["studentId"], ["Student.id"], name="fk_Incident_studentId_Student"),
        sa.ForeignKeyConstraint(["recordedById"], ["User.id"], name="fk_Incident_recordedById_User"),
    )
    op.create_index("ix_Incident_schoolId_occurredAt", "Incident", ["schoolId", "occurredAt"])
    op.create_index("ix_Incident_studentId", "Incident", ["studentId"])
    op.create_index("ix_Incident_severity", "Incident", ["severity"])

    # ---- HealthVisit ------------------------------------------------
    op.create_table(
        "HealthVisit",
        sa.Column("id", sa.String(length=30), primary_key=True),
        sa.Column("schoolId", sa.String(length=30), nullable=False),
        sa.Column("studentId", sa.String(length=30), nullable=True),
        sa.Column("type", HEALTH_VISIT_TYPE, nullable=False),
        sa.Column("description", sa.String(), nullable=False),
        sa.Column("visitDate", sa.Date(), nullable=False),
        sa.Column("nurseName", sa.String(), nullable=True),
        sa.Column("status", HEALTH_VISIT_STATUS, nullable=False, server_default="REPORTED"),
        sa.Column("createdAt", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updatedAt", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["schoolId"], ["School.id"], name="fk_HealthVisit_schoolId_School"),
        sa.ForeignKeyConstraint(["studentId"], ["Student.id"], name="fk_HealthVisit_studentId_Student"),
    )
    op.create_index("ix_HealthVisit_schoolId_visitDate", "HealthVisit", ["schoolId", "visitDate"])
    op.create_index("ix_HealthVisit_studentId", "HealthVisit", ["studentId"])

    # ---- BusRoute ---------------------------------------------------
    op.create_table(
        "BusRoute",
        sa.Column("id", sa.String(length=30), primary_key=True),
        sa.Column("schoolId", sa.String(length=30), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("capacity", sa.Integer(), nullable=False, server_default="40"),
        sa.Column("departureTime", sa.String(length=5), nullable=False),
        sa.Column("returnTime", sa.String(length=5), nullable=False),
        sa.Column("driverName", sa.String(), nullable=True),
        sa.Column("driverPhone", sa.String(), nullable=True),
        sa.Column("plate", sa.String(), nullable=True),
        sa.Column("status", TRANSPORT_ROUTE_STATUS, nullable=False, server_default="ACTIVE"),
        sa.Column("studentsAssigned", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("createdAt", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updatedAt", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["schoolId"], ["School.id"], name="fk_BusRoute_schoolId_School"),
        sa.UniqueConstraint("schoolId", "name", name="uq_BusRoute_schoolId_name"),
    )
    op.create_index("ix_BusRoute_schoolId_status", "BusRoute", ["schoolId", "status"])

    # ---- MealService ------------------------------------------------
    op.create_table(
        "MealService",
        sa.Column("id", sa.String(length=30), primary_key=True),
        sa.Column("schoolId", sa.String(length=30), nullable=False),
        sa.Column("type", MEAL_SERVICE_TYPE, nullable=False, server_default="LUNCH"),
        sa.Column("serviceDate", sa.Date(), nullable=False),
        sa.Column("mealsPlanned", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("mealsServed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("costPerMealGNF", sa.Float(), nullable=False, server_default="2500"),
        sa.Column("notes", sa.String(), nullable=True),
        sa.Column("createdAt", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updatedAt", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["schoolId"], ["School.id"], name="fk_MealService_schoolId_School"),
    )
    op.create_index("ix_MealService_schoolId_serviceDate", "MealService", ["schoolId", "serviceDate"])

    # ---- TimetableSlot ----------------------------------------------
    op.create_table(
        "TimetableSlot",
        sa.Column("id", sa.String(length=30), primary_key=True),
        sa.Column("classRoomId", sa.String(length=30), nullable=False),
        sa.Column("dayOfWeek", DAY_OF_WEEK, nullable=False),
        sa.Column("startTime", sa.Time(), nullable=False),
        sa.Column("endTime", sa.Time(), nullable=False),
        sa.Column("subjectId", sa.String(length=30), nullable=True),
        sa.Column("teacherId", sa.String(length=30), nullable=True),
        sa.Column("room", sa.String(), nullable=True),
        sa.Column("createdAt", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updatedAt", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["classRoomId"], ["ClassRoom.id"], name="fk_TimetableSlot_classRoomId_ClassRoom"),
        sa.ForeignKeyConstraint(["subjectId"], ["Subject.id"], name="fk_TimetableSlot_subjectId_Subject"),
        sa.ForeignKeyConstraint(["teacherId"], ["Teacher.id"], name="fk_TimetableSlot_teacherId_Teacher"),
    )
    op.create_index("ix_TimetableSlot_classRoomId_dayOfWeek", "TimetableSlot", ["classRoomId", "dayOfWeek"])


def downgrade() -> None:
    bind = op.get_bind()
    op.drop_table("TimetableSlot")
    op.drop_table("MealService")
    op.drop_table("BusRoute")
    op.drop_table("HealthVisit")
    op.drop_table("Incident")
    for enum_type in (
        DAY_OF_WEEK, MEAL_SERVICE_TYPE, TRANSPORT_ROUTE_STATUS,
        HEALTH_VISIT_STATUS, HEALTH_VISIT_TYPE,
        INCIDENT_SANCTION, INCIDENT_SEVERITY, INCIDENT_TYPE,
    ):
        enum_type.drop(bind, checkfirst=True)
