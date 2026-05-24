"""module 2D — Recommandation transferts enseignants (IIPE / staffing)

Revision ID: 0029_teacher_recommendations
Revises: 0028_capacity_demand
Create Date: 2026-05-24

Pourquoi ?
----------
Objectif gouvernemental "optimiser la répartition des enseignants" :
les Modules 2A/2B/2C couvrent la projection des élèves et la capacité
infrastructure, mais pas la répartition des enseignants. Or, en Guinée,
le ratio élèves/enseignant varie violemment d'une école à l'autre
(zones rurales souvent sous-dotées, urbaines parfois sur-dotées).

Le Module 2D matérialise pour chaque école active un **snapshot de
staffing** (ratio actuel + sévérité) et produit des **recommandations
consultatives** de transfert d'enseignants pour rééquilibrer.

Norme MEN Guinée : 40 élèves / enseignant (cible IIPE).

Seuils de classification :

* ``OVER_STAFFED``  — ratio < 25 (trop d'enseignants)
* ``ADEQUATE``      — 25 ≤ ratio ≤ 50 (zone verte)
* ``UNDER_STAFFED`` — 50 < ratio ≤ 70 (warning)
* ``CRITICAL``      — ratio > 70 (sous-doté grave)

Modèle de données
-----------------
``TeacherStaffingSnapshot`` (1 row par (schoolYearId, schoolId)) :
* ``studentsCount`` / ``teachersCount`` — comptages au moment du calcul.
* ``ratio`` — Decimal(8,2) ; NULL si teachersCount = 0.
* ``severity`` — enum StaffingSeverity.
* ``expectedTeachers`` — math.ceil(students / NORM).
* ``gap`` — int signé : négatif = sur-doté, positif = besoin.

``TeacherTransferRecommendation`` (paire donneur → receveur) :
* ``transfersSuggested`` — nombre d'enseignants à déplacer (entier).
* ``priorityScore`` — Decimal(6,2) ; bonus si même préfecture.
* ``status`` — workflow PENDING → REVIEWED/ACCEPTED/REJECTED/EXECUTED.
* ``reviewedById`` / ``reviewedAt`` / ``reviewNote`` — audit revue.

Index
-----
* ``(schoolYearId, severity)`` — vue "écoles CRITICAL pour l'année t".
* ``(regionId, priorityScore DESC)`` — top recommandations par région.
* ``(status)`` — workflow pending.

Hooks
-----
* Module 9  — ajoute ``CRITICAL_TEACHER_SHORTAGE`` à ``AnomalyType``.
* Module 19 — ajoute ``SCHOOLS_CRITICAL_STAFFING_COUNT`` à ``KpiKey``.

Downgrade
---------
Drop tables + drop enums StaffingSeverity, RecommendationStatus. Les
valeurs ajoutées aux enums AnomalyType / KpiKey ne peuvent pas être
retirées (Postgres ne supporte pas ``ALTER TYPE ... DROP VALUE``) — on
laisse les valeurs en place : elles deviennent inutilisables sans la
table.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0029_teacher_recommendations"
down_revision: str | Sequence[str] | None = "0028_capacity_demand"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


STAFFING_SEVERITY = postgresql.ENUM(
    "OVER_STAFFED",
    "ADEQUATE",
    "UNDER_STAFFED",
    "CRITICAL",
    name="StaffingSeverity",
    create_type=False,
)

RECOMMENDATION_STATUS = postgresql.ENUM(
    "PENDING",
    "REVIEWED",
    "ACCEPTED",
    "REJECTED",
    "EXECUTED",
    name="RecommendationStatus",
    create_type=False,
)


def upgrade() -> None:
    bind = op.get_bind()
    STAFFING_SEVERITY.create(bind, checkfirst=True)
    RECOMMENDATION_STATUS.create(bind, checkfirst=True)

    # Module 9 — ajoute CRITICAL_TEACHER_SHORTAGE à l'enum AnomalyType.
    op.execute(
        'ALTER TYPE "AnomalyType" '
        "ADD VALUE IF NOT EXISTS 'CRITICAL_TEACHER_SHORTAGE'"
    )

    # Module 19 — ajoute SCHOOLS_CRITICAL_STAFFING_COUNT à l'enum KpiKey.
    op.execute(
        'ALTER TYPE "KpiKey" '
        "ADD VALUE IF NOT EXISTS 'SCHOOLS_CRITICAL_STAFFING_COUNT'"
    )

    # -- TeacherStaffingSnapshot ----------------------------------------------
    op.create_table(
        "TeacherStaffingSnapshot",
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
        sa.Column("studentsCount", sa.Integer(), nullable=False),
        sa.Column("teachersCount", sa.Integer(), nullable=False),
        sa.Column(
            "ratio",
            sa.Numeric(precision=8, scale=2),
            nullable=True,
        ),
        sa.Column("severity", STAFFING_SEVERITY, nullable=False),
        sa.Column("expectedTeachers", sa.Integer(), nullable=False),
        sa.Column("gap", sa.Integer(), nullable=False),
        sa.Column(
            "computedAt",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "createdAt",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "schoolYearId", "schoolId",
            name="uq_TeacherStaffingSnapshot_year_school",
        ),
    )
    op.create_index(
        "ix_TeacherStaffingSnapshot_year_severity",
        "TeacherStaffingSnapshot",
        ["schoolYearId", "severity"],
    )

    # -- TeacherTransferRecommendation ----------------------------------------
    op.create_table(
        "TeacherTransferRecommendation",
        sa.Column("id", sa.String(length=30), primary_key=True),
        sa.Column(
            "schoolYearId",
            sa.String(length=30),
            sa.ForeignKey("SchoolYear.id"),
            nullable=False,
        ),
        sa.Column(
            "fromSchoolId",
            sa.String(length=30),
            sa.ForeignKey("School.id"),
            nullable=False,
        ),
        sa.Column(
            "toSchoolId",
            sa.String(length=30),
            sa.ForeignKey("School.id"),
            nullable=False,
        ),
        sa.Column(
            "prefectureId",
            sa.String(length=30),
            sa.ForeignKey("Prefecture.id"),
            nullable=True,
        ),
        sa.Column(
            "regionId",
            sa.String(length=30),
            sa.ForeignKey("Region.id"),
            nullable=False,
        ),
        sa.Column("transfersSuggested", sa.Integer(), nullable=False),
        sa.Column(
            "priorityScore",
            sa.Numeric(precision=6, scale=2),
            nullable=False,
        ),
        sa.Column("rationale", sa.Text(), nullable=True),
        sa.Column(
            "status",
            RECOMMENDATION_STATUS,
            nullable=False,
            server_default=sa.text("'PENDING'"),
        ),
        sa.Column(
            "createdAt",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "reviewedById",
            sa.String(length=30),
            sa.ForeignKey("User.id"),
            nullable=True,
        ),
        sa.Column(
            "reviewedAt",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column("reviewNote", sa.Text(), nullable=True),
    )
    op.create_index(
        "ix_TeacherTransferRecommendation_region_priority",
        "TeacherTransferRecommendation",
        ["regionId", "priorityScore"],
        postgresql_ops={"priorityScore": "DESC"},
    )
    op.create_index(
        "ix_TeacherTransferRecommendation_status",
        "TeacherTransferRecommendation",
        ["status"],
    )
    op.create_index(
        "ix_TeacherTransferRecommendation_year",
        "TeacherTransferRecommendation",
        ["schoolYearId"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_TeacherTransferRecommendation_year",
        table_name="TeacherTransferRecommendation",
    )
    op.drop_index(
        "ix_TeacherTransferRecommendation_status",
        table_name="TeacherTransferRecommendation",
    )
    op.drop_index(
        "ix_TeacherTransferRecommendation_region_priority",
        table_name="TeacherTransferRecommendation",
    )
    op.drop_table("TeacherTransferRecommendation")

    op.drop_index(
        "ix_TeacherStaffingSnapshot_year_severity",
        table_name="TeacherStaffingSnapshot",
    )
    op.drop_table("TeacherStaffingSnapshot")

    bind = op.get_bind()
    RECOMMENDATION_STATUS.drop(bind, checkfirst=True)
    STAFFING_SEVERITY.drop(bind, checkfirst=True)
    # Note : on ne retire pas les valeurs ajoutées aux enums
    # AnomalyType / KpiKey (Postgres ne supporte pas DROP VALUE).
