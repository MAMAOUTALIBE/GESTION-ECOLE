"""phase 11 — Finance & Budget

Revision ID: 0005_phase11
Revises: 0004_phase10
Create Date: 2026-05-05

Ajoute :
* 4 nouveaux ENUMs Postgres : BudgetStatus, BudgetCategory, ExpenseStatus,
  PolicyUnitCostCode
* 3 nouvelles tables : Budget, Expense, PolicyUnitCost
* Index pertinents pour les requêtes pilotage budgétaire et le simulator
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005_phase11"
down_revision: str | Sequence[str] | None = "0004_phase10"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


BUDGET_STATUS = postgresql.ENUM(
    "DRAFT", "APPROVED", "ACTIVE", "CLOSED",
    name="BudgetStatus", create_type=False,
)
BUDGET_CATEGORY = postgresql.ENUM(
    "SALARIES", "INFRASTRUCTURE", "EQUIPMENT", "OPERATIONS", "TRAINING",
    "TRANSPORT", "MEALS", "MISC",
    name="BudgetCategory", create_type=False,
)
EXPENSE_STATUS = postgresql.ENUM(
    "PENDING", "APPROVED", "REJECTED", "PAID",
    name="ExpenseStatus", create_type=False,
)
POLICY_UNIT_COST_CODE = postgresql.ENUM(
    "NEW_SCHOOL", "NEW_CLASSROOM", "TEACHER_YEAR",
    "GIRLS_TOILETS", "ELECTRICITY_SOLAR", "WATER_BOREHOLE",
    name="PolicyUnitCostCode", create_type=False,
)


def upgrade() -> None:
    bind = op.get_bind()

    for enum_type in (
        BUDGET_STATUS, BUDGET_CATEGORY, EXPENSE_STATUS, POLICY_UNIT_COST_CODE,
    ):
        enum_type.create(bind, checkfirst=True)

    # ---- Budget -----------------------------------------------------
    op.create_table(
        "Budget",
        sa.Column("id", sa.String(length=30), primary_key=True),
        sa.Column("fiscalYear", sa.Integer(), nullable=False),
        sa.Column("category", BUDGET_CATEGORY, nullable=False),
        sa.Column(
            "status", BUDGET_STATUS, nullable=False, server_default="DRAFT"
        ),
        sa.Column("regionId", sa.String(length=30), nullable=True),
        sa.Column("prefectureId", sa.String(length=30), nullable=True),
        sa.Column("subPrefectureId", sa.String(length=30), nullable=True),
        sa.Column("schoolId", sa.String(length=30), nullable=True),
        sa.Column("amountPlanned", sa.Float(), nullable=False),
        sa.Column(
            "currency", sa.String(length=3), nullable=False, server_default="GNF"
        ),
        sa.Column("notes", sa.String(), nullable=True),
        sa.Column("createdById", sa.String(length=30), nullable=True),
        sa.Column(
            "createdAt", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updatedAt", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["regionId"], ["Region.id"], name="fk_Budget_regionId_Region"
        ),
        sa.ForeignKeyConstraint(
            ["prefectureId"], ["Prefecture.id"],
            name="fk_Budget_prefectureId_Prefecture",
        ),
        sa.ForeignKeyConstraint(
            ["subPrefectureId"], ["SubPrefecture.id"],
            name="fk_Budget_subPrefectureId_SubPrefecture",
        ),
        sa.ForeignKeyConstraint(
            ["schoolId"], ["School.id"], name="fk_Budget_schoolId_School"
        ),
        sa.ForeignKeyConstraint(
            ["createdById"], ["User.id"], name="fk_Budget_createdById_User"
        ),
    )
    op.create_index(
        "ix_Budget_fiscalYear_status", "Budget", ["fiscalYear", "status"]
    )
    op.create_index(
        "ix_Budget_regionId_fiscalYear", "Budget", ["regionId", "fiscalYear"]
    )
    op.create_index(
        "ix_Budget_schoolId_fiscalYear", "Budget", ["schoolId", "fiscalYear"]
    )
    op.create_index(
        "ix_Budget_category_fiscalYear", "Budget", ["category", "fiscalYear"]
    )

    # ---- Expense ----------------------------------------------------
    op.create_table(
        "Expense",
        sa.Column("id", sa.String(length=30), primary_key=True),
        sa.Column("budgetId", sa.String(length=30), nullable=True),
        sa.Column("category", BUDGET_CATEGORY, nullable=False),
        sa.Column("amount", sa.Float(), nullable=False),
        sa.Column(
            "currency", sa.String(length=3), nullable=False, server_default="GNF"
        ),
        sa.Column("description", sa.String(), nullable=False),
        sa.Column("expenseDate", sa.Date(), nullable=False),
        sa.Column(
            "status", EXPENSE_STATUS, nullable=False, server_default="PENDING"
        ),
        sa.Column("schoolId", sa.String(length=30), nullable=True),
        sa.Column("regionId", sa.String(length=30), nullable=True),
        sa.Column("prefectureId", sa.String(length=30), nullable=True),
        sa.Column("subPrefectureId", sa.String(length=30), nullable=True),
        sa.Column("approvedById", sa.String(length=30), nullable=True),
        sa.Column("approvedAt", sa.DateTime(timezone=True), nullable=True),
        sa.Column("createdById", sa.String(length=30), nullable=True),
        sa.Column("receiptUrl", sa.String(), nullable=True),
        sa.Column(
            "createdAt", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updatedAt", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["budgetId"], ["Budget.id"], name="fk_Expense_budgetId_Budget"
        ),
        sa.ForeignKeyConstraint(
            ["schoolId"], ["School.id"], name="fk_Expense_schoolId_School"
        ),
        sa.ForeignKeyConstraint(
            ["regionId"], ["Region.id"], name="fk_Expense_regionId_Region"
        ),
        sa.ForeignKeyConstraint(
            ["prefectureId"], ["Prefecture.id"],
            name="fk_Expense_prefectureId_Prefecture",
        ),
        sa.ForeignKeyConstraint(
            ["subPrefectureId"], ["SubPrefecture.id"],
            name="fk_Expense_subPrefectureId_SubPrefecture",
        ),
        sa.ForeignKeyConstraint(
            ["approvedById"], ["User.id"], name="fk_Expense_approvedById_User"
        ),
        sa.ForeignKeyConstraint(
            ["createdById"], ["User.id"], name="fk_Expense_createdById_User"
        ),
    )
    op.create_index("ix_Expense_budgetId", "Expense", ["budgetId"])
    op.create_index(
        "ix_Expense_schoolId_expenseDate", "Expense", ["schoolId", "expenseDate"]
    )
    op.create_index(
        "ix_Expense_category_expenseDate", "Expense", ["category", "expenseDate"]
    )
    op.create_index(
        "ix_Expense_status_expenseDate", "Expense", ["status", "expenseDate"]
    )

    # ---- PolicyUnitCost --------------------------------------------
    op.create_table(
        "PolicyUnitCost",
        sa.Column("id", sa.String(length=30), primary_key=True),
        sa.Column("code", POLICY_UNIT_COST_CODE, nullable=False),
        sa.Column("label", sa.String(), nullable=False),
        sa.Column("amount", sa.Float(), nullable=False),
        sa.Column(
            "currency", sa.String(length=3), nullable=False, server_default="USD"
        ),
        sa.Column("source", sa.String(), nullable=True),
        sa.Column(
            "isActive", sa.Boolean(), nullable=False, server_default=sa.true()
        ),
        sa.Column("updatedById", sa.String(length=30), nullable=True),
        sa.Column(
            "createdAt", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updatedAt", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("code", name="uq_PolicyUnitCost_code"),
        sa.ForeignKeyConstraint(
            ["updatedById"], ["User.id"],
            name="fk_PolicyUnitCost_updatedById_User",
        ),
    )

    # Seed des coûts unitaires Banque Mondiale (overridable via API)
    op.execute(
        """
        INSERT INTO "PolicyUnitCost"
            (id, code, label, amount, currency, source, "isActive",
             "createdAt", "updatedAt")
        VALUES
            ('seed_new_school_bm23', 'NEW_SCHOOL',
             'Construction nouvelle école primaire',
             150000, 'USD', 'Banque Mondiale Afrique de l''Ouest 2023',
             true, NOW(), NOW()),
            ('seed_new_classroom_bm23', 'NEW_CLASSROOM',
             'Construction salle de classe additionnelle',
             25000, 'USD', 'Banque Mondiale Afrique de l''Ouest 2023',
             true, NOW(), NOW()),
            ('seed_teacher_year_bm23', 'TEACHER_YEAR',
             'Salaire annuel enseignant fonctionnaire',
             5000, 'USD', 'Banque Mondiale Afrique de l''Ouest 2023',
             true, NOW(), NOW()),
            ('seed_girls_toilets_bm23', 'GIRLS_TOILETS',
             'Bloc sanitaire filles par école',
             5000, 'USD', 'Banque Mondiale Afrique de l''Ouest 2023',
             true, NOW(), NOW()),
            ('seed_elec_solar_bm23', 'ELECTRICITY_SOLAR',
             'Solarisation école (kit complet)',
             8000, 'USD', 'Banque Mondiale Afrique de l''Ouest 2023',
             true, NOW(), NOW()),
            ('seed_water_borehole_bm23', 'WATER_BOREHOLE',
             'Forage d''eau potable école',
             10000, 'USD', 'Banque Mondiale Afrique de l''Ouest 2023',
             true, NOW(), NOW())
        """
    )


def downgrade() -> None:
    bind = op.get_bind()

    op.drop_table("PolicyUnitCost")

    op.drop_index("ix_Expense_status_expenseDate", table_name="Expense")
    op.drop_index("ix_Expense_category_expenseDate", table_name="Expense")
    op.drop_index("ix_Expense_schoolId_expenseDate", table_name="Expense")
    op.drop_index("ix_Expense_budgetId", table_name="Expense")
    op.drop_table("Expense")

    op.drop_index("ix_Budget_category_fiscalYear", table_name="Budget")
    op.drop_index("ix_Budget_schoolId_fiscalYear", table_name="Budget")
    op.drop_index("ix_Budget_regionId_fiscalYear", table_name="Budget")
    op.drop_index("ix_Budget_fiscalYear_status", table_name="Budget")
    op.drop_table("Budget")

    for enum_type in (
        POLICY_UNIT_COST_CODE, EXPENSE_STATUS, BUDGET_CATEGORY, BUDGET_STATUS,
    ):
        enum_type.drop(bind, checkfirst=True)
