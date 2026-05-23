"""phase 10 — School infrastructure structurée + module Inspections

Revision ID: 0004_phase10
Revises: 0003_phase3_postgis
Create Date: 2026-05-05

Ajoute :
* 4 nouveaux ENUMs Postgres : WaterSource, ElectricitySource, BuildingCondition,
  SchoolAffiliation
* 4 nouveaux ENUMs Inspection : InspectionStatus, InspectionCriterion,
  FindingSeverity, ActionItemStatus
* 14 nouveaux champs à `School` (tous nullables / défauts safe pour ne pas
  casser les écoles existantes)
* 3 nouvelles tables : `Inspection`, `InspectionFinding`, `InspectionActionItem`
* Index pertinents pour les requêtes du dashboard inspections
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004_phase10"
down_revision: str | Sequence[str] | None = "0003_phase3_postgis"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# --- ENUM definitions (CamelCase pour matcher la convention Prisma) ---
WATER_SOURCE = postgresql.ENUM(
    "NONE", "WELL", "BOREHOLE", "NETWORK", "RIVER",
    name="WaterSource", create_type=False,
)
ELECTRICITY_SOURCE = postgresql.ENUM(
    "NONE", "GRID", "SOLAR", "GENERATOR", "HYBRID",
    name="ElectricitySource", create_type=False,
)
BUILDING_CONDITION = postgresql.ENUM(
    "EXCELLENT", "GOOD", "FAIR", "POOR", "DANGEROUS",
    name="BuildingCondition", create_type=False,
)
SCHOOL_AFFILIATION = postgresql.ENUM(
    "PUBLIC", "PRIVATE_SECULAR", "CATHOLIC", "PROTESTANT", "ISLAMIC",
    "QURANIC", "FRANCO_ARABIC",
    name="SchoolAffiliation", create_type=False,
)
INSPECTION_STATUS = postgresql.ENUM(
    "PLANNED", "IN_PROGRESS", "COMPLETED", "CANCELLED",
    name="InspectionStatus", create_type=False,
)
INSPECTION_CRITERION = postgresql.ENUM(
    "GOVERNANCE", "PEDAGOGY", "INFRASTRUCTURE", "SAFETY", "HYGIENE",
    "EQUITY", "ATTENDANCE", "DOCUMENTS",
    name="InspectionCriterion", create_type=False,
)
FINDING_SEVERITY = postgresql.ENUM(
    "INFO", "MINOR", "MAJOR", "CRITICAL",
    name="FindingSeverity", create_type=False,
)
ACTION_ITEM_STATUS = postgresql.ENUM(
    "OPEN", "IN_PROGRESS", "RESOLVED", "CANCELLED",
    name="ActionItemStatus", create_type=False,
)


def upgrade() -> None:
    bind = op.get_bind()

    # 1. Create ENUM types
    for enum_type in (
        WATER_SOURCE, ELECTRICITY_SOURCE, BUILDING_CONDITION, SCHOOL_AFFILIATION,
        INSPECTION_STATUS, INSPECTION_CRITERION, FINDING_SEVERITY, ACTION_ITEM_STATUS,
    ):
        enum_type.create(bind, checkfirst=True)

    # 2. Extend School with infrastructure columns
    op.add_column("School", sa.Column("waterSource", WATER_SOURCE, nullable=True))
    op.add_column("School", sa.Column("electricitySource", ELECTRICITY_SOURCE, nullable=True))
    op.add_column(
        "School",
        sa.Column(
            "internetAvailable", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
    )
    op.add_column("School", sa.Column("toiletsBoys", sa.Integer(), nullable=True))
    op.add_column("School", sa.Column("toiletsGirls", sa.Integer(), nullable=True))
    op.add_column(
        "School",
        sa.Column(
            "toiletsAccessible", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
    )
    op.add_column("School", sa.Column("classroomsTotal", sa.Integer(), nullable=True))
    op.add_column("School", sa.Column("classroomsUsable", sa.Integer(), nullable=True))
    op.add_column(
        "School",
        sa.Column("buildingCondition", BUILDING_CONDITION, nullable=True),
    )
    op.add_column("School", sa.Column("buildingYear", sa.Integer(), nullable=True))
    op.add_column(
        "School",
        sa.Column(
            "multiShift", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
    )
    op.add_column(
        "School", sa.Column("distanceToHealthCenterKm", sa.Float(), nullable=True)
    )
    op.add_column("School", sa.Column("affiliation", SCHOOL_AFFILIATION, nullable=True))

    # 3. Inspection tables
    op.create_table(
        "Inspection",
        sa.Column("id", sa.String(length=30), primary_key=True),
        sa.Column("schoolId", sa.String(length=30), nullable=False),
        sa.Column("inspectorId", sa.String(length=30), nullable=False),
        sa.Column(
            "scheduledDate", sa.DateTime(timezone=True), nullable=False
        ),
        sa.Column("performedDate", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "status", INSPECTION_STATUS, nullable=False, server_default="PLANNED"
        ),
        sa.Column("overallScore", sa.Float(), nullable=True),
        sa.Column("notes", sa.String(), nullable=True),
        sa.Column(
            "createdAt", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updatedAt", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["schoolId"], ["School.id"], name="fk_Inspection_schoolId_School"
        ),
        sa.ForeignKeyConstraint(
            ["inspectorId"], ["User.id"], name="fk_Inspection_inspectorId_User"
        ),
    )
    op.create_index(
        "ix_Inspection_schoolId_status", "Inspection", ["schoolId", "status"]
    )
    op.create_index(
        "ix_Inspection_inspectorId_scheduledDate",
        "Inspection", ["inspectorId", "scheduledDate"],
    )
    op.create_index(
        "ix_Inspection_status_performedDate",
        "Inspection", ["status", "performedDate"],
    )

    op.create_table(
        "InspectionFinding",
        sa.Column("id", sa.String(length=30), primary_key=True),
        sa.Column("inspectionId", sa.String(length=30), nullable=False),
        sa.Column("criterion", INSPECTION_CRITERION, nullable=False),
        sa.Column("score", sa.Integer(), nullable=False),
        sa.Column(
            "severity", FINDING_SEVERITY, nullable=False, server_default="INFO"
        ),
        sa.Column("comment", sa.String(), nullable=True),
        sa.Column("photoUrl", sa.String(), nullable=True),
        sa.Column(
            "createdAt", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["inspectionId"], ["Inspection.id"],
            name="fk_InspectionFinding_inspectionId_Inspection",
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        "ix_InspectionFinding_inspectionId",
        "InspectionFinding", ["inspectionId"],
    )
    op.create_index(
        "ix_InspectionFinding_criterion_severity",
        "InspectionFinding", ["criterion", "severity"],
    )

    op.create_table(
        "InspectionActionItem",
        sa.Column("id", sa.String(length=30), primary_key=True),
        sa.Column("inspectionId", sa.String(length=30), nullable=False),
        sa.Column("description", sa.String(), nullable=False),
        sa.Column("dueDate", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "status", ACTION_ITEM_STATUS, nullable=False, server_default="OPEN"
        ),
        sa.Column("resolvedAt", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolvedById", sa.String(length=30), nullable=True),
        sa.Column(
            "createdAt", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updatedAt", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["inspectionId"], ["Inspection.id"],
            name="fk_InspectionActionItem_inspectionId_Inspection",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["resolvedById"], ["User.id"],
            name="fk_InspectionActionItem_resolvedById_User",
        ),
    )
    op.create_index(
        "ix_InspectionActionItem_inspectionId_status",
        "InspectionActionItem", ["inspectionId", "status"],
    )
    op.create_index(
        "ix_InspectionActionItem_dueDate_status",
        "InspectionActionItem", ["dueDate", "status"],
    )


def downgrade() -> None:
    bind = op.get_bind()

    op.drop_index(
        "ix_InspectionActionItem_dueDate_status", table_name="InspectionActionItem"
    )
    op.drop_index(
        "ix_InspectionActionItem_inspectionId_status",
        table_name="InspectionActionItem",
    )
    op.drop_table("InspectionActionItem")

    op.drop_index(
        "ix_InspectionFinding_criterion_severity", table_name="InspectionFinding"
    )
    op.drop_index("ix_InspectionFinding_inspectionId", table_name="InspectionFinding")
    op.drop_table("InspectionFinding")

    op.drop_index("ix_Inspection_status_performedDate", table_name="Inspection")
    op.drop_index("ix_Inspection_inspectorId_scheduledDate", table_name="Inspection")
    op.drop_index("ix_Inspection_schoolId_status", table_name="Inspection")
    op.drop_table("Inspection")

    for col in (
        "affiliation", "distanceToHealthCenterKm", "multiShift", "buildingYear",
        "buildingCondition", "classroomsUsable", "classroomsTotal",
        "toiletsAccessible", "toiletsGirls", "toiletsBoys", "internetAvailable",
        "electricitySource", "waterSource",
    ):
        op.drop_column("School", col)

    for enum_type in (
        ACTION_ITEM_STATUS, FINDING_SEVERITY, INSPECTION_CRITERION,
        INSPECTION_STATUS, SCHOOL_AFFILIATION, BUILDING_CONDITION,
        ELECTRICITY_SOURCE, WATER_SOURCE,
    ):
        enum_type.drop(bind, checkfirst=True)
