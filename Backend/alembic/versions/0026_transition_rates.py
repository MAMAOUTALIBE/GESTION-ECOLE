"""module 2A — Taux de transition par cohortes (IIPE-UNESCO)

Revision ID: 0026_transition_rates
Revises: 0025_zone_type
Create Date: 2026-05-24

Pourquoi ?
----------
Le taux de transition d'un niveau N vers N+1 est l'indicateur clef de la
projection IIPE-UNESCO :

    tt(region, levelN→levelN+1, gender, year_t) =
       enrollment[region, levelN+1, gender, year_t+1]
       /
       enrollment[region, levelN, gender, year_t]

C'est la base du Module 2B (projections cohorte sur 6 ans) et de la
mesure équité filles/garçons des passages de niveau.

Stratégie de stockage
---------------------
* Table persistée (pas live) — calcul cher, et le résultat point-in-time
  doit rester traçable (rapports IIPE, sources Enrollment évoluent).
* Deux échelons : ``NATIONAL`` (entityId NULL) et ``REGIONAL``
  (entityId = regionId).
* Désagrégé par genre (filles/garçons séparés) et par paire de niveaux
  (CP1→CP2, …, CM1→CM2 — 8 transitions du primaire guinéen).
* ``rate`` ``NUMERIC(6,4) NULL`` — précision 4 décimales, NULL quand le
  dénominateur (count_from) vaut 0 (pas de division par zéro).
* ``sampleSize`` ``INT NOT NULL`` : volume du dénominateur (count_from),
  utile pour la confiance (un rate calculé sur 10 élèves est fragile).
* ``isOutlier`` ``BOOL DEFAULT false`` : ``true`` si rate > 2 ou rate < 0
  (signal d'erreur de saisie ou redoublement de masse).

Index
-----
* ``(scope, entityId, schoolYearFromId)`` : "tous les rates d'une région
  pour une année donnée" — vue dashboard.
* ``(classLevelFrom, classLevelTo)`` : tri/filtre par paire de niveaux.

Unique
------
* ``(scope, entityId, schoolYearFromId, classLevelFrom, gender)`` —
  garantit l'upsert idempotent au recalcul (un seul row par cellule).

Downgrade
---------
Drop table + drop enum ``TransitionScope``.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0026_transition_rates"
down_revision: str | Sequence[str] | None = "0025_zone_type"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


TRANSITION_SCOPE = postgresql.ENUM(
    "NATIONAL",
    "REGIONAL",
    name="TransitionScope",
    create_type=False,
)


def upgrade() -> None:
    bind = op.get_bind()
    TRANSITION_SCOPE.create(bind, checkfirst=True)

    # Module 9 — ajoute TRANSITION_RATE_OUTLIER à l'enum AnomalyType.
    op.execute(
        'ALTER TYPE "AnomalyType" '
        "ADD VALUE IF NOT EXISTS 'TRANSITION_RATE_OUTLIER'"
    )

    op.create_table(
        "TransitionRate",
        sa.Column("id", sa.String(length=30), primary_key=True),
        sa.Column(
            "schoolYearFromId",
            sa.String(length=30),
            sa.ForeignKey("SchoolYear.id"),
            nullable=False,
        ),
        sa.Column(
            "schoolYearToId",
            sa.String(length=30),
            sa.ForeignKey("SchoolYear.id"),
            nullable=False,
        ),
        sa.Column("scope", TRANSITION_SCOPE, nullable=False),
        # entityId nullable uniquement pour scope=NATIONAL.
        sa.Column("entityId", sa.String(length=30), nullable=True),
        sa.Column(
            "classLevelFrom",
            postgresql.ENUM(
                "MATERNELLE_1", "MATERNELLE_2", "MATERNELLE_3",
                "CP1", "CP2", "CE1", "CE2", "CM1", "CM2",
                name="EnrollmentClassLevel",
                create_type=False,
            ),
            nullable=False,
        ),
        sa.Column(
            "classLevelTo",
            postgresql.ENUM(
                "MATERNELLE_1", "MATERNELLE_2", "MATERNELLE_3",
                "CP1", "CP2", "CE1", "CE2", "CM1", "CM2",
                name="EnrollmentClassLevel",
                create_type=False,
            ),
            nullable=False,
        ),
        sa.Column(
            "gender",
            postgresql.ENUM(
                "FEMALE", "MALE", "OTHER",
                name="Gender", create_type=False,
            ),
            nullable=False,
        ),
        sa.Column("rate", sa.Numeric(precision=6, scale=4), nullable=True),
        sa.Column("sampleSize", sa.Integer(), nullable=False),
        sa.Column(
            "isOutlier", sa.Boolean(),
            nullable=False, server_default=sa.text("false"),
        ),
        sa.Column(
            "computedAt", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.func.now(),
        ),
        sa.Column(
            "createdById",
            sa.String(length=30),
            sa.ForeignKey("User.id"),
            nullable=True,
        ),
        sa.Column(
            "createdAt", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "scope", "entityId", "schoolYearFromId",
            "classLevelFrom", "gender",
            name="uq_TransitionRate_scope_entity_year_level_gender",
        ),
    )

    op.create_index(
        "ix_TransitionRate_scope_entityId_schoolYearFromId",
        "TransitionRate",
        ["scope", "entityId", "schoolYearFromId"],
    )
    op.create_index(
        "ix_TransitionRate_classLevelFrom_classLevelTo",
        "TransitionRate",
        ["classLevelFrom", "classLevelTo"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_TransitionRate_classLevelFrom_classLevelTo",
        table_name="TransitionRate",
    )
    op.drop_index(
        "ix_TransitionRate_scope_entityId_schoolYearFromId",
        table_name="TransitionRate",
    )
    op.drop_table("TransitionRate")
    bind = op.get_bind()
    TRANSITION_SCOPE.drop(bind, checkfirst=True)
