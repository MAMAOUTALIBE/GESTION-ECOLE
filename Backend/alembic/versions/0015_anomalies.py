"""module 9 — anomalies detection : append-only audit table + workflow review

Revision ID: 0015_anomalies
Revises: 0014_predictions
Create Date: 2026-05-24

Pourquoi ?
----------
Module 9 expose un système de détection d'anomalies métier basé sur règles
(grades impossibles, présences suspectes, transferts excessifs…). Toutes
les anomalies détectées sont persistées dans une seule table
``AnomalyDetection`` (append-only) avec un workflow human-in-the-loop
(PENDING → CONFIRMED / DISMISSED / FALSE_POSITIVE).

La colonne ``evidence`` (JSONB) stocke les champs exacts qui ont déclenché
l'anomalie pour expliquer la décision au directeur d'école. ``schoolId`` /
``regionId`` sont dénormalisés pour permettre le scope territorial dans
le listing sans JOIN à chaque requête.

Indexes
-------
* ``(status, severity)`` — triage par sévérité au sein des PENDING.
* ``(entityType, entityId)`` — historique d'une entité (déduplication
  côté service, dernière occurrence par (entityType, entityId, type)).
* ``(schoolId, detectedAt DESC)`` — listing scope école, plus récent d'abord.

Downgrade
---------
Drop de la table + drop des trois enums (``AnomalyType``,
``AnomalySeverity``, ``AnomalyStatus``). Module 8 reste intact.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0015_anomalies"
down_revision: str | Sequence[str] | None = "0014_predictions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


ANOMALY_TYPE = postgresql.ENUM(
    "IMPOSSIBLE_GRADE",
    "SUSPICIOUS_ATTENDANCE",
    "GRADE_JUMP",
    "INVALID_BIRTHDATE",
    "DUPLICATE_CODE",
    "EXCESSIVE_TRANSFER",
    name="AnomalyType",
    create_type=False,
)
ANOMALY_SEVERITY = postgresql.ENUM(
    "LOW", "MEDIUM", "HIGH", "CRITICAL",
    name="AnomalySeverity", create_type=False,
)
ANOMALY_STATUS = postgresql.ENUM(
    "PENDING", "CONFIRMED", "DISMISSED", "FALSE_POSITIVE",
    name="AnomalyStatus", create_type=False,
)

_ALL_ENUMS = (ANOMALY_TYPE, ANOMALY_SEVERITY, ANOMALY_STATUS)


def upgrade() -> None:
    bind = op.get_bind()
    for enum_type in _ALL_ENUMS:
        enum_type.create(bind, checkfirst=True)

    op.create_table(
        "AnomalyDetection",
        sa.Column("id", sa.String(length=30), primary_key=True),
        sa.Column("type", ANOMALY_TYPE, nullable=False),
        sa.Column("severity", ANOMALY_SEVERITY, nullable=False),
        sa.Column(
            "status", ANOMALY_STATUS,
            nullable=False, server_default="PENDING",
        ),
        sa.Column("entityType", sa.String(length=40), nullable=False),
        sa.Column("entityId", sa.String(length=30), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column(
            "evidence", postgresql.JSONB(),
            nullable=False, server_default="{}",
        ),
        sa.Column("schoolId", sa.String(length=30), nullable=True),
        sa.Column("regionId", sa.String(length=30), nullable=True),
        sa.Column(
            "detectedAt", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.func.now(),
        ),
        sa.Column("reviewedAt", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reviewedById", sa.String(length=30), nullable=True),
        sa.Column("reviewNote", sa.Text(), nullable=True),
        sa.Column(
            "createdAt", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["schoolId"], ["School.id"],
            name="fk_AnomalyDetection_schoolId_School",
        ),
        sa.ForeignKeyConstraint(
            ["regionId"], ["Region.id"],
            name="fk_AnomalyDetection_regionId_Region",
        ),
        sa.ForeignKeyConstraint(
            ["reviewedById"], ["User.id"],
            name="fk_AnomalyDetection_reviewedById_User",
        ),
    )

    op.create_index(
        "ix_AnomalyDetection_status_severity",
        "AnomalyDetection", ["status", "severity"],
    )
    op.create_index(
        "ix_AnomalyDetection_entityType_entityId",
        "AnomalyDetection", ["entityType", "entityId"],
    )
    op.create_index(
        "ix_AnomalyDetection_schoolId_detectedAt",
        "AnomalyDetection", ["schoolId", sa.text('"detectedAt" DESC')],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_AnomalyDetection_schoolId_detectedAt",
        table_name="AnomalyDetection",
    )
    op.drop_index(
        "ix_AnomalyDetection_entityType_entityId",
        table_name="AnomalyDetection",
    )
    op.drop_index(
        "ix_AnomalyDetection_status_severity",
        table_name="AnomalyDetection",
    )
    op.drop_table("AnomalyDetection")

    bind = op.get_bind()
    for enum_type in reversed(_ALL_ENUMS):
        enum_type.drop(bind, checkfirst=True)
