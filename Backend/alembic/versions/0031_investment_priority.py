"""Module 3C — Score composite de priorité d'investissement par école.

Revision ID: 0031_investment_priority
Revises: 0030_what_if_scenarios
Create Date: 2026-05-25

Pourquoi ?
----------
L'étape 3 IIPE de la carte scolaire ("orienter les investissements")
nécessite un classement par école pour permettre au cabinet de produire
un Top N "à investir cette année". On agrège 4 dimensions normalisées
(infrastructure, saturation, équité, accessibilité) en un score 0-100 et
on classe l'école en TRES_HAUTE / HAUTE / MOYENNE / BASSE.

Le score est recalculé périodiquement (idempotent par école : unique sur
``schoolId``), persisté avec un breakdownJson auditeur.

Modèle
------
``InvestmentPriorityScore`` :

* ``id`` (cuid)
* ``schoolId`` FK ``School.id`` UNIQUE
* ``baseSchoolYearId`` FK ``SchoolYear.id`` — année de référence des
  données (effectifs, projections)
* 4 sous-scores ``infrastructureScore``, ``saturationScore``,
  ``equityScore``, ``accessibilityScore`` (INT 0..n, pondérés)
* ``totalScore`` INT (somme, 0..100)
* ``priorityCategory`` ENUM (TRES_HAUTE / HAUTE / MOYENNE / BASSE)
* ``computedAt``, ``breakdownJson`` JSONB (détails par dimension pour
  audit + UI)

Index
-----
* ``(totalScore DESC)`` — top priorités
* ``(priorityCategory)`` — filtre par catégorie
* ``(baseSchoolYearId)`` — historisation année par année (un seul row
  actif par école, mais on garde l'année pour la traçabilité)

Hook Module 19 cockpit : ajoute ``HIGH_INVESTMENT_PRIORITY_COUNT`` à
l'enum ``KpiKey`` (count écoles TRES_HAUTE + HAUTE).

Downgrade
---------
Drop table + drop enum ``InvestmentPriorityCategory``. On ne retire pas
la valeur ajoutée à ``KpiKey`` (Postgres ne supporte pas DROP VALUE sur
un enum).
"""
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0031_investment_priority"
down_revision: str | Sequence[str] | None = "0030_what_if_scenarios"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


PRIORITY_CATEGORY = postgresql.ENUM(
    "TRES_HAUTE",
    "HAUTE",
    "MOYENNE",
    "BASSE",
    name="InvestmentPriorityCategory",
    create_type=False,
)


def upgrade() -> None:
    bind = op.get_bind()
    PRIORITY_CATEGORY.create(bind, checkfirst=True)

    # Hook Module 19 — ajoute la clef KPI investissement à l'enum KpiKey.
    op.execute(
        'ALTER TYPE "KpiKey" '
        "ADD VALUE IF NOT EXISTS 'HIGH_INVESTMENT_PRIORITY_COUNT'"
    )

    op.create_table(
        "InvestmentPriorityScore",
        sa.Column("id", sa.String(length=30), primary_key=True),
        sa.Column(
            "schoolId",
            sa.String(length=30),
            sa.ForeignKey("School.id"),
            nullable=False,
            unique=True,
        ),
        sa.Column(
            "baseSchoolYearId",
            sa.String(length=30),
            sa.ForeignKey("SchoolYear.id"),
            nullable=False,
        ),
        sa.Column("infrastructureScore", sa.Integer(), nullable=False),
        sa.Column("saturationScore", sa.Integer(), nullable=False),
        sa.Column("equityScore", sa.Integer(), nullable=False),
        sa.Column("accessibilityScore", sa.Integer(), nullable=False),
        sa.Column("totalScore", sa.Integer(), nullable=False),
        sa.Column("priorityCategory", PRIORITY_CATEGORY, nullable=False),
        sa.Column(
            "computedAt",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "breakdownJson",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column(
            "createdAt",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )

    op.create_index(
        "ix_InvestmentPriorityScore_totalScore",
        "InvestmentPriorityScore",
        ["totalScore"],
        postgresql_ops={"totalScore": "DESC"},
    )
    op.create_index(
        "ix_InvestmentPriorityScore_priorityCategory",
        "InvestmentPriorityScore",
        ["priorityCategory"],
    )
    op.create_index(
        "ix_InvestmentPriorityScore_baseSchoolYearId",
        "InvestmentPriorityScore",
        ["baseSchoolYearId"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_InvestmentPriorityScore_baseSchoolYearId",
        table_name="InvestmentPriorityScore",
    )
    op.drop_index(
        "ix_InvestmentPriorityScore_priorityCategory",
        table_name="InvestmentPriorityScore",
    )
    op.drop_index(
        "ix_InvestmentPriorityScore_totalScore",
        table_name="InvestmentPriorityScore",
    )
    op.drop_table("InvestmentPriorityScore")

    bind = op.get_bind()
    PRIORITY_CATEGORY.drop(bind, checkfirst=True)
    # Note : on ne retire pas HIGH_INVESTMENT_PRIORITY_COUNT de l'enum
    # KpiKey — Postgres ne supporte pas DROP VALUE sur un type enum.
