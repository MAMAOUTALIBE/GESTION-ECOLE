"""phase 13bis — Paramètres plateforme (PlatformSetting)

Revision ID: 0007_phase13bis
Revises: 0006_phase13
Create Date: 2026-05-05

Crée la table PlatformSetting (clé/valeur typée par catégorie) + un seed
des paramètres opérationnels par défaut.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007_phase13bis"
down_revision: str | Sequence[str] | None = "0006_phase13"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "PlatformSetting",
        sa.Column("id", sa.String(length=30), primary_key=True),
        sa.Column("key", sa.String(), nullable=False),
        sa.Column("value", sa.String(), nullable=False),
        sa.Column("category", sa.String(), nullable=False),
        sa.Column("label", sa.String(), nullable=False),
        sa.Column("description", sa.String(), nullable=True),
        sa.Column(
            "valueType", sa.String(), nullable=False, server_default="string"
        ),
        sa.Column("updatedById", sa.String(length=30), nullable=True),
        sa.Column("createdAt", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("updatedAt", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.UniqueConstraint("key", name="uq_PlatformSetting_key"),
    )

    # Seed paramètres par défaut
    op.execute("""
        INSERT INTO "PlatformSetting"
            (id, key, value, category, label, description, "valueType",
             "createdAt", "updatedAt")
        VALUES
            ('seed_attendance_threshold', 'attendance.alert_threshold_pct',
             '70', 'thresholds', 'Seuil alerte présence (%)',
             'En dessous de ce taux sur 7 jours, l''école est en alerte absentéisme',
             'number', NOW(), NOW()),
            ('seed_ratio_threshold', 'pedagogy.ratio_critical',
             '45', 'thresholds', 'Ratio élèves/enseignant critique',
             'Au-dessus de ce ratio, l''école est en surcharge critique',
             'number', NOW(), NOW()),
            ('seed_ratio_warning', 'pedagogy.ratio_warning',
             '35', 'thresholds', 'Ratio élèves/enseignant en tension',
             'Au-dessus de ce ratio, l''école est en tension',
             'number', NOW(), NOW()),
            ('seed_validation_delay', 'workflow.validation_max_days',
             '14', 'workflow', 'Délai max validation (jours)',
             'Au-delà, escalade automatique au niveau supérieur',
             'number', NOW(), NOW()),
            ('seed_default_channel', 'communication.default_channel',
             '"SMS"', 'communication', 'Canal de communication par défaut',
             'Canal utilisé pour les alertes parents si aucune préférence',
             'string', NOW(), NOW()),
            ('seed_email_enabled', 'communication.email_enabled',
             'true', 'communication', 'Email activé',
             'Active l''envoi d''emails aux parents qui en ont fourni un',
             'boolean', NOW(), NOW()),
            ('seed_sms_enabled', 'communication.sms_enabled',
             'true', 'communication', 'SMS activé',
             'Active l''envoi de SMS aux parents',
             'boolean', NOW(), NOW()),
            ('seed_inspection_score_critical', 'inspections.score_critical',
             '50', 'thresholds', 'Score inspection critique',
             'En dessous de ce score, l''école est notée critique au pilotage',
             'number', NOW(), NOW()),
            ('seed_bulletin_auto_publish', 'bulletins.auto_publish',
             'false', 'bulletins', 'Publication automatique bulletins',
             'Si activé, les bulletins validés sont publiés sans attente directeur',
             'boolean', NOW(), NOW()),
            ('seed_qr_offline_window', 'attendance.qr_offline_hours',
             '48', 'attendance', 'Fenêtre offline scan QR (h)',
             'Durée pendant laquelle un scan offline reste valide',
             'number', NOW(), NOW())
    """)


def downgrade() -> None:
    op.drop_table("PlatformSetting")
