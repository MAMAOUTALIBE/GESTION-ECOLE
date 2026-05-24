"""Module 3B — Simulateur "what-if" de réorganisation du réseau scolaire.

Revision ID: 0030_what_if_scenarios
Revises: 0029_teacher_recommendations
Create Date: 2026-05-24

Pourquoi ?
----------
L'étape 3 IIPE de la carte scolaire (réorganisation du réseau) demande au
planificateur de tester des hypothèses sans toucher aux données réelles :

* "Si je crée une école ici (lat/lon, capacité 200), qui en bénéficie ?"
* "Si je ferme l'école X, où vont ses élèves ?"
* "Si je fusionne école A + B, quel impact ?"

On stocke ces scénarios de simulation pour les rejouer / auditer, mais on
n'écrit JAMAIS dans la table ``School`` : c'est un calcul read-only sur la
photo actuelle du réseau enrichie par les opérations du scénario.

Modèle
------
Une seule table ``SimulationScenario`` :

* ``id`` (cuid)
* ``name`` (court, descriptif)
* ``description`` (texte libre, optionnel)
* ``createdById`` FK ``User.id`` — auteur du scénario.
* ``createdAt`` (now par défaut)
* ``status`` ENUM(DRAFT, COMPUTED, ARCHIVED) — workflow.
* ``baselineSchoolYearId`` FK ``SchoolYear.id`` — année des effectifs base.
* ``scenarioJson`` JSONB — payload des opérations (cf ``schemas.py``).
* ``impactJson`` JSONB nullable — rempli après ``compute`` ; stocke
  ``ImpactReport`` (couverture, saturation, distance, redistribués).
* ``computedAt`` nullable — datetime de dernier compute.

Index : ``(createdById, createdAt DESC)`` (vue "mes scénarios récents") et
``(status)`` (filtre workflow).

Downgrade : drop table + drop enum ``SimulationScenarioStatus``.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0030_what_if_scenarios"
down_revision: str | Sequence[str] | None = "0029_teacher_recommendations"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


SCENARIO_STATUS = postgresql.ENUM(
    "DRAFT",
    "COMPUTED",
    "ARCHIVED",
    name="SimulationScenarioStatus",
    create_type=False,
)


def upgrade() -> None:
    bind = op.get_bind()
    SCENARIO_STATUS.create(bind, checkfirst=True)

    op.create_table(
        "SimulationScenario",
        sa.Column("id", sa.String(length=30), primary_key=True),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("description", sa.String(length=500), nullable=True),
        sa.Column(
            "createdById",
            sa.String(length=30),
            sa.ForeignKey("User.id"),
            nullable=False,
        ),
        sa.Column(
            "createdAt",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "status",
            SCENARIO_STATUS,
            nullable=False,
            server_default=sa.text("'DRAFT'"),
        ),
        sa.Column(
            "baselineSchoolYearId",
            sa.String(length=30),
            sa.ForeignKey("SchoolYear.id"),
            nullable=False,
        ),
        sa.Column(
            "scenarioJson",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "impactJson",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column(
            "computedAt",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_SimulationScenario_createdBy_createdAt",
        "SimulationScenario",
        ["createdById", "createdAt"],
        postgresql_ops={"createdAt": "DESC"},
    )
    op.create_index(
        "ix_SimulationScenario_status",
        "SimulationScenario",
        ["status"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_SimulationScenario_status",
        table_name="SimulationScenario",
    )
    op.drop_index(
        "ix_SimulationScenario_createdBy_createdAt",
        table_name="SimulationScenario",
    )
    op.drop_table("SimulationScenario")

    bind = op.get_bind()
    SCENARIO_STATUS.drop(bind, checkfirst=True)
