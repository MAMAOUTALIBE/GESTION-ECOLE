"""module 7 — schoollife: vaccinations / allergies / meal attendance / bus stops & subscriptions

Revision ID: 0013_schoollife
Revises: 0012_i18n_and_workflow_sla
Create Date: 2026-05-24

Pourquoi ?
----------
La phase 13 (migration 0006) avait posé les bases de la vie scolaire avec 5
tables (Incident, HealthVisit, BusRoute, MealService, TimetableSlot). Module
7 enrichit ces fondations pour couvrir les 4 sous-domaines :

* **Discipline** : on ajoute une colonne ``status`` à ``Incident`` pour
  suivre le cycle (OPEN → UNDER_REVIEW → RESOLVED → CLOSED).
* **Santé** : nouvelles tables ``Vaccination`` (traces vaccin) et
  ``StudentAllergy`` (allergies générales / alimentaires).
* **Cantine** : ``MealAttendance`` pour la présence par élève à un service,
  + ``MealMenu`` (JSON satellite à MealService pour stocker les plats).
* **Transport** : ``BusStop`` (points d'arrêt rattachés à une route) et
  ``StudentBusSubscription`` (abonnement élève → route → arrêt).

Downgrade
---------
Drop tables greenfield + drop la colonne ``Incident.status`` + drop les
enums créés ici.  Phase 13 reste intacte.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0013_schoollife"
down_revision: str | Sequence[str] | None = "0012_i18n_and_workflow_sla"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


INCIDENT_STATUS = postgresql.ENUM(
    "OPEN", "UNDER_REVIEW", "RESOLVED", "CLOSED",
    name="IncidentStatus", create_type=False,
)
VACCINATION_STATUS = postgresql.ENUM(
    "SCHEDULED", "ADMINISTERED", "SKIPPED", "REFUSED",
    name="VaccinationStatus", create_type=False,
)
ALLERGY_CATEGORY = postgresql.ENUM(
    "FOOD", "DRUG", "ENVIRONMENTAL", "OTHER",
    name="AllergyCategory", create_type=False,
)
ALLERGY_SEVERITY = postgresql.ENUM(
    "MILD", "MODERATE", "SEVERE", "ANAPHYLACTIC",
    name="AllergySeverity", create_type=False,
)
MEAL_ATTENDANCE_STATUS = postgresql.ENUM(
    "PRESENT", "ABSENT", "EXCUSED",
    name="MealAttendanceStatus", create_type=False,
)
BUS_SUBSCRIPTION_STATUS = postgresql.ENUM(
    "ACTIVE", "SUSPENDED", "EXPIRED", "CANCELLED",
    name="BusSubscriptionStatus", create_type=False,
)

_ALL_ENUMS = (
    INCIDENT_STATUS, VACCINATION_STATUS, ALLERGY_CATEGORY,
    ALLERGY_SEVERITY, MEAL_ATTENDANCE_STATUS, BUS_SUBSCRIPTION_STATUS,
)


def upgrade() -> None:
    bind = op.get_bind()
    for enum_type in _ALL_ENUMS:
        enum_type.create(bind, checkfirst=True)

    # ---- Incident.status (column add) ---------------------------------
    op.add_column(
        "Incident",
        sa.Column(
            "status", INCIDENT_STATUS,
            nullable=False, server_default="OPEN",
        ),
    )

    # ---- Vaccination --------------------------------------------------
    op.create_table(
        "Vaccination",
        sa.Column("id", sa.String(length=30), primary_key=True),
        sa.Column("studentId", sa.String(length=30), nullable=False),
        sa.Column("vaccine", sa.String(length=120), nullable=False),
        sa.Column("dateAdministered", sa.Date(), nullable=False),
        sa.Column("batchNumber", sa.String(length=80), nullable=True),
        sa.Column("administeredBy", sa.String(length=200), nullable=True),
        sa.Column(
            "status", VACCINATION_STATUS,
            nullable=False, server_default="ADMINISTERED",
        ),
        sa.Column("notes", sa.String(), nullable=True),
        sa.Column("recordedById", sa.String(length=30), nullable=True),
        sa.Column(
            "createdAt", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.func.now(),
        ),
        sa.Column(
            "updatedAt", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["studentId"], ["Student.id"],
            name="fk_Vaccination_studentId_Student",
        ),
        sa.ForeignKeyConstraint(
            ["recordedById"], ["User.id"],
            name="fk_Vaccination_recordedById_User",
        ),
    )
    op.create_index("ix_Vaccination_studentId", "Vaccination", ["studentId"])
    op.create_index(
        "ix_Vaccination_vaccine_dateAdministered", "Vaccination",
        ["vaccine", "dateAdministered"],
    )

    # ---- StudentAllergy ----------------------------------------------
    op.create_table(
        "StudentAllergy",
        sa.Column("id", sa.String(length=30), primary_key=True),
        sa.Column("studentId", sa.String(length=30), nullable=False),
        sa.Column("allergen", sa.String(length=200), nullable=False),
        sa.Column(
            "category", ALLERGY_CATEGORY,
            nullable=False, server_default="FOOD",
        ),
        sa.Column(
            "severity", ALLERGY_SEVERITY,
            nullable=False, server_default="MILD",
        ),
        sa.Column("notes", sa.String(), nullable=True),
        sa.Column("recordedById", sa.String(length=30), nullable=True),
        sa.Column(
            "createdAt", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.func.now(),
        ),
        sa.Column(
            "updatedAt", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["studentId"], ["Student.id"],
            name="fk_StudentAllergy_studentId_Student",
        ),
        sa.ForeignKeyConstraint(
            ["recordedById"], ["User.id"],
            name="fk_StudentAllergy_recordedById_User",
        ),
    )
    op.create_index("ix_StudentAllergy_studentId", "StudentAllergy", ["studentId"])
    op.create_index("ix_StudentAllergy_category", "StudentAllergy", ["category"])

    # ---- MealAttendance ----------------------------------------------
    op.create_table(
        "MealAttendance",
        sa.Column("id", sa.String(length=30), primary_key=True),
        sa.Column("mealServiceId", sa.String(length=30), nullable=False),
        sa.Column("studentId", sa.String(length=30), nullable=False),
        sa.Column(
            "status", MEAL_ATTENDANCE_STATUS,
            nullable=False, server_default="PRESENT",
        ),
        sa.Column("recordedById", sa.String(length=30), nullable=True),
        sa.Column(
            "createdAt", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.func.now(),
        ),
        sa.Column(
            "updatedAt", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["mealServiceId"], ["MealService.id"],
            name="fk_MealAttendance_mealServiceId_MealService",
        ),
        sa.ForeignKeyConstraint(
            ["studentId"], ["Student.id"],
            name="fk_MealAttendance_studentId_Student",
        ),
        sa.ForeignKeyConstraint(
            ["recordedById"], ["User.id"],
            name="fk_MealAttendance_recordedById_User",
        ),
        sa.UniqueConstraint(
            "mealServiceId", "studentId",
            name="uq_MealAttendance_mealServiceId_studentId",
        ),
    )
    op.create_index("ix_MealAttendance_studentId", "MealAttendance", ["studentId"])

    # ---- MealMenu -----------------------------------------------------
    op.create_table(
        "MealMenu",
        sa.Column("id", sa.String(length=30), primary_key=True),
        sa.Column("mealServiceId", sa.String(length=30), nullable=False),
        sa.Column("items", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("allergens", postgresql.JSONB(), nullable=True, server_default="[]"),
        sa.Column("estimatedCostGNF", sa.Float(), nullable=True),
        sa.Column(
            "createdAt", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.func.now(),
        ),
        sa.Column(
            "updatedAt", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["mealServiceId"], ["MealService.id"],
            name="fk_MealMenu_mealServiceId_MealService",
        ),
        sa.UniqueConstraint(
            "mealServiceId", name="uq_MealMenu_mealServiceId",
        ),
    )

    # ---- BusStop ------------------------------------------------------
    op.create_table(
        "BusStop",
        sa.Column("id", sa.String(length=30), primary_key=True),
        sa.Column("routeId", sa.String(length=30), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("lat", sa.Float(), nullable=True),
        sa.Column("lon", sa.Float(), nullable=True),
        sa.Column("pickupTime", sa.String(length=5), nullable=True),
        sa.Column("dropoffTime", sa.String(length=5), nullable=True),
        sa.Column("stopOrder", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "createdAt", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.func.now(),
        ),
        sa.Column(
            "updatedAt", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["routeId"], ["BusRoute.id"],
            name="fk_BusStop_routeId_BusRoute",
        ),
        sa.UniqueConstraint(
            "routeId", "name", name="uq_BusStop_routeId_name",
        ),
    )
    op.create_index(
        "ix_BusStop_routeId_order", "BusStop", ["routeId", "stopOrder"],
    )

    # ---- StudentBusSubscription --------------------------------------
    op.create_table(
        "StudentBusSubscription",
        sa.Column("id", sa.String(length=30), primary_key=True),
        sa.Column("studentId", sa.String(length=30), nullable=False),
        sa.Column("routeId", sa.String(length=30), nullable=False),
        sa.Column("stopId", sa.String(length=30), nullable=True),
        sa.Column("startDate", sa.Date(), nullable=False),
        sa.Column("endDate", sa.Date(), nullable=True),
        sa.Column(
            "status", BUS_SUBSCRIPTION_STATUS,
            nullable=False, server_default="ACTIVE",
        ),
        sa.Column("monthlyFeeGNF", sa.Float(), nullable=True),
        sa.Column("notes", sa.String(), nullable=True),
        sa.Column(
            "createdAt", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.func.now(),
        ),
        sa.Column(
            "updatedAt", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["studentId"], ["Student.id"],
            name="fk_StudentBusSubscription_studentId_Student",
        ),
        sa.ForeignKeyConstraint(
            ["routeId"], ["BusRoute.id"],
            name="fk_StudentBusSubscription_routeId_BusRoute",
        ),
        sa.ForeignKeyConstraint(
            ["stopId"], ["BusStop.id"],
            name="fk_StudentBusSubscription_stopId_BusStop",
        ),
        sa.UniqueConstraint(
            "studentId", "routeId", "startDate",
            name="uq_StudentBusSubscription_student_route_start",
        ),
    )
    op.create_index(
        "ix_StudentBusSubscription_studentId",
        "StudentBusSubscription", ["studentId"],
    )
    op.create_index(
        "ix_StudentBusSubscription_routeId_status",
        "StudentBusSubscription", ["routeId", "status"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_StudentBusSubscription_routeId_status",
        table_name="StudentBusSubscription",
    )
    op.drop_index(
        "ix_StudentBusSubscription_studentId",
        table_name="StudentBusSubscription",
    )
    op.drop_table("StudentBusSubscription")

    op.drop_index("ix_BusStop_routeId_order", table_name="BusStop")
    op.drop_table("BusStop")

    op.drop_table("MealMenu")

    op.drop_index("ix_MealAttendance_studentId", table_name="MealAttendance")
    op.drop_table("MealAttendance")

    op.drop_index("ix_StudentAllergy_category", table_name="StudentAllergy")
    op.drop_index("ix_StudentAllergy_studentId", table_name="StudentAllergy")
    op.drop_table("StudentAllergy")

    op.drop_index(
        "ix_Vaccination_vaccine_dateAdministered",
        table_name="Vaccination",
    )
    op.drop_index("ix_Vaccination_studentId", table_name="Vaccination")
    op.drop_table("Vaccination")

    op.drop_column("Incident", "status")

    bind = op.get_bind()
    for enum_type in _ALL_ENUMS:
        enum_type.drop(bind, checkfirst=True)
