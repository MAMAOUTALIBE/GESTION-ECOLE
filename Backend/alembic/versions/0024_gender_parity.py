"""module 1B — Indice de parité fille/garçon (GPI) : snapshots + alertes auto

Revision ID: 0024_gender_parity
Revises: 0023_enrollment
Create Date: 2026-05-24

Pourquoi ?
----------
Le Gender Parity Index (UNESCO/IIPE) = filles / garçons. Indicateur
phare pour l'objectif gouvernemental "améliorer la scolarisation des
filles". Calculé à 4 échelons (national, régional, préfectoral,
école) et persisté pour :

* Comparer d'une année sur l'autre (séries temporelles).
* Alimenter le cockpit ministériel (Module 19) sans re-scanner
  ``Enrollment`` à chaque hit.
* Déclencher automatiquement des anomalies (Module 9) sur les "points
  chauds" (GPI < 0.85).

Conventions
-----------
* ``gpi`` est un ``NUMERIC(6,4)`` (Decimal, pas float) — précision
  financière, ces chiffres remontent au cabinet.
* ``severity`` est calculé côté service à partir du gpi puis figé en
  base. Permet un filtrage rapide ``severity = CRITICAL_GIRLS`` pour
  la vue "points chauds".
* ``entityId`` est nullable uniquement pour le scope ``NATIONAL`` (pas
  d'entité explicite). Pour ``REGIONAL`` / ``PREFECTURE`` / ``SCHOOL``
  il vaut respectivement regionId / prefectureId / schoolId.

Index
-----
* ``(schoolYearId, scope, severity)`` : "points chauds nationaux pour
  l'année en cours" (cas dominant cockpit ministre).
* ``(entityId, computedAt DESC)`` : séries temporelles d'une entité
  (vue évolution annuelle d'une école).

Downgrade
---------
Drop de la table + drop des deux enums (``GpiScope``, ``GpiSeverity``).
"""
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0024_gender_parity"
down_revision: str | Sequence[str] | None = "0023_enrollment"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


GPI_SCOPE = postgresql.ENUM(
    "NATIONAL",
    "REGIONAL",
    "PREFECTURE",
    "SCHOOL",
    name="GpiScope",
    create_type=False,
)

GPI_SEVERITY = postgresql.ENUM(
    "NORMAL",
    "WARNING_GIRLS",
    "CRITICAL_GIRLS",
    "WARNING_BOYS",
    name="GpiSeverity",
    create_type=False,
)

_ALL_ENUMS = (GPI_SCOPE, GPI_SEVERITY)


def upgrade() -> None:
    bind = op.get_bind()
    for enum_type in _ALL_ENUMS:
        enum_type.create(bind, checkfirst=True)

    # Module 9 — ajoute CRITICAL_GPI à l'enum AnomalyType existant.
    # Module 19 — ajoute NATIONAL_GPI à l'enum KpiKey du cockpit.
    # `ALTER TYPE … ADD VALUE` est supporté en transaction depuis
    # Postgres 12 (la prod tourne en 16 ; cf. infra). Le ``IF NOT EXISTS``
    # rend l'opération idempotente.
    op.execute(
        "ALTER TYPE \"AnomalyType\" ADD VALUE IF NOT EXISTS 'CRITICAL_GPI'"
    )
    op.execute(
        "ALTER TYPE \"KpiKey\" ADD VALUE IF NOT EXISTS 'NATIONAL_GPI'"
    )

    op.create_table(
        "GpiSnapshot",
        sa.Column("id", sa.String(length=30), primary_key=True),
        sa.Column(
            "schoolYearId",
            sa.String(length=30),
            sa.ForeignKey("SchoolYear.id"),
            nullable=False,
        ),
        sa.Column("scope", GPI_SCOPE, nullable=False),
        # entityId nullable pour NATIONAL (pas d'entité dédiée).
        sa.Column("entityId", sa.String(length=30), nullable=True),
        sa.Column("girlsCount", sa.Integer(), nullable=False),
        sa.Column("boysCount", sa.Integer(), nullable=False),
        sa.Column("gpi", sa.Numeric(precision=6, scale=4), nullable=True),
        sa.Column("severity", GPI_SEVERITY, nullable=False),
        sa.Column(
            "computedAt", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.func.now(),
        ),
        sa.Column(
            "createdAt", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.func.now(),
        ),
    )

    op.create_index(
        "ix_GpiSnapshot_schoolYearId_scope_severity",
        "GpiSnapshot",
        ["schoolYearId", "scope", "severity"],
    )
    op.create_index(
        "ix_GpiSnapshot_entityId_computedAt",
        "GpiSnapshot",
        ["entityId", sa.text('"computedAt" DESC')],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_GpiSnapshot_entityId_computedAt",
        table_name="GpiSnapshot",
    )
    op.drop_index(
        "ix_GpiSnapshot_schoolYearId_scope_severity",
        table_name="GpiSnapshot",
    )
    op.drop_table("GpiSnapshot")
    bind = op.get_bind()
    for enum_type in reversed(_ALL_ENUMS):
        enum_type.drop(bind, checkfirst=True)
