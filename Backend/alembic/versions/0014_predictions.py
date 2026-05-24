"""module 8 — predictions ML : dropout risk scoring per student

Revision ID: 0014_predictions
Revises: 0013_schoollife
Create Date: 2026-05-24

Pourquoi ?
----------
Module 8 expose un pipeline ML simple (logistic regression scikit-learn) qui
calcule pour chaque élève une probabilité d'abandon scolaire dans les 90
jours. On stocke les scores en DB pour suivi temporel (1 score par élève
par mois). Deux tables :

* ``DropoutPrediction`` — un score calculé pour un (studentId, schoolYearId,
  computedAt) donné. ``probability`` (0..1), ``riskLevel`` (LOW/MEDIUM/HIGH),
  ``featuresSnapshot`` JSONB pour traçabilité (debug / fairness audit), et
  ``modelVersion`` pour savoir quel modèle a produit le score.
* ``DropoutModelMetadata`` — registry minimal des versions de modèles
  entraînés (version, date, métriques, chemin de l'artefact joblib).

Indexes :
* ``(studentId, computedAt DESC)`` pour récupérer le dernier score d'un élève
  rapidement (timeline).
* ``(riskLevel, schoolId)`` pour lister les élèves à risque d'une école
  (via JOIN sur Student.schoolId — on dénormalise pas pour l'instant).

Downgrade
---------
Drop des deux tables + drop de l'enum ``DropoutRiskLevel``. Module 7 reste
intact.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0014_predictions"
down_revision: str | Sequence[str] | None = "0013_schoollife"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


DROPOUT_RISK_LEVEL = postgresql.ENUM(
    "LOW", "MEDIUM", "HIGH",
    name="DropoutRiskLevel", create_type=False,
)


def upgrade() -> None:
    bind = op.get_bind()
    DROPOUT_RISK_LEVEL.create(bind, checkfirst=True)

    # ---- DropoutPrediction --------------------------------------------
    op.create_table(
        "DropoutPrediction",
        sa.Column("id", sa.String(length=30), primary_key=True),
        sa.Column("studentId", sa.String(length=30), nullable=False),
        sa.Column("schoolYearId", sa.String(length=30), nullable=True),
        sa.Column(
            "computedAt", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.func.now(),
        ),
        sa.Column("probability", sa.Float(), nullable=False),
        sa.Column(
            "riskLevel", DROPOUT_RISK_LEVEL,
            nullable=False, server_default="LOW",
        ),
        sa.Column(
            "featuresSnapshot", postgresql.JSONB(),
            nullable=False, server_default="{}",
        ),
        sa.Column("modelVersion", sa.String(length=20), nullable=False),
        sa.Column(
            "createdAt", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["studentId"], ["Student.id"],
            name="fk_DropoutPrediction_studentId_Student",
        ),
        sa.ForeignKeyConstraint(
            ["schoolYearId"], ["SchoolYear.id"],
            name="fk_DropoutPrediction_schoolYearId_SchoolYear",
        ),
    )
    op.create_index(
        "ix_DropoutPrediction_studentId_computedAt",
        "DropoutPrediction", ["studentId", sa.text('"computedAt" DESC')],
    )
    op.create_index(
        "ix_DropoutPrediction_riskLevel",
        "DropoutPrediction", ["riskLevel"],
    )

    # ---- DropoutModelMetadata -----------------------------------------
    op.create_table(
        "DropoutModelMetadata",
        sa.Column("id", sa.String(length=30), primary_key=True),
        sa.Column("version", sa.String(length=20), nullable=False),
        sa.Column(
            "trainedAt", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.func.now(),
        ),
        sa.Column(
            "metrics", postgresql.JSONB(),
            nullable=False, server_default="{}",
        ),
        sa.Column("artifactPath", sa.String(length=500), nullable=False),
        sa.Column(
            "createdAt", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "version", name="uq_DropoutModelMetadata_version",
        ),
    )
    op.create_index(
        "ix_DropoutModelMetadata_trainedAt",
        "DropoutModelMetadata", [sa.text('"trainedAt" DESC')],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_DropoutModelMetadata_trainedAt",
        table_name="DropoutModelMetadata",
    )
    op.drop_table("DropoutModelMetadata")

    op.drop_index(
        "ix_DropoutPrediction_riskLevel",
        table_name="DropoutPrediction",
    )
    op.drop_index(
        "ix_DropoutPrediction_studentId_computedAt",
        table_name="DropoutPrediction",
    )
    op.drop_table("DropoutPrediction")

    bind = op.get_bind()
    DROPOUT_RISK_LEVEL.drop(bind, checkfirst=True)
