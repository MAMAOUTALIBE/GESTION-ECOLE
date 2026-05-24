"""module 2B — Projection effectifs horizon 5 ans (IIPE-UNESCO)

Revision ID: 0027_projections
Revises: 0026_transition_rates
Create Date: 2026-05-24

Pourquoi ?
----------
Le Module 2B applique les taux de transition (Module 2A) sur les effectifs
de l'année de base (CENSUS_DECLARED) pour produire des projections horizon
1 à 10 ans (5 par défaut). Indispensable pour :

* planifier les recrutements d'enseignants par région à 5 ans,
* anticiper les besoins en infrastructure (classes manquantes),
* nourrir la modélisation budgétaire du cabinet ministre.

Algorithme cohortes :

    projection[r, levelN, g, t+k] =
        enrollment[r, levelN-1, g, t+k-1]
        × transition_rate[r, levelN-1 → levelN, g]

MATERNELLE_1 : pas de niveau précédent → croissance démographique annuelle
(taux INS Guinée = 2.4 %).

Modèle de données
-----------------
``ProjectionScenario`` : un scénario de projection (taux de croissance
démographique paramétrable, surcharge des transition rates JSONB pour
simulations "what-if"). Un scénario implicite ``BASELINE`` existe en
permanence (id = nom = "BASELINE").

``ProjectedEnrollment`` : un row par cellule projetée
``(baseSchoolYearId, projectedYear, scope, entityId, classLevel,
gender, scenarioId)``. Unique pour permettre l'upsert idempotent.

Index
-----
* ``(baseSchoolYearId, projectedYear, scope, entityId)`` — vue dashboard
  "effectifs projetés pour la région X en 2028".
* ``(scenarioId)`` — comparaison entre scénarios.

Downgrade
---------
Drop tables + drop séquence éventuelle. Ne touche pas à l'enum
``TransitionScope`` (réutilisé du Module 2A).
"""
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0027_projections"
down_revision: str | Sequence[str] | None = "0026_transition_rates"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Table des scénarios — précède ProjectedEnrollment (FK).
    op.create_table(
        "ProjectionScenario",
        sa.Column("id", sa.String(length=30), primary_key=True),
        sa.Column("name", sa.String(length=80), nullable=False, unique=True),
        sa.Column("description", sa.String(length=500), nullable=True),
        sa.Column(
            "demographicGrowthRate",
            sa.Numeric(precision=5, scale=4),
            nullable=False,
            server_default=sa.text("0.0240"),
        ),
        # JSONB pour surcharger les transition rates (ex.
        # {"CP1→CP2:FEMALE": 0.95}). NULL = baseline.
        sa.Column(
            "customTransitionRates",
            postgresql.JSONB(),
            nullable=True,
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
    )

    # Table des effectifs projetés.
    op.create_table(
        "ProjectedEnrollment",
        sa.Column("id", sa.String(length=30), primary_key=True),
        sa.Column(
            "baseSchoolYearId",
            sa.String(length=30),
            sa.ForeignKey("SchoolYear.id"),
            nullable=False,
        ),
        sa.Column("projectedYear", sa.Integer(), nullable=False),
        # Réutilise l'enum TransitionScope (Module 2A) — NATIONAL/REGIONAL.
        sa.Column(
            "scope",
            postgresql.ENUM(
                "NATIONAL", "REGIONAL",
                name="TransitionScope", create_type=False,
            ),
            nullable=False,
        ),
        sa.Column("entityId", sa.String(length=30), nullable=True),
        sa.Column(
            "classLevel",
            postgresql.ENUM(
                "MATERNELLE_1", "MATERNELLE_2", "MATERNELLE_3",
                "CP1", "CP2", "CE1", "CE2", "CM1", "CM2",
                name="EnrollmentClassLevel", create_type=False,
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
        sa.Column("projectedCount", sa.Integer(), nullable=False),
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
            "baseSchoolYearId", "projectedYear", "scope", "entityId",
            "classLevel", "gender", "scenarioId",
            name="uq_ProjectedEnrollment_full",
        ),
    )

    op.create_index(
        "ix_ProjectedEnrollment_base_year_scope_entity",
        "ProjectedEnrollment",
        ["baseSchoolYearId", "projectedYear", "scope", "entityId"],
    )
    op.create_index(
        "ix_ProjectedEnrollment_scenarioId",
        "ProjectedEnrollment",
        ["scenarioId"],
    )

    # Seed du scénario BASELINE — identifiant fixe pour pouvoir
    # référencer le scénario par défaut depuis l'API sans lookup préalable.
    op.execute(
        """
        INSERT INTO "ProjectionScenario" (
            id, name, description, "demographicGrowthRate",
            "customTransitionRates", "createdAt"
        )
        VALUES (
            'BASELINE', 'BASELINE',
            'Scénario par défaut : croissance démographique INS Guinée 2.4%.',
            0.0240, NULL, NOW()
        )
        ON CONFLICT (id) DO NOTHING
        """
    )


def downgrade() -> None:
    op.drop_index(
        "ix_ProjectedEnrollment_scenarioId",
        table_name="ProjectedEnrollment",
    )
    op.drop_index(
        "ix_ProjectedEnrollment_base_year_scope_entity",
        table_name="ProjectedEnrollment",
    )
    op.drop_table("ProjectedEnrollment")
    op.drop_table("ProjectionScenario")
