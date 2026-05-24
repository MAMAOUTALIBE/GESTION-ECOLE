"""module 2C — Capacité vs demande projetée (IIPE / planification infra)

Revision ID: 0028_capacity_demand
Revises: 0027_projections
Create Date: 2026-05-24

Pourquoi ?
----------
Module 2B a produit les projections d'effectifs horizon 1..5 ans
(NATIONAL + REGIONAL). Reste à comparer cette **demande projetée** à la
**capacité physique** des écoles pour orienter les investissements
infrastructure (où construire ? où réhabiliter ? où fusionner ?).

Formule
-------

::

    capacity(school)      = classroomsUsable × STUDENTS_PER_CLASSROOM_NORM
    demand(school, t+k)   = somme des effectifs projetés (tous niveaux/genres)
    gap(school, t+k)      = demand - capacity
    saturation(school)    = demand / capacity × 100  (NULL si capacity=0)

    severity :
      OK       si saturation <= 80
      WARNING  si  80 < saturation <= 100
      CRITICAL si saturation > 100

Niveau OK = marge ; WARNING = saturation proche ; CRITICAL = salles
supplémentaires requises immédiatement.

Modèle de données
-----------------
``CapacityDemandSnapshot`` : un row par (baseSchoolYearId, projectedYear,
scope, entityId, scenarioId). Scope agrège progressivement les écoles
vers les préfectures, régions, et le national :

* ``SCHOOL``     — entityId = School.id, source primaire.
* ``PREFECTURE`` — entityId = Prefecture.id, somme des écoles.
* ``REGIONAL``   — entityId = Region.id, somme des préfectures.
* ``NATIONAL``   — entityId NULL, somme des régions.

Index
-----
* ``(baseSchoolYearId, projectedYear, scope, severity)`` — vue dashboard
  "écoles CRITICAL pour l'année t+1".
* ``(entityId, computedAt DESC)`` — historique d'une école / région.

Unique
------
* ``(baseSchoolYearId, projectedYear, scope, entityId, scenarioId)`` —
  upsert idempotent au recalcul.

Hooks
-----
* Module 9 — ajoute ``CAPACITY_CRITICAL_PROJECTED`` à l'enum AnomalyType.
* Module 19 cockpit — ajoute ``PROJECTED_CRITICAL_SCHOOLS_COUNT`` à
  l'enum KpiKey (count écoles CRITICAL sur projection +1 an).

Downgrade
---------
Drop table + drop enum CapacityScope, CapacitySeverity. L'enum
AnomalyType/KpiKey hérité d'enums Postgres ne peut pas se rétracter
(``ALTER TYPE ... DROP VALUE`` n'existe pas). On laisse les valeurs en
place : elles deviennent simplement inutilisables sans la table.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0028_capacity_demand"
down_revision: str | Sequence[str] | None = "0027_projections"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


CAPACITY_SCOPE = postgresql.ENUM(
    "NATIONAL",
    "REGIONAL",
    "PREFECTURE",
    "SCHOOL",
    name="CapacityScope",
    create_type=False,
)

CAPACITY_SEVERITY = postgresql.ENUM(
    "OK",
    "WARNING",
    "CRITICAL",
    name="CapacitySeverity",
    create_type=False,
)


def upgrade() -> None:
    bind = op.get_bind()
    CAPACITY_SCOPE.create(bind, checkfirst=True)
    CAPACITY_SEVERITY.create(bind, checkfirst=True)

    # Module 9 — ajoute CAPACITY_CRITICAL_PROJECTED à l'enum AnomalyType.
    op.execute(
        'ALTER TYPE "AnomalyType" '
        "ADD VALUE IF NOT EXISTS 'CAPACITY_CRITICAL_PROJECTED'"
    )

    # Module 19 — ajoute PROJECTED_CRITICAL_SCHOOLS_COUNT à l'enum KpiKey.
    op.execute(
        'ALTER TYPE "KpiKey" '
        "ADD VALUE IF NOT EXISTS 'PROJECTED_CRITICAL_SCHOOLS_COUNT'"
    )

    op.create_table(
        "CapacityDemandSnapshot",
        sa.Column("id", sa.String(length=30), primary_key=True),
        sa.Column(
            "baseSchoolYearId",
            sa.String(length=30),
            sa.ForeignKey("SchoolYear.id"),
            nullable=False,
        ),
        sa.Column("projectedYear", sa.Integer(), nullable=False),
        sa.Column("scope", CAPACITY_SCOPE, nullable=False),
        # entityId nullable uniquement pour scope=NATIONAL.
        sa.Column("entityId", sa.String(length=30), nullable=True),
        sa.Column("capacity", sa.Integer(), nullable=False),
        sa.Column("demand", sa.Integer(), nullable=False),
        sa.Column("gap", sa.Integer(), nullable=False),
        # saturationPct nullable quand capacity=0 (pas de division par zéro).
        sa.Column(
            "saturationPct",
            sa.Numeric(precision=6, scale=2),
            nullable=True,
        ),
        sa.Column("severity", CAPACITY_SEVERITY, nullable=False),
        sa.Column(
            "scenarioId",
            sa.String(length=30),
            sa.ForeignKey("ProjectionScenario.id"),
            nullable=False,
            server_default=sa.text("'BASELINE'"),
        ),
        sa.Column(
            "computedAt", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.func.now(),
        ),
        sa.Column(
            "createdAt", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "baseSchoolYearId", "projectedYear", "scope",
            "entityId", "scenarioId",
            name="uq_CapacityDemandSnapshot_full",
        ),
    )

    op.create_index(
        "ix_CapacityDemandSnapshot_base_year_scope_severity",
        "CapacityDemandSnapshot",
        ["baseSchoolYearId", "projectedYear", "scope", "severity"],
    )
    op.create_index(
        "ix_CapacityDemandSnapshot_entityId_computedAt",
        "CapacityDemandSnapshot",
        ["entityId", "computedAt"],
        postgresql_using="btree",
    )


def downgrade() -> None:
    op.drop_index(
        "ix_CapacityDemandSnapshot_entityId_computedAt",
        table_name="CapacityDemandSnapshot",
    )
    op.drop_index(
        "ix_CapacityDemandSnapshot_base_year_scope_severity",
        table_name="CapacityDemandSnapshot",
    )
    op.drop_table("CapacityDemandSnapshot")
    bind = op.get_bind()
    CAPACITY_SEVERITY.drop(bind, checkfirst=True)
    CAPACITY_SCOPE.drop(bind, checkfirst=True)
    # Note : on ne retire pas les valeurs ajoutées aux enums
    # AnomalyType / KpiKey (Postgres ne supporte pas DROP VALUE).
