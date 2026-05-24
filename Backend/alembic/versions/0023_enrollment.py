"""module 1A — Enrollment désagrégé (niveau × genre × source)

Revision ID: 0023_enrollment
Revises: 0022_cockpit
Create Date: 2026-05-24

Pourquoi ?
----------
Fondation Phase 1 carte scolaire IIPE. Permet de stocker les effectifs
déclarés annuellement par établissement, désagrégés par niveau scolaire
× genre. C'est la base de :
* dashboard équité (1D)
* indice de parité fille/garçon GPI (1B)
* projections par cohorte (Phase 2)

Enums
-----
* ``EnrollmentClassLevel`` — 9 valeurs (maternelle 1/2/3 + CP1..CM2),
  primaire guinéen.
* ``EnrollmentSource`` — CENSUS_DECLARED (vérité officielle) /
  COMPUTED_FROM_STUDENTS (recalcul depuis Student, signal data quality) /
  IMPORT (bulks historiques).

Index
-----
* ``(schoolYearId, schoolId)`` : UI saisie (cas dominant).
* ``(schoolYearId, classLevel, gender)`` : agrégations équité nationales.

Unique
------
* ``(schoolYearId, schoolId, classLevel, gender, source)`` — autorise la
  coexistence d'une déclaration et d'un calcul pour la même cellule
  (cross-check qualité).

Downgrade
---------
Drop table + drop des deux enums.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0023_enrollment"
down_revision: str | Sequence[str] | None = "0022_cockpit"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


ENROLLMENT_CLASS_LEVEL = postgresql.ENUM(
    "MATERNELLE_1",
    "MATERNELLE_2",
    "MATERNELLE_3",
    "CP1",
    "CP2",
    "CE1",
    "CE2",
    "CM1",
    "CM2",
    name="EnrollmentClassLevel",
    create_type=False,
)

ENROLLMENT_SOURCE = postgresql.ENUM(
    "CENSUS_DECLARED",
    "COMPUTED_FROM_STUDENTS",
    "IMPORT",
    name="EnrollmentSource",
    create_type=False,
)

_ALL_ENUMS = (ENROLLMENT_CLASS_LEVEL, ENROLLMENT_SOURCE)


def upgrade() -> None:
    bind = op.get_bind()
    for enum_type in _ALL_ENUMS:
        enum_type.create(bind, checkfirst=True)

    op.create_table(
        "Enrollment",
        sa.Column("id", sa.String(length=30), primary_key=True),
        sa.Column(
            "schoolYearId",
            sa.String(length=30),
            sa.ForeignKey("SchoolYear.id"),
            nullable=False,
        ),
        sa.Column(
            "schoolId",
            sa.String(length=30),
            sa.ForeignKey("School.id"),
            nullable=False,
        ),
        sa.Column("classLevel", ENROLLMENT_CLASS_LEVEL, nullable=False),
        sa.Column(
            "gender",
            postgresql.ENUM(
                "FEMALE", "MALE", "OTHER",
                name="Gender", create_type=False,
            ),
            nullable=False,
        ),
        sa.Column("count", sa.Integer(), nullable=False),
        sa.Column("source", ENROLLMENT_SOURCE, nullable=False),
        sa.Column(
            "recordedAt", sa.DateTime(timezone=True), nullable=False
        ),
        sa.Column(
            "recordedById",
            sa.String(length=30),
            sa.ForeignKey("User.id"),
            nullable=True,
        ),
        sa.Column("notes", sa.String(length=500), nullable=True),
        sa.Column(
            "createdAt", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.func.now(),
        ),
        sa.Column(
            "updatedAt", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "schoolYearId", "schoolId", "classLevel", "gender", "source",
            name="uq_Enrollment_year_school_level_gender_source",
        ),
    )
    op.create_index(
        "ix_Enrollment_schoolYearId_schoolId",
        "Enrollment",
        ["schoolYearId", "schoolId"],
    )
    op.create_index(
        "ix_Enrollment_schoolYearId_classLevel_gender",
        "Enrollment",
        ["schoolYearId", "classLevel", "gender"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_Enrollment_schoolYearId_classLevel_gender",
        table_name="Enrollment",
    )
    op.drop_index(
        "ix_Enrollment_schoolYearId_schoolId",
        table_name="Enrollment",
    )
    op.drop_table("Enrollment")
    bind = op.get_bind()
    for enum_type in reversed(_ALL_ENUMS):
        enum_type.drop(bind, checkfirst=True)
